"""
Prediction Error Tracker — Audit Expected Path model after each trade.

Per Executive Assessment v11:
    "The Expected Path module should be audited after every closed trade.
     For each trade compute:
         Predicted    Actual
         Expected MFE Real MFE
         Expected MAE Real MAE
         Expected R   Real R
         Expected Hold Real Hold

     Then track prediction error.
     If prediction error begins increasing,
     reduce the influence of the Expected Path model until it is recalibrated.
     This prevents stale models from silently degrading performance."

Key Features:
    1. Prediction Logging — record predictions before execution
    2. Outcome Matching — match predictions to actual outcomes
    3. Error Calculation — measure prediction accuracy
    4. Drift Detection — detect increasing prediction error
    5. Model Confidence — adjust model influence based on accuracy

READ-ONLY: Never modifies upstream data. Uses separate log file.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"
_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "prediction_errors.json"

# Rolling window for error calculation
ERROR_WINDOW = 50


@dataclass
class PredictionRecord:
    """Record of a prediction and its actual outcome."""
    trade_id: str = ""
    symbol: str = ""
    side: str = ""
    prediction_time: float = 0.0

    # Predicted values
    predicted_r: float = 0.0
    predicted_mfe_r: float = 0.0
    predicted_mae_r: float = 0.0
    predicted_hold_minutes: float = 0.0
    predicted_prob_profit: float = 0.0

    # Actual outcomes (filled after trade closes)
    actual_r: float = 0.0
    actual_mfe_r: float = 0.0
    actual_mae_r: float = 0.0
    actual_hold_minutes: float = 0.0
    actual_outcome_tracked: bool = False

    # Errors
    error_r: float = 0.0           # actual_r - predicted_r
    error_mfe: float = 0.0         # actual_mfe_r - predicted_mfe_r
    error_mae: float = 0.0         # actual_mae_r - predicted_mae_r
    error_hold: float = 0.0        # actual_hold - predicted_hold
    squared_error_r: float = 0.0   # (actual_r - predicted_r)^2

    def to_dict(self) -> Dict:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "side": self.side,
            "predicted_r": round(self.predicted_r, 3),
            "actual_r": round(self.actual_r, 3),
            "error_r": round(self.error_r, 3),
            "predicted_mfe_r": round(self.predicted_mfe_r, 3),
            "actual_mfe_r": round(self.actual_mfe_r, 3),
            "predicted_mae_r": round(self.predicted_mae_r, 3),
            "actual_mae_r": round(self.actual_mae_r, 3),
            "tracked": self.actual_outcome_tracked,
        }


@dataclass
class ErrorMetrics:
    """Aggregated prediction error metrics."""
    # Sample size
    total_predictions: int = 0
    matched_predictions: int = 0

    # R-multiple errors
    mean_error_r: float = 0.0       # Mean (actual - predicted) R
    mean_abs_error_r: float = 0.0   # Mean absolute error
    rmse_r: float = 0.0             # Root mean squared error
    mape_r: float = 0.0             # Mean absolute percentage error

    # MFE errors
    mean_error_mfe: float = 0.0
    mean_abs_error_mfe: float = 0.0

    # MAE errors
    mean_error_mae: float = 0.0
    mean_abs_error_mae: float = 0.0

    # Hold time errors
    mean_error_hold: float = 0.0

    # Bias (systematic error)
    bias_r: float = 0.0             # Positive = under-predicting, Negative = over-predicting

    # Model confidence
    model_confidence: float = 100.0  # 0-100, decreases with increasing error
    confidence_trend: str = ""       # IMPROVING / STABLE / DECLINING

    def to_dict(self) -> Dict:
        return {
            "total": self.total_predictions,
            "matched": self.matched_predictions,
            "r_error": {
                "mean": round(self.mean_error_r, 4),
                "mean_abs": round(self.mean_abs_error_r, 4),
                "rmse": round(self.rmse_r, 4),
                "mape": round(self.mape_r, 4),
            },
            "mfe_error": {
                "mean": round(self.mean_error_mfe, 4),
                "mean_abs": round(self.mean_abs_error_mfe, 4),
            },
            "mae_error": {
                "mean": round(self.mean_error_mae, 4),
                "mean_abs": round(self.mean_abs_error_mae, 4),
            },
            "hold_error": {
                "mean": round(self.mean_error_hold, 1),
            },
            "bias": round(self.bias_r, 4),
            "model_confidence": round(self.model_confidence, 1),
            "confidence_trend": self.confidence_trend,
        }


@dataclass
class PredictionErrorReport:
    """Complete prediction error analysis."""
    timestamp: float = 0.0
    metrics: ErrorMetrics = field(default_factory=ErrorMetrics)

    # Recent errors
    recent_errors: List[PredictionRecord] = field(default_factory=list)

    # Recommendations
    recommendation: str = ""
    model_influence: float = 1.0  # 0-1, how much to trust the model

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "metrics": self.metrics.to_dict(),
            "recent_errors": [r.to_dict() for r in self.recent_errors],
            "recommendation": self.recommendation,
            "model_influence": round(self.model_influence, 3),
        }


class PredictionErrorTracker:
    """
    Audits prediction accuracy after each closed trade.

    Per Executive Assessment v11:
        "If prediction error begins increasing,
         reduce the influence of the Expected Path model."

    This engine:
        1. Logs predictions before execution
        2. Matches predictions to actual outcomes
        3. Calculates prediction error metrics
        4. Detects drift (increasing error)
        5. Adjusts model confidence based on accuracy

    READ-ONLY: Never modifies upstream data. Uses separate log file.
    """

    def __init__(self, db_path: Optional[Path] = None, log_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._log_path = log_path or _LOG_PATH
        self._records: List[PredictionRecord] = []
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        """Load records from log file."""
        if time.time() - self._last_load < 300:
            return
        self._load_records()

    def _load_records(self) -> None:
        """Load prediction records from JSON log."""
        try:
            if self._log_path.exists():
                with open(self._log_path, "r") as f:
                    data = json.load(f)
                # Filter out extra fields that aren't in __init__
                valid_fields = {f.name for f in PredictionRecord.__dataclass_fields__.values()}
                filtered_data = [{k: v for k, v in item.items() if k in valid_fields} for item in data]
                self._records = [PredictionRecord(**item) for item in filtered_data]
            else:
                self._records = []
            self._last_load = time.time()
        except Exception as e:
            logger.warning("Could not load prediction error tracker: {}", e)
            self._records = []

    def _save_records(self) -> None:
        """Save records to JSON log."""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "w") as f:
                json.dump([r.to_dict() for r in self._records[-2000:]], f, indent=2)
        except Exception as e:
            logger.warning("Could not save prediction errors: {}", e)

    def log_prediction(
        self,
        symbol: str,
        side: str,
        predicted_r: float,
        predicted_mfe_r: float = 0.0,
        predicted_mae_r: float = 0.0,
        predicted_hold_minutes: float = 0.0,
        predicted_prob_profit: float = 0.5,
    ) -> None:
        """Log a prediction for later outcome tracking."""
        record = PredictionRecord(
            trade_id=f"{symbol}_{side}_{int(time.time())}",
            symbol=symbol,
            side=side,
            prediction_time=time.time(),
            predicted_r=predicted_r,
            predicted_mfe_r=predicted_mfe_r,
            predicted_mae_r=predicted_mae_r,
            predicted_hold_minutes=predicted_hold_minutes,
            predicted_prob_profit=predicted_prob_profit,
        )
        self._records.append(record)
        self._save_records()

    def track_outcome(
        self,
        symbol: str,
        side: str,
        actual_r: float,
        actual_mfe_r: float = 0.0,
        actual_mae_r: float = 0.0,
        actual_hold_minutes: float = 0.0,
    ) -> None:
        """Track the actual outcome of a previously predicted trade."""
        cutoff = time.time() - 86400  # 24 hours
        for record in reversed(self._records):
            if (record.symbol == symbol
                and record.side == side
                and record.prediction_time > cutoff
                and not record.actual_outcome_tracked):

                record.actual_outcome_tracked = True
                record.actual_r = actual_r
                record.actual_mfe_r = actual_mfe_r
                record.actual_mae_r = actual_mae_r
                record.actual_hold_minutes = actual_hold_minutes

                # Calculate errors
                record.error_r = actual_r - record.predicted_r
                record.error_mfe = actual_mfe_r - record.predicted_mfe_r
                record.error_mae = actual_mae_r - record.predicted_mae_r
                record.error_hold = actual_hold_minutes - record.predicted_hold_minutes
                record.squared_error_r = record.error_r ** 2

                logger.debug(
                    "📊 PREDICTION TRACKED: {} {} predicted={:.2f}R actual={:.2f}R error={:.2f}R",
                    symbol, side, record.predicted_r, actual_r, record.error_r,
                )
                break

        self._save_records()

    def analyze(self) -> PredictionErrorReport:
        """Analyze prediction accuracy and generate report."""
        self._ensure_loaded()

        report = PredictionErrorReport(timestamp=time.time())

        # Filter to matched records
        matched = [r for r in self._records if r.actual_outcome_tracked]
        report.metrics.total_predictions = len(self._records)
        report.metrics.matched_predictions = len(matched)

        if not matched:
            report.recommendation = "No matched predictions yet"
            report.model_influence = 1.0
            return report

        # ── R-multiple errors ──
        r_errors = [r.error_r for r in matched]
        report.metrics.mean_error_r = sum(r_errors) / len(r_errors)
        report.metrics.mean_abs_error_r = sum(abs(e) for e in r_errors) / len(r_errors)
        report.metrics.rmse_r = math.sqrt(sum(e ** 2 for e in r_errors) / len(r_errors))

        # MAPE (avoid division by zero)
        r_actuals = [r.actual_r for r in matched if abs(r.actual_r) > 0.01]
        if r_actuals:
            report.metrics.mape_r = sum(
                abs((r.actual_r - r.predicted_r) / r.actual_r)
                for r in matched if abs(r.actual_r) > 0.01
            ) / len(r_actuals) * 100

        # Bias
        report.metrics.bias_r = report.metrics.mean_error_r

        # ── MFE errors ──
        mfe_errors = [r.error_mfe for r in matched]
        report.metrics.mean_error_mfe = sum(mfe_errors) / len(mfe_errors)
        report.metrics.mean_abs_error_mfe = sum(abs(e) for e in mfe_errors) / len(mfe_errors)

        # ── MAE errors ──
        mae_errors = [r.error_mae for r in matched]
        report.metrics.mean_error_mae = sum(mae_errors) / len(mae_errors)
        report.metrics.mean_abs_error_mae = sum(abs(e) for e in mae_errors) / len(mae_errors)

        # ── Hold time errors ──
        hold_errors = [r.error_hold for r in matched]
        report.metrics.mean_error_hold = sum(hold_errors) / len(hold_errors)

        # ── Model confidence ──
        # Based on recent RMSE (last 50 trades)
        recent = matched[:ERROR_WINDOW]
        if len(recent) >= 10:
            recent_rmse = math.sqrt(sum(r.squared_error_r for r in recent) / len(recent))
            # Lower RMSE = higher confidence
            report.metrics.model_confidence = max(0, min(100, 100 - recent_rmse * 50))
        else:
            report.metrics.model_confidence = 50  # Insufficient data

        # ── Confidence trend ──
        if len(matched) >= 20:
            mid = len(matched) // 2
            early_rmse = math.sqrt(sum(r.squared_error_r for r in matched[mid:]) / max(1, mid))
            late_rmse = math.sqrt(sum(r.squared_error_r for r in matched[:mid]) / max(1, mid))

            if late_rmse < early_rmse * 0.9:
                report.metrics.confidence_trend = "IMPROVING"
            elif late_rmse > early_rmse * 1.1:
                report.metrics.confidence_trend = "DECLINING"
            else:
                report.metrics.confidence_trend = "STABLE"

        # ── Recent errors ──
        report.recent_errors = matched[:10]

        # ── Recommendation ──
        if report.metrics.model_confidence < 30:
            report.recommendation = "Model confidence is low — reduce influence"
            report.model_influence = report.metrics.model_confidence / 100
        elif report.metrics.confidence_trend == "DECLINING":
            report.recommendation = "Prediction error increasing — monitor closely"
            report.model_influence = 0.7
        elif report.metrics.model_confidence > 70:
            report.recommendation = "Model is performing well — full influence"
            report.model_influence = 1.0
        else:
            report.recommendation = "Model performance is acceptable"
            report.model_influence = 0.9

        return report

    def get_model_influence(self) -> float:
        """Get current model influence (0-1)."""
        report = self.analyze()
        return report.model_influence
