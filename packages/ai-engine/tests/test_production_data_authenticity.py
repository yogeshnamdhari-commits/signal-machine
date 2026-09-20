from pathlib import Path


ENGINE = Path(__file__).resolve().parents[1] / "core" / "engine.py"


def test_engine_contains_no_ohlcv_synthetic_trade():
    source = ENGINE.read_text()
    assert "Generate a synthetic trade from last 5m close price" not in source
    assert 'quantity": 0.001' not in source


def test_engine_contains_no_oi_proxy_fallback():
    source = ENGINE.read_text()
    assert "OI PROXY" not in source
    assert "derive OI from trade flow proxy" not in source
    assert "cumulative delta as proxy" not in source


def test_engine_contains_no_trade_buffer_exchange_flow_fallback():
    source = ENGINE.read_text()
    assert "EXCHANGE FLOW FALLBACK" not in source
    assert "CVD fallback" not in source
    assert "EF/CVD COMPLEMENT" not in source
