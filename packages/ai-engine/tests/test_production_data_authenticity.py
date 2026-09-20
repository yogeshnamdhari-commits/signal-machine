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


def test_engine_does_not_route_regular_aggtrade_into_liquidation_engine():
    source = ENGINE.read_text()
    assert 'await self.liquidation.process_trade(sym, data)' not in source
    assert 'Regular aggTrade events are NOT liquidation evidence' in source


def test_engine_does_not_invent_directional_volume():
    source = ENGINE.read_text()
    assert 'vol_bias = "neutral" if vol > 0 else None' not in source


def test_engine_requires_mark_price_for_oi_usd_valuation():
    source = ENGINE.read_text()
    assert 'oi_price = mark if mark > 0 else price' not in source
