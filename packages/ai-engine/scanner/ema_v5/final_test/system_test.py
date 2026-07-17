"""
EMA_V5 System Test — Comprehensive system testing for production.
Isolated from existing testing systems.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from loguru import logger


class EMAv5SystemTest:
    """Comprehensive system testing for EMA_V5."""

    def __init__(self) -> None:
        self._results: List[Dict] = []
        self._passed = 0
        self._failed = 0

    def run_all(self) -> Dict[str, Any]:
        """Run all system tests."""
        logger.info("📊 EMA_V5 system testing starting")
        self._results = []
        self._passed = 0
        self._failed = 0

        self._test_imports()
        self._test_configuration()
        self._test_storage()
        self._test_scanner()
        self._test_verification()
        self._test_analytics()
        self._test_execution()
        self._test_gateway()
        self._test_security()
        self._test_integration()
        self._test_performance()
        self._test_stress()

        return self._compile_report()

    def _test_imports(self) -> None:
        """Test all module imports."""
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
                self._record("import_" + module.split(".")[-1], True)
            except Exception as e:
                self._record("import_" + module.split(".")[-1], False, str(e))

    def _test_configuration(self) -> None:
        """Test configuration."""
        try:
            from ..config import ema_v5_config
            assert ema_v5_config.ema.fast == 20
            assert ema_v5_config.ema.medium == 50
            assert ema_v5_config.signal.min_rr == 1.5
            self._record("config", True)
        except Exception as e:
            self._record("config", False, str(e))

    def _test_storage(self) -> None:
        """Test storage layer."""
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

            self._record("storage", True)
        except Exception as e:
            self._record("storage", False, str(e))

    def _test_scanner(self) -> None:
        """Test scanner module."""
        try:
            from ..scanner import EMAv5Scanner
            scanner = EMAv5Scanner()
            stats = scanner.get_stats()
            assert "scan_count" in stats
            self._record("scanner", True)
        except Exception as e:
            self._record("scanner", False, str(e))

    def _test_verification(self) -> None:
        """Test verification module."""
        try:
            from ..verification.verifier import EMAv5Verifier
            v = EMAv5Verifier()
            stats = v.get_stats()
            assert "total_verifications" in stats
            self._record("verification", True)
        except Exception as e:
            self._record("verification", False, str(e))

    def _test_analytics(self) -> None:
        """Test analytics module."""
        try:
            from ..analytics.performance_calculator import PerformanceCalculator
            pc = PerformanceCalculator()
            metrics = pc._empty_metrics()
            assert "total_trades" in metrics
            self._record("analytics", True)
        except Exception as e:
            self._record("analytics", False, str(e))

    def _test_execution(self) -> None:
        """Test execution module."""
        try:
            from ..execution.paper_trader import EMAv5PaperTrader
            pt = EMAv5PaperTrader()
            status = pt.get_status()
            assert "balance" in status
            self._record("execution", True)
        except Exception as e:
            self._record("execution", False, str(e))

    def _test_gateway(self) -> None:
        """Test gateway module."""
        try:
            from ..gateway.api_server import EMAv5APIServer
            server = EMAv5APIServer()
            routes = server.get_routes()
            assert len(routes) > 0
            self._record("gateway", True)
        except Exception as e:
            self._record("gateway", False, str(e))

    def _test_security(self) -> None:
        """Test security module."""
        try:
            from ..security.input_sanitizer import EMAv5InputSanitizer
            san = EMAv5InputSanitizer()
            result = san.check_sql_injection("SELECT * FROM users")
            assert result["safe"] == False
            self._record("security", True)
        except Exception as e:
            self._record("security", False, str(e))

    def _test_integration(self) -> None:
        """Test integration module."""
        try:
            from ..integration.module_registry import EMAv5ModuleRegistry
            registry = EMAv5ModuleRegistry()
            registry.register("test", lambda: {})
            modules = registry.list_modules()
            assert len(modules) > 0
            self._record("integration", True)
        except Exception as e:
            self._record("integration", False, str(e))

    def _test_performance(self) -> None:
        """Test performance metrics."""
        try:
            from ..performance.real_time_tracker import EMAv5RealTimeTracker
            tracker = EMAv5RealTimeTracker()
            metrics = tracker.get_current_metrics()
            assert "total_trades" in metrics
            self._record("performance", True)
        except Exception as e:
            self._record("performance", False, str(e))

    def _test_stress(self) -> None:
        """Test stress testing module."""
        try:
            from ..stress.load_tester import EMAv5LoadTester
            lt = EMAv5LoadTester()
            self._record("stress", True)
        except Exception as e:
            self._record("stress", False, str(e))

    def _record(self, name: str, passed: bool, details: str = "") -> None:
        """Record a test result."""
        if passed:
            self._passed += 1
        else:
            self._failed += 1
        self._results.append({
            "test": name,
            "passed": passed,
            "details": details,
        })

    def _compile_report(self) -> Dict[str, Any]:
        """Compile test report."""
        return {
            "test_type": "system",
            "total": self._passed + self._failed,
            "passed": self._passed,
            "failed": self._failed,
            "pass_rate": round(self._passed / max(self._passed + self._failed, 1) * 100, 1),
            "results": self._results,
            "all_passed": self._failed == 0,
        }
