"""
Continuous Learning Layer — Feedback loop from closed trades.

Per Executive Assessment v2:
    "Every closed trade should automatically update:
        - Symbol Profit Factor
        - Session Profit Factor
        - Exit Efficiency
        - Average MFE
        - Average MAE
        - Average Hold Time
        - Average Slippage
        - Average Fees
        - Average Net R

    Then use those statistics in the next execution decision.
    This creates a feedback loop without modifying Smart Money or EMA V5."

Also implements:
    - Symbol Performance Filter (disable persistently unprofitable symbols)
    - Session Performance Filter (trade only profitable time windows)

READ-ONLY: Never modifies upstream data. Only reads trade history and
writes learning statistics.
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Minimum trades before statistics are reliable
MIN_TRADES_FOR_STATS = 10

# Symbol performance thresholds
SYMBOL_BLOCK_PF = 0.7        # Block symbols with PF < 0.7 after 20+ trades
SYMBOL_WARN_PF = 0.85        # Warn about symbols with PF < 0.85
SYMBOL_REDUCE_PF = 0.95      # Reduce size for symbols with PF < 0.95
SYMBOL_BOOST_PF = 1.3        # Boost size for symbols with PF > 1.3
SYMBOL_TRADES_FOR_BLOCK = 20 # Minimum trades before blocking a symbol

# Session performance thresholds
SESSION_BLOCK_PF = 0.75      # Block sessions with PF < 0.75
SESSION_REDUCE_PF = 0.9      # Reduce size for sessions with PF < 0.9
SESSION_TRADES_FOR_BLOCK = 15 # Minimum trades before blocking a session

# Lookback window (days) for recent performance
RECENT_LOOKBACK_DAYS = 30


@dataclass
class SymbolStats:
    """Performance statistics for a single symbol."""
    symbol: str = ""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_r: float = 0.0
    avg_winner_r: float = 0.0
    avg_loser_r: float = 0.0
    total_pnl: float = 0.0
    avg_mfe_r: float = 0.0
    avg_mae_r: float = 0.0
    avg_hold_minutes: float = 0.0
    avg_fees: float = 0.0
    exit_efficiency: float = 0.0  # avg profit captured / avg MFE
    rating: str = ""              # EXCELLENT / GOOD / AVERAGE / POOR / AVOID
    size_adjustment: float = 1.0  # Position size multiplier

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 2),
            "avg_r": round(self.avg_r, 3),
            "avg_winner_r": round(self.avg_winner_r, 3),
            "avg_loser_r": round(self.avg_loser_r, 3),
            "total_pnl": round(self.total_pnl, 2),
            "avg_mfe_r": round(self.avg_mfe_r, 3),
            "avg_mae_r": round(self.avg_mae_r, 3),
            "avg_hold_minutes": round(self.avg_hold_minutes, 1),
            "avg_fees": round(self.avg_fees, 2),
            "exit_efficiency": round(self.exit_efficiency, 1),
            "rating": self.rating,
            "size_adjustment": round(self.size_adjustment, 2),
        }


@dataclass
class SessionStats:
    """Performance statistics for a single session."""
    session: str = ""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_r: float = 0.0
    total_pnl: float = 0.0
    avg_hold_minutes: float = 0.0
    rating: str = ""
    size_adjustment: float = 1.0

    def to_dict(self) -> Dict:
        return {
            "session": self.session,
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 2),
            "avg_r": round(self.avg_r, 3),
            "total_pnl": round(self.total_pnl, 2),
            "avg_hold_minutes": round(self.avg_hold_minutes, 1),
            "rating": self.rating,
            "size_adjustment": round(self.size_adjustment, 2),
        }


@dataclass
class StrategyStats:
    """Aggregate strategy performance statistics."""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_r: float = 0.0
    total_pnl: float = 0.0
    avg_exit_efficiency: float = 0.0
    avg_mfe_r: float = 0.0
    avg_mae_r: float = 0.0
    avg_hold_minutes: float = 0.0
    recent_pf_7d: float = 0.0   # Last 7 days
    recent_pf_30d: float = 0.0  # Last 30 days

    def to_dict(self) -> Dict:
        return {
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 2),
            "avg_r": round(self.avg_r, 3),
            "total_pnl": round(self.total_pnl, 2),
            "avg_exit_efficiency": round(self.avg_exit_efficiency, 1),
            "avg_mfe_r": round(self.avg_mfe_r, 3),
            "avg_mae_r": round(self.avg_mae_r, 3),
            "avg_hold_minutes": round(self.avg_hold_minutes, 1),
            "recent_pf_7d": round(self.recent_pf_7d, 2),
            "recent_pf_30d": round(self.recent_pf_30d, 2),
        }


class ContinuousLearningLayer:
    """
    Feedback loop from closed trades to improve future execution decisions.

    Every closed trade updates:
        - Symbol statistics (PF, win rate, MFE, MAE, exit efficiency)
        - Session statistics (PF, win rate, avg R)
        - Strategy statistics (overall PF, recent performance)

    These statistics feed back into:
        - Execution Eligibility Engine (symbol/session performance scores)
        - Position Sizing Engine (size adjustments)
        - Adaptive Risk Engine (risk multipliers)

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._symbol_stats: Dict[str, SymbolStats] = {}
        self._session_stats: Dict[str, SessionStats] = {}
        self._strategy_stats: Optional[StrategyStats] = None
        self._last_load = 0.0

    # ═══════════════════════════════════════════════════════════════
    # DATA LOADING
    # ═══════════════════════════════════════════════════════════════

    def _ensure_loaded(self) -> None:
        """Load statistics from DB if stale (> 5 minutes)."""
        if time.time() - self._last_load < 300:
            return
        self._load_stats()

    def _load_stats(self) -> None:
        """Load all statistics from positions_archive table."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # ── Load all closed trades ──
            cursor.execute("""
                SELECT symbol, side, entry_price, quantity, pnl, fees,
                       hold_minutes, exit_reason, mfe_pct, mae_pct,
                       highest_pnl, realized_r, session, opened_at,
                       closed_at, confidence, regime, institutional_score,
                       risk_reward
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                logger.info("📊 No closed trades in archive for learning")
                return

            # ── Aggregate by symbol ──
            symbol_trades = defaultdict(list)
            session_trades = defaultdict(list)
            all_trades = []

            for row in rows:
                trade = dict(row)
                sym = trade.get("symbol", "")
                sess = trade.get("session", "unknown")
                symbol_trades[sym].append(trade)
                session_trades[sess].append(trade)
                all_trades.append(trade)

            # ── Calculate symbol stats ──
            self._symbol_stats = {}
            for sym, trades in symbol_trades.items():
                self._symbol_stats[sym] = self._calc_symbol_stats(sym, trades)

            # ── Calculate session stats ──
            self._session_stats = {}
            for sess, trades in session_trades.items():
                self._session_stats[sess] = self._calc_session_stats(sess, trades)

            # ── Calculate strategy stats ──
            self._strategy_stats = self._calc_strategy_stats(all_trades)

            self._last_load = time.time()

            logger.info(
                "📊 Learning loaded: {} symbols, {} sessions, {} total trades",
                len(self._symbol_stats), len(self._session_stats), len(all_trades),
            )

        except Exception as e:
            logger.warning("Could not load learning stats: {}", e)

    def _calc_symbol_stats(self, symbol: str, trades: List[Dict]) -> SymbolStats:
        """Calculate statistics for a single symbol."""
        stats = SymbolStats(symbol=symbol, total_trades=len(trades))

        wins = []
        losses = []
        mfe_vals = []
        mae_vals = []
        hold_vals = []
        fee_vals = []
        exit_effs = []

        for t in trades:
            pnl = t.get("pnl", 0) or 0
            realized_r = t.get("realized_r", 0) or 0
            mfe = t.get("highest_pnl", 0) or 0
            mae = t.get("mae_pct", 0) or 0
            hold = t.get("hold_minutes", 0) or 0
            fees = t.get("fees", 0) or 0

            if pnl > 0:
                stats.wins += 1
                wins.append(realized_r)
            else:
                stats.losses += 1
                losses.append(abs(realized_r))

            if mfe > 0:
                mfe_vals.append(mfe)
                # Exit efficiency = realized R / MFE R
                if mfe > 0:
                    eff = max(0, min(100, (realized_r / mfe) * 100))
                    exit_effs.append(eff)

            mae_vals.append(abs(mae))
            hold_vals.append(hold)
            fee_vals.append(fees)
            stats.total_pnl += pnl

        # ── Derived metrics ──
        stats.win_rate = stats.wins / max(1, stats.total_trades)
        stats.avg_r = sum(wins + [-l for l in losses]) / max(1, stats.total_trades)
        stats.avg_winner_r = sum(wins) / max(1, len(wins))
        stats.avg_loser_r = sum(losses) / max(1, len(losses))
        stats.avg_mfe_r = sum(mfe_vals) / max(1, len(mfe_vals))
        stats.avg_mae_r = sum(mae_vals) / max(1, len(mae_vals))
        stats.avg_hold_minutes = sum(hold_vals) / max(1, len(hold_vals))
        stats.avg_fees = sum(fee_vals) / max(1, len(fee_vals))
        stats.exit_efficiency = sum(exit_effs) / max(1, len(exit_effs))

        # ── Profit Factor ──
        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        stats.profit_factor = gross_profit / max(0.01, gross_loss)

        # ── Rating ──
        stats.rating, stats.size_adjustment = self._rate_symbol(stats)

        return stats

    def _calc_session_stats(self, session: str, trades: List[Dict]) -> SessionStats:
        """Calculate statistics for a single session."""
        stats = SessionStats(session=session, total_trades=len(trades))

        wins = []
        losses = []
        hold_vals = []

        for t in trades:
            pnl = t.get("pnl", 0) or 0
            realized_r = t.get("realized_r", 0) or 0
            hold = t.get("hold_minutes", 0) or 0

            if pnl > 0:
                stats.wins += 1
                wins.append(realized_r)
            else:
                stats.losses += 1
                losses.append(abs(realized_r))

            hold_vals.append(hold)
            stats.total_pnl += pnl

        stats.win_rate = stats.wins / max(1, stats.total_trades)
        stats.avg_r = sum(wins + [-l for l in losses]) / max(1, stats.total_trades)
        stats.avg_hold_minutes = sum(hold_vals) / max(1, len(hold_vals))

        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        stats.profit_factor = gross_profit / max(0.01, gross_loss)

        # ── Rating ──
        if stats.total_trades < MIN_TRADES_FOR_STATS:
            stats.rating = "INSUFFICIENT"
            stats.size_adjustment = 1.0
        elif stats.profit_factor >= 1.5:
            stats.rating = "EXCELLENT"
            stats.size_adjustment = 1.2
        elif stats.profit_factor >= 1.2:
            stats.rating = "GOOD"
            stats.size_adjustment = 1.1
        elif stats.profit_factor >= 0.9:
            stats.rating = "AVERAGE"
            stats.size_adjustment = 1.0
        elif stats.profit_factor >= SESSION_BLOCK_PF:
            stats.rating = "POOR"
            stats.size_adjustment = 0.6
        else:
            stats.rating = "AVOID"
            stats.size_adjustment = 0.0

        return stats

    def _calc_strategy_stats(self, trades: List[Dict]) -> StrategyStats:
        """Calculate aggregate strategy statistics."""
        stats = StrategyStats(total_trades=len(trades))

        wins = []
        losses = []
        mfe_vals = []
        mae_vals = []
        hold_vals = []
        exit_effs = []
        recent_7d = []
        recent_30d = []
        now = time.time()

        for t in trades:
            pnl = t.get("pnl", 0) or 0
            realized_r = t.get("realized_r", 0) or 0
            mfe = t.get("highest_pnl", 0) or 0
            mae = t.get("mae_pct", 0) or 0
            hold = t.get("hold_minutes", 0) or 0
            closed_at = t.get("closed_at", 0) or 0

            if pnl > 0:
                stats.wins += 1
                wins.append(realized_r)
            else:
                stats.losses += 1
                losses.append(abs(realized_r))

            if mfe > 0:
                mfe_vals.append(mfe)
                if mfe > 0:
                    eff = max(0, min(100, (realized_r / mfe) * 100))
                    exit_effs.append(eff)

            mae_vals.append(abs(mae))
            hold_vals.append(hold)
            stats.total_pnl += pnl

            # Recent performance
            if closed_at:
                age_days = (now - closed_at) / 86400
                if age_days <= 7:
                    recent_7d.append(realized_r)
                if age_days <= 30:
                    recent_30d.append(realized_r)

        stats.win_rate = stats.wins / max(1, stats.total_trades)
        stats.avg_r = sum(wins + [-l for l in losses]) / max(1, stats.total_trades)
        stats.avg_mfe_r = sum(mfe_vals) / max(1, len(mfe_vals))
        stats.avg_mae_r = sum(mae_vals) / max(1, len(mae_vals))
        stats.avg_hold_minutes = sum(hold_vals) / max(1, len(hold_vals))
        stats.avg_exit_efficiency = sum(exit_effs) / max(1, len(exit_effs))

        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        stats.profit_factor = gross_profit / max(0.01, gross_loss)

        # Recent PFs
        if recent_7d:
            r7_wins = [r for r in recent_7d if r > 0]
            r7_losses = [abs(r) for r in recent_7d if r < 0]
            stats.recent_pf_7d = sum(r7_wins) / max(0.01, sum(r7_losses))
        if recent_30d:
            r30_wins = [r for r in recent_30d if r > 0]
            r30_losses = [abs(r) for r in recent_30d if r < 0]
            stats.recent_pf_30d = sum(r30_wins) / max(0.01, sum(r30_losses))

        return stats

    def _rate_symbol(self, stats: SymbolStats) -> Tuple[str, float]:
        """Rate a symbol and determine size adjustment."""
        if stats.total_trades < MIN_TRADES_FOR_STATS:
            return "INSUFFICIENT", 1.0

        pf = stats.profit_factor
        if pf >= 1.5:
            return "EXCELLENT", 1.3
        elif pf >= 1.2:
            return "GOOD", 1.15
        elif pf >= 1.0:
            return "AVERAGE", 1.0
        elif pf >= SYMBOL_REDUCE_PF:
            return "POOR", 0.7
        elif pf >= SYMBOL_BLOCK_PF and stats.total_trades >= SYMBOL_TRADES_FOR_BLOCK:
            return "AVOID", 0.3
        elif pf < SYMBOL_BLOCK_PF and stats.total_trades >= SYMBOL_TRADES_FOR_BLOCK:
            return "BLOCKED", 0.0
        return "AVERAGE", 1.0

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════

    def get_symbol_stats(self, symbol: str) -> Optional[SymbolStats]:
        """Get statistics for a specific symbol."""
        self._ensure_loaded()
        return self._symbol_stats.get(symbol)

    def get_session_stats(self, session: str) -> Optional[SessionStats]:
        """Get statistics for a specific session."""
        self._ensure_loaded()
        return self._session_stats.get(session)

    def get_strategy_stats(self) -> Optional[StrategyStats]:
        """Get aggregate strategy statistics."""
        self._ensure_loaded()
        return self._strategy_stats

    def get_symbol_pf(self, symbol: str) -> float:
        """Get profit factor for a symbol (0 = no data)."""
        stats = self.get_symbol_stats(symbol)
        return stats.profit_factor if stats else 0

    def get_session_pf(self, session: str) -> float:
        """Get profit factor for a session (0 = no data)."""
        stats = self.get_session_stats(session)
        return stats.profit_factor if stats else 0

    def get_strategy_pf(self) -> float:
        """Get overall strategy profit factor."""
        stats = self.get_strategy_stats()
        return stats.profit_factor if stats else 1.0

    def get_symbol_adjustment(self, symbol: str) -> float:
        """Get position size adjustment for a symbol."""
        stats = self.get_symbol_stats(symbol)
        return stats.size_adjustment if stats else 1.0

    def get_session_adjustment(self, session: str) -> float:
        """Get position size adjustment for a session."""
        stats = self.get_session_stats(session)
        return stats.size_adjustment if stats else 1.0

    def is_symbol_blocked(self, symbol: str) -> bool:
        """Check if a symbol should be blocked from trading."""
        stats = self.get_symbol_stats(symbol)
        if not stats:
            return False
        return (
            stats.rating == "BLOCKED"
            or (stats.rating == "AVOID" and stats.total_trades >= SYMBOL_TRADES_FOR_BLOCK)
        )

    def is_session_blocked(self, session: str) -> bool:
        """Check if a session should be blocked from trading."""
        stats = self.get_session_stats(session)
        if not stats:
            return False
        return (
            stats.rating == "AVOID"
            and stats.total_trades >= SESSION_TRADES_FOR_BLOCK
        )

    def get_all_symbol_stats(self) -> Dict[str, SymbolStats]:
        """Get statistics for all symbols."""
        self._ensure_loaded()
        return dict(self._symbol_stats)

    def get_all_session_stats(self) -> Dict[str, SessionStats]:
        """Get statistics for all sessions."""
        self._ensure_loaded()
        return dict(self._session_stats)

    def get_blocked_symbols(self) -> List[str]:
        """Get list of symbols that should be blocked."""
        self._ensure_loaded()
        return [
            sym for sym, stats in self._symbol_stats.items()
            if stats.rating in ("BLOCKED", "AVOID")
            and stats.total_trades >= SYMBOL_TRADES_FOR_BLOCK
        ]

    def get_top_symbols(self, n: int = 10) -> List[SymbolStats]:
        """Get top N symbols by profit factor."""
        self._ensure_loaded()
        sorted_stats = sorted(
            self._symbol_stats.values(),
            key=lambda s: s.profit_factor,
            reverse=True,
        )
        return sorted_stats[:n]

    def get_worst_symbols(self, n: int = 10) -> List[SymbolStats]:
        """Get worst N symbols by profit factor."""
        self._ensure_loaded()
        sorted_stats = sorted(
            self._symbol_stats.values(),
            key=lambda s: s.profit_factor,
        )
        return sorted_stats[:n]

    def get_summary(self) -> Dict[str, Any]:
        """Get complete learning summary."""
        self._ensure_loaded()
        return {
            "symbols_tracked": len(self._symbol_stats),
            "sessions_tracked": len(self._session_stats),
            "blocked_symbols": self.get_blocked_symbols(),
            "strategy": self._strategy_stats.to_dict() if self._strategy_stats else {},
            "top_symbols": [s.to_dict() for s in self.get_top_symbols(5)],
            "worst_symbols": [s.to_dict() for s in self.get_worst_symbols(5)],
            "sessions": {k: v.to_dict() for k, v in self._session_stats.items()},
        }
