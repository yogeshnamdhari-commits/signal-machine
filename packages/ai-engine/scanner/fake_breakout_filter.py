"""
Fake Breakout Filter — detects and filters false breakouts.
Uses volume, re-entry, time-based, and structure analysis.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from loguru import logger


@dataclass
class BreakoutSignal:
    symbol: str
    direction: str          # "LONG" or "SHORT"
    breakout_price: float
    is_fake: bool
    fake_score: float       # 0-1 (higher = more likely fake)
    confidence: float       # 0-1 (filter confidence)
    reasons: List[str]
    timestamp: float


@dataclass
class BreakoutState:
    symbol: str
    signals: List[BreakoutSignal] = field(default_factory=list)
    price_history: List[float] = field(default_factory=list)
    volume_history: List[float] = field(default_factory=list)
    high_history: List[float] = field(default_factory=list)
    low_history: List[float] = field(default_factory=list)
    filter_count: int = 0
    pass_count: int = 0


class FakeBreakoutFilter:
    """
    Multi-layer fake breakout detection:
    - Volume confirmation (real breakouts need volume)
    - Re-entry check (fake: price returns quickly)
    - Time filter (fake: breakout fails within N candles)
    - Range analysis (fake: breaks then reverses)
    - Body-to-wick ratio (fake: long wick rejection)
    - Momentum alignment
    """

    def __init__(self) -> None:
        self._states: Dict[str, BreakoutState] = {}
        self._volume_threshold = 1.3   # 1.3x average volume
        self._reentry_window = 5       # candles
        self._wick_ratio_threshold = 2.5  # wick > 2.5x body = fake

    async def initialize(self) -> None:
        logger.info("FakeBreakoutFilter ready")

    def update_kline(self, symbol: str, kline: Dict) -> None:
        st = self._states.setdefault(symbol, BreakoutState(symbol=symbol))
        st.price_history.append(kline["close"])
        st.volume_history.append(kline.get("volume", 0))
        st.high_history.append(kline["high"])
        st.low_history.append(kline["low"])
        for arr in (st.price_history, st.volume_history, st.high_history, st.low_history):
            if len(arr) > 200:
                del arr[:len(arr) - 200]

    async def filter_breakout(self, symbol: str, direction: str, breakout_price: float) -> BreakoutSignal:
        st = self._states.setdefault(symbol, BreakoutState(symbol=symbol))
        reasons: List[str] = []
        fake_score = 0.0

        prices = np.array(st.price_history) if st.price_history else np.array([breakout_price])
        volumes = np.array(st.volume_history) if st.volume_history else np.array([1.0])
        highs = np.array(st.high_history) if st.high_history else np.array([breakout_price])
        lows = np.array(st.low_history) if st.low_history else np.array([breakout_price])

        # 1. Volume check — low volume = likely fake
        if len(volumes) >= 20:
            avg_vol = np.mean(volumes[-20:])
            recent_vol = volumes[-1] if len(volumes) > 0 else 0
            if avg_vol > 0 and recent_vol / avg_vol < self._volume_threshold:
                fake_score += 0.3
                reasons.append(f"Low volume ({recent_vol/avg_vol:.2f}x avg)")

        # 2. Re-entry check — price returns to range quickly
        if len(prices) >= self._reentry_window:
            recent_prices = prices[-self._reentry_window:]
            if direction == "LONG":
                # Breakout above should stay above
                if min(recent_prices) < breakout_price * 0.998:
                    fake_score += 0.25
                    reasons.append("Price re-entered range quickly")
            else:
                if max(recent_prices) > breakout_price * 1.002:
                    fake_score += 0.25
                    reasons.append("Price re-entered range quickly")

        # 3. Wick rejection — long wick on breakout candle
        if len(highs) >= 2 and len(lows) >= 2 and len(prices) >= 2:
            last_high = highs[-1]
            last_low = lows[-1]
            last_close = prices[-1]
            last_open = prices[-2] if len(prices) >= 2 else last_close

            body = abs(last_close - last_open)
            if direction == "LONG":
                upper_wick = last_high - max(last_close, last_open)
                if body > 0 and upper_wick / body > self._wick_ratio_threshold:
                    fake_score += 0.25
                    reasons.append(f"Upper wick rejection ({upper_wick/body:.1f}x body)")
            else:
                lower_wick = min(last_close, last_open) - last_low
                if body > 0 and lower_wick / body > self._wick_ratio_threshold:
                    fake_score += 0.25
                    reasons.append(f"Lower wick rejection ({lower_wick/body:.1f}x body)")

        # 4. Range analysis — breakout from tight range without follow-through
        if len(highs) >= 20 and len(lows) >= 20:
            range_20 = np.mean(highs[-20:]) - np.mean(lows[-20:])
            mid = (np.mean(highs[-20:]) + np.mean(lows[-20:])) / 2
            if mid > 0 and range_20 / mid < 0.01:  # Very tight range
                fake_score += 0.1
                reasons.append("Breakout from tight range (low conviction)")

        # 5. Momentum — if momentum fading
        if len(prices) >= 10:
            momentum = (prices[-1] - prices[-10]) / prices[-10] if prices[-10] != 0 else 0
            if direction == "LONG" and momentum < 0:
                fake_score += 0.1
                reasons.append("Momentum fading against direction")
            elif direction == "SHORT" and momentum > 0:
                fake_score += 0.1
                reasons.append("Momentum fading against direction")

        fake_score = min(fake_score, 1.0)
        is_fake = fake_score > 0.5
        confidence = 1 - fake_score  # High fake_score = low confidence in breakout

        result = BreakoutSignal(
            symbol=symbol, direction=direction, breakout_price=breakout_price,
            is_fake=is_fake, fake_score=fake_score, confidence=confidence,
            reasons=reasons, timestamp=time.time(),
        )
        st.signals.append(result)
        if len(st.signals) > 200:
            st.signals = st.signals[-100:]
        if is_fake:
            st.filter_count += 1
        else:
            st.pass_count += 1
        return result

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        st = self._states.get(symbol)
        if not st or not st.signals:
            return None
        recent = st.signals[-20:]
        fakes = sum(1 for s in recent if s.is_fake)
        total = st.filter_count + st.pass_count
        return {
            "symbol": symbol,
            "total_breakouts": total,
            "filtered_fakes": st.filter_count,
            "pass_rate": st.pass_count / total if total > 0 else 0,
            "recent_fake_rate": fakes / len(recent) if recent else 0,
        }
