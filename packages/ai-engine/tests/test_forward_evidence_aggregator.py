import csv
import hashlib
import json
from pathlib import Path

import pytest

from app_layer.forward_evidence_aggregator import (
    ForwardEvidenceError,
    aggregate_forward_evidence,
)


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _write_session(root: Path, session: str, start: float, end: float, trade_id: str, pnl: float):
    evidence_dir = root / f"session_{session}_evidence"
    evidence_dir.mkdir(parents=True)

    trades_path = evidence_dir / "paper_trading_trades.csv"
    with trades_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "id", "signal_id", "symbol", "side", "entry_time", "exit_time",
                "gross_pnl", "net_pnl", "quantity", "fees", "funding_pnl",
                "total_slippage", "status",
            ],
        )
        writer.writeheader()
        writer.writerow({
            "id": trade_id,
            "signal_id": f"sig-{trade_id}",
            "symbol": "BTCUSDT",
            "side": "LONG",
            "entry_time": start + 1,
            "exit_time": end - 1,
            "gross_pnl": pnl,
            "net_pnl": pnl,
            "quantity": 1,
            "fees": 0,
            "funding_pnl": 0,
            "total_slippage": 0.01,
            "status": "closed",
        })

    signals_path = evidence_dir / "paper_trading_signals.csv"
    signals_path.write_text("id,symbol\nsig, BTCUSDT\n", encoding="utf-8")
    summary = {"total_trades": 1, "total_signals": 1}
    summary_path = evidence_dir / "summary.canonical.json"
    summary_bytes = _canonical(summary).encode("utf-8")
    summary_path.write_bytes(summary_bytes)

    artifacts = {}
    for name, path in {
        "trades": trades_path,
        "signals": signals_path,
        "summary": summary_path,
    }.items():
        payload = path.read_bytes()
        artifacts[name] = {
            "path": str(path.relative_to(root.parent)),
            "sha256": _sha(payload),
            "bytes": len(payload),
        }

    artifact = {
        "schema_version": 1,
        "session": session,
        "status": "COMPLETED",
        "started_at_epoch": start,
        "ended_at_epoch": end,
        "code_commit_sha": "commit-1",
        "parameter_hash": "param-1",
        "data_source": "BINANCE_FUTURES_PRODUCTION",
        "market_data_mode": "PRODUCTION",
        "execution_mode": "SIMULATION",
        "real_orders": False,
        "closed_trade_count": 1,
        "signal_count": 1,
        "summary": summary,
        "summary_sha256": _sha(_canonical(summary).encode("utf-8")),
        "evidence_bundle": {
            "schema_version": 1,
            "session": session,
            "root": str(evidence_dir.relative_to(root.parent)),
            "artifacts": artifacts,
        },
    }
    artifact["provenance_sha256"] = _sha(
        _canonical({k: v for k, v in artifact.items() if k != "provenance_sha256"}).encode("utf-8")
    )
    (root / f"session_{session}.json").write_text(
        json.dumps(artifact), encoding="utf-8"
    )


def test_aggregator_verifies_and_combines_c_and_d(tmp_path):
    root = tmp_path / "forward_sessions"
    root.mkdir()
    _write_session(root, "C", 1000, 2000, "C-1", 10)
    _write_session(root, "D", 2000, 3000, "D-1", -2)

    report = aggregate_forward_evidence(artifact_root=root)

    assert report["status"] == "EVIDENCE_VALID"
    assert report["authorization"] == "NOT_A_LIVE_CERTIFICATION"
    assert report["total_closed_trades"] == 2
    assert report["net_pnl"] == 8
    assert report["profit_factor"] == 5
    assert report["total_slippage"] == 0.02


