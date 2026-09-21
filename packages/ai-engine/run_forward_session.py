
"""Restart-safe runner for controlled C/D forward-evidence sessions.

This wrapper is simulation-only. It uses production Binance market data,
never submits exchange orders, and preserves session history across bounded
self-hosted-runner job restarts.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import signal
import sys
from pathlib import Path
from typing import Dict, List

_ai_root = Path(__file__).resolve().parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from app_layer.forward_session_guard import ForwardSessionGuard, ForwardSessionError
from backtesting.paper_trading_validator import (
    DATA_DIR,
    PaperSignal,
    PaperTrade,
    PaperTradingEngine,
)
from scanner.parameter_freeze import ParameterFreeze


def _to_int(value: str, name: str) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise ForwardSessionError(f"Invalid {name}: {value!r}") from exc


def _to_float(value: str, name: str) -> float:
    try:
        result = float(str(value))
    except (TypeError, ValueError) as exc:
        raise ForwardSessionError(f"Invalid {name}: {value!r}") from exc
    if not (result == result and abs(result) != float("inf")):
        raise ForwardSessionError(f"Non-finite {name}: {value!r}")
    return result


def _load_rows(path: Path) -> List[Dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _restore_signals(path: Path, session_start: float) -> List[PaperSignal]:
    rows = _load_rows(path)
    restored: List[PaperSignal] = []
    seen: set[str] = set()
    for row in rows:
        signal_ts = _to_float(row.get("timestamp", ""), "signal timestamp")
        if signal_ts < session_start:
            continue
        signal_id = str(row.get("id", "")).strip()
        if not signal_id or signal_id in seen:
            raise ForwardSessionError("Signal log contains an empty or duplicate signal id")
        seen.add(signal_id)
        restored.append(
            PaperSignal(
                id=signal_id,
                timestamp=signal_ts,
                symbol=str(row.get("symbol", "")).strip(),
                side=str(row.get("side", "")).strip().upper(),
                entry_price=_to_float(row.get("entry_price", ""), "signal entry_price"),
                stop_loss=_to_float(row.get("stop_loss", ""), "signal stop_loss"),
                take_profit=_to_float(row.get("take_profit", ""), "signal take_profit"),
                confidence=_to_float(row.get("confidence", "0"), "signal confidence"),
                institutional_score=_to_float(
                    row.get("institutional_score", "0"), "signal institutional_score"
                ),
                market_regime=str(row.get("market_regime", "")),
                position_size=_to_float(row.get("position_size", "0"), "signal position_size"),
                status=str(row.get("status", "generated")).strip().lower(),
                rejection_reason=str(row.get("rejection_reason", "")),
                filled_at=_to_float(row.get("filled_at", "0"), "signal filled_at"),
                mtf_alignment=_to_int(row.get("mtf_alignment", "0"), "signal mtf_alignment"),
                risk_reward=_to_float(row.get("risk_reward", "0"), "signal risk_reward"),
            )
        )
    return restored


def _restore_trades(path: Path, session_start: float) -> List[PaperTrade]:
    rows = _load_rows(path)
    restored: List[PaperTrade] = []
    seen: set[str] = set()
    for row in rows:
        entry_time = _to_float(row.get("entry_time", ""), "trade entry_time")
        if entry_time < session_start:
            continue
        trade_id = str(row.get("id", "")).strip()
        if not trade_id or trade_id in seen:
            raise ForwardSessionError("Trade log contains an empty or duplicate trade id")
        seen.add(trade_id)
        restored.append(
            PaperTrade(
                id=trade_id,
                signal_id=str(row.get("signal_id", "")).strip(),
                symbol=str(row.get("symbol", "")).strip(),
                side=str(row.get("side", "")).strip().upper(),
                entry_time=entry_time,
                exit_time=_to_float(row.get("exit_time", "0"), "trade exit_time"),
                duration_min=_to_float(row.get("duration_min", "0"), "trade duration_min"),
                entry_price=_to_float(row.get("entry_price", "0"), "trade entry_price"),
                expected_entry=_to_float(row.get("expected_entry", "0"), "trade expected_entry"),
                exit_price=_to_float(row.get("exit_price", "0"), "trade exit_price"),
                expected_exit=_to_float(row.get("expected_exit", "0"), "trade expected_exit"),
                quantity=_to_float(row.get("quantity", "0"), "trade quantity"),
                leverage=_to_int(row.get("leverage", "10"), "trade leverage"),
                gross_pnl=_to_float(row.get("gross_pnl", "0"), "trade gross_pnl"),
                net_pnl=_to_float(row.get("net_pnl", "0"), "trade net_pnl"),
                return_pct=_to_float(row.get("return_pct", "0"), "trade return_pct"),
                entry_slippage=_to_float(row.get("entry_slippage", "0"), "trade entry_slippage"),
                exit_slippage=_to_float(row.get("exit_slippage", "0"), "trade exit_slippage"),
                total_slippage=_to_float(row.get("total_slippage", "0"), "trade total_slippage"),
                fees=_to_float(row.get("fees", "0"), "trade fees"),
                funding_pnl=_to_float(row.get("funding_pnl", "0"), "trade funding_pnl"),
                funding_events=_to_int(row.get("funding_events", "0"), "trade funding_events"),
                last_funding_rate=_to_float(
                    row.get("last_funding_rate", "0"), "trade last_funding_rate"
                ),
                last_funding_time=_to_int(
                    row.get("last_funding_time", "0"), "trade last_funding_time"
                ),
                drawdown=_to_float(row.get("drawdown", "0"), "trade drawdown"),
                exit_reason=str(row.get("exit_reason", "")),
                stop_loss=_to_float(row.get("stop_loss", "0"), "trade stop_loss"),
                take_profit=_to_float(row.get("take_profit", "0"), "trade take_profit"),
                confidence=_to_float(row.get("confidence", "0"), "trade confidence"),
                institutional_score=_to_float(
                    row.get("institutional_score", "0"), "trade institutional_score"
                ),
                market_regime=str(row.get("market_regime", "")),
                status=str(row.get("status", "closed")).strip().lower(),
            )
        )
    return restored


async def _checkpoint_only_stop(engine: PaperTradingEngine) -> None:
    """Persist restart state without completing a C/D evidence session."""
    engine.is_running = False
    for task in getattr(engine, "_tasks", []):
        if not task.done():
            task.cancel()
    pending = [task for task in getattr(engine, "_tasks", []) if not task.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    try:
        await engine.ws.stop()
    finally:
        engine._settle_due_funding()
        await engine._save_state()
        engine._export_trades_csv()
        engine._export_signals_csv()


async def _timer(engine: PaperTradingEngine, hours: float) -> None:
    await asyncio.sleep(max(1.0, hours * 3600.0))
    engine.is_running = False


async def _run(args: argparse.Namespace) -> None:
    if os.environ.get("BINANCE_TESTNET", "true").strip().lower() == "true":
        raise ForwardSessionError(
            "Forward evidence runner requires BINANCE_TESTNET=false; refusing testnet evidence"
        )

    freeze = ParameterFreeze()
    freeze_status = freeze.check()
    if not freeze_status.get("frozen") or not freeze_status.get("clean"):
        if args.create_freeze:
            created = freeze.freeze()
            if not created.get("frozen"):
                raise ForwardSessionError(
                    f"Parameter freeze creation failed: {created.get('reason', 'unknown error')}"
                )
            freeze_status = freeze.check()
        if not freeze_status.get("frozen") or not freeze_status.get("clean"):
            raise ForwardSessionError(
                f"Forward run blocked by parameter freeze: {freeze_status.get('reason', 'unclean freeze')}"
            )

    artifact_root = DATA_DIR / "forward_sessions"
    provenance = ForwardSessionGuard.prepare(
        args.session,
        production_data=True,
        artifact_root=artifact_root,
    )
    session_start = float(provenance.get("started_at_epoch", 0.0))
    if session_start <= 0:
        raise ForwardSessionError("Forward session has no valid start timestamp")

    engine = PaperTradingEngine(forward_provenance=provenance)
    engine.signals = _restore_signals(DATA_DIR / "paper_trading_signals.csv", session_start)
    engine.closed_trades = _restore_trades(DATA_DIR / "paper_trading_trades.csv", session_start)

    original_stop = engine.stop
    if not args.finalize:
        async def checkpoint_stop() -> None:
            await _checkpoint_only_stop(engine)
        engine.stop = checkpoint_stop  # type: ignore[method-assign]

    timer_task = asyncio.create_task(_timer(engine, args.hours))
    try:
        loop = asyncio.get_running_loop()

        def _on_signal(_signum, _frame=None) -> None:
            engine.is_running = False

        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, _on_signal, signum)
            except (NotImplementedError, RuntimeError):
                signal.signal(signum, _on_signal)

        await engine.start()
    finally:
        timer_task.cancel()
        await asyncio.gather(timer_task, return_exceptions=True)
        if args.finalize:
            engine.stop = original_stop  # type: ignore[method-assign]


def main() -> None:
    parser = argparse.ArgumentParser(description="Controlled forward C/D session runner")
    parser.add_argument("--session", choices=("C", "D"), required=True)
    parser.add_argument("--hours", type=float, required=True)
    parser.add_argument("--create-freeze", action="store_true")
    parser.add_argument("--finalize", action="store_true")
    args = parser.parse_args()
    if args.hours <= 0 or args.hours > 119:
        raise SystemExit("--hours must be >0 and <=119")
    os.environ["BINANCE_TESTNET"] = "false"
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
