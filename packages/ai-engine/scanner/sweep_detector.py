"""
Sweep Detection — liquidity sweep / stop hunt / wick rejection detection.
Enhanced: volume spike confirmation, multi-bar context, rejection strength scoring.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from loguru import logger


@dataclass
class SweepEvent:
    symbol: str
    sweep_type: str      # "high_sweep", "low_sweep"
    sweep_price: float
    reject_price: float
    wick_ratio: float     # wick / body ratio
    volume_spike: float   # vs average
    timestamp: float
    confidence: float


@dataclass
class SweepState:
    symbol: str
    events: List[SweepEvent] = field(default_factory=list)
    recent_sweep_count: int = 0
    last_sweep_side: str = ""
    sweep_momentum: float = 0
    vol_history: List[float] = field(default_factory=list)
    vol_history_by_interval: Dict[str, List[float]] = field(default_factory=dict)
    events_by_interval: Dict[str, List[SweepEvent]] = field(default_factory=dict)


class SweepDetector:
    """
    Liquidity sweep detection:
    - High sweep (wick above, close below) with volume confirmation
    - Low sweep (wick below, close above) with volume confirmation
    - Multi-bar context to filter noise
    - Rejection strength scoring with body ratio
    """

    def __init__(self) -> None:
        self._states: Dict[str, SweepState] = {}
        self._wick_threshold = 2.0  # wick > 2x body
        self._volume_spike_min = 1.3  # volume must be 1.3x average
        self._vol_lookback = 20

    async def initialize(self) -> None:
        logger.info("SweepDetector ready")

    async def process_kline(self, symbol: str, kline: Dict) -> Optional[SweepEvent]:
        """Detect sweeps from one closed candle timeframe without cross-timeframe mixing."""
        if not kline.get("is_closed", False):
            return None
        st = self._states.setdefault(symbol, SweepState(symbol=symbol))
        interval = str(kline.get("interval", "5m"))
        o = float(kline.get("open", 0) or 0)
        h = float(kline.get("high", 0) or 0)
        l = float(kline.get("low", 0) or 0)
        close = float(kline.get("close", 0) or 0)
        vol = float(kline.get("volume", 0) or 0)
        if o <= 0 or h <= 0 or l <= 0 or close <= 0:
            return None

        hist = st.vol_history_by_interval.setdefault(interval, [])
        hist.append(vol)
        if len(hist) > self._vol_lookback:
            st.vol_history_by_interval[interval] = hist[-self._vol_lookback:]
            hist = st.vol_history_by_interval[interval]

        body = abs(close - o)
        upper_wick = h - max(o, close)
        lower_wick = min(o, close) - l
        avg_vol = np.mean(hist) if len(hist) >= 5 else vol
        vol_spike = vol / avg_vol if avg_vol > 0 else 1.0

        event_ts_ms = float(kline.get("close_time", 0) or 0)
        event_ts = event_ts_ms / 1000.0 if event_ts_ms > 0 else time.time()
        event = None

        if upper_wick > body * self._wick_threshold and upper_wick > 0:
            wick_ratio = upper_wick / max(body, 0.0001)
            reject_level = max(o, close)
            body_ratio = body / (h - l) if (h - l) > 0 else 0
            vol_conf = min(vol_spike / 3.0, 1.0)
            conf = min((wick_ratio / 5) * 0.4 + vol_conf * 0.35 + (1 - body_ratio) * 0.25, 1.0)
            if conf > 0.3 and vol_spike >= self._volume_spike_min:
                event = SweepEvent(
                    symbol=symbol, sweep_type="high_sweep",
                    sweep_price=h, reject_price=reject_level,
                    wick_ratio=wick_ratio, volume_spike=vol_spike,
                    timestamp=event_ts, confidence=conf,
                )
        elif lower_wick > body * self._wick_threshold and lower_wick > 0:
            wick_ratio = lower_wick / max(body, 0.0001)
            reject_level = min(o, close)
            body_ratio = body / (h - l) if (h - l) > 0 else 0
            vol_conf = min(vol_spike / 3.0, 1.0)
            conf = min((wick_ratio / 5) * 0.4 + vol_conf * 0.35 + (1 - body_ratio) * 0.25, 1.0)
            if conf > 0.3 and vol_spike >= self._volume_spike_min:
                event = SweepEvent(
                    symbol=symbol, sweep_type="low_sweep",
                    sweep_price=l, reject_price=reject_level,
                    wick_ratio=wick_ratio, volume_spike=vol_spike,
                    timestamp=event_ts, confidence=conf,
                )

        if event:
            by_iv = st.events_by_interval.setdefault(interval, [])
            by_iv.append(event)
            if len(by_iv) > 200:
                st.events_by_interval[interval] = by_iv[-100:]
                by_iv = st.events_by_interval[interval]
            st.events.append(event)
            if len(st.events) > 500:
                st.events = st.events[-250:]
            now = time.time()
            recent = [e for e in st.events if now - e.timestamp < 3600]
            st.recent_sweep_count = len(recent)
            st.last_sweep_side = event.sweep_type

        return event

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        st = self._states.get(symbol)
        if not st:
            return None
        recent = [e for e in st.events if time.time() - e.timestamp < 3600]
        high_sweeps = sum(1 for e in recent if e.sweep_type == "high_sweep")
        low_sweeps = sum(1 for e in recent if e.sweep_type == "low_sweep")
        avg_conf = np.mean([e.confidence for e in recent]) if recent else 0
        # Get latest sweep price for dashboard display
        latest_sweep = max(recent, key=lambda e: e.timestamp) if recent else None
        return {
            "symbol": symbol,
            "recent_sweep_count": len(recent),
            "high_sweeps": high_sweeps,
            "low_sweeps": low_sweeps,
            "last_sweep_side": st.last_sweep_side,
            "avg_confidence": float(avg_conf),
            "sweep_price": latest_sweep.sweep_price if latest_sweep else 0,
            "sweep_reject_price": latest_sweep.reject_price if latest_sweep else 0,
            "signal": (
                "bearish_rejection" if high_sweeps > low_sweeps and high_sweeps >= 2 else
                "bullish_rejection" if low_sweeps > high_sweeps and low_sweeps >= 2 else
                "neutral"
            ),
        }
