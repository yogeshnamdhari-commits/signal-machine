"""
Regime Adaptive Risk Sizing — Scale risk by historical regime performance.

Per Executive Assessment v4:
    "Performance by Regime should become adaptive.
     BUY_MODE PF = 1.35 Risk = 1.25x
     SELL_MODE PF = 0.82 Risk = 0.50x
     Execution becomes adaptive based on historical evidence."

Key Features:
    1. Regime PF Tracking — rolling PF per market regime
    2. Adaptive Risk Multiplier — scale position size by regime performance
    3. Regime Transition Detection — detect when regime changes
    4. Regime Confidence — how reliable is the regime PF estimate
    5. Combined with Symbol/Session adjustments

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

# ═══════════════════════════════════════════════════════════════
# REGIME RISK CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Rolling window for regime statistics
REGIME_ROLLING_WINDOW = 40

# Risk multipliers by regime PF
REGIME_RISK_MULTIPLIERS = {
    (1.5, 999): 1.25,    # Excellent regime — increase risk
    (1.2, 1.5): 1.10,    # Good regime — slight increase
    (1.0, 1.2): 1.00,    # Neutral regime — standard risk
    (0.8, 1.0): 0.70,    # Weak regime — reduce risk
    (0.0, 0.8): 0.40,    # Bad regime — heavy reduction
}

# Minimum trades for regime assessment
MIN_TRADES_FOR_REGIME = 10

# Regime definitions
REGIMES = {
    "trending_up": "Bullish trend — price above key MAs",
    "trending_down": "Bearish trend — price below key MAs",
    "range": "Range-bound — no clear trend",
    "volatile": "High volatility — large price swings",
    "calm": "Low volatility — compressed price action",
    "breakout": "Breakout — price breaking key levels",
    "reversal": "Reversal — trend changing direction",
}


@dataclass
class RegimeStats:
    """Performance statistics for a single regime."""
    regime: str = ""
    total_trades: int = 0
    rolling_trades: int = 0
    rolling_pf: float = 0.0
    rolling_ev_r: float = 0.0
    rolling_win_rate: float = 0.0
    rolling_avg_r: float = 0.0
    risk_multiplier: float = 1.0
    confidence: str = "LOW"  # LOW / MEDIUM / HIGH
    status: str = "NEUTRAL"  # FAVORABLE / NEUTRAL / UNFAVORABLE

    def to_dict(self) -> Dict:
        return {
            "regime": self.regime,
            "total_trades": self.total_trades,
            "rolling_trades": self.rolling_trades,
            "rolling_pf": round(self.rolling_pf, 2),
            "rolling_ev_r": round(self.rolling_ev_r, 3),
            "rolling_win_rate": round(self.rolling_win_rate, 3),
            "rolling_avg_r": round(self.rolling_avg_r, 3),
            "risk_multiplier": round(self.risk_multiplier, 2),
            "confidence": self.confidence,
            "status": self.status,
        }


@dataclass
class RegimeRiskResult:
    """Result from regime adaptive risk evaluation."""
    current_regime: str = ""
    regime_stats: Optional[RegimeStats] = None
    risk_multiplier: float = 1.0
    all_regimes: Dict[str, RegimeStats] = None
    timestamp: float = 0.0

    def __post_init__(self):
        if self.all_regimes is None:
            self.all_regimes = {}

    def to_dict(self) -> Dict:
        return {
            "current_regime": self.current_regime,
            "regime_stats": self.regime_stats.to_dict() if self.regime_stats else {},
            "risk_multiplier": round(self.risk_multiplier, 2),
            "all_regimes": {k: v.to_dict() for k, v in self.all_regimes.items()},
            "timestamp": self.timestamp,
        }


class RegimeAdaptiveRiskSizing:
    """
    Scales risk based on historical regime performance.

    Per Executive Assessment v4:
        "BUY_MODE PF = 1.35 Risk = 1.25x
         SELL_MODE PF = 0.82 Risk = 0.50x"

    This engine:
        1. Tracks rolling PF per market regime
        2. Calculates risk multiplier for current regime
        3. Combines with symbol/session adjustments
        4. Provides regime transition insights

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._regime_stats: Dict[str, RegimeStats] = {}
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        """Load regime data from DB if stale."""
        if time.time() - self._last_load < 300:
            return
        self._load_regime_data()

    def _load_regime_data(self) -> None:
        """Load all closed trades and compute rolling PF per regime."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT regime, realized_r, pnl, closed_at
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            # Group by regime
            by_regime: Dict[str, List[Dict]] = defaultdict(list)
            for row in rows:
                r = dict(row)
                regime = r.get("regime", "unknown")
                by_regime[regime].append(r)

            # Calculate rolling PF for each regime
            self._regime_stats = {}
            for regime, trades in by_regime.items():
                self._regime_stats[regime] = self._calc_regime_stats(regime, trades)

            self._last_load = time.time()
            logger.info(
                "📊 Regime Adaptive Risk loaded: {} regimes",
                len(self._regime_stats),
            )

        except Exception as e:
            logger.warning("Could not load regime adaptive risk: {}", e)

    def _calc_regime_stats(self, regime: str, trades: List[Dict]) -> RegimeStats:
        """Calculate rolling PF and risk multiplier for a regime."""
        stats = RegimeStats(
            regime=regime,
            total_trades=len(trades),
        )

        if not trades:
            return stats

        # Rolling window
        rolling = trades[:REGIME_ROLLING_WINDOW]
        stats.rolling_trades = len(rolling)

        # Calculate rolling PF
        wins = [t.get("realized_r", 0) or 0 for t in rolling if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in rolling if (t.get("realized_r", 0) or 0) < 0]

        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        stats.rolling_pf = gross_profit / max(0.01, gross_loss)

        # Rolling EV
        all_r = [t.get("realized_r", 0) or 0 for t in rolling]
        stats.rolling_ev_r = sum(all_r) / max(1, len(all_r))
        stats.rolling_avg_r = stats.rolling_ev_r

        # Win rate
        stats.rolling_win_rate = len(wins) / max(1, len(rolling))

        # Confidence
        if stats.total_trades >= 30:
            stats.confidence = "HIGH"
        elif stats.total_trades >= MIN_TRADES_FOR_REGIME:
            stats.confidence = "MEDIUM"
        else:
            stats.confidence = "LOW"

        # Status
        if stats.rolling_pf >= 1.2:
            stats.status = "FAVORABLE"
        elif stats.rolling_pf >= 0.9:
            stats.status = "NEUTRAL"
        else:
            stats.status = "UNFAVORABLE"

        # Risk multiplier
        stats.risk_multiplier = self._get_risk_multiplier(stats.rolling_pf)

        return stats

    def _get_risk_multiplier(self, pf: float) -> float:
        """Get risk multiplier based on regime PF."""
        for (low, high), mult in REGIME_RISK_MULTIPLIERS.items():
            if low <= pf < high:
                return mult
        return 1.0

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════

    def evaluate(self, current_regime: str = "unknown") -> RegimeRiskResult:
        """
        Evaluate regime risk for the current market condition.

        Args:
            current_regime: Current market regime

        Returns:
            RegimeRiskResult with risk multiplier and regime stats
        """
        self._ensure_loaded()

        result = RegimeRiskResult(
            current_regime=current_regime,
            timestamp=time.time(),
            all_regimes=dict(self._regime_stats),
        )

        regime_stats = self._regime_stats.get(current_regime)
        if regime_stats:
            result.regime_stats = regime_stats
            result.risk_multiplier = regime_stats.risk_multiplier
        else:
            # No data for this regime — use default
            result.risk_multiplier = 1.0

        return result

    def get_regime_stats(self, regime: str) -> Optional[RegimeStats]:
        """Get stats for a specific regime."""
        self._ensure_loaded()
        return self._regime_stats.get(regime)

    def get_risk_multiplier(self, regime: str) -> float:
        """Get risk multiplier for a regime."""
        stats = self.get_regime_stats(regime)
        return stats.risk_multiplier if stats else 1.0

    def get_all_regimes(self) -> Dict[str, RegimeStats]:
        """Get stats for all regimes."""
        self._ensure_loaded()
        return dict(self._regime_stats)

    def get_favorable_regimes(self) -> List[str]:
        """Get list of regimes with PF > 1.2."""
        self._ensure_loaded()
        return [
            r for r, s in self._regime_stats.items()
            if s.rolling_pf >= 1.2 and s.rolling_trades >= MIN_TRADES_FOR_REGIME
        ]

    def get_unfavorable_regimes(self) -> List[str]:
        """Get list of regimes with PF < 0.9."""
        self._ensure_loaded()
        return [
            r for r, s in self._regime_stats.items()
            if s.rolling_pf < 0.9 and s.rolling_trades >= MIN_TRADES_FOR_REGIME
        ]

    def get_summary(self) -> Dict[str, Any]:
        """Get complete regime risk summary."""
        self._ensure_loaded()
        return {
            "total_regimes": len(self._regime_stats),
            "favorable": self.get_favorable_regimes(),
            "unfavorable": self.get_unfavorable_regimes(),
            "regimes": {k: v.to_dict() for k, v in self._regime_stats.items()},
        }
