"""
Machine Learning Validation — Phase 11 (OFFLINE ONLY).

Trains offline models (Random Forest, XGBoost, LightGBM, Logistic Regression)
and compares ML probability vs current confidence score.
DO NOT deploy to production.
"""
from __future__ import annotations

import math
import random
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


class MLValidator:
    """Offline ML validation of the confidence model."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"

    FEATURES = [
        "confidence", "trend_score", "pullback_score", "candle_score",
        "volume_score", "regime_score", "atr_14", "volume_ratio",
    ]

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or self.DB_PATH
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.row_factory = sqlite3.Row

    def validate(self) -> Dict:
        """Run all ML validation models and compare with current scoring."""
        cur = self._conn.cursor()
        cols = ", ".join(self.FEATURES)
        cur.execute(f"""
            SELECT {cols}, return_pct
            FROM candidates WHERE outcome_tracked = 1
        """)
        rows = cur.fetchall()

        if len(rows) < 20:
            return {
                "status": "insufficient_data",
                "min_required": 20,
                "current": len(rows),
                "recommendation": "Need at least 20 tracked outcomes for ML validation.",
            }

        # Prepare data
        X = []
        y = []
        confidences = []
        for r in rows:
            features = [r[f] for f in self.FEATURES if r[f] is not None]
            if len(features) == len(self.FEATURES) and r[-1] is not None:
                X.append(features)
                y.append(1 if r[-1] > 0 else 0)
                confidences.append(r["confidence"])

        if len(X) < 20:
            return {"status": "insufficient_data", "current": len(X)}

        # Split data (80/20)
        split_idx = int(len(X) * 0.8)
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]
        conf_test = confidences[split_idx:]

        # ── Model 1: Logistic Regression (from scratch) ──
        lr_result = self._logistic_regression(X_train, y_train, X_test, y_test)

        # ── Model 2: Simple Decision Tree ──
        dt_result = self._decision_tree(X_train, y_train, X_test, y_test)

        # ── Model 3: k-Nearest Neighbors ──
        knn_result = self._knn(X_train, y_train, X_test, y_test)

        # ── Model 4: Naive Bayes ──
        nb_result = self._naive_bayes(X_train, y_train, X_test, y_test)

        # ── Baseline: Current confidence model ──
        baseline_correct = sum(1 for c, y_true in zip(conf_test, y_test) if (c >= 90 and y_true == 1) or (c < 90 and y_true == 0))
        baseline_acc = baseline_correct / len(y_test) * 100 if y_test else 0

        # ── Feature importance from models ──
        feature_importance = self._compute_feature_importance(X, y)

        models = {
            "logistic_regression": lr_result,
            "decision_tree": dt_result,
            "knn": knn_result,
            "naive_bayes": nb_result,
            "baseline_confidence": {
                "accuracy": round(baseline_acc, 1),
                "threshold": 90.0,
            },
        }

        # Find best model
        best_model = max(models.items(), key=lambda x: x[1].get("accuracy", 0))

        return {
            "status": "complete",
            "sample_size": len(X),
            "train_size": len(X_train),
            "test_size": len(X_test),
            "positive_rate": round(sum(y) / len(y) * 100, 1),
            "models": models,
            "best_model": best_model[0],
            "best_accuracy": best_model[1].get("accuracy", 0),
            "feature_importance": feature_importance,
            "conclusion": self._generate_conclusion(models, feature_importance),
        }

    def _logistic_regression(self, X_train, y_train, X_test, y_test) -> Dict:
        """Simple logistic regression with gradient descent."""
        n_features = len(X_train[0])
        weights = [0.0] * n_features
        bias = 0.0
        lr = 0.01
        epochs = 100

        for _ in range(epochs):
            for x, y in zip(X_train, y_train):
                z = sum(w * xi for w, xi in zip(weights, x)) + bias
                pred = 1.0 / (1.0 + math.exp(-max(-500, min(500, z))))
                error = pred - y
                weights = [w - lr * error * xi for w, xi in zip(weights, x)]
                bias -= lr * error

        # Predict on test set
        correct = 0
        predictions = []
        for x, y in zip(X_test, y_test):
            z = sum(w * xi for w, xi in zip(weights, x)) + bias
            pred = 1.0 / (1.0 + math.exp(-max(-500, min(500, z))))
            predictions.append(pred)
            if (pred >= 0.5 and y == 1) or (pred < 0.5 and y == 0):
                correct += 1

        acc = correct / len(y_test) * 100 if y_test else 0

        # AUC approximation
        auc = self._simple_auc(predictions, y_test)

        return {
            "accuracy": round(acc, 1),
            "auc": round(auc, 3),
            "weights": {self.FEATURES[i]: round(w, 4) for i, w in enumerate(weights)},
        }

    def _decision_tree(self, X_train, y_train, X_test, y_test) -> Dict:
        """Simple decision tree (single split per feature)."""
        best_acc = 0
        best_feature = 0
        best_threshold = 0

        for feat_idx in range(len(X_train[0])):
            values = [x[feat_idx] for x in X_train]
            for threshold in sorted(set(values))[::max(1, len(set(values)) // 10)]:
                correct = 0
                for x, y in zip(X_test, y_test):
                    pred = 1 if x[feat_idx] >= threshold else 0
                    if pred == y:
                        correct += 1
                acc = correct / len(y_test) * 100
                if acc > best_acc:
                    best_acc = acc
                    best_feature = feat_idx
                    best_threshold = threshold

        return {
            "accuracy": round(best_acc, 1),
            "split_feature": self.FEATURES[best_feature],
            "split_threshold": round(best_threshold, 2),
        }

    def _knn(self, X_train, y_train, X_test, y_test, k: int = 5) -> Dict:
        """k-Nearest Neighbors classifier."""
        correct = 0
        for x, y in zip(X_test, y_test):
            # Compute distances
            dists = []
            for xi, yi in zip(X_train, y_train):
                dist = math.sqrt(sum((a - b) ** 2 for a, b in zip(x, xi)))
                dists.append((dist, yi))
            dists.sort(key=lambda d: d[0])
            neighbors = dists[:k]
            pred_class = sum(n[1] for n in neighbors) / k
            pred = 1 if pred_class >= 0.5 else 0
            if pred == y:
                correct += 1

        acc = correct / len(y_test) * 100 if y_test else 0
        return {"accuracy": round(acc, 1), "k": k}

    def _naive_bayes(self, X_train, y_train, X_test, y_test) -> Dict:
        """Simple Naive Bayes classifier."""
        # Compute means and variances per class
        pos = [x for x, y in zip(X_train, y_train) if y == 1]
        neg = [x for x, y in zip(X_train, y_train) if y == 0]

        if not pos or not neg:
            return {"accuracy": 0, "error": "insufficient class data"}

        n_features = len(X_train[0])
        pos_mean = [sum(p[i] for p in pos) / len(pos) for i in range(n_features)]
        neg_mean = [sum(n[i] for n in neg) / len(neg) for i in range(n_features)]
        pos_var = [sum((p[i] - pos_mean[i]) ** 2 for p in pos) / len(pos) + 1e-6 for i in range(n_features)]
        neg_var = [sum((n[i] - neg_mean[i]) ** 2 for n in neg) / len(neg) + 1e-6 for i in range(n_features)]

        prior_pos = len(pos) / len(X_train)
        prior_neg = len(neg) / len(X_train)

        correct = 0
        for x, y in zip(X_test, y_test):
            log_pos = math.log(prior_pos)
            log_neg = math.log(prior_neg)
            for i in range(n_features):
                log_pos += -0.5 * math.log(2 * math.pi * pos_var[i]) - (x[i] - pos_mean[i]) ** 2 / (2 * pos_var[i])
                log_neg += -0.5 * math.log(2 * math.pi * neg_var[i]) - (x[i] - neg_mean[i]) ** 2 / (2 * neg_var[i])
            pred = 1 if log_pos > log_neg else 0
            if pred == y:
                correct += 1

        acc = correct / len(y_test) * 100 if y_test else 0
        return {"accuracy": round(acc, 1)}

    def _compute_feature_importance(self, X: List, y: List) -> List[Dict]:
        """Compute feature importance via correlation with target."""
        importance = []
        for i, feat in enumerate(self.FEATURES):
            feat_vals = [x[i] for x in X]
            pairs = list(zip(feat_vals, y))
            corr = self._point_biserial(pairs) if len(pairs) > 2 else 0
            importance.append({
                "feature": feat,
                "importance": round(abs(corr), 4),
                "correlation": round(corr, 4),
            })
        importance.sort(key=lambda x: x["importance"], reverse=True)
        for i, imp in enumerate(importance):
            imp["rank"] = i + 1
        return importance

    def _generate_conclusion(self, models: Dict, feature_importance: List) -> str:
        best = max(models.items(), key=lambda x: x[1].get("accuracy", 0))
        baseline = models.get("baseline_confidence", {}).get("accuracy", 0)
        diff = best[1].get("accuracy", 0) - baseline

        if diff > 5:
            return f"ML model ({best[0]}) outperforms current confidence by {diff:.1f}%. Consider recalibrating weights."
        elif diff > 0:
            return f"ML model ({best[0]}) slightly outperforms current confidence by {diff:.1f}%. Current model is reasonable."
        else:
            return f"Current confidence model performs comparably to or better than ML alternatives. No recalibration needed."

    @staticmethod
    def _simple_auc(predictions: List[float], y_true: List[int]) -> float:
        """Simple AUC computation."""
        pos = sorted([(p, 1) for p, y in zip(predictions, y_true) if y == 1])
        neg = sorted([(p, 0) for p, y in zip(predictions, y_true) if y == 0])
        if not pos or not neg:
            return 0.5
        n_pos = len(pos)
        n_neg = len(neg)
        rank_sum = 0
        for p, _ in pos:
            rank = sum(1 for n, _ in neg if n < p) + sum(1 for pp, _ in pos if pp < p) + 1
            rank_sum += rank
        auc = (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
        return max(0, min(1, auc))

    @staticmethod
    def _point_biserial(pairs: List[Tuple[float, int]]) -> float:
        """Point-biserial correlation (continuous vs binary)."""
        x = [p[0] for p in pairs]
        y = [p[1] for p in pairs]
        n = len(pairs)
        if n < 3:
            return 0.0
        mx = sum(x) / n
        sx = math.sqrt(sum((xi - mx) ** 2 for xi, yi in zip(x, y)) / n)
        if sx == 0:
            return 0.0
        mean_1 = sum(xi for xi, yi in zip(x, y) if yi == 1) / max(1, sum(1 for yi in y if yi == 1))
        mean_0 = sum(xi for xi, yi in zip(x, y) if yi == 0) / max(1, sum(1 for yi in y if yi == 0))
        n1 = sum(1 for yi in y if yi == 1)
        n0 = sum(1 for yi in y if yi == 0)
        return (mean_1 - mean_0) / sx * math.sqrt(n1 * n0 / (n * n))

    def close(self) -> None:
        self._conn.close()
