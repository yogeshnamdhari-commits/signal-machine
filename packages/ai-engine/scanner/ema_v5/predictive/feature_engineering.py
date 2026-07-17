"""
EMA V5 Feature Engineering Pipeline — Extract predictive features from candidate data.

This module extracts 30+ features from candidate evaluations for use in
the predictive confidence model. Features are designed to capture:
  - Multi-timeframe trend alignment
  - EMA structure and compression
  - Pullback quality and depth
  - Volume dynamics
  - Candle pattern quality
  - Volatility regime
  - Risk/reward characteristics

All features are normalized to [0, 1] range for model training.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger


class FeatureEngineer:
    """Extracts and normalizes predictive features from candidate data."""

    # Feature names in canonical order
    FEATURE_NAMES = [
        # Direction
        "is_buy",
        # EMA structure
        "ema_chain_alignment",
        "ema20_slope",
        "ema50_slope",
        "ema_distance_from_200",
        "ema_distance_from_144",
        "ema_compression",
        # Trend
        "trend_score",
        "trend_direction",
        "trend_persistence",
        # Pullback
        "pullback_detected",
        "pullback_depth",
        "pullback_quality",
        # Candle
        "candle_pattern_quality",
        "body_ratio",
        "wick_ratio",
        "upper_wick_ratio",
        # Volume
        "volume_ratio",
        "volume_expanding",
        "volume_surge",
        "volume_score",
        # Volatility
        "atr_normalized",
        "volatility_regime",
        # Risk/Reward
        "rr_available",
        "sl_distance_pct",
        # Regime
        "regime_strength",
        # Funding
        "has_funding_data",
    ]

    def __init__(self) -> None:
        # Normalization ranges (will be updated from data)
        self._ranges: Dict[str, Tuple[float, float]] = {}

    def extract_features(self, candidate: Dict) -> Dict[str, float]:
        """Extract all features from a candidate dictionary.

        Args:
            candidate: Dictionary containing candidate data with parsed JSON fields

        Returns:
            Dictionary of feature_name -> normalized_value (0-1)
        """
        features = {}

        # Parse JSON fields if they're strings
        ema_data = self._parse_json(candidate.get("ema_data"))
        trend_eval = self._parse_json(candidate.get("trend_eval"))
        pullback_eval = self._parse_json(candidate.get("pullback_eval"))
        candle_eval = self._parse_json(candidate.get("candle_eval"))
        volume_eval = self._parse_json(candidate.get("volume_eval"))
        regime_eval = self._parse_json(candidate.get("regime_eval"))

        # ═══ DIRECTION ═══
        features["is_buy"] = 1.0 if candidate.get("direction") == "BUY" else 0.0

        # ═══ EMA STRUCTURE ═══
        features["ema_chain_alignment"] = 1.0 if trend_eval.get("ema_chain_perfect") else 0.0

        # EMA slopes (normalized to [-1, 1] range)
        features["ema20_slope"] = self._normalize_slope(ema_data.get("ema20_slope", 0))
        features["ema50_slope"] = self._normalize_slope(ema_data.get("ema50_slope", 0))

        # Distance from key EMAs (as % of price)
        last_close = ema_data.get("last_close", 0)
        if last_close > 0:
            ema200 = ema_data.get("ema200", last_close)
            ema144 = ema_data.get("ema144", last_close)
            features["ema_distance_from_200"] = abs(last_close - ema200) / last_close
            features["ema_distance_from_144"] = abs(last_close - ema144) / last_close
        else:
            features["ema_distance_from_200"] = 0
            features["ema_distance_from_144"] = 0

        # EMA compression (distance between EMA20 and EMA50)
        ema20 = ema_data.get("ema20", 0)
        ema50 = ema_data.get("ema50", 0)
        if last_close > 0 and ema50 > 0:
            features["ema_compression"] = abs(ema20 - ema50) / last_close
        else:
            features["ema_compression"] = 0

        # ═══ TREND ═══
        features["trend_score"] = (trend_eval.get("trend_score", 50)) / 100.0
        features["trend_direction"] = 1.0 if trend_eval.get("direction") == "BUY" else 0.0
        features["trend_persistence"] = min(1.0, trend_eval.get("confirmation_bars", 0) / 10.0)

        # ═══ PULLBACK ═══
        features["pullback_detected"] = 1.0 if pullback_eval.get("pullback_detected") else 0.0
        features["pullback_depth"] = min(1.0, pullback_eval.get("pullback_depth_pct", 0) / 5.0)
        features["pullback_quality"] = 1.0 if pullback_eval.get("bounce_confirmed") else 0.0

        # ═══ CANDLE ═══
        features["candle_pattern_quality"] = (candle_eval.get("candle_score", 50)) / 100.0
        diagnostics = candle_eval.get("diagnostics", {})
        features["body_ratio"] = diagnostics.get("body_ratio", 0.5)
        features["wick_ratio"] = min(1.0, diagnostics.get("wick_ratio", 1.0) / 5.0)
        features["upper_wick_ratio"] = min(1.0, diagnostics.get("upper_wick_ratio", 1.0) / 5.0)

        # ═══ VOLUME ═══
        features["volume_ratio"] = min(1.0, volume_eval.get("volume_ratio", 0) / 2.0)
        features["volume_expanding"] = 1.0 if volume_eval.get("volume_expanding") else 0.0
        features["volume_surge"] = 1.0 if volume_eval.get("volume_surge") else 0.0
        features["volume_score"] = (volume_eval.get("volume_score", 50)) / 100.0

        # ═══ VOLATILITY ═══
        atr = ema_data.get("atr_14", 0)
        if last_close > 0:
            features["atr_normalized"] = min(1.0, (atr / last_close) * 100 / 5.0)
        else:
            features["atr_normalized"] = 0.5

        # Volatility regime (high vol = 1, low vol = 0)
        features["volatility_regime"] = min(1.0, features["atr_normalized"] * 2)

        # ═══ RISK/REWARD ═══
        entry_price = candidate.get("entry_price", 0)
        stop_loss = candidate.get("stop_loss", 0)
        take_profit = candidate.get("take_profit", 0)

        if entry_price > 0 and stop_loss > 0:
            sl_dist = abs(entry_price - stop_loss) / entry_price
            features["sl_distance_pct"] = min(1.0, sl_dist * 100 / 5.0)
            if take_profit > 0:
                tp_dist = abs(take_profit - entry_price) / entry_price
                features["rr_available"] = min(1.0, tp_dist / max(sl_dist, 0.001) / 5.0)
            else:
                features["rr_available"] = 0
        else:
            features["sl_distance_pct"] = 0.5
            features["rr_available"] = 0

        # ═══ REGIME ═══
        features["regime_strength"] = 1.0 if regime_eval.get("ema_chain_aligned") else 0.5

        # ═══ FUNDING ═══
        features["has_funding_data"] = 1.0 if candidate.get("funding_rate") is not None else 0.0

        return features

    def extract_features_batch(self, candidates: List[Dict]) -> Tuple[np.ndarray, List[str]]:
        """Extract features for a batch of candidates.

        Returns:
            Tuple of (feature_matrix, feature_names)
        """
        all_features = []
        for candidate in candidates:
            features = self.extract_features(candidate)
            all_features.append([features.get(name, 0) for name in self.FEATURE_NAMES])

        return np.array(all_features), self.FEATURE_NAMES

    def _parse_json(self, value) -> Dict:
        """Parse JSON string to dict, handling already-parsed dicts."""
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return {}
        return {}

    def _normalize_slope(self, slope: float) -> float:
        """Normalize slope to [-1, 1] range."""
        # Typical EMA slopes are in [-0.05, 0.05] range
        return max(-1.0, min(1.0, slope * 20))

    def get_feature_names(self) -> List[str]:
        """Get canonical feature names."""
        return list(self.FEATURE_NAMES)


# Import json at module level
import json
