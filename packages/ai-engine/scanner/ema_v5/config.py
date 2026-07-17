"""
EMA_V5 Configuration — All strategy parameters in one place.
No hardcoded values. Every parameter is configurable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass
class EMAConfig:
    """EMA calculation parameters."""
    fast: int = 20
    medium: int = 50
    institutional: int = 144
    long_term: int = 200
    slope_lookback: int = 5          # bars to compute slope
    min_candles: int = 220           # need enough for EMA200 warmup


@dataclass
class TrendConfig:
    """Trend classification parameters."""
    ema_chain_tolerance: float = 0.0  # EMA20 > EMA50 > EMA144 > EMA200 (strict)
    slope_threshold: float = 0.0      # slope must be > 0 for bull, < 0 for bear
    min_confirmation_bars: int = 3    # bars above/below EMA chain to confirm


@dataclass
class PullbackConfig:
    """Pullback detection parameters."""
    touch_tolerance_pct: float = 0.3  # price within 0.3% of EMA = "touch"
    max_pullback_pct: float = 2.0     # max pullback before invalidating
    require_bounce: bool = True       # price must bounce after touch


@dataclass
class CandleConfig:
    """Candlestick pattern parameters."""
    body_ratio_min: float = 0.5       # body must be >= 50% of range for engulfing
    wick_ratio_min: float = 2.0       # wick must be >= 2x body for pin bar
    confirmation_close: bool = True   # pattern must close to confirm


@dataclass
class VolumeConfig:
    """Volume confirmation parameters."""
    sma_period: int = 20
    min_volume_ratio: float = 1.0     # volume > SMA20 (1.0x minimum)
    volume_surge_ratio: float = 1.5   # surge = 1.5x SMA20


@dataclass
class ConfidenceConfig:
    """Confidence scoring parameters."""
    min_confidence: float = 40.0      # v33 recalibrated: was 60, unreachable with inverted components. Now uses trend_score as base (replaces missing institutional_score).
    # Component weights (must sum to 1.0)
    # v33: Inverted MSS/FVG/Volatility based on multivariate analysis
    # regime: STRONG positive correlation (+0.04) → reward
    # trend(MSS): negative correlation (-0.04) → penalty
    # pullback: neutral → keep positive (binary detection)
    # candle(FVG): negative correlation (-0.02) → penalty
    # volume(Vol): STRONG negative correlation (-0.15) → penalty
    trend_weight: float = 0.10         # v33: reduced from 0.25 (negatively correlated)
    pullback_weight: float = 0.15      # v33: reduced from 0.25 (neutral)
    candle_weight: float = 0.10        # v33: reduced from 0.20 (negatively correlated)
    volume_weight: float = 0.05        # v33: reduced from 0.15 (strongly negative)
    regime_weight: float = 0.10        # v33: reduced from 0.15 (binary, causes inflation)
    # v33: Session penalty (not in formula yet — needs session parameter)
    session_penalty_ny: float = 0.05   # 5% confidence reduction for NY session


@dataclass
class SignalConfig:
    """Signal generation parameters."""
    min_rr: float = 1.5              # minimum risk:reward
    sl_atr_mult: float = 1.5         # SL = 1.5 × ATR from entry
    tp1_rr: float = 1.5              # TP1 at 1.5R
    tp2_rr: float = 3.0              # TP2 at 3.0R
    tp3_rr: float = 5.0              # TP3 at 5.0R
    tp1_exit_pct: float = 0.35       # close 35% at TP1
    tp2_exit_pct: float = 0.40       # close 40% at TP2
    tp3_exit_pct: float = 0.25       # close 25% at TP3


@dataclass
class TradeConfig:
    """Trade management parameters."""
    risk_per_trade_pct: float = 1.0   # 1% account risk
    max_positions: int = 3            # max concurrent EMA_V5 positions
    max_hold_hours: int = 48          # force close after 48h
    breakeven_at_r: float = 1.0       # move SL to BE at 1R
    trailing_atr_mult: float = 1.0    # trail at 1.0 × ATR


@dataclass
class StateConfig:
    """State machine parameters."""
    persist_state: bool = True
    state_file: str = "data/ema_v5_state.json"


@dataclass
class CacheConfig:
    """EMA cache parameters."""
    max_cached_symbols: int = 500
    cache_ttl_sec: int = 300          # 5 minutes


@dataclass
class CooldownConfig:
    """Signal cooldown parameters."""
    same_symbol_sec: int = 3600       # 1 hour between same-symbol signals
    global_sec: int = 60              # 1 minute between any signals
    max_signals_per_hour: int = 10


@dataclass
class LogConfig:
    """Logging parameters."""
    log_file: str = "data/logs/ema_v5.log"
    log_level: str = "INFO"
    max_log_size_mb: int = 10
    log_rotation: str = "1 day"
    log_retention: str = "7 days"


@dataclass
class EMAv5Config:
    """Master configuration for EMA_V5 strategy."""
    ema: EMAConfig = field(default_factory=EMAConfig)
    trend: TrendConfig = field(default_factory=TrendConfig)
    pullback: PullbackConfig = field(default_factory=PullbackConfig)
    candle: CandleConfig = field(default_factory=CandleConfig)
    volume: VolumeConfig = field(default_factory=VolumeConfig)
    confidence: ConfidenceConfig = field(default_factory=ConfidenceConfig)
    signal: SignalConfig = field(default_factory=SignalConfig)
    trade: TradeConfig = field(default_factory=TradeConfig)
    state: StateConfig = field(default_factory=StateConfig)
    cache: CacheConfig = field(default_factory=CacheConfig)
    cooldown: CooldownConfig = field(default_factory=CooldownConfig)
    log: LogConfig = field(default_factory=LogConfig)

    # Allowed regimes for EMA_V5 signals
    allowed_regimes: Tuple[str, ...] = (
        "trending_bull", "trending_bear", "breakout", "compression",
    )

    # Blocked regimes
    blocked_regimes: Tuple[str, ...] = ("volatile", "unknown")

    # Timeframe to use for EMA calculations
    primary_tf: str = "5m"

    # Additional timeframes for multi-TF confirmation
    confirmation_tfs: Tuple[str, ...] = ("15m", "1h")


# Global singleton
ema_v5_config = EMAv5Config()
