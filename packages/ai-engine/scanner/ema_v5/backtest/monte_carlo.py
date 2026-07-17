"""
EMA_V5 Monte Carlo Simulation — Stress testing and confidence intervals.
Isolated from existing Monte Carlo implementations.
"""
from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

from loguru import logger

from .backtest_engine import EMAv5BacktestResult


class EMAv5MonteCarlo:
    """Monte Carlo simulation for EMA_V5 strategy stress testing."""

    def __init__(self, n_simulations: int = 1000, seed: Optional[int] = None) -> None:
        self.n_simulations = n_simulations
        if seed is not None:
            random.seed(seed)

    def simulate(
        self,
        result: EMAv5BacktestResult,
        initial_balance: float = 10_000.0,
        confidence_level: float = 0.95,
    ) -> Dict[str, Any]:
        """Run Monte Carlo simulation by resampling trade outcomes.
        
        Reshuffles trade order to test path dependency.
        """
        if not result.trades:
            return {"error": "No trades to simulate"}

        pnls = [t.pnl for t in result.trades]
        n_trades = len(pnls)

        # Run simulations
        final_balances = []
        max_drawdowns = []
        min_balances = []

        for _ in range(self.n_simulations):
            # Resample with replacement
            sampled = random.choices(pnls, k=n_trades)

            # Compute equity curve
            equity = [initial_balance]
            for pnl in sampled:
                equity.append(equity[-1] + pnl)

            final_balances.append(equity[-1])

            # Max drawdown
            peak = initial_balance
            max_dd = 0
            min_bal = initial_balance
            for eq in equity:
                peak = max(peak, eq)
                dd = (peak - eq) / peak * 100 if peak > 0 else 0
                max_dd = max(max_dd, dd)
                min_bal = min(min_bal, eq)

            max_drawdowns.append(max_dd)
            min_balances.append(min_bal)

        # Sort for percentiles
        final_balances.sort()
        max_drawdowns.sort()
        min_balances.sort()

        n = len(final_balances)
        alpha = (1 - confidence_level) / 2

        # Confidence intervals
        ci_low = final_balances[int(n * alpha)]
        ci_high = final_balances[int(n * (1 - alpha))]

        # Statistics
        mean_final = sum(final_balances) / n
        median_final = final_balances[n // 2]
        std_final = (sum((x - mean_final) ** 2 for x in final_balances) / n) ** 0.5

        # Probability of profit
        prob_profit = sum(1 for x in final_balances if x > initial_balance) / n * 100

        # Probability of ruin (balance < 50% of initial)
        ruin_threshold = initial_balance * 0.5
        prob_ruin = sum(1 for x in final_balances if x < ruin_threshold) / n * 100

        # Worst case
        worst_case = final_balances[0]
        best_case = final_balances[-1]

        # Average max drawdown
        avg_max_dd = sum(max_drawdowns) / n
        worst_max_dd = max_drawdowns[-1]

        return {
            "n_simulations": self.n_simulations,
            "n_trades": n_trades,
            "initial_balance": initial_balance,
            "confidence_level": confidence_level,
            "statistics": {
                "mean_final_balance": round(mean_final, 2),
                "median_final_balance": round(median_final, 2),
                "std_final_balance": round(std_final, 2),
                "mean_return_pct": round((mean_final - initial_balance) / initial_balance * 100, 2),
            },
            "confidence_interval": {
                "low": round(ci_low, 2),
                "high": round(ci_high, 2),
                "low_return_pct": round((ci_low - initial_balance) / initial_balance * 100, 2),
                "high_return_pct": round((ci_high - initial_balance) / initial_balance * 100, 2),
            },
            "probabilities": {
                "prob_profit": round(prob_profit, 1),
                "prob_ruin": round(prob_ruin, 1),
            },
            "extremes": {
                "worst_case": round(worst_case, 2),
                "best_case": round(best_case, 2),
                "worst_drawdown_pct": round(worst_max_dd, 2),
                "avg_drawdown_pct": round(avg_max_dd, 2),
            },
            "percentiles": {
                "p5": round(final_balances[int(n * 0.05)], 2),
                "p25": round(final_balances[int(n * 0.25)], 2),
                "p50": round(final_balances[int(n * 0.50)], 2),
                "p75": round(final_balances[int(n * 0.75)], 2),
                "p95": round(final_balances[int(n * 0.95)], 2),
            },
        }

    def sensitivity_test(
        self,
        result: EMAv5BacktestResult,
        trade_removal_pct: float = 0.1,
        n_simulations: int = 500,
    ) -> Dict[str, Any]:
        """Test strategy sensitivity by removing random trades.
        
        Removes trade_removal_pct of trades and measures impact.
        """
        if not result.trades:
            return {"error": "No trades to test"}

        pnls = [t.pnl for t in result.trades]
        n_remove = int(len(pnls) * trade_removal_pct)
        original_pnl = sum(pnls)

        impacts = []
        for _ in range(n_simulations):
            # Randomly remove trades
            remaining = list(pnls)
            for _ in range(n_remove):
                if remaining:
                    idx = random.randint(0, len(remaining) - 1)
                    remaining.pop(idx)

            new_pnl = sum(remaining)
            impact = (new_pnl - original_pnl) / abs(original_pnl) * 100 if original_pnl != 0 else 0
            impacts.append(impact)

        impacts.sort()
        n = len(impacts)

        return {
            "trades_removed_pct": trade_removal_pct * 100,
            "original_pnl": round(original_pnl, 4),
            "avg_impact_pct": round(sum(impacts) / n, 2),
            "worst_impact_pct": round(impacts[0], 2),
            "best_impact_pct": round(impacts[-1], 2),
            "median_impact_pct": round(impacts[n // 2], 2),
            "verdict": "ROBUST" if abs(impacts[n // 2]) < 20 else "SENSITIVE",
        }
