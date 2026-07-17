"""
EMA_V5 Final Unified — Unified interface for all EMA_V5 capabilities.
Isolated from existing unified interfaces.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger

from .final_orchestrator import EMAv5FinalOrchestrator


class EMAv5FinalUnified:
    """Unified interface for all EMA_V5 capabilities."""

    VERSION = "1.0.0"

    def __init__(self) -> None:
        self._orchestrator = EMAv5FinalOrchestrator()
        self._initialized = False

    def initialize(self) -> Dict[str, Any]:
        """Initialize the system."""
        if self._initialized:
            return {"status": "already_initialized"}

        result = self._orchestrator.initialize()
        if result["status"] == "initialized":
            self._initialized = True

        return result

    def shutdown(self) -> Dict[str, Any]:
        """Shutdown the system."""
        result = self._orchestrator.shutdown()
        self._initialized = False
        return result

    # ── Scanner Interface ───────────────────────────────────────

    async def scan_symbol(self, symbol: str, market_data: Dict) -> Optional[Dict]:
        """Scan a symbol for signals."""
        scanner = self._orchestrator.get_module("scanner")
        return await scanner.evaluate(symbol, market_data)

    def get_signals(self, limit: int = 100) -> List[Dict]:
        """Get recent signals."""
        db = self._orchestrator.get_module("database")
        return db.get_signals(limit=limit)

    # ── Verification Interface ──────────────────────────────────

    def verify_signal(self, signal: Dict, ema_data: Dict, regime: Dict,
                     trend: Dict, pullback: Dict, candle: Dict,
                     volume: Dict, confidence: Dict) -> Dict:
        """Verify a signal."""
        verifier = self._orchestrator.get_module("verifier")
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
        pc = self._orchestrator.get_module("performance_calc")
        return pc.compute_all()

    def get_risk_metrics(self) -> Dict[str, Any]:
        """Get risk metrics."""
        rm = self._orchestrator.get_module("risk_metrics")
        return rm.compute_all()

    # ── Execution Interface ─────────────────────────────────────

    def process_signal(self, signal: Dict) -> Dict:
        """Process a signal through paper trading."""
        pt = self._orchestrator.get_module("paper_trader")
        return pt.process_signal(signal)

    # ── Security Interface ──────────────────────────────────────

    def check_security(self, source: str, path: str) -> Dict[str, Any]:
        """Check security for a request."""
        sanitizer = self._orchestrator.get_module("sanitizer")
        sql_check = sanitizer.check_sql_injection(path)
        xss_check = sanitizer.check_xss(path)

        return {
            "safe": sql_check["safe"] and xss_check["safe"],
            "sql_threats": sql_check.get("threats", []),
            "xss_threats": xss_check.get("threats", []),
        }

    # ── Status Interface ────────────────────────────────────────

    def get_status(self) -> Dict[str, Any]:
        """Get complete system status."""
        return {
            "version": self.VERSION,
            "initialized": self._initialized,
            "orchestrator": self._orchestrator.get_status(),
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
            "modules": self._orchestrator.get_status()["module_names"],
        }


# Singleton instance
_instance: Optional[EMAv5FinalUnified] = None


def get_instance() -> EMAv5FinalUnified:
    """Get the singleton instance."""
    global _instance
    if _instance is None:
        _instance = EMAv5FinalUnified()
    return _instance


def initialize() -> Dict[str, Any]:
    """Initialize the EMA_V5 system."""
    return get_instance().initialize()


def shutdown() -> Dict[str, Any]:
    """Shutdown the EMA_V5 system."""
    return get_instance().shutdown()
