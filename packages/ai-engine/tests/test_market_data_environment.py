from config import config


def test_market_data_websocket_uses_production_feed():
    assert config.binance.market_data_ws_url == config.binance.ws_production
