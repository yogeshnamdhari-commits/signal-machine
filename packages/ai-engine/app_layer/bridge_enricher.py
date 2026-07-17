"""
Bridge Enricher — Fills the data gap between bridge signals and full DB data.

The bridge (signals.json) only contains a subset of signal fields:
    symbol, side, confidence, entry_price, stop_loss, take_profit,
    risk_reward, status, timestamp, id, regime, institutional_score

But the App Layer needs the full institutional data:
    cvd, delta, exchange_flow, oi_delta, funding_rate,
    absorption_score, sweep_score, mss_score, fvg_score, mtf_alignment,
    open_interest, spoofing_score

This module reads the full signal data from the database and enriches
the bridge signals with the missing fields.

READ-ONLY: never modifies the database or bridge files.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# DATABASE PATH
# ═══════════════════════════════════════════════════════════════

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


class BridgeEnricher:
    """
    Enriches bridge signals with full institutional data from the database.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH

    def enrich_signals(self, bridge_signals: List[Dict]) -> List[Dict]:
        """
        Enrich bridge signals with full data from database.

        Args:
            bridge_signals: List of signals from bridge (signals.json)

        Returns:
            List of enriched signal dicts with all institutional fields
        """
        if not bridge_signals:
            return []

        enriched = []
        conn = None

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            for sig in bridge_signals:
                signal_id = sig.get("id", "")
                if not signal_id:
                    enriched.append(sig)
                    continue

                # Query full signal data from DB
                cur.execute(
                    "SELECT * FROM signals WHERE id = ? LIMIT 1",
                    (signal_id,),
                )
                row = cur.fetchone()

                if row:
                    # Merge DB data into bridge signal
                    db_data = dict(row)
                    merged = self._merge_signal(sig, db_data)
                    enriched.append(merged)
                else:
                    # Signal not in DB — use bridge data as-is
                    enriched.append(sig)

        except Exception as e:
            logger.warning("Bridge enrichment error: {}", e)
            # Return bridge signals as-is on error
            enriched = bridge_signals
        finally:
            if conn:
                conn.close()

        return enriched

    def _merge_signal(self, bridge_sig: Dict, db_data: Dict) -> Dict:
        """Merge bridge signal with full DB data."""
        merged = dict(bridge_sig)

        # Add missing institutional fields from DB
        institutional_fields = [
            "open_interest", "oi_delta", "funding_rate", "exchange_flow",
            "delta", "cvd", "absorption_score", "sweep_score", "spoofing_score",
            "market_regime", "mtf_alignment", "mss_score", "fvg_score",
            "take_profit_2", "take_profit_3", "rr_1", "rr_2", "rr_3",
            "sl_source", "tp1_source", "tp2_source", "tp3_source",
            "entry_reason", "metadata",
        ]

        for field in institutional_fields:
            if field not in merged or merged[field] is None or merged[field] == 0:
                db_val = db_data.get(field)
                if db_val is not None and db_val != 0 and db_val != "":
                    merged[field] = db_val

        # Normalize confidence (DB stores 0-1, bridge stores 0-100)
        if "confidence" in merged and merged["confidence"] <= 1.0:
            merged["confidence"] = merged["confidence"] * 100

        # Ensure regime field is consistent
        if "regime" not in merged or not merged.get("regime"):
            merged["regime"] = db_data.get("market_regime", "unknown")

        return merged

    def get_enriched_signal(self, signal_id: str) -> Optional[Dict]:
        """Get a single enriched signal by ID."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("SELECT * FROM signals WHERE id = ? LIMIT 1", (signal_id,))
            row = cur.fetchone()
            conn.close()

            if row:
                return dict(row)
        except Exception as e:
            logger.warning("Error fetching signal {}: {}", signal_id, e)

        return None

    def get_all_active_signals(self) -> List[Dict]:
        """Get all active signals with full data from DB."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Get signals from today
            today_start = time.time() - (time.time() % 86400)
            cur.execute(
                "SELECT * FROM signals WHERE status = 'active' AND timestamp >= ? "
                "ORDER BY timestamp DESC",
                (today_start,),
            )
            rows = cur.fetchall()
            conn.close()

            return [dict(r) for r in rows]
        except Exception as e:
            logger.warning("Error fetching active signals: {}", e)
            return []
