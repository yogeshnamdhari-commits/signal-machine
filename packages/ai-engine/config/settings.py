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

# ── Paths ────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_DIR = DATA_DIR / "database"
LOGS_DIR = DATA_DIR / "logs"

for _d in (DATA_DIR, DB_DIR, LOGS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ── Helpers ──────────────────────────────────────────────────────
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


# ── Sub-configs (frozen = immutable) ─────────────────────────────
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
        """Always use production endpoints for market data display (OI, tickers, funding).
        Trading uses testnet, but market data should match production Binance."""
        return self.rest_production


@dataclass(frozen=True)
class ScannerConfig:
    quote_asset: str = "USDT"
    min_volume_24h: float = 2_000_000  # Lowered to capture 400+ symbols (was $5M)
    max_symbols: int = 250  # Top 250 by volume (was 50, now covers 200+ perps)
    timeframes: Tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h")
    primary_timeframe: str = "5m"
    orderbook_depth: int = 20
    scan_interval_sec: int = 5
    signal_cooldown_sec: int = 600
    # WebSocket streams to subscribe per symbol (lightweight default)
    # Options: "aggTrade", "trade", "bookTicker", "depth@100ms", "kline_5m", etc.
    ws_streams: Tuple[str, ...] = ("aggTrade", "bookTicker", "openInterest")  # Per-symbol streams: aggTrade+bookTicker+OI via WS (bypasses banned REST /fapi/v1/openInterest)
    kline_intervals: Tuple[str, ...] = ("5m",)  # Kline intervals to subscribe via WS for regime detection
    global_streams: Tuple[str, ...] = ("!markPrice@arr", "!forceOrder@arr")  # Real-time funding + liquidation-time funding + liquidations via WS
    # Pattern thresholds
    iceberg_threshold: float = 0.7
    spoofing_threshold: float = 0.6
    absorption_threshold: float = 0.65
    sweep_threshold: float = 0.75
    stop_hunt_threshold: float = 0.7
    regime_lookback: int = 100
    volatility_window: int = 20


@dataclass(frozen=True)
class RiskConfig:
    max_position_pct: float = 2.5           # ↑ slightly larger for high-conviction setups
    max_leverage: int = 20
    max_daily_loss_pct: float = 3.0          # ↓ tighter daily loss limit (was 5.0)
    max_drawdown_pct: float = 8.0            # ↓ tighter max drawdown (was 10.0)
    risk_per_trade_pct: float = 0.75         # Raised from 0.40 — higher conviction trades get more capital
    max_open_positions: int = 15             # ↑ balanced for 250-symbol universe (was 6, too restrictive)
    sl_atr_mult: float = 2.5                 # FIX: Wider SL (was 2.0) — reduces noise stopouts
    # Evidence: Winners avg SL=1.91%, Losers avg SL=1.17% — losers too tight
    sl_atr_mult_long: float = 2.5             # Unified SL width — LONG/SHORT use same multiplier
    tp_atr_mult: float = 4.5                 # ↓ slightly closer TP for higher hit rate (was 5.0)
    max_sl_distance_pct: float = 5.0        # 🆕 Reject signals with SL > 5% from entry
    max_positions_per_cycle: int = 5          # Raised from 3 — more opportunities per cycle
    regime_direction_gate: bool = True        # 🆕 Block LONG in bear, SHORT in bull
    quality_gate_score: float = 90.0          # 🆕 Minimum institutional score to trade
    tier_elite_score: float = 95.0            # 🆕 Score for 2.5x sizing
    tier_elite_mult: float = 2.50             # 🆕 Position multiplier for elite trades
    tier_strong_score: float = 90.0           # 🆕 Score for 1.8x sizing
    tier_strong_mult: float = 1.80            # 🆕 Position multiplier for strong trades
    tier_marginal_score: float = 85.0         # 🆕 Score for reduced sizing
    tier_marginal_mult: float = 0.40          # 🆕 Position multiplier for marginal trades


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
    ws_testnet: str = "wss://socket.india.delta.exchange"
    rest_production: str = "https://api.india.delta.exchange"
    rest_testnet: str = "https://api.india.delta.exchange"
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
    """Directional Neutralizer — caps signal direction imbalance per cycle."""
    enabled: bool = True
    max_direction_ratio: float = 0.70        # Max 70% signals in one direction
    penalty_floor: float = 0.40              # Minimum multiplier for penalised side
    divergence_threshold: float = 0.55       # Minority must be < 55% for bonus
    divergence_bonus_max: float = 0.10       # Max additive confidence bonus
    extreme_imbalance_ratio: float = 0.85    # 85%+ triggers extreme penalty
    extreme_penalty: float = 0.30            # Hard penalty at extreme imbalance
    uniform_direction_bonus: float = 0.08    # Bonus when ALL signals are same direction
    min_signals_for_penalty: int = 5         # Need ≥ 5 signals before penalties apply


@dataclass(frozen=True)
class DirectionalExposureConfig:
    """Directional Exposure Limiter — prevents stacking same-direction positions.

    Unlike the DirectionalNeutralizer (per-cycle signal balance), this blocks
    new entries when too many positions in the same direction are opened within
    a rolling time window.

    Root cause: June 16 — 4 SHORTs opened in 2h, all hit SL.
    """
    enabled: bool = True
    max_same_direction: int = 3              # Max same-direction positions in window
    window_minutes: int = 120                # Rolling window (2 hours)
    max_same_direction_pct: float = 0.60     # Soft cap: max 60% in one direction
    max_positions_per_window: int = 6        # Rate limit: max entries per window


@dataclass(frozen=True)
class DashboardConfig:
    host: str = "localhost"
    port: int = 8501
    refresh_sec: int = 5


@dataclass(frozen=True)
class IntradayConfig:
    """Intraday signal enhancement configuration."""
    # SL/TP ATR multipliers by regime (fallback from intraday enhancer)
    min_rr: float = 2.0          # Minimum risk-reward ratio
    target_rr: float = 2.5       # Target R:R for optimal signals
    min_sl_pct: float = 0.0015   # Minimum SL distance (0.15%)
    min_tp_pct: float = 0.0025   # Minimum TP distance (0.25%)
    # Quality thresholds
    min_quality_score: float = 45.0  # Minimum intraday quality to pass (lowered from 55)
    quality_tier_a: float = 75.0     # A-tier threshold
    quality_tier_b: float = 55.0     # B-tier threshold
    # Session filtering
    session_confidence_boost: float = 0.05  # Boost during high-liquidity sessions
    low_liquidity_penalty: float = 0.07     # Penalty during off-hours
    # Volatility thresholds
    vol_extreme_pctile: float = 90.0   # Above this = extreme volatility
    vol_high_pctile: float = 75.0      # Above this = high volatility
    vol_low_pctile: float = 20.0       # Below this = low volatility
    # Enable/disable enhancement
    enabled: bool = True
    enhance_sl_tp: bool = True          # Adaptive SL/TP placement
    apply_confidence_adj: bool = True   # Apply quality-based confidence adjustment


@dataclass(frozen=True)
class ArbitrageConfig:
    enabled: bool = field(default_factory=lambda: _env_bool("ARBITRAGE_ENABLED", False))
    scan_interval_sec: float = 1.0
    min_profit_bps: float = 5.0 # Minimum net profit in basis points to consider
    min_funding_diff_bps: float = 10.0 # Minimum funding rate difference in bps
    min_basis_bps: float = 50.0 # Minimum basis in bps
    estimated_slippage_bps: float = 2.0 # Default slippage for calculation
    default_position_size_usdt: float = 1000.0
    min_execution_score: float = 70.0 # Minimum score from ArbitrageRanker to execute
    execution_timeout_sec: float = 5.0 # Max time to wait for arbitrage leg fills
    core_symbols: Tuple[str, ...] = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    statistical_pairs: Tuple[Tuple[str, str], ...] = (("BTCUSDT", "ETHUSDT"),)
    statistical_window_size: int = 100
    statistical_zscore_threshold: float = 2.0
    statistical_edge_multiplier: float = 0.001 # Multiplier for Z-score to get net edge


# ── Root config ──────────────────────────────────────────────────
@dataclass(frozen=True)
class AppConfig:
    env: str = field(default_factory=lambda: _env("APP_ENV", "development"))
    debug: bool = field(default_factory=lambda: _env_bool("DEBUG", True))
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    binance: BinanceConfig = field(default_factory=BinanceConfig)
    bybit: BybitConfig = field(default_factory=BybitConfig)
    okx: OKXConfig = field(default_factory=OKXConfig)
    delta: DeltaConfig = field(default_factory=DeltaConfig)
    scanner: ScannerConfig = field(default_factory=ScannerConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    directional_bias: DirectionalBiasConfig = field(default_factory=DirectionalBiasConfig)
    directional_exposure: DirectionalExposureConfig = field(default_factory=DirectionalExposureConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    intraday: IntradayConfig = field(default_factory=IntradayConfig)
    arbitrage: ArbitrageConfig = field(default_factory=ArbitrageConfig)


# ── Singleton ────────────────────────────────────────────────────
config = AppConfig()
