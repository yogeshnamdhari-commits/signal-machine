"""
Trade Governance — Institutional-grade risk controls for the execution layer.

Six governance features that sit in the App Layer ONLY. No changes to
EMA V5, Smart Money, RR Audit, or Research Platform.

Features:
    1. Dynamic Kill Switch — Auto-pause when recent performance collapses
    2. Symbol Blacklist — Block symbols with poor historical PF
    3. Session Blacklist — Block sessions with poor historical PF
    4. Confidence Calibration — Reject confidence ranges that historically lose money
    5. Daily Loss Stop — Hard daily drawdown limit
    6. Time-Based Exit — Exit stale trades that haven't moved
    7. Max Simultaneous Exposure — Limit correlated directional bets

All features READ from the trade history database. They never modify
upstream signal data or EMA V5 logic.

Database: institutional_v1.db → positions table (closed trades)
"""
from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

# ═══════════════════════════════════════════════════════════════
# 1. DYNAMIC KILL SWITCH
# ═══════════════════════════════════════════════════════════════

KILL_SWITCH_LOOKBACK = 10          # Check last N trades
KILL_SWITCH_WIN_RATE_MIN = 0.25    # Win rate below this → kill
KILL_SWITCH_PF_MIN = 0.80          # PF below this → kill
# Both conditions must be true simultaneously to trigger

# ═══════════════════════════════════════════════════════════════
# 2. SYMBOL BLACKLIST
# ═══════════════════════════════════════════════════════════════

SYMBOL_BLACKLIST_LOOKBACK = 20     # Last N trades per symbol
SYMBOL_BLACKLIST_PF_MIN = 0.80     # Block if PF < this
SYMBOL_MIN_TRADES = 5              # Minimum trades before blacklisting

# ═══════════════════════════════════════════════════════════════
# 3. SESSION BLACKLIST
# ═══════════════════════════════════════════════════════════════

SESSION_BLACKLIST_LOOKBACK = 20    # Last N trades per session
SESSION_BLACKLIST_PF_MIN = 0.80    # Block if PF < this
SESSION_MIN_TRADES = 5             # Minimum trades before blacklisting

# ═══════════════════════════════════════════════════════════════
# 4. CONFIDENCE CALIBRATION
# ═══════════════════════════════════════════════════════════════

CONFIDENCE_BUCKET_SIZE = 5         # Bucket width (e.g., 85-89, 90-94, 95-100)
CONFIDENCE_MIN_TRADES = 5          # Minimum trades per bucket
CONFIDENCE_BUCKET_PF_MIN = 0.80    # Reject if bucket PF < this

# ═══════════════════════════════════════════════════════════════
# 5. DAILY LOSS STOP
# ═══════════════════════════════════════════════════════════════

DAILY_LOSS_STOP_PCT = 2.0          # Stop trading at -2% daily loss

# ═══════════════════════════════════════════════════════════════
# 6. TIME-BASED EXIT
# ═══════════════════════════════════════════════════════════════

STALE_TRADE_MAX_HOURS = 8          # Check after this many hours
STALE_TRADE_MIN_R = 0.3            # If profit < this R after max hours → exit
STALE_TRADE_WARN_HOURS = 6         # Start monitoring at this point

# ═══════════════════════════════════════════════════════════════
# 7. MAX SIMULTANEOUS EXPOSURE
# ═══════════════════════════════════════════════════════════════

MAX_LONG_POSITIONS = 1
MAX_SHORT_POSITIONS = 1
# Correlated symbol groups — treated as single exposure
CORRELATED_GROUPS = {
    "L1_ALTS": {"ETHUSDT", "SOLUSDT", "AVAXUSDT", "ADAUSDT", "DOTUSDT",
                "NEARUSDT", "APTUSDT", "SUIUSDT", "SEIUSDT", "TONUSDT"},
    "MEME": {"DOGEUSDT", "SHIBUSDT", "1000PEPEUSDT", "WIFUSDT", "FLOKIUSDT",
             "BONKUSDT", "1000BONKUSDT"},
    "L2": {"ARBUSDT", "OPUSDT", "MATICUSDT", "STRKUSDT"},
    "AI": {"FETUSDT", "RENDERUSDT", "TAOUSDT", "NEARUSDT", "GRTUSDT"},
}


# ═══════════════════════════════════════════════════════════════
# DATA STRUCTURES
# ═══════════════════════════════════════════════════════════════

