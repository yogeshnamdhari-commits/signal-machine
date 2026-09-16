from dashboard.live_sheet_contract import build_signal_display, display_value, freshness_state


def test_fresh_snapshot_is_live():
    assert freshness_state(100.0, 130.0, max_age=60.0) == "LIVE"


def test_old_snapshot_is_stale():
    assert freshness_state(100.0, 200.0, max_age=60.0) == "STALE"


def test_missing_snapshot_is_unavailable():
    assert freshness_state(0.0, 200.0, max_age=60.0) == "UNAVAILABLE"


def test_live_sheet_never_generates_implied_signal():
    row = {"oi_bias": "buy", "cvd_bias": "bullish", "flow_signal": "buy"}
    assert build_signal_display({}, row)["signal"] == "NO_SIGNAL"


def test_live_sheet_rejects_noncanonical_bridge_signal():
    row = {"symbol": "BTCUSDT"}
    signal = {"symbol": "BTCUSDT", "side": "LONG"}
    display = build_signal_display(signal, row)
    assert display["signal"] == "NO_SIGNAL"
    assert display["authority"] == "none"


def test_live_sheet_accepts_canonical_bridge_signal():
    row = {"symbol": "BTCUSDT"}
    signal = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "source": "python_engine",
        "authority": "python",
        "canonical": True,
    }
    display = build_signal_display(signal, row)
    assert display["signal"] == "BUY"
    assert display["authority"] == "python-bridge"


def test_missing_flow_is_not_rendered_as_zero():
    row = {"flow_total_trades": 0, "net_delta": 0.0, "buy_sell_ratio": 0.5}
    assert display_value(row, "net_delta") is None
    assert display_value(row, "buy_sell_ratio") is None


def test_missing_oi_is_not_rendered_as_zero():
    row = {"open_interest": 0.0, "oi_change_pct": 0.0}
    assert display_value(row, "open_interest") is None
    assert display_value(row, "oi_change_pct") is None


def test_no_liquidation_clusters_do_not_render_low_risk_as_fact():
    row = {"cluster_count": 0, "long_liq_count": 0, "short_liq_count": 0, "liq_risk_level": "low"}
    assert display_value(row, "liq_risk_level") is None
