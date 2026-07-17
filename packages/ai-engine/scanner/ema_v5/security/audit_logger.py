"""
EMA_V5 Audit Logger — Logs all security-relevant events.
Isolated from existing audit logging systems.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class EMAv5AuditLogger:
    """Logs all security-relevant events for audit trail."""

    def __init__(self, log_file: str = "data/logs/ema_v5_audit.json") -> None:
        self._log_file = Path(log_file)
        self._log_file.parent.mkdir(parents=True, exist_ok=True)
        self._entries: List[Dict] = []
        self._max_entries = 10000

    def log_event(self, event_type: str, details: Dict[str, Any],
                 severity: str = "info", user: str = "system") -> None:
        """Log a security event."""
        entry = {
            "timestamp": time.time(),
            "time_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "event_type": event_type,
            "severity": severity,
            "user": user,
            "details": details,
        }

        self._entries.append(entry)

        # Trim
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries:]

        # Write to file
        self._write_to_file(entry)

        # Log to loguru
        log_msg = f"[AUDIT] {event_type}: {json.dumps(details, default=str)}"
        if severity == "critical":
            logger.critical(log_msg)
        elif severity == "high":
            logger.error(log_msg)
        elif severity == "medium":
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

    def log_auth_event(self, action: str, key_id: str = "",
                      success: bool = True, details: Optional[Dict] = None) -> None:
        """Log authentication event."""
        self.log_event(
            event_type=f"auth_{action}",
            details={
                "key_id": key_id,
                "success": success,
                **(details or {}),
            },
            severity="info" if success else "warning",
        )

    def log_data_access(self, resource: str, action: str,
                       user: str = "system", details: Optional[Dict] = None) -> None:
        """Log data access event."""
        self.log_event(
            event_type="data_access",
            details={
                "resource": resource,
                "action": action,
                **(details or {}),
            },
            severity="info",
            user=user,
        )

    def log_security_event(self, event_type: str, details: Dict[str, Any]) -> None:
        """Log a security event."""
        self.log_event(
            event_type=event_type,
            details=details,
            severity="high",
        )

    def log_config_change(self, component: str, changes: Dict[str, Any]) -> None:
        """Log configuration change."""
        self.log_event(
            event_type="config_change",
            details={
                "component": component,
                "changes": changes,
            },
            severity="medium",
        )

    def log_signal_event(self, action: str, symbol: str, side: str = "",
                        details: Optional[Dict] = None) -> None:
        """Log signal-related event."""
        self.log_event(
            event_type=f"signal_{action}",
            details={
                "symbol": symbol,
                "side": side,
                **(details or {}),
            },
            severity="info",
        )

    def log_trade_event(self, action: str, symbol: str, pnl: float = 0,
                       details: Optional[Dict] = None) -> None:
        """Log trade-related event."""
        self.log_event(
            event_type=f"trade_{action}",
            details={
                "symbol": symbol,
                "pnl": pnl,
                **(details or {}),
            },
            severity="info",
        )

    def _write_to_file(self, entry: Dict) -> None:
        """Append entry to audit log file."""
        try:
            with open(self._log_file, "a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            pass

    def get_entries(self, n: int = 100, event_type: Optional[str] = None,
                   severity: Optional[str] = None) -> List[Dict]:
        """Get recent audit entries."""
        entries = self._entries

        if event_type:
            entries = [e for e in entries if e.get("event_type") == event_type]
        if severity:
            entries = [e for e in entries if e.get("severity") == severity]

        return entries[-n:]

    def get_stats(self) -> Dict[str, Any]:
        """Get audit log statistics."""
        total = len(self._entries)
        by_type: Dict[str, int] = {}
        by_severity: Dict[str, int] = {}

        for entry in self._entries:
            et = entry.get("event_type", "unknown")
            sev = entry.get("severity", "info")
            by_type[et] = by_type.get(et, 0) + 1
            by_severity[sev] = by_severity.get(sev, 0) + 1

        return {
            "total_entries": total,
            "by_type": by_type,
            "by_severity": by_severity,
        }

    def clear(self) -> int:
        """Clear all entries."""
        count = len(self._entries)
        self._entries.clear()
        return count
