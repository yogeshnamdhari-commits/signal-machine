"""Machine-checkable provenance manifest for current research evidence."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Tuple

from config.schema import config_fingerprint


@dataclass(frozen=True)
class EvidenceManifest:
    """Immutable identity and freshness contract for research evidence."""

    commit_sha: str
    configuration_fingerprint: str
    issued_at: float
    expires_at: float
    dataset_fingerprint: str


@dataclass(frozen=True)
class ResearchEvidenceManifestDecision:
    approved: bool
    reasons: Tuple[str, ...]


def _finite_timestamp(value: object) -> bool:
    """Accept real finite numbers only; bool is intentionally rejected."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def verify_evidence_manifest(
    manifest: EvidenceManifest,
    *,
    expected_commit: str,
    expected_config,
    now_ts: float,
) -> ResearchEvidenceManifestDecision:
    reasons: list[str] = []
    if not manifest.commit_sha or manifest.commit_sha != expected_commit:
        reasons.append("evidence_commit_mismatch")
    if manifest.configuration_fingerprint != config_fingerprint(expected_config):
        reasons.append("evidence_config_mismatch")
    if not manifest.dataset_fingerprint:
        reasons.append("missing_dataset_fingerprint")
    if not _finite_timestamp(manifest.issued_at) or not _finite_timestamp(manifest.expires_at):
        reasons.append("invalid_evidence_window")
    elif not _finite_timestamp(now_ts):
        reasons.append("invalid_evidence_timestamp")
    elif manifest.expires_at <= manifest.issued_at:
        reasons.append("invalid_evidence_window")
    elif now_ts >= manifest.expires_at:
        reasons.append("evidence_expired")
    elif now_ts < manifest.issued_at:
        reasons.append("evidence_not_yet_valid")
    return ResearchEvidenceManifestDecision(approved=not reasons, reasons=tuple(reasons))
