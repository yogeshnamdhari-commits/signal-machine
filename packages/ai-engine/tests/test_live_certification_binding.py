import hashlib
import json

import pytest

from config import config
from config.schema import config_fingerprint
from validation import live_gate
from validation.live_gate import LiveCertificationError, verify_live_certification




def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _write_forward_evidence(tmp_path, commit="commit-a"):
    report = {
        "schema_version": 1,
        "status": "EVIDENCE_VALID",
        "authorization": "NOT_A_LIVE_CERTIFICATION",
        "sessions": ["C", "D"],
        "code_commit_sha": commit,
        "parameter_hash": "param-1",
        "total_signals": 500,
        "total_closed_trades": 100,
        "win_rate": 0.55,
        "profit_factor": 1.50,
        "expectancy": 1.25,
        "net_pnl": 125.0,
        "total_gross_pnl": 200.0,
        "total_fees": 50.0,
        "total_funding_pnl": -5.0,
        "total_slippage": 20.0,
        "max_drawdown_pct": 8.0,
        "session_c_trade_count": 50,
        "session_d_trade_count": 50,
        "bundle_roots": {
            "C": "session_C_evidence",
            "D": "session_D_evidence",
        },
    }
    session_manifests = {}
    evidence_manifests = {}
    for session in ("C", "D"):
        bundle_root = tmp_path / f"session_{session}_evidence"
        bundle_root.mkdir(exist_ok=True)
        evidence_manifests[session] = {}
        for name, payload in {
            "trades": b"id,signal_id\nT-1,S-1\n",
            "signals": b"id\nS-1\n",
            "summary": b"{}",
        }.items():
            filename = {
                "trades": "paper_trading_trades.csv",
                "signals": "paper_trading_signals.csv",
                "summary": "summary.canonical.json",
            }[name]
            path = bundle_root / filename
            path.write_bytes(payload)
            evidence_manifests[session][name] = {
                "path": str(path.relative_to(tmp_path)),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }
        artifact = {
            "schema_version": 1,
            "session": session,
            "status": "COMPLETED",
            "code_commit_sha": commit,
            "parameter_hash": "param-1",
        }
        artifact_path = tmp_path / f"session_{session}.json"
        artifact_payload = json.dumps(artifact, sort_keys=True).encode("utf-8")
        artifact_path.write_bytes(artifact_payload)
        session_manifests[session] = {
            "path": artifact_path.name,
            "sha256": hashlib.sha256(artifact_payload).hexdigest(),
            "bytes": len(artifact_payload),
        }
    report["session_artifacts"] = session_manifests
    report["evidence_files"] = evidence_manifests
    report["artifact_root"] = "."
    report["aggregate_sha256"] = hashlib.sha256(
        _canonical(report).encode("utf-8")
    ).hexdigest()
    path = tmp_path / "forward_aggregate.json"
    payload = json.dumps(report, indent=2, sort_keys=True).encode("utf-8")
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()

def _write_artifact(tmp_path, **overrides):
    evidence_path, evidence_sha = _write_forward_evidence(tmp_path)
    live_gate.DEFAULT_FORWARD_EVIDENCE_PATH = evidence_path
    artifact = {
        "state": "LIVE_ELIGIBLE",
        "commit_sha": "commit-a",
        "configuration_fingerprint": config_fingerprint(config),
        "failures": [],
        "issued_at": 1000.0,
        "expires_at": 2000.0,
        "forward_evidence_sha256": evidence_sha,
    }
    artifact.update(overrides)
    path = tmp_path / "certification.current.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return path


def _trusted_commit(monkeypatch, value="commit-a", evidence_path=None):
    monkeypatch.setattr(live_gate, "_current_commit_sha", lambda: value)
    if evidence_path is not None:
        monkeypatch.setattr(live_gate, "DEFAULT_FORWARD_EVIDENCE_PATH", evidence_path)


