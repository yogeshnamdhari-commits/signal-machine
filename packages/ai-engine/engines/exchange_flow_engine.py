"""
Exchange Flow Engine — Data source metadata and flow analysis for heatmaps.

Data Source: Binance Futures aggTrade WebSocket (taker side)
  - Each trade counted ONCE: taker_buy_vol OR taker_sell_vol (never both)
  - Synthetic trades from !ticker@arr are EXCLUDED (fake quantities)
  - Rolling window: last 2000 trades for buy/sell volume calculation
  - Flow signal: >0.60 = BUY, <0.40 = SELL, otherwise NEUTRAL
  - Flow strength: percentile ranking over 24h net_flow distribution
  - Validation: net_flow must be < 24h traded volume
  - 24h Volume: uses quoteVolume from /fapi/v1/ticker/24hr (already USDT)
  - No cross-exchange — Binance taker trades only (most liquid perpetual book)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# ── Data source metadata (consumed by dashboard heatmaps) ──
DATA_SOURCE = {
    "name": "Exchange Flow",
    "provider": "Binance",
    "api": "Futures WebSocket — Trade Stream",
    "metrics": {
        "net_flow": "Taker Buy Vol − Taker Sell Vol (USD)",
        "buy_sell_ratio": "Taker Buy Vol / Total Vol (0–1)",
        "taker_dominance": "% of volume that is taker-initiated",
        "flow_signal": ">0.60 = BUY, <0.40 = SELL, otherwise NEUTRAL",
        "flow_strength": "Percentile ranking (0–100) over 24h net_flow distribution",
    },
    "refresh": "Real-time (every trade tick)",
    "scope": "Binance Futures perpetuals — single exchange",
    "caveats": [
        "Binance only — does not aggregate Bybit/OKX/Delta taker flow",
        "Each trade counted exactly once (no volume accumulation duplication)",
        "24h Volume from REST /fapi/v1/ticker/24hr quoteVolume — already in USDT",
        "Flow signal is relative to recent trade history, not absolute",
    ],
}


def get_source_label() -> str:
    """Return a one-line human-readable source label for display under heatmaps."""
    return "Flow: Binance WS aggTrade — real taker trades only, no synthetic"


def get_source_detail() -> str:
    """Return a multi-line source detail block for expanders."""
    lines = [
        f"**Provider:** {DATA_SOURCE['provider']}",
        f"**API:** {DATA_SOURCE['api']}",
        f"**Refresh:** {DATA_SOURCE['refresh']}",
        f"**Scope:** {DATA_SOURCE['scope']}",
    ]
    for caveat in DATA_SOURCE["caveats"]:
        lines.append(f"⚠️ {caveat}")
    return " · ".join(lines)
