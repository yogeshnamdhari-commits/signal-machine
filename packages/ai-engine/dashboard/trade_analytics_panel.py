"""
Trade Analytics Panel — comprehensive trade analysis with versioned analytics,
hold time optimizer, exit optimizer, confidence accuracy, symbol expectancy,
session analytics, live performance, and auto recommendations.
Uses TradeAnalyticsOrchestrator as primary data engine.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from plotly.subplots import make_subplots

from dashboard.data_bridge import reader as bridge_reader
from scanner.trade_analytics_engine import (
    TradeAnalyticsOrchestrator,
    load_trades,
    versioned_analytics,
    hold_time_optimizer,
    exit_optimizer,
    confidence_accuracy,
    symbol_expectancy,
    session_analytics,
    live_engine_performance,
    auto_recommendations,
    _compute_group_stats,
)

# DB path (same as engine uses)
_AI_ROOT = Path(__file__).resolve().parent.parent          # packages/ai-engine/
_DB_PATH = _AI_ROOT / "data" / "institutional_v1.db"


def render_trade_analytics():
    """Render the trade analytics panel with all 8 upgrade components."""
    st.subheader("📈 Trade Analytics — Institutional Grade")

    # ── Sidebar date range filter ──
    with st.sidebar:
        st.markdown("---")
        st.markdown("### 📅 Trade History Filter")
        date_range = st.selectbox(
            "Show trades from last",
            options=[7, 14, 30, 60, 90, "All time"],
            index=2,
            key="trade_date_range",
        )
        if date_range == "All time":
            since_days = None
        else:
            since_days = int(date_range)

    # ── Load data from new analytics engine ──
    trades_raw = load_trades(since_days=since_days)
    source_label = "database"

    # ── Fallback: Bridge JSON ──
    if not trades_raw:
        trade_history = bridge_reader.read_trade_history()
        if trade_history:
            trades_raw = _build_trades_from_bridge(trade_history)
            source_label = "bridge"

    # ── Fallback: Backtest trades ──
    if not trades_raw:
        bt_path = _AI_ROOT / "data" / "bridge" / "backtest_trades.json"
        if bt_path.exists():
            try:
                import json as _json
                with open(bt_path) as f:
                    bt_data = _json.load(f)
                bt_trades = bt_data.get("trades", [])
                if bt_trades:
                    trades_raw = _build_trades_from_bridge(bt_trades)
                    source_label = "backtest"
            except Exception:
                pass

    # Load supplementary bridge data
    signals = bridge_reader.read_signals()
    equity_history = bridge_reader.read_equity_history()
    metrics = bridge_reader.read_metrics()

    if not trades_raw:
        _render_signal_based_analytics(signals, metrics, equity_history)
        return

    # Detect if these are TradeRecord objects or dicts
    is_dataclass = hasattr(trades_raw[0], '__dataclass_fields__')
    if is_dataclass:
        trades = trades_raw
        df = pd.DataFrame([vars(t) for t in trades])
        # Compute derived columns for time analysis and trade history
        if "opened_at" in df.columns:
            df["hour"] = df["opened_at"].apply(
                lambda x: datetime.fromtimestamp(x, tz=timezone.utc).hour if x else 0
            )
            df["day_of_week"] = df["opened_at"].apply(
                lambda x: datetime.fromtimestamp(x, tz=timezone.utc).strftime("%A") if x else "Unknown"
            )
        if "hold_minutes" in df.columns:
            df["hold_str"] = df["hold_minutes"].apply(_fmt_hold_time)
        if "pnl" in df.columns and "entry_price" in df.columns and "stop_loss" in df.columns:
            def _calc_r(row):
                risk = abs(row.get("entry_price", 0) - row.get("stop_loss", 0))
                if risk and risk > 0 and row.get("pnl"):
                    return round(row["pnl"] / (risk * row.get("quantity", 1)), 2) if row.get("quantity", 1) else 0
                return 0
            df["r_multiple"] = df.apply(_calc_r, axis=1)
    else:
        trades = trades_raw
        df = pd.DataFrame(trades)

    st.caption(
        f"📊 {len(df)} trades from {source_label} · "
        f"Last updated {datetime.now().strftime('%H:%M:%S')}"
    )

    # ── Summary metrics ──
    _render_summary_metrics(df, metrics if source_label == "bridge" else None)

    # ── Main tabs ──
    tabs = st.tabs([
        "📊 PnL Distribution",
        "⏰ Time Analysis",
        "🏷️ By Symbol",
        "📋 Trade History",
        "📈 Equity Curve",
        "🏆 Version Analytics",
        "⏱️ Hold Time & Exits",
        "🎯 Confidence & Sessions",
        "📊 Live Performance",
    ])

    with tabs[0]:
        _render_pnl_distribution(df)

    with tabs[1]:
        _render_time_analysis(df)

    with tabs[2]:
        _render_symbol_analysis(df)

    with tabs[3]:
        _render_trade_history(df)

    with tabs[4]:
        _render_equity_curve(equity_history, metrics)

    with tabs[5]:
        _render_version_analytics(trades, is_dataclass)

    with tabs[6]:
        _render_hold_time_and_exits(trades, is_dataclass)

    with tabs[7]:
        _render_confidence_and_sessions(trades, is_dataclass)

    with tabs[8]:
        _render_live_performance(trades, is_dataclass)


# ══════════════════════════════════════════════════════════════
# NEW TAB 6: VERSION ANALYTICS
# ══════════════════════════════════════════════════════════════
def _fmt_hold_time(minutes: float) -> str:
    """Format hold minutes into human-readable string."""
    if minutes < 60:
        return f"{int(minutes)}m"
    elif minutes < 1440:
        h = int(minutes / 60)
        m = int(minutes % 60)
        return f"{h}h {m}m"
    else:
        d = int(minutes / 1440)
        h = int((minutes % 1440) / 60)
        return f"{d}d {h}h"


def _render_version_analytics(trades, is_dc: bool = True) -> None:
    """Render versioned strategy performance comparison."""
    st.markdown("### 🏆 Strategy Version Performance")
    st.caption("Compare performance across engine versions — see which version is actually profitable")

    if not is_dc:
        st.info("Version analytics require database trades (not bridge JSON).")
        return

    ver_stats = versioned_analytics(trades)
    if not ver_stats:
        st.info("No versioned trade data available.")
        return

    # ── Summary cards ──
    cols = st.columns(len(ver_stats))
    for i, v in enumerate(ver_stats):
        pnl_color = "#00ff88" if v["total_pnl"] >= 0 else "#ff4444"
        wr_color = "#00ff88" if v["win_rate"] >= 50 else ("#f59e0b" if v["win_rate"] >= 40 else "#ff4444")
        pf_color = "#00ff88" if v["profit_factor"] >= 1.5 else ("#f59e0b" if v["profit_factor"] >= 1.0 else "#ff4444")

        with cols[i]:
            st.markdown(
                f"""<div style="background:linear-gradient(135deg,#0f1420,#0c1018);
                padding:16px 12px;border-radius:10px;border:1px solid rgba(255,255,255,0.06);
                text-align:center;">
                <div style="font-size:0.75rem;color:#8892a4;margin-bottom:6px;">{v['label']}</div>
                <div style="font-size:1.5rem;font-weight:800;color:{pnl_color};margin-bottom:4px;">${v['total_pnl']:+,.2f}</div>
                <div style="font-size:0.8rem;color:{wr_color};">WR: {v['win_rate']}%</div>
                <div style="font-size:0.8rem;color:{pf_color};">PF: {v['profit_factor']}</div>
                <div style="font-size:0.75rem;color:#8892a4;margin-top:4px;">{v['trades']} trades · Exp ${v['expectancy']:+.2f}</div>
                </div>""",
                unsafe_allow_html=True,
            )

    # ── Version comparison table ──
    st.markdown("#### 📊 Detailed Comparison")
    ver_df = pd.DataFrame(ver_stats)
    display_cols = ["label", "trades", "win_rate", "profit_factor", "total_pnl", "expectancy", "avg_win", "avg_loss", "sharpe", "max_dd_pct"]
    avail = [c for c in display_cols if c in ver_df.columns]
    rename = {"label": "Version", "trades": "Trades", "win_rate": "Win Rate %", "profit_factor": "PF",
              "total_pnl": "Total PnL", "expectancy": "Expectancy", "avg_win": "Avg Win",
              "avg_loss": "Avg Loss", "sharpe": "Sharpe", "max_dd_pct": "Max DD %"}
    st.dataframe(
        ver_df[avail].rename(columns=rename).style.background_gradient(
            subset=["Total PnL"], cmap="RdYlGn"
        ).background_gradient(
            subset=["Win Rate %"], cmap="RdYlGn"
        ),
        width="stretch", hide_index=True,
    )

    # ── Cumulative PnL by version ──
    st.markdown("#### 📈 Cumulative PnL by Version")
    fig = go.Figure()
    version_colors = {"legacy": "#6b7280", "inst_v1": "#f59e0b", "inst_v2": "#3b82f6", "current": "#22c55e"}

    for ver in ["legacy", "inst_v1", "inst_v2", "current"]:
        ver_trades = [t for t in trades if t.strategy_version == ver]
        if not ver_trades:
            continue
        pnls = np.cumsum([t.pnl for t in ver_trades])
        fig.add_trace(go.Scatter(
            y=pnls, mode="lines", name=ver.replace("_", " ").title(),
            line=dict(color=version_colors.get(ver, "#fff"), width=2),
        ))

    fig.add_hline(y=0, line_color="#666")
    fig.update_layout(
        title="📈 Cumulative PnL by Strategy Version",
        xaxis_title="Trade #", yaxis_title="Cumulative PnL ($)",
        height=350, template="plotly_dark",
        margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig, width="stretch")

    # ── Version improvement trend ──
    if len(ver_stats) >= 2:
        st.markdown("#### 📊 Version Trend")
        latest = ver_stats[-1]
        prev = ver_stats[-2]
        if latest["expectancy"] > prev["expectancy"]:
            st.success(f"📈 **Improving**: {latest['label']} expectancy ${latest['expectancy']:+.2f} vs ${prev['expectancy']:+.2f} (previous)")
        elif latest["expectancy"] < prev["expectancy"]:
            st.warning(f"⚠️ **Regression**: {latest['label']} expectancy ${latest['expectancy']:+.2f} vs ${prev['expectancy']:+.2f} (previous)")
        else:
            st.info(f"➡️ **Flat**: {latest['label']} expectancy ${latest['expectancy']:+.2f} (same as previous)")


# ══════════════════════════════════════════════════════════════
# NEW TAB 7: HOLD TIME & EXIT OPTIMIZER
# ══════════════════════════════════════════════════════════════
def _render_hold_time_and_exits(trades, is_dc: bool = True) -> None:
    """Render hold time optimizer and exit optimizer side by side."""
    st.markdown("### ⏱️ Hold Time & Exit Optimizer")
    st.caption("Find the optimal hold time and exit method for maximum profitability")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### ⏱️ Performance by Hold Time")
        hold_stats = hold_time_optimizer(trades)

        if hold_stats:
            hold_df = pd.DataFrame(hold_stats)
            display_df = hold_df[[c for c in ["zone", "trades", "win_rate", "profit_factor", "total_pnl", "expectancy"] if c in hold_df.columns]].copy()
            display_df["⭐"] = hold_df["is_best"].apply(lambda x: "⭐" if x else "")
            display_df = display_df.rename(columns={"zone": "Zone", "trades": "Trades", "win_rate": "WR %", "profit_factor": "PF",
                                                     "total_pnl": "Total PnL", "expectancy": "Expectancy"})

            st.dataframe(
                display_df.style.background_gradient(
                    subset=["Total PnL"], cmap="RdYlGn"
                ),
                width="stretch", hide_index=True,
            )

            best = max(hold_stats, key=lambda x: x["total_pnl"])
            if best["total_pnl"] > 0:
                st.success(f"🏆 **Best zone: {best['zone']}** — {best['win_rate']}% WR, ${best['total_pnl']:+,.2f} total PnL")

            fig = go.Figure(go.Bar(
                x=[h["zone"] for h in hold_stats],
                y=[h["total_pnl"] for h in hold_stats],
                marker_color=["#22c55e" if h["total_pnl"] > 0 else "#ef4444" for h in hold_stats],
                text=[f"${h['total_pnl']:+,.0f}" for h in hold_stats],
                textposition="outside",
            ))
            fig.add_hline(y=0, line_color="#666")
            fig.update_layout(
                title="💰 PnL by Hold Time Zone",
                xaxis_title="", yaxis_title="Total PnL ($)",
                height=300, template="plotly_dark",
                margin=dict(l=10, r=10, t=40, b=10),
            )
            st.plotly_chart(fig, width="stretch")

    with col2:
        st.markdown("#### 🚪 Performance by Exit Method")
        exit_stats = exit_optimizer(trades)

        if exit_stats:
            exit_df = pd.DataFrame(exit_stats)
            display_df = exit_df[[c for c in ["label", "trades", "win_rate", "total_pnl", "expectancy"] if c in exit_df.columns]].copy()
            display_df["⭐"] = exit_df["is_best"].apply(lambda x: "⭐" if x else "")
            display_df = display_df.rename(columns={"label": "Exit Method", "trades": "Trades", "win_rate": "WR %",
                                                     "total_pnl": "Total PnL", "expectancy": "Expectancy"})

            st.dataframe(
                display_df.style.background_gradient(
                    subset=["Total PnL"], cmap="RdYlGn"
                ),
                width="stretch", hide_index=True,
            )

            best = max(exit_stats, key=lambda x: x["expectancy"])
            if best["expectancy"] > 0:
                st.success(f"🏆 **Best exit: {best['label']}** — {best['win_rate']}% WR, ${best['expectancy']:+.2f} expectancy")

            fig = go.Figure(go.Bar(
                x=[e["label"] for e in exit_stats],
                y=[e["expectancy"] for e in exit_stats],
                marker_color=["#22c55e" if e["expectancy"] > 0 else "#ef4444" for e in exit_stats],
                text=[f"${e['expectancy']:+.2f}" for e in exit_stats],
                textposition="outside",
            ))
            fig.add_hline(y=0, line_color="#666")
            fig.update_layout(
                title="💰 Expectancy by Exit Method",
                xaxis_title="", yaxis_title="Expectancy ($)",
                height=300, template="plotly_dark",
                margin=dict(l=10, r=10, t=40, b=10),
            )
            st.plotly_chart(fig, width="stretch")

    # ── Combined recommendation ──
    if hold_stats and exit_stats:
        profitable_holds = [h for h in hold_stats if h.get("total_pnl", 0) > 0]
        best_hold = max(profitable_holds, key=lambda x: x["total_pnl"]) if profitable_holds else None
        best_exit = max(exit_stats, key=lambda x: x["expectancy"])

        if best_hold:
            st.info(
                f"💡 **Optimal Strategy**: Hold for **{best_hold['zone']}** "
                f"(+${best_hold['total_pnl']:+,.2f}) + Use **{best_exit['label']}** exit "
                f"(${best_exit['expectancy']:+.2f} expectancy)"
            )


# ══════════════════════════════════════════════════════════════
# NEW TAB 8: CONFIDENCE ACCURACY & SESSIONS
# ══════════════════════════════════════════════════════════════
def _render_confidence_and_sessions(trades, is_dc: bool = True) -> None:
    """Render confidence accuracy table and session analytics."""
    st.markdown("### 🎯 Confidence Accuracy & Session Analytics")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 🎯 Confidence Calibration")
        conf_stats = confidence_accuracy(trades)

        if conf_stats:
            conf_df = pd.DataFrame(conf_stats)
            rename = {"bucket": "Confidence", "trades": "Trades", "win_rate": "Actual WR %",
                      "avg_confidence": "Avg Conf %", "calibration_error": "Error %",
                      "calibration_note": "Status", "total_pnl": "PnL"}
            avail = [c for c in ["bucket", "trades", "win_rate", "avg_confidence", "calibration_error", "calibration_note", "total_pnl"]
                     if c in conf_df.columns]
            st.dataframe(
                conf_df[avail].rename(columns=rename).style.background_gradient(
                    subset=["PnL"] if "PnL" in conf_df.columns else [], cmap="RdYlGn"
                ),
                width="stretch", hide_index=True,
            )

            # Calibration chart
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=[c["bucket"] for c in conf_stats],
                y=[c["avg_confidence"] for c in conf_stats],
                name="Raw Confidence", marker_color="#3b82f6", opacity=0.7,
            ))
            fig.add_trace(go.Bar(
                x=[c["bucket"] for c in conf_stats],
                y=[c["win_rate"] for c in conf_stats],
                name="Actual Win Rate", marker_color="#22c55e", opacity=0.7,
            ))
            fig.update_layout(
                title="🎯 Confidence vs Reality",
                xaxis_title="Confidence Bucket", yaxis_title="Win Rate %",
                barmode="group", height=300, template="plotly_dark",
                margin=dict(l=10, r=10, t=40, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02),
            )
            st.plotly_chart(fig, width="stretch")

            overconf = [c for c in conf_stats if c.get("calibration_error", 0) < -10]
            if overconf:
                worst = min(overconf, key=lambda x: x["calibration_error"])
                st.warning(
                    f"⚠️ **Overconfidence detected**: {worst['bucket']} bucket claims {worst['avg_confidence']}% "
                    f"but actual WR is {worst['win_rate']}% (error: {worst['calibration_error']:+.1f}%)"
                )

    with col2:
        st.markdown("#### 🌏 Session Performance")
        sess_stats = session_analytics(trades)

        if sess_stats:
            sess_df = pd.DataFrame(sess_stats)
            display_df = sess_df[[c for c in ["label", "trades", "win_rate", "total_pnl", "expectancy", "profit_factor"] if c in sess_df.columns]].copy()
            display_df["⭐"] = sess_df["is_best"].apply(lambda x: "⭐" if x else "")
            display_df = display_df.rename(columns={"label": "Session", "trades": "Trades", "win_rate": "WR %",
                                                     "total_pnl": "Total PnL", "expectancy": "Expectancy", "profit_factor": "PF"})

            st.dataframe(
                display_df.style.background_gradient(
                    subset=["Total PnL"], cmap="RdYlGn"
                ),
                width="stretch", hide_index=True,
            )

            fig = go.Figure(go.Bar(
                x=[s["label"] for s in sess_stats],
                y=[s["total_pnl"] for s in sess_stats],
                marker_color=["#22c55e" if s["total_pnl"] > 0 else "#ef4444" for s in sess_stats],
                text=[f"${s['total_pnl']:+,.0f}" for s in sess_stats],
                textposition="outside",
            ))
            fig.add_hline(y=0, line_color="#666")
            fig.update_layout(
                title="💰 PnL by Trading Session",
                xaxis_title="", yaxis_title="Total PnL ($)",
                height=300, template="plotly_dark",
                margin=dict(l=10, r=10, t=40, b=10),
            )
            st.plotly_chart(fig, width="stretch")

            profitable = [s for s in sess_stats if s["total_pnl"] > 0]
            if profitable:
                best = max(profitable, key=lambda x: x["expectancy"])
                st.success(f"🏆 **Best session: {best['label']}** — {best['win_rate']}% WR, ${best['total_pnl']:+,.2f} PnL")
            else:
                worst = min(sess_stats, key=lambda x: x["total_pnl"])
                st.warning(f"⚠️ All sessions negative — avoid **{worst['label']}** (worst: ${worst['total_pnl']:+,.2f})")

    # ── Symbol Expectancy Table ──
    st.markdown("#### 🏷️ Symbol Expectancy Table")
    st.caption("Only showing symbols with positive expectancy or ≥3 trades")

    sym_stats = symbol_expectancy(trades)
    if sym_stats:
        sym_df = pd.DataFrame(sym_stats)
        rename = {"symbol": "Symbol", "trades": "Trades", "win_rate": "WR %", "total_pnl": "PnL",
                  "expectancy": "Expectancy", "profit_factor": "PF", "avg_r": "Avg R", "max_dd": "Max DD"}
        avail = [c for c in ["symbol", "trades", "win_rate", "total_pnl", "expectancy", "profit_factor", "avg_r", "max_dd"]
                 if c in sym_df.columns]

        pos_df = sym_df[sym_df.get("is_positive", pd.Series([True]*len(sym_df)))]
        display = pos_df if len(pos_df) > 0 else sym_df
        st.dataframe(
            display[avail].rename(columns=rename).style.background_gradient(
                subset=["PnL"], cmap="RdYlGn"
            ).background_gradient(
                subset=["WR %"], cmap="RdYlGn"
            ),
            width="stretch", hide_index=True,
        )

        top5 = [s for s in sym_stats if s["is_positive"] and s["trades"] >= 3][:5]
        if top5:
            st.success(
                f"🏆 **Top symbols**: {', '.join(s['symbol'] for s in top5)} — "
                f"combined ${sum(s['total_pnl'] for s in top5):+,.2f} PnL"
            )
    else:
        st.info("No symbol data available.")


# ══════════════════════════════════════════════════════════════
# NEW TAB 9: LIVE ENGINE PERFORMANCE & RECOMMENDATIONS
# ══════════════════════════════════════════════════════════════
def _render_live_performance(trades, is_dc: bool = True) -> None:
    """Render rolling live performance windows and auto-recommendations."""
    st.markdown("### 📊 Live Engine Performance & Recommendations")

    # ── Rolling Performance Windows ──
    st.markdown("#### 📊 Rolling Performance Windows")
    live_stats = live_engine_performance(trades)

    if live_stats:
        window_order = ["all", "last_100", "last_50", "last_20"]
        window_labels = {"all": "📈 All Trades", "last_100": "📊 Last 100", "last_50": "📊 Last 50", "last_20": "📊 Last 20"}

        valid_windows = [w for w in window_order if w in live_stats]
        cols = st.columns(len(valid_windows))
        for idx, w in enumerate(valid_windows):
            v = live_stats[w]
            pnl_color = "#00ff88" if v["total_pnl"] >= 0 else "#ff4444"
            wr_color = "#00ff88" if v["win_rate"] >= 50 else ("#f59e0b" if v["win_rate"] >= 40 else "#ff4444")
            pf_color = "#00ff88" if v["profit_factor"] >= 1.5 else ("#f59e0b" if v["profit_factor"] >= 1.0 else "#ff4444")

            with cols[idx]:
                st.markdown(
                    f"""<div style="background:linear-gradient(135deg,#0f1420,#0c1018);
                    padding:16px 12px;border-radius:10px;border:1px solid rgba(255,255,255,0.06);
                    text-align:center;">
                    <div style="font-size:0.75rem;color:#8892a4;margin-bottom:4px;">{window_labels.get(w, w)}</div>
                    <div style="font-size:1.3rem;font-weight:800;color:{pnl_color};">${v['total_pnl']:+,.2f}</div>
                    <div style="font-size:0.8rem;color:{wr_color};">WR: {v['win_rate']}%</div>
                    <div style="font-size:0.8rem;color:{pf_color};">PF: {v['profit_factor']}</div>
                    <div style="font-size:0.7rem;color:#8892a4;margin-top:4px;">Exp: ${v['expectancy']:+.2f}</div>
                    <div style="font-size:0.65rem;color:#555;margin-top:2px;">{v.get('from','')} → {v.get('to','')}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )

        # Trend analysis
        if "last_20" in live_stats and "all" in live_stats:
            recent = live_stats["last_20"]
            overall = live_stats["all"]

            st.markdown("#### 📊 Trend vs Overall")
            improvement = recent["expectancy"] - overall["expectancy"]
            if improvement > 0:
                st.success(f"📈 **Improving**: Last 20 trades expectancy ${recent['expectancy']:+.2f} is ${improvement:+.2f} better than overall ${overall['expectancy']:+.2f}")
            elif improvement < 0:
                st.warning(f"📉 **Declining**: Last 20 trades expectancy ${recent['expectancy']:+.2f} is ${improvement:+.2f} worse than overall ${overall['expectancy']:+.2f}")
            else:
                st.info(f"➡️ **Flat**: Last 20 trades expectancy ${recent['expectancy']:+.2f} (same as overall)")

    # ── Auto Recommendations ──
    st.markdown("#### 💡 Auto Recommendations")
    st.caption("Data-driven recommendations from your actual trade history")

    recs = auto_recommendations(trades)

    for rec in recs:
        priority = rec.get("priority", "")
        if "HIGH" in priority or "🔴" in priority:
            border_color = "#ef4444"
        elif "MEDIUM" in priority or "🟡" in priority:
            border_color = "#f59e0b"
        elif "🟢" in priority or "INFO" in priority:
            border_color = "#22c55e"
        else:
            border_color = "#3b82f6"

        st.markdown(
            f"""<div style="background:linear-gradient(135deg,#0f1420,#0c1018);
            padding:14px 18px;border-radius:8px;border-left:4px solid {border_color};
            margin-bottom:10px;">
            <div style="font-size:0.75rem;color:{border_color};font-weight:700;margin-bottom:4px;">
            {rec.get('priority', '')}</div>
            <div style="font-size:1.0rem;color:#e8ecf1;font-weight:600;margin-bottom:4px;">
            {rec.get('recommendation', '')}</div>
            <div style="font-size:0.85rem;color:#8892a4;">
            {rec.get('detail', '')}</div>
            <div style="font-size:0.8rem;color:#22c55e;margin-top:4px;">
            📈 {rec.get('impact', '')}</div>
            </div>""",
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════
# LEGACY FUNCTIONS (kept for backwards compatibility)
# ══════════════════════════════════════════════════════════════

def _build_trades_from_bridge(trade_history: List[Dict]) -> List[Dict]:
    """Build trades list from bridge JSON — used as fallback when DB is empty."""
    trades = []
    for t in trade_history:
        ts = t.get("timestamp", time.time())
        dt = datetime.fromtimestamp(ts)
        hold_seconds = t.get("holding_period", 0)
        hold_min = hold_seconds / 60 if hold_seconds else 0

        entry = t.get("entry_price", 0)
        exit_p = t.get("exit_price", 0)
        side = t.get("side", "LONG")
        pnl = t.get("pnl", 0)
        qty = t.get("quantity", 0)

        risk_dist = abs(entry * 0.009)
        r_mult = pnl / (risk_dist * qty) if risk_dist and qty else 0

        trades.append({
            "symbol": t.get("symbol", "UNKNOWN"),
            "side": side,
            "entry_price": entry,
            "exit_price": exit_p,
            "pnl": pnl,
            "fees": abs(pnl) * 0.0004,
            "hold_minutes": round(hold_min, 1),
            "hold_str": t.get("holding_period_str", "—"),
            "exit_reason": t.get("exit_reason", "unknown"),
            "hour": dt.hour,
            "day_of_week": dt.strftime("%A"),
            "r_multiple": round(r_mult, 2),
            "timestamp": ts,
            "partial": t.get("partial", False),
            "mae_pct": t.get("mae_pct", 0),
            "mfe_pct": t.get("mfe_pct", 0),
            "entry_quality": t.get("entry_quality", 0),
            "exit_quality": t.get("exit_quality", 0),
            "execution_score": t.get("execution_score", 0),
            "entry_slippage_bps": t.get("entry_slippage_bps", 0),
            "total_slippage_bps": t.get("total_slippage_bps", 0),
            "confidence": t.get("confidence", 0),
            "institutional_score": t.get("institutional_score", 0),
            "regime": t.get("regime", "—"),
            "trend_score": t.get("trend_score", 0),
            "risk_reward": t.get("risk_reward", 0),
        })
    trades.sort(key=lambda x: x.get("timestamp", 0))
    return trades


def _render_signal_based_analytics(signals: List[Dict], metrics: Dict, equity_history: List[Dict]) -> None:
    """Render analytics based on signals and metrics when no completed trades exist."""
    st.info("📊 No completed trades yet. Showing signal-based analytics from the scanner.")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("📡 Active Signals", len(signals))
    with col2:
        n_long = sum(1 for s in signals if s.get("type") == "LONG")
        n_short = len(signals) - n_long
        st.metric("🟢 LONG / 🔴 SHORT", f"{n_long} / {n_short}")
    with col3:
        avg_conf = np.mean([s.get("confidence", 0) for s in signals]) if signals else 0
        st.metric("🎯 Avg Confidence", f"{avg_conf:.1%}")
    with col4:
        avg_rr = np.mean([s.get("risk_adjusted", {}).get("risk_reward", 0) for s in signals]) if signals else 0
        st.metric("⚖️ Avg R:R", f"{avg_rr:.2f}")

    if signals:
        confidences = [s.get("confidence", 0) for s in signals]
        fig = go.Figure(go.Histogram(
            x=confidences, nbinsx=20, marker_color="#3b82f6", opacity=0.8,
        ))
        fig.add_vline(x=np.mean(confidences), line_dash="dot", line_color="#f59e0b",
                      annotation_text=f"Avg: {np.mean(confidences):.2f}")
        fig.update_layout(
            title="🎯 Signal Confidence Distribution",
            xaxis_title="Confidence", yaxis_title="Count",
            height=350, template="plotly_dark",
            margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig, width="stretch")

    if equity_history:
        _render_equity_curve(equity_history, metrics)


def _render_summary_metrics(df: pd.DataFrame, metrics: Dict = None) -> None:
    """Render summary metric cards."""
    total_trades = len(df)
    winners = df[df["pnl"] > 0]
    losers = df[df["pnl"] <= 0]

    win_rate = len(winners) / total_trades * 100 if total_trades > 0 else 0
    avg_win = winners["pnl"].mean() if len(winners) > 0 else 0
    avg_loss = losers["pnl"].mean() if len(losers) > 0 else 0
    profit_factor = abs(winners["pnl"].sum() / losers["pnl"].sum()) if len(losers) > 0 and losers["pnl"].sum() != 0 else 999.0
    total_pnl = df["pnl"].sum()
    expectancy = df["pnl"].mean()

    sharpe = 0
    sortino = 0
    if total_trades >= 2 and df["pnl"].std() > 0:
        sharpe = df["pnl"].mean() / df["pnl"].std() * np.sqrt(min(total_trades, 252))
        downside = df[df["pnl"] <= 0]["pnl"]
        if len(downside) > 1 and downside.std() > 0:
            sortino = df["pnl"].mean() / downside.std() * np.sqrt(min(total_trades, 252))
        else:
            sortino = sharpe

    max_dd_pct = 0
    if total_trades > 0:
        cum_pnl = df["pnl"].cumsum()
        running_max = cum_pnl.cummax()
        drawdowns = cum_pnl - running_max
        max_dd_abs = abs(drawdowns.min()) if len(drawdowns) > 0 else 0
        peak_equity = 10000 + running_max.max()
        max_dd_pct = (max_dd_abs / peak_equity * 100) if peak_equity > 0 else 0

    if metrics:
        total_pnl = metrics.get("total_pnl", total_pnl)
        if metrics.get("sharpe_ratio", 0) != 0:
            sharpe = metrics["sharpe_ratio"]
        if metrics.get("sortino_ratio", 0) != 0:
            sortino = metrics["sortino_ratio"]
        if metrics.get("max_drawdown", 0) > 0:
            max_dd_pct = metrics["max_drawdown"]
        if metrics.get("profit_factor", 0) > 0:
            profit_factor = metrics["profit_factor"]
        if metrics.get("expectancy", 0) != 0:
            expectancy = metrics["expectancy"]

    def _card(icon, label, value, color="#e8ecf1"):
        return (
            f'<div style="background:linear-gradient(135deg,#0f1420,#0c1018);padding:14px 10px;border-radius:10px;'
            f'border:1px solid rgba(255,255,255,0.06);text-align:center;min-height:70px;">'
            f'<div style="font-size:0.68rem;color:#8892a4;text-transform:uppercase;letter-spacing:0.06em;margin-bottom:4px;">{icon} {label}</div>'
            f'<div style="font-size:1.35rem;font-weight:800;color:{color};line-height:1.2;">{value}</div>'
            f'<div style="font-size:0.6rem;color:#00ff88;margin-top:3px;">✓ Verified</div>'
            f'</div>'
        )

    pnl_color = "#00ff88" if total_pnl >= 0 else "#ff4444"
    wr_color = "#00ff88" if win_rate >= 50 else "#ff4444"
    pf_color = "#00ff88" if profit_factor >= 1.5 else ("#f59e0b" if profit_factor >= 1.0 else "#ff4444")
    dd_color = "#ff4444" if max_dd_pct > 5 else ("#f59e0b" if max_dd_pct > 1 else "#00ff88")
    exp_color = "#00ff88" if expectancy > 0 else "#ff4444"

    row1 = st.columns(5)
    with row1[0]:
        st.markdown(_card("📊", "Total Trades", f"{total_trades}", "#3b82f6"), unsafe_allow_html=True)
    with row1[1]:
        st.markdown(_card("🎯", "Win Rate", f"{win_rate:.1f}%", wr_color), unsafe_allow_html=True)
    with row1[2]:
        st.markdown(_card("💰", "Total PnL", f"${total_pnl:+,.2f}", pnl_color), unsafe_allow_html=True)
    with row1[3]:
        pf_str = f"{profit_factor:.2f}" if profit_factor < 999 else "∞"
        st.markdown(_card("⚖️", "Profit Factor", pf_str, pf_color), unsafe_allow_html=True)
    with row1[4]:
        st.markdown(_card("📉", "Max Drawdown", f"{max_dd_pct:.2f}%", dd_color), unsafe_allow_html=True)

    aw_color = "#00ff88"
    al_color = "#ff4444"
    sh_color = "#3b82f6" if sharpe > 0 else "#ff4444"

    row2 = st.columns(5)
    with row2[0]:
        st.markdown(_card("📈", "Avg Win", f"${avg_win:+.2f}", aw_color), unsafe_allow_html=True)
    with row2[1]:
        st.markdown(_card("📉", "Avg Loss", f"${avg_loss:+.2f}", al_color), unsafe_allow_html=True)
    with row2[2]:
        st.markdown(_card("🎲", "Expectancy", f"${expectancy:+.2f}", exp_color), unsafe_allow_html=True)
    with row2[3]:
        st.markdown(_card("⚡", "Sharpe Ratio", f"{sharpe:.2f}", sh_color), unsafe_allow_html=True)
    with row2[4]:
        st.markdown(_card("🛡️", "Sortino Ratio", f"{sortino:.2f}", sh_color), unsafe_allow_html=True)


def _render_pnl_distribution(df: pd.DataFrame) -> None:
    """Render PnL distribution charts."""
    col1, col2 = st.columns(2)

    with col1:
        fig = go.Figure()
        fig.add_trace(go.Histogram(
            x=df["pnl"], nbinsx=30, name="PnL",
            marker_color=["#22c55e" if x > 0 else "#ef4444" for x in df["pnl"]],
            opacity=0.8,
        ))
        fig.add_vline(x=0, line_dash="dash", line_color="#fff")
        fig.add_vline(x=df["pnl"].mean(), line_dash="dot", line_color="#f59e0b",
                      annotation_text=f"Mean: ${df['pnl'].mean():,.0f}")
        fig.update_layout(
            title="📊 PnL Distribution", xaxis_title="PnL ($)", yaxis_title="Count",
            height=350, template="plotly_dark",
            margin=dict(l=10, r=10, t=40, b=10),
            title_font=dict(size=16),
            xaxis=dict(tickfont=dict(size=12), title_font=dict(size=13)),
            yaxis=dict(tickfont=dict(size=12), title_font=dict(size=13)),
            font=dict(size=13),
        )
        st.plotly_chart(fig, width="stretch")

    with col2:
        cum_pnl = df["pnl"].cumsum()
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            y=cum_pnl, mode="lines", name="Cumulative PnL",
            line=dict(color="#00ff88", width=2),
            fill="tozeroy", fillcolor="rgba(0,255,136,0.1)",
        ))
        fig.add_hline(y=0, line_color="#666")
        fig.update_layout(
            title="📈 Cumulative PnL", xaxis_title="Trade #", yaxis_title="PnL ($)",
            height=350, template="plotly_dark",
            margin=dict(l=10, r=10, t=40, b=10),
            title_font=dict(size=16),
            xaxis=dict(tickfont=dict(size=12), title_font=dict(size=13)),
            yaxis=dict(tickfont=dict(size=12), title_font=dict(size=13)),
        )
        st.plotly_chart(fig, width="stretch")

    col3, col4 = st.columns(2)

    with col3:
        wins = len(df[df["pnl"] > 0])
        losses = len(df[df["pnl"] <= 0])
        fig = go.Figure(go.Pie(
            labels=["Wins", "Losses"], values=[wins, losses],
            marker=dict(colors=["#22c55e", "#ef4444"]), hole=0.4, textinfo="label+percent",
        ))
        fig.update_layout(
            title="🎯 Win/Loss Ratio", height=300, template="plotly_dark",
            margin=dict(l=10, r=10, t=40, b=10), showlegend=False,
            title_font=dict(size=16), font=dict(size=14),
        )
        st.plotly_chart(fig, width="stretch")

    with col4:
        if "r_multiple" in df.columns:
            fig = go.Figure(go.Histogram(
                x=df["r_multiple"], nbinsx=20, marker_color="#a855f7", opacity=0.8,
            ))
            fig.add_vline(x=0, line_dash="dash", line_color="#fff")
            fig.add_vline(x=1, line_dash="dot", line_color="#22c55e", annotation_text="1R target")
            fig.update_layout(
                title="🎲 R-Multiple Distribution", xaxis_title="R-Multiple", yaxis_title="Count",
                height=300, template="plotly_dark",
                margin=dict(l=10, r=10, t=40, b=10),
                title_font=dict(size=16),
                xaxis=dict(tickfont=dict(size=12), title_font=dict(size=13)),
                yaxis=dict(tickfont=dict(size=12), title_font=dict(size=13)),
            )
            st.plotly_chart(fig, width="stretch")


