"""Verify current live certification before any live execution mode."""
from __future__ import annotations

import json
import os
from pathlib import Path

from config import config
from config.schema import config_fingerprint


class LiveCertificationError(RuntimeError):
    pass


def verify_live_certification(cert_path: Path | None = None) -> dict:
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
    return artifact
