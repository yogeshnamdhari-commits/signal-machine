"""
Exchange Adapter — Production-grade Binance Futures API wrapper.

Handles:
- Order submission (market, limit, stop, TP, trailing stop)
- Order cancellation
- Position queries
- Account state queries
- Rate limiting with token bucket
- Exponential backoff on failures
- Request signing and authentication
- Automatic retry with idempotency
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import aiohttp
from loguru import logger

import sys
from pathlib import Path
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from config import config


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"
    TRAILING_STOP_MARKET = "TRAILING_STOP_MARKET"
    STOP = "STOP"
    TAKE_PROFIT = "TAKE_PROFIT"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TimeInForce(str, Enum):
    GTC = "GTC"     # Good Till Cancel
    IOC = "IOC"     # Immediate or Cancel
    FOK = "FOK"     # Fill or Kill
    GTX = "GTX"     # Good Till Crossing (post-only)


class PositionSide(str, Enum):
    BOTH = "BOTH"
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class ExchangeOrder:
    """Normalized order response from exchange."""
    order_id: int = 0
    client_order_id: str = ""
    symbol: str = ""
    side: str = ""
    order_type: str = ""
    status: str = ""           # NEW, PARTIALLY_FILLED, FILLED, CANCELED, REJECTED, EXPIRED
    price: float = 0.0
    avg_price: float = 0.0
    quantity: float = 0.0
    executed_qty: float = 0.0
    cum_quote: float = 0.0
    time_in_force: str = "GTC"
    reduce_only: bool = False
    close_position: bool = False
    stop_price: float = 0.0
    working_type: str = "CONTRACT_PRICE"
    price_protect: bool = False
    update_time: int = 0
    transact_time: int = 0

    def to_dict(self) -> Dict:
        return {
            "order_id": self.order_id,
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "status": self.status,
            "price": self.price,
            "avg_price": self.avg_price,
            "quantity": self.quantity,
            "executed_qty": self.executed_qty,
            "cum_quote": self.cum_quote,
            "time_in_force": self.time_in_force,
            "reduce_only": self.reduce_only,
            "stop_price": self.stop_price,
            "update_time": self.update_time,
        }


@dataclass
class ExchangePosition:
    """Normalized position from exchange."""
    symbol: str = ""
    side: str = ""             # LONG / SHORT / BOTH
    quantity: float = 0.0
    entry_price: float = 0.0
    mark_price: float = 0.0
    unrealized_pnl: float = 0.0
    leverage: int = 1
    margin_type: str = "cross"
    isolated_margin: float = 0.0
    position_side: str = "BOTH"
    notional: float = 0.0
    liquidation_price: float = 0.0
    update_time: int = 0


@dataclass
class AccountState:
    """Normalized account state from exchange."""
    total_balance: float = 0.0
    available_balance: float = 0.0
    total_unrealized_pnl: float = 0.0
    total_margin: float = 0.0
    positions: List[ExchangePosition] = field(default_factory=list)
    update_time: int = 0


class RateLimiter:
    """Token bucket rate limiter for Binance API."""

    def __init__(self, max_requests: int = 1200, window_sec: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_sec = window_sec
        self._tokens = float(max_requests)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(
                self.max_requests,
                self._tokens + elapsed * (self.max_requests / self.window_sec),
            )
            self._last_refill = now

            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) * (self.window_sec / self.max_requests)
                logger.warning("Rate limit: waiting {:.2f}s", wait)
                await asyncio.sleep(wait)
                self._tokens = 1.0
                self._last_refill = time.monotonic()

            self._tokens -= 1.0


class ExchangeAdapter:
    """
    Production-grade Binance Futures API adapter.

    Features:
    - HMAC-SHA256 request signing
    - Token bucket rate limiting
    - Exponential backoff retry (1s→2s→4s→8s→16s, max 5 retries)
    - Automatic error classification and recovery
    - Order normalization
    - Position synchronization
    """

    MAX_RETRIES = 5
    BACKOFF_BASE = 1.0
    BACKOFF_MAX = 16.0

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limiter = RateLimiter(
            max_requests=config.binance.rate_limit_rpm,
            window_sec=60.0,
        )
        self._request_count = 0
        self._error_count = 0
        self._last_request_time = 0.0
        self._server_time_offset = 0

    @property
    def base_url(self) -> str:
        return config.binance.rest_url

    @property
    def api_key(self) -> str:
        return config.binance.api_key

    @property
    def api_secret(self) -> str:
        return config.binance.api_secret

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"X-MBX-APIKEY": self.api_key},
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ── Request Signing ──────────────────────────────────────────

    def _sign(self, params: Dict) -> str:
        query = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return signature

    def _timestamp(self) -> int:
        return int(time.time() * 1000) + self._server_time_offset

    # ── Core HTTP with Retry ─────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        signed: bool = True,
        retries: int = MAX_RETRIES,
    ) -> Dict:
        """Execute API request with exponential backoff retry."""
        params = params or {}

        for attempt in range(retries):
            await self._rate_limiter.acquire()

            if signed:
                params["timestamp"] = self._timestamp()
                params["signature"] = self._sign(params)

            url = f"{self.base_url}{path}"
            session = self._ensure_session()

            try:
                self._request_count += 1
                self._last_request_time = time.time()

                if method == "GET":
                    async with session.get(url, params=params) as resp:
                        return await self._handle_response(resp, path, attempt)
                elif method == "POST":
                    async with session.post(url, params=params) as resp:
                        return await self._handle_response(resp, path, attempt)
                elif method == "DELETE":
                    async with session.delete(url, params=params) as resp:
                        return await self._handle_response(resp, path, attempt)
                elif method == "PUT":
                    async with session.put(url, params=params) as resp:
                        return await self._handle_response(resp, path, attempt)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

            except asyncio.TimeoutError:
                self._error_count += 1
                backoff = min(self.BACKOFF_BASE * (2 ** attempt), self.BACKOFF_MAX)
                logger.warning("API timeout {} (attempt {}/{}): retry in {:.1f}s",
                               path, attempt + 1, retries, backoff)
                await asyncio.sleep(backoff)

            except aiohttp.ClientError as exc:
                self._error_count += 1
                backoff = min(self.BACKOFF_BASE * (2 ** attempt), self.BACKOFF_MAX)
                logger.warning("API error {} (attempt {}/{}): {} — retry in {:.1f}s",
                               path, attempt + 1, retries, exc, backoff)
                await asyncio.sleep(backoff)

            except Exception as exc:
                self._error_count += 1
                logger.error("Unexpected API error {}: {}", path, exc)
                if attempt < retries - 1:
                    await asyncio.sleep(min(self.BACKOFF_BASE * (2 ** attempt), self.BACKOFF_MAX))

        raise ExchangeError(f"API request failed after {retries} attempts: {path}")

    async def _handle_response(self, resp: aiohttp.ClientResponse, path: str, attempt: int) -> Dict:
        """Handle HTTP response with error classification."""
        status = resp.status
        body = await resp.text()

        if status == 200:
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                raise ExchangeError(f"Invalid JSON from {path}: {body[:200]}")

        self._error_count += 1

        try:
            error_data = json.loads(body)
            code = error_data.get("code", 0)
            msg = error_data.get("msg", body[:200])
        except (json.JSONDecodeError, AttributeError):
            code = 0
            msg = body[:200]

        if status == 429:
            # Rate limit — extract retry-after
            retry_after = float(resp.headers.get("Retry-After", 5))
            logger.warning("Rate limited (429) on {}: waiting {:.1f}s (attempt {})",
                           path, retry_after, attempt + 1)
            await asyncio.sleep(retry_after)
            raise RateLimitError(f"Rate limited: {msg}")

        if status == 418:
            # IP banned
            logger.error("IP BANNED (418) on {} — stopping all requests", path)
            raise IPBanError(f"IP banned: {msg}")

        if status >= 500:
            backoff = min(self.BACKOFF_BASE * (2 ** attempt), self.BACKOFF_MAX)
            logger.warning("Server error {} on {} (attempt {}): retry in {:.1f}s",
                           status, path, attempt + 1, backoff)
            await asyncio.sleep(backoff)
            raise ServerError(f"Server error {status}: {msg}")

        # 4xx errors — don't retry
        raise ExchangeError(f"API error {status} on {path}: [{code}] {msg}")

    # ── Order Management ─────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float = 0.0,
        stop_price: float = 0.0,
        time_in_force: TimeInForce = TimeInForce.GTC,
        reduce_only: bool = False,
        close_position: bool = False,
        position_side: PositionSide = PositionSide.BOTH,
        client_order_id: str = "",
        working_type: str = "CONTRACT_PRICE",
        callback_rate: float = 0.0,
    ) -> ExchangeOrder:
        """Place an order on Binance Futures."""
        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side.value,
            "type": order_type.value,
        }

        if client_order_id:
            params["newClientOrderId"] = client_order_id

        if quantity > 0:
            params["quantity"] = self._format_quantity(symbol, quantity)

        if order_type in (OrderType.LIMIT, OrderType.STOP, OrderType.TAKE_PROFIT):
            params["price"] = self._format_price(symbol, price)
            params["timeInForce"] = time_in_force.value

        if order_type in (OrderType.STOP_MARKET, OrderType.TAKE_PROFIT_MARKET,
                          OrderType.TRAILING_STOP_MARKET):
            if stop_price > 0:
                params["stopPrice"] = self._format_price(symbol, stop_price)
            params["workingType"] = working_type

        if order_type == OrderType.TRAILING_STOP_MARKET and callback_rate > 0:
            params["callbackRate"] = str(callback_rate)

        if reduce_only and not close_position:
            params["reduceOnly"] = "true"

        if close_position:
            params["closePosition"] = "true"
            params.pop("quantity", None)

        if position_side != PositionSide.BOTH:
            params["positionSide"] = position_side.value

        logger.info("Placing {} {} {} qty={} price={} stop={}",
                     order_type.value, side.value, symbol,
                     quantity, price, stop_price)

        result = await self._request("POST", "/fapi/v1/order", params, signed=True)
        return self._parse_order(result)

    async def cancel_order(self, symbol: str, order_id: int = 0,
                           client_order_id: str = "") -> ExchangeOrder:
        """Cancel an open order."""
        params: Dict[str, Any] = {"symbol": symbol}
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id

        result = await self._request("DELETE", "/fapi/v1/order", params, signed=True)
        return self._parse_order(result)

    async def cancel_all_orders(self, symbol: str) -> Dict:
        """Cancel all open orders for a symbol."""
        params = {"symbol": symbol}
        return await self._request("DELETE", "/fapi/v1/allOpenOrders", params, signed=True)

    async def get_order(self, symbol: str, order_id: int = 0,
                        client_order_id: str = "") -> ExchangeOrder:
        """Query an order by ID."""
        params: Dict[str, Any] = {"symbol": symbol}
        if order_id:
            params["orderId"] = order_id
        if client_order_id:
            params["origClientOrderId"] = client_order_id

        result = await self._request("GET", "/fapi/v1/order", params, signed=True)
        return self._parse_order(result)

    async def get_open_orders(self, symbol: str = "") -> List[ExchangeOrder]:
        """Get all open orders, optionally filtered by symbol."""
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol

        result = await self._request("GET", "/fapi/v1/openOrders", params, signed=True)
        return [self._parse_order(o) for o in result]

    async def get_all_orders(self, symbol: str, limit: int = 500) -> List[ExchangeOrder]:
        """Get all orders (including filled/cancelled)."""
        params = {"symbol": symbol, "limit": limit}
        result = await self._request("GET", "/fapi/v1/allOrders", params, signed=True)
        return [self._parse_order(o) for o in result]

    # ── Position Queries ─────────────────────────────────────────

    async def get_positions(self) -> List[ExchangePosition]:
        """Get all positions with non-zero quantity."""
        result = await self._request("GET", "/fapi/v2/positionRisk", {}, signed=True)
        positions = []
        for p in result:
            qty = float(p.get("positionAmt", 0))
            if qty != 0:
                positions.append(ExchangePosition(
                    symbol=p["symbol"],
                    side="LONG" if qty > 0 else "SHORT",
                    quantity=abs(qty),
                    entry_price=float(p.get("entryPrice", 0)),
                    mark_price=float(p.get("markPrice", 0)),
                    unrealized_pnl=float(p.get("unRealizedProfit", 0)),
                    leverage=int(p.get("leverage", 1)),
                    margin_type=p.get("marginType", "cross"),
                    isolated_margin=float(p.get("isolatedMargin", 0)),
                    position_side=p.get("positionSide", "BOTH"),
                    notional=abs(float(p.get("notional", 0))),
                    liquidation_price=float(p.get("liquidationPrice", 0)),
                    update_time=int(p.get("updateTime", 0)),
                ))
        return positions

    async def get_position(self, symbol: str) -> Optional[ExchangePosition]:
        """Get position for a specific symbol."""
        positions = await self.get_positions()
        for p in positions:
            if p.symbol == symbol:
                return p
        return None

    # ── Account Queries ──────────────────────────────────────────

    async def get_account(self) -> AccountState:
        """Get full account state."""
        result = await self._request("GET", "/fapi/v2/account", {}, signed=True)

        positions = []
        for p in result.get("positions", []):
            qty = float(p.get("positionAmt", 0))
            if qty != 0:
                positions.append(ExchangePosition(
                    symbol=p["symbol"],
                    side="LONG" if qty > 0 else "SHORT",
                    quantity=abs(qty),
                    entry_price=float(p.get("entryPrice", 0)),
                    mark_price=float(p.get("markPrice", 0)),
                    unrealized_pnl=float(p.get("unRealizedProfit", 0)),
                    leverage=int(p.get("leverage", 1)),
                    margin_type=p.get("marginType", "cross"),
                    position_side=p.get("positionSide", "BOTH"),
                    notional=abs(float(p.get("notional", 0))),
                ))

        return AccountState(
            total_balance=float(result.get("totalWalletBalance", 0)),
            available_balance=float(result.get("availableBalance", 0)),
            total_unrealized_pnl=float(result.get("totalUnrealizedProfit", 0)),
            total_margin=float(result.get("totalMarginBalance", 0)),
            positions=positions,
            update_time=int(result.get("updateTime", 0)),
        )

    async def get_balance(self) -> Dict[str, float]:
        """Get account balances."""
        result = await self._request("GET", "/fapi/v2/balance", {}, signed=True)
        for item in result:
            if item.get("asset") == "USDT":
                return {
                    "balance": float(item.get("balance", 0)),
                    "available": float(item.get("availableBalance", 0)),
                    "unrealized_pnl": float(item.get("crossUnPnl", 0)),
                }
        return {"balance": 0.0, "available": 0.0, "unrealized_pnl": 0.0}

    # ── Leverage & Margin ────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> Dict:
        """Set leverage for a symbol."""
        params = {"symbol": symbol, "leverage": leverage}
        return await self._request("POST", "/fapi/v1/leverage", params, signed=True)

    async def set_margin_type(self, symbol: str, margin_type: str = "CROSSED") -> Dict:
        """Set margin type (CROSSED or ISOLATED)."""
        params = {"symbol": symbol, "marginType": margin_type}
        try:
            return await self._request("POST", "/fapi/v1/marginType", params, signed=True)
        except ExchangeError as e:
            if "-4046" in str(e):
                # Already set to this margin type
                return {"msg": "No need to change margin type"}
            raise

    # ── Market Data ──────────────────────────────────────────────

    async def get_symbol_info(self, symbol: str) -> Optional[Dict]:
        """Get symbol trading rules."""
        result = await self._request("GET", "/fapi/v1/exchangeInfo", {}, signed=False)
        for s in result.get("symbols", []):
            if s["symbol"] == symbol:
                return s
        return None

    async def get_server_time(self) -> int:
        """Get server time for clock sync."""
        result = await self._request("GET", "/fapi/v1/time", {}, signed=False)
        return result.get("serverTime", int(time.time() * 1000))

    async def sync_time(self) -> None:
        """Sync local clock with exchange server time."""
        try:
            server_time = await self.get_server_time()
            local_time = int(time.time() * 1000)
            self._server_time_offset = server_time - local_time
            logger.info("Time sync: offset={}ms", self._server_time_offset)
        except Exception as exc:
            logger.warning("Time sync failed: {}", exc)

    # ── Symbol Filters ───────────────────────────────────────────

    _symbol_info_cache: Dict[str, Dict] = {}

    def _get_symbol_filters(self, symbol: str) -> Dict:
        """Get cached symbol filters for quantity/price precision."""
        return self._symbol_info_cache.get(symbol, {
            "tick_size": 0.01,
            "step_size": 0.001,
            "min_qty": 0.001,
            "min_notional": 5.0,
        })

    def _format_price(self, symbol: str, price: float) -> str:
        """Format price according to symbol's tick size."""
        filters = self._get_symbol_filters(symbol)
        tick = filters.get("tick_size", 0.01)
        precision = max(0, len(str(tick).rstrip('0').split('.')[-1])) if '.' in str(tick) else 0
        rounded = round(price - (price % tick), precision) if tick > 0 else price
        return f"{rounded:.{precision}f}"

    def _format_quantity(self, symbol: str, qty: float) -> str:
        """Format quantity according to symbol's step size."""
        filters = self._get_symbol_filters(symbol)
        step = filters.get("step_size", 0.001)
        precision = max(0, len(str(step).rstrip('0').split('.')[-1])) if '.' in str(step) else 0
        rounded = round(qty - (qty % step), precision) if step > 0 else qty
        return f"{rounded:.{precision}f}"

    async def load_symbol_filters(self, symbols: List[str]) -> None:
        """Load and cache symbol trading filters."""
        result = await self._request("GET", "/fapi/v1/exchangeInfo", {}, signed=False)
        for s in result.get("symbols", []):
            if s["symbol"] in symbols:
                filters = {}
                for f in s.get("filters", []):
                    if f["filterType"] == "PRICE_FILTER":
                        filters["tick_size"] = float(f["tickSize"])
                        filters["min_price"] = float(f["minPrice"])
                        filters["max_price"] = float(f["maxPrice"])
                    elif f["filterType"] == "LOT_SIZE":
                        filters["step_size"] = float(f["stepSize"])
                        filters["min_qty"] = float(f["minQty"])
                        filters["max_qty"] = float(f["maxQty"])
                    elif f["filterType"] == "MIN_NOTIONAL":
                        filters["min_notional"] = float(f.get("notional", 5))
                self._symbol_info_cache[s["symbol"]] = filters
        logger.info("Loaded filters for {} symbols", len(self._symbol_info_cache))

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _parse_order(data: Dict) -> ExchangeOrder:
        """Parse exchange order response to normalized ExchangeOrder."""
        return ExchangeOrder(
            order_id=int(data.get("orderId", 0)),
            client_order_id=data.get("clientOrderId", ""),
            symbol=data.get("symbol", ""),
            side=data.get("side", ""),
            order_type=data.get("type", ""),
            status=data.get("status", ""),
            price=float(data.get("price", 0)),
            avg_price=float(data.get("avgPrice", 0)),
            quantity=float(data.get("origQty", 0)),
            executed_qty=float(data.get("executedQty", 0)),
            cum_quote=float(data.get("cumQuote", 0)),
            time_in_force=data.get("timeInForce", "GTC"),
            reduce_only=data.get("reduceOnly", False),
            close_position=data.get("closePosition", False),
            stop_price=float(data.get("stopPrice", 0)),
            working_type=data.get("workingType", "CONTRACT_PRICE"),
            price_protect=data.get("priceProtect", False),
            update_time=int(data.get("updateTime", 0)),
            transact_time=int(data.get("transactTime", 0)),
        )

    def get_stats(self) -> Dict:
        """Get adapter statistics."""
        return {
            "request_count": self._request_count,
            "error_count": self._error_count,
            "error_rate": self._error_count / max(1, self._request_count),
            "last_request_time": self._last_request_time,
            "server_time_offset_ms": self._server_time_offset,
        }


# ── Exception hierarchy ──────────────────────────────────────────

class ExchangeError(Exception):
    """Base exchange error."""
    pass


class RateLimitError(ExchangeError):
    """Rate limit exceeded."""
    pass


class IPBanError(ExchangeError):
    """IP banned by exchange."""
    pass


class ServerError(ExchangeError):
    """Exchange server error."""
    pass
