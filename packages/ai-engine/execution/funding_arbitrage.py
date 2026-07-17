"""
Funding Rate Arbitrage — differential capture.

Detects opportunities by monitoring funding rates across multiple exchanges.
A positive funding rate means longs pay shorts, a negative rate means shorts pay longs.
Opportunity: LONG on exchange with low/negative funding, SHORT on exchange with high funding.
"""
from __future__ import annotations
import asyncio
import time
from typing import Any, Dict, List
from loguru import logger

from exchanges.base_exchange import BaseExchange, FundingInfo
from execution.arbitrage_ranker import ArbitrageOpportunity
from config import config

class FundingArbitrage:
    def __init__(self, exchanges: Dict[str, BaseExchange]):
        self.exchanges = exchanges
        self._min_funding_diff_bps = config.arbitrage.min_funding_diff_bps
        self._core_symbols = config.arbitrage.core_symbols
        self._scan_count = 0

    async def scan(self) -> List[ArbitrageOpportunity]:
        opportunities = []
        self._scan_count += 1
        
        for symbol in self._core_symbols:
            rates: Dict[str, float] = {}
            tasks = {name: exch.get_funding_rate(symbol) for name, exch in self.exchanges.items()}
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            
            for name, info in zip(tasks.keys(), results):
                if isinstance(info, FundingInfo):
                    rates[name] = info.funding_rate

            if len(rates) < 2:
                continue

            # Find exchanges with most extreme funding rates
            low_funding_ex = min(rates, key=rates.get)
            high_funding_ex = max(rates, key=rates.get)
            
            low_rate = rates[low_funding_ex]
            high_rate = rates[high_funding_ex]
            
            diff = high_rate - low_rate
            diff_bps = diff * 10000 # Convert to basis points

            if diff_bps > self._min_funding_diff_bps: 
                # Opportunity: Long on low_funding_ex, Short on high_funding_ex
                opp = ArbitrageOpportunity(
                    arb_type="funding_arbitrage",
                    symbol=symbol,
                    long_exchange=low_funding_ex,
                    short_exchange=high_funding_ex,
                    entry_spread_bps=diff_bps,
                    net_edge_bps=diff_bps, # Simplified, actual net edge needs to consider execution fees
                    expected_profit_usd=config.arbitrage.default_position_size_usdt * (diff_bps / 10000),
                    confidence=min(1.0, diff_bps / 100.0), # Confidence based on funding diff
                    timestamp=int(time.time() * 1000),
                    meta={"low_rate": low_rate, "high_rate": high_rate, "annualized_return": diff * 3 * 365}
                )
                opportunities.append(opp)
                logger.debug("Funding arb detected: {} | Diff: {:.2f} bps | Long: {} ({:.4%}) | Short: {} ({:.4%})",
                             symbol, diff_bps, low_funding_ex, low_rate, high_funding_ex, high_rate)
        return opportunities

    def get_stats(self) -> Dict:
        return {"scan_count": self._scan_count}