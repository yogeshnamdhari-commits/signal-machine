"""
DeltaTerminal — AI-Powered Binance Futures Scanner
Production entry point with graceful shutdown.
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from fastapi import FastAPI
    import uvicorn
except ImportError:
    FastAPI = None

from loguru import logger


def _setup_logging(level: str = "INFO") -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan> — <level>{message}</level>"
        ),
        level=level,
    )
    logger.add(
        "data/logs/engine_{time:YYYY-MM-DD}.log",
        rotation="1 day",
        retention="7 days",
        level="DEBUG",
    )

if FastAPI:
    from core.engine import DeltaTerminalEngine
    
    # Create a global engine instance for the API
    api_engine = DeltaTerminalEngine()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Handles the startup and shutdown of the DeltaTerminal Engine."""
        logger.info("Initializing Engine via API lifespan...")
        await api_engine.start()
        yield
        logger.info("Shutting down Engine via API lifespan...")
        await api_engine.stop()

    app = FastAPI(title="DeltaTerminal API", lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "engine": api_engine.get_status()}

    @app.get("/signals")
    async def get_signals():
        return {"count": len(api_engine.signals), "signals": api_engine.signals}
else:
    app = None


async def _acquire_engine_lock() -> bool:
    """Acquire singleton engine lock using fcntl.flock() to prevent race conditions.

    This uses an atomic file lock (not just a PID check) so that two processes
    starting at the exact same time cannot both acquire the lock.
    """
    import fcntl, os, time

    _data_dir = Path(__file__).parent / "data"
    _data_dir.mkdir(parents=True, exist_ok=True)
    _lock_path = _data_dir / "engine.lock"
    _pid_path = _data_dir / "engine.pid"

    # Step 1: open lock file and try non-blocking exclusive flock
    try:
        lock_fd = open(_lock_path, "w")
    except OSError as exc:
        logger.error("Cannot create lock file {}: {}", _lock_path, exc)
        return False

    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Another engine holds the lock
        # Read its PID for a helpful message
        try:
            old_pid = int(_pid_path.read_text().strip())
            alive = False
            try:
                os.kill(old_pid, 0)
                alive = True
            except OSError:
                pass
            if alive:
                logger.error(
                    "❌ Another engine is already running (PID {}). "
                    "Exiting to prevent duplicate trading. "
                    "Kill it first with: kill {}", old_pid, old_pid
                )
            else:
                logger.error(
                    "❌ Stale lock from dead PID {}. Cleaning up.", old_pid
                )
                _pid_path.unlink(missing_ok=True)
                lock_fd.close()
                _lock_path.unlink(missing_ok=True)
                # Retry once after cleanup
                return await _acquire_engine_lock()
        except (ValueError, OSError):
            logger.error("❌ Engine lock held by unknown process. Exiting.")
        lock_fd.close()
        return False

    # Step 2: Lock acquired — write our PID
    _pid_path.write_text(str(os.getpid()))
    # Keep fd open so the lock stays held for the lifetime of the process
    # Store ref so it isn't garbage-collected
    global _engine_lock_fd
    _engine_lock_fd = lock_fd

    logger.info("✅ Engine lock acquired. PID {} written to {}", os.getpid(), _pid_path)
    return True

# Module-level ref to prevent lock fd from being garbage-collected
_engine_lock_fd = None


def _release_engine_lock() -> None:
    """Release singleton engine lock."""
    import fcntl, os
    global _engine_lock_fd
    _pid_file = Path(__file__).parent / "data" / "engine.pid"
    _lock_file = Path(__file__).parent / "data" / "engine.lock"
    try:
        if _engine_lock_fd is not None:
            fcntl.flock(_engine_lock_fd, fcntl.LOCK_UN)
            _engine_lock_fd.close()
            _engine_lock_fd = None
        _pid_file.unlink(missing_ok=True)
        _lock_file.unlink(missing_ok=True)
    except OSError:
        pass
    logger.info("Engine lock released")


async def _run_engine() -> None:
    from core.engine import DeltaTerminalEngine

    if not await _acquire_engine_lock():
        return
    
    engine = DeltaTerminalEngine()
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _on_signal(sig):
        logger.info("Signal {} received", sig.name)
        stop.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, _on_signal, s)

    try:
        await engine.start()
        await stop.wait()
    except Exception as exc:
        logger.error("Fatal: {}", exc)
    finally:
        await engine.stop()
        _release_engine_lock()


def _run_dashboard() -> None:
    import subprocess
    from config import config

    path = Path(__file__).parent / "dashboard" / "app.py"
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(path),
        "--server.port", str(config.dashboard.port),
        "--server.address", config.dashboard.host,
        "--theme.base", "dark",
    ]
    logger.info("Dashboard → http://{}:{}", config.dashboard.host, config.dashboard.port)
    subprocess.run(cmd)


def _run_api() -> None:
    if not app:
        logger.error("FastAPI is not installed. Run 'pip install fastapi uvicorn'")
        return
    from config import config
    logger.info("Starting REST API on http://{}:8000", config.dashboard.host)
    uvicorn.run("main:app", host=config.dashboard.host, port=8000, reload=True)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="DeltaTerminal")
    parser.add_argument("--mode", choices=["engine", "dashboard", "api", "both"], default="engine")
    parser.add_argument("--testnet", action="store_true")
    args = parser.parse_args()

    if args.testnet:
        os.environ["BINANCE_TESTNET"] = "true"

    from config import config
    _setup_logging(config.log_level)

    logger.info("=" * 56)
    logger.info("⚡ DeltaTerminal — AI-Powered Binance Futures Scanner")
    logger.info("=" * 56)

    if args.mode == "engine":
        asyncio.run(_run_engine())
    elif args.mode == "dashboard":
        _run_dashboard()
    elif args.mode == "api":
        _run_api()
    elif args.mode == "both":
        import threading

        t = threading.Thread(target=lambda: asyncio.run(_run_engine()), daemon=True)
        t.start()
        _run_dashboard()


if __name__ == "__main__":
    main()
