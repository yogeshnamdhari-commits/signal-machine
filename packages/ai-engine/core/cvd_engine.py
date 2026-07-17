"""
CVDEngine — Cumulative Volume Delta tracker with multi-timeframe support.

Data Source: Binance Futures AggTrade WebSocket
  - m = false → Aggressive Buy  → delta += qty
  - m = true  → Aggressive Sell → delta -= qty

CRITICAL: Synthetic trades from !ticker@arr are EXCLUDED.

Provides:
- Real-time CVD per timeframe (1m, 5m, 15m, 1h, 4h)
- Rolling trade windows bucketed by time
- Bullish/Bearish divergence detection (price vs CVD direction)
- 5-level CVD Bias: Strong Bullish / Bullish / Neutral / Bearish / Strong Bearish
- Integration into institutional scoring pipeline

Divergence:
  - Bullish: Price lower low + CVD higher low
  - Bearish: Price higher high + CVD lower high

Signal:
  - Strong Bullish: CVD rising + Price rising
  - Bullish Divergence: CVD rising + Price flat/down
  - Strong Bearish: CVD falling + Price falling
  - Bearish Divergence: CVD falling + Price flat/up
  - Neutral: No significant difference
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Dict, List, Optional

import numpy as np
from loguru import logger


# Timeframe definitions in seconds
_TF_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
}

# Max trades to keep per symbol (rolling window)
_MAX_TRADES = 5000


class CVDEngine:
    """
    Tracks cumulative volume delta per symbol across multiple timeframes.
    Uses rolling windows per timeframe (not cumulative forever).
    Detects price-CVD divergence and classifies into 5-level bias.
    """

    def __init__(self) -> None:
        # Raw trade buffer per symbol (for time-bucketing)
        self._trades: Dict[str, List[Dict]] = defaultdict(list)

        # Rolling delta windows per symbol per timeframe
        self._delta_windows: Dict[str, Dict[str, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=2000))
        )

        # Buy/sell volume per timeframe
        self._buy_vol: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._sell_vol: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))

        # Price history per timeframe for divergence detection
        self._price_closes: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
        self._cvd_closes: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))

        # Current bias per timeframe
        self._bias: Dict[str, Dict[str, str]] = defaultdict(lambda: defaultdict(str))

        # Divergence state per timeframe
        self._divergence: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))

        # Last processed timestamps per symbol per TF
        self._last_tf_update: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))

    async def initialize(self) -> None:
        logger.info("CVDEngine ready (multi-TF: 1m, 5m, 15m, 1h, 4h)")

    def update(self, symbol: str, price: float, quantity: float, is_buyer_maker: bool) -> None:
        """
        Process a single trade tick. Accumulates into all active timeframes.

        Delta per spec: qty-based (not price x qty)
          - m == false -> delta += qty (aggressive buy)
          - m == true  -> delta -= qty (aggressive sell)
        """
        now = time.time()

        # Delta per spec: qty-based
        delta = quantity if not is_buyer_maker else -quantity
        value = price * quantity

        # Store raw trade
        trade = {
            "price": price,
            "quantity": quantity,
            "value": value,
            "delta": delta,
            "is_buyer_maker": is_buyer_maker,
            "ts": now,
        }
        buf = self._trades[symbol]
        buf.append(trade)
        if len(buf) > _MAX_TRADES:
            self._trades[symbol] = buf[-_MAX_TRADES // 2:]

        # Accumulate into all timeframes
        for tf_name, tf_seconds in _TF_SECONDS.items():
            # Add delta to rolling window
            self._delta_windows[symbol][tf_name].append((delta, now))

            # Accumulate buy/sell volume (value-based for ratio)
            if is_buyer_maker:
                self._sell_vol[symbol][tf_name] += value
            else:
                self._buy_vol[symbol][tf_name] += value

            # Track closes for divergence (sampled: one per second max)
            last_t = self._last_tf_update[symbol][tf_name]
            if now - last_t >= 1.0:
                # Compute CVD from rolling window for this timeframe
                window = self._delta_windows[symbol][tf_name]
                cutoff = now - tf_seconds
                cvd_in_window = sum(d for d, t in window if t >= cutoff)

                self._price_closes[symbol][tf_name].append(price)
                self._cvd_closes[symbol][tf_name].append(cvd_in_window)
                # Keep last 200 closes per TF
                if len(self._price_closes[symbol][tf_name]) > 200:
                    self._price_closes[symbol][tf_name] = self._price_closes[symbol][tf_name][-200:]
                    self._cvd_closes[symbol][tf_name] = self._cvd_closes[symbol][tf_name][-200:]
                self._last_tf_update[symbol][tf_name] = now

        # Recompute bias for all TFs
        self._recompute_bias(symbol)

    def _recompute_bias(self, symbol: str) -> None:
        """Recompute 5-level CVD bias for each timeframe."""
        for tf_name in _TF_SECONDS:
            # Compute CVD from rolling window
            now = time.time()
            window = self._delta_windows[symbol][tf_name]
            tf_seconds = _TF_SECONDS[tf_name]
            cutoff = now - tf_seconds
            cvd = sum(d for d, t in window if t >= cutoff)

            buy_v = self._buy_vol[symbol][tf_name]
            sell_v = self._sell_vol[symbol][tf_name]
            total = buy_v + sell_v

            if total == 0:
                self._bias[symbol][tf_name] = "neutral"
                self._divergence[symbol][tf_name] = 0.0
                continue

            # Buy ratio: 0 = all selling, 1 = all buying
            buy_ratio = buy_v / total

            # Delta momentum: recent vs older
            momentum = self._compute_momentum(symbol, tf_name)

            # Divergence detection
            div = self._detect_divergence(symbol, tf_name)
            self._divergence[symbol][tf_name] = div

            # 5-level bias classification (correct logic):
            #   CVD positive + rising momentum + buy dominant -> strong_bullish
            #   CVD positive + (rising momentum OR buy dominant) -> bullish
            #   CVD negative + falling momentum + sell dominant -> strong_bearish
            #   CVD negative + (falling momentum OR sell dominant) -> bearish
            #   Otherwise -> neutral
            cvd_positive = cvd > 0
            momentum_positive = momentum > 0.1
            momentum_negative = momentum < -0.1
            buy_dominant = buy_ratio > 0.52
            sell_dominant = buy_ratio < 0.48

            if cvd_positive and momentum_positive and buy_dominant:
                self._bias[symbol][tf_name] = "strong_bullish"
            elif cvd_positive and (momentum_positive or buy_dominant):
                self._bias[symbol][tf_name] = "bullish"
            elif not cvd_positive and momentum_negative and sell_dominant:
                self._bias[symbol][tf_name] = "strong_bearish"
            elif not cvd_positive and (momentum_negative or sell_dominant):
                self._bias[symbol][tf_name] = "bearish"
            else:
                self._bias[symbol][tf_name] = "neutral"

    def _compute_momentum(self, symbol: str, tf_name: str) -> float:
        """Compute CVD momentum: recent delta vs older delta. Returns -1 to 1."""
        closes = self._cvd_closes[symbol][tf_name]
        if len(closes) < 20:
            return 0.0

        recent = closes[-10:]
        older = closes[-20:-10]
        recent_delta = recent[-1] - recent[0]
        older_delta = older[-1] - older[0] if older else 0
        diff = recent_delta - older_delta
        return float(np.clip(diff / max(abs(older_delta), 1), -1, 1))

    def _detect_divergence(self, symbol: str, tf_name: str) -> float:
        """
        Detect price-CVD divergence.
        Returns -1 to 1:
          +1 = strong bullish divergence (price lower low, CVD higher low)
          -1 = strong bearish divergence (price higher high, CVD lower high)
           0 = no divergence
        """
        prices = self._price_closes[symbol][tf_name]
        cvds = self._cvd_closes[symbol][tf_name]

        if len(prices) < 30 or len(cvds) < 30:
            return 0.0

        # Use last 30 data points, split into two halves
        p_recent = prices[-15:]
        p_older = prices[-30:-15]
        c_recent = cvds[-15:]
        c_older = cvds[-30:-15]

        # Price trend
        price_higher = max(p_recent) > max(p_older)
        price_lower = min(p_recent) < min(p_older)

        # CVD trend (use mean for stability)
        cvd_rising = np.mean(c_recent) > np.mean(c_older)
        cvd_falling = np.mean(c_recent) < np.mean(c_older)

        # Bullish divergence: price lower low + CVD higher low
        if price_lower and cvd_rising:
            strength = min(abs((np.mean(c_recent) - np.mean(c_older)) / max(abs(np.mean(c_older)), 1)), 1.0)
            return strength

        # Bearish divergence: price higher high + CVD lower high
        if price_higher and cvd_falling:
            strength = min(abs((np.mean(c_recent) - np.mean(c_older)) / max(abs(np.mean(c_older)), 1)), 1.0)
            return -strength

        return 0.0

    # ── Public API ───────────────────────────────────────────────

    def get_cvd(self, symbol: str, timeframe: str = "1m") -> float:
        """Return current CVD for a timeframe (from rolling window)."""
        now = time.time()
        window = self._delta_windows[symbol][timeframe]
        tf_seconds = _TF_SECONDS[timeframe]
        cutoff = now - tf_seconds
        return sum(d for d, t in window if t >= cutoff)

    def get_bias(self, symbol: str, timeframe: str = "1m") -> str:
        """Return 5-level CVD bias for a timeframe."""
        return self._bias[symbol][timeframe]

    def get_divergence(self, symbol: str, timeframe: str = "1m") -> float:
        """Return divergence score (-1 to 1) for a timeframe."""
        return self._divergence[symbol][timeframe]

    def get_buy_sell_ratio(self, symbol: str, timeframe: str = "1m") -> float:
        """Return buy/sell volume ratio for a timeframe."""
        buy = self._buy_vol[symbol][timeframe]
        sell = self._sell_vol[symbol][timeframe]
        if sell == 0:
            return 1.0 if buy == 0 else float("inf")
        return buy / sell

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        """
        Full analysis dict for the scoring pipeline.
        Returns CVD values across all timeframes + divergence + bias.
        """
        if symbol not in self._delta_windows:
            return None

        # Primary timeframe for scoring (5m is the most balanced)
        primary_tf = "5m"

        # Compute CVD from rolling window for primary TF
        primary_cvd = self.get_cvd(symbol, primary_tf)
        buy_5m = self._buy_vol[symbol]["5m"]
        sell_5m = self._sell_vol[symbol]["5m"]
        total_5m = buy_5m + sell_5m

        result = {
            "symbol": symbol,
            # Primary TF values (backward-compatible with existing scoring)
            "cumulative_delta": primary_cvd,
            "delta_momentum": self._compute_momentum(symbol, primary_tf),
            "price_delta_divergence": self._divergence[symbol][primary_tf],
            "buy_pressure": buy_5m / max(total_5m, 1),
            "signal_strength": 0.0,
            # Multi-TF CVD values
            "cvd_1m": round(self.get_cvd(symbol, "1m"), 2),
            "cvd_5m": round(self.get_cvd(symbol, "5m"), 2),
            "cvd_15m": round(self.get_cvd(symbol, "15m"), 2),
            "cvd_1h": round(self.get_cvd(symbol, "1h"), 2),
            "cvd_4h": round(self.get_cvd(symbol, "4h"), 2),
            # Multi-TF bias
            "cvd_bias_1m": self._bias[symbol]["1m"],
            "cvd_bias_5m": self._bias[symbol]["5m"],
            "cvd_bias_15m": self._bias[symbol]["15m"],
            "cvd_bias_1h": self._bias[symbol]["1h"],
            "cvd_bias_4h": self._bias[symbol]["4h"],
            # Primary bias
            "cvd_bias": self._bias[symbol][primary_tf],
            # Divergence per TF
            "cvd_divergence_5m": round(self._divergence[symbol]["5m"], 4),
            "cvd_divergence_15m": round(self._divergence[symbol]["15m"], 4),
            # Buy/sell ratio
            "cvd_buy_ratio_5m": round(buy_5m / max(total_5m, 1), 4),
            # Momentum
            "momentum": self._compute_momentum(symbol, primary_tf),
        }

        # Compute signal strength from bias
        bias_to_strength = {
            "strong_bullish": 0.9,
            "bullish": 0.7,
            "neutral": 0.5,
            "bearish": 0.3,
            "strong_bearish": 0.1,
        }
        result["signal_strength"] = bias_to_strength.get(result["cvd_bias"], 0.5)

        return result

    def get_divergence_adjustment(self, symbol: str, side: str) -> Dict:
        """
        Phase 4: CVD Divergence boost/penalty for confidence scoring.
        
        Bullish Divergence: Price LL + CVD HL → +15 points if LONG
        Bearish Divergence: Price HH + CVD LH → +15 points if SHORT
        Opposing divergence → -20 points
        
        Returns dict with:
            adjustment: float (points to add/subtract from confidence_100)
            divergence_type: str ("bullish", "bearish", "none")
            description: str
        """
        # Get divergence from primary TF (5m)
        div_5m = self._divergence[symbol]["5m"]
        div_15m = self._divergence[symbol]["15m"]
        
        # Get CVD bias
        bias_5m = self._bias[symbol]["5m"]
        bias_15m = self._bias[symbol]["15m"]
        
        # Determine divergence type
        # Positive divergence = bullish (CVD rising while price flat/falling)
        # Negative divergence = bearish (CVD falling while price flat/rising)
        avg_div = (div_5m + div_15m) / 2 if div_15m != 0 else div_5m
        
        result = {
            "adjustment": 0.0,
            "divergence_type": "none",
            "div_5m": round(div_5m, 4),
            "div_15m": round(div_15m, 4),
            "description": "No significant divergence",
        }
        
        if avg_div > 0.15:
            # Bullish divergence detected
            result["divergence_type"] = "bullish"
            if side == "LONG":
                result["adjustment"] = 15.0  # +15 points boost
                result["description"] = f"Bullish CVD divergence confirms LONG (+15)"
            else:
                result["adjustment"] = -20.0  # -20 penalty (opposes SHORT)
                result["description"] = f"Bullish CVD divergence opposes SHORT (-20)"
        elif avg_div < -0.15:
            # Bearish divergence detected
            result["divergence_type"] = "bearish"
            if side == "SHORT":
                result["adjustment"] = 15.0  # +15 points boost
                result["description"] = f"Bearish CVD divergence confirms SHORT (+15)"
            else:
                result["adjustment"] = -20.0  # -20 penalty (opposes LONG)
                result["description"] = f"Bearish CVD divergence opposes LONG (-20)"
        
        # Multi-TF confirmation bonus: if 5m and 15m agree on divergence
        if (div_5m > 0.1 and div_15m > 0.1) or (div_5m < -0.1 and div_15m < -0.1):
            if result["adjustment"] > 0:
                result["adjustment"] += 5  # Extra 5 points for multi-TF confirmation
                result["description"] += " (multi-TF confirmed +5)"
        
        return result
