"""
Fill Manager — Tracks all order fills including partial fills.

Responsible for:
- Tracking individual fills
- Partial fill aggregation
- Average entry price calculation
- Fee tracking
- Slippage measurement
- Fill quality analysis
- Persistent state
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

import sys
from pathlib import Path
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))


@dataclass
class Fill:
    """Individual fill record."""
    fill_id: str = ""
    order_id: str = ""
    exchange_order_id: int = 0
    signal_id: str = ""
    symbol: str = ""
    side: str = ""
    price: float = 0.0
    quantity: float = 0.0
    quote_qty: float = 0.0
    fee: float = 0.0
    fee_asset: str = "USDT"
    is_maker: bool = False
    realized_pnl: float = 0.0
    timestamp: float = 0.0
    trade_id: int = 0

    def __post_init__(self):
        if not self.fill_id:
            self.fill_id = str(uuid.uuid4())
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class FillAggregate:
    """Aggregated fills for an order."""
    order_id: str = ""
    signal_id: str = ""
    symbol: str = ""
    side: str = ""
    total_quantity: float = 0.0
    total_quote_qty: float = 0.0
    total_fees: float = 0.0
    avg_price: float = 0.0
    fill_count: int = 0
    first_fill_time: float = 0.0
    last_fill_time: float = 0.0
    fill_pct: float = 0.0       # % of order filled
    expected_price: float = 0.0 # Price when order was placed
    slippage_bps: float = 0.0   # Slippage in basis points
    is_complete: bool = False    # 100% filled
    fills: List[Fill] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "order_id": self.order_id,
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "side": self.side,
            "total_quantity": self.total_quantity,
            "total_quote_qty": self.total_quote_qty,
            "total_fees": self.total_fees,
            "avg_price": self.avg_price,
            "fill_count": self.fill_count,
            "first_fill_time": self.first_fill_time,
            "last_fill_time": self.last_fill_time,
            "fill_pct": self.fill_pct,
            "expected_price": self.expected_price,
            "slippage_bps": self.slippage_bps,
            "is_complete": self.is_complete,
        }


class FillManager:
    """
    Tracks all fills with aggregation, slippage analysis, and persistence.

    Key features:
    - Individual fill tracking
    - Partial fill aggregation (10%, 25%, 50%, 75%, 100%)
    - Weighted average price calculation
    - Slippage measurement vs expected price
    - Fee aggregation
    - Fill quality scoring
    """

    STATE_FILE = _ai_root / "data" / "execution" / "fill_state.json"

    def __init__(self) -> None:
        self._fills: Dict[str, Fill] = {}                    # fill_id → Fill
        self._aggregates: Dict[str, FillAggregate] = {}       # order_id → FillAggregate
        self._fills_by_order: Dict[str, List[str]] = {}       # order_id → [fill_ids]
        self._fills_by_signal: Dict[str, List[str]] = {}      # signal_id → [fill_ids]
        self._fills_by_symbol: Dict[str, List[str]] = {}      # symbol → [fill_ids]
        self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    # ── Fill Recording ───────────────────────────────────────────

    def record_fill(
        self,
        order_id: str,
        signal_id: str,
        symbol: str,
        side: str,
        price: float,
        quantity: float,
        quote_qty: float = 0.0,
        fee: float = 0.0,
        fee_asset: str = "USDT",
        is_maker: bool = False,
        realized_pnl: float = 0.0,
        exchange_order_id: int = 0,
        trade_id: int = 0,
        expected_price: float = 0.0,
        order_quantity: float = 0.0,
    ) -> Fill:
        """Record a fill and update aggregates."""
        if quote_qty == 0.0:
            quote_qty = price * quantity

        fill = Fill(
            order_id=order_id,
            exchange_order_id=exchange_order_id,
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            price=price,
            quantity=quantity,
            quote_qty=quote_qty,
            fee=fee,
            fee_asset=fee_asset,
            is_maker=is_maker,
            realized_pnl=realized_pnl,
            trade_id=trade_id,
        )

        # Store fill
        self._fills[fill.fill_id] = fill
        self._fills_by_order.setdefault(order_id, []).append(fill.fill_id)
        self._fills_by_signal.setdefault(signal_id, []).append(fill.fill_id)
        self._fills_by_symbol.setdefault(symbol, []).append(fill.fill_id)

        # Update aggregate
        agg = self._aggregates.get(order_id)
        if not agg:
            agg = FillAggregate(
                order_id=order_id,
                signal_id=signal_id,
                symbol=symbol,
                side=side,
                expected_price=expected_price,
            )
            self._aggregates[order_id] = agg

        agg.fills.append(fill)
        agg.fill_count += 1
        agg.last_fill_time = fill.timestamp
        if agg.first_fill_time == 0:
            agg.first_fill_time = fill.timestamp

        # Recalculate weighted average
        old_qty = agg.total_quantity
        old_value = agg.avg_price * old_qty
        new_value = price * quantity
        agg.total_quantity += quantity
        agg.total_quote_qty += quote_qty
        agg.total_fees += fee

        if agg.total_quantity > 0:
            agg.avg_price = (old_value + new_value) / agg.total_quantity

        # Fill percentage
        if order_quantity > 0:
            agg.fill_pct = (agg.total_quantity / order_quantity) * 100
            agg.is_complete = agg.fill_pct >= 99.9

        # Slippage
        if expected_price > 0 and agg.avg_price > 0:
            if side == "BUY":
                agg.slippage_bps = ((agg.avg_price - expected_price) / expected_price) * 10000
            else:
                agg.slippage_bps = ((expected_price - agg.avg_price) / expected_price) * 10000

        logger.info("Fill recorded: {} {} {:.6f} @ {:.4f} (agg: {:.6f} @ {:.4f}, {:.1f}%, slippage={:.1f}bps)",
                     order_id[:8], side, quantity, price,
                     agg.total_quantity, agg.avg_price, agg.fill_pct, agg.slippage_bps)

        return fill

    # ── Queries ──────────────────────────────────────────────────

    def get_fill(self, fill_id: str) -> Optional[Fill]:
        return self._fills.get(fill_id)

    def get_aggregate(self, order_id: str) -> Optional[FillAggregate]:
        return self._aggregates.get(order_id)

    def get_order_fills(self, order_id: str) -> List[Fill]:
        fill_ids = self._fills_by_order.get(order_id, [])
        return [self._fills[fid] for fid in fill_ids if fid in self._fills]

    def get_signal_fills(self, signal_id: str) -> List[Fill]:
        fill_ids = self._fills_by_signal.get(signal_id, [])
        return [self._fills[fid] for fid in fill_ids if fid in self._fills]

    def get_symbol_fills(self, symbol: str, since: float = 0) -> List[Fill]:
        fill_ids = self._fills_by_symbol.get(symbol, [])
        fills = [self._fills[fid] for fid in fill_ids if fid in self._fills]
        if since > 0:
            fills = [f for f in fills if f.timestamp >= since]
        return fills

    def get_recent_fills(self, limit: int = 100) -> List[Fill]:
        all_fills = sorted(self._fills.values(), key=lambda f: f.timestamp, reverse=True)
        return all_fills[:limit]

    # ── Analysis ─────────────────────────────────────────────────

    def get_fill_quality(self, order_id: str) -> Dict:
        """Analyze fill quality for an order."""
        agg = self._aggregates.get(order_id)
        if not agg:
            return {"quality": "unknown"}

        quality_score = 100.0

        # Slippage penalty
        if abs(agg.slippage_bps) > 10:
            quality_score -= 20
        elif abs(agg.slippage_bps) > 5:
            quality_score -= 10
        elif abs(agg.slippage_bps) > 2:
            quality_score -= 5

        # Partial fill penalty
        if not agg.is_complete:
            quality_score -= (100 - agg.fill_pct) * 0.5

        # Multiple fills penalty (indicates thin liquidity)
        if agg.fill_count > 5:
            quality_score -= (agg.fill_count - 5) * 2

        quality_score = max(0, min(100, quality_score))

        return {
            "quality_score": round(quality_score, 1),
            "quality": "excellent" if quality_score >= 90 else
                       "good" if quality_score >= 75 else
                       "fair" if quality_score >= 50 else "poor",
            "slippage_bps": round(agg.slippage_bps, 2),
            "fill_pct": round(agg.fill_pct, 1),
            "fill_count": agg.fill_count,
            "avg_price": agg.avg_price,
            "total_fees": agg.total_fees,
        }

    def get_stats(self) -> Dict:
        """Get fill manager statistics."""
        total_fills = len(self._fills)
        total_volume = sum(f.quote_qty for f in self._fills.values())
        total_fees = sum(f.fee for f in self._fills.values())

        slippages = [a.slippage_bps for a in self._aggregates.values()]
        avg_slippage = sum(slippages) / len(slippages) if slippages else 0.0

        fill_pcts = [a.fill_pct for a in self._aggregates.values()]
        complete_fills = sum(1 for p in fill_pcts if p >= 99.9)

        return {
            "total_fills": total_fills,
            "total_aggregates": len(self._aggregates),
            "complete_fills": complete_fills,
            "partial_fills": len(self._aggregates) - complete_fills,
            "total_volume": round(total_volume, 2),
            "total_fees": round(total_fees, 4),
            "avg_slippage_bps": round(avg_slippage, 2),
            "unique_symbols": len(self._fills_by_symbol),
        }

    # ── State Persistence ────────────────────────────────────────

    async def save_state(self) -> None:
        """Persist fill state to disk."""
        try:
            state = {
                "fills": {fid: f.to_dict() for fid, f in self._fills.items()},
                "aggregates": {oid: a.to_dict() for oid, a in self._aggregates.items()},
                "saved_at": time.time(),
            }
            tmp = str(self.STATE_FILE) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f)
            Path(tmp).rename(self.STATE_FILE)
        except Exception as exc:
            logger.error("Failed to save fill state: {}", exc)

    async def load_state(self) -> int:
        """Restore fill state from disk."""
        if not self.STATE_FILE.exists():
            return 0

        try:
            with open(self.STATE_FILE) as f:
                state = json.load(f)

            for fid, data in state.get("fills", {}).items():
                fill = Fill(**data)
                self._fills[fid] = fill
                self._fills_by_order.setdefault(fill.order_id, []).append(fid)
                self._fills_by_signal.setdefault(fill.signal_id, []).append(fid)
                self._fills_by_symbol.setdefault(fill.symbol, []).append(fid)

            for oid, data in state.get("aggregates", {}).items():
                agg = FillAggregate(**data)
                self._aggregates[oid] = agg

            logger.info("Fill state restored: {} fills, {} aggregates",
                        len(self._fills), len(self._aggregates))
            return len(self._fills)

        except Exception as exc:
            logger.error("Failed to load fill state: {}", exc)
            return 0
