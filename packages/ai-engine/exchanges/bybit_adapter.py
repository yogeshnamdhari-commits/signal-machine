"""
BybitAdapter — Bybit V5 API implementation of BaseExchange.

Production-grade adapter for Bybit perpetual futures.
Uses Bybit V5 unified API with HMAC-SHA256 signing.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import aiohttp
from loguru import logger

import sys
from pathlib import Path
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from config import config
from exchanges.base_exchange import (
    BaseExchange, ExchangeOrder, ExchangePosition, AccountState,
    OrderbookSnapshot, OrderbookLevel, FundingInfo, ExchangeLatency,
)


class BybitAdapter(BaseExchange):
    """
    Bybit V5 Futures adapter implementing BaseExchange interface.

    API: Bybit V5 Unified Trading
    Auth: HMAC-SHA256 with timestamp + apiKey + recvWindow
    Rate limits: 120 requests/second (IP), 10 orders/second
    """

    MAKER_FEE = 0.0002    # 0.02%
    TAKER_FEE = 0.00055   # 0.055%
    RECV_WINDOW = 5000

    def __init__(self, api_key: str, api_secret: str) -> None:
        super().__init__(name="bybit", api_key=api_key, api_secret=api_secret)
        self._session: Optional[aiohttp.ClientSession] = None
        self._base_url = config.bybit.rest_url

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    # ── Auth ─────────────────────────────────────────────────────

    def _sign(self, params: Dict) -> str:
        """HMAC-SHA256 signature for Bybit V5."""
        query = urlencode(sorted(params.items()))
        return hmac.new(
            self._api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _auth_headers(self, params: Dict) -> Dict[str, str]:
        """Generate authentication headers."""
        timestamp = str(int(time.time() * 1000))
        signature = self._sign(params)
        return {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-SIGN": signature,
            "X-BAPI-RECV-WINDOW": str(self.RECV_WINDOW),
        }

    # ── HTTP ─────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        signed: bool = True,
        max_retries: int = 3,
    ) -> Dict:
        """Execute API request with retry."""
        params = params or {}
        url = f"{self._base_url}{path}"
        session = self._ensure_session()

        for attempt in range(max_retries):
            try:
                headers = self._auth_headers(params) if signed else {}

                if method == "GET":
                    async with session.get(url, params=params, headers=headers) as resp:
                        return await self._handle_response(resp)
                elif method == "POST":
                    headers["Content-Type"] = "application/json"
                    async with session.post(url, json=params, headers=headers) as resp:
                        return await self._handle_response(resp)
                else:
                    raise ValueError(f"Unsupported method: {method}")

            except asyncio.TimeoutError:
                logger.warning("[bybit] Timeout {} (attempt {}/{})", path, attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
            except aiohttp.ClientError as exc:
                logger.warning("[bybit] Client error {} (attempt {}/{}): {}", path, attempt + 1, max_retries, exc)
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

        raise ExchangeError(f"[bybit] Request failed after {max_retries} attempts: {path}")

    async def _handle_response(self, resp: aiohttp.ClientResponse) -> Dict:
        body = await resp.text()
        if resp.status != 200:
            raise ExchangeError(f"[bybit] HTTP {resp.status}: {body[:200]}")

        data = json.loads(body)
        ret_code = data.get("retCode", 0)
        if ret_code != 0:
            raise ExchangeError(f"[bybit] API error {ret_code}: {data.get('retMsg', '')}")
        return data.get("result", data)

    # ── Lifecycle ────────────────────────────────────────────────

    async def connect(self) -> None:
        try:
            await self._request("GET", "/v5/market/time", {}, signed=False)
            self._connected = True
            logger.info("[bybit] Connected to Bybit V5")
        except Exception as exc:
            logger.error("[bybit] Connection failed: {}", exc)
            self._connected = False

    async def disconnect(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        self._connected = False
        logger.info("[bybit] Disconnected")

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
        t0 = time.monotonic()
        try:
            params: Dict[str, Any] = {
                "category": "linear",
                "symbol": symbol,
                "side": side.capitalize(),
                "orderType": order_type.replace("_", " ").title().replace(" ", ""),
                "qty": str(quantity),
            }

            if client_order_id:
                params["orderLinkId"] = client_order_id

            if order_type in ("LIMIT", "STOP", "TAKE_PROFIT"):
                params["price"] = str(price)
                params["timeInForce"] = time_in_force

            if order_type in ("STOP_MARKET", "TAKE_PROFIT_MARKET"):
                params["triggerPrice"] = str(stop_price)
                params["timeInForce"] = "GTC"

            if reduce_only:
                params["reduceOnly"] = True

            result = await self._request("POST", "/v5/order/create", params)
            order_id = result.get("orderId", "")

            # Query order to get full details
            await asyncio.sleep(0.1)
            order = await self.get_order(symbol, order_id=order_id)
            self.record_success()
            return order

        except Exception as exc:
            self.record_failure()
            raise ExchangeError(f"[bybit] Place order failed: {exc}")
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def cancel_order(
        self, symbol: str, order_id: str = "", client_order_id: str = "",
    ) -> ExchangeOrder:
        t0 = time.monotonic()
        try:
            params: Dict[str, Any] = {
                "category": "linear",
                "symbol": symbol,
            }
            if order_id:
                params["orderId"] = order_id
            if client_order_id:
                params["orderLinkId"] = client_order_id

            await self._request("POST", "/v5/order/cancel", params)
            self.record_success()
            return ExchangeOrder(
                order_id=order_id or client_order_id,
                symbol=symbol,
                status="CANCELED",
            )
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_order(
        self, symbol: str, order_id: str = "", client_order_id: str = "",
    ) -> ExchangeOrder:
        t0 = time.monotonic()
        try:
            params: Dict[str, Any] = {
                "category": "linear",
                "symbol": symbol,
            }
            if order_id:
                params["orderId"] = order_id
            if client_order_id:
                params["orderLinkId"] = client_order_id

            data = await self._request("GET", "/v5/order/realtime", params)
            orders = data.get("list", [])
            if not orders:
                return ExchangeOrder(symbol=symbol, status="NOT_FOUND")
            self.record_success()
            return self._parse_order(orders[0])
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_open_orders(self, symbol: str = "") -> List[ExchangeOrder]:
        t0 = time.monotonic()
        try:
            params: Dict[str, Any] = {"category": "linear"}
            if symbol:
                params["symbol"] = symbol

            data = await self._request("GET", "/v5/order/realtime", params)
            self.record_success()
            return [self._parse_order(o) for o in data.get("list", [])]
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    # ── Positions & Account ──────────────────────────────────────

    async def get_positions(self) -> List[ExchangePosition]:
        t0 = time.monotonic()
        try:
            data = await self._request(
                "GET", "/v5/position/list",
                {"category": "linear", "settleCoin": "USDT"},
            )
            self.record_success()
            positions = []
            for p in data.get("list", []):
                qty = float(p.get("size", 0))
                if qty != 0:
                    positions.append(ExchangePosition(
                        symbol=p.get("symbol", ""),
                        side="LONG" if p.get("side") == "Buy" else "SHORT",
                        quantity=abs(qty),
                        entry_price=float(p.get("avgPrice", 0)),
                        mark_price=float(p.get("markPrice", 0)),
                        unrealized_pnl=float(p.get("unrealisedPnl", 0)),
                        leverage=int(float(p.get("leverage", 1))),
                        margin_type=p.get("marginMode", "cross").lower(),
                        notional=abs(float(p.get("positionValue", 0))),
                        liquidation_price=float(p.get("liqPrice", 0)),
                    ))
            return positions
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_account(self) -> AccountState:
        t0 = time.monotonic()
        try:
            data = await self._request(
                "GET", "/v5/account/wallet-balance",
                {"accountType": "UNIFIED"},
            )
            accounts = data.get("list", [])
            if not accounts:
                return AccountState()
            acct = accounts[0]
            self.record_success()
            return AccountState(
                total_balance=float(acct.get("totalWalletBalance", 0)),
                available_balance=float(acct.get("totalAvailableBalance", 0)),
                total_unrealized_pnl=float(acct.get("totalUnrealisedPnl", 0)),
                total_margin=float(acct.get("totalMarginBalance", 0)),
            )
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_balance(self) -> Dict[str, float]:
        t0 = time.monotonic()
        try:
            data = await self._request(
                "GET", "/v5/account/wallet-balance",
                {"accountType": "UNIFIED"},
            )
            accounts = data.get("list", [])
            if not accounts:
                return {"balance": 0.0, "available": 0.0, "unrealized_pnl": 0.0}
            acct = accounts[0]
            self.record_success()
            return {
                "balance": float(acct.get("totalWalletBalance", 0)),
                "available": float(acct.get("totalAvailableBalance", 0)),
                "unrealized_pnl": float(acct.get("totalUnrealisedPnl", 0)),
            }
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    # ── Market Data ──────────────────────────────────────────────

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderbookSnapshot:
        t0 = time.monotonic()
        try:
            data = await self._request(
                "GET", "/v5/market/orderbook",
                {"category": "linear", "symbol": symbol, "limit": min(depth, 200)},
                signed=False,
            )
            self.record_success()
            bids = [
                OrderbookLevel(price=float(b[0]), quantity=float(b[1]))
                for b in data.get("b", [])
            ]
            asks = [
                OrderbookLevel(price=float(a[0]), quantity=float(a[1]))
                for a in data.get("a", [])
            ]
            return OrderbookSnapshot(
                symbol=symbol, bids=bids, asks=asks, timestamp=time.time(),
            )
        except Exception as exc:
            self.record_failure()
            logger.warning("[bybit] Orderbook failed for {}: {}", symbol, exc)
            return OrderbookSnapshot(symbol=symbol, timestamp=time.time())
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_mark_price(self, symbol: str) -> float:
        """Get mark price from Bybit."""
        t0 = time.monotonic()
        try:
            data = await self._request(
                "GET", "/v5/market/tickers", {"category": "linear", "symbol": symbol}, signed=False,
            )
            tickers = data.get("list", [])
            self.record_success()
            if tickers:
                return float(tickers[0].get("markPrice", 0.0))
            return 0.0
        except Exception as exc:
            self.record_failure()
            logger.warning("[bybit] Mark price fetch failed for {}: {}", symbol, exc)
            return 0.0
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_funding_rate(self, symbol: str) -> FundingInfo:
        t0 = time.monotonic()
        try:
            data = await self._request(
                "GET", "/v5/market/tickers",
                {"category": "linear", "symbol": symbol},
                signed=False,
            )
            self.record_success()
            tickers = data.get("list", [])
            if tickers:
                return FundingInfo(exchange=self.name, symbol=symbol, funding_rate=float(tickers[0].get("fundingRate", 0.0)))
            return FundingInfo(exchange=self.name, symbol=symbol, funding_rate=0.0)
        except Exception as exc:
            self.record_failure()
            logger.warning("[bybit] Funding rate failed for {}: {}", symbol, exc)
            return FundingInfo(exchange=self.name, symbol=symbol, funding_rate=0.0)
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_latency(self) -> ExchangeLatency:
        return ExchangeLatency(exchange=self.name, ping_ms=self.get_latency_ms(), update_time=int(time.time()))

    def get_maker_fee(self) -> float:
        return self.MAKER_FEE

    def get_taker_fee(self) -> float:
        return self.TAKER_FEE

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _parse_order(data: Dict) -> ExchangeOrder:
        status_map = {
            "New": "NEW",
            "PartiallyFilled": "PARTIALLY_FILLED",
            "Filled": "FILLED",
            "Cancelled": "CANCELED",
            "Rejected": "REJECTED",
            "Untriggered": "NEW",
        }
        return ExchangeOrder(
            order_id=data.get("orderId", ""),
            client_order_id=data.get("orderLinkId", ""),
            symbol=data.get("symbol", ""),
            side=data.get("side", "").upper(),
            order_type=data.get("orderType", "").upper(),
            status=status_map.get(data.get("orderStatus", ""), data.get("orderStatus", "")),
            price=float(data.get("price", 0)),
            avg_price=float(data.get("avgPrice", 0)),
            quantity=float(data.get("qty", 0)),
            executed_qty=float(data.get("cumExecQty", 0)),
            cum_quote=float(data.get("cumExecValue", 0)),
            time_in_force=data.get("timeInForce", "GTC"),
            stop_price=float(data.get("triggerPrice", 0)),
        )


class ExchangeError(Exception):
    pass
