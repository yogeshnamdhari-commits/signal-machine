"""
Emergency Kill Switch — Instantaneous Trading Halt
===================================================
Triggers: Drawdown > 10%, API Failure, Position Desync,
          Exchange Failure, Risk Breach, Unexpected Exposure
Actions:  Close Positions, Cancel Orders, Disable Trading, Send Alert
"""

import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "capital"


# ─── Enums ───────────────────────────────────────────────────────────────────
class KillTrigger(Enum):
    DRAWDOWN_BREACH = "drawdown_breach"
    API_FAILURE = "api_failure"
    POSITION_DESYNC = "position_desync"
    EXCHANGE_FAILURE = "exchange_failure"
    RISK_BREACH = "risk_breach"
    UNEXPECTED_EXPOSURE = "unexpected_exposure"
    MANUAL = "manual"
    CONSECUTIVE_LOSSES = "consecutive_losses"
    DAILY_LOSS_LIMIT = "daily_loss_limit"
    MARGIN_CALL = "margin_call"


class KillState(Enum):
    ARMED = "ARMED"           # Normal operation
    TRIGGERED = "TRIGGERED"   # Kill activated, executing
    EXECUTING = "EXECUTING"   # Closing positions
    HALTED = "HALTED"         # All positions closed, trading disabled
    RECOVERING = "RECOVERING" # Attempting to restore
    DISABLED = "DISABLED"     # Kill switch disabled (manual override)


# ─── Data Classes ────────────────────────────────────────────────────────────
@dataclass
class KillEvent:
    """Record of a kill switch activation."""
    event_id: str
    trigger: str
    reason: str
    state: str
    positions_closed: int
    orders_cancelled: int
    timestamp: str
    resolved: bool = False
    resolved_at: str = ""
    metadata: dict = field(default_factory=dict)


