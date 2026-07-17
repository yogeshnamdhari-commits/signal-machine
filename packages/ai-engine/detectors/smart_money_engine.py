"""
Smart Money Engine — Accumulation / Distribution Probability Detector

Outputs a **probability** (0–1) that smart money is accumulating or
distributing, with a confidence interval reflecting how much evidence
has been gathered.  This replaces the old deterministic score with an
explicit "we think there is a X% chance of accumulation" statement.

Methodology
-----------
1. **Stealth clustering** — many small trades at the same price level
   suggests hidden intent.  Count stealth clusters vs. expected random
   distribution → likelihood ratio → prior-updated probability.
2. **Volume-weighted directional flow** — net taker volume over a rolling
   window, normalised by total volume → directional probability.
3. **Price stability during high volume** — accumulation/distribution
   often happens while price stays stable despite heavy volume.
4. **Bayesian combination** — independent sub-signals are combined via
   log-odds addition (naïve Bayes) and then converted back to
   probability.

All scores are **probabilities**, not raw counts.  A value of 0.73 means
"73 % probability that smart money is currently accumulating".
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger


# ── Configuration ────────────────────────────────────────────────
LARGE_ORDER_USD = 5_000
_CLUSTER_TOLERANCE = 0.002          # 0.2 % price bucket
_MIN_TRADES = 3                      # minimum to detect pattern
_PRIOR_ACCUM = 0.15                  # prior probability of accumulation
_PRIOR_DISTRIB = 0.15               # prior probability of distribution
_PRIOR_NEUTRAL = 0.70               # prior probability of neutral
_EVIDENCE_DECAY = 0.97              # exponential decay per tick
_MIN_EVIDENCE_FOR_PROB = 5          # need this many data points
_MIN_TRADE_VALUE = 50               # ignore micro-dust trades


# ── State ────────────────────────────────────────────────────────
@dataclass
class _SymbolState:
    trades: List[Dict] = field(default_factory=list)
    # Accumulated evidence (log-odds space)
    _evidence_accum: float = 0.0
    _evidence_distrib: float = 0.0
    _evidence_neutral: float = 0.0
    _trade_count: int = 0
    _last_update: float = 0.0
    # Running stats for normalisation
    _total_volume: float = 0.0
    _buy_volume: float = 0.0
    _sell_volume: float = 0.0
    _stealth_buy_events: int = 0
    _stealth_sell_events: int = 0
    _high_vol_stable_events: int = 0
    _price_history: List[float] = field(default_factory=list)
    _volume_history: List[float] = field(default_factory=list)
    _flow_history: List[float] = field(default_factory=list)


class SmartMoneyProbabilityDetector:
    """
    Computes P(accumulation), P(distribution), and P(neutral) for each
    symbol using Bayesian inference over multiple observable signals.

    Usage::

        detector = SmartMoneyProbabilityDetector()
        await detector.process_trade("BTCUSDT", {"price": 100, "quantity": 0.5, ...})
        result = detector.get_probability("BTCUSDT")
        # result = {
        #     "accumulation_probability": 0.62,
        #     "distribution_probability": 0.08,
        #     "neutral_probability": 0.30,
        #     "confidence": 0.45,          # how much evidence we have
        #     "evidence_count": 12,
        #     "dominant_signal": "accumulation",
        #     "sub_signals": { ... },
        # }
    """

    def __init__(self) -> None:
        self._states: Dict[str, _SymbolState] = {}
        self._median_trade_values: Dict[str, float] = {}

    async def initialize(self) -> None:
        logger.info("SmartMoneyProbabilityDetector ready")

    # ── Public API ───────────────────────────────────────────────

    async def process_trade(self, symbol: str, trade: Dict) -> None:
        """Ingest a single trade and update probability estimates."""
        st = self._states.setdefault(symbol, _SymbolState())
        price = float(trade.get("price", 0))
        qty = float(trade.get("quantity", 0))
        val = price * qty
        is_maker = bool(trade.get("is_buyer_maker", False))
        ts = float(trade.get("trade_time", time.time()))

        if val < _MIN_TRADE_VALUE:
            return

        st.trades.append({
            "price": price, "qty": qty, "value": val,
            "is_maker": is_maker, "ts": ts,
        })
        if len(st.trades) > 3000:
            st.trades = st.trades[-1500:]

        # Running volume stats
        st._total_volume += val
        direction = -1 if is_maker else 1
        if not is_maker:
            st._buy_volume += val
        else:
            st._sell_volume += val
        st._trade_count += 1

        # Price/volume/flow history
        st._price_history.append(price)
        st._volume_history.append(val)
        st._flow_history.append(direction * val)
        if len(st._price_history) > 300:
            st._price_history = st._price_history[-300:]
            st._volume_history = st._volume_history[-300:]
            st._flow_history = st._flow_history[-300:]

        # Adaptive median
        if len(st.trades) >= 10:
            vals = sorted([t["value"] for t in st.trades[-100:]])
            self._median_trade_values[symbol] = vals[len(vals) // 2]

        # Update sub-signals
        self._update_stealth_evidence(symbol, st)
        self._update_flow_evidence(symbol, st)
        self._update_stability_evidence(symbol, st)

        # Evidence decay: prevent saturation at 0% or 100%
        st._evidence_accum *= _EVIDENCE_DECAY
        st._evidence_distrib *= _EVIDENCE_DECAY
        st._evidence_neutral *= _EVIDENCE_DECAY

        st._last_update = ts

    def get_probability(self, symbol: str) -> Dict:
        """
        Return current probability estimate for accumulation / distribution.

        Returns dict with probabilities, confidence, and sub-signal breakdown.
        """
        st = self._states.get(symbol)
        if not st or st._trade_count < _MIN_TRADES:
            return self._empty_result(symbol)

        # Convert log-odds evidence to probabilities via softmax
        accum_prob, distrib_prob, neutral_prob = self._evidence_to_prob(st)

        # Confidence: based on how much evidence we have (0–1)
        evidence_count = st._trade_count
        confidence = min(1.0, evidence_count / 100)  # full confidence at 100+ trades
        # Boost confidence when sub-signals agree
        agreement = self._compute_agreement(st)
        confidence = min(1.0, confidence * (0.7 + 0.3 * agreement))

        # Dominant signal
        probs = {"accumulation": accum_prob, "distribution": distrib_prob, "neutral": neutral_prob}
        dominant = max(probs, key=probs.get)

        # Sub-signal breakdown for transparency
        sub_signals = self._get_sub_signals(st)

        return {
            "symbol": symbol,
            "accumulation_probability": round(accum_prob, 4),
            "distribution_probability": round(distrib_prob, 4),
            "neutral_probability": round(neutral_prob, 4),
            "confidence": round(confidence, 4),
            "evidence_count": evidence_count,
            "dominant_signal": dominant,
            "sub_signals": sub_signals,
            "timestamp": time.time(),
        }

    def get_all_probabilities(self) -> Dict[str, Dict]:
        """Return probabilities for all tracked symbols."""
        return {sym: self.get_probability(sym) for sym in self._states}

    # ── Internal: Sub-signal evidence ────────────────────────────

    def _update_stealth_evidence(self, symbol: str, st: _SymbolState) -> None:
        """
        Stealth clustering evidence:
        Many small trades at the same price → likely hidden accumulation/distribution.
        Likelihood ratio: P(observation | stealth) / P(observation | noise).
        """
        if len(st.trades) < _MIN_TRADES:
            return

        recent = st.trades[-50:]
        if not recent:
            return

        ref_price = recent[-1]["price"]
        if ref_price <= 0:
            return
        bucket_size = max(ref_price * _CLUSTER_TOLERANCE, 0.0001)

        # Bucket trades by price
        buckets: Dict[float, List[Dict]] = {}
        for t in recent:
            bucket = round(t["price"] / bucket_size) * bucket_size
            buckets.setdefault(bucket, []).append(t)

        median_val = self._median_trade_values.get(symbol, 100)
        small_threshold = median_val * 0.25

        for _bucket, trades in buckets.items():
            if len(trades) < _MIN_TRADES:
                continue

            small = [t for t in trades if t["value"] < small_threshold]
            if len(small) < 3:
                continue

            buys = sum(1 for t in small if not t["is_maker"])
            sells = sum(1 for t in small if t["is_maker"])

            # Likelihood ratio for stealth accumulation
            if buys > sells * 1.2:
                # Strong evidence for accumulation
                lr = np.log(max((buys / max(sells, 1)) * 1.5, 0.01))  # log likelihood ratio
                st._evidence_accum += lr
                st._stealth_buy_events += 1
            elif sells > buys * 1.2:
                # Strong evidence for distribution
                lr = np.log(max((sells / max(buys, 1)) * 1.5, 0.01))
                st._evidence_distrib += lr
                st._stealth_sell_events += 1
            else:
                # Mixed — slight evidence for neutral
                st._evidence_neutral += 0.05

    def _update_flow_evidence(self, symbol: str, st: _SymbolState) -> None:
        """
        Volume-weighted directional flow:
        Net taker volume as fraction of total → directional probability.
        Large imbalances are strong evidence.
        """
        try:
            if len(st._flow_history) < 20:
                return

            # Filter to only numeric values
            flow_vals = []
            for f in st._flow_history[-50:]:
                try:
                    flow_vals.append(float(f))
                except (TypeError, ValueError):
                    continue
            if len(flow_vals) < 10:
                return

            window = np.array(flow_vals, dtype=float)
            total_vol = float(np.sum(np.abs(window)))
            if total_vol <= 0:
                return

            net_flow = float(np.sum(window))
            buy_flow = float(np.sum(window[window > 0]))
            sell_flow = float(np.abs(np.sum(window[window < 0])))

            # Directional ratio: 0.5 = balanced, >0.5 = buy dominant
            buy_ratio = buy_flow / total_vol

            # Convert to log-odds evidence
            buy_ratio = np.clip(buy_ratio, 0.01, 0.99)  # prevent log(0)
            if buy_ratio > 0.55:
                lr = np.log(buy_ratio / (1 - buy_ratio)) * 0.5
                st._evidence_accum += lr
            elif buy_ratio < 0.45:
                lr = np.log((1 - buy_ratio) / buy_ratio) * 0.5
                st._evidence_distrib += lr
            else:
                st._evidence_neutral += 0.02
        except Exception:
            pass  # silently skip on type errors

    def _update_stability_evidence(self, symbol: str, st: _SymbolState) -> None:
        """
        Price stability during high volume:
        When volume is high but price barely moves, it suggests large
        passive orders absorbing aggressive flow → institutional activity.
        """
        try:
            if len(st._price_history) < 30 or len(st._volume_history) < 30:
                return

            # Filter to only numeric values
            price_vals = []
            for p in st._price_history[-50:]:
                try:
                    price_vals.append(float(p))
                except (TypeError, ValueError):
                    continue
            vol_vals = []
            for v in st._volume_history[-50:]:
                try:
                    vol_vals.append(float(v))
                except (TypeError, ValueError):
                    continue
            if len(price_vals) < 20 or len(vol_vals) < 20:
                return

            prices = np.array(price_vals, dtype=float)
            volumes = np.array(vol_vals, dtype=float)

            price_range = (prices.max() - prices.min()) / prices.mean() if prices.mean() > 0 else 1
            avg_vol = float(volumes.mean())
            recent_vol = float(volumes[-10:].mean()) if len(volumes) >= 10 else avg_vol

            # High volume + tight range = absorption / stealth activity
            if price_range < 0.005 and recent_vol > avg_vol * 1.3:
                st._high_vol_stable_events += 1
                # Direction depends on flow
                net = sum(float(f) for f in st._flow_history[-20:] if isinstance(f, (int, float)))
                if net > 0:
                    st._evidence_accum += 0.3
                elif net < 0:
                    st._evidence_distrib += 0.3
                else:
                    st._evidence_neutral += 0.1
        except Exception:
            pass  # silently skip on type errors

    # ── Internal: Probability computation ────────────────────────

    def _evidence_to_prob(self, st: _SymbolState) -> Tuple[float, float, float]:
        """
        Convert accumulated log-odds evidence to probabilities via
        softmax with priors.
        """
        # Apply priors (in log space)
        log_prior_accum = np.log(_PRIOR_ACCUM)
        log_prior_distrib = np.log(_PRIOR_DISTRIB)
        log_prior_neutral = np.log(_PRIOR_NEUTRAL)

        # Total evidence = prior + accumulated
        log_accum = log_prior_accum + st._evidence_accum
        log_distrib = log_prior_distrib + st._evidence_distrib
        log_neutral = log_prior_neutral + st._evidence_neutral

        # Replace any NaN/Inf with prior values
        for v in [log_accum, log_distrib, log_neutral]:
            if not np.isfinite(v):
                log_accum = log_prior_accum
                log_distrib = log_prior_distrib
                log_neutral = log_prior_neutral
                break

        # Clamp to prevent overflow
        max_log = max(log_accum, log_distrib, log_neutral)
        log_accum -= max_log
        log_distrib -= max_log
        log_neutral -= max_log

        # Softmax
        exp_accum = np.exp(log_accum)
        exp_distrib = np.exp(log_distrib)
        exp_neutral = np.exp(log_neutral)
        total = exp_accum + exp_distrib + exp_neutral

        if total <= 0 or not np.isfinite(total):
            return _PRIOR_ACCUM, _PRIOR_DISTRIB, _PRIOR_NEUTRAL

        return exp_accum / total, exp_distrib / total, exp_neutral / total

    def _compute_agreement(self, st: _SymbolState) -> float:
        """
        How much do the sub-signals agree?  1.0 = full agreement, 0 = conflict.
        """
        evidence = [st._evidence_accum, st._evidence_distrib, st._evidence_neutral]
        max_ev = max(evidence)
        if max_ev <= 0:
            return 0.0
        # Ratio of strongest vs sum of others
        others = sum(e for e in evidence if e != max_ev)
        if others <= 0:
            return 1.0
        return min(1.0, max_ev / (max_ev + abs(others)))

    def _get_sub_signals(self, st: _SymbolState) -> Dict:
        """Breakdown of sub-signal contributions."""
        return {
            "stealth_buy_events": st._stealth_buy_events,
            "stealth_sell_events": st._stealth_sell_events,
            "high_vol_stable_events": st._high_vol_stable_events,
            "total_volume": round(st._total_volume, 2),
            "buy_volume": round(st._buy_volume, 2),
            "sell_volume": round(st._sell_volume, 2),
            "evidence_accum": round(st._evidence_accum, 3),
            "evidence_distrib": round(st._evidence_distrib, 3),
            "evidence_neutral": round(st._evidence_neutral, 3),
        }

    def _empty_result(self, symbol: str) -> Dict:
        """Return neutral probability when insufficient data."""
        return {
            "symbol": symbol,
            "accumulation_probability": 0.0,
            "distribution_probability": 0.0,
            "neutral_probability": 1.0,
            "confidence": 0.0,
            "evidence_count": 0,
            "dominant_signal": "neutral",
            "sub_signals": {},
            "timestamp": time.time(),
        }
