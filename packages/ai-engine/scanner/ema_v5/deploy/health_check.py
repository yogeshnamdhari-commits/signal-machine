"""
EMA_V5 Health Check — Comprehensive health checks for all components.
Isolated from existing health check systems.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class EMAv5HealthCheck:
    """Performs health checks on all EMA_V5 components."""

    def __init__(self) -> None:
        self._results: List[Dict] = []

    def check_all(self) -> Dict[str, Any]:
        """Run all health checks."""
        self._results = []

        self._check_scanner()
        self._check_storage()
        self._check_bridge()
        self._check_database()
        self._check_state()
        self._check_logs()

        healthy = all(r["status"] == "ok" for r in self._results)

        return {
            "healthy": healthy,
            "timestamp": time.time(),
            "checks": self._results,
            "summary": {
                "total": len(self._results),
                "ok": sum(1 for r in self._results if r["status"] == "ok"),
                "warning": sum(1 for r in self._results if r["status"] == "warning"),
                "error": sum(1 for r in self._results if r["status"] == "error"),
            },
        }

    def _check_scanner(self) -> None:
        """Check scanner module health."""
        try:
            from ..scanner import EMAv5Scanner
            scanner = EMAv5Scanner()
            stats = scanner.get_stats()
            self._results.append({
                "name": "scanner",
                "status": "ok",
                "details": f"scan_count={stats.get('scan_count', 0)}, uptime={stats.get('uptime_sec', 0):.0f}s",
            })
        except Exception as e:
            self._results.append({
                "name": "scanner",
                "status": "error",
                "details": str(e),
            })

    def _check_storage(self) -> None:
        """Check storage module health."""
        try:
            from ..storage.database import EMAv5Database
            db = EMAv5Database()
            count = db.count_signals()
            self._results.append({
                "name": "storage",
                "status": "ok",
                "details": f"signals={count}",
            })
        except Exception as e:
            self._results.append({
                "name": "storage",
                "status": "error",
                "details": str(e),
            })

    def _check_bridge(self) -> None:
        """Check bridge file health."""
        bridge_path = Path("data/bridge/ema_v5.json")
        if bridge_path.exists():
            age = time.time() - bridge_path.stat().st_mtime
            if age > 300:
                status = "warning"
                details = f"Bridge file stale ({age:.0f}s old)"
            else:
                status = "ok"
                details = f"Bridge file fresh ({age:.0f}s old)"
        else:
            status = "warning"
            details = "Bridge file not found"

        self._results.append({
            "name": "bridge",
            "status": status,
            "details": details,
        })

    def _check_database(self) -> None:
        """Check database health."""
        db_path = Path("data/ema_v5_signals.db")
        if db_path.exists():
            try:
                import sqlite3
                conn = sqlite3.connect(str(db_path), timeout=5)
                cur = conn.execute("SELECT COUNT(*) FROM ema_v5_signals")
                count = cur.fetchone()[0]
                conn.close()
                self._results.append({
                    "name": "database",
                    "status": "ok",
                    "details": f"records={count}",
                })
            except Exception as e:
                self._results.append({
                    "name": "database",
                    "status": "error",
                    "details": str(e),
                })
        else:
            self._results.append({
                "name": "database",
                "status": "warning",
                "details": "Database file not found",
            })

    def _check_state(self) -> None:
        """Check state file health."""
        state_path = Path("data/ema_v5_state.json")
        if state_path.exists():
            try:
                import json
                with open(state_path) as f:
                    state = json.load(f)
                self._results.append({
                    "name": "state",
                    "status": "ok",
                    "details": f"symbols={len(state)}",
                })
            except Exception as e:
                self._results.append({
                    "name": "state",
                    "status": "error",
                    "details": str(e),
                })
        else:
            self._results.append({
                "name": "state",
                "status": "warning",
                "details": "State file not found",
            })

    def _check_logs(self) -> None:
        """Check log directory health."""
        log_dir = Path("data/logs")
        if log_dir.exists():
            log_files = list(log_dir.glob("*.log"))
            total_size = sum(f.stat().st_size for f in log_files)
            self._results.append({
                "name": "logs",
                "status": "ok",
                "details": f"files={len(log_files)}, size={total_size / 1024:.1f}KB",
            })
        else:
            self._results.append({
                "name": "logs",
                "status": "warning",
                "details": "Log directory not found",
            })

    def quick_check(self) -> Dict[str, Any]:
        """Quick health check — minimal checks."""
        try:
            from ..scanner import EMAv5Scanner
            scanner = EMAv5Scanner()
            return {"healthy": True, "scanner": "ok"}
        except Exception as e:
            return {"healthy": False, "scanner": str(e)}
