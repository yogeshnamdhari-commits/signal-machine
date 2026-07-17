"""
Intraday Signal Enhancer — Adaptive SL/TP, multi-timeframe confluence,
volume-profile aware levels, and session-aware signal validation.

Improves real-time signal accuracy for intraday entries by:
1. Adaptive SL placement using support/resistance proximity + volatility bands
2. Dynamic TP targeting based on regime + volume profile zones
3. Multi-timeframe SL/TP alignment (1m → 5m → 15m → 1h)
4. Session-aware confidence adjustment (Asian/European/US)
5. Intraday trend strength scoring
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from loguru import logger


# ── Intraday Trading Sessions (UTC) ─────────────────────────────
_SESSIONS = {
    "asian":    (0, 8),    # 00:00–08:00 UTC (Tokyo/HK)
    "european": (7, 15),   # 07:00–15:00 UTC (London/Frankfurt)
    "us":       (13, 21),  # 13:00–21:00 UTC (New York)
    "overlap":  (13, 15),  # EU/US overlap — highest liquidity
}


@dataclass
class IntradayLevels:
    """Computed intraday trading levels for a signal."""
    # Adaptive SL/TP
    adaptive_sl: float = 0.0
    adaptive_tp: float = 0.0
    sl_distance_pct: float = 0.0
    tp_distance_pct: float = 0.0
    risk_reward: float = 0.0

    # Multi-timeframe confirmation
    mtf_sl_aligned: bool = False
    mtf_tp_aligned: bool = False
    mtf_alignment_score: float = 0.0  # 0-1

    # Volatility context
    atr_1m: float = 0.0
    atr_5m: float = 0.0
    atr_15m: float = 0.0
    atr_1h: float = 0.0
    volatility_regime: str = "normal"  # low, normal, high, extreme
    volatility_percentile: float = 50.0

    # Support/Resistance proximity
    nearest_support: float = 0.0
    nearest_resistance: float = 0.0
    support_distance_pct: float = 0.0
    resistance_distance_pct: float = 0.0

    # Volume profile
    vp_point_of_control: float = 0.0  # Highest volume price
    vp_value_area_high: float = 0.0
    vp_value_area_low: float = 0.0
    price_in_value_area: bool = False

    # Session context
    current_session: str = ""
    session_liquidity_score: float = 0.5
    session_volatility_mult: float = 1.0

    # Trend alignment
    intraday_trend: str = ""  # bullish, bearish, sideways
    trend_strength: float = 0.0
    momentum_score: float = 0.0

    # Final quality metrics
    signal_quality_score: float = 0.0  # 0-100
    quality_tier: str = "C"  # A, B, C
    confidence_adjustment: float = 0.0  # -0.2 to +0.2

    # Risk metrics
    max_position_risk_pct: float = 1.0
    suggested_leverage: int = 5
    intraday_stop_width: float = 0.0  # ATR-normalized stop width


class IntradaySignalEnhancer:
    """
    Enhances raw signals with adaptive intraday SL/TP levels and quality scoring.
    
    Pipeline:
    1. Compute multi-timeframe ATR context
    2. Place adaptive SL at key structural levels (S/R + ATR floor)
    3. Set dynamic TP based on regime, R:R targets, and volume profile
    4. Validate against session liquidity
    5. Score overall signal quality for intraday trading
    """

    def __init__(self) -> None:
        # ATR multipliers for SL/TP (regime-adjusted)
        self._sl_atr_map = {
            "trending_up": 1.8,
            "trending_down": 1.8,
            "breakout": 2.2,
            "reversal": 1.5,
            "ranging": 1.2,
            "volatile": 2.5,
            "quiet": 1.0,
        }
        self._tp_atr_map = {
            "trending_up": 3.5,
            "trending_down": 3.5,
            "breakout": 4.0,
            "reversal": 2.5,
            "ranging": 2.0,
            "volatile": 3.0,
            "quiet": 1.5,
        }
        # Minimum R:R thresholds
        self._min_rr = 1.5
        self._target_rr = 2.5
        # Session liquidity scores
        self._session_liquidity = {
            "asian": 0.6,
            "european": 0.9,
            "us": 1.0,
            "overlap": 1.0,
            "off": 0.3,
        }

    def enhance_signal(
        self,
        signal: Dict[str, Any],
        market_data: Dict[str, Any],
        orderflow: Optional[Dict] = None,
        regime: Optional[Dict] = None,
        liquidity_map: Optional[Dict] = None,
        cumulative_delta: Optional[Dict] = None,
        absorption: Optional[Dict] = None,
        liquidation: Optional[Dict] = None,
        oi_data: Optional[Dict] = None,
        funding_data: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Enhance a signal with adaptive intraday SL/TP and quality scoring.
        
        Args:
            signal: Raw signal dict with entry_price, stop_loss, take_profit, type, etc.
            market_data: Dict with klines, trades, etc.
            orderflow: Order flow analysis data
            regime: Market regime detection
            liquidity_map: Liquidity mapping engine output
            cumulative_delta: CVD analysis data
            absorption: Absorption detector analysis (passive order levels)
            liquidation: Liquidation analytics (cluster zones, heat zones)
            oi_data: Open interest analysis (squeeze risk, divergence)
            funding_data: Funding rate analysis (extremes, z-score)
            
        Returns:
            Enhanced signal dict with additional intraday fields.
        """
        entry = signal.get("entry_price", 0)
        direction = signal.get("type", signal.get("side", "LONG"))
        if entry <= 0:
            return signal

        # ── 1. Compute multi-timeframe ATR ──────────────────────
        klines = market_data.get("klines", {})
        atrs = self._compute_multi_tf_atr(klines, entry)

        # ── 2. Determine volatility regime ──────────────────────
        vol_regime, vol_pctile = self._assess_volatility(atrs, klines)

        # ── 3. Get session context ──────────────────────────────
        session, session_liq, session_vol_mult = self._get_session_context()

        # ── 4. Compute support/resistance from klines + liquidity map ──
        supports, resistances = self._find_structural_levels(
            klines, liquidity_map, entry
        )

        # ── 4b. Merge absorption levels (high-confidence S/R from passive orders) ──
        if absorption and absorption.get("top_levels"):
            for lvl in absorption["top_levels"]:
                price = lvl.get("price", 0)
                side = lvl.get("side", "")
                if price > 0:
                    if side == "bid_absorption" and price < entry:
                        supports.insert(0, price)  # Highest priority
                    elif side == "ask_absorption" and price > entry:
                        resistances.insert(0, price)

        # ── 4c. Merge liquidation cluster zones ──
        if liquidation and liquidation.get("heat_zones"):
            for hz in liquidation["heat_zones"]:
                zone_high = hz.get("zone_high", 0)
                zone_low = hz.get("zone_low", 0)
                risk = hz.get("total_risk", 50)
                if risk > 60:  # Only high-risk zones matter
                    if zone_high > 0 and zone_high < entry:
                        supports.insert(0, zone_high)
                    elif zone_low > 0 and zone_low > entry:
                        resistances.insert(0, zone_low)

        # Deduplicate and re-sort after merging
        supports = sorted(set(s for s in supports if 0 < s < entry), reverse=True)[:5]
        resistances = sorted(set(r for r in resistances if r > entry))[:5]

        # ── 5. Compute volume profile levels ────────────────────
        vp = self._compute_volume_profile(klines, trades=market_data.get("trades", []))

        # ── 6. Adaptive SL placement ────────────────────────────
        regime_name = regime.get("regime", "ranging") if regime else "ranging"
        adaptive_sl = self._place_adaptive_sl(
            entry, direction, regime_name, atrs, supports, resistances, vol_regime
        )

        # ── 7. Dynamic TP targeting ─────────────────────────────
        adaptive_tp = self._place_dynamic_tp(
            entry, direction, regime_name, atrs, resistances, supports,
            vp, session_vol_mult
        )

        # ── 8. MTF SL/TP alignment check ────────────────────────
        mtf_sl_aligned, mtf_tp_aligned, mtf_score = self._check_mtf_alignment(
            entry, adaptive_sl, adaptive_tp, direction, klines
        )

        # ── 9. Intraday trend analysis ──────────────────────────
        trend, trend_strength, momentum = self._analyze_intraday_trend(klines, cumulative_delta)

        # ── 10. Compute quality score ───────────────────────────
        quality_score = self._compute_quality_score(
            signal, adaptive_sl, adaptive_tp, atrs, vol_regime, session_liq,
            mtf_score, trend_strength, regime
        )

        # ── 11. Confidence adjustment ───────────────────────────
        conf_adj = self._compute_confidence_adjustment(
            quality_score, mtf_score, session_liq, vol_regime, trend_strength
        )

        # ── 12. Position sizing guidance ────────────────────────
        risk_pct, leverage = self._suggest_position_params(
            quality_score, vol_regime, session_liq
        )

        # Compute final R:R
        sl_dist = abs(entry - adaptive_sl) if adaptive_sl else 0
        tp_dist = abs(adaptive_tp - entry) if adaptive_tp else 0
        rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0

        # Build enhancement dict
        levels = IntradayLevels(
            adaptive_sl=adaptive_sl,
            adaptive_tp=adaptive_tp,
            sl_distance_pct=round(sl_dist / entry * 100, 2) if entry else 0,
            tp_distance_pct=round(tp_dist / entry * 100, 2) if entry else 0,
            risk_reward=rr,
            mtf_sl_aligned=mtf_sl_aligned,
            mtf_tp_aligned=mtf_tp_aligned,
            mtf_alignment_score=round(mtf_score, 2),
            atr_1m=round(atrs.get("1m", 0), 6),
            atr_5m=round(atrs.get("5m", 0), 6),
            atr_15m=round(atrs.get("15m", 0), 6),
            atr_1h=round(atrs.get("1h", 0), 6),
            volatility_regime=vol_regime,
            volatility_percentile=round(vol_pctile, 1),
            nearest_support=round(supports[0], 4) if supports else 0,
            nearest_resistance=round(resistances[0], 4) if resistances else 0,
            support_distance_pct=round(abs(entry - supports[0]) / entry * 100, 2) if supports and entry else 0,
            resistance_distance_pct=round(abs(resistances[0] - entry) / entry * 100, 2) if resistances and entry else 0,
            vp_point_of_control=round(vp.get("poc", 0), 4),
            vp_value_area_high=round(vp.get("vah", 0), 4),
            vp_value_area_low=round(vp.get("val", 0), 4),
            price_in_value_area=vp.get("in_va", False),
            current_session=session,
            session_liquidity_score=round(session_liq, 2),
            session_volatility_mult=round(session_vol_mult, 2),
            intraday_trend=trend,
            trend_strength=round(trend_strength, 2),
            momentum_score=round(momentum, 2),
            signal_quality_score=round(quality_score, 1),
            quality_tier=self._quality_tier(quality_score),
            confidence_adjustment=round(conf_adj, 3),
            max_position_risk_pct=round(risk_pct, 2),
            suggested_leverage=leverage,
            intraday_stop_width=round(sl_dist / entry * 100, 2) if entry else 0,
        )

        # Update signal with enhanced levels
        signal["adaptive_sl"] = adaptive_sl
        signal["adaptive_tp"] = adaptive_tp
        # Only override SL/TP if production targets haven't already set them
        # (production targets use ALL data sources and are higher quality)
        if not signal.get("take_profit_1"):
            signal["stop_loss"] = adaptive_sl
            signal["take_profit"] = adaptive_tp
            signal["risk_reward"] = rr
            signal["sl_distance_pct"] = levels.sl_distance_pct
            signal["tp_distance_pct"] = levels.tp_distance_pct
        else:
            # Production targets are set — only update R:R based on actual SL/TP
            _sl = signal.get("stop_loss", adaptive_sl)
            _tp = signal.get("take_profit", adaptive_tp)
            _sl_dist = abs(entry - _sl) if _sl else 0
            _tp_dist = abs(_tp - entry) if _tp else 0
            signal["risk_reward"] = round(_tp_dist / _sl_dist, 2) if _sl_dist > 0 else 0
            signal["sl_distance_pct"] = round(_sl_dist / entry * 100, 2) if entry else 0
            signal["tp_distance_pct"] = round(_tp_dist / entry * 100, 2) if entry else 0
        signal["intraday"] = {
            "quality_score": levels.signal_quality_score,
            "quality_tier": levels.quality_tier,
            "volatility_regime": levels.volatility_regime,
            "session": levels.current_session,
            "mtf_aligned": levels.mtf_sl_aligned and levels.mtf_tp_aligned,
            "mtf_score": levels.mtf_alignment_score,
            "trend": levels.intraday_trend,
            "trend_strength": levels.trend_strength,
            "nearest_support": levels.nearest_support,
            "nearest_resistance": levels.nearest_resistance,
            "vp_poc": levels.vp_point_of_control,
            "session_liq": levels.session_liquidity_score,
            "confidence_adj": levels.confidence_adjustment,
            "atr_5m": levels.atr_5m,
            "suggested_leverage": levels.suggested_leverage,
            "risk_pct": levels.max_position_risk_pct,
        }
        # Apply confidence adjustment
        base_conf = signal.get("confidence", 0)
        signal["confidence"] = max(0, min(1, base_conf + conf_adj))

        logger.debug(
            "IntradayEnhance {} | SL={} TP={} RR={} | Quality={} ({}) | Session={} | Vol={}",
            signal.get("symbol", "?"), round(adaptive_sl, 4), round(adaptive_tp, 4),
            rr, levels.signal_quality_score, levels.quality_tier,
            session, vol_regime
        )

        return signal

    # ── ATR Computation ─────────────────────────────────────────

    def _compute_multi_tf_atr(self, klines: Dict, entry_price: float) -> Dict[str, float]:
        """Compute ATR for each available timeframe."""
        atrs = {}
        for tf, tf_klines in klines.items():
            if len(tf_klines) < 14:
                continue
            trs = []
            for i in range(1, min(len(tf_klines), 20)):
                h = tf_klines[i].get("high", tf_klines[i].get("price", entry_price))
                l = tf_klines[i].get("low", tf_klines[i].get("price", entry_price))
                prev_c = tf_klines[i - 1].get("close", tf_klines[i - 1].get("price", entry_price))
                tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
                trs.append(tr)
            if trs:
                # EMA-smoothed ATR (more responsive than SMA)
                atr = self._ema_atr(trs, span=14)
                atrs[tf] = atr
        return atrs

    def _ema_atr(self, trs: List[float], span: int = 14) -> float:
        """Exponential moving average of true ranges."""
        if not trs:
            return 0
        alpha = 2 / (span + 1)
        ema = trs[0]
        for tr in trs[1:]:
            ema = alpha * tr + (1 - alpha) * ema
        return ema

    # ── Volatility Assessment ───────────────────────────────────

    def _assess_volatility(self, atrs: Dict[str, float], klines: Dict) -> Tuple[str, float]:
        """Assess current volatility regime and percentile."""
        atr_5m = atrs.get("5m", 0)
        atr_15m = atrs.get("15m", 0)
        atr_1h = atrs.get("1h", 0)

        if atr_1h <= 0:
            return "normal", 50.0

        # Compare short-term vs long-term volatility
        vol_ratio = atr_5m / atr_1h if atr_1h > 0 else 1.0

        # Compute historical percentile from klines if available
        hist_5m = klines.get("5m", [])
        if len(hist_5m) >= 50:
            hist_ranges = []
            for i in range(1, min(len(hist_5m), 100)):
                h = hist_5m[i].get("high", 0)
                l = hist_5m[i].get("low", 0)
                if h > l > 0:
                    hist_ranges.append(h - l)
            if hist_ranges:
                current_range = atr_5m if atr_5m > 0 else (hist_5m[-1].get("high", 0) - hist_5m[-1].get("low", 0))
                if current_range > 0:
                    percentile = sum(1 for r in hist_ranges if r < current_range) / len(hist_ranges) * 100
                else:
                    percentile = 50.0
            else:
                percentile = 50.0
        else:
            percentile = 50.0

        # Classify regime
        if percentile >= 90:
            regime = "extreme"
        elif percentile >= 75:
            regime = "high"
        elif percentile <= 20:
            regime = "low"
        else:
            regime = "normal"

        return regime, percentile

    # ── Session Detection ───────────────────────────────────────

    def _get_session_context(self) -> Tuple[str, float, float]:
        """Get current trading session, liquidity score, and vol multiplier."""
        utc_hour = datetime.now(timezone.utc).hour

        # Check overlaps first (highest liquidity)
        if _SESSIONS["overlap"][0] <= utc_hour < _SESSIONS["overlap"][1]:
            return "overlap", self._session_liquidity["overlap"], 1.2

        for name, (start, end) in _SESSIONS.items():
            if name == "overlap":
                continue
            if start <= utc_hour < end:
                liq = self._session_liquidity[name]
                vol_mult = 1.1 if name in ("european", "us") else 0.9
                return name, liq, vol_mult

        return "off", self._session_liquidity["off"], 0.7

    # ── Structural Levels ───────────────────────────────────────

    def _find_structural_levels(
        self, klines: Dict, liquidity_map: Optional[Dict], entry: float
    ) -> Tuple[List[float], List[float]]:
        """Find nearest support and resistance levels."""
        supports = []
        resistances = []

        # 1. From liquidity map (orderbook-based)
        if liquidity_map:
            liq_supports = liquidity_map.get("support_levels", [])
            liq_resistances = liquidity_map.get("resistance_levels", [])
            supports.extend(liq_supports)
            resistances.extend(liq_resistances)

        # 2. From kline-based pivot points (use 1h klines for structural levels)
        for tf in ("1h", "4h"):
            tf_klines = klines.get(tf, [])
            if len(tf_klines) >= 10:
                # Use recent swing highs/lows
                for i in range(2, min(len(tf_klines) - 2, 50)):
                    h = tf_klines[i].get("high", 0)
                    l = tf_klines[i].get("low", 0)
                    # Swing low = support
                    if l > 0 and i >= 2:
                        prev_h = tf_klines[i-1].get("high", h)
                        next_h = tf_klines[i+1].get("high", h)
                        if l < prev_h and l < next_h:
                            supports.append(l)
                    # Swing high = resistance
                    if h > 0 and i >= 2:
                        prev_l = tf_klines[i-1].get("low", l)
                        next_l = tf_klines[i+1].get("low", l)
                        if h > prev_l and h > next_l:
                            resistances.append(h)

        # 3. Absorption levels — large passive orders defending price
        # These are high-confidence S/R because real money is defending them
        # (passed via enhance_signal's absorption parameter, stored on self for access)
        # Note: absorption data is now passed through the signal context

        # 4. Liquidation cluster zones — cascade magnet/repulsion zones
        # (passed via enhance_signal's liquidation parameter)

        # 5. Round number levels (psychological)
        if entry > 100:
            step = entry * 0.01  # 1% increments
            round_level = round(entry / step) * step
            if round_level < entry:
                supports.append(round_level)
            else:
                resistances.append(round_level)

        # Sort and deduplicate
        supports = sorted(set(s for s in supports if 0 < s < entry), reverse=True)
        resistances = sorted(set(r for r in resistances if r > entry))

        return supports[:5], resistances[:5]

    # ── Volume Profile ──────────────────────────────────────────

    def _compute_volume_profile(
        self, klines: Dict, trades: List[Dict]
    ) -> Dict[str, float]:
        """Compute volume profile: POC, value area high/low."""
        result = {"poc": 0, "vah": 0, "val": 0, "in_va": False}

        # Use 5m klines with volume for volume profile
        kline_data = klines.get("5m", [])
        if len(kline_data) < 10:
            return result

        # Build price-volume distribution
        price_volumes = []
        for kl in kline_data[-50:]:  # Last 50 candles (~4h of data)
            h = kl.get("high", 0)
            l = kl.get("low", 0)
            c = kl.get("close", 0)
            v = kl.get("volume", 0)
            if h > l > 0 and v > 0:
                mid = (h + l) / 2
                price_volumes.append((mid, v))

        if not price_volumes:
            return result

        # POC: price with highest volume
        poc_price, poc_vol = max(price_volumes, key=lambda x: x[1])

        # Value area: 70% of total volume centered on POC
        total_vol = sum(v for _, v in price_volumes)
        target_vol = total_vol * 0.7

        # Sort by distance from POC
        sorted_pvs = sorted(price_volumes, key=lambda x: abs(x[0] - poc_price))
        accumulated = 0
        va_prices = []
        for price, vol in sorted_pvs:
            accumulated += vol
            va_prices.append(price)
            if accumulated >= target_vol:
                break

        val = min(va_prices) if va_prices else poc_price * 0.99
        vah = max(va_prices) if va_prices else poc_price * 1.01

        return {"poc": poc_price, "vah": vah, "val": val, "in_va": val <= 0 <= vah}

    # ── Adaptive SL Placement ───────────────────────────────────

    def _place_adaptive_sl(
        self, entry: float, direction: str, regime: str,
        atrs: Dict[str, float], supports: List[float],
        resistances: List[float], vol_regime: str
    ) -> float:
        """
        Place SL at the best of:
        1. Below nearest support (LONG) / above nearest resistance (SHORT) + buffer
        2. ATR-based stop (regime-adjusted multiplier)
        3. Minimum % floor
        """
        # Base ATR from 5m (primary intraday timeframe)
        base_atr = atrs.get("5m", atrs.get("1m", entry * 0.005))
        if base_atr <= 0:
            base_atr = entry * 0.005

        # Regime-adjusted ATR multiplier
        atr_mult = self._sl_atr_map.get(regime, 1.8)
        atr_stop_dist = base_atr * atr_mult

        # Volatility adjustment
        vol_mult = {"low": 0.8, "normal": 1.0, "high": 1.3, "extreme": 1.5}
        atr_stop_dist *= vol_mult.get(vol_regime, 1.0)

        # Minimum stop distance (0.15% for crypto intraday)
        min_stop = entry * 0.0015
        stop_dist = max(atr_stop_dist, min_stop)

        if direction == "LONG":
            # Option 1: S/R-based stop
            sr_stop = 0
            if supports:
                # SL just below nearest support with buffer
                buffer = base_atr * 0.3
                sr_stop = supports[0] - buffer

            # Option 2: ATR-based stop
            atr_stop = entry - stop_dist

            # Choose the tighter stop that still gives room (but not too tight)
            if sr_stop > 0 and sr_stop > entry - stop_dist * 1.5:
                # S/R stop is within 1.5x ATR — use it (more structural)
                return max(sr_stop, entry - stop_dist * 1.5)
            else:
                return atr_stop
        else:
            # SHORT
            sr_stop = 0
            if resistances:
                buffer = base_atr * 0.3
                sr_stop = resistances[0] + buffer

            atr_stop = entry + stop_dist

            if sr_stop > 0 and sr_stop < entry + stop_dist * 1.5:
                return min(sr_stop, entry + stop_dist * 1.5)
            else:
                return atr_stop

    # ── Dynamic TP Placement ────────────────────────────────────

    def _place_dynamic_tp(
        self, entry: float, direction: str, regime: str,
        atrs: Dict[str, float], resistances: List[float],
        supports: List[float], vp: Dict[str, float],
        session_vol_mult: float
    ) -> float:
        """
        Dynamic TP targeting:
        1. Aim for minimum R:R of 1.5
        2. Target next S/R level if within ATR reach
        3. Use regime-adjusted ATR multiplier
        4. Adjust for session volatility
        """
        base_atr = atrs.get("5m", atrs.get("1m", entry * 0.005))
        if base_atr <= 0:
            base_atr = entry * 0.005

        # Regime-adjusted TP multiplier
        tp_mult = self._tp_atr_map.get(regime, 3.5)
        atr_tp_dist = base_atr * tp_mult * session_vol_mult

        # Minimum TP: 1.5x the SL distance
        sl_dist = abs(entry - (entry - base_atr * self._sl_atr_map.get(regime, 1.8)))
        min_tp = sl_dist * self._min_rr

        tp_dist = max(atr_tp_dist, min_tp)

        if direction == "LONG":
            # Check if there's a resistance target within reach
            target = entry + tp_dist
            if resistances:
                # Use closest resistance that's at least 1.5x SL away
                for r in resistances:
                    if r > entry + sl_dist * self._min_rr:
                        # If resistance is close to ATR target, use it
                        if abs(r - target) / target < 0.02:
                            return r
                        # If resistance is between min_tp and atr_tp, use it as TP
                        if entry + min_tp <= r <= entry + tp_dist * 1.2:
                            return r

            # Check if POC acts as a magnet
            poc = vp.get("poc", 0)
            if poc > entry + sl_dist * self._min_rr:
                # POC can be a target if above entry
                return poc

            return target
        else:
            target = entry - tp_dist
            if supports:
                for s in supports:
                    if s < entry - sl_dist * self._min_rr:
                        if abs(s - target) / max(target, 1) < 0.02:
                            return s
                        if entry - tp_dist * 1.2 <= s <= entry - min_tp:
                            return s

            poc = vp.get("poc", 0)
            if 0 < poc < entry - sl_dist * self._min_rr:
                return poc

            return target

    # ── MTF Alignment ───────────────────────────────────────────

    def _check_mtf_alignment(
        self, entry: float, sl: float, tp: float,
        direction: str, klines: Dict
    ) -> Tuple[bool, bool, float]:
        """
        Check if SL/TP placement aligns with multi-timeframe structure.
        - SL should not be violated by higher TF swings
        - TP should be reachable within higher TF trend
        """
        sl_aligned = True
        tp_aligned = True
        alignment_score = 0.5

        for tf in ("5m", "15m", "1h"):
            tf_klines = klines.get(tf, [])
            if len(tf_klines) < 5:
                continue

            # Check recent range
            recent_highs = [k.get("high", entry) for k in tf_klines[-5:]]
            recent_lows = [k.get("low", entry) for k in tf_klines[-5:]]
            tf_high = max(recent_highs)
            tf_low = min(recent_lows)

            if direction == "LONG":
                # SL should be below the higher TF recent low
                if sl > tf_low * 0.999:  # SL too close to higher TF low
                    sl_aligned = False
                # TP should not exceed higher TF ATR too much
                tf_range = tf_high - tf_low
                if tp > entry + tf_range * 2:
                    tp_aligned = False
            else:
                if sl < tf_high * 1.001:
                    sl_aligned = False
                tf_range = tf_high - tf_low
                if tp < entry - tf_range * 2:
                    tp_aligned = False

            # Trend confirmation
            closes = [k.get("close", entry) for k in tf_klines[-10:]]
            if len(closes) >= 5:
                trend_aligned = (closes[-1] > closes[-5]) if direction == "LONG" else (closes[-1] < closes[-5])
                if trend_aligned:
                    alignment_score += 0.15

        alignment_score = min(1.0, alignment_score)
        return sl_aligned, tp_aligned, alignment_score

    # ── Intraday Trend Analysis ─────────────────────────────────

    def _analyze_intraday_trend(
        self, klines: Dict, cumulative_delta: Optional[Dict]
    ) -> Tuple[str, float, float]:
        """Analyze intraday trend from 5m/15m klines and CVD."""
        trend = "sideways"
        strength = 0.5
        momentum = 0.0

        # Use 15m klines for intraday trend (good balance of noise vs signal)
        kline_data = klines.get("15m", klines.get("5m", []))
        if len(kline_data) < 10:
            return trend, strength, momentum

        closes = np.array([k.get("close", 0) for k in kline_data[-20:]])
        if closes[-1] <= 0 or closes[0] <= 0:
            return trend, strength, momentum

        # Linear regression slope for trend direction and strength
        x = np.arange(len(closes))
        coeffs = np.polyfit(x, closes, 1)
        slope = coeffs[0]
        normalized_slope = slope / closes.mean() * len(closes) if closes.mean() else 0

        # R² for trend strength
        y_pred = np.polyval(coeffs, x)
        ss_res = np.sum((closes - y_pred) ** 2)
        ss_tot = np.sum((closes - closes.mean()) ** 2)
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0

        strength = min(abs(normalized_slope) * r_squared, 1.0)

        if normalized_slope > 0.1 and r_squared > 0.3:
            trend = "bullish"
        elif normalized_slope < -0.1 and r_squared > 0.3:
            trend = "bearish"
        else:
            trend = "sideways"

        # Momentum from CVD
        if cumulative_delta:
            momentum = cumulative_delta.get("momentum", 0)
        else:
            # Price-based momentum
            momentum = normalized_slope

        return trend, strength, momentum

    # ── Quality Scoring ─────────────────────────────────────────

    def _compute_quality_score(
        self, signal: Dict, sl: float, tp: float,
        atrs: Dict[str, float], vol_regime: str, session_liq: float,
        mtf_score: float, trend_strength: float, regime: Optional[Dict]
    ) -> float:
        """
        Compute 0-100 quality score for intraday signal.
        
        Factors (weighted):
        - R:R ratio (25%)
        - MTF alignment (20%)
        - Trend strength (15%)
        - Session liquidity (10%)
        - Volatility appropriateness (10%)
        - Confidence base (10%)
        - Regime fit (10%)
        """
        entry = signal.get("entry_price", 0)
        base_conf = signal.get("confidence", 0)
        regime_name = regime.get("regime", "ranging") if regime else "ranging"

        # R:R score
        sl_dist = abs(entry - sl) if entry and sl else 0
        tp_dist = abs(tp - entry) if tp and entry else 0
        rr = tp_dist / sl_dist if sl_dist > 0 else 0
        rr_score = min(rr / 3.0, 1.0)  # 3:1 = perfect score

        # MTF alignment score (0-1)
        mtf_sc = mtf_score

        # Trend strength (0-1)
        trend_sc = trend_strength

        # Session liquidity (0-1)
        liq_sc = session_liq

        # Volatility appropriateness
        # Normal vol is ideal; extreme/low reduce quality
        vol_scores = {"low": 0.4, "normal": 1.0, "high": 0.7, "extreme": 0.3}
        vol_sc = vol_scores.get(vol_regime, 0.5)

        # Base confidence
        conf_sc = base_conf

        # Regime fit
        regime_scores = {
            "trending_up": 0.8, "trending_down": 0.8, "breakout": 0.9,
            "reversal": 0.6, "ranging": 0.5, "volatile": 0.4, "quiet": 0.3,
        }
        direction = signal.get("type", "LONG")
        # Breakout regime should match signal direction context
        regime_sc = regime_scores.get(regime_name, 0.5)
        if regime_name in ("trending_up",) and direction == "LONG":
            regime_sc = 0.95
        elif regime_name in ("trending_down",) and direction == "SHORT":
            regime_sc = 0.95
        elif regime_name in ("trending_up",) and direction == "SHORT":
            regime_sc = 0.2
        elif regime_name in ("trending_down",) and direction == "LONG":
            regime_sc = 0.2

        # Weighted composite
        score = (
            0.25 * rr_score +
            0.20 * mtf_sc +
            0.15 * trend_sc +
            0.10 * liq_sc +
            0.10 * vol_sc +
            0.10 * conf_sc +
            0.10 * regime_sc
        ) * 100

        return max(0, min(100, score))

    def _quality_tier(self, score: float) -> str:
        if score >= 75:
            return "A"
        elif score >= 55:
            return "B"
        return "C"

    # ── Confidence Adjustment ───────────────────────────────────

    def _compute_confidence_adjustment(
        self, quality_score: float, mtf_score: float,
        session_liq: float, vol_regime: str, trend_strength: float
    ) -> float:
        """
        Compute confidence adjustment (-0.2 to +0.2) based on intraday quality.
        """
        adj = 0.0

        # Quality score bonus/penalty
        if quality_score >= 75:
            adj += 0.10
        elif quality_score >= 60:
            adj += 0.05
        elif quality_score < 40:
            adj -= 0.10
        elif quality_score < 50:
            adj -= 0.05

        # MTF alignment bonus
        if mtf_score >= 0.8:
            adj += 0.05
        elif mtf_score < 0.4:
            adj -= 0.05

        # Session liquidity adjustment
        if session_liq >= 0.9:
            adj += 0.03
        elif session_liq < 0.4:
            adj -= 0.07

        # Volatility penalty for extreme conditions
        if vol_regime == "extreme":
            adj -= 0.08
        elif vol_regime == "low":
            adj -= 0.03

        # Trend strength bonus
        if trend_strength >= 0.7:
            adj += 0.03

        return max(-0.2, min(0.2, adj))

    # ── Position Sizing Guidance ────────────────────────────────

    def _suggest_position_params(
        self, quality_score: float, vol_regime: str, session_liq: float
    ) -> Tuple[float, int]:
        """Suggest risk % and leverage based on signal quality."""
        # Base risk per trade
        risk_pct = 1.0

        # Quality-based adjustment
        if quality_score >= 75:
            risk_pct = 1.2  # A-tier: slightly more
        elif quality_score >= 55:
            risk_pct = 1.0  # B-tier: standard
        else:
            risk_pct = 0.5  # C-tier: reduced

        # Volatility adjustment
        vol_adj = {"low": 1.1, "normal": 1.0, "high": 0.7, "extreme": 0.5}
        risk_pct *= vol_adj.get(vol_regime, 1.0)

        # Session adjustment
        if session_liq < 0.4:
            risk_pct *= 0.6  # Low liquidity = reduce

        risk_pct = max(0.25, min(2.0, risk_pct))

        # Leverage suggestion
        if quality_score >= 75 and vol_regime in ("normal", "low"):
            leverage = 10
        elif quality_score >= 55:
            leverage = 5
        else:
            leverage = 3

        if vol_regime == "extreme":
            leverage = min(leverage, 3)

        return risk_pct, leverage
