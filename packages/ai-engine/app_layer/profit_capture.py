"""
Profit Capture Dashboard — Measure how much profit is captured vs available.

Per Executive Assessment v16:
    "Instead of only Total PnL, display:
         Maximum Favorable Excursion (MFE) — How much profit was available?
         Realized Profit — How much was captured?
         Capture Ratio — Realized ÷ Available
         Exit Efficiency — Compare exit price with best achievable move

     This isolates whether the system is finding poor trades or
     managing good trades poorly."

Key Innovation:
    v21 measured: Overall execution quality
    v22 measures: Profit capture efficiency

    This allows:
        - Distinguishing trade selection from exit management
        - Finding specific profit leaks
        - Optimizing exit timing
        - Improving profit retention

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
class CaptureMetrics:
    """Profit capture metrics for a group of trades."""
    trade_count: int = 0

    # Available vs Captured
    total_mfe_r: float = 0.0       # Total MFE across all trades
    total_realized_r: float = 0.0  # Total realized R
    total_mae_r: float = 0.0       # Total MAE across all trades

    avg_mfe_r: float = 0.0         # Average MFE per trade
    avg_realized_r: float = 0.0    # Average realized R per trade
    avg_mae_r: float = 0.0         # Average MAE per trade

    # Capture efficiency
    capture_ratio: float = 0.0     # Realized / MFE (0-1)
    exit_efficiency: float = 0.0   # How well exits capture available profit
    mfe_mae_ratio: float = 0.0    # MFE / MAE (reward vs risk)

    # Distribution
    capture_distribution: Dict[str, int] = field(default_factory=dict)  # Bucket counts

    def to_dict(self) -> Dict:
        return {
            "trades": self.trade_count,
            "available": {
                "total_mfe_r": round(self.total_mfe_r, 3),
                "avg_mfe_r": round(self.avg_mfe_r, 3),
            },
            "captured": {
                "total_realized_r": round(self.total_realized_r, 3),
                "avg_realized_r": round(self.avg_realized_r, 3),
            },
            "adverse": {
                "total_mae_r": round(self.total_mae_r, 3),
                "avg_mae_r": round(self.avg_mae_r, 3),
            },
            "efficiency": {
                "capture_ratio": round(self.capture_ratio, 3),
                "exit_efficiency": round(self.exit_efficiency, 1),
                "mfe_mae_ratio": round(self.mfe_mae_ratio, 3),
            },
            "distribution": self.capture_distribution,
        }


@dataclass
class ProfitCaptureDashboard:
    """Complete profit capture analysis."""
    timestamp: float = 0.0

    # Overall metrics
    overall: CaptureMetrics = field(default_factory=CaptureMetrics)

    # By exit reason
    by_exit_reason: Dict[str, CaptureMetrics] = field(default_factory=dict)

    # By symbol (top/bottom)
    by_symbol: Dict[str, CaptureMetrics] = field(default_factory=dict)
    top_symbols: List[Dict] = field(default_factory=list)
    worst_symbols: List[Dict] = field(default_factory=list)

    # Diagnosis
    quality_score: float = 0.0  # 0-100
    diagnosis: str = ""
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "overall": self.overall.to_dict(),
            "by_exit_reason": {k: v.to_dict() for k, v in self.by_exit_reason.items()},
            "top_symbols": self.top_symbols,
            "worst_symbols": self.worst_symbols,
            "quality_score": round(self.quality_score, 1),
            "diagnosis": self.diagnosis,
            "recommendations": self.recommendations,
        }


class ProfitCaptureDashboardEngine:
    """
    Measures profit capture efficiency.

    Per Executive Assessment v16:
        "This isolates whether the system is finding poor trades
         or managing good trades poorly."

    This engine:
        1. Calculates MFE, realized R, MAE for every trade
        2. Measures capture ratio (realized / MFE)
        3. Groups by exit reason and symbol
        4. Identifies profit leaks
        5. Recommends optimization targets

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
                SELECT symbol, side, realized_r, mfe_pct, mae_pct,
                       highest_pnl, exit_reason
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load profit capture dashboard: {}", e)

    def analyze(self) -> ProfitCaptureDashboard:
        """
        Analyze profit capture and generate dashboard.

        Returns:
            ProfitCaptureDashboard with complete analysis
        """
        self._ensure_loaded()

        dashboard = ProfitCaptureDashboard(timestamp=time.time())

        if not self._trades:
            return dashboard

        # ── Overall metrics ──
        dashboard.overall = self._calc_capture_metrics(self._trades)

        # ── By exit reason ──
        by_reason: Dict[str, List[Dict]] = defaultdict(list)
        for t in self._trades:
            by_reason[t.get("exit_reason", "unknown")].append(t)

        for reason, trades in by_reason.items():
            dashboard.by_exit_reason[reason] = self._calc_capture_metrics(trades)

        # ── By symbol ──
        by_symbol: Dict[str, List[Dict]] = defaultdict(list)
        for t in self._trades:
            by_symbol[t.get("symbol", "")].append(t)

        for symbol, trades in by_symbol.items():
            if len(trades) >= 3:
                dashboard.by_symbol[symbol] = self._calc_capture_metrics(trades)

        # Top/bottom symbols
        symbol_list = [
            {"symbol": s, "capture_ratio": m.capture_ratio, "trades": m.trade_count}
            for s, m in dashboard.by_symbol.items()
        ]
        dashboard.top_symbols = sorted(symbol_list, key=lambda x: x["capture_ratio"], reverse=True)[:5]
        dashboard.worst_symbols = sorted(symbol_list, key=lambda x: x["capture_ratio"])[:5]

        # ── Quality score ──
        dashboard.quality_score = max(0, min(100, dashboard.overall.capture_ratio * 100))

        # ── Diagnosis ──
        if dashboard.overall.capture_ratio > 0.6:
            dashboard.diagnosis = "Profit capture is good — system retains most available profit"
        elif dashboard.overall.capture_ratio > 0.4:
            dashboard.diagnosis = "Profit capture is moderate — some profit is being left on the table"
        elif dashboard.overall.capture_ratio > 0.2:
            dashboard.diagnosis = "Profit capture is poor — significant profit leaks"
        else:
            dashboard.diagnosis = "Profit capture is very poor — major optimization needed"

        # ── Recommendations ──
        dashboard.recommendations = self._recommend(dashboard)

        return dashboard

    def _calc_capture_metrics(self, trades: List[Dict]) -> CaptureMetrics:
        """Calculate capture metrics for a set of trades."""
        metrics = CaptureMetrics(trade_count=len(trades))

        if not trades:
            return metrics

        mfe_vals = []
        realized_vals = []
        mae_vals = []

        for t in trades:
            mfe = t.get("highest_pnl", 0) or 0
            r = t.get("realized_r", 0) or 0
            mae = abs(t.get("mae_pct", 0) or 0)

            if mfe > 0:
                mfe_vals.append(mfe)
                metrics.total_mfe_r += mfe

            realized_vals.append(r)
            metrics.total_realized_r += r

            if mae > 0:
                mae_vals.append(mae)
                metrics.total_mae_r += mae

        # Averages
        metrics.avg_mfe_r = metrics.total_mfe_r / max(1, len(mfe_vals))
        metrics.avg_realized_r = metrics.total_realized_r / max(1, len(trades))
        metrics.avg_mae_r = metrics.total_mae_r / max(1, len(mae_vals))

        # Capture ratio
        if metrics.total_mfe_r > 0:
            metrics.capture_ratio = metrics.total_realized_r / metrics.total_mfe_r

        # Exit efficiency (0-100)
        metrics.exit_efficiency = max(0, min(100, metrics.capture_ratio * 100))

        # MFE/MAE ratio
        if metrics.avg_mae_r > 0:
            metrics.mfe_mae_ratio = metrics.avg_mfe_r / metrics.avg_mae_r

        # Capture distribution
        for t in trades:
            mfe = t.get("highest_pnl", 0) or 0
            r = t.get("realized_r", 0) or 0
            if mfe > 0:
                capture = (r / mfe) * 100
                if capture > 70:
                    bucket = "excellent_70plus"
                elif capture > 50:
                    bucket = "good_50_70"
                elif capture > 30:
                    bucket = "fair_30_50"
                else:
                    bucket = "poor_below_30"
                metrics.capture_distribution[bucket] = metrics.capture_distribution.get(bucket, 0) + 1

        return metrics

    def _recommend(self, dashboard: ProfitCaptureDashboard) -> List[str]:
        """Generate recommendations."""
        recs = []

        if dashboard.overall.capture_ratio < 0.3:
            recs.append(
                f"Profit capture is critically low ({dashboard.overall.capture_ratio:.1%}) — "
                f"trades are leaving {100 - dashboard.overall.capture_ratio * 100:.0f}% of available profit"
            )

        # Find worst exit reasons
        worst_reasons = sorted(
            [(k, v) for k, v in dashboard.by_exit_reason.items() if v.trade_count >= 5],
            key=lambda x: x[1].capture_ratio,
        )[:2]

        for reason, metrics in worst_reasons:
            if metrics.capture_ratio < 0.3:
                recs.append(
                    f"{reason}: Low capture ({metrics.capture_ratio:.1%}) — "
                    f"consider adjusting exit timing"
                )

        if not recs:
            recs.append("Profit capture is acceptable — continue monitoring")

        return recs
