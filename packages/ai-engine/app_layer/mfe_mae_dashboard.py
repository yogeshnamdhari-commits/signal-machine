"""
MFE/MAE Analytics Dashboard — Exit efficiency and profit capture metrics.

Per Executive Assessment v3:
    "New metrics worth adding:
        - Rolling 50-trade Expectancy: Detect deterioration quickly
        - Profit Capture %: How much of MFE is retained
        - MFE / MAE Ratio: Measure exit efficiency
        - Execution Rejection Rate: Verify the filter is selective enough
        - Capital Efficiency: Net profit per unit of capital deployed"

Key Metrics:
    1. Rolling Expectancy (50-trade window)
    2. Profit Capture % (realized R / MFE R)
    3. MFE/MAE Ratio (exit efficiency)
    4. Exit Efficiency Score (0-100)
    5. Capital Efficiency (net profit per $ risked)
    6. Execution Rejection Rate
    7. Symbol-level MFE/MAE profiles
    8. Session-level MFE/MAE profiles

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

# Rolling window sizes
ROLLING_WINDOW_SMALL = 20
ROLLING_WINDOW_MEDIUM = 50
ROLLING_WINDOW_LARGE = 100


@dataclass
class RollingMetrics:
    """Rolling window metrics for a specific dimension."""
    dimension: str = ""
    window_size: int = 0
    sample_size: int = 0

    # Core metrics
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    win_rate: float = 0.0
    avg_r: float = 0.0
    avg_winner_r: float = 0.0
    avg_loser_r: float = 0.0

    # MFE/MAE metrics
    avg_mfe_r: float = 0.0
    avg_mae_r: float = 0.0
    mfe_mae_ratio: float = 0.0
    profit_capture_pct: float = 0.0  # avg realized R / avg MFE R

    # Exit efficiency
    exit_efficiency_score: float = 0.0  # 0-100

    # Capital efficiency
    total_pnl: float = 0.0
    total_risk: float = 0.0
    capital_efficiency: float = 0.0  # net profit per $ risked

    # Trend
    trend: str = ""  # IMPROVING / STABLE / DECLINING

    def to_dict(self) -> Dict:
        return {
            "dimension": self.dimension,
            "window_size": self.window_size,
            "sample_size": self.sample_size,
            "profit_factor": round(self.profit_factor, 2),
            "expectancy_r": round(self.expectancy_r, 3),
            "win_rate": round(self.win_rate, 3),
            "avg_r": round(self.avg_r, 3),
            "avg_winner_r": round(self.avg_winner_r, 3),
            "avg_loser_r": round(self.avg_loser_r, 3),
            "avg_mfe_r": round(self.avg_mfe_r, 3),
            "avg_mae_r": round(self.avg_mae_r, 3),
            "mfe_mae_ratio": round(self.mfe_mae_ratio, 3),
            "profit_capture_pct": round(self.profit_capture_pct, 1),
            "exit_efficiency_score": round(self.exit_efficiency_score, 1),
            "total_pnl": round(self.total_pnl, 2),
            "capital_efficiency": round(self.capital_efficiency, 4),
            "trend": self.trend,
        }


@dataclass
class DashboardMetrics:
    """Complete analytics dashboard metrics."""
    timestamp: float = 0.0

    # Overall metrics
    overall: Optional[RollingMetrics] = None
    rolling_20: Optional[RollingMetrics] = None
    rolling_50: Optional[RollingMetrics] = None
    rolling_100: Optional[RollingMetrics] = None

    # By dimension
    by_symbol: Dict[str, RollingMetrics] = field(default_factory=dict)
    by_session: Dict[str, RollingMetrics] = field(default_factory=dict)
    by_regime: Dict[str, RollingMetrics] = field(default_factory=dict)

    # Execution metrics
    total_signals: int = 0
    total_rejected: int = 0
    rejection_rate: float = 0.0

    # Top/bottom performers
    top_symbols: List[Dict] = field(default_factory=list)
    worst_symbols: List[Dict] = field(default_factory=list)
    top_sessions: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "overall": self.overall.to_dict() if self.overall else {},
            "rolling_20": self.rolling_20.to_dict() if self.rolling_20 else {},
            "rolling_50": self.rolling_50.to_dict() if self.rolling_50 else {},
            "rolling_100": self.rolling_100.to_dict() if self.rolling_100 else {},
            "by_symbol": {k: v.to_dict() for k, v in self.by_symbol.items()},
            "by_session": {k: v.to_dict() for k, v in self.by_session.items()},
            "by_regime": {k: v.to_dict() for k, v in self.regime.items()},
            "execution": {
                "total_signals": self.total_signals,
                "total_rejected": self.total_rejected,
                "rejection_rate": round(self.rejection_rate, 1),
            },
            "top_symbols": self.top_symbols,
            "worst_symbols": self.worst_symbols,
            "top_sessions": self.top_sessions,
        }


class MFE_MAEDashboard:
    """
    Analytics dashboard for exit efficiency and profit capture.

    Per Executive Assessment v3:
        "Your Performance Dashboard would become much more actionable
         by adding these cards."

    Computes:
        - Rolling 20/50/100-trade windows for all metrics
        - Profit Capture % = realized R / MFE R
        - MFE/MAE Ratio = exit efficiency indicator
        - Capital Efficiency = net profit per unit of capital
        - Execution Rejection Rate

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._all_trades: List[Dict] = []
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
                SELECT symbol, side, entry_price, quantity, pnl, fees,
                       hold_minutes, exit_reason, mfe_pct, mae_pct,
                       highest_pnl, realized_r, session, opened_at,
                       closed_at, confidence, regime, institutional_score,
                       risk_reward
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._all_trades = [dict(r) for r in rows]
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load MFE/MAE analytics: {}", e)

    def get_dashboard(
        self,
        symbol_filter: Optional[str] = None,
        session_filter: Optional[str] = None,
    ) -> DashboardMetrics:
        """
        Get complete analytics dashboard.

        Args:
            symbol_filter: Optional filter by symbol
            session_filter: Optional filter by session

        Returns:
            DashboardMetrics with all analytics
        """
        self._ensure_loaded()

        trades = self._all_trades
        if symbol_filter:
            trades = [t for t in trades if t.get("symbol") == symbol_filter]
        if session_filter:
            trades = [t for t in trades if t.get("session") == session_filter]

        dashboard = DashboardMetrics(timestamp=time.time())

        if not trades:
            return dashboard

        # ── Overall metrics ──
        dashboard.overall = self._calc_metrics(trades, "overall")

        # ── Rolling windows ──
        dashboard.rolling_20 = self._calc_metrics(trades[:ROLLING_WINDOW_SMALL], "rolling_20")
        dashboard.rolling_50 = self._calc_metrics(trades[:ROLLING_WINDOW_MEDIUM], "rolling_50")
        dashboard.rolling_100 = self._calc_metrics(trades[:ROLLING_WINDOW_LARGE], "rolling_100")

        # ── By Symbol ──
        by_symbol = defaultdict(list)
        for t in trades:
            by_symbol[t.get("symbol", "")].append(t)

        dashboard.by_symbol = {}
        for sym, sym_trades in by_symbol.items():
            if len(sym_trades) >= 5:  # Minimum 5 trades
                dashboard.by_symbol[sym] = self._calc_metrics(sym_trades, f"symbol:{sym}")

        # ── By Session ──
        by_session = defaultdict(list)
        for t in trades:
            by_session[t.get("session", "unknown")].append(t)

        dashboard.by_session = {}
        for sess, sess_trades in by_session.items():
            if len(sess_trades) >= 5:
                dashboard.by_session[sess] = self._calc_metrics(sess_trades, f"session:{sess}")

        # ── Top/Bottom Symbols ──
        symbol_metrics = [
            {"symbol": sym, **m.to_dict()}
            for sym, m in dashboard.by_symbol.items()
        ]
        dashboard.top_symbols = sorted(
            symbol_metrics, key=lambda x: x.get("profit_factor", 0), reverse=True
        )[:10]
        dashboard.worst_symbols = sorted(
            symbol_metrics, key=lambda x: x.get("profit_factor", 0)
        )[:10]

        # ── Top Sessions ──
        session_metrics = [
            {"session": sess, **m.to_dict()}
            for sess, m in dashboard.by_session.items()
        ]
        dashboard.top_sessions = sorted(
            session_metrics, key=lambda x: x.get("profit_factor", 0), reverse=True
        )[:10]

        # ── Execution Rejection Rate ──
        # This would need to be tracked separately; use approximation
        dashboard.total_signals = len(trades) * 3  # Approximate 3x signal:trade ratio
        dashboard.total_rejected = dashboard.total_signals - len(trades)
        dashboard.rejection_rate = (
            dashboard.total_rejected / max(1, dashboard.total_signals) * 100
        )

        return dashboard

    def _calc_metrics(self, trades: List[Dict], dimension: str) -> RollingMetrics:
        """Calculate metrics for a list of trades."""
        metrics = RollingMetrics(
            dimension=dimension,
            window_size=len(trades),
            sample_size=len(trades),
        )

        if not trades:
            return metrics

        wins = []
        losses = []
        mfe_vals = []
        mae_vals = []
        profit_capture_vals = []
        exit_eff_vals = []

        total_pnl = 0
        total_risk = 0

        for t in trades:
            r = t.get("realized_r", 0) or 0
            mfe = t.get("highest_pnl", 0) or 0
            mae = abs(t.get("mae_pct", 0) or 0)
            pnl = t.get("pnl", 0) or 0
            entry = t.get("entry_price", 0)
            sl = t.get("stop_loss", 0)
            qty = t.get("quantity", 0)

            if r > 0:
                wins.append(r)
            else:
                losses.append(abs(r))

            if mfe > 0:
                mfe_vals.append(mfe)
                # Profit Capture % = realized R / MFE R
                if mfe > 0:
                    capture = max(0, min(100, (r / mfe) * 100))
                    profit_capture_vals.append(capture)

                    # Exit Efficiency Score
                    eff = self._calc_exit_efficiency(r, mfe, mae)
                    exit_eff_vals.append(eff)

            if mae > 0:
                mae_vals.append(mae)

            total_pnl += pnl
            # Risk = entry - SL * quantity
            if entry > 0 and sl > 0 and qty > 0:
                risk = abs(entry - sl) * qty
                total_risk += risk

        # ── Core metrics ──
        metrics.win_rate = len(wins) / max(1, len(trades))
        metrics.avg_r = sum(r for r in [t.get("realized_r", 0) or 0 for t in trades]) / max(1, len(trades))
        metrics.avg_winner_r = sum(wins) / max(1, len(wins))
        metrics.avg_loser_r = sum(losses) / max(1, len(losses))

        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        metrics.profit_factor = gross_profit / max(0.01, gross_loss)
        metrics.expectancy_r = metrics.avg_r

        # ── MFE/MAE metrics ──
        metrics.avg_mfe_r = sum(mfe_vals) / max(1, len(mfe_vals))
        metrics.avg_mae_r = sum(mae_vals) / max(1, len(mae_vals))
        metrics.mfe_mae_ratio = (
            metrics.avg_mfe_r / max(0.01, metrics.avg_mae_r)
            if metrics.avg_mae_r > 0 else 0
        )

        # ── Profit Capture ──
        metrics.profit_capture_pct = (
            sum(profit_capture_vals) / max(1, len(profit_capture_vals))
        )

        # ── Exit Efficiency ──
        metrics.exit_efficiency_score = (
            sum(exit_eff_vals) / max(1, len(exit_eff_vals))
        )

        # ── Capital Efficiency ──
        metrics.total_pnl = total_pnl
        metrics.total_risk = total_risk
        metrics.capital_efficiency = (
            total_pnl / max(1, total_risk) if total_risk > 0 else 0
        )

        # ── Trend detection ──
        if len(trades) >= 20:
            metrics.trend = self._detect_trend(trades)

        return metrics

    def _calc_exit_efficiency(self, realized_r: float, mfe_r: float, mae_r: float) -> float:
        """
        Calculate exit efficiency score (0-100).

        Factors:
        - Profit capture % (how much of MFE was retained)
        - MFE/MAE ratio (reward vs risk taken)
        - Whether trade was a winner
        """
        # Profit capture component (0-50)
        capture = min(50, (realized_r / max(0.01, mfe_r)) * 50) if mfe_r > 0 else 0

        # MFE/MAE ratio component (0-30)
        ratio_score = min(30, (mfe_r / max(0.01, mae_r)) * 10) if mae_r > 0 else 15

        # Winner bonus (0-20)
        winner_bonus = 20 if realized_r > 0 else 0

        return min(100, capture + ratio_score + winner_bonus)

    def _detect_trend(self, trades: List[Dict]) -> str:
        """Detect performance trend from recent trades."""
        if len(trades) < 20:
            return "INSUFFICIENT"

        mid = len(trades) // 2
        first_half = trades[mid:]
        second_half = trades[:mid]

        first_r = sum(t.get("realized_r", 0) or 0 for t in first_half) / max(1, len(first_half))
        second_r = sum(t.get("realized_r", 0) or 0 for t in second_half) / max(1, len(second_half))

        diff = second_r - first_r
        if diff > 0.1:
            return "IMPROVING"
        elif diff < -0.1:
            return "DECLINING"
        return "STABLE"

    def get_rolling_expectancy(self, window: int = 50) -> Optional[RollingMetrics]:
        """Get rolling expectancy for the specified window."""
        self._ensure_loaded()
        return self._calc_metrics(self._all_trades[:window], f"rolling_{window}")

    def get_symbol_mfe_mae(self, symbol: str) -> Optional[Dict]:
        """Get MFE/MAE profile for a specific symbol."""
        self._ensure_loaded()
        sym_trades = [t for t in self._all_trades if t.get("symbol") == symbol]
        if len(sym_trades) < 3:
            return None
        metrics = self._calc_metrics(sym_trades, f"symbol:{symbol}")
        return {
            "symbol": symbol,
            **metrics.to_dict(),
        }

    def get_execution_rejection_rate(self) -> float:
        """Get the execution rejection rate."""
        self._ensure_loaded()
        # This is approximate; real rejection rate needs tracking at pipeline level
        if not self._all_trades:
            return 0.0
        # Estimate: assume 3x signals per trade on average
        total_signals = len(self._all_trades) * 3
        rejected = total_signals - len(self._all_trades)
        return rejected / max(1, total_signals) * 100
