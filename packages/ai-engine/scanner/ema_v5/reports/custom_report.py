"""
EMA_V5 Custom Report — Flexible date range and filter-based reports.
Reads from analytics layer. Pure computation.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

from ..storage.database import EMAv5Database
from ..analytics.performance_calculator import PerformanceCalculator
from ..analytics.risk_metrics import RiskMetrics
from ..analytics.trade_analyzer import TradeAnalyzer
from ..analytics.equity_curve import EquityCurve


class CustomReport:
    """Generates custom EMA_V5 reports with flexible filters."""

    def __init__(self, db: Optional[EMAv5Database] = None) -> None:
        self._db = db or EMAv5Database()
        self._perf = PerformanceCalculator(self._db)
        self._risk = RiskMetrics(self._db)
        self._trades = TradeAnalyzer(self._db)
        self._equity = EquityCurve(self._db)

    def generate(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        symbol: Optional[str] = None,
        side: Optional[str] = None,
        regime: Optional[str] = None,
        min_confidence: Optional[float] = None,
        result_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate report with flexible filters.
        
        Args:
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            symbol: Filter by symbol (e.g., "BTCUSDT")
            side: Filter by side ("LONG" or "SHORT")
            regime: Filter by regime ("BUY_MODE" or "SELL_MODE")
            min_confidence: Minimum confidence threshold (0-1)
            result_filter: Filter by result ("win" or "loss")
        """
        signals = self._db.get_all_signals()

        # Apply filters
        filtered = self._apply_filters(
            signals, start_date, end_date, symbol, side, regime, min_confidence, result_filter
        )
        closed = [s for s in filtered if s.get("result") in ("win", "loss")]

        # Core metrics
        perf = self._perf.compute_all(filtered) if filtered else self._perf._empty_metrics()
        risk = self._risk.compute_all(filtered) if filtered else self._risk._empty_risk()
        trade_data = self._trades.analyze_all(filtered) if filtered else {"trades": [], "quality": {}}

        # Side breakdown
        longs = [s for s in closed if s.get("side") == "LONG"]
        shorts = [s for s in closed if s.get("side") == "SHORT"]

        # Regime breakdown
        regimes = self._regime_breakdown(closed)

        # Symbol breakdown
        symbols = self._symbol_breakdown(closed)

        # Confidence distribution
        conf_dist = self._confidence_distribution(closed)

        # Equity curve
        equity = self._equity.compute(filtered) if filtered else {"points": [], "peak": 0, "current": 0}

        return {
            "report_type": "custom",
            "filters": {
                "start_date": start_date,
                "end_date": end_date,
                "symbol": symbol,
                "side": side,
                "regime": regime,
                "min_confidence": min_confidence,
                "result_filter": result_filter,
            },
            "generated_at": time.time(),
            "generated_at_str": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "summary": {
                "total_signals": len(filtered),
                "total_trades": len(closed),
                "wins": perf.get("wins", 0),
                "losses": perf.get("losses", 0),
                "win_rate": perf.get("win_rate", 0),
                "total_pnl": perf.get("total_pnl", 0),
                "avg_pnl": perf.get("avg_pnl", 0),
                "profit_factor": perf.get("profit_factor", 0),
                "expectancy": perf.get("expectancy", 0),
                "avg_hold_minutes": perf.get("avg_hold_minutes", 0),
            },
            "sides": {
                "long_trades": len(longs),
                "short_trades": len(shorts),
                "long_pnl": round(sum(s.get("pnl", 0) for s in longs), 4),
                "short_pnl": round(sum(s.get("pnl", 0) for s in shorts), 4),
            },
            "regimes": regimes,
            "symbols": symbols,
            "confidence_distribution": conf_dist,
            "risk": {
                "max_drawdown_pct": risk.get("max_drawdown_pct", 0),
                "sharpe_ratio": risk.get("sharpe_ratio", 0),
                "sortino_ratio": risk.get("sortino_ratio", 0),
            },
            "quality": trade_data.get("quality", {}),
            "equity_peak": equity.get("peak", 0),
            "equity_current": equity.get("current", 0),
        }

    def _apply_filters(self, signals: List[Dict], start_date: Optional[str],
                       end_date: Optional[str], symbol: Optional[str],
                       side: Optional[str], regime: Optional[str],
                       min_confidence: Optional[float],
                       result_filter: Optional[str]) -> List[Dict]:
        """Apply all filters to signals."""
        filtered = list(signals)

        if start_date:
            filtered = [s for s in filtered if (s.get("date", "") or "") >= start_date]
        if end_date:
            filtered = [s for s in filtered if (s.get("date", "") or "") <= end_date]
        if symbol:
            filtered = [s for s in filtered if s.get("symbol", "") == symbol]
        if side:
            filtered = [s for s in filtered if s.get("side", "") == side]
        if regime:
            filtered = [s for s in filtered if s.get("regime", "") == regime]
        if min_confidence is not None:
            filtered = [s for s in filtered if (s.get("confidence", 0) or 0) >= min_confidence]
        if result_filter:
            filtered = [s for s in filtered if s.get("result", "") == result_filter]

        return filtered

    def _regime_breakdown(self, trades: List[Dict]) -> Dict[str, Dict]:
        """Break down by regime."""
        regimes: Dict[str, List] = {}
        for t in trades:
            regime = t.get("regime", "unknown")
            regimes.setdefault(regime, []).append(t)

        result = {}
        for regime, trades_r in regimes.items():
            wins = sum(1 for t in trades_r if t.get("result") == "win")
            total = len(trades_r)
            result[regime] = {
                "trades": total,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "pnl": round(sum(t.get("pnl", 0) for t in trades_r), 4),
            }
        return result

    def _symbol_breakdown(self, trades: List[Dict]) -> Dict[str, Dict]:
        """Break down by symbol."""
        symbols: Dict[str, List] = {}
        for t in trades:
            sym = t.get("symbol", "")
            symbols.setdefault(sym, []).append(t)

        result = {}
        for sym, trades_s in symbols.items():
            wins = sum(1 for t in trades_s if t.get("result") == "win")
            total = len(trades_s)
            result[sym] = {
                "trades": total,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                "pnl": round(sum(t.get("pnl", 0) for t in trades_s), 4),
            }
        return result

    def _confidence_distribution(self, trades: List[Dict]) -> Dict[str, int]:
        """Distribution of confidence levels."""
        buckets = {
            "90-92%": 0, "92-94%": 0, "94-96%": 0, "96-98%": 0, "98-100%": 0,
        }
        for t in trades:
            conf = (t.get("confidence", 0) or 0) * 100
            if conf >= 98:
                buckets["98-100%"] += 1
            elif conf >= 96:
                buckets["96-98%"] += 1
            elif conf >= 94:
                buckets["94-96%"] += 1
            elif conf >= 92:
                buckets["92-94%"] += 1
            elif conf >= 90:
                buckets["90-92%"] += 1
        return buckets
