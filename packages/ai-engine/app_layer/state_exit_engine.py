"""
State-Based Exit Engine — Trend-health-aware exits instead of price retracement.

Per Executive Assessment v5:
    "Replace trailing stop with state-based trailing.
     Entry → Expansion → Trend Healthy? → YES → Hold
     → Momentum Weakening? → YES → Tighten stop
     → Structure Break? → Exit

     The stop should react to trend deterioration, not merely to price retracement."

Key Innovation:
    Traditional trailing: Exit when price retraces X% from peak
    State-based exit: Exit when trend STRUCTURE deteriorates

State Machine:
    ENTRY → EXPANSION → TRENDING → WEAKENING → EXIT

    Each state has different exit behavior:
    - ENTRY: No trailing, let trade breathe
    - EXPANSYON: Breakeven only, let momentum run
    - TRENDING: Wide trail, respect trend structure
    - WEAKENING: Tight trail, prepare for exit
    - EXIT: Execute exit

Trend Health Indicators:
    1. Price vs EMA20 — trend direction
    2. EMA20 vs EMA50 — trend strength
    3. CVD slope — momentum direction
    4. OI change — participation
    5. Volume trend — conviction
    6. ATR expansion — volatility support

READ-ONLY: Returns exit decisions for execution layer.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# STATE DEFINITIONS
# ═══════════════════════════════════════════════════════════════

class TradeState(Enum):
    """Trade lifecycle states."""
    ENTRY = "entry"              # Just entered, no trailing
    EXPANSION = "expansion"      # Moving in profit, let it run
    TRENDING = "trending"        # Strong trend, wide trail
    WEAKENING = "weakening"      # Trend fading, tight trail
    EXIT = "exit"                # Exit triggered


class TrendHealth(Enum):
    """Trend health assessment."""
    STRONG = "strong"            # All indicators aligned
    HEALTHY = "healthy"          # Mostly aligned, minor weakness
    NEUTRAL = "neutral"          # Mixed signals
    WEAK = "weak"                # Significant deterioration
    BROKEN = "broken"            # Trend structure broken


@dataclass
class TrendIndicators:
    """Current trend health indicators."""
    price_vs_ema20: float = 0.0    # Positive = above EMA20
    ema20_vs_ema50: float = 0.0    # Positive = EMA20 above EMA50
    cvd_slope: float = 0.0         # Positive = CVD increasing
    oi_change: float = 0.0         # Positive = OI increasing
    volume_trend: float = 0.0      # Positive = volume expanding
    atr_expansion: float = 0.0     # Positive = ATR expanding
    momentum_score: float = 0.0    # -1 to 1, composite momentum
    trend_health: TrendHealth = TrendHealth.NEUTRAL

    def to_dict(self) -> Dict:
        return {
            "price_vs_ema20": round(self.price_vs_ema20, 4),
            "ema20_vs_ema50": round(self.ema20_vs_ema50, 4),
            "cvd_slope": round(self.cvd_slope, 4),
            "oi_change": round(self.oi_change, 4),
            "volume_trend": round(self.volume_trend, 4),
            "atr_expansion": round(self.atr_expansion, 4),
            "momentum_score": round(self.momentum_score, 4),
            "trend_health": self.trend_health.value,
        }


@dataclass
class StateExitDecision:
    """Decision from state-based exit engine."""
    symbol: str = ""
    action: str = "HOLD"         # HOLD / MODIFY_SL / TAKE_PARTIAL / EXIT
    reason: str = ""
    new_sl: float = 0.0
    exit_quantity: float = 0.0
    exit_reason: str = ""
    urgency: str = "normal"      # normal / high / critical

    # State info
    current_state: str = ""
    trend_health: str = ""
    state_duration_minutes: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "reason": self.reason,
            "new_sl": round(self.new_sl, 6),
            "exit_quantity": round(self.exit_quantity, 6),
            "exit_reason": self.exit_reason,
            "urgency": self.urgency,
            "current_state": self.current_state,
            "trend_health": self.trend_health,
            "state_duration_minutes": round(self.state_duration_minutes, 1),
        }


@dataclass
class PositionState:
    """Tracks state for a single position."""
    symbol: str = ""
    side: str = ""
    entry_price: float = 0.0
    entry_time: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    quantity: float = 0.0
    remaining_quantity: float = 0.0

    # State machine
    current_state: TradeState = TradeState.ENTRY
    state_entry_time: float = 0.0
    previous_state: TradeState = TradeState.ENTRY

    # Trailing
    peak_pnl_r: float = 0.0
    original_risk: float = 0.0
    breakeven_moved: bool = False
    partial_taken: bool = False

    # Trend tracking
    consecutive_weak_reads: int = 0
    consecutive_strong_reads: int = 0
    last_health: TrendHealth = TrendHealth.NEUTRAL


# ═══════════════════════════════════════════════════════════════
# STATE CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# State transition thresholds (in R-multiples)
EXPANSION_TRIGGER_R = 0.5     # Enter EXPANSION at +0.5R
TRENDING_TRIGGER_R = 1.5      # Enter TRENDING at +1.5R
WEAKENING_TRIGGER_R = 2.5     # Enter WEAKENING at +2.5R (if health deteriorates)

# Trail distances by state (in R-multiples from peak)
STATE_TRAIL_DISTANCES = {
    TradeState.ENTRY: 0.0,       # No trail
    TradeState.EXPANSION: 0.0,   # No trail (breakeven only)
    TradeState.TRENDING: 1.0,    # Wide trail (1R below peak)
    TradeState.WEAKENING: 0.5,   # Tight trail (0.5R below peak)
    TradeState.EXIT: 0.0,        # No trail (exiting)
}

# Trend health thresholds
HEALTH_STRONG_THRESHOLD = 0.6
HEALTH_HEALTHY_THRESHOLD = 0.3
HEALTH_WEAK_THRESHOLD = -0.2
HEALTH_BROKEN_THRESHOLD = -0.5

# State transition requires N consecutive reads
CONSECUTIVE_READS_FOR_TRANSITION = 3

# Partial profit
PARTIAL_PROFIT_R = 2.0
PARTIAL_PROFIT_PCT = 0.40


class StateBasedExitEngine:
    """
    Trend-health-aware exit engine.

    Per Executive Assessment v5:
        "The stop should react to trend deterioration, not merely
         to price retracement."

    This engine:
        1. Tracks trade state (ENTRY → EXPANSION → TRENDING → WEAKENING → EXIT)
        2. Evaluates trend health using multiple indicators
        3. Adjusts trailing stop based on state, not just price
        4. Exits when trend structure breaks, not when price retraces

    READ-ONLY: Returns exit decisions for execution layer.
    """

    def __init__(self) -> None:
        self._positions: Dict[str, PositionState] = {}

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
        """Register a new position for state-based exit management."""
        risk = abs(entry_price - stop_loss)
        if risk <= 0:
            risk = entry_price * 0.01

        now = time.time()
        self._positions[symbol] = PositionState(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            entry_time=entry_time or now,
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=quantity,
            remaining_quantity=quantity,
            current_state=TradeState.ENTRY,
            state_entry_time=entry_time or now,
            original_risk=risk,
        )

    def evaluate(
        self,
        symbol: str,
        current_price: float,
        indicators: Optional[TrendIndicators] = None,
    ) -> StateExitDecision:
        """
        Evaluate exit conditions using state-based logic.

        Args:
            symbol: Position symbol
            current_price: Current market price
            indicators: Current trend health indicators

        Returns:
            StateExitDecision with action to take
        """
        pos = self._positions.get(symbol)
        if not pos:
            return StateExitDecision(symbol=symbol, action="HOLD", reason="not registered")

        decision = StateExitDecision(symbol=symbol)

        # ── Calculate current R-multiple ──
        risk = pos.original_risk
        if risk <= 0:
            return StateExitDecision(symbol=symbol, action="HOLD", reason="invalid risk")

        if pos.side == "LONG":
            current_r = (current_price - pos.entry_price) / risk
        else:
            current_r = (pos.entry_price - current_price) / risk

        # Update peak
        if current_r > pos.peak_pnl_r:
            pos.peak_pnl_r = current_r

        # ── Check hard stop loss first ──
        if pos.side == "LONG" and current_price <= pos.stop_loss:
            return StateExitDecision(
                symbol=symbol, action="EXIT", exit_reason="stop_loss",
                exit_quantity=pos.remaining_quantity, urgency="critical",
                current_state=pos.current_state.value,
            )
        elif pos.side == "SHORT" and current_price >= pos.stop_loss:
            return StateExitDecision(
                symbol=symbol, action="EXIT", exit_reason="stop_loss",
                exit_quantity=pos.remaining_quantity, urgency="critical",
                current_state=pos.current_state.value,
            )

        # ── Evaluate trend health ──
        if indicators:
            health = self._assess_trend_health(indicators, pos.side)
            pos.last_health = health
        else:
            health = pos.last_health

        # ── State machine transition ──
        new_state = self._determine_state(pos, current_r, health)

        if new_state != pos.current_state:
            # Check if we have enough consecutive reads
            if new_state == TradeState.WEAKENING:
                pos.consecutive_weak_reads += 1
                pos.consecutive_strong_reads = 0
                if pos.consecutive_weak_reads < CONSECUTIVE_READS_FOR_TRANSITION:
                    new_state = pos.current_state  # Don't transition yet
            elif new_state == TradeState.TRENDING:
                pos.consecutive_strong_reads += 1
                pos.consecutive_weak_reads = 0
                if pos.consecutive_strong_reads < CONSECUTIVE_READS_FOR_TRANSITION:
                    new_state = pos.current_state
            else:
                pos.consecutive_weak_reads = 0
                pos.consecutive_strong_reads = 0

            if new_state != pos.current_state:
                pos.previous_state = pos.current_state
                pos.current_state = new_state
                pos.state_entry_time = time.time()
                logger.info(
                    "📊 STATE: {} {} → {} (was {}, R={:.2f}, health={})",
                    pos.side, symbol, new_state.value, pos.previous_state.value,
                    current_r, health.value,
                )

        # ── Execute state-based exit logic ──
        decision.current_state = pos.current_state.value
        decision.trend_health = health.value
        decision.state_duration_minutes = (time.time() - pos.state_entry_time) / 60

        if pos.current_state == TradeState.EXIT:
            return StateExitDecision(
                symbol=symbol, action="EXIT", exit_reason="trend_broken",
                exit_quantity=pos.remaining_quantity, urgency="high",
                current_state=pos.current_state.value, trend_health=health.value,
            )

        if pos.current_state == TradeState.WEAKENING:
            # Tight trail — prepare for exit
            trail_dist = STATE_TRAIL_DISTANCES[TradeState.WEAKENING]
            trail_sl = self._calc_trail_sl(pos, current_price, trail_dist)

            if trail_sl > pos.stop_loss if pos.side == "LONG" else trail_sl < pos.stop_loss:
                pos.stop_loss = trail_sl
                return StateExitDecision(
                    symbol=symbol, action="MODIFY_SL", new_sl=trail_sl,
                    reason=f"weakening: tight trail ({trail_dist}R), health={health.value}",
                    current_state=pos.current_state.value, trend_health=health.value,
                )

            # Check if trailing SL hit
            if pos.side == "LONG" and current_price <= pos.stop_loss:
                return StateExitDecision(
                    symbol=symbol, action="EXIT", exit_reason="weakening_trail_hit",
                    exit_quantity=pos.remaining_quantity, urgency="high",
                    current_state=pos.current_state.value, trend_health=health.value,
                )
            elif pos.side == "SHORT" and current_price >= pos.stop_loss:
                return StateExitDecision(
                    symbol=symbol, action="EXIT", exit_reason="weakening_trail_hit",
                    exit_quantity=pos.remaining_quantity, urgency="high",
                    current_state=pos.current_state.value, trend_health=health.value,
                )

        if pos.current_state == TradeState.TRENDING:
            # Wide trail — let trend run
            trail_dist = STATE_TRAIL_DISTANCES[TradeState.TRENDING]
            trail_sl = self._calc_trail_sl(pos, current_price, trail_dist)

            if trail_sl > pos.stop_loss if pos.side == "LONG" else trail_sl < pos.stop_loss:
                pos.stop_loss = trail_sl
                return StateExitDecision(
                    symbol=symbol, action="MODIFY_SL", new_sl=trail_sl,
                    reason=f"trending: wide trail ({trail_dist}R), health={health.value}",
                    current_state=pos.current_state.value, trend_health=health.value,
                )

        if pos.current_state == TradeState.EXPANSION:
            # Breakeven only
            if not pos.breakeven_moved and pos.peak_pnl_r >= EXPANSION_TRIGGER_R:
                fee_buffer = pos.entry_price * 0.0004
                if pos.side == "LONG":
                    new_sl = pos.entry_price + fee_buffer
                    if new_sl > pos.stop_loss:
                        pos.stop_loss = new_sl
                        pos.breakeven_moved = True
                        return StateExitDecision(
                            symbol=symbol, action="MODIFY_SL", new_sl=new_sl,
                            reason="expansion: breakeven",
                            current_state=pos.current_state.value, trend_health=health.value,
                        )
                elif pos.side == "SHORT":
                    new_sl = pos.entry_price - fee_buffer
                    if new_sl < pos.stop_loss:
                        pos.stop_loss = new_sl
                        pos.breakeven_moved = True
                        return StateExitDecision(
                            symbol=symbol, action="MODIFY_SL", new_sl=new_sl,
                            reason="expansion: breakeven",
                            current_state=pos.current_state.value, trend_health=health.value,
                        )

            # Partial profit at +2R
            if not pos.partial_taken and pos.peak_pnl_r >= PARTIAL_PROFIT_R:
                partial_qty = pos.quantity * PARTIAL_PROFIT_PCT
                pos.partial_taken = True
                pos.remaining_quantity -= partial_qty
                return StateExitDecision(
                    symbol=symbol, action="TAKE_PARTIAL", exit_quantity=partial_qty,
                    exit_reason=f"partial_profit_{PARTIAL_PROFIT_R}R",
                    current_state=pos.current_state.value, trend_health=health.value,
                )

        # ── Default: HOLD ──
        decision.action = "HOLD"
        decision.reason = f"state={pos.current_state.value} health={health.value} R={current_r:.2f}"
        return decision

    def _determine_state(
        self,
        pos: PositionState,
        current_r: float,
        health: TrendHealth,
    ) -> TradeState:
        """Determine the appropriate state based on R-multiple and trend health."""
        # If trend is broken, go to EXIT regardless of R
        if health == TrendHealth.BROKEN and pos.peak_pnl_r >= 1.0:
            return TradeState.EXIT

        # If health is weak and we're in trending state, transition to WEAKENING
        if health in (TrendHealth.WEAK, TrendHealth.BROKEN):
            if pos.current_state == TradeState.TRENDING:
                return TradeState.WEAKENING
            elif pos.current_state == TradeState.EXPANSION and pos.peak_pnl_r >= WEAKENING_TRIGGER_R:
                return TradeState.WEAKENING

        # State transitions based on R-multiples
        if current_r >= WEAKENING_TRIGGER_R and health in (TrendHealth.WEAK, TrendHealth.BROKEN):
            return TradeState.WEAKENING
        elif current_r >= TRENDING_TRIGGER_R and health in (TrendHealth.STRONG, TrendHealth.HEALTHY):
            return TradeState.TRENDING
        elif current_r >= EXPANSION_TRIGGER_R:
            if pos.current_state == TradeState.ENTRY:
                return TradeState.EXPANSION
            elif pos.current_state == TradeState.TRENDING and health == TrendHealth.WEAK:
                return TradeState.WEAKENING

        return pos.current_state  # Stay in current state

    def _assess_trend_health(
        self,
        indicators: TrendIndicators,
        side: str,
    ) -> TrendHealth:
        """Assess trend health from indicators."""
        # Calculate composite score
        score = 0.0
        weights = {
            "ema_alignment": 0.25,
            "cvd": 0.20,
            "oi": 0.15,
            "volume": 0.15,
            "atr": 0.10,
            "momentum": 0.15,
        }

        # EMA alignment (price vs EMA20, EMA20 vs EMA50)
        if side == "LONG":
            ema_score = 0
            if indicators.price_vs_ema20 > 0:
                ema_score += 0.5
            if indicators.ema20_vs_ema50 > 0:
                ema_score += 0.5
        else:
            ema_score = 0
            if indicators.price_vs_ema20 < 0:
                ema_score += 0.5
            if indicators.ema20_vs_ema50 < 0:
                ema_score += 0.5

        score += ema_score * weights["ema_alignment"]

        # CVD slope
        if side == "LONG":
            cvd_score = max(0, min(1, (indicators.cvd_slope + 0.5)))
        else:
            cvd_score = max(0, min(1, (-indicators.cvd_slope + 0.5)))
        score += cvd_score * weights["cvd"]

        # OI change
        if side == "LONG":
            oi_score = max(0, min(1, (indicators.oi_change + 0.5)))
        else:
            oi_score = max(0, min(1, (-indicators.oi_change + 0.5)))
        score += oi_score * weights["oi"]

        # Volume trend
        vol_score = max(0, min(1, (indicators.volume_trend + 0.5)))
        score += vol_score * weights["volume"]

        # ATR expansion
        atr_score = max(0, min(1, (indicators.atr_expansion + 0.5)))
        score += atr_score * weights["atr"]

        # Momentum
        if side == "LONG":
            mom_score = max(0, min(1, (indicators.momentum_score + 1) / 2))
        else:
            mom_score = max(0, min(1, (-indicators.momentum_score + 1) / 2))
        score += mom_score * weights["momentum"]

        # Classify
        if score >= HEALTH_STRONG_THRESHOLD:
            return TrendHealth.STRONG
        elif score >= HEALTH_HEALTHY_THRESHOLD:
            return TrendHealth.HEALTHY
        elif score >= HEALTH_WEAK_THRESHOLD:
            return TrendHealth.NEUTRAL
        elif score >= HEALTH_BROKEN_THRESHOLD:
            return TrendHealth.WEAK
        return TrendHealth.BROKEN

    def _calc_trail_sl(
        self,
        pos: PositionState,
        current_price: float,
        trail_distance_r: float,
    ) -> float:
        """Calculate trailing stop price."""
        trail_dist_price = pos.original_risk * trail_distance_r

        if pos.side == "LONG":
            return current_price - trail_dist_price
        else:
            return current_price + trail_dist_price

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════

    def get_state(self, symbol: str) -> Optional[str]:
        """Get current state for a symbol."""
        pos = self._positions.get(symbol)
        return pos.current_state.value if pos else None

    def get_all_states(self) -> Dict[str, Dict]:
        """Get all position states."""
        return {
            sym: {
                "state": pos.current_state.value,
                "health": pos.last_health.value,
                "peak_r": round(pos.peak_pnl_r, 3),
                "breakeven": pos.breakeven_moved,
                "partial": pos.partial_taken,
            }
            for sym, pos in self._positions.items()
        }

    def cleanup(self, symbol: str) -> None:
        """Remove tracking for a closed position."""
        self._positions.pop(symbol, None)
