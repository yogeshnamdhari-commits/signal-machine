"""
EMA_V5 Confidence Engine — Confidence scoring from all components.

FULL AUDIT: Every evaluation produces a detailed breakdown with:
  - Per-component score (0-100)
  - Per-component weight
  - Per-component contribution (weighted points toward confidence)
  - Raw vs final confidence
  - Gap to threshold
"""
from __future__ import annotations

import time
from typing import Dict, Optional

from loguru import logger

from .config import ema_v5_config


class ConfidenceEngine:
    """Computes final confidence score from component evaluations."""

    def __init__(self) -> None:
        # ── AUDIT: Track every confidence evaluation ──
        self._eval_count: int = 0
        self._pass_count: int = 0
        self._reject_count: int = 0
        self._score_history: list = []  # last N scores for diagnostics

    def compute(
        self,
        regime_eval: Dict,
        trend_eval: Dict,
        pullback_eval: Dict,
        candle_eval: Dict,
        volume_eval: Dict,
    ) -> Dict:
        """Compute weighted confidence score.

        Returns:
            {
                "confidence": float (0-100),
                "passed": bool,
                "breakdown": dict,
                "audit": dict,   # ← NEW: detailed per-component audit
                "reason": str,
            }
        """
        cfg = ema_v5_config.confidence
        _t0 = time.monotonic()

        # Component scores (0-100)
        regime_score = 100 if regime_eval.get("regime") in ("BUY_MODE", "SELL_MODE") else 0
        trend_score = trend_eval.get("trend_score", 0)
        pullback_score = 100 if pullback_eval.get("pullback_detected") else 0
        candle_score = candle_eval.get("candle_score", 0)
        volume_score = volume_eval.get("volume_score", 0)

        # v33: Recalibrated confidence formula
        # Validated via multivariate analysis:
        # - institutional_score: ONLY positive correlation (+0.14) → primary driver
        # - trend(MSS): negative correlation (-0.04) → INVERT (low = good)
        # - candle(FVG): negative correlation (-0.02) → INVERT (low = good)
        # - volume(Vol): strong negative correlation (-0.15) → INVERT (low = good)
        # - regime: weak positive → small bonus only (binary causes inflation)
        # MONOTONICITY VERIFIED: <50=0.055 → 50-60=0.130 → 60-70=1.847
        #
        # FIX: Use trend_score as base when institutional_score is unavailable.
        # The EMA_V5 pipeline doesn't have a separate institutional scoring engine,
        # so trend_score (0-100) is the primary quality metric.
        # This replaces the hardcoded default of 50 that made the threshold unreachable.
        inst_score = trend_eval.get("institutional_score") or trend_score or 50
        regime_contrib = regime_score * cfg.regime_weight
        trend_contrib = -(trend_score * cfg.trend_weight)          # INVERTED: low MSS = reward
        pullback_contrib = pullback_score * cfg.pullback_weight    # pullback detection reward
        candle_contrib = -(candle_score * cfg.candle_weight)       # INVERTED: low FVG = reward
        volume_contrib = -(volume_score * cfg.volume_weight)       # INVERTED: low Vol = reward

        confidence = (
            inst_score * 0.50 +     # institutional_score is primary (positive corr)
            regime_contrib +
            trend_contrib +
            pullback_contrib +
            candle_contrib +
            volume_contrib
        )
        confidence = max(0, min(100, confidence))  # clamp to 0-100

        passed = confidence >= cfg.min_confidence
        gap = cfg.min_confidence - confidence  # positive = short by this amount

        # Per-component audit — detailed breakdown with weights and contributions
        audit = {
            "regime": {
                "score": round(regime_score, 1),
                "weight": cfg.regime_weight,
                "contribution": round(regime_contrib, 2),
                "pct_of_confidence": round(regime_contrib / max(confidence, 0.01) * 100, 1),
            },
            "trend": {
                "score": round(trend_score, 1),
                "weight": cfg.trend_weight,
                "contribution": round(trend_contrib, 2),
                "pct_of_confidence": round(trend_contrib / max(confidence, 0.01) * 100, 1),
            },
            "pullback": {
                "score": round(pullback_score, 1),
                "weight": cfg.pullback_weight,
                "contribution": round(pullback_contrib, 2),
                "pct_of_confidence": round(pullback_contrib / max(confidence, 0.01) * 100, 1),
            },
            "candle": {
                "score": round(candle_score, 1),
                "weight": cfg.candle_weight,
                "contribution": round(candle_contrib, 2),
                "pct_of_confidence": round(candle_contrib / max(confidence, 0.01) * 100, 1),
            },
            "volume": {
                "score": round(volume_score, 1),
                "weight": cfg.volume_weight,
                "contribution": round(volume_contrib, 2),
                "pct_of_confidence": round(volume_contrib / max(confidence, 0.01) * 100, 1),
            },
            "raw_confidence": round(confidence, 2),
            "final_confidence": round(confidence, 1),
            "threshold": cfg.min_confidence,
            "gap": round(gap, 2),
            "passed": passed,
        }

        # Update counters
        self._eval_count += 1
        if passed:
            self._pass_count += 1
        else:
            self._reject_count += 1

        # Track score history (last 500) — NOW with full component breakdown
        self._score_history.append({
            "confidence": round(confidence, 1),
            "passed": passed,
            "gap": round(gap, 2),
            "ts": time.time(),
            "inst_score": round(inst_score, 1),
            "regime": round(regime_score, 1),
            "regime_contrib": round(regime_contrib, 2),
            "trend": round(trend_score, 1),
            "trend_contrib": round(trend_contrib, 2),
            "pullback": round(pullback_score, 1),
            "pullback_contrib": round(pullback_contrib, 2),
            "candle": round(candle_score, 1),
            "candle_contrib": round(candle_contrib, 2),
            "volume": round(volume_score, 1),
            "volume_contrib": round(volume_contrib, 2),
        })
        if len(self._score_history) > 500:
            self._score_history = self._score_history[-500:]

        # ── INFO LOG: Always log when candidates reach confidence stage ──
        # This makes it easy to see component breakdown in engine logs
        logger.info(
            "🎯 CONF_SCORED inst={:.0f} regime={:.0f}(+{:.1f}) trend={:.0f}({:+.1f}) "
            "pullback={:.0f}(+{:.1f}) candle={:.0f}({:+.1f}) volume={:.0f}({:+.1f}) "
            "→ conf={:.1f}/{:.0f} gap={:+.1f} {}",
            inst_score,
            regime_score, regime_contrib,
            trend_score, trend_contrib,
            pullback_score, pullback_contrib,
            candle_score, candle_contrib,
            volume_score, volume_contrib,
            confidence, cfg.min_confidence, gap,
            "PASS" if passed else "REJECT",
        )

        breakdown = {
            "regime": round(regime_score, 1),
            "trend": round(trend_score, 1),
            "pullback": round(pullback_score, 1),
            "candle": round(candle_score, 1),
            "volume": round(volume_score, 1),
            "confidence": round(confidence, 1),
        }

        # Log detailed audit at DEBUG level for every evaluation
        logger.debug(
            "CONF_AUDIT regime={:.0f}×{:.2f}={:.1f} trend={:.0f}×{:.2f}={:.1f} "
            "pullback={:.0f}×{:.2f}={:.1f} candle={:.0f}×{:.2f}={:.1f} "
            "volume={:.0f}×{:.2f}={:.1f} → conf={:.1f}/{:.0f} gap={:+.1f} {}",
            regime_score, cfg.regime_weight, regime_contrib,
            trend_score, cfg.trend_weight, trend_contrib,
            pullback_score, cfg.pullback_weight, pullback_contrib,
            candle_score, cfg.candle_weight, candle_contrib,
            volume_score, cfg.volume_weight, volume_contrib,
            confidence, cfg.min_confidence, gap,
            "PASS" if passed else "REJECT",
        )

        return {
            "confidence": round(confidence, 1),
            "passed": passed,
            "breakdown": breakdown,
            "audit": audit,
            "reason": f"conf={confidence:.1f}_min={cfg.min_confidence}_gap={gap:+.1f}_{'PASS' if passed else 'FAIL'}",
        }

    def get_audit_stats(self) -> Dict:
        """Get cumulative audit statistics + recent score breakdowns."""
        pass_rate = self._pass_count / max(self._eval_count, 1) * 100
        avg_conf = 0
        avg_gap = 0
        if self._score_history:
            avg_conf = sum(s["confidence"] for s in self._score_history) / len(self._score_history)
            gaps = [s["gap"] for s in self._score_history if not s["passed"]]
            avg_gap = sum(gaps) / len(gaps) if gaps else 0

        # Score distribution
        bins = {"<70": 0, "70-80": 0, "80-85": 0, "85-88": 0, "88-90": 0, "90-95": 0, "95+": 0}
        for s in self._score_history:
            c = s["confidence"]
            if c >= 95: bins["95+"] += 1
            elif c >= 90: bins["90-95"] += 1
            elif c >= 88: bins["88-90"] += 1
            elif c >= 85: bins["85-88"] += 1
            elif c >= 80: bins["80-85"] += 1
            elif c >= 70: bins["70-80"] += 1
            else: bins["<70"] += 1

        # Component contribution averages (for dashboard diagnostics)
        component_avgs = {}
        if self._score_history:
            for comp in ["inst_score", "regime", "regime_contrib", "trend", "trend_contrib",
                         "pullback", "pullback_contrib", "candle", "candle_contrib",
                         "volume", "volume_contrib"]:
                vals = [s.get(comp, 0) for s in self._score_history if comp in s]
                component_avgs[comp] = round(sum(vals) / len(vals), 2) if vals else 0

        # Recent evaluations with full breakdown (last 20)
        recent_evals = []
        for s in self._score_history[-20:]:
            recent_evals.append({
                "confidence": s.get("confidence", 0),
                "passed": s.get("passed", False),
                "gap": s.get("gap", 0),
                "inst_score": s.get("inst_score", 0),
                "regime": s.get("regime", 0),
                "regime_contrib": s.get("regime_contrib", 0),
                "trend": s.get("trend", 0),
                "trend_contrib": s.get("trend_contrib", 0),
                "pullback": s.get("pullback", 0),
                "pullback_contrib": s.get("pullback_contrib", 0),
                "candle": s.get("candle", 0),
                "candle_contrib": s.get("candle_contrib", 0),
                "volume": s.get("volume", 0),
                "volume_contrib": s.get("volume_contrib", 0),
            })

        return {
            "total_evaluations": self._eval_count,
            "passed": self._pass_count,
            "rejected": self._reject_count,
            "pass_rate_pct": round(pass_rate, 1),
            "avg_confidence": round(avg_conf, 1),
            "avg_gap_when_rejected": round(avg_gap, 1),
            "distribution": bins,
            "threshold": ema_v5_config.confidence.min_confidence,
            "component_averages": component_avgs,
            "recent_evaluations": recent_evals,
        }
