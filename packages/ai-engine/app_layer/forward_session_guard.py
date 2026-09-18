"""Controlled forward-evidence session provenance and gating.

Forward sessions C and D are evidence-collection windows, not certification
or live-trading authorization. A session may start only when the active
parameter freeze is clean, source-bound to the current Git commit, and the
market-data source is production Binance with simulated execution.

Session D additionally requires Session C to have completed. Session
artifacts are immutable once completed and are never silently overwritten.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from scanner.parameter_freeze import ParameterFreeze


FORWARD_SCHEMA_VERSION = 1
ALLOWED_SESSIONS = {"C", "D"}


class ForwardSessionError(RuntimeError):
    """Raised when a forward-evidence session cannot be safely started."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: Dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def _artifact_path(root: Path, session: str) -> Path:
    return root / f"session_{session}.json"


def _load_artifact(root: Path, session: str) -> Dict[str, Any] | None:
    path = _artifact_path(root, session)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardSessionError(f"Invalid {session} session artifact: {exc}") from exc



def _verify_completed_artifact(artifact: Dict[str, Any], session: str) -> None:
    """Verify an immutable completed session artifact before it is reused."""
    if artifact.get("schema_version") != FORWARD_SCHEMA_VERSION:
        raise ForwardSessionError(f"Invalid {session} session schema version")
    if artifact.get("session") != session:
        raise ForwardSessionError(f"{session} session artifact has mismatched session identity")
    if artifact.get("status") != "COMPLETED":
        raise ForwardSessionError(f"{session} session artifact is not completed")

    summary = artifact.get("summary")
    if not isinstance(summary, dict):
        raise ForwardSessionError(f"{session} session artifact is missing its final summary")

    expected_summary_hash = hashlib.sha256(
        _canonical_json(summary).encode("utf-8")
    ).hexdigest()
    if artifact.get("summary_sha256") != expected_summary_hash:
        raise ForwardSessionError(f"{session} session summary integrity check failed")

    stored_provenance_hash = artifact.get("provenance_sha256")
    if not isinstance(stored_provenance_hash, str) or not stored_provenance_hash:
        raise ForwardSessionError(f"{session} session provenance hash is missing")

    expected_provenance_hash = hashlib.sha256(
        _canonical_json(
            {k: v for k, v in artifact.items() if k != "provenance_sha256"}
        ).encode("utf-8")
    ).hexdigest()
    if stored_provenance_hash != expected_provenance_hash:
        raise ForwardSessionError(f"{session} session provenance integrity check failed")


def _validate_freeze() -> Dict[str, Any]:
    status = ParameterFreeze().check()
    if not status.get("frozen") or not status.get("clean"):
        reason = status.get("reason", "parameter freeze is not clean")
        raise ForwardSessionError(f"Forward session blocked: {reason}")
    if not status.get("code_commit_sha") or not status.get("hash"):
        raise ForwardSessionError("Forward session blocked: freeze provenance is incomplete")
    return status


class ForwardSessionGuard:
    """Fail-closed lifecycle guard for controlled forward evidence."""

    @staticmethod
    def prepare(
        session: str,
        *,
        production_data: bool,
        artifact_root: Path,
    ) -> Dict[str, Any]:
        session = str(session).upper().strip()
        if session not in ALLOWED_SESSIONS:
            raise ForwardSessionError("Forward session must be C or D")
        if not production_data:
            raise ForwardSessionError(
                "Forward evidence requires production market data; testnet is not valid evidence"
            )

        freeze = _validate_freeze()
        existing = _load_artifact(artifact_root, session)
        if existing:
            status = existing.get("status")
            if status == "COMPLETED":
                raise ForwardSessionError(
                    f"Session {session} is already completed; completed evidence cannot be overwritten"
                )
            if (
                status == "RUNNING"
                and existing.get("code_commit_sha") == freeze["code_commit_sha"]
                and existing.get("parameter_hash") == freeze["hash"]
            ):
                return existing
            raise ForwardSessionError(f"Session {session} has incompatible prior provenance")

        if session == "D":
            session_c = _load_artifact(artifact_root, "C")
            if not session_c or session_c.get("status") != "COMPLETED":
                raise ForwardSessionError(
                    "Session D requires a completed Session C artifact"
                )
            _verify_completed_artifact(session_c, "C")
            if session_c.get("code_commit_sha") != freeze["code_commit_sha"]:
                raise ForwardSessionError(
                    "Session D blocked: Session C used a different source commit"
                )
            if session_c.get("parameter_hash") != freeze["hash"]:
                raise ForwardSessionError(
                    "Session D blocked: Session C used a different parameter hash"
                )

        started = _now()
        provenance = {
            "schema_version": FORWARD_SCHEMA_VERSION,
            "session": session,
            "status": "RUNNING",
            "started_at": started.isoformat(),
            "started_at_epoch": started.timestamp(),
            "code_commit_sha": freeze["code_commit_sha"],
            "parameter_hash": freeze["hash"],
            "parameter_freeze_at": freeze.get("frozen_at", ""),
            "data_source": "BINANCE_FUTURES_PRODUCTION",
            "market_data_mode": "PRODUCTION",
            "execution_mode": "SIMULATION",
            "real_orders": False,
            "orders_policy": "NO_REAL_ORDERS",
        }
        provenance["provenance_sha256"] = hashlib.sha256(
            _canonical_json(provenance).encode("utf-8")
        ).hexdigest()
        _atomic_write(_artifact_path(artifact_root, session), provenance)
        return provenance

    @staticmethod
    def finalize(
        provenance: Dict[str, Any],
        summary: Dict[str, Any],
        *,
        artifact_root: Path,
        evidence_bundle: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        session = str(provenance.get("session", "")).upper()
        if session not in ALLOWED_SESSIONS:
            raise ForwardSessionError("Invalid forward session provenance")
        artifact = _load_artifact(artifact_root, session)
        if not artifact:
            raise ForwardSessionError(f"Missing {session} session artifact")
        if artifact.get("status") != "RUNNING":
            raise ForwardSessionError(f"Session {session} is not running")

        freeze = _validate_freeze()
        if artifact.get("code_commit_sha") != freeze["code_commit_sha"]:
            raise ForwardSessionError("Forward session commit changed before finalization")
        if artifact.get("parameter_hash") != freeze["hash"]:
            raise ForwardSessionError("Forward session parameter hash changed before finalization")

        completed = _now()
        if evidence_bundle is not None and not isinstance(evidence_bundle, dict):
            raise ForwardSessionError("Forward evidence bundle metadata must be a dictionary")

        final = dict(artifact)
        final.update(
            {
                "status": "COMPLETED",
                "ended_at": completed.isoformat(),
                "ended_at_epoch": completed.timestamp(),
                "closed_trade_count": int(summary.get("total_trades", 0)),
                "signal_count": int(summary.get("total_signals", 0)),
                # Preserve the exact final summary inside the immutable session
                # artifact. The shared paper_trading_summary.json is overwritten
                # by subsequent sessions, so the hash alone would otherwise be
                # impossible to re-verify after Session D starts.
                "summary": summary,
                "evidence_bundle": dict(evidence_bundle or {}),
                "summary_sha256": hashlib.sha256(
                    _canonical_json(summary).encode("utf-8")
                ).hexdigest(),
            }
        )
        final["provenance_sha256"] = hashlib.sha256(
            _canonical_json({k: v for k, v in final.items() if k != "provenance_sha256"}).encode("utf-8")
        ).hexdigest()
        _atomic_write(_artifact_path(artifact_root, session), final)
        return final
