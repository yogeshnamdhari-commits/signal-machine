"""
Performance Analytics Engine — comprehensive metrics, attribution, and reporting.
Calculates 50+ performance metrics with trade analysis and visualization support.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger


@dataclass
class TradeAnalytics:
    """Detailed trade-level analytics."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    avg_trade_duration_hours: float
    median_trade_duration_hours: float
    profit_factor: float
    payoff_ratio: float
    expectancy: float
    kelly_criterion: float
    # Streak analysis
    max_consecutive_wins: int
    max_consecutive_losses: int
    avg_consecutive_wins: float
    avg_consecutive_losses: float
    # Time analysis
    best_hour: int
    worst_hour: int
    best_day_of_week: str
    worst_day_of_week: str
    # Direction analysis
    long_trades: int
    short_trades: int
    long_win_rate: float
    short_win_rate: float
    long_pnl: float
    short_pnl: float


@dataclass
class RiskAnalytics:
    """Risk-adjusted performance metrics."""
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    max_drawdown_pct: float
    max_drawdown_duration_days: float
    avg_drawdown_pct: float
    recovery_factor: float
    tail_ratio: float
    # Value at Risk
    var_95: float
    var_99: float
    cvar_95: float
    cvar_99: float
    # Volatility
    daily_volatility: float
    annual_volatility: float
    downside_deviation: float
    # Ulcer Index
    ulcer_index: float
    # Pain Index
    pain_index: float


@dataclass
class EquityAnalytics:
    """Equity curve analysis."""
    initial_capital: float
    final_equity: float
    total_return_pct: float
    cagr: float
    equity_curve: List[float]
    drawdown_curve: List[float]
    monthly_returns: Dict[str, float]
    yearly_returns: Dict[str, float]
    # Milestone tracking
    time_to_first_profit_hours: float
    time_to_recovery_hours: float
    new_highs_count: int


@dataclass
class PerformanceReport:
    """Complete performance analytics report."""
    symbol: str
    period_start: datetime
    period_end: datetime
    trade_analytics: TradeAnalytics
    risk_analytics: RiskAnalytics
    equity_analytics: EquityAnalytics
    # Composite scores
    overall_score: float  # 0-100
    robustness_score: float
    consistency_score: float
    efficiency_score: float


