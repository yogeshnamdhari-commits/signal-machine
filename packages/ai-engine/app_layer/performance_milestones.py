"""
Performance Milestones — Rolling windows instead of fixed trade counts.

Answers the 5 questions that matter:
    1. Are we making money? (Rolling PF)
    2. Are we improving? (Window comparison)
    3. Why? (Engine attribution)
    4. Can we trust it? (Regime coverage)
    5. Can it be promoted? (Gate status)

READ-ONLY: never modifies upstream data.
"""
from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

# Rolling window requirements
WINDOWS = [
    (100, "PF > 1.10", "Early validation"),
    (250, "PF > 1.20", "Initial decomposition"),
    (500, "PF > 1.25", "Full statistical validation"),
    (1000, "Stable across regimes", "Production eligibility"),
]


@dataclass
class RollingWindow:
    """Performance metrics for a rolling window."""
    window_size: int = 0
    trades: int = 0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    avg_winner_r: float = 0.0
    avg_loser_r: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    requirement: str = ""
    met: bool = False
    trend: str = "STABLE"  # IMPROVING / STABLE / DETERIORATING

    def to_dict(self) -> Dict:
        return {
            "window": self.window_size,
            "trades": self.trades,
            "pf": round(self.profit_factor, 2),
            "ev": round(self.expectancy_r, 3),
            "requirement": self.requirement,
            "met": self.met,
            "trend": self.trend,
        }


@dataclass
class MilestoneStatus:
    """Complete milestone status."""
    total_trades: int = 0
    windows: List[RollingWindow] = field(default_factory=list)

    # 5 Questions
    making_money: str = "NO"
    improving: str = "NO"
    why: str = ""
    trustworthy: str = "NO"
    promotable: str = "NO"

    def to_dict(self) -> Dict:
        return {
            "total_trades": self.total_trades,
            "windows": [w.to_dict() for w in self.windows],
            "questions": {
                "making_money": self.making_money,
                "improving": self.improving,
                "why": self.why,
                "trustworthy": self.trustworthy,
                "promotable": self.promotable,
            },
        }

    def render(self) -> str:
        lines = []
        lines.append("═" * 80)
        lines.append("  PERFORMANCE MILESTONES — Rolling Windows")
        lines.append("═" * 80)
        lines.append("")
        lines.append(f"  Total Completed Trades: {self.total_trades}")
        lines.append("")

        # Question 1: Are we making money?
        lines.append("┌─ Q1: ARE WE MAKING MONEY? ─" + "─" * 50 + "┐")
        if self.windows:
            w = self.windows[0]
            icon = "🟢" if w.profit_factor > 1.0 else "🔴"
            lines.append(f"│  {icon} Rolling PF: {w.profit_factor:>6.2f}  │  "
                         f"Requirement: {w.requirement:<20s}  │  "
                         f"{'✅ MET' if w.met else '❌ NOT MET':<10s}  │")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

        # Question 2: Are we improving?
        lines.append("┌─ Q2: ARE WE IMPROVING? ─" + "─" * 52 + "┐")
        if len(self.windows) >= 2:
            curr = self.windows[0]
            prev = self.windows[1]
            if prev.profit_factor > 0:
                change = (curr.profit_factor - prev.profit_factor) / prev.profit_factor * 100
                icon = "📈" if change > 0 else "📉" if change < 0 else "➡️"
                lines.append(f"│  {icon} Current: PF {curr.profit_factor:.2f}  │  "
                             f"Previous: PF {prev.profit_factor:.2f}  │  "
                             f"Change: {change:+.1f}%  │")
            else:
                lines.append(f"│  ➡️ Insufficient data for comparison  │")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

        # Question 3: Why?
        lines.append("┌─ Q3: WHY? (Engine Attribution) ─" + "─" * 44 + "┐")
        lines.append("│  (See Research Dashboard for engine-level attribution)    │")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

        # Question 4: Can we trust it?
        lines.append("┌─ Q4: CAN WE TRUST IT? ─" + "─" * 54 + "┐")
        regimes_met = sum(1 for w in self.windows if w.met)
        total_windows = len(self.windows)
        if total_windows > 0:
            trust_pct = regimes_met / total_windows * 100
            icon = "🟢" if trust_pct >= 75 else "🟡" if trust_pct >= 50 else "🔴"
            lines.append(f"│  {icon} Windows met: {regimes_met}/{total_windows}  │  "
                         f"Confidence: {trust_pct:.0f}%  │")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

        # Question 5: Can it be promoted?
        lines.append("┌─ Q5: CAN IT BE PROMOTED? ─" + "─" * 51 + "┐")
        promo_icon = "🟢" if self.promotable == "YES" else "🔴" if self.promotable == "NO" else "🟡"
        lines.append(f"│  {promo_icon} Promotion Status: {self.promotable:<50s}  │")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

        # Rolling Windows Detail
        lines.append("┌─ ROLLING WINDOWS ─" + "─" * 58 + "┐")
        lines.append(f"│  {'Window':>8s} │ {'Trades':>6s} │ {'PF':>6s} │ {'EV':>8s} │ "
                     f"{'Requirement':<20s} │ {'Status':>6s} │")
        lines.append("│  " + "─" * 72 + "  │")
        for w in self.windows:
            status = "✅" if w.met else "❌"
            lines.append(
                f"│  {w.window_size:>7d} │ {w.trades:>6d} │ {w.profit_factor:>5.2f} │ "
                f"{w.expectancy_r:>+7.3f}R │ {w.requirement:<20s} │ {status:>6s} │"
            )
        lines.append("└" + "─" * 78 + "┘")

        return "\n".join(lines)


