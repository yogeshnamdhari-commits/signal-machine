"""
Signal Repository — Permanent SQLite persistence for institutional signals and performance tracking.
"""
from __future__ import annotations
import aiosqlite
from pathlib import Path
import json
import time
from typing import Any, Dict, List, Optional
from pathlib import Path
from loguru import logger


class SignalRepository:
    def __init__(self, db_path: str = "data/institutional_v1.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    def _connect(self):
        """Create a connection with busy_timeout + WAL to prevent 'database is locked'.

        SQLite uses file-level locking. When async tasks hit the DB concurrently,
        the default 5s busy_timeout expires and raises 'database is locked'.
        10s timeout + WAL mode handles even heavy concurrent access.
        """
        import sqlite3
        return aiosqlite.connect(
            self.db_path,
            timeout=10,
            isolation_level=None,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )

    async def initialize(self) -> None:
        """Create tables if they don't exist."""
        async with self._connect() as db:
            # P0: WAL mode + busy_timeout at connection level
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA busy_timeout=10000")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    id TEXT PRIMARY KEY,
                    timestamp REAL,
                    symbol TEXT,
                    side TEXT,
                    confidence REAL,
                    entry REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    risk_reward REAL,
                    open_interest REAL,
                    oi_delta REAL,
                    funding_rate REAL,
                    exchange_flow REAL,
                    delta REAL,
                    cvd REAL,
                    absorption_score REAL,
                    sweep_score REAL,
                    spoofing_score REAL,
                    market_regime TEXT,
                    institutional_score REAL,
                    mtf_alignment INTEGER,
                    status TEXT,
                    metadata TEXT,
                    take_profit_2 REAL DEFAULT 0,
                    take_profit_3 REAL DEFAULT 0,
                    rr_1 REAL DEFAULT 0,
                    rr_2 REAL DEFAULT 0,
                    rr_3 REAL DEFAULT 0,
                    sl_source TEXT DEFAULT '',
                    tp1_source TEXT DEFAULT '',
                    tp2_source TEXT DEFAULT '',
                    tp3_source TEXT DEFAULT ''
                )
            """)
            # Migration: add multi-target columns if missing (safe for existing DBs)
            for col, default in [
                ("take_profit_2", 0), ("take_profit_3", 0),
                ("rr_1", 0), ("rr_2", 0), ("rr_3", 0),
                ("sl_source", "''"), ("tp1_source", "''"),
                ("tp2_source", "''"), ("tp3_source", "''"),
            ]:
                try:
                    await db.execute(f"ALTER TABLE signals ADD COLUMN {col} DEFAULT {default}")
                except Exception:
                    pass  # Column already exists
            await db.execute("CREATE INDEX IF NOT EXISTS idx_sig_sym ON signals(symbol)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_sig_score ON signals(institutional_score)")

            # Elite Signals Table (Subset of signals with specific filter)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS elite_signals (
                    signal_id TEXT PRIMARY KEY,
                    captured_at REAL,
                    FOREIGN KEY(signal_id) REFERENCES signals(id)
                )
            """)

            # Symbols table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS symbols (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol     TEXT UNIQUE NOT NULL,
                    base_asset TEXT NOT NULL,
                    quote_asset TEXT DEFAULT 'USDT',
                    is_active  INTEGER DEFAULT 1,
                    created_at REAL DEFAULT (strftime('%s','now'))
                )
            """)

            # Positions table
            await db.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id   TEXT,
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
                    opened_at   REAL DEFAULT (strftime('%s','now')),
                    closed_at   REAL,
                    take_profit_2 REAL DEFAULT 0,
                    take_profit_3 REAL DEFAULT 0,
                    current_tp_index INTEGER DEFAULT 1,
                    FOREIGN KEY (signal_id) REFERENCES signals(id)
                )
            """)
            # Migration: add multi-target columns if missing
            for col, default in [("take_profit_2", 0), ("take_profit_3", 0), ("current_tp_index", 1)]:
                try:
                    await db.execute(f"ALTER TABLE positions ADD COLUMN {col} DEFAULT {default}")
                except Exception:
                    pass
            # PHASE 2: Migration — add all metadata columns used by open_position INSERT
            # These may be missing from older DB schemas, causing confidence=0 in archive
            _pos_migrations = [
                ("confidence", "REAL DEFAULT 0"),
                ("regime", "TEXT DEFAULT 'unknown'"),
                ("institutional_score", "REAL DEFAULT 0"),
                ("risk_reward", "REAL DEFAULT 0"),
                ("session", "TEXT DEFAULT 'unknown'"),
                ("strategy_version", "TEXT DEFAULT 'current'"),
                ("hold_minutes", "REAL DEFAULT 0"),
                ("exit_reason", "TEXT DEFAULT ''"),
                ("planned_rr", "REAL DEFAULT 0"),
                ("volatility_score", "REAL DEFAULT 0"),
                ("at_open_regime", "TEXT DEFAULT ''"),
                ("at_open_session", "TEXT DEFAULT ''"),
                ("mss_score", "REAL DEFAULT 0"),
                ("fvg_score", "REAL DEFAULT 0"),
                ("alpha_score", "REAL DEFAULT 0"),
                ("alpha_tier", "TEXT DEFAULT 'C'"),
                ("entry_reason", "TEXT DEFAULT ''"),
                ("mfe_pct", "REAL DEFAULT 0"),
                ("mae_pct", "REAL DEFAULT 0"),
                ("highest_pnl", "REAL DEFAULT 0"),
                ("quiet_market_blocked", "INTEGER DEFAULT 0"),
                ("outcome", "TEXT DEFAULT ''"),
                ("realized_r", "REAL DEFAULT 0"),
            ]
            for col, typedef in _pos_migrations:
                try:
                    await db.execute(f"ALTER TABLE positions ADD COLUMN {col} DEFAULT 0")
                except Exception:
                    pass
            await db.execute("CREATE INDEX IF NOT EXISTS idx_positions_st ON positions(status)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_positions_sym ON positions(symbol)")

            # PHASE 2: Positions Archive table — ensures schema matches close_position INSERT
            await db.execute("""
                CREATE TABLE IF NOT EXISTS positions_archive (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id   TEXT,
                    symbol      TEXT NOT NULL,
                    side        TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    quantity    REAL NOT NULL,
                    leverage    INTEGER DEFAULT 1,
                    stop_loss   REAL,
                    take_profit REAL,
                    pnl         REAL DEFAULT 0,
                    fees        REAL DEFAULT 0,
                    status      TEXT DEFAULT 'closed',
                    opened_at   REAL,
                    closed_at   REAL,
                    take_profit_2 REAL DEFAULT 0,
                    take_profit_3 REAL DEFAULT 0,
                    current_tp_index INTEGER DEFAULT 1,
                    exit_reason TEXT DEFAULT '',
                    strategy_version TEXT DEFAULT 'current',
                    confidence  REAL DEFAULT 0,
                    regime      TEXT DEFAULT 'unknown',
                    institutional_score REAL DEFAULT 0,
                    risk_reward REAL DEFAULT 0,
                    hold_minutes REAL DEFAULT 0,
                    session     TEXT DEFAULT 'unknown',
                    mfe_pct     REAL DEFAULT 0,
                    mae_pct     REAL DEFAULT 0,
                    alpha_score REAL DEFAULT 0,
                    alpha_tier  TEXT DEFAULT 'C',
                    mss_score   REAL DEFAULT 0,
                    fvg_score   REAL DEFAULT 0,
                    entry_reason TEXT DEFAULT '',
                    outcome     TEXT DEFAULT '',
                    realized_r  REAL DEFAULT 0,
                    planned_rr  REAL DEFAULT 0,
                    at_open_regime TEXT DEFAULT '',
                    at_open_session TEXT DEFAULT '',
                    volatility_score REAL DEFAULT 0,
                    quiet_market_blocked INTEGER DEFAULT 0,
                    highest_pnl REAL DEFAULT 0
                )
            """)
            # Migration: add any missing columns to archive
            _archive_migrations = [
                ("confidence", 0), ("regime", "'unknown'"), ("institutional_score", 0),
                ("risk_reward", 0), ("hold_minutes", 0), ("session", "'unknown'"),
                ("mfe_pct", 0), ("mae_pct", 0), ("alpha_score", 0), ("alpha_tier", "'C'"),
                ("mss_score", 0), ("fvg_score", 0), ("entry_reason", "''"),
                ("outcome", "''"), ("realized_r", 0), ("planned_rr", 0),
                ("at_open_regime", "''"), ("at_open_session", "''"),
                ("volatility_score", 0), ("quiet_market_blocked", 0), ("highest_pnl", 0),
                ("strategy_version", "'current'"), ("exit_reason", "''"),
                ("current_tp_index", 1), ("take_profit_2", 0), ("take_profit_3", 0),
            ]
            for col, default in _archive_migrations:
                try:
                    await db.execute(f"ALTER TABLE positions_archive ADD COLUMN {col} DEFAULT {default}")
                except Exception:
                    pass
            await db.execute("CREATE INDEX IF NOT EXISTS idx_pa_sym ON positions_archive(symbol)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_pa_closed ON positions_archive(closed_at)")

            await db.commit()
        logger.info("SignalRepository initialized at {}", self.db_path)

    async def save_signal(self, sig: Dict[str, Any], is_elite: bool = False) -> str:
        """Permanently store a signal and optionally mark it as Elite.
        Deduplicates: skips if an active signal with same symbol+side+entry exists."""
        sig_id = sig.get("id", f"{sig['symbol']}_{int(time.time())}")
        async with self._connect() as db:
            # Deduplication check: skip if active signal exists for same symbol+side+entry
            entry_price = sig.get('entry_price', 0)
            cur = await db.execute(
                "SELECT COUNT(*) FROM signals WHERE symbol=? AND side=? AND status='active' "
                "AND ABS(entry-?) < 0.001",
                (sig['symbol'], sig['type'], entry_price)
            )
            row = await cur.fetchone()
            if row and row[0] > 0:
                logger.debug("Duplicate signal skipped: {} {} @ {}", sig['symbol'], sig['type'], entry_price)
                return ""
        async with self._connect() as db:
            await db.execute("""
                INSERT OR REPLACE INTO signals (
                    id, timestamp, symbol, side, confidence, entry, stop_loss, take_profit,
                    risk_reward, open_interest, oi_delta, funding_rate, exchange_flow,
                    delta, cvd, absorption_score, sweep_score, spoofing_score,
                    market_regime, institutional_score, mtf_alignment, status, metadata,
                    take_profit_2, take_profit_3, rr_1, rr_2, rr_3,
                    sl_source, tp1_source, tp2_source, tp3_source,
                    mss_score, fvg_score, entry_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                sig_id, time.time(), sig['symbol'], sig['type'], sig['confidence'],
                sig['entry_price'], sig['stop_loss'], sig['take_profit'],
                sig.get('risk_reward', 0), sig.get('open_interest', 0), sig.get('oi_delta', 0),
                sig.get('funding_rate', 0), sig.get('exchange_flow', 0),
                sig.get('delta', 0), sig.get('cvd', 0), sig.get('absorption_score', 0),
                sig.get('sweep_score', 0), sig.get('spoofing_score', 0),
                sig.get('regime', 'unknown'), sig.get('institutional_score', 0),
                sig.get('mtf_alignment', 0), sig.get('status', 'active'),
                json.dumps(sig.get('factors', {})),
                sig.get('take_profit_2', 0), sig.get('take_profit_3', 0),
                sig.get('rr_1', 0), sig.get('rr_2', 0), sig.get('rr_3', 0),
                sig.get('sl_source', ''), sig.get('tp1_source', ''),
                sig.get('tp2_source', ''), sig.get('tp3_source', ''),
                sig.get('mss_score', 0), sig.get('fvg_score', 0),
                sig.get('entry_reason', ''),
            ))

            if is_elite:
                await db.execute("INSERT OR IGNORE INTO elite_signals (signal_id, captured_at) VALUES (?, ?)",
                                 (sig_id, time.time()))

            await db.commit()
        return sig_id

    async def disconnect(self) -> None:
        """Compatibility no-op — connections are per-operation in this repository."""
        logger.debug("SignalRepository disconnect (no-op)")

    async def upsert_symbol(self, symbol: str, base: str, quote: str = "USDT") -> None:
        """Insert or update a tracked symbol."""
        async with self._connect() as db:
            await db.execute(
                "INSERT INTO symbols (symbol, base_asset, quote_asset, is_active) "
                "VALUES (?, ?, ?, 1) "
                "ON CONFLICT(symbol) DO UPDATE SET base_asset=?, quote_asset=?, is_active=1",
                (symbol, base, quote, base, quote),
            )
            await db.commit()

    async def get_open_positions(self) -> List[Dict[str, Any]]:
        """Return all positions with status='open'."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM positions WHERE status='open'"
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def open_position(
        self,
        signal_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        quantity: float,
        leverage: int = 1,
        stop_loss: float = 0.0,
        take_profit: float = 0.0,
        fees: float = 0.0,
        take_profit_2: float = 0.0,
        take_profit_3: float = 0.0,
        # FIX #6: Institutional data fields
        confidence: float = 0.0,
        regime: str = "",
        institutional_score: float = 0.0,
        risk_reward: float = 0.0,
        session: str = "",
        strategy_version: str = "current",
        # FIX #4/7: Quiet market + persistence fields
        planned_rr: float = 0.0,
        volatility_score: float = 0.0,
        quiet_market_blocked: int = 0,
        # FIX: Add MSS and FVG scores
        mss_score: float = 0.0,
        fvg_score: float = 0.0,
    ) -> Optional[int]:
        # ═══════════════════════════════════════════════════════════════
        # FINAL SAFETY NET: Block zero-confidence trades at DB level
        # June 16 proof: 4 trades with conf=0, regime=unknown, inst=0
        # slipped through upstream gates. This is the last line of defense.
        # ═══════════════════════════════════════════════════════════════
        # EMA V5 signals use confidence (0-100 scale), not institutional_score
        _is_ema_v5 = strategy_version == "ema_v5"
        if confidence < 0.85 or regime in ("unknown", "") or (institutional_score == 0 and not _is_ema_v5):
            logger.warning(
                "🚫 DB_GATE: BLOCKED {} {} — conf={:.1%} regime={} inst_score={}",
                symbol, side, confidence, regime, institutional_score,
            )
            return None
        # ═══════════════════════════════════════════════════════════════
        # SAFETY NET: Reject trades with missing SL/TP
        # June 16 proof: BCHUSDT, SYNUSDT, METUSDT opened with SL=0, TP=0
        # had zero exit protection, lost $3.96 total
        # ═══════════════════════════════════════════════════════════════
        if stop_loss == 0 or take_profit == 0:
            logger.warning(
                "🚫 DB_GATE: REJECTED {} {} — SL={} TP={} (missing risk params)",
                symbol, side, stop_loss, take_profit,
            )
            return None
        # ═══════════════════════════════════════════════════════════════
        # FINAL SAFETY NET: Block legacy engine trades at DB level
        # 99.7% of losses came from legacy/inst_v1/inst_v2/current engines
        # Only production_v2 and ema_v5 strategy_versions are allowed
        # ═══════════════════════════════════════════════════════════════
        if strategy_version not in ("production_v2", "ema_v5"):
            logger.warning(
                "🚫 DB_GATE: BLOCKED {} {} — strategy_version={}",
                symbol, side, strategy_version,
            )
            return None
        """Open a new position and return its DB id."""
        now = time.time()
        async with self._connect() as db:
            cursor = await db.execute(
                "INSERT INTO positions "
                "(signal_id, symbol, side, entry_price, quantity, leverage, stop_loss, take_profit, fees, status, opened_at, take_profit_2, take_profit_3, "
                "confidence, regime, institutional_score, risk_reward, session, strategy_version, hold_minutes, exit_reason, "
                "planned_rr, volatility_score, at_open_regime, at_open_session, mss_score, fvg_score, "
                "alpha_score, alpha_tier, entry_reason, mfe_pct, mae_pct, highest_pnl, quiet_market_blocked) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, '', ?, ?, ?, ?, ?, ?, 0.0, 'C', '', 0, 0, 0, ?)",
                (signal_id, symbol, side.upper(), entry_price, quantity, leverage,
                 stop_loss, take_profit, fees, now, take_profit_2, take_profit_3,
                 confidence, regime, institutional_score, risk_reward, session, strategy_version,
                 planned_rr, volatility_score, regime, session, mss_score, fvg_score,
                 0),
            )
            await db.commit()
            return cursor.lastrowid

    async def close_position(
        self, position_id: int, pnl: float, partial: bool = False, remaining_qty: float = 0,
        hold_minutes: float = 0, mae_pct: float = 0, mfe_pct: float = 0,
        exit_reason: str = "", realized_r: float = 0,
    ) -> None:
        """Close a position and record PnL.

        If partial=True, reduce the position quantity and accumulate PnL
        instead of fully closing.

        On full close: copy row to positions_archive, then delete from positions.
        This prevents double-counting between the two tables."""
        async with self._connect() as db:
            if partial and remaining_qty > 0:
                # Reduce quantity, add PnL, keep position open
                await db.execute(
                    "UPDATE positions SET quantity=?, pnl=pnl+? WHERE id=?",
                    (remaining_qty, pnl, position_id),
                )
            else:
                # ── Read full row BEFORE closing (for archival) ──
                db.row_factory = aiosqlite.Row
                cur = await db.execute("SELECT * FROM positions WHERE id=?", (position_id,))
                row = await cur.fetchone()
                now = time.time()

                # Full close — store lifecycle data
                # Calculate total accumulated PnL (existing + incremental)
                total_pnl = (r.get("pnl", 0) or 0) + pnl
                await db.execute(
                    """UPDATE positions SET status='closed', pnl=?, closed_at=?,
                       hold_minutes=?, mae_pct=?, mfe_pct=?, exit_reason=?,
                       realized_r=?, outcome=?
                       WHERE id=?""",
                    (round(total_pnl, 4), now, hold_minutes, mae_pct, mfe_pct, exit_reason,
                     realized_r, "win" if total_pnl > 0 else "loss", position_id),
                )

                # ── Archive: copy to positions_archive, then delete from positions ──
                if row:
                    r = dict(row)
                    await db.execute(
                        """INSERT INTO positions_archive
                           (id, signal_id, symbol, side, entry_price, quantity, leverage,
                            stop_loss, take_profit, pnl, fees, status, opened_at, closed_at,
                            take_profit_2, take_profit_3, current_tp_index, exit_reason,
                            strategy_version, confidence, regime, institutional_score,
                            risk_reward, hold_minutes, session, mfe_pct, mae_pct,
                            alpha_score, alpha_tier, mss_score, fvg_score, entry_reason,
                            outcome, realized_r, planned_rr, at_open_regime, at_open_session,
                            volatility_score, quiet_market_blocked, highest_pnl)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            r.get("id", position_id),  # Preserve original ID
                            r.get("signal_id", ""), r.get("symbol", ""), r.get("side", ""),
                            r.get("entry_price", 0), r.get("quantity", 0), r.get("leverage", 1),
                            r.get("stop_loss", 0), r.get("take_profit", 0),
                            round(pnl + (r.get("pnl", 0) or 0), 4),
                            r.get("fees", 0), "closed",
                            r.get("opened_at", 0), now,
                            r.get("take_profit_2", 0), r.get("take_profit_3", 0),
                            r.get("current_tp_index", 1), exit_reason,
                            r.get("strategy_version", "current"),
                            r.get("confidence", 0), r.get("regime", ""),
                            r.get("institutional_score", 0), r.get("risk_reward", 0),
                            round(hold_minutes, 1), r.get("session", ""),
                            round(mfe_pct, 4), round(mae_pct, 4),
                            r.get("alpha_score", 0), r.get("alpha_tier", "C"),
                            r.get("mss_score", 0), r.get("fvg_score", 0),
                            r.get("entry_reason", ""),
                            "win" if total_pnl > 0 else "loss",
                            round(realized_r, 2), r.get("planned_rr", 0),
                            r.get("at_open_regime", ""), r.get("at_open_session", ""),
                            r.get("volatility_score", 0), r.get("quiet_market_blocked", 0),
                            round(r.get("highest_pnl", 0) or 0, 4),
                        ),
                    )
                    # Delete from live positions table (trade is now in archive)
                    await db.execute("DELETE FROM positions WHERE id=?", (position_id,))

            await db.commit()

    async def update_position_peak(self, position_id: int, highest_pnl: float, mfe_pct: float = 0.0) -> None:
        """Persist the highest unrealized PnR (R-multiples) and MFE% for a live position.

        Called every scan cycle to ensure trailing stop peak state survives restarts.
        P0 fix: without this, engine restarts reset _highest_pnl to 0, causing time_exit_6h.
        FIX 5: Also persists MFE% so MFE trailing stop survives restarts.
        """
        async with self._connect() as db:
            await db.execute(
                "UPDATE positions SET highest_pnl=?, mfe_pct=? WHERE id=?",
                (round(highest_pnl, 4), round(mfe_pct, 4), position_id),
            )
            await db.commit()

    async def update_position_sl(self, position_id: int, new_stop_loss: float) -> None:
        """Update stop_loss for a live position (SL trailing after partial TP exits).

        Called by FIX 3: TP1 → breakeven, TP2 → trail to TP1.
        Ensures the remaining position can never lose money after first partial profit.
        """
        async with self._connect() as db:
            await db.execute(
                "UPDATE positions SET stop_loss=? WHERE id=?",
                (round(new_stop_loss, 8), position_id),
            )
            await db.commit()

    async def update_signal_status(self, signal_id: str, status: str) -> None:
        """Update the status of a signal (e.g. 'expired', 'closed')."""
        async with self._connect() as db:
            await db.execute(
                "UPDATE signals SET status=? WHERE id=?",
                (status, signal_id),
            )
            await db.commit()

    async def expire_zombie_signals(self, max_age_hours: int = 24) -> int:
        """Expire active signals older than max_age_hours with no open position.

        These 'zombie' signals block dedup for their symbols, preventing new
        signals from being emitted. This method cleans them up by marking them
        as 'expired' in the database.

        Returns the number of zombie signals expired.
        """
        cutoff = time.time() - (max_age_hours * 3600)
        async with self._connect() as db:
            # Find zombie active signals: old + no corresponding open position
            cur = await db.execute(
                "SELECT s.id FROM signals s "
                "LEFT JOIN positions p ON s.id = p.signal_id "
                "WHERE s.status = 'active' "
                "AND s.timestamp < ? "
                "AND p.id IS NULL",
                (cutoff,),
            )
            zombies = await cur.fetchall()
            if zombies:
                await db.executemany(
                    "UPDATE signals SET status = 'expired' WHERE id = ?",
                    [(z[0],) for z in zombies],
                )
                await db.commit()
                logger.info(
                    "Expired {} zombie signals (age > {}h, no position)",
                    len(zombies), max_age_hours,
                )
            return len(zombies)

    async def cleanup_old_data(self, days: int = 30) -> None:
        """Delete old data: signals, archived positions, and elite signals older than *days* days.
        NOTE: positions (live table) is no longer cleaned up here since closed trades
        are moved to positions_archive on close."""
        cutoff = time.time() - (days * 86400)
        async with self._connect() as db:
            await db.execute("DELETE FROM signals WHERE timestamp < ?", (cutoff,))
            await db.execute("DELETE FROM positions_archive WHERE closed_at < ?", (cutoff,))
            await db.execute("DELETE FROM elite_signals WHERE captured_at < ?", (cutoff,))
            await db.commit()
        logger.debug("Cleanup: removed data older than {} days", days)


# Global singleton — imported as `repo` by core.engine and scanner.signal_router
repo = SignalRepository(db_path=str(Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"))
