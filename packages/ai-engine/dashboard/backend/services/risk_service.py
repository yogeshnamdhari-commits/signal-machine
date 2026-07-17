"""
Risk Service — Portfolio and exchange risk data aggregation.

Integrates with:
- MultiExchangePortfolioRiskEngine
- RiskGuardian
- RiskEngine
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional

from loguru import logger


class RiskService:
    """
    Aggregates risk data from all risk engines.
    Provides unified risk panel data.
    """

    def __init__(self) -> None:
        self._portfolio_risk: Dict[str, Any] = {
            "portfolio_risk_pct": 0.0,
            "exchange_risk": {},
            "symbol_risk": {},
            "sector_risk": {},
            "current_exposure": 0.0,
            "net_exposure": 0.0,
            "gross_exposure": 0.0,
            "var_95": 0.0,
            "cvar_95": 0.0,
            "drawdown_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "risk_of_ruin": 0.0,
            "margin_utilization_pct": 0.0,
            "risk_level": "NORMAL",
            "risk_alerts": [],
            "stress_tests": [],
        }
        self._returns: List[float] = []
        self._equity_history: List[float] = []
        self._peak_equity: float = 0.0

    def update_risk_state(
        self,
        equity: float,
        exposure: float,
        positions: List[Dict[str, Any]],
    ) -> None:
        """Update risk state with current portfolio data."""
        self._equity_history.append(equity)
        if len(self._equity_history) > 2000:
            self._equity_history = self._equity_history[-1000:]

        if equity > self._peak_equity:
            self._peak_equity = equity

        # Returns
        if len(self._equity_history) >= 2:
            prev = self._equity_history[-2]
            if prev > 0:
                ret = (equity - prev) / prev
                self._returns.append(ret)
                if len(self._returns) > 1000:
                    self._returns = self._returns[-500:]

        # Drawdown
        drawdown_pct = 0.0
        if self._peak_equity > 0:
            drawdown_pct = (self._peak_equity - equity) / self._peak_equity * 100

        self._portfolio_risk.update({
            "current_exposure": exposure,
            "gross_exposure": exposure,
            "net_exposure": exposure,  # Simplified
            "drawdown_pct": drawdown_pct,
            "max_drawdown_pct": max(self._portfolio_risk.get("max_drawdown_pct", 0), drawdown_pct),
            "timestamp": time.time(),
        })

        # Per-exchange and per-symbol risk
        exchange_risk: Dict[str, float] = {}
        symbol_risk: Dict[str, float] = {}
        for pos in positions:
            exch = pos.get("exchange", "unknown")
            sym = pos.get("symbol", "unknown")
            notional = pos.get("notional", abs(pos.get("quantity", 0) * pos.get("mark_price", 0)))
            exchange_risk[exch] = exchange_risk.get(exch, 0) + notional
            symbol_risk[sym] = symbol_risk.get(sym, 0) + notional

        self._portfolio_risk["exchange_risk"] = exchange_risk
        self._portfolio_risk["symbol_risk"] = symbol_risk

        # VaR and CVaR
        self._calculate_var()

        # Risk level
        self._determine_risk_level()

    def _calculate_var(self) -> None:
        """Calculate Value at Risk and Conditional VaR."""
        if len(self._returns) < 20:
            return

        sorted_returns = sorted(self._returns)
        idx_95 = int(len(sorted_returns) * 0.05)
        idx_99 = int(len(sorted_returns) * 0.01)

        self._portfolio_risk["var_95"] = abs(sorted_returns[idx_95]) * 100 if idx_95 < len(sorted_returns) else 0
        self._portfolio_risk["var_99"] = abs(sorted_returns[idx_99]) * 100 if idx_99 < len(sorted_returns) else 0

        # CVaR (Expected Shortfall)
        tail = sorted_returns[:max(idx_95, 1)]
        self._portfolio_risk["cvar_95"] = abs(sum(tail) / len(tail)) * 100 if tail else 0

    def _determine_risk_level(self) -> None:
        """Determine current risk level."""
        dd = self._portfolio_risk.get("drawdown_pct", 0)
        margin = self._portfolio_risk.get("margin_utilization_pct", 0)

        if dd > 15 or margin > 90:
            self._portfolio_risk["risk_level"] = "CRITICAL"
        elif dd > 10 or margin > 80:
            self._portfolio_risk["risk_level"] = "HIGH"
        elif dd > 5 or margin > 60:
            self._portfolio_risk["risk_level"] = "ELEVATED"
        else:
            self._portfolio_risk["risk_level"] = "NORMAL"

    def run_stress_test(self) -> List[Dict[str, Any]]:
        """Run portfolio stress tests."""
        results = []
        scenarios = [
            {"name": "Flash Crash -30%", "equity_shock": -0.30, "volatility_spike": 3.0},
            {"name": "Exchange Outage", "equity_shock": -0.05, "volatility_spike": 1.5},
            {"name": "Funding Spike", "equity_shock": -0.02, "volatility_spike": 1.2},
            {"name": "Correlation Breakdown", "equity_shock": -0.15, "volatility_spike": 2.5},
            {"name": "Liquidity Crisis", "equity_shock": -0.20, "volatility_spike": 2.0},
        ]

        for scenario in scenarios:
            equity = self._equity_history[-1] if self._equity_history else 10000
            shocked_equity = equity * (1 + scenario["equity_shock"])
            impact = shocked_equity - equity
            recovery_days = abs(impact) / max(equity * 0.001, 1)  # Assume 0.1% daily recovery

            results.append({
                "scenario": scenario["name"],
                "equity_impact": round(impact, 2),
                "equity_after": round(shocked_equity, 2),
                "drawdown_pct": abs(scenario["equity_shock"]) * 100,
                "volatility_multiplier": scenario["volatility_spike"],
                "estimated_recovery_days": round(recovery_days, 1),
                "risk_rating": "HIGH" if abs(scenario["equity_shock"]) > 0.15 else "MEDIUM" if abs(scenario["equity_shock"]) > 0.05 else "LOW",
            })

        self._portfolio_risk["stress_tests"] = results
        return results

    def get_risk_panel(self) -> Dict[str, Any]:
        """Get data for the risk management panel."""
        return {
            **self._portfolio_risk,
            "returns_count": len(self._returns),
            "equity_count": len(self._equity_history),
            "timestamp": time.time(),
        }

    def get_risk_heatmap_data(self) -> Dict[str, Any]:
        """Get risk heatmap data for visualization."""
        exchange_risk = self._portfolio_risk.get("exchange_risk", {})
        symbol_risk = self._portfolio_risk.get("symbol_risk", {})
        total = sum(exchange_risk.values()) or 1

        return {
            "exchanges": {
                k: {"value": v, "pct": round(v / total * 100, 2)}
                for k, v in exchange_risk.items()
            },
            "symbols": {
                k: {"value": v, "pct": round(v / total * 100, 2)}
                for k, v in symbol_risk.items()
            },
            "total_exposure": total,
        }
