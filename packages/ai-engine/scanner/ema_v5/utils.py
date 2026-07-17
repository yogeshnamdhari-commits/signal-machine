"""
EMA_V5 Utilities — Helper functions for calculations.
"""
from __future__ import annotations

from typing import List, Optional, Dict


def ema(values: List[float], period: int) -> List[float]:
    """Compute Exponential Moving Average.

    Returns list of same length as input, with NaN for warmup period.
    """
    if not values or period <= 0:
        return []
    result = [0.0] * len(values)
    k = 2.0 / (period + 1)
    # Seed with SMA for first 'period' values
    if len(values) >= period:
        sma_sum = sum(values[:period])
        result[period - 1] = sma_sum / period
        for i in range(period, len(values)):
            result[i] = values[i] * k + result[i - 1] * (1 - k)
    return result


def ema_last(values: List[float], period: int) -> float:
    """Return the last EMA value (most recent)."""
    result = ema(values, period)
    return result[-1] if result else 0.0


def sma(values: List[float], period: int) -> float:
    """Simple Moving Average of last 'period' values."""
    if not values or len(values) < period:
        return 0.0
    return sum(values[-period:]) / period


def slope(values: List[float], lookback: int = 5) -> float:
    """Compute slope of last 'lookback' values.

    Positive = rising, negative = falling.
    Normalized: slope / avg * 100 (percentage change per bar).
    """
    if len(values) < lookback:
        return 0.0
    recent = values[-lookback:]
    avg = sum(recent) / len(recent)
    if avg == 0:
        return 0.0
    # Simple linear slope: (last - first) / (n-1)
    raw_slope = (recent[-1] - recent[0]) / (len(recent) - 1) if len(recent) > 1 else 0
    return raw_slope / avg * 100


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> float:
    """Average True Range."""
    if len(highs) < period + 1:
        return 0.0
    true_ranges = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)
    if len(true_ranges) < period:
        return 0.0
    return sum(true_ranges[-period:]) / period


def is_bullish_engulfing(
    open1: float, close1: float, open2: float, close2: float,
    body_ratio_min: float = 0.5,
) -> bool:
    """Check if candle 2 is a bullish engulfing of candle 1.

    Candle 1: bearish (close < open)
    Candle 2: bullish (close > open), body engulfs candle 1 body
    """
    if close1 >= open1:  # candle 1 must be bearish
        return False
    if close2 <= open2:  # candle 2 must be bullish
        return False
    body1 = abs(close1 - open1)
    body2 = abs(close2 - open2)
    range2 = max(open2, close2) - min(open2, close2)
    if range2 == 0:
        return False
    if body2 / range2 < body_ratio_min:
        return False
    # Candle 2 body must engulf candle 1 body
    return close2 > open1 and open2 < close1


def is_bearish_engulfing(
    open1: float, close1: float, open2: float, close2: float,
    body_ratio_min: float = 0.5,
) -> bool:
    """Check if candle 2 is a bearish engulfing of candle 1."""
    if close1 <= open1:  # candle 1 must be bullish
        return False
    if close2 >= open2:  # candle 2 must be bearish
        return False
    body1 = abs(close1 - open1)
    body2 = abs(close2 - open2)
    range2 = max(open2, close2) - min(open2, close2)
    if range2 == 0:
        return False
    if body2 / range2 < body_ratio_min:
        return False
    return close2 < open1 and open2 > close1


def is_hammer(
    open_p: float, high: float, low: float, close: float,
    wick_ratio_min: float = 2.0,
) -> bool:
    """Check if candle is a hammer (bullish reversal).

    Small body at top, long lower wick.
    """
    body = abs(close - open_p)
    total_range = high - low
    if total_range == 0:
        return False
    lower_wick = min(open_p, close) - low
    upper_wick = high - max(open_p, close)
    # Lower wick must be >= 2x body
    if body > 0 and lower_wick / body < wick_ratio_min:
        return False
    # Upper wick must be small (< body)
    if upper_wick > body:
        return False
    return True


def is_shooting_star(
    open_p: float, high: float, low: float, close: float,
    wick_ratio_min: float = 2.0,
) -> bool:
    """Check if candle is a shooting star (bearish reversal).

    Small body at bottom, long upper wick.
    """
    body = abs(close - open_p)
    total_range = high - low
    if total_range == 0:
        return False
    upper_wick = high - max(open_p, close)
    lower_wick = min(open_p, close) - low
    if body > 0 and upper_wick / body < wick_ratio_min:
        return False
    if lower_wick > body:
        return False
    return True


def is_bullish_pin_bar(
    open_p: float, high: float, low: float, close: float,
    wick_ratio_min: float = 2.0,
) -> bool:
    """Check if candle is a bullish pin bar (long lower wick rejection)."""
    body = abs(close - open_p)
    total_range = high - low
    if total_range == 0:
        return False
    lower_wick = min(open_p, close) - low
    upper_wick = high - max(open_p, close)
    # Lower wick >= 2x body
    if body > 0 and lower_wick / body < wick_ratio_min:
        return False
    # Upper wick must be small (not dominating)
    if body > 0 and upper_wick > body:
        return False
    return True


def is_bearish_pin_bar(
    open_p: float, high: float, low: float, close: float,
    wick_ratio_min: float = 2.0,
) -> bool:
    """Check if candle is a bearish pin bar (long upper wick rejection)."""
    body = abs(close - open_p)
    total_range = high - low
    if total_range == 0:
        return False
    upper_wick = high - max(open_p, close)
    lower_wick = min(open_p, close) - low
    if body > 0 and upper_wick / body < wick_ratio_min:
        return False
    if body > 0 and lower_wick > body:
        return False
    return True


def price_touches_ema(
    price: float, ema_value: float, tolerance_pct: float = 0.3,
) -> bool:
    """Check if price is within tolerance of an EMA value."""
    if ema_value == 0:
        return False
    dist_pct = abs(price - ema_value) / ema_value * 100
    return dist_pct <= tolerance_pct


def ema_chain_aligned(
    ema20: float, ema50: float, ema144: float, ema200: float,
    side: str,
) -> bool:
    """Check if EMAs are in correct chain order.

    BUY:  EMA20 > EMA50 > EMA144 > EMA200
    SELL: EMA20 < EMA50 < EMA144 < EMA200
    """
    if side == "BUY":
        return ema20 > ema50 > ema144 > ema200
    else:
        return ema20 < ema50 < ema144 < ema200


def compute_rr(entry: float, sl: float, tp: float) -> float:
    """Compute risk:reward ratio."""
    risk = abs(entry - sl)
    if risk == 0:
        return 0.0
    reward = abs(tp - entry)
    return reward / risk
