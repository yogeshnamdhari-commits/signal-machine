from dashboard.live_sheet_contract import (
    build_signal_display,
    display_value,
    freshness_state,
    live_observation_values,
)


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


def test_raw_live_observations_are_exposed_separately_from_directional_biases():
    row = {
        "flow_total_trades": 25,
        "cvd_5m": -1250.0,
        "flow_strength": 0.42,
        "exchange_flow": 250000.0,
        "imbalance": -0.11,
    }
    observed = live_observation_values(row)
    assert observed == {
        "cvd_5m": -1250.0,
        "flow_strength": 0.42,
        "exchange_flow": 250000.0,
        "imbalance": -0.11,
    }


def test_raw_live_observations_are_unavailable_without_trade_tape():
    row = {
        "flow_total_trades": 0,
        "cvd_5m": -1250.0,
        "flow_strength": 0.42,
        "exchange_flow": 250000.0,
        "imbalance": -0.11,
    }
    observed = live_observation_values(row)
    assert observed["cvd_5m"] is None
    assert observed["flow_strength"] is None
    assert observed["exchange_flow"] is None
    assert observed["imbalance"] == -0.11

def test_l2_imbalance_is_unavailable_without_depth_observation():
    row = {"imbalance": 0.9, "observed_at_by_metric": {}}
    display = build_signal_display({}, row)
    assert display["imbalance"].quality.value == "UNAVAILABLE"

def test_l2_imbalance_uses_depth_timestamp_not_trade_timestamp():
    row = {
        "imbalance": 0.9,
        "trade": "present",
        "observed_at_by_metric": {"imbalance": 100.0},
    }
    display = build_signal_display({}, row)
    assert display["imbalance"].observed_at == 100.0


def test_stale_raw_metric_is_not_displayed_as_current():
    row = {
        "open_interest": 123.0,
        "observed_at_by_metric": {"oi": 1.0},
    }
    assert display_value(row, "open_interest") is None


def test_volume_has_no_directional_vote():
    row = {
        "volume_24h": 1000.0,
        "vol_bias": None,
        "observed_at_by_metric": {"volume": 1.0},
    }
    assert build_signal_display({}, row)["volume"].quality.value == "NOT_APPLICABLE"
