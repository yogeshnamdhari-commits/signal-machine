"""
EMA_V5 Unified Entry — Single entry point for the entire EMA_V5 system.
Provides a unified interface to all modules and services.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger

from .lifecycle_manager import EMAv5LifecycleManager
from .module_registry import EMAv5ModuleRegistry


class EMAv5UnifiedEntry:
    """Unified entry point for the EMA_V5 system."""

    VERSION = "1.0.0"

    def __init__(self) -> None:
        self._registry = EMAv5ModuleRegistry()
        self._lifecycle = EMAv5LifecycleManager(self._registry)
        self._initialized = False

    def initialize(self) -> Dict[str, Any]:
        """Initialize the entire EMA_V5 system."""
        if self._initialized:
            return {"status": "already_initialized"}

        logger.info("📊 EMA_V5 Unified Entry: initializing v{}", self.VERSION)

        # Start lifecycle
        result = self._lifecycle.start()
        self._initialized = True

        return {
            "status": "initialized",
            "version": self.VERSION,
            "modules": result.get("modules_initialized", 0),
            "failed": result.get("modules_failed", 0),
        }

    def shutdown(self) -> Dict[str, Any]:
        """Shutdown the entire EMA_V5 system."""
        result = self._lifecycle.stop()
        self._initialized = False
        return result

    # ── Scanner Interface ───────────────────────────────────────

    async def scan_symbol(self, symbol: str, market_data: Dict) -> Optional[Dict]:
        """Scan a symbol for signals."""
        scanner = self._registry.get("scanner")
        return await scanner.evaluate(symbol, market_data)

    def get_signals(self, limit: int = 100) -> List[Dict]:
        """Get recent signals."""
        db = self._registry.get("database")
        return db.get_signals(limit=limit)

    # ── Verification Interface ──────────────────────────────────

    def verify_signal(self, signal: Dict, ema_data: Dict, regime: Dict,
                     trend: Dict, pullback: Dict, candle: Dict,
                     volume: Dict, confidence: Dict) -> Dict:
        """Verify a signal."""
        verifier = self._registry.get("verifier")
        verdict, diag = verifier.verify(
            signal, ema_data, regime, trend, pullback, candle, volume, confidence
        )
        return {
            "verdict": verdict,
            "diagnostics": diag.to_dict(),
        }

    # ── Analytics Interface ─────────────────────────────────────

    def get_performance(self) -> Dict[str, Any]:
        """Get performance metrics."""
        perf = self._registry.get("performance")
        return perf.quick_status()

    # ── Security Interface ──────────────────────────────────────

    def check_security(self, source: str, path: str) -> Dict[str, Any]:
        """Check security for a request."""
        monitor = self._registry.get("security")
        return monitor.check_request(source, path)

    # ── Status Interface ────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Get complete system status."""
        lifecycle_status = self._lifecycle.get_status()

        return {
            "version": self.VERSION,
            "initialized": self._initialized,
            "running": lifecycle_status["running"],
            "uptime": lifecycle_status["uptime"],
            "modules": lifecycle_status["modules"],
        }

    def get_health(self) -> Dict[str, Any]:
        """Get system health."""
        from ..deploy.health_check import EMAv5HealthCheck
        hc = EMAv5HealthCheck()
        return hc.check_all()

    def version(self) -> Dict[str, Any]:
        """Get version information."""
        return {
            "version": self.VERSION,
            "modules": self._registry.list_modules(),
            "module_count": len(self._registry.list_modules()),
        }


# Singleton instance
_instance: Optional[EMAv5UnifiedEntry] = None


def get_instance() -> EMAv5UnifiedEntry:
    """Get the singleton instance."""
    global _instance
    if _instance is None:
        _instance = EMAv5UnifiedEntry()
    return _instance


def initialize() -> Dict[str, Any]:
    """Initialize the EMA_V5 system."""
    return get_instance().initialize()


def shutdown() -> Dict[str, Any]:
    """Shutdown the EMA_V5 system."""
    return get_instance().shutdown()
