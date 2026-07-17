"""
EMA_V5 Response Formatter — Formats API responses consistently.
Isolated from existing response formatters.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger


class EMAv5ResponseFormatter:
    """Formats API responses consistently."""

    @staticmethod
    def success(data: Any, message: str = "Success") -> Dict[str, Any]:
        """Format a success response."""
        return {
            "status": "success",
            "message": message,
            "data": data,
            "timestamp": time.time(),
        }

    @staticmethod
    def error(message: str, code: str = "ERROR", details: Optional[Dict] = None) -> Dict[str, Any]:
        """Format an error response."""
        response = {
            "status": "error",
            "message": message,
            "code": code,
            "timestamp": time.time(),
        }
        if details:
            response["details"] = details
        return response

    @staticmethod
    def paginated(data: List[Any], total: int, page: int = 1,
                  per_page: int = 50) -> Dict[str, Any]:
        """Format a paginated response."""
        return {
            "status": "success",
            "data": data,
            "pagination": {
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": (total + per_page - 1) // per_page,
                "has_next": page * per_page < total,
                "has_prev": page > 1,
            },
            "timestamp": time.time(),
        }

    @staticmethod
    def signal(signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """Format a signal response."""
        return {
            "status": "success",
            "data": {
                "uuid": signal_data.get("uuid", ""),
                "symbol": signal_data.get("symbol", ""),
                "side": signal_data.get("side", ""),
                "entry": signal_data.get("entry", 0),
                "stop_loss": signal_data.get("stop_loss", 0),
                "take_profit_1": signal_data.get("tp1", 0),
                "take_profit_2": signal_data.get("tp2", 0),
                "take_profit_3": signal_data.get("tp3", 0),
                "confidence": signal_data.get("confidence", 0),
                "regime": signal_data.get("regime", ""),
                "timestamp": signal_data.get("timestamp", 0),
            },
            "timestamp": time.time(),
        }

    @staticmethod
    def signals_list(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Format a signals list response."""
        return {
            "status": "success",
            "data": {
                "count": len(signals),
                "signals": [
                    {
                        "uuid": s.get("uuid", ""),
                        "symbol": s.get("symbol", ""),
                        "side": s.get("side", ""),
                        "entry": s.get("entry", 0),
                        "confidence": s.get("confidence", 0),
                        "regime": s.get("regime", ""),
                        "timestamp": s.get("timestamp", 0),
                    }
                    for s in signals
                ],
            },
            "timestamp": time.time(),
        }

    @staticmethod
    def verification_result(verdict: str, diagnostics: Dict[str, Any]) -> Dict[str, Any]:
        """Format a verification result response."""
        return {
            "status": "success",
            "data": {
                "verdict": verdict,
                "checks": len(diagnostics.get("checks", [])),
                "reasons_passed": diagnostics.get("reasons_passed", []),
                "reasons_failed": diagnostics.get("reasons_failed", []),
                "confidence": diagnostics.get("confidence_score", 0),
                "execution_time_ms": diagnostics.get("execution_time_ms", 0),
            },
            "timestamp": time.time(),
        }

    @staticmethod
    def health(health_data: Dict[str, Any]) -> Dict[str, Any]:
        """Format a health check response."""
        return {
            "status": "success",
            "data": {
                "healthy": health_data.get("healthy", False),
                "checks": health_data.get("checks", {}),
            },
            "timestamp": time.time(),
        }

    @staticmethod
    def status(status_data: Dict[str, Any]) -> Dict[str, Any]:
        """Format a status response."""
        return {
            "status": "success",
            "data": status_data,
            "timestamp": time.time(),
        }

    @staticmethod
    def export_result(file_path: str, format_type: str, count: int) -> Dict[str, Any]:
        """Format an export result response."""
        return {
            "status": "success",
            "data": {
                "file": file_path,
                "format": format_type,
                "count": count,
            },
            "timestamp": time.time(),
        }
