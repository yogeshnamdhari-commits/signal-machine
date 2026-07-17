"""
EMA_V5 ADX Engine — Trend strength filter using Average Directional Index.
Rejects weak trends. Only trades when ADX >= threshold.
"""
from __future__ import annotations

from typing import Dict, List


def _true_range(highs: List[float], lows: List[float], closes: List[float]) -> List[float]:
    tr = [highs[0] - lows[0]]
    for i in range(1, len(highs)):
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])))
    return tr


def _smoothed(values: List[float], period: int) -> List[float]:
    result = [0.0] * len(values)
    if len(values) < period:
        return result
    result[period - 1] = sum(values[:period]) / period
    for i in range(period, len(values)):
        result[i] = (result[i - 1] * (period - 1) + values[i]) / period
    return result


def compute_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Dict:
    """Compute ADX, +DI, -DI from OHLC data."""
    n = len(highs)
    if n < period + 1:
        return {"adx": 0, "plus_di": 0, "minus_di": 0, "trend_strength": "NO_DATA"}

    tr = _true_range(highs, lows, closes)
    plus_dm = [0.0] * n
    minus_dm = [0.0] * n

    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        if up > down and up > 0:
            plus_dm[i] = up
        if down > up and down > 0:
            minus_dm[i] = down

    smoothed_tr = _smoothed(tr, period)
    smoothed_plus_dm = _smoothed(plus_dm, period)
    smoothed_minus_dm = _smoothed(minus_dm, period)

    plus_di = [0.0] * n
    minus_di = [0.0] * n
    dx = [0.0] * n

    for i in range(period, n):
        if smoothed_tr[i] > 0:
            plus_di[i] = 100.0 * smoothed_plus_dm[i] / smoothed_tr[i]
            minus_di[i] = 100.0 * smoothed_minus_dm[i] / smoothed_tr[i]
        d_sum = plus_di[i] + minus_di[i]
        if d_sum > 0:
            dx[i] = 100.0 * abs(plus_di[i] - minus_di[i]) / d_sum

    adx = _smoothed(dx, period)

    latest_adx = adx[-1] if adx else 0
    latest_plus = plus_di[-1] if plus_di else 0
    latest_minus = minus_di[-1] if minus_di else 0

    if latest_adx >= 25:
        strength = "STRONG"
    elif latest_adx >= 20:
        strength = "MODERATE"
    else:
        strength = "WEAK"

    return {
        "adx": round(latest_adx, 2),
        "plus_di": round(latest_plus, 2),
        "minus_di": round(latest_minus, 2),
        "trend_strength": strength,
    }


class ADXEngine:
    """Trend strength gate — rejects weak trends."""

    def __init__(self, min_adx: float = 25.0):
        self.min_adx = min_adx

    def evaluate(self, highs: List[float], lows: List[float], closes: List[float]) -> Dict:
        adx_data = compute_adx(highs, lows, closes, period=14)
        passed = adx_data["adx"] >= self.min_adx
        score = min(100, adx_data["adx"] / 50 * 100)
        return {
            "adx": adx_data["adx"],
            "plus_di": adx_data["plus_di"],
            "minus_di": adx_data["minus_di"],
            "trend_strength": adx_data["trend_strength"],
            "adx_score": round(score, 1),
            "passed": passed,
        }
