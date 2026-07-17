"""
EMA_V5 Final Validator — Comprehensive final validation for production.
Isolated from existing validation systems.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from loguru import logger


class EMAv5FinalValidator:
    """Comprehensive final validation for EMA_V5."""

    def __init__(self) -> None:
        self._results: List[Dict] = []
        self._passed = 0
        self._failed = 0

    def validate_all(self) -> Dict[str, Any]:
        """Run all final validation checks."""
        logger.info("📊 EMA_V5 final validation starting")
        self._results = []
        self._passed = 0
        self._failed = 0

        self._validate_system()
        self._validate_storage()
        self._validate_scanner()
        self._validate_verification()
        self._validate_analytics()
        self._validate_execution()
        self._validate_gateway()
        self._validate_security()
        self._validate_integration()
        self._validate_testing()
        self._validate_documentation()
        self._validate_deployment()
        self._validate_monitoring()

        return self._compile_report()

    def _validate_system(self) -> None:
        """Validate system structure."""
        try:
            from pathlib import Path

            packages = [d for d in Path("scanner/ema_v5").iterdir()
                       if d.is_dir() and not d.name.startswith("__")]
            assert len(packages) >= 25, f"Expected >=25 packages, got {len(packages)}"

            py_files = list(Path("scanner/ema_v5").rglob("*.py"))
            assert len(py_files) >= 140, f"Expected >=140 files, got {len(py_files)}"

            self._record("system_structure", True, f"{len(packages)} packages, {len(py_files)} files")
        except Exception as e:
            self._record("system_structure", False, str(e))

    def _validate_storage(self) -> None:
        """Validate storage layer."""
        try:
            from ..storage.database import EMAv5Database
            from ..storage.serializer import EMAv5Serializer

            ser = EMAv5Serializer()
            test_signal = {
                "symbol": "BTCUSDT", "side": "LONG", "entry": 100000,
                "sl": 99000, "timestamp": time.time(),
            }
            uuid = ser.generate_uuid(test_signal)
            assert uuid.startswith("emav5-")

            self._record("storage", True, "Storage layer valid")
        except Exception as e:
            self._record("storage", False, str(e))

    def _validate_scanner(self) -> None:
        """Validate scanner module."""
        try:
            from ..scanner import EMAv5Scanner
            scanner = EMAv5Scanner()
            stats = scanner.get_stats()
            assert "scan_count" in stats
            self._record("scanner", True, "Scanner module valid")
        except Exception as e:
            self._record("scanner", False, str(e))

    def _validate_verification(self) -> None:
        """Validate verification module."""
        try:
            from ..verification.verifier import EMAv5Verifier
            v = EMAv5Verifier()
            stats = v.get_stats()
            assert "total_verifications" in stats
            self._record("verification", True, "Verification module valid")
        except Exception as e:
            self._record("verification", False, str(e))

    def _validate_analytics(self) -> None:
        """Validate analytics module."""
        try:
            from ..analytics.performance_calculator import PerformanceCalculator
            pc = PerformanceCalculator()
            metrics = pc._empty_metrics()
            assert "total_trades" in metrics
            self._record("analytics", True, "Analytics module valid")
        except Exception as e:
            self._record("analytics", False, str(e))

    def _validate_execution(self) -> None:
        """Validate execution module."""
        try:
            from ..execution.paper_trader import EMAv5PaperTrader
            pt = EMAv5PaperTrader()
            status = pt.get_status()
            assert "balance" in status
            self._record("execution", True, "Execution module valid")
        except Exception as e:
            self._record("execution", False, str(e))

    def _validate_gateway(self) -> None:
        """Validate gateway module."""
        try:
            from ..gateway.api_server import EMAv5APIServer
            server = EMAv5APIServer()
            routes = server.get_routes()
            assert len(routes) > 0
            self._record("gateway", True, f"Gateway valid: {len(routes)} routes")
        except Exception as e:
            self._record("gateway", False, str(e))

    def _validate_security(self) -> None:
        """Validate security module."""
        try:
            from ..security.input_sanitizer import EMAv5InputSanitizer
            san = EMAv5InputSanitizer()
            result = san.check_sql_injection("SELECT * FROM users")
            assert result["safe"] == False
            self._record("security", True, "Security module valid")
        except Exception as e:
            self._record("security", False, str(e))

    def _validate_integration(self) -> None:
        """Validate integration module."""
        try:
            from ..integration.module_registry import EMAv5ModuleRegistry
            registry = EMAv5ModuleRegistry()
            registry.register("test", lambda: {})
            modules = registry.list_modules()
            assert len(modules) > 0
            self._record("integration", True, "Integration module valid")
        except Exception as e:
            self._record("integration", False, str(e))

    def _validate_testing(self) -> None:
        """Validate testing module."""
        try:
            from ..final_testing.final_system_test import EMAv5FinalSystemTest
            from ..final_testing.final_performance_test import EMAv5FinalPerformanceTest
            from ..final_testing.final_security_test import EMAv5FinalSecurityTest
            self._record("testing", True, "Testing module valid")
        except Exception as e:
            self._record("testing", False, str(e))

    def _validate_documentation(self) -> None:
        """Validate documentation module."""
        try:
            from ..final_documentation.final_system_docs import EMAv5FinalSystemDocs
            sd = EMAv5FinalSystemDocs()
            doc = sd.generate()
            assert "overview" in doc
            self._record("documentation", True, "Documentation module valid")
        except Exception as e:
            self._record("documentation", False, str(e))

    def _validate_deployment(self) -> None:
        """Validate deployment module."""
        try:
            from ..final_deployment.final_deploy_script import EMAv5FinalDeployScript
            from ..final_deployment.final_env_config import EMAv5FinalEnvConfig
            self._record("deployment", True, "Deployment module valid")
        except Exception as e:
            self._record("deployment", False, str(e))

    def _validate_monitoring(self) -> None:
        """Validate monitoring module."""
        try:
            from ..final_deployment.final_monitoring import EMAv5FinalMonitoring
            mon = EMAv5FinalMonitoring()
            result = mon.setup()
            assert result["monitors"] > 0
            self._record("monitoring", True, f"Monitoring valid: {result['monitors']} monitors")
        except Exception as e:
            self._record("monitoring", False, str(e))

    def _record(self, name: str, passed: bool, details: str = "") -> None:
        """Record a validation result."""
        if passed:
            self._passed += 1
        else:
            self._failed += 1
        self._results.append({
            "check": name,
            "passed": passed,
            "details": details,
        })

    def _compile_report(self) -> Dict[str, Any]:
        """Compile validation report."""
        return {
            "validation_type": "final_v2",
            "total": self._passed + self._failed,
            "passed": self._passed,
            "failed": self._failed,
            "pass_rate": round(self._passed / max(self._passed + self._failed, 1) * 100, 1),
            "results": self._results,
            "all_passed": self._failed == 0,
        }
