"""
Walk-Forward Testing Engine — rolling window optimization for robust strategy validation.
Prevents overfitting by testing on out-of-sample data with periodic re-optimization.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger

from .backtester import BacktestConfig, BacktestEngine, BacktestResult


@dataclass
class WalkForwardConfig:
    in_sample_days: int = 60  # Training window
    out_sample_days: int = 20  # Testing window
    step_days: int = 20  # Step size between windows
    min_bars: int = 500  # Minimum bars required
    optimization_metric: str = "sharpe_ratio"  # Metric to optimize
    n_optimization_trials: int = 50  # Parameter search iterations
    param_bounds: Dict[str, Tuple[float, float]] = field(default_factory=lambda: {
        "stop_loss_atr_mult": (1.0, 4.0),
        "take_profit_atr_mult": (1.5, 6.0),
        "risk_per_trade_pct": (0.01, 0.05),
        "rsi_entry": (25, 40),
        "rsi_exit": (60, 80),
    })


@dataclass
class WindowResult:
    window_id: int
    in_sample_start: datetime
    in_sample_end: datetime
    out_sample_start: datetime
    out_sample_end: datetime
    best_params: Dict[str, Any]
    in_sample_result: BacktestResult
    out_sample_result: BacktestResult
    optimization_score: float
    is_degradation: bool = False
    degradation_pct: float = 0


@dataclass
class WalkForwardResult:
    symbol: str
    total_windows: int
    windows: List[WindowResult]
    combined_oos_pnl: float
    combined_oos_trades: int
    combined_oos_win_rate: float
    avg_oos_sharpe: float
    avg_oos_drawdown: float
    wfe: float  # Walk-Forward Efficiency
    robustness_score: float
    is_robust: bool


class WalkForwardEngine:
    """
    Walk-Forward Optimization (WFO) engine.
    
    Prevents overfitting by:
    1. Splitting data into in-sample (IS) and out-of-sample (OOS) windows
    2. Optimizing parameters on IS data
    3. Testing on OOS data
    4. Rolling forward and repeating
    5. Measuring consistency across all OOS periods
    
    Walk-Forward Efficiency (WFE):
    - OOS performance / IS performance ratio
    - > 0.5 is generally considered robust
    """

    def __init__(self, config: Optional[WalkForwardConfig] = None) -> None:
        self.config = config or WalkForwardConfig()
        self._backtest_config = BacktestConfig()
        self._backtest: Optional[BacktestEngine] = None

    async def initialize(self) -> None:
        """Initialize the walk-forward engine."""
        self._backtest = BacktestEngine(self._backtest_config)
        await self._backtest.initialize()
        logger.info("WalkForward engine ready — IS:{}d OOS:{}d step:{}d",
                     self.config.in_sample_days, self.config.out_sample_days,
                     self.config.step_days)

    async def run(
        self,
        symbol: str,
        data: pd.DataFrame,
        strategy_func: Callable[[Dict], Callable],
    ) -> WalkForwardResult:
        """
        Run walk-forward optimization.
        
        Args:
            symbol: Trading pair
            data: Full historical dataset
            strategy_func: Factory function that takes params and returns signal_func
                          e.g., def make_strategy(params): return lambda df, i: ...
        
        Returns:
            WalkForwardResult with per-window and aggregate results
        """
        if data.empty or len(data) < self.config.min_bars:
            logger.warning("Insufficient data for WFO: {} bars (need {})",
                           len(data), self.config.min_bars)
            return self._empty_result(symbol)

        windows = self._generate_windows(data)
        logger.info("WFO: {} windows for {}", len(windows), symbol)

        window_results: List[WindowResult] = []
        all_oos_trades = 0
        all_oos_pnl = 0.0
        all_oos_sharpes = []
        all_oos_drawdowns = []

        for i, (is_start, is_end, oos_start, oos_end) in enumerate(windows):
            logger.info("Window {}/{}: IS {} → {} | OOS {} → {}",
                         i + 1, len(windows),
                         is_start.strftime("%Y-%m-%d"), is_end.strftime("%Y-%m-%d"),
                         oos_start.strftime("%Y-%m-%d"), oos_end.strftime("%Y-%m-%d"))

            # Extract windows
            is_data = data[(data["open_time"] >= is_start) & (data["open_time"] <= is_end)]
            oos_data = data[(data["open_time"] >= oos_start) & (data["open_time"] <= oos_end)]

            if len(is_data) < 100 or len(oos_data) < 50:
                logger.warning("Window {} skipped — insufficient data", i)
                continue

            # Optimize on in-sample
            best_params, is_result = await self._optimize_window(
                symbol, is_data, strategy_func
            )

            # Test on out-of-sample
            strategy = strategy_func(best_params)
            oos_result = await self._backtest.run(symbol, oos_data, strategy)

            # Measure degradation
            is_score = getattr(is_result, self.config.optimization_metric, 0)
            oos_score = getattr(oos_result, self.config.optimization_metric, 0)
            degradation = (is_score - oos_score) / abs(is_score) if is_score != 0 else 0

            wr = WindowResult(
                window_id=i,
                in_sample_start=is_start,
                in_sample_end=is_end,
                out_sample_start=oos_start,
                out_sample_end=oos_end,
                best_params=best_params,
                in_sample_result=is_result,
                out_sample_result=oos_result,
                optimization_score=oos_score,
                is_degradation=degradation > 0.5,
                degradation_pct=degradation * 100,
            )
            window_results.append(wr)

            all_oos_trades += oos_result.total_trades
            all_oos_pnl += oos_result.net_pnl
            all_oos_sharpes.append(oos_result.sharpe_ratio)
            all_oos_drawdowns.append(oos_result.max_drawdown)

        result = self._aggregate_results(symbol, window_results, all_oos_pnl,
                                         all_oos_trades, all_oos_sharpes, all_oos_drawdowns)

        logger.info("WFO complete: {} — WFE={:.2f}, robust={}",
                     symbol, result.wfe, result.is_robust)
        return result

    def _generate_windows(
        self, data: pd.DataFrame
    ) -> List[Tuple[datetime, datetime, datetime, datetime]]:
        """Generate IS/OOS window pairs."""
        start = data["open_time"].iloc[0]
        end = data["open_time"].iloc[-1]
        windows = []

        current = start
        window_id = 0

        while True:
            is_start = current
            is_end = is_start + pd.Timedelta(days=self.config.in_sample_days)
            oos_start = is_end
            oos_end = oos_start + pd.Timedelta(days=self.config.out_sample_days)

            if oos_end > end:
                break

            windows.append((is_start, is_end, oos_start, oos_end))
            current += pd.Timedelta(days=self.config.step_days)
            window_id += 1

        return windows

    async def _optimize_window(
        self,
        symbol: str,
        data: pd.DataFrame,
        strategy_func: Callable,
    ) -> Tuple[Dict[str, Any], BacktestResult]:
        """Optimize parameters for a single in-sample window using random search."""
        best_score = -float("inf")
        best_params = {}
        best_result = None

        for trial in range(self.config.n_optimization_trials):
            # Sample random parameters
            params = self._sample_params()

            # Run backtest
            strategy = strategy_func(params)
            result = await self._backtest.run(symbol, data, strategy)

            # Score
            score = self._score_result(result)

            if score > best_score:
                best_score = score
                best_params = params
                best_result = result

        return best_params, best_result

    def _sample_params(self) -> Dict[str, Any]:
        """Sample random parameters within bounds."""
        params = {}
        for key, (low, high) in self.config.param_bounds.items():
            params[key] = np.random.uniform(low, high)
        return params

    def _score_result(self, result: BacktestResult) -> float:
        """Score a backtest result based on optimization metric."""
        if result.total_trades < 5:
            return -float("inf")

        metric = self.config.optimization_metric
        score = getattr(result, metric, 0)

        # Penalize excessive drawdown
        if result.max_drawdown > 25:
            score *= 0.5
        elif result.max_drawdown > 15:
            score *= 0.8

        # Reward consistency
        if result.win_rate > 40 and result.profit_factor > 1.2:
            score *= 1.1

        return score

    def _aggregate_results(
        self,
        symbol: str,
        windows: List[WindowResult],
        oos_pnl: float,
        oos_trades: int,
        oos_sharpes: List[float],
        oos_drawdowns: List[float],
    ) -> WalkForwardResult:
        """Aggregate results across all windows."""
        if not windows:
            return self._empty_result(symbol)

        # Walk-Forward Efficiency
        is_sharpes = [w.in_sample_result.sharpe_ratio for w in windows]
        oos_sharpes_arr = np.array(oos_sharpes) if oos_sharpes else np.array([0])
        is_sharpes_arr = np.array(is_sharpes) if is_sharpes else np.array([1])

        avg_is = np.mean(is_sharpes_arr)
        avg_oos = np.mean(oos_sharpes_arr)
        wfe = avg_oos / avg_is if avg_is > 0 else 0

        # Robustness score
        degradation_count = sum(1 for w in windows if w.is_degradation)
        degradation_ratio = degradation_count / len(windows) if windows else 1
        robustness = max(0, 1 - degradation_ratio) * min(wfe, 1)

        # Win rate
        total_wins = sum(w.out_sample_result.win_count for w in windows)
        win_rate = (total_wins / oos_trades * 100) if oos_trades > 0 else 0

        return WalkForwardResult(
            symbol=symbol,
            total_windows=len(windows),
            windows=windows,
            combined_oos_pnl=oos_pnl,
            combined_oos_trades=oos_trades,
            combined_oos_win_rate=win_rate,
            avg_oos_sharpe=float(np.mean(oos_sharpes_arr)),
            avg_oos_drawdown=float(np.mean(oos_drawdowns)) if oos_drawdowns else 0,
            wfe=wfe,
            robustness_score=robustness,
            is_robust=wfe > 0.5 and robustness > 0.6,
        )

    def _empty_result(self, symbol: str) -> WalkForwardResult:
        """Return empty result for insufficient data."""
        return WalkForwardResult(
            symbol=symbol, total_windows=0, windows=[],
            combined_oos_pnl=0, combined_oos_trades=0,
            combined_oos_win_rate=0, avg_oos_sharpe=0,
            avg_oos_drawdown=0, wfe=0, robustness_score=0, is_robust=False,
        )

    def get_best_robust_params(self, result: WalkForwardResult) -> Dict[str, Any]:
        """Extract the most robust parameter set from WFO results."""
        if not result.windows:
            return {}

        # Weight by OOS performance
        scored = []
        for w in result.windows:
            score = w.out_sample_result.sharpe_ratio
            if not w.is_degradation:
                scored.append((score, w.best_params))

        if not scored:
            # Fallback: use window with least degradation
            scored = [(1 / max(w.degradation_pct, 1), w.best_params) for w in result.windows]

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[0][1]

    def summary(self, result: WalkForwardResult) -> str:
        """Generate human-readable WFO summary."""
        lines = [
            f"═══ Walk-Forward Analysis: {result.symbol} ═══",
            f"Windows: {result.total_windows}",
            f"─────────────────────────────────────",
            f"OOS PnL: ${result.combined_oos_pnl:,.2f}",
            f"OOS Trades: {result.combined_oos_trades}",
            f"OOS Win Rate: {result.combined_oos_win_rate:.1f}%",
            f"Avg OOS Sharpe: {result.avg_oos_sharpe:.2f}",
            f"Avg OOS Drawdown: {result.avg_oos_drawdown:.1f}%",
            f"─────────────────────────────────────",
            f"Walk-Forward Efficiency: {result.wfe:.2f}",
            f"Robustness Score: {result.robustness_score:.2f}",
            f"Is Robust: {'✅ YES' if result.is_robust else '❌ NO'}",
            f"─────────────────────────────────────",
        ]

        for w in result.windows:
            status = "⚠️ DEGRADED" if w.is_degradation else "✅ OK"
            lines.append(
                f"  W{w.window_id + 1}: IS Sharpe={w.in_sample_result.sharpe_ratio:.2f} "
                f"→ OOS Sharpe={w.out_sample_result.sharpe_ratio:.2f} "
                f"({w.degradation_pct:.0f}% deg) {status}"
            )

        return "\n".join(lines)
