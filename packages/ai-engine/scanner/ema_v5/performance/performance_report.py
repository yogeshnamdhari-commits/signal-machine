"""
EMA_V5 Performance Report — Aggregates all performance data into reports.
Isolated from existing report systems.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger

from ..storage.database import EMAv5Database
from .real_time_tracker import EMAv5RealTimeTracker
from .historical_analyzer import EMAv5HistoricalAnalyzer
from .benchmark_comparator import EMAv5BenchmarkComparator
from .degradation_detector import EMAv5DegradationDetector


class EMAv5PerformanceReport:
    """Generates comprehensive performance reports."""

    def __init__(self, db: Optional[EMAv5Database] = None) -> None:
        self._db = db or EMAv5Database()
        self._tracker = EMAv5RealTimeTracker()
        self._historical = EMAv5HistoricalAnalyzer(self._db)
        self._benchmark = EMAv5BenchmarkComparator(self._db)
        self._degradation = EMAv5DegradationDetector(db=self._db)

    def generate(self, signals: Optional[List[Dict]] = None) -> Dict[str, Any]:
        """Generate complete performance report."""
        return {
            "report_type": "performance",
            "generated_at": time.time(),
            "generated_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "real_time": self._tracker.get_current_metrics(),
            "historical": self._historical.analyze(signals),
            "benchmark": self._benchmark.compare(signals),
            "degradation": {
                "alerts": [
                    {"type": a.alert_type, "severity": a.severity, "message": a.message}
                    for a in self._degradation.check(signals)
                ],
                "status": self._degradation.get_status(),
            },
        }

    def quick_status(self) -> Dict[str, Any]:
        """Quick status for dashboard widget."""
        metrics = self._tracker.get_current_metrics()
        degradation = self._degradation.get_status()

        return {
            "total_trades": metrics.get("total_trades", 0),
            "win_rate": metrics.get("win_rate", 0),
            "total_pnl": metrics.get("total_pnl", 0),
            "profit_factor": metrics.get("profit_factor", 0),
            "drawdown": metrics.get("drawdown", 0),
            "streak": metrics.get("streak", {}),
            "has_degradation": degradation.get("has_critical", False),
            "today": metrics.get("today", {}),
        }

    def record_trade(self, trade_data: Dict[str, Any]) -> None:
        """Record a trade for real-time tracking."""
        from .real_time_tracker import TradeRecord
        trade = TradeRecord(
            symbol=trade_data.get("symbol", ""),
            side=trade_data.get("side", ""),
            entry_price=trade_data.get("entry_price", 0),
            exit_price=trade_data.get("exit_price", 0),
            pnl=trade_data.get("pnl", 0),
            result=trade_data.get("result", ""),
            timestamp=trade_data.get("timestamp", time.time()),
            hold_minutes=trade_data.get("hold_minutes", 0),
            regime=trade_data.get("regime", ""),
            confidence=trade_data.get("confidence", 0),
        )
        self._tracker.record_trade(trade)

    def get_tracker(self) -> EMAv5RealTimeTracker:
        """Get the real-time tracker."""
        return self._tracker

    def get_degradation_detector(self) -> EMAv5DegradationDetector:
        """Get the degradation detector."""
        return self._degradation
