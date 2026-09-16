"""Fail-closed risk admission wrapper for the canonical RiskEngine."""
from __future__ import annotations

from typing import Any, Dict

from config import config


def _effective_score(signal: Dict[str, Any]) -> float:
    institutional = float(signal.get("institutional_score", 0) or 0)
    confidence = float(signal.get("confidence", 0) or 0)
    return institutional if institutional > 0 else confidence * 100.0


async def _check_signal_hardened(self: Any, signal: Dict[str, Any]) -> Dict[str, Any]:
    entry = float(signal.get("entry_price", 0) or 0)
    sl = float(signal.get("stop_loss", 0) or 0)
    if entry <= 0 or sl <= 0:
        return {"allowed": False, "reason": "missing prices"}

    max_daily_loss = self.balance * config.risk.max_daily_loss_pct / 100.0
    if self.daily_pnl < -max_daily_loss:
        return {"allowed": False, "reason": "daily loss limit"}

    drawdown = (self.peak - self.balance) / self.peak * 100.0 if self.peak else 0.0
    if drawdown >= config.risk.max_drawdown_pct:
        return {"allowed": False, "reason": "max drawdown"}

    if self.open_count >= config.risk.max_open_positions:
        return {"allowed": False, "reason": "max positions"}

    score = _effective_score(signal)
    threshold = float(config.risk.quality_gate_score)
    if score < threshold:
        return {"allowed": False, "reason": f"low quality score: {score:.1f}/100 < {threshold:.1f}"}

    sl_dist_pct = abs(entry - sl) / entry * 100.0
    if sl_dist_pct > config.risk.max_sl_distance_pct:
        return {"allowed": False, "reason": f"SL too wide: {sl_dist_pct:.1f}% > {config.risk.max_sl_distance_pct}%"}

    if score >= config.risk.tier_elite_score:
        size_mult = config.risk.tier_elite_mult
    elif score >= config.risk.tier_strong_score:
        size_mult = config.risk.tier_strong_mult
    elif score >= config.risk.tier_marginal_score:
        size_mult = config.risk.tier_marginal_mult
    else:
        return {"allowed": False, "reason": "score below executable sizing tier"}

    risk_usd = self.balance * config.risk.risk_per_trade_pct / 100.0 * size_mult
    regime_mult = float(signal.get("regime_size_mult", 1.0) or 1.0)
    if regime_mult <= 0:
        return {"allowed": False, "reason": "invalid regime sizing multiplier"}
    risk_usd *= regime_mult

    risk_dist = max(abs(entry - sl), entry * 0.002)
    if risk_dist <= 0 or risk_usd <= 0:
        return {"allowed": False, "reason": "non-positive risk budget"}

    quantity = risk_usd / risk_dist
    if quantity <= 0:
        return {"allowed": False, "reason": "zero quantity"}

    position_value = quantity * entry
    max_risk_usd = self.balance * config.risk.risk_per_trade_pct / 100.0 * size_mult * 2.0
    if risk_dist * quantity > max_risk_usd:
        quantity = max_risk_usd / risk_dist
        position_value = quantity * entry
    max_position_value = self.balance * config.risk.max_position_pct / 100.0
    if position_value > max_position_value:
        quantity = max_position_value / entry
        position_value = max_position_value

    if quantity <= 0:
        return {"allowed": False, "reason": "quantity became zero after risk caps"}

    tp = float(signal.get("take_profit", entry) or entry)
    rr = abs(tp - entry) / risk_dist if risk_dist else 0.0
    return {
        "allowed": True,
        "quantity": round(quantity, 6),
        "position_value": round(position_value, 2),
        "risk_reward": round(rr, 2),
        "margin_required": round(position_value / config.risk.max_leverage, 2),
        "sizing_multiplier": round(size_mult, 2),
        "quality_threshold": threshold,
        "quality_score": round(score, 2),
    }


def apply_risk_integrity_patch() -> None:
    from execution.risk_engine import RiskEngine

    if getattr(RiskEngine, "_signal_machine_integrity_patched", False):
        return
    RiskEngine.check_signal = _check_signal_hardened  # type: ignore[method-assign]
    RiskEngine._signal_machine_integrity_patched = True
