"""
Validation Dashboard — Phase 13.

Terminal-based institutional dashboard displaying all validation metrics.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, Optional

from loguru import logger


class ValidationDashboard:
    """Terminal dashboard for EMA_V5 validation metrics."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or self.DB_PATH
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.row_factory = sqlite3.Row

    def render(self) -> str:
        """Render the full dashboard as a string."""
        lines = []
        lines.append("=" * 80)
        lines.append("  EMA_V5 INSTITUTIONAL VALIDATION DASHBOARD")
        lines.append("=" * 80)
        lines.append("")

        # ── Overview ──
        overview = self._overview()
        lines.append("┌─ OVERVIEW " + "─" * 68 + "┐")
        lines.append(f"│  Total Candidates:  {overview['total']:>8}   │  Avg Confidence:  {overview['avg_conf']:>6}   │")
        lines.append(f"│  Tracked Outcomes:  {overview['tracked']:>8}   │  Max Confidence:  {overview['max_conf']:>6}   │")
        lines.append(f"│  Passed Gate:       {overview['passed']:>8}   │  Avg Return:      {overview['avg_ret']:>6}% │")
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

        # ── Score Distribution ──
        dist = self._distribution()
        lines.append("┌─ CONFIDENCE DISTRIBUTION " + "─" * 53 + "┐")
        lines.append("│  Bucket   │ Count │ Tracked │ Win Rate │ Avg Ret  │ Max Ret  │ Min Ret  │")
        lines.append("│  ─────────┼───────┼─────────┼──────────┼──────────┼──────────┼──────────│")
        for d in dist:
            lines.append(
                f"│  {d['bucket']:>7} │ {d['total']:>5} │ {d['tracked']:>7} │ "
                f"{d['win_rate']:>6}%  │ {d['avg_return']:>+7.3f}% │ {d['max_return']:>+7.3f}% │ {d['min_return']:>+7.3f}% │"
            )
        lines.append("└" + "─" * 78 + "┘")
        lines.append("")

        # ── Component Analysis ──
        comps = self._component_analysis()
        if comps and comps[0].get("avg_score_all"):
            lines.append("┌─ COMPONENT ANALYSIS " + "─" * 58 + "┐")
            lines.append("│  Component │ Avg All │ Avg Win │ Avg Loss │ Gap    │ Corr Ret │ FP Rate  │")
            lines.append("│  ──────────┼─────────┼─────────┼──────────┼────────┼──────────┼──────────│")
            for c in comps:
                lines.append(
                    f"│  {c['component']:>9} │ {c['avg_score_all']:>6.1f}  │ {c['avg_score_profitable']:>6.1f}  │ "
                    f"{c['avg_score_unprofitable']:>7.1f}  │ {c['score_gap']:>+5.1f} │ "
                    f"{c['correlation_with_return']:>+7.4f} │ {c['false_positive_rate']:>6.1f}% │"
                )
            lines.append("└" + "─" * 78 + "┘")
            lines.append("")

        # ── Threshold Simulation ──
        thresh = self._threshold_simulation()
        if thresh:
            lines.append("┌─ THRESHOLD SIMULATION " + "─" * 56 + "┐")
            lines.append("│  Thresh │ Trades │ Win Rate │ Avg Ret  │ PF     │ Expect │ Sharpe │")
            lines.append("│  ───────┼────────┼──────────┼──────────┼────────┼────────┼────────│")
            for t in thresh:
                opt = " ★" if t.get("optimal") else ""
                lines.append(
                    f"│  {t['threshold']:>5.0f}  │ {t.get('total_trades', 0):>6} │ "
                    f"{t.get('win_rate', 0):>6.1f}%  │ {t.get('avg_return', 0):>+7.3f}% │ "
                    f"{t.get('profit_factor', 0):>5.2f} │ {t.get('expectancy', 0):>+5.3f}% │ "
                    f"{t.get('sharpe', 0):>+5.3f} │{opt}"
                )
            lines.append("└" + "─" * 78 + "┘")
            lines.append("")

        # ── False Analysis ──
        false_neg = self._false_negatives()
        if false_neg["count"] > 0:
            lines.append(f"┌─ FALSE NEGATIVES: {false_neg['count']} rejected trades with ≥2R potential " + "─" * (57 - len(str(false_neg['count']))) + "┐")
            if false_neg.get("top_rejecting"):
                lines.append(f"│  Top rejecting component: {false_neg['top_rejecting']['component']} ({false_neg['top_rejecting']['count']} trades)                    │")
            lines.append(f"│  Missed win rate: {false_neg.get('missed_win_rate', 0):.1f}%  │  Avg lost profit: {false_neg.get('avg_lost_profit', 0):.3f}%              │")
            lines.append("└" + "─" * 78 + "┘")
        else:
            lines.append("┌─ FALSE NEGATIVES " + "─" * 60 + "┐")
            lines.append("│  No tracked false negatives yet. Need more outcome data.                              │")
            lines.append("└" + "─" * 78 + "┘")
        lines.append("")

        lines.append("=" * 80)
        lines.append(f"  Dashboard generated from: {self._db_path}")
        lines.append("=" * 80)

        return "\n".join(lines)

    def _overview(self) -> Dict:
        cur = self._conn.cursor()
        cur.execute("""
            SELECT COUNT(*),
                   COUNT(CASE WHEN outcome_tracked = 1 THEN 1 END),
                   COUNT(CASE WHEN passed = 1 THEN 1 END),
                   ROUND(AVG(confidence), 1),
                   ROUND(MAX(confidence), 1),
                   ROUND(AVG(CASE WHEN outcome_tracked = 1 THEN return_pct END), 3)
            FROM candidates
        """)
        r = cur.fetchone()
        return {
            "total": r[0] or 0, "tracked": r[1] or 0, "passed": r[2] or 0,
            "avg_conf": r[3] or 0, "max_conf": r[4] or 0, "avg_ret": r[5] or 0,
        }

    def _distribution(self) -> list:
        buckets = [(70, 74), (75, 79), (80, 84), (85, 89), (90, 94), (95, 100)]
        cur = self._conn.cursor()
        results = []
        for lo, hi in buckets:
            cur.execute("""
                SELECT COUNT(*),
                       SUM(CASE WHEN outcome_tracked = 1 THEN 1 ELSE 0 END),
                       AVG(CASE WHEN outcome_tracked = 1 THEN return_pct END),
                       MAX(CASE WHEN outcome_tracked = 1 THEN return_pct END),
                       MIN(CASE WHEN outcome_tracked = 1 THEN return_pct END)
                FROM candidates WHERE confidence >= ? AND confidence <= ?
            """, (lo, hi))
            r = cur.fetchone()
            total = r[0] or 0
            tracked = r[1] or 0
            cur.execute("""
                SELECT COUNT(*) FROM candidates
                WHERE confidence >= ? AND confidence <= ? AND outcome_tracked = 1 AND return_pct > 0
            """, (lo, hi))
            wins = cur.fetchone()[0] or 0
            wr = wins / tracked * 100 if tracked > 0 else 0
            results.append({
                "bucket": f"{lo}-{hi}", "total": total, "tracked": tracked,
                "win_rate": round(wr, 1), "avg_return": round(r[2] or 0, 3),
                "max_return": round(r[3] or 0, 3), "min_return": round(r[4] or 0, 3),
            })
        return results

    def _component_analysis(self) -> list:
        from .component_importance import ComponentImportance
        ci = ComponentImportance(self._db_path)
        try:
            return ci.analyze()
        finally:
            ci.close()

    def _threshold_simulation(self) -> list:
        from .comprehensive_analytics import ComprehensiveAnalytics
        ca = ComprehensiveAnalytics(self._db_path)
        try:
            return ca.threshold_simulation()
        finally:
            ca.close()

    def _false_negatives(self) -> Dict:
        from .false_analysis import FalseAnalysis
        fa = FalseAnalysis(self._db_path)
        try:
            fn = fa.false_negatives(min_rr=2.0)
            return {
                "count": fn["count"],
                "missed_win_rate": fn["missed_win_rate"],
                "avg_lost_profit": fn["avg_lost_profit_pct"],
                "top_rejecting": fn["component_rejection_ranking"][0] if fn["component_rejection_ranking"] else None,
            }
        finally:
            fa.close()

    def close(self) -> None:
        self._conn.close()
