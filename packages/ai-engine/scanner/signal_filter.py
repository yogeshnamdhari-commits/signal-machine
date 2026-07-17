"""
Signal Filter — 8-layer quality gate for signal validation.
Applies dynamic confidence thresholds, OI confirmation, volume filtering,
funding crowd detection, trend strength, directional bias correction,
confidence calibration, and adaptive weight learning.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger


class SignalFilter:
    """8-priority signal quality filter applied before position entry."""

    def __init__(self) -> None:
        # P1: Dynamic confidence threshold
        # Phase 1: Adjusted to 0.75 — balance between quality and signal flow
        self._min_confidence = 0.70  # Phase 1: was 0.85 — 0.70 balances quality + signal flow
        self._confidence_history: List[Dict] = []  # {confidence, won, pnl}
        self._max_history = 200

        # P2: OI confirmation thresholds
        self._min_oi_change_pct = 1.0  # ↑ Raised from 0.5 — need stronger OI confirmation

        # P3: Volume percentile filter
        self._volume_percentile_threshold = 75.0  # ↑ Raised from 70 — only top 25% by volume
        self._symbol_volumes: Dict[str, float] = {}

        # P4: Funding crowd thresholds
        self._funding_crowd_long_max = 0.0003   # ↓ Tightened from 0.0005 — reject crowded LONGs earlier
        self._funding_crowd_short_min = -0.0003  # ↑ Tightened from -0.0005

        # P6: Trend strength threshold
        self._min_trend_score = 0.30  # ↑ Raised from 0.25 — reject more sideways trades

        # P7: Confidence calibration buckets
        self._calibration_buckets: Dict[str, Dict] = defaultdict(lambda: {"wins": 0, "total": 0, "pnl": 0.0})

        # P8: Adaptive factor weights (updated from trade history)
        self._factor_performance: Dict[str, Dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "total_pnl": 0.0})
        self._last_adaptive_update = 0.0
        self._adaptive_update_interval = 120.0  # Update every 2 min

        # P9: Symbol blacklist — banned after repeated losses
        self._symbol_losses: Dict[str, List[float]] = defaultdict(list)  # symbol → [pnl, pnl, ...]
        self._blacklisted_symbols: set = set()
        self._max_losses_before_blacklist = 2  # ↓ Ban after 2 losses (was 3) — faster rejection
        self._max_loss_per_symbol = -40.0      # ↓ Ban at $40 total loss (was $60)

        # ── PERMANENT TOXIC SYMBOL BLACKLIST (from DB analysis) ──
        # These symbols have >10 trades AND negative avg PnL AND negative total PnL
        _TOXIC = {
            "AIAUSDT", "ENAUSDT", "LABUSDT", "DOGEUSDT", "ARUSDT",
            "PORTALUSDT", "STOUSDT", "TSTUSDT", "GRASSUSDT", "MYXUSDT",
            "GUAUSDT", "INUSDT", "MBOXUSDT", "NEARUSDT", "LDOUSDT",
            "HUSDT", "WLDUSDT", "ZECUSDT", "LAUSDT", "JTOUSDT",
        }
        self._blacklisted_symbols.update(_TOXIC)
        if _TOXIC:
            logger.info("🚫 PERMANENT BLACKLIST: {} toxic symbols loaded ({} total)",
                        len(_TOXIC), len(self._blacklisted_symbols))

        # Callback for state persistence (set by engine)
        self._on_state_change = None  # type: Optional[Callable]

    def update_volume_map(self, vol_map: Dict[str, float]) -> None:
        """Update 24h volume data for all symbols (called from engine)."""
        self._symbol_volumes = vol_map

    def record_trade_outcome(self, trade: Dict) -> None:
        """Record a completed trade for adaptive learning (P7 + P8)."""
        conf = trade.get("confidence", 0)
        pnl = trade.get("pnl", 0)
        won = pnl > 0

        # P7: Record in calibration bucket
        bucket = f"{int(conf * 10) * 10}-{int(conf * 10) * 10 + 10}"
        self._calibration_buckets[bucket]["total"] += 1
        self._calibration_buckets[bucket]["wins"] += 1 if won else 0
        self._calibration_buckets[bucket]["pnl"] += pnl

        # P8: Record factor performance
        factors = trade.get("confirmation_factors", [])
        for f in factors:
            self._factor_performance[f]["wins"] += 1 if won else 0
            self._factor_performance[f]["losses"] += 0 if won else 1
            self._factor_performance[f]["total_pnl"] += pnl

        # P1: Record for dynamic threshold
        self._confidence_history.append({
            "confidence": conf, "won": won, "pnl": pnl
        })
        if len(self._confidence_history) > self._max_history:
            self._confidence_history = self._confidence_history[-self._max_history:]

        # P9: Track per-symbol losses and blacklist
        sym = trade.get("symbol", "")
        if sym and pnl < 0:
            self._symbol_losses[sym].append(pnl)
            total_loss = sum(self._symbol_losses[sym])
            if len(self._symbol_losses[sym]) >= self._max_losses_before_blacklist:
                self._blacklisted_symbols.add(sym)
                logger.warning("🚫 BLACKLISTED {} after {} losses (${:.2f} total)",
                               sym, len(self._symbol_losses[sym]), total_loss)
            elif total_loss <= self._max_loss_per_symbol:
                self._blacklisted_symbols.add(sym)
                logger.warning("🚫 BLACKLISTED {} after ${:.2f} total loss",
                               sym, total_loss)

        # Recompute dynamic threshold
        self._recompute_dynamic_threshold()

        # Notify state persistence of beneficial change
        if self._on_state_change:
            self._on_state_change()

        # P8: Update adaptive weights periodically
        now = time.time()
        if now - self._last_adaptive_update > self._adaptive_update_interval:
            self._last_adaptive_update = now
            self._log_calibration_report()

    def _recompute_dynamic_threshold(self) -> None:
        """P1: Find the breakeven confidence level from history.
        Signals below this level have negative expectancy — reject them."""
        if len(self._confidence_history) < 10:
            return

        # Sort by confidence, compute cumulative PnL
        sorted_hist = sorted(self._confidence_history, key=lambda x: x["confidence"])
        best_threshold = 0.50
        best_pnl = 0.0
        cumulative_pnl = 0.0

        # Walk from lowest to highest confidence
        for entry in sorted_hist:
            cumulative_pnl += entry["pnl"]
            # If cumulative PnL is higher at this confidence level, it's a better cutoff
            if cumulative_pnl > best_pnl:
                best_pnl = cumulative_pnl
                best_threshold = entry["confidence"]

        # Phase 14: Clamp between 0.55 and 0.70
        # Rule #5: >=0.55 PF=1.09 (849 trades), >=0.70 PF=3.06 (65 trades)
        # Max 0.70 ensures signals flow — 0.85 blocked 99% of symbols (zero-flow violation)
        new_threshold = max(0.55, min(0.70, best_threshold))
        old = self._min_confidence
        self._min_confidence = new_threshold
        if abs(new_threshold - old) > 0.02:
            logger.info("📊 DYNAMIC CONFIDENCE: {} → {} (breakeven from {} trades)",
                        f"{old:.0%}", f"{new_threshold:.0%}", len(self._confidence_history))

    def _log_calibration_report(self) -> None:
        """P7: Log confidence calibration report."""
        if not self._calibration_buckets:
            return
        logger.info("📊 CALIBRATION REPORT:")
        for bucket, data in sorted(self._calibration_buckets.items()):
            if data["total"] >= 2:
                wr = data["wins"] / data["total"] * 100
                avg = data["pnl"] / data["total"]
                logger.info("  {} conf: {} trades, {:.0f}% WR, ${:.2f}/trade",
                            bucket, data["total"], wr, avg)

    def get_adaptive_boost(self, factor_name: str) -> float:
        """P8: Get adaptive weight boost/penalty for a factor.
        Positive = profitable factor (boost), negative = losing factor (penalize)."""
        perf = self._factor_performance.get(factor_name)
        if not perf or perf["wins"] + perf["losses"] < 5:
            return 0.0  # Not enough data
        total = perf["wins"] + perf["losses"]
        win_rate = perf["wins"] / total
        # Boost profitable factors, penalize losers
        return (win_rate - 0.5) * 0.3  # Range: -0.15 to +0.15

    def apply_all_filters(self, signal: Dict, market_data: Dict, orderflow: Optional[Dict],
                          oi_data: Optional[Dict], funding_data: Optional[Dict],
                          regime: Optional[Dict], vol_map: Dict[str, float]) -> Tuple[bool, str]:
        """Apply all 8 priority filters. Returns (pass, reason)."""
        sym = signal.get("symbol", "?")
        side = signal.get("side", signal.get("type", "LONG"))
        confidence = signal.get("confidence", 0)
        inst_score = signal.get("institutional_score", 0)
        trend_score = signal.get("trend_score", 0.5)

        # ── P9: Symbol Blacklist ──
        if sym in self._blacklisted_symbols:
            loss_count = len(self._symbol_losses.get(sym, []))
            return False, f"P9_BLACKLISTED: {sym} ({loss_count} losses)"

        # ── P10: LONG/SHORT Balance Check ──
        # When LONG signals outnumber SHORTs > 5:1, require higher quality for LONGs
        # This prevents the system from being overwhelmingly LONG in mixed markets
        if side == "LONG" and inst_score < 65:
            # Check if market is choppy/ranging — be more selective for LONGs
            regime_type = regime.get("regime", "range") if regime else "range"
            if regime_type in ("range", "compression"):
                return False, f"P10_LONG_BIAS_GUARD: LONG in {regime_type} regime (score={inst_score:.0f} < 65)"

        # ── P1: Dynamic Confidence Threshold ──
        effective_min = self._min_confidence
        if confidence < effective_min:
            return False, f"P1_LOW_CONFIDENCE: {confidence:.2f} < {effective_min:.2f}"

        # ── P2: OI Confirmation ──
        if oi_data and oi_data.get("current_oi", 0) > 0:
            oi_trend = oi_data.get("oi_trend", 0)  # positive = OI increasing
            price_change = signal.get("change_24h", 0)
            if side == "LONG" and oi_trend < -0.1 and price_change > 0.5:
                return False, f"P2_OI_DIVERGENCE_LONG: price↑ OI↓ (oi_trend={oi_trend:.2f})"
            if side == "SHORT" and oi_trend < -0.1 and price_change < -0.5:
                return False, f"P2_OI_DIVERGENCE_SHORT: price↓ OI↓ (oi_trend={oi_trend:.2f})"
            # Require OI expansion for conviction signals
            if abs(oi_trend) < 0.05 and inst_score < 60:
                # Weak OI + low conviction = suspect
                pass  # Don't reject, but flag
        elif inst_score < 55:
            return False, f"P2_NO_OI_DATA: score={inst_score:.0f} < 55 (need OI confirmation)"

        # ── P3: Volume Percentile Filter ──
        sym_vol = vol_map.get(sym, 0)
        if sym_vol > 0 and self._symbol_volumes:
            all_vols = sorted(self._symbol_volumes.values())
            if all_vols:
                vol_rank = np.searchsorted(all_vols, sym_vol) / len(all_vols) * 100
                if vol_rank < self._volume_percentile_threshold:
                    return False, f"P3_LOW_VOLUME: rank={vol_rank:.0f}th (need top {100 - self._volume_percentile_threshold:.0f}%)"
        elif sym_vol < 5_000_000:
            return False, f"P3_LOW_VOLUME: ${sym_vol / 1e6:.1f}M < $5M minimum"

        # ── P4: Funding Crowd Filter ──
        if funding_data:
            current_rate = funding_data.get("current_rate", 0)
            if side == "LONG" and current_rate > self._funding_crowd_long_max:
                return False, f"P4_FUNDING_CROWD_LONG: rate={current_rate:.4%} > {self._funding_crowd_long_max:.4%}"
            if side == "SHORT" and current_rate < self._funding_crowd_short_min:
                return False, f"P4_FUNDING_CROWD_SHORT: rate={current_rate:.4%} < {self._funding_crowd_short_min:.4%}"

        # ── P5: Directional Bias Check (log only, don't block) ──
        # This is handled in regime scoring — see _score_regime fix

        # ── P6: Trend Strength Filter ──
        regime_type = regime.get("regime", "range") if regime else "range"
        regime_conf = regime.get("confidence", 0.5) if regime else 0.5
        regime_conf_pct = regime.get("regime_confidence_pct", 50) if regime else 50
        # Reject signals in low-trend environments
        if regime_type in ("range",) and trend_score < self._min_trend_score:
            return False, f"P6_WEAK_TREND: regime={regime_type} trend={trend_score:.2f} < {self._min_trend_score}"
        # Extra strict for low-confidence regimes
        if regime_type == "compression" and inst_score < 70:
            return False, f"P6_COMPRESSION: regime=compression score={inst_score:.0f} < 70"
        # Block signals against strong regime direction
        if regime_type == "trending_bull" and side == "SHORT" and regime_conf_pct > 70:
            return False, f"P6_REGIME_CONFLICT: SHORT vs trending_bull (conf={regime_conf_pct:.0f}%)"
        if regime_type == "trending_bear" and side == "LONG" and regime_conf_pct > 70:
            return False, f"P6_REGIME_CONFLICT: LONG vs trending_bear (conf={regime_conf_pct:.0f}%)"

        # ── P6b: Minimum R:R Filter (Phase 8: lowered to 1.5 — aligns with production targets) ──
        rr = signal.get("risk_reward", 0)
        if 0 < rr < 1.5:
            return False, f"P6b_LOW_RR: {rr:.1f}x < 1.5x minimum (Phase 8)"

        # ── PHASE 6: Order Flow Confirmation ──
        # Signal cannot fire without order flow agreement
        if orderflow:
            delta = orderflow.get("delta", 0)
            flow_ratio = orderflow.get("flow_ratio", 0.5)
            total_trades = orderflow.get("total_trades", 0)
            # Require minimum orderflow data (lowered from 20 to allow more signals)
            if total_trades < 10:
                return False, f"P6_ORDERFLOW_INSUFFICIENT: {total_trades} trades < 10 minimum"
            # Require flow direction agreement with signal
            if side == "LONG" and flow_ratio < 0.45:
                return False, f"P6_ORDERFLOW_CONFLICT: LONG vs flow_ratio={flow_ratio:.2f} (sell dominant)"
            if side == "SHORT" and flow_ratio > 0.55:
                return False, f"P6_ORDERFLOW_CONFLICT: SHORT vs flow_ratio={flow_ratio:.2f} (buy dominant)"
        # Also check CVD confirmation
        if orderflow:
            cvd = orderflow.get("delta", 0)
            if side == "LONG" and cvd < -10000:
                return False, f"P6_CVD_CONFLICT: LONG vs negative CVD={cvd:,.0f}"
            if side == "SHORT" and cvd > 10000:
                return False, f"P6_CVD_CONFLICT: SHORT vs positive CVD={cvd:,.0f}"

        # ── PHASE 7: Smart Filters ──
        # Funding extreme check (enhanced from P4)
        if funding_data:
            current_rate = funding_data.get("current_rate", 0)
            is_extreme = funding_data.get("is_extreme", False)
            if is_extreme:
                # Extreme funding = crowd trade, reject
                if side == "LONG" and current_rate > 0.001:
                    return False, f"P7_FUNDING_EXTREME: LONG with extreme positive funding={current_rate:.4%}"
                if side == "SHORT" and current_rate < -0.001:
                    return False, f"P7_FUNDING_EXTREME: SHORT with extreme negative funding={current_rate:.4%}"

        # ── P7: Confidence Calibration (already handled by P1 dynamic threshold) ──

        # ── P8: Adaptive Boost (apply to score, don't block) ──
        # This is applied in the engine's confidence calculation

        return True, "PASSED"

    def get_dynamic_min_confidence(self) -> float:
        """P1: Return current dynamic confidence threshold."""
        return self._min_confidence

    def get_factor_performance_summary(self) -> Dict:
        """P8: Return factor performance data for dashboard."""
        summary = {}
        for name, perf in self._factor_performance.items():
            total = perf["wins"] + perf["losses"]
            if total > 0:
                summary[name] = {
                    "win_rate": perf["wins"] / total * 100,
                    "trades": total,
                    "total_pnl": round(perf["total_pnl"], 2),
                }
        return summary