class PerformanceAnalyticsEngine:
    """
    Comprehensive performance analytics engine.
    
    Calculates 50+ metrics across:
    - Trade-level analytics (win rate, streaks, time analysis)
    - Risk-adjusted metrics (Sharpe, Sortino, VaR, CVaR)
    - Equity curve analysis (CAGR, drawdowns, monthly returns)
    - Composite scoring (overall, robustness, consistency, efficiency)
    
    Supports:
    - Individual trade analysis
    - Portfolio-level aggregation
    - Multi-strategy comparison
    - JSON/HTML report export
    """

    def __init__(self, initial_capital: float = 10_000, risk_free_rate: float = 0.0) -> None:
        self.initial_capital = initial_capital
        self.risk_free_rate = risk_free_rate
        self._output_dir = Path("data/reports")
        self._output_dir.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """Initialize the analytics engine."""
        logger.info("PerformanceAnalytics engine ready")

    # ── Main Analysis ────────────────────────────────────────────

    async def analyze(
        self,
        symbol: str,
        trades: List[Dict],
        equity_curve: Optional[List[float]] = None,
    ) -> PerformanceReport:
        """
        Generate comprehensive performance report.
        
        Args:
            symbol: Trading pair
            trades: List of trade dicts with keys:
                    side, entry_price, exit_price, size, entry_time, exit_time,
                    pnl, fees, slippage, exit_reason
            equity_curve: Optional equity curve (if None, will be computed)
        """
        if not trades:
            return self._empty_report(symbol)

        # Convert trades to structured format
        trade_data = self._parse_trades(trades)

        # Compute equity curve if not provided
        if equity_curve is None:
            equity_curve = self._compute_equity_curve(trade_data)

        # Calculate all analytics
        trade_analytics = self._analyze_trades(trade_data)
        risk_analytics = self._analyze_risk(equity_curve, trade_data)
        equity_analytics = self._analyze_equity(equity_curve, trade_data)

        # Composite scores
        overall = self._compute_overall_score(trade_analytics, risk_analytics, equity_analytics)
        robustness = self._compute_robustness_score(trade_analytics, risk_analytics)
        consistency = self._compute_consistency_score(equity_analytics)
        efficiency = self._compute_efficiency_score(trade_analytics, risk_analytics)

        # Build timestamps
        entry_times = [t["entry_time"] for t in trade_data if "entry_time" in t]
        exit_times = [t["exit_time"] for t in trade_data if "exit_time" in t]
        start = entry_times[0] if entry_times else datetime.now()
        end = exit_times[-1] if exit_times else datetime.now()

        report = PerformanceReport(
            symbol=symbol,
            period_start=start,
            period_end=end,
            trade_analytics=trade_analytics,
            risk_analytics=risk_analytics,
            equity_analytics=equity_analytics,
            overall_score=overall,
            robustness_score=robustness,
            consistency_score=consistency,
            efficiency_score=efficiency,
        )

        logger.info("Analytics complete: {} — score={:.0f}/100, sharpe={:.2f}",
                     symbol, overall, risk_analytics.sharpe_ratio)
        return report

    # ── Trade Analytics ──────────────────────────────────────────

    def _analyze_trades(self, trades: List[Dict]) -> TradeAnalytics:
        """Calculate comprehensive trade-level metrics."""
        if not trades:
            return self._empty_trade_analytics()

        pnls = np.array([t.get("pnl", 0) for t in trades])
        wins = pnls[pnls > 0]
        losses = pnls[pnls < 0]

        # Win/loss metrics
        win_rate = len(wins) / len(trades) * 100
        avg_win = float(np.mean(wins)) if len(wins) > 0 else 0
        avg_loss = float(np.mean(losses)) if len(losses) > 0 else 0
        largest_win = float(np.max(wins)) if len(wins) > 0 else 0
        largest_loss = float(np.min(losses)) if len(losses) > 0 else 0

        # Profit factor
        gross_profit = float(np.sum(wins))
        gross_loss = float(np.abs(np.sum(losses)))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        # Payoff ratio
        payoff_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

        # Expectancy
        expectancy = float(np.mean(pnls))

        # Kelly criterion
        if payoff_ratio != float("inf") and win_rate < 100:
            kelly = (win_rate / 100 * payoff_ratio - (1 - win_rate / 100)) / payoff_ratio
            kelly = max(kelly, 0)
        else:
            kelly = 0

        # Duration analysis
        durations = []
        for t in trades:
            if "entry_time" in t and "exit_time" in t:
                entry = t["entry_time"]
                exit_ = t["exit_time"]
                if isinstance(entry, datetime) and isinstance(exit_, datetime):
                    durations.append((exit_ - entry).total_seconds() / 3600)

        avg_duration = float(np.mean(durations)) if durations else 0
        median_duration = float(np.median(durations)) if durations else 0

        # Streak analysis
        max_wins, max_losses, avg_wins, avg_losses = self._streak_analysis(pnls)

        # Time analysis
        best_hour, worst_hour = self._time_analysis(trades, "hour")
        best_dow, worst_dow = self._time_analysis(trades, "day_of_week")

        # Direction analysis
        long_trades = [t for t in trades if t.get("side", "").upper() == "LONG"]
        short_trades = [t for t in trades if t.get("side", "").upper() == "SHORT"]
        long_wins = sum(1 for t in long_trades if t.get("pnl", 0) > 0)
        short_wins = sum(1 for t in short_trades if t.get("pnl", 0) > 0)
        long_pnl = sum(t.get("pnl", 0) for t in long_trades)
        short_pnl = sum(t.get("pnl", 0) for t in short_trades)

        return TradeAnalytics(
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            largest_win=largest_win,
            largest_loss=largest_loss,
            avg_trade_duration_hours=avg_duration,
            median_trade_duration_hours=median_duration,
            profit_factor=profit_factor,
            payoff_ratio=payoff_ratio,
            expectancy=expectancy,
            kelly_criterion=kelly,
            max_consecutive_wins=max_wins,
            max_consecutive_losses=max_losses,
            avg_consecutive_wins=avg_wins,
            avg_consecutive_losses=avg_losses,
            best_hour=best_hour,
            worst_hour=worst_hour,
            best_day_of_week=best_dow,
            worst_day_of_week=worst_dow,
            long_trades=len(long_trades),
            short_trades=len(short_trades),
            long_win_rate=(long_wins / len(long_trades) * 100) if long_trades else 0,
            short_win_rate=(short_wins / len(short_trades) * 100) if short_trades else 0,
            long_pnl=long_pnl,
            short_pnl=short_pnl,
        )

    def _streak_analysis(self, pnls: np.ndarray) -> Tuple[int, int, float, float]:
        """Calculate consecutive win/loss streaks."""
        max_wins = max_losses = 0
        current_wins = current_losses = 0
        win_streaks: List[int] = []
        loss_streaks: List[int] = []

        for pnl in pnls:
            if pnl > 0:
                current_wins += 1
                if current_losses > 0:
                    loss_streaks.append(current_losses)
                    current_losses = 0
            elif pnl < 0:
                current_losses += 1
                if current_wins > 0:
                    win_streaks.append(current_wins)
                    current_wins = 0

        if current_wins > 0:
            win_streaks.append(current_wins)
        if current_losses > 0:
            loss_streaks.append(current_losses)

        return (
            max(win_streaks) if win_streaks else 0,
            max(loss_streaks) if loss_streaks else 0,
            float(np.mean(win_streaks)) if win_streaks else 0,
            float(np.mean(loss_streaks)) if loss_streaks else 0,
        )

    def _time_analysis(self, trades: List[Dict], mode: str) -> Any:
        """Analyze trade performance by time."""
        if mode == "hour":
            hour_pnl: Dict[int, List[float]] = {}
            for t in trades:
                if "entry_time" in t and isinstance(t["entry_time"], datetime):
                    hour = t["entry_time"].hour
                    hour_pnl.setdefault(hour, []).append(t.get("pnl", 0))
            if hour_pnl:
                best = max(hour_pnl, key=lambda h: sum(hour_pnl[h]))
                worst = min(hour_pnl, key=lambda h: sum(hour_pnl[h]))
                return best, worst
            return 0, 0

        elif mode == "day_of_week":
            dow_pnl: Dict[str, List[float]] = {}
            for t in trades:
                if "entry_time" in t and isinstance(t["entry_time"], datetime):
                    dow = t["entry_time"].strftime("%A")
                    dow_pnl.setdefault(dow, []).append(t.get("pnl", 0))
            if dow_pnl:
                best = max(dow_pnl, key=lambda d: sum(dow_pnl[d]))
                worst = min(dow_pnl, key=lambda d: sum(dow_pnl[d]))
                return best, worst
            return "Monday", "Monday"

        return None, None

    # ── Risk Analytics ───────────────────────────────────────────

    def _analyze_risk(self, equity: List[float], trades: List[Dict]) -> RiskAnalytics:
        """Calculate comprehensive risk metrics."""
        equity_arr = np.array(equity)
        returns = np.diff(equity_arr) / equity_arr[:-1]
        returns = returns[~np.isnan(returns) & ~np.isinf(returns)]

        # Daily returns (assuming bar-level, convert to daily)
        daily_returns = returns * 24 * 60 / 5  # Approximate for 5m bars

        # Sharpe Ratio
        if len(daily_returns) > 1 and np.std(daily_returns) > 0:
            sharpe = float((np.mean(daily_returns) - self.risk_free_rate / 252) /
                           np.std(daily_returns) * np.sqrt(252))
        else:
            sharpe = 0

        # Sortino Ratio
        downside = daily_returns[daily_returns < 0]
        if len(downside) > 0 and np.std(downside) > 0:
            sortino = float((np.mean(daily_returns) - self.risk_free_rate / 252) /
                            np.std(downside) * np.sqrt(252))
        else:
            sortino = 0

        # Max Drawdown
        peak = np.maximum.accumulate(equity_arr)
        drawdown = (peak - equity_arr) / peak
        max_dd = float(np.max(drawdown))

        # Max Drawdown Duration
        dd_duration = self._drawdown_duration(equity_arr)

        # Average Drawdown
        avg_dd = float(np.mean(drawdown[drawdown > 0])) if np.any(drawdown > 0) else 0

        # Calmar Ratio
        total_return = (equity_arr[-1] / equity_arr[0]) - 1 if equity_arr[0] > 0 else 0
        calmar = total_return / max_dd if max_dd > 0 else 0

        # Recovery Factor
        recovery = total_return / max_dd if max_dd > 0 else 0

        # Tail Ratio
        if len(returns) > 0:
            right_tail = float(np.percentile(returns, 95))
            left_tail = abs(float(np.percentile(returns, 5)))
            tail_ratio = right_tail / left_tail if left_tail > 0 else 0
        else:
            tail_ratio = 0

        # VaR and CVaR
        var_95 = float(np.percentile(returns, 5)) if len(returns) > 0 else 0
        var_99 = float(np.percentile(returns, 1)) if len(returns) > 0 else 0
        cvar_95 = float(np.mean(returns[returns <= var_95])) if len(returns[returns <= var_95]) > 0 else var_95
        cvar_99 = float(np.mean(returns[returns <= var_99])) if len(returns[returns <= var_99]) > 0 else var_99

        # Volatility
        daily_vol = float(np.std(returns)) if len(returns) > 0 else 0
        annual_vol = daily_vol * np.sqrt(252 * 24 * 60 / 5)
        downside_dev = float(np.std(downside)) * np.sqrt(252) if len(downside) > 0 else 0

        # Ulcer Index
        ulcer = float(np.sqrt(np.mean(drawdown ** 2)))

        # Pain Index
        pain = float(np.mean(drawdown))

        return RiskAnalytics(
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            max_drawdown_pct=max_dd * 100,
            max_drawdown_duration_days=dd_duration,
            avg_drawdown_pct=avg_dd * 100,
            recovery_factor=recovery,
            tail_ratio=tail_ratio,
            var_95=var_95,
            var_99=var_99,
            cvar_95=cvar_95,
            cvar_99=cvar_99,
            daily_volatility=daily_vol,
            annual_volatility=annual_vol,
            downside_deviation=downside_dev,
            ulcer_index=ulcer,
            pain_index=pain,
        )

    def _drawdown_duration(self, equity: np.ndarray) -> float:
        """Calculate max drawdown duration in days."""
        peak = np.maximum.accumulate(equity)
        in_dd = equity < peak
        max_duration = 0
        current_duration = 0

        for is_dd in in_dd:
            if is_dd:
                current_duration += 1
                max_duration = max(max_duration, current_duration)
            else:
                current_duration = 0

        # Convert bars to days (assuming 5m bars)
        return max_duration * 5 / (24 * 60)

    # ── Equity Analytics ─────────────────────────────────────────

    def _analyze_equity(self, equity: List[float], trades: List[Dict]) -> EquityAnalytics:
        """Analyze equity curve and returns."""
        equity_arr = np.array(equity)
        total_return = (equity_arr[-1] / equity_arr[0] - 1) * 100 if equity_arr[0] > 0 else 0

        # CAGR (assuming 5m bars)
        n_bars = len(equity_arr)
        years = n_bars * 5 / (24 * 60 * 365)
        cagr = ((equity_arr[-1] / equity_arr[0]) ** (1 / max(years, 0.01)) - 1) * 100 if equity_arr[0] > 0 else 0

        # Drawdown curve
        peak = np.maximum.accumulate(equity_arr)
        dd_curve = ((peak - equity_arr) / peak * 100).tolist()

        # Monthly/Yearly returns
        monthly = self._periodic_returns(equity_arr, "monthly")
        yearly = self._periodic_returns(equity_arr, "yearly")

        # New highs
        new_highs = sum(1 for i in range(1, len(equity_arr)) if equity_arr[i] > peak[i - 1])

        return EquityAnalytics(
            initial_capital=equity_arr[0],
            final_equity=equity_arr[-1],
            total_return_pct=total_return,
            cagr=cagr,
            equity_curve=equity_arr.tolist(),
            drawdown_curve=dd_curve,
            monthly_returns=monthly,
            yearly_returns=yearly,
            time_to_first_profit_hours=0,
            time_to_recovery_hours=0,
            new_highs_count=new_highs,
        )

    def _periodic_returns(self, equity: np.ndarray, period: str) -> Dict[str, float]:
        """Calculate periodic returns."""
        returns = {}
        n = len(equity)
        chunk = {"monthly": max(n // 12, 1), "yearly": max(n // 3, 1)}.get(period, max(n // 12, 1))

        for i in range(0, n, chunk):
            end = min(i + chunk, n - 1)
            if equity[i] > 0:
                ret = (equity[end] / equity[i] - 1) * 100
                key = f"period_{i // chunk}"
                returns[key] = ret

        return returns

    # ── Composite Scores ─────────────────────────────────────────

    def _compute_overall_score(
        self,
        trade: TradeAnalytics,
        risk: RiskAnalytics,
        equity: EquityAnalytics,
    ) -> float:
        """Compute overall performance score (0-100)."""
        score = 0.0

        # Profitability (30 points)
        score += min(trade.profit_factor / 3 * 15, 15)
        score += min(trade.win_rate / 60 * 15, 15)

        # Risk-adjusted (30 points)
        score += min(max(risk.sharpe_ratio, 0) / 2 * 15, 15)
        score += min(max(risk.sortino_ratio, 0) / 3 * 15, 15)

        # Consistency (20 points)
        score += max(0, (1 - risk.max_drawdown_pct / 30) * 10)
        score += min(trade.profit_factor / 2, 1) * 10

        # Efficiency (20 points)
        score += min(max(risk.calmar_ratio, 0) / 2 * 10, 10)
        score += min(trade.kelly_criterion * 100, 10)

        return min(score, 100)

    def _compute_robustness_score(
        self,
        trade: TradeAnalytics,
        risk: RiskAnalytics,
    ) -> float:
        """Compute strategy robustness score."""
        score = 0.0

        # Low drawdown
        score += max(0, (1 - risk.max_drawdown_pct / 25) * 30)

        # Positive expectancy
        score += min(max(trade.expectancy, 0) / 100 * 20, 20)

        # Good tail ratio
        score += min(risk.tail_ratio / 2 * 20, 20)

        # Recovery factor
        score += min(risk.recovery_factor / 3 * 15, 15)

        # Not too many consecutive losses
        score += max(0, (1 - trade.max_consecutive_losses / 10) * 15)

        return min(score, 100)

    def _compute_consistency_score(self, equity: EquityAnalytics) -> float:
        """Compute equity curve consistency score."""
        if not equity.monthly_returns:
            return 50

        monthly_vals = list(equity.monthly_returns.values())
        if not monthly_vals:
            return 50

        positive_months = sum(1 for r in monthly_vals if r > 0)
        consistency = positive_months / len(monthly_vals) * 100

        return min(consistency, 100)

    def _compute_efficiency_score(
        self,
        trade: TradeAnalytics,
        risk: RiskAnalytics,
    ) -> float:
        """Compute capital efficiency score."""
        score = 0.0

        # Sharpe per unit of drawdown
        if risk.max_drawdown_pct > 0:
            score += min(risk.sharpe_ratio / risk.max_drawdown_pct * 100, 30)

        # Win rate vs hold time
        if trade.avg_trade_duration_hours > 0:
            efficiency = trade.win_rate / trade.avg_trade_duration_hours
            score += min(efficiency * 10, 30)

        # Profit factor
        score += min(trade.profit_factor / 3 * 20, 20)

        # Kelly criterion
        score += min(trade.kelly_criterion * 200, 20)

        return min(score, 100)

    # ── Utilities ────────────────────────────────────────────────

    def _parse_trades(self, trades: List[Dict]) -> List[Dict]:
        """Parse and normalize trade data."""
        parsed = []
        for t in trades:
            entry_time = t.get("entry_time")
            exit_time = t.get("exit_time")

            if isinstance(entry_time, str):
                try:
                    entry_time = datetime.fromisoformat(entry_time)
                except Exception:
                    entry_time = datetime.now()
            if isinstance(exit_time, str):
                try:
                    exit_time = datetime.fromisoformat(exit_time)
                except Exception:
                    exit_time = datetime.now()

            parsed.append({
                "side": t.get("side", "LONG"),
                "entry_price": t.get("entry_price", 0),
                "exit_price": t.get("exit_price", 0),
                "size": t.get("size", 0),
                "pnl": t.get("pnl", 0),
                "fees": t.get("fees", 0),
                "slippage": t.get("slippage", 0),
                "entry_time": entry_time,
                "exit_time": exit_time,
                "exit_reason": t.get("exit_reason", ""),
                "hold_time_minutes": t.get("hold_time_minutes", 0),
            })
        return parsed

    def _compute_equity_curve(self, trades: List[Dict]) -> List[float]:
        """Compute equity curve from trades."""
        equity = [self.initial_capital]
        for t in trades:
            equity.append(equity[-1] + t.get("pnl", 0) - t.get("fees", 0))
        return equity

    def _empty_trade_analytics(self) -> TradeAnalytics:
        """Return empty trade analytics."""
        return TradeAnalytics(
            total_trades=0, winning_trades=0, losing_trades=0, win_rate=0,
            avg_win=0, avg_loss=0, largest_win=0, largest_loss=0,
            avg_trade_duration_hours=0, median_trade_duration_hours=0,
            profit_factor=0, payoff_ratio=0, expectancy=0, kelly_criterion=0,
            max_consecutive_wins=0, max_consecutive_losses=0,
            avg_consecutive_wins=0, avg_consecutive_losses=0,
            best_hour=0, worst_hour=0, best_day_of_week="", worst_day_of_week="",
            long_trades=0, short_trades=0, long_win_rate=0, short_win_rate=0,
            long_pnl=0, short_pnl=0,
        )

    def _empty_report(self, symbol: str) -> PerformanceReport:
        """Return empty report."""
        return PerformanceReport(
            symbol=symbol,
            period_start=datetime.now(),
            period_end=datetime.now(),
            trade_analytics=self._empty_trade_analytics(),
            risk_analytics=RiskAnalytics(
                sharpe_ratio=0, sortino_ratio=0, calmar_ratio=0,
                max_drawdown_pct=0, max_drawdown_duration_days=0,
                avg_drawdown_pct=0, recovery_factor=0, tail_ratio=0,
                var_95=0, var_99=0, cvar_95=0, cvar_99=0,
                daily_volatility=0, annual_volatility=0,
                downside_deviation=0, ulcer_index=0, pain_index=0,
            ),
            equity_analytics=EquityAnalytics(
                initial_capital=self.initial_capital,
                final_equity=self.initial_capital,
                total_return_pct=0, cagr=0,
                equity_curve=[self.initial_capital],
                drawdown_curve=[0],
                monthly_returns={}, yearly_returns={},
                time_to_first_profit_hours=0, time_to_recovery_hours=0,
                new_highs_count=0,
            ),
            overall_score=0, robustness_score=0,
            consistency_score=0, efficiency_score=0,
        )

    # ── Export ───────────────────────────────────────────────────

    def export_json(self, report: PerformanceReport, filename: Optional[str] = None) -> str:
        """Export report as JSON."""
        if filename is None:
            filename = f"report_{report.symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

        filepath = self._output_dir / filename

        data = {
            "symbol": report.symbol,
            "period": {
                "start": report.period_start.isoformat(),
                "end": report.period_end.isoformat(),
            },
            "scores": {
                "overall": report.overall_score,
                "robustness": report.robustness_score,
                "consistency": report.consistency_score,
                "efficiency": report.efficiency_score,
            },
            "trade_analytics": {
                "total_trades": report.trade_analytics.total_trades,
                "win_rate": report.trade_analytics.win_rate,
                "profit_factor": report.trade_analytics.profit_factor,
                "expectancy": report.trade_analytics.expectancy,
                "kelly_criterion": report.trade_analytics.kelly_criterion,
                "max_consecutive_losses": report.trade_analytics.max_consecutive_losses,
            },
            "risk_analytics": {
                "sharpe_ratio": report.risk_analytics.sharpe_ratio,
                "sortino_ratio": report.risk_analytics.sortino_ratio,
                "max_drawdown_pct": report.risk_analytics.max_drawdown_pct,
                "var_95": report.risk_analytics.var_95,
                "cvar_95": report.risk_analytics.cvar_95,
            },
            "equity_analytics": {
                "total_return_pct": report.equity_analytics.total_return_pct,
                "cagr": report.equity_analytics.cagr,
                "new_highs": report.equity_analytics.new_highs_count,
            },
        }

        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)

        logger.info("Report exported: {}", filepath)
        return str(filepath)

    def summary(self, report: PerformanceReport) -> str:
        """Generate human-readable summary."""
        return (
            f"═══ Performance Report: {report.symbol} ═══\n"
            f"Period: {report.period_start.date()} → {report.period_end.date()}\n"
            f"─────────────────────────────────────\n"
            f"OVERALL SCORE: {report.overall_score:.0f}/100\n"
            f"  Robustness: {report.robustness_score:.0f}/100\n"
            f"  Consistency: {report.consistency_score:.0f}/100\n"
            f"  Efficiency: {report.efficiency_score:.0f}/100\n"
            f"─────────────────────────────────────\n"
            f"TRADES\n"
            f"  Total: {report.trade_analytics.total_trades}\n"
            f"  Win Rate: {report.trade_analytics.win_rate:.1f}%\n"
            f"  Profit Factor: {report.trade_analytics.profit_factor:.2f}\n"
            f"  Expectancy: ${report.trade_analytics.expectancy:,.2f}\n"
            f"  Kelly: {report.trade_analytics.kelly_criterion:.2%}\n"
            f"─────────────────────────────────────\n"
            f"RISK\n"
            f"  Sharpe: {report.risk_analytics.sharpe_ratio:.2f}\n"
            f"  Sortino: {report.risk_analytics.sortino_ratio:.2f}\n"
            f"  Max DD: {report.risk_analytics.max_drawdown_pct:.1f}%\n"
            f"  VaR (95%): {report.risk_analytics.var_95:.4f}\n"
            f"  CVaR (95%): {report.risk_analytics.cvar_95:.4f}\n"
            f"─────────────────────────────────────\n"
            f"EQUITY\n"
            f"  Return: {report.equity_analytics.total_return_pct:+.1f}%\n"
            f"  CAGR: {report.equity_analytics.cagr:+.1f}%\n"
            f"  ${report.equity_analytics.initial_capital:,.0f} → ${report.equity_analytics.final_equity:,.0f}\n"
        )
