"""
Directional Exposure Limiter — prevents stacking same-direction positions.

Unlike the DirectionalNeutralizer (which balances signals per scan cycle),
this limiter tracks ACTUAL OPEN POSITIONS and blocks new entries when
too many positions in the same direction are opened within a rolling time window.

Root cause: On June 16, the engine opened 4 SHORTs (USELESS, HAEDAL, POWER,
ZEREBRO) within 2 hours. All hit SL. The signal-level directional cap was
disabled, and no position-level concentration check existed.

This fix adds a HARD BLOCK: if N positions of the same side were opened
in the last M minutes, reject any new position in that same direction.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple
from loguru import logger


class DirectionalExposureLimiter:
    """
    Tracks recently opened positions by direction and blocks new entries
    when same-direction concentration exceeds the configured limit.

    Usage:
        limiter = DirectionalExposureLimiter(max_same_direction=3, window_minutes=120)

        # Before opening a position:
        allowed, reason = limiter.check(symbol, side, risk_engine._positions, db_open_positions)
        if not allowed:
            logger.warning(f"BLOCKED: {reason}")
            return
    """

    def __init__(
        self,
        max_same_direction: int = 3,
        window_minutes: int = 120,
        max_total_positions: int = 15,
        max_same_direction_pct: float = 0.60,
        max_positions_per_window: int = 6,
    ) -> None:
        """
        Args:
            max_same_direction:     Hard cap on positions in the same direction
                                    within the rolling window. E.g., 3 means no more
                                    than 3 LONGs (or SHORTs) can be open in 2h.
            window_minutes:         Rolling window in minutes for counting.
            max_total_positions:    Maximum total positions (defense-in-depth).
            max_same_direction_pct: Soft cap — warn when same-direction positions
                                    exceed this fraction of total (0.60 = 60%).
            max_positions_per_window: Rate limit — max total entries in window
                                     regardless of direction (prevents over-trading).
        """
        self.max_same = max_same_direction
        self.window_sec = window_minutes * 60
        self.max_total = max_total_positions
        self.max_pct = max_same_direction_pct
        self.max_per_window = max_positions_per_window

        # Track position open times: {symbol: {side: opened_at}}
        self._position_log: List[Dict] = []

    def _clean_window(self, now: float) -> None:
        """Remove entries older than the rolling window."""
        cutoff = now - self.window_sec
        self._position_log = [
            p for p in self._position_log if p["opened_at"] >= cutoff
        ]

    def record_open(self, symbol: str, side: str, opened_at: float = None) -> None:
        """Record a position opening event."""
        self._position_log.append({
            "symbol": symbol,
            "side": side.upper(),
            "opened_at": opened_at or time.time(),
        })

    def check(
        self,
        symbol: str,
        side: str,
        risk_positions: Optional[Dict] = None,
        open_db_positions: Optional[List[Dict]] = None,
        now: float = None,
    ) -> Tuple[bool, str]:
        """
        Check if a new position in the given direction is allowed.

        Uses BOTH:
        1. The internal position_log (tracks openings during this engine run)
        2. Live open positions from risk engine or DB (covers restarts)

        Args:
            symbol:          Symbol to open
            side:            "LONG" or "SHORT"
            risk_positions:  dict from risk._positions (keyed by symbol)
            open_db_positions: list of dicts from db.get_open_positions()
            now:             Current timestamp (for testing)

        Returns:
            (allowed: bool, reason: str)
        """
        now = now or time.time()
        self._clean_window(now)

        side = side.upper()

        # ── Count same-direction positions from all sources ──
        same_count = 0
        total_count = 0
        same_symbols: List[str] = []
        seen_symbols = set()

        # Source 1: Internal log (rolling window)
        for p in self._position_log:
            if p["side"] == side:
                same_count += 1
                same_symbols.append(p["symbol"])
            total_count += 1

        # Source 2: Risk engine live positions
        if risk_positions:
            for sym, pos in risk_positions.items():
                if sym in seen_symbols:
                    continue
                seen_symbols.add(sym)
                pos_side = pos.get("side", "").upper()
                pos_opened = pos.get("opened_at", 0)
                # Only count if within window
                if now - pos_opened <= self.window_sec:
                    if pos_side == side:
                        same_count += 1
                        if sym not in same_symbols:
                            same_symbols.append(sym)
                    total_count += 1

        # Source 3: DB open positions (covers engine restarts)
        if open_db_positions:
            for pos in open_db_positions:
                sym = pos.get("symbol", "")
                if sym in seen_symbols:
                    continue
                seen_symbols.add(sym)
                pos_side = pos.get("side", "").upper()
                pos_opened = pos.get("opened_at", 0)
                if now - pos_opened <= self.window_sec:
                    if pos_side == side:
                        same_count += 1
                        if sym not in same_symbols:
                            same_symbols.append(sym)
                    total_count += 1

        # ── Check rate limit: total entries in window ──
        if total_count >= self.max_per_window:
            reason = (
                f"RATE_LIMIT: {total_count} positions opened in last "
                f"{self.window_sec // 60}min (limit={self.max_per_window}) "
                f"— too many entries too fast"
            )
            logger.warning("🚫 {}", reason)
            return False, reason

        # ── Check hard cap: same direction in window ──
        if same_count >= self.max_same:
            reason = (
                f"DIRECTIONAL_EXPOSURE: {same_count} {side} positions "
                f"already open in last {self.window_sec // 60}min "
                f"({', '.join(same_symbols[:5])}) — max={self.max_same}"
            )
            logger.warning("🚫 {}", reason)
            return False, reason

        # ── Check soft cap: same-direction percentage ──
        if total_count >= 3:  # Need at least 3 positions before pct check
            same_pct = same_count / total_count
            if same_pct > self.max_pct:
                reason = (
                    f"DIRECTIONAL_EXPOSURE: {same_count}/{total_count} "
                    f"({same_pct:.0%}) positions are {side} — exceeds "
                    f"{self.max_pct:.0%} concentration limit"
                )
                logger.warning("⚠️ {}", reason)
                return False, reason

        # ── Check max total positions (defense-in-depth) ──
        if total_count >= self.max_total:
            reason = (
                f"DIRECTIONAL_EXPOSURE: {total_count} total positions "
                f"— max={self.max_total}"
            )
            logger.warning("🚫 {}", reason)
            return False, reason

        logger.debug(
            "✅ DIRECTIONAL_EXPOSURE OK: {} {} — same_dir={}/{} total={}",
            side, symbol, same_count, self.max_same, total_count,
        )
        return True, "OK"

    def get_stats(self) -> Dict:
        """Return current exposure stats for monitoring/dashboard."""
        now = time.time()
        self._clean_window(now)

        longs = sum(1 for p in self._position_log if p["side"] == "LONG")
        shorts = sum(1 for p in self._position_log if p["side"] == "SHORT")
        total = longs + shorts

        return {
            "window_positions": total,
            "long_count": longs,
            "short_count": shorts,
            "long_pct": longs / total if total > 0 else 0.5,
            "short_pct": shorts / total if total > 0 else 0.5,
            "max_same_direction": self.max_same,
            "window_minutes": self.window_sec // 60,
        }
