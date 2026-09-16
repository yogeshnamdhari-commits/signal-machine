from config import config
from config.environment_contract import DELTA_PRODUCTION_REST, DELTA_TESTNET_REST


def test_delta_testnet_uses_distinct_indian_testnet_rest():
    assert config.delta.testnet is True
    assert config.delta.rest_url == DELTA_TESTNET_REST
    assert config.delta.rest_url != DELTA_PRODUCTION_REST
    assert "testnet" in config.delta.rest_url or "cdn-ind.testnet" in config.delta.rest_url


def test_binance_scanner_has_real_l2_depth_stream():
    assert "depth@100ms" in config.scanner.ws_streams


def test_binance_stream_budget_stays_below_exchange_limit():
    per_symbol = len(config.scanner.ws_streams)
    global_streams = 1 + len(config.scanner.global_streams)  # !ticker@arr + configured globals
    total = config.scanner.max_symbols * per_symbol + global_streams
    assert total <= 1024
