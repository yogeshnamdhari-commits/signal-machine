"""Canonical real-trade tape semantics used by live flow components."""
from __future__ import annotations

from typing import Any, Dict


REAL_SOURCE_VALUES = frozenset({"binance"})
REAL_INTERNAL_SOURCES = frozenset({"rest_trades"})
SYNTHETIC_SOURCES = frozenset({"prefetch_synthetic", "ticker_arr"})


def is_real_trade(trade: Dict[str, Any]) -> bool:
    """Return True only for exchange-derived trades eligible for live flow."""
    if not isinstance(trade, dict):
        return False
    source = str(trade.get("source", "") or "").lower()
    internal_source = str(trade.get("_source", "") or "").lower()
    if source in SYNTHETIC_SOURCES or internal_source in SYNTHETIC_SOURCES:
        return False
    return source in REAL_SOURCE_VALUES or internal_source in REAL_INTERNAL_SOURCES


def _trade_timestamp(trade: Dict[str, Any]) -> float:
    value = trade.get("trade_time", trade.get("time", trade.get("T", 0)))
    try:
        ts = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return ts / 1000.0 if ts > 1e10 else ts


def has_recent_real_trade(
    symbol_data: Dict[str, Any],
    *,
    now: float,
    max_age: float = 60.0,
) -> bool:
    """Return True when the symbol has at least one fresh real exchange trade."""
    trades = symbol_data.get("trades", []) if isinstance(symbol_data, dict) else []
    for trade in reversed(trades):
        if not is_real_trade(trade):
            continue
        ts = _trade_timestamp(trade)
        if ts > 0 and 0 <= now - ts <= max_age:
            return True
    return False


def filter_real_trades(trades: list[Dict[str, Any]]) -> list[Dict[str, Any]]:
    """Filter a trade buffer to exchange-derived observations only."""
    return [trade for trade in trades if is_real_trade(trade)]
