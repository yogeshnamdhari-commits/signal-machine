"""
Rolling Portfolio Health — Real-time portfolio risk and performance metrics.

Per Executive Assessment v4:
    "Portfolio Quality metrics:
        - Capital Utilization
        - Risk Concentration
        - Correlation Score
        - Active Exposure
        - Idle Capital %
     Rolling portfolio health metrics would allow you to distinguish
     a temporary drawdown from a structural deterioration."

Key Metrics:
    1. Rolling 25/50/100-trade PF — detect deterioration quickly
    2. Capital Utilization — how much capital is deployed
    3. Risk Concentration — how concentrated is risk
    4. Active Exposure — net long/short exposure
    5. Idle Capital % — capital not at risk
    6. Portfolio Drawdown — current drawdown from peak
    7. Recovery Factor — net profit / max drawdown
    8. Sharpe-like Ratio — risk-adjusted return

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
class RollingHealthMetrics:
    """Rolling window portfolio health metrics."""
    window_size: int = 0
    sample_size: int = 0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    win_rate: float = 0.0
    avg_r: float = 0.0
    total_pnl: float = 0.0
    max_drawdown_pct: float = 0.0
    recovery_factor: float = 0.0
    sharpe_ratio: float = 0.0
    trend: str = ""  # IMPROVING / STABLE / DECLINING

    def to_dict(self) -> Dict:
        return {
            "window_size": self.window_size,
            "sample_size": self.sample_size,
            "profit_factor": round(self.profit_factor, 2),
            "expectancy_r": round(self.expectancy_r, 3),
            "win_rate": round(self.win_rate, 3),
            "avg_r": round(self.avg_r, 3),
            "total_pnl": round(self.total_pnl, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "recovery_factor": round(self.recovery_factor, 3),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "trend": self.trend,
        }


@dataclass
class PortfolioHealthDashboard:
    """Complete portfolio health dashboard."""
    timestamp: float = 0.0

    # Rolling metrics
    rolling_25: Optional[RollingHealthMetrics] = None
    rolling_50: Optional[RollingHealthMetrics] = None
    rolling_100: Optional[RollingHealthMetrics] = None

    # Current state
    open_positions: int = 0
    total_exposure_pct: float = 0.0
    net_exposure: float = 0.0  # Positive = net long, negative = net short
    idle_capital_pct: float = 0.0
    capital_utilization: float = 0.0
    risk_concentration: float = 0.0  # 0 = diversified, 1 = concentrated

    # Drawdown
    current_drawdown_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    peak_balance: float = 0.0
    current_balance: float = 0.0

    # Health score
    health_score: float = 0.0  # 0-100
    health_status: str = ""    # HEALTHY / CAUTION / STRESSED / CRITICAL

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "rolling_25": self.rolling_25.to_dict() if self.rolling_25 else {},
            "rolling_50": self.rolling_50.to_dict() if self.rolling_50 else {},
            "rolling_100": self.rolling_100.to_dict() if self.rolling_100 else {},
            "current_state": {
                "open_positions": self.open_positions,
                "total_exposure_pct": round(self.total_exposure_pct, 1),
                "net_exposure": round(self.net_exposure, 2),
                "idle_capital_pct": round(self.idle_capital_pct, 1),
                "capital_utilization": round(self.capital_utilization, 1),
                "risk_concentration": round(self.risk_concentration, 3),
            },
            "drawdown": {
                "current_drawdown_pct": round(self.current_drawdown_pct, 2),
                "max_drawdown_pct": round(self.max_drawdown_pct, 2),
                "peak_balance": round(self.peak_balance, 2),
                "current_balance": round(self.current_balance, 2),
            },
            "health": {
                "score": round(self.health_score, 1),
                "status": self.health_status,
            },
        }


class RollingPortfolioHealth:
    """
    Real-time portfolio risk and performance monitoring.

    Per Executive Assessment v4:
        "Rolling portfolio health metrics would allow you to distinguish
         a temporary drawdown from a structural deterioration."

    This engine:
        1. Calculates rolling 25/50/100-trade PF
        2. Tracks capital utilization and exposure
        3. Monitors drawdown and recovery
        4. Provides health score (0-100)
        5. Distinguishes temporary vs structural issues

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
                SELECT symbol, side, realized_r, pnl, closed_at
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load portfolio health: {}", e)

    def get_dashboard(
        self,
        open_positions: Optional[List[Dict]] = None,
        balance: float = 10_000.0,
        peak_balance: float = 10_000.0,
    ) -> PortfolioHealthDashboard:
        """
        Get complete portfolio health dashboard.

        Args:
            open_positions: Current open positions
            balance: Current account balance
            peak_balance: Peak account balance (for drawdown calc)

        Returns:
            PortfolioHealthDashboard with all metrics
        """
        self._ensure_loaded()

        dash = PortfolioHealthDashboard(timestamp=time.time())

        if not self._trades:
            return dash

        # ── Rolling metrics ──
        dash.rolling_25 = self._calc_rolling_health(self._trades[:25], 25)
        dash.rolling_50 = self._calc_rolling_health(self._trades[:50], 50)
        dash.rolling_100 = self._calc_rolling_health(self._trades[:100], 100)

        # ── Current state ──
        positions = open_positions or []
        dash.open_positions = len(positions)

        # Capital utilization
        total_margin = sum(p.get("margin_required", 0) or 0 for p in positions)
        dash.capital_utilization = (total_margin / max(1, balance)) * 100
        dash.idle_capital_pct = max(0, 100 - dash.capital_utilization)

        # Exposure
        long_count = sum(1 for p in positions if p.get("side") == "LONG")
        short_count = sum(1 for p in positions if p.get("side") == "SHORT")
        dash.net_exposure = long_count - short_count
        dash.total_exposure_pct = (len(positions) / 5) * 100  # Max 5 positions

        # Risk concentration (Herfindahl-like)
        if positions:
            total_value = sum(abs(p.get("position_value", 0) or 0) for p in positions)
            if total_value > 0:
                hhi = sum(
                    ((abs(p.get("position_value", 0) or 0) / total_value) ** 2)
                    for p in positions
                )
                dash.risk_concentration = hhi
            else:
                dash.risk_concentration = 0
        else:
            dash.risk_concentration = 0

        # ── Drawdown ──
        dash.peak_balance = peak_balance
        dash.current_balance = balance
        dash.current_drawdown_pct = (
            ((peak_balance - balance) / peak_balance * 100)
            if peak_balance > 0 else 0
        )

        # Max drawdown from trade history
        dash.max_drawdown_pct = self._calc_max_drawdown()

        # ── Health Score ──
        dash.health_score = self._calc_health_score(dash)
        dash.health_status = self._get_health_status(dash.health_score)

        return dash

    def _calc_rolling_health(self, trades: List[Dict], window: int) -> RollingHealthMetrics:
        """Calculate rolling health metrics for a window."""
        metrics = RollingHealthMetrics(window_size=window, sample_size=len(trades))

        if not trades:
            return metrics

        wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]

        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        metrics.profit_factor = gross_profit / max(0.01, gross_loss)

        all_r = [t.get("realized_r", 0) or 0 for t in trades]
        metrics.avg_r = sum(all_r) / max(1, len(all_r))
        metrics.expectancy_r = metrics.avg_r
        metrics.win_rate = len(wins) / max(1, len(trades))
        metrics.total_pnl = sum(t.get("pnl", 0) or 0 for t in trades)

        # Max drawdown
        equity_curve = []
        running = 0
        for r in reversed(all_r):
            running += r
            equity_curve.append(running)

        if equity_curve:
            peak = equity_curve[0]
            max_dd = 0
            for val in equity_curve:
                if val > peak:
                    peak = val
                dd = (peak - val) / max(0.01, abs(peak)) * 100 if peak > 0 else 0
                max_dd = max(max_dd, dd)
            metrics.max_drawdown_pct = max_dd

        # Recovery factor
        if metrics.max_drawdown_pct > 0:
            metrics.recovery_factor = metrics.total_pnl / max(0.01, metrics.max_drawdown_pct)

        # Sharpe-like ratio (simplified)
        if len(all_r) > 1:
            mean_r = sum(all_r) / len(all_r)
            std_r = math.sqrt(sum((r - mean_r) ** 2 for r in all_r) / len(all_r))
            metrics.sharpe_ratio = mean_r / max(0.001, std_r)

        # Trend
        if len(trades) >= 10:
            mid = len(trades) // 2
            first_half = trades[mid:]
            second_half = trades[:mid]
            first_r = sum(t.get("realized_r", 0) or 0 for t in first_half) / max(1, len(first_half))
            second_r = sum(t.get("realized_r", 0) or 0 for t in second_half) / max(1, len(second_half))
            if second_r > first_r + 0.1:
                metrics.trend = "IMPROVING"
            elif second_r < first_r - 0.1:
                metrics.trend = "DECLINING"
            else:
                metrics.trend = "STABLE"

        return metrics

    def _calc_max_drawdown(self) -> float:
        """Calculate maximum drawdown from trade history."""
        if not self._trades:
            return 0

        # Build equity curve from recent trades
        equity = 0
        equity_curve = [0]
        for t in self._trades[:200]:  # Last 200 trades
            equity += t.get("pnl", 0) or 0
            equity_curve.append(equity)

        if not equity_curve:
            return 0

        peak = equity_curve[0]
        max_dd = 0
        for val in equity_curve:
            if val > peak:
                peak = val
            dd = (peak - val) / max(0.01, abs(peak)) * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

        return max_dd

    def _calc_health_score(self, dash: PortfolioHealthDashboard) -> float:
        """Calculate overall portfolio health score (0-100)."""
        score = 50  # Base

        # Rolling PF contribution (0-30 points)
        if dash.rolling_50:
            pf = dash.rolling_50.profit_factor
            if pf >= 1.3:
                score += 30
            elif pf >= 1.1:
                score += 20
            elif pf >= 1.0:
                score += 10
            elif pf >= 0.8:
                score -= 10
            else:
                score -= 20

        # Drawdown contribution (-20 to +10)
        dd = dash.current_drawdown_pct
        if dd < 1:
            score += 10
        elif dd < 3:
            score += 0
        elif dd < 5:
            score -= 10
        else:
            score -= 20

        # Trend contribution (-10 to +10)
        if dash.rolling_50:
            if dash.rolling_50.trend == "IMPROVING":
                score += 10
            elif dash.rolling_50.trend == "DECLINING":
                score -= 10

        # Concentration penalty (-10 to 0)
        if dash.risk_concentration > 0.5:
            score -= 10
        elif dash.risk_concentration > 0.3:
            score -= 5

        return max(0, min(100, score))

    def _get_health_status(self, score: float) -> str:
        """Get health status from score."""
        if score >= 70:
            return "HEALTHY"
        elif score >= 50:
            return "CAUTION"
        elif score >= 30:
            return "STRESSED"
        return "CRITICAL"

    def get_summary(self) -> Dict[str, Any]:
        """Get portfolio health summary."""
        self._ensure_loaded()
        return {
            "total_trades": len(self._trades),
            "max_drawdown_pct": self._calc_max_drawdown(),
        }
