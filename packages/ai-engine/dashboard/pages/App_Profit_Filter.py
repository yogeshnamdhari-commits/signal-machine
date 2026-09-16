"""
🛡️ App Profit Filter — Diagnostic Admission Layer
Shows ALL vs APP-ADMITTED statistics, the REJECTED BY APP breakdown, and the
live decision log produced by app_layer/profit_filter.py.

READ-ONLY by design: EMA V5 and Smart Money are never modified here.
This panel only inspects the admission decisions the App layer records.

Phase A = DIAGNOSTIC (decisions logged, nothing blocked).
Phase B = set PROFIT_FILTER_BLOCKING=true to reject non-EXECUTE decisions.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="App Profit Filter", page_icon="🛡️", layout="wide")

# ── Path Setup ───────────────────────────────────────────────────
_ai_root = Path(__file__).resolve().parent.parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from config.settings import config, DATA_DIR  # noqa: E402
from database.signal_repository import SignalRepository  # noqa: E402
from app_layer.profit_filter import (  # noqa: E402
    evaluate, load_live_sheet_rows, WEIGHTS, SCORE_VERSION,
)

_REPO_PATH = DATA_DIR / "institutional_v1.db"
repo = SignalRepository(str(_REPO_PATH))

st.title("🛡️ App Profit Filter — Admission Layer")

_cfg = config.profit_filter

# ── Decision mode banner ─────────────────────────────────────────
# Phase A = diagnostic: the App records what it WOULD do, but never
# changes actual execution. "REJECT" below means "would reject if
# blocking were enabled" — it is NOT a real rejection.
if _cfg.blocking:
    st.error(
        "🔴 DECISION MODE: BLOCKING\n\n"
        "**BLOCKING: ON** — non-EXECUTE decisions are actually rejected. "
        "Trading is controlled by the App Profit Filter.\n\n"
        "WOULD EXECUTE / WOULD REJECT are real gates right now."
    )
else:
    st.warning(
        "🔵 DECISION MODE: DIAGNOSTIC\n\n"
        "**BLOCKING: OFF** — this panel only records what the App layer WOULD do. "
        "No signal is blocked by the App Profit Filter.\n\n"
        "WOULD EXECUTE: YES/NO (hypothetical) · WOULD REJECT: YES/NO (hypothetical) · "
        "**ACTUAL EXECUTION: UNCHANGED** — controlled by the existing engine gates "
        "(EMA V5 → Smart Money → TIME_HALT → execution).\n\n"
        "The words REJECT / ACCEPT below are **hypothetical** diagnostic labels."
    )

st.caption(f"min_execute_score={_cfg.min_execute_score} watch_min={_cfg.watch_min_score} | "
           f"enabled={_cfg.enabled} | PROFIT_FILTER_BLOCKING="
           f"{'true' if _cfg.blocking else 'false'}")

# ── Load data ────────────────────────────────────────────────────
@st.cache_data(ttl=5, show_spinner=False)
def _load_stats() -> dict:
    import asyncio
    return asyncio.run(repo.profit_filter_stats(since_ts=0))


@st.cache_data(ttl=5, show_spinner=False)
def _load_decisions(limit: int = 300) -> list:
    import asyncio
    return asyncio.run(repo.get_profit_filter_decisions(limit=limit))


@st.cache_data(ttl=15, show_spinner=False)
def _load_cohorts() -> dict:
    import asyncio

    async def _go():
        await repo.backfill_profit_filter_outcomes()
        return await repo.profit_filter_cohort_stats()

    return asyncio.run(_go())


try:
    stats = _load_stats()
    decisions = _load_decisions()
except Exception as e:
    if "no such table" in str(e).lower():
        st.info("The App Profit Filter decision table does not exist yet — it is created automatically "
                "on the next engine start (`db.initialize()`). Decisions will appear here after that.")
        st.stop()
    st.error(f"Could not read decision log: {e}")
    st.stop()

# ── ALL vs APP-ADMITTED ──────────────────────────────────────────
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Candidates (reached filter)", stats["candidates"])
c2.metric("APP-ADMITTED (ACCEPT)", stats["admitted"])
c3.metric("WATCH (B-grade)", stats["watch"])
c4.metric("REJECTED", stats["rejected"])
c5.metric("NO APP DECISION (data-quality)", stats["no_decision"])
adm_pct = (100.0 * stats["admitted"] / stats["candidates"]) if stats["candidates"] else 0.0
c6.metric("Admission rate", f"{adm_pct:.1f}%")

st.divider()
st.subheader("REJECTED BY APP — reason breakdown")
df_reasons = pd.DataFrame(
    [{"decision": k.split(":", 1)[0], "reason": k.split(":", 1)[1] if ":" in k else k,
      "count": v} for k, v in stats["by_reason"].items()]
).sort_values("count", ascending=False)
if df_reasons.empty:
    st.info("No decisions recorded yet — decisions appear as soon as the engine evaluates a signal against the LIVE SHEET.")
else:
    st.bar_chart(df_reasons.set_index("reason")["count"])

c1, c2 = st.columns(2)
with c1:
    st.subheader("Decision mix")
    if stats["by_decision"]:
        st.bar_chart(pd.Series(stats["by_decision"]).rename("count"))
    else:
        st.caption("No data yet.")
with c2:
    st.subheader("Grade distribution")
    if stats["by_grade"]:
        st.bar_chart(pd.Series(stats["by_grade"]).rename("count"))
    else:
        st.caption("No data yet.")

st.divider()

# ── LIVE APP PERFORMANCE (ALL vs ACCEPTED vs REJECTED vs WATCH) ──
st.subheader("📈 LIVE APP PERFORMANCE — cohort outcomes (P/L & R linked from real trades)")
try:
    cohorts = _load_cohorts()
except Exception as e:
    st.warning(f"Cohort outcome metrics unavailable: {e}")
    cohorts = {}
if cohorts:
    rows_out = []
    for cohort, perf in cohorts.items():
        rows_out.append({
            "Cohort": cohort,
            "Trades": perf["n"],
            "Closed": perf["closed"],
            "Open": perf["open"],
            "Wins": perf["wins"],
            "Losses": perf["losses"],
            "Win rate": f"{perf['win_rate']:.0f}%",
            "Avg R": round(perf["avg_r"], 2),
            "Net P/L": round(perf["net_pnl"], 2),
        })
    st.dataframe(pd.DataFrame(rows_out).set_index("Cohort"),
                 use_container_width=True, height=180)
    _a, _r, _w = cohorts.get("ACCEPTED", {}), cohorts.get("REJECTED", {}), cohorts.get("WATCH", {})
    c_a, c_r, c_w = st.columns(3)
    if _a.get("n"):
        c_a.metric("ACCEPTED net P/L", f"${_a['net_pnl']:,.0f}",
                   f"{_a['win_rate']:.0f}% W · {_a['avg_r']:+.2f} R ({_a['n']} trades)")
    if _r.get("n"):
        c_r.metric("REJECTED net P/L (would have missed)", f"${_r['net_pnl']:,.0f}",
                   f"{_r['win_rate']:.0f}% W · {_r['avg_r']:+.2f} R ({_r['n']} trades)")
    if _w.get("n"):
        c_w.metric("WATCH net P/L (would have missed)", f"${_w['net_pnl']:,.0f}",
                   f"{_w['win_rate']:.0f}% W · {_w['avg_r']:+.2f} R ({_w['n']} trades)")
    st.caption("Outcomes are backfilled from positions_archive by signal_id. "
               "REJECTED/WATCH P&L is the counterfactual money the filter WOULD have "
               "protected/missed — diagnostics only, nothing is blocked.")
else:
    st.info("No closed outcomes to link yet — they appear after real trades close.")

st.divider()

# ── Recent decision log ──────────────────────────────────────────
st.subheader("Recent App Profit Filter decisions (HYPOTHETICAL — diagnostic)")
if decisions:
    df = pd.DataFrame(decisions)
    df["ts"] = pd.to_datetime(df["timestamp"], unit="s")
    df["quality_score"] = df["quality_score"].round(1)
    df["signal_confidence"] = df["signal_confidence"].round(2)
    if not _cfg.blocking:
        df["WOULD EXECUTE"] = df["decision"].map(
            lambda d: "YES" if d == "ACCEPT" else "NO"
        )
        df["WOULD REJECT"] = df["decision"].map(
            lambda d: "YES" if d.startswith("REJECT") else "NO"
        )
        df["ACTUAL EXECUTION"] = "UNCHANGED"
    st.dataframe(
        df[["ts", "symbol", "side", "decision", "grade", "quality_score", "reason",
            "regime_class", "strategy_version"] + (
            ["WOULD EXECUTE", "WOULD REJECT", "ACTUAL EXECUTION"] if not _cfg.blocking else []
        )],
        height=420, use_container_width=True,
    )
    if not _cfg.blocking:
        st.caption(
            "Every decision above is a **hypothetical** label. REJECT = the App layer WOULD reject "
            "if PROFIT_FILTER_BLOCKING=true. Nothing here blocked real trading."
        )
else:
    st.info("No decisions recorded yet.")

st.divider()

# ── Live simulation: evaluate a symbol right now ─────────────────
st.subheader("🧪 Simulate a signal against the LIVE SHEET")
rows_by_sym = load_live_sheet_rows()
if rows_by_sym:
    col1, col2, _ = st.columns([1, 1, 2])
    sym = col1.selectbox("Symbol", sorted(rows_by_sym.keys())[:80])
    side = col2.selectbox("Side", ["LONG", "SHORT"])
    if sym:
        d = evaluate(sym, side, rows_by_sym[sym])
        row = rows_by_sym[sym]
        st.write(f"**{sym} {side}** → **{d['decision']}** (grade {d['grade']}, score {d['score']}) "
                 f"— reason: `{d['reason'] or '—'}`")
        st.caption(
            f"LIVE: regime={row.get('regime')} @ {row.get('regime_confidence_pct')}% | "
            f"CVD={row.get('cvd_bias')} | ratio={row.get('buy_sell_ratio')} | "
            f"flow={row.get('flow_signal')} | vol={row.get('vol_bias')} | "
            f"OI={row.get('oi_bias')} | funding={row.get('funding')}% | "
            f"liq={row.get('liq_risk_level')} | sweep={row.get('sweep_direction') or 'none'} | "
            f"FVG={row.get('fvg_alignment')}"
        )
        bd = pd.DataFrame([
            {"component": k, "score": v["score"], "max": v["max"],
             "agreed": "✓" if v["agreed"] else "✗",
             "conflict": "⚠" if v["conflict"] else "", "detail": v["detail"]}
            for k, v in d["components"].items()
        ])
        st.dataframe(bd, use_container_width=True, height=260)

st.caption(f"⏱️ Page snapshot @ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} "
           f"| scoring v{SCORE_VERSION} | weights: {WEIGHTS}")
