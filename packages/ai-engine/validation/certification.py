"""Current certification artifacts tied to executable evidence."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from config.schema import config_fingerprint
from validation.evidence_manifest import EvidenceManifest, verify_evidence_manifest
from validation.statistical_validation import ResearchDecision


class CertificationState(str, Enum):
    ENGINEERING_VALID = "ENGINEERING_VALID"
    RESEARCH_VALIDATED = "RESEARCH_VALIDATED"
    LIVE_ELIGIBLE = "LIVE_ELIGIBLE"


@dataclass(frozen=True)
class CertificationArtifact:
    state: CertificationState
    commit_sha: str
    configuration_fingerprint: str
    checks: Tuple[str, ...]
    failures: Tuple[str, ...]
    issued_at: float = 0.0
    expires_at: float = 0.0

    @property
    def valid(self) -> bool:
        return not self.failures


def generate_certification(
    *,
    config,
    commit_sha: str,
    checks: Tuple[str, ...],
    failures: Tuple[str, ...],
    research_validated: bool = False,
    live_eligible: bool = False,
    research_decision: ResearchDecision | None = None,
    evidence_manifest: EvidenceManifest | None = None,
    evidence_now_ts: float | None = None,
) -> CertificationArtifact:
    """Create a certification artifact, failing closed on absent/stale evidence.

    The legacy ``research_validated`` flag is retained for compatibility, but it
    cannot by itself authorize research or live certification. A current approved
    ``ResearchDecision`` and a matching, unexpired ``EvidenceManifest`` are
    required whenever research validation is requested.

    For research-validated or live-eligible certificates, freshness metadata is
    copied from the approved evidence manifest so the runtime live gate can reject
    stale certification artifacts independently.
    """
    failure_list = list(failures)

    research_approved = False
    if research_decision is None:
        if research_validated:
            failure_list.append("missing_research_decision")
    elif not research_decision.approved:
        failure_list.append("research_validation_rejected")
        failure_list.extend(
            reason
            for reason in research_decision.reasons
            if reason not in failure_list
        )
    else:
        research_approved = True

    manifest_approved = False
    if research_validated:
        if evidence_manifest is None:
            failure_list.append("missing_evidence_manifest")
        elif evidence_now_ts is None:
            failure_list.append("missing_evidence_timestamp")
        else:
            manifest_decision = verify_evidence_manifest(
                evidence_manifest,
                expected_commit=commit_sha,
                expected_config=config,
                now_ts=evidence_now_ts,
            )
            if manifest_decision.approved:
                manifest_approved = True
            else:
                failure_list.extend(
                    reason
                    for reason in manifest_decision.reasons
                    if reason not in failure_list
                )

    research_ready = research_validated and research_approved and manifest_approved
    if live_eligible and not research_ready:
        failure_list.append("live_requires_research_validation")

    failures_tuple = tuple(dict.fromkeys(failure_list))
    state = (
        CertificationState.LIVE_ELIGIBLE
        if live_eligible and research_ready and not failures_tuple
        else (
            CertificationState.RESEARCH_VALIDATED
            if research_ready and not failures_tuple
            else CertificationState.ENGINEERING_VALID
        )
    )

    issued_at = evidence_manifest.issued_at if research_ready and evidence_manifest else 0.0
    expires_at = evidence_manifest.expires_at if research_ready and evidence_manifest else 0.0

    return CertificationArtifact(
        state=state,
        commit_sha=commit_sha,
        configuration_fingerprint=config_fingerprint(config),
        checks=tuple(checks),
        failures=failures_tuple,
        issued_at=issued_at,
        expires_at=expires_at,
    )
