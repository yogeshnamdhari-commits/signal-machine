"""
EMA_V5 Institutional Strategy — Production Plug-in.

A completely isolated EMA-based signal generation module that integrates
with the existing engine through approved extension points only.

Usage:
    from scanner.ema_v5 import EMAv5Scanner
    scanner = EMAv5Scanner()
    signal = await scanner.evaluate(symbol, market_data, regime_data)
"""
from .scanner import EMAv5Scanner
from .config import EMAv5Config, ema_v5_config

__all__ = ["EMAv5Scanner", "EMAv5Config", "ema_v5_config"]
__version__ = "5.0.0"