def test_live_certification_rejects_source_commit_mismatch(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path)
    _trusted_commit(monkeypatch, "commit-b")
    monkeypatch.setenv("GITHUB_SHA", "commit-a")

    with pytest.raises(LiveCertificationError, match="Certification commit does not match"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_ignores_caller_supplied_commit_env(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path)
    _trusted_commit(monkeypatch, "commit-a")
    monkeypatch.setenv("GITHUB_SHA", "attacker-controlled")
    monkeypatch.setenv("LIVE_CERT_COMMIT", "attacker-controlled")

    artifact = verify_live_certification(path, now_ts=1500.0)
    assert artifact["commit_sha"] == "commit-a"


def test_live_certification_rejects_unavailable_source_commit(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path)
    _trusted_commit(monkeypatch, "")

    with pytest.raises(LiveCertificationError, match="source commit is unavailable"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_rejects_embedded_failures(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path, failures=["missing_forward_evidence"])
    _trusted_commit(monkeypatch)

    with pytest.raises(LiveCertificationError, match="contains failures"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_rejects_missing_freshness_metadata(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path)
    artifact = json.loads(path.read_text(encoding="utf-8"))
    artifact.pop("issued_at")
    path.write_text(json.dumps(artifact), encoding="utf-8")
    _trusted_commit(monkeypatch)

    with pytest.raises(LiveCertificationError, match="freshness metadata is missing"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_rejects_expired_artifact(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path, issued_at=1000.0, expires_at=1500.0)
    _trusted_commit(monkeypatch)

    with pytest.raises(LiveCertificationError, match="has expired"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_rejects_not_yet_valid_artifact(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path, issued_at=1600.0, expires_at=2000.0)
    _trusted_commit(monkeypatch)

    with pytest.raises(LiveCertificationError, match="not yet valid"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_rejects_zero_length_freshness_window(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path, issued_at=1500.0, expires_at=1500.0)
    _trusted_commit(monkeypatch)

    with pytest.raises(LiveCertificationError, match="freshness window is invalid"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_rejects_non_finite_freshness_metadata(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path, issued_at=float("nan"), expires_at=2000.0)
    _trusted_commit(monkeypatch)

    with pytest.raises(LiveCertificationError, match="freshness metadata is invalid"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_rejects_non_finite_verification_timestamp(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path)
    _trusted_commit(monkeypatch)

    with pytest.raises(LiveCertificationError, match="verification timestamp is invalid"):
        verify_live_certification(path, now_ts=float("inf"))


def test_live_certification_rejects_boolean_freshness_metadata(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path, issued_at=True, expires_at=2000.0)
    _trusted_commit(monkeypatch)

    with pytest.raises(LiveCertificationError, match="freshness metadata is missing"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_rejects_boolean_verification_timestamp(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path)
    _trusted_commit(monkeypatch)

    with pytest.raises(LiveCertificationError, match="verification timestamp is invalid"):
        verify_live_certification(path, now_ts=True)


def test_live_certification_rejects_missing_forward_evidence_binding(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path)
    artifact = json.loads(path.read_text(encoding="utf-8"))
    artifact.pop("forward_evidence_sha256")
    path.write_text(json.dumps(artifact), encoding="utf-8")
    evidence_path, _ = _write_forward_evidence(tmp_path)
    _trusted_commit(monkeypatch, evidence_path=evidence_path)

    with pytest.raises(LiveCertificationError, match="not bound to immutable C/D"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_rejects_forward_evidence_hash_mismatch(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path)
    artifact = json.loads(path.read_text(encoding="utf-8"))
    artifact["forward_evidence_sha256"] = "0" * 64
    path.write_text(json.dumps(artifact), encoding="utf-8")
    evidence_path, _ = _write_forward_evidence(tmp_path)
    _trusted_commit(monkeypatch, evidence_path=evidence_path)

    with pytest.raises(LiveCertificationError, match="hash does not match certification"):
        verify_live_certification(path, now_ts=1500.0)
