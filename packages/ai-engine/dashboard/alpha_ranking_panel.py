"""
Alpha Ranking Dashboard Panel — Symbol intelligence, tier rankings, health dashboard.
Reads from alpha_ranking.json bridge file.
"""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from plotly.subplots import make_subplots

from dashboard.data_bridge import reader as bridge_reader


def render_alpha_ranking():
    """Render the Alpha Ranking dashboard."""
    st.subheader("🏆 Adaptive Alpha Ranking Engine")

    data = bridge_reader.read_alpha_ranking()
    if not data or not data.get("symbols"):
        st.info("📊 No alpha ranking data yet. Engine will generate rankings after collecting trade data.")
        _render_empty_state()
        return

    symbols = data.get("symbols", [])
    tier_counts = data.get("tier_counts", {})
    blacklisted = data.get("blacklisted", [])
    promoted = data.get("promoted", [])
    best_today = data.get("best_today", [])
    best_week = data.get("best_week", [])
    best_month = data.get("best_month", [])

    st.caption(f"📊 {len(symbols)} symbols ranked · Last updated {datetime.fromtimestamp(data.get('timestamp', 0)).strftime('%H:%M:%S')}")

    # ── Tier Summary Cards ──
    _render_tier_cards(tier_counts, blacklisted, promoted)

    # ── Tabs ──
    tabs = st.tabs([
        "🏆 Tier Rankings",
        "📊 Symbol Health",
        "⚡ Scanner Priority",
        "💰 Capital Allocation",
        "⛔ Auto-Blacklist",
        "⭐ Auto-Promotion",
    ])

    with tabs[0]:
        _render_tier_rankings(symbols)

    with tabs[1]:
        _render_symbol_health(best_today, best_week, best_month, symbols)

    with tabs[2]:
        _render_scanner_priority(symbols)

    with tabs[3]:
        _render_capital_allocation(symbols)

    with tabs[4]:
        _render_blacklist(blacklisted)

    with tabs[5]:
        _render_promotion(promoted)


def _render_empty_state():
    """Render empty state when no data is available."""
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("🏆 S Tier", "—")
    with col2:
        st.metric("🔵 A Tier", "—")
    with col3:
        st.metric("🟡 B Tier", "—")

    st.info("""
    **How Alpha Ranking Works:**
    
    1. **ALPHA_SCORE** is calculated per symbol from:
       - Win Rate (20%)
       - Profit Factor (25%)
       - Expectancy (25%)
       - Average R (10%)
       - Consistency (10%)
       - Smart Money Accuracy (5%)
       - Sample Quality (5%)
    
    2. **Tiers**: S (≥80), A (≥60), B (≥40), C (<40)
    
    3. **Scanner Priority**: S=100%, A=75%, B=50%, C=Ignore
    
    4. **Position Sizing**: S=3x, A=2x, B=1x, C=0x
    
    5. **Auto-Blacklist**: PF<1 or Expectancy<$0 for 50+ trades
    
    6. **Auto-Promotion**: PF≥1.5, Expectancy≥$0, 20+ trades
    """)


