"""
Statistical Safeguards — Minimum sample requirements before adaptation.

Per Priority A: The Learning Engine needs minimum sample requirements.
    BTC PF = 2.1, Trades = 4 → should NOT change parameters.
    Minimum trades 100 before adaptation.
    Otherwise the system will overfit.

Safeguards:
    1. Minimum trades per symbol before profile adaptation
    2. Minimum trades per regime before regime learning
    3. Minimum trades per session before session intelligence
    4. Confidence intervals on all calculated metrics
    5. Overfitting detection (too many parameters vs data points)
    6. Statistical significance testing before threshold changes

READ-ONLY: never modifies upstream data.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# MINIMUM SAMPLE REQUIREMENTS
# ═══════════════════════════════════════════════════════════════

# Minimum trades for symbol profile adaptation
MIN_TRADES_SYMBOL = 20

# Minimum trades for regime learning adaptation
MIN_TRADES_REGIME = 15

# Minimum trades for session intelligence adaptation
MIN_TRADES_SESSION = 10

# Minimum trades for exit strategy adaptation
MIN_TRADES_EXIT = 10

# Minimum trades for global threshold adaptation
MIN_TRADES_GLOBAL = 50

# Minimum trades for walk-forward validation
MIN_TRADES_WALK_FORWARD = 100

# Maximum parameters per data point (overfitting guard)
MAX_PARAMS_PER_DATAPOINT = 0.05  # 1 parameter per 20 trades

# Confidence level for statistical significance (95%)
CONFIDENCE_LEVEL = 0.95
Z_SCORE_95 = 1.96  # z-score for 95% confidence


@dataclass
class StatisticalGate:
    """Result of a statistical gate check."""
    gate_name: str = ""
    passed: bool = False
    current_count: int = 0
    required_count: int = 0
    confidence: float = 0.0
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "gate": self.gate_name,
            "passed": self.passed,
            "current": self.current_count,
            "required": self.required_count,
            "confidence": round(self.confidence, 2),
            "reason": self.reason,
        }


@dataclass
class ConfidenceInterval:
    """Confidence interval for a metric."""
    metric_name: str = ""
    point_estimate: float = 0.0
    lower_bound: float = 0.0
    upper_bound: float = 0.0
    sample_size: int = 0
    standard_error: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "metric": self.metric_name,
            "estimate": round(self.point_estimate, 4),
            "ci_lower": round(self.lower_bound, 4),
            "ci_upper": round(self.upper_bound, 4),
            "sample_size": self.sample_size,
            "se": round(self.standard_error, 4),
        }


class StatisticalSafeguards:
    """
    Minimum sample requirements and confidence gates.

    Per Priority A: Prevent overfitting with minimum samples.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self):
        self._gates: Dict[str, StatisticalGate] = {}

    def check_symbol_gate(self, symbol: str, trade_count: int) -> StatisticalGate:
        """Check if symbol has enough trades for adaptation."""
        gate = StatisticalGate(
            gate_name=f"symbol_{symbol}",
            current_count=trade_count,
            required_count=MIN_TRADES_SYMBOL,
            passed=trade_count >= MIN_TRADES_SYMBOL,
            confidence=min(trade_count / MIN_TRADES_SYMBOL, 1.0),
        )
        if not gate.passed:
            gate.reason = f"{trade_count} trades < {MIN_TRADES_SYMBOL} minimum for {symbol}"
        self._gates[gate.gate_name] = gate
        return gate

    def check_regime_gate(self, regime: str, trade_count: int) -> StatisticalGate:
        """Check if regime has enough trades for adaptation."""
        gate = StatisticalGate(
            gate_name=f"regime_{regime}",
            current_count=trade_count,
            required_count=MIN_TRADES_REGIME,
            passed=trade_count >= MIN_TRADES_REGIME,
            confidence=min(trade_count / MIN_TRADES_REGIME, 1.0),
        )
        if not gate.passed:
            gate.reason = f"{trade_count} trades < {MIN_TRADES_REGIME} minimum for {regime}"
        self._gates[gate.gate_name] = gate
        return gate

    def check_session_gate(self, session: str, trade_count: int) -> StatisticalGate:
        """Check if session has enough trades for adaptation."""
        gate = StatisticalGate(
            gate_name=f"session_{session}",
            current_count=trade_count,
            required_count=MIN_TRADES_SESSION,
            passed=trade_count >= MIN_TRADES_SESSION,
            confidence=min(trade_count / MIN_TRADES_SESSION, 1.0),
        )
        if not gate.passed:
            gate.reason = f"{trade_count} trades < {MIN_TRADES_SESSION} minimum for {session}"
        self._gates[gate.gate_name] = gate
        return gate

    def check_exit_gate(self, exit_reason: str, trade_count: int) -> StatisticalGate:
        """Check if exit strategy has enough trades for adaptation."""
        gate = StatisticalGate(
            gate_name=f"exit_{exit_reason}",
            current_count=trade_count,
            required_count=MIN_TRADES_EXIT,
            passed=trade_count >= MIN_TRADES_EXIT,
            confidence=min(trade_count / MIN_TRADES_EXIT, 1.0),
        )
        if not gate.passed:
            gate.reason = f"{trade_count} trades < {MIN_TRADES_EXIT} minimum for {exit_reason}"
        self._gates[gate.gate_name] = gate
        return gate

    def check_global_gate(self, total_trades: int) -> StatisticalGate:
        """Check if global threshold adaptation is allowed."""
        gate = StatisticalGate(
            gate_name="global",
            current_count=total_trades,
            required_count=MIN_TRADES_GLOBAL,
            passed=total_trades >= MIN_TRADES_GLOBAL,
            confidence=min(total_trades / MIN_TRADES_GLOBAL, 1.0),
        )
        if not gate.passed:
            gate.reason = f"{total_trades} trades < {MIN_TRADES_GLOBAL} minimum for global adaptation"
        self._gates[gate.gate_name] = gate
        return gate

    def check_overfitting(
        self,
        num_parameters: int,
        num_trades: int,
    ) -> StatisticalGate:
        """Check for overfitting (too many parameters vs data points)."""
        if num_trades == 0:
            return StatisticalGate(
                gate_name="overfitting",
                passed=False,
                reason="no trades for overfitting check",
            )

        params_per_datapoint = num_parameters / num_trades
        max_allowed = MAX_PARAMS_PER_DATAPOINT

        gate = StatisticalGate(
            gate_name="overfitting",
            current_count=num_parameters,
            required_count=int(num_trades * max_allowed),
            passed=params_per_datapoint <= max_allowed,
            confidence=max(0, 1 - params_per_datapoint / max_allowed),
        )

        if not gate.passed:
            gate.reason = (
                f"overfitting risk: {num_parameters} params / {num_trades} trades = "
                f"{params_per_datapoint:.4f} > {max_allowed} limit"
            )

        self._gates[gate.gate_name] = gate
        return gate

    def is_significant_difference(
        self,
        metric_a: float,
        metric_b: float,
        sample_a: int,
        sample_b: int,
        std_a: float = 1.0,
        std_b: float = 1.0,
    ) -> Tuple[bool, float]:
        """
        Test if difference between two metrics is statistically significant.

        Uses z-test for difference of means.

        Returns:
            Tuple of (is_significant, p_value)
        """
        if sample_a < 5 or sample_b < 5:
            return False, 1.0

        se_a = std_a / math.sqrt(sample_a)
        se_b = std_b / math.sqrt(sample_b)
        se_diff = math.sqrt(se_a**2 + se_b**2)

        if se_diff == 0:
            return False, 1.0

        z_score = abs(metric_a - metric_b) / se_diff

        # Approximate p-value from z-score
        p_value = 2 * (1 - self._normal_cdf(z_score))

        return p_value < (1 - CONFIDENCE_LEVEL), p_value

    @staticmethod
    def _normal_cdf(x: float) -> float:
        """Approximate cumulative distribution function of normal distribution."""
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    def calculate_confidence_interval(
        self,
        metric_name: str,
        point_estimate: float,
        sample_size: int,
        std_dev: float = 1.0,
    ) -> ConfidenceInterval:
        """Calculate confidence interval for a metric."""
        if sample_size < 2:
            return ConfidenceInterval(
                metric_name=metric_name,
                point_estimate=point_estimate,
                lower_bound=point_estimate,
                upper_bound=point_estimate,
                sample_size=sample_size,
                standard_error=0,
            )

        se = std_dev / math.sqrt(sample_size)
        margin = Z_SCORE_95 * se

        return ConfidenceInterval(
            metric_name=metric_name,
            point_estimate=point_estimate,
            lower_bound=point_estimate - margin,
            upper_bound=point_estimate + margin,
            sample_size=sample_size,
            standard_error=se,
        )

    def get_all_gates(self) -> Dict[str, StatisticalGate]:
        """Get all gate results."""
        return dict(self._gates)

    def get_minimum_trades_for_confidence(self, confidence: float = 0.95) -> int:
        """Get minimum trades needed for a given confidence level."""
        # Approximate: for 95% CI with ±10% margin, need ~100 trades
        return int(100 * (confidence / 0.95))
