"""Verify and aggregate immutable Session C/D forward evidence.

This module reports evidence integrity and aggregate observed performance. It
never grants live authorization; certification remains a separate fail-closed
gate.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List


ECONOMIC_EPSILON = 1e-9


REQUIRED_TRADE_FIELDS = {
    "id",
    "signal_id",
    "symbol",
    "side",
    "entry_time",
    "exit_time",
    "gross_pnl",
    "net_pnl",
    "quantity",
    "fees",
    "funding_pnl",
    "total_slippage",
    "status",
}


class ForwardEvidenceError(RuntimeError):
    """Raised when immutable forward evidence cannot be verified."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardEvidenceError(f"Unable to read evidence artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ForwardEvidenceError(f"Evidence artifact {path} is not a JSON object")
    return value


def _verify_session_artifact(path: Path, expected_session: str) -> Dict[str, Any]:
    artifact = _load_json(path)
    if artifact.get("schema_version") != 1:
        raise ForwardEvidenceError(f"Session {expected_session} has unsupported schema")
    if artifact.get("session") != expected_session:
        raise ForwardEvidenceError(f"Session artifact identity mismatch for {expected_session}")
    if artifact.get("status") != "COMPLETED":
        raise ForwardEvidenceError(f"Session {expected_session} is not COMPLETED")

    summary = artifact.get("summary")
    if not isinstance(summary, dict):
        raise ForwardEvidenceError(f"Session {expected_session} has no immutable summary")

    summary_hash = _sha256_bytes(_canonical_json(summary).encode("utf-8"))
    if artifact.get("summary_sha256") != summary_hash:
        raise ForwardEvidenceError(f"Session {expected_session} summary hash mismatch")

    provenance_hash = artifact.get("provenance_sha256")
    if not isinstance(provenance_hash, str) or not provenance_hash:
        raise ForwardEvidenceError(f"Session {expected_session} provenance hash missing")
    expected_provenance_hash = _sha256_bytes(
        _canonical_json(
            {k: v for k, v in artifact.items() if k != "provenance_sha256"}
        ).encode("utf-8")
    )
    if provenance_hash != expected_provenance_hash:
        raise ForwardEvidenceError(f"Session {expected_session} provenance hash mismatch")

    bundle = artifact.get("evidence_bundle")
    if not isinstance(bundle, dict):
        raise ForwardEvidenceError(f"Session {expected_session} evidence bundle metadata missing")
    if bundle.get("session") != expected_session:
        raise ForwardEvidenceError(f"Session {expected_session} evidence bundle identity mismatch")
    artifacts = bundle.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ForwardEvidenceError(f"Session {expected_session} evidence bundle index missing")

    return artifact


def _verify_bundle_files(
    artifact: Dict[str, Any],
    *,
    data_root: Path,
) -> Dict[str, Path]:
    bundle = artifact["evidence_bundle"]
    indexed = bundle["artifacts"]
    bundle_root_rel = bundle.get("root")
    if not isinstance(bundle_root_rel, str) or not bundle_root_rel:
        raise ForwardEvidenceError("Evidence bundle root is missing")
    bundle_root = (data_root / bundle_root_rel).resolve()
    data_root_resolved = data_root.resolve()
    try:
        bundle_root.relative_to(data_root_resolved)
    except ValueError as exc:
        raise ForwardEvidenceError("Evidence bundle root escapes the data root") from exc

    resolved: Dict[str, Path] = {}

    for name, meta in indexed.items():
        if not isinstance(meta, dict):
            raise ForwardEvidenceError(f"Evidence bundle entry {name!r} is invalid")
        rel = meta.get("path")
        expected_sha = meta.get("sha256")
        expected_bytes = meta.get("bytes")
        if not isinstance(rel, str) or not isinstance(expected_sha, str):
            raise ForwardEvidenceError(f"Evidence bundle entry {name!r} lacks path/hash")
        candidate = (data_root / rel).resolve()
        data_root_resolved = data_root.resolve()
        try:
            candidate.relative_to(data_root_resolved)
        except ValueError as exc:
            raise ForwardEvidenceError(
                f"Evidence path escapes the data root: {rel}"
            ) from exc
        try:
            candidate.relative_to(bundle_root)
        except ValueError as exc:
            raise ForwardEvidenceError(
                f"Evidence path is outside the declared bundle root: {rel}"
            ) from exc
        path = candidate
        if not path.is_file():
            raise ForwardEvidenceError(f"Evidence file missing: {path}")
        payload = path.read_bytes()
        if _sha256_bytes(payload) != expected_sha:
            raise ForwardEvidenceError(f"Evidence file hash mismatch: {path}")
        if isinstance(expected_bytes, int) and expected_bytes != len(payload):
            raise ForwardEvidenceError(f"Evidence file size mismatch: {path}")
        resolved[name] = path

    for required in ("trades", "signals", "summary"):
        if required not in resolved:
            raise ForwardEvidenceError(f"Evidence bundle is missing required {required!r} artifact")

    return resolved


