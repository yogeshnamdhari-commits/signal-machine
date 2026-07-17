"""
Probabilistic Path Predictor — Predict outcome distributions, not point estimates.

Per Executive Assessment v11:
    "Instead of predicting Expected R = 0.43, predict a distribution:

         Outcome    Probability
         Loss       38%
         1R         26%
         2R         20%
         3R         10%
         5R+        6%

     This is much more informative for capital allocation."

Key Innovation:
    v15 predicted: Point estimates (Expected R = 0.43)
    v16 predicts: Probability distributions (Loss=38%, 1R=26%, 2R=20%, ...)

    This allows:
        - Kelly Criterion sizing based on true edge
        - Risk-adjusted position sizing
        - Better capital allocation decisions
        - More accurate expected value calculations

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


@dataclass
class OutcomeBucket:
    """A single outcome bucket in the distribution."""
    label: str = ""           # e.g., "Loss", "1R", "2R", "3R", "5R+"
    min_r: float = 0.0        # Minimum R-multiple for this bucket
    max_r: float = 0.0        # Maximum R-multiple for this bucket
    probability: float = 0.0  # Probability of this outcome (0-1)
    expected_r: float = 0.0   # Expected R within this bucket

    def to_dict(self) -> Dict:
        return {
            "label": self.label,
            "min_r": round(self.min_r, 2),
            "max_r": round(self.max_r, 2),
            "probability": round(self.probability, 3),
            "expected_r": round(self.expected_r, 3),
        }


@dataclass
class ProbabilisticPrediction:
    """Probabilistic prediction of trade outcome distribution."""
    symbol: str = ""
    side: str = ""

    # Distribution
    outcome_distribution: List[OutcomeBucket] = field(default_factory=list)

    # Summary statistics
    expected_r: float = 0.0          # Weighted average R
    median_r: float = 0.0           # Median outcome
    mode_r: float = 0.0             # Most likely outcome
    variance_r: float = 0.0         # Variance of outcomes
    std_dev_r: float = 0.0          # Standard deviation

    # Risk metrics
    prob_profit: float = 0.0        # Probability of any profit
    prob_loss: float = 0.0          # Probability of any loss
    prob_large_profit: float = 0.0  # Probability of >3R
    prob_large_loss: float = 0.0    # Probability of <-2R
    kelly_fraction: float = 0.0     # Kelly-optimal bet size

    # Expected value components
    gross_profit_ev: float = 0.0    # Expected profit (from winning buckets)
    gross_loss_ev: float = 0.0      # Expected loss (from losing buckets)
    net_ev: float = 0.0             # Expected value (profit - loss)
    edge: float = 0.0               # Edge = prob_profit × avg_win - prob_loss × avg_loss

    # Confidence
    distribution_confidence: float = 0.0  # 0-100, confidence in distribution
    sample_size: int = 0            # Number of historical trades used

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "distribution": [b.to_dict() for b in self.outcome_distribution],
            "summary": {
                "expected_r": round(self.expected_r, 3),
                "median_r": round(self.median_r, 3),
                "mode_r": round(self.mode_r, 3),
                "variance": round(self.variance_r, 3),
                "std_dev": round(self.std_dev_r, 3),
            },
            "risk": {
                "prob_profit": round(self.prob_profit, 3),
                "prob_loss": round(self.prob_loss, 3),
                "prob_large_profit": round(self.prob_large_profit, 3),
                "prob_large_loss": round(self.prob_large_loss, 3),
                "kelly_fraction": round(self.kelly_fraction, 3),
            },
            "expected_value": {
                "gross_profit": round(self.gross_profit_ev, 3),
                "gross_loss": round(self.gross_loss_ev, 3),
                "net_ev": round(self.net_ev, 3),
                "edge": round(self.edge, 3),
            },
            "confidence": round(self.distribution_confidence, 1),
            "sample_size": self.sample_size,
        }


class ProbabilisticPathPredictor:
    """
    Predicts outcome distributions instead of point estimates.

    Per Executive Assessment v11:
        "Predict a distribution. This is much more informative
         for capital allocation."

    This engine:
        1. Builds outcome distribution from historical data
        2. Calculates probability for each outcome bucket
        3. Estimates expected value from distribution
        4. Computes Kelly fraction for optimal sizing
        5. Provides confidence in prediction

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0
        self._symbol_distributions: Dict[str, List[float]] = {}

    def _ensure_loaded(self) -> None:
        """Load trades from DB if stale."""
        if time.time() - self._last_load < 300:
            return
        self._load_trades()

    def _load_trades(self) -> None:
        """Load all closed trades and extract R-multiples."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol, side, realized_r, pnl
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]

            # Build per-symbol distributions
            by_symbol: Dict[str, List[float]] = defaultdict(list)
            for t in self._trades:
                sym = t.get("symbol", "")
                r = t.get("realized_r", 0) or 0
                by_symbol[sym].append(r)

            self._symbol_distributions = dict(by_symbol)
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load probabilistic path predictor: {}", e)

    def predict(
        self,
        symbol: str,
        side: str,
        regime: str = "unknown",
    ) -> ProbabilisticPrediction:
        """
        Predict outcome distribution for a trade.

        Args:
            symbol: Trading symbol
            side: LONG or SHORT
            regime: Market regime

        Returns:
            ProbabilisticPrediction with full distribution
        """
        self._ensure_loaded()

        prediction = ProbabilisticPrediction(symbol=symbol, side=side)

        # ── Get historical R-multiples ──
        all_r = self._symbol_distributions.get(symbol, [])

        if not all_r:
            # Use global distribution as fallback
            all_r = [t.get("realized_r", 0) or 0 for t in self._trades]

        if not all_r:
            # No data — return uniform distribution
            return self._empty_prediction(symbol, side)

        prediction.sample_size = len(all_r)

        # ── Build outcome distribution ──
        buckets = self._build_distribution(all_r)
        prediction.outcome_distribution = buckets

        # ── Calculate summary statistics ──
        prediction.expected_r = sum(b.expected_r * b.probability for b in buckets)
        prediction.variance_r = sum(
            b.probability * (b.expected_r - prediction.expected_r) ** 2
            for b in buckets
        )
        prediction.std_dev_r = math.sqrt(prediction.variance_r)

        # Median and mode
        sorted_buckets = sorted(buckets, key=lambda b: b.expected_r)
        cumulative = 0
        for b in sorted_buckets:
            cumulative += b.probability
            if cumulative >= 0.5:
                prediction.median_r = b.expected_r
                break

        prediction.mode_r = max(buckets, key=lambda b: b.probability).expected_r

        # ── Risk metrics ──
        prediction.prob_profit = sum(b.probability for b in buckets if b.expected_r > 0)
        prediction.prob_loss = sum(b.probability for b in buckets if b.expected_r < 0)
        prediction.prob_large_profit = sum(b.probability for b in buckets if b.expected_r >= 3.0)
        prediction.prob_large_loss = sum(b.probability for b in buckets if b.expected_r <= -2.0)

        # Kelly fraction: f* = (p × b - q) / b
        # where p = prob_profit, q = prob_loss, b = avg_win / avg_loss
        if prediction.prob_loss > 0 and prediction.prob_profit > 0:
            avg_win = sum(b.expected_r * b.probability for b in buckets if b.expected_r > 0) / prediction.prob_profit
            avg_loss = abs(sum(b.expected_r * b.probability for b in buckets if b.expected_r < 0) / prediction.prob_loss)
            if avg_loss > 0:
                payoff_ratio = avg_win / avg_loss
                kelly = (prediction.prob_profit * payoff_ratio - prediction.prob_loss) / payoff_ratio
                prediction.kelly_fraction = max(0, min(0.5, kelly / 2))  # Half-Kelly for safety

        # ── Expected value components ──
        prediction.gross_profit_ev = sum(b.expected_r * b.probability for b in buckets if b.expected_r > 0)
        prediction.gross_loss_ev = abs(sum(b.expected_r * b.probability for b in buckets if b.expected_r < 0))
        prediction.net_ev = prediction.gross_profit_ev - prediction.gross_loss_ev
        prediction.edge = prediction.net_ev

        # ── Confidence ──
        # Higher sample size = higher confidence
        prediction.distribution_confidence = min(100, len(all_r) * 2)

        return prediction

    def _build_distribution(self, all_r: List[float]) -> List[OutcomeBucket]:
        """Build outcome distribution from R-multiples."""
        # Define buckets
        bucket_defs = [
            ("Loss", -10, -0.01),
            ("0R", -0.01, 0.01),
            ("0.5R", 0.01, 0.75),
            ("1R", 0.75, 1.5),
            ("2R", 1.5, 2.5),
            ("3R", 2.5, 4.0),
            ("5R+", 4.0, 20.0),
        ]

        n = len(all_r)
        buckets = []

        for label, min_r, max_r in bucket_defs:
            count = sum(1 for r in all_r if min_r <= r < max_r)
            prob = count / max(1, n)

            # Expected R within bucket (midpoint)
            mid_r = (min_r + max_r) / 2
            # More accurate: use actual values in bucket
            vals = [r for r in all_r if min_r <= r < max_r]
            expected_r = sum(vals) / max(1, len(vals)) if vals else mid_r

            buckets.append(OutcomeBucket(
                label=label,
                min_r=min_r,
                max_r=max_r,
                probability=prob,
                expected_r=expected_r,
            ))

        return buckets

    def _empty_prediction(
        self,
        symbol: str,
        side: str,
    ) -> ProbabilisticPrediction:
        """Return empty prediction when no data available."""
        return ProbabilisticPrediction(
            symbol=symbol,
            side=side,
            outcome_distribution=[
                OutcomeBucket("Unknown", -10, 20, 1.0, 0.0),
            ],
            distribution_confidence=0,
        )

    def get_kelly_size(
        self,
        symbol: str,
        balance: float = 10_000.0,
    ) -> float:
        """Get Kelly-optimal position size in USD."""
        pred = self.predict(symbol, "")
        return balance * pred.kelly_fraction

    def rank_by_expected_value(
        self,
        signals: List[Dict[str, Any]],
    ) -> List[Dict]:
        """
        Rank signals by expected value (not confidence).

        Per Executive Assessment v11:
            "Sort by Expected Value. Not confidence.
             Those are different concepts."
        """
        ranked = []
        for sig in signals:
            pred = self.predict(
                symbol=sig.get("symbol", ""),
                side=sig.get("side", ""),
                regime=sig.get("regime", "unknown"),
            )
            ranked.append({
                "signal": sig,
                "prediction": pred,
                "expected_value": pred.net_ev,
                "kelly_fraction": pred.kelly_fraction,
                "prob_profit": pred.prob_profit,
            })

        # Sort by expected value (highest first)
        ranked.sort(key=lambda x: x["expected_value"], reverse=True)
        return ranked
