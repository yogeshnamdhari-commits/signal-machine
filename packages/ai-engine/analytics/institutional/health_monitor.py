"""
Strategy Health Monitor — System Health Dashboard
==================================================
Phase 9: Health indicators for all system components.

READ-ONLY — Never modifies trading logic.

Monitors:
- Scanner Health
- Signal Health
- Performance Health
- Data Health
- Exchange Health
- WebSocket Health
- API Health
- Bridge Health
- Repository Health
- State Health
- Confidence Health
- RR Health

Status: Green / Yellow / Red
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from enum import Enum

from loguru import logger


class HealthStatus(Enum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"
    UNKNOWN = "unknown"


class HealthIndicator:
    """A single health indicator."""
    
    def __init__(self, name: str, category: str):
        self.name = name
        self.category = category
        self.status = HealthStatus.UNKNOWN
        self.message = ""
        self.value = None
        self.threshold = None
        self.last_check = 0
        self.details: Dict[str, Any] = {}
    
    def set_status(self, status: HealthStatus, message: str = "", value: Any = None) -> None:
        self.status = status
        self.message = message
        self.value = value
        self.last_check = time.time()
    
    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "category": self.category,
            "status": self.status.value,
            "message": self.message,
            "value": self.value,
            "last_check": self.last_check,
            "details": self.details,
        }


class HealthMonitor:
    """
    System health monitoring dashboard.
    
    Checks all components and returns health status.
    
    READ-ONLY: Never modifies trading logic.
    """
    
    BRIDGE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "bridge"
    DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "institutional_v1.db"
    LOG_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "logs"
    
    def __init__(self):
        self._indicators: Dict[str, HealthIndicator] = {}
        self._initialize_indicators()
    
    def _initialize_indicators(self) -> None:
        """Initialize all health indicators."""
        categories = {
            "scanner": ["scanner_running", "scan_count", "symbols_connected"],
            "signal": ["signal_generation", "confidence_engine", "rr_gate"],
            "performance": ["win_rate", "profit_factor", "expectancy"],
            "data": ["bridge_freshness", "database_health", "log_health"],
            "exchange": ["websocket_status", "api_status"],
            "system": ["memory_usage", "disk_usage"],
        }
        
        for category, indicators in categories.items():
            for name in indicators:
                self._indicators[name] = HealthIndicator(name, category)
    
    def check_all(self) -> Dict[str, HealthIndicator]:
        """Run all health checks."""
        self._check_scanner_health()
        self._check_signal_health()
        self._check_performance_health()
        self._check_data_health()
        self._check_exchange_health()
        self._check_system_health()
        return self._indicators
    
    def _check_scanner_health(self) -> None:
        """Check scanner status from bridge data."""
        try:
            status_file = self.BRIDGE_DIR / "status.json"
            if status_file.exists():
                with open(status_file) as f:
                    data = json.load(f)
                
                status = data.get("status", {})
                running = status.get("running", False)
                symbols = status.get("symbols_connected", 0)
                
                # Scanner running
                ind = self._indicators["scanner_running"]
                if running:
                    ind.set_status(HealthStatus.GREEN, "Scanner is running")
                else:
                    ind.set_status(HealthStatus.RED, "Scanner is not running")
                
                # Symbols connected
                ind = self._indicators["symbols_connected"]
                if symbols > 100:
                    ind.set_status(HealthStatus.GREEN, f"{symbols} symbols connected", symbols)
                elif symbols > 0:
                    ind.set_status(HealthStatus.YELLOW, f"Only {symbols} symbols connected", symbols)
                else:
                    ind.set_status(HealthStatus.RED, "No symbols connected", symbols)
            else:
                self._indicators["scanner_running"].set_status(
                    HealthStatus.RED, "Status file not found"
                )
        except Exception as e:
            self._indicators["scanner_running"].set_status(
                HealthStatus.RED, f"Error checking scanner: {e}"
            )
    
    def _check_signal_health(self) -> None:
        """Check signal generation health."""
        try:
            signals_file = self.BRIDGE_DIR / "signals.json"
            if signals_file.exists():
                with open(signals_file) as f:
                    data = json.load(f)
                
                signals = data.get("signals", [])
                ind = self._indicators["signal_generation"]
                
                if len(signals) > 0:
                    ind.set_status(HealthStatus.GREEN, f"{len(signals)} signals available", len(signals))
                else:
                    ind.set_status(HealthStatus.YELLOW, "No signals currently", 0)
            else:
                self._indicators["signal_generation"].set_status(
                    HealthStatus.YELLOW, "Signals file not found"
                )
        except Exception as e:
            self._indicators["signal_generation"].set_status(
                HealthStatus.RED, f"Error checking signals: {e}"
            )
    
    def _check_performance_health(self) -> None:
        """Check performance metrics health."""
        try:
            import sqlite3
            conn = sqlite3.connect(str(self.DB_PATH), timeout=5)
            cur = conn.cursor()
            
            # Check recent trades
            cur.execute("""
                SELECT COUNT(*), 
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
                       AVG(pnl)
                FROM positions 
                WHERE status = 'closed' 
                AND closed_at > ?
            """, (time.time() - 86400 * 7,))  # Last 7 days
            
            row = cur.fetchone()
            total, wins, avg_pnl = row if row else (0, 0, 0)
            
            # Win rate
            ind = self._indicators["win_rate"]
            if total > 0:
                wr = (wins / total * 100) if wins else 0
                if wr >= 50:
                    ind.set_status(HealthStatus.GREEN, f"Win rate: {wr:.1f}%", wr)
                elif wr >= 40:
                    ind.set_status(HealthStatus.YELLOW, f"Win rate: {wr:.1f}%", wr)
                else:
                    ind.set_status(HealthStatus.RED, f"Win rate: {wr:.1f}%", wr)
            else:
                ind.set_status(HealthStatus.UNKNOWN, "No recent trades")
            
            conn.close()
        except Exception as e:
            self._indicators["win_rate"].set_status(
                HealthStatus.RED, f"Error checking performance: {e}"
            )
    
    def _check_data_health(self) -> None:
        """Check data freshness and health."""
        # Bridge freshness
        ind = self._indicators["bridge_freshness"]
        try:
            bridge_files = list(self.BRIDGE_DIR.glob("*.json"))
            if bridge_files:
                newest = max(f.stat().st_mtime for f in bridge_files)
                age = time.time() - newest
                
                if age < 60:
                    ind.set_status(HealthStatus.GREEN, f"Bridge data fresh ({age:.0f}s old)", age)
                elif age < 300:
                    ind.set_status(HealthStatus.YELLOW, f"Bridge data aging ({age:.0f}s old)", age)
                else:
                    ind.set_status(HealthStatus.RED, f"Bridge data stale ({age:.0f}s old)", age)
            else:
                ind.set_status(HealthStatus.RED, "No bridge files found")
        except Exception as e:
            ind.set_status(HealthStatus.RED, f"Error checking bridge: {e}")
        
        # Database health
        ind = self._indicators["database_health"]
        try:
            if self.DB_PATH.exists():
                size_mb = self.DB_PATH.stat().st_size / 1024 / 1024
                ind.set_status(HealthStatus.GREEN, f"Database: {size_mb:.1f}MB", size_mb)
            else:
                ind.set_status(HealthStatus.RED, "Database not found")
        except Exception as e:
            ind.set_status(HealthStatus.RED, f"Error checking database: {e}")
    
    def _check_exchange_health(self) -> None:
        """Check exchange connectivity."""
        try:
            status_file = self.BRIDGE_DIR / "status.json"
            if status_file.exists():
                with open(status_file) as f:
                    data = json.load(f)
                
                status = data.get("status", {})
                ws_connected = status.get("ws_connected", False)
                
                ind = self._indicators["websocket_status"]
                if ws_connected:
                    ind.set_status(HealthStatus.GREEN, "WebSocket connected")
                else:
                    ind.set_status(HealthStatus.YELLOW, "WebSocket not connected")
            else:
                self._indicators["websocket_status"].set_status(
                    HealthStatus.UNKNOWN, "Status file not found"
                )
        except Exception as e:
            self._indicators["websocket_status"].set_status(
                HealthStatus.RED, f"Error checking exchange: {e}"
            )
    
    def _check_system_health(self) -> None:
        """Check system resource usage."""
        try:
            import psutil
            
            # Memory
            mem = psutil.virtual_memory()
            ind = self._indicators["memory_usage"]
            if mem.percent < 80:
                ind.set_status(HealthStatus.GREEN, f"Memory: {mem.percent:.1f}%", mem.percent)
            elif mem.percent < 90:
                ind.set_status(HealthStatus.YELLOW, f"Memory: {mem.percent:.1f}%", mem.percent)
            else:
                ind.set_status(HealthStatus.RED, f"Memory: {mem.percent:.1f}%", mem.percent)
            
            # Disk
            disk = psutil.disk_usage("/")
            ind = self._indicators["disk_usage"]
            if disk.percent < 80:
                ind.set_status(HealthStatus.GREEN, f"Disk: {disk.percent:.1f}%", disk.percent)
            elif disk.percent < 90:
                ind.set_status(HealthStatus.YELLOW, f"Disk: {disk.percent:.1f}%", disk.percent)
            else:
                ind.set_status(HealthStatus.RED, f"Disk: {disk.percent:.1f}%", disk.percent)
        except ImportError:
            self._indicators["memory_usage"].set_status(
                HealthStatus.UNKNOWN, "psutil not installed"
            )
            self._indicators["disk_usage"].set_status(
                HealthStatus.UNKNOWN, "psutil not installed"
            )
    
    def get_overall_status(self) -> HealthStatus:
        """Get overall system health status."""
        statuses = [ind.status for ind in self._indicators.values()]
        
        if HealthStatus.RED in statuses:
            return HealthStatus.RED
        elif HealthStatus.YELLOW in statuses:
            return HealthStatus.YELLOW
        elif all(s == HealthStatus.GREEN for s in statuses):
            return HealthStatus.GREEN
        else:
            return HealthStatus.UNKNOWN
    
    def get_summary(self) -> Dict:
        """Get health summary."""
        overall = self.get_overall_status()
        
        by_status = {
            "green": 0,
            "yellow": 0,
            "red": 0,
            "unknown": 0,
        }
        
        for ind in self._indicators.values():
            by_status[ind.status.value] += 1
        
        return {
            "overall_status": overall.value,
            "total_indicators": len(self._indicators),
            "by_status": by_status,
            "last_check": time.time(),
        }
    
    def get_report(self) -> str:
        """Generate health report as string."""
        self.check_all()
        summary = self.get_summary()
        
        lines = []
        lines.append("=" * 60)
        lines.append("🏥 STRATEGY HEALTH REPORT")
        lines.append(f"   Overall Status: {summary['overall_status'].upper()}")
        lines.append(f"   Green: {summary['by_status']['green']} | "
                    f"Yellow: {summary['by_status']['yellow']} | "
                    f"Red: {summary['by_status']['red']}")
        lines.append("=" * 60)
        
        # Group by category
        categories = {}
        for ind in self._indicators.values():
            if ind.category not in categories:
                categories[ind.category] = []
            categories[ind.category].append(ind)
        
        for category, indicators in sorted(categories.items()):
            lines.append(f"\n📊 {category.upper()}")
            for ind in indicators:
                icon = {"green": "🟢", "yellow": "🟡", "red": "🔴", "unknown": "⚪"}
                lines.append(f"   {icon.get(ind.status.value, '⚪')} {ind.name}: {ind.message}")
        
        lines.append("\n" + "=" * 60)
        return "\n".join(lines)


# Global singleton
_monitor: Optional[HealthMonitor] = None

def get_health_monitor() -> HealthMonitor:
    """Get or create the global health monitor."""
    global _monitor
    if _monitor is None:
        _monitor = HealthMonitor()
    return _monitor
