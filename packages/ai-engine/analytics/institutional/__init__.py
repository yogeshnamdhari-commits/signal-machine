"""
Institutional Analytics Package
================================
Production-grade analytics, validation, and monitoring platform.

READ-ONLY — Never modifies trading logic.
Only measures, validates, audits, and reports.

Modules:
- trade_journal: Permanent trade journaling
- drift_detector: Strategy drift detection
- health_monitor: System health dashboard
- pattern_analytics: Pattern-based performance analysis
- institutional_reports: Automated report generation
- calibration_assistant: Evidence-based recommendations
- statistical_validation: Comprehensive statistical validation
- portfolio_analytics: Portfolio-level analytics and risk metrics
- observability_logger: Structured logging for all decisions
- configuration_governance: Immutable configuration records
- validation_report: Comprehensive validation reports
"""
from .trade_journal import TradeJournal
from .drift_detector import DriftDetector
from .health_monitor import HealthMonitor
from .pattern_analytics import PatternAnalytics
from .institutional_reports import InstitutionalReports
from .calibration_assistant import CalibrationAssistant
from .statistical_validation import StatisticalValidationEngine, get_statistical_engine
from .portfolio_analytics import PortfolioAnalyticsEngine, get_portfolio_engine
from .observability_logger import ObservabilityLogger, get_observability_logger
from .configuration_governance import ConfigurationGovernance, get_configuration_governance
from .validation_report import ValidationReportGenerator, get_report_generator

__all__ = [
    "TradeJournal",
    "DriftDetector",
    "HealthMonitor",
    "PatternAnalytics",
    "InstitutionalReports",
    "CalibrationAssistant",
    "StatisticalValidationEngine",
    "get_statistical_engine",
    "PortfolioAnalyticsEngine",
    "get_portfolio_engine",
    "ObservabilityLogger",
    "get_observability_logger",
    "ConfigurationGovernance",
    "get_configuration_governance",
    "ValidationReportGenerator",
    "get_report_generator",
]
