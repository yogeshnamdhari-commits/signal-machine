"""
Institutional Pattern Detection — iceberg, spoofing, absorption, sweep, stop hunt.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np
from loguru import logger
from config import config



@dataclass
class OBState:
    symbol: str = ""
    bids: List[Tuple[float, float]] = field(default_factory=list)
    asks: List[Tuple[float, float]] = field(default_factory=list)
    bid_hist: List = field(default_factory=list)
    ask_hist: List = field(default_factory=list)
    patterns: List[Dict] = field(default_factory=list)
    mid_price: float = 0
    spread: float = 0


class InstitutionalDetector:
    def __init__(self) -> None:
        self._states: Dict[str, OBState] = {}
        self._hist_max = 50

    async def initialize(self) -> None:
        logger.info("Institutional detector ready")

    async def process_orderbook(self, symbol: str, book: Dict) -> None:
        st = self._states.setdefault(symbol, OBState(symbol=symbol))
        st.bids = [(float(p), float(q)) for p, q in book.get("bids", [])[:10]]
        st.asks = [(float(p), float(q)) for p, q in book.get("asks", [])[:10]]
        st.bid_hist.append(st.bids)
        st.ask_hist.append(st.asks)
        if len(st.bid_hist) > self._hist_max:
            st.bid_hist = st.bid_hist[-self._hist_max:]
            st.ask_hist = st.ask_hist[-self._hist_max:]
        if st.bids and st.asks:
            st.mid_price = (st.bids[0][0] + st.asks[0][0]) / 2
            st.spread = st.asks[0][0] - st.bids[0][0]
        await self._detect(symbol, st)

    async def _detect(self, symbol: str, st: OBState) -> None:
        if len(st.bid_hist) < 5:
            return
        await self._detect_iceberg(symbol, st)
        await self._detect_spoofing(symbol, st)
        await self._detect_absorption(symbol, st)
        await self._detect_sweep(symbol, st)

    async def _detect_iceberg(self, symbol: str, st: OBState) -> None:
        for side_label, hist in [("buy", st.bid_hist), ("sell", st.ask_hist)]:
            if len(hist) < 5:
                continue
            level_vols: Dict[float, List[float]] = defaultdict(list)
            for snap in hist[-5:]:
                for price, qty in snap[:5]:
                    level_vols[round(price, 4)].append(qty)
            for price, vols in level_vols.items():
                if len(vols) >= 4:
                    avg = np.mean(vols)
                    std = np.std(vols) if len(vols) > 1 else 0
                    if avg > 0 and std / avg < 0.3 and avg > 1_000:
                        conf = min(0.5 + avg / 10_000 * 0.3, 0.95)
                        if conf >= config.scanner.iceberg_threshold:
                            st.patterns.append({"type": "iceberg", "side": side_label,
                                                 "price": price, "volume": float(avg),
                                                 "confidence": conf, "ts": time.time()})

    async def _detect_spoofing(self, symbol: str, st: OBState) -> None:
        for side_label, hist in [("buy", st.bid_hist), ("sell", st.ask_hist)]:
            if len(hist) < 3:
                continue
            prev = {round(p, 4): q for p, q in hist[-3]}
            curr = {round(p, 4): q for p, q in hist[-1]}
            for price, qty in prev.items():
                if price not in curr and qty > 5_000:
                    conf = min(0.4 + qty / 20_000 * 0.4, 0.9)
                    if conf >= config.scanner.spoofing_threshold:
                        st.patterns.append({"type": "spoofing", "side": side_label,
                                             "price": price, "volume": float(qty),
                                             "confidence": conf, "ts": time.time()})

    async def _detect_absorption(self, symbol: str, st: OBState) -> None:
        for side_label, hist in [("buy", st.bid_hist), ("sell", st.ask_hist)]:
            if len(hist) < 10:
                continue
            vol_sum: Dict[float, float] = defaultdict(float)
            for snap in hist[-10:]:
                for price, qty in snap[:5]:
                    vol_sum[round(price, 4)] += qty
            for price, total in vol_sum.items():
                if total > 50_000 and price > 0:  # guard: skip zero-price padding entries
                    # Check price stability
                    stayed = sum(1 for snap in hist[-10:] if any(
                        abs(p - price) / price < 0.001 for p, _ in snap[:3]
                    ))
                    if stayed / min(len(hist), 10) > 0.7:
                        conf = min(0.5 + total / 1_000_000 * 0.3, 0.9)
                        if conf >= config.scanner.absorption_threshold:
                            st.patterns.append({"type": "absorption", "side": side_label,
                                                 "price": price, "volume": float(total),
                                                 "confidence": conf, "ts": time.time()})

    async def _detect_sweep(self, symbol: str, st: OBState) -> None:
        mids = []
        for i in range(-5, 0):
            if len(st.bid_hist) >= abs(i) and len(st.ask_hist) >= abs(i):
                b, a = st.bid_hist[i], st.ask_hist[i]
                if b and a:
                    mids.append((b[0][0] + a[0][0]) / 2)
        if len(mids) < 3:
            return
        prices = np.array(mids)
        diff = np.diff(prices)
        rng = float(prices.max() - prices.min())
        if rng < st.mid_price * 0.001:
            return
        if len(diff) >= 2:
            if diff[-2] < 0 and diff[-1] > 0 and abs(diff[-1]) > abs(diff[-2]) * 1.5:
                conf = min(0.6 + rng / st.mid_price * 10, 0.95)
                if conf >= config.scanner.sweep_threshold:
                    st.patterns.append({"type": "sweep", "side": "buy", "price": st.mid_price,
                                         "volume": rng, "confidence": conf, "ts": time.time()})
            elif diff[-2] > 0 and diff[-1] < 0 and abs(diff[-1]) > abs(diff[-2]) * 1.5:
                conf = min(0.6 + rng / st.mid_price * 10, 0.95)
                if conf >= config.scanner.sweep_threshold:
                    st.patterns.append({"type": "sweep", "side": "sell", "price": st.mid_price,
                                         "volume": rng, "confidence": conf, "ts": time.time()})

    def get_patterns(self, symbol: str) -> List[Dict]:
        st = self._states.get(symbol)
        if not st:
            return []
        cutoff = time.time() - 300
        recent = [p for p in st.patterns if p["ts"] > cutoff]
        # Deduplicate
        seen: set = set()
        unique: List[Dict] = []
        for p in sorted(recent, key=lambda x: x["confidence"], reverse=True):
            key = (p["type"], p["side"], round(p["price"], 4))
            if key not in seen:
                seen.add(key)
                unique.append(p)
        return unique[:10]
