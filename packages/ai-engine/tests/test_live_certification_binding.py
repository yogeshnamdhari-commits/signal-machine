import json

import pytest

from config import config
from config.schema import config_fingerprint
from validation.live_gate import LiveCertificationError, verify_live_certification


def _write_artifact(tmp_path, **overrides):
    artifact = {
        "state": "LIVE_ELIGIBLE",
        "commit_sha": "commit-a",
        "configuration_fingerprint": config_fingerprint(config),
        "failures": [],
        "issued_at": 1000.0,
        "expires_at": 2000.0,
    }
    artifact.update(overrides)
    path = tmp_path / "certification.current.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return path


def test_live_certification_rejects_source_commit_mismatch(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path)
    monkeypatch.setenv("GITHUB_SHA", "commit-b")

    with pytest.raises(LiveCertificationError, match="Certification commit does not match"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_rejects_embedded_failures(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path, failures=["missing_forward_evidence"])
    monkeypatch.setenv("GITHUB_SHA", "commit-a")

    with pytest.raises(LiveCertificationError, match="contains failures"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_rejects_missing_freshness_metadata(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path)
    artifact = json.loads(path.read_text(encoding="utf-8"))
    artifact.pop("issued_at")
    path.write_text(json.dumps(artifact), encoding="utf-8")
    monkeypatch.setenv("GITHUB_SHA", "commit-a")

    with pytest.raises(LiveCertificationError, match="freshness metadata is missing"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_rejects_expired_artifact(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path, issued_at=1000.0, expires_at=1500.0)
    monkeypatch.setenv("GITHUB_SHA", "commit-a")

    with pytest.raises(LiveCertificationError, match="has expired"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_rejects_not_yet_valid_artifact(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path, issued_at=1600.0, expires_at=2000.0)
    monkeypatch.setenv("GITHUB_SHA", "commit-a")

    with pytest.raises(LiveCertificationError, match="not yet valid"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_rejects_zero_length_freshness_window(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path, issued_at=1500.0, expires_at=1500.0)
    monkeypatch.setenv("GITHUB_SHA", "commit-a")

    with pytest.raises(LiveCertificationError, match="freshness window is invalid"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_rejects_non_finite_freshness_metadata(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path, issued_at=float("nan"), expires_at=2000.0)
    monkeypatch.setenv("GITHUB_SHA", "commit-a")

    with pytest.raises(LiveCertificationError, match="freshness metadata is invalid"):
        verify_live_certification(path, now_ts=1500.0)


def test_live_certification_rejects_non_finite_verification_timestamp(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path)
    monkeypatch.setenv("GITHUB_SHA", "commit-a")

    with pytest.raises(LiveCertificationError, match="verification timestamp is invalid"):
        verify_live_certification(path, now_ts=float("inf"))


def test_live_certification_rejects_boolean_freshness_metadata(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path, issued_at=True, expires_at=2000.0)
    monkeypatch.setenv("GITHUB_SHA", "commit-a")

    with pytest.raises(LiveCertificationError, match="freshness metadata is missing"):
        verify_live_certification(path, now_ts=1500.0)
