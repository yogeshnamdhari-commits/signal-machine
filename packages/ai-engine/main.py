"""
DeltaTerminal — AI-Powered Binance Futures Scanner
Production entry point with graceful shutdown.
"""
from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from fastapi import FastAPI
    import uvicorn
except ImportError:
    FastAPI = None

from loguru import logger

from config import config
from config.environment_contract import validate_runtime_config
from exchanges.integrity_patch import apply_integrity_patches
from execution.risk_integrity import apply_risk_integrity_patch


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


apply_integrity_patches()
apply_risk_integrity_patch()
CONFIG_FINGERPRINT = validate_runtime_config(config)

if FastAPI:
    # API is intentionally read-only. It must never instantiate or start a second
    # executable engine authority. Engine state is exposed through the canonical
    # Python bridge written by the engine process itself.
    from dashboard.data_bridge import reader as bridge_reader

    app = FastAPI(title="DeltaTerminal API")

    @app.get("/health")
    async def health():
        status = bridge_reader.read_status()
        return {
            "status": "ok",
            "config_fingerprint": CONFIG_FINGERPRINT,
            "engine": {
                "running": status.running,
                "halted": status.halted,
                "ws_connected": status.ws_connected,
                "symbols": status.symbols,
                "signals": status.signals,
                "last_update": status.last_update,
            },
            "authority": "python-engine",
            "mode": "read-only-relay",
        }

    @app.get("/signals")
    async def get_signals():
        signals = bridge_reader.read_signals()
        return {
            "count": len(signals),
            "signals": signals,
            "authority": "python-engine",
            "config_fingerprint": CONFIG_FINGERPRINT,
        }
else:
    app = None


async def _acquire_engine_lock() -> bool:
    import fcntl

    _data_dir = Path(__file__).parent / "data"
    _data_dir.mkdir(parents=True, exist_ok=True)
    _lock_path = _data_dir / "engine.lock"
    _pid_path = _data_dir / "engine.pid"

    try:
        lock_fd = open(_lock_path, "w")
    except OSError as exc:
        logger.error("Cannot create lock file {}: {}", _lock_path, exc)
        return False

    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            old_pid = int(_pid_path.read_text().strip())
            alive = False
            try:
                import os
                os.kill(old_pid, 0)
                alive = True
            except OSError:
                pass
            if alive:
                logger.error(
                    "❌ Another engine is already running (PID {}). Exiting to prevent duplicate trading. Kill it first with: kill {}",
                    old_pid,
                    old_pid,
                )
            else:
                logger.error("❌ Stale lock from dead PID {}. Cleaning up.", old_pid)
                _pid_path.unlink(missing_ok=True)
                lock_fd.close()
                _lock_path.unlink(missing_ok=True)
                return await _acquire_engine_lock()
        except (ValueError, OSError):
            logger.error("❌ Engine lock held by unknown process. Exiting.")
        lock_fd.close()
        return False

    import os
    _pid_path.write_text(str(os.getpid()))
    global _engine_lock_fd
    _engine_lock_fd = lock_fd
    logger.info("✅ Engine lock acquired. PID {} written to {}", os.getpid(), _pid_path)
    return True


_engine_lock_fd = None


def _release_engine_lock() -> None:
    import fcntl

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

    try:
        import json as _json
        _manifest_path = Path(__file__).parent / "data" / "cohort_baseline_v1.json"
        _bl = _json.loads(_manifest_path.read_text())
        logger.info(
            "🧪 FORWARD-TEST COHORT START: {} — strategy FROZEN (config hash {}), infra v{}",
            _bl["cohort"]["id"],
            _bl["hashes"]["strategy_config_hash"],
            _bl["infrastructure_version"]["id"],
        )
    except Exception:
        pass

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

    path = Path(__file__).parent / "dashboard" / "app.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(path),
        "--server.port",
        str(config.dashboard.port),
        "--server.address",
        config.dashboard.host,
        "--theme.base",
        "dark",
    ]
    logger.info("Dashboard → http://{}:{}", config.dashboard.host, config.dashboard.port)
    subprocess.run(cmd)


def _run_api() -> None:
    if not app:
        logger.error("FastAPI is not installed. Install the locked API dependencies before using --mode api.")
        return
    logger.info("Starting read-only REST API on http://{}:8000", config.dashboard.host)
    uvicorn.run("main:app", host=config.dashboard.host, port=8000, reload=False)


def _run_live() -> int:
    """Never start live execution without a current matching certification artifact."""
    from validation.live_gate import LiveCertificationError, verify_live_certification

    try:
        artifact = verify_live_certification()
    except LiveCertificationError as exc:
        logger.error("LIVE EXECUTION BLOCKED: {}", exc)
        return 2

    logger.info("LIVE CERTIFICATION VERIFIED: state={}, commit={}", artifact.get("state"), artifact.get("commit_sha"))
    return 0


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="DeltaTerminal")
    parser.add_argument("--mode", choices=["engine", "dashboard", "api", "both", "live"], default="engine")
    args = parser.parse_args()

    _setup_logging(config.log_level)

    logger.info("=" * 56)
    logger.info("⚡ DeltaTerminal — AI-Powered Binance Futures Scanner")
    logger.info("=" * 56)
    logger.info("Configuration fingerprint: {}", CONFIG_FINGERPRINT)

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
    elif args.mode == "live":
        raise SystemExit(_run_live())


if __name__ == "__main__":
    main()
