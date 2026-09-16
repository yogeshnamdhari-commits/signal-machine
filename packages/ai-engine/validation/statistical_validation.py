"""Fail-closed research evidence gates for current strategy certification."""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from typing import Dict, Tuple


MIN_COMPLETED_TRADES = 100
MIN_PROFIT_FACTOR = 1.20
MIN_EXPECTANCY = 0.0
MAX_DRAWDOWN_PCT = 12.0


@dataclass(frozen=True)
class ResearchEvidence:
    completed_trades: int
    train_period: Tuple[str, str]
    validation_period: Tuple[str, str]
    test_period: Tuple[str, str]
    regime_counts: Dict[str, int] = field(default_factory=dict)
    cost_model: str = ""
    slippage_model: str = ""
    funding_source: str = ""
    rejected_signals: int = 0
    rejected_outcomes_complete: bool = False
    bootstrap_seed: int | None = None
    code_commit: str = ""
    config_fingerprint: str = ""
    profit_factor: float | None = None
    expectancy: float | None = None
    max_drawdown_pct: float | None = None


@dataclass(frozen=True)
class ResearchDecision:
    approved: bool
    reasons: Tuple[str, ...]


def evaluate_research_evidence(e: ResearchEvidence) -> ResearchDecision:
    reasons = []
    if e.completed_trades < MIN_COMPLETED_TRADES:
        reasons.append("insufficient_completed_trades")
    if not e.cost_model:
        reasons.append("missing_cost_model")
    if not e.slippage_model:
        reasons.append("missing_slippage_model")
    if not e.funding_source:
        reasons.append("missing_funding_source")
    if not e.code_commit or not e.config_fingerprint:
        reasons.append("missing_reproducibility_fingerprint")
    if e.bootstrap_seed is None:
        reasons.append("missing_bootstrap_seed")
    if e.rejected_signals < 0:
        reasons.append("invalid_rejected_signal_count")
    if not e.rejected_outcomes_complete:
        reasons.append("missing_rejected_signal_outcomes")
    if not e.regime_counts or sum(e.regime_counts.values()) <= 0:
        reasons.append("missing_regime_coverage")

    if e.profit_factor is None or not isfinite(e.profit_factor):
        reasons.append("missing_profit_factor")
    elif e.profit_factor < MIN_PROFIT_FACTOR:
        reasons.append("profit_factor_below_minimum")

    if e.expectancy is None or not isfinite(e.expectancy):
        reasons.append("missing_expectancy")
    elif e.expectancy <= MIN_EXPECTANCY:
        reasons.append("expectancy_not_positive")

    if e.max_drawdown_pct is None or not isfinite(e.max_drawdown_pct):
        reasons.append("missing_max_drawdown")
    elif e.max_drawdown_pct >= MAX_DRAWDOWN_PCT:
        reasons.append("max_drawdown_above_limit")

    periods = [e.train_period, e.validation_period, e.test_period]
    for earlier, later in zip(periods, periods[1:]):
        if earlier[1] > later[0]:
            reasons.append("overlapping_temporal_windows")
            break
    return ResearchDecision(approved=not reasons, reasons=tuple(reasons))
