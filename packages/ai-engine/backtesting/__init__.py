"""
DeltaTerminal Backtesting Framework
Phase 5: Backtesting + Optimization
Phase 3: Live Paper Trading Validation
"""
from .historical_data import HistoricalDataEngine
from .backtester import BacktestEngine
from .walk_forward import WalkForwardEngine
from .walk_forward_validator import WalkForwardValidator
from .walk_forward_validator_v2 import WalkForwardValidatorV2
from .monte_carlo import MonteCarloEngine
from .monte_carlo_validator import MonteCarloValidator, run_monte_carlo_validation
from .optimizer import AIAdaptiveOptimizer
from .analytics import PerformanceAnalyticsEngine
from .paper_trading_validator import (
    PaperTradingEngine,
    PaperSignal,
    PaperTrade,
    ExecutionQuality,
    DailyReport,
    WeeklyReport,
    SystemHealth,
    PaperTradingSummary,
    SimulatedPositionManager,
    SystemHealthMonitor,
    ExecutionQualityAnalyzer,
    run_paper_trading_validation,
)

__all__ = [
    "HistoricalDataEngine",
    "BacktestEngine",
    "WalkForwardEngine",
    "WalkForwardValidator",
    "WalkForwardValidatorV2",
    "MonteCarloEngine",
    "MonteCarloValidator",
    "run_monte_carlo_validation",
    "AIAdaptiveOptimizer",
    "PerformanceAnalyticsEngine",
    "PaperTradingEngine",
    "PaperSignal",
    "PaperTrade",
    "ExecutionQuality",
    "DailyReport",
    "WeeklyReport",
    "SystemHealth",
    "PaperTradingSummary",
    "SimulatedPositionManager",
    "SystemHealthMonitor",
    "ExecutionQualityAnalyzer",
    "run_paper_trading_validation",
]
