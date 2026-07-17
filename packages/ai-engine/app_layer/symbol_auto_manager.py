"""
Symbol Auto-Enable/Disable — Rolling PF-based symbol management.

Per Executive Assessment v4:
    "Instead of treating every symbol equally.
     I would extend this table with:
        Symbol PF Expectancy Last 50 Trades Status
        BTC 1.42 +0.35R 58 Enabled
        ETH 1.27 +0.22R 54 Enabled
        SOL 0.81 -0.14R 61 Reduced
        DOGE 0.63 -0.41R 57 Disabled

     Then execution simply skips weak symbols until they recover.
     No EMA logic changes."

Key Features:
    1. Rolling PF Calculation — per-symbol rolling 50-trade PF
    2. Auto-Disable — symbols with PF < 0.8 after 20+ trades
    3. Auto-Reduce — symbols with PF < 0.95 (partial capital)
    4. Auto-Enable — symbols that recover above threshold
    5. Cooldown Period — disabled symbols can re-enable after N trades
    6. Confidence Scoring — how reliable is the symbol's PF estimate

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

# ═══════════════════════════════════════════════════════════════
# SYMBOL MANAGEMENT CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Rolling window size
ROLLING_WINDOW = 50

# Auto-disable thresholds
DISABLE_PF_THRESHOLD = 0.80     # Disable if rolling PF < 0.80
DISABLE_MIN_TRADES = 20         # Minimum trades before disabling
DISABLE_EV_THRESHOLD = -0.20    # Disable if rolling EV < -0.20R

# Auto-reduce thresholds
REDUCE_PF_THRESHOLD = 0.95      # Reduce if rolling PF < 0.95
REDUCE_MIN_TRADES = 15          # Minimum trades before reducing

# Auto-enable thresholds
ENABLE_PF_THRESHOLD = 1.05      # Re-enable if rolling PF > 1.05
ENABLE_MIN_TRADES = 10          # Minimum trades in new window to re-enable

# Size adjustments by status
STATUS_ADJUSTMENTS = {
    "ENABLED": 1.0,
    "REDUCED": 0.5,
    "DISABLED": 0.0,
}

# Confidence thresholds
HIGH_CONFIDENCE_TRADES = 30     # 30+ trades = high confidence in PF estimate
MEDIUM_CONFIDENCE_TRADES = 15   # 15+ trades = medium confidence


@dataclass
class SymbolStatus:
    """Status and rolling metrics for a single symbol."""
    symbol: str = ""
    status: str = "ENABLED"      # ENABLED / REDUCED / DISABLED
    rolling_pf: float = 0.0
    rolling_ev_r: float = 0.0
    rolling_win_rate: float = 0.0
    rolling_avg_r: float = 0.0
    total_trades: int = 0
    rolling_trades: int = 0      # Trades in current rolling window
    confidence: str = "LOW"      # LOW / MEDIUM / HIGH
    size_adjustment: float = 1.0
    last_status_change: float = 0.0
    disable_reason: str = ""
    days_since_last_trade: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "status": self.status,
            "rolling_pf": round(self.rolling_pf, 2),
            "rolling_ev_r": round(self.rolling_ev_r, 3),
            "rolling_win_rate": round(self.rolling_win_rate, 3),
            "rolling_avg_r": round(self.rolling_avg_r, 3),
            "total_trades": self.total_trades,
            "rolling_trades": self.rolling_trades,
            "confidence": self.confidence,
            "size_adjustment": round(self.size_adjustment, 2),
            "disable_reason": self.disable_reason,
            "days_since_last_trade": round(self.days_since_last_trade, 1),
        }


@dataclass
class SymbolManagementResult:
    """Result from symbol management evaluation."""
    timestamp: float = 0.0
    total_symbols: int = 0
    enabled_count: int = 0
    reduced_count: int = 0
    disabled_count: int = 0
    symbols: List[SymbolStatus] = field(default_factory=list)
    newly_disabled: List[str] = field(default_factory=list)
    newly_reduced: List[str] = field(default_factory=list)
    newly_enabled: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "total_symbols": self.total_symbols,
            "enabled_count": self.enabled_count,
            "reduced_count": self.reduced_count,
            "disabled_count": self.disabled_count,
            "newly_disabled": self.newly_disabled,
            "newly_reduced": self.newly_reduced,
            "newly_enabled": self.newly_enabled,
            "symbols": [s.to_dict() for s in self.symbols],
        }


class SymbolAutoManager:
    """
    Automatically enables/disables symbols based on rolling PF.

    Per Executive Assessment v4:
        "Execution simply skips weak symbols until they recover."

    This engine:
        1. Calculates rolling 50-trade PF for each symbol
        2. Auto-disables symbols with PF < 0.80 after 20+ trades
        3. Auto-reduces symbols with PF < 0.95
        4. Auto-enables symbols that recover above threshold
        5. Tracks confidence in PF estimates

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._symbol_status: Dict[str, SymbolStatus] = {}
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        """Load symbol data from DB if stale."""
        if time.time() - self._last_load < 300:
            return
        self._load_symbol_data()

    def _load_symbol_data(self) -> None:
        """Load all closed trades and compute rolling PF per symbol."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol, realized_r, pnl, closed_at
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            # Group by symbol
            by_symbol: Dict[str, List[Dict]] = defaultdict(list)
            for row in rows:
                r = dict(row)
                by_symbol[r.get("symbol", "")].append(r)

            # Calculate rolling PF for each symbol
            self._symbol_status = {}
            for sym, trades in by_symbol.items():
                self._symbol_status[sym] = self._calc_symbol_status(sym, trades)

            self._last_load = time.time()
            logger.info(
                "📊 Symbol Auto-Manager loaded: {} symbols ({} enabled, {} reduced, {} disabled)",
                len(self._symbol_status),
                sum(1 for s in self._symbol_status.values() if s.status == "ENABLED"),
                sum(1 for s in self._symbol_status.values() if s.status == "REDUCED"),
                sum(1 for s in self._symbol_status.values() if s.status == "DISABLED"),
            )

        except Exception as e:
            logger.warning("Could not load symbol auto-manager: {}", e)

    def _calc_symbol_status(self, symbol: str, trades: List[Dict]) -> SymbolStatus:
        """Calculate rolling PF and status for a symbol."""
        status = SymbolStatus(
            symbol=symbol,
            total_trades=len(trades),
        )

        if not trades:
            return status

        # Rolling window
        rolling = trades[:ROLLING_WINDOW]
        status.rolling_trades = len(rolling)

        # Calculate rolling PF
        wins = [t.get("realized_r", 0) or 0 for t in rolling if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in rolling if (t.get("realized_r", 0) or 0) < 0]

        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        status.rolling_pf = gross_profit / max(0.01, gross_loss)

        # Rolling EV
        all_r = [t.get("realized_r", 0) or 0 for t in rolling]
        status.rolling_ev_r = sum(all_r) / max(1, len(all_r))
        status.rolling_avg_r = status.rolling_ev_r

        # Win rate
        status.rolling_win_rate = len(wins) / max(1, len(rolling))

        # Confidence
        if status.total_trades >= HIGH_CONFIDENCE_TRADES:
            status.confidence = "HIGH"
        elif status.total_trades >= MEDIUM_CONFIDENCE_TRADES:
            status.confidence = "MEDIUM"
        else:
            status.confidence = "LOW"

        # Days since last trade
        if trades:
            last_closed = trades[0].get("closed_at", 0) or 0
            if last_closed > 0:
                status.days_since_last_trade = (time.time() - last_closed) / 86400

        # Determine status
        status.status = self._determine_status(status)
        status.size_adjustment = STATUS_ADJUSTMENTS.get(status.status, 1.0)

        return status

    def _determine_status(self, status: SymbolStatus) -> str:
        """Determine symbol status based on rolling metrics."""
        # Need minimum trades for reliable assessment
        if status.rolling_trades < REDUCE_MIN_TRADES:
            return "ENABLED"  # Not enough data — default to enabled

        # Auto-disable
        if (status.rolling_trades >= DISABLE_MIN_TRADES
            and (status.rolling_pf < DISABLE_PF_THRESHOLD
                 or status.rolling_ev_r < DISABLE_EV_THRESHOLD)):
            return "DISABLED"

        # Auto-reduce
        if (status.rolling_trades >= REDUCE_MIN_TRADES
            and status.rolling_pf < REDUCE_PF_THRESHOLD):
            return "REDUCED"

        # Auto-enable (for previously disabled/reduced symbols)
        if (status.rolling_trades >= ENABLE_MIN_TRADES
            and status.rolling_pf >= ENABLE_PF_THRESHOLD):
            return "ENABLED"

        return "ENABLED"

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═══════════════════════════════════════════════════════════════

    def evaluate(self) -> SymbolManagementResult:
        """
        Evaluate all symbols and return management decisions.

        Returns:
            SymbolManagementResult with status for all symbols
        """
        self._ensure_loaded()

        result = SymbolManagementResult(timestamp=time.time())
        result.total_symbols = len(self._symbol_status)
        result.symbols = sorted(
            self._symbol_status.values(),
            key=lambda s: s.rolling_pf,
            reverse=True,
        )

        for s in result.symbols:
            if s.status == "ENABLED":
                result.enabled_count += 1
            elif s.status == "REDUCED":
                result.reduced_count += 1
            elif s.status == "DISABLED":
                result.disabled_count += 1

        return result

    def get_status(self, symbol: str) -> SymbolStatus:
        """Get status for a specific symbol."""
        self._ensure_loaded()
        return self._symbol_status.get(symbol, SymbolStatus(symbol=symbol))

    def is_enabled(self, symbol: str) -> bool:
        """Check if a symbol is enabled for trading."""
        status = self.get_status(symbol)
        return status.status != "DISABLED"

    def get_adjustment(self, symbol: str) -> float:
        """Get position size adjustment for a symbol."""
        status = self.get_status(symbol)
        return status.size_adjustment

    def get_disabled_symbols(self) -> List[str]:
        """Get list of disabled symbols."""
        self._ensure_loaded()
        return [s.symbol for s in self._symbol_status.values() if s.status == "DISABLED"]

    def get_reduced_symbols(self) -> List[str]:
        """Get list of reduced symbols."""
        self._ensure_loaded()
        return [s.symbol for s in self._symbol_status.values() if s.status == "REDUCED"]

    def get_top_symbols(self, n: int = 10) -> List[SymbolStatus]:
        """Get top N symbols by rolling PF."""
        self._ensure_loaded()
        enabled = [s for s in self._symbol_status.values() if s.status == "ENABLED"]
        return sorted(enabled, key=lambda s: s.rolling_pf, reverse=True)[:n]

    def get_worst_symbols(self, n: int = 10) -> List[SymbolStatus]:
        """Get worst N symbols by rolling PF."""
        self._ensure_loaded()
        return sorted(self._symbol_status.values(), key=lambda s: s.rolling_pf)[:n]

    def get_summary(self) -> Dict[str, Any]:
        """Get complete symbol management summary."""
        self._ensure_loaded()
        return {
            "total_symbols": len(self._symbol_status),
            "enabled": sum(1 for s in self._symbol_status.values() if s.status == "ENABLED"),
            "reduced": sum(1 for s in self._symbol_status.values() if s.status == "REDUCED"),
            "disabled": sum(1 for s in self._symbol_status.values() if s.status == "DISABLED"),
            "disabled_symbols": self.get_disabled_symbols(),
            "top_symbols": [s.to_dict() for s in self.get_top_symbols(5)],
            "worst_symbols": [s.to_dict() for s in self.get_worst_symbols(5)],
        }
