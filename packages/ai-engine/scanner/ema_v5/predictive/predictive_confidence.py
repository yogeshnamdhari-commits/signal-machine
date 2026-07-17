"""
EMA V5 Predictive Confidence Model — Main entry point.

This module provides the predictive confidence scoring system that
replaces the static weighted score with a data-driven model.

Key features:
  - Feature engineering from candidate data
  - Weight optimization from historical trades
  - Self-calibration and performance metrics
  - Forward validation tracking
  - Production analytics

Usage:
    model = PredictiveConfidence()
    result = model.score(candidate)
    # result = {
    #     "confidence": 85.3,
    #     "passed": True,
    #     "breakdown": {...},
    #     "audit": {...},
    #     "features": {...},
    #     "edge_score": 0.15,
    # }
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger

from .feature_engineering import FeatureEngineer
from .weight_optimizer import WeightOptimizer
from .self_calibrator import SelfCalibrator
from .forward_validator import ForwardValidator
from .performance_analytics import PerformanceAnalytics


class PredictiveConfidence:
    """Predictive confidence scoring model."""

    def __init__(self) -> None:
        self.feature_engineer = FeatureEngineer()
        self.weight_optimizer = WeightOptimizer()
        self.calibrator = SelfCalibrator()
        self.validator = ForwardValidator()
        self.analytics = PerformanceAnalytics()

        # Load weights from DB if available
        self.weight_optimizer.load_from_db()

        # Thresholds
        self.min_confidence = 70.0
        self.min_samples_for_optimization = 100

        # State
        self._weights: Optional[Dict[str, float]] = None
        self._last_optimization: float = 0
        self._optimization_interval: float = 86400  # Re-optimize daily

        logger.info("Predictive Confidence Model initialized")

    def score(self, candidate: Dict) -> Dict:
        """Score a candidate using the predictive model.

        Args:
            candidate: Dictionary containing candidate data

        Returns:
            Dictionary with confidence score and diagnostics
        """
        # Extract features
        features = self.feature_engineer.extract_features(candidate)

        # Get weights
        weights = self._get_weights()

        # Compute raw score
        raw_score = sum(features.get(name, 0) * weights.get(name, 0) for name in features)

        # Normalize to [0, 100] range
        confidence = min(100, max(0, raw_score * 100))

        passed = confidence >= self.min_confidence

        # Compute feature contributions
        contributions = {}
        for name in features:
            contributions[name] = {
                "value": round(features[name], 4),
                "weight": round(weights.get(name, 0), 4),
                "contribution": round(features[name] * weights.get(name, 0), 4),
            }

        # Compute edge score (simplified)
        edge_score = self._compute_edge_score(candidate, confidence)

        # Build breakdown
        breakdown = {
            "directional": contributions.get("is_buy", {}).get("contribution", 0),
            "ema_structure": sum(contributions.get(k, {}).get("contribution", 0) for k in ["ema_chain_alignment", "ema20_slope", "ema50_slope", "ema_distance_from_200", "ema_distance_from_144", "ema_compression"]),
            "trend": sum(contributions.get(k, {}).get("contribution", 0) for k in ["trend_score", "trend_direction", "trend_persistence"]),
            "pullback": sum(contributions.get(k, {}).get("contribution", 0) for k in ["pullback_detected", "pullback_depth", "pullback_quality"]),
            "candle": sum(contributions.get(k, {}).get("contribution", 0) for k in ["candle_pattern_quality", "body_ratio", "wick_ratio", "upper_wick_ratio"]),
            "volume": sum(contributions.get(k, {}).get("contribution", 0) for k in ["volume_ratio", "volume_expanding", "volume_surge", "volume_score"]),
            "volatility": sum(contributions.get(k, {}).get("contribution", 0) for k in ["atr_normalized", "volatility_regime"]),
            "risk_reward": sum(contributions.get(k, {}).get("contribution", 0) for k in ["rr_available", "sl_distance_pct"]),
            "regime": contributions.get("regime_strength", {}).get("contribution", 0),
        }

        # Build audit trail
        audit = {
            "model": "predictive_v1",
            "raw_score": round(raw_score, 4),
            "confidence": round(confidence, 1),
            "threshold": self.min_confidence,
            "passed": passed,
            "edge_score": round(edge_score, 4),
            "feature_count": len(features),
            "weight_source": "optimized" if self._weights else "default",
            "contributions": contributions,
        }

        return {
            "confidence": round(confidence, 1),
            "passed": passed,
            "breakdown": breakdown,
            "audit": audit,
            "features": features,
            "edge_score": round(edge_score, 4),
            "reason": f"conf={confidence:.1f}_min={self.min_confidence}_edge={edge_score:.3f}_{'PASS' if passed else 'FAIL'}",
        }

    def _get_weights(self) -> Dict[str, float]:
        """Get current weights, optimizing if needed."""
        now = time.time()

        # Check if we need to re-optimize
        if self._weights is None or (now - self._last_optimization) > self._optimization_interval:
            self._try_optimize()

        return self._weights or self.feature_engineer.get_feature_names()

    def _try_optimize(self) -> bool:
        """Try to optimize weights from historical data."""
        try:
            if not self.weight_optimizer.DB_PATH.exists():
                return False

            conn = sqlite3.connect(str(self.weight_optimizer.DB_PATH))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("""
                SELECT * FROM candidates 
                WHERE outcome_tracked=1 AND return_pct IS NOT NULL
                ORDER BY timestamp
            """)
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()

            if len(rows) < self.min_samples_for_optimization:
                logger.debug("Insufficient samples for optimization: {}", len(rows))
                return False

            # Extract features and returns
            candidates = []
            returns = []
            for row in rows:
                try:
                    features = self.feature_engineer.extract_features(row)
                    candidates.append(row)
                    returns.append(row['return_pct'])
                except Exception:
                    continue

            if len(candidates) < self.min_samples_for_optimization:
                return False

            returns_array = np.array(returns)

            # Optimize weights
            self._weights = self.weight_optimizer.optimize_weights(
                candidates, returns_array, method="combined"
            )
            self._last_optimization = time.time()

            # Save to DB
            self.weight_optimizer.save_to_db()

            logger.info("Weight optimization completed: {} samples", len(candidates))
            return True

        except Exception as e:
            logger.debug("Weight optimization failed: {}", e)
            return False

    def _compute_edge_score(self, candidate: Dict, confidence: float) -> float:
        """Compute edge score for a candidate."""
        # Simplified edge score based on confidence and historical performance
        # In production, this would use more sophisticated metrics

        # Base edge from confidence
        base_edge = (confidence - 50) / 100  # Range: -0.5 to 0.5

        # Adjust for directional bias (BUY signals have positive edge)
        direction_bonus = 0.05 if candidate.get("direction") == "BUY" else -0.05

        return base_edge + direction_bonus

    def add_trade(self, trade: Dict) -> Optional[Dict]:
        """Add a completed trade for forward validation.

        Args:
            trade: Dictionary with trade outcome data

        Returns:
            Milestone validation result if reached, None otherwise
        """
        return self.validator.add_trade(trade)

    def get_validation_status(self) -> Dict:
        """Get current validation status."""
        return self.validator.get_current_stats()

    def get_analytics(self, force: bool = False) -> Dict:
        """Get comprehensive analytics."""
        return self.analytics.get_live_analytics(force=force)

    def get_weights(self) -> Dict[str, float]:
        """Get current model weights."""
        return self._weights or {}

    def get_feature_names(self) -> List[str]:
        """Get list of feature names."""
        return self.feature_engineer.get_feature_names()
