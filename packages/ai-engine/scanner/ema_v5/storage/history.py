"""
EMA_V5 History — Complete audit trail. Every signal stored, never overwritten.
Coordinates between database, JSON, and Excel for consistent history.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from loguru import logger

from .database import EMAv5Database
from .json_storage import EMAv5JsonStorage
from .serializer import EMAv5Serializer


class EMAv5History:
    """Maintains complete audit history across all storage backends.
    
    Write path: Signal → Serialize → DB + JSON + Excel (append).
    Read path: DB primary, JSON backup, Excel export.
    """

    def __init__(self, db: Optional[EMAv5Database] = None,
                 json_store: Optional[EMAv5JsonStorage] = None) -> None:
        self._db = db or EMAv5Database()
        self._json = json_store or EMAv5JsonStorage()
        self._serializer = EMAv5Serializer()

    def record_signal(self, signal: Dict[str, Any]) -> str:
        """Record a new signal across all backends. Returns UUID.
        
        Idempotent: same signal (same symbol+entry+timestamp) gets same UUID.
        """
        # Serialize to canonical format
        serialized = self._serializer.serialize_signal(signal)
        uuid = serialized["uuid"]

        # Duplicate check
        if self._db.uuid_exists(uuid):
            logger.debug("EMAv5 signal {} already exists, skipping", uuid)
            return uuid

        # Write to database (primary store)
        self._db.store_signal(serialized)

        # Append to JSON history (backup + dashboard access)
        self._json.append_signal_history(serialized)

        # Append to Excel (latest snapshot)
        self._refresh_excel()

        logger.info("📊 EMA_V5 HISTORY: recorded {} {} @ {:.4f} uuid={}",
                     signal.get("side", "?"), signal.get("symbol", "?"),
                     signal.get("entry", 0), uuid)
        return uuid

    def record_trade_close(self, uuid: str, close_data: Dict[str, Any]) -> bool:
        """Record a trade close event. Append-only."""
        serialized = self._serializer.serialize_trade_close(uuid, close_data)

        # Write to database history table
        self._db.store_trade_close(serialized)

        # Update signal result in main table
        self._db.update_signal_result(
            uuid=uuid,
            result=close_data.get("outcome", ""),
            pnl=close_data.get("pnl", 0),
            hold_time=close_data.get("hold_minutes", 0),
        )

        # Append to JSON trade history
        self._json.append_trade_history(serialized)

        # Refresh Excel with updated results
        self._refresh_excel()

        logger.info("📊 EMA_V5 TRADE CLOSE: {} pnl={:.4f} reason={}",
                     uuid, close_data.get("pnl", 0), close_data.get("exit_reason", ""))
        return True

    def _refresh_excel(self) -> None:
        """Refresh Excel file with current state from DB."""
        try:
            from .excel_writer import EMAv5ExcelWriter
            excel = EMAv5ExcelWriter()
            signals = self._db.get_all_signals()
            excel.write(signals)
        except Exception as e:
            logger.debug("EMAv5 Excel refresh skipped: {}", e)

    # ── Read Operations ──────────────────────────────────────────

    def get_signal(self, uuid: str) -> Optional[Dict[str, Any]]:
        """Get a signal by UUID."""
        return self._db.get_signal(uuid)

    def get_signals(self, symbol: Optional[str] = None, side: Optional[str] = None,
                    date: Optional[str] = None, limit: int = 1000) -> List[Dict[str, Any]]:
        """Get signals with filters from DB."""
        return self._db.get_signals(symbol=symbol, side=side, date=date, limit=limit)

    def get_trade_history(self, uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get trade close history."""
        return self._db.get_trade_history(uuid=uuid)

    def get_all_signals(self) -> List[Dict[str, Any]]:
        """Get all signals — full audit."""
        return self._db.get_all_signals()

    def get_stats(self) -> Dict[str, Any]:
        """Get aggregate statistics."""
        return self._db.get_stats()

    def count_signals(self) -> int:
        """Count total signals."""
        return self._db.count_signals()
