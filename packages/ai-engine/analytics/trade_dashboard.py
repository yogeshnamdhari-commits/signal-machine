"""
Trade Dashboard — Terminal-based research dashboard for Phase III evidence collection.

Renders a comprehensive institutional-grade dashboard showing:
- Core performance metrics
- Confidence bucket breakdown
- Session analysis
- Regime analysis
- Exit reason analysis
- Equity curve (ASCII)
- Rolling metrics
- Milestone progress

READ-ONLY — displays data, never modifies strategy or trading logic.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .production_analytics import ProductionAnalytics


class TradeDashboard:
    """Terminal-based institutional trade dashboard."""

    def __init__(self, analytics: Optional[ProductionAnalytics] = None):
        self._analytics = analytics or ProductionAnalytics()

    def render(self, strategy_filter: Optional[str] = None) -> str:
        """Render the full dashboard as a string."""
        report = self._analytics.full_report(strategy_filter)
        core = report["core"]

        if core.get("status") == "no_data":
            return self._render_no_data()

        lines = []
        lines.append("=" * 76)
        lines.append("  EMA V5 INSTITUTIONAL — PRODUCTION TRADE DASHBOARD")
        lines.append("  Phase III: Evidence Collection & Statistical Validation")
        lines.append("=" * 76)
        lines.append("")

        # ── Core Metrics ──────────────────────────────────────────
        lines.extend(self._render_core_metrics(core))
        lines.append("")

        # ── Confidence Buckets ────────────────────────────────────
        lines.extend(self._render_confidence_buckets(report["confidence_buckets"]))
        lines.append("")

        # ── Session Analysis ──────────────────────────────────────
        lines.extend(self._render_sessions(report["sessions"]))
        lines.append("")

        # ── Regime Analysis ───────────────────────────────────────
        lines.extend(self._render_regimes(report["regimes"]))
        lines.append("")

        # ── Exit Analysis ─────────────────────────────────────────
        lines.extend(self._render_exits(report["exits"]))
        lines.append("")

        # ── Top Symbols ───────────────────────────────────────────
        lines.extend(self._render_symbols(report["symbols"]))
        lines.append("")

        # ── Largest Winners/Losers ────────────────────────────────
        lines.extend(self._render_largest_trades(report["largest_trades"]))
        lines.append("")

        # ── Equity Curve (ASCII) ──────────────────────────────────
        lines.extend(self._render_equity_ascii(report["equity_curve"]))
        lines.append("")

        # ── Milestone Progress ────────────────────────────────────
        lines.extend(self._render_milestone_progress(core["total_trades"]))
        lines.append("")

        # ── Status Footer ─────────────────────────────────────────
        lines.append("─" * 76)
        lines.append("  STATUS: COLLECTING | GOVERNANCE: LOCKED")
        lines.append("  Next Gate: 50 Completed Trades → Production Health Review")
        lines.append("─" * 76)

        return "\n".join(lines)

    def _render_no_data(self) -> str:
        return (
            "=" * 76 + "\n"
            "  EMA V5 INSTITUTIONAL — PRODUCTION TRADE DASHBOARD\n"
            "  Phase III: Evidence Collection & Statistical Validation\n"
            "=" * 76 + "\n\n"
            "  No completed trades yet.\n"
            "  Waiting for first trade to close.\n\n"
            "  STATUS: COLLECTING | GOVERNANCE: LOCKED\n"
            "─" * 76 + "\n"
        )

    def _render_core_metrics(self, c: Dict) -> List[str]:
        lines = []
        lines.append("┌─ CORE METRICS " + "─" * 60 + "┐")

        lines.append(f"│  Total Trades:    {c['total_trades']:>6}    │  "
                     f"Win Rate:      {c['win_rate']:>6.1f}%     │")
        lines.append(f"│  Wins:            {c['win_count']:>6}    │  "
                     f"Profit Factor: {c['profit_factor']:>6.2f}      │")
        lines.append(f"│  Losses:          {c['loss_count']:>6}    │  "
                     f"Expectancy:    {c['expectancy']:>+7.4f}    │")
        lines.append("│" + "─" * 74 + "│")
        lines.append(f"│  Total PnL:     ${c['total_pnl']:>+9.2f}  │  "
                     f"Avg Winner:    ${c['avg_win']:>+7.4f}    │")
        lines.append(f"│  Gross Profit:  ${c['gross_profit']:>+9.2f}  │  "
                     f"Avg Loser:     ${c['avg_loss']:>+7.4f}    │")
        lines.append(f"│  Gross Loss:    ${c['gross_loss']:>+9.2f}  │  "
                     f"Payoff Ratio:  {c['payoff_ratio']:>7.2f}x     │")
        lines.append(f"│  Total Fees:    ${c['total_fees']:>+9.2f}  │  "
                     f"Avg R:         {c['avg_r']:>+7.2f}      │")
        lines.append("│" + "─" * 74 + "│")
        lines.append(f"│  Sharpe Ratio:   {c['sharpe']:>+7.2f}     │  "
                     f"Sortino:       {c['sortino']:>+7.2f}      │")
        lines.append(f"│  Max Drawdown:  ${c['max_drawdown']:>+9.2f}  │  "
                     f"Recovery:      {c['recovery_factor']:>+7.2f}      │")
        lines.append(f"│  Avg Hold:       {c['avg_hold_minutes']:>6.0f}m   │  "
                     f"Avg MAE:       {c['avg_mae_pct']:>+7.2f}%    │")
        lines.append("│" + "─" * 74 + "│")
        lines.append(f"│  Long Trades:    {c['long_trades']:>6}    │  "
                     f"Long WR:       {c['long_win_rate']:>6.1f}%     │")
        lines.append(f"│  Short Trades:   {c['short_trades']:>6}    │  "
                     f"Short WR:      {c['short_win_rate']:>6.1f}%     │")
        lines.append(f"│  Long PnL:     ${c['long_pnl']:>+9.2f}  │  "
                     f"Short PnL:    ${c['short_pnl']:>+9.2f}  │")
        lines.append("└" + "─" * 74 + "┘")

        return lines

    def _render_confidence_buckets(self, buckets: List[Dict]) -> List[str]:
        lines = []
        lines.append("┌─ CONFIDENCE BUCKET ANALYSIS " + "─" * 46 + "┐")
        lines.append("│  Bucket   │ Trades │  WR     │  PF    │  Expectancy  │  Avg R   │  PnL      │")
        lines.append("│  ─────────┼────────┼─────────┼────────┼──────────────┼──────────┼───────────│")

        for b in buckets:
            if b["trades"] == 0:
                continue
            marker = " ★" if b["pf"] > 1.5 and b["expectancy"] > 0 else "  "
            lines.append(
                f"│  {b['bucket']:>7} │ {b['trades']:>6} │ {b['win_rate']:>5.1f}%  │ "
                f"{b['pf']:>5.2f} │ {b['expectancy']:>+10.4f} │ {b['avg_r']:>+7.2f} │ "
                f"${b['pnl']:>+8.2f} │{marker}"
            )

        lines.append("└" + "─" * 74 + "┘")
        return lines

    def _render_sessions(self, sessions: List[Dict]) -> List[str]:
        lines = []
        lines.append("┌─ SESSION ANALYSIS " + "─" * 57 + "┐")
        lines.append("│  Session              │ Trades │  WR     │  PF    │  Expectancy  │  PnL      │")
        lines.append("│  ─────────────────────┼────────┼─────────┼────────┼──────────────┼───────────│")

        for s in sessions:
            marker = " ★" if s["win_rate"] > 40 and s["pnl"] > 0 else "  "
            lines.append(
                f"│  {s['session']:>20s} │ {s['trades']:>6} │ {s['win_rate']:>5.1f}%  │ "
                f"{s['pf']:>5.2f} │ {s['expectancy']:>+10.4f} │ ${s['pnl']:>+8.2f} │{marker}"
            )

        lines.append("└" + "─" * 74 + "┘")
        return lines

    def _render_regimes(self, regimes: List[Dict]) -> List[str]:
        lines = []
        lines.append("┌─ REGIME ANALYSIS " + "─" * 58 + "┐")
        lines.append("│  Regime               │ Trades │  WR     │  PF    │  Expectancy  │  PnL      │")
        lines.append("│  ─────────────────────┼────────┼─────────┼────────┼──────────────┼───────────│")

        for r in regimes:
            marker = " ★" if r["win_rate"] > 40 and r["pnl"] > 0 else "  "
            lines.append(
                f"│  {r['regime']:>20s} │ {r['trades']:>6} │ {r['win_rate']:>5.1f}%  │ "
                f"{r['pf']:>5.2f} │ {r['expectancy']:>+10.4f} │ ${r['pnl']:>+8.2f} │{marker}"
            )

        lines.append("└" + "─" * 74 + "┘")
        return lines

    def _render_exits(self, exits: List[Dict]) -> List[str]:
        lines = []
        lines.append("┌─ EXIT REASON ANALYSIS " + "─" * 53 + "┐")
        lines.append("│  Reason                     │ Trades │  WR     │  PF    │  Avg R   │  PnL        │")
        lines.append("│  ───────────────────────────┼────────┼─────────┼────────┼──────────┼─────────────│")

        for e in exits:
            lines.append(
                f"│  {e['reason']:>26s} │ {e['trades']:>6} │ {e['win_rate']:>5.1f}%  │ "
                f"{e['pf']:>5.2f} │ {e['avg_r']:>+7.2f} │ ${e['pnl']:>+9.2f}  │"
            )

        lines.append("└" + "─" * 74 + "┘")
        return lines

    def _render_symbols(self, symbols: List[Dict]) -> List[str]:
        lines = []
        lines.append("┌─ TOP SYMBOLS (by PnL) " + "─" * 53 + "┐")
        lines.append("│  Symbol                 │ Trades │  WR     │  PF    │  Avg R   │  PnL          │")
        lines.append("│  ───────────────────────┼────────┼─────────┼────────┼──────────┼───────────────│")

        for s in symbols[:10]:
            marker = " ★" if s["pf"] > 1.5 and s["expectancy"] > 0 else "  "
            lines.append(
                f"│  {s['symbol']:>23s} │ {s['trades']:>6} │ {s['win_rate']:>5.1f}%  │ "
                f"{s['pf']:>5.2f} │ {s['avg_r']:>+7.2f} │ ${s['pnl']:>+10.2f}   │{marker}"
            )

        lines.append("└" + "─" * 74 + "┘")
        return lines
    
    def _render_largest_trades(self, largest: Dict) -> List[str]:
        lines = []
        lines.append("┌─ LARGEST WINNERS & LOSERS " + "─" * 48 + "┐")
        
        # Winners
        lines.append("│  TOP WINNERS:" + " " * 60 + "│")
        lines.append("│  Symbol                Side    PnL       R      Conf   Exit" + " " * 15 + "│")
        lines.append("│  ───────────────────── ─────── ───────── ────── ────── ─────────" + " " * 7 + "│")
        
        for w in largest.get("winners", []):
            hold = f"{w['hold_minutes']:.0f}m" if w.get("hold_minutes") else "N/A"
            lines.append(
                f"│  {w['symbol']:>21s} {w['side']:>6s} ${w['pnl']:>+7.4f} {w['realized_r']:>+5.2f}R "
                f"{w['confidence']:>5.1f}% {w['exit_reason'][:20]:<20s}"
            )
        
        # Losers
        lines.append("│" + " " * 74 + "│")
        lines.append("│  TOP LOSERS:" + " " * 60 + "│")
        lines.append("│  Symbol                Side    PnL       R      Conf   Exit" + " " * 15 + "│")
        lines.append("│  ───────────────────── ─────── ───────── ────── ────── ─────────" + " " * 7 + "│")
        
        for l in largest.get("losers", []):
            hold = f"{l['hold_minutes']:.0f}m" if l.get("hold_minutes") else "N/A"
            lines.append(
                f"│  {l['symbol']:>21s} {l['side']:>6s} ${l['pnl']:>+7.4f} {l['realized_r']:>+5.2f}R "
                f"{l['confidence']:>5.1f}% {l['exit_reason'][:20]:<20s}"
            )
        
        lines.append("└" + "─" * 74 + "┘")
        return lines

    def _render_equity_ascii(self, curve: List[Dict]) -> List[str]:
        """Render a simple ASCII equity curve."""
        if not curve:
            return []

        lines = []
        lines.append("┌─ EQUITY CURVE " + "─" * 61 + "┐")

        # Get cumulative PnL values
        cum_pnls = [c["cum_pnl"] for c in curve]
        if not cum_pnls:
            lines.append("│  No data yet." + " " * 60 + "│")
            lines.append("└" + "─" * 74 + "┘")
            return lines

        min_val = min(cum_pnls)
        max_val = max(cum_pnls)
        spread = max_val - min_val if max_val != min_val else 1

        height = 12
        width = min(len(cum_pnls), 60)

        # Sample down if too many points
        if len(cum_pnls) > width:
            step = len(cum_pnls) / width
            sampled = [cum_pnls[int(i * step)] for i in range(width)]
        else:
            sampled = cum_pnls

        # Build the grid
        grid = [[" " for _ in range(len(sampled))] for _ in range(height)]

        for col, val in enumerate(sampled):
            row = int((val - min_val) / spread * (height - 1))
            row = max(0, min(height - 1, row))
            grid[row][col] = "█"

        # Render
        for row in range(height - 1, -1, -1):
            val = min_val + (row / (height - 1)) * spread
            label = f"${val:>+7.1f}"
            line = "".join(grid[row])
            lines.append(f"│  {label} │{line}│")

        lines.append("│" + " " * 11 + "└" + "─" * len(sampled) + "│")
        lines.append("│" + " " * 11 + " " + "Trade #".center(len(sampled)) + " " + "│")
        lines.append("└" + "─" * 74 + "┘")

        return lines

    def _render_milestone_progress(self, total_trades: int) -> List[str]:
        lines = []
        lines.append("┌─ MILESTONE PROGRESS " + "─" * 55 + "┐")

        milestones = [
            (50, "Production Health Review"),
            (200, "Initial Statistical Decomposition"),
            (500, "Full Statistical Validation"),
            (1000, "Optimization Eligibility"),
        ]

        for target, desc in milestones:
            progress = min(total_trades / target * 100, 100) if target > 0 else 0
            filled = int(progress / 100 * 30)
            bar = "█" * filled + "░" * (30 - filled)
            status = "✓" if total_trades >= target else " "
            lines.append(
                f"│  {status} {target:>5} │ {bar} │ {progress:>5.1f}% │ {desc}"
            )

        lines.append("│" + " " * 75 + "│")
        lines.append(f"│  Current: {total_trades} completed trades"
                     + " " * (75 - 11 - len(str(total_trades)) - 20) + "│")
        lines.append("└" + "─" * 74 + "┘")

        return lines
