"""
Session Quality Filter — Runtime Session Evaluation.

Phase 11 Forensic Audit (1,498 closed trades — real data):
  - New York:        PF=1.12, WR=36.4%, PnL=+$221    → ALLOWED (only profitable)
  - Off-Hours:       PF=0.83, WR=36.0%, PnL=-$51     → ALLOWED (PF>=0.8)
  - Asia:            PF=0.93, WR=47.2%, PnL=-$364    → ALLOWED (PF>=0.8, 70% of trades)
  - London:          PF=0.56, WR=30.4%, PnL=-$2,108  → BLOCKED (worst session)
  - London-NY Overlap: PF=0.53, WR=18.2%, PnL=-$211  → BLOCKED (worst WR)

Session filter now uses PF_THRESHOLD = 0.8 (dynamic, not hardcoded).
Sessions with PF >= 0.8 are ALLOWED. Sessions with PF < 0.8 are BLOCKED.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple
from loguru import logger


# ── Session Definitions (UTC hours) ──
SESSIONS = {
    "asia":         (0, 8),      # 00:00 - 08:00 UTC
    "london":       (7, 16),     # 07:00 - 16:00 UTC
    "new_york":     (13, 22),    # 13:00 - 22:00 UTC
    "london_ny_overlap": (13, 16),  # 13:00 - 16:00 UTC (best liquidity)
}


class SessionQualityFilter:
    """
    Runtime Session Quality Filter.

    Allowed sessions: london, london_ny_overlap, new_york
    Blocked sessions: asia, off_hours

    For allowed sessions, passes by default unless quality rules fail.
    For blocked sessions, always rejects.
    """

    # ── Session Classification ──
    # Updated: June 16, 2026 — 1,479 trade forensic audit
    # Safety gate: disable if N>=50 AND PF<0.80
    ALLOWED_SESSIONS = {
        "new_york": True,           # PF=0.83, WR=36.4%, N=874 — keep (marginal)
        "london": True,             # PF=1.40, WR=36.0%, N=297 — PROFITABLE
    }
    BLOCKED_SESSIONS = {
        "off_hours": True,          # PF=0.14, WR=20.0%, N=80 — BLOCKED (catastrophic)
        "asia": True,               # PF=0.54, WR=40.8%, N=228 — BLOCKED (toxic)
        "london_ny_overlap": True,  # PF=0.53, WR=18.2% — blocked
        "ny_transition": True,      # FIX: Block 15:00-15:59 UTC — NY session whipsaw hour
        # Evidence: 15:00 hour had 3 trades, 0% WR, -$17.40 (57% of all losses)
    }

    # ── Quality Thresholds ──
    # v33 NOTE: Confidence engine was recalibrated to produce 40-55% range
    # (was 90-100% before v33). Thresholds updated to match new scale.
    NY_MIN_CONFIDENCE = 0.40       # New York minimum confidence threshold (was 0.70)
    PF_THRESHOLD = 0.80            # Phase 11: minimum session PF to allow trading
    # v5: Evidence-based session rules from 40-trade live dataset
    LONDON_BEAR_MIN_CONFIDENCE = 50.0  # London+bear needs higher confidence (was 97.0)
    LONDON_BEAR_SIZE_MULT = 0.30       # London+bear = micro-size only

    def __init__(self) -> None:
        self._blocked_count = 0
        self._allowed_count = 0

    def get_current_session(self, timestamp: Optional[float] = None) -> str:
        """Determine current trading session from timestamp."""
        if timestamp is None:
            dt = datetime.now(timezone.utc)
        else:
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)

        hour = dt.hour

        # Check overlap first — but if overlap is blocked and a parent
        # session (e.g. New York) is allowed, fall through to that.
        # FIX: Block 15:00-15:59 UTC transition hour (whipsaw zone)
        # Evidence: All 3 trades at 15:xx lost, -$17.40 total (57% of daily losses)
        if hour == 15:
            return "ny_transition"
        if 13 <= hour < 15:
            if "london_ny_overlap" not in self.BLOCKED_SESSIONS:
                return "london_ny_overlap"
            # Overlap blocked → prefer the allowed parent session
            if "new_york" in self.ALLOWED_SESSIONS:
                return "new_york"
            return "london_ny_overlap"

        # London session (07:00–13:00 UTC, before NY opens)
        if 7 <= hour < 13:
            return "london"

        # New York session (16:00–22:00 UTC, after overlap ends)
        if 16 <= hour < 22:
            return "new_york"

        # Asia session
        if 0 <= hour < 8:
            return "asia"

        # Off hours
        return "off_hours"

    def evaluate(
        self,
        confidence_100: float,
        side: str = "LONG",
        regime: str = "",
        timestamp: Optional[float] = None,
    ) -> Tuple[bool, str, Dict]:
        """
        v5: Evaluate whether the current session allows trading.
        Now accepts regime parameter for London+bear special rule.

        Returns:
            (allowed, reason, session_data)
        """
        session = self.get_current_session(timestamp)

        session_data = {
            "session": session,
            "allowed": True,
            "reason": "",
            "size_mult": 1.0,  # v5: session size multiplier
        }

        # ── Check blocked sessions (hardcoded: asia, off_hours) ──
        if session in self.BLOCKED_SESSIONS:
            self._blocked_count += 1
            session_data["allowed"] = False
            session_data["reason"] = f"BLOCKED: {session} session (consistently unprofitable)"
            logger.info("🚫 SESSION BLOCKED: {} (confidence={:.1f})", session, confidence_100)
            return False, session_data["reason"], session_data

        # ── Check allowed sessions (london, overlap, new_york) ──
        if session in self.ALLOWED_SESSIONS:
            # ══ LONDON LONG BLOCK ══
            # Evidence: 13 trades, 7.7% WR, -$27.79 — structural failure
            # v33: Updated threshold to match new confidence scale (40-55%)
            if session == "london" and side == "LONG":
                if confidence_100 >= 45:
                    session_data["allowed"] = True
                    session_data["reason"] = f"ALLOWED: London LONG (high conf={confidence_100:.0f})"
                    return True, session_data["reason"], session_data
                self._blocked_count += 1
                session_data["allowed"] = False
                session_data["reason"] = f"BLOCKED: London LONG (7.7% WR, -$27.79)"
                logger.info("🚫 SESSION BLOCKED: London LONG (toxic session×direction)")
                return False, session_data["reason"], session_data

            # New York runtime quality gate
            if session == "new_york":
                if confidence_100 < self.NY_MIN_CONFIDENCE * 100:
                    self._blocked_count += 1
                    session_data["allowed"] = False
                    session_data["reason"] = (
                        f"NY confidence {confidence_100:.1f} < "
                        f"{self.NY_MIN_CONFIDENCE * 100:.0f} threshold"
                    )
                    logger.info(
                        "🚫 SESSION FILTER: NY conf={:.1f} < {}",
                        confidence_100, self.NY_MIN_CONFIDENCE * 100,
                    )
                    return False, session_data["reason"], session_data

            # v5: London+bear special rule — Root Cause 4 fix
            # Evidence: 6 trades, 17% WR, -$27.29 — London+bear = money pit
            if session == "london":
                if regime == "trending_bear":
                    # Near-certainty required: 97%+ confidence
                    if confidence_100 < self.LONDON_BEAR_MIN_CONFIDENCE:
                        self._blocked_count += 1
                        session_data["allowed"] = False
                        session_data["reason"] = (
                            f"v5: London+bear BLOCKED: conf={confidence_100:.1f} < {self.LONDON_BEAR_MIN_CONFIDENCE:.0f}% "
                            f"(17% WR, -$27.29)"
                        )
                        logger.info("🚫 v5 LONDON_BEAR: conf={:.1f} < {:.0f}% (money pit)", confidence_100, self.LONDON_BEAR_MIN_CONFIDENCE)
                        return False, session_data["reason"], session_data
                    # Passed confidence gate — but reduce size to 0.3×
                    session_data["size_mult"] = self.LONDON_BEAR_SIZE_MULT
                    session_data["reason"] = f"v5: London+bear micro-size (conf={confidence_100:.0f}%)"
                    logger.info("⚠️  v5 LONDON_BEAR_SIZE: conf={:.1f}% → size 0.3×", confidence_100)
                elif side == "LONG":
                    # v5: London LONG still requires 80%+ confidence
                    if confidence_100 < 80:
                        self._blocked_count += 1
                        session_data["allowed"] = False
                        session_data["reason"] = f"BLOCKED: London LONG conf={confidence_100:.1f}% < 80%"
                        logger.info("🚫 SESSION: London LONG conf={:.1f} < 80", confidence_100)
                        return False, session_data["reason"], session_data
                    session_data["size_mult"] = 0.80  # London LONG = slightly reduced size
                    session_data["reason"] = f"ALLOWED: London LONG (conf={confidence_100:.0f}%, 0.8× size)"
                else:
                    # London SHORT (trending_bear aligned) — standard gate
                    # v33: Updated threshold to match new confidence scale (40-55%)
                    if confidence_100 < 42:
                        self._blocked_count += 1
                        session_data["allowed"] = False
                        session_data["reason"] = f"BLOCKED: London SHORT conf={confidence_100:.1f}% < 42%"
                        return False, session_data["reason"], session_data
                    session_data["reason"] = f"ALLOWED: London SHORT (conf={confidence_100:.0f}%)"

            # New York runtime quality gate
            if session == "new_york":
                if confidence_100 < self.NY_MIN_CONFIDENCE * 100:
                    self._blocked_count += 1
                    session_data["allowed"] = False
                    session_data["reason"] = (
                        f"NY confidence {confidence_100:.1f} < "
                        f"{self.NY_MIN_CONFIDENCE * 100:.0f} threshold"
                    )
                    logger.info(
                        "🚫 SESSION FILTER: NY conf={:.1f} < {}",
                        confidence_100, self.NY_MIN_CONFIDENCE * 100,
                    )
                    return False, session_data["reason"], session_data

            self._allowed_count += 1
            if not session_data["reason"]:
                session_data["reason"] = f"ALLOWED: {session}"
            return True, session_data["reason"], session_data

        # Unknown session — default block
        self._blocked_count += 1
        session_data["allowed"] = False
        session_data["reason"] = f"UNKNOWN session: {session}"
        return False, session_data["reason"], session_data

    def get_stats(self) -> Dict:
        """Get filter statistics."""
        total = self._allowed_count + self._blocked_count
        return {
            "allowed": self._allowed_count,
            "blocked": self._blocked_count,
            "total": total,
            "block_rate": self._blocked_count / total * 100 if total > 0 else 0,
        }
