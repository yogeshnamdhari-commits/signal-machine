import pytest

from core.market_data import make_observation, require_real_market_data
from core.provenance import DataQuality


def test_real_observation_passes():
    obs = make_observation(
        source="binance",
        feed="aggTrade",
        symbol="BTCUSDT",
        event_ts=100.0,
        received_ts=101.0,
        payload={"price": 100},
    )
    require_real_market_data(obs, max_age_s=5, now_ts=101.0)


def test_synthetic_observation_is_rejected():
    obs = make_observation(
        source="test",
        feed="fixture",
        symbol="BTCUSDT",
        event_ts=100.0,
        received_ts=101.0,
        quality=DataQuality.SYNTHETIC,
        payload={"price": 100},
    )
    with pytest.raises(ValueError, match="Non-real"):
        require_real_market_data(obs, max_age_s=5, now_ts=101.0)


def test_stale_observation_is_rejected():
    obs = make_observation(
        source="binance",
        feed="aggTrade",
        symbol="BTCUSDT",
        event_ts=100.0,
        received_ts=101.0,
        payload={"price": 100},
    )
    with pytest.raises(ValueError, match="stale"):
        require_real_market_data(obs, max_age_s=5, now_ts=107.0)
