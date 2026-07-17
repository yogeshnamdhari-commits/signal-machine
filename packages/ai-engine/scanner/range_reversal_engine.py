"""
Range Reversal Engine — Specialized mode for ranging markets.

When the market is in range (60%+ of symbols), trend-following strategies
collapse. This engine detects MEAN-REVERSAL setups at range boundaries:

LONG Reversal:
  1. Price near range low (support bounce)
  2. Bullish absorption (large sell orders absorbed)
  3. CVD divergence (price down, CVD flat/up)
  4. OI expansion at support (new longs entering)

SHORT Reversal:
  1. Price near range high (resistance rejection)
  2. Bearish absorption (large buy orders absorbed)
  3. CVD divergence (price up, CVD flat/down)
  4. OI expansion at resistance (new shorts entering)

This allows the system to generate signals even during ranging markets,
where the standard trend-following pipeline produces zero signals.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger


@dataclass
class RangeReversalSetup:
    """A detected range reversal setup."""
    symbol: str = ""
    side: str = ""              # "LONG" or "SHORT"
    reversal_type: str = ""     # "support_bounce" or "resistance_rejection"
    # Component scores (0-100)
    range_position_score: float = 0    # How close to range boundary
    absorption_score: float = 0        # Absorption detection
    cvd_divergence_score: float = 0    # CVD divergence at boundary
    oi_expansion_score: float = 0      # OI expansion at boundary
    # Composite
    composite_score: float = 0
    valid_setup: bool = False
    confidence_boost: float = 0        # Additional confidence to add (0-15 pts)
    position_sizing_mult: float = 0.5  # Reduced sizing for range trades
    timestamp: float = 0


class RangeReversalEngine:
    """
    Detects mean-reversal setups in ranging markets.

    Activated when:
    - Market breadth shows >50% ranging symbols
    - Individual symbol is in 'range' or 'compression' regime
    - Price is near a detected support/resistance level

    Provides:
    - Reversal setup detection
    - Confidence boost for valid setups (up to +15 pts)
    - Position sizing adjustment (0.5x for range trades)
    """

    # Range boundary thresholds
    BOUNDARY_PCT = 0.15       # Within 15% of range = near boundary
    MIN_ABSORPTION_TRADES = 5 # Minimum trades to detect absorption
    OI_EXPANSION_PCT = 0.05   # 5% OI increase = expansion

    def __init__(self) -> None:
        self._cache: Dict[str, RangeReversalSetup] = {}
        self._last_update: float = 0
        # Track range stats for adaptive tuning
        self._range_hit_count: int = 0
        self._range_miss_count: int = 0
        self._total_setups_detected: int = 0

    def evaluate_reversal(
        self,
        symbol: str,
        side: str,
        regime_data: Dict,
        orderflow: Dict,
        cvd_data: Dict,
        oi_data: Dict,
        market_data: Dict,
        sweep_analysis: Dict = None,
        absorption_data: Dict = None,
    ) -> Optional[RangeReversalSetup]:
        """
        Evaluate whether a symbol has a valid range reversal setup.

        Returns RangeReversalSetup if valid, None otherwise.
        """
        raw_regime = regime_data.get("regime", "range") if regime_data else "range"
        regime_conf = regime_data.get("confidence", 0.5) if regime_data else 0.5

        # Only activate for ranging/compression regimes
        if raw_regime not in ("range", "compression"):
            return None

        setup = RangeReversalSetup(symbol=symbol, side=side, timestamp=time.time())

        # ── 1. Range Position Score (0-100) ──
        # How close is price to the detected range boundary?
        klines_5m = market_data.get("klines", {}).get("5m", []) if market_data else []
        if len(klines_5m) < 10:
            return None

        closes = [k.get("close", 0) for k in klines_5m[-20:] if k.get("close", 0) > 0]
        if len(closes) < 10:
            return None

        current_price = closes[-1]
        range_high = max(closes)
        range_low = min(closes)
        range_size = range_high - range_low

        if range_size <= 0:
            return None

        # Position within range: 0=bottom, 1=top
        range_position = (current_price - range_low) / range_size

        if side == "LONG":
            # Support bounce: price near bottom of range
            proximity = max(0, 1.0 - range_position)  # 1.0 at bottom, 0.0 at top
            setup.range_position_score = min(100, proximity * 120)  # Amplify near boundary
            setup.reversal_type = "support_bounce"
        else:
            # Resistance rejection: price near top of range
            proximity = max(0, range_position)  # 1.0 at top, 0.0 at bottom
            setup.range_position_score = min(100, proximity * 120)
            setup.reversal_type = "resistance_rejection"

        # ── 2. Absorption Score (0-100) ──
        # Look for large orders being absorbed (passive absorption)
        if absorption_data:
            absorption_events = absorption_data.get("absorption_events", 0)
            absorption_strength = absorption_data.get("absorption_strength", 0)
            setup.absorption_score = min(100, absorption_events * 15 + absorption_strength * 50)
        elif orderflow:
            # Fallback: check trade imbalance near boundary
            buy_trades = orderflow.get("large_buy_trades", 0)
            sell_trades = orderflow.get("large_sell_trades", 0)
            total_large = buy_trades + sell_trades
            if total_large >= self.MIN_ABSORPTION_TRADES:
                if side == "LONG" and sell_trades > buy_trades:
                    # Large sells being absorbed by passive bids
                    setup.absorption_score = min(100, sell_trades * 12)
                elif side == "SHORT" and buy_trades > sell_trades:
                    setup.absorption_score = min(100, buy_trades * 12)

        # ── 3. CVD Divergence Score (0-100) ──
        if cvd_data:
            cd_momentum = cvd_data.get("delta_momentum", 0)
            cd_divergence = cvd_data.get("price_delta_divergence", 0)

            if side == "LONG" and cd_divergence > 0:
                # Price falling but CVD rising = bullish divergence
                setup.cvd_divergence_score = min(100, cd_divergence * 200 + 30)
            elif side == "SHORT" and cd_divergence < 0:
                # Price rising but CVD falling = bearish divergence
                setup.cvd_divergence_score = min(100, abs(cd_divergence) * 200 + 30)
            else:
                # No divergence — still give base score if momentum aligns
                if (side == "LONG" and cd_momentum > 0) or (side == "SHORT" and cd_momentum < 0):
                    setup.cvd_divergence_score = 30

        # ── 4. OI Expansion Score (0-100) ──
        if oi_data:
            oi_change_pct = oi_data.get("change_pct", 0)
            oi_regime = oi_data.get("oi_regime", "neutral_oi")

            if side == "LONG" and oi_change_pct > self.OI_EXPANSION_PCT * 100:
                # OI expanding while near support = new longs entering
                setup.oi_expansion_score = min(100, oi_change_pct * 10 + 40)
            elif side == "SHORT" and oi_change_pct > self.OI_EXPANSION_PCT * 100:
                # OI expanding while near resistance = new shorts entering
                setup.oi_expansion_score = min(100, oi_change_pct * 10 + 40)
            elif oi_regime in ("bullish_oi", "bearish_oi"):
                setup.oi_expansion_score = 35

        # ── COMPOSITE SCORE ──
        # Weighted: Range Position (35%) + Absorption (25%) + CVD (25%) + OI (15%)
        setup.composite_score = (
            setup.range_position_score * 0.35 +
            setup.absorption_score * 0.25 +
            setup.cvd_divergence_score * 0.25 +
            setup.oi_expansion_score * 0.15
        )

        # ── VALIDITY CHECK ──
        # Need at least 2 components scoring > 20
        components_above_threshold = sum(1 for s in [
            setup.range_position_score, setup.absorption_score,
            setup.cvd_divergence_score, setup.oi_expansion_score
        ] if s > 20)

        # Composite must be >= 35 for a valid range reversal
        setup.valid_setup = (
            components_above_threshold >= 2 and
            setup.composite_score >= 35 and
            setup.range_position_score >= 25  # Must be near boundary
        )

        if setup.valid_setup:
            # Confidence boost: 5-15 pts based on composite score
            setup.confidence_boost = min(15, setup.composite_score * 0.3)
            setup.position_sizing_mult = 0.5  # Half size for range trades
            self._total_setups_detected += 1
            self._range_hit_count += 1

            logger.info(
                "🔄 RANGE_REVERSAL: {} {} | Type={} | Composite={:.0f} | Boost=+{:.0f}pts | "
                "RangePos={:.0f} Abs={:.0f} CVD={:.0f} OI={:.0f}",
                side, symbol, setup.reversal_type, setup.composite_score,
                setup.confidence_boost,
                setup.range_position_score, setup.absorption_score,
                setup.cvd_divergence_score, setup.oi_expansion_score,
            )
        else:
            self._range_miss_count += 1

        self._cache[symbol] = setup
        self._last_update = time.time()
        return setup

    def get_stats(self) -> Dict:
        """Get range reversal engine statistics."""
        total = self._range_hit_count + self._range_miss_count
        hit_rate = self._range_hit_count / total * 100 if total > 0 else 0
        return {
            "total_setups": self._total_setups_detected,
            "hit_rate": round(hit_rate, 1),
            "hits": self._range_hit_count,
            "misses": self._range_miss_count,
            "last_update": self._last_update,
        }

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        """Get cached analysis for a symbol."""
        setup = self._cache.get(symbol)
        if not setup:
            return None
        return {
            "valid_setup": setup.valid_setup,
            "reversal_type": setup.reversal_type,
            "composite_score": setup.composite_score,
            "confidence_boost": setup.confidence_boost,
            "range_position_score": setup.range_position_score,
            "absorption_score": setup.absorption_score,
            "cvd_divergence_score": setup.cvd_divergence_score,
            "oi_expansion_score": setup.oi_expansion_score,
        }
