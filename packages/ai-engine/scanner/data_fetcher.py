"""Compatibility data fetcher for risk-state reconstruction.

The risk engine historically imported scanner.data_fetcher.DataFetcher.
Keep that boundary explicit while delegating real kline retrieval to the
canonical Binance Futures adapter. No synthetic market data is generated.
"""

from __future__ import annotations

from typing import Dict, List

from exchanges.binance_ws import BinanceWebSocket


class DataFetcher:
    """Small REST-backed kline fetcher used by risk-state reconstruction."""

    def __init__(self) -> None:
        self._client = BinanceWebSocket()

    async def get_klines(
        self, symbol: str, interval: str = "5m", limit: int = 100
    ) -> List[Dict]:
        """Return real Binance Futures klines."""
        return await self._client.get_klines(symbol, interval, limit)

    async def close(self) -> None:
        """Release the underlying HTTP session."""
        await self._client.stop()
