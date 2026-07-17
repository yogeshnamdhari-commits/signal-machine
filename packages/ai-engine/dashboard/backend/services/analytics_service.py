"""
Performance Analytics Service — Performance reporting and analytics.

Generates:
- Daily, weekly, monthly, YTD performance
- Performance metrics (Sharpe, Sortino, Calmar, etc.)
- Best/worst symbols and exchanges
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class AnalyticsService:
    """
    Performance analytics and reporting service.
    Generates comprehensive performance metrics and reports.
    """

    REPORTS_DIR = Path("data/reports")

    def __init__(self) -> None:
        self.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

        self._trade_history: List[Dict[str, Any]] = []
        self._daily_pnl: Dict[str, float] = {}
        self._symbol_performance: Dict[str, Dict[str, Any]] = {}
        self._exchange_performance: Dict[str, Dict[str, Any]] = {}
        self._equity_history: List[Dict[str, Any]] = []
        self._max_history = 10000

    def record_trade(self, trade: Dict[str, Any]) -> None:
        """Record a completed trade."""
        self._trade_history.append({
            **trade,
            "recorded_at": time.time(),
        })
        if len(self._trade_history) > self._max_history:
            self._trade_history = self._trade_history[-self._max_history // 2:]

        # Update daily PnL
        day = time.strftime("%Y-%m-%d")
        pnl = trade.get("pnl", 0)
        self._daily_pnl[day] = self._daily_pnl.get(day, 0) + pnl

        # Update symbol performance
        symbol = trade.get("symbol", "unknown")
        if symbol not in self._symbol_performance:
            self._symbol_performance[symbol] = {
                "trades": 0, "wins": 0, "losses": 0,
                "total_pnl": 0, "gross_profit": 0, "gross_loss": 0,
            }
        sp = self._symbol_performance[symbol]
        sp["trades"] += 1
        sp["total_pnl"] += pnl
        if pnl > 0:
            sp["wins"] += 1
            sp["gross_profit"] += pnl
        elif pnl < 0:
            sp["losses"] += 1
            sp["gross_loss"] += abs(pnl)

        # Update exchange performance
        exchange = trade.get("exchange", "unknown")
        if exchange not in self._exchange_performance:
            self._exchange_performance[exchange] = {
                "trades": 0, "wins": 0, "losses": 0,
                "total_pnl": 0, "total_fees": 0,
            }
        ep = self._exchange_performance[exchange]
        ep["trades"] += 1
        ep["total_pnl"] += pnl
        ep["total_fees"] += trade.get("fee", 0)
        if pnl > 0:
            ep["wins"] += 1
        elif pnl < 0:
            ep["losses"] += 1

    def record_equity(self, equity: float) -> None:
        """Record equity snapshot."""
        self._equity_history.append({
            "timestamp": time.time(),
            "equity": equity,
        })
        if len(self._equity_history) > self._max_history:
            self._equity_history = self._equity_history[-self._max_history // 2:]

    def get_performance_analytics(self) -> Dict[str, Any]:
        """Get comprehensive performance analytics."""
        total_trades = len(self._trade_history)
        wins = sum(1 for t in self._trade_history if t.get("pnl", 0) > 0)
        losses = sum(1 for t in self._trade_history if t.get("pnl", 0) < 0)

        gross_profit = sum(t.get("pnl", 0) for t in self._trade_history if t.get("pnl", 0) > 0)
        gross_loss = abs(sum(t.get("pnl", 0) for t in self._trade_history if t.get("pnl", 0) < 0))

        win_rate = (wins / max(total_trades, 1)) * 100
        profit_factor = gross_profit / max(gross_loss, 0.01)

        avg_win = gross_profit / max(wins, 1)
        avg_loss = gross_loss / max(losses, 1)
        expectancy = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss)

        # Calmar ratio
        max_dd = self._calculate_max_drawdown()
        total_return = sum(self._daily_pnl.values())
        calmar = total_return / max(max_dd, 0.01) if max_dd > 0 else 0

        # Best/worst
        best_symbol = max(
            self._symbol_performance.items(),
            key=lambda x: x[1]["total_pnl"],
            default=("none", {"total_pnl": 0}),
        )
        worst_symbol = min(
            self._symbol_performance.items(),
            key=lambda x: x[1]["total_pnl"],
            default=("none", {"total_pnl": 0}),
        )
        best_exchange = max(
            self._exchange_performance.items(),
            key=lambda x: x[1]["total_pnl"],
            default=("none", {"total_pnl": 0}),
        )
        worst_exchange = min(
            self._exchange_performance.items(),
            key=lambda x: x[1]["total_pnl"],
            default=("none", {"total_pnl": 0}),
        )

        return {
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 4),
            "expectancy": round(expectancy, 4),
            "calmar_ratio": round(calmar, 4),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "net_profit": round(gross_profit - gross_loss, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "max_drawdown": round(max_dd, 2),
            "daily_pnl": self._daily_pnl,
            "best_symbol": {"name": best_symbol[0], "pnl": best_symbol[1]["total_pnl"]},
            "worst_symbol": {"name": worst_symbol[0], "pnl": worst_symbol[1]["total_pnl"]},
            "best_exchange": {"name": best_exchange[0], "pnl": best_exchange[1]["total_pnl"]},
            "worst_exchange": {"name": worst_exchange[0], "pnl": worst_exchange[1]["total_pnl"]},
            "symbol_performance": {
                k: {**v, "win_rate": round(v["wins"] / max(v["trades"], 1) * 100, 2)}
                for k, v in self._symbol_performance.items()
            },
            "exchange_performance": {
                k: {**v, "win_rate": round(v["wins"] / max(v["trades"], 1) * 100, 2)}
                for k, v in self._exchange_performance.items()
            },
            "timestamp": time.time(),
        }

    def _calculate_max_drawdown(self) -> float:
        """Calculate maximum drawdown from equity history."""
        if not self._equity_history:
            return 0.0

        peak = 0
        max_dd = 0
        for entry in self._equity_history:
            eq = entry.get("equity", 0)
            if eq > peak:
                peak = eq
            dd = (peak - eq) / max(peak, 1) * 100
            if dd > max_dd:
                max_dd = dd

        return max_dd

    def generate_report(
        self, report_type: str, format: str = "json"
    ) -> Path:
        """Generate a performance report."""
        data = self.get_performance_analytics()
        data["report_type"] = report_type
        data["generated_at"] = time.time()

        if format == "json":
            path = self.REPORTS_DIR / f"{report_type}_report.json"
            with open(path, "w") as f:
                json.dump(data, f, indent=2, default=str)
        elif format == "csv":
            path = self.REPORTS_DIR / f"{report_type}_report.csv"
            if self._trade_history:
                keys = self._trade_history[0].keys()
                with open(path, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=keys)
                    writer.writeheader()
                    writer.writerows(self._trade_history[-1000:])
        else:
            path = self.REPORTS_DIR / f"{report_type}_report.json"
            with open(path, "w") as f:
                json.dump(data, f, indent=2, default=str)

        logger.info("[Analytics] Report generated: {}", path)
        return path
