"""Fail-closed binding between live certification and immutable C/D evidence."""
from __future__ import annotations

import hashlib
import json
from math import isfinite
from pathlib import Path
from typing import Any, Dict

DEFAULT_FORWARD_EVIDENCE_PATH = (
    Path(__file__).resolve().parent.parent
    / "data"
    / "reports"
    / "forward_sessions"
    / "forward_aggregate.json"
)

REQUIRED_SCHEMA_VERSION = 1
MIN_FORWARD_SIGNALS = 500
MIN_FORWARD_CLOSED_TRADES = 100
MIN_FORWARD_PROFIT_FACTOR = 1.20
EPSILON = 1e-9


class ForwardEvidenceBindingError(RuntimeError):
    """Raised when the immutable C/D evidence cannot back a live certificate."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def verify_forward_evidence_report(
    path: Path | None = None,
    *,
    expected_commit: str,
) -> Dict[str, Any]:
    """Verify the immutable C/D aggregate required for live certification."""
    report_path = Path(path) if path is not None else DEFAULT_FORWARD_EVIDENCE_PATH
    try:
        if report_path.is_symlink() or not report_path.is_file():
            raise ForwardEvidenceBindingError(
                f"Forward evidence aggregate is not a safe file: {report_path}"
            )
        payload = report_path.read_bytes()
        report = json.loads(payload.decode("utf-8"))
    except ForwardEvidenceBindingError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ForwardEvidenceBindingError(
            f"Forward evidence aggregate is unreadable: {exc}"
        ) from exc

    if not isinstance(report, dict):
        raise ForwardEvidenceBindingError("Forward evidence aggregate is not a JSON object")
    if report.get("schema_version") != REQUIRED_SCHEMA_VERSION:
        raise ForwardEvidenceBindingError("Forward evidence aggregate schema is invalid")
    if report.get("status") != "EVIDENCE_VALID":
        raise ForwardEvidenceBindingError("Forward evidence aggregate is not valid evidence")
    if report.get("authorization") != "NOT_A_LIVE_CERTIFICATION":
        raise ForwardEvidenceBindingError(
            "Forward evidence aggregate has an invalid authorization marker"
        )
    if not expected_commit or report.get("code_commit_sha") != expected_commit:
        raise ForwardEvidenceBindingError(
            "Forward evidence aggregate commit does not match the running source"
        )
    if report.get("sessions") != ["C", "D"]:
        raise ForwardEvidenceBindingError("Forward evidence aggregate is not a complete C/D aggregate")

    stored_digest = report.get("aggregate_sha256")
    expected_digest = _sha256_bytes(
        _canonical_json(
            {key: value for key, value in report.items() if key != "aggregate_sha256"}
        ).encode("utf-8")
    )
    if not isinstance(stored_digest, str) or stored_digest != expected_digest:
        raise ForwardEvidenceBindingError("Forward evidence aggregate integrity hash mismatch")

    def finite_number(name: str) -> float:
        value = report.get(name)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(float(value)):
            raise ForwardEvidenceBindingError(f"Forward evidence field {name!r} is invalid")
        return float(value)

    total_signals = finite_number("total_signals")
    total_trades = finite_number("total_closed_trades")
    profit_factor = finite_number("profit_factor")
    expectancy = finite_number("expectancy")
    net_pnl = finite_number("net_pnl")

    if total_signals < MIN_FORWARD_SIGNALS:
        raise ForwardEvidenceBindingError("Forward evidence signal threshold is not met")
    if total_trades < MIN_FORWARD_CLOSED_TRADES:
        raise ForwardEvidenceBindingError("Forward evidence trade threshold is not met")
    if profit_factor <= MIN_FORWARD_PROFIT_FACTOR:
        raise ForwardEvidenceBindingError("Forward evidence profit-factor threshold is not met")
    if expectancy <= 0:
        raise ForwardEvidenceBindingError("Forward evidence expectancy is not positive")
    if net_pnl <= 0:
        raise ForwardEvidenceBindingError("Forward evidence net PnL is not positive")

    if not isinstance(report.get("parameter_hash"), str) or not report["parameter_hash"]:
        raise ForwardEvidenceBindingError("Forward evidence parameter hash is missing")

    return {
        **report,
        "_file_sha256": _sha256_bytes(payload),
        "_path": str(report_path),
    }
