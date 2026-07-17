"""
Open Interest Engine — Data source metadata and OI analysis for heatmaps.

Data Source: Binance Futures WebSocket — <symbol>@openInterest stream
  - Stream: wss://fstream.binance.com/ws/<symbol>@openInterest (3-second push)
  - Format: {"e":"openInterest","E":1672515782136,"s":"BTCUSDT","o":"117347.301"}
  - 'o' = current open interest in CONTRACTS (not USD)
  - Real-time updates — no REST polling needed (bypasses IP ban on /fapi/v1/openInterest)
  - USD conversion: oi_usd = float(openInterest) × mark_price (from /fapi/v1/premiumIndex)
  - Mark price used for valuation (not last trade price)
  - Derived: OI change %, spike/flush detection, positioning classification
  - Cross-check: OI delta vs price delta → divergence / squeeze signals
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# ── Data source metadata (consumed by dashboard heatmaps) ──
DATA_SOURCE = {
    "name": "Open Interest",
    "provider": "Binance",
    "api": "WebSocket <symbol>@openInterest stream (3-second push, real-time)",
    "metrics": {
        "open_interest": "Current OI in USD (contracts × mark price)",
        "oi_change_pct": "ΔOI vs previous reading (%)",
        "oi_regime": "Bullish OI / Bearish OI / Neutral OI",
        "positioning": "Long Build-up / Short Build-up / Unwinding / Covering",
        "oi_strength": "Composite 0–100 from change magnitude + trend",
    },
    "refresh": "Real-time via WebSocket (3-second push per symbol)",
    "scope": "Binance Futures perpetuals — 250 symbols",
    "caveats": [
        "Binance only — does not aggregate Bybit/OKX/Delta OI",
        "OI updates every 3 seconds (not tick-by-tick)",
        "Raw value is CONTRACTS — converted to USD using mark price from premiumIndex",
        "Mark price (not last trade price) used for accurate valuation",
    ],
}


def get_source_label() -> str:
    """Return a one-line human-readable source label for display under heatmaps."""
    return "OI: WebSocket @openInterest (3s push) → contracts × mark_price = USD"


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
