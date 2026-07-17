"""
TP/SL Engine — Real-time stop loss and take profit monitoring with MAE/MFE.

This engine monitors price movements for open positions and:
  1. Tracks MAE (Maximum Adverse Excursion) in real-time
  2. Tracks MFE (Maximum Favourable Excursion) in real-time
  3. Computes risk-adjusted metrics (R-multiple, reward:risk utilised)
  4. Detects when price approaches SL/TP levels (proximity alerts)
  5. Provides live excursion data for the dashboard

The TP/SLEngine feeds into TradeEngine (which stores the final values)
and also provides real-time data for the dashboard's live position cards.

Usage::

    ts = TPSLEngine()
    ts.register_position(sym, side, entry, sl, tp, qty)
    result = ts.update(sym, current_price)
    # result = {
    #     "mae": 0.5, "mae_pct": 0.3, "mae_r": -0.8,
    #     "mfe": 2.1, "mfe_pct": 1.2, "mfe_r": 3.5,
    #     "current_r": 1.2,
    #     "sl_distance_pct": 0.5,
    #     "tp_distance_pct": 1.2,
    #     "sl_proximity": "safe",
    #     "tp_proximity": "approaching",
    # }
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
from loguru import logger


# Proximity thresholds (as % of entry price)
_SL_PROXIMITY_DANGER = 0.002   # within 0.2% of SL = danger
_SL_PROXIMITY_WARN = 0.005     # within 0.5% of SL = warning
_TP_PROXIMITY_CLOSE = 0.005    # within 0.5% of TP = approaching
_TP_PROXIMITY_VERY_CLOSE = 0.002  # within 0.2% of TP = almost there


@dataclass
class _PositionState:
    """Running state for a monitored position."""
    symbol: str = ""
    side: str = ""
    entry_price: float = 0
    stop_loss: float = 0
    take_profit: float = 0
    # Multi-target system
    take_profit_1: float = 0
    take_profit_2: float = 0
    take_profit_3: float = 0
    current_tp_index: int = 1  # Which TP is active (1, 2, or 3)
    tp1_exit_pct: float = 0.30  # Phase 8: Close 30% at TP1 (1R)
    tp2_exit_pct: float = 0.40  # Phase 8: Close 40% at TP2 (3R)
    tp3_exit_pct: float = 0.30  # Phase 8: Close 30% at TP3 (5R)
    trailing_activation: float = 3.0  # Phase 8: Trail after TP2 (3R)
    breakeven_activation: float = 1.0  # Phase 8: Move to BE after TP1 (1R)
    quantity: float = 0
    # Risk distance (entry to SL)
    risk_per_unit: float = 0
    # Excursion tracking
    mae: float = 0              # worst adverse price delta
    mae_pct: float = 0          # worst adverse % of entry
    mae_r: float = 0            # worst adverse in R-multiples
    mfe: float = 0              # best favourable price delta
    mfe_pct: float = 0          # best favourable % of entry
    mfe_r: float = 0            # best favourable in R-multiples
    # Current state
    current_price: float = 0
    current_r: float = 0        # current R-multiple
    # Price extremes
    peak_price: float = 0       # highest seen
    trough_price: float = 0     # lowest seen
    # Timing
    registered_at: float = 0
    last_update: float = 0
    # Tick count
    tick_count: int = 0


class TPSLEngine:
    """
    Real-time SL/TP monitoring with MAE/MFE tracking.

    Provides both final values (for trade records) and live data
    (for dashboard display of open positions).
    """

    def __init__(self) -> None:
        self._positions: Dict[str, _PositionState] = {}

    def register_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        take_profit: float,
        quantity: float = 0,
        take_profit_2: float = 0,
        take_profit_3: float = 0,
        tp1_exit_pct: float = 0.30,
        tp2_exit_pct: float = 0.40,
        tp3_exit_pct: float = 0.30,
        trailing_activation: float = 3.0,
        breakeven_activation: float = 1.0,
    ) -> None:
        """Register a position for real-time monitoring."""
        # Compute risk per unit
        if side == "LONG":
            risk = max(abs(entry_price - stop_loss), entry_price * 0.002)
        else:
            risk = max(abs(stop_loss - entry_price), entry_price * 0.002)

        ps = _PositionState(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            take_profit_1=take_profit,
            take_profit_2=take_profit_2,
            take_profit_3=take_profit_3,
            tp1_exit_pct=tp1_exit_pct,
            tp2_exit_pct=tp2_exit_pct,
            tp3_exit_pct=tp3_exit_pct,
            trailing_activation=trailing_activation,
            breakeven_activation=breakeven_activation,
            quantity=quantity,
            risk_per_unit=risk,
            registered_at=time.time(),
            current_price=entry_price,
            peak_price=entry_price,
            trough_price=entry_price,
        )
        self._positions[symbol] = ps
        logger.debug(
            "🎯 TP/SL ENGINE: registered {} {} entry={} sl={} tp={} risk={:.4f}",
            side, symbol, entry_price, stop_loss, take_profit, risk,
        )

    def update(self, symbol: str, price: float) -> Dict:
        """
        Update with current price. Returns live excursion data.

        Call this on every tick for each open position.
        Returns a dict with MAE/MFE/R-multiple/proximity data.
        """
        ps = self._positions.get(symbol)
        if not ps or price <= 0:
            return {}

        ps.current_price = price
        ps.last_update = time.time()
        ps.tick_count += 1

        # ── MAE / MFE computation ──
        if ps.side == "LONG":
            adverse = ps.entry_price - price     # positive = price dropped (adverse)
            favourable = price - ps.entry_price  # positive = price rose (favourable)
        else:
            adverse = price - ps.entry_price     # positive = price rose (adverse for short)
            favourable = ps.entry_price - price  # positive = price dropped (favourable for short)

        # Update MAE
        if adverse > ps.mae:
            ps.mae = adverse
            ps.mae_pct = adverse / ps.entry_price * 100 if ps.entry_price > 0 else 0
            ps.mae_r = adverse / ps.risk_per_unit if ps.risk_per_unit > 0 else 0

        # Update MFE
        if favourable > ps.mfe:
            ps.mfe = favourable
            ps.mfe_pct = favourable / ps.entry_price * 100 if ps.entry_price > 0 else 0
            ps.mfe_r = favourable / ps.risk_per_unit if ps.risk_per_unit > 0 else 0

        # Current R-multiple
        if ps.side == "LONG":
            ps.current_r = (price - ps.entry_price) / ps.risk_per_unit if ps.risk_per_unit > 0 else 0
        else:
            ps.current_r = (ps.entry_price - price) / ps.risk_per_unit if ps.risk_per_unit > 0 else 0

        # Price extremes
        if price > ps.peak_price:
            ps.peak_price = price
        if price < ps.trough_price:
            ps.trough_price = price

        # ── SL/TP proximity ──
        sl_dist_pct = abs(price - ps.stop_loss) / ps.entry_price if ps.entry_price > 0 else 1
        tp_dist_pct = abs(ps.take_profit - price) / ps.entry_price if ps.entry_price > 0 else 1

        if sl_dist_pct <= _SL_PROXIMITY_DANGER:
            sl_proximity = "danger"
        elif sl_dist_pct <= _SL_PROXIMITY_WARN:
            sl_proximity = "warning"
        else:
            sl_proximity = "safe"

        if tp_dist_pct <= _TP_PROXIMITY_VERY_CLOSE:
            tp_proximity = "almost_there"
        elif tp_dist_pct <= _TP_PROXIMITY_CLOSE:
            tp_proximity = "approaching"
        else:
            tp_proximity = "far"

        return {
            "symbol": symbol,
            "side": ps.side,
            # MAE
            "mae": round(ps.mae, 6),
            "mae_pct": round(ps.mae_pct, 4),
            "mae_r": round(ps.mae_r, 2),
            # MFE
            "mfe": round(ps.mfe, 6),
            "mfe_pct": round(ps.mfe_pct, 4),
            "mfe_r": round(ps.mfe_r, 2),
            # Current
            "current_r": round(ps.current_r, 2),
            "current_price": price,
            "entry_price": ps.entry_price,
            # Multi-target
            "take_profit_1": ps.take_profit_1,
            "take_profit_2": ps.take_profit_2,
            "take_profit_3": ps.take_profit_3,
            "current_tp_index": ps.current_tp_index,
            "active_tp": self._get_active_tp(ps),
            # Distances
            "sl_distance_pct": round(sl_dist_pct * 100, 3),
            "tp_distance_pct": round(tp_dist_pct * 100, 3),
            # Proximity alerts
            "sl_proximity": sl_proximity,
            "tp_proximity": tp_proximity,
            # Price extremes
            "peak_price": ps.peak_price,
            "trough_price": ps.trough_price,
            # Meta
            "tick_count": ps.tick_count,
            "holding_seconds": round(time.time() - ps.registered_at, 1),
        }

    def get_excursion_summary(self, symbol: str) -> Dict:
        """Get current MAE/MFE summary without updating."""
        ps = self._positions.get(symbol)
        if not ps:
            return {}
        return {
            "mae": round(ps.mae, 6),
            "mae_pct": round(ps.mae_pct, 4),
            "mae_r": round(ps.mae_r, 2),
            "mfe": round(ps.mfe, 6),
            "mfe_pct": round(ps.mfe_pct, 4),
            "mfe_r": round(ps.mfe_r, 2),
            "current_r": round(ps.current_r, 2),
            "tick_count": ps.tick_count,
        }

    def _get_active_tp(self, ps: _PositionState) -> float:
        """Return the currently active take-profit price based on TP index."""
        if ps.current_tp_index == 1:
            return ps.take_profit_1
        elif ps.current_tp_index == 2:
            return ps.take_profit_2
        elif ps.current_tp_index == 3:
            return ps.take_profit_3
        return ps.take_profit_1  # Default to TP1

    def deregister_position(self, symbol: str) -> Optional[Dict]:
        """Remove a position and return final excursion data."""
        ps = self._positions.pop(symbol, None)
        if not ps:
            return None
        return {
            "mae": round(ps.mae, 6),
            "mae_pct": round(ps.mae_pct, 4),
            "mae_r": round(ps.mae_r, 2),
            "mfe": round(ps.mfe, 6),
            "mfe_pct": round(ps.mfe_pct, 4),
            "mfe_r": round(ps.mfe_r, 2),
            "peak_price": ps.peak_price,
            "trough_price": ps.trough_price,
            "tick_count": ps.tick_count,
        }

    @property
    def monitored_count(self) -> int:
        return len(self._positions)

    def get_all_excursions(self) -> Dict[str, Dict]:
        """Get excursion data for all monitored positions."""
        return {sym: self.get_excursion_summary(sym) for sym in self._positions}
