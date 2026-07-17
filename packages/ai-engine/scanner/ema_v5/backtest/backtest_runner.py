"""
EMA_V5 Backtest Runner — Runs backtests across multiple symbols and timeframes.
Isolated from existing backtest runner.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger

from .backtest_engine import EMAv5BacktestEngine, EMAv5BacktestConfig, EMAv5BacktestResult


class EMAv5BacktestRunner:
    """Runs EMA_V5 backtests across multiple symbols and aggregates results."""

    def __init__(self, config: Optional[EMAv5BacktestConfig] = None) -> None:
        self.config = config or EMAv5BacktestConfig()

    def run_single(self, klines: pd.DataFrame, symbol: str) -> EMAv5BacktestResult:
        """Run backtest for a single symbol."""
        engine = EMAv5BacktestEngine(self.config)
        result = engine.run(klines, symbol)
        logger.info("📊 EMA_V5 backtest {}: {} trades, {:.2f}% return, PF={:.2f}",
                     symbol, result.total_trades, result.total_return_pct, result.profit_factor)
        return result

    def run_multiple(self, data: Dict[str, pd.DataFrame]) -> EMAv5BacktestResult:
        """Run backtest across multiple symbols and aggregate results.
        
        Args:
            data: Dict of symbol → klines DataFrame
        """
        all_trades = []
        all_equity = [self.config.initial_balance]
        total_pnl = 0.0
        balance = self.config.initial_balance
        peak = balance

        for symbol, klines in data.items():
            engine = EMAv5BacktestEngine(self.config)
            result = engine.run(klines, symbol)

            # Aggregate trades
            all_trades.extend(result.trades)

            # Aggregate equity
            for eq in result.equity_curve[1:]:
                balance += eq - self.config.initial_balance
                all_equity.append(balance)
                peak = max(peak, balance)

        if not all_trades:
            return EMAv5BacktestResult()

        # Recompute aggregate metrics
        wins = [t for t in all_trades if t.pnl > 0]
        losses = [t for t in all_trades if t.pnl <= 0]
        total_pnl = sum(t.pnl for t in all_trades)
        gross_wins = sum(t.pnl for t in wins)
        gross_losses = abs(sum(t.pnl for t in losses))

        # Drawdown
        peak_eq = self.config.initial_balance
        max_dd = 0
        for eq in all_equity:
            peak_eq = max(peak_eq, eq)
            dd = (peak_eq - eq) / peak_eq * 100 if peak_eq > 0 else 0
            max_dd = max(max_dd, dd)

        # Sharpe
        import math
        returns = []
        for i in range(1, len(all_equity)):
            prev = all_equity[i - 1]
            if prev > 0:
                returns.append((all_equity[i] - prev) / prev)
        if returns and len(returns) > 1:
            mean_r = sum(returns) / len(returns)
            std_r = math.sqrt(sum((r - mean_r) ** 2 for r in returns) / (len(returns) - 1))
            sharpe = (mean_r / std_r) * math.sqrt(4 * 365) if std_r > 0 else 0
        else:
            sharpe = 0

        return EMAv5BacktestResult(
            total_pnl=round(total_pnl, 4),
            total_return_pct=round(total_pnl / self.config.initial_balance * 100, 2),
            final_balance=round(balance, 4),
            total_trades=len(all_trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=round(len(wins) / len(all_trades) * 100, 1) if all_trades else 0,
            avg_win=round(gross_wins / len(wins), 4) if wins else 0,
            avg_loss=round(gross_losses / len(losses), 4) if losses else 0,
            largest_win=round(max(t.pnl for t in all_trades), 4),
            largest_loss=round(min(t.pnl for t in all_trades), 4),
            profit_factor=round(gross_wins / gross_losses, 2) if gross_losses > 0 else 99.99,
            expectancy=round(total_pnl / len(all_trades), 4) if all_trades else 0,
            avg_r=round(sum(t.r_multiple for t in all_trades) / len(all_trades), 2) if all_trades else 0,
            max_drawdown_pct=round(max_dd, 2),
            max_drawdown_usd=round(max(all_equity[0] - eq for eq in all_equity), 4),
            sharpe_ratio=round(sharpe, 3),
            trades=all_trades,
            equity_curve=all_equity,
        )

    def run_date_range(self, klines: pd.DataFrame, symbol: str,
                       start_date: str, end_date: str) -> EMAv5BacktestResult:
        """Run backtest for a specific date range."""
        if "timestamp" in klines.columns:
            mask = (klines["timestamp"] >= start_date) & (klines["timestamp"] <= end_date)
            filtered = klines[mask]
        elif isinstance(klines.index, pd.DatetimeIndex):
            mask = (klines.index >= start_date) & (klines.index <= end_date)
            filtered = klines[mask]
        else:
            filtered = klines

        return self.run_single(filtered, symbol)
