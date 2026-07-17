from .risk_engine import RiskEngine
from .exchange_adapter import (
    ExchangeAdapter, ExchangeOrder, ExchangePosition, AccountState,
    OrderType, OrderSide, TimeInForce, PositionSide,
    ExchangeError, RateLimitError, IPBanError, ServerError,
)
from .order_manager import OrderManager, OrderRecord, OrderState, OrderPurpose
from .position_manager import PositionManager, Position, PositionStatus
from .fill_manager import FillManager, Fill, FillAggregate
from .execution_audit import ExecutionAudit, AuditEventType, AuditEvent
from .risk_guardian import RiskGuardian, RiskLevel, RiskAction, RiskState
from .position_reconciler import PositionReconciler, MismatchType, ReconciliationResult
from .execution_recovery import ExecutionRecovery, RecoveryResult
from .execution_monitor import ExecutionMonitor, HealthSnapshot
from .multi_exchange_portfolio_risk import MultiExchangePortfolioRiskEngine, RiskLimits, PortfolioSnapshot

# Guarded imports — modules with unmet dependencies are skipped gracefully
_HedgeExecutor = None
_ExecutionEngine = None

try:
    from .hedge_executor import HedgeExecutor as _HedgeExecutor
except (ImportError, ModuleNotFoundError):
    pass

try:
    from .execution_engine import ExecutionEngine as _ExecutionEngine
except (ImportError, ModuleNotFoundError):
    pass

__all__ = [
    "RiskEngine",
    "ExchangeAdapter", "ExchangeOrder", "ExchangePosition", "AccountState",
    "OrderType", "OrderSide", "TimeInForce", "PositionSide",
    "ExchangeError", "RateLimitError", "IPBanError", "ServerError",
    # Order management
    "OrderManager", "OrderRecord", "OrderState", "OrderPurpose",
    # Position management
    "PositionManager", "Position", "PositionStatus",
    # Fill management
    "FillManager", "Fill", "FillAggregate",
    # Audit
    "ExecutionAudit", "AuditEventType", "AuditEvent",
    # Risk
    "RiskEngine", "RiskGuardian", "RiskLevel", "RiskAction", "RiskState",
    # Portfolio risk
    "MultiExchangePortfolioRiskEngine", "RiskLimits", "PortfolioSnapshot",
    # Reconciliation
    "PositionReconciler", "MismatchType", "ReconciliationResult",
    # Recovery
    "ExecutionRecovery", "RecoveryResult",
    # Monitoring
    "ExecutionMonitor", "HealthSnapshot",
]

# Conditional exports — only available if their dependencies resolve
if _HedgeExecutor is not None:
    HedgeExecutor = _HedgeExecutor
    __all__.append("HedgeExecutor")

if _ExecutionEngine is not None:
    ExecutionEngine = _ExecutionEngine
    __all__.append("ExecutionEngine")
