"""
Production Analytics & Validation Package — Phase III Evidence Collection.

Modules:
- production_analytics: Core metrics calculation from positions table
- trade_dashboard: Terminal-based research dashboard
- milestone_reports: Automated milestone report generator
"""
from .production_analytics import ProductionAnalytics
from .trade_dashboard import TradeDashboard
from .milestone_reports import MilestoneReportGenerator

__all__ = ["ProductionAnalytics", "TradeDashboard", "MilestoneReportGenerator"]
