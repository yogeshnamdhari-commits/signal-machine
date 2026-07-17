"""
Open Interest Engine — OI regime classification, spike/flush detection,
positioning analysis (long/short build-up, unwinding, covering), and OI strength scoring.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from loguru import logger


@dataclass
class OIState:
    symbol: str
    readings: List[Dict] = field(default_factory=list)
    current_oi: float = 0
    prev_oi: float = 0
    change_abs: float = 0
    change_pct: float = 0
    oi_trend: float = 0  # 1 = increasing, -1 = decreasing
    price_oi_divergence: float = 0
    # Spike/flush detection
    spike_detected: bool = False
    flush_detected: bool = False
    spike_magnitude: float = 0.0
    flush_magnitude: float = 0.0
    # Positioning classification
    oi_regime: str = "neutral_oi"      # bullish_oi / bearish_oi / neutral_oi
    positioning: str = "neutral"        # long_buildup / short_buildup / long_unwinding / short_covering / neutral
    oi_strength_score: float = 50.0     # 0-100
    # Historical stats for adaptive thresholds
    avg_change_pct: float = 0.0
    std_change_pct: float = 0.0
    peak_oi: float = 0.0


# Adaptive thresholds (in standard deviations)
_SPIKE_Z_THRESHOLD = 2.5     # OI jump > 2.5σ = spike
_FLUSH_Z_THRESHOLD = -2.5    # OI drop > 2.5σ = flush
_MIN_READINGS_FOR_STATS = 10 # Need at least 10 readings for adaptive thresholds
_BUILDUP_MIN_CHANGE_PCT = 0.005 # Minimum % change to classify as build-up/unwinding (lowered from 0.3)


class OpenInterestEngine:
    """
    Open Interest analysis with regime classification:
    - OI spike/flush detection (adaptive z-score)
    - Positioning: Long Build-up, Short Build-up, Long Unwinding, Short Covering
    - OI regime: Bullish OI, Bearish OI, Neutral OI
    - OI Strength score (0-100)
    - Price-OI divergence (squeeze detection)
    """

    def __init__(self) -> None:
        self._states: Dict[str, OIState] = {}

    async def initialize(self) -> None:
        logger.info("OpenInterest engine ready (regime + positioning + strength)")

    async def process_oi(self, symbol: str, oi: float, price: float, timestamp: float) -> None:
        st = self._states.setdefault(symbol, OIState(symbol=symbol))

        st.readings.append({"oi": oi, "price": price, "ts": timestamp})
        if len(st.readings) > 500:
            st.readings = st.readings[-250:]

        st.prev_oi = st.current_oi
        st.current_oi = oi
        st.change_abs = oi - st.prev_oi
        st.change_pct = (st.change_abs / st.prev_oi * 100) if st.prev_oi > 0 else 0

        # Track peak OI
        if oi > st.peak_oi:
            st.peak_oi = oi

        # ── OI trend (5-reading window) ──
        if len(st.readings) >= 5:
            ois = [r["oi"] for r in st.readings[-5:]]
            st.oi_trend = float(np.sign(np.mean(np.diff(ois))))

        # ── Adaptive stats for spike/flush detection ──
        if len(st.readings) >= _MIN_READINGS_FOR_STATS:
            changes = []
            for i in range(1, len(st.readings)):
                prev_oi = st.readings[i - 1]["oi"]
                if prev_oi > 0:
                    changes.append((st.readings[i]["oi"] - prev_oi) / prev_oi * 100)
            if len(changes) >= 5:
                st.avg_change_pct = float(np.mean(changes))
                st.std_change_pct = float(np.std(changes)) if len(changes) > 1 else 0.01

        # ── Spike / Flush detection ──
        if st.std_change_pct > 0 and len(st.readings) >= _MIN_READINGS_FOR_STATS:
            z_score = (st.change_pct - st.avg_change_pct) / st.std_change_pct
            st.spike_detected = z_score > _SPIKE_Z_THRESHOLD
            st.flush_detected = z_score < _FLUSH_Z_THRESHOLD
            st.spike_magnitude = max(0, z_score) if st.spike_detected else 0
            st.flush_magnitude = abs(min(0, z_score)) if st.flush_detected else 0
        else:
            st.spike_detected = False
            st.flush_detected = False

        # ── Price-OI divergence ──
        if len(st.readings) >= 10:
            recent = st.readings[-10:]
            price_change = recent[-1]["price"] - recent[0]["price"]
            oi_change = recent[-1]["oi"] - recent[0]["oi"]
            if recent[0]["price"] > 0 and recent[0]["oi"] > 0:
                pc = price_change / recent[0]["price"]
                oc = oi_change / recent[0]["oi"]
                if pc > 0 and oc < 0:
                    st.price_oi_divergence = -1  # Bearish: price up, oi down
                elif pc < 0 and oc > 0:
                    st.price_oi_divergence = 1   # Bullish: price down, oi up (new shorts)
                elif pc > 0 and oc > 0:
                    st.price_oi_divergence = 0.5  # Trend continuation
                else:
                    st.price_oi_divergence = -0.5  # Liquidation cascade risk

        # ── Positioning classification ──
        st.positioning = self._classify_positioning(st)

        # ── OI regime classification ──
        st.oi_regime = self._classify_regime(st)

        # ── OI strength score ──
        st.oi_strength_score = self._compute_strength(st)

    def _classify_positioning(self, st: OIState) -> str:
        """
        Classify positioning based on OI change direction + price direction.
        Long Build-up:   OI ↑ + Price ↑ → new longs entering
        Short Build-up:  OI ↑ + Price ↓ → new shorts entering
        Long Unwinding:  OI ↓ + Price ↓ → longs closing
        Short Covering:  OI ↓ + Price ↑ → shorts closing
        """
        if len(st.readings) < 5:
            return "neutral"

        oi_up = st.oi_trend > 0
        oi_down = st.oi_trend < 0

        # Price direction from last 5 readings
        prices = [r["price"] for r in st.readings[-5:]]
        price_trend = prices[-1] - prices[0]
        price_up = price_trend > 0
        price_down = price_trend < 0

        # Need minimum OI change to classify
        if abs(st.change_pct) < _BUILDUP_MIN_CHANGE_PCT and not st.spike_detected and not st.flush_detected:
            return "neutral"

        if oi_up and price_up:
            return "long_buildup"
        elif oi_up and price_down:
            return "short_buildup"
        elif oi_down and price_down:
            return "long_unwinding"
        elif oi_down and price_up:
            return "short_covering"

        return "neutral"

    def _classify_regime(self, st: OIState) -> str:
        """
        Classify OI regime:
        - bullish_oi: Long Build-up or Short Covering (both bullish)
        - bearish_oi: Short Build-up or Long Unwinding (both bearish)
        - neutral_oi: No significant pattern
        """
        if st.positioning in ("long_buildup", "short_covering"):
            return "bullish_oi"
        elif st.positioning in ("short_buildup", "long_unwinding"):
            return "bearish_oi"
        return "neutral_oi"

    def _compute_strength(self, st: OIState) -> float:
        """
        Compute OI Strength score (0-100).
        Components:
        - Change magnitude (30%): how big is the OI change
        - Trend consistency (25%): how consistent is the OI direction
        - Divergence strength (25%): how strong is the price-OI divergence
        - Spike/flush bonus (20%): detection of extreme moves
        """
        if len(st.readings) < 5:
            return 50.0

        # 1. Change magnitude (0-1)
        if st.std_change_pct > 0 and len(st.readings) >= _MIN_READINGS_FOR_STATS:
            z = abs(st.change_pct - st.avg_change_pct) / st.std_change_pct
            magnitude = min(z / 3, 1.0)  # normalize: 3σ = max
        else:
            magnitude = min(abs(st.change_pct) / 5, 1.0)  # fallback: 5% = max

        # 2. Trend consistency (0-1): how many of last N readings agree on direction
        if len(st.readings) >= 10:
            recent_oi_diffs = [st.readings[i]["oi"] - st.readings[i-1]["oi"]
                               for i in range(max(1, len(st.readings) - 10), len(st.readings))]
            if recent_oi_diffs:
                agreeing = sum(1 for d in recent_oi_diffs if d * st.oi_trend > 0)
                consistency = agreeing / len(recent_oi_diffs)
            else:
                consistency = 0.5
        else:
            consistency = 0.5

        # 3. Divergence strength (0-1)
        divergence = abs(st.price_oi_divergence)

        # 4. Spike/flush bonus (0-1)
        extreme = 0.0
        if st.spike_detected:
            extreme = min(st.spike_magnitude / 3, 1.0)
        elif st.flush_detected:
            extreme = min(st.flush_magnitude / 3, 1.0)

        # Weighted combination
        raw = (
            magnitude * 0.30 +
            consistency * 0.25 +
            divergence * 0.25 +
            extreme * 0.20
        )

        # Scale to 0-100
        return round(max(0, min(100, raw * 100)), 1)

    # ── Public API ───────────────────────────────────────────────

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        st = self._states.get(symbol)
        if not st or not st.readings:
            return None
        return {
            "symbol": symbol,
            # Core fields (backward-compatible)
            "current_oi": st.current_oi,
            "change_abs": st.change_abs,
            "change_pct": st.change_pct,
            "oi_trend": st.oi_trend,
            "price_oi_divergence": st.price_oi_divergence,
            "squeeze_risk": abs(st.price_oi_divergence) > 0.8,
            "signal": (
                "long_squeeze" if st.price_oi_divergence < -0.8 else
                "short_squeeze" if st.price_oi_divergence > 0.8 else
                "neutral"
            ),
            # New fields — spike/flush
            "spike_detected": st.spike_detected,
            "flush_detected": st.flush_detected,
            "spike_magnitude": round(st.spike_magnitude, 2),
            "flush_magnitude": round(st.flush_magnitude, 2),
            # New fields — positioning
            "positioning": st.positioning,
            "oi_regime": st.oi_regime,
            "oi_strength_score": st.oi_strength_score,
            # Stats
            "peak_oi": st.peak_oi,
            "avg_change_pct": round(st.avg_change_pct, 4),
            "std_change_pct": round(st.std_change_pct, 4),
            # ── Phase 5: OI Expansion & Momentum ──
            "oi_expansion_pct": round(st.change_pct, 4),
            "oi_trend_label": "RISING" if st.oi_trend > 0 else ("FALLING" if st.oi_trend < 0 else "FLAT"),
            "oi_momentum_score": round(st.oi_strength_score, 1),
        }

    def validate_signal(self, symbol: str, side: str, price_change_24h: float = 0) -> Dict:
        """
        Phase 5: Validate signal against OI rules.
        
        Rules:
          OI Rising + Price Rising = Long ✅
          OI Rising + Price Falling = Short ✅
          Price Rising + OI Falling = REJECT ❌
          Price Falling + OI Falling = REJECT ❌
        
        Returns dict with:
            valid: bool
            reason: str
            oi_expansion_pct: float
            oi_trend: str
            oi_momentum_score: float
        """
        st = self._states.get(symbol)
        if not st or not st.readings:
            return {
                "valid": True,  # No data = don't block (let other filters handle)
                "reason": "No OI data available",
                "oi_expansion_pct": 0,
                "oi_trend": "UNKNOWN",
                "oi_momentum_score": 50,
            }
        
        oi_rising = st.oi_trend > 0
        oi_falling = st.oi_trend < 0
        price_rising = price_change_24h > 0.5  # > 0.5% = rising
        price_falling = price_change_24h < -0.5  # < -0.5% = falling
        
        valid = True
        reason = "OI validation passed"
        
        # Phase 5 rules:
        if price_rising and oi_falling:
            # REJECT: Price Rising + OI Falling (weak conviction, no new positions)
            valid = False
            reason = f"REJECT: Price↑ OI↓ ({st.change_pct:+.2f}%) — no conviction"
        elif price_falling and oi_falling:
            # REJECT: Price Falling + OI Falling (liquidation cascade risk)
            valid = False
            reason = f"REJECT: Price↓ OI↓ ({st.change_pct:+.2f}%) — unwinding"
        elif oi_rising and price_rising and side == "LONG":
            reason = f"CONFIRMED: OI↑ Price↑ ({st.change_pct:+.2f}%) — Long buildup"
        elif oi_rising and price_falling and side == "SHORT":
            reason = f"CONFIRMED: OI↑ Price↓ ({st.change_pct:+.2f}%) — Short buildup"
        
        return {
            "valid": valid,
            "reason": reason,
            "oi_expansion_pct": round(st.change_pct, 4),
            "oi_trend": "RISING" if oi_rising else ("FALLING" if oi_falling else "FLAT"),
            "oi_momentum_score": round(st.oi_strength_score, 1),
        }
