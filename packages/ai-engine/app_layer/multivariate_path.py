"""
Multivariate Path Predictor — Predict based on multiple features, not just one.

Per Executive Assessment v12:
    "The probability model appears to be univariate.
     Real markets are path dependent.
     The probability should depend on:
         Entry Quality + Market Regime + Volatility + Liquidity
         + Holding Time + Recent Regime Stability
     Not just one snapshot."

Key Innovation:
    v16 predicted: Distribution from historical trades (univariate)
    v17 predicts: Distribution conditioned on multiple features (multivariate)

    This allows:
        - Different predictions for different market conditions
        - More accurate probability estimates
        - Better capital allocation in varying regimes
        - Regime-aware position sizing

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


@dataclass
class FeatureSet:
    """Features that influence trade outcome."""
    # Market conditions
    regime: str = ""              # trending_bull, trending_bear, range, reversal
    volatility_regime: str = ""   # high, normal, low
    session: str = ""             # asia, london, new_york, off_hours

    # Signal quality
    confidence: float = 0.0       # 0-100
    risk_reward: float = 0.0      # Planned R:R
    institutional_score: float = 0.0  # 0-100

    # Technical context
    atr_pct: float = 0.0          # ATR as % of price
    volume_ratio: float = 0.0     # Current vs average volume
    momentum_score: float = 0.0   # -1 to 1

    def to_dict(self) -> Dict:
        return {
            "regime": self.regime,
            "volatility_regime": self.volatility_regime,
            "session": self.session,
            "confidence": round(self.confidence, 1),
            "risk_reward": round(self.risk_reward, 2),
            "institutional_score": round(self.institutional_score, 1),
            "atr_pct": round(self.atr_pct, 4),
            "volume_ratio": round(self.volume_ratio, 3),
            "momentum_score": round(self.momentum_score, 3),
        }


@dataclass
class ConfidenceInterval:
    """Confidence interval for a prediction."""
    point_estimate: float = 0.0
    lower_bound: float = 0.0      # 95% CI lower
    upper_bound: float = 0.0      # 95% CI upper
    uncertainty: float = 0.0      # Width of CI
    confidence_level: float = 0.95

    def to_dict(self) -> Dict:
        return {
            "point": round(self.point_estimate, 3),
            "lower": round(self.lower_bound, 3),
            "upper": round(self.upper_bound, 3),
            "uncertainty": round(self.uncertainty, 3),
            "level": round(self.confidence_level, 2),
        }


@dataclass
class MultivariatePrediction:
    """Prediction based on multiple features."""
    symbol: str = ""
    side: str = ""
    features: FeatureSet = field(default_factory=FeatureSet)

    # Outcome distribution (conditioned on features)
    outcome_distribution: List[Dict] = field(default_factory=list)

    # Expected value with confidence interval
    expected_r_ci: ConfidenceInterval = field(default_factory=ConfidenceInterval)
    expected_mfe_ci: ConfidenceInterval = field(default_factory=ConfidenceInterval)
    expected_mae_ci: ConfidenceInterval = field(default_factory=ConfidenceInterval)

    # Probabilities
    prob_profit: float = 0.0
    prob_loss: float = 0.0
    prob_reach_3r: float = 0.0

    # Risk metrics
    kelly_fraction: float = 0.0
    conservative_kelly: float = 0.0  # Fraction of Kelly (e.g., 0.25 × Kelly)

    # Feature importance (which features matter most)
    feature_importance: Dict[str, float] = field(default_factory=dict)

    # Confidence
    prediction_confidence: float = 0.0
    sample_size: int = 0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "features": self.features.to_dict(),
            "expected_r": self.expected_r_ci.to_dict(),
            "expected_mfe": self.expected_mfe_ci.to_dict(),
            "expected_mae": self.expected_mae_ci.to_dict(),
            "probabilities": {
                "profit": round(self.prob_profit, 3),
                "loss": round(self.prob_loss, 3),
                "reach_3r": round(self.prob_reach_3r, 3),
            },
            "kelly": {
                "optimal": round(self.kelly_fraction, 4),
                "conservative": round(self.conservative_kelly, 4),
            },
            "feature_importance": {k: round(v, 3) for k, v in self.feature_importance.items()},
            "confidence": round(self.prediction_confidence, 1),
            "sample_size": self.sample_size,
        }


class MultivariatePathPredictor:
    """
    Predicts trade outcomes conditioned on multiple features.

    Per Executive Assessment v12:
        "The probability should depend on Entry Quality + Market Regime
         + Volatility + Liquidity + Holding Time + Recent Regime Stability."

    This engine:
        1. Conditions predictions on multiple features
        2. Provides confidence intervals for predictions
        3. Calculates feature importance
        4. Uses conservative Kelly sizing (fraction of optimal)
        5. Adapts predictions to market regime

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        """Load trades from DB if stale."""
        if time.time() - self._last_load < 300:
            return
        self._load_trades()

    def _load_trades(self) -> None:
        """Load all closed trades."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol, side, realized_r, pnl, mfe_pct, mae_pct,
                       highest_pnl, exit_reason, regime, session,
                       hold_minutes, confidence, institutional_score
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load multivariate path predictor: {}", e)

    def predict(
        self,
        symbol: str,
        side: str,
        features: FeatureSet,
    ) -> MultivariatePrediction:
        """
        Predict trade outcome based on multiple features.

        Args:
            symbol: Trading symbol
            side: LONG or SHORT
            features: Feature set for this trade

        Returns:
            MultivariatePrediction with conditioned distribution
        """
        self._ensure_loaded()

        prediction = MultivariatePrediction(
            symbol=symbol,
            side=side,
            features=features,
        )

        if not self._trades:
            return prediction

        # ── Filter trades by similar features ──
        similar_trades = self._filter_similar_trades(features)
        prediction.sample_size = len(similar_trades)

        if len(similar_trades) < 5:
            # Not enough data — use global distribution with high uncertainty
            similar_trades = self._trades[:100]
            prediction.prediction_confidence = 30
        else:
            prediction.prediction_confidence = min(100, len(similar_trades) * 2)

        # ── Build conditioned distribution ──
        all_r = [t.get("realized_r", 0) or 0 for t in similar_trades]
        all_mfe = [t.get("highest_pnl", 0) or 0 for t in similar_trades if (t.get("highest_pnl", 0) or 0) > 0]
        all_mae = [abs(t.get("mae_pct", 0) or 0) for t in similar_trades if (t.get("mae_pct", 0) or 0) > 0]

        # ── Expected R with confidence interval ──
        if all_r:
            mean_r = sum(all_r) / len(all_r)
            std_r = math.sqrt(sum((r - mean_r) ** 2 for r in all_r) / max(1, len(all_r) - 1))
            se = std_r / math.sqrt(len(all_r))  # Standard error

            prediction.expected_r_ci = ConfidenceInterval(
                point_estimate=mean_r,
                lower_bound=mean_r - 1.96 * se,  # 95% CI
                upper_bound=mean_r + 1.96 * se,
                uncertainty=3.92 * se,  # Width of CI
            )

        # ── Expected MFE with confidence interval ──
        if all_mfe:
            mean_mfe = sum(all_mfe) / len(all_mfe)
            std_mfe = math.sqrt(sum((m - mean_mfe) ** 2 for m in all_mfe) / max(1, len(all_mfe) - 1))
            se_mfe = std_mfe / math.sqrt(len(all_mfe))

            prediction.expected_mfe_ci = ConfidenceInterval(
                point_estimate=mean_mfe,
                lower_bound=mean_mfe - 1.96 * se_mfe,
                upper_bound=mean_mfe + 1.96 * se_mfe,
                uncertainty=3.92 * se_mfe,
            )

        # ── Expected MAE with confidence interval ──
        if all_mae:
            mean_mae = sum(all_mae) / len(all_mae)
            std_mae = math.sqrt(sum((m - mean_mae) ** 2 for m in all_mae) / max(1, len(all_mae) - 1))
            se_mae = std_mae / math.sqrt(len(all_mae))

            prediction.expected_mae_ci = ConfidenceInterval(
                point_estimate=mean_mae,
                lower_bound=mean_mae - 1.96 * se_mae,
                upper_bound=mean_mae + 1.96 * se_mae,
                uncertainty=3.92 * se_mae,
            )

        # ── Probabilities ──
        prediction.prob_profit = sum(1 for r in all_r if r > 0) / max(1, len(all_r))
        prediction.prob_loss = 1 - prediction.prob_profit
        prediction.prob_reach_3r = sum(1 for r in all_r if r >= 3.0) / max(1, len(all_r))

        # ── Kelly sizing (conservative) ──
        if prediction.prob_loss > 0 and prediction.prob_profit > 0:
            avg_win = sum(r for r in all_r if r > 0) / max(1, sum(1 for r in all_r if r > 0))
            avg_loss = abs(sum(r for r in all_r if r < 0) / max(1, sum(1 for r in all_r if r < 0)))

            if avg_loss > 0:
                payoff_ratio = avg_win / avg_loss
                kelly = (prediction.prob_profit * payoff_ratio - prediction.prob_loss) / payoff_ratio
                prediction.kelly_fraction = max(0, kelly)
                # Conservative: use 25% of Kelly
                prediction.conservative_kelly = max(0, kelly * 0.25)

        # ── Feature importance (simplified) ──
        prediction.feature_importance = self._estimate_feature_importance(features)

        return prediction

    def _filter_similar_trades(self, features: FeatureSet) -> List[Dict]:
        """Filter trades by similar features."""
        similar = []

        for t in self._trades:
            score = 0
            total = 0

            # Regime match
            if features.regime and t.get("regime", "") == features.regime:
                score += 1
            total += 1

            # Session match
            if features.session and t.get("session", "") == features.session:
                score += 1
            total += 1

            # Confidence match (within 10 points)
            t_conf = t.get("confidence", 0) or 0
            if abs(features.confidence - t_conf) < 10:
                score += 1
            total += 1

            # Institutional score match (within 10 points)
            t_inst = t.get("institutional_score", 0) or 0
            if abs(features.institutional_score - t_inst) < 10:
                score += 1
            total += 1

            # Require at least 50% match
            if score / max(1, total) >= 0.5:
                similar.append(t)

        return similar

    def _estimate_feature_importance(self, features: FeatureSet) -> Dict[str, float]:
        """Estimate importance of each feature."""
        importance = {}

        # Regime is typically most important
        importance["regime"] = 0.30 if features.regime else 0.10

        # Confidence and institutional score
        importance["confidence"] = 0.20 if features.confidence > 85 else 0.10
        importance["institutional_score"] = 0.20 if features.institutional_score > 85 else 0.10

        # Session
        importance["session"] = 0.15 if features.session in ("london", "new_york") else 0.05

        # Volatility
        importance["volatility"] = 0.10 if features.volatility_regime == "normal" else 0.05

        # Momentum
        importance["momentum"] = 0.10 if abs(features.momentum_score) > 0.3 else 0.05

        # Normalize
        total = sum(importance.values())
        if total > 0:
            importance = {k: v / total for k, v in importance.items()}

        return importance
