"""
Signal Repository — Permanent SQLite persistence for institutional signals and performance tracking.
"""
from __future__ import annotations
import aiosqlite
import json
import time
from typing import Any, Dict, List, Optional
from pathlib import Path
from loguru import logger

class SignalRepository:
    def __init__(self, db_path: str = "data/institutional_v1.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """Create tables if they don't exist."""
        async with aiosqlite.connect(self.db_path) as db:
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
                    metadata TEXT
                )
            """)
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
            await db.commit()
        logger.info("SignalRepository initialized at {}", self.db_path)

    async def save_signal(self, sig: Dict[str, Any], is_elite: bool = False) -> str:
        """Permanently store a signal and optionally mark it as Elite."""
        sig_id = sig.get("id", f"{sig['symbol']}_{int(time.time())}")
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO signals (
                    id, timestamp, symbol, side, confidence, entry, stop_loss, take_profit,
                    risk_reward, open_interest, oi_delta, funding_rate, exchange_flow,
                    delta, cvd, absorption_score, sweep_score, spoofing_score,
                    market_regime, institutional_score, mtf_alignment, status, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                sig_id, time.time(), sig['symbol'], sig['type'], sig['confidence'],
                sig['entry_price'], sig['stop_loss'], sig['take_profit'],
                sig.get('risk_reward', 0), sig.get('open_interest', 0), sig.get('oi_delta', 0),
                sig.get('funding_rate', 0), sig.get('exchange_flow', 0),
                sig.get('delta', 0), sig.get('cvd', 0), sig.get('absorption_score', 0),
                sig.get('sweep_score', 0), sig.get('spoofing_score', 0),
                sig.get('regime', 'unknown'), sig.get('institutional_score', 0),
                sig.get('mtf_alignment', 0), sig.get('status', 'active'),
                json.dumps(sig.get('factors', {}))
            ))
            
            if is_elite:
                await db.execute("INSERT OR IGNORE INTO elite_signals (signal_id, captured_at) VALUES (?, ?)",
                                 (sig_id, time.time()))
            
            await db.commit()
        return sig_id

# Global Instance
repo = SignalRepository()