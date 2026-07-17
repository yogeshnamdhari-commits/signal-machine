"""
EMA_V5 Monitor — Runtime monitoring and alerting.
Isolated from existing monitoring systems.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class MonitorConfig:
    """Monitor configuration."""
    check_interval_sec: int = 60
    alert_on_degradation: bool = True
    alert_on_error: bool = True
    max_alerts_per_hour: int = 10


class EMAv5Monitor:
    """Runtime monitoring for EMA_V5."""

    def __init__(self, config: Optional[MonitorConfig] = None) -> None:
        self.config = config or MonitorConfig()
        self._metrics: Dict[str, Any] = {}
        self._alerts: List[Dict] = []
        self._start_time = time.time()
        self._last_check = 0

    def record_metric(self, name: str, value: Any) -> None:
        """Record a metric value."""
        self._metrics[name] = {
            "value": value,
            "timestamp": time.time(),
        }

    def check_health(self) -> Dict[str, Any]:
        """Check system health."""
        from .health_check import EMAv5HealthCheck
        hc = EMAv5HealthCheck()
        health = hc.check_all()

        # Record metrics
        self.record_metric("health_ok", health["summary"]["ok"])
        self.record_metric("health_warning", health["summary"]["warning"])
        self.record_metric("health_error", health["summary"]["error"])

        # Alert on issues
        if health["summary"]["error"] > 0 and self.config.alert_on_error:
            self._add_alert("error", f"{health['summary']['error']} health check errors")

        self._last_check = time.time()
        return health

    def check_performance(self) -> Dict[str, Any]:
        """Check performance metrics."""
        try:
            from ..performance.real_time_tracker import EMAv5RealTimeTracker
            tracker = EMAv5RealTimeTracker()
            metrics = tracker.get_current_metrics()

            self.record_metric("total_trades", metrics.get("total_trades", 0))
            self.record_metric("win_rate", metrics.get("win_rate", 0))
            self.record_metric("total_pnl", metrics.get("total_pnl", 0))

            # Alert on degradation
            if self.config.alert_on_degradation:
                if metrics.get("win_rate", 0) < 40:
                    self._add_alert("warning", f"Win rate below 40%: {metrics.get('win_rate', 0)}%")

            return metrics
        except Exception as e:
            return {"error": str(e)}

    def get_metrics(self) -> Dict[str, Any]:
        """Get all recorded metrics."""
        return {
            name: {
                "value": m["value"],
                "age": time.time() - m["timestamp"],
            }
            for name, m in self._metrics.items()
        }

    def get_alerts(self, n: int = 50) -> List[Dict]:
        """Get last N alerts."""
        return self._alerts[-n:]

    def _add_alert(self, level: str, message: str) -> None:
        """Add an alert."""
        self._alerts.append({
            "level": level,
            "message": message,
            "timestamp": time.time(),
        })
        # Trim old alerts
        if len(self._alerts) > 1000:
            self._alerts = self._alerts[-500:]

    def get_status(self) -> Dict[str, Any]:
        """Get monitor status."""
        uptime = time.time() - self._start_time
        return {
            "uptime_seconds": round(uptime, 0),
            "metrics_count": len(self._metrics),
            "alerts_count": len(self._alerts),
            "last_check": self._last_check,
            "recent_alerts": self._alerts[-5:],
        }

    def reset(self) -> None:
        """Reset monitor state."""
        self._metrics.clear()
        self._alerts.clear()
        self._start_time = time.time()
        self._last_check = 0
