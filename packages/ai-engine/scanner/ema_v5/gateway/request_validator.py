"""
EMA_V5 Request Validator — Validates API request data.
Isolated from existing validation systems.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from loguru import logger


class EMAv5RequestValidator:
    """Validates API request data."""

    VALID_SIDES = {"LONG", "SHORT"}
    VALID_REGIMES = {"BUY_MODE", "SELL_MODE", "NO_TREND"}
    VALID_VERDICTS = {"PASS", "WARNING", "FAIL"}

    def validate_signal(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a signal request."""
        errors = []

        # Required fields
        required = ["symbol", "side", "entry", "sl"]
        for field in required:
            if field not in data:
                errors.append(f"Missing required field: {field}")

        if errors:
            return {"valid": False, "errors": errors}

        # Validate symbol
        symbol = data.get("symbol", "")
        if not isinstance(symbol, str) or len(symbol) < 3:
            errors.append("Invalid symbol format")

        # Validate side
        side = data.get("side", "")
        if side not in self.VALID_SIDES:
            errors.append(f"Invalid side: {side}. Must be {self.VALID_SIDES}")

        # Validate prices
        for field in ["entry", "sl", "take_profit_1", "take_profit_2", "take_profit_3"]:
            value = data.get(field)
            if value is not None and (not isinstance(value, (int, float)) or value <= 0):
                errors.append(f"Invalid {field}: must be positive number")

        # Validate confidence
        confidence = data.get("confidence")
        if confidence is not None:
            if not isinstance(confidence, (int, float)) or not (0 <= confidence <= 1):
                errors.append("Invalid confidence: must be between 0 and 1")

        # Validate regime
        regime = data.get("regime")
        if regime is not None and regime not in self.VALID_REGIMES:
            errors.append(f"Invalid regime: {regime}")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
        }

    def validate_verification_request(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a verification request."""
        errors = []

        required = ["signal", "ema_data", "regime_eval", "trend_eval",
                    "pullback_eval", "candle_eval", "volume_eval", "confidence_eval"]
        for field in required:
            if field not in data:
                errors.append(f"Missing required field: {field}")

        if errors:
            return {"valid": False, "errors": errors}

        # Validate signal sub-fields
        signal = data.get("signal", {})
        signal_errors = self.validate_signal(signal)
        if not signal_errors["valid"]:
            errors.extend([f"signal.{e}" for e in signal_errors["errors"]])

        return {
            "valid": len(errors) == 0,
            "errors": errors,
        }

    def validate_date_range(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a date range request."""
        errors = []

        start = data.get("start_date")
        end = data.get("end_date")

        if start and not isinstance(start, str):
            errors.append("start_date must be a string (YYYY-MM-DD)")
        if end and not isinstance(end, str):
            errors.append("end_date must be a string (YYYY-MM-DD)")

        if start and end:
            try:
                from datetime import datetime
                s = datetime.strptime(start, "%Y-%m-%d")
                e = datetime.strptime(end, "%Y-%m-%d")
                if s > e:
                    errors.append("start_date must be before end_date")
            except ValueError:
                errors.append("Invalid date format (use YYYY-MM-DD)")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
        }

    def validate_export_request(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate an export request."""
        errors = []

        format_type = data.get("format", "csv")
        if format_type not in ("csv", "json", "excel"):
            errors.append(f"Invalid format: {format_type}. Must be csv, json, or excel")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
        }

    def sanitize_input(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize input data."""
        sanitized = {}
        for key, value in data.items():
            if isinstance(value, str):
                # Strip whitespace
                sanitized[key] = value.strip()
            elif isinstance(value, (int, float)):
                sanitized[key] = value
            elif isinstance(value, dict):
                sanitized[key] = self.sanitize_input(value)
            elif isinstance(value, list):
                sanitized[key] = [
                    self.sanitize_input(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                sanitized[key] = value
        return sanitized
