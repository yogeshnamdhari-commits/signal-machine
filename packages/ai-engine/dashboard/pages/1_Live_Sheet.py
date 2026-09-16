"""Production Live Sheet.

Raw market observations are displayed as LIVE/STALE/UNAVAILABLE. BUY/SELL/NEUTRAL
is descriptive evidence. The Signal column is populated only from the Python engine
bridge and only while the market-data snapshot is fresh.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit_autorefresh import st_autorefresh

_AI_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_AI_ROOT) not in sys.path:
    sys.path.insert(0, str(_AI_ROOT))

from dashboard.data_bridge import reader as bridge_reader
from dashboard.live_sheet_contract import build_signal_display, freshness_state

st.set_page_config(
    page_title="Live Sheet — Canonical Market Data",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st_autorefresh(interval=1000, key="canonical_live_sheet")


def fmt_number(value, suffix=""):
    if value is None or value == "":
        return "UNAVAILABLE"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return str(value)
    absolute = abs(value)
    if absolute >= 1e12:
        return f"{value / 1e12:.2f}T{suffix}"
    if absolute >= 1e9:
        return f"{value / 1e9:.2f}B{suffix}"
    if absolute >= 1e6:
        return f"{value / 1e6:.2f}M{suffix}"
    if absolute >= 1e3:
        return f"{value / 1e3:.2f}K{suffix}"
    return f"{value:.4f}{suffix}"


def evidence_cell(factor):
    return f"{factor.state.value} | {factor.quality.value}"


market_data = bridge_reader.read_market_data()
signals = bridge_reader.read_signals()
freshness = bridge_reader.read_market_data_freshness()
snapshot_ts = float(freshness.get("timestamp", 0) or 0)
snapshot_state = freshness_state(snapshot_ts, max_age=60.0)

st.title("📡 Canonical Live Sheet")
status = "🟢 LIVE" if snapshot_state == "LIVE" else "🟠 STALE" if snapshot_state == "STALE" else "🔴 UNAVAILABLE"
st.caption(
    f"Market snapshot: {status} · age {freshness.get('age', 0):.1f}s · "
    f"rows {freshness.get('rows', 0)} · refresh 1s · signal authority: Python"
)

# A stale snapshot can be useful for diagnostics, but must never display a current
# executable signal as though it were live.
signal_lookup = {str(s.get("symbol")): s for s in signals if s.get("symbol")} if snapshot_state == "LIVE" else {}
rows = []

for source_row in market_data:
    symbol = str(source_row.get("symbol", "?"))
    row = dict(source_row)
    row["timestamp"] = snapshot_ts
    signal = signal_lookup.get(symbol, {})
    display = build_signal_display(signal, row)

    fvg = display["fvg"]
    fvg_state = f"{fvg['state']} | {fvg['quality']}" if fvg["value"] is not None else "NOT_APPLICABLE"

    rows.append({
        "Symbol": symbol,
        "Price": fmt_number(row.get("price"), "$"),
        "24h": fmt_number(row.get("change_24h"), "%"),
        "Volume 24h": fmt_number(row.get("volume_24h"), "$"),
        "OI": fmt_number(row.get("open_interest"), "$"),
        "OI Bias": evidence_cell(display["oi"]),
        "OI Δ%": fmt_number(row.get("oi_change_pct"), "%"),
        "Funding": fmt_number(row.get("funding"), "%"),
        "Fund Bias": evidence_cell(display["funding"]),
        "Net Delta": fmt_number(row.get("net_delta"), "$"),
        "B/S Ratio": fmt_number(row.get("buy_sell_ratio")),
        "B/S": evidence_cell(display["b_s_ratio"]),
        "CVD": evidence_cell(display["cvd"]),
        "Flow": evidence_cell(display["flow"]),
        "Ex Flow": evidence_cell(display["exchange_flow"]),
        "Vol Bias": evidence_cell(display["volume"]),
        "Imbalance": evidence_cell(display["imbalance"]),
        "Liq Risk": str(display["liq_risk"]).upper(),
        "Sweep": evidence_cell(display["sweep"]),
        "Sweep Price": fmt_number(display["sweep_price"], "$"),
        "FVG": fvg_state,
        "FVG Price": fmt_number(fvg["value"], "$"),
        "Regime": evidence_cell(display["regime"]),
        "Reg Conf": fmt_number(display["regime_conf"], "%"),
        "Signal": display["signal"],
        "Signal Authority": display["authority"],
        "Signal Reason": display["signal_reason"],
    })

if not rows:
    st.warning("UNAVAILABLE — no market-data snapshot is available from the Python bridge.")
else:
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=720)

st.markdown("### Parameter dictionary")
semantics = [
    ("Price", "BUY = positive 24h direction; SELL = negative; NEUTRAL = flat."),
    ("OI", "Raw level; direction is supplied separately by OI Bias."),
    ("OI Bias", "BUY/SELL = engine OI positioning interpretation."),
    ("OI Δ%", "Change in open interest; interpreted with engine OI state."),
    ("Funding", "Raw funding rate; positioning/risk input, not a standalone vote."),
    ("Fund Bias", "BUY/SELL = engine interpretation of funding/positioning state."),
    ("B/S Ratio", "BUY > 1.02; SELL < 0.98; otherwise NEUTRAL."),
    ("Delta", "BUY = positive taker delta; SELL = negative; unavailable is not zero."),
    ("CVD", "BUY/SELL from actual trade-tape CVD state; unavailable stays unavailable."),
    ("Flow / Ex Flow", "Directional classification from actual underlying flow only."),
    ("Vol Bias", "Directional volume classification from the engine."),
    ("Imbalance", "BUY > +0.05; SELL < -0.05; otherwise NEUTRAL."),
    ("Liq Risk", "Risk state only; never a standalone BUY/SELL generator."),
    ("Sweep", "BUY/SELL only on a detected qualifying event; otherwise NOT_APPLICABLE."),
    ("Sweep Price", "Event location only; not an independent vote."),
    ("FVG", "Actual detector state from FVG gap boundaries; contextual evidence."),
    ("FVG Price", "Midpoint of the detected FVG gap; contextual only."),
    ("Regime", "BUY = bullish; SELL = bearish; range/unknown = NEUTRAL."),
    ("Reg Conf", "Confidence in regime classification, not a standalone signal."),
    ("Signal", "Canonical Python engine only: BUY / SELL / NO_SIGNAL."),
]
st.dataframe(pd.DataFrame(semantics, columns=["Parameter", "Definition"]), width="stretch", hide_index=True)

st.info(
    "Authenticity rules: no implied LONG/SHORT signals; no synthetic flow; no zero-filling of missing directional data. "
    "STALE/UNAVAILABLE snapshots cannot present a current executable signal."
)
