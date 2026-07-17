"""
Execution Analytics — tracks per-trade execution quality.
Stores: latency, slippage, fill ratio, rejection rate.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from loguru import logger


@dataclass
class ExecutionRecord:
    symbol: str = ""
    side: str = ""
    entry_time: float = 0.0
    entry_price: float = 0.0
    expected_entry: float = 0.0
    exit_time: float = 0.0
    exit_price: float = 0.0
    expected_exit: float = 0.0
    entry_latency_ms: float = 0.0
    exit_latency_ms: float = 0.0
    entry_slippage_bps: float = 0.0
    exit_slippage_bps: float = 0.0
    fill_ratio: float = 1.0
    partial_fills: int = 0
    rejections: int = 0
    order_type: str = "market"


class ExecutionAnalytics:
    """Tracks execution quality for all trades."""

    def __init__(self) -> None:
        self._records: List[ExecutionRecord] = []
        self._max_records = 1000

    def record_entry(self, symbol: str, side: str, expected_price: float,
                     actual_price: float, latency_ms: float = 0,
                     fill_ratio: float = 1.0, partial_fills: int = 0) -> None:
        """Record trade entry execution quality."""
        rec = ExecutionRecord(
            symbol=symbol,
            side=side,
            entry_time=time.time(),
            entry_price=actual_price,
            expected_entry=expected_price,
            entry_latency_ms=latency_ms,
            fill_ratio=fill_ratio,
            partial_fills=partial_fills,
        )
        # Calculate slippage in basis points
        if expected_price > 0:
            rec.entry_slippage_bps = abs(actual_price - expected_price) / expected_price * 10000

        self._records.append(rec)
        if len(self._records) > self._max_records:
            self._records = self._records[-self._max_records // 2:]

    def record_exit(self, symbol: str, expected_price: float,
                    actual_price: float, latency_ms: float = 0,
                    fill_ratio: float = 1.0, rejections: int = 0) -> None:
        """Record trade exit execution quality."""
        # Find the most recent entry for this symbol
        for rec in reversed(self._records):
            if rec.symbol == symbol and rec.exit_time == 0:
                rec.exit_time = time.time()
                rec.exit_price = actual_price
                rec.expected_exit = expected_price
                rec.exit_latency_ms = latency_ms
                rec.rejections = rejections
                if expected_price > 0:
                    rec.exit_slippage_bps = abs(actual_price - expected_price) / expected_price * 10000
                break

    def get_stats(self) -> Dict:
        """Get aggregate execution statistics."""
        if not self._records:
            return {
                "total_trades": 0,
                "avg_entry_slippage_bps": 0,
                "max_entry_slippage_bps": 0,
                "avg_exit_slippage_bps": 0,
                "max_exit_slippage_bps": 0,
                "avg_entry_latency_ms": 0,
                "max_entry_latency_ms": 0,
                "avg_fill_ratio": 0,
                "total_rejections": 0,
                "rejection_rate": 0,
            }

        entry_slips = [r.entry_slippage_bps for r in self._records if r.entry_slippage_bps > 0]
        exit_slips = [r.exit_slippage_bps for r in self._records if r.exit_slippage_bps > 0]
        latencies = [r.entry_latency_ms for r in self._records if r.entry_latency_ms > 0]
        fill_ratios = [r.fill_ratio for r in self._records]
        total_rej = sum(r.rejections for r in self._records)
        total_with_exit = sum(1 for r in self._records if r.exit_time > 0)

        return {
            "total_trades": len(self._records),
            "avg_entry_slippage_bps": round(sum(entry_slips) / max(len(entry_slips), 1), 2),
            "max_entry_slippage_bps": round(max(entry_slips) if entry_slips else 0, 2),
            "avg_exit_slippage_bps": round(sum(exit_slips) / max(len(exit_slips), 1), 2),
            "max_exit_slippage_bps": round(max(exit_slips) if exit_slips else 0, 2),
            "avg_entry_latency_ms": round(sum(latencies) / max(len(latencies), 1), 1),
            "max_entry_latency_ms": round(max(latencies) if latencies else 0, 1),
            "avg_fill_ratio": round(sum(fill_ratios) / max(len(fill_ratios), 1), 3),
            "total_rejections": total_rej,
            "rejection_rate": round(total_rej / max(total_with_exit, 1), 3),
        }

    def get_recent(self, n: int = 10) -> List[Dict]:
        """Get recent execution records."""
        return [
            {
                "symbol": r.symbol,
                "side": r.side,
                "entry_slippage_bps": round(r.entry_slippage_bps, 2),
                "exit_slippage_bps": round(r.exit_slippage_bps, 2),
                "entry_latency_ms": round(r.entry_latency_ms, 1),
                "fill_ratio": r.fill_ratio,
            }
            for r in self._records[-n:]
        ]


# Singleton
execution_analytics = ExecutionAnalytics()
