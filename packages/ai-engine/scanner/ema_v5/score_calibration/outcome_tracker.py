"""
Outcome Tracker — Tracks forward prices for rejected candidates.

Periodically polls the database for untracked candidates and records
price at 15m, 30m, 1h, 2h, 4h, 8h, 24h intervals.
Also computes MFE, MAE, drawdown, runup, return, and RR achieved.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


class OutcomeTracker:
    """Tracks forward price outcomes for logged candidates."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"

    # Time horizons in seconds
    HORIZONS = {
        "price_15m": 15 * 60,
        "price_30m": 30 * 60,
        "price_1h": 60 * 60,
        "price_2h": 2 * 60 * 60,
        "price_4h": 4 * 60 * 60,
        "price_8h": 8 * 60 * 60,
        "price_24h": 24 * 60 * 60,
    }

    def __init__(self, db_path: Optional[Path] = None, price_fn=None) -> None:
        self._db_path = db_path or self.DB_PATH
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        # price_fn: callable(symbol) -> float (current mark price)
        self._price_fn = price_fn
        self._tracked = 0

    def set_price_function(self, price_fn) -> None:
        """Set the function to retrieve current prices."""
        self._price_fn = price_fn

    def update_outcomes(self) -> int:
        """Check all untracked candidates and record available price horizons.

        Returns number of candidates updated.
        """
        if not self._price_fn:
            return 0

        now = time.time()
        cur = self._conn.cursor()

        # Get candidates that still have horizons to track
        cur.execute("""
            SELECT id, symbol, direction, entry_price, stop_loss, take_profit, timestamp,
                   price_15m, price_30m, price_1h, price_2h, price_4h, price_8h, price_24h,
                   mfe, mae
            FROM candidates
            WHERE outcome_tracked = 0
            ORDER BY timestamp ASC
            LIMIT 200
        """)
        rows = cur.fetchall()
        updated = 0

        for row in rows:
            (cid, symbol, direction, entry, sl, tp, ts,
             p15, p30, p1h, p2h, p4h, p8h, p24h, mfe, mae) = row

            if not entry or entry <= 0:
                continue

            # Get current price
            try:
                current_price = self._price_fn(symbol)
                if not current_price or current_price <= 0:
                    continue
            except Exception:
                continue

            elapsed = now - ts
            updates = {}

            # Record price at each horizon
            for col, horizon in self.HORIZONS.items():
                existing = locals().get(f"p{col.split('_')[1]}", None)
                if existing is None and elapsed >= horizon:
                    updates[col] = current_price

            # Update MFE and MAE (running max/min of favorable/adverse excursion)
            if direction in ("LONG", "BUY"):
                excursion = (current_price - entry) / entry * 100
            elif direction in ("SHORT", "SELL"):
                excursion = (entry - current_price) / entry * 100
            else:
                continue

            new_mfe = max(mfe or 0, excursion)
            new_mae = min(mae or 0, excursion)
            if new_mfe != (mfe or 0) or new_mae != (mae or 0):
                updates["mfe"] = new_mfe
                updates["mae"] = new_mae

            # Check if all horizons are filled
            all_filled = all(
                locals().get(f"p{col.split('_')[1]}", None) is not None or elapsed < horizon
                for col, horizon in self.HORIZONS.items()
            )

            # Compute final metrics when 24h horizon is reached
            if elapsed >= self.HORIZONS["price_24h"] and not updates.get("return_pct"):
                price_24h = updates.get("price_24h") or p24h
                if price_24h and entry > 0:
                    if direction in ("LONG", "BUY"):
                        ret = (price_24h - entry) / entry * 100
                    else:
                        ret = (entry - price_24h) / entry * 100
                    updates["return_pct"] = round(ret, 4)

                    # RR achieved
                    risk = abs(entry - sl) if sl and sl > 0 else entry * 0.02
                    if risk > 0:
                        reward = abs(price_24h - entry)
                        updates["rr_achieved"] = round(reward / risk, 2) if direction in ("LONG", "BUY") else round(reward / risk, 2)

                    updates["outcome_tracked"] = 1

            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                values = list(updates.values()) + [cid]
                cur.execute(f"UPDATE candidates SET {set_clause} WHERE id = ?", values)
                updated += 1

        self._conn.commit()
        self._tracked += updated
        return updated

    def get_untracked_count(self) -> int:
        cur = self._conn.cursor()
        cur.execute("SELECT COUNT(*) FROM candidates WHERE outcome_tracked = 0")
        return cur.fetchone()[0]

    def get_tracked_count(self) -> int:
        cur = self._conn.cursor()
        cur.execute("SELECT COUNT(*) FROM candidates WHERE outcome_tracked = 1")
        return cur.fetchone()[0]

    def close(self) -> None:
        self._conn.close()
