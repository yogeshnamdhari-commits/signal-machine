"""
EMA_V5 Risk Metrics — Drawdown, Sharpe, Sortino, Calmar ratios.
Pure computation from trade history. No side effects.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from loguru import logger

from ..storage.database import EMAv5Database


class RiskMetrics:
    """Risk-adjusted performance metrics for EMA_V5."""

    def __init__(self, db: Optional[EMAv5Database] = None) -> None:
        self._db = db or EMAv5Database()

    def compute_all(self, signals: Optional[List[Dict]] = None,
                    risk_free_rate: float = 0.0) -> Dict[str, Any]:
        """Compute complete risk metrics."""
        if signals is None:
            signals = self._db.get_all_signals()

        closed = [s for s in signals if s.get("result") in ("win", "loss")]
        if not closed:
            return self._empty_risk()

        # Build equity series
        equity = self._build_equity_curve(closed)
        returns = self._compute_returns(equity)

        # Drawdown analysis
        dd = self._drawdown_analysis(equity)

        # Sharpe ratio (annualized, assuming ~4 trades/day for 5m timeframe)
        sharpe = self._sharpe_ratio(returns, risk_free_rate)

        # Sortino ratio (downside deviation only)
        sortino = self._sortino_ratio(returns, risk_free_rate)

        # Calmar ratio
        calmar = self._calmar_ratio(equity, dd["max_drawdown_pct"])

        # Volatility
        volatility = self._volatility(returns)

        # Profit factor components
        pnl_list = [s.get("pnl", 0) for s in closed]
        wins = [p for p in pnl_list if p > 0]
        losses = [abs(p) for p in pnl_list if p < 0]

        # Payoff ratio
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 1
        payoff_ratio = avg_win / avg_loss if avg_loss > 0 else 0

        # Kelly criterion
        win_rate = len(wins) / len(pnl_list) if pnl_list else 0
        kelly = self._kelly_criterion(win_rate, payoff_ratio)

        # Tail risk
        tail = self._tail_risk(pnl_list)

        return {
            "max_drawdown_usd": round(dd["max_drawdown_usd"], 4),
            "max_drawdown_pct": round(dd["max_drawdown_pct"], 2),
            "max_drawdown_duration": dd["max_drawdown_duration"],
            "current_drawdown_pct": round(dd["current_drawdown_pct"], 2),
            "sharpe_ratio": round(sharpe, 3),
            "sortino_ratio": round(sortino, 3),
            "calmar_ratio": round(calmar, 3),
            "volatility": round(volatility, 4),
            "payoff_ratio": round(payoff_ratio, 2),
            "kelly_criterion": round(kelly * 100, 1),
            "tail_risk_5pct": round(tail["var_5pct"], 4),
            "tail_risk_1pct": round(tail["var_1pct"], 4),
            "max_loss_streak_usd": round(dd["max_loss_streak_usd"], 4),
            "recovery_factor": round(dd["recovery_factor"], 2),
        }

    def _build_equity_curve(self, closed: List[Dict]) -> List[float]:
        """Build cumulative equity curve from closed trades."""
        equity = [0.0]
        for t in closed:
            equity.append(equity[-1] + t.get("pnl", 0))
        return equity

    def _compute_returns(self, equity: List[float]) -> List[float]:
        """Compute percentage returns from equity curve."""
        returns = []
        for i in range(1, len(equity)):
            prev = equity[i - 1]
            if prev != 0:
                returns.append((equity[i] - prev) / abs(prev) * 100)
            else:
                returns.append(0)
        return returns

    def _drawdown_analysis(self, equity: List[float]) -> Dict[str, Any]:
        """Compute drawdown metrics from equity curve."""
        peak = equity[0]
        max_dd_usd = 0
        max_dd_pct = 0
        current_dd_pct = 0
        dd_duration = 0
        max_dd_duration = 0
        max_loss_streak_usd = 0
        current_streak = 0

        for val in equity:
            if val >= peak:
                peak = val
                dd_duration = 0
            else:
                dd_duration += 1
                dd_usd = peak - val
                dd_pct = (dd_usd / abs(peak) * 100) if peak != 0 else 0
                max_dd_usd = max(max_dd_usd, dd_usd)
                max_dd_pct = max(max_dd_pct, dd_pct)
                max_dd_duration = max(max_dd_duration, dd_duration)
                current_dd_pct = dd_pct

                current_streak += dd_usd
                max_loss_streak_usd = max(max_loss_streak_usd, current_streak)

        # Recovery factor: total return / max drawdown
        total_return = equity[-1] if equity else 0
        recovery = total_return / max_dd_usd if max_dd_usd > 0 else 0

        return {
            "max_drawdown_usd": max_dd_usd,
            "max_drawdown_pct": max_dd_pct,
            "max_drawdown_duration": max_dd_duration,
            "current_drawdown_pct": current_dd_pct,
            "max_loss_streak_usd": max_loss_streak_usd,
            "recovery_factor": recovery,
        }

    def _sharpe_ratio(self, returns: List[float], risk_free: float = 0.0) -> float:
        """Annualized Sharpe ratio."""
        if not returns or len(returns) < 2:
            return 0.0
        excess = [r - risk_free for r in returns]
        mean = sum(excess) / len(excess)
        std = math.sqrt(sum((r - mean) ** 2 for r in excess) / (len(excess) - 1))
        if std == 0:
            return 0.0
        # Annualize: ~4 trades/day × 365 days
        return (mean / std) * math.sqrt(4 * 365)

    def _sortino_ratio(self, returns: List[float], risk_free: float = 0.0) -> float:
        """Sortino ratio (downside deviation only)."""
        if not returns or len(returns) < 2:
            return 0.0
        excess = [r - risk_free for r in returns]
        mean = sum(excess) / len(excess)
        downside = [r for r in excess if r < 0]
        if not downside:
            return 99.99 if mean > 0 else 0.0
        downside_var = sum(r ** 2 for r in downside) / len(excess)
        downside_std = math.sqrt(downside_var)
        if downside_std == 0:
            return 0.0
        return (mean / downside_std) * math.sqrt(4 * 365)

    def _calmar_ratio(self, equity: List[float], max_dd_pct: float) -> float:
        """Calmar ratio: annual return / max drawdown."""
        if not equity or max_dd_pct == 0:
            return 0.0
        total_return = equity[-1]
        annual_return = total_return * (4 * 365 / max(len(equity), 1))
        return annual_return / abs(max_dd_pct) if max_dd_pct != 0 else 0

    def _volatility(self, returns: List[float]) -> float:
        """Annualized volatility."""
        if not returns or len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        return math.sqrt(var) * math.sqrt(4 * 365)

    def _kelly_criterion(self, win_rate: float, payoff_ratio: float) -> float:
        """Kelly fraction for optimal position sizing."""
        if payoff_ratio == 0:
            return 0.0
        return (win_rate * payoff_ratio - (1 - win_rate)) / payoff_ratio

    def _tail_risk(self, pnl_list: List[float]) -> Dict[str, float]:
        """Value at Risk at 5% and 1% confidence."""
        if not pnl_list:
            return {"var_5pct": 0, "var_1pct": 0}
        sorted_pnl = sorted(pnl_list)
        n = len(sorted_pnl)
        var_5 = sorted_pnl[max(0, int(n * 0.05))]
        var_1 = sorted_pnl[max(0, int(n * 0.01))]
        return {"var_5pct": var_5, "var_1pct": var_1}

    def _empty_risk(self) -> Dict[str, Any]:
        """Return zeroed risk metrics."""
        return {
            "max_drawdown_usd": 0, "max_drawdown_pct": 0,
            "max_drawdown_duration": 0, "current_drawdown_pct": 0,
            "sharpe_ratio": 0, "sortino_ratio": 0, "calmar_ratio": 0,
            "volatility": 0, "payoff_ratio": 0, "kelly_criterion": 0,
            "tail_risk_5pct": 0, "tail_risk_1pct": 0,
            "max_loss_streak_usd": 0, "recovery_factor": 0,
        }
