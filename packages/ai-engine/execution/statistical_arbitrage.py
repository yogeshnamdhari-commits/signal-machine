"""
Statistical Arbitrage — Pair Engine for BTC/ETH, etc.

Detects opportunities by identifying temporary divergences in co-integrated asset pairs.
Uses Z-score of the price ratio to determine over-extension.
"""
from __future__ import annotations
import numpy as np
import time
from typing import Any, Dict, List
from loguru import logger

from exchanges.base_exchange import BaseExchange
from execution.arbitrage_ranker import ArbitrageOpportunity
from config import config

class StatisticalArbitrage:
    def __init__(self, exchanges: Dict[str, BaseExchange]):
        self.exchanges = exchanges
        self.pairs = config.arbitrage.statistical_pairs # e.g., [("BTCUSDT", "ETHUSDT")]
        self.windows: Dict[str, List[float]] = {} # Store rolling history of price ratios
        self.window_size = config.arbitrage.statistical_window_size
        self.zscore_threshold = config.arbitrage.statistical_zscore_threshold
        self._scan_count = 0

    async def scan(self) -> List[ArbitrageOpportunity]:
        opportunities = []
        self._scan_count += 1
        
        for asset_a, asset_b in self.pairs:
            pair_key = f"{asset_a}_{asset_b}"
            
            # Get prices from a primary exchange (e.g., Binance for both legs)
            # Statistical arbitrage typically assumes execution on the same exchange
            try:
                price_a = await self.exchanges['binance'].get_mark_price(asset_a)
                price_b = await self.exchanges['binance'].get_mark_price(asset_b)
                
                if price_a <= 0 or price_b <= 0:
                    continue

                ratio = price_a / price_b
                
                # Update rolling window
                if pair_key not in self.windows:
                    self.windows[pair_key] = []
                self.windows[pair_key].append(ratio)
                
                if len(self.windows[pair_key]) > self.window_size:
                    self.windows[pair_key].pop(0)
                    
                    # Calculate Z-Score
                    history = np.array(self.windows[pair_key])
                    mean = np.mean(history)
                    std = np.std(history)
                    zscore = (ratio - mean) / std if std > 0 else 0
                    
                    # Entry Trigger: |Z| > threshold
                    if abs(zscore) > self.zscore_threshold:
                        # If Z-score is positive, asset_a is relatively overvalued (short A, long B)
                        # If Z-score is negative, asset_a is relatively undervalued (long A, short B)
                        side_a = "SHORT" if zscore > 0 else "LONG"
                        side_b = "LONG" if zscore > 0 else "SHORT"
                        
                        opp = ArbitrageOpportunity(
                            arb_type="statistical_arbitrage",
                            symbol=pair_key, # Use pair key as symbol for stat arb
                            long_exchange="binance", # Both legs on same exchange
                            short_exchange="binance",
                            entry_spread_bps=zscore, # Z-score as spread proxy
                            net_edge_bps=abs(zscore) * config.arbitrage.statistical_edge_multiplier,
                            confidence=min(1.0, abs(zscore) / (self.zscore_threshold * 2)),
                            timestamp=int(time.time() * 1000),
                            meta={"asset_a": asset_a, "asset_b": asset_b, "zscore": zscore, "mean_ratio": mean}
                        )
                        opportunities.append(opp)
                        logger.debug("Stat arb detected: {} | Z-score: {:.2f} | Action: {} {} / {} {}",
                                     pair_key, zscore, side_a, asset_a, side_b, asset_b)
            except Exception as e:
                logger.warning("Stat arb scan error for {}: {}", pair_key, e)
        return opportunities

    def get_stats(self) -> Dict:
        return {"scan_count": self._scan_count, "active_pairs": len(self.pairs)}