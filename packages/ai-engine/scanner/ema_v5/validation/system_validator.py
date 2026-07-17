"""
EMA_V5 System Validator — Validates the entire EMA_V5 system.
Checks all modules, dependencies, and configurations.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from loguru import logger


class EMAv5SystemValidator:
    """Validates the entire EMA_V5 system."""

    def __init__(self) -> None:
        self._results: List[Dict] = []
        self._passed = 0
        self._failed = 0
        self._warnings = 0

    def validate_all(self) -> Dict[str, Any]:
        """Run all validation checks."""
        logger.info("📊 EMA_V5 system validation starting")
        self._results = []
        self._passed = 0
        self._failed = 0
        self._warnings = 0

        self._validate_imports()
        self._validate_configuration()
        self._validate_storage()
        self._validate_scanner()
        self._validate_verification()
        self._validate_analytics()
        self._validate_execution()
        self._validate_gateway()
        self._validate_security()
        self._validate_integration()

        return self._compile_report()

    def _validate_imports(self) -> None:
        """Validate all module imports."""
        modules = [
            "scanner.ema_v5.scanner",
            "scanner.ema_v5.config",
            "scanner.ema_v5.state_manager",
            "scanner.ema_v5.signal_engine",
            "scanner.ema_v5.storage.database",
            "scanner.ema_v5.storage.serializer",
            "scanner.ema_v5.verification.verifier",
            "scanner.ema_v5.analytics.performance_calculator",
            "scanner.ema_v5.execution.paper_trader",
            "scanner.ema_v5.gateway.api_server",
            "scanner.ema_v5.security.input_sanitizer",
            "scanner.ema_v5.integration.unified_entry",
        ]

        for module in modules:
            try:
                __import__(module)
                self._record("import_" + module.split(".")[-1], True, f"Module {module} imported")
            except Exception as e:
                self._record("import_" + module.split(".")[-1], False, f"Import failed: {e}")

    def _validate_configuration(self) -> None:
        """Validate configuration."""
        try:
            from ..config import ema_v5_config
            assert ema_v5_config.ema.fast == 20
            assert ema_v5_config.ema.medium == 50
            assert ema_v5_config.ema.institutional == 144
            assert ema_v5_config.ema.long_term == 200
            assert ema_v5_config.signal.min_rr == 1.5
            assert ema_v5_config.confidence.min_confidence == 90.0
            self._record("config", True, "Configuration valid")
        except Exception as e:
            self._record("config", False, f"Config validation failed: {e}")

    def _validate_storage(self) -> None:
        """Validate storage layer."""
        try:
            from ..storage.database import EMAv5Database
            from ..storage.serializer import EMAv5Serializer

            # Test serializer
            ser = EMAv5Serializer()
            test_signal = {
                "symbol": "BTCUSDT", "side": "LONG", "entry": 100000,
                "sl": 99000, "timestamp": time.time(),
            }
            uuid = ser.generate_uuid(test_signal)
            assert uuid.startswith("emav5-")

            self._record("storage", True, "Storage layer valid")
        except Exception as e:
            self._record("storage", False, f"Storage validation failed: {e}")

    def _validate_scanner(self) -> None:
        """Validate scanner module."""
        try:
            from ..scanner import EMAv5Scanner
            scanner = EMAv5Scanner()
            stats = scanner.get_stats()
            assert "scan_count" in stats
            assert "signal_count" in stats
            self._record("scanner", True, "Scanner module valid")
        except Exception as e:
            self._record("scanner", False, f"Scanner validation failed: {e}")

    def _validate_verification(self) -> None:
        """Validate verification module."""
        try:
            from ..verification.verifier import EMAv5Verifier
            v = EMAv5Verifier()
            stats = v.get_stats()
            assert "total_verifications" in stats
            self._record("verification", True, "Verification module valid")
        except Exception as e:
            self._record("verification", False, f"Verification validation failed: {e}")

    def _validate_analytics(self) -> None:
        """Validate analytics module."""
        try:
            from ..analytics.performance_calculator import PerformanceCalculator
            pc = PerformanceCalculator()
            metrics = pc._empty_metrics()
            assert "total_trades" in metrics
            self._record("analytics", True, "Analytics module valid")
        except Exception as e:
            self._record("analytics", False, f"Analytics validation failed: {e}")

    def _validate_execution(self) -> None:
        """Validate execution module."""
        try:
            from ..execution.paper_trader import EMAv5PaperTrader
            pt = EMAv5PaperTrader()
            status = pt.get_status()
            assert "balance" in status
            self._record("execution", True, "Execution module valid")
        except Exception as e:
            self._record("execution", False, f"Execution validation failed: {e}")

    def _validate_gateway(self) -> None:
        """Validate gateway module."""
        try:
            from ..gateway.api_server import EMAv5APIServer
            server = EMAv5APIServer()
            routes = server.get_routes()
            assert len(routes) > 0
            self._record("gateway", True, f"Gateway valid: {len(routes)} routes")
        except Exception as e:
            self._record("gateway", False, f"Gateway validation failed: {e}")

    def _validate_security(self) -> None:
        """Validate security module."""
        try:
            from ..security.input_sanitizer import EMAv5InputSanitizer
            san = EMAv5InputSanitizer()
            result = san.check_sql_injection("SELECT * FROM users")
            assert result["safe"] == False
            self._record("security", True, "Security module valid")
        except Exception as e:
            self._record("security", False, f"Security validation failed: {e}")

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
            self._record("integration", False, f"Integration validation failed: {e}")

    def _record(self, name: str, passed: bool, details: str) -> None:
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
            "validation_type": "system",
            "total": self._passed + self._failed,
            "passed": self._passed,
            "failed": self._failed,
            "pass_rate": round(self._passed / max(self._passed + self._failed, 1) * 100, 1),
            "results": self._results,
            "all_passed": self._failed == 0,
        }
