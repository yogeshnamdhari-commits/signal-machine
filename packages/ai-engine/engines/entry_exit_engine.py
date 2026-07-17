"""
Entry/Exit Engine — Precision tracking of order fills and execution quality.

Tracks:
  - Signal → Entry: time delay, price slippage, fill quality
  - Entry → Exit: execution reason, market conditions at exit
  - Fill quality scoring (how close to optimal entry/exit)
  - Market impact estimation

The EntryExitEngine works alongside TradeEngine but focuses on
execution quality rather than excursion analytics.

Usage::

    ee = EntryExitEngine()
    ee.record_signal(symbol, signal_price, signal_time)
    ee.record_fill(symbol, fill_price, fill_time, is_entry=True)
    report = ee.get_execution_report(symbol)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from loguru import logger


@dataclass
class _ExecutionState:
    """Tracks execution quality for a single symbol."""
    symbol: str = ""
    # Signal
    signal_price: float = 0
    signal_time: float = 0
    signal_side: str = ""
    # Entry fill
    entry_price: float = 0
    entry_time: float = 0
    entry_fill_delay_ms: float = 0     # ms from signal to fill
    entry_slippage_bps: float = 0
    # Exit fill
    exit_price: float = 0
    exit_time: float = 0
    exit_reason: str = ""
    exit_fill_delay_ms: float = 0
    exit_slippage_bps: float = 0
    # Market context at fill
    spread_at_entry: float = 0
    spread_at_exit: float = 0
    volume_at_entry: float = 0
    # Quality score (0-100)
    entry_quality: float = 0
    exit_quality: float = 0
    # History of price snapshots during trade
    price_snapshots: List[Dict] = field(default_factory=list)
    _max_snapshots: int = 200


class EntryExitEngine:
    """
    Tracks execution quality: slippage, fill delays, and market context.

    Complements TradeEngine by focusing on HOW trades were executed
    rather than how they performed.
    """

    def __init__(self) -> None:
        self._states: Dict[str, _ExecutionState] = {}

    async def initialize(self) -> None:
        logger.info("EntryExitEngine ready")

    def record_signal(
        self,
        symbol: str,
        signal_price: float,
        side: str = "",
    ) -> None:
        """Record when a signal is generated (before execution)."""
        st = self._states.setdefault(symbol, _ExecutionState(symbol=symbol))
        st.signal_price = signal_price
        st.signal_time = time.time()
        st.signal_side = side

    def record_fill(
        self,
        symbol: str,
        fill_price: float,
        is_entry: bool = True,
        spread: float = 0,
        volume: float = 0,
    ) -> None:
        """Record an actual fill (entry or exit)."""
        st = self._states.setdefault(symbol, _ExecutionState(symbol=symbol))
        now = time.time()

        if is_entry:
            st.entry_price = fill_price
            st.entry_time = now
            st.spread_at_entry = spread
            st.volume_at_entry = volume

            # Fill delay (signal → entry)
            if st.signal_time > 0:
                st.entry_fill_delay_ms = (now - st.signal_time) * 1000

            # Slippage (entry vs signal)
            if st.signal_price > 0:
                st.entry_slippage_bps = (fill_price - st.signal_price) / st.signal_price * 10_000

            # Entry quality score
            st.entry_quality = self._compute_entry_quality(st)
        else:
            st.exit_price = fill_price
            st.exit_time = now
            st.spread_at_exit = spread

            # Exit slippage (relative to last tracked price)
            if st.entry_price > 0:
                st.exit_slippage_bps = (fill_price - st.entry_price) / st.entry_price * 10_000

            # Exit quality score
            st.exit_quality = self._compute_exit_quality(st)

        logger.debug(
            "📊 FILL: {} {} {} @ {} (delay={:.0f}ms, slip={:.1f}bps, quality={:.0f})",
            "ENTRY" if is_entry else "EXIT", symbol, st.signal_side,
            fill_price, st.entry_fill_delay_ms if is_entry else st.exit_fill_delay_ms,
            st.entry_slippage_bps if is_entry else st.exit_slippage_bps,
            st.entry_quality if is_entry else st.exit_quality,
        )

    def record_exit_reason(self, symbol: str, reason: str) -> None:
        """Record the exit reason (stop_loss, take_profit, trailing, etc.)."""
        st = self._states.get(symbol)
        if st:
            st.exit_reason = reason

    def record_price_snapshot(self, symbol: str, price: float) -> None:
        """Record a price snapshot during the trade for analysis."""
        st = self._states.get(symbol)
        if not st:
            return
        st.price_snapshots.append({
            "price": price,
            "time": time.time(),
        })
        if len(st.price_snapshots) > st._max_snapshots:
            st.price_snapshots = st.price_snapshots[-st._max_snapshots:]

    def get_execution_report(self, symbol: str) -> Dict:
        """Get the full execution report for a symbol."""
        st = self._states.get(symbol)
        if not st:
            return {}

        return {
            "symbol": st.symbol,
            "side": st.signal_side,
            # Signal
            "signal_price": st.signal_price,
            "signal_time": st.signal_time,
            # Entry
            "entry_price": st.entry_price,
            "entry_time": st.entry_time,
            "entry_fill_delay_ms": round(st.entry_fill_delay_ms, 1),
            "entry_slippage_bps": round(st.entry_slippage_bps, 2),
            "entry_quality": round(st.entry_quality, 1),
            "spread_at_entry": st.spread_at_entry,
            # Exit
            "exit_price": st.exit_price,
            "exit_time": st.exit_time,
            "exit_reason": st.exit_reason,
            "exit_slippage_bps": round(st.exit_slippage_bps, 2),
            "exit_quality": round(st.exit_quality, 1),
            "spread_at_exit": st.spread_at_exit,
            # Combined
            "total_slippage_bps": round(st.entry_slippage_bps + st.exit_slippage_bps, 2),
            "execution_score": round((st.entry_quality + st.exit_quality) / 2, 1) if st.exit_price > 0 else round(st.entry_quality, 1),
        }

    def cleanup(self, symbol: str) -> Optional[Dict]:
        """Finalise and return report, then remove state."""
        report = self.get_execution_report(symbol)
        self._states.pop(symbol, None)
        return report

    # ── Quality scoring ──────────────────────────────────────────

    def _compute_entry_quality(self, st: _ExecutionState) -> float:
        """
        Entry quality score (0-100).
        
        Factors:
        - Slippage: less = better (0 bps = 100, >10 bps = 0)
        - Fill delay: faster = better (<500ms = 100, >5000ms = 0)
        """
        score = 100.0

        # Slippage penalty
        slip = abs(st.entry_slippage_bps)
        if slip <= 0.5:
            slip_score = 100
        elif slip <= 2:
            slip_score = 80
        elif slip <= 5:
            slip_score = 50
        elif slip <= 10:
            slip_score = 20
        else:
            slip_score = 0

        # Delay penalty
        delay = st.entry_fill_delay_ms
        if delay <= 200:
            delay_score = 100
        elif delay <= 500:
            delay_score = 80
        elif delay <= 1000:
            delay_score = 60
        elif delay <= 3000:
            delay_score = 30
        else:
            delay_score = 0

        # Weighted: 70% slippage, 30% delay
        score = slip_score * 0.7 + delay_score * 0.3
        return max(0, min(100, score))

    def _compute_exit_quality(self, st: _ExecutionState) -> float:
        """
        Exit quality score (0-100).
        
        Based on exit slippage and spread.
        """
        slip = abs(st.exit_slippage_bps)
        if slip <= 0.5:
            return 100
        elif slip <= 2:
            return 80
        elif slip <= 5:
            return 50
        elif slip <= 10:
            return 20
        else:
            return 0
