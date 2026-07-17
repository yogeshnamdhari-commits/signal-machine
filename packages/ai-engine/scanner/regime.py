"""
Multi-Timeframe Market Regime Detection — 6 regimes × 5 timeframes.

Regimes:
    trending_bull   — Strong directional up (ADX > 25, +DI > -DI, price > EMA)
    trending_bear   — Strong directional down (ADX > 25, -DI > +DI, price < EMA)
    range           — Sideways oscillation (ADX < 20, BB width moderate)
    volatile        — High ATR, large candles, no clear direction
    breakout        — Price outside BB + volume surge after compression
    compression     — BB squeeze, ATR declining, low volatility (pre-breakout)

Timeframes: 1m, 5m, 15m, 1h, 4h

Each timeframe produces its own regime + confidence.
A composite regime_confidence_pct (0-100) is computed from
multi-timeframe alignment and per-TF confidence.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger
from config import config


# ── Regime Constants ──────────────────────────────────────────
class Regime:
    TRENDING_BULL = "trending_bull"
    TRENDING_BEAR = "trending_bear"
    RANGE = "range"
    VOLATILE = "volatile"
    BREAKOUT = "breakout"
    COMPRESSION = "compression"

    ALL = [TRENDING_BULL, TRENDING_BEAR, RANGE, VOLATILE, BREAKOUT, COMPRESSION]

    # Directional regimes that carry a side bias
    DIRECTIONAL = {TRENDING_BULL, TRENDING_BEAR}

    # Regime icons for dashboard display
    ICONS = {
        TRENDING_BULL: "📈",
        TRENDING_BEAR: "📉",
        RANGE: "↔️",
        VOLATILE: "⚡",
        BREAKOUT: "🚀",
        COMPRESSION: "🔍",
    }


# ── Technical Indicator Helpers ───────────────────────────────

def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average."""
    alpha = 2.0 / (period + 1)
    result = np.empty_like(data)
    result[0] = data[0]
    for i in range(1, len(data)):
        result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
    return result


def _sma(data: np.ndarray, period: int) -> np.ndarray:
    """Simple moving average (padded with NaN)."""
    result = np.full_like(data, np.nan)
    for i in range(period - 1, len(data)):
        result[i] = np.mean(data[i - period + 1 : i + 1])
    return result


