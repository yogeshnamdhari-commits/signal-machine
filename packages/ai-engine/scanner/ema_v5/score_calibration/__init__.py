"""
EMA_V5 Score Calibration Framework — Statistical validation of the confidence model.

Collects candidate data, tracks outcomes, and produces analytics to determine
whether the confidence threshold and weights are correctly calibrated.

This is a DATA COLLECTION framework. It does NOT modify trading logic,
thresholds, weights, or any production behavior.
"""
from .candidate_logger import CandidateLogger
from .outcome_tracker import OutcomeTracker
from .analytics import CalibrationAnalytics
from .comprehensive_analytics import ComprehensiveAnalytics
from .component_importance import ComponentImportance
from .weight_optimizer import WeightOptimizer
from .false_analysis import FalseAnalysis
from .feature_correlation import FeatureCorrelation
from .ml_validator import MLValidator
from .monte_carlo import MonteCarloSimulator
from .dashboard import ValidationDashboard
from .execution_quality import ExecutionQualityAnalyzer
from .trade_lifecycle import TradeLifecycleValidator
from .performance_metrics import PerformanceMetrics
from .score_validation import ScoreValidator
from .forward_validation import ForwardValidator
from .milestone_tracker import MilestoneTracker
from .report import generate_calibration_report
from .full_report import generate_full_validation_report

__all__ = [
    "CandidateLogger",
    "OutcomeTracker",
    "CalibrationAnalytics",
    "ComprehensiveAnalytics",
    "ComponentImportance",
    "WeightOptimizer",
    "FalseAnalysis",
    "FeatureCorrelation",
    "MLValidator",
    "MonteCarloSimulator",
    "ValidationDashboard",
    "ExecutionQualityAnalyzer",
    "TradeLifecycleValidator",
    "PerformanceMetrics",
    "ScoreValidator",
    "ForwardValidator",
    "MilestoneTracker",
    "generate_calibration_report",
    "generate_full_validation_report",
]
