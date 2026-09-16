"""Current certification artifacts tied to executable evidence."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from config.schema import config_fingerprint


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

    @property
    def valid(self) -> bool:
        return not self.failures


def generate_certification(*, config, commit_sha: str, checks: Tuple[str, ...], failures: Tuple[str, ...], research_validated: bool = False, live_eligible: bool = False) -> CertificationArtifact:
    if live_eligible and not research_validated:
        failures = tuple(failures) + ("live_requires_research_validation",)
    state = CertificationState.LIVE_ELIGIBLE if live_eligible and research_validated and not failures else (
        CertificationState.RESEARCH_VALIDATED if research_validated and not failures else CertificationState.ENGINEERING_VALID
    )
    return CertificationArtifact(
        state=state,
        commit_sha=commit_sha,
        configuration_fingerprint=config_fingerprint(config),
        checks=tuple(checks),
        failures=tuple(failures),
    )
