"""
BinanceAdapter — Binance Futures implementation of BaseExchange.

Wraps the existing production-grade ExchangeAdapter from execution/
and exposes the unified BaseExchange interface for multi-exchange support.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional

from loguru import logger

import sys
from pathlib import Path
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from exchanges.base_exchange import (
    BaseExchange, ExchangeOrder, ExchangePosition, AccountState,
    OrderbookSnapshot, OrderbookLevel,
)
from execution.exchange_adapter import (
    ExchangeAdapter, OrderType, OrderSide, TimeInForce, PositionSide,
    ExchangeError,
)


class BinanceAdapter(BaseExchange):
    """
    Binance Futures adapter implementing BaseExchange interface.

    Uses the existing production ExchangeAdapter for HTTP/WS communication
    and adds orderbook depth and funding rate endpoints for the router.
    """

    # Fee schedule (VIP0 default)
    MAKER_FEE = 0.0002    # 0.02%
    TAKER_FEE = 0.0004    # 0.04%

    def __init__(self, api_key: str, api_secret: str) -> None:
        super().__init__(name="binance", api_key=api_key, api_secret=api_secret)
        self._adapter = ExchangeAdapter()

    async def connect(self) -> None:
        """Connect to Binance Futures."""
        try:
            await self._adapter.sync_time()
            self._connected = True
            logger.info("[binance] Connected to Binance Futures")
        except Exception as exc:
            logger.error("[binance] Connection failed: {}", exc)
            self._connected = False

    async def disconnect(self) -> None:
        """Disconnect from Binance."""
        await self._adapter.close()
        self._connected = False
        logger.info("[binance] Disconnected")

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
            o_side = OrderSide(side)
            o_type = OrderType(order_type)
            o_tif = TimeInForce(time_in_force)
            o_pos = PositionSide(position_side)

            result = await self._adapter.place_order(
                symbol=symbol,
                side=o_side,
                order_type=o_type,
                quantity=quantity,
                price=price,
                stop_price=stop_price,
                time_in_force=o_tif,
                reduce_only=reduce_only,
                close_position=close_position,
                position_side=o_pos,
                client_order_id=client_order_id,
                working_type=working_type,
                callback_rate=callback_rate,
            )
            self.record_success()
            return self._convert_order(result)
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def cancel_order(
        self, symbol: str, order_id: str = "", client_order_id: str = "",
    ) -> ExchangeOrder:
        t0 = time.monotonic()
        try:
            result = await self._adapter.cancel_order(
                symbol=symbol,
                order_id=int(order_id) if order_id else 0,
                client_order_id=client_order_id,
            )
            self.record_success()
            return self._convert_order(result)
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
            result = await self._adapter.get_order(
                symbol=symbol,
                order_id=int(order_id) if order_id else 0,
                client_order_id=client_order_id,
            )
            self.record_success()
            return self._convert_order(result)
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_open_orders(self, symbol: str = "") -> List[ExchangeOrder]:
        t0 = time.monotonic()
        try:
            results = await self._adapter.get_open_orders(symbol)
            self.record_success()
            return [self._convert_order(o) for o in results]
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    # ── Position & Account ───────────────────────────────────────

    async def get_positions(self) -> List[ExchangePosition]:
        t0 = time.monotonic()
        try:
            results = await self._adapter.get_positions()
            self.record_success()
            return [self._convert_position(p) for p in results]
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_account(self) -> AccountState:
        t0 = time.monotonic()
        try:
            result = await self._adapter.get_account()
            self.record_success()
            return AccountState(
                total_balance=result.total_balance,
                available_balance=result.available_balance,
                total_unrealized_pnl=result.total_unrealized_pnl,
                total_margin=result.total_margin,
                positions=[self._convert_position(p) for p in result.positions],
                update_time=result.update_time,
            )
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_balance(self) -> Dict[str, float]:
        t0 = time.monotonic()
        try:
            result = await self._adapter.get_balance()
            self.record_success()
            return result
        except Exception as exc:
            self.record_failure()
            raise
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    # ── Market Data (Router-specific) ────────────────────────────

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderbookSnapshot:
        """Get orderbook depth from Binance Futures REST API."""
        t0 = time.monotonic()
        try:
            params = {"symbol": symbol, "limit": depth}
            data = await self._adapter._request(
                "GET", "/fapi/v1/depth", params, signed=False,
            )
            self.record_success()
            bids = [
                OrderbookLevel(price=float(b[0]), quantity=float(b[1]))
                for b in data.get("bids", [])
            ]
            asks = [
                OrderbookLevel(price=float(a[0]), quantity=float(a[1]))
                for a in data.get("asks", [])
            ]
            return OrderbookSnapshot(
                symbol=symbol,
                bids=bids,
                asks=asks,
                timestamp=time.time(),
            )
        except Exception as exc:
            self.record_failure()
            logger.warning("[binance] Orderbook fetch failed for {}: {}", symbol, exc)
            return OrderbookSnapshot(symbol=symbol, timestamp=time.time())
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_mark_price(self, symbol: str) -> float:
        """Get mark price from Binance."""
        t0 = time.monotonic()
        try:
            params = {"symbol": symbol}
            data = await self._adapter._request(
                "GET", "/fapi/v1/premiumIndex", params, signed=False,
            )
            self.record_success()
            return float(data.get("markPrice", 0.0))
        except Exception as exc:
            self.record_failure()
            logger.warning("[binance] Mark price fetch failed for {}: {}", symbol, exc)
            return 0.0
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_funding_rate(self, symbol: str) -> FundingInfo:
        """Get current funding rate from Binance."""
        t0 = time.monotonic()
        try:
            params = {"symbol": symbol}
            data = await self._adapter._request(
                "GET", "/fapi/v1/premiumIndex", params, signed=False, # Use premiumIndex for more info
            )
            self.record_success()
            return FundingInfo(
                exchange=self.name,
                symbol=symbol,
                funding_rate=float(data.get("lastFundingRate", 0.0)),
                next_funding_time=int(data.get("nextFundingTime", 0)),
                mark_price=float(data.get("markPrice", 0.0)),
            )
        except Exception as exc:
            self.record_failure()
            logger.warning("[binance] Funding rate fetch failed for {}: {}", symbol, exc)
            return 0.0
        finally:
            self.record_latency((time.monotonic() - t0) * 1000)

    async def get_latency(self) -> ExchangeLatency:
        # BaseExchange already tracks latency, just return it
        return ExchangeLatency(exchange=self.name, ping_ms=self.get_latency_ms(), update_time=int(time.time()))

    # ── Fees ─────────────────────────────────────────────────────

    def get_maker_fee(self) -> float:
        return self.MAKER_FEE

    def get_taker_fee(self) -> float:
        return self.TAKER_FEE

    # ── Leverage ─────────────────────────────────────────────────

    async def set_leverage(self, symbol: str, leverage: int) -> Dict:
        return await self._adapter.set_leverage(symbol, leverage)

    async def set_margin_type(self, symbol: str, margin_type: str = "CROSSED") -> Dict:
        return await self._adapter.set_margin_type(symbol, margin_type)

    # ── Internal converters ──────────────────────────────────────

    @staticmethod
    def _convert_order(o: Any) -> ExchangeOrder:
        """Convert execution.ExchangeOrder to exchanges.ExchangeOrder."""
        return ExchangeOrder(
            order_id=str(o.order_id),
            client_order_id=o.client_order_id,
            symbol=o.symbol,
            side=o.side,
            order_type=o.order_type,
            status=o.status,
            price=o.price,
            avg_price=o.avg_price,
            quantity=o.quantity,
            executed_qty=o.executed_qty,
            cum_quote=o.cum_quote,
            time_in_force=o.time_in_force,
            reduce_only=o.reduce_only,
            close_position=o.close_position,
            stop_price=o.stop_price,
            working_type=o.working_type,
            update_time=o.update_time,
            transact_time=o.transact_time,
        )

    @staticmethod
    def _convert_position(p: Any) -> ExchangePosition:
        """Convert execution.ExchangePosition to exchanges.ExchangePosition."""
        return ExchangePosition(
            symbol=p.symbol,
            side=p.side,
            quantity=p.quantity,
            entry_price=p.entry_price,
            mark_price=p.mark_price,
            unrealized_pnl=p.unrealized_pnl,
            leverage=p.leverage,
            margin_type=p.margin_type,
            isolated_margin=p.isolated_margin,
            position_side=p.position_side,
            notional=p.notional,
            liquidation_price=p.liquidation_price,
            update_time=p.update_time,
        )
