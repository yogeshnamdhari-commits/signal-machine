"""
Symbol Profiles — Per-symbol statistics and optimal parameters.

Per Priority 2: Every coin behaves differently.
    BTC: Best RR, Best Session, Best Hold Time, Best Exit, Best Confidence
    ETH: Best RR, Best Session, Best Hold Time, Best Exit, Best Confidence
    DOGE: Best RR, Best Session, Best Hold Time, Best Exit, Best Confidence

This module maintains per-symbol profiles that influence future decisions.

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

# Minimum trades per symbol for profile reliability
MIN_TRADES_FOR_PROFILE = 5


@dataclass
class SymbolProfile:
    """Complete profile for a single symbol."""
    symbol: str = ""
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_r: float = 0.0
    expectancy_r: float = 0.0
    total_pnl: float = 0.0

    # Optimal parameters (learned from history)
    best_session: str = ""
    best_regime: str = ""
    best_confidence_min: float = 0.85
    best_rr_min: float = 2.0
    best_hold_minutes: float = 0.0
    best_exit_reason: str = ""

    # Directional bias
    long_win_rate: float = 0.0
    short_win_rate: float = 0.0
    long_pnl: float = 0.0
    short_pnl: float = 0.0
    preferred_side: str = ""  # LONG / SHORT / NEUTRAL

    # Risk profile
    avg_mae_pct: float = 0.0
    avg_mfe_pct: float = 0.0
    max_drawdown: float = 0.0
    volatility_class: str = ""  # LOW / MEDIUM / HIGH

    # Session performance
    session_performance: Dict[str, Dict] = field(default_factory=dict)

    # Regime performance
    regime_performance: Dict[str, Dict] = field(default_factory=dict)

    # Metadata
    last_updated: float = 0.0
    confidence_score: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "total_trades": self.total_trades,
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 2),
            "avg_r": round(self.avg_r, 3),
            "expectancy_r": round(self.expectancy_r, 3),
            "total_pnl": round(self.total_pnl, 2),
            "best_session": self.best_session,
            "best_regime": self.best_regime,
            "best_confidence_min": round(self.best_confidence_min, 3),
            "best_rr_min": round(self.best_rr_min, 2),
            "best_hold_minutes": round(self.best_hold_minutes, 0),
            "best_exit_reason": self.best_exit_reason,
            "preferred_side": self.preferred_side,
            "long_win_rate": round(self.long_win_rate, 3),
            "short_win_rate": round(self.short_win_rate, 3),
            "long_pnl": round(self.long_pnl, 2),
            "short_pnl": round(self.short_pnl, 2),
            "avg_mae_pct": round(self.avg_mae_pct, 2),
            "avg_mfe_pct": round(self.avg_mfe_pct, 2),
            "volatility_class": self.volatility_class,
            "session_performance": self.session_performance,
            "regime_performance": self.regime_performance,
            "confidence_score": round(self.confidence_score, 2),
        }


class SymbolProfiles:
    """
    Maintains per-symbol statistics and optimal parameters.

    Per Priority 2: Every coin behaves differently.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._profiles: Dict[str, SymbolProfile] = {}
        self._last_load = 0.0

    def get_profile(self, symbol: str) -> Optional[SymbolProfile]:
        """Get profile for a symbol."""
        self._ensure_loaded()
        return self._profiles.get(symbol)

    def get_all_profiles(self) -> Dict[str, SymbolProfile]:
        """Get all symbol profiles."""
        self._ensure_loaded()
        return dict(self._profiles)

    def get_ranked_symbols(self, min_trades: int = 5) -> List[SymbolProfile]:
        """Get symbols ranked by expectancy."""
        self._ensure_loaded()
        profiles = [p for p in self._profiles.values() if p.total_trades >= min_trades]
        profiles.sort(key=lambda p: p.expectancy_r, reverse=True)
        return profiles

    def get_symbol_adjustments(self, symbol: str) -> Dict[str, Any]:
        """Get parameter adjustments for a symbol."""
        profile = self.get_profile(symbol)
        if not profile or profile.total_trades < MIN_TRADES_FOR_PROFILE:
            return {}

        adjustments = {}

        # Adjust confidence minimum based on symbol performance
        if profile.expectancy_r > 0:
            # Good symbol — can lower threshold slightly
            adjustments["confidence_min"] = max(0.75, profile.best_confidence_min - 0.05)
        else:
            # Bad symbol — raise threshold
            adjustments["confidence_min"] = min(0.95, profile.best_confidence_min + 0.05)

        # Adjust R:R minimum
        if profile.best_rr_min > 0:
            adjustments["rr_min"] = profile.best_rr_min

        # Adjust position size multiplier
        if profile.profit_factor > 1.5:
            adjustments["size_multiplier"] = 1.2  # Good symbol — larger size
        elif profile.profit_factor < 0.8:
            adjustments["size_multiplier"] = 0.6  # Bad symbol — smaller size
        else:
            adjustments["size_multiplier"] = 1.0

        # Preferred side
        if profile.preferred_side:
            adjustments["preferred_side"] = profile.preferred_side

        return adjustments

    def _ensure_loaded(self) -> None:
        """Load profiles from database if stale."""
        now = time.time()
        if now - self._last_load < 300:  # Refresh every 5 min
            return

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Get all symbols with trades
            cur.execute("""
                SELECT symbol,
                       COUNT(*) as n,
                       SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                       SUM(pnl) as total_pnl,
                       AVG(realized_r) as avg_r,
                       AVG(CASE WHEN side = 'LONG' THEN pnl END) as long_pnl,
                       AVG(CASE WHEN side = 'SHORT' THEN pnl END) as short_pnl,
                       SUM(CASE WHEN side = 'LONG' THEN 1 ELSE 0 END) as long_count,
                       SUM(CASE WHEN side = 'SHORT' THEN 1 ELSE 0 END) as short_count,
                       SUM(CASE WHEN side = 'LONG' AND pnl > 0 THEN 1 ELSE 0 END) as long_wins,
                       SUM(CASE WHEN side = 'SHORT' AND pnl > 0 THEN 1 ELSE 0 END) as short_wins,
                       AVG(mae_pct) as avg_mae,
                       AVG(mfe_pct) as avg_mfe,
                       AVG(hold_minutes) as avg_hold
                FROM positions
                WHERE status = 'closed'
                GROUP BY symbol
                HAVING n >= 2
            """)
            rows = cur.fetchall()

            self._profiles.clear()

            for row in rows:
                d = dict(row)
                symbol = d["symbol"]
                n = d["n"]

                wr = d["wins"] / n if n > 0 else 0
                long_wr = d["long_wins"] / d["long_count"] if d["long_count"] and d["long_count"] > 0 else 0
                short_wr = d["short_wins"] / d["short_count"] if d["short_count"] and d["short_count"] > 0 else 0

                # Profit factor
                cur.execute("""
                    SELECT SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END),
                           SUM(CASE WHEN pnl <= 0 THEN ABS(pnl) ELSE 0 END)
                    FROM positions WHERE symbol = ? AND status = 'closed'
                """, (symbol,))
                pf_row = cur.fetchone()
                gp = pf_row[0] or 0
                gl = pf_row[1] or 0
                pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)

                # Best session
                cur.execute("""
                    SELECT session, AVG(pnl) as avg_pnl, COUNT(*) as n
                    FROM positions WHERE symbol = ? AND status = 'closed' AND session IS NOT NULL
                    GROUP BY session ORDER BY avg_pnl DESC LIMIT 1
                """, (symbol,))
                sess_row = cur.fetchone()
                best_session = sess_row[0] if sess_row else ""

                # Best regime
                cur.execute("""
                    SELECT regime, AVG(pnl) as avg_pnl, COUNT(*) as n
                    FROM positions WHERE symbol = ? AND status = 'closed' AND regime IS NOT NULL
                    GROUP BY regime ORDER BY avg_pnl DESC LIMIT 1
                """, (symbol,))
                regime_row = cur.fetchone()
                best_regime = regime_row[0] if regime_row else ""

                # Best exit
                cur.execute("""
                    SELECT exit_reason, AVG(pnl) as avg_pnl, COUNT(*) as n
                    FROM positions WHERE symbol = ? AND status = 'closed' AND exit_reason IS NOT NULL
                    GROUP BY exit_reason HAVING n >= 2 ORDER BY avg_pnl DESC LIMIT 1
                """, (symbol,))
                exit_row = cur.fetchone()
                best_exit = exit_row[0] if exit_row else ""

                # Preferred side
                if long_pnl := (d["long_pnl"] or 0) > (d["short_pnl"] or 0):
                    preferred = "LONG" if (d["long_pnl"] or 0) > 0 else "NEUTRAL"
                else:
                    preferred = "SHORT" if (d["short_pnl"] or 0) > 0 else "NEUTRAL"

                profile = SymbolProfile(
                    symbol=symbol,
                    total_trades=n,
                    win_rate=wr,
                    profit_factor=pf,
                    avg_r=d["avg_r"] or 0,
                    expectancy_r=(wr * 1.5 - (1 - wr) * 1.0),  # Estimated EV
                    total_pnl=d["total_pnl"] or 0,
                    best_session=best_session,
                    best_regime=best_regime,
                    best_confidence_min=0.85,
                    best_rr_min=2.0,
                    best_hold_minutes=d["avg_hold"] or 0,
                    best_exit_reason=best_exit,
                    preferred_side=preferred,
                    long_win_rate=long_wr,
                    short_win_rate=short_wr,
                    long_pnl=d["long_pnl"] or 0,
                    short_pnl=d["short_pnl"] or 0,
                    avg_mae_pct=d["avg_mae"] or 0,
                    avg_mfe_pct=d["avg_mfe"] or 0,
                    last_updated=time.time(),
                    confidence_score=min(n / 20, 1.0),
                )

                self._profiles[symbol] = profile

            conn.close()
            self._last_load = time.time()

            logger.debug("Symbol profiles loaded: {} symbols", len(self._profiles))

        except Exception as e:
            logger.warning("Symbol profile load error: {}", e)

    def force_reload(self) -> None:
        """Force profile reload."""
        self._last_load = 0.0
