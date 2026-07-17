"""
Session Filter — Phase 2 CRITICAL BUG C.

Market session gating based on UTC time. Blocks signals during dead/thin
market hours where bid-ask spreads widen, volume thins, and CVD signals
become unreliable.

Problem:
  - Trades opened at 03:03 UTC, 03:04 UTC, 03:07 UTC...
  - These are Asia dead-zone hours for most altcoins
  - Spreads widen, volume thins, false signals increase

Solution:
  - Hard block 00:00–07:00 UTC (Asia dead zone) — NO signals
  - Quality-gated sessions for remaining hours
  - Session size multipliers (overlap = 1.0, Asia close = 0.7)
  - Dynamic quality floor per session

Session map (UTC):
  00–07:  BLOCKED    — Asia dead zone (no signals)
  07–12:  London     — quality floor 75, size 0.9x
  12–16:  Overlap    — quality floor 70, size 1.0x (BEST window)
  16–20:  NY         — quality floor 75, size 0.9x
  20–24:  Asia close — quality floor 80, size 0.7x

Integration:
    from scanner.session_filter import session_filter
    ok, size_mult = session_filter.allows_signal(quality_score)
    if not ok:
        continue  # skip this signal
    final_qty = base_qty * size_mult
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, Tuple

from loguru import logger


# ── Session Definitions ──
SESSIONS = {
    "asia_open":    {"start": 0,  "end": 7,  "quality": "low"},
    "london":       {"start": 7,  "end": 12, "quality": "high"},
    "overlap":      {"start": 12, "end": 16, "quality": "highest"},
    "ny":           {"start": 16, "end": 20, "quality": "high"},
    "asia_close":   {"start": 20, "end": 24, "quality": "medium"},
}

# Quality rules per session tier
SESSION_SIGNAL_RULES = {
    "highest": {"max_signals": 10, "min_quality_score": 70, "size_multiplier": 1.0},
    "high":    {"max_signals": 6,  "min_quality_score": 75, "size_multiplier": 0.9},
    "medium":  {"max_signals": 3,  "min_quality_score": 80, "size_multiplier": 0.7},
    "low":     {"max_signals": 0,  "min_quality_score": 999, "size_multiplier": 0.0},
    # low quality = NO SIGNALS (max_signals=0 → hard block)
}


class SessionFilter:
    """
    Phase 2 Market Session Filter.

    Hard blocks signals during 00:00–07:00 UTC (Asia dead zone).
    Applies quality floors and size multipliers for other sessions.
    """

    def __init__(self):
        self._blocked_count = 0
        self._allowed_count = 0
        self._session_stats: Dict[str, Dict] = {}

    def get_current_session(self, timestamp: float = None) -> Tuple[str, Dict]:
        """
        Determine current trading session from timestamp.

        Returns (session_name, session_rules).
        """
        if timestamp is None:
            dt = datetime.now(timezone.utc)
        else:
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)

        hour = dt.hour

        if 12 <= hour < 16:
            return "overlap", SESSION_SIGNAL_RULES["highest"]
        elif 7 <= hour < 12:
            return "london", SESSION_SIGNAL_RULES["high"]
        elif 16 <= hour < 20:
            return "ny", SESSION_SIGNAL_RULES["high"]
        elif 20 <= hour < 24:
            return "asia_close", SESSION_SIGNAL_RULES["medium"]
        else:  # 0–6 UTC
            return "asia_open", SESSION_SIGNAL_RULES["low"]

    def allows_signal(
        self, quality_score: float, timestamp: float = None
    ) -> Tuple[bool, float, str]:
        """
        Check if the current session allows a signal of given quality.

        Returns:
            (allowed, size_multiplier, reason)
        """
        session_name, rules = self.get_current_session(timestamp)
        hour = datetime.now(timezone.utc).hour if timestamp is None else \
               datetime.fromtimestamp(timestamp, tz=timezone.utc).hour

        # ── Hard block: Asia dead zone (00:00–07:00 UTC) ──
        if rules["max_signals"] == 0:
            self._blocked_count += 1
            reason = (
                f"SESSION_BLOCK: {session_name} (UTC {hour:02d}:xx) — "
                f"no signals allowed (dead zone)"
            )
            logger.debug("🚫 {}", reason)
            return False, 0.0, reason

        # ── Quality floor check ──
        if quality_score < rules["min_quality_score"]:
            self._blocked_count += 1
            reason = (
                f"SESSION_QUALITY: {session_name} requires "
                f"{rules['min_quality_score']:.0f}, got {quality_score:.0f}"
            )
            logger.debug("🚫 {}", reason)
            return False, 0.0, reason

        # ── Allowed ──
        self._allowed_count += 1
        self._track_session(session_name)
        return True, rules["size_multiplier"], f"SESSION_OK: {session_name}"

    def _track_session(self, session_name: str) -> None:
        """Track signal count per session for daily budget."""
        if session_name not in self._session_stats:
            self._session_stats[session_name] = {"signals": 0}
        self._session_stats[session_name]["signals"] += 1

    def get_stats(self) -> Dict:
        """Return session filter statistics."""
        total = self._allowed_count + self._blocked_count
        return {
            "allowed": self._allowed_count,
            "blocked": self._blocked_count,
            "total": total,
            "block_rate": self._blocked_count / total * 100 if total > 0 else 0,
            "session_breakdown": self._session_stats.copy(),
        }

    def reset_daily(self) -> None:
        """Reset daily counters (call at UTC midnight)."""
        self._blocked_count = 0
        self._allowed_count = 0
        self._session_stats.clear()


# ── Daily Signal Budget ──
class DailySignalBudget:
    """
    Phase 2 Signal Overgeneration Prevention.

    Caps total signals per day and per hour.
    Raises quality floor as daily count increases.
    """

    DAILY_LIMIT = 40        # Hard cap: max signals per UTC day
    HOURLY_LIMIT = 8        # Burst prevention: max signals per hour

    # Quality floor escalation as budget fills
    QUALITY_FLOORS = [
        (20, 75),    # 0–19 signals: normal floor (75)
        (30, 80),    # 20–29 signals: more selective (80)
        (40, 85),    # 30–39 signals: very selective (85)
        (999, 999),  # 40+ signals: budget exhausted (no more signals)
    ]

    def __init__(self):
        self._daily_count = 0
        self._hourly_count = 0
        self._current_hour = -1
        self._daily_date = ""

    def can_emit(self, quality_score: float) -> Tuple[bool, str, float]:
        """
        Check if a new signal can be emitted within budget.

        Returns:
            (allowed, reason, effective_quality_floor)
        """
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        current_hour = datetime.now(timezone.utc).hour

        # Reset daily counter at UTC day boundary
        if today != self._daily_date:
            self._daily_count = 0
            self._daily_date = today

        # Reset hourly counter at hour boundary
        if current_hour != self._current_hour:
            self._hourly_count = 0
            self._current_hour = current_hour

        # ── Check daily cap ──
        if self._daily_count >= self.DAILY_LIMIT:
            return False, f"BUDGET_EXHAUSTED: {self._daily_count}/{self.DAILY_LIMIT} daily signals", 999

        # ── Check hourly cap ──
        if self._hourly_count >= self.HOURLY_LIMIT:
            return False, f"HOURLY_BURST: {self._hourly_count}/{self.HOURLY_LIMIT} signals this hour", 999

        # ── Dynamic quality floor ──
        floor = 75  # default
        for threshold, f in self.QUALITY_FLOORS:
            if self._daily_count < threshold:
                floor = f
                break

        if quality_score < floor:
            return False, f"QUALITY_FLOOR: {quality_score:.0f} < {floor} (daily count: {self._daily_count})", floor

        return True, f"BUDGET_OK: {self._daily_count + 1}/{self.DAILY_LIMIT}", floor

    def record_signal(self) -> None:
        """Record that a signal was emitted."""
        self._daily_count += 1
        self._hourly_count += 1

    def reset_daily(self) -> None:
        """Reset daily counters (called at UTC midnight)."""
        self._daily_count = 0
        self._hourly_count = 0
        self._current_hour = -1

    @property
    def daily_count(self) -> int:
        return self._daily_count

    @property
    def remaining(self) -> int:
        return max(0, self.DAILY_LIMIT - self._daily_count)

    def get_stats(self) -> Dict:
        return {
            "daily_count": self._daily_count,
            "daily_limit": self.DAILY_LIMIT,
            "hourly_count": self._hourly_count,
            "hourly_limit": self.HOURLY_LIMIT,
            "remaining": self.remaining,
        }


# ── Global singletons ──
session_filter = SessionFilter()
daily_budget = DailySignalBudget()
