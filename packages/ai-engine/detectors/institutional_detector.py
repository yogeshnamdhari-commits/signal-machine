"""
Institutional Activity Probability Detector

Outputs a **probability** (0–1) that institutional players are actively
participating in a given symbol, based on orderbook and trade-flow
evidence.  Instead of claiming "institutional score = 85", we state
"P(institutional activity) = 0.71 ± confidence".

Methodology
-----------
1. **Order book depth concentration** — institutional players place large
   resting orders.  High depth at few levels vs. spread-out retail
   liquidity → likelihood ratio.
2. **Large order frequency** — count of trades above a configurable
   institutional threshold (default $5K) as fraction of total.
3. **Absorption detection** — high volume at a stable price level
   indicates passive institutional absorption.
4. **Iceberg pattern evidence** — repeated refills at same level suggest
   hidden institutional orders.
5. **Bayesian combination** — sub-signals combined via log-odds addition
   and softmax.

All outputs are **probabilities**, not scores.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger


# ── Configuration ────────────────────────────────────────────────
INSTITUTIONAL_ORDER_USD = 5_000      # minimum for "institutional" trade
_LARGE_ORDER_USD = 20_000            # definitely large
_PRIOR_INST = 0.20                   # prior: 20% chance of institutional activity
_PRIOR_NONE = 0.80                   # prior: 80% chance of no institutional activity
_DEPTH_IMBALANCE_THRESHOLD = 3.0     # depth ratio for "concentrated"
_ABSORPTION_VOLUME_THRESHOLD = 50_000
_EVIDENCE_DECAY = 0.98
_MIN_TRADES_FOR_ANALYSIS = 10
_PRICE_STABILITY_THRESHOLD = 0.005   # 0.5% range


# ── State ────────────────────────────────────────────────────────
@dataclass
class _SymbolState:
    trades: List[Dict] = field(default_factory=list)
    bid_depth_history: List[List[Tuple[float, float]]] = field(default_factory=list)
    ask_depth_history: List[List[Tuple[float, float]]] = field(default_factory=list)
    # Evidence (log-odds)
    _evidence_inst: float = 0.0
    _evidence_none: float = 0.0
    _trade_count: int = 0
    _large_order_count: int = 0
    _absorption_events: int = 0
    _iceberg_events: int = 0
    _depth_concentration_events: int = 0
    _last_update: float = 0.0
    # Price/volume tracking
    _price_history: List[float] = field(default_factory=list)
    _volume_history: List[float] = field(default_factory=list)
    _level_volumes: Dict[float, float] = field(default_factory=dict)


class InstitutionalProbabilityDetector:
    """
    Computes P(institutional activity) for each symbol using Bayesian
    inference over orderbook and trade-flow evidence.

    Usage::

        detector = InstitutionalProbabilityDetector()
        await detector.process_trade("BTCUSDT", trade_data)
        await detector.process_orderbook("BTCUSDT", {"bids": [...], "asks": [...]})
        result = detector.get_probability("BTCUSDT")
        # result = {
        #     "institutional_probability": 0.71,
        #     "none_probability": 0.29,
        #     "confidence": 0.55,
        #     "evidence_count": 45,
        #     "dominant_signal": "institutional",
        #     "sub_signals": { ... },
        # }
    """

    def __init__(self) -> None:
        self._states: Dict[str, _SymbolState] = {}

    async def initialize(self) -> None:
        logger.info("InstitutionalProbabilityDetector ready")

    # ── Public API ───────────────────────────────────────────────

    async def process_trade(self, symbol: str, trade: Dict) -> None:
        """Ingest a trade and update institutional probability."""
        st = self._states.setdefault(symbol, _SymbolState())
        price = float(trade.get("price", 0))
        qty = float(trade.get("quantity", 0))
        val = price * qty
        ts = float(trade.get("trade_time", time.time()))

        st.trades.append({
            "price": price, "qty": qty, "value": val,
            "is_maker": bool(trade.get("is_buyer_maker", False)),
            "ts": ts,
        })
        if len(st.trades) > 3000:
            st.trades = st.trades[-1500:]

        st._trade_count += 1
        st._price_history.append(price)
        st._volume_history.append(val)
        if len(st._price_history) > 300:
            st._price_history = st._price_history[-300:]
            st._volume_history = st._volume_history[-300:]

        # Track volume at each price level
        bucket = round(price, 4)
        st._level_volumes[bucket] = st._level_volumes.get(bucket, 0) + val

        # Large order evidence
        if val >= INSTITUTIONAL_ORDER_USD:
            st._large_order_count += 1
            # Strong evidence for institutional activity
            lr = np.log(2.0) if val < _LARGE_ORDER_USD else np.log(3.5)
            st._evidence_inst += lr

        # Very small trades → evidence against institutional activity
        if val < 100:
            st._evidence_none += 0.05

        # Evidence decay: prevent saturation
        st._evidence_inst *= _EVIDENCE_DECAY
        st._evidence_none *= _EVIDENCE_DECAY

        st._last_update = ts

    async def process_orderbook(self, symbol: str, book: Dict) -> None:
        """Ingest orderbook snapshot and update institutional probability."""
        st = self._states.setdefault(symbol, _SymbolState())

        bids = [(float(p), float(q)) for p, q in book.get("bids", [])[:20]]
        asks = [(float(p), float(q)) for p, q in book.get("asks", [])[:20]]

        st.bid_depth_history.append(bids)
        st.ask_depth_history.append(asks)
        if len(st.bid_depth_history) > 50:
            st.bid_depth_history = st.bid_depth_history[-50:]
            st.ask_depth_history = st.ask_depth_history[-50:]

        if len(st.bid_depth_history) < 3:
            return

        # Depth concentration analysis
        self._analyze_depth_concentration(symbol, st)

        # Absorption detection
        self._analyze_absorption(symbol, st)

        # Iceberg detection
        self._analyze_iceberg(symbol, st)

    def get_probability(self, symbol: str) -> Dict:
        """Return current probability of institutional activity."""
        st = self._states.get(symbol)
        if not st or st._trade_count < _MIN_TRADES_FOR_ANALYSIS:
            return self._empty_result(symbol)

        inst_prob, none_prob = self._evidence_to_prob(st)

        # Confidence
        evidence_count = st._trade_count
        confidence = min(1.0, evidence_count / 150)
        agreement = self._compute_agreement(st)
        confidence = min(1.0, confidence * (0.6 + 0.4 * agreement))

        dominant = "institutional" if inst_prob > none_prob else "none"

        return {
            "symbol": symbol,
            "institutional_probability": round(inst_prob, 4),
            "none_probability": round(none_prob, 4),
            "confidence": round(confidence, 4),
            "evidence_count": evidence_count,
            "dominant_signal": dominant,
            "sub_signals": self._get_sub_signals(st),
            "timestamp": time.time(),
        }

    def get_all_probabilities(self) -> Dict[str, Dict]:
        """Return probabilities for all tracked symbols."""
        return {sym: self.get_probability(sym) for sym in self._states}

    # ── Internal: Orderbook analysis ─────────────────────────────

    def _analyze_depth_concentration(self, symbol: str, st: _SymbolState) -> None:
        """Institutional orders concentrate depth at few levels."""
        try:
            for side_label, hist in [("bid", st.bid_depth_history), ("ask", st.ask_depth_history)]:
                if len(hist) < 3:
                    continue

                recent = hist[-1]
                if not recent:
                    continue

                total_depth = sum(float(qty) for _, qty in recent)
                if total_depth <= 0:
                    continue

                top_3_vol = sum(float(qty) for _, qty in recent[:3])
                concentration = top_3_vol / total_depth

                if len(hist) >= 5:
                    hist_concentrations = []
                    for snap in hist[-5:]:
                        h_total = sum(float(qty) for _, qty in snap)
                        if h_total > 0:
                            h_top3 = sum(float(qty) for _, qty in snap[:3])
                            hist_concentrations.append(h_top3 / h_total)

                    avg_concentration = float(np.mean(hist_concentrations)) if hist_concentrations else 0.5
                    if concentration > avg_concentration * 1.5 and concentration > 0.4:
                        st._depth_concentration_events += 1
                        st._evidence_inst += 0.4
                    elif concentration < avg_concentration * 0.7:
                        st._evidence_none += 0.2
                else:
                    if concentration > 0.6:
                        st._depth_concentration_events += 1
                        st._evidence_inst += 0.3
        except Exception:
            pass

    def _analyze_absorption(self, symbol: str, st: _SymbolState) -> None:
        """
        Absorption: high volume at a stable price level over multiple
        orderbook snapshots → passive institutional order.
        """
        try:
            for side_label, hist in [("bid", st.bid_depth_history), ("ask", st.ask_depth_history)]:
                if len(hist) < 10:
                    continue

                level_snapshots: Dict[float, List[float]] = {}
                for snap in hist[-10:]:
                    for price, qty in snap[:10]:
                        bucket = round(float(price), 4)
                        level_snapshots.setdefault(bucket, []).append(float(qty))

                for level, vols in level_snapshots.items():
                    if len(vols) < 5:
                        continue
                    total_vol = sum(vols)
                    if total_vol > _ABSORPTION_VOLUME_THRESHOLD:
                        prices_near = [float(p) for p in st._price_history[-30:]
                                       if isinstance(p, (int, float)) and abs(float(p) - level) / max(level, 0.0001) < 0.002]
                        if len(prices_near) >= 5:
                            price_stability = 1 - (max(prices_near) - min(prices_near)) / max(prices_near, 0.0001)
                            if price_stability > 0.995:
                                st._absorption_events += 1
                                st._evidence_inst += 0.5
                                break
        except Exception:
            pass

    def _analyze_iceberg(self, symbol: str, st: _SymbolState) -> None:
        """
        Iceberg detection: repeated refills at the same price level
        suggest hidden institutional orders.
        """
        try:
            if len(st.bid_depth_history) < 5:
                return

            for side_label, hist in [("bid", st.bid_depth_history), ("ask", st.ask_depth_history)]:
                level_volumes: Dict[float, List[float]] = {}
                for snap in hist[-5:]:
                    for price, qty in snap[:5]:
                        level_volumes.setdefault(round(float(price), 4), []).append(float(qty))

                for level, vols in level_volumes.items():
                    if len(vols) >= 4:
                        avg = float(np.mean(vols))
                        std = float(np.std(vols)) if len(vols) > 1 else 0
                        if avg > 0 and std / max(avg, 0.001) < 0.3 and avg > 1_000:
                            st._iceberg_events += 1
                            st._evidence_inst += 0.6
                            break
        except Exception:
            pass

    # ── Internal: Probability computation ────────────────────────

    def _evidence_to_prob(self, st: _SymbolState) -> Tuple[float, float]:
        """Convert log-odds evidence to probabilities via softmax with priors."""
        log_prior_inst = np.log(_PRIOR_INST)
        log_prior_none = np.log(_PRIOR_NONE)

        log_inst = log_prior_inst + st._evidence_inst
        log_none = log_prior_none + st._evidence_none

        # Replace any NaN/Inf
        if not np.isfinite(log_inst) or not np.isfinite(log_none):
            return _PRIOR_INST, _PRIOR_NONE

        # Clamp
        max_log = max(log_inst, log_none)
        log_inst -= max_log
        log_none -= max_log

        exp_inst = np.exp(log_inst)
        exp_none = np.exp(log_none)
        total = exp_inst + exp_none

        if total <= 0 or not np.isfinite(total):
            return _PRIOR_INST, _PRIOR_NONE

        return exp_inst / total, exp_none / total

    def _compute_agreement(self, st: _SymbolState) -> float:
        """How much do the sub-signals agree on institutional presence."""
        evidence = [st._evidence_inst, st._evidence_none]
        max_ev = max(evidence)
        if max_ev <= 0:
            return 0.0
        others = sum(e for e in evidence if e != max_ev)
        if others <= 0:
            return 1.0
        return min(1.0, max_ev / (max_ev + abs(others)))

    def _get_sub_signals(self, st: _SymbolState) -> Dict:
        """Breakdown of sub-signal contributions."""
        return {
            "large_order_count": st._large_order_count,
            "absorption_events": st._absorption_events,
            "iceberg_events": st._iceberg_events,
            "depth_concentration_events": st._depth_concentration_events,
            "evidence_inst": round(st._evidence_inst, 3),
            "evidence_none": round(st._evidence_none, 3),
        }

    def _empty_result(self, symbol: str) -> Dict:
        """Return neutral probability when insufficient data."""
        return {
            "symbol": symbol,
            "institutional_probability": 0.0,
            "none_probability": 1.0,
            "confidence": 0.0,
            "evidence_count": 0,
            "dominant_signal": "none",
            "sub_signals": {},
            "timestamp": time.time(),
        }
