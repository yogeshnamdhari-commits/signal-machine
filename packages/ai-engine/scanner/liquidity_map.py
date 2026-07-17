"""
Liquidity Mapping Engine — maps liquidity clusters, stop zones, key levels.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger


@dataclass
class LiquidityLevel:
    price: float
    liquidity_type: str   # "bid_cluster", "ask_cluster", "stop_cluster", "poc"
    total_volume: float
    touch_count: int
    confidence: float
    last_seen: float


@dataclass
class LiquidityMap:
    symbol: str
    levels: List[LiquidityLevel] = field(default_factory=list)
    poc: float = 0            # Point of Control (highest volume level)
    value_area_high: float = 0
    value_area_low: float = 0
    support_levels: List[float] = field(default_factory=list)
    resistance_levels: List[float] = field(default_factory=list)
    stop_zones: List[Dict] = field(default_factory=list)


class LiquidityMappingEngine:
    """
    Liquidity mapping:
    - Volume profile (POC, value area)
    - Support/resistance from liquidity clusters
    - Stop loss zone detection
    - Liquidity heatmap generation
    """

    def __init__(self) -> None:
        self._maps: Dict[str, LiquidityMap] = {}
        self._volume_profile: Dict[str, Dict[float, float]] = defaultdict(lambda: defaultdict(float))
        self._price_bins: Dict[str, List[float]] = {}
        self._bin_size_pct = 0.001  # 0.1% bins

    async def initialize(self) -> None:
        logger.info("LiquidityMapping engine ready")

    async def process_kline(self, symbol: str, kline: Dict) -> None:
        lm = self._maps.setdefault(symbol, LiquidityMap(symbol=symbol))
        o, h, l, c, v = kline["open"], kline["high"], kline["low"], kline["close"], kline.get("volume", 0)
        mid = (h + l + c) / 3  # typical price
        bin_size = mid * self._bin_size_pct
        if bin_size <= 0:
            return
        bucket = round(mid / bin_size) * bin_size
        self._volume_profile[symbol][bucket] += v

    async def process_orderbook(self, symbol: str, bids: List, asks: List) -> None:
        lm = self._maps.setdefault(symbol, LiquidityMap(symbol=symbol))

        # Bid clusters
        bid_levels = defaultdict(float)
        for p, q in bids:
            bid_levels[round(float(p), 2)] += float(q)

        # Ask clusters
        ask_levels = defaultdict(float)
        for p, q in asks:
            ask_levels[round(float(p), 2)] += float(q)

        # Detect stop zones (concentrated liquidity near round numbers)
        now = time.time()
        all_levels = []
        avg_bid = np.mean(list(bid_levels.values())) if bid_levels else 0
        avg_ask = np.mean(list(ask_levels.values())) if ask_levels else 0
        # With single-level bookTicker, use absolute threshold instead of relative
        use_absolute = len(bid_levels) <= 2 and len(ask_levels) <= 2

        for price, vol in bid_levels.items():
            if vol > avg_bid * 3 or (use_absolute and vol > 0.5):
                lm.levels.append(LiquidityLevel(
                    price=price, liquidity_type="bid_cluster",
                    total_volume=vol, touch_count=1,
                    confidence=min(vol / (avg_bid * 5), 1),
                    last_seen=now,
                ))
                all_levels.append({"price": price, "type": "support", "volume": vol})

        for price, vol in ask_levels.items():
            if vol > avg_ask * 3 or (use_absolute and vol > 0.5):
                lm.levels.append(LiquidityLevel(
                    price=price, liquidity_type="ask_cluster",
                    total_volume=vol, touch_count=1,
                    confidence=min(vol / (avg_ask * 5), 1),
                    last_seen=now,
                ))
                all_levels.append({"price": price, "type": "resistance", "volume": vol})

        # Deduplicate and sort
        lm.support_levels = sorted([l["price"] for l in all_levels if l["type"] == "support"], reverse=True)[:5]
        lm.resistance_levels = sorted([l["price"] for l in all_levels if l["type"] == "resistance"])[:5]

        if len(lm.levels) > 500:
            lm.levels = lm.levels[-250:]

    def calculate_volume_profile(self, symbol: str) -> None:
        """Calculate POC and value area from volume profile."""
        lm = self._maps.setdefault(symbol, LiquidityMap(symbol=symbol))
        profile = self._volume_profile.get(symbol, {})
        if not profile:
            return

        sorted_levels = sorted(profile.items(), key=lambda x: x[1], reverse=True)
        if sorted_levels:
            lm.poc = sorted_levels[0][0]

        total_vol = sum(profile.values())
        if total_vol == 0:
            return

        # Value area (70% of volume)
        cumulative = 0
        va_prices = []
        for price, vol in sorted_levels:
            cumulative += vol
            va_prices.append(price)
            if cumulative >= total_vol * 0.7:
                break

        if va_prices:
            lm.value_area_high = max(va_prices)
            lm.value_area_low = min(va_prices)

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        self.calculate_volume_profile(symbol)
        lm = self._maps.get(symbol)
        if not lm:
            return None
        return {
            "symbol": symbol,
            "poc": lm.poc,
            "value_area_high": lm.value_area_high,
            "value_area_low": lm.value_area_low,
            "support_levels": lm.support_levels,
            "resistance_levels": lm.resistance_levels,
            "total_liquidity_levels": len(lm.levels),
            "nearby_support": lm.support_levels[0] if lm.support_levels else 0,
            "nearby_resistance": lm.resistance_levels[0] if lm.resistance_levels else 0,
        }
