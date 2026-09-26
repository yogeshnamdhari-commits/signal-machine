"""Runtime hardening for exchange adapters.

This patch layer exists so all supported entry points can opt into the same
integrity boundary without duplicating exchange parsing logic.
"""
from __future__ import annotations

from typing import Any, Dict

from loguru import logger


async def _safe_ticker_handler(self: Any, tickers: list) -> None:
    """Cache 24h ticker data without fabricating trade/order-flow events."""
    import time

    for t in tickers:
        sym = t.get("s", "")
        price = float(t.get("c", 0) or 0)
        if not sym or price <= 0:
            continue
        self._ws_ticker_cache[sym] = {
            "symbol": sym,
            "price": price,
            "volume": float(t.get("v", 0) or 0),
            "quoteVolume": float(t.get("q", 0) or 0),
            "change_pct": float(t.get("P", 0) or 0),
            "price_change": float(t.get("p", 0) or 0),
            "high": float(t.get("h", 0) or 0),
            "low": float(t.get("l", 0) or 0),
            "open": float(t.get("o", 0) or 0),
            "count": int(t.get("n", 0) or 0),
            "last_update": time.time(),
            "data_quality": "REAL_TICKER_ONLY",
        }
    logger.debug("Cached {} real 24h ticker observations; no synthetic trades emitted", len(tickers))


def _provenance_book_ticker(self: Any, d: Dict) -> None:
    import time

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
        import asyncio
        return asyncio.create_task(self._callback("depth", depth))
    return None


def apply_integrity_patches() -> None:
    """Install hardening once per process."""
    from exchanges.binance_ws import BinanceWebSocket

    if getattr(BinanceWebSocket, "_signal_machine_integrity_patched", False):
        return
    BinanceWebSocket._on_ticker_arr = _safe_ticker_handler  # type: ignore[method-assign]
    BinanceWebSocket._on_book_ticker = _provenance_book_ticker  # type: ignore[method-assign]
    BinanceWebSocket._signal_machine_integrity_patched = True
