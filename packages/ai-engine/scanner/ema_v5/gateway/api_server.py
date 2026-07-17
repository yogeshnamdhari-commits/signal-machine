"""
EMA_V5 API Server — REST API endpoints for EMA_V5 strategy.
Isolated from existing API servers.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger


class EMAv5APIServer:
    """REST API server for EMA_V5 strategy."""

    def __init__(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        self.host = host
        self.port = port
        self._routes: Dict[str, Dict] = {}
        self._middleware: List[Dict] = []
        self._register_routes()

    def _register_routes(self) -> None:
        """Register all API routes."""
        # Signal endpoints
        self._routes["GET /api/v1/signals"] = {
            "handler": self._get_signals,
            "description": "Get all EMA_V5 signals",
            "auth_required": True,
        }
        self._routes["GET /api/v1/signals/{symbol}"] = {
            "handler": self._get_signals_by_symbol,
            "description": "Get signals for a specific symbol",
            "auth_required": True,
        }
        self._routes["GET /api/v1/signals/latest"] = {
            "handler": self._get_latest_signal,
            "description": "Get the latest signal",
            "auth_required": True,
        }

        # Status endpoints
        self._routes["GET /api/v1/status"] = {
            "handler": self._get_status,
            "description": "Get scanner status",
            "auth_required": False,
        }
        self._routes["GET /api/v1/health"] = {
            "handler": self._get_health,
            "description": "Health check endpoint",
            "auth_required": False,
        }

        # Analytics endpoints
        self._routes["GET /api/v1/analytics/performance"] = {
            "handler": self._get_performance,
            "description": "Get performance metrics",
            "auth_required": True,
        }
        self._routes["GET /api/v1/analytics/risk"] = {
            "handler": self._get_risk_metrics,
            "description": "Get risk metrics",
            "auth_required": True,
        }

        # Verification endpoints
        self._routes["POST /api/v1/verify"] = {
            "handler": self._verify_signal,
            "description": "Verify a signal",
            "auth_required": True,
        }

        # Export endpoints
        self._routes["GET /api/v1/export/csv"] = {
            "handler": self._export_csv,
            "description": "Export signals to CSV",
            "auth_required": True,
        }
        self._routes["GET /api/v1/export/json"] = {
            "handler": self._export_json,
            "description": "Export signals to JSON",
            "auth_required": True,
        }

    async def handle_request(self, method: str, path: str,
                            headers: Optional[Dict] = None,
                            body: Optional[Dict] = None,
                            query: Optional[Dict] = None) -> Dict[str, Any]:
        """Handle an API request."""
        route_key = f"{method} {path}"

        # Check route exists
        if route_key not in self._routes:
            return {
                "status": 404,
                "error": "Not Found",
                "message": f"Route {method} {path} not found",
            }

        route = self._routes[route_key]

        # Check authentication
        if route.get("auth_required", False):
            auth_result = self._authenticate(headers)
            if not auth_result["authenticated"]:
                return {
                    "status": 401,
                    "error": "Unauthorized",
                    "message": auth_result.get("message", "Authentication required"),
                }

        # Execute handler
        try:
            result = await route["handler"](body=body, query=query)
            return {
                "status": 200,
                "data": result,
                "timestamp": time.time(),
            }
        except Exception as e:
            logger.error("API error: {}", e)
            return {
                "status": 500,
                "error": "Internal Server Error",
                "message": str(e),
            }

    def _authenticate(self, headers: Optional[Dict] = None) -> Dict[str, Any]:
        """Authenticate a request."""
        if not headers:
            return {"authenticated": False, "message": "No headers provided"}

        api_key = headers.get("X-API-Key") or headers.get("Authorization", "").replace("Bearer ", "")
        if not api_key:
            return {"authenticated": False, "message": "API key required"}

        # Simple key validation (in production, use proper auth)
        from .auth import EMAv5Auth
        auth = EMAv5Auth()
        return auth.validate_key(api_key)

    # ── Signal Endpoints ────────────────────────────────────────

    async def _get_signals(self, body: Optional[Dict] = None,
                          query: Optional[Dict] = None) -> Dict[str, Any]:
        """Get all signals."""
        from ..storage.database import EMAv5Database
        db = EMAv5Database()
        signals = db.get_all_signals()
        return {
            "count": len(signals),
            "signals": signals,
        }

    async def _get_signals_by_symbol(self, body: Optional[Dict] = None,
                                    query: Optional[Dict] = None) -> Dict[str, Any]:
        """Get signals by symbol."""
        symbol = query.get("symbol", "") if query else ""
        from ..storage.database import EMAv5Database
        db = EMAv5Database()
        signals = db.get_signals(symbol=symbol)
        return {
            "symbol": symbol,
            "count": len(signals),
            "signals": signals,
        }

    async def _get_latest_signal(self, body: Optional[Dict] = None,
                                query: Optional[Dict] = None) -> Dict[str, Any]:
        """Get latest signal."""
        from ..storage.database import EMAv5Database
        db = EMAv5Database()
        signals = db.get_signals(limit=1)
        return {
            "signal": signals[0] if signals else None,
        }

    # ── Status Endpoints ────────────────────────────────────────

    async def _get_status(self, body: Optional[Dict] = None,
                         query: Optional[Dict] = None) -> Dict[str, Any]:
        """Get scanner status."""
        from ..scanner import EMAv5Scanner
        scanner = EMAv5Scanner()
        stats = scanner.get_stats()

        from ..storage.database import EMAv5Database
        db = EMAv5Database()

        return {
            "version": "1.0.0",
            "scanner": stats,
            "database": {
                "signals": db.count_signals(),
            },
            "uptime": stats.get("uptime_sec", 0),
        }

    async def _get_health(self, body: Optional[Dict] = None,
                         query: Optional[Dict] = None) -> Dict[str, Any]:
        """Health check."""
        from ..deploy.health_check import EMAv5HealthCheck
        hc = EMAv5HealthCheck()
        health = hc.check_all()
        return {
            "healthy": health["healthy"],
            "checks": health["summary"],
        }

    # ── Analytics Endpoints ─────────────────────────────────────

    async def _get_performance(self, body: Optional[Dict] = None,
                              query: Optional[Dict] = None) -> Dict[str, Any]:
        """Get performance metrics."""
        from ..analytics.performance_calculator import PerformanceCalculator
        pc = PerformanceCalculator()
        metrics = pc.compute_all()
        return metrics

    async def _get_risk_metrics(self, body: Optional[Dict] = None,
                               query: Optional[Dict] = None) -> Dict[str, Any]:
        """Get risk metrics."""
        from ..analytics.risk_metrics import RiskMetrics
        rm = RiskMetrics()
        metrics = rm.compute_all()
        return metrics

    # ── Verification Endpoints ──────────────────────────────────

    async def _verify_signal(self, body: Optional[Dict] = None,
                            query: Optional[Dict] = None) -> Dict[str, Any]:
        """Verify a signal."""
        if not body:
            return {"error": "Request body required"}

        from ..verification.verifier import EMAv5Verifier
        v = EMAv5Verifier()

        # Extract data from body
        signal = body.get("signal", {})
        ema = body.get("ema_data", {})
        regime = body.get("regime_eval", {})
        trend = body.get("trend_eval", {})
        pullback = body.get("pullback_eval", {})
        candle = body.get("candle_eval", {})
        volume = body.get("volume_eval", {})
        confidence = body.get("confidence_eval", {})

        verdict, diag = v.verify(signal, ema, regime, trend, pullback, candle, volume, confidence)

        return {
            "verdict": verdict,
            "diagnostics": diag.to_dict(),
        }

    # ── Export Endpoints ────────────────────────────────────────

    async def _export_csv(self, body: Optional[Dict] = None,
                         query: Optional[Dict] = None) -> Dict[str, Any]:
        """Export signals to CSV."""
        from ..storage.exporter import EMAv5Exporter
        from ..storage.database import EMAv5Database
        db = EMAv5Database()
        exporter = EMAv5Exporter(db=db)
        path = exporter.export_csv()
        return {"file": path, "format": "csv"}

    async def _export_json(self, body: Optional[Dict] = None,
                          query: Optional[Dict] = None) -> Dict[str, Any]:
        """Export signals to JSON."""
        from ..storage.exporter import EMAv5Exporter
        from ..storage.database import EMAv5Database
        db = EMAv5Database()
        exporter = EMAv5Exporter(db=db)
        path = exporter.export_json()
        return {"file": path, "format": "json"}

    def get_routes(self) -> List[Dict]:
        """Get all registered routes."""
        return [
            {
                "method": key.split(" ")[0],
                "path": key.split(" ")[1],
                "description": route.get("description", ""),
                "auth_required": route.get("auth_required", False),
            }
            for key, route in self._routes.items()
        ]
