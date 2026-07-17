"""
EMA_V5 Performance Metrics — Continuously calculates all performance indicators.

Metrics tracked:
  - Signal frequency (signals per hour)
  - Acceptance % (signals / candidates)
  - Rejection % (1 - acceptance)
  - Average confidence
  - Average pipeline latency
  - Signal persistence (how long signals stay active)
  - Win rate
  - Profit factor
  - Expectancy
  - Average R:R
  - Maximum drawdown
  - Sharpe ratio (approximate)
  - Sortino ratio (approximate)
  - MAR ratio
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Optional

from loguru import logger


class PerformanceMetrics:
    """Continuously calculates and tracks performance metrics."""

    def __init__(self) -> None:
        self._start_time: float = time.time()
        self._signal_count: int = 0
        self._candidate_count: int = 0
        self._confidence_scores: List[float] = []
        self._latencies_ms: List[float] = []

        # Trade performance
        self._trade_returns: List[float] = []
        self._trade_rrs: List[float] = []
        self._equity_curve: List[float] = [10_000.0]  # Start with $10K
        self._peak_equity: float = 10_000.0
        self._max_drawdown: float = 0.0

        # Signal lifecycle
        self._signal_durations: List[float] = []
        self._active_signals: Dict[str, float] = {}  # signal_id → start_time

        # Rolling window (last N trades)
        self._rolling_window: int = 50

    def record_candidate(self, confidence: float = 0, latency_ms: float = 0) -> None:
        """Record a candidate evaluation."""
        self._candidate_count += 1
        if confidence > 0:
            self._confidence_scores.append(confidence)
            if len(self._confidence_scores) > 500:
                self._confidence_scores = self._confidence_scores[-500:]
        if latency_ms > 0:
            self._latencies_ms.append(latency_ms)
            if len(self._latencies_ms) > 500:
                self._latencies_ms = self._latencies_ms[-500:]

    def record_signal(self, signal_id: str) -> None:
        """Record signal generation."""
        self._signal_count += 1
        self._active_signals[signal_id] = time.time()

    def close_signal(self, signal_id: str, pnl: float = 0, rr: float = 0) -> None:
        """Record signal close with outcome."""
        start = self._active_signals.pop(signal_id, None)
        if start:
            duration = time.time() - start
            self._signal_durations.append(duration)
            if len(self._signal_durations) > 500:
                self._signal_durations = self._signal_durations[-500:]

        if pnl != 0:
            self._trade_returns.append(pnl)
            if len(self._trade_returns) > 500:
                self._trade_returns = self._trade_returns[-500:]

        if rr != 0:
            self._trade_rrs.append(rr)
            if len(self._trade_rrs) > 500:
                self._trade_rrs = self._trade_rrs[-500:]

        # Update equity curve
        if self._equity_curve:
            new_equity = self._equity_curve[-1] + pnl
            self._equity_curve.append(new_equity)
            self._peak_equity = max(self._peak_equity, new_equity)
            dd = (self._peak_equity - new_equity) / max(self._peak_equity, 1) * 100
            self._max_drawdown = max(self._max_drawdown, dd)

    def get_metrics(self) -> Dict:
        """Get all current performance metrics."""
        uptime_hours = max((time.time() - self._start_time) / 3600, 0.001)

        # Signal frequency
        signals_per_hour = self._signal_count / uptime_hours

        # Acceptance rate
        acceptance_pct = self._signal_count / max(self._candidate_count, 1) * 100
        rejection_pct = 100 - acceptance_pct

        # Average confidence
        avg_confidence = 0
        if self._confidence_scores:
            avg_confidence = sum(self._confidence_scores) / len(self._confidence_scores)

        # Average latency
        avg_latency_ms = 0
        max_latency_ms = 0
        if self._latencies_ms:
            avg_latency_ms = sum(self._latencies_ms) / len(self._latencies_ms)
            max_latency_ms = max(self._latencies_ms)

        # Trade performance
        avg_rr = 0
        win_rate = 0
        profit_factor = 0
        expectancy = 0
        sharpe = 0
        sortino = 0
        mar = 0

        if self._trade_returns:
            returns = self._trade_returns
            wins = [r for r in returns if r > 0]
            losses = [r for r in returns if r <= 0]

            win_rate = len(wins) / len(returns) * 100
            avg_return = sum(returns) / len(returns)
            expectancy = avg_return

            gross_profit = sum(wins) if wins else 0
            gross_loss = abs(sum(losses)) if losses else 0.001
            profit_factor = gross_profit / gross_loss

            # Sharpe (annualized approximate — assuming ~4 trades/day)
            mean_ret = sum(returns) / len(returns)
            if len(returns) > 1:
                std_ret = math.sqrt(sum((r - mean_ret) ** 2 for r in returns) / (len(returns) - 1))
                sharpe = mean_ret / max(std_ret, 0.001) * math.sqrt(365 * 4)  # annualized

                # Sortino (downside deviation)
                downside = [r for r in returns if r < 0]
                if downside:
                    downside_dev = math.sqrt(sum(r ** 2 for r in downside) / len(downside))
                    sortino = mean_ret / max(downside_dev, 0.001) * math.sqrt(365 * 4)
                else:
                    sortino = float('inf') if mean_ret > 0 else 0

                # MAR = CAGR / Max DD
                total_return = sum(returns)
                cagr = total_return / max(uptime_hours / (365 * 24), 0.001)
                mar = cagr / max(self._max_drawdown, 0.01)

        if self._trade_rrs:
            avg_rr = sum(self._trade_rrs) / len(self._trade_rrs)

        # Average signal duration
        avg_signal_duration = 0
        if self._signal_durations:
            avg_signal_duration = sum(self._signal_durations) / len(self._signal_durations)

        return {
            "uptime_hours": round(uptime_hours, 1),
            "signal_frequency": round(signals_per_hour, 2),
            "total_candidates": self._candidate_count,
            "total_signals": self._signal_count,
            "acceptance_pct": round(acceptance_pct, 2),
            "rejection_pct": round(rejection_pct, 2),
            "avg_confidence": round(avg_confidence, 1),
            "avg_latency_ms": round(avg_latency_ms, 2),
            "max_latency_ms": round(max_latency_ms, 2),
            "currently_active_signals": len(self._active_signals),
            "win_rate": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 3),
            "avg_rr": round(avg_rr, 2),
            "max_drawdown_pct": round(self._max_drawdown, 2),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2) if sortino != float('inf') else "inf",
            "mar_ratio": round(mar, 2),
            "total_trades": len(self._trade_returns),
            "avg_signal_duration_sec": round(avg_signal_duration, 1),
        }