def _render_tier_cards(tier_counts: Dict, blacklisted: List, promoted: List):
    """Render tier summary cards."""
    col1, col2, col3, col4, col5, col6 = st.columns(6)

    with col1:
        s_count = tier_counts.get("S", 0)
        st.markdown(
            f"""<div style="background:linear-gradient(135deg,#0f1420,#0c1018);
            padding:16px;border-radius:10px;border:1px solid rgba(0,255,136,0.3);
            text-align:center;">
            <div style="font-size:0.75rem;color:#00ff88;margin-bottom:4px;">🟢 S TIER</div>
            <div style="font-size:2rem;font-weight:800;color:#00ff88;">{s_count}</div>
            <div style="font-size:0.65rem;color:#8892a4;">3x Size · 100% Scan</div>
            </div>""",
            unsafe_allow_html=True,
        )

    with col2:
        a_count = tier_counts.get("A", 0)
        st.markdown(
            f"""<div style="background:linear-gradient(135deg,#0f1420,#0c1018);
            padding:16px;border-radius:10px;border:1px solid rgba(56,189,248,0.3);
            text-align:center;">
            <div style="font-size:0.75rem;color:#38bdf8;margin-bottom:4px;">🔵 A TIER</div>
            <div style="font-size:2rem;font-weight:800;color:#38bdf8;">{a_count}</div>
            <div style="font-size:0.65rem;color:#8892a4;">2x Size · 75% Scan</div>
            </div>""",
            unsafe_allow_html=True,
        )

    with col3:
        b_count = tier_counts.get("B", 0)
        st.markdown(
            f"""<div style="background:linear-gradient(135deg,#0f1420,#0c1018);
            padding:16px;border-radius:10px;border:1px solid rgba(245,158,11,0.3);
            text-align:center;">
            <div style="font-size:0.75rem;color:#f59e0b;margin-bottom:4px;">🟡 B TIER</div>
            <div style="font-size:2rem;font-weight:800;color:#f59e0b;">{b_count}</div>
            <div style="font-size:0.65rem;color:#8892a4;">1x Size · 50% Scan</div>
            </div>""",
            unsafe_allow_html=True,
        )

    with col4:
        c_count = tier_counts.get("C", 0)
        st.markdown(
            f"""<div style="background:linear-gradient(135deg,#0f1420,#0c1018);
            padding:16px;border-radius:10px;border:1px solid rgba(100,116,139,0.3);
            text-align:center;">
            <div style="font-size:0.75rem;color:#64748b;margin-bottom:4px;">⚫ C TIER</div>
            <div style="font-size:2rem;font-weight:800;color:#64748b;">{c_count}</div>
            <div style="font-size:0.65rem;color:#8892a4;">0x Size · Ignored</div>
            </div>""",
            unsafe_allow_html=True,
        )

    with col5:
        st.markdown(
            f"""<div style="background:linear-gradient(135deg,#0f1420,#0c1018);
            padding:16px;border-radius:10px;border:1px solid rgba(255,77,77,0.3);
            text-align:center;">
            <div style="font-size:0.75rem;color:#ff4d4d;margin-bottom:4px;">⛔ BLACKLISTED</div>
            <div style="font-size:2rem;font-weight:800;color:#ff4d4d;">{len(blacklisted)}</div>
            <div style="font-size:0.65rem;color:#8892a4;">Auto-disabled</div>
            </div>""",
            unsafe_allow_html=True,
        )

    with col6:
        st.markdown(
            f"""<div style="background:linear-gradient(135deg,#0f1420,#0c1018);
            padding:16px;border-radius:10px;border:1px solid rgba(0,255,136,0.3);
            text-align:center;">
            <div style="font-size:0.75rem;color:#00ff88;margin-bottom:4px;">⭐ PROMOTED</div>
            <div style="font-size:2rem;font-weight:800;color:#00ff88;">{len(promoted)}</div>
            <div style="font-size:0.65rem;color:#8892a4;">Auto-eligible</div>
            </div>""",
            unsafe_allow_html=True,
        )


def _render_tier_rankings(symbols: List[Dict]):
    """Render the full tier rankings table."""
    st.markdown("### 🏆 Tier Rankings")

    # Tier filter
    tier_filter = st.multiselect(
        "Filter by Tier",
        ["S", "A", "B", "C"],
        default=["S", "A", "B"],
        key="tier_filter",
    )

    filtered = [s for s in symbols if s.get("tier") in tier_filter]

    if not filtered:
        st.info("No symbols match the selected filters.")
        return

    # Build dataframe
    df = pd.DataFrame(filtered)

    # Tier color mapping
    tier_colors = {"S": "#00ff88", "A": "#38bdf8", "B": "#f59e0b", "C": "#64748b"}

    # Display columns
    display_cols = ["symbol", "tier", "alpha_score", "total_trades", "win_rate", "profit_factor", "expectancy", "avg_r", "total_pnl", "scanner_priority", "position_multiplier"]
    rename_map = {
        "symbol": "Symbol", "tier": "Tier", "alpha_score": "Alpha Score",
        "total_trades": "Trades", "win_rate": "WR %", "profit_factor": "PF",
        "expectancy": "Expectancy", "avg_r": "Avg R", "total_pnl": "Total PnL",
        "scanner_priority": "Scan %", "position_multiplier": "Size ×",
    }

    avail = [c for c in display_cols if c in df.columns]
    display_df = df[avail].copy()

    # Format columns
    if "alpha_score" in display_df.columns:
        display_df["alpha_score"] = display_df["alpha_score"].round(1)
    if "win_rate" in display_df.columns:
        display_df["win_rate"] = display_df["win_rate"].round(1)
    if "profit_factor" in display_df.columns:
        display_df["profit_factor"] = display_df["profit_factor"].round(2)
    if "expectancy" in display_df.columns:
        display_df["expectancy"] = display_df["expectancy"].round(2)
    if "scanner_priority" in display_df.columns:
        display_df["scanner_priority"] = (display_df["scanner_priority"] * 100).astype(int).astype(str) + "%"

    display_df = display_df.rename(columns={k: v for k, v in rename_map.items() if k in display_df.columns})

    st.dataframe(
        display_df.style.map(
            lambda v: f"color: {tier_colors.get(str(v), '#e8ecf1')}",
            subset=["Tier"] if "Tier" in display_df.columns else [],
        ),
        width="stretch",
        hide_index=True,
        height=min(500, 40 + len(display_df) * 35),
    )

    # Tier distribution chart
    st.markdown("#### 📊 Score Distribution")
    scores = [s.get("alpha_score", 0) for s in symbols]
    tiers = [s.get("tier", "C") for s in symbols]

    fig = go.Figure()
    for tier in ["S", "A", "B", "C"]:
        tier_scores = [s for s, t in zip(scores, tiers) if t == tier]
        if tier_scores:
            fig.add_trace(go.Histogram(
                x=tier_scores,
                name=f"Tier {tier}",
                marker_color=tier_colors[tier],
                opacity=0.7,
                nbinsx=20,
            ))

    fig.add_vline(x=80, line_dash="dash", line_color="#00ff88", annotation_text="S Tier (80)")
    fig.add_vline(x=60, line_dash="dash", line_color="#38bdf8", annotation_text="A Tier (60)")
    fig.add_vline(x=40, line_dash="dash", line_color="#f59e0b", annotation_text="B Tier (40)")

    fig.update_layout(
        title="🎯 ALPHA_SCORE Distribution by Tier",
        xaxis_title="Alpha Score", yaxis_title="Count",
        barmode="stack", height=300, template="plotly_dark",
        margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig, width="stretch")


