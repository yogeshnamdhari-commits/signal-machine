"""
Portfolio Service — Aggregates portfolio data from all engines.

Integrates with:
- MultiExchangePortfolioRiskEngine
- PositionManager
- FillManager
- CapitalAllocationEngine
- RiskEngine / RiskGuardian
- DataBridge
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger


class PortfolioService:
    """
    Central portfolio data aggregation service.

    Pulls data from all engines and provides a unified
    API for the dashboard to consume.
    """

    def __init__(self) -> None:
        self._equity_history: List[Dict[str, Any]] = []
        self._pnl_history: List[Dict[str, Any]] = []
        self._start_equity: float = 10_000.0
        self._peak_equity: float = 10_000.0
        self._daily_pnl: float = 0.0
        self._weekly_pnl: float = 0.0
        self._monthly_pnl: float = 0.0
        self._total_pnl: float = 0.0
        self._trade_count: int = 0
        self._win_count: int = 0
        self._loss_count: int = 0
        self._max_drawdown: float = 0.0
        self._current_drawdown: float = 0.0
        self._profit_factor: float = 0.0
        self._gross_profit: float = 0.0
        self._gross_loss: float = 0.0
        self._sharpe_ratio: float = 0.0
        self._sortino_ratio: float = 0.0
        self._expectancy: float = 0.0
        self._risk_of_ruin: float = 0.0
        self._returns: List[float] = []
        self._equity: float = 10_000.0

    def update_equity(self, equity: float) -> None:
        """Update current equity and recalculate metrics."""
        prev = self._equity
        self._equity = equity

        if equity > self._peak_equity:
            self._peak_equity = equity

        # Drawdown
        if self._peak_equity > 0:
            dd = (self._peak_equity - equity) / self._peak_equity * 100
            self._current_drawdown = dd
            if dd > self._max_drawdown:
                self._max_drawdown = dd

        # Returns
        if prev > 0:
            ret = (equity - prev) / prev
            self._returns.append(ret)
            if len(self._returns) > 1000:
                self._returns = self._returns[-500:]

        # PnL
        self._total_pnl = equity - self._start_equity

        # Sharpe / Sortino
        self._calculate_risk_metrics()

        # Equity history
        self._equity_history.append({
            "timestamp": time.time(),
            "equity": equity,
            "drawdown": self._current_drawdown,
        })
        if len(self._equity_history) > 5000:
            self._equity_history = self._equity_history[-2500:]

    def record_trade(self, pnl: float) -> None:
        """Record a completed trade."""
        self._trade_count += 1
        if pnl > 0:
            self._win_count += 1
            self._gross_profit += pnl
        elif pnl < 0:
            self._loss_count += 1
            self._gross_loss += abs(pnl)

        self._total_pnl += pnl

        # Profit factor
        if self._gross_loss > 0:
            self._profit_factor = self._gross_profit / self._gross_loss
        else:
            self._profit_factor = float("inf") if self._gross_profit > 0 else 0.0

        # Expectancy
        if self._trade_count > 0:
            win_rate = self._win_count / self._trade_count
            avg_win = self._gross_profit / max(self._win_count, 1)
            avg_loss = self._gross_loss / max(self._loss_count, 1)
            self._expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)

        # Risk of ruin (simplified Kelly-based)
        if self._trade_count >= 10:
            win_rate = self._win_count / self._trade_count
            avg_win = self._gross_profit / max(self._win_count, 1)
            avg_loss = self._gross_loss / max(self._loss_count, 1)
            if avg_loss > 0 and win_rate > 0:
                payoff = avg_win / avg_loss
                kelly = win_rate - ((1 - win_rate) / payoff)
                if kelly > 0 and kelly < 1:
                    import math
                    ratio = (1 - kelly) / kelly
                    exponent = min(self._equity / avg_loss, 1000)
                    # Use log to avoid overflow
                    log_ratio = math.log(max(ratio, 1e-300))
                    log_result = exponent * log_ratio
                    if log_result < -50:
                        self._risk_of_ruin = 0.0  # Essentially zero
                    elif log_result > 50:
                        self._risk_of_ruin = 1.0  # Capped at 1
                    else:
                        self._risk_of_ruin = max(0, min(1.0, math.exp(log_result)))
                elif kelly <= 0:
                    self._risk_of_ruin = 1.0
                else:
                    self._risk_of_ruin = 0.0

    def _compute_health_score(self) -> float:
        """Compute overall portfolio health score (0-100)."""
        score = 50.0  # Base score

        # Drawdown impact (up to -30)
        if self._current_drawdown < 2:
            score += 15
        elif self._current_drawdown < 5:
            score += 5
        elif self._current_drawdown > 15:
            score -= 20
        elif self._current_drawdown > 10:
            score -= 10

        # Win rate impact (up to +20)
        if self._trade_count > 0:
            wr = self._win_count / self._trade_count
            if wr > 0.6:
                score += 20
            elif wr > 0.5:
                score += 10
            elif wr < 0.3:
                score -= 15

        # Sharpe impact (up to +15)
        if self._sharpe_ratio > 2:
            score += 15
        elif self._sharpe_ratio > 1:
            score += 10
        elif self._sharpe_ratio < 0:
            score -= 10

        # Profit factor impact (up to +15)
        if self._profit_factor > 2:
            score += 15
        elif self._profit_factor > 1.5:
            score += 10
        elif self._profit_factor < 1:
            score -= 10

        return max(0, min(100, score))

    def _calculate_risk_metrics(self) -> None:
        """Calculate Sharpe and Sortino ratios."""
        if len(self._returns) < 10:
            return

        import math

        mean_ret = sum(self._returns) / len(self._returns)
        variance = sum((r - mean_ret) ** 2 for r in self._returns) / len(self._returns)
        std_dev = math.sqrt(variance) if variance > 0 else 0.0001

        # Annualized Sharpe (assume ~365 trading days, 1s updates)
        annualization = math.sqrt(365 * 24 * 3600)
        if std_dev > 0:
            self._sharpe_ratio = (mean_ret / std_dev) * annualization
        else:
            self._sharpe_ratio = 0.0

        # Sortino (downside deviation)
        neg_returns = [r for r in self._returns if r < 0]
        if neg_returns:
            downside_var = sum(r ** 2 for r in neg_returns) / len(neg_returns)
            downside_dev = math.sqrt(downside_var)
            if downside_dev > 0:
                self._sortino_ratio = (mean_ret / downside_dev) * annualization
            else:
                self._sortino_ratio = 0.0
        else:
            self._sortino_ratio = self._sharpe_ratio

    def get_executive_overview(self) -> Dict[str, Any]:
        """Get executive overview data for the dashboard."""
        win_rate = (self._win_count / max(self._trade_count, 1)) * 100

        return {
            "total_equity": round(self._equity, 2),
            "daily_pnl": round(self._daily_pnl, 2),
            "weekly_pnl": round(self._weekly_pnl, 2),
            "monthly_pnl": round(self._monthly_pnl, 2),
            "total_pnl": round(self._total_pnl, 2),
            "portfolio_value": round(self._equity, 2),
            "available_capital": round(self._equity * 0.5, 2),  # Estimated
            "used_capital": round(self._equity * 0.5, 2),
            "open_risk": round(self._current_drawdown, 2),
            "current_drawdown": round(self._current_drawdown, 2),
            "max_drawdown": round(self._max_drawdown, 2),
            "profit_factor": round(self._profit_factor, 4),
            "win_rate": round(win_rate, 2),
            "sharpe_ratio": round(self._sharpe_ratio, 4),
            "sortino_ratio": round(self._sortino_ratio, 4),
            "expectancy": round(self._expectancy, 4),
            "risk_of_ruin": round(self._risk_of_ruin, 6),
            "trade_count": self._trade_count,
            "win_count": self._win_count,
            "loss_count": self._loss_count,
            "gross_profit": round(self._gross_profit, 2),
            "gross_loss": round(self._gross_loss, 2),
            "health_score": round(self._compute_health_score(), 2),
            "equity_history": self._equity_history[-200:],
            "timestamp": time.time(),
        }

    def get_equity_history(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Get equity curve data."""
        return self._equity_history[-limit:]

    def get_pnl_history(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Get PnL history."""
        return self._pnl_history[-limit:]

    def get_health_score(self) -> float:
        """Calculate overall portfolio health score (0-100)."""
        score = 100.0

        # Deduct for drawdown
        if self._current_drawdown > 5:
            score -= min(30, self._current_drawdown * 3)

        # Deduct for low win rate
        win_rate = self._win_count / max(self._trade_count, 1)
        if win_rate < 0.5:
            score -= (0.5 - win_rate) * 40

        # Deduct for negative profit factor
        if self._profit_factor < 1.0:
            score -= 20

        # Deduct for low Sharpe
        if self._sharpe_ratio < 1.0:
            score -= min(15, (1.0 - self._sharpe_ratio) * 15)

        return max(0, min(100, score))
