"""
Continuous Trade Monitor — Re-score open positions every refresh cycle.

Production analysis (343 trades, PF 0.94, expectancy -$0.61):
    - TP1 hit: 28.3%, TP2/3: 0%, SL hit: 42%
    - Avg hold: 163 min
    - Problem: App keeps weak trades alive instead of exiting earlier

This engine re-evaluates open positions every 5-10 seconds using live
market data. If trade quality deteriorates, it triggers exit or reduction.

This does NOT touch EMA V5, Smart Money, RR Audit, or Research Platform.
It only governs what happens AFTER a trade is already open.

Components:
    1. Trade Quality Re-score — Recompute quality from live data each cycle
    2. Confidence Decay Detection — Track confidence trajectory over time
    3. Dynamic Position Scaling — Reduce size as conviction drops
    4. Time-Based Expectancy Exit — Close trades failing expected progress

READ-ONLY: Returns decisions for execution layer to act on.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Quality score thresholds
QUALITY_EXIT_THRESHOLD = 35       # Exit if quality drops below this
QUALITY_REDUCE_THRESHOLD = 50     # Reduce position if quality below this
QUALITY_RESTORE_THRESHOLD = 65    # Stop reducing when quality recovers

# Confidence decay
CONFIDENCE_DECAY_WINDOW = 12      # Number of readings to track (12 × 5s = 60s)
CONFIDENCE_DECAY_EXIT = -15       # Exit if confidence drops this much from peak
CONFIDENCE_DECAY_REDUCE = -10     # Reduce if confidence drops this much

# Dynamic scaling levels (quality → position fraction)
SCALE_LEVELS = [
    (70, 1.0),    # Quality ≥ 70 → full position
    (55, 0.75),   # Quality ≥ 55 → 75% position
    (45, 0.50),   # Quality ≥ 45 → 50% position
    (35, 0.25),   # Quality ≥ 35 → 25% position
    (0,  0.0),    # Quality < 35 → exit
]

# Time-based expectancy
EXPECTANCY_CHECK_HOURS = 2.0      # Check expected progress after this
EXPECTANCY_MIN_R = 0.3            # Minimum R after expectancy window
EXPECTANCY_HARD_HOURS = 6.0       # Hard exit if < 0.3R after 6 hours

# Quality component weights (live re-scoring)
QUALITY_WEIGHTS = {
    "flow_alignment":     0.20,   # Flow signal supports trade direction
    "cvd_momentum":       0.18,   # CVD slope supports trade direction
    "oi_health":          0.15,   # OI change supports trade direction
    "volume_support":     0.12,   # Volume confirms the move
    "price_action":       0.15,   # Price moving in trade direction
    "funding_alignment":  0.10,   # Funding rate supports direction
    "inst_agreement":     0.10,   # Institutional data still agrees
}


@dataclass
class TradeQualityReading:
    """Single quality reading for an open position."""
    timestamp: float = 0.0
    quality_score: float = 0.0    # 0-100 composite
    components: Dict[str, float] = field(default_factory=dict)
    current_r: float = 0.0
    hold_minutes: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "quality_score": round(self.quality_score, 1),
            "current_r": round(self.current_r, 2),
            "hold_minutes": round(self.hold_minutes, 0),
            "components": {k: round(v, 2) for k, v in self.components.items()},
        }


@dataclass
class PositionMonitorState:
    """Continuous monitoring state for one position."""
    symbol: str = ""
    side: str = ""
    entry_price: float = 0.0
    entry_time: float = 0.0
    stop_loss: float = 0.0
    quantity: float = 0.0
    remaining_quantity: float = 0.0

    # Quality tracking
    readings: deque = field(default_factory=lambda: deque(maxlen=60))
    peak_quality: float = 50.0
    current_quality: float = 50.0
    quality_trend: float = 0.0     # Positive = improving, negative = declining

    # Confidence tracking
    peak_confidence: float = 50.0
    current_confidence: float = 50.0
    confidence_history: deque = field(default_factory=lambda: deque(maxlen=CONFIDENCE_DECAY_WINDOW))

    # Scaling state
    current_scale: float = 1.0
    last_scale_action: str = ""
    scale_history: List[Dict] = field(default_factory=list)

    # Exit state
    exit_triggered: bool = False
    exit_reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "current_quality": round(self.current_quality, 1),
            "peak_quality": round(self.peak_quality, 1),
            "quality_trend": round(self.quality_trend, 2),
            "current_confidence": round(self.current_confidence, 1),
            "peak_confidence": round(self.peak_confidence, 1),
            "current_scale": round(self.current_scale, 2),
            "readings_count": len(self.readings),
        }


@dataclass
class MonitorDecision:
    """Decision from continuous trade monitor."""
    symbol: str = ""
    action: str = "HOLD"          # HOLD / REDUCE / EXIT
    reason: str = ""
    reduce_fraction: float = 0.0  # 0-1, how much to keep (1 = no change)

    # Quality info
    quality_score: float = 0.0
    quality_trend: float = 0.0
    quality_change: float = 0.0   # Change from previous reading

    # Confidence info
    confidence_score: float = 0.0
    confidence_decay: float = 0.0  # Drop from peak

    # Timing
    hold_minutes: float = 0.0
    current_r: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "reason": self.reason,
            "reduce_fraction": round(self.reduce_fraction, 2),
            "quality_score": round(self.quality_score, 1),
            "quality_trend": round(self.quality_trend, 2),
            "confidence_score": round(self.confidence_score, 1),
            "confidence_decay": round(self.confidence_decay, 1),
            "hold_minutes": round(self.hold_minutes, 0),
            "current_r": round(self.current_r, 2),
        }


class ContinuousTradeMonitor:
    """
    Re-evaluates open positions every refresh cycle using live market data.

    Instead of opening a trade and waiting passively for TP/SL, this engine:
        1. Recomputes trade quality from live data (flow, OI, CVD, volume)
        2. Detects confidence decay over time
        3. Triggers position reduction before full exit
        4. Enforces time-based expectancy exits

    This is the post-entry counterpart to the pre-entry Portfolio Admission Engine.

    READ-ONLY: Returns decisions for execution layer.
    """

    def __init__(self) -> None:
        self._positions: Dict[str, PositionMonitorState] = {}

        # Stats
        self._total_evaluations: int = 0
        self._total_reductions: int = 0
        self._total_exits: int = 0

    # ─────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────

    def register_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        quantity: float,
        entry_time: Optional[float] = None,
        initial_confidence: float = 50.0,
    ) -> None:
        """Register a new position for continuous monitoring."""
        self._positions[symbol] = PositionMonitorState(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            entry_time=entry_time or time.time(),
            stop_loss=stop_loss,
            quantity=quantity,
            remaining_quantity=quantity,
            peak_confidence=initial_confidence,
            current_confidence=initial_confidence,
        )

    def unregister_position(self, symbol: str) -> None:
        """Remove a position from monitoring."""
        self._positions.pop(symbol, None)

    def evaluate(
        self,
        symbol: str,
        current_price: float,
        signal: Dict[str, Any],
    ) -> MonitorDecision:
        """
        Re-evaluate an open position using live market data.

        Call this every refresh cycle (5-10 seconds) for each open position.

        Args:
            symbol: Position symbol
            current_price: Current market price
            signal: Live market data dict with flow, OI, CVD, volume, etc.

        Returns:
            MonitorDecision with HOLD / REDUCE / EXIT
        """
        self._total_evaluations += 1

        state = self._positions.get(symbol)
        if not state:
            return MonitorDecision(symbol=symbol, action="HOLD", reason="not_registered")

        if state.exit_triggered:
            return MonitorDecision(
                symbol=symbol, action="EXIT",
                reason=state.exit_reason,
            )

        # ── Calculate current R ──
        risk = abs(state.entry_price - state.stop_loss)
        if risk <= 0:
            risk = state.entry_price * 0.01

        if state.side == "LONG":
            current_r = (current_price - state.entry_price) / risk
        else:
            current_r = (state.entry_price - current_price) / risk

        hold_minutes = (time.time() - state.entry_time) / 60

        # ── Re-score trade quality from live data ──
        quality_score, components = self._score_trade_quality(state, signal)

        # Record reading
        reading = TradeQualityReading(
            timestamp=time.time(),
            quality_score=quality_score,
            components=components,
            current_r=current_r,
            hold_minutes=hold_minutes,
        )
        state.readings.append(reading)

        # Update quality tracking
        prev_quality = state.current_quality
        state.current_quality = quality_score
        if quality_score > state.peak_quality:
            state.peak_quality = quality_score

        # Calculate quality trend (slope of last N readings)
        state.quality_trend = self._calculate_trend(state.readings)

        # ── Update confidence tracking ──
        confidence = signal.get("confidence", state.current_confidence)
        state.confidence_history.append(confidence)
        state.current_confidence = confidence
        if confidence > state.peak_confidence:
            state.peak_confidence = confidence

        confidence_decay = state.peak_confidence - state.current_confidence

        # ── Build decision ──
        decision = MonitorDecision(
            symbol=symbol,
            quality_score=quality_score,
            quality_trend=state.quality_trend,
            quality_change=quality_score - prev_quality,
            confidence_score=confidence,
            confidence_decay=confidence_decay,
            hold_minutes=hold_minutes,
            current_r=current_r,
        )

        # ═══════════════════════════════════════════════════════
        # CHECK 1: Quality Collapse Exit
        # If quality drops below exit threshold → exit immediately
        # ═══════════════════════════════════════════════════════
        if quality_score < QUALITY_EXIT_THRESHOLD:
            decision.action = "EXIT"
            decision.reason = (
                f"quality_collapse: {quality_score:.1f} < {QUALITY_EXIT_THRESHOLD} "
                f"(peak={state.peak_quality:.1f}, trend={state.quality_trend:.2f})"
            )
            state.exit_triggered = True
            state.exit_reason = decision.reason
            self._total_exits += 1
            logger.warning(
                "🔴 QUALITY EXIT: {} — score={:.1f} < {} (peak={:.1f}, trend={:.2f})",
                symbol, quality_score, QUALITY_EXIT_THRESHOLD,
                state.peak_quality, state.quality_trend,
            )
            return decision

        # ═══════════════════════════════════════════════════════
        # CHECK 2: Confidence Decay Exit
        # If confidence dropped significantly from peak → exit
        # ═══════════════════════════════════════════════════════
        if confidence_decay >= abs(CONFIDENCE_DECAY_EXIT):
            decision.action = "EXIT"
            decision.reason = (
                f"confidence_decay: peak={state.peak_confidence:.0f} "
                f"→ now={confidence:.0f} (drop={confidence_decay:.0f})"
            )
            state.exit_triggered = True
            state.exit_reason = decision.reason
            self._total_exits += 1
            logger.warning(
                "🔴 CONFIDENCE DECAY EXIT: {} — peak={} → now={} (drop={})",
                symbol, state.peak_confidence, confidence, confidence_decay,
            )
            return decision

        # ═══════════════════════════════════════════════════════
        # CHECK 3: Time-Based Expectancy Exit
        # If trade hasn't made expected progress within time window
        # ═══════════════════════════════════════════════════════
        hold_hours = hold_minutes / 60

        if hold_hours >= EXPECTANCY_HARD_HOURS and current_r < EXPECTANCY_MIN_R:
            decision.action = "EXIT"
            decision.reason = (
                f"expectancy_exit: {hold_hours:.1f}h hold, "
                f"only {current_r:.2f}R (need {EXPECTANCY_MIN_R}R)"
            )
            state.exit_triggered = True
            state.exit_reason = decision.reason
            self._total_exits += 1
            logger.warning(
                "🔴 EXPECTANCY EXIT: {} — {:.1f}h hold, only {:.2f}R",
                symbol, hold_hours, current_r,
            )
            return decision

        if hold_hours >= EXPECTANCY_CHECK_HOURS and current_r < EXPECTANCY_MIN_R:
            # Not yet at hard exit, but warn and potentially reduce
            if quality_score < QUALITY_REDUCE_THRESHOLD:
                decision.action = "REDUCE"
                decision.reduce_fraction = 0.5  # Cut to 50%
                decision.reason = (
                    f"expectancy_warning: {hold_hours:.1f}h, {current_r:.2f}R, "
                    f"quality={quality_score:.1f} — reducing to 50%"
                )
                self._total_reductions += 1
                return decision

        # ═══════════════════════════════════════════════════════
        # CHECK 4: Dynamic Position Scaling
        # Reduce position as quality/con conviction deteriorates
        # ═══════════════════════════════════════════════════════
        target_scale = self._get_target_scale(quality_score)
        if target_scale < state.current_scale:
            # Need to reduce
            reduce_to = target_scale
            decision.action = "REDUCE"
            decision.reduce_fraction = reduce_to
            decision.reason = (
                f"scale_down: quality={quality_score:.1f} "
                f"→ scale {state.current_scale:.0%} → {reduce_to:.0%}"
            )
            state.current_scale = target_scale
            state.last_scale_action = decision.reason
            self._total_reductions += 1
            logger.info(
                "📉 SCALE DOWN: {} — quality={:.1f} → scale {:.0%}",
                symbol, quality_score, reduce_to,
            )
            return decision

        # ═══════════════════════════════════════════════════════
        # CHECK 5: Quality Trend Warning
        # If quality is declining rapidly, prepare for action
        # ═══════════════════════════════════════════════════════
        if state.quality_trend < -2.0 and quality_score < QUALITY_RESTORE_THRESHOLD:
            decision.action = "REDUCE"
            decision.reduce_fraction = max(0.25, state.current_scale - 0.25)
            decision.reason = (
                f"quality_declining: trend={state.quality_trend:.2f}, "
                f"score={quality_score:.1f} — reducing to {decision.reduce_fraction:.0%}"
            )
            state.current_scale = decision.reduce_fraction
            self._total_reductions += 1
            return decision

        # ── Default: HOLD ──
        decision.action = "HOLD"
        decision.reduce_fraction = state.current_scale
        decision.reason = (
            f"quality={quality_score:.1f} trend={state.quality_trend:.2f} "
            f"conf={confidence:.0f} R={current_r:.2f}"
        )
        return decision

    def get_position_state(self, symbol: str) -> Optional[Dict]:
        """Get monitoring state for a position."""
        state = self._positions.get(symbol)
        if state:
            return state.to_dict()
        return None

    def get_all_states(self) -> Dict[str, Dict]:
        """Get monitoring states for all positions."""
        return {sym: s.to_dict() for sym, s in self._positions.items()}

    def get_status(self) -> Dict:
        """Get complete monitor status."""
        return {
            "active_positions": len(self._positions),
            "total_evaluations": self._total_evaluations,
            "total_reductions": self._total_reductions,
            "total_exits": self._total_exits,
            "positions": self.get_all_states(),
        }

    # ─────────────────────────────────────────────────────────
    # QUALITY SCORING
    # ─────────────────────────────────────────────────────────

    def _score_trade_quality(
        self,
        state: PositionMonitorState,
        signal: Dict[str, Any],
    ) -> Tuple[float, Dict[str, float]]:
        """
        Re-score trade quality from live market data.

        Returns (composite_score, component_scores).
        """
        side = state.side
        is_long = side == "LONG"
        components = {}

        # ── 1. Flow alignment ──
        flow = signal.get("flow", signal.get("flow_signal", 0))
        if isinstance(flow, str):
            flow_score = 1.0 if (is_long and "buy" in flow) or (not is_long and "sell" in flow) else (
                -1.0 if (is_long and "sell" in flow) or (not is_long and "buy" in flow) else 0
            )
        else:
            flow_score = flow if is_long else -flow
        components["flow_alignment"] = max(0, min(100, 50 + flow_score * 50))

        # ── 2. CVD momentum ──
        cvd = signal.get("cvd", signal.get("cvd_bias", 0))
        if isinstance(cvd, str):
            cvd_score = 1.0 if (is_long and "bullish" in cvd) else (
                -1.0 if (is_long and "bearish" in cvd) else 0
            )
        else:
            cvd_score = cvd if is_long else -cvd
        components["cvd_momentum"] = max(0, min(100, 50 + cvd_score * 50))

        # ── 3. OI health ──
        oi = signal.get("oi_change", signal.get("oi_delta", 0))
        if isinstance(oi, str):
            oi_score = 0
        else:
            # For longs: increasing OI = healthy, decreasing = weak
            # For shorts: decreasing OI = healthy
            oi_score = oi if is_long else -oi
        components["oi_health"] = max(0, min(100, 50 + oi_score * 500))

        # ── 4. Volume support ──
        vol = signal.get("volume_ratio", signal.get("vol_ratio", 1.0))
        if isinstance(vol, (int, float)) and vol > 0:
            vol_score = min(2.0, vol)  # Cap at 2x
            components["volume_support"] = max(0, min(100, vol_score * 50))
        else:
            components["volume_support"] = 50

        # ── 5. Price action ──
        price = signal.get("price", signal.get("current_price", 0))
        if price > 0 and state.entry_price > 0:
            if is_long:
                price_change = (price - state.entry_price) / state.entry_price
            else:
                price_change = (state.entry_price - price) / state.entry_price
            components["price_action"] = max(0, min(100, 50 + price_change * 500))
        else:
            components["price_action"] = 50

        # ── 6. Funding alignment ──
        funding = signal.get("funding_rate", signal.get("funding", 0))
        if isinstance(funding, (int, float)):
            # For longs: negative funding = bullish (shorts paying longs)
            # For shorts: positive funding = bearish (longs paying shorts)
            fund_score = -funding if is_long else funding
            components["funding_alignment"] = max(0, min(100, 50 + fund_score * 1000))
        else:
            components["funding_alignment"] = 50

        # ── 7. Institutional agreement ──
        inst = signal.get("institutional_score", signal.get("inst_agreement", 0.5))
        if isinstance(inst, (int, float)):
            components["inst_agreement"] = max(0, min(100, inst * 100))
        else:
            components["inst_agreement"] = 50

        # ── Composite score ──
        composite = sum(
            components.get(k, 50) * w
            for k, w in QUALITY_WEIGHTS.items()
        )

        return composite, components

    def _calculate_trend(self, readings: deque) -> float:
        """Calculate quality trend (slope) from recent readings."""
        if len(readings) < 3:
            return 0.0

        # Simple linear regression on last N readings
        recent = list(readings)[-10:]  # Last 10 readings
        n = len(recent)
        if n < 2:
            return 0.0

        x_vals = list(range(n))
        y_vals = [r.quality_score for r in recent]

        x_mean = sum(x_vals) / n
        y_mean = sum(y_vals) / n

        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(x_vals, y_vals))
        denominator = sum((x - x_mean) ** 2 for x in x_vals)

        if denominator == 0:
            return 0.0

        slope = numerator / denominator
        return slope

    def _get_target_scale(self, quality_score: float) -> float:
        """Get target position scale for a quality score."""
        for threshold, scale in SCALE_LEVELS:
            if quality_score >= threshold:
                return scale
        return 0.0
