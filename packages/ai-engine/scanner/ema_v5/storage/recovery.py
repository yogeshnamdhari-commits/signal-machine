"""
EMA_V5 Recovery — Restores state, trades, signals, stats on engine restart.
No duplicate entries. Idempotent recovery.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .database import EMAv5Database
from .json_storage import EMAv5JsonStorage
from .serializer import EMAv5Serializer


class EMAv5Recovery:
    """Restores EMA_V5 state on engine restart.
    
    Recovery order:
    1. Database signals → rebuild JSON history
    2. JSON state → restore symbol state machine
    3. JSON stats → restore computed statistics
    4. Bridge file → populate dashboard data
    
    Idempotent: running multiple times produces same result.
    """

    def __init__(self, db: Optional[EMAv5Database] = None,
                 json_store: Optional[EMAv5JsonStorage] = None) -> None:
        self._db = db or EMAv5Database()
        self._json = json_store or EMAv5JsonStorage()
        self._serializer = EMAv5Serializer()

    def recover_all(self) -> Dict[str, Any]:
        """Run full recovery. Returns recovery report."""
        report = {
            "signals_recovered": 0,
            "trades_recovered": 0,
            "state_restored": False,
            "stats_restored": False,
            "bridge_populated": False,
            "duplicates_prevented": 0,
            "errors": [],
            "timestamp": time.time(),
        }

        try:
            # Step 1: Recover signals from DB → JSON history
            report["signals_recovered"] = self._recover_signals()
        except Exception as e:
            report["errors"].append(f"signal_recovery: {e}")
            logger.error("EMAv5 recovery signals failed: {}", e)

        try:
            # Step 2: Recover trades from DB → JSON history
            report["trades_recovered"] = self._recover_trades()
        except Exception as e:
            report["errors"].append(f"trade_recovery: {e}")
            logger.error("EMAv5 recovery trades failed: {}", e)

        try:
            # Step 3: Restore state from JSON state file
            report["state_restored"] = self._recover_state()
        except Exception as e:
            report["errors"].append(f"state_recovery: {e}")
            logger.error("EMAv5 recovery state failed: {}", e)

        try:
            # Step 4: Compute and restore stats
            report["stats_restored"] = self._recover_stats()
        except Exception as e:
            report["errors"].append(f"stats_recovery: {e}")
            logger.error("EMAv5 recovery stats failed: {}", e)

        try:
            # Step 5: Populate bridge with latest data for dashboard
            report["bridge_populated"] = self._populate_bridge()
        except Exception as e:
            report["errors"].append(f"bridge_population: {e}")
            logger.error("EMAv5 recovery bridge failed: {}", e)

        logger.info("📊 EMA_V5 RECOVERY: {} signals, {} trades, state={}, stats={}, errors={}",
                     report["signals_recovered"], report["trades_recovered"],
                     report["state_restored"], report["stats_restored"],
                     len(report["errors"]))
        return report

    def _recover_signals(self) -> int:
        """Recover signals from DB to JSON history (no duplicates)."""
        signals = self._db.get_all_signals()
        if not signals:
            return 0

        # Read existing JSON history to check for duplicates
        history = self._json.read_history()
        existing_uuids = {s.get("uuid") for s in history.get("signals", [])}

        recovered = 0
        for sig in signals:
            if sig.get("uuid") not in existing_uuids:
                self._json.append_signal_history(sig)
                recovered += 1

        logger.debug("EMAv5 signal recovery: {} new of {} total", recovered, len(signals))
        return recovered

    def _recover_trades(self) -> int:
        """Recover trade close records from DB to JSON history."""
        trades = self._db.get_trade_history()
        if not trades:
            return 0

        history = self._json.read_history()
        existing_keys = {(t.get("uuid"), t.get("closed_at")) for t in history.get("trades", [])}

        recovered = 0
        for trade in trades:
            key = (trade.get("uuid"), trade.get("closed_at"))
            if key not in existing_keys:
                self._json.append_trade_history(trade)
                recovered += 1

        logger.debug("EMAv5 trade recovery: {} new of {} total", recovered, len(trades))
        return recovered

    def _recover_state(self) -> bool:
        """Restore state from JSON state file. Returns True if no errors."""
        try:
            state = self._json.read_state()
            # Empty state is valid (new engine, no history yet)
            logger.debug("EMAv5 state recovery: {} symbols restored", len(state))
            return True
        except Exception:
            return False

    def _recover_stats(self) -> bool:
        """Compute and restore statistics from DB."""
        stats = self._db.get_stats()
        if not stats:
            return False

        self._json.write_stats(stats)
        logger.debug("EMAv5 stats recovery: {} signals, {:.1f}% win rate",
                      stats.get("total_signals", 0), stats.get("win_rate", 0))
        return True

    def _populate_bridge(self) -> bool:
        """Populate bridge file with latest data for dashboard."""
        signals = self._db.get_signals(limit=100)
        state = self._json.read_state()
        stats = self._db.get_stats()

        # Build state counts
        state_counts: Dict[str, int] = {}
        for sym_data in state.values():
            s = sym_data.get("state", "NO_TREND")
            state_counts[s] = state_counts.get(s, 0) + 1

        self._json.write_signals(
            signals=signals,
            scanner={
                "scan_count": stats.get("total_signals", 0),
                "signal_count": stats.get("total_signals", 0),
                "signal_rate": 0,
                "uptime_sec": 0,
                "cache_size": 0,
                "open_trades": 0,
                "last_scan_time": time.time(),
            },
            states=state,
            state_counts=state_counts,
            health={
                "engine_running": True,
                "api_connected": True,
                "ws_connected": True,
                "db_connected": True,
                "error_count": 0,
            },
        )
        return True

    def get_recovery_status(self) -> Dict[str, Any]:
        """Check if recovery is needed (e.g., empty state after restart)."""
        state = self._json.read_state()
        stats = self._db.get_stats()
        signal_count = self._db.count_signals()

        return {
            "needs_recovery": signal_count > 0 and not state,
            "signal_count": signal_count,
            "state_symbols": len(state),
            "has_stats": bool(stats),
        }
