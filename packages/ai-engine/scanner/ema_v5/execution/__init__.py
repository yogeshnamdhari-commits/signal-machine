"""
EMA_V5 Execution — Isolated order execution layer for EMA_V5 strategy.
Reads from exchange adapters. Never modifies existing execution code.
"""
from .order_manager import EMAv5OrderManager
from .position_manager import EMAv5PositionManager
from .risk_manager import EMAv5RiskManager
from .paper_trader import EMAv5PaperTrader
from .order_history import EMAv5OrderHistory

__all__ = [
    "EMAv5OrderManager",
    "EMAv5PositionManager",
    "EMAv5RiskManager",
    "EMAv5PaperTrader",
    "EMAv5OrderHistory",
]