def _render_time_analysis(df: pd.DataFrame) -> None:
    """Render time-based analysis."""
    col1, col2 = st.columns(2)

    with col1:
        hourly = df.groupby("hour")["pnl"].agg(["mean", "count"]).reset_index()
        hourly.columns = ["hour", "avg_pnl", "trades"]
        colors = ["#22c55e" if x > 0 else "#ef4444" for x in hourly["avg_pnl"]]

        fig = go.Figure(go.Bar(
            x=hourly["hour"], y=hourly["avg_pnl"], marker_color=colors,
            text=hourly["trades"], textposition="outside",
        ))
        fig.add_hline(y=0, line_color="#666")
        fig.update_layout(
            title="⏰ Avg PnL by Hour (UTC)", xaxis_title="Hour", yaxis_title="Avg PnL ($)",
            height=350, template="plotly_dark",
            margin=dict(l=10, r=10, t=40, b=10),
            title_font=dict(size=16),
            xaxis=dict(tickfont=dict(size=12), title_font=dict(size=13)),
            yaxis=dict(tickfont=dict(size=12), title_font=dict(size=13)),
        )
        st.plotly_chart(fig, width="stretch")

    with col2:
        dow_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        dow = df.groupby("day_of_week")["pnl"].agg(["mean", "sum", "count"]).reset_index()
        dow.columns = ["day", "avg_pnl", "total_pnl", "trades"]
        dow["day"] = pd.Categorical(dow["day"], categories=dow_order, ordered=True)
        dow = dow.sort_values("day")

        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Bar(
            x=dow["day"], y=dow["avg_pnl"], name="Avg PnL",
            marker_color=["#22c55e" if x > 0 else "#ef4444" for x in dow["avg_pnl"]],
        ))
        fig.add_trace(go.Scatter(
            x=dow["day"], y=dow["trades"], name="Trade Count",
            line=dict(color="#f59e0b", width=2), mode="lines+markers",
        ), secondary_y=True)
        fig.update_layout(
            title="📅 Performance by Day of Week", height=350, template="plotly_dark",
            margin=dict(l=10, r=10, t=40, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            title_font=dict(size=16), xaxis=dict(tickfont=dict(size=12)),
            yaxis=dict(tickfont=dict(size=12), title_font=dict(size=13)),
        )
        fig.update_yaxes(title_text="Avg PnL ($)", secondary_y=False)
        fig.update_yaxes(title_text="Trade Count", secondary_y=True)
        st.plotly_chart(fig, width="stretch")

    if "hold_minutes" in df.columns:
        fig = go.Figure(go.Histogram(
            x=df["hold_minutes"], nbinsx=20, marker_color="#3b82f6", opacity=0.8,
        ))
        fig.update_layout(
            title="⏱️ Hold Time Distribution", xaxis_title="Hold Time (minutes)",
            yaxis_title="Count", height=250, template="plotly_dark",
            margin=dict(l=10, r=10, t=40, b=10),
            title_font=dict(size=16),
            xaxis=dict(tickfont=dict(size=12), title_font=dict(size=13)),
            yaxis=dict(tickfont=dict(size=12), title_font=dict(size=13)),
        )
        st.plotly_chart(fig, width="stretch")


def _render_symbol_analysis(df: pd.DataFrame) -> None:
    """Render per-symbol analysis."""
    sym_stats = df.groupby("symbol").agg(
        trades=("pnl", "count"), total_pnl=("pnl", "sum"),
        avg_pnl=("pnl", "mean"), win_rate=("pnl", lambda x: (x > 0).mean() * 100),
        best_trade=("pnl", "max"), worst_trade=("pnl", "min"),
    ).round(2).reset_index()
    sym_stats = sym_stats.sort_values("total_pnl", ascending=False)

    col1, col2 = st.columns(2)

    with col1:
        colors = ["#22c55e" if x > 0 else "#ef4444" for x in sym_stats["total_pnl"]]
        fig = go.Figure(go.Bar(
            x=sym_stats["symbol"], y=sym_stats["total_pnl"], marker_color=colors,
            text=sym_stats["total_pnl"].apply(lambda x: f"${x:+,.0f}"), textposition="outside",
        ))
        fig.update_layout(
            title="💰 Total PnL by Symbol", xaxis_title="", yaxis_title="PnL ($)",
            height=400, template="plotly_dark",
            margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig, width="stretch")

    with col2:
        fig = go.Figure(go.Bar(
            x=sym_stats["symbol"], y=sym_stats["win_rate"], marker_color="#3b82f6",
            text=sym_stats["win_rate"].apply(lambda x: f"{x:.1f}%"), textposition="outside",
        ))
        fig.add_hline(y=50, line_dash="dash", line_color="#666", annotation_text="50%")
        fig.update_layout(
            title="🎯 Win Rate by Symbol", xaxis_title="", yaxis_title="Win Rate (%)",
            height=400, template="plotly_dark",
            margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig, width="stretch")

    st.markdown("#### 📋 Symbol Performance Table")
    try:
        st.dataframe(
            sym_stats.style.background_gradient(
                subset=["total_pnl"], cmap="RdYlGn"
            ).background_gradient(
                subset=["win_rate"], cmap="RdYlGn"
            ),
            width="stretch",
        )
    except ImportError:
        st.dataframe(sym_stats, width="stretch")


def _render_trade_history(df: pd.DataFrame) -> None:
    """Render trade history table."""
    display_df = df.copy()
    display_df["pnl_fmt"] = display_df["pnl"].apply(lambda x: f"${x:+,.2f}")
    display_df["result"] = display_df["pnl"].apply(lambda x: "✅" if x > 0 else "❌")
    display_df["side_fmt"] = display_df["side"].apply(lambda x: f"{'🟢' if x=='LONG' else '🔴'} {x}")

    reason_icons = {"take_profit": "🎯", "stop_loss": "🛑", "trailing_stop": "📈",
                    "partial_profit_2.5R": "💰", "partial_profit_3R": "💰",
                    "breakeven_stop": "⚖️", "timeout": "⏰", "unknown": "📌"}
    display_df["reason_fmt"] = display_df["exit_reason"].apply(
        lambda x: f"{reason_icons.get(x, '📌')} {x.replace('_', ' ').title()}"
    )

    cols = ["result", "symbol", "side_fmt", "entry_price", "pnl_fmt",
            "hold_minutes", "hold_str", "reason_fmt", "r_multiple",
            "confidence", "institutional_score", "regime", "strategy_version"]
    col_names = {"result": "", "symbol": "Symbol", "side_fmt": "Side",
                 "entry_price": "Entry", "pnl_fmt": "PnL",
                 "hold_minutes": "Hold (m)", "hold_str": "Duration",
                 "reason_fmt": "Reason", "r_multiple": "R-Mult",
                 "confidence": "Conf", "institutional_score": "Score",
                 "regime": "Regime", "strategy_version": "Version"}

    available_cols = [c for c in cols if c in display_df.columns]
    rename_map = {k: v for k, v in col_names.items() if k in available_cols}

    st.dataframe(
        display_df[available_cols].rename(columns=rename_map),
        width="stretch",
        height=min(400, 40 + len(display_df) * 33),
        hide_index=True,
    )


def _render_equity_curve(equity_history: List[Dict], metrics: Dict) -> None:
    """Render equity curve from bridge data."""
    if not equity_history:
        st.info("No equity history data available.")
        return

    equity = [e.get("equity", 10000) for e in equity_history]
    timestamps = [e.get("timestamp", 0) for e in equity_history]

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=timestamps, y=equity, mode="lines", name="Equity",
        line=dict(color="#00ff88", width=2),
        fill="tozeroy", fillcolor="rgba(0,255,136,0.05)",
    ))
    fig.add_hline(y=10000, line_dash="dash", line_color="#666")
    fig.update_layout(
        title="📈 Equity Curve", xaxis_title="Time", yaxis_title="Equity ($)",
        height=350, template="plotly_dark",
        margin=dict(l=10, r=10, t=40, b=10),
    )
    st.plotly_chart(fig, width="stretch")