@dataclass
class KillSwitchState:
    """Dynamic kill switch status."""
    active: bool = False
    reason: str = ""
    recent_win_rate: float = 0.0
    recent_pf: float = 0.0
    recent_trades: int = 0
    triggered_at: float = 0.0
    cooldown_until: float = 0.0  # Auto-resume after cooldown

    def to_dict(self) -> Dict:
        return {
            "active": self.active,
            "reason": self.reason,
            "recent_win_rate": round(self.recent_win_rate, 3),
            "recent_pf": round(self.recent_pf, 2),
            "recent_trades": self.recent_trades,
            "triggered_at": self.triggered_at,
            "cooldown_until": self.cooldown_until,
        }


@dataclass
class SymbolBlacklistEntry:
    """Blacklist status for a single symbol."""
    symbol: str = ""
    status: str = "ACTIVE"      # ACTIVE / BLACKLISTED
    rolling_pf: float = 0.0
    rolling_win_rate: float = 0.0
    trades_in_window: int = 0
    total_trades: int = 0
    blacklisted_at: float = 0.0
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "status": self.status,
            "rolling_pf": round(self.rolling_pf, 2),
            "rolling_win_rate": round(self.rolling_win_rate, 3),
            "trades_in_window": self.trades_in_window,
            "total_trades": self.total_trades,
            "reason": self.reason,
        }


@dataclass
class SessionBlacklistEntry:
    """Blacklist status for a single session."""
    session: str = ""
    status: str = "ACTIVE"
    rolling_pf: float = 0.0
    rolling_win_rate: float = 0.0
    trades_in_window: int = 0
    blacklisted_at: float = 0.0
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "session": self.session,
            "status": self.status,
            "rolling_pf": round(self.rolling_pf, 2),
            "rolling_win_rate": round(self.rolling_win_rate, 3),
            "trades_in_window": self.trades_in_window,
            "reason": self.reason,
        }


@dataclass
class ConfidenceBucket:
    """Historical performance for a confidence range."""
    bucket_min: int = 0
    bucket_max: int = 0
    label: str = ""
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_r: float = 0.0
    total_pnl: float = 0.0
    status: str = "ACCEPT"      # ACCEPT / REJECT
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "label": self.label,
            "trades": self.trades,
            "win_rate": round(self.win_rate, 3),
            "profit_factor": round(self.profit_factor, 2),
            "avg_r": round(self.avg_r, 3),
            "total_pnl": round(self.total_pnl, 2),
            "status": self.status,
            "reason": self.reason,
        }


@dataclass
class DailyLossState:
    """Daily loss tracking."""
    date: str = ""
    daily_pnl: float = 0.0
    daily_pnl_pct: float = 0.0
    starting_balance: float = 0.0
    current_balance: float = 0.0
    blocked: bool = False
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "date": self.date,
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_pnl_pct": round(self.daily_pnl_pct, 2),
            "starting_balance": round(self.starting_balance, 2),
            "blocked": self.blocked,
            "reason": self.reason,
        }


@dataclass
class StaleTradeExit:
    """Time-based exit decision for a stale trade."""
    symbol: str = ""
    side: str = ""
    hold_hours: float = 0.0
    current_r: float = 0.0
    action: str = "HOLD"        # HOLD / WARN / EXIT
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "hold_hours": round(self.hold_hours, 1),
            "current_r": round(self.current_r, 2),
            "action": self.action,
            "reason": self.reason,
        }


@dataclass
class ExposureCheck:
    """Max simultaneous exposure check result."""
    approved: bool = True
    current_longs: int = 0
    current_shorts: int = 0
    max_longs: int = MAX_LONG_POSITIONS
    max_shorts: int = MAX_SHORT_POSITIONS
    blocked_group: str = ""
    reason: str = ""

    def to_dict(self) -> Dict:
        return {
            "approved": self.approved,
            "current_longs": self.current_longs,
            "current_shorts": self.current_shorts,
            "max_longs": self.max_longs,
            "max_shorts": self.max_shorts,
            "blocked_group": self.blocked_group,
            "reason": self.reason,
        }


@dataclass
class GovernanceDecision:
    """Combined governance decision for a signal."""
    approved: bool = True
    kill_switch: Optional[KillSwitchState] = None
    symbol_blacklist: Optional[SymbolBlacklistEntry] = None
    session_blacklist: Optional[SessionBlacklistEntry] = None
    confidence_bucket: Optional[ConfidenceBucket] = None
    daily_loss: Optional[DailyLossState] = None
    exposure: Optional[ExposureCheck] = None
    rejection_reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        result = {
            "approved": self.approved,
            "rejection_reasons": self.rejection_reasons,
        }
        if self.kill_switch:
            result["kill_switch"] = self.kill_switch.to_dict()
        if self.symbol_blacklist:
            result["symbol_blacklist"] = self.symbol_blacklist.to_dict()
        if self.session_blacklist:
            result["session_blacklist"] = self.session_blacklist.to_dict()
        if self.confidence_bucket:
            result["confidence_bucket"] = self.confidence_bucket.to_dict()
        if self.daily_loss:
            result["daily_loss"] = self.daily_loss.to_dict()
        if self.exposure:
            result["exposure"] = self.exposure.to_dict()
        return result


