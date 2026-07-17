"""
Execution Monitor — System health monitoring and alerting.

Monitors:
- CPU usage
- Memory usage
- Disk usage
- WebSocket status
- API latency
- Database health
- Queue depths
- Error rates
- Execution latency

Generates:
- Health snapshots
- Performance metrics
- Alert events
- System reports
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from loguru import logger

import sys
from pathlib import Path
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))


@dataclass
class HealthSnapshot:
    """System health snapshot."""
    timestamp: float = 0.0
    cpu_pct: float = 0.0
    memory_mb: float = 0.0
    memory_pct: float = 0.0
    disk_usage_pct: float = 0.0
    ws_connected: bool = False
    ws_reconnects: int = 0
    api_latency_ms: float = 0.0
    api_errors: int = 0
    api_error_rate: float = 0.0
    db_healthy: bool = True
    queue_depth: int = 0
    messages_processed: int = 0
    messages_per_sec: float = 0.0
    execution_latency_ms: float = 0.0
    uptime_sec: float = 0.0
    uptime_pct: float = 100.0

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "cpu_pct": round(self.cpu_pct, 1),
            "memory_mb": round(self.memory_mb, 1),
            "memory_pct": round(self.memory_pct, 1),
            "disk_usage_pct": round(self.disk_usage_pct, 1),
            "ws_connected": self.ws_connected,
            "ws_reconnects": self.ws_reconnects,
            "api_latency_ms": round(self.api_latency_ms, 1),
            "api_errors": self.api_errors,
            "api_error_rate": round(self.api_error_rate, 4),
            "db_healthy": self.db_healthy,
            "queue_depth": self.queue_depth,
            "messages_per_sec": round(self.messages_per_sec, 1),
            "execution_latency_ms": round(self.execution_latency_ms, 1),
            "uptime_sec": round(self.uptime_sec, 0),
            "uptime_pct": round(self.uptime_pct, 2),
        }


@dataclass
class AlertRule:
    """Alert configuration."""
    name: str
    condition: str  # e.g., "cpu > 90", "memory > 90"
    threshold: float
    message: str
    severity: str = "WARNING"
    cooldown_sec: float = 300  # Minimum time between alerts
    last_triggered: float = 0.0


class ExecutionMonitor:
    """
    System health monitoring and alerting.

    Features:
    - Continuous health monitoring
    - Configurable alert rules
    - Alert cooldown to prevent spam
    - Health history for trend analysis
    - Performance metrics tracking
    """

    CHECK_INTERVAL_SEC = 30

    def __init__(self) -> None:
        self._start_time = time.time()
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Health history
        self._snapshots: List[HealthSnapshot] = []
        self._max_snapshots = 1000

        # Performance tracking
        self._execution_latencies: List[float] = []
        self._api_latencies: List[float] = []
        self._ws_reconnects = 0
        self._api_errors = 0
        self._total_messages = 0
        self._message_counts: List[Tuple[float, int]] = []  # (timestamp, count)
        self._downtime_sec = 0.0
        self._last_ws_disconnect = 0.0

        # Alert rules
        self._alert_rules: List[AlertRule] = [
            AlertRule("high_cpu", "cpu > 90", 90.0,
                     "CPU usage critical: {value}%", "CRITICAL"),
            AlertRule("high_memory", "memory > 90", 90.0,
                     "Memory usage critical: {value}%", "CRITICAL"),
            AlertRule("high_disk", "disk > 90", 90.0,
                     "Disk usage critical: {value}%", "WARNING"),
            AlertRule("api_down", "api_errors > 10", 10.0,
                     "API errors high: {value}", "ERROR"),
            AlertRule("ws_disconnect", "ws_reconnects > 5", 5.0,
                     "WebSocket reconnects: {value}", "WARNING"),
            AlertRule("high_latency", "latency > 1000", 1000.0,
                     "API latency high: {value}ms", "WARNING"),
            AlertRule("queue_backlog", "queue > 100", 100.0,
                     "Queue backlog: {value}", "WARNING"),
        ]

        # Callbacks
        self._on_alert: Optional[Callable] = None

    def set_callbacks(self, on_alert: Optional[Callable] = None) -> None:
        self._on_alert = on_alert

    async def start(self) -> None:
        """Start monitoring loop."""
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Execution monitor started")

    async def stop(self) -> None:
        """Stop monitoring loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _monitor_loop(self) -> None:
        """Main monitoring loop."""
        while self._running:
            try:
                snapshot = await self._collect_snapshot()
                self._snapshots.append(snapshot)
                if len(self._snapshots) > self._max_snapshots:
                    self._snapshots = self._snapshots[-self._max_snapshots // 2:]

                await self._check_alerts(snapshot)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Monitor error: {}", exc)

            await asyncio.sleep(self.CHECK_INTERVAL_SEC)

    async def _collect_snapshot(self) -> HealthSnapshot:
        """Collect current system health metrics."""
        snapshot = HealthSnapshot(timestamp=time.time())

        # CPU (cross-platform)
        try:
            # Use os.getloadavg on Unix
            load = os.getloadavg()
            cpu_count = os.cpu_count() or 1
            snapshot.cpu_pct = (load[0] / cpu_count) * 100
        except (OSError, AttributeError):
            snapshot.cpu_pct = 0.0

        # Memory
        try:
            import resource
            usage = resource.getrusage(resource.RUSAGE_SELF)
            snapshot.memory_mb = usage.ru_maxrss / 1024  # macOS: bytes → MB
        except (ImportError, AttributeError):
            snapshot.memory_mb = 0.0

        # Disk
        try:
            stat = os.statvfs(str(_ai_root))
            snapshot.disk_usage_pct = (1 - stat.f_bavail / stat.f_blocks) * 100 if stat.f_blocks > 0 else 0
        except (OSError, AttributeError):
            snapshot.disk_usage_pct = 0.0

        # WebSocket
        snapshot.ws_reconnects = self._ws_reconnects

        # API
        snapshot.api_errors = self._api_errors
        if self._api_latencies:
            snapshot.api_latency_ms = sorted(self._api_latencies)[len(self._api_latencies) // 2]

        # Messages
        snapshot.messages_processed = self._total_messages
        now = time.time()
        # Calculate messages/sec from last 60s
        recent = [(t, c) for t, c in self._message_counts if now - t < 60]
        if recent:
            total_recent = sum(c for _, c in recent)
            time_span = now - recent[0][0] if recent else 60
            snapshot.messages_per_sec = total_recent / max(time_span, 1)

        # Execution latency
        if self._execution_latencies:
            snapshot.execution_latency_ms = sorted(self._execution_latencies)[len(self._execution_latencies) // 2]

        # Uptime
        elapsed = now - self._start_time
        snapshot.uptime_sec = elapsed
        snapshot.uptime_pct = ((elapsed - self._downtime_sec) / elapsed * 100) if elapsed > 0 else 100.0

        return snapshot

    async def _check_alerts(self, snapshot: HealthSnapshot) -> None:
        """Check alert rules against snapshot."""
        now = time.time()

        for rule in self._alert_rules:
            # Check cooldown
            if now - rule.last_triggered < rule.cooldown_sec:
                continue

            triggered = False
            value = 0.0

            if rule.name == "high_cpu":
                value = snapshot.cpu_pct
                triggered = value > rule.threshold
            elif rule.name == "high_memory":
                value = snapshot.memory_pct
                triggered = value > rule.threshold
            elif rule.name == "high_disk":
                value = snapshot.disk_usage_pct
                triggered = value > rule.threshold
            elif rule.name == "api_down":
                value = snapshot.api_errors
                triggered = value > rule.threshold
            elif rule.name == "ws_disconnect":
                value = snapshot.ws_reconnects
                triggered = value > rule.threshold
            elif rule.name == "high_latency":
                value = snapshot.api_latency_ms
                triggered = value > rule.threshold
            elif rule.name == "queue_backlog":
                value = snapshot.queue_depth
                triggered = value > rule.threshold

            if triggered:
                rule.last_triggered = now
                message = rule.message.format(value=value)
                logger.warning("ALERT [{}]: {}", rule.severity, message)

                if self._on_alert:
                    await self._on_alert({
                        "rule": rule.name,
                        "severity": rule.severity,
                        "message": message,
                        "value": value,
                        "threshold": rule.threshold,
                        "timestamp": now,
                    })

    # ── Event Recording ──────────────────────────────────────────

    def record_execution_latency(self, latency_ms: float) -> None:
        self._execution_latencies.append(latency_ms)
        if len(self._execution_latencies) > 1000:
            self._execution_latencies = self._execution_latencies[-500:]

    def record_api_latency(self, latency_ms: float) -> None:
        self._api_latencies.append(latency_ms)
        if len(self._api_latencies) > 1000:
            self._api_latencies = self._api_latencies[-500:]

    def record_api_error(self) -> None:
        self._api_errors += 1

    def record_ws_disconnect(self) -> None:
        self._ws_reconnects += 1
        self._last_ws_disconnect = time.time()

    def record_ws_reconnect(self) -> None:
        if self._last_ws_disconnect > 0:
            self._downtime_sec += time.time() - self._last_ws_disconnect
            self._last_ws_disconnect = 0

    def record_message(self) -> None:
        self._total_messages += 1
        self._message_counts.append((time.time(), 1))
        if len(self._message_counts) > 10000:
            self._message_counts = self._message_counts[-5000:]

    # ── Queries ──────────────────────────────────────────────────

    def get_latest_snapshot(self) -> Optional[HealthSnapshot]:
        return self._snapshots[-1] if self._snapshots else None

    def get_history(self, minutes: int = 60) -> List[HealthSnapshot]:
        since = time.time() - minutes * 60
        return [s for s in self._snapshots if s.timestamp >= since]

    def get_stats(self) -> Dict:
        """Get monitoring statistics."""
        latest = self.get_latest_snapshot()
        return {
            "uptime_sec": round(time.time() - self._start_time, 0),
            "uptime_pct": round(latest.uptime_pct, 2) if latest else 100.0,
            "cpu_pct": round(latest.cpu_pct, 1) if latest else 0.0,
            "memory_mb": round(latest.memory_mb, 1) if latest else 0.0,
            "ws_reconnects": self._ws_reconnects,
            "api_errors": self._api_errors,
            "total_messages": self._total_messages,
            "snapshots_collected": len(self._snapshots),
            "alert_rules": len(self._alert_rules),
        }
