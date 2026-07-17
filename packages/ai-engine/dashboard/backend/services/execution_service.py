"""
Execution Service — Order execution and routing data aggregation.

Integrates with:
- SmartOrderRouter
- ExecutionEngine
- FillManager
- OrderManager
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from collections import defaultdict

from loguru import logger


class ExecutionService:
    """
    Aggregates execution data for the dashboard.
    Tracks orders, fills, slippage, and routing decisions.
    """

    def __init__(self) -> None:
        self._orders_submitted: int = 0
        self._orders_filled: int = 0
        self._orders_rejected: int = 0
        self._orders_cancelled: int = 0
        self._partial_fills: int = 0
        self._total_slippage_bps: float = 0.0
        self._total_execution_cost: float = 0.0
        self._fill_count: int = 0
        self._routing_decisions: List[Dict[str, Any]] = []
        self._venue_distribution: Dict[str, int] = defaultdict(int)
        self._recent_orders: List[Dict[str, Any]] = []
        self._latency_history: List[float] = []
        self._max_history = 2000

    def record_order_submitted(self, order: Dict[str, Any]) -> None:
        """Record an order submission."""
        self._orders_submitted += 1
        self._recent_orders.append({
            **order,
            "event": "submitted",
            "timestamp": time.time(),
        })
        if len(self._recent_orders) > self._max_history:
            self._recent_orders = self._recent_orders[-self._max_history // 2:]

    def record_order_filled(self, fill: Dict[str, Any]) -> None:
        """Record an order fill."""
        self._orders_filled += 1
        self._fill_count += 1

        slippage = fill.get("slippage_bps", 0)
        self._total_slippage_bps += slippage

        cost = fill.get("fee", 0)
        self._total_execution_cost += cost

        exchange = fill.get("exchange", "unknown")
        self._venue_distribution[exchange] += 1

        self._recent_orders.append({
            **fill,
            "event": "filled",
            "timestamp": time.time(),
        })

    def record_order_rejected(self, order: Dict[str, Any]) -> None:
        """Record an order rejection."""
        self._orders_rejected += 1
        self._recent_orders.append({
            **order,
            "event": "rejected",
            "timestamp": time.time(),
        })

    def record_order_cancelled(self, order: Dict[str, Any]) -> None:
        """Record an order cancellation."""
        self._orders_cancelled += 1
        self._recent_orders.append({
            **order,
            "event": "cancelled",
            "timestamp": time.time(),
        })

    def record_partial_fill(self, fill: Dict[str, Any]) -> None:
        """Record a partial fill."""
        self._partial_fills += 1

    def record_routing_decision(self, decision: Dict[str, Any]) -> None:
        """Record a routing decision from SmartOrderRouter."""
        self._routing_decisions.append({
            **decision,
            "timestamp": time.time(),
        })
        if len(self._routing_decisions) > self._max_history:
            self._routing_decisions = self._routing_decisions[-self._max_history // 2:]

        exchange = decision.get("exchange", "unknown")
        self._venue_distribution[exchange] += 1

    def record_latency(self, latency_ms: float) -> None:
        """Record execution latency."""
        self._latency_history.append(latency_ms)
        if len(self._latency_history) > 1000:
            self._latency_history = self._latency_history[-500:]

    def get_execution_panel(self) -> Dict[str, Any]:
        """Get data for the execution monitor panel."""
        fill_rate = (
            (self._orders_filled / max(self._orders_submitted, 1)) * 100
        )
        avg_slippage = (
            self._total_slippage_bps / max(self._fill_count, 1)
        )
        avg_latency = (
            sum(self._latency_history) / len(self._latency_history)
            if self._latency_history else 0
        )

        return {
            "orders_submitted": self._orders_submitted,
            "orders_filled": self._orders_filled,
            "orders_rejected": self._orders_rejected,
            "orders_cancelled": self._orders_cancelled,
            "partial_fills": self._partial_fills,
            "fill_rate": round(fill_rate, 2),
            "avg_slippage_bps": round(avg_slippage, 4),
            "total_execution_cost": round(self._total_execution_cost, 4),
            "venue_distribution": dict(self._venue_distribution),
            "avg_latency_ms": round(avg_latency, 2),
            "recent_orders": self._recent_orders[-50:],
            "recent_routing": self._routing_decisions[-50:],
            "timestamp": time.time(),
        }

    def get_routing_stats(self) -> Dict[str, Any]:
        """Get routing statistics."""
        routing_reasons: Dict[str, int] = defaultdict(int)
        for dec in self._routing_decisions:
            reason = dec.get("routing_reason", "unknown")
            routing_reasons[reason] += 1

        return {
            "total_routes": len(self._routing_decisions),
            "venue_distribution": dict(self._venue_distribution),
            "routing_reasons": dict(routing_reasons),
            "avg_score": sum(
                d.get("score", 0) for d in self._routing_decisions
            ) / max(len(self._routing_decisions), 1),
        }
