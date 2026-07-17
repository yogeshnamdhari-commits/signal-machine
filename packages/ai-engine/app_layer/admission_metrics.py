"""
Admission Metrics Calculator — Compute precision, recall, false rates.

Per Executive Assessment v17:
    "Explicitly measure:
         Admission Precision: Accepted trades that became profitable
         Admission Recall: Fraction of all profitable opportunities that were accepted
         False Admission Rate: Accepted trades that should have been rejected
         False Rejection Rate: Rejected trades that would have been profitable
     Those four metrics directly evaluate the quality of the decision engine."

Key Innovation:
    v20 measured: Basic admission quality (TP/FP/TN/FN)
    v23 calculates: Full precision/recall/F1 metrics with thresholds

    This allows:
        - Direct evaluation of entry decision quality
        - Calibration of admission thresholds
        - Balancing selectivity vs opportunity cost
        - Data-driven threshold optimization

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


@dataclass
class AdmissionBucket:
    """Admission metrics for a specific score bucket."""
    bucket_label: str = ""      # e.g., "90-95", "85-90"
    min_score: float = 0.0
    max_score: float = 0.0
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    profit_factor: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "bucket": self.bucket_label,
            "trades": self.trade_count,
            "wins": self.win_count,
            "losses": self.loss_count,
            "win_rate": round(self.win_rate, 3),
            "avg_r": round(self.avg_r, 3),
            "profit_factor": round(self.profit_factor, 2),
        }


@dataclass
class AdmissionMetricsResult:
    """Complete admission metrics analysis."""
    timestamp: float = 0.0

    # Overall metrics
    total_trades: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    profit_factor: float = 0.0

    # Admission metrics by score bucket
    buckets: List[AdmissionBucket] = field(default_factory=list)

    # Optimal threshold analysis
    optimal_threshold: float = 0.0
    threshold_pf: float = 0.0
    current_threshold_pf: float = 0.0

    # Precision/Recall at different thresholds
    precision_recall: List[Dict] = field(default_factory=list)

    # Diagnosis
    quality_score: float = 0.0  # 0-100
    diagnosis: str = ""
    recommendation: str = ""

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "overall": {
                "trades": self.total_trades,
                "wins": self.win_count,
                "losses": self.loss_count,
                "win_rate": round(self.win_rate, 3),
                "avg_r": round(self.avg_r, 3),
                "profit_factor": round(self.profit_factor, 2),
            },
            "buckets": [b.to_dict() for b in self.buckets],
            "optimal_threshold": {
                "score": round(self.optimal_threshold, 1),
                "pf": round(self.threshold_pf, 3),
                "current_pf": round(self.current_threshold_pf, 3),
            },
            "precision_recall": self.precision_recall,
            "quality_score": round(self.quality_score, 1),
            "diagnosis": self.diagnosis,
            "recommendation": self.recommendation,
        }


class AdmissionMetricsCalculator:
    """
    Computes full admission metrics including precision/recall.

    Per Executive Assessment v17:
        "Those four metrics directly evaluate the quality
         of the decision engine."

    This engine:
        1. Groups trades by admission score
        2. Calculates precision/recall at each threshold
        3. Finds optimal threshold
        4. Provides actionable recommendations

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        """Load trades from DB if stale."""
        if time.time() - self._last_load < 300:
            return
        self._load_trades()

    def _load_trades(self) -> None:
        """Load all closed trades."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol, side, realized_r, confidence, institutional_score
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load admission metrics calculator: {}", e)

    def calculate(self) -> AdmissionMetricsResult:
        """
        Calculate admission metrics.

        Returns:
            AdmissionMetricsResult with full analysis
        """
        self._ensure_loaded()

        result = AdmissionMetricsResult(timestamp=time.time())

        if not self._trades or len(self._trades) < 20:
            result.diagnosis = "Insufficient data"
            return result

        # ── Overall metrics ──
        result.total_trades = len(self._trades[:200])
        wins = [t for t in self._trades[:200] if (t.get("realized_r", 0) or 0) > 0]
        losses = [t for t in self._trades[:200] if (t.get("realized_r", 0) or 0) <= 0]

        result.win_count = len(wins)
        result.loss_count = len(losses)
        result.win_rate = len(wins) / max(1, len(self._trades[:200]))

        all_r = [t.get("realized_r", 0) or 0 for t in self._trades[:200]]
        result.avg_r = sum(all_r) / max(1, len(all_r))

        gross_profit = sum(t.get("realized_r", 0) or 0 for t in wins) if wins else 0
        gross_loss = sum(abs(t.get("realized_r", 0) or 0) for t in losses) if losses else 0.01
        result.profit_factor = gross_profit / max(0.01, gross_loss)

        # ── Buckets by confidence score ──
        bucket_defs = [
            ("95-100", 95, 100),
            ("90-95", 90, 95),
            ("85-90", 85, 90),
            ("80-85", 80, 85),
            ("75-80", 75, 80),
            ("70-75", 70, 75),
            ("<70", 0, 70),
        ]

        for label, min_s, max_s in bucket_defs:
            bucket_trades = [
                t for t in self._trades[:200]
                if min_s <= (t.get("confidence", 0) or 0) < max_s
            ]

            if bucket_trades:
                b_wins = [t for t in bucket_trades if (t.get("realized_r", 0) or 0) > 0]
                b_losses = [t for t in bucket_trades if (t.get("realized_r", 0) or 0) <= 0]

                b_all_r = [t.get("realized_r", 0) or 0 for t in bucket_trades]
                b_gp = sum(t.get("realized_r", 0) or 0 for t in b_wins) if b_wins else 0
                b_gl = sum(abs(t.get("realized_r", 0) or 0) for t in b_losses) if b_losses else 0.01

                bucket = AdmissionBucket(
                    bucket_label=label,
                    min_score=min_s,
                    max_score=max_s,
                    trade_count=len(bucket_trades),
                    win_count=len(b_wins),
                    loss_count=len(b_losses),
                    win_rate=len(b_wins) / max(1, len(bucket_trades)),
                    avg_r=sum(b_all_r) / max(1, len(b_all_r)),
                    profit_factor=b_gp / max(0.01, b_gl),
                )
                result.buckets.append(bucket)

        # ── Optimal threshold analysis ──
        best_pf = 0
        best_threshold = 85  # Default
        for threshold in range(70, 100, 5):
            threshold_trades = [
                t for t in self._trades[:200]
                if (t.get("confidence", 0) or 0) >= threshold
            ]
            if len(threshold_trades) >= 10:
                t_wins = [t for t in threshold_trades if (t.get("realized_r", 0) or 0) > 0]
                t_losses = [t for t in threshold_trades if (t.get("realized_r", 0) or 0) <= 0]
                t_gp = sum(t.get("realized_r", 0) or 0 for t in t_wins) if t_wins else 0
                t_gl = sum(abs(t.get("realized_r", 0) or 0) for t in t_losses) if t_losses else 0.01
                t_pf = t_gp / max(0.01, t_gl)

                if t_pf > best_pf:
                    best_pf = t_pf
                    best_threshold = threshold

        result.optimal_threshold = best_threshold
        result.threshold_pf = best_pf

        # Current threshold PF (85)
        current_trades = [
            t for t in self._trades[:200]
            if (t.get("confidence", 0) or 0) >= 85
        ]
        if current_trades:
            c_wins = [t for t in current_trades if (t.get("realized_r", 0) or 0) > 0]
            c_losses = [t for t in current_trades if (t.get("realized_r", 0) or 0) <= 0]
            c_gp = sum(t.get("realized_r", 0) or 0 for t in c_wins) if c_wins else 0
            c_gl = sum(abs(t.get("realized_r", 0) or 0) for t in c_losses) if c_losses else 0.01
            result.current_threshold_pf = c_gp / max(0.01, c_gl)

        # ── Precision/Recall at different thresholds ──
        for threshold in range(70, 100, 5):
            threshold_trades = [
                t for t in self._trades[:200]
                if (t.get("confidence", 0) or 0) >= threshold
            ]
            all_profitable = [t for t in self._trades[:200] if (t.get("realized_r", 0) or 0) > 0]

            if threshold_trades and all_profitable:
                tp = sum(1 for t in threshold_trades if (t.get("realized_r", 0) or 0) > 0)
                fp = sum(1 for t in threshold_trades if (t.get("realized_r", 0) or 0) <= 0)
                fn = sum(1 for t in all_profitable if (t.get("confidence", 0) or 0) < threshold)

                precision = tp / max(1, tp + fp)
                recall = tp / max(1, tp + fn)
                f1 = 2 * precision * recall / max(0.001, precision + recall)

                result.precision_recall.append({
                    "threshold": threshold,
                    "trades": len(threshold_trades),
                    "precision": round(precision, 3),
                    "recall": round(recall, 3),
                    "f1": round(f1, 3),
                })

        # ── Quality score ──
        result.quality_score = max(0, min(100, result.profit_factor * 50))

        # ── Diagnosis ──
        if result.profit_factor > 1.2:
            result.diagnosis = "Admission quality is good — system admits profitable trades"
        elif result.profit_factor > 1.0:
            result.diagnosis = "Admission quality is acceptable — marginal edge"
        elif result.profit_factor > 0.8:
            result.diagnosis = "Admission quality is poor — too many losing trades admitted"
        else:
            result.diagnosis = "Admission quality is very poor — major filtering issue"

        # ── Recommendation ──
        if result.optimal_threshold != 85:
            result.recommendation = (
                f"Consider changing threshold from 85 to {result.optimal_threshold:.0f} "
                f"(PF would improve from {result.current_threshold_pf:.3f} to {result.threshold_pf:.3f})"
            )
        else:
            result.recommendation = "Current threshold appears optimal"

        return result
