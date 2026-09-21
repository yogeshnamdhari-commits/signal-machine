"""Tests for the controlled forward-session evidence guard."""

import hashlib
import json

import pytest

from app_layer import forward_session_guard as guard


def _bundle(session="C"):
    return {
        "schema_version": 1,
        "session": session,
        "root": f"forward_sessions/session_{session}_evidence",
        "artifacts": {
            "trades": {"path": f"forward_sessions/session_{session}_evidence/paper_trading_trades.csv", "sha256": "a" * 64, "bytes": 1},
            "signals": {"path": f"forward_sessions/session_{session}_evidence/paper_trading_signals.csv", "sha256": "b" * 64, "bytes": 1},
            "summary": {"path": f"forward_sessions/session_{session}_evidence/summary.canonical.json", "sha256": "c" * 64, "bytes": 1},
        },
    }


def _fake_freeze(monkeypatch, commit="commit-1", param_hash="param-1"):
    monkeypatch.setattr(
        guard.ParameterFreeze,
        "check",
        lambda self: {
            "frozen": True,
            "clean": True,
            "code_commit_sha": commit,
            "hash": param_hash,
            "frozen_at": "2026-09-18T00:00:00+00:00",
        },
    )


def _materialize_bundle(tmp_path, session="C"):
    bundle_root = tmp_path / "forward_sessions" / f"session_{session}_evidence"
    bundle_root.mkdir(parents=True)
    payloads = {
        "trades": b"id,signal_id,symbol\nT-1,S-1,BTCUSDT\n",
        "signals": b"id,symbol\nS-1,BTCUSDT\n",
        "summary": b"{\"total_signals\":19,\"total_trades\":7}",
    }
    artifacts = {}
    for name, payload in payloads.items():
        filename = {
            "trades": "paper_trading_trades.csv",
            "signals": "paper_trading_signals.csv",
            "summary": "summary.canonical.json",
        }[name]
        path = bundle_root / filename
        path.write_bytes(payload)
        artifacts[name] = {
            "path": str(path.relative_to(tmp_path)),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
    bundle = _bundle(session)
    bundle["artifacts"] = artifacts
    return bundle


def test_forward_session_c_requires_production_and_freeze(monkeypatch, tmp_path):
    _fake_freeze(monkeypatch)
    with pytest.raises(guard.ForwardSessionError):
        guard.ForwardSessionGuard.prepare(
            "C", production_data=False, artifact_root=tmp_path
        )
    record = guard.ForwardSessionGuard.prepare(
        "C", production_data=True, artifact_root=tmp_path
    )
    assert record["session"] == "C"
    assert record["code_commit_sha"] == "commit-1"
    assert record["parameter_hash"] == "param-1"
    assert record["real_orders"] is False
    assert (tmp_path / "session_C.json").exists()


def test_forward_session_rejects_dirty_freeze(monkeypatch, tmp_path):
    monkeypatch.setattr(
        guard.ParameterFreeze,
        "check",
        lambda self: {"frozen": True, "clean": False, "reason": "Source commit changed"},
    )
    with pytest.raises(guard.ForwardSessionError, match="Source commit changed"):
        guard.ForwardSessionGuard.prepare(
            "C", production_data=True, artifact_root=tmp_path
        )


def test_forward_session_d_requires_completed_c(monkeypatch, tmp_path):
    _fake_freeze(monkeypatch)
    artifact_root = tmp_path / "forward_sessions"
    artifact_root.mkdir()
    with pytest.raises(guard.ForwardSessionError, match="completed Session C"):
        guard.ForwardSessionGuard.prepare(
            "D", production_data=True, artifact_root=tmp_path
        )

    c_base = {
        "schema_version": 1,
        "session": "C",
        "status": "COMPLETED",
        "code_commit_sha": "commit-1",
        "parameter_hash": "param-1",
        "summary": {"total_trades": 7, "total_signals": 19},
        "evidence_bundle": _materialize_bundle(tmp_path, "C"),
        "summary_sha256": "",
        "provenance_sha256": "",
    }
    c_base["summary_sha256"] = hashlib.sha256(
        json.dumps(c_base["summary"], sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    c_base["provenance_sha256"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in c_base.items() if k != "provenance_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    (artifact_root / "session_C.json").write_text(json.dumps(c_base), encoding="utf-8")

    d = guard.ForwardSessionGuard.prepare(
        "D", production_data=True, artifact_root=artifact_root
    )
    assert d["session"] == "D"
    assert d["code_commit_sha"] == "commit-1"


def test_completed_session_cannot_be_overwritten(monkeypatch, tmp_path):
    _fake_freeze(monkeypatch)
    completed = {
        "schema_version": 1,
        "session": "C",
        "status": "COMPLETED",
        "code_commit_sha": "commit-1",
        "parameter_hash": "param-1",
    }
    (tmp_path / "session_C.json").write_text(json.dumps(completed), encoding="utf-8")
    with pytest.raises(guard.ForwardSessionError, match="already completed"):
        guard.ForwardSessionGuard.prepare(
            "C", production_data=True, artifact_root=tmp_path
        )


def test_finalize_records_observation_counts_and_summary_hash(monkeypatch, tmp_path):
    _fake_freeze(monkeypatch)
    provenance = guard.ForwardSessionGuard.prepare(
        "C", production_data=True, artifact_root=tmp_path
    )
    summary = {"total_trades": 7, "total_signals": 19}
    final = guard.ForwardSessionGuard.finalize(
        provenance,
        summary,
        artifact_root=tmp_path,
        evidence_bundle=_bundle("C"),
    )
    assert final["status"] == "COMPLETED"
    assert final["closed_trade_count"] == 7
    assert final["signal_count"] == 19
    assert len(final["summary_sha256"]) == 64
    assert final["summary"] == summary
    saved = json.loads((tmp_path / "session_C.json").read_text(encoding="utf-8"))
    assert saved["provenance_sha256"] == final["provenance_sha256"]
    assert saved["summary"] == summary
    assert saved["summary_sha256"] == final["summary_sha256"]


def test_forward_session_d_rejects_tampered_completed_c(monkeypatch, tmp_path):
    _fake_freeze(monkeypatch)
    c = {
        "schema_version": 1,
        "session": "C",
        "status": "COMPLETED",
        "code_commit_sha": "commit-1",
        "parameter_hash": "param-1",
        "summary": {"total_trades": 7, "total_signals": 19},
        "evidence_bundle": _bundle("C"),
        "summary_sha256": hashlib.sha256(
            json.dumps(
                {"total_trades": 7, "total_signals": 19},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "provenance_sha256": "tampered",
    }
    (tmp_path / "session_C.json").write_text(json.dumps(c), encoding="utf-8")

    with pytest.raises(guard.ForwardSessionError, match="provenance integrity check failed"):
        guard.ForwardSessionGuard.prepare(
            "D", production_data=True, artifact_root=tmp_path
        )


def test_forward_session_d_rejects_tampered_completed_c_summary(monkeypatch, tmp_path):
    _fake_freeze(monkeypatch)
    c = {
        "schema_version": 1,
        "session": "C",
        "status": "COMPLETED",
        "code_commit_sha": "commit-1",
        "parameter_hash": "param-1",
        "summary": {"total_trades": 7, "total_signals": 19},
        "evidence_bundle": _bundle("C"),
        "summary_sha256": hashlib.sha256(
            json.dumps(
                {"total_trades": 6, "total_signals": 19},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "provenance_sha256": "",
    }
    c["provenance_sha256"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in c.items() if k != "provenance_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    (tmp_path / "session_C.json").write_text(json.dumps(c), encoding="utf-8")

    with pytest.raises(guard.ForwardSessionError, match="summary integrity check failed"):
        guard.ForwardSessionGuard.prepare(
            "D", production_data=True, artifact_root=tmp_path
        )



def test_finalize_requires_evidence_bundle(monkeypatch, tmp_path):
    _fake_freeze(monkeypatch)
    provenance = guard.ForwardSessionGuard.prepare(
        "C", production_data=True, artifact_root=tmp_path
    )

    with pytest.raises(guard.ForwardSessionError, match="requires an immutable evidence bundle"):
        guard.ForwardSessionGuard.finalize(
            provenance,
            {"total_trades": 1, "total_signals": 1},
            artifact_root=tmp_path,
            evidence_bundle=None,
        )


def test_session_d_rejects_completed_c_without_evidence_bundle(monkeypatch, tmp_path):
    _fake_freeze(monkeypatch)
    c = {
        "schema_version": 1,
        "session": "C",
        "status": "COMPLETED",
        "code_commit_sha": "commit-1",
        "parameter_hash": "param-1",
        "summary": {"total_trades": 1, "total_signals": 1},
        "summary_sha256": hashlib.sha256(
            json.dumps(
                {"total_trades": 1, "total_signals": 1},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "provenance_sha256": "",
    }
    c["provenance_sha256"] = hashlib.sha256(
        json.dumps(
            {k: v for k, v in c.items() if k != "provenance_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    (tmp_path / "session_C.json").write_text(json.dumps(c), encoding="utf-8")

    with pytest.raises(guard.ForwardSessionError, match="evidence bundle metadata is missing"):
        guard.ForwardSessionGuard.prepare("D", production_data=True, artifact_root=tmp_path)


def test_session_d_rejects_tampered_completed_c_bundle_file(monkeypatch, tmp_path):
    _fake_freeze(monkeypatch)
    artifact_root = tmp_path / "forward_sessions"
    artifact_root.mkdir()
    bundle = _materialize_bundle(tmp_path, "C")
    c = {
        "schema_version": 1,
        "session": "C",
        "status": "COMPLETED",
        "code_commit_sha": "commit-1",
        "parameter_hash": "param-1",
        "summary": {"total_trades": 7, "total_signals": 19},
        "evidence_bundle": bundle,
        "summary_sha256": hashlib.sha256(
            json.dumps(
                {"total_trades": 7, "total_signals": 19},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "provenance_sha256": "",
    }
    c["provenance_sha256"] = hashlib.sha256(
        json.dumps({k: v for k, v in c.items() if k != "provenance_sha256"}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    (artifact_root / "session_C.json").write_text(json.dumps(c), encoding="utf-8")
    (tmp_path / "forward_sessions" / "session_C_evidence" / "paper_trading_trades.csv").write_bytes(b"tampered")

    with pytest.raises(guard.ForwardSessionError, match="hash mismatch"):
        guard.ForwardSessionGuard.prepare("D", production_data=True, artifact_root=artifact_root)


def test_session_d_rejects_bundle_path_escape(monkeypatch, tmp_path):
    _fake_freeze(monkeypatch)
    artifact_root = tmp_path / "forward_sessions"
    artifact_root.mkdir()
    bundle = _materialize_bundle(tmp_path, "C")
    bundle["artifacts"]["trades"]["path"] = "../outside.csv"
    c = {
        "schema_version": 1,
        "session": "C",
        "status": "COMPLETED",
        "code_commit_sha": "commit-1",
        "parameter_hash": "param-1",
        "summary": {"total_trades": 7, "total_signals": 19},
        "evidence_bundle": bundle,
        "summary_sha256": hashlib.sha256(
            json.dumps({"total_trades": 7, "total_signals": 19}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "provenance_sha256": "",
    }
    c["provenance_sha256"] = hashlib.sha256(
        json.dumps({k: v for k, v in c.items() if k != "provenance_sha256"}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    (artifact_root / "session_C.json").write_text(json.dumps(c), encoding="utf-8")

    with pytest.raises(guard.ForwardSessionError, match="escapes its declared bundle root"):
        guard.ForwardSessionGuard.prepare("D", production_data=True, artifact_root=artifact_root)


def test_session_d_rejects_symlinked_bundle_root(monkeypatch, tmp_path):
    _fake_freeze(monkeypatch)
    artifact_root = tmp_path / "forward_sessions"
    artifact_root.mkdir()
    bundle = _materialize_bundle(tmp_path, "C")
    real_root = tmp_path / "forward_sessions" / "session_C_evidence_real"
    symlink_root = tmp_path / "forward_sessions" / "session_C_evidence"
    (tmp_path / "forward_sessions" / "session_C_evidence").rename(real_root)
    symlink_root.symlink_to(real_root, target_is_directory=True)
    bundle["root"] = str(symlink_root.relative_to(tmp_path))

    c = {
        "schema_version": 1,
        "session": "C",
        "status": "COMPLETED",
        "code_commit_sha": "commit-1",
        "parameter_hash": "param-1",
        "summary": {"total_trades": 7, "total_signals": 19},
        "evidence_bundle": bundle,
        "summary_sha256": hashlib.sha256(
            json.dumps({"total_trades": 7, "total_signals": 19}, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "provenance_sha256": "",
    }
    c["provenance_sha256"] = hashlib.sha256(
        json.dumps({k: v for k, v in c.items() if k != "provenance_sha256"}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    (artifact_root / "session_C.json").write_text(json.dumps(c), encoding="utf-8")

    with pytest.raises(guard.ForwardSessionError, match="must not be a symlink"):
        guard.ForwardSessionGuard.prepare("D", production_data=True, artifact_root=artifact_root)


def test_session_d_upgrades_legacy_completed_c_without_rerun(monkeypatch, tmp_path):
    _fake_freeze(monkeypatch)
    artifact_root = tmp_path / "forward_sessions"
    artifact_root.mkdir()
    summary = {"total_trades": 1, "total_signals": 1}
    (tmp_path / "paper_trading_summary.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )
    (tmp_path / "paper_trading_trades.csv").write_text(
        "id,signal_id,symbol,side,entry_time,exit_time,gross_pnl,net_pnl,quantity,fees,funding_pnl,total_slippage,status\n"
        "T-1,S-1,BTCUSDT,LONG,1001,1999,10,10,1,0,0,0.01,closed\n",
        encoding="utf-8",
    )
    (tmp_path / "paper_trading_signals.csv").write_text(
        "id,timestamp,symbol,side,entry_price,stop_loss,take_profit,status\n"
        "S-1,1000,BTCUSDT,LONG,100,99,102,generated\n",
        encoding="utf-8",
    )

    legacy = {
        "schema_version": 1,
        "session": "C",
        "status": "COMPLETED",
                "started_at_epoch": 1000,
"ended_at_epoch": 2000,
        "code_commit_sha": "commit-1",
        "parameter_hash": "param-1",
        "data_source": "BINANCE_FUTURES_PRODUCTION",
        "market_data_mode": "PRODUCTION",
        "execution_mode": "SIMULATION",
        "real_orders": False,
        "closed_trade_count": 1,
        "signal_count": 1,
        "summary_sha256": hashlib.sha256(
            json.dumps(summary, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "provenance_sha256": "",
    }
    legacy["provenance_sha256"] = hashlib.sha256(
        json.dumps({k: v for k, v in legacy.items() if k != "provenance_sha256"},
                   sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    legacy_path = artifact_root / "session_C.json"
    legacy_path.write_text(json.dumps(legacy), encoding="utf-8")
    original_bytes = legacy_path.read_bytes()

    import os

    for path in (
        tmp_path / "paper_trading_summary.json",
        tmp_path / "paper_trading_trades.csv",
        tmp_path / "paper_trading_signals.csv",
    ):
        os.utime(path, (1999, 1999))

    d = guard.ForwardSessionGuard.prepare(
        "D", production_data=True, artifact_root=artifact_root
    )

    upgraded = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert d["session"] == "D"
    assert upgraded["summary"] == summary
    assert "evidence_bundle" in upgraded
    assert upgraded["legacy_upgrade"]["source_artifact_sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert (artifact_root / "session_C.legacy.json").read_bytes() == original_bytes
    assert (tmp_path / "forward_sessions" / "session_C_evidence" / "paper_trading_trades.csv").exists()


def test_session_d_rejects_legacy_c_exports_outside_session_window(monkeypatch, tmp_path):
    _fake_freeze(monkeypatch)
    artifact_root = tmp_path / "forward_sessions"
    artifact_root.mkdir()
    summary = {"total_trades": 1, "total_signals": 1}
    for name, payload in {
        "paper_trading_summary.json": json.dumps(summary),
        "paper_trading_trades.csv": (
            "id,signal_id,symbol,side,entry_time,exit_time,gross_pnl,net_pnl,quantity,fees,funding_pnl,total_slippage,status\n"
            "T-1,S-1,BTCUSDT,LONG,1001,1999,10,10,1,0,0,0.01,closed\n"
        ),
        "paper_trading_signals.csv": (
            "id,timestamp,symbol,side,entry_price,stop_loss,take_profit,status\n"
            "S-1,1000,BTCUSDT,LONG,100,99,102,generated\n"
        ),
    }.items():
        (tmp_path / name).write_text(payload, encoding="utf-8")

    legacy = {
        "schema_version": 1,
        "session": "C",
        "status": "COMPLETED",
        "started_at_epoch": 1000,
        "ended_at_epoch": 2000,
        "code_commit_sha": "commit-1",
        "parameter_hash": "param-1",
        "closed_trade_count": 1,
        "signal_count": 1,
        "summary_sha256": hashlib.sha256(
            json.dumps(summary, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
    }
    legacy["provenance_sha256"] = hashlib.sha256(
        json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    (artifact_root / "session_C.json").write_text(json.dumps(legacy), encoding="utf-8")

    with pytest.raises(guard.ForwardSessionError, match="timestamp is outside"):
        guard.ForwardSessionGuard.prepare("D", production_data=True, artifact_root=artifact_root)


def test_finalize_session_d_materializes_c_d_aggregate(monkeypatch, tmp_path):
    _fake_freeze(monkeypatch)
    artifact_root = tmp_path / "forward_sessions"
    artifact_root.mkdir()
    c_bundle = _materialize_bundle(tmp_path, "C")
    c_summary = {"total_trades": 7, "total_signals": 19}
    c = {
        "schema_version": 1,
        "session": "C",
        "status": "COMPLETED",
        "code_commit_sha": "commit-1",
        "parameter_hash": "param-1",
        "summary": c_summary,
        "evidence_bundle": c_bundle,
        "summary_sha256": hashlib.sha256(
            json.dumps(c_summary, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "provenance_sha256": "",
    }
    c["provenance_sha256"] = hashlib.sha256(
        json.dumps({k: v for k, v in c.items() if k != "provenance_sha256"}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    (artifact_root / "session_C.json").write_text(json.dumps(c), encoding="utf-8")

    provenance = guard.ForwardSessionGuard.prepare("D", production_data=True, artifact_root=artifact_root)
    d_bundle = _materialize_bundle(tmp_path, "D")
    calls = []

    import app_layer.forward_evidence_aggregator as aggregator

    def fake_aggregate(*, artifact_root, output_path):
        calls.append((artifact_root, output_path))
        return {"status": "EVIDENCE_VALID"}

    monkeypatch.setattr(aggregator, "aggregate_forward_evidence", fake_aggregate)
    final = guard.ForwardSessionGuard.finalize(
        provenance,
        c_summary,
        artifact_root=artifact_root,
        evidence_bundle=d_bundle,
    )

    assert final["status"] == "COMPLETED"
    assert calls == [(artifact_root, artifact_root / "forward_aggregate.json")]
