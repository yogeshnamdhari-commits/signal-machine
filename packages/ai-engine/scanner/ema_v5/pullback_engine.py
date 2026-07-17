"""
EMA_V5 Pullback Engine — Detects pullback to EMA20 or EMA50.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from loguru import logger

from .config import ema_v5_config
from .utils import price_touches_ema


class PullbackEngine:
    """Detects pullback to key EMA levels."""

    def evaluate(
        self, klines: List[Dict], ema_data: Dict, regime: str,
    ) -> Dict:
        """Evaluate if a pullback is occurring.

        Returns:
            {
                "pullback_detected": bool,
                "touch_level": "ema20" | "ema50" | None,
                "touch_price": float,
                "pullback_depth_pct": float,
                "bounce_confirmed": bool,
                "reason": str,
            }
        """
        if regime not in ("BUY_MODE", "SELL_MODE"):
            return {"pullback_detected": False, "reason": "no_trend"}

        cfg = ema_v5_config.pullback
        ema20 = ema_data.get("ema20", 0)
        ema50 = ema_data.get("ema50", 0)
        last_close = ema_data.get("last_close", 0)
        last_low = ema_data.get("last_low", 0)
        last_high = ema_data.get("last_high", 0)

        if not all([ema20, ema50, last_close]):
            return {"pullback_detected": False, "reason": "missing_data"}

        # Check last 3 candles for EMA touch
        recent = klines[-3:] if len(klines) >= 3 else klines
        touch_level = None
        touch_price = 0

        for candle in reversed(recent):
            low = candle.get("low", 0)
            high = candle.get("high", 0)
            close = candle.get("close", 0)

            if regime == "BUY_MODE":
                # Price pulled back to EMA20 or EMA50 from above
                if low <= ema20 and close >= ema20:
                    if price_touches_ema(low, ema20, cfg.touch_tolerance_pct):
                        touch_level = "ema20"
                        touch_price = low
                        break
                if low <= ema50 and close >= ema50:
                    if price_touches_ema(low, ema50, cfg.touch_tolerance_pct):
                        touch_level = "ema50"
                        touch_price = low
                        break
            else:  # SELL_MODE
                # Price pulled back to EMA20 or EMA50 from below
                if high >= ema20 and close <= ema20:
                    if price_touches_ema(high, ema20, cfg.touch_tolerance_pct):
                        touch_level = "ema20"
                        touch_price = high
                        break
                if high >= ema50 and close <= ema50:
                    if price_touches_ema(high, ema50, cfg.touch_tolerance_pct):
                        touch_level = "ema50"
                        touch_price = high
                        break

        if not touch_level:
            return {"pullback_detected": False, "reason": "no_ema_touch"}

        # Compute pullback depth
        ema_val = ema20 if touch_level == "ema20" else ema50
        if regime == "BUY_MODE":
            depth_pct = (ema_val - last_close) / ema_val * 100 if ema_val else 0
        else:
            depth_pct = (last_close - ema_val) / ema_val * 100 if ema_val else 0

        # Check for bounce (price moved away from EMA after touch)
        bounce = False
        if regime == "BUY_MODE":
            bounce = last_close > ema_val  # closed above EMA after touching
        else:
            bounce = last_close < ema_val

        return {
            "pullback_detected": True,
            "touch_level": touch_level,
            "touch_price": touch_price,
            "pullback_depth_pct": abs(depth_pct),
            "bounce_confirmed": bounce,
            "reason": f"touch_{touch_level}_bounce={'yes' if bounce else 'no'}",
        }
