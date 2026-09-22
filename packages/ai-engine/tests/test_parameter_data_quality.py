from core.directional_factor import DataQuality, DirectionState
from core.parameter_semantics import direction_for_parameter


def test_zero_funding_is_neutral_not_forced_sell():
    factor = direction_for_parameter(
        "funding",
        {"funding": 0.0, "mark_price": 100.0, "timestamp": 1_700_000_000},
    )
    assert factor.state is DirectionState.NEUTRAL
    # Funding direction is derived from the observed mark-price/funding pair;
    # it is not a raw trade-tape observation.
    assert factor.quality is DataQuality.CALCULATED


def test_missing_trade_tape_does_not_become_neutral_vote():
    factor = direction_for_parameter(
        "delta",
        {"net_delta": 0.0, "flow_total_trades": 0, "timestamp": 1_700_000_000},
    )
    assert factor.state is DirectionState.NEUTRAL
    assert factor.quality is DataQuality.UNAVAILABLE


def test_missing_open_interest_does_not_become_zero_vote():
    factor = direction_for_parameter(
        "oi",
        {"open_interest": 0.0, "oi_bias": "buy", "timestamp": 1_700_000_000},
    )
    assert factor.quality is DataQuality.UNAVAILABLE


def test_missing_regime_snapshot_is_unavailable():
    factor = direction_for_parameter(
        "regime",
        {
            "regime": "range",
            "regime_confidence_pct": 50,
            "regime_alignment": 0,
            "regime_1m": "",
            "regime_5m": "",
            "regime_15m": "",
            "regime_1h": "",
            "regime_4h": "",
            "timestamp": 1_700_000_000,
        },
    )
    assert factor.quality is DataQuality.UNAVAILABLE
    assert factor.state is DirectionState.NEUTRAL
