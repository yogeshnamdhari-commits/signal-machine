from config import config
from config.environment_contract import DELTA_PRODUCTION_REST, DELTA_TESTNET_REST, validate_runtime_config
from config.schema import config_fingerprint


def test_delta_testnet_is_not_production():
    validate_runtime_config(config)
    assert config.delta.testnet is True
    assert config.delta.rest_url == DELTA_TESTNET_REST
    assert config.delta.rest_url != DELTA_PRODUCTION_REST


def test_configuration_fingerprint_is_stable():
    first = config_fingerprint(config)
    second = config_fingerprint(config)
    assert first == second
    assert len(first) == 64
