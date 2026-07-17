"""
DeltaTerminal — Async SQLite Database
Connection-managed, fault-tolerant, production-grade.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiosqlite
from loguru import logger

DB_DIR = Path("data/database")
DB_DIR.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol     TEXT UNIQUE NOT NULL,
    base_asset TEXT NOT NULL,
    quote_asset TEXT DEFAULT 'USDT',
    is_active  BOOLEAN DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS klines (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol    TEXT NOT NULL,
    interval  TEXT NOT NULL,
    open_time INTEGER NOT NULL,
    open      REAL NOT NULL,
    high      REAL NOT NULL,
    low       REAL NOT NULL,
    close     REAL NOT NULL,
    volume    REAL NOT NULL,
    trades    INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, interval, open_time)
);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol     TEXT NOT NULL,
    bids       TEXT NOT NULL,
    asks       TEXT NOT NULL,
    timestamp  INTEGER NOT NULL,
    spread     REAL,
    mid_price  REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    price           REAL NOT NULL,
    quantity        REAL NOT NULL,
    is_buyer_maker  BOOLEAN,
    trade_time      INTEGER NOT NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS signals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol        TEXT NOT NULL,
    signal_type   TEXT NOT NULL,
    confidence    REAL NOT NULL,
    entry_price   REAL,
    stop_loss     REAL,
    take_profit   REAL,
    timeframes    TEXT,
    indicators    TEXT,
    regime        TEXT,
    factors       TEXT,
    status        TEXT DEFAULT 'active',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    triggered_at  TIMESTAMP,
    closed_at     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id   INTEGER,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,
    entry_price REAL NOT NULL,
    quantity    REAL NOT NULL,
    leverage    INTEGER DEFAULT 1,
    stop_loss   REAL,
    take_profit REAL,
    pnl         REAL DEFAULT 0,
    fees        REAL DEFAULT 0,
    status      TEXT DEFAULT 'open',
    opened_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at   TIMESTAMP,
    FOREIGN KEY (signal_id) REFERENCES signals(id)
);

CREATE TABLE IF NOT EXISTS daily_pnl (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    date             TEXT NOT NULL,
    starting_balance REAL NOT NULL,
    ending_balance   REAL NOT NULL,
    pnl              REAL NOT NULL,
    pnl_pct          REAL NOT NULL,
    trades_count     INTEGER DEFAULT 0,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS performance_metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    period      TEXT,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_klines_sym_iv ON klines(symbol, interval);
CREATE INDEX IF NOT EXISTS idx_klines_time   ON klines(open_time);
CREATE INDEX IF NOT EXISTS idx_trades_sym   ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_time  ON trades(trade_time);
CREATE INDEX IF NOT EXISTS idx_signals_sym  ON signals(symbol);
CREATE INDEX IF NOT EXISTS idx_signals_st   ON signals(status);
CREATE INDEX IF NOT EXISTS idx_signals_conf ON signals(confidence);
CREATE INDEX IF NOT EXISTS idx_positions_st ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_sym ON positions(symbol);
CREATE INDEX IF NOT EXISTS idx_daily_pnl_date ON daily_pnl(date);
CREATE INDEX IF NOT EXISTS idx_perf_metrics_name ON performance_metrics(metric_name);
"""


