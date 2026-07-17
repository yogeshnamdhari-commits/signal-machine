"""
Arbitrage Engine — Multi-exchange arbitrage orchestration.

Responsibilities:
- Scan exchanges for various arbitrage opportunities (cross-exchange, funding, basis, statistical, synthetic).
- Estimate execution cost and net edge for each opportunity.
- Rank opportunities based on a scoring mechanism.
- Publish arbitrage signals for potential execution.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional
from loguru import logger

from exchanges.base_exchange import BaseExchange, OrderbookSnapshot, FundingInfo
from execution.funding_arbitrage import FundingArbitrage
from execution.basis_arbitrage import BasisArbitrage
from execution.statistical_arbitrage import StatisticalArbitrage
from execution.arbitrage_ranker import ArbitrageRanker, ArbitrageOpportunity
from execution.hedge_executor import HedgeExecutor
from execution.arbitrage_db import ArbitrageDB
from execution.execution_audit import ExecutionAudit, AuditEventType
from config import config


class ArbitrageEngine:
    """
    Orchestrates scanning, detection, ranking and execution of arbitrage.
    """
    def __init__(
        self, 
        exchanges: Dict[str, BaseExchange],
        execution_engine: Any, # Reference to the main ExecutionEngine
        audit: ExecutionAudit
    ) -> None:
        self.exchanges = exchanges
        self.execution_engine = execution_engine
        self.audit = audit
        self.db = ArbitrageDB()
        
        # Specialized Scanners
        self.funding_arb = FundingArbitrage(exchanges)
        self.basis_arb = BasisArbitrage(exchanges)
        self.stat_arb = StatisticalArbitrage(exchanges)
        # self.synthetic_arb = SyntheticArbitrage(exchanges) # Placeholder for future
        self.ranker = ArbitrageRanker()
        self.hedge_executor = HedgeExecutor(exchanges, execution_engine, audit, self.db)

        self._running = False
        self._scan_interval = config.arbitrage.scan_interval_sec # High-frequency scan
        self._min_profit_bps = config.arbitrage.min_profit_bps
        self._core_symbols = config.arbitrage.core_symbols

    async def start(self) -> None:
        self._running = True
        await self.db.initialize()
        asyncio.create_task(self._scan_loop(), name="arbitrage_scan_loop")
        logger.info("Arbitrage Engine started across {} exchanges", len(self.exchanges))

    async def stop(self) -> None:
        self._running = False
        await self.db.close()
        logger.info("Arbitrage Engine stopped")

    async def _scan_loop(self) -> None:
        """Main scanning loop."""
        while self._running:
            try:
                # 1. Collect all opportunities concurrently
                opps_futures = asyncio.gather(
                    self._detect_cross_exchange_spreads(),
                    self.funding_arb.scan(),
                    self.basis_arb.scan(),
                    self.stat_arb.scan(),
                    # self.synthetic_arb.scan(), # Add when implemented
                    return_exceptions=True
                )
                
                results = await opps_futures

                # Flatten results and filter out exceptions
                all_opps: List[ArbitrageOpportunity] = []
                for res in results:
                    if isinstance(res, list):
                        all_opps.extend([o for o in res if isinstance(o, ArbitrageOpportunity)])
                    elif isinstance(res, Exception):
                        logger.error("Error in arbitrage scanner: {}", res)
                
                if not all_opps:
                    await asyncio.sleep(self._scan_interval)
                    continue

                # 2. Rank opportunities
                ranked_opportunities = self.ranker.rank_opportunities(all_opps)

                # 3. Execute top quality opportunity
                if ranked_opportunities:
                    best_opp = ranked_opportunities[0]
                    if best_opp.score >= config.arbitrage.min_execution_score:
                        await self._process_execution(best_opp)
                    else:
                        logger.debug("Top arbitrage opportunity score {} below threshold {}", 
                                     best_opp.score, config.arbitrage.min_execution_score)

            except Exception as e:
                logger.error("Arbitrage scan loop error: {}", e)
                await self.audit.system_event(
                    AuditEventType.SYSTEM_ERROR,
                    f"Arbitrage scan loop failed: {e}",
                    {"error": str(e)}
                )
            
            await asyncio.sleep(self._scan_interval)

    async def _detect_cross_exchange_spreads(self) -> List[ArbitrageOpportunity]:
        """Detects price spreads for the same symbol across exchanges."""
        opportunities = []
        
        for symbol in self._core_symbols:
            prices: Dict[str, float] = {}
            # Concurrently fetch mark prices from all exchanges
            tasks = {name: exch.get_mark_price(symbol) for name, exch in self.exchanges.items()}
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            
            for name, price in zip(tasks.keys(), results):
                if isinstance(price, (int, float)) and price > 0:
                    prices[name] = price

            if len(prices) < 2:
                continue

            # Find min and max price venues
            long_ex_name = min(prices, key=prices.get)
            short_ex_name = max(prices, key=prices.get)
            long_price = prices[long_ex_name]
            short_price = prices[short_ex_name]

            if long_price <= 0 or short_price <= 0:
                continue

            spread = (short_price - long_price) / long_price
            spread_bps = spread * 10000

            # Estimate fees and slippage (simplified for detection)
            # Actual fees/slippage will be calculated by SmartOrderRouter during execution
            estimated_fees_bps = self.exchanges[long_ex_name].get_taker_fee() * 10000 + \
                                 self.exchanges[short_ex_name].get_taker_fee() * 10000
            estimated_slippage_bps = config.arbitrage.estimated_slippage_bps
            
            net_edge_bps = spread_bps - estimated_fees_bps - estimated_slippage_bps

            if net_edge_bps > self._min_profit_bps:
                # Placeholder for position size, actual sizing by CapitalAllocationEngine
                position_size_usdt = config.arbitrage.default_position_size_usdt
                expected_profit = (net_edge_bps / 10000) * position_size_usdt

                opp = ArbitrageOpportunity(
                    arb_type="cross_exchange_spread",
                    symbol=symbol,
                    long_exchange=long_ex_name,
                    short_exchange=short_ex_name,
                    entry_spread_bps=spread_bps,
                    net_edge_bps=net_edge_bps,
                    expected_profit_usd=expected_profit,
                    expected_fee_usd=position_size_usdt * (estimated_fees_bps / 10000),
                    expected_slippage_usd=position_size_usdt * (estimated_slippage_bps / 10000),
                    confidence=min(1.0, net_edge_bps / 50.0), # Confidence based on net edge
                    timestamp=int(time.time() * 1000)
                )
                opportunities.append(opp)
                await self.db.save_opportunity(opp)
                logger.info("Cross-exchange spread found: {} | Net Edge: {:.2f} bps | Profit: ${:.2f}", 
                            symbol, opp.net_edge_bps, opp.expected_profit_usd)
        
        return opportunities

    async def _process_execution(self, opp: ArbitrageOpportunity) -> None:
        """Safety check and atomic hedge execution."""
        # 1. Pre-trade Safety Validation
        if not await self._verify_safety(opp):
            return

        # 2. Capital Allocation (sizing will be done by CapitalAllocationEngine)
        # The HedgeExecutor will request sizing from the ExecutionEngine, which uses the Allocator.
        
        # 3. Execute atomic hedge
        success = await self.hedge_executor.execute_atomic_hedge(opp)
        if success:
            logger.success(f"Arbitrage executed: {opp.symbol} | Type: {opp.arb_type} | Net Edge: {opp.net_edge_bps:.2f} bps")
            await self.audit.system_event(
                AuditEventType.ORDER_SUBMITTED,
                f"Arbitrage executed: {opp.symbol} ({opp.arb_type})",
                opp.to_dict()
            )

    async def _verify_safety(self, opp: ArbitrageOpportunity) -> bool:
        """Verify liquidity and spread persist before pulling trigger."""
        long_exch = self.exchanges[opp.long_exchange]
        short_exch = self.exchanges[opp.short_exchange]
        
        # 1. Refresh orderbooks
        books = await asyncio.gather(
            long_exch.get_orderbook(opp.symbol),
            short_exch.get_orderbook(opp.symbol)
        )
        
        # Check for exceptions in orderbook fetching
        if any(isinstance(b, Exception) for b in books):
            logger.warning("Arbitrage aborted: Failed to fetch orderbooks for {}", opp.symbol)
            return False

        long_book: OrderbookSnapshot = books[0] # type: ignore
        short_book: OrderbookSnapshot = books[1] # type: ignore

        # 2. Check if spread still exists on the top of book
        # We need to consider the actual bid/ask for the specific legs
        current_long_ask = long_book.best_ask
        current_short_bid = short_book.best_bid

        if current_long_ask <= 0 or current_short_bid <= 0:
            logger.warning("Arbitrage aborted: Invalid prices from orderbooks for {}", opp.symbol)
            return False

        current_spread_bps = ((current_short_bid - current_long_ask) / current_long_ask) * 10000
        
        # Re-estimate net edge with current prices and expected taker fees/slippage
        estimated_fees_bps = long_exch.get_taker_fee() * 10000 + short_exch.get_taker_fee() * 10000
        estimated_slippage_bps = config.arbitrage.estimated_slippage_bps # Assume same for verification
        
        net_edge_after_recheck_bps = current_spread_bps - estimated_fees_bps - estimated_slippage_bps

        if net_edge_after_recheck_bps < config.arbitrage.min_profit_bps:
            logger.warning("Arbitrage aborted: Edge collapsed during pre-trade validation. Current net edge: {:.2f} bps", net_edge_after_recheck_bps)
            return False
            
        return True

    def get_stats(self) -> Dict:
        """Returns aggregated statistics for the dashboard."""
        stats = self.hedge_executor.get_portfolio_stats()
        stats["active_scanners"] = {
            "funding": self.funding_arb.get_stats(),
            "basis": self.basis_arb.get_stats(),
            "statistical": self.stat_arb.get_stats(),
        }
        stats["opportunities_detected"] = self.db.get_opportunity_counts()
        return stats