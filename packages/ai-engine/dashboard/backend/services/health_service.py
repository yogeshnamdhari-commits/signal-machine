"""
System Health Service — Infrastructure monitoring data aggregation.

Integrates with:
- ExecutionMonitor (HealthSnapshot)
- DataBridge (status.json)
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional

from loguru import logger

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


class HealthService:
    """
    Aggregates system health data for the dashboard.
    Monitors CPU, memory, disk, network, and service health.
    """

    def __init__(self) -> None:
        self._snapshots: List[Dict[str, Any]] = []
        self._service_status: Dict[str, Dict[str, Any]] = {
            "database": {"status": "unknown", "latency_ms": 0, "last_check": 0},
            "redis": {"status": "unknown", "latency_ms": 0, "last_check": 0},
            "api": {"status": "unknown", "latency_ms": 0, "last_check": 0},
            "websocket": {"status": "unknown", "latency_ms": 0, "last_check": 0},
        }
        self._exchange_status: Dict[str, Dict[str, Any]] = {
            "binance": {"status": "unknown", "ws_connected": False, "latency_ms": 0},
            "bybit": {"status": "unknown", "ws_connected": False, "latency_ms": 0},
            "okx": {"status": "unknown", "ws_connected": False, "latency_ms": 0},
            "delta": {"status": "unknown", "ws_connected": False, "latency_ms": 0},
        }
        self._error_count: int = 0
        self._recovery_count: int = 0
        self._queue_depth: int = 0
        self._message_rate: float = 0.0
        self._start_time: float = time.time()
        self._max_snapshots = 2000

    def collect_snapshot(self) -> Dict[str, Any]:
        """Collect current system health metrics."""
        snapshot: Dict[str, Any] = {
            "timestamp": time.time(),
            "uptime_sec": time.time() - self._start_time,
        }

        # CPU
        if _HAS_PSUTIL:
            try:
                snapshot["cpu_pct"] = psutil.cpu_percent(interval=0)
                snapshot["cpu_count"] = psutil.cpu_count()
            except Exception:
                snapshot["cpu_pct"] = 0.0
                snapshot["cpu_count"] = os.cpu_count() or 1
        else:
            try:
                load = os.getloadavg()
                cpu_count = os.cpu_count() or 1
                snapshot["cpu_pct"] = round((load[0] / cpu_count) * 100, 1)
                snapshot["cpu_count"] = cpu_count
            except (OSError, AttributeError):
                snapshot["cpu_pct"] = 0.0
                snapshot["cpu_count"] = 1

        # Memory
        if _HAS_PSUTIL:
            try:
                mem = psutil.virtual_memory()
                snapshot["memory_mb"] = round(mem.used / (1024 * 1024), 1)
                snapshot["memory_total_mb"] = round(mem.total / (1024 * 1024), 1)
                snapshot["memory_pct"] = mem.percent
            except Exception:
                snapshot["memory_mb"] = 0
                snapshot["memory_total_mb"] = 0
                snapshot["memory_pct"] = 0
        else:
            try:
                import resource
                usage = resource.getrusage(resource.RUSAGE_SELF)
                snapshot["memory_mb"] = round(usage.ru_maxrss / 1024, 1)
                snapshot["memory_total_mb"] = 0
                snapshot["memory_pct"] = 0
            except Exception:
                snapshot["memory_mb"] = 0
                snapshot["memory_total_mb"] = 0
                snapshot["memory_pct"] = 0

        # Disk
        if _HAS_PSUTIL:
            try:
                disk = psutil.disk_usage("/")
                snapshot["disk_usage_pct"] = disk.percent
                snapshot["disk_free_gb"] = round(disk.free / (1024 ** 3), 2)
            except Exception:
                snapshot["disk_usage_pct"] = 0
                snapshot["disk_free_gb"] = 0
        else:
            snapshot["disk_usage_pct"] = 0
            snapshot["disk_free_gb"] = 0

        # Services
        snapshot["services"] = self._service_status.copy()
        snapshot["exchanges"] = self._exchange_status.copy()
        snapshot["error_count"] = self._error_count
        snapshot["recovery_count"] = self._recovery_count
        snapshot["queue_depth"] = self._queue_depth
        snapshot["message_rate"] = self._message_rate

        self._snapshots.append(snapshot)
        if len(self._snapshots) > self._max_snapshots:
            self._snapshots = self._snapshots[-self._max_snapshots // 2:]

        return snapshot

    def update_service_status(
        self, service: str, status: str, latency_ms: float = 0
    ) -> None:
        """Update status for a service."""
        if service in self._service_status:
            self._service_status[service].update({
                "status": status,
                "latency_ms": latency_ms,
                "last_check": time.time(),
            })

    def update_exchange_status(
        self, exchange: str, status: str, ws_connected: bool = False, latency_ms: float = 0
    ) -> None:
        """Update status for an exchange."""
        if exchange in self._exchange_status:
            self._exchange_status[exchange].update({
                "status": status,
                "ws_connected": ws_connected,
                "latency_ms": latency_ms,
                "last_check": time.time(),
            })

    def record_error(self) -> None:
        """Record an error event."""
        self._error_count += 1

    def record_recovery(self) -> None:
        """Record a recovery event."""
        self._recovery_count += 1

    def get_health_panel(self) -> Dict[str, Any]:
        """Get data for the system health panel."""
        latest = self._snapshots[-1] if self._snapshots else self.collect_snapshot()

        # Calculate uptime percentage
        total_time = time.time() - self._start_time
        uptime_pct = 100.0  # Simplified - would need downtime tracking

        return {
            "cpu_pct": latest.get("cpu_pct", 0),
            "cpu_count": latest.get("cpu_count", 1),
            "memory_mb": latest.get("memory_mb", 0),
            "memory_total_mb": latest.get("memory_total_mb", 0),
            "memory_pct": latest.get("memory_pct", 0),
            "disk_usage_pct": latest.get("disk_usage_pct", 0),
            "disk_free_gb": latest.get("disk_free_gb", 0),
            "services": self._service_status,
            "exchanges": self._exchange_status,
            "error_count": self._error_count,
            "recovery_count": self._recovery_count,
            "queue_depth": self._queue_depth,
            "message_rate": self._message_rate,
            "uptime_sec": total_time,
            "uptime_pct": round(uptime_pct, 3),
            "health_score": self._calculate_health_score(),
            "timestamp": time.time(),
        }

    def get_health_history(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Get health snapshot history."""
        return self._snapshots[-limit:]

    def _calculate_health_score(self) -> float:
        """Calculate overall system health score (0-100)."""
        score = 100.0
        latest = self._snapshots[-1] if self._snapshots else {}

        cpu = latest.get("cpu_pct", 0)
        if cpu > 90:
            score -= 30
        elif cpu > 70:
            score -= 15
        elif cpu > 50:
            score -= 5

        mem = latest.get("memory_pct", 0)
        if mem > 90:
            score -= 30
        elif mem > 70:
            score -= 15

        disk = latest.get("disk_usage_pct", 0)
        if disk > 90:
            score -= 20
        elif disk > 80:
            score -= 10

        # Service health
        for svc in self._service_status.values():
            if svc.get("status") != "connected" and svc.get("status") != "healthy":
                score -= 5

        # Exchange health
        for exch in self._exchange_status.values():
            if not exch.get("ws_connected"):
                score -= 5

        return max(0, min(100, score))
