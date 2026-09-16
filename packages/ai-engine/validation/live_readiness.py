"""Aggregate live-readiness gate for infrastructure and research evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class LiveReadinessReport:
    ready: bool
    gates: Tuple[Tuple[str, bool, str], ...]

    @property
    def failures(self) -> Tuple[str, ...]:
        return tuple(f"{name}: {reason}" for name, ok, reason in self.gates if not ok)


def evaluate_live_readiness(*, engineering_ok: bool, provenance_ok: bool, synthetic_isolated: bool, config_ok: bool, single_authority_ok: bool, ci_ok: bool, historical_certification_rejected: bool, backtester_ok: bool, delta_environment_ok: bool, research_validated: bool) -> LiveReadinessReport:
    gates = (
        ("engineering", engineering_ok, "engineering checks failed"),
        ("provenance", provenance_ok, "market-data provenance gate failed"),
        ("synthetic_isolation", synthetic_isolated, "synthetic/estimated data can reach real-data paths"),
        ("configuration", config_ok, "configuration/environment contract failed"),
        ("single_authority", single_authority_ok, "multiple trading authorities remain"),
        ("ci", ci_ok, "CI gate failed"),
        ("historical_certification", historical_certification_rejected, "historical validation accepted as current"),
        ("backtester", backtester_ok, "backtester correctness gate failed"),
        ("delta", delta_environment_ok, "Delta environment contract failed"),
        ("research", research_validated, "current profitability evidence is insufficient"),
    )
    return LiveReadinessReport(ready=all(ok for _, ok, _ in gates), gates=gates)
