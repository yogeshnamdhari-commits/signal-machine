"""
Rolling Stability Monitor — Track performance stability over time.

Per Executive Assessment v9:
    "Add rolling metrics such as:
         - 50-trade PF
         - 100-trade PF
         - rolling expectancy
         - rolling profit capture

     These reveal degradation much earlier than cumulative statistics."

Key Features:
    1. Rolling Windows — 25/50/100-trade PF, EV, win rate
    2. Stability Score — how consistent is performance
    3. Degradation Detection — early warning of edge decay
    4. Trend Analysis — is performance improving or declining
    5. Alert Thresholds — notify when stability drops

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
class RollingWindow:
    """Metrics for a single rolling window."""
    window_size: int = 0
    trade_count: int = 0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    win_rate: float = 0.0
    avg_r: float = 0.0
    total_pnl: float = 0.0
    max_drawdown_r: float = 0.0
    profit_capture_pct: float = 0.0
    timestamp: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "window_size": self.window_size,
            "trades": self.trade_count,
            "pf": round(self.profit_factor, 2),
            "ev_r": round(self.expectancy_r, 3),
            "win_rate": round(self.win_rate, 3),
            "avg_r": round(self.avg_r, 3),
            "total_pnl": round(self.total_pnl, 2),
            "max_dd_r": round(self.max_drawdown_r, 3),
            "profit_capture": round(self.profit_capture_pct, 1),
        }


@dataclass
class StabilityMetrics:
    """Stability analysis across multiple windows."""
    # Rolling windows
    rolling_25: Optional[RollingWindow] = None
    rolling_50: Optional[RollingWindow] = None
    rolling_100: Optional[RollingWindow] = None

    # Stability scores (0-100)
    pf_stability: float = 0.0     # How stable is PF across windows
    ev_stability: float = 0.0     # How stable is EV across windows
    win_rate_stability: float = 0.0

    # Trend
    pf_trend: str = ""            # IMPROVING / STABLE / DECLINING
    ev_trend: str = ""
    trend_strength: float = 0.0   # -1 to 1

    # Alerts
    alerts: List[str] = field(default_factory=list)
    health_score: float = 0.0     # 0-100 overall health

    def to_dict(self) -> Dict:
        return {
            "rolling_25": self.rolling_25.to_dict() if self.rolling_25 else {},
            "rolling_50": self.rolling_50.to_dict() if self.rolling_50 else {},
            "rolling_100": self.rolling_100.to_dict() if self.rolling_100 else {},
            "stability": {
                "pf": round(self.pf_stability, 1),
                "ev": round(self.ev_stability, 1),
                "win_rate": round(self.win_rate_stability, 1),
            },
            "trend": {
                "pf": self.pf_trend,
                "ev": self.ev_trend,
                "strength": round(self.trend_strength, 3),
            },
            "alerts": self.alerts,
            "health_score": round(self.health_score, 1),
        }


class RollingStabilityMonitor:
    """
    Tracks performance stability over rolling windows.

    Per Executive Assessment v9:
        "These reveal degradation much earlier than cumulative statistics."

    This engine:
        1. Calculates rolling 25/50/100-trade metrics
        2. Measures stability across windows
        3. Detects degradation early
        4. Provides health score (0-100)
        5. Generates alerts for concerning patterns

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
                       exit_reason, closed_at, hold_minutes
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load rolling stability monitor: {}", e)

    def evaluate(self) -> StabilityMetrics:
        """
        Evaluate rolling stability metrics.

        Returns:
            StabilityMetrics with complete analysis
        """
        self._ensure_loaded()

        metrics = StabilityMetrics()

        if not self._trades:
            return metrics

        # ── Rolling windows ──
        metrics.rolling_25 = self._calc_rolling_window(25)
        metrics.rolling_50 = self._calc_rolling_window(50)
        metrics.rolling_100 = self._calc_rolling_window(100)

        # ── Stability scores ──
        metrics.pf_stability = self._calc_pf_stability()
        metrics.ev_stability = self._calc_ev_stability()
        metrics.win_rate_stability = self._calc_win_rate_stability()

        # ── Trend analysis ──
        metrics.pf_trend, metrics.ev_trend, metrics.trend_strength = self._calc_trends()

        # ── Alerts ──
        metrics.alerts = self._generate_alerts(metrics)

        # ── Health score ──
        metrics.health_score = self._calc_health_score(metrics)

        return metrics

    def _calc_rolling_window(self, window_size: int) -> RollingWindow:
        """Calculate metrics for a rolling window."""
        window = RollingWindow(window_size=window_size)

        if len(self._trades) < window_size:
            window.trade_count = len(self._trades)
            trades = self._trades
        else:
            window.trade_count = window_size
            trades = self._trades[:window_size]

        if not trades:
            return window

        wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]

        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        window.profit_factor = gross_profit / max(0.01, gross_loss)

        all_r = [t.get("realized_r", 0) or 0 for t in trades]
        window.avg_r = sum(all_r) / max(1, len(all_r))
        window.expectancy_r = window.avg_r
        window.win_rate = len(wins) / max(1, len(trades))
        window.total_pnl = sum(t.get("pnl", 0) or 0 for t in trades)

        # Max drawdown
        equity = 0
        peak = 0
        max_dd = 0
        for r in all_r:
            equity += r
            if equity > peak:
                peak = equity
            dd = peak - equity
            max_dd = max(max_dd, dd)
        window.max_drawdown_r = max_dd

        # Profit capture
        mfe_vals = [t.get("highest_pnl", 0) or 0 for t in trades if (t.get("highest_pnl", 0) or 0) > 0]
        capture_vals = []
        for t in trades:
            mfe = t.get("highest_pnl", 0) or 0
            r = t.get("realized_r", 0) or 0
            if mfe > 0:
                capture_vals.append((r / mfe) * 100)
        window.profit_capture_pct = sum(capture_vals) / max(1, len(capture_vals))

        window.timestamp = time.time()
        return window

    def _calc_pf_stability(self) -> float:
        """Calculate PF stability across windows."""
        windows = []
        for size in [25, 50, 100]:
            if len(self._trades) >= size:
                w = self._calc_rolling_window(size)
                windows.append(w.profit_factor)

        if len(windows) < 2:
            return 50.0

        mean_pf = sum(windows) / len(windows)
        variance = sum((pf - mean_pf) ** 2 for pf in windows) / len(windows)
        std_pf = math.sqrt(variance)

        # Lower std = more stable = higher score
        return max(0, min(100, 100 - std_pf * 50))

    def _calc_ev_stability(self) -> float:
        """Calculate EV stability across windows."""
        windows = []
        for size in [25, 50, 100]:
            if len(self._trades) >= size:
                w = self._calc_rolling_window(size)
                windows.append(w.expectancy_r)

        if len(windows) < 2:
            return 50.0

        mean_ev = sum(windows) / len(windows)
        variance = sum((ev - mean_ev) ** 2 for ev in windows) / len(windows)
        std_ev = math.sqrt(variance)

        return max(0, min(100, 100 - std_ev * 100))

    def _calc_win_rate_stability(self) -> float:
        """Calculate win rate stability across windows."""
        windows = []
        for size in [25, 50, 100]:
            if len(self._trades) >= size:
                w = self._calc_rolling_window(size)
                windows.append(w.win_rate)

        if len(windows) < 2:
            return 50.0

        mean_wr = sum(windows) / len(windows)
        variance = sum((wr - mean_wr) ** 2 for wr in windows) / len(windows)
        std_wr = math.sqrt(variance)

        return max(0, min(100, 100 - std_wr * 200))

    def _calc_trends(self) -> tuple:
        """Calculate PF and EV trends."""
        if len(self._trades) < 50:
            return "INSUFFICIENT", "INSUFFICIENT", 0.0

        # Split into two halves
        mid = len(self._trades) // 2
        first_half = self._trades[mid:]
        second_half = self._trades[:mid]

        # PF trend
        pf1 = self._calc_window_pf(first_half)
        pf2 = self._calc_window_pf(second_half)

        if pf2 > pf1 + 0.05:
            pf_trend = "IMPROVING"
        elif pf2 < pf1 - 0.05:
            pf_trend = "DECLINING"
        else:
            pf_trend = "STABLE"

        # EV trend
        ev1 = self._calc_window_ev(first_half)
        ev2 = self._calc_window_ev(second_half)

        if ev2 > ev1 + 0.02:
            ev_trend = "IMPROVING"
        elif ev2 < ev1 - 0.02:
            ev_trend = "DECLINING"
        else:
            ev_trend = "STABLE"

        # Trend strength
        if pf1 > 0:
            trend_strength = (pf2 - pf1) / abs(pf1)
        else:
            trend_strength = 0.0

        trend_strength = max(-1.0, min(1.0, trend_strength))

        return pf_trend, ev_trend, trend_strength

    def _calc_window_pf(self, trades: List[Dict]) -> float:
        """Calculate PF for a set of trades."""
        if not trades:
            return 0.0
        wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]
        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        return gross_profit / max(0.01, gross_loss)

    def _calc_window_ev(self, trades: List[Dict]) -> float:
        """Calculate EV for a set of trades."""
        if not trades:
            return 0.0
        all_r = [t.get("realized_r", 0) or 0 for t in trades]
        return sum(all_r) / max(1, len(all_r))

    def _generate_alerts(self, metrics: StabilityMetrics) -> List[str]:
        """Generate alerts for concerning patterns."""
        alerts = []

        # PF declining
        if metrics.pf_trend == "DECLINING":
            alerts.append("PF is declining — potential edge decay")

        # EV negative
        if metrics.rolling_50 and metrics.rolling_50.expectancy_r < 0:
            alerts.append("50-trade EV is negative — system is losing")

        # Low stability
        if metrics.pf_stability < 50:
            alerts.append("PF stability is low — performance is inconsistent")

        # Profit capture declining
        if metrics.rolling_25 and metrics.rolling_50:
            if metrics.rolling_25.profit_capture_pct < metrics.rolling_50.profit_capture_pct - 10:
                alerts.append("Profit capture declining — exits may be worsening")

        # High drawdown
        if metrics.rolling_25 and metrics.rolling_25.max_drawdown_r > 2.0:
            alerts.append("Recent drawdown is high (>2R)")

        return alerts

    def _calc_health_score(self, metrics: StabilityMetrics) -> float:
        """Calculate overall health score (0-100)."""
        score = 50  # Base

        # PF stability contribution
        score += (metrics.pf_stability - 50) * 0.2

        # EV stability contribution
        score += (metrics.ev_stability - 50) * 0.2

        # Trend contribution
        if metrics.pf_trend == "IMPROVING":
            score += 10
        elif metrics.pf_trend == "DECLINING":
            score -= 15

        # Alert penalty
        score -= len(metrics.alerts) * 5

        return max(0, min(100, score))

    def get_summary(self) -> Dict[str, Any]:
        """Get rolling stability summary."""
        self._ensure_loaded()
        return {
            "total_trades": len(self._trades),
            "health_score": self.evaluate().health_score,
        }
