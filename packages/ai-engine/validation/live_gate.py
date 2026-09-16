"""Verify current live certification before any live execution mode."""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

from config import config
from config.schema import config_fingerprint


class LiveCertificationError(RuntimeError):
    pass


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

    expected_commit = os.getenv("GITHUB_SHA") or os.getenv("LIVE_CERT_COMMIT")
    if expected_commit and artifact.get("commit_sha") != expected_commit:
        raise LiveCertificationError("Certification commit does not match the running source commit")

    failures = artifact.get("failures") or []
    if failures:
        raise LiveCertificationError("Certification artifact contains failures: " + ", ".join(map(str, failures)))

    issued_at = artifact.get("issued_at")
    expires_at = artifact.get("expires_at")
    if not isinstance(issued_at, (int, float)) or not isinstance(expires_at, (int, float)):
        raise LiveCertificationError("Certification freshness metadata is missing")
    if not math.isfinite(float(issued_at)) or not math.isfinite(float(expires_at)):
        raise LiveCertificationError("Certification freshness metadata is invalid")
    if float(expires_at) <= float(issued_at):
        raise LiveCertificationError("Certification freshness window is invalid")

    current_ts = time.time() if now_ts is None else float(now_ts)
    if not math.isfinite(current_ts):
        raise LiveCertificationError("Certification verification timestamp is invalid")
    if current_ts < float(issued_at):
        raise LiveCertificationError("Certification is not yet valid")
    if current_ts >= float(expires_at):
        raise LiveCertificationError("Certification has expired")

    return artifact
