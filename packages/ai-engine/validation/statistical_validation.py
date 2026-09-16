"""Fail-closed research evidence gates; no profitability is inferred."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass(frozen=True)
class ResearchEvidence:
    completed_trades: int
    train_period: Tuple[str, str]
    validation_period: Tuple[str, str]
    test_period: Tuple[str, str]
    regime_counts: Dict[str, int] = field(default_factory=dict)
    cost_model: str = ""
    funding_source: str = ""
    rejected_signals: int = 0
    rejected_outcomes_complete: bool = False
    bootstrap_seed: int | None = None
    code_commit: str = ""
    config_fingerprint: str = ""


@dataclass(frozen=True)
class ResearchDecision:
    approved: bool
    reasons: Tuple[str, ...]


def evaluate_research_evidence(e: ResearchEvidence) -> ResearchDecision:
    reasons = []
    if e.completed_trades < 100:
        reasons.append("insufficient_completed_trades")
    if not e.cost_model:
        reasons.append("missing_cost_model")
    if not e.funding_source:
        reasons.append("missing_funding_source")
    if not e.code_commit or not e.config_fingerprint:
        reasons.append("missing_reproducibility_fingerprint")
    if not e.rejected_outcomes_complete:
        reasons.append("missing_rejected_signal_outcomes")
    if not e.regime_counts or sum(e.regime_counts.values()) <= 0:
        reasons.append("missing_regime_coverage")
    periods = [e.train_period, e.validation_period, e.test_period]
    for earlier, later in zip(periods, periods[1:]):
        if earlier[1] > later[0]:
            reasons.append("overlapping_temporal_windows")
            break
    return ResearchDecision(approved=not reasons, reasons=tuple(reasons))
