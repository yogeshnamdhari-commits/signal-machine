"""
EMA_V5 SQL Guard — Prevents SQL injection in database queries.
Isolated from existing SQL protection systems.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from loguru import logger


class EMAv5SQLGuard:
    """SQL injection prevention for EMA_V5 database queries."""

    # Dangerous SQL patterns
    DANGEROUS_PATTERNS = [
        (r"\b(SELECT|INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|EXEC|EXECUTE)\b", "SQL keyword"),
        (r"--|;|/\*|\*/", "SQL comment/terminator"),
        (r"UNION\s+ALL|UNION\s+SELECT", "UNION attack"),
        (r"OR\s+['\"]?\w+['\"]?\s*=\s*['\"]?\w+['\"]?", "OR injection"),
        (r"AND\s+['\"]?\w+['\"]?\s*=\s*['\"]?\w+['\"]?", "AND injection"),
        (r"CHAR\s*\(|CONCAT\s*\(", "Function injection"),
        (r"BENCHMARK\s*\(|SLEEP\s*\(", "Time-based injection"),
        (r"LOAD_FILE\s*\(", "File access injection"),
    ]

    # Allowed characters for different field types
    SYMBOL_PATTERN = re.compile(r'^[A-Z0-9]{1,20}$')
    SIDE_PATTERN = re.compile(r'^(LONG|SHORT)$')
    NUMBER_PATTERN = re.compile(r'^-?\d+\.?\d*$')

    # Dangerous patterns that indicate injection in query strings
    # (not basic SQL keywords which are expected in parameterized queries)
    QUERY_THREAT_PATTERNS = [
        (r";\s*(DROP|DELETE|UPDATE|INSERT|ALTER|CREATE|EXEC)", "Multi-statement injection"),
        (r"UNION\s+(ALL\s+)?SELECT", "UNION injection"),
        (r"(--|/\*|\*/)", "SQL comment/terminator"),
        (r"OR\s+1\s*=\s*1", "OR injection"),
        (r"AND\s+1\s*=\s*1", "AND injection"),
    ]

    def __init__(self) -> None:
        self._blocked_queries: List[Dict] = []

    def validate_query(self, query: str, params: Optional[tuple] = None) -> Dict[str, Any]:
        """Validate a SQL query for safety.
        
        Checks query string for injection patterns (multi-statement, UNION, comments)
        and parameters for SQL keyword injection.
        """
        threats = []

        # Check query string for actual injection patterns (not basic SQL keywords)
        for pattern, threat_type in self.QUERY_THREAT_PATTERNS:
            if re.search(pattern, query, re.IGNORECASE):
                threats.append({
                    "type": threat_type,
                    "pattern": pattern,
                    "severity": "high",
                })

        # Check parameters (the real injection vector)
        if params:
            for i, param in enumerate(params):
                if isinstance(param, str):
                    for pattern, threat_type in self.DANGEROUS_PATTERNS:
                        if re.search(pattern, param, re.IGNORECASE):
                            threats.append({
                                "type": f"Parameter {i}: {threat_type}",
                                "pattern": pattern,
                                "severity": "critical",
                            })

        safe = len(threats) == 0

        if not safe:
            self._blocked_queries.append({
                "query": query[:200],
                "threats": threats,
                "timestamp": time.time(),
            })
            logger.warning("EMAv5 SQL guard: blocked query with {} threats", len(threats))

        return {
            "safe": safe,
            "threats": threats,
        }

    def sanitize_identifier(self, identifier: str) -> str:
        """Sanitize a SQL identifier (table/column name)."""
        # Only allow alphanumeric and underscore
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '', identifier)

        # Must start with letter or underscore
        if sanitized and not sanitized[0].isalpha() and sanitized[0] != '_':
            sanitized = '_' + sanitized

        return sanitized

    def sanitize_value(self, value: Any, field_type: str = "string") -> Any:
        """Sanitize a value for safe SQL insertion."""
        if value is None:
            return None

        if field_type == "symbol":
            if isinstance(value, str):
                # Only allow A-Z, 0-9
                return re.sub(r'[^A-Z0-9]', '', value.upper())[:20]
            return ""

        elif field_type == "side":
            if isinstance(value, str):
                val = value.upper().strip()
                return val if val in ("LONG", "SHORT") else ""
            return ""

        elif field_type == "number":
            try:
                val = float(value)
                if val != val or abs(val) == float('inf'):  # NaN/Inf check
                    return 0
                return val
            except (ValueError, TypeError):
                return 0

        elif field_type == "text":
            if isinstance(value, str):
                # Remove null bytes, truncate
                return value.replace("\x00", "")[:1000]
            return str(value)[:1000] if value else ""

        return value

    def safe_insert(self, table: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a safe INSERT query."""
        # Sanitize table name
        safe_table = self.sanitize_identifier(table)

        # Sanitize column names and values
        safe_columns = []
        safe_values = []
        for col, val in data.items():
            safe_col = self.sanitize_identifier(col)
            if safe_col:
                safe_columns.append(safe_col)
                safe_values.append(val)

        if not safe_columns:
            return {"safe": False, "error": "No valid columns"}

        # Build parameterized query
        placeholders = ", ".join(["?"] * len(safe_columns))
        col_names = ", ".join(safe_columns)
        query = f"INSERT INTO {safe_table} ({col_names}) VALUES ({placeholders})"

        return {
            "safe": True,
            "query": query,
            "params": tuple(safe_values),
        }

    def safe_update(self, table: str, data: Dict[str, Any],
                   where: str, where_params: tuple) -> Dict[str, Any]:
        """Generate a safe UPDATE query."""
        safe_table = self.sanitize_identifier(table)

        set_parts = []
        set_values = []
        for col, val in data.items():
            safe_col = self.sanitize_identifier(col)
            if safe_col:
                set_parts.append(f"{safe_col} = ?")
                set_values.append(val)

        if not set_parts:
            return {"safe": False, "error": "No valid columns"}

        set_clause = ", ".join(set_parts)
        query = f"UPDATE {safe_table} SET {set_clause} WHERE {where}"

        return {
            "safe": True,
            "query": query,
            "params": tuple(set_values) + where_params,
        }

    def safe_select(self, table: str, columns: List[str] = None,
                   where: Optional[str] = None, where_params: tuple = (),
                   order_by: Optional[str] = None, limit: int = 1000) -> Dict[str, Any]:
        """Generate a safe SELECT query."""
        safe_table = self.sanitize_identifier(table)

        if columns:
            safe_cols = ", ".join(self.sanitize_identifier(c) for c in columns if c)
        else:
            safe_cols = "*"

        query = f"SELECT {safe_cols} FROM {safe_table}"

        params = list(where_params)
        if where:
            query += f" WHERE {where}"

        if order_by:
            safe_order = self.sanitize_identifier(order_by)
            if safe_order:
                query += f" ORDER BY {safe_order}"

        query += f" LIMIT {min(limit, 10000)}"

        return {
            "safe": True,
            "query": query,
            "params": tuple(params),
        }

    def get_blocked_queries(self, n: int = 50) -> List[Dict]:
        """Get recently blocked queries."""
        return self._blocked_queries[-n:]

    def get_stats(self) -> Dict[str, Any]:
        """Get SQL guard statistics."""
        return {
            "blocked_queries": len(self._blocked_queries),
            "recent_blocks": len(self._blocked_queries[-10:]),
        }
