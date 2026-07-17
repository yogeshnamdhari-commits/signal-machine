"""
Trade Engine — Full lifecycle tracking for every position.

Tracks:
  - entry_time / exit_time / holding_period
  - MAE (Maximum Adverse Excursion) — worst unrealised loss during trade
  - MFE (Maximum Favourable Excursion) — best unrealised profit during trade
  - slippage (entry_slippage, exit_slippage in bps)
  - realised_pnl, fees, funding_cost, net_pnl

The TradeEngine is the single source of truth for trade analytics.
It receives tick updates for every open position and maintains running
statistics that are written into the final trade record on close.

Usage::

    te = TradeEngine()
    te.open_position(sym, side, entry_price, signal_price, qty, ...)
    te.update_price(sym, current_price)          # called every tick
    record = te.close_position(sym, exit_price, reason)
    # record contains all analytics fields
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional
from loguru import logger


@dataclass
class _LivePosition:
    """Internal state for a tracked position."""
    symbol: str
    side: str                           # "LONG" or "SHORT"
    entry_price: float                  # actual fill price
    signal_price: float = 0             # price when signal was generated (for slippage)
    quantity: float = 0
    leverage: int = 1
    stop_loss: float = 0
    take_profit: float = 0
    # Multi-target system
    take_profit_1: float = 0
    take_profit_2: float = 0
    take_profit_3: float = 0
    current_tp_index: int = 1
    # Timing
    entry_time: float = 0               # epoch seconds — actual fill
    signal_time: float = 0              # epoch seconds — signal generated
    # Running analytics
    current_price: float = 0
    entry_slippage_bps: float = 0       # (entry - signal) / signal * 10000
    mae: float = 0                      # worst adverse move (in price delta)
    mae_pct: float = 0                  # worst adverse move (% of entry)
    mfe: float = 0                      # best favourable move (in price delta)
    mfe_pct: float = 0                  # best favourable move (% of entry)
    peak_price: float = 0               # highest price seen (for MFE)
    trough_price: float = 0             # lowest price seen (for MAE)
    _initialised: bool = False


class TradeEngine:
    """
    Tracks complete lifecycle of every position with real-time analytics.

    Call ``update_price()`` on every price tick for open positions.
    Call ``close_position()`` to finalise and get the complete trade record.
    """

    def __init__(self) -> None:
        self._positions: Dict[str, _LivePosition] = {}

    # ── Public API ───────────────────────────────────────────────

    def open_position(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        signal_price: float,
        quantity: float,
        leverage: int = 1,
        stop_loss: float = 0,
        take_profit: float = 0,
        signal_time: float = 0,
        metadata: Optional[Dict] = None,
    ) -> None:
        """Register a new position for tracking."""
        now = time.time()
        slippage_bps = 0.0
        if signal_price > 0:
            slippage_bps = (entry_price - signal_price) / signal_price * 10_000

        lp = _LivePosition(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            signal_price=signal_price,
            quantity=quantity,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            entry_time=now,
            signal_time=signal_time if signal_time > 0 else now,
            current_price=entry_price,
            entry_slippage_bps=round(slippage_bps, 2),
            peak_price=entry_price,
            trough_price=entry_price,
            _initialised=True,
        )
        self._positions[symbol] = lp
        logger.debug(
            "📈 TRADE ENGINE: tracking {} {} entry={} signal={} slippage={:.1f}bps",
            side, symbol, entry_price, signal_price, slippage_bps,
        )

    def update_price(self, symbol: str, price: float) -> None:
        """Update current price — called on every tick. Updates MAE/MFE."""
        lp = self._positions.get(symbol)
        if not lp or not lp._initialised or price <= 0:
            return

        lp.current_price = price

        # Update MAE (Maximum Adverse Excursion)
        if lp.side == "LONG":
            adverse = lp.entry_price - price   # positive = adverse
        else:
            adverse = price - lp.entry_price   # positive = adverse

        if adverse > lp.mae:
            lp.mae = adverse
            lp.mae_pct = adverse / lp.entry_price * 100 if lp.entry_price > 0 else 0

        # Update MFE (Maximum Favourable Excursion)
        if lp.side == "LONG":
            favourable = price - lp.entry_price   # positive = favourable
        else:
            favourable = lp.entry_price - price   # positive = favourable

        if favourable > lp.mfe:
            lp.mfe = favourable
            lp.mfe_pct = favourable / lp.entry_price * 100 if lp.entry_price > 0 else 0

        # Track peak/trough prices
        if price > lp.peak_price:
            lp.peak_price = price
        if price < lp.trough_price:
            lp.trough_price = price

    def close_position(self, symbol: str, exit_price: float, reason: str = "") -> Dict:
        """
        Finalise a position and return the complete trade record.

        The returned dict contains all tracked analytics:
        entry_time, exit_time, holding_period, MAE, MFE, slippage, PnL, etc.
        """
        lp = self._positions.pop(symbol, None)
        if not lp:
            return {}

        now = time.time()
        holding_period = now - lp.entry_time

        # Exit slippage (compared to last known price / theoretical fill)
        exit_slippage_bps = 0.0  # We use the actual exit price as fill

        # ═══════════════════════════════════════════════════════════════
        # FIX 11: PnL without leverage multiplication
        # Leverage is informational only — qty already reflects leveraged notional.
        # Multiplying by leverage again inflates PnL by 10x (e.g. -$33.95 vs -$3.58).
        # Canonical formula: (exit - entry) × qty for LONG, reversed for SHORT.
        # ═══════════════════════════════════════════════════════════════
        if lp.side == "LONG":
            raw_pnl = (exit_price - lp.entry_price) * lp.quantity
        else:
            raw_pnl = (lp.entry_price - exit_price) * lp.quantity

        # Fees (taker entry + taker exit)
        entry_fee = lp.entry_price * lp.quantity * 0.0004
        exit_fee = exit_price * lp.quantity * 0.0004
        total_fees = entry_fee + exit_fee

        # Funding cost estimate (0.01% per 8h)
        hold_hours = holding_period / 3600
        funding_cost = lp.entry_price * lp.quantity * 0.0001 * (hold_hours / 8)

        net_pnl = raw_pnl - total_fees - funding_cost

        record = {
            # Identity
            "symbol": symbol,
            "side": lp.side,
            # Prices
            "entry_price": lp.entry_price,
            "exit_price": exit_price,
            "signal_price": lp.signal_price,
            "peak_price": lp.peak_price,
            "trough_price": lp.trough_price,
            # Timing
            "entry_time": lp.entry_time,
            "exit_time": now,
            "signal_time": lp.signal_time,
            "holding_period": round(holding_period, 1),
            "holding_period_str": self._format_duration(holding_period),
            # Excursion (the key metrics the user asked for)
            "mae": round(lp.mae, 6),
            "mae_pct": round(lp.mae_pct, 4),
            "mfe": round(lp.mfe, 6),
            "mfe_pct": round(lp.mfe_pct, 4),
            # Slippage
            "entry_slippage_bps": lp.entry_slippage_bps,
            "exit_slippage_bps": exit_slippage_bps,
            "total_slippage_bps": round(lp.entry_slippage_bps + exit_slippage_bps, 2),
            # PnL
            "quantity": lp.quantity,
            "leverage": lp.leverage,
            "raw_pnl": round(raw_pnl, 4),
            "fees": round(total_fees, 4),
            "funding_cost": round(funding_cost, 4),
            "pnl": round(net_pnl, 2),
            # Exit
            "exit_reason": reason,
            "timestamp": now,
        }

        logger.info(
            "📉 TRADE CLOSED: {} {} hold={} MAE={:.2f}% MFE={:.2f}% slip={:.1f}bps PnL=${:.2f} ({})",
            lp.side, symbol, record["holding_period_str"],
            lp.mae_pct, lp.mfe_pct, lp.entry_slippage_bps, net_pnl, reason,
        )

        return record

    def get_live_analytics(self, symbol: str) -> Optional[Dict]:
        """Get current analytics for an open position (for live display)."""
        lp = self._positions.get(symbol)
        if not lp or not lp._initialised:
            return None

        now = time.time()
        return {
            "symbol": lp.symbol,
            "side": lp.side,
            "entry_price": lp.entry_price,
            "current_price": lp.current_price,
            "entry_slippage_bps": lp.entry_slippage_bps,
            "mae": round(lp.mae, 6),
            "mae_pct": round(lp.mae_pct, 4),
            "mfe": round(lp.mfe, 6),
            "mfe_pct": round(lp.mfe_pct, 4),
            "holding_period": round(now - lp.entry_time, 1),
            "holding_period_str": self._format_duration(now - lp.entry_time),
            "peak_price": lp.peak_price,
            "trough_price": lp.trough_price,
        }

    def get_all_live(self) -> Dict[str, Dict]:
        """Get live analytics for all tracked positions."""
        return {sym: self.get_live_analytics(sym) for sym in self._positions}

    @property
    def tracked_count(self) -> int:
        return len(self._positions)

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format seconds into human-readable duration."""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds / 60)}m {int(seconds % 60)}s"
        elif seconds < 86400:
            h = int(seconds / 3600)
            m = int((seconds % 3600) / 60)
            return f"{h}h {m}m"
        else:
            d = int(seconds / 86400)
            h = int((seconds % 86400) / 3600)
            return f"{d}d {h}h"
