"""
Fair Value Gap (FVG) Detector — Institutional-grade imbalance detection.

Identifies price imbalances (fair value gaps) from candlestick data:
  - Bullish FVG: candle[i-1].high < candle[i+1].low → gap up (demand imbalance)
  - Bearish FVG: candle[i-1].low > candle[i+1].high → gap down (supply imbalance)

FVGs represent institutional order flow imbalances where price moved too quickly,
leaving unfilled orders. Price tends to revisit these zones (mean reversion).

Provides:
  - Real-time FVG detection per symbol per timeframe
  - FVG fill tracking (partially or fully filled)
  - FVG strength scoring (gap size relative to ATR)
  - Multi-timeframe FVG aggregation
  - Integration into institutional scoring pipeline
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger


@dataclass
class FVGEvent:
    """A single Fair Value Gap detection."""
    symbol: str
    fvg_type: str          # "bullish" or "bearish"
    gap_high: float        # Upper boundary of the gap
    gap_low: float         # Lower boundary of the gap
    gap_size: float        # Absolute gap size (price)
    gap_pct: float         # Gap as % of price
    strength: float        # 0-1 strength score (gap_size / ATR)
    timestamp: float
    interval: str          # Timeframe ("5m", "15m", "1h", etc.)
    filled: bool = False   # Whether the FVG has been filled
    fill_pct: float = 0.0  # 0-100% fill level
    origin_candle_idx: int = 0  # Index of the middle candle


@dataclass
class FVGState:
    """Per-symbol FVG tracking state."""
    symbol: str
    events: List[FVGEvent] = field(default_factory=list)
    unfilled_bullish: List[FVGEvent] = field(default_factory=list)
    unfilled_bearish: List[FVGEvent] = field(default_factory=list)
    last_fvg_side: str = ""
    fvg_momentum: float = 0.0  # -1 to 1 (bearish to bullish)


# Maximum FVGs to track per symbol
_MAX_FVGS = 100
# Minimum gap size as % of price to qualify as significant FVG
_MIN_GAP_PCT = 0.05  # 0.05% minimum
# FVG expiry (seconds) — ignore old FVGs
_FVG_EXPIRY = 86400 * 3  # 3 days


class FVGDetect:
    """
    Detects Fair Value Gaps from candlestick data.

    A bullish FVG exists when candle[i-1].high < candle[i+1].low.
    A bearish FVG exists when candle[i-1].low > candle[i+1].high.
    """

    def __init__(self) -> None:
        self._states: Dict[str, FVGState] = {}

    async def initialize(self) -> None:
        logger.info("FVG Detector ready")

    async def process_kline(self, symbol: str, kline: Dict) -> Optional[FVGEvent]:
        """
        Process one closed Binance kline in its own timeframe.
        FVG detection never mixes 1m/5m/15m/1h/4h candles.
        """
        if not kline.get("is_closed", False):
            return None

        st = self._states.setdefault(symbol, FVGState(symbol=symbol))
        interval = str(kline.get("interval", "5m"))
        candles = st.recent_candles_by_interval.setdefault(interval, [])
        candles.append(kline)
        if len(candles) > 50:
            st.recent_candles_by_interval[interval] = candles[-50:]
            candles = st.recent_candles_by_interval[interval]

        if len(candles) < 3:
            return None

        c_prev = candles[-3]
        c_mid = candles[-2]
        c_curr = candles[-1]

        h_prev = float(c_prev.get("high", 0) or 0)
        l_prev = float(c_prev.get("low", 0) or 0)
        l_curr = float(c_curr.get("low", 0) or 0)
        h_curr = float(c_curr.get("high", 0) or 0)
        c_mid_v = float(c_mid.get("close", 0) or 0)
        c_curr_v = float(c_curr.get("close", 0) or 0)

        if any(v <= 0 for v in [h_prev, l_prev, h_curr, l_curr]):
            return None

        price = c_mid_v if c_mid_v > 0 else c_curr_v
        if price <= 0:
            return None

        bull_list = st.unfilled_bullish_by_interval.setdefault(interval, [])
        bear_list = st.unfilled_bearish_by_interval.setdefault(interval, [])

        def event_timestamp() -> float:
            close_ms = float(kline.get("close_time", 0) or 0)
            if close_ms > 0:
                return close_ms / 1000.0
            open_ms = float(kline.get("open_time", 0) or 0)
            return open_ms / 1000.0 if open_ms > 0 else time.time()

        # Bullish FVG.
        if l_curr > h_prev:
            gap_size = l_curr - h_prev
            gap_pct = (gap_size / price) * 100.0
            if gap_pct >= _MIN_GAP_PCT:
                avg_range = np.mean([
                    max(float(cc.get("high", 0) or 0) - float(cc.get("low", 0) or 0), 0.001)
                    for cc in candles[-10:]
                ]) if len(candles) >= 5 else gap_size
                strength = min(gap_size / max(avg_range, 0.001), 1.0)
                event = FVGEvent(
                    symbol=symbol,
                    fvg_type="bullish",
                    gap_high=l_curr,
                    gap_low=h_prev,
                    gap_size=gap_size,
                    gap_pct=gap_pct,
                    strength=strength,
                    timestamp=event_timestamp(),
                    interval=interval,
                    filled=False,
                    fill_pct=0.0,
                    origin_candle_idx=len(candles) - 2,
                )
                st.events.append(event)
                bull_list.append(event)
                st.unfilled_bullish.append(event)
                st.last_fvg_side = "bullish"
                self._trim_interval_lists(st, interval)
                self._trim_events(st)
                return event

        # Bearish FVG.
        elif h_curr < l_prev:
            gap_size = l_prev - h_curr
            gap_pct = (gap_size / price) * 100.0
            if gap_pct >= _MIN_GAP_PCT:
                avg_range = np.mean([
                    max(float(cc.get("high", 0) or 0) - float(cc.get("low", 0) or 0), 0.001)
                    for cc in candles[-10:]
                ]) if len(candles) >= 5 else gap_size
                strength = min(gap_size / max(avg_range, 0.001), 1.0)
                event = FVGEvent(
                    symbol=symbol,
                    fvg_type="bearish",
                    gap_high=l_prev,
                    gap_low=h_curr,
                    gap_size=gap_size,
                    gap_pct=gap_pct,
                    strength=strength,
                    timestamp=event_timestamp(),
                    interval=interval,
                    filled=False,
                    fill_pct=0.0,
                    origin_candle_idx=len(candles) - 2,
                )
                st.events.append(event)
                bear_list.append(event)
                st.unfilled_bearish.append(event)
                st.last_fvg_side = "bearish"
                self._trim_interval_lists(st, interval)
                self._trim_events(st)
                return event

        self._update_fills(st, interval, l_curr, h_curr)
        return None

    def _update_fills(self, st: FVGState, interval: str, current_low: float, current_high: float) -> None:
        """Update only FVGs created on the same authenticated timeframe."""
        now = time.time()
        bull = st.unfilled_bullish_by_interval.setdefault(interval, [])
        bear = st.unfilled_bearish_by_interval.setdefault(interval, [])

        for fvg_list in (bull, bear):
            keep = []
            for fvg in fvg_list:
                if now - fvg.timestamp > _FVG_EXPIRY:
                    continue
                if fvg.fvg_type == "bullish":
                    if current_low <= fvg.gap_high:
                        if current_low <= fvg.gap_low:
                            fvg.fill_pct = 100.0
                            fvg.filled = True
                        else:
                            depth = fvg.gap_high - current_low
                            fvg.fill_pct = min((depth / fvg.gap_size) * 100.0, 100.0)
                            if fvg.fill_pct >= 90.0:
                                fvg.filled = True
                else:
                    if current_high >= fvg.gap_low:
                        if current_high >= fvg.gap_high:
                            fvg.fill_pct = 100.0
                            fvg.filled = True
                        else:
                            depth = current_high - fvg.gap_low
                            fvg.fill_pct = min((depth / fvg.gap_size) * 100.0, 100.0)
                            if fvg.fill_pct >= 90.0:
                                fvg.filled = True
                if not fvg.filled:
                    keep.append(fvg)
            fvg_list[:] = keep

        st.unfilled_bullish = [e for vals in st.unfilled_bullish_by_interval.values() for e in vals]
        st.unfilled_bearish = [e for vals in st.unfilled_bearish_by_interval.values() for e in vals]
        st.events = [e for e in st.events if not e.filled and (now - e.timestamp) < _FVG_EXPIRY]

    def _trim_interval_lists(self, st: FVGState, interval: str) -> None:
        bull = st.unfilled_bullish_by_interval.setdefault(interval, [])
        bear = st.unfilled_bearish_by_interval.setdefault(interval, [])
        if len(bull) > 50:
            bull[:] = bull[-25:]
        if len(bear) > 50:
            bear[:] = bear[-25:]
        st.unfilled_bullish = [e for vals in st.unfilled_bullish_by_interval.values() for e in vals]
        st.unfilled_bearish = [e for vals in st.unfilled_bearish_by_interval.values() for e in vals]

    def _trim_events(self, st: FVGState) -> None:
        """Keep event lists bounded."""
        if len(st.events) > _MAX_FVGS:
            st.events = st.events[-_MAX_FVGS // 2:]
        if len(st.unfilled_bullish) > 50:
            st.unfilled_bullish = st.unfilled_bullish[-25:]
        if len(st.unfilled_bearish) > 50:
            st.unfilled_bearish = st.unfilled_bearish[-25:]

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        """
        Get FVG analysis for a symbol — used by institutional scoring.

        Returns:
            {
                "has_bullish_fvg": bool,
                "has_bearish_fvg": bool,
                "fvg_score": float (0-100),
                "fvg_alignment": "bullish" | "bearish" | "neutral",
                "unfilled_bullish_count": int,
                "unfilled_bearish_count": int,
                "avg_fvg_strength": float,
                "latest_fvg_type": str,
                "fvg_momentum": float (-1 to 1),
            }
        """
        st = self._states.get(symbol)
        if not st:
            return None

        now = time.time()
        # Filter to recent FVGs (last 24h)
        recent = [f for f in st.events if now - f.timestamp < _FVG_EXPIRY]
        unfilled_bull = [f for f in st.unfilled_bullish if now - f.timestamp < _FVG_EXPIRY]
        unfilled_bear = [f for f in st.unfilled_bearish if now - f.timestamp < _FVG_EXPIRY]

        if not recent:
            return {
                "has_bullish_fvg": False,
                "has_bearish_fvg": False,
                "fvg_score": 50.0,  # Neutral when no data
                "fvg_alignment": "neutral",
                "unfilled_bullish_count": 0,
                "unfilled_bearish_count": 0,
                "avg_fvg_strength": 0.0,
                "latest_fvg_type": "none",
                "fvg_momentum": 0.0,
            }

        # Compute FVG alignment: which side has more unfilled FVGs
        bull_count = len(unfilled_bull)
        bear_count = len(unfilled_bear)
        total = bull_count + bear_count

        if total == 0:
            alignment = "neutral"
        elif bull_count > bear_count * 1.5:
            alignment = "bullish"
        elif bear_count > bull_count * 1.5:
            alignment = "bearish"
        else:
            alignment = "neutral"

        # FVG score: based on count, strength, and alignment
        avg_strength = np.mean([f.strength for f in recent]) if recent else 0
        count_score = min(total / 5, 1.0) * 40  # Up to 40 points from count
        strength_score = avg_strength * 30  # Up to 30 points from strength
        alignment_score = 30 if alignment != "neutral" else 10  # Up to 30 points from alignment

        fvg_score = count_score + strength_score + alignment_score

        # FVG momentum: positive = bullish FVGs dominating, negative = bearish
        if total > 0:
            fvg_momentum = (bull_count - bear_count) / total
        else:
            fvg_momentum = 0.0

        # Get latest unfilled FVG price levels for dashboard display
        latest_bull = max(unfilled_bull, key=lambda f: f.timestamp) if unfilled_bull else None
        latest_bear = max(unfilled_bear, key=lambda f: f.timestamp) if unfilled_bear else None
        # Use the most recent FVG regardless of direction
        all_unfilled = unfilled_bull + unfilled_bear
        latest_any = max(all_unfilled, key=lambda f: f.timestamp) if all_unfilled else None

        return {
            "has_bullish_fvg": bull_count > 0,
            "has_bearish_fvg": bear_count > 0,
            "fvg_score": min(fvg_score, 100.0),
            "fvg_alignment": alignment,
            "unfilled_bullish_count": bull_count,
            "unfilled_bearish_count": bear_count,
            "avg_fvg_strength": float(avg_strength),
            "latest_fvg_type": st.last_fvg_side or "none",
            "fvg_momentum": float(fvg_momentum),
            # Latest FVG price levels for display
            "fvg_gap_high": latest_any.gap_high if latest_any else 0,
            "fvg_gap_low": latest_any.gap_low if latest_any else 0,
            "fvg_gap_size": latest_any.gap_size if latest_any else 0,
            "fvg_latest_strength": latest_any.strength if latest_any else 0,
            # Bull-specific FVG price
            "fvg_bull_gap_high": latest_bull.gap_high if latest_bull else 0,
            "fvg_bull_gap_low": latest_bull.gap_low if latest_bull else 0,
            # Bear-specific FVG price
            "fvg_bear_gap_high": latest_bear.gap_high if latest_bear else 0,
            "fvg_bear_gap_low": latest_bear.gap_low if latest_bear else 0,
        }

    def get_unfilled_fvgs(self, symbol: str, side: str = "bullish") -> List[Dict]:
        """Get unfilled FVGs for a specific side — used for entry/TP targeting."""
        st = self._states.get(symbol)
        if not st:
            return []

        now = time.time()
        if side == "bullish":
            fvgs = [f for f in st.unfilled_bullish if not f.filled and (now - f.timestamp) < _FVG_EXPIRY]
        else:
            fvgs = [f for f in st.unfilled_bearish if not f.filled and (now - f.timestamp) < _FVG_EXPIRY]

        return [
            {
                "gap_high": f.gap_high,
                "gap_low": f.gap_low,
                "gap_size": f.gap_size,
                "strength": f.strength,
                "interval": f.interval,
                "age_seconds": now - f.timestamp,
            }
            for f in sorted(fvgs, key=lambda x: x.timestamp, reverse=True)[:10]
        ]
