"""
EMA_V5 Trend Maturity Engine — Scored filter for trend extension.

Detects when a trend is mature/exhausted vs fresh/healthy.
Returns a score 0-100 where:
  100 = fresh trend (ideal entry)
   80 = healthy trend (good entry)
   45 = mature trend (caution)
    0 = exhausted trend (avoid)

Components:
  1. EMA200 distance (normalized by ATR) — how far price has moved
  2. Slope degradation — is momentum fading
  3. Consecutive candles — how long since last pullback

Integration:
  - Inserted after Trend Analysis in the pipeline
  - Score fed into Confidence Engine as weighted component
  - Does NOT hard-reject — lets confidence scoring decide
"""
from __future__ import annotations

from typing import Dict, List

from loguru import logger

from .config import ema_v5_config


class TrendMaturityEngine:
    """Scores trend maturity from 0 (exhausted) to 100 (fresh)."""

    def evaluate(self, ema_data: Dict, klines: List[Dict], regime: str) -> Dict:
        """Evaluate trend maturity.

        Args:
            ema_data: Computed EMA values and slopes
            klines: Raw candle data (last N candles)
            regime: Current regime (BUY_MODE or SELL_MODE)

        Returns:
            {
                "maturity_score": float (0-100),
                "distance_score": float (0-100),
                "slope_score": float (0-100),
                "consecutive_score": float (0-100),
                "distance_atr": float,
                "slope_ratio": float,
                "consecutive_bars": int,
                "classification": str,
                "reason": str,
            }
        """
        if regime not in ("BUY_MODE", "SELL_MODE"):
            return {
                "maturity_score": 50,
                "distance_score": 50,
                "slope_score": 50,
                "consecutive_score": 50,
                "distance_atr": 0,
                "slope_ratio": 1.0,
                "consecutive_bars": 0,
                "classification": "NO_TREND",
                "reason": "no_trend_regime",
            }

        cfg = ema_v5_config.trend_maturity

        # ── Component 1: EMA200 Distance ──
        distance_result = self._score_distance(ema_data)

        # ── Component 2: Slope Degradation ──
        slope_result = self._score_slope(ema_data)

        # ── Component 3: Consecutive Candles ──
        consecutive_result = self._score_consecutive(klines, regime)

        # ── Weighted combination ──
        maturity_score = (
            distance_result["score"] * cfg.distance_weight +
            slope_result["score"] * cfg.slope_weight +
            consecutive_result["score"] * cfg.consecutive_weight
        )
        maturity_score = max(0, min(100, maturity_score))

        # ── Classification ──
        if maturity_score >= 80:
            classification = "FRESH"
        elif maturity_score >= 60:
            classification = "HEALTHY"
        elif maturity_score >= 40:
            classification = "MATURE"
        else:
            classification = "EXHAUSTED"

        return {
            "maturity_score": round(maturity_score, 1),
            "distance_score": round(distance_result["score"], 1),
            "slope_score": round(slope_result["score"], 1),
            "consecutive_score": round(consecutive_result["score"], 1),
            "distance_atr": round(distance_result["distance_atr"], 2),
            "slope_ratio": round(slope_result["slope_ratio"], 3),
            "consecutive_bars": consecutive_result["consecutive_bars"],
            "classification": classification,
            "reason": (
                f"dist={distance_result['distance_atr']:.1f}atr "
                f"slope={slope_result['slope_ratio']:.2f} "
                f"bars={consecutive_result['consecutive_bars']} "
                f"→ {classification}"
            ),
        }

    def _score_distance(self, ema_data: Dict) -> Dict:
        """Score based on price distance from EMA200 (normalized by ATR).

        Fresh trend: price close to EMA200 (low distance)
        Exhausted trend: price far from EMA200 (high distance)
        """
        cfg = ema_v5_config.trend_maturity
        price = ema_data.get("last_close", 0)
        ema200 = ema_data.get("ema200", 0)
        atr = ema_data.get("atr_14", 0)

        if not all([price, ema200, atr]) or atr <= 0:
            return {"score": 50, "distance_atr": 0, "reason": "insufficient_data"}

        distance_atr = abs(price - ema200) / atr

        # Score: 100 at distance=0, linear decay to 0 at max_distance
        # Example: max=3.0 → at 1.5atr score=50, at 3.0atr score=0
        score = max(0, 100 * (1 - distance_atr / cfg.max_ema200_distance_atr))

        return {
            "score": score,
            "distance_atr": distance_atr,
            "reason": f"{distance_atr:.1f}atr_from_ema200",
        }

    def _score_slope(self, ema_data: Dict) -> Dict:
        """Score based on EMA20 slope degradation.

        Fresh trend: slope strong or increasing
        Exhausted trend: slope degrading (current << previous)
        """
        cfg = ema_v5_config.trend_maturity
        ema20_slope = ema_data.get("ema20_slope", 0)
        ema20_slope_prev = ema_data.get("ema20_slope_prev", ema20_slope)

        if ema20_slope_prev == 0:
            return {"score": 50, "slope_ratio": 1.0, "reason": "no_previous_slope"}

        # Slope ratio: current / previous
        # Positive ratio = slope maintained or increasing
        # Negative ratio = slope reversed
        # Ratio < 1.0 = slope degrading
        slope_ratio = ema20_slope / ema20_slope_prev

        # Score based on slope ratio
        # ratio >= 1.0 → score 100 (slope maintained/increasing)
        # ratio = 0.5 → score 50 (slope degrading by 50%)
        # ratio <= 0.0 → score 0 (slope reversed or flat)
        if slope_ratio >= 1.0:
            score = 100
        elif slope_ratio >= 0:
            # Linear decay from 100 to 50 as ratio goes from 1.0 to 0
            score = 50 + 50 * slope_ratio
        else:
            # Negative ratio (slope reversed) → score 0-50
            score = max(0, 50 + 50 * slope_ratio)

        return {
            "score": score,
            "slope_ratio": slope_ratio,
            "reason": f"slope_ratio={slope_ratio:.2f}",
        }

    def _score_consecutive(self, klines: List[Dict], regime: str) -> Dict:
        """Score based on consecutive candles without pullback.

        Fresh trend: few consecutive candles (recent pullback)
        Exhausted trend: many consecutive candles (extended move)
        """
        cfg = ema_v5_config.trend_maturity
        consecutive = self._count_consecutive(klines, regime)

        # Score: 100 at 0 consecutive, linear decay to 0 at max
        # Example: max=15 → at 7 bars score=53, at 15 bars score=0
        score = max(0, 100 * (1 - consecutive / cfg.max_consecutive_candles))

        return {
            "score": score,
            "consecutive_bars": consecutive,
            "reason": f"{consecutive}_consecutive_bars",
        }

    def _count_consecutive(self, klines: List[Dict], regime: str) -> int:
        """Count consecutive candles in trend direction without pullback.

        A pullback is defined as a candle that closes against the trend direction.
        """
        if not klines or len(klines) < 2:
            return 0

        count = 0
        # Walk backwards from most recent candle
        for i in range(len(klines) - 1, -1, -1):
            candle = klines[i]
            close = candle.get("close", 0)
            open_price = candle.get("open", 0)

            if not close or not open_price:
                break

            if regime == "BUY_MODE":
                # Bullish candle (close > open) continues trend
                if close > open_price:
                    count += 1
                else:
                    break  # Bearish candle = pullback
            else:  # SELL_MODE
                # Bearish candle (close < open) continues trend
                if close < open_price:
                    count += 1
                else:
                    break  # Bullish candle = pullback

        return count
