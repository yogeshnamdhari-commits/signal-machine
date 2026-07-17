"""
EMA_V5 Daily Report — Day-level performance report.
Reads from analytics layer. Pure computation.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

from ..storage.database import EMAv5Database
from ..analytics.performance_calculator import PerformanceCalculator
from ..analytics.risk_metrics import RiskMetrics
from ..analytics.trade_analyzer import TradeAnalyzer


class DailyReport:
    """Generates daily performance reports for EMA_V5."""

    def __init__(self, db: Optional[EMAv5Database] = None) -> None:
        self._db = db or EMAv5Database()
        self._perf = PerformanceCalculator(self._db)
        self._risk = RiskMetrics(self._db)
        self._trades = TradeAnalyzer(self._db)

    def generate(self, date: Optional[str] = None) -> Dict[str, Any]:
        """Generate report for a specific date (YYYY-MM-DD). Defaults to today."""
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        signals = self._db.get_all_signals()
        day_signals = [s for s in signals if s.get("date") == date]
        closed = [s for s in day_signals if s.get("result") in ("win", "loss")]

        # Core metrics
        perf = self._perf.compute_all(day_signals) if day_signals else self._perf._empty_metrics()

        # Risk (on day's trades only)
        risk = self._risk.compute_all(day_signals) if day_signals else self._risk._empty_risk()

        # Trade analysis
        trade_data = self._trades.analyze_all(day_signals) if day_signals else {"trades": [], "patterns": {}, "quality": {}}

        # Side breakdown
        longs = [s for s in closed if s.get("side") == "LONG"]
        shorts = [s for s in closed if s.get("side") == "SHORT"]

        # Hourly distribution
        hourly = self._hourly_distribution(closed)

        # Regime breakdown
        regimes = self._regime_breakdown(closed)

        # Best/worst trades
        best = max(closed, key=lambda s: s.get("pnl", 0)) if closed else None
        worst = min(closed, key=lambda s: s.get("pnl", 0)) if closed else None

        return {
            "report_type": "daily",
            "date": date,
            "generated_at": time.time(),
            "generated_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "summary": {
                "total_signals": len(day_signals),
                "total_trades": len(closed),
                "wins": perf.get("wins", 0),
                "losses": perf.get("losses", 0),
                "win_rate": perf.get("win_rate", 0),
                "total_pnl": perf.get("total_pnl", 0),
                "avg_pnl": perf.get("avg_pnl", 0),
                "profit_factor": perf.get("profit_factor", 0),
                "expectancy": perf.get("expectancy", 0),
            },
            "sides": {
                "long_trades": len(longs),
                "short_trades": len(shorts),
                "long_pnl": round(sum(s.get("pnl", 0) for s in longs), 4),
                "short_pnl": round(sum(s.get("pnl", 0) for s in shorts), 4),
                "long_win_rate": round(sum(1 for s in longs if s.get("result") == "win") / len(longs) * 100, 1) if longs else 0,
                "short_win_rate": round(sum(1 for s in shorts if s.get("result") == "win") / len(shorts) * 100, 1) if shorts else 0,
            },
            "hourly": hourly,
            "regimes": regimes,
            "risk": {
                "max_drawdown_pct": risk.get("max_drawdown_pct", 0),
                "volatility": risk.get("volatility", 0),
            },
            "quality": trade_data.get("quality", {}),
            "best_trade": self._trade_summary(best) if best else None,
            "worst_trade": self._trade_summary(worst) if worst else None,
            "trades": [self._trade_summary(t) for t in closed],
        }

    def generate_comparison(self, today: Optional[str] = None,
                           yesterday: Optional[str] = None) -> Dict[str, Any]:
        """Compare today vs yesterday."""
        if today is None:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if yesterday is None:
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

        today_report = self.generate(today)
        yesterday_report = self.generate(yesterday)

        today_s = today_report.get("summary", {})
        yesterday_s = yesterday_report.get("summary", {})

        return {
            "report_type": "daily_comparison",
            "today": today,
            "yesterday": yesterday,
            "today_summary": today_s,
            "yesterday_summary": yesterday_s,
            "changes": {
                "pnl_change": round(today_s.get("total_pnl", 0) - yesterday_s.get("total_pnl", 0), 4),
                "wr_change": round(today_s.get("win_rate", 0) - yesterday_s.get("win_rate", 0), 1),
                "trades_change": today_s.get("total_trades", 0) - yesterday_s.get("total_trades", 0),
            },
        }

    def _hourly_distribution(self, trades: List[Dict]) -> Dict[int, Dict]:
        """Break down trades by hour of day."""
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
                "wins": wins,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "pnl": round(sum(t.get("pnl", 0) for t in trades_h), 4),
            }
        return result

    def _regime_breakdown(self, trades: List[Dict]) -> Dict[str, Dict]:
        """Break down by regime."""
        regimes: Dict[str, List] = {}
        for t in trades:
            regime = t.get("regime", "unknown")
            regimes.setdefault(regime, []).append(t)

        result = {}
        for regime, trades_r in regimes.items():
            wins = sum(1 for t in trades_r if t.get("result") == "win")
            total = len(trades_r)
            result[regime] = {
                "trades": total,
                "wins": wins,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "pnl": round(sum(t.get("pnl", 0) for t in trades_r), 4),
            }
        return result

    def _trade_summary(self, trade: Dict) -> Dict[str, Any]:
        """Compact trade summary."""
        return {
            "symbol": trade.get("symbol", ""),
            "side": trade.get("side", ""),
            "entry": trade.get("entry", 0),
            "pnl": trade.get("pnl", 0),
            "result": trade.get("result", ""),
            "confidence": trade.get("confidence", 0),
            "regime": trade.get("regime", ""),
            "hold_time": trade.get("hold_time", 0),
        }

    def list_available_dates(self, days: int = 30) -> List[str]:
        """List dates with trade data."""
        signals = self._db.get_all_signals()
        dates = sorted(set(s.get("date", "") for s in signals if s.get("date")), reverse=True)
        return dates[:days]
