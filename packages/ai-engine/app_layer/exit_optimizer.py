"""
Exit Optimizer — Learn which exit strategies work best per symbol/setup.

Per Priority 5: The current Exit Engine is rule-based.
    TP1 works 62%, TP2 works 71%, Trailing works 84%
    Select the exit style with the highest historical expectancy.

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
class ExitStrategyPerformance:
    """Performance metrics for a specific exit strategy."""
    exit_reason: str = ""
    trades: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    avg_r: float = 0.0
    total_pnl: float = 0.0
    profit_factor: float = 0.0
    rating: str = ""  # EXCELLENT / GOOD / AVERAGE / POOR / AVOID

    def to_dict(self) -> Dict:
        return {
            "exit_reason": self.exit_reason,
            "trades": self.trades,
            "win_rate": round(self.win_rate, 3),
            "avg_pnl": round(self.avg_pnl, 4),
            "avg_r": round(self.avg_r, 3),
            "total_pnl": round(self.total_pnl, 2),
            "profit_factor": round(self.profit_factor, 2),
            "rating": self.rating,
        }


@dataclass
class SymbolExitProfile:
    """Exit strategy profile for a symbol."""
    symbol: str = ""
    strategies: Dict[str, ExitStrategyPerformance] = field(default_factory=dict)
    best_strategy: str = ""
    worst_strategy: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "strategies": {k: v.to_dict() for k, v in self.strategies.items()},
            "best_strategy": self.best_strategy,
            "worst_strategy": self.worst_strategy,
        }


class ExitOptimizer:
    """
    Learn which exit strategies work best per symbol/setup.

    Per Priority 5: Select exit style with highest historical expectancy.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._profiles: Dict[str, SymbolExitProfile] = {}
        self._global_strategies: Dict[str, ExitStrategyPerformance] = {}
        self._last_load = 0.0

    def get_best_exit(self, symbol: str) -> str:
        """Get the best exit strategy for a symbol."""
        self._ensure_loaded()
        profile = self._profiles.get(symbol)
        return profile.best_strategy if profile else ""

    def get_exit_adjustment(self, symbol: str, exit_reason: str) -> float:
        """Get position size adjustment based on exit strategy quality."""
        self._ensure_loaded()

        # Try symbol-specific first
        profile = self._profiles.get(symbol)
        if profile:
            strategy = profile.strategies.get(exit_reason)
            if strategy and strategy.trades >= 3:
                ratings = {
                    "EXCELLENT": 1.3,
                    "GOOD": 1.1,
                    "AVERAGE": 1.0,
                    "POOR": 0.7,
                    "AVOID": 0.0,
                }
                return ratings.get(strategy.rating, 1.0)

        # Fall back to global
        strategy = self._global_strategies.get(exit_reason)
        if strategy and strategy.trades >= 10:
            ratings = {
                "EXCELLENT": 1.2,
                "GOOD": 1.1,
                "AVERAGE": 1.0,
                "POOR": 0.8,
                "AVOID": 0.5,
            }
            return ratings.get(strategy.rating, 1.0)

        return 1.0

    def get_all_profiles(self) -> Dict[str, SymbolExitProfile]:
        """Get all exit profiles."""
        self._ensure_loaded()
        return dict(self._profiles)

    def _ensure_loaded(self) -> None:
        """Load exit data from database."""
        now = time.time()
        if now - self._last_load < 300:
            return

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            # Global exit performance
            cur.execute("""
                SELECT exit_reason,
                       COUNT(*) as n,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                       AVG(pnl) as avg_pnl,
                       AVG(realized_r) as avg_r,
                       SUM(pnl) as total_pnl
                FROM positions
                WHERE status = 'closed' AND exit_reason IS NOT NULL AND exit_reason != ''
                GROUP BY exit_reason
                HAVING n >= 3
            """)
            rows = cur.fetchall()

            self._global_strategies.clear()
            for exit_reason, n, wins, avg_pnl, avg_r, total_pnl in rows:
                wr = wins / n if n > 0 else 0

                # Profit factor
                cur.execute("""
                    SELECT SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END),
                           SUM(CASE WHEN pnl <= 0 THEN ABS(pnl) ELSE 0 END)
                    FROM positions WHERE exit_reason = ? AND status = 'closed'
                """, (exit_reason,))
                pf_row = cur.fetchone()
                gp = pf_row[0] or 0
                gl = pf_row[1] or 0
                pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)

                rating = self._rate_strategy(wr, pf, avg_pnl or 0)

                self._global_strategies[exit_reason] = ExitStrategyPerformance(
                    exit_reason=exit_reason,
                    trades=n,
                    win_rate=wr,
                    avg_pnl=avg_pnl or 0,
                    avg_r=avg_r or 0,
                    total_pnl=total_pnl or 0,
                    profit_factor=pf,
                    rating=rating,
                )

            # Per-symbol exit performance
            cur.execute("""
                SELECT symbol, exit_reason,
                       COUNT(*) as n,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                       AVG(pnl) as avg_pnl,
                       AVG(realized_r) as avg_r,
                       SUM(pnl) as total_pnl
                FROM positions
                WHERE status = 'closed' AND exit_reason IS NOT NULL AND exit_reason != ''
                GROUP BY symbol, exit_reason
                HAVING n >= 2
            """)
            rows = cur.fetchall()

            self._profiles.clear()
            for symbol, exit_reason, n, wins, avg_pnl, avg_r, total_pnl in rows:
                if symbol not in self._profiles:
                    self._profiles[symbol] = SymbolExitProfile(symbol=symbol)

                wr = wins / n if n > 0 else 0

                cur.execute("""
                    SELECT SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END),
                           SUM(CASE WHEN pnl <= 0 THEN ABS(pnl) ELSE 0 END)
                    FROM positions WHERE symbol = ? AND exit_reason = ? AND status = 'closed'
                """, (symbol, exit_reason))
                pf_row = cur.fetchone()
                gp = pf_row[0] or 0
                gl = pf_row[1] or 0
                pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)

                rating = self._rate_strategy(wr, pf, avg_pnl or 0)

                esp = ExitStrategyPerformance(
                    exit_reason=exit_reason,
                    trades=n,
                    win_rate=wr,
                    avg_pnl=avg_pnl or 0,
                    avg_r=avg_r or 0,
                    total_pnl=total_pnl or 0,
                    profit_factor=pf,
                    rating=rating,
                )

                self._profiles[symbol].strategies[exit_reason] = esp

            # Determine best/worst per symbol
            for symbol, profile in self._profiles.items():
                if profile.strategies:
                    best = max(profile.strategies.values(), key=lambda s: s.avg_r)
                    worst = min(profile.strategies.values(), key=lambda s: s.avg_r)
                    profile.best_strategy = best.exit_reason
                    profile.worst_strategy = worst.exit_reason

            conn.close()
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Exit optimizer load error: {}", e)

    @staticmethod
    def _rate_strategy(wr: float, pf: float, avg_pnl: float) -> str:
        """Rate an exit strategy."""
        if pf >= 2.0 and wr >= 0.50 and avg_pnl > 0:
            return "EXCELLENT"
        elif pf >= 1.5 and wr >= 0.40 and avg_pnl > 0:
            return "GOOD"
        elif pf >= 1.0 and wr >= 0.35:
            return "AVERAGE"
        elif pf >= 0.7:
            return "POOR"
        else:
            return "AVOID"
