"""
DeltaTerminal — Centralized Logging
Uses loguru for structured, rotation-aware logging.
"""
import sys
from pathlib import Path

from loguru import logger

from config import config

# ── Remove default handler ───────────────────────────────────────
logger.remove()

# ── Console handler ──────────────────────────────────────────────
logger.add(
    sys.stderr,
    format=(
        "<green>{time:HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan> — "
        "<level>{message}</level>"
    ),
    level=config.log_level,
    colorize=True,
)

# ── File handler (daily rotation, 7-day retention) ───────────────
_log_dir = Path("data/logs")
_log_dir.mkdir(parents=True, exist_ok=True)

logger.add(
    str(_log_dir / "engine_{time:YYYY-MM-DD}.log"),
    rotation="1 day",
    retention="7 days",
    compression="gz",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} — {message}",
    encoding="utf-8",
)

__all__ = ["logger"]
