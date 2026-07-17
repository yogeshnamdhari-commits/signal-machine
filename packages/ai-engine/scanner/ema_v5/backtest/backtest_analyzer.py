"""
EMA_V5 Backtest Analyzer — Deep analysis of backtest results.
Isolated from existing analyzer. Reads from backtest engine output.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from .backtest_engine import EMAv5BacktestResult, EMAv5Trade


class EMAv5BacktestAnalyzer:
    """Analyzes EMA_V5 backtest results in depth."""

    def analyze(self, result: EMAv5BacktestResult) -> Dict[str, Any]:
        """Complete analysis of backtest results."""
        if not result.trades:
            return {"error": "No trades to analyze"}

        return {
            "summary": self._summary(result),
            "trade_distribution": self._trade_distribution(result),
            "time_analysis": self._time_analysis(result),
            "regime_analysis": self._regime_analysis(result),
            "exit_analysis": self._exit_analysis(result),
            "streak_analysis": self._streak_analysis(result),
            "risk_analysis": self._risk_analysis(result),
        }

    def _summary(self, result: EMAv5BacktestResult) -> Dict[str, Any]:
        """Core summary metrics."""
        return {
            "total_pnl": result.total_pnl,
            "total_return_pct": result.total_return_pct,
            "final_balance": result.final_balance,
            "total_trades": result.total_trades,
            "win_rate": result.win_rate,
            "profit_factor": result.profit_factor,
            "expectancy": result.expectancy,
            "avg_r": result.avg_r,
            "max_drawdown_pct": result.max_drawdown_pct,
            "sharpe_ratio": result.sharpe_ratio,
        }

    def _trade_distribution(self, result: EMAv5BacktestResult) -> Dict[str, Any]:
        """Distribution of trade PnL."""
        pnls = [t.pnl for t in result.trades]
        if not pnls:
            return {}

        # PnL buckets
        buckets = {"large_loss": 0, "small_loss": 0, "small_win": 0, "large_win": 0}
        for p in pnls:
            if p < -100:
                buckets["large_loss"] += 1
            elif p < 0:
                buckets["small_loss"] += 1
            elif p < 100:
                buckets["small_win"] += 1
            else:
                buckets["large_win"] += 1

        # Side distribution
        longs = [t for t in result.trades if t.side == "LONG"]
        shorts = [t for t in result.trades if t.side == "SHORT"]

        return {
            "pnl_distribution": buckets,
            "long_count": len(longs),
            "short_count": len(shorts),
            "long_win_rate": round(sum(1 for t in longs if t.pnl > 0) / len(longs) * 100, 1) if longs else 0,
            "short_win_rate": round(sum(1 for t in shorts if t.pnl > 0) / len(shorts) * 100, 1) if shorts else 0,
            "long_pnl": round(sum(t.pnl for t in longs), 4),
            "short_pnl": round(sum(t.pnl for t in shorts), 4),
        }

    def _time_analysis(self, result: EMAv5BacktestResult) -> Dict[str, Any]:
        """Time-based analysis."""
        holds = [t.hold_bars for t in result.trades]
        if not holds:
            return {}

        avg_hold = sum(holds) / len(holds)
        # Convert bars to minutes (assuming 5m timeframe)
        avg_hold_minutes = avg_hold * 5

        return {
            "avg_hold_bars": round(avg_hold, 1),
            "avg_hold_minutes": round(avg_hold_minutes, 1),
            "min_hold_bars": min(holds),
            "max_hold_bars": max(holds),
        }

    def _regime_analysis(self, result: EMAv5BacktestResult) -> Dict[str, Any]:
        """Performance by regime."""
        regimes: Dict[str, List[EMAv5Trade]] = {}
        for t in result.trades:
            regimes.setdefault(t.regime, []).append(t)

        regime_stats = {}
        for regime, trades in regimes.items():
            wins = sum(1 for t in trades if t.pnl > 0)
            pnl = sum(t.pnl for t in trades)
            regime_stats[regime] = {
                "trades": len(trades),
                "win_rate": round(wins / len(trades) * 100, 1),
                "total_pnl": round(pnl, 4),
                "avg_pnl": round(pnl / len(trades), 4),
            }

        return regime_stats

    def _exit_analysis(self, result: EMAv5BacktestResult) -> Dict[str, Any]:
        """Exit reason breakdown."""
        exits: Dict[str, List[EMAv5Trade]] = {}
        for t in result.trades:
            exits.setdefault(t.exit_reason, []).append(t)

        exit_stats = {}
        for reason, trades in exits.items():
            wins = sum(1 for t in trades if t.pnl > 0)
            pnl = sum(t.pnl for t in trades)
            exit_stats[reason] = {
                "count": len(trades),
                "win_rate": round(wins / len(trades) * 100, 1),
                "total_pnl": round(pnl, 4),
            }

        return exit_stats

    def _streak_analysis(self, result: EMAv5BacktestResult) -> Dict[str, Any]:
        """Win/loss streak analysis."""
        streaks = {"max_win_streak": 0, "max_loss_streak": 0, "current_streak": 0, "current_type": ""}
        current = 0
        current_type = ""

        for t in result.trades:
            result_type = "win" if t.pnl > 0 else "loss"
            if result_type == current_type:
                current += 1
            else:
                current_type = result_type
                current = 1

            if current_type == "win":
                streaks["max_win_streak"] = max(streaks["max_win_streak"], current)
            else:
                streaks["max_loss_streak"] = max(streaks["max_loss_streak"], current)

        streaks["current_streak"] = current
        streaks["current_type"] = current_type
        return streaks

    def _risk_analysis(self, result: EMAv5BacktestResult) -> Dict[str, Any]:
        """Risk analysis."""
        if not result.equity_curve:
            return {}

        # Drawdown series
        peak = result.equity_curve[0]
        drawdowns = []
        for eq in result.equity_curve:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            drawdowns.append(dd)

        # Time in drawdown
        in_dd = sum(1 for d in drawdowns if d > 0)
        dd_pct = in_dd / len(drawdowns) * 100 if drawdowns else 0

        return {
            "max_drawdown_pct": result.max_drawdown_pct,
            "max_drawdown_usd": result.max_drawdown_usd,
            "time_in_drawdown_pct": round(dd_pct, 1),
            "recovery_factor": round(result.total_pnl / result.max_drawdown_usd, 2) if result.max_drawdown_usd > 0 else 0,
        }

    def compare_results(self, results: List[EMAv5BacktestResult]) -> Dict[str, Any]:
        """Compare multiple backtest results (e.g., different parameters)."""
        if not results:
            return {}

        comparison = []
        for i, r in enumerate(results):
            comparison.append({
                "run": i + 1,
                "total_pnl": r.total_pnl,
                "win_rate": r.win_rate,
                "profit_factor": r.profit_factor,
                "max_drawdown_pct": r.max_drawdown_pct,
                "sharpe_ratio": r.sharpe_ratio,
                "total_trades": r.total_trades,
            })

        # Best by each metric
        best_pnl = max(comparison, key=lambda x: x["total_pnl"])
        best_wr = max(comparison, key=lambda x: x["win_rate"])
        best_pf = max(comparison, key=lambda x: x["profit_factor"])
        best_sharpe = max(comparison, key=lambda x: x["sharpe_ratio"])

        return {
            "runs": comparison,
            "best_by_pnl": best_pnl["run"],
            "best_by_win_rate": best_wr["run"],
            "best_by_profit_factor": best_pf["run"],
            "best_by_sharpe": best_sharpe["run"],
        }
