"""
Transparent Edge Confidence — Make edge confidence calculation transparent.

Per Executive Assessment v17:
    "Make the edge confidence calculation more transparent.
     Instead of Edge Confidence = 32%, show:
         Edge Confidence = Trade Admission Quality 35%
                        + Prediction Calibration 20%
                        + Rolling PF 20%
                        + Feature Stability 15%
                        + Regime Stability 10%
     Then every change can be traced to a measurable contributor."

Key Innovation:
    v22 reported: Single edge confidence score
    v23 makes: Score fully transparent with component breakdown

    This allows:
        - Tracing every change to a measurable contributor
        - Understanding why confidence is low
        - Targeting specific improvements
        - Avoiding blind optimization

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
class TransparentComponent:
    """A single component with full transparency."""
    name: str = ""
    raw_value: float = 0.0
    normalized_score: float = 0.0  # 0-100
    weight: float = 0.0
    weighted_score: float = 0.0
    contribution_pct: float = 0.0  # % of total score
    detail: str = ""
    trend: str = ""               # IMPROVING / STABLE / DECLINING
    confidence: float = 0.0       # Confidence in this component (0-100)

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "raw": round(self.raw_value, 4),
            "score": round(self.normalized_score, 1),
            "weight": round(self.weight, 3),
            "weighted": round(self.weighted_score, 2),
            "contribution": round(self.contribution_pct, 1),
            "detail": self.detail,
            "trend": self.trend,
            "confidence": round(self.confidence, 1),
        }


@dataclass
class TransparentEdgeScore:
    """Complete transparent edge confidence assessment."""
    timestamp: float = 0.0

    # Components with full transparency
    components: List[TransparentComponent] = field(default_factory=list)

    # Overall score
    edge_confidence: float = 0.0
    edge_tier: str = ""
    risk_pct: float = 100.0
    max_positions: int = 5
    recommended_action: str = ""

    # Transparency
    score_breakdown: str = ""     # Human-readable breakdown
    biggest_contributor: str = "" # Component contributing most
    biggest_detractor: str = ""   # Component hurting most
    improvement_targets: List[str] = field(default_factory=list)

    # Derived
    position_size_factor: float = 1.0
    portfolio_heat_limit: float = 5.0

    # Trend
    trend: str = ""
    previous_score: Optional[float] = None

    # Forward
    forward_confidence: float = 0.0
    confidence_in_estimate: float = 0.0

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
            "transparency": {
                "breakdown": self.score_breakdown,
                "biggest_contributor": self.biggest_contributor,
                "biggest_detractor": self.biggest_detractor,
                "improvement_targets": self.improvement_targets,
            },
            "derived": {
                "position_size_factor": round(self.position_size_factor, 3),
                "portfolio_heat_limit": round(self.portfolio_heat_limit, 1),
            },
            "trend": self.trend,
            "forward": {
                "estimated": round(self.forward_confidence, 1),
                "confidence": round(self.confidence_in_estimate, 1),
            },
        }


# ═══════════════════════════════════════════════════════════════
# RISK TIERS
# ═══════════════════════════════════════════════════════════════

EDGE_TIERS = [
    {"min_score": 90, "risk_pct": 100, "max_positions": 5, "label": "HIGH CONFIDENCE", "action": "Full allocation"},
    {"min_score": 75, "risk_pct": 80, "max_positions": 4, "label": "CONFIDENT", "action": "Standard allocation"},
    {"min_score": 60, "risk_pct": 50, "max_positions": 3, "label": "NEUTRAL", "action": "Reduced allocation"},
    {"min_score": 40, "risk_pct": 25, "max_positions": 2, "label": "LOW CONFIDENCE", "action": "Minimal allocation"},
    {"min_score": 0, "risk_pct": 0, "max_positions": 0, "label": "NO EDGE", "action": "Stop trading"},
]


class TransparentEdgeScorer:
    """
    Makes edge confidence calculation fully transparent.

    Per Executive Assessment v17:
        "Then every change can be traced to a measurable contributor."

    This engine:
        1. Calculates each component with full transparency
        2. Shows contribution of each component to total score
        3. Identifies biggest contributor and detractor
        4. Recommends specific improvement targets
        5. Provides confidence in each component estimate

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
            logger.warning("Could not load transparent edge scorer: {}", e)

    def evaluate(self) -> TransparentEdgeScore:
        """
        Evaluate edge confidence with full transparency.

        Returns:
            TransparentEdgeScore with complete breakdown
        """
        self._ensure_loaded()

        score = TransparentEdgeScore(timestamp=time.time())

        if not self._trades or len(self._trades) < 50:
            score.edge_confidence = 0
            score.edge_tier = "NO EDGE"
            score.risk_pct = 0
            score.max_positions = 0
            score.recommended_action = "Insufficient data"
            score.score_breakdown = "No data available"
            return score

        # ── Calculate components ──
        components = []

        # Component 1: Trade Admission Quality (35%)
        admission = self._calc_admission_quality()
        components.append(TransparentComponent(
            name="Trade Admission Quality",
            raw_value=admission["raw"],
            normalized_score=admission["score"],
            weight=0.35,
            weighted_score=admission["score"] * 0.35,
            contribution_pct=0,  # Calculated after sum
            detail=f"Precision={admission['precision']:.3f}, Recall={admission['recall']:.3f}",
            confidence=min(100, len(self._trades) * 2),
        ))

        # Component 2: Prediction Calibration (20%)
        calibration = self._calc_calibration()
        components.append(TransparentComponent(
            name="Prediction Calibration",
            raw_value=calibration["raw"],
            normalized_score=calibration["score"],
            weight=0.20,
            weighted_score=calibration["score"] * 0.20,
            contribution_pct=0,
            detail=f"ECE = {calibration['raw']:.4f}",
            confidence=min(100, len(self._trades) * 2),
        ))

        # Component 3: Rolling PF (20%)
        rolling_pf = self._calc_rolling_pf()
        components.append(TransparentComponent(
            name="Rolling PF",
            raw_value=rolling_pf["raw"],
            normalized_score=rolling_pf["score"],
            weight=0.20,
            weighted_score=rolling_pf["score"] * 0.20,
            contribution_pct=0,
            detail=f"PF = {rolling_pf['raw']:.3f}",
            confidence=min(100, len(self._trades) * 2),
        ))

        # Component 4: Feature Stability (15%)
        stability = self._calc_feature_stability()
        components.append(TransparentComponent(
            name="Feature Stability",
            raw_value=stability["raw"],
            normalized_score=stability["score"],
            weight=0.15,
            weighted_score=stability["score"] * 0.15,
            contribution_pct=0,
            detail=f"Drift = {stability['raw']:.3f}",
            confidence=min(100, len(self._trades)),
        ))

        # Component 5: Regime Stability (10%)
        regime = self._calc_regime_stability()
        components.append(TransparentComponent(
            name="Regime Stability",
            raw_value=regime["raw"],
            normalized_score=regime["score"],
            weight=0.10,
            weighted_score=regime["score"] * 0.10,
            contribution_pct=0,
            detail=f"Drift = {regime['raw']:.3f}",
            confidence=min(100, len(self._trades)),
        ))

        # ── Calculate contributions ──
        total_weighted = sum(c.weighted_score for c in components)
        for c in components:
            c.contribution_pct = (c.weighted_score / max(0.01, total_weighted)) * 100

        score.components = components

        # ── Overall Score ──
        score.edge_confidence = total_weighted

        # ── Tier and Risk ──
        for tier in EDGE_TIERS:
            if score.edge_confidence >= tier["min_score"]:
                score.edge_tier = tier["label"]
                score.risk_pct = tier["risk_pct"]
                score.max_positions = tier["max_positions"]
                score.recommended_action = tier["action"]
                break

        # ── Derived ──
        score.position_size_factor = score.risk_pct / 100.0
        score.portfolio_heat_limit = score.risk_pct / 20.0

        # ── Transparency ──
        score.score_breakdown = self._format_breakdown(components, score.edge_confidence)

        # Find biggest contributor and detractor
        if components:
            score.biggest_contributor = max(components, key=lambda c: c.weighted_score).name
            score.biggest_detractor = min(components, key=lambda c: c.weighted_score).name

        # Improvement targets
        score.improvement_targets = self._find_improvement_targets(components)

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

        # ── Forward ──
        if score.trend == "DECLINING":
            score.forward_confidence = max(0, score.edge_confidence - 5)
        elif score.trend == "IMPROVING":
            score.forward_confidence = min(100, score.edge_confidence + 3)
        else:
            score.forward_confidence = score.edge_confidence
        score.confidence_in_estimate = min(100, len(self._trades) / 5)

        return score

    def _calc_admission_quality(self) -> Dict:
        """Calculate admission quality score."""
        if len(self._trades) < 20:
            return {"raw": 0.5, "score": 50, "precision": 0.5, "recall": 0.5}

        # Use confidence as proxy for admission decision
        matched = []
        for t in self._trades[:200]:
            conf = (t.get("confidence", 0) or 0) / 100
            outcome = 1 if (t.get("realized_r", 0) or 0) > 0 else 0
            matched.append((conf, outcome))

        # Precision: how many admitted trades were profitable
        admitted = [o for p, o in matched if p > 0.7]
        precision = sum(admitted) / max(1, len(admitted)) if admitted else 0.5

        # Recall: how many profitable trades were admitted
        profitable = [p for p, o in matched if o == 1]
        recalled = [p for p, o in matched if o == 1 and p > 0.7]
        recall = len(recalled) / max(1, len(profitable)) if profitable else 0.5

        # Score: average of precision and recall
        score = (precision + recall) / 2 * 100

        return {"raw": precision, "score": score, "precision": precision, "recall": recall}

    def _calc_calibration(self) -> Dict:
        """Calculate calibration score."""
        if len(self._trades) < 20:
            return {"raw": 0.5, "score": 50}

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

        ece = sum(bucket_errors) / max(1, len(bucket_errors))
        score = max(0, min(100, 100 - ece * 200))

        return {"raw": ece, "score": score}

    def _calc_rolling_pf(self) -> Dict:
        """Calculate rolling PF score."""
        if not self._trades:
            return {"raw": 0, "score": 0}

        rolling = self._trades[:50]
        wins = [t.get("realized_r", 0) or 0 for t in rolling if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in rolling if (t.get("realized_r", 0) or 0) < 0]
        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        pf = gross_profit / max(0.01, gross_loss)

        if pf >= 1.5:
            score = 100
        elif pf >= 1.0:
            score = 50 + (pf - 1.0) * 100
        elif pf >= 0.5:
            score = (pf - 0.5) * 100
        else:
            score = 0

        return {"raw": pf, "score": max(0, min(100, score))}

    def _calc_feature_stability(self) -> Dict:
        """Calculate feature stability score."""
        if len(self._trades) < 100:
            return {"raw": 0.3, "score": 50}

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
        score = max(0, min(100, 100 - drift * 200))

        return {"raw": drift, "score": score}

    def _calc_regime_stability(self) -> Dict:
        """Calculate regime stability score."""
        if len(self._trades) < 100:
            return {"raw": 0.2, "score": 50}

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
        score = max(0, min(100, 100 - avg_drift * 200))

        return {"raw": avg_drift, "score": score}

    def _format_breakdown(self, components: List[TransparentComponent], total: float) -> str:
        """Format human-readable breakdown."""
        lines = [f"Edge Confidence = {total:.1f}/100"]
        for c in components:
            lines.append(f"  {c.name}: {c.normalized_score:.1f} × {c.weight:.0%} = {c.weighted_score:.1f} ({c.contribution_pct:.0f}%)")
        return "\n".join(lines)

    def _find_improvement_targets(self, components: List[TransparentComponent]) -> List[str]:
        """Find specific improvement targets."""
        targets = []
        for c in components:
            if c.normalized_score < 40:
                targets.append(f"CRITICAL: {c.name} = {c.normalized_score:.1f} — address immediately")
            elif c.normalized_score < 60:
                targets.append(f"WARNING: {c.name} = {c.normalized_score:.1f} — needs improvement")
        return targets
