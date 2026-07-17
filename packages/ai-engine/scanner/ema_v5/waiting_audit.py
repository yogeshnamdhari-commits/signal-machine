"""
EMA_V5 WAITING_CONFIRMATION Audit — Tracks state transitions through the critical phase.

Every symbol entering WAITING_CONFIRMATION must eventually become exactly one of:
  - ACTIVE_BUY
  - ACTIVE_SELL
  - REJECTED
  - TIMED_OUT
  - EXPIRED

Never remain indefinitely. This module tracks:
  - Entered time
  - Exit time
  - Duration
  - Exit reason
"""
from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


class WaitingConfirmationAudit:
    """Tracks every symbol that enters WAITING_CONFIRMATION and its lifecycle."""

    # Timeout: symbols stuck in WAITING_CONFIRMATION for longer than this are expired
    TIMEOUT_SEC = 300  # 5 minutes

    def __init__(self) -> None:
        # Active entries: symbol → {entered_at, regime, confidence, side}
        self._active: Dict[str, Dict] = {}

        # Completed entries (last N)
        self._completed: List[Dict] = []
        self._max_completed = 500

        # Counters
        self._total_entered: int = 0
        self._total_completed: int = 0
        self._total_timeout: int = 0

        # Exit reason counts
        self._exit_reasons: Dict[str, int] = defaultdict(int)

        # Duration tracking
        self._durations: List[float] = []

        # Log path
        self._log_path = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "waiting_confirmation_audit.log"
        self._log_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("🔍 WAITING_CONFIRMATION Audit initialized — timeout={}s", self.TIMEOUT_SEC)

    def on_enter(
        self,
        symbol: str,
        regime: str = "",
        confidence: float = 0.0,
        side: str = "",
    ) -> None:
        """Called when a symbol enters WAITING_CONFIRMATION state."""
        self._active[symbol] = {
            "entered_at": time.time(),
            "regime": regime,
            "confidence": confidence,
            "side": side,
            "state": "WAITING_CONFIRMATION",
        }
        self._total_entered += 1
        logger.debug(
            "🔍 WC_ENTER sym={} regime={} conf={:.1f} side={} total_active={}",
            symbol, regime, confidence, side, len(self._active),
        )

    def on_exit(self, symbol: str, reason: str) -> Optional[Dict]:
        """Called when a symbol exits WAITING_CONFIRMATION.

        Args:
            symbol: The symbol that exited
            reason: Exit reason — ACTIVE_BUY, ACTIVE_SELL, REJECTED, TIMED_OUT, EXPIRED

        Returns:
            The completed journey dict, or None if symbol was not tracked
        """
        entry = self._active.pop(symbol, None)
        if not entry:
            return None

        now = time.time()
        duration = now - entry["entered_at"]

        journey = {
            "symbol": symbol,
            "entered_at": entry["entered_at"],
            "exited_at": now,
            "duration_sec": round(duration, 2),
            "exit_reason": reason,
            "regime": entry.get("regime", ""),
            "confidence": entry.get("confidence", 0),
            "side": entry.get("side", ""),
        }

        self._completed.append(journey)
        if len(self._completed) > self._max_completed:
            self._completed = self._completed[-self._max_completed:]

        self._total_completed += 1
        self._exit_reasons[reason] += 1
        self._durations.append(duration)
        if len(self._durations) > self._max_completed:
            self._durations = self._durations[-self._max_completed:]

        # Log to file
        try:
            with open(self._log_path, "a") as f:
                f.write(
                    f"{symbol} | entered={entry['entered_at']:.0f} | exited={now:.0f} "
                    f"| duration={duration:.1f}s | reason={reason} "
                    f"| regime={entry.get('regime', '')} conf={entry.get('confidence', 0):.1f}\n"
                )
        except Exception:
            pass

        logger.info(
            "🔍 WC_EXIT sym={} reason={} duration={:.1f}s regime={} conf={:.1f}",
            symbol, reason, duration, entry.get("regime", ""), entry.get("confidence", 0),
        )

        return journey

    def check_timeouts(self) -> List[str]:
        """Check for symbols stuck in WAITING_CONFIRMATION beyond timeout.

        Returns:
            List of symbols that timed out
        """
        now = time.time()
        timed_out = []

        for symbol, entry in list(self._active.items()):
            duration = now - entry["entered_at"]
            if duration > self.TIMEOUT_SEC:
                timed_out.append(symbol)
                self.on_exit(symbol, "TIMED_OUT")
                self._total_timeout += 1
                logger.warning(
                    "⏰ WC_TIMEOUT sym={} duration={:.1f}s (max={:.1f}s) regime={} conf={:.1f}",
                    symbol, duration, self.TIMEOUT_SEC,
                    entry.get("regime", ""), entry.get("confidence", 0),
                )

        return timed_out

    def get_stats(self) -> Dict:
        """Get audit statistics."""
        avg_duration = 0
        if self._durations:
            avg_duration = sum(self._durations) / len(self._durations)

        # Active count by age
        now = time.time()
        active_ages = {}
        for sym, entry in self._active.items():
            age = now - entry["entered_at"]
            if age > 300:
                active_ages[sym] = round(age, 1)

        return {
            "total_entered": self._total_entered,
            "total_completed": self._total_completed,
            "currently_active": len(self._active),
            "total_timeouts": self._total_timeout,
            "exit_reasons": dict(self._exit_reasons),
            "avg_duration_sec": round(avg_duration, 1),
            "max_duration_sec": round(max(self._durations), 1) if self._durations else 0,
            "stale_symbols": active_ages,  # symbols active > 5 min
        }

    def get_active(self) -> Dict[str, Dict]:
        """Get currently active WAITING_CONFIRMATION symbols."""
        now = time.time()
        return {
            sym: {
                **entry,
                "age_sec": round(now - entry["entered_at"], 1),
            }
            for sym, entry in self._active.items()
        }

    def get_recent_completions(self, limit: int = 20) -> List[Dict]:
        """Get recent completions for dashboard display."""
        return self._completed[-limit:]
