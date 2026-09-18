from types import SimpleNamespace

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


def _set_test_config(monkeypatch, *, testnet, api_key, api_secret, rest_url):
    import execution.live_runner as runner

    monkeypatch.setattr(
        runner,
        "config",
        SimpleNamespace(
            binance=SimpleNamespace(
                testnet=testnet,
                api_key=api_key,
                api_secret=api_secret,
                rest_url=rest_url,
            )
        ),
    )
    return runner


def test_live_preflight_requires_explicit_arm(monkeypatch):
    runner = _set_test_config(
        monkeypatch,
        testnet=False,
        api_key="key",
        api_secret="secret",
        rest_url="https://fapi.binance.com",
    )
    monkeypatch.setattr(runner, "verify_live_certification", _cert)
    monkeypatch.delenv("LIVE_TRADING_ARMED", raising=False)

    with pytest.raises(LiveTradingConfigurationError, match="LIVE_TRADING_ARMED"):
        verify_live_runtime_preflight()


def test_live_preflight_rejects_testnet(monkeypatch):
    runner = _set_test_config(
        monkeypatch,
        testnet=True,
        api_key="key",
        api_secret="secret",
        rest_url="https://testnet.binancefuture.com",
    )
    monkeypatch.setattr(runner, "verify_live_certification", _cert)
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")

    with pytest.raises(LiveTradingConfigurationError, match="testnet=false"):
        verify_live_runtime_preflight()


def test_live_preflight_rejects_missing_credentials(monkeypatch):
    runner = _set_test_config(
        monkeypatch,
        testnet=False,
        api_key="",
        api_secret="",
        rest_url="https://fapi.binance.com",
    )
    monkeypatch.setattr(runner, "verify_live_certification", _cert)
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")

    with pytest.raises(LiveTradingConfigurationError, match="credentials are missing"):
        verify_live_runtime_preflight()


def test_live_preflight_accepts_complete_explicit_configuration(monkeypatch):
    runner = _set_test_config(
        monkeypatch,
        testnet=False,
        api_key="key",
        api_secret="secret",
        rest_url="https://fapi.binance.com",
    )
    monkeypatch.setattr(runner, "verify_live_certification", _cert)
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")

    result = verify_live_runtime_preflight()
    assert result.live_armed is True
    assert result.production_endpoint is True
    assert result.credentials_present is True
    assert result.certification_commit == "verified-commit"
