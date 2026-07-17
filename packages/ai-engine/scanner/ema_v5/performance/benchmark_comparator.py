"""
EMA_V5 Benchmark Comparator — Compare performance against benchmarks.
Isolated from existing benchmarking.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger

from ..storage.database import EMAv5Database


class EMAv5BenchmarkComparator:
    """Compares EMA_V5 performance against benchmarks."""

    # Default benchmarks (annualized)
    DEFAULT_BENCHMARKS = {
        "buy_and_hold_btc": {"annual_return": 50.0, "max_drawdown": 30.0, "sharpe": 0.8},
        "buy_and_hold_eth": {"annual_return": 40.0, "max_drawdown": 40.0, "sharpe": 0.6},
        "sp500": {"annual_return": 10.0, "max_drawdown": 20.0, "sharpe": 0.5},
        "risk_free": {"annual_return": 5.0, "max_drawdown": 0.0, "sharpe": 0.0},
    }

    def __init__(self, db: Optional[EMAv5Database] = None) -> None:
        self._db = db or EMAv5Database()

    def compare(self, signals: Optional[List[Dict]] = None,
                benchmarks: Optional[Dict] = None) -> Dict[str, Any]:
        """Compare EMA_V5 performance against benchmarks."""
        if signals is None:
            signals = self._db.get_all_signals()

        benchmarks = benchmarks or self.DEFAULT_BENCHMARKS

        closed = [s for s in signals if s.get("result") in ("win", "loss")]
        if not closed:
            return {"error": "No trades to compare"}

        # Compute EMA_V5 metrics
        emav5_metrics = self._compute_metrics(closed)

        # Compare against each benchmark
        comparisons = {}
        for name, bench in benchmarks.items():
            comparisons[name] = {
                "benchmark": bench,
                "emav5": emav5_metrics,
                "excess_return": round(emav5_metrics["annualized_return"] - bench["annual_return"], 2),
                "excess_sharpe": round(emav5_metrics["sharpe_ratio"] - bench["sharpe"], 2),
                "better_return": emav5_metrics["annualized_return"] > bench["annual_return"],
                "better_sharpe": emav5_metrics["sharpe_ratio"] > bench["sharpe"],
                "lower_drawdown": emav5_metrics["max_drawdown_pct"] < bench["max_drawdown"],
            }

        # Overall assessment
        win_count = sum(1 for c in comparisons.values() if c["better_return"])
        total = len(comparisons)

        return {
            "emav5_metrics": emav5_metrics,
            "comparisons": comparisons,
            "summary": {
                "beats_benchmark_return": win_count,
                "total_benchmarks": total,
                "beat_rate": round(win_count / total * 100, 1) if total > 0 else 0,
                "best_benchmark": max(comparisons.items(), key=lambda x: x[1]["excess_return"])[0] if comparisons else "",
                "worst_benchmark": min(comparisons.items(), key=lambda x: x[1]["excess_return"])[0] if comparisons else "",
            },
        }

    def _compute_metrics(self, trades: List[Dict]) -> Dict[str, Any]:
        """Compute EMA_V5 metrics for comparison."""
        import math

        total = len(trades)
        wins = sum(1 for t in trades if t.get("result") == "win")
        pnl = sum(t.get("pnl", 0) for t in trades)

        # Estimate annualized return
        if trades:
            first_ts = min(t.get("timestamp", time.time()) for t in trades)
            last_ts = max(t.get("timestamp", time.time()) for t in trades)
            days = max((last_ts - first_ts) / 86400, 1)
        else:
            days = 1

        total_return_pct = pnl / 10000 * 100  # Assuming $10K initial
        annualized_return = (total_return_pct / days) * 365

        # Sharpe ratio
        pnls = [t.get("pnl", 0) for t in trades]
        if len(pnls) > 1:
            mean_pnl = sum(pnls) / len(pnls)
            std_pnl = (sum((p - mean_pnl) ** 2 for p in pnls) / (len(pnls) - 1)) ** 0.5
            sharpe = (mean_pnl / std_pnl) * math.sqrt(4 * 365) if std_pnl > 0 else 0
        else:
            sharpe = 0

        # Max drawdown
        cumulative = 0
        peak = 0
        max_dd = 0
        for p in pnls:
            cumulative += p
            peak = max(peak, cumulative)
            dd = (peak - cumulative) / max(peak, 1) * 100
            max_dd = max(max_dd, dd)

        return {
            "total_trades": total,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "total_pnl": round(pnl, 4),
            "annualized_return": round(annualized_return, 1),
            "sharpe_ratio": round(sharpe, 3),
            "max_drawdown_pct": round(max_dd, 2),
            "period_days": round(days, 1),
        }
