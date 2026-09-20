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
    """Return only semantically valid and fresh production observations."""
    value = row.get(key)

    # When the canonical row carries source timestamps, enforce per-metric
    # freshness instead of trusting the single dashboard snapshot timestamp.
    metric_ts = row.get("metric_timestamps", {}).get(key)
    if metric_ts:
        try:
            age = time.time() - float(metric_ts)
            max_age = {
                "price": 60.0,
                "change_24h": 120.0,
                "volume_24h": 120.0,
                "open_interest": 15.0,
                "oi_change_pct": 420.0,
                "funding": 30.0,
                "net_delta": 15.0,
                "buy_sell_ratio": 15.0,
                "cvd_5m": 15.0,
                "exchange_flow": 15.0,
                "flow_strength": 15.0,
                "imbalance": 5.0,
                "sweep": 3600.0,
                "sweep_price": 3600.0,
                "regime": 420.0,
                "fvg": 259200.0,
                "fvg_gap_high": 259200.0,
                "fvg_gap_low": 259200.0,
                "liq_risk_level": 3600.0,
                "long_liq_vol": 3600.0,
                "short_liq_vol": 3600.0,
            }.get(key, 60.0)
            if age < 0 or age > max_age:
                return None
        except (TypeError, ValueError):
            return None

    if key in {"open_interest", "oi_change_pct"}:
        if _as_positive_float(row.get("open_interest")) is None:
            return None

    if key in {"net_delta", "buy_sell_ratio", "cvd_5m"}:
        if int(row.get("flow_total_trades", 0) or 0) <= 0:
            return None

    if key in {"exchange_flow", "aggressive_buy_vol", "aggressive_sell_vol", "flow_strength"}:
        if int(row.get("flow_total_trades", 0) or 0) <= 0:
            return None

    if key in {"long_liq_vol", "short_liq_vol"}:
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


def _direction_for_live_metric(
    name: str,
    row: Dict[str, Any],
    raw_key: str,
    *,
    bias_keys: tuple[str, ...] = (),
) -> Any:
    """Apply the exact source timestamp and raw-value gate before deriving direction."""
    rr = dict(row)
    shown = display_value(row, raw_key)
    metric_ts = row.get("metric_timestamps", {}).get(raw_key)
    if metric_ts:
        rr["observed_at"] = metric_ts
    if shown is None:
        for key in bias_keys:
            rr[key] = None
        if name == "sweep":
            rr["sweep_detected"] = False
        if name == "regime":
            rr["regime"] = None
    return direction_for_parameter(name, rr)


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
    gap_high = display_value(row, "fvg_gap_high")
    gap_low = display_value(row, "fvg_gap_low")
    score = _as_positive_float(row.get("fvg_score"))

    gap_high = _as_positive_float(gap_high)
    gap_low = _as_positive_float(gap_low)
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
        "oi": _direction_for_live_metric("oi", row, "open_interest", bias_keys=("oi_bias",)),
        "funding": _direction_for_live_metric("funding", row, "funding", bias_keys=("funding_bias",)),
        "delta": _direction_for_live_metric("delta", row, "net_delta"),
        "b_s_ratio": _direction_for_live_metric("b_s_ratio", row, "buy_sell_ratio"),
        "cvd": _direction_for_live_metric("cvd", row, "cvd_5m", bias_keys=("cvd_bias",)),
        "flow": _direction_for_live_metric("flow", row, "flow_strength", bias_keys=("flow_signal",)),
        "exchange_flow": _direction_for_live_metric("exchange_flow", row, "exchange_flow", bias_keys=("exchange_bias",)),
        "volume": _direction_for_live_metric("volume", row, "imbalance", bias_keys=("vol_bias",)),
        "imbalance": _direction_for_live_metric("imbalance", row, "imbalance"),
        "sweep": _direction_for_live_metric("sweep", row, "sweep", bias_keys=("sweep_direction",)),
        "regime": _direction_for_live_metric("regime", row, "regime"),
        "price": _direction_for_live_metric("price", row, "change_24h"),
        "fvg": _fvg_display(row),
        "sweep_price": display_value(row, "sweep_price") if row.get("sweep_detected") else None,
        "liq_risk": display_value(row, "liq_risk_level"),
        "regime_conf": row.get("regime_confidence_pct") if row.get("regime") not in (None, "") else None,
    }

def display_cell(value: Any, *, none_label: str = "UNAVAILABLE") -> str:
    if value is None or value == "":
        return none_label
    return str(value)
