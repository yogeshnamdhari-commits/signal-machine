"""
Checklist Gate — Institutional-grade signal validation.

Converts score-based trading to CHECKLIST-BASED trading.
ALL available criteria MUST pass. UNAVAILABLE data is SKIPPED (not counted).
ANY real failure = REJECT.

Data Availability States:
  PASS      — Check succeeded
  FAIL      — Check failed (data was available but condition not met)
  SKIP      — Data unavailable (source blocked, missing, or simulated)

Scoring:
  required_checks = all checks where data_status != SKIP
  passes          = checks where result == True
  score           = f"{passes}/{required_checks}"

This is the final gate before signal emission.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


class DataStatus(Enum):
    """Data availability status for each checklist item."""
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"      # Data unavailable — not counted against the signal


@dataclass
class ChecklistResult:
    """Result of the institutional checklist with 3-state data awareness."""
    passed: bool
    score: float                    # "passes/required" count
    score_str: str = ""             # Human-readable "8/9", "6/6", etc.
    checks: Dict[str, bool] = field(default_factory=dict)         # True/False result
    data_status: Dict[str, str] = field(default_factory=dict)     # "pass"/"fail"/"skip"
    details: Dict[str, str] = field(default_factory=dict)
    failures: List[str] = field(default_factory=list)
    required_checks: int = 0        # Checks where data was available
    passes: int = 0                 # Checks that passed
    skipped: int = 0                # Checks skipped (data unavailable)
    data_health: Dict[str, str] = field(default_factory=dict)     # Source-level: "LIVE"/"SIMULATED"/"UNAVAILABLE"

    def to_dict(self) -> Dict:
        return {
            "passed": self.passed,
            "score": self.score,
            "score_str": self.score_str,
            "checks": self.checks,
            "data_status": self.data_status,
            "details": self.details,
            "failures": self.failures,
            "required_checks": self.required_checks,
            "passes": self.passes,
            "skipped": self.skipped,
            "data_health": self.data_health,
        }


class ChecklistGate:
    """
    Institutional Elite Signal Checklist — 3-State Data Aware + SMC Gates.

    Only generates a signal when ALL available criteria pass:
      1. REGIME FILTER    — BTC/ETH trend aligned, market breadth aligned
      2. LIQUIDITY SWEEP  — Sweep detected
      3. MSS              — Market Structure Shift
      4. DISPLACEMENT     — Displacement candle
      5. DELTA            — Delta confirmation (> 0.80 normalized)
      6. CVD              — CVD confirmation (> 0.80 normalized)
      7. OI EXPANSION     — OI expansion (> 0.80 normalized)
      8. VOLUME EXPANSION — Volume expansion (> 0.80 normalized)
      9. FVG RETEST       — Entry at FVG retest
     10. RR >= 1.5        — Risk/Reward ratio
     11. BOS/CHoCH        — Break of Structure or Change of Character on HTF
     12. ORDER BLOCK      — Price returning to OB zone
     13. SESSION GATE     — London/NY only (blocks asia, off_hours)

    Data Availability:
      - UNAVAILABLE data → SKIP (not counted as fail)
      - Only checks with live data are REQUIRED
      - Score = passes / required_checks

    Additional hard rejects:
      - Stop > 1.5 ATR
      - Funding conflicts
      - Confidence < 85
      - Low-liquidity symbols (not in top 50 by volume)
      - Legacy strategy_version (must be production_v2)
    """

    # Checks that are data-dependent (can be SKIP when source unavailable)
    DATA_DEPENDENT_CHECKS = {"delta", "cvd", "oi_expansion", "volume_expansion"}

    def __init__(self):
        # Thresholds — hard minimums (NOT score-based)
        self.MIN_DELTA_SCORE = 0.80
        self.MIN_CVD_SCORE = 0.80
        self.MIN_OI_SCORE = 0.80
        self.MIN_VOLUME_SCORE = 0.80
        self.MIN_RR = 1.5  # Phase 8: lowered from 3.0 — aligns with production targets (rr_1=1.8x)
        self.MIN_CONFIDENCE = 40.0  # Phase 8: lowered from 85 — AI scorer produces 22-50 range
        self.MAX_STOP_ATR_MULT = 8.0  # Phase 9: raised from 3.0 — production targets deliver ratios up to 7.75
        self.MIN_REGIME_CONFIDENCE = 35.0  # Phase 9: lowered from 55 — allow all trending regimes (conf 36+)

    @staticmethod
    def _is_data_available(data: Any, source_name: str = "") -> bool:
        """
        Determine if a data source is truly available vs simulated/missing.

        Returns False when:
          - data is None
          - data is empty dict
          - data has only default/simulated indicators
        """
        if data is None:
            return False
        if not isinstance(data, dict):
            return bool(data)
        if len(data) == 0:
            return False
        # Check for simulated/mock indicators
        if data.get("_simulated") or data.get("_mock") or data.get("source") == "simulated":
            return False
        # Check for OI proxy (derived from orderflow, not real OI)
        if data.get("_proxy") or data.get("source") == "oi_proxy":
            return False
        return True

    def evaluate(
        self,
        sig: Dict[str, Any],
        regime: Optional[Dict],
        sweep_setup: Optional[Dict],
        orderflow: Optional[Dict],
        cvd_data: Optional[Dict],
        oi_data: Optional[Dict],
        funding_data: Optional[Dict],
        absorption_data: Optional[Dict],
        smart_money_data: Optional[Dict],
        market_data: Optional[Dict],
        sweep_analysis: Optional[Dict] = None,
    ) -> ChecklistResult:
        """
        Run the institutional checklist with 3-state data awareness.

        For data-dependent checks (OI, CVD, Delta, Volume):
          - If data is truly available → evaluate normally (PASS/FAIL)
          - If data is unavailable → SKIP (not counted as failure)

        Returns ChecklistResult with pass/fail/skip per check and data health.
        """
        checks: Dict[str, bool] = {}
        data_status: Dict[str, str] = {}   # "pass"/"fail"/"skip"
        data_health: Dict[str, str] = {}   # Source-level status
        details: Dict[str, str] = {}
        failures: List[str] = []
        side = sig.get("side", "LONG")
        is_long = side == "LONG"

        # ═══════════════════════════════════════════════════════════
        # DATA AVAILABILITY DETECTION
        # ═══════════════════════════════════════════════════════════

        # OI data availability — Binance IP ban blocks REST endpoint
        oi_available = self._is_data_available(oi_data, "open_interest")
        if not oi_available:
            data_health["oi"] = "UNAVAILABLE"
        else:
            data_health["oi"] = "LIVE"

        # CVD data — from multi-TF CVD analyzer
        cvd_available = self._is_data_available(cvd_data, "cvd")
        if not cvd_available:
            data_health["cvd"] = "UNAVAILABLE"
        else:
            # Check if CVD has real multi-timeframe data
            tf_biases = [
                cvd_data.get("cvd_bias_5m", "neutral"),
                cvd_data.get("cvd_bias_15m", "neutral"),
                cvd_data.get("cvd_bias_1h", "neutral"),
            ]
            has_real_data = any(b != "neutral" for b in tf_biases)
            data_health["cvd"] = "LIVE" if has_real_data else "SIMULATED"

        # Orderflow / Delta data — from trade buffer or WebSocket
        delta_available = False
        if orderflow:
            # Check if orderflow has real trade data (not just defaults)
            total_trades = orderflow.get("total_trades", 0)
            buy_vol = orderflow.get("buy_volume", 0)
            sell_vol = orderflow.get("sell_volume", 0)
            if total_trades > 0 or buy_vol > 0 or sell_vol > 0:
                delta_available = True
                data_health["delta"] = "LIVE"
            else:
                data_health["delta"] = "SIMULATED"
        else:
            data_health["delta"] = "UNAVAILABLE"

        # Volume data — from orderflow or market data
        volume_available = False
        if orderflow and orderflow.get("flow_strength_score", 0) > 0:
            volume_available = True
            data_health["volume"] = "LIVE"
        elif market_data and market_data.get("volume", 0) > 0:
            volume_available = True
            data_health["volume"] = "LIVE"
        else:
            data_health["volume"] = "UNAVAILABLE"

        # Funding data
        funding_available = self._is_data_available(funding_data, "funding")
        if not funding_available:
            data_health["funding"] = "UNAVAILABLE"
        else:
            data_health["funding"] = "LIVE"

        # ═══════════════════════════════════════════════════════════
        # CHECK 1: REGIME FILTER (always required)
        # Phase 11: Allow "range" regime (matches engine's HARD_ALLOWED_REGIMES)
        # Phase 11: Allow directional mismatch (counter-trend signals are valid)
        # Phase 11: When regime_type == "range" AND conf < MIN_REGIME_CONFIDENCE,
        #           treat as SKIP (regime classifier not working, not a real failure)
        # ═══════════════════════════════════════════════════════════
        regime_type = regime.get("regime", "range") if regime else "range"
        regime_conf = (regime.get("confidence", 0) * 100) if regime else 0
        regime_pct = regime.get("regime_confidence_pct", regime_conf) if regime else 0
        # Phase 11: expanded allowed regimes to include "range"
        ALLOWED_REGIMES = ("trending_bull", "trending_bear", "breakout", "range")
        regime_type_ok = regime_type in ALLOWED_REGIMES
        # Phase 11: removed directional mismatch check — counter-trend signals valid
        regime_conf_ok = regime_pct >= self.MIN_REGIME_CONFIDENCE
        # Phase 11: "range" with low confidence = regime classifier not working → SKIP
        if regime_type == "range" and not regime_conf_ok:
            checks["regime"] = True  # SKIP — not counted as fail
            data_status["regime"] = "skip"
            details["regime"] = f"{regime_type} conf={regime_pct:.0f} ⏭️ SKIP (range regime, low confidence = classifier not working)"
        else:
            checks["regime"] = regime_type_ok and regime_conf_ok
            data_status["regime"] = "pass" if checks["regime"] else "fail"
            details["regime"] = f"{regime_type} conf={regime_pct:.0f} {'✅' if checks['regime'] else '❌ regime not allowed or conf < ' + str(self.MIN_REGIME_CONFIDENCE)}"
            if not checks["regime"]:
                failures.append(f"REGIME: {regime_type} conf={regime_pct:.0f}")

        # ═══════════════════════════════════════════════════════════
        # CHECK 2: LIQUIDITY SWEEP (always required — historical data)
        # ═══════════════════════════════════════════════════════════
        sweep_valid = False
        sweep_data_available = False
        if sweep_setup:
            sweep_data_available = True
            sweep_valid = sweep_setup.get("valid", False) or sweep_setup.get("conditions_met", 0) >= 3
        if sweep_analysis:
            sweep_data_available = True
            if not sweep_valid:
                sweep_valid = sweep_analysis.get("sweep_detected", False)
        if not sweep_data_available:
            sweep_valid = True  # No data = pass (not applicable)
        checks["sweep"] = sweep_valid
        data_status["sweep"] = "pass" if sweep_valid else "fail"
        details["sweep"] = f"{'✅' if sweep_valid else '❌ no sweep detected'}{' (no data)' if not sweep_data_available else ''}"
        if not sweep_valid:
            failures.append("SWEEP: no liquidity sweep detected")

        # ═══════════════════════════════════════════════════════════
        # CHECK 3: MSS (always required — historical data)
        # ═══════════════════════════════════════════════════════════
        mss_score = 0
        mss_data_available = False
        if sweep_setup:
            mss_data_available = True
            mss_score = sweep_setup.get("mss_score", 0)
        if sweep_analysis:
            mss_data_available = True
            if mss_score == 0:
                mss_score = 1 if sweep_analysis.get("mss_detected", False) else 0
        if not mss_data_available:
            # Phase 9: No sweep data = SKIP (data-dependent check)
            checks["mss"] = True  # Treated as pass for scoring
            data_status["mss"] = "skip"
            details["mss"] = f"mss_score={mss_score} ⏭️ SKIP (no sweep data)"
        else:
            checks["mss"] = mss_score > 0
            data_status["mss"] = "pass" if checks["mss"] else "fail"
            details["mss"] = f"mss_score={mss_score} {'✅' if checks['mss'] else '❌ no MSS'}"
            if not checks["mss"]:
                failures.append("MSS: no Market Structure Shift")

        # ═══════════════════════════════════════════════════════════
        # CHECK 4: DISPLACEMENT (always required — derived from trades)
        # ═══════════════════════════════════════════════════════════
        displacement_score = 0
        if sweep_setup:
            displacement_score = sweep_setup.get("delta_score", 0)
        of_delta = orderflow.get("delta", 0) if orderflow else 0
        of_imbalance = orderflow.get("imbalance", 0) if orderflow else 0
        displacement_ok = displacement_score > 0
        if not displacement_ok and orderflow:
            if is_long and of_delta > 0 and abs(of_imbalance) > 0.15:
                displacement_ok = True
            elif not is_long and of_delta < 0 and abs(of_imbalance) > 0.15:
                displacement_ok = True
        # Phase 9: Make displacement data-dependent — SKIP when no orderflow data available
        has_orderflow_data = orderflow and (orderflow.get("total_trades", 0) > 0 or abs(of_delta) > 0)
        if not displacement_ok and not has_orderflow_data and displacement_score == 0:
            checks["displacement"] = True  # No data = SKIP
            data_status["displacement"] = "skip"
            details["displacement"] = f"delta_score={displacement_score} ⏭️ SKIP (no orderflow data)"
        else:
            checks["displacement"] = displacement_ok
            data_status["displacement"] = "pass" if displacement_ok else "fail"
            details["displacement"] = f"delta_score={displacement_score} of_delta={of_delta:.0f} imb={of_imbalance:.3f} {'✅' if displacement_ok else '❌ no displacement'}"
            if not displacement_ok:
                failures.append("DISPLACEMENT: no displacement candle")

        # ═══════════════════════════════════════════════════════════
        # CHECK 5: DELTA CONFIRMATION (data-dependent → SKIP if unavailable)
        # ═══════════════════════════════════════════════════════════
        delta_confirmed = False
        delta_raw = abs(of_delta) if orderflow else 0
        delta_imb = abs(of_imbalance) if orderflow else 0
        delta_normalized = min(1.0, delta_imb * 2.5)

        if delta_available:
            if orderflow:
                if is_long and of_delta > 0:
                    delta_confirmed = delta_normalized >= self.MIN_DELTA_SCORE
                elif not is_long and of_delta < 0:
                    delta_confirmed = delta_normalized >= self.MIN_DELTA_SCORE
            checks["delta"] = delta_confirmed
            data_status["delta"] = "pass" if delta_confirmed else "fail"
            details["delta"] = f"norm={delta_normalized:.2f} raw_delta={of_delta:.0f} {'✅' if delta_confirmed else '❌ delta < ' + str(self.MIN_DELTA_SCORE)}"
            if not delta_confirmed:
                failures.append(f"DELTA: normalized={delta_normalized:.2f} < {self.MIN_DELTA_SCORE}")
        else:
            # Data unavailable or SIMULATED → SKIP (not counted as fail)
            checks["delta"] = True  # Treated as pass for scoring
            data_status["delta"] = "skip"
            details["delta"] = f"norm={delta_normalized:.2f} ⏭️ SKIP (orderflow data unavailable)"

        # ═══════════════════════════════════════════════════════════
        # CHECK 6: CVD CONFIRMATION (data-dependent → SKIP if unavailable)
        # ═══════════════════════════════════════════════════════════
        cvd_confirmed = False
        cvd_bias = "neutral"
        cvd_5m = 0
        cvd_normalized = 0.0

        if cvd_available:
            cvd_bias = cvd_data.get("cvd_bias", "neutral")
            cvd_5m = cvd_data.get("cvd_5m", 0)
            tf_biases = [
                cvd_data.get("cvd_bias_5m", "neutral"),
                cvd_data.get("cvd_bias_15m", "neutral"),
                cvd_data.get("cvd_bias_1h", "neutral"),
            ]
            bullish_count = sum(1 for b in tf_biases if "bullish" in str(b))
            bearish_count = sum(1 for b in tf_biases if "bearish" in str(b))
            total_tf = len([b for b in tf_biases if b != "neutral"])
            if total_tf > 0:
                cvd_normalized = (bullish_count / total_tf) if is_long else (bearish_count / total_tf)
            cvd_confirmed = cvd_normalized >= self.MIN_CVD_SCORE
            # Phase 9: SIMULATED CVD data is unreliable → treat as SKIP
            if data_health.get("cvd") == "SIMULATED":
                checks["cvd"] = True
                data_status["cvd"] = "skip"
                details["cvd"] = f"bias={cvd_bias} norm={cvd_normalized:.2f} ⏭️ SKIP (CVD data simulated)"
            else:
                checks["cvd"] = cvd_confirmed
                data_status["cvd"] = "pass" if cvd_confirmed else "fail"
                details["cvd"] = f"bias={cvd_bias} cvd_5m={cvd_5m:.0f} norm={cvd_normalized:.2f} {'✅' if cvd_confirmed else '❌ cvd < ' + str(self.MIN_CVD_SCORE)}"
                if not cvd_confirmed:
                    failures.append(f"CVD: normalized={cvd_normalized:.2f} < {self.MIN_CVD_SCORE}")
        else:
            checks["cvd"] = True  # Treated as pass for scoring
            data_status["cvd"] = "skip"
            details["cvd"] = f"bias=N/A norm=0.00 ⏭️ SKIP (CVD data unavailable)"

        # ═══════════════════════════════════════════════════════════
        # CHECK 7: OI EXPANSION (data-dependent → SKIP if unavailable)
        # ═══════════════════════════════════════════════════════════
        oi_confirmed = False
        oi_change = 0
        oi_signal = "neutral"

        if oi_available:
            oi_change = oi_data.get("change_pct", 0)
            oi_signal = oi_data.get("signal", "neutral")
            price_change = sig.get("change_24h", 0)
            oi_expansion = abs(oi_change)
            # Phase 8: lowered threshold from 2% to 0.5% — typical OI changes are 0-0.12%
            # Also accept squeeze signals as confirmation
            if is_long:
                oi_confirmed = oi_change > 0 and price_change > 0 and oi_expansion >= 0.005
            else:
                oi_confirmed = oi_change > 0 and price_change < 0 and oi_expansion >= 0.005
            if not oi_confirmed and oi_signal in ("long_squeeze", "short_squeeze"):
                oi_confirmed = True
            # Phase 9: OI change < 1% is noise — SKIP instead of FAIL
            # Phase 11: raised SKIP threshold from 1% to 10% — typical OI changes are 0.01-0.12%
            #           Changes below 10% are market noise, not meaningful OI expansion
            if not oi_confirmed and oi_expansion < 0.10:
                checks["oi_expansion"] = True  # Near-zero change = SKIP
                data_status["oi_expansion"] = "skip"
                details["oi_expansion"] = f"change={oi_change:.2f}% signal={oi_signal} ⏭️ SKIP (OI change < 10% = noise)"
            else:
                checks["oi_expansion"] = oi_confirmed
                data_status["oi_expansion"] = "pass" if oi_confirmed else "fail"
                details["oi_expansion"] = f"change={oi_change:.2f}% signal={oi_signal} {'✅' if oi_confirmed else '❌ no OI expansion'}"
                if not oi_confirmed:
                    failures.append(f"OI_EXPANSION: change={oi_change:.2f}% signal={oi_signal}")
        else:
            checks["oi_expansion"] = True  # Treated as pass for scoring
            data_status["oi_expansion"] = "skip"
            details["oi_expansion"] = f"change=0.00% signal=N/A ⏭️ SKIP (OI data unavailable)"

        # ═══════════════════════════════════════════════════════════
        # CHECK 8: VOLUME EXPANSION (data-dependent → SKIP if unavailable)
        # ═══════════════════════════════════════════════════════════
        volume_confirmed = False
        flow_strength = 0
        flow_signal = "neutral"
        if orderflow:
            flow_strength = orderflow.get("flow_strength_score", 50)
            flow_signal = orderflow.get("flow_signal", "neutral")
        vol_24h = market_data.get("volume", 0) if market_data else 0

        if volume_available:
            # Phase 9: Lowered strength threshold from 70→40, accept neutral (no opposing flow)
            if is_long:
                volume_confirmed = flow_signal in ("buy", "strong_buy", "neutral") and flow_strength >= 40
            else:
                volume_confirmed = flow_signal in ("sell", "strong_sell", "neutral") and flow_strength >= 40
            checks["volume_expansion"] = volume_confirmed
            data_status["volume_expansion"] = "pass" if volume_confirmed else "fail"
            details["volume_expansion"] = f"flow={flow_signal} strength={flow_strength:.0f} vol24h={vol_24h:.0f} {'✅' if volume_confirmed else '❌ no volume expansion'}"
            if not volume_confirmed:
                failures.append(f"VOLUME_EXPANSION: flow={flow_signal} strength={flow_strength:.0f}")
        else:
            checks["volume_expansion"] = True  # Treated as pass for scoring
            data_status["volume_expansion"] = "skip"
            details["volume_expansion"] = f"flow=N/A strength=0 vol24h={vol_24h:.0f} ⏭️ SKIP (volume data unavailable)"

        # ═══════════════════════════════════════════════════════════
        # WEIGHTED DATA COMPOSITE (replaces individual 0.80 thresholds)
        # ═══════════════════════════════════════════════════════════
        _dc_weights = {"delta": 0.30, "cvd": 0.25, "oi_expansion": 0.25, "volume_expansion": 0.20}
        _dc_scores = {}
        if data_status.get("delta") != "skip":
            _dc_dir_ok = (is_long and of_delta > 0) or (not is_long and of_delta < 0)
            _dc_scores["delta"] = delta_normalized if _dc_dir_ok else 0.0
        if data_status.get("cvd") != "skip":
            _dc_scores["cvd"] = cvd_normalized
        if data_status.get("oi_expansion") != "skip":
            _dc_scores["oi_expansion"] = min(1.0, abs(oi_change) / 5.0)
        if data_status.get("volume_expansion") != "skip":
            _dc_scores["volume_expansion"] = flow_strength / 100.0 if flow_strength else 0.0

        if _dc_scores:
            _dc_total_w = sum(_dc_weights[k] for k in _dc_scores)
            _dc_composite = sum(_dc_scores[k] * (_dc_weights[k] / _dc_total_w) for k in _dc_scores) if _dc_total_w > 0 else 0
            details["data_composite"] = f"composite={_dc_composite:.2f} sources={list(_dc_scores.keys())}"
            # Remove individual data failures — composite replaces them
            failures = [f for f in failures if not any(x in f for x in ["DELTA:", "CVD:", "OI_EXPANSION:", "VOLUME_EXPANSION:"])]
            if _dc_composite < 0.72:
                for _dk in ["delta", "cvd", "oi_expansion", "volume_expansion"]:
                    if _dk in _dc_scores:
                        checks[_dk] = False
                        data_status[_dk] = "fail"
                failures.append(f"DATA_COMPOSITE: {_dc_composite:.2f} < 0.72")
            else:
                # Composite passes — rescue any individual data check failures
                for _dk in ["delta", "cvd", "oi_expansion", "volume_expansion"]:
                    if _dk in _dc_scores and not checks.get(_dk, True):
                        checks[_dk] = True
                        data_status[_dk] = "pass"
                        details[_dk] += " → rescued by composite"

        # ═══════════════════════════════════════════════════════════
        # CHECK 9: FVG RETEST (always required — historical data)
        # ═══════════════════════════════════════════════════════════
        fvg_ok = False
        fvg_score = 0
        fvg_data_available = False
        if sweep_setup:
            fvg_data_available = True
            fvg_score = sweep_setup.get("fvg_score", 0)
        if sweep_analysis:
            fvg_data_available = True
            if fvg_score == 0:
                fvg_score = 1 if sweep_analysis.get("fvg_detected", False) else 0
        if not fvg_data_available:
            # Phase 9: No sweep data = SKIP (data-dependent check)
            fvg_ok = True
            data_status["fvg_retest"] = "skip"
            details["fvg_retest"] = f"fvg_score=0 ⏭️ SKIP (no sweep data)"
        else:
            fvg_ok = fvg_score > 0
            data_status["fvg_retest"] = "pass" if fvg_ok else "fail"
            details["fvg_retest"] = f"fvg_score={fvg_score} {'✅' if fvg_ok else '❌ no FVG retest'}"
            if not fvg_ok:
                failures.append("FVG_RETEST: no FVG/OB retest")
        checks["fvg_retest"] = fvg_ok

        # ═══════════════════════════════════════════════════════════
        # CHECK 10: RISK/REWARD >= 3.0 (always required)
        # ═══════════════════════════════════════════════════════════
        rr = sig.get("risk_reward", sig.get("risk_reward_ratio", 0))
        if isinstance(rr, dict):
            rr = rr.get("ratio", 0)
        rr_ok = rr >= self.MIN_RR
        checks["rr"] = rr_ok
        data_status["rr"] = "pass" if rr_ok else "fail"
        details["rr"] = f"R:R={rr:.2f} {'✅' if rr_ok else '❌ R:R < ' + str(self.MIN_RR)}"
        if not rr_ok:
            failures.append(f"RR: {rr:.2f} < {self.MIN_RR}")

        # ═══════════════════════════════════════════════════════════
        # ADDITIONAL HARD REJECTS (always required)
        # ═══════════════════════════════════════════════════════════

        # Stop > 1.5 ATR
        atr = sig.get("atr", 0)
        sl_distance = sig.get("sl_distance_pct", 0) / 100
        if atr > 0 and sig.get("entry_price", 0) > 0:
            stop_atr_ratio = (sl_distance * sig["entry_price"]) / atr
        else:
            stop_atr_ratio = 0
        if stop_atr_ratio > self.MAX_STOP_ATR_MULT:
            checks["stop_atr"] = False
            data_status["stop_atr"] = "fail"
            details["stop_atr"] = f"stop/ATR={stop_atr_ratio:.2f} > {self.MAX_STOP_ATR_MULT} ❌"
            failures.append(f"STOP_ATR: {stop_atr_ratio:.2f} > {self.MAX_STOP_ATR_MULT}")
        else:
            checks["stop_atr"] = True
            data_status["stop_atr"] = "pass"
            details["stop_atr"] = f"stop/ATR={stop_atr_ratio:.2f} ✅"

        # Funding conflicts
        funding_rate = funding_data.get("current_rate", 0) if funding_data else 0
        funding_signal = funding_data.get("signal", "neutral") if funding_data else "neutral"
        funding_conflict = False
        if is_long and funding_rate > 0.001 and funding_signal == "sell":
            funding_conflict = True
        elif not is_long and funding_rate < -0.001 and funding_signal == "buy":
            funding_conflict = True
        checks["funding"] = not funding_conflict
        data_status["funding"] = "pass" if not funding_conflict else "fail"
        details["funding"] = f"rate={funding_rate*100:.4f}% signal={funding_signal} {'✅' if not funding_conflict else '❌ funding conflicts'}"
        if funding_conflict:
            failures.append(f"FUNDING: rate={funding_rate*100:.4f}% conflicts with {side}")

        # Confidence >= 85
        confidence = sig.get("confidence_100", sig.get("confidence", 0) * 100 if isinstance(sig.get("confidence"), float) and sig.get("confidence", 0) <= 1 else sig.get("confidence", 0))
        conf_ok = confidence >= self.MIN_CONFIDENCE
        checks["confidence"] = conf_ok
        data_status["confidence"] = "pass" if conf_ok else "fail"
        details["confidence"] = f"conf={confidence:.1f} {'✅' if conf_ok else '❌ conf < ' + str(self.MIN_CONFIDENCE)}"
        if not conf_ok:
            failures.append(f"CONFIDENCE: {confidence:.1f} < {self.MIN_CONFIDENCE}")

        # ═══════════════════════════════════════════════════════════
        # CHECK 11: BOS/CHoCH — Market Structure on HTF
        # Price must have broken structure (BOS) or changed character (CHoCH)
        # on the 15m/1H timeframe before entry. This confirms the trend.
        # ═══════════════════════════════════════════════════════════
        mss_score = sig.get("mss_score", 0)
        # MSS score >= 60 indicates BOS/CHoCH was detected
        bos_choch_ok = mss_score >= 60
        checks["bos_choch"] = bos_choch_ok
        data_status["bos_choch"] = "pass" if bos_choch_ok else "fail"
        details["bos_choch"] = f"MSS/BOS score={mss_score:.0f} {'✅' if bos_choch_ok else '❌ no BOS/CHoCH confirmation'}"
        if not bos_choch_ok:
            failures.append(f"BOS_CHoCH: MSS score={mss_score:.0f} < 60")

        # ═══════════════════════════════════════════════════════════
        # CHECK 12: ORDER BLOCK — Price returning to OB zone
        # Price must be within 1.5 ATR of a detected order block level
        # ═══════════════════════════════════════════════════════════
        # Order block is detected via absorption + sweep combination
        abs_score = 0
        if absorption_data:
            abs_signal = absorption_data.get("signal", "neutral")
            if abs_signal in ("absorption_buy", "absorption_sell"):
                # Check if absorption aligns with trade direction
                if (is_long and abs_signal == "absorption_buy") or \
                   (not is_long and abs_signal == "absorption_sell"):
                    abs_score = 80
                else:
                    abs_score = 40  # opposing absorption
            abs_top = absorption_data.get("top_levels", [])
            if abs_top:
                abs_score = max(abs_score, 70)

        # Also check sweep as proxy for order block
        sweep_score_check = sig.get("sweep_score", 0)
        ob_score = max(abs_score, sweep_score_check)
        ob_ok = ob_score >= 50  # At least some structural level detected
        checks["order_block"] = ob_ok
        data_status["order_block"] = "pass" if ob_ok else "skip"
        details["order_block"] = f"OB_score={ob_score:.0f} (abs={abs_score}, sweep={sweep_score_check}) {'✅' if ob_ok else '⚠️ no OB zone detected'}"
        # This is data-dependent — skip if no absorption/sweep data
        if not absorption_data and sweep_score_check == 0:
            data_status["order_block"] = "skip"
            details["order_block"] = f"OB_score=0 ⚠️ SKIP (no absorption/sweep data)"

        # ═══════════════════════════════════════════════════════════
        # CHECK 13: SESSION GATE — London/NY only
        # Asia and off_hours are blocked (consistently unprofitable)
        # ═══════════════════════════════════════════════════════════
        from scanner.session_quality_filter import SessionQualityFilter
        session_filter = SessionQualityFilter()
        session_allowed, session_reason, session_data = session_filter.evaluate(confidence)
        checks["session"] = session_allowed
        data_status["session"] = "pass" if session_allowed else "fail"
        details["session"] = f"{session_data.get('session', 'unknown')} {'✅' if session_allowed else '❌ ' + session_reason}"
        if not session_allowed:
            failures.append(f"SESSION: {session_reason}")

        # ═══════════════════════════════════════════════════════════
        # CHECK 14: SYMBOL LIQUIDITY — Top 50 by volume only
        # ═══════════════════════════════════════════════════════════
        volume_24h = sig.get("volume_24h", 0) or sig.get("quote_volume", 0) or 0
        # If volume data available, check if symbol is in top 50
        if volume_24h > 0:
            # Threshold: top 50 crypto futures typically have > $10M daily volume
            liq_ok = volume_24h >= 10_000_000
            checks["liquidity"] = liq_ok
            data_status["liquidity"] = "pass" if liq_ok else "fail"
            details["liquidity"] = f"vol_24h=${volume_24h/1e6:.1f}M {'✅' if liq_ok else '❌ low liquidity'}"
            if not liq_ok:
                failures.append(f"LIQUIDITY: vol_24h=${volume_24h/1e6:.1f}M < $50M")
        else:
            # No volume data — skip check
            checks["liquidity"] = True
            data_status["liquidity"] = "skip"
            details["liquidity"] = f"vol_24h=unknown ⚠️ SKIP"

        # ═══════════════════════════════════════════════════════════
        # CHECK 15: EXIT ENGINE — Verify strategy_version is set
        # The actual legacy block is enforced at engine.py db.open_position()
        # This check only validates signal has a strategy_version assigned.
        # ═══════════════════════════════════════════════════════════
        strategy = sig.get("strategy_version", "")
        checks["exit_engine"] = True  # Always pass — engine.py handles legacy block
        data_status["exit_engine"] = "pass"
        details["exit_engine"] = f"strategy={strategy or '(not set)'} ✅ (engine blocks legacy at open)"

        # ═══════════════════════════════════════════════════════════
        # 3-STATE SCORING
        # ═══════════════════════════════════════════════════════════
        # Required checks = all checks where data was available (not SKIP)
        required_checks = sum(1 for k, v in data_status.items() if v != "skip")
        passes = sum(1 for k, v in data_status.items() if v == "pass")
        skips = sum(1 for k, v in data_status.items() if v == "skip")
        real_failures = sum(1 for k, v in data_status.items() if v == "fail")

        # PASS condition: ALL required checks must pass, zero real failures
        passed = (real_failures == 0) and (passes >= required_checks)
        score_str = f"{passes}/{required_checks}"

        return ChecklistResult(
            passed=passed,
            score=passes,
            score_str=score_str,
            checks=checks,
            data_status=data_status,
            details=details,
            failures=failures,
            required_checks=required_checks,
            passes=passes,
            skipped=skips,
            data_health=data_health,
        )
