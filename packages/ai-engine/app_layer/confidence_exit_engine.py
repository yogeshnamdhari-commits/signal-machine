"""
Confidence-Scored Exit Engine — Smooth 0-100 trend confidence instead of discrete states.

Per Executive Assessment v6:
    "Instead of one 'trend health' state, create a confidence score.
     Trend Health Exit Behavior
     90–100 Hold, no tightening
     75–89 Light tightening
     60–74 Moderate tightening
     <60 Aggressive protection
     <40 Exit

     This prevents abrupt transitions between states."

Key Innovation:
    v10 used discrete states: ENTRY → EXPANSION → TRENDING → WEAKENING → EXIT
    v11 uses continuous 0-100 confidence score:
        - 90-100: Hold, no tightening — trend is very strong
        - 75-89: Light tightening — trend is healthy
        - 60-74: Moderate tightening — trend is showing weakness
        - 40-59: Aggressive protection — trend is deteriorating
        - <40: Exit — trend structure broken

    This prevents abrupt transitions and allows smoother trade management.

Exit Logic:
    1. Calculate confidence score from trend indicators (0-100)
    2. Determine trail distance based on confidence (not price retracement)
    3. Exit when confidence drops below threshold (not when price retraces)
    4. Partial exits at confidence thresholds (not fixed R levels)

READ-ONLY: Returns exit decisions for execution layer.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# CONFIDENCE CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Confidence thresholds
CONFIDENCE_HOLD = 90          # Hold, no tightening
CONFIDENCE_LIGHT_TRAIL = 75   # Light tightening
CONFIDENCE_MODERATE_TRAIL = 60  # Moderate tightening
CONFIDENCE_AGGRESSIVE = 40    # Aggressive protection
CONFIDENCE_EXIT = 30          # Exit — trend broken

# Trail distances by confidence (in R-multiples from peak)
CONFIDENCE_TRAIL_DISTANCES = {
    90: 0.0,    # No trail — hold tight
    75: 0.5,    # Light trail
    60: 0.75,   # Moderate trail
    40: 1.0,    # Aggressive trail
    30: 1.25,   # Very aggressive trail
    0: 1.5,     # Maximum trail
}

# Indicator weights for confidence calculation
INDICATOR_WEIGHTS = {
    "ema_alignment": 0.25,    # Price vs EMA20, EMA20 vs EMA50
    "cvd_slope": 0.20,        # CVD momentum
    "oi_change": 0.15,        # Open interest
    "volume_trend": 0.15,     # Volume conviction
    "atr_expansion": 0.10,    # Volatility support
    "price_momentum": 0.15,   # Price velocity
}

# Partial exit thresholds (by confidence drop from peak)
PARTIAL_EXIT_CONFIDENCE_DROP = 20  # Take partial if confidence drops 20+ from peak


@dataclass
class TrendIndicators:
    """Current trend health indicators."""
    price_vs_ema20: float = 0.0    # Positive = above EMA20
    ema20_vs_ema50: float = 0.0    # Positive = EMA20 above EMA50
    cvd_slope: float = 0.0         # Positive = CVD increasing
    oi_change: float = 0.0         # Positive = OI increasing
    volume_trend: float = 0.0      # Positive = volume expanding
    atr_expansion: float = 0.0     # Positive = ATR expanding
    price_momentum: float = 0.0    # Price velocity (normalized)
    momentum_score: float = 0.0    # -1 to 1, composite momentum

    def to_dict(self) -> Dict:
        return {
            "price_vs_ema20": round(self.price_vs_ema20, 4),
            "ema20_vs_ema50": round(self.ema20_vs_ema50, 4),
            "cvd_slope": round(self.cvd_slope, 4),
            "oi_change": round(self.oi_change, 4),
            "volume_trend": round(self.volume_trend, 4),
            "atr_expansion": round(self.atr_expansion, 4),
            "price_momentum": round(self.price_momentum, 4),
            "momentum_score": round(self.momentum_score, 4),
        }


@dataclass
class ConfidenceExitDecision:
    """Decision from confidence-scored exit engine."""
    symbol: str = ""
    action: str = "HOLD"         # HOLD / MODIFY_SL / TAKE_PARTIAL / EXIT
    reason: str = ""
    new_sl: float = 0.0
    exit_quantity: float = 0.0
    exit_reason: str = ""
    urgency: str = "normal"      # normal / high / critical

    # Confidence info
    confidence_score: float = 0.0   # 0-100 trend confidence
    confidence_zone: str = ""       # HOLD / LIGHT_TRAIL / MODERATE / AGGRESSIVE / EXIT
    confidence_change: float = 0.0  # Change from previous evaluation
    peak_confidence: float = 0.0    # Highest confidence since entry

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "reason": self.reason,
            "new_sl": round(self.new_sl, 6),
            "exit_quantity": round(self.exit_quantity, 6),
            "exit_reason": self.exit_reason,
            "urgency": self.urgency,
            "confidence_score": round(self.confidence_score, 1),
            "confidence_zone": self.confidence_zone,
            "confidence_change": round(self.confidence_change, 1),
            "peak_confidence": round(self.peak_confidence, 1),
        }


@dataclass
class PositionConfidenceState:
    """Tracks confidence state for a single position."""
    symbol: str = ""
    side: str = ""
    entry_price: float = 0.0
    entry_time: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    quantity: float = 0.0
    remaining_quantity: float = 0.0
    original_risk: float = 0.0

    # Confidence tracking
    current_confidence: float = 50.0   # Current confidence score
    peak_confidence: float = 50.0      # Highest confidence since entry
    previous_confidence: float = 50.0  # Last confidence score
    confidence_history: List[float] = field(default_factory=list)  # Last N readings

    # Exit state
    breakeven_moved: bool = False
    partial_taken: bool = False
    partial_exit_qty: float = 0.0


class ConfidenceExitEngine:
    """
    Confidence-scored exit engine with smooth 0-100 trend confidence.

    Per Executive Assessment v6:
        "This prevents abrupt transitions between states."

    This engine:
        1. Calculates continuous 0-100 confidence score from indicators
        2. Adjusts trail distance based on confidence (not price retracement)
        3. Exits when confidence drops below threshold
        4. Takes partials at confidence drop thresholds

    READ-ONLY: Returns exit decisions for execution layer.
    """

    def __init__(self) -> None:
        self._positions: Dict[str, PositionConfidenceState] = {}

    def register_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        quantity: float,
        entry_time: Optional[float] = None,
        initial_confidence: float = 50.0,
    ) -> None:
        """Register a new position for confidence-based exit management."""
        risk = abs(entry_price - stop_loss)
        if risk <= 0:
            risk = entry_price * 0.01

        self._positions[symbol] = PositionConfidenceState(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            entry_time=entry_time or time.time(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            quantity=quantity,
            remaining_quantity=quantity,
            original_risk=risk,
            current_confidence=initial_confidence,
            peak_confidence=initial_confidence,
            previous_confidence=initial_confidence,
        )

    def evaluate(
        self,
        symbol: str,
        current_price: float,
        indicators: Optional[TrendIndicators] = None,
    ) -> ConfidenceExitDecision:
        """
        Evaluate exit conditions using confidence-scored logic.

        Args:
            symbol: Position symbol
            current_price: Current market price
            indicators: Current trend indicators

        Returns:
            ConfidenceExitDecision with action to take
        """
        pos = self._positions.get(symbol)
        if not pos:
            return ConfidenceExitDecision(symbol=symbol, action="HOLD", reason="not registered")

        decision = ConfidenceExitDecision(symbol=symbol)

        # ── Calculate current R-multiple ──
        risk = pos.original_risk
        if risk <= 0:
            return ConfidenceExitDecision(symbol=symbol, action="HOLD", reason="invalid risk")

        if pos.side == "LONG":
            current_r = (current_price - pos.entry_price) / risk
        else:
            current_r = (pos.entry_price - current_price) / risk

        # ── Calculate confidence score ──
        if indicators:
            confidence = self._calculate_confidence(indicators, pos.side)
        else:
            # Use last known confidence if no new indicators
            confidence = pos.current_confidence

        # Update confidence tracking
        pos.previous_confidence = pos.current_confidence
        pos.current_confidence = confidence
        pos.confidence_history.append(confidence)
        if len(pos.confidence_history) > 20:
            pos.confidence_history = pos.confidence_history[-20:]

        if confidence > pos.peak_confidence:
            pos.peak_confidence = confidence

        confidence_change = confidence - pos.previous_confidence

        # ── Check hard stop loss first ──
        if pos.side == "LONG" and current_price <= pos.stop_loss:
            return ConfidenceExitDecision(
                symbol=symbol, action="EXIT", exit_reason="stop_loss",
                exit_quantity=pos.remaining_quantity, urgency="critical",
                confidence_score=confidence, confidence_zone="EXIT",
                peak_confidence=pos.peak_confidence,
            )
        elif pos.side == "SHORT" and current_price >= pos.stop_loss:
            return ConfidenceExitDecision(
                symbol=symbol, action="EXIT", exit_reason="stop_loss",
                exit_quantity=pos.remaining_quantity, urgency="critical",
                confidence_score=confidence, confidence_zone="EXIT",
                peak_confidence=pos.peak_confidence,
            )

        # ── Determine confidence zone ──
        zone = self._get_confidence_zone(confidence)
        decision.confidence_score = confidence
        decision.confidence_zone = zone
        decision.confidence_change = confidence_change
        decision.peak_confidence = pos.peak_confidence

        # ── EXIT zone: confidence < 30 ──
        if confidence < CONFIDENCE_EXIT:
            return ConfidenceExitDecision(
                symbol=symbol, action="EXIT",
                exit_reason=f"confidence_breakdown ({confidence:.1f} < {CONFIDENCE_EXIT})",
                exit_quantity=pos.remaining_quantity, urgency="high",
                confidence_score=confidence, confidence_zone=zone,
                peak_confidence=pos.peak_confidence,
            )

        # ── AGGRESSIVE zone: confidence 40-59 ──
        if confidence < CONFIDENCE_AGGRESSIVE:
            # Aggressive trail
            trail_dist = self._get_trail_distance(confidence)
            trail_sl = self._calc_trail_sl(pos, current_price, trail_dist)

            if pos.side == "LONG" and trail_sl > pos.stop_loss:
                pos.stop_loss = trail_sl
                return ConfidenceExitDecision(
                    symbol=symbol, action="MODIFY_SL", new_sl=trail_sl,
                    reason=f"aggressive_trail: conf={confidence:.1f}, trail={trail_dist}R",
                    confidence_score=confidence, confidence_zone=zone,
                    peak_confidence=pos.peak_confidence,
                )
            elif pos.side == "SHORT" and trail_sl < pos.stop_loss:
                pos.stop_loss = trail_sl
                return ConfidenceExitDecision(
                    symbol=symbol, action="MODIFY_SL", new_sl=trail_sl,
                    reason=f"aggressive_trail: conf={confidence:.1f}, trail={trail_dist}R",
                    confidence_score=confidence, confidence_zone=zone,
                    peak_confidence=pos.peak_confidence,
                )

            # Check if trailing SL hit
            if pos.side == "LONG" and current_price <= pos.stop_loss:
                return ConfidenceExitDecision(
                    symbol=symbol, action="EXIT", exit_reason="aggressive_trail_hit",
                    exit_quantity=pos.remaining_quantity, urgency="high",
                    confidence_score=confidence, confidence_zone=zone,
                )
            elif pos.side == "SHORT" and current_price >= pos.stop_loss:
                return ConfidenceExitDecision(
                    symbol=symbol, action="EXIT", exit_reason="aggressive_trail_hit",
                    exit_quantity=pos.remaining_quantity, urgency="high",
                    confidence_score=confidence, confidence_zone=zone,
                )

        # ── MODERATE zone: confidence 60-74 ──
        if confidence < CONFIDENCE_MODERATE_TRAIL:
            trail_dist = self._get_trail_distance(confidence)
            trail_sl = self._calc_trail_sl(pos, current_price, trail_dist)

            if pos.side == "LONG" and trail_sl > pos.stop_loss:
                pos.stop_loss = trail_sl
                return ConfidenceExitDecision(
                    symbol=symbol, action="MODIFY_SL", new_sl=trail_sl,
                    reason=f"moderate_trail: conf={confidence:.1f}, trail={trail_dist}R",
                    confidence_score=confidence, confidence_zone=zone,
                    peak_confidence=pos.peak_confidence,
                )
            elif pos.side == "SHORT" and trail_sl < pos.stop_loss:
                pos.stop_loss = trail_sl
                return ConfidenceExitDecision(
                    symbol=symbol, action="MODIFY_SL", new_sl=trail_sl,
                    reason=f"moderate_trail: conf={confidence:.1f}, trail={trail_dist}R",
                    confidence_score=confidence, confidence_zone=zone,
                    peak_confidence=pos.peak_confidence,
                )

        # ── LIGHT_TRAIL zone: confidence 75-89 ──
        if confidence < CONFIDENCE_LIGHT_TRAIL:
            trail_dist = self._get_trail_distance(confidence)
            trail_sl = self._calc_trail_sl(pos, current_price, trail_dist)

            if pos.side == "LONG" and trail_sl > pos.stop_loss:
                pos.stop_loss = trail_sl
                return ConfidenceExitDecision(
                    symbol=symbol, action="MODIFY_SL", new_sl=trail_sl,
                    reason=f"light_trail: conf={confidence:.1f}, trail={trail_dist}R",
                    confidence_score=confidence, confidence_zone=zone,
                    peak_confidence=pos.peak_confidence,
                )
            elif pos.side == "SHORT" and trail_sl < pos.stop_loss:
                pos.stop_loss = trail_sl
                return ConfidenceExitDecision(
                    symbol=symbol, action="MODIFY_SL", new_sl=trail_sl,
                    reason=f"light_trail: conf={confidence:.1f}, trail={trail_dist}R",
                    confidence_score=confidence, confidence_zone=zone,
                    peak_confidence=pos.peak_confidence,
                )

        # ── Partial exit on confidence drop ──
        if (not pos.partial_taken
            and pos.peak_confidence - confidence >= PARTIAL_EXIT_CONFIDENCE_DROP
            and current_r > 0.5):
            partial_qty = pos.quantity * 0.4
            pos.partial_taken = True
            pos.remaining_quantity -= partial_qty
            return ConfidenceExitDecision(
                symbol=symbol, action="TAKE_PARTIAL", exit_quantity=partial_qty,
                exit_reason=f"confidence_drop ({pos.peak_confidence:.0f} → {confidence:.0f})",
                confidence_score=confidence, confidence_zone=zone,
                peak_confidence=pos.peak_confidence,
            )

        # ── Breakeven at +1R ──
        if not pos.breakeven_moved and current_r >= 1.0:
            fee_buffer = pos.entry_price * 0.0004
            if pos.side == "LONG":
                new_sl = pos.entry_price + fee_buffer
                if new_sl > pos.stop_loss:
                    pos.stop_loss = new_sl
                    pos.breakeven_moved = True
                    return ConfidenceExitDecision(
                        symbol=symbol, action="MODIFY_SL", new_sl=new_sl,
                        reason=f"breakeven at +1R",
                        confidence_score=confidence, confidence_zone=zone,
                    )
            elif pos.side == "SHORT":
                new_sl = pos.entry_price - fee_buffer
                if new_sl < pos.stop_loss:
                    pos.stop_loss = new_sl
                    pos.breakeven_moved = True
                    return ConfidenceExitDecision(
                        symbol=symbol, action="MODIFY_SL", new_sl=new_sl,
                        reason=f"breakeven at +1R",
                        confidence_score=confidence, confidence_zone=zone,
                    )

        # ── HOLD zone: confidence 90-100 ──
        # No tightening — let the trend run
        decision.action = "HOLD"
        decision.reason = f"confidence={confidence:.1f} zone={zone} R={current_r:.2f}"
        return decision

    def _calculate_confidence(self, indicators: TrendIndicators, side: str) -> float:
        """
        Calculate trend confidence score (0-100) from indicators.

        Uses weighted scoring across multiple dimensions.
        """
        scores = {}

        # ── EMA Alignment (25%) ──
        if side == "LONG":
            ema_score = 0
            if indicators.price_vs_ema20 > 0:
                ema_score += 50
            if indicators.ema20_vs_ema50 > 0:
                ema_score += 50
            # Bonus for strong alignment
            if indicators.price_vs_ema20 > 0.02:
                ema_score = min(100, ema_score + 10)
        else:
            ema_score = 0
            if indicators.price_vs_ema20 < 0:
                ema_score += 50
            if indicators.ema20_vs_ema50 < 0:
                ema_score += 50
            if indicators.price_vs_ema20 < -0.02:
                ema_score = min(100, ema_score + 10)
        scores["ema_alignment"] = ema_score

        # ── CVD Slope (20%) ──
        if side == "LONG":
            cvd_score = max(0, min(100, (indicators.cvd_slope + 0.5) * 100))
        else:
            cvd_score = max(0, min(100, (-indicators.cvd_slope + 0.5) * 100))
        scores["cvd_slope"] = cvd_score

        # ── OI Change (15%) ──
        if side == "LONG":
            oi_score = max(0, min(100, (indicators.oi_change + 0.5) * 100))
        else:
            oi_score = max(0, min(100, (-indicators.oi_change + 0.5) * 100))
        scores["oi_change"] = oi_score

        # ── Volume Trend (15%) ──
        vol_score = max(0, min(100, (indicators.volume_trend + 0.5) * 100))
        scores["volume_trend"] = vol_score

        # ── ATR Expansion (10%) ──
        atr_score = max(0, min(100, (indicators.atr_expansion + 0.5) * 100))
        scores["atr_expansion"] = atr_score

        # ── Price Momentum (15%) ──
        if side == "LONG":
            mom_score = max(0, min(100, (indicators.price_momentum + 1) * 50))
        else:
            mom_score = max(0, min(100, (-indicators.price_momentum + 1) * 50))
        scores["price_momentum"] = mom_score

        # ── Weighted composite ──
        confidence = 0.0
        for component, weight in INDICATOR_WEIGHTS.items():
            confidence += scores.get(component, 50) * weight

        return max(0, min(100, confidence))

    def _get_confidence_zone(self, confidence: float) -> str:
        """Get confidence zone from score."""
        if confidence >= CONFIDENCE_HOLD:
            return "HOLD"
        elif confidence >= CONFIDENCE_LIGHT_TRAIL:
            return "LIGHT_TRAIL"
        elif confidence >= CONFIDENCE_MODERATE_TRAIL:
            return "MODERATE_TRAIL"
        elif confidence >= CONFIDENCE_AGGRESSIVE:
            return "AGGRESSIVE"
        return "EXIT"

    def _get_trail_distance(self, confidence: float) -> float:
        """Get trail distance in R-multiples from confidence score."""
        # Interpolate between thresholds
        sorted_thresholds = sorted(CONFIDENCE_TRAIL_DISTANCES.items(), reverse=True)

        for i in range(len(sorted_thresholds) - 1):
            conf_high, dist_high = sorted_thresholds[i]
            conf_low, dist_low = sorted_thresholds[i + 1]

            if conf_low <= confidence < conf_high:
                # Linear interpolation
                range_conf = conf_high - conf_low
                range_dist = dist_high - dist_low
                if range_conf > 0:
                    progress = (confidence - conf_low) / range_conf
                    return dist_low + progress * range_dist

        # Fallback
        if confidence >= 90:
            return 0.0
        return 1.5

    def _calc_trail_sl(
        self,
        pos: PositionConfidenceState,
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

    def get_confidence(self, symbol: str) -> float:
        """Get current confidence for a symbol."""
        pos = self._positions.get(symbol)
        return pos.current_confidence if pos else 0.0

    def get_all_confidences(self) -> Dict[str, float]:
        """Get all position confidences."""
        return {sym: pos.current_confidence for sym, pos in self._positions.items()}

    def cleanup(self, symbol: str) -> None:
        """Remove tracking for a closed position."""
        self._positions.pop(symbol, None)
