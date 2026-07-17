"""
Cumulative Delta & Imbalance Engine — net pressure, divergence, zones.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from loguru import logger


@dataclass
class CDState:
    symbol: str
    cum_delta: float = 0
    buy_vol: float = 0
    sell_vol: float = 0
    delta_hist: List[float] = field(default_factory=list)
    price_hist: List[float] = field(default_factory=list)
    delta_by_price: Dict[float, float] = field(default_factory=dict)
    trades_buf: List[Dict] = field(default_factory=list)


class CumulativeDeltaEngine:
    def __init__(self) -> None:
        self._states: Dict[str, CDState] = {}
        self._lookback = 100

    async def initialize(self) -> None:
        logger.info("CumulativeDelta engine ready")

    async def process_trade(self, symbol: str, trade: Dict) -> None:
        st = self._states.setdefault(symbol, CDState(symbol=symbol))
        price = trade["price"]
        qty = trade["quantity"]
        val = price * qty
        delta = -val if trade["is_buyer_maker"] else val

        st.cum_delta += delta
        if trade["is_buyer_maker"]:
            st.sell_vol += val
        else:
            st.buy_vol += val

        st.delta_hist.append(delta)
        st.price_hist.append(price)
        if len(st.delta_hist) > self._lookback:
            st.delta_hist = st.delta_hist[-self._lookback:]
            st.price_hist = st.price_hist[-self._lookback:]

        # Volume profile
        bucket = round(price * 100) / 100
        st.delta_by_price[bucket] = st.delta_by_price.get(bucket, 0) + delta
        if len(st.delta_by_price) > 500:
            top = sorted(st.delta_by_price.items(), key=lambda x: abs(x[1]), reverse=True)[:300]
            st.delta_by_price = dict(top)

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        st = self._states.get(symbol)
        if not st or not st.delta_hist:
            return None

        # Delta momentum
        momentum = 0.0
        if len(st.delta_hist) >= 10:
            recent = st.delta_hist[-10:]
            weights = np.exp(np.linspace(-1, 0, len(recent)))
            weights /= weights.sum()
            momentum = float(np.clip(np.sum(np.array(recent) * weights) / max(abs(sum(recent)), 1), -1, 1))

        # Price-delta divergence
        divergence = 0.0
        if len(st.price_hist) >= 20 and len(st.delta_hist) >= 20:
            pc = (st.price_hist[-1] - st.price_hist[-20]) / st.price_hist[-20] if st.price_hist[-20] else 0
            dc = sum(st.delta_hist[-10:]) - sum(st.delta_hist[-20:-10])
            if pc > 0 and dc < 0:
                divergence = -min(abs(dc) / 100_000, 1)
            elif pc < 0 and dc > 0:
                divergence = min(abs(dc) / 100_000, 1)

        # Imbalance zones
        imbalances: List[Dict] = []
        if len(st.delta_by_price) >= 10:
            vals = list(st.delta_by_price.values())
            mu, sigma = np.mean(vals), np.std(vals)
            if sigma > 0:
                for price, delta in st.delta_by_price.items():
                    z = (delta - mu) / sigma
                    if abs(z) > 2:
                        imbalances.append({
                            "price": price, "delta": delta, "z_score": float(z),
                            "type": "buy_imbalance" if z > 0 else "sell_imbalance",
                        })
                imbalances.sort(key=lambda x: abs(x["z_score"]), reverse=True)
                imbalances = imbalances[:10]

        total = st.buy_vol + st.sell_vol
        buy_pressure = st.buy_vol / total if total > 0 else 0.5

        # Signal strength
        sig = momentum * 0.3 + divergence * 0.25
        if imbalances:
            bi = sum(1 for i in imbalances if i["type"] == "buy_imbalance")
            si = len(imbalances) - bi
            sig += (bi - si) / max(len(imbalances), 1) * 0.25
        sig = float(np.clip(sig, -1, 1))

        return {
            "symbol": symbol,
            "cumulative_delta": st.cum_delta,
            "delta_momentum": momentum,
            "price_delta_divergence": divergence,
            "imbalance_zones": imbalances,
            "buy_pressure": buy_pressure,
            "signal_strength": sig,
        }
