"""
YOG'Z INSTITUTIONAL TRADING COMPANY — Production Configuration
Immutable, validated, env-driven.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Tuple

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_DIR = DATA_DIR / "database"
LOGS_DIR = DATA_DIR / "logs"

for _d in (DATA_DIR, DB_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _env_bool(key: str, default: bool = False) -> bool:
    return _env(key, str(default)).lower() in ("true", "1", "yes")


def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(_env(key, str(default)))
    except ValueError:
        return default


def _env_float(key: str, default: float = 0.0) -> float:
    try:
        return float(_env(key, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class BinanceConfig:
    api_key: str = field(default_factory=lambda: _env("BINANCE_API_KEY"))
    api_secret: str = field(default_factory=lambda: _env("BINANCE_API_SECRET"))
    testnet: bool = field(default_factory=lambda: _env_bool("BINANCE_TESTNET", True))
    ws_production: str = "wss://fstream.binance.com"
    ws_testnet: str = "wss://stream.binancefuture.com"
    rest_production: str = "https://fapi.binance.com"
    rest_testnet: str = "https://testnet.binancefuture.com"
    rate_limit_rpm: int = 1200

    @property
    def ws_url(self) -> str:
        return self.ws_testnet if self.testnet else self.ws_production

    @property
    def rest_url(self) -> str:
        return self.rest_testnet if self.testnet else self.rest_production

    @property
    def data_rest_url(self) -> str:
        return self.rest_production


@dataclass(frozen=True)
class ScannerConfig:
    quote_asset: str = "USDT"
    min_volume_24h: float = 2_000_000
    max_symbols: int = 250
    timeframes: Tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h")
    primary_timeframe: str = "5m"
    orderbook_depth: int = 20
    scan_interval_sec: int = 5
    signal_cooldown_sec: int = 600
    # Real per-symbol production feeds. depth20@100ms is a top-20 L2 snapshot,
    # so DOM analytics never misinterprets diff-depth updates as full books.
    # 250 * 4 + 3 global streams = 1003, below the 1024-stream limit.
    ws_streams: Tuple[str, ...] = ("aggTrade", "bookTicker", "openInterest", "depth20@100ms")
    kline_intervals: Tuple[str, ...] = ("5m",)
    global_streams: Tuple[str, ...] = ("!markPrice@arr", "!forceOrder@arr")
    iceberg_threshold: float = 0.7
    spoofing_threshold: float = 0.6
    absorption_threshold: float = 0.65
    sweep_threshold: float = 0.75
    stop_hunt_threshold: float = 0.7
    regime_lookback: int = 100
    volatility_window: int = 20


@dataclass(frozen=True)
class RiskConfig:
    max_position_pct: float = 2.5
    max_leverage: int = 20
    max_daily_loss_pct: float = 3.0
    max_drawdown_pct: float = 8.0
    risk_per_trade_pct: float = 0.75
    max_open_positions: int = 15
    sl_atr_mult: float = 2.5
    sl_atr_mult_long: float = 2.5
    tp_atr_mult: float = 4.5
    max_sl_distance_pct: float = 5.0
    max_positions_per_cycle: int = 5
    regime_direction_gate: bool = True
    quality_gate_score: float = 90.0
    tier_elite_score: float = 95.0
    tier_elite_mult: float = 2.50
    tier_strong_score: float = 90.0
    tier_strong_mult: float = 1.80
    tier_marginal_score: float = 85.0
    tier_marginal_mult: float = 0.40
    max_price_age_sec: float = 60.0


@dataclass(frozen=True)
class TelegramConfig:
    enabled: bool = field(default_factory=lambda: _env_bool("TELEGRAM_ENABLED"))
    bot_token: str = field(default_factory=lambda: _env("TELEGRAM_BOT_TOKEN"))
    chat_id: str = field(default_factory=lambda: _env("TELEGRAM_CHAT_ID"))
    min_confidence: float = 0.7


@dataclass(frozen=True)
class BybitConfig:
    api_key: str = field(default_factory=lambda: _env("BYBIT_API_KEY"))
    api_secret: str = field(default_factory=lambda: _env("BYBIT_API_SECRET"))
    testnet: bool = field(default_factory=lambda: _env_bool("BYBIT_TESTNET", True))
    ws_production: str = "wss://stream.bybit.com"
    ws_testnet: str = "wss://stream-testnet.bybit.com"
    rest_production: str = "https://api.bybit.com"
    rest_testnet: str = "https://api-testnet.bybit.com"
    rate_limit_rpm: int = 600

    @property
    def ws_url(self) -> str:
        return self.ws_testnet if self.testnet else self.ws_production

    @property
    def rest_url(self) -> str:
        return self.rest_testnet if self.testnet else self.rest_production


@dataclass(frozen=True)
class OKXConfig:
    api_key: str = field(default_factory=lambda: _env("OKX_API_KEY"))
    api_secret: str = field(default_factory=lambda: _env("OKX_API_SECRET"))
    passphrase: str = field(default_factory=lambda: _env("OKX_PASSPHRASE"))
    testnet: bool = field(default_factory=lambda: _env_bool("OKX_TESTNET", True))
    ws_production: str = "wss://ws.okx.com:8443/ws/api/v5/public"
    ws_testnet: str = "wss://wspap.okx.com:8443/ws/v5/public"
    rest_production: str = "https://www.okx.com"
    rest_testnet: str = "https://www.okx.com"
    rate_limit_rpm: int = 600

    @property
    def ws_url(self) -> str:
        return self.ws_testnet if self.testnet else self.ws_production

    @property
    def rest_url(self) -> str:
        return self.rest_testnet if self.testnet else self.rest_production


@dataclass(frozen=True)
class DeltaConfig:
    api_key: str = field(default_factory=lambda: _env("DELTA_API_KEY"))
    api_secret: str = field(default_factory=lambda: _env("DELTA_API_SECRET"))
    testnet: bool = field(default_factory=lambda: _env_bool("DELTA_TESTNET", True))
    ws_production: str = "wss://socket.india.delta.exchange"
    ws_testnet: str = "wss://socket-ind-pub.testnet.deltaex.org"
    rest_production: str = "https://api.india.delta.exchange"
    rest_testnet: str = "https://cdn-ind.testnet.deltaex.org"
    rate_limit_rpm: int = 300

    @property
    def ws_url(self) -> str:
        return self.ws_testnet if self.testnet else self.ws_production

    @property
    def rest_url(self) -> str:
        return self.rest_testnet if self.testnet else self.rest_production


@dataclass(frozen=True)
class AIConfig:
    weights: Dict[str, float] = field(default_factory=lambda: {
        "order_flow": 0.20,
        "institutional": 0.15,
        "regime": 0.15,
        "momentum": 0.10,
        "volume": 0.05,
        "imbalance": 0.10,
        "funding": 0.15,
        "fake_breakout": 0.10,
    })
    min_factors: int = 2
    min_confidence: float = 0.60
    fake_breakout_lookback: int = 50


@dataclass(frozen=True)
class DirectionalBiasConfig:
    enabled: bool = True
    max_direction_ratio: float = 0.70
    penalty_floor: float = 0.40
    divergence_threshold: float = 0.55
    divergence_bonus_max: float = 0.10
    extreme_imbalance_ratio: float = 0.85
    extreme_penalty: float = 0.30
    uniform_direction_bonus: float = 0.08
    min_signals_for_penalty: int = 5


@dataclass(frozen=True)
class DirectionalExposureConfig:
    enabled: bool = True
    max_same_direction: int = 3
    window_minutes: int = 120
    max_same_direction_pct: float = 0.60
    max_positions_per_window: int = 6


@dataclass(frozen=True)
class DashboardConfig:
    host: str = "localhost"
    port: int = 8501
    refresh_sec: int = 5


@dataclass(frozen=True)
class IntradayConfig:
    min_rr: float = 2.0
    target_rr: float = 2.5
    min_sl_pct: float = 0.0015
    min_tp_pct: float = 0.0025
    min_quality_score: float = 45.0
    quality_tier_a: float = 75.0
    quality_tier_b: float = 55.0
    session_confidence_boost: float = 0.05
    low_liquidity_penalty: float = 0.07
    vol_extreme_pctile: float = 90.0
    vol_high_pctile: float = 75.0
    vol_low_pctile: float = 20.0
    enabled: bool = True
    enhance_sl_tp: bool = True
    apply_confidence_adj: bool = True


@dataclass(frozen=True)
class ArbitrageConfig:
    enabled: bool = field(default_factory=lambda: _env_bool("ARBITRAGE_ENABLED", False))
    scan_interval_sec: float = 1.0
    min_profit_bps: float = 5.0
    min_funding_diff_bps: float = 10.0
    min_basis_bps: float = 50.0
    estimated_slippage_bps: float = 2.0
    default_position_size_usdt: float = 1000.0
    min_execution_score: float = 70.0
    execution_timeout_sec: float = 5.0
    core_symbols: Tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    statistical_pairs: Tuple[Tuple[str, str], ...] = (("BTCUSDT", "ETHUSDT"),)
    statistical_window_size: int = 100
    statistical_zscore_threshold: float = 2.0
    statistical_edge_multiplier: float = 0.001


@dataclass(frozen=True)
class ProfitFilterConfig:
    enabled: bool = field(default_factory=lambda: _env_bool("PROFIT_FILTER_ENABLED", True))
    blocking: bool = field(default_factory=lambda: _env_bool("PROFIT_FILTER_BLOCKING", False))
    block_watch: bool = field(default_factory=lambda: _env_bool("PROFIT_FILTER_BLOCK_WATCH", True))
    min_execute_score: int = field(default_factory=lambda: _env_int("PROFIT_FILTER_MIN_EXECUTE_SCORE", 70))
    watch_floor_score: int = field(default_factory=lambda: _env_int("PROFIT_FILTER_WATCH_FLOOR_SCORE", 60))


@dataclass(frozen=True)
class AppConfig:
    env: str = field(default_factory=lambda: _env("APP_ENV", "development"))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    binance: BinanceConfig = field(default_factory=BinanceConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    bybit: BybitConfig = field(default_factory=BybitConfig)
    okx: OKXConfig = field(default_factory=OKXConfig)
    delta: DeltaConfig = field(default_factory=DeltaConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    directional_bias: DirectionalBiasConfig = field(default_factory=DirectionalBiasConfig)
    directional_exposure: DirectionalExposureConfig = field(default_factory=DirectionalExposureConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    intraday: IntradayConfig = field(default_factory=IntradayConfig)
    arbitrage: ArbitrageConfig = field(default_factory=ArbitrageConfig)
    profit_filter: ProfitFilterConfig = field(default_factory=ProfitFilterConfig)


config = AppConfig()
