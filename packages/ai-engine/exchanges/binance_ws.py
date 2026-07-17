"""
DeltaTerminal — Binance Futures WebSocket + REST
Async, auto-reconnecting, buffered, production-grade.
Race-condition-safe: proper task tracking, atomic flags, bounded buffers.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, List, Optional

import aiohttp
import websockets
from loguru import logger

from config import config

_MAX_BUFFER_PER_SYMBOL = 500
_MAX_BUFFER_SYMBOLS = 200
_FLUSH_BATCH_SIZE = 100


class BinanceWebSocket:
    """Handles WebSocket streams and REST queries for Binance USDT-M Futures."""

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._callback: Optional[Callable] = None
        self._running = False
        self._connected = False
        self._reconnect_delay = 1
        self._max_reconnect_delay = 120
        self._last_pong = 0.0
        self._connect_task: Optional[asyncio.Task] = None
        self._flush_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()  # Protect _connected flag
        self._disconnect_count = 0
        self._reconnect_count = 0
        self._connected_at = 0.0

        # Trade buffer for batched DB writes (bounded)
        self._buffer: Dict[str, List[Dict]] = {}
        self._buffer_max = _MAX_BUFFER_PER_SYMBOL

        # OI cache — tracks current + previous OI per symbol for change detection
        # Survives across WS reconnects (in-memory only; OpenInterestEngine tracks history)
        self._oi_cache: Dict[str, Dict] = {}  # {symbol: {oi, prev_oi, ts, change_pct}}

        # WS Ticker cache — stores !ticker@arr data for symbol loading when REST is banned
        self._ws_ticker_cache: Dict[str, Dict] = {}  # {symbol: {price, volume, quoteVolume, ...}}
        self._ws_symbols_cache: List[str] = []  # Latest symbol list from exchangeInfo or WS

        # WS Mark Price cache — from !markPrice@arr (bypasses banned premiumIndex REST)
        self._ws_mark_prices: Dict[str, float] = {}  # {symbol: mark_price}
        self._ws_premium_cache: Dict[str, Dict] = {}  # {symbol: full premium data incl funding}

    def _ensure_session(self) -> aiohttp.ClientSession:
        """Create the HTTP session lazily if not yet created."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"X-MBX-APIKEY": config.binance.api_key} if config.binance.api_key else {},
            )
        return self._session

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self, callback: Callable) -> None:
        self._callback = callback
        self._running = True
        self._ensure_session()
        self._connect_task = asyncio.create_task(self._connect_loop(), name="ws_connect")
        self._flush_task = asyncio.create_task(self._flush_loop(), name="ws_flush")
        logger.info("WebSocket client started")

    def get_stats(self) -> dict:
        """Get WebSocket connection statistics."""
        uptime = time.time() - self._connected_at if self._connected_at > 0 else 0
        return {
            "connected": self._connected,
            "disconnect_count": self._disconnect_count,
            "reconnect_count": self._reconnect_count,
            "uptime_seconds": round(uptime, 1),
            "uptime_pct": round(uptime / max(time.time() - (self._connected_at - uptime), 1) * 100, 1) if self._connected_at > 0 else 0,
        }

    async def stop(self) -> None:
        logger.info("WebSocket stopping…")
        self._running = False
        # Close WS first so _connect_loop exits
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        # Cancel tasks
        for task in (self._connect_task, self._flush_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        # Close HTTP session
        if self._session and not self._session.closed:
            await self._session.close()
        self._ws = None
        self._session = None
        logger.info("WebSocket client stopped")

    # ── Connection loop (self-healing) ───────────────────────────

    async def _connect_loop(self) -> None:
        while self._running:
            try:
                await self._connect()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._disconnect_count += 1
                logger.error("WS connect failed: {} — retry in {}s (disconnects={})", exc, self._reconnect_delay, self._disconnect_count)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
                # Cap disconnect counter — if we reconnect successfully, reset
                if self._disconnect_count > 50:
                    logger.warning("⚠️  WS disconnect storm ({} disconnects) — backing off 60s", self._disconnect_count)
                    await asyncio.sleep(60)
                    self._disconnect_count = 0

    async def _connect(self) -> None:
        # Build URL with global streams (ticker for bootstrap + markPrice for funding)
        global_streams = list(config.scanner.global_streams) if hasattr(config.scanner, 'global_streams') else []
        all_globals = ["!ticker@arr"] + global_streams
        streams_param = "/".join(all_globals)
        url = f"{config.binance.ws_url}/stream?streams={streams_param}"
        logger.info("WS connecting → {}", url)

        async with websockets.connect(url, ping_interval=30, ping_timeout=20, close_timeout=10) as ws:
            async with self._lock:
                self._ws = ws
                self._connected = True
            self._reconnect_delay = 1
            self._disconnect_count = 0  # Reset on successful connect
            self._reconnect_count += 1
            self._connected_at = time.time()
            self._last_pong = time.time()

            # Subscribe to individual symbol streams
            await self._subscribe_all()

            async for raw in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw)
                    await self._dispatch(msg)
                except json.JSONDecodeError:
                    pass
                except Exception as exc:
                    logger.error("WS message error: {}", exc)

            async with self._lock:
                self._connected = False
            logger.warning("WS disconnected")

    async def _subscribe_all(self) -> None:
        """Subscribe to streams for all active symbols.
        
        Uses configurable stream set. Default = lightweight 2-stream combo:
          • aggTrade  — aggregated trades (much lower volume than raw @trade)
          • bookTicker — best bid/ask (cheapest depth alternative)
        
        Also subscribes to kline streams for regime detection.
        """
        from database import db

        symbols = await db.get_active_symbols()
        stream_types: List[str] = list(config.scanner.ws_streams)
        names: List[str] = []
        for s in symbols[: config.scanner.max_symbols]:
            sym = s["symbol"].lower()
            for st in stream_types:
                names.append(f"{sym}@{st}")

        # Send in batches of 200
        for i in range(0, len(names), 200):
            batch = names[i : i + 200]
            await self._ws.send(json.dumps({  # type: ignore[union-attr]
                "method": "SUBSCRIBE",
                "params": batch,
                "id": i + 1,
            }))
        subscribed = len(names) // len(stream_types) if stream_types else 0
        logger.info("Subscribed to {} streams for {} symbols ({} streams/symbol)",
                     len(names), subscribed, len(stream_types))

    # ── Message dispatch ─────────────────────────────────────────

    async def _dispatch(self, msg: Dict) -> None:
        stream = msg.get("stream", "")
        data = msg.get("data")

        if not data or not self._callback:
            return

        if "@aggTrade" in stream or "@trade" in stream:
            await self._on_trade(data)
        elif "@bookTicker" in stream:
            await self._on_book_ticker(data)
        elif "@depth" in stream:
            await self._on_depth(data)
        elif "@kline" in stream:
            await self._on_kline(data)
        elif "@markPrice" in stream or "markPrice" in stream:
            # markPrice@arr stream returns an array of objects
            if isinstance(data, list):
                for item in data:
                    await self._on_mark_price(item)
            else:
                await self._on_mark_price(data)
        elif "@openInterest" in stream:
            await self._on_open_interest(data)
        elif "@forceOrder" in stream or "forceOrder" in stream:
            # forceOrder can also come from combined global stream
            if isinstance(data, dict) and "o" in data:
                await self._on_force_order(data)
            elif isinstance(data, dict):
                await self._on_force_order({"o": data})
        elif "!ticker@arr" in stream or "ticker@arr" in stream:
            # Global ticker: array of 24h ticker objects for ALL symbols
            # Use this to bootstrap price data for symbols without active trades
            if isinstance(data, list):
                await self._on_ticker_arr(data)

    async def _on_trade(self, d: Dict) -> None:
        trade = {
            "symbol": d["s"],
            "price": float(d["p"]),
            "quantity": float(d["q"]),
            "is_buyer_maker": d["m"],
            "trade_time": d["T"],
        }
        sym = trade["symbol"]
        self._buffer.setdefault(sym, []).append(trade)
        if self._callback:
            await self._callback("trade", trade)

    async def _on_book_ticker(self, d: Dict) -> None:
        """Handle bookTicker — best bid/ask, lightweight depth proxy."""
        depth = {
            "symbol": d["s"],
            "bids": [[d["b"], d["B"]]],  # best bid price, qty
            "asks": [[d["a"], d["A"]]],  # best ask price, qty
            "timestamp": int(time.time() * 1000),
        }
        if self._callback:
            await self._callback("depth", depth)

    async def _on_depth(self, d: Dict) -> None:
        depth = {
            "symbol": d["s"],
            "bids": d.get("b", []),
            "asks": d.get("a", []),
            "timestamp": int(time.time() * 1000),
        }
        if self._callback:
            await self._callback("depth", depth)

    async def _on_kline(self, d: Dict) -> None:
        k = d["k"]
        kline = {
            "symbol": k["s"],
            "interval": k["i"],
            "open_time": k["t"],
            "close_time": k["T"],
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
            "trades": k["n"],
            "is_closed": k["x"],
        }
        if self._callback:
            await self._callback("kline", kline)

    async def _on_mark_price(self, d: Dict) -> None:
        """Handle markPrice stream — funding rate + mark price data.

        Caches mark prices for OI USD conversion (bypasses banned premiumIndex REST).
        """
        sym = d.get("s", "")
        if not sym:
            return
        mark_price = float(d.get("p", 0))
        index_price = float(d.get("i", 0))
        funding_rate = float(d.get("r", 0))
        funding = {
            "symbol": sym,
            "mark_price": mark_price,
            "index_price": index_price,
            "funding_rate": funding_rate,
            "next_funding_time": d.get("T", 0),
            "timestamp": int(time.time() * 1000),
        }
        # Cache mark price for OI USD conversion
        if mark_price > 0:
            self._ws_mark_prices[sym] = mark_price
        # Cache full premium data (funding rate, countdown, index) for get_premium_index_all
        self._ws_premium_cache[sym] = {
            "symbol": sym,
            "current_rate": funding_rate,
            "next_funding_time": d.get("T", 0),
            "mark_price": mark_price,
            "index_price": index_price,
            "estimated_settle_price": 0,
            "timestamp": int(time.time() * 1000),
        }
        if self._callback:
            await self._callback("funding", funding)

    async def _on_force_order(self, d: Dict) -> None:
        """Handle forceOrder stream — liquidation data."""
        order = d.get("o", d)
        liq = {
            "symbol": order["s"],
            "side": "SELL" if order["S"] == "SELL" else "BUY",  # Liquidation side
            "price": float(order["p"]),
            "quantity": float(order["q"]),
            "order_type": order.get("o", "MARKET"),
            "timestamp": order.get("T", int(time.time() * 1000)),
        }
        if self._callback:
            await self._callback("liquidation", liq)

    async def _on_ticker_arr(self, tickers: list) -> None:
        """Handle !ticker@arr — global 24h ticker for ALL symbols.

        1. Cache ticker data for symbol loading when REST is banned.
        2. Generate synthetic trade events to bootstrap price data for symbols
           that have no active trading on testnet.
        """
        if not self._callback:
            return
        for i, t in enumerate(tickers):
            sym = t.get("s", "")
            price = float(t.get("c", 0))  # last price
            vol = float(t.get("v", 0))    # 24h base volume
            quote_vol = float(t.get("q", 0))  # 24h quote volume (USDT)
            if not sym or price <= 0:
                continue
            # Cache ticker data for WS-based symbol loading
            self._ws_ticker_cache[sym] = {
                "symbol": sym,
                "price": price,
                "volume": vol,
                "quoteVolume": quote_vol,
                "change_pct": float(t.get("P", 0)),  # P=priceChangePercent (not p=priceChange)
                "price_change": float(t.get("p", 0)),  # FIX: absolute price change (missing before)
                "high": float(t.get("h", 0)),
                "low": float(t.get("l", 0)),
                "open": float(t.get("o", 0)),
                "count": int(t.get("n", 0)),
                "last_update": time.time(),
            }
            # Create a synthetic trade event to populate engine.symbol_data
            # Alternate is_buyer_maker to avoid skewing exchange flow data
            trade = {
                "symbol": sym,
                "price": price,
                "quantity": vol / 1000 if vol > 0 else 0.001,  # approximate trade size
                "is_buyer_maker": (i % 2 == 0),  # alternate to balance buy/sell
                "trade_time": int(time.time() * 1000),
                "_source": "ticker_arr",
            }
            await self._callback("trade", trade)

    # ── Batched DB flush (bounded, atomic) ──────────────────────

    async def _flush_loop(self) -> None:
        while self._running:
            await asyncio.sleep(1)
            # Snapshot and clear buffers atomically
            to_flush: Dict[str, List[Dict]] = {}
            for sym in list(self._buffer.keys()):
                buf = self._buffer[sym]
                if len(buf) >= self._buffer_max:
                    to_flush[sym] = buf[:_FLUSH_BATCH_SIZE]
                    self._buffer[sym] = buf[_FLUSH_BATCH_SIZE:]

            for sym, trades in to_flush.items():
                try:
                    from database import db
                    await db.insert_trades(sym, trades)
                except Exception as exc:
                    logger.error("Flush error {}: {}", sym, exc)

            # Trim buffer map if too many symbols tracked
            if len(self._buffer) > _MAX_BUFFER_SYMBOLS:
                # Evict oldest symbols by trade count (smallest first)
                syms_by_size = sorted(self._buffer.items(), key=lambda x: len(x[1]))
                for sym, _ in syms_by_size[:len(syms_by_size) - _MAX_BUFFER_SYMBOLS]:
                    if len(self._buffer[sym]) < self._buffer_max:
                        del self._buffer[sym]

    # ── REST API ─────────────────────────────────────────────────

    async def _get(self, path: str, params=None, use_data_url: bool = False) -> Any:
        """Fetch from Binance REST API. use_data_url=True forces production endpoint for market data display."""
        base_url = config.binance.data_rest_url if use_data_url else config.binance.rest_url
        url = f"{base_url}{path}"
        try:
            session = self._ensure_session()
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                body = await resp.text()
                logger.error("REST {} {} → {}: {}", "GET", path, resp.status, body[:200])
                return None
        except asyncio.TimeoutError:
            logger.warning("REST timeout: {}{} — falling back", base_url, path)
            return None
        except (aiohttp.ClientError, ConnectionError, OSError) as exc:
            logger.warning("REST connection error: {}{} — {}", base_url, path, exc)
            return None

    async def get_futures_symbols(self) -> List[str]:
        """Get list of active USDT perpetual futures symbols.

        PRIORITY: Use WS cache (from previous REST call or DB).
        FALLBACK: REST API exchangeInfo (may return 418 if IP banned).
        LAST RESORT: Use symbols from WS ticker cache.
        """
        # Try REST first
        data = await self._get("/fapi/v1/exchangeInfo")
        if data:
            syms = [
                s["symbol"]
                for s in data.get("symbols", [])
                if s.get("contractType") == "PERPETUAL"
                and s.get("quoteAsset") == "USDT"
                and s.get("status") == "TRADING"
            ]
            if syms:
                self._ws_symbols_cache = syms
                return syms
        # Fallback: symbols from WS ticker cache
        if self._ws_ticker_cache:
            ws_syms = [s for s in self._ws_ticker_cache.keys() if s.endswith("USDT")]
            if ws_syms:
                logger.info("Using {} symbols from WS ticker cache (REST banned)", len(ws_syms))
                return ws_syms
        return []

    async def get_24h_tickers(self) -> List[Dict]:
        """Get 24h ticker data for all symbols.

        PRIORITY: Use WS cache (real-time from !ticker@arr, no IP ban risk).
        FALLBACK: REST API (may return 418 if IP banned).
        """
        # Try WS cache first — populated by !ticker@arr stream every 1-2s
        if self._ws_ticker_cache:
            return [
                {
                    "symbol": sym,
                    "price": d["price"],
                    "change_pct": d.get("change_pct", 0),
                    "price_change": d.get("price_change", 0),
                    "volume": d["volume"],
                    "quoteVolume": d["quoteVolume"],
                    "high": d["high"],
                    "low": d["low"],
                    "open": d["open"],
                    "count": d["count"],
                }
                for sym, d in self._ws_ticker_cache.items()
                if d.get("price", 0) > 0 and sym.endswith("USDT")
            ]
        # Fallback: REST API (may be IP-banned → returns None)
        data = await self._get("/fapi/v1/ticker/24hr", use_data_url=True)
        if not data:
            return []
        return [
            {
                "symbol": t["symbol"],
                "price": float(t["lastPrice"]),
                "change_pct": float(t["priceChangePercent"]),
                "price_change": float(t.get("priceChange", 0)),
                "volume": float(t["volume"]),
                "quoteVolume": float(t["quoteVolume"]),
                "high": float(t["highPrice"]),
                "low": float(t["lowPrice"]),
                "open": float(t["openPrice"]),
                "count": int(t["count"]),
            }
            for t in data
            if t["symbol"].endswith("USDT")
        ]

    async def get_klines(self, symbol: str, interval: str = "5m", limit: int = 100) -> List[Dict]:
        data = await self._get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        if not data:
            return []
        return [
            {
                "open_time": k[0], "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
                "close_time": k[6], "quote_volume": float(k[7]), "trades": k[8],
            }
            for k in data
        ]

    async def _on_open_interest(self, d: Dict) -> None:
        """Handle @openInterest WebSocket event (3-second push per symbol).

        Format: {"e":"openInterest","E":1672515782136,"s":"BTCUSDT","o":"117347.301"}
        'o' = current open interest in contracts.
        """
        sym = d.get("s", "")
        if not sym:
            return
        try:
            oi = float(d.get("o", 0))
        except (ValueError, TypeError):
            return
        if oi <= 0:
            return

        now = time.time()
        cached = self._oi_cache.get(sym)
        if cached:
            prev_oi = cached.get("oi", 0)
            change_pct = ((oi - prev_oi) / prev_oi * 100) if prev_oi > 0 else 0
        else:
            prev_oi = 0
            change_pct = 0

        self._oi_cache[sym] = {
            "oi": oi,
            "prev_oi": prev_oi,
            "change_pct": change_pct,
            "ts": now,
        }

        # Emit event to engine callback
        if self._callback:
            await self._callback("open_interest", {
                "symbol": sym,
                "open_interest": oi,
                "change_pct": change_pct,
                "timestamp": int(now * 1000),
            })

    def get_cached_oi(self, symbol: str) -> Optional[Dict]:
        """Get cached OI data for a symbol (from WS stream). Returns None if no data."""
        return self._oi_cache.get(symbol)

    def get_all_cached_oi(self) -> Dict[str, Dict]:
        """Get OI cache for all symbols."""
        return dict(self._oi_cache)

    async def get_open_interest(self, symbol: str) -> Optional[Dict]:
        """Get current open interest for a symbol.

        PRIORITY: Use WS cache if available (real-time, no IP ban risk).
        FALLBACK: REST API (may return 418 if IP banned).
        """
        # Try WS cache first — updated every 3 seconds
        cached = self._oi_cache.get(symbol)
        if cached and cached.get("oi", 0) > 0:
            return {
                "symbol": symbol,
                "open_interest": cached["oi"],
                "change_pct": cached.get("change_pct", 0),
                "source": "websocket",
                "timestamp": int(cached.get("ts", time.time()) * 1000),
            }
        # Fallback: REST API (may be IP-banned → returns None)
        data = await self._get("/fapi/v1/openInterest", {"symbol": symbol}, use_data_url=True)
        if not data:
            return None
        return {
            "symbol": data["symbol"],
            "open_interest": float(data["openInterest"]),
            "change_pct": 0,  # No change from single REST reading
            "source": "rest",
            "timestamp": int(time.time() * 1000),
        }

    async def get_funding_rate(self, symbol: str, limit: int = 10) -> List[Dict]:
        """Get recent funding rate history."""
        data = await self._get("/fapi/v1/fundingRate", {"symbol": symbol, "limit": limit})
        if not data:
            return []
        return [
            {
                "symbol": d["symbol"],
                "funding_rate": float(d["fundingRate"]),
                "funding_time": d["fundingTime"],
            }
            for d in data
        ]

    async def get_premium_index_all(self) -> Dict[str, Dict]:
        """Get real-time funding rates for ALL symbols.

        PRIORITY: Use WS mark price cache (real-time from !markPrice@arr, no IP ban risk).
        FALLBACK: REST API (may return 418 if IP banned).

        Returns dict keyed by symbol with current_rate, next_funding_time, mark_price.
        """
        now_ms = int(time.time() * 1000)

        # Try WS cache first — populated by !markPrice@arr stream every 1-2s
        # _ws_premium_cache has full data: funding_rate, next_funding_time, mark_price, index_price
        if self._ws_premium_cache:
            result = {}
            for sym, pi in self._ws_premium_cache.items():
                if pi.get("mark_price", 0) > 0:
                    result[sym] = pi
            if result:
                return result
        # Fallback: WS mark prices only (no funding rate)
        if self._ws_mark_prices:
            # Get funding rates from the callback's processing
            result = {}
            for sym, mp in self._ws_mark_prices.items():
                if mp > 0:
                    result[sym] = {
                        "symbol": sym,
                        "current_rate": 0,  # Will be updated by funding event callback
                        "next_funding_time": 0,
                        "mark_price": mp,
                        "index_price": 0,
                        "estimated_settle_price": 0,
                        "timestamp": now_ms,
                    }
            if result:
                return result

        # Fallback: REST API (may be IP-banned → returns None)
        data = await self._get("/fapi/v1/premiumIndex", use_data_url=True)
        if not data:
            return {}
        result = {}
        for d in data:
            sym = d.get("symbol", "")
            if not sym:
                continue
            try:
                result[sym] = {
                    "symbol": sym,
                    "current_rate": float(d.get("lastFundingRate", 0)),
                    "next_funding_time": int(d.get("nextFundingTime", 0)),
                    "mark_price": float(d.get("markPrice", 0)),
                    "index_price": float(d.get("indexPrice", 0)),
                    "estimated_settle_price": float(d.get("estimatedSettlePrice", 0)),
                    "timestamp": now_ms,
                }
            except (ValueError, TypeError):
                continue
        return result
