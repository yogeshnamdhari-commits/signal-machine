"""
Exchange Service — Multi-exchange data aggregation.

Integrates with:
- BinanceAdapter, BybitAdapter, OKXAdapter, DeltaAdapter
- SmartOrderRouter
- DataBridge
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger


class ExchangeService:
    """
    Aggregates data from all exchange adapters.
    Provides unified exchange panel data.
    """

    SUPPORTED_EXCHANGES = ["binance", "bybit", "okx", "delta"]

    def __init__(self) -> None:
        self._exchange_states: Dict[str, Dict[str, Any]] = {}
        self._initialize_exchanges()

    def _initialize_exchanges(self) -> None:
        """Initialize exchange state tracking."""
        for name in self.SUPPORTED_EXCHANGES:
            self._exchange_states[name] = {
                "name": name,
                "balance": 0.0,
                "available_margin": 0.0,
                "used_margin": 0.0,
                "open_positions": 0,
                "open_orders": 0,
                "funding_paid": 0.0,
                "funding_received": 0.0,
                "latency_ms": 0.0,
                "avg_latency_ms": 0.0,
                "p99_latency_ms": 0.0,
                "api_status": "disconnected",
                "ws_status": "disconnected",
                "error_count": 0,
                "reconnect_count": 0,
                "health_score": 0.0,
                "last_update": 0.0,
                "total_requests": 0,
                "total_failures": 0,
                "uptime_pct": 100.0,
            }

    def update_exchange_state(
        self, exchange: str, data: Dict[str, Any]
    ) -> None:
        """Update state for an exchange."""
        if exchange in self._exchange_states:
            self._exchange_states[exchange].update(data)
            self._exchange_states[exchange]["last_update"] = time.time()

    def update_latency(self, exchange: str, latency_ms: float) -> None:
        """Update latency for an exchange."""
        if exchange in self._exchange_states:
            state = self._exchange_states[exchange]
            state["latency_ms"] = latency_ms
            # Exponential moving average
            alpha = 0.1
            state["avg_latency_ms"] = (
                alpha * latency_ms + (1 - alpha) * state.get("avg_latency_ms", latency_ms)
            )
            # Track p99 (simplified)
            if latency_ms > state.get("p99_latency_ms", 0):
                state["p99_latency_ms"] = latency_ms

    def record_error(self, exchange: str) -> None:
        """Record an error for an exchange."""
        if exchange in self._exchange_states:
            self._exchange_states[exchange]["error_count"] += 1
            self._exchange_states[exchange]["total_failures"] += 1

    def record_reconnect(self, exchange: str) -> None:
        """Record a reconnect for an exchange."""
        if exchange in self._exchange_states:
            self._exchange_states[exchange]["reconnect_count"] += 1

    def set_connected(self, exchange: str, connected: bool) -> None:
        """Set connection status for an exchange."""
        if exchange in self._exchange_states:
            state = self._exchange_states[exchange]
            state["api_status"] = "connected" if connected else "disconnected"
            state["ws_status"] = "connected" if connected else "disconnected"
            state["total_requests"] += 1

    def calculate_health_score(self, exchange: str) -> float:
        """Calculate health score for an exchange (0-100)."""
        state = self._exchange_states.get(exchange, {})
        score = 100.0

        # Deduct for errors
        error_rate = state.get("error_count", 0) / max(state.get("total_requests", 1), 1)
        score -= min(40, error_rate * 100)

        # Deduct for high latency
        latency = state.get("avg_latency_ms", 0)
        if latency > 100:
            score -= min(30, (latency - 100) / 10)

        # Deduct for disconnection
        if state.get("api_status") != "connected":
            score -= 30

        # Deduct for reconnects
        reconnects = state.get("reconnect_count", 0)
        if reconnects > 5:
            score -= min(20, reconnects)

        result = max(0, min(100, score))
        if exchange in self._exchange_states:
            self._exchange_states[exchange]["health_score"] = result
        return result

    def get_exchange_panel(self) -> Dict[str, Any]:
        """Get data for the multi-exchange panel."""
        exchanges = {}
        for name, state in self._exchange_states.items():
            self.calculate_health_score(name)
            exchanges[name] = {
                **state,
                "last_update": state.get("last_update", 0),
            }

        return {
            "exchanges": exchanges,
            "total_exchanges": len(self.SUPPORTED_EXCHANGES),
            "connected_count": sum(
                1 for s in self._exchange_states.values()
                if s.get("api_status") == "connected"
            ),
            "avg_health": sum(
                s.get("health_score", 0) for s in self._exchange_states.values()
            ) / max(len(self._exchange_states), 1),
            "timestamp": time.time(),
        }

    def get_all_latencies(self) -> Dict[str, float]:
        """Get latency for all exchanges."""
        return {
            name: state.get("avg_latency_ms", 0)
            for name, state in self._exchange_states.items()
        }

    def get_exchange(self, name: str) -> Optional[Dict[str, Any]]:
        """Get state for a specific exchange."""
        return self._exchange_states.get(name)
