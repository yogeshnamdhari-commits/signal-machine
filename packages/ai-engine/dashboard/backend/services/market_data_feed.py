"""
Real-Time Market Data Feed — Simulated live market data for the dashboard.

Generates realistic price movements, volume spikes, orderbook depth,
and trade flow data for multiple crypto pairs. Uses geometric Brownian
motion with regime switching for authentic market behavior.
"""
from __future__ import annotations

import asyncio
import math
import random
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Set

from loguru import logger


# ── Symbol Definitions ───────────────────────────────────────────

SYMBOLS = [
    {"symbol": "BTCUSDT", "base": "BTC", "price": 104500.0, "vol_24h": 2_800_000_000, "tick": 0.10, "category": "major"},
    {"symbol": "ETHUSDT", "base": "ETH", "price": 2580.0, "vol_24h": 1_200_000_000, "tick": 0.01, "category": "major"},
    {"symbol": "SOLUSDT", "base": "SOL", "price": 172.0, "vol_24h": 800_000_000, "tick": 0.01, "category": "major"},
    {"symbol": "BNBUSDT", "base": "BNB", "price": 665.0, "vol_24h": 400_000_000, "tick": 0.10, "category": "major"},
    {"symbol": "XRPUSDT", "base": "XRP", "price": 2.28, "vol_24h": 600_000_000, "tick": 0.001, "category": "major"},
    {"symbol": "DOGEUSDT", "base": "DOGE", "price": 0.228, "vol_24h": 350_000_000, "tick": 0.0001, "category": "meme"},
    {"symbol": "AVAXUSDT", "base": "AVAX", "price": 38.5, "vol_24h": 250_000_000, "tick": 0.01, "category": "alt"},
    {"symbol": "ADAUSDT", "base": "ADA", "price": 0.78, "vol_24h": 200_000_000, "tick": 0.001, "category": "alt"},
    {"symbol": "LINKUSDT", "base": "LINK", "price": 16.2, "vol_24h": 300_000_000, "tick": 0.01, "category": "alt"},
    {"symbol": "DOTUSDT", "base": "DOT", "price": 4.65, "vol_24h": 150_000_000, "tick": 0.001, "category": "alt"},
    {"symbol": "MATICUSDT", "base": "MATIC", "price": 0.58, "vol_24h": 180_000_000, "tick": 0.001, "category": "alt"},
    {"symbol": "ARBUSDT", "base": "ARB", "price": 1.12, "vol_24h": 220_000_000, "tick": 0.001, "category": "alt"},
    {"symbol": "OPUSDT", "base": "OP", "price": 2.45, "vol_24h": 160_000_000, "tick": 0.001, "category": "alt"},
    {"symbol": "NEARUSDT", "base": "NEAR", "price": 5.80, "vol_24h": 140_000_000, "tick": 0.01, "category": "alt"},
    {"symbol": "AAVEUSDT", "base": "AAVE", "price": 285.0, "vol_24h": 120_000_000, "tick": 0.10, "category": "defi"},
    {"symbol": "UNIUSDT", "base": "UNI", "price": 7.85, "vol_24h": 100_000_000, "tick": 0.01, "category": "defi"},
]

# Market regimes with transition probabilities
REGIMES = {
    "trending_up":   {"drift": 0.0003, "vol_mult": 1.0, "persistence": 0.95},
    "trending_down": {"drift": -0.0003, "vol_mult": 1.0, "persistence": 0.95},
    "ranging":       {"drift": 0.0,    "vol_mult": 0.6, "persistence": 0.90},
    "volatile":      {"drift": 0.0,    "vol_mult": 2.0, "persistence": 0.85},
    "breakout":      {"drift": 0.0005, "vol_mult": 2.5, "persistence": 0.80},
}


class MarketTick:
    """Single market tick data."""
    __slots__ = (
        "symbol", "price", "volume", "bid", "ask", "spread",
        "change_1m", "change_5m", "change_1h", "change_24h",
        "high_24h", "low_24h", "buy_vol", "sell_vol",
        "timestamp", "regime", "trade_count",
    )

    def __init__(self, **kwargs):
        for k in self.__slots__:
            setattr(self, k, kwargs.get(k, 0))

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}


class OrderbookSnapshot:
    """Orderbook snapshot with depth."""
    __slots__ = ("symbol", "bids", "asks", "mid_price", "spread_bps", "imbalance", "timestamp")

    def __init__(self, **kwargs):
        for k in self.__slots__:
            setattr(self, k, kwargs.get(k, 0))

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}


