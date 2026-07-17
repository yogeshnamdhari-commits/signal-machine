"""
Session Intelligence — Per-symbol, per-session performance tracking.

Per Priority 3: Learn which sessions work best for each symbol.
    BTC London excellent, ETH New York excellent, DOGE Asia poor

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
class SessionPerformance:
    """Performance metrics for a specific session."""
    session: str = ""
    trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_r: float = 0.0
    total_pnl: float = 0.0
    avg_hold_minutes: float = 0.0
    rating: str = ""  # EXCELLENT / GOOD / AVERAGE / POOR / AVOID

    def to_dict(self) -> Dict:
        return {
            "session": self.session,
            "trades": self.trades,
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 2),
            "avg_r": round(self.avg_r, 3),
            "total_pnl": round(self.total_pnl, 2),
            "rating": self.rating,
        }


@dataclass
class SymbolSessionProfile:
    """Complete session profile for a symbol."""
    symbol: str = ""
    sessions: Dict[str, SessionPerformance] = field(default_factory=dict)
    best_session: str = ""
    worst_session: str = ""
    session_adjustment: float = 1.0  # Multiplier for position sizing

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "sessions": {k: v.to_dict() for k, v in self.sessions.items()},
            "best_session": self.best_session,
            "worst_session": self.worst_session,
            "session_adjustment": round(self.session_adjustment, 2),
        }


class SessionIntelligence:
    """
    Per-symbol, per-session performance tracking.

    Per Priority 3: Learn which sessions work best for each symbol.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._profiles: Dict[str, SymbolSessionProfile] = {}
        self._last_load = 0.0

    def get_session_adjustment(self, symbol: str, session: str) -> float:
        """Get position size adjustment for a symbol+session combination."""
        self._ensure_loaded()
        profile = self._profiles.get(symbol)
        if not profile:
            return 1.0

        sp = profile.sessions.get(session)
        if not sp or sp.trades < 3:
            return 1.0

        # Rating-based adjustment
        ratings = {
            "EXCELLENT": 1.3,
            "GOOD": 1.1,
            "AVERAGE": 1.0,
            "POOR": 0.7,
            "AVOID": 0.0,
        }
        return ratings.get(sp.rating, 1.0)

    def get_best_session(self, symbol: str) -> str:
        """Get the best session for a symbol."""
        self._ensure_loaded()
        profile = self._profiles.get(symbol)
        return profile.best_session if profile else ""

    def get_all_profiles(self) -> Dict[str, SymbolSessionProfile]:
        """Get all session profiles."""
        self._ensure_loaded()
        return dict(self._profiles)

    def _ensure_loaded(self) -> None:
        """Load session data from database."""
        now = time.time()
        if now - self._last_load < 300:
            return

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            cur.execute("""
                SELECT symbol, session,
                       COUNT(*) as n,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                       AVG(pnl) as avg_pnl,
                       AVG(realized_r) as avg_r,
                       SUM(pnl) as total_pnl,
                       AVG(hold_minutes) as avg_hold
                FROM positions
                WHERE status = 'closed' AND session IS NOT NULL AND session != ''
                GROUP BY symbol, session
                HAVING n >= 2
            """)
            rows = cur.fetchall()

            self._profiles.clear()

            for symbol, session, n, wins, avg_pnl, avg_r, total_pnl, avg_hold in rows:
                if symbol not in self._profiles:
                    self._profiles[symbol] = SymbolSessionProfile(symbol=symbol)

                wr = wins / n if n > 0 else 0

                # Calculate profit factor
                cur.execute("""
                    SELECT SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END),
                           SUM(CASE WHEN pnl <= 0 THEN ABS(pnl) ELSE 0 END)
                    FROM positions WHERE symbol = ? AND session = ? AND status = 'closed'
                """, (symbol, session))
                pf_row = cur.fetchone()
                gp = pf_row[0] or 0
                gl = pf_row[1] or 0
                pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)

                # Rate session
                if wr >= 0.50 and pf >= 2.0:
                    rating = "EXCELLENT"
                elif wr >= 0.45 and pf >= 1.5:
                    rating = "GOOD"
                elif wr >= 0.35 and pf >= 1.0:
                    rating = "AVERAGE"
                elif wr >= 0.25 or pf >= 0.5:
                    rating = "POOR"
                else:
                    rating = "AVOID"

                sp = SessionPerformance(
                    session=session,
                    trades=n,
                    win_rate=wr,
                    profit_factor=pf,
                    avg_r=avg_r or 0,
                    total_pnl=total_pnl or 0,
                    avg_hold_minutes=avg_hold or 0,
                    rating=rating,
                )

                self._profiles[symbol].sessions[session] = sp

            # Determine best/worst sessions per symbol
            for symbol, profile in self._profiles.items():
                if profile.sessions:
                    best = max(profile.sessions.values(), key=lambda s: s.avg_r if s.avg_r else 0)
                    worst = min(profile.sessions.values(), key=lambda s: s.avg_r if s.avg_r else 0)
                    profile.best_session = best.session
                    profile.worst_session = worst.session

            conn.close()
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Session intelligence load error: {}", e)
