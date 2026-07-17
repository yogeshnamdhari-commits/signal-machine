"""
Deployment Gates — Learning → Shadow → Paper → Small Capital → Production.

Never allow Learning → Production directly.
Instead use a staged deployment pipeline.

READ-ONLY: never modifies upstream data.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from loguru import logger


class DeploymentStage(str, Enum):
    LEARNING = "learning"
    SHADOW = "shadow"
    PAPER = "paper"
    SMALL_CAPITAL = "small_capital"
    PRODUCTION = "production"
    REJECTED = "rejected"
    ROLLED_BACK = "rolled_back"


@dataclass
class GateResult:
    """Result of a deployment gate check."""
    stage: str = ""
    passed: bool = False
    current_trades: int = 0
    required_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "stage": self.stage,
            "passed": self.passed,
            "trades": f"{self.current_trades}/{self.required_trades}",
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 2),
            "expectancy_r": round(self.expectancy_r, 3),
            "reason": self.reason,
        }


@dataclass
class DeploymentStatus:
    """Current deployment status."""
    current_stage: str = DeploymentStage.LEARNING
    stage_history: List[Dict] = field(default_factory=list)
    gate_results: List[GateResult] = field(default_factory=list)
    promotion_ready: bool = False
    rollback_recommended: bool = False

    def to_dict(self) -> Dict:
        return {
            "current_stage": self.current_stage,
            "promotion_ready": self.promotion_ready,
            "rollback_recommended": self.rollback_recommended,
            "gate_results": [g.to_dict() for g in self.gate_results],
        }

    def render(self) -> str:
        """Render deployment status."""
        lines = []
        lines.append("┌─ DEPLOYMENT PIPELINE ─" + "─" * 33 + "┐")

        stages = [
            ("learning", "Learning"),
            ("shadow", "Shadow"),
            ("paper", "Paper Trading"),
            ("small_capital", "Small Capital"),
            ("production", "Production"),
        ]

        for stage_id, stage_name in stages:
            is_current = self.current_stage == stage_id
            is_passed = self._is_stage_passed(stage_id)
            icon = "●" if is_current else ("✓" if is_passed else "○")
            lines.append(f"│  {icon} {stage_name:<20s} {'← CURRENT' if is_current else '':<15s}     │")

        lines.append("└" + "─" * 54 + "┘")
        return "\n".join(lines)

    def _is_stage_passed(self, stage_id: str) -> bool:
        """Check if a stage has been passed."""
        stage_order = ["learning", "shadow", "paper", "small_capital", "production"]
        current_idx = stage_order.index(self.current_stage) if self.current_stage in stage_order else 0
        stage_idx = stage_order.index(stage_id) if stage_id in stage_order else 0
        return stage_idx < current_idx


class DeploymentGates:
    """
    Manages staged deployment pipeline.

    Never allow Learning → Production directly.
    Instead: Learning → Shadow → Paper → Small Capital → Production.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self):
        self._status = DeploymentStatus()
        self._history: List[Dict] = []

    def evaluate_gate(self) -> Optional[GateResult]:
        """Evaluate the current deployment gate."""
        stage = self._status.current_stage

        if stage == DeploymentStage.LEARNING:
            return self._evaluate_learning_gate()
        elif stage == DeploymentStage.SHADOW:
            return self._evaluate_shadow_gate()
        elif stage == DeploymentStage.PAPER:
            return self._evaluate_paper_gate()
        elif stage == DeploymentStage.SMALL_CAPITAL:
            return self._evaluate_small_capital_gate()
        elif stage == DeploymentStage.PRODUCTION:
            return self._evaluate_production_gate()

        return None

    def promote(self) -> bool:
        """Promote to next deployment stage."""
        stage_order = [
            DeploymentStage.LEARNING,
            DeploymentStage.SHADOW,
            DeploymentStage.PAPER,
            DeploymentStage.SMALL_CAPITAL,
            DeploymentStage.PRODUCTION,
        ]

        current_idx = stage_order.index(self._status.current_stage) if self._status.current_stage in stage_order else -1
        if current_idx >= len(stage_order) - 1:
            return False  # Already at production

        next_stage = stage_order[current_idx + 1]
        self._status.current_stage = next_stage

        self._history.append({
            "action": "promote",
            "from": stage_order[current_idx].value,
            "to": next_stage.value,
            "timestamp": time.time(),
        })

        logger.info("DEPLOYMENT: promoted to {}", next_stage.value)
        return True

    def rollback(self) -> bool:
        """Rollback to previous deployment stage."""
        stage_order = [
            DeploymentStage.LEARNING,
            DeploymentStage.SHADOW,
            DeploymentStage.PAPER,
            DeploymentStage.SMALL_CAPITAL,
            DeploymentStage.PRODUCTION,
        ]

        current_idx = stage_order.index(self._status.current_stage) if self._status.current_stage in stage_order else -1
        if current_idx <= 0:
            return False  # Already at learning

        prev_stage = stage_order[current_idx - 1]
        self._status.current_stage = prev_stage

        self._history.append({
            "action": "rollback",
            "from": stage_order[current_idx].value,
            "to": prev_stage.value,
            "timestamp": time.time(),
        })

        logger.warning("DEPLOYMENT: rolled back to {}", prev_stage.value)
        return True

    def get_status(self) -> DeploymentStatus:
        """Get current deployment status."""
        return self._status

    def get_phases_display(self) -> "DeploymentPhases":
        """Get deployment phases with conditions for UI display."""
        from .governance_config import DeploymentPhases, DeploymentPhase

        phases = DeploymentPhases(current_phase=self._status.current_stage)

        phase_defs = [
            ("learning", "Learning", [
                "Collecting training data",
                "Building statistical models",
                "No live capital at risk",
            ]),
            ("shadow", "Shadow", [
                "Running in parallel with champion",
                "≥30 shadow trades completed",
                "Shadow PF ≥ champion PF",
            ]),
            ("paper", "Paper Trading", [
                "Simulated capital execution",
                "≥30 paper trades completed",
                "Paper PF ≥ 1.3",
                "All health checks PASS",
            ]),
            ("small_capital", "Small Capital", [
                "Minimal real capital",
                "≥50 small-cap trades",
                "Positive expectancy confirmed",
                "Drawdown within limits",
            ]),
            ("production", "Production", [
                "Full capital deployment",
                "Production Confidence ≥ 90%",
                "All governance gates PASS",
                "Kill switch armed",
            ]),
        ]

        stage_order = ["learning", "shadow", "paper", "small_capital", "production"]
        current_idx = stage_order.index(self._status.current_stage) if self._status.current_stage in stage_order else 0

        for stage_id, display_name, conditions in phase_defs:
            phase_idx = stage_order.index(stage_id)
            is_current = phase_idx == current_idx
            is_passed = phase_idx < current_idx

            # Check conditions (simplified — in production would query actual state)
            conditions_met = []
            for i, cond in enumerate(conditions):
                if is_passed:
                    conditions_met.append(True)
                elif is_current:
                    # First N conditions met based on progress
                    conditions_met.append(i < len(conditions) // 2)
                else:
                    conditions_met.append(False)

            phases.phases.append(DeploymentPhase(
                name=stage_id,
                display_name=display_name,
                is_current=is_current,
                is_passed=is_passed,
                conditions=conditions,
                conditions_met=conditions_met,
            ))

        return phases

    def _evaluate_learning_gate(self) -> GateResult:
        """Evaluate learning → shadow gate."""
        result = GateResult(
            stage="learning",
            required_trades=100,
        )

        # Check if model has enough learning trades
        # This would query the training database in production
        result.current_trades = 0  # Placeholder
        result.passed = False
        result.reason = "learning phase — collecting training data"

        return result

    def _evaluate_shadow_gate(self) -> GateResult:
        """Evaluate shadow → paper gate."""
        result = GateResult(
            stage="shadow",
            required_trades=50,
        )

        # Check shadow performance
        result.passed = False
        result.reason = "shadow testing — validating on unseen data"

        return result

    def _evaluate_paper_gate(self) -> GateResult:
        """Evaluate paper → small capital gate."""
        result = GateResult(
            stage="paper",
            required_trades=30,
        )

        # Check paper trading performance
        result.passed = False
        result.reason = "paper trading — testing with simulated capital"

        return result

    def _evaluate_small_capital_gate(self) -> GateResult:
        """Evaluate small capital → production gate."""
        result = GateResult(
            stage="small_capital",
            required_trades=50,
        )

        # Check small capital performance
        result.passed = False
        result.reason = "small capital — testing with minimal real capital"

        return result

    def _evaluate_production_gate(self) -> GateResult:
        """Evaluate production health."""
        result = GateResult(
            stage="production",
        )

        result.passed = True
        result.reason = "production — monitoring ongoing performance"

        return result
