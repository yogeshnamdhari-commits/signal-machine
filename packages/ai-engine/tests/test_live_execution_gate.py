import json

import pytest

from config import config
from config.schema import config_fingerprint
from execution.live_execution_gate import require_live_certification
from validation.live_gate import LiveCertificationError, verify_live_certification


def test_live_execution_gate_is_fail_closed(monkeypatch):
    import execution.live_execution_gate as gate

    def deny():
        raise LiveCertificationError("no current matching certification")

    monkeypatch.setattr(gate, "verify_live_certification", deny)
    with pytest.raises(LiveCertificationError, match="live execution certification gate"):
        require_live_certification()


def test_missing_current_certification_artifact_is_rejected(tmp_path):
    with pytest.raises(LiveCertificationError, match="No current certification artifact exists"):
        verify_live_certification(tmp_path / "missing.json")


def test_non_live_certification_state_is_rejected(tmp_path):
    artifact_path = tmp_path / "certification.current.json"
    artifact_path.write_text(
        json.dumps(
            {
                "state": "ENGINEERING_VALID",
                "commit_sha": "verified-commit",
                "configuration_fingerprint": config_fingerprint(config),
                "failures": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(LiveCertificationError, match="not LIVE_ELIGIBLE"):
        verify_live_certification(artifact_path)
