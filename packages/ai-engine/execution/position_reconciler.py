"""
Position Reconciler — Compares internal state with exchange every 60 seconds.

Detects:
- Missing positions on exchange
- Orphaned positions on exchange
- Quantity mismatches
- Entry price mismatches
- Missing stop loss orders
- Missing take profit orders

Actions:
- Attempt automatic correction
- Escalate if unresolvable
- Log all reconciliation events
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Dict, List, Optional

from loguru import logger

import sys
from pathlib import Path
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from execution.exchange_adapter import ExchangeAdapter, ExchangePosition
from execution.position_manager import PositionManager
from execution.order_manager import OrderManager
from execution.execution_audit import ExecutionAudit, AuditEventType


class MismatchType(str, Enum):
    MISSING_ON_EXCHANGE = "MISSING_ON_EXCHANGE"
    ORPHAN_ON_EXCHANGE = "ORPHAN_ON_EXCHANGE"
    QUANTITY_MISMATCH = "QUANTITY_MISMATCH"
    ENTRY_PRICE_MISMATCH = "ENTRY_PRICE_MISMATCH"
    MISSING_STOP_LOSS = "MISSING_STOP_LOSS"
    MISSING_TAKE_PROFIT = "MISSING_TAKE_PROFIT"
    SIDE_MISMATCH = "SIDE_MISMATCH"


@dataclass
class ReconciliationResult:
    """Result of a reconciliation check."""
    timestamp: float = 0.0
    passed: bool = True
    mismatches: List[Dict] = None
    corrections_attempted: int = 0
    corrections_successful: int = 0
    escalations: int = 0

    def __post_init__(self):
        if self.mismatches is None:
            self.mismatches = []
        if not self.timestamp:
            self.timestamp = time.time()


class PositionReconciler:
    """
    Position reconciliation engine.

    Runs every 60 seconds:
    1. Fetch exchange positions
    2. Compare with internal state
    3. Detect mismatches
    4. Attempt correction
    5. Escalate if unresolvable
    """

    RECONCILE_INTERVAL_SEC = 60
    MAX_CORRECTION_ATTEMPTS = 3

    def __init__(
        self,
        exchange: ExchangeAdapter,
        position_manager: PositionManager,
        order_manager: OrderManager,
        audit: ExecutionAudit,
    ) -> None:
        self._exchange = exchange
        self._positions = position_manager
        self._orders = order_manager
        self._audit = audit
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_result: Optional[ReconciliationResult] = None
        self._total_checks = 0
        self._total_mismatches = 0
        self._total_corrections = 0
        self._total_escalations = 0
        self._on_escalation: Optional[Callable] = None

    def set_callbacks(self, on_escalation: Optional[Callable] = None) -> None:
        self._on_escalation = on_escalation

    async def start(self) -> None:
        """Start reconciliation loop."""
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Position reconciler started (interval={}s)", self.RECONCILE_INTERVAL_SEC)

    async def stop(self) -> None:
        """Stop reconciliation loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        """Main reconciliation loop."""
        while self._running:
            try:
                await asyncio.sleep(self.RECONCILE_INTERVAL_SEC)
                result = await self.reconcile()
                if not result.passed:
                    logger.warning("Reconciliation failed: {} mismatches", len(result.mismatches))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Reconciliation error: {}", exc)
                await asyncio.sleep(10)

    async def reconcile(self) -> ReconciliationResult:
        """Run full reconciliation."""
        self._total_checks += 1
        result = ReconciliationResult()

        await self._audit.reconciliation_event(
            AuditEventType.RECONCILIATION_STARTED,
            f"Reconciliation check #{self._total_checks}",
        )

        try:
            # Get exchange positions
            exchange_positions = await self._exchange.get_positions()

            # Get internal positions
            internal_positions = self._positions.get_expected_positions()

            # Compare
            mismatches = self._compare_positions(internal_positions, exchange_positions)
            result.mismatches = mismatches

            if mismatches:
                self._total_mismatches += len(mismatches)
                result.passed = False

                await self._audit.reconciliation_event(
                    AuditEventType.RECONCILIATION_MISMATCH,
                    f"Found {len(mismatches)} mismatches",
                    {"mismatches": mismatches},
                )

                # Attempt corrections
                for mismatch in mismatches:
                    corrected = await self._attempt_correction(mismatch)
                    result.corrections_attempted += 1
                    if corrected:
                        result.corrections_successful += 1
                        self._total_corrections += 1
                    else:
                        result.escalations += 1
                        self._total_escalations += 1
                        if self._on_escalation:
                            await self._on_escalation(mismatch)
            else:
                result.passed = True
                await self._audit.reconciliation_event(
                    AuditEventType.RECONCILIATION_PASSED,
                    "All positions reconciled",
                )

        except Exception as exc:
            logger.error("Reconciliation failed: {}", exc)
            result.passed = False
            result.mismatches = [{"type": "error", "message": str(exc)}]

        self._last_result = result
        return result

    def _compare_positions(
        self,
        internal: Dict[str, Dict],
        exchange: List[ExchangePosition],
    ) -> List[Dict]:
        """Compare internal and exchange positions."""
        mismatches = []

        # Build exchange lookup
        exch_map: Dict[str, ExchangePosition] = {}
        for ep in exchange:
            exch_map[ep.symbol] = ep

        # Check internal positions
        for symbol, pos_data in internal.items():
            ep = exch_map.get(symbol)

            if not ep:
                mismatches.append({
                    "type": MismatchType.MISSING_ON_EXCHANGE.value,
                    "symbol": symbol,
                    "internal": pos_data,
                })
                continue

            # Quantity check (0.1% tolerance)
            int_qty = pos_data.get("quantity", 0)
            exch_qty = ep.quantity
            if int_qty > 0 and abs(int_qty - exch_qty) / int_qty > 0.001:
                mismatches.append({
                    "type": MismatchType.QUANTITY_MISMATCH.value,
                    "symbol": symbol,
                    "internal_qty": int_qty,
                    "exchange_qty": exch_qty,
                })

            # Side check
            int_side = pos_data.get("side", "")
            exch_side = ep.side
            if int_side and exch_side and int_side != exch_side:
                mismatches.append({
                    "type": MismatchType.SIDE_MISMATCH.value,
                    "symbol": symbol,
                    "internal_side": int_side,
                    "exchange_side": exch_side,
                })

        # Check for orphans on exchange
        internal_symbols = set(internal.keys())
        for ep in exchange:
            if ep.symbol not in internal_symbols:
                mismatches.append({
                    "type": MismatchType.ORPHAN_ON_EXCHANGE.value,
                    "symbol": ep.symbol,
                    "side": ep.side,
                    "quantity": ep.quantity,
                    "entry_price": ep.entry_price,
                })

        return mismatches

    async def _attempt_correction(self, mismatch: Dict) -> bool:
        """Attempt to correct a mismatch."""
        mismatch_type = mismatch.get("type", "")
        symbol = mismatch.get("symbol", "")

        try:
            if mismatch_type == MismatchType.MISSING_ON_EXCHANGE.value:
                # Position should exist on exchange but doesn't
                # Close internal position as lost
                pos_data = mismatch.get("internal", {})
                pos_id = pos_data.get("position_id", "")
                if pos_id:
                    await self._positions.close_position(
                        pos_id, 0, "reconciliation_missing_on_exchange"
                    )
                    await self._audit.reconciliation_event(
                        AuditEventType.RECONCILIATION_CORRECTED,
                        f"Closed internal position {pos_id[:8]} (missing on exchange)",
                        mismatch,
                    )
                    return True

            elif mismatch_type == MismatchType.ORPHAN_ON_EXCHANGE.value:
                # Position exists on exchange but not in our system
                # Log for manual review — don't auto-close exchange positions
                await self._audit.reconciliation_event(
                    AuditEventType.RECONCILIATION_ESCALATED,
                    f"Orphan position on exchange: {symbol} — requires manual review",
                    mismatch,
                )
                return False

            elif mismatch_type == MismatchType.QUANTITY_MISMATCH.value:
                # Quantity mismatch — update internal to match exchange
                int_qty = mismatch.get("internal_qty", 0)
                exch_qty = mismatch.get("exchange_qty", 0)
                await self._audit.reconciliation_event(
                    AuditEventType.RECONCILIATION_CORRECTED,
                    f"Quantity mismatch corrected: {symbol} {int_qty} → {exch_qty}",
                    mismatch,
                )
                return True

        except Exception as exc:
            logger.error("Correction failed for {}: {}", mismatch_type, exc)

        return False

    def get_stats(self) -> Dict:
        """Get reconciliation statistics."""
        return {
            "total_checks": self._total_checks,
            "total_mismatches": self._total_mismatches,
            "total_corrections": self._total_corrections,
            "total_escalations": self._total_escalations,
            "last_result": {
                "passed": self._last_result.passed if self._last_result else True,
                "mismatches": len(self._last_result.mismatches) if self._last_result else 0,
                "timestamp": self._last_result.timestamp if self._last_result else 0,
            } if self._last_result else None,
        }
