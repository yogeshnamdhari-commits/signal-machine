"""
EMA_V5 Order History — Persistent order and trade audit trail.
Reads/writes to isolated storage. Never touches existing history.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_AI_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DB_PATH = _AI_ROOT / "data" / "ema_v5_signals.db"
_TABLE = "ema_v5_order_history"


class EMAv5OrderHistory:
    """Persistent order and trade history for EMA_V5."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = str(db_path or _DB_PATH)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        # In-memory buffer for fast access
        self._buffer: List[Dict] = []

    def _init_db(self) -> None:
        """Create order history table if not exists."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {_TABLE} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT,
                    client_order_id TEXT,
                    symbol TEXT,
                    side TEXT,
                    order_type TEXT,
                    quantity REAL,
                    price REAL,
                    avg_price REAL,
                    status TEXT,
                    reason TEXT,
                    signal_uuid TEXT,
                    pnl REAL DEFAULT 0,
                    fees REAL DEFAULT 0,
                    hold_minutes REAL DEFAULT 0,
                    regime TEXT,
                    confidence REAL,
                    created_at REAL,
                    updated_at REAL,
                    stored_at REAL
                )
            """)
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_oh_sym ON {_TABLE}(symbol)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_oh_ts ON {_TABLE}(created_at)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_oh_status ON {_TABLE}(status)")
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error("EMAv5 order history init failed: {}", e)

    def record_order(self, order_data: Dict[str, Any]) -> bool:
        """Record an order event."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            now = time.time()
            conn.execute(f"""
                INSERT INTO {_TABLE}
                (order_id, client_order_id, symbol, side, order_type, quantity,
                 price, avg_price, status, reason, signal_uuid, pnl, fees,
                 hold_minutes, regime, confidence, created_at, updated_at, stored_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order_data.get("order_id", ""),
                order_data.get("client_order_id", ""),
                order_data.get("symbol", ""),
                order_data.get("side", ""),
                order_data.get("order_type", ""),
                order_data.get("quantity", 0),
                order_data.get("price", 0),
                order_data.get("avg_price", 0),
                order_data.get("status", ""),
                order_data.get("reason", ""),
                order_data.get("signal_uuid", ""),
                order_data.get("pnl", 0),
                order_data.get("fees", 0),
                order_data.get("hold_minutes", 0),
                order_data.get("regime", ""),
                order_data.get("confidence", 0),
                order_data.get("created_at", now),
                order_data.get("updated_at", now),
                now,
            ))
            conn.commit()
            conn.close()
            self._buffer.append(order_data)
            return True
        except Exception as e:
            logger.error("EMAv5 record_order failed: {}", e)
            return False

    def record_trade_close(self, trade_data: Dict[str, Any]) -> bool:
        """Record a trade close event."""
        return self.record_order({
            **trade_data,
            "status": "CLOSED",
            "order_type": "TRADE_CLOSE",
        })

    def get_orders(self, symbol: Optional[str] = None,
                   status: Optional[str] = None,
                   limit: int = 100) -> List[Dict]:
        """Get orders with optional filters."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.row_factory = sqlite3.Row

            query = f"SELECT * FROM {_TABLE} WHERE 1=1"
            params: list = []
            if symbol:
                query += " AND symbol=?"
                params.append(symbol)
            if status:
                query += " AND status=?"
                params.append(status)
            query += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cur = conn.execute(query, params)
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            logger.error("EMAv5 get_orders failed: {}", e)
            return []

    def get_trades(self, symbol: Optional[str] = None,
                   limit: int = 500) -> List[Dict]:
        """Get closed trades."""
        return self.get_orders(symbol=symbol, status="CLOSED", limit=limit)

    def get_stats(self) -> Dict[str, Any]:
        """Get order/trade statistics."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=10)
            conn.row_factory = sqlite3.Row

            cur = conn.execute(f"SELECT COUNT(*) as total FROM {_TABLE}")
            total = cur.fetchone()["total"]

            cur = conn.execute(f"SELECT COUNT(*) as closed FROM {_TABLE} WHERE status='CLOSED'")
            closed = cur.fetchone()["closed"]

            cur = conn.execute(f"SELECT COALESCE(SUM(pnl), 0) as total_pnl FROM {_TABLE} WHERE status='CLOSED'")
            total_pnl = cur.fetchone()["total_pnl"]

            cur = conn.execute(f"SELECT COALESCE(SUM(fees), 0) as total_fees FROM {_TABLE}")
            total_fees = cur.fetchone()["total_fees"]

            conn.close()

            return {
                "total_orders": total,
                "closed_trades": closed,
                "total_pnl": round(total_pnl, 4),
                "total_fees": round(total_fees, 4),
            }
        except Exception as e:
            logger.error("EMAv5 get_stats failed: {}", e)
            return {}

    def count_orders(self) -> int:
        """Count total orders."""
        try:
            conn = sqlite3.connect(self._db_path, timeout=5)
            cur = conn.execute(f"SELECT COUNT(*) FROM {_TABLE}")
            count = cur.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return 0
