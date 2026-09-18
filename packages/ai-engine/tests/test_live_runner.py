import pytest

from execution.live_runner import (
    LiveTradingConfigurationError,
    verify_live_runtime_preflight,
)


def _cert():
    return {
        "state": "LIVE_ELIGIBLE",
        "commit_sha": "verified-commit",
        "configuration_fingerprint": "fp",
    }


def test_live_preflight_requires_explicit_arm(monkeypatch):
    import execution.live_runner as runner

    monkeypatch.setattr(runner, "verify_live_certification", _cert)
    monkeypatch.delenv("LIVE_TRADING_ARMED", raising=False)
    monkeypatch.setattr(runner.config.binance, "testnet", False, raising=False)
    monkeypatch.setattr(runner.config.binance, "api_key", "key", raising=False)
    monkeypatch.setattr(runner.config.binance, "api_secret", "secret", raising=False)
    monkeypatch.setattr(runner.config.binance, "rest_url", "https://fapi.binance.com", raising=False)

    with pytest.raises(LiveTradingConfigurationError, match="LIVE_TRADING_ARMED"):
        verify_live_runtime_preflight()


def test_live_preflight_rejects_testnet(monkeypatch):
    import execution.live_runner as runner

    monkeypatch.setattr(runner, "verify_live_certification", _cert)
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    monkeypatch.setattr(runner.config.binance, "testnet", True, raising=False)
    monkeypatch.setattr(runner.config.binance, "api_key", "key", raising=False)
    monkeypatch.setattr(runner.config.binance, "api_secret", "secret", raising=False)
    monkeypatch.setattr(runner.config.binance, "rest_url", "https://testnet.binancefuture.com", raising=False)

    with pytest.raises(LiveTradingConfigurationError, match="testnet=false"):
        verify_live_runtime_preflight()


def test_live_preflight_rejects_missing_credentials(monkeypatch):
    import execution.live_runner as runner

    monkeypatch.setattr(runner, "verify_live_certification", _cert)
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    monkeypatch.setattr(runner.config.binance, "testnet", False, raising=False)
    monkeypatch.setattr(runner.config.binance, "api_key", "", raising=False)
    monkeypatch.setattr(runner.config.binance, "api_secret", "", raising=False)
    monkeypatch.setattr(runner.config.binance, "rest_url", "https://fapi.binance.com", raising=False)

    with pytest.raises(LiveTradingConfigurationError, match="credentials are missing"):
        verify_live_runtime_preflight()


def test_live_preflight_accepts_complete_explicit_configuration(monkeypatch):
    import execution.live_runner as runner

    monkeypatch.setattr(runner, "verify_live_certification", _cert)
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    monkeypatch.setattr(runner.config.binance, "testnet", False, raising=False)
    monkeypatch.setattr(runner.config.binance, "api_key", "key", raising=False)
    monkeypatch.setattr(runner.config.binance, "api_secret", "secret", raising=False)
    monkeypatch.setattr(runner.config.binance, "rest_url", "https://fapi.binance.com", raising=False)

    result = verify_live_runtime_preflight()
    assert result.live_armed is True
    assert result.production_endpoint is True
    assert result.credentials_present is True
    assert result.certification_commit == "verified-commit"
