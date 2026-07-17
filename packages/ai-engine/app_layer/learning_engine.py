"""
Learning Engine — Adaptive self-learning from completed trades.

Per Priority 1: Thresholds should not stay fixed forever.
    Backtest → Live Performance → Auto Calibration → Update Threshold

This module continuously recalibrates pipeline thresholds using
historical trade data. Every N completed trades, it recomputes
optimal values for institution agreement, R:R minimums, TQ thresholds,
and other parameters.

READ-ONLY: never modifies upstream data. Only updates internal thresholds.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

# Recalibration interval (number of new trades since last calibration)
RECALIBRATION_INTERVAL = 50

# Minimum trades for reliable calibration
MIN_TRADES_FOR_CALIBRATION = 30

# Confidence threshold bounds
MIN_INSTITUTION_AGREEMENT = 0.40
MAX_INSTITUTION_AGREEMENT = 0.85

MIN_RR = 1.5
MAX_RR = 4.0

MIN_TQ_THRESHOLD = 50
MAX_TQ_THRESHOLD = 95


@dataclass
class CalibratedThresholds:
    """Adaptive thresholds calibrated from live performance."""
    # Institution Agreement
    institution_agreement_min: float = 0.60
    institution_agreement_elite: float = 0.70

    # Reward Filter
    rr_min: float = 2.0
    rr_preferred: float = 2.5

    # Trade Quality
    tq_min_execute: float = 75
    tq_min_elite: float = 85

    # Expectancy
    ev_min_r: float = 0.5

    # Position Sizing
    quality_multiplier_base: float = 1.0

    # Exit
    breakeven_trigger_r: float = 1.0
    trailing_distance_r: float = 1.0
    time_stop_hours: float = 6.0

    # Metadata
    calibration_trades: int = 0
    calibration_timestamp: float = 0.0
    confidence_score: float = 0.0  # How confident we are in these thresholds

    def to_dict(self) -> Dict:
        return {
            "institution_agreement_min": round(self.institution_agreement_min, 3),
            "institution_agreement_elite": round(self.institution_agreement_elite, 3),
            "rr_min": round(self.rr_min, 2),
            "rr_preferred": round(self.rr_preferred, 2),
            "tq_min_execute": round(self.tq_min_execute, 1),
            "tq_min_elite": round(self.tq_min_elite, 1),
            "ev_min_r": round(self.ev_min_r, 3),
            "breakeven_trigger_r": round(self.breakeven_trigger_r, 2),
            "trailing_distance_r": round(self.trailing_distance_r, 2),
            "time_stop_hours": round(self.time_stop_hours, 1),
            "calibration_trades": self.calibration_trades,
            "confidence_score": round(self.confidence_score, 2),
        }


@dataclass
class CalibrationResult:
    """Result of a calibration cycle."""
    thresholds: CalibratedThresholds
    changes: List[str] = field(default_factory=list)
    previous: Optional[CalibratedThresholds] = None

    def to_dict(self) -> Dict:
        return {
            "thresholds": self.thresholds.to_dict(),
            "changes": self.changes,
        }


class LearningEngine:
    """
    Adaptive self-learning engine that recalibrates pipeline thresholds.

    Per Priority 1: Auto-calibrate from live performance.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._thresholds = CalibratedThresholds()
        self._last_calibration_count = 0
        self._last_calibration_time = 0.0

    def maybe_recalibrate(self, force: bool = False) -> Optional[CalibrationResult]:
        """
        Check if recalibration is needed and perform it.

        Args:
            force: Force recalibration regardless of trade count

        Returns:
            CalibrationResult if recalibrated, None if not needed
        """
        # Count completed trades
        trade_count = self._count_completed_trades()

        # Check if recalibration is needed
        if not force and (trade_count - self._last_calibration_count) < RECALIBRATION_INTERVAL:
            return None

        if trade_count < MIN_TRADES_FOR_CALIBRATION:
            logger.debug("Learning: {} trades < {} minimum for calibration", trade_count, MIN_TRADES_FOR_CALIBRATION)
            return None

        # Perform calibration
        result = self._calibrate(trade_count)
        self._last_calibration_count = trade_count
        self._last_calibration_time = time.time()

        if result.changes:
            logger.info(
                "LEARNING: Recalibrated with {} trades — {} changes: {}",
                trade_count, len(result.changes), "; ".join(result.changes[:3]),
            )
        else:
            logger.debug("Learning: Recalibrated with {} trades — no changes", trade_count)

        return result

    def get_thresholds(self) -> CalibratedThresholds:
        """Get current calibrated thresholds."""
        self.maybe_recalibrate()
        return self._thresholds

    def get_symbol_adjusted_thresholds(self, symbol: str) -> CalibratedThresholds:
        """Get thresholds adjusted for a specific symbol."""
        base = self.get_thresholds()
        # Symbol-specific adjustments will be applied by SymbolProfiles
        return base

    # ── Calibration Logic ────────────────────────────────────────

    def _calibrate(self, trade_count: int) -> CalibrationResult:
        """Perform full calibration cycle."""
        previous = CalibratedThresholds(
            institution_agreement_min=self._thresholds.institution_agreement_min,
            institution_agreement_elite=self._thresholds.institution_agreement_elite,
            rr_min=self._thresholds.rr_min,
            rr_preferred=self._thresholds.rr_preferred,
            tq_min_execute=self._thresholds.tq_min_execute,
            tq_min_elite=self._thresholds.tq_min_elite,
            ev_min_r=self._thresholds.ev_min_r,
        )
        changes = []

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            # ── Calibrate Institution Agreement ──
            old_ia = self._thresholds.institution_agreement_min
            new_ia = self._calibrate_institution_agreement(cur)
            if abs(new_ia - old_ia) > 0.02:
                self._thresholds.institution_agreement_min = new_ia
                self._thresholds.institution_agreement_elite = min(new_ia + 0.10, 0.90)
                changes.append(f"inst_agree: {old_ia:.0%} → {new_ia:.0%}")

            # ── Calibrate R:R Minimum ──
            old_rr = self._thresholds.rr_min
            new_rr = self._calibrate_rr_min(cur)
            if abs(new_rr - old_rr) > 0.1:
                self._thresholds.rr_min = new_rr
                self._thresholds.rr_preferred = new_rr + 0.5
                changes.append(f"rr_min: {old_rr:.2f} → {new_rr:.2f}")

            # ── Calibrate TQ Threshold ──
            old_tq = self._thresholds.tq_min_execute
            new_tq = self._calibrate_tq_threshold(cur)
            if abs(new_tq - old_tq) > 2:
                self._thresholds.tq_min_execute = new_tq
                self._thresholds.tq_min_elite = new_tq + 10
                changes.append(f"tq_min: {old_tq:.0f} → {new_tq:.0f}")

            # ── Calibrate EV Minimum ──
            old_ev = self._thresholds.ev_min_r
            new_ev = self._calibrate_ev_min(cur)
            if abs(new_ev - old_ev) > 0.1:
                self._thresholds.ev_min_r = new_ev
                changes.append(f"ev_min: {old_ev:.2f}R → {new_ev:.2f}R")

            # ── Calibrate Exit Parameters ──
            old_be = self._thresholds.breakeven_trigger_r
            new_be = self._calibrate_breakeven(cur)
            if abs(new_be - old_be) > 0.1:
                self._thresholds.breakeven_trigger_r = new_be
                changes.append(f"breakeven: {old_be:.2f}R → {new_be:.2f}R")

            old_trail = self._thresholds.trailing_distance_r
            new_trail = self._calibrate_trailing(cur)
            if abs(new_trail - old_trail) > 0.1:
                self._thresholds.trailing_distance_r = new_trail
                changes.append(f"trailing: {old_trail:.2f}R → {new_trail:.2f}R")

            conn.close()

        except Exception as e:
            logger.warning("Calibration error: {}", e)

        # Calculate confidence based on sample size
        self._thresholds.calibration_trades = trade_count
        self._thresholds.calibration_timestamp = time.time()
        self._thresholds.confidence_score = min(trade_count / 500, 1.0)

        return CalibrationResult(
            thresholds=self._thresholds,
            changes=changes,
            previous=previous,
        )

    def _calibrate_institution_agreement(self, cur) -> float:
        """Find optimal institution agreement threshold."""
        # Query: for different agreement levels, what's the win rate?
        cur.execute("""
            SELECT
                CASE
                    WHEN institution_agreement >= 0.8 THEN '80+'
                    WHEN institution_agreement >= 0.7 THEN '70-80'
                    WHEN institution_agreement >= 0.6 THEN '60-70'
                    WHEN institution_agreement >= 0.5 THEN '50-60'
                    ELSE '<50'
                END as bucket,
                COUNT(*) as n,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                AVG(pnl) as avg_pnl
            FROM positions
            WHERE status = 'closed' AND institution_agreement IS NOT NULL
            GROUP BY bucket
            ORDER BY bucket DESC
        """)
        rows = cur.fetchall()

        # Find the bucket where win rate first exceeds 40%
        best_threshold = 0.60
        for bucket, n, wins, avg_pnl in rows:
            if n >= 5:
                wr = wins / n if n > 0 else 0
                if wr >= 0.40 and avg_pnl > 0:
                    # Parse bucket to threshold
                    if bucket == '80+':
                        best_threshold = 0.80
                    elif bucket == '70-80':
                        best_threshold = 0.70
                    elif bucket == '60-70':
                        best_threshold = 0.60
                    elif bucket == '50-60':
                        best_threshold = 0.50
                    break

        return max(MIN_INSTITUTION_AGREEMENT, min(best_threshold, MAX_INSTITUTION_AGREEMENT))

    def _calibrate_rr_min(self, cur) -> float:
        """Find optimal minimum R:R."""
        cur.execute("""
            SELECT
                CASE
                    WHEN risk_reward >= 3.0 THEN '3.0+'
                    WHEN risk_reward >= 2.5 THEN '2.5-3.0'
                    WHEN risk_reward >= 2.0 THEN '2.0-2.5'
                    WHEN risk_reward >= 1.5 THEN '1.5-2.0'
                    ELSE '<1.5'
                END as bucket,
                COUNT(*) as n,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                AVG(pnl) as avg_pnl,
                AVG(realized_r) as avg_r
            FROM positions
            WHERE status = 'closed' AND risk_reward IS NOT NULL
            GROUP BY bucket
            ORDER BY bucket DESC
        """)
        rows = cur.fetchall()

        # Find minimum R:R with positive expectancy
        best_rr = 2.0
        for bucket, n, wins, avg_pnl, avg_r in rows:
            if n >= 5 and avg_pnl and avg_pnl > 0:
                if bucket == '3.0+':
                    best_rr = 3.0
                elif bucket == '2.5-3.0':
                    best_rr = 2.5
                elif bucket == '2.0-2.5':
                    best_rr = 2.0
                elif bucket == '1.5-2.0':
                    best_rr = 1.5
                break

        return max(MIN_RR, min(best_rr, MAX_RR))

    def _calibrate_tq_threshold(self, cur) -> float:
        """Find optimal TQ threshold."""
        # Use confidence as proxy for TQ
        cur.execute("""
            SELECT
                CASE
                    WHEN confidence >= 0.95 THEN '95+'
                    WHEN confidence >= 0.90 THEN '90-95'
                    WHEN confidence >= 0.85 THEN '85-90'
                    WHEN confidence >= 0.80 THEN '80-85'
                    ELSE '<80'
                END as bucket,
                COUNT(*) as n,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                AVG(pnl) as avg_pnl
            FROM positions
            WHERE status = 'closed'
            GROUP BY bucket
            ORDER BY bucket DESC
        """)
        rows = cur.fetchall()

        # Find minimum bucket with positive expectancy
        best_tq = 75.0
        for bucket, n, wins, avg_pnl in rows:
            if n >= 5 and avg_pnl and avg_pnl > 0:
                if bucket == '95+':
                    best_tq = 95
                elif bucket == '90-95':
                    best_tq = 90
                elif bucket == '85-90':
                    best_tq = 85
                elif bucket == '80-85':
                    best_tq = 80
                break

        return max(MIN_TQ_THRESHOLD, min(best_tq, MAX_TQ_THRESHOLD))

    def _calibrate_ev_min(self, cur) -> float:
        """Find optimal minimum EV."""
        # Use realized_r as proxy for EV
        cur.execute("""
            SELECT AVG(realized_r) as avg_r
            FROM positions
            WHERE status = 'closed' AND realized_r IS NOT NULL AND realized_r > 0
        """)
        row = cur.fetchone()
        if row and row[0]:
            # Set minimum EV to 30% of average winning R
            return max(0.3, row[0] * 0.3)
        return 0.5

    def _calibrate_breakeven(self, cur) -> float:
        """Find optimal breakeven trigger."""
        # Analyze trades that moved to breakeven vs didn't
        cur.execute("""
            SELECT
                AVG(CASE WHEN realized_r >= 1.0 THEN realized_r ELSE NULL END) as be_win_r,
                AVG(CASE WHEN realized_r < 1.0 THEN realized_r ELSE NULL END) as no_be_win_r
            FROM positions
            WHERE status = 'closed'
        """)
        row = cur.fetchone()
        if row and row[0] and row[1]:
            # If breakeven trades perform better, use lower trigger
            if row[0] > row[1]:
                return 0.8  # Lower trigger = more trades move to breakeven
            else:
                return 1.2  # Higher trigger = let winners run more
        return 1.0

    def _calibrate_trailing(self, cur) -> float:
        """Find optimal trailing distance."""
        cur.execute("""
            SELECT
                AVG(CASE WHEN exit_reason LIKE '%trailing%' THEN realized_r ELSE NULL END) as trail_r,
                AVG(CASE WHEN exit_reason LIKE '%take_profit%' THEN realized_r ELSE NULL END) as tp_r,
                AVG(CASE WHEN exit_reason = 'stop_loss' THEN realized_r ELSE NULL END) as sl_r
            FROM positions
            WHERE status = 'closed'
        """)
        row = cur.fetchone()
        if row and row[0]:
            # If trailing exits perform well, use tighter trail
            if row[0] and row[0] > 0:
                return 0.8  # Tighter trail captures more profit
            else:
                return 1.2  # Wider trail lets winners run
        return 1.0

    def _count_completed_trades(self) -> int:
        """Count total completed trades."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM positions WHERE status = 'closed'")
            count = cur.fetchone()[0]
            conn.close()
            return count
        except:
            return 0