def _read_signal_count(path: Path) -> int:
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ForwardEvidenceError(f"Signal log {path} has no header")
            return sum(1 for _ in reader)
    except (OSError, csv.Error, UnicodeError) as exc:
        raise ForwardEvidenceError(f"Unable to read signal log {path}: {exc}") from exc


def _read_canonical_summary(path: Path) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ForwardEvidenceError(f"Unable to read summary artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ForwardEvidenceError(f"Summary artifact {path} is not a JSON object")
    return value


def _read_trades(path: Path) -> List[Dict[str, Any]]:
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            fields = {str(name).strip() for name in (reader.fieldnames or []) if name}
            missing = REQUIRED_TRADE_FIELDS - fields
            if missing:
                raise ForwardEvidenceError(
                    f"Trade log {path} is missing fields: {', '.join(sorted(missing))}"
                )
            rows = list(reader)
    except (OSError, csv.Error, UnicodeError) as exc:
        raise ForwardEvidenceError(f"Unable to read trade log {path}: {exc}") from exc

    for row in rows:
        if row.get("status", "").strip().lower() != "closed":
            raise ForwardEvidenceError(f"Trade log {path} contains non-closed trade row")
    return rows


def _to_float(row: Dict[str, Any], field: str, path: Path) -> float:
    try:
        value = float(row.get(field, ""))
    except (TypeError, ValueError) as exc:
        raise ForwardEvidenceError(f"Invalid {field} in {path}") from exc
    if not (value == value and abs(value) != float("inf")):
        raise ForwardEvidenceError(f"Non-finite {field} in {path}")
    return value


def _to_time(row: Dict[str, Any], field: str, path: Path) -> float:
    value = _to_float(row, field, path)
    if value <= 0:
        raise ForwardEvidenceError(f"Invalid {field} timestamp in {path}")
    return value


def _validate_trade_economics(rows: Iterable[Dict[str, Any]]) -> None:
    """Require an explicit gross-to-net economic decomposition for every trade."""
    for row in rows:
        path = Path("<aggregate>")
        entry_time = _to_time(row, "entry_time", path)
        exit_time = _to_time(row, "exit_time", path)
        if exit_time < entry_time:
            raise ForwardEvidenceError("Trade exit time precedes entry time")

        gross = _to_float(row, "gross_pnl", path)
        net = _to_float(row, "net_pnl", path)
        fees = _to_float(row, "fees", path)
        funding = _to_float(row, "funding_pnl", path)
        slippage = _to_float(row, "total_slippage", path)
        quantity = _to_float(row, "quantity", path)

        if fees < 0 or quantity < 0 or slippage < 0:
            raise ForwardEvidenceError("Trade economic inputs contain a negative cost/quantity")
        expected_net = gross - fees + funding
        if abs(net - expected_net) > ECONOMIC_EPSILON:
            raise ForwardEvidenceError(
                "Trade economic decomposition failed: net PnL does not reconcile"
            )

def _max_drawdown(rows: Iterable[Dict[str, Any]]) -> float:
    equity = 10_000.0
    peak = equity
    max_dd = 0.0
    for row in sorted(rows, key=lambda r: _to_time(r, "exit_time", Path("<aggregate>"))):
        equity += float(row["net_pnl"])
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak * 100.0)
    return max_dd


