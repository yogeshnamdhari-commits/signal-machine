"""Presentation contract for the production live sheet."""
from __future__ import annotations

import time
from typing import Any, Dict

from core.parameter_semantics import direction_for_parameter, signal_from_canonical


def freshness_state(snapshot_ts: float, now: float | None = None, *, max_age: float = 60.0) -> str:
    now = time.time() if now is None else now
    if not snapshot_ts or snapshot_ts <= 0:
        return "UNAVAILABLE"
    age = now - snapshot_ts
    if age < 0:
        return "STALE"
    return "LIVE" if age <= max_age else "STALE"


def _safe_value(row: Dict[str, Any], key: str, suffix: str = "") -> str:
    value = row.get(key)
    if value is None or value == "":
        return "UNAVAILABLE"
    if isinstance(value, (int, float)):
        return f"{value}{suffix}"
    return str(value)


def build_signal_display(signal: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    """Build display metadata without ever voting factors into a trade signal."""
    canonical = signal_from_canonical(signal, bridge_trusted=bool(signal))
    return {
        "signal": canonical,
        "authority": "python-bridge" if canonical != "NO_SIGNAL" else "none",
        "signal_reason": "canonical Python engine signal" if canonical != "NO_SIGNAL" else "no canonical engine signal",
        "oi": direction_for_parameter("oi", row),
        "funding": direction_for_parameter("funding", row),
        "delta": direction_for_parameter("delta", row),
        "b_s_ratio": direction_for_parameter("b_s_ratio", row),
        "cvd": direction_for_parameter("cvd", row),
        "flow": direction_for_parameter("flow", row),
        "exchange_flow": direction_for_parameter("exchange_flow", row),
        "volume": direction_for_parameter("volume", row),
        "imbalance": direction_for_parameter("imbalance", row),
        "sweep": direction_for_parameter("sweep", row),
        "regime": direction_for_parameter("regime", row),
        "price": direction_for_parameter("price", row),
        "fvg": {
            "state": "BUY" if str(signal.get("side", "")).upper() == "LONG" and signal.get("fvg_score", 0) else "SELL" if str(signal.get("side", "")).upper() == "SHORT" and signal.get("fvg_score", 0) else "NEUTRAL",
            "value": signal.get("vp_poc") if signal.get("fvg_score", 0) else None,
            "quality": "CALCULATED" if signal.get("fvg_score", 0) else "NOT_APPLICABLE",
        },
        "sweep_price": row.get("sweep_price") if row.get("sweep_detected") else None,
        "liq_risk": row.get("liq_risk_level", "UNAVAILABLE"),
        "regime_conf": row.get("regime_confidence_pct"),
    }


def display_cell(value: Any, *, none_label: str = "UNAVAILABLE") -> str:
    if value is None or value == "":
        return none_label
    return str(value)
