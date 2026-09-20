"""Deterministic BUY/SELL/NEUTRAL semantics for dashboard parameters.

This module is display/evidence logic. It never creates an executable trade signal.
"""
from __future__ import annotations

from typing import Any, Dict

from core.directional_factor import DataQuality, DirectionState, DirectionalFactor


def _num(row: Dict[str, Any], key: str):
    value = row.get(key)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _observed_at(row: Dict[str, Any]) -> float:
    value = row.get("observed_at", row.get("timestamp", row.get("last_update", 0)))
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _factor(
    name: str,
    state: DirectionState,
    score: float,
    reason: str,
    row: Dict[str, Any],
    feed: str,
    quality: DataQuality,
    source: str = "engine",
) -> DirectionalFactor:
    return DirectionalFactor(
        name=name,
        state=state,
        score=score,
        reason=reason,
        source=str(row.get("source", source)),
        feed=str(row.get("feed", feed)),
        observed_at=_observed_at(row) or 1.0,
        quality=quality,
    )


def _from_bias(name: str, row: Dict[str, Any], key: str, feed: str = "derived") -> DirectionalFactor:
    value = str(row.get(key, "") or "").lower()
    if value in {"buy", "bull", "bullish", "strong_bullish", "strong_buy", "long", "up", "taker_buy"}:
        state = DirectionState.BUY
    elif value in {"sell", "bear", "bearish", "strong_bearish", "strong_sell", "short", "down", "taker_sell"}:
        state = DirectionState.SELL
    else:
        state = DirectionState.NEUTRAL
    observed_at = _observed_at(row)
    quality = DataQuality.CALCULATED if value and observed_at > 0 else DataQuality.UNAVAILABLE
    return _factor(
        name,
        state,
        70.0 if state is not DirectionState.NEUTRAL else 50.0,
        f"{key}={value or 'unavailable'}",
        row,
        feed,
        quality,
    )


