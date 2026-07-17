"""
Forward Validation Framework — Continuous monitoring of live trading performance.

Tracks rolling metrics, detects performance degradation, and generates
alerts when metrics deviate from expected ranges.
"""
from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


class ForwardValidator:
    """Continuous forward validation of live trading performance."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "institutional_v1.db"

    # Performance thresholds for alerting
    ALERT_THRESHOLDS = {
        "min_win_rate": 35,          # Alert if win rate drops below 35%
        "min_profit_factor": 0.8,    # Alert if PF drops below 0.8
        "max_drawdown_pct": 15,      # Alert if drawdown exceeds 15%
        "min_sample_size": 20,       # Need at least 20 trades for valid metrics
        "max_consec_losses": 5,      # Alert after 5 consecutive losses
    }

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or self.DB_PATH
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.row_factory = sqlite3.Row

    def validate(self) -> Dict:
        """Run full forward validation."""
        cur = self._conn.cursor()

        # Get all closed trades ordered by time
        cur.execute("""
            SELECT symbol, side, entry_price, pnl, confidence * 100 as conf_pct,
                   exit_reason, hold_minutes, realized_r, strategy_version,
                   opened_at, closed_at
            FROM positions WHERE status = 'closed'
            ORDER BY closed_at ASC
        """)
        rows = cur.fetchall()

        if not rows:
            return {"status": "no_data", "message": "No trades for forward validation"}

        # Get open trades
        cur.execute("""
            SELECT symbol, side, entry_price, pnl, confidence * 100 as conf_pct,
                   strategy_version, opened_at
            FROM positions WHERE status = 'open'
        """)
        open_rows = cur.fetchall()

        # Rolling window analysis
        rolling = self._rolling_analysis(rows)

        # Performance degradation detection
        degradation = self._detect_degradation(rows)

        # Statistical significance test
        significance = self._statistical_significance(rows)

        # Regime analysis
        regime = self._regime_analysis(rows)

        # Current health status
        health = self._current_health(rows, open_rows)

        return {
            "status": "complete",
            "total_trades": len(rows),
            "open_trades": len(open_rows),
            "rolling_analysis": rolling,
            "degradation": degradation,
            "statistical_significance": significance,
            "regime_analysis": regime,
            "health": health,
        }

    def _rolling_analysis(self, rows: list) -> Dict:
        """Compute rolling metrics over different windows."""
        windows = [10, 20, 50, len(rows)]
        results = {}

        for window in windows:
            if window > len(rows):
                continue

            recent = rows[-window:]
            pnls = [r["pnl"] or 0 for r in recent]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]

            total = len(pnls)
            wr = len(wins) / total * 100 if total else 0
            gp = sum(wins)
            gl = sum(abs(l) for l in losses)
            pf = gp / gl if gl > 0 else 0
            avg_pnl = sum(pnls) / total if total else 0

            # Cumulative PnL
            cum_pnl = sum(pnls)

            # Max drawdown in window
            cumulative = 0
            peak = 0
            max_dd = 0
            for p in pnls:
                cumulative += p
                peak = max(peak, cumulative)
                dd = peak - cumulative
                max_dd = max(max_dd, dd)

            results[f"last_{window}"] = {
                "trades": total,
                "win_rate": round(wr, 1),
                "profit_factor": round(pf, 2),
                "avg_pnl": round(avg_pnl, 3),
                "cumulative_pnl": round(cum_pnl, 3),
                "max_drawdown": round(max_dd, 3),
            }

        return results

    def _detect_degradation(self, rows: list) -> Dict:
        """Detect if performance is degrading over time."""
        if len(rows) < 20:
            return {"status": "insufficient_data"}

        # Split into first half and second half
        mid = len(rows) // 2
        first_half = rows[:mid]
        second_half = rows[mid:]

        def compute_metrics(trade_list):
            pnls = [r["pnl"] or 0 for r in trade_list]
            wins = [p for p in pnls if p > 0]
            gp = sum(wins)
            gl = sum(abs(l) for l in pnls if l <= 0)
            return {
                "trades": len(pnls),
                "win_rate": len(wins) / len(pnls) * 100 if pnls else 0,
                "profit_factor": gp / gl if gl > 0 else 0,
                "avg_pnl": sum(pnls) / len(pnls) if pnls else 0,
            }

        first_metrics = compute_metrics(first_half)
        second_metrics = compute_metrics(second_half)

        # Detect degradation
        degradations = []
        if second_metrics["win_rate"] < first_metrics["win_rate"] - 5:
            degradations.append({
                "metric": "win_rate",
                "first_half": round(first_metrics["win_rate"], 1),
                "second_half": round(second_metrics["win_rate"], 1),
                "change": round(second_metrics["win_rate"] - first_metrics["win_rate"], 1),
            })

        if second_metrics["profit_factor"] < first_metrics["profit_factor"] * 0.7:
            degradations.append({
                "metric": "profit_factor",
                "first_half": round(first_metrics["profit_factor"], 2),
                "second_half": round(second_metrics["profit_factor"], 2),
                "change": round(second_metrics["profit_factor"] - first_metrics["profit_factor"], 2),
            })

        return {
            "status": "complete",
            "first_half": first_metrics,
            "second_half": second_metrics,
            "degradations": degradations,
            "is_degrading": len(degradations) > 0,
        }

    def _statistical_significance(self, rows: list) -> Dict:
        """Test if performance is statistically different from random."""
        pnls = [r["pnl"] or 0 for r in rows]
        n = len(pnls)

        if n < 10:
            return {"status": "insufficient_data"}

        mean = sum(pnls) / n
        std = math.sqrt(sum((p - mean) ** 2 for p in pnls) / (n - 1))
        se = std / math.sqrt(n)

        # t-statistic: is mean significantly different from 0?
        t_stat = mean / se if se > 0 else 0

        # Approximate p-value (two-tailed)
        # For large n, t-distribution approaches normal
        p_value = 2 * (1 - self._normal_cdf(abs(t_stat)))

        # Confidence interval for mean
        ci_95 = (mean - 1.96 * se, mean + 1.96 * se)

        # Is performance significantly positive?
        is_significant = p_value < 0.05 and mean > 0

        return {
            "status": "complete",
            "sample_size": n,
            "mean_pnl": round(mean, 4),
            "std_pnl": round(std, 4),
            "t_statistic": round(t_stat, 3),
            "p_value": round(p_value, 4),
            "ci_95": (round(ci_95[0], 4), round(ci_95[1], 4)),
            "is_significant": is_significant,
            "interpretation": (
                "Statistically significant positive performance" if is_significant else
                "Performance not yet statistically significant" if mean > 0 else
                "Performance is negative"
            ),
        }

    def _regime_analysis(self, rows: list) -> Dict:
        """Analyze performance by market regime."""
        # Group by consecutive wins/losses to infer regime
        regimes = {"winning_streak": [], "losing_streak": [], "mixed": []}

        current_streak = []
        current_type = None

        for r in rows:
            pnl = r["pnl"] or 0
            is_win = pnl > 0

            if current_type is None:
                current_type = is_win
                current_streak = [r]
            elif is_win == current_type:
                current_streak.append(r)
            else:
                # Streak ended
                streak_type = "winning_streak" if current_type else "losing_streak"
                regimes[streak_type].append({
                    "length": len(current_streak),
                    "total_pnl": sum(t["pnl"] or 0 for t in current_streak),
                })
                current_streak = [r]
                current_type = is_win

        # Add final streak
        if current_streak:
            streak_type = "winning_streak" if current_type else "losing_streak"
            regimes[streak_type].append({
                "length": len(current_streak),
                "total_pnl": sum(t["pnl"] or 0 for t in current_streak),
            })

        # Streak statistics
        win_streaks = regimes["winning_streak"]
        lose_streaks = regimes["losing_streak"]

        return {
            "avg_winning_streak": round(sum(s["length"] for s in win_streaks) / len(win_streaks), 1) if win_streaks else 0,
            "avg_losing_streak": round(sum(s["length"] for s in lose_streaks) / len(lose_streaks), 1) if lose_streaks else 0,
            "max_winning_streak": max((s["length"] for s in win_streaks), default=0),
            "max_losing_streak": max((s["length"] for s in lose_streaks), default=0),
            "current_streak": {
                "type": "winning" if current_type else "losing",
                "length": len(current_streak),
            },
        }

    def _current_health(self, rows: list, open_rows: list) -> Dict:
        """Current system health status."""
        # Last 10 trades
        last_10 = rows[-10:] if len(rows) >= 10 else rows
        pnls = [r["pnl"] or 0 for r in last_10]
        wins = [p for p in pnls if p > 0]
        wr = len(wins) / len(pnls) * 100 if pnls else 0

        # Consecutive losses
        consec_losses = 0
        for r in reversed(rows):
            if (r["pnl"] or 0) <= 0:
                consec_losses += 1
            else:
                break

        # Alerts
        alerts = []
        if wr < self.ALERT_THRESHOLDS["min_win_rate"]:
            alerts.append(f"⚠️ Win rate ({wr:.1f}%) below threshold ({self.ALERT_THRESHOLDS['min_win_rate']}%)")
        if consec_losses >= self.ALERT_THRESHOLDS["max_consec_losses"]:
            alerts.append(f"🚨 {consec_losses} consecutive losses")

        # Overall health
        health_score = 100
        if wr < 40:
            health_score -= 20
        if consec_losses >= 3:
            health_score -= 15
        if len(open_rows) > 5:
            health_score -= 10

        return {
            "health_score": max(0, health_score),
            "status": "healthy" if health_score >= 70 else "warning" if health_score >= 40 else "critical",
            "last_10_win_rate": round(wr, 1),
            "consecutive_losses": consec_losses,
            "open_positions": len(open_rows),
            "alerts": alerts,
        }

    @staticmethod
    def _normal_cdf(x: float) -> float:
        """Approximate cumulative distribution function of standard normal."""
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    def close(self) -> None:
        self._conn.close()