def _render_symbol_health(best_today: List, best_week: List, best_month: List, all_symbols: List):
    """Render symbol health dashboard."""
    st.markdown("### 📊 Symbol Health Dashboard")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("#### 🏆 Best Symbols Today")
        if best_today:
            for i, s in enumerate(best_today[:5]):
                pnl = s.get("total_pnl", 0)
                color = "#00ff88" if pnl >= 0 else "#ff4d4d"
                st.markdown(
                    f"""<div style="background:linear-gradient(135deg,#0f1420,#0c1018);
                    padding:10px 12px;border-radius:8px;border-left:3px solid {color};
                    margin-bottom:6px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <span style="font-weight:600;color:#e8ecf1;">{s['symbol']}</span>
                        <span style="font-weight:700;color:{color};">${pnl:+.2f}</span>
                    </div>
                    <div style="font-size:0.7rem;color:#8892a4;">WR: {s.get('win_rate', 0)}% · {s.get('total_trades', 0)} trades · Tier {s.get('tier', 'C')}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )
        else:
            st.info("No trades today yet.")

    with col2:
        st.markdown("#### 📅 Best Symbols This Week")
        if best_week:
            for i, s in enumerate(best_week[:5]):
                pnl = s.get("total_pnl", 0)
                color = "#00ff88" if pnl >= 0 else "#ff4d4d"
                st.markdown(
                    f"""<div style="background:linear-gradient(135deg,#0f1420,#0c1018);
                    padding:10px 12px;border-radius:8px;border-left:3px solid {color};
                    margin-bottom:6px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <span style="font-weight:600;color:#e8ecf1;">{s['symbol']}</span>
                        <span style="font-weight:700;color:{color};">${pnl:+.2f}</span>
                    </div>
                    <div style="font-size:0.7rem;color:#8892a4;">WR: {s.get('win_rate', 0)}% · {s.get('total_trades', 0)} trades · Tier {s.get('tier', 'C')}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )
        else:
            st.info("No trades this week yet.")

    with col3:
        st.markdown("#### 📆 Best Symbols This Month")
        if best_month:
            for i, s in enumerate(best_month[:5]):
                pnl = s.get("total_pnl", 0)
                color = "#00ff88" if pnl >= 0 else "#ff4d4d"
                st.markdown(
                    f"""<div style="background:linear-gradient(135deg,#0f1420,#0c1018);
                    padding:10px 12px;border-radius:8px;border-left:3px solid {color};
                    margin-bottom:6px;">
                    <div style="display:flex;justify-content:space-between;align-items:center;">
                        <span style="font-weight:600;color:#e8ecf1;">{s['symbol']}</span>
                        <span style="font-weight:700;color:{color};">${pnl:+.2f}</span>
                    </div>
                    <div style="font-size:0.7rem;color:#8892a4;">WR: {s.get('win_rate', 0)}% · {s.get('total_trades', 0)} trades · Tier {s.get('tier', 'C')}</div>
                    </div>""",
                    unsafe_allow_html=True,
                )
        else:
            st.info("No trades this month yet.")

    # PnL by tier chart
    st.markdown("#### 💰 PnL by Tier")
    tier_pnl = {}
    for s in all_symbols:
        tier = s.get("tier", "C")
        if tier not in tier_pnl:
            tier_pnl[tier] = 0
        tier_pnl[tier] += s.get("total_pnl", 0)

    if tier_pnl:
        tier_order = ["S", "A", "B", "C"]
        tier_colors_list = ["#00ff88", "#38bdf8", "#f59e0b", "#64748b"]
        fig = go.Figure(go.Bar(
            x=[f"Tier {t}" for t in tier_order if t in tier_pnl],
            y=[tier_pnl.get(t, 0) for t in tier_order if t in tier_pnl],
            marker_color=[tier_colors_list[tier_order.index(t)] for t in tier_order if t in tier_pnl],
            text=[f"${tier_pnl.get(t, 0):+,.0f}" for t in tier_order if t in tier_pnl],
            textposition="outside",
        ))
        fig.add_hline(y=0, line_color="#666")
        fig.update_layout(
            title="💰 Total PnL by Tier", xaxis_title="", yaxis_title="PnL ($)",
            height=300, template="plotly_dark",
            margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig, width="stretch")