class TradeEvent:
    """Individual trade event."""
    __slots__ = ("symbol", "price", "quantity", "value_usd", "side", "is_large", "timestamp")

    def __init__(self, **kwargs):
        for k in self.__slots__:
            setattr(self, k, kwargs.get(k, 0))

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}


class MarketDataFeed:
    """
    Real-time market data feed engine.
    Generates realistic market data using GBM with regime switching.
    Publishes ticks, orderbook snapshots, and trade events.
    """

    def __init__(self) -> None:
        # Per-symbol state
        self._prices: Dict[str, float] = {}
        self._prev_prices: Dict[str, Dict[str, float]] = {}  # {sym: {1m: p, 5m: p, 1h: p, 24h: p}}
        self._highs: Dict[str, float] = {}
        self._lows: Dict[str, float] = {}
        self._buy_vols: Dict[str, float] = {}
        self._sell_vols: Dict[str, float] = {}
        self._regimes: Dict[str, str] = {}
        self._volatilities: Dict[str, float] = {}
        self._trade_counts: Dict[str, int] = {}

        # Market-wide state
        self._market_regime: str = "ranging"
        self._correlation_matrix: Dict[str, Dict[str, float]] = {}
        self._funding_rates: Dict[str, float] = {}
        self._open_interests: Dict[str, float] = {}

        # Price history for charts
        self._price_history: Dict[str, List[Dict]] = {}  # {sym: [{ts, price, vol}]}
        self._max_history: int = 500

        # Callbacks for subscribers
        self._tick_callbacks: List[Callable] = []
        self._trade_callbacks: List[Callable] = []
        self._orderbook_callbacks: List[Callable] = []

        self._running: bool = False
        self._tasks: List[asyncio.Task] = []
        self._last_tick: float = 0

    async def initialize(self) -> None:
        """Initialize the market data feed."""
        now = time.time()
        for sym_def in SYMBOLS:
            sym = sym_def["symbol"]
            base_price = sym_def["price"]
            # Add slight randomization to starting price
            jitter = base_price * random.uniform(-0.02, 0.02)
            self._prices[sym] = base_price + jitter
            self._highs[sym] = self._prices[sym] * 1.02
            self._lows[sym] = self._prices[sym] * 0.98
            self._buy_vols[sym] = random.uniform(100_000, 500_000)
            self._sell_vols[sym] = random.uniform(100_000, 500_000)
            self._regimes[sym] = "ranging"
            self._volatilities[sym] = sym_def["price"] * 0.001  # 0.1% base vol
            self._trade_counts[sym] = random.randint(5000, 50000)
            self._price_history[sym] = []

            # Initialize prev prices for change calc
            self._prev_prices[sym] = {
                "1m": base_price * (1 + random.uniform(-0.002, 0.002)),
                "5m": base_price * (1 + random.uniform(-0.005, 0.005)),
                "1h": base_price * (1 + random.uniform(-0.015, 0.015)),
                "24h": base_price * (1 + random.uniform(-0.05, 0.05)),
            }

            # Funding rates (-0.01% to +0.03%)
            self._funding_rates[sym] = random.uniform(-0.0001, 0.0003)
            # Open interest
            self._open_interests[sym] = sym_def["vol_24h"] * random.uniform(0.5, 2.0)

        # Set initial market regime
        self._market_regime = random.choice(["trending_up", "ranging", "volatile"])

        logger.info("MarketDataFeed initialized — {} symbols", len(SYMBOLS))

    async def start(self) -> None:
        """Start all data generation loops."""
        self._running = True
        self._tasks = [
            asyncio.create_task(self._tick_loop(), name="tick-gen"),
            asyncio.create_task(self._trade_loop(), name="trade-gen"),
            asyncio.create_task(self._orderbook_loop(), name="ob-gen"),
            asyncio.create_task(self._regime_loop(), name="regime-switch"),
            asyncio.create_task(self._price_history_loop(), name="price-history"),
        ]
        logger.info("MarketDataFeed started — generating live data")

    async def stop(self) -> None:
        """Stop all generation loops."""
        self._running = False
        for t in self._tasks:
            if not t.done():
                t.cancel()
        self._tasks.clear()
        logger.info("MarketDataFeed stopped")

    # ── Subscriber callbacks ─────────────────────────────────────

    def on_tick(self, callback: Callable) -> None:
        self._tick_callbacks.append(callback)

    def on_trade(self, callback: Callable) -> None:
        self._trade_callbacks.append(callback)

    def on_orderbook(self, callback: Callable) -> None:
        self._orderbook_callbacks.append(callback)

    # ── Price generation (GBM with regime switching) ─────────────

    def _evolve_price(self, sym: str, dt: float = 0.1) -> float:
        """Evolve price using Geometric Brownian Motion with regime."""
        price = self._prices[sym]
        regime = REGIMES.get(self._regimes[sym], REGIMES["ranging"])

        drift = regime["drift"] * dt
        vol = self._volatilities[sym] * regime["vol_mult"]
        shock = random.gauss(0, 1) * vol * math.sqrt(dt) / price

        # Add occasional jumps (fat tails)
        if random.random() < 0.005:  # 0.5% chance of jump
            jump = random.gauss(0, 0.005) * price
            price += jump

        # Mean reversion for ranging regime
        if self._regimes[sym] == "ranging":
            sym_def = next((s for s in SYMBOLS if s["symbol"] == sym), None)
            if sym_def:
                fair = sym_def["price"]
                reversion = (fair - price) / fair * 0.01
                drift += reversion

        new_price = price * (1 + drift + shock)
        new_price = max(new_price, price * 0.95)  # Max 5% drop per tick
        new_price = min(new_price, price * 1.05)  # Max 5% rise per tick

        # Round to tick size
        sym_def = next((s for s in SYMBOLS if s["symbol"] == sym), None)
        if sym_def:
            tick = sym_def["tick"]
            new_price = round(new_price / tick) * tick

        self._prices[sym] = new_price
        return new_price

    # ── Tick generation loop (100ms) ─────────────────────────────

    async def _tick_loop(self) -> None:
        """Generate market ticks at ~100ms intervals."""
        while self._running:
            now = time.time()
            ticks = []
            for sym_def in SYMBOLS:
                sym = sym_def["symbol"]
                price = self._evolve_price(sym)

                # Update high/low
                self._highs[sym] = max(self._highs.get(sym, price), price)
                self._lows[sym] = min(self._lows.get(sym, price), price)

                # Simulate volume
                base_vol = sym_def["vol_24h"] / 86400 / 10  # per 100ms
                vol_mult = REGIMES[self._regimes[sym]]["vol_mult"]
                volume = base_vol * vol_mult * random.uniform(0.5, 1.5)

                # Buy/sell split
                buy_ratio = random.uniform(0.3, 0.7)
                if self._regimes[sym] == "trending_up":
                    buy_ratio = random.uniform(0.5, 0.7)
                elif self._regimes[sym] == "trending_down":
                    buy_ratio = random.uniform(0.3, 0.5)

                buy_vol = volume * buy_ratio
                sell_vol = volume * (1 - buy_ratio)
                self._buy_vols[sym] += buy_vol
                self._sell_vols[sym] += sell_vol

                # Spread (basis points)
                spread_bps = random.uniform(0.5, 5.0)
                if self._regimes[sym] == "volatile":
                    spread_bps *= 2

                self._trade_counts[sym] += random.randint(5, 50)

                tick = MarketTick(
                    symbol=sym,
                    price=round(price, 8),
                    volume=round(volume, 2),
                    bid=round(price * (1 - spread_bps / 20000), 8),
                    ask=round(price * (1 + spread_bps / 20000), 8),
                    spread=round(spread_bps, 2),
                    change_1m=round((price / self._prev_prices[sym]["1m"] - 1) * 100, 4),
                    change_5m=round((price / self._prev_prices[sym]["5m"] - 1) * 100, 4),
                    change_1h=round((price / self._prev_prices[sym]["1h"] - 1) * 100, 4),
                    change_24h=round((price / self._prev_prices[sym]["24h"] - 1) * 100, 4),
                    high_24h=round(self._highs[sym], 8),
                    low_24h=round(self._lows[sym], 8),
                    buy_vol=round(self._buy_vols[sym], 2),
                    sell_vol=round(self._sell_vols[sym], 2),
                    timestamp=now,
                    regime=self._regimes[sym],
                    trade_count=self._trade_counts[sym],
                )
                ticks.append(tick.to_dict())

            # Notify callbacks
            for cb in self._tick_callbacks:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(ticks)
                    else:
                        cb(ticks)
                except Exception as e:
                    logger.error("Tick callback error: {}", e)

            self._last_tick = now
            await asyncio.sleep(0.1)  # 100ms tick interval

    # ── Trade generation loop (50ms) ─────────────────────────────

    async def _trade_loop(self) -> None:
        """Generate individual trade events."""
        while self._running:
            # Generate trades for random symbols
            num_trades = random.randint(1, 5)
            for _ in range(num_trades):
                sym_def = random.choice(SYMBOLS)
                sym = sym_def["symbol"]
                price = self._prices[sym]

                # Trade size follows power law (many small, few large)
                base_size = sym_def["vol_24h"] / price / 100_000
                size = base_size * (random.paretovariate(1.5))
                size = min(size, base_size * 100)  # Cap

                value_usd = price * size
                is_buy = random.random() < 0.5
                is_large = value_usd > 50_000  # $50K+ is "large"

                trade = TradeEvent(
                    symbol=sym,
                    price=round(price, 8),
                    quantity=round(size, 6),
                    value_usd=round(value_usd, 2),
                    side="BUY" if is_buy else "SELL",
                    is_large=is_large,
                    timestamp=time.time(),
                )

                for cb in self._trade_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(trade.to_dict())
                        else:
                            cb(trade.to_dict())
                    except Exception as e:
                        logger.error("Trade callback error: {}", e)

            await asyncio.sleep(0.05)  # 50ms

    # ── Orderbook generation loop (200ms) ────────────────────────

    async def _orderbook_loop(self) -> None:
        """Generate orderbook snapshots."""
        while self._running:
            # Generate for top 6 symbols
            top_symbols = SYMBOLS[:6]
            for sym_def in top_symbols:
                sym = sym_def["symbol"]
                price = self._prices[sym]
                tick = sym_def["tick"]

                # Generate 20 levels of depth
                bids = []
                asks = []
                for i in range(20):
                    spread = tick * (i + 1) * random.uniform(1, 3)
                    bid_price = round(price - spread, 8)
                    ask_price = round(price + spread, 8)
                    bid_qty = round(random.uniform(0.1, 50) * (1 + i * 0.1), 4)
                    ask_qty = round(random.uniform(0.1, 50) * (1 + i * 0.1), 4)
                    bids.append([bid_price, bid_qty])
                    asks.append([ask_price, ask_qty])

                total_bid = sum(b[1] for b in bids)
                total_ask = sum(a[1] for a in asks)
                imbalance = (total_bid - total_ask) / max(total_bid + total_ask, 1)

                spread_bps = (asks[0][0] - bids[0][0]) / price * 10000

                snapshot = OrderbookSnapshot(
                    symbol=sym,
                    bids=bids,
                    asks=asks,
                    mid_price=round((bids[0][0] + asks[0][0]) / 2, 8),
                    spread_bps=round(spread_bps, 2),
                    imbalance=round(imbalance, 4),
                    timestamp=time.time(),
                )

                for cb in self._orderbook_callbacks:
                    try:
                        if asyncio.iscoroutinefunction(cb):
                            await cb(snapshot.to_dict())
                        else:
                            cb(snapshot.to_dict())
                    except Exception as e:
                        logger.error("Orderbook callback error: {}", e)

            await asyncio.sleep(0.2)  # 200ms

    # ── Regime switching loop (30s) ──────────────────────────────

    async def _regime_loop(self) -> None:
        """Switch market regimes periodically."""
        while self._running:
            await asyncio.sleep(30)

            # Market-wide regime shift
            if random.random() < 0.3:  # 30% chance of market-wide shift
                self._market_regime = random.choice(list(REGIMES.keys()))

            # Per-symbol regime (correlated with market)
            for sym_def in SYMBOLS:
                sym = sym_def["symbol"]
                current = self._regimes.get(sym, "ranging")
                regime = REGIMES[current]

                # Check persistence
                if random.random() < regime["persistence"]:
                    continue  # Stay in current regime

                # Transition
                if self._market_regime in ("trending_up", "trending_down", "breakout"):
                    # Higher chance to follow market
                    weights = {
                        "trending_up": 0.35, "trending_down": 0.25,
                        "volatile": 0.15, "ranging": 0.15, "breakout": 0.10,
                    }
                else:
                    weights = {
                        "ranging": 0.35, "volatile": 0.25,
                        "trending_up": 0.15, "trending_down": 0.15, "breakout": 0.10,
                    }

                regimes = list(weights.keys())
                probs = list(weights.values())
                new_regime = random.choices(regimes, weights=probs, k=1)[0]
                self._regimes[sym] = new_regime

                # Adjust volatility when regime changes
                vol_mult = REGIMES[new_regime]["vol_mult"]
                base_vol = sym_def["price"] * 0.001
                self._volatilities[sym] = base_vol * vol_mult

    # ── Price history loop (1s) ──────────────────────────────────

    async def _price_history_loop(self) -> None:
        """Record price history for charting."""
        while self._running:
            now = time.time()
            for sym_def in SYMBOLS:
                sym = sym_def["symbol"]
                price = self._prices.get(sym, 0)
                entry = {
                    "ts": now,
                    "price": round(price, 8),
                    "volume": round(random.uniform(1000, 100_000), 2),
                }
                history = self._price_history.setdefault(sym, [])
                history.append(entry)
                if len(history) > self._max_history:
                    self._price_history[sym] = history[-self._max_history:]

                # Update prev prices for change calculations
                pp = self._prev_prices.setdefault(sym, {})
                if now - pp.get("_last_1m", 0) >= 60:
                    pp["1m"] = price
                    pp["_last_1m"] = now
                if now - pp.get("_last_5m", 0) >= 300:
                    pp["5m"] = price
                    pp["_last_5m"] = now
                if now - pp.get("_last_1h", 0) >= 3600:
                    pp["1h"] = price
                    pp["_last_1h"] = now

            await asyncio.sleep(1)

    # ── Public getters ───────────────────────────────────────────

    def get_all_ticks(self) -> List[Dict[str, Any]]:
        """Get latest tick for all symbols."""
        now = time.time()
        ticks = []
        for sym_def in SYMBOLS:
            sym = sym_def["symbol"]
            price = self._prices.get(sym, sym_def["price"])
            spread_bps = random.uniform(0.5, 5.0)
            pp = self._prev_prices.get(sym, {})
            ticks.append({
                "symbol": sym,
                "base": sym_def["base"],
                "category": sym_def["category"],
                "price": round(price, 8),
                "volume_24h": sym_def["vol_24h"],
                "change_1m": round((price / pp.get("1m", price) - 1) * 100, 4),
                "change_5m": round((price / pp.get("5m", price) - 1) * 100, 4),
                "change_1h": round((price / pp.get("1h", price) - 1) * 100, 4),
                "change_24h": round((price / pp.get("24h", price) - 1) * 100, 4),
                "high_24h": round(self._highs.get(sym, price), 8),
                "low_24h": round(self._lows.get(sym, price), 8),
                "buy_volume": round(self._buy_vols.get(sym, 0), 2),
                "sell_volume": round(self._sell_vols.get(sym, 0), 2),
                "regime": self._regimes.get(sym, "ranging"),
                "spread_bps": round(spread_bps, 2),
                "funding_rate": round(self._funding_rates.get(sym, 0), 6),
                "open_interest": round(self._open_interests.get(sym, 0), 2),
                "trade_count": self._trade_counts.get(sym, 0),
                "timestamp": now,
            })
        return ticks

    def get_tick(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get latest tick for a specific symbol."""
        ticks = self.get_all_ticks()
        for t in ticks:
            if t["symbol"] == symbol:
                return t
        return None

    def get_price(self, symbol: str) -> Optional[float]:
        """Get current price for a symbol."""
        return self._prices.get(symbol)

    def get_price_history(self, symbol: str, limit: int = 200) -> List[Dict]:
        """Get price history for charting."""
        history = self._price_history.get(symbol, [])
        return history[-limit:]

    def get_market_overview(self) -> Dict[str, Any]:
        """Get aggregated market overview."""
        ticks = self.get_all_ticks()
        total_vol = sum(t["volume_24h"] for t in ticks)
        avg_change = sum(t["change_24h"] for t in ticks) / max(len(ticks), 1)
        gainers = sum(1 for t in ticks if t["change_24h"] > 0)
        losers = sum(1 for t in ticks if t["change_24h"] < 0)

        # Regime distribution
        regime_counts: Dict[str, int] = {}
        for t in ticks:
            r = t["regime"]
            regime_counts[r] = regime_counts.get(r, 0) + 1

        return {
            "total_volume_24h": round(total_vol, 2),
            "avg_change_24h": round(avg_change, 4),
            "gainers": gainers,
            "losers": losers,
            "neutral": len(ticks) - gainers - losers,
            "market_regime": self._market_regime,
            "regime_distribution": regime_counts,
            "symbols_tracked": len(ticks),
            "funding_rates": {sym: round(self._funding_rates.get(sym, 0), 6) for sym in self._funding_rates},
            "timestamp": time.time(),
        }

    def get_symbol_count(self) -> int:
        return len(SYMBOLS)


# Global singleton
market_feed = MarketDataFeed()
