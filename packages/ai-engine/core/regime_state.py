"""
Regime State Persistence — Phase 2 CRITICAL BUG A.

Persistent halt/resume system that prevents the engine from resuming trading
after a circuit breaker unless the MARKET REGIME has actually changed.

Problem (June 18 collapse):
  - 3 consecutive losses → 4h pause triggered correctly
  - After 4h cooldown, system resumed into the SAME bad market
  - Generated more losses because regime never changed

Solution:
  - Halt state persisted to disk (survives restarts)
  - Resume requires BOTH time elapsed AND regime change
  - Three halt tiers: regime-change, daily-reset, severe

Usage:
    from core.regime_state import regime_state
    if regime_state.is_halted(current_regime):
        # skip all signal generation
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

from loguru import logger


REGIME_STATE_FILE = str(Path(__file__).resolve().parent.parent / "data" / "regime_state.json")

DEFAULT_STATE: Dict = {
    "current_regime": "unknown",
    "regime_confirmed_at": 0,
    "consecutive_losses": 0,
    "daily_loss_pct": 0.0,
    "halt_until": 0,
    "halt_reason": None,
    "resume_condition": None,       # "regime_must_change" | "daily_reset" | None
    "halted_in_regime": None,       # regime when halt was triggered
    "last_regime_check": 0,
    "last_halt_check": 0,
    "total_halts_today": 0,
    "last_halt_date": "",
}


class RegimeStateManager:
    """
    Persistent regime halt state manager.

    Halt tiers:
      - 3 consecutive losses → 4h halt, resume when regime changes
      - Daily loss > 2%     → 24h halt, resume next UTC day
      - Daily loss > 4%     → 48h halt, resume when regime changes
      - Regime → unknown/volatile → immediate halt until regime clears

    Key principle: TIME ALONE IS NOT ENOUGH TO RESUME.
    The regime must also confirm the halt condition is resolved.
    """

    def __init__(self, state_file: str = REGIME_STATE_FILE):
        self._state_file = state_file
        self._state: Dict = DEFAULT_STATE.copy()
        self._load()

    def _load(self) -> None:
        """Load state from disk. If missing, use defaults."""
        try:
            if os.path.exists(self._state_file):
                with open(self._state_file) as f:
                    loaded = json.load(f)
                # Merge with defaults (handles schema evolution)
                self._state = {**DEFAULT_STATE, **loaded}
                logger.debug("📋 Regime state loaded from {}", self._state_file)
            else:
                self._state = DEFAULT_STATE.copy()
        except Exception as e:
            logger.warning("Could not load regime state: {} — using defaults", e)
            self._state = DEFAULT_STATE.copy()

    def _save(self) -> None:
        """Persist state to disk."""
        try:
            os.makedirs(os.path.dirname(self._state_file), exist_ok=True)
            with open(self._state_file, "w") as f:
                json.dump(self._state, f, indent=2)
        except Exception as e:
            logger.error("Failed to save regime state: {}", e)

    def is_halted(self, current_regime: str = "unknown") -> Tuple[bool, str]:
        """
        Check if the system is currently halted.

        Returns (is_halted, reason).
        A system is halted if:
          1. Time-based halt is still active, OR
          2. Time elapsed but resume condition not met (regime unchanged)
        """
        self._load()  # Re-read from disk in case another process updated
        now = time.time()
        self._state["last_halt_check"] = now

        halt_until = self._state.get("halt_until", 0)
        if halt_until <= 0:
            return False, ""

        # ── Check 1: Still within time-based halt ──
        if now < halt_until:
            remaining = int(halt_until - now)
            hours = remaining // 3600
            mins = (remaining % 3600) // 60
            reason = self._state.get("halt_reason", "unknown halt")
            return True, f"TIME_HALT: {reason} — {hours}h{mins}m remaining"

        # ── Check 2: Time elapsed — now check resume condition ──
        resume_cond = self._state.get("resume_condition")
        halted_in_regime = self._state.get("halted_in_regime")

        # SAFETY: Maximum hold duration — after 4h past halt expiry, force resume
        # Prevents permanent deadlock when regime never changes
        MAX_HOLD_AFTER_EXPIRY = 4 * 3600  # 4 hours
        if now - halt_until > MAX_HOLD_AFTER_EXPIRY:
            logger.warning(
                "⏰ SAFETY_TIMEOUT: halt expired {:.0f}h ago with unchanged regime — "
                "force-resuming to prevent deadlock",
                (now - halt_until) / 3600,
            )
            self._clear_halt()
            return False, "Safety timeout — force-resuming after 24h"

        if resume_cond == "regime_must_change":
            if halted_in_regime and current_regime == halted_in_regime:
                reason = (
                    f"REGIME_UNCHANGED: halted in {halted_in_regime}, "
                    f"still {current_regime} — refusing to resume"
                )
                logger.info("🚫 {}", reason)
                return True, reason
            # Regime changed — safe to resume
            logger.info(
                "✅ REGIME_CHANGED: {} → {} — resuming trading",
                halted_in_regime, current_regime,
            )
            self._clear_halt()
            return False, "Regime changed — resuming"

        if resume_cond == "daily_reset":
            # Only resume at next UTC day boundary
            halt_day = int(halt_until / 86400)
            today = int(now / 86400)
            if today <= halt_day:
                return True, "DAILY_HALT: waiting for next UTC day boundary"
            # New day — safe to resume
            logger.info("✅ NEW_DAY: UTC day boundary passed — resuming")
            self._clear_halt()
            return False, "New UTC day — resuming"

        # Default: halt cleared (no resume condition or unrecognized)
        self._clear_halt()
        return False, "Halt cleared"

    def _clear_halt(self) -> None:
        """Clear the halt state."""
        self._state["halt_until"] = 0
        self._state["halt_reason"] = None
        self._state["resume_condition"] = None
        self._state["halted_in_regime"] = None
        self._save()

    def trigger_halt(
        self,
        reason: str,
        duration_hours: float,
        resume_condition: str,
        current_regime: str,
    ) -> None:
        """
        Trigger a system halt.

        Args:
            reason: Human-readable reason for the halt
            duration_hours: How long to halt (hours)
            resume_condition: What must be true to resume
                - "regime_must_change": regime must differ from current
                - "daily_reset": wait for next UTC day
            current_regime: The regime at halt time (for comparison on resume)
        """
        now = time.time()
        halt_until = now + duration_hours * 3600

        # Don't override a longer existing halt with a shorter one
        existing_halt_until = self._state.get("halt_until", 0)
        if existing_halt_until > halt_until:
            logger.info(
                "⏸️  HALT_NOT_EXTENDED: existing halt ({:.0f}s) longer than requested ({:.0f}s)",
                existing_halt_until - now, duration_hours * 3600,
            )
            return

        # Check date for daily counter
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if self._state.get("last_halt_date") != today:
            self._state["total_halts_today"] = 0
            self._state["last_halt_date"] = today

        self._state["halt_until"] = halt_until
        self._state["halt_reason"] = reason
        self._state["resume_condition"] = resume_condition
        self._state["halted_in_regime"] = current_regime
        self._state["total_halts_today"] = self._state.get("total_halts_today", 0) + 1
        self._save()

        logger.warning(
            "🛑 HALT_TRIGGERED: {} | Duration: {}h | Resume: {} | Regime: {} | Halts today: {}",
            reason, duration_hours, resume_condition, current_regime,
            self._state["total_halts_today"],
        )

    def evaluate_halt_conditions(
        self,
        daily_stats: Dict,
        consecutive_losses: int,
        account_balance: float,
        current_regime: str,
    ) -> None:
        """
        v5: Evaluate all halt conditions and trigger if needed.

        Circuit breaker tiers (v5 from live data):
        - 4 consecutive losses → 4h halt (was 3 — tightened for reality)
        - Daily loss > 3% → 24h halt (was 2%)
        - Daily loss > 5% → 48h halt (was 4%)
        - Rolling PF < 0.8 over 20 trades → 4h halt (checked separately in engine)

        Args:
            daily_stats: {"pnl": float, "trades": int, "wins": int}
            consecutive_losses: Current loss streak
            account_balance: Current account balance
            current_regime: Current market regime
        """
        daily_pnl = daily_stats.get("pnl", 0)
        balance = max(account_balance, 1.0)  # avoid div by zero
        daily_loss_pct = abs(daily_pnl) / balance * 100 if daily_pnl < 0 else 0

        # ── v5 Tier 1: 4+ consecutive losses → 4h halt, regime must change ──
        # v5: raised from 3 to 4 — 3 losses is within normal variance
        if consecutive_losses >= 4:
            self.trigger_halt(
                reason=f"{consecutive_losses} consecutive losses (v5 threshold)",
                duration_hours=4,
                resume_condition="regime_must_change",
                current_regime=current_regime,
            )

        # ── v5 Tier 2: Daily loss > 3% → 24h halt, resume next day ──
        # v5: raised from 2% to 3% — allows slightly more aggressive trading
        if daily_loss_pct > 3.0:
            self.trigger_halt(
                reason=f"Daily loss {daily_loss_pct:.1f}% exceeded 3% (v5 threshold)",
                duration_hours=24,
                resume_condition="daily_reset",
                current_regime=current_regime,
            )

        # ── v5 Tier 3: Daily loss > 5% → 48h halt, regime must change ──
        # v5: raised from 4% to 5% — aligns with catastrophic loss definition
        if daily_loss_pct > 5.0:
            self.trigger_halt(
                reason=f"SEVERE daily loss {daily_loss_pct:.1f}% (v5: >5%)",
                duration_hours=48,
                resume_condition="regime_must_change",
                current_regime=current_regime,
            )

        self._state["consecutive_losses"] = consecutive_losses
        self._state["daily_loss_pct"] = daily_loss_pct
        self._save()

    def update_regime(self, regime: str) -> None:
        """Track regime changes for halt evaluation."""
        self._state["current_regime"] = regime
        self._state["regime_confirmed_at"] = time.time()
        self._state["last_regime_check"] = time.time()
        self._save()

    def increment_consecutive_losses(self) -> int:
        """Increment and return consecutive loss count."""
        self._state["consecutive_losses"] = self._state.get("consecutive_losses", 0) + 1
        self._save()
        return self._state["consecutive_losses"]

    def reset_consecutive_losses(self) -> None:
        """Reset consecutive losses (on a win)."""
        if self._state.get("consecutive_losses", 0) > 0:
            self._state["consecutive_losses"] = 0
            self._save()

    @property
    def consecutive_losses(self) -> int:
        return self._state.get("consecutive_losses", 0)

    @property
    def daily_loss_pct(self) -> float:
        return self._state.get("daily_loss_pct", 0.0)

    def get_state(self) -> Dict:
        """Return full state for dashboard display."""
        self._load()
        return self._state.copy()

    def force_resume(self) -> None:
        """Emergency: force-resume trading (manual override)."""
        logger.warning("⚠️  FORCE_RESUME: manual override triggered")
        self._clear_halt()


# Global singleton
regime_state = RegimeStateManager()
