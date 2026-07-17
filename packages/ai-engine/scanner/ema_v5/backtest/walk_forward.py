"""
EMA_V5 Walk-Forward Analysis — Out-of-sample validation.
Isolated from existing walk-forward implementations.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd
from loguru import logger

from .backtest_engine import EMAv5BacktestEngine, EMAv5BacktestConfig, EMAv5BacktestResult


class EMAv5WalkForward:
    """Walk-forward analysis for EMA_V5 strategy validation."""

    def __init__(self, config: Optional[EMAv5BacktestConfig] = None) -> None:
        self.config = config or EMAv5BacktestConfig()

    def run(
        self,
        klines: pd.DataFrame,
        symbol: str,
        n_splits: int = 5,
        train_pct: float = 0.7,
        optimize_params: Optional[Dict[str, List[Any]]] = None,
    ) -> Dict[str, Any]:
        """Run walk-forward analysis.
        
        Splits data into n_splits folds. For each fold:
        1. Train on first train_pct of the fold
        2. Test on remaining (1 - train_pct)
        3. Report in-sample vs out-of-sample performance
        """
        if klines.empty or len(klines) < 100:
            return {"error": "Insufficient data for walk-forward analysis"}

        fold_size = len(klines) // n_splits
        folds = []

        for i in range(n_splits):
            start = i * fold_size
            end = min((i + 1) * fold_size, len(klines))
            fold_data = klines.iloc[start:end]

            train_end = int(len(fold_data) * train_pct)
            train_data = fold_data.iloc[:train_end]
            test_data = fold_data.iloc[train_end:]

            if train_data.empty or test_data.empty:
                continue

            # In-sample (train) backtest
            train_engine = EMAv5BacktestEngine(self.config)
            train_result = train_engine.run(train_data, symbol)

            # Out-of-sample (test) backtest
            test_engine = EMAv5BacktestEngine(self.config)
            test_result = test_engine.run(test_data, symbol)

            folds.append({
                "fold": i + 1,
                "train_bars": len(train_data),
                "test_bars": len(test_data),
                "train": {
                    "total_pnl": train_result.total_pnl,
                    "win_rate": train_result.win_rate,
                    "profit_factor": train_result.profit_factor,
                    "total_trades": train_result.total_trades,
                },
                "test": {
                    "total_pnl": test_result.total_pnl,
                    "win_rate": test_result.win_rate,
                    "profit_factor": test_result.profit_factor,
                    "total_trades": test_result.total_trades,
                },
            })

        # Aggregate
        avg_train_wr = sum(f["train"]["win_rate"] for f in folds) / len(folds) if folds else 0
        avg_test_wr = sum(f["test"]["win_rate"] for f in folds) / len(folds) if folds else 0
        avg_train_pnl = sum(f["train"]["total_pnl"] for f in folds) / len(folds) if folds else 0
        avg_test_pnl = sum(f["test"]["total_pnl"] for f in folds) / len(folds) if folds else 0

        # Degradation ratio
        wr_degradation = (avg_train_wr - avg_test_wr) / avg_train_wr * 100 if avg_train_wr > 0 else 0
        pnl_degradation = (avg_train_pnl - avg_test_pnl) / abs(avg_train_pnl) * 100 if avg_train_pnl != 0 else 0

        return {
            "symbol": symbol,
            "n_splits": n_splits,
            "folds": folds,
            "summary": {
                "avg_train_win_rate": round(avg_train_wr, 1),
                "avg_test_win_rate": round(avg_test_wr, 1),
                "avg_train_pnl": round(avg_train_pnl, 4),
                "avg_test_pnl": round(avg_test_pnl, 4),
                "win_rate_degradation_pct": round(wr_degradation, 1),
                "pnl_degradation_pct": round(pnl_degradation, 1),
                "robust": wr_degradation < 20 and pnl_degradation < 30,
            },
        }

    def validate(self, klines: pd.DataFrame, symbol: str,
                 n_splits: int = 5) -> Dict[str, Any]:
        """Quick validation: does the strategy hold out-of-sample?"""
        result = self.run(klines, symbol, n_splits=n_splits)
        summary = result.get("summary", {})

        return {
            "symbol": symbol,
            "is_robust": summary.get("robust", False),
            "train_wr": summary.get("avg_train_win_rate", 0),
            "test_wr": summary.get("avg_test_win_rate", 0),
            "degradation": summary.get("win_rate_degradation_pct", 0),
            "verdict": "PASS" if summary.get("robust", False) else "FAIL",
        }
