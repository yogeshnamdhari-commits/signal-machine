"""
EMA_V5 Test Runner — Runs all test suites and generates comprehensive report.
Isolated from existing test runners.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from loguru import logger

from .unit_tests import EMAv5UnitTests
from .integration_tests import EMAv5IntegrationTests
from .e2e_tests import EMAv5E2ETests
from .regression_tests import EMAv5RegressionTests


class EMAv5TestRunner:
    """Runs all EMA_V5 test suites."""

    def __init__(self) -> None:
        self._unit = EMAv5UnitTests()
        self._integration = EMAv5IntegrationTests()
        self._e2e = EMAv5E2ETests()
        self._regression = EMAv5RegressionTests()

    def run_all(self) -> Dict[str, Any]:
        """Run all test suites."""
        logger.info("📊 EMA_V5 test suite starting")
        start = time.time()

        unit_report = self._unit.run_all()
        integration_report = self._integration.run_all()
        e2e_report = self._e2e.run_all()
        regression_report = self._regression.run_all()

        elapsed = (time.time() - start) * 1000

        return self._compile_full_report(
            unit_report, integration_report, e2e_report, regression_report, elapsed
        )

    def run_unit(self) -> Dict[str, Any]:
        """Run unit tests only."""
        return self._unit.run_all()

    def run_integration(self) -> Dict[str, Any]:
        """Run integration tests only."""
        return self._integration.run_all()

    def run_e2e(self) -> Dict[str, Any]:
        """Run E2E tests only."""
        return self._e2e.run_all()

    def run_regression(self) -> Dict[str, Any]:
        """Run regression tests only."""
        return self._regression.run_all()

    def _compile_full_report(
        self,
        unit: Dict,
        integration: Dict,
        e2e: Dict,
        regression: Dict,
        elapsed_ms: float,
    ) -> Dict[str, Any]:
        """Compile full test report."""
        total = unit["total"] + integration["total"] + e2e["total"] + regression["total"]
        passed = unit["passed"] + integration["passed"] + e2e["passed"] + regression["passed"]
        failed = unit["failed"] + integration["failed"] + e2e["failed"] + regression["failed"]

        all_passed = failed == 0

        # Collect all failed tests
        all_failures = []
        for suite_name, suite in [("unit", unit), ("integration", integration), ("e2e", e2e), ("regression", regression)]:
            for r in suite.get("results", []):
                if not r.get("passed", True):
                    all_failures.append({
                        "suite": suite_name,
                        "test": r["test"],
                        "details": r.get("details", ""),
                    })

        return {
            "report_type": "test_suite",
            "generated_at": time.time(),
            "generated_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "unit": unit,
            "integration": integration,
            "e2e": e2e,
            "regression": regression,
            "summary": {
                "total": total,
                "passed": passed,
                "failed": failed,
                "pass_rate": round(passed / max(total, 1) * 100, 1),
                "all_passed": all_passed,
                "elapsed_ms": round(elapsed_ms, 1),
                "suites": {
                    "unit": f"{unit['passed']}/{unit['total']}",
                    "integration": f"{integration['passed']}/{integration['total']}",
                    "e2e": f"{e2e['passed']}/{e2e['total']}",
                    "regression": f"{regression['passed']}/{regression['total']}",
                },
            },
            "failures": all_failures,
        }

    def quick_check(self) -> Dict[str, Any]:
        """Quick smoke test — minimal test suite."""
        unit = self._unit.run_all()
        regression = self._regression.run_all()

        total = unit["total"] + regression["total"]
        passed = unit["passed"] + regression["passed"]

        return {
            "quick_check": True,
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "all_passed": passed == total,
        }
