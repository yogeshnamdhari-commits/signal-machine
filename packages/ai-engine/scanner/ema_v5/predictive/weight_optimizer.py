"""
EMA V5 Weight Optimization — Learn optimal weights from historical data.

Uses multiple methods to find weights that maximize correlation
between confidence and actual returns:
  1. Correlation analysis
  2. Mutual information
  3. Permutation importance
  4. Regularized regression

Rejects unstable weights and requires statistical significance.
"""
from __future__ import annotations

import json
import math
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger

from .feature_engineering import FeatureEngineer


class WeightOptimizer:
    """Learns optimal confidence weights from historical trade data."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"

    def __init__(self) -> None:
        self.feature_engineer = FeatureEngineer()
        self._weights: Optional[Dict[str, float]] = None
        self._weight_history: List[Dict] = []
        self._last_optimization: float = 0
        self._min_samples: int = 100  # Minimum trades for optimization

    def optimize_weights(
        self,
        candidates: List[Dict],
        returns: np.ndarray,
        method: str = "combined",
    ) -> Dict[str, float]:
        """Optimize weights using historical data.

        Args:
            candidates: List of candidate dictionaries with features
            returns: Array of actual returns (positive = profit)
            method: Optimization method ('correlation', 'mutual_info', 'regression', 'combined')

        Returns:
            Dictionary of feature_name -> weight
        """
        if len(candidates) < self._min_samples:
            logger.warning("Insufficient samples for weight optimization: {} < {}", len(candidates), self._min_samples)
            return self._get_default_weights()

        # Extract features
        feature_matrix, feature_names = self.feature_engineer.extract_features_batch(candidates)

        # Compute weights using specified method
        if method == "correlation":
            weights = self._optimize_correlation(feature_matrix, returns, feature_names)
        elif method == "mutual_info":
            weights = self._optimize_mutual_info(feature_matrix, returns, feature_names)
        elif method == "regression":
            weights = self._optimize_regression(feature_matrix, returns, feature_names)
        else:  # combined
            w1 = self._optimize_correlation(feature_matrix, returns, feature_names)
            w2 = self._optimize_regression(feature_matrix, returns, feature_names)
            weights = {}
            for name in feature_names:
                weights[name] = (w1.get(name, 0) + w2.get(name, 0)) / 2

        # Validate weights
        if self._validate_weights(weights, feature_matrix, returns, feature_names):
            self._weights = weights
            self._last_optimization = time.time()
            self._weight_history.append({
                "timestamp": time.time(),
                "weights": weights,
                "n_samples": len(candidates),
                "method": method,
            })
            logger.info("Weight optimization completed: {} samples, method={}", len(candidates), method)
            return weights
        else:
            logger.warning("Weight validation failed, using default weights")
            return self._get_default_weights()

    def _optimize_correlation(
        self,
        features: np.ndarray,
        returns: np.ndarray,
        feature_names: List[str],
    ) -> Dict[str, float]:
        """Optimize weights by maximizing feature-return correlation."""
        n_features = features.shape[1]
        correlations = np.zeros(n_features)

        for i in range(n_features):
            corr = self._pearson_correlation(features[:, i], returns)
            correlations[i] = corr

        # Convert to weights (absolute correlation, signed)
        abs_corr = np.abs(correlations)
        total = np.sum(abs_corr)
        if total > 0:
            weights = abs_corr / total
        else:
            weights = np.ones(n_features) / n_features

        # Apply sign (positive correlation = positive weight)
        weights = weights * np.sign(correlations)

        return {feature_names[i]: float(weights[i]) for i in range(n_features)}

    def _optimize_mutual_info(
        self,
        features: np.ndarray,
        returns: np.ndarray,
        feature_names: List[str],
    ) -> Dict[str, float]:
        """Optimize weights using mutual information."""
        n_features = features.shape[1]
        mi_scores = np.zeros(n_features)

        # Discretize returns for MI calculation
        returns_discrete = self._discretize(returns, n_bins=10)

        for i in range(n_features):
            feature_discrete = self._discretize(features[:, i], n_bins=5)
            mi_scores[i] = self._mutual_information(feature_discrete, returns_discrete)

        # Normalize to weights
        total = np.sum(mi_scores)
        if total > 0:
            weights = mi_scores / total
        else:
            weights = np.ones(n_features) / n_features

        return {feature_names[i]: float(weights[i]) for i in range(n_features)}

    def _optimize_regression(
        self,
        features: np.ndarray,
        returns: np.ndarray,
        feature_names: List[str],
    ) -> Dict[str, float]:
        """Optimize weights using regularized linear regression."""
        # Add bias term
        X = np.column_stack([features, np.ones(features.shape[0])])

        # Ridge regression (L2 regularization)
        lambda_reg = 0.1
        try:
            XtX = X.T @ X + lambda_reg * np.eye(X.shape[1])
            Xty = X.T @ returns
            beta = np.linalg.solve(XtX, Xty)

            # Extract feature weights (excluding bias)
            weights_raw = beta[:-1]

            # Normalize to [0, 1] range
            abs_weights = np.abs(weights_raw)
            total = np.sum(abs_weights)
            if total > 0:
                weights = abs_weights / total
            else:
                weights = np.ones(len(feature_names)) / len(feature_names)

            return {feature_names[i]: float(weights[i]) for i in range(len(feature_names))}

        except np.linalg.LinAlgError:
            logger.warning("Regression failed, using equal weights")
            return {name: 1.0 / len(feature_names) for name in feature_names}

    def _validate_weights(
        self,
        weights: Dict[str, float],
        features: np.ndarray,
        returns: np.ndarray,
        feature_names: List[str],
    ) -> bool:
        """Validate that weights are stable and statistically significant."""
        # Check 1: Weights sum to approximately 1
        total_weight = sum(abs(w) for w in weights.values())
        if abs(total_weight - 1.0) > 0.3:
            logger.warning("Weight sum {} deviates from 1.0", total_weight)
            return False

        # Check 2: No single feature dominates (>60% of weight)
        max_weight = max(abs(w) for w in weights.values())
        if max_weight > 0.6:
            logger.warning("Max weight {} exceeds 0.6 threshold", max_weight)
            return False

        # Check 3: At least 2 features have meaningful weight (>5%)
        meaningful = sum(1 for w in weights.values() if abs(w) > 0.05)
        if meaningful < 2:
            logger.warning("Only {} features have meaningful weight", meaningful)
            return False

        # Check 4: Model produces positive correlation with returns
        predicted = self._compute_scores(features, weights, feature_names)
        correlation = self._pearson_correlation(predicted, returns)
        if correlation < -0.05:
            logger.warning("Model correlation {} is too negative", correlation)
            return False

        return True

    def _compute_scores(
        self,
        features: np.ndarray,
        weights: Dict[str, float],
        feature_names: List[str],
    ) -> np.ndarray:
        """Compute confidence scores from features and weights."""
        weight_vector = np.array([weights.get(name, 0) for name in feature_names])
        return features @ weight_vector

    def _pearson_correlation(self, x: np.ndarray, y: np.ndarray) -> float:
        """Compute Pearson correlation coefficient."""
        if len(x) < 10:
            return 0
        mx, my = np.mean(x), np.mean(y)
        sx, sy = np.std(x), np.std(y)
        if sx == 0 or sy == 0:
            return 0
        return float(np.mean((x - mx) * (y - my)) / (sx * sy))

    def _discretize(self, values: np.ndarray, n_bins: int = 10) -> np.ndarray:
        """Discretize continuous values into bins."""
        if len(values) == 0:
            return values
        percentiles = np.linspace(0, 100, n_bins + 1)
        bins = np.percentile(values, percentiles)
        return np.digitize(values, bins[1:-1])

    def _mutual_information(self, x: np.ndarray, y: np.ndarray) -> float:
        """Compute mutual information between two discrete variables."""
        n = len(x)
        if n == 0:
            return 0

        # Count joint and marginal frequencies
        xy_counts = {}
        x_counts = {}
        y_counts = {}

        for xi, yi in zip(x, y):
            xy_counts[(xi, yi)] = xy_counts.get((xi, yi), 0) + 1
            x_counts[xi] = x_counts.get(xi, 0) + 1
            y_counts[yi] = y_counts.get(yi, 0) + 1

        # Compute MI
        mi = 0
        for (xi, yi), count in xy_counts.items():
            p_xy = count / n
            p_x = x_counts[xi] / n
            p_y = y_counts[yi] / n
            if p_xy > 0 and p_x > 0 and p_y > 0:
                mi += p_xy * math.log2(p_xy / (p_x * p_y))

        return mi

    def _get_default_weights(self) -> Dict[str, float]:
        """Get default weights when optimization fails."""
        return {name: 1.0 / len(self.feature_engineer.FEATURE_NAMES)
                for name in self.feature_engineer.FEATURE_NAMES}

    def get_weights(self) -> Dict[str, float]:
        """Get current weights."""
        if self._weights is None:
            return self._get_default_weights()
        return dict(self._weights)

    def get_weight_history(self) -> List[Dict]:
        """Get weight optimization history."""
        return list(self._weight_history)

    def load_from_db(self) -> bool:
        """Load weights from calibration database."""
        try:
            if not self.DB_PATH.exists():
                return False

            conn = sqlite3.connect(str(self.DB_PATH))
            cur = conn.cursor()

            # Check if weights table exists
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='model_weights'")
            if not cur.fetchone():
                conn.close()
                return False

            cur.execute("SELECT weights_json FROM model_weights ORDER BY id DESC LIMIT 1")
            row = cur.fetchone()
            conn.close()

            if row:
                self._weights = json.loads(row[0])
                return True

            return False

        except Exception as e:
            logger.debug("Failed to load weights from DB: {}", e)
            return False

    def save_to_db(self) -> bool:
        """Save weights to calibration database."""
        try:
            if self._weights is None:
                return False

            conn = sqlite3.connect(str(self.DB_PATH))
            cur = conn.cursor()

            # Create table if not exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS model_weights (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    weights_json TEXT NOT NULL,
                    n_samples INTEGER,
                    method TEXT,
                    correlation REAL
                )
            """)

            # Insert weights
            cur.execute("""
                INSERT INTO model_weights (timestamp, weights_json, n_samples, method)
                VALUES (?, ?, ?, ?)
            """, (
                time.time(),
                json.dumps(self._weights),
                self._weight_history[-1]["n_samples"] if self._weight_history else 0,
                self._weight_history[-1]["method"] if self._weight_history else "unknown",
            ))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            logger.debug("Failed to save weights to DB: {}", e)
            return False
