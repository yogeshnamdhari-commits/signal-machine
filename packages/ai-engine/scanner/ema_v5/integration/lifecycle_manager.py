"""
EMA_V5 Lifecycle Manager — Manages the complete lifecycle of all modules.
Start, stop, restart, health checks, and graceful shutdown.
"""
from __future__ import annotations

import signal
import time
from typing import Any, Dict, List, Optional

from loguru import logger

from .module_registry import EMAv5ModuleRegistry


class EMAv5LifecycleManager:
    """Manages the complete lifecycle of EMA_V5 modules."""

    def __init__(self, registry: Optional[EMAv5ModuleRegistry] = None) -> None:
        self._registry = registry or EMAv5ModuleRegistry()
        self._start_time: Optional[float] = None
        self._running = False
        self._shutdown_hooks: List[callable] = []

    def start(self) -> Dict[str, Any]:
        """Start all EMA_V5 modules."""
        if self._running:
            return {"status": "already_running"}

        logger.info("📊 EMA_V5 Lifecycle: starting all modules")
        self._start_time = time.time()

        # Register all modules
        self._register_modules()

        # Initialize all modules
        results = self._registry.initialize_all()
        failed = [name for name, ok in results.items() if not ok]

        if failed:
            logger.warning("EMAv5: {} modules failed to initialize", len(failed))

        self._running = True

        # Register shutdown handler
        try:
            signal.signal(signal.SIGTERM, self._signal_handler)
            signal.signal(signal.SIGINT, self._signal_handler)
        except (OSError, ValueError):
            pass  # Can't set signal handler in non-main thread

        logger.info("📊 EMA_V5 Lifecycle: started successfully")

        return {
            "status": "started",
            "modules_initialized": sum(results.values()),
            "modules_failed": len(failed),
            "failed_modules": failed,
        }

    def stop(self) -> Dict[str, Any]:
        """Stop all modules gracefully."""
        if not self._running:
            return {"status": "already_stopped"}

        logger.info("📊 EMA_V5 Lifecycle: stopping all modules")

        # Run shutdown hooks
        for hook in self._shutdown_hooks:
            try:
                hook()
            except Exception as e:
                logger.error("EMAv5 shutdown hook error: {}", e)

        # Stop all modules
        self._registry.stop_all()
        self._running = False

        uptime = time.time() - self._start_time if self._start_time else 0
        logger.info("📊 EMA_V5 Lifecycle: stopped (uptime: {:.1f}s)", uptime)

        return {
            "status": "stopped",
            "uptime": round(uptime, 1),
        }

    def restart(self) -> Dict[str, Any]:
        """Restart all modules."""
        self.stop()
        time.sleep(1)
        return self.start()

    def register_shutdown_hook(self, hook: callable) -> None:
        """Register a shutdown hook."""
        self._shutdown_hooks.append(hook)

    def _signal_handler(self, signum: int, frame) -> None:
        """Handle shutdown signals."""
        logger.info("📊 EMA_V5 Lifecycle: received signal {}", signum)
        self.stop()

    def _register_modules(self) -> None:
        """Register all EMA_V5 modules."""
        # Core modules
        self._registry.register(
            "scanner",
            lambda: __import__("scanner.ema_v5.scanner", fromlist=["EMAv5Scanner"]).EMAv5Scanner(),
            description="Main EMA_V5 scanner",
        )

        # Storage modules
        self._registry.register(
            "database",
            lambda: __import__("scanner.ema_v5.storage.database", fromlist=["EMAv5Database"]).EMAv5Database(),
            description="SQLite storage",
        )

        self._registry.register(
            "history",
            lambda: __import__("scanner.ema_v5.storage.history", fromlist=["EMAv5History"]).EMAv5History(),
            dependencies={"database"},
            description="Audit trail",
        )

        # Verification modules
        self._registry.register(
            "verifier",
            lambda: __import__("scanner.ema_v5.verification.verifier", fromlist=["EMAv5Verifier"]).EMAv5Verifier(),
            description="Signal verification",
        )

        # Analytics modules
        self._registry.register(
            "performance",
            lambda: __import__("scanner.ema_v5.performance.performance_report", fromlist=["EMAv5PerformanceReport"]).EMAv5PerformanceReport(),
            dependencies={"database"},
            description="Performance analytics",
        )

        # Security modules
        self._registry.register(
            "security",
            lambda: __import__("scanner.ema_v5.security.security_monitor", fromlist=["EMAv5SecurityMonitor"]).EMAv5SecurityMonitor(),
            description="Security monitoring",
        )

        # Logging modules
        self._registry.register(
            "audit_logger",
            lambda: __import__("scanner.ema_v5.security.audit_logger", fromlist=["EMAv5AuditLogger"]).EMAv5AuditLogger(),
            description="Audit logging",
        )

        # Gateway modules
        self._registry.register(
            "api_server",
            lambda: __import__("scanner.ema_v5.gateway.api_server", fromlist=["EMAv5APIServer"]).EMAv5APIServer(),
            description="REST API server",
        )

    def get_status(self) -> Dict[str, Any]:
        """Get lifecycle status."""
        uptime = time.time() - self._start_time if self._start_time else 0

        return {
            "running": self._running,
            "uptime": round(uptime, 1),
            "modules": self._registry.get_status(),
            "shutdown_hooks": len(self._shutdown_hooks),
        }
