"""Tests for the controlled forward-session evidence guard."""

import hashlib
import json

import pytest

from app_layer import forward_session_guard as guard


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
    (tmp_path / "session_C.json").write_text(json.dumps(c_base), encoding="utf-8")

    d = guard.ForwardSessionGuard.prepare(
        "D", production_data=True, artifact_root=tmp_path
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
        provenance, summary, artifact_root=tmp_path
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
