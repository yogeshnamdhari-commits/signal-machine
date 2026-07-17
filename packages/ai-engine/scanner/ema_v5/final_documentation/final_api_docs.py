"""
EMA_V5 Final API Documentation — Complete API documentation.
Isolated from existing API documentation systems.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from loguru import logger


class EMAv5FinalAPIDocs:
    """Generates complete API documentation."""

    def generate(self) -> Dict[str, Any]:
        """Generate complete API reference."""
        return {
            "title": "EMA V5 API Reference",
            "version": "1.0.0",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "endpoints": self._endpoints(),
            "models": self._models(),
            "errors": self._errors(),
            "authentication": self._authentication(),
        }

    def _endpoints(self) -> Dict[str, Any]:
        """API endpoints documentation."""
        return {
            "GET /api/v1/signals": {
                "description": "Get all EMA_V5 signals",
                "auth_required": True,
                "response": {
                    "count": "int",
                    "signals": "List[Signal]",
                },
            },
            "GET /api/v1/signals/{symbol}": {
                "description": "Get signals for a specific symbol",
                "auth_required": True,
                "params": {"symbol": "string - Symbol name"},
                "response": {
                    "symbol": "string",
                    "count": "int",
                    "signals": "List[Signal]",
                },
            },
            "GET /api/v1/signals/latest": {
                "description": "Get the latest signal",
                "auth_required": True,
                "response": {
                    "signal": "Signal or null",
                },
            },
            "GET /api/v1/status": {
                "description": "Get scanner status",
                "auth_required": False,
                "response": {
                    "version": "string",
                    "scanner": "Dict",
                    "database": "Dict",
                    "uptime": "float",
                },
            },
            "GET /api/v1/health": {
                "description": "Health check endpoint",
                "auth_required": False,
                "response": {
                    "healthy": "bool",
                    "checks": "Dict",
                },
            },
            "GET /api/v1/analytics/performance": {
                "description": "Get performance metrics",
                "auth_required": True,
                "response": "PerformanceMetrics",
            },
            "GET /api/v1/analytics/risk": {
                "description": "Get risk metrics",
                "auth_required": True,
                "response": "RiskMetrics",
            },
            "POST /api/v1/verify": {
                "description": "Verify a signal",
                "auth_required": True,
                "body": {
                    "signal": "Signal",
                    "ema_data": "Dict",
                    "regime_eval": "Dict",
                    "trend_eval": "Dict",
                    "pullback_eval": "Dict",
                    "candle_eval": "Dict",
                    "volume_eval": "Dict",
                    "confidence_eval": "Dict",
                },
                "response": {
                    "verdict": "string (PASS/WARNING/FAIL)",
                    "diagnostics": "Diagnostics",
                },
            },
            "GET /api/v1/export/csv": {
                "description": "Export signals to CSV",
                "auth_required": True,
                "response": {
                    "file": "string",
                    "format": "csv",
                },
            },
            "GET /api/v1/export/json": {
                "description": "Export signals to JSON",
                "auth_required": True,
                "response": {
                    "file": "string",
                    "format": "json",
                },
            },
        }

    def _models(self) -> Dict[str, Any]:
        """Data models documentation."""
        return {
            "Signal": {
                "uuid": "string - Unique identifier",
                "symbol": "string - Trading symbol",
                "side": "string - LONG or SHORT",
                "entry": "float - Entry price",
                "stop_loss": "float - Stop loss price",
                "tp1": "float - Take profit 1",
                "tp2": "float - Take profit 2",
                "tp3": "float - Take profit 3",
                "confidence": "float - Confidence score (0-1)",
                "regime": "string - Market regime",
                "timestamp": "float - Unix timestamp",
            },
            "Diagnostics": {
                "signal_uuid": "string",
                "symbol": "string",
                "verdict": "string (PASS/WARNING/FAIL)",
                "checks": "List[Check]",
                "reasons_passed": "List[string]",
                "reasons_failed": "List[string]",
                "confidence_score": "float",
                "execution_time_ms": "float",
            },
            "PerformanceMetrics": {
                "total_trades": "int",
                "win_rate": "float",
                "total_pnl": "float",
                "profit_factor": "float",
                "expectancy": "float",
                "max_drawdown_pct": "float",
                "sharpe_ratio": "float",
            },
            "RiskMetrics": {
                "max_drawdown_usd": "float",
                "max_drawdown_pct": "float",
                "sharpe_ratio": "float",
                "sortino_ratio": "float",
                "calmar_ratio": "float",
                "volatility": "float",
                "kelly_criterion": "float",
            },
        }

    def _errors(self) -> Dict[str, Any]:
        """Error codes documentation."""
        return {
            "400": "Bad Request - Invalid input",
            "401": "Unauthorized - Authentication required",
            "403": "Forbidden - Insufficient permissions",
            "404": "Not Found - Resource not found",
            "429": "Too Many Requests - Rate limit exceeded",
            "500": "Internal Server Error - Server error",
        }

    def _authentication(self) -> Dict[str, Any]:
        """Authentication documentation."""
        return {
            "type": "API Key",
            "header": "X-API-Key",
            "alternative": "Authorization: Bearer <key>",
            "creation": "POST /api/v1/auth/keys",
            "revocation": "DELETE /api/v1/auth/keys/{key_id}",
            "rate_limit": "100 requests per minute",
        }
