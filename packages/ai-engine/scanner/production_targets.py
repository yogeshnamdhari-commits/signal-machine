"""
Production-Grade Target Engine — Unified TP/SL calculation using ALL real-time data sources.

Merges:
1. ATR (base volatility) — multi-timeframe EMA-smoothed
2. Liquidity Map — orderbook bid/ask clusters (S/R from real resting orders)
3. Absorption Levels — large passive orders absorbing aggressive flow
4. Liquidation Clusters — cascade zones (magnets for price)
5. Volume Profile — POC, Value Area High/Low
6. OI Squeeze Zones — high-OI levels that trigger cascades
7. Funding Extremes — crowd positioning (contrarian targets)
8. CVD Divergence — momentum exhaustion (SL tightening)
9. Multi-TF Structure — swing highs/lows from 1h/4h
10. Session Context — liquidity windows affect target distance

Output: Multi-target system (TP1, TP2, TP3) + structural SL + trailing config.
"""
from __future__ import annotations

import time
from config import config
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from loguru import logger


@dataclass
class ProductionTargets:
    """Complete target specification for a signal."""
    # Core targets
    entry: float = 0.0
    stop_loss: float = 0.0
    take_profit_1: float = 0.0  # Conservative (high probability)
    take_profit_2: float = 0.0  # Standard (regime-based)
    take_profit_3: float = 0.0  # Aggressive (structural extension)

    # Distances
    sl_distance_pct: float = 0.0
    tp1_distance_pct: float = 0.0
    tp2_distance_pct: float = 0.0
    tp3_distance_pct: float = 0.0

    # Risk:Reward
    rr_1: float = 0.0  # R:R to TP1
    rr_2: float = 0.0  # R:R to TP2
    rr_3: float = 0.0  # R:R to TP3

    # Position sizing at each target — Fix 3: Tiered TP System with SL trailing
    tp1_exit_pct: float = 0.40   # Close 40% at TP1 (1.5R) — move SL to breakeven
    tp2_exit_pct: float = 0.40   # Close 40% at TP2 (3R) — trail SL to TP1
    tp3_exit_pct: float = 0.20   # Close remaining 20% at TP3 (5R) — runner

    # Trailing stop config
    trailing_activation: float = 0.0  # R-multiple to activate trailing
    trailing_step: float = 0.0        # Trail distance in ATR units
    breakeven_activation: float = 1.0  # Move SL to BE at this R-multiple

    # Structural context
    sl_source: str = ""  # "structural" / "atr" / "absorption" / "liquidation"
    tp1_source: str = ""  # "volume_profile" / "resistance" / "atr" / "oi_squeeze"
    tp2_source: str = ""
    tp3_source: str = ""

    # Confidence metrics
    sl_quality: float = 0.0   # 0-1 confidence in SL placement
    tp_quality: float = 0.0   # 0-1 confidence in TP placement
    data_coverage: float = 0.0  # % of data sources available

    # Support/resistance map for dashboard
    support_levels: List[float] = field(default_factory=list)
    resistance_levels: List[float] = field(default_factory=list)
    poc: float = 0.0
    vah: float = 0.0
    val: float = 0.0

    # Session
    session: str = ""
    volatility_regime: str = ""


