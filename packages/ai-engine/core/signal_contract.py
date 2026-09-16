"""Typed contract for independent signal-engine evidence."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class EngineSignal:
    engine_id: str
    engine_version: str
    symbol: str
    side: str
    score: float
    event_ts: float
    provenance_ids: Tuple[str, ...] = ()
    evidence: Dict[str, float] = field(default_factory=dict)
    independent_group: str = "default"
    config_fingerprint: str = ""
    source: str = ""

    def __post_init__(self) -> None:
        if not self.engine_id or not self.engine_version:
            raise ValueError("Signal requires engine identity and version")
        if self.side not in {"BUY", "SELL", "LONG", "SHORT", "NEUTRAL"}:
            raise ValueError(f"Unsupported signal side: {self.side}")
        if not 0 <= self.score <= 100:
            raise ValueError("Signal score must be within [0, 100]")
        if self.event_ts <= 0:
            raise ValueError("Signal event_ts must be positive")
