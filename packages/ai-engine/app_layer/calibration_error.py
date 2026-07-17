"""
Calibration Error Tracker — Check if predicted probabilities match observed outcomes.

Per Executive Assessment v12:
    "Instead of only Prediction Error, track Calibration Error.
     Example:
         Predicted 80% probability
         Observed 52%
         That is a calibration issue.
     Good probability models should be both discriminative
     (separating good from bad trades) and well calibrated
     (their predicted probabilities match observed frequencies)."

Key Innovation:
    v17 measured: Prediction error (how far are predictions from actuals?)
    v18 measures: Calibration error (do probabilities match frequencies?)

    This allows:
        - Detecting overconfident predictions
        - Detecting underconfident predictions
        - Adjusting probability estimates
        - Improving decision quality

READ-ONLY: Never modifies upstream data.
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
_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "calibration_errors.json"


@dataclass
class CalibrationBucket:
    """A single bucket in the calibration curve."""
    predicted_prob: float = 0.0    # Predicted probability (midpoint of bucket)
    actual_freq: float = 0.0       # Observed frequency
    count: int = 0                 # Number of trades in this bucket
    calibration_error: float = 0.0 # |predicted - actual|
    overconfident: bool = False    # Predicted > actual
    underconfident: bool = False   # Predicted < actual

    def to_dict(self) -> Dict:
        return {
            "predicted": round(self.predicted_prob, 3),
            "actual": round(self.actual_freq, 3),
            "count": self.count,
            "error": round(self.calibration_error, 3),
            "overconfident": self.overconfident,
            "underconfident": self.underconfident,
        }


@dataclass
class CalibrationMetrics:
    """Aggregated calibration metrics."""
    # Expected Calibration Error (ECE)
    ece: float = 0.0              # Weighted average of bucket errors

    # Maximum Calibration Error (MCE)
    mce: float = 0.0              # Worst bucket error

    # Brier Score
    brier_score: float = 0.0      # Mean squared probability error

    # Calibration curve
    calibration_curve: List[CalibrationBucket] = field(default_factory=list)

    # Bias
    avg_predicted_prob: float = 0.0
    avg_actual_freq: float = 0.0
    calibration_bias: float = 0.0  # Positive = overconfident

    # Sample size
    total_predictions: int = 0
    matched_predictions: int = 0

    def to_dict(self) -> Dict:
        return {
            "ece": round(self.ece, 4),
            "mce": round(self.mce, 4),
            "brier_score": round(self.brier_score, 4),
            "calibration_curve": [b.to_dict() for b in self.calibration_curve],
            "avg_predicted": round(self.avg_predicted_prob, 3),
            "avg_actual": round(self.avg_actual_freq, 3),
            "bias": round(self.calibration_bias, 3),
            "total": self.total_predictions,
            "matched": self.matched_predictions,
        }


@dataclass
class CalibrationReport:
    """Complete calibration analysis."""
    timestamp: float = 0.0
    metrics: CalibrationMetrics = field(default_factory=CalibrationMetrics)

    # Diagnosis
    is_well_calibrated: bool = False
    diagnosis: str = ""
    recommendation: str = ""

    # Adjustments
    probability_adjustment: float = 1.0  # Multiplier for probabilities

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "metrics": self.metrics.to_dict(),
            "diagnosis": {
                "well_calibrated": self.is_well_calibrated,
                "description": self.diagnosis,
                "recommendation": self.recommendation,
            },
            "adjustment": round(self.probability_adjustment, 3),
        }


class CalibrationErrorTracker:
    """
    Checks if predicted probabilities match observed frequencies.

    Per Executive Assessment v12:
        "Good probability models should be both discriminative
         and well calibrated."

    This engine:
        1. Logs predicted probabilities
        2. Matches to actual outcomes
        3. Builds calibration curve
        4. Calculates ECE, MCE, Brier score
        5. Recommends probability adjustments

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None, log_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._log_path = log_path or _LOG_PATH
        self._records: List[Dict] = []
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        """Load records from log file."""
        if time.time() - self._last_load < 300:
            return
        self._load_records()

    def _load_records(self) -> None:
        """Load calibration records from JSON log."""
        try:
            if self._log_path.exists():
                with open(self._log_path, "r") as f:
                    self._records = json.load(f)
            else:
                self._records = []
            self._last_load = time.time()
        except Exception as e:
            logger.warning("Could not load calibration error tracker: {}", e)
            self._records = []

    def _save_records(self) -> None:
        """Save records to JSON log."""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "w") as f:
                json.dump(self._records[-2000:], f, indent=2)
        except Exception as e:
            logger.warning("Could not save calibration records: {}", e)

    def log_prediction(
        self,
        symbol: str,
        predicted_prob_profit: float,
        predicted_r: float = 0.0,
    ) -> None:
        """Log a probability prediction for later calibration check."""
        record = {
            "symbol": symbol,
            "predicted_prob_profit": predicted_prob_profit,
            "predicted_r": predicted_r,
            "prediction_time": time.time(),
            "actual_outcome": None,
            "actual_r": None,
        }
        self._records.append(record)
        self._save_records()

    def track_outcome(
        self,
        symbol: str,
        actual_r: float,
    ) -> None:
        """Track the actual outcome of a predicted trade."""
        cutoff = time.time() - 86400  # 24 hours
        for record in reversed(self._records):
            if (record.get("symbol") == symbol
                and record.get("prediction_time", 0) > cutoff
                and record.get("actual_outcome") is None):

                record["actual_outcome"] = 1 if actual_r > 0 else 0
                record["actual_r"] = actual_r

                logger.debug(
                    "📊 CALIBRATION TRACKED: {} predicted={:.3f} actual={}",
                    symbol, record.get("predicted_prob_profit", 0),
                    "win" if actual_r > 0 else "loss",
                )
                break

        self._save_records()

    def analyze(self) -> CalibrationReport:
        """Analyze calibration and generate report."""
        self._ensure_loaded()

        report = CalibrationReport(timestamp=time.time())

        # Filter to matched records
        matched = [r for r in self._records if r.get("actual_outcome") is not None]
        report.metrics.total_predictions = len(self._records)
        report.metrics.matched_predictions = len(matched)

        if len(matched) < 20:
            report.diagnosis = "Insufficient data for calibration analysis"
            report.recommendation = "Collect more predictions with outcomes"
            return report

        # ── Build calibration curve ──
        # Define probability buckets
        bucket_edges = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        buckets = []

        for i in range(len(bucket_edges) - 1):
            low = bucket_edges[i]
            high = bucket_edges[i + 1]
            mid = (low + high) / 2

            # Get trades in this bucket
            in_bucket = [
                r for r in matched
                if low <= r.get("predicted_prob_profit", 0) < high
            ]

            if in_bucket:
                actual_freq = sum(r.get("actual_outcome", 0) for r in in_bucket) / len(in_bucket)
                error = abs(mid - actual_freq)

                bucket = CalibrationBucket(
                    predicted_prob=mid,
                    actual_freq=actual_freq,
                    count=len(in_bucket),
                    calibration_error=error,
                    overconfident=mid > actual_freq + 0.05,
                    underconfident=mid < actual_freq - 0.05,
                )
                buckets.append(bucket)

        report.metrics.calibration_curve = buckets

        # ── Calculate ECE (Expected Calibration Error) ──
        if buckets:
            total_count = sum(b.count for b in buckets)
            report.metrics.ece = sum(
                b.calibration_error * b.count / max(1, total_count)
                for b in buckets
            )

        # ── Calculate MCE (Maximum Calibration Error) ──
        if buckets:
            report.metrics.mce = max(b.calibration_error for b in buckets)

        # ── Calculate Brier Score ──
        brier_scores = []
        for r in matched:
            pred = r.get("predicted_prob_profit", 0.5)
            actual = r.get("actual_outcome", 0)
            brier_scores.append((pred - actual) ** 2)
        report.metrics.brier_score = sum(brier_scores) / max(1, len(brier_scores))

        # ── Calculate bias ──
        report.metrics.avg_predicted_prob = sum(
            r.get("predicted_prob_profit", 0.5) for r in matched
        ) / len(matched)
        report.metrics.avg_actual_freq = sum(
            r.get("actual_outcome", 0) for r in matched
        ) / len(matched)
        report.metrics.calibration_bias = report.metrics.avg_predicted_prob - report.metrics.avg_actual_freq

        # ── Diagnosis ──
        if report.metrics.ece < 0.05:
            report.is_well_calibrated = True
            report.diagnosis = "Well calibrated (ECE < 0.05)"
            report.recommendation = "No adjustment needed"
            report.probability_adjustment = 1.0
        elif report.metrics.calibration_bias > 0.05:
            report.diagnosis = "Overconfident — predicted probabilities too high"
            report.recommendation = "Reduce predicted probabilities by %.0f%%" % (report.metrics.calibration_bias * 100)
            report.probability_adjustment = 1.0 - report.metrics.calibration_bias
        elif report.metrics.calibration_bias < -0.05:
            report.diagnosis = "Underconfident — predicted probabilities too low"
            report.recommendation = "Increase predicted probabilities by %.0f%%" % (abs(report.metrics.calibration_bias) * 100)
            report.probability_adjustment = 1.0 + abs(report.metrics.calibration_bias)
        else:
            report.is_well_calibrated = True
            report.diagnosis = "Acceptably calibrated (bias < 0.05)"
            report.recommendation = "Minor adjustments may improve performance"
            report.probability_adjustment = 1.0

        return report
