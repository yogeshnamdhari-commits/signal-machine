"""
EMA_V5 Pullback Engine — Adaptive ATR-based pullback detection.
Detects pullback to EMA20 or EMA50 using volatility-normalized distance.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from loguru import logger

from .config import ema_v5_config
from .utils import price_touches_ema


class PullbackEngine:
    """Detects pullback to key EMA levels using adaptive ATR-based logic."""

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
                "pullback_depth_atr": float,
                "bounce_confirmed": bool,
                "structure_intact": bool,
                "reason": str,
                "diagnostics": {
                    "ema20_dist_pct": float,
                    "ema50_dist_pct": float,
                    "ema20_dist_atr": float,
                    "ema50_dist_atr": float,
                    "atr_14": float,
                    "atr_threshold": float,
                    "near_ema20": bool,
                    "near_ema50": bool,
                    "structure_intact": bool,
                    "pullback_depth_pct": float,
                    "pullback_depth_atr": float,
                    "last_close": float,
                    "candles_checked": int,
                    "mode": "adaptive" | "legacy",
                },
            }
        """
        cfg = ema_v5_config.pullback
        
        diagnostics = {
            "ema20_dist_pct": 0,
            "ema50_dist_pct": 0,
            "ema20_dist_atr": 0,
            "ema50_dist_atr": 0,
            "atr_14": ema_data.get("atr_14", 0),
            "atr_threshold": cfg.ema_distance_atr_threshold,
            "near_ema20": False,
            "near_ema50": False,
            "structure_intact": True,
            "pullback_depth_pct": 0,
            "pullback_depth_atr": 0,
            "last_close": ema_data.get("last_close", 0),
            "candles_checked": 0,
            "mode": "adaptive" if cfg.atr_normalize_enabled else "legacy",
        }

        if regime not in ("BUY_MODE", "SELL_MODE"):
            return {"pullback_detected": False, "reason": "no_trend", "diagnostics": diagnostics}

        ema20 = ema_data.get("ema20", 0)
        ema50 = ema_data.get("ema50", 0)
        last_close = ema_data.get("last_close", 0)
        atr_14 = ema_data.get("atr_14", 0)

        if not all([ema20, ema50, last_close]):
            return {"pullback_detected": False, "reason": "missing_data", "diagnostics": diagnostics}

        # Check if we should use adaptive mode
        use_adaptive = cfg.atr_normalize_enabled and atr_14 > 0 and not cfg.use_legacy_mode
        diagnostics["mode"] = "adaptive" if use_adaptive else "legacy"

        # Compute distances to EMAs
        if ema20 > 0:
            diagnostics["ema20_dist_pct"] = abs(last_close - ema20) / ema20 * 100
            diagnostics["ema20_dist_atr"] = abs(last_close - ema20) / atr_14 if atr_14 > 0 else 0
        if ema50 > 0:
            diagnostics["ema50_dist_pct"] = abs(last_close - ema50) / ema50 * 100
            diagnostics["ema50_dist_atr"] = abs(last_close - ema50) / atr_14 if atr_14 > 0 else 0

        # Determine if price is "near" EMA based on mode
        if use_adaptive:
            # Adaptive mode: distance / ATR <= threshold
            diagnostics["near_ema20"] = diagnostics["ema20_dist_atr"] <= cfg.ema_distance_atr_threshold
            diagnostics["near_ema50"] = diagnostics["ema50_dist_atr"] <= cfg.ema_distance_atr_threshold
        else:
            # Legacy mode: rigid percentage
            diagnostics["near_ema20"] = diagnostics["ema20_dist_pct"] <= cfg.touch_tolerance_pct
            diagnostics["near_ema50"] = diagnostics["ema50_dist_pct"] <= cfg.touch_tolerance_pct

        # Check last 3 candles for EMA proximity
        recent = klines[-3:] if len(klines) >= 3 else klines
        diagnostics["candles_checked"] = len(recent)
        
        # Find the nearest EMA and compute pullback depth
        touch_level = None
        touch_price = 0
        pullback_depth_pct = 0
        pullback_depth_atr = 0

        for candle in reversed(recent):
            low = candle.get("low", 0)
            high = candle.get("high", 0)
            close = candle.get("close", 0)

            if regime == "BUY_MODE":
                # For BUY: price should pull back toward EMA from above
                # Check if price reached near EMA20
                if diagnostics["near_ema20"] and low <= ema20 * 1.01:  # Within 1% of EMA20
                    touch_level = "ema20"
                    touch_price = low
                    pullback_depth_pct = (ema20 - last_close) / ema20 * 100 if ema20 > 0 else 0
                    pullback_depth_atr = (ema20 - last_close) / atr_14 if atr_14 > 0 else 0
                    break
                # Check if price reached near EMA50
                if diagnostics["near_ema50"] and low <= ema50 * 1.01:  # Within 1% of EMA50
                    touch_level = "ema50"
                    touch_price = low
                    pullback_depth_pct = (ema50 - last_close) / ema50 * 100 if ema50 > 0 else 0
                    pullback_depth_atr = (ema50 - last_close) / atr_14 if atr_14 > 0 else 0
                    break
            else:  # SELL_MODE
                # For SELL: price should pull back toward EMA from below
                # Check if price reached near EMA20
                if diagnostics["near_ema20"] and high >= ema20 * 0.99:  # Within 1% of EMA20
                    touch_level = "ema20"
                    touch_price = high
                    pullback_depth_pct = (last_close - ema20) / ema20 * 100 if ema20 > 0 else 0
                    pullback_depth_atr = (last_close - ema20) / atr_14 if atr_14 > 0 else 0
                    break
                # Check if price reached near EMA50
                if diagnostics["near_ema50"] and high >= ema50 * 0.99:  # Within 1% of EMA50
                    touch_level = "ema50"
                    touch_price = high
                    pullback_depth_pct = (last_close - ema50) / ema50 * 100 if ema50 > 0 else 0
                    pullback_depth_atr = (last_close - ema50) / atr_14 if atr_14 > 0 else 0
                    break

        # If no EMA proximity detected, check if price is "near" based on current distance
        if not touch_level:
            if diagnostics["near_ema20"]:
                touch_level = "ema20"
                touch_price = last_close
                if regime == "BUY_MODE":
                    pullback_depth_pct = (ema20 - last_close) / ema20 * 100 if ema20 > 0 else 0
                    pullback_depth_atr = (ema20 - last_close) / atr_14 if atr_14 > 0 else 0
                else:
                    pullback_depth_pct = (last_close - ema20) / ema20 * 100 if ema20 > 0 else 0
                    pullback_depth_atr = (last_close - ema20) / atr_14 if atr_14 > 0 else 0
            elif diagnostics["near_ema50"]:
                touch_level = "ema50"
                touch_price = last_close
                if regime == "BUY_MODE":
                    pullback_depth_pct = (ema50 - last_close) / ema50 * 100 if ema50 > 0 else 0
                    pullback_depth_atr = (ema50 - last_close) / atr_14 if atr_14 > 0 else 0
                else:
                    pullback_depth_pct = (last_close - ema50) / ema50 * 100 if ema50 > 0 else 0
                    pullback_depth_atr = (last_close - ema50) / atr_14 if atr_14 > 0 else 0

        diagnostics["pullback_depth_pct"] = abs(pullback_depth_pct)
        diagnostics["pullback_depth_atr"] = abs(pullback_depth_atr)

        # If no EMA proximity detected at all
        if not touch_level:
            if use_adaptive:
                if not diagnostics["near_ema20"] and not diagnostics["near_ema50"]:
                    reason = f"not_near_ema: ema20_dist={diagnostics['ema20_dist_atr']:.2f}atr ema50_dist={diagnostics['ema50_dist_atr']:.2f}atr threshold={cfg.ema_distance_atr_threshold}"
                else:
                    reason = "no_ema_proximity"
            else:
                if not diagnostics["near_ema20"] and not diagnostics["near_ema50"]:
                    reason = f"outside_tolerance: ema20_dist={diagnostics['ema20_dist_pct']:.2f}% ema50_dist={diagnostics['ema50_dist_pct']:.2f}% tolerance={cfg.touch_tolerance_pct}%"
                else:
                    reason = "no_ema_touch"
            return {"pullback_detected": False, "reason": reason, "diagnostics": diagnostics}

        # Check pullback depth
        if use_adaptive:
            if pullback_depth_atr > cfg.max_pullback_atr:
                reason = f"pullback_too_deep: depth={pullback_depth_atr:.2f}atr max={cfg.max_pullback_atr}"
                return {"pullback_detected": False, "reason": reason, "diagnostics": diagnostics}
        else:
            if abs(pullback_depth_pct) > cfg.max_pullback_pct:
                reason = f"pullback_too_deep: depth={abs(pullback_depth_pct):.2f}% max={cfg.max_pullback_pct}%"
                return {"pullback_detected": False, "reason": reason, "diagnostics": diagnostics}

        # Check structure integrity
        structure_intact = self._check_structure(klines, regime, cfg)
        diagnostics["structure_intact"] = structure_intact
        
        if cfg.require_structure_intact and not structure_intact:
            reason = "structure_broken"
            return {"pullback_detected": False, "reason": reason, "diagnostics": diagnostics}

        # Check for bounce (price moved away from EMA after touch)
        bounce = False
        ema_val = ema20 if touch_level == "ema20" else ema50
        if regime == "BUY_MODE":
            bounce = last_close > ema_val
        else:
            bounce = last_close < ema_val

        # Build reason string
        if use_adaptive:
            reason = f"near_{touch_level}_bounce={'yes' if bounce else 'no'}_atr={diagnostics['ema20_dist_atr'] if touch_level == 'ema20' else diagnostics['ema50_dist_atr']:.2f}"
        else:
            reason = f"touch_{touch_level}_bounce={'yes' if bounce else 'no'}"

        return {
            "pullback_detected": True,
            "touch_level": touch_level,
            "touch_price": touch_price,
            "pullback_depth_pct": abs(pullback_depth_pct),
            "pullback_depth_atr": abs(pullback_depth_atr),
            "bounce_confirmed": bounce,
            "structure_intact": structure_intact,
            "reason": reason,
            "diagnostics": diagnostics,
        }

    def _check_structure(self, klines: List[Dict], regime: str, cfg) -> bool:
        """Check if trend structure is intact during pullback.
        
        For BUY: No lower low that breaks structure
        For SELL: No higher high that breaks structure
        """
        if not klines or len(klines) < cfg.structure_lookback:
            return True  # Not enough data, assume intact
        
        lookback = klines[-cfg.structure_lookback:]
        
        if regime == "BUY_MODE":
            # Check for lower low that breaks structure
            lows = [c.get("low", 0) for c in lookback]
            closes = [c.get("close", 0) for c in lookback]
            
            # Find the lowest low in the lookback period
            lowest_low = min(lows)
            current_close = closes[-1] if closes else 0
            
            # If current price is significantly below recent lows, structure is broken
            if current_close < lowest_low * (1 - cfg.structure_break_pct / 100):
                return False
                
        else:  # SELL_MODE
            # Check for higher high that breaks structure
            highs = [c.get("high", 0) for c in lookback]
            closes = [c.get("close", 0) for c in lookback]
            
            # Find the highest high in the lookback period
            highest_high = max(highs)
            current_close = closes[-1] if closes else 0
            
            # If current price is significantly above recent highs, structure is broken
            if current_close > highest_high * (1 + cfg.structure_break_pct / 100):
                return False
        
        return True
