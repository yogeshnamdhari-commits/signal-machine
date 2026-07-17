"""
Model Health Dashboard — Monitor the health of the predictive model.

Per Executive Assessment v13:
    "Add a Model Health panel.
     Metric                        Purpose
     Calibration error             Are probabilities still reliable?
     Feature drift                 Are feature relationships changing?
     Prediction error trend        Is the model becoming stale?
     Regime drift                  Has the market changed materially?
     Decision stability           Are similar situations producing similar scores?

     This would complement the existing Performance Dashboard by monitoring
     the health of the predictive model itself."

Key Innovation:
    v18 measured: Individual metrics (calibration, stability, error)
    v19 aggregates: Unified model health dashboard

    This allows:
        - Single view of model health
        - Early warning of model degradation
        - Guided recalibration decisions
        - Production readiness monitoring

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


@dataclass
class HealthMetric:
    """A single health metric."""
    name: str = ""
    value: float = 0.0
    target: float = 0.0
    status: str = ""           # GOOD / WARNING / CRITICAL
    trend: str = ""            # IMPROVING / STABLE / DECLINING
    detail: str = ""

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "value": round(self.value, 3),
            "target": round(self.target, 3),
            "status": self.status,
            "trend": self.trend,
            "detail": self.detail,
        }


@dataclass
class ModelHealthDashboard:
    """Complete model health dashboard."""
    timestamp: float = 0.0

    # Health metrics
    metrics: List[HealthMetric] = field(default_factory=list)

    # Overall health
    health_score: float = 0.0    # 0-100
    health_status: str = ""      # HEALTHY / CAUTION / CRITICAL
    health_trend: str = ""       # IMPROVING / STABLE / DECLINING

    # Alerts
    alerts: List[str] = field(default_factory=list)

    # Recommendations
    recommendations: List[str] = field(default_factory=list)

    # Model readiness
    is_production_ready: bool = False
    readiness_score: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "metrics": [m.to_dict() for m in self.metrics],
            "health": {
                "score": round(self.health_score, 1),
                "status": self.health_status,
                "trend": self.health_trend,
            },
            "alerts": self.alerts,
            "recommendations": self.recommendations,
            "readiness": {
                "is_ready": self.is_production_ready,
                "score": round(self.readiness_score, 1),
            },
        }


class ModelHealthMonitor:
    """
    Monitors the health of the predictive model.

    Per Executive Assessment v13:
        "This would complement the existing Performance Dashboard
         by monitoring the health of the predictive model itself."

    This engine:
        1. Aggregates calibration error, feature drift, prediction error
        2. Calculates overall health score
        3. Detects model degradation
        4. Recommends recalibration
        5. Provides production readiness assessment

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0

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
                SELECT symbol, side, realized_r, pnl, regime, session,
                       confidence, institutional_score, closed_at
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load model health monitor: {}", e)

    def evaluate(self) -> ModelHealthDashboard:
        """
        Evaluate model health and generate dashboard.

        Returns:
            ModelHealthDashboard with complete health analysis
        """
        self._ensure_loaded()

        dashboard = ModelHealthDashboard(timestamp=time.time())

        if not self._trades or len(self._trades) < 50:
            dashboard.health_score = 0
            dashboard.health_status = "INSUFFICIENT DATA"
            dashboard.recommendations.append("Collect more data for health monitoring")
            return dashboard

        # ── Metric 1: Calibration Error ──
        cal_error = self._calc_calibration_error()
        dashboard.metrics.append(HealthMetric(
            name="Calibration Error",
            value=cal_error,
            target=0.05,
            status="GOOD" if cal_error < 0.05 else "WARNING" if cal_error < 0.10 else "CRITICAL",
            detail=f"ECE = {cal_error:.4f} (target: < 0.05)",
        ))

        # ── Metric 2: Feature Drift ──
        drift = self._calc_feature_drift()
        dashboard.metrics.append(HealthMetric(
            name="Feature Drift",
            value=drift,
            target=0.1,
            status="GOOD" if drift < 0.1 else "WARNING" if drift < 0.2 else "CRITICAL",
            detail=f"Drift score = {drift:.3f} (target: < 0.1)",
        ))

        # ── Metric 3: Prediction Error Trend ──
        pred_error = self._calc_prediction_error_trend()
        dashboard.metrics.append(HealthMetric(
            name="Prediction Error Trend",
            value=pred_error,
            target=0.0,
            status="GOOD" if abs(pred_error) < 0.1 else "WARNING" if abs(pred_error) < 0.2 else "CRITICAL",
            detail=f"Trend = {pred_error:.3f} (0 = stable)",
            trend="DECLINING" if pred_error > 0.1 else "IMPROVING" if pred_error < -0.1 else "STABLE",
        ))

        # ── Metric 4: Regime Drift ──
        regime_drift = self._calc_regime_drift()
        dashboard.metrics.append(HealthMetric(
            name="Regime Drift",
            value=regime_drift,
            target=0.15,
            status="GOOD" if regime_drift < 0.15 else "WARNING" if regime_drift < 0.30 else "CRITICAL",
            detail=f"Drift = {regime_drift:.3f} (target: < 0.15)",
        ))

        # ── Metric 5: Decision Stability ──
        decision_stability = self._calc_decision_stability()
        dashboard.metrics.append(HealthMetric(
            name="Decision Stability",
            value=decision_stability,
            target=70.0,
            status="GOOD" if decision_stability > 70 else "WARNING" if decision_stability > 50 else "CRITICAL",
            detail=f"Stability = {decision_stability:.1f}/100 (target: > 70)",
        ))

        # ── Metric 6: Rolling PF ──
        rolling_pf = self._calc_rolling_pf(50)
        dashboard.metrics.append(HealthMetric(
            name="Rolling PF (50)",
            value=rolling_pf,
            target=1.0,
            status="GOOD" if rolling_pf > 1.0 else "WARNING" if rolling_pf > 0.85 else "CRITICAL",
            detail=f"PF = {rolling_pf:.3f} (target: > 1.0)",
        ))

        # ── Overall Health Score ──
        dashboard.health_score = self._calc_health_score(dashboard.metrics)

        if dashboard.health_score >= 70:
            dashboard.health_status = "HEALTHY"
        elif dashboard.health_score >= 50:
            dashboard.health_status = "CAUTION"
        else:
            dashboard.health_status = "CRITICAL"

        # ── Alerts ──
        dashboard.alerts = self._generate_alerts(dashboard.metrics)

        # ── Recommendations ──
        dashboard.recommendations = self._generate_recommendations(dashboard)

        # ── Production Readiness ──
        dashboard.readiness_score = dashboard.health_score
        dashboard.is_production_ready = dashboard.health_score >= 70 and rolling_pf > 1.0

        return dashboard

    def _calc_calibration_error(self) -> float:
        """Calculate calibration error (ECE)."""
        if len(self._trades) < 20:
            return 0.5  # High uncertainty

        # Use confidence as proxy for predicted probability
        matched = []
        for t in self._trades[:100]:
            conf = (t.get("confidence", 0) or 0) / 100
            outcome = 1 if (t.get("realized_r", 0) or 0) > 0 else 0
            matched.append((conf, outcome))

        if not matched:
            return 0.5

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

        if bucket_errors:
            return sum(bucket_errors) / len(bucket_errors)
        return 0.5

    def _calc_feature_drift(self) -> float:
        """Calculate feature drift over time."""
        if len(self._trades) < 100:
            return 0.3  # High uncertainty

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
            return abs(drift_scores[0] - drift_scores[1])
        return 0.3

    def _calc_prediction_error_trend(self) -> float:
        """Calculate prediction error trend."""
        if len(self._trades) < 100:
            return 0.0

        # Compare recent PF vs older PF
        recent = self._trades[:50]
        older = self._trades[50:100]

        recent_pf = self._calc_pf(recent)
        older_pf = self._calc_pf(older)

        return recent_pf - older_pf

    def _calc_regime_drift(self) -> float:
        """Calculate regime distribution drift."""
        if len(self._trades) < 100:
            return 0.2

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

        # Normalize
        total_first = sum(first_regimes.values())
        total_second = sum(second_regimes.values())

        all_regimes = set(first_regimes.keys()) | set(second_regimes.keys())
        drift = 0
        for regime in all_regimes:
            p1 = first_regimes.get(regime, 0) / max(1, total_first)
            p2 = second_regimes.get(regime, 0) / max(1, total_second)
            drift += abs(p1 - p2)

        return drift / max(1, len(all_regimes))

    def _calc_decision_stability(self) -> float:
        """Calculate decision stability."""
        if len(self._trades) < 50:
            return 50.0

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
            return sum(stability_scores) / len(stability_scores)
        return 50.0

    def _calc_rolling_pf(self, window: int) -> float:
        """Calculate rolling profit factor."""
        if len(self._trades) < window:
            return self._calc_pf(self._trades)
        return self._calc_pf(self._trades[:window])

    def _calc_pf(self, trades: List[Dict]) -> float:
        """Calculate profit factor."""
        if not trades:
            return 0.0
        wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]
        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        return gross_profit / max(0.01, gross_loss)

    def _calc_health_score(self, metrics: List[HealthMetric]) -> float:
        """Calculate overall health score."""
        if not metrics:
            return 0.0

        scores = []
        for m in metrics:
            if m.status == "GOOD":
                scores.append(100)
            elif m.status == "WARNING":
                scores.append(60)
            else:
                scores.append(20)

        return sum(scores) / len(scores)

    def _generate_alerts(self, metrics: List[HealthMetric]) -> List[str]:
        """Generate alerts for critical metrics."""
        alerts = []
        for m in metrics:
            if m.status == "CRITICAL":
                alerts.append(f"CRITICAL: {m.name} — {m.detail}")
            elif m.status == "WARNING":
                alerts.append(f"WARNING: {m.name} — {m.detail}")
        return alerts

    def _generate_recommendations(self, dashboard: ModelHealthDashboard) -> List[str]:
        """Generate recommendations based on health status."""
        recs = []

        if dashboard.health_score < 50:
            recs.append("Model health is critical — consider recalibration")

        for m in dashboard.metrics:
            if m.name == "Calibration Error" and m.status == "CRITICAL":
                recs.append("Calibration error is high — recalibrate probability model")
            elif m.name == "Feature Drift" and m.status == "CRITICAL":
                recs.append("Feature relationships are changing — retrain model")
            elif m.name == "Rolling PF (50)" and m.status == "CRITICAL":
                recs.append("Recent performance is poor — reduce exposure")

        if not recs:
            recs.append("Model health is acceptable — continue monitoring")

        return recs
