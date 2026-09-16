from core.parameter_semantics import direction_for_parameter, signal_from_canonical


def test_bullish_price_is_buy():
    result = direction_for_parameter("price", {"change_24h": 1.2, "timestamp": 100})
    assert result.state.value == "BUY"
    assert result.quality.value == "LIVE"


def test_bearish_price_is_sell():
    result = direction_for_parameter("price", {"change_24h": -1.2, "timestamp": 100})
    assert result.state.value == "SELL"
    assert result.quality.value == "LIVE"


def test_direction_without_timestamp_is_unavailable():
    result = direction_for_parameter("price", {"change_24h": 1.2})
    assert result.state.value == "NEUTRAL"
    assert result.quality.value == "UNAVAILABLE"


def test_neutral_ratio_is_neutral():
    result = direction_for_parameter("b_s_ratio", {"buy_sell_ratio": 1.0, "timestamp": 100})
    assert result.state.value == "NEUTRAL"


def test_missing_delta_is_unavailable_not_zero_sell():
    result = direction_for_parameter("delta", {})
    assert result.quality.value == "UNAVAILABLE"
    assert result.state.value == "NEUTRAL"


def _canonical(side: str):
    return {
        "side": side,
        "source": "python",
        "authority": "python",
        "canonical": True,
    }


def test_canonical_signal_requires_full_authority_contract():
    assert signal_from_canonical(_canonical("LONG")) == "BUY"
    assert signal_from_canonical(_canonical("SHORT")) == "SELL"
    assert signal_from_canonical(_canonical("BUY")) == "BUY"
    assert signal_from_canonical(_canonical("SELL")) == "SELL"
    assert signal_from_canonical({"side": "LONG", "source": "python"}) == "NO_SIGNAL"
    assert signal_from_canonical({"side": "LONG", "source": "dashboard", "authority": "dashboard", "canonical": True}) == "NO_SIGNAL"
    assert signal_from_canonical({"side": "LONG"}) == "NO_SIGNAL"
    assert signal_from_canonical({}) == "NO_SIGNAL"
