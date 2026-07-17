"""Capital Deployment Framework — Production-grade capital management"""
from .capital_tiers import CapitalTierManager, TierConfig, DeploymentTier
from .position_sizing_engine import (
    PositionSizingEngine, SizingMethod, PortfolioState, SizingRequest, SizingResult
)
from .portfolio_risk_engine import (
    PortfolioRiskEngine, PortfolioSnapshot, PositionRisk, RiskLimits
)
from .kill_switch import EmergencyKillSwitch, KillTrigger, KillState, KillEvent
from .slippage_analyzer import SlippageAnalyzer, SlippageRecord, SlippageReport
from .performance_validator import PerformanceValidator
from .automated_reporter import AutomatedReporter, PeriodReport
from .operational_monitor import OperationalMonitor
from .alert_dispatcher import AlertDispatcher
from .deployment_validator import DeploymentValidator, DeploymentChecklist
from .orchestrator import CapitalDeploymentOrchestrator

__all__ = [
    # Capital tiers
    "CapitalTierManager", "TierConfig", "DeploymentTier",
    # Position sizing
    "PositionSizingEngine", "SizingMethod", "PortfolioState", "SizingRequest", "SizingResult",
    # Portfolio risk
    "PortfolioRiskEngine", "PortfolioSnapshot", "PositionRisk", "RiskLimits",
    # Kill switch
    "EmergencyKillSwitch", "KillTrigger", "KillState", "KillEvent",
    # Slippage
    "SlippageAnalyzer", "SlippageRecord", "SlippageReport",
    # Performance
    "PerformanceValidator",
    # Reporting
    "AutomatedReporter", "PeriodReport",
    # Operations
    "OperationalMonitor",
    # Alerting
    "AlertDispatcher",
    # Validation
    "DeploymentValidator", "DeploymentChecklist",
    # Orchestration
    "CapitalDeploymentOrchestrator",
]