class PerformanceMilestoneTracker:
    """
    Rolling window performance tracking.

    Uses rolling windows instead of fixed trade counts.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH

    def evaluate(self) -> MilestoneStatus:
        """Evaluate all rolling windows and 5 questions."""
        status = MilestoneStatus()

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            # Get total trades
            cur.execute("SELECT COUNT(*) FROM positions WHERE status = 'closed'")
            status.total_trades = cur.fetchone()[0]

            # Get all PnLs ordered by time
            cur.execute("SELECT pnl FROM positions WHERE status = 'closed' ORDER BY closed_at ASC")
            all_pnls = [r[0] for r in cur.fetchall()]

            conn.close()

            # Evaluate each rolling window
            for window_size, requirement, _ in WINDOWS:
                # Use all available data if less than window size
                actual_window = min(window_size, len(all_pnls))
                if actual_window > 0:
                    window_pnls = all_pnls[-actual_window:]
                    rw = self._calculate_window(window_size, window_pnls, requirement)
                    rw.met = self._check_requirement(rw, requirement)
                else:
                    rw = RollingWindow(
                        window_size=window_size,
                        trades=0,
                        requirement=requirement,
                        met=False,
                    )

                status.windows.append(rw)

            # Answer the 5 questions
            self._answer_questions(status)

        except Exception as e:
            logger.warning("Milestone evaluation error: {}", e)

        return status

    def _calculate_window(
        self, window_size: int, pnls: List[float], requirement: str
    ) -> RollingWindow:
        """Calculate metrics for a rolling window."""
        rw = RollingWindow(window_size=window_size, trades=len(pnls), requirement=requirement)

        n = len(pnls)
        rw.total_pnl = sum(pnls)
        rw.win_rate = sum(1 for p in pnls if p > 0) / n * 100 if n > 0 else 0

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        gp = sum(wins) if wins else 0
        gl = sum(abs(l) for l in losses) if losses else 0
        rw.profit_factor = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)

        avg_win = gp / len(wins) if wins else 0
        avg_loss = gl / len(losses) if losses else 0
        rw.avg_winner_r = avg_win
        rw.avg_loser_r = -avg_loss

        # Expectancy
        wr = rw.win_rate / 100
        rw.expectancy_r = (wr * avg_win) - ((1 - wr) * avg_loss)

        # Max Drawdown
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            dd = peak - cum
            max_dd = max(max_dd, dd)
        rw.max_drawdown_pct = max_dd / 10000 * 100 if 10000 > 0 else 0

        return rw

    def _check_requirement(self, rw: RollingWindow, requirement: str) -> bool:
        """Check if window meets its requirement."""
        if "PF > 1.10" in requirement:
            return rw.profit_factor > 1.10
        elif "PF > 1.20" in requirement:
            return rw.profit_factor > 1.20
        elif "PF > 1.25" in requirement:
            return rw.profit_factor > 1.25
        elif "Stable across regimes" in requirement:
            return rw.profit_factor > 1.0 and rw.expectancy_r > 0
        return False

    def _answer_questions(self, status: MilestoneStatus) -> None:
        """Answer the 5 key questions."""
        if not status.windows:
            return

        w = status.windows[0]

        # Q1: Making money?
        status.making_money = "YES" if w.profit_factor > 1.0 else "NO"

        # Q2: Improving?
        if len(status.windows) >= 2:
            curr = status.windows[0]
            prev = status.windows[1]
            if prev.profit_factor > 0:
                change = (curr.profit_factor - prev.profit_factor) / prev.profit_factor
                status.improving = "YES" if change > 0.05 else "NO"

        # Q3: Why? (placeholder — populated by engine attribution)
        status.why = "See Research Dashboard"

        # Q4: Trustworthy?
        regimes_met = sum(1 for win in status.windows if win.met)
        total = len(status.windows)
        status.trustworthy = "YES" if regimes_met >= total * 0.75 else "NO"

        # Q5: Promotable?
        all_met = all(w.met for w in status.windows[:2])  # First 2 windows must pass
        status.promotable = "YES" if all_met else "NO"
