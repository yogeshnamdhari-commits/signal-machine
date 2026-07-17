"""
Absorption Detection — passive order absorption at key levels.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from loguru import logger


@dataclass
class AbsorptionEvent:
    symbol: str
    price_level: float
    side: str            # "bid_absorption" or "ask_absorption"
    volume_absorbed: float
    trade_count: int
    price_stability: float  # how stable price was at level
    timestamp: float
    confidence: float


@dataclass
class AbsorptionState:
    symbol: str
    events: List[AbsorptionEvent] = field(default_factory=list)
    active_levels: Dict[float, Dict] = field(default_factory=dict)


class AbsorptionDetector:
    """
    Absorption detection:
    - Large resting orders absorbing aggressive flow
    - Price stability at a level despite heavy volume
    - Support/resistance reinforcement
    """

    def __init__(self) -> None:
        self._states: Dict[str, AbsorptionState] = {}
        self._min_volume = 10_000  # lowered from 100K for testnet/low-timeframe detection
        self._stability_threshold = 0.005  # 0.5% price tolerance

    async def initialize(self) -> None:
        logger.info("AbsorptionDetector ready")

    async def process_trades(self, symbol: str, trades: List[Dict], orderbook: Dict) -> Optional[AbsorptionEvent]:
        st = self._states.setdefault(symbol, AbsorptionState(symbol=symbol))

        # Filter out synthetic trades
        real_trades = [t for t in trades if t.get("_source") != "ticker_arr"]
        if len(real_trades) < 10:
            return None

        prices = [t["price"] for t in real_trades[-20:]]
        volumes = [t["quantity"] * t["price"] for t in real_trades[-20:]]
        total_vol = sum(volumes)

        if total_vol < self._min_volume:
            return None

        # Find price cluster (most volume at a level)
        price_clusters: Dict[float, float] = {}
        for p, v in zip(prices, volumes):
            bucket = round(p, 2)
            price_clusters[bucket] = price_clusters.get(bucket, 0) + v

        if not price_clusters:
            return None

        top_price = max(price_clusters, key=price_clusters.get)
        top_vol = price_clusters[top_price]

        # Check price stability
        price_range = max(prices) - min(prices)
        mid_price = np.mean(prices)
        stability = 1 - (price_range / mid_price) if mid_price > 0 else 0

        # Check if orderbook supports absorption
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        side = "neutral"
        confidence = 0

        # Bid absorption: aggressive selling absorbed by bids
        buy_trades = sum(1 for t in real_trades[-20:] if not t["is_buyer_maker"])
        sell_trades = len(real_trades[-20:]) - buy_trades

        if sell_trades > buy_trades * 1.2 and stability > 0.85:
            side = "bid_absorption"
            confidence = min(stability * (top_vol / 50_000), 1)
        elif buy_trades > sell_trades * 1.2 and stability > 0.85:
            side = "ask_absorption"
            confidence = min(stability * (top_vol / 50_000), 1)

        if confidence > 0.25:
            event = AbsorptionEvent(
                symbol=symbol, price_level=top_price, side=side,
                volume_absorbed=top_vol, trade_count=len(trades[-20:]),
                price_stability=stability, timestamp=time.time(),
                confidence=confidence,
            )
            st.events.append(event)
            if len(st.events) > 100:
                st.events = st.events[-50:]
            return event

        return None

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        st = self._states.get(symbol)
        if not st or not st.events:
            return None
        recent = [e for e in st.events if time.time() - e.timestamp < 3600]
        if not recent:
            return None
        bid_abs = [e for e in recent if e.side == "bid_absorption"]
        ask_abs = [e for e in recent if e.side == "ask_absorption"]
        return {
            "symbol": symbol,
            "total_absorptions": len(recent),
            "bid_absorptions": len(bid_abs),
            "ask_absorptions": len(ask_abs),
            "avg_confidence": float(np.mean([e.confidence for e in recent])),
            "top_levels": [{"price": e.price_level, "side": e.side, "vol": e.volume_absorbed} for e in sorted(recent, key=lambda x: x.volume_absorbed, reverse=True)[:3]],
            "signal": (
                "strong_support" if len(bid_abs) > len(ask_abs) and len(bid_abs) >= 2 else
                "strong_resistance" if len(ask_abs) > len(bid_abs) and len(ask_abs) >= 2 else
                "neutral"
            ),
        }
