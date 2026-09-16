"""Canonical root dashboard.

The dashboard is a read-only presentation layer. It never manufactures a BUY/SELL
signal from component votes. The executable Signal column is populated only from
the Python engine bridge and only from a fresh market-data snapshot.
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
from dashboard.live_sheet_contract import build_signal_display, freshness_state

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
    fvg = display["fvg"]
    liq_risk = str(row.get("liq_risk_level", row.get("liq_risk", "UNAVAILABLE")) or "UNAVAILABLE").upper()
    rows.append({
        "Symbol": symbol,
        "Price": fmt(row.get("price"), "$"),
        "24h": fmt(row.get("change_24h"), "%"),
        "Volume 24h": fmt(row.get("volume_24h"), "$"),
        "OI": fmt(row.get("open_interest"), "$"),
        "OI Bias": evidence(display["oi"]),
        "OI Δ%": fmt(row.get("oi_change_pct"), "%"),
        "Funding": fmt(row.get("funding"), "%"),
        "Fund Bias": evidence(display["funding"]),
        "Net Delta": fmt(row.get("net_delta"), "$"),
        "B/S Ratio": fmt(row.get("buy_sell_ratio")),
        "B/S": evidence(display["b_s_ratio"]),
        "CVD": evidence(display["cvd"]),
        "Flow": evidence(display["flow"]),
        "Ex Flow": evidence(display["exchange_flow"]),
        "Vol Bias": evidence(display["volume"]),
        "Imbalance": evidence(display["imbalance"]),
        "Liq Zone ↓": fmt(row.get("long_liq_vol"), "$"),
        "Liq Zone ↑": fmt(row.get("short_liq_vol"), "$"),
        "Liq Risk": liq_risk,
        "Sweep": evidence(display["sweep"]),
        "Sweep Price": fmt(display["sweep_price"], "$"),
        "FVG": f"{fvg['state']} | {fvg['quality']}",
        "FVG Price": fmt(fvg["value"], "$"),
        "Regime": evidence(display["regime"]),
        "Reg Conf": fmt(display["regime_conf"], "%"),
        "Signal": display["signal"],
        "Signal Authority": display["authority"],
    })

if not rows:
    st.warning("UNAVAILABLE — no market-data snapshot is available from the Python engine bridge.")
else:
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=700)

st.markdown("### What every field means")
semantics = [
    ("Price", "BUY = positive 24h direction; SELL = negative; NEUTRAL = flat."),
    ("OI", "Raw open interest level. Direction is supplied separately by OI Bias."),
    ("OI Bias", "BUY/SELL = engine OI positioning interpretation. Missing OI never becomes zero."),
    ("OI Δ%", "Open-interest change, interpreted together with the engine's OI state."),
    ("Funding", "Raw funding rate. It is a positioning/risk input, not a standalone signal."),
    ("Fund Bias", "BUY/SELL = engine interpretation of the funding/positioning state."),
    ("B/S Ratio", "BUY > 1.02; SELL < 0.98; otherwise NEUTRAL."),
    ("Delta / CVD", "BUY = positive taker pressure; SELL = negative taker pressure; unavailable stays unavailable."),
    ("Flow / Ex Flow", "Directional classification only from actual underlying flow observations."),
    ("Vol Bias", "Directional volume classification produced by the engine."),
    ("Imbalance", "BUY > +0.05; SELL < -0.05; otherwise NEUTRAL."),
    ("Liq Zone ↓ / ↑", "Long/short liquidation-volume context from the liquidation engine; not standalone votes."),
    ("Liq Risk", "Risk state only; never a standalone BUY/SELL generator."),
    ("Sweep", "BUY/SELL only when a qualifying sweep event is detected; otherwise NOT_APPLICABLE."),
    ("Sweep Price", "Event location only; it is not an independent trade vote."),
    ("FVG", "Actual detector state from FVG gap boundaries; contextual evidence."),
    ("FVG Price", "Midpoint of the detected FVG gap; contextual only."),
    ("Regime", "BUY = bullish regime; SELL = bearish regime; range/unknown = NEUTRAL."),
    ("Reg Conf", "Confidence in regime classification, not a standalone signal."),
    ("Signal", "Canonical Python engine only: BUY / SELL / NO_SIGNAL. No implied signals are permitted."),
]
st.dataframe(pd.DataFrame(semantics, columns=["Parameter", "Definition"]), width="stretch", hide_index=True)

st.info(
    "Authenticity rule: a missing, stale, or unavailable input is shown explicitly. "
    "The dashboard never converts OI + CVD + Flow + Imbalance into an executable signal."
)
