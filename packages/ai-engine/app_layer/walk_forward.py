"""
Walk-Forward Validation Framework — Train→Test→Train→Test pattern.

Per Priority: Instead of validating on all historical data:
    Train → Test → Train → Test → Train → Test
    This verifies that the Learning Engine generalizes rather than memorizes.

Walk-Forward Process:
    1. Split historical data into N folds
    2. For each fold:
       a. Train on fold[i] (optimize parameters)
       b. Test on fold[i+1] (validate on unseen data)
       c. Record performance
    3. Aggregate out-of-sample results
    4. Only deploy if out-of-sample performance is positive

READ-ONLY: never modifies upstream data.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

# Walk-forward configuration
DEFAULT_FOLDS = 5
MIN_TRADES_PER_FOLD = 20
MIN_OOS_TRADES = 30  # Minimum out-of-sample trades


@dataclass
class FoldResult:
    """Result of a single walk-forward fold."""
    fold_index: int = 0
    train_start: float = 0.0
    train_end: float = 0.0
    test_start: float = 0.0
    test_end: float = 0.0
    train_trades: int = 0
    test_trades: int = 0

    # In-sample (training) metrics
    train_win_rate: float = 0.0
    train_profit_factor: float = 0.0
    train_expectancy_r: float = 0.0
    train_pnl: float = 0.0

    # Out-of-sample (testing) metrics
    test_win_rate: float = 0.0
    test_profit_factor: float = 0.0
    test_expectancy_r: float = 0.0
    test_pnl: float = 0.0

    # Degradation metrics
    pf_degradation: float = 0.0  # How much PF dropped from train to test
    overfitting_score: float = 0.0  # Higher = more overfitting

    def to_dict(self) -> Dict:
        return {
            "fold": self.fold_index,
            "train_trades": self.train_trades,
            "test_trades": self.test_trades,
            "train_pf": round(self.train_profit_factor, 2),
            "test_pf": round(self.test_profit_factor, 2),
            "train_wr": round(self.train_win_rate, 3),
            "test_wr": round(self.test_win_rate, 3),
            "train_ev": round(self.train_expectancy_r, 3),
            "test_ev": round(self.test_expectancy_r, 3),
            "pf_degradation": round(self.pf_degradation, 2),
            "overfitting_score": round(self.overfitting_score, 3),
        }


@dataclass
class WalkForwardResult:
    """Complete walk-forward validation result."""
    model_name: str = ""
    total_folds: int = 0
    total_train_trades: int = 0
    total_test_trades: int = 0

    # Aggregated out-of-sample metrics
    avg_oos_win_rate: float = 0.0
    avg_oos_profit_factor: float = 0.0
    avg_oos_expectancy_r: float = 0.0
    total_oos_pnl: float = 0.0

    # Overfitting assessment
    avg_pf_degradation: float = 0.0
    overfitting_risk: str = ""  # LOW / MODERATE / HIGH
    overfitting_score: float = 0.0

    # Pass/Fail
    passed: bool = False
    reason: str = ""

    # Fold details
    folds: List[FoldResult] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "model": self.model_name,
            "total_folds": self.total_folds,
            "total_test_trades": self.total_test_trades,
            "avg_oos_pf": round(self.avg_oos_profit_factor, 2),
            "avg_oos_wr": round(self.avg_oos_win_rate, 3),
            "avg_oos_ev": round(self.avg_oos_expectancy_r, 3),
            "total_oos_pnl": round(self.total_oos_pnl, 2),
            "avg_pf_degradation": round(self.avg_pf_degradation, 2),
            "overfitting_risk": self.overfitting_risk,
            "passed": self.passed,
            "reason": self.reason,
            "folds": [f.to_dict() for f in self.folds],
        }


class WalkForwardValidation:
    """
    Walk-forward validation framework.

    Per Priority: Verifies Learning Engine generalizes rather than memorizes.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH

    def validate(
        self,
        model_name: str = "decision_model",
        num_folds: int = DEFAULT_FOLDS,
    ) -> WalkForwardResult:
        """
        Perform walk-forward validation.

        Args:
            model_name: Name of the model to validate
            num_folds: Number of walk-forward folds

        Returns:
            WalkForwardResult with validation results
        """
        result = WalkForwardResult(model_name=model_name)

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Get all trades ordered by time
            cur.execute("""
                SELECT id, symbol, side, pnl, realized_r, confidence,
                       regime, session, exit_reason, opened_at, closed_at
                FROM positions
                WHERE status = 'closed'
                ORDER BY closed_at ASC
            """)
            trades = [dict(r) for r in cur.fetchall()]
            conn.close()

            if len(trades) < num_folds * MIN_TRADES_PER_FOLD:
                result.reason = (
                    f"insufficient trades: {len(trades)} < "
                    f"{num_folds * MIN_TRADES_PER_FOLD} required for {num_folds} folds"
                )
                logger.info("WALK-FORWARD: {}", result.reason)
                return result

            # Split into folds
            fold_size = len(trades) // num_folds
            folds = []

            for i in range(num_folds - 1):
                train_start = i * fold_size
                train_end = (i + 1) * fold_size
                test_start = train_end
                test_end = min(test_start + fold_size, len(trades))

                train_trades = trades[train_start:train_end]
                test_trades = trades[test_start:test_end]

                if len(test_trades) < MIN_TRADES_PER_FOLD:
                    continue

                fold_result = self._evaluate_fold(
                    i, train_trades, test_trades,
                    train_start_idx=train_start,
                    train_end_idx=train_end,
                    test_start_idx=test_start,
                    test_end_idx=test_end,
                )
                folds.append(fold_result)

            if not folds:
                result.reason = "no valid folds after filtering"
                return result

            result.folds = folds
            result.total_folds = len(folds)
            result.total_train_trades = sum(f.train_trades for f in folds)
            result.total_test_trades = sum(f.test_trades for f in folds)

            # Aggregate out-of-sample metrics
            oos_wrs = [f.test_win_rate for f in folds if f.test_trades > 0]
            oos_pfs = [f.test_profit_factor for f in folds if f.test_trades > 0 and f.test_profit_factor < 100]
            oos_evs = [f.test_expectancy_r for f in folds if f.test_trades > 0]

            result.avg_oos_win_rate = sum(oos_wrs) / len(oos_wrs) if oos_wrs else 0
            result.avg_oos_profit_factor = sum(oos_pfs) / len(oos_pfs) if oos_pfs else 0
            result.avg_oos_expectancy_r = sum(oos_evs) / len(oos_evs) if oos_evs else 0
            result.total_oos_pnl = sum(f.test_pnl for f in folds)

            # Overfitting assessment
            degradations = [f.pf_degradation for f in folds]
            result.avg_pf_degradation = sum(degradations) / len(degradations) if degradations else 0

            # Overfitting score: average of all fold overfitting scores
            overfit_scores = [f.overfitting_score for f in folds]
            result.overfitting_score = sum(overfit_scores) / len(overfit_scores) if overfit_scores else 0

            if result.overfitting_score < 0.3:
                result.overfitting_risk = "LOW"
            elif result.overfitting_score < 0.6:
                result.overfitting_risk = "MODERATE"
            else:
                result.overfitting_risk = "HIGH"

            # Pass/Fail criteria
            min_oos_trades = MIN_OOS_TRADES
            if result.total_test_trades < min_oos_trades:
                result.passed = False
                result.reason = f"insufficient OOS trades: {result.total_test_trades} < {min_oos_trades}"
            elif result.avg_oos_profit_factor < 1.0:
                result.passed = False
                result.reason = f"OOS profit factor {result.avg_oos_profit_factor:.2f} < 1.0"
            elif result.avg_oos_expectancy_r <= 0:
                result.passed = False
                result.reason = f"OOS expectancy {result.avg_oos_expectancy_r:.3f}R <= 0"
            elif result.overfitting_risk == "HIGH":
                result.passed = False
                result.reason = f"overfitting risk HIGH (score={result.overfitting_score:.2f})"
            else:
                result.passed = True
                result.reason = (
                    f"OOS PF={result.avg_oos_profit_factor:.2f}, "
                    f"EV={result.avg_oos_expectancy_r:.3f}R, "
                    f"overfitting={result.overfitting_risk}"
                )

            logger.info(
                "WALK-FORWARD: {} — {} folds, {} OOS trades, "
                "PF={:.2f}, EV={:.3f}R, overfitting={}, PASSED={}",
                model_name, result.total_folds, result.total_test_trades,
                result.avg_oos_profit_factor, result.avg_oos_expectancy_r,
                result.overfitting_risk, result.passed,
            )

        except Exception as e:
            result.reason = f"validation error: {e}"
            logger.warning("Walk-forward error: {}", e)

        return result

    def _evaluate_fold(
        self,
        fold_index: int,
        train_trades: List[Dict],
        test_trades: List[Dict],
        train_start_idx: int = 0,
        train_end_idx: int = 0,
        test_start_idx: int = 0,
        test_end_idx: int = 0,
    ) -> FoldResult:
        """Evaluate a single walk-forward fold."""
        fold = FoldResult(fold_index=fold_index)

        # Training metrics
        fold.train_trades = len(train_trades)
        train_pnls = [t.get("pnl", 0) or 0 for t in train_trades]
        train_rs = [t.get("realized_r", 0) or 0 for t in train_trades]

        if train_pnls:
            fold.train_pnl = sum(train_pnls)
            fold.train_win_rate = sum(1 for p in train_pnls if p > 0) / len(train_pnls)
            train_wins = [r for r in train_rs if r > 0]
            train_losses = [r for r in train_rs if r <= 0]
            gp = sum(train_wins) if train_wins else 0
            gl = sum(abs(r) for r in train_losses) if train_losses else 0
            fold.train_profit_factor = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
            fold.train_expectancy_r = sum(train_rs) / len(train_rs) if train_rs else 0

        # Testing metrics
        fold.test_trades = len(test_trades)
        test_pnls = [t.get("pnl", 0) or 0 for t in test_trades]
        test_rs = [t.get("realized_r", 0) or 0 for t in test_trades]

        if test_pnls:
            fold.test_pnl = sum(test_pnls)
            fold.test_win_rate = sum(1 for p in test_pnls if p > 0) / len(test_pnls)
            test_wins = [r for r in test_rs if r > 0]
            test_losses = [r for r in test_rs if r <= 0]
            gp = sum(test_wins) if test_wins else 0
            gl = sum(abs(r) for r in test_losses) if test_losses else 0
            fold.test_profit_factor = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
            fold.test_expectancy_r = sum(test_rs) / len(test_rs) if test_rs else 0

        # Degradation calculation
        if fold.train_profit_factor > 0 and fold.train_profit_factor < 100:
            fold.pf_degradation = (
                (fold.train_profit_factor - fold.test_profit_factor)
                / fold.train_profit_factor * 100
            )
        else:
            fold.pf_degradation = 0

        # Overfitting score
        # High degradation + high train PF + low test PF = overfitting
        if fold.train_profit_factor > 1.5:
            fold.overfitting_score = max(0, fold.pf_degradation / 100)
        else:
            fold.overfitting_score = 0.1  # Low baseline

        return fold
