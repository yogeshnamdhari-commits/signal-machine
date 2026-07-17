#!/usr/bin/env python3
"""
YOG'Z Signal Machine — Supervisor Watchdog
Keeps engine + dashboard running 24/7 with auto-restart.
Prevents macOS sleep. Survives crashes.
"""
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AI_ROOT = PROJECT_ROOT / "packages" / "ai-engine"
VENV_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"
STREAMLIT = PROJECT_ROOT / ".venv" / "bin" / "streamlit"
LOG_DIR = AI_ROOT / "data" / "logs"
PID_DIR = PROJECT_ROOT / "service"
LOG_DIR.mkdir(parents=True, exist_ok=True)
PID_DIR.mkdir(parents=True, exist_ok=True)

HEALTH_CHECK_INTERVAL = 30  # seconds
STARTUP_DELAY = 10  # seconds to wait after starting a process
MAX_RESTARTS = 20
RESTART_COOLDOWN = 300  # 5 min cooldown after too many restarts

engine_proc = None
dashboard_proc = None
caffeinate_proc = None
restart_counts = {"engine": 0, "dashboard": 0}
last_healthy_time = time.time()


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_DIR / "supervisor.log", "a") as f:
        f.write(line + "\n")


def prevent_sleep():
    global caffeinate_proc
    if caffeinate_proc and caffeinate_proc.poll() is None:
        return
    caffeinate_proc = subprocess.Popen(
        ["caffeinate", "-s", "-i", "-d", "-w", "1"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    log(f"🛡️  caffeinate active (PID: {caffeinate_proc.pid})")


def start_engine() -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    # Aggressively kill ALL stale engine processes + clean lock/PID files
    import subprocess as _sp
    _sp.run(["pkill", "-9", "-f", "main.py.*engine"], capture_output=True)
    time.sleep(3)
    # Verify no engine processes remain
    for _ in range(5):
        result = _sp.run(["pgrep", "-f", "main.py.*engine"], capture_output=True, text=True)
        if not result.stdout.strip():
            break
        time.sleep(2)
    lock_path = AI_ROOT / "data" / "engine.lock"
    pid_path = AI_ROOT / "data" / "engine.pid"
    try:
        if lock_path.exists():
            lock_path.unlink()
        if pid_path.exists():
            pid_path.unlink()
    except Exception:
        pass
    p = subprocess.Popen(
        [str(VENV_PYTHON), str(AI_ROOT / "main.py"), "--mode", "engine"],
        stdout=open(LOG_DIR / "engine_service.log", "a"),
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    log(f"🧠 Engine started (PID: {p.pid})")
    return p


def start_dashboard() -> subprocess.Popen:
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(PROJECT_ROOT / ".venv")
    env["PATH"] = f"{PROJECT_ROOT / '.venv' / 'bin'}:{env.get('PATH', '')}"
    # Kill ALL stale streamlit processes and wait for port to free
    import subprocess as _sp
    _sp.run(["pkill", "-f", "streamlit"], capture_output=True)
    time.sleep(3)
    # Verify port is free
    for _attempt in range(5):
        try:
            import socket
            _s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            _s.settimeout(1)
            _result = _s.connect_ex(("127.0.0.1", 8501))
            _s.close()
            if _result != 0:
                break  # Port is free
        except Exception:
            break
        time.sleep(2)
    cmd = [
        str(STREAMLIT), "run", str(AI_ROOT / "dashboard" / "app.py"),
        "--server.port", "8501",
        "--server.address", "0.0.0.0",
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--server.enableCORS", "false",
        "--server.enableXsrfProtection", "false",
        "--theme.base", "dark",
        "--theme.primaryColor", "#00ff88",
        "--theme.backgroundColor", "#0e1117",
        "--theme.secondaryBackgroundColor", "#1a1a2e",
        "--theme.textColor", "#e0e0e0",
    ]
    p = subprocess.Popen(
        cmd,
        stdout=open(LOG_DIR / "dashboard_service.log", "a"),
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    log(f"📊 Dashboard started (PID: {p.pid})")
    return p


def is_alive(proc) -> bool:
    return proc is not None and proc.poll() is None


def http_healthy() -> bool:
    try:
        url = "http://localhost:8501/_stcore/health"
        req = urllib.request.Request(url, method="GET")
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status == 200
    except Exception:
        return False


def shutdown(sig=None, frame=None):
    log("🛑 Shutdown signal received — stopping all services...")
    for p in [engine_proc, dashboard_proc, caffeinate_proc]:
        if p and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
    log("✅ All services stopped.")
    sys.exit(0)


def main():
    global engine_proc, dashboard_proc, last_healthy_time

    log("═══════════════════════════════════════════════════════════════")
    log("⚡ YOG'Z Signal Machine — Supervisor Watchdog v1")
    log("═══════════════════════════════════════════════════════════════")

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    prevent_sleep()

    # ── Initial start ─────────────────────────────────────────
    engine_proc = start_engine()
    log(f"⏳ Waiting {STARTUP_DELAY}s for engine...")
    time.sleep(STARTUP_DELAY)

    dashboard_proc = start_dashboard()
    log(f"⏳ Waiting {STARTUP_DELAY}s for dashboard...")
    time.sleep(STARTUP_DELAY)

    # Wait for dashboard HTTP
    for i in range(12):
        if http_healthy():
            log("✅ Dashboard HTTP healthy")
            break
        time.sleep(5)
    else:
        log("⚠️  Dashboard HTTP not yet responsive — continuing, will retry")

    log("🟢 All services started. Monitoring...")

    # ── Watchdog loop ─────────────────────────────────────────
    while True:
        time.sleep(HEALTH_CHECK_INTERVAL)

        # Keep caffeinate alive
        if not is_alive(caffeinate_proc):
            prevent_sleep()

        # Engine check
        if not is_alive(engine_proc):
            restart_counts["engine"] += 1
            if restart_counts["engine"] > MAX_RESTARTS:
                log(f"🚨 Engine restart limit ({MAX_RESTARTS}) — cooling down")
                time.sleep(RESTART_COOLDOWN)
                restart_counts["engine"] = 0
            log(f"🔄 Engine died — restarting (#{restart_counts['engine']})")
            engine_proc = start_engine()

        # Dashboard check — use HTTP health instead of PID (Streamlit forks children)
        if not http_healthy():
            restart_counts["dashboard"] += 1
            if restart_counts["dashboard"] > MAX_RESTARTS:
                log(f"🚨 Dashboard restart limit ({MAX_RESTARTS}) — cooling down")
                time.sleep(RESTART_COOLDOWN)
                restart_counts["dashboard"] = 0
            log(f"🔄 Dashboard unhealthy — restarting (#{restart_counts['dashboard']})")
            dashboard_proc = start_dashboard()
            time.sleep(STARTUP_DELAY)
        else:
            # Dashboard healthy — reset restart counter
            restart_counts["dashboard"] = 0

        # HTTP health — check every cycle for responsive restart
        if is_alive(dashboard_proc):
            if http_healthy():
                last_healthy_time = time.time()
                restart_counts["dashboard"] = 0  # Reset on healthy
            else:
                # Only kill after 3 consecutive failures (~90s total)
                _unhealthy_secs = time.time() - last_healthy_time
                if _unhealthy_secs > 90:
                    log(f"🔄 Dashboard unresponsive for {_unhealthy_secs:.0f}s — restarting...")
                    restart_counts["dashboard"] += 1
                    if restart_counts["dashboard"] > MAX_RESTARTS:
                        log(f"🚨 Dashboard restart limit ({MAX_RESTARTS}) — cooling down {RESTART_COOLDOWN}s")
                        time.sleep(RESTART_COOLDOWN)
                        restart_counts["dashboard"] = 0
                    try:
                        dashboard_proc.terminate()
                        dashboard_proc.wait(timeout=5)
                    except Exception:
                        try:
                            dashboard_proc.kill()
                        except Exception:
                            pass
                    dashboard_proc = start_dashboard()
                    time.sleep(STARTUP_DELAY)
                    last_healthy_time = time.time()  # Reset timer after restart

        # Reset counters when healthy
        if is_alive(engine_proc) and is_alive(dashboard_proc):
            restart_counts["engine"] = 0
            restart_counts["dashboard"] = 0


if __name__ == "__main__":
    main()
