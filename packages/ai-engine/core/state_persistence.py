"""
State Persistence — auto-save engine state on beneficial changes.

Persists across restarts:
- RiskEngine: balance, daily_pnl, peak, equity_peak, drawdown
- SignalFilter: symbol losses, dynamic blacklist, confidence history, adaptive weights
- Engine: symbol cooldowns, equity history, closed trades

Saves to data/engine_state.json:
- Every 60 seconds (periodic)
- On every beneficial change (trade closed, blacklist update, new signal)
- On graceful shutdown (signal handler)
"""
from __future__ import annotations

import json
import os
import signal
import time
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

_AI_ROOT = Path(__file__).resolve().parent.parent  # packages/ai-engine/
_STATE_DIR = _AI_ROOT / "data"
_STATE_FILE = _STATE_DIR / "engine_state.json"
_STATE_BACKUP = _STATE_DIR / "engine_state.backup.json"

# Maximum items to persist (bounded memory)
_MAX_EQUITY_HISTORY = 500
_MAX_CLOSED_TRADES = 200
_MAX_CONFIDENCE_HISTORY = 200
_MAX_COOLDOWNS = 100


class StatePersistence:
    """Atomic state save/load for engine survival across restarts.

    Usage:
        state = StatePersistence()
        state.bind(engine, risk_engine, signal_filter)
        state.start_autosave()        # periodic + shutdown handler
        state.load()                  # on engine start
        state.mark_dirty()            # call after beneficial changes
        state.save()                  # manual save (also called automatically)
    """

    def __init__(self) -> None:
        self._engine = None
        self._risk = None
        self._signal_filter = None
        self._dirty = False
        self._save_interval = 30.0  # seconds — reduced from 60 for faster state recovery
        self._last_save = 0.0
        self._autosave_task: Optional[threading.Timer] = None
        self._shutdown_registered = False
        self._enabled = True

    def bind(self, engine, risk_engine, signal_filter) -> None:
        """Bind to engine components for state extraction/injection."""
        self._engine = engine
        self._risk = risk_engine
        self._signal_filter = signal_filter
        logger.info("💾 State persistence bound to engine components")

    def mark_dirty(self) -> None:
        """Mark state as changed — triggers save on next autosave cycle."""
        self._dirty = True

    # ── Save ──────────────────────────────────────────────────

    def save(self) -> bool:
        """Serialize current state to JSON atomically (write-then-rename)."""
        if not self._enabled or not self._engine:
            return False

        try:
            state = self._extract_state()

            # Atomic write: write to temp file, then rename
            tmp = _STATE_FILE.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2, default=str)

            # CRITICAL: Backup PREVIOUS state BEFORE overwriting (not after)
            # If crash happens during rename, old state is safe in backup
            if _STATE_FILE.exists():
                try:
                    _STATE_FILE.replace(_STATE_BACKUP)
                except Exception:
                    pass

            tmp.replace(_STATE_FILE)
            self._dirty = False
            self._last_save = time.time()
            logger.debug("💾 State saved ({} bytes)", _STATE_FILE.stat().st_size)
            return True

        except Exception as e:
            logger.error("💾 State save FAILED: {}", e)
            return False

    def _extract_state(self) -> Dict[str, Any]:
        """Extract serializable state from all bound components."""
        now = time.time()
        risk = self._risk
        sf = self._signal_filter
        eng = self._engine

        # ── RiskEngine state ──
        risk_state = {}
        if risk:
            risk_state = {
                "balance": risk.balance,
                "daily_pnl": risk.daily_pnl,
                "peak": risk.peak,
                "equity_peak": getattr(risk, "_equity_peak", risk.balance),
                "current_drawdown": getattr(risk, "_current_drawdown", 0.0),
                "highest_pnl": _truncate_dict(risk._highest_pnl, _MAX_COOLDOWNS),
                "mfe_pct": _truncate_dict(getattr(risk, "_mfe_pct", {}), _MAX_COOLDOWNS),
                "breached_breakeven": list(getattr(risk, "_breached_breakeven", set())),
                "partials_taken": list(getattr(risk, "_partials_taken", set())),
            }

        # ── SignalFilter state ──
        sf_state = {}
        if sf:
            sf_state = {
                "min_confidence": sf._min_confidence,
                "confidence_history": sf._confidence_history[-_MAX_CONFIDENCE_HISTORY:],
                "symbol_losses": {k: v[-10:] for k, v in sf._symbol_losses.items()
                                  if v},  # last 10 losses per symbol
                "blacklisted_symbols": list(sf._blacklisted_symbols),
                "calibration_buckets": dict(sf._calibration_buckets),
                "factor_performance": dict(sf._factor_performance),
                "last_adaptive_update": sf._last_adaptive_update,
            }

        # ── Engine state ──
        eng_state = {}
        if eng:
            eng_state = {
                "symbol_cooldowns": _truncate_dict(
                    getattr(eng, "_symbol_cooldowns", {}), _MAX_COOLDOWNS
                ),
                "equity_history": (
                    getattr(eng, "_equity_history", [])[-_MAX_EQUITY_HISTORY:]
                ),
                "equity_peak": getattr(eng, "_equity_peak", 0.0),
                "closed_trades": (
                    getattr(eng, "_closed_trades", [])[-_MAX_CLOSED_TRADES:]
                ),
                # Save total PnL for balance reconstruction
                "total_closed_pnl": sum(
                    t.get("pnl", 0) for t in getattr(eng, "_closed_trades", [])
                ),
            }

        return {
            "version": 2,
            "saved_at": now,
            "saved_at_human": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
            "risk": risk_state,
            "signal_filter": sf_state,
            "engine": eng_state,
        }

    # ── Load ──────────────────────────────────────────────────

    def load(self) -> bool:
        """Load state from disk and inject into bound components."""
        if not self._enabled or not self._engine:
            return False

        state = self._load_from_disk()
        if not state:
            return False

        version = state.get("version", 0)
        saved_at = state.get("saved_at_human", "unknown")
        logger.info("💾 Loading state from {} (saved {})", _STATE_FILE.name, saved_at)

        try:
            self._inject_state(state)
            logger.info("💾 State restored successfully (v{})", version)
            return True
        except Exception as e:
            logger.error("💾 State restore FAILED: {}", e)
            # Try backup
            logger.info("💾 Trying backup state file...")
            try:
                if _STATE_BACKUP.exists():
                    with open(_STATE_BACKUP) as f:
                        state = json.load(f)
                    self._inject_state(state)
                    logger.info("💾 State restored from backup")
                    return True
            except Exception as e2:
                logger.error("💾 Backup restore also failed: {}", e2)
            return False

    def _load_from_disk(self) -> Optional[Dict]:
        """Load state JSON from disk with fallback to backup."""
        for path in (_STATE_FILE, _STATE_BACKUP):
            if path.exists():
                try:
                    with open(path) as f:
                        state = json.load(f)
                    return state
                except (json.JSONDecodeError, OSError) as e:
                    logger.warning("💾 Failed to read {}: {}", path.name, e)
        return None

    def _inject_state(self, state: Dict) -> None:
        """Inject loaded state into bound components."""
        risk = self._risk
        sf = self._signal_filter
        eng = self._engine

        # ── Restore RiskEngine ──
        rs = state.get("risk", {})
        if risk and rs:
            old_balance = risk.balance
            risk.balance = rs.get("balance", risk.balance)
            risk.daily_pnl = rs.get("daily_pnl", 0.0)
            risk.peak = rs.get("peak", risk.balance)
            risk._equity_peak = rs.get("equity_peak", risk.balance)
            risk._current_drawdown = rs.get("current_drawdown", 0.0)
            # Restore trailing stop state
            # FIX: MERGE instead of overwrite — preserve any entries added by
            # load_positions_from_db() or the risk loop since last save.
            _saved_peaks = rs.get("highest_pnl", {})
            for k, v in _saved_peaks.items():
                if k not in risk._highest_pnl or v > risk._highest_pnl[k]:
                    risk._highest_pnl[k] = v
            _saved_mfe = rs.get("mfe_pct", {})
            for k, v in _saved_mfe.items():
                if k not in risk._mfe_pct or v > risk._mfe_pct[k]:
                    risk._mfe_pct[k] = v
            risk._breached_breakeven = set(rs.get("breached_breakeven", []))
            risk._partials_taken = set(rs.get("partials_taken", []))
            if risk.balance != old_balance:
                logger.info("  💰 Balance: ${:,.2f} → ${:,.2f}", old_balance, risk.balance)
            logger.info("  📊 Daily PnL: ${:+.2f} | Peak: ${:,.2f} | DD: {:.1f}%",
                        risk.daily_pnl, risk.peak, risk._current_drawdown)

        # ── Restore SignalFilter ──
        sfs = state.get("signal_filter", {})
        if sf and sfs:
            sf._min_confidence = sfs.get("min_confidence", sf._min_confidence)
            # Phase 14: Clamp restored threshold — old state had 0.85 (blocked 99% of symbols)
            sf._min_confidence = max(0.55, min(0.70, sf._min_confidence))
            sf._confidence_history = sfs.get("confidence_history", [])
            # Restore symbol losses
            sf._symbol_losses.clear()
            for sym, losses in sfs.get("symbol_losses", {}).items():
                sf._symbol_losses[sym] = losses
            # Restore dynamic blacklist (merge with permanent toxic list)
            dynamic_blacklist = set(sfs.get("blacklisted_symbols", []))
            sf._blacklisted_symbols.update(dynamic_blacklist)
            # Restore calibration
            cal = sfs.get("calibration_buckets", {})
            sf._calibration_buckets.clear()
            for k, v in cal.items():
                sf._calibration_buckets[k] = v
            # Restore adaptive weights
            fp = sfs.get("factor_performance", {})
            sf._factor_performance.clear()
            for k, v in fp.items():
                sf._factor_performance[k] = v
            sf._last_adaptive_update = sfs.get("last_adaptive_update", 0)
            logger.info("  🎯 Confidence threshold: {:.0%} | History: {} trades | Blacklist: {} symbols",
                        sf._min_confidence, len(sf._confidence_history),
                        len(sf._blacklisted_symbols))

        # ── Restore Engine ──
        es = state.get("engine", {})
        if eng and es:
            eng._symbol_cooldowns = es.get("symbol_cooldowns", {})
            eng._equity_history = es.get("equity_history", [])
            eng._equity_peak = es.get("equity_peak", 0.0)
            eng._closed_trades = es.get("closed_trades", [])
            n_cooldowns = len(eng._symbol_cooldowns)
            n_equity = len(eng._equity_history)
            n_trades = len(eng._closed_trades)
            logger.info("  🔄 Cooldowns: {} | Equity points: {} | Closed trades: {}",
                        n_cooldowns, n_equity, n_trades)
            # ── P0 FIX: Backfill _closed_trades from DB ──
            # JSON state file can be stale (30s autosave gap) or lost on crash.
            # DB is always the source of truth for closed trade history.
            try:
                import sqlite3 as _s3
                import os as _os
                _db = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)),
                                     "data", "institutional_v1.db")
                _c = _s3.connect(_db, timeout=5)
                _c.execute("PRAGMA journal_mode=WAL")
                _cr = _c.cursor()
                _cr.execute("""SELECT symbol, side, entry_price, quantity, pnl,
                    closed_at, exit_reason, confidence, institutional_score,
                    risk_reward, hold_minutes, session
                    FROM positions_archive WHERE status='closed'
                    AND closed_at > ? ORDER BY closed_at""",
                    (time.time() - 86400,))
                _db_trades = _cr.fetchall()
                _c.close()
                # Merge: keep existing trades, add missing ones from DB
                existing_keys = set()
                for t in eng._closed_trades:
                    _k = (t.get('symbol',''), t.get('pnl',0), t.get('closed_at',0))
                    existing_keys.add(_k)
                _added = 0
                for row in _db_trades:
                    _sym, _sd, _ep, _qty, _pnl, _ca, _er, _cf, _is, _rr, _hm, _ss = row
                    _k = (_sym, _pnl, _ca)
                    if _k not in existing_keys:
                        eng._closed_trades.append({
                            'symbol': _sym, 'side': _sd, 'entry_price': _ep,
                            'quantity': _qty, 'pnl': _pnl, 'closed_at': _ca,
                            'exit_reason': _er, 'confidence': _cf or 0,
                            'institutional_score': _is or 0, 'risk_reward': _rr or 0,
                            'hold_minutes': _hm or 0, 'session': _ss or 'unknown',
                            'timestamp': _ca,
                        })
                        _added += 1
                if _added > 0:
                    eng._closed_trades.sort(key=lambda t: t.get('timestamp', 0))
                    logger.info("  📥 Backfilled {} closed trades from DB", _added)
            except Exception as _bt_err:
                logger.debug("  DB backfill of closed trades failed: {}", _bt_err)

    # ── Autosave ──────────────────────────────────────────────

    def start_autosave(self) -> None:
        """Start periodic autosave + register shutdown handler."""
        if self._shutdown_registered:
            return
        self._shutdown_registered = True

        # Register signal handlers for graceful shutdown save
        def _on_shutdown(signum, frame):
            logger.info("💾 Shutdown signal received ({}), saving state...", signum)
            self.save()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                old = signal.getsignal(sig)
                def _chain(signum, frame, _old=old):
                    self.save()
                    if callable(_old):
                        _old(signum, frame)
                signal.signal(sig, _chain)
            except (OSError, ValueError):
                pass  # Can't set signal handler in non-main thread

        # Start periodic autosave timer
        self._start_timer()
        logger.info("💾 Autosave enabled (every {}s + on shutdown)", self._save_interval)

    def _start_timer(self) -> None:
        """Start the periodic save timer."""
        if not self._enabled:
            return

        def _tick():
            if self._dirty:
                self.save()
            self._start_timer()  # reschedule

        self._autosave_timer = threading.Timer(self._save_interval, _tick)
        self._autosave_timer.daemon = True
        self._autosave_timer.start()

    def stop_autosave(self) -> None:
        """Stop autosave and do a final save."""
        if hasattr(self, '_autosave_timer') and self._autosave_timer:
            self._autosave_timer.cancel()
        self.save()


def _truncate_dict(d: Dict, max_items: int) -> Dict:
    """Keep only the most recent entries in a dict (by insertion order)."""
    if len(d) <= max_items:
        return dict(d)
    # Keep the most recent items
    items = list(d.items())[-max_items:]
    return dict(items)
