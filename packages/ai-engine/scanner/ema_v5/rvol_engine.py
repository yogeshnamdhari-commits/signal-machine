"""
EMA_V5 RVOL Engine — Relative Volume filter.
Requires RVOL >= threshold for breakouts and momentum trades.
"""
from __future__ import annotations

from typing import Dict, List


class RVOLEngine:
    """Relative Volume gate — requires volume surge for high-quality signals."""

    def __init__(self, min_rvol: float = 1.5, sma_period: int = 20):
        self.min_rvol = min_rvol
        self.sma_period = sma_period

    def evaluate(self, volumes: List[float]) -> Dict:
        if len(volumes) < self.sma_period + 1:
            return {"rvol": 0, "rvol_score": 0, "passed": False, "reason": "insufficient_data"}

        vol_sma = sum(volumes[-self.sma_period - 1:-1]) / self.sma_period
        current_vol = volumes[-1]

        if vol_sma <= 0:
            return {"rvol": 0, "rvol_score": 0, "passed": False, "reason": "zero_avg_volume"}

        rvol = current_vol / vol_sma
        passed = rvol >= self.min_rvol
        score = min(100, (rvol - 1.0) * 50)

        return {
            "rvol": round(rvol, 2),
            "rvol_score": round(score, 1),
            "passed": passed,
            "reason": f"rvol={rvol:.2f}_threshold={self.min_rvol}",
        }
