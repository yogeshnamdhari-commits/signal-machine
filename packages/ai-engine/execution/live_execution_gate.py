"""Fail-closed certification gate shared by every live order path."""
from __future__ import annotations

from typing import Any, Dict

from validation.live_gate import LiveCertificationError, verify_live_certification


def require_live_certification() -> Dict[str, Any]:
    """Return the current certification artifact or raise a hard execution gate."""
    try:
        return verify_live_certification()
    except LiveCertificationError as exc:
        raise LiveCertificationError(f"live execution certification gate: {exc}") from exc
