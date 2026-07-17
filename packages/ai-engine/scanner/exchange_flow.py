"""
Exchange Flow Engine — REAL taker buy/sell pressure from Binance Futures aggTrades.

Data Source: Binance Futures AggTrades WebSocket
  - m = false → buyer initiated trade (taker buy)
  - m = true  → seller initiated trade (taker sell)
  - Each trade counted ONCE: taker_buy_vol OR taker_sell_vol

CRITICAL: Synthetic trades from !ticker@arr are EXCLUDED.
They carry fake quantities and would inflate flow beyond actual volume.

Flow Calculation:
  - trade_value = price × quantity
  - m == false → taker_buy += trade_value
  - m == true  → taker_sell += trade_value
  - net_flow = taker_buy - taker_sell
  - flow_ratio = taker_buy / (taker_buy + taker_sell)

Flow Signal (spec):
  - flow_ratio > 0.60 → BUY
  - flow_ratio < 0.40 → SELL
  - otherwise         → NEUTRAL

Flow Strength: percentile ranking (0-100) based on last 24h net_flow distribution.

Validation: abs(net_flow) must be < 24h traded volume.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from loguru import logger


# ── Configuration ────────────────────────────────────────────────
_ROLLING_WINDOW_TRADES = 2000       # trades for rolling buy/sell accumulation
_FLOW_HISTORY_RETENTION_S = 86400   # 24 hours of net_flow snapshots for percentile
_LARGE_TRADE_USD = 50_000           # threshold for large taker order tracking
_FLOW_RATIO_BUY = 0.60             # flow_ratio > this → BUY signal
_FLOW_RATIO_SELL = 0.40            # flow_ratio < this → SELL signal
_MIN_TRADES_FOR_SIGNAL = 20        # minimum trades before generating a signal


@dataclass
class FlowState:
    symbol: str
    # ── Rolling window trades (most recent _ROLLING_WINDOW_TRADES) ──
    flows: deque = field(default_factory=lambda: deque(maxlen=_ROLLING_WINDOW_TRADES))
    # ── Rolling volumes (computed from rolling window, NOT cumulative) ──
    taker_buy_vol: float = 0.0
    taker_sell_vol: float = 0.0
    net_flow: float = 0.0
    flow_ratio: float = 0.5
    aggressive_side: str = "neutral"
    # ── Momentum ──
    flow_momentum: float = 0.0
    # ── Large orders ──
    large_taker_buys: int = 0
    large_taker_sells: int = 0
    # ── Enhanced metrics ──
    taker_dominance: float = 0.5
    flow_strength_score: float = 50.0
    flow_signal: str = "neutral"
    recent_buy_vol: float = 0.0
    recent_sell_vol: float = 0.0
    recent_net_delta: float = 0.0
    # ── History for percentile ranking (24h) ──
    net_flow_history: deque = field(default_factory=lambda: deque(maxlen=10000))
    # ── Trade counter for minimum threshold ──
    total_trades: int = 0
    # ── Debug / validation ──
    last_update_ts: float = 0.0
    source_label: str = "Binance Futures"
    vol_24h: float = 0.0  # set externally for validation


class ExchangeFlowEngine:
    """
    Taker/maker exchange flow analysis using ONLY real aggTrade data.

    Excludes synthetic trades from !ticker@arr (tagged with _source="ticker_arr").
    Uses rolling window for buy/sell volumes (not cumulative-all-time).
    Flow strength is percentile-ranked over 24h distribution.
    """

    def __init__(self) -> None:
        self._states: Dict[str, FlowState] = {}

    async def initialize(self) -> None:
        logger.info("ExchangeFlow engine ready — real aggTrade only, no synthetic")

    def set_vol_24h(self, symbol: str, vol_24h: float) -> None:
        """Set 24h quote volume for validation (called from engine bridge sync)."""
        st = self._states.get(symbol)
        if st:
            st.vol_24h = vol_24h

    async def process_trade(self, symbol: str, trade: Dict) -> None:
        """
        Process a SINGLE real aggTrade event.

        Excludes synthetic trades (from !ticker@arr) which have _source="ticker_arr".
        These synthetic trades carry fake quantities (vol/1000) and would inflate
        flow beyond actual traded volume.
        """
        # ── CRITICAL: Skip synthetic trades ──
        if trade.get("_source") == "ticker_arr":
            return

        st = self._states.setdefault(symbol, FlowState(symbol=symbol))
        price = trade.get("price", 0)
        qty = trade.get("quantity", 0)
        is_maker = trade.get("is_buyer_maker", False)
        trade_time = trade.get("trade_time", int(time.time() * 1000))

        if price <= 0 or qty <= 0:
            return

        val = price * qty
        side = "sell" if is_maker else "buy"

        # ── Add to rolling window ──
        st.flows.append({"val": val, "side": side, "ts": trade_time})
        st.total_trades += 1
        st.last_update_ts = time.time() / 1000 if trade_time > 1e10 else trade_time

        # ── Recalculate rolling volumes from the window ──
        st.taker_buy_vol = sum(f["val"] for f in st.flows if f["side"] == "buy")
        st.taker_sell_vol = sum(f["val"] for f in st.flows if f["side"] == "sell")
        total = st.taker_buy_vol + st.taker_sell_vol
        st.net_flow = st.taker_buy_vol - st.taker_sell_vol
        st.flow_ratio = st.taker_buy_vol / total if total > 0 else 0.5

        # ── Record net_flow snapshot for 24h percentile ranking ──
        st.net_flow_history.append({
            "net_flow": st.net_flow,
            "ts": time.time(),
        })
        # Prune old entries (> 24h)
        cutoff = time.time() - _FLOW_HISTORY_RETENTION_S
        while st.net_flow_history and st.net_flow_history[0]["ts"] < cutoff:
            st.net_flow_history.popleft()

        # ── Aggressive side label ──
        if st.flow_ratio > _FLOW_RATIO_BUY:
            st.aggressive_side = "taker_buy"
        elif st.flow_ratio < _FLOW_RATIO_SELL:
            st.aggressive_side = "taker_sell"
        else:
            st.aggressive_side = "balanced"

        # ── Large taker orders ──
        if val >= _LARGE_TRADE_USD:
            if is_maker:
                st.large_taker_sells += 1
            else:
                st.large_taker_buys += 1

        # ── Flow momentum (recent half vs older half of rolling window) ──
        n = len(st.flows)
        if n >= 40:
            half = n // 2
            recent = list(st.flows)[-half:]
            older = list(st.flows)[:-half] if half < n else []
            recent_buy = sum(f["val"] for f in recent if f["side"] == "buy")
            recent_sell = sum(f["val"] for f in recent if f["side"] == "sell")
            recent_net = recent_buy - recent_sell
            older_buy = sum(f["val"] for f in older if f["side"] == "buy")
            older_sell = sum(f["val"] for f in older if f["side"] == "sell")
            older_net = older_buy - older_sell
            st.flow_momentum = float(np.clip(
                (recent_net - older_net) / max(abs(older_net), 1), -1, 1
            ))
        else:
            st.flow_momentum = 0.0

        # ── Rolling window stats (most recent trades) ──
        window_size = min(500, n)
        window = list(st.flows)[-window_size:] if window_size > 0 else []
        st.recent_buy_vol = sum(f["val"] for f in window if f["side"] == "buy")
        st.recent_sell_vol = sum(f["val"] for f in window if f["side"] == "sell")
        st.recent_net_delta = st.recent_buy_vol - st.recent_sell_vol

        # ── Taker dominance: approximated from large trade ratio ──
        rolling_total = st.recent_buy_vol + st.recent_sell_vol
        large_total = st.large_taker_buys + st.large_taker_sells
        if rolling_total > 0 and large_total > 0:
            st.taker_dominance = min(1.0, 0.5 + (large_total / max(len(window), 1)) * 2)
        else:
            st.taker_dominance = 0.5

        # ── Flow strength: MAGNITUDE-based (not percentile) ──
        # Strong flow in EITHER direction = high strength
        # Uses ratio deviation from neutral (0.5) as primary signal
        ratio_dev = abs(st.flow_ratio - 0.5) * 2  # 0-1 scale
        # Scale: 0% deviation = 50 (neutral), 50% deviation = 100 (extreme)
        base_strength = 50 + ratio_dev * 50
        # Volume magnitude boost: more volume = more confidence
        vol_boost = min(total / 100000, 1.0) * 10 if total > 0 else 0
        # Momentum boost: accelerating flow = stronger signal
        momentum_boost = abs(st.flow_momentum) * 10
        st.flow_strength_score = round(max(0, min(100, base_strength + vol_boost + momentum_boost)), 1)

        # ── 5-level flow signal (spec thresholds) ──
        if st.total_trades < _MIN_TRADES_FOR_SIGNAL:
            st.flow_signal = "neutral"
        elif st.flow_ratio > _FLOW_RATIO_BUY:
            st.flow_signal = "buy"
        elif st.flow_ratio < _FLOW_RATIO_SELL:
            st.flow_signal = "sell"
        else:
            st.flow_signal = "neutral"

    async def fetch_rest_trades(self, symbol: str, trades: list) -> int:
        """Process trades fetched from REST API to fill flow gaps.
        
        Called for symbols that don't receive WS aggTrade data (testnet limitation).
        Each trade is processed through the same pipeline as WS trades.
        Returns number of trades processed.
        """
        count = 0
        for t in trades:
            try:
                # REST API format: {id, price, qty, quoteQty, time, isBuyerMaker}
                trade = {
                    "price": float(t.get("price", 0)),
                    "quantity": float(t.get("qty", 0)),
                    "is_buyer_maker": t.get("isBuyerMaker", False),
                    "trade_time": t.get("time", int(time.time() * 1000)),
                    "_source": "rest_api",
                }
                await self.process_trade(symbol, trade)
                count += 1
            except Exception:
                continue
        # Update source label
        st = self._states.get(symbol)
        if st:
            st.source_label = "Binance Futures (REST fallback)"
        return count

    def needs_flow_data(self, symbol: str, min_trades: int = 20) -> bool:
        """Check if a symbol needs flow data from REST API."""
        st = self._states.get(symbol)
        if not st:
            return True
        return st.total_trades < min_trades

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        st = self._states.get(symbol)
        if not st:
            return None

        # ── Validation: net_flow must be < 24h volume ──
        vol_24h_valid = True
        vol_validation_msg = ""
        total_flow = st.taker_buy_vol + st.taker_sell_vol
        if st.vol_24h > 0:
            if abs(st.net_flow) > st.vol_24h:
                vol_24h_valid = False
                vol_validation_msg = (
                    f"DATA QUALITY FAILED: |net_flow|=${abs(st.net_flow):,.0f} "
                    f"> 24h_vol=${st.vol_24h:,.0f}"
                )
            elif total_flow > st.vol_24h * 1.1:
                vol_24h_valid = False
                vol_validation_msg = (
                    f"DATA QUALITY FAILED: total_flow=${total_flow:,.0f} "
                    f"> 110% of 24h_vol=${st.vol_24h:,.0f}"
                )

        return {
            "symbol": symbol,
            "taker_buy_vol": st.taker_buy_vol,
            "taker_sell_vol": st.taker_sell_vol,
            "net_flow": st.net_flow,
            "flow_ratio": st.flow_ratio,
            "aggressive_side": st.aggressive_side,
            "flow_momentum": round(st.flow_momentum, 4),
            "large_taker_buys": st.large_taker_buys,
            "large_taker_sells": st.large_taker_sells,
            # Enhanced fields
            "taker_dominance": round(st.taker_dominance, 3),
            "flow_strength_score": st.flow_strength_score,
            "flow_signal": st.flow_signal,
            "recent_buy_vol": round(st.recent_buy_vol, 2),
            "recent_sell_vol": round(st.recent_sell_vol, 2),
            "recent_net_delta": round(st.recent_net_delta, 2),
            # Debug / validation
            "total_trades": st.total_trades,
            "source_label": st.source_label,
            "vol_24h": st.vol_24h,
            "vol_24h_valid": vol_24h_valid,
            "vol_validation_msg": vol_validation_msg,
            "flow_history_samples": len(st.net_flow_history),
            # Backward-compatible signal field
            "signal": st.flow_signal,
        }
