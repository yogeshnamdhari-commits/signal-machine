"""
Funding Rate Engine — Data source metadata and funding analysis for heatmaps.

Data Source: Binance Futures REST API — /fapi/v1/premiumIndex (PRIMARY)
  - Endpoint: GET https://fapi.binance.com/fapi/v1/premiumIndex
  - Returns: {"symbol":"BTCUSDT","lastFundingRate":"-0.0000073","markPrice":"60704.1"}
  - Polled every 60 s for ALL symbols (production endpoint, use_data_url=True)
  - Display: funding_percent = lastFundingRate × 100
  - WS markPrice stream used only as fallback (may be testnet when --testnet flag)
  - Derived: z-score, extreme detection, cumulative cost, direction
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# ── Data source metadata (consumed by dashboard heatmaps) ──
DATA_SOURCE = {
    "name": "Funding Rate",
    "providers": ["Binance", "Bybit", "OKX"],
    "apis": {
        "Binance": "GET /fapi/v1/premiumIndex — lastFundingRate (production endpoint)",
        "WS_Fallback": "markPrice@arr stream (testnet when --testnet, skipped if REST available)",
    },
    "aggregation": "Direct from Binance production REST API (single source of truth)",
    "metrics": {
        "funding_rate": "Current 8h funding rate (weighted avg, %)",
        "z_score": "Deviation from rolling mean in σ units",
        "is_extreme": "True if |z| > 2.0 → mean-reversion signal",
        "cumulative_8h": "Cumulative rate over last 8h window",
        "direction": "long_paying / short_paying / neutral",
    },
    "refresh": "Real-time (funding events, ~8h intervals)",
    "scope": "Top perpetual futures across 3 major exchanges",
    "caveats": [
        "Binance production REST API is the single source of truth",
        "WS markPrice stream skipped when production data available (prevents testnet contamination)",
        "Funding settles every 8h — rate shown is latest estimated, not realized",
        "Formula: display_percent = lastFundingRate × 100",
    ],
}


def get_source_label() -> str:
    """Return a one-line human-readable source label for display under heatmaps."""
    return "Funding: GET /fapi/v1/premiumIndex → lastFundingRate × 100 = %"


def get_source_detail() -> str:
    """Return a multi-line source detail block for expanders."""
    providers = ", ".join(DATA_SOURCE["providers"])
    lines = [
        f"**Providers:** {providers}",
        f"**Aggregation:** {DATA_SOURCE['aggregation']}",
        f"**Refresh:** {DATA_SOURCE['refresh']}",
        f"**Scope:** {DATA_SOURCE['scope']}",
    ]
    for caveat in DATA_SOURCE["caveats"]:
        lines.append(f"⚠️ {caveat}")
    return " · ".join(lines)
