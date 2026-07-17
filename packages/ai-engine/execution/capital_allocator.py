"""
Capital Allocation Engine — Institutional-grade portfolio weighting and risk sizing.
"""
from __future__ import annotations

import asyncio
import csv
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


class AllocationModel(str, Enum):
    FIXED_FRACTIONAL = "fixed_fractional"
    VOLATILITY_TARGETING = "volatility_targeting"
    INSTITUTIONAL_WEIGHTED = "institutional_weighted"
    KELLY_FRACTION = "kelly_fraction"
    RISK_PARITY = "risk_parity"


@dataclass
class AllocationRequest:
    symbol: str
    exchange: str
    signal_score: float  # 0-100
    confidence: float    # 0-1
    volatility: float    # Annualized std dev or ATR-based %
    market_regime: str   # bull, bear, range, volatile
    portfolio_equity: float
    is_arbitrage: bool = False
    # Optional metrics for advanced models
    win_rate: float = 0.50
    profit_factor: float = 1.0
    expectancy: float = 0.0
    drawdown: float = 0.0
    avg_win_pct: float = 0.02
    avg_loss_pct: float = 0.01


@dataclass
class AllocationResult:
    allocation_pct: float
    capital_usd: float
    leverage: float
    position_size: float
    risk_pct: float
    reason: str
    model_used: str
    timestamp: float = field(default_factory=time.time)


