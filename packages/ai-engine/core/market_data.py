"""Helpers that keep real and estimated market data on separate paths."""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, Optional

from core.provenance import DataQuality, MarketObservation, validate_observation


def provenance_id(*, source: str, feed: str, symbol: str, event_ts: float, sequence: Optional[int]) -> str:
    raw = f"{source}|{feed}|{symbol}|{event_ts:.6f}|{sequence}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def make_observation(
    *,
    source: str,
    feed: str,
    symbol: str,
    event_ts: float,
    payload: Dict[str, Any],
    quality: DataQuality = DataQuality.REAL,
    sequence: Optional[int] = None,
    received_ts: Optional[float] = None,
) -> MarketObservation:
    received = time.time() if received_ts is None else float(received_ts)
    return MarketObservation(
        source=source,
        feed=feed,
        symbol=symbol,
        event_ts=float(event_ts),
        received_ts=received,
        sequence=sequence,
        quality=quality,
        payload=json.loads(json.dumps(payload, default=str)),
        provenance_id=provenance_id(
            source=source,
            feed=feed,
            symbol=symbol,
            event_ts=float(event_ts),
            sequence=sequence,
        ),
    )


def require_real_market_data(observation: MarketObservation, *, max_age_s: float, now_ts: Optional[float] = None) -> MarketObservation:
    validate_observation(observation, time.time() if now_ts is None else now_ts, max_age_s)
    return observation
