"""Canonical root dashboard.

Read-only presentation layer. It never manufactures a BUY/SELL signal from
component votes; executable signal state comes only from the canonical Python
engine bridge.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

_AI_ROOT = Path(__file__).resolve().parent.parent
if str(_AI_ROOT) not in sys.path:
    sys.path.insert(0, str(_AI_ROOT))

from dashboard.data_bridge import reader as bridge_reader
from dashboard.live_sheet_contract import (
    build_signal_display,
    display_value,
    freshness_state,
    live_observation_values,
)

st.set_page_config(
    page_title="DeltaTerminal — Canonical Live Data",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st_autorefresh(interval=1000, key="root_canonical_refresh")


def fmt(value, suffix=""):
    if value is None or value == "":
        return "UNAVAILABLE"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    a = abs(number)
    if a >= 1e12:
        return f"{number/1e12:.2f}T{suffix}"
    if a >= 1e9:
        return f"{number/1e9:.2f}B{suffix}"
    if a >= 1e6:
        return f"{number/1e6:.2f}M{suffix}"
    if a >= 1e3:
        return f"{number/1e3:.2f}K{suffix}"
    return f"{number:.4f}{suffix}"

def evidence(factor):
    return f"{factor.state.value} | {factor.quality.value}"


def _format_liq_risk(display: Dict, liq_feed_state: str) -> str:
    """Format liquidation risk with feed state awareness.
    
    - OBSERVED: Show risk level (LOW/MEDIUM/HIGH)
    - NO_OBSERVED_LIQUIDATION: Show "NO_DATA" (feed is live but no events for this symbol)
    - UNAVAILABLE: Show "UNAVAILABLE" (feed is down/stale)
    """
    if liq_feed_state == "OBSERVED":
        risk = display.get("liq_risk")
        if risk is not None:
            return str(risk).upper()
        return "LOW"
    elif liq_feed_state == "NO_OBSERVED_LIQUIDATION":
        return "NO_DATA"
    else:
        return "UNAVAILABLE"


market_data = bridge_reader.read_market_data()
signals = bridge_reader.read_signals()
fresh = bridge_reader.read_market_data_freshness()
engine_health = bridge_reader.read_engine_health()
snapshot_ts = float(fresh.get("timestamp", 0) or 0)
state = freshness_state(snapshot_ts, max_age=60)
signal_lookup = {str(s.get("symbol")): s for s in signals if s.get("symbol")} if state == "LIVE" else {}
liq_feed = engine_health.get("liquidation_feed", {}) if isinstance(engine_health, dict) else {}
liq_feed_status = str(liq_feed.get("status", "UNAVAILABLE"))
liq_event_count = int(liq_feed.get("observed_event_count", 0) or 0)
liq_last_age = liq_feed.get("last_event_age_sec")
if liq_last_age is None:
    liq_last_text = "none observed"
else:
    liq_last_text = f"{float(liq_last_age):.0f}s ago"

st.title("📡 DeltaTerminal — Canonical Live Data")
st.caption(
    f"Snapshot: {state} · age {fresh.get('age', 0):.1f}s · symbols {fresh.get('rows', 0)} · "
    "Signal authority: Python engine"
)
st.caption(
    f"Liquidation feed: {liq_feed_status} · observed forceOrder events: {liq_event_count} · "
    f"last event: {liq_last_text}"
)

rows = []
for source_row in market_data:
    row = dict(source_row)
    # Preserve the engine's own observation timestamp; only fall back to the bridge
    # snapshot timestamp when the row did not carry one.
    if not row.get("timestamp"):
        row["timestamp"] = snapshot_ts
    symbol = str(row.get("symbol", "?"))
    display = build_signal_display(signal_lookup.get(symbol, {}), row)
    observations = live_observation_values(row)
    fvg = display["fvg"]
    
    # Liquidation feed state for this symbol
    liq_feed_state = row.get("liq_feed_state", "UNAVAILABLE")
    
    rows.append({
        "Symbol": symbol,
        "Price": fmt(display_value(row, "price"), "$"),
        "24h": fmt(display_value(row, "change_24h"), "%"),
        "Volume 24h": fmt(display_value(row, "volume_24h"), "$"),
        "OI": fmt(display_value(row, "open_interest"), "$"),
        "OI Bias": evidence(display["oi"]),
        "OI Δ%": fmt(display_value(row, "oi_change_pct"), "%"),
        "Funding": fmt(display_value(row, "funding"), "%"),
        "Fund Bias": evidence(display["funding"]),
        "Net Delta": fmt(display_value(row, "net_delta"), "$"),
        "B/S Ratio": fmt(display_value(row, "buy_sell_ratio")),
        "B/S": evidence(display["b_s_ratio"]),
        "CVD 5m": fmt(observations["cvd_5m"]),
        "CVD Bias": evidence(display["cvd"]),
        "Flow Strength": fmt(observations["flow_strength"]),
        "Flow Bias": evidence(display["flow"]),
        "Ex Flow": fmt(observations["exchange_flow"], "$"),
        "Vol Bias": evidence(display["volume"]),
        "Imbalance": fmt(observations["imbalance"]),
        "Imb Bias": evidence(display["imbalance"]),
        # Liquidation Zone Price Levels (observed from forceOrder clusters)
        "Liq Zone ↓": fmt(display_value(row, "liq_long_zone_price"), "$"),
        "Liq Zone ↑": fmt(display_value(row, "liq_short_zone_price"), "$"),
        # Liquidation Volumes & Events
        "Liq Long Vol": fmt(display_value(row, "long_liq_vol"), "$"),
        "Liq Short Vol": fmt(display_value(row, "short_liq_vol"), "$"),
        "Liq Events": display_value(row, "liq_total_events"),
        # Liquidation Risk & Feed State
        "Liq Risk": _format_liq_risk(display, liq_feed_state),
        "Liq Feed": liq_feed_state,
        "Sweep": evidence(display["sweep"]),
        "Sweep Price": fmt(display["sweep_price"], "$"),
        "FVG": f"{fvg['state']} | {fvg['quality']}",
        "FVG Price": fmt(fvg["value"], "$"),
        "Regime": evidence(display["regime"]),
        "Reg Conf": fmt(display["regime_conf"], "%"),
        "Signal": display["signal"],
        "Signal Authority": display["authority"],
        "Signal Reason": display["signal_reason"],
    })

if not rows:
    st.warning("UNAVAILABLE — no market-data snapshot is available from the Python engine bridge.")
else:
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=700)

st.markdown("### Parameter semantics")
semantics = [
    ("Price", "BUY = positive price direction; SELL = negative; NEUTRAL = flat."),
    ("Volume 24h", "Market-size/selection context; never a standalone BUY/SELL trigger."),
    ("OI", "Raw open interest level. OI Bias supplies the directional interpretation."),
    ("OI Bias", "BUY = bullish positioning interpretation; SELL = bearish positioning interpretation; UNAVAILABLE when OI is absent."),
    ("OI Δ%", "Change in open interest; use with price/OI regime rather than as a standalone trigger."),
    ("Funding", "Raw funding rate. Negative funding is long-supportive; positive funding is short-supportive; near zero is neutral."),
    ("Fund Bias", "Directional funding interpretation; contextual risk/positioning evidence, not a standalone trigger."),
    ("Net Delta", "Raw taker delta from the real trade tape; unavailable when the trade tape is absent."),
    ("B/S Ratio", "Raw taker buy/sell ratio; BUY > 1.02; SELL < 0.98; unavailable without trade tape."),
    ("CVD 5m", "Raw 5-minute cumulative volume delta in traded-quantity units, derived only from actual aggTrade observations; missing trade tape remains unavailable."),
    ("CVD Bias", "Directional interpretation of CVD; derived from observed trade data and never used alone to create a trade signal."),
    ("Flow Strength", "Raw flow-strength metric from the order-flow engine; unavailable when the trade tape is absent."),
    ("Flow Bias", "Directional interpretation of order flow; contextual evidence only."),
    ("Ex Flow", "Raw exchange-flow observation; unavailable when the underlying flow observation is absent."),
    ("Vol Bias", "Directional volume interpretation; it is not a substitute for order-flow evidence."),
    ("Imbalance", "Raw L2 order-book imbalance; requires real depth evidence."),
    ("Imb Bias", "BUY > +0.05; SELL < -0.05; otherwise NEUTRAL; derived from the observed L2 imbalance."),
    ("Liq Zone ↓", "Nearest observed long-liquidation cluster price (from Binance forceOrder). UNAVAILABLE if no clusters."),
    ("Liq Zone ↑", "Nearest observed short-liquidation cluster price (from Binance forceOrder). UNAVAILABLE if no clusters."),
    ("Liq Long Vol", "Observed long liquidation volume (USD) from forceOrder events."),
    ("Liq Short Vol", "Observed short liquidation volume (USD) from forceOrder events."),
    ("Liq Events", "Total observed liquidation event count for this symbol."),
    ("Liq Risk", "Risk level: LOW/MEDIUM/HIGH when OBSERVED; NO_DATA when feed is live but no events; UNAVAILABLE when feed is down."),
    ("Liq Feed", "Per-symbol feed state: OBSERVED / NO_OBSERVED_LIQUIDATION / UNAVAILABLE."),
    ("Sweep", "BUY/SELL only when a qualifying sweep event exists; otherwise NOT_APPLICABLE."),
    ("Sweep Price", "Event location only; never an independent BUY/SELL generator."),
    ("FVG", "BUY/SELL contextual evidence from actual detected FVG boundaries; NOT_APPLICABLE when no FVG exists."),
    ("FVG Price", "Midpoint of the detected FVG gap; contextual location only."),
    ("Regime", "BUY = bullish regime; SELL = bearish regime; RANGE = NEUTRAL; unavailable when regime snapshot is absent."),
    ("Reg Conf", "Confidence in regime classification, not a standalone trading signal."),
    ("Signal", "Canonical Python engine only: BUY / SELL / NO_SIGNAL. No implied signals are permitted."),
    ("Signal Authority", "python-bridge only when the signal carries canonical Python provenance."),
]
st.dataframe(pd.DataFrame(semantics, columns=["Parameter", "Definition"]), width="stretch", hide_index=True)

st.info(
    "Authenticity rule: LIVE/CALCULATED/STALE/UNAVAILABLE/NOT_APPLICABLE are distinct states. "
    "A missing input is never turned into zero, neutral evidence, or an implied trade."
)
