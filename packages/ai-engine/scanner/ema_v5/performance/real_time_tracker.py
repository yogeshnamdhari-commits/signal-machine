"""
EMA_V5 Real-Time Tracker — Live performance metrics updated on every trade.
Tracks rolling windows, streaks, and current state.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class TradeRecord:
    """Single trade record for real-time tracking."""
    symbol: str = ""
    side: str = ""
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl: float = 0.0
    result: str = ""  # win, loss
    timestamp: float = 0.0
    hold_minutes: float = 0.0
    regime: str = ""
    confidence: float = 0.0


class EMAv5RealTimeTracker:
    """Tracks EMA_V5 performance in real-time with rolling windows."""

    def __init__(self, window_sizes: Optional[List[int]] = None) -> None:
        self._trades: deque = deque(maxlen=10000)
        self._window_sizes = window_sizes or [10, 25, 50, 100]
        self._start_time = time.time()
        self._peak_pnl = 0.0
        self._current_pnl = 0.0
        self._daily_pnl: Dict[str, float] = {}
        self._streak_type = ""
        self._streak_count = 0
        self._max_win_streak = 0
        self._max_loss_streak = 0

    def record_trade(self, trade: TradeRecord) -> None:
        """Record a completed trade."""
        self._trades.append(trade)
        self._current_pnl += trade.pnl
        self._peak_pnl = max(self._peak_pnl, self._current_pnl)

        # Daily tracking
        day = time.strftime("%Y-%m-%d", time.gmtime(trade.timestamp))
        self._daily_pnl[day] = self._daily_pnl.get(day, 0) + trade.pnl

        # Streak tracking
        if trade.result == self._streak_type:
            self._streak_count += 1
        else:
            self._streak_type = trade.result
            self._streak_count = 1

        if trade.result == "win":
            self._max_win_streak = max(self._max_win_streak, self._streak_count)
        else:
            self._max_loss_streak = max(self._max_loss_streak, self._streak_count)

    def get_current_metrics(self) -> Dict[str, Any]:
        """Get current real-time metrics."""
        trades = list(self._trades)
        total = len(trades)

        if total == 0:
            return self._empty_metrics()

        wins = sum(1 for t in trades if t.result == "win")
        losses = sum(1 for t in trades if t.result == "loss")
        total_pnl = sum(t.pnl for t in trades)
        gross_wins = sum(t.pnl for t in trades if t.result == "win")
        gross_losses = abs(sum(t.pnl for t in trades if t.result == "loss"))

        # Rolling windows
        rolling = {}
        for window in self._window_sizes:
            recent = trades[-window:]
            if recent:
                r_wins = sum(1 for t in recent if t.result == "win")
                r_pnl = sum(t.pnl for t in recent)
                rolling[f"last_{window}"] = {
                    "trades": len(recent),
                    "win_rate": round(r_wins / len(recent) * 100, 1),
                    "pnl": round(r_pnl, 4),
                }

        # Current streak
        streak = {
            "type": self._streak_type,
            "count": self._streak_count,
            "max_win": self._max_win_streak,
            "max_loss": self._max_loss_streak,
        }

        # Drawdown
        drawdown = self._peak_pnl - self._current_pnl if self._peak_pnl > 0 else 0

        # Today's performance
        today = time.strftime("%Y-%m-%d", time.gmtime())
        today_pnl = self._daily_pnl.get(today, 0)
        today_trades = [t for t in trades if time.strftime("%Y-%m-%d", time.gmtime(t.timestamp)) == today]
        today_wins = sum(1 for t in today_trades if t.result == "win")

        # Uptime
        uptime_hours = (time.time() - self._start_time) / 3600

        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total * 100, 1),
            "total_pnl": round(total_pnl, 4),
            "avg_pnl": round(total_pnl / total, 4),
            "profit_factor": round(gross_wins / gross_losses, 2) if gross_losses > 0 else 99.99,
            "peak_pnl": round(self._peak_pnl, 4),
            "current_pnl": round(self._current_pnl, 4),
            "drawdown": round(drawdown, 4),
            "streak": streak,
            "rolling": rolling,
            "today": {
                "trades": len(today_trades),
                "wins": today_wins,
                "win_rate": round(today_wins / max(len(today_trades), 1) * 100, 1),
                "pnl": round(today_pnl, 4),
            },
            "uptime_hours": round(uptime_hours, 1),
            "trades_per_hour": round(total / max(uptime_hours, 0.1), 1),
        }

    def get_rolling_window(self, window: int = 50) -> Dict[str, Any]:
        """Get metrics for a specific rolling window."""
        trades = list(self._trades)
        recent = trades[-window:] if len(trades) >= window else trades

        if not recent:
            return {"window": window, "trades": 0, "win_rate": 0, "pnl": 0}

        wins = sum(1 for t in recent if t.result == "win")
        pnl = sum(t.pnl for t in recent)

        return {
            "window": window,
            "trades": len(recent),
            "wins": wins,
            "losses": len(recent) - wins,
            "win_rate": round(wins / len(recent) * 100, 1),
            "pnl": round(pnl, 4),
            "avg_pnl": round(pnl / len(recent), 4),
        }

    def get_daily_pnl(self, days: int = 30) -> Dict[str, float]:
        """Get daily PnL for last N days."""
        sorted_days = sorted(self._daily_pnl.keys(), reverse=True)
        return {d: round(self._daily_pnl[d], 4) for d in sorted_days[:days]}

    def _empty_metrics(self) -> Dict[str, Any]:
        """Return zeroed metrics."""
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "total_pnl": 0, "avg_pnl": 0, "profit_factor": 0,
            "peak_pnl": 0, "current_pnl": 0, "drawdown": 0,
            "streak": {"type": "", "count": 0, "max_win": 0, "max_loss": 0},
            "rolling": {}, "today": {"trades": 0, "wins": 0, "win_rate": 0, "pnl": 0},
            "uptime_hours": 0, "trades_per_hour": 0,
        }

    def reset(self) -> None:
        """Reset all tracking state."""
        self._trades.clear()
        self._start_time = time.time()
        self._peak_pnl = 0.0
        self._current_pnl = 0.0
        self._daily_pnl.clear()
        self._streak_type = ""
        self._streak_count = 0
        self._max_win_streak = 0
        self._max_loss_streak = 0
