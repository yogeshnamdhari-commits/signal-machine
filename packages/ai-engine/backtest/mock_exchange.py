"""
MockHistoricalExchange — Historical replay adapter for backtesting.

Implements every abstract method from ``BaseExchange`` so the class can be
instantiated and used as a drop-in replacement for any live exchange adapter.
Feeds historical kline data bar-by-bar to enable backtest replay.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiohttp
import pandas as pd
from loguru import logger

from exchanges.base_exchange import (
    AccountState,
    BaseExchange,
    ExchangeLatency,
    ExchangeOrder,
    ExchangePosition,
    FundingInfo,
    OrderbookLevel,
    OrderbookSnapshot,
)


class MockHistoricalExchange(BaseExchange):
    """
    Mock exchange adapter for backtesting.

    Every abstract method from ``BaseExchange`` is implemented as a minimal
    stub that returns sensible defaults.  The class keeps lightweight
    in-memory tracking of orders, positions, and balance so the backtester
    can exercise order-management and account-query logic without a live
    connection.

    Supports historical replay: load klines via ``load_klines()`` then
    step through them bar-by-bar with ``next_bar()``.
    """

    BINANCE_FAPI = "https://fapi.binance.com"

    def __init__(
        self,
        name: str = "mock_historical",
        initial_balance: float = 10_000.0,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0004,
    ) -> None:
        super().__init__(name=name)
        self._initial_balance = initial_balance
        self._maker_fee = maker_fee
        self._taker_fee = taker_fee

        # In-memory state for backtesting
        self._balance: Dict[str, float] = {"USDT": initial_balance}
        self._positions: List[ExchangePosition] = []
        self._open_orders: List[ExchangeOrder] = []

        # Historical replay state
        self._klines: Dict[str, pd.DataFrame] = {}   # symbol -> DataFrame
        self._bar_index: Dict[str, int] = {}           # symbol -> current bar index
        self._current_bar: Dict[str, Dict] = {}        # symbol -> current OHLCV bar

    # ── Lifecycle ────────────────────────────────────────────────

    async def connect(self) -> None:
        """No-op: no real connection needed for backtesting."""
        self._connected = True

    async def disconnect(self) -> None:
        """No-op: nothing to tear down."""
        self._connected = False

    # ── Historical Data Loading ──────────────────────────────────

    async def fetch_klines(
        self,
        symbol: str,
        interval: str = "5m",
        days: int = 7,
        limit: int = 1500,
    ) -> pd.DataFrame:
        """
        Fetch historical OHLCV klines from Binance Futures REST API.

        Returns a DataFrame with columns:
            open_time, open, high, low, close, volume, quote_volume,
            trades, taker_buy_vol, taker_buy_quote_vol, bar_index
        """
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (days * 24 * 3600 * 1000)

        url = f"{self.BINANCE_FAPI}/fapi/v1/klines"
        params: Dict[str, Any] = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
            "startTime": start_ms,
            "endTime": now_ms,
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(
                        f"Binance API error {resp.status} for {symbol}: {text}"
                    )
                data = await resp.json()

        df = pd.DataFrame(data, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades", "taker_buy_vol",
            "taker_buy_quote_vol", "ignore",
        ])
        for col in (
            "open", "high", "low", "close", "volume",
            "quote_volume", "taker_buy_vol", "taker_buy_quote_vol",
        ):
            df[col] = df[col].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
        df["bar_index"] = range(len(df))

        logger.info(
            "Fetched {} klines for {} {} ({} to {})",
            len(df), symbol, interval,
            df["open_time"].iloc[0], df["open_time"].iloc[-1],
        )
        return df

    def load_klines(self, symbol: str, df: pd.DataFrame) -> None:
        """
        Load a pre-fetched kline DataFrame for bar-by-bar replay.

        Call ``load_klines()`` for each symbol before calling ``next_bar()``.
        """
        self._klines[symbol] = df.copy()
        self._bar_index[symbol] = 0
        self._current_bar[symbol] = self._row_to_dict(df.iloc[0])
        logger.debug("Loaded {} klines for {} (ready for replay)", len(df), symbol)

    def load_cache(self, symbol: str, interval: str, days: int) -> Optional[pd.DataFrame]:
        """Load cached klines from parquet if available."""
        cache_path = Path("data/backtest_cache") / f"{symbol}_{interval}_{days}d.parquet"
        if cache_path.exists():
            df = pd.read_parquet(str(cache_path))
            logger.debug("Loaded cached klines: {} ({} bars)", cache_path, len(df))
            return df
        return None

    def save_cache(self, symbol: str, interval: str, days: int, df: pd.DataFrame) -> None:
        """Persist klines to parquet for reuse."""
        cache_path = Path("data/backtest_cache") / f"{symbol}_{interval}_{days}d.parquet"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(str(cache_path), index=False)

    # ── Bar-by-Bar Replay ────────────────────────────────────────

    def next_bar(self, symbol: str) -> Optional[Dict]:
        """
        Advance to the next kline bar for *symbol*.

        Returns the new bar dict (open/high/low/close/volume/…) or ``None``
        if there are no more bars.
        """
        idx = self._bar_index.get(symbol, 0)
        df = self._klines.get(symbol)
        if df is None or idx >= len(df) - 1:
            return None

        idx += 1
        self._bar_index[symbol] = idx
        bar = self._row_to_dict(df.iloc[idx])
        self._current_bar[symbol] = bar
        return bar

    def get_bar(self, symbol: str) -> Optional[Dict]:
        """Return the current bar for *symbol* without advancing."""
        return self._current_bar.get(symbol)

    def get_bar_index(self, symbol: str) -> int:
        """Return the current bar index for *symbol*."""
        return self._bar_index.get(symbol, 0)

    def get_total_bars(self, symbol: str) -> int:
        """Return total number of loaded bars for *symbol*."""
        df = self._klines.get(symbol)
        return len(df) if df is not None else 0

    def get_close(self, symbol: str) -> float:
        """Return the close price of the current bar."""
        bar = self._current_bar.get(symbol)
        return bar["close"] if bar else 0.0

    def get_ohlcv(self, symbol: str, up_to_bar: int = -1) -> pd.DataFrame:
        """
        Return the kline DataFrame (or slice up to *up_to_bar*).

        Useful for indicator calculation that needs lookback.
        """
        df = self._klines.get(symbol)
        if df is None:
            return pd.DataFrame()
        if up_to_bar >= 0:
            return df.iloc[: up_to_bar + 1].copy()
        return df.copy()

    def has_more_bars(self, symbol: str) -> bool:
        """Check if there are more bars to replay."""
        idx = self._bar_index.get(symbol, 0)
        df = self._klines.get(symbol)
        return df is not None and idx < len(df) - 1

    # ── Bulk Fetch for Top N Futures ─────────────────────────────

    @staticmethod
    async def fetch_top_futures(
        n: int = 50,
        quote: str = "USDT",
    ) -> List[str]:
        """
        Fetch the top *n* USDT perpetual futures by 24 h quote volume.

        Returns a list of symbol strings, e.g. ``["BTCUSDT", "ETHUSDT", …]``.
        """
        url = f"{MockHistoricalExchange.BINANCE_FAPI}/fapi/v1/ticker/24hr"
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Binance API error {resp.status}")
                tickers = await resp.json()

        perps = [
            t for t in tickers
            if t["symbol"].endswith(quote)
            and float(t.get("quoteVolume", 0)) > 0
        ]
        perps.sort(key=lambda t: float(t.get("quoteVolume", 0)), reverse=True)

        symbols = [t["symbol"] for t in perps[:n]]
        logger.info(
            "Top {} futures by volume: {} … {}",
            len(symbols),
            symbols[0] if symbols else "N/A",
            symbols[-1] if len(symbols) > 1 else "N/A",
        )
        return symbols

    # ── Order Management ─────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float = 0.0,
        stop_price: float = 0.0,
        time_in_force: str = "GTC",
        reduce_only: bool = False,
        close_position: bool = False,
        position_side: str = "BOTH",
        client_order_id: str = "",
        working_type: str = "CONTRACT_PRICE",
        callback_rate: float = 0.0,
    ) -> ExchangeOrder:
        """Simulate placing an order — fills immediately for backtesting."""
        order_id = f"mock_{uuid.uuid4().hex[:12]}"
        fill_price = price if price > 0 else self.get_close(symbol)

        order = ExchangeOrder(
            order_id=order_id,
            client_order_id=client_order_id or order_id,
            symbol=symbol,
            side=side.upper(),
            order_type=order_type.upper(),
            status="FILLED",
            price=price,
            avg_price=fill_price,
            quantity=quantity,
            executed_qty=quantity,
            time_in_force=time_in_force,
            reduce_only=reduce_only,
            close_position=close_position,
            stop_price=stop_price,
            working_type=working_type,
            update_time=int(time.time() * 1000),
            transact_time=int(time.time() * 1000),
        )

        # Update in-memory balance for fills
        self._apply_fill(order)

        return order

    async def cancel_order(
        self,
        symbol: str,
        order_id: str = "",
        client_order_id: str = "",
    ) -> ExchangeOrder:
        """Simulate cancelling an order."""
        return ExchangeOrder(
            order_id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            status="CANCELED",
            update_time=int(time.time() * 1000),
        )

    async def get_order(
        self,
        symbol: str,
        order_id: str = "",
        client_order_id: str = "",
    ) -> ExchangeOrder:
        """Return a filled order stub for backtesting."""
        return ExchangeOrder(
            order_id=order_id,
            client_order_id=client_order_id,
            symbol=symbol,
            status="FILLED",
            update_time=int(time.time() * 1000),
        )

    async def get_open_orders(self, symbol: str = "") -> List[ExchangeOrder]:
        """Return the list of tracked open orders (empty by default)."""
        if symbol:
            return [o for o in self._open_orders if o.symbol == symbol]
        return list(self._open_orders)

    # ── Position & Account ───────────────────────────────────────

    async def get_positions(self) -> List[ExchangePosition]:
        """Return tracked positions (empty by default)."""
        return list(self._positions)

    async def get_account(self) -> AccountState:
        """Return account state derived from the in-memory balance."""
        total = sum(self._balance.values())
        return AccountState(
            total_balance=total,
            available_balance=total,
            total_unrealized_pnl=0.0,
            total_margin=0.0,
            positions=list(self._positions),
            update_time=int(time.time() * 1000),
        )

    async def get_balance(self) -> Dict[str, float]:
        """Return asset balances."""
        return dict(self._balance)

    # ── Market Data ──────────────────────────────────────────────

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderbookSnapshot:
        """Synthesize an orderbook from the current bar's OHLCV."""
        bar = self._current_bar.get(symbol)
        if not bar:
            return OrderbookSnapshot(symbol=symbol, timestamp=time.time())

        close = bar["close"]
        spread = close * 0.0001  # 1 bps spread
        bids = [
            OrderbookLevel(price=close - spread * (i + 1), quantity=bar.get("volume", 0) / (depth * 2))
            for i in range(depth)
        ]
        asks = [
            OrderbookLevel(price=close + spread * (i + 1), quantity=bar.get("volume", 0) / (depth * 2))
            for i in range(depth)
        ]
        return OrderbookSnapshot(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=time.time(),
        )

    async def get_funding_rate(self, symbol: str) -> FundingInfo:
        """Return zero funding rate for backtesting."""
        return FundingInfo(
            exchange=self._name,
            symbol=symbol,
            funding_rate=0.0,
            next_funding_time=0,
            mark_price=0.0,
            index_price=0.0,
            predicted_rate=0.0,
        )

    async def get_latency(self) -> ExchangeLatency:
        """Return zero latency — no network in backtesting."""
        return ExchangeLatency(
            exchange=self._name,
            ping_ms=0.0,
            update_time=int(time.time() * 1000),
            avg_latency_ms=0.0,
            p99_latency_ms=0.0,
        )

    # ── Fee Structure ────────────────────────────────────────────

    def get_maker_fee(self) -> float:
        """Return maker fee rate."""
        return self._maker_fee

    def get_taker_fee(self) -> float:
        """Return taker fee rate."""
        return self._taker_fee

    # ── Internal Helpers ─────────────────────────────────────────

    def _apply_fill(self, order: ExchangeOrder) -> None:
        """Update in-memory balance after an order fill."""
        fill_value = order.avg_price * order.executed_qty
        fee = fill_value * self._taker_fee

        if order.side == "BUY":
            self._balance["USDT"] = self._balance.get("USDT", 0) - fee
        else:
            self._balance["USDT"] = self._balance.get("USDT", 0) - fee

    @staticmethod
    def _row_to_dict(row: pd.Series) -> Dict[str, Any]:
        """Convert a kline DataFrame row to a plain dict."""
        return {
            "open_time": row.get("open_time"),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 0)),
            "quote_volume": float(row.get("quote_volume", 0)),
            "trades": int(row.get("trades", 0)),
            "taker_buy_vol": float(row.get("taker_buy_vol", 0)),
            "taker_buy_quote_vol": float(row.get("taker_buy_quote_vol", 0)),
        }
