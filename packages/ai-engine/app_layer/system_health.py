"""
System Health Score — Single unified health metric for the entire system.

Per Executive Assessment v15:
    "Create a single System Health Score instead of many independent indicators.
     Example:
         Calibration
         Prediction Error
         Feature Drift
         Rolling PF
         Decision Stability
         ↓ Reliability Score
         ↓ Risk Multiplier
     That simplifies operational decisions and reduces conflicting signals."

Key Innovation:
    v20 reported: Multiple independent health metrics
    v21 combines: Single unified system health score

    This allows:
        - Simplified operational decisions
        - Automatic risk adjustment
        - Clear production readiness
        - Reduced conflicting signals

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
# SYSTEM HEALTH CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Risk tiers based on health score
HEALTH_TIERS = [
    {"min_score": 90, "risk_pct": 100, "label": "PRODUCTION", "action": "Full trading"},
    {"min_score": 75, "risk_pct": 80, "label": "HEALTHY", "action": "Standard trading"},
    {"min_score": 60, "risk_pct": 50, "label": "CAUTION", "action": "Reduce position sizes"},
    {"min_score": 40, "risk_pct": 25, "label": "STRESSED", "action": "Minimal allocation"},
    {"min_score": 0, "risk_pct": 0, "label": "CRITICAL", "action": "Stop trading"},
]

# Component weights (must sum to 1.0)
COMPONENT_WEIGHTS = {
    "model_reliability": 0.30,    # From Model Reliability Score
    "rolling_performance": 0.25,  # Rolling PF and EV
    "prediction_quality": 0.15,   # From Prediction Quality Report
    "execution_quality": 0.15,    # From Execution Quality Report
    "selection_quality": 0.15,    # From Selection Quality Report
}


@dataclass
class HealthComponent:
    """A single component of the system health score."""
    name: str = ""
    score: float = 0.0          # 0-100
    weight: float = 0.0
    weighted_score: float = 0.0
    detail: str = ""

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "score": round(self.score, 1),
            "weight": round(self.weight, 3),
            "weighted": round(self.weighted_score, 2),
            "detail": self.detail,
        }


@dataclass
class SystemHealthScore:
    """Complete system health assessment."""
    timestamp: float = 0.0

    # Components
    components: List[HealthComponent] = field(default_factory=list)

    # Overall score
    health_score: float = 0.0   # 0-100
    health_tier: str = ""       # PRODUCTION / HEALTHY / CAUTION / STRESSED / CRITICAL
    risk_pct: float = 100.0    # Risk allocation %
    recommended_action: str = ""

    # Trend
    trend: str = ""             # IMPROVING / STABLE / DECLINING
    previous_score: Optional[float] = None

    # Forward estimate
    forward_health: float = 0.0
    confidence: float = 0.0

    # Alerts
    alerts: List[str] = field(default_factory=list)

    # Recommendations
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "components": [c.to_dict() for c in self.components],
            "health": {
                "score": round(self.health_score, 1),
                "tier": self.health_tier,
                "risk_pct": round(self.risk_pct, 1),
                "action": self.recommended_action,
            },
            "trend": self.trend,
            "forward": {
                "estimated_score": round(self.forward_health, 1),
                "confidence": round(self.confidence, 1),
            },
            "alerts": self.alerts,
            "recommendations": self.recommendations,
        }


class SystemHealthMonitor:
    """
    Combines all health metrics into a single system health score.

    Per Executive Assessment v15:
        "Create a single System Health Score instead of many
         independent indicators."

    This engine:
        1. Aggregates model reliability, performance, quality metrics
        2. Calculates unified health score (0-100)
        3. Assigns risk tier based on score
        4. Provides clear action recommendations
        5. Estimates forward health

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
            logger.warning("Could not load system health monitor: {}", e)

    def evaluate(self) -> SystemHealthScore:
        """
        Evaluate system health and generate unified score.

        Returns:
            SystemHealthScore with complete assessment
        """
        self._ensure_loaded()

        score = SystemHealthScore(timestamp=time.time())

        if not self._trades or len(self._trades) < 50:
            score.health_score = 0
            score.health_tier = "CRITICAL"
            score.risk_pct = 0
            score.recommended_action = "Insufficient data"
            return score

        # ── Component 1: Model Reliability (30%) ──
        model_rel = self._calc_model_reliability()
        score.components.append(HealthComponent(
            name="Model Reliability",
            score=model_rel,
            weight=COMPONENT_WEIGHTS["model_reliability"],
            weighted_score=model_rel * COMPONENT_WEIGHTS["model_reliability"],
            detail=f"Reliability = {model_rel:.1f}/100",
        ))

        # ── Component 2: Rolling Performance (25%) ──
        rolling_perf = self._calc_rolling_performance()
        score.components.append(HealthComponent(
            name="Rolling Performance",
            score=rolling_perf,
            weight=COMPONENT_WEIGHTS["rolling_performance"],
            weighted_score=rolling_perf * COMPONENT_WEIGHTS["rolling_performance"],
            detail=f"PF = {self._calc_pf(self._trades[:50]):.3f}",
        ))

        # ── Component 3: Prediction Quality (15%) ──
        pred_quality = self._calc_prediction_quality()
        score.components.append(HealthComponent(
            name="Prediction Quality",
            score=pred_quality,
            weight=COMPONENT_WEIGHTS["prediction_quality"],
            weighted_score=pred_quality * COMPONENT_WEIGHTS["prediction_quality"],
            detail=f"Calibration = {self._calc_calibration_error():.4f}",
        ))

        # ── Component 4: Execution Quality (15%) ──
        exec_quality = self._calc_execution_quality()
        score.components.append(HealthComponent(
            name="Execution Quality",
            score=exec_quality,
            weight=COMPONENT_WEIGHTS["execution_quality"],
            weighted_score=exec_quality * COMPONENT_WEIGHTS["execution_quality"],
            detail=f"Efficiency = {self._calc_exit_efficiency():.1f}%",
        ))

        # ── Component 5: Selection Quality (15%) ──
        sel_quality = self._calc_selection_quality()
        score.components.append(HealthComponent(
            name="Selection Quality",
            score=sel_quality,
            weight=COMPONENT_WEIGHTS["selection_quality"],
            weighted_score=sel_quality * COMPONENT_WEIGHTS["selection_quality"],
            detail=f"PF = {self._calc_pf(self._trades):.3f}",
        ))

        # ── Overall Score ──
        score.health_score = sum(c.weighted_score for c in score.components)

        # ── Tier and Risk ──
        for tier in HEALTH_TIERS:
            if score.health_score >= tier["min_score"]:
                score.health_tier = tier["label"]
                score.risk_pct = tier["risk_pct"]
                score.recommended_action = tier["action"]
                break

        # ── Trend ──
        if self._previous_score is not None:
            diff = score.health_score - self._previous_score
            if diff > 2:
                score.trend = "IMPROVING"
            elif diff < -2:
                score.trend = "DECLINING"
            else:
                score.trend = "STABLE"
        self._previous_score = score.health_score

        # ── Forward Estimate ──
        if score.trend == "DECLINING":
            score.forward_health = max(0, score.health_score - 5)
        elif score.trend == "IMPROVING":
            score.forward_health = min(100, score.health_score + 3)
        else:
            score.forward_health = score.health_score
        score.confidence = min(100, len(self._trades) / 5)

        # ── Alerts ──
        score.alerts = self._generate_alerts(score)

        # ── Recommendations ──
        score.recommendations = self._generate_recommendations(score)

        return score

    def _calc_model_reliability(self) -> float:
        """Calculate model reliability score."""
        # Simplified: combine calibration, drift, stability
        cal = 100 - self._calc_calibration_error() * 200
        drift = 100 - self._calc_feature_drift() * 200
        stability = self._calc_decision_stability()
        return (cal * 0.4 + drift * 0.3 + stability * 0.3)

    def _calc_rolling_performance(self) -> float:
        """Calculate rolling performance score."""
        pf = self._calc_pf(self._trades[:50])
        if pf >= 1.5:
            return 100
        elif pf >= 1.0:
            return 50 + (pf - 1.0) * 100
        elif pf >= 0.5:
            return (pf - 0.5) * 100
        return 0

    def _calc_prediction_quality(self) -> float:
        """Calculate prediction quality score."""
        cal_error = self._calc_calibration_error()
        return max(0, min(100, 100 - cal_error * 200))

    def _calc_execution_quality(self) -> float:
        """Calculate execution quality score."""
        return self._calc_exit_efficiency()

    def _calc_selection_quality(self) -> float:
        """Calculate selection quality score."""
        pf = self._calc_pf(self._trades)
        if pf >= 1.2:
            return 90
        elif pf >= 1.0:
            return 70
        elif pf >= 0.8:
            return 50
        return 30

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

    def _calc_feature_drift(self) -> float:
        """Calculate feature drift."""
        if len(self._trades) < 100:
            return 0.3
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
        return abs(drift_scores[0] - drift_scores[1]) if len(drift_scores) >= 2 else 0.3

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

    def _calc_exit_efficiency(self) -> float:
        """Calculate exit efficiency."""
        capture_vals = []
        for t in self._trades[:200]:
            mfe = t.get("highest_pnl", 0) or 0
            r = t.get("realized_r", 0) or 0
            if mfe > 0:
                capture_vals.append((r / mfe) * 100)
        return sum(capture_vals) / max(1, len(capture_vals)) if capture_vals else 50

    def _calc_pf(self, trades: List[Dict]) -> float:
        """Calculate profit factor."""
        if not trades:
            return 0.0
        wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]
        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        return gross_profit / max(0.01, gross_loss)

    def _generate_alerts(self, score: SystemHealthScore) -> List[str]:
        """Generate alerts."""
        alerts = []
        if score.health_score < 40:
            alerts.append("CRITICAL: System health is below 40 — stop trading")
        elif score.health_score < 60:
            alerts.append("WARNING: System health is stressed — minimize allocation")
        if score.trend == "DECLINING":
            alerts.append("System health is declining — monitor closely")
        return alerts

    def _generate_recommendations(self, score: SystemHealthScore) -> List[str]:
        """Generate recommendations."""
        recs = []
        if score.health_score < 60:
            recs.append("System health is below threshold — reduce exposure")
        for c in score.components:
            if c.score < 40:
                recs.append(f"{c.name} is critically low — address immediately")
        if not recs:
            recs.append("System health is acceptable — continue monitoring")
        return recs
