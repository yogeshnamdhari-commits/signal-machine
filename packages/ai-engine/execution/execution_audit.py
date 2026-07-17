"""
Execution Audit — Comprehensive audit logging for all execution events.

Tracks:
- Signal processing
- Order lifecycle
- Fill events
- Position changes
- Risk events
- Recovery events
- System errors
- Reconciliation events

All events are:
- Logged to structured log files
- Persisted to SQLite for querying
- Available for real-time monitoring
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite
from loguru import logger

import sys
from pathlib import Path
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))


class AuditEventType(str, Enum):
    # Signal events
    SIGNAL_RECEIVED = "SIGNAL_RECEIVED"
    SIGNAL_VALIDATED = "SIGNAL_VALIDATED"
    SIGNAL_REJECTED = "SIGNAL_REJECTED"
    SIGNAL_DUPLICATE = "SIGNAL_DUPLICATE"

    # Order events
    ORDER_CREATED = "ORDER_CREATED"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_ACCEPTED = "ORDER_ACCEPTED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_PARTIALLY_FILLED = "ORDER_PARTIALLY_FILLED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    ORDER_FAILED = "ORDER_FAILED"
    ORDER_TIMEOUT = "ORDER_TIMEOUT"

    # Fill events
    FILL_RECEIVED = "FILL_RECEIVED"
    FILL_PARTIAL = "FILL_PARTIAL"
    FILL_COMPLETE = "FILL_COMPLETE"

    # Position events
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_UPDATED = "POSITION_UPDATED"
    POSITION_CLOSED = "POSITION_CLOSED"
    POSITION_STOPPED = "POSITION_STOPPED"
    POSITION_TP_HIT = "POSITION_TP_HIT"
    POSITION_LIQUIDATED = "POSITION_LIQUIDATED"

    # Risk events
    RISK_CHECK_PASSED = "RISK_CHECK_PASSED"
    RISK_CHECK_FAILED = "RISK_CHECK_FAILED"
    RISK_DRAWDOWN_WARNING = "RISK_DRAWDOWN_WARNING"
    RISK_DAILY_LIMIT = "RISK_DAILY_LIMIT"
    RISK_POSITION_LIMIT = "RISK_POSITION_LIMIT"
    RISK_BREACH = "RISK_BREACH"

    # Recovery events
    RECOVERY_STARTED = "RECOVERY_STARTED"
    RECOVERY_COMPLETED = "RECOVERY_COMPLETED"
    RECOVERY_FAILED = "RECOVERY_FAILED"
    RECOVERY_POSITION_RESTORED = "RECOVERY_POSITION_RESTORED"
    RECOVERY_ORDER_RESTORED = "RECOVERY_ORDER_RESTORED"

    # Reconciliation events
    RECONCILIATION_STARTED = "RECONCILIATION_STARTED"
    RECONCILIATION_PASSED = "RECONCILIATION_PASSED"
    RECONCILIATION_MISMATCH = "RECONCILIATION_MISMATCH"
    RECONCILIATION_CORRECTED = "RECONCILIATION_CORRECTED"
    RECONCILIATION_ESCALATED = "RECONCILIATION_ESCALATED"

    # System events
    SYSTEM_START = "SYSTEM_START"
    SYSTEM_STOP = "SYSTEM_STOP"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    WS_CONNECTED = "WS_CONNECTED"
    WS_DISCONNECTED = "WS_DISCONNECTED"
    WS_RECONNECTED = "WS_RECONNECTED"
    API_ERROR = "API_ERROR"
    API_RATE_LIMIT = "API_RATE_LIMIT"
    DATABASE_ERROR = "DATABASE_ERROR"


@dataclass
class AuditEvent:
    """Single audit event."""
    event_id: str = ""
    event_type: str = ""
    timestamp: float = 0.0
    component: str = ""         # Which module generated this
    entity_type: str = ""       # signal, order, position, etc.
    entity_id: str = ""         # ID of the entity
    severity: str = "INFO"      # DEBUG, INFO, WARNING, ERROR, CRITICAL
    message: str = ""
    details: Dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> Dict:
        return asdict(self)


class ExecutionAudit:
    """
    Comprehensive audit logging system.

    Features:
    - Structured event logging
    - SQLite persistence for querying
    - Loguru integration for file logging
    - Real-time event feed
    - Event filtering and search
    """

    DB_PATH = _ai_root / "data" / "execution" / "audit.db"

    def __init__(self) -> None:
        self._db: Optional[aiosqlite.Connection] = None
        self._event_buffer: List[AuditEvent] = []
        self._buffer_size = 100
        self._total_events = 0
        self._events_by_type: Dict[str, int] = {}
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """Initialize audit database."""
        try:
            self._db = await aiosqlite.connect(str(self.DB_PATH))
            await self._db.execute("PRAGMA journal_mode=WAL")
            await self._db.execute("PRAGMA synchronous=NORMAL")

            await self._db.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    component TEXT,
                    entity_type TEXT,
                    entity_id TEXT,
                    severity TEXT DEFAULT 'INFO',
                    message TEXT,
                    details TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_events(event_type)
            """)
            await self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events(timestamp)
            """)
            await self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_events(entity_type, entity_id)
            """)
            await self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_severity ON audit_events(severity)
            """)

            await self._db.commit()
            logger.info("Audit database initialized: {}", self.DB_PATH)

        except Exception as exc:
            logger.error("Audit DB init failed: {}", exc)

    async def close(self) -> None:
        """Flush buffer and close database."""
        await self._flush_buffer()
        if self._db:
            await self._db.close()

    # ── Event Recording ──────────────────────────────────────────

    async def record(
        self,
        event_type: AuditEventType,
        component: str,
        entity_type: str = "",
        entity_id: str = "",
        severity: str = "INFO",
        message: str = "",
        details: Optional[Dict] = None,
    ) -> AuditEvent:
        """Record an audit event."""
        import uuid

        event = AuditEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type.value,
            component=component,
            entity_type=entity_type,
            entity_id=entity_id,
            severity=severity,
            message=message,
            details=details or {},
        )

        # Log to loguru
        log_msg = f"[{event_type.value}] {component}: {message}"
        if severity == "CRITICAL":
            logger.critical(log_msg)
        elif severity == "ERROR":
            logger.error(log_msg)
        elif severity == "WARNING":
            logger.warning(log_msg)
        elif severity == "DEBUG":
            logger.debug(log_msg)
        else:
            logger.info(log_msg)

        # Buffer for batch insert
        self._event_buffer.append(event)
        self._total_events += 1
        self._events_by_type[event_type.value] = self._events_by_type.get(event_type.value, 0) + 1

        if len(self._event_buffer) >= self._buffer_size:
            await self._flush_buffer()

        return event

    async def _flush_buffer(self) -> None:
        """Flush event buffer to database."""
        if not self._event_buffer or not self._db:
            return

        try:
            events = self._event_buffer.copy()
            self._event_buffer.clear()

            await self._db.executemany(
                """INSERT OR IGNORE INTO audit_events
                   (event_id, event_type, timestamp, component, entity_type,
                    entity_id, severity, message, details)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (e.event_id, e.event_type, e.timestamp, e.component,
                     e.entity_type, e.entity_id, e.severity, e.message,
                     json.dumps(e.details))
                    for e in events
                ],
            )
            await self._db.commit()

        except Exception as exc:
            logger.error("Audit buffer flush failed: {}", exc)

    # ── Convenience Methods ──────────────────────────────────────

    async def signal_received(self, signal_id: str, symbol: str, details: Dict = None) -> None:
        await self.record(
            AuditEventType.SIGNAL_RECEIVED, "execution_engine",
            "signal", signal_id, "INFO",
            f"Signal received: {symbol}", details,
        )

    async def signal_rejected(self, signal_id: str, reason: str, details: Dict = None) -> None:
        await self.record(
            AuditEventType.SIGNAL_REJECTED, "execution_engine",
            "signal", signal_id, "WARNING",
            f"Signal rejected: {reason}", details,
        )

    async def order_event(self, event_type: AuditEventType, order_id: str,
                          symbol: str, message: str, details: Dict = None) -> None:
        await self.record(
            event_type, "order_manager",
            "order", order_id, "INFO", message, details,
        )

    async def position_event(self, event_type: AuditEventType, position_id: str,
                             symbol: str, message: str, details: Dict = None) -> None:
        await self.record(
            event_type, "position_manager",
            "position", position_id, "INFO", message, details,
        )

    async def risk_event(self, event_type: AuditEventType, message: str,
                         details: Dict = None) -> None:
        severity = "WARNING" if "FAIL" in event_type.value or "BREACH" in event_type.value else "INFO"
        await self.record(
            event_type, "risk_guardian",
            "risk", "", severity, message, details,
        )

    async def recovery_event(self, event_type: AuditEventType, message: str,
                             details: Dict = None) -> None:
        await self.record(
            event_type, "execution_recovery",
            "recovery", "", "INFO", message, details,
        )

    async def reconciliation_event(self, event_type: AuditEventType, message: str,
                                   details: Dict = None) -> None:
        severity = "WARNING" if "MISMATCH" in event_type.value else "INFO"
        await self.record(
            event_type, "position_reconciler",
            "reconciliation", "", severity, message, details,
        )

    async def system_event(self, event_type: AuditEventType, message: str,
                           details: Dict = None) -> None:
        await self.record(
            event_type, "system",
            "system", "", "INFO", message, details,
        )

    # ── Queries ──────────────────────────────────────────────────

    async def get_events(
        self,
        event_type: str = "",
        entity_type: str = "",
        entity_id: str = "",
        severity: str = "",
        since: float = 0,
        limit: int = 100,
    ) -> List[Dict]:
        """Query audit events."""
        if not self._db:
            return []

        await self._flush_buffer()

        query = "SELECT * FROM audit_events WHERE 1=1"
        params: List[Any] = []

        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        if entity_type:
            query += " AND entity_type = ?"
            params.append(entity_type)
        if entity_id:
            query += " AND entity_id = ?"
            params.append(entity_id)
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        if since > 0:
            query += " AND timestamp >= ?"
            params.append(since)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        try:
            cursor = await self._db.execute(query, params)
            rows = await cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in rows]
        except Exception as exc:
            logger.error("Audit query failed: {}", exc)
            return []

    async def get_event_counts(self, since: float = 0) -> Dict[str, int]:
        """Get event counts by type."""
        if not self._db:
            return self._events_by_type.copy()

        await self._flush_buffer()

        query = "SELECT event_type, COUNT(*) as cnt FROM audit_events"
        params: List[Any] = []
        if since > 0:
            query += " WHERE timestamp >= ?"
            params.append(since)
        query += " GROUP BY event_type"

        try:
            cursor = await self._db.execute(query, params)
            rows = await cursor.fetchall()
            return {row[0]: row[1] for row in rows}
        except Exception:
            return self._events_by_type.copy()

    async def get_error_events(self, since: float = 0, limit: int = 50) -> List[Dict]:
        """Get recent error events."""
        return await self.get_events(severity="ERROR", since=since, limit=limit)

    def get_stats(self) -> Dict:
        """Get audit statistics."""
        return {
            "total_events": self._total_events,
            "buffer_size": len(self._event_buffer),
            "events_by_type": dict(sorted(
                self._events_by_type.items(),
                key=lambda x: x[1], reverse=True,
            )[:20]),
        }
