"""
Calibration Report Generator — Produces EMA_V5_VALIDATION_REPORT.md

Generates a comprehensive markdown report from the calibration analytics.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from .analytics import CalibrationAnalytics


def generate_calibration_report(
    analytics: CalibrationAnalytics,
    output_path: Optional[Path] = None,
) -> str:
    """Generate the EMA_V5 calibration validation report."""
    if output_path is None:
        output_path = Path(__file__).resolve().parent.parent.parent.parent / "EMA_V5_VALIDATION_REPORT.md"

    summary = analytics.summary()
    distribution = analytics.score_distribution()
    threshold_sim = analytics.threshold_simulation()
    component = analytics.component_analysis()
    false_neg = analytics.false_negatives()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        f"# EMA_V5 Calibration Validation Report",
        f"",
        f"**Generated:** {now}",
        f"",
        f"---",
        f"",
        f"## Executive Summary",
        f"",
        f"| Metric | Value |",
        f"|---|---|",
        f"| Total candidates logged | {summary['total_candidates']} |",
        f"| Average confidence | {summary['avg_confidence']} |",
        f"| Maximum confidence | {summary['max_confidence']} |",
        f"| Passed gate (≥90) | {summary['passed_gate']} |",
        f"| Outcomes tracked | {summary['tracked_outcomes']} |",
        f"| Average return | {summary['avg_return']}% |",
        f"",
    ]

    # Score Distribution
    lines.extend([
        f"## Confidence Distribution",
        f"",
        f"| Bucket | Candidates | Tracked | Win Rate | Avg Return | Max Return | Min Return | Avg MFE | Avg MAE | Profit Factor |",
        f"|---|---|---|---|---|---|---|---|---|---|",
    ])
    for b in distribution:
        lines.append(
            f"| {b['bucket']} | {b['total']} | {b['tracked']} | {b['win_rate']}% "
            f"| {b['avg_return']}% | {b['max_return']}% | {b['min_return']}% "
            f"| {b['avg_mfe']}% | {b['avg_mae']}% | {b['profit_factor']} |"
        )
    lines.append("")

    # Threshold Simulation
    lines.extend([
        f"## Threshold Simulation",
        f"",
        f"| Threshold | Trades | Win Rate | Avg Return | Profit Factor | Expectancy | Avg MFE | Avg MAE | Trades/Day |",
        f"|---|---|---|---|---|---|---|---|---|",
    ])
    for t in threshold_sim:
        lines.append(
            f"| {t['threshold']} | {t['total_trades']} | {t['win_rate']}% "
            f"| {t['avg_return']}% | {t['profit_factor']} | {t['expectancy']}% "
            f"| {t['avg_mfe']}% | {t['avg_mae']}% | {t['trades_per_day']} |"
        )
    lines.append("")

    # Component Analysis
    lines.extend([
        f"## Component Contribution Analysis",
        f"",
        f"| Component | Avg Score (All) | Avg Score (Profitable) | Avg Score (Unprofitable) | Score Gap | Avg Return When Low | Candidates <50 |",
        f"|---|---|---|---|---|---|---|",
    ])
    for c in component:
        lines.append(
            f"| {c['component']} | {c['avg_score_all']} | {c['avg_score_profitable']} "
            f"| {c['avg_score_unprofitable']} | {c['score_gap']} "
            f"| {c['avg_return_when_low']}% | {c['candidates_below_50']} |"
        )
    lines.append("")

    # False Negatives
    lines.extend([
        f"## False Negative Analysis (Rejected but profitable ≥2R)",
        f"",
    ])
    if false_neg:
        lines.extend([
            f"| Symbol | Confidence | Direction | Entry | Return | RR | Volume | Trend | Rejection Reason |",
            f"|---|---|---|---|---|---|---|---|---|",
        ])
        for fn in false_neg[:20]:
            lines.append(
                f"| {fn['symbol']} | {fn['confidence']:.1f} | {fn['direction']} "
                f"| {fn['entry']:.4f} | {fn['return_pct']:.2f}% | {fn['rr_achieved']:.1f}R "
                f"| {fn['volume']:.1f} | {fn['trend']:.1f} | {fn['rejection_reason']} |"
            )
    else:
        lines.append("*No false negatives with tracked outcomes yet. Need more data.*")
    lines.append("")

    # Recommendations
    lines.extend([
        f"## Recommendations",
        f"",
        f"*Based on the data above, the following recommendations can be made:*",
        f"",
        f"1. **Threshold:** Examine the threshold simulation table to identify the optimal confidence threshold that maximizes profit factor.",
        f"2. **Weights:** Examine the component analysis to identify which scoring components are over-penalizing profitable candidates.",
        f"3. **False Negatives:** Review rejected candidates with high MFE to identify systematic blind spots in the scoring model.",
        f"",
        f"---",
        f"",
        f"*This report is auto-generated from calibration data. Update by running:*",
        f"```",
        f"python -m scanner.ema_v5.score_calibration.report",
        f"```",
        f"",
    ])

    report_text = "\n".join(lines)
    output_path.write_text(report_text)
    logger.info("📊 Calibration report generated: {}", output_path)
    return str(output_path)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
    from scanner.ema_v5.score_calibration.analytics import CalibrationAnalytics
    analytics = CalibrationAnalytics()
    path = generate_calibration_report(analytics)
    print(f"Report: {path}")
    analytics.close()
