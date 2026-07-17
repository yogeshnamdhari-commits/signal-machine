"""
Admission Dashboard — Full Confusion Matrix for Admission Quality.

Per v25 directive:
    "Calculate the confusion matrix: TP/FP/FN/TN, then derive
     Precision, Recall, F1, Specificity, False Discovery Rate."

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"
THRESHOLDS = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]


@dataclass
class ConfusionMatrix:
    """Full confusion matrix for admission decisions."""
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    true_negative: int = 0
    precision: float = 0.0
    recall: float = 0.0
    f1_score: float = 0.0
    specificity: float = 0.0
    false_discovery_rate: float = 0.0
    false_omission_rate: float = 0.0
    false_rejection_rate: float = 0.0  # FN / (FN + TN) — would-have-won rate
    accuracy: float = 0.0
    formatted: str = ""  # Human-readable confusion matrix table
    total_accepted: int = 0
    total_rejected: int = 0
    total_good: int = 0
    total_bad: int = 0
    total_signals: int = 0

    def to_dict(self) -> Dict:
        return {
            "matrix": {"true_positive": self.true_positive, "false_positive": self.false_positive,
                        "false_negative": self.false_negative, "true_negative": self.true_negative},
            "derived": {"precision": round(self.precision, 4), "recall": round(self.recall, 4),
                         "f1_score": round(self.f1_score, 4), "specificity": round(self.specificity, 4),
                         "false_discovery_rate": round(self.false_discovery_rate, 4),
                         "false_omission_rate": round(self.false_omission_rate, 4),
                         "accuracy": round(self.accuracy, 4)},
            "false_rejection_rate": round(self.false_rejection_rate, 4),
            "totals": {"accepted": self.total_accepted, "rejected": self.total_rejected,
                        "good": self.total_good, "bad": self.total_bad, "signals": self.total_signals},
        }


@dataclass
class ThresholdAnalysis:
    """Analysis at a specific admission threshold."""
    threshold: float = 0.0
    confusion: ConfusionMatrix = field(default_factory=ConfusionMatrix)
    expected_value: float = 0.0
    admission_pf: float = 0.0

    def to_dict(self) -> Dict:
        return {"threshold": self.threshold, "confusion": self.confusion.to_dict(),
                "expected_value": round(self.expected_value, 4), "admission_pf": round(self.admission_pf, 4)}


@dataclass
class AdmissionDashboard:
    """Complete admission dashboard with full confusion matrix."""
    timestamp: float = 0.0
    confusion: ConfusionMatrix = field(default_factory=ConfusionMatrix)
    threshold_analyses: List[ThresholdAnalysis] = field(default_factory=list)
    optimal_threshold: float = 85.0
    optimal_f1: float = 0.0
    optimal_precision: float = 0.0
    by_confidence: List[Dict] = field(default_factory=list)
    quality_score: float = 0.0
    diagnosis: str = ""
    recommendation: str = ""
    precision_trend: str = ""

    # v26 additions — the two statistics that matter
    accepted_but_losers: List[Dict] = field(default_factory=list)   # Accepted trades that became losers
    rejected_but_winners: List[Dict] = field(default_factory=list)  # Rejected trades that would have won
    accepted_but_losers_count: int = 0
    rejected_but_winners_count: int = 0
    accepted_but_losers_r: float = 0.0     # Total R lost to bad admissions
    rejected_but_winners_r: float = 0.0    # Total R missed from bad rejections

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "confusion": self.confusion.to_dict(),
            "threshold_analyses": [ta.to_dict() for ta in self.threshold_analyses],
            "optimal": {"threshold": round(self.optimal_threshold, 1), "f1": round(self.optimal_f1, 4),
                         "precision": round(self.optimal_precision, 4)},
            "by_confidence": self.by_confidence,
            "accepted_but_losers": self.accepted_but_losers[:10],
            "rejected_but_winners": self.rejected_but_winners[:10],
            "accepted_but_losers_count": self.accepted_but_losers_count,
            "rejected_but_winners_count": self.rejected_but_winners_count,
            "accepted_but_losers_r": round(self.accepted_but_losers_r, 4),
            "rejected_but_winners_r": round(self.rejected_but_winners_r, 4),
            "quality_score": round(self.quality_score, 1),
            "diagnosis": self.diagnosis, "recommendation": self.recommendation, "trend": self.precision_trend,
        }


class AdmissionDashboardEngine:
    """Full confusion matrix dashboard for admission quality."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        if time.time() - self._last_load < 300: return
        self._load_trades()

    def _load_trades(self) -> None:
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT symbol, side, realized_r, confidence, institutional_score
                FROM positions_archive WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()
            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()
        except Exception as e:
            logger.warning("Could not load admission dashboard: {}", e)

    def evaluate(self) -> AdmissionDashboard:
        self._ensure_loaded()
        dashboard = AdmissionDashboard(timestamp=time.time())
        if not self._trades or len(self._trades) < 20:
            dashboard.diagnosis = "Insufficient data (need >=20 trades)"
            return dashboard

        trades = self._trades[:300]

        # Confusion matrix at threshold 85
        dashboard.confusion = self._build_confusion_matrix(trades, threshold=85)

        # v26: Track accepted-but-losers and rejected-but-winners
        threshold = 0.85
        for t in trades:
            conf = (t.get("confidence", 0) or 0)
            r = (t.get("realized_r", 0) or 0)
            accepted = conf >= threshold
            actually_good = r > 0

            if accepted and not actually_good:
                # Accepted but became a loser
                dashboard.accepted_but_losers_count += 1
                dashboard.accepted_but_losers_r += r
                if len(dashboard.accepted_but_losers) < 20:
                    dashboard.accepted_but_losers.append({
                        "symbol": t.get("symbol", "?"), "side": t.get("side", "?"),
                        "confidence": round(conf, 1), "realized_r": round(r, 4),
                        "exit_reason": t.get("exit_reason", "unknown"),
                    })
            elif not accepted and actually_good:
                # Rejected but would have been a winner
                dashboard.rejected_but_winners_count += 1
                dashboard.rejected_but_winners_r += r
                if len(dashboard.rejected_but_winners) < 20:
                    dashboard.rejected_but_winners.append({
                        "symbol": t.get("symbol", "?"), "side": t.get("side", "?"),
                        "confidence": round(conf, 1), "realized_r": round(r, 4),
                        "exit_reason": t.get("exit_reason", "unknown"),
                    })

        # Per-threshold analysis
        for thresh in THRESHOLDS:
            ta = ThresholdAnalysis(threshold=thresh)
            ta.confusion = self._build_confusion_matrix(trades, threshold=thresh)
            accepted = [t for t in trades if (t.get("confidence", 0) or 0) >= thresh]
            if accepted:
                ta.expected_value = sum(t.get("realized_r", 0) or 0 for t in accepted) / len(accepted)
                ta.admission_pf = self._calc_pf(accepted)
            dashboard.threshold_analyses.append(ta)

        # Optimal threshold by F1
        best_f1 = 0; best_thresh = 0.85; best_prec = 0
        for ta in dashboard.threshold_analyses:
            if ta.confusion.f1_score > best_f1:
                best_f1 = ta.confusion.f1_score; best_thresh = ta.threshold; best_prec = ta.confusion.precision
        dashboard.optimal_threshold = best_thresh
        dashboard.optimal_f1 = best_f1
        dashboard.optimal_precision = best_prec

        # By confidence bucket
        bucket_defs = [("0.95-1.00", 0.95, 1.00), ("0.90-0.95", 0.90, 0.95), ("0.85-0.90", 0.85, 0.90),
                       ("0.80-0.85", 0.80, 0.85), ("0.75-0.80", 0.75, 0.80), ("<0.75", 0, 0.75)]
        for label, min_s, max_s in bucket_defs:
            bt = [t for t in trades if min_s <= (t.get("confidence", 0) or 0) < max_s]
            if bt:
                b_wins = sum(1 for t in bt if (t.get("realized_r", 0) or 0) > 0)
                b_r = [t.get("realized_r", 0) or 0 for t in bt]
                avg_r = sum(b_r) / len(b_r)
                w = sum(r for r in b_r if r > 0)
                l = sum(abs(r) for r in b_r if r < 0)
                pf = w / max(0.01, l)
                dashboard.by_confidence.append({
                    "bucket": label, "trades": len(bt), "winners": b_wins,
                    "losers": len(bt) - b_wins, "win_rate": round(b_wins / len(bt) * 100, 1),
                    "avg_r": round(avg_r, 3), "profit_factor": round(pf, 3),
                    "total_r": round(sum(b_r), 3), "sample_size": len(bt)})
            else:
                dashboard.by_confidence.append({
                    "bucket": label, "trades": 0, "winners": 0, "losers": 0,
                    "win_rate": 0.0, "avg_r": 0.0, "profit_factor": 0.0,
                    "total_r": 0.0, "sample_size": 0})

        cm = dashboard.confusion
        dashboard.quality_score = max(0, min(100, cm.precision * 100))

        if cm.precision > 0.6:
            dashboard.diagnosis = f"Filter effective (precision={cm.precision:.1%}, specificity={cm.specificity:.1%})"
        elif cm.precision > 0.4:
            dashboard.diagnosis = f"Filter moderate (precision={cm.precision:.1%}, FDR={cm.false_discovery_rate:.1%})"
        elif cm.precision > 0.2:
            dashboard.diagnosis = f"Filter weak (precision={cm.precision:.1%}, FDR={cm.false_discovery_rate:.1%}) — needs calibration"
        else:
            dashboard.diagnosis = f"Filter not helping (precision={cm.precision:.1%}, FDR={cm.false_discovery_rate:.1%})"

        if best_thresh != 0.85:
            dashboard.recommendation = f"Raise threshold from 0.85 to {best_thresh} — F1 improves from {cm.f1_score:.3f} to {best_f1:.3f}"
        elif cm.false_discovery_rate > 0.5:
            dashboard.recommendation = f"FDR is {cm.false_discovery_rate:.1%} — more than half of accepted trades are losers"
        else:
            dashboard.recommendation = "Current threshold is near optimal"

        if len(trades) >= 100:
            recent_wr = sum(1 for t in trades[:50] if (t.get("realized_r", 0) or 0) > 0) / 50
            older_wr = sum(1 for t in trades[50:100] if (t.get("realized_r", 0) or 0) > 0) / 50
            if recent_wr > older_wr + 0.05: dashboard.precision_trend = "IMPROVING"
            elif recent_wr < older_wr - 0.05: dashboard.precision_trend = "DECLINING"
            else: dashboard.precision_trend = "STABLE"

        return dashboard

    def _build_confusion_matrix(self, trades, threshold):
        cm = ConfusionMatrix()
        cm.total_signals = len(trades)
        for t in trades:
            conf = (t.get("confidence", 0) or 0)
            r = (t.get("realized_r", 0) or 0)
            accepted = conf >= threshold
            actually_good = r > 0
            if accepted and actually_good: cm.true_positive += 1
            elif accepted and not actually_good: cm.false_positive += 1
            elif not accepted and actually_good: cm.false_negative += 1
            else: cm.true_negative += 1

        cm.total_accepted = cm.true_positive + cm.false_positive
        cm.total_rejected = cm.false_negative + cm.true_negative
        cm.total_good = cm.true_positive + cm.false_negative
        cm.total_bad = cm.false_positive + cm.true_negative
        cm.precision = cm.true_positive / max(1, cm.true_positive + cm.false_positive)
        cm.recall = cm.true_positive / max(1, cm.true_positive + cm.false_negative)
        if cm.precision + cm.recall > 0:
            cm.f1_score = 2 * cm.precision * cm.recall / (cm.precision + cm.recall)
        cm.specificity = cm.true_negative / max(1, cm.true_negative + cm.false_positive)
        cm.false_discovery_rate = cm.false_positive / max(1, cm.true_positive + cm.false_positive)
        cm.false_omission_rate = cm.false_negative / max(1, cm.false_negative + cm.true_negative)
        cm.false_rejection_rate = cm.false_negative / max(1, cm.false_negative + cm.true_negative)
        cm.accuracy = (cm.true_positive + cm.true_negative) / max(1, cm.total_signals)

        # v27: Formatted matrix for display
        cm.formatted = (
            f"{'Decision':>20} | {'Winner':>10} | {'Loser':>10} | {'Total':>10}\n"
            f"{'─' * 20}─┼─{'─' * 10}─┼─{'─' * 10}─┼─{'─' * 10}\n"
            f"{'Accepted':>20} | {cm.true_positive:>10} | {cm.false_positive:>10} | {cm.total_accepted:>10}\n"
            f"{'Rejected':>20} | {cm.false_negative:>10} | {cm.true_negative:>10} | {cm.total_rejected:>10}\n"
            f"{'─' * 20}─┼─{'─' * 10}─┼─{'─' * 10}─┼─{'─' * 10}\n"
            f"{'Total':>20} | {cm.total_good:>10} | {cm.total_bad:>10} | {cm.total_signals:>10}\n\n"
            f"Precision: {cm.precision:.1%} (Accepted that won / Total accepted)\n"
            f"Recall:    {cm.recall:.1%} (Accepted that won / Total winners)\n"
            f"FDR:       {cm.false_discovery_rate:.1%} (Accepted that lost / Total accepted)\n"
            f"FRR:       {cm.false_rejection_rate:.1%} (Rejected that would have won / Total rejected)\n"
            f"Specificity: {cm.specificity:.1%} (Rejected that lost / Total losers)"
        )
        return cm

    def _calc_pf(self, trades):
        wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]
        return sum(wins) / max(0.01, sum(losses))
