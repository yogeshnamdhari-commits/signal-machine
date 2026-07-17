"""
Monte Carlo Engine — randomized simulation for strategy robustness testing.
Uses bootstrap resampling and trade permutation to estimate confidence intervals.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from loguru import logger


@dataclass
class MonteCarloConfig:
    n_simulations: int = 10_000
    confidence_levels: List[float] = field(default_factory=lambda: [0.05, 0.25, 0.50, 0.75, 0.95])
    initial_capital: float = 10_000
    max_drawdown_limit: float = 0.30  # 30%
    random_seed: Optional[int] = None


@dataclass
class SimulationResult:
    final_equity: float
    total_return: float
    max_drawdown: float
    sharpe_ratio: float
    win_rate: float
    profit_factor: float
    total_trades: int
    equity_curve: np.ndarray
    drawdown_curve: np.ndarray


@dataclass
class MonteCarloResult:
    n_simulations: int
    simulations: List[SimulationResult]
    # Distribution statistics
    mean_return: float
    median_return: float
    std_return: float
    percentile_returns: Dict[float, float]
    mean_sharpe: float
    mean_max_drawdown: float
    percentile_drawdowns: Dict[float, float]
    probability_of_profit: float
    probability_of_ruin: float  # probability of losing > max_drawdown_limit
    expected_shortfall: float  # CVaR at 5%
    best_case: SimulationResult
    worst_case: SimulationResult
    median_case: SimulationResult
    # Confidence intervals
    return_ci_95: Tuple[float, float]
    drawdown_ci_95: Tuple[float, float]


class MonteCarloEngine:
    """
    Monte Carlo simulation engine for strategy robustness testing.
    
    Methods:
    1. Bootstrap Resampling: Randomly resample trades with replacement
    2. Trade Permutation: Randomly reorder trade sequence
    3. Parameter Perturbation: Add noise to strategy parameters
    4. Bootstrap Equity Curve: Resample returns from equity curve
    
    Use cases:
    - Estimate confidence intervals for returns
    - Assess probability of ruin
    - Test strategy robustness to trade ordering
    - Generate stress-test scenarios
    """

    def __init__(self, config: Optional[MonteCarloConfig] = None) -> None:
        self.config = config or MonteCarloConfig()
        if self.config.random_seed is not None:
            np.random.seed(self.config.random_seed)

    async def initialize(self) -> None:
        """Initialize the Monte Carlo engine."""
        logger.info("MonteCarlo engine ready — {} simulations", self.config.n_simulations)

    # ── Bootstrap Resampling ─────────────────────────────────────

    async def bootstrap_trades(
        self,
        trade_pnls: List[float],
        trade_returns: Optional[List[float]] = None,
    ) -> MonteCarloResult:
        """
        Bootstrap resampling of individual trade PnLs.
        Resamples trades with replacement to create synthetic equity curves.
        """
        if not trade_pnls:
            return self._empty_result()

        pnls = np.array(trade_pnls)
        n_trades = len(pnls)
        simulations = []

        for _ in range(self.config.n_simulations):
            # Resample with replacement
            indices = np.random.choice(n_trades, size=n_trades, replace=True)
            sampled_pnls = pnls[indices]

            # Build equity curve
            equity = np.cumsum(sampled_pnls) + self.config.initial_capital
            equity = np.insert(equity, 0, self.config.initial_capital)

            # Calculate metrics
            sim = self._simulate_from_pnls(equity, sampled_pnls)
            simulations.append(sim)

        return self._build_result(simulations)

    # ── Trade Permutation ────────────────────────────────────────

    async def permute_trades(
        self,
        trade_pnls: List[float],
    ) -> MonteCarloResult:
        """
        Randomly permute trade order to test sensitivity to sequence.
        Tests if strategy performance depends on specific trade ordering.
        """
        if not trade_pnls:
            return self._empty_result()

        pnls = np.array(trade_pnls)
        n_trades = len(pnls)
        simulations = []

        for _ in range(self.config.n_simulations):
            # Shuffle trades
            permuted = np.random.permutation(pnls)
            equity = np.cumsum(permuted) + self.config.initial_capital
            equity = np.insert(equity, 0, self.config.initial_capital)

            sim = self._simulate_from_pnls(equity, permuted)
            simulations.append(sim)

        return self._build_result(simulations)

    # ── Bootstrap Equity Curve ───────────────────────────────────

    async def bootstrap_equity_curve(
        self,
        equity_curve: List[float],
        window_size: int = 20,
    ) -> MonteCarloResult:
        """
        Block bootstrap on equity curve returns.
        Preserves autocorrelation by resampling windows of returns.
        """
        if len(equity_curve) < 10:
            return self._empty_result()

        equity = np.array(equity_curve)
        returns = np.diff(equity) / equity[:-1]
        n_returns = len(returns)
        simulations = []

        for _ in range(self.config.n_simulations):
            # Block bootstrap
            n_blocks = int(np.ceil(n_returns / window_size))
            blocks = []
            for _ in range(n_blocks):
                start = np.random.randint(0, max(n_returns - window_size, 1))
                blocks.append(returns[start:start + window_size])
            sampled_returns = np.concatenate(blocks)[:n_returns]

            # Reconstruct equity curve
            sim_equity = np.cumprod(1 + sampled_returns) * equity[0]
            sim_equity = np.insert(sim_equity, 0, equity[0])

            sim = self._simulate_from_equity(sim_equity)
            simulations.append(sim)

        return self._build_result(simulations)

    # ── Stress Testing ───────────────────────────────────────────

    async def stress_test(
        self,
        trade_pnls: List[float],
        stress_scenarios: Optional[Dict[str, Dict]] = None,
    ) -> Dict[str, MonteCarloResult]:
        """
        Run Monte Carlo under various stress scenarios:
        - Normal: baseline
        - High slippage: increased costs
        - Win streak broken: worst-case sequence
        - Losing streak: consecutive losses
        - Black swan: extreme outlier losses
        """
        if not trade_pnls:
            return {}

        scenarios = stress_scenarios or {
            "normal": {"slippage_mult": 1.0, "fee_mult": 1.0},
            "high_slippage": {"slippage_mult": 3.0, "fee_mult": 2.0},
            "reduced_wins": {"win_reduction": 0.3},
            "black_swan": {"outlier_mult": 5.0, "outlier_prob": 0.05},
        }

        results = {}
        pnls = np.array(trade_pnls)

        for name, params in scenarios.items():
            modified_pnls = self._apply_stress(pnls.copy(), params)
            result = await self.bootstrap_trades(modified_pnls.tolist())
            results[name] = result
            logger.info("Stress test '{}': mean return={:.2f}%, P(profit)={:.1f}%",
                         name, result.mean_return * 100, result.probability_of_profit * 100)

        return results

    def _apply_stress(self, pnls: np.ndarray, params: Dict) -> np.ndarray:
        """Apply stress parameters to trade PnLs."""
        slippage_mult = params.get("slippage_mult", 1.0)
        fee_mult = params.get("fee_mult", 1.0)
        win_reduction = params.get("win_reduction", 0)
        outlier_mult = params.get("outlier_mult", 1.0)
        outlier_prob = params.get("outlier_prob", 0)

        # Apply slippage/fee increase
        if slippage_mult != 1.0 or fee_mult != 1.0:
            costs = pnls[pnls < 0]
            pnls[pnls < 0] = costs * slippage_mult

        # Reduce wins
        if win_reduction > 0:
            wins = pnls[pnls > 0]
            pnls[pnls > 0] = wins * (1 - win_reduction)

        # Black swan events
        if outlier_mult > 1.0 and outlier_prob > 0:
            mask = np.random.random(len(pnls)) < outlier_prob
            pnls[mask] = pnls[mask] * outlier_mult

        return pnls

    # ── Helper Methods ───────────────────────────────────────────

    def _simulate_from_pnls(self, equity: np.ndarray, pnls: np.ndarray) -> SimulationResult:
        """Calculate simulation metrics from equity curve and PnLs."""
        returns = np.diff(equity) / equity[:-1]
        returns = returns[~np.isnan(returns) & ~np.isinf(returns)]

        # Max drawdown
        peak = np.maximum.accumulate(equity)
        drawdown = (peak - equity) / peak
        max_dd = float(np.max(drawdown))

        # Sharpe
        if len(returns) > 1 and np.std(returns) > 0:
            sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(252 * 24 * 60 / 5))
        else:
            sharpe = 0

        # Win rate
        wins = np.sum(pnls > 0)
        total = len(pnls)
        win_rate = wins / total if total > 0 else 0

        # Profit factor
        gross_profit = float(np.sum(pnls[pnls > 0]))
        gross_loss = float(np.abs(np.sum(pnls[pnls < 0])))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        return SimulationResult(
            final_equity=float(equity[-1]),
            total_return=float((equity[-1] - equity[0]) / equity[0]),
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
            win_rate=win_rate,
            profit_factor=pf,
            total_trades=total,
            equity_curve=equity,
            drawdown_curve=drawdown,
        )

    def _simulate_from_equity(self, equity: np.ndarray) -> SimulationResult:
        """Calculate simulation metrics from equity curve."""
        returns = np.diff(equity) / equity[:-1]
        returns = returns[~np.isnan(returns) & ~np.isinf(returns)]

        peak = np.maximum.accumulate(equity)
        drawdown = (peak - equity) / peak
        max_dd = float(np.max(drawdown))

        if len(returns) > 1 and np.std(returns) > 0:
            sharpe = float(np.mean(returns) / np.std(returns) * np.sqrt(252 * 24 * 60 / 5))
        else:
            sharpe = 0

        return SimulationResult(
            final_equity=float(equity[-1]),
            total_return=float((equity[-1] - equity[0]) / equity[0]),
            max_drawdown=max_dd,
            sharpe_ratio=sharpe,
            win_rate=0,
            profit_factor=0,
            total_trades=len(returns),
            equity_curve=equity,
            drawdown_curve=drawdown,
        )

    def _build_result(self, simulations: List[SimulationResult]) -> MonteCarloResult:
        """Build aggregate Monte Carlo result from simulations."""
        returns = np.array([s.total_return for s in simulations])
        drawdowns = np.array([s.max_drawdown for s in simulations])
        sharpes = np.array([s.sharpe_ratio for s in simulations])

        # Percentile returns
        percentile_returns = {}
        for level in self.config.confidence_levels:
            percentile_returns[level] = float(np.percentile(returns, level * 100))

        # Percentile drawdowns
        percentile_drawdowns = {}
        for level in self.config.confidence_levels:
            percentile_drawdowns[level] = float(np.percentile(drawdowns, level * 100))

        # Probability of profit
        prob_profit = float(np.mean(returns > 0))

        # Probability of ruin
        prob_ruin = float(np.mean(drawdowns > self.config.max_drawdown_limit))

        # Expected Shortfall (CVaR at 5%)
        var_5 = np.percentile(returns, 5)
        tail = returns[returns <= var_5]
        es = float(np.mean(tail)) if len(tail) > 0 else var_5

        # Best, worst, median cases
        best_idx = int(np.argmax(returns))
        worst_idx = int(np.argmin(returns))
        median_idx = int(np.argsort(returns)[len(returns) // 2])

        return MonteCarloResult(
            n_simulations=len(simulations),
            simulations=simulations,
            mean_return=float(np.mean(returns)),
            median_return=float(np.median(returns)),
            std_return=float(np.std(returns)),
            percentile_returns=percentile_returns,
            mean_sharpe=float(np.mean(sharpes)),
            mean_max_drawdown=float(np.mean(drawdowns)),
            percentile_drawdowns=percentile_drawdowns,
            probability_of_profit=prob_profit,
            probability_of_ruin=prob_ruin,
            expected_shortfall=es,
            best_case=simulations[best_idx],
            worst_case=simulations[worst_idx],
            median_case=simulations[median_idx],
            return_ci_95=(float(np.percentile(returns, 2.5)), float(np.percentile(returns, 97.5))),
            drawdown_ci_95=(float(np.percentile(drawdowns, 2.5)), float(np.percentile(drawdowns, 97.5))),
        )

    def _empty_result(self) -> MonteCarloResult:
        """Return empty result."""
        return MonteCarloResult(
            n_simulations=0, simulations=[],
            mean_return=0, median_return=0, std_return=0,
            percentile_returns={}, mean_sharpe=0, mean_max_drawdown=0,
            percentile_drawdowns={}, probability_of_profit=0, probability_of_ruin=1,
            expected_shortfall=0,
            best_case=SimulationResult(0, 0, 0, 0, 0, 0, 0, np.array([]), np.array([])),
            worst_case=SimulationResult(0, 0, 0, 0, 0, 0, 0, np.array([]), np.array([])),
            median_case=SimulationResult(0, 0, 0, 0, 0, 0, 0, np.array([]), np.array([])),
            return_ci_95=(0, 0), drawdown_ci_95=(0, 0),
        )

    def summary(self, result: MonteCarloResult) -> str:
        """Generate human-readable Monte Carlo summary."""
        return (
            f"═══ Monte Carlo Analysis ({result.n_simulations:,} sims) ═══\n"
            f"─────────────────────────────────────\n"
            f"Mean Return: {result.mean_return * 100:+.2f}%\n"
            f"Median Return: {result.median_return * 100:+.2f}%\n"
            f"Std Dev: {result.std_return * 100:.2f}%\n"
            f"95% CI: [{result.return_ci_95[0] * 100:+.1f}%, {result.return_ci_95[1] * 100:+.1f}%]\n"
            f"─────────────────────────────────────\n"
            f"Mean Sharpe: {result.mean_sharpe:.2f}\n"
            f"Mean Max DD: {result.mean_max_drawdown * 100:.1f}%\n"
            f"DD 95% CI: [{result.drawdown_ci_95[0] * 100:.1f}%, {result.drawdown_ci_95[1] * 100:.1f}%]\n"
            f"─────────────────────────────────────\n"
            f"P(Profit): {result.probability_of_profit * 100:.1f}%\n"
            f"P(Ruin): {result.probability_of_ruin * 100:.1f}%\n"
            f"CVaR (5%): {result.expected_shortfall * 100:+.2f}%\n"
            f"─────────────────────────────────────\n"
            f"Best Case: {result.best_case.total_return * 100:+.1f}%\n"
            f"Worst Case: {result.worst_case.total_return * 100:+.1f}%\n"
            f"Median Case: {result.median_case.total_return * 100:+.1f}%\n"
        )
