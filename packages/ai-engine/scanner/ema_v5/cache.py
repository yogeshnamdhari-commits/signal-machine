"""
EMA_V5 Cache — EMA value caching to avoid recalculation.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional, Tuple

from loguru import logger

from .config import ema_v5_config
from .utils import ema, sma, slope, atr


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

        # ── DIAG-4: History continuity — ALL duplicates + gaps ──
        open_times = [k.get("open_time", 0) for k in klines]
        _adj_dups = 0
        _all_dups = 0
        _gaps = 0
        _seen = set()
        for _i, _ot in enumerate(open_times):
            if _ot in _seen:
                _all_dups += 1
            _seen.add(_ot)
            if _i > 0:
                if open_times[_i] == open_times[_i - 1]:
                    _adj_dups += 1
                elif open_times[_i] - open_times[_i - 1] > 300_000:
                    _gaps += 1
        _unique = len(_seen)
        _total = len(open_times)
        if _all_dups > 0:
            logger.warning(
                "🔍 DIAG[KLINE_QUALITY] sym={} total={} unique={} non_adj_dups={} adj_dups={} gaps={}",
                symbol, _total, _unique, _all_dups, _adj_dups, _gaps,
            )
            # Log first 10 duplicate open_times for diagnosis
            _dup_vals = []
            _counted = set()
            for _ot in open_times:
                if _ot in _counted and _ot not in _dup_vals:
                    _dup_vals.append(_ot)
                _counted.add(_ot)
                if len(_dup_vals) >= 10:
                    break
            logger.warning("🔍 DIAG[KLINE_QUALITY] sym={} dup_timestamps={}", symbol, _dup_vals[:10])
        elif _gaps > 0:
            logger.warning("🔍 DIAG[KLINE_QUALITY] sym={} candles={} dups=0 gaps={}", symbol, len(klines), _gaps)
        else:
            logger.info("🔍 DIAG[KLINE_QUALITY] sym={} candles={} unique={} dups=0 gaps=0 ✓", symbol, _total, _unique)

        ema20 = ema(closes, cfg.fast)
        ema50 = ema(closes, cfg.medium)
        ema144 = ema(closes, cfg.institutional)
        ema200 = ema(closes, cfg.long_term)
        vol_sma = sma(volumes, ema_v5_config.volume.sma_period)
        atr_val = atr(highs, lows, closes, 14)

        if not ema20 or not ema50 or not ema144 or not ema200:
            return None

        # ── DIAG-5: Log first EMA computation for independent verification ──
        if not getattr(self, '_diag_ema_sampled', False):
            logger.info(
                "🔍 DIAG[EMA_SAMPLE] sym={} candles={} close={:.8f} "
                "EMA20={:.8f} EMA50={:.8f} EMA144={:.8f} EMA200={:.8f}",
                symbol, len(klines), closes[-1],
                ema20[-1], ema50[-1], ema144[-1], ema200[-1],
            )
            self._diag_ema_sampled = True

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
            "prev_volume": volumes[-2] if len(volumes) > 1 else 0,
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