# ─── Kill Switch ─────────────────────────────────────────────────────────────
class EmergencyKillSwitch:
    """
    Emergency kill switch for immediate trading halt.

    Usage:
        kill = EmergencyKillSwitch()
        kill.set_triggers(drawdown_limit=0.10, daily_loss_limit=0.03)
        kill.check(  # Called continuously
            drawdown=0.05,
            daily_loss=0.02,
            api_healthy=True,
            positions_synced=True,
        )
    """

    def __init__(self):
        self._state = KillState.ARMED
        self._events: list[KillEvent] = []
        self._triggers: dict[str, float] = {
            "drawdown_limit": 0.10,
            "daily_loss_limit": 0.03,
            "consecutive_losses": 5,
            "api_error_threshold": 10,
            "desync_threshold": 3,
            "exposure_limit": 1.5,  # 150% of expected
        }
        self._api_errors = 0
        self._desync_count = 0
        self._consecutive_losses = 0
        self._halt_until: Optional[float] = None
        self._cooldown_seconds = 300  # 5 min cooldown after kill

        # Callbacks
        self._on_kill: list[Callable] = []
        self._on_recover: list[Callable] = []

        self._load_state()
        logger.info("KillSwitch initialized: state=%s", self._state.value)

    # ── Configuration ────────────────────────────────────────────────────────
    def set_triggers(self, **kwargs):
        """Update trigger thresholds."""
        for key, value in kwargs.items():
            if key in self._triggers:
                self._triggers[key] = value
                logger.info("Kill trigger '%s' set to %s", key, value)

    def on_kill(self, callback: Callable):
        """Register kill callback."""
        self._on_kill.append(callback)

    def on_recover(self, callback: Callable):
        """Register recovery callback."""
        self._on_recover.append(callback)

    # ── Continuous Check ─────────────────────────────────────────────────────
    def check(
        self,
        drawdown: float = 0.0,
        daily_loss: float = 0.0,
        api_healthy: bool = True,
        positions_synced: bool = True,
        exchange_healthy: bool = True,
        total_exposure: float = 0.0,
        expected_exposure: float = 0.0,
        consecutive_losses: int = 0,
        margin_usage: float = 0.0,
    ) -> Optional[KillTrigger]:
        """
        Check all kill conditions. Returns trigger if kill activated, None otherwise.
        Call this continuously (every second).
        """
        if self._state in (KillState.TRIGGERED, KillState.EXECUTING, KillState.HALTED):
            return None  # Already killing

        if self._state == KillState.DISABLED:
            return None  # Manual override

        # Check cooldown
        if self._halt_until and time.time() < self._halt_until:
            return None

        self._consecutive_losses = consecutive_losses
        trigger = None

        # 1. Drawdown breach
        if drawdown >= self._triggers["drawdown_limit"]:
            trigger = KillTrigger.DRAWDOWN_BREACH
            reason = f"Drawdown {drawdown:.1%} >= {self._triggers['drawdown_limit']:.1%}"

        # 2. Daily loss limit
        elif daily_loss >= self._triggers["daily_loss_limit"]:
            trigger = KillTrigger.DAILY_LOSS_LIMIT
            reason = f"Daily loss {daily_loss:.1%} >= {self._triggers['daily_loss_limit']:.1%}"

        # 3. API failure
        elif not api_healthy:
            self._api_errors += 1
            if self._api_errors >= self._triggers["api_error_threshold"]:
                trigger = KillTrigger.API_FAILURE
                reason = f"API errors: {self._api_errors}"
        else:
            self._api_errors = max(0, self._api_errors - 1)  # Decay

        # 4. Position desync
        if not positions_synced:
            self._desync_count += 1
            if self._desync_count >= self._triggers["desync_threshold"]:
                trigger = KillTrigger.POSITION_DESYNC
                reason = f"Position desync count: {self._desync_count}"
        else:
            self._desync_count = 0

        # 5. Exchange failure
        if not exchange_healthy:
            trigger = KillTrigger.EXCHANGE_FAILURE
            reason = "Exchange connection failure"

        # 6. Unexpected exposure
        if expected_exposure > 0:
            exposure_ratio = total_exposure / expected_exposure
            if exposure_ratio > self._triggers["exposure_limit"]:
                trigger = KillTrigger.UNEXPECTED_EXPOSURE
                reason = f"Exposure ratio {exposure_ratio:.2f} > {self._triggers['exposure_limit']:.2f}"

        # 7. Consecutive losses
        if consecutive_losses >= self._triggers["consecutive_losses"]:
            trigger = KillTrigger.CONSECUTIVE_LOSSES
            reason = f"{consecutive_losses} consecutive losses"

        # 8. Margin call
        if margin_usage > 0.95:
            trigger = KillTrigger.MARGIN_CALL
            reason = f"Margin usage {margin_usage:.1%}"

        # Activate kill if triggered
        if trigger:
            self._activate_kill(trigger, reason)

        return trigger

    # ── Manual Controls ───────────────────────────────────────────────────────
    def manual_kill(self, reason: str = "Manual kill"):
        """Manually activate kill switch."""
        self._activate_kill(KillTrigger.MANUAL, reason)

    def manual_recover(self):
        """Manually recover from kill state."""
        if self._state == KillState.HALTED:
            self._state = KillState.ARMED
            self._api_errors = 0
            self._desync_count = 0
            self._consecutive_losses = 0
            self._halt_until = None
            self._save_state()
            logger.info("Kill switch manually recovered")
            for cb in self._on_recover:
                try:
                    cb()
                except Exception as e:
                    logger.error("Recover callback error: %s", e)

    def disable(self):
        """Disable kill switch (use with extreme caution)."""
        self._state = KillState.DISABLED
        self._save_state()
        logger.warning("Kill switch DISABLED")

    def arm(self):
        """Re-arm kill switch."""
        if self._state == KillState.DISABLED:
            self._state = KillState.ARMED
            self._save_state()
            logger.info("Kill switch ARMED")

    # ── Kill Activation ──────────────────────────────────────────────────────
    def _activate_kill(self, trigger: KillTrigger, reason: str):
        """Activate the kill switch."""
        self._state = KillState.TRIGGERED
        logger.critical("🚨 KILL SWITCH ACTIVATED: %s — %s", trigger.value, reason)

        positions_closed = 0
        orders_cancelled = 0

        # Execute kill callbacks
        for cb in self._on_kill:
            try:
                result = cb(trigger, reason)
                if isinstance(result, dict):
                    positions_closed += result.get("positions_closed", 0)
                    orders_cancelled += result.get("orders_cancelled", 0)
            except Exception as e:
                logger.error("Kill callback error: %s", e)

        self._state = KillState.HALTED

        # Set cooldown
        self._halt_until = time.time() + self._cooldown_seconds
        self._cooldown_seconds = min(self._cooldown_seconds * 2, 1800)  # Double, max 30 min

        # Record event
        event = KillEvent(
            event_id=f"KILL-{int(time.time())}",
            trigger=trigger.value,
            reason=reason,
            state=self._state.value,
            positions_closed=positions_closed,
            orders_cancelled=orders_cancelled,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._events.append(event)
        self._save_state()

    # ── Status ────────────────────────────────────────────────────────────────
    @property
    def state(self) -> KillState:
        return self._state

    @property
    def is_halted(self) -> bool:
        return self._state in (KillState.TRIGGERED, KillState.EXECUTING, KillState.HALTED)

    @property
    def is_armed(self) -> bool:
        return self._state == KillState.ARMED
    
    def is_triggered(self) -> bool:
        """Check if kill switch has been triggered."""
        return self.is_halted
    
    def trigger(self, trigger_type: str = "manual", reason: str = "Manual trigger"):
        """Manually trigger the kill switch."""
        try:
            trigger = KillTrigger(trigger_type)
        except ValueError:
            trigger = KillTrigger.MANUAL
        self._activate_kill(trigger, reason)

    def get_events(self) -> list[dict]:
        """Get all kill events."""
        return [asdict(e) for e in self._events]

    def get_stats(self) -> dict:
        """Get kill switch statistics."""
        total = len(self._events)
        resolved = sum(1 for e in self._events if e.resolved)
        return {
            "state": self._state.value,
            "total_events": total,
            "resolved": resolved,
            "unresolved": total - resolved,
            "api_errors": self._api_errors,
            "desync_count": self._desync_count,
            "consecutive_losses": self._consecutive_losses,
            "triggers": self._triggers,
        }

    # ── State Persistence ─────────────────────────────────────────────────────
    def _save_state(self):
        """Save kill switch state."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "state": self._state.value,
            "api_errors": self._api_errors,
            "desync_count": self._desync_count,
            "consecutive_losses": self._consecutive_losses,
            "cooldown_seconds": self._cooldown_seconds,
            "halt_until": self._halt_until,
            "events": [asdict(e) for e in self._events[-50:]],
            "triggers": self._triggers,
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        (DATA_DIR / "kill_switch_state.json").write_text(json.dumps(state, indent=2, default=str))

    def _load_state(self):
        """Load persisted state."""
        path = DATA_DIR / "kill_switch_state.json"
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text())
            self._state = KillState(state.get("state", "ARMED"))
            self._api_errors = state.get("api_errors", 0)
            self._desync_count = state.get("desync_count", 0)
            self._consecutive_losses = state.get("consecutive_losses", 0)
            self._cooldown_seconds = state.get("cooldown_seconds", 300)
            self._halt_until = state.get("halt_until")
            self._triggers = state.get("triggers", self._triggers)
            for e in state.get("events", []):
                self._events.append(KillEvent(**e))
        except Exception as e:
            logger.error("Failed to load kill switch state: %s", e)
