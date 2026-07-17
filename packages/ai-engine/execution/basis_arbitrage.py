"""
Basis Arbitrage — Spot vs Perpetual Convergence.

Detects opportunities by comparing the price of a perpetual future to its underlying spot price.
A positive basis (futures > spot) suggests a "cash and carry" trade: buy spot, short futures.
A negative basis (spot > futures) suggests a "reverse cash and carry": short spot, long futures.
"""
from __future__ import annotations
import time
from typing import Any, Dict, List
from loguru import logger

from exchanges.base_exchange import BaseExchange
from execution.arbitrage_ranker import ArbitrageOpportunity
from config import config

class BasisArbitrage:
    def __init__(self, exchanges: Dict[str, BaseExchange]):
        self.exchanges = exchanges
        self._min_basis_bps = config.arbitrage.min_basis_bps
        self._core_symbols = config.arbitrage.core_symbols
        self._scan_count = 0

    async def scan(self) -> List[ArbitrageOpportunity]:
        opportunities = []
        self._scan_count += 1
        
        for symbol in self._core_symbols:
            # For now, we'll simulate spot price from Binance's mark price and compare with Delta's mark price
            # In Phase 7, a dedicated spot exchange adapter would provide the true spot price.
            try:
                binance_futures_price = await self.exchanges['binance'].get_mark_price(symbol)
                delta_futures_price = await self.exchanges['delta'].get_mark_price(symbol)
                
                # Use Binance as a proxy for "spot" for now, comparing against another futures exchange
                # This is a simplification for multi-exchange basis arb detection.
                spot_proxy_price = binance_futures_price 
                futures_price = delta_futures_price

                if spot_proxy_price <= 0 or futures_price <= 0:
                    continue

                basis = (futures_price - spot_proxy_price) / spot_proxy_price
                basis_bps = basis * 10000
                
                if abs(basis_bps) > self._min_basis_bps:
                    opp = ArbitrageOpportunity(
                        arb_type="basis_arbitrage",
                        symbol=symbol,
                        long_exchange="binance" if basis > 0 else "delta", # Buy spot proxy, sell futures
                        short_exchange="delta" if basis > 0 else "binance", # Sell spot proxy, buy futures
                        entry_spread_bps=basis_bps,
                        net_edge_bps=abs(basis_bps) - config.arbitrage.estimated_slippage_bps,
                        confidence=min(1.0, abs(basis_bps) / 100.0),
                        timestamp=int(time.time() * 1000)
                    )
                    opportunities.append(opp)
            except Exception as e:
                logger.warning("Basis arb scan error for {}: {}", symbol, e)
        return opportunities

    def get_stats(self) -> Dict:
        return {"scan_count": self._scan_count}