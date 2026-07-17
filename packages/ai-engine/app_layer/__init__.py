"""
App Layer — Portfolio Manager engines that sit between Live Sheet and execution.

This layer is the ONLY code that may be modified per Master Directive.
It reads from Live Sheet (signals, market data, institutional data) and
produces trade decisions without modifying any upstream data.

Pipeline:
    EMA V5 Scanner → Smart Money → Live Sheet → [APP LAYER] → Execution

Engines (Decision):
    - Trade Quality Engine: Scores each signal on 12 dimensions
    - Institution Agreement Engine: Requires consensus among institutional data
    - Expectancy Engine: Ranks signals by Expected Value, not confidence
    - Regime Filter: Blocks low-quality regime trades
    - Reward Filter: Minimum R:R and expectancy thresholds
    - Correlation Engine: Recognizes correlated trades
    - Position Sizing Engine: Dynamic sizing by quality and risk
    - Execution Quality Filter: Pre-trade execution assessment
    - Adaptive Risk Governor: Reacts to session performance
    - Exit Engine: Dynamic trailing, breakeven, momentum, Live Sheet exits
    - Portfolio Manager: Correlation, exposure, drawdown limits
    - Position Queue: Ranks and queues simultaneous trades
    - Signal Priority: Elite/High/Medium/Low/Reject classification
    - Decision Audit: Complete evidence trail for every decision
    - Bridge Enricher: Fills data gap between bridge and full DB data
    - Pipeline: Orchestrates the complete decision flow
    - Trade Governance: Kill switch, symbol/session blacklist, confidence
      calibration, daily loss stop, time-based exit, max exposure limits

Engines (Learning):
    - Learning Engine: Adaptive self-learning from completed trades
    - Symbol Profiles: Per-symbol statistics and optimal parameters
    - Session Intelligence: Per-symbol, per-session performance tracking
    - Regime Learning: Per-regime performance tracking and auto-adjustment
    - Exit Optimizer: Learn which exit strategies work best
    - Explainability Engine: Human-readable decision summaries
    - Performance Feedback Loop: Completed trades update all engines
    - KPI Dashboard: Profit-focused metrics
    - Signal Lifecycle: Track signal through entire pipeline
    - Continuous Validation: Recompute thresholds every 500-1000 trades
"""
from .trade_quality_engine import TradeQualityEngine
from .institution_agreement_engine import InstitutionAgreementEngine
from .expectancy_engine import ExpectancyEngine
from .regime_filter import RegimeFilter
from .reward_filter import RewardFilter
from .correlation_engine import CorrelationEngine
from .position_sizing_engine import AppPositionSizingEngine
from .execution_quality import ExecutionQualityFilter
from .adaptive_risk import AdaptiveRiskGovernor
from .exit_engine import AppExitEngine
from .portfolio_manager import PortfolioManager
from .position_queue import PositionQueue
from .signal_priority import SignalPriorityClassifier
from .decision_audit import DecisionAuditLogger
from .bridge_enricher import BridgeEnricher
from .pipeline import AppLayerPipeline

# Learning engines
from .learning_engine import LearningEngine
from .symbol_profiles import SymbolProfiles
from .session_intelligence import SessionIntelligence
from .regime_learning import RegimeLearning
from .exit_optimizer import ExitOptimizer
from .explainability import ExplainabilityEngine
from .performance_feedback import PerformanceFeedbackLoop
from .kpi_dashboard import KPIEngine
from .signal_lifecycle import SignalLifecycleTracker
from .continuous_validation import ContinuousValidation

# Production-readiness engines
from .statistical_safeguards import StatisticalSafeguards
from .controlled_learning import ControlledLearning
from .champion_challenger import ChampionChallenger
from .versioned_parameters import VersionedParameterManager
from .parameter_constraints import ParameterConstraints
from .walk_forward import WalkForwardValidation

# Governance engines
from .model_governance import ModelGovernanceEngine
from .deployment_gates import DeploymentGates
from .continuous_evidence import ContinuousEvidence
from .governance_config import (
    GovernanceRules, EngineeringHealth, TradingHealth,
    StabilityMetrics, EngineValidationTracker,
    CIMetric, CIMetricsSet, DriftCategory, CategorizedDrift,
    SafeModeState, RiskStateMachine, DeploymentPhase, DeploymentPhases,
    PromotionPolicy, ResearchDashboard,
)
from .strategy_comparison import StrategyComparisonEngine
from .trade_attribution import TradeAttribution, TradeAttributionLogger
from .performance_milestones import PerformanceMilestoneTracker
from .trade_quality_dashboard import TradeQualityDashboard, AcceptanceCurve, FalsePositiveExplorer

__all__ = [
    # Decision engines
    "TradeQualityEngine",
    "InstitutionAgreementEngine",
    "ExpectancyEngine",
    "RegimeFilter",
    "RewardFilter",
    "CorrelationEngine",
    "AppPositionSizingEngine",
    "ExecutionQualityFilter",
    "AdaptiveRiskGovernor",
    "AppExitEngine",
    "PortfolioManager",
    "PositionQueue",
    "SignalPriorityClassifier",
    "DecisionAuditLogger",
    "BridgeEnricher",
    "AppLayerPipeline",
    # Learning engines
    "LearningEngine",
    "SymbolProfiles",
    "SessionIntelligence",
    "RegimeLearning",
    "ExitOptimizer",
    "ExplainabilityEngine",
    "PerformanceFeedbackLoop",
    "KPIEngine",
    "SignalLifecycleTracker",
    "ContinuousValidation",
    # Production-readiness engines
    "StatisticalSafeguards",
    "ControlledLearning",
    "ChampionChallenger",
    "VersionedParameterManager",
    "ParameterConstraints",
    "WalkForwardValidation",
    # Governance engines
    "ModelGovernanceEngine",
    "DeploymentGates",
    "ContinuousEvidence",
    "GovernanceRules",
    "EngineeringHealth",
    "TradingHealth",
    "StabilityMetrics",
    "EngineValidationTracker",
    "CIMetric",
    "CIMetricsSet",
    "DriftCategory",
    "CategorizedDrift",
    "SafeModeState",
    "RiskStateMachine",
    "DeploymentPhase",
    "DeploymentPhases",
    "PromotionPolicy",
    "ResearchDashboard",
    "StrategyComparisonEngine",
    "TradeAttribution",
    "TradeAttributionLogger",
    "PerformanceMilestoneTracker",
    "TradeQualityDashboard",
]
