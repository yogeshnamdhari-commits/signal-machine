#!/usr/bin/env python3
"""
Production Analytics CLI — Run from terminal.

Usage:
    python -m analytics              # Full dashboard
    python -m analytics --report 50  # 50-trade milestone report
    python -m analytics --report 200 # 200-trade milestone report
    python -m analytics --json       # Full report as JSON
    python -m analytics --save       # Save current milestone report to file
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analytics.production_analytics import ProductionAnalytics
from analytics.trade_dashboard import TradeDashboard
from analytics.milestone_reports import MilestoneReportGenerator


def main():
    parser = argparse.ArgumentParser(description="EMA V5 Production Analytics")
    parser.add_argument("--report", type=int, help="Generate milestone report (50, 200, 500, 1000)")
    parser.add_argument("--json", action="store_true", help="Output full report as JSON")
    parser.add_argument("--save", action="store_true", help="Save milestone report to file")
    parser.add_argument("--strategy", type=str, default=None, help="Filter by strategy version")
    args = parser.parse_args()

    analytics = ProductionAnalytics()

    if args.json:
        report = analytics.full_report(args.strategy)
        print(json.dumps(report, indent=2, default=str))

    elif args.report:
        gen = MilestoneReportGenerator(analytics)
        if args.save:
            filepath = gen.save_report(args.report, strategy_filter=args.strategy)
            print(f"Report saved to: {filepath}")
            print()
        print(gen.generate(args.report, args.strategy))

    else:
        dashboard = TradeDashboard(analytics)
        print(dashboard.render(args.strategy))


if __name__ == "__main__":
    main()
