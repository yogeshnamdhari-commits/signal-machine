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
        Process a new closed kline and check for FVG formation.
        Needs at least 3 consecutive closed candles to detect FVGs.
        """
        if not kline.get("is_closed", False):
            return None

        st = self._states.setdefault(symbol, FVGState(symbol=symbol))

        # Store recent closes for FVG detection
        if not hasattr(st, '_recent_candles'):
            st._recent_candles = []
        st._recent_candles.append(kline)
        if len(st._recent_candles) > 50:
            st._recent_candles = st._recent_candles[-50:]

        # Need at least 3 candles to detect FVG
        if len(st._recent_candles) < 3:
            return None

        candles = st._recent_candles
        c_prev = candles[-3]  # Candle i-1
        c_mid = candles[-2]   # Candle i (the middle/filling candle)
        c_curr = candles[-1]  # Candle i+1 (current candle)

        o_prev, h_prev, l_prev, c_prev_v = (
            c_prev.get("open", 0), c_prev.get("high", 0),
            c_prev.get("low", 0), c_prev.get("close", 0)
        )
        o_mid, h_mid, l_mid, c_mid_v = (
            c_mid.get("open", 0), c_mid.get("high", 0),
            c_mid.get("low", 0), c_mid.get("close", 0)
        )
        o_curr, h_curr, l_curr, c_curr_v = (
            c_curr.get("open", 0), c_curr.get("high", 0),
            c_curr.get("low", 0), c_curr.get("close", 0)
        )

        if any(v <= 0 for v in [h_prev, l_prev, h_curr, l_curr]):
            return None

        # Price for % calculation
        price = c_mid_v if c_mid_v > 0 else c_curr_v
        if price <= 0:
            return None

        # ── Bullish FVG: gap between prev high and curr low ──
        # Candle i-1 high < Candle i+1 low → demand imbalance (gap up)
        if l_curr > h_prev:
            gap_size = l_curr - h_prev
            gap_pct = (gap_size / price) * 100

            if gap_pct >= _MIN_GAP_PCT:
                # Strength: gap_size / ATR (use recent candle range as ATR proxy)
                avg_range = np.mean([
                    max(c.get("high", 0) - c.get("low", 0), 0.001)
                    for c in candles[-10:]
                ]) if len(candles) >= 5 else gap_size
                strength = min(gap_size / max(avg_range, 0.001), 1.0)

                interval = kline.get("interval", "5m")
                event = FVGEvent(
                    symbol=symbol,
                    fvg_type="bullish",
                    gap_high=l_curr,   # Upper boundary of gap
                    gap_low=h_prev,    # Lower boundary of gap
                    gap_size=gap_size,
                    gap_pct=gap_pct,
                    strength=strength,
                    timestamp=time.time(),
                    interval=interval,
                    filled=False,
                    fill_pct=0.0,
                    origin_candle_idx=len(st._recent_candles) - 2,
                )
                st.events.append(event)
                st.unfilled_bullish.append(event)
                st.last_fvg_side = "bullish"

                self._trim_events(st)
                logger.debug("🟢 BULLISH FVG: {} {} gap={:.4f} ({:.3f}%) strength={:.2f}",
                             symbol, interval, gap_size, gap_pct, strength)
                return event

        # ── Bearish FVG: gap between prev low and curr high ──
        # Candle i-1 low > Candle i+1 high → supply imbalance (gap down)
        elif h_curr < l_prev:
            gap_size = l_prev - h_curr
            gap_pct = (gap_size / price) * 100

            if gap_pct >= _MIN_GAP_PCT:
                avg_range = np.mean([
                    max(c.get("high", 0) - c.get("low", 0), 0.001)
                    for c in candles[-10:]
                ]) if len(candles) >= 5 else gap_size
                strength = min(gap_size / max(avg_range, 0.001), 1.0)

                interval = kline.get("interval", "5m")
                event = FVGEvent(
                    symbol=symbol,
                    fvg_type="bearish",
                    gap_high=l_prev,   # Upper boundary of gap
                    gap_low=h_curr,    # Lower boundary of gap
                    gap_size=gap_size,
                    gap_pct=gap_pct,
                    strength=strength,
                    timestamp=time.time(),
                    interval=interval,
                    filled=False,
                    fill_pct=0.0,
                    origin_candle_idx=len(st._recent_candles) - 2,
                )
                st.events.append(event)
                st.unfilled_bearish.append(event)
                st.last_fvg_side = "bearish"

                self._trim_events(st)
                logger.debug("🔴 BEARISH FVG: {} {} gap={:.4f} ({:.3f}%) strength={:.2f}",
                             symbol, interval, gap_size, gap_pct, strength)
                return event

        # ── Update fill status for existing unfilled FVGs ──
        self._update_fills(st, l_curr, h_curr)

        return None

    def _update_fills(self, st: FVGState, current_low: float, current_high: float) -> None:
        """Check if current price action has filled any existing FVGs."""
        now = time.time()

        for fvg_list in [st.unfilled_bullish, st.unfilled_bearish]:
            filled_indices = []
            for i, fvg in enumerate(fvg_list):
                # Expire old FVGs
                if now - fvg.timestamp > _FVG_EXPIRY:
                    filled_indices.append(i)
                    continue

                # Check fill: price enters the gap zone
                if fvg.fvg_type == "bullish":
                    # Bullish FVG fills when price drops into the gap (gap_low to gap_high)
                    if current_low <= fvg.gap_high:
                        if current_low <= fvg.gap_low:
                            fvg.fill_pct = 100.0
                            fvg.filled = True
                        else:
                            # Partial fill
                            fill_depth = fvg.gap_high - current_low
                            fvg.fill_pct = min((fill_depth / fvg.gap_size) * 100, 100.0)
                            if fvg.fill_pct >= 90:
                                fvg.filled = True
                else:
                    # Bearish FVG fills when price rises into the gap (gap_low to gap_high)
                    if current_high >= fvg.gap_low:
                        if current_high >= fvg.gap_high:
                            fvg.fill_pct = 100.0
                            fvg.filled = True
                        else:
                            fill_depth = current_high - fvg.gap_low
                            fvg.fill_pct = min((fill_depth / fvg.gap_size) * 100, 100.0)
                            if fvg.fill_pct >= 90:
                                fvg.filled = True

            # Remove filled/expired FVGs
            for i in sorted(filled_indices, reverse=True):
                fvg_list.pop(i)

        # Remove from main events list too
        st.events = [f for f in st.events if not f.filled and (now - f.timestamp) < _FVG_EXPIRY]

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