def _render_scanner_priority(symbols: List[Dict]):
    """Render scanner priority visualization."""
    st.markdown("### ⚡ Scanner Priority")
    st.caption("Which symbols get scanned each cycle — S=100%, A=75%, B=50%, C=Ignore")

    # Priority distribution
    priority_data = {"S (100%)": 0, "A (75%)": 0, "B (50%)": 0, "C (0%)": 0}
    for s in symbols:
        tier = s.get("tier", "C")
        if tier == "S":
            priority_data["S (100%)"] += 1
        elif tier == "A":
            priority_data["A (75%)"] += 1
        elif tier == "B":
            priority_data["B (50%)"] += 1
        else:
            priority_data["C (0%)"] += 1

    col1, col2 = st.columns(2)

    with col1:
        fig = go.Figure(go.Pie(
            labels=list(priority_data.keys()),
            values=list(priority_data.values()),
            marker=dict(colors=["#00ff88", "#38bdf8", "#f59e0b", "#64748b"]),
            hole=0.4,
            textinfo="label+value",
        ))
        fig.update_layout(
            title="📊 Symbols by Scan Priority",
            height=350, template="plotly_dark",
            margin=dict(l=10, r=10, t=40, b=10),
            showlegend=True,
        )
        st.plotly_chart(fig, width="stretch")

    with col2:
        # S and A tier symbols list
        s_tier = [s for s in symbols if s.get("tier") == "S"]
        a_tier = [s for s in symbols if s.get("tier") == "A"]

        st.markdown("#### 🟢 S Tier (100% Scan)")
        for s in s_tier:
            st.markdown(f"  • **{s['symbol']}** — Score: {s.get('alpha_score', 0):.1f}, PF: {s.get('profit_factor', 0):.2f}")

        st.markdown("#### 🔵 A Tier (75% Scan)")
        for s in a_tier[:10]:
            st.markdown(f"  • **{s['symbol']}** — Score: {s.get('alpha_score', 0):.1f}, PF: {s.get('profit_factor', 0):.2f}")
        if len(a_tier) > 10:
            st.caption(f"  ... and {len(a_tier) - 10} more")


