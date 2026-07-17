"""
Comprehensive Analytics — Phases 3, 5, 9 combined.

Score distribution analysis, threshold simulation, and confidence analytics.
All analysis is READ-ONLY against the calibration database.

Produces statistically rigorous output for every confidence bucket.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger


class ComprehensiveAnalytics:
    """Full statistical analysis of candidates and their forward outcomes."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"

    BUCKETS = [
        (70, 74), (75, 79), (80, 84), (85, 89), (90, 94), (95, 100),
    ]

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or self.DB_PATH
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.row_factory = sqlite3.Row

    # ── Phase 9: Score Distribution ──────────────────────────────

    def score_distribution(self) -> Dict:
        """Produce histogram, CDF, percentiles, and descriptive statistics."""
        cur = self._conn.cursor()
        cur.execute("SELECT confidence FROM candidates ORDER BY confidence ASC")
        scores = [r[0] for r in cur.fetchall() if r[0] is not None]

        if not scores:
            return {"error": "No candidates logged yet", "total": 0}

        n = len(scores)
        mean = sum(scores) / n
        variance = sum((s - mean) ** 2 for s in scores) / n
        std = math.sqrt(variance)
        median = scores[n // 2] if n % 2 else (scores[n // 2 - 1] + scores[n // 2]) / 2

        # Percentiles
        percentiles = {}
        for p in [5, 10, 25, 50, 75, 90, 95]:
            idx = int(n * p / 100)
            idx = min(idx, n - 1)
            percentiles[f"p{p}"] = round(scores[idx], 1)

        # Histogram buckets
        hist = []
        for lo, hi in self.BUCKETS:
            count = sum(1 for s in scores if lo <= s <= hi)
            hist.append({"bucket": f"{lo}-{hi}", "count": count, "pct": round(count / n * 100, 1)})

        # CDF
        cdf = []
        cumulative = 0
        for lo, hi in self.BUCKETS:
            count = sum(1 for s in scores if lo <= s <= hi)
            cumulative += count
            cdf.append({"bucket": f"{lo}-{hi}", "cumulative": cumulative, "cumulative_pct": round(cumulative / n * 100, 1)})

        # Probability of profitability by confidence
        profit_prob = []
        cur.execute("""
            SELECT
                CASE
                    WHEN confidence >= 95 THEN '95-100'
                    WHEN confidence >= 90 THEN '90-94'
                    WHEN confidence >= 85 THEN '85-89'
                    WHEN confidence >= 80 THEN '80-84'
                    WHEN confidence >= 75 THEN '75-79'
                    ELSE '70-74'
                END as bucket,
                COUNT(*) as total,
                SUM(CASE WHEN return_pct > 0 THEN 1 ELSE 0 END) as wins,
                AVG(return_pct) as avg_ret
            FROM candidates
            WHERE outcome_tracked = 1
            GROUP BY bucket ORDER BY bucket DESC
        """)
        for row in cur.fetchall():
            if row[1] > 0:
                profit_prob.append({
                    "bucket": row[0],
                    "total": row[1],
                    "wins": row[2] or 0,
                    "win_rate": round((row[2] or 0) / row[1] * 100, 1),
                    "avg_return": round(row[3] or 0, 3),
                })

        return {
            "total": n,
            "mean": round(mean, 2),
            "median": round(median, 2),
            "std_dev": round(std, 2),
            "variance": round(variance, 2),
            "min": round(scores[0], 1),
            "max": round(scores[-1], 1),
            "percentiles": percentiles,
            "histogram": hist,
            "cdf": cdf,
            "profitability_by_bucket": profit_prob,
        }

    # ── Phase 3: Confidence Analytics ────────────────────────────

    def bucket_analytics(self) -> List[Dict]:
        """For every confidence bucket calculate comprehensive performance metrics."""
        results = []
        cur = self._conn.cursor()

        for lo, hi in self.BUCKETS:
            cur.execute("""
                SELECT
                    confidence, return_pct, mfe, mae, rr_achieved,
                    trend_score, pullback_score, candle_score, volume_score, regime_score,
                    direction, entry_price, stop_loss, symbol, timestamp
                FROM candidates
                WHERE confidence >= ? AND confidence <= ? AND outcome_tracked = 1
                ORDER BY confidence DESC
            """, (lo, hi))
            rows = cur.fetchall()

            total = len(rows)
            if total == 0:
                results.append({
                    "bucket": f"{lo}-{hi}", "total": 0, "tracked": 0,
                    "note": "No tracked outcomes yet",
                })
                continue

            returns = [r[1] for r in rows if r[1] is not None]
            mfes = [r[2] for r in rows if r[2] is not None]
            maes = [r[3] for r in rows if r[3] is not None]
            rrs = [r[4] for r in rows if r[4] is not None]

            wins = [r for r in returns if r > 0]
            losses = [r for r in returns if r <= 0]
            win_rate = len(wins) / total * 100 if total else 0

            avg_ret = sum(returns) / len(returns) if returns else 0
            median_ret = sorted(returns)[len(returns) // 2] if returns else 0
            max_gain = max(returns) if returns else 0
            max_loss = min(returns) if returns else 0

            gross_profit = sum(wins) if wins else 0
            gross_loss = sum(abs(l) for l in losses) if losses else 0
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf') if gross_profit > 0 else 0

            avg_mfe = sum(mfes) / len(mfes) if mfes else 0
            avg_mae = sum(maes) / len(maes) if maes else 0
            avg_rr = sum(rrs) / len(rrs) if rrs else 0

            # Expectancy: E = WR * avg_win - (1-WR) * avg_loss
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(abs(l) for l in losses) / len(losses) if losses else 0
            expectancy = (win_rate / 100 * avg_win) - ((100 - win_rate) / 100 * avg_loss)

            # Sharpe approximation (using returns)
            if len(returns) > 1:
                ret_mean = sum(returns) / len(returns)
                ret_std = math.sqrt(sum((r - ret_mean) ** 2 for r in returns) / (len(returns) - 1))
                sharpe = (ret_mean / ret_std) if ret_std > 0 else 0
            else:
                sharpe = 0

            # Sortino (downside deviation)
            downside = [r for r in returns if r < 0]
            if len(downside) > 1:
                ds_mean = sum(downside) / len(downside)
                ds_std = math.sqrt(sum((r - ds_mean) ** 2 for r in downside) / (len(downside) - 1))
                sortino = (avg_ret / ds_std) if ds_std > 0 else 0
            else:
                sortino = sharpe  # Approximate

            # Max drawdown from returns
            cumulative = 0
            peak = 0
            max_dd = 0
            for r in returns:
                cumulative += r
                peak = max(peak, cumulative)
                dd = peak - cumulative
                max_dd = max(max_dd, dd)

            # Calmar (annualized return / max drawdown)
            calmar = avg_ret / max_dd if max_dd > 0 else 0

            # Holding period approximation (based on avg return)
            results.append({
                "bucket": f"{lo}-{hi}",
                "total": total,
                "tracked": total,
                "win_rate": round(win_rate, 1),
                "loss_rate": round(100 - win_rate, 1),
                "avg_return": round(avg_ret, 3),
                "median_return": round(median_ret, 3),
                "max_gain": round(max_gain, 3),
                "max_loss": round(max_loss, 3),
                "avg_win": round(avg_win, 3),
                "avg_loss": round(avg_loss, 3),
                "profit_factor": round(profit_factor, 2),
                "expectancy": round(expectancy, 3),
                "sharpe": round(sharpe, 3),
                "sortino": round(sortino, 3),
                "calmar": round(calmar, 3),
                "avg_mfe": round(avg_mfe, 3),
                "avg_mae": round(avg_mae, 3),
                "avg_rr": round(avg_rr, 3),
                "max_drawdown": round(max_dd, 3),
            })

        return results

    # ── Phase 5: Threshold Simulation ────────────────────────────

    def threshold_simulation(
        self,
        thresholds: Optional[List[float]] = None,
    ) -> List[Dict]:
        """Simulate different confidence thresholds with full metrics."""
        if thresholds is None:
            thresholds = [80, 82, 84, 86, 88, 90, 92, 95]

        results = []
        cur = self._conn.cursor()

        for thresh in thresholds:
            cur.execute("""
                SELECT confidence, return_pct, mfe, mae, rr_achieved, timestamp
                FROM candidates
                WHERE confidence >= ? AND outcome_tracked = 1
                ORDER BY confidence DESC
            """, (thresh,))
            rows = cur.fetchall()

            total = len(rows)
            if total == 0:
                results.append({
                    "threshold": thresh,
                    "total_trades": 0,
                    "note": "No tracked outcomes at this threshold",
                })
                continue

            returns = [r[1] for r in rows if r[1] is not None]
            mfes = [r[2] for r in rows if r[2] is not None]
            maes = [r[3] for r in rows if r[3] is not None]
            rrs = [r[4] for r in rows if r[4] is not None]

            wins = [r for r in returns if r > 0]
            losses = [r for r in returns if r <= 0]
            win_rate = len(wins) / total * 100

            avg_ret = sum(returns) / len(returns) if returns else 0
            gross_profit = sum(wins) if wins else 0
            gross_loss = sum(abs(l) for l in losses) if losses else 0
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(abs(l) for l in losses) / len(losses) if losses else 0
            expectancy = (win_rate / 100 * avg_win) - ((100 - win_rate) / 100 * avg_loss)

            avg_mfe = sum(mfes) / len(mfes) if mfes else 0
            avg_mae = sum(maes) / len(maes) if maes else 0
            avg_rr = sum(rrs) / len(rrs) if rrs else 0

            # Sharpe
            if len(returns) > 1:
                ret_mean = sum(returns) / len(returns)
                ret_std = math.sqrt(sum((r - ret_mean) ** 2 for r in returns) / (len(returns) - 1))
                sharpe = (ret_mean / ret_std) if ret_std > 0 else 0
            else:
                sharpe = 0

            # Sortino
            downside = [r for r in returns if r < 0]
            if len(downside) > 1:
                ds_mean = sum(downside) / len(downside)
                ds_std = math.sqrt(sum((r - ds_mean) ** 2 for r in downside) / (len(downside) - 1))
                sortino = (avg_ret / ds_std) if ds_std > 0 else 0
            else:
                sortino = sharpe

            # Max drawdown
            cumulative = 0
            peak = 0
            max_dd = 0
            for r in returns:
                cumulative += r
                peak = max(peak, cumulative)
                max_dd = max(max_dd, peak - cumulative)

            # Trades per day
            timestamps = [r[5] for r in rows if r[5]]
            if len(timestamps) >= 2:
                days = max((max(timestamps) - min(timestamps)) / 86400, 0.01)
                trades_per_day = total / days
            else:
                trades_per_day = 0

            results.append({
                "threshold": thresh,
                "total_trades": total,
                "win_rate": round(win_rate, 1),
                "avg_return": round(avg_ret, 3),
                "profit_factor": round(profit_factor, 2),
                "expectancy": round(expectancy, 3),
                "avg_mfe": round(avg_mfe, 3),
                "avg_mae": round(avg_mae, 3),
                "avg_rr": round(avg_rr, 3),
                "sharpe": round(sharpe, 3),
                "sortino": round(sortino, 3),
                "max_drawdown": round(max_dd, 3),
                "trades_per_day": round(trades_per_day, 1),
            })

        # Mark optimal threshold
        valid = [r for r in results if r.get("total_trades", 0) > 0]
        if valid:
            best = max(valid, key=lambda x: x.get("profit_factor", 0))
            for r in results:
                r["optimal"] = r["threshold"] == best["threshold"]

        return results

    # ── Utility ──────────────────────────────────────────────────

    def summary(self) -> Dict:
        """Overall summary statistics."""
        cur = self._conn.cursor()
        cur.execute("""
            SELECT COUNT(*),
                   COUNT(CASE WHEN outcome_tracked = 1 THEN 1 END),
                   COUNT(CASE WHEN passed = 1 THEN 1 END),
                   ROUND(AVG(confidence), 1),
                   ROUND(MAX(confidence), 1),
                   ROUND(MIN(confidence), 1),
                   ROUND(AVG(CASE WHEN outcome_tracked = 1 THEN return_pct END), 3)
            FROM candidates
        """)
        row = cur.fetchone()
        return {
            "total_candidates": row[0] or 0,
            "tracked_outcomes": row[1] or 0,
            "passed_gate": row[2] or 0,
            "avg_confidence": row[3] or 0,
            "max_confidence": row[4] or 0,
            "min_confidence": row[5] or 0,
            "avg_return": row[6] or 0,
        }

    def close(self) -> None:
        self._conn.close()
