"""
Smart Money Engine — institutional order detection, stealth accumulation/distribution.
Extended: reaccumulation, redistribution, hidden order depth, liquidity pool detection,
sweep/absorption integration, and composite Smart Money Strength Score (0-100).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from loguru import logger

LARGE_ORDER_USD = 5_000  # lowered from 50K for testnet/retail detection


@dataclass
class SmartMoneyState:
    symbol: str
    events: List[Dict] = field(default_factory=list)
    accumulation_score: float = 0  # -1 to 1
    distribution_score: float = 0
    stealth_buys: int = 0
    stealth_sells: int = 0
    hidden_orders: List[Dict] = field(default_factory=list)
    institutional_flow: float = 0
    median_trade_value: float = 0  # adaptive threshold
    # Extended fields
    reaccumulation_score: float = 0
    redistribution_score: float = 0
    hidden_order_depth: int = 0
    liquidity_pool_score: float = 0
    sweep_confidence: float = 0
    absorption_confidence: float = 0
    iceberg_confidence: float = 0
    smart_money_strength: float = 0  # 0-100
    # Price action tracking for reaccum/redistrib detection
    _price_history: List[float] = field(default_factory=list)
    _volume_history: List[float] = field(default_factory=list)
    _flow_history: List[float] = field(default_factory=list)


class SmartMoneyEngine:
    """
    Smart money detection:
    - Stealth accumulation (small orders at same level)
    - Distribution detection (stealth selling)
    - Hidden order inference
    - Institutional flow scoring
    """

    def __init__(self) -> None:
        self._states: Dict[str, SmartMoneyState] = {}
        self._min_trades_for_pattern = 2  # lowered for faster detection
        self._cluster_tolerance = 0.005  # 0.5% price tolerance (wider for detection)

    async def initialize(self) -> None:
        logger.info("SmartMoney engine ready")

    async def process_trade(self, symbol: str, trade: Dict) -> None:
        st = self._states.setdefault(symbol, SmartMoneyState(symbol=symbol))
        price = trade["price"]
        qty = trade["quantity"]
        val = price * qty
        is_maker = trade["is_buyer_maker"]

        st.events.append({
            "price": price, "quantity": qty, "value": val,
            "is_maker": is_maker, "ts": trade["trade_time"],
        })
        if len(st.events) > 5000:
            st.events = st.events[-2500:]

        # Track price/volume/flow history for reaccum/redistrib detection
        st._price_history.append(price)
        st._volume_history.append(val)
        direction = -1 if is_maker else 1
        st._flow_history.append(direction * val)
        if len(st._price_history) > 500:
            st._price_history = st._price_history[-500:]
            st._volume_history = st._volume_history[-500:]
            st._flow_history = st._flow_history[-500:]

        # Update adaptive median trade value
        if len(st.events) >= 10:
            vals = sorted([e["value"] for e in st.events[-100:]])
            st.median_trade_value = vals[len(vals) // 2]

        # Detect stealth patterns (many small trades at similar price)
        self._detect_stealth(symbol, st)

        # Detect reaccumulation / redistribution
        self._detect_reaccumulation_redistribution(symbol, st)

        # Track institutional flow
        if val >= LARGE_ORDER_USD:
            direction = -1 if is_maker else 1
            st.institutional_flow += direction * val

        # Update hidden order depth
        st.hidden_order_depth = len(st.hidden_orders)

    def _detect_stealth(self, symbol: str, st: SmartMoneyState) -> None:
        if len(st.events) < self._min_trades_for_pattern:
            return

        recent = st.events[-50:]
        # Group by similar price — use reference price for consistent bucketing
        if not recent:
            return
        ref_price = recent[-1]["price"]
        bucket_size = max(ref_price * self._cluster_tolerance, 0.0001)
        price_buckets: Dict[float, List[Dict]] = {}
        for e in recent:
            bucket = round(e["price"] / bucket_size) * bucket_size
            price_buckets.setdefault(bucket, []).append(e)

        for bucket, trades in price_buckets.items():
            if len(trades) >= self._min_trades_for_pattern:
                # Stealth detection: count individual small trades at same level
                ref_val = max(st.median_trade_value, 100)  # fallback $100
                small_threshold = ref_val * 0.4  # trade < 40% of median = "small" (widened)
                small_trades = [t for t in trades if t["value"] < small_threshold]
                small_count = len(small_trades)
                small_total = sum(t["value"] for t in small_trades)

                # Stealth: small trades clustered at same price = smart money hiding
                if small_count >= 2:  # lowered from 3
                    buys = sum(1 for t in small_trades if not t["is_maker"])
                    sells = sum(1 for t in small_trades if t["is_maker"])

                    if buys > sells * 1.1:  # relaxed from 1.2
                        st.stealth_buys += 1
                        st.accumulation_score = min(st.accumulation_score + 0.15, 1)
                        # Mark as hidden order
                        if not any(h["price"] == bucket for h in st.hidden_orders):
                            st.hidden_orders.append({
                                "price": bucket, "type": "accumulation",
                                "strength": min(small_total / ref_val, 1),
                                "ts": time.time(),
                            })
                    elif sells > buys * 1.1:
                        st.stealth_sells += 1
                        st.distribution_score = min(st.distribution_score + 0.15, 1)
                        if not any(h["price"] == bucket for h in st.hidden_orders):
                            st.hidden_orders.append({
                                "price": bucket, "type": "distribution",
                                "strength": min(small_total / ref_val, 1),
                                "ts": time.time(),
                            })

        # ── FLOW-BASED SCORING: use institutional flow direction ──
        # When stealth clustering fails, infer from buy/sell volume ratio
        if len(recent) >= 5:
            buy_vol = sum(e["value"] for e in recent if not e["is_maker"])
            sell_vol = sum(e["value"] for e in recent if e["is_maker"])
            total_vol = buy_vol + sell_vol
            if total_vol > 0:
                flow_ratio = buy_vol / total_vol
                # Strong flow imbalance → boost accum/dist
                if flow_ratio > 0.6:  # >60% buy volume
                    st.accumulation_score = min(st.accumulation_score + 0.05, 1)
                elif flow_ratio < 0.4:  # >60% sell volume
                    st.distribution_score = min(st.distribution_score + 0.05, 1)

        # Decay scores (slower decay to maintain signal)
        st.accumulation_score *= 0.995
        st.distribution_score *= 0.995

    def _detect_reaccumulation_redistribution(self, symbol: str, st: SmartMoneyState) -> None:
        """
        Reaccumulation: price ranging/consolidating after downtrend with net buy flow.
        Redistribution: price ranging/consolidating after uptrend with net sell flow.
        
        Key signals:
        - Price in tight range (consolidation)
        - Net flow direction vs prior trend
        - Volume pattern: declining volume during consolidation
        """
        if len(st._price_history) < 50 or len(st._flow_history) < 50:
            return

        prices = np.array(st._price_history[-100:])
        flows = np.array(st._flow_history[-100:])
        volumes = np.array(st._volume_history[-100:])

        # Price range analysis
        price_range = (prices.max() - prices.min()) / prices.mean() if prices.mean() > 0 else 0
        is_consolidating = price_range < 0.02  # <2% range = consolidation

        if not is_consolidating:
            return

        # Prior trend (first half vs second half)
        mid = len(prices) // 2
        prior_trend = (prices[mid] - prices[0]) / prices[0] if prices[0] > 0 else 0

        # Current net flow
        net_flow = flows.sum()
        buy_flow = flows[flows > 0].sum()
        sell_flow = abs(flows[flows < 0].sum())
        flow_ratio = buy_flow / max(buy_flow + sell_flow, 1)

        # Volume trend (declining = consolidation)
        vol_first = volumes[:mid].mean() if mid > 0 else 0
        vol_second = volumes[mid:].mean() if len(volumes) > mid else 0
        vol_declining = vol_second < vol_first * 0.85 if vol_first > 0 else False

        # Reaccumulation: downtrend + consolidation + net buying
        if prior_trend < -0.005 and flow_ratio > 0.55:
            strength = min(abs(prior_trend) * 10 + flow_ratio * 0.5 + (0.15 if vol_declining else 0), 1.0)
            st.reaccumulation_score = max(st.reaccumulation_score, strength * 0.8)

        # Redistribution: uptrend + consolidation + net selling
        elif prior_trend > 0.005 and flow_ratio < 0.45:
            strength = min(abs(prior_trend) * 10 + (1 - flow_ratio) * 0.5 + (0.15 if vol_declining else 0), 1.0)
            st.redistribution_score = max(st.redistribution_score, strength * 0.8)

        # Decay
        st.reaccumulation_score *= 0.995
        st.redistribution_score *= 0.995

    def update_external_signals(
        self, symbol: str,
        sweep_confidence: float = 0,
        absorption_confidence: float = 0,
        iceberg_confidence: float = 0,
        liquidity_pool_score: float = 0,
    ) -> None:
        """Receive confidence scores from external detectors for strength computation."""
        st = self._states.setdefault(symbol, SmartMoneyState(symbol=symbol))
        # Exponential moving average to smooth external signals
        alpha = 0.3
        st.sweep_confidence = alpha * sweep_confidence + (1 - alpha) * st.sweep_confidence
        st.absorption_confidence = alpha * absorption_confidence + (1 - alpha) * st.absorption_confidence
        st.iceberg_confidence = alpha * iceberg_confidence + (1 - alpha) * st.iceberg_confidence
        st.liquidity_pool_score = alpha * liquidity_pool_score + (1 - alpha) * st.liquidity_pool_score

    def _compute_strength_score(self, st: SmartMoneyState) -> float:
        """
        Compute Smart Money Strength Score (0-100) from all detection sources.
        
        Components:
        - Accumulation/Distribution signal: 25%
        - Stealth pattern activity: 15%
        - Hidden order depth: 10%
        - Reaccumulation/Redistribution: 15%
        - Sweep detection: 10%
        - Absorption detection: 10%
        - Iceberg detection: 10%
        - Liquidity pool activity: 5%
        """
        score = 0.0

        # 1. Accumulation/Distribution signal (0-25)
        ad_signal = max(st.accumulation_score, st.distribution_score)
        score += ad_signal * 25

        # 2. Stealth pattern activity (0-15)
        stealth_activity = min((st.stealth_buys + st.stealth_sells) / 20, 1.0)
        score += stealth_activity * 15

        # 3. Hidden order depth (0-10)
        ho_depth = min(st.hidden_order_depth / 10, 1.0)
        score += ho_depth * 10

        # 4. Reaccumulation/Redistribution (0-15)
        reaccum_redistrib = max(st.reaccumulation_score, st.redistribution_score)
        score += reaccum_redistrib * 15

        # 5. Sweep detection (0-10)
        score += st.sweep_confidence * 10

        # 6. Absorption detection (0-10)
        score += st.absorption_confidence * 10

        # 7. Iceberg detection (0-10)
        score += st.iceberg_confidence * 10

        # 8. Liquidity pool activity (0-5)
        score += st.liquidity_pool_score * 5

        # 9. Institutional flow magnitude boost (0-10)
        if st.institutional_flow != 0:
            flow_magnitude = min(abs(st.institutional_flow) / 1e9, 1.0)  # scale by $1B
            score += flow_magnitude * 10

        return min(round(score, 1), 100)

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        st = self._states.get(symbol)
        if not st:
            return None

        # Compute strength score
        st.smart_money_strength = self._compute_strength_score(st)

        # Determine primary signal (lowered thresholds for better detection)
        signals = []
        if st.accumulation_score > 0.2:
            signals.append("accumulation")
        if st.distribution_score > 0.2:
            signals.append("distribution")
        if st.reaccumulation_score > 0.2:
            signals.append("reaccumulation")
        if st.redistribution_score > 0.2:
            signals.append("redistribution")
        if st.sweep_confidence > 0.3:
            signals.append("sweep_active")
        if st.absorption_confidence > 0.3:
            signals.append("absorption_active")
        if st.iceberg_confidence > 0.3:
            signals.append("iceberg_active")
        # Flow-based signal: if institutional flow is strong, add flow signal
        if abs(st.institutional_flow) > 1e6:  # > $1M net flow
            flow_dir = "buying" if st.institutional_flow > 0 else "selling"
            signals.append(f"flow_{flow_dir}")

        # Strength level (adjusted thresholds)
        if st.smart_money_strength >= 60:
            strength_level = "strong"
        elif st.smart_money_strength >= 30:
            strength_level = "moderate"
        else:
            strength_level = "weak"

        # Smart money side: use DOMINANT direction from scores + flow
        if st.accumulation_score > 0.2 and st.accumulation_score > st.distribution_score * 1.2:
            sm_side = "accumulating"
        elif st.distribution_score > 0.2 and st.distribution_score > st.accumulation_score * 1.2:
            sm_side = "distributing"
        elif st.accumulation_score > 0.3 and st.distribution_score <= 0.3:
            sm_side = "accumulating"
        elif st.distribution_score > 0.3 and st.accumulation_score <= 0.3:
            sm_side = "distributing"
        elif st.institutional_flow > 1e7:  # > $10M net buy
            sm_side = "accumulating"
        elif st.institutional_flow < -1e7:  # > $10M net sell
            sm_side = "distributing"
        else:
            sm_side = "neutral"

        return {
            "symbol": symbol,
            # Existing fields (backward compatible)
            "accumulation_score": round(st.accumulation_score, 4),
            "distribution_score": round(st.distribution_score, 4),
            "stealth_buys": st.stealth_buys,
            "stealth_sells": st.stealth_sells,
            "hidden_orders": st.hidden_orders[-5:],
            "institutional_flow": st.institutional_flow,
            "smart_money_side": sm_side,
            # Extended fields
            "reaccumulation_score": round(st.reaccumulation_score, 4),
            "redistribution_score": round(st.redistribution_score, 4),
            "hidden_order_depth": st.hidden_order_depth,
            "liquidity_pool_score": round(st.liquidity_pool_score, 4),
            "sweep_confidence": round(st.sweep_confidence, 4),
            "absorption_confidence": round(st.absorption_confidence, 4),
            "iceberg_confidence": round(st.iceberg_confidence, 4),
            # Strength score
            "smart_money_strength": st.smart_money_strength,
            "strength_level": strength_level,
            "active_signals": signals,
            "signal_count": len(signals),
        }
