#!/usr/bin/env python3
"""
🚀 DeltaTerminal — Always-On Dashboard Launcher

Starts the Streamlit dashboard as a managed background process with:
- Auto-restart on crash (watchdog)
- Auto-open browser on first launch
- Health monitoring (ping dashboard every 30s)
- Graceful shutdown on Ctrl+C
- PID file management for singleton execution
- Log rotation

Usage:
    python launch_dashboard.py                  # Start with defaults
    python launch_dashboard.py --port 8501      # Custom port
    python launch_dashboard.py --no-browser     # Don't auto-open browser
    python launch_dashboard.py --stop           # Stop running instance

The dashboard NEVER stops working — if Streamlit crashes, it restarts automatically.
"""
from __future__ import annotations

import argparse
import atexit
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Paths ────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent
AI_ROOT = PROJECT_ROOT / "packages" / "ai-engine"
DASHBOARD_DIR = AI_ROOT / "dashboard"
APP_FILE = DASHBOARD_DIR / "app.py"
API_FILE = AI_ROOT / "execution" / "server.py"
ENGINE_FILE = AI_ROOT / "main.py"
PID_FILE = PROJECT_ROOT / ".dashboard.pid"
LOG_FILE = PROJECT_ROOT / "dashboard.log"
LOCK_FILE = PROJECT_ROOT / ".dashboard.lock"
LAUNCHER_PID_FILE = PROJECT_ROOT / ".launcher.pid"

# ── Default Config ───────────────────────────────────────────────
DEFAULT_PORT = 8501
DEFAULT_HOST = "0.0.0.0"
HEALTH_CHECK_INTERVAL = 30  # seconds
MAX_RESTART_DELAY = 60  # max backoff delay
RESTART_DELAY_INITIAL = 2  # initial restart delay


