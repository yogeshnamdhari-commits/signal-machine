"""
EMA_V5 Report Formatter — Formats reports as text, markdown, or HTML.
Isolated from existing formatters.
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from loguru import logger


class ReportFormatter:
    """Formats EMA_V5 reports for different output channels."""

    @staticmethod
    def to_text(report: Dict[str, Any]) -> str:
        """Format report as plain text."""
        lines = []
        report_type = report.get("report_type", "unknown")
        summary = report.get("summary", {})

        # Header
        lines.append("=" * 60)
        lines.append(f"EMA_V5 {report_type.upper()} REPORT")
        lines.append("=" * 60)

        if report.get("period"):
            p = report["period"]
            if isinstance(p, dict):
                lines.append(f"Period: {p.get('start', '')} to {p.get('end', '')}")
            else:
                lines.append(f"Period: {p}")

        if report.get("date"):
            lines.append(f"Date: {report['date']}")

        lines.append(f"Generated: {report.get('generated_at_str', '')}")
        lines.append("")

        # Summary
        lines.append("--- SUMMARY ---")
        lines.append(f"Total Signals:  {summary.get('total_signals', 0)}")
        lines.append(f"Total Trades:   {summary.get('total_trades', 0)}")
        lines.append(f"Wins/Losses:    {summary.get('wins', 0)}/{summary.get('losses', 0)}")
        lines.append(f"Win Rate:       {summary.get('win_rate', 0)}%")
        lines.append(f"Total PnL:      ${summary.get('total_pnl', 0):.2f}")
        lines.append(f"Avg PnL:        ${summary.get('avg_pnl', 0):.2f}")
        lines.append(f"Profit Factor:  {summary.get('profit_factor', 0)}")
        lines.append(f"Expectancy:     ${summary.get('expectancy', 0):.2f}")
        lines.append("")

        # Sides
        sides = report.get("sides", {})
        if sides:
            lines.append("--- SIDES ---")
            lines.append(f"LONG:  {sides.get('long_trades', 0)} trades, ${sides.get('long_pnl', 0):.2f}")
            lines.append(f"SHORT: {sides.get('short_trades', 0)} trades, ${sides.get('short_pnl', 0):.2f}")
            lines.append("")

        # Risk
        risk = report.get("risk", {})
        if risk:
            lines.append("--- RISK ---")
            lines.append(f"Max Drawdown:  {risk.get('max_drawdown_pct', 0)}%")
            lines.append(f"Sharpe Ratio:  {risk.get('sharpe_ratio', 0)}")
            lines.append("")

        # Regimes
        regimes = report.get("regimes", {})
        if regimes:
            lines.append("--- REGIMES ---")
            for regime, data in regimes.items():
                lines.append(f"  {regime}: {data.get('trades', 0)} trades, "
                           f"WR={data.get('win_rate', 0)}%, PnL=${data.get('pnl', 0):.2f}")
            lines.append("")

        # Symbols
        symbols = report.get("symbols", {})
        if symbols:
            lines.append("--- SYMBOLS ---")
            for sym, data in symbols.items():
                lines.append(f"  {sym}: {data.get('trades', 0)} trades, "
                           f"WR={data.get('win_rate', 0)}%, PnL=${data.get('pnl', 0):.2f}")
            lines.append("")

        # Best/Worst trade
        best = report.get("best_trade")
        worst = report.get("worst_trade")
        if best or worst:
            lines.append("--- NOTABLE ---")
            if best:
                lines.append(f"Best:  {best.get('symbol', '')} {best.get('side', '')} "
                           f"PnL=${best.get('pnl', 0):.2f}")
            if worst:
                lines.append(f"Worst: {worst.get('symbol', '')} {worst.get('side', '')} "
                           f"PnL=${worst.get('pnl', 0):.2f}")
            lines.append("")

        lines.append("=" * 60)
        return "\n".join(lines)

    @staticmethod
    def to_markdown(report: Dict[str, Any]) -> str:
        """Format report as markdown."""
        lines = []
        report_type = report.get("report_type", "unknown")
        summary = report.get("summary", {})

        # Header
        lines.append(f"# EMA_V5 {report_type.title()} Report\n")

        if report.get("period"):
            p = report["period"]
            if isinstance(p, dict):
                lines.append(f"**Period**: {p.get('start', '')} to {p.get('end', '')}\n")
        if report.get("date"):
            lines.append(f"**Date**: {report['date']}\n")

        lines.append(f"*Generated: {report.get('generated_at_str', '')}*\n")

        # Summary table
        lines.append("## Summary\n")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Total Signals | {summary.get('total_signals', 0)} |")
        lines.append(f"| Total Trades | {summary.get('total_trades', 0)} |")
        lines.append(f"| Wins/Losses | {summary.get('wins', 0)}/{summary.get('losses', 0)} |")
        lines.append(f"| Win Rate | {summary.get('win_rate', 0)}% |")
        lines.append(f"| Total PnL | ${summary.get('total_pnl', 0):.2f} |")
        lines.append(f"| Profit Factor | {summary.get('profit_factor', 0)} |")
        lines.append(f"| Expectancy | ${summary.get('expectancy', 0):.2f} |")
        lines.append("")

        # Sides
        sides = report.get("sides", {})
        if sides:
            lines.append("## Sides\n")
            lines.append("| Side | Trades | PnL |")
            lines.append("|------|--------|-----|")
            lines.append(f"| LONG | {sides.get('long_trades', 0)} | ${sides.get('long_pnl', 0):.2f} |")
            lines.append(f"| SHORT | {sides.get('short_trades', 0)} | ${sides.get('short_pnl', 0):.2f} |")
            lines.append("")

        # Regimes
        regimes = report.get("regimes", {})
        if regimes:
            lines.append("## Regimes\n")
            lines.append("| Regime | Trades | Win Rate | PnL |")
            lines.append("|--------|--------|----------|-----|")
            for regime, data in regimes.items():
                lines.append(f"| {regime} | {data.get('trades', 0)} | "
                           f"{data.get('win_rate', 0)}% | ${data.get('pnl', 0):.2f} |")
            lines.append("")

        # Symbols
        symbols = report.get("symbols", {})
        if symbols:
            lines.append("## Symbols\n")
            lines.append("| Symbol | Trades | Win Rate | PnL |")
            lines.append("|--------|--------|----------|-----|")
            for sym, data in symbols.items():
                lines.append(f"| {sym} | {data.get('trades', 0)} | "
                           f"{data.get('win_rate', 0)}% | ${data.get('pnl', 0):.2f} |")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def to_html(report: Dict[str, Any]) -> str:
        """Format report as HTML."""
        summary = report.get("summary", {})
        report_type = report.get("report_type", "unknown")

        html = f"""<!DOCTYPE html>
