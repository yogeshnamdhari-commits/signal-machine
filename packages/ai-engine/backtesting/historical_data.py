"""
Historical Data Engine — fetch, cache, and serve OHLCV data from Binance.
Supports multi-timeframe data with local SQLite caching and incremental updates.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiohttp
import numpy as np
import pandas as pd
from loguru import logger

from config import config


@dataclass
class DataRequest:
    symbol: str
    interval: str
    start_ms: int
    end_ms: int
    limit: int = 1500


@dataclass
class DataStats:
    symbol: str
    interval: str
    bars: int
    start_time: datetime
    end_time: datetime
    coverage_pct: float
    gaps: int


class HistoricalDataEngine:
    """
    Fetches and manages historical OHLCV data from Binance Futures.
    
    Features:
    - Multi-timeframe support (1m, 5m, 15m, 1h, 4h, 1d)
    - Local SQLite caching with incremental updates
    - Batch downloading with rate limiting
    - Data validation and gap detection
    - DataFrame export for backtesting
    """

    INTERVAL_MS = {
        "1m": 60_000,
        "3m": 180_000,
        "5m": 300_000,
        "15m": 900_000,
        "30m": 1_800_000,
        "1h": 3_600_000,
        "2h": 7_200_000,
        "4h": 14_400_000,
        "6h": 21_600_000,
        "8h": 28_800_000,
        "12h": 43_200_000,
        "1d": 86_400_000,
    }

    def __init__(self) -> None:
        self._cache: Dict[str, pd.DataFrame] = {}
        self._session: Optional[aiohttp.ClientSession] = None
        self._rate_limit_delay = 0.1  # 100ms between requests
        self._max_retries = 3
        self._base_url = config.binance.rest_url
        self._db_path = Path("data/database/historical_klines.db")
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self) -> None:
        """Initialize HTTP session and database."""
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"X-MBX-APIKEY": config.binance.api_key} if config.binance.api_key else {},
        )
        await self._init_db()
        logger.info("HistoricalData engine ready — cache: {}", self._db_path)

    async def _init_db(self) -> None:
        """Initialize SQLite cache database."""
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS klines (
                    symbol TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    open_time INTEGER NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    trades INTEGER DEFAULT 0,
                    PRIMARY KEY (symbol, interval, open_time)
                )
            """)
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_klines_lookup 
                ON klines (symbol, interval, open_time)
            """)
            await db.commit()

    async def stop(self) -> None:
        """Cleanup resources."""
        if self._session:
            await self._session.close()
        logger.info("HistoricalData engine stopped")

    # ── Data Fetching ────────────────────────────────────────────

    async def fetch_klines(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
    ) -> pd.DataFrame:
        """
        Fetch historical klines from Binance API.
        Returns DataFrame with columns: [open_time, open, high, low, close, volume, trades]
        """
        if interval not in self.INTERVAL_MS:
            raise ValueError(f"Unsupported interval: {interval}")

        all_klines: List[List] = []
        current_start = start_ms
        bar_ms = self.INTERVAL_MS[interval]

        while current_start < end_ms:
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": current_start,
                "endTime": end_ms,
                "limit": 1500,
            }

            for attempt in range(self._max_retries):
                try:
                    url = f"{self._base_url}/fapi/v1/klines"
                    async with self._session.get(url, params=params) as resp:
                        if resp.status == 429:
                            wait = int(resp.headers.get("Retry-After", 5))
                            logger.warning("Rate limited — waiting {}s", wait)
                            await asyncio.sleep(wait)
                            continue
                        resp.raise_for_status()
                        data = await resp.json()
                        break
                except Exception as e:
                    logger.error("Fetch error (attempt {}/{}): {}", attempt + 1, self._max_retries, e)
                    if attempt == self._max_retries - 1:
                        raise
                    await asyncio.sleep(2 ** attempt)

            if not data:
                break

            all_klines.extend(data)
            current_start = data[-1][6] + bar_ms  # close_time + 1 bar
            await asyncio.sleep(self._rate_limit_delay)

        if not all_klines:
            return pd.DataFrame()

        df = self._parse_klines(all_klines)
        logger.info("Fetched {} bars for {} {} ({} → {})",
                     len(df), symbol, interval,
                     datetime.fromtimestamp(start_ms / 1000),
                     datetime.fromtimestamp(end_ms / 1000))
        return df

    def _parse_klines(self, raw: List[List]) -> pd.DataFrame:
        """Parse raw Binance kline data into DataFrame."""
        records = []
        for k in raw:
            records.append({
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": k[6],
                "quote_volume": float(k[7]),
                "trades": int(k[8]),
                "taker_buy_volume": float(k[9]),
                "taker_buy_quote_volume": float(k[10]),
            })
        df = pd.DataFrame(records)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")
        return df

    # ── Cache Management ─────────────────────────────────────────

    async def get_cached_data(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
    ) -> Optional[pd.DataFrame]:
        """Retrieve data from cache, returning None if not available."""
        cache_key = f"{symbol}_{interval}"
        if cache_key in self._cache:
            df = self._cache[cache_key]
            mask = (df["open_time"] >= pd.to_datetime(start_ms, unit="ms")) & \
                   (df["open_time"] <= pd.to_datetime(end_ms, unit="ms"))
            cached = df[mask]
            if len(cached) > 0:
                return cached.copy()
        return None

    async def store_to_cache(self, symbol: str, interval: str, df: pd.DataFrame) -> None:
        """Store DataFrame to local cache."""
        import aiosqlite
        if df.empty:
            return

        records = []
        for _, row in df.iterrows():
            records.append((
                symbol, interval,
                int(row["open_time"].timestamp() * 1000) if hasattr(row["open_time"], "timestamp") else row["open_time"],
                row["open"], row["high"], row["low"], row["close"],
                row["volume"], row.get("trades", 0),
            ))

        async with aiosqlite.connect(self._db_path) as db:
            await db.executemany("""
                INSERT OR REPLACE INTO klines 
                (symbol, interval, open_time, open, high, low, close, volume, trades)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, records)
            await db.commit()

        # Update in-memory cache
        cache_key = f"{symbol}_{interval}"
        if cache_key in self._cache:
            self._cache[cache_key] = pd.concat([self._cache[cache_key], df]).drop_duplicates(
                subset=["open_time"]
            ).sort_values("open_time").reset_index(drop=True)
        else:
            self._cache[cache_key] = df.sort_values("open_time").reset_index(drop=True)

        logger.debug("Cached {} bars for {} {}", len(df), symbol, interval)

    async def load_from_db(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
    ) -> pd.DataFrame:
        """Load cached data from SQLite database."""
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("""
                SELECT * FROM klines 
                WHERE symbol = ? AND interval = ? 
                AND open_time >= ? AND open_time <= ?
                ORDER BY open_time
            """, (symbol, interval, start_ms, end_ms))
            rows = await cursor.fetchall()

        if not rows:
            return pd.DataFrame()

        records = [dict(r) for r in rows]
        df = pd.DataFrame(records)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        return df

    # ── High-Level API ───────────────────────────────────────────

    async def get_historical_data(
        self,
        symbol: str,
        interval: str = "5m",
        days: int = 30,
        use_cache: bool = True,
    ) -> pd.DataFrame:
        """
        Main entry point: get historical data with caching.
        Fetches from API if not in cache, stores result.
        """
        now_ms = int(time.time() * 1000)
        start_ms = now_ms - (days * 24 * 60 * 60 * 1000)

        # Try cache first
        if use_cache:
            cached = await self.get_cached_data(symbol, interval, start_ms, now_ms)
            if cached is not None and len(cached) > 0:
                logger.debug("Cache hit: {} {} bars={}", symbol, interval, len(cached))
                return cached

            # Try SQLite
            db_data = await self.load_from_db(symbol, interval, start_ms, now_ms)
            if len(db_data) > 0:
                await self.store_to_cache(symbol, interval, db_data)
                return db_data

        # Fetch from API
        df = await self.fetch_klines(symbol, interval, start_ms, now_ms)
        if not df.empty:
            await self.store_to_cache(symbol, interval, df)
        return df

    async def get_multi_timeframe_data(
        self,
        symbol: str,
        timeframes: Optional[List[str]] = None,
        days: int = 30,
    ) -> Dict[str, pd.DataFrame]:
        """Fetch data for multiple timeframes."""
        if timeframes is None:
            timeframes = ["1m", "5m", "15m", "1h", "4h"]

        results: Dict[str, pd.DataFrame] = {}
        for tf in timeframes:
            results[tf] = await self.get_historical_data(symbol, tf, days)
            await asyncio.sleep(0.2)  # Rate limit protection

        return results

    async def get_symbols_data(
        self,
        symbols: List[str],
        interval: str = "5m",
        days: int = 30,
    ) -> Dict[str, pd.DataFrame]:
        """Fetch data for multiple symbols."""
        results: Dict[str, pd.DataFrame] = {}
        for sym in symbols:
            results[sym] = await self.get_historical_data(sym, interval, days)
            await asyncio.sleep(0.3)  # Rate limit protection

        return results

    # ── Data Validation ──────────────────────────────────────────

    def validate_data(self, df: pd.DataFrame) -> DataStats:
        """Validate data quality and detect gaps."""
        if df.empty:
            return DataStats(
                symbol="", interval="", bars=0,
                start_time=datetime.now(), end_time=datetime.now(),
                coverage_pct=0, gaps=0,
            )

        interval = "unknown"
        if len(df) >= 2:
            diff = (df["open_time"].iloc[1] - df["open_time"].iloc[0]).total_seconds() * 1000
            for name, ms in self.INTERVAL_MS.items():
                if abs(diff - ms) < ms * 0.1:
                    interval = name
                    break

        # Detect gaps
        gaps = 0
        if len(df) >= 2 and interval in self.INTERVAL_MS:
            expected_ms = self.INTERVAL_MS[interval]
            time_diffs = df["open_time"].diff().dt.total_seconds() * 1000
            gaps = int((time_diffs > expected_ms * 1.5).sum())

        expected_bars = 0
        if len(df) >= 2 and interval in self.INTERVAL_MS:
            total_ms = (df["open_time"].iloc[-1] - df["open_time"].iloc[0]).total_seconds() * 1000
            expected_bars = int(total_ms / self.INTERVAL_MS[interval]) + 1

        coverage = len(df) / max(expected_bars, 1) * 100 if expected_bars > 0 else 100

        return DataStats(
            symbol=df.get("symbol", pd.Series([""])).iloc[0] if "symbol" in df.columns else "",
            interval=interval,
            bars=len(df),
            start_time=df["open_time"].iloc[0],
            end_time=df["open_time"].iloc[-1],
            coverage_pct=min(coverage, 100),
            gaps=gaps,
        )

    def detect_gaps(self, df: pd.DataFrame, interval: str = "5m") -> List[Tuple[datetime, datetime]]:
        """Detect time gaps in the data."""
        if df.empty or interval not in self.INTERVAL_MS:
            return []

        expected_ms = self.INTERVAL_MS[interval]
        gaps = []
        for i in range(1, len(df)):
            diff = (df["open_time"].iloc[i] - df["open_time"].iloc[i - 1]).total_seconds() * 1000
            if diff > expected_ms * 1.5:
                gaps.append((df["open_time"].iloc[i - 1], df["open_time"].iloc[i]))

        return gaps

    # ── Utility ──────────────────────────────────────────────────

    def to_numpy(self, df: pd.DataFrame) -> Dict[str, np.ndarray]:
        """Convert DataFrame to numpy arrays for fast computation."""
        return {
            "open": df["open"].values,
            "high": df["high"].values,
            "low": df["low"].values,
            "close": df["close"].values,
            "volume": df["volume"].values,
            "trades": df["trades"].values if "trades" in df.columns else np.zeros(len(df)),
        }

    def resample(self, df: pd.DataFrame, target_interval: str) -> pd.DataFrame:
        """Resample data to a different timeframe."""
        if df.empty or target_interval not in self.INTERVAL_MS:
            return df

        df = df.set_index("open_time")
        rule = {
            "1m": "1min", "5m": "5min", "15m": "15min", "30m": "30min",
            "1h": "1h", "4h": "4h", "1d": "1D",
        }.get(target_interval, "5min")

        resampled = df.resample(rule).agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "trades": "sum" if "trades" in df.columns else "first",
        }).dropna()

        return resampled.reset_index()

    def add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add common technical indicators to the DataFrame."""
        if df.empty or len(df) < 20:
            return df

        df = df.copy()

        # SMA
        for period in [20, 50, 200]:
            df[f"sma_{period}"] = df["close"].rolling(window=period).mean()

        # EMA
        for period in [12, 26]:
            df[f"ema_{period}"] = df["close"].ewm(span=period, adjust=False).mean()

        # RSI
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df["rsi"] = 100 - (100 / (1 + rs))

        # MACD
        df["macd"] = df["ema_12"] - df["ema_26"]
        df["macd_signal"] = df["macd"].ewm(span=9, adjust=False).mean()
        df["macd_hist"] = df["macd"] - df["macd_signal"]

        # Bollinger Bands
        df["bb_mid"] = df["sma_20"]
        bb_std = df["close"].rolling(window=20).std()
        df["bb_upper"] = df["bb_mid"] + 2 * bb_std
        df["bb_lower"] = df["bb_mid"] - 2 * bb_std

        # ATR
        high_low = df["high"] - df["low"]
        high_close = (df["high"] - df["close"].shift()).abs()
        low_close = (df["low"] - df["close"].shift()).abs()
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df["atr"] = tr.rolling(window=14).mean()

        # Volume SMA
        df["volume_sma"] = df["volume"].rolling(window=20).mean()

        return df