def test_aggregator_rejects_tampered_trade_bundle(tmp_path):
    root = tmp_path / "forward_sessions"
    root.mkdir()
    _write_session(root, "C", 1000, 2000, "C-1", 10)
    _write_session(root, "D", 2000, 3000, "D-1", -2)

    trade_file = root / "session_C_evidence" / "paper_trading_trades.csv"
    trade_file.write_text(trade_file.read_text(encoding="utf-8").replace(",10,", ",99,"), encoding="utf-8")

    with pytest.raises(ForwardEvidenceError, match="hash mismatch"):
        aggregate_forward_evidence(artifact_root=root)


def test_aggregator_rejects_overlapping_sessions(tmp_path):
    root = tmp_path / "forward_sessions"
    root.mkdir()
    _write_session(root, "C", 1000, 2000, "C-1", 10)
    _write_session(root, "D", 1999, 3000, "D-1", -2)

    with pytest.raises(ForwardEvidenceError, match="Session D must start after Session C completed"):
        aggregate_forward_evidence(artifact_root=root)



def test_aggregator_rejects_non_reconciled_net_pnl(tmp_path):
    root = tmp_path / "forward_sessions"
    root.mkdir()
    _write_session(root, "C", 1000, 2000, "C-1", 10)
    _write_session(root, "D", 2000, 3000, "D-1", -2)

    trade_file = root / "session_C_evidence" / "paper_trading_trades.csv"
    text_value = trade_file.read_text(encoding="utf-8")
    lines = text_value.splitlines()
    # Preserve the indexed hash mismatch separately; this test targets economics.
    lines[-1] = lines[-1].replace(",10,1,0,0,0.01,closed", ",10.50,1,0,0,0.01,closed")
    trade_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    artifact = json.loads((root / "session_C.json").read_text(encoding="utf-8"))
    payload = trade_file.read_bytes()
    artifact["evidence_bundle"]["artifacts"]["trades"]["sha256"] = _sha(payload)
    artifact["evidence_bundle"]["artifacts"]["trades"]["bytes"] = len(payload)
    artifact["provenance_sha256"] = _sha(
        _canonical({k: v for k, v in artifact.items() if k != "provenance_sha256"}).encode("utf-8")
    )
    (root / "session_C.json").write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(ForwardEvidenceError, match="economic decomposition failed"):
        aggregate_forward_evidence(artifact_root=root)


def test_aggregator_rejects_summary_bundle_mismatch(tmp_path):
    root = tmp_path / "forward_sessions"
    root.mkdir()
    _write_session(root, "C", 1000, 2000, "C-1", 10)
    _write_session(root, "D", 2000, 3000, "D-1", -2)

    summary_file = root / "session_C_evidence" / "summary.canonical.json"
    summary_file.write_text(_canonical({"total_trades": 999, "total_signals": 1}), encoding="utf-8")
    artifact = json.loads((root / "session_C.json").read_text(encoding="utf-8"))
    payload = summary_file.read_bytes()
    artifact["evidence_bundle"]["artifacts"]["summary"]["sha256"] = _sha(payload)
    artifact["evidence_bundle"]["artifacts"]["summary"]["bytes"] = len(payload)
    artifact["provenance_sha256"] = _sha(
        _canonical({k: v for k, v in artifact.items() if k != "provenance_sha256"}).encode("utf-8")
    )
    (root / "session_C.json").write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(ForwardEvidenceError, match="bundled summary does not match"):
        aggregate_forward_evidence(artifact_root=root)


def test_aggregator_rejects_bundle_path_escape(tmp_path):
    root = tmp_path / "forward_sessions"
    root.mkdir()
    _write_session(root, "C", 1000, 2000, "C-1", 10)
    _write_session(root, "D", 2000, 3000, "D-1", -2)

    artifact = json.loads((root / "session_C.json").read_text(encoding="utf-8"))
    artifact["evidence_bundle"]["artifacts"]["trades"]["path"] = "../escape.csv"
    artifact["provenance_sha256"] = _sha(
        _canonical({k: v for k, v in artifact.items() if k != "provenance_sha256"}).encode("utf-8")
    )
    (root / "session_C.json").write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(ForwardEvidenceError, match="escapes the data root"):
        aggregate_forward_evidence(artifact_root=root)
