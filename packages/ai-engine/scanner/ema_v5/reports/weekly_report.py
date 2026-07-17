"""
EMA_V5 Weekly Report — Week-level performance report.
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
from ..analytics.equity_curve import EquityCurve


class WeeklyReport:
    """Generates weekly performance reports for EMA_V5."""

    def __init__(self, db: Optional[EMAv5Database] = None) -> None:
        self._db = db or EMAv5Database()
        self._perf = PerformanceCalculator(self._db)
        self._risk = RiskMetrics(self._db)
        self._trades = TradeAnalyzer(self._db)
        self._equity = EquityCurve(self._db)

    def generate(self, week_offset: int = 0) -> Dict[str, Any]:
        """Generate report for a specific week. week_offset=0 is current week."""
        now = datetime.now(timezone.utc)
        # Find Monday of current week
        monday = now - timedelta(days=now.weekday())
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        # Apply offset
        monday += timedelta(weeks=week_offset)
        sunday = monday + timedelta(days=6, hours=23, minutes=59, seconds=59)

        return self._generate_for_range(monday, sunday)

    def generate_for_dates(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """Generate report for a specific date range."""
        start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        end = datetime.strptime(end_date, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
        return self._generate_for_range(start, end)

    def _generate_for_range(self, start: datetime, end: datetime) -> Dict[str, Any]:
        """Generate report for a datetime range."""
        signals = self._db.get_all_signals()
        start_ts = start.timestamp()
        end_ts = end.timestamp()

        week_signals = [
            s for s in signals
            if start_ts <= (s.get("timestamp", 0) or 0) <= end_ts
        ]
        closed = [s for s in week_signals if s.get("result") in ("win", "loss")]

        # Core metrics
        perf = self._perf.compute_all(week_signals) if week_signals else self._perf._empty_metrics()
        risk = self._risk.compute_all(week_signals) if week_signals else self._risk._empty_risk()
        trade_data = self._trades.analyze_all(week_signals) if week_signals else {"trades": [], "quality": {}}

        # Daily breakdown
        daily = self._daily_breakdown(closed, start, end)

        # Side breakdown
        longs = [s for s in closed if s.get("side") == "LONG"]
        shorts = [s for s in closed if s.get("side") == "SHORT"]

        # Regime breakdown
        regimes = self._regime_breakdown(closed)

        # Symbol breakdown
        symbols = self._symbol_breakdown(closed)

        # Day-of-week performance
        dow = self._day_of_week_performance(closed)

        # Weekly stats
        winning_days = sum(1 for d in daily.values() if d.get("pnl", 0) > 0)
        losing_days = sum(1 for d in daily.values() if d.get("pnl", 0) < 0)

        return {
            "report_type": "weekly",
            "period": {
                "start": start.strftime("%Y-%m-%d"),
                "end": end.strftime("%Y-%m-%d"),
                "days": (end - start).days + 1,
            },
            "generated_at": time.time(),
            "generated_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "summary": {
                "total_signals": len(week_signals),
                "total_trades": len(closed),
                "wins": perf.get("wins", 0),
                "losses": perf.get("losses", 0),
                "win_rate": perf.get("win_rate", 0),
                "total_pnl": perf.get("total_pnl", 0),
                "avg_pnl": perf.get("avg_pnl", 0),
                "profit_factor": perf.get("profit_factor", 0),
                "expectancy": perf.get("expectancy", 0),
                "avg_hold_minutes": perf.get("avg_hold_minutes", 0),
            },
            "daily_breakdown": daily,
            "sides": {
                "long_trades": len(longs),
                "short_trades": len(shorts),
                "long_pnl": round(sum(s.get("pnl", 0) for s in longs), 4),
                "short_pnl": round(sum(s.get("pnl", 0) for s in shorts), 4),
            },
            "regimes": regimes,
            "symbols": symbols,
            "day_of_week": dow,
            "risk": {
                "max_drawdown_pct": risk.get("max_drawdown_pct", 0),
                "sharpe_ratio": risk.get("sharpe_ratio", 0),
                "sortino_ratio": risk.get("sortino_ratio", 0),
            },
            "quality": trade_data.get("quality", {}),
            "stability": {
                "winning_days": winning_days,
                "losing_days": losing_days,
                "winning_days_pct": round(winning_days / max(len(daily), 1) * 100, 1),
            },
        }

    def _daily_breakdown(self, trades: List[Dict], start: datetime, end: datetime) -> Dict[str, Dict]:
        """Break down by day."""
        daily: Dict[str, List] = {}
        for t in trades:
            day = t.get("date", "")
            if day:
                daily.setdefault(day, []).append(t)

        result = {}
        for day in sorted(daily.keys()):
            trades_d = daily[day]
            wins = sum(1 for t in trades_d if t.get("result") == "win")
            total = len(trades_d)
            pnl = sum(t.get("pnl", 0) for t in trades_d)
            result[day] = {
                "trades": total,
                "wins": wins,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "pnl": round(pnl, 4),
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
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "pnl": round(sum(t.get("pnl", 0) for t in trades_r), 4),
            }
        return result

    def _symbol_breakdown(self, trades: List[Dict]) -> Dict[str, Dict]:
        """Break down by symbol."""
        symbols: Dict[str, List] = {}
        for t in trades:
            sym = t.get("symbol", "")
            symbols.setdefault(sym, []).append(t)

        result = {}
        for sym, trades_s in symbols.items():
            wins = sum(1 for t in trades_s if t.get("result") == "win")
            total = len(trades_s)
            result[sym] = {
                "trades": total,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "pnl": round(sum(t.get("pnl", 0) for t in trades_s), 4),
            }
        return result

    def _day_of_week_performance(self, trades: List[Dict]) -> Dict[str, Dict]:
        """Performance by day of week."""
        import calendar
        dow: Dict[int, List] = {}
        for t in trades:
            ts = t.get("timestamp", 0)
            if ts:
                day_num = datetime.fromtimestamp(ts, tz=timezone.utc).weekday()
                dow.setdefault(day_num, []).append(t)

        result = {}
        for day_num in sorted(dow.keys()):
            trades_d = dow[day_num]
            wins = sum(1 for t in trades_d if t.get("result") == "win")
            total = len(trades_d)
            result[calendar.day_name[day_num]] = {
                "trades": total,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "pnl": round(sum(t.get("pnl", 0) for t in trades_d), 4),
            }
        return result

    def list_available_weeks(self, weeks: int = 12) -> List[Dict]:
        """List weeks with trade data."""
        signals = self._db.get_all_signals()
        if not signals:
            return []

        dates = sorted(set(s.get("date", "") for s in signals if s.get("date")))
        if not dates:
            return []

        # Group by week
        weeks_data = []
        seen_weeks = set()
        for d in dates:
            dt = datetime.strptime(d, "%Y-%m-%d")
            monday = dt - timedelta(days=dt.weekday())
            week_key = monday.strftime("%Y-%m-%d")
            if week_key not in seen_weeks:
                seen_weeks.add(week_key)
                sunday = monday + timedelta(days=6)
                weeks_data.append({
                    "start": monday.strftime("%Y-%m-%d"),
                    "end": sunday.strftime("%Y-%m-%d"),
                })

        return sorted(weeks_data, key=lambda w: w["start"], reverse=True)[:weeks]