def direction_for_parameter(name: str, row: Dict[str, Any]) -> DirectionalFactor:
    key = name.lower()
    observed_at = _observed_at(row)

    if key == "price":
        value = _num(row, "change_24h")
        if value is None or observed_at <= 0:
            return _factor("price", DirectionState.NEUTRAL, 0, "24h price observation unavailable", row, "ticker24h", DataQuality.UNAVAILABLE, "exchange")
        state = DirectionState.BUY if value > 0 else DirectionState.SELL if value < 0 else DirectionState.NEUTRAL
        return _factor("price", state, min(100, 50 + abs(value) * 5), f"24h change={value:+.2f}%", row, "ticker24h", DataQuality.LIVE, "exchange")

    if key in {"b_s_ratio", "delta", "cvd", "flow", "exchange_flow"} and (row.get("flow_total_trades") is not None and int(row.get("flow_total_trades") or 0) <= 0):
        return _factor(key, DirectionState.NEUTRAL, 0, "trade tape unavailable; no directional vote", row, "aggTrade", DataQuality.UNAVAILABLE)

    if key == "b_s_ratio":
        value = _num(row, "buy_sell_ratio")
        if value is None or observed_at <= 0:
            return _factor("b_s_ratio", DirectionState.NEUTRAL, 0, "taker buy/sell ratio unavailable", row, "aggTrade", DataQuality.UNAVAILABLE, "exchange")
        state = DirectionState.BUY if value > 1.02 else DirectionState.SELL if value < 0.98 else DirectionState.NEUTRAL
        return _factor("b_s_ratio", state, min(100, 50 + abs(value - 1) * 500), f"B/S ratio={value:.3f}", row, "aggTrade", DataQuality.LIVE, "exchange")

    if key == "delta":
        value = _num(row, "net_delta")
        if value is None or observed_at <= 0:
            return _factor("delta", DirectionState.NEUTRAL, 0, "trade delta unavailable", row, "aggTrade", DataQuality.UNAVAILABLE, "exchange")
        state = DirectionState.BUY if value > 0 else DirectionState.SELL if value < 0 else DirectionState.NEUTRAL
        return _factor("delta", state, 70 if value else 50, f"net delta={value:+.2f}", row, "aggTrade", DataQuality.LIVE, "exchange")

    if key == "oi":
        value = _num(row, "open_interest")
        if value is None or value <= 0:
            return _factor("oi", DirectionState.NEUTRAL, 0, "open interest unavailable", row, "openInterest", DataQuality.UNAVAILABLE, "exchange")
        return _from_bias("oi", row, "oi_bias", "openInterest")

    if key == "funding":
        value = _num(row, "funding")
        if value is None or observed_at <= 0 or _num(row, "mark_price") is None:
            return _factor("funding", DirectionState.NEUTRAL, 0, "funding observation unavailable", row, "markPrice", DataQuality.UNAVAILABLE, "exchange")
        # Funding is a positioning/risk modifier, not a standalone trade trigger.
        # Negative funding is long-supportive; positive funding is short-supportive.
        # Row value is expressed in percent (raw funding rate × 100).
        state = DirectionState.BUY if value < -0.01 else DirectionState.SELL if value > 0.01 else DirectionState.NEUTRAL
        return _factor("funding", state, min(100, 50 + min(abs(value) * 5000, 50)), f"funding={value:+.6f}%", row, "markPrice", DataQuality.LIVE, "exchange")

    if key == "cvd":
        return _from_bias("cvd", row, "cvd_bias", "aggTrade")
    if key == "flow":
        return _from_bias("flow", row, "flow_signal", "aggTrade")
    if key == "exchange_flow":
        return _from_bias("exchange_flow", row, "exchange_bias", "exchangeFlow")
    if key == "volume":
        return _from_bias("volume", row, "vol_bias", "ticker24h")

    if key == "imbalance":
        value = _num(row, "imbalance")
        if value is None or observed_at <= 0:
            return _factor("imbalance", DirectionState.NEUTRAL, 0, "order-book imbalance unavailable", row, "depth", DataQuality.UNAVAILABLE, "exchange")
        state = DirectionState.BUY if value > 0.05 else DirectionState.SELL if value < -0.05 else DirectionState.NEUTRAL
        return _factor("imbalance", state, min(100, 50 + abs(value) * 100), f"book imbalance={value:+.3f}", row, "depth", DataQuality.LIVE, "exchange")

    if key == "sweep":
        detected = bool(row.get("sweep_detected"))
        if not detected:
            return _factor("sweep", DirectionState.NEUTRAL, 0, "no qualifying liquidity sweep", row, "sweep_detector", DataQuality.NOT_APPLICABLE)
        return _from_bias("sweep", row, "sweep_direction", "sweep_detector")

    if key == "regime":
        regime = str(row.get("regime", "") or "").lower()
        tf_values = [str(row.get(k, "") or "") for k in ("regime_1m", "regime_5m", "regime_15m", "regime_1h", "regime_4h")]
        regime_missing = not regime or (
            regime == "range"
            and float(row.get("regime_confidence_pct", 50) or 50) == 50
            and float(row.get("regime_alignment", 0) or 0) == 0
            and not any(tf_values)
        )
        if regime_missing or observed_at <= 0:
            return _factor("regime", DirectionState.NEUTRAL, 0, "regime snapshot unavailable", row, "regime", DataQuality.UNAVAILABLE)
        if "bull" in regime:
            state = DirectionState.BUY
        elif "bear" in regime:
            state = DirectionState.SELL
        else:
            state = DirectionState.NEUTRAL
        confidence = float(row.get("regime_confidence_pct", 50) or 50)
        return _factor("regime", state, max(0, min(100, confidence)), f"regime={regime}; confidence={confidence:.1f}%", row, "regime", DataQuality.CALCULATED)

    return _factor(key, DirectionState.NEUTRAL, 0, "no directional semantics defined", row, "", DataQuality.NOT_APPLICABLE)


def signal_from_canonical(signal: Dict[str, Any]) -> str:
    """Return BUY/SELL only for a structurally canonical Python-engine signal."""
    if not isinstance(signal, dict) or not signal:
        return "NO_SIGNAL"
    if str(signal.get("source", "")).lower() not in {"python", "python_engine"}:
        return "NO_SIGNAL"
    if str(signal.get("authority", "")).lower() != "python":
        return "NO_SIGNAL"
    if signal.get("canonical") is not True:
        return "NO_SIGNAL"
    side = str(signal.get("side", signal.get("type", ""))).upper()
    if side in {"BUY", "LONG"}:
        return "BUY"
    if side in {"SELL", "SHORT"}:
        return "SELL"
    return "NO_SIGNAL"
