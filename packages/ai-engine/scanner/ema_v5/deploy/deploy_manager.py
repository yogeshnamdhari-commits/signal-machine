"""
EMA_V5 Deploy Manager — Manages deployment lifecycle.
Start, stop, restart, status, and version management.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger


class EMAv5DeployManager:
    """Manages EMA_V5 deployment lifecycle."""

    VERSION = "1.0.0"

    def __init__(self) -> None:
        self._start_time: Optional[float] = None
        self._running = False
        self._version = self.VERSION

    def start(self) -> Dict[str, Any]:
        """Start the EMA_V5 system."""
        if self._running:
            return {"status": "already_running", "uptime": self._get_uptime()}

        logger.info("📊 EMA_V5 Deploy: starting v{}", self._version)

        # Validate environment
        from .env_setup import EMAv5EnvSetup
        env = EMAv5EnvSetup()
        validation = env.validate()

        if not validation["healthy"]:
            return {
                "status": "failed",
                "reason": "Environment validation failed",
                "issues": validation["issues"],
            }

        # Setup directories
        env.setup_directories()

        # Run health check
        from .health_check import EMAv5HealthCheck
        hc = EMAv5HealthCheck()
        health = hc.check_all()

        self._start_time = time.time()
        self._running = True

        logger.info("📊 EMA_V5 Deploy: started successfully")

        return {
            "status": "started",
            "version": self._version,
            "health": health["summary"],
        }

    def stop(self) -> Dict[str, Any]:
        """Stop the EMA_V5 system."""
        if not self._running:
            return {"status": "already_stopped"}

        logger.info("📊 EMA_V5 Deploy: stopping")
        self._running = False
        return {"status": "stopped"}

    def restart(self) -> Dict[str, Any]:
        """Restart the EMA_V5 system."""
        self.stop()
        time.sleep(1)
        return self.start()

    def status(self) -> Dict[str, Any]:
        """Get deployment status."""
        from .health_check import EMAv5HealthCheck
        hc = EMAv5HealthCheck()
        health = hc.check_all()

        return {
            "version": self._version,
            "running": self._running,
            "uptime": self._get_uptime(),
            "health": health["summary"],
            "healthy": health["healthy"],
        }

    def version(self) -> Dict[str, Any]:
        """Get version information."""
        return {
            "version": self._version,
            "modules": self._get_module_versions(),
        }

    def _get_uptime(self) -> str:
        """Get formatted uptime."""
        if not self._start_time:
            return "not started"
        uptime = time.time() - self._start_time
        if uptime > 3600:
            return f"{uptime / 3600:.1f}h"
        elif uptime > 60:
            return f"{uptime / 60:.0f}m"
        else:
            return f"{uptime:.0f}s"

    def _get_module_versions(self) -> Dict[str, str]:
        """Get versions of all modules."""
        modules = {}
        try:
            from ..storage.database import EMAv5Database
            modules["storage"] = "1.0.0"
        except Exception:
            modules["storage"] = "error"

        try:
            from ..verification.verifier import EMAv5Verifier
            modules["verification"] = "1.0.0"
        except Exception:
            modules["verification"] = "error"

        try:
            from ..analytics.performance_calculator import PerformanceCalculator
            modules["analytics"] = "1.0.0"
        except Exception:
            modules["analytics"] = "error"

        return modules
