from dashboard.live_sheet_contract import build_signal_display, freshness_state


def test_fresh_snapshot_is_live():
    assert freshness_state(100.0, 130.0, max_age=60.0) == "LIVE"


def test_old_snapshot_is_stale():
    assert freshness_state(100.0, 200.0, max_age=60.0) == "STALE"


def test_missing_snapshot_is_unavailable():
    assert freshness_state(0.0, 200.0, max_age=60.0) == "UNAVAILABLE"


def test_live_sheet_never_generates_implied_signal():
    row = {"oi_bias": "buy", "cvd_bias": "bullish", "flow_signal": "buy"}
    assert build_signal_display({}, row)["signal"] == "NO_SIGNAL"


def test_live_sheet_accepts_only_canonical_bridge_signal():
    row = {"symbol": "BTCUSDT"}
    signal = {"symbol": "BTCUSDT", "side": "LONG"}
    assert build_signal_display(signal, row)["signal"] == "BUY"
    assert build_signal_display(signal, row)["authority"] == "python-bridge"
