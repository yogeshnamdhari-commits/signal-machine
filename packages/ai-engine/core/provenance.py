"""Canonical provenance and quality contracts for all market observations."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional


class DataQuality(str, Enum):
    REAL = "REAL"
    ESTIMATED = "ESTIMATED"
    SYNTHETIC = "SYNTHETIC"
    INVALID = "INVALID"


@dataclass(frozen=True)
class MarketObservation:
    source: str
    feed: str
    symbol: str
    event_ts: float
    received_ts: float
    sequence: Optional[int]
    quality: DataQuality
    payload: Dict[str, Any]
    provenance_id: str

    @property
    def age_seconds(self) -> float:
        return max(0.0, self.received_ts - self.event_ts)


def validate_observation(
    observation: MarketObservation,
    now_ts: float,
    max_age_s: float,
    *,
    allow_non_real: bool = False,
) -> None:
    if not observation.source or not observation.feed or not observation.symbol:
        raise ValueError("Market observation is missing provenance identity")
    if observation.event_ts <= 0 or observation.received_ts <= 0:
        raise ValueError("Market observation timestamps must be positive")
    if observation.event_ts > observation.received_ts + 5:
        raise ValueError("Market observation event time is implausibly ahead of receipt time")
    if now_ts - observation.received_ts > max_age_s:
        raise ValueError("Market observation is stale")
    if observation.quality != DataQuality.REAL and not allow_non_real:
        raise ValueError(
            f"Non-real market observation rejected by real-data gate: {observation.quality.value}"
        )
    if not observation.provenance_id:
        raise ValueError("Market observation requires provenance_id")
