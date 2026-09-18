"""Fail-closed research evidence gates for current strategy certification."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from math import isfinite
from typing import Dict, Tuple


MIN_COMPLETED_TRADES = 100
MIN_PROFIT_FACTOR = 1.20
MIN_EXPECTANCY = 0.0
MAX_DRAWDOWN_PCT = 12.0

# Empty/placeholder labels are not evidence of an actually applied execution
# cost, slippage, or funding model. Treat them as missing so certification
# cannot be obtained by supplying a nominal string.
_INVALID_EVIDENCE_LABELS = frozenset({"", "none", "n/a", "na", "unknown", "not modeled", "not_modelled"})


@dataclass(frozen=True)
class ResearchEvidence:
    completed_trades: int
    train_period: Tuple[str, str]
    validation_period: Tuple[str, str]
    test_period: Tuple[str, str]
    regime_counts: Dict[str, int] = field(default_factory=dict)
    cost_model: str = ""
    slippage_model: str = ""
    funding_source: str = ""
    rejected_signals: int = 0
    rejected_outcomes_complete: bool = False
    bootstrap_seed: int | None = None
    code_commit: str = ""
    config_fingerprint: str = ""
    profit_factor: float | None = None
    expectancy: float | None = None
    max_drawdown_pct: float | None = None


@dataclass(frozen=True)
class ResearchDecision:
    approved: bool
    reasons: Tuple[str, ...]


def _parse_period_endpoint(value: object):
    """Parse ISO-8601 endpoints into one comparable UTC representation."""
    if not isinstance(value, str) or not value:
        return None

    try:
        if len(value) == 10:
            return datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _validate_periods(periods: list[Tuple[str, str]], reasons: list[str]) -> None:
    previous_end = None
    for period in periods:
        if not isinstance(period, (tuple, list)) or len(period) != 2:
            reasons.append("invalid_temporal_window")
            return
        start_raw, end_raw = period
        start = _parse_period_endpoint(start_raw)
        end = _parse_period_endpoint(end_raw)
        if start is None or end is None or start >= end:
            reasons.append("invalid_temporal_window")
            return
        if previous_end is not None and previous_end > start:
            reasons.append("overlapping_temporal_windows")
            return
        previous_end = end


def _evidence_label_present(value: object) -> bool:
    """Require a substantive execution-evidence label, not a placeholder."""
    return isinstance(value, str) and value.strip().lower() not in _INVALID_EVIDENCE_LABELS


def _finite_number(value: object) -> bool:
    """Require a real finite numeric value; booleans are not statistics."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value))


def evaluate_research_evidence(e: ResearchEvidence) -> ResearchDecision:
    reasons = []
    if not isinstance(e.completed_trades, int) or isinstance(e.completed_trades, bool) or e.completed_trades < 0:
        reasons.append("invalid_completed_trade_count")
    elif e.completed_trades < MIN_COMPLETED_TRADES:
        reasons.append("insufficient_completed_trades")
    if not _evidence_label_present(e.cost_model):
        reasons.append("missing_cost_model")
    if not _evidence_label_present(e.slippage_model):
        reasons.append("missing_slippage_model")
    if not _evidence_label_present(e.funding_source):
        reasons.append("missing_funding_source")
    if not e.code_commit or not e.config_fingerprint:
        reasons.append("missing_reproducibility_fingerprint")
    if e.bootstrap_seed is None:
        reasons.append("missing_bootstrap_seed")
    if not isinstance(e.rejected_signals, int) or isinstance(e.rejected_signals, bool) or e.rejected_signals < 0:
        reasons.append("invalid_rejected_signal_count")
    if not e.rejected_outcomes_complete:
        reasons.append("missing_rejected_signal_outcomes")

    regime_valid = (
        bool(e.regime_counts)
        and all(
            isinstance(count, int) and not isinstance(count, bool) and count >= 0
            for count in e.regime_counts.values()
        )
        and sum(e.regime_counts.values()) > 0
    )
    if not regime_valid:
        reasons.append("missing_regime_coverage")
    elif isinstance(e.completed_trades, int) and not isinstance(e.completed_trades, bool):
        regime_total = sum(e.regime_counts.values())
        if regime_total != e.completed_trades:
            reasons.append("regime_trade_count_mismatch")

    if not _finite_number(e.profit_factor):
        reasons.append("missing_profit_factor")
    elif e.profit_factor < MIN_PROFIT_FACTOR:
        reasons.append("profit_factor_below_minimum")

    if not _finite_number(e.expectancy):
        reasons.append("missing_expectancy")
    elif e.expectancy <= MIN_EXPECTANCY:
        reasons.append("expectancy_not_positive")

    if not _finite_number(e.max_drawdown_pct):
        reasons.append("missing_max_drawdown")
    elif e.max_drawdown_pct < 0:
        reasons.append("invalid_max_drawdown")
    elif e.max_drawdown_pct >= MAX_DRAWDOWN_PCT:
        reasons.append("max_drawdown_above_limit")

    _validate_periods([e.train_period, e.validation_period, e.test_period], reasons)
    return ResearchDecision(approved=not reasons, reasons=tuple(dict.fromkeys(reasons)))
