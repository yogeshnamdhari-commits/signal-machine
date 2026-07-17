"""
Portfolio Backtester — Multi-symbol and multi-exchange strategy validation.
"""
from __future__ import annotations

import pandas as pd
import numpy as np
from typing import List, Dict, Any
from dataclasses import dataclass, asdict
from loguru import logger

@dataclass
class PortfolioResult:
    total_pnl: float
    profit_factor: float
    win_rate: float
    max_drawdown: float
    exchange_attribution: Dict[str, float]
    symbol_attribution: Dict[str, float]

class PortfolioBacktester:
    """
    Simulates portfolio performance across multiple assets and venues.
    """
    def __init__(self, initial_equity: float = 100000.0):
        self.initial_equity = initial_equity
        self.taker_fee = 0.0004
        self.slippage_bps = 2.0
        self.equity_curve: List[Dict] = []
        self.trade_history: List[Dict] = []

    async def run_simulation(
        self, 
        market_data: Dict[str, pd.DataFrame], # "exchange:symbol" -> OHLCV
        strategy_logic: Any
    ) -> PortfolioResult:
        """
        Executes the backtest over a unified timeline.
        """
        current_equity = self.initial_equity
        exch_pnl: Dict[str, float] = {}
        sym_pnl: Dict[str, float] = {}

        # Determine unified timeline
        all_timestamps = sorted(set().union(*(df.index for df in market_data.values())))

        for ts in all_timestamps:
            # 1. Capture snapshot of prices across universe
            snapshot = {key: df.loc[ts] for key, df in market_data.items() if ts in df.index}
            
            # 2. Get strategy signals
            signals = strategy_logic.get_signals(ts, snapshot, current_equity)
            
            # 3. Simulate execution per signal
            for signal in signals:
                pnl = self._process_fill(signal, snapshot)
                current_equity += pnl
                
                # Update attribution
                venue, sym = signal['exchange'], signal['symbol']
                exch_pnl[venue] = exch_pnl.get(venue, 0.0) + pnl
                sym_pnl[sym] = sym_pnl.get(sym, 0.0) + pnl
                
                self.trade_history.append({"ts": ts, "pnl": pnl, "venue": venue, "symbol": sym})

            self.equity_curve.append({"ts": ts, "equity": current_equity})

        return self._summarize_results(current_equity, exch_pnl, sym_pnl)

    def _process_fill(self, signal: Dict, snapshot: Dict) -> float:
        """
        Institutional-grade fill simulation.
        Calculates slippage and fees based on signal type.
        """
        symbol = signal['symbol']
        entry_price = signal.get('entry_price', 0)
        quantity = signal.get('quantity', 0)
        
        if quantity == 0 or entry_price == 0:
            return 0.0
            
        # Calculate costs
        slippage_cost = entry_price * quantity * (self.slippage_bps / 10000)
        fee_cost = entry_price * quantity * self.taker_fee
        
        # Expected PnL (gross)
        raw_pnl = signal.get('simulated_pnl', 0.0)
        
        # Net PnL = Gross - (Entry Cost + Exit Cost)
        # Assuming exit has similar slippage/fees
        total_costs = (slippage_cost + fee_cost) * 2
        
        return raw_pnl - total_costs

    def _summarize_results(self, final_equity: float, exch_attr: Dict, sym_attr: Dict) -> PortfolioResult:
        df_trades = pd.DataFrame(self.trade_history)
        if df_trades.empty:
            return PortfolioResult(0, 0, 0, 0, {}, {})
            
        pos_pnl = df_trades[df_trades['pnl'] > 0]['pnl'].sum()
        neg_pnl = abs(df_trades[df_trades['pnl'] < 0]['pnl'].sum())
        pf = pos_pnl / neg_pnl if neg_pnl > 0 else float('inf')
        wr = len(df_trades[df_trades['pnl'] > 0]) / len(df_trades)
        
        df_equity = pd.DataFrame(self.equity_curve)
        dd = (df_equity['equity'].cummax() - df_equity['equity']) / df_equity['equity'].cummax()
        
        return PortfolioResult(
            total_pnl=final_equity - self.initial_equity,
            profit_factor=round(pf, 2),
            win_rate=round(wr, 4),
            max_drawdown=round(dd.max(), 4),
            exchange_attribution=exch_attr,
            symbol_attribution=sym_attr
        )

    def save_reports(self, result: PortfolioResult, path: str = "./data/reports"):
        """Export simulation results."""
        import os, json
        os.makedirs(path, exist_ok=True)
        with open(f"{path}/portfolio_metrics.json", "w") as f:
            json.dump(asdict(result), f, indent=2)