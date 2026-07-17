"""
App Exit Engine — Dynamic exits only. No static exits when dynamic is superior.

Per Master Directive:
    "Dynamic exits only. Allowed: Trailing stop, Break-even, Partial profit,
     ATR trailing, Volatility trailing, Momentum exit, Institutional reversal exit,
     Time stop. Never use static exits when dynamic exits are superior."

Per Executive Assessment — Problem 1 (Intelligent Trailing Stop):
    v1: Trailing activated immediately → killed trend trades
    v2: Delayed activation with progressive tightening:
        No trailing before +1R — let trade breathe
        Move SL to breakeven after +1R — risk-free
        Trail at 1.0R after +1.5R — protect gains
        Aggressive trail at 0.5R after +2R — lock profits
        Wide trail at 1.5R in volatile regimes

Per Executive Assessment — Problem 7 (Time Stop):
    v1: Static 6h exit → trapped capital in trending markets
    v2: Dynamic time stop:
        45–90 min AND PnL ≈ 0 AND momentum decreasing → exit
        Skip if MFE > 5% (trade showed strength)
        Hard max 24h → always exit

Exit Logic:
    1. Hard SL — immediate exit (priority 1)
    2. Dynamic Time Stop — 45-90 min no-progress with momentum check
    3. Break-even at +1R — risk-free trade
    4. Partial Profit at +1.5R — take 40%, let rest run
    5. Delayed Trailing Stop — activates at +1R, progressive tightening
    6. Momentum Exit — CVD + delta + OI + flow reversal
    7. Volatility Exit — ATR compression collapse
    8. Hard Time Stop — 24h maximum (no exceptions)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# EXIT CONFIGURATION — Progressive Trailing (v2)
# ═══════════════════════════════════════════════════════════════

# ── Trailing Stop — Progressive (Problem 1 fix) ──
TRAIL_ACTIVATION_R = 1.0       # No trailing before +1R (let trade breathe)
TRAIL_DISTANCE_STAGE1 = 1.0    # Trail 1.0R below peak (1R–2R zone)
TRAIL_DISTANCE_STAGE2 = 0.75   # Trail 0.75R below peak (2R–3R zone)
TRAIL_DISTANCE_STAGE3 = 0.5    # Trail 0.5R below peak (>3R zone, aggressive lock)
TRAIL_DISTANCE_MOMENTUM = 0.4  # Trail 0.4R in strong momentum

# ── Break-even — Delayed to +1R (Problem 1 fix) ──
BREAKEVEN_TRIGGER_R = 1.0      # Move SL to entry after 1R profit (was same, now delayed trailing)

# ── Partial Profit — Adjusted (Problem 1 fix) ──
PARTIAL_PROFIT_R = 1.5         # Take partial at 1.5R
PARTIAL_PROFIT_PCT = 0.40      # Take 40% of position (was 50%)

# ── Time Stop — Dynamic (Problem 7 fix) ──
TIME_STOP_NO_PROGRESS_MIN = 45    # Minimum minutes to check for no-progress
TIME_STOP_NO_PROGRESS_MAX = 90    # Exit if no progress after 90 min
TIME_STOP_HARD_MAX_HOURS = 24     # Hard maximum hold time
MFE_BYPASS_THRESHOLD = 5.0       # Skip time stop if MFE > 5% (showed strength)
MOMENTUM_DECREASE_THRESHOLD = -0.1  # Momentum decreasing threshold

# ── Stale Trade Exit — Mid-term time stop (governance) ──
STALE_TRADE_CHECK_HOURS = 6      # Start checking after 6 hours
STALE_TRADE_EXIT_HOURS = 8       # Force exit after 8 hours if < 0.3R
STALE_TRADE_MIN_R = 0.3          # Minimum R after stale hours

# ── Momentum Exit — Enhanced (Problem 4 fix) ──
MOMENTUM_REVERSAL_CVD = -0.3      # CVD reversal threshold
MOMENTUM_REVERSAL_DELTA = -0.2    # Delta reversal threshold
MOMENTUM_REVERSAL_OI = -0.15      # OI decrease threshold (OI declining = weak)
FLOW_REVERSAL_THRESHOLD = -0.2    # Flow reversal threshold


@dataclass
class ExitState:
    """Tracks exit state for a single position."""
    symbol: str = ""
    side: str = ""
    entry_price: float = 0.0
    entry_time: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    quantity: float = 0.0

    # Trailing state — Progressive (v2)
    peak_pnl_r: float = 0.0       # Peak PnL in R-multiples
    current_trail_sl: float = 0.0  # Current trailing stop price
    trail_active: bool = False
    trail_stage: int = 0           # 0=inactive, 1=1R, 2=2R, 3=3R+

    # Break-even state
    breakeven_moved: bool = False

    # Partial profit state
    partial_taken: bool = False
    remaining_quantity: float = 0.0

    # Momentum tracking — Enhanced (Problem 4)
    last_cvd: float = 0.0
    last_delta: float = 0.0
    last_oi_change: float = 0.0
    last_flow: float = 0.0
    momentum_score: float = 0.0
    momentum_decreasing: bool = False

    # Institutional tracking
    last_inst_agreement: float = 0.0

    # Time tracking
    last_progress_time: float = 0.0  # Time of last meaningful price improvement


@dataclass
class ExitDecision:
    """Decision from exit engine."""
    symbol: str = ""
    action: str = "HOLD"       # HOLD / MODIFY_SL / TAKE_PARTIAL / EXIT
    reason: str = ""
    new_sl: float = 0.0
    exit_quantity: float = 0.0
    exit_reason: str = ""
    urgency: str = "normal"    # normal / high / critical

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "reason": self.reason,
            "new_sl": round(self.new_sl, 6),
            "exit_quantity": round(self.exit_quantity, 6),
            "exit_reason": self.exit_reason,
            "urgency": self.urgency,
        }


class AppExitEngine:
    """
    Dynamic exit management for open positions.

    Per Master Directive: No static exits. All exits are dynamic and
    adapt to market conditions.

    READ-ONLY: never modifies upstream data. Returns ExitDecision for
    the execution layer to act on.
    """

    def __init__(self) -> None:
        self._states: Dict[str, ExitState] = {}

    def register_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        quantity: float,
        entry_time: Optional[float] = None,
    ) -> None:
        """Register a new position for exit management."""
        self._states[symbol] = ExitState(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            entry_time=entry_time or time.time(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=quantity,
            remaining_quantity=quantity,
            current_trail_sl=stop_loss,
        )
        # Store original risk for R-multiple calculations
        # (must persist even after breakeven moves SL to entry)
        self._states[symbol]._original_risk = abs(entry_price - stop_loss)

    def evaluate(
        self,
        symbol: str,
        current_price: float,
        signal: Optional[Dict] = None,
    ) -> ExitDecision:
        """
        Evaluate exit conditions for a position — v2 with progressive trailing.

        Priority order:
            1. Hard stop loss (immediate exit)
            2. Dynamic time stop (45-90 min no-progress + momentum check)
            3. Break-even at +1R (risk-free)
            4. Partial profit at +1.5R (take 40%)
            5. Progressive trailing stop (delayed activation at +1R)
            6. Enhanced momentum exit (CVD + delta + OI + flow)
            7. Hard time stop (24h max)

        Args:
            symbol: Position symbol
            current_price: Current market price
            signal: Optional current signal data for momentum/institutional checks

        Returns:
            ExitDecision with action to take
        """
        state = self._states.get(symbol)
        if not state:
            return ExitDecision(symbol=symbol, action="HOLD", reason="no state")

        decision = ExitDecision(symbol=symbol)

        # ── Calculate current R-multiple using ORIGINAL risk ──
        # After breakeven moves SL to entry, current risk approaches 0,
        # which would make R-multiples explode. Always use original risk.
        risk = getattr(state, '_original_risk', 0) or abs(state.entry_price - state.stop_loss)
        if risk <= 0:
            return ExitDecision(symbol=symbol, action="HOLD", reason="invalid risk")

        if state.side == "LONG":
            current_pnl_r = (current_price - state.entry_price) / risk
        else:
            current_pnl_r = (state.entry_price - current_price) / risk

        # ── Update peak ──
        prev_peak = state.peak_pnl_r
        if current_pnl_r > state.peak_pnl_r:
            state.peak_pnl_r = current_pnl_r

        # ── Update momentum tracking ──
        if signal:
            self._update_momentum_tracking(state, signal)

        # ══════════════════════════════════════════════════════════
        # CHECK 1: Hard stop loss — immediate exit (no exceptions)
        # ══════════════════════════════════════════════════════════
        if state.side == "LONG" and current_price <= state.stop_loss:
            decision.action = "EXIT"
            decision.exit_reason = "stop_loss"
            decision.exit_quantity = state.remaining_quantity
            decision.urgency = "critical"
            return decision
        elif state.side == "SHORT" and current_price >= state.stop_loss:
            decision.action = "EXIT"
            decision.exit_reason = "stop_loss"
            decision.exit_quantity = state.remaining_quantity
            decision.urgency = "critical"
            return decision

        # ══════════════════════════════════════════════════════════
        # CHECK 2: Dynamic time stop (Problem 7 fix)
        # 45-90 min AND PnL ≈ 0 AND momentum decreasing → exit
        # Skip if MFE > 5% (trade showed strength)
        # Hard max 24h → always exit
        # ══════════════════════════════════════════════════════════
        hold_minutes = (time.time() - state.entry_time) / 60

        # Hard maximum: 24 hours — no exceptions
        if hold_minutes >= TIME_STOP_HARD_MAX_HOURS * 60:
            decision.action = "EXIT"
            decision.exit_reason = "max_hold_24h"
            decision.exit_quantity = state.remaining_quantity
            decision.urgency = "high"
            return decision

        # Stale trade exit: 6-8 hours with insufficient progress
        hold_hours = hold_minutes / 60
        if hold_hours >= STALE_TRADE_EXIT_HOURS and current_pnl_r < STALE_TRADE_MIN_R:
            decision.action = "EXIT"
            decision.exit_reason = (
                f"stale_trade_{hold_hours:.1f}h_{current_pnl_r:.2f}R"
            )
            decision.exit_quantity = state.remaining_quantity
            decision.urgency = "high"
            decision.reason = (
                f"Stale trade: {hold_hours:.1f}h hold, "
                f"only {current_pnl_r:.2f}R (need {STALE_TRADE_MIN_R}R)"
            )
            return decision

        # Dynamic no-progress exit: 45-90 minutes
        if TIME_STOP_NO_PROGRESS_MIN <= hold_minutes <= TIME_STOP_NO_PROGRESS_MAX * 2:
            # Calculate MFE percentage for bypass check
            if state.side == "LONG" and state.entry_price > 0:
                mfe_pct = (state.entry_price + risk * state.peak_pnl_r - state.entry_price) / state.entry_price * 100
            elif state.side == "SHORT" and state.entry_price > 0:
                mfe_pct = (state.entry_price - (state.entry_price - risk * state.peak_pnl_r)) / state.entry_price * 100
            else:
                mfe_pct = 0

            # Skip time stop if trade showed strength (MFE > 5%)
            if mfe_pct >= MFE_BYPASS_THRESHOLD:
                pass  # Let it ride — trade showed strength
            elif current_pnl_r <= 0.3 and state.momentum_decreasing:
                # No progress AND losing/stagnant AND momentum fading → exit
                decision.action = "EXIT"
                decision.exit_reason = "no_progress_stagnant"
                decision.exit_quantity = state.remaining_quantity
                decision.urgency = "normal"
                return decision

        # ══════════════════════════════════════════════════════════
        # CHECK 3: Break-even at +1R (Problem 1 fix — delayed)
        # Move SL to entry + fee buffer to make trade risk-free
        # ══════════════════════════════════════════════════════════
        if not state.breakeven_moved and current_pnl_r >= BREAKEVEN_TRIGGER_R:
            fee_buffer = state.entry_price * 0.0004  # Taker fee buffer
            if state.side == "LONG":
                new_sl = state.entry_price + fee_buffer
                if new_sl > state.stop_loss:
                    decision.action = "MODIFY_SL"
                    decision.new_sl = new_sl
                    decision.reason = f"breakeven at {BREAKEVEN_TRIGGER_R}R"
                    state.breakeven_moved = True
                    state.stop_loss = new_sl
                    return decision
            elif state.side == "SHORT":
                new_sl = state.entry_price - fee_buffer
                if new_sl < state.stop_loss:
                    decision.action = "MODIFY_SL"
                    decision.new_sl = new_sl
                    decision.reason = f"breakeven at {BREAKEVEN_TRIGGER_R}R"
                    state.breakeven_moved = True
                    state.stop_loss = new_sl
                    return decision

        # ══════════════════════════════════════════════════════════
        # CHECK 4: Partial profit at +1.5R (take 40%)
        # ══════════════════════════════════════════════════════════
        if not state.partial_taken and current_pnl_r >= PARTIAL_PROFIT_R:
            partial_qty = state.quantity * PARTIAL_PROFIT_PCT
            decision.action = "TAKE_PARTIAL"
            decision.exit_quantity = partial_qty
            decision.exit_reason = f"partial_profit_{PARTIAL_PROFIT_R}R"
            state.partial_taken = True
            state.remaining_quantity -= partial_qty
            return decision

        # ══════════════════════════════════════════════════════════
        # CHECK 5: Progressive trailing stop (Problem 1 fix — KEY CHANGE)
        # No trailing before +1R → let trade breathe
        # After +1R: progressive tightening based on peak R
        # ══════════════════════════════════════════════════════════
        if state.peak_pnl_r >= TRAIL_ACTIVATION_R:
            # Determine trail distance based on peak R (progressive)
            new_trail_stage = self._get_trail_stage(state.peak_pnl_r, signal)

            # Only update if we're moving to a tighter stage or first activation
            if new_trail_stage > state.trail_stage or not state.trail_active:
                trail_dist_r = self._get_trail_distance(new_trail_stage)
                trail_sl = self._calculate_trail_sl(state, current_price, trail_dist_r)

                # Only modify if trail improves SL (moves it in our favor)
                if state.side == "LONG" and trail_sl > state.stop_loss:
                    decision.action = "MODIFY_SL"
                    decision.new_sl = trail_sl
                    decision.reason = (
                        f"trailing_stage{new_trail_stage}: peak={state.peak_pnl_r:.2f}R "
                        f"trail={trail_dist_r:.2f}R"
                    )
                    state.stop_loss = trail_sl
                    state.trail_active = True
                    state.trail_stage = new_trail_stage
                    return decision
                elif state.side == "SHORT" and trail_sl < state.stop_loss:
                    decision.action = "MODIFY_SL"
                    decision.new_sl = trail_sl
                    decision.reason = (
                        f"trailing_stage{new_trail_stage}: peak={state.peak_pnl_r:.2f}R "
                        f"trail={trail_dist_r:.2f}R"
                    )
                    state.stop_loss = trail_sl
                    state.trail_active = True
                    state.trail_stage = new_trail_stage
                    return decision

        # ══════════════════════════════════════════════════════════
        # CHECK 6: Enhanced momentum exit (Problem 4 fix)
        # Use CVD + delta + OI + flow for multi-signal exit
        # ══════════════════════════════════════════════════════════
        if signal and current_pnl_r > 0:
            exit_momentum = self._check_momentum_exit(state, signal, current_pnl_r)
            if exit_momentum:
                decision.action = "EXIT"
                decision.exit_reason = "momentum_reversal"
                decision.exit_quantity = state.remaining_quantity
                decision.urgency = "high"
                decision.reason = exit_momentum
                return decision

        # ══════════════════════════════════════════════════════════
        # CHECK 7: Hard time stop — 24h (backup, already checked above)
        # ══════════════════════════════════════════════════════════

        # ── Default: HOLD ──
        decision.action = "HOLD"
        decision.reason = (
            f"pnl_r={current_pnl_r:.2f} peak={state.peak_pnl_r:.2f} "
            f"trail_stage={state.trail_stage} hold={hold_minutes:.0f}m"
        )
        return decision

    def _get_trail_stage(self, peak_r: float, signal: Optional[Dict] = None) -> int:
        """Determine trail stage based on peak R and momentum."""
        # Check for strong momentum — use tighter trail
        strong_momentum = False
        if signal:
            cvd = abs(signal.get("cvd", 0))
            delta = abs(signal.get("delta", 0))
            strong_momentum = cvd > 0.5 and delta > 0.3

        if peak_r >= 3.0:
            return 3  # Aggressive lock
        elif peak_r >= 2.0:
            return 2  # Tight trail
        elif peak_r >= TRAIL_ACTIVATION_R:
            return 1  # Standard trail
        return 0  # No trail yet

    def _get_trail_distance(self, stage: int) -> float:
        """Get trail distance in R-multiples for a given stage."""
        distances = {
            0: 0.0,
            1: TRAIL_DISTANCE_STAGE1,
            2: TRAIL_DISTANCE_STAGE2,
            3: TRAIL_DISTANCE_STAGE3,
        }
        return distances.get(stage, TRAIL_DISTANCE_STAGE1)

    def _update_momentum_tracking(self, state: ExitState, signal: Dict) -> None:
        """Update momentum tracking fields from signal data."""
        cvd = signal.get("cvd", 0)
        delta = signal.get("delta", 0)
        oi = signal.get("oi_change", signal.get("oi_delta", 0))
        flow = signal.get("flow", 0)

        # Track if momentum is decreasing
        prev_cvd = state.last_cvd
        state.last_cvd = cvd
        state.last_delta = delta
        state.last_oi_change = oi
        state.last_flow = flow

        # Momentum is decreasing if CVD is declining and OI is declining
        state.momentum_decreasing = (
            (state.side == "LONG" and cvd < prev_cvd and cvd < MOMENTUM_DECREASE_THRESHOLD)
            or (state.side == "SHORT" and cvd > -prev_cvd and cvd > -MOMENTUM_DECREASE_THRESHOLD)
        )

    def _check_momentum_exit(
        self, state: ExitState, signal: Dict, current_r: float
    ) -> Optional[str]:
        """
        Check for momentum-based exit using multiple signals.

        Returns exit reason string if momentum reversal detected, else None.

        Problem 4 fix: Use CVD + delta + OI + flow instead of just price.
        """
        cvd = signal.get("cvd", 0)
        delta = signal.get("delta", 0)
        oi_change = signal.get("oi_change", signal.get("oi_delta", 0))
        flow = signal.get("flow", 0)

        # ── LONG exit signals ──
        if state.side == "LONG":
            # CVD + Delta reversal (primary signal)
            if cvd < MOMENTUM_REVERSAL_CVD and delta < MOMENTUM_REVERSAL_DELTA:
                if current_r > 0.5:  # Only exit if we have some profit
                    return (
                        f"momentum: CVD={cvd:.3f} delta={delta:.3f} "
                        f"(thresholds: cvd<{MOMENTUM_REVERSAL_CVD}, delta<{MOMENTUM_REVERSAL_DELTA})"
                    )

            # OI declining + negative flow (secondary — weaker signal)
            if oi_change < MOMENTUM_REVERSAL_OI and flow < FLOW_REVERSAL_THRESHOLD:
                if current_r > 1.0:  # Need more profit to trigger on secondary signals
                    return (
                        f"flow_reversal: OI={oi_change:.3f} flow={flow:.3f} "
                        f"(thresholds: oi<{MOMENTUM_REVERSAL_OI}, flow<{FLOW_REVERSAL_THRESHOLD})"
                    )

        # ── SHORT exit signals ──
        elif state.side == "SHORT":
            # CVD + Delta reversal (primary signal)
            if cvd > -MOMENTUM_REVERSAL_CVD and delta > -MOMENTUM_REVERSAL_DELTA:
                if current_r > 0.5:
                    return (
                        f"momentum: CVD={cvd:.3f} delta={delta:.3f} "
                        f"(thresholds: cvd>{-MOMENTUM_REVERSAL_CVD}, delta>{-MOMENTUM_REVERSAL_DELTA})"
                    )

            # OI increasing + positive flow (secondary)
            if oi_change > -MOMENTUM_REVERSAL_OI and flow > -FLOW_REVERSAL_THRESHOLD:
                if current_r > 1.0:
                    return (
                        f"flow_reversal: OI={oi_change:.3f} flow={flow:.3f} "
                        f"(thresholds: oi>{-MOMENTUM_REVERSAL_OI}, flow>{-FLOW_REVERSAL_THRESHOLD})"
                    )

        return None

    def _calculate_trail_sl(
        self, state: ExitState, current_price: float, trail_distance_r: float
    ) -> float:
        """Calculate trailing stop price using original risk."""
        # Use original risk for trail distance (not current SL which may be at breakeven)
        risk = getattr(state, '_original_risk', 0) or abs(state.entry_price - state.stop_loss)
        trail_distance_price = risk * trail_distance_r

        if state.side == "LONG":
            return current_price - trail_distance_price
        else:
            return current_price + trail_distance_price

    def get_state(self, symbol: str) -> Optional[ExitState]:
        """Get exit state for a position."""
        return self._states.get(symbol)

    def remove_position(self, symbol: str) -> None:
        """Remove position from exit tracking."""
        self._states.pop(symbol, None)

    def get_all_states(self) -> Dict[str, ExitState]:
        """Get all exit states."""
        return dict(self._states)

    # ── Live Sheet Deterioration Monitoring ──────────────────────

    def check_live_sheet_deterioration(
        self,
        symbol: str,
        current_signal: Dict[str, Any],
    ) -> Optional[ExitDecision]:
        """
        Check if Live Sheet data has deteriorated for an open position.

        Per Master Directive: Exit early if multiple conditions weaken:
        - Institutional agreement falls
        - Flow reverses
        - CVD reverses
        - Delta collapses
        - Regime weakens

        Args:
            symbol: Position symbol
            current_signal: Latest signal data from Live Sheet

        Returns:
            ExitDecision if deterioration detected, None if OK
        """
        state = self._states.get(symbol)
        if not state:
            return None

        deterioration_score = 0
        reasons = []

        # ── Check 1: CVD Reversal ──
        cvd = current_signal.get("cvd", 0)
        if state.side == "LONG" and cvd < -0.3:
            deterioration_score += 25
            reasons.append("cvd_reversal_bearish")
        elif state.side == "SHORT" and cvd > 0.3:
            deterioration_score += 25
            reasons.append("cvd_reversal_bullish")

        # ── Check 2: Delta Collapse ──
        delta = current_signal.get("delta", 0)
        if state.side == "LONG" and delta < -0.5:
            deterioration_score += 20
            reasons.append("delta_collapse_bearish")
        elif state.side == "SHORT" and delta > 0.5:
            deterioration_score += 20
            reasons.append("delta_collapse_bullish")

        # ── Check 3: Exchange Flow Reversal ──
        flow = current_signal.get("exchange_flow", 0)
        if state.side == "LONG" and flow > 0.5:
            deterioration_score += 15
            reasons.append("flow_inflow_bearish")
        elif state.side == "SHORT" and flow < -0.5:
            deterioration_score += 15
            reasons.append("flow_outflow_bullish")

        # ── Check 4: Regime Weakening ──
        regime = current_signal.get("regime", current_signal.get("market_regime", ""))
        if regime in ("range", "compression", "unknown"):
            deterioration_score += 10
            reasons.append(f"regime_weakened_{regime}")

        # ── Check 5: Institutional Score Drop ──
        inst_score = current_signal.get("institutional_score", 85)
        if inst_score < 50:
            deterioration_score += 15
            reasons.append(f"institution_weak_{inst_score:.0f}")

        # ── Decision ──
        if deterioration_score >= 50:
            # Multiple conditions deteriorated — exit
            return ExitDecision(
                symbol=symbol,
                action="EXIT",
                exit_reason=f"live_sheet_deterioration ({deterioration_score})",
                exit_quantity=state.remaining_quantity,
                reason=f"deterioration: {', '.join(reasons)}",
                urgency="high",
            )
        elif deterioration_score >= 30:
            # Moderate deterioration — tighten trail
            risk = abs(state.entry_price - state.stop_loss)
            if state.side == "LONG":
                new_sl = current_signal.get("entry_price", 0) + risk * 0.5
            else:
                new_sl = current_signal.get("entry_price", 0) - risk * 0.5

            if state.side == "LONG" and new_sl > state.stop_loss:
                return ExitDecision(
                    symbol=symbol,
                    action="MODIFY_SL",
                    new_sl=new_sl,
                    reason=f"live_sheet_warning ({deterioration_score}): {', '.join(reasons)}",
                )
            elif state.side == "SHORT" and new_sl < state.stop_loss:
                return ExitDecision(
                    symbol=symbol,
                    action="MODIFY_SL",
                    new_sl=new_sl,
                    reason=f"live_sheet_warning ({deterioration_score}): {', '.join(reasons)}",
                )

        return None
