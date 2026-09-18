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



def _verify_completed_artifact(
    artifact: Dict[str, Any],
    session: str,
    *,
    data_root: Path | None = None,
) -> None:
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

    evidence_bundle = artifact.get("evidence_bundle")
    if not isinstance(evidence_bundle, dict):
        raise ForwardSessionError(f"{session} session evidence bundle metadata is missing")
    if evidence_bundle.get("session") != session:
        raise ForwardSessionError(f"{session} session evidence bundle identity mismatch")
    if evidence_bundle.get("schema_version") != 1:
        raise ForwardSessionError(f"{session} session evidence bundle schema is invalid")
    artifacts = evidence_bundle.get("artifacts")
    if not isinstance(artifacts, dict) or not {"trades", "signals", "summary"}.issubset(artifacts):
        raise ForwardSessionError(f"{session} session evidence bundle is incomplete")

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

    if data_root is not None:
        _verify_evidence_bundle_files(artifact, data_root=data_root)


def _verify_evidence_bundle_files(artifact: Dict[str, Any], *, data_root: Path) -> None:
    """Verify the immutable bundle paths, hashes, and byte counts before reuse."""
    bundle = artifact.get("evidence_bundle")
    if not isinstance(bundle, dict):
        raise ForwardSessionError("Session evidence bundle metadata is missing")

    root_rel = bundle.get("root")
    indexed = bundle.get("artifacts")
    if not isinstance(root_rel, str) or not root_rel:
        raise ForwardSessionError("Session evidence bundle root is missing")
    if not isinstance(indexed, dict):
        raise ForwardSessionError("Session evidence bundle index is missing")

    data_root = data_root.resolve()
    raw_bundle_root = data_root / root_rel
    if raw_bundle_root.is_symlink():
        raise ForwardSessionError("Session evidence bundle root must not be a symlink")
    bundle_root = raw_bundle_root.resolve()
    try:
        bundle_root.relative_to(data_root)
    except ValueError as exc:
        raise ForwardSessionError("Session evidence bundle root escapes the data root") from exc
    if not bundle_root.is_dir() or bundle_root.is_symlink():
        raise ForwardSessionError("Session evidence bundle root is not a safe directory")

    for required in ("trades", "signals", "summary"):
        meta = indexed.get(required)
        if not isinstance(meta, dict):
            raise ForwardSessionError(f"Session evidence bundle is missing {required!r} metadata")
        rel = meta.get("path")
        expected_sha = meta.get("sha256")
        expected_bytes = meta.get("bytes")
        if not isinstance(rel, str) or not isinstance(expected_sha, str):
            raise ForwardSessionError(f"Session evidence {required!r} path/hash is invalid")
        if not isinstance(expected_bytes, int) or expected_bytes < 0:
            raise ForwardSessionError(f"Session evidence {required!r} byte count is invalid")

        raw_path = data_root / rel
        if raw_path.is_symlink():
            raise ForwardSessionError(f"Session evidence {required!r} must not be a symlink")
        path = raw_path.resolve()
        try:
            path.relative_to(data_root)
            path.relative_to(bundle_root)
        except ValueError as exc:
            raise ForwardSessionError(
                f"Session evidence {required!r} path escapes its declared bundle root"
            ) from exc
        if path.is_symlink() or not path.is_file():
            raise ForwardSessionError(f"Session evidence {required!r} is not a safe file")
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected_sha:
            raise ForwardSessionError(f"Session evidence {required!r} hash mismatch")
        if len(payload) != expected_bytes:
            raise ForwardSessionError(f"Session evidence {required!r} byte count mismatch")

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
            _verify_completed_artifact(
                session_c,
                "C",
                data_root=artifact_root.parent,
            )
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
        if not isinstance(evidence_bundle, dict) or not evidence_bundle:
            raise ForwardSessionError(
                "Forward session finalization requires an immutable evidence bundle"
            )
        if evidence_bundle.get("session") != session:
            raise ForwardSessionError("Forward evidence bundle session identity mismatch")
        if evidence_bundle.get("schema_version") != 1:
            raise ForwardSessionError("Forward evidence bundle schema is invalid")
        bundle_artifacts = evidence_bundle.get("artifacts")
        if (
            not isinstance(bundle_artifacts, dict)
            or not {"trades", "signals", "summary"}.issubset(bundle_artifacts)
        ):
            raise ForwardSessionError("Forward evidence bundle is incomplete")

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
