"""
Health Monitor — tracks system component health.
Exposes green/yellow/red status for dashboard.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict
from loguru import logger


class HealthMonitor:
    """Monitors all system components and reports health status."""

    def __init__(self) -> None:
        self._status: Dict[str, Dict] = {}
        self._report_path = Path(__file__).parent.parent / "data" / "health_status.json"
        self._last_update = 0.0

    def update(self, **kwargs) -> None:
        """Update health status for components."""
        for component, status in kwargs.items():
            self._status[component] = {
                "status": status.get("status", "unknown"),
                "message": status.get("message", ""),
                "last_check": time.time(),
            }

    def check_process(self) -> Dict:
        """Check engine process health."""
        try:
            pids = []
            for line in os.popen("pgrep -f 'python.*main.py'").read().strip().split("\n"):
                if line.strip():
                    pids.append(int(line.strip()))
            return {
                "status": "green" if len(pids) <= 1 else "red",
                "message": f"{len(pids)} engine process(es)",
                "pids": pids,
            }
        except Exception as e:
            return {"status": "yellow", "message": str(e)}

    def check_database(self, db_path: str) -> Dict:
        """Check database connectivity."""
        try:
            import sqlite3
            conn = sqlite3.connect(db_path, timeout=5)
            cur = conn.execute("SELECT COUNT(*) FROM positions")
            count = cur.fetchone()[0]
            conn.close()
            return {
                "status": "green",
                "message": f"{count} positions",
            }
        except Exception as e:
            return {"status": "red", "message": str(e)}

    def check_websocket(self, status_data: Dict) -> Dict:
        """Check WebSocket connection status."""
        connected = status_data.get("ws_connected", False)
        return {
            "status": "green" if connected else "red",
            "message": "connected" if connected else "disconnected",
        }

    def check_bridge(self, bridge_dir: Path) -> Dict:
        """Check bridge file freshness."""
        try:
            status_file = bridge_dir / "status.json"
            if not status_file.exists():
                return {"status": "red", "message": "status.json missing"}
            age = time.time() - status_file.stat().st_mtime
            if age < 30:
                return {"status": "green", "message": f"{age:.0f}s old"}
            elif age < 120:
                return {"status": "yellow", "message": f"{age:.0f}s old"}
            else:
                return {"status": "red", "message": f"stale: {age:.0f}s"}
        except Exception as e:
            return {"status": "yellow", "message": str(e)}

    def get_overall_status(self) -> str:
        """Get overall system health: green/yellow/red."""
        statuses = [s.get("status", "unknown") for s in self._status.values()]
        if "red" in statuses:
            return "red"
        elif "yellow" in statuses:
            return "yellow"
        return "green"

    def get_dashboard_data(self) -> Dict:
        """Get health data formatted for dashboard display."""
        return {
            "overall": self.get_overall_status(),
            "components": self._status,
            "timestamp": time.time(),
        }

    def save_report(self) -> None:
        """Save health report to JSON."""
        try:
            self._report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._report_path, "w") as f:
                json.dump(self.get_dashboard_data(), f, indent=2, default=str)
        except Exception as e:
            logger.error("Failed to save health report: {}", e)


# Singleton
health_monitor = HealthMonitor()
