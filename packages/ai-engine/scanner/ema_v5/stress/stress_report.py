"""
EMA_V5 Stress Report — Aggregates all stress test results into reports.
Isolated from existing report systems.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger

from .load_tester import EMAv5LoadTester, LoadTestConfig
from .failure_simulator import EMAv5FailureSimulator, FailureConfig
from .recovery_tester import EMAv5RecoveryTester, RecoveryTestConfig


class EMAv5StressReport:
    """Generates comprehensive stress test reports."""

    def __init__(self) -> None:
        self._load_tester = EMAv5LoadTester()
        self._failure_sim = EMAv5FailureSimulator()
        self._recovery_tester = EMAv5RecoveryTester()

    def run_all(self) -> Dict[str, Any]:
        """Run all stress tests and generate report."""
        logger.info("📊 EMA_V5 stress test suite starting")

        load_report = self._load_tester.run()
        failure_report = self._failure_sim.run()
        recovery_report = self._recovery_tester.run()

        return self._compile_full_report(load_report, failure_report, recovery_report)

    def run_load_test(self, symbol_counts: Optional[List[int]] = None) -> Dict[str, Any]:
        """Run load test only."""
        config = LoadTestConfig(symbol_counts=symbol_counts or [100, 250, 500, 1000])
        tester = EMAv5LoadTester(config)
        return tester.run()

    def run_failure_test(self) -> Dict[str, Any]:
        """Run failure simulation only."""
        return self._failure_sim.run()

    def run_recovery_test(self) -> Dict[str, Any]:
        """Run recovery test only."""
        return self._recovery_tester.run()

    def _compile_full_report(
        self,
        load_report: Dict,
        failure_report: Dict,
        recovery_report: Dict,
    ) -> Dict[str, Any]:
        """Compile full stress test report."""
        # Overall assessment
        load_passed = load_report.get("summary", {}).get("all_passed", False)
        failure_passed = failure_report.get("summary", {}).get("passed", 0) == failure_report.get("summary", {}).get("total_tests", 0)
        recovery_passed = recovery_report.get("summary", {}).get("all_passed", False)

        all_passed = load_passed and failure_passed and recovery_passed

        # Risk assessment
        if not all_passed:
            risk_level = "HIGH"
        elif failure_report.get("summary", {}).get("data_loss", False):
            risk_level = "CRITICAL"
        else:
            risk_level = "LOW"

        # Recommendations
        recommendations = []
        if not load_passed:
            bp = load_report.get("summary", {}).get("breaking_point")
            if bp:
                recommendations.append(f"Performance degrades at {bp} symbols — optimize scanning loop")
            else:
                recommendations.append("Load test failed — review scanner performance")

        if not failure_passed:
            failed_types = [
                r["failure_type"] for r in failure_report.get("results", [])
                if not r.get("passed", True)
            ]
            recommendations.append(f"Failure simulation failed for: {', '.join(failed_types)}")

        if not recovery_passed:
            recommendations.append("Recovery test failed — review state persistence")

        if failure_report.get("summary", {}).get("data_loss", False):
            recommendations.append("CRITICAL: Data loss detected in failure simulation")

        return {
            "report_type": "stress_test",
            "generated_at": time.time(),
            "generated_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "load_test": load_report,
            "failure_simulation": failure_report,
            "recovery_test": recovery_report,
            "summary": {
                "all_passed": all_passed,
                "risk_level": risk_level,
                "load_test_passed": load_passed,
                "failure_test_passed": failure_passed,
                "recovery_test_passed": recovery_passed,
                "total_tests": (
                    load_report.get("summary", {}).get("total_tests", 0) +
                    failure_report.get("summary", {}).get("total_tests", 0) +
                    recovery_report.get("summary", {}).get("total_tests", 0)
                ),
                "total_passed": (
                    (1 if load_passed else 0) +
                    failure_report.get("summary", {}).get("passed", 0) +
                    recovery_report.get("summary", {}).get("passed", 0)
                ),
            },
            "recommendations": recommendations,
        }

    def quick_health_check(self) -> Dict[str, Any]:
        """Quick health check — minimal stress test."""
        # Quick load test with small symbol count
        load = self.run_load_test(symbol_counts=[50, 100])

        return {
            "health_check": True,
            "load_test": load.get("summary", {}),
            "timestamp": time.time(),
        }
