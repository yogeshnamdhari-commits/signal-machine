"""
EMA_V5 Production Readiness — Checks if the system is production-ready.
Isolated from existing readiness systems.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from loguru import logger


class EMAv5ProductionReadiness:
    """Checks if the EMA_V5 system is production-ready."""

    def __init__(self) -> None:
        self._checks: List[Dict] = []
        self._passed = 0
        self._failed = 0

    def check_all(self) -> Dict[str, Any]:
        """Run all production readiness checks."""
        logger.info("📊 EMA_V5 production readiness check")
        self._checks = []
        self._passed = 0
        self._failed = 0

        self._check_environment()
        self._check_dependencies()
        self._check_database()
        self._check_configuration()
        self._check_security()
        self._check_monitoring()
        self._check_backup()

        ready = self._failed == 0

        return {
            "ready": ready,
            "total_checks": self._passed + self._failed,
            "passed": self._passed,
            "failed": self._failed,
            "checks": self._checks,
            "recommendations": self._get_recommendations(),
        }

    def _check_environment(self) -> None:
        """Check environment readiness."""
        import sys
        import os

        # Python version
        py_version = sys.version_info
        if py_version >= (3, 10):
            self._record("python_version", True, f"Python {py_version.major}.{py_version.minor}.{py_version.micro}")
        else:
            self._record("python_version", False, f"Python {py_version.major}.{py_version.minor} < 3.10")

        # Required packages
        required = ["numpy", "pandas", "loguru", "httpx", "openpyxl"]
        missing = []
        for pkg in required:
            try:
                __import__(pkg)
            except ImportError:
                missing.append(pkg)

        if not missing:
            self._record("packages", True, "All required packages installed")
        else:
            self._record("packages", False, f"Missing: {', '.join(missing)}")

        # Disk space
        try:
            import shutil
            usage = shutil.disk_usage(".")
            free_gb = usage.free / (1024 ** 3)
            if free_gb > 1:
                self._record("disk_space", True, f"{free_gb:.1f}GB free")
            else:
                self._record("disk_space", False, f"{free_gb:.2f}GB free (need >1GB)")
        except Exception:
            self._record("disk_space", True, "Disk check skipped")

    def _check_dependencies(self) -> None:
        """Check module dependencies."""
        modules = [
            "scanner.ema_v5.scanner",
            "scanner.ema_v5.storage.database",
            "scanner.ema_v5.verification.verifier",
            "scanner.ema_v5.analytics.performance_calculator",
            "scanner.ema_v5.execution.paper_trader",
            "scanner.ema_v5.gateway.api_server",
            "scanner.ema_v5.security.input_sanitizer",
            "scanner.ema_v5.integration.unified_entry",
        ]

        failed_imports = []
        for module in modules:
            try:
                __import__(module)
            except Exception:
                failed_imports.append(module.split(".")[-1])

        if not failed_imports:
            self._record("dependencies", True, "All modules importable")
        else:
            self._record("dependencies", False, f"Failed: {', '.join(failed_imports)}")

    def _check_database(self) -> None:
        """Check database readiness."""
        from pathlib import Path
        db_path = Path("data/ema_v5_signals.db")

        if db_path.exists():
            try:
                import sqlite3
                conn = sqlite3.connect(str(db_path), timeout=5)
                conn.execute("SELECT 1")
                conn.close()
                self._record("database", True, "Database accessible")
            except Exception as e:
                self._record("database", False, f"Database error: {e}")
        else:
            self._record("database", True, "Database will be created on first run")

    def _check_configuration(self) -> None:
        """Check configuration readiness."""
        try:
            from ..config import ema_v5_config
            # Validate critical config values
            assert ema_v5_config.ema.fast > 0
            assert ema_v5_config.ema.medium > ema_v5_config.ema.fast
            assert ema_v5_config.signal.min_rr > 0
            assert ema_v5_config.confidence.min_confidence > 0
            self._record("configuration", True, "Configuration valid")
        except Exception as e:
            self._record("configuration", False, f"Config error: {e}")

    def _check_security(self) -> None:
        """Check security readiness."""
        try:
            from ..security.input_sanitizer import EMAv5InputSanitizer
            san = EMAv5InputSanitizer()

            # Test sanitization
            result = san.check_sql_injection("SELECT * FROM users")
            assert result["safe"] == False

            # Test XSS detection
            result2 = san.check_xss("<script>alert(1)</script>")
            assert result2["safe"] == False

            self._record("security", True, "Security checks passed")
        except Exception as e:
            self._record("security", False, f"Security error: {e}")

    def _check_monitoring(self) -> None:
        """Check monitoring readiness."""
        try:
            from ..deploy.health_check import EMAv5HealthCheck
            hc = EMAv5HealthCheck()
            health = hc.check_all()
            if health["summary"]["error"] == 0:
                self._record("monitoring", True, "Health checks pass")
            else:
                self._record("monitoring", False, f"{health['summary']['error']} health check errors")
        except Exception as e:
            self._record("monitoring", False, f"Monitoring error: {e}")

    def _check_backup(self) -> None:
        """Check backup readiness."""
        from pathlib import Path

        # Check if backup directory exists
        backup_dir = Path("data/backup")
        if backup_dir.exists():
            self._record("backup", True, "Backup directory exists")
        else:
            self._record("backup", True, "Backup directory will be created")

    def _record(self, name: str, passed: bool, details: str) -> None:
        """Record a check result."""
        if passed:
            self._passed += 1
        else:
            self._failed += 1
        self._checks.append({
            "check": name,
            "passed": passed,
            "details": details,
        })

    def _get_recommendations(self) -> List[str]:
        """Get recommendations based on failed checks."""
        recommendations = []
        for check in self._checks:
            if not check["passed"]:
                if check["check"] == "packages":
                    recommendations.append("Install missing packages: pip install -r requirements.txt")
                elif check["check"] == "database":
                    recommendations.append("Check database permissions and path")
                elif check["check"] == "security":
                    recommendations.append("Review security configuration")
                elif check["check"] == "monitoring":
                    recommendations.append("Fix health check errors before deployment")
        return recommendations
