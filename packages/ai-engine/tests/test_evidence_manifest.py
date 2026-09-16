import time

from config.schema import config_fingerprint
from validation.evidence_manifest import EvidenceManifest, verify_evidence_manifest
from validation.certification import CertificationState, generate_certification
from validation.statistical_validation import ResearchDecision


def _manifest(**overrides):
    values = {
        "commit_sha": "abc",
        "configuration_fingerprint": config_fingerprint({}),
        "issued_at": 1_000.0,
        "expires_at": 2_000.0,
        "dataset_fingerprint": "dataset-1",
    }
    values.update(overrides)
    return EvidenceManifest(**values)


def test_expired_evidence_is_rejected():
    decision = verify_evidence_manifest(_manifest(), expected_commit="abc", expected_config={}, now_ts=2_001.0)
    assert not decision.approved
    assert "evidence_expired" in decision.reasons


def test_commit_mismatch_is_rejected():
    decision = verify_evidence_manifest(_manifest(), expected_commit="def", expected_config={}, now_ts=1_500.0)
    assert not decision.approved
    assert "evidence_commit_mismatch" in decision.reasons


def test_config_mismatch_is_rejected():
    decision = verify_evidence_manifest(_manifest(), expected_commit="abc", expected_config={"risk": 1}, now_ts=1_500.0)
    assert not decision.approved
    assert "evidence_config_mismatch" in decision.reasons


def test_historical_manifest_cannot_reach_research_validated():
    decision = ResearchDecision(True, ())
    artifact = generate_certification(
        config={},
        commit_sha="abc",
        checks=("research",),
        failures=(),
        research_decision=decision,
        research_validated=True,
        evidence_manifest=_manifest(expires_at=900.0),
        evidence_now_ts=1_000.0,
    )
    assert artifact.state is CertificationState.ENGINEERING_VALID
    assert "evidence_expired" in artifact.failures
