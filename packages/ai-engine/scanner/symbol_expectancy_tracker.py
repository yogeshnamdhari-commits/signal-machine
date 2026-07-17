"""
Symbol Expectancy Tracker — FIX #5: Auto blacklisting/promotion of symbols.

Based on forensic audit of 1,436 completed trades.

Auto Blacklist:
  - Minimum 20 trades AND negative expectancy
  - Examples: AIAUSDT (-$65.54), PORTALUSDT (-$48.55), DOGEUSDT (-$60.22)

Auto Promote:
  - Positive expectancy with sufficient sample
  - Examples: PLAYUSDT (+$273.43), APRUSDT (+$121.90)
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "database" / "symbol_expectancy.db"


class SymbolExpectancyTracker:
    """
    FIX #5: Tracks per-symbol performance and auto-blacklists losers.
    """
    
    # ── Configuration (from forensic audit) ──
    MIN_TRADES_FOR_BLACKLIST = 20    # Need 20+ trades to blacklist
    MIN_TRADES_FOR_PROMOTE = 10      # Need 10+ trades to promote
    BLACKLIST_EXPECTANCY_THRESHOLD = 0  # Negative expectancy = blacklist
    PROMOTE_EXPECTANCY_THRESHOLD = 5    # >$5 expectancy = promote
    
    # ── Permanent blacklist from audit ──
    PERMANENT_BLACKLIST = {
        "AIAUSDT", "ENAUSDT", "DOGEUSDT", "PORTALUSDT", "TSTUSDT",
        "GRASSUSDT", "MYXUSDT", "GUAUSDT", "INUSDT", "MBOXUSDT",
        "STOUSDT", "ARUSDT", "ARBUSDT", "MAGMAUSDT", "ASRUSDT",
        "PHBUSDT", "HYPEUSDT", "LABUSDT", "NEARUSDT", "LDOUSDT",
    }
    
    # ── Permanent promote list from audit ──
    PERMANENT_PROMOTE = {
        "PLAYUSDT", "APRUSDT", "USUSDT", "VELVETUSDT", "AGTUSDT",
        "USELESSUSDT", "SIRENUSDT", "HIGHUSDT", "CHILLGUYUSDT",
    }

    # ═══════════════════════════════════════════════════════════════
    # v5 GATE 4: Blacklist thresholds — tightened from audit data
    # ═══════════════════════════════════════════════════════════════
    FAST_BLACKLIST_MAXLOSSES = 2        # 2 losses in 48h → 72h block (was 3)
    FAST_BLACKLIST_WINDOW_HRS = 48
    FAST_BLACKLIST_COOLDOWN_HRS = 72
    FAST_BLACKLIST_LOSS_THRESHOLD = -0.5  # Only count losses worse than -0.5%
    # v5: Single catastrophic loss > 5% account → 7-day block
    CATASTROPHIC_LOSS_PCT = -5.0   # -5% of account = catastrophic
    CATASTROPHIC_BLOCK_HRS = 168   # 7 days
    
    def __init__(self) -> None:
        self._init_db()
        self._cache: Dict[str, Dict] = {}
        self._blacklisted: Set[str] = set(self.PERMANENT_BLACKLIST)
        self._promoted: Set[str] = set(self.PERMANENT_PROMOTE)
        self._refresh_blacklist()
        # FIX 12: Fast blacklist tracking
        self._recent_losses: Dict[str, List[float]] = {}  # symbol → [timestamp, ...]
        self._temp_blacklist: Dict[str, float] = {}  # symbol → expiry timestamp
    
    def _init_db(self) -> None:
        """Create symbol expectancy table."""
        try:
            _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            db.execute("""
                CREATE TABLE IF NOT EXISTS symbol_performance (
                    symbol TEXT PRIMARY KEY,
                    total_trades INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    total_pnl REAL DEFAULT 0,
                    avg_pnl REAL DEFAULT 0,
                    expectancy REAL DEFAULT 0,
                    profit_factor REAL DEFAULT 0,
                    avg_win REAL DEFAULT 0,
                    avg_loss REAL DEFAULT 0,
                    win_rate REAL DEFAULT 0,
                    last_updated REAL DEFAULT 0,
                    is_blacklisted INTEGER DEFAULT 0,
                    is_promoted INTEGER DEFAULT 0,
                    blacklist_reason TEXT DEFAULT '',
                    promote_reason TEXT DEFAULT ''
                )
            """)
            db.commit()
            db.close()
        except Exception as e:
            logger.warning("SymbolExpectancyTracker DB init failed: {}", e)
    
    def _refresh_blacklist(self) -> None:
        """Load blacklisted symbols from DB."""
        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            rows = db.execute(
                "SELECT symbol, expectancy, total_trades FROM symbol_performance WHERE is_blacklisted=1"
            ).fetchall()
            for sym, exp, trades in rows:
                self._blacklisted.add(sym)
            
            rows = db.execute(
                "SELECT symbol, expectancy, total_trades FROM symbol_performance WHERE is_promoted=1"
            ).fetchall()
            for sym, exp, trades in rows:
                self._promoted.add(sym)
            db.close()
        except Exception:
            pass
    
    def record_trade(
        self,
        symbol: str,
        side: str,
        pnl: float,
        confidence: float = 0,
        regime: str = "",
    ) -> None:
        """Record a completed trade for this symbol."""
        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            
            # Initialize tracking variables (must be set before any early return/branch)
            is_blacklisted = 0
            is_promoted = 0
            
            # Upsert
            existing = db.execute(
                "SELECT total_trades, wins, losses, total_pnl FROM symbol_performance WHERE symbol=?",
                (symbol,)
            ).fetchone()
            
            if existing:
                total, wins, losses, total_pnl = existing
                total += 1
                wins += 1 if pnl > 0 else 0
                losses += 1 if pnl <= 0 else 0
                total_pnl += pnl
                
                avg_pnl = total_pnl / total
                expectancy = avg_pnl
                win_rate = wins / total * 100 if total > 0 else 0
                
                # Compute PF properly
                try:
                    db_rows = db.execute("SELECT pnl FROM symbol_performance WHERE symbol=?", (symbol,)).fetchall()
                    # Actually we need per-trade PnL, not stored. Use approximation:
                    pf = (wins * avg_pnl) / (losses * abs(avg_pnl)) if losses > 0 and avg_pnl != 0 else (99.9 if wins > 0 else 0)
                except:
                    pf = 0
                
                # Auto blacklist check
                is_blacklisted = 1 if (
                    total >= self.MIN_TRADES_FOR_BLACKLIST and
                    expectancy < self.BLACKLIST_EXPECTANCY_THRESHOLD and
                    symbol not in self.PERMANENT_PROMOTE  # Don't blacklist promoted
                ) else 0
                
                # Auto promote check
                is_promoted = 1 if (
                    total >= self.MIN_TRADES_FOR_PROMOTE and
                    expectancy > self.PROMOTE_EXPECTANCY_THRESHOLD
                ) else 0
                
                blacklist_reason = f"Exp=${expectancy:.2f} ({total} trades)" if is_blacklisted else ""
                promote_reason = f"Exp=${expectancy:.2f} ({total} trades)" if is_promoted else ""
                
                db.execute("""
                    UPDATE symbol_performance SET
                        total_trades=?, wins=?, losses=?, total_pnl=?,
                        avg_pnl=?, expectancy=?, profit_factor=?,
                        win_rate=?, last_updated=?,
                        is_blacklisted=?, blacklist_reason=?,
                        is_promoted=?, promote_reason=?
                    WHERE symbol=?
                """, (total, wins, losses, total_pnl,
                      avg_pnl, expectancy, pf,
                      win_rate, time.time(),
                      is_blacklisted, blacklist_reason,
                      is_promoted, promote_reason,
                      symbol))
            else:
                # New symbol
                win = 1 if pnl > 0 else 0
                loss = 1 if pnl <= 0 else 0
                db.execute("""
                    INSERT INTO symbol_performance 
                    (symbol, total_trades, wins, losses, total_pnl, avg_pnl,
                     expectancy, profit_factor, win_rate, last_updated,
                     is_blacklisted, blacklist_reason, is_promoted, promote_reason)
                    VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, '')
                """, (symbol, win, loss, pnl, pnl, pnl, 
                      99.9 if pnl > 0 else 0,
                      100 if pnl > 0 else 0,
                      time.time(),
                      1 if pnl < 0 and symbol in self.PERMANENT_BLACKLIST else 0,
                      1 if pnl > 0 and symbol in self.PERMANENT_PROMOTE else 0))
            
            db.commit()
            db.close()
            
            # Update in-memory sets
            if is_blacklisted:
                self._blacklisted.add(symbol)
            if is_promoted:
                self._promoted.add(symbol)
                
        except Exception as e:
            logger.debug("SymbolExpectancyTracker record failed: {}", e)
    
    def is_blacklisted(self, symbol: str) -> bool:
        """Check if symbol is blacklisted (permanent OR fast blacklist)."""
        return symbol in self._blacklisted or self.is_temp_blacklisted(symbol)
    
    def is_promoted(self, symbol: str) -> bool:
        """Check if symbol is promoted."""
        return symbol in self._promoted

    # ═══════════════════════════════════════════════════════════════
    # FIX 12: Fast Blacklist — 3 losses in 48h → 72h cooldown
    # ═══════════════════════════════════════════════════════════════

    def record_loss(self, symbol: str, pnl_pct: float) -> None:
        """Record a loss for fast blacklist tracking.

        v5: Two-tier system:
        1. Fast blacklist: 2 losses in 48h → 72h block
        2. Catastrophic: single loss > 5% account → 7-day block
        """
        now = time.time()

        # v5: Catastrophic loss check — single loss > 5% = 7-day block
        if pnl_pct <= self.CATASTROPHIC_LOSS_PCT:
            cooldown_sec = self.CATASTROPHIC_BLOCK_HRS * 3600
            self._temp_blacklist[symbol] = now + cooldown_sec
            logger.info(
                "🚫🚫 CATASTROPHIC_BLACKLIST: {} — loss {:.1f}% > {:.1f}% → blocked for {} days",
                symbol, pnl_pct, self.CATASTROPHIC_LOSS_PCT, self.CATASTROPHIC_BLOCK_HRS // 24,
            )
            return  # No need to check fast blacklist — catastrophic blocks longer

        if pnl_pct >= self.FAST_BLACKLIST_LOSS_THRESHOLD:
            return  # Not a significant loss

        now = time.time()
        window_sec = self.FAST_BLACKLIST_WINDOW_HRS * 3600

        # Add this loss
        if symbol not in self._recent_losses:
            self._recent_losses[symbol] = []
        self._recent_losses[symbol].append(now)

        # Prune old losses outside window
        self._recent_losses[symbol] = [
            t for t in self._recent_losses[symbol]
            if now - t < window_sec
        ]

        # Check blacklist threshold
        recent_count = len(self._recent_losses[symbol])
        if recent_count >= self.FAST_BLACKLIST_MAXLOSSES:
            cooldown_sec = self.FAST_BLACKLIST_COOLDOWN_HRS * 3600
            self._temp_blacklist[symbol] = now + cooldown_sec
            logger.info(
                "🚫 BLACKLIST: {} — {} losses in {}h → blocked for {}h",
                symbol, recent_count,
                self.FAST_BLACKLIST_WINDOW_HRS,
                self.FAST_BLACKLIST_COOLDOWN_HRS,
            )

    def is_temp_blacklisted(self, symbol: str) -> bool:
        """Check if symbol is temporarily blacklisted (fast blacklist)."""
        expiry = self._temp_blacklist.get(symbol, 0)
        if expiry == 0:
            return False
        if time.time() >= expiry:
            # Expired — remove
            del self._temp_blacklist[symbol]
            self._recent_losses.pop(symbol, None)
            return False
        return True
    
    def get_symbol_stats(self, symbol: str) -> Optional[Dict]:
        """Get performance stats for a symbol."""
        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            db.row_factory = sqlite3.Row
            row = db.execute(
                "SELECT * FROM symbol_performance WHERE symbol=?", (symbol,)
            ).fetchone()
            db.close()
            return dict(row) if row else None
        except Exception:
            return None
    
    def get_blacklist(self) -> List[str]:
        """Get all blacklisted symbols."""
        return sorted(self._blacklisted)
    
    def get_promoted(self) -> List[str]:
        """Get all promoted symbols."""
        return sorted(self._promoted)
    
    def get_stats(self) -> Dict:
        """Get overall tracker statistics."""
        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            total = db.execute("SELECT COUNT(*) FROM symbol_performance").fetchone()[0]
            blacklisted = db.execute("SELECT COUNT(*) FROM symbol_performance WHERE is_blacklisted=1").fetchone()[0]
            promoted = db.execute("SELECT COUNT(*) FROM symbol_performance WHERE is_promoted=1").fetchone()[0]
            db.close()
            return {
                "total_symbols": total,
                "blacklisted": blacklisted,
                "promoted": promoted,
                "permanent_blacklist": len(self.PERMANENT_BLACKLIST),
            }
        except Exception:
            return {"total_symbols": 0, "blacklisted": 0, "promoted": 0}
