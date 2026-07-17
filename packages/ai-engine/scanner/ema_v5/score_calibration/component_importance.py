"""
Component Importance Analysis — Phase 4.

Measures the predictive value of every scoring component.
Calculates correlation with returns, feature importance,
false positive/negative rates, and produces ranked importance.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger


class ComponentImportance:
    """Analyzes predictive power of each scoring component."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"

    COMPONENTS = ["trend_score", "pullback_score", "candle_score", "volume_score", "regime_score"]
    COMPONENT_LABELS = {
        "trend_score": "trend",
        "pullback_score": "pullback",
        "candle_score": "candle",
        "volume_score": "volume",
        "regime_score": "regime",
    }

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or self.DB_PATH
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.row_factory = sqlite3.Row

    def analyze(self) -> List[Dict]:
        """Full component importance analysis with ranked output."""
        cur = self._conn.cursor()
        cur.execute("""
            SELECT confidence, trend_score, pullback_score, candle_score,
                   volume_score, regime_score, return_pct, mfe, mae, rr_achieved,
                   passed, direction
            FROM candidates
            WHERE outcome_tracked = 1
            ORDER BY confidence DESC
        """)
        rows = cur.fetchall()

        if not rows:
            return [{"component": self.COMPONENT_LABELS[c], "status": "insufficient_data"} for c in self.COMPONENTS]

        results = []
        for comp_col in self.COMPONENTS:
            label = self.COMPONENT_LABELS[comp_col]
            col_idx = self.COMPONENTS.index(comp_col) + 1  # offset by 1 for confidence

            scores = [r[col_idx] for r in rows if r[col_idx] is not None]
            returns = [r[8] for r in rows if r[8] is not None]  # return_pct
            mfes = [r[9] for r in rows if r[9] is not None]    # mfe

            # Match pairs for correlation
            pairs_ret = [(r[col_idx], r[8]) for r in rows if r[col_idx] is not None and r[8] is not None]
            pairs_mfe = [(r[col_idx], r[9]) for r in rows if r[col_idx] is not None and r[9] is not None]

            # Average contribution to profitable vs unprofitable
            profitable = [r[col_idx] for r in rows if r[8] is not None and r[8] > 0 and r[col_idx] is not None]
            unprofitable = [r[col_idx] for r in rows if r[8] is not None and r[8] <= 0 and r[col_idx] is not None]

            avg_all = sum(scores) / len(scores) if scores else 0
            avg_profitable = sum(profitable) / len(profitable) if profitable else 0
            avg_unprofitable = sum(unprofitable) / len(unprofitable) if unprofitable else 0

            # Correlation with return
            corr_return = self._pearson(pairs_ret) if len(pairs_ret) > 2 else 0
            corr_mfe = self._pearson(pairs_mfe) if len(pairs_mfe) > 2 else 0

            # Feature importance via variance reduction
            # How much does this component separate winners from losers?
            if profitable and unprofitable:
                pooled_std = math.sqrt(
                    (self._variance(profitable) + self._variance(unprofitable)) / 2
                )
                separation = (avg_profitable - avg_unprofitable) / pooled_std if pooled_std > 0 else 0
            else:
                separation = 0

            # False positive rate: high component score but trade lost
            high_threshold = 80
            fp = [r for r in rows if r[col_idx] is not None and r[col_idx] >= high_threshold and r[8] is not None and r[8] <= 0]
            tp = [r for r in rows if r[col_idx] is not None and r[col_idx] >= high_threshold and r[8] is not None and r[8] > 0]
            fp_rate = len(fp) / (len(fp) + len(tp)) * 100 if (len(fp) + len(tp)) > 0 else 0

            # False negative rate: low component score but trade would have been profitable
            low_threshold = 50
            fn = [r for r in rows if r[col_idx] is not None and r[col_idx] < low_threshold and r[8] is not None and r[8] > 0]
            tn = [r for r in rows if r[col_idx] is not None and r[col_idx] < low_threshold and r[8] is not None and r[8] <= 0]
            fn_rate = len(fn) / (len(fn) + len(tn)) * 100 if (len(fn) + len(tn)) > 0 else 0

            # Candidates penalized (score < 50) that would have been profitable
            penalized_profitable = len(fn)

            results.append({
                "component": label,
                "avg_score_all": round(avg_all, 1),
                "avg_score_profitable": round(avg_profitable, 1),
                "avg_score_unprofitable": round(avg_unprofitable, 1),
                "score_gap": round(avg_profitable - avg_unprofitable, 1),
                "separation_index": round(separation, 3),
                "correlation_with_return": round(corr_return, 4),
                "correlation_with_mfe": round(corr_mfe, 4),
                "false_positive_rate": round(fp_rate, 1),
                "false_negative_rate": round(fn_rate, 1),
                "penalized_profitable_count": penalized_profitable,
                "contribution_to_confidence": round(avg_all / sum(s for s in scores) * 100, 1) if sum(scores) > 0 else 0,
            })

        # Rank by predictive power (absolute correlation with return)
        results.sort(key=lambda x: abs(x["correlation_with_return"]), reverse=True)
        for i, r in enumerate(results):
            r["rank"] = i + 1

        return results

    def marginal_contribution(self) -> Dict[str, Dict]:
        """Measure marginal contribution of each component to final confidence."""
        cur = self._conn.cursor()
        cur.execute("""
            SELECT confidence, trend_score, pullback_score, candle_score,
                   volume_score, regime_score, return_pct
            FROM candidates WHERE outcome_tracked = 1
        """)
        rows = cur.fetchall()
        if not rows:
            return {}

        results = {}
        for comp_col in self.COMPONENTS:
            label = self.COMPONENT_LABELS[comp_col]
            col_idx = self.COMPONENTS.index(comp_col) + 1

            # Weight sensitivity: how much does a 10-point change in this component
            # affect final confidence?
            scores_col = [r[col_idx] for r in rows if r[col_idx] is not None]
            confs = [r[0] for r in rows if r[0] is not None]

            if not scores_col or not confs:
                continue

            # Correlation between component and final confidence
            pairs = [(r[col_idx], r[0]) for r in rows if r[col_idx] is not None and r[0] is not None]
            corr_with_conf = self._pearson(pairs) if len(pairs) > 2 else 0

            # Sensitivity: change in confidence per unit change in component
            pairs_ret = [(r[col_idx], r[7]) for r in rows if r[col_idx] is not None and r[7] is not None]
            corr_with_return = self._pearson(pairs_ret) if len(pairs_ret) > 2 else 0

            # Marginal value: correlation_with_return * weight
            # Higher = more predictive power per unit weight
            marginal = abs(corr_with_return) * (sum(scores_col) / len(scores_col) / 100)

            results[label] = {
                "correlation_with_confidence": round(corr_with_conf, 4),
                "correlation_with_return": round(corr_with_return, 4),
                "marginal_value": round(marginal, 4),
                "avg_score": round(sum(scores_col) / len(scores_col), 1),
            }

        return results

    # ── Statistics utilities ─────────────────────────────────────

    @staticmethod
    def _pearson(pairs: List[Tuple[float, float]]) -> float:
        """Pearson correlation coefficient."""
        n = len(pairs)
        if n < 3:
            return 0.0
        x = [p[0] for p in pairs]
        y = [p[1] for p in pairs]
        mx = sum(x) / n
        my = sum(y) / n
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / n
        sx = math.sqrt(sum((xi - mx) ** 2 for xi, yi in zip(x, y)) / n)
        sy = math.sqrt(sum((yi - my) ** 2 for xi, yi in zip(x, y)) / n)
        if sx * sy == 0:
            return 0.0
        return cov / (sx * sy)

    @staticmethod
    def _variance(values: List[float]) -> float:
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        return sum((v - mean) ** 2 for v in values) / len(values)

    def close(self) -> None:
        self._conn.close()
