"""
EMA_V5 Production Validation Package.

CODE FREEZE: NO strategy changes allowed.
Phase 1: Paper trading with MFE/MAE tracking.
Phase 2: Small-size live validation.
Phase 3: Gradual position size increase.
Phase 4: Full production.

DO NOT modify any signal logic. This is observation-only.
"""
from .validation_engine import (
    ValidationEngine, PaperTrader, ValidationDB,
    MetricsEngine, DeviationMonitor, PromotionManager,
    ReportGenerator, BASELINE, PROMOTION_PHASES,
)
from .analytics import (
    AnalyticsEngine, SignalJournal, EvidenceDashboard,
    ConfidenceCalibration, WeaknessDetector, PromotionGates,
)
from .institutional_analytics import (
    InstitutionalAnalytics, ExtendedJournal, AlphaDiscovery,
    ConfidenceCalibrator, SymbolRanker, PortfolioAnalytics,
    LivePerformanceMonitor, InstitutionalReportGenerator, ProductionMonitor,
)
from .execution_gate import (
    ExecutionGate, ExecutionDecision, LearningEngine,
    AdaptiveThresholds, PortfolioRanker, GateDB,
)
from .portfolio_decision_engine import (
    PortfolioDecisionEngine, PortfolioSignal, AllocationDecision,
    PortfolioHealth, OpportunityRanker, CapitalAllocator,
    PortfolioHealthMonitor, CapitalRotation, DecisionLearning, PortfolioDB,
)
from .integration import ValidationHooks

__all__ = [
    "ValidationEngine", "PaperTrader", "ValidationDB",
    "MetricsEngine", "DeviationMonitor", "PromotionManager",
    "ReportGenerator", "ValidationHooks",
    "AnalyticsEngine", "SignalJournal", "EvidenceDashboard",
    "ConfidenceCalibration", "WeaknessDetector", "PromotionGates",
    "InstitutionalAnalytics", "ExtendedJournal", "AlphaDiscovery",
    "ConfidenceCalibrator", "SymbolRanker", "PortfolioAnalytics",
    "LivePerformanceMonitor", "InstitutionalReportGenerator",
    "ExecutionGate", "ExecutionDecision", "LearningEngine",
    "AdaptiveThresholds", "PortfolioRanker", "GateDB",
    "PortfolioDecisionEngine", "PortfolioSignal", "AllocationDecision",
    "PortfolioHealth", "OpportunityRanker", "CapitalAllocator",
    "PortfolioHealthMonitor", "CapitalRotation", "DecisionLearning", "PortfolioDB",
    "BASELINE", "PROMOTION_PHASES",
]
