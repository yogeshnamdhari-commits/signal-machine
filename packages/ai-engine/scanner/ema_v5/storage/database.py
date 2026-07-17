"""
EMA_V5 Database — Isolated SQLite storage for EMA_V5 signals.
Creates its own database file. Never touches existing tables.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

# Own database file — completely separate from institutional_v1.db
_AI_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DB_DIR = _AI_ROOT / "data"
_DB_PATH = _DB_DIR / "ema_v5_signals.db"

# Table name — isolated, no collision with existing tables
_TABLE = "ema_v5_signals"
_HISTORY_TABLE = "ema_v5_trade_history"


class EMAv5Database:
    """SQLite storage for EMA_V5 signals. Isolated from all existing tables."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = str(db_path or _DB_PATH)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if they don't exist. Idempotent."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")

            # Main signals table — complete audit trail
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    uuid TEXT PRIMARY KEY,
                    timestamp REAL NOT NULL,
                    date TEXT,
                    time TEXT,
                    exchange TEXT DEFAULT 'Binance',
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    trend TEXT,
                    current_state TEXT,
                    ema20 REAL,
                    ema50 REAL,
                    ema144 REAL,
                    ema200 REAL,
                    entry REAL,
                    stop_loss REAL,
                    tp1 REAL,
                    tp2 REAL,
                    tp3 REAL,
                    volume INTEGER DEFAULT 0,
                    confidence REAL,
                    reason TEXT,
                    pattern TEXT,
                    result TEXT DEFAULT '',
                    pnl REAL DEFAULT 0,
                    hold_time REAL DEFAULT 0,
                    strategy_version TEXT DEFAULT 'ema_v5',
                    rr_1 REAL,
                    rr_2 REAL,
                    rr_3 REAL,
                    regime TEXT,
                    session TEXT,
                    sl_dist_pct REAL,
                    state TEXT,
                    ema_chain_aligned INTEGER DEFAULT 0,
                    slope_ema20 REAL,
                    slope_ema50 REAL,
                    pullback_detected INTEGER DEFAULT 0,
                    pullback_level TEXT,
                    candle_score REAL,
                    volume_score REAL,
                    trend_score REAL,
                    regime_score REAL,
                    schema_version TEXT,
                    stored_at REAL
                )
            """)

            # Trade history table — append-only, never overwrite
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {_HISTORY_TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    uuid TEXT NOT NULL,
                    closed_at REAL,
                    exit_reason TEXT,
                    pnl REAL DEFAULT 0,
                    hold_minutes REAL DEFAULT 0,
                    realized_r REAL DEFAULT 0,
                    mfe_pct REAL DEFAULT 0,
                    mae_pct REAL DEFAULT 0,
                    outcome TEXT,
                    stored_at REAL,
                    FOREIGN KEY (uuid) REFERENCES {_TABLE}(uuid)
                )
            """)

            # Performance indexes
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_ev5_sym ON {_TABLE}(symbol)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_ev5_ts ON {_TABLE}(timestamp)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_ev5_side ON {_TABLE}(side)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_ev5_date ON {_TABLE}(date)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_ev5_result ON {_TABLE}(result)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_ev5h_uuid ON {_HISTORY_TABLE}(uuid)")

            conn.commit()
            conn.close()
            logger.debug("EMAv5Database initialized: {}", self._db_path)
        except Exception as e:
            logger.error("EMAv5Database init failed: {}", e)

    def store_signal(self, signal: Dict[str, Any]) -> bool:
        """Store a serialized signal. Idempotent (INSERT OR REPLACE)."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")

            # Map signal keys to database column names
            _KEY_MAP = {
                "sl": "stop_loss",
                "take_profit_1": "tp1",
                "take_profit_2": "tp2",
                "take_profit_3": "tp3",
            }
            mapped = {}
            for k, v in signal.items():
                mapped[_KEY_MAP.get(k, k)] = v

            cols = list(mapped.keys())
            placeholders = ", ".join(["?"] * len(cols))
            col_names = ", ".join(cols)
            values = [mapped.get(c, 0 if isinstance(c, (int, float)) else "") for c in cols]

            conn.execute(
                f"INSERT OR REPLACE INTO {_TABLE} ({col_names}) VALUES ({placeholders})",
                values,
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error("EMAv5 store_signal failed: {}", e)
            return False

    def store_trade_close(self, close_data: Dict[str, Any]) -> bool:
        """Store a trade close record in history. Append-only."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")

            conn.execute(
                f"""INSERT INTO {_HISTORY_TABLE}
                    (uuid, closed_at, exit_reason, pnl, hold_minutes,
                     realized_r, mfe_pct, mae_pct, outcome, stored_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    close_data.get("uuid", ""),
                    close_data.get("closed_at", time.time()),
                    close_data.get("exit_reason", ""),
                    close_data.get("pnl", 0),
                    close_data.get("hold_minutes", 0),
                    close_data.get("realized_r", 0),
                    close_data.get("mfe_pct", 0),
                    close_data.get("mae_pct", 0),
                    close_data.get("outcome", ""),
                    time.time(),
                ),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error("EMAv5 store_trade_close failed: {}", e)
            return False

    def update_signal_result(self, uuid: str, result: str, pnl: float,
                              hold_time: float = 0, state: str = "") -> bool:
        """Update a signal's result after trade closes. Non-destructive."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")

            conn.execute(
                f"""UPDATE {_TABLE}
                    SET result=?, pnl=?, hold_time=?, current_state=?
                    WHERE uuid=?""",
                (result, pnl, hold_time, state, uuid),
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error("EMAv5 update_signal_result failed: {}", e)
            return False

    def get_signal(self, uuid: str) -> Optional[Dict[str, Any]]:
        """Retrieve a signal by UUID."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(f"SELECT * FROM {_TABLE} WHERE uuid=?", (uuid,))
            row = cur.fetchone()
            conn.close()
            return dict(row) if row else None
        except Exception as e:
            logger.error("EMAv5 get_signal failed: {}", e)
            return None

    def get_signals(self, symbol: Optional[str] = None, side: Optional[str] = None,
                    date: Optional[str] = None, limit: int = 1000) -> List[Dict[str, Any]]:
        """Retrieve signals with optional filters."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.row_factory = sqlite3.Row

            query = f"SELECT * FROM {_TABLE} WHERE 1=1"
            params: list = []

            if symbol:
                query += " AND symbol=?"
                params.append(symbol)
            if side:
                query += " AND side=?"
                params.append(side)
            if date:
                query += " AND date=?"
                params.append(date)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cur = conn.execute(query, params)
            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("EMAv5 get_signals failed: {}", e)
            return []

    def get_all_signals(self) -> List[Dict[str, Any]]:
        """Retrieve all signals — for recovery and export."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            cur = conn.execute(f"SELECT * FROM {_TABLE} ORDER BY timestamp ASC")
            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("EMAv5 get_all_signals failed: {}", e)
            return []

    def get_trade_history(self, uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve trade close history."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.row_factory = sqlite3.Row

            if uuid:
                cur = conn.execute(
                    f"SELECT * FROM {_HISTORY_TABLE} WHERE uuid=? ORDER BY closed_at",
                    (uuid,),
                )
            else:
                cur = conn.execute(
                    f"SELECT * FROM {_HISTORY_TABLE} ORDER BY closed_at DESC LIMIT 500"
                )

            rows = cur.fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.error("EMAv5 get_trade_history failed: {}", e)
            return []

    def get_stats(self) -> Dict[str, Any]:
        """Aggregate statistics for dashboard display."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.row_factory = sqlite3.Row

            cur = conn.execute(f"SELECT COUNT(*) as total FROM {_TABLE}")
            total = cur.fetchone()["total"]

            cur = conn.execute(f"SELECT COUNT(*) as wins FROM {_TABLE} WHERE result='win'")
            wins = cur.fetchone()["wins"]

            cur = conn.execute(f"SELECT COUNT(*) as losses FROM {_TABLE} WHERE result='loss'")
            losses = cur.fetchone()["losses"]

            cur = conn.execute(f"SELECT COALESCE(SUM(pnl), 0) as total_pnl FROM {_TABLE}")
            total_pnl = cur.fetchone()["total_pnl"]

            cur = conn.execute(f"SELECT COALESCE(AVG(pnl), 0) as avg_pnl FROM {_TABLE} WHERE pnl != 0")
            avg_pnl = cur.fetchone()["avg_pnl"]

            cur = conn.execute(f"SELECT COALESCE(AVG(confidence), 0) as avg_conf FROM {_TABLE}")
            avg_conf = cur.fetchone()["avg_conf"]

            cur = conn.execute(f"SELECT COUNT(*) as buy FROM {_TABLE} WHERE side='LONG'")
            buy_count = cur.fetchone()["buy"]

            cur = conn.execute(f"SELECT COUNT(*) as sell FROM {_TABLE} WHERE side='SHORT'")
            sell_count = cur.fetchone()["sell"]

            conn.close()

            win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
            gross_wins = total_pnl if total_pnl > 0 else 0
            gross_losses = abs(total_pnl) if total_pnl < 0 else 0
            pf = (gross_wins / gross_losses) if gross_losses > 0 else 0

            return {
                "total_signals": total,
                "wins": wins,
                "losses": losses,
                "win_rate": round(win_rate, 1),
                "total_pnl": round(total_pnl, 4),
                "avg_pnl": round(avg_pnl, 4),
                "avg_confidence": round(avg_conf, 1),
                "profit_factor": round(pf, 2),
                "buy_signals": buy_count,
                "sell_signals": sell_count,
            }
        except Exception as e:
            logger.error("EMAv5 get_stats failed: {}", e)
            return {}

    def count_signals(self) -> int:
        """Count total signals stored."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            cur = conn.execute(f"SELECT COUNT(*) FROM {_TABLE}")
            count = cur.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0

    def uuid_exists(self, uuid: str) -> bool:
        """Check if a UUID already exists (duplicate prevention)."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            cur = conn.execute(f"SELECT 1 FROM {_TABLE} WHERE uuid=? LIMIT 1", (uuid,))
            exists = cur.fetchone() is not None
            conn.close()
            return exists
        except Exception:
            return False
