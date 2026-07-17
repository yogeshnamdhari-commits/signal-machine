"""
EMA_V5 Regime Engine — EMA-based regime classification.
Determines if market is in BUY_MODE, SELL_MODE, or NO_TREND.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from loguru import logger

from .config import ema_v5_config
from .utils import ema_chain_aligned


class RegimeEngine:
    """Classifies market regime using EMA chain alignment."""

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
