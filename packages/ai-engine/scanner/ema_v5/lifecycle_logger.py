"""
EMA_V5 State Lifecycle Logger — Read-only instrumentation.

Records every state transition with timestamps for debugging.
Writes to data/logs/ema_v5_lifecycle.log

Usage:
    lifecycle_log.transition("ETHUSDT", "BUY_MODE", "WAITING_PULLBACK", "pullback_detected")
    lifecycle_log.session_result("ETHUSDT", "REJECT", "outside_trading_hours", 94.2)
    lifecycle_log.state_after_reject("ETHUSDT", "WAITING_CONFIRMATION", "no_reset_performed")
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from loguru import logger


class LifecycleLogger:
    """Lightweight state transition logger for debugging."""

    def __init__(self) -> None:
        self._log_path = (
            Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "ema_v5_lifecycle.log"
        )
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._count = 0
        logger.info("📋 LIFECYCLE LOGGER initialized → {}", self._log_path)

    def transition(
        self,
        symbol: str,
        from_state: str,
        to_state: str,
        reason: str = "",
        confidence: float = 0,
        side: str = "",
    ) -> None:
        """Record a state transition."""
        self._count += 1
        ts = time.strftime("%H:%M:%S")
        line = (
            f"{ts} | {symbol:<14} | {from_state:<22} → {to_state:<22} | "
            f"conf={confidence:>5.1f} side={side:<5} | {reason}"
        )
        self._write(line)

    def session_result(
        self,
        symbol: str,
        result: str,
        reason: str,
        confidence: float,
        state_before: str,
        state_after: str,
    ) -> None:
        """Record session filter result and resulting state."""
        self._count += 1
        ts = time.strftime("%H:%M:%S")
        line = (
            f"{ts} | {symbol:<14} | SESSION_FILTER: {result:<6} | "
            f"reason={reason:<30} | conf={confidence:>5.1f} | "
            f"state: {state_before} → {state_after}"
        )
        self._write(line)

    def stuck_check(
        self,
        symbol: str,
        current_state: str,
        duration_sec: float,
        timeout_sec: float,
    ) -> None:
        """Record a stuck state detection."""
        self._count += 1
        ts = time.strftime("%H:%M:%S")
        status = "TIMED_OUT" if duration_sec >= timeout_sec else "STILL_WAITING"
        line = (
            f"{ts} | {symbol:<14} | STUCK_CHECK: {current_state:<22} | "
            f"duration={duration_sec:.0f}s timeout={timeout_sec:.0f}s | {status}"
        )
        self._write(line)

    def scan_entry(
        self,
        symbol: str,
        current_state: str,
        regime: str,
    ) -> None:
        """Record when scanner starts evaluating a symbol."""
        self._count += 1
        ts = time.strftime("%H:%M:%S")
        line = (
            f"{ts} | {symbol:<14} | SCAN_ENTRY: state={current_state:<22} | "
            f"regime={regime}"
        )
        self._write(line)

    def _write(self, line: str) -> None:
        """Write to lifecycle log."""
        try:
            import datetime
            today = datetime.date.today().isoformat()
            path = self._log_path.with_name(f"ema_v5_lifecycle_{today}.log")
            with open(path, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass


# Global instance
lifecycle_log = LifecycleLogger()
