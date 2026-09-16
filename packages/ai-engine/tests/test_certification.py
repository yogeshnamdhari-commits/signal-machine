from validation.certification import CertificationState, generate_certification
from validation.evidence_manifest import EvidenceManifest
from validation.statistical_validation import ResearchDecision
from config.schema import config_fingerprint


def _manifest():
    return EvidenceManifest(
        commit_sha="abc",
        configuration_fingerprint=config_fingerprint({}),
        issued_at=1_000.0,
        expires_at=2_000.0,
        dataset_fingerprint="dataset-1",
    )


def test_rejected_research_decision_cannot_certify():
    decision = ResearchDecision(False, ("profit_factor_below_minimum",))
    artifact = generate_certification(
        config={},
        commit_sha="abc",
        checks=("research",),
        failures=(),
        research_decision=decision,
        research_validated=True,
        evidence_manifest=_manifest(),
        evidence_now_ts=1_500.0,
    )

    assert artifact.state is CertificationState.ENGINEERING_VALID
    assert "research_validation_rejected" in artifact.failures


def test_approved_research_decision_can_reach_research_validated():
    decision = ResearchDecision(True, ())
    artifact = generate_certification(
        config={},
        commit_sha="abc",
        checks=("research",),
        failures=(),
        research_decision=decision,
        research_validated=True,
        evidence_manifest=_manifest(),
        evidence_now_ts=1_500.0,
    )

    assert artifact.state is CertificationState.RESEARCH_VALIDATED
    assert artifact.failures == ()


def test_live_eligibility_requires_approved_research_decision():
    decision = ResearchDecision(False, ("expectancy_not_positive",))
    artifact = generate_certification(
        config={},
        commit_sha="abc",
        checks=("research",),
        failures=(),
        research_decision=decision,
        research_validated=True,
        evidence_manifest=_manifest(),
        evidence_now_ts=1_500.0,
        live_eligible=True,
    )

    assert artifact.state is CertificationState.ENGINEERING_VALID
    assert "research_validation_rejected" in artifact.failures
    assert "live_requires_research_validation" in artifact.failures


def test_boolean_research_validated_without_decision_fails_closed():
    artifact = generate_certification(
        config={},
        commit_sha="abc",
        checks=("research",),
        failures=(),
        research_validated=True,
    )

    assert artifact.state is CertificationState.ENGINEERING_VALID
    assert "missing_research_decision" in artifact.failures
