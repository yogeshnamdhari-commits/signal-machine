"""
EMA_V5 Input Sanitizer — Sanitizes and validates all user inputs.
Isolated from existing sanitization systems.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Union

from loguru import logger


class EMAv5InputSanitizer:
    """Sanitizes all user inputs to prevent injection and malformed data."""

    # Patterns for dangerous content
    SQL_PATTERNS = [
        r"(\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|EXEC|EXECUTE)\b)",
        r"(--|;|/\*|\*/|xp_)",
        r"(UNION\s+ALL|UNION\s+SELECT)",
        r"(OR\s+1\s*=\s*1|AND\s+1\s*=\s*1)",
    ]

    SCRIPT_PATTERNS = [
        r"<script[^>]*>.*?</script>",
        r"javascript:",
        r"on\w+\s*=",
        r"eval\s*\(",
        r"document\.\w+",
        r"window\.\w+",
    ]

    # Max lengths
    MAX_SYMBOL_LENGTH = 20
    MAX_STRING_LENGTH = 1000
    MAX_NUMBER_VALUE = 1e12

    def sanitize_string(self, value: str, max_length: Optional[int] = None) -> str:
        """Sanitize a string input."""
        if not isinstance(value, str):
            value = str(value)

        # Strip whitespace
        value = value.strip()

        # Truncate
        max_len = max_length or self.MAX_STRING_LENGTH
        value = value[:max_len]

        # Remove null bytes
        value = value.replace("\x00", "")

        return value

    def sanitize_symbol(self, symbol: str) -> str:
        """Sanitize a trading symbol."""
        symbol = self.sanitize_string(symbol, self.MAX_SYMBOL_LENGTH)

        # Only allow alphanumeric and common trading pair characters
        symbol = re.sub(r'[^A-Za-z0-9]', '', symbol)

        return symbol.upper()

    def sanitize_number(self, value: Union[int, float, str],
                       min_val: Optional[float] = None,
                       max_val: Optional[float] = None) -> float:
        """Sanitize a numeric input."""
        try:
            value = float(value)
        except (ValueError, TypeError):
            return 0.0

        # Check for NaN/Inf
        if value != value:  # NaN check
            return 0.0
        if value == float('inf') or value == float('-inf'):
            return 0.0

        # Clamp to max
        if abs(value) > self.MAX_NUMBER_VALUE:
            value = self.MAX_NUMBER_VALUE if value > 0 else -self.MAX_NUMBER_VALUE

        # Apply bounds
        if min_val is not None and value < min_val:
            value = min_val
        if max_val is not None and value > max_val:
            value = max_val

        return value

    def sanitize_dict(self, data: Dict[str, Any], allowed_keys: Optional[List[str]] = None) -> Dict[str, Any]:
        """Sanitize a dictionary input."""
        if not isinstance(data, dict):
            return {}

        sanitized = {}
        for key, value in data.items():
            # Sanitize key
            clean_key = self.sanitize_string(key, 100)

            # Filter by allowed keys if specified
            if allowed_keys and clean_key not in allowed_keys:
                continue

            # Sanitize value based on type
            if isinstance(value, str):
                sanitized[clean_key] = self.sanitize_string(value)
            elif isinstance(value, (int, float)):
                sanitized[clean_key] = self.sanitize_number(value)
            elif isinstance(value, dict):
                sanitized[clean_key] = self.sanitize_dict(value)
            elif isinstance(value, list):
                sanitized[clean_key] = [
                    self.sanitize_string(item) if isinstance(item, str)
                    else self.sanitize_number(item) if isinstance(item, (int, float))
                    else item
                    for item in value[:100]  # Limit list size
                ]
            else:
                sanitized[clean_key] = value

        return sanitized

    def check_sql_injection(self, value: str) -> Dict[str, Any]:
        """Check for SQL injection patterns."""
        if not isinstance(value, str):
            return {"safe": True, "threats": []}

        threats = []
        for pattern in self.SQL_PATTERNS:
            matches = re.findall(pattern, value, re.IGNORECASE)
            if matches:
                threats.append({
                    "pattern": pattern,
                    "matches": matches[:5],
                })

        return {
            "safe": len(threats) == 0,
            "threats": threats,
        }

    def check_xss(self, value: str) -> Dict[str, Any]:
        """Check for XSS patterns."""
        if not isinstance(value, str):
            return {"safe": True, "threats": []}

        threats = []
        for pattern in self.SCRIPT_PATTERNS:
            matches = re.findall(pattern, value, re.IGNORECASE)
            if matches:
                threats.append({
                    "pattern": pattern,
                    "matches": matches[:5],
                })

        return {
            "safe": len(threats) == 0,
            "threats": threats,
        }

    def validate_signal_input(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and sanitize signal input."""
        errors = []

        # Sanitize symbol
        if "symbol" in data:
            data["symbol"] = self.sanitize_symbol(data["symbol"])
            if not data["symbol"]:
                errors.append("Invalid symbol")

        # Sanitize side
        if "side" in data:
            data["side"] = self.sanitize_string(data["side"], 10).upper()
            if data["side"] not in ("LONG", "SHORT"):
                errors.append(f"Invalid side: {data['side']}")

        # Sanitize prices
        for field in ["entry", "sl", "take_profit_1", "take_profit_2", "take_profit_3"]:
            if field in data:
                data[field] = self.sanitize_number(data[field], min_val=0)

        # Sanitize confidence
        if "confidence" in data:
            data["confidence"] = self.sanitize_number(data["confidence"], min_val=0, max_val=1)

        # Check for injection in string fields
        for field in ["symbol", "side", "regime", "reason"]:
            if field in data and isinstance(data[field], str):
                sql_check = self.check_sql_injection(data[field])
                if not sql_check["safe"]:
                    errors.append(f"SQL injection detected in {field}")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "sanitized": data,
        }
