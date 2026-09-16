from core.signal_provenance import canonicalize_signal


def test_canonicalize_engine_signal_stamps_python_authority():
    result = canonicalize_signal({"symbol": "BTCUSDT", "side": "LONG", "score": 91})
    assert result["source"] == "python_engine"
    assert result["authority"] == "python"
    assert result["canonical"] is True
    assert result["side"] == "LONG"


def test_canonicalize_does_not_accept_dashboard_signal_as_authority():
    result = canonicalize_signal({"symbol": "BTCUSDT", "side": "LONG", "source": "dashboard"})
    assert result["authority"] == "python"
    assert result["canonical"] is True
    assert result["source"] == "python_engine"
