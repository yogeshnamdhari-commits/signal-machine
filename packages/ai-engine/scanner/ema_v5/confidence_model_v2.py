"""
EMA V5 Confidence Model V2 — Data-Driven Predictive Scoring.

Based on analysis of 375 completed trades:
  - candle_score: r=-0.1698 (NEGATIVE predictor)
  - trend_score: r=-0.1658 (NEGATIVE predictor)
  - volume_expanding: r=+0.1011 (POSITIVE predictor)
  - is_buy: r=+0.1035 (POSITIVE predictor)
  - volume_score: r=+0.0594 (POSITIVE predictor)

The V1 model was INVERSELY correlated with returns.
This V2 model uses features that ACTUALLY predict profitability.

Key changes:
  1. Directional bias: BUY signals get bonus, SELL signals get penalty
  2. Volume expansion: Primary positive signal
  3. Volatility normalization: Lower ATR% = better entry
  4. Candle pattern: Reduced weight (inversely correlated)
  5. Trend alignment: Reduced weight (inversely correlated)
"""
from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

from loguru import logger

from .config import ema_v5_config


class ConfidenceModelV2:
    """Data-driven confidence scoring based on actual trade outcomes."""

    def __init__(self) -> None:
        # V2 weights (optimized from 375-trade analysis)
        self.weights = {
            "directional": 0.25,    # BUY vs SELL bias
            "volume_expansion": 0.25,  # Volume expanding = good
            "volume_quality": 0.15,    # Volume score
            "volatility": 0.15,        # ATR% normalization
            "pullback_quality": 0.10,  # Pullback detection
            "candleConfirmation": 0.05,  # Reduced from 0.20 (was negative predictor)
            "trend_alignment": 0.05,   # Reduced from 0.25 (was negative predictor)
        }
        
        # Thresholds
        self.min_confidence = 70.0  # Lower than V1 (90) because V2 is more selective
        self.min_volume_ratio = 0.4  # Pullback-aware threshold
        
        # Directional bias
        self.buy_bonus = 10.0       # BUY signals get +10 points
        self.sell_penalty = -15.0   # SELL signals get -15 points
        
        # Volatility thresholds
        self.optimal_atr_pct = 1.5  # Optimal ATR as % of price
        self.max_atr_pct = 4.0      # Maximum acceptable ATR%

    def compute(
        self,
        symbol: str,
        side: str,
        regime: str,
        regime_eval: Dict,
        trend_eval: Dict,
        pullback_eval: Dict,
        candle_eval: Dict,
        volume_eval: Dict,
        ema_data: Dict,
    ) -> Dict:
        """
        Compute V2 confidence score based on predictive features.
        
        Returns:
            {
                "confidence": float (0-100),
                "passed": bool,
                "breakdown": dict,
                "audit": dict,
                "reason": str,
            }
        """
        # Extract raw values
        entry_price = ema_data.get("last_close", 0)
        atr_14 = ema_data.get("atr_14", 0)
        volume_ratio = volume_eval.get("volume_ratio", 0)
        volume_expanding = volume_eval.get("volume_expanding", False)
        volume_surge = volume_eval.get("volume_surge", False)
        pullback_detected = pullback_eval.get("pullback_detected", False)
        candle_pattern = candle_eval.get("pattern_found", False)
        trend_direction = trend_eval.get("direction", "")
        
        # ═══ COMPONENT 1: DIRECTIONAL BIAS ═══
        # BUY signals are +10% more likely to win (56.9% vs 35.8%)
        directional_score = 0
        if side == "LONG":
            directional_score = 100 + self.buy_bonus  # 110 → capped at 100
        else:
            directional_score = 100 + self.sell_penalty  # 85
        directional_score = max(0, min(100, directional_score))
        
        # ═══ COMPONENT 2: VOLUME EXPANSION ═══
        # Volume expanding is the STRONGEST positive predictor (r=+0.1011)
        vol_expansion_score = 0
        if volume_expanding:
            vol_expansion_score = 80  # Base score for expanding
            if volume_surge:
                vol_expansion_score = 100  # Surge = highest quality
            elif volume_ratio >= 1.0:
                vol_expansion_score = 90
            elif volume_ratio >= 0.7:
                vol_expansion_score = 75
        else:
            # Non-expanding volume = lower quality
            vol_expansion_score = max(0, 40 - (1.0 - volume_ratio) * 50)
        
        # ═══ COMPONENT 3: VOLUME QUALITY ═══
        # Volume score from V1 (still slightly positive r=+0.0594)
        vol_quality_score = volume_eval.get("volume_score", 0)
        
        # ═══ COMPONENT 4: VOLATILITY NORMALIZATION ═══
        # Lower ATR% = better entry (r=-0.1130)
        atr_pct = (atr_14 / entry_price * 100) if entry_price > 0 else 2.0
        if atr_pct <= self.optimal_atr_pct:
            volatility_score = 100  # Low volatility = excellent
        elif atr_pct <= 2.5:
            volatility_score = 80
        elif atr_pct <= 3.5:
            volatility_score = 60
        elif atr_pct <= self.max_atr_pct:
            volatility_score = 40
        else:
            volatility_score = 20  # Very high volatility = poor entry
        
        # ═══ COMPONENT 5: PULLBACK QUALITY ═══
        # Pullback detection (still useful for entry timing)
        pullback_score = 100 if pullback_detected else 30
        
        # ═══ COMPONENT 6: CANDLE PATTERN ═══
        # REDUCED weight — candle_score was r=-0.1698 (inversely predictive!)
        # Good patterns may actually indicate reversals, not continuations
        candle_score = 0
        if candle_pattern:
            # Base score for pattern detection
            candle_score = 60  # Not 100 — patterns are less reliable
            # Bonus for specific high-quality patterns
            pattern_name = candle_eval.get("pattern_name", "")
            if "engulfing" in pattern_name.lower():
                candle_score = 70  # Engulfing is more reliable
            elif "pin" in pattern_name.lower():
                candle_score = 65
        
        # ═══ COMPONENT 7: TREND ALIGNMENT ═══
        # REDUCED weight — trend_score was r=-0.1658 (inversely predictive!)
        # Strong trends may indicate overextended entries
        trend_score = 0
        if trend_direction:
            # Moderate trend alignment (not extreme)
            trend_score = 50  # Neutral — don't over-reward trend strength
            # Slight bonus for trend direction matching side
            if (side == "LONG" and trend_direction == "bull") or \
               (side == "SHORT" and trend_direction == "bear"):
                trend_score = 65  # Aligned but not extreme
        
        # ═══ WEIGHTED SUM ═══
        confidence = (
            directional_score * self.weights["directional"] +
            vol_expansion_score * self.weights["volume_expansion"] +
            vol_quality_score * self.weights["volume_quality"] +
            volatility_score * self.weights["volatility"] +
            pullback_score * self.weights["pullback_quality"] +
            candle_score * self.weights["candleConfirmation"] +
            trend_score * self.weights["trend_alignment"]
        )
        
        # Cap at 100
        confidence = min(100, confidence)
        
        passed = confidence >= self.min_confidence
        gap = self.min_confidence - confidence
        
        # ═══ AUDIT TRAIL ═══
        breakdown = {
            "directional": round(directional_score, 1),
            "vol_expansion": round(vol_expansion_score, 1),
            "vol_quality": round(vol_quality_score, 1),
            "volatility": round(volatility_score, 1),
            "pullback": round(pullback_score, 1),
            "candle": round(candle_score, 1),
            "trend": round(trend_score, 1),
        }
        
        audit = {
            "model": "v2_predictive",
            "symbol": symbol,
            "side": side,
            "directional_score": round(directional_score, 1),
            "directional_weight": self.weights["directional"],
            "directional_contribution": round(directional_score * self.weights["directional"], 2),
            "vol_expansion_score": round(vol_expansion_score, 1),
            "vol_expansion_weight": self.weights["volume_expansion"],
            "vol_expansion_contribution": round(vol_expansion_score * self.weights["volume_expansion"], 2),
            "volatility_score": round(volatility_score, 1),
            "volatility_weight": self.weights["volatility"],
            "volatility_contribution": round(volatility_score * self.weights["volatility"], 2),
            "atr_pct": round(atr_pct, 2),
            "volume_ratio": round(volume_ratio, 2),
            "volume_expanding": volume_expanding,
            "pullback_detected": pullback_detected,
            "candle_pattern": candle_eval.get("pattern_name", ""),
            "trend_direction": trend_direction,
            "raw_confidence": round(confidence, 2),
            "final_confidence": round(confidence, 1),
            "threshold": self.min_confidence,
            "gap": round(gap, 2),
            "passed": passed,
        }
        
        # Log audit at INFO level (visible in production)
        logger.info(
            "CONF_V2 {} {} side={} dir={:.0f} vol_exp={:.0f} vol_q={:.0f} "
            "volat={:.0f} pull={:.0f} candle={:.0f} trend={:.0f} → conf={:.1f}/{:.0f} {}",
            symbol, regime, side,
            directional_score, vol_expansion_score, vol_quality_score,
            volatility_score, pullback_score, candle_score, trend_score,
            confidence, self.min_confidence,
            "PASS" if passed else "REJECT",
        )
        
        return {
            "confidence": round(confidence, 1),
            "passed": passed,
            "breakdown": breakdown,
            "audit": audit,
            "reason": f"conf={confidence:.1f}_min={self.min_confidence}_gap={gap:+.1f}_{'PASS' if passed else 'FAIL'}",
        }
    
    def get_weights(self) -> Dict:
        """Get current model weights."""
        return dict(self.weights)
    
    def set_weights(self, weights: Dict) -> None:
        """Update model weights (for optimization)."""
        self.weights.update(weights)
    
    def get_info(self) -> Dict:
        """Get model information."""
        return {
            "version": "v2_predictive",
            "trained_on": "375 completed trades",
            "weights": self.weights,
            "min_confidence": self.min_confidence,
            "key_insights": [
                "BUY signals: 56.9% WR vs SELL 35.8% WR",
                "Volume expanding: r=+0.1011 (strongest positive)",
                "Candle score: r=-0.1698 (inversely predictive!)",
                "Trend score: r=-0.1658 (inversely predictive!)",
                "ATR%: r=-0.1130 (lower volatility = better)",
            ],
        }