def aggregate_forward_evidence(
    *,
    artifact_root: Path,
    output_path: Path | None = None,
) -> Dict[str, Any]:
    """Verify C/D bundles and return an integrity-bound aggregate report."""
    c_path = artifact_root / "session_C.json"
    d_path = artifact_root / "session_D.json"
    c = _verify_session_artifact(c_path, "C")
    d = _verify_session_artifact(d_path, "D")

    for artifact in (c, d):
        if artifact.get("data_source") != "BINANCE_FUTURES_PRODUCTION":
            raise ForwardEvidenceError("Forward evidence must use production Binance market data")
        if artifact.get("market_data_mode") != "PRODUCTION":
            raise ForwardEvidenceError("Forward evidence is not marked PRODUCTION")
        if artifact.get("execution_mode") != "SIMULATION" or artifact.get("real_orders") is not False:
            raise ForwardEvidenceError("Forward evidence must be simulation-only")

    identity_fields = ("code_commit_sha", "parameter_hash")
    for field in identity_fields:
        if c.get(field) != d.get(field):
            raise ForwardEvidenceError(f"C/D {field} mismatch")

    c_end = float(c.get("ended_at_epoch", 0))
    d_start = float(d.get("started_at_epoch", 0))
    if c_end <= 0 or d_start <= 0 or d_start < c_end:
        raise ForwardEvidenceError("Session D must start after Session C completed")

    c_files = _verify_bundle_files(c, data_root=artifact_root.parent)
    d_files = _verify_bundle_files(d, data_root=artifact_root.parent)

    for artifact, label, files in ((c, "C", c_files), (d, "D", d_files)):
        bundled_summary = _read_canonical_summary(files["summary"])
        if _canonical_json(bundled_summary) != _canonical_json(artifact["summary"]):
            raise ForwardEvidenceError(
                f"Session {label} bundled summary does not match immutable session summary"
            )
        bundled_signal_count = _read_signal_count(files["signals"])
        if bundled_signal_count != int(artifact["signal_count"]):
            raise ForwardEvidenceError(
                f"Session {label} signal count does not match its immutable signal log"
            )

    c_rows = _read_trades(c_files["trades"])
    d_rows = _read_trades(d_files["trades"])
    rows = c_rows + d_rows
    _validate_trade_economics(rows)

    trade_ids = [row.get("id", "") for row in rows]
    if any(not value for value in trade_ids):
        raise ForwardEvidenceError("Forward trade log contains an empty trade id")
    if len(set(trade_ids)) != len(trade_ids):
        raise ForwardEvidenceError("Duplicate trade id detected across C/D evidence")

    for artifact, label, rows_for_session in ((c, "C", c_rows), (d, "D", d_rows)):
        expected = artifact["closed_trade_count"]
        if int(expected) != len(rows_for_session):
            raise ForwardEvidenceError(
                f"Session {label} closed-trade count does not match its immutable trade log"
            )

    total_trades = len(rows)
    wins = sum(1 for row in rows if _to_float(row, "net_pnl", Path("<aggregate>")) > 0)
    gross_profit = sum(
        max(0.0, _to_float(row, "net_pnl", Path("<aggregate>"))) for row in rows
    )
    gross_loss = abs(
        sum(min(0.0, _to_float(row, "net_pnl", Path("<aggregate>"))) for row in rows)
    )
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    net_pnl = sum(_to_float(row, "net_pnl", Path("<aggregate>")) for row in rows)

    report = {
        "schema_version": 1,
        "status": "EVIDENCE_VALID",
        "authorization": "NOT_A_LIVE_CERTIFICATION",
        "sessions": ["C", "D"],
        "code_commit_sha": c["code_commit_sha"],
        "parameter_hash": c["parameter_hash"],
        "total_signals": int(c["signal_count"]) + int(d["signal_count"]),
        "total_closed_trades": total_trades,
        "win_rate": wins / total_trades if total_trades else 0.0,
        "profit_factor": profit_factor,
        "expectancy": net_pnl / total_trades if total_trades else 0.0,
        "net_pnl": net_pnl,
        "total_gross_pnl": sum(_to_float(row, "gross_pnl", Path("<aggregate>")) for row in rows),
        "total_fees": sum(_to_float(row, "fees", Path("<aggregate>")) for row in rows),
        "total_funding_pnl": sum(_to_float(row, "funding_pnl", Path("<aggregate>")) for row in rows),
        "total_slippage": sum(
            _to_float(row, "total_slippage", Path("<aggregate>"))
            * _to_float(row, "quantity", Path("<aggregate>"))
            for row in rows
        ),
        "max_drawdown_pct": _max_drawdown(rows),
        "session_c_trade_count": len(c_rows),
        "session_d_trade_count": len(d_rows),
        "bundle_roots": {
            "C": c["evidence_bundle"]["root"],
            "D": d["evidence_bundle"]["root"],
        },
    }

    report["aggregate_sha256"] = _sha256_bytes(_canonical_json(report).encode("utf-8"))
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    return report


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent / "data" / "reports" / "forward_sessions"
    report = aggregate_forward_evidence(
        artifact_root=root,
        output_path=root / "forward_aggregate.json",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
