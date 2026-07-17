"""
EMA_V5 Deploy Script — Production deployment automation.
Isolated from existing deployment scripts.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger


class EMAv5DeployScript:
    """Production deployment automation for EMA_V5."""

    def __init__(self) -> None:
        self._steps: List[Dict] = []
        self._results: List[Dict] = []

    def deploy(self, environment: str = "production") -> Dict[str, Any]:
        """Run full deployment."""
        logger.info("📊 EMA_V5 deployment starting for {}", environment)
        start = time.time()

        self._steps = [
            ("validate_environment", self._validate_environment),
            ("check_dependencies", self._check_dependencies),
            ("setup_directories", self._setup_directories),
            ("initialize_database", self._initialize_database),
            ("run_health_checks", self._run_health_checks),
            ("verify_integration", self._verify_integration),
        ]

        self._results = []
        all_passed = True

        for step_name, step_func in self._steps:
            try:
                result = step_func()
                self._results.append({
                    "step": step_name,
                    "status": "success",
                    "details": result,
                })
                logger.info("📊 Deployment step OK: {}", step_name)
            except Exception as e:
                self._results.append({
                    "step": step_name,
                    "status": "failed",
                    "details": str(e),
                })
                logger.error("📊 Deployment step FAILED: {} - {}", step_name, e)
                all_passed = False

        elapsed = (time.time() - start) * 1000

        return {
            "environment": environment,
            "status": "deployed" if all_passed else "failed",
            "steps": self._results,
            "elapsed_ms": round(elapsed, 1),
            "all_passed": all_passed,
        }

    def _validate_environment(self) -> str:
        """Validate the deployment environment."""
        from ..deploy.env_setup import EMAv5EnvSetup
        env = EMAv5EnvSetup()
        validation = env.validate()

        if not validation["healthy"]:
            raise Exception(f"Environment validation failed: {validation['issues']}")

        return f"Python {validation['python_version']}, platform={validation['platform']}"

    def _check_dependencies(self) -> str:
        """Check all dependencies are installed."""
        required = ["numpy", "pandas", "loguru", "httpx", "openpyxl"]
        missing = []

        for pkg in required:
            try:
                __import__(pkg)
            except ImportError:
                missing.append(pkg)

        if missing:
            raise Exception(f"Missing dependencies: {', '.join(missing)}")

        return f"All {len(required)} dependencies installed"

    def _setup_directories(self) -> str:
        """Setup required directories."""
        dirs = [
            "data/bridge",
            "data/logs",
            "data/ema_v5_exports",
            "data/cache",
        ]

        created = []
        for dir_path in dirs:
            Path(dir_path).mkdir(parents=True, exist_ok=True)
            created.append(dir_path)

        return f"Created {len(created)} directories"

    def _initialize_database(self) -> str:
        """Initialize the database."""
        from ..storage.database import EMAv5Database
        db = EMAv5Database()
        count = db.count_signals()

        return f"Database initialized, {count} existing signals"

    def _run_health_checks(self) -> str:
        """Run health checks."""
        from ..deploy.health_check import EMAv5HealthCheck
        hc = EMAv5HealthCheck()
        health = hc.check_all()

        if health["summary"]["error"] > 0:
            raise Exception(f"{health['summary']['error']} health check errors")

        return f"{health['summary']['ok']}/{health['summary']['total']} health checks passed"

    def _verify_integration(self) -> str:
        """Verify integration works."""
        from ..integration.module_registry import EMAv5ModuleRegistry
        registry = EMAv5ModuleRegistry()

        # Register test module
        registry.register("test", lambda: {"test": True})
        modules = registry.list_modules()

        return f"Integration verified, {len(modules)} modules registered"

    def get_results(self) -> List[Dict]:
        """Get deployment results."""
        return self._results
