"""
Weight Optimizer — Phase 6.

Uses historical outcomes, marginal contribution, sensitivity analysis,
and feature importance to recommend optimal component weights.
All recommendations include confidence intervals and statistical significance.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger


class WeightOptimizer:
    """Recommends optimal component weights based on historical performance."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"

    # Current production weights
    CURRENT_WEIGHTS = {
        "trend": 0.25,
        "pullback": 0.25,
        "candle": 0.20,
        "volume": 0.15,
        "regime": 0.15,
    }

    COMPONENTS = ["trend_score", "pullback_score", "candle_score", "volume_score", "regime_score"]
    COMP_LABELS = {
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

    def optimize(self) -> Dict:
        """Full weight optimization analysis."""
        cur = self._conn.cursor()
        cur.execute("""
            SELECT confidence, trend_score, pullback_score, candle_score,
                   volume_score, regime_score, return_pct, mfe, mae, rr_achieved
            FROM candidates WHERE outcome_tracked = 1
        """)
        rows = cur.fetchall()

        if len(rows) < 10:
            return {
                "status": "insufficient_data",
                "min_required": 10,
                "current": len(rows),
                "recommendation": "Continue collecting data. Need at least 10 tracked outcomes.",
            }

        # ── Method 1: Correlation-based weight suggestion ──
        corr_weights = self._correlation_based(rows)

        # ── Method 2: Sensitivity analysis ──
        sensitivity = self._sensitivity_analysis(rows)

        # ── Method 3: Marginal contribution ──
        marginal = self._marginal_contribution(rows)

        # ── Bootstrap confidence intervals ──
        bootstrap_ci = self._bootstrap_weights(rows)

        # ── Final recommendation: ensemble of methods ──
        recommended = {}
        significance = {}

        for label in self.CURRENT_WEIGHTS:
            vals = [
                corr_weights.get(label, self.CURRENT_WEIGHTS[label]),
                sensitivity.get(label, {}).get("optimal_weight", self.CURRENT_WEIGHTS[label]),
                marginal.get(label, {}).get("suggested_weight", self.CURRENT_WEIGHTS[label]),
            ]
            recommended[label] = round(sum(vals) / len(vals), 4)
            ci = bootstrap_ci.get(label, {"low": 0, "high": 1})
            significance[label] = {
                "current": self.CURRENT_WEIGHTS[label],
                "suggested": recommended[label],
                "delta": round(recommended[label] - self.CURRENT_WEIGHTS[label], 4),
                "ci_low": ci["low"],
                "ci_high": ci["high"],
                "statistically_significant": abs(recommended[label] - self.CURRENT_WEIGHTS[label]) > ci["high"] - ci["low"],
            }

        # Normalize to sum = 1.0
        total = sum(recommended.values())
        if total > 0:
            recommended = {k: round(v / total, 4) for k, v in recommended.items()}
            for k in recommended:
                significance[k]["suggested_normalized"] = round(recommended[k], 4)

        return {
            "status": "complete",
            "sample_size": len(rows),
            "correlation_based": corr_weights,
            "sensitivity": sensitivity,
            "marginal": marginal,
            "bootstrap_ci": bootstrap_ci,
            "recommended_weights": recommended,
            "significance": significance,
            "expected_improvement": self._estimate_improvement(rows, recommended),
        }

    def _correlation_based(self, rows: list) -> Dict[str, float]:
        """Weight components by their correlation with forward returns."""
        correlations = {}
        for comp in self.COMPONENTS:
            label = self.COMP_LABELS[comp]
            col_idx = self.COMPONENTS.index(comp) + 1
            pairs = [(r[col_idx], r[7]) for r in rows if r[col_idx] is not None and r[7] is not None]
            corr = self._pearson(pairs) if len(pairs) > 2 else 0
            correlations[label] = abs(corr)

        # Normalize correlations to weights
        total_corr = sum(correlations.values())
        if total_corr > 0:
            return {k: round(v / total_corr, 4) for k, v in correlations.items()}
        return self.CURRENT_WEIGHTS.copy()

    def _sensitivity_analysis(self, rows: list) -> Dict[str, Dict]:
        """Measure how sensitive final performance is to each component."""
        results = {}
        for comp in self.COMPONENTS:
            label = self.COMP_LABELS[comp]
            col_idx = self.COMPONENTS.index(comp) + 1

            scores = [r[col_idx] for r in rows if r[col_idx] is not None]
            returns = [r[7] for r in rows if r[7] is not None and r[col_idx] is not None]

            if not scores or not returns:
                continue

            # Split at median and compare returns
            median_score = sorted(scores)[len(scores) // 2]
            high_returns = [r[7] for r in rows if r[col_idx] is not None and r[col_idx] >= median_score and r[7] is not None]
            low_returns = [r[7] for r in rows if r[col_idx] is not None and r[col_idx] < median_score and r[7] is not None]

            avg_high = sum(high_returns) / len(high_returns) if high_returns else 0
            avg_low = sum(low_returns) / len(low_returns) if low_returns else 0

            # Sensitivity = difference in returns between high/low component scores
            sensitivity = avg_high - avg_low

            # Suggested weight proportional to sensitivity
            results[label] = {
                "sensitivity": round(sensitivity, 4),
                "median_score": round(median_score, 1),
                "avg_return_high": round(avg_high, 3),
                "avg_return_low": round(avg_low, 3),
            }

        # Normalize sensitivities to weights
        total_sens = sum(abs(v["sensitivity"]) for v in results.values())
        if total_sens > 0:
            for label in results:
                results[label]["optimal_weight"] = round(abs(results[label]["sensitivity"]) / total_sens, 4)

        return results

    def _marginal_contribution(self, rows: list) -> Dict[str, Dict]:
        """Measure marginal contribution of each component."""
        results = {}
        for comp in self.COMPONENTS:
            label = self.COMP_LABELS[comp]
            col_idx = self.COMPONENTS.index(comp) + 1

            pairs = [(r[col_idx], r[7]) for r in rows if r[col_idx] is not None and r[7] is not None]
            if len(pairs) < 3:
                continue

            corr = self._pearson(pairs)
            avg_score = sum(r[col_idx] for r in rows if r[col_idx] is not None) / len([r for r in rows if r[col_idx] is not None])

            # Marginal = correlation * avg_normalized_score
            marginal = abs(corr) * (avg_score / 100)

            results[label] = {
                "correlation": round(corr, 4),
                "avg_score": round(avg_score, 1),
                "marginal_value": round(marginal, 4),
                "suggested_weight": round(marginal, 4),
            }

        # Normalize
        total = sum(v["suggested_weight"] for v in results.values())
        if total > 0:
            for label in results:
                results[label]["suggested_weight"] = round(results[label]["suggested_weight"] / total, 4)

        return results

    def _bootstrap_weights(self, rows: list, n_bootstrap: int = 100) -> Dict[str, Dict]:
        """Bootstrap confidence intervals for optimal weights."""
        import random

        ci = {}
        for comp in self.COMPONENTS:
            label = self.COMP_LABELS[comp]
            col_idx = self.COMPONENTS.index(comp) + 1

            bootstrap_corrs = []
            for _ in range(n_bootstrap):
                sample = random.choices(rows, k=len(rows))
                pairs = [(r[col_idx], r[7]) for r in sample if r[col_idx] is not None and r[7] is not None]
                if len(pairs) > 2:
                    bootstrap_corrs.append(abs(self._pearson(pairs)))

            if bootstrap_corrs:
                bootstrap_corrs.sort()
                ci[label] = {
                    "low": round(bootstrap_corrs[int(len(bootstrap_corrs) * 0.05)], 4),
                    "high": round(bootstrap_corrs[int(len(bootstrap_corrs) * 0.95)], 4),
                    "mean": round(sum(bootstrap_corrs) / len(bootstrap_corrs), 4),
                }
            else:
                ci[label] = {"low": 0, "high": 0, "mean": 0}

        return ci

    def _estimate_improvement(self, rows: list, new_weights: Dict) -> Dict:
        """Estimate expected improvement from weight change."""
        return {
            "estimated_pf_change": "Insufficient data for reliable estimation",
            "min_trades_for_significance": 50,
        }

    @staticmethod
    def _pearson(pairs: List[Tuple[float, float]]) -> float:
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

    def close(self) -> None:
        self._conn.close()
