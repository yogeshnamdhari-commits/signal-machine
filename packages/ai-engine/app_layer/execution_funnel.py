"""
Execution Funnel — Tracks signal flow from universe to execution.

Per Executive Assessment v18 + User v25 directive:
    "One useful addition would be an Execution Funnel:

         Universe: 250
         ↓ Tradable: 84
         ↓ Passed Regime: 39
         ↓ Passed Quality: 14
         ↓ Executed: 2
         ↓ Open: 0

     That instantly shows how selective the engine is and where
     trades are being filtered out."

Key Innovation:
    v25: Real-time funnel tracking with stage-by-stage metrics

    This allows:
        - Instant visibility into filter selectivity
        - Identifying which filter removes the most trades
        - Monitoring funnel changes over time
        - Detecting if filters are too aggressive or too loose

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class FunnelStage:
    """A single stage in the execution funnel."""
    name: str = ""
    count: int = 0
    previous_count: int = 0  # Count at previous stage
    pass_rate: float = 0.0   # Count / previous_count
    removal_pct: float = 0.0 # % removed at this stage
    description: str = ""

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "count": self.count,
            "previous_count": self.previous_count,
            "pass_rate": round(self.pass_rate, 1),
            "removal_pct": round(self.removal_pct, 1),
            "description": self.description,
        }


@dataclass
class ExecutionFunnel:
    """Complete execution funnel from universe to open positions."""
    timestamp: float = 0.0

    # Funnel stages
    stages: List[FunnelStage] = field(default_factory=list)

    # Summary
    universe_size: int = 0
    final_executed: int = 0
    selectivity: float = 0.0  # executed / universe
    bottleneck_stage: str = ""
    bottleneck_removal: float = 0.0

    # Historical comparison
    avg_selectivity_7d: float = 0.0
    avg_selectivity_30d: float = 0.0
    selectivity_trend: str = ""  # INCREASING / STABLE / DECREASING

    # Diagnosis
    diagnosis: str = ""
    recommendation: str = ""

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "stages": [s.to_dict() for s in self.stages],
            "summary": {
                "universe_size": self.universe_size,
                "final_executed": self.final_executed,
                "selectivity": round(self.selectivity, 2),
                "bottleneck_stage": self.bottleneck_stage,
                "bottleneck_removal": round(self.bottleneck_removal, 1),
            },
            "historical": {
                "avg_selectivity_7d": round(self.avg_selectivity_7d, 2),
                "avg_selectivity_30d": round(self.avg_selectivity_30d, 2),
                "trend": self.selectivity_trend,
            },
            "diagnosis": self.diagnosis,
            "recommendation": self.recommendation,
        }


class ExecutionFunnelEngine:
    """
    Tracks signal flow from universe to execution.

    Per v25 directive:
        "An Execution Funnel that instantly shows how selective
         the engine is and where trades are being filtered out."

    This engine:
        1. Records funnel snapshots after each pipeline run
        2. Calculates stage-by-stage pass rates
        3. Identifies the bottleneck stage
        4. Tracks selectivity trends over time

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self):
        self._history: List[Dict] = []  # Historical funnel snapshots

    def record_snapshot(
        self,
        universe_size: int = 0,
        tradable: int = 0,
        passed_symbol_filter: int = 0,
        passed_validation: int = 0,
        passed_regime: int = 0,
        passed_quality: int = 0,
        passed_institution: int = 0,
        passed_ev: int = 0,
        passed_reward: int = 0,
        passed_portfolio: int = 0,
        passed_correlation: int = 0,
        passed_risk: int = 0,
        passed_sizing: int = 0,
        passed_execution_quality: int = 0,
        passed_eligibility: int = 0,
        executed: int = 0,
        open_positions: int = 0,
    ) -> ExecutionFunnel:
        """Record a funnel snapshot and return analysis."""
        stages = [
            FunnelStage(
                name="Universe",
                count=universe_size,
                previous_count=universe_size,
                pass_rate=100.0,
                removal_pct=0.0,
                description="All symbols in scan universe",
            ),
            FunnelStage(
                name="Tradable",
                count=tradable,
                previous_count=universe_size,
                pass_rate=tradable / max(1, universe_size) * 100,
                removal_pct=(universe_size - tradable) / max(1, universe_size) * 100,
                description="Symbols with sufficient liquidity/data",
            ),
            FunnelStage(
                name="Symbol Filter",
                count=passed_symbol_filter,
                previous_count=tradable,
                pass_rate=passed_symbol_filter / max(1, tradable) * 100,
                removal_pct=(tradable - passed_symbol_filter) / max(1, tradable) * 100,
                description="Passed auto-symbol management",
            ),
            FunnelStage(
                name="Validation",
                count=passed_validation,
                previous_count=passed_symbol_filter,
                pass_rate=passed_validation / max(1, passed_symbol_filter) * 100,
                removal_pct=(passed_symbol_filter - passed_validation) / max(1, passed_symbol_filter) * 100,
                description="Passed quality validation",
            ),
            FunnelStage(
                name="Regime",
                count=passed_regime,
                previous_count=passed_validation,
                pass_rate=passed_regime / max(1, passed_validation) * 100,
                removal_pct=(passed_validation - passed_regime) / max(1, passed_validation) * 100,
                description="Passed regime filter",
            ),
            FunnelStage(
                name="Quality",
                count=passed_quality,
                previous_count=passed_regime,
                pass_rate=passed_quality / max(1, passed_regime) * 100,
                removal_pct=(passed_regime - passed_quality) / max(1, passed_regime) * 100,
                description="Passed trade quality score",
            ),
            FunnelStage(
                name="Institution",
                count=passed_institution,
                previous_count=passed_quality,
                pass_rate=passed_institution / max(1, passed_quality) * 100,
                removal_pct=(passed_quality - passed_institution) / max(1, passed_quality) * 100,
                description="Passed institution agreement",
            ),
            FunnelStage(
                name="EV",
                count=passed_ev,
                previous_count=passed_institution,
                pass_rate=passed_ev / max(1, passed_institution) * 100,
                removal_pct=(passed_institution - passed_ev) / max(1, passed_institution) * 100,
                description="Passed positive expected value",
            ),
            FunnelStage(
                name="Reward",
                count=passed_reward,
                previous_count=passed_ev,
                pass_rate=passed_reward / max(1, passed_ev) * 100,
                removal_pct=(passed_ev - passed_reward) / max(1, passed_ev) * 100,
                description="Passed reward filter",
            ),
            FunnelStage(
                name="Portfolio",
                count=passed_portfolio,
                previous_count=passed_reward,
                pass_rate=passed_portfolio / max(1, passed_reward) * 100,
                removal_pct=(passed_reward - passed_portfolio) / max(1, passed_reward) * 100,
                description="Passed portfolio limits",
            ),
            FunnelStage(
                name="Correlation",
                count=passed_correlation,
                previous_count=passed_portfolio,
                pass_rate=passed_correlation / max(1, passed_portfolio) * 100,
                removal_pct=(passed_portfolio - passed_correlation) / max(1, passed_portfolio) * 100,
                description="Passed correlation filter",
            ),
            FunnelStage(
                name="Risk",
                count=passed_risk,
                previous_count=passed_correlation,
                pass_rate=passed_risk / max(1, passed_correlation) * 100,
                removal_pct=(passed_correlation - passed_risk) / max(1, passed_correlation) * 100,
                description="Passed adaptive risk governor",
            ),
            FunnelStage(
                name="Sizing",
                count=passed_sizing,
                previous_count=passed_risk,
                pass_rate=passed_sizing / max(1, passed_risk) * 100,
                removal_pct=(passed_risk - passed_sizing) / max(1, passed_risk) * 100,
                description="Passed position sizing",
            ),
            FunnelStage(
                name="Exec Quality",
                count=passed_execution_quality,
                previous_count=passed_sizing,
                pass_rate=passed_execution_quality / max(1, passed_sizing) * 100,
                removal_pct=(passed_sizing - passed_execution_quality) / max(1, passed_sizing) * 100,
                description="Passed execution quality filter",
            ),
            FunnelStage(
                name="Eligibility",
                count=passed_eligibility,
                previous_count=passed_execution_quality,
                pass_rate=passed_eligibility / max(1, passed_execution_quality) * 100,
                removal_pct=(passed_execution_quality - passed_eligibility) / max(1, passed_execution_quality) * 100,
                description="Passed elite eligibility filter",
            ),
            FunnelStage(
                name="Executed",
                count=executed,
                previous_count=passed_eligibility,
                pass_rate=executed / max(1, passed_eligibility) * 100,
                removal_pct=(passed_eligibility - executed) / max(1, passed_eligibility) * 100,
                description="Signals that were executed",
            ),
            FunnelStage(
                name="Open",
                count=open_positions,
                previous_count=executed,
                pass_rate=open_positions / max(1, executed) * 100,
                removal_pct=(executed - open_positions) / max(1, executed) * 100,
                description="Currently open positions",
            ),
        ]

        # Remove zero-count trailing stages
        while stages and stages[-1].count == 0 and len(stages) > 3:
            stages.pop()

        # Find bottleneck (stage with highest removal %)
        bottleneck = max(stages[1:], key=lambda s: s.removal_pct) if len(stages) > 1 else stages[0]

        # Calculate selectivity
        selectivity = executed / max(1, universe_size) * 100

        # Build funnel
        funnel = ExecutionFunnel(
            timestamp=time.time(),
            stages=stages,
            universe_size=universe_size,
            final_executed=executed,
            selectivity=selectivity,
            bottleneck_stage=bottleneck.name,
            bottleneck_removal=bottleneck.removal_pct,
        )

        # Record in history
        self._history.append({
            "timestamp": time.time(),
            "universe": universe_size,
            "executed": executed,
            "selectivity": selectivity,
        })

        # Historical averages
        if len(self._history) >= 7:
            recent_7 = self._history[-7:]
            funnel.avg_selectivity_7d = sum(h["selectivity"] for h in recent_7) / len(recent_7)
        if len(self._history) >= 30:
            recent_30 = self._history[-30:]
            funnel.avg_selectivity_30d = sum(h["selectivity"] for h in recent_30) / len(recent_30)

        # Trend
        if funnel.avg_selectivity_7d > funnel.avg_selectivity_30d * 1.1:
            funnel.selectivity_trend = "INCREASING"
        elif funnel.avg_selectivity_7d < funnel.avg_selectivity_30d * 0.9:
            funnel.selectivity_trend = "DECREASING"
        else:
            funnel.selectivity_trend = "STABLE"

        # Diagnosis
        if selectivity < 0.5:
            funnel.diagnosis = (
                f"Extremely selective — {selectivity:.1f}% of universe executed. "
                f"Filter may be too aggressive."
            )
        elif selectivity < 2.0:
            funnel.diagnosis = (
                f"Highly selective — {selectivity:.1f}% of universe executed. "
                f"Bottleneck: {bottleneck.name} ({bottleneck.removal_pct:.0f}% removed)"
            )
        elif selectivity < 5.0:
            funnel.diagnosis = (
                f"Moderately selective — {selectivity:.1f}% executed. "
                f"Bottleneck: {bottleneck.name}"
            )
        else:
            funnel.diagnosis = (
                f"Low selectivity — {selectivity:.1f}% executed. "
                f"Consider tightening filters."
            )

        # Recommendation
        if bottleneck.removal_pct > 80:
            funnel.recommendation = (
                f"'{bottleneck.name}' removes {bottleneck.removal_pct:.0f}% of signals — "
                f"verify this is intentional and not a data issue"
            )
        elif selectivity < 0.5:
            funnel.recommendation = (
                "Selectivity is very low — pipeline may be rejecting viable trades. "
                "Review bottleneck stages."
            )
        elif selectivity > 10:
            funnel.recommendation = (
                "Selectivity is high — pipeline may be too permissive. "
                "Consider tightening admission criteria."
            )
        else:
            funnel.recommendation = "Funnel selectivity is within normal range"

        return funnel

    def get_history(self) -> List[Dict]:
        """Get historical funnel snapshots."""
        return list(self._history)
