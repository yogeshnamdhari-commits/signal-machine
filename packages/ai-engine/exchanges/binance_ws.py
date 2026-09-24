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
        self._public_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._market_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._callback: Optional[Callable] = None
        self._running = False
        self._connected = False
        self._reconnect_delay = {"public": 1.0, "market": 1.0}
        self._max_reconnect_delay = 120.0
        self._last_pong = 0.0
        self._connect_task: Optional[asyncio.Task] = None
        self._flush_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self._disconnect_count = 0
        self._reconnect_count = 0
        self._connected_at = 0.0
        self._buffer: Dict[str, List[Dict]] = {}
        self._buffer_max = _MAX_BUFFER_PER_SYMBOL
        self._oi_cache: Dict[str, Dict] = {}
        self._ws_ticker_cache: Dict[str, Dict] = {}
        self._ws_symbols_cache: List[str] = []
        self._ws_mark_prices: Dict[str, float] = {}
        self._ws_premium_cache: Dict[str, Dict] = {}
        # Read-only runtime diagnostics for control-stream health. These never
        # synthesize market observations and never affect execution decisions.
        self._subscription_ack_count: Dict[str, int] = {"public": 0, "market": 0}
        self._subscription_error_count: Dict[str, int] = {"public": 0, "market": 0}
        self._last_subscription_error: Dict[str, Dict[str, Any]] = {}
        self._force_order_subscribed = False
        self._force_order_event_count = 0
        self._last_force_order_event_ms = 0
        self._open_interest_subscribed = False
        self._open_interest_event_count = 0
        self._last_open_interest_event_ms = 0

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"X-MBX-APIKEY": config.binance.api_key} if config.binance.api_key else {},
            )
        return self._session

    async def start(self, callback: Callable) -> None:
        self._callback = callback
        self._running = True
        self._ensure_session()
        self._connect_task = asyncio.create_task(self._connect_both_loop(), name="ws_connect")
        self._flush_task = asyncio.create_task(self._flush_loop(), name="ws_flush")
        logger.info("WebSocket client started")

    def get_stats(self) -> dict:
        uptime = time.time() - self._connected_at if self._connected_at > 0 else 0
        return {
            "connected": self._connected,
            "disconnect_count": self._disconnect_count,
            "reconnect_count": self._reconnect_count,
            "uptime_seconds": round(uptime, 1),
            "uptime_pct": round(uptime / max(time.time() - (self._connected_at - uptime), 1) * 100, 1) if self._connected_at > 0 else 0,
            "force_order_subscribed": self._force_order_subscribed,
            "force_order_event_count": self._force_order_event_count,
            "last_force_order_event_ms": self._last_force_order_event_ms,
            "open_interest_subscribed": self._open_interest_subscribed,
            "open_interest_event_count": self._open_interest_event_count,
            "last_open_interest_event_ms": self._last_open_interest_event_ms,
            "open_interest_cache_size": len(self._oi_cache),
            "subscription_ack_count": dict(self._subscription_ack_count),
            "subscription_error_count": dict(self._subscription_error_count),
            "last_subscription_error": dict(self._last_subscription_error),
        }

    async def stop(self) -> None:
        logger.info("WebSocket stopping…")
        self._running = False
        for ws in (self._ws, self._public_ws, self._market_ws):
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass
        for task in (self._connect_task, self._flush_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._session and not self._session.closed:
            await self._session.close()
        self._ws = None
        self._public_ws = None
        self._market_ws = None
        self._session = None
        self._ping_monitor_task: Optional[asyncio.Task] = None
        logger.info("WebSocket client stopped")

    async def _connect_both_loop(self) -> None:
        """Maintain separate production Public and Market websocket routes."""
        await asyncio.gather(
            self._connect_loop("public"),
            self._connect_loop("market"),
        )

    async def _connect_loop(self, route: str) -> None:
        while self._running:
            try:
                await self._connect(route)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._disconnect_count += 1
                delay = self._reconnect_delay.get(route, 1.0)
                logger.error(
                    "WS {} connect failed: {} — retry in {:.1f}s (disconnects={})",
                    route, exc, delay, self._disconnect_count,
                )
                await asyncio.sleep(delay)
                self._reconnect_delay[route] = min(delay * 2.0, self._max_reconnect_delay)

    async def _connect(self, route: str) -> None:
        if route not in {"public", "market"}:
            raise ValueError(f"Unsupported websocket route: {route}")

        base = config.binance.ws_url.rstrip("/")
        # Binance combined stream endpoint is /stream for both public and market streams
        url = f"{base}/stream"
        logger.info("WS {} connecting → {}", route.upper(), url)

        async with websockets.connect(url, ping_interval=20, ping_timeout=30, close_timeout=15, max_size=2**20) as ws:
            async with self._lock:
                if route == "public":
                    self._public_ws = ws
                    self._ws = ws
                else:
                    self._market_ws = ws
                self._connected = True

            self._reconnect_delay[route] = 1.0
            self._reconnect_count += 1
            self._connected_at = time.time()
            self._last_pong = time.time()
            await self._subscribe_route(ws, route)

            # Start ping monitor for this connection
            ping_monitor = asyncio.create_task(self._ping_monitor(ws, route), name=f"ws_ping_{route}")

            async for raw in ws:
                if not self._running:
                    break
                try:
                    msg = json.loads(raw)
                    await self._dispatch(msg, route)
                except json.JSONDecodeError:
                    logger.warning("WS {} non-JSON message ignored", route)
                except Exception as exc:
                    import traceback
                    logger.error("WS {} message error: {}\n{}", route, exc, traceback.format_exc())

            ping_monitor.cancel()
            try:
                await ping_monitor
            except asyncio.CancelledError:
                pass

            async with self._lock:
                if route == "public":
                    self._public_ws = None
                else:
                    self._market_ws = None
                self._connected = bool(self._public_ws or self._market_ws)
            logger.warning("WS {} disconnected", route.upper())

    async def _ping_monitor(self, ws: websockets.WebSocketClientProtocol, route: str) -> None:
        """Monitor connection health with explicit ping/pong."""
        while self._running:
            try:
                await asyncio.sleep(15)
                if not self._running:
                    break
                # Send explicit ping to verify connection
                pong_waiter = await ws.ping()
                await asyncio.wait_for(pong_waiter, timeout=10)
                self._last_pong = time.time()
            except asyncio.TimeoutError:
                logger.warning("WS {} ping timeout — closing connection", route.upper())
                await ws.close()
                break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("WS {} ping monitor error: {}", route.upper(), exc)
                break

    async def _subscribe_route(self, ws: websockets.WebSocketClientProtocol, route: str) -> None:
        from database import db

        symbols = await db.get_active_symbols()
        configured = list(config.scanner.ws_streams)

        if route == "public":
            stream_types = [s for s in configured if s in {"bookTicker", "depth@100ms", "depth"}]
        else:
            stream_types = [s for s in configured if s in {"aggTrade", "trade", "kline_5m", "kline", "openInterest"}]

        names: List[str] = []
        for s in symbols[: config.scanner.max_symbols]:
            sym = s["symbol"].lower()
            for st in stream_types:
                names.append(f"{sym}@{st}")

        if route == "market":
            global_streams = ["!ticker@arr"]
            if hasattr(config.scanner, "global_streams"):
                global_streams.extend(list(config.scanner.global_streams))
            names.extend(global_streams)

        for i in range(0, len(names), 200):
            batch = names[i : i + 200]
            await ws.send(json.dumps({
                "method": "SUBSCRIBE",
                "params": batch,
                "id": i + 1,
            }))

        # Confirm the server's actual subscription set. This is especially
        # important for the global forceOrder feed because an accepted socket
        # connection alone does not prove the stream was subscribed.
        if route == "market":
            await ws.send(json.dumps({
                "method": "LIST_SUBSCRIPTIONS",
                "id": 990001,
            }))

        subscribed_symbols = len(symbols[: config.scanner.max_symbols])
        logger.info(
            "Subscribed {} route: {} streams for {} symbols ({} per-symbol stream types)",
            route.upper(), len(names), subscribed_symbols, len(stream_types),
        )

    async def _dispatch(self, msg: Dict, route: str = "unknown") -> None:
        # Control-plane responses do not contain a stream payload. Preserve
        # them explicitly so subscription failures cannot become silent.
        if "code" in msg and "msg" in msg:
            if route in self._subscription_error_count:
                self._subscription_error_count[route] += 1
                self._last_subscription_error[route] = {
                    "code": msg.get("code"),
                    "msg": msg.get("msg"),
                    "id": msg.get("id"),
                    "timestamp": time.time(),
                }
            logger.error(
                "WS {} control error id={} code={} msg={}",
                route.upper(), msg.get("id"), msg.get("code"), msg.get("msg"),
            )
            return

        if "id" in msg and "result" in msg:
            result = msg.get("result")
            if route in self._subscription_ack_count and result is None:
                self._subscription_ack_count[route] += 1
                return
            if route == "market" and isinstance(result, list):
                self._force_order_subscribed = "!forceOrder@arr" in result
                # Binance stream format: !openInterest@arr (contains "openInterest@" not "@openInterest")
                self._open_interest_subscribed = any(
                    "openInterest@" in s for s in result
                )
                global_in_result = [s for s in result if s.startswith("!")]
                logger.info(
                    "WS MARKET subscriptions confirmed: count={} forceOrder={} openInterest={} global_streams={}",
                    len(result), self._force_order_subscribed, self._open_interest_subscribed, global_in_result,
                )
                return
            return

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
            if isinstance(data, list):
                for item in data:
                    await self._on_mark_price(item)
            else:
                await self._on_mark_price(data)
        elif "@openInterest" in stream:
            if isinstance(data, list):
                for item in data:
                    await self._on_open_interest(item)
            else:
                await self._on_open_interest(data)
        elif "@forceOrder" in stream or "forceOrder" in stream:
            if isinstance(data, dict) and "o" in data:
                await self._on_force_order(data)
            elif isinstance(data, dict):
                await self._on_force_order({"o": data})
        elif "!ticker@arr" in stream or "ticker@arr" in stream:
            if isinstance(data, list):
                print(f"DEBUG: _on_ticker_arr = {self._on_ticker_arr}, type = {type(self._on_ticker_arr)}", flush=True)
                await self._on_ticker_arr(data)

    async def _on_trade(self, d: Dict) -> None:
        trade = {
            "symbol": d["s"],
            "price": float(d["p"]),
            "quantity": float(d["q"]),
            "is_buyer_maker": d["m"],
            "trade_time": d["T"],
            "source": "binance",
            "feed": "aggTrade",
            "data_quality": "REAL",
            "synthetic": False,
        }
        sym = trade["symbol"]
        self._buffer.setdefault(sym, []).append(trade)
        if self._callback:
            await self._callback('trade', trade)

    async def _on_book_ticker(self, d: Dict) -> None:
        depth = {
            "symbol": d["s"],
            "bids": [[d["b"], d["B"]]],
            "asks": [[d["a"], d["A"]]],
            "timestamp": int(time.time() * 1000),
            "source": "binance",
            "feed": "bookTicker",
            "depth_quality": "L1",
            "synthetic": False,
        }
        if self._callback:
            await self._callback("depth", depth)

    async def _on_depth(self, d: Dict) -> None:
        depth = {
            "symbol": d["s"],
            "bids": d.get("b", []),
            "asks": d.get("a", []),
            "timestamp": int(time.time() * 1000),
            "source": "binance",
            "feed": "depth",
            "depth_quality": "L2",
            "synthetic": False,
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
            "source": "binance",
            "feed": "kline",
            "data_quality": "REAL",
        }
        if self._callback:
            await self._callback("kline", kline)

    async def _on_mark_price(self, d: Dict) -> None:
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
            "source": "binance",
            "feed": "markPrice",
            "data_quality": "REAL",
        }
        if mark_price > 0:
            self._ws_mark_prices[sym] = mark_price
        self._ws_premium_cache[sym] = {
            "symbol": sym,
            "current_rate": funding_rate,
            "next_funding_time": d.get("T", 0),
            "mark_price": mark_price,
            "index_price": index_price,
            "estimated_settle_price": 0,
            "timestamp": int(time.time() * 1000),
            "source": "binance",
            "feed": "markPrice",
            "data_quality": "REAL",
        }
        if self._callback:
            await self._callback("funding", funding)

    async def _on_force_order(self, d: Dict) -> None:
        order = d.get("o", d)
        liq = {
            "symbol": order["s"],
            "side": "SELL" if order["S"] == "SELL" else "BUY",
            "price": float(order["p"]),
            "quantity": float(order["q"]),
            "order_type": order.get("o", "MARKET"),
            "timestamp": order.get("T", int(time.time() * 1000)),
            "source": "binance",
            "feed": "forceOrder",
            "data_quality": "REAL",
        }
        self._force_order_event_count += 1
        self._last_force_order_event_ms = int(liq["timestamp"])
        # An observed authentic event is conclusive evidence that the feed is
        # active even if LIST_SUBSCRIPTIONS has not yet been acknowledged.
        self._force_order_subscribed = True
        if self._callback:
            await self._callback("liquidation", liq)

    async def _on_ticker_arr(self, tickers: list) -> None:
        """Cache real 24h ticker observations; never fabricate trade events."""
        print(f"DEBUG: _on_ticker_arr called with {len(tickers) if tickers else 0} tickers", flush=True)
        if not self._callback:
            return
        for t in tickers:
            sym = t.get("s", "")
            price = float(t.get("c", 0) or 0)
            vol = float(t.get("v", 0) or 0)
            quote_vol = float(t.get("q", 0) or 0)
            if not sym or price <= 0:
                continue
            self._ws_ticker_cache[sym] = {
                "symbol": sym,
                "price": price,
                "volume": vol,
                "quoteVolume": quote_vol,
                "change_pct": float(t.get("P", 0) or 0),
                "price_change": float(t.get("p", 0) or 0),
                "high": float(t.get("h", 0) or 0),
                "low": float(t.get("l", 0) or 0),
                "open": float(t.get("o", 0) or 0),
                "count": int(t.get("n", 0) or 0),
                "last_update": time.time(),
                "data_quality": "REAL",
                "source": "binance",
                "feed": "ticker24h",
            }
        # Ticker data is informational/selection data, not a trade tape.
        await self._callback("ticker", {
            "source": "binance",
            "feed": "ticker24h",
            "data_quality": "REAL",
            "count": len(tickers),
            "timestamp": int(time.time() * 1000),
        })

    async def _flush_loop(self) -> None:
        while self._running:
            await asyncio.sleep(1)
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

            if len(self._buffer) > _MAX_BUFFER_SYMBOLS:
                syms_by_size = sorted(self._buffer.items(), key=lambda x: len(x[1]))
                for sym, _ in syms_by_size[: len(syms_by_size) - _MAX_BUFFER_SYMBOLS]:
                    if len(self._buffer[sym]) < self._buffer_max:
                        del self._buffer[sym]

    async def _get(self, path: str, params=None, use_data_url: bool = False) -> Any:
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
        data = await self._get("/fapi/v1/exchangeInfo")
        if data:
            syms = [
                s["symbol"] for s in data.get("symbols", [])
                if s.get("contractType") == "PERPETUAL"
                and s.get("quoteAsset") == "USDT"
                and s.get("status") == "TRADING"
            ]
            if syms:
                self._ws_symbols_cache = syms
                return syms
        if self._ws_ticker_cache:
            ws_syms = [s for s in self._ws_ticker_cache.keys() if s.endswith("USDT")]
            if ws_syms:
                logger.info("Using {} symbols from WS ticker cache (REST banned)", len(ws_syms))
                return ws_syms
        return []

    async def get_24h_tickers(self) -> List[Dict]:
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
                    "last_update": d.get("last_update", 0),
                    "source": "binance",
                    "feed": "ticker24h",
                    "data_quality": "REAL",
                }
                for sym, d in self._ws_ticker_cache.items()
                if d.get("price", 0) > 0 and sym.endswith("USDT")
            ]
        data = await self._get("/fapi/v1/ticker/24hr", use_data_url=True)
        if not data:
            return []
        _rest_stamp = time.time()
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
                "last_update": _rest_stamp,
                "source": "binance",
                "feed": "ticker24h",
                "data_quality": "REAL",
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
                "source": "binance", "feed": "klines", "data_quality": "REAL",
            }
            for k in data
        ]

    async def _on_open_interest(self, d: Dict) -> None:
        sym = d.get("s", "")
        if not sym:
            return
        try:
            oi = float(d.get("o", 0))
        except (ValueError, TypeError):
            return
        if oi <= 0:
            return

        self._open_interest_event_count += 1
        self._last_open_interest_event_ms = int(time.time() * 1000)

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
            "source": "binance",
            "feed": "openInterest",
            "data_quality": "REAL",
        }

        if self._callback:
            await self._callback("open_interest", {
                "symbol": sym,
                "open_interest": oi,
                "change_pct": change_pct,
                "timestamp": int(now * 1000),
                "source": "binance",
                "feed": "openInterest",
                "data_quality": "REAL",
            })

    def get_cached_oi(self, symbol: str) -> Optional[Dict]:
        return self._oi_cache.get(symbol)

    def get_all_cached_oi(self) -> Dict[str, Dict]:
        return dict(self._oi_cache)

    async def get_open_interest(self, symbol: str) -> Optional[Dict]:
        cached = self._oi_cache.get(symbol)
        if cached and cached.get("oi", 0) > 0:
            return {
                "symbol": symbol,
                "open_interest": cached["oi"],
                "change_pct": cached.get("change_pct", 0),
                "source": "websocket",
                "data_quality": "REAL",
                "timestamp": int(cached.get("ts", time.time()) * 1000),
            }
        # REST fallback is disabled when the OI WebSocket stream is subscribed
        # and producing events.  Falling back silently would mask a missing or
        # stalled WS OI feed and falsely present REST-sourced data as real-time.
        if self._open_interest_subscribed and self._open_interest_event_count > 0:
            return None
        data = await self._get("/fapi/v1/openInterest", {"symbol": symbol}, use_data_url=True)
        if not data:
            return None
        return {
            "symbol": data["symbol"],
            "open_interest": float(data["openInterest"]),
            "change_pct": 0,
            "source": "rest",
            "data_quality": "REAL",
            "timestamp": int(time.time() * 1000),
        }

    async def get_funding_rate(self, symbol: str, limit: int = 10) -> List[Dict]:
        data = await self._get("/fapi/v1/fundingRate", {"symbol": symbol, "limit": limit})
        if not data:
            return []
        return [
            {
                "symbol": d["symbol"],
                "funding_rate": float(d["fundingRate"]),
                "funding_time": d["fundingTime"],
                "source": "binance",
                "feed": "fundingRate",
                "data_quality": "REAL",
            }
            for d in data
        ]

    async def get_premium_index_all(self) -> Dict[str, Dict]:
        now_ms = int(time.time() * 1000)
        if self._ws_premium_cache:
            result = {}
            for sym, pi in self._ws_premium_cache.items():
                if pi.get("mark_price", 0) > 0:
                    result[sym] = pi
            if result:
                return result
        if self._ws_mark_prices:
            result = {}
            for sym, mp in self._ws_mark_prices.items():
                if mp > 0:
                    result[sym] = {
                        "symbol": sym,
                        "current_rate": 0,
                        "next_funding_time": 0,
                        "mark_price": mp,
                        "index_price": 0,
                        "estimated_settle_price": 0,
                        "timestamp": now_ms,
                        "source": "binance",
                        "feed": "markPrice",
                        "data_quality": "REAL",
                    }
            if result:
                return result

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
                    "source": "binance",
                    "feed": "premiumIndex",
                    "data_quality": "REAL",
                }
            except (ValueError, TypeError):
                continue
        return result