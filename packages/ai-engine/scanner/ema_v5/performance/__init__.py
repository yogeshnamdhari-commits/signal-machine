"""
EMA_V5 Performance — Real-time performance tracking and benchmarking.
Isolated from existing analytics. Focuses on live tracking and degradation detection.
"""
from .real_time_tracker import EMAv5RealTimeTracker
from .historical_analyzer import EMAv5HistoricalAnalyzer
from .benchmark_comparator import EMAv5BenchmarkComparator
from .degradation_detector import EMAv5DegradationDetector
from .performance_report import EMAv5PerformanceReport

__all__ = [
    "EMAv5RealTimeTracker",
    "EMAv5HistoricalAnalyzer",
    "EMAv5BenchmarkComparator",
    "EMAv5DegradationDetector",
    "EMAv5PerformanceReport",
]
