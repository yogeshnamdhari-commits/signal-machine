"""
EMA_V5 Performance Calculator — Core metrics from stored signals.
Reads from database only. Pure computation, no side effects.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from ..storage.database import EMAv5Database


class PerformanceCalculator:
    """Computes core performance metrics from EMA_V5 signal history."""

    def __init__(self, db: Optional[EMAv5Database] = None) -> None:
        self._db = db or EMAv5Database()

    def compute_all(self, signals: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Compute complete performance report. Returns flat metrics dict."""
        if signals is None:
            signals = self._db.get_all_signals()

        if not signals:
            return self._empty_metrics()

        closed = [s for s in signals if s.get("result") in ("win", "loss")]
        wins = [s for s in closed if s.get("result") == "win"]
        losses = [s for s in closed if s.get("result") == "loss"]

        total = len(closed)
        n_wins = len(wins)
        n_losses = len(losses)

        # PnL
        total_pnl = sum(s.get("pnl", 0) for s in closed)
        gross_wins = sum(s.get("pnl", 0) for s in wins)
        gross_losses = abs(sum(s.get("pnl", 0) for s in losses))
        avg_win = gross_wins / n_wins if n_wins > 0 else 0
        avg_loss = gross_losses / n_losses if n_losses > 0 else 0

        # Win rate
        win_rate = (n_wins / total * 100) if total > 0 else 0

        # Profit factor
        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else (
            99.99 if gross_wins > 0 else 0
        )

        # Expectancy (R-multiple based)
        avg_rr = sum(s.get("rr_1", 0) for s in closed) / total if total > 0 else 0
        expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss) if total > 0 else 0
        expectancy_r = expectancy / avg_loss if avg_loss > 0 else 0

        # Max consecutive
        max_consec_wins = self._max_consecutive(closed, "win")
        max_consec_losses = self._max_consecutive(closed, "loss")

        # Confidence stats
        confs = [s.get("confidence", 0) for s in signals if s.get("confidence")]
        avg_confidence = (sum(confs) / len(confs) * 100) if confs else 0

        # Hold time
        hold_times = [s.get("hold_time", 0) for s in closed if s.get("hold_time")]
        avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0

        # Side breakdown
        longs = [s for s in closed if s.get("side") == "LONG"]
        shorts = [s for s in closed if s.get("side") == "SHORT"]
        long_pnl = sum(s.get("pnl", 0) for s in longs)
        short_pnl = sum(s.get("pnl", 0) for s in shorts)
        long_wr = (sum(1 for s in longs if s.get("result") == "win") / len(longs) * 100) if longs else 0
        short_wr = (sum(1 for s in shorts if s.get("result") == "win") / len(shorts) * 100) if shorts else 0

        return {
            "total_signals": len(signals),
            "total_trades": total,
            "wins": n_wins,
            "losses": n_losses,
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 4),
            "gross_profit": round(gross_wins, 4),
            "gross_loss": round(gross_losses, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "avg_pnl": round(total_pnl / total, 4) if total > 0 else 0,
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 4),
            "expectancy_r": round(expectancy_r, 2),
            "avg_rr": round(avg_rr, 2),
            "max_consec_wins": max_consec_wins,
            "max_consec_losses": max_consec_losses,
            "avg_confidence": round(avg_confidence, 1),
            "avg_hold_minutes": round(avg_hold, 1),
            "long_trades": len(longs),
            "short_trades": len(shorts),
            "long_pnl": round(long_pnl, 4),
            "short_pnl": round(short_pnl, 4),
            "long_win_rate": round(long_wr, 1),
            "short_win_rate": round(short_wr, 1),
        }

    def compute_daily(self, signals: Optional[List[Dict]] = None) -> List[Dict[str, Any]]:
        """Compute daily performance breakdown."""
        if signals is None:
            signals = self._db.get_all_signals()

        closed = [s for s in signals if s.get("result") in ("win", "loss")]
        daily: Dict[str, List] = {}
        for s in closed:
            day = s.get("date", "")
            if day:
                daily.setdefault(day, []).append(s)

        result = []
        for day in sorted(daily.keys()):
            trades = daily[day]
            wins = sum(1 for t in trades if t.get("result") == "win")
            pnl = sum(t.get("pnl", 0) for t in trades)
            result.append({
                "date": day,
                "trades": len(trades),
                "wins": wins,
                "losses": len(trades) - wins,
                "win_rate": round(wins / len(trades) * 100, 1) if trades else 0,
                "pnl": round(pnl, 4),
            })
        return result

    def _max_consecutive(self, trades: List[Dict], result: str) -> int:
        """Count max consecutive wins or losses."""
        max_c = 0
        current = 0
        for t in trades:
            if t.get("result") == result:
                current += 1
                max_c = max(max_c, current)
            else:
                current = 0
        return max_c

    def _empty_metrics(self) -> Dict[str, Any]:
        """Return zeroed metrics for empty dataset."""
        return {
            "total_signals": 0, "total_trades": 0, "wins": 0, "losses": 0,
            "win_rate": 0, "total_pnl": 0, "gross_profit": 0, "gross_loss": 0,
            "avg_win": 0, "avg_loss": 0, "avg_pnl": 0, "profit_factor": 0,
            "expectancy": 0, "expectancy_r": 0, "avg_rr": 0,
            "max_consec_wins": 0, "max_consec_losses": 0,
            "avg_confidence": 0, "avg_hold_minutes": 0,
            "long_trades": 0, "short_trades": 0,
            "long_pnl": 0, "short_pnl": 0,
            "long_win_rate": 0, "short_win_rate": 0,
        }
