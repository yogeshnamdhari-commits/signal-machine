"""
KPI Dashboard — Profit-focused metrics for the trading system.

Per Priority 8: Primary metrics should become:
    - Profit Factor
    - Expectancy (R)
    - Average Winner
    - Average Loser
    - Net Expectancy
    - Drawdown
    - Portfolio Heat
    - Capital Utilization
    - Sharpe Ratio
    - Return on Risk

Win rate should be a secondary metric.

READ-ONLY: never modifies upstream data.
"""
from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


@dataclass
class KPIDashboard:
    """Complete KPI dashboard data."""
    # PRIMARY METRICS (profit-focused)
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    avg_winner_r: float = 0.0
    avg_loser_r: float = 0.0
    net_expectancy_usd: float = 0.0
    max_drawdown_pct: float = 0.0
    current_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    return_on_risk: float = 0.0
    recovery_factor: float = 0.0

    # SECONDARY METRICS
    win_rate: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    avg_trade_pnl: float = 0.0

    # PORTFOLIO METRICS
    open_positions: int = 0
    portfolio_heat_pct: float = 0.0
    capital_utilization_pct: float = 0.0
    long_exposure_pct: float = 0.0
    short_exposure_pct: float = 0.0

    # SESSION METRICS
    best_session: str = ""
    worst_session: str = ""

    # REGIME METRICS
    best_regime: str = ""
    worst_regime: str = ""

    # Timestamp
    generated_at: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "primary": {
                "profit_factor": round(self.profit_factor, 2),
                "expectancy_r": round(self.expectancy_r, 3),
                "avg_winner_r": round(self.avg_winner_r, 3),
                "avg_loser_r": round(self.avg_loser_r, 3),
                "net_expectancy_usd": round(self.net_expectancy_usd, 2),
                "max_drawdown_pct": round(self.max_drawdown_pct, 2),
                "current_drawdown_pct": round(self.current_drawdown_pct, 2),
                "sharpe_ratio": round(self.sharpe_ratio, 2),
                "return_on_risk": round(self.return_on_risk, 2),
            },
            "secondary": {
                "win_rate": round(self.win_rate, 3),
                "total_trades": self.total_trades,
                "winning_trades": self.winning_trades,
                "losing_trades": self.losing_trades,
                "total_pnl": round(self.total_pnl, 2),
                "gross_profit": round(self.gross_profit, 2),
                "gross_loss": round(self.gross_loss, 2),
                "avg_trade_pnl": round(self.avg_trade_pnl, 4),
            },
            "portfolio": {
                "open_positions": self.open_positions,
                "portfolio_heat_pct": round(self.portfolio_heat_pct, 2),
                "capital_utilization_pct": round(self.capital_utilization_pct, 2),
                "long_exposure_pct": round(self.long_exposure_pct, 2),
                "short_exposure_pct": round(self.short_exposure_pct, 2),
            },
            "insights": {
                "best_session": self.best_session,
                "worst_session": self.worst_session,
                "best_regime": self.best_regime,
                "worst_regime": self.worst_regime,
            },
        }

    def render_terminal(self) -> str:
        """Render KPI dashboard as terminal string — Profit-focused hierarchy."""
        lines = []
        lines.append("=" * 76)
        lines.append("  PROFIT-FOCUSED KPI DASHBOARD")
        lines.append("  Goal: Maximize Net Profit (NOT Win Rate)")
        lines.append("=" * 76)
        lines.append("")

        # ── PRIMARY: The metrics that matter most ──
        lines.append("┌─ PRIMARY METRICS ─" + "─" * 56 + "┐")
        lines.append(f"│  1. Profit Factor:       {self.profit_factor:>8.2f}                      │")
        lines.append(f"│  2. Expectancy:        {self.expectancy_r:>+7.3f}R                        │")
        lines.append(f"│  3. Net Profit:       ${self.net_expectancy_usd:>+9.2f}                        │")
        lines.append(f"│  4. Max Drawdown:      {self.max_drawdown_pct:>7.2f}%                        │")
        lines.append(f"│  5. Return on Risk:    {self.return_on_risk:>+7.2f}                          │")
        lines.append(f"│  6. Avg Winner:        {self.avg_winner_r:>+7.3f}R    │  Avg Loser: {self.avg_loser_r:>+7.3f}R  │")
        lines.append("└" + "─" * 74 + "┘")
        lines.append("")

        # ── SECONDARY: Informational ──
        lines.append("┌─ SECONDARY (Informational) ─" + "─" * 46 + "┐")
        lines.append(f"│  7. Capital Utilization: {self.capital_utilization_pct:>6.2f}%                        │")
        lines.append(f"│  8. Recovery Factor:     {self.recovery_factor:>+7.2f}                          │")
        lines.append(f"│  9. Trade Frequency:     {self.total_trades:>6} trades                     │")
        lines.append(f"│  10. Win Rate:           {self.win_rate:>6.1f}%   (informational only)      │")
        lines.append("└" + "─" * 74 + "┘")
        lines.append("")

        # ── PORTFOLIO ──
        lines.append("┌─ PORTFOLIO ─" + "─" * 62 + "┐")
        lines.append(f"│  Open Positions:  {self.open_positions:>4}  │  "
                     f"Portfolio Heat: {self.portfolio_heat_pct:>5.2f}%  │  "
                     f"Long: {self.long_exposure_pct:>5.2f}% │")
        lines.append("└" + "─" * 74 + "┘")
        lines.append("")

        # ── INSIGHTS ──
        lines.append("┌─ INSIGHTS ─" + "─" * 62 + "┐")
        lines.append(f"│  Best Session:  {self.best_session:<18s}  │  Best Regime:  {self.best_regime:<18s}  │")
        lines.append(f"│  Worst Session: {self.worst_session:<18s}  │  Worst Regime: {self.worst_regime:<18s}  │")
        lines.append("└" + "─" * 74 + "┘")

        return "\n".join(lines)


