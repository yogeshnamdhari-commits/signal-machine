"""
Edge Confidence Metric — Combine all health metrics into single confidence score.

Per Executive Assessment v16:
    "Instead of monitoring Calibration, Drift, Prediction Error, Stability,
     Rolling PF independently, combine them into Edge Confidence 0–100.
     Then drive position size, number of simultaneous trades, allowable
     portfolio heat from that single value."

Key Innovation:
    v21 reported: Multiple independent health metrics
    v22 combines: Single Edge Confidence score driving all risk decisions

    This allows:
        - Simplified operational decisions
        - Automatic risk adjustment
        - Clear production readiness
        - Single metric for all risk controls

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


# ═══════════════════════════════════════════════════════════════
# EDGE CONFIDENCE CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Risk tiers based on edge confidence
EDGE_TIERS = [
    {"min_score": 90, "risk_pct": 100, "max_positions": 5, "label": "HIGH CONFIDENCE", "action": "Full allocation"},
    {"min_score": 75, "risk_pct": 80, "max_positions": 4, "label": "CONFIDENT", "action": "Standard allocation"},
    {"min_score": 60, "risk_pct": 50, "max_positions": 3, "label": "NEUTRAL", "action": "Reduced allocation"},
    {"min_score": 40, "risk_pct": 25, "max_positions": 2, "label": "LOW CONFIDENCE", "action": "Minimal allocation"},
    {"min_score": 0, "risk_pct": 0, "max_positions": 0, "label": "NO EDGE", "action": "Stop trading"},
]

# Component weights (must sum to 1.0)
COMPONENT_WEIGHTS = {
    "rolling_pf": 0.25,           # Recent profit factor
    "expectancy": 0.20,           # Expected value
    "calibration": 0.15,          # Probability calibration
    "profit_capture": 0.15,       # How much profit is captured
    "feature_stability": 0.10,    # Are feature relationships stable
    "decision_stability": 0.10,   # Are decisions consistent
    "regime_stability": 0.05,     # Is regime detection stable
}


@dataclass
class EdgeComponent:
    """A single component of the edge confidence score."""
    name: str = ""
    raw_value: float = 0.0
    normalized_score: float = 0.0  # 0-100
    weight: float = 0.0
    weighted_score: float = 0.0
    detail: str = ""

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "raw": round(self.raw_value, 4),
            "score": round(self.normalized_score, 1),
            "weight": round(self.weight, 3),
            "weighted": round(self.weighted_score, 2),
            "detail": self.detail,
        }


@dataclass
class EdgeConfidenceScore:
    """Complete edge confidence assessment."""
    timestamp: float = 0.0

    # Components
    components: List[EdgeComponent] = field(default_factory=list)

    # Overall score
    edge_confidence: float = 0.0   # 0-100
    edge_tier: str = ""            # HIGH CONFIDENCE / CONFIDENT / NEUTRAL / LOW / NO EDGE
    risk_pct: float = 100.0       # Risk allocation %
    max_positions: int = 5        # Maximum simultaneous positions
    recommended_action: str = ""

    # Derived metrics
    position_size_factor: float = 1.0  # Multiplier for position sizing
    portfolio_heat_limit: float = 5.0  # Maximum portfolio heat %

    # Trend
    trend: str = ""                # IMPROVING / STABLE / DECLINING
    previous_score: Optional[float] = None

    # Forward estimate
    forward_confidence: float = 0.0
    confidence_in_estimate: float = 0.0

    # Alerts
    alerts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "components": [c.to_dict() for c in self.components],
            "edge": {
                "confidence": round(self.edge_confidence, 1),
                "tier": self.edge_tier,
                "risk_pct": round(self.risk_pct, 1),
                "max_positions": self.max_positions,
                "action": self.recommended_action,
            },
            "derived": {
                "position_size_factor": round(self.position_size_factor, 3),
                "portfolio_heat_limit": round(self.portfolio_heat_limit, 1),
            },
            "trend": self.trend,
            "forward": {
                "estimated_confidence": round(self.forward_confidence, 1),
                "confidence": round(self.confidence_in_estimate, 1),
            },
            "alerts": self.alerts,
        }


class EdgeConfidenceScorer:
    """
    Combines all health metrics into single edge confidence score.

    Per Executive Assessment v16:
        "Combine them into Edge Confidence 0–100. Then drive position size,
         number of simultaneous trades, allowable portfolio heat
         from that single value."

    This engine:
        1. Aggregates PF, expectancy, calibration, capture, stability
        2. Calculates unified edge confidence (0-100)
        3. Drives all risk decisions from single score
        4. Provides clear action recommendations

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0
        self._previous_score: Optional[float] = None

    def _ensure_loaded(self) -> None:
        """Load trades from DB if stale."""
        if time.time() - self._last_load < 300:
            return
        self._load_trades()

    def _load_trades(self) -> None:
        """Load all closed trades."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol, side, realized_r, regime, session,
                       confidence, institutional_score, highest_pnl, mfe_pct
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load edge confidence scorer: {}", e)

    def evaluate(self) -> EdgeConfidenceScore:
        """
        Evaluate edge confidence and generate score.

        Returns:
            EdgeConfidenceScore with complete assessment
        """
        self._ensure_loaded()

        score = EdgeConfidenceScore(timestamp=time.time())

        if not self._trades or len(self._trades) < 50:
            score.edge_confidence = 0
            score.edge_tier = "NO EDGE"
            score.risk_pct = 0
            score.max_positions = 0
            score.recommended_action = "Insufficient data"
            return score

        # ── Component 1: Rolling PF (25%) ──
        rolling_pf = self._calc_pf(self._trades[:50])
        pf_score = self._normalize_pf(rolling_pf)
        score.components.append(EdgeComponent(
            name="Rolling PF",
            raw_value=rolling_pf,
            normalized_score=pf_score,
            weight=COMPONENT_WEIGHTS["rolling_pf"],
            weighted_score=pf_score * COMPONENT_WEIGHTS["rolling_pf"],
            detail=f"PF = {rolling_pf:.3f}",
        ))

        # ── Component 2: Expectancy (20%) ──
        all_r = [t.get("realized_r", 0) or 0 for t in self._trades[:50]]
        expectancy = sum(all_r) / max(1, len(all_r))
        ev_score = self._normalize_ev(expectancy)
        score.components.append(EdgeComponent(
            name="Expectancy",
            raw_value=expectancy,
            normalized_score=ev_score,
            weight=COMPONENT_WEIGHTS["expectancy"],
            weighted_score=ev_score * COMPONENT_WEIGHTS["expectancy"],
            detail=f"EV = {expectancy:.3f}R",
        ))

        # ── Component 3: Calibration (15%) ──
        cal_error = self._calc_calibration_error()
        cal_score = max(0, min(100, 100 - cal_error * 200))
        score.components.append(EdgeComponent(
            name="Calibration",
            raw_value=cal_error,
            normalized_score=cal_score,
            weight=COMPONENT_WEIGHTS["calibration"],
            weighted_score=cal_score * COMPONENT_WEIGHTS["calibration"],
            detail=f"ECE = {cal_error:.4f}",
        ))

        # ── Component 4: Profit Capture (15%) ──
        capture = self._calc_profit_capture()
        cap_score = max(0, min(100, capture * 100))
        score.components.append(EdgeComponent(
            name="Profit Capture",
            raw_value=capture,
            normalized_score=cap_score,
            weight=COMPONENT_WEIGHTS["profit_capture"],
            weighted_score=cap_score * COMPONENT_WEIGHTS["profit_capture"],
            detail=f"Capture = {capture:.1%}",
        ))

        # ── Component 5: Feature Stability (10%) ──
        stability = self._calc_feature_stability()
        score.components.append(EdgeComponent(
            name="Feature Stability",
            raw_value=stability,
            normalized_score=stability,
            weight=COMPONENT_WEIGHTS["feature_stability"],
            weighted_score=stability * COMPONENT_WEIGHTS["feature_stability"],
            detail=f"Stability = {stability:.1f}/100",
        ))

        # ── Component 6: Decision Stability (10%) ──
        decision = self._calc_decision_stability()
        score.components.append(EdgeComponent(
            name="Decision Stability",
            raw_value=decision,
            normalized_score=decision,
            weight=COMPONENT_WEIGHTS["decision_stability"],
            weighted_score=decision * COMPONENT_WEIGHTS["decision_stability"],
            detail=f"Stability = {decision:.1f}/100",
        ))

        # ── Component 7: Regime Stability (5%) ──
        regime = self._calc_regime_stability()
        score.components.append(EdgeComponent(
            name="Regime Stability",
            raw_value=regime,
            normalized_score=regime,
            weight=COMPONENT_WEIGHTS["regime_stability"],
            weighted_score=regime * COMPONENT_WEIGHTS["regime_stability"],
            detail=f"Stability = {regime:.1f}/100",
        ))

        # ── Overall Score ──
        score.edge_confidence = sum(c.weighted_score for c in score.components)

        # ── Tier and Risk ──
        for tier in EDGE_TIERS:
            if score.edge_confidence >= tier["min_score"]:
                score.edge_tier = tier["label"]
                score.risk_pct = tier["risk_pct"]
                score.max_positions = tier["max_positions"]
                score.recommended_action = tier["action"]
                break

        # ── Derived metrics ──
        score.position_size_factor = score.risk_pct / 100.0
        score.portfolio_heat_limit = score.risk_pct / 20.0  # Max 5% at full confidence

        # ── Trend ──
        if self._previous_score is not None:
            diff = score.edge_confidence - self._previous_score
            if diff > 2:
                score.trend = "IMPROVING"
            elif diff < -2:
                score.trend = "DECLINING"
            else:
                score.trend = "STABLE"
        self._previous_score = score.edge_confidence

        # ── Forward Estimate ──
        if score.trend == "DECLINING":
            score.forward_confidence = max(0, score.edge_confidence - 5)
        elif score.trend == "IMPROVING":
            score.forward_confidence = min(100, score.edge_confidence + 3)
        else:
            score.forward_confidence = score.edge_confidence
        score.confidence_in_estimate = min(100, len(self._trades) / 5)

        # ── Alerts ──
        score.alerts = self._generate_alerts(score)

        return score

    def _normalize_pf(self, pf: float) -> float:
        """Normalize PF to 0-100 score."""
        if pf >= 1.5:
            return 100
        elif pf >= 1.0:
            return 50 + (pf - 1.0) * 100
        elif pf >= 0.5:
            return (pf - 0.5) * 100
        return 0

    def _normalize_ev(self, ev: float) -> float:
        """Normalize expectancy to 0-100 score."""
        if ev >= 0.5:
            return 100
        elif ev >= 0.0:
            return 50 + ev * 100
        elif ev >= -0.5:
            return 50 + ev * 100
        return 0

    def _calc_calibration_error(self) -> float:
        """Calculate calibration error."""
        if len(self._trades) < 20:
            return 0.5
        matched = []
        for t in self._trades[:100]:
            conf = (t.get("confidence", 0) or 0) / 100
            outcome = 1 if (t.get("realized_r", 0) or 0) > 0 else 0
            matched.append((conf, outcome))
        bucket_errors = []
        for i in range(10):
            low, high = i * 0.1, (i + 1) * 0.1
            in_bucket = [(p, o) for p, o in matched if low <= p < high]
            if in_bucket:
                avg_pred = sum(p for p, o in in_bucket) / len(in_bucket)
                actual_freq = sum(o for p, o in in_bucket) / len(in_bucket)
                bucket_errors.append(abs(avg_pred - actual_freq))
        return sum(bucket_errors) / max(1, len(bucket_errors))

    def _calc_profit_capture(self) -> float:
        """Calculate profit capture ratio."""
        capture_vals = []
        for t in self._trades[:200]:
            mfe = t.get("highest_pnl", 0) or 0
            r = t.get("realized_r", 0) or 0
            if mfe > 0:
                capture_vals.append(r / mfe)
        return sum(capture_vals) / max(1, len(capture_vals)) if capture_vals else 0.5

    def _calc_feature_stability(self) -> float:
        """Calculate feature stability."""
        if len(self._trades) < 100:
            return 50
        mid = len(self._trades) // 2
        first_half = self._trades[mid:]
        second_half = self._trades[:mid]
        drift_scores = []
        for half in [first_half, second_half]:
            high_conf = [t for t in half if (t.get("confidence", 0) or 0) > 85]
            low_conf = [t for t in half if (t.get("confidence", 0) or 0) <= 85]
            if high_conf and low_conf:
                high_wr = sum(1 for t in high_conf if (t.get("realized_r", 0) or 0) > 0) / len(high_conf)
                low_wr = sum(1 for t in low_conf if (t.get("realized_r", 0) or 0) > 0) / len(low_conf)
                drift_scores.append(abs(high_wr - low_wr))
        drift = abs(drift_scores[0] - drift_scores[1]) if len(drift_scores) >= 2 else 0.3
        return max(0, min(100, 100 - drift * 200))

    def _calc_decision_stability(self) -> float:
        """Calculate decision stability."""
        if len(self._trades) < 50:
            return 50
        by_symbol = defaultdict(list)
        for t in self._trades:
            by_symbol[t.get("symbol", "")].append(t)
        stability_scores = []
        for symbol, trades in by_symbol.items():
            if len(trades) >= 5:
                confs = [(t.get("confidence", 0) or 0) for t in trades[:10]]
                if len(confs) >= 2:
                    mean_conf = sum(confs) / len(confs)
                    variance = sum((c - mean_conf) ** 2 for c in confs) / len(confs)
                    stability_scores.append(max(0, 100 - variance / 10))
        return sum(stability_scores) / max(1, len(stability_scores)) if stability_scores else 50

    def _calc_regime_stability(self) -> float:
        """Calculate regime stability."""
        if len(self._trades) < 100:
            return 50
        mid = len(self._trades) // 2
        first_half = self._trades[mid:]
        second_half = self._trades[:mid]
        first_regimes = defaultdict(int)
        second_regimes = defaultdict(int)
        for t in first_half:
            first_regimes[t.get("regime", "unknown")] += 1
        for t in second_half:
            second_regimes[t.get("regime", "unknown")] += 1
        total_first = sum(first_regimes.values())
        total_second = sum(second_regimes.values())
        all_regimes = set(first_regimes.keys()) | set(second_regimes.keys())
        drift = 0
        for regime in all_regimes:
            p1 = first_regimes.get(regime, 0) / max(1, total_first)
            p2 = second_regimes.get(regime, 0) / max(1, total_second)
            drift += abs(p1 - p2)
        avg_drift = drift / max(1, len(all_regimes))
        return max(0, min(100, 100 - avg_drift * 200))

    def _calc_pf(self, trades: List[Dict]) -> float:
        """Calculate profit factor."""
        if not trades:
            return 0.0
        wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]
        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        return gross_profit / max(0.01, gross_loss)

    def _generate_alerts(self, score: EdgeConfidenceScore) -> List[str]:
        """Generate alerts."""
        alerts = []
        if score.edge_confidence < 40:
            alerts.append("CRITICAL: Edge confidence is below 40 — stop trading")
        elif score.edge_confidence < 60:
            alerts.append("WARNING: Edge confidence is low — minimize allocation")
        if score.trend == "DECLINING":
            alerts.append("Edge confidence is declining — monitor closely")
        return alerts
