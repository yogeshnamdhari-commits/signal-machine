"""Consensus diagnostics for overlapping signal engines.

The consensus layer does not create new predictive evidence. It only measures
agreement/conflict between independently identified engine outputs while
preventing multiple signals from the same evidence group from being counted
as independent confirmation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Tuple

from core.signal_contract import EngineSignal


@dataclass(frozen=True)
class ConsensusResult:
    symbol: str
    side: str
    agreeing_engines: Tuple[str, ...]
    conflicting_engines: Tuple[str, ...]
    independent_groups: Tuple[str, ...]
    agreement_count: int
    conflict_count: int
    mean_score: float
    max_score: float
    min_score: float
    provenance_ids: Tuple[str, ...]
    config_fingerprints: Tuple[str, ...]

    @property
    def independent_agreement(self) -> int:
        return len(self.independent_groups)

    @property
    def unanimous(self) -> bool:
        return self.conflict_count == 0 and self.agreement_count > 0


def aggregate_signals(signals: Iterable[EngineSignal], *, symbol: str) -> Tuple[ConsensusResult, ...]:
    grouped: Dict[str, list[EngineSignal]] = {}
    for signal in signals:
        if signal.symbol != symbol or signal.side == "NEUTRAL":
            continue
        grouped.setdefault(signal.side, []).append(signal)

    results = []
    for side, side_signals in grouped.items():
        opposing = [s for other_side, vals in grouped.items() if other_side != side for s in vals]
        # Count each independent evidence group once. This prevents duplicate
        # engines that consume the same feed from inflating confirmation.
        independent = tuple(sorted({s.independent_group for s in side_signals if s.independent_group}))
        provenance = tuple(sorted({p for s in side_signals for p in s.provenance_ids}))
        configs = tuple(sorted({s.config_fingerprint for s in side_signals if s.config_fingerprint}))
        scores = [s.score for s in side_signals]
        results.append(
            ConsensusResult(
                symbol=symbol,
                side=side,
                agreeing_engines=tuple(sorted(s.engine_id for s in side_signals)),
                conflicting_engines=tuple(sorted(s.engine_id for s in opposing)),
                independent_groups=independent,
                agreement_count=len(side_signals),
                conflict_count=len(opposing),
                mean_score=sum(scores) / len(scores),
                max_score=max(scores),
                min_score=min(scores),
                provenance_ids=provenance,
                config_fingerprints=configs,
            )
        )
    return tuple(results)
