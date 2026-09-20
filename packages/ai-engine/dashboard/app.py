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

market_data = bridge_reader.read_market_data()
signals = bridge_reader.read_signals()
fresh = bridge_reader.read_market_data_freshness()
snapshot_ts = float(fresh.get("timestamp", 0) or 0)
state = freshness_state(snapshot_ts, max_age=60)
signal_lookup = {str(s.get("symbol")): s for s in signals if s.get("symbol")} if state == "LIVE" else {}

st.title("📡 DeltaTerminal — Canonical Live Data")
st.caption(
    f"Snapshot: {state} · age {fresh.get('age', 0):.1f}s · symbols {fresh.get('rows', 0)} · "
    "Signal authority: Python engine"
)

rows = []
for source_row in market_data:
    row = dict(source_row)
    row["timestamp"] = snapshot_ts
    symbol = str(row.get("symbol", "?"))
    display = build_signal_display(signal_lookup.get(symbol, {}), row)
    observations = live_observation_values(row)
    fvg = display["fvg"]
    rows.append({
        "Symbol": symbol,
        "Price": fmt(display_value(row, "price"), "$"),
        "24h": fmt(display_value(row, "change_24h"), "%"),
        "Volume 24h": fmt(display_value(row, "volume_24h"), "$"),
        "OI": fmt(display_value(row, "open_interest"), "$"),
        "OI Bias": evidence(display["oi"]),
        "OI Δ% (5m)": fmt(display_value(row, "oi_change_pct"), "%"),
        "Funding": fmt(display_value(row, "funding"), "%"),
        "Fund Bias": evidence(display["funding"]),
        "Net Delta": fmt(display_value(row, "net_delta"), "$"),
        "B/S Ratio": fmt(display_value(row, "buy_sell_ratio")),
        "B/S": evidence(display["b_s_ratio"]),
        "CVD 5m": fmt(observations["cvd_5m"]),
        "CVD Bias": evidence(display["cvd"]),
        "Flow Strength": fmt(observations["flow_strength"]),
        "Flow Bias": evidence(display["flow"]),
        "Taker Net Flow": fmt(observations["exchange_flow"], "$"),
        "Book Bias": evidence(display["volume"]),
        "Imbalance": fmt(observations["imbalance"]),
        "Imb Bias": evidence(display["imbalance"]),
        "Observed Liq Cluster ↓": fmt(display_value(row, "observed_liq_cluster_down_price"), "$"),
        "Observed Liq Cluster ↑": fmt(display_value(row, "observed_liq_cluster_up_price"), "$"),
        "Long Liq Vol": fmt(display_value(row, "long_liq_vol"), "$"),
        "Short Liq Vol": fmt(display_value(row, "short_liq_vol"), "$"),
        "Liq Risk": str(display["liq_risk"] or "UNAVAILABLE").upper(),
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
    ("OI Δ% (5m)", "Authentic 5-minute change in Binance open interest from Open Interest Statistics; unavailable if the history endpoint is unavailable."),
    ("Funding", "Raw funding rate. Negative funding is long-supportive; positive funding is short-supportive; near zero is neutral."),
    ("Fund Bias", "Directional funding interpretation; contextual risk/positioning evidence, not a standalone trigger."),
    ("Net Delta", "Raw taker delta from the real trade tape; unavailable when the trade tape is absent."),
    ("B/S Ratio", "Raw taker buy/sell ratio; BUY > 1.02; SELL < 0.98; unavailable without trade tape."),
    ("CVD 5m", "Raw 5-minute cumulative volume delta in base-asset quantity, derived from actual Binance aggTrade observations; missing trade tape remains unavailable."),
    ("CVD Bias", "Directional interpretation of CVD; derived from observed trade data and never used alone to create a trade signal."),
    ("Flow Strength", "Raw flow-strength metric from the order-flow engine; unavailable when the trade tape is absent."),
    ("Flow Bias", "Directional interpretation of order flow; contextual evidence only."),
    ("Ex Flow", "Raw exchange-flow observation; unavailable when the underlying flow observation is absent."),
    ("Vol Bias", "Directional volume interpretation; it is not a substitute for order-flow evidence."),
    ("Imbalance", "Raw L2 order-book imbalance; requires real depth evidence."),
    ("Imb Bias", "BUY > +0.05; SELL < -0.05; otherwise NEUTRAL; derived from the observed L2 imbalance."),
    ("Liq Zone ↓ / ↑", "Long/short liquidation-volume context; location/risk evidence, not standalone votes."),
    ("Liq Risk", "Risk classification only; UNAVAILABLE when no liquidation-cluster evidence exists."),
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
