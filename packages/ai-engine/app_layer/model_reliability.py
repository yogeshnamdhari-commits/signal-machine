"""
Model Reliability Score — Combine all health metrics into single actionable score.

Per Executive Assessment v14:
    "Instead of Calibration, Feature Drift, Prediction Error, Decision Stability,
     Rolling PF — combine them into one score:
         Model Reliability 0–100
     Example:
         95+   Production
         80–95 Normal
         65–80 Reduced Risk
         50–65 Minimal Allocation
         <50   Stop Trading
     Then Adaptive Risk can react automatically."

Key Innovation:
    v19 reported: Individual health metrics
    v20 combines: Single reliability score with risk tiers

    This allows:
        - Automatic risk adjustment based on model health
        - Clear production readiness assessment
        - Simplified decision-making
        - Integration with adaptive risk systems

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
# RELIABILITY CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Risk tiers based on reliability score
RELIABILITY_TIERS = [
    {"min_score": 95, "risk_pct": 100, "label": "PRODUCTION", "action": "Full trading"},
    {"min_score": 80, "risk_pct": 80, "label": "NORMAL", "action": "Standard trading"},
    {"min_score": 65, "risk_pct": 50, "label": "REDUCED", "action": "Reduce position sizes"},
    {"min_score": 50, "risk_pct": 25, "label": "MINIMAL", "action": "Minimal allocation"},
    {"min_score": 0, "risk_pct": 0, "label": "STOP", "action": "Stop trading"},
]

# Weights for combining metrics (must sum to 1.0)
METRIC_WEIGHTS = {
    "calibration": 0.20,
    "feature_stability": 0.20,
    "prediction_error": 0.15,
    "decision_stability": 0.15,
    "rolling_pf": 0.20,
    "regime_stability": 0.10,
}


@dataclass
class ReliabilityComponent:
    """A single component of the reliability score."""
    name: str = ""
    raw_value: float = 0.0
    normalized_score: float = 0.0  # 0-100
    weight: float = 0.0
    weighted_score: float = 0.0
    trend: str = ""               # IMPROVING / STABLE / DECLINING
    detail: str = ""

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "raw": round(self.raw_value, 4),
            "score": round(self.normalized_score, 1),
            "weight": round(self.weight, 3),
            "weighted": round(self.weighted_score, 2),
            "trend": self.trend,
            "detail": self.detail,
        }


@dataclass
class ReliabilityScore:
    """Complete model reliability assessment."""
    timestamp: float = 0.0

    # Components
    components: List[ReliabilityComponent] = field(default_factory=list)

    # Overall score
    reliability_score: float = 0.0   # 0-100
    reliability_tier: str = ""       # PRODUCTION / NORMAL / REDUCED / MINIMAL / STOP
    risk_pct: float = 100.0         # Risk allocation %
    recommended_action: str = ""

    # Trend
    score_trend: str = ""           # IMPROVING / STABLE / DECLINING
    trend_strength: float = 0.0     # -1 to 1

    # Forward estimate
    forward_reliability: float = 0.0  # Estimated reliability in 50 trades
    confidence_in_estimate: float = 0.0

    # Alerts
    alerts: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "components": [c.to_dict() for c in self.components],
            "reliability": {
                "score": round(self.reliability_score, 1),
                "tier": self.reliability_tier,
                "risk_pct": round(self.risk_pct, 1),
                "action": self.recommended_action,
            },
            "trend": {
                "direction": self.score_trend,
                "strength": round(self.trend_strength, 3),
            },
            "forward": {
                "estimated_score": round(self.forward_reliability, 1),
                "confidence": round(self.confidence_in_estimate, 1),
            },
            "alerts": self.alerts,
        }


class ModelReliabilityScorer:
    """
    Combines all health metrics into a single reliability score.

    Per Executive Assessment v14:
        "Combine them into one score. Then Adaptive Risk can
         react automatically."

    This engine:
        1. Aggregates calibration, stability, error, PF metrics
        2. Calculates weighted reliability score
        3. Assigns risk tier based on score
        4. Estimates forward reliability
        5. Provides clear action recommendations

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
                       confidence, institutional_score
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load model reliability scorer: {}", e)

    def evaluate(self) -> ReliabilityScore:
        """
        Evaluate model reliability and generate score.

        Returns:
            ReliabilityScore with complete assessment
        """
        self._ensure_loaded()

        score = ReliabilityScore(timestamp=time.time())

        if not self._trades or len(self._trades) < 50:
            score.reliability_score = 0
            score.reliability_tier = "STOP"
            score.risk_pct = 0
            score.recommended_action = "Insufficient data for reliability assessment"
            return score

        # ── Component 1: Calibration (20%) ──
        calibration = self._calc_calibration_score()
        score.components.append(ReliabilityComponent(
            name="Calibration",
            raw_value=calibration["raw"],
            normalized_score=calibration["score"],
            weight=METRIC_WEIGHTS["calibration"],
            weighted_score=calibration["score"] * METRIC_WEIGHTS["calibration"],
            detail=f"ECE = {calibration['raw']:.4f}",
        ))

        # ── Component 2: Feature Stability (20%) ──
        stability = self._calc_feature_stability_score()
        score.components.append(ReliabilityComponent(
            name="Feature Stability",
            raw_value=stability["raw"],
            normalized_score=stability["score"],
            weight=METRIC_WEIGHTS["feature_stability"],
            weighted_score=stability["score"] * METRIC_WEIGHTS["feature_stability"],
            detail=f"Stability = {stability['raw']:.3f}",
        ))

        # ── Component 3: Prediction Error (15%) ──
        pred_error = self._calc_prediction_error_score()
        score.components.append(ReliabilityComponent(
            name="Prediction Error",
            raw_value=pred_error["raw"],
            normalized_score=pred_error["score"],
            weight=METRIC_WEIGHTS["prediction_error"],
            weighted_score=pred_error["score"] * METRIC_WEIGHTS["prediction_error"],
            detail=f"Error = {pred_error['raw']:.4f}",
        ))

        # ── Component 4: Decision Stability (15%) ──
        decision = self._calc_decision_stability_score()
        score.components.append(ReliabilityComponent(
            name="Decision Stability",
            raw_value=decision["raw"],
            normalized_score=decision["score"],
            weight=METRIC_WEIGHTS["decision_stability"],
            weighted_score=decision["score"] * METRIC_WEIGHTS["decision_stability"],
            detail=f"Stability = {decision['raw']:.1f}/100",
        ))

        # ── Component 5: Rolling PF (20%) ──
        rolling_pf = self._calc_rolling_pf_score()
        score.components.append(ReliabilityComponent(
            name="Rolling PF",
            raw_value=rolling_pf["raw"],
            normalized_score=rolling_pf["score"],
            weight=METRIC_WEIGHTS["rolling_pf"],
            weighted_score=rolling_pf["score"] * METRIC_WEIGHTS["rolling_pf"],
            detail=f"PF = {rolling_pf['raw']:.3f}",
        ))

        # ── Component 6: Regime Stability (10%) ──
        regime = self._calc_regime_stability_score()
        score.components.append(ReliabilityComponent(
            name="Regime Stability",
            raw_value=regime["raw"],
            normalized_score=regime["score"],
            weight=METRIC_WEIGHTS["regime_stability"],
            weighted_score=regime["score"] * METRIC_WEIGHTS["regime_stability"],
            detail=f"Stability = {regime['raw']:.3f}",
        ))

        # ── Overall Score ──
        score.reliability_score = sum(c.weighted_score for c in score.components)

        # ── Tier and Risk ──
        for tier in RELIABILITY_TIERS:
            if score.reliability_score >= tier["min_score"]:
                score.reliability_tier = tier["label"]
                score.risk_pct = tier["risk_pct"]
                score.recommended_action = tier["action"]
                break

        # ── Trend ──
        if self._previous_score is not None:
            diff = score.reliability_score - self._previous_score
            if diff > 2:
                score.score_trend = "IMPROVING"
                score.trend_strength = min(1.0, diff / 10)
            elif diff < -2:
                score.score_trend = "DECLINING"
                score.trend_strength = max(-1.0, diff / 10)
            else:
                score.score_trend = "STABLE"
                score.trend_strength = 0.0
        self._previous_score = score.reliability_score

        # ── Forward Estimate ──
        score.forward_reliability = self._estimate_forward_reliability(score)
        score.confidence_in_estimate = min(100, len(self._trades) / 5)

        # ── Alerts ──
        score.alerts = self._generate_alerts(score)

        return score

    def _calc_calibration_score(self) -> Dict:
        """Calculate calibration score (0-100)."""
        if len(self._trades) < 20:
            return {"raw": 0.5, "score": 50.0}

        # Use confidence as proxy for predicted probability
        matched = []
        for t in self._trades[:100]:
            conf = (t.get("confidence", 0) or 0) / 100
            outcome = 1 if (t.get("realized_r", 0) or 0) > 0 else 0
            matched.append((conf, outcome))

        if not matched:
            return {"raw": 0.5, "score": 50.0}

        # Build calibration curve
        bucket_errors = []
        for i in range(10):
            low = i * 0.1
            high = (i + 1) * 0.1
            in_bucket = [(p, o) for p, o in matched if low <= p < high]
            if in_bucket:
                avg_pred = sum(p for p, o in in_bucket) / len(in_bucket)
                actual_freq = sum(o for p, o in in_bucket) / len(in_bucket)
                bucket_errors.append(abs(avg_pred - actual_freq))

        ece = sum(bucket_errors) / max(1, len(bucket_errors))

        # Score: lower ECE = higher score
        score = max(0, min(100, 100 - ece * 200))
        return {"raw": ece, "score": score}

    def _calc_feature_stability_score(self) -> Dict:
        """Calculate feature stability score (0-100)."""
        if len(self._trades) < 100:
            return {"raw": 0.3, "score": 50.0}

        # Compare first half vs second half
        mid = len(self._trades) // 2
        first_half = self._trades[mid:]
        second_half = self._trades[:mid]

        # Compare win rates by confidence bucket
        drift_scores = []

        for half in [first_half, second_half]:
            high_conf = [t for t in half if (t.get("confidence", 0) or 0) > 85]
            low_conf = [t for t in half if (t.get("confidence", 0) or 0) <= 85]

            if high_conf and low_conf:
                high_wr = sum(1 for t in high_conf if (t.get("realized_r", 0) or 0) > 0) / len(high_conf)
                low_wr = sum(1 for t in low_conf if (t.get("realized_r", 0) or 0) > 0) / len(low_conf)
                drift_scores.append(abs(high_wr - low_wr))

        if len(drift_scores) >= 2:
            drift = abs(drift_scores[0] - drift_scores[1])
        else:
            drift = 0.3

        # Score: lower drift = higher score
        score = max(0, min(100, 100 - drift * 200))
        return {"raw": drift, "score": score}

    def _calc_prediction_error_score(self) -> Dict:
        """Calculate prediction error score (0-100)."""
        if len(self._trades) < 100:
            return {"raw": 0.1, "score": 50.0}

        # Compare recent PF vs older PF
        recent = self._trades[:50]
        older = self._trades[50:100]

        recent_pf = self._calc_pf(recent)
        older_pf = self._calc_pf(older)

        error = abs(recent_pf - older_pf)

        # Score: lower error = higher score
        score = max(0, min(100, 100 - error * 100))
        return {"raw": error, "score": score}

    def _calc_decision_stability_score(self) -> Dict:
        """Calculate decision stability score (0-100)."""
        if len(self._trades) < 50:
            return {"raw": 50.0, "score": 50.0}

        # Compare confidence scores for similar trades
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

        if stability_scores:
            avg_stability = sum(stability_scores) / len(stability_scores)
        else:
            avg_stability = 50.0

        return {"raw": avg_stability, "score": avg_stability}

    def _calc_rolling_pf_score(self) -> Dict:
        """Calculate rolling PF score (0-100)."""
        rolling_pf = self._calc_pf(self._trades[:50])

        # Score: PF > 1.5 = 100, PF = 1.0 = 50, PF < 0.5 = 0
        if rolling_pf >= 1.5:
            score = 100
        elif rolling_pf >= 1.0:
            score = 50 + (rolling_pf - 1.0) * 100
        elif rolling_pf >= 0.5:
            score = (rolling_pf - 0.5) * 100
        else:
            score = 0

        return {"raw": rolling_pf, "score": max(0, min(100, score))}

    def _calc_regime_stability_score(self) -> Dict:
        """Calculate regime stability score (0-100)."""
        if len(self._trades) < 100:
            return {"raw": 0.2, "score": 50.0}

        mid = len(self._trades) // 2
        first_half = self._trades[mid:]
        second_half = self._trades[:mid]

        # Compare regime distributions
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

        # Score: lower drift = higher score
        score = max(0, min(100, 100 - avg_drift * 200))
        return {"raw": avg_drift, "score": score}

    def _calc_pf(self, trades: List[Dict]) -> float:
        """Calculate profit factor."""
        if not trades:
            return 0.0
        wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]
        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        return gross_profit / max(0.01, gross_loss)

    def _estimate_forward_reliability(self, current: ReliabilityScore) -> float:
        """Estimate reliability in next 50 trades."""
        # Simple extrapolation based on trend
        if current.score_trend == "DECLINING":
            return max(0, current.reliability_score - 5)
        elif current.score_trend == "IMPROVING":
            return min(100, current.reliability_score + 3)
        return current.reliability_score

    def _generate_alerts(self, score: ReliabilityScore) -> List[str]:
        """Generate alerts for low reliability."""
        alerts = []

        if score.reliability_score < 50:
            alerts.append("CRITICAL: Model reliability is below 50 — stop trading")
        elif score.reliability_score < 65:
            alerts.append("WARNING: Model reliability is reduced — minimize allocation")
        elif score.reliability_score < 80:
            alerts.append("NOTICE: Model reliability is normal — standard trading")

        if score.score_trend == "DECLINING":
            alerts.append("Model reliability is declining — monitor closely")

        return alerts

    def get_risk_multiplier(self) -> float:
        """Get risk multiplier based on reliability score."""
        score = self.evaluate()
        return score.risk_pct / 100.0

    def is_trading_allowed(self) -> bool:
        """Check if trading is allowed based on reliability."""
        score = self.evaluate()
        return score.risk_pct > 0
