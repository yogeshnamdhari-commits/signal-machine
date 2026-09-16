"""Canonical per-parameter directional evidence contract."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DirectionState(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NEUTRAL = "NEUTRAL"


class DataQuality(str, Enum):
    LIVE = "LIVE"
    CALCULATED = "CALCULATED"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class DirectionalFactor:
    name: str
    state: DirectionState
    score: float
    reason: str
    source: str
    feed: str
    observed_at: float
    quality: DataQuality

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("name is required")
        if not self.reason.strip():
            raise ValueError("reason is required")
        if not 0 <= self.score <= 100:
            raise ValueError("score must be within [0, 100]")
        if self.observed_at <= 0:
            raise ValueError("observed_at must be positive")
        if self.quality is DataQuality.LIVE and (not self.source or not self.feed):
            raise ValueError("LIVE evidence requires source and feed")

    @property
    def usable_for_signal(self) -> bool:
        return self.quality in {DataQuality.LIVE, DataQuality.CALCULATED}
