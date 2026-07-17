"""
DeltaAdapter — Delta Exchange API implementation of BaseExchange.

Production-grade adapter for Delta Exchange perpetual futures.
Uses Delta Exchange REST API with HMAC-SHA256 signing.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from typing import Any, Dict, List, Optional

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


class DeltaAdapter(BaseExchange):
    """
    Delta Exchange adapter implementing BaseExchange interface.

    API: Delta Exchange v2 REST API
    Auth: HMAC-SHA256 with timestamp + method + path + body
    """

    MAKER_FEE = 0.0002    # 0.02%
    TAKER_FEE = 0.0005    # 0.05%

    def __init__(self, api_key: str, api_secret: str) -> None:
        super().__init__(name="delta", api_key=api_key, api_secret=api_secret)
        self._session: Optional[aiohttp.ClientSession] = None
        self._base_url = config.delta.rest_url

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    # ── Auth ─────────────────────────────────────────────────────

    def _sign(self, method: str, path: str, body: str = "") -> str:
        """HMAC-SHA256 signature for Delta Exchange."""
        timestamp = str(int(time.time()))
        message = f"{method}{timestamp}{path}{body}"
        return hmac.new(
            self._api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _auth_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        timestamp = str(int(time.time()))
        signature = self._sign(method, path, body)
        return {
            "api-key": self._api_key,
            "timestamp": timestamp,
            "signature": signature,
            "Content-Type": "application/json",
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
        params = params or {}
        session = self._ensure_session()
        url = f"{self._base_url}{path}"

        for attempt in range(max_retries):
            try:
                if method == "GET":
                    headers = self._auth_headers("GET", path) if signed else {}
                    async with session.get(url, params=params, headers=headers) as resp:
                        return await self._handle_response(resp)
                elif method == "POST":
                    body = json.dumps(params)
                    headers = self._auth_headers("POST", path, body) if signed else {}
                    async with session.post(url, data=body, headers=headers) as resp:
                        return await self._handle_response(resp)
                elif method == "DELETE":
                    body = json.dumps(params) if params else ""
                    headers = self._auth_headers("DELETE", path, body) if signed else {}
                    async with session.delete(url, data=body if body else None, headers=headers) as resp:
                        return await self._handle_response(resp)
                else:
                    raise ValueError(f"Unsupported method: {method}")

            except asyncio.TimeoutError:
                logger.warning("[delta] Timeout {} (attempt {}/{})", path, attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
            except aiohttp.ClientError as exc:
                logger.warning("[delta] Client error {} (attempt {}/{}): {}", path, attempt + 1, max_retries, exc)
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

        raise ExchangeError(f"[delta] Request failed after {max_retries} attempts: {path}")

    async def _handle_response(self, resp: aiohttp.ClientResponse) -> Dict:
        body = await resp.text()
        if resp.status != 200:
            raise ExchangeError(f"[delta] HTTP {resp.status}: {body[:200]}")

        data = json.loads(body)
        if "error" in data:
            raise ExchangeError(f"[delta] API error: {data['error']}")
        return data

    # ── Lifecycle ────────────────────────────────────────────────

    async def connect(self) -> None:
        try:
            await self._request("GET", "/v2/tickers", {}, signed=False)
            self._connected = True
            logger.info("[delta] Connected to Delta Exchange")
        except Exception as exc:
            logger.error("[delta] Connection failed: {}", exc)
            self._connected = False

    async def disconnect(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        self._connected = False
        logger.info("[delta] Disconnected")

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
            delta_type_map = {
                "MARKET": "market_order",
                "LIMIT": "limit_order",
                "STOP_MARKET": "market_order",
                "TAKE_PROFIT_MARKET": "market_order",
            }

            params: Dict[str, Any] = {
                "product_symbol": symbol,
                "side": side.lower(),
                "order_type": delta_type_map.get(order_type, "market_order"),
                "size": int(quantity),
            }

            if client_order_id:
                params["client_order_id"] = client_order_id

            if order_type in ("LIMIT", "STOP", "TAKE_PROFIT"):
                params["limit_price"] = str(price)
                tif_map = {"GTC": "gtc", "IOC": "ioc", "FOK": "fok"}
                params["time_in_force"] = tif_map.get(time_in_force, "gtc")

            if stop_price > 0:
                params["stop_order_type"] = "stop_loss" if "STOP" in order_type else "take_profit"
                params["stop_price"] = str(stop_price)

            if reduce_only:
                params["reduce_only"] = True

            data = await self._request("POST", "/v2/orders", params)
            order_data = data.get("result", data)
            self.record_success()
            return self._parse_order(order_data)

        except Exception as exc:
            self.record_failure()
            raise ExchangeError(f"[delta] Place order failed: {exc}")
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def cancel_order(
        self, symbol: str, order_id: str = "", client_order_id: str = "",
    ) -> ExchangeOrder:
        t0 = time.monotonic()
        try:
            params: Dict[str, Any] = {"product_symbol": symbol}
            if order_id:
                params["id"] = order_id

            await self._request("DELETE", "/v2/orders", params)
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
            data = await self._request("GET", f"/v2/orders/{order_id}", {}, signed=True)
            self.record_success()
            return self._parse_order(data.get("result", data))
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_open_orders(self, symbol: str = "") -> List[ExchangeOrder]:
        t0 = time.monotonic()
        try:
            params: Dict[str, Any] = {"state": "open"}
            if symbol:
                params["product_symbol"] = symbol

            data = await self._request("GET", "/v2/orders", params)
            self.record_success()
            return [self._parse_order(o) for o in data.get("result", [])]
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    # ── Positions & Account ──────────────────────────────────────

    async def get_positions(self) -> List[ExchangePosition]:
        t0 = time.monotonic()
        try:
            data = await self._request("GET", "/v2/positions", {})
            self.record_success()
            positions = []
            for p in data.get("result", []):
                qty = float(p.get("size", 0))
                if qty != 0:
                    entry = float(p.get("entry_price", 0))
                    mark = float(p.get("mark_price", 0))
                    positions.append(ExchangePosition(
                        symbol=p.get("product_symbol", ""),
                        side="LONG" if p.get("side") == "buy" else "SHORT",
                        quantity=abs(qty),
                        entry_price=entry,
                        mark_price=mark,
                        unrealized_pnl=float(p.get("unrealized_pnl", 0)),
                        leverage=int(float(p.get("leverage", 1))),
                        margin_type="cross",
                        notional=abs(qty * entry),
                        liquidation_price=float(p.get("liquidation_price", 0)),
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
            data = await self._request("GET", "/v2/wallet/balances", {})
            result = data.get("result", [])
            usdt = next((b for b in result if b.get("asset_symbol") == "USDT"), {})
            self.record_success()
            return AccountState(
                total_balance=float(usdt.get("balance", 0)),
                available_balance=float(usdt.get("balance", 0) - usdt.get("margin", 0)),
                total_unrealized_pnl=float(usdt.get("unrealized_pnl", 0)),
            )
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_balance(self) -> Dict[str, float]:
        t0 = time.monotonic()
        try:
            data = await self._request("GET", "/v2/wallet/balances", {})
            result = data.get("result", [])
            usdt = next((b for b in result if b.get("asset_symbol") == "USDT"), {})
            self.record_success()
            return {
                "balance": float(usdt.get("balance", 0)),
                "available": float(usdt.get("balance", 0) - usdt.get("margin", 0)),
                "unrealized_pnl": float(usdt.get("unrealized_pnl", 0)),
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
                "GET", f"/v2/l2orderbook/{symbol}", {},
                signed=False,
            )
            result = data.get("result", {})
            self.record_success()
            bids = [
                OrderbookLevel(price=float(b[0]), quantity=float(b[1]))
                for b in result.get("buy", [])[:depth]
            ]
            asks = [
                OrderbookLevel(price=float(a[0]), quantity=float(a[1]))
                for a in result.get("sell", [])[:depth]
            ]
            return OrderbookSnapshot(
                symbol=symbol, bids=bids, asks=asks, timestamp=time.time(),
            )
        except Exception as exc:
            self.record_failure()
            logger.warning("[delta] Orderbook failed for {}: {}", symbol, exc)
            return OrderbookSnapshot(symbol=symbol, timestamp=time.time())
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_mark_price(self, symbol: str) -> float:
        """Get mark price from Delta Exchange."""
        t0 = time.monotonic()
        try:
            data = await self._request(
                "GET", f"/v2/tickers/{symbol}", {}, signed=False,
            )
            result = data.get("result", {})
            self.record_success()
            return float(result.get("mark_price", 0.0))
        except Exception as exc:
            self.record_failure()
            logger.warning("[delta] Mark price fetch failed for {}: {}", symbol, exc)
            return 0.0
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_funding_rate(self, symbol: str) -> FundingInfo:
        t0 = time.monotonic()
        try:
            data = await self._request(
                "GET", f"/v2/tickers/{symbol}", {},
                signed=False,
            )
            result = data.get("result", {})
            self.record_success()
            return FundingInfo(exchange=self.name, symbol=symbol, funding_rate=float(result.get("funding_rate", 0.0)))
        except Exception as exc:
            self.record_failure()
            logger.warning("[delta] Funding rate failed for {}: {}", symbol, exc)
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
            "open": "NEW",
            "pending": "NEW",
            "partially_filled": "PARTIALLY_FILLED",
            "filled": "FILLED",
            "cancelled": "CANCELED",
            "closed": "FILLED",
            "rejected": "REJECTED",
        }
        return ExchangeOrder(
            order_id=str(data.get("id", "")),
            client_order_id=data.get("client_order_id", ""),
            symbol=data.get("product_symbol", ""),
            side=data.get("side", "").upper(),
            order_type=data.get("order_type", "").upper(),
            status=status_map.get(data.get("state", ""), data.get("state", "")),
            price=float(data.get("limit_price", 0)),
            avg_price=float(data.get("average_fill_price", 0)),
            quantity=float(data.get("size", 0)),
            executed_qty=float(data.get("size", 0)) if data.get("state") == "filled" else 0,
            time_in_force=data.get("time_in_force", "GTC"),
            stop_price=float(data.get("stop_price", 0)),
        )


class ExchangeError(Exception):
    pass
