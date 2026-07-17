"""
Arbitrage Service — Arbitrage data aggregation.

Integrates with:
- ArbitrageEngine
- FundingArbitrage, BasisArbitrage, StatisticalArbitrage
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger


class ArbitrageService:
    """
    Aggregates arbitrage data for the dashboard panel.
    Tracks active, historical, and performance metrics.
    """

    def __init__(self) -> None:
        self._active_arbitrages: List[Dict[str, Any]] = []
        self._historical: List[Dict[str, Any]] = []
        self._metrics: Dict[str, Any] = {
            "total_scans": 0,
            "opportunities_found": 0,
            "opportunities_executed": 0,
            "total_expected_profit": 0.0,
            "total_realized_profit": 0.0,
            "avg_spread_bps": 0.0,
            "avg_funding_bps": 0.0,
            "avg_basis_bps": 0.0,
            "avg_confidence": 0.0,
            "win_rate": 0.0,
            "by_type": {
                "funding_arbitrage": {"count": 0, "profit": 0.0},
                "spread_arbitrage": {"count": 0, "profit": 0.0},
                "basis_arbitrage": {"count": 0, "profit": 0.0},
                "statistical_arbitrage": {"count": 0, "profit": 0.0},
            },
        }
        self._max_history = 5000

    def record_opportunity(self, opp: Dict[str, Any]) -> None:
        """Record a new arbitrage opportunity."""
        self._active_arbitrages.append({
            **opp,
            "detected_at": time.time(),
            "status": "active",
        })
        self._metrics["opportunities_found"] += 1

        arb_type = opp.get("arb_type", "unknown")
        if arb_type in self._metrics["by_type"]:
            self._metrics["by_type"][arb_type]["count"] += 1

    def record_execution(self, arb_id: str, result: Dict[str, Any]) -> None:
        """Record an arbitrage execution result."""
        profit = result.get("realized_profit", 0.0)
        self._metrics["opportunities_executed"] += 1
        self._metrics["total_realized_profit"] += profit

        arb_type = result.get("arb_type", "unknown")
        if arb_type in self._metrics["by_type"]:
            self._metrics["by_type"][arb_type]["profit"] += profit

        # Move from active to historical
        self._active_arbitrages = [
            a for a in self._active_arbitrages if a.get("id") != arb_id
        ]
        self._historical.append({
            **result,
            "closed_at": time.time(),
        })
        if len(self._historical) > self._max_history:
            self._historical = self._historical[-self._max_history // 2:]

        # Win rate
        wins = sum(1 for h in self._historical if h.get("realized_profit", 0) > 0)
        total = len(self._historical)
        self._metrics["win_rate"] = (wins / total * 100) if total > 0 else 0

    def record_scan(self) -> None:
        """Record a scan cycle."""
        self._metrics["total_scans"] += 1

    def get_arbitrage_panel(self) -> Dict[str, Any]:
        """Get data for the arbitrage panel."""
        # Calculate averages from historical
        if self._historical:
            spreads = [h.get("spread_bps", 0) for h in self._historical]
            fundings = [h.get("funding_bps", 0) for h in self._historical]
            bases = [h.get("basis_bps", 0) for h in self._historical]
            confs = [h.get("confidence", 0) for h in self._historical]

            self._metrics["avg_spread_bps"] = sum(spreads) / len(spreads) if spreads else 0
            self._metrics["avg_funding_bps"] = sum(fundings) / len(fundings) if fundings else 0
            self._metrics["avg_basis_bps"] = sum(bases) / len(bases) if bases else 0
            self._metrics["avg_confidence"] = sum(confs) / len(confs) if confs else 0

        return {
            "active_arbitrages": self._active_arbitrages[-50:],
            "active_count": len(self._active_arbitrages),
            "historical": self._historical[-100:],
            "historical_count": len(self._historical),
            "metrics": self._metrics,
            "timestamp": time.time(),
        }

    def get_by_type(self, arb_type: str) -> List[Dict[str, Any]]:
        """Get arbitrages by type."""
        return [
            a for a in self._active_arbitrages + self._historical
            if a.get("arb_type") == arb_type
        ]

    def get_performance_summary(self) -> Dict[str, Any]:
        """Get arbitrage performance summary."""
        return {
            "total_opportunities": self._metrics["opportunities_found"],
            "total_executed": self._metrics["opportunities_executed"],
            "execution_rate": (
                self._metrics["opportunities_executed"] /
                max(self._metrics["opportunities_found"], 1) * 100
            ),
            "total_profit": self._metrics["total_realized_profit"],
            "win_rate": self._metrics["win_rate"],
            "avg_profit_per_trade": (
                self._metrics["total_realized_profit"] /
                max(self._metrics["opportunities_executed"], 1)
            ),
            "by_type": self._metrics["by_type"],
        }
