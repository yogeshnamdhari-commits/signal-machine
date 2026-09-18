"""Fail-closed live trading orchestrator.

Starts the canonical signal engine and execution engine together only after
all explicit live-readiness controls pass. It never creates or modifies a live
certification artifact.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Optional

from loguru import logger

from config import config
from execution.execution_engine import ExecutionEngine
from validation.live_gate import LiveCertificationError, verify_live_certification
from core.engine import DeltaTerminalEngine


class LiveTradingConfigurationError(RuntimeError):
    """The live process is not explicitly and safely armed."""


@dataclass(frozen=True)
class LiveTradingPreflight:
    certification_commit: str
    configuration_fingerprint: str
    live_armed: bool
    production_endpoint: bool
    credentials_present: bool


def _env_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def verify_live_runtime_preflight() -> LiveTradingPreflight:
    """Validate all local live controls before starting any execution task."""
    artifact = verify_live_certification()

    if not _env_true("LIVE_TRADING_ARMED"):
        raise LiveTradingConfigurationError(
            "LIVE_TRADING_ARMED must be explicitly set to true"
        )

    if bool(config.binance.testnet):
        raise LiveTradingConfigurationError(
            "Production live mode requires config.binance.testnet=false"
        )

    api_key = str(config.binance.api_key or "").strip()
    api_secret = str(config.binance.api_secret or "").strip()
    if not api_key or not api_secret:
        raise LiveTradingConfigurationError(
            "Binance production API credentials are missing"
        )

    rest_url = str(config.binance.rest_url or "").lower()
    production_endpoint = "testnet" not in rest_url and "demo" not in rest_url
    if not production_endpoint:
        raise LiveTradingConfigurationError(
            "Binance REST endpoint is not a production endpoint"
        )

    return LiveTradingPreflight(
        certification_commit=str(artifact.get("commit_sha", "")),
        configuration_fingerprint=str(artifact.get("configuration_fingerprint", "")),
        live_armed=True,
        production_endpoint=True,
        credentials_present=True,
    )


async def run_live() -> None:
    """Run signal generation and live execution as one coordinated process."""
    preflight = verify_live_runtime_preflight()
    logger.critical(
        "LIVE TRADING STARTING — certified_commit={} fingerprint={}",
        preflight.certification_commit,
        preflight.configuration_fingerprint,
    )

    signal_engine = DeltaTerminalEngine()
    execution_engine = ExecutionEngine()

    started_execution = False
    started_signal_engine = False
    try:
        await execution_engine.start()
        started_execution = True
        await signal_engine.start()
        started_signal_engine = True

        await signal_engine.ws.wait_until_stopped()
    finally:
        if started_signal_engine:
            await signal_engine.stop()
        if started_execution:
            await execution_engine.stop()
