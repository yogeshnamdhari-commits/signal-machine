"""
Milestone Report Generator — Automated milestone-based validation reports.

Generates reports at predefined milestones:
- 50 trades: Production Health Review
- 200 trades: Initial Statistical Decomposition
- 500 trades: Full Statistical Validation
- 1000+ trades: Optimization Eligibility Assessment

Each report follows the institutional report format and includes
a formal recommendation on whether governance should be unlocked.

READ-ONLY — never modifies strategy or trading logic.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .production_analytics import ProductionAnalytics


class MilestoneReportGenerator:
    """Generates milestone-based validation reports."""

    MILESTONES = {
        50: "Production Health Review",
        200: "Initial Statistical Decomposition",
        500: "Full Statistical Validation",
        1000: "Optimization Eligibility Assessment",
    }

    def __init__(self, analytics: Optional[ProductionAnalytics] = None):
        self._analytics = analytics or ProductionAnalytics()

    def generate(self, milestone: int, strategy_filter: Optional[str] = None) -> str:
        """Generate a milestone report."""
        report = self._analytics.full_report(strategy_filter)
        core = report["core"]

        if core.get("status") == "no_data":
            return self._no_data_report()

        total = core["total_trades"]
        desc = self.MILESTONES.get(milestone, f"Milestone {milestone}")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        lines = []
        lines.append("=" * 76)
        lines.append(f"  EMA V5 INSTITUTIONAL — {desc.upper()}")
        lines.append(f"  Milestone: {milestone} Completed Trades")
        lines.append(f"  Generated: {now}")
        lines.append(f"  Classification: CONFIDENTIAL — Internal Research Only")
        lines.append("=" * 76)
        lines.append("")

        # ── Executive Summary ─────────────────────────────────────
        lines.extend(self._section_executive_summary(core, milestone))
        lines.append("")

        # ── Core Performance ──────────────────────────────────────
        lines.extend(self._section_core_performance(core))
        lines.append("")

        # ── Confidence Bucket Analysis ────────────────────────────
        lines.extend(self._section_confidence(report["confidence_buckets"]))
        lines.append("")

        # ── Session Analysis ──────────────────────────────────────
        lines.extend(self._section_sessions(report["sessions"]))
        lines.append("")

        # ── Regime Analysis ───────────────────────────────────────
        lines.extend(self._section_regimes(report["regimes"]))
        lines.append("")

        # ── Exit Analysis ─────────────────────────────────────────
        lines.extend(self._section_exits(report["exits"]))
        lines.append("")

        # ── Symbol Analysis ───────────────────────────────────────
        lines.extend(self._section_symbols(report["symbols"]))
        lines.append("")

        # ── Risk Assessment ───────────────────────────────────────
        lines.extend(self._section_risk(core))
        lines.append("")

        # ── Milestone-Specific Analysis ───────────────────────────
        if milestone >= 200:
            lines.extend(self._section_advanced_analysis(report))
            lines.append("")

        # ── Governance Recommendation ─────────────────────────────
        lines.extend(self._section_governance(core, milestone))
        lines.append("")

        # ── Status Footer ─────────────────────────────────────────
        lines.append("─" * 76)
        lines.append("  STATUS: COLLECTING | GOVERNANCE: LOCKED")
        lines.append("  Next Gate: " + self._next_gate_text(milestone))
        lines.append("─" * 76)

        return "\n".join(lines)

    def _no_data_report(self) -> str:
        return (
            "=" * 76 + "\n"
            "  EMA V5 INSTITUTIONAL — MILESTONE REPORT\n"
            "=" * 76 + "\n\n"
            "  No completed trades yet.\n"
            "  Waiting for first trade to close.\n\n"
            "  STATUS: COLLECTING | GOVERNANCE: LOCKED\n"
            "─" * 76 + "\n"
        )

    def _next_gate_text(self, current: int) -> str:
        for m in sorted(self.MILESTONES.keys()):
            if m > current:
                return f"{m} → {self.MILESTONES[m]}"
        return "Continue collecting data"

    def _section_executive_summary(self, core: Dict, milestone: int) -> List[str]:
        lines = []
        lines.append("## Executive Summary")
        lines.append("")
        lines.append(f"| Metric | Value | Target | Status |")
        lines.append(f"|---|---|---|---|")
        lines.append(f"| Completed Trades | {core['total_trades']} | {milestone} | "
                     f"{'✓ PASS' if core['total_trades'] >= milestone else '⏳ COLLECTING'} |")
        lines.append(f"| Win Rate | {core['win_rate']}% | >35% | "
                     f"{'✓' if core['win_rate'] > 35 else '✗'} |")
        lines.append(f"| Profit Factor | {core['profit_factor']} | >1.50 | "
                     f"{'✓' if core['profit_factor'] > 1.5 else '✗'} |")
        lines.append(f"| Expectancy | ${core['expectancy']}/trade | >$0 | "
                     f"{'✓' if core['expectancy'] > 0 else '✗'} |")
        lines.append(f"| Sharpe Ratio | {core['sharpe']} | >1.50 | "
                     f"{'✓' if core['sharpe'] > 1.5 else '✗'} |")
        lines.append(f"| Max Drawdown | ${core['max_drawdown']} | <$50 | "
                     f"{'✓' if core['max_drawdown'] < 50 else '✗'} |")
        lines.append(f"| Avg R | {core['avg_r']} | >+0.50 | "
                     f"{'✓' if core['avg_r'] > 0.5 else '✗'} |")
        lines.append("")
        return lines

    def _section_core_performance(self, core: Dict) -> List[str]:
        lines = []
        lines.append("## Core Performance Metrics")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append(f"| Total Trades | {core['total_trades']} |")
        lines.append(f"| Win Rate | {core['win_rate']}% |")
        lines.append(f"| Profit Factor | {core['profit_factor']} |")
        lines.append(f"| Expectancy | ${core['expectancy']}/trade |")
        lines.append(f"| Average Winner | ${core['avg_win']} |")
        lines.append(f"| Average Loser | ${core['avg_loss']} |")
        lines.append(f"| Payoff Ratio | {core['payoff_ratio']}x |")
        lines.append(f"| Average R | {core['avg_r']} |")
        lines.append(f"| Sharpe Ratio | {core['sharpe']} |")
        lines.append(f"| Sortino Ratio | {core['sortino']} |")
        lines.append(f"| Max Drawdown | ${core['max_drawdown']} |")
        lines.append(f"| Recovery Factor | {core['recovery_factor']} |")
        lines.append(f"| Total PnL | ${core['total_pnl']} |")
        lines.append(f"| Total Fees | ${core['total_fees']} |")
        lines.append(f"| Net PnL | ${core['net_pnl']} |")
        lines.append(f"| Avg Hold Time | {core['avg_hold_minutes']:.0f} min |")
        lines.append(f"| Avg MAE | {core['avg_mae_pct']}% |")
        lines.append(f"| Avg MFE | {core['avg_mfe_pct']}% |")
        lines.append(f"| Long Trades | {core['long_trades']} (WR {core['long_win_rate']}%) |")
        lines.append(f"| Short Trades | {core['short_trades']} (WR {core['short_win_rate']}%) |")
        lines.append("")
        return lines

    def _section_confidence(self, buckets: List[Dict]) -> List[str]:
        lines = []
        lines.append("## Confidence Bucket Analysis")
        lines.append("")
        lines.append("| Bucket | Trades | Win Rate | PF | Expectancy | Avg R | PnL |")
        lines.append("|---|---|---|---|---|---|---|")

        for b in buckets:
            if b["trades"] == 0:
                continue
            lines.append(
                f"| {b['bucket']} | {b['trades']} | {b['win_rate']}% | "
                f"{b['pf']} | ${b['expectancy']} | {b['avg_r']} | ${b['pnl']} |"
            )

        lines.append("")
        return lines

    def _section_sessions(self, sessions: List[Dict]) -> List[str]:
        lines = []
        lines.append("## Session Analysis")
        lines.append("")
        lines.append("| Session | Trades | Win Rate | PF | Expectancy | PnL |")
        lines.append("|---|---|---|---|---|---|")

        for s in sessions:
            lines.append(
                f"| {s['session']} | {s['trades']} | {s['win_rate']}% | "
                f"{s['pf']} | ${s['expectancy']} | ${s['pnl']} |"
            )

        lines.append("")
        return lines

    def _section_regimes(self, regimes: List[Dict]) -> List[str]:
        lines = []
        lines.append("## Market Regime Analysis")
        lines.append("")
        lines.append("| Regime | Trades | Win Rate | PF | Expectancy | PnL |")
        lines.append("|---|---|---|---|---|---|")

        for r in regimes:
            lines.append(
                f"| {r['regime']} | {r['trades']} | {r['win_rate']}% | "
                f"{r['pf']} | ${r['expectancy']} | ${r['pnl']} |"
            )

        lines.append("")
        return lines

    def _section_exits(self, exits: List[Dict]) -> List[str]:
        lines = []
        lines.append("## Exit Reason Analysis")
        lines.append("")
        lines.append("| Exit Reason | Trades | Win Rate | Total PnL | Avg PnL |")
        lines.append("|---|---|---|---|---|")

        for e in exits:
            lines.append(
                f"| {e['reason']} | {e['trades']} | {e['win_rate']}% | "
                f"${e['pnl']} | ${e['avg_pnl']} |"
            )

        lines.append("")
        return lines

    def _section_symbols(self, symbols: List[Dict]) -> List[str]:
        lines = []
        lines.append("## Symbol Performance (Top 10)")
        lines.append("")
        lines.append("| Symbol | Trades | Win Rate | PnL |")
        lines.append("|---|---|---|---|")

        for s in symbols[:10]:
            lines.append(
                f"| {s['symbol']} | {s['trades']} | {s['win_rate']}% | ${s['pnl']} |"
            )

        lines.append("")
        return lines

    def _section_risk(self, core: Dict) -> List[str]:
        lines = []
        lines.append("## Risk Assessment")
        lines.append("")

        dd_status = "✓ ACCEPTABLE" if core["max_drawdown"] < 50 else "⚠ ELEVATED"
        sharpe_status = "✓" if core["sharpe"] > 1.5 else "✗ BELOW TARGET"
        pf_status = "✓" if core["profit_factor"] > 1.5 else "✗ BELOW TARGET"

        lines.append(f"| Risk Metric | Value | Status |")
        lines.append(f"|---|---|---|")
        lines.append(f"| Max Drawdown | ${core['max_drawdown']} | {dd_status} |")
        lines.append(f"| Sharpe Ratio | {core['sharpe']} | {sharpe_status} |")
        lines.append(f"| Profit Factor | {core['profit_factor']} | {pf_status} |")
        lines.append(f"| Recovery Factor | {core['recovery_factor']} | "
                     f"{'✓' if core['recovery_factor'] > 1 else '✗'} |")
        lines.append("")
        return lines

    def _section_advanced_analysis(self, report: Dict) -> List[str]:
        lines = []
        lines.append("## Advanced Analysis")
        lines.append("")

        # Rolling metrics summary
        rolling = report.get("rolling", [])
        if rolling:
            last = rolling[-1]
            lines.append(f"### Rolling {last['window']}-Trade Window")
            lines.append(f"- Win Rate: {last['win_rate']}%")
            lines.append(f"- Profit Factor: {last['pf']}")
            lines.append(f"- Expectancy: ${last['expectancy']}")
            lines.append(f"- Sharpe: {last['sharpe']}")
            lines.append("")

        # Find best/worst confidence bucket
        buckets = [b for b in report["confidence_buckets"] if b["trades"] > 0]
        if buckets:
            best = max(buckets, key=lambda x: x["expectancy"])
            worst = min(buckets, key=lambda x: x["expectancy"])
            lines.append(f"### Best Confidence Bucket: {best['bucket']}")
            lines.append(f"- Trades: {best['trades']}, WR: {best['win_rate']}%, "
                         f"PF: {best['pf']}, Expectancy: ${best['expectancy']}")
            lines.append(f"### Worst Confidence Bucket: {worst['bucket']}")
            lines.append(f"- Trades: {worst['trades']}, WR: {worst['win_rate']}%, "
                         f"PF: {worst['pf']}, Expectancy: ${worst['expectancy']}")
            lines.append("")

        lines.append("")
        return lines

    def _section_governance(self, core: Dict, milestone: int) -> List[str]:
        lines = []
        lines.append("## Governance Recommendation")
        lines.append("")

        passed = 0
        total_criteria = 7
        criteria = []

        # Criterion 1: Sample size
        c1 = core["total_trades"] >= milestone
        criteria.append(("Sample size sufficient", c1, f"{core['total_trades']}/{milestone}"))
        passed += c1

        # Criterion 2: Win rate
        c2 = core["win_rate"] > 35
        criteria.append(("Win rate > 35%", c2, f"{core['win_rate']}%"))
        passed += c2

        # Criterion 3: Profit factor
        c3 = core["profit_factor"] > 1.5
        criteria.append(("Profit Factor > 1.5", c3, f"{core['profit_factor']}"))
        passed += c3

        # Criterion 4: Positive expectancy
        c4 = core["expectancy"] > 0
        criteria.append(("Positive expectancy", c4, f"${core['expectancy']}"))
        passed += c4

        # Criterion 5: Positive Sharpe
        c5 = core["sharpe"] > 1.5
        criteria.append(("Sharpe > 1.5", c5, f"{core['sharpe']}"))
        passed += c5

        # Criterion 6: Acceptable drawdown
        c6 = core["max_drawdown"] < 50
        criteria.append(("Drawdown < $50", c6, f"${core['max_drawdown']}"))
        passed += c6

        # Criterion 7: Positive Avg R
        c7 = core["avg_r"] > 0.5
        criteria.append(("Avg R > +0.50", c7, f"{core['avg_r']}"))
        passed += c7

        lines.append(f"**Criteria Passed: {passed}/{total_criteria}**")
        lines.append("")
        lines.append("| Criterion | Result | Value |")
        lines.append("|---|---|---|")
        for name, met, val in criteria:
            lines.append(f"| {name} | {'✓ PASS' if met else '✗ FAIL'} | {val} |")
        lines.append("")

        if passed == total_criteria:
            lines.append("**RECOMMENDATION: ALL CRITERIA PASSED**")
            lines.append("Governance MAY be unlocked for controlled optimization.")
            lines.append("Full statistical validation still recommended before changes.")
        else:
            lines.append("**RECOMMENDATION: REJECTED — Not all criteria met.**")
            lines.append("Continue evidence collection. Do NOT modify strategy.")

        lines.append("")
        lines.append(f"**Governance Status: {'UNLOCK ELIGIBLE' if passed == total_criteria else 'LOCKED'}**")
        lines.append("")
        return lines

    def save_report(self, milestone: int, output_dir: Optional[Path] = None,
                    strategy_filter: Optional[str] = None) -> Path:
        """Generate and save a milestone report."""
        report_text = self.generate(milestone, strategy_filter)

        if output_dir is None:
            output_dir = Path(__file__).resolve().parent.parent / "data" / "reports"

        output_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"milestone_{milestone}trades_{now}.md"
        filepath = output_dir / filename

        filepath.write_text(report_text)
        return filepath
