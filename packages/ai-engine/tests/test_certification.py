from validation.certification import CertificationState, generate_certification
from validation.statistical_validation import ResearchDecision


def test_rejected_research_decision_cannot_certify():
    decision = ResearchDecision(False, ("profit_factor_below_minimum",))
    artifact = generate_certification(
        config={},
        commit_sha="abc",
        checks=("research",),
        failures=(),
        research_decision=decision,
        research_validated=True,
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
