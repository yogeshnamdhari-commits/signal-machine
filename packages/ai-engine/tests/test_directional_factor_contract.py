import time

import pytest

from core.directional_factor import DirectionalFactor, DirectionState, DataQuality


def test_directional_factor_requires_explicit_direction_and_reason():
    factor = DirectionalFactor(
        name="delta",
        state=DirectionState.BUY,
        score=82.0,
        reason="positive taker delta over the observation window",
        source="binance",
        feed="aggTrade",
        observed_at=time.time(),
        quality=DataQuality.LIVE,
    )
    assert factor.state is DirectionState.BUY
    assert factor.quality is DataQuality.LIVE
    assert factor.reason


def test_directional_factor_rejects_empty_reason():
    with pytest.raises(ValueError, match="reason"):
        DirectionalFactor(
            name="cvd",
            state=DirectionState.NEUTRAL,
            score=50.0,
            reason="",
            source="binance",
            feed="aggTrade",
            observed_at=time.time(),
            quality=DataQuality.LIVE,
        )


def test_directional_factor_rejects_invalid_score():
    with pytest.raises(ValueError, match="score"):
        DirectionalFactor(
            name="oi",
            state=DirectionState.SELL,
            score=101.0,
            reason="short buildup",
            source="binance",
            feed="openInterest",
            observed_at=time.time(),
            quality=DataQuality.LIVE,
        )
