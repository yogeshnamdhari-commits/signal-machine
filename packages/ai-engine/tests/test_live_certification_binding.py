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
    }
    artifact.update(overrides)
    path = tmp_path / "certification.current.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    return path


def test_live_certification_rejects_source_commit_mismatch(tmp_path, monkeypatch):
    path = _write_artifact(tmp_path)
    monkeypatch.setenv("GITHUB_SHA", "commit-b")

    with pytest.raises(LiveCertificationError, match="Certification commit does not match"):
        verify_live_certification(path)


def test_live_certification_rejects_embedded_failures(tmp_path):
    path = _write_artifact(tmp_path, failures=["missing_forward_evidence"])

    with pytest.raises(LiveCertificationError, match="contains failures"):
        verify_live_certification(path)
