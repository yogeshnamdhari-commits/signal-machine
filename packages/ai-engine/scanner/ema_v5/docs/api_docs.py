"""
EMA_V5 API Documentation — Documents all public APIs and interfaces.
Isolated from existing documentation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger


class EMAv5APIDocs:
    """Generates API documentation for EMA_V5 modules."""

    def generate(self) -> Dict[str, Any]:
        """Generate complete API documentation."""
        return {
            "version": "1.0.0",
            "modules": {
                "scanner": self._scanner_api(),
                "signal_engine": self._signal_engine_api(),
                "state_manager": self._state_manager_api(),
                "storage": self._storage_api(),
                "verification": self._verification_api(),
                "analytics": self._analytics_api(),
                "backtest": self._backtest_api(),
                "execution": self._execution_api(),
                "telegram": self._telegram_api(),
                "performance": self._performance_api(),
                "reports": self._reports_api(),
                "stress": self._stress_api(),
            },
        }

    def _scanner_api(self) -> Dict[str, Any]:
        """Scanner API documentation."""
        return {
            "class": "EMAv5Scanner",
            "description": "Main EMA_V5 scanner orchestrator",
            "methods": {
                "evaluate": {
                    "signature": "async evaluate(symbol, market_data, regime_data=None, orderflow=None, cvd_data=None) -> Optional[Dict]",
                    "description": "Evaluate a symbol for EMA_V5 signal",
                    "params": {
                        "symbol": "str - Symbol to evaluate (e.g., 'BTCUSDT')",
                        "market_data": "Dict - Market data with klines",
                        "regime_data": "Optional[Dict] - External regime data",
                        "orderflow": "Optional[Dict] - Orderflow data",
                        "cvd_data": "Optional[Dict] - CVD data",
                    },
                    "returns": "Optional[Dict] - Signal dict or None",
                },
                "get_bridge_data": {
                    "signature": "get_bridge_data() -> Dict",
                    "description": "Export scanner state for dashboard bridge",
                    "returns": "Dict with scanner stats, states, state_counts",
                },
                "get_stats": {
                    "signature": "get_stats() -> Dict",
                    "description": "Get scanner statistics",
                    "returns": "Dict with scan_count, signal_count, uptime, cache_size",
                },
            },
        }

    def _signal_engine_api(self) -> Dict[str, Any]:
        """Signal engine API documentation."""
        return {
            "class": "SignalEngine",
            "description": "Signal generation with dedup and cooldown",
            "methods": {
                "generate": {
                    "signature": "generate(symbol, regime, regime_eval, trend_eval, pullback_eval, candle_eval, volume_eval, confidence_eval, ema_data) -> Optional[Dict]",
                    "description": "Generate a signal if all conditions pass",
                    "returns": "Optional[Dict] - Signal dict or None",
                },
                "_check_duplicate": {
                    "signature": "_check_duplicate(symbol, regime) -> bool",
                    "description": "Check for duplicate signals",
                },
                "_check_cooldown": {
                    "signature": "_check_cooldown(symbol) -> bool",
                    "description": "Check cooldown between signals",
                },
            },
        }

    def _state_manager_api(self) -> Dict[str, Any]:
        """State manager API documentation."""
        return {
            "class": "StateManager",
            "description": "Per-symbol state machine with persistence",
            "methods": {
                "get_state": {
                    "signature": "get_state(symbol) -> str",
                    "description": "Get current state for a symbol",
                    "returns": "str - State name (NO_TREND, BUY_MODE, etc.)",
                },
                "set_state": {
                    "signature": "set_state(symbol, new_state) -> bool",
                    "description": "Transition to new state",
                    "returns": "bool - True if transition valid",
                },
                "reset": {
                    "signature": "reset(symbol) -> None",
                    "description": "Reset symbol to NO_TREND",
                },
                "get_all_states": {
                    "signature": "get_all_states() -> Dict[str, Dict]",
                    "description": "Get all symbol states",
                },
                "get_state_counts": {
                    "signature": "get_state_counts() -> Dict[str, int]",
                    "description": "Count symbols in each state",
                },
            },
            "states": {
                "NO_TREND": "No trend detected",
                "BUY_MODE": "Bullish trend confirmed",
                "SELL_MODE": "Bearish trend confirmed",
                "WAITING_PULLBACK": "Waiting for pullback to EMA",
                "WAITING_CONFIRMATION": "Waiting for candle confirmation",
                "ACTIVE_BUY": "Active long position",
                "ACTIVE_SELL": "Active short position",
                "TRADE_CLOSED": "Trade closed",
            },
        }

    def _storage_api(self) -> Dict[str, Any]:
        """Storage API documentation."""
        return {
            "classes": {
                "EMAv5Database": "SQLite storage for signals",
                "EMAv5JsonStorage": "JSON file persistence",
                "EMAv5ExcelWriter": "Excel export",
                "EMAv5History": "Audit trail coordination",
                "EMAv5Exporter": "CSV/JSON/Excel export",
                "EMAv5Recovery": "Restart recovery",
                "EMAv5Serializer": "Signal serialization",
            },
            "database": {
                "file": "data/ema_v5_signals.db",
                "tables": {
                    "ema_v5_signals": "Main signals table (38 columns)",
                    "ema_v5_trade_history": "Trade close history",
                },
            },
            "json_files": {
                "ema_v5.json": "Bridge file for dashboard",
                "ema_v5_state.json": "Per-symbol state persistence",
                "ema_v5_history.json": "Append-only signal/trade history",
                "ema_v5_stats.json": "Computed statistics",
            },
        }

    def _verification_api(self) -> Dict[str, Any]:
        """Verification API documentation."""
        return {
            "classes": {
                "EMAv5Verifier": "12-check signal verification engine",
                "EMAv5Diagnostics": "Per-signal diagnostic data",
                "EMAv5Statistics": "Quality metrics tracking",
                "EMAv5Quality": "Quality scoring (A+ to F)",
                "EMAv5VerificationReport": "5 report types",
            },
            "checks": [
                "ema_alignment", "trend_direction", "ema_slopes", "pullback",
                "candlestick", "volume", "confidence", "state_transition",
                "duplicate", "risk_reward", "price_validity", "trade_lifecycle",
            ],
            "verdicts": ["PASS", "WARNING", "FAIL"],
        }

    def _analytics_api(self) -> Dict[str, Any]:
        """Analytics API documentation."""
        return {
            "classes": {
                "PerformanceCalculator": "Core performance metrics",
                "RiskMetrics": "Risk-adjusted metrics (Sharpe, Sortino, etc.)",
                "EquityCurve": "Cumulative PnL tracking",
                "TradeAnalyzer": "Trade quality analysis",
                "RegimeAnalytics": "Per-regime performance",
                "SessionAnalytics": "Per-session performance",
                "SymbolAnalytics": "Per-symbol ranking",
                "ReportGenerator": "Report aggregation",
            },
        }

    def _backtest_api(self) -> Dict[str, Any]:
        """Backtest API documentation."""
        return {
            "classes": {
                "EMAv5BacktestEngine": "Core simulation engine",
                "EMAv5BacktestRunner": "Multi-symbol runner",
                "EMAv5BacktestAnalyzer": "Deep analysis",
                "EMAv5ParameterOptimizer": "Grid search",
                "EMAv5WalkForward": "Out-of-sample validation",
                "EMAv5MonteCarlo": "Stress testing",
            },
        }

    def _execution_api(self) -> Dict[str, Any]:
        """Execution API documentation."""
        return {
            "classes": {
                "EMAv5OrderManager": "Order lifecycle",
                "EMAv5PositionManager": "Position tracking",
                "EMAv5RiskManager": "Risk controls",
                "EMAv5PaperTrader": "Simulated execution",
                "EMAv5OrderHistory": "Persistent audit trail",
            },
        }

    def _telegram_api(self) -> Dict[str, Any]:
        """Telegram API documentation."""
        return {
            "classes": {
                "EMAv5TelegramBot": "Bot with rate limiting",
                "EMAv5AlertManager": "Alert routing",
                "EMAv5MessageFormatter": "HTML formatting",
                "EMAv5NotificationQueue": "Async queue",
            },
        }

    def _performance_api(self) -> Dict[str, Any]:
        """Performance API documentation."""
        return {
            "classes": {
                "EMAv5RealTimeTracker": "Live metrics",
                "EMAv5HistoricalAnalyzer": "Historical analysis",
                "EMAv5BenchmarkComparator": "Benchmark comparison",
                "EMAv5DegradationDetector": "Early warning",
                "EMAv5PerformanceReport": "Report aggregation",
            },
        }

    def _reports_api(self) -> Dict[str, Any]:
        """Reports API documentation."""
        return {
            "classes": {
                "DailyReport": "Daily report",
                "WeeklyReport": "Weekly report",
                "MonthlyReport": "Monthly report",
                "CustomReport": "Flexible filters",
                "ReportFormatter": "Text/Markdown/HTML/JSON",
            },
        }

    def _stress_api(self) -> Dict[str, Any]:
        """Stress testing API documentation."""
        return {
            "classes": {
                "EMAv5LoadTester": "Load testing",
                "EMAv5FailureSimulator": "Failure simulation",
                "EMAv5RecoveryTester": "Recovery testing",
                "EMAv5StressReport": "Report aggregation",
            },
        }
