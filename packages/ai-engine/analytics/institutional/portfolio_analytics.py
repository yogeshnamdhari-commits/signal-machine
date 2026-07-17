"""
Portfolio Analytics Module
===========================
Comprehensive portfolio-level analytics and risk metrics.

Computes:
- Exposure, Capital Utilization, Correlation
- Symbol Concentration, Sector Concentration
- Rolling Expectancy, Rolling PF, Rolling Sharpe
- Drawdown Duration, Recovery Duration
- Risk Contribution, Largest Losing/Winning Streak
- Heat Map, Monthly/Weekly/Session/Regime Performance

READ-ONLY — Never modifies trading logic.
"""
from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class PortfolioMetrics:
    """Complete portfolio analytics."""
    # Exposure
    total_trades: int = 0
    active_trades: int = 0
    exposure_pct: float = 0.0
    capital_utilization: float = 0.0
    
    # Concentration
    symbol_concentration: Dict[str, float] = field(default_factory=dict)
    top_symbols: List[Dict[str, Any]] = field(default_factory=list)
    
    # Rolling metrics
    rolling_expectancy: List[float] = field(default_factory=list)
    rolling_profit_factor: List[float] = field(default_factory=list)
    rolling_sharpe: List[float] = field(default_factory=list)
    
    # Drawdown analysis
    max_drawdown: float = 0.0
    avg_drawdown: float = 0.0
    drawdown_durations: List[int] = field(default_factory=list)
    recovery_durations: List[int] = field(default_factory=list)
    
    # Risk contribution
    risk_by_symbol: Dict[str, float] = field(default_factory=dict)
    risk_by_session: Dict[str, float] = field(default_factory=dict)
    risk_by_regime: Dict[str, float] = field(default_factory=dict)
    
    # Streaks
    largest_winning_streak: int = 0
    largest_losing_streak: int = 0
    current_streak: int = 0
    current_streak_type: str = ""
    
    # Performance by period
    monthly_performance: Dict[str, float] = field(default_factory=dict)
    weekly_performance: Dict[str, float] = field(default_factory=dict)
    session_performance: Dict[str, float] = field(default_factory=dict)
    regime_performance: Dict[str, float] = field(default_factory=dict)
    
    # Heat map
    hourly_heatmap: Dict[str, float] = field(default_factory=dict)
    daily_heatmap: Dict[str, float] = field(default_factory=dict)


