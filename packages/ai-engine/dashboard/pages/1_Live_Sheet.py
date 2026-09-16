"""Production Live Sheet.

Rules:
- Raw market observations are displayed as LIVE/STALE/UNAVAILABLE.
- BUY/SELL/NEUTRAL is descriptive evidence, not a trade signal.
- The Signal column comes only from the Python engine bridge.
- No dashboard voting, inferred LONG/SHORT, or fabricated zero values.
"""
from __future__ import annotations

import sys
import time
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
snapshot_state = freshness_state(freshness.get("timestamp", 0), max_age=60.0)

st.title("📡 Canonical Live Sheet")
status = "🟢 LIVE" if snapshot_state == "LIVE" else "🟠 STALE" if snapshot_state == "STALE" else "🔴 UNAVAILABLE"
st.caption(
    f"Market snapshot: {status} · age {freshness.get('age', 0):.1f}s · "
    f"rows {freshness.get('rows', 0)} · refresh 1s"
)

signal_lookup = {str(s.get("symbol")): s for s in signals if s.get("symbol")}
rows = []

for row in market_data:
    symbol = str(row.get("symbol", "?"))
    signal = signal_lookup.get(symbol, {})
    display = build_signal_display(signal, row)

    canonical_signal = display["signal"]
    signal_reason = display["signal_reason"]

    regime = display["regime"]
    regime_conf = row.get("regime_confidence_pct")
    liq_risk = str(row.get("liq_risk_level", "UNAVAILABLE") or "UNAVAILABLE").upper()

    fvg = display["fvg"]
    fvg_state = f"{fvg['state']} | {fvg['quality']}" if fvg["value"] is not None else "NOT_APPLICABLE"

    rows.append({
        "Symbol": symbol,
        "Price": fmt_number(row.get("price"), "$"),
        "24h": fmt_number(row.get("change_24h"), "%"),
        "OI": fmt_number(row.get("open_interest"), "$"),
        "OI Bias": evidence_cell(display["oi"]),
        "OI Δ%": fmt_number(row.get("oi_change_pct"), "%"),
        "Funding": fmt_number(row.get("funding"), "%"),
        "Funding Bias": evidence_cell(display["funding"]),
        "B/S Ratio": fmt_number(row.get("buy_sell_ratio")),
        "B/S": evidence_cell(display["b_s_ratio"]),
        "Net Delta": fmt_number(row.get("net_delta"), "$"),
        "Delta": evidence_cell(display["delta"]),
        "CVD": evidence_cell(display["cvd"]),
        "Flow": evidence_cell(display["flow"]),
        "Ex Flow": evidence_cell(display["exchange_flow"]),
        "Volume": evidence_cell(display["volume"]),
        "Imbalance": evidence_cell(display["imbalance"]),
        "Sweep": evidence_cell(display["sweep"]),
        "Sweep Price": fmt_number(display["sweep_price"], "$"),
        "FVG": fvg_state,
        "FVG Price": fmt_number(fvg["value"], "$"),
        "Regime": evidence_cell(regime),
        "Reg Conf": fmt_number(regime_conf, "%"),
        "Liq Risk": liq_risk,
        "Signal": canonical_signal,
        "Signal Authority": display["authority"],
        "Signal Reason": signal_reason,
    })

if not rows:
    st.warning("UNAVAILABLE — no fresh market-data snapshot is available from the Python bridge.")
else:
    df = pd.DataFrame(rows)
    st.dataframe(df, width="stretch", hide_index=True, height=min(720, len(df) * 30 + 60))

st.markdown("### Parameter dictionary")
st.caption("BUY/SELL/NEUTRAL describes the parameter; it never creates the canonical Signal column.")

parameter_rows = [
    ("Price", "BUY if positive 24h change; SELL if negative; otherwise NEUTRAL", "Directional"),
    ("OI", "BUY/SELL from engine OI bias; unavailable means no vote", "Directional"),
    ("Funding", "BUY/SELL from engine funding bias; interpreted as positioning evidence", "Directional / risk"),
    ("B/S Ratio", "BUY > 1.02; SELL < 0.98; otherwise NEUTRAL", "Directional"),
    ("Delta", "BUY if positive taker delta; SELL if negative", "Directional"),
    ("CVD", "BUY/SELL from CVD bias; unavailable is not zero", "Directional"),
    ("Flow", "BUY/SELL from actual trade-flow classification only", "Directional"),
    ("Exchange Flow", "BUY/SELL from exchange-flow classifier; missing remains unavailable", "Directional"),
    ("Volume", "BUY/SELL from volume-bias classifier", "Directional"),
    ("Imbalance", "BUY > +0.05; SELL < -0.05; otherwise NEUTRAL", "Directional"),
    ("Sweep", "BUY/SELL only when a qualifying sweep is actually detected", "Event"),
    ("FVG", "Contextual bullish/bearish imbalance from canonical engine evidence", "Context"),
    ("Regime", "BUY for bullish regime; SELL for bearish; Range/other = NEUTRAL", "Directional"),
    ("Reg Conf", "Confidence in regime classification; not a standalone vote", "Context"),
    ("Liq Risk", "Risk state only; never a standalone BUY/SELL generator", "Risk"),
    ("Signal", "Canonical Python engine only: BUY / SELL / NO_SIGNAL", "Executable authority"),
]
st.dataframe(pd.DataFrame(parameter_rows, columns=["Parameter", "BUY / SELL / NEUTRAL definition", "Role"]), width="stretch", hide_index=True)

st.markdown("### Authenticity rules")
st.info(
    "The dashboard never converts OI + CVD + Flow + Imbalance into a trade signal. "
    "When the Python engine has no canonical signal, the displayed value is NO_SIGNAL. "
    "A missing source value is shown as UNAVAILABLE/STALE/NOT_APPLICABLE rather than zero."
)
