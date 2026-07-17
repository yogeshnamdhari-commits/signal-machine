"""
EMA_V5 State Manager — Symbol state machine with persistence.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, Optional

from loguru import logger

from .config import ema_v5_config

# States
NO_TREND = "NO_TREND"
BUY_MODE = "BUY_MODE"
SELL_MODE = "SELL_MODE"
WAITING_PULLBACK = "WAITING_PULLBACK"
WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
ACTIVE_BUY = "ACTIVE_BUY"
ACTIVE_SELL = "ACTIVE_SELL"
TRADE_CLOSED = "TRADE_CLOSED"

# Valid transitions
_TRANSITIONS = {
    NO_TREND: {BUY_MODE, SELL_MODE, WAITING_PULLBACK},
    BUY_MODE: {WAITING_PULLBACK, SELL_MODE, NO_TREND},
    SELL_MODE: {WAITING_PULLBACK, BUY_MODE, NO_TREND},
    WAITING_PULLBACK: {WAITING_CONFIRMATION, ACTIVE_BUY, ACTIVE_SELL, BUY_MODE, SELL_MODE, NO_TREND},
    WAITING_CONFIRMATION: {ACTIVE_BUY, ACTIVE_SELL, WAITING_PULLBACK, NO_TREND},
    ACTIVE_BUY: {TRADE_CLOSED, NO_TREND, SELL_MODE},
    ACTIVE_SELL: {TRADE_CLOSED, NO_TREND, BUY_MODE},
    TRADE_CLOSED: {NO_TREND, BUY_MODE, SELL_MODE},
}


class StateManager:
    """Manages per-symbol state machine."""

    def __init__(self) -> None:
        self._states: Dict[str, Dict] = {}  # symbol → {state, last_update, ...}
        self._state_file = Path(ema_v5_config.state.state_file)
        if ema_v5_config.state.persist_state:
            self._load()

    def get_state(self, symbol: str) -> str:
        """Get current state for a symbol."""
        return self._states.get(symbol, {}).get("state", NO_TREND)

    def set_state(self, symbol: str, new_state: str) -> bool:
        """Transition to new state. Returns True if valid."""
        current = self.get_state(symbol)
        if current == new_state:
            return True
        valid = _TRANSITIONS.get(current, set())
        if new_state not in valid:
            logger.debug("Invalid state transition: {} → {} for {}", current, new_state, symbol)
            return False
        self._states[symbol] = {
            "state": new_state,
            "last_update": time.time(),
            "previous": current,
        }
        if ema_v5_config.state.persist_state:
            self._save()
        logger.debug("State: {} {} → {}", symbol, current, new_state)
        return True

    def reset(self, symbol: str) -> None:
        """Reset symbol to NO_TREND."""
        self._states[symbol] = {
            "state": NO_TREND,
            "last_update": time.time(),
            "previous": self.get_state(symbol),
        }

    def get_all_states(self) -> Dict[str, Dict]:
        """Get all symbol states (read-only snapshot for bridge export)."""
        return dict(self._states)

    def get_state_counts(self) -> Dict[str, int]:
        """Count symbols in each state."""
        counts: Dict[str, int] = {}
        for sym, sym_data in self._states.items():
            if not isinstance(sym_data, dict):
                # Corrupted entry — remove it silently
                logger.debug("Removing corrupted state entry for {}: {}", sym, type(sym_data).__name__)
                del self._states[sym]
                continue
            state = sym_data.get("state", NO_TREND)
            counts[state] = counts.get(state, 0) + 1
        return counts

    def _load(self) -> None:
        """Load state from disk."""
        try:
            if self._state_file.exists():
                with open(self._state_file) as f:
                    raw = json.load(f)
                # Filter out corrupted entries (non-dict values like stray timestamps)
                self._states = {
                    k: v for k, v in raw.items()
                    if isinstance(v, dict) and "state" in v
                }
                pruned = len(raw) - len(self._states)
                if pruned:
                    logger.warning("EMA_V5 state: pruned {} corrupted entries", pruned)
                logger.debug("Loaded EMA_V5 state: {} symbols", len(self._states))
        except Exception as e:
            logger.warning("EMA_V5 state load failed: {}", e)

    def _save(self) -> None:
        """Save state to disk."""
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_file.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(self._states, f, indent=2, default=str)
            tmp.replace(self._state_file)
        except Exception as e:
            logger.debug("EMA_V5 state save failed: {}", e)
