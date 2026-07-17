"""
Risk Engine — position sizing, SL/TP, trailing stops, breakeven, drawdown limits.
"""
from __future__ import annotations

import time
import numpy as np
from typing import Dict, Tuple
from loguru import logger
from config import config


class RiskEngine:
    def __init__(self) -> None:
        self.balance = 10_000.0
        self.daily_pnl = 0.0
        self.peak = self.balance
        self._equity_peak = self.balance  # for drawdown calc
        self._current_drawdown = 0.0
        self._positions: Dict[str, Dict] = {}
        # ── Trailing stop state (per-symbol) ──
        self._highest_pnl: Dict[str, float] = {}   # peak unrealized PnR in R-multiples
        self._mfe_pct: Dict[str, float] = {}        # FIX5: peak MFE as raw percentage
        self._breached_breakeven: set = set()        # symbols that moved SL to breakeven
        self._partials_taken: set = set()            # symbols where partial profit was taken
        # ── Position Sizing Engine (for alpha ranking integration) ──
        from scanner.position_sizing import PositionSizingEngine
        self.position_sizing = PositionSizingEngine()

    @property
    def open_count(self) -> int:
        """Single source of truth — always derived from actual positions dict."""
        return len(self._positions)

    async def load_positions_from_db(self) -> None:
        """Restore open positions from DB on startup so risk limits are accurate.

        P0 FIX: Also restore _highest_pnl (trailing stop peak state) from the
        DB column. Without this, engine restarts reset peak to 0R, causing
        time_exit_6h to fire on trades that had significant unrealized profit.

        If DB has no highest_pnl (old positions), reconstruct from price data.
        """
        try:
            from database.signal_repository import SignalRepository
            from pathlib import Path
            repo = SignalRepository(db_path=str(Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"))
            rows = await repo.get_open_positions()
            if rows:
                restored_count = 0
                reconstructed_count = 0
                for pos in rows:
                    sym = pos.get("symbol", "")
                    if sym and sym not in self._positions:
                        self._positions[sym] = {
                            "id": pos.get("id"),
                            "signal_id": pos.get("signal_id"),
                            "symbol": sym,
                            "side": pos.get("side", "LONG"),
                            "entry_price": pos.get("entry_price", 0),
                            "quantity": pos.get("quantity", 0),
                            "leverage": pos.get("leverage", 1),
                            "stop_loss": pos.get("stop_loss", 0),
                            "take_profit": pos.get("take_profit", 0),
                            "take_profit_2": pos.get("take_profit_2", 0),
                            "take_profit_3": pos.get("take_profit_3", 0),
                            "opened_at": pos.get("opened_at", 0),
                            "confidence": pos.get("confidence", 0),
                            "institutional_score": pos.get("institutional_score", 0),
                            "regime": pos.get("regime", "unknown"),
                        }
                        # ── P0 FIX: Restore trailing stop peak from DB ──
                        db_peak = pos.get("highest_pnl", 0) or 0
                        if db_peak > 0:
                            self._highest_pnl[sym] = db_peak
                            restored_count += 1
                        else:
                            # No persisted peak — reconstruct from price history
                            reconstructed = await self._reconstruct_peak_from_price(sym, pos)
                            if reconstructed > 0:
                                self._highest_pnl[sym] = reconstructed
                                reconstructed_count += 1
                        # ── FIX 5: Restore MFE% from DB ──
                        db_mfe = pos.get("mfe_pct", 0) or 0
                        if db_mfe > 0:
                            self._mfe_pct[sym] = db_mfe

                total = len(rows)
                logger.info(
                    "📋 Loaded {} open positions from DB ({} peaks restored, {} reconstructed)",
                    total, restored_count, reconstructed_count,
                )
            else:
                logger.info("📋 No open positions in DB at startup")
        except Exception as e:
            logger.warning("Could not load positions from DB: {}", e)

    async def _reconstruct_peak_from_price(self, sym: str, pos: Dict) -> float:
        """Reconstruct the peak R-multiple from kline price history.

        Used when DB has no persisted highest_pnl (old positions or first
        deploy of this fix). Queries the highest price since entry and
        computes the R-multiple that would have been tracked.

        Returns 0 if reconstruction fails (klines not available).
        """
        try:
            entry = pos.get("entry_price", 0)
            sl = pos.get("stop_loss", 0)
            side = pos.get("side", "LONG")
            opened_at = pos.get("opened_at", 0)

            if not entry or not opened_at:
                return 0

            # Risk per unit with same floor as check_exit_conditions
            risk_per_unit = max(abs(entry - sl) if sl else entry * 0.02, entry * 0.002)

            # Try to get the best price from klines (import from scanner)
            try:
                from scanner.data_fetcher import DataFetcher
                fetcher = DataFetcher()
                # Get 1h klines since entry (most reliable timeframe)
                klines = await fetcher.get_klines(sym, "1h", limit=100)
                if not klines:
                    return 0

                # Find highest/lowest price since entry
                best_price = entry
                for k in klines:
                    ts = k.get("timestamp", 0)
                    if ts >= opened_at:
                        high = k.get("high", 0)
                        low = k.get("low", 0)
                        if side == "LONG" and high > best_price:
                            best_price = high
                        elif side == "SHORT" and low < best_price:
                            best_price = low

                if best_price == entry:
                    return 0  # No movement since entry

                # Compute R-multiple
                if side == "LONG":
                    peak_r = (best_price - entry) / risk_per_unit
                else:
                    peak_r = (entry - best_price) / risk_per_unit

                if peak_r > 0:
                    logger.info("🔄 Reconstructed peak for {}: {:.1f}R from kline history", sym, peak_r)
                return max(0, peak_r)

            except Exception:
                return 0

        except Exception:
            return 0

    # ── Confidence-scaled position sizing ──────────────────────
    @staticmethod
    def _confidence_sizing_mult(inst_score: float) -> float:
        """Tiered position sizing by institutional score.
        
        Data-driven tiers (7-day backtest):
          95-100 → 2.50x  (elite: 60%+ WR, highest avg PnL)
          90-95  → 1.80x  (strong: positive expectancy)
          85-90  → 0.40x  (marginal: reduce exposure)
          <85    → blocked by quality gate
        """
        if inst_score >= 95:
            return 2.50
        if inst_score >= 90:
            return 1.80
        if inst_score >= 85:
            return 0.40
        return 0.0  # should never reach — quality gate blocks <85

    async def check_signal(self, signal: Dict) -> Dict:
        entry = signal.get("entry_price", 0)
        sl = signal.get("stop_loss", 0)
        conf = signal.get("confidence", 0)
        if not entry or not sl:
            return {"allowed": False, "reason": "missing prices"}

        # Daily loss
        max_dl = self.balance * config.risk.max_daily_loss_pct / 100
        if self.daily_pnl < -max_dl:
            return {"allowed": False, "reason": "daily loss limit"}

        # Drawdown
        dd = (self.peak - self.balance) / self.peak * 100 if self.peak else 0
        if dd >= config.risk.max_drawdown_pct:
            return {"allowed": False, "reason": "max drawdown"}

        # Position count
        if self.open_count >= config.risk.max_open_positions:
            return {"allowed": False, "reason": "max positions"}

        # Confidence — use institutional_score as primary (0-100 scale),
        # fall back to raw confidence (0-1 scale)
        inst_score = signal.get("institutional_score", 0)
        conf = signal.get("confidence", 0)
        effective_score = inst_score if inst_score > 0 else conf * 100
        if effective_score < 70:  # Phase 12: lowered from 90 — signals passing 13/13 checklist have proven quality
            return {"allowed": False, "reason": f"low quality score: {effective_score:.1f}/100 < 90"}

        # ── Confidence-scaled position sizing ──
        size_mult = self._confidence_sizing_mult(effective_score)

        # ── SL DISTANCE CAP: Reject signals with excessively wide stops ──
        sl_dist_pct = abs(entry - sl) / entry * 100 if entry else 0
        if sl_dist_pct > config.risk.max_sl_distance_pct:
            return {"allowed": False, "reason": f"SL too wide: {sl_dist_pct:.1f}% > {config.risk.max_sl_distance_pct}%"}

        risk_usd = self.balance * config.risk.risk_per_trade_pct / 100 * size_mult

        # FIX 8: Regime-based position sizing (range=0.5x, compression=0.7x)
        regime_size_mult = signal.get("regime_size_mult", 1.0)
        if regime_size_mult != 1.0:
            risk_usd *= regime_size_mult
        # Audit: Enforce minimum 0.2% risk distance floor for RR calculation to prevent inflated multiples
        risk_dist = max(abs(entry - sl), entry * 0.002)
        if risk_dist == 0:
            return {"allowed": False, "reason": "zero risk distance"}

        qty = risk_usd / risk_dist
        pos_val = qty * entry
        # ── RISK-NORMALIZED CAP: Use risk amount as primary limit, not position value ──
        max_risk_usd = self.balance * config.risk.risk_per_trade_pct / 100 * size_mult * 2.0  # 2x base risk as hard cap
        actual_risk = risk_dist * qty
        if actual_risk > max_risk_usd:
            qty = max_risk_usd / risk_dist
            pos_val = qty * entry
        max_pos = self.balance * config.risk.max_position_pct / 100
        if pos_val > max_pos:
            qty = max_pos / entry
            pos_val = max_pos

        tp = signal.get("take_profit", entry)
        rr = abs(tp - entry) / risk_dist if risk_dist else 0

        return {
            "allowed": True,
            "quantity": round(qty, 6),
            "position_value": round(pos_val, 2),
            "risk_reward": round(rr, 2),
            "margin_required": round(pos_val / config.risk.max_leverage, 2),
            "sizing_multiplier": round(size_mult, 2),
        }

    # ── Exit logic with trailing stop + breakeven + partial + time ──

    def check_exit_conditions(self, position: Dict, price: float) -> Tuple[bool, str]:
        """v5 Enhanced exit conditions:
        1. Standard SL/TP hit
        2. v5: Early trailing stop — activated at +0.5% MFE (was TP2 only)
        3. v5: Breakeven at +0.3% MFE (was 1.0R) — early protection
        4. Partial profit — TP1 35%, TP2 40%, TP3 25%
        5. Time-based exit — max 24h, no-progress 6h
        6. MFE trailing stop — catch trades with strong directional move
        7. v5: Tighter trail after TP1 hit (0.5× ATR)"""
        side = position.get("side", "")
        entry = position.get("entry_price", 0)
        sl = position.get("stop_loss", 0)
        tp = position.get("take_profit", 0)
        qty = position.get("quantity", 0)
        lev = position.get("leverage", 1)
        opened_at = position.get("opened_at", 0)
        sym = position.get("symbol", "")

        if side == "LONG" or side == "SHORT":
            # --- Compute R-multiple of current price vs entry/SL ---
            # Audit: Enforce minimum 0.2% risk floor to prevent extreme R-multiple outliers
            risk_per_unit = max(abs(entry - sl) if sl else entry * 0.02, entry * 0.002)

            if side == "LONG":
                unrealized_r = (price - entry) / risk_per_unit
            else:
                unrealized_r = (entry - price) / risk_per_unit

            # Track peak R for trailing stop
            prev_peak = self._highest_pnl.get(sym, 0)
            if unrealized_r > prev_peak:
                self._highest_pnl[sym] = unrealized_r
                prev_peak = unrealized_r
            # FIX: Always ensure symbol is tracked (even at 0) so state persistence saves it
            if sym not in self._highest_pnl:
                self._highest_pnl[sym] = 0.0

            # ── FIX 5: Track MFE as raw percentage (not R-multiple) ──
            # This gives a universal measure of trade strength regardless of SL width.
            # SPACEUSDT peaked at +13.8% MFE but was killed by time_exit_6h
            # because peak R was 0. MFE% catches this.
            if side == "LONG" and entry > 0:
                current_mfe_pct = (price - entry) / entry * 100
            elif side == "SHORT" and entry > 0:
                current_mfe_pct = (entry - price) / entry * 100
            else:
                current_mfe_pct = 0.0
            prev_mfe = self._mfe_pct.get(sym, 0)
            if current_mfe_pct > prev_mfe:
                self._mfe_pct[sym] = current_mfe_pct
                prev_mfe = current_mfe_pct
            # FIX: Always ensure symbol is tracked (even at 0) so state persistence saves it
            if sym not in self._mfe_pct:
                self._mfe_pct[sym] = 0.0

            # ═══════════════════════════════════════════════════════════════
            # v6: PROGRESSIVE BREAKEVEN — at +1R (was +0.3% MFE)
            # Executive Assessment Problem 1: Trailing activates too early
            # Root cause: 0.3% MFE breakeven triggered too fast, didn't let
            # trades breathe. Moved to +1R (standard risk unit).
            # ═══════════════════════════════════════════════════════════════
            if prev_peak >= 1.0 and sym not in self._breached_breakeven:
                # Move SL to entry (breakeven) — risk-free trade
                fee_buffer = entry * 0.0004  # taker fee buffer
                if side == "LONG":
                    new_sl = entry + fee_buffer
                    if new_sl > position.get("stop_loss", 0):
                        position["stop_loss"] = new_sl
                        self._breached_breakeven.add(sym)
                        logger.info("🔒 {} BREAKEVEN: {} → {} (peak={:.2f}R >= 1.0R)", side, sym, new_sl, prev_peak)
                elif side == "SHORT":
                    new_sl = entry - fee_buffer
                    if new_sl < position.get("stop_loss", float('inf')):
                        position["stop_loss"] = new_sl
                        self._breached_breakeven.add(sym)
                        logger.info("🔒 {} BREAKEVEN: {} → {} (peak={:.2f}R >= 1.0R)", side, sym, new_sl, prev_peak)

            # ═══════════════════════════════════════════════════════════════
            # v6: PROGRESSIVE TRAILING STOP (Executive Assessment Problem 1)
            # KEY CHANGE: Delayed activation with progressive tightening
            #   Stage 0: No trailing before +1R (let trade breathe)
            #   Stage 1: Trail at 1.0R below peak (1R–2R zone)
            #   Stage 2: Trail at 0.75R below peak (2R–3R zone)
            #   Stage 3: Trail at 0.5R below peak (>3R zone, aggressive lock)
            # ═══════════════════════════════════════════════════════════════
            if prev_peak >= 1.0:  # TRAIL_ACTIVATION_R
                # Determine trail stage based on peak R
                if prev_peak >= 3.0:
                    trail_stage = 3
                    trail_dist_r = 0.5    # Aggressive lock
                elif prev_peak >= 2.0:
                    trail_stage = 2
                    trail_dist_r = 0.75   # Tight trail
                else:
                    trail_stage = 1
                    trail_dist_r = 1.0    # Standard trail

                trail_dist_price = risk_per_unit * trail_dist_r

                if side == "LONG":
                    trail_sl = price - trail_dist_price
                    new_sl = max(position.get("stop_loss", entry), trail_sl)
                else:
                    trail_sl = price + trail_dist_price
                    new_sl = min(position.get("stop_loss", entry), trail_sl)

                if new_sl != position.get("stop_loss", 0):
                    position["stop_loss"] = new_sl
                    logger.info("🛡️  v6_TRAIL_S{}: {} {} → {:.4f} (peak={:.2f}R, trail={}R)", trail_stage, side, sym, new_sl, prev_peak, trail_dist_r)

                # Check if trailing SL hit
                if side == "LONG" and price <= position.get("stop_loss", 0):
                    return True, "trailing_stop"
                elif side == "SHORT" and price >= position.get("stop_loss", 0):
                    return True, "trailing_stop"

            # ═══════════════════════════════════════════════════════════════
            # v6: DYNAMIC TIME STOP (Executive Assessment Problem 7)
            # 45-90 min AND PnL ≈ 0 AND momentum decreasing → exit
            # Skip if MFE > 5% (trade showed strength)
            # Hard max 24h → always exit
            # ═══════════════════════════════════════════════════════════════
            if opened_at > 0:
                hold_hours = (time.time() - opened_at) / 3600
                hold_minutes = hold_hours * 60

                # 1. Hard maximum: 24 hours — no exceptions
                if hold_hours >= 24:
                    return True, "max_hold_24h"

                # 2. Dynamic no-progress exit: 45-90 minutes
                # Only exit if: PnL ≈ 0 AND momentum decreasing
                if 45 <= hold_minutes <= 180:
                    tp_idx = position.get("current_tp_index", 1)
                    if tp_idx == 1:  # Still on TP1 = no progress
                        # Skip if MFE > 5% (trade showed strength)
                        if prev_mfe >= 5.0:
                            pass  # Let it ride
                        elif unrealized_r <= 0.3:
                            # Check momentum decreasing (CVD + OI declining)
                            cvd_val = signal.get("cvd", 0) if signal else 0
                            oi_val = signal.get("oi_change", signal.get("oi_delta", 0)) if signal else 0
                            momentum_fading = cvd_val < -0.1 or oi_val < -0.1
                            if momentum_fading:
                                return True, "no_progress_stagnant"

                # 3. Legacy: 6-hour no-progress (backup for edge cases)
                if tp_idx == 1 and hold_hours >= 6:
                    if prev_mfe < 0.5:
                        return True, "no_progress_6h"

            # ═══════════════════════════════════════════════════════════════
            # v6: TP2+ backup trailing (safety net)
            # Primary trailing is the progressive trail above
            # ═══════════════════════════════════════════════════════════════
            tp_idx = position.get("current_tp_index", 1)
            if tp_idx >= 2:
                # TP1 already hit → additional trailing at 75% of peak R
                if prev_peak >= 1.0:
                    trail_r = prev_peak * 0.75
                    if unrealized_r <= trail_r:
                        return True, f"trailing_stop_r (peak={prev_peak:.1f}R, current={unrealized_r:.1f}R)"

            # --- 3. Multi-target exits: check TP1, TP2, TP3 sequentially ---
            tp1 = position.get("take_profit_1", position.get("take_profit", 0))
            tp2 = position.get("take_profit_2", 0)
            tp3 = position.get("take_profit_3", 0)
            tp_idx = position.get("current_tp_index", 1)
            tp1_hit = position.get("_tp1_hit", False)
            tp2_hit = position.get("_tp2_hit", False)

            # TP1 hit → partial close, advance to TP2
            if tp1 > 0 and not tp1_hit:
                if (side == "LONG" and price >= tp1) or (side == "SHORT" and price <= tp1):
                    position["_tp1_hit"] = True
                    position["current_tp_index"] = 2
                    return True, "take_profit_1"

            # TP2 hit → partial close, advance to TP3
            if tp2 > 0 and not tp2_hit:
                if (side == "LONG" and price >= tp2) or (side == "SHORT" and price <= tp2):
                    position["_tp2_hit"] = True
                    position["current_tp_index"] = 3
                    return True, "take_profit_2"

            # TP3 hit → full close
            if tp3 > 0:
                if (side == "LONG" and price >= tp3) or (side == "SHORT" and price <= tp3):
                    return True, "take_profit_3"

            # Fallback: legacy single TP
            if not tp1 and not tp2 and not tp3:
                pass  # Fall through to standard SL/TP check below

            # --- Standard SL/TP ---
            if side == "LONG":
                if price <= sl:
                    return True, "stop_loss"
                if price >= tp:
                    return True, "take_profit"
            elif side == "SHORT":
                if price >= sl:
                    return True, "stop_loss"
                if price <= tp:
                    return True, "take_profit"

        return False, ""

    def cleanup_position_state(self, sym: str) -> None:
        """Clean up trailing stop state when a position is fully closed."""
        self._highest_pnl.pop(sym, None)
        self._mfe_pct.pop(sym, None)
        self._breached_breakeven.discard(sym)
        self._partials_taken.discard(sym)

    def calculate_pnl(self, position: Dict, exit_price: float) -> float:
        """Calculate PnL with taker fees + estimated funding cost."""
        entry = position.get("entry_price", 0)
        qty = position.get("quantity", 0)
        lev = position.get("leverage", 1)
        side = position.get("side", "")
        opened_at = position.get("opened_at", 0)
        # For partial exits, use partial quantity if provided
        exit_qty = position.get("_exit_qty", qty)

        pnl = (exit_price - entry) * exit_qty if side == "LONG" else (entry - exit_price) * exit_qty
        # Taker fees (entry + exit) — fees are on position value, not leveraged
        fees = entry * exit_qty * 0.0004 + exit_price * exit_qty * 0.0004
        # Estimated funding cost: avg 0.01% per 8h, proportional to hold time
        hold_hours = (time.time() - opened_at) / 3600 if opened_at else 0
        funding_cost = entry * exit_qty * 0.0001 * (hold_hours / 8)  # 0.01% per 8h
        return round(pnl - fees - funding_cost, 2)
