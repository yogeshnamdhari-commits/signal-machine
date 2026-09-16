from core.parameter_semantics import signal_from_canonical
from dashboard.live_sheet_contract import build_signal_display


def test_noncanonical_bridge_payload_never_becomes_buy_or_sell():
    payload = {"symbol": "BTCUSDT", "side": "BUY", "source": "node", "authority": "node"}
    assert signal_from_canonical(payload) == "NO_SIGNAL"


def test_dashboard_requires_explicit_canonical_authority():
    payload = {"symbol": "BTCUSDT", "side": "BUY"}
    display = build_signal_display(payload, {"symbol": "BTCUSDT", "timestamp": 1_700_000_000})
    assert display["signal"] == "NO_SIGNAL"
    assert display["authority"] == "none"


def test_canonical_python_signal_is_displayed_as_buy():
    payload = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "source": "python_engine",
        "authority": "python",
        "canonical": True,
    }
    display = build_signal_display(payload, {"symbol": "BTCUSDT", "timestamp": 1_700_000_000})
    assert display["signal"] == "BUY"
    assert display["authority"] == "python-bridge"
