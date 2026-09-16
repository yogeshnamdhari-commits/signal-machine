"""Fail-closed environment contracts for live/runtime execution."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from config.schema import config_fingerprint

DELTA_PRODUCTION_REST = "https://api.india.delta.exchange"
DELTA_TESTNET_REST = "https://cdn-ind.testnet.deltaex.org"
DELTA_PRODUCTION_PRIVATE_WS = "wss://socket.india.delta.exchange"
DELTA_PRODUCTION_PUBLIC_WS = "wss://public-socket.india.delta.exchange"
DELTA_TESTNET_PRIVATE_WS = "wss://socket-ind.testnet.deltaex.org"
DELTA_TESTNET_PUBLIC_WS = "wss://socket-ind-pub.testnet.deltaex.org"
BINANCE_MAX_STREAMS_PER_CONNECTION = 1024


class EnvironmentContractError(RuntimeError):
    """Raised when runtime configuration is unsafe or internally inconsistent."""


def enforce_data_feed_environment(config: Any) -> None:
    """Make the market-data path production-grade while retaining testnet trading REST.

    Public Binance market data is deliberately sourced from the production feed so
    a testnet execution environment cannot silently display a different market.
    The adapter is public market-data-only; execution credentials/endpoints are
    validated separately and are not changed here.
    """
    streams = tuple(config.scanner.ws_streams)
    if "depth@100ms" not in streams:
        streams = streams + ("depth@100ms",)
    global_count = 1 + len(config.scanner.global_streams)
    total_streams = config.scanner.max_symbols * len(streams) + global_count
    if total_streams > BINANCE_MAX_STREAMS_PER_CONNECTION:
        raise EnvironmentContractError(
            f"Binance stream budget exceeded: {total_streams}>{BINANCE_MAX_STREAMS_PER_CONNECTION}"
        )
    object.__setattr__(config, "scanner", replace(config.scanner, ws_streams=streams))

    # Preserve testnet REST trading configuration but route public market data
    # WebSocket traffic to the production market feed.
    if config.binance.testnet and config.binance.ws_url != config.binance.ws_production:
        object.__setattr__(config, "binance", replace(config.binance, ws_testnet=config.binance.ws_production))


def enforce_delta_environment(config: Any) -> str:
    """Normalize Delta testnet endpoints and verify environment separation."""
    delta = config.delta
    if delta.testnet:
        corrected = replace(
            delta,
            ws_testnet=DELTA_TESTNET_PRIVATE_WS,
            rest_testnet=DELTA_TESTNET_REST,
        )
        object.__setattr__(config, "delta", corrected)
    if config.delta.rest_url == DELTA_PRODUCTION_REST and config.delta.testnet:
        raise EnvironmentContractError("Delta testnet resolved to production REST endpoint")
    if config.delta.ws_url in {DELTA_PRODUCTION_PRIVATE_WS, DELTA_PRODUCTION_PUBLIC_WS} and config.delta.testnet:
        raise EnvironmentContractError("Delta testnet resolved to production WebSocket endpoint")
    return config_fingerprint(config)


def validate_runtime_config(config: Any) -> str:
    if getattr(config, "env", "").lower() not in {"development", "test", "staging", "production"}:
        raise EnvironmentContractError(f"Unsupported APP_ENV: {config.env!r}")
    if config.risk.quality_gate_score < 0 or config.risk.quality_gate_score > 100:
        raise EnvironmentContractError("Risk quality_gate_score must be in [0, 100]")
    if config.risk.max_leverage <= 0:
        raise EnvironmentContractError("max_leverage must be positive")
    enforce_data_feed_environment(config)
    return enforce_delta_environment(config)
