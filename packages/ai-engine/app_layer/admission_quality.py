"""
Trade Admission Quality Tracker — Measure false admissions and false rejections.

Per Executive Assessment v14:
    "I still believe the dashboard is missing the single metric that matters most:
     Trade Admission Quality.
     For every trade rejected, store:
         Rejected ↓ Would it have won?
     For every trade accepted, store:
         Accepted ↓ Should it have been rejected?
     Then calculate:
         Admission Precision
         Admission Recall
         False Admission Rate
         False Rejection Rate
     This directly evaluates the quality of the entry decision process."

Key Innovation:
    v19 measured: Downstream performance (PF, expectancy)
    v20 measures: Admission quality (precision, recall, false rates)

    This allows:
        - Direct evaluation of entry decision quality
        - Calibration of admission thresholds
        - Balancing selectivity vs opportunity cost
        - Improving trade admission without changing exits

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
_LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "admission_quality.json"


@dataclass
class AdmissionRecord:
    """Record of a trade admission decision and its outcome."""
    trade_id: str = ""
    symbol: str = ""
    side: str = ""
    decision_time: float = 0.0

    # Admission decision
    admitted: bool = False
    admission_score: float = 0.0
    admission_reason: str = ""

    # Actual outcome (filled after trade closes)
    actual_r: float = 0.0
    actual_outcome_tracked: bool = False

    # Classification
    true_positive: bool = False    # Admitted and profitable
    false_positive: bool = False   # Admitted but lost
    true_negative: bool = False    # Rejected and would have lost
    false_negative: bool = False   # Rejected but would have been profitable

    def to_dict(self) -> Dict:
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "side": self.side,
            "admitted": self.admitted,
            "admission_score": round(self.admission_score, 1),
            "actual_r": round(self.actual_r, 3),
            "tracked": self.actual_outcome_tracked,
            "tp": self.true_positive,
            "fp": self.false_positive,
            "tn": self.true_negative,
            "fn": self.false_negative,
        }


@dataclass
class AdmissionMetrics:
    """Aggregated admission quality metrics."""
    # Sample sizes
    total_admitted: int = 0
    total_rejected: int = 0
    tracked_admitted: int = 0
    tracked_rejected: int = 0

    # Classification counts
    true_positives: int = 0      # Admitted and profitable
    false_positives: int = 0     # Admitted but lost
    true_negatives: int = 0      # Rejected and would have lost
    false_negatives: int = 0     # Rejected but would have been profitable

    # Quality metrics
    precision: float = 0.0       # TP / (TP + FP) — how many admitted trades were profitable
    recall: float = 0.0          # TP / (TP + FN) — how many profitable trades were admitted
    f1_score: float = 0.0        # Harmonic mean of precision and recall
    false_admission_rate: float = 0.0   # FP / (TP + FP) — how many admitted trades lost
    false_rejection_rate: float = 0.0   # FN / (TN + FN) — how many profitable trades were rejected

    # Profitability
    avg_admitted_r: float = 0.0
    avg_rejected_r: float = 0.0
    admitted_profitable_pct: float = 0.0
    rejected_profitable_pct: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "sample_sizes": {
                "admitted": self.total_admitted,
                "rejected": self.total_rejected,
                "tracked_admitted": self.tracked_admitted,
                "tracked_rejected": self.tracked_rejected,
            },
            "classification": {
                "true_positives": self.true_positives,
                "false_positives": self.false_positives,
                "true_negatives": self.true_negatives,
                "false_negatives": self.false_negatives,
            },
            "quality": {
                "precision": round(self.precision, 3),
                "recall": round(self.recall, 3),
                "f1_score": round(self.f1_score, 3),
                "false_admission_rate": round(self.false_admission_rate, 3),
                "false_rejection_rate": round(self.false_rejection_rate, 3),
            },
            "profitability": {
                "avg_admitted_r": round(self.avg_admitted_r, 3),
                "avg_rejected_r": round(self.avg_rejected_r, 3),
                "admitted_profitable_pct": round(self.admitted_profitable_pct, 1),
                "rejected_profitable_pct": round(self.rejected_profitable_pct, 1),
            },
        }


@dataclass
class AdmissionQualityReport:
    """Complete admission quality analysis."""
    timestamp: float = 0.0
    metrics: AdmissionMetrics = field(default_factory=AdmissionMetrics)

    # Diagnosis
    is_well_calibrated: bool = False
    diagnosis: str = ""
    recommendation: str = ""

    # Threshold adjustment
    suggested_threshold_change: float = 0.0  # Positive = raise threshold

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "metrics": self.metrics.to_dict(),
            "diagnosis": {
                "well_calibrated": self.is_well_calibrated,
                "description": self.diagnosis,
                "recommendation": self.recommendation,
            },
            "threshold_adjustment": round(self.suggested_threshold_change, 1),
        }


class AdmissionQualityTracker:
    """
    Tracks trade admission quality (precision, recall, false rates).

    Per Executive Assessment v14:
        "This directly evaluates the quality of the entry decision process."

    This engine:
        1. Logs admission decisions
        2. Matches to actual outcomes
        3. Calculates precision, recall, F1
        4. Measures false admission and rejection rates
        5. Recommends threshold adjustments

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None, log_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._log_path = log_path or _LOG_PATH
        self._records: List[AdmissionRecord] = []
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        """Load records from log file."""
        if time.time() - self._last_load < 300:
            return
        self._load_records()

    def _load_records(self) -> None:
        """Load admission records from JSON log."""
        try:
            if self._log_path.exists():
                with open(self._log_path, "r") as f:
                    data = json.load(f)
                # Filter out extra fields that aren't in __init__
                valid_fields = {f.name for f in AdmissionRecord.__dataclass_fields__.values()}
                filtered_data = [{k: v for k, v in item.items() if k in valid_fields} for item in data]
                self._records = [AdmissionRecord(**item) for item in filtered_data]
            else:
                self._records = []
            self._last_load = time.time()
        except Exception as e:
            logger.warning("Could not load admission quality tracker: {}", e)
            self._records = []

    def _save_records(self) -> None:
        """Save records to JSON log."""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log_path, "w") as f:
                json.dump([r.to_dict() for r in self._records[-2000:]], f, indent=2)
        except Exception as e:
            logger.warning("Could not save admission records: {}", e)

    def log_admission(
        self,
        symbol: str,
        side: str,
        admitted: bool,
        score: float = 0.0,
        reason: str = "",
    ) -> None:
        """Log a trade admission decision."""
        record = AdmissionRecord(
            trade_id=f"{symbol}_{side}_{int(time.time())}",
            symbol=symbol,
            side=side,
            decision_time=time.time(),
            admitted=admitted,
            admission_score=score,
            admission_reason=reason,
        )
        self._records.append(record)
        self._save_records()

    def track_outcome(
        self,
        symbol: str,
        side: str,
        actual_r: float,
    ) -> None:
        """Track the actual outcome of an admitted trade."""
        cutoff = time.time() - 86400  # 24 hours
        for record in reversed(self._records):
            if (record.symbol == symbol
                and record.side == side
                and record.decision_time > cutoff
                and not record.actual_outcome_tracked):

                record.actual_outcome_tracked = True
                record.actual_r = actual_r

                # Classify
                if record.admitted and actual_r > 0:
                    record.true_positive = True
                elif record.admitted and actual_r <= 0:
                    record.false_positive = True
                elif not record.admitted and actual_r > 0:
                    record.false_negative = True
                else:
                    record.true_negative = True

                logger.debug(
                    "📊 ADMISSION TRACKED: {} {} admitted={} actual={:.2f}R → {}",
                    symbol, side, record.admitted, actual_r,
                    "TP" if record.true_positive else
                    "FP" if record.false_positive else
                    "FN" if record.false_negative else "TN",
                )
                break

        self._save_records()

    def analyze(self) -> AdmissionQualityReport:
        """Analyze admission quality and generate report."""
        self._ensure_loaded()

        report = AdmissionQualityReport(timestamp=time.time())

        admitted = [r for r in self._records if r.admitted]
        rejected = [r for r in self._records if not r.admitted]
        tracked_admitted = [r for r in admitted if r.actual_outcome_tracked]
        tracked_rejected = [r for r in rejected if r.actual_outcome_tracked]

        report.metrics.total_admitted = len(admitted)
        report.metrics.total_rejected = len(rejected)
        report.metrics.tracked_admitted = len(tracked_admitted)
        report.metrics.tracked_rejected = len(tracked_rejected)

        if not tracked_admitted and not tracked_rejected:
            report.diagnosis = "No tracked outcomes yet"
            report.recommendation = "Continue tracking admission decisions and outcomes"
            return report

        # ── Classification counts ──
        report.metrics.true_positives = sum(1 for r in tracked_admitted if r.true_positive)
        report.metrics.false_positives = sum(1 for r in tracked_admitted if r.false_positive)
        report.metrics.true_negatives = sum(1 for r in tracked_rejected if r.true_negative)
        report.metrics.false_negatives = sum(1 for r in tracked_rejected if r.false_negative)

        # ── Quality metrics ──
        tp = report.metrics.true_positives
        fp = report.metrics.false_positives
        tn = report.metrics.true_negatives
        fn = report.metrics.false_negatives

        # Precision: TP / (TP + FP) — how many admitted trades were profitable
        if tp + fp > 0:
            report.metrics.precision = tp / (tp + fp)

        # Recall: TP / (TP + FN) — how many profitable trades were admitted
        if tp + fn > 0:
            report.metrics.recall = tp / (tp + fn)

        # F1 Score
        if report.metrics.precision + report.metrics.recall > 0:
            report.metrics.f1_score = 2 * report.metrics.precision * report.metrics.recall / (
                report.metrics.precision + report.metrics.recall
            )

        # False Admission Rate: FP / (TP + FP)
        if tp + fp > 0:
            report.metrics.false_admission_rate = fp / (tp + fp)

        # False Rejection Rate: FN / (TN + FN)
        if tn + fn > 0:
            report.metrics.false_rejection_rate = fn / (tn + fn)

        # ── Profitability ──
        if tracked_admitted:
            report.metrics.avg_admitted_r = sum(r.actual_r for r in tracked_admitted) / len(tracked_admitted)
            report.metrics.admitted_profitable_pct = report.metrics.true_positives / len(tracked_admitted) * 100

        if tracked_rejected:
            report.metrics.avg_rejected_r = sum(r.actual_r for r in tracked_rejected) / len(tracked_rejected)
            report.metrics.rejected_profitable_pct = report.metrics.false_negatives / len(tracked_rejected) * 100

        # ── Diagnosis ──
        if report.metrics.precision > 0.6 and report.metrics.recall > 0.5:
            report.is_well_calibrated = True
            report.diagnosis = "Good admission quality (precision > 60%, recall > 50%)"
            report.recommendation = "Maintain current admission thresholds"
        elif report.metrics.false_admission_rate > 0.5:
            report.diagnosis = "Too many false admissions — admission is too loose"
            report.recommendation = "Raise admission threshold to reduce false positives"
            report.suggested_threshold_change = 5.0
        elif report.metrics.false_rejection_rate > 0.3:
            report.diagnosis = "Too many false rejections — admission is too strict"
            report.recommendation = "Lower admission threshold to capture more opportunities"
            report.suggested_threshold_change = -5.0
        else:
            report.diagnosis = "Admission quality is acceptable"
            report.recommendation = "Continue monitoring"

        return report
