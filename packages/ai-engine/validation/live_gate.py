"""Verify current live certification before any live execution mode."""
from __future__ import annotations

import json
import math
import subprocess
import time
from pathlib import Path

from config import config
from config.schema import config_fingerprint
from validation.forward_evidence_binding import (
    DEFAULT_FORWARD_EVIDENCE_PATH,
    ForwardEvidenceBindingError,
    verify_forward_evidence_report,
)


class LiveCertificationError(RuntimeError):
    pass


def _current_commit_sha() -> str:
    """Return the checked-out source commit; never trust caller-supplied commit env vars."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(Path(__file__).resolve().parents[3]),
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    sha = result.stdout.strip()
    return sha if len(sha) == 40 and all(ch in "0123456789abcdef" for ch in sha.lower()) else ""


def verify_live_certification(
    cert_path: Path | None = None,
    *,
    now_ts: float | None = None,
) -> dict:
    """Verify state, provenance, failure status, and freshness of a live certificate."""
    path = cert_path or (Path(__file__).resolve().parent.parent / "data" / "certification.current.json")
    if not path.exists():
        raise LiveCertificationError("No current certification artifact exists")

    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise LiveCertificationError(f"Invalid certification artifact: {exc}") from exc

    if artifact.get("state") != "LIVE_ELIGIBLE":
        raise LiveCertificationError(f"Certification state is {artifact.get('state')!r}, not LIVE_ELIGIBLE")
    if artifact.get("configuration_fingerprint") != config_fingerprint(config):
        raise LiveCertificationError("Certification configuration fingerprint does not match runtime configuration")

    running_commit = _current_commit_sha()
    certified_commit = str(artifact.get("commit_sha", "")).strip()
    if not running_commit:
        raise LiveCertificationError("Running source commit is unavailable; live certification cannot be verified")
    if not certified_commit or certified_commit != running_commit:
        raise LiveCertificationError("Certification commit does not match the running source commit")

    failures = artifact.get("failures") or []
    if failures:
        raise LiveCertificationError("Certification artifact contains failures: " + ", ".join(map(str, failures)))

    forward_evidence_sha256 = artifact.get("forward_evidence_sha256")
    if not isinstance(forward_evidence_sha256, str) or not forward_evidence_sha256:
        raise LiveCertificationError("Live certification is not bound to immutable C/D forward evidence")
    try:
        evidence = verify_forward_evidence_report(
            DEFAULT_FORWARD_EVIDENCE_PATH,
            expected_commit=running_commit,
        )
    except ForwardEvidenceBindingError as exc:
        raise LiveCertificationError(f"Forward evidence binding failed: {exc}") from exc
    if evidence["_file_sha256"] != forward_evidence_sha256:
        raise LiveCertificationError("Forward evidence aggregate hash does not match certification")

    issued_at = artifact.get("issued_at")
    expires_at = artifact.get("expires_at")
    numeric_freshness = (
        isinstance(issued_at, (int, float))
        and not isinstance(issued_at, bool)
        and isinstance(expires_at, (int, float))
        and not isinstance(expires_at, bool)
    )
    if not numeric_freshness:
        raise LiveCertificationError("Certification freshness metadata is missing")
    if not math.isfinite(float(issued_at)) or not math.isfinite(float(expires_at)):
        raise LiveCertificationError("Certification freshness metadata is invalid")
    if float(expires_at) <= float(issued_at):
        raise LiveCertificationError("Certification freshness window is invalid")

    if now_ts is not None and isinstance(now_ts, bool):
        raise LiveCertificationError("Certification verification timestamp is invalid")
    current_ts = time.time() if now_ts is None else float(now_ts)
    if not math.isfinite(current_ts):
        raise LiveCertificationError("Certification verification timestamp is invalid")
    if current_ts < float(issued_at):
        raise LiveCertificationError("Certification is not yet valid")
    if current_ts >= float(expires_at):
        raise LiveCertificationError("Certification has expired")

    return artifact
