"""
📊 Monitoring Dashboard — Process Health & System Status
Standalone monitoring view for the always-on dashboard system.
Shows Streamlit process health, system resources, and bridge data freshness.

Usage:
    python monitoring_dashboard.py
    python monitoring_dashboard.py --watch  # Continuous monitoring mode
"""
from __future__ import annotations

import json
import csv
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# ── Path Setup ───────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
AI_ROOT = PROJECT_ROOT / "packages" / "ai-engine"
sys.path.insert(0, str(AI_ROOT))

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

BRIDGE_DIR = AI_ROOT / "data" / "bridge"
REPORTS_DIR = AI_ROOT / "data" / "reports"
DB_PATH = AI_ROOT / "data" / "database" / "arbitrage.db"
ENGINE_LOG = AI_ROOT / "data" / "logs" / "engine.log"
PID_FILE = PROJECT_ROOT / ".dashboard.pid"
LOG_FILE = PROJECT_ROOT / "dashboard.log"


def check_system_processes() -> Dict:
    """Check if dashboard components (API & UI) are running."""
    result = {
        "ui": {"running": False, "pid": None, "memory_mb": 0},
        "api": {"running": False, "port": 3001}
    }

    if not PID_FILE.exists():
        return result

    try:
        pid = int(PID_FILE.read_text().strip())
        # UI Check
        if not HAS_PSUTIL:
            # Without psutil, fall back to simple port ping
            import socket
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                result["ui"]["running"] = s.connect_ex(('localhost', 8501)) == 0
            result["ui"]["pid"] = pid
        else:
            proc = psutil.Process(pid)
            result["ui"]["running"] = proc.is_running()
            result["ui"]["pid"] = pid
            result["ui"]["memory_mb"] = proc.memory_info().rss / 1024 / 1024

        # API Check (Simple port ping)
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            result["api"]["running"] = s.connect_ex(('localhost', 3001)) == 0
            
    except (ProcessLookupError, ValueError, OSError):
        pass
    except Exception:
        pass

    return result


def check_bridge_data() -> Dict:
    """Check bridge data freshness."""
    files = {
        "signals": BRIDGE_DIR / "signals.json",
        "metrics": BRIDGE_DIR / "metrics.json",
        "status": BRIDGE_DIR / "status.json",
        "market_intel": BRIDGE_DIR / "market_intelligence.json",
        "positions": BRIDGE_DIR / "positions.json",
        "equity_history": BRIDGE_DIR / "equity_history.json",
        "trade_history": BRIDGE_DIR / "trade_history.json",
    }

    result = {}
    now = time.time()

    for name, filepath in files.items():
        if filepath.exists():
            try:
                with open(filepath) as f:
                    data = json.load(f)
                ts = data.get("timestamp", 0)
                age = now - ts
                result[name] = {
                    "exists": True,
                    "age_sec": round(age, 1),
                    "fresh": age < 300,
                    "size_kb": filepath.stat().st_size / 1024,
                }
            except (json.JSONDecodeError, Exception):
                result[name] = {"exists": True, "age_sec": -1, "fresh": False, "size_kb": 0}
        else:
            result[name] = {"exists": False, "age_sec": -1, "fresh": False, "size_kb": 0}

    return result


def check_trading_events() -> Dict:
    """Check for recent rejections and arbitrage detections."""
    events = {"rejections": [], "arbitrage_count": 0, "recent_scans": []}
    
    # Check Allocation Rejections
    alloc_log = REPORTS_DIR / "allocation_log.csv"
    if alloc_log.exists():
        try:
            with open(alloc_log, 'r') as f:
                reader = csv.DictReader(f)
                # Get last 5 rejections
                rows = list(reader)
                rejections = [r for r in rows if float(r.get("allocation_pct", 1)) == 0]
                events["rejections"] = rejections[-5:]
        except Exception:
            pass

    # Check Arbitrage DB
    if DB_PATH.exists():
        conn = None
        try:
            import sqlite3
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM opportunities")
            count_result = cursor.fetchone()
            events["arbitrage_count"] = count_result[0] if count_result else 0
            
            # Get last 5 scan results (opportunities detected)
            cursor.execute("SELECT type, symbol, edge, timestamp FROM opportunities ORDER BY timestamp DESC LIMIT 5")
            events["recent_scans"] = cursor.fetchall()

            # Get last successful execution
            cursor.execute("SELECT status, profit, timestamp FROM executions ORDER BY timestamp DESC LIMIT 1")
            events["last_execution"] = cursor.fetchone()
        except Exception:
            events["arbitrage_count"] = 0
        finally:
            if conn:
                conn.close()
            
    return events


def check_system_resources() -> Dict:
    """Check system resource usage."""
    result = {"cpu_pct": 0, "memory_pct": 0, "memory_available_gb": 0, "disk_free_gb": 0}

    if not HAS_PSUTIL:
        return result

    result["cpu_pct"] = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    result["memory_pct"] = mem.percent
    result["memory_available_gb"] = mem.available / 1024 / 1024 / 1024

    disk = psutil.disk_usage("/")
    result["disk_free_gb"] = disk.free / 1024 / 1024 / 1024

    return result


def check_dashboard_health() -> bool:
    """Check if dashboard HTTP endpoint is responsive."""
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:8501/_stcore/health", method="GET")
        response = urllib.request.urlopen(req, timeout=5)
        return response.status == 200
    except Exception:
        return False

