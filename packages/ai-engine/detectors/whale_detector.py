"""
Whale Activity Probability Detector

Outputs a **probability** (0–1) that "whale" (large player) activity
is present in a given symbol, with explicit uncertainty quantification.

Instead of claiming "whale_activity = true", we state
"P(whale activity) = 0.68 with 0.45 confidence".

A "whale" is defined as any single entity capable of moving the market
with individual trades above a configurable USD threshold.

Methodology
-----------
1. **Large trade detection** — trades above the whale threshold
   (default $20K) are flagged.  Frequency of such trades → evidence.
2. **Trade size distribution** — the ratio of large trades to total
   trades.  A bimodal distribution (many small + few very large)
   suggests whale presence.
3. **Volume spike detection** — sudden increases in volume above
   rolling average → likely whale activity.
4. **Price impact correlation** — large trades that cause measurable
   price movement → active whale.
5. **Bayesian combination** — sub-signals combined via log-odds.

All outputs are **probabilities**, not binary flags.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np
from loguru import logger


# ── Configuration ────────────────────────────────────────────────
WHALE_THRESHOLD_USD = 20_000         # trade value to count as "whale"
MEGAWHALE_THRESHOLD_USD = 100_000    # very large trade
_VOLUME_SPIKE_MULTIPLIER = 2.0       # 2x average = spike
_PRIOR_WHALE = 0.15                  # prior probability of whale activity
_PRIOR_NONE = 0.85
_EVIDENCE_DECAY = 0.98
_MIN_TRADES_FOR_ANALYSIS = 10
_PRICE_IMPACT_THRESHOLD = 0.001      # 0.1% price move after large trade


# ── State ────────────────────────────────────────────────────────
@dataclass
class _SymbolState:
    trades: List[Dict] = field(default_factory=list)
    # Evidence (log-odds)
    _evidence_whale: float = 0.0
    _evidence_none: float = 0.0
    _trade_count: int = 0
    _whale_trade_count: int = 0
    _megawhale_count: int = 0
    _volume_spike_events: int = 0
    _price_impact_events: int = 0
    _bimodal_score: float = 0.0      # trade size distribution bimodality
    _last_update: float = 0.0
    # Running stats
    _total_volume: float = 0.0
    _whale_volume: float = 0.0
    _volume_history: List[float] = field(default_factory=list)
    _price_history: List[float] = field(default_factory=list)
    _trade_values: List[float] = field(default_factory=list)


class WhaleProbabilityDetector:
    """
    Computes P(whale activity) for each symbol using Bayesian inference
    over trade size distribution and volume patterns.

    Usage::

        detector = WhaleProbabilityDetector()
        await detector.process_trade("BTCUSDT", trade_data)
        result = detector.get_probability("BTCUSDT")
        # result = {
        #     "whale_probability": 0.68,
        #     "none_probability": 0.32,
        #     "confidence": 0.45,
        #     "evidence_count": 80,
        #     "dominant_signal": "whale",
        #     "sub_signals": { ... },
        # }
    """

    def __init__(self) -> None:
        self._states: Dict[str, _SymbolState] = {}

    async def initialize(self) -> None:
        logger.info("WhaleProbabilityDetector ready")

    # ── Public API ───────────────────────────────────────────────

    async def process_trade(self, symbol: str, trade: Dict) -> None:
        """Ingest a trade and update whale probability."""
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
        st._total_volume += val
        st._trade_values.append(val)
        st._price_history.append(price)
        st._volume_history.append(val)

        if len(st._trade_values) > 500:
            st._trade_values = st._trade_values[-500:]
        if len(st._price_history) > 500:
            st._price_history = st._price_history[-500:]
            st._volume_history = st._volume_history[-500:]

        # ── Evidence decay: prevent saturation ──
        st._evidence_whale *= _EVIDENCE_DECAY
        st._evidence_none *= _EVIDENCE_DECAY

        # ── Sub-signal: large trade detection ──
        if val >= WHALE_THRESHOLD_USD:
            st._whale_trade_count += 1
            st._whale_volume += val

            # Stronger evidence for larger trades
            if val >= MEGAWHALE_THRESHOLD_USD:
                st._megawhale_count += 1
                st._evidence_whale += np.log(4.0)  # very strong evidence
            else:
                st._evidence_whale += np.log(2.0)

            # Check if this trade caused price impact
            self._check_price_impact(symbol, st, price, ts)
        else:
            # Small trades are weak evidence against whale activity
            if val < 500:
                st._evidence_none += 0.02

        # ── Sub-signal: volume spike detection ──
        self._check_volume_spike(symbol, st)

        # ── Sub-signal: trade size distribution bimodality ──
        self._update_bimodality(symbol, st)

        st._last_update = ts

    def get_probability(self, symbol: str) -> Dict:
        """Return current probability of whale activity."""
        st = self._states.get(symbol)
        if not st or st._trade_count < _MIN_TRADES_FOR_ANALYSIS:
            return self._empty_result(symbol)

        whale_prob, none_prob = self._evidence_to_prob(st)

        # Confidence
        confidence = min(1.0, st._trade_count / 150)
        agreement = self._compute_agreement(st)
        confidence = min(1.0, confidence * (0.6 + 0.4 * agreement))

        # Whale activity ratio
        whale_ratio = st._whale_volume / max(st._total_volume, 1)

        dominant = "whale" if whale_prob > none_prob else "none"

        return {
            "symbol": symbol,
            "whale_probability": round(whale_prob, 4),
            "none_probability": round(none_prob, 4),
            "confidence": round(confidence, 4),
            "evidence_count": st._trade_count,
            "dominant_signal": dominant,
            "sub_signals": self._get_sub_signals(st),
            "timestamp": time.time(),
        }

    def get_all_probabilities(self) -> Dict[str, Dict]:
        """Return probabilities for all tracked symbols."""
        return {sym: self.get_probability(sym) for sym in self._states}

    # ── Internal: Sub-signal analysis ────────────────────────────

    def _check_price_impact(self, symbol: str, st: _SymbolState, trigger_price: float, ts: float) -> None:
        """Did a large trade cause measurable price movement?"""
        try:
            if len(st._price_history) < 5:
                return
            post_prices = [float(p) for p in st._price_history[-10:] if isinstance(p, (int, float)) and float(p) != trigger_price]
            if not post_prices:
                return
            avg_post = float(np.mean(post_prices))
            impact = abs(avg_post - trigger_price) / max(trigger_price, 0.0001)
            if impact > _PRICE_IMPACT_THRESHOLD:
                st._price_impact_events += 1
                st._evidence_whale += 0.5
        except Exception:
            pass

    def _check_volume_spike(self, symbol: str, st: _SymbolState) -> None:
        """Sudden volume increase above rolling average."""
        try:
            if len(st._volume_history) < 30:
                return
            vol_vals = [float(v) for v in st._volume_history[-50:] if isinstance(v, (int, float))]
            if len(vol_vals) < 10:
                return
            vols = np.array(vol_vals, dtype=float)
            avg_vol = float(vols.mean())
            recent_vol = float(vols[-5:].mean()) if len(vols) >= 5 else avg_vol
            if avg_vol > 0 and recent_vol > avg_vol * _VOLUME_SPIKE_MULTIPLIER:
                st._volume_spike_events += 1
                st._evidence_whale += 0.4
        except Exception:
            pass

    def _update_bimodality(self, symbol: str, st: _SymbolState) -> None:
        """Trade size distribution bimodality analysis."""
        try:
            if len(st._trade_values) < 30:
                return
            val_vals = [float(v) for v in st._trade_values[-100:] if isinstance(v, (int, float))]
            if len(val_vals) < 10:
                return
            values = np.array(val_vals, dtype=float)
            if values.std() == 0:
                return

            # Normalised values
            norm = (values - values.mean()) / values.std()

            # High kurtosis + high spread = bimodal tendency
            kurtosis = float(np.mean(norm ** 4)) - 3  # excess kurtosis
            skewness = abs(float(np.mean(norm ** 3)))

            # Bimodality coefficient: BC = (skewness^2 + 1) / kurtosis
            # BC > 0.555 suggests bimodality (Pearson)
            if kurtosis > 0:
                bc = (skewness ** 2 + 1) / kurtosis
            else:
                bc = 0

            # Large spread between median and mean also suggests bimodality
            median_val = float(np.median(values))
            mean_val = float(values.mean())
            spread_ratio = abs(mean_val - median_val) / max(median_val, 0.001)

            bimodal_signal = 0
            if bc > 0.555:
                bimodal_signal += 0.3
            if spread_ratio > 0.5:
                bimodal_signal += 0.2
            # Whale fraction: what % of volume is from whale trades?
            whale_vol_frac = st._whale_volume / max(st._total_volume, 1)
            if whale_vol_frac > 0.3:
                bimodal_signal += 0.3

            st._bimodal_score = min(1.0, bimodal_signal)
            if bimodal_signal > 0.3:
                st._evidence_whale += bimodal_signal * 0.5
        except Exception:
            pass  # silently skip on type errors

    # ── Internal: Probability computation ────────────────────────

    def _evidence_to_prob(self, st: _SymbolState) -> Tuple[float, float]:
        """Convert log-odds evidence to probabilities via softmax with priors."""
        log_prior_whale = np.log(_PRIOR_WHALE)
        log_prior_none = np.log(_PRIOR_NONE)

        log_whale = log_prior_whale + st._evidence_whale
        log_none = log_prior_none + st._evidence_none

        # Replace any NaN/Inf
        if not np.isfinite(log_whale) or not np.isfinite(log_none):
            return _PRIOR_WHALE, _PRIOR_NONE

        max_log = max(log_whale, log_none)
        log_whale -= max_log
        log_none -= max_log

        exp_whale = np.exp(log_whale)
        exp_none = np.exp(log_none)
        total = exp_whale + exp_none

        if total <= 0 or not np.isfinite(total):
            return _PRIOR_WHALE, _PRIOR_NONE

        return exp_whale / total, exp_none / total

    def _compute_agreement(self, st: _SymbolState) -> float:
        """How much do the sub-signals agree."""
        evidence = [st._evidence_whale, st._evidence_none]
        max_ev = max(evidence)
        if max_ev <= 0:
            return 0.0
        others = sum(e for e in evidence if e != max_ev)
        if others <= 0:
            return 1.0
        return min(1.0, max_ev / (max_ev + abs(others)))

    def _get_sub_signals(self, st: _SymbolState) -> Dict:
        """Breakdown of sub-signal contributions."""
        whale_vol_frac = st._whale_volume / max(st._total_volume, 1)
        return {
            "whale_trade_count": st._whale_trade_count,
            "megawhale_count": st._megawhale_count,
            "volume_spike_events": st._volume_spike_events,
            "price_impact_events": st._price_impact_events,
            "bimodal_score": round(st._bimodal_score, 3),
            "whale_volume_fraction": round(whale_vol_frac, 4),
            "whale_volume_usd": round(st._whale_volume, 2),
            "total_volume_usd": round(st._total_volume, 2),
            "evidence_whale": round(st._evidence_whale, 3),
            "evidence_none": round(st._evidence_none, 3),
        }

    def _empty_result(self, symbol: str) -> Dict:
        """Return neutral probability when insufficient data."""
        return {
            "symbol": symbol,
            "whale_probability": 0.0,
            "none_probability": 1.0,
            "confidence": 0.0,
            "evidence_count": 0,
            "dominant_signal": "none",
            "sub_signals": {},
            "timestamp": time.time(),
        }
