"""
Trade Journal — Permanent Trade Record System
===============================================
Phase 11: Every completed trade becomes a permanent journal entry.

READ-ONLY with respect to trading logic. Only records and analyzes.

Stores:
- Full trade context (entry, exit, SL, TP, confidence, regime, etc.)
- Performance metrics (MFE, MAE, R-multiple, PnL)
- Outcome classification (win/loss/breakeven)
- Tags and notes for manual annotation
- Lessons learned and root cause
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field, asdict

from loguru import logger


@dataclass
class TradeJournalEntry:
    """Complete journal entry for a single trade."""
    # Identity
    trade_id: str
    signal_id: str
    timestamp: float
    
    # Trade Details
    symbol: str
    exchange: str = "binance"
    side: str = "LONG"
    entry_price: float = 0.0
    stop_loss: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    tp3: float = 0.0
    exit_price: float = 0.0
    exit_reason: str = ""
    
    # Signal Context
    confidence: float = 0.0
    market_regime: str = ""
    pattern: str = ""
    session: str = ""
    scanner_version: str = ""
    signal_version: str = ""
    
    # Risk Metrics
    atr: float = 0.0
    volume: float = 0.0
    ema20: float = 0.0
    ema50: float = 0.0
    ema144: float = 0.0
    ema200: float = 0.0
    risk: float = 0.0
    reward: float = 0.0
    rr_ratio: float = 0.0
    position_size: float = 0.0
    
    # Outcome Metrics
    holding_duration_minutes: float = 0.0
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    highest_profit: float = 0.0
    worst_drawdown: float = 0.0
    final_r_multiple: float = 0.0
    net_profit: float = 0.0
    roi_pct: float = 0.0
    outcome: str = ""  # WIN/LOSS/BREAKEVEN
    
    # State Transitions
    state_transitions: str = "[]"  # JSON array of state changes
    
    # Journal Fields
    notes: str = ""
    lessons: str = ""
    root_cause: str = ""
    tags: str = ""
    screenshot_path: str = ""
    
    # Metadata
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class TradeJournal:
    """
    Permanent trade journal system.
    
    Creates a complete record for every published signal,
    tracking it from entry through exit with full context.
    
    READ-ONLY: Never modifies trading logic.
    """
    
    DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "institutional_v1.db"
    
    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or self.DB_PATH
        self._initialized = False
    
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    
    def initialize(self) -> None:
        """Create journal table if it doesn't exist."""
        if self._initialized:
            return
            
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trade_journal (
                    trade_id TEXT PRIMARY KEY,
                    signal_id TEXT,
                    timestamp REAL,
                    symbol TEXT NOT NULL,
                    exchange TEXT DEFAULT 'binance',
                    side TEXT DEFAULT 'LONG',
                    entry_price REAL DEFAULT 0,
                    stop_loss REAL DEFAULT 0,
                    tp1 REAL DEFAULT 0,
                    tp2 REAL DEFAULT 0,
                    tp3 REAL DEFAULT 0,
                    exit_price REAL DEFAULT 0,
                    exit_reason TEXT DEFAULT '',
                    confidence REAL DEFAULT 0,
                    market_regime TEXT DEFAULT '',
                    pattern TEXT DEFAULT '',
                    session TEXT DEFAULT '',
                    scanner_version TEXT DEFAULT '',
                    signal_version TEXT DEFAULT '',
                    atr REAL DEFAULT 0,
                    volume REAL DEFAULT 0,
                    ema20 REAL DEFAULT 0,
                    ema50 REAL DEFAULT 0,
                    ema144 REAL DEFAULT 0,
                    ema200 REAL DEFAULT 0,
                    risk REAL DEFAULT 0,
                    reward REAL DEFAULT 0,
                    rr_ratio REAL DEFAULT 0,
                    position_size REAL DEFAULT 0,
                    holding_duration_minutes REAL DEFAULT 0,
                    mfe_pct REAL DEFAULT 0,
                    mae_pct REAL DEFAULT 0,
                    highest_profit REAL DEFAULT 0,
                    worst_drawdown REAL DEFAULT 0,
                    final_r_multiple REAL DEFAULT 0,
                    net_profit REAL DEFAULT 0,
                    roi_pct REAL DEFAULT 0,
                    outcome TEXT DEFAULT '',
                    state_transitions TEXT DEFAULT '[]',
                    notes TEXT DEFAULT '',
                    lessons TEXT DEFAULT '',
                    root_cause TEXT DEFAULT '',
                    tags TEXT DEFAULT '',
                    screenshot_path TEXT DEFAULT '',
                    created_at REAL,
                    updated_at REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_journal_symbol ON trade_journal(symbol)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_journal_outcome ON trade_journal(outcome)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_journal_timestamp ON trade_journal(timestamp)")
            conn.commit()
            self._initialized = True
            logger.info("📋 TradeJournal initialized at {}", self._db_path)
        finally:
            conn.close()
    
    def record_signal(
        self,
        signal_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        tp1: float,
        tp2: float = 0,
        tp3: float = 0,
        confidence: float = 0,
        market_regime: str = "",
        pattern: str = "",
        session: str = "",
        atr: float = 0,
        volume: float = 0,
        ema20: float = 0,
        ema50: float = 0,
        ema144: float = 0,
        ema200: float = 0,
        position_size: float = 0,
        scanner_version: str = "ema_v5",
        signal_version: str = "production_v2",
        **kwargs,
    ) -> str:
        """Record a new signal as a journal entry."""
        self.initialize()
        
        trade_id = f"JNL_{signal_id}_{int(time.time())}"
        
        # Calculate risk/reward
        risk = abs(entry_price - stop_loss) if entry_price and stop_loss else 0
        reward = abs(tp1 - entry_price) if entry_price and tp1 else 0
        rr_ratio = reward / risk if risk > 0 else 0
        
        entry = TradeJournalEntry(
            trade_id=trade_id,
            signal_id=signal_id,
            timestamp=time.time(),
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            confidence=confidence,
            market_regime=market_regime,
            pattern=pattern,
            session=session,
            scanner_version=scanner_version,
            signal_version=signal_version,
            atr=atr,
            volume=volume,
            ema20=ema20,
            ema50=ema50,
            ema144=ema144,
            ema200=ema200,
            risk=risk,
            reward=reward,
            rr_ratio=rr_ratio,
            position_size=position_size,
            **kwargs,
        )
        
        self._insert_entry(entry)
        logger.info("📝 JOURNAL: Recorded signal {} for {} {}", trade_id, side, symbol)
        return trade_id
    
    def record_exit(
        self,
        trade_id: str,
        exit_price: float,
        exit_reason: str = "",
        holding_duration_minutes: float = 0,
        mfe_pct: float = 0,
        mae_pct: float = 0,
        highest_profit: float = 0,
        worst_drawdown: float = 0,
        net_profit: float = 0,
        roi_pct: float = 0,
        notes: str = "",
        lessons: str = "",
        root_cause: str = "",
        tags: str = "",
    ) -> None:
        """Record trade exit and calculate outcome."""
        self.initialize()
        
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM trade_journal WHERE trade_id = ?", (trade_id,)
            ).fetchone()
            
            if not row:
                logger.warning("JOURNAL: Trade {} not found", trade_id)
                return
            
            entry_price = row["entry_price"]
            stop_loss = row["stop_loss"]
            risk = row["risk"]
            
            # Calculate R-multiple
            if risk > 0 and entry_price > 0:
                if row["side"] == "LONG":
                    r_multiple = (exit_price - entry_price) / risk
                else:
                    r_multiple = (entry_price - exit_price) / risk
            else:
                r_multiple = 0
            
            # Determine outcome
            if net_profit > 0:
                outcome = "WIN"
            elif net_profit < 0:
                outcome = "LOSS"
            else:
                outcome = "BREAKEVEN"
            
            conn.execute("""
                UPDATE trade_journal SET
                    exit_price = ?,
                    exit_reason = ?,
                    holding_duration_minutes = ?,
                    mfe_pct = ?,
                    mae_pct = ?,
                    highest_profit = ?,
                    worst_drawdown = ?,
                    final_r_multiple = ?,
                    net_profit = ?,
                    roi_pct = ?,
                    outcome = ?,
                    notes = ?,
                    lessons = ?,
                    root_cause = ?,
                    tags = ?,
                    updated_at = ?
                WHERE trade_id = ?
            """, (
                exit_price, exit_reason, holding_duration_minutes,
                mfe_pct, mae_pct, highest_profit, worst_drawdown,
                round(r_multiple, 4), net_profit, roi_pct, outcome,
                notes, lessons, root_cause, tags,
                time.time(), trade_id,
            ))
            conn.commit()
            
            logger.info(
                "📝 JOURNAL: Recorded exit for {} | {} {} | R={:.2f} | PnL={:.4f} | {}",
                trade_id, row["side"], row["symbol"], r_multiple, net_profit, outcome,
            )
        finally:
            conn.close()
    
    def _insert_entry(self, entry: TradeJournalEntry) -> None:
        """Insert a journal entry into the database."""
        conn = self._connect()
        try:
            data = asdict(entry)
            columns = ", ".join(data.keys())
            placeholders = ", ".join(["?"] * len(data))
            
            conn.execute(
                f"INSERT OR REPLACE INTO trade_journal ({columns}) VALUES ({placeholders})",
                list(data.values()),
            )
            conn.commit()
        finally:
            conn.close()
    
    def get_entry(self, trade_id: str) -> Optional[Dict]:
        """Get a single journal entry."""
        self.initialize()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM trade_journal WHERE trade_id = ?", (trade_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    
    def get_recent_entries(self, count: int = 50) -> List[Dict]:
        """Get recent journal entries."""
        self.initialize()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM trade_journal ORDER BY timestamp DESC LIMIT ?", (count,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    
    def get_entries_by_symbol(self, symbol: str) -> List[Dict]:
        """Get all journal entries for a symbol."""
        self.initialize()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM trade_journal WHERE symbol = ? ORDER BY timestamp DESC",
                (symbol,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    
    def get_entries_by_outcome(self, outcome: str) -> List[Dict]:
        """Get all journal entries with specific outcome."""
        self.initialize()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM trade_journal WHERE outcome = ? ORDER BY timestamp DESC",
                (outcome,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    
    def get_statistics(self) -> Dict:
        """Get journal statistics."""
        self.initialize()
        conn = self._connect()
        try:
            total = conn.execute("SELECT COUNT(*) FROM trade_journal").fetchone()[0]
            wins = conn.execute("SELECT COUNT(*) FROM trade_journal WHERE outcome = 'WIN'").fetchone()[0]
            losses = conn.execute("SELECT COUNT(*) FROM trade_journal WHERE outcome = 'LOSS'").fetchone()[0]
            open_trades = conn.execute("SELECT COUNT(*) FROM trade_journal WHERE outcome = ''").fetchone()[0]
            
            return {
                "total_entries": total,
                "wins": wins,
                "losses": losses,
                "open_trades": open_trades,
                "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            }
        finally:
            conn.close()
    
    def add_notes(self, trade_id: str, notes: str) -> None:
        """Add notes to an existing journal entry."""
        self.initialize()
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE trade_journal SET notes = ?, updated_at = ? WHERE trade_id = ?",
                (notes, time.time(), trade_id),
            )
            conn.commit()
        finally:
            conn.close()
    
    def add_tags(self, trade_id: str, tags: str) -> None:
        """Add tags to an existing journal entry."""
        self.initialize()
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE trade_journal SET tags = ?, updated_at = ? WHERE trade_id = ?",
                (tags, time.time(), trade_id),
            )
            conn.commit()
        finally:
            conn.close()


# Global singleton
_journal: Optional[TradeJournal] = None

def get_trade_journal() -> TradeJournal:
    """Get or create the global trade journal."""
    global _journal
    if _journal is None:
        _journal = TradeJournal()
    return _journal
