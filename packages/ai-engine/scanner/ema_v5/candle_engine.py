"""
EMA_V5 Candle Engine — Candlestick pattern recognition with detailed diagnostics.
"""
from __future__ import annotations

from typing import Dict, List

from loguru import logger

from .config import ema_v5_config
from .utils import (
    is_bullish_engulfing, is_bearish_engulfing,
    is_hammer, is_shooting_star,
    is_bullish_pin_bar, is_bearish_pin_bar,
)


class CandleEngine:
    """Identifies bullish/bearish candlestick patterns at EMA touch."""

    def evaluate(self, klines: List[Dict], regime: str) -> Dict:
        """Evaluate last 2 candles for pattern formation.

        Returns:
            {
                "pattern_found": bool,
                "pattern": str | None,
                "candle_score": float (0-100),
                "reason": str,
                "diagnostics": dict (body_ratio, wick_ratio, patterns_checked),
            }
        """
        if not klines or len(klines) < 2:
            return {"pattern_found": False, "reason": "insufficient_candles", "diagnostics": {}}

        cfg = ema_v5_config.candle

        # Previous candle (candle 1) and current candle (candle 2)
        c1 = klines[-2]
        c2 = klines[-1]

        o1, h1, l1, cl1 = c1.get("open", 0), c1.get("high", 0), c1.get("low", 0), c1.get("close", 0)
        o2, h2, l2, cl2 = c2.get("open", 0), c2.get("high", 0), c2.get("low", 0), c2.get("close", 0)

        if not all([o1, h1, l1, cl1, o2, h2, l2, cl2]):
            return {"pattern_found": False, "reason": "invalid_ohlcv", "diagnostics": {}}

        # Compute candle metrics for diagnostics
        body2 = abs(cl2 - o2)
        range2 = h2 - l2 if h2 > l2 else 0.0001
        body_ratio = body2 / range2 if range2 > 0 else 0

        lower_wick = min(o2, cl2) - l2
        upper_wick = h2 - max(o2, cl2)
        wick_ratio = lower_wick / body2 if body2 > 0 else 0
        upper_wick_ratio = upper_wick / body2 if body2 > 0 else 0

        diagnostics = {
            "body_ratio": round(body_ratio, 3),
            "wick_ratio": round(wick_ratio, 2),
            "upper_wick_ratio": round(upper_wick_ratio, 2),
            "body_ratio_min": cfg.body_ratio_min,
            "wick_ratio_min": cfg.wick_ratio_min,
            "range": round(range2, 8),
            "body": round(body2, 8),
        }

        if regime == "BUY_MODE":
            # Check bullish engulfing
            if is_bullish_engulfing(o1, cl1, o2, cl2, cfg.body_ratio_min):
                return {"pattern_found": True, "pattern": "bullish_engulfing", "candle_score": 100, "reason": "bullish_engulfing", "diagnostics": diagnostics}

            # Check hammer
            if is_hammer(o2, h2, l2, cl2, cfg.wick_ratio_min):
                if abs(cl2 - o2) > (h2 - l2) * 0.15:
                    return {"pattern_found": True, "pattern": "hammer", "candle_score": 85, "reason": "hammer", "diagnostics": diagnostics}
                diagnostics["hammer_fail"] = "body_too_small"

            # Check bullish pin bar
            if is_bullish_pin_bar(o2, h2, l2, cl2, cfg.wick_ratio_min):
                return {"pattern_found": True, "pattern": "bullish_pin_bar", "candle_score": 90, "reason": "bullish_pin_bar", "diagnostics": diagnostics}

            # Determine specific rejection reason for BUY_MODE
            _reasons = []
            # Check engulfing failure reason
            if cl1 >= o1:
                _reasons.append("c1_not_bearish")
            if cl2 <= o2:
                _reasons.append("c2_not_bullish")
            if body_ratio < cfg.body_ratio_min:
                _reasons.append(f"body_low:{body_ratio:.2f}")
            # Check hammer failure reason
            lower_wick_val = min(o2, cl2) - l2
            if body2 > 0 and lower_wick_val / body2 < cfg.wick_ratio_min:
                _reasons.append(f"wick_low:{wick_ratio:.1f}")
            if upper_wick > body2:
                _reasons.append(f"upper_wick_high:{upper_wick_ratio:.1f}")
            if abs(cl2 - o2) <= (h2 - l2) * 0.15:
                _reasons.append("hammer_body_small")
            _diag_reject = ",".join(_reasons) if _reasons else "no_pattern"
            diagnostics["rejection_reason"] = _diag_reject

        else:  # SELL_MODE
            # Check bearish engulfing
            if is_bearish_engulfing(o1, cl1, o2, cl2, cfg.body_ratio_min):
                return {"pattern_found": True, "pattern": "bearish_engulfing", "candle_score": 100, "reason": "bearish_engulfing", "diagnostics": diagnostics}

            # Check shooting star
            if is_shooting_star(o2, h2, l2, cl2, cfg.wick_ratio_min):
                if abs(cl2 - o2) > (h2 - l2) * 0.15:
                    return {"pattern_found": True, "pattern": "shooting_star", "candle_score": 85, "reason": "shooting_star", "diagnostics": diagnostics}
                diagnostics["shooting_star_fail"] = "body_too_small"

            # Check bearish pin bar
            if is_bearish_pin_bar(o2, h2, l2, cl2, cfg.wick_ratio_min):
                return {"pattern_found": True, "pattern": "bearish_pin_bar", "candle_score": 90, "reason": "bearish_pin_bar", "diagnostics": diagnostics}

            # Determine specific rejection reason for SELL_MODE
            _reasons = []
            if cl1 <= o1:
                _reasons.append("c1_not_bullish")
            if cl2 >= o2:
                _reasons.append("c2_not_bearish")
            if body_ratio < cfg.body_ratio_min:
                _reasons.append(f"body_low:{body_ratio:.2f}")
            upper_wick_val = h2 - max(o2, cl2)
            if body2 > 0 and upper_wick_val / body2 < cfg.wick_ratio_min:
                _reasons.append(f"wick_low:{upper_wick_ratio:.1f}")
            lower_wick_val = min(o2, cl2) - l2
            if lower_wick_val > body2:
                _reasons.append(f"lower_wick_high:{lower_wick_val / body2:.1f}")
            if abs(cl2 - o2) <= (h2 - l2) * 0.15:
                _reasons.append("ss_body_small")
            _diag_reject = ",".join(_reasons) if _reasons else "no_pattern"
            diagnostics["rejection_reason"] = _diag_reject

        return {"pattern_found": False, "reason": "no_pattern", "diagnostics": diagnostics}
