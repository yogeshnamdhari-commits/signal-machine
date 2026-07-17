"""
Regime Learning — Per-regime performance tracking and auto-adjustment.

Per Priority 4: Track performance in each regime.
    Bull PF 2.4, Bear PF 1.1, Range PF 0.5
    Auto-reduce activity in regimes with poor historical expectancy.

READ-ONLY: never modifies upstream data.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


@dataclass
class RegimePerformance:
    """Performance metrics for a specific regime."""
    regime: str = ""
    trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_r: float = 0.0
    total_pnl: float = 0.0
    expectancy_r: float = 0.0
    rating: str = ""  # STRONG / POSITIVE / NEUTRAL / WEAK / AVOID
    activity_multiplier: float = 1.0  # How much to reduce activity

    def to_dict(self) -> Dict:
        return {
            "regime": self.regime,
            "trades": self.trades,
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 2),
            "avg_r": round(self.avg_r, 3),
            "total_pnl": round(self.total_pnl, 2),
            "expectancy_r": round(self.expectancy_r, 3),
            "rating": self.rating,
            "activity_multiplier": round(self.activity_multiplier, 2),
        }


class RegimeLearning:
    """
    Per-regime performance tracking and auto-adjustment.

    Per Priority 4: Auto-reduce activity in poor regimes.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._regime_data: Dict[str, RegimePerformance] = {}
        self._last_load = 0.0

    def get_activity_multiplier(self, regime: str) -> float:
        """Get activity multiplier for a regime."""
        self._ensure_loaded()
        rp = self._regime_data.get(regime)
        if not rp or rp.trades < 5:
            return 1.0
        return rp.activity_multiplier

    def get_regime_rating(self, regime: str) -> str:
        """Get rating for a regime."""
        self._ensure_loaded()
        rp = self._regime_data.get(regime)
        return rp.rating if rp else "UNKNOWN"

    def should_trade_in_regime(self, regime: str) -> bool:
        """Determine if we should trade in this regime."""
        self._ensure_loaded()
        rp = self._regime_data.get(regime)
        if not rp or rp.trades < 5:
            return True  # Unknown — allow
        return rp.activity_multiplier > 0.3

    def get_all_regimes(self) -> Dict[str, RegimePerformance]:
        """Get all regime performance data."""
        self._ensure_loaded()
        return dict(self._regime_data)

    def _ensure_loaded(self) -> None:
        """Load regime data from database."""
        now = time.time()
        if now - self._last_load < 300:
            return

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            cur.execute("""
                SELECT regime,
                       COUNT(*) as n,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                       AVG(pnl) as avg_pnl,
                       AVG(realized_r) as avg_r,
                       SUM(pnl) as total_pnl
                FROM positions
                WHERE status = 'closed' AND regime IS NOT NULL AND regime != '' AND regime != '0.0'
                GROUP BY regime
            """)
            rows = cur.fetchall()

            self._regime_data.clear()

            for regime, n, wins, avg_pnl, avg_r, total_pnl in rows:
                wr = wins / n if n > 0 else 0

                # Calculate profit factor
                cur.execute("""
                    SELECT SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END),
                           SUM(CASE WHEN pnl <= 0 THEN ABS(pnl) ELSE 0 END)
                    FROM positions WHERE regime = ? AND status = 'closed'
                """, (regime,))
                pf_row = cur.fetchone()
                gp = pf_row[0] or 0
                gl = pf_row[1] or 0
                pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)

                # Calculate expectancy
                ev = (wr * (avg_pnl if avg_pnl and avg_pnl > 0 else 0)) - \
                     ((1 - wr) * abs(avg_pnl if avg_pnl and avg_pnl < 0 else 0))

                # Rate regime
                if pf >= 2.0 and wr >= 0.45:
                    rating = "STRONG"
                    multiplier = 1.2
                elif pf >= 1.5 and wr >= 0.40:
                    rating = "POSITIVE"
                    multiplier = 1.0
                elif pf >= 1.0 and wr >= 0.35:
                    rating = "NEUTRAL"
                    multiplier = 0.8
                elif pf >= 0.7:
                    rating = "WEAK"
                    multiplier = 0.5
                else:
                    rating = "AVOID"
                    multiplier = 0.2

                rp = RegimePerformance(
                    regime=regime,
                    trades=n,
                    win_rate=wr,
                    profit_factor=pf,
                    avg_r=avg_r or 0,
                    total_pnl=total_pnl or 0,
                    expectancy_r=ev,
                    rating=rating,
                    activity_multiplier=multiplier,
                )

                self._regime_data[regime] = rp

            conn.close()
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Regime learning load error: {}", e)
