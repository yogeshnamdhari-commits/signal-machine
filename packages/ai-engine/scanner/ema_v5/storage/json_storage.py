"""
EMA_V5 JSON Storage — Persistent JSON files for signals, state, history, stats.
Maintains 4 isolated files that auto-persist on every write.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class EMAv5JsonStorage:
    """JSON-based persistence for EMA_V5. 4 files, auto-persist, append-only history."""

    def __init__(self, data_dir: Optional[str] = None) -> None:
        _root = Path(data_dir) if data_dir else Path(__file__).resolve().parent.parent.parent.parent / "data"
        self._dir = _root / "bridge"
        self._dir.mkdir(parents=True, exist_ok=True)

        self._signals_file = self._dir / "ema_v5.json"
        self._state_file = _root / "ema_v5_state.json"
        self._history_file = _root / "ema_v5_history.json"
        self._stats_file = _root / "ema_v5_stats.json"

        # Initialize files if missing
        for fp, default in [
            (self._signals_file, {"ema_v5": {"scanner": {}, "states": {}, "state_counts": {}, "signals": [], "health": {}}}),
            (self._state_file, {}),
            (self._history_file, {"signals": [], "trades": []}),
            (self._stats_file, {}),
        ]:
            if not fp.exists():
                self._atomic_write(fp, default)

    # ── Atomic Write ──────────────────────────────────────────────

    def _atomic_write(self, filepath: Path, data: Any) -> None:
        """Write JSON atomically (tmp → replace)."""
        tmp = filepath.with_suffix(".tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(data, f, default=str, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp), str(filepath))
        except Exception as e:
            logger.error("EMAv5 JSON write error {}: {}", filepath.name, e)

    def _atomic_read(self, filepath: Path) -> Any:
        """Read JSON safely."""
        try:
            with open(filepath) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    # ── Signals (Bridge File) ────────────────────────────────────

    def write_signals(self, signals: List[Dict[str, Any]], scanner: Dict = None,
                      states: Dict = None, state_counts: Dict = None,
                      health: Dict = None) -> None:
        """Write EMA_V5 signals to the bridge file for dashboard display."""
        data = {
            "ema_v5": {
                "scanner": scanner or {},
                "states": states or {},
                "state_counts": state_counts or {},
                "signals": signals,
                "health": health or {},
            },
            "timestamp": time.time(),
        }
        self._atomic_write(self._signals_file, data)

    def read_signals(self) -> List[Dict[str, Any]]:
        """Read signals from bridge file."""
        data = self._atomic_read(self._signals_file)
        if not data:
            return []
        return data.get("ema_v5", {}).get("signals", [])

    def read_bridge(self) -> Dict[str, Any]:
        """Read full bridge data."""
        data = self._atomic_read(self._signals_file)
        if not data:
            return {}
        return data.get("ema_v5", {})

    # ── State ────────────────────────────────────────────────────

    def write_state(self, states: Dict[str, Any]) -> None:
        """Write per-symbol state machine data."""
        self._atomic_write(self._state_file, {
            "states": states,
            "timestamp": time.time(),
        })

    def read_state(self) -> Dict[str, Any]:
        """Read state machine data."""
        data = self._atomic_read(self._state_file)
        if not data:
            return {}
        return data.get("states", {})

    # ── History (Append-Only) ────────────────────────────────────

    def append_signal_history(self, signal: Dict[str, Any]) -> None:
        """Append a signal to history. Never overwrites."""
        data = self._atomic_read(self._history_file) or {"signals": [], "trades": []}
        data["signals"].append(signal)
        data["timestamp"] = time.time()
        # Keep last 10000 signals in JSON (full audit in DB)
        if len(data["signals"]) > 10000:
            data["signals"] = data["signals"][-10000:]
        self._atomic_write(self._history_file, data)

    def append_trade_history(self, trade: Dict[str, Any]) -> None:
        """Append a trade close record to history. Never overwrites."""
        data = self._atomic_read(self._history_file) or {"signals": [], "trades": []}
        data["trades"].append(trade)
        data["timestamp"] = time.time()
        # Keep last 5000 trades in JSON
        if len(data["trades"]) > 5000:
            data["trades"] = data["trades"][-5000:]
        self._atomic_write(self._history_file, data)

    def read_history(self) -> Dict[str, Any]:
        """Read full history."""
        data = self._atomic_read(self._history_file)
        if not data:
            return {"signals": [], "trades": []}
        return data

    # ── Stats ────────────────────────────────────────────────────

    def write_stats(self, stats: Dict[str, Any]) -> None:
        """Write computed statistics."""
        data = {
            "stats": stats,
            "timestamp": time.time(),
        }
        self._atomic_write(self._stats_file, data)

    def read_stats(self) -> Dict[str, Any]:
        """Read statistics."""
        data = self._atomic_read(self._stats_file)
        if not data:
            return {}
        return data.get("stats", {})

    # ── Bulk Operations ──────────────────────────────────────────

    def get_all_signals_for_export(self) -> List[Dict[str, Any]]:
        """Get all signals from history for export."""
        history = self.read_history()
        return history.get("signals", [])

    def get_all_trades_for_export(self) -> List[Dict[str, Any]]:
        """Get all trades from history for export."""
        history = self.read_history()
        return history.get("trades", [])
