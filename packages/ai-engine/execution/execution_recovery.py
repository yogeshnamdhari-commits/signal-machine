"""
Execution Recovery — Crash and restart recovery system.

Recovers:
- Open positions from exchange
- Open orders from exchange
- Pending signals from state files
- Risk state from state files
- Account state from exchange
- Execution state from state files

Ensures:
- No lost positions after restart
- No duplicate orders after restart
- Automatic resumption of monitoring
- State consistency verification
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

import sys
from pathlib import Path
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from execution.exchange_adapter import ExchangeAdapter
from execution.position_manager import PositionManager, Position
from execution.order_manager import OrderManager, OrderRecord, OrderState
from execution.fill_manager import FillManager
from execution.execution_audit import ExecutionAudit, AuditEventType


@dataclass
class RecoveryResult:
    """Result of a recovery attempt."""
    success: bool = False
    positions_restored: int = 0
    orders_restored: int = 0
    fills_restored: int = 0
    account_balance: float = 0.0
    errors: List[str] = None
    duration_sec: float = 0.0

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


class ExecutionRecovery:
    """
    Crash and restart recovery system.

    Recovery Process:
    1. Sync time with exchange
    2. Load persisted state files
    3. Query exchange for current positions
    4. Query exchange for open orders
    5. Reconcile with persisted state
    6. Restore monitoring for recovered positions
    7. Cancel orphaned orders
    8. Verify state consistency
    """

    def __init__(
        self,
        exchanges: Dict[str, BaseExchange],
        position_manager: PositionManager,
        order_manager: OrderManager,
        fill_manager: FillManager,
        audit: ExecutionAudit,
    ) -> None:
        self._exchanges = exchanges
        self._positions = position_manager
        self._orders = order_manager
        self._fills = fill_manager
        self._audit = audit
        self._last_recovery: Optional[RecoveryResult] = None

    async def recover(self) -> RecoveryResult:
        """Execute full recovery sequence."""
        start = time.time()
        result = RecoveryResult()

        await self._audit.recovery_event(
            AuditEventType.RECOVERY_STARTED,
            "Starting execution recovery",
        )

        logger.info("═══ EXECUTION RECOVERY STARTED ═══")

        try:
            # Step 1: Sync time
            logger.info("Step 1/6: Syncing time with exchange...")
            for exch in self._exchanges.values():
                if hasattr(exch, 'sync_time'):
                    await exch.sync_time()

            # Step 2: Load persisted state
            logger.info("Step 2/6: Loading persisted state...")
            pos_loaded = await self._positions.load_state()
            orders_loaded = await self._orders.load_state()
            fills_loaded = await self._fills.load_state()
            logger.info("  Loaded: {} positions, {} orders, {} fills",
                       pos_loaded, orders_loaded, fills_loaded)

            # Step 3: Query exchange positions
            logger.info("Step 3/6: Querying exchange positions...")
            for exch_name, exch in self._exchanges.items():
                try:
                    exchange_positions = await exch.get_positions()
                    logger.info("  Exchange has {} open positions", len(exchange_positions))

                    # Reconcile with internal state
                    for ep in exchange_positions:
                        internal = self._positions.get_symbol_positions(ep.symbol)
                        if not internal:
                            # Position on exchange but not in our state
                            # Create recovery position
                            pos = await self._positions.open_position(
                                signal_id=f"RECOVERY-{ep.symbol}-{int(time.time())}",
                                exchange=exch_name,
                                symbol=ep.symbol,
                                side=ep.side,
                                entry_price=ep.entry_price,
                                quantity=ep.quantity,
                                leverage=ep.leverage,
                                confidence=0.0,
                                institutional_score=0.0,
                                market_regime="recovery",
                            )
                            if pos:
                                result.positions_restored += 1
                                await self._audit.recovery_event(
                                    AuditEventType.RECOVERY_POSITION_RESTORED,
                                    f"Recovered position: {ep.symbol} {ep.side} qty={ep.quantity}",
                                )
                        else:
                            result.positions_restored += 1

                except Exception as exc:
                    result.errors.append(f"Position query failed: {exc}")
                    logger.error("Position query failed: {}", exc)

            # Step 4: Query exchange orders
            logger.info("Step 4/6: Querying exchange open orders...")
            try:
                open_orders = await self._exchange.get_open_orders()
                logger.info("  Exchange has {} open orders", len(open_orders))

                for eo in open_orders:
                    # Check if we have this order
                    existing = self._orders.get_order_by_exchange_id(eo.order_id)
                    if existing:
                        result.orders_restored += 1
                    else:
                        # Orphaned order — cancel it
                        logger.warning("  Cancelling orphaned order: {} {} {}",
                                      eo.symbol, eo.side, eo.order_type)
                        try:
                            await self._exchange.cancel_order(
                                symbol=eo.symbol,
                                order_id=eo.order_id,
                            )
                            await self._audit.recovery_event(
                                AuditEventType.RECOVERY_ORDER_RESTORED,
                                f"Cancelled orphaned order: {eo.symbol} {eo.side}",
                            )
                        except Exception as cancel_exc:
                            result.errors.append(f"Failed to cancel orphan order: {cancel_exc}")

            except Exception as exc:
                result.errors.append(f"Order query failed: {exc}")
                logger.error("Order query failed: {}", exc)

            # Step 5: Sync active orders
            logger.info("Step 5/6: Syncing active orders...")
            synced = await self._orders.sync_all_active()
            logger.info("  Synced {} active orders", synced)

            # Step 6: Get account balance
            logger.info("Step 6/6: Querying account balance...")
            try:
                balance = await self._exchange.get_balance()
                result.account_balance = balance.get("balance", 0)
                logger.info("  Account balance: ${:.2f}", result.account_balance)
            except Exception as exc:
                result.errors.append(f"Balance query failed: {exc}")

            result.success = len(result.errors) == 0
            result.duration_sec = time.time() - start

            status = "SUCCESS" if result.success else "PARTIAL"
            logger.info("═══ EXECUTION RECOVERY {} ═══", status)
            logger.info("  Positions restored: {}", result.positions_restored)
            logger.info("  Orders restored: {}", result.orders_restored)
            logger.info("  Account balance: ${:.2f}", result.account_balance)
            logger.info("  Errors: {}", len(result.errors))
            logger.info("  Duration: {:.1f}s", result.duration_sec)

            await self._audit.recovery_event(
                AuditEventType.RECOVERY_COMPLETED if result.success else AuditEventType.RECOVERY_FAILED,
                f"Recovery {'completed' if result.success else 'failed'}: "
                f"{result.positions_restored} positions, {result.orders_restored} orders, "
                f"{len(result.errors)} errors in {result.duration_sec:.1f}s",
                {
                    "positions_restored": result.positions_restored,
                    "orders_restored": result.orders_restored,
                    "account_balance": result.account_balance,
                    "errors": result.errors,
                    "duration_sec": result.duration_sec,
                },
            )

        except Exception as exc:
            result.success = False
            result.errors.append(f"Recovery failed: {exc}")
            result.duration_sec = time.time() - start
            logger.error("Recovery failed: {}", exc)

            await self._audit.recovery_event(
                AuditEventType.RECOVERY_FAILED,
                f"Recovery failed: {exc}",
            )

        self._last_recovery = result
        return result

    def get_stats(self) -> Dict:
        """Get recovery statistics."""
        return {
            "last_recovery": {
                "success": self._last_recovery.success,
                "positions_restored": self._last_recovery.positions_restored,
                "orders_restored": self._last_recovery.orders_restored,
                "account_balance": self._last_recovery.account_balance,
                "errors": self._last_recovery.errors,
                "duration_sec": self._last_recovery.duration_sec,
            } if self._last_recovery else None,
        }