class DashboardLauncher:
    """
    Always-on dashboard launcher with watchdog and auto-restart.
    """

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        host: str = DEFAULT_HOST,
        open_browser: bool = True,
        log_file: Path = LOG_FILE,
        prevent_sleep: bool = True,
    ):
        self.port = port
        self.host = host
        self.open_browser = open_browser
        self.log_file = log_file
        self.prevent_sleep = prevent_sleep
        self.process: Optional[subprocess.Popen] = None
        self.api_process: Optional[subprocess.Popen] = None
        self.engine_process: Optional[subprocess.Popen] = None
        self.caffeinate_process: Optional[subprocess.Popen] = None
        self.running = False
        self.restart_count = 0
        self.restart_delay = RESTART_DELAY_INITIAL
        self._health_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()
        self._browser_opened = False

    # ── Singleton Lock ───────────────────────────────────────────

    def _acquire_lock(self) -> bool:
        """Ensure only one instance runs at a time."""
        if LAUNCHER_PID_FILE.exists():
            try:
                old_pid = int(LAUNCHER_PID_FILE.read_text().strip())
                # Check if process is actually running
                os.getpgid(old_pid)
                print(f"⚠️  Dashboard already running (PID {old_pid})")
                return False
            except (ProcessLookupError, ValueError, OSError):
                LAUNCHER_PID_FILE.unlink(missing_ok=True)

        LAUNCHER_PID_FILE.write_text(str(os.getpid()))
        return True

    def _release_lock(self):
        """Release launcher and dashboard locks."""
        LAUNCHER_PID_FILE.unlink(missing_ok=True)
        PID_FILE.unlink(missing_ok=True)

    # ── Sleep Prevention (caffeinate) ──────────────────────────

    def _start_caffeinate(self):
        """Start caffeinate to prevent macOS from sleeping."""
        if not self.prevent_sleep:
            return
        try:
            self.caffeinate_process = subprocess.Popen(
                ["caffeinate", "-s", "-i", "-d"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"🛡️  caffeinate active (PID: {self.caffeinate_process.pid}) — system will not sleep")
        except FileNotFoundError:
            print("⚠️  caffeinate not found — sleep prevention disabled")

    def _stop_caffeinate(self):
        """Stop caffeinate process."""
        if self.caffeinate_process and self.caffeinate_process.poll() is None:
            try:
                self.caffeinate_process.terminate()
                self.caffeinate_process.wait(timeout=3)
            except Exception:
                pass
            self.caffeinate_process = None

    # ── Streamlit Process Management ─────────────────────────────

    def _build_command(self) -> list:
        """Build the Streamlit run command."""
        # Find streamlit executable in venv or system
        venv_python = AI_ROOT / "venv" / "bin" / "python"
        if venv_python.exists():
            streamlit = str(AI_ROOT / "venv" / "bin" / "streamlit")
        else:
            streamlit = "streamlit"

        return [
            streamlit, "run", str(APP_FILE),
            "--server.port", str(self.port),
            "--server.address", self.host,
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

    def _cleanup_port(self):
        """Kill any process holding the dashboard port."""
        try:
            import psutil
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    for conns in proc.connections(kind='inet'):
                        if conns.laddr.port == self.port:
                            print(f"🔨 Cleaning up zombie on port {self.port} (PID {proc.pid})")
                            proc.terminate()
                            proc.wait(timeout=3)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            # Non-blocking fallback
            if sys.platform != "win32":
                subprocess.run(["fuser", "-k", f"{self.port}/tcp"], capture_output=True)

    def _start_streamlit(self) -> subprocess.Popen:
        """Start Streamlit as a subprocess."""
        self._cleanup_port()
        cmd = self._build_command()

        log_fh = open(self.log_file, "a")
        log_fh.write(f"\n{'='*60}\n")
        log_fh.write(f"[{datetime.now().isoformat()}] Starting Streamlit (attempt {self.restart_count + 1})\n")
        log_fh.write(f"Command: {' '.join(cmd)}\n")
        log_fh.flush()

        # Set environment for venv
        env = os.environ.copy()
        venv_bin = AI_ROOT / "venv" / "bin"
        if venv_bin.exists():
            env["VIRTUAL_ENV"] = str(AI_ROOT / "venv")
            env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"

        # Start API Server first
        if not self.api_process or self.api_process.poll() is not None:
            venv_python = AI_ROOT / "venv" / "bin" / "python"
            api_python = str(venv_python) if venv_python.exists() else sys.executable
            self.api_process = subprocess.Popen(
                [api_python, str(API_FILE)],
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=str(AI_ROOT),
                env=env,
            )

        # Start Engine (main.py --mode engine)
        if not self.engine_process or self.engine_process.poll() is not None:
            venv_python = AI_ROOT / "venv" / "bin" / "python"
            engine_python = str(venv_python) if venv_python.exists() else sys.executable
            self.engine_process = subprocess.Popen(
                [engine_python, str(ENGINE_FILE), "--mode", "engine"],
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=str(AI_ROOT),
                env=env,
            )
            log_fh.write(f"[{datetime.now().isoformat()}] Engine started with PID {self.engine_process.pid}\n")
            log_fh.flush()

        process = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(AI_ROOT),
            start_new_session=True,  # Create new process group for clean shutdown (cross-platform)
        )

        log_fh.write(f"[{datetime.now().isoformat()}] Streamlit started with PID {process.pid}\n")
        log_fh.flush()
        PID_FILE.write_text(str(process.pid)) # Log the ACTUAL UI PID for the monitor

        return process

    def _stop_streamlit(self):
        """Gracefully stop Streamlit."""
        if self.process and self.process.poll() is None:
            try:
                # Send SIGTERM to process group
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                self.process.wait(timeout=10)
            except (subprocess.TimeoutExpired, ProcessLookupError):
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.process = None
            
        if self.api_process and self.api_process.poll() is None:
            try:
                self.api_process.terminate()
                self.api_process.wait(timeout=5)
            except Exception:
                pass

        if self.engine_process and self.engine_process.poll() is None:
            try:
                self.engine_process.terminate()
                self.engine_process.wait(timeout=5)
            except Exception:
                pass
    # ── Health Check ─────────────────────────────────────────────

    def _health_check(self) -> bool:
        """Check if Streamlit is responsive."""
        try:
            import urllib.request
            url = f"http://localhost:{self.port}/_stcore/health"
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", "DeltaTerminal-Launcher/1.0")
            response = urllib.request.urlopen(req, timeout=5)
            return response.status == 200
        except Exception:
            return False

    def _health_monitor(self):
        """Background thread that monitors Streamlit and Engine health."""
        while not self._shutdown_event.is_set():
            self._shutdown_event.wait(HEALTH_CHECK_INTERVAL)
            if self._shutdown_event.is_set():
                break

            # Check engine process
            if self.engine_process and self.engine_process.poll() is not None:
                print(f"⚠️  Engine exited (code: {self.engine_process.returncode}). Restarting engine...")
                self._restart_engine()
                continue

            if self.process is None or self.process.poll() is not None:
                # Process died — trigger restart
                exit_code = self.process.returncode if self.process else "unknown"
                print(f"⚠️  Streamlit exited (code: {exit_code}). Restarting...")
                self._restart_streamlit()
            elif not self._health_check():
                # Process running but unresponsive — restart
                print(f"⚠️  Streamlit unresponsive. Restarting...")
                self._restart_streamlit()
            else:
                # Healthy — reset backoff
                self.restart_delay = RESTART_DELAY_INITIAL

    def _restart_engine(self):
        """Restart just the engine process."""
        env = os.environ.copy()
        venv_bin = AI_ROOT / "venv" / "bin"
        if venv_bin.exists():
            env["VIRTUAL_ENV"] = str(AI_ROOT / "venv")
            env["PATH"] = f"{venv_bin}:{env.get('PATH', '')}"

        venv_python = AI_ROOT / "venv" / "bin" / "python"
        engine_python = str(venv_python) if venv_python.exists() else sys.executable
        with open(self.log_file, "a") as log_fh:
            self.engine_process = subprocess.Popen(
                [engine_python, str(ENGINE_FILE), "--mode", "engine"],
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                cwd=str(AI_ROOT),
                env=env,
            )
            log_fh.write(f"[{datetime.now().isoformat()}] Engine restarted with PID {self.engine_process.pid}\n")
            log_fh.flush()

    def _restart_streamlit(self):
        """Restart Streamlit with exponential backoff."""
        self._stop_streamlit()
        self.restart_count += 1

        if self.restart_count > 5:
            delay = min(self.restart_delay * 2, MAX_RESTART_DELAY)
            self.restart_delay = delay
        else:
            delay = RESTART_DELAY_INITIAL

        print(f"🔄 Restarting in {delay}s... (restart #{self.restart_count})")
        time.sleep(delay)

        self.process = self._start_streamlit()

        # Wait for it to become healthy
        for _ in range(30):  # 30 seconds max
            time.sleep(1)
            if self._health_check():
                print(f"✅ Streamlit recovered! (PID: {self.process.pid})")
                self._open_browser()
                return

        print(f"⚠️  Streamlit started but health check pending...")

    # ── Browser ──────────────────────────────────────────────────

    def _open_browser(self):
        """Open the dashboard in the default browser."""
        if not self.open_browser or self._browser_opened:
            return

        url = f"http://localhost:{self.port}"
        live_sheet_url = f"http://localhost:{self.port}/_Live_Sheet"

        print(f"🌐 Opening dashboard: {url}")
        print(f"📡 Live Sheet: {live_sheet_url}")

        try:
            webbrowser.open(url)
            self._browser_opened = True
        except Exception as e:
            print(f"⚠️  Could not auto-open browser: {e}")
            print(f"   Please open manually: {url}")

    # ── Main Lifecycle ───────────────────────────────────────────

    def start(self):
        """Start the always-on dashboard."""
        if not self._acquire_lock():
            return

        self.running = True

        # Register cleanup
        atexit.register(self.stop)
        signal.signal(signal.SIGINT, lambda *_: self.stop())
        signal.signal(signal.SIGTERM, lambda *_: self.stop())

        print("=" * 60)
        print("⚡ DeltaTerminal — Always-On Dashboard Launcher")
        print("=" * 60)
        print(f"📊 Dashboard:  http://localhost:{self.port}")
        print(f"📡 Live Sheet: http://localhost:{self.port}/_Live_Sheet")
        print(f"🧠 Engine:     main.py --mode engine")
        print(f"📡 API Bridge: server.py (port 3001)")
        print(f"🖥️  Host:       {self.host}")
        print(f"📝 Log:        {self.log_file}")
        print(f"🔧 PID:        {os.getpid()}")
        if self.prevent_sleep:
            print(f"🛡️  Sleep:      PREVENTED (caffeinate)")
        print("=" * 60)
        print()
        print("🔄 Watchdog active — auto-restart on crash")
        print("   Press Ctrl+C to stop")
        print()

        # Prevent macOS sleep
        self._start_caffeinate()

        # Start Streamlit
        self.process = self._start_streamlit()
        self.restart_count = 0

        # Wait for initial startup
        print("⏳ Waiting for Streamlit to start...")
        for i in range(60):
            time.sleep(1)
            if self._health_check():
                print(f"✅ Streamlit is running! (PID: {self.process.pid})")
                break
            if self.process.poll() is not None:
                print(f"❌ Streamlit failed to start. Check {self.log_file}")
                self._release_lock()
                return
        else:
            print(f"⚠️  Streamlit may still be starting...")

        # Auto-open browser
        self._open_browser()

        # Start health monitor
        self._health_thread = threading.Thread(
            target=self._health_monitor,
            daemon=True,
            name="dashboard-watchdog",
        )
        self._health_thread.start()

        print()
        print("🟢 Dashboard is LIVE and will stay running!")
        print()

        # Keep main thread alive
        try:
            while self.running and not self._shutdown_event.is_set():
                self._shutdown_event.wait(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self):
        """Stop the dashboard gracefully."""
        if not self.running:
            return

        self.running = False
        self._shutdown_event.set()

        print("\n🛑 Shutting down dashboard...")
        self._stop_streamlit()
        self._stop_caffeinate()
        self._release_lock()

        print("✅ Dashboard stopped.")


def stop_running_instance():
    """Stop a running dashboard instance by PID."""
    if not PID_FILE.exists():
        print("ℹ️  No running dashboard found.")
        return

    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        print(f"🛑 Sent SIGTERM to dashboard (PID {pid})")
        time.sleep(2)
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
            print(f"🔨 Sent SIGKILL to dashboard (PID {pid})")
        except ProcessLookupError:
            pass
        PID_FILE.unlink(missing_ok=True)
        print("✅ Dashboard stopped.")
    except (ProcessLookupError, ValueError):
        print("ℹ️  Stale PID file cleaned up.")
        PID_FILE.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(
        description="🚀 DeltaTerminal — Always-On Dashboard Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})")
    parser.add_argument("--host", type=str, default=DEFAULT_HOST, help=f"Host (default: {DEFAULT_HOST})")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    parser.add_argument("--no-sleep-prevention", action="store_true", help="Don't prevent macOS sleep")
    parser.add_argument("--stop", action="store_true", help="Stop running dashboard instance")
    args = parser.parse_args()

    if args.stop:
        stop_running_instance()
        return

    launcher = DashboardLauncher(
        port=args.port,
        host=args.host,
        open_browser=not args.no_browser,
        prevent_sleep=not args.no_sleep_prevention,
    )
    launcher.start()


if __name__ == "__main__":
    main()
