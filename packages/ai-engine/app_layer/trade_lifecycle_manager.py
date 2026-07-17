"""
Trade Lifecycle Manager — Full trade lifecycle orchestration.

Per Executive Assessment Problem 11:
    Current: Entry → Exit
    Need:    Entry → Initial Risk → Breakeven → Partial Profit
             → Profit Lock → Trend Ride → Exit

Integrates:
    - ProfitProtectionEngine (progressive profit lock)
    - TradeAnalyticsEngine (MFE/MAE tracking, exit quality)
    - ExitEngine (dynamic exits)
    - Adaptive take-profit (Problem 6)
    - Time stop (Problem 7)

This is the single orchestrator for all trade-management decisions.

READ-ONLY: Returns lifecycle decisions for execution layer to act on.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from .profit_protection import ProfitProtectionEngine, ProtectionDecision
from .trade_analytics import TradeAnalyticsEngine, TradeExcursion, ExitQualityAssessment
from .exit_engine import AppExitEngine, ExitDecision


@dataclass
class LifecycleDecision:
    """Combined decision from all trade management engines."""
    symbol: str = ""
    action: str = "HOLD"          # HOLD / MODIFY_SL / TAKE_PARTIAL / EXIT
    reason: str = ""
    exit_reason: str = ""
    new_sl: float = 0.0
    exit_quantity: float = 0.0
    urgency: str = "normal"       # normal / high / critical

    # Protection state
    protection_state: str = ""
    locked_r: float = 0.0

    # Analytics
    current_r: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    efficiency_pct: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "action": self.action,
            "reason": self.reason,
            "exit_reason": self.exit_reason,
            "new_sl": round(self.new_sl, 6),
            "exit_quantity": round(self.exit_quantity, 6),
            "urgency": self.urgency,
            "protection_state": self.protection_state,
            "locked_r": round(self.locked_r, 3),
            "current_r": round(self.current_r, 3),
            "mfe_r": round(self.mfe_r, 3),
            "mae_r": round(self.mae_r, 3),
            "efficiency_pct": round(self.efficiency_pct, 1),
        }


# ═══════════════════════════════════════════════════════════════
# ADAPTIVE TAKE-PROFIT CONFIGURATION (Problem 6)
# ═══════════════════════════════════════════════════════════════

# TP as R-multiple — adapts to volatility/regime
ADAPTIVE_TP = {
    "trending":   {"tp_r": 5.0, "tp2_r": 3.0, "tp3_r": 8.0},
    "momentum":   {"tp_r": 4.0, "tp2_r": 2.5, "tp3_r": 6.0},
    "normal":     {"tp_r": 3.0, "tp2_r": 2.0, "tp3_r": 4.5},
    "range":      {"tp_r": 2.0, "tp2_r": 1.5, "tp3_r": 3.0},
    "compression": {"tp_r": 1.5, "tp2_r": 1.0, "tp3_r": 2.5},
    "unknown":    {"tp_r": 2.5, "tp2_r": 1.8, "tp3_r": 4.0},
}


class TradeLifecycleManager:
    """
    Orchestrates the complete trade lifecycle:
        Entry → Initial Risk → Breakeven → Partial Profit
        → Profit Lock → Trend Ride → Exit

    Integrates ProfitProtection, TradeAnalytics, and ExitEngine.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self) -> None:
        self.protection = ProfitProtectionEngine()
        self.analytics = TradeAnalyticsEngine()
        self.exit_engine = AppExitEngine()
        self._active_trades: Dict[str, Dict] = {}

    # ── Lifecycle Registration ────────────────────────────────

    def register_trade(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        quantity: float,
        regime: str = "unknown",
        entry_time: Optional[float] = None,
    ) -> None:
        """Register a new trade for full lifecycle management."""
        risk = max(abs(entry_price - stop_loss), entry_price * 0.002)

        # Register with all sub-engines
        self.protection.register_position(symbol, side, entry_price, stop_loss)
        self.analytics.register_position(symbol, side, entry_price, entry_time, risk)
        self.exit_engine.register_position(symbol, side, entry_price, stop_loss, take_profit, quantity, entry_time)

        # Track locally
        self._active_trades[symbol] = {
            "side": side,
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "quantity": quantity,
            "regime": regime,
            "entry_time": entry_time or time.time(),
            "risk_per_unit": risk,
        }

        logger.info(
            "📊 LIFECYCLE_REGISTERED: {} {} @ {} SL={} TP={} qty={} regime={}",
            side, symbol, entry_price, stop_loss, take_profit, quantity, regime,
        )

    # ── Lifecycle Evaluation ──────────────────────────────────

    def evaluate(
        self,
        symbol: str,
        current_price: float,
        signal: Optional[Dict] = None,
        current_atr: float = 0.0,
    ) -> LifecycleDecision:
        """
        Evaluate all trade management conditions for a position.

        Priority order:
            1. Hard stop loss (immediate exit)
            2. Profit protection (progressive lock)
            3. Exit engine (trailing, breakeven, partial, time, momentum)
            4. Adaptive take-profit

        Args:
            symbol: Position symbol
            current_price: Current market price
            signal: Optional signal data for momentum/institutional checks
            current_atr: Current ATR value for adaptive TP

        Returns:
            LifecycleDecision with action to take
        """
        trade = self._active_trades.get(symbol)
        if not trade:
            return LifecycleDecision(symbol=symbol, action="HOLD", reason="not_registered")

        decision = LifecycleDecision(symbol=symbol)

        # ── Update analytics (MFE/MAE tracking) ──
        excursion = self.analytics.update_price(symbol, current_price)
        if excursion:
            decision.current_r = excursion.current_pnl_r
            decision.mfe_r = excursion.max_favorable_r
            decision.mae_r = excursion.max_adverse_r
            decision.efficiency_pct = excursion.get_efficiency()

        # ── 1. Profit Protection (highest priority after hard SL) ──
        protection_decision = self.protection.evaluate(symbol, current_price, current_atr)
        decision.protection_state = protection_decision.new_state
        decision.locked_r = protection_decision.locked_r

        if protection_decision.action == "EXIT":
            decision.action = "EXIT"
            decision.exit_reason = protection_decision.reason
            decision.urgency = "high"
            decision.new_sl = protection_decision.new_sl
            decision.reason = f"profit_protection_exit: {protection_decision.reason}"
            return decision

        if protection_decision.action == "MODIFY_SL":
            decision.action = "MODIFY_SL"
            decision.new_sl = protection_decision.new_sl
            decision.reason = protection_decision.reason
            # Continue to check if exit engine also wants to act

        # ── 2. Exit Engine (trailing, breakeven, partial, time, momentum) ──
        exit_decision = self.exit_engine.evaluate(symbol, current_price, signal)

        if exit_decision.action == "EXIT":
            decision.action = "EXIT"
            decision.exit_reason = exit_decision.exit_reason
            decision.exit_quantity = exit_decision.exit_quantity
            decision.urgency = exit_decision.urgency
            decision.reason = f"exit_engine: {exit_decision.reason}"
            return decision

        if exit_decision.action == "MODIFY_SL" and decision.action != "MODIFY_SL":
            # Only use exit engine SL modification if profit protection didn't already set one
            decision.action = "MODIFY_SL"
            decision.new_sl = exit_decision.new_sl
            decision.reason = exit_decision.reason

        if exit_decision.action == "TAKE_PARTIAL":
            decision.action = "TAKE_PARTIAL"
            decision.exit_quantity = exit_decision.exit_quantity
            decision.exit_reason = exit_decision.exit_reason
            decision.reason = exit_decision.reason

        return decision

    # ── Adaptive Take-Profit ──────────────────────────────────

    def calculate_adaptive_tp(
        self,
        entry_price: float,
        stop_loss: float,
        side: str,
        regime: str = "unknown",
        current_atr: float = 0.0,
        confidence: float = 85.0,
    ) -> Dict[str, float]:
        """
        Calculate adaptive take-profit levels based on regime and ATR.

        Problem 6: Same TP for every trade → Need adaptive TP.

        High ATR → TP = 4-5R
        Low ATR  → TP = 2R
        Range    → TP = 1.5R
        Trend    → TP = 5R

        Args:
            entry_price: Entry price
            stop_loss: Stop loss price
            side: LONG or SHORT
            regime: Market regime
            current_atr: Current ATR value
            confidence: Signal confidence (0-100)

        Returns:
            Dict with take_profit_1, take_profit_2, take_profit_3
        """
        risk = max(abs(entry_price - stop_loss), entry_price * 0.002)

        # Get regime-based TP levels
        tp_config = ADAPTIVE_TP.get(regime, ADAPTIVE_TP["unknown"])

        # Adjust TP based on ATR (if available)
        atr_mult = 1.0
        if current_atr > 0 and entry_price > 0:
            atr_pct = current_atr / entry_price * 100
            # High ATR → extend TP; Low ATR → reduce TP
            if atr_pct > 3.0:
                atr_mult = 1.3  # High volatility → wider TP
            elif atr_pct > 2.0:
                atr_mult = 1.15
            elif atr_pct < 0.5:
                atr_mult = 0.7   # Low volatility → tighter TP
            elif atr_pct < 1.0:
                atr_mult = 0.85

        # Confidence adjustment: high confidence → slightly wider TP
        conf_mult = 1.0 + max(0, (confidence - 90) / 100)

        # Final TP levels
        tp1_r = tp_config["tp_r"] * atr_mult * conf_mult
        tp2_r = tp_config["tp2_r"] * atr_mult * conf_mult
        tp3_r = tp_config["tp3_r"] * atr_mult * conf_mult

        if side == "LONG":
            tp1 = entry_price + risk * tp1_r
            tp2 = entry_price + risk * tp2_r
            tp3 = entry_price + risk * tp3_r
        else:
            tp1 = entry_price - risk * tp1_r
            tp2 = entry_price - risk * tp2_r
            tp3 = entry_price - risk * tp3_r

        return {
            "take_profit_1": round(tp1, 6),
            "take_profit_2": round(tp2, 6),
            "take_profit_3": round(tp3, 6),
            "tp1_r": round(tp1_r, 2),
            "tp2_r": round(tp2_r, 2),
            "tp3_r": round(tp3_r, 2),
            "regime": regime,
            "atr_mult": round(atr_mult, 2),
            "conf_mult": round(conf_mult, 2),
        }

    # ── Exit Quality Assessment ───────────────────────────────

    def assess_exit(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str,
        momentum_at_exit: float = 0.0,
        cvd_at_exit: float = 0.0,
        oi_change_at_exit: float = 0.0,
    ) -> ExitQualityAssessment:
        """Assess exit quality after a trade closes."""
        trade = self._active_trades.get(symbol, {})
        return self.analytics.assess_exit_quality(
            symbol=symbol,
            exit_price=exit_price,
            exit_reason=exit_reason,
            side=trade.get("side", ""),
            entry_price=trade.get("entry_price", 0),
            risk_per_unit=trade.get("risk_per_unit", 0),
            momentum_at_exit=momentum_at_exit,
            cvd_at_exit=cvd_at_exit,
            oi_change_at_exit=oi_change_at_exit,
        )

    # ── Trade Closure ─────────────────────────────────────────

    def close_trade(self, symbol: str) -> Optional[Dict]:
        """
        Close a trade and collect final analytics.

        Returns:
            Dict with final excursion data and exit quality assessment
        """
        trade = self._active_trades.pop(symbol, None)
        if not trade:
            return None

        excursion = self.analytics.cleanup(symbol)
        protection_state = self.protection.get_state(symbol)
        self.protection.cleanup(symbol)

        result = {
            "symbol": symbol,
            "side": trade.get("side", ""),
            "entry_price": trade.get("entry_price", 0),
            "regime": trade.get("regime", "unknown"),
            "protection_state": protection_state,
        }

        if excursion:
            result["mfe_r"] = round(excursion.max_favorable_r, 3)
            result["mae_r"] = round(excursion.max_adverse_r, 3)
            result["mfe_pct"] = round(excursion.max_favorable_pct, 4)
            result["mae_pct"] = round(excursion.max_adverse_pct, 4)
            result["efficiency_pct"] = round(excursion.get_efficiency(), 1)
            result["hold_minutes"] = round(
                (time.time() - trade.get("entry_time", time.time())) / 60, 0
            )

        logger.info(
            "📊 LIFECYCLE_CLOSED: {} {} MFE={:.2f}R MAE={:.2f}R eff={:.0f}%",
            symbol, trade.get("side", ""),
            result.get("mfe_r", 0), result.get("mae_r", 0),
            result.get("efficiency_pct", 0),
        )

        return result

    # ── Capital Efficiency ────────────────────────────────────

    def rank_positions(self, current_prices: Dict[str, float]) -> List[Dict]:
        """Rank all open positions by capital efficiency."""
        positions = []
        for sym, trade in self._active_trades.items():
            positions.append({
                "symbol": sym,
                "side": trade["side"],
                "entry_price": trade["entry_price"],
                "stop_loss": trade["stop_loss"],
                "opened_at": trade["entry_time"],
            })

        ranks = self.analytics.rank_capital_efficiency(positions, current_prices)
        return [r.to_dict() for r in ranks]

    # ── Summary ───────────────────────────────────────────────

    def get_summary(self) -> Dict[str, Any]:
        """Get lifecycle summary for all active trades."""
        return {
            "active_trades": len(self._active_trades),
            "protection_states": self.protection.get_all_states(),
            "analytics": self.analytics.get_summary(),
        }
