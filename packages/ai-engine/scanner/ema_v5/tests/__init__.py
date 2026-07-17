"""
EMA_V5 Tests — Comprehensive test suite for all EMA_V5 modules.
Isolated from existing test systems.
"""
from .unit_tests import EMAv5UnitTests
from .integration_tests import EMAv5IntegrationTests
from .e2e_tests import EMAv5E2ETests
from .regression_tests import EMAv5RegressionTests
from .test_runner import EMAv5TestRunner

__all__ = [
    "EMAv5UnitTests",
    "EMAv5IntegrationTests",
    "EMAv5E2ETests",
    "EMAv5RegressionTests",
    "EMAv5TestRunner",
]
