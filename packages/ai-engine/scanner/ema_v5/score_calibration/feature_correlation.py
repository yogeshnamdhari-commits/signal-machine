"""
Feature Correlation Matrix — Phase 10.

Calculates correlation between all scoring components and forward returns.
Identifies redundant variables, predictive variables, and low-information variables.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger


class FeatureCorrelation:
    """Computes feature correlation matrix and identifies predictive value."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"

    FEATURES = [
        "confidence", "trend_score", "pullback_score", "candle_score",
        "volume_score", "regime_score", "atr_14", "volume_ratio",
    ]
    FEATURE_LABELS = {
        "confidence": "confidence",
        "trend_score": "trend",
        "pullback_score": "pullback",
        "candle_score": "candle",
        "volume_score": "volume",
        "regime_score": "regime",
        "atr_14": "atr",
        "volume_ratio": "vol_ratio",
    }

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or self.DB_PATH
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.row_factory = sqlite3.Row

    def compute_matrix(self) -> Dict:
        """Compute full correlation matrix for all features."""
        cur = self._conn.cursor()
        cols = ", ".join(self.FEATURES)
        cur.execute(f"""
            SELECT {cols}, return_pct
            FROM candidates WHERE outcome_tracked = 1
        """)
        rows = cur.fetchall()

        if len(rows) < 5:
            return {
                "status": "insufficient_data",
                "min_required": 5,
                "current": len(rows),
            }

        # Build data arrays
        data = {}
        for feat in self.FEATURES:
            data[feat] = [r[feat] for r in rows if r[feat] is not None]
        data["return_pct"] = [r[-1] for r in rows if r[-1] is not None]

        # Correlation matrix
        all_features = self.FEATURES + ["return_pct"]
        matrix = {}
        for f1 in all_features:
            matrix[f1] = {}
            for f2 in all_features:
                pairs = [(data[f1][i], data[f2][i]) for i in range(min(len(data.get(f1, [])), len(data.get(f2, [])))) if data[f1][i] is not None and data[f2][i] is not None]
                corr = self._pearson(pairs) if len(pairs) > 2 else 0
                matrix[f1][self.FEATURE_LABELS.get(f2, f2)] = round(corr, 4)

        # Classify features
        classifications = self._classify_features(data, rows)

        return {
            "status": "complete",
            "sample_size": len(rows),
            "matrix": {
                self.FEATURE_LABELS.get(f, f): row
                for f, row in matrix.items()
            },
            "classifications": classifications,
        }

    def _classify_features(self, data: Dict, rows: list) -> List[Dict]:
        """Classify each feature as redundant, predictive, or low-information."""
        results = []
        returns = data.get("return_pct", [])

        for feat in self.FEATURES:
            label = self.FEATURE_LABELS[feat]
            feat_data = data.get(feat, [])

            if not feat_data or not returns:
                continue

            # Correlation with return
            pairs = [(feat_data[i], returns[i]) for i in range(min(len(feat_data), len(returns)))]
            corr_return = self._pearson(pairs) if len(pairs) > 2 else 0

            # Correlation with other features (avg redundancy)
            other_corrs = []
            for other_feat in self.FEATURES:
                if other_feat == feat:
                    continue
                other_data = data.get(other_feat, [])
                if not other_data:
                    continue
                pairs = [(feat_data[i], other_data[i]) for i in range(min(len(feat_data), len(other_data)))]
                corr = self._pearson(pairs) if len(pairs) > 2 else 0
                other_corrs.append(abs(corr))

            avg_redundancy = sum(other_corrs) / len(other_corrs) if other_corrs else 0
            max_redundancy = max(other_corrs) if other_corrs else 0

            # Classification
            if avg_redundancy > 0.7:
                classification = "redundant"
            elif abs(corr_return) > 0.1:
                classification = "predictive"
            else:
                classification = "low_information"

            results.append({
                "feature": label,
                "correlation_with_return": round(corr_return, 4),
                "avg_redundancy": round(avg_redundancy, 4),
                "max_redundancy": round(max_redundancy, 4),
                "classification": classification,
                "abs_correlation": round(abs(corr_return), 4),
            })

        results.sort(key=lambda x: abs(x["correlation_with_return"]), reverse=True)
        return results

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