class Database:
    """Async SQLite with auto-connect, schema init, WAL mode, and clean shutdown."""

    def __init__(self) -> None:
        self._path = DB_DIR / "deltaterminal.db"
        self._db: Optional[aiosqlite.Connection] = None

    # ── Connection lifecycle ─────────────────────────────────────

    async def connect(self) -> None:
        if self._db is not None:
            return
        self._db = await aiosqlite.connect(str(self._path))
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA cache_size=-64000")  # 64MB page cache
        await self._db.execute("PRAGMA temp_store=MEMORY")
        await self._db.execute("PRAGMA mmap_size=268435456")  # 256MB mmap
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        logger.info("Database connected → {}", self._path)

    async def disconnect(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("Database disconnected")

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "Call db.connect() first"
        return self._db

    # ── Symbol CRUD ──────────────────────────────────────────────

    async def upsert_symbol(self, symbol: str, base_asset: str, quote_asset: str = "USDT") -> None:
        await self.db.execute(
            "INSERT INTO symbols(symbol,base_asset,quote_asset) VALUES(?,?,?) "
            "ON CONFLICT(symbol) DO UPDATE SET base_asset=excluded.base_asset",
            (symbol, base_asset, quote_asset),
        )
        await self.db.commit()

    async def get_active_symbols(self) -> List[Dict]:
        cur = await self.db.execute("SELECT * FROM symbols WHERE is_active=1 ORDER BY symbol")
        return [dict(r) for r in await cur.fetchall()]

    # ── Klines ───────────────────────────────────────────────────

    async def insert_klines(self, symbol: str, interval: str, rows: List[Dict]) -> None:
        await self.db.executemany(
            "INSERT OR REPLACE INTO klines(symbol,interval,open_time,open,high,low,close,volume,trades) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            [(symbol, interval, r["open_time"], r["open"], r["high"], r["low"],
              r["close"], r["volume"], r.get("trades", 0)) for r in rows],
        )
        await self.db.commit()

    async def get_klines(self, symbol: str, interval: str, limit: int = 100) -> List[Dict]:
        cur = await self.db.execute(
            "SELECT * FROM klines WHERE symbol=? AND interval=? ORDER BY open_time DESC LIMIT ?",
            (symbol, interval, limit),
        )
        return [dict(r) for r in await cur.fetchall()]

    # ── Trades ───────────────────────────────────────────────────

    async def insert_trades(self, symbol: str, trades: List[Dict]) -> None:
        await self.db.executemany(
            "INSERT INTO trades(symbol,price,quantity,is_buyer_maker,trade_time) VALUES(?,?,?,?,?)",
            [(symbol, t["price"], t["quantity"], t["is_buyer_maker"], t["trade_time"]) for t in trades],
        )
        await self.db.commit()

    # ── Signals ──────────────────────────────────────────────────

    async def save_signal(self, signal: Dict) -> int:
        cur = await self.db.execute(
            "INSERT INTO signals(symbol,signal_type,confidence,entry_price,stop_loss,take_profit,"
            "timeframes,indicators,regime,factors) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                signal["symbol"], signal["type"], signal["confidence"],
                signal.get("entry_price"), signal.get("stop_loss"), signal.get("take_profit"),
                json.dumps(signal.get("timeframes", [])),
                json.dumps(signal.get("indicators", {})),
                signal.get("regime"),
                json.dumps(signal.get("factors", [])),
            ),
        )
        await self.db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def get_active_signals(self) -> List[Dict]:
        cur = await self.db.execute(
            "SELECT * FROM signals WHERE status='active' ORDER BY confidence DESC"
        )
        return [dict(r) for r in await cur.fetchall()]

    async def update_signal_status(self, signal_id: int, status: str) -> None:
        await self.db.execute("UPDATE signals SET status=? WHERE id=?", (status, signal_id))
        await self.db.commit()

    # ── Positions ────────────────────────────────────────────────

    async def save_position(self, pos: Dict) -> int:
        cur = await self.db.execute(
            "INSERT INTO positions(signal_id,symbol,side,entry_price,quantity,leverage,stop_loss,take_profit) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                pos.get("signal_id"), pos["symbol"], pos["side"],
                pos["entry_price"], pos["quantity"], pos.get("leverage", 1),
                pos.get("stop_loss"), pos.get("take_profit"),
            ),
        )
        await self.db.commit()
        return cur.lastrowid  # type: ignore[return-value]

    async def get_open_positions(self) -> List[Dict]:
        cur = await self.db.execute("SELECT * FROM positions WHERE status='open'")
        return [dict(r) for r in await cur.fetchall()]

    async def close_position(self, position_id: int, pnl: float, fees: float = 0,
                             exit_reason: str = "unknown", hold_minutes: float = 0,
                             mae_pct: float = 0, mfe_pct: float = 0,
                             partial: bool = False, remaining_qty: float = 0) -> None:
        if partial and remaining_qty > 0:
            # Partial close: reduce quantity, keep open
            await self.db.execute(
                "UPDATE positions SET quantity=?, pnl=? WHERE id=?",
                (remaining_qty, pnl, position_id),
            )
        else:
            await self.db.execute(
                "UPDATE positions SET status='closed',pnl=?,fees=?,exit_reason=?,"
                "hold_minutes=?,mae_pct=?,mfe_pct=?,closed_at=CURRENT_TIMESTAMP WHERE id=?",
                (pnl, fees, exit_reason, hold_minutes, mae_pct, mfe_pct, position_id),
            )
        await self.db.commit()

    # ── Analytics ────────────────────────────────────────────────

    async def get_performance_stats(self) -> Dict:
        cur = await self.db.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) as wins, "
            "SUM(pnl) as total_pnl, AVG(pnl) as avg_pnl "
            "FROM positions WHERE status='closed'"
        )
        row = await cur.fetchone()
        return dict(row) if row else {}

    # ── Cleanup ──────────────────────────────────────────────────

    async def cleanup_old_data(self, days: int = 30) -> None:
        cutoff_ms = int((__import__("time").time() - days * 86400) * 1000)
        for table, col in [("klines", "open_time"), ("trades", "trade_time"), ("orderbook_snapshots", "timestamp")]:
            await self.db.execute(f"DELETE FROM {table} WHERE {col} < ?", (cutoff_ms,))
        await self.db.commit()


# ── Singleton ────────────────────────────────────────────────────
db = Database()
