"""
Liquidity Sweep Engine — Phase 3: Institutional-grade sweep detection.

Requires ALL 4 conditions for a valid signal:

LONG Requirements:
  1. Sell-side liquidity sweep (price wicks below support, closes above)
  2. Bullish MSS (Market Structure Shift — price breaks above recent high)
  3. Bullish FVG (Fair Value Gap — demand imbalance)
  4. Positive Delta (aggressive buying)

SHORT Requirements:
  1. Buy-side liquidity sweep (price wicks above resistance, closes below)
  2. Bearish MSS (price breaks below recent low)
  3. Bearish FVG (supply imbalance)
  4. Negative Delta (aggressive selling)

No sweep = No signal.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger


@dataclass
class SweepSetup:
    """A complete institutional sweep setup with all 4 conditions."""
    symbol: str = ""
    side: str = ""              # "LONG" or "SHORT"
    sweep_type: str = ""        # "sell_side_sweep" or "buy_side_sweep"
    sweep_detected: bool = False
    mss_detected: bool = False
    fvg_detected: bool = False
    delta_confirmed: bool = False
    # Scores for each component (0-100)
    sweep_score: float = 0
    mss_score: float = 0
    fvg_score: float = 0
    delta_score: float = 0
    # Composite
    composite_score: float = 0
    valid_setup: bool = False
    timestamp: float = 0
    conditions_met: int = 0
    conditions_required: int = 2


class LiquiditySweepEngine:
    """
    Phase 3: Comprehensive liquidity sweep detection requiring 4 conditions.
    
    Uses existing SweepDetector for basic sweep detection, plus:
    - MSS (Market Structure Shift) from recent price action
    - FVG from FVGDetect
    - Delta from orderflow
    """

    def __init__(self) -> None:
        self._states: Dict[str, Dict] = {}
        self._min_conditions = 1  # Require at least 1 of 4 conditions (relaxed from 2)
        # Partial scoring: even 0 conditions gives a base score so signals aren't killed

    async def initialize(self) -> None:
        logger.info("Liquidity Sweep Engine ready (4-condition institutional model)")

    def evaluate_setup(
        self,
        symbol: str,
        side: str,
        sweep_analysis: Optional[Dict],
        regime_data: Optional[Dict],
        fvg_analysis: Optional[Dict],
        orderflow: Optional[Dict],
        cvd_data: Optional[Dict],
        market_data: Optional[Dict],
    ) -> Optional[SweepSetup]:
        """
        Evaluate whether a complete institutional sweep setup exists.
        
        Returns SweepSetup with valid_setup=True only if ALL 4 conditions met.
        Returns None if no sweep is detected at all.
        """
        if not sweep_analysis:
            # No sweep data at all — still evaluate other conditions
            sweep_analysis = {"recent_sweep_count": 0, "signal": "neutral", "avg_confidence": 0, "last_sweep_side": ""}

        setup = SweepSetup()
        setup.symbol = symbol
        setup.side = side
        setup.timestamp = time.time()

        # ── Condition 1: Liquidity Sweep ──
        recent_sweeps = sweep_analysis.get("recent_sweep_count", 0)
        last_side = sweep_analysis.get("last_sweep_side", "")
        sweep_signal = sweep_analysis.get("signal", "neutral")
        avg_sweep_conf = sweep_analysis.get("avg_confidence", 0)

        if side == "LONG":
            # Need sell-side liquidity sweep (low sweep = price swept below support)
            setup.sweep_detected = (
                recent_sweeps > 0 and
                (last_side == "low_sweep" or sweep_signal == "bullish_rejection")
            )
            setup.sweep_type = "sell_side_sweep"
        else:
            # Need buy-side liquidity sweep (high sweep = price swept above resistance)
            setup.sweep_detected = (
                recent_sweeps > 0 and
                (last_side == "high_sweep" or sweep_signal == "bearish_rejection")
            )
            setup.sweep_type = "buy_side_sweep"

        if setup.sweep_detected:
            setup.sweep_score = min(
                recent_sweeps / 3 * 40 +  # Count contribution (up to 40)
                avg_sweep_conf * 40 +       # Confidence contribution (up to 40)
                20,                          # Base bonus for detection (20)
                100
            )
        else:
            # No sweep detected — give partial credit for price action proximity
            # Check if price is near recent extremes (soft sweep)
            if market_data:
                price = market_data.get("price", 0)
                low_24h = market_data.get("low_24h", 0)
                high_24h = market_data.get("high_24h", 0)
                if price and low_24h and high_24h:
                    rng = high_24h - low_24h
                    if rng > 0:
                        if side == "LONG" and (price - low_24h) / rng < 0.15:
                            setup.sweep_score = 45  # Near 24h low = soft sell-side sweep
                            setup.sweep_detected = True  # Upgrade to detected
                        elif side == "SHORT" and (high_24h - price) / rng < 0.15:
                            setup.sweep_score = 45  # Near 24h high = soft buy-side sweep
                            setup.sweep_detected = True
                        else:
                            setup.sweep_score = 35  # Neutral
                else:
                    setup.sweep_score = 35
            else:
                setup.sweep_score = 35  # Low score for no data

        # ── Condition 2: Market Structure Shift (MSS) ──
        if regime_data:
            regime_type = regime_data.get("regime", "range")
            regime_conf = regime_data.get("confidence", 0.5)
            alignment = regime_data.get("alignment_score", 0)

            if side == "LONG":
                # Bullish MSS: price broke above recent structure (trending_bull or breakout)
                setup.mss_detected = regime_type in ("trending_bull", "breakout")
                # Also check if alignment is positive (bullish)
                if not setup.mss_detected and alignment > 0.3:
                    setup.mss_detected = True  # Strong bullish alignment = MSS
            else:
                # Bearish MSS: price broke below recent structure
                setup.mss_detected = regime_type in ("trending_bear", "breakout")
                if not setup.mss_detected and alignment < -0.3:
                    setup.mss_detected = True

            if setup.mss_detected:
                # MSS score: regime quality + alignment
                regime_score = {
                    "trending_bull": 85, "trending_bear": 85,
                    "breakout": 75, "range": 30,
                    "compression": 20, "volatile": 50,
                }.get(regime_type, 40)
                setup.mss_score = min(regime_score * regime_conf + abs(alignment) * 15, 100)

        # ── Condition 3: Fair Value Gap ──
        if fvg_analysis:
            has_bull_fvg = fvg_analysis.get("has_bullish_fvg", False)
            has_bear_fvg = fvg_analysis.get("has_bearish_fvg", False)
            fvg_alignment = fvg_analysis.get("fvg_alignment", "neutral")
            fvg_strength = fvg_analysis.get("avg_fvg_strength", 0)

            if side == "LONG":
                setup.fvg_detected = has_bull_fvg and fvg_alignment in ("bullish", "neutral")
            else:
                setup.fvg_detected = has_bear_fvg and fvg_alignment in ("bearish", "neutral")

            if setup.fvg_detected:
                setup.fvg_score = min(
                    fvg_analysis.get("fvg_score", 50) +
                    fvg_strength * 20,  # Strength bonus
                    100
                )

        # ── Condition 4: Delta/CVD Filter (not a trigger — a confirmation) ──
        # FIX 4: Delta and CVD are DIRECTION FILTERS, not entry triggers.
        # They CONFIRM the sweep setup is aligned, but don't add to conditions_met.
        # Strong opposing delta/CVD BLOCKS the setup (reduces score).
        # Neutral or aligned delta/CVD is simply "not blocking" (no score change).
        if orderflow:
            delta = orderflow.get("delta", 0)
            flow_ratio = orderflow.get("flow_ratio", 0.5)

            # Delta aligned = good, but NOT a condition met
            if side == "LONG":
                setup.delta_confirmed = delta > 0 or flow_ratio > 0.50
            else:
                setup.delta_confirmed = delta < 0 or flow_ratio < 0.50

            # Delta OPPOSED = penalty (blocks chase entries at momentum peaks)
            if side == "LONG" and delta < -0.3 and flow_ratio < 0.45:
                setup.delta_score = -20  # Penalty for strong selling during LONG
            elif side == "SHORT" and delta > 0.3 and flow_ratio > 0.55:
                setup.delta_score = -20  # Penalty for strong buying during SHORT
            elif setup.delta_confirmed:
                setup.delta_score = 10  # Small bonus for alignment (not enough to be a trigger)

        # CVD filter: penalize opposing CVD, but don't trigger on aligned CVD
        if cvd_data:
            cvd_momentum = cvd_data.get("delta_momentum", 0)
            if side == "LONG" and cvd_momentum < -0.5:
                setup.delta_score -= 10  # Strong selling CVD penalty
            elif side == "SHORT" and cvd_momentum > 0.5:
                setup.delta_score -= 10  # Strong buying CVD penalty

        # ── Count conditions met ──
        conditions = [
            setup.sweep_detected,
            setup.mss_detected,
            setup.fvg_detected,
            setup.delta_confirmed,
        ]
        setup.conditions_met = sum(conditions)

        # ── Composite score: weighted average with condition bonus ──
        raw_composite = (
            setup.sweep_score * 0.30 +   # Sweep is primary (30%)
            setup.mss_score * 0.25 +      # MSS is structural (25%)
            setup.fvg_score * 0.25 +      # FVG is imbalance (25%)
            setup.delta_score * 0.20       # Delta confirms flow (20%)
        )
        # Condition bonus: +5 per condition met (up to +20)
        condition_bonus = setup.conditions_met * 5
        setup.composite_score = max(0, min(100, raw_composite + condition_bonus))
        
        # Valid if >= 1 condition met (relaxed from 2)
        setup.valid_setup = setup.conditions_met >= self._min_conditions

        # Store state
        self._states[symbol] = {
            "setup": setup,
            "timestamp": time.time(),
        }

        if setup.valid_setup:
            logger.info(
                "🔥 COMPLETE SWAP SETUP: {} {} | sweep={} mss={} fvg={} delta={} | composite={:.1f}",
                side, symbol, setup.sweep_detected, setup.mss_detected,
                setup.fvg_detected, setup.delta_confirmed, setup.composite_score
            )
        else:
            logger.debug(
                "📊 {} {} partial sweep: {}/4 conditions | sweep={} mss={} fvg={} delta={}",
                side, symbol, setup.conditions_met, setup.sweep_detected,
                setup.mss_detected, setup.fvg_detected, setup.delta_confirmed
            )

        return setup

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        """Get the latest sweep setup analysis for a symbol."""
        state = self._states.get(symbol)
        if not state:
            return None

        setup = state.get("setup")
        if not setup:
            return None

        return {
            "symbol": symbol,
            "side": setup.side,
            "sweep_type": setup.sweep_type,
            "valid_setup": setup.valid_setup,
            "conditions_met": setup.conditions_met,
            "conditions_required": setup.conditions_required,
            "sweep_detected": setup.sweep_detected,
            "mss_detected": setup.mss_detected,
            "fvg_detected": setup.fvg_detected,
            "delta_confirmed": setup.delta_confirmed,
            "sweep_score": setup.sweep_score,
            "mss_score": setup.mss_score,
            "fvg_score": setup.fvg_score,
            "delta_score": setup.delta_score,
            "composite_score": setup.composite_score,
            "timestamp": setup.timestamp,
        }
