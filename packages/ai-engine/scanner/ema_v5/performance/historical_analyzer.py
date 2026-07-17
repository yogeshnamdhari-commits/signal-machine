"""
EMA_V5 Historical Analyzer — Deep analysis of historical performance.
Reads from database. Focuses on trends and patterns over time.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

from ..storage.database import EMAv5Database


class EMAv5HistoricalAnalyzer:
    """Analyzes historical EMA_V5 performance patterns."""

    def __init__(self, db: Optional[EMAv5Database] = None) -> None:
        self._db = db or EMAv5Database()

    def analyze(self, signals: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Complete historical analysis."""
        if signals is None:
            signals = self._db.get_all_signals()

        closed = [s for s in signals if s.get("result") in ("win", "loss")]
        if not closed:
            return {"error": "No trades to analyze"}

        return {
            "summary": self._summary(closed),
            "trends": self._performance_trends(closed),
            "consistency": self._consistency_metrics(closed),
            "stability": self._stability_analysis(closed),
            "regime_performance": self._regime_performance(closed),
            "symbol_performance": self._symbol_performance(closed),
            "time_patterns": self._time_patterns(closed),
        }

    def _summary(self, trades: List[Dict]) -> Dict[str, Any]:
        """Core summary."""
        total = len(trades)
        wins = sum(1 for t in trades if t.get("result") == "win")
        pnl = sum(t.get("pnl", 0) for t in trades)
        gross_wins = sum(t.get("pnl", 0) for t in trades if t.get("result") == "win")
        gross_losses = abs(sum(t.get("pnl", 0) for t in trades if t.get("result") == "loss"))

        return {
            "total_trades": total,
            "win_rate": round(wins / total * 100, 1),
            "total_pnl": round(pnl, 4),
            "profit_factor": round(gross_wins / gross_losses, 2) if gross_losses > 0 else 99.99,
            "avg_pnl": round(pnl / total, 4),
            "expectancy": round(pnl / total, 4),
        }

    def _performance_trends(self, trades: List[Dict]) -> Dict[str, Any]:
        """Analyze performance trends over time."""
        # Sort by timestamp
        sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", 0))

        # Split into quarters
        n = len(sorted_trades)
        quarter_size = max(n // 4, 1)
        quarters = []
        for i in range(0, n, quarter_size):
            chunk = sorted_trades[i:i + quarter_size]
            if chunk:
                wins = sum(1 for t in chunk if t.get("result") == "win")
                pnl = sum(t.get("pnl", 0) for t in chunk)
                quarters.append({
                    "period": len(quarters) + 1,
                    "trades": len(chunk),
                    "win_rate": round(wins / len(chunk) * 100, 1),
                    "pnl": round(pnl, 4),
                })

        # Trend direction
        if len(quarters) >= 2:
            recent_wr = quarters[-1]["win_rate"]
            earlier_wr = quarters[0]["win_rate"]
            if recent_wr > earlier_wr + 5:
                trend = "improving"
            elif recent_wr < earlier_wr - 5:
                trend = "degrading"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"

        return {
            "quarters": quarters,
            "trend": trend,
        }

    def _consistency_metrics(self, trades: List[Dict]) -> Dict[str, Any]:
        """Measure consistency of performance."""
        pnls = [t.get("pnl", 0) for t in trades]

        # Winning days vs losing days
        daily: Dict[str, List[float]] = {}
        for t in trades:
            day = t.get("date", "")
            if day:
                daily.setdefault(day, []).append(t.get("pnl", 0))

        winning_days = sum(1 for pnls_d in daily.values() if sum(pnls_d) > 0)
        losing_days = sum(1 for pnls_d in daily.values() if sum(pnls_d) <= 0)

        # Consecutive wins/losses
        max_consec_wins = 0
        max_consec_losses = 0
        current = 0
        current_type = ""
        for t in trades:
            result = t.get("result", "")
            if result == current_type:
                current += 1
            else:
                current_type = result
                current = 1
            if result == "win":
                max_consec_wins = max(max_consec_wins, current)
            else:
                max_consec_losses = max(max_consec_losses, current)

        return {
            "winning_days": winning_days,
            "losing_days": losing_days,
            "winning_days_pct": round(winning_days / max(len(daily), 1) * 100, 1),
            "max_consec_wins": max_consec_wins,
            "max_consec_losses": max_consec_losses,
        }

    def _stability_analysis(self, trades: List[Dict]) -> Dict[str, Any]:
        """Analyze performance stability."""
        pnls = [t.get("pnl", 0) for t in trades]
        if len(pnls) < 2:
            return {"stability_score": 0, "volatility": 0}

        mean_pnl = sum(pnls) / len(pnls)
        variance = sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
        std_pnl = variance ** 0.5

        # Coefficient of variation (lower = more stable)
        cv = (std_pnl / abs(mean_pnl)) if mean_pnl != 0 else 999

        # Stability score (0-100, higher = more stable)
        stability_score = max(0, min(100, 100 - cv * 10))

        return {
            "stability_score": round(stability_score, 1),
            "volatility": round(std_pnl, 4),
            "coefficient_of_variation": round(cv, 2),
            "mean_pnl": round(mean_pnl, 4),
        }

    def _regime_performance(self, trades: List[Dict]) -> Dict[str, Any]:
        """Performance by market regime."""
        regimes: Dict[str, List] = {}
        for t in trades:
            regime = t.get("regime", "unknown")
            regimes.setdefault(regime, []).append(t)

        result = {}
        for regime, trades_r in regimes.items():
            wins = sum(1 for t in trades_r if t.get("result") == "win")
            total = len(trades_r)
            pnl = sum(t.get("pnl", 0) for t in trades_r)
            result[regime] = {
                "trades": total,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "pnl": round(pnl, 4),
                "avg_pnl": round(pnl / total, 4) if total > 0 else 0,
            }
        return result

    def _symbol_performance(self, trades: List[Dict]) -> Dict[str, Any]:
        """Performance by symbol."""
        symbols: Dict[str, List] = {}
        for t in trades:
            sym = t.get("symbol", "")
            symbols.setdefault(sym, []).append(t)

        result = {}
        for sym, trades_s in symbols.items():
            wins = sum(1 for t in trades_s if t.get("result") == "win")
            total = len(trades_s)
            pnl = sum(t.get("pnl", 0) for t in trades_s)
            result[sym] = {
                "trades": total,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "pnl": round(pnl, 4),
            }
        return result

    def _time_patterns(self, trades: List[Dict]) -> Dict[str, Any]:
        """Analyze time-based patterns."""
        hourly: Dict[int, List] = {}
        for t in trades:
            ts = t.get("timestamp", 0)
            if ts:
                hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
                hourly.setdefault(hour, []).append(t)

        result = {}
        for hour in sorted(hourly.keys()):
            trades_h = hourly[hour]
            wins = sum(1 for t in trades_h if t.get("result") == "win")
            total = len(trades_h)
            result[hour] = {
                "trades": total,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            }
        return result
