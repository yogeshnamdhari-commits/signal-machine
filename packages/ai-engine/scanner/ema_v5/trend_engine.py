"""
EMA_V5 Trend Engine — Trend direction and strength analysis.
"""
from __future__ import annotations

from typing import Dict

from .config import ema_v5_config
from .utils import ema_chain_aligned, slope


class TrendEngine:
    """Analyzes trend direction and strength using EMA alignment."""

    def evaluate(self, ema_data: Dict, regime: str) -> Dict:
        """Evaluate trend strength and confirmation.

        Returns:
            {
                "trend": "STRONG_BULL" | "BULL" | "STRONG_BEAR" | "BEAR" | "NEUTRAL",
                "direction": "BUY" | "SELL" | None,
                "ema_chain_perfect": bool,
                "confirmation_bars": int,
                "trend_score": float (0-100),
                "reason": str,
            }
        """
        if regime not in ("BUY_MODE", "SELL_MODE"):
            return {
                "trend": "NEUTRAL",
                "direction": None,
                "ema_chain_perfect": False,
                "confirmation_bars": 0,
                "trend_score": 0,
                "reason": "no_trend_regime",
            }

        side = "BUY" if regime == "BUY_MODE" else "SELL"
        ema20 = ema_data.get("ema20", 0)
        ema50 = ema_data.get("ema50", 0)
        ema144 = ema_data.get("ema144", 0)
        ema200 = ema_data.get("ema200", 0)
        ema20_slope = ema_data.get("ema20_slope", 0)
        ema50_slope = ema_data.get("ema50_slope", 0)
        ema144_slope = ema_data.get("ema144_slope", 0)

        chain_perfect = ema_chain_aligned(ema20, ema50, ema144, ema200, side)

        # Compute trend score (0-100)
        score = 0
        if chain_perfect:
            score += 40  # perfect chain alignment
        if side == "BUY":
            if ema20_slope > 0:
                score += 15
            if ema50_slope > 0:
                score += 15
            if ema144_slope > 0:
                score += 15
        else:
            if ema20_slope < 0:
                score += 15
            if ema50_slope < 0:
                score += 15
            if ema144_slope < 0:
                score += 15
        # Bonus for steep slopes
        if abs(ema20_slope) > 0.1:
            score += 15

        score = min(100, score)

        # Classify trend strength
        if score >= 80:
            trend = "STRONG_BULL" if side == "BUY" else "STRONG_BEAR"
        elif score >= 50:
            trend = "BULL" if side == "BUY" else "BEAR"
        else:
            trend = "NEUTRAL"
            side = None

        return {
            "trend": trend,
            "direction": side,
            "ema_chain_perfect": chain_perfect,
            "confirmation_bars": 0,  # computed from klines if needed
            "trend_score": score,
            "reason": f"score={score:.0f}_chain={'ok' if chain_perfect else 'fail'}",
        }