def _render_capital_allocation(symbols: List[Dict]):
    """Render smart capital allocation view."""
    st.markdown("### 💰 Smart Capital Allocation")
    st.caption("Position sizing: S=3x, A=2x, B=1x, C=0x (ignored)")

    # Allocation breakdown
    alloc_data = []
    for s in symbols:
        tier = s.get("tier", "C")
        mult = {"S": 3, "A": 2, "B": 1, "C": 0}.get(tier, 0)
        alloc_data.append({
            "Symbol": s["symbol"],
            "Tier": tier,
            "Alpha Score": s.get("alpha_score", 0),
            "Size Multiplier": f"{mult}x",
            "Total PnL": f"${s.get('total_pnl', 0):+,.2f}",
            "WR %": f"{s.get('win_rate', 0):.1f}%",
            "PF": f"{s.get('profit_factor', 0):.2f}",
        })

    df = pd.DataFrame(alloc_data)

    # Filter to non-zero allocation
    active = df[df["Size Multiplier"] != "0x"]
    inactive = df[df["Size Multiplier"] == "0x"]

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(f"#### 🟢 Active Allocation ({len(active)} symbols)")
        if len(active) > 0:
            st.dataframe(active, width="stretch", hide_index=True, height=min(400, 40 + len(active) * 35))

    with col2:
        st.markdown(f"#### ⚫ No Allocation ({len(inactive)} symbols)")
        if len(inactive) > 0:
            st.dataframe(inactive.head(20), width="stretch", hide_index=True, height=min(400, 40 + min(20, len(inactive)) * 35))

    # Allocation impact chart
    st.markdown("#### 📊 Allocation Impact")
    tier_stats = {}
    for s in symbols:
        tier = s.get("tier", "C")
        if tier not in tier_stats:
            tier_stats[tier] = {"count": 0, "total_pnl": 0, "avg_score": []}
        tier_stats[tier]["count"] += 1
        tier_stats[tier]["total_pnl"] += s.get("total_pnl", 0)
        tier_stats[tier]["avg_score"].append(s.get("alpha_score", 0))

    tier_order = ["S", "A", "B", "C"]
    tier_colors_list = ["#00ff88", "#38bdf8", "#f59e0b", "#64748b"]

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    for i, tier in enumerate(tier_order):
        if tier in tier_stats:
            stats = tier_stats[tier]
            avg_score = np.mean(stats["avg_score"]) if stats["avg_score"] else 0
            fig.add_trace(go.Bar(
                x=[f"Tier {tier}"],
                y=[stats["total_pnl"]],
                name=f"Tier {tier} PnL",
                marker_color=tier_colors_list[i],
                text=[f"${stats['total_pnl']:+,.0f}"],
                textposition="outside",
            ), secondary_y=False)
            fig.add_trace(go.Scatter(
                x=[f"Tier {tier}"],
                y=[avg_score],
                name=f"Tier {tier} Avg Score",
                mode="markers",
                marker=dict(size=12, color=tier_colors_list[i], symbol="diamond"),
                showlegend=False,
            ), secondary_y=True)

    fig.add_hline(y=0, line_color="#666", secondary_y=False)
    fig.update_layout(
        title="💰 PnL & Average Score by Tier",
        height=350, template="plotly_dark",
        margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    fig.update_yaxes(title_text="Total PnL ($)", secondary_y=False)
    fig.update_yaxes(title_text="Avg Alpha Score", secondary_y=True)
    st.plotly_chart(fig, width="stretch")


def _render_blacklist(blacklisted: List):
    """Render auto-blacklist section."""
    st.markdown("### ⛔ Auto-Blacklist")
    st.caption("Symbols disabled: PF < 1 or Expectancy < $0 for 50+ trades")

    if not blacklisted:
        st.success("✅ No symbols currently blacklisted. All symbols performing adequately.")
        return

    for b in blacklisted:
        st.markdown(
            f"""<div style="background:linear-gradient(135deg,#0f1420,#0c1018);
            padding:12px 16px;border-radius:8px;border-left:4px solid #ff4d4d;
            margin-bottom:8px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="font-weight:700;color:#ff4d4d;">⛔ {b['symbol']}</span>
                <span style="font-size:0.8rem;color:#8892a4;">{b['trades']} trades</span>
            </div>
            <div style="font-size:0.85rem;color:#e8ecf1;margin-top:4px;">{b['reason']}</div>
            <div style="font-size:0.75rem;color:#8892a4;margin-top:2px;">PF: {b['pf']:.2f} · PnL: ${b['pnl']:+,.2f}</div>
            </div>""",
            unsafe_allow_html=True,
        )


def _render_promotion(promoted: List):
    """Render auto-promotion section."""
    st.markdown("### ⭐ Auto-Promotion")
    st.caption("Symbols eligible for promotion: PF ≥ 1.5, Expectancy ≥ $0, 20+ trades")

    if not promoted:
        st.info("No symbols currently eligible for promotion.")
        return

    for p in promoted:
        st.markdown(
            f"""<div style="background:linear-gradient(135deg,#0f1420,#0c1018);
            padding:12px 16px;border-radius:8px;border-left:4px solid #00ff88;
            margin-bottom:8px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <span style="font-weight:700;color:#00ff88;">⭐ {p['symbol']}</span>
                <span style="font-size:0.8rem;color:#8892a4;">{p['trades']} trades</span>
            </div>
            <div style="font-size:0.85rem;color:#e8ecf1;margin-top:4px;">{p['reason']}</div>
            <div style="font-size:0.75rem;color:#8892a4;margin-top:2px;">PF: {p['pf']:.2f} · PnL: ${p['pnl']:+,.2f}</div>
            </div>""",
            unsafe_allow_html=True,
        )
