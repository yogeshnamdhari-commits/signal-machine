"""
Strategy Comparison Dashboard — Baseline vs Full App comparison.

Continuously compares:
    Baseline (EMA V5 + Smart Money) vs Full App

For every completed validation window.

This quantifies how much value the App itself is adding
over the locked baseline.

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


@dataclass
class StrategyMetrics:
    """Metrics for a single strategy configuration."""
    name: str = ""
    trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    avg_winner_r: float = 0.0
    avg_loser_r: float = 0.0
    total_pnl: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    recovery_factor: float = 0.0
    avg_hold_minutes: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "trades": self.trades,
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 2),
            "expectancy_r": round(self.expectancy_r, 3),
            "avg_winner_r": round(self.avg_winner_r, 3),
            "avg_loser_r": round(self.avg_loser_r, 3),
            "total_pnl": round(self.total_pnl, 2),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 2),
            "recovery_factor": round(self.recovery_factor, 2),
            "avg_hold_minutes": round(self.avg_hold_minutes, 0),
        }


@dataclass
class MetricComparison:
    """Comparison of a single metric between baseline and app."""
    metric_name: str = ""
    baseline_value: float = 0.0
    app_value: float = 0.0
    difference: float = 0.0
    improvement_pct: float = 0.0
    app_better: bool = False
    unit: str = ""

    def to_dict(self) -> Dict:
        return {
            "metric": self.metric_name,
            "baseline": round(self.baseline_value, 4),
            "app": round(self.app_value, 4),
            "difference": round(self.difference, 4),
            "improvement_pct": round(self.improvement_pct, 2),
            "app_better": self.app_better,
        }


@dataclass
class ComparisonResult:
    """Complete strategy comparison result."""
    timestamp: float = 0.0
    validation_window: str = ""
    baseline: Optional[StrategyMetrics] = None
    app: Optional[StrategyMetrics] = None
    metrics: List[MetricComparison] = field(default_factory=list)
    overall_verdict: str = ""  # APP_SUPERIOR / BASELINE_SUPERIOR / EQUIVALENT
    confidence: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "window": self.validation_window,
            "baseline": self.baseline.to_dict() if self.baseline else None,
            "app": self.app.to_dict() if self.app else None,
            "metrics": [m.to_dict() for m in self.metrics],
            "verdict": self.overall_verdict,
            "confidence": round(self.confidence, 2),
        }

    def render(self) -> str:
        """Render comparison dashboard."""
        lines = []
        lines.append("═" * 76)
        lines.append("  STRATEGY COMPARISON: Baseline vs Full App")
        lines.append("═" * 76)
        lines.append("")

        # Verdict
        verdict_colors = {
            "APP_SUPERIOR": "🟢",
            "BASELINE_SUPERIOR": "🔴",
            "EQUIVALENT": "🟡",
        }
        icon = verdict_colors.get(self.overall_verdict, "❓")
        lines.append(f"  {icon} Verdict: {self.overall_verdict}  (confidence: {self.confidence:.0f}%)")
        lines.append("")

        # Comparison table
        lines.append("┌─ METRIC COMPARISON ─" + "─" * 54 + "┐")
        lines.append(f"│  {'Metric':<22s} {'Baseline':>10s} {'App':>10s} {'Diff':>10s} {'Better':>8s}  │")
        lines.append("│  " + "─" * 70 + "  │")

        for m in self.metrics:
            better = "✓ APP" if m.app_better else "✗ BASE" if m.difference != 0 else "─ TIE"
            lines.append(
                f"│  {m.metric_name:<22s} "
                f"{m.baseline_value:>+9.3f}{m.unit:<2s} "
                f"{m.app_value:>+9.3f}{m.unit:<2s} "
                f"{m.difference:>+9.3f}{m.unit:<2s} "
                f"{better:>7s}  │"
            )

        lines.append("└" + "─" * 74 + "┘")

        # Summary
        app_wins = sum(1 for m in self.metrics if m.app_better)
        total = len(self.metrics)
        lines.append("")
        lines.append(f"  App outperforms on {app_wins}/{total} metrics")

        return "\n".join(lines)


class StrategyComparisonEngine:
    """
    Continuously compares Baseline vs Full App performance.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._history: List[ComparisonResult] = []

    def compare(self, window_trades: int = 100) -> ComparisonResult:
        """
        Compare baseline vs app over the last N trades.

        Args:
            window_trades: Number of trades in comparison window

        Returns:
            ComparisonResult with full comparison
        """
        result = ComparisonResult(timestamp=time.time())

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Get baseline metrics (all trades — represents EMA V5 + Smart Money)
            result.baseline = self._get_strategy_metrics(cur, "Baseline (EMA V5 + Smart Money)")

            # Get app metrics (trades with institutional_score >= 80 — represents App filtering)
            result.app = self._get_app_filtered_metrics(cur)

            conn.close()

        except Exception as e:
            logger.warning("Strategy comparison error: {}", e)
            return result

        if not result.baseline or not result.app:
            return result

        # Compare metrics
        result.metrics = self._compare_metrics(result.baseline, result.app)

        # Determine verdict
        app_wins = sum(1 for m in result.metrics if m.app_better)
        total = len(result.metrics)

        if app_wins > total * 0.6:
            result.overall_verdict = "APP_SUPERIOR"
        elif app_wins < total * 0.4:
            result.overall_verdict = "BASELINE_SUPERIOR"
        else:
            result.overall_verdict = "EQUIVALENT"

        result.confidence = min(result.baseline.trades / 100, 1.0) * 100

        self._history.append(result)

        logger.info(
            "COMPARISON: {} (trades={}, confidence={:.0f}%)",
            result.overall_verdict, result.baseline.trades, result.confidence,
        )

        return result

    def _get_strategy_metrics(self, cur, name: str) -> StrategyMetrics:
        """Get metrics for all trades (baseline)."""
        m = StrategyMetrics(name=name)

        cur.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
                   SUM(pnl),
                   SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END),
                   SUM(CASE WHEN pnl <= 0 THEN ABS(pnl) ELSE 0 END),
                   AVG(CASE WHEN pnl > 0 THEN realized_r ELSE NULL END),
                   AVG(CASE WHEN pnl <= 0 THEN realized_r ELSE NULL END),
                   AVG(hold_minutes)
            FROM positions WHERE status = 'closed'
        """)
        row = cur.fetchone()
        if row and row[0] > 0:
            n, wins, total_pnl, gp, gl, avg_wr, avg_lr, avg_hold = row
            m.trades = n
            m.win_rate = (wins or 0) / n * 100
            m.total_pnl = total_pnl or 0
            m.avg_winner_r = avg_wr or 0
            m.avg_loser_r = avg_lr or 0
            m.avg_hold_minutes = avg_hold or 0
            m.profit_factor = (gp or 0) / (gl or 1) if gl and gl > 0 else 0
            m.expectancy_r = (m.win_rate / 100 * m.avg_winner_r) - \
                ((1 - m.win_rate / 100) * abs(m.avg_loser_r))

            # Sharpe
            cur.execute("SELECT realized_r FROM positions WHERE status = 'closed' AND realized_r IS NOT NULL")
            rs = [r[0] for r in cur.fetchall() if r[0] is not None]
            if len(rs) > 1:
                mean_r = sum(rs) / len(rs)
                std_r = math.sqrt(sum((r - mean_r) ** 2 for r in rs) / (len(rs) - 1))
                m.sharpe_ratio = (mean_r / std_r) * math.sqrt(252) if std_r > 0 else 0

            # Max Drawdown
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
                m.max_drawdown_pct = max_dd / 10000 * 100

            m.recovery_factor = m.total_pnl / max_dd if max_dd > 0 else 0

        return m

    def _get_app_filtered_metrics(self, cur) -> StrategyMetrics:
        """Get metrics for high-confidence trades (represents App filtering)."""
        m = StrategyMetrics(name="Full App (Filtered)")

        cur.execute("""
            SELECT COUNT(*),
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
                   SUM(pnl),
                   SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END),
                   SUM(CASE WHEN pnl <= 0 THEN ABS(pnl) ELSE 0 END),
                   AVG(CASE WHEN pnl > 0 THEN realized_r ELSE NULL END),
                   AVG(CASE WHEN pnl <= 0 THEN realized_r ELSE NULL END),
                   AVG(hold_minutes)
            FROM positions WHERE status = 'closed'
            AND confidence >= 0.90
            AND risk_reward >= 2.0
        """)
        row = cur.fetchone()
        if row and row[0] > 0:
            n, wins, total_pnl, gp, gl, avg_wr, avg_lr, avg_hold = row
            m.trades = n
            m.win_rate = (wins or 0) / n * 100
            m.total_pnl = total_pnl or 0
            m.avg_winner_r = avg_wr or 0
            m.avg_loser_r = avg_lr or 0
            m.avg_hold_minutes = avg_hold or 0
            m.profit_factor = (gp or 0) / (gl or 1) if gl and gl > 0 else 0
            m.expectancy_r = (m.win_rate / 100 * m.avg_winner_r) - \
                ((1 - m.win_rate / 100) * abs(m.avg_loser_r))

            cur.execute("""
                SELECT realized_r FROM positions WHERE status = 'closed'
                AND confidence >= 0.90 AND risk_reward >= 2.0
                AND realized_r IS NOT NULL
            """)
            rs = [r[0] for r in cur.fetchall() if r[0] is not None]
            if len(rs) > 1:
                mean_r = sum(rs) / len(rs)
                std_r = math.sqrt(sum((r - mean_r) ** 2 for r in rs) / (len(rs) - 1))
                m.sharpe_ratio = (mean_r / std_r) * math.sqrt(252) if std_r > 0 else 0

            cur.execute("""
                SELECT pnl FROM positions WHERE status = 'closed'
                AND confidence >= 0.90 AND risk_reward >= 2.0
                ORDER BY closed_at ASC
            """)
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
                m.max_drawdown_pct = max_dd / 10000 * 100

            m.recovery_factor = m.total_pnl / max_dd if max_dd > 0 else 0

        return m

    def _compare_metrics(
        self, baseline: StrategyMetrics, app: StrategyMetrics
    ) -> List[MetricComparison]:
        """Compare all metrics between baseline and app."""
        comparisons = []

        # Profit Factor (higher is better)
        pf_diff = app.profit_factor - baseline.profit_factor
        comparisons.append(MetricComparison(
            metric_name="Profit Factor",
            baseline_value=baseline.profit_factor,
            app_value=app.profit_factor,
            difference=pf_diff,
            improvement_pct=pf_diff / max(baseline.profit_factor, 0.01) * 100,
            app_better=pf_diff > 0,
        ))

        # Expectancy (higher is better)
        ev_diff = app.expectancy_r - baseline.expectancy_r
        comparisons.append(MetricComparison(
            metric_name="Expectancy",
            baseline_value=baseline.expectancy_r,
            app_value=app.expectancy_r,
            difference=ev_diff,
            improvement_pct=ev_diff / max(abs(baseline.expectancy_r), 0.01) * 100,
            app_better=ev_diff > 0,
            unit="R",
        ))

        # Total PnL (higher is better)
        pnl_diff = app.total_pnl - baseline.total_pnl
        comparisons.append(MetricComparison(
            metric_name="Net Profit",
            baseline_value=baseline.total_pnl,
            app_value=app.total_pnl,
            difference=pnl_diff,
            improvement_pct=pnl_diff / max(abs(baseline.total_pnl), 1) * 100,
            app_better=app.total_pnl > baseline.total_pnl,
            unit="$",
        ))

        # Max Drawdown (lower is better)
        dd_diff = app.max_drawdown_pct - baseline.max_drawdown_pct
        comparisons.append(MetricComparison(
            metric_name="Max Drawdown",
            baseline_value=baseline.max_drawdown_pct,
            app_value=app.max_drawdown_pct,
            difference=dd_diff,
            improvement_pct=-dd_diff / max(baseline.max_drawdown_pct, 0.01) * 100,
            app_better=dd_diff < 0,
            unit="%",
        ))

        # Avg Winner (higher is better)
        aw_diff = app.avg_winner_r - baseline.avg_winner_r
        comparisons.append(MetricComparison(
            metric_name="Avg Winner",
            baseline_value=baseline.avg_winner_r,
            app_value=app.avg_winner_r,
            difference=aw_diff,
            improvement_pct=aw_diff / max(abs(baseline.avg_winner_r), 0.01) * 100,
            app_better=aw_diff > 0,
            unit="R",
        ))

        # Avg Loser (less negative is better)
        al_diff = abs(app.avg_loser_r) - abs(baseline.avg_loser_r)
        comparisons.append(MetricComparison(
            metric_name="Avg Loser",
            baseline_value=baseline.avg_loser_r,
            app_value=app.avg_loser_r,
            difference=al_diff,
            improvement_pct=-al_diff / max(abs(baseline.avg_loser_r), 0.01) * 100,
            app_better=al_diff < 0,
            unit="R",
        ))

        # Sharpe Ratio (higher is better)
        sharpe_diff = app.sharpe_ratio - baseline.sharpe_ratio
        comparisons.append(MetricComparison(
            metric_name="Sharpe Ratio",
            baseline_value=baseline.sharpe_ratio,
            app_value=app.sharpe_ratio,
            difference=sharpe_diff,
            improvement_pct=sharpe_diff / max(abs(baseline.sharpe_ratio), 0.01) * 100,
            app_better=sharpe_diff > 0,
        ))

        # Win Rate (informational)
        wr_diff = app.win_rate - baseline.win_rate
        comparisons.append(MetricComparison(
            metric_name="Win Rate",
            baseline_value=baseline.win_rate,
            app_value=app.win_rate,
            difference=wr_diff,
            improvement_pct=wr_diff / max(baseline.win_rate, 0.01) * 100,
            app_better=wr_diff > 0,
            unit="%",
        ))

        return comparisons

    def get_history(self) -> List[ComparisonResult]:
        """Get comparison history."""
        return list(self._history)