class ProductionTargetEngine:
    """
    Computes production-grade TP/SL targets by merging all available real-time data.

    Pipeline:
    1. Compute base ATR from multi-TF klines
    2. Gather structural levels (liquidity map, absorption, liquidation, volume profile)
    3. Place SL at the most structural level (not just ATR)
    4. Compute TP1/TP2/TP3 using graduated targeting
    5. Adjust for regime, session, OI, funding, CVD
    6. Set trailing stop parameters
    """

    def __init__(self) -> None:
        # Regime-adaptive TP multipliers
        self._tp_mults = {
            "trending_bull": 3.5, "trending_bear": 3.5,
            "breakout": 4.5, "compression": 2.5,
            "volatile": 3.0, "range": 2.0,
        }
        # Regime scaling for SL (applied on top of config.risk.sl_atr_mult)
        self._regime_sl_scale = {
            "trending_bull": 0.9, "trending_bear": 0.9,
            "breakout": 1.0, "compression": 0.85,
            "volatile": 1.1, "range": 0.8,
        }
        # Minimum floors
        self._min_sl_pct = 0.0050   # 0.50% — v5: raised from 0.30% to clear MAE test zone
        self._min_tp_pct = 0.0025   # 0.25%
        self._min_rr = 1.2          # v5: lowered from 1.8 — TP1 at 1.2R for higher hit rate

        # ═══════════════════════════════════════════════════════════════
        # v5 GATE 4: Asset-class-specific SL floors (widened from v3)
        # Root cause: MAE avg 1.54% — old floors were too tight
        # v5 floors: BTC/ETH 0.7%, major alts 1.0%, others 1.5%
        # ═══════════════════════════════════════════════════════════════
        self._asset_sl_floors = {
            # Tier 1: Major (v5: raised from 0.5% to 0.7%)
            "BTCUSDT": 0.007, "ETHUSDT": 0.007,
            # Tier 2: Large cap alts (v5: raised from 0.8% to 1.0%)
            "SOLUSDT": 0.010, "BNBUSDT": 0.010, "XRPUSDT": 0.010,
            "AVAXUSDT": 0.010, "DOGEUSDT": 0.010, "ADAUSDT": 0.010,
            "LINKUSDT": 0.010, "DOTUSDT": 0.010, "MATICUSDT": 0.010,
            "UNIUSDT": 0.010, "LTCUSDT": 0.010, "ATOMUSDT": 0.010,
        }
        self._default_alt_sl_floor = 0.015  # v5: raised from 1.2% to 1.5%

        # Session multipliers (affects TP distance)
        self._session_tp_mult = {
            "overlap": 1.2, "european": 1.1, "us": 1.15,
            "asian": 0.85, "off": 0.7,
        }
        # Session SL multipliers — NY session has highest volatility
        # June 16 proof: 9/38 NY trades stopped out within 60min at 0.8% SL
        # Widening NY SL by 40% gives trades room to breathe
        self._session_sl_mult = {
            "overlap": 1.1, "european": 1.0, "us": 1.4,
            "asian": 0.9, "off": 0.85,
        }

    def _get_asset_min_sl(self, symbol: str) -> float:
        """Get minimum SL distance as a decimal fraction by asset class.

        Returns: 0.005 (0.5%) for BTC/ETH, 0.008 (0.8%) for tier2, 0.012 (1.2%) for alts.
        """
        return self._asset_sl_floors.get(symbol, self._default_alt_sl_floor)

    def compute_targets(
        self,
        entry: float,
        direction: str,
        symbol: str = "",
        regime: Optional[Dict] = None,
        market_data: Optional[Dict] = None,
        orderflow: Optional[Dict] = None,
        liquidity_map: Optional[Dict] = None,
        absorption: Optional[Dict] = None,
        liquidation: Optional[Dict] = None,
        oi_data: Optional[Dict] = None,
        funding_data: Optional[Dict] = None,
        cvd_data: Optional[Dict] = None,
        cumulative_delta: Optional[Dict] = None,
        volume_data: Optional[Dict] = None,
        smart_money: Optional[Dict] = None,
        sweep_data: Optional[Dict] = None,
        session: str = "",
        vol_regime: str = "normal",
    ) -> ProductionTargets:
        """
        Compute production-grade targets from all available data.

        Returns a ProductionTargets with TP1/TP2/TP3 + structural SL.
        """
        if entry <= 0:
            return ProductionTargets()

        t = ProductionTargets(entry=entry, session=session, volatility_regime=vol_regime)
        direction_mult = 1.0 if direction == "LONG" else -1.0

        # ═══════════════════════════════════════════════════════
        # STEP 1: Compute base ATR (multi-TF EMA-smoothed)
        # ═══════════════════════════════════════════════════════
        klines = (market_data or {}).get("klines", {})
        atr_5m = self._ema_atr(klines.get("5m", []))
        atr_15m = self._ema_atr(klines.get("15m", []))
        atr_1h = self._ema_atr(klines.get("1h", []))

        # Blend: 60% 5m + 25% 15m + 15% 1h for robustness
        if atr_5m > 0 and atr_15m > 0 and atr_1h > 0:
            atr = atr_5m * 0.60 + atr_15m * 0.25 + atr_1h * 0.15
        elif atr_5m > 0 and atr_15m > 0:
            atr = atr_5m * 0.70 + atr_15m * 0.30
        elif atr_5m > 0:
            atr = atr_5m
        elif atr_15m > 0:
            atr = atr_15m
        else:
            atr = self._fallback_atr(klines, entry)

        if atr <= 0:
            atr = entry * 0.005  # absolute fallback

        # ═══════════════════════════════════════════════════════
        # STEP 2: Gather ALL structural levels
        # ═══════════════════════════════════════════════════════
        regime_type = (regime or {}).get("regime", "range")
        # Single source of truth: config.risk.sl_atr_mult (2.5)
        base_sl_mult = config.risk.sl_atr_mult  # 2.5 — unified LONG/SHORT
        regime_scale = self._regime_sl_scale.get(regime_type, 1.0)
        sl_mult = base_sl_mult * regime_scale
        tp_mult = self._tp_mults.get(regime_type, 3.0)

        # Volatility adjustment
        vol_adj = {"low": 0.85, "normal": 1.0, "high": 1.2, "extreme": 1.4}.get(vol_regime, 1.0)

        # Session adjustment
        session_mult = self._session_tp_mult.get(session, 1.0)
        session_sl_mult = self._session_sl_mult.get(session, 1.0)

        # ATR-based reference distances — config.risk.sl_atr_mult is the single source of truth
        atr_sl_dist = atr * sl_mult * vol_adj * session_sl_mult
        atr_tp_dist = atr * tp_mult * vol_adj * session_mult

        # ── Liquidity Map levels (orderbook clusters) ──
        liq_supports = (liquidity_map or {}).get("support_levels", [])
        liq_resistances = (liquidity_map or {}).get("resistance_levels", [])
        liq_poc = (liquidity_map or {}).get("poc", 0)

        # ── Absorption levels (large passive orders) ──
        abs_levels = []
        abs_signal = (absorption or {}).get("signal", "neutral")
        abs_top = (absorption or {}).get("top_levels", [])
        for lvl in abs_top:
            p = lvl.get("price", 0)
            if p > 0:
                abs_levels.append(p)

        # ── Liquidation clusters (cascade zones) ──
        liq_clusters = []
        liq_heat_zones = (liquidation or {}).get("heat_zones", [])
        for hz in liq_heat_zones:
            low = hz.get("zone_low", 0)
            high = hz.get("zone_high", 0)
            if low > 0 and high > 0:
                liq_clusters.append({"low": low, "high": high, "risk": hz.get("total_risk", 50)})

        # ── Volume Profile ──
        poc = (volume_data or {}).get("poc", 0)
        vah = (volume_data or {}).get("vah", 0)
        val = (volume_data or {}).get("val", 0)
        if poc <= 0:
            poc = liq_poc
        # Also check intraday enhancer's volume profile
        if poc <= 0 and market_data:
            vp = self._compute_volume_profile(klines, market_data.get("trades", []))
            poc = vp.get("poc", 0)
            vah = vp.get("vah", 0)
            val = vp.get("val", 0)

        # ── Kline-based structural levels (swing highs/lows) ──
        kline_supports, kline_resistances = self._find_kline_levels(klines, entry)

        # ── OI squeeze zone ──
        oi_squeeze = (oi_data or {}).get("squeeze_risk", False)
        oi_divergence = (oi_data or {}).get("price_oi_divergence", 0)
        oi_regime = (oi_data or {}).get("oi_regime", "neutral_oi")

        # ── Funding extremes ──
        funding_rate = (funding_data or {}).get("current_rate", 0)
        funding_z = (funding_data or {}).get("z_score", 0)
        funding_extreme = (funding_data or {}).get("is_extreme", False)

        # ── CVD divergence ──
        cvd_divergence = (cvd_data or {}).get("price_delta_divergence", 0)
        cvd_momentum = (cvd_data or {}).get("delta_momentum", 0)

        # ═══════════════════════════════════════════════════════
        # STEP 3: Merge ALL supports and resistances
        # ═══════════════════════════════════════════════════════
        if direction == "LONG":
            # Support levels (for SL placement) — below entry
            all_supports = []
            all_supports.extend([(s, "liquidity_map", 0.9) for s in liq_supports if 0 < s < entry])
            all_supports.extend([(s, "absorption", 0.85) for s in abs_levels if s < entry])
            all_supports.extend([(s, "kline_structure", 0.7) for s in kline_supports if s < entry])
            # Liquidation clusters below entry (avoid SL inside them)
            liq_below = [(hz["high"], "liquidation_zone", 0.8) for hz in liq_clusters if hz["high"] < entry]

            # Resistance levels (for TP targeting) — above entry
            all_resistances = []
            all_resistances.extend([(r, "liquidity_map", 0.9) for r in liq_resistances if r > entry])
            all_resistances.extend([(r, "kline_structure", 0.7) for r in kline_resistances if r > entry])
            # POC and VAH as potential targets
            if poc > entry:
                all_resistances.append((poc, "volume_profile_poc", 0.85))
            if vah > entry:
                all_resistances.append((vah, "volume_profile_vah", 0.75))
        else:
            # SHORT: resistances for SL, supports for TP
            all_supports = []
            all_supports.extend([(s, "liquidity_map", 0.9) for s in liq_supports if s > 0])
            all_supports.extend([(s, "kline_structure", 0.7) for s in kline_supports if s > 0])

            all_resistances = []
            all_resistances.extend([(r, "liquidity_map", 0.9) for r in liq_resistances if r > entry])
            all_resistances.extend([(r, "absorption", 0.85) for r in abs_levels if r > entry])
            all_resistances.extend([(r, "kline_structure", 0.7) for r in kline_resistances if r > entry])

            # POC and VAL are TP TARGETS for SHORT, NOT SL levels
            # BUG FIX: These must NOT go into all_resistances (used for SL placement)
            # because poc < entry places SL BELOW entry = inverted SL for SHORT
            # Store them separately for TP computation only
            short_tp_targets = []
            if poc < entry:
                short_tp_targets.append((poc, "volume_profile_poc", 0.85))
            if val < entry and val > 0:
                short_tp_targets.append((val, "volume_profile_val", 0.75))

        # Sort by distance from entry
        all_supports.sort(key=lambda x: abs(x[0] - entry), reverse=True)
        all_resistances.sort(key=lambda x: abs(x[0] - entry))

        # ═══════════════════════════════════════════════════════
        # STEP 4: Place structural SL
        # ═══════════════════════════════════════════════════════
        sl_price, sl_source, sl_quality = self._place_structural_sl(
            entry, direction, atr_sl_dist, all_supports, all_resistances, atr
        )

        # ═══════════════════════════════════════════════════════
        # STEP 5: Compute TP1/TP2/TP3
        # ═══════════════════════════════════════════════════════
        sl_dist = abs(entry - sl_price)

        tp1, tp1_source = self._compute_tp1(
            entry, direction, sl_dist, atr, all_resistances if direction == "LONG" else all_supports,
            poc, vah, val, regime_type, session_mult
        )

        tp2, tp2_source = self._compute_tp2(
            entry, direction, sl_dist, atr, tp1, atr_tp_dist,
            all_resistances if direction == "LONG" else all_supports,
            poc, vah, val, regime_type, session_mult
        )

        tp3, tp3_source = self._compute_tp3(
            entry, direction, sl_dist, atr, tp1, tp2, atr_tp_dist,
            all_resistances if direction == "LONG" else all_supports,
            poc, vah, val, regime_type, oi_squeeze, funding_extreme
        )

        # ═══════════════════════════════════════════════════════
        # STEP 6: Apply adjustments
        # ═══════════════════════════════════════════════════════

        # ═══════════════════════════════════════════════════════
        # STEP 6: ORDERFLOW-ENHANCED ADJUSTMENTS
        # ═══════════════════════════════════════════════════════
        # This is where real-time production-grade orderflow data
        # directly shapes SL/TP for maximum trade profit.

        # ── 6a: Flow-strength SL tightening/widening ──
        # When orderflow strongly confirms our direction → widen SL (let it breathe)
        # When orderflow opposes our direction → tighten SL (cut losers fast)
        flow_data = orderflow or {}
        flow_ratio = flow_data.get("flow_ratio", 0.5)
        flow_strength = abs(flow_ratio - 0.5) * 2  # 0 = neutral, 1 = extreme

        if direction == "LONG":
            # Strong taker buying (flow_ratio > 0.6) = confirm → widen SL by 15%
            # Strong taker selling (flow_ratio < 0.4) = oppose → tighten SL by 20%
            if flow_ratio > 0.60:
                widen = atr * 0.15 * flow_strength
                sl_price = sl_price - widen
                sl_source = f"flow_widened (buy:{flow_ratio:.2f})"
            elif flow_ratio < 0.40:
                tighten = atr * 0.20 * flow_strength
                sl_price = sl_price + tighten
                sl_source = f"flow_tightened (sell:{flow_ratio:.2f})"
        else:  # SHORT
            if flow_ratio < 0.40:
                widen = atr * 0.15 * flow_strength
                sl_price = sl_price + widen
                sl_source = f"flow_widened (sell:{flow_ratio:.2f})"
            elif flow_ratio > 0.60:
                tighten = atr * 0.20 * flow_strength
                sl_price = sl_price - tighten
                sl_source = f"flow_tightened (buy:{flow_ratio:.2f})"

        # ── 6b: CVD divergence — dynamic SL adjustment ──
        # CVD divergence is the strongest signal of institutional positioning
        if cvd_divergence != 0:
            if direction == "LONG" and cvd_divergence < -0.3:
                # Bearish CVD divergence — price rising but volume selling = distribution
                # Tighten SL aggressively (25% of ATR per unit of divergence)
                tighten = min(abs(cvd_divergence) * atr * 0.4, sl_dist * 0.25)
                sl_price = sl_price + tighten
                sl_source = f"cvd_diverge_tight ({cvd_divergence:.2f})"
                # Also tighten TP — don't expect full move if distribution happening
                tp1 = tp1 - atr * 0.2
                tp2 = tp2 - atr * 0.3
            elif direction == "SHORT" and cvd_divergence > 0.3:
                tighten = min(abs(cvd_divergence) * atr * 0.4, sl_dist * 0.25)
                sl_price = sl_price - tighten
                sl_source = f"cvd_diverge_tight ({cvd_divergence:.2f})"
                tp1 = tp1 + atr * 0.2
                tp2 = tp2 + atr * 0.3
            elif direction == "LONG" and cvd_divergence > 0.4:
                # Bullish CVD divergence — volume buying = accumulation
                # Widen SL (institutional support) and extend TP
                widen = min(cvd_divergence * atr * 0.3, sl_dist * 0.15)
                sl_price = sl_price - widen
                sl_source = f"cvd_diverge_wide ({cvd_divergence:.2f})"
                tp1 = tp1 + atr * 0.15
                tp2 = tp2 + atr * 0.25
            elif direction == "SHORT" and cvd_divergence < -0.4:
                widen = min(abs(cvd_divergence) * atr * 0.3, sl_dist * 0.15)
                sl_price = sl_price + widen
                sl_source = f"cvd_diverge_wide ({cvd_divergence:.2f})"
                tp1 = tp1 - atr * 0.15
                tp2 = tp2 - atr * 0.25

        # ── 6c: Smart money accumulation/distribution ──
        sm_data = smart_money or {}
        accum = sm_data.get("accumulation_score", 0)
        distrib = sm_data.get("distribution_score", 0)
        inst_flow = sm_data.get("institutional_flow", 0)
        sweep_conf = sm_data.get("sweep_confidence", 0)
        abs_conf = sm_data.get("absorption_confidence", 0)

        if direction == "LONG":
            # Strong accumulation = institutional buying = widen SL + extend TP
            if accum > 0.6:
                sl_price -= atr * 0.12
                tp1 += atr * 0.15
                tp2 += atr * 0.20
                sl_source = f"smart_money_accum ({accum:.2f})"
            # Strong distribution = institutional selling = tighten SL
            elif distrib > 0.6:
                sl_price += atr * 0.15
                sl_source = f"smart_money_distrib ({distrib:.2f})"
                tp1 -= atr * 0.10
        else:
            if distrib > 0.6:
                sl_price += atr * 0.12
                tp1 -= atr * 0.15
                tp2 -= atr * 0.20
                sl_source = f"smart_money_distrib ({distrib:.2f})"
            elif accum > 0.6:
                sl_price -= atr * 0.15
                sl_source = f"smart_money_accum ({accum:.2f})"
                tp1 += atr * 0.10

        # ── 6d: Sweep detection — place SL beyond sweep zones ──
        sweep_data_check = sweep_data or {}
        sweep_detected = sweep_data_check.get("sweep_detected", False)
        sweep_direction = sweep_data_check.get("sweep_direction", "")
        sweep_intensity = sweep_data_check.get("sweep_intensity", 0)

        if sweep_detected and sweep_intensity > 0.5:
            sweep_buffer = atr * 0.3 * sweep_intensity
            if direction == "LONG" and sweep_direction == "down":
                # Downward sweep = stop hunt below → SL should be below the sweep
                sl_price = min(sl_price, entry - atr * 1.2)
                sl_source = f"sweep_protected (int={sweep_intensity:.2f})"
            elif direction == "SHORT" and sweep_direction == "up":
                sl_price = max(sl_price, entry + atr * 1.2)
                sl_source = f"sweep_protected (int={sweep_intensity:.2f})"

        # ── 6e: ORDER BLOCK SL — Place SL below the OB that validates the trade ──
        # SMC principle: SL goes below the OB that confirms the entry thesis.
        # If price doesn't breach the OB, the thesis is still valid.
        # This is the #1 fix for 56.8% of losses hitting in first 30 minutes.
        abs_data = absorption or {}
        abs_signal_type = abs_data.get("signal", "neutral")
        abs_top_levels = abs_data.get("top_levels", [])

        ob_placed = False
        if direction == "LONG" and abs_signal_type == "absorption_buy":
            # Passive buyers absorbing = OB below entry → SL below OB
            for lvl in abs_top_levels:
                p = lvl.get("price", 0)
                confidence = lvl.get("confidence", 0.8)
                if p > 0 and p < entry:
                    # SL below the OB level with ATR buffer
                    ob_sl = p - atr * 0.25  # 25% ATR buffer below OB
                    # Must be below entry and at least 0.5 ATR away
                    if ob_sl < entry and (entry - ob_sl) >= 0.5 * atr:
                        sl_price = ob_sl
                        sl_source = f"order_block_support ({p:.4f}, conf={confidence:.2f})"
                        ob_placed = True
                        break

        elif direction == "SHORT" and abs_signal_type == "absorption_sell":
            # Passive sellers absorbing = OB above entry → SL above OB
            for lvl in abs_top_levels:
                p = lvl.get("price", 0)
                confidence = lvl.get("confidence", 0.8)
                if p > 0 and p > entry:
                    ob_sl = p + atr * 0.25
                    if ob_sl > entry and (ob_sl - entry) >= 0.5 * atr:
                        sl_price = ob_sl
                        sl_source = f"order_block_resist ({p:.4f}, conf={confidence:.2f})"
                        ob_placed = True
                        break

        # If no absorption OB found, try liquidity map levels as OB proxy
        if not ob_placed:
            liq_map = (market_data or {}).get("liquidity_map", {})
            if direction == "LONG":
                supports = liq_map.get("support_levels", [])
                for s in supports:
                    if s > 0 and s < entry:
                        ob_sl = s - atr * 0.20
                        if ob_sl < entry and (entry - ob_sl) >= 0.5 * atr:
                            sl_price = ob_sl
                            sl_source = f"liq_ob_support ({s:.4f})"
                            ob_placed = True
                            break
            else:
                resistances = liq_map.get("resistance_levels", [])
                for r in resistances:
                    if r > 0 and r > entry:
                        ob_sl = r + atr * 0.20
                        if ob_sl > entry and (ob_sl - entry) >= 0.5 * atr:
                            sl_price = ob_sl
                            sl_source = f"liq_ob_resist ({r:.4f})"
                            ob_placed = True
                            break

        # ── 6f: Liquidation cascade zones — extend TP to cascade targets ──
        liq_data_adj = liquidation or {}
        heat_zones = liq_data_adj.get("heat_zones", [])
        cascade_active = liq_data_adj.get("cascade_active", False)
        cascade_side = liq_data_adj.get("cascade_side", "")
        cascade_intensity = liq_data_adj.get("cascade_intensity", 0)

        if cascade_active and cascade_intensity > 0.3:
            if direction == "LONG" and cascade_side == "short_liquidation":
                # Short liquidation cascade = price rocketing up → extend TP3
                cascade_ext = atr * cascade_intensity * 2.0
                tp3 = max(tp3, entry + atr_tp_dist * 1.8 + cascade_ext)
                tp3_source = f"cascade_ext ({cascade_intensity:.2f})"
            elif direction == "SHORT" and cascade_side == "long_liquidation":
                cascade_ext = atr * cascade_intensity * 2.0
                tp3 = min(tp3, entry - atr_tp_dist * 1.8 - cascade_ext)
                tp3_source = f"cascade_ext ({cascade_intensity:.2f})"

        # ── 6g: Exchange flow percentile — high conviction = wider targets ──
        exchange_flow_data = (orderflow or {})
        flow_percentile = exchange_flow_data.get("flow_percentile", 50)
        if flow_percentile > 85 or flow_percentile < 15:
            # Extreme flow conviction — extend TP by 20%
            extension = atr * 0.2
            if direction == "LONG":
                tp2 += extension
                tp3 += extension * 1.5
            else:
                tp2 -= extension
                tp3 -= extension * 1.5

        # OI squeeze: extend TP3 if squeeze risk detected
        if oi_squeeze and direction == "LONG":
            tp3 = max(tp3, entry + atr_tp_dist * 1.5)
            tp3_source = "oi_squeeze_extended"
        elif oi_squeeze and direction == "SHORT":
            tp3 = min(tp3, entry - atr_tp_dist * 1.5)
            tp3_source = "oi_squeeze_extended"

        # Funding extreme: crowd positioning → extend TP
        if funding_extreme:
            funding_adj = atr * 0.5  # extend TP by 0.5 ATR
            if direction == "LONG" and funding_rate < 0:
                # Negative funding = crowd short → contrarian LONG gets extension
                tp3 = tp3 + funding_adj
                tp3_source = "funding_extended"
            elif direction == "SHORT" and funding_rate > 0:
                tp3 = tp3 - funding_adj
                tp3_source = "funding_extended"

        # ═══════════════════════════════════════════════════════
        # STEP 7: Enforce minimum floors and R:R
        # ═══════════════════════════════════════════════════════
        sl_dist = abs(entry - sl_price)

        # ═══════════════════════════════════════════════════════════════
        # v5: WIDER SL — clear the MAE test zone
        # Root cause: MAE avg 1.54% vs SL avg 1.93% — only 0.39% buffer
        # 82% of losses went straight to SL without ever being profitable
        # FIX: MIN SL = max(ATR × 1.8, asset_floor, absolute_floor 0.50%)
        # Wider SL = smaller position for same risk = more TP hits
        # ═══════════════════════════════════════════════════════════════
        _asset_floor_pct = self._get_asset_min_sl(symbol)  # 0.5% BTC, 0.8% tier2, 1.2% alts
        # v5: raised ATR floor from 1.2× to 1.8× to clear the natural test zone
        min_sl_dist = max(
            atr * 1.8,                              # v5: Beyond MAE zone: ≥ 1.8 ATR
            entry * _asset_floor_pct,               # Asset-class floor (0.5%–1.2%)
            entry * self._min_sl_pct,               # v5: Absolute floor (0.50%)
        )
        if sl_dist < min_sl_dist:
            if direction == "LONG":
                sl_price = entry - min_sl_dist
            else:
                sl_price = entry + min_sl_dist
            sl_dist = min_sl_dist
            sl_source = "floor"

        # Ensure TP1 meets minimum R:R
        tp1_dist = abs(tp1 - entry)
        min_tp1_dist = max(entry * self._min_tp_pct, sl_dist * self._min_rr)
        if tp1_dist < min_tp1_dist:
            if direction == "LONG":
                tp1 = entry + min_tp1_dist
            else:
                tp1 = entry - min_tp1_dist
            tp1_dist = min_tp1_dist
            tp1_source = "floor"

        # TP2 must be > TP1
        tp2_dist = abs(tp2 - entry)
        if tp2_dist <= tp1_dist:
            if direction == "LONG":
                tp2 = entry + tp1_dist * 1.8
            else:
                tp2 = entry - tp1_dist * 1.8
            tp2_dist = abs(tp2 - entry)
            tp2_source = "scaled"

        # TP3 must be > TP2
        tp3_dist = abs(tp3 - entry)
        if tp3_dist <= tp2_dist:
            if direction == "LONG":
                tp3 = entry + tp2_dist * 1.5
            else:
                tp3 = entry - tp2_dist * 1.5
            tp3_dist = abs(tp3 - entry)
            tp3_source = "scaled"

        # ═══════════════════════════════════════════════════════
        # STEP 7a: SL DISTANCE CAP — Tighten excessively wide SLs
        # If structural SL is > 5% from entry, replace with ATR-based SL
        # ═══════════════════════════════════════════════════════
        sl_dist_check = abs(entry - sl_price)
        sl_dist_pct_check = sl_dist_check / entry * 100 if entry else 0
        max_sl_pct = 5.0  # hard cap
        if sl_dist_pct_check > max_sl_pct:
            logger.warning(
                "⚠️ SL_TOO_WIDE: {} SL {} is {:.1f}% from entry (max {}%) — tightening to ATR",
                direction, round(sl_price, 6), sl_dist_pct_check, max_sl_pct,
            )
            if direction == "LONG":
                sl_price = entry - atr_sl_dist
            else:
                sl_price = entry + atr_sl_dist
            sl_source = "atr_cap"

        # ═══════════════════════════════════════════════════════
        # STEP 7b: FINAL SL DIRECTION ENFORCEMENT
        # June 16 proof: 5 SHORT trades had SL BELOW entry (inverted)
        # This is the last safety net — override any SL on the wrong side
        # ═══════════════════════════════════════════════════════
        if direction == "LONG" and sl_price >= entry:
            logger.warning(
                "🚫 SL_INVERSION_FIX: LONG SL {} >= entry {} → overriding to ATR floor",
                round(sl_price, 6), round(entry, 6),
            )
            sl_price = entry - min_sl_dist
            sl_source = "inversion_fix"
        elif direction == "SHORT" and sl_price <= entry:
            logger.warning(
                "🚫 SL_INVERSION_FIX: SHORT SL {} <= entry {} → overriding to ATR ceiling",
                round(sl_price, 6), round(entry, 6),
            )
            sl_price = entry + min_sl_dist
            sl_source = "inversion_fix"

        # ═══════════════════════════════════════════════════════
        # STEP 8: Fill ProductionTargets
        # ═══════════════════════════════════════════════════════
        t.stop_loss = round(sl_price, 8)
        t.take_profit_1 = round(tp1, 8)
        t.take_profit_2 = round(tp2, 8)
        t.take_profit_3 = round(tp3, 8)

        t.sl_distance_pct = round(sl_dist / entry * 100, 3) if entry else 0
        t.tp1_distance_pct = round(tp1_dist / entry * 100, 3) if entry else 0
        t.tp2_distance_pct = round(tp2_dist / entry * 100, 3) if entry else 0
        t.tp3_distance_pct = round(tp3_dist / entry * 100, 3) if entry else 0

        t.rr_1 = round(tp1_dist / sl_dist, 2) if sl_dist > 0 else 0
        t.rr_2 = round(tp2_dist / sl_dist, 2) if sl_dist > 0 else 0
        t.rr_3 = round(tp3_dist / sl_dist, 2) if sl_dist > 0 else 0

        t.sl_source = sl_source
        t.tp1_source = tp1_source
        t.tp2_source = tp2_source
        t.tp3_source = tp3_source

        # v5: Trailing stop config — activate EARLY at +0.5% MFE (not at TP2)
        t.trailing_activation = 0.50  # v5: Start trailing at +0.5% MFE (was 3.0R)
        t.trailing_step = 1.0          # v5: Trail at 1.0× ATR behind price (was 0.60)
        t.breakeven_activation = 0.30  # v5: Move SL to BE at +0.3% MFE (was 1.0R)
        # v5: Tiered exit percentages — closer TP1 for higher hit rate
        t.tp1_exit_pct = 0.35  # v5: Close 35% at TP1 (1.2R) — was 30% at 1.0R
        t.tp2_exit_pct = 0.40  # Close 40% at TP2 (3R) — unchanged
        t.tp3_exit_pct = 0.25  # v5: Close 25% at TP3 (5R) — was 30%

        # Quality scores
        data_sources = [bool(liquidity_map), bool(absorption), bool(liquidation),
                        bool(oi_data), bool(funding_data), bool(cvd_data),
                        bool(volume_data), bool(smart_money), bool(sweep_data)]
        t.data_coverage = sum(data_sources) / len(data_sources)
        t.sl_quality = min(0.5 + sl_dist / atr * 0.2 + (1 if "structural" in sl_source or "absorption" in sl_source else 0) * 0.3, 1.0)
        t.tp_quality = min(0.5 + t.rr_2 / 3.0 * 0.3 + t.data_coverage * 0.2, 1.0)

        # Support/Resistance map for dashboard
        t.support_levels = [round(s, 4) for s, *_ in all_supports[:5]] if direction == "LONG" else [round(s, 4) for s, *_ in all_resistances[:5]]
        t.resistance_levels = [round(r, 4) for r, *_ in all_resistances[:5]] if direction == "LONG" else [round(r, 4) for r, *_ in all_supports[:5]]
        t.poc = round(poc, 4)
        t.vah = round(vah, 4)
        t.val = round(val, 4)

        logger.debug(
            "🎯 {} {} | SL={} ({}) | TP1={} ({}) TP2={} ({}) TP3={} ({}) | R:R {}/{}/{} | Coverage={:.0%}",
            direction, (market_data or {}).get("symbol", "?"),
            round(sl_price, 4), sl_source,
            round(tp1, 4), tp1_source,
            round(tp2, 4), tp2_source,
            round(tp3, 4), tp3_source,
            t.rr_1, t.rr_2, t.rr_3,
            t.data_coverage,
        )

        return t

    # ── SL Placement ──────────────────────────────────────────

    def _place_structural_sl(
        self,
        entry: float,
        direction: str,
        atr_sl_dist: float,
        supports: List[Tuple[float, str, float]],
        resistances: List[Tuple[float, str, float]],
        atr: float,
    ) -> Tuple[float, str, float]:
        """
        Place SL at the most structural level available.

        Priority:
        1. Absorption level (large passive order defending)
        2. Liquidity map cluster (orderbook resting orders)
        3. Kline structure (swing low/high)
        4. ATR-based (regime-adjusted)
        """
        buffer = atr * 0.25  # 25% ATR buffer beyond structural level

        if direction == "LONG":
            # Look for structural supports below entry
            for price, source, confidence in supports:
                # SL should be below the support level
                candidate_sl = price - buffer
                dist = entry - candidate_sl
                # Must be within 2x ATR (not too far) and at least 0.5x ATR (not too tight)
                if 0.5 * atr_sl_dist <= dist <= 2.5 * atr_sl_dist:
                    return candidate_sl, f"structural_{source}", confidence
                # Also accept if within ATR range but with good confidence
                if dist <= atr_sl_dist * 1.5 and confidence >= 0.8:
                    return candidate_sl, f"structural_{source}", confidence

            # Fallback: ATR-based
            return entry - atr_sl_dist, "atr", 0.5
        else:
            # SHORT: look for resistances ABOVE entry
            for price, source, confidence in resistances:
                # SAFETY: Skip levels at or below entry — they would invert the SL
                if price <= entry:
                    continue
                candidate_sl = price + buffer
                # SAFETY: Final check — SL must be above entry for SHORT
                if candidate_sl <= entry:
                    continue
                dist = candidate_sl - entry
                if 0.5 * atr_sl_dist <= dist <= 2.5 * atr_sl_dist:
                    return candidate_sl, f"structural_{source}", confidence
                if dist <= atr_sl_dist * 1.5 and confidence >= 0.8:
                    return candidate_sl, f"structural_{source}", confidence

            return entry + atr_sl_dist, "atr", 0.5

    # ── TP1: Conservative (high probability) ──────────────────

    def _compute_tp1(
        self,
        entry: float,
        direction: str,
        sl_dist: float,
        atr: float,
        targets: List[Tuple[float, str, float]],
        poc: float,
        vah: float,
        val: float,
        regime: str,
        session_mult: float,
    ) -> Tuple[float, str]:
        """
        TP1: First take-profit target.
        - Targets nearest structural level that gives min R:R
        - Falls back to 1.5x SL distance
        """
        min_tp = sl_dist * 1.5  # Minimum R:R of 1.5

        if direction == "LONG":
            # Look for nearest resistance/target above entry
            for price, source, conf in targets:
                dist = price - entry
                if dist >= min_tp and dist <= atr * 5:
                    return price, source

            # Fallback: 1.5x SL
            return entry + min_tp, "atr_floor"
        else:
            for price, source, conf in targets:
                dist = entry - price
                if dist >= min_tp and dist <= atr * 5:
                    return price, source

            return entry - min_tp, "atr_floor"

    # ── TP2: Standard (regime-based) ─────────────────────────

    def _compute_tp2(
        self,
        entry: float,
        direction: str,
        sl_dist: float,
        atr: float,
        tp1: float,
        atr_tp_dist: float,
        targets: List[Tuple[float, str, float]],
        poc: float,
        vah: float,
        val: float,
        regime: str,
        session_mult: float,
    ) -> Tuple[float, str]:
        """
        TP2: Standard target.
        - Next structural level beyond TP1
        - Or regime-based ATR target
        - POC as magnet
        """
        tp1_dist = abs(tp1 - entry)

        if direction == "LONG":
            # Look for next resistance beyond TP1
            for price, source, conf in targets:
                dist = price - entry
                if dist > tp1_dist * 1.2 and dist <= atr_tp_dist * 1.5:
                    return price, source

            # POC as magnet if above entry and beyond TP1
            if poc > entry + tp1_dist * 1.2:
                return poc, "volume_profile_poc"

            # VAH as target
            if vah > entry + tp1_dist * 1.2:
                return vah, "volume_profile_vah"

            # Fallback: ATR-based
            return entry + atr_tp_dist, "atr"
        else:
            for price, source, conf in targets:
                dist = entry - price
                if dist > tp1_dist * 1.2 and dist <= atr_tp_dist * 1.5:
                    return price, source

            if poc > 0 and poc < entry - tp1_dist * 1.2:
                return poc, "volume_profile_poc"

            if val > 0 and val < entry - tp1_dist * 1.2:
                return val, "volume_profile_val"

            return entry - atr_tp_dist, "atr"

    # ── TP3: Aggressive (structural extension) ───────────────

    def _compute_tp3(
        self,
        entry: float,
        direction: str,
        sl_dist: float,
        atr: float,
        tp1: float,
        tp2: float,
        atr_tp_dist: float,
        targets: List[Tuple[float, str, float]],
        poc: float,
        vah: float,
        val: float,
        regime: str,
        oi_squeeze: bool,
        funding_extreme: bool,
    ) -> Tuple[float, str]:
        """
        TP3: Aggressive target.
        - Furthest structural level
        - Extended ATR target (2-3x ATR)
        - OI squeeze / funding extreme extensions
        """
        tp2_dist = abs(tp2 - entry)
        tp3_min = tp2_dist * 1.3

        # Extension multiplier for aggressive targets
        ext_mult = 1.0
        if regime in ("trending_bull", "trending_bear"):
            ext_mult = 1.3  # Trending markets can extend further
        elif regime == "breakout":
            ext_mult = 1.5  # Breakouts can run
        if oi_squeeze:
            ext_mult *= 1.2
        if funding_extreme:
            ext_mult *= 1.1

        if direction == "LONG":
            # Look for furthest reasonable target
            best = None
            for price, source, conf in targets:
                dist = price - entry
                if dist >= tp3_min and dist <= atr_tp_dist * ext_mult * 2:
                    if best is None or dist > abs(best[0] - entry):
                        best = (price, source)

            if best:
                return best

            # Fallback: extended ATR
            return entry + atr_tp_dist * ext_mult, "atr_extended"
        else:
            best = None
            for price, source, conf in targets:
                dist = entry - price
                if dist >= tp3_min and dist <= atr_tp_dist * ext_mult * 2:
                    if best is None or dist > abs(entry - best[0]):
                        best = (price, source)

            if best:
                return best

            return entry - atr_tp_dist * ext_mult, "atr_extended"

    # ── Helpers ───────────────────────────────────────────────

    def _ema_atr(self, kline_list: list, max_bars: int = 20, span: int = 14) -> float:
        """Compute EMA-smoothed ATR from kline data."""
        trs = []
        for i in range(1, min(len(kline_list), max_bars)):
            h = kline_list[i].get("high", kline_list[i].get("price", 0))
            l = kline_list[i].get("low", kline_list[i].get("price", 0))
            prev_c = kline_list[i - 1].get("close", kline_list[i - 1].get("price", 0))
            if h > 0 and l > 0:
                trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
        if not trs:
            return 0
        alpha = 2 / (span + 1)
        ema = trs[0]
        for tr in trs[1:]:
            ema = alpha * tr + (1 - alpha) * ema
        return ema

    def _fallback_atr(self, klines: Dict, entry: float) -> float:
        """Fallback ATR from trade buffer."""
        all_klines = []
        for tf_klines in klines.values():
            all_klines.extend(tf_klines)
        if not all_klines:
            return entry * 0.005
        highs = [k.get("high", 0) for k in all_klines[-20:]]
        lows = [k.get("low", 0) for k in all_klines[-20:]]
        if highs and lows and max(highs) > 0:
            return (max(highs) - min(l for l in lows if l > 0)) / len(highs) if len(highs) > 0 else entry * 0.005
        return entry * 0.005

    def _compute_volume_profile(self, klines: Dict, trades: list) -> Dict[str, float]:
        """Compute volume profile from klines (POC, VAH, VAL)."""
        result = {"poc": 0, "vah": 0, "val": 0}
        kline_data = klines.get("5m", [])
        if len(kline_data) < 10:
            return result

        price_volumes = []
        for kl in kline_data[-50:]:
            h = kl.get("high", 0)
            l = kl.get("low", 0)
            v = kl.get("volume", 0)
            if h > l > 0 and v > 0:
                mid = (h + l) / 2
                price_volumes.append((mid, v))

        if not price_volumes:
            return result

        poc_price, _ = max(price_volumes, key=lambda x: x[1])
        total_vol = sum(v for _, v in price_volumes)
        target_vol = total_vol * 0.7
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

        return {"poc": poc_price, "vah": vah, "val": val}

    def _find_kline_levels(self, klines: Dict, entry: float) -> Tuple[List[float], List[float]]:
        """Find swing highs/lows from 1h/4h klines."""
        supports = []
        resistances = []

        for tf in ("1h", "4h"):
            tf_klines = klines.get(tf, [])
            if len(tf_klines) < 10:
                continue
            for i in range(2, min(len(tf_klines) - 2, 50)):
                h = tf_klines[i].get("high", 0)
                l = tf_klines[i].get("low", 0)
                if l > 0 and i >= 2:
                    prev_h = tf_klines[i - 1].get("high", h)
                    next_h = tf_klines[i + 1].get("high", h)
                    if l < prev_h and l < next_h:
                        supports.append(l)
                if h > 0 and i >= 2:
                    prev_l = tf_klines[i - 1].get("low", l)
                    next_l = tf_klines[i + 1].get("low", l)
                    if h > prev_l and h > next_l:
                        resistances.append(h)

        supports = sorted(set(s for s in supports if 0 < s < entry), reverse=True)
        resistances = sorted(set(r for r in resistances if r > entry))
        return supports[:5], resistances[:5]
