"""
Expected Path Predictor — Predict trade evolution before execution.

Per Executive Assessment v10:
    "Currently the engine estimates Entry Quality.
     It does not estimate Trade Evolution.

     Example:
         Trade A: 90% chance to reach 1R
         Trade B: 90% chance to reach 4R
         Those are completely different trades.
         Current scoring treats them similarly.

     Instead predict:
         Expected Path
         Expected Holding Time
         Expected Maximum Favorable Excursion
         Expected Maximum Adverse Excursion

     Then execution can optimize differently."

Key Innovation:
    v14 scored: Entry Quality (how good is the signal?)
    v15 predicts: Trade Evolution (how will the trade develop?)

    This allows:
        - Different exit strategies for different expected paths
        - Better position sizing based on expected evolution
        - More accurate capital allocation
        - Earlier detection of trades unlikely to succeed

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
class TradePathPrediction:
    """Prediction of how a trade will evolve."""
    symbol: str = ""
    side: str = ""

    # Expected outcomes
    expected_r: float = 0.0          # Expected R-multiple at exit
    expected_mfe_r: float = 0.0     # Expected maximum favorable excursion
    expected_mae_r: float = 0.0     # Expected maximum adverse excursion
    expected_hold_minutes: float = 0.0  # Expected holding time

    # Probabilities
    prob_reach_1r: float = 0.0      # Probability of reaching +1R
    prob_reach_2r: float = 0.0      # Probability of reaching +2R
    prob_reach_3r: float = 0.0      # Probability of reaching +3R
    prob_reach_5r: float = 0.0      # Probability of reaching +5R
    prob_stop_loss: float = 0.0     # Probability of hitting stop loss

    # Risk profile
    risk_reward_ratio: float = 0.0  # Expected reward per unit of risk
    expected_capture_pct: float = 0.0  # Expected % of MFE captured
    expected_efficiency: float = 0.0   # Expected exit efficiency (0-100)

    # Classification
    trade_type: str = ""            # SCALP / SWING / TREND / REVERSAL
    confidence: float = 0.0         # Confidence in prediction (0-100)
    recommendation: str = ""        # SIZE_UP / NORMAL / SIZE_DOWN / SKIP

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "expected_r": round(self.expected_r, 3),
            "expected_mfe_r": round(self.expected_mfe_r, 3),
            "expected_mae_r": round(self.expected_mae_r, 3),
            "expected_hold_minutes": round(self.expected_hold_minutes, 1),
            "probabilities": {
                "reach_1r": round(self.prob_reach_1r, 3),
                "reach_2r": round(self.prob_reach_2r, 3),
                "reach_3r": round(self.prob_reach_3r, 3),
                "reach_5r": round(self.prob_reach_5r, 3),
                "stop_loss": round(self.prob_stop_loss, 3),
            },
            "risk_reward_ratio": round(self.risk_reward_ratio, 3),
            "expected_capture_pct": round(self.expected_capture_pct, 1),
            "expected_efficiency": round(self.expected_efficiency, 1),
            "trade_type": self.trade_type,
            "confidence": round(self.confidence, 1),
            "recommendation": self.recommendation,
        }


@dataclass
class PathPredictionReport:
    """Complete path prediction analysis."""
    timestamp: float = 0.0
    predictions: List[TradePathPrediction] = field(default_factory=list)

    # Aggregate
    avg_expected_r: float = 0.0
    avg_expected_mfe_r: float = 0.0
    avg_prob_reach_3r: float = 0.0
    avg_efficiency: float = 0.0

    # Distribution
    scalp_count: int = 0
    swing_count: int = 0
    trend_count: int = 0

    # Recommendations
    size_up_count: int = 0
    normal_count: int = 0
    size_down_count: int = 0
    skip_count: int = 0

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "aggregate": {
                "avg_expected_r": round(self.avg_expected_r, 3),
                "avg_expected_mfe_r": round(self.avg_expected_mfe_r, 3),
                "avg_prob_reach_3r": round(self.avg_prob_reach_3r, 3),
                "avg_efficiency": round(self.avg_efficiency, 1),
            },
            "distribution": {
                "scalp": self.scalp_count,
                "swing": self.swing_count,
                "trend": self.trend_count,
            },
            "recommendations": {
                "size_up": self.size_up_count,
                "normal": self.normal_count,
                "size_down": self.size_down_count,
                "skip": self.skip_count,
            },
            "predictions": [p.to_dict() for p in self.predictions],
        }


class ExpectedPathPredictor:
    """
    Predicts trade evolution before execution.

    Per Executive Assessment v10:
        "Predict Expected Path, Expected Holding Time,
         Expected MFE, Expected MAE. Then execution can
         optimize differently."

    This engine:
        1. Estimates expected R-multiple from historical data
        2. Estimates expected MFE and MAE
        3. Calculates probability of reaching profit targets
        4. Classifies trades by expected evolution
        5. Recommends position sizing based on expected path

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0
        self._symbol_stats: Dict[str, Dict] = {}
        self._regime_stats: Dict[str, Dict] = {}

    def _ensure_loaded(self) -> None:
        """Load trades from DB if stale."""
        if time.time() - self._last_load < 300:
            return
        self._load_trades()

    def _load_trades(self) -> None:
        """Load all closed trades and compute statistics."""
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
            self._compute_statistics()
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load expected path predictor: {}", e)

    def _compute_statistics(self) -> None:
        """Compute per-symbol and per-regime statistics."""
        # By symbol
        by_symbol: Dict[str, List[Dict]] = defaultdict(list)
        for t in self._trades:
            by_symbol[t.get("symbol", "")].append(t)

        for sym, trades in by_symbol.items():
            self._symbol_stats[sym] = self._calc_stats(trades)

        # By regime
        by_regime: Dict[str, List[Dict]] = defaultdict(list)
        for t in self._trades:
            by_regime[t.get("regime", "unknown")].append(t)

        for regime, trades in by_regime.items():
            self._regime_stats[regime] = self._calc_stats(trades)

    def _calc_stats(self, trades: List[Dict]) -> Dict:
        """Calculate statistics for a set of trades."""
        if not trades:
            return {}

        wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]

        all_r = [t.get("realized_r", 0) or 0 for t in trades]
        mfe_vals = [t.get("highest_pnl", 0) or 0 for t in trades if (t.get("highest_pnl", 0) or 0) > 0]
        mae_vals = [abs(t.get("mae_pct", 0) or 0) for t in trades if (t.get("mae_pct", 0) or 0) > 0]
        hold_vals = [t.get("hold_minutes", 0) or 0 for t in trades]

        # Probabilities
        prob_1r = sum(1 for r in all_r if r >= 1.0) / max(1, len(all_r))
        prob_2r = sum(1 for r in all_r if r >= 2.0) / max(1, len(all_r))
        prob_3r = sum(1 for r in all_r if r >= 3.0) / max(1, len(all_r))
        prob_5r = sum(1 for r in all_r if r >= 5.0) / max(1, len(all_r))
        prob_sl = sum(1 for r in all_r if r < 0) / max(1, len(all_r))

        return {
            "count": len(trades),
            "avg_r": sum(all_r) / max(1, len(all_r)),
            "avg_mfe_r": sum(mfe_vals) / max(1, len(mfe_vals)),
            "avg_mae_r": sum(mae_vals) / max(1, len(mae_vals)),
            "avg_hold": sum(hold_vals) / max(1, len(hold_vals)),
            "win_rate": len(wins) / max(1, len(trades)),
            "prob_1r": prob_1r,
            "prob_2r": prob_2r,
            "prob_3r": prob_3r,
            "prob_5r": prob_5r,
            "prob_sl": prob_sl,
        }

    def predict(
        self,
        symbol: str,
        side: str,
        regime: str = "unknown",
        confidence: float = 85.0,
        risk_reward: float = 2.0,
    ) -> TradePathPrediction:
        """
        Predict trade evolution for a signal.

        Args:
            symbol: Trading symbol
            side: LONG or SHORT
            regime: Market regime
            confidence: Signal confidence (0-100)
            risk_reward: Planned risk/reward ratio

        Returns:
            TradePathPrediction with expected evolution
        """
        self._ensure_loaded()

        prediction = TradePathPrediction(symbol=symbol, side=side)

        # ── Get historical statistics ──
        sym_stats = self._symbol_stats.get(symbol, {})
        regime_stats = self._regime_stats.get(regime, {})

        # Blend symbol and regime stats (weight by sample size)
        sym_weight = min(1.0, sym_stats.get("count", 0) / 30)  # Full weight at 30+ trades
        regime_weight = min(1.0, regime_stats.get("count", 0) / 50)  # Full weight at 50+ trades

        if sym_weight + regime_weight > 0:
            s_sym = sym_weight / (sym_weight + regime_weight)
            s_reg = regime_weight / (sym_weight + regime_weight)
        else:
            s_sym = 0.5
            s_reg = 0.5

        # ── Expected R-multiple ──
        sym_avg_r = sym_stats.get("avg_r", 0)
        regime_avg_r = regime_stats.get("avg_r", 0)
        prediction.expected_r = sym_avg_r * s_sym + regime_avg_r * s_reg

        # ── Expected MFE ──
        sym_avg_mfe = sym_stats.get("avg_mfe_r", 0)
        regime_avg_mfe = regime_stats.get("avg_mfe_r", 0)
        prediction.expected_mfe_r = sym_avg_mfe * s_sym + regime_avg_mfe * s_reg

        # ── Expected MAE ──
        sym_avg_mae = sym_stats.get("avg_mae_r", 0)
        regime_avg_mae = regime_stats.get("avg_mae_r", 0)
        prediction.expected_mae_r = sym_avg_mae * s_sym + regime_avg_mae * s_reg

        # ── Expected holding time ──
        sym_avg_hold = sym_stats.get("avg_hold", 0)
        regime_avg_hold = regime_stats.get("avg_hold", 0)
        prediction.expected_hold_minutes = sym_avg_hold * s_sym + regime_avg_hold * s_reg

        # ── Probabilities ──
        sym_p1r = sym_stats.get("prob_1r", 0.5)
        regime_p1r = regime_stats.get("prob_1r", 0.5)
        prediction.prob_reach_1r = sym_p1r * s_sym + regime_p1r * s_reg

        sym_p2r = sym_stats.get("prob_2r", 0.3)
        regime_p2r = regime_stats.get("prob_2r", 0.3)
        prediction.prob_reach_2r = sym_p2r * s_sym + regime_p2r * s_reg

        sym_p3r = sym_stats.get("prob_3r", 0.2)
        regime_p3r = regime_stats.get("prob_3r", 0.2)
        prediction.prob_reach_3r = sym_p3r * s_sym + regime_p3r * s_reg

        sym_p5r = sym_stats.get("prob_5r", 0.1)
        regime_p5r = regime_stats.get("prob_5r", 0.1)
        prediction.prob_reach_5r = sym_p5r * s_sym + regime_p5r * s_reg

        sym_psl = sym_stats.get("prob_sl", 0.5)
        regime_psl = regime_stats.get("prob_sl", 0.5)
        prediction.prob_stop_loss = sym_psl * s_sym + regime_psl * s_reg

        # ── Risk/Reward Ratio ──
        if prediction.expected_mae_r > 0:
            prediction.risk_reward_ratio = prediction.expected_mfe_r / prediction.expected_mae_r
        else:
            prediction.risk_reward_ratio = risk_reward

        # ── Expected Capture ──
        sym_wr = sym_stats.get("win_rate", 0.5)
        regime_wr = regime_stats.get("win_rate", 0.5)
        win_rate = sym_wr * s_sym + regime_wr * s_reg
        prediction.expected_capture_pct = win_rate * 60 + (1 - win_rate) * 20  # Rough estimate

        # ── Expected Efficiency ──
        prediction.expected_efficiency = prediction.expected_capture_pct * 0.8

        # ── Trade Classification ──
        if prediction.expected_hold_minutes < 30:
            prediction.trade_type = "SCALP"
        elif prediction.expected_hold_minutes < 120:
            prediction.trade_type = "SWING"
        elif prediction.expected_hold_minutes < 480:
            prediction.trade_type = "TREND"
        else:
            prediction.trade_type = "REVERSAL"

        # ── Confidence ──
        data_confidence = min(100, (sym_stats.get("count", 0) + regime_stats.get("count", 0)) / 2)
        prediction.confidence = data_confidence * 0.7 + confidence * 0.3

        # ── Recommendation ──
        if prediction.expected_r > 0.3 and prediction.prob_reach_2r > 0.3:
            prediction.recommendation = "SIZE_UP"
        elif prediction.expected_r > 0:
            prediction.recommendation = "NORMAL"
        elif prediction.expected_r > -0.2:
            prediction.recommendation = "SIZE_DOWN"
        else:
            prediction.recommendation = "SKIP"

        return prediction

    def predict_batch(
        self,
        signals: List[Dict[str, Any]],
    ) -> PathPredictionReport:
        """
        Predict trade evolution for multiple signals.

        Args:
            signals: List of signal dicts

        Returns:
            PathPredictionReport with all predictions
        """
        report = PathPredictionReport(timestamp=time.time())

        for sig in signals:
            pred = self.predict(
                symbol=sig.get("symbol", ""),
                side=sig.get("side", ""),
                regime=sig.get("regime", "unknown"),
                confidence=sig.get("confidence", 85),
                risk_reward=sig.get("risk_reward", 2.0),
            )
            report.predictions.append(pred)

        # Aggregate
        if report.predictions:
            report.avg_expected_r = sum(p.expected_r for p in report.predictions) / len(report.predictions)
            report.avg_expected_mfe_r = sum(p.expected_mfe_r for p in report.predictions) / len(report.predictions)
            report.avg_prob_reach_3r = sum(p.prob_reach_3r for p in report.predictions) / len(report.predictions)
            report.avg_efficiency = sum(p.expected_efficiency for p in report.predictions) / len(report.predictions)

            for p in report.predictions:
                if p.trade_type == "SCALP":
                    report.scalp_count += 1
                elif p.trade_type == "SWING":
                    report.swing_count += 1
                elif p.trade_type == "TREND":
                    report.trend_count += 1

                if p.recommendation == "SIZE_UP":
                    report.size_up_count += 1
                elif p.recommendation == "NORMAL":
                    report.normal_count += 1
                elif p.recommendation == "SIZE_DOWN":
                    report.size_down_count += 1
                else:
                    report.skip_count += 1

        return report
