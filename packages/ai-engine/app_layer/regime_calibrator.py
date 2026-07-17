"""
Regime-Specific Calibrator — Maintain separate parameter sets per regime.

Per Executive Assessment v12:
    "Instead of one predictive model, I would seriously consider
     maintaining separate calibrated parameter sets for materially
     different market regimes (for example, trending vs. ranging,
     high vs. low volatility), provided each regime has enough
     historical data to support reliable estimation."

Key Innovation:
    v17 used: One global model for all regimes
    v18 uses: Separate calibrated parameters per regime

    This allows:
        - Regime-specific entry thresholds
        - Regime-specific exit parameters
        - Regime-specific position sizing
        - Better adaptation to market conditions

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
class RegimeParameters:
    """Calibrated parameters for a specific regime."""
    regime: str = ""

    # Performance metrics
    trade_count: int = 0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    win_rate: float = 0.0
    avg_r: float = 0.0
    avg_mfe_r: float = 0.0
    avg_mae_r: float = 0.0

    # Calibrated parameters
    confidence_threshold: float = 85.0  # Minimum confidence for this regime
    risk_multiplier: float = 1.0        # Risk adjustment for this regime
    position_size_factor: float = 1.0   # Position size adjustment
    take_profit_r: float = 2.0          # Target R:R for this regime
    stop_loss_r: float = 1.0            # Stop distance in R

    # Calibration quality
    sample_size: int = 0
    calibration_confidence: float = 0.0  # 0-100

    def to_dict(self) -> Dict:
        return {
            "regime": self.regime,
            "performance": {
                "trades": self.trade_count,
                "pf": round(self.profit_factor, 2),
                "ev_r": round(self.expectancy_r, 3),
                "win_rate": round(self.win_rate, 3),
                "avg_r": round(self.avg_r, 3),
                "avg_mfe_r": round(self.avg_mfe_r, 3),
                "avg_mae_r": round(self.avg_mae_r, 3),
            },
            "parameters": {
                "confidence_threshold": round(self.confidence_threshold, 1),
                "risk_multiplier": round(self.risk_multiplier, 2),
                "position_size_factor": round(self.position_size_factor, 2),
                "take_profit_r": round(self.take_profit_r, 2),
                "stop_loss_r": round(self.stop_loss_r, 2),
            },
            "calibration": {
                "sample_size": self.sample_size,
                "confidence": round(self.calibration_confidence, 1),
            },
        }


@dataclass
class RegimeCalibrationReport:
    """Complete regime-specific calibration report."""
    timestamp: float = 0.0
    regimes: List[RegimeParameters] = field(default_factory=list)

    # Summary
    total_regimes: int = 0
    well_calibrated_regimes: int = 0
    under_calibrated_regimes: int = 0

    # Recommendations
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "regimes": [r.to_dict() for r in self.regimes],
            "summary": {
                "total": self.total_regimes,
                "well_calibrated": self.well_calibrated_regimes,
                "under_calibrated": self.under_calibrated_regimes,
            },
            "recommendations": self.recommendations,
        }


class RegimeSpecificCalibrator:
    """
    Maintains separate calibrated parameters per regime.

    Per Executive Assessment v12:
        "Maintain separate calibrated parameter sets for materially
         different market regimes."

    This engine:
        1. Calculates performance per regime
        2. Calibrates parameters per regime
        3. Identifies regimes with sufficient data
        4. Recommends regime-specific adjustments

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
                       exit_reason, regime, session, confidence,
                       institutional_score
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load regime-specific calibrator: {}", e)

    def calibrate(self) -> RegimeCalibrationReport:
        """
        Calibrate parameters for each regime.

        Returns:
            RegimeCalibrationReport with regime-specific parameters
        """
        self._ensure_loaded()

        report = RegimeCalibrationReport(timestamp=time.time())

        if not self._trades:
            return report

        # ── Group by regime ──
        by_regime: Dict[str, List[Dict]] = defaultdict(list)
        for t in self._trades:
            by_regime[t.get("regime", "unknown")].append(t)

        # ── Calibrate each regime ──
        for regime, trades in by_regime.items():
            params = self._calibrate_regime(regime, trades)
            report.regimes.append(params)

        # ── Summary ──
        report.total_regimes = len(report.regimes)
        report.well_calibrated_regimes = sum(
            1 for r in report.regimes if r.calibration_confidence >= 70
        )
        report.under_calibrated_regimes = sum(
            1 for r in report.regimes if r.calibration_confidence < 50
        )

        # ── Recommendations ──
        report.recommendations = self._generate_recommendations(report)

        return report

    def _calibrate_regime(self, regime: str, trades: List[Dict]) -> RegimeParameters:
        """Calibrate parameters for a single regime."""
        params = RegimeParameters(regime=regime)
        params.sample_size = len(trades)

        if len(trades) < 10:
            params.calibration_confidence = len(trades) * 5  # Low confidence
            return params

        # ── Performance metrics ──
        wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]

        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        params.profit_factor = gross_profit / max(0.01, gross_loss)

        all_r = [t.get("realized_r", 0) or 0 for t in trades]
        params.expectancy_r = sum(all_r) / max(1, len(all_r))
        params.win_rate = len(wins) / max(1, len(trades))
        params.avg_r = params.expectancy_r

        mfe_vals = [t.get("highest_pnl", 0) or 0 for t in trades if (t.get("highest_pnl", 0) or 0) > 0]
        mae_vals = [abs(t.get("mae_pct", 0) or 0) for t in trades if (t.get("mae_pct", 0) or 0) > 0]
        params.avg_mfe_r = sum(mfe_vals) / max(1, len(mfe_vals))
        params.avg_mae_r = sum(mae_vals) / max(1, len(mae_vals))

        # ── Calibrate parameters ──
        # Confidence threshold: lower for profitable regimes
        if params.profit_factor > 1.2:
            params.confidence_threshold = 80.0
        elif params.profit_factor > 1.0:
            params.confidence_threshold = 85.0
        else:
            params.confidence_threshold = 90.0

        # Risk multiplier: scale by regime performance
        if params.profit_factor > 1.5:
            params.risk_multiplier = 1.2
        elif params.profit_factor > 1.2:
            params.risk_multiplier = 1.1
        elif params.profit_factor > 1.0:
            params.risk_multiplier = 1.0
        elif params.profit_factor > 0.8:
            params.risk_multiplier = 0.7
        else:
            params.risk_multiplier = 0.4

        # Position size factor: similar to risk multiplier
        params.position_size_factor = params.risk_multiplier

        # Take profit: based on regime characteristics
        if params.avg_mfe_r > 3.0:
            params.take_profit_r = 4.0  # Let winners run in high-MFE regimes
        elif params.avg_mfe_r > 2.0:
            params.take_profit_r = 3.0
        else:
            params.take_profit_r = 2.0

        # Stop loss: based on regime volatility
        params.stop_loss_r = max(0.5, min(2.0, params.avg_mae_r))

        # ── Calibration confidence ──
        params.calibration_confidence = min(100, len(trades) * 2)

        return params

    def _generate_recommendations(self, report: RegimeCalibrationReport) -> List[str]:
        """Generate calibration recommendations."""
        recs = []

        for regime in report.regimes:
            if regime.calibration_confidence < 50:
                recs.append(
                    f"{regime.regime}: Insufficient data ({regime.sample_size} trades) "
                    f"— use global parameters"
                )
            elif regime.profit_factor < 0.8:
                recs.append(
                    f"{regime.regime}: PF={regime.profit_factor:.2f} — "
                    f"consider reducing exposure or blocking trades"
                )
            elif regime.profit_factor > 1.3:
                recs.append(
                    f"{regime.regime}: PF={regime.profit_factor:.2f} — "
                    f"favorable regime, consider increasing exposure"
                )

        return recs

    def get_regime_params(self, regime: str) -> Optional[RegimeParameters]:
        """Get calibrated parameters for a specific regime."""
        report = self.calibrate()
        for r in report.regimes:
            if r.regime == regime:
                return r
        return None

    def get_all_regime_params(self) -> Dict[str, RegimeParameters]:
        """Get calibrated parameters for all regimes."""
        report = self.calibrate()
        return {r.regime: r for r in report.regimes}
