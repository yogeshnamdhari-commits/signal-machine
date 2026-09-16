"""Effective runtime defaults applied after legacy settings construction."""
from __future__ import annotations

from dataclasses import replace

from config.environment_contract import (
    DELTA_TESTNET_PRIVATE_WS,
    DELTA_TESTNET_REST,
)


def apply_runtime_defaults(app_config):
    """Return a normalized configuration without changing strategy thresholds."""
    delta = app_config.delta
    if delta.testnet:
        delta = replace(
            delta,
            ws_testnet=DELTA_TESTNET_PRIVATE_WS,
            rest_testnet=DELTA_TESTNET_REST,
        )

    scanner = app_config.scanner
    # DOM analytics expects an actual L2 depth stream. Keep the existing aggTrade
    # and OI feeds but replace the L1-only bookTicker with depth@100ms.
    if "depth@100ms" not in scanner.ws_streams:
        streams = tuple("depth@100ms" if item == "bookTicker" else item for item in scanner.ws_streams)
        if "depth@100ms" not in streams:
            streams = (*streams, "depth@100ms")
        scanner = replace(scanner, ws_streams=streams)

    return replace(app_config, delta=delta, scanner=scanner)
