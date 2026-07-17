"""
Spoofing + Iceberg Detection — layering attacks and hidden order detection.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from loguru import logger


@dataclass
class SpoofEvent:
    symbol: str
    event_type: str      # "spoof" or "iceberg"
    side: str            # "bid" or "ask"
    price: float
    volume: float
    confidence: float
    timestamp: float
    evidence: Dict = field(default_factory=dict)


@dataclass
class IcebergState:
    symbol: str
    events: List[SpoofEvent] = field(default_factory=list)
    level_history: Dict[float, List[Dict]] = field(default_factory=dict)  # price -> [{qty, ts}]
    spoof_count: int = 0
    iceberg_count: int = 0


class SpoofingIcebergDetector:
    """
    Spoofing detection:
    - Large orders placed then quickly cancelled (layering)
    - Unusual order-to-fill ratio at a level

    Iceberg detection:
    - Persistent refilling at same price
    - Low variance in level size across snapshots
    """

    def __init__(self) -> None:
        self._states: Dict[str, IcebergState] = {}
        self._snapshot_history: Dict[str, List[Dict]] = defaultdict(list)
        self._max_history = 50

    async def initialize(self) -> None:
        logger.info("SpoofingIcebergDetector ready")

    async def process_orderbook(self, symbol: str, bids: List, asks: List) -> List[SpoofEvent]:
        st = self._states.setdefault(symbol, IcebergState(symbol=symbol))
        now = time.time()
        events: List[SpoofEvent] = []

        # Store snapshot
        snap = {"bids": bids[:10], "asks": asks[:10], "ts": now}
        history = self._snapshot_history[symbol]
        history.append(snap)
        if len(history) > self._max_history:
            self._snapshot_history[symbol] = history[-self._max_history:]

        if len(history) < 5:
            return events

        # Detect spoofing: large order appeared then disappeared
        for side_label, side_key in [("bid", "bids"), ("ask", "asks")]:
            prev_snap = history[-5]
            curr_snap = history[-1]

            prev_levels = {round(float(p), 4): float(q) for p, q in prev_snap.get(side_key, [])}
            curr_levels = {round(float(p), 4): float(q) for p, q in curr_snap.get(side_key, [])}

            for price, prev_qty in prev_levels.items():
                if price not in curr_levels and prev_qty > 50_000:
                    # Large order vanished = potential spoof
                    confidence = min(0.4 + prev_qty / 200_000 * 0.4, 0.9)
                    event = SpoofEvent(
                        symbol=symbol, event_type="spoof", side=side_label,
                        price=price, volume=prev_qty, confidence=confidence,
                        timestamp=now,
                        evidence={"appeared_then_vanished": True, "vanish_window": 5},
                    )
                    events.append(event)
                    st.events.append(event)
                    st.spoof_count += 1

        # Detect iceberg: persistent refilling
        for side_label, side_key in [("bid", "bids"), ("ask", "asks")]:
            level_vols: Dict[float, List[float]] = defaultdict(list)
            for snap_item in history[-10:]:
                for p, q in snap_item.get(side_key, [])[:5]:
                    level_vols[round(float(p), 4)].append(float(q))

            for price, vols in level_vols.items():
                if len(vols) >= 7:  # Present in most snapshots
                    avg = np.mean(vols)
                    std = np.std(vols) if len(vols) > 1 else 0
                    cv = std / avg if avg > 0 else 0  # Coefficient of variation

                    if cv < 0.2 and avg > 10_000:  # Very consistent refill
                        confidence = min(0.5 + avg / 100_000 * 0.3, 0.95)
                        event = SpoofEvent(
                            symbol=symbol, event_type="iceberg", side=side_label,
                            price=price, volume=avg, confidence=confidence,
                            timestamp=now,
                            evidence={"persistence": len(vols), "cv": float(cv), "avg_qty": float(avg)},
                        )
                        events.append(event)
                        st.events.append(event)
                        st.iceberg_count += 1

        if len(st.events) > 500:
            st.events = st.events[-250:]

        return events

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        st = self._states.get(symbol)
        if not st:
            return None
        recent = [e for e in st.events if time.time() - e.timestamp < 3600]
        spoofs = [e for e in recent if e.event_type == "spoof"]
        icebergs = [e for e in recent if e.event_type == "iceberg"]
        return {
            "symbol": symbol,
            "total_events": len(recent),
            "spoof_count": len(spoofs),
            "iceberg_count": len(icebergs),
            "total_spoofs_historical": st.spoof_count,
            "total_icebergs_historical": st.iceberg_count,
            "recent_spoofs": [{"price": e.price, "side": e.side, "conf": e.confidence} for e in spoofs[:5]],
            "recent_icebergs": [{"price": e.price, "side": e.side, "conf": e.confidence, "avg": e.evidence.get("avg_qty", 0)} for e in icebergs[:5]],
            "manipulation_risk": "high" if len(spoofs) >= 3 else "medium" if len(spoofs) >= 1 else "low",
        }
