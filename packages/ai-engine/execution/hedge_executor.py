"""
Hedge Executor — Atomic multi-leg execution with recovery logic.

Ensures that both legs of an arbitrage trade are executed as close to simultaneously as possible.
Implements robust recovery mechanisms to prevent naked exposure if one leg fails or partially fills.
"""
from __future__ import annotations
import asyncio
import time
from typing import Dict, Any, List, Optional
from loguru import logger

from exchanges.base_exchange import BaseExchange, ExchangeOrder
from execution.arbitrage_ranker import ArbitrageOpportunity
from execution.arbitrage_db import ArbitrageDB
from execution.execution_audit import ExecutionAudit, AuditEventType
from execution.capital_allocator import AllocationRequest

class HedgeExecutor:
    def __init__(
        self, 
        exchanges: Dict[str, BaseExchange], 
        execution_engine: Any, # Reference to the main ExecutionEngine
        audit: ExecutionAudit,
        arbitrage_db: ArbitrageDB
    ) -> None:
        self.exchanges = exchanges
        self.engine = execution_engine
        self.audit = audit
        self.db = arbitrage_db
        self._active_hedges: Dict[str, Dict] = {} # arb_id -> hedge_state
        self._history: List[Dict] = [] # Simplified in-memory history
        self._max_history = 100

    async def execute_atomic_hedge(self, opp: ArbitrageOpportunity) -> bool:
        """
        Executes two legs simultaneously. 
        If one leg fails to fill, it immediately attempts to liquidate the other.
        """
        arb_id = opp.arb_id
        symbol = opp.symbol
        
        # 1. Fetch current prices from both exchanges
        long_ex = self.exchanges[opp.long_exchange]
        short_ex = self.exchanges[opp.short_exchange]

        long_price, short_price = await asyncio.gather(
            long_ex.get_mark_price(symbol),
            short_ex.get_mark_price(symbol),
            return_exceptions=True,
        )
        if isinstance(long_price, Exception) or isinstance(short_price, Exception):
            logger.error(f"Failed to fetch prices for {arb_id}: long={long_price}, short={short_price}")
            return False
        if long_price <= 0 or short_price <= 0:
            logger.error(f"Invalid prices for {arb_id}: long={long_price}, short={short_price}")
            return False

        # 2. Determine size via Allocation Engine (integrated with regular execution flow)
        alloc_req = AllocationRequest(
            symbol=symbol, exchange=opp.long_exchange, signal_score=opp.score,
            confidence=opp.confidence, volatility=0.02, market_regime="range",
            portfolio_equity=self.engine.get_equity()
        )
        allocation = await self.engine.allocator.allocate(alloc_req)
        if allocation.capital_usd <= 0:
            return False
        qty = allocation.capital_usd / long_price

        logger.info(f"Executing atomic hedge for {arb_id}: LONG {opp.long_exchange} / SHORT {opp.short_exchange} {symbol} qty={qty}")
        await self.audit.system_event(
            AuditEventType.ORDER_CREATED,
            f"Attempting atomic hedge for {arb_id}",
            opp.to_dict()
        )

        # 3. Atomic Order Submission
        # We use gather but must process results sequentially to detect immediate atomic failures
        results = await asyncio.gather(
            self.engine.place_arbitrage_order(arb_id, opp.long_exchange, symbol, "BUY", qty, long_price, True),
            self.engine.place_arbitrage_order(arb_id, opp.short_exchange, symbol, "SELL", qty, short_price, False),
            return_exceptions=True
        )
        
        long_order_record: Optional[ExchangeOrder] = results[0]
        short_order_record: Optional[ExchangeOrder] = results[1]

        # Check for immediate failures (e.g., API errors, router couldn't place)
        if isinstance(long_order_record, Exception) or isinstance(short_order_record, Exception) or \
           long_order_record is None or short_order_record is None:
            return await self._handle_execution_failure(long_order_record, short_order_record, opp, qty)

        # Wait for orders to fill (or timeout)
        fill_timeout = 10.0 # seconds
        start_wait = time.time()
        while time.time() - start_wait < fill_timeout:
            long_order_status = await long_ex.get_order(symbol, order_id=long_order_record.order_id)
            short_order_status = await short_ex.get_order(symbol, order_id=short_order_record.order_id)

            if long_order_status.status == "FILLED" and short_order_status.status == "FILLED":
                self._record_success(opp, long_order_status, short_order_status)
                await self.db.record_execution(opp, long_order_status, short_order_status, "COMPLETED")
                return True
            
            if long_order_status.status in ["CANCELED", "REJECTED", "EXPIRED"] or \
               short_order_status.status in ["CANCELED", "REJECTED", "EXPIRED"]:
                logger.warning(f"One or both arbitrage legs failed to fill: Long status={long_order_status.status}, Short status={short_order_status.status}")
                return await self._handle_execution_failure(long_order_status, short_order_status, opp, qty)
            
            await asyncio.sleep(0.5)
        
        # If timeout, attempt to cancel and clean up
        logger.warning(f"Arbitrage execution timed out for {arb_id}. Attempting cleanup.")
        return await self._handle_execution_failure(long_order_record, short_order_record, opp, qty)

    async def _handle_execution_failure(self, l1_res: Any, l2_res: Any, opp: ArbitrageOpportunity, qty: float) -> bool:
        """
        Cleanup if one or both legs failed to fill completely.
        Goal: Never leave naked exposure.
        """
        arb_id = opp.arb_id
        symbol = opp.symbol
        long_ex = self.exchanges[opp.long_exchange]
        short_ex = self.exchanges[opp.short_exchange]

        logger.critical(f"Hedge Leg Failure detected for {arb_id}. Commencing emergency cleanup.")
        await self.audit.system_event(
            AuditEventType.SYSTEM_ERROR,
            f"Arbitrage hedge failure for {arb_id}. Initiating recovery.",
            {"opportunity": opp.to_dict(), "long_res": str(l1_res), "short_res": str(l2_res)}
        )

        # Check if long leg filled (or partially filled)
        if isinstance(l1_res, ExchangeOrder) and l1_res.executed_qty > 0:
            logger.warning(f"Closing orphaned LONG leg on {opp.long_exchange} for {symbol} qty={l1_res.executed_qty}")
            await long_ex.place_order(symbol, "SELL", "MARKET", l1_res.executed_qty, reduce_only=True)

        # Check if short leg filled (or partially filled)
        if isinstance(l2_res, ExchangeOrder) and l2_res.executed_qty > 0:
            logger.warning(f"Closing orphaned SHORT leg on {opp.short_exchange} for {symbol} qty={l2_res.executed_qty}")
            await short_ex.place_order(symbol, "BUY", "MARKET", l2_res.executed_qty, reduce_only=True)
        
        await self.db.record_execution(opp, l1_res, l2_res, "FAILED_RECOVERED")
        return False

    def _record_success(self, opp: ArbitrageOpportunity, l1_order: ExchangeOrder, l2_order: ExchangeOrder):
        """Records a successful atomic hedge."""
        profit = (l2_order.avg_price - l1_order.avg_price) * l1_order.executed_qty # Simplified PnL
        record = {
            "timestamp": time.time(),
            "arb_id": opp.arb_id,
            "symbol": opp.symbol,
            "long_ex": opp.long_exchange,
            "short_ex": opp.short_exchange,
            "profit": profit,
            "status": "COMPLETED"
        }
        self._history.append(record)
        if len(self._history) > self._max_history:
            self._history.pop(0)
        logger.info(f"Hedge {opp.arb_id} completed. Profit: ${profit:.2f}")

    def get_portfolio_stats(self) -> Dict:
        """Returns aggregated statistics for the dashboard."""
        profits = [r['profit'] for r in self._history if r.get('profit') is not None]
        total_arbitrages = len(self._history)
        realized_pnl = sum(profits)
        win_rate = len([p for p in profits if p > 0]) / max(1, total_arbitrages)

        return {
            "total_arbitrages": total_arbitrages,
            "realized_pnl_usd": round(realized_pnl, 2),
            "win_rate": round(win_rate, 4),
            "last_10_hedges": self._history[-10:]
        }