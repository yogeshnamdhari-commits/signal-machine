"""
📊 Production Analytics — Completed Trade Performance Dashboard
Read-only. Queries positions_archive. No modifications to trading logic.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import streamlit as st
import pandas as pd
import numpy as np
import altair as alt

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "institutional_v1.db"

st.set_page_config(page_title="📊 Production Analytics", layout="wide")
st.title("📊 Production Analytics")

# Data source label
st.markdown(
    '<div style="background: #1a1a2e; border: 1px solid #333; border-radius: 6px; padding: 8px 12px; margin-bottom: 12px; font-size: 0.8rem; color: #8b8b9e;">'
    '📦 <b>Data Source:</b> <code>positions_archive</code> table in <code>institutional_v1.db</code> | '
    '⚠️ Default view shows <b>ALL strategies</b> — use sidebar filter to select EMA V5 only</div>',
    unsafe_allow_html=True,
)


def query(sql: str, params=()) -> pd.DataFrame:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


# ── Load data ──
df = query("SELECT * FROM positions_archive")
df["opened_at_dt"] = pd.to_datetime(df["opened_at"], unit="s", utc=True)
df["closed_at_dt"] = pd.to_datetime(df["closed_at"], unit="s", utc=True)
df["date"] = df["opened_at_dt"].dt.date

# Normalize strategy versions
STRATEGY_MAP = {
    "legacy": "Legacy",
    "inst_v1": "Inst V1",
    "inst_v2": "Inst V2",
    "production_v2": "Production V2",
    "current": "Current",
    "ema_v5": "EMA V5",
}
df["strategy_label"] = df["strategy_version"].map(STRATEGY_MAP).fillna(df["strategy_version"].fillna("Unknown"))
# Fix: strategy_version column should be used for mapping, not strategy_label
if "strategy_version" not in df.columns:
    df["strategy_version"] = df.get("strategy_label", "Unknown")

# Normalize exit reasons
df["exit_simple"] = df["exit_reason"].apply(
    lambda x: "trailing_stop" if str(x).startswith("trailing_stop") else
              "mfe_trailing_stop" if str(x).startswith("mfe_trailing") else str(x)
)

# ── Sidebar Filters ──
st.sidebar.header("🔍 Filters")

# Strategy filter
all_strategies = ["All"] + sorted(df["strategy_label"].unique().tolist())
selected_strategy = st.sidebar.selectbox("Strategy", all_strategies, index=0)

# Date range filter
min_date = df["date"].min()
max_date = df["date"].max()
date_range = st.sidebar.date_input("Date Range", value=(min_date, max_date), min_value=min_date, max_value=max_date)

# Side filter
all_sides = ["All", "LONG", "SHORT"]
selected_side = st.sidebar.selectbox("Side", all_sides, index=0)

# Apply filters
mask = pd.Series(True, index=df.index)
if selected_strategy != "All":
    mask &= df["strategy_label"] == selected_strategy
if len(date_range) == 2:
    mask &= df["date"].between(date_range[0], date_range[1])
if selected_side != "All":
    mask &= df["side"] == selected_side

fdf = df[mask].copy()

if len(fdf) == 0:
    st.warning("No trades match the selected filters.")
    st.stop()

# ── Compute metrics ──
total = len(fdf)
wins = (fdf["pnl"] > 0).sum()
losses = (fdf["pnl"] < 0).sum()
breakeven = total - wins - losses
win_rate = wins / total * 100 if total else 0
total_pnl = fdf["pnl"].sum()
gross_profit = fdf.loc[fdf["pnl"] > 0, "pnl"].sum()
gross_loss = fdf.loc[fdf["pnl"] < 0, "pnl"].sum()
avg_pnl = fdf["pnl"].mean()
avg_win = fdf.loc[fdf["pnl"] > 0, "pnl"].mean() if wins else 0
avg_loss = fdf.loc[fdf["pnl"] < 0, "pnl"].mean() if losses else 0
largest_win = fdf["pnl"].max()
largest_loss = fdf["pnl"].min()
avg_hold = fdf.loc[fdf["hold_minutes"] > 0, "hold_minutes"].mean()
profit_factor = abs(gross_profit) / abs(gross_loss) if gross_loss else 0
expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)
reward_risk = abs(avg_win / avg_loss) if avg_loss else 0
kelly = (win_rate / 100 - (1 - win_rate / 100) / reward_risk) * 100 if reward_risk else 0

# Drawdown
eq = fdf.sort_values("closed_at")["pnl"].cumsum()
peak = eq.cummax()
drawdown = eq - peak
max_drawdown = drawdown.min()
recovery_factor = abs(total_pnl / max_drawdown) if max_drawdown else 0

# Sharpe / Sortino (annualized, assuming ~250 trading days)
daily_pnl = fdf.groupby("date")["pnl"].sum()
if len(daily_pnl) > 1:
    sharpe = (daily_pnl.mean() / daily_pnl.std()) * np.sqrt(250) if daily_pnl.std() > 0 else 0
    downside = daily_pnl[daily_pnl < 0]
    sortino = (daily_pnl.mean() / downside.std()) * np.sqrt(250) if len(downside) > 1 and downside.std() > 0 else 0
    mar = abs(daily_pnl.mean() * 250 / max_drawdown) if max_drawdown else 0
else:
    sharpe = sortino = mar = 0

# ── Filter header ──
filter_label = f"{selected_strategy}" if selected_strategy != "All" else "All Strategies"
date_label = f"{date_range[0]} to {date_range[1]}" if len(date_range) == 2 else "All dates"
side_label = selected_side if selected_side != "All" else "Both Sides"
st.subheader(f"📊 {filter_label} | {date_label} | {side_label}")

# ── KPI Row 1: Core ──
st.markdown("---")
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Total Trades", f"{total:,}")
c2.metric("Win Rate", f"{win_rate:.1f}%")
c3.metric("Profit Factor", f"{profit_factor:.2f}")
c4.metric("Expectancy", f"${expectancy:.2f}")
c5.metric("Total PnL", f"${total_pnl:,.2f}")
c6.metric("Avg Hold", f"{avg_hold:.0f} min" if avg_hold else "N/A")

# ── KPI Row 2: Advanced ──
c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
c1.metric("Gross Profit", f"${gross_profit:,.2f}")
c2.metric("Gross Loss", f"${gross_loss:,.2f}")
c3.metric("Largest Win", f"${largest_win:,.2f}")
c4.metric("Largest Loss", f"${largest_loss:,.2f}")
c5.metric("Avg Winner", f"${avg_win:.2f}")
c6.metric("Avg Loser", f"${avg_loss:.2f}")
c7.metric("R:R Ratio", f"{reward_risk:.2f}")

# ── KPI Row 3: Risk ──
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Max Drawdown", f"${max_drawdown:,.2f}")
c2.metric("Recovery Factor", f"{recovery_factor:.2f}")
c3.metric("Sharpe Ratio", f"{sharpe:.2f}")
c4.metric("Sortino Ratio", f"{sortino:.2f}")
c5.metric("Kelly %", f"{kelly:.1f}%")

st.markdown("---")

# ── Equity Curve ──
st.subheader("📈 Equity Curve")
eq_df = fdf.sort_values("closed_at")[["closed_at_dt", "pnl"]].copy()
eq_df["cum_pnl"] = eq_df["pnl"].cumsum()
st.line_chart(eq_df.set_index("closed_at_dt")["cum_pnl"], use_container_width=True)

# ── PnL Distribution (Altair histogram) ──
st.subheader("📊 PnL Distribution")
chart = alt.Chart(fdf).mark_bar(opacity=0.7).encode(
    alt.X("pnl:Q", bin=alt.Bin(maxbins=50), title="PnL (USDT)"),
    alt.Y("count()", title="Trades"),
    alt.Color("strategy_label:N", title="Strategy"),
).properties(width=800, height=300)
st.altair_chart(chart, use_container_width=True)

# ── TP/SL Hit Rates ──
st.subheader("🎯 Entry/Exit Analysis")
col1, col2, col3, col4 = st.columns(4)

tp1_trades = fdf[fdf["exit_simple"] == "take_profit_1"]
tp2_trades = fdf[fdf["exit_simple"] == "take_profit_2"]
tp3_trades = fdf[fdf["exit_simple"] == "take_profit_3"]
sl_trades = fdf[fdf["exit_simple"] == "stop_loss"]

col1.metric("TP1 Hit %", f"{len(tp1_trades)/total*100:.1f}%" if total else "N/A", f"{len(tp1_trades)} trades")
col2.metric("TP2 Hit %", f"{len(tp2_trades)/total*100:.1f}%" if total else "N/A", f"{len(tp2_trades)} trades")
col3.metric("TP3 Hit %", f"{len(tp3_trades)/total*100:.1f}%" if total else "N/A", f"{len(tp3_trades)} trades")
col4.metric("SL Hit %", f"{len(sl_trades)/total*100:.1f}%" if total else "N/A", f"{len(sl_trades)} trades")

# ── By Strategy ──
st.subheader("🎯 Performance by Strategy")
strat = fdf.groupby("strategy_label").agg(
    trades=("pnl", "count"),
    total_pnl=("pnl", "sum"),
    avg_pnl=("pnl", "mean"),
    win_rate=("pnl", lambda x: (x > 0).sum() / len(x) * 100),
    profit_factor=("pnl", lambda x: abs(x[x > 0].sum()) / abs(x[x < 0].sum()) if (x < 0).sum() else 0),
    avg_hold=("hold_minutes", lambda x: x[x > 0].mean()),
).sort_values("trades", ascending=False)
strat.columns = ["Trades", "Total PnL", "Avg PnL", "Win Rate %", "Profit Factor", "Avg Hold (min)"]
st.dataframe(strat.style.format({
    "Total PnL": "${:,.2f}",
    "Avg PnL": "${:.2f}",
    "Win Rate %": "{:.1f}%",
    "Profit Factor": "{:.2f}",
    "Avg Hold (min)": "{:.0f}",
}))

# ── By Exit Reason ──
st.subheader("🚪 Performance by Exit Reason")
exit_stats = fdf.groupby("exit_simple").agg(
    trades=("pnl", "count"),
    total_pnl=("pnl", "sum"),
    avg_pnl=("pnl", "mean"),
    win_rate=("pnl", lambda x: (x > 0).sum() / len(x) * 100),
).sort_values("trades", ascending=False)
exit_stats.columns = ["Trades", "Total PnL", "Avg PnL", "Win Rate %"]
st.dataframe(exit_stats.style.format({
    "Total PnL": "${:,.2f}",
    "Avg PnL": "${:.2f}",
    "Win Rate %": "{:.1f}%",
}))

# ── By Side ──
st.subheader("⬆️⬇️ Performance by Side")
side = fdf.groupby("side").agg(
    trades=("pnl", "count"),
    total_pnl=("pnl", "sum"),
    avg_pnl=("pnl", "mean"),
    win_rate=("pnl", lambda x: (x > 0).sum() / len(x) * 100),
)
side.columns = ["Trades", "Total PnL", "Avg PnL", "Win Rate %"]
st.dataframe(side.style.format({
    "Total PnL": "${:,.2f}",
    "Avg PnL": "${:.2f}",
    "Win Rate %": "{:.1f}%",
}))

# ── Confidence Bucket Performance ──
st.subheader("🎯 Performance by Confidence Bucket")
fdf["conf_bucket"] = pd.cut(fdf["confidence"] * 100, bins=[0, 70, 75, 80, 85, 90, 95, 100], labels=["<70", "70-75", "75-80", "80-85", "85-90", "90-95", "95-100"])
conf = fdf.groupby("conf_bucket", observed=True).agg(
    trades=("pnl", "count"),
    total_pnl=("pnl", "sum"),
    avg_pnl=("pnl", "mean"),
    win_rate=("pnl", lambda x: (x > 0).sum() / len(x) * 100),
).dropna(subset=["trades"])
conf.columns = ["Trades", "Total PnL", "Avg PnL", "Win Rate %"]
st.dataframe(conf.style.format({
    "Total PnL": "${:,.2f}",
    "Avg PnL": "${:.2f}",
    "Win Rate %": "{:.1f}%",
}))

# ── By Session ──
if "session" in fdf.columns:
    st.subheader("🕐 Performance by Session")
    sess = fdf.groupby("session").agg(
        trades=("pnl", "count"),
        total_pnl=("pnl", "sum"),
        avg_pnl=("pnl", "mean"),
        win_rate=("pnl", lambda x: (x > 0).sum() / len(x) * 100),
    ).dropna(subset=["trades"]).sort_values("trades", ascending=False)
    sess.columns = ["Trades", "Total PnL", "Avg PnL", "Win Rate %"]
    st.dataframe(sess.style.format({
        "Total PnL": "${:,.2f}",
        "Avg PnL": "${:.2f}",
        "Win Rate %": "{:.1f}%",
    }))

# ── Top/Bottom Symbols ──
st.subheader("🏆 Top & Bottom Symbols")
sym = fdf.groupby("symbol").agg(
    trades=("pnl", "count"),
    total_pnl=("pnl", "sum"),
    avg_pnl=("pnl", "mean"),
    win_rate=("pnl", lambda x: (x > 0).sum() / len(x) * 100),
).sort_values("total_pnl", ascending=False)
sym.columns = ["Trades", "Total PnL", "Avg PnL", "Win Rate %"]

col1, col2 = st.columns(2)
with col1:
    st.markdown("**Top 10 Profitable**")
    st.dataframe(sym.head(10).style.format({
        "Total PnL": "${:,.2f}",
        "Avg PnL": "${:.2f}",
        "Win Rate %": "{:.1f}%",
    }))
with col2:
    st.markdown("**Top 10 Losing**")
    st.dataframe(sym.tail(10).style.format({
        "Total PnL": "${:,.2f}",
        "Avg PnL": "${:.2f}",
        "Win Rate %": "{:.1f}%",
    }))

# ── Daily PnL ──
st.subheader("📅 Daily PnL")
daily = fdf.groupby("date").agg(
    trades=("pnl", "count"),
    total_pnl=("pnl", "sum"),
    win_rate=("pnl", lambda x: (x > 0).sum() / len(x) * 100),
)
daily.columns = ["Trades", "Total PnL", "Win Rate %"]
st.bar_chart(daily["Total PnL"])

# ── R-Multiple Distribution ──
if "realized_r" in fdf.columns:
    st.subheader("📐 R-Multiple Distribution")
    r_data = fdf["realized_r"].dropna()
    if len(r_data) > 0:
        r_chart = alt.Chart(fdf.dropna(subset=["realized_r"])).mark_bar(opacity=0.7).encode(
            alt.X("realized_r:Q", bin=alt.Bin(maxbins=50), title="Realized R"),
            alt.Y("count()", title="Trades"),
        ).properties(width=800, height=250)
        st.altair_chart(r_chart, use_container_width=True)
        rc1, rc2, rc3 = st.columns(3)
        rc1.metric("Avg R", f"{r_data.mean():.2f}")
        rc2.metric("Median R", f"{r_data.median():.2f}")
        rc3.metric("R > 0", f"{(r_data > 0).sum()} ({(r_data > 0).sum()/len(r_data)*100:.1f}%)")

# ── Profit Factor Deep Dive ──
st.markdown("---")
st.subheader("🔍 Profit Factor Deep Dive")

def pf_table(group_col: str, label: str) -> pd.DataFrame:
    """Compute profit factor and other metrics grouped by a column."""
    def _pf(x):
        gp = x[x > 0].sum()
        gl = abs(x[x < 0].sum())
        return gp / gl if gl else 0
    def _rr(x):
        aw = x[x > 0].mean() if (x > 0).any() else 0
        al = abs(x[x < 0].mean()) if (x < 0).any() else 1
        return aw / al if al else 0
    g = fdf.groupby(group_col).agg(
        trades=("pnl", "count"),
        total_pnl=("pnl", "sum"),
        profit_factor=("pnl", _pf),
        win_rate=("pnl", lambda x: (x > 0).sum() / len(x) * 100),
        avg_win=("pnl", lambda x: x[x > 0].mean() if (x > 0).any() else 0),
        avg_loss=("pnl", lambda x: x[x < 0].mean() if (x < 0).any() else 0),
        reward_risk=("pnl", _rr),
        expectancy=("pnl", lambda x: (x > 0).sum() / len(x) * x[x > 0].mean() + (x < 0).sum() / len(x) * x[x < 0].mean() if len(x) else 0),
    ).sort_values("trades", ascending=False)
    return g

# PF by Confidence
st.markdown("**📊 Profit Factor by Confidence Bucket**")
pf_conf = pf_table("conf_bucket", "Confidence")
pf_conf.columns = ["Trades", "Total PnL", "PF", "Win Rate %", "Avg Win", "Avg Loss", "R:R", "Expectancy"]
st.dataframe(pf_conf.style.format({
    "Total PnL": "${:,.2f}", "PF": "{:.2f}", "Win Rate %": "{:.1f}%",
    "Avg Win": "${:.2f}", "Avg Loss": "${:.2f}", "R:R": "{:.2f}", "Expectancy": "${:.2f}",
}))

# PF by Side
st.markdown("**⬆️⬇️ Profit Factor by Side**")
pf_side = pf_table("side", "Side")
pf_side.columns = ["Trades", "Total PnL", "PF", "Win Rate %", "Avg Win", "Avg Loss", "R:R", "Expectancy"]
st.dataframe(pf_side.style.format({
    "Total PnL": "${:,.2f}", "PF": "{:.2f}", "Win Rate %": "{:.1f}%",
    "Avg Win": "${:.2f}", "Avg Loss": "${:.2f}", "R:R": "{:.2f}", "Expectancy": "${:.2f}",
}))

# PF by Session
st.markdown("**🕐 Profit Factor by Session**")
pf_sess = pf_table("session", "Session")
pf_sess.columns = ["Trades", "Total PnL", "PF", "Win Rate %", "Avg Win", "Avg Loss", "R:R", "Expectancy"]
st.dataframe(pf_sess.style.format({
    "Total PnL": "${:,.2f}", "PF": "{:.2f}", "Win Rate %": "{:.1f}%",
    "Avg Win": "${:.2f}", "Avg Loss": "${:.2f}", "R:R": "{:.2f}", "Expectancy": "${:.2f}",
}))

# PF by Symbol (top 20)
st.markdown("**🏆 Profit Factor by Symbol (top 20 by trade count)**")
pf_sym = pf_table("symbol", "Symbol")
pf_sym.columns = ["Trades", "Total PnL", "PF", "Win Rate %", "Avg Win", "Avg Loss", "R:R", "Expectancy"]
pf_sym = pf_sym.head(20)
st.dataframe(pf_sym.style.format({
    "Total PnL": "${:,.2f}", "PF": "{:.2f}", "Win Rate %": "{:.1f}%",
    "Avg Win": "${:.2f}", "Avg Loss": "${:.2f}", "R:R": "{:.2f}", "Expectancy": "${:.2f}",
}))

# ── Weekday & Hour Performance ──
st.markdown("---")
st.subheader("📅 Performance by Weekday & Hour")

if "opened_at_dt" in fdf.columns:
    fdf["weekday"] = fdf["opened_at_dt"].dt.day_name()
    fdf["hour"] = fdf["opened_at_dt"].dt.hour

    weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    wd = fdf.groupby("weekday", observed=True).agg(
        trades=("pnl", "count"),
        total_pnl=("pnl", "sum"),
        profit_factor=("pnl", lambda x: abs(x[x > 0].sum()) / abs(x[x < 0].sum()) if (x < 0).sum() else 0),
        win_rate=("pnl", lambda x: (x > 0).sum() / len(x) * 100),
    ).reindex(weekday_order).dropna(subset=["trades"])
    wd.columns = ["Trades", "Total PnL", "PF", "Win Rate %"]
    st.markdown("**📊 By Weekday**")
    st.dataframe(wd.style.format({"Total PnL": "${:,.2f}", "PF": "{:.2f}", "Win Rate %": "{:.1f}%"}))

    hr = fdf.groupby("hour").agg(
        trades=("pnl", "count"),
        total_pnl=("pnl", "sum"),
        profit_factor=("pnl", lambda x: abs(x[x > 0].sum()) / abs(x[x < 0].sum()) if (x < 0).sum() else 0),
        win_rate=("pnl", lambda x: (x > 0).sum() / len(x) * 100),
    )
    hr.columns = ["Trades", "Total PnL", "PF", "Win Rate %"]
    st.markdown("**📊 By Hour (UTC)**")
    st.dataframe(hr.style.format({"Total PnL": "${:,.2f}", "PF": "{:.2f}", "Win Rate %": "{:.1f}%"}))

# ── Exit Type Analysis ──
st.markdown("---")
st.subheader("🚪 Exit Type Deep Dive")

exit_deep = fdf.groupby("exit_simple").agg(
    trades=("pnl", "count"),
    total_pnl=("pnl", "sum"),
    profit_factor=("pnl", lambda x: abs(x[x > 0].sum()) / abs(x[x < 0].sum()) if (x < 0).sum() else 0),
    win_rate=("pnl", lambda x: (x > 0).sum() / len(x) * 100),
    avg_pnl=("pnl", "mean"),
    avg_hold=("hold_minutes", lambda x: x[x > 0].mean()),
).sort_values("trades", ascending=False)
exit_deep.columns = ["Trades", "Total PnL", "PF", "Win Rate %", "Avg PnL", "Avg Hold (min)"]
st.dataframe(exit_deep.style.format({
    "Total PnL": "${:,.2f}", "PF": "{:.2f}", "Win Rate %": "{:.1f}%",
    "Avg PnL": "${:.2f}", "Avg Hold (min)": "{:.0f}",
}))

# ── MFE / MAE Analysis ──
if "mfe_pct" in fdf.columns and "mae_pct" in fdf.columns:
    st.markdown("---")
    st.subheader("📐 MFE / MAE Analysis")

    mfe_mae = fdf[["symbol", "side", "pnl", "mfe_pct", "mae_pct", "exit_simple"]].dropna()
    if len(mfe_mae) > 0:
        mfe_mae["mfe_pct"] = mfe_mae["mfe_pct"] * 100
        mfe_mae["mae_pct"] = mfe_mae["mae_pct"] * 100

        # Summary
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Avg MFE", f"{mfe_mae['mfe_pct'].mean():.2f}%")
        c2.metric("Avg MAE", f"{mfe_mae['mae_pct'].mean():.2f}%")
        c3.metric("MFE/MAE Ratio", f"{abs(mfe_mae['mfe_pct'].mean() / mfe_mae['mae_pct'].mean()):.2f}" if mfe_mae['mae_pct'].mean() != 0 else "N/A")
        winners = mfe_mae[mfe_mae["pnl"] > 0]
        losers = mfe_mae[mfe_mae["pnl"] < 0]
        c4.metric("Winner Avg MFE", f"{winners['mfe_pct'].mean():.2f}%" if len(winners) else "N/A")

        # MFE/MAE by exit type
        st.markdown("**MFE/MAE by Exit Type**")
        mfe_exit = mfe_mae.groupby("exit_simple").agg(
            trades=("pnl", "count"),
            avg_mfe=("mfe_pct", "mean"),
            avg_mae=("mae_pct", "mean"),
            avg_pnl=("pnl", "mean"),
        ).sort_values("trades", ascending=False)
        mfe_exit.columns = ["Trades", "Avg MFE %", "Avg MAE %", "Avg PnL"]
        st.dataframe(mfe_exit.style.format({
            "Avg MFE %": "{:.2f}%", "Avg MAE %": "{:.2f}%", "Avg PnL": "${:.2f}",
        }))

        # MFE histogram for winners
        st.markdown("**MFE Distribution (Winners vs Losers)**")
        mfe_chart = alt.Chart(mfe_mae).mark_bar(opacity=0.6).encode(
            alt.X("mfe_pct:Q", bin=alt.Bin(maxbins=30), title="MFE %"),
            alt.Y("count()", title="Trades"),
            alt.Color("pnl:Q", scale=alt.Scale(scheme="redyellowgreen", domain=[mfe_mae["pnl"].min(), 0, mfe_mae["pnl"].max()]), title="PnL"),
        ).properties(width=800, height=250)
        st.altair_chart(mfe_chart, use_container_width=True)

        # MAE histogram
        mae_chart = alt.Chart(mfe_mae).mark_bar(opacity=0.6).encode(
            alt.X("mae_pct:Q", bin=alt.Bin(maxbins=30), title="MAE % (adverse)"),
            alt.Y("count()", title="Trades"),
            alt.Color("pnl:Q", scale=alt.Scale(scheme="redyellowgreen", domain=[mfe_mae["pnl"].min(), 0, mfe_mae["pnl"].max()]), title="PnL"),
        ).properties(width=800, height=250)
        st.altair_chart(mae_chart, use_container_width=True)

st.markdown("---")
st.caption(
    f"📦 Data source: <code>positions_archive</code> in <code>institutional_v1.db</code> | "
    f"Showing: {filter_label} | {total:,} trades | "
    f"⚠️ Select 'EMA V5' in sidebar to see EMA V5-only performance",
    unsafe_allow_html=True,
)
