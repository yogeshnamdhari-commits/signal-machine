"""
Forward Test Database — SQLite persistence for forward-test signal & trade collection.

Prevents:
  - Overfitting (only live data used)
  - Survivorship bias (all signals stored, not just winners)
  - Retrospective curve fitting (no backfill allowed)

Schema designed for 18-phase validation framework.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "forward_test.db"


class ForwardTestDB:
    """
    Forward-test database for institutional production validation.
    All data is live-only — no backfill, no synthetic data.
    """
    
    MIN_SIGNALS_FOR_PHASE4 = 500
    MIN_TRADES_FOR_PHASE5 = 100
    
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else _DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _init_db(self) -> None:
        """Create forward-test tables."""
        db = sqlite3.connect(str(self.db_path), timeout=10)
        
        # Signals table — every signal scanned
        db.execute("""
            CREATE TABLE IF NOT EXISTS forward_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                -- Raw scores
                confidence_100 REAL DEFAULT 0,
                institutional_score REAL DEFAULT 0,
                regime TEXT DEFAULT '',
                session TEXT DEFAULT '',
                -- Entry data
                entry_price REAL DEFAULT 0,
                stop_loss REAL DEFAULT 0,
                take_profit REAL DEFAULT 0,
                risk_reward REAL DEFAULT 0,
                -- Order flow
                delta REAL DEFAULT 0,
                cvd REAL DEFAULT 0,
                oi_delta REAL DEFAULT 0,
                funding_rate REAL DEFAULT 0,
                -- Smart money
                sweep_score REAL DEFAULT 0,
                mss_score REAL DEFAULT 0,
                fvg_score REAL DEFAULT 0,
                -- Outcome tracking
                entry_reason TEXT DEFAULT '',
                signal_status TEXT DEFAULT 'pending',
                -- Metadata
                mtf_alignment REAL DEFAULT 0,
                checklist_score INTEGER DEFAULT 0,
                regime_confidence REAL DEFAULT 0,
                volatility_score REAL DEFAULT 0,
                quiet_market_blocked INTEGER DEFAULT 0,
                metadata TEXT DEFAULT '{}'
            )
        """)
        
        # Trades table — every closed trade
        db.execute("""
            CREATE TABLE IF NOT EXISTS forward_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER,
                timestamp REAL NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                -- Entry
                entry_price REAL NOT NULL,
                entry_time REAL NOT NULL,
                -- Exit
                exit_price REAL DEFAULT 0,
                exit_time REAL DEFAULT 0,
                exit_reason TEXT DEFAULT '',
                -- P&L
                pnl REAL DEFAULT 0,
                fees REAL DEFAULT 0,
                net_pnl REAL DEFAULT 0,
                -- Risk
                stop_loss REAL DEFAULT 0,
                take_profit REAL DEFAULT 0,
                planned_rr REAL DEFAULT 0,
                realized_r REAL DEFAULT 0,
                -- Lifecycle
                hold_minutes REAL DEFAULT 0,
                mae_pct REAL DEFAULT 0,
                mfe_pct REAL DEFAULT 0,
                -- Quality markers
                regime TEXT DEFAULT '',
                session TEXT DEFAULT '',
                confidence_100 REAL DEFAULT 0,
                institutional_score REAL DEFAULT 0,
                sweep_score REAL DEFAULT 0,
                mss_score REAL DEFAULT 0,
                fvg_score REAL DEFAULT 0,
                delta REAL DEFAULT 0,
                cvd REAL DEFAULT 0,
                oi_delta REAL DEFAULT 0,
                funding_rate REAL DEFAULT 0,
                -- Outcome
                outcome TEXT DEFAULT '',
                strategy_version TEXT DEFAULT 'forward_test_v1',
                FOREIGN KEY (signal_id) REFERENCES forward_signals(id)
            )
        """)
        
        # Indexes for fast validation queries
        db.execute("CREATE INDEX IF NOT EXISTS idx_fs_ts ON forward_signals(timestamp)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_fs_regime ON forward_signals(regime)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_fs_session ON forward_signals(session)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_fs_conf ON forward_signals(confidence_100)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ft_ts ON forward_trades(timestamp)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ft_regime ON forward_trades(regime)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ft_session ON forward_trades(session)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ft_outcome ON forward_trades(outcome)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ft_symbol ON forward_trades(symbol)")
        
        db.commit()
        db.close()
        logger.info("ForwardTestDB initialized at {}", self.db_path)
    
    def record_signal(self, sig: Dict[str, Any]) -> int:
        """Record a scanned signal (ALL signals, not just winners)."""
        db = sqlite3.connect(str(self.db_path), timeout=10)
        cursor = db.execute("""
            INSERT INTO forward_signals (
                timestamp, symbol, side, confidence_100, institutional_score,
                regime, session, entry_price, stop_loss, take_profit, risk_reward,
                delta, cvd, oi_delta, funding_rate, sweep_score, mss_score, fvg_score,
                entry_reason, signal_status, mtf_alignment, checklist_score,
                regime_confidence, volatility_score, quiet_market_blocked, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            sig.get("timestamp", time.time()),
            sig.get("symbol", ""),
            sig.get("side", ""),
            sig.get("confidence_100", 0),
            sig.get("institutional_score", 0),
            sig.get("regime", ""),
            sig.get("session", ""),
            sig.get("entry_price", 0),
            sig.get("stop_loss", 0),
            sig.get("take_profit", 0),
            sig.get("risk_reward", 0),
            sig.get("delta", 0),
            sig.get("cvd", 0),
            sig.get("oi_delta", 0),
            sig.get("funding_rate", 0),
            sig.get("sweep_score", 0),
            sig.get("mss_score", 0),
            sig.get("fvg_score", 0),
            sig.get("entry_reason", ""),
            sig.get("signal_status", "pending"),
            sig.get("mtf_alignment", 0),
            sig.get("checklist_score", 0),
            sig.get("regime_confidence", 0),
            sig.get("volatility_score", 0),
            1 if sig.get("quiet_market_blocked") else 0,
            json.dumps(sig.get("metadata", {})),
        ))
        db.commit()
        row_id = cursor.lastrowid
        db.close()
        return row_id
    
    def record_trade(self, trade: Dict[str, Any]) -> int:
        """Record a closed trade."""
        db = sqlite3.connect(str(self.db_path), timeout=10)
        net_pnl = trade.get("pnl", 0) - trade.get("fees", 0)
        outcome = "win" if net_pnl > 0 else "loss"
        cursor = db.execute("""
            INSERT INTO forward_trades (
                signal_id, timestamp, symbol, side, entry_price, entry_time,
                exit_price, exit_time, exit_reason, pnl, fees, net_pnl,
                stop_loss, take_profit, planned_rr, realized_r,
                hold_minutes, mae_pct, mfe_pct, regime, session,
                confidence_100, institutional_score, sweep_score, mss_score, fvg_score,
                delta, cvd, oi_delta, funding_rate, outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.get("signal_id"),
            trade.get("timestamp", time.time()),
            trade.get("symbol", ""),
            trade.get("side", ""),
            trade.get("entry_price", 0),
            trade.get("entry_time", 0),
            trade.get("exit_price", 0),
            trade.get("exit_time", 0),
            trade.get("exit_reason", ""),
            trade.get("pnl", 0),
            trade.get("fees", 0),
            net_pnl,
            trade.get("stop_loss", 0),
            trade.get("take_profit", 0),
            trade.get("planned_rr", 0),
            trade.get("realized_r", 0),
            trade.get("hold_minutes", 0),
            trade.get("mae_pct", 0),
            trade.get("mfe_pct", 0),
            trade.get("regime", ""),
            trade.get("session", ""),
            trade.get("confidence_100", 0),
            trade.get("institutional_score", 0),
            trade.get("sweep_score", 0),
            trade.get("mss_score", 0),
            trade.get("fvg_score", 0),
            trade.get("delta", 0),
            trade.get("cvd", 0),
            trade.get("oi_delta", 0),
            trade.get("funding_rate", 0),
            outcome,
        ))
        db.commit()
        row_id = cursor.lastrowid
        db.close()
        return row_id
    
    def update_signal_status(self, signal_id: int, status: str) -> None:
        """Update signal status (pending → entered / expired)."""
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.execute("UPDATE forward_signals SET signal_status=? WHERE id=?", (status, signal_id))
        db.commit()
        db.close()
    
    def get_signal_count(self) -> int:
        """Total signals collected."""
        db = sqlite3.connect(str(self.db_path), timeout=10)
        count = db.execute("SELECT COUNT(*) FROM forward_signals").fetchone()[0]
        db.close()
        return count
    
    def get_trade_count(self) -> int:
        """Total closed trades."""
        db = sqlite3.connect(str(self.db_path), timeout=10)
        count = db.execute("SELECT COUNT(*) FROM forward_trades").fetchone()[0]
        db.close()
        return count
    
    def get_collection_status(self) -> Dict:
        """Check collection progress."""
        db = sqlite3.connect(str(self.db_path), timeout=10)
        sig_count = db.execute("SELECT COUNT(*) FROM forward_signals").fetchone()[0]
        trade_count = db.execute("SELECT COUNT(*) FROM forward_trades WHERE outcome != ''").fetchone()[0]
        
        # By regime
        regimes = dict(db.execute(
            "SELECT regime, COUNT(*) FROM forward_signals GROUP BY regime"
        ).fetchall())
        
        # By session
        sessions = dict(db.execute(
            "SELECT session, COUNT(*) FROM forward_signals GROUP BY session"
        ).fetchall())
        
        # Completeness
        total_cols = 26  # Number of tracked columns in forward_signals
        non_null_cols = db.execute("""
            SELECT COUNT(*) FROM forward_signals 
            WHERE confidence_100 > 0 AND regime != '' AND session != '' AND entry_price > 0
        """).fetchone()[0]
        
        db.close()
        
        return {
            "signal_count": sig_count,
            "trade_count": trade_count,
            "signals_needed": max(0, self.MIN_SIGNALS_FOR_PHASE4 - sig_count),
            "trades_needed": max(0, self.MIN_TRADES_FOR_PHASE5 - trade_count),
            "regimes": regimes,
            "sessions": sessions,
            "data_completeness": round(non_null_cols / max(sig_count, 1) * 100, 1),
        }
    
    def query(self, sql: str, params: tuple = ()) -> List[Dict]:
        """Execute a read query and return list of dicts."""
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        rows = db.execute(sql, params).fetchall()
        db.close()
        return [dict(r) for r in rows]
    
    def query_scalar(self, sql: str, params: tuple = ()) -> Any:
        """Execute a query returning a single value."""
        db = sqlite3.connect(str(self.db_path), timeout=10)
        result = db.execute(sql, params).fetchone()
        db.close()
        return result[0] if result else None


# Global singleton
forward_test_db = ForwardTestDB()
