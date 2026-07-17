"""
Controlled Learning — Shadow validation and out-of-sample testing.

Per Priority B: Learning should never immediately overwrite parameters.
    Live Results → Candidate Adjustment → Shadow Validation
    → Out-of-sample Test → Promote → Production

Never: Loss → Immediately change threshold

This module manages the learning lifecycle:
    1. CANDIDATE: Learning proposes a parameter change
    2. SHADOW: New parameters are tested in parallel (shadow mode)
    3. VALIDATE: Shadow results are compared to champion
    4. PROMOTE: Only if challenger proves better over meaningful sample
    5. PRODUCTION: Champion is updated

READ-ONLY: never modifies upstream data.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from loguru import logger


class LearningPhase(str, Enum):
    CANDIDATE = "candidate"
    SHADOW = "shadow"
    VALIDATE = "validate"
    PROMOTE = "promote"
    PRODUCTION = "production"
    REJECTED = "rejected"


@dataclass
class ParameterChange:
    """A proposed parameter change."""
    parameter_name: str = ""
    current_value: float = 0.0
    proposed_value: float = 0.0
    reason: str = ""
    confidence: float = 0.0
    source_trades: int = 0
    timestamp: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "parameter": self.parameter_name,
            "current": self.current_value,
            "proposed": self.proposed_value,
            "reason": self.reason,
            "confidence": round(self.confidence, 2),
            "source_trades": self.source_trades,
        }


@dataclass
class ShadowResult:
    """Results from shadow testing."""
    change_id: str = ""
    parameter_name: str = ""
    champion_value: float = 0.0
    challenger_value: float = 0.0
    shadow_trades: int = 0
    champion_pnl: float = 0.0
    challenger_pnl: float = 0.0
    champion_pf: float = 0.0
    challenger_pf: float = 0.0
    champion_win_rate: float = 0.0
    challenger_win_rate: float = 0.0
    improvement_pct: float = 0.0
    statistically_significant: bool = False
    recommendation: str = ""  # PROMOTE / REJECT / CONTINUE_SHADOW

    def to_dict(self) -> Dict:
        return {
            "change_id": self.change_id,
            "parameter": self.parameter_name,
            "champion": self.champion_value,
            "challenger": self.challenger_value,
            "shadow_trades": self.shadow_trades,
            "champion_pnl": round(self.champion_pnl, 2),
            "challenger_pnl": round(self.challenger_pnl, 2),
            "champion_pf": round(self.champion_pf, 2),
            "challenger_pf": round(self.challenger_pf, 2),
            "improvement_pct": round(self.improvement_pct, 2),
            "significant": self.statistically_significant,
            "recommendation": self.recommendation,
        }


class ControlledLearning:
    """
    Manages the learning lifecycle with shadow validation.

    Per Priority B: Never immediately overwrite parameters.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self):
        self._candidates: Dict[str, ParameterChange] = {}
        self._shadows: Dict[str, ShadowResult] = {}
        self._history: List[Dict] = []

    def propose_change(
        self,
        parameter_name: str,
        current_value: float,
        proposed_value: float,
        reason: str,
        confidence: float = 0.5,
        source_trades: int = 0,
    ) -> str:
        """
        Propose a parameter change (CANDIDATE phase).

        Returns:
            change_id for tracking
        """
        change_id = f"{parameter_name}_{int(time.time())}"

        change = ParameterChange(
            parameter_name=parameter_name,
            current_value=current_value,
            proposed_value=proposed_value,
            reason=reason,
            confidence=confidence,
            source_trades=source_trades,
            timestamp=time.time(),
        )

        self._candidates[change_id] = change

        logger.info(
            "LEARNING CANDIDATE: {} {} → {} (confidence={:.0%}, trades={})",
            parameter_name, current_value, proposed_value, confidence, source_trades,
        )

        return change_id

    def start_shadow(self, change_id: str) -> Optional[ShadowResult]:
        """
        Start shadow testing for a candidate change.

        The challenger parameters are tested in parallel with champion.
        """
        change = self._candidates.get(change_id)
        if not change:
            return None

        shadow = ShadowResult(
            change_id=change_id,
            parameter_name=change.parameter_name,
            champion_value=change.current_value,
            challenger_value=change.proposed_value,
        )

        self._shadows[change_id] = shadow

        logger.info(
            "SHADOW STARTED: {} (champion={} challenger={})",
            change.parameter_name, change.current_value, change.proposed_value,
        )

        return shadow

    def record_shadow_trade(
        self,
        change_id: str,
        champion_pnl: float,
        challenger_pnl: float,
    ) -> None:
        """Record a trade result during shadow testing."""
        shadow = self._shadows.get(change_id)
        if not shadow:
            return

        shadow.shadow_trades += 1
        shadow.champion_pnl += champion_pnl
        shadow.challenger_pnl += challenger_pnl

    def evaluate_shadow(self, change_id: str) -> Optional[ShadowResult]:
        """
        Evaluate shadow results and decide whether to promote.

        Requires minimum shadow trades and statistically significant improvement.
        """
        shadow = self._shadows.get(change_id)
        if not shadow:
            return None

        # Minimum shadow trades before evaluation
        MIN_SHADOW_TRADES = 30

        if shadow.shadow_trades < MIN_SHADOW_TRADES:
            shadow.recommendation = "CONTINUE_SHADOW"
            logger.debug(
                "SHADOW {}: {} trades < {} minimum",
                change_id, shadow.shadow_trades, MIN_SHADOW_TRADES,
            )
            return shadow

        # Calculate metrics
        if shadow.champion_pnl != 0:
            shadow.improvement_pct = (
                (shadow.challenger_pnl - shadow.champion_pnl)
                / abs(shadow.champion_pnl) * 100
            )

        # Statistical significance check
        # Simple check: if improvement > 10% over shadow sample
        if shadow.shadow_trades >= MIN_SHADOW_TRADES:
            shadow.statistically_significant = shadow.improvement_pct > 10

        # Decision
        if shadow.statistically_significant and shadow.challenger_pnl > shadow.champion_pnl:
            shadow.recommendation = "PROMOTE"
            logger.info(
                "SHADOW PROMOTE: {} improved by {:.1f}% over {} trades",
                change_id, shadow.improvement_pct, shadow.shadow_trades,
            )
        elif shadow.shadow_trades >= 100:
            shadow.recommendation = "REJECT"
            logger.info(
                "SHADOW REJECT: {} no significant improvement over {} trades",
                change_id, shadow.shadow_trades,
            )
        else:
            shadow.recommendation = "CONTINUE_SHADOW"

        return shadow

    def promote_to_production(self, change_id: str) -> Optional[ParameterChange]:
        """
        Promote a shadow-tested change to production.

        Returns the promoted change if successful.
        """
        shadow = self._shadows.get(change_id)
        change = self._candidates.get(change_id)

        if not shadow or not change:
            return None

        if shadow.recommendation != "PROMOTE":
            logger.warning("Cannot promote {} — recommendation is {}", change_id, shadow.recommendation)
            return None

        # Record in history
        self._history.append({
            "change_id": change_id,
            "parameter": change.parameter_name,
            "old_value": change.current_value,
            "new_value": change.proposed_value,
            "shadow_trades": shadow.shadow_trades,
            "improvement_pct": shadow.improvement_pct,
            "promoted_at": time.time(),
        })

        # Clean up
        self._candidates.pop(change_id, None)
        self._shadows.pop(change_id, None)

        logger.info(
            "PROMOTED: {} {} → {} (shadow trades={}, improvement={:.1f}%)",
            change.parameter_name, change.current_value, change.proposed_value,
            shadow.shadow_trades, shadow.improvement_pct,
        )

        return change

    def reject_change(self, change_id: str) -> None:
        """Reject a candidate change."""
        change = self._candidates.pop(change_id, None)
        shadow = self._shadows.pop(change_id, None)

        if change:
            logger.info("REJECTED: {} (reason: insufficient evidence)", change.parameter_name)

    def get_pending_candidates(self) -> Dict[str, ParameterChange]:
        """Get all pending candidate changes."""
        return dict(self._candidates)

    def get_active_shadows(self) -> Dict[str, ShadowResult]:
        """Get all active shadow tests."""
        return dict(self._shadows)

    def get_history(self) -> List[Dict]:
        """Get promotion history."""
        return list(self._history)