class KPIEngine:
    """
    Generates profit-focused KPI dashboard.

    Per Priority 8: Win rate is secondary. Profit factor is primary.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH

    def generate_dashboard(
        self,
        open_positions: Optional[List[Dict]] = None,
        balance: float = 10_000.0,
    ) -> KPIDashboard:
        """
        Generate complete KPI dashboard.

        Args:
            open_positions: Current open positions
            balance: Account balance

        Returns:
            KPIDashboard with all metrics
        """
        dashboard = KPIDashboard(generated_at=time.time())

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            # ── Core metrics ──
            cur.execute("""
                SELECT COUNT(*),
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
                       SUM(pnl),
                       SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END),
                       SUM(CASE WHEN pnl <= 0 THEN ABS(pnl) ELSE 0 END),
                       AVG(pnl),
                       AVG(CASE WHEN pnl > 0 THEN realized_r ELSE NULL END),
                       AVG(CASE WHEN pnl <= 0 THEN realized_r ELSE NULL END)
                FROM positions WHERE status = 'closed'
            """)
            row = cur.fetchone()
            if row and row[0] > 0:
                n, wins, total_pnl, gp, gl, avg_pnl, avg_wr, avg_lr = row
                dashboard.total_trades = n
                dashboard.winning_trades = wins or 0
                dashboard.losing_trades = n - (wins or 0)
                dashboard.win_rate = (wins or 0) / n if n > 0 else 0
                dashboard.total_pnl = total_pnl or 0
                dashboard.gross_profit = gp or 0
                dashboard.gross_loss = gl or 0
                dashboard.avg_trade_pnl = avg_pnl or 0
                dashboard.avg_winner_r = avg_wr or 0
                dashboard.avg_loser_r = avg_lr or 0

                # Profit Factor
                dashboard.profit_factor = (gp or 0) / (gl or 1) if gl and gl > 0 else (
                    float('inf') if gp and gp > 0 else 0
                )

                # Expectancy in R
                dashboard.expectancy_r = (dashboard.win_rate * (avg_wr or 0)) - \
                    ((1 - dashboard.win_rate) * abs(avg_lr or 0))

                # Net Expectancy in USD
                dashboard.net_expectancy_usd = dashboard.expectancy_r * 100  # Assuming 1% risk

                # Sharpe Ratio
                cur.execute("SELECT realized_r FROM positions WHERE status = 'closed' AND realized_r IS NOT NULL")
                rs = [r[0] for r in cur.fetchall() if r[0] is not None]
                if len(rs) > 1:
                    mean_r = sum(rs) / len(rs)
                    std_r = math.sqrt(sum((r - mean_r) ** 2 for r in rs) / (len(rs) - 1))
                    dashboard.sharpe_ratio = (mean_r / std_r) * math.sqrt(252) if std_r > 0 else 0

                # Return on Risk
                if dashboard.avg_loser_r and abs(dashboard.avg_loser_r) > 0:
                    dashboard.return_on_risk = abs(dashboard.avg_winner_r / dashboard.avg_loser_r)

            # ── Drawdown ──
            cur.execute("SELECT pnl FROM positions WHERE status = 'closed' ORDER BY closed_at ASC")
            pnls = [r[0] for r in cur.fetchall()]
            if pnls:
                cum = 0.0
                peak = 0.0
                max_dd = 0.0
                for p in pnls:
                    cum += p
                    peak = max(peak, cum)
                    dd = peak - cum
                    max_dd = max(max_dd, dd)
                dashboard.max_drawdown_pct = max_dd / balance * 100 if balance > 0 else 0

            # ── Session performance ──
            cur.execute("""
                SELECT session, AVG(pnl) as avg_pnl, COUNT(*) as n
                FROM positions WHERE status = 'closed' AND session IS NOT NULL AND session != ''
                GROUP BY session HAVING n >= 3 ORDER BY avg_pnl DESC
            """)
            sessions = cur.fetchall()
            if sessions:
                dashboard.best_session = sessions[0][0]
                dashboard.worst_session = sessions[-1][0]

            # ── Regime performance ──
            cur.execute("""
                SELECT regime, AVG(pnl) as avg_pnl, COUNT(*) as n
                FROM positions WHERE status = 'closed' AND regime IS NOT NULL
                AND regime != '' AND regime != '0.0'
                GROUP BY regime HAVING n >= 3 ORDER BY avg_pnl DESC
            """)
            regimes = cur.fetchall()
            if regimes:
                dashboard.best_regime = regimes[0][0]
                dashboard.worst_regime = regimes[-1][0]

            conn.close()

        except Exception as e:
            logger.warning("KPI dashboard error: {}", e)

        # ── Open position metrics ──
        if open_positions:
            dashboard.open_positions = len(open_positions)
            total_exposure = sum(
                abs(p.get("entry_price", 0) * p.get("quantity", 0))
                for p in open_positions
            )
            dashboard.portfolio_heat_pct = total_exposure / balance * 100 if balance > 0 else 0
            dashboard.capital_utilization_pct = min(dashboard.portfolio_heat_pct, 100)

            longs = [p for p in open_positions if p.get("side") == "LONG"]
            shorts = [p for p in open_positions if p.get("side") == "SHORT"]
            long_val = sum(abs(p.get("entry_price", 0) * p.get("quantity", 0)) for p in longs)
            short_val = sum(abs(p.get("entry_price", 0) * p.get("quantity", 0)) for p in shorts)
            dashboard.long_exposure_pct = long_val / balance * 100 if balance > 0 else 0
            dashboard.short_exposure_pct = short_val / balance * 100 if balance > 0 else 0

        return dashboard
