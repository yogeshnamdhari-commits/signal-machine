from config import config
from config.environment_contract import validate_runtime_config


def test_market_data_websocket_uses_production_feed():
    validate_runtime_config(config)
    assert config.binance.ws_url == config.binance.ws_production


def test_market_data_rest_can_remain_testnet_for_execution():
    validate_runtime_config(config)
    if config.binance.testnet:
        assert config.binance.rest_url == config.binance.rest_testnet
