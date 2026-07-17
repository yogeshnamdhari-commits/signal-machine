"""
Feature Stability Analyzer — Measure how stable feature importance is over time.

Per Executive Assessment v12:
    "Feature importance is not feature reliability.
     A feature that is important only in bull markets should not
     receive the same weight in ranges.

     Add Feature Stability:
         Feature         Importance    Stability
         Regime          35%           High
         Momentum        18%           Low
         Volume          12%           Medium
         Session         8%            High

     Then the model can automatically reduce unstable features."

Key Innovation:
    v17 measured: Feature importance (how much each feature matters)
    v18 measures: Feature stability (how consistent is each feature's importance)

    This allows:
        - Down-weighting unstable features
        - Regime-specific feature selection
        - More robust predictions
        - Better generalization

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
class FeatureStability:
    """Stability metrics for a single feature."""
    feature_name: str = ""
    importance: float = 0.0         # Current importance (0-1)
    stability: float = 0.0          # Stability score (0-100)
    stability_label: str = ""       # HIGH / MEDIUM / LOW
    trend: str = ""                 # IMPROVING / STABLE / DECLINING
    regime_variance: float = 0.0    # Variance across regimes
    time_variance: float = 0.0      # Variance over time
    confidence: float = 0.0         # Confidence in importance estimate

    def to_dict(self) -> Dict:
        return {
            "feature": self.feature_name,
            "importance": round(self.importance, 3),
            "stability": round(self.stability, 1),
            "stability_label": self.stability_label,
            "trend": self.trend,
            "regime_variance": round(self.regime_variance, 4),
            "time_variance": round(self.time_variance, 4),
            "confidence": round(self.confidence, 1),
        }


@dataclass
class FeatureStabilityReport:
    """Complete feature stability analysis."""
    timestamp: float = 0.0
    features: List[FeatureStability] = field(default_factory=list)

    # Summary
    avg_stability: float = 0.0
    most_stable_feature: str = ""
    least_stable_feature: str = ""
    unstable_features: List[str] = field(default_factory=list)

    # Recommendations
    feature_weights: Dict[str, float] = field(default_factory=dict)  # Adjusted weights

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "features": [f.to_dict() for f in self.features],
            "summary": {
                "avg_stability": round(self.avg_stability, 1),
                "most_stable": self.most_stable_feature,
                "least_stable": self.least_stable_feature,
                "unstable": self.unstable_features,
            },
            "adjusted_weights": {k: round(v, 3) for k, v in self.feature_weights.items()},
        }


class FeatureStabilityAnalyzer:
    """
    Measures how stable feature importance is over time.

    Per Executive Assessment v12:
        "A feature that is important only in bull markets should not
         receive the same weight in ranges."

    This engine:
        1. Calculates feature importance across regimes
        2. Measures variance in importance across time windows
        3. Identifies unstable features
        4. Recommends adjusted feature weights

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
            logger.warning("Could not load feature stability analyzer: {}", e)

    def analyze(self) -> FeatureStabilityReport:
        """
        Analyze feature stability across time and regimes.

        Returns:
            FeatureStabilityReport with stability metrics
        """
        self._ensure_loaded()

        report = FeatureStabilityReport(timestamp=time.time())

        if not self._trades or len(self._trades) < 50:
            return report

        # ── Define features to analyze ──
        features = ["regime", "session", "confidence", "institutional_score"]

        for feature in features:
            stability = self._analyze_feature(feature)
            report.features.append(stability)

        # ── Summary ──
        if report.features:
            report.avg_stability = sum(f.stability for f in report.features) / len(report.features)
            report.most_stable_feature = max(report.features, key=lambda f: f.stability).feature_name
            report.least_stable_feature = min(report.features, key=lambda f: f.stability).feature_name
            report.unstable_features = [
                f.feature_name for f in report.features if f.stability < 50
            ]

        # ── Adjusted weights ──
        report.feature_weights = self._calculate_adjusted_weights(report.features)

        return report

    def _analyze_feature(self, feature_name: str) -> FeatureStability:
        """Analyze stability of a single feature."""
        stability = FeatureStability(feature_name=feature_name)

        # ── Calculate importance by regime ──
        regime_importance = self._calculate_importance_by_regime(feature_name)
        if regime_importance:
            values = list(regime_importance.values())
            stability.importance = sum(values) / len(values)
            stability.regime_variance = self._calc_variance(values)

        # ── Calculate importance by time window ──
        time_importance = self._calculate_importance_by_time(feature_name)
        if time_importance:
            values = list(time_importance.values())
            stability.time_variance = self._calc_variance(values)

        # ── Stability score ──
        # Lower variance = higher stability
        total_variance = stability.regime_variance + stability.time_variance
        stability.stability = max(0, min(100, 100 - total_variance * 200))

        # ── Stability label ──
        if stability.stability >= 70:
            stability.stability_label = "HIGH"
        elif stability.stability >= 40:
            stability.stability_label = "MEDIUM"
        else:
            stability.stability_label = "LOW"

        # ── Trend ──
        if len(time_importance) >= 2:
            values = list(time_importance.values())
            first_half = values[:len(values)//2]
            second_half = values[len(values)//2:]
            avg_first = sum(first_half) / max(1, len(first_half))
            avg_second = sum(second_half) / max(1, len(second_half))

            if avg_second > avg_first * 1.1:
                stability.trend = "IMPROVING"
            elif avg_second < avg_first * 0.9:
                stability.trend = "DECLINING"
            else:
                stability.trend = "STABLE"

        # ── Confidence ──
        stability.confidence = min(100, len(self._trades) / 5)

        return stability

    def _calculate_importance_by_regime(self, feature_name: str) -> Dict[str, float]:
        """Calculate feature importance for each regime."""
        by_regime: Dict[str, List[Dict]] = defaultdict(list)
        for t in self._trades:
            by_regime[t.get("regime", "unknown")].append(t)

        importance = {}
        for regime, trades in by_regime.items():
            if len(trades) < 10:
                continue

            # Simple importance: correlation with outcome
            outcomes = [t.get("realized_r", 0) or 0 for t in trades]

            if feature_name == "regime":
                # All trades in this regime have same regime value
                # Importance = variance of outcomes in this regime
                variance = self._calc_variance(outcomes)
                importance[regime] = min(1.0, variance)

            elif feature_name == "session":
                by_session = defaultdict(list)
                for t in trades:
                    by_session[t.get("session", "unknown")].append(t.get("realized_r", 0) or 0)
                session_vars = [self._calc_variance(v) for v in by_session.values() if len(v) >= 5]
                importance[regime] = min(1.0, sum(session_vars) / max(1, len(session_vars)))

            elif feature_name == "confidence":
                high_conf = [t.get("realized_r", 0) or 0 for t in trades if (t.get("confidence", 0) or 0) > 85]
                low_conf = [t.get("realized_r", 0) or 0 for t in trades if (t.get("confidence", 0) or 0) <= 85]
                if high_conf and low_conf:
                    diff = abs(sum(high_conf)/len(high_conf) - sum(low_conf)/len(low_conf))
                    importance[regime] = min(1.0, diff)

            elif feature_name == "institutional_score":
                high_inst = [t.get("realized_r", 0) or 0 for t in trades if (t.get("institutional_score", 0) or 0) > 85]
                low_inst = [t.get("realized_r", 0) or 0 for t in trades if (t.get("institutional_score", 0) or 0) <= 85]
                if high_inst and low_inst:
                    diff = abs(sum(high_inst)/len(high_inst) - sum(low_inst)/len(low_inst))
                    importance[regime] = min(1.0, diff)

        return importance

    def _calculate_importance_by_time(self, feature_name: str) -> Dict[int, float]:
        """Calculate feature importance across time windows."""
        window_size = max(50, len(self._trades) // 5)
        importance = {}

        for i in range(0, len(self._trades), window_size):
            window = self._trades[i:i + window_size]
            if len(window) < 20:
                continue

            outcomes = [t.get("realized_r", 0) or 0 for t in window]

            if feature_name == "confidence":
                high_conf = [t.get("realized_r", 0) or 0 for t in window if (t.get("confidence", 0) or 0) > 85]
                low_conf = [t.get("realized_r", 0) or 0 for t in window if (t.get("confidence", 0) or 0) <= 85]
                if high_conf and low_conf:
                    diff = abs(sum(high_conf)/len(high_conf) - sum(low_conf)/len(low_conf))
                    importance[i] = min(1.0, diff)
                else:
                    importance[i] = 0.5
            else:
                variance = self._calc_variance(outcomes)
                importance[i] = min(1.0, variance)

        return importance

    def _calc_variance(self, values: List[float]) -> float:
        """Calculate variance of a list of values."""
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        return sum((v - mean) ** 2 for v in values) / len(values)

    def _calculate_adjusted_weights(
        self,
        features: List[FeatureStability],
    ) -> Dict[str, float]:
        """Calculate adjusted feature weights based on stability."""
        if not features:
            return {}

        # Base weight = importance
        # Adjusted weight = importance × stability_factor
        weights = {}
        total = 0

        for f in features:
            stability_factor = f.stability / 100  # 0-1
            adjusted = f.importance * (0.5 + 0.5 * stability_factor)  # At least 50% of original
            weights[f.feature_name] = adjusted
            total += adjusted

        # Normalize to sum to 1
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        return weights
