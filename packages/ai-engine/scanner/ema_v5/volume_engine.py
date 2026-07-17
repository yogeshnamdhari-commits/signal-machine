"""
EMA_V5 Volume Engine — Volume confirmation with pullback-aware logic.

Pullback candles naturally have lower volume (healthy market behavior).
The engine checks:
  1. Pullback volume: can be below average (≥ 0.4x SMA20)
  2. Confirmation expansion: current volume should expand vs prior candle
"""
from __future__ import annotations

from typing import Dict

from .config import ema_v5_config


class VolumeEngine:
    """Validates volume confirmation with pullback-aware thresholds."""

    def evaluate(self, ema_data: Dict) -> Dict:
        """Evaluate volume against SMA20 with pullback-aware logic.

        Returns:
            {
                "volume_ok": bool,
                "volume_ratio": float,
                "volume_surge": bool,
                "volume_expanding": bool,
                "volume_score": float (0-100),
                "reason": str,
            }
        """
        cfg = ema_v5_config.volume

        last_volume = ema_data.get("last_volume", 0)
        vol_sma20 = ema_data.get("vol_sma20", 0)
        # Previous candle volume (from klines, approximated)
        prev_volume = ema_data.get("prev_volume", 0)

        if vol_sma20 <= 0 or last_volume <= 0:
            return {"volume_ok": False, "volume_ratio": 0, "volume_surge": False,
                    "volume_expanding": False, "volume_score": 0, "reason": "no_volume_data"}

        ratio = last_volume / vol_sma20

        # Pullback-aware threshold: allow lower volume during pullback
        pullback_threshold = 0.4  # 40% of SMA20 minimum for pullback candle
        ok = ratio >= pullback_threshold

        surge = ratio >= cfg.volume_surge_ratio

        # Volume expansion: current > previous (confirms buying/selling interest)
        expanding = last_volume > prev_volume if prev_volume > 0 else True

        # Score: 0-100 based on volume ratio
        score = min(100, ratio * 50)  # 1.0x = 50, 2.0x = 100

        # Bonus for expansion
        if expanding:
            score = min(100, score + 10)

        reason_parts = [f"ratio={ratio:.2f}"]
        reason_parts.append(f"surge={'yes' if surge else 'no'}")
        reason_parts.append(f"expand={'yes' if expanding else 'no'}")

        return {
            "volume_ok": ok,
            "volume_ratio": round(ratio, 2),
            "volume_surge": surge,
            "volume_expanding": expanding,
            "volume_score": round(score, 1),
            "reason": "_".join(reason_parts),
        }
