"""
Order Flow Analytics — real aggressive buying/selling from Binance aggTrades.

Data Source: Binance Futures AggTrade WebSocket
  - m = false → Aggressive Buy (buyer initiated)
  - m = true  → Aggressive Sell (seller initiated)

CRITICAL: Synthetic trades from !ticker@arr are EXCLUDED.
They carry fake quantities and would inflate flow beyond actual volume.

Order Flow Calculation:
  - buy_volume += price × qty  (when m == false)
  - sell_volume += price × qty (when m == true)
  - net_flow = buy_volume - sell_volume
  - flow_ratio = buy_volume / (buy_volume + sell_volume)

Order Flow Signal (spec):
  - flow_ratio > 0.55 → BUY
  - flow_ratio < 0.45 → SELL
  - otherwise         → NEUTRAL

Flow Strength: percentile ranking (0-100) based on recent net_flow distribution.

Absorption Detection:
  - Aggressive buying + price not moving higher → Sell Absorption
  - Aggressive selling + price not moving lower → Buy Absorption

Sweep Detection:
  - Large cluster of aggressive trades within 1-3 seconds → Buy/Sell Sweep
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
from loguru import logger

# ── Configuration ──
_ROLLING_WINDOW_TRADES = 2000       # trades for rolling buy/sell accumulation
_LARGE_TRADE_USD = 10_000           # threshold for large taker order tracking
_FLOW_RATIO_BUY = 0.55             # flow_ratio > this → BUY signal
_FLOW_RATIO_SELL = 0.45            # flow_ratio < this → SELL signal
_MIN_TRADES_FOR_SIGNAL = 20        # minimum trades before generating a signal
_ABSORPTION_STABILITY = 0.005      # 0.5% price tolerance for absorption
_ABSORPTION_MIN_VOLUME = 10_000    # minimum volume for absorption detection
_SWEEP_CLUSTER_WINDOW = 3.0        # seconds for sweep cluster detection
_SWEEP_MIN_TRADES = 5              # minimum trades in cluster for sweep
_SWEEP_MIN_VOLUME = 50_000         # minimum USD volume in cluster for sweep


@dataclass
class OFState:
    symbol: str
    # ── Rolling window trades (most recent _ROLLING_WINDOW_TRADES) ──
    trades: deque = field(default_factory=lambda: deque(maxlen=_ROLLING_WINDOW_TRADES))
    # ── Rolling volumes (computed from window, NOT cumulative) ──
    buy_vol: float = 0.0
    sell_vol: float = 0.0
    cum_delta: float = 0.0
    vwap: float = 0.0
    vwap_vol: float = 0.0
    vwap_sum: float = 0.0
    # ── Snapshot history for delta trend ──
    snapshots: List[Dict] = field(default_factory=list)
    # ── Absorption tracking ──
    absorption_events: deque = field(default_factory=lambda: deque(maxlen=100))
    last_absorption_side: str = "none"
    # ── Sweep tracking ──
    sweep_events: deque = field(default_factory=lambda: deque(maxlen=100))
    last_sweep_side: str = "none"
    # ── Debug ──
    total_trades: int = 0
    last_update_ts: float = 0.0
    flow_ratio: float = 0.5
    flow_signal: str = "neutral"
    flow_strength_score: float = 50.0
    # ── History for percentile ranking ──
    net_flow_history: deque = field(default_factory=lambda: deque(maxlen=10000))


class OrderFlowAnalyzer:
    """
    Order flow analytics using ONLY real aggTrade data.

    Excludes synthetic trades from !ticker@arr (tagged with _source="ticker_arr").
    Uses rolling window for buy/sell volumes (not cumulative-all-time).
    Flow strength is percentile-ranked over recent distribution.
    """

    def __init__(self) -> None:
        self._states: Dict[str, OFState] = {}
        self._last_snap: Dict[str, float] = {}
        self._last_absorption: Dict[str, float] = {}
        self._last_sweep: Dict[str, float] = {}

    async def initialize(self) -> None:
        logger.info("OrderFlow analyzer ready — real aggTrade only, no synthetic")

    async def process_trade(self, symbol: str, trade: Dict) -> None:
        """
        Process a SINGLE real aggTrade event.

        Excludes synthetic trades (from !ticker@arr) which have _source="ticker_arr".
        """
        # ── CRITICAL: Skip synthetic trades ──
        if trade.get("_source") == "ticker_arr":
            return

        st = self._states.setdefault(symbol, OFState(symbol=symbol))
        price = trade.get("price", 0)
        qty = trade.get("quantity", 0)
        is_maker = trade.get("is_buyer_maker", False)
        trade_time = trade.get("trade_time", int(time.time() * 1000))

        if price <= 0 or qty <= 0:
            return

        val = price * qty
        now = time.time()

        # ── Add to rolling window ──
        st.trades.append(trade)
        st.total_trades += 1
        st.last_update_ts = trade_time / 1000 if trade_time > 1e10 else trade_time

        # ── Recalculate rolling volumes from the window ──
        st.buy_vol = sum(t["price"] * t["quantity"] for t in st.trades if not t["is_buyer_maker"])
        st.sell_vol = sum(t["price"] * t["quantity"] for t in st.trades if t["is_buyer_maker"])
        total = st.buy_vol + st.sell_vol
        st.cum_delta = st.buy_vol - st.sell_vol
        st.flow_ratio = st.buy_vol / total if total > 0 else 0.5

        # ── VWAP ──
        st.vwap_sum += price * qty
        st.vwap_vol += qty
        if st.vwap_vol > 0:
            st.vwap = st.vwap_sum / st.vwap_vol

        # ── Record net_flow snapshot for percentile ranking ──
        st.net_flow_history.append({
            "net_flow": st.cum_delta,
            "ts": now,
        })
        # Prune old entries (> 1h for percentile)
        cutoff = now - 3600
        while st.net_flow_history and st.net_flow_history[0]["ts"] < cutoff:
            st.net_flow_history.popleft()

        # ── Flow signal (spec thresholds) ──
        if st.total_trades < _MIN_TRADES_FOR_SIGNAL:
            st.flow_signal = "neutral"
        elif st.flow_ratio > _FLOW_RATIO_BUY:
            st.flow_signal = "buy"
        elif st.flow_ratio < _FLOW_RATIO_SELL:
            st.flow_signal = "sell"
        else:
            st.flow_signal = "neutral"

        # ── Flow strength: percentile ranking of current net_flow ──
        history = [h["net_flow"] for h in st.net_flow_history]
        if len(history) >= 10:
            arr = np.array(history)
            st.flow_strength_score = round(
                float(np.sum(arr < st.cum_delta) / len(arr) * 100), 1
            )
        else:
            ratio_dev = abs(st.flow_ratio - 0.5) * 2
            direction = 1 if st.flow_ratio > 0.5 else -1
            st.flow_strength_score = round(max(0, min(100, 50 + direction * ratio_dev * 50)), 1)

        # ── Snapshot every 30s for delta trend ──
        if now - self._last_snap.get(symbol, 0) >= 30:
            self._take_snapshot(symbol, now)
            self._last_snap[symbol] = now

        # ── Absorption detection (every 5s cooldown) ──
        if now - self._last_absorption.get(symbol, 0) >= 5:
            self._detect_absorption(symbol, st, now)
            self._last_absorption[symbol] = now

        # ── Sweep detection (every 3s cooldown) ──
        if now - self._last_sweep.get(symbol, 0) >= 3:
            self._detect_sweep(symbol, st, now)
            self._last_sweep[symbol] = now

    def _take_snapshot(self, symbol: str, now: float) -> None:
        """Take a snapshot for delta trend analysis (60s lookback)."""
        st = self._states.get(symbol)
        if not st:
            return
        recent = [t for t in st.trades if now - (t["trade_time"] / 1000 if t["trade_time"] > 1e10 else t["trade_time"]) < 60]
        if not recent:
            return

        buy_v = sum(t["price"] * t["quantity"] for t in recent if not t["is_buyer_maker"])
        sell_v = sum(t["price"] * t["quantity"] for t in recent if t["is_buyer_maker"])
        total = buy_v + sell_v
        delta = buy_v - sell_v
        imbalance = delta / total if total else 0

        large_buys = sum(1 for t in recent if not t["is_buyer_maker"] and t["price"] * t["quantity"] >= _LARGE_TRADE_USD)
        large_sells = sum(1 for t in recent if t["is_buyer_maker"] and t["price"] * t["quantity"] >= _LARGE_TRADE_USD)

        st.snapshots.append({
            "ts": now, "buy_vol": buy_v, "sell_vol": sell_v,
            "delta": delta, "imbalance": imbalance,
            "large_buys": large_buys, "large_sells": large_sells,
            "total_vol": total, "avg_size": total / len(recent) if recent else 0,
        })
        if len(st.snapshots) > 50:
            st.snapshots = st.snapshots[-25:]

    def _detect_absorption(self, symbol: str, st: OFState, now: float) -> None:
        """
        Detect absorption: aggressive buying + price not moving higher → Sell Absorption
        Or: aggressive selling + price not moving lower → Buy Absorption
        """
        # Need at least 10 recent trades
        recent = [t for t in st.trades if now - (t["trade_time"] / 1000 if t["trade_time"] > 1e10 else t["trade_time"]) < 10]
        if len(recent) < 10:
            return

        prices = [t["price"] for t in recent]
        volumes = [t["price"] * t["quantity"] for t in recent]
        total_vol = sum(volumes)

        if total_vol < _ABSORPTION_MIN_VOLUME:
            return

        # Check price stability
        price_range = max(prices) - min(prices)
        mid_price = np.mean(prices)
        stability = 1 - (price_range / mid_price) if mid_price > 0 else 0

        if stability < (1 - _ABSORPTION_STABILITY):
            return  # Price moved too much — not absorption

        # Count aggressive buy vs sell trades
        buy_trades = [t for t in recent if not t["is_buyer_maker"]]
        sell_trades = [t for t in recent if t["is_buyer_maker"]]
        buy_vol = sum(t["price"] * t["quantity"] for t in buy_trades)
        sell_vol = sum(t["price"] * t["quantity"] for t in sell_trades)

        side = "none"
        confidence = 0

        # Sell Absorption: aggressive buying absorbed (price stable despite buy pressure)
        if buy_vol > sell_vol * 1.5 and stability > 0.95:
            side = "sell_absorption"
            confidence = min(stability * (buy_vol / total_vol), 1.0)
        # Buy Absorption: aggressive selling absorbed (price stable despite sell pressure)
        elif sell_vol > buy_vol * 1.5 and stability > 0.95:
            side = "buy_absorption"
            confidence = min(stability * (sell_vol / total_vol), 1.0)

        if confidence > 0.25:
            event = {
                "side": side,
                "confidence": round(confidence, 3),
                "price_level": round(mid_price, 4),
                "volume": round(total_vol, 2),
                "stability": round(stability, 4),
                "ts": now,
            }
            st.absorption_events.append(event)
            st.last_absorption_side = side

    def _detect_sweep(self, symbol: str, st: OFState, now: float) -> None:
        """
        Detect sweep: large cluster of aggressive trades within 1-3 seconds.
        """
        # Find trades in the last 3 seconds
        recent = [t for t in st.trades if now - (t["trade_time"] / 1000 if t["trade_time"] > 1e10 else t["trade_time"]) < _SWEEP_CLUSTER_WINDOW]
        if len(recent) < _SWEEP_MIN_TRADES:
            return

        total_vol = sum(t["price"] * t["quantity"] for t in recent)
        if total_vol < _SWEEP_MIN_VOLUME:
            return

        buy_vol = sum(t["price"] * t["quantity"] for t in recent if not t["is_buyer_maker"])
        sell_vol = sum(t["price"] * t["quantity"] for t in recent if t["is_buyer_maker"])

        side = "none"
        confidence = 0

        if buy_vol > sell_vol * 2.0:
            side = "buy_sweep"
            confidence = min(buy_vol / total_vol, 1.0)
        elif sell_vol > buy_vol * 2.0:
            side = "sell_sweep"
            confidence = min(sell_vol / total_vol, 1.0)

        if confidence > 0.3:
            event = {
                "side": side,
                "confidence": round(confidence, 3),
                "trade_count": len(recent),
                "volume": round(total_vol, 2),
                "duration": _SWEEP_CLUSTER_WINDOW,
                "ts": now,
            }
            st.sweep_events.append(event)
            st.last_sweep_side = side

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        st = self._states.get(symbol)
        if not st or not st.snapshots:
            return None
        latest = st.snapshots[-1]

        # Delta trend
        if len(st.snapshots) >= 5:
            deltas = [s["delta"] for s in st.snapshots[-5:]]
            delta_trend = float(np.mean(np.diff(deltas))) if len(deltas) > 1 else 0
        else:
            delta_trend = 0

        # Recent absorption
        now = time.time()
        recent_abs = [e for e in st.absorption_events if now - e["ts"] < 3600]
        recent_sweep = [e for e in st.sweep_events if now - e["ts"] < 3600]

        # Absorption summary
        buy_abs = sum(1 for e in recent_abs if e["side"] == "buy_absorption")
        sell_abs = sum(1 for e in recent_abs if e["side"] == "sell_absorption")
        if buy_abs > sell_abs:
            absorption_label = "bullish_absorption"
        elif sell_abs > buy_abs:
            absorption_label = "bearish_absorption"
        else:
            absorption_label = "none"

        # Sweep summary
        buy_sweeps = sum(1 for e in recent_sweep if e["side"] == "buy_sweep")
        sell_sweeps = sum(1 for e in recent_sweep if e["side"] == "sell_sweep")
        if buy_sweeps > sell_sweeps:
            sweep_label = "buy_sweep"
        elif sell_sweeps > buy_sweeps:
            sweep_label = "sell_sweep"
        else:
            sweep_label = "none"

        return {
            "symbol": symbol,
            "buy_volume": st.buy_vol,
            "sell_volume": st.sell_vol,
            "delta": st.cum_delta,
            "cumulative_delta": st.cum_delta,
            "imbalance": latest["imbalance"],
            "flow_ratio": st.flow_ratio,
            "flow_signal": st.flow_signal,
            "flow_strength_score": st.flow_strength_score,
            "large_buy_trades": latest["large_buys"],
            "large_sell_trades": latest["large_sells"],
            "avg_trade_size": latest["avg_size"],
            "delta_trend": delta_trend,
            "vwap": st.vwap,
            # Absorption
            "absorption": absorption_label,
            "absorption_events": len(recent_abs),
            # Sweep
            "sweep": sweep_label,
            "sweep_events": len(recent_sweep),
            # Debug
            "total_trades": st.total_trades,
            # Backward-compatible
            "signal_strength": st.flow_strength_score / 100.0,
        }