<html>
<head>
<title>EMA_V5 {report_type.title()} Report</title>
<style>
body {{ font-family: monospace; background: #0e1117; color: #c9d1d9; padding: 20px; }}
h1 {{ color: #58a6ff; }}
table {{ border-collapse: collapse; margin: 10px 0; }}
th, td {{ padding: 6px 12px; border: 1px solid #30363d; text-align: left; }}
th {{ background: #161b22; color: #8b949e; }}
.metric {{ color: #58a6ff; font-weight: bold; }}
.positive {{ color: #3fb950; }}
.negative {{ color: #f85149; }}
</style>
</head>
<body>
<h1>EMA_V5 {report_type.title()} Report</h1>
<p>Generated: {report.get('generated_at_str', '')}</p>

<h2>Summary</h2>
<table>
<tr><th>Metric</th><th>Value</th></tr>
<tr><td>Total Signals</td><td class="metric">{summary.get('total_signals', 0)}</td></tr>
<tr><td>Total Trades</td><td class="metric">{summary.get('total_trades', 0)}</td></tr>
<tr><td>Win Rate</td><td class="metric">{summary.get('win_rate', 0)}%</td></tr>
<tr><td>Total PnL</td><td class="{'positive' if summary.get('total_pnl', 0) >= 0 else 'negative'}">${summary.get('total_pnl', 0):.2f}</td></tr>
<tr><td>Profit Factor</td><td class="metric">{summary.get('profit_factor', 0)}</td></tr>
</table>
"""
        html += "</body></html>"
        return html

    @staticmethod
    def to_json(report: Dict[str, Any]) -> str:
        """Format report as JSON string."""
        import json
        return json.dumps(report, indent=2, default=str)

    @staticmethod
    def format_side_comparison(report: Dict[str, Any]) -> str:
        """Format side comparison as compact text."""
        sides = report.get("sides", {})
        long_pnl = sides.get("long_pnl", 0)
        short_pnl = sides.get("short_pnl", 0)
        long_t = sides.get("long_trades", 0)
        short_t = sides.get("short_trades", 0)
        return (f"LONG: {long_t} trades ${long_pnl:.2f} | "
                f"SHORT: {short_t} trades ${short_pnl:.2f}")
