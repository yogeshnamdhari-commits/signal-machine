"""
Trade Lifecycle Engine — Minimum Hold Logic + MAE/MFE Tracking.

FIX #1: Prevents premature exits that destroy profitability.

Based on forensic audit:
  - 720 trades (50%) closed <15 min → PF=0.40, PnL=-$9,118
  - Trades held 30-240 min → PF=2.60, PnL=+$5,329

Logic:
  IF profit < 1R AND hold_minutes < 30:
    BLOCK discretionary exit (only allow hard SL)
  IF profit >= 1R OR hold_minutes >= 30:
    Allow normal exit management
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from loguru import logger


@dataclass
class LifecycleState:
    """State for a single position's lifecycle."""
    symbol: str
    side: str
    entry_price: float
    entry_time: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    risk_distance: float  # |entry - SL|
    # Tracking
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_r: float = 0.0
    hold_minutes: float = 0.0
    mae_pct: float = 0.0
    mfe_pct: float = 0.0
    mae_price: float = 0.0
    mfe_price: float = 0.0
    # Lifecycle rules
    min_hold_minutes: float = 30.0
    min_r_for_exit: float = 1.0
    hard_sl_active: bool = True


class TradeLifecycleEngine:
    """
    FIX #1: Trade Lifecycle Management.
    
    Prevents premature exits that destroy profitability.
    Based on forensic audit of 1,436 completed trades.
    """
    
    # ── Configuration (from forensic audit data) ──
    MIN_HOLD_MINUTES = 20.0      # Phase 14: >=20min PF=1.23, PnL=+$4,085 (670 trades, 45% retained)
                                  # vs 45min: PF=1.21, PnL=+$2,843 (419 trades, 28% retained)
                                  # 20min balances quality + frequency per Rule #1 (no zero flow)
    MIN_R_FOR_DISCRETIONARY = 1.0  # Don't exit until at least 1R profit
    MAX_HOLD_MINUTES = 120.0     # SQL PROOF: 30-120m PF=2.30, >2h PF=0.38
    HARD_SL_ALWAYS_ACTIVE = True  # Stop loss always honored
    
    def __init__(self) -> None:
        self._positions: Dict[str, LifecycleState] = {}
        self._closed: List[Dict] = []
    
    def register_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        risk_reward: float,
    ) -> None:
        """Register a new position for lifecycle tracking."""
        risk_distance = abs(entry_price - stop_loss)
        if risk_distance <= 0:
            risk_distance = entry_price * 0.02  # Fallback: 2%
        
        self._positions[symbol] = LifecycleState(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            entry_time=time.time(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward=risk_reward,
            risk_distance=risk_distance,
        )
        logger.info("🔄 LIFECYCLE REGISTERED: {} {} @ {} SL={} RR={:.1f} min_hold={}m",
                     side, symbol, entry_price, stop_loss, risk_reward, self.MIN_HOLD_MINUTES)
    
    def update_price(self, symbol: str, current_price: float) -> Dict:
        """Update current price and return lifecycle status."""
        if symbol not in self._positions:
            return {"action": "none", "reason": "not_tracked"}
        
        pos = self._positions[symbol]
        pos.current_price = current_price
        pos.hold_minutes = (time.time() - pos.entry_time) / 60.0
        
        # Calculate unrealized R
        if pos.risk_distance > 0:
            if pos.side == "LONG":
                price_diff = current_price - pos.entry_price
            else:
                price_diff = pos.entry_price - current_price
            pos.unrealized_r = price_diff / pos.risk_distance
        else:
            pos.unrealized_r = 0.0
        
        pos.unrealized_pnl = price_diff * (1 if pos.side == "LONG" else -1)
        
        # Track MAE/MFE
        if pos.side == "LONG":
            if current_price > pos.mfe_price or pos.mfe_price == 0:
                pos.mfe_price = current_price
            if current_price < pos.mae_price or pos.mae_price == 0:
                pos.mae_price = current_price
        else:
            if current_price < pos.mfe_price or pos.mfe_price == 0:
                pos.mfe_price = current_price
            if current_price > pos.mae_price or pos.mae_price == 0:
                pos.mae_price = current_price
        
        # MAE/MFE as percentage
        pos.mae_pct = abs(pos.entry_price - pos.mae_price) / pos.entry_price * 100
        pos.mfe_pct = abs(pos.mfe_price - pos.entry_price) / pos.entry_price * 100
        
        # ── LIFECYCLE DECISION ──
        return self._evaluate_exit(symbol)
    
    def _evaluate_exit(self, symbol: str) -> Dict:
        """Evaluate whether exit should be allowed or blocked."""
        pos = self._positions[symbol]
        
        # HARD STOP LOSS — always allowed
        if pos.hard_sl_active:
            if pos.side == "LONG" and pos.current_price <= pos.stop_loss:
                return {"action": "exit", "reason": "hard_sl", "exit_type": "stop_loss"}
            elif pos.side == "SHORT" and pos.current_price >= pos.stop_loss:
                return {"action": "exit", "reason": "hard_sl", "exit_type": "stop_loss"}
        
        # TAKE PROFIT — always allowed
        if pos.side == "LONG" and pos.current_price >= pos.take_profit:
            return {"action": "exit", "reason": "take_profit", "exit_type": "tp"}
        elif pos.side == "SHORT" and pos.current_price <= pos.take_profit:
            return {"action": "exit", "reason": "take_profit", "exit_type": "tp"}
        
        # ── MINIMUM HOLD CHECK ──
        in_minimum_hold = pos.hold_minutes < self.MIN_HOLD_MINUTES
        below_min_r = pos.unrealized_r < self.MIN_R_FOR_DISCRETIONARY
        
        if in_minimum_hold and below_min_r:
            # BLOCK discretionary exit — trade hasn't developed yet
            return {
                "action": "hold",
                "reason": f"minimum_hold ({pos.hold_minutes:.0f}m < {self.MIN_HOLD_MINUTES}m) AND R={pos.unrealized_r:.2f} < {self.MIN_R_FOR_DISCRETIONARY}",
                "hold_minutes": pos.hold_minutes,
                "unrealized_r": pos.unrealized_r,
            }
        
        # ── MAXIMUM HOLD CHECK ──
        # FIX 5: Skip max_hold_stale if MFE > 5% — trade showed directional strength
        # This prevents the lifecycle engine from killing trades that had strong moves
        # (like SPACEUSDT which peaked at +13.8% MFE but was exited at -$24).
        if pos.hold_minutes > self.MAX_HOLD_MINUTES and pos.unrealized_r < 0.5:
            if pos.mfe_pct < 5.0:
                return {
                    "action": "exit",
                    "reason": f"max_hold_stale ({pos.hold_minutes:.0f}m, R={pos.unrealized_r:.2f})",
                    "exit_type": "timeout",
                }
            else:
                logger.info("⏰ MAX_HOLD_SKIPPED: {} MFE={:.1f}% > 5% — trade showed strength",
                           pos.symbol, pos.mfe_pct)
        
        # ── TRAILING STOP ZONE ──
        # If in profit and past minimum hold, allow exit
        if pos.unrealized_r >= 1.0 and pos.hold_minutes >= self.MIN_HOLD_MINUTES:
            return {
                "action": "allow_exit",
                "reason": f"developed (R={pos.unrealized_r:.2f}, hold={pos.hold_minutes:.0f}m)",
                "unrealized_r": pos.unrealized_r,
                "hold_minutes": pos.hold_minutes,
            }
        
        # Default: hold
        return {
            "action": "hold",
            "reason": f"developing (R={pos.unrealized_r:.2f}, hold={pos.hold_minutes:.0f}m)",
            "unrealized_r": pos.unrealized_r,
            "hold_minutes": pos.hold_minutes,
        }
    
    def close_position(self, symbol: str, exit_price: float, exit_reason: str) -> Dict:
        """Close position and return full lifecycle data."""
        if symbol not in self._positions:
            return {}
        
        pos = self._positions.pop(symbol)
        hold_minutes = (time.time() - pos.entry_time) / 60.0
        
        # Final PnL calculation
        if pos.side == "LONG":
            pnl = (exit_price - pos.entry_price)
        else:
            pnl = (pos.entry_price - exit_price)
        
        realized_r = pnl / pos.risk_distance if pos.risk_distance > 0 else 0
        
        result = {
            "symbol": symbol,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "stop_loss": pos.stop_loss,
            "take_profit": pos.take_profit,
            "hold_minutes": round(hold_minutes, 1),
            "mae_pct": round(pos.mae_pct, 4),
            "mfe_pct": round(pos.mfe_pct, 4),
            "realized_r": round(realized_r, 2),
            "exit_reason": exit_reason,
            "was_in_min_hold": hold_minutes < self.MIN_HOLD_MINUTES,
        }
        
        self._closed.append(result)
        
        logger.info("📊 LIFECYCLE CLOSED: {} {} | Hold={:.0f}m MAE={:.2f}% MFE={:.2f}% R={:.2f} | {}",
                     pos.side, symbol, hold_minutes, pos.mae_pct, pos.mfe_pct, realized_r, exit_reason)
        
        return result
    
    def get_position_count(self) -> int:
        return len(self._positions)
    
    def get_closed_trades(self) -> List[Dict]:
        return list(self._closed)
    
    def get_status(self) -> Dict:
        """Get lifecycle engine status for dashboard."""
        active = {}
        for sym, pos in self._positions.items():
            active[sym] = {
                "hold_minutes": round(pos.hold_minutes, 1),
                "unrealized_r": round(pos.unrealized_r, 2),
                "mae_pct": round(pos.mae_pct, 4),
                "mfe_pct": round(pos.mfe_pct, 4),
                "in_min_hold": pos.hold_minutes < self.MIN_HOLD_MINUTES,
            }
        return {
            "active_positions": len(active),
            "total_closed": len(self._closed),
            "min_hold_minutes": self.MIN_HOLD_MINUTES,
            "positions": active,
        }
