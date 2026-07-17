"""
OKXAdapter — OKX V5 API implementation of BaseExchange.

Production-grade adapter for OKX perpetual swaps.
Uses OKX V5 REST API with HMAC-SHA256 signing.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
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


class OKXAdapter(BaseExchange):
    """
    OKX V5 adapter implementing BaseExchange interface.

    API: OKX V5 REST
    Auth: HMAC-SHA256 with timestamp + method + requestPath + body
    Rate limits: 20 requests/2s (private), 20 requests/2s (public)
    """

    MAKER_FEE = 0.0002    # 0.02%
    TAKER_FEE = 0.0005    # 0.05%

    def __init__(self, api_key: str, api_secret: str, passphrase: str = "") -> None:
        super().__init__(name="okx", api_key=api_key, api_secret=api_secret)
        self._passphrase = passphrase
        self._session: Optional[aiohttp.ClientSession] = None
        self._base_url = config.okx.rest_url

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    # ── Auth ─────────────────────────────────────────────────────

    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """HMAC-SHA256 signature for OKX V5."""
        message = f"{timestamp}{method.upper()}{path}{body}"
        mac = hmac.new(
            self._api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _auth_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        sign = self._sign(timestamp, method, path, body)
        return {
            "OK-ACCESS-KEY": self._api_key,
            "OK-ACCESS-SIGN": sign,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self._passphrase,
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
                    query = "&".join(f"{k}={v}" for k, v in params.items() if v)
                    full_path = f"{path}?{query}" if query else path
                    full_url = f"{url}?{query}" if query else url
                    headers = self._auth_headers("GET", full_path) if signed else {}
                    async with session.get(full_url, headers=headers) as resp:
                        return await self._handle_response(resp)
                elif method == "POST":
                    body = json.dumps(params)
                    headers = self._auth_headers("POST", path, body) if signed else {}
                    headers["Content-Type"] = "application/json"
                    async with session.post(url, data=body, headers=headers) as resp:
                        return await self._handle_response(resp)
                else:
                    raise ValueError(f"Unsupported method: {method}")

            except asyncio.TimeoutError:
                logger.warning("[okx] Timeout {} (attempt {}/{})", path, attempt + 1, max_retries)
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
            except aiohttp.ClientError as exc:
                logger.warning("[okx] Client error {} (attempt {}/{}): {}", path, attempt + 1, max_retries, exc)
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 ** attempt)

        raise ExchangeError(f"[okx] Request failed after {max_retries} attempts: {path}")

    async def _handle_response(self, resp: aiohttp.ClientResponse) -> Dict:
        body = await resp.text()
        if resp.status != 200:
            raise ExchangeError(f"[okx] HTTP {resp.status}: {body[:200]}")

        data = json.loads(body)
        code = data.get("code", "0")
        if code != "0":
            raise ExchangeError(f"[okx] API error {code}: {data.get('msg', '')}")
        return data

    # ── Lifecycle ────────────────────────────────────────────────

    async def connect(self) -> None:
        try:
            await self._request("GET", "/api/v5/public/time", {}, signed=False)
            self._connected = True
            logger.info("[okx] Connected to OKX V5")
        except Exception as exc:
            logger.error("[okx] Connection failed: {}", exc)
            self._connected = False

    async def disconnect(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
        self._connected = False
        logger.info("[okx] Disconnected")

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
            # OKX uses instId, tdMode, side, ordType, sz
            okx_type_map = {
                "MARKET": "market",
                "LIMIT": "limit",
                "STOP_MARKET": "conditional",
                "TAKE_PROFIT_MARKET": "conditional",
            }

            params: Dict[str, Any] = {
                "instId": symbol.replace("USDT", "-USDT-SWAP"),
                "tdMode": "cross",
                "side": "buy" if side == "BUY" else "sell",
                "ordType": okx_type_map.get(order_type, "market"),
                "sz": str(quantity),
            }

            if client_order_id:
                params["clOrdId"] = client_order_id

            if order_type in ("LIMIT", "STOP", "TAKE_PROFIT"):
                params["px"] = str(price)
                tif_map = {"GTC": "GTC", "IOC": "IOC", "FOK": "FOK", "GTX": "post_only"}
                params["force"] = tif_map.get(time_in_force, "GTC")

            if reduce_only:
                params["reduceOnly"] = True

            data = await self._request("POST", "/api/v5/trade/order", params)
            result_list = data.get("data", [])
            if result_list:
                order_id = result_list[0].get("ordId", "")
                await asyncio.sleep(0.1)
                order = await self.get_order(symbol, order_id=order_id)
                self.record_success()
                return order

            self.record_success()
            return ExchangeOrder(symbol=symbol, status="NEW")
        except Exception as exc:
            self.record_failure()
            raise ExchangeError(f"[okx] Place order failed: {exc}")
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def cancel_order(
        self, symbol: str, order_id: str = "", client_order_id: str = "",
    ) -> ExchangeOrder:
        t0 = time.monotonic()
        try:
            inst_id = symbol.replace("USDT", "-USDT-SWAP")
            params: Dict[str, Any] = {"instId": inst_id}
            if order_id:
                params["ordId"] = order_id
            if client_order_id:
                params["clOrdId"] = client_order_id

            await self._request("POST", "/api/v5/trade/cancel-order", params)
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
            inst_id = symbol.replace("USDT", "-USDT-SWAP")
            params: Dict[str, Any] = {"instId": inst_id}
            if order_id:
                params["ordId"] = order_id
            if client_order_id:
                params["clOrdId"] = client_order_id

            data = await self._request("GET", "/api/v5/trade/order", params)
            result_list = data.get("data", [])
            if result_list:
                self.record_success()
                return self._parse_order(result_list[0])
            self.record_success()
            return ExchangeOrder(symbol=symbol, status="NOT_FOUND")
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_open_orders(self, symbol: str = "") -> List[ExchangeOrder]:
        t0 = time.monotonic()
        try:
            params: Dict[str, Any] = {"instType": "SWAP"}
            if symbol:
                params["instId"] = symbol.replace("USDT", "-USDT-SWAP")
            data = await self._request("GET", "/api/v5/trade/orders-pending", params)
            self.record_success()
            return [self._parse_order(o) for o in data.get("data", [])]
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
                "GET", "/api/v5/account/positions",
                {"instType": "SWAP"},
            )
            self.record_success()
            positions = []
            for p in data.get("data", []):
                qty = float(p.get("pos", 0))
                if qty != 0:
                    positions.append(ExchangePosition(
                        symbol=p.get("instId", "").replace("-USDT-SWAP", "USDT"),
                        side="LONG" if p.get("posSide") == "long" else "SHORT",
                        quantity=abs(qty),
                        entry_price=float(p.get("avgPx", 0)),
                        mark_price=float(p.get("markPx", 0)),
                        unrealized_pnl=float(p.get("upl", 0)),
                        leverage=int(float(p.get("lever", 1))),
                        margin_type=p.get("mgnMode", "cross"),
                        notional=abs(float(p.get("notionalUsd", 0))),
                        liquidation_price=float(p.get("liqPx", 0)),
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
            data = await self._request("GET", "/api/v5/account/balance", {})
            details = data.get("data", [{}])[0].get("details", [])
            usdt = next((d for d in details if d.get("ccy") == "USDT"), {})
            self.record_success()
            return AccountState(
                total_balance=float(usdt.get("eq", 0)),
                available_balance=float(usdt.get("availBal", 0)),
                total_unrealized_pnl=float(usdt.get("upl", 0)),
            )
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_balance(self) -> Dict[str, float]:
        t0 = time.monotonic()
        try:
            data = await self._request("GET", "/api/v5/account/balance", {})
            details = data.get("data", [{}])[0].get("details", [])
            usdt = next((d for d in details if d.get("ccy") == "USDT"), {})
            self.record_success()
            return {
                "balance": float(usdt.get("eq", 0)),
                "available": float(usdt.get("availBal", 0)),
                "unrealized_pnl": float(usdt.get("upl", 0)),
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
            inst_id = symbol.replace("USDT", "-USDT-SWAP")
            data = await self._request(
                "GET", "/api/v5/market/books",
                {"instId": inst_id, "sz": str(min(depth, 400))},
                signed=False,
            )
            result = data.get("data", [{}])[0]
            self.record_success()
            bids = [
                OrderbookLevel(price=float(b[0]), quantity=float(b[1]))
                for b in result.get("bids", [])
            ]
            asks = [
                OrderbookLevel(price=float(a[0]), quantity=float(a[1]))
                for a in result.get("asks", [])
            ]
            return OrderbookSnapshot(
                symbol=symbol, bids=bids, asks=asks, timestamp=time.time(),
            )
        except Exception as exc:
            self.record_failure()
            logger.warning("[okx] Orderbook failed for {}: {}", symbol, exc)
            return OrderbookSnapshot(symbol=symbol, timestamp=time.time())
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_mark_price(self, symbol: str) -> float:
        """Get mark price from OKX."""
        t0 = time.monotonic()
        try:
            inst_id = symbol.replace("USDT", "-USDT-SWAP")
            data = await self._request(
                "GET", "/api/v5/market/tickers", {"instId": inst_id}, signed=False,
            )
            result = data.get("data", [])
            self.record_success()
            if result:
                return float(result[0].get("markPx", 0.0))
            return 0.0
        except Exception as exc:
            self.record_failure()
            logger.warning("[okx] Mark price fetch failed for {}: {}", symbol, exc)
            return 0.0
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_funding_rate(self, symbol: str) -> FundingInfo:
        t0 = time.monotonic()
        try:
            inst_id = symbol.replace("USDT", "-USDT-SWAP")
            data = await self._request(
                "GET", "/api/v5/public/funding-rate",
                {"instId": inst_id},
                signed=False,
            )
            result = data.get("data", [])
            self.record_success()
            if result:
                return FundingInfo(exchange=self.name, symbol=symbol, funding_rate=float(result[0].get("fundingRate", 0.0)))
            return FundingInfo(exchange=self.name, symbol=symbol, funding_rate=0.0)
        except Exception as exc:
            self.record_failure()
            logger.warning("[okx] Funding rate failed for {}: {}", symbol, exc)
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
            "live": "NEW",
            "partially_filled": "PARTIALLY_FILLED",
            "filled": "FILLED",
            "canceled": "CANCELED",
            "order_failed": "REJECTED",
        }
        return ExchangeOrder(
            order_id=data.get("ordId", ""),
            client_order_id=data.get("clOrdId", ""),
            symbol=data.get("instId", "").replace("-USDT-SWAP", "USDT"),
            side=data.get("side", "").upper(),
            order_type=data.get("ordType", "").upper(),
            status=status_map.get(data.get("state", ""), data.get("state", "")),
            price=float(data.get("px", 0)),
            avg_price=float(data.get("avgPx", 0)),
            quantity=float(data.get("sz", 0)),
            executed_qty=float(data.get("accFillSz", 0)),
            time_in_force=data.get("force", "GTC"),
            stop_price=float(data.get("triggerPx", 0)),
        )


class ExchangeError(Exception):
    pass
