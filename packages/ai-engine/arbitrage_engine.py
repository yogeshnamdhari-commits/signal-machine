"""
Arbitrage Engine — Multi-exchange arbitrage orchestration.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional
from loguru import logger

from exchanges.base_exchange import BaseExchange, ExchangeOrderBook
from core.funding_arbitrage import FundingArbitrage
from core.basis_arbitrage import BasisArbitrage
from core.statistical_arbitrage import StatisticalArbitrage
from core.synthetic_arbitrage import SyntheticArbitrage
from core.arbitrage_ranker import ArbitrageRanker, ArbitrageOpportunity
from execution.hedge_executor import HedgeExecutor
from database.arbitrage_db import ArbitrageDB
from execution.execution_audit import ExecutionAudit, AuditEventType
from config import config


class ArbitrageEngine:
    """
    Orchestrates scanning, detection, ranking and execution of arbitrage.
    """
    def __init__(
        self, 
        exchanges: Dict[str, BaseExchange],
        execution_engine: Any,
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
        self.synthetic_arb = SyntheticArbitrage(exchanges)
        self.ranker = ArbitrageRanker()
        self.hedge_executor = HedgeExecutor(exchanges, execution_engine, audit, self.db)

        self._running = False
        self._scan_interval = config.arbitrage.scan_interval_sec
        self._core_symbols = config.arbitrage.core_symbols

    async def start(self) -> None:
        self._running = True
        await self.db.initialize()
        asyncio.create_task(self._scan_loop(), name="arbitrage_scan_loop")
        logger.info("Arbitrage Engine started across {} venues", len(self.exchanges))

    async def stop(self) -> None:
        self._running = False
        await self.db.close()
        logger.info("Arbitrage Engine stopped")

    async def _scan_loop(self) -> None:
        """High-frequency multi-strategy scanning loop."""
        while self._running:
            try:
                # 1. Collect all opportunities concurrently
                opps_futures = asyncio.gather(
                    self._detect_cross_exchange_spreads(),
                    self.funding_arb.scan(),
                    self.basis_arb.scan(),
                    self.stat_arb.scan(),
                    self.synthetic_arb.scan(),
                    return_exceptions=True
                )
                
                results = await opps_futures
                all_opps: List[ArbitrageOpportunity] = []
                for res in results:
                    if isinstance(res, list):
                        all_opps.extend([o for o in res if isinstance(o, ArbitrageOpportunity)])
                    elif isinstance(res, Exception):
                        logger.error("Arbitrage scanner failure: {}", res)
                
                if not all_opps:
                    await asyncio.sleep(self._scan_interval)
                    continue

                # 2. Rank and score opportunities
                ranked = self.ranker.rank_opportunities(all_opps)

                # 3. Execute top quality opportunity if above threshold
                if ranked:
                    best_opp = ranked[0]
                    if best_opp.score >= config.arbitrage.min_execution_score:
                        await self._process_execution(best_opp)

            except Exception as e:
                logger.error("Arbitrage loop error: {}", e)
            
            await asyncio.sleep(self._scan_interval)

    async def _detect_cross_exchange_spreads(self) -> List[ArbitrageOpportunity]:
        """Identify standard price discrepancies for the same asset."""
        opportunities = []
        for symbol in self._core_symbols:
            prices: Dict[str, float] = {}
            tasks = {name: exch.get_mark_price(symbol) for name, exch in self.exchanges.items()}
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            
            for name, price in zip(tasks.keys(), results):
                if isinstance(price, (int, float)) and price > 0:
                    prices[name] = price

            if len(prices) < 2: continue

            long_ex = min(prices, key=prices.get)
            short_ex = max(prices, key=prices.get)
            spread_bps = ((prices[short_ex] - prices[long_ex]) / prices[long_ex]) * 10000

            # Preliminary edge calculation
            total_fees = (self.exchanges[long_ex].get_taker_fee() + self.exchanges[short_ex].get_taker_fee()) * 10000
            net_edge = spread_bps - total_fees - config.arbitrage.estimated_slippage_bps

            if net_edge > config.arbitrage.min_profit_bps:
                opp = ArbitrageOpportunity(
                    arb_type="cross_exchange",
                    symbol=symbol,
                    long_exchange=long_ex,
                    short_exchange=short_ex,
                    long_exchange_price=prices[long_ex],
                    short_exchange_price=prices[short_ex],
                    entry_spread_bps=spread_bps,
                    net_edge_bps=net_edge,
                    expected_profit_usd=(net_edge / 10000) * config.arbitrage.default_position_size_usdt,
                    confidence=min(1.0, net_edge / 50.0),
                    timestamp=int(time.time() * 1000)
                )
                opportunities.append(opp)
                await self.db.save_opportunity(opp)
        
        return opportunities

    async def _process_execution(self, opp: ArbitrageOpportunity) -> None:
        """Pre-trade validation and atomic multi-leg execution."""
        if not await self._verify_safety(opp):
            return

        # Sizing and Risk are handled within the HedgeExecutor/ExecutionEngine pipeline
        success = await self.hedge_executor.execute_atomic_hedge(opp)
        if success:
            logger.success(f"Arb Executed: {opp.symbol} [{opp.arb_type}] Edge: {opp.net_edge_bps:.1f} bps")

    async def _verify_safety(self, opp: ArbitrageOpportunity) -> bool:
        """Verify liquidity and spread persist in orderbooks before fire."""
        try:
            books = await asyncio.gather(
                self.exchanges[opp.long_exchange].get_orderbook(opp.symbol),
                self.exchanges[opp.short_exchange].get_orderbook(opp.symbol)
            )
            
            current_spread = ((books[1].best_bid - books[0].best_ask) / books[0].best_ask) * 10000
            taker_cost = (self.exchanges[opp.long_exchange].get_taker_fee() + 
                          self.exchanges[opp.short_exchange].get_taker_fee()) * 10000
            
            if current_spread - taker_cost < config.arbitrage.min_profit_bps:
                logger.warning(f"Arb aborted: Edge collapsed to {current_spread:.1f} bps during re-check")
                return False
                
            return True
        except Exception as e:
            logger.error(f"Safety re-check failed: {e}")
            return False

    def get_stats(self) -> Dict:
        stats = self.hedge_executor.get_portfolio_stats()
        stats.update({
            "opportunities_detected": self.db.get_opportunity_counts(),
            "active_scanners": ["funding", "basis", "statistical", "synthetic"]
        })
        return stats