def scan_engine_logs() -> List[str]:
    """Scan engine logs for critical trading events."""
    if not ENGINE_LOG.exists():
        return []

    # Updated keywords based on recent monitoring patterns
    keywords = ["REJECTED", "SIGNAL", "ARB", "ABORTED", "EXECUTED", "RECOVERY", "SCAN", "ELITE", "LOADED", "SYMBOLS"]
    pattern = re.compile("|".join(keywords), re.IGNORECASE)
    recent_events = []
    try:
        with open(ENGINE_LOG, 'r') as f:
            # Read last 100 complete lines to avoid splitting mid-line
            all_lines = f.readlines()
            for line in all_lines[-100:]:
                if pattern.search(line):
                    recent_events.append(line.strip())
    except Exception as e:
        recent_events.append(f"Error scanning logs: {str(e)}")
    
    return recent_events[-10:]


def print_status():
    """Print comprehensive status report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    print()
    print("═" * 60)
    print(f"📊 DeltaTerminal — System Monitor")
    print(f"🕐 {now}")
    print("═" * 60)

    # Component Health
    procs = check_system_processes()
    ui_icon = "🟢" if procs["ui"]["running"] else "🔴"
    api_icon = "🟢" if procs["api"]["running"] else "🔴"
    
    print(f"\n🖥️  Dashboard Components")
    print(f"   {ui_icon} UI (Streamlit): {'RUNNING' if procs['ui']['running'] else 'OFFLINE'}")
    if procs["ui"]["running"]:
        print(f"      PID: {procs['ui']['pid']} | Mem: {procs['ui']['memory_mb']:.1f}MB")
    
    print(f"   {api_icon} API (FastAPI):  {'RUNNING' if procs['api']['running'] else 'OFFLINE'}")

    # Dashboard HTTP Health
    http_ok = check_dashboard_health()
    http_icon = "🟢" if http_ok else "🔴"
    print(f"\n{http_icon} UI Accessibility")
    if http_ok:
        print(f"   Endpoint: http://localhost:8501 (OK)")
    else:
        print(f"   Endpoint: http://localhost:8501 (UNREACHABLE)")

    # Bridge Data
    bridge = check_bridge_data()
    print(f"\n📡 Bridge Data")
    for name, info in bridge.items():
        icon = "🟢" if info["fresh"] else ("🟡" if info["exists"] else "🔴")
        age = f"{info['age_sec']:.0f}s ago" if info["exists"] else "missing"
        size = f"{info['size_kb']:.1f}KB" if info["exists"] else "—"
        print(f"   {icon} {name:20s} {age:15s} {size}")

    # Trading Events
    events = check_trading_events()
    print(f"\n📈 Arbitrage & Signal Summary")
    print(f"   🔍 Opportunities Detected: {events['arbitrage_count']}")
    if events["recent_scans"]:
        print(f"      Recent Detections:")
        for s_type, sym, edge, ts in events["recent_scans"]:
            ts_str = datetime.fromtimestamp(ts/1000).strftime("%H:%M:%S")
            print(f"      • {ts_str} | {s_type:20s} | {sym:10s} | {edge:6.2f} bps")

    if "last_execution" in events and events["last_execution"]:
        status, profit, _ = events["last_execution"]
        exec_icon = "🟢" if status == "COMPLETED" else "🔴"
        print(f"   {exec_icon} Last Execution: {status} (${profit:.2f})")
    
    if events["rejections"]:
        print(f"\n   🚫 Recent Rejections:")
        for r in events["rejections"]:
            print(f"      • {r['timestamp'][-8:]} | {r['symbol']} | {r['reason']}")
    else:
        print(f"   🟢 No recent rejections.")


    # System Resources
    sys_info = check_system_resources()
    print(f"\n💻 System Resources")
    cpu_color = "🟢" if sys_info["cpu_pct"] < 70 else ("🟡" if sys_info["cpu_pct"] < 90 else "🔴")
    mem_color = "🟢" if sys_info["memory_pct"] < 70 else ("🟡" if sys_info["memory_pct"] < 90 else "🔴")
    print(f"   {cpu_color} CPU: {sys_info['cpu_pct']:.1f}%")
    print(f"   {mem_color} Memory: {sys_info['memory_pct']:.1f}% ({sys_info['memory_available_gb']:.1f}GB free)")
    print(f"   🟢 Disk: {sys_info['disk_free_gb']:.1f}GB free")

    # Engine Log Events
    engine_events = scan_engine_logs()
    if engine_events:
        print(f"\n⚙️ Recent Engine Events (Filtered)")
        for event in engine_events:
            print(f"   • {event}")

    # Log tail
    if LOG_FILE.exists():
        print(f"\n📝 Filtered Dashboard Events ({LOG_FILE})")
        # Apply the user's specific grep pattern: SCAN|SIGNAL|REJECTED|ELITE|Loaded|symbols
        ui_pattern = re.compile(r"SCAN|SIGNAL|REJECTED|ELITE|Loaded|symbols", re.IGNORECASE)
        try:
            lines = LOG_FILE.read_text().strip().split("\n")
            filtered_lines = [line for line in lines if ui_pattern.search(line)]
            for line in filtered_lines[-8:]:
                print(f"   {line}")
        except Exception:
            print("   (could not read log)")

    print()
    print("═" * 60)


def watch_mode(interval: int = 10):
    """Continuous monitoring mode."""
    print("🔄 Watch mode — refreshing every {interval}s (Ctrl+C to stop)")
    try:
        while True:
            os.system("clear" if os.name != "nt" else "cls")
            print_status()
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n👋 Monitoring stopped.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="DeltaTerminal System Monitor")
    parser.add_argument("--watch", "-w", action="store_true", help="Continuous monitoring mode")
    parser.add_argument("--interval", "-i", type=int, default=10, help="Refresh interval in seconds")
    args = parser.parse_args()

    if args.watch:
        watch_mode(args.interval)
    else:
        print_status()
