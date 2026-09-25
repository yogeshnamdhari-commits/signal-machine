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


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_positive_float(value: Any) -> float | None:
    value = _as_float(value)
    return value if value is not None and value > 0 else None


def display_value(row: Dict[str, Any], key: str) -> Any:
    """Return a semantically valid value; do not display placeholder zeroes as live data."""
    value = row.get(key)

    if key in {"open_interest", "oi_change_pct"}:
        if _as_positive_float(row.get("open_interest")) is None:
            return None

    if key in {"net_delta", "buy_sell_ratio", "cvd_5m"}:
        if int(row.get("flow_total_trades", 0) or 0) <= 0:
            return None

    if key in {"exchange_flow", "aggressive_buy_vol", "aggressive_sell_vol", "flow_strength"}:
        if int(row.get("flow_total_trades", 0) or 0) <= 0:
            return None

    if key in {"long_liq_vol", "short_liq_vol", "liq_long_zone_price", "liq_short_zone_price"}:
        if int(row.get("cluster_count", 0) or 0) <= 0 and int(row.get("long_liq_count", 0) or 0) <= 0 and int(row.get("short_liq_count", 0) or 0) <= 0:
            return None

    if key == "liq_risk_level":
        has_liq_data = (
            int(row.get("cluster_count", 0) or 0) > 0
            or int(row.get("long_liq_count", 0) or 0) > 0
            or int(row.get("short_liq_count", 0) or 0) > 0
        )
        return value if has_liq_data and value not in (None, "", "low") else (value if has_liq_data else None)

    if key == "volume_24h" and (_as_float(value) or 0) <= 0:
        return None

    return value


def live_observation_values(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return raw observations separately from their directional interpretations.

    Missing trade-tape inputs remain unavailable rather than being converted to zero.
    """
    return {
        "cvd_5m": display_value(row, "cvd_5m"),
        "flow_strength": display_value(row, "flow_strength"),
        "exchange_flow": display_value(row, "exchange_flow"),
        "imbalance": display_value(row, "imbalance"),
    }


def _fvg_display(row: Dict[str, Any]) -> Dict[str, Any]:
    alignment = str(row.get("fvg_alignment", "") or "").lower()
    gap_high = _as_positive_float(row.get("fvg_gap_high"))
    gap_low = _as_positive_float(row.get("fvg_gap_low"))
    score = _as_positive_float(row.get("fvg_score"))

    if gap_high is None or gap_low is None or gap_high < gap_low:
        return {"state": "NEUTRAL", "value": None, "quality": "NOT_APPLICABLE"}

    state = "BUY" if alignment == "bullish" else "SELL" if alignment == "bearish" else "NEUTRAL"
    return {
        "state": state,
        "value": (gap_high + gap_low) / 2.0,
        "gap_high": gap_high,
        "gap_low": gap_low,
        "score": score or 0.0,
        "quality": "CALCULATED",
    }


def build_signal_display(signal: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    """Build display metadata without ever voting factors into a trade signal."""
    canonical = signal_from_canonical(signal)
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
        "fvg": _fvg_display(row),
        "sweep_price": row.get("sweep_price") if row.get("sweep_detected") else None,
        "liq_risk": display_value(row, "liq_risk_level"),
        "regime_conf": row.get("regime_confidence_pct") if row.get("regime") not in (None, "") else None,
    }


def display_cell(value: Any, *, none_label: str = "UNAVAILABLE") -> str:
    if value is None or value == "":
        return none_label
    return str(value)
