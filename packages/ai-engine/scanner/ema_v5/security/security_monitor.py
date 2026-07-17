"""
EMA_V5 Security Monitor — Monitors for security threats and anomalies.
Isolated from existing security monitoring systems.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class ThreatIndicator:
    """Single threat indicator."""
    threat_type: str = ""
    severity: str = "low"  # low, medium, high, critical
    source: str = ""
    message: str = ""
    timestamp: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


class EMAv5SecurityMonitor:
    """Monitors for security threats and anomalies."""

    def __init__(self, window_size: int = 1000) -> None:
        self._window_size = window_size
        self._threats: deque = deque(maxlen=window_size)
        self._request_counts: Dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        self._blocked_ips: Dict[str, float] = {}
        self._suspicious_patterns: List[Dict] = []

    def check_request(self, source: str, path: str, method: str = "GET") -> Dict[str, Any]:
        """Check a request for security threats."""
        threats = []

        # Rate limiting check
        rate_threat = self._check_rate_limit(source)
        if rate_threat:
            threats.append(rate_threat)

        # Path traversal check
        path_threat = self._check_path_traversal(path)
        if path_threat:
            threats.append(path_threat)

        # SQL injection check
        sql_threat = self._check_sql_injection(path)
        if sql_threat:
            threats.append(sql_threat)

        # XSS check
        xss_threat = self._check_xss(path)
        if xss_threat:
            threats.append(xss_threat)

        # Record threats
        for threat in threats:
            self._threats.append(threat)
            self._log_threat(threat)

        return {
            "safe": len(threats) == 0,
            "threats": [
                {
                    "type": t.threat_type,
                    "severity": t.severity,
                    "message": t.message,
                }
                for t in threats
            ],
        }

    def _check_rate_limit(self, source: str) -> Optional[ThreatIndicator]:
        """Check for rate limiting violations."""
        now = time.time()
        self._request_counts[source].append(now)

        # Count requests in last 60 seconds
        recent = [t for t in self._request_counts[source] if now - t < 60]

        if len(recent) > 100:  # More than 100 requests per minute
            return ThreatIndicator(
                threat_type="rate_limit",
                severity="high",
                source=source,
                message=f"Rate limit exceeded: {len(recent)} requests in 60s",
                timestamp=now,
            )

        return None

    def _check_path_traversal(self, path: str) -> Optional[ThreatIndicator]:
        """Check for path traversal attacks."""
        traversal_patterns = ["../", "..\\", "%2e%2e", "%252e%252e"]
        path_lower = path.lower()

        for pattern in traversal_patterns:
            if pattern in path_lower:
                return ThreatIndicator(
                    threat_type="path_traversal",
                    severity="high",
                    source=path,
                    message=f"Path traversal detected: {pattern}",
                    timestamp=time.time(),
                )

        return None

    def _check_sql_injection(self, path: str) -> Optional[ThreatIndicator]:
        """Check for SQL injection attempts."""
        sql_patterns = [
            "union", "select", "insert", "update", "delete",
            "drop", "create", "exec", "execute", "--", ";",
        ]

        path_lower = path.lower()
        for pattern in sql_patterns:
            if pattern in path_lower:
                return ThreatIndicator(
                    threat_type="sql_injection",
                    severity="critical",
                    source=path,
                    message=f"SQL injection attempt: {pattern}",
                    timestamp=time.time(),
                )

        return None

    def _check_xss(self, path: str) -> Optional[ThreatIndicator]:
        """Check for XSS attempts."""
        xss_patterns = ["<script", "javascript:", "onerror=", "onload="]
        path_lower = path.lower()

        for pattern in xss_patterns:
            if pattern in path_lower:
                return ThreatIndicator(
                    threat_type="xss",
                    severity="high",
                    source=path,
                    message=f"XSS attempt: {pattern}",
                    timestamp=time.time(),
                )

        return None

    def _log_threat(self, threat: ThreatIndicator) -> None:
        """Log a detected threat."""
        from .audit_logger import EMAv5AuditLogger
        audit = EMAv5AuditLogger()
        audit.log_security_event(
            event_type=threat.threat_type,
            details={
                "severity": threat.severity,
                "source": threat.source,
                "message": threat.message,
            },
        )

    def block_ip(self, ip: str, duration: int = 3600) -> None:
        """Block an IP address."""
        self._blocked_ips[ip] = time.time() + duration
        logger.warning("EMAv5 security: blocked IP {} for {}s", ip, duration)

    def is_blocked(self, ip: str) -> bool:
        """Check if an IP is blocked."""
        if ip in self._blocked_ips:
            if time.time() < self._blocked_ips[ip]:
                return True
            else:
                del self._blocked_ips[ip]
        return False

    def get_threats(self, n: int = 100) -> List[Dict]:
        """Get recent threats."""
        return [
            {
                "type": t.threat_type,
                "severity": t.severity,
                "source": t.source,
                "message": t.message,
                "timestamp": t.timestamp,
            }
            for t in list(self._threats)[-n:]
        ]

    def get_stats(self) -> Dict[str, Any]:
        """Get security statistics."""
        total_threats = len(self._threats)
        by_type: Dict[str, int] = defaultdict(int)
        by_severity: Dict[str, int] = defaultdict(int)

        for t in self._threats:
            by_type[t.threat_type] += 1
            by_severity[t.severity] += 1

        return {
            "total_threats": total_threats,
            "blocked_ips": len(self._blocked_ips),
            "by_type": dict(by_type),
            "by_severity": dict(by_severity),
        }

    def reset(self) -> None:
        """Reset all monitoring state."""
        self._threats.clear()
        self._request_counts.clear()
        self._blocked_ips.clear()
        self._suspicious_patterns.clear()