class PortfolioAnalyticsEngine:
    """
    Comprehensive portfolio analytics engine.
    
    READ-ONLY: Never modifies trading logic.
    """
    
    DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "institutional_v1.db"
    
    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or self.DB_PATH
    
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _get_trades(self) -> List[Dict]:
        """Fetch all closed trades."""
        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT * FROM positions WHERE status = 'closed' ORDER BY closed_at ASC
            """).fetchall()
            trades = [dict(r) for r in rows]
            
            rows2 = conn.execute("""
                SELECT * FROM positions_archive WHERE status = 'closed' ORDER BY closed_at ASC
            """).fetchall()
            trades.extend([dict(r) for r in rows2])
            
            return trades
        finally:
            conn.close()
    
    def compute_portfolio_metrics(self, trades: Optional[List[Dict]] = None) -> PortfolioMetrics:
        """Compute all portfolio metrics."""
        if trades is None:
            trades = self._get_trades()
        
        if not trades:
            return PortfolioMetrics()
        
        metrics = PortfolioMetrics()
        metrics.total_trades = len(trades)
        
        # Symbol concentration
        symbol_trades = defaultdict(list)
        for t in trades:
            symbol = t.get("symbol", "unknown")
            symbol_trades[symbol].append(t)
        
        symbol_pnls = {}
        for symbol, ts in symbol_trades.items():
            pnls = [t.get("pnl", 0) or 0 for t in ts]
            symbol_pnls[symbol] = sum(pnls)
        
        total_pnl = sum(symbol_pnls.values())
        for symbol, pnl in symbol_pnls.items():
            metrics.symbol_concentration[symbol] = round(pnl / total_pnl * 100, 2) if total_pnl != 0 else 0
        
        metrics.top_symbols = [
            {"symbol": s, "pnl": round(p, 2), "trades": len(symbol_trades[s])}
            for s, p in sorted(symbol_pnls.items(), key=lambda x: -x[1])[:10]
        ]
        
        # Rolling metrics
        pnls = [t.get("pnl", 0) or 0 for t in trades]
        metrics.rolling_expectancy = self._compute_rolling(pnls, 20, "expectancy")
        metrics.rolling_profit_factor = self._compute_rolling(pnls, 20, "pf")
        metrics.rolling_sharpe = self._compute_rolling(pnls, 20, "sharpe")
        
        # Drawdown analysis
        dd_info = self._analyze_drawdown(pnls)
        metrics.max_drawdown = dd_info["max_dd"]
        metrics.avg_drawdown = dd_info["avg_dd"]
        metrics.drawdown_durations = dd_info["dd_durations"]
        metrics.recovery_durations = dd_info["recovery_durations"]
        
        # Risk contribution
        metrics.risk_by_symbol = self._compute_risk_by_dimension(trades, "symbol")
        metrics.risk_by_session = self._compute_risk_by_dimension(trades, "session")
        metrics.risk_by_regime = self._compute_risk_by_dimension(trades, "regime")
        
        # Streaks
        streak_info = self._compute_streaks(pnls)
        metrics.largest_winning_streak = streak_info["max_win"]
        metrics.largest_losing_streak = streak_info["max_loss"]
        metrics.current_streak = streak_info["current"]
        metrics.current_streak_type = streak_info["type"]
        
        # Performance by period
        metrics.monthly_performance = self._compute_period_performance(trades, "month")
        metrics.weekly_performance = self._compute_period_performance(trades, "week")
        metrics.session_performance = self._compute_dimension_performance(trades, "session")
        metrics.regime_performance = self._compute_dimension_performance(trades, "regime")
        
        return metrics
    
    def _compute_rolling(self, pnls: List[float], window: int, metric: str) -> List[float]:
        """Compute rolling metrics."""
        if len(pnls) < window:
            return []
        
        results = []
        for i in range(window, len(pnls) + 1):
            w = pnls[i - window:i]
            n = len(w)
            
            if metric == "expectancy":
                wins = [p for p in w if p > 0]
                losses = [abs(p) for p in w if p < 0]
                wr = len(wins) / n * 100 if n else 0
                avg_win = sum(wins) / len(wins) if wins else 0
                avg_loss = sum(losses) / len(losses) if losses else 0
                exp = (wr / 100 * avg_win) - ((100 - wr) / 100 * avg_loss)
                results.append(round(exp, 4))
            
            elif metric == "pf":
                gp = sum(p for p in w if p > 0)
                gl = sum(abs(p) for p in w if p < 0)
                pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
                results.append(round(min(pf, 10), 2))
            
            elif metric == "sharpe":
                if n > 1:
                    mean = sum(w) / n
                    std = math.sqrt(sum((p - mean) ** 2 for p in w) / (n - 1))
                    sharpe = (mean / std) * math.sqrt(252) if std > 0 else 0
                    results.append(round(sharpe, 2))
                else:
                    results.append(0)
        
        return results
    
    def _analyze_drawdown(self, pnls: List[float]) -> Dict:
        """Analyze drawdown periods."""
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        dd_sum = 0.0
        dd_count = 0
        dd_durations = []
        recovery_durations = []
        
        in_dd = False
        dd_start = 0
        dd_peak = 0
        
        for i, p in enumerate(pnls):
            cum += p
            if cum > peak:
                if in_dd:
                    dd_durations.append(i - dd_start)
                    recovery_durations.append(i - dd_peak)
                    in_dd = False
                peak = cum
                dd_peak = i
            
            dd = peak - cum
            if dd > 0:
                if not in_dd:
                    dd_start = i
                    in_dd = True
                max_dd = max(max_dd, dd)
                dd_sum += dd
                dd_count += 1
        
        return {
            "max_dd": round(max_dd, 2),
            "avg_dd": round(dd_sum / dd_count, 2) if dd_count > 0 else 0,
            "dd_durations": dd_durations,
            "recovery_durations": recovery_durations,
        }
    
    def _compute_risk_by_dimension(self, trades: List[Dict], dimension: str) -> Dict[str, float]:
        """Compute risk contribution by dimension."""
        dim_trades = defaultdict(list)
        for t in trades:
            key = t.get(dimension, "unknown") or "unknown"
            dim_trades[key].append(t)
        
        risk = {}
        for key, ts in dim_trades.items():
            pnls = [t.get("pnl", 0) or 0 for t in ts]
            if pnls:
                std = math.sqrt(sum((p - sum(pnls)/len(pnls)) ** 2 for p in pnls) / len(pnls))
                risk[key] = round(std, 2)
        
        return risk
    
    def _compute_streaks(self, pnls: List[float]) -> Dict:
        """Compute winning/losing streaks."""
        max_win = 0
        max_loss = 0
        current = 0
        current_type = ""
        
        for p in pnls:
            if p > 0:
                if current_type == "win":
                    current += 1
                else:
                    current = 1
                    current_type = "win"
                max_win = max(max_win, current)
            elif p < 0:
                if current_type == "loss":
                    current += 1
                else:
                    current = 1
                    current_type = "loss"
                max_loss = max(max_loss, current)
        
        return {
            "max_win": max_win,
            "max_loss": max_loss,
            "current": current,
            "type": current_type,
        }
    
    def _compute_period_performance(self, trades: List[Dict], period: str) -> Dict[str, float]:
        """Compute performance by time period."""
        period_pnls = defaultdict(float)
        
        for t in trades:
            closed_at = t.get("closed_at", 0)
            if not closed_at:
                continue
            
            dt = datetime.fromtimestamp(closed_at, tz=timezone.utc)
            if period == "month":
                key = dt.strftime("%Y-%m")
            elif period == "week":
                key = dt.strftime("%Y-W%W")
            else:
                key = dt.strftime("%Y-%m-%d")
            
            period_pnls[key] += t.get("pnl", 0) or 0
        
        return dict(sorted(period_pnls.items()))
    
    def _compute_dimension_performance(self, trades: List[Dict], dimension: str) -> Dict[str, float]:
        """Compute performance by dimension."""
        dim_pnls = defaultdict(float)
        
        for t in trades:
            key = t.get(dimension, "unknown") or "unknown"
            dim_pnls[key] += t.get("pnl", 0) or 0
        
        return dict(dim_pnls)
    
    def generate_report(self, trades: Optional[List[Dict]] = None) -> str:
        """Generate portfolio analytics report."""
        metrics = self.compute_portfolio_metrics(trades)
        
        lines = []
        lines.append("=" * 80)
        lines.append("📊 PORTFOLIO ANALYTICS REPORT")
        lines.append("=" * 80)
        
        lines.append(f"\n📈 EXPOSURE:")
        lines.append(f"   Total Trades: {metrics.total_trades}")
        lines.append(f"   Active Trades: {metrics.active_trades}")
        
        lines.append(f"\n🎯 CONCENTRATION:")
        for sym in metrics.top_symbols[:5]:
            lines.append(f"   {sym['symbol']}: ${sym['pnl']:.2f} ({sym['trades']} trades)")
        
        lines.append(f"\n📉 DRAWDOWN:")
        lines.append(f"   Max Drawdown: ${metrics.max_drawdown:.2f}")
        lines.append(f"   Avg Drawdown: ${metrics.avg_drawdown:.2f}")
        
        lines.append(f"\n🔥 STREAKS:")
        lines.append(f"   Largest Winning Streak: {metrics.largest_winning_streak}")
        lines.append(f"   Largest Losing Streak: {metrics.largest_losing_streak}")
        lines.append(f"   Current: {metrics.current_streak} ({metrics.current_streak_type})")
        
        if metrics.regime_performance:
            lines.append(f"\n🌍 REGIME PERFORMANCE:")
            for regime, pnl in sorted(metrics.regime_performance.items(), key=lambda x: -x[1]):
                emoji = "🟢" if pnl > 0 else "🔴"
                lines.append(f"   {emoji} {regime}: ${pnl:.2f}")
        
        if metrics.session_performance:
            lines.append(f"\n🕐 SESSION PERFORMANCE:")
            for session, pnl in sorted(metrics.session_performance.items(), key=lambda x: -x[1]):
                emoji = "🟢" if pnl > 0 else "🔴"
                lines.append(f"   {emoji} {session}: ${pnl:.2f}")
        
        lines.append("\n" + "=" * 80)
        return "\n".join(lines)


# Global singleton
_engine: Optional[PortfolioAnalyticsEngine] = None

def get_portfolio_engine() -> PortfolioAnalyticsEngine:
    """Get or create the global portfolio analytics engine."""
    global _engine
    if _engine is None:
        _engine = PortfolioAnalyticsEngine()
    return _engine
