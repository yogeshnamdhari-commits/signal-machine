"""Normalize signals emitted through the canonical Python bridge."""
from __future__ import annotations

from typing import Any, Dict


def canonicalize_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(signal)
    result["source"] = "python_engine"
    result["authority"] = "python"
    result["canonical"] = True
    result.setdefault("engine_version", "unknown")
    result.setdefault("provenance_ids", [])
    return result
