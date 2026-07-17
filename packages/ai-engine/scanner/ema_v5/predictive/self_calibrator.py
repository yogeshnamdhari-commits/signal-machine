"""
EMA V5 Self-Calibration — Continuously computes performance metrics and updates calibration.

Metrics tracked:
  - Feature importance
  - Win rate
  - Profit Factor
  - Expectancy
  - Average R
  - Average MAE
  - Average MFE
  - Sharpe ratio
  - Sortino ratio
  - MAR ratio
  - Maximum Drawdown
  - Kelly %
  - Edge Score

Updates confidence calibration periodically based on rolling statistics.
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


class SelfCalibrator:
    """Continuously computes performance metrics and updates calibration."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"

    def __init__(self) -> None:
        self._metrics_history: List[Dict] = []
        self._feature_importance: Dict[str, float] = {}
        self._last_calibration: float = 0
        self._calibration_interval: float = 3600  # Re-calibrate every hour

    def compute_metrics(
        self,
        trades: List[Dict],
        returns: np.ndarray,
        confidences: np.ndarray,
    ) -> Dict:
        """Compute comprehensive performance metrics.

        Args:
            trades: List of trade dictionaries
            returns: Array of trade returns
            confidences: Array of confidence scores

        Returns:
            Dictionary of performance metrics
        """
        if len(trades) < 10:
            return {"error": "Insufficient trades for metrics computation"}

        n = len(returns)
        wins = returns[returns > 0]
        losses = returns[returns <= 0]

        # Basic metrics
        win_rate = len(wins) / n if n > 0 else 0
        avg_return = np.mean(returns) if n > 0 else 0
        avg_win = np.mean(wins) if len(wins) > 0 else 0
        avg_loss = np.mean(losses) if len(losses) > 0 else 0

        # Profit factor
        gross_profit = np.sum(wins) if len(wins) > 0 else 0
        gross_loss = abs(np.sum(losses)) if len(losses) > 0 else 0.001
        profit_factor = gross_profit / gross_loss

        # R-multiple (risk-adjusted return)
        avg_rr = avg_win / abs(avg_loss) if abs(avg_loss) > 0 else 0

        # Expectancy (expected value per trade)
        expectancy = avg_return

        # Volatility metrics
        std_returns = np.std(returns) if n > 1 else 0
        sharpe = (avg_return / std_returns * math.sqrt(252)) if std_returns > 0 else 0

        # Sortino (downside deviation)
        downside_returns = returns[returns < 0]
        downside_std = np.std(downside_returns) if len(downside_returns) > 1 else 0
        sortino = (avg_return / downside_std * math.sqrt(252)) if downside_std > 0 else 0

        # Maximum drawdown
        cumulative = np.cumsum(returns)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = running_max - cumulative
        max_drawdown = np.max(drawdowns) if len(drawdowns) > 0 else 0

        # MAR ratio (return / max drawdown)
        total_return = np.sum(returns)
        mar = total_return / max_drawdown if max_drawdown > 0 else 0

        # Kelly criterion
        if avg_loss != 0:
            kelly = win_rate - (1 - win_rate) / abs(avg_loss / avg_win) if avg_win > 0 else 0
        else:
            kelly = 0

        # Edge score (simplified)
        edge = profit_factor - 1.0 if profit_factor > 1.0 else 0

        # Confidence correlation
        if len(confidences) == len(returns):
            confidence_correlation = self._pearson_correlation(confidences, returns)
        else:
            confidence_correlation = 0

        # MAE/MFE (if available in trades)
        mfe_values = [t.get("mfe", 0) for t in trades if t.get("mfe") is not None]
        mae_values = [t.get("mae", 0) for t in trades if t.get("mae") is not None]
        avg_mfe = np.mean(mfe_values) if mfe_values else 0
        avg_mae = np.mean(mae_values) if mae_values else 0

        metrics = {
            "n_trades": n,
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 4),
            "expectancy": round(expectancy, 4),
            "avg_return": round(avg_return, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "avg_rr": round(avg_rr, 4),
            "avg_mfe": round(avg_mfe, 4),
            "avg_mae": round(avg_mae, 4),
            "sharpe_ratio": round(sharpe, 4),
            "sortino_ratio": round(sortino, 4),
            "mar_ratio": round(mar, 4),
            "max_drawdown": round(max_drawdown, 4),
            "kelly_pct": round(kelly, 4),
            "edge_score": round(edge, 4),
            "confidence_correlation": round(confidence_correlation, 4),
            "total_return": round(total_return, 4),
            "std_returns": round(std_returns, 4),
            "timestamp": time.time(),
        }

        self._metrics_history.append(metrics)
        if len(self._metrics_history) > 100:
            self._metrics_history = self._metrics_history[-100:]

        return metrics

    def compute_rolling_metrics(
        self,
        returns: np.ndarray,
        confidences: np.ndarray,
        window: int = 25,
    ) -> Dict[str, List]:
        """Compute rolling metrics over different windows.

        Returns:
            Dictionary with rolling metrics for windows: 25, 50, 100, 200, 500
        """
        windows = [25, 50, 100, 200, 500]
        rolling = {f"rolling_{w}": {} for w in windows}

        for w in windows:
            if len(returns) < w:
                rolling[f"rolling_{w}"] = {"error": f"Insufficient data for window {w}"}
                continue

            # Compute metrics for each window
            window_returns = returns[-w:]
            window_confidences = confidences[-w:]

            wins = window_returns[window_returns > 0]
            losses = window_returns[window_returns <= 0]

            win_rate = len(wins) / w if w > 0 else 0
            avg_return = np.mean(window_returns) if w > 0 else 0

            gross_profit = np.sum(wins) if len(wins) > 0 else 0
            gross_loss = abs(np.sum(losses)) if len(losses) > 0 else 0.001
            profit_factor = gross_profit / gross_loss

            # Confidence correlation for this window
            confidence_corr = self._pearson_correlation(window_confidences, window_returns)

            rolling[f"rolling_{w}"] = {
                "win_rate": round(win_rate, 4),
                "profit_factor": round(profit_factor, 4),
                "expectancy": round(avg_return, 4),
                "confidence_correlation": round(confidence_corr, 4),
                "n_trades": w,
            }

        return rolling

    def compute_bucket_analysis(
        self,
        returns: np.ndarray,
        confidences: np.ndarray,
        n_buckets: int = 5,
    ) -> Dict[str, Dict]:
        """Analyze performance by confidence bucket.

        Returns:
            Dictionary with metrics for each confidence bucket
        """
        if len(returns) < n_buckets * 5:
            return {"error": "Insufficient data for bucket analysis"}

        # Create confidence buckets
        percentiles = np.linspace(0, 100, n_buckets + 1)
        bucket_edges = np.percentile(confidences, percentiles)

        buckets = {}
        for i in range(n_buckets):
            low = bucket_edges[i]
            high = bucket_edges[i + 1]

            mask = (confidences >= low) & (confidences < high)
            if i == n_buckets - 1:  # Include upper edge in last bucket
                mask = (confidences >= low) & (confidences <= high)

            bucket_returns = returns[mask]
            bucket_confidences = confidences[mask]

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
                    "avg_confidence": round(np.mean(bucket_confidences), 4),
                }

        return buckets

    def check_calibration_warning(
        self,
        returns: np.ndarray,
        confidences: np.ndarray,
    ) -> List[str]:
        """Check for calibration warnings.

        Returns:
            List of warning messages
        """
        warnings = []

        # Check 1: Negative correlation
        if len(returns) >= 25:
            corr = self._pearson_correlation(confidences, returns)
            if corr < 0:
                warnings.append(f"WARNING: Confidence-return correlation is NEGATIVE ({corr:+.4f})")

        # Check 2: Higher confidence not performing better
        if len(returns) >= 50:
            # Split into top/bottom halves by confidence
            median_conf = np.median(confidences)
            high_conf_returns = returns[confidences >= median_conf]
            low_conf_returns = returns[confidences < median_conf]

            if len(high_conf_returns) > 0 and len(low_conf_returns) > 0:
                high_avg = np.mean(high_conf_returns)
                low_avg = np.mean(low_conf_returns)

                if high_avg < low_avg:
                    warnings.append(f"WARNING: High confidence ({high_avg:+.3f}) underperforms low confidence ({low_avg:+.3f})")

        # Check 3: Profit factor below 1.0
        if len(returns) >= 25:
            wins = returns[returns > 0]
            losses = returns[returns <= 0]
            gp = np.sum(wins) if len(wins) > 0 else 0
            gl = abs(np.sum(losses)) if len(losses) > 0 else 0.001
            pf = gp / gl

            if pf < 1.0:
                warnings.append(f"WARNING: Profit Factor is below 1.0 ({pf:.2f})")

        # Check 4: Maximum drawdown too high
        if len(returns) >= 25:
            cumulative = np.cumsum(returns)
            running_max = np.maximum.accumulate(cumulative)
            drawdowns = running_max - cumulative
            max_dd = np.max(drawdowns)

            if max_dd > 20:  # 20% drawdown threshold
                warnings.append(f"WARNING: Maximum drawdown is {max_dd:.2f}%")

        return warnings

    def _pearson_correlation(self, x: np.ndarray, y: np.ndarray) -> float:
        """Compute Pearson correlation coefficient."""
        if len(x) < 10:
            return 0
        mx, my = np.mean(x), np.mean(y)
        sx, sy = np.std(x), np.std(y)
        if sx == 0 or sy == 0:
            return 0
        return float(np.mean((x - mx) * (y - my)) / (sx * sy))

    def get_metrics_history(self) -> List[Dict]:
        """Get metrics computation history."""
        return list(self._metrics_history)

    def get_latest_metrics(self) -> Optional[Dict]:
        """Get most recent metrics."""
        return self._metrics_history[-1] if self._metrics_history else None