class CapitalAllocationEngine:
    """
    Determines the optimal capital deployment for a signal based on multi-factor edge.
    """

    LOG_PATH = Path("data/reports/allocation_log.csv")

    def __init__(self, config: Dict[str, Any] = None) -> None:
        self.config = config or {}
        # Hard Limits
        self.max_portfolio_risk = 0.10  # 10%
        self.max_symbol_risk = 0.02     # 2%
        self.max_exchange_risk = 0.05   # 5%
        self.max_sector_risk = 0.04     # 4%
        self.max_leverage_per_trade = 5.0
        
        # Exchange Health metrics for distribution
        self._exchange_metrics = {
            "binance": {"weight": 0.4, "latency": 30},
            "bybit": {"weight": 0.3, "latency": 50},
            "delta": {"weight": 0.2, "latency": 80},
            "okx": {"weight": 0.1, "latency": 45}
        }
        
        # Performance Tracking for Feedback Loop
        self._performance_stats: Dict[str, Dict] = {} # symbol -> stats

        # Initialize CSV log
        self.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not self.LOG_PATH.exists():
            with open(self.LOG_PATH, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    "timestamp", "symbol", "exchange", "score", "confidence",
                    "allocation_pct", "capital", "leverage", "risk_pct", "reason"
                ])

    async def allocate(self, request: AllocationRequest, model: AllocationModel = AllocationModel.INSTITUTIONAL_WEIGHTED) -> AllocationResult:
        """Main entry point for capital allocation."""
        t0 = time.perf_counter()

        # 1. Base Allocation based on selected model
        if model == AllocationModel.FIXED_FRACTIONAL:
            res = self._fixed_fractional(request)
        elif model == AllocationModel.VOLATILITY_TARGETING:
            res = self._volatility_targeting(request)
        elif model == AllocationModel.KELLY_FRACTION:
            res = self._kelly_fraction(request)
        elif model == AllocationModel.RISK_PARITY:
            res = self._risk_parity(request)
        else:
            res = self._institutional_weighted(request)

        # 2. Dynamic Leverage Adjustment
        res.leverage = self._calculate_dynamic_leverage(request)

        # 3. Market Regime Adjustment
        self._apply_regime_adjustment(request, res)

        # 4. Exchange Allocation Adjustment
        self._apply_exchange_distribution(request, res)

        # 5. Performance Feedback Loop
        self._apply_performance_feedback(request.symbol, res)

        # 6. Apply Portfolio Constraints (Hard Limits)
        self._enforce_constraints(request, res)

        # Finalize position size (Notional)
        res.position_size = (res.capital_usd * res.leverage)

        # 7. Audit Logging
        self._log_allocation(request, res)

        latency = (time.perf_counter() - t0) * 1000
        logger.debug(f"[allocator] Decision for {request.symbol} in {latency:.2f}ms")

        return res

    # ─── Allocation Models ──────────────────────────────────────────────────

    def _fixed_fractional(self, req: AllocationRequest) -> AllocationResult:
        risk_pct = 0.01 # 1% default risk
        capital = req.portfolio_equity * risk_pct
        return AllocationResult(
            allocation_pct=risk_pct,
            capital_usd=capital,
            leverage=1.0,
            position_size=0,
            risk_pct=risk_pct,
            reason="Fixed Fractional (1%)",
            model_used="fixed_fractional"
        )

    def _volatility_targeting(self, req: AllocationRequest) -> AllocationResult:
        # Target 1% risk per trade, adjusted by relative volatility
        target_vol = 0.02 
        actual_vol = max(req.volatility, 0.001)
        multiplier = target_vol / actual_vol
        
        allocation_pct = 0.02 * multiplier
        capital = req.portfolio_equity * allocation_pct
        
        return AllocationResult(
            allocation_pct=allocation_pct,
            capital_usd=capital,
            leverage=1.0,
            position_size=0,
            risk_pct=allocation_pct,
            reason=f"Vol Targeting (multiplier: {multiplier:.2f})",
            model_used="volatility_targeting"
        )

    def _institutional_weighted(self, req: AllocationRequest) -> AllocationResult:
        score = req.signal_score
        if score >= 90:
            pct = 0.05
        elif score >= 80:
            pct = 0.03
        elif score >= 70:
            pct = 0.02
        elif score >= 60:
            pct = 0.01
        else:
            pct = 0.0
            
        return AllocationResult(
            allocation_pct=pct,
            capital_usd=req.portfolio_equity * pct,
            leverage=1.0,
            position_size=0,
            risk_pct=pct,
            reason=f"Institutional Score: {score}",
            model_used="institutional_weighted"
        )

    def _kelly_fraction(self, req: AllocationRequest) -> AllocationResult:
        # f* = p - (q/b)
        # Half Kelly default
        p = req.win_rate
        q = 1 - p
        b = req.avg_win_pct / max(req.avg_loss_pct, 0.0001)
        
        kelly = p - (q / b) if b > 0 else 0
        final_kelly = max(0, kelly * 0.5)
        
        # Hard cap at 10% for safety
        final_kelly = min(final_kelly, 0.10)
        
        return AllocationResult(
            allocation_pct=final_kelly,
            capital_usd=req.portfolio_equity * final_kelly,
            leverage=1.0,
            position_size=0,
            risk_pct=final_kelly,
            reason=f"Half Kelly (b={b:.2f})",
            model_used="kelly_fraction"
        )

    def _risk_parity(self, req: AllocationRequest) -> AllocationResult:
        # Equalize contribution based on inverse volatility proxy
        inv_vol = 1.0 / max(req.volatility, 0.001)
        allocation_pct = (inv_vol * 0.0002) 
        
        return AllocationResult(
            allocation_pct=allocation_pct,
            capital_usd=req.portfolio_equity * allocation_pct,
            leverage=1.0,
            position_size=0,
            risk_pct=allocation_pct,
            reason="Risk Parity Proxy",
            model_used="risk_parity"
        )

    # ─── Dynamic Engines ────────────────────────────────────────────────────

    def _calculate_dynamic_leverage(self, req: AllocationRequest) -> float:
        """Determines leverage based on score, volatility and drawdown."""
        score = req.signal_score
        
        if score >= 90 and req.volatility < 0.02:
            lev = 5.0
        elif score >= 80:
            lev = 3.0
        elif score >= 70:
            lev = 2.0
        elif score >= 60:
            lev = 1.0
        else:
            lev = 1.0
            
        if req.drawdown > 0.05: # Reduce on 5% drawdown
            lev *= 0.5
            
        return min(lev, self.max_leverage_per_trade)

    def _apply_regime_adjustment(self, req: AllocationRequest, res: AllocationResult) -> None:
        """Adjusts capital based on market environment."""
        regime = req.market_regime.lower()
        mult = 1.0
        
        if regime == "bull":
            mult = 1.2
        elif regime == "bear":
            mult = 0.7
        elif regime == "range":
            mult = 0.8
            res.leverage *= 0.8
        elif regime == "volatile":
            mult = 0.6
            
        res.allocation_pct *= mult
        res.capital_usd *= mult
        res.risk_pct *= mult

    def _apply_exchange_distribution(self, req: AllocationRequest, res: AllocationResult) -> None:
        """Adjusts capital based on venue characteristics (Step 5)."""
        metrics = self._exchange_metrics.get(req.exchange.lower())
        if not metrics:
            return
            
        # Penalize slow venues
        if metrics['latency'] > 100:
            res.allocation_pct *= 0.8
            res.capital_usd *= 0.8
            res.reason += " | Latency Penalty"

    def _apply_performance_feedback(self, symbol: str, res: AllocationResult) -> None:
        """Increases/decreases allocation based on historical PF."""
        stats = self._performance_stats.get(symbol)
        if not stats:
            return
            
        pf = stats.get("profit_factor", 1.0)
        if pf > 2.0:
            res.allocation_pct *= 1.25
            res.capital_usd *= 1.25
            res.reason += " | Performance Bonus (PF > 2.0)"
        elif pf < 1.0:
            res.allocation_pct *= 0.50
            res.capital_usd *= 0.50
            res.reason += " | Performance Penalty (PF < 1.0)"

    def _enforce_constraints(self, req: AllocationRequest, res: AllocationResult) -> None:
        """Hard risk limits."""
        if res.risk_pct > self.max_symbol_risk:
            res.risk_pct = self.max_symbol_risk
            res.allocation_pct = res.risk_pct
            res.capital_usd = req.portfolio_equity * res.allocation_pct
            res.reason += " | Capped by max_symbol_risk"
            
        if res.allocation_pct <= 0 or req.signal_score < 60:
            res.allocation_pct = 0
            res.capital_usd = 0
            res.leverage = 0
            res.reason = "Rejected: Score < 60 or 0 allocation"

    # ─── Utility ────────────────────────────────────────────────────────────

    def _log_allocation(self, req: AllocationRequest, res: AllocationResult) -> None:
        """Audit log for allocation decisions."""
        try:
            with open(self.LOG_PATH, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    datetime.fromtimestamp(res.timestamp).isoformat(),
                    req.symbol,
                    req.exchange,
                    req.signal_score,
                    req.confidence,
                    f"{res.allocation_pct:.4f}",
                    f"{res.capital_usd:.2f}",
                    res.leverage,
                    f"{res.risk_pct:.4f}",
                    res.reason
                ])
        except Exception as e:
            logger.error(f"Failed to log allocation: {e}")

    def update_performance(self, symbol: str, profit_factor: float) -> None:
        """Update the internal feedback loop stats."""
        self._performance_stats[symbol] = {"profit_factor": profit_factor}

    def get_stats(self) -> Dict:
        return {
            "total_decisions": len(self._performance_stats),
            "max_portfolio_risk": self.max_portfolio_risk,
            "active_symbols": list(self._performance_stats.keys())
        }