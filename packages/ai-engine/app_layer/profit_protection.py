"""
Profit Protection Engine — Lock profits progressively at +1R, +2R, +3R, +4R.

Per Executive Assessment Problem 5:
    After trade reaches +2R, profits can return to 0 or loss.
    Need Profit Lock Engine:
        +1R → SL -> Entry (breakeven)
        +2R → lock +0.8R
        +3R → lock +2R
        +4R → ATR trailing

State Machine:
    NONE → BREAKEVEN → LOCKED_1R → LOCKED_2R → TREND_RIDE

READ-ONLY: Returns ProtectionDecision for execution layer to act on.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional


class ProtectionState(Enum):
    """Profit protection state machine."""
    NONE = "none"              # No protection — initial state
    BREAKEVEN = "breakeven"    # SL moved to entry + fee buffer
    LOCKED_1R = "locked_1r"    # Locked at +1R profit
    LOCKED_2R = "locked_2r"    # Locked at +2R profit
    TREND_RIDE = "trend_ride"  # ATR trailing — let winners run


@dataclass
class ProtectionLevel:
    """Defines a protection level with trigger R and lock R."""
    trigger_r: float      # R-multiple that activates this level
    lock_r: float         # R-multiple to lock as minimum profit
    state: ProtectionState = ProtectionState.NONE

    def __init__(self, trigger_r: float, lock_r: float, state: ProtectionState):
        self.trigger_r = trigger_r
        self.lock_r = lock_r
        self.state = state


# ═══════════════════════════════════════════════════════════════
# PROTECTION LEVELS — Progressive profit lock ladder
# ═══════════════════════════════════════════════════════════════

PROTECTION_LADDER = [
    ProtectionLevel(trigger_r=0.5,  lock_r=0.0,   state=ProtectionState.BREAKEVEN),
    ProtectionLevel(trigger_r=1.5,  lock_r=0.8,   state=ProtectionState.LOCKED_1R),
    ProtectionLevel(trigger_r=3.0,  lock_r=2.0,   state=ProtectionState.LOCKED_2R),
    ProtectionLevel(trigger_r=4.0,  lock_r=0.0,   state=ProtectionState.TREND_RIDE),
]

# ATR trail multiplier for TREND_RIDE state (in R-multiples from peak)
TREND_RIDE_ATR_MULT = 0.75  # Trail 0.75R below peak


@dataclass
class PositionProtection:
    """Tracks protection state for a single position."""
    symbol: str = ""
    side: str = ""
    entry_price: float = 0.0
    stop_loss: float = 0.0
    risk_per_unit: float = 0.0

    # State machine
    current_state: ProtectionState = ProtectionState.NONE
    locked_profit_r: float = 0.0  # Currently locked profit in R
    peak_r: float = 0.0          # Highest R achieved
    last_sl_price: float = 0.0   # Last SL price sent to exchange


@dataclass
class ProtectionDecision:
    """Decision from profit protection engine."""
    symbol: str = ""
    action: str = "HOLD"         # HOLD / MODIFY_SL / EXIT
    new_sl: float = 0.0
    new_state: str = ""
    reason: str = ""
    locked_r: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "new_sl": round(self.new_sl, 6),
            "new_state": self.new_state,
            "reason": self.reason,
            "locked_r": round(self.locked_r, 3),
        }


class ProfitProtectionEngine:
    """
    Progressive profit lock engine.

    As a trade moves into profit, progressively locks in minimum gains:
        +0.5R → SL to breakeven (risk-free)
        +1.5R → Lock +0.8R (guaranteed 0.8R win)
        +3.0R → Lock +2.0R (guaranteed 2R win)
        +4.0R → ATR trailing (let winners run)

    This prevents the #1 profit killer: winners returning to losers.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self) -> None:
        self._positions: Dict[str, PositionProtection] = {}

    def register_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
    ) -> None:
        """Register a position for profit protection."""
        risk = abs(entry_price - stop_loss)
        if risk <= 0:
            risk = entry_price * 0.01  # Fallback 1% risk

        self._positions[symbol] = PositionProtection(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            risk_per_unit=risk,
            current_state=ProtectionState.NONE,
            peak_r=0.0,
            last_sl_price=stop_loss,
        )

    def evaluate(
        self,
        symbol: str,
        current_price: float,
        current_atr: float = 0.0,
    ) -> ProtectionDecision:
        """
        Evaluate profit protection for a position.

        Args:
            symbol: Position symbol
            current_price: Current market price
            current_atr: Current ATR for trailing in TREND_RIDE

        Returns:
            ProtectionDecision with action to take
        """
        pos = self._positions.get(symbol)
        if not pos:
            return ProtectionDecision(symbol=symbol, action="HOLD", reason="not_registered")

        decision = ProtectionDecision(symbol=symbol)

        # ── Calculate current R-multiple ──
        if pos.risk_per_unit <= 0:
            return ProtectionDecision(symbol=symbol, action="HOLD", reason="invalid_risk")

        if pos.side == "LONG":
            current_r = (current_price - pos.entry_price) / pos.risk_per_unit
        else:
            current_r = (pos.entry_price - current_price) / pos.risk_per_unit

        # ── Update peak ──
        if current_r > pos.peak_r:
            pos.peak_r = current_r

        # ── Check each protection level (highest first) ──
        for level in reversed(PROTECTION_LADDER):
            if pos.peak_r >= level.trigger_r:
                # Position has reached this protection level
                if pos.current_state != level.state:
                    # Transition to new state
                    new_sl = self._calculate_protected_sl(pos, level, current_price, current_atr)

                    if new_sl != pos.last_sl_price:
                        pos.current_state = level.state
                        pos.locked_profit_r = level.lock_r
                        pos.last_sl_price = new_sl
                        decision.action = "MODIFY_SL"
                        decision.new_sl = new_sl
                        decision.new_state = level.state.value
                        decision.locked_r = level.lock_r
                        decision.reason = (
                            f"profit_protected: {pos.peak_r:.2f}R peak → "
                            f"{level.state.value} (locked {level.lock_r:.1f}R)"
                        )
                        return decision
                else:
                    # Already at this level — check if we need to update trailing
                    if level.state == ProtectionState.TREND_RIDE:
                        new_sl = self._calculate_trend_ride_sl(pos, current_price, current_atr)
                        if pos.side == "LONG" and new_sl > pos.last_sl_price:
                            pos.last_sl_price = new_sl
                            decision.action = "MODIFY_SL"
                            decision.new_sl = new_sl
                            decision.new_state = ProtectionState.TREND_RIDE.value
                            decision.reason = f"trend_ride_trail: {new_sl:.4f}"
                            return decision
                        elif pos.side == "SHORT" and new_sl < pos.last_sl_price:
                            pos.last_sl_price = new_sl
                            decision.action = "MODIFY_SL"
                            decision.new_sl = new_sl
                            decision.new_state = ProtectionState.TREND_RIDE.value
                            decision.reason = f"trend_ride_trail: {new_sl:.4f}"
                            return decision

                # At highest reached level, no further action needed
                break

        # ── Check if current price would hit protected SL ──
        if pos.current_state != ProtectionState.NONE:
            if pos.side == "LONG" and current_price <= pos.last_sl_price:
                decision.action = "EXIT"
                decision.new_sl = pos.last_sl_price
                decision.new_state = pos.current_state.value
                decision.locked_r = pos.locked_profit_r
                decision.reason = f"protected_sl_hit: {pos.current_state.value}"
                return decision
            elif pos.side == "SHORT" and current_price >= pos.last_sl_price:
                decision.action = "EXIT"
                decision.new_sl = pos.last_sl_price
                decision.new_state = pos.current_state.value
                decision.locked_r = pos.locked_profit_r
                decision.reason = f"protected_sl_hit: {pos.current_state.value}"
                return decision

        decision.action = "HOLD"
        decision.new_state = pos.current_state.value
        decision.locked_r = pos.locked_profit_r
        decision.reason = f"pnl_r={current_r:.2f} peak={pos.peak_r:.2f} state={pos.current_state.value}"
        return decision

    def _calculate_protected_sl(
        self,
        pos: PositionProtection,
        level: ProtectionLevel,
        current_price: float,
        current_atr: float,
    ) -> float:
        """Calculate the new SL for a protection level."""
        entry = pos.entry_price
        risk = pos.risk_per_unit

        if level.state == ProtectionState.BREAKEVEN:
            # SL to entry + fee buffer
            fee_buffer = entry * 0.0004
            if pos.side == "LONG":
                return entry + fee_buffer
            else:
                return entry - fee_buffer

        elif level.state == ProtectionState.LOCKED_1R:
            # Lock +0.8R profit
            if pos.side == "LONG":
                return entry + risk * 0.8
            else:
                return entry - risk * 0.8

        elif level.state == ProtectionState.LOCKED_2R:
            # Lock +2.0R profit
            if pos.side == "LONG":
                return entry + risk * 2.0
            else:
                return entry - risk * 2.0

        elif level.state == ProtectionState.TREND_RIDE:
            # ATR-based trailing from peak
            return self._calculate_trend_ride_sl(pos, current_price, current_atr)

        return pos.last_sl_price

    def _calculate_trend_ride_sl(
        self,
        pos: PositionProtection,
        current_price: float,
        current_atr: float,
    ) -> float:
        """Calculate trailing SL for TREND_RIDE state using ATR."""
        risk = pos.risk_per_unit
        trail_dist = max(current_atr * TREND_RIDE_ATR_MULT, risk * 0.75)

        if pos.side == "LONG":
            return current_price - trail_dist
        else:
            return current_price + trail_dist

    def get_state(self, symbol: str) -> Optional[str]:
        """Get current protection state for a symbol."""
        pos = self._positions.get(symbol)
        return pos.current_state.value if pos else None

    def cleanup(self, symbol: str) -> None:
        """Remove tracking for a closed position."""
        self._positions.pop(symbol, None)

    def get_all_states(self) -> Dict[str, Dict]:
        """Get all position protection states."""
        return {
            sym: {
                "state": pos.current_state.value,
                "locked_r": round(pos.locked_profit_r, 3),
                "peak_r": round(pos.peak_r, 3),
                "sl": round(pos.last_sl_price, 6),
            }
            for sym, pos in self._positions.items()
        }
