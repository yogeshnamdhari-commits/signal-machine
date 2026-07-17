"""
EMA_V5 Signal Engine — Signal generation with dedup and cooldown.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from loguru import logger

from .config import ema_v5_config
from .utils import atr, compute_rr
from .rr_audit import get_rr_audit


class SignalEngine:
    """Generates signals from validated components with dedup protection."""

    def __init__(self) -> None:
        self._last_signal: Dict[str, Dict] = {}  # symbol → last signal
        self._cooldowns: Dict[str, float] = {}   # symbol → cooldown expiry
        self._global_cooldown: float = 0
        # ── Diagnostic counters for final publication gate ──
        self.gate_rejections: Dict[str, int] = {
            "duplicate": 0,
            "cooldown": 0,
            "invalid_entry_atr": 0,
            "rr_too_low": 0,
            "passed": 0,
        }

    def get_gate_stats(self) -> Dict[str, int]:
        """Return current gate rejection counts (for bridge/dashboard)."""
        return dict(self.gate_rejections)

    def reset_gate_stats(self) -> None:
        """Reset gate counters (called per scan cycle)."""
        for k in self.gate_rejections:
            self.gate_rejections[k] = 0

    def generate(
        self,
        symbol: str,
        regime: str,
        regime_eval: Dict,
        trend_eval: Dict,
        pullback_eval: Dict,
        candle_eval: Dict,
        volume_eval: Dict,
        confidence_eval: Dict,
        ema_data: Dict,
    ) -> Optional[Dict]:
        """Generate a signal if all conditions pass.

        Returns signal dict or None.
        """
        cfg = ema_v5_config.signal
        _conf = confidence_eval.get("confidence", 0)

        # ── Duplicate protection ──
        if not self._check_duplicate(symbol, regime):
            self.gate_rejections["duplicate"] += 1
            logger.info(
                "🔴 SIGNAL_GATE_1: {} BLOCKED duplicate protection "
                "(same_sym_sec={}) regime={} conf={:.1f}",
                symbol, ema_v5_config.cooldown.same_symbol_sec, regime, _conf,
            )
            return None

        # ── Cooldown check ──
        if not self._check_cooldown(symbol):
            self.gate_rejections["cooldown"] += 1
            _remaining = max(0, self._cooldowns.get(symbol, 0) - time.time())
            logger.info(
                "🔴 SIGNAL_GATE_2: {} BLOCKED cooldown "
                "(remaining={:.0f}s) conf={:.1f}",
                symbol, _remaining, _conf,
            )
            return None

        # ── Compute entry/SL/TP ──
        side = "LONG" if regime == "BUY_MODE" else "SHORT"
        entry = ema_data.get("last_close", 0)
        atr_val = ema_data.get("atr_14", 0)

        if entry <= 0 or atr_val <= 0:
            self.gate_rejections["invalid_entry_atr"] += 1
            logger.info(
                "🔴 SIGNAL_GATE_3: {} BLOCKED entry/ATR invalid "
                "(entry={:.6f} atr={:.6f}) conf={:.1f}",
                symbol, entry, atr_val, _conf,
            )
            return None

        # SL placement: beyond the pullback level
        pullback_level = pullback_eval.get("touch_level", "ema20")
        ema_key = f"ema{'20' if pullback_level == 'ema20' else '50'}"
        ema_val = ema_data.get(ema_key, 0)

        if side == "LONG":
            sl = entry - atr_val * cfg.sl_atr_mult
            # Tighten SL if EMA50 provides better level
            if ema_val > 0 and ema_val < entry:
                sl = max(sl, ema_val - atr_val * 0.2)
            tp1 = entry + abs(entry - sl) * cfg.tp1_rr
            tp2 = entry + abs(entry - sl) * cfg.tp2_rr
            tp3 = entry + abs(entry - sl) * cfg.tp3_rr
        else:
            sl = entry + atr_val * cfg.sl_atr_mult
            if ema_val > 0 and ema_val > entry:
                sl = min(sl, ema_val + atr_val * 0.2)
            tp1 = entry - abs(sl - entry) * cfg.tp1_rr
            tp2 = entry - abs(sl - entry) * cfg.tp2_rr
            tp3 = entry - abs(sl - entry) * cfg.tp3_rr

        # Validate R:R (with floating-point tolerance)
        rr = compute_rr(entry, sl, tp1)
        _rr_epsilon = 0.005  # Tolerance: allow rr within 0.5% of minimum
        if rr < (cfg.min_rr - _rr_epsilon):
            self.gate_rejections["rr_too_low"] += 1
            logger.info(
                "🔴 SIGNAL_GATE_4: {} BLOCKED R:R "
                "(rr={:.4f} < min_rr={:.4f} - ε={:.4f}) entry={:.6f} SL={:.6f} TP1={:.6f} conf={:.1f}",
                symbol, rr, cfg.min_rr, _rr_epsilon, entry, sl, tp1, _conf,
            )
            # ── RR AUDIT: Record detailed rejection data ──
            try:
                rr_audit = get_rr_audit()
                rr_audit.record_rejection(
                    symbol=symbol,
                    side=side,
                    entry=entry,
                    stop_loss=sl,
                    tp1=tp1,
                    tp2=tp2,
                    tp3=tp3,
                    atr_value=atr_val,
                    sl_atr_mult=cfg.sl_atr_mult,
                    tp1_rr_mult=cfg.tp1_rr,
                    session="ema_v5",
                    regime=regime,
                    confidence=_conf,
                    rr_required=cfg.min_rr,
                    rejection_source="signal_engine",
                    rejection_reason=f"RR {rr:.2f} < {cfg.min_rr:.2f} (SL dist={abs(entry-sl)/entry*100:.2f}%)",
                )
            except Exception as e:
                logger.debug("RR_AUDIT: Failed to record rejection: {}", e)
            return None

        # ── Build signal ──
        signal = {
            "action": "open_position",
            "strategy": "ema_v5",
            "symbol": symbol,
            "side": side,
            "entry": entry,
            "entry_type": "LIMIT",
            "sl": round(sl, 8),
            "sl_dist_pct": round(abs(entry - sl) / entry * 100, 3),
            "take_profit_1": round(tp1, 8),
            "take_profit_2": round(tp2, 8),
            "take_profit_3": round(tp3, 8),
            "tp1_exit_pct": cfg.tp1_exit_pct,
            "tp2_exit_pct": cfg.tp2_exit_pct,
            "tp3_exit_pct": cfg.tp3_exit_pct,
            "rr_1": round(rr, 2),
            "rr_2": round(compute_rr(entry, sl, tp2), 2),
            "rr_3": round(compute_rr(entry, sl, tp3), 2),
            "confidence": confidence_eval.get("confidence", 0),
            "regime": regime,
            "session": "ema_v5",
            "trailing_stop": {
                "breakeven_at_r": ema_v5_config.trade.breakeven_at_r,
                "activate_at_r": ema_v5_config.trade.breakeven_at_r,
                "trail_atr_mult": ema_v5_config.trade.trailing_atr_mult,
            },
            "ema_data": {
                "ema20": ema_data.get("ema20", 0),
                "ema50": ema_data.get("ema50", 0),
                "ema144": ema_data.get("ema144", 0),
                "ema200": ema_data.get("ema200", 0),
            },
            "components": {
                "regime": regime_eval.get("reason", ""),
                "trend": trend_eval.get("reason", ""),
                "pullback": pullback_eval.get("reason", ""),
                "candle": candle_eval.get("reason", ""),
                "volume": volume_eval.get("reason", ""),
                "confidence": confidence_eval.get("breakdown", {}),
            },
            # Pattern metadata for research platform
            "mss_score": trend_eval.get("trend_score", 0),
            "fvg_score": candle_eval.get("candle_score", 0),
            "volatility_score": volume_eval.get("volume_score", 0),
            "institutional_score": trend_eval.get("institutional_score", 0),
            "entry_reason": f"ema_v5_{regime}_{pullback_eval.get('touch_level', 'ema20')}",
            "strategy_version": "ema_v5",
            "timestamp": time.time(),
        }

        # Record signal for dedup
        self._last_signal[symbol] = {
            "regime": regime,
            "timestamp": time.time(),
        }

        # Set cooldown
        cd_cfg = ema_v5_config.cooldown
        self._cooldowns[symbol] = time.time() + cd_cfg.same_symbol_sec
        self._global_cooldown = time.time() + cd_cfg.global_sec

        logger.info(
            "📊 EMA_V5 SIGNAL: {} {} @ {:.4f} SL={:.4f} TP1={:.4f} conf={:.1f}%",
            side, symbol, entry, sl, tp1, signal["confidence"],
        )
        logger.info("SIGNAL_TRACE sym={} PASS all gates — entry={:.4f} SL={:.4f} TP1={:.4f} RR={:.2f}", symbol, entry, sl, tp1, rr)
        self.gate_rejections["passed"] += 1

        return signal

    def _check_duplicate(self, symbol: str, regime: str) -> bool:
        """Prevent duplicate signals unless conditions changed."""
        last = self._last_signal.get(symbol)
        if not last:
            return True
        # Same regime = duplicate (unless cooldown expired)
        if last.get("regime") == regime:
            age = time.time() - last.get("timestamp", 0)
            if age < ema_v5_config.cooldown.same_symbol_sec:
                return False
        return True

    def _check_cooldown(self, symbol: str) -> bool:
        """Check if symbol is in cooldown period."""
        now = time.time()
        if now < self._global_cooldown:
            return False
        if now < self._cooldowns.get(symbol, 0):
            return False
        return True

    def clear_cooldown(self, symbol: str) -> None:
        """Clear cooldown for a symbol (called on trade close)."""
        self._cooldowns.pop(symbol, None)
        self._last_signal.pop(symbol, None)
