"""
EMA_V5 Cache — EMA value caching to avoid recalculation.
Moved from cache.py into cache/ package for namespace compatibility.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from loguru import logger

from ..config import ema_v5_config
from ..utils import ema, sma, slope, atr


class EMACache:
    """Caches EMA values per symbol. Only recalculates newest candles."""

    def __init__(self) -> None:
        self._cache: Dict[str, Dict] = {}
        self._last_update: Dict[str, float] = {}

    def get_emas(self, symbol: str) -> Optional[Dict]:
        """Get cached EMA values for a symbol."""
        if symbol not in self._cache:
            return None
        age = time.time() - self._last_update.get(symbol, 0)
        if age > ema_v5_config.cache.cache_ttl_sec:
            return None  # stale
        return self._cache[symbol]

    def update(self, symbol: str, klines: List[Dict]) -> Optional[Dict]:
        """Update EMA cache from klines. Returns computed EMAs.

        Only recalculates if new data is available.
        """
        if not klines or len(klines) < ema_v5_config.ema.min_candles:
            return None

        cfg = ema_v5_config.ema
        closes = [k.get("close", 0) for k in klines]
        highs = [k.get("high", 0) for k in klines]
        lows = [k.get("low", 0) for k in klines]
        volumes = [k.get("volume", 0) for k in klines]

        ema20 = ema(closes, cfg.fast)
        ema50 = ema(closes, cfg.medium)
        ema144 = ema(closes, cfg.institutional)
        ema200 = ema(closes, cfg.long_term)
        vol_sma = sma(volumes, ema_v5_config.volume.sma_period)
        atr_val = atr(highs, lows, closes, 14)

        if not ema20 or not ema50 or not ema144 or not ema200:
            return None

        # Only keep last values (don't store full arrays)
        result = {
            "ema20": ema20[-1],
            "ema50": ema50[-1],
            "ema144": ema144[-1],
            "ema200": ema200[-1],
            "ema20_prev": ema20[-2] if len(ema20) > 1 else ema20[-1],
            "ema50_prev": ema50[-2] if len(ema50) > 1 else ema50[-1],
            "ema144_prev": ema144[-2] if len(ema144) > 1 else ema144[-1],
            "ema200_prev": ema200[-2] if len(ema200) > 1 else ema200[-1],
            "ema20_slope": slope(ema20, cfg.slope_lookback),
            "ema50_slope": slope(ema50, cfg.slope_lookback),
            "ema144_slope": slope(ema144, cfg.slope_lookback),
            "ema200_slope": slope(ema200, cfg.slope_lookback),
            "vol_sma20": vol_sma,
            "atr_14": atr_val,
            "last_close": closes[-1],
            "last_high": highs[-1],
            "last_low": lows[-1],
            "last_volume": volumes[-1],
            "candle_count": len(klines),
        }

        self._cache[symbol] = result
        self._last_update[symbol] = time.time()

        # Evict old entries
        if len(self._cache) > ema_v5_config.cache.max_cached_symbols:
            oldest = min(self._last_update, key=self._last_update.get)
            del self._cache[oldest]
            del self._last_update[oldest]

        return result

    def clear(self, symbol: Optional[str] = None) -> None:
        """Clear cache for one or all symbols."""
        if symbol:
            self._cache.pop(symbol, None)
            self._last_update.pop(symbol, None)
        else:
            self._cache.clear()
            self._last_update.clear()

    @property
    def size(self) -> int:
        return len(self._cache)
