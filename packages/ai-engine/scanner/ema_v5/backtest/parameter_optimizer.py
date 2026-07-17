"""
EMA_V5 Parameter Optimizer — Grid search and optimization of strategy parameters.
Isolated from existing optimizer.
"""
from __future__ import annotations

import itertools
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from loguru import logger

from .backtest_engine import EMAv5BacktestEngine, EMAv5BacktestConfig, EMAv5BacktestResult


class EMAv5ParameterOptimizer:
    """Grid search optimizer for EMA_V5 parameters."""

    def __init__(self) -> None:
        self._results: List[Dict] = []

    def grid_search(
        self,
        klines: pd.DataFrame,
        symbol: str,
        param_grid: Dict[str, List[Any]],
        base_config: Optional[EMAv5BacktestConfig] = None,
        objective: str = "sharpe_ratio",
    ) -> Dict[str, Any]:
        """Run grid search over parameter combinations.
        
        Args:
            klines: Historical kline data
            symbol: Symbol to test
            param_grid: Dict of param_name → list of values to test
            base_config: Base config to modify
            objective: Metric to optimize ("sharpe_ratio", "profit_factor", "total_pnl", "win_rate")
        """
        config = base_config or EMAv5BacktestConfig()
        self._results = []

        # Generate all combinations
        param_names = list(param_grid.keys())
        param_values = list(param_grid.values())
        combinations = list(itertools.product(*param_values))

        logger.info("📊 EMA_V5 optimizer: {} combinations for {}", len(combinations), symbol)

        best_score = float("-inf")
        best_params = {}
        best_result = None

        for i, combo in enumerate(combinations):
            # Create config with this combination
            test_config = EMAv5BacktestConfig(
                initial_balance=config.initial_balance,
                risk_per_trade_pct=config.risk_per_trade_pct,
                max_positions=config.max_positions,
                max_daily_loss_pct=config.max_daily_loss_pct,
                max_drawdown_pct=config.max_drawdown_pct,
                leverage=config.leverage,
                taker_fee=config.taker_fee,
                maker_fee=config.maker_fee,
                funding_rate_8h=config.funding_rate_8h,
                sl_atr_mult=config.sl_atr_mult,
                tp1_rr=config.tp1_rr,
                tp2_rr=config.tp2_rr,
                tp3_rr=config.tp3_rr,
                tp1_exit_pct=config.tp1_exit_pct,
                tp2_exit_pct=config.tp2_exit_pct,
                tp3_exit_pct=config.tp3_exit_pct,
                breakeven_at_r=config.breakeven_at_r,
                trailing_atr_mult=config.trailing_atr_mult,
                max_hold_hours=config.max_hold_hours,
                ema_fast=config.ema_fast,
                ema_medium=config.ema_medium,
                ema_institutional=config.ema_institutional,
                ema_long_term=config.ema_long_term,
                min_confidence=config.min_confidence,
                timeframe=config.timeframe,
                atr_period=config.atr_period,
            )

            # Apply parameter combination
            params = dict(zip(param_names, combo))
            for name, value in params.items():
                if hasattr(test_config, name):
                    setattr(test_config, name, value)

            # Run backtest
            engine = EMAv5BacktestEngine(test_config)
            result = engine.run(klines, symbol)

            # Extract objective score
            score = getattr(result, objective, 0)
            if objective == "max_drawdown_pct":
                score = -score  # Lower is better for drawdown

            self._results.append({
                "params": params,
                "total_pnl": result.total_pnl,
                "win_rate": result.win_rate,
                "profit_factor": result.profit_factor,
                "sharpe_ratio": result.sharpe_ratio,
                "max_drawdown_pct": result.max_drawdown_pct,
                "total_trades": result.total_trades,
                "score": score,
            })

            if score > best_score:
                best_score = score
                best_params = params
                best_result = result

            if (i + 1) % 10 == 0:
                logger.debug("Optimizer progress: {}/{}", i + 1, len(combinations))

        # Sort by score
        self._results.sort(key=lambda x: x["score"], reverse=True)

        return {
            "best_params": best_params,
            "best_score": round(best_score, 4),
            "best_result": {
                "total_pnl": best_result.total_pnl if best_result else 0,
                "win_rate": best_result.win_rate if best_result else 0,
                "profit_factor": best_result.profit_factor if best_result else 0,
                "sharpe_ratio": best_result.sharpe_ratio if best_result else 0,
                "max_drawdown_pct": best_result.max_drawdown_pct if best_result else 0,
            } if best_result else {},
            "total_combinations": len(combinations),
            "top_10": self._results[:10],
            "objective": objective,
        }

    def get_results(self) -> List[Dict]:
        """Get all optimization results."""
        return self._results

    def sensitivity_analysis(
        self,
        klines: pd.DataFrame,
        symbol: str,
        param_name: str,
        values: List[Any],
        base_config: Optional[EMAv5BacktestConfig] = None,
    ) -> Dict[str, Any]:
        """Analyze sensitivity to a single parameter."""
        config = base_config or EMAv5BacktestConfig()
        results = []

        for value in values:
            test_config = EMAv5BacktestConfig(**{k: v for k, v in config.__dict__.items()})
            setattr(test_config, param_name, value)

            engine = EMAv5BacktestEngine(test_config)
            result = engine.run(klines, symbol)

            results.append({
                "value": value,
                "total_pnl": result.total_pnl,
                "win_rate": result.win_rate,
                "profit_factor": result.profit_factor,
                "sharpe_ratio": result.sharpe_ratio,
                "total_trades": result.total_trades,
            })

        return {
            "param_name": param_name,
            "values_tested": len(values),
            "results": results,
        }