# ═══════════════════════════════════════════════════════════════
# TRADE GOVERNANCE ENGINE
# ═══════════════════════════════════════════════════════════════

class TradeGovernanceEngine:
    """
    Institutional-grade trade governance — six risk controls that
    prevent the App from opening trades that statistically should
    never be opened.

    Sits in the App Layer ONLY. No changes to EMA V5, Smart Money,
    RR Audit, or Research Platform.

    READ-ONLY: Never modifies upstream data. Only reads trade history
    and returns governance decisions.
    """

    def __init__(self, db_path: Optional[Path] = None, balance: float = 10_000.0):
        self._db_path = db_path or _DB_PATH
        self._balance = balance

        # Kill switch state
        self._kill_switch = KillSwitchState()
        self._kill_switch_cooldown_sec = 3600  # 1 hour cooldown

        # Symbol blacklist cache
        self._symbol_blacklist: Dict[str, SymbolBlacklistEntry] = {}

        # Session blacklist cache
        self._session_blacklist: Dict[str, SessionBlacklistEntry] = {}

        # Confidence calibration cache
        self._confidence_buckets: Dict[str, ConfidenceBucket] = {}

        # Daily loss state
        self._daily_loss = DailyLossState()
        self._daily_starting_balance: float = 0.0

        # Cache timestamps
        self._last_kill_check: float = 0.0
        self._last_symbol_check: float = 0.0
        self._last_session_check: float = 0.0
        self._last_confidence_check: float = 0.0
        self._last_daily_check: float = 0.0

        # Cache TTL (seconds)
        self._cache_ttl = 60  # Re-check every 60 seconds

    # ─────────────────────────────────────────────────────────
    # PUBLIC API
    # ─────────────────────────────────────────────────────────

    def evaluate_signal(
        self,
        signal: Dict[str, Any],
        open_positions: Optional[List[Dict]] = None,
        balance: float = 0.0,
    ) -> GovernanceDecision:
        """
        Evaluate a signal against all governance rules.

        Args:
            signal: Signal dict with symbol, side, confidence, session, etc.
            open_positions: Current open positions
            balance: Account balance

        Returns:
            GovernanceDecision with approval/rejection
        """
        if balance > 0:
            self._balance = balance

        positions = open_positions or []
        decision = GovernanceDecision(approved=True)

        # ── 1. Kill Switch ──
        decision.kill_switch = self._check_kill_switch()
        if decision.kill_switch.active:
            decision.approved = False
            decision.rejection_reasons.append(
                f"kill_switch_active: {decision.kill_switch.reason}"
            )
            return decision

        # ── 2. Symbol Blacklist ──
        symbol = signal.get("symbol", "")
        decision.symbol_blacklist = self._check_symbol_blacklist(symbol)
        if decision.symbol_blacklist.status == "BLACKLISTED":
            decision.approved = False
            decision.rejection_reasons.append(
                f"symbol_blacklisted: {symbol} — {decision.symbol_blacklist.reason}"
            )
            return decision

        # ── 3. Session Blacklist ──
        session = signal.get("session", signal.get("at_open_session", "unknown"))
        decision.session_blacklist = self._check_session_blacklist(session)
        if decision.session_blacklist.status == "BLACKLISTED":
            decision.approved = False
            decision.rejection_reasons.append(
                f"session_blacklisted: {session} — {decision.session_blacklist.reason}"
            )
            return decision

        # ── 4. Confidence Calibration ──
        confidence = signal.get("confidence", 0)
        decision.confidence_bucket = self._check_confidence_calibration(confidence)
        if decision.confidence_bucket.status == "REJECT":
            decision.approved = False
            decision.rejection_reasons.append(
                f"confidence_rejected: {confidence} in bucket "
                f"{decision.confidence_bucket.label} — {decision.confidence_bucket.reason}"
            )
            return decision

        # ── 5. Daily Loss Stop ──
        decision.daily_loss = self._check_daily_loss()
        if decision.daily_loss.blocked:
            decision.approved = False
            decision.rejection_reasons.append(
                f"daily_loss_stop: {decision.daily_loss.reason}"
            )
            return decision

        # ── 6. Max Simultaneous Exposure ──
        decision.exposure = self._check_exposure(signal, positions)
        if not decision.exposure.approved:
            decision.approved = False
            decision.rejection_reasons.append(
                f"exposure_blocked: {decision.exposure.reason}"
            )
            return decision

        return decision

    def check_stale_trades(
        self,
        open_positions: List[Dict],
    ) -> List[StaleTradeExit]:
        """
        Check all open positions for time-based exit.

        Args:
            open_positions: Current open positions

        Returns:
            List of StaleTradeExit decisions (EXIT for stale trades)
        """
        exits = []
        now = time.time()

        for pos in open_positions:
            symbol = pos.get("symbol", "")
            side = pos.get("side", "")
            opened_at = pos.get("opened_at", 0)
            entry_price = pos.get("entry_price", 0)
            current_price = pos.get("current_price", pos.get("price", 0))
            stop_loss = pos.get("stop_loss", 0)

            if not opened_at or not entry_price:
                continue

            hold_hours = (now - opened_at) / 3600

            # Calculate current R
            current_r = 0.0
            risk = abs(entry_price - stop_loss) if stop_loss else 0
            if risk > 0 and current_price > 0:
                if side == "LONG":
                    current_r = (current_price - entry_price) / risk
                else:
                    current_r = (entry_price - current_price) / risk

            # Check stale trade
            if hold_hours >= STALE_TRADE_MAX_HOURS and current_r < STALE_TRADE_MIN_R:
                exits.append(StaleTradeExit(
                    symbol=symbol,
                    side=side,
                    hold_hours=hold_hours,
                    current_r=current_r,
                    action="EXIT",
                    reason=(
                        f"stale_trade: {hold_hours:.1f}h hold, "
                        f"only {current_r:.2f}R (need {STALE_TRADE_MIN_R}R)"
                    ),
                ))
            elif hold_hours >= STALE_TRADE_WARN_HOURS and current_r < STALE_TRADE_MIN_R:
                exits.append(StaleTradeExit(
                    symbol=symbol,
                    side=side,
                    hold_hours=hold_hours,
                    current_r=current_r,
                    action="WARN",
                    reason=(
                        f"stale_warning: {hold_hours:.1f}h hold, "
                        f"only {current_r:.2f}R — approaching exit threshold"
                    ),
                ))
            else:
                exits.append(StaleTradeExit(
                    symbol=symbol,
                    side=side,
                    hold_hours=hold_hours,
                    current_r=current_r,
                    action="HOLD",
                    reason="ok",
                ))

        return exits

    def get_kill_switch_status(self) -> KillSwitchState:
        """Get current kill switch status."""
        self._check_kill_switch()
        return self._kill_switch

    def get_symbol_blacklist(self) -> Dict[str, SymbolBlacklistEntry]:
        """Get all symbol blacklist entries."""
        self._refresh_symbol_blacklist()
        return dict(self._symbol_blacklist)

    def get_session_blacklist(self) -> Dict[str, SessionBlacklistEntry]:
        """Get all session blacklist entries."""
        self._refresh_session_blacklist()
        return dict(self._session_blacklist)

    def get_confidence_buckets(self) -> Dict[str, ConfidenceBucket]:
        """Get all confidence calibration buckets."""
        self._refresh_confidence_calibration()
        return dict(self._confidence_buckets)

    def get_daily_loss_status(self) -> DailyLossState:
        """Get current daily loss status."""
        self._check_daily_loss()
        return self._daily_loss

    def get_full_status(self) -> Dict:
        """Get complete governance status for dashboard display."""
        return {
            "kill_switch": self.get_kill_switch_status().to_dict(),
            "symbol_blacklist": {
                k: v.to_dict() for k, v in self.get_symbol_blacklist().items()
            },
            "session_blacklist": {
                k: v.to_dict() for k, v in self.get_session_blacklist().items()
            },
            "confidence_buckets": {
                k: v.to_dict() for k, v in self.get_confidence_buckets().items()
            },
            "daily_loss": self.get_daily_loss_status().to_dict(),
            "config": {
                "kill_switch_lookback": KILL_SWITCH_LOOKBACK,
                "kill_switch_win_rate_min": KILL_SWITCH_WIN_RATE_MIN,
                "kill_switch_pf_min": KILL_SWITCH_PF_MIN,
                "symbol_blacklist_lookback": SYMBOL_BLACKLIST_LOOKBACK,
                "symbol_blacklist_pf_min": SYMBOL_BLACKLIST_PF_MIN,
                "session_blacklist_lookback": SESSION_BLACKLIST_LOOKBACK,
                "session_blacklist_pf_min": SESSION_BLACKLIST_PF_MIN,
                "confidence_bucket_pf_min": CONFIDENCE_BUCKET_PF_MIN,
                "daily_loss_stop_pct": DAILY_LOSS_STOP_PCT,
                "stale_trade_max_hours": STALE_TRADE_MAX_HOURS,
                "stale_trade_min_r": STALE_TRADE_MIN_R,
                "max_longs": MAX_LONG_POSITIONS,
                "max_shorts": MAX_SHORT_POSITIONS,
            },
        }

    # ─────────────────────────────────────────────────────────
    # 1. DYNAMIC KILL SWITCH
    # ─────────────────────────────────────────────────────────

    def _check_kill_switch(self) -> KillSwitchState:
        """Check if the kill switch should be active."""
        now = time.time()

        # Respect cooldown
        if self._kill_switch.active and now < self._kill_switch.cooldown_until:
            return self._kill_switch

        # Auto-resume after cooldown
        if self._kill_switch.active and now >= self._kill_switch.cooldown_until:
            self._kill_switch.active = False
            self._kill_switch.reason = ""
            logger.info("🟢 KILL SWITCH AUTO-RESUMED after cooldown")

        # Cache check
        if now - self._last_kill_check < self._cache_ttl:
            return self._kill_switch
        self._last_kill_check = now

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            # Get last N closed trades
            cur.execute("""
                SELECT pnl, realized_r
                FROM positions
                WHERE status = 'closed' AND pnl IS NOT NULL
                ORDER BY closed_at DESC
                LIMIT ?
            """, (KILL_SWITCH_LOOKBACK,))
            rows = cur.fetchall()
            conn.close()

            if len(rows) < KILL_SWITCH_LOOKBACK:
                # Not enough data to evaluate
                self._kill_switch.recent_trades = len(rows)
                return self._kill_switch

            pnls = [r[0] for r in rows]
            wins = sum(1 for p in pnls if p > 0)
            losses = sum(1 for p in pnls if p <= 0)
            win_rate = wins / len(pnls) if pnls else 0

            gross_profit = sum(p for p in pnls if p > 0)
            gross_loss = abs(sum(p for p in pnls if p < 0))
            pf = gross_profit / gross_loss if gross_loss > 0 else (
                float('inf') if gross_profit > 0 else 0
            )

            self._kill_switch.recent_win_rate = win_rate
            self._kill_switch.recent_pf = pf
            self._kill_switch.recent_trades = len(pnls)

            # Trigger if BOTH conditions met
            if win_rate < KILL_SWITCH_WIN_RATE_MIN and pf < KILL_SWITCH_PF_MIN:
                self._kill_switch.active = True
                self._kill_switch.reason = (
                    f"Last {len(pnls)} trades: "
                    f"WR={win_rate:.1%} < {KILL_SWITCH_WIN_RATE_MIN:.0%} "
                    f"AND PF={pf:.2f} < {KILL_SWITCH_PF_MIN:.2f}"
                )
                self._kill_switch.triggered_at = now
                self._kill_switch.cooldown_until = now + self._kill_switch_cooldown_sec
                logger.warning(
                    "🔴 KILL SWITCH ACTIVATED: Last {} trades — WR={:.1%} PF={:.2f} "
                    "(thresholds: WR<{:.0%} AND PF<{:.2f})",
                    len(pnls), win_rate, pf,
                    KILL_SWITCH_WIN_RATE_MIN, KILL_SWITCH_PF_MIN,
                )

            return self._kill_switch

        except Exception as e:
            logger.warning("Kill switch check error: {}", e)
            return self._kill_switch

    # ─────────────────────────────────────────────────────────
    # 2. SYMBOL BLACKLIST
    # ─────────────────────────────────────────────────────────

    def _check_symbol_blacklist(self, symbol: str) -> SymbolBlacklistEntry:
        """Check if a symbol is blacklisted."""
        self._refresh_symbol_blacklist()

        entry = self._symbol_blacklist.get(symbol)
        if entry:
            return entry

        # Unknown symbol — not enough data, allow
        return SymbolBlacklistEntry(
            symbol=symbol,
            status="ACTIVE",
            reason="insufficient_data",
        )

    def _refresh_symbol_blacklist(self) -> None:
        """Refresh symbol blacklist from trade history."""
        now = time.time()
        if now - self._last_symbol_check < self._cache_ttl:
            return
        self._last_symbol_check = now

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            # Get all symbols with closed trades
            cur.execute("""
                SELECT DISTINCT symbol
                FROM positions
                WHERE status = 'closed' AND pnl IS NOT NULL
            """)
            symbols = [r[0] for r in cur.fetchall()]

            for symbol in symbols:
                # Get last N trades for this symbol
                cur.execute("""
                    SELECT pnl
                    FROM positions
                    WHERE symbol = ? AND status = 'closed' AND pnl IS NOT NULL
                    ORDER BY closed_at DESC
                    LIMIT ?
                """, (symbol, SYMBOL_BLACKLIST_LOOKBACK))
                rows = cur.fetchall()

                if len(rows) < SYMBOL_MIN_TRADES:
                    self._symbol_blacklist[symbol] = SymbolBlacklistEntry(
                        symbol=symbol,
                        status="ACTIVE",
                        trades_in_window=len(rows),
                        reason="insufficient_trades",
                    )
                    continue

                pnls = [r[0] for r in rows]
                wins = sum(1 for p in pnls if p > 0)
                win_rate = wins / len(pnls) if pnls else 0

                gross_profit = sum(p for p in pnls if p > 0)
                gross_loss = abs(sum(p for p in pnls if p < 0))
                pf = gross_profit / gross_loss if gross_loss > 0 else (
                    float('inf') if gross_profit > 0 else 0
                )

                # Get total trades for this symbol
                cur.execute("""
                    SELECT COUNT(*) FROM positions
                    WHERE symbol = ? AND status = 'closed' AND pnl IS NOT NULL
                """, (symbol,))
                total = cur.fetchone()[0]

                status = "ACTIVE"
                reason = ""
                if pf < SYMBOL_BLACKLIST_PF_MIN:
                    status = "BLACKLISTED"
                    reason = (
                        f"PF={pf:.2f} < {SYMBOL_BLACKLIST_PF_MIN:.2f} "
                        f"over last {len(pnls)} trades"
                    )

                self._symbol_blacklist[symbol] = SymbolBlacklistEntry(
                    symbol=symbol,
                    status=status,
                    rolling_pf=pf,
                    rolling_win_rate=win_rate,
                    trades_in_window=len(rows),
                    total_trades=total,
                    blacklisted_at=now if status == "BLACKLISTED" else 0,
                    reason=reason,
                )

                if status == "BLACKLISTED":
                    logger.warning(
                        "🚫 SYMBOL BLACKLISTED: {} — PF={:.2f} WR={:.1%} ({} trades)",
                        symbol, pf, win_rate, len(pnls),
                    )

            conn.close()

        except Exception as e:
            logger.warning("Symbol blacklist refresh error: {}", e)

    # ─────────────────────────────────────────────────────────
    # 3. SESSION BLACKLIST
    # ─────────────────────────────────────────────────────────

    def _check_session_blacklist(self, session: str) -> SessionBlacklistEntry:
        """Check if a session is blacklisted."""
        self._refresh_session_blacklist()

        entry = self._session_blacklist.get(session)
        if entry:
            return entry

        return SessionBlacklistEntry(
            session=session,
            status="ACTIVE",
            reason="insufficient_data",
        )

    def _refresh_session_blacklist(self) -> None:
        """Refresh session blacklist from trade history."""
        now = time.time()
        if now - self._last_session_check < self._cache_ttl:
            return
        self._last_session_check = now

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            # Get all sessions with closed trades
            cur.execute("""
                SELECT DISTINCT session
                FROM positions
                WHERE status = 'closed' AND pnl IS NOT NULL
                  AND session IS NOT NULL AND session != ''
            """)
            sessions = [r[0] for r in cur.fetchall()]

            for session in sessions:
                # Get last N trades for this session
                cur.execute("""
                    SELECT pnl
                    FROM positions
                    WHERE session = ? AND status = 'closed' AND pnl IS NOT NULL
                    ORDER BY closed_at DESC
                    LIMIT ?
                """, (session, SESSION_BLACKLIST_LOOKBACK))
                rows = cur.fetchall()

                if len(rows) < SESSION_MIN_TRADES:
                    self._session_blacklist[session] = SessionBlacklistEntry(
                        session=session,
                        status="ACTIVE",
                        trades_in_window=len(rows),
                        reason="insufficient_trades",
                    )
                    continue

                pnls = [r[0] for r in rows]
                wins = sum(1 for p in pnls if p > 0)
                win_rate = wins / len(pnls) if pnls else 0

                gross_profit = sum(p for p in pnls if p > 0)
                gross_loss = abs(sum(p for p in pnls if p < 0))
                pf = gross_profit / gross_loss if gross_loss > 0 else (
                    float('inf') if gross_profit > 0 else 0
                )

                status = "ACTIVE"
                reason = ""
                if pf < SESSION_BLACKLIST_PF_MIN:
                    status = "BLACKLISTED"
                    reason = (
                        f"PF={pf:.2f} < {SESSION_BLACKLIST_PF_MIN:.2f} "
                        f"over last {len(pnls)} trades"
                    )

                self._session_blacklist[session] = SessionBlacklistEntry(
                    session=session,
                    status=status,
                    rolling_pf=pf,
                    rolling_win_rate=win_rate,
                    trades_in_window=len(rows),
                    blacklisted_at=now if status == "BLACKLISTED" else 0,
                    reason=reason,
                )

                if status == "BLACKLISTED":
                    logger.warning(
                        "🚫 SESSION BLACKLISTED: {} — PF={:.2f} WR={:.1%} ({} trades)",
                        session, pf, win_rate, len(pnls),
                    )

            conn.close()

        except Exception as e:
            logger.warning("Session blacklist refresh error: {}", e)

    # ─────────────────────────────────────────────────────────
    # 4. CONFIDENCE CALIBRATION
    # ─────────────────────────────────────────────────────────

    def _check_confidence_calibration(self, confidence: float) -> ConfidenceBucket:
        """Check if the confidence value falls in a historically losing bucket."""
        self._refresh_confidence_calibration()

        # Find the bucket
        bucket_min = int(confidence // CONFIDENCE_BUCKET_SIZE) * CONFIDENCE_BUCKET_SIZE
        bucket_max = bucket_min + CONFIDENCE_BUCKET_SIZE - 1
        label = f"{bucket_min}-{bucket_max}"

        bucket = self._confidence_buckets.get(label)
        if bucket:
            return bucket

        # Unknown bucket — allow
        return ConfidenceBucket(
            bucket_min=bucket_min,
            bucket_max=bucket_max,
            label=label,
            status="ACCEPT",
            reason="insufficient_data",
        )

    def _refresh_confidence_calibration(self) -> None:
        """Refresh confidence calibration from trade history."""
        now = time.time()
        if now - self._last_confidence_check < self._cache_ttl:
            return
        self._last_confidence_check = now

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            # Get all closed trades with confidence
            cur.execute("""
                SELECT confidence, pnl, realized_r
                FROM positions
                WHERE status = 'closed' AND pnl IS NOT NULL
                  AND confidence > 0
                ORDER BY closed_at DESC
            """)
            rows = cur.fetchall()
            conn.close()

            # Group into buckets
            buckets: Dict[str, List[Tuple[float, float]]] = defaultdict(list)
            for conf, pnl, r_val in rows:
                bucket_min = int(conf // CONFIDENCE_BUCKET_SIZE) * CONFIDENCE_BUCKET_SIZE
                bucket_max = bucket_min + CONFIDENCE_BUCKET_SIZE - 1
                label = f"{bucket_min}-{bucket_max}"
                buckets[label].append((pnl, r_val or 0))

            self._confidence_buckets.clear()

            for label, trades in buckets.items():
                parts = label.split("-")
                bucket_min = int(parts[0])
                bucket_max = int(parts[1])

                pnls = [t[0] for t in trades]
                r_vals = [t[1] for t in trades]

                wins = sum(1 for p in pnls if p > 0)
                losses = sum(1 for p in pnls if p <= 0)
                win_rate = wins / len(pnls) if pnls else 0

                gross_profit = sum(p for p in pnls if p > 0)
                gross_loss = abs(sum(p for p in pnls if p < 0))
                pf = gross_profit / gross_loss if gross_loss > 0 else (
                    float('inf') if gross_profit > 0 else 0
                )

                avg_r = sum(r_vals) / len(r_vals) if r_vals else 0

                status = "ACCEPT"
                reason = ""
                if len(trades) >= CONFIDENCE_MIN_TRADES and pf < CONFIDENCE_BUCKET_PF_MIN:
                    status = "REJECT"
                    reason = (
                        f"PF={pf:.2f} < {CONFIDENCE_BUCKET_PF_MIN:.2f} "
                        f"over {len(trades)} trades in {label} range"
                    )

                self._confidence_buckets[label] = ConfidenceBucket(
                    bucket_min=bucket_min,
                    bucket_max=bucket_max,
                    label=label,
                    trades=len(trades),
                    wins=wins,
                    losses=losses,
                    win_rate=win_rate,
                    profit_factor=pf,
                    avg_r=avg_r,
                    total_pnl=sum(pnls),
                    status=status,
                    reason=reason,
                )

                if status == "REJECT":
                    logger.warning(
                        "🚫 CONFIDENCE RANGE REJECTED: {} — PF={:.2f} WR={:.1%} "
                        "({} trades, avg_r={:.3f}R)",
                        label, pf, win_rate, len(trades), avg_r,
                    )

        except Exception as e:
            logger.warning("Confidence calibration refresh error: {}", e)

    # ─────────────────────────────────────────────────────────
    # 5. DAILY LOSS STOP
    # ─────────────────────────────────────────────────────────

    def _check_daily_loss(self) -> DailyLossState:
        """Check if daily loss exceeds the stop threshold."""
        now = time.time()

        # Cache check
        if now - self._last_daily_check < 30:  # Check every 30 seconds
            return self._daily_loss
        self._last_daily_check = now

        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            # Get today's PnL from closed trades
            cur.execute("""
                SELECT COALESCE(SUM(pnl), 0)
                FROM positions
                WHERE status = 'closed' AND pnl IS NOT NULL
                  AND date(closed_at, 'unixepoch') = ?
            """, (today,))
            daily_pnl = cur.fetchone()[0] or 0

            # Get starting balance (from daily_pnl table or fallback to current balance)
            starting_balance = self._balance
            try:
                cur.execute("""
                    SELECT starting_balance
                    FROM daily_pnl
                    WHERE date = ?
                    ORDER BY id DESC
                    LIMIT 1
                """, (today,))
                row = cur.fetchone()
                if row and row[0]:
                    starting_balance = row[0]
            except Exception:
                pass  # Table may not exist — use current balance as fallback

            conn.close()

            if starting_balance <= 0:
                starting_balance = self._balance

            daily_pnl_pct = (daily_pnl / starting_balance) * 100 if starting_balance > 0 else 0

            blocked = daily_pnl_pct <= -DAILY_LOSS_STOP_PCT
            reason = ""
            if blocked:
                reason = (
                    f"Daily loss {daily_pnl_pct:.2f}% exceeds "
                    f"-{DAILY_LOSS_STOP_PCT:.1f}% limit"
                )
                logger.warning(
                    "🔴 DAILY LOSS STOP: PnL={:.2f} ({:.2f}%) — limit -{:.1f}%",
                    daily_pnl, daily_pnl_pct, DAILY_LOSS_STOP_PCT,
                )

            self._daily_loss = DailyLossState(
                date=today,
                daily_pnl=daily_pnl,
                daily_pnl_pct=daily_pnl_pct,
                starting_balance=starting_balance,
                current_balance=starting_balance + daily_pnl,
                blocked=blocked,
                reason=reason,
            )

            return self._daily_loss

        except Exception as e:
            logger.warning("Daily loss check error: {}", e)
            return self._daily_loss

    # ─────────────────────────────────────────────────────────
    # 6. TIME-BASED EXIT (called from check_stale_trades)
    # ─────────────────────────────────────────────────────────

    # Implemented in check_stale_trades() above

    # ─────────────────────────────────────────────────────────
    # 7. MAX SIMULTANEOUS EXPOSURE
    # ─────────────────────────────────────────────────────────

    def _check_exposure(
        self,
        signal: Dict[str, Any],
        open_positions: List[Dict],
    ) -> ExposureCheck:
        """Check if adding this position would exceed exposure limits."""
        side = signal.get("side", "").upper()
        symbol = signal.get("symbol", "")

        # Count current longs and shorts
        current_longs = 0
        current_shorts = 0

        # Track which correlated groups are already occupied
        occupied_groups: Dict[str, str] = {}  # group → side

        for pos in open_positions:
            pos_side = pos.get("side", "").upper()
            pos_symbol = pos.get("symbol", "")

            if pos_side == "LONG":
                current_longs += 1
            elif pos_side == "SHORT":
                current_shorts += 1

            # Check correlated groups
            for group_name, group_symbols in CORRELATED_GROUPS.items():
                if pos_symbol in group_symbols:
                    if group_name not in occupied_groups:
                        occupied_groups[group_name] = pos_side

        # Check direction limit
        if side == "LONG" and current_longs >= MAX_LONG_POSITIONS:
            return ExposureCheck(
                approved=False,
                current_longs=current_longs,
                current_shorts=current_shorts,
                reason=(
                    f"Max {MAX_LONG_POSITIONS} long position(s) allowed, "
                    f"currently {current_longs}"
                ),
            )

        if side == "SHORT" and current_shorts >= MAX_SHORT_POSITIONS:
            return ExposureCheck(
                approved=False,
                current_longs=current_longs,
                current_shorts=current_shorts,
                reason=(
                    f"Max {MAX_SHORT_POSITIONS} short position(s) allowed, "
                    f"currently {current_shorts}"
                ),
            )

        # Check correlated group exposure
        for group_name, group_symbols in CORRELATED_GROUPS.items():
            if symbol in group_symbols and group_name in occupied_groups:
                existing_side = occupied_groups[group_name]
                if existing_side == side:
                    return ExposureCheck(
                        approved=False,
                        current_longs=current_longs,
                        current_shorts=current_shorts,
                        blocked_group=group_name,
                        reason=(
                            f"Correlated group '{group_name}' already has a "
                            f"{existing_side} position — {symbol} blocked"
                        ),
                    )

        return ExposureCheck(
            approved=True,
            current_longs=current_longs,
            current_shorts=current_shorts,
        )
