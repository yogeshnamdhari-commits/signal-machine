"""
EMA_V5 Regime Engine — EMA-based regime classification.
Determines if market is in BUY_MODE, SELL_MODE, or NO_TREND.
Now includes ranging market detection to reject flat/low-volatility conditions.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from loguru import logger

from .config import ema_v5_config
from .utils import ema_chain_aligned


class RegimeEngine:
    """Classifies market regime using EMA chain alignment + ranging detection."""

    def __init__(self):
        # Ranging market thresholds
        self._min_atr_pct = 0.3  # Minimum ATR as % of price (reject if below)
        self._min_ema_spread = 0.5  # Minimum total EMA spread as % (reject if below)

    def _is_ranging(self, ema_data: Dict) -> Tuple[bool, str]:
        """Detect ranging/flat markets that should be rejected."""
        entry = ema_data.get("last_close", 0)
        atr = ema_data.get("atr_14", 0)
        e20 = ema_data.get("ema20", 0)
        e50 = ema_data.get("ema50", 0)
        e144 = ema_data.get("ema144", 0)
        e200 = ema_data.get("ema200", 0)
        
        if not entry or not all([e20, e50, e144, e200]):
            return False, ""
        
        # Check 1: ATR too low (low volatility = ranging)
        atr_pct = atr / entry * 100 if atr and entry else 0
        if atr_pct < self._min_atr_pct:
            return True, f"low_volatility_atr={atr_pct:.2f}%"
        
        # Check 2: EMA spread too tight (compression = ranging)
        total_spread = (abs(e20 - e50) + abs(e50 - e144) + abs(e144 - e200)) / entry * 100
        if total_spread < self._min_ema_spread:
            return True, f"ema_compression_spread={total_spread:.2f}%"
        
        return False, ""

    def evaluate(self, ema_data: Dict, current_regime: str = "unknown") -> Dict:
        """Evaluate regime from EMA data.

        Returns:
            {
                "regime": "BUY_MODE" | "SELL_MODE" | "NO_TREND",
                "ema_chain_aligned": bool,
                "ema144_slope_ok": bool,
                "ema200_slope_ok": bool,
                "price_above_144_200": bool (for BUY),
                "price_below_144_200": bool (for SELL),
                "reason": str,
            }
        """
        cfg = ema_v5_config.ema
        trend_cfg = ema_v5_config.trend

        ema20 = ema_data.get("ema20", 0)
        ema50 = ema_data.get("ema50", 0)
        ema144 = ema_data.get("ema144", 0)
        ema200 = ema_data.get("ema200", 0)
        ema144_slope = ema_data.get("ema144_slope", 0)
        ema200_slope = ema_data.get("ema200_slope", 0)
        last_close = ema_data.get("last_close", 0)

        if not all([ema20, ema50, ema144, ema200, last_close]):
            return {"regime": "NO_TREND", "reason": "insufficient_ema_data"}

        # ── Ranging market detection ──
        # Reject flat/low-volatility markets before checking trend
        is_ranging, ranging_reason = self._is_ranging(ema_data)
        if is_ranging:
            return {
                "regime": "NO_TREND",
                "ema_chain_aligned": False,
                "ema144_slope_ok": False,
                "ema200_slope_ok": False,
                "price_above_144_200": False,
                "price_below_144_200": False,
                "reason": f"ranging_market: {ranging_reason}",
            }

        # ── BUY MODE checks ──
        buy_chain = ema_chain_aligned(ema20, ema50, ema144, ema200, "BUY")
        buy_slope_144 = ema144_slope > trend_cfg.slope_threshold
        buy_slope_200 = ema200_slope > trend_cfg.slope_threshold
        buy_price = last_close > ema144 and last_close > ema200

        if buy_chain and buy_slope_144 and buy_slope_200 and buy_price:
            return {
                "regime": "BUY_MODE",
                "ema_chain_aligned": True,
                "ema144_slope_ok": True,
                "ema200_slope_ok": True,
                "price_above_144_200": True,
                "price_below_144_200": False,
                "reason": "bullish_ema_chain",
            }

        # ── SELL MODE checks ──
        sell_chain = ema_chain_aligned(ema20, ema50, ema144, ema200, "SELL")
        sell_slope_144 = ema144_slope < -trend_cfg.slope_threshold
        sell_slope_200 = ema200_slope < -trend_cfg.slope_threshold
        sell_price = last_close < ema144 and last_close < ema200

        if sell_chain and sell_slope_144 and sell_slope_200 and sell_price:
            return {
                "regime": "SELL_MODE",
                "ema_chain_aligned": True,
                "ema144_slope_ok": True,
                "ema200_slope_ok": True,
                "price_above_144_200": False,
                "price_below_144_200": True,
                "reason": "bearish_ema_chain",
            }

        # ── NO TREND ──
        reasons = []
        if not buy_chain and not sell_chain:
            reasons.append("ema_chain_not_aligned")
        if not buy_slope_144 and not sell_slope_144:
            reasons.append("ema144_slope_flat")
        if not buy_slope_200 and not sell_slope_200:
            reasons.append("ema200_slope_flat")

        return {
            "regime": "NO_TREND",
            "ema_chain_aligned": False,
            "ema144_slope_ok": buy_slope_144 or sell_slope_144,
            "ema200_slope_ok": buy_slope_200 or sell_slope_200,
            "price_above_144_200": last_close > ema144 and last_close > ema200,
            "price_below_144_200": last_close < ema144 and last_close < ema200,
            "reason": " | ".join(reasons) if reasons else "no_trend",
        }
