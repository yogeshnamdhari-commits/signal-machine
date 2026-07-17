"""
Calibration Analytics — Score distribution, threshold simulation, weight optimization.

Produces statistical evidence for confidence model calibration decisions.
All analysis is READ-ONLY against the calibration database.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


class CalibrationAnalytics:
    """Statistical analysis of logged candidates and their outcomes."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"

    # Confidence buckets
    BUCKETS = [
        (70, 74), (75, 79), (80, 84), (85, 89), (90, 94), (95, 100),
    ]

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or self.DB_PATH
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)

    def score_distribution(self) -> List[Dict]:
        """Get candidate count and outcome stats per confidence bucket."""
        results = []
        cur = self._conn.cursor()

        for lo, hi in self.BUCKETS:
            cur.execute("""
                SELECT COUNT(*),
                       SUM(CASE WHEN outcome_tracked = 1 THEN 1 ELSE 0 END),
                       AVG(CASE WHEN outcome_tracked = 1 THEN return_pct END),
                       MAX(CASE WHEN outcome_tracked = 1 THEN return_pct END),
                       MIN(CASE WHEN outcome_tracked = 1 THEN return_pct END),
                       AVG(CASE WHEN outcome_tracked = 1 THEN mfe END),
                       AVG(CASE WHEN outcome_tracked = 1 THEN mae END),
                       AVG(confidence)
                FROM candidates
                WHERE confidence >= ? AND confidence < ?
            """, (lo, hi + 1))
            row = cur.fetchone()
            total = row[0] or 0
            tracked = row[1] or 0
            avg_ret = row[2] or 0
            max_ret = row[3] or 0
            min_ret = row[4] or 0
            avg_mfe = row[5] or 0
            avg_mae = row[6] or 0
            avg_conf = row[7] or 0

            # Win rate
            cur.execute("""
                SELECT COUNT(*) FROM candidates
                WHERE confidence >= ? AND confidence < ? AND outcome_tracked = 1 AND return_pct > 0
            """, (lo, hi + 1))
            wins = cur.fetchone()[0] or 0
            win_rate = (wins / tracked * 100) if tracked > 0 else 0

            # Profit factor
            cur.execute("""
                SELECT
                    SUM(CASE WHEN return_pct > 0 THEN return_pct ELSE 0 END),
                    SUM(CASE WHEN return_pct < 0 THEN ABS(return_pct) ELSE 0 END)
                FROM candidates
                WHERE confidence >= ? AND confidence < ? AND outcome_tracked = 1
            """, (lo, hi + 1))
            pf_row = cur.fetchone()
            gross_profit = pf_row[0] or 0
            gross_loss = pf_row[1] or 0
            profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0

            results.append({
                "bucket": f"{lo}-{hi}",
                "total": total,
                "tracked": tracked,
                "win_rate": round(win_rate, 1),
                "avg_return": round(avg_ret, 3),
                "max_return": round(max_ret, 3),
                "min_return": round(min_ret, 3),
                "avg_mfe": round(avg_mfe, 3),
                "avg_mae": round(avg_mae, 3),
                "profit_factor": round(profit_factor, 2),
                "avg_confidence": round(avg_conf, 1),
            })

        return results

    def threshold_simulation(self, thresholds: Optional[List[float]] = None) -> List[Dict]:
        """Simulate different confidence thresholds and compute hypothetical performance."""
        if thresholds is None:
            thresholds = [80, 82, 84, 86, 88, 90, 92, 95]

        results = []
        cur = self._conn.cursor()

        for thresh in thresholds:
            cur.execute("""
                SELECT COUNT(*),
                       SUM(CASE WHEN return_pct > 0 THEN 1 ELSE 0 END),
                       AVG(CASE WHEN outcome_tracked = 1 THEN return_pct END),
                       SUM(CASE WHEN return_pct > 0 THEN return_pct ELSE 0 END),
                       SUM(CASE WHEN return_pct < 0 THEN ABS(return_pct) ELSE 0 END),
                       AVG(CASE WHEN outcome_tracked = 1 THEN mfe END),
                       AVG(CASE WHEN outcome_tracked = 1 THEN mae END)
                FROM candidates
                WHERE confidence >= ? AND outcome_tracked = 1
            """, (thresh,))
            row = cur.fetchone()
            total = row[0] or 0
            wins = row[1] or 0
            avg_ret = row[2] or 0
            gross_profit = row[3] or 0
            gross_loss = row[4] or 0
            avg_mfe = row[5] or 0
            avg_mae = row[6] or 0

            win_rate = (wins / total * 100) if total > 0 else 0
            profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0
            expectancy = (avg_ret * win_rate / 100 - (100 - win_rate) / 100 * abs(avg_ret)) if total > 0 else 0

            # Trades per day
            cur.execute("""
                SELECT MIN(timestamp), MAX(timestamp) FROM candidates WHERE confidence >= ?
            """, (thresh,))
            ts_row = cur.fetchone()
            if ts_row[0] and ts_row[1]:
                days = max((ts_row[1] - ts_row[0]) / 86400, 0.01)
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
                "trades_per_day": round(trades_per_day, 1),
            })

        return results

    def component_analysis(self) -> List[Dict]:
        """Analyze which scoring component most penalizes profitable candidates."""
        cur = self._conn.cursor()

        # Get candidates with outcomes
        cur.execute("""
            SELECT id, confidence, trend_score, pullback_score, candle_score,
                   volume_score, regime_score, return_pct, mfe, mae, direction
            FROM candidates WHERE outcome_tracked = 1
            ORDER BY return_pct DESC
        """)
        rows = cur.fetchall()

        if not rows:
            return []

        components = ["trend", "pullback", "candle", "volume", "regime"]
        results = []

        for comp in components:
            col_idx = {"trend": 2, "pullback": 3, "candle": 4, "volume": 5, "regime": 6}[comp]

            # Average score for profitable vs unprofitable
            profitable_scores = [r[col_idx] for r in rows if r[7] and r[7] > 0 and r[col_idx] is not None]
            unprofitable_scores = [r[col_idx] for r in rows if r[7] and r[7] <= 0 and r[col_idx] is not None]
            all_scores = [r[col_idx] for r in rows if r[col_idx] is not None]

            avg_profitable = sum(profitable_scores) / len(profitable_scores) if profitable_scores else 0
            avg_unprofitable = sum(unprofitable_scores) / len(unprofitable_scores) if unprofitable_scores else 0
            avg_all = sum(all_scores) / len(all_scores) if all_scores else 0

            # MFE when this component was low (< 50)
            low_component = [(r[7], r[8]) for r in rows if r[col_idx] is not None and r[col_idx] < 50]
            avg_ret_low = sum(x[0] for x in low_component) / len(low_component) if low_component else 0
            avg_mfe_low = sum(x[1] for x in low_component if x[1]) / len([x for x in low_component if x[1]]) if low_component else 0

            results.append({
                "component": comp,
                "avg_score_all": round(avg_all, 1),
                "avg_score_profitable": round(avg_profitable, 1),
                "avg_score_unprofitable": round(avg_unprofitable, 1),
                "score_gap": round(avg_profitable - avg_unprofitable, 1),
                "avg_return_when_low": round(avg_ret_low, 3),
                "avg_mfe_when_low": round(avg_mfe_low, 3),
                "candidates_below_50": len(low_component),
            })

        # Sort by score gap (largest gap = most predictive)
        results.sort(key=lambda x: abs(x["score_gap"]), reverse=True)
        return results

    def false_negatives(self, min_rr: float = 2.0) -> List[Dict]:
        """Find rejected candidates that would have produced ≥min_rr return."""
        cur = self._conn.cursor()
        cur.execute("""
            SELECT symbol, timestamp, confidence, direction, entry_price,
                   stop_loss, take_profit, trend_score, pullback_score,
                   candle_score, volume_score, regime_score, return_pct,
                   mfe, mae, rr_achieved, rejection_reason
            FROM candidates
            WHERE passed = 0 AND outcome_tracked = 1 AND rr_achieved >= ?
            ORDER BY rr_achieved DESC
            LIMIT 50
        """, (min_rr,))
        rows = cur.fetchall()

        return [{
            "symbol": r[0], "timestamp": r[1], "confidence": r[2],
            "direction": r[3], "entry": r[4], "sl": r[5], "tp": r[6],
            "trend": r[7], "pullback": r[8], "candle": r[9],
            "volume": r[10], "regime": r[11], "return_pct": r[12],
            "mfe": r[13], "mae": r[14], "rr_achieved": r[15],
            "rejection_reason": r[16],
        } for r in rows]

    def summary(self) -> Dict:
        """Get overall summary statistics."""
        cur = self._conn.cursor()
        cur.execute("""
            SELECT COUNT(*),
                   AVG(confidence),
                   MAX(confidence),
                   SUM(CASE WHEN passed = 1 THEN 1 ELSE 0 END),
                   SUM(CASE WHEN outcome_tracked = 1 THEN 1 ELSE 0 END),
                   AVG(CASE WHEN outcome_tracked = 1 THEN return_pct END)
            FROM candidates
        """)
        row = cur.fetchone()
        return {
            "total_candidates": row[0] or 0,
            "avg_confidence": round(row[1] or 0, 1),
            "max_confidence": round(row[2] or 0, 1),
            "passed_gate": row[3] or 0,
            "tracked_outcomes": row[4] or 0,
            "avg_return": round(row[5] or 0, 3),
        }

    def close(self) -> None:
        self._conn.close()
