from config import config
from exchanges.binance_ws import market_data_ws_url


def test_market_data_websocket_uses_production_feed():
    assert market_data_ws_url(config) == config.binance.ws_production


def test_market_data_websocket_is_not_trading_testnet():
    assert market_data_ws_url(config) != config.binance.ws_testnet
