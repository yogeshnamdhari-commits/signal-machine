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


def _from_bias(name: str, row: Dict[str, Any], key: str, feed: str = "derived") -> DirectionalFactor:
    value = str(row.get(key, "") or "").lower()
    if value in {"buy", "bull", "bullish", "strong_bullish", "strong_buy", "long", "up"}:
        state = DirectionState.BUY
    elif value in {"sell", "bear", "bearish", "strong_bearish", "strong_sell", "short", "down"}:
        state = DirectionState.SELL
    else:
        state = DirectionState.NEUTRAL
    quality = DataQuality.CALCULATED if value else DataQuality.UNAVAILABLE
    return DirectionalFactor(
        name=name,
        state=state,
        score=70.0 if state is not DirectionState.NEUTRAL else 50.0,
        reason=f"{key}={value or 'unavailable'}",
        source=str(row.get("source", "engine")),
        feed=str(row.get("feed", feed)),
        observed_at=float(row.get("observed_at", row.get("timestamp", 1)) or 1),
        quality=quality,
    )


def direction_for_parameter(name: str, row: Dict[str, Any]) -> DirectionalFactor:
    key = name.lower()
    now_ts = float(row.get("timestamp", 1) or 1)

    if key == "price":
        value = _num(row, "change_24h")
        if value is None:
            return DirectionalFactor("price", DirectionState.NEUTRAL, 0, "24h price change unavailable", "", "ticker", now_ts, DataQuality.UNAVAILABLE)
        state = DirectionState.BUY if value > 0 else DirectionState.SELL if value < 0 else DirectionState.NEUTRAL
        return DirectionalFactor("price", state, min(100, 50 + abs(value) * 5), f"24h change={value:+.2f}%", "exchange", "ticker24h", now_ts, DataQuality.LIVE)

    if key == "b_s_ratio":
        value = _num(row, "buy_sell_ratio")
        if value is None:
            return DirectionalFactor("b_s_ratio", DirectionState.NEUTRAL, 0, "taker buy/sell ratio unavailable", "", "aggTrade", now_ts, DataQuality.UNAVAILABLE)
        state = DirectionState.BUY if value > 1.02 else DirectionState.SELL if value < 0.98 else DirectionState.NEUTRAL
        return DirectionalFactor("b_s_ratio", state, min(100, 50 + abs(value - 1) * 500), f"B/S ratio={value:.3f}", "exchange", "aggTrade", now_ts, DataQuality.LIVE)

    if key == "delta":
        value = _num(row, "net_delta")
        if value is None:
            return DirectionalFactor("delta", DirectionState.NEUTRAL, 0, "trade delta unavailable", "", "aggTrade", now_ts, DataQuality.UNAVAILABLE)
        state = DirectionState.BUY if value > 0 else DirectionState.SELL if value < 0 else DirectionState.NEUTRAL
        return DirectionalFactor("delta", state, 70 if value else 50, f"net delta={value:+.2f}", "exchange", "aggTrade", now_ts, DataQuality.LIVE)

    if key == "oi":
        return _from_bias("oi", row, "oi_bias", "openInterest")
    if key == "funding":
        return _from_bias("funding", row, "funding_bias", "markPrice")
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
        if value is None:
            return DirectionalFactor("imbalance", DirectionState.NEUTRAL, 0, "order-book imbalance unavailable", "", "depth", now_ts, DataQuality.UNAVAILABLE)
        state = DirectionState.BUY if value > 0.05 else DirectionState.SELL if value < -0.05 else DirectionState.NEUTRAL
        return DirectionalFactor("imbalance", state, min(100, 50 + abs(value) * 100), f"book imbalance={value:+.3f}", "exchange", "depth", now_ts, DataQuality.LIVE)
    if key == "sweep":
        detected = bool(row.get("sweep_detected"))
        if not detected:
            return DirectionalFactor("sweep", DirectionState.NEUTRAL, 50, "no qualifying liquidity sweep", "engine", "sweep_detector", now_ts, DataQuality.NOT_APPLICABLE)
        return _from_bias("sweep", row, "sweep_direction", "sweep_detector")
    if key == "regime":
        regime = str(row.get("regime", "")).lower()
        if "bull" in regime:
            state = DirectionState.BUY
        elif "bear" in regime:
            state = DirectionState.SELL
        else:
            state = DirectionState.NEUTRAL
        return DirectionalFactor("regime", state, float(row.get("regime_confidence_pct", 50) or 50), f"regime={regime or 'unavailable'}", "engine", "regime", now_ts, DataQuality.CALCULATED if regime else DataQuality.UNAVAILABLE)

    return DirectionalFactor(key, DirectionState.NEUTRAL, 0, "no directional semantics defined", "", "", now_ts, DataQuality.NOT_APPLICABLE)


def signal_from_canonical(signal: Dict[str, Any], *, bridge_trusted: bool = False) -> str:
    """Return BUY/SELL only for a Python-canonical signal; never infer one.

    ``bridge_trusted`` is reserved for data read directly from the Python engine's
    atomic bridge file. A dashboard-created dict never qualifies merely because it
    contains LONG/SHORT text.
    """
    if not bridge_trusted and str(signal.get("source", "")).lower() not in {"python", "python_engine", "canonical"}:
        return "NO_SIGNAL"
    if str(signal.get("authority", "python")).lower() not in {"python", ""}:
        return "NO_SIGNAL"
    if signal.get("canonical") is False:
        return "NO_SIGNAL"
    side = str(signal.get("side", signal.get("type", ""))).upper()
    if side in {"BUY", "LONG"}:
        return "BUY"
    if side in {"SELL", "SHORT"}:
        return "SELL"
    return "NO_SIGNAL"
