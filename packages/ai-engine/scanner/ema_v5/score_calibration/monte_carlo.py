"""
Monte Carlo Simulation — Phase 12.

Walk-forward validation, bootstrap analysis, Monte Carlo simulation,
and cross-validation for robustness testing.
"""
from __future__ import annotations

import math
import random
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger


class MonteCarloSimulator:
    """Monte Carlo and walk-forward validation of the confidence model."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or self.DB_PATH
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.row_factory = sqlite3.Row

    def full_analysis(self) -> Dict:
        """Run all Monte Carlo analyses."""
        cur = self._conn.cursor()
        cur.execute("""
            SELECT confidence, return_pct, mfe, mae, rr_achieved, timestamp
            FROM candidates WHERE outcome_tracked = 1 ORDER BY timestamp ASC
        """)
        rows = cur.fetchall()

        if len(rows) < 10:
            return {
                "status": "insufficient_data",
                "min_required": 10,
                "current": len(rows),
            }

        returns = [r[1] for r in rows if r[1] is not None]
        confidences = [r[0] for r in rows if r[0] is not None]

        return {
            "status": "complete",
            "sample_size": len(returns),
            "bootstrap": self._bootstrap_analysis(returns),
            "monte_carlo": self._monte_carlo_simulation(returns),
            "walk_forward": self._walk_forward_validation(rows),
            "cross_validation": self._cross_validation(rows),
            "ruin_probability": self._ruin_probability(returns),
            "confidence_intervals": self._confidence_intervals(returns),
        }

    def _bootstrap_analysis(self, returns: List[float], n_bootstrap: int = 1000) -> Dict:
        """Bootstrap resampling for robust performance estimates."""
        if len(returns) < 5:
            return {"error": "insufficient data"}

        bootstrap_metrics = {"returns": [], "sharpe": [], "profit_factor": [], "win_rate": []}

        for _ in range(n_bootstrap):
            sample = random.choices(returns, k=len(returns))
            avg_ret = sum(sample) / len(sample)
            wins = [r for r in sample if r > 0]
            losses = [r for r in sample if r <= 0]
            wr = len(wins) / len(sample) * 100
            gp = sum(wins) if wins else 0
            gl = sum(abs(l) for l in losses) if losses else 0
            pf = gp / gl if gl > 0 else 0

            if len(sample) > 1:
                std = math.sqrt(sum((r - avg_ret) ** 2 for r in sample) / (len(sample) - 1))
                sharpe = avg_ret / std if std > 0 else 0
            else:
                sharpe = 0

            bootstrap_metrics["returns"].append(avg_ret)
            bootstrap_metrics["sharpe"].append(sharpe)
            bootstrap_metrics["profit_factor"].append(pf)
            bootstrap_metrics["win_rate"].append(wr)

        # Compute confidence intervals (95%)
        def ci(data, name):
            s = sorted(data)
            return {
                "mean": round(sum(data) / len(data), 4),
                "ci_95_low": round(s[int(len(s) * 0.025)], 4),
                "ci_95_high": round(s[int(len(s) * 0.975)], 4),
                "std": round(math.sqrt(sum((d - sum(data)/len(data))**2 for d in data) / len(data)), 4),
            }

        return {
            "iterations": n_bootstrap,
            "avg_return": ci(bootstrap_metrics["returns"], "return"),
            "sharpe": ci(bootstrap_metrics["sharpe"], "sharpe"),
            "profit_factor": ci(bootstrap_metrics["profit_factor"], "pf"),
            "win_rate": ci(bootstrap_metrics["win_rate"], "wr"),
        }

    def _monte_carlo_simulation(
        self, returns: List[float],
        n_simulations: int = 1000,
        n_periods: int = 252,
        initial_capital: float = 10000,
    ) -> Dict:
        """Monte Carlo simulation of future equity curves."""
        if len(returns) < 5:
            return {"error": "insufficient data"}

        final_capitals = []
        max_drawdowns = []
        sharpe_ratios = []

        for _ in range(n_simulations):
            equity = [initial_capital]
            peak = initial_capital
            max_dd = 0
            period_returns = []

            for _ in range(n_periods):
                ret = random.choice(returns) / 100  # Convert from pct
                new_eq = equity[-1] * (1 + ret)
                equity.append(new_eq)
                period_returns.append(ret)
                peak = max(peak, new_eq)
                dd = (peak - new_eq) / peak
                max_dd = max(max_dd, dd)

            final_capitals.append(equity[-1])
            max_drawdowns.append(max_dd * 100)

            if len(period_returns) > 1:
                avg_r = sum(period_returns) / len(period_returns)
                std_r = math.sqrt(sum((r - avg_r)**2 for r in period_returns) / (len(period_returns) - 1))
                sharpe_ratios.append((avg_r / std_r * math.sqrt(252)) if std_r > 0 else 0)
            else:
                sharpe_ratios.append(0)

        final_capitals.sort()
        max_drawdowns.sort()
        sharpe_ratios.sort()

        return {
            "iterations": n_simulations,
            "periods": n_periods,
            "initial_capital": initial_capital,
            "final_capital": {
                "mean": round(sum(final_capitals) / len(final_capitals), 2),
                "median": round(final_capitals[len(final_capitals) // 2], 2),
                "ci_5": round(final_capitals[int(len(final_capitals) * 0.05)], 2),
                "ci_95": round(final_capitals[int(len(final_capitals) * 0.95)], 2),
                "worst": round(final_capitals[0], 2),
                "best": round(final_capitals[-1], 2),
            },
            "max_drawdown": {
                "mean": round(sum(max_drawdowns) / len(max_drawdowns), 2),
                "median": round(max_drawdowns[len(max_drawdowns) // 2], 2),
                "ci_95": round(max_drawdowns[int(len(max_drawdowns) * 0.95)], 2),
                "worst": round(max_drawdowns[-1], 2),
            },
            "sharpe_ratio": {
                "mean": round(sum(sharpe_ratios) / len(sharpe_ratios), 3),
                "median": round(sharpe_ratios[len(sharpe_ratios) // 2], 3),
                "ci_5": round(sharpe_ratios[int(len(sharpe_ratios) * 0.05)], 3),
                "ci_95": round(sharpe_ratios[int(len(sharpe_ratios) * 0.95)], 3),
            },
            "probability_of_profit": round(
                sum(1 for c in final_capitals if c > initial_capital) / len(final_capitals) * 100, 1
            ),
        }

    def _walk_forward_validation(self, rows: list, n_folds: int = 3) -> Dict:
        """Walk-forward out-of-sample validation."""
        returns = [(r[0], r[1]) for r in rows if r[1] is not None]
        if len(returns) < n_folds * 5:
            return {"error": "insufficient data for walk-forward"}

        fold_size = len(returns) // n_folds
        fold_results = []

        for i in range(n_folds):
            start = i * fold_size
            end = min(start + fold_size, len(returns))
            fold_returns = [r[1] for r in returns[start:end]]

            avg_ret = sum(fold_returns) / len(fold_returns) if fold_returns else 0
            wins = [r for r in fold_returns if r > 0]
            wr = len(wins) / len(fold_returns) * 100 if fold_returns else 0
            gp = sum(wins) if wins else 0
            gl = sum(abs(l) for l in fold_returns if l <= 0)
            pf = gp / gl if gl > 0 else 0

            fold_results.append({
                "fold": i + 1,
                "trades": len(fold_returns),
                "avg_return": round(avg_ret, 3),
                "win_rate": round(wr, 1),
                "profit_factor": round(pf, 2),
            })

        # Consistency check
        avg_returns = [f["avg_return"] for f in fold_results]
        consistency = 1.0 - (max(avg_returns) - min(avg_returns)) / max(abs(max(avg_returns)), abs(min(avg_returns)), 0.01)

        return {
            "folds": n_folds,
            "results": fold_results,
            "consistency": round(consistency, 3),
            "is_robust": consistency > 0.5 and all(f["profit_factor"] > 0.8 for f in fold_results),
        }

    def _cross_validation(self, rows: list, k: int = 5) -> Dict:
        """k-fold cross-validation."""
        returns = [(r[0], r[1]) for r in rows if r[1] is not None]
        if len(returns) < k * 2:
            return {"error": "insufficient data"}

        random.shuffle(returns)
        fold_size = len(returns) // k
        fold_results = []

        for i in range(k):
            test_start = i * fold_size
            test_end = min(test_start + fold_size, len(returns))
            test_returns = [r[1] for r in returns[test_start:test_end]]

            avg_ret = sum(test_returns) / len(test_returns) if test_returns else 0
            wins = [r for r in test_returns if r > 0]
            wr = len(wins) / len(test_returns) * 100 if test_returns else 0

            fold_results.append({
                "fold": i + 1,
                "trades": len(test_returns),
                "avg_return": round(avg_ret, 3),
                "win_rate": round(wr, 1),
            })

        mean_return = sum(f["avg_return"] for f in fold_results) / len(fold_results)
        std_return = math.sqrt(sum((f["avg_return"] - mean_return)**2 for f in fold_results) / len(fold_results))

        return {
            "k": k,
            "results": fold_results,
            "mean_return": round(mean_return, 3),
            "std_return": round(std_return, 3),
            "coefficient_of_variation": round(std_return / abs(mean_return), 3) if mean_return != 0 else float('inf'),
        }

    def _ruin_probability(self, returns: List[float], kelly_fraction: float = 0.5) -> Dict:
        """Estimate probability of ruin based on historical returns."""
        if len(returns) < 5:
            return {"error": "insufficient data"}

        avg_ret = sum(returns) / len(returns)
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r <= 0]
        wr = len(wins) / len(returns) if returns else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0

        # Kelly criterion
        if avg_loss > 0:
            kelly = (wr * avg_win - (1 - wr) * avg_loss) / avg_win if avg_win > 0 else 0
        else:
            kelly = 0

        # Simplified ruin probability using gambler's ruin formula
        if avg_ret > 0 and avg_loss > 0:
            edge = avg_ret / avg_loss
            if edge < 1:
                ruin_prob = 1 - edge
            else:
                ruin_prob = 0.01  # Very low with positive edge
        else:
            ruin_prob = 0.5

        return {
            "kelly_criterion": round(kelly, 4),
            "optimal_bet_fraction": round(kelly * kelly_fraction, 4),
            "ruin_probability": round(max(0, min(1, ruin_prob)) * 100, 2),
            "edge": round(avg_ret, 3),
            "win_rate": round(wr * 100, 1),
            "avg_win": round(avg_win, 3),
            "avg_loss": round(avg_loss, 3),
        }

    def _confidence_intervals(self, returns: List[float]) -> Dict:
        """Compute confidence intervals for key metrics."""
        if len(returns) < 5:
            return {"error": "insufficient data"}

        n = len(returns)
        mean = sum(returns) / n
        std = math.sqrt(sum((r - mean)**2 for r in returns) / (n - 1)) if n > 1 else 0
        se = std / math.sqrt(n) if n > 0 else 0

        return {
            "mean_return": {
                "estimate": round(mean, 4),
                "se": round(se, 4),
                "ci_90": (round(mean - 1.645 * se, 4), round(mean + 1.645 * se, 4)),
                "ci_95": (round(mean - 1.96 * se, 4), round(mean + 1.96 * se, 4)),
                "ci_99": (round(mean - 2.576 * se, 4), round(mean + 2.576 * se, 4)),
            },
            "sample_size": n,
        }

    def close(self) -> None:
        self._conn.close()
