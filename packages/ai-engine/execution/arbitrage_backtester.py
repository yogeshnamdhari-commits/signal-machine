"""
Arbitrage Backtester — Simulates arbitrage strategies on historical data.
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from typing import List, Dict, Any
from dataclasses import dataclass, asdict
from loguru import logger

@dataclass
class ArbitrageBacktestResult:
    total_pnl: float
    profit_factor: float
    win_rate: float
    max_drawdown: float
    arbitrage_type_attribution: Dict[str, float]
    exchange_attribution: Dict[str, float]

class ArbitrageBacktester:
    def __init__(self, initial_capital: float = 100000.0):
        self.initial_capital = initial_capital
        self.equity_curve = []
        self.trades = []

    async def run_simulation(
        self, 
        historical_market_data: Dict[str, pd.DataFrame], # "exchange:symbol" -> OHLCV
        arbitrage_strategy_fn: callable # Function that takes snapshot and returns opportunities
    ) -> ArbitrageBacktestResult:
        """
        Simulates arbitrage strategy performance.
        historical_market_data: Dictionary where keys are "exchange:symbol" and values are DataFrames.
        arbitrage_strategy_fn: A function that, given a timestamp and market snapshot,
                               returns a list of detected ArbitrageOpportunity objects.
        """
        current_equity = self.initial_capital
        arb_type_pnl: Dict[str, float] = {}
        exch_pnl: Dict[str, float] = {}

        # Get unified timeline from all market data
        all_timestamps = sorted(set().union(*(df.index for df in historical_market_data.values())))

        for ts in all_timestamps:
            # 1. Create a market snapshot for the current timestamp
            market_snapshot = {k: df.loc[ts] for k, df in historical_market_data.items() if ts in df.index}
            
            # 2. Detect arbitrage opportunities using the strategy function
            opportunities = arbitrage_strategy_fn(ts, market_snapshot)
            
            # 3. Simulate execution for each opportunity
            for opp in opportunities:
                # Simulate execution (simplified: assume fill at expected profit)
                pnl = opp.expected_profit_usd # Use expected profit as simulated PnL
                current_equity += pnl
                
                # Attribution
                arb_type_pnl[opp.arb_type] = arb_type_pnl.get(opp.arb_type, 0.0) + pnl
                exch_pnl[opp.long_exchange] = exch_pnl.get(opp.long_exchange, 0.0) + pnl/2
                exch_pnl[opp.short_exchange] = exch_pnl.get(opp.short_exchange, 0.0) + pnl/2
                
                self.trades.append({"ts": ts, "pnl": pnl, "arb_type": opp.arb_type, "long_ex": opp.long_exchange, "short_ex": opp.short_exchange})

            self.equity_curve.append({"ts": ts, "equity": current_equity})

        return self._calculate_metrics(current_equity, arb_type_pnl, exch_pnl)

    def _calculate_metrics(self, final_equity, arb_type_attr, exch_attr) -> ArbitrageBacktestResult:
        df_trades = pd.DataFrame(self.trades)
        if df_trades.empty:
            return ArbitrageBacktestResult(0, 0, 0, 0, {}, {})
            
        pos_pnl = df_trades[df_trades['pnl'] > 0]['pnl'].sum()
        neg_pnl = abs(df_trades[df_trades['pnl'] < 0]['pnl'].sum())
        pf = pos_pnl / neg_pnl if neg_pnl > 0 else float('inf')
        wr = len(df_trades[df_trades['pnl'] > 0]) / len(df_trades)
        
        df_equity = pd.DataFrame(self.equity_curve)
        dd = (df_equity['equity'].cummax() - df_equity['equity']) / df_equity['equity'].cummax()
        
        return ArbitrageBacktestResult(
            total_pnl=final_equity - self.initial_capital,
            profit_factor=round(pf, 2),
            win_rate=round(wr, 4),
            max_drawdown=round(dd.max(), 4),
            arbitrage_type_attribution=arb_type_attr,
            exchange_attribution=exch_attr
        )

    def save_reports(self, result: ArbitrageBacktestResult, path: str = "./data/reports"):
        """Export simulation results."""
        import os, json
        os.makedirs(path, exist_ok=True)
        with open(f"{path}/arbitrage_metrics.json", "w") as f:
            json.dump(asdict(result), f, indent=2)
        logger.info("Arbitrage backtest reports generated in {}", path)