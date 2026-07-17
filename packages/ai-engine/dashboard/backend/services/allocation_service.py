"""
Allocation Service — Capital allocation data aggregation.

Integrates with:
- CapitalAllocationEngine
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger


class AllocationService:
    """
    Aggregates capital allocation data for the dashboard.
    Tracks allocation decisions, models, and audit log.
    """

    def __init__(self) -> None:
        self._allocation_history: List[Dict[str, Any]] = []
        self._current_model: str = "institutional_weighted"
        self._capital_usage: float = 0.0
        self._risk_usage: float = 0.0
        self._leverage_usage: float = 0.0
        self._kelly_fraction: float = 0.0
        self._vol_target: float = 0.0
        self._rejections: List[Dict[str, Any]] = []
        self._max_history = 5000

    def record_allocation(self, allocation: Dict[str, Any]) -> None:
        """Record an allocation decision."""
        self._allocation_history.append({
            **allocation,
            "recorded_at": time.time(),
        })
        if len(self._allocation_history) > self._max_history:
            self._allocation_history = self._allocation_history[-self._max_history // 2:]

        self._capital_usage = allocation.get("capital_pct", 0)
        self._leverage_usage = allocation.get("leverage", 0)
        self._kelly_fraction = allocation.get("kelly_fraction", 0)

    def record_rejection(self, rejection: Dict[str, Any]) -> None:
        """Record an allocation rejection."""
        self._rejections.append({
            **rejection,
            "timestamp": time.time(),
        })
        if len(self._rejections) > 1000:
            self._rejections = self._rejections[-500:]

    def set_model(self, model: str) -> None:
        """Set current allocation model."""
        self._current_model = model

    def get_allocation_panel(self) -> Dict[str, Any]:
        """Get data for the capital allocation panel."""
        return {
            "allocation_model": self._current_model,
            "recent_allocations": self._allocation_history[-50:],
            "total_allocations": len(self._allocation_history),
            "capital_usage_pct": round(self._capital_usage * 100, 2),
            "risk_usage_pct": round(self._risk_usage * 100, 2),
            "leverage_usage": round(self._leverage_usage, 2),
            "kelly_fraction": round(self._kelly_fraction, 4),
            "volatility_target": round(self._vol_target, 4),
            "rejections": self._rejections[-20:],
            "rejection_count": len(self._rejections),
            "score_weighting": {
                "order_flow": 0.25,
                "institutional": 0.20,
                "regime": 0.15,
                "momentum": 0.15,
                "volume": 0.10,
                "imbalance": 0.10,
                "fake_breakout": 0.05,
            },
            "timestamp": time.time(),
        }

    def get_allocation_audit(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get allocation audit log."""
        return self._allocation_history[-limit:]

    def get_portfolio_allocation(self) -> Dict[str, Any]:
        """Get portfolio allocation breakdown for charts."""
        # Aggregate from allocation history
        exchange_alloc: Dict[str, float] = {}
        symbol_alloc: Dict[str, float] = {}

        for alloc in self._allocation_history[-200:]:
            exchange = alloc.get("exchange", "unknown")
            symbol = alloc.get("symbol", "unknown")
            capital = alloc.get("capital_usd", 0)

            exchange_alloc[exchange] = exchange_alloc.get(exchange, 0) + capital
            symbol_alloc[symbol] = symbol_alloc.get(symbol, 0) + capital

        total_capital = sum(exchange_alloc.values()) or 1

        return {
            "exchange_allocation": {
                k: {"value": v, "pct": round(v / total_capital * 100, 2)}
                for k, v in exchange_alloc.items()
            },
            "symbol_allocation": {
                k: {"value": v, "pct": round(v / total_capital * 100, 2)}
                for k, v in symbol_alloc.items()
            },
            "target_allocation": {
                "binance": 40,
                "bybit": 30,
                "delta": 20,
                "okx": 10,
            },
            "total_capital": total_capital,
        }
