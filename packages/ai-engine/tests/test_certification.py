import hashlib
import json

from validation.certification import CertificationState, generate_certification
from validation.evidence_manifest import EvidenceManifest
from validation.statistical_validation import ResearchDecision
from config.schema import config_fingerprint




def _forward_evidence_file(tmp_path, commit="abc"):
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
        "bundle_roots": {"C": "forward_sessions/session_C_evidence", "D": "forward_sessions/session_D_evidence"},
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), default=str)
    report["aggregate_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    path = tmp_path / "forward_aggregate.json"
    payload = json.dumps(report, sort_keys=True, indent=2).encode("utf-8")
    path.write_bytes(payload)
    return path


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


def test_expired_evidence_manifest_cannot_certify_research():
    decision = ResearchDecision(True, ())
    artifact = generate_certification(
        config={},
        commit_sha="abc",
        checks=("research",),
        failures=(),
        research_decision=decision,
        research_validated=True,
        evidence_manifest=_manifest(),
        evidence_now_ts=2_000.0,
    )

    assert artifact.state is CertificationState.ENGINEERING_VALID
    assert "evidence_expired" in artifact.failures


def test_future_evidence_manifest_cannot_certify_research():
    decision = ResearchDecision(True, ())
    artifact = generate_certification(
        config={},
        commit_sha="abc",
        checks=("research",),
        failures=(),
        research_decision=decision,
        research_validated=True,
        evidence_manifest=_manifest(),
        evidence_now_ts=999.0,
    )

    assert artifact.state is CertificationState.ENGINEERING_VALID
    assert "evidence_not_yet_valid" in artifact.failures


def test_live_eligibility_requires_forward_evidence(tmp_path):
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
        live_eligible=True,
    )

    assert artifact.state is CertificationState.ENGINEERING_VALID
    assert "missing_forward_evidence" in artifact.failures


def test_live_eligibility_binds_forward_evidence_hash(tmp_path):
    decision = ResearchDecision(True, ())
    evidence_path = _forward_evidence_file(tmp_path, "abc")
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
        forward_evidence_path=evidence_path,
    )

    expected = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
    assert artifact.state is CertificationState.LIVE_ELIGIBLE
    assert artifact.forward_evidence_sha256 == expected
    assert artifact.failures == ()


def test_live_eligibility_rejects_wrong_forward_evidence_commit(tmp_path):
    decision = ResearchDecision(True, ())
    evidence_path = _forward_evidence_file(tmp_path, "different-commit")
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
        forward_evidence_path=evidence_path,
    )

    assert artifact.state is CertificationState.ENGINEERING_VALID
    assert any(reason.startswith("forward_evidence_invalid:") for reason in artifact.failures)
