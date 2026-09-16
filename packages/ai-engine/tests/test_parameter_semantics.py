from core.parameter_semantics import direction_for_parameter, signal_from_canonical


def test_bullish_price_is_buy():
    result = direction_for_parameter("price", {"change_24h": 1.2})
    assert result.state.value == "BUY"


def test_bearish_price_is_sell():
    result = direction_for_parameter("price", {"change_24h": -1.2})
    assert result.state.value == "SELL"


def test_neutral_ratio_is_neutral():
    result = direction_for_parameter("b_s_ratio", {"buy_sell_ratio": 1.0})
    assert result.state.value == "NEUTRAL"


def test_missing_delta_is_unavailable_not_zero_sell():
    result = direction_for_parameter("delta", {})
    assert result.quality.value == "UNAVAILABLE"
    assert result.state.value == "NEUTRAL"


def test_canonical_signal_only_accepts_engine_side():
    assert signal_from_canonical({"side": "LONG", "source": "python"}) == "BUY"
    assert signal_from_canonical({"side": "SHORT", "source": "python"}) == "SELL"
    assert signal_from_canonical({"side": "BUY", "source": "python"}) == "BUY"
    assert signal_from_canonical({"side": "SELL", "source": "python"}) == "SELL"
    assert signal_from_canonical({"side": "LONG", "source": "dashboard"}) == "NO_SIGNAL"
    assert signal_from_canonical({"side": "LONG"}, bridge_trusted=True) == "BUY"
    assert signal_from_canonical({"side": "LONG"}) == "NO_SIGNAL"
    assert signal_from_canonical({}) == "NO_SIGNAL"
