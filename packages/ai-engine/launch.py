#!/usr/bin/env python3
"""
DeltaTerminal — Full System Launcher
Runs the AI engine + Streamlit dashboard together.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VENV_PYTHON = Path.home() / "Documents/signal machine/.venv/bin/python"


def _check_deps() -> bool:
    """Verify all dependencies are installed."""
    required = ["loguru", "numpy", "pandas", "aiohttp", "streamlit", "plotly"]
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    if missing:
        print(f"❌ Missing packages: {', '.join(missing)}")
        print(f"   Run: pip install -r requirements.txt")
        return False
    return True


def launch_engine(testnet: bool = True) -> subprocess.Popen:
    """Launch the AI scanner engine."""
    cmd = [str(VENV_PYTHON), "main.py", "--mode", "engine"]
    if testnet:
        cmd.append("--testnet")

    print(f"🚀 Launching AI Engine ({'testnet' if testnet else 'production'})...")
    return subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def launch_api() -> subprocess.Popen:
    """Launch the FastAPI REST server."""
    cmd = [str(VENV_PYTHON), "main.py", "--mode", "api"]
    print(f"📡 Launching API Server → http://localhost:8000")
    return subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def launch_dashboard() -> subprocess.Popen:
    """Launch the Streamlit dashboard."""
    cmd = [
        str(VENV_PYTHON),
        "-m",
        "streamlit",
        "run",
        "dashboard/app.py",
        "--server.port", "8501",
        "--server.address", "localhost",
        "--theme.base", "dark",
        "--server.headless", "true",
    ]

    print(f"📊 Launching Dashboard → http://localhost:8501")
    return subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="DeltaTerminal Full System Launcher")
    parser.add_argument("--mode", choices=["engine", "dashboard", "api", "both", "all"], default="both")
    parser.add_argument("--testnet", action="store_true", default=True)
    parser.add_argument("--production", action="store_true")
    args = parser.parse_args()

    if not _check_deps():
        sys.exit(1)

    testnet = not args.production
    processes = []

    print("=" * 56)
    print("⚡ DeltaTerminal — AI-Powered Binance Futures Scanner")
    print("=" * 56)

    if args.mode in ("engine", "both"):
        p = launch_engine(testnet)
        processes.append(("Engine", p))
        time.sleep(2)

    if args.mode in ("dashboard", "both"):
        p = launch_dashboard()
        processes.append(("Dashboard", p))

    if args.mode == "api":
        p = launch_api()
        processes.append(("API", p))

    if args.mode == "all":
        processes.append(("API", launch_api()))
        processes.append(("Dashboard", launch_dashboard()))

    if not processes:
        print("❌ Nothing to launch")
        return

    print(f"\n✅ Running: {', '.join(name for name, _ in processes)}")
    print("   Press Ctrl+C to stop\n")

    def shutdown(sig, frame):
        print("\n🛑 Shutting down...")
        for name, p in processes:
            print(f"   Stopping {name}...")
            p.terminate()
        for _, p in processes:
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
        print("✅ All services stopped")
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Monitor processes
    try:
        while True:
            for name, p in processes:
                if p.poll() is not None:
                    print(f"⚠️ {name} exited with code {p.returncode}")
            time.sleep(5)
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
# packages/ai-engine/database/signal_repository.py

class SignalRepository:
    def __init__(self):
        pass

    async def initialize(self) -> None:
        """Initialize database connections."""
        pass

    async def disconnect(self) -> None:
        """Close database connections."""
        pass

    async def upsert_symbol(self, symbol: str, base: str, quote: str) -> None:
        """Save or update market symbol info."""
        pass

    async def save_signal(self, signal: dict) -> str:
        """Save a generated signal and return its ID."""
        return "some-id"

    async def update_signal_status(self, signal_id: str, status: str) -> None:
        """Update signal status (e.g., 'expired')."""
        pass

    async def get_open_positions(self) -> list:
        """Fetch current open positions."""
        return []

    async def close_position(self, position_id: str, pnl: float) -> None:
        """Mark a position as closed with final PnL."""
        pass

    async def cleanup_old_data(self, days: int) -> None:
        """Remove data older than X days."""
        pass

# The exported object expected by engine.py
repo = SignalRepository()
from database.signal_repository import repo as db
