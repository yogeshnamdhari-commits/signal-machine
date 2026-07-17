"""
Operational Monitor — System Health & Infrastructure Monitoring
================================================================
Monitors: CPU, RAM, Disk, API Health, WebSocket Health,
          Database Health, Latency, Execution Latency
"""

import json
import logging
import os
import platform
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "capital"


# ─── Data Classes ────────────────────────────────────────────────────────────
@dataclass
class SystemHealth:
    """System resource health snapshot."""
    timestamp: str
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_total_mb: float
    disk_percent: float
    disk_used_gb: float
    disk_total_gb: float
    uptime_seconds: float
    load_average: list
    process_count: int
    thread_count: int


@dataclass
class ServiceHealth:
    """External service health."""
    service: str
    healthy: bool
    latency_ms: float
    last_check: str
    error_count: int
    last_error: str
    uptime_pct: float


@dataclass
class OperationalSnapshot:
    """Complete operational health snapshot."""
    timestamp: str
    overall_status: str  # HEALTHY, DEGRADED, UNHEALTHY, CRITICAL
    health_score: float  # 0-100
    system: dict
    services: dict
    alerts: list
    metrics: dict


# ─── Operational Monitor ─────────────────────────────────────────────────────
class OperationalMonitor:
    """
    Monitors system health and external service connectivity.

    Usage:
        monitor = OperationalMonitor()
        monitor.register_service("binance_api", check_fn)
        snapshot = monitor.check_all()
    """

    # Thresholds
    CPU_WARNING = 70
    CPU_CRITICAL = 90
    MEMORY_WARNING = 75
    MEMORY_CRITICAL = 90
    DISK_WARNING = 80
    DISK_CRITICAL = 95
    LATENCY_WARNING = 500    # ms
    LATENCY_CRITICAL = 2000  # ms
    UPTIME_MINIMUM = 0.99    # 99%

    def __init__(self):
        self._services: dict[str, dict] = {}
        self._health_history: list[dict] = []
        self._alerts: list[dict] = []
        self._start_time = time.time()
        self._check_counts: dict[str, int] = {}
        self._error_counts: dict[str, int] = {}
        self._latency_history: dict[str, list[float]] = {}

        # Register default services
        self._register_defaults()
        logger.info("OperationalMonitor initialized")

    # ── Service Registration ──────────────────────────────────────────────────
    def register_service(self, name: str, check_fn=None, critical: bool = True):
        """Register a service for monitoring."""
        self._services[name] = {
            "check_fn": check_fn,
            "critical": critical,
            "healthy": True,
            "latency_ms": 0,
            "last_check": "",
            "error": "",
        }
        self._check_counts[name] = 0
        self._error_counts[name] = 0
        self._latency_history[name] = []

    def _register_defaults(self):
        """Register default services."""
        self.register_service("binance_api", critical=True)
        self.register_service("websocket", critical=True)
        self.register_service("database", critical=True)
        self.register_service("event_bus", critical=False)

    # ── Health Checks ─────────────────────────────────────────────────────────
    def check_all(self) -> OperationalSnapshot:
        """Run all health checks."""
        alerts = []

        # System health
        system = self._check_system()

        # Service health
        services = {}
        for name, config in self._services.items():
            svc_health = self._check_service(name, config)
            services[name] = asdict(svc_health)
            if not svc_health.healthy and config["critical"]:
                alerts.append({
                    "severity": "CRITICAL",
                    "service": name,
                    "message": f"{name} is unhealthy: {svc_health.last_error}",
                })

        # System alerts
        if system.cpu_percent > self.CPU_CRITICAL:
            alerts.append({"severity": "CRITICAL", "message": f"CPU at {system.cpu_percent:.1f}%"})
        elif system.cpu_percent > self.CPU_WARNING:
            alerts.append({"severity": "WARNING", "message": f"CPU at {system.cpu_percent:.1f}%"})

        if system.memory_percent > self.MEMORY_CRITICAL:
            alerts.append({"severity": "CRITICAL", "message": f"Memory at {system.memory_percent:.1f}%"})
        elif system.memory_percent > self.MEMORY_WARNING:
            alerts.append({"severity": "WARNING", "message": f"Memory at {system.memory_percent:.1f}%"})

        if system.disk_percent > self.DISK_CRITICAL:
            alerts.append({"severity": "CRITICAL", "message": f"Disk at {system.disk_percent:.1f}%"})
        elif system.disk_percent > self.DISK_WARNING:
            alerts.append({"severity": "WARNING", "message": f"Disk at {system.disk_percent:.1f}%"})

        # Calculate health score
        score = self._calculate_health_score(system, services)
        status = self._determine_status(score, alerts)

        # Execution metrics
        metrics = self._calculate_metrics()

        snapshot = OperationalSnapshot(
            timestamp=datetime.now(timezone.utc).isoformat(),
            overall_status=status,
            health_score=round(score, 1),
            system=asdict(system),
            services=services,
            alerts=alerts,
            metrics=metrics,
        )

        self._health_history.append(asdict(snapshot))
        self._alerts.extend(alerts)
        self._save_state()

        return snapshot

    # ── System Check ──────────────────────────────────────────────────────────
    def _check_system(self) -> SystemHealth:
        """Check system resources."""
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.1)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            load = list(os.getloadavg()) if hasattr(os, 'getloadavg') else [0, 0, 0]

            return SystemHealth(
                timestamp=datetime.now(timezone.utc).isoformat(),
                cpu_percent=cpu,
                memory_percent=mem.percent,
                memory_used_mb=round(mem.used / 1024 / 1024, 1),
                memory_total_mb=round(mem.total / 1024 / 1024, 1),
                disk_percent=disk.percent,
                disk_used_gb=round(disk.used / 1024 / 1024 / 1024, 2),
                disk_total_gb=round(disk.total / 1024 / 1024 / 1024, 2),
                uptime_seconds=time.time() - self._start_time,
                load_average=load,
                process_count=len(psutil.pids()),
                thread_count=0,
            )
        except ImportError:
            # Fallback without psutil
            return SystemHealth(
                timestamp=datetime.now(timezone.utc).isoformat(),
                cpu_percent=0, memory_percent=0, memory_used_mb=0, memory_total_mb=0,
                disk_percent=0, disk_used_gb=0, disk_total_gb=0,
                uptime_seconds=time.time() - self._start_time,
                load_average=[0, 0, 0], process_count=0, thread_count=0,
            )

    # ── Service Check ─────────────────────────────────────────────────────────
    def _check_service(self, name: str, config: dict) -> ServiceHealth:
        """Check a single service."""
        check_fn = config.get("check_fn")
        healthy = True
        latency = 0.0
        error = ""

        if check_fn:
            try:
                start = time.time()
                result = check_fn()
                latency = (time.time() - start) * 1000
                healthy = bool(result)
            except Exception as e:
                healthy = False
                error = str(e)
                self._error_counts[name] = self._error_counts.get(name, 0) + 1
        else:
            # Default: assume healthy if no check function
            healthy = config.get("healthy", True)
            latency = config.get("latency_ms", 0)

        self._check_counts[name] = self._check_counts.get(name, 0) + 1
        self._latency_history.setdefault(name, []).append(latency)
        # Keep last 100
        self._latency_history[name] = self._latency_history[name][-100:]

        # Uptime calculation
        total = self._check_counts[name]
        errors = self._error_counts.get(name, 0)
        uptime = (total - errors) / total if total > 0 else 1.0

        return ServiceHealth(
            service=name,
            healthy=healthy,
            latency_ms=round(latency, 2),
            last_check=datetime.now(timezone.utc).isoformat(),
            error_count=errors,
            last_error=error,
            uptime_pct=round(uptime * 100, 2),
        )

    # ── Scoring ───────────────────────────────────────────────────────────────
    def _calculate_health_score(self, system: SystemHealth, services: dict) -> float:
        """Calculate overall health score (0-100)."""
        score = 100.0

        # System penalties
        if system.cpu_percent > self.CPU_CRITICAL:
            score -= 25
        elif system.cpu_percent > self.CPU_WARNING:
            score -= 10

        if system.memory_percent > self.MEMORY_CRITICAL:
            score -= 25
        elif system.memory_percent > self.MEMORY_WARNING:
            score -= 10

        if system.disk_percent > self.DISK_CRITICAL:
            score -= 20
        elif system.disk_percent > self.DISK_WARNING:
            score -= 5

        # Service penalties
        for name, svc in services.items():
            if not svc.get("healthy"):
                if self._services.get(name, {}).get("critical"):
                    score -= 30
                else:
                    score -= 10
            latency = svc.get("latency_ms", 0)
            if latency > self.LATENCY_CRITICAL:
                score -= 15
            elif latency > self.LATENCY_WARNING:
                score -= 5

        return max(score, 0)

    def _determine_status(self, score: float, alerts: list) -> str:
        """Determine overall status."""
        critical_alerts = sum(1 for a in alerts if a.get("severity") == "CRITICAL")
        if critical_alerts > 0 or score < 30:
            return "CRITICAL"
        elif score < 60:
            return "UNHEALTHY"
        elif score < 80:
            return "DEGRADED"
        return "HEALTHY"

    # ── Metrics ───────────────────────────────────────────────────────────────
    def _calculate_metrics(self) -> dict:
        """Calculate operational metrics."""
        uptime = time.time() - self._start_time
        total_checks = sum(self._check_counts.values())
        total_errors = sum(self._error_counts.values())

        avg_latencies = {}
        for name, latencies in self._latency_history.items():
            if latencies:
                avg_latencies[name] = round(sum(latencies) / len(latencies), 2)

        return {
            "uptime_seconds": round(uptime, 1),
            "uptime_hours": round(uptime / 3600, 2),
            "total_checks": total_checks,
            "total_errors": total_errors,
            "error_rate": round(total_errors / total_checks * 100, 2) if total_checks > 0 else 0,
            "avg_latencies": avg_latencies,
            "health_snapshots": len(self._health_history),
            "alerts_total": len(self._alerts),
        }

    # ── Manual Service Status ─────────────────────────────────────────────────
    def set_service_status(self, name: str, healthy: bool, latency_ms: float = 0, error: str = ""):
        """Manually set service status (for external health reporting)."""
        if name in self._services:
            self._services[name]["healthy"] = healthy
            self._services[name]["latency_ms"] = latency_ms
            if error:
                self._services[name]["error"] = error
                self._error_counts[name] = self._error_counts.get(name, 0) + 1

    # ── State Persistence ─────────────────────────────────────────────────────
    def _save_state(self):
        """Save monitor state."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "start_time": self._start_time,
            "check_counts": self._check_counts,
            "error_counts": self._error_counts,
            "total_alerts": len(self._alerts),
            "health_snapshots": len(self._health_history),
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        (DATA_DIR / "operational_state.json").write_text(json.dumps(state, indent=2, default=str))

    def get_stats(self) -> dict:
        """Get monitor statistics."""
        metrics = self._calculate_metrics()
        return {
            "uptime_hours": metrics["uptime_hours"],
            "total_checks": metrics["total_checks"],
            "total_errors": metrics["total_errors"],
            "error_rate": metrics["error_rate"],
            "alerts": metrics["alerts_total"],
            "services": list(self._services.keys()),
        }
