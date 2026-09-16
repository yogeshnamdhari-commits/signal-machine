from config import config
from config.environment_contract import DELTA_PRODUCTION_REST, DELTA_TESTNET_REST, validate_runtime_config


def test_delta_testnet_uses_distinct_indian_testnet_rest():
    validate_runtime_config(config)
    assert config.delta.testnet is True
    assert config.delta.rest_url == DELTA_TESTNET_REST
    assert config.delta.rest_url != DELTA_PRODUCTION_REST
    assert "testnet" in config.delta.rest_url or "cdn-ind.testnet" in config.delta.rest_url


def test_binance_scanner_has_real_l2_depth_stream():
    validate_runtime_config(config)
    assert "depth@100ms" in config.scanner.ws_streams


def test_binance_stream_budget_stays_below_exchange_limit():
    validate_runtime_config(config)
    per_symbol = len(config.scanner.ws_streams)
    global_streams = 1 + len(config.scanner.global_streams)
    total = config.scanner.max_symbols * per_symbol + global_streams
    assert total <= 1024