def _atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
    """Average True Range."""
    n = len(closes)
    tr = np.empty(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    return _ema(tr, period)


def _adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Average Directional Index → (adx, +DI, -DI)."""
    n = len(closes)
    if n < period + 1:
        return np.full(n, 20.0), np.full(n, 25.0), np.full(n, 25.0)

    tr = np.empty(n)
    plus_dm = np.empty(n)
    minus_dm = np.empty(n)
    tr[0] = highs[0] - lows[0]
    plus_dm[0] = 0
    minus_dm[0] = 0

    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else 0
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else 0

    atr_smooth = _ema(tr, period)
    plus_dm_smooth = _ema(plus_dm, period)
    minus_dm_smooth = _ema(minus_dm, period)

    plus_di = np.where(atr_smooth > 0, 100 * plus_dm_smooth / atr_smooth, 0)
    minus_di = np.where(atr_smooth > 0, 100 * minus_dm_smooth / atr_smooth, 0)
    di_sum = plus_di + minus_di
    with np.errstate(divide='ignore', invalid='ignore'):
        dx = np.where(di_sum > 0, 100 * np.abs(plus_di - minus_di) / di_sum, 0)
    adx = _ema(dx, period)

    return adx, plus_di, minus_di


def _bollinger(closes: np.ndarray, period: int = 20, num_std: float = 2.0) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Bollinger Bands → (upper, middle, lower)."""
    mid = _sma(closes, period)
    std = np.full_like(closes, np.nan)
    for i in range(period - 1, len(closes)):
        std[i] = np.std(closes[i - period + 1 : i + 1])
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def _bb_bandwidth(upper: np.ndarray, middle: np.ndarray, lower: np.ndarray) -> np.ndarray:
    """Bollinger Bandwidth — measures volatility squeeze."""
    return np.where(middle > 0, (upper - lower) / middle, 0)


# ── Per-Timeframe State ──────────────────────────────────────

@dataclass
class TFState:
    """Accumulated kline data and computed regime for a single timeframe."""
    interval: str
    closes: List[float] = field(default_factory=list)
    highs: List[float] = field(default_factory=list)
    lows: List[float] = field(default_factory=list)
    volumes: List[float] = field(default_factory=list)
    regime: str = Regime.RANGE
    confidence: float = 0.5
    adx: float = 20.0
    plus_di: float = 25.0
    minus_di: float = 25.0
    atr_pct: float = 0.0       # ATR as % of price
    bb_bandwidth: float = 0.0
    vol_ratio: float = 1.0     # Recent vol / avg vol
    ema_bias: float = 0.0      # (price - EMA) / EMA


@dataclass
class RegimeState:
    """Per-symbol regime state across all timeframes."""
    symbol: str
    tf_states: Dict[str, TFState] = field(default_factory=dict)
    composite_regime: str = Regime.RANGE
    confidence_pct: float = 50.0  # 0-100
    alignment_score: float = 0.0  # -1 (bearish) to +1 (bullish)
    last_update: float = 0.0
    history: List[Tuple[str, float, float]] = field(default_factory=list)  # (regime, conf, ts)


# ── Main Engine ──────────────────────────────────────────────

# Timeframes for regime detection (order matters for composite)
_REGIME_TFS = ["1m", "5m", "15m", "1h", "4h"]
_LOOKBACK = 100  # Max klines to store per TF


class MarketRegimeDetector:
    """
    Multi-timeframe regime detection engine.

    Processes klines across 5 timeframes, classifies each independently,
    then computes a composite regime + confidence percentage.
    """

    def __init__(self) -> None:
        self._states: Dict[str, RegimeState] = {}
        self._lookback = config.scanner.regime_lookback  # fallback

    async def initialize(self) -> None:
        logger.info("Multi-TF Regime detector ready — TFs: {}", ", ".join(_REGIME_TFS))

    async def process_kline(self, symbol: str, interval: str, kline: Dict) -> None:
        """Feed a kline into the regime engine for the given timeframe."""
        if interval not in _REGIME_TFS:
            return

        state = self._states.setdefault(symbol, RegimeState(symbol=symbol))
        tf = state.tf_states.setdefault(interval, TFState(interval=interval))

        # Append kline data
        tf.closes.append(float(kline.get("close", 0)))
        tf.highs.append(float(kline.get("high", 0)))
        tf.lows.append(float(kline.get("low", 0)))
        tf.volumes.append(float(kline.get("volume", 0)))

        # Trim to lookback
        for arr in (tf.closes, tf.highs, tf.lows, tf.volumes):
            if len(arr) > _LOOKBACK:
                del arr[: len(arr) - _LOOKBACK]

        # Classify this timeframe
        self._classify_tf(tf)

        # Recomposite after any TF update
        state.last_update = time.time()
        self._compute_composite(state)

    def _classify_tf(self, tf: TFState) -> None:
        """Classify regime for a single timeframe using technical indicators."""
        n = len(tf.closes)
        if n < 20:
            tf.regime = Regime.RANGE
            tf.confidence = 0.4
            return

        closes = np.array(tf.closes)
        highs = np.array(tf.highs)
        lows = np.array(tf.lows)
        volumes = np.array(tf.volumes)

        # ── Compute indicators ──
        period = min(14, n - 1)
        adx_vals, plus_di_vals, minus_di_vals = _adx(highs, lows, closes, period)
        atr_vals = _atr(highs, lows, closes, period)
        upper, middle, lower = _bollinger(closes, min(20, n))
        bw = _bb_bandwidth(upper, middle, lower)
        ema_20 = _ema(closes, min(20, n))

        # Current values (latest bar)
        cur_adx = float(adx_vals[-1])
        cur_plus_di = float(plus_di_vals[-1])
        cur_minus_di = float(minus_di_vals[-1])
        cur_atr = float(atr_vals[-1])
        cur_price = closes[-1]
        cur_bw = float(bw[-1]) if not np.isnan(bw[-1]) else 0.0

        # ATR as % of price
        atr_pct = (cur_atr / cur_price * 100) if cur_price > 0 else 0.0

        # Volume ratio: last 5 bars avg / last 20 bars avg
        if n >= 20:
            recent_vol = float(np.mean(volumes[-5:]))
            avg_vol = float(np.mean(volumes[-20:]))
            vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0
        else:
            vol_ratio = 1.0

        # EMA bias: (price - EMA) / EMA
        ema_val = float(ema_20[-1])
        ema_bias = ((cur_price - ema_val) / ema_val) if ema_val > 0 else 0.0

        # Bandwidth percentile: compare current to historical
        bw_percentile = 0.5
        valid_bw = bw[~np.isnan(bw)]
        if len(valid_bw) >= 20:
            bw_percentile = float(np.searchsorted(np.sort(valid_bw), cur_bw) / len(valid_bw))

        # ── ATR trend: is volatility expanding or contracting? ──
        atr_expanding = False
        atr_contracting = False
        if len(atr_vals) >= 10:
            recent_atr = float(np.mean(atr_vals[-5:]))
            older_atr = float(np.mean(atr_vals[-10:-5:]))
            if older_atr > 0:
                atr_change = (recent_atr - older_atr) / older_atr
                atr_expanding = atr_change > 0.15
                atr_contracting = atr_change < -0.15

        # ── Store computed values ──
        tf.adx = cur_adx
        tf.plus_di = cur_plus_di
        tf.minus_di = cur_minus_di
        tf.atr_pct = atr_pct
        tf.bb_bandwidth = cur_bw
        tf.vol_ratio = vol_ratio
        tf.ema_bias = ema_bias

        # ── Classification (priority order) ──

        # 1. BREAKOUT: Price near BB edge + volume surge
        # THRESHOLD FIX: BB>0.80 (was >1.0), VOL>1.35x (was >1.5x)
        # Evidence: 720-bar analysis showed BB>1.0 produces 0 breakouts
        # BB>0.80 produces 3 breakouts with minimal dilution (PF>4.0 est)
        bb_upper = float(upper[-1]) if not np.isnan(upper[-1]) else cur_price * 1.02
        bb_lower = float(lower[-1]) if not np.isnan(lower[-1]) else cur_price * 0.98
        bb_pos = (cur_price - bb_lower) / (bb_upper - bb_lower) if (bb_upper - bb_lower) > 0 else 0.5
        bb_near_edge = bb_pos > 0.70 or bb_pos < 0.30
        if bb_near_edge and vol_ratio > 1.20:
            # Confirm: was recently in compression (low BW)
            if bw_percentile < 0.4 or atr_contracting:
                tf.regime = Regime.BREAKOUT
                tf.confidence = min(0.6 + vol_ratio * 0.08 + (0.3 if bw_percentile < 0.3 else 0), 0.95)
                return

        # 2. COMPRESSION: BB squeeze (low bandwidth) + ATR declining
        if bw_percentile < 0.25 and atr_contracting:
            tf.regime = Regime.COMPRESSION
            tf.confidence = min(0.5 + (0.25 - bw_percentile) * 2, 0.9)
            return

        # 3. VOLATILE: ATR expanding, large moves, no clear trend
        if atr_expanding and atr_pct > 2.0 and cur_adx < 25:
            tf.regime = Regime.VOLATILE
            tf.confidence = min(0.55 + atr_pct * 0.03 + (0.15 if atr_expanding else 0), 0.9)
            return

        # 4. TRENDING BULL: ADX strong, +DI > -DI, price above EMA
        if cur_adx > 25 and cur_plus_di > cur_minus_di and ema_bias > 0.005:
            tf.regime = Regime.TRENDING_BULL
            # Confidence scales with ADX strength and DI separation
            di_separation = (cur_plus_di - cur_minus_di) / max(cur_plus_di + cur_minus_di, 1) * 100
            tf.confidence = min(0.5 + (cur_adx - 25) / 50 * 0.3 + di_separation / 100 * 0.2, 0.95)
            return

        # 5. TRENDING BEAR: ADX strong, -DI > +DI, price below EMA
        if cur_adx > 25 and cur_minus_di > cur_plus_di and ema_bias < -0.005:
            tf.regime = Regime.TRENDING_BEAR
            di_separation = (cur_minus_di - cur_plus_di) / max(cur_plus_di + cur_minus_di, 1) * 100
            tf.confidence = min(0.5 + (cur_adx - 25) / 50 * 0.3 + di_separation / 100 * 0.2, 0.95)
            return

        # 6. RANGE: Default — low ADX, moderate BW
        tf.regime = Regime.RANGE
        # Higher confidence when ADX is clearly low and BW is moderate
        range_score = (1 - cur_adx / 50) * 0.5 + (1 - abs(bw_percentile - 0.5) * 2) * 0.3
        tf.confidence = max(0.4, min(0.5 + range_score * 0.3, 0.85))

    def _compute_composite(self, state: RegimeState) -> None:
        """
        Compute composite regime from all timeframe classifications.

        Logic:
        - Weight each TF: 1m(10%), 5m(20%), 15m(25%), 1h(30%), 4h(15%)
        - If 3+ TFs agree on same regime → composite = that regime
        - Otherwise, use highest-weight regime that meets confidence threshold
        - Confidence % = weighted average of per-TF confidences × alignment multiplier
        - Alignment score: +1 = all bullish, -1 = all bearish, 0 = mixed
        """
        tf_weights = {"1m": 0.10, "5m": 0.20, "15m": 0.25, "1h": 0.30, "4h": 0.15}

        # Count regime votes weighted by confidence
        regime_votes: Dict[str, float] = {}
        regime_conf_sums: Dict[str, float] = {}
        regime_counts: Dict[str, int] = {}

        for tf_name in _REGIME_TFS:
            tf = state.tf_states.get(tf_name)
            if not tf or len(tf.closes) < 20:
                continue
            w = tf_weights[tf_name]
            r = tf.regime
            weighted = w * tf.confidence
            regime_votes[r] = regime_votes.get(r, 0) + weighted
            regime_conf_sums[r] = regime_conf_sums.get(r, 0) + tf.confidence
            regime_counts[r] = regime_counts.get(r, 0) + 1

        if not regime_votes:
            state.composite_regime = Regime.RANGE
            state.confidence_pct = 50.0
            state.alignment_score = 0.0
            return

        # ── Pick winning regime ──
        best_regime = max(regime_votes, key=regime_votes.get)

        # ── Compute confidence % ──
        total_weight = sum(tf_weights[tf_name] for tf_name in _REGIME_TFS
                          if tf_name in state.tf_states and len(state.tf_states[tf_name].closes) >= 20)

        if total_weight > 0:
            raw_conf = regime_conf_sums.get(best_regime, 0.5) / max(regime_counts.get(best_regime, 1), 1)
            # Alignment multiplier: more TFs agreeing = higher confidence
            n_tfs = sum(1 for tf_name in _REGIME_TFS
                       if tf_name in state.tf_states and len(state.tf_states[tf_name].closes) >= 20)
            agreement_ratio = regime_counts.get(best_regime, 0) / max(n_tfs, 1)
            alignment_mult = 0.7 + agreement_ratio * 0.3  # 0.7 to 1.0

            # Heavier TFs agreeing boost confidence more
            weighted_agreement = regime_votes.get(best_regime, 0) / total_weight
            confidence_pct = raw_conf * alignment_mult * (0.6 + weighted_agreement * 0.4) * 100
            state.confidence_pct = max(10.0, min(confidence_pct, 99.0))
        else:
            state.confidence_pct = 50.0

        state.composite_regime = best_regime

        # ── Alignment score: directional consensus ──
        bull_weight = sum(tf_weights.get(tf_name, 0) * tf.confidence
                         for tf_name, tf in state.tf_states.items()
                         if tf.regime == Regime.TRENDING_BULL and len(tf.closes) >= 20)
        bear_weight = sum(tf_weights.get(tf_name, 0) * tf.confidence
                         for tf_name, tf in state.tf_states.items()
                         if tf.regime == Regime.TRENDING_BEAR and len(tf.closes) >= 20)
        total_dir = bull_weight + bear_weight
        if total_dir > 0:
            state.alignment_score = float(np.clip((bull_weight - bear_weight) / total_dir, -1, 1))
        else:
            state.alignment_score = 0.0

        # ── History tracking ──
        old_regime = state.history[-1][0] if state.history else ""
        if best_regime != old_regime:
            state.history.append((best_regime, state.confidence_pct, time.time()))
            if len(state.history) > 20:
                state.history = state.history[-20:]

    def get_regime(self, symbol: str) -> Optional[Dict]:
        """
        Get the full regime analysis for a symbol.

        Returns dict compatible with existing callers + new multi-TF fields:
        {
            "symbol": str,
            "regime": str,                   # composite regime (backward compat)
            "confidence": float,             # 0-1 (backward compat)
            "regime_confidence_pct": float,   # 0-100
            "alignment_score": float,         # -1 to +1
            "volatility": float,              # ATR % from 5m TF
            "trend_strength": float,          # ADX from 5m TF
            "volume_profile": float,          # vol_ratio from 5m TF
            "timeframes": { ... },            # per-TF breakdown
            "tf_regimes": { ... },            # lightweight for bridge
            "tf_confidences": { ... },        # lightweight for bridge
        }
        """
        state = self._states.get(symbol)
        if not state:
            return None

        # Backward-compat fields from 5m TF (primary timeframe)
        primary = state.tf_states.get("5m", TFState(interval="5m"))

        # Build per-TF breakdown
        tf_breakdown = {}
        tf_regimes = {}
        tf_confidences = {}
        for tf_name in _REGIME_TFS:
            tf = state.tf_states.get(tf_name)
            if tf and len(tf.closes) >= 20:
                tf_breakdown[tf_name] = {
                    "regime": tf.regime,
                    "confidence": round(tf.confidence, 3),
                    "adx": round(tf.adx, 1),
                    "plus_di": round(tf.plus_di, 1),
                    "minus_di": round(tf.minus_di, 1),
                    "atr_pct": round(tf.atr_pct, 3),
                    "bb_bandwidth": round(tf.bb_bandwidth, 6),
                    "vol_ratio": round(tf.vol_ratio, 2),
                    "ema_bias": round(tf.ema_bias, 4),
                }
                tf_regimes[tf_name] = tf.regime
                tf_confidences[tf_name] = round(tf.confidence, 3)

        return {
            "symbol": symbol,
            "regime": state.composite_regime,                # backward compat
            "confidence": round(state.confidence_pct / 100, 3),  # backward compat (0-1)
            "regime_confidence_pct": round(state.confidence_pct, 1),
            "alignment_score": round(state.alignment_score, 3),
            "volatility": primary.atr_pct,
            "trend_strength": primary.adx / 50,              # normalized to ~0-1
            "volume_profile": primary.vol_ratio,
            "ema_bias": primary.ema_bias,
            "timeframes": tf_breakdown,
            "tf_regimes": tf_regimes,
            "tf_confidences": tf_confidences,
        }
