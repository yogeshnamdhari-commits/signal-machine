"""
EMA_V5 Report Generator — Aggregates all analytics into a single report.
Reads from all analytics modules. Pure computation.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger

from ..storage.database import EMAv5Database
from .performance_calculator import PerformanceCalculator
from .risk_metrics import RiskMetrics
from .equity_curve import EquityCurve
from .trade_analyzer import TradeAnalyzer
from .regime_analytics import RegimeAnalytics
from .session_analytics import SessionAnalytics
from .symbol_analytics import SymbolAnalytics


class ReportGenerator:
    """Generates comprehensive EMA_V5 performance reports."""

    def __init__(self, db: Optional[EMAv5Database] = None) -> None:
        self._db = db or EMAv5Database()
        self._perf = PerformanceCalculator(self._db)
        self._risk = RiskMetrics(self._db)
        self._equity = EquityCurve(self._db)
        self._trades = TradeAnalyzer(self._db)
        self._regime = RegimeAnalytics(self._db)
        self._session = SessionAnalytics(self._db)
        self._symbol = SymbolAnalytics(self._db)

    def generate_full(self, signals: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Generate complete performance report."""
        if signals is None:
            signals = self._db.get_all_signals()

        return {
            "report_type": "ema_v5_full",
            "generated_at": time.time(),
            "generated_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "signal_count": len(signals),
            "performance": self._perf.compute_all(signals),
            "risk": self._risk.compute_all(signals),
            "equity": self._equity.compute(signals),
            "trades": self._trades.analyze_all(signals),
            "regime": self._regime.compute_all(signals),
            "session": self._session.compute_all(signals),
            "symbol": self._symbol.compute_all(signals),
        }

    def generate_summary(self, signals: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Generate condensed summary for dashboard display."""
        full = self.generate_full(signals)
        perf = full.get("performance", {})
        risk = full.get("risk", {})

        return {
            "total_trades": perf.get("total_trades", 0),
            "win_rate": perf.get("win_rate", 0),
            "total_pnl": perf.get("total_pnl", 0),
            "profit_factor": perf.get("profit_factor", 0),
            "max_drawdown_pct": risk.get("max_drawdown_pct", 0),
            "sharpe_ratio": risk.get("sharpe_ratio", 0),
            "expectancy": perf.get("expectancy", 0),
            "avg_hold_minutes": perf.get("avg_hold_minutes", 0),
            "best_symbol": full.get("symbol", {}).get("summary", {}).get("most_profitable", ""),
            "best_regime": full.get("regime", {}).get("summary", {}).get("best_regime", ""),
        }

    def generate_daily_report(self, date: Optional[str] = None,
                               signals: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Generate report for a specific day."""
        if signals is None:
            signals = self._db.get_all_signals()

        if date:
            signals = [s for s in signals if s.get("date") == date]

        daily = self._perf.compute_daily(signals)
        day_data = next((d for d in daily if d.get("date") == date), {}) if date else {}

        return {
            "report_type": "ema_v5_daily",
            "date": date or "all",
            "generated_at": time.time(),
            "performance": self._perf.compute_all(signals),
            "risk": self._risk.compute_all(signals),
            "trades": self._trades.analyze_all(signals),
            "daily_summary": day_data,
        }

    def get_quick_stats(self) -> Dict[str, Any]:
        """Quick stats for sidebar/widget display."""
        signals = self._db.get_all_signals()
        stats = self._db.get_stats()
        return {
            "total_signals": stats.get("total_signals", 0),
            "win_rate": stats.get("win_rate", 0),
            "total_pnl": stats.get("total_pnl", 0),
            "profit_factor": stats.get("profit_factor", 0),
            "buy_signals": stats.get("buy_signals", 0),
            "sell_signals": stats.get("sell_signals", 0),
        }
