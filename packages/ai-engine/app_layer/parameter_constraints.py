"""
Parameter Constraints — Safe bounds for all learning parameters.

Per Priority E: Learning must stay within safe bounds.
    Institution Agreement: Allowed 65–90%, Never 20%, Never 99%
    Position sizing, risk limits, queue depth, exit multipliers

READ-ONLY: never modifies upstream data.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from loguru import logger


@dataclass
class ParameterBound:
    """Safe bounds for a parameter."""
    name: str
    min_value: float
    max_value: float
    default_value: float
    step_size: float = 0.01  # Maximum change per update
    description: str = ""

    def clamp(self, value: float) -> float:
        """Clamp value to safe bounds."""
        return max(self.min_value, min(value, self.max_value))

    def is_within_bounds(self, value: float) -> bool:
        """Check if value is within bounds."""
        return self.min_value <= value <= self.max_value

    def validate_change(self, current: float, proposed: float) -> Tuple[bool, str]:
        """Validate if a proposed change is allowed."""
        if not self.is_within_bounds(proposed):
            return False, f"proposed value {proposed} outside bounds [{self.min_value}, {self.max_value}]"

        change = abs(proposed - current)
        if change > self.step_size:
            return False, f"change {change:.4f} exceeds maximum step {self.step_size}"

        return True, "valid"

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "min": self.min_value,
            "max": self.max_value,
            "default": self.default_value,
            "step": self.step_size,
        }


# ═══════════════════════════════════════════════════════════════
# PARAMETER BOUNDS — Safe ranges for all parameters
# ═══════════════════════════════════════════════════════════════

PARAMETER_BOUNDS: Dict[str, ParameterBound] = {
    # Institution Agreement
    "institution_agreement_min": ParameterBound(
        name="institution_agreement_min",
        min_value=0.50, max_value=0.90, default_value=0.60,
        step_size=0.05,
        description="Minimum institution agreement ratio",
    ),
    "institution_agreement_elite": ParameterBound(
        name="institution_agreement_elite",
        min_value=0.60, max_value=0.95, default_value=0.70,
        step_size=0.05,
        description="Elite institution agreement threshold",
    ),

    # Reward Filter
    "rr_min": ParameterBound(
        name="rr_min",
        min_value=1.5, max_value=4.0, default_value=2.0,
        step_size=0.25,
        description="Minimum R:R ratio",
    ),
    "rr_preferred": ParameterBound(
        name="rr_preferred",
        min_value=2.0, max_value=5.0, default_value=2.5,
        step_size=0.25,
        description="Preferred R:R ratio",
    ),

    # Trade Quality
    "tq_min_execute": ParameterBound(
        name="tq_min_execute",
        min_value=50, max_value=95, default_value=75,
        step_size=5,
        description="Minimum TQ score for execution",
    ),
    "tq_min_elite": ParameterBound(
        name="tq_min_elite",
        min_value=60, max_value=100, default_value=85,
        step_size=5,
        description="Minimum TQ score for elite classification",
    ),

    # Expectancy
    "ev_min_r": ParameterBound(
        name="ev_min_r",
        min_value=0.0, max_value=2.0, default_value=0.5,
        step_size=0.1,
        description="Minimum expected value in R-multiples",
    ),

    # Position Sizing
    "position_size_multiplier": ParameterBound(
        name="position_size_multiplier",
        min_value=0.3, max_value=3.0, default_value=1.0,
        step_size=0.2,
        description="Position size multiplier",
    ),
    "max_portfolio_heat_pct": ParameterBound(
        name="max_portfolio_heat_pct",
        min_value=2.0, max_value=10.0, default_value=5.0,
        step_size=0.5,
        description="Maximum portfolio heat as % of balance",
    ),
    "max_position_size_pct": ParameterBound(
        name="max_position_size_pct",
        min_value=1.0, max_value=5.0, default_value=3.0,
        step_size=0.5,
        description="Maximum single position as % of balance",
    ),

    # Queue
    "max_executions_per_cycle": ParameterBound(
        name="max_executions_per_cycle",
        min_value=1, max_value=5, default_value=3,
        step_size=1,
        description="Maximum simultaneous executions per cycle",
    ),

    # Exit Multipliers
    "breakeven_trigger_r": ParameterBound(
        name="breakeven_trigger_r",
        min_value=0.5, max_value=2.0, default_value=1.0,
        step_size=0.1,
        description="Breakeven trigger in R-multiples",
    ),
    "trailing_distance_r": ParameterBound(
        name="trailing_distance_r",
        min_value=0.3, max_value=2.0, default_value=1.0,
        step_size=0.1,
        description="Trailing stop distance in R-multiples",
    ),
    "partial_profit_r": ParameterBound(
        name="partial_profit_r",
        min_value=1.0, max_value=3.0, default_value=1.5,
        step_size=0.25,
        description="Partial profit trigger in R-multiples",
    ),

    # Risk
    "daily_loss_limit_pct": ParameterBound(
        name="daily_loss_limit_pct",
        min_value=1.0, max_value=5.0, default_value=3.0,
        step_size=0.5,
        description="Daily loss limit as % of balance",
    ),
    "consecutive_loss_pause": ParameterBound(
        name="consecutive_loss_pause",
        min_value=3, max_value=10, default_value=5,
        step_size=1,
        description="Consecutive losses before pause",
    ),
}


class ParameterConstraints:
    """
    Enforces safe bounds on all learning parameters.

    Per Priority E: Learning should optimize within engineering constraints.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self):
        self._bounds = dict(PARAMETER_BOUNDS)

    def validate(
        self,
        parameter_name: str,
        proposed_value: float,
        current_value: Optional[float] = None,
    ) -> Tuple[bool, str]:
        """
        Validate a proposed parameter change.

        Returns:
            Tuple of (is_valid, reason)
        """
        bound = self._bounds.get(parameter_name)
        if not bound:
            return False, f"unknown parameter: {parameter_name}"

        # Check bounds
        if not bound.is_within_bounds(proposed_value):
            return False, (
                f"{parameter_name}={proposed_value} outside bounds "
                f"[{bound.min_value}, {bound.max_value}]"
            )

        # Check step size
        if current_value is not None:
            is_valid, reason = bound.validate_change(current_value, proposed_value)
            if not is_valid:
                return False, reason

        return True, "valid"

    def clamp(self, parameter_name: str, value: float) -> float:
        """Clamp a value to safe bounds."""
        bound = self._bounds.get(parameter_name)
        if not bound:
            return value
        return bound.clamp(value)

    def get_bounds(self, parameter_name: str) -> Optional[ParameterBound]:
        """Get bounds for a parameter."""
        return self._bounds.get(parameter_name)

    def get_all_bounds(self) -> Dict[str, ParameterBound]:
        """Get all parameter bounds."""
        return dict(self._bounds)

    def get_defaults(self) -> Dict[str, float]:
        """Get all default values."""
        return {name: bound.default_value for name, bound in self._bounds.items()}

    def validate_all(
        self,
        parameters: Dict[str, float],
        current_parameters: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Tuple[bool, str]]:
        """
        Validate all parameters at once.

        Returns:
            Dict of parameter_name → (is_valid, reason)
        """
        results = {}
        current = current_parameters or {}

        for name, value in parameters.items():
            current_val = current.get(name)
            results[name] = self.validate(name, value, current_val)

        return results
