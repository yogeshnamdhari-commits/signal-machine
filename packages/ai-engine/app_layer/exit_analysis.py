"""
Exit Analysis Dashboard — Identify why Profit Factor fell by analyzing exits.

Per Executive Assessment v16:
    "Split every winning trade into buckets.
     Exit Reason    Avg R    Number    TP1 ?? Trailing Stop ?? Time Exit ??
     If 70% of profitable trades are exiting via one mechanism with poor
     average return, you've found a concrete optimization target."

Key Innovation:
    v21 measured: Overall execution quality
    v22 analyzes: Exit-specific performance to find optimization targets

    This allows:
        - Identifying which exit mechanisms are underperforming
        - Finding concrete optimization targets
        - Isolating exit-related profit leaks
        - Data-driven exit improvement

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
class ExitBucket:
    """Performance metrics for a single exit reason."""
    exit_reason: str = ""
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    avg_winner_r: float = 0.0
    avg_loser_r: float = 0.0
    profit_factor: float = 0.0
    total_pnl: float = 0.0
    avg_mfe_r: float = 0.0
    avg_mae_r: float = 0.0
    profit_capture_pct: float = 0.0
    avg_hold_minutes: float = 0.0

    # Contribution
    contribution_pct: float = 0.0  # % of total PnL
    pnl_contribution: float = 0.0  # Absolute PnL contribution

    # Rating
    rating: str = ""  # EXCELLENT / GOOD / AVERAGE / POOR / AVOID

    def to_dict(self) -> Dict:
        return {
            "exit_reason": self.exit_reason,
            "trades": self.trade_count,
            "wins": self.win_count,
            "losses": self.loss_count,
            "win_rate": round(self.win_rate, 3),
            "avg_r": round(self.avg_r, 3),
            "avg_winner_r": round(self.avg_winner_r, 3),
            "avg_loser_r": round(self.avg_loser_r, 3),
            "profit_factor": round(self.profit_factor, 2),
            "total_pnl": round(self.total_pnl, 2),
            "avg_mfe_r": round(self.avg_mfe_r, 3),
            "avg_mae_r": round(self.avg_mae_r, 3),
            "profit_capture_pct": round(self.profit_capture_pct, 1),
            "avg_hold_minutes": round(self.avg_hold_minutes, 1),
            "contribution_pct": round(self.contribution_pct, 1),
            "rating": self.rating,
        }


@dataclass
class ExitAnalysisReport:
    """Complete exit analysis report."""
    timestamp: float = 0.0

    # Overall exit metrics
    total_trades: int = 0
    overall_pf: float = 0.0
    overall_avg_r: float = 0.0
    overall_profit_capture: float = 0.0

    # By exit reason
    exit_buckets: List[ExitBucket] = field(default_factory=list)

    # Problem identification
    worst_exits: List[ExitBucket] = field(default_factory=list)
    best_exits: List[ExitBucket] = field(default_factory=list)
    optimization_targets: List[str] = field(default_factory=list)

    # Diagnosis
    diagnosis: str = ""
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "overall": {
                "trades": self.total_trades,
                "pf": round(self.overall_pf, 3),
                "avg_r": round(self.overall_avg_r, 3),
                "profit_capture": round(self.overall_profit_capture, 1),
            },
            "exit_buckets": [b.to_dict() for b in self.exit_buckets],
            "worst_exits": [b.to_dict() for b in self.worst_exits[:3]],
            "best_exits": [b.to_dict() for b in self.best_exits[:3]],
            "optimization_targets": self.optimization_targets,
            "diagnosis": self.diagnosis,
            "recommendations": self.recommendations,
        }


class ExitAnalysisDashboard:
    """
    Analyzes exit performance to find optimization targets.

    Per Executive Assessment v16:
        "If 70% of profitable trades are exiting via one mechanism
         with poor average return, you've found a concrete optimization target."

    This engine:
        1. Groups trades by exit reason
        2. Calculates performance metrics per exit type
        3. Identifies underperforming exits
        4. Recommends concrete optimization targets

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
                SELECT symbol, side, realized_r, pnl, mfe_pct, mae_pct,
                       highest_pnl, exit_reason, hold_minutes
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load exit analysis dashboard: {}", e)

    def analyze(self) -> ExitAnalysisReport:
        """
        Analyze exit performance and generate report.

        Returns:
            ExitAnalysisReport with exit-specific analysis
        """
        self._ensure_loaded()

        report = ExitAnalysisReport(timestamp=time.time())

        if not self._trades:
            return report

        report.total_trades = len(self._trades)

        # ── Overall metrics ──
        wins = [t.get("realized_r", 0) or 0 for t in self._trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in self._trades if (t.get("realized_r", 0) or 0) < 0]
        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        report.overall_pf = gross_profit / max(0.01, gross_loss)

        all_r = [t.get("realized_r", 0) or 0 for t in self._trades]
        report.overall_avg_r = sum(all_r) / max(1, len(all_r))

        # Overall profit capture
        capture_vals = []
        for t in self._trades:
            mfe = t.get("highest_pnl", 0) or 0
            r = t.get("realized_r", 0) or 0
            if mfe > 0:
                capture_vals.append((r / mfe) * 100)
        report.overall_profit_capture = sum(capture_vals) / max(1, len(capture_vals))

        # ── By exit reason ──
        by_reason: Dict[str, List[Dict]] = defaultdict(list)
        for t in self._trades:
            reason = t.get("exit_reason", "unknown")
            by_reason[reason].append(t)

        total_pnl = sum(t.get("pnl", 0) or 0 for t in self._trades)

        for reason, trades in by_reason.items():
            bucket = self._calc_exit_bucket(reason, trades, total_pnl)
            report.exit_buckets.append(bucket)

        # Sort by PnL contribution
        report.exit_buckets.sort(key=lambda b: b.total_pnl, reverse=True)

        # ── Identify problems ──
        report.worst_exits = sorted(
            [b for b in report.exit_buckets if b.trade_count >= 5],
            key=lambda b: b.profit_factor,
        )[:3]

        report.best_exits = sorted(
            [b for b in report.exit_buckets if b.trade_count >= 5],
            key=lambda b: b.profit_factor,
            reverse=True,
        )[:3]

        # ── Optimization targets ──
        report.optimization_targets = self._find_optimization_targets(report.exit_buckets)

        # ── Diagnosis ──
        report.diagnosis = self._diagnose(report)
        report.recommendations = self._recommend(report)

        return report

    def _calc_exit_bucket(
        self,
        reason: str,
        trades: List[Dict],
        total_pnl: float,
    ) -> ExitBucket:
        """Calculate metrics for a single exit reason."""
        bucket = ExitBucket(exit_reason=reason, trade_count=len(trades))

        wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]

        bucket.win_count = len(wins)
        bucket.loss_count = len(losses)
        bucket.win_rate = len(wins) / max(1, len(trades))

        all_r = [t.get("realized_r", 0) or 0 for t in trades]
        bucket.avg_r = sum(all_r) / max(1, len(all_r))
        bucket.avg_winner_r = sum(wins) / max(1, len(wins)) if wins else 0
        bucket.avg_loser_r = sum(losses) / max(1, len(losses)) if losses else 0

        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        bucket.profit_factor = gross_profit / max(0.01, gross_loss)

        bucket.total_pnl = sum(t.get("pnl", 0) or 0 for t in trades)

        # MFE/MAE
        mfe_vals = [t.get("highest_pnl", 0) or 0 for t in trades if (t.get("highest_pnl", 0) or 0) > 0]
        mae_vals = [abs(t.get("mae_pct", 0) or 0) for t in trades if (t.get("mae_pct", 0) or 0) > 0]
        bucket.avg_mfe_r = sum(mfe_vals) / max(1, len(mfe_vals))
        bucket.avg_mae_r = sum(mae_vals) / max(1, len(mae_vals))

        # Profit capture
        capture_vals = []
        for t in trades:
            mfe = t.get("highest_pnl", 0) or 0
            r = t.get("realized_r", 0) or 0
            if mfe > 0:
                capture_vals.append((r / mfe) * 100)
        bucket.profit_capture_pct = sum(capture_vals) / max(1, len(capture_vals))

        # Hold time
        hold_vals = [t.get("hold_minutes", 0) or 0 for t in trades]
        bucket.avg_hold_minutes = sum(hold_vals) / max(1, len(hold_vals))

        # Contribution
        bucket.contribution_pct = (bucket.total_pnl / max(0.01, abs(total_pnl))) * 100 if total_pnl != 0 else 0

        # Rating
        if bucket.profit_factor > 1.5 and bucket.avg_r > 0.3:
            bucket.rating = "EXCELLENT"
        elif bucket.profit_factor > 1.2:
            bucket.rating = "GOOD"
        elif bucket.profit_factor > 0.9:
            bucket.rating = "AVERAGE"
        elif bucket.profit_factor > 0.6:
            bucket.rating = "POOR"
        else:
            bucket.rating = "AVOID"

        return bucket

    def _find_optimization_targets(self, buckets: List[ExitBucket]) -> List[str]:
        """Find concrete optimization targets."""
        targets = []

        for bucket in buckets:
            if bucket.trade_count < 5:
                continue

            if bucket.rating in ("POOR", "AVOID"):
                targets.append(
                    f"{bucket.exit_reason}: PF={bucket.profit_factor:.2f}, "
                    f"avg_r={bucket.avg_r:.3f}, capture={bucket.profit_capture_pct:.1f}% "
                    f"— {bucket.trade_count} trades"
                )
            elif bucket.profit_capture_pct < 30 and bucket.trade_count >= 10:
                targets.append(
                    f"{bucket.exit_reason}: Low capture ({bucket.profit_capture_pct:.1f}%) "
                    f"— {bucket.trade_count} trades"
                )

        return targets

    def _diagnose(self, report: ExitAnalysisReport) -> str:
        """Diagnose exit performance."""
        poor_exits = [b for b in report.exit_buckets if b.rating in ("POOR", "AVOID") and b.trade_count >= 5]

        if not poor_exits:
            return "No exit mechanisms are significantly underperforming"

        total_poor = sum(b.trade_count for b in poor_exits)
        poor_pct = (total_poor / max(1, report.total_trades)) * 100

        return (
            f"{len(poor_exits)} exit mechanisms are underperforming, "
            f"affecting {poor_pct:.0f}% of trades"
        )

    def _recommend(self, report: ExitAnalysisReport) -> List[str]:
        """Generate recommendations."""
        recs = []

        for target in report.optimization_targets[:3]:
            recs.append(f"OPTIMIZE: {target}")

        if report.overall_profit_capture < 50:
            recs.append(
                f"Overall profit capture is {report.overall_profit_capture:.1f}% — "
                f"trades are leaving significant profit on the table"
            )

        if not recs:
            recs.append("Exit performance is acceptable — no immediate action needed")

        return recs
