"""
Score Validation — Verifies that higher confidence scores correlate with
better trading outcomes (monotonicity check).

Key questions:
- Do 95-100 trades outperform 90-94 trades?
- Is confidence monotonic with profitability?
- Which confidence ranges are profitable vs unprofitable?
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


class ScoreValidator:
    """Validates that confidence scores are properly calibrated."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "institutional_v1.db"

    BUCKETS = [
        (95, 100, "95-100"),
        (90, 94.99, "90-94"),
        (85, 89.99, "85-89"),
        (80, 84.99, "80-84"),
        (75, 79.99, "75-79"),
        (0, 74.99, "<75"),
    ]

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or self.DB_PATH
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.row_factory = sqlite3.Row

    def validate(self) -> Dict:
        """Full score validation analysis."""
        cur = self._conn.cursor()

        # Get all closed trades
        cur.execute("""
            SELECT symbol, side, entry_price, pnl, confidence * 100 as conf_pct,
                   exit_reason, hold_minutes, realized_r, mfe_pct, mae_pct,
                   strategy_version, opened_at, closed_at
            FROM positions WHERE status = 'closed'
            ORDER BY conf_pct DESC
        """)
        rows = cur.fetchall()

        if not rows:
            return {"status": "no_data", "message": "No closed trades for score validation"}

        # Bucket analysis
        bucket_results = []
        for lo, hi, label in self.BUCKETS:
            trades = [r for r in rows if lo <= (r["conf_pct"] or 0) <= hi]
            if not trades:
                continue

            pnls = [t["pnl"] or 0 for t in trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            r_multiples = [t["realized_r"] or 0 for t in trades if t["realized_r"] is not None]
            mfes = [t["mfe_pct"] or 0 for t in trades if t["mfe_pct"] is not None]
            maes = [t["mae_pct"] or 0 for t in trades if t["mae_pct"] is not None]

            total = len(pnls)
            wr = len(wins) / total * 100 if total else 0
            gp = sum(wins)
            gl = sum(abs(l) for l in losses)
            pf = gp / gl if gl > 0 else 0
            avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0
            avg_mfe = sum(mfes) / len(mfes) if mfes else 0
            avg_mae = sum(maes) / len(maes) if maes else 0
            avg_hold = sum(t["hold_minutes"] or 0 for t in trades) / total if total else 0

            # Expectancy
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(abs(l) for l in losses) / len(losses) if losses else 0
            expectancy = (wr / 100 * avg_win) - ((100 - wr) / 100 * avg_loss)

            bucket_results.append({
                "bucket": label,
                "trades": total,
                "win_rate": round(wr, 1),
                "total_pnl": round(sum(pnls), 3),
                "avg_pnl": round(sum(pnls) / total, 3) if total else 0,
                "profit_factor": round(pf, 2),
                "expectancy": round(expectancy, 3),
                "avg_r": round(avg_r, 3),
                "avg_mfe": round(avg_mfe, 3),
                "avg_mae": round(avg_mae, 3),
                "avg_hold_min": round(avg_hold, 1),
            })

        # Monotonicity check
        # A properly calibrated model should show:
        # 1. Win rate increases with confidence
        # 2. Profit factor increases with confidence
        # 3. Average return increases with confidence
        monotonicity = self._check_monotonicity(bucket_results)

        # Rank correlation (Spearman)
        spearman = self._spearman_correlation(rows)

        # Optimal threshold analysis
        optimal = self._find_optimal_threshold(rows)

        # Anomaly detection: identify buckets that break monotonicity
        anomalies = self._detect_anomalies(bucket_results)

        return {
            "status": "complete",
            "total_trades": len(rows),
            "bucket_analysis": bucket_results,
            "monotonicity": monotonicity,
            "spearman_correlation": spearman,
            "optimal_threshold": optimal,
            "anomalies": anomalies,
        }

    def _check_monotonicity(self, buckets: List[Dict]) -> Dict:
        """Check if metrics increase monotonically with confidence."""
        if len(buckets) < 2:
            return {"status": "insufficient_data"}

        # Sort by confidence (high to low)
        sorted_buckets = sorted(buckets, key=lambda x: self._bucket_midpoint(x["bucket"]), reverse=True)

        metrics_to_check = ["win_rate", "profit_factor", "avg_pnl", "expectancy", "avg_r"]
        monotonicity_results = {}

        for metric in metrics_to_check:
            values = [b[metric] for b in sorted_buckets]
            # Count monotonic pairs
            monotonic_pairs = 0
            total_pairs = 0
            for i in range(len(values)):
                for j in range(i + 1, len(values)):
                    total_pairs += 1
                    if values[i] >= values[j]:  # Higher confidence should have higher value
                        monotonic_pairs += 1

            monotonicity_pct = monotonic_pairs / total_pairs * 100 if total_pairs > 0 else 0
            monotonicity_results[metric] = {
                "monotonicity_pct": round(monotonicity_pct, 1),
                "is_monotonic": monotonicity_pct >= 80,
                "values": [round(v, 3) for v in values],
            }

        # Overall monotonicity score
        scores = [m["monotonicity_pct"] for m in monotonicity_results.values()]
        overall = sum(scores) / len(scores) if scores else 0

        return {
            "overall_score": round(overall, 1),
            "is_monotonic": overall >= 80,
            "metrics": monotonicity_results,
        }

    def _spearman_correlation(self, rows: list) -> Dict:
        """Spearman rank correlation between confidence and PnL."""
        pairs = [(r["conf_pct"] or 0, r["pnl"] or 0) for r in rows]
        if len(pairs) < 5:
            return {"correlation": 0, "status": "insufficient_data"}

        # Rank both variables
        x_ranked = self._rank([p[0] for p in pairs])
        y_ranked = self._rank([p[1] for p in pairs])

        # Pearson on ranks
        n = len(pairs)
        mx = sum(x_ranked) / n
        my = sum(y_ranked) / n
        cov = sum((x - mx) * (y - my) for x, y in zip(x_ranked, y_ranked)) / n
        sx = math.sqrt(sum((x - mx) ** 2 for x, y in zip(x_ranked, y_ranked)) / n)
        sy = math.sqrt(sum((y - my) ** 2 for x, y in zip(x_ranked, y_ranked)) / n)

        spearman = cov / (sx * sy) if sx * sy > 0 else 0

        return {
            "correlation": round(spearman, 4),
            "interpretation": (
                "Strong positive" if spearman > 0.5 else
                "Moderate positive" if spearman > 0.2 else
                "Weak" if spearman > -0.2 else
                "Negative" if spearman > -0.5 else
                "Strong negative"
            ),
        }

    def _find_optimal_threshold(self, rows: list) -> Dict:
        """Find the confidence threshold that maximizes profit factor."""
        thresholds = range(70, 100, 1)
        best_pf = 0
        best_thresh = 90
        results = []

        for thresh in thresholds:
            trades = [r for r in rows if (r["conf_pct"] or 0) >= thresh]
            if len(trades) < 3:
                continue

            pnls = [t["pnl"] or 0 for t in trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            gp = sum(wins)
            gl = sum(abs(l) for l in losses)
            pf = gp / gl if gl > 0 else 0
            wr = len(wins) / len(pnls) * 100 if pnls else 0

            results.append({
                "threshold": thresh,
                "trades": len(trades),
                "win_rate": round(wr, 1),
                "profit_factor": round(pf, 2),
                "total_pnl": round(sum(pnls), 3),
            })

            if pf > best_pf:
                best_pf = pf
                best_thresh = thresh

        return {
            "optimal_threshold": best_thresh,
            "best_profit_factor": best_pf,
            "simulation": results,
        }

    def _detect_anomalies(self, buckets: List[Dict]) -> List[Dict]:
        """Detect buckets that break the expected monotonic pattern."""
        anomalies = []
        sorted_buckets = sorted(buckets, key=lambda x: self._bucket_midpoint(x["bucket"]), reverse=True)

        for i in range(len(sorted_buckets) - 1):
            current = sorted_buckets[i]
            lower = sorted_buckets[i + 1]

            # Higher confidence should have better metrics
            if current["win_rate"] < lower["win_rate"]:
                anomalies.append({
                    "type": "win_rate_inversion",
                    "higher_bucket": current["bucket"],
                    "lower_bucket": lower["bucket"],
                    "higher_wr": current["win_rate"],
                    "lower_wr": lower["win_rate"],
                })

            if current["profit_factor"] < lower["profit_factor"] and lower["profit_factor"] > 0:
                anomalies.append({
                    "type": "profit_factor_inversion",
                    "higher_bucket": current["bucket"],
                    "lower_bucket": lower["bucket"],
                    "higher_pf": current["profit_factor"],
                    "lower_pf": lower["profit_factor"],
                })

        return anomalies

    @staticmethod
    def _bucket_midpoint(label: str) -> float:
        """Get numeric midpoint of bucket label for sorting."""
        mapping = {"95-100": 97.5, "90-94": 92, "85-89": 87, "80-84": 82, "75-79": 77, "<75": 70}
        return mapping.get(label, 0)

    @staticmethod
    def _rank(values: List[float]) -> List[float]:
        """Assign ranks to values (average rank for ties)."""
        n = len(values)
        sorted_vals = sorted(enumerate(values), key=lambda x: x[1])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and sorted_vals[j + 1][1] == sorted_vals[j][1]:
                j += 1
            avg_rank = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[sorted_vals[k][0]] = avg_rank
            i = j + 1
        return ranks

    def close(self) -> None:
        self._conn.close()
