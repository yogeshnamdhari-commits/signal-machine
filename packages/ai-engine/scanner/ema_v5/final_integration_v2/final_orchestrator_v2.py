"""
EMA_V5 Final Orchestrator v2 — Orchestrates all modules for production.
Isolated from existing orchestration systems.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger


class EMAv5FinalOrchestratorV2:
    """Orchestrates all EMA_V5 modules for production."""

    def __init__(self) -> None:
        self._modules: Dict[str, Any] = {}
        self._initialized = False
        self._start_time: Optional[float] = None

    def initialize(self) -> Dict[str, Any]:
        """Initialize all modules."""
        if self._initialized:
            return {"status": "already_initialized"}

        logger.info("📊 EMA_V5 Final Orchestrator v2: initializing")
        self._start_time = time.time()

        try:
            self._initialize_core()
            self._initialize_storage()
            self._initialize_verification()
            self._initialize_analytics()
            self._initialize_execution()
            self._initialize_gateway()
            self._initialize_security()
            self._initialize_logging()
            self._initialize_monitoring()

            self._initialized = True
            logger.info("📊 EMA_V5 Final Orchestrator v2: initialized successfully")

            return {
                "status": "initialized",
                "modules": len(self._modules),
            }
        except Exception as e:
            logger.error("EMAv5 Final Orchestrator v2 init failed: {}", e)
            return {
                "status": "failed",
                "error": str(e),
            }

    def _initialize_core(self) -> None:
        """Initialize core modules."""
        from ..scanner import EMAv5Scanner
        from ..config import ema_v5_config

        self._modules["scanner"] = EMAv5Scanner()
        self._modules["config"] = ema_v5_config

    def _initialize_storage(self) -> None:
        """Initialize storage modules."""
        from ..storage.database import EMAv5Database
        from ..storage.serializer import EMAv5Serializer
        from ..storage.history import EMAv5History

        db = EMAv5Database()
        self._modules["database"] = db
        self._modules["serializer"] = EMAv5Serializer()
        self._modules["history"] = EMAv5History(db=db)

    def _initialize_verification(self) -> None:
        """Initialize verification modules."""
        from ..verification.verifier import EMAv5Verifier
        from ..verification.statistics import EMAv5Statistics
        from ..verification.quality import EMAv5Quality

        verifier = EMAv5Verifier()
        self._modules["verifier"] = verifier
        self._modules["statistics"] = EMAv5Statistics(verifier.get_diagnostics())
        self._modules["quality"] = EMAv5Quality()

    def _initialize_analytics(self) -> None:
        """Initialize analytics modules."""
        from ..analytics.performance_calculator import PerformanceCalculator
        from ..analytics.risk_metrics import RiskMetrics
        from ..analytics.equity_curve import EquityCurve

        self._modules["performance_calc"] = PerformanceCalculator()
        self._modules["risk_metrics"] = RiskMetrics()
        self._modules["equity_curve"] = EquityCurve()

    def _initialize_execution(self) -> None:
        """Initialize execution modules."""
        from ..execution.paper_trader import EMAv5PaperTrader
        from ..execution.order_manager import EMAv5OrderManager
        from ..execution.position_manager import EMAv5PositionManager

        self._modules["paper_trader"] = EMAv5PaperTrader()
        self._modules["order_manager"] = EMAv5OrderManager()
        self._modules["position_manager"] = EMAv5PositionManager()

    def _initialize_gateway(self) -> None:
        """Initialize gateway modules."""
        from ..gateway.api_server import EMAv5APIServer
        from ..gateway.auth import EMAv5Auth
        from ..gateway.rate_limiter import EMAv5RateLimiter

        self._modules["api_server"] = EMAv5APIServer()
        self._modules["auth"] = EMAv5Auth()
        self._modules["rate_limiter"] = EMAv5RateLimiter()

    def _initialize_security(self) -> None:
        """Initialize security modules."""
        from ..security.input_sanitizer import EMAv5InputSanitizer
        from ..security.sql_guard import EMAv5SQLGuard
        from ..security.audit_logger import EMAv5AuditLogger

        self._modules["sanitizer"] = EMAv5InputSanitizer()
        self._modules["sql_guard"] = EMAv5SQLGuard()
        self._modules["audit_logger"] = EMAv5AuditLogger()

    def _initialize_logging(self) -> None:
        """Initialize logging modules."""
        from ..logging.structured_logger import EMAv5StructuredLogger
        self._modules["structured_logger"] = EMAv5StructuredLogger()

    def _initialize_monitoring(self) -> None:
        """Initialize monitoring modules."""
        from ..deploy.health_check import EMAv5HealthCheck
        self._modules["health_check"] = EMAv5HealthCheck()

    def get_module(self, name: str) -> Any:
        """Get a module by name."""
        return self._modules.get(name)

    def get_status(self) -> Dict[str, Any]:
        """Get orchestrator status."""
        uptime = time.time() - self._start_time if self._start_time else 0

        return {
            "initialized": self._initialized,
            "modules": len(self._modules),
            "module_names": list(self._modules.keys()),
            "uptime": round(uptime, 1),
        }

    def shutdown(self) -> Dict[str, Any]:
        """Shutdown all modules."""
        self._modules.clear()
        self._initialized = False

        return {
            "status": "shutdown",
            "modules_cleared": True,
        }
