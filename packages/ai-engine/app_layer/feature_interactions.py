"""
Feature Interaction Detector — Find combinations of features that work together.

Per Executive Assessment v13:
    "Research Topic 1: Feature interactions.
     Not Momentum, but Momentum + Regime + Volatility.
     Some features may only be valuable in combination."

Key Innovation:
    v18 analyzed: Individual feature importance
    v19 analyzes: Feature interaction effects

    This allows:
        - Discovering synergistic feature combinations
        - Better predictions in specific market conditions
        - More nuanced capital allocation
        - Regime-specific feature weighting

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


@dataclass
class FeatureInteraction:
    """A discovered interaction between features."""
    feature_1: str = ""
    feature_2: str = ""
    interaction_type: str = ""    # SYNERGY / REDUNDANCY / INDEPENDENT
    interaction_strength: float = 0.0  # -1 to 1 (positive = synergy)
    combined_pf: float = 0.0     # PF when both features are strong
    individual_pf_1: float = 0.0 # PF with only feature 1 strong
    individual_pf_2: float = 0.0 # PF with only feature 2 strong
    sample_size: int = 0
    confidence: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "features": [self.feature_1, self.feature_2],
            "type": self.interaction_type,
            "strength": round(self.interaction_strength, 3),
            "combined_pf": round(self.combined_pf, 2),
            "individual_pf_1": round(self.individual_pf_1, 2),
            "individual_pf_2": round(self.individual_pf_2, 2),
            "sample_size": self.sample_size,
            "confidence": round(self.confidence, 1),
        }


@dataclass
class InteractionReport:
    """Complete feature interaction analysis."""
    timestamp: float = 0.0
    interactions: List[FeatureInteraction] = field(default_factory=list)

    # Summary
    total_interactions: int = 0
    synergistic_pairs: int = 0
    redundant_pairs: int = 0
    independent_pairs: int = 0

    # Top interactions
    top_synergies: List[FeatureInteraction] = field(default_factory=list)
    top_redundancies: List[FeatureInteraction] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "summary": {
                "total": self.total_interactions,
                "synergistic": self.synergistic_pairs,
                "redundant": self.redundant_pairs,
                "independent": self.independent_pairs,
            },
            "top_synergies": [i.to_dict() for i in self.top_synergies[:5]],
            "top_redundancies": [i.to_dict() for i in self.top_redundancies[:5]],
            "all_interactions": [i.to_dict() for i in self.interactions],
        }


class FeatureInteractionDetector:
    """
    Discovers interactions between features.

    Per Executive Assessment v13:
        "Some features may only be valuable in combination."

    This engine:
        1. Tests pairs of features for interaction effects
        2. Identifies synergistic pairs (better together)
        3. Identifies redundant pairs (overlap)
        4. Recommends feature weighting adjustments

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
            logger.warning("Could not load feature interaction detector: {}", e)

    def analyze(self) -> InteractionReport:
        """
        Analyze feature interactions.

        Returns:
            InteractionReport with discovered interactions
        """
        self._ensure_loaded()

        report = InteractionReport(timestamp=time.time())

        if not self._trades or len(self._trades) < 50:
            return report

        # ── Define feature pairs to test ──
        features = ["regime", "session", "confidence", "institutional_score"]
        pairs = [(f1, f2) for i, f1 in enumerate(features) for f2 in features[i+1:]]

        for f1, f2 in pairs:
            interaction = self._test_interaction(f1, f2)
            if interaction:
                report.interactions.append(interaction)

        # ── Classify interactions ──
        for interaction in report.interactions:
            if interaction.interaction_strength > 0.1:
                report.synergistic_pairs += 1
            elif interaction.interaction_strength < -0.1:
                report.redundant_pairs += 1
            else:
                report.independent_pairs += 1

        report.total_interactions = len(report.interactions)

        # ── Top interactions ──
        report.top_synergies = sorted(
            [i for i in report.interactions if i.interaction_strength > 0],
            key=lambda i: i.interaction_strength,
            reverse=True,
        )
        report.top_redundancies = sorted(
            [i for i in report.interactions if i.interaction_strength < 0],
            key=lambda i: i.interaction_strength,
        )

        return report

    def _test_interaction(
        self,
        feature_1: str,
        feature_2: str,
    ) -> Optional[FeatureInteraction]:
        """Test interaction between two features."""
        # Define "strong" thresholds
        strong_thresholds = {
            "confidence": 85,
            "institutional_score": 85,
        }

        # Split trades into groups
        both_strong = []
        only_f1_strong = []
        only_f2_strong = []
        neither_strong = []

        for t in self._trades:
            val_1 = self._get_feature_value(t, feature_1)
            val_2 = self._get_feature_value(t, feature_2)

            f1_strong = val_1 >= strong_thresholds.get(feature_1, 0.5)
            f2_strong = val_2 >= strong_thresholds.get(feature_2, 0.5)

            if f1_strong and f2_strong:
                both_strong.append(t)
            elif f1_strong and not f2_strong:
                only_f1_strong.append(t)
            elif not f1_strong and f2_strong:
                only_f2_strong.append(t)
            else:
                neither_strong.append(t)

        # Calculate PF for each group
        pf_both = self._calc_pf(both_strong)
        pf_f1 = self._calc_pf(only_f1_strong)
        pf_f2 = self._calc_pf(only_f2_strong)

        # Interaction strength = combined - average of individuals
        if pf_f1 > 0 and pf_f2 > 0:
            avg_individual = (pf_f1 + pf_f2) / 2
            interaction_strength = pf_both - avg_individual
        else:
            return None

        # Determine interaction type
        if interaction_strength > 0.1:
            interaction_type = "SYNERGY"
        elif interaction_strength < -0.1:
            interaction_type = "REDUNDANCY"
        else:
            interaction_type = "INDEPENDENT"

        # Confidence based on sample size
        sample_size = len(both_strong) + len(only_f1_strong) + len(only_f2_strong)
        confidence = min(100, sample_size * 2)

        return FeatureInteraction(
            feature_1=feature_1,
            feature_2=feature_2,
            interaction_type=interaction_type,
            interaction_strength=interaction_strength,
            combined_pf=pf_both,
            individual_pf_1=pf_f1,
            individual_pf_2=pf_f2,
            sample_size=sample_size,
            confidence=confidence,
        )

    def _get_feature_value(self, trade: Dict, feature: str) -> float:
        """Get feature value from trade."""
        if feature == "confidence":
            return (trade.get("confidence", 0) or 0) / 100
        elif feature == "institutional_score":
            return (trade.get("institutional_score", 0) or 0) / 100
        elif feature == "regime":
            # Binary: trending vs other
            regime = trade.get("regime", "unknown")
            return 1.0 if "trending" in regime else 0.0
        elif feature == "session":
            # Binary: active vs off-hours
            session = trade.get("session", "unknown")
            return 1.0 if session in ("london", "new_york") else 0.0
        return 0.0

    def _calc_pf(self, trades: List[Dict]) -> float:
        """Calculate profit factor."""
        if not trades:
            return 0.0
        wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]
        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        return gross_profit / max(0.01, gross_loss)
