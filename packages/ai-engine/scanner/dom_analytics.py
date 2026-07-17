"""
DOM Analytics — orderbook depth, imbalance, pressure, levels analysis.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger


@dataclass
class DOMSnapshot:
    symbol: str
    timestamp: float
    bid_depth: float = 0      # Total bid quantity (top N levels)
    ask_depth: float = 0      # Total ask quantity (top N levels)
    spread: float = 0
    mid_price: float = 0
    imbalance: float = 0      # (bid - ask) / (bid + ask)
    bid_levels: int = 0
    ask_levels: int = 0
    large_bid_wall: Optional[float] = None   # Price with unusually large bid
    large_ask_wall: Optional[float] = None   # Price with unusually large ask
    bid_wall_size: float = 0
    ask_wall_size: float = 0
    cumulative_delta_side: str = ""  # "buy" or "sell"


@dataclass
class DOMState:
    symbol: str
    snapshots: List[DOMSnapshot] = field(default_factory=list)
    level_history: Dict[float, List[float]] = field(default_factory=dict)  # price -> [qty history]
    wall_prices: Dict[str, float] = field(default_factory=dict)  # "bid"/"ask" -> price


class DOMAnalytics:
    """
    Deep orderbook analysis:
    - Bid/ask depth & imbalance
    - Large order detection (walls)
    - Level persistence tracking
    - Pressure zone identification
    """

    def __init__(self, depth: int = 20) -> None:
        self._depth = depth
        self._states: Dict[str, DOMState] = {}
        self._wall_threshold = 3.0  # 3x average = wall

    async def initialize(self) -> None:
        logger.info("DOM Analytics ready (depth={})", self._depth)

    async def process_orderbook(self, symbol: str, bids: List, asks: List) -> None:
        st = self._states.setdefault(symbol, DOMState(symbol=symbol))
        now = time.time()

        bids_f = [(float(p), float(q)) for p, q in bids[:self._depth]]
        asks_f = [(float(p), float(q)) for p, q in asks[:self._depth]]

        if not bids_f or not asks_f:
            return

        bid_depth = sum(q for _, q in bids_f)
        ask_depth = sum(q for _, q in asks_f)
        spread = asks_f[0][0] - bids_f[0][0]
        mid = (bids_f[0][0] + asks_f[0][0]) / 2
        imbalance = (bid_depth - ask_depth) / (bid_depth + ask_depth) if (bid_depth + ask_depth) > 0 else 0

        # Detect walls
        avg_bid = bid_depth / len(bids_f) if bids_f else 0
        avg_ask = ask_depth / len(asks_f) if asks_f else 0
        large_bid = max(bids_f, key=lambda x: x[1]) if bids_f else (0, 0)
        large_ask = max(asks_f, key=lambda x: x[1]) if asks_f else (0, 0)

        bid_wall = large_bid[0] if large_bid[1] > avg_bid * self._wall_threshold else None
        ask_wall = large_ask[0] if large_ask[1] > avg_ask * self._wall_threshold else None

        # Track level history
        for price, qty in bids_f + asks_f:
            key = round(price, 6)
            hist = st.level_history.setdefault(key, [])
            hist.append(qty)
            if len(hist) > 100:
                st.level_history[key] = hist[-50:]

        snap = DOMSnapshot(
            symbol=symbol, timestamp=now,
            bid_depth=bid_depth, ask_depth=ask_depth,
            spread=spread, mid_price=mid, imbalance=imbalance,
            bid_levels=len(bids_f), ask_levels=len(asks_f),
            large_bid_wall=bid_wall, large_ask_wall=ask_wall,
            bid_wall_size=large_bid[1], ask_wall_size=large_ask[1],
            cumulative_delta_side="buy" if imbalance > 0 else "sell",
        )
        st.snapshots.append(snap)
        if len(st.snapshots) > 200:
            st.snapshots = st.snapshots[-100:]

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        st = self._states.get(symbol)
        if not st or not st.snapshots:
            return None
        latest = st.snapshots[-1]

        # Trend (last 10 snapshots)
        if len(st.snapshots) >= 10:
            recent = st.snapshots[-10:]
            imb_trend = np.mean([s.imbalance for s in recent])
            depth_trend = np.mean([s.bid_depth - s.ask_depth for s in recent])
        else:
            imb_trend = latest.imbalance
            depth_trend = latest.bid_depth - latest.ask_depth

        # Persistent levels
        persistent = []
        for price, hist in st.level_history.items():
            if len(hist) >= 5:
                avg = np.mean(hist[-5:])
                std = np.std(hist[-5:]) if len(hist) > 1 else 0
                if avg > 0 and std / avg < 0.3:  # Stable level
                    persistent.append({"price": price, "avg_qty": float(avg), "stability": float(1 - std / avg) if avg > 0 else 0})
        persistent.sort(key=lambda x: x["avg_qty"], reverse=True)

        return {
            "symbol": symbol,
            "bid_depth": latest.bid_depth,
            "ask_depth": latest.ask_depth,
            "spread": latest.spread,
            "mid_price": latest.mid_price,
            "imbalance": latest.imbalance,
            "imbalance_trend": float(imb_trend),
            "depth_imbalance": float(depth_trend),
            "has_bid_wall": latest.large_bid_wall is not None,
            "has_ask_wall": latest.large_ask_wall is not None,
            "bid_wall_price": latest.large_bid_wall,
            "ask_wall_price": latest.large_ask_wall,
            "persistent_levels": persistent[:5],
            "pressure_side": "buy" if latest.imbalance > 0.1 else ("sell" if latest.imbalance < -0.1 else "neutral"),
        }
