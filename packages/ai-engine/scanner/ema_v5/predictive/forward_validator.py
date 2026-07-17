"""
EMA V5 Forward Validation — Tracks live forward testing metrics.

Maintains rolling statistics at key milestones:
  - 25 trades
  - 50 trades
  - 100 trades
  - 200 trades
  - 500 trades

Tracks:
  - Confidence vs Return correlation
  - Confidence buckets performance
  - Profit Factor by bucket
  - Expectancy by bucket
  - Win Rate by bucket
  - Average R by bucket
  - Average MAE
  - Average MFE

Raises warnings if higher confidence is not outperforming lower confidence.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger


class ForwardValidator:
    """Tracks live forward testing metrics and validates model performance."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"

    # Key milestones for validation
    MILESTONES = [25, 50, 100, 200, 500]

    def __init__(self) -> None:
        self._trades: List[Dict] = []
        self._returns: np.ndarray = np.array([])
        self._confidences: np.ndarray = np.array([])
        self._milestone_reached: Dict[int, bool] = {m: False for m in self.MILESTONES}
        self._validation_history: List[Dict] = []

    def add_trade(self, trade: Dict) -> Optional[Dict]:
        """Add a completed trade to the validation tracking.

        Args:
            trade: Dictionary with at least 'return_pct' and 'confidence'

        Returns:
            Milestone validation result if a milestone was reached, None otherwise
        """
        self._trades.append(trade)

        # Update arrays
        ret = trade.get("return_pct", 0)
        conf = trade.get("confidence", 0)

        self._returns = np.append(self._returns, ret)
        self._confidences = np.append(self._confidences, conf)

        # Check milestones
        n = len(self._trades)
        for milestone in self.MILESTONES:
            if n >= milestone and not self._milestone_reached[milestone]:
                self._milestone_reached[milestone] = True
                result = self._validate_milestone(milestone)
                self._validation_history.append(result)
                logger.info(
                    "🎯 MILESTONE REACHED: {} trades — PF={:.2f} WR={:.1f}% Corr={:+.4f}",
                    milestone,
                    result["profit_factor"],
                    result["win_rate"] * 100,
                    result["confidence_correlation"],
                )
                return result

        return None

    def _validate_milestone(self, milestone: int) -> Dict:
        """Validate model performance at a specific milestone."""
        n = len(self._returns)
        if n < milestone:
            return {"error": f"Insufficient trades: {n} < {milestone}"}

        # Use last 'milestone' trades for rolling validation
        returns = self._returns[-milestone:]
        confidences = self._confidences[-milestone:]

        # Basic metrics
        wins = returns[returns > 0]
        losses = returns[returns <= 0]

        win_rate = len(wins) / milestone if milestone > 0 else 0
        avg_return = np.mean(returns) if milestone > 0 else 0

        gross_profit = np.sum(wins) if len(wins) > 0 else 0
        gross_loss = abs(np.sum(losses)) if len(losses) > 0 else 0.001
        profit_factor = gross_profit / gross_loss

        # Confidence correlation
        confidence_correlation = self._pearson_correlation(confidences, returns)

        # Bucket analysis
        bucket_analysis = self._compute_bucket_analysis(returns, confidences)

        # R-multiple
        avg_win = np.mean(wins) if len(wins) > 0 else 0
        avg_loss = np.mean(losses) if len(losses) > 0 else 0
        avg_rr = avg_win / abs(avg_loss) if abs(avg_loss) > 0 else 0

        # MAE/MFE
        mfe_values = [t.get("mfe", 0) for t in self._trades[-milestone:] if t.get("mfe") is not None]
        mae_values = [t.get("mae", 0) for t in self._trades[-milestone:] if t.get("mae") is not None]
        avg_mfe = np.mean(mfe_values) if mfe_values else 0
        avg_mae = np.mean(mae_values) if mae_values else 0

        # Sharpe ratio
        std_returns = np.std(returns) if milestone > 1 else 0
        sharpe = (avg_return / std_returns * math.sqrt(252)) if std_returns > 0 else 0

        # Maximum drawdown
        cumulative = np.cumsum(returns)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = running_max - cumulative
        max_drawdown = np.max(drawdowns) if len(drawdowns) > 0 else 0

        result = {
            "milestone": milestone,
            "n_trades": milestone,
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 4),
            "expectancy": round(avg_return, 4),
            "avg_rr": round(avg_rr, 4),
            "avg_mfe": round(avg_mfe, 4),
            "avg_mae": round(avg_mae, 4),
            "confidence_correlation": round(confidence_correlation, 4),
            "sharpe_ratio": round(sharpe, 4),
            "max_drawdown": round(max_drawdown, 4),
            "bucket_analysis": bucket_analysis,
            "timestamp": time.time(),
        }

        return result

    def _compute_bucket_analysis(
        self,
        returns: np.ndarray,
        confidences: np.ndarray,
        n_buckets: int = 5,
    ) -> Dict[str, Dict]:
        """Analyze performance by confidence bucket."""
        if len(returns) < n_buckets * 3:
            return {}

        # Create confidence buckets
        percentiles = np.linspace(0, 100, n_buckets + 1)
        bucket_edges = np.percentile(confidences, percentiles)

        buckets = {}
        for i in range(n_buckets):
            low = bucket_edges[i]
            high = bucket_edges[i + 1]

            mask = (confidences >= low) & (confidences < high)
            if i == n_buckets - 1:
                mask = (confidences >= low) & (confidences <= high)

            bucket_returns = returns[mask]

            if len(bucket_returns) > 0:
                wins = bucket_returns[bucket_returns > 0]
                losses = bucket_returns[bucket_returns <= 0]

                win_rate = len(wins) / len(bucket_returns)
                avg_return = np.mean(bucket_returns)
                gross_profit = np.sum(wins) if len(wins) > 0 else 0
                gross_loss = abs(np.sum(losses)) if len(losses) > 0 else 0.001
                profit_factor = gross_profit / gross_loss
                avg_rr = np.mean(wins) / abs(np.mean(losses)) if len(losses) > 0 and np.mean(losses) != 0 else 0

                buckets[f"bucket_{i}"] = {
                    "confidence_range": f"{low:.1f}-{high:.1f}",
                    "n_trades": len(bucket_returns),
                    "win_rate": round(win_rate, 4),
                    "profit_factor": round(profit_factor, 4),
                    "expectancy": round(avg_return, 4),
                    "avg_rr": round(avg_rr, 4),
                }

        return buckets

    def check_monotonicity(self) -> Dict:
        """Check if higher confidence buckets consistently outperform lower ones.

        Returns:
            Dictionary with monotonicity analysis
        """
        if len(self._returns) < 50:
            return {"error": "Insufficient data for monotonicity check"}

        # Split into quintiles by confidence
        n = len(self._returns)
        quintile_size = n // 5
        quintiles = []

        sorted_indices = np.argsort(self._confidences)
        for i in range(5):
            start = i * quintile_size
            end = start + quintile_size if i < 4 else n
            idx = sorted_indices[start:end]
            quintile_returns = self._returns[idx]
            quintile_conf = self._confidences[idx]

            wins = quintile_returns[quintile_returns > 0]
            losses = quintile_returns[quintile_returns <= 0]

            win_rate = len(wins) / len(quintile_returns) if len(quintile_returns) > 0 else 0
            avg_return = np.mean(quintile_returns) if len(quintile_returns) > 0 else 0
            gp = np.sum(wins) if len(wins) > 0 else 0
            gl = abs(np.sum(losses)) if len(losses) > 0 else 0.001
            pf = gp / gl

            quintiles.append({
                "quintile": i + 1,
                "avg_confidence": round(np.mean(quintile_conf), 4),
                "win_rate": round(win_rate, 4),
                "profit_factor": round(pf, 4),
                "expectancy": round(avg_return, 4),
                "n_trades": len(quintile_returns),
            })

        # Check monotonicity
        expectations = [q["expectancy"] for q in quintiles]
        is_monotonic = all(expectations[i] <= expectations[i + 1] for i in range(len(expectations) - 1))

        return {
            "quintiles": quintiles,
            "is_monotonic": is_monotonic,
            "monotonicity_score": sum(1 for i in range(len(expectations) - 1) if expectations[i] <= expectations[i + 1]) / (len(expectations) - 1),
        }

    def _pearson_correlation(self, x: np.ndarray, y: np.ndarray) -> float:
        """Compute Pearson correlation coefficient."""
        if len(x) < 10:
            return 0
        mx, my = np.mean(x), np.mean(y)
        sx, sy = np.std(x), np.std(y)
        if sx == 0 or sy == 0:
            return 0
        return float(np.mean((x - mx) * (y - my)) / (sx * sy))

    def get_validation_history(self) -> List[Dict]:
        """Get validation history."""
        return list(self._validation_history)

    def get_current_stats(self) -> Dict:
        """Get current validation statistics."""
        n = len(self._returns)
        if n == 0:
            return {"n_trades": 0, "milestones": self._milestone_reached}

        wins = self._returns[self._returns > 0]
        losses = self._returns[self._returns <= 0]

        win_rate = len(wins) / n
        avg_return = np.mean(self._returns)
        gp = np.sum(wins) if len(wins) > 0 else 0
        gl = abs(np.sum(losses)) if len(losses) > 0 else 0.001
        pf = gp / gl

        return {
            "n_trades": n,
            "win_rate": round(win_rate, 4),
            "profit_factor": round(pf, 4),
            "expectancy": round(avg_return, 4),
            "confidence_correlation": round(self._pearson_correlation(self._confidences, self._returns), 4),
            "milestones": dict(self._milestone_reached),
            "next_milestone": self._get_next_milestone(),
        }

    def _get_next_milestone(self) -> Optional[int]:
        """Get the next milestone to reach."""
        n = len(self._trades)
        for m in self.MILESTONES:
            if n < m:
                return m
        return None
