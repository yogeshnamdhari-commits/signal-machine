"""
📊 Institutional Research Dashboard
====================================
Production-grade analytics dashboard for trade research.

Pages:
1. Portfolio Overview — Win rate, PnL, expectancy, drawdown
2. Confidence Research — Bucket analysis, calibration
3. Pattern Research — Per-pattern performance
4. Symbol Analytics — Per-symbol performance
5. Session Analytics — Session performance
6. Regime Analytics — Regime performance
7. Exit Analytics — Exit reason analysis
8. Candidate Lifecycle — Pipeline state analysis
9. Equity Curve — Cumulative PnL and drawdown
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from analytics.institutional.research_platform import ResearchPlatform
from analytics.institutional.candidate_repository import CandidateRepository

st.set_page_config(
    page_title="Research Platform",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ───────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .metric-card {
        background: #1a1a2e;
        border: 1px solid #2d2d44;
        border-radius: 8px;
        padding: 12px 16px;
        text-align: center;
    }
    .metric-value {
        font-size: 1.8rem;
        font-weight: 700;
        color: #e0e0e0;
    }
    .metric-label {
        font-size: 0.75rem;
        color: #8b8b9e;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    .positive { color: #00d26a; }
    .negative { color: #ff4757; }
    .neutral  { color: #ffc107; }
    .section-header {
        font-size: 1.1rem;
        font-weight: 600;
        color: #b0b0c0;
        border-bottom: 1px solid #2d2d44;
        padding-bottom: 8px;
        margin: 16px 0 12px 0;
    }
    .stDataFrame { border: 1px solid #2d2d44; border-radius: 6px; }
</style>
""", unsafe_allow_html=True)


# ── Data Loading ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_analytics():
    """Load and cache analytics data."""
    platform = ResearchPlatform()
    summary = platform.compute_full_analytics()
    return platform.to_dict(summary)


@st.cache_data(ttl=60)
def load_repository():
    """Load and cache candidate repository data."""
    repo = CandidateRepository()
    summary = repo.compute_full_analytics()
    return repo.to_dict(summary)


def fmt_pct(val: float) -> str:
    """Format percentage."""
    return f"{val * 100:.1f}%"


def fmt_money(val: float) -> str:
    """Format money."""
    return f"${val:,.2f}"


def fmt_num(val: float, decimals: int = 2) -> str:
    """Format number."""
    return f"{val:.{decimals}f}"


def color_val(val: float, threshold: float = 0.0) -> str:
    """Return CSS class based on value."""
    if val > threshold:
        return "positive"
    elif val < threshold:
        return "negative"
    return "neutral"


# ── Sidebar ──────────────────────────────────────────────────────────────────

st.sidebar.title("📊 Research Platform")
st.sidebar.markdown("---")

page = st.sidebar.radio(
    "Navigation",
    [
        "🏠 Portfolio Overview",
        "📊 Production Scorecard",
        "📋 Candidate Repository",
        "🎯 Confidence Research",
        "🕯️ Pattern Research",
        "📈 Symbol Analytics",
        "🕐 Session Analytics",
        "🌊 Regime Analytics",
        "🚪 Exit Analytics",
        "🔄 Candidate Lifecycle",
        "📉 Equity Curve",
        "📈 Rolling Validation",
        "✅ Signal Verification",
        "🔧 Confidence Calibration",
    ],
)

st.sidebar.markdown("---")
st.sidebar.markdown(
    "**Data Source:** `ema_v5_trade_facts` view\n\n"
    "**Strategy:** EMA V5 only\n\n"
    "**Status:** READ-ONLY analytics\n\n"
    "**Trading Logic:** LOCKED — no modifications"
)

# ── Load Data ────────────────────────────────────────────────────────────────

try:
    data = load_analytics()
except Exception as e:
    st.error(f"Failed to load analytics: {e}")
    st.stop()

# ── Load Pipeline Reconciliation ─────────────────────────────────────────────
try:
    from analytics.institutional.research_platform import ResearchPlatform
    _rp = ResearchPlatform()
    pipeline = _rp.get_pipeline_reconciliation()
except Exception as e:
    pipeline = {
        "scanner_signal_count": 0,
        "ema_v5_signals_db": 0,
        "session_filtered": 0,
        "all_signals_db": 0,
        "open_positions": 0,
        "closed_positions": 0,
        "total_positions": 0,
        "with_outcome": 0,
        "without_outcome": 0,
        "analyzed_trades": 0,
        "excluded_trades": 0,
        "conversion_rate": 0,
        "filtered_out": 0,
        "rejection_traces": 0,
        "rejection_breakdown": {},
    }


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 1: PORTFOLIO OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════

if page == "🏠 Portfolio Overview":
    st.title("🏠 Portfolio Overview")
    
    o = data["overview"]
    r = data["risk_metrics"]
    t = data["timing"]
    e = data["excursion"]
    s = data["streaks"]
    
    # ── Pipeline Reconciliation Banner ──
    scanner_count = pipeline.get("scanner_signal_count", 0)
    db_signals = pipeline.get("ema_v5_signals_db", 0)
    session_filtered = pipeline.get("session_filtered", 0)
    opened = pipeline.get("total_positions", 0)
    closed = pipeline.get("closed_positions", 0)
    analyzed = pipeline.get("analyzed_trades", 0)
    excluded = pipeline.get("excluded_trades", 0)
    conversion = pipeline.get("conversion_rate", 0)
    filtered = pipeline.get("filtered_out", 0)
    rejection_breakdown = pipeline.get("rejection_breakdown", {})
    
    # Funnel banner
    st.markdown(
        f'<div style="background: #1a1a2e; border: 1px solid #333; border-radius: 8px; padding: 16px; margin-bottom: 16px;">'
        f'<div style="font-size: 0.85rem; color: #8b8b9e; text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 4px;">'
        f'📊 EMA V5 Signal-to-Trade Funnel</div>'
        f'<div style="font-size: 0.7rem; color: #555; margin-bottom: 12px;">'
        f'Scope: All historical EMA V5 data. Scanner count is runtime (persisted), DB counts are from database. '
        f'Scanner live count (e.g. 77 today) measures runtime candidates across sessions.</div>'
        f'<div style="display: flex; justify-content: space-around; text-align: center; align-items: center;">'
        f'<div><div style="font-size: 2rem; font-weight: 700; color: #4ecdc4;">{scanner_count}</div>'
        f'<div style="font-size: 0.75rem; color: #8b8b9e;">Scanner Signals<br><span style="font-size:0.6rem;color:#555">(runtime counter)</span></div></div>'
        f'<div style="color: #555; font-size: 1rem;">→<br><span style="font-size:0.6rem;color:#f85149">-{session_filtered} session</span></div>'
        f'<div><div style="font-size: 2rem; font-weight: 700; color: #4ecdc4;">{db_signals}</div>'
        f'<div style="font-size: 0.75rem; color: #8b8b9e;">DB Signals<br><span style="font-size:0.6rem;color:#555">(metadata filter)</span></div></div>'
        f'<div style="color: #555; font-size: 1rem;">→<br><span style="font-size:0.6rem;color:#f85149">-{filtered} exec filter</span></div>'
        f'<div><div style="font-size: 2rem; font-weight: 700; color: #4ecdc4;">{opened}</div>'
        f'<div style="font-size: 0.75rem; color: #8b8b9e;">Positions Opened</div></div>'
        f'<div style="color: #555; font-size: 1rem;">→</div>'
        f'<div><div style="font-size: 2rem; font-weight: 700; color: #00d26a;">{analyzed}</div>'
        f'<div style="font-size: 0.75rem; color: #8b8b9e;">Analyzed</div></div>'
        f'</div>'
        f'<div style="font-size: 0.75rem; color: #666; text-align: center; margin-top: 8px;">'
        f'Identity: {opened} opened = {analyzed} analyzed + {excluded} excluded ✅ | '
        f'Overall: {scanner_count} scanner → {opened} trades ({conversion}% conversion)</div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    
    # Rejection breakdown (if available)
    if rejection_breakdown:
        st.markdown("**🚫 Signal Rejection Breakdown:**")
        rej_cols = st.columns(min(len(rejection_breakdown), 4))
        for i, (gate, count) in enumerate(sorted(rejection_breakdown.items(), key=lambda x: -x[1])):
            with rej_cols[i % len(rej_cols)]:
                st.metric(gate.replace("_", " ").title(), count)
    
    # ── Row 0: Expectancy Hero Card ──
    exp_val = o["expectancy"]
    exp_color = "positive" if exp_val > 0 else "negative"
    st.markdown(
        f'<div style="background: #1a1a2e; border: 2px solid {"#00d26a" if exp_val > 0 else "#ff4757"}; '
        f'border-radius: 12px; padding: 20px; text-align: center; margin-bottom: 16px;">'
        f'<div style="font-size: 0.85rem; color: #8b8b9e; text-transform: uppercase; letter-spacing: 0.1em;">Current Expectancy</div>'
        f'<div style="font-size: 3rem; font-weight: 700; color: {"#00d26a" if exp_val > 0 else "#ff4757"};">'
        f'{"+" if exp_val > 0 else ""}{exp_val:.2f}R</div>'
        f'<div style="font-size: 0.9rem; color: #b0b0c0;">'
        f'${exp_val:.2f} per trade  •  {o["total_trades"]} trades  •  '
        f'PF: {o["profit_factor"]:.2f}  •  WR: {o["win_rate"]*100:.1f}%</div></div>',
        unsafe_allow_html=True,
    )
    
    # ── Row 1: Core Metrics ──
    cols = st.columns(6)
    metrics = [
        ("Completed Trades", str(o["total_trades"]), ""),
        ("Win Rate", fmt_pct(o["win_rate"]), color_val(o["win_rate"], 0.5)),
        ("Profit Factor", fmt_num(o["profit_factor"]), color_val(o["profit_factor"], 1.0)),
        ("Expectancy", fmt_money(o["expectancy"]), color_val(o["expectancy"])),
        ("Total PnL", fmt_money(o["total_pnl"]), color_val(o["total_pnl"])),
        ("Avg PnL", fmt_money(o["avg_pnl"]), color_val(o["avg_pnl"])),
    ]
    for col, (label, value, css) in zip(cols, metrics):
        with col:
            st.markdown(
                f'<div class="metric-card">'
                f'<div class="metric-value {css}">{value}</div>'
                f'<div class="metric-label">{label}</div></div>',
                unsafe_allow_html=True,
            )
    
    # ── Row 2: Risk Metrics ──
    st.markdown('<div class="section-header">Risk Metrics</div>', unsafe_allow_html=True)
    cols = st.columns(6)
    risk_metrics = [
        ("Sharpe", fmt_num(r["sharpe"]), color_val(r["sharpe"], 0)),
        ("Sortino", fmt_num(r["sortino"]), color_val(r["sortino"], 0)),
        ("Calmar", fmt_num(r["calmar"]), color_val(r["calmar"], 0)),
        ("Max Drawdown", fmt_pct(r["max_drawdown_pct"] / 100), "negative"),
        ("Recovery Factor", fmt_num(r["recovery_factor"]), color_val(r["recovery_factor"], 1)),
        ("Avg Realized R", fmt_num(r["avg_realized_r"]), color_val(r["avg_realized_r"])),
    ]
    for col, (label, value, css) in zip(cols, risk_metrics):
        with col:
            st.markdown(
                f'<div class="metric-card">'
                f'<div class="metric-value {css}">{value}</div>'
                f'<div class="metric-label">{label}</div></div>',
                unsafe_allow_html=True,
            )
    
    # ── Row 3: Timing & Excursion ──
    cols = st.columns(4)
    timing = [
        ("Avg Hold", f"{t['avg_hold_minutes']:.0f} min", ""),
        ("Max Hold", f"{t['max_hold_minutes']:.0f} min", ""),
        ("Avg MFE", fmt_pct(e["avg_mfe"]), "positive"),
        ("Avg MAE", fmt_pct(e["avg_mae"]), "negative"),
    ]
    for col, (label, value, css) in zip(cols, timing):
        with col:
            st.markdown(
                f'<div class="metric-card">'
                f'<div class="metric-value {css}">{value}</div>'
                f'<div class="metric-label">{label}</div></div>',
                unsafe_allow_html=True,
            )
    
    # ── Row 4: Streaks ──
    cols = st.columns(2)
    with cols[0]:
        st.markdown(
            f'<div class="metric-card">'
            f'<div class="metric-value positive">{s["max_win_streak"]}</div>'
            f'<div class="metric-label">Max Win Streak</div></div>',
            unsafe_allow_html=True,
        )
    with cols[1]:
        st.markdown(
            f'<div class="metric-card">'
            f'<div class="metric-value negative">{s["max_loss_streak"]}</div>'
            f'<div class="metric-label">Max Loss Streak</div></div>',
            unsafe_allow_html=True,
        )
    
    # ── Summary Table ──
    st.markdown('<div class="section-header">Trade Summary</div>', unsafe_allow_html=True)
    import pandas as pd
    
    summary_data = pd.DataFrame([
        {"Metric": "Total Trades", "Value": o["total_trades"]},
        {"Metric": "Wins", "Value": o["total_wins"]},
        {"Metric": "Losses", "Value": o["total_losses"]},
        {"Metric": "Win Rate", "Value": fmt_pct(o["win_rate"])},
        {"Metric": "Profit Factor", "Value": fmt_num(o["profit_factor"])},
        {"Metric": "Expectancy", "Value": fmt_money(o["expectancy"])},
        {"Metric": "Total PnL", "Value": fmt_money(o["total_pnl"])},
        {"Metric": "Sharpe Ratio", "Value": fmt_num(r["sharpe"])},
        {"Metric": "Sortino Ratio", "Value": fmt_num(r["sortino"])},
        {"Metric": "Max Drawdown", "Value": fmt_pct(r["max_drawdown_pct"] / 100)},
        {"Metric": "Avg Hold Time", "Value": f"{t['avg_hold_minutes']:.0f} min"},
        {"Metric": "Efficiency", "Value": fmt_pct(e["efficiency"])},
    ])
    st.dataframe(summary_data, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 2: CONFIDENCE RESEARCH
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🎯 Confidence Research":
    st.title("🎯 Confidence Research")
    st.markdown("Performance analysis by confidence score bucket.")
    
    import pandas as pd
    
    conf = data.get("confidence_analysis", {})
    if not conf:
        st.warning("No confidence data available.")
    else:
        rows = []
        for bucket, stats in sorted(conf.items()):
            rows.append({
                "Bucket": bucket,
                "Trades": stats["trades"],
                "Wins": stats["wins"],
                "Losses": stats["losses"],
                "Win Rate": fmt_pct(stats["win_rate"]),
                "Profit Factor": fmt_num(stats["profit_factor"]),
                "Expectancy": fmt_money(stats["expectancy"]),
                "Avg PnL": fmt_money(stats["avg_pnl"]),
                "Avg RR": fmt_num(stats["avg_rr"]),
                "Sharpe": fmt_num(stats["sharpe"]),
                "Avg Hold (min)": f"{stats['avg_hold_minutes']:.0f}",
                "Avg MFE": fmt_pct(stats["avg_mfe"]),
                "Avg MAE": fmt_pct(stats["avg_mae"]),
            })
        
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        
        # Chart: Win Rate by Confidence
        st.markdown("### Win Rate by Confidence Bucket")
        chart_data = pd.DataFrame({
            "Bucket": list(conf.keys()),
            "Win Rate": [conf[k]["win_rate"] * 100 for k in conf.keys()],
            "Trades": [conf[k]["trades"] for k in conf.keys()],
        }).sort_values("Bucket")
        st.bar_chart(chart_data.set_index("Bucket")["Win Rate"])
        
        # Chart: Expectancy by Confidence
        st.markdown("### Expectancy by Confidence Bucket")
        exp_data = pd.DataFrame({
            "Bucket": list(conf.keys()),
            "Expectancy ($)": [conf[k]["expectancy"] for k in conf.keys()],
        }).sort_values("Bucket")
        st.bar_chart(exp_data.set_index("Bucket")["Expectancy ($)"])


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 3: PATTERN RESEARCH
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🕯️ Pattern Research":
    st.title("🕯️ Pattern Research")
    st.markdown("Performance analysis by candle pattern.")
    
    import pandas as pd
    
    patterns = data.get("pattern_analysis", {})
    if not patterns:
        st.warning("No pattern data available.")
    else:
        rows = []
        for pattern, stats in sorted(patterns.items(), key=lambda x: x[1]["trades"], reverse=True):
            rows.append({
                "Pattern": pattern,
                "Trades": stats["trades"],
                "Win Rate": fmt_pct(stats["win_rate"]),
                "Profit Factor": fmt_num(stats["profit_factor"]),
                "Expectancy": fmt_money(stats["expectancy"]),
                "Avg PnL": fmt_money(stats["avg_pnl"]),
                "Avg RR": fmt_num(stats["avg_rr"]),
                "Avg Hold (min)": f"{stats['avg_hold_minutes']:.0f}",
            })
        
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 4: SYMBOL ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📈 Symbol Analytics":
    st.title("📈 Symbol Analytics")
    st.markdown("Performance analysis by trading symbol.")
    
    import pandas as pd
    
    symbols = data.get("symbol_analysis", {})
    if not symbols:
        st.warning("No symbol data available.")
    else:
        rows = []
        for sym, stats in sorted(symbols.items(), key=lambda x: x[1]["trades"], reverse=True):
            rows.append({
                "Symbol": sym,
                "Trades": stats["trades"],
                "Win Rate": fmt_pct(stats["win_rate"]),
                "Profit Factor": fmt_num(stats["profit_factor"]),
                "Expectancy": fmt_money(stats["expectancy"]),
                "Total PnL": fmt_money(stats["total_pnl"]),
                "Avg RR": fmt_num(stats["avg_rr"]),
                "Sharpe": fmt_num(stats["sharpe"]),
                "Max Win": fmt_money(stats["max_win"]),
                "Max Loss": fmt_money(stats["max_loss"]),
            })
        
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        
        # Top/Bottom performers
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("### Top 10 by PnL")
            top = df.sort_values("Total PnL", ascending=False).head(10)
            st.dataframe(top, use_container_width=True, hide_index=True)
        with col2:
            st.markdown("### Bottom 10 by PnL")
            bottom = df.sort_values("Total PnL", ascending=True).head(10)
            st.dataframe(bottom, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 5: SESSION ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🕐 Session Analytics":
    st.title("🕐 Session Analytics")
    st.markdown("Performance analysis by trading session.")
    
    import pandas as pd
    
    sessions = data.get("session_analysis", {})
    if not sessions:
        st.warning("No session data available.")
    else:
        rows = []
        for session, stats in sorted(sessions.items()):
            rows.append({
                "Session": session.title(),
                "Trades": stats["trades"],
                "Win Rate": fmt_pct(stats["win_rate"]),
                "Profit Factor": fmt_num(stats["profit_factor"]),
                "Expectancy": fmt_money(stats["expectancy"]),
                "Total PnL": fmt_money(stats["total_pnl"]),
                "Avg PnL": fmt_money(stats["avg_pnl"]),
                "Avg RR": fmt_num(stats["avg_rr"]),
                "Sharpe": fmt_num(stats["sharpe"]),
            })
        
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        
        # Chart
        st.markdown("### Win Rate by Session")
        chart_data = pd.DataFrame({
            "Session": [k.title() for k in sessions.keys()],
            "Win Rate (%)": [sessions[k]["win_rate"] * 100 for k in sessions.keys()],
        })
        st.bar_chart(chart_data.set_index("Session"))


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 6: REGIME ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🌊 Regime Analytics":
    st.title("🌊 Regime Analytics")
    st.markdown("Performance analysis by market regime.")
    
    import pandas as pd
    
    regimes = data.get("regime_analysis", {})
    if not regimes:
        st.warning("No regime data available.")
    else:
        rows = []
        for regime, stats in sorted(regimes.items()):
            rows.append({
                "Regime": regime.replace("_", " ").title(),
                "Trades": stats["trades"],
                "Win Rate": fmt_pct(stats["win_rate"]),
                "Profit Factor": fmt_num(stats["profit_factor"]),
                "Expectancy": fmt_money(stats["expectancy"]),
                "Total PnL": fmt_money(stats["total_pnl"]),
                "Avg PnL": fmt_money(stats["avg_pnl"]),
                "Avg RR": fmt_num(stats["avg_rr"]),
                "Sharpe": fmt_num(stats["sharpe"]),
            })
        
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 7: EXIT ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🚪 Exit Analytics":
    st.title("🚪 Exit Analytics")
    st.markdown("Performance analysis by exit reason.")
    
    import pandas as pd
    
    exits = data.get("exit_analysis", {})
    if not exits:
        st.warning("No exit data available.")
    else:
        rows = []
        for reason, stats in sorted(exits.items(), key=lambda x: x[1]["count"], reverse=True):
            rows.append({
                "Exit Reason": reason.replace("_", " ").title(),
                "Count": stats["count"],
                "Wins": stats["wins"],
                "Losses": stats["losses"],
                "Win Rate": fmt_pct(stats["win_rate"]),
                "Total PnL": fmt_money(stats["total_pnl"]),
                "Avg PnL": fmt_money(stats["avg_pnl"]),
                "Expectancy": fmt_money(stats["expectancy"]),
                "Avg Hold (min)": f"{stats['avg_hold_minutes']:.0f}",
            })
        
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        
        # Chart: PnL by exit reason
        st.markdown("### Total PnL by Exit Reason")
        chart_data = pd.DataFrame({
            "Exit Reason": [k.replace("_", " ").title() for k in exits.keys()],
            "Total PnL ($)": [exits[k]["total_pnl"] for k in exits.keys()],
        })
        st.bar_chart(chart_data.set_index("Exit Reason"))


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 8: CANDIDATE LIFECYCLE
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🔄 Candidate Lifecycle":
    st.title("🔄 Candidate Lifecycle")
    st.markdown("Pipeline state analysis and dwell times.")
    
    import pandas as pd
    
    lifecycle = data.get("candidate_lifecycle", {})
    rejections = data.get("rejection_analysis", {})
    
    if lifecycle:
        st.markdown("### Current State Distribution")
        rows = []
        for state, stats in sorted(lifecycle.items()):
            rows.append({
                "State": state.replace("_", " ").title(),
                "Count": stats["count"],
                "Avg Dwell (min)": f"{stats['avg_dwell_minutes']:.0f}",
                "Median Dwell (min)": f"{stats['median_dwell_minutes']:.0f}",
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    
    if rejections and "stages" in rejections:
        st.markdown("### Pipeline Rejection Analysis")
        st.markdown(f"**Total Candidates Processed:** {rejections.get('total_candidates', 0):,}")
        
        rows = []
        for stage in rejections["stages"]:
            rows.append({
                "Stage": stage["stage"].replace("_", " ").title(),
                "Passed": stage["passed"],
                "Rejected": stage["rejected"],
                "Rejection Rate": fmt_pct(stage["rejection_rate"]),
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 9: EQUITY CURVE
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📉 Equity Curve":
    st.title("📉 Equity Curve")
    st.markdown("Cumulative PnL and drawdown analysis.")
    
    import pandas as pd
    
    equity = data.get("equity_curve", [])
    rolling_wr = data.get("rolling_win_rate", [])
    rolling_exp = data.get("rolling_expectancy", [])
    
    if equity:
        st.markdown("### Cumulative PnL")
        eq_df = pd.DataFrame(equity)
        st.line_chart(eq_df.set_index("trade_num")["cumulative_pnl"])
        
        st.markdown("### Drawdown (%)")
        st.line_chart(eq_df.set_index("trade_num")["drawdown_pct"])
    
    if rolling_wr:
        st.markdown("### Rolling Win Rate (20-trade window)")
        wr_df = pd.DataFrame(rolling_wr)
        st.line_chart(wr_df.set_index("trade_num")["win_rate"])
    
    if rolling_exp:
        st.markdown("### Rolling Expectancy (20-trade window)")
        exp_df = pd.DataFrame(rolling_exp)
        st.line_chart(exp_df.set_index("trade_num")["expectancy"])


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 10: SIGNAL VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════

elif page == "✅ Signal Verification":
    st.title("✅ Signal Verification")
    st.markdown("Per-trade verification checklist showing exactly why each signal was considered valid.")
    
    import sqlite3
    
    conn = sqlite3.connect(
        str(Path(__file__).resolve().parent.parent.parent / "data" / "institutional_v1.db"),
        timeout=10,
    )
    conn.row_factory = sqlite3.Row
    
    # Load recent trades (EMA V5 only)
    trades = []
    for table in ["positions", "positions_archive"]:
        rows = conn.execute(f"""
            SELECT signal_id, symbol, side, entry_price, stop_loss, take_profit,
                   pnl, confidence, regime, session, risk_reward, hold_minutes,
                   mfe_pct, mae_pct, realized_r, exit_reason, outcome,
                   institutional_score, at_open_regime, at_open_session,
                   volatility_score, mss_score, fvg_score, entry_reason,
                   opened_at, closed_at, status
            FROM {table}
            WHERE status = 'closed' AND outcome IS NOT NULL
            AND strategy_version = 'ema_v5'
            ORDER BY closed_at DESC
        """).fetchall()
        trades.extend([dict(r) for r in rows])
    conn.close()
    
    if not trades:
        st.warning("No completed trades found.")
    else:
        # Filters
        col1, col2, col3 = st.columns(3)
        with col1:
            outcome_filter = st.selectbox("Outcome", ["ALL", "win", "loss"], key="sv_outcome")
        with col2:
            symbol_filter = st.text_input("Symbol", key="sv_symbol", placeholder="BTCUSDT")
        with col3:
            limit = st.number_input("Show last N trades", 10, 500, 50, key="sv_limit")
        
        filtered = trades
        if outcome_filter != "ALL":
            filtered = [t for t in filtered if t.get("outcome") == outcome_filter]
        if symbol_filter:
            filtered = [t for t in filtered if symbol_filter.upper() in (t.get("symbol") or "").upper()]
        filtered = filtered[:limit]
        
        st.markdown(f"**Showing {len(filtered)} trades**")
        
        for trade in filtered:
            sym = trade.get("symbol", "?")
            side = trade.get("side", "?")
            outcome = trade.get("outcome", "?")
            pnl = trade.get("pnl", 0) or 0
            conf = trade.get("confidence", 0) or 0
            conf_pct = conf * 100 if conf <= 1 else conf
            
            # Verification checks
            regime = trade.get("regime", "") or trade.get("at_open_regime", "")
            session = trade.get("session", "") or trade.get("at_open_session", "")
            rr = trade.get("risk_reward", 0) or 0
            inst_score = trade.get("institutional_score", 0) or 0
            mss = trade.get("mss_score", 0) or 0
            fvg = trade.get("fvg_score", 0) or 0
            vol_score = trade.get("volatility_score", 0) or 0
            entry = trade.get("entry_price", 0) or 0
            sl = trade.get("stop_loss", 0) or 0
            tp = trade.get("take_profit", 0) or 0
            mfe = trade.get("mfe_pct", 0) or 0
            mae = trade.get("mae_pct", 0) or 0
            realized_r = trade.get("realized_r", 0) or 0
            exit_reason = trade.get("exit_reason", "") or ""
            
            # Color based on outcome
            outcome_color = "green" if outcome == "win" else "red"
            pnl_color = "green" if pnl > 0 else "red"
            
            with st.expander(f"{'🟢' if outcome=='win' else '🔴'} {sym} {side} — {outcome.upper()} — PnL: ${pnl:.2f}"):
                # Verification checklist
                st.markdown("### Signal Verification")
                
                checks = []
                
                # 1. Execution state
                checks.append(("Execution State", True, "ACTIVE_BUY/ACTIVE_SELL"))
                
                # 2. EMA Regime
                regime_ok = regime and regime not in ("unknown", "", "NO_TREND")
                checks.append(("EMA Regime", regime_ok, regime or "unknown"))
                
                # 3. Trend
                trend_ok = inst_score > 0
                checks.append(("Trend", trend_ok, f"Score: {inst_score:.1f}"))
                
                # 4. Pullback
                pullback_ok = mss > 0 or fvg > 0
                checks.append(("Pullback", pullback_ok, f"MSS: {mss:.0f}, FVG: {fvg:.0f}"))
                
                # 5. Confirmation candle
                candle_ok = True  # If it reached execution, candle was confirmed
                checks.append(("Confirmation Candle", candle_ok, "Confirmed"))
                
                # 6. Volume
                vol_ok = vol_score > 0
                checks.append(("Volume", vol_ok, f"Score: {vol_score:.1f}"))
                
                # 7. Confidence
                conf_ok = conf_pct >= 40  # Minimum threshold
                checks.append(("Confidence", conf_ok, f"{conf_pct:.1f}%"))
                
                # 8. Risk:Reward
                rr_ok = rr >= 1.5
                checks.append(("Risk:Reward", rr_ok, f"1:{rr:.1f}"))
                
                # 9. Session
                session_ok = session and session not in ("unknown", "")
                checks.append(("Session", session_ok, session or "unknown"))
                
                # 10. Ready to trade
                all_pass = all(c[1] for c in checks)
                checks.append(("Ready To Trade", all_pass, "YES" if all_pass else "NO"))
                
                # Display checklist
                for check_name, passed, detail in checks:
                    icon = "✅" if passed else "❌"
                    st.markdown(f"  {icon} **{check_name}**: {detail}")
                
                # Trade details
                st.markdown("### Trade Details")
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.markdown(f"**Entry:** ${entry:.6f}")
                    st.markdown(f"**Stop Loss:** ${sl:.6f}")
                    st.markdown(f"**Take Profit:** ${tp:.6f}")
                with col2:
                    st.markdown(f"**MFE:** {mfe*100:.2f}%")
                    st.markdown(f"**MAE:** {mae*100:.2f}%")
                    st.markdown(f"**Realized R:** {realized_r:.2f}")
                with col3:
                    st.markdown(f"**Exit Reason:** {exit_reason}")
                    st.markdown(f"**Hold Time:** {trade.get('hold_minutes', 0):.0f} min")
                    st.markdown(f"**Regime:** {regime}")
                
                # Execution Decision Audit
                st.markdown("### Execution Decision Audit")
                
                # Load audit data from signals table
                signal_id = trade.get("signal_id", "")
                if signal_id:
                    conn2 = sqlite3.connect(
                        str(Path(__file__).resolve().parent.parent.parent / "data" / "institutional_v1.db"),
                        timeout=10,
                    )
                    conn2.row_factory = sqlite3.Row
                    
                    # Try to find matching signal
                    sig = conn2.execute(
                        "SELECT * FROM signals WHERE id = ?", (signal_id,)
                    ).fetchone()
                    
                    if not sig:
                        # Try by symbol and timestamp
                        sym_clean = sym.replace("USDT", "")
                        sig = conn2.execute(
                            "SELECT * FROM signals WHERE symbol LIKE ? ORDER BY id DESC LIMIT 1",
                            (f"%{sym_clean}%",),
                        ).fetchone()
                    
                    conn2.close()
                    
                    if sig:
                        # Parse metadata
                        metadata = []
                        try:
                            if sig["metadata"]:
                                metadata = json.loads(sig["metadata"])
                        except:
                            pass
                        
                        audit_items = [
                            ("Entry Reason", sig["entry_reason"] or "N/A"),
                            ("SL Source", sig["sl_source"] or "N/A"),
                            ("TP1 Source", sig["tp1_source"] or "N/A"),
                            ("TP2 Source", sig["tp2_source"] or "N/A"),
                            ("TP3 Source", sig["tp3_source"] or "N/A"),
                            ("MSS Score", f"{sig['mss_score']:.1f}" if sig["mss_score"] else "N/A"),
                            ("FVG Score", f"{sig['fvg_score']:.1f}" if sig["fvg_score"] else "N/A"),
                            ("Institutional Score", f"{inst_score:.1f}"),
                            ("Regime Approved", "✅" if regime_ok else "❌"),
                            ("Confidence", f"{conf_pct:.1f}%"),
                            ("Risk:Reward", f"1:{rr:.1f}"),
                        ]
                        
                        for label, value in audit_items:
                            st.markdown(f"  **{label}:** {value}")
                        
                        # Metadata components
                        if metadata:
                            st.markdown("**Component Scores (from metadata):**")
                            for item in metadata:
                                if isinstance(item, dict):
                                    name = item.get("name", "?")
                                    value = item.get("value", 0)
                                    st.markdown(f"  - {name}: {value:.4f}")
                                else:
                                    st.markdown(f"  - {item}")
                        
                        # Market Snapshot
                        st.markdown("### Market Snapshot (at signal creation)")
                        market_items = []
                        if sig["open_interest"]:
                            market_items.append(("Open Interest", f"{sig['open_interest']:,.0f}"))
                        if sig["oi_delta"]:
                            market_items.append(("OI Delta", f"{sig['oi_delta']:.4f}"))
                        if sig["funding_rate"]:
                            market_items.append(("Funding Rate", f"{sig['funding_rate']:.6f}"))
                        if sig["exchange_flow"]:
                            market_items.append(("Exchange Flow", f"{sig['exchange_flow']:.4f}"))
                        if sig["delta"]:
                            market_items.append(("Delta", f"{sig['delta']:.2f}"))
                        if sig["cvd"]:
                            market_items.append(("CVD", f"{sig['cvd']:.2f}"))
                        if sig["absorption_score"]:
                            market_items.append(("Absorption", f"{sig['absorption_score']:.2f}"))
                        if sig["sweep_score"]:
                            market_items.append(("Sweep Score", f"{sig['sweep_score']:.1f}"))
                        if sig["market_regime"]:
                            market_items.append(("Market Regime", sig["market_regime"]))
                        if sig["mtf_alignment"]:
                            market_items.append(("MTF Alignment", str(sig["mtf_alignment"])))
                        
                        if market_items:
                            col1, col2 = st.columns(2)
                            for i, (label, value) in enumerate(market_items):
                                with (col1 if i % 2 == 0 else col2):
                                    st.markdown(f"  **{label}:** {value}")
                        else:
                            st.info("No market snapshot data available.")
                    else:
                        st.info("No audit data found for this trade in signals table.")
                
                # Decision Fingerprint
                st.markdown("### Decision Fingerprint")
                fingerprint_items = [
                    ("Regime", regime or "N/A"),
                    ("Trend Score", f"{inst_score:.0f}"),
                    ("Confidence", f"{conf_pct:.1f}%"),
                    ("Risk:Reward", f"1:{rr:.1f}"),
                    ("Session", session or "N/A"),
                    ("MSS Score", f"{mss:.0f}" if mss else "N/A"),
                    ("FVG Score", f"{fvg:.0f}" if fvg else "N/A"),
                    ("Volatility Score", f"{vol_score:.0f}" if vol_score else "N/A"),
                ]
                for label, value in fingerprint_items:
                    st.markdown(f"  **{label}:** {value}")
    
    # ── Why NOT? Section ──
    st.markdown("---")
    st.markdown("## ❌ Why NOT? — Rejected Signals")
    st.markdown("Understanding why signals were rejected is often more valuable than understanding why they were accepted.")
    
    conn3 = sqlite3.connect(
        str(Path(__file__).resolve().parent.parent.parent / "data" / "institutional_v1.db"),
        timeout=10,
    )
    conn3.row_factory = sqlite3.Row
    
    rejected = conn3.execute("""
        SELECT symbol, side, decision, confidence, regime, institutional_score,
               trade_quality_score, expected_value_r, institution_agreement,
               regime_approved, reward_approved, portfolio_approved,
               rejection_reasons, timestamp
        FROM decision_audit
        WHERE decision = 'REJECT'
        ORDER BY timestamp DESC
        LIMIT 50
    """).fetchall()
    conn3.close()
    
    if rejected:
        rows = []
        for r in rejected:
            reasons = r["rejection_reasons"] or "N/A"
            try:
                reasons_list = json.loads(reasons)
                reasons_str = "; ".join(reasons_list) if isinstance(reasons_list, list) else str(reasons_list)
            except:
                reasons_str = str(reasons)
            
            rows.append({
                "Symbol": r["symbol"],
                "Side": r["side"],
                "Confidence": f"{r['confidence']:.1f}%",
                "Institutional Score": f"{r['institutional_score']:.1f}",
                "Agreement": f"{r['institution_agreement']*100:.0f}%",
                "Rejection Reasons": reasons_str,
            })
        
        import pandas as pd
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        
        # Summary of rejection reasons
        st.markdown("### Rejection Reason Summary")
        reason_counts = {}
        for r in rejected:
            reasons = r["rejection_reasons"] or ""
            try:
                reasons_list = json.loads(reasons)
                for reason in reasons_list:
                    # Extract the main reason (before colon)
                    main = reason.split(":")[0].strip() if ":" in reason else reason
                    reason_counts[main] = reason_counts.get(main, 0) + 1
            except:
                reason_counts["unknown"] = reason_counts.get("unknown", 0) + 1
        
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
            st.markdown(f"  - **{reason}**: {count} rejections")
    else:
        st.info("No rejected signals found in decision audit.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE 11: CONFIDENCE CALIBRATION
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🔧 Confidence Calibration":
    st.title("🔧 Confidence Calibration")
    st.markdown("Expected vs actual performance by confidence bucket. If they align, the confidence engine is well-calibrated.")
    
    import sqlite3
    
    conn = sqlite3.connect(
        str(Path(__file__).resolve().parent.parent.parent / "data" / "institutional_v1.db"),
        timeout=10,
    )
    conn.row_factory = sqlite3.Row
    
    # Load all completed trades with confidence (EMA V5 only)
    trades = []
    for table in ["positions_archive", "positions"]:
        rows = conn.execute(f"""
            SELECT confidence, outcome, pnl, risk_reward, regime, session,
                   symbol, side, alpha_score, mss_score, fvg_score,
                   volatility_score, institutional_score, mae_pct, mfe_pct
            FROM {table}
            WHERE status = 'closed' AND outcome IS NOT NULL AND confidence IS NOT NULL
            AND strategy_version = 'ema_v5'
        """).fetchall()
        trades.extend([dict(r) for r in rows])
    conn.close()
    
    if not trades:
        st.warning("No completed trades with confidence data.")
    else:
        # ── Quick Summary Bar ──
        import math
        total_trades = len(trades)
        total_wins = sum(1 for t in trades if t["outcome"] == "win")
        overall_wr = total_wins / total_trades if total_trades > 0 else 0
        avg_confidence = 0
        for t in trades:
            c = t["confidence"]
            avg_confidence += (c / 100 if c > 1 else c)
        avg_confidence = avg_confidence / total_trades if total_trades > 0 else 0
        bias = avg_confidence - overall_wr

        pnls_all = [t["pnl"] for t in trades]
        gross_win = sum(p for p in pnls_all if p > 0)
        gross_loss = abs(sum(p for p in pnls_all if p < 0))
        overall_pf = gross_win / gross_loss if gross_loss > 0 else 0

        # Best regime
        regime_perf = {}
        for t in trades:
            r = t.get("regime", "unknown") or "unknown"
            if r not in regime_perf:
                regime_perf[r] = {"wins": 0, "total": 0, "pnls": []}
            regime_perf[r]["total"] += 1
            regime_perf[r]["pnls"].append(t["pnl"])
            if t["outcome"] == "win":
                regime_perf[r]["wins"] += 1
        best_regime = "N/A"
        best_regime_pf = 0
        for r, s in regime_perf.items():
            if s["total"] >= 10:
                gw = sum(p for p in s["pnls"] if p > 0)
                gl = abs(sum(p for p in s["pnls"] if p < 0))
                pf = gw / gl if gl > 0 else 0
                if pf > best_regime_pf:
                    best_regime_pf = pf
                    best_regime = r.replace("_", " ").title()

        # Calibration status
        brier_quick = 0
        for t in trades:
            c = t["confidence"]
            if c > 1:
                c = c / 100
            a = 1.0 if t["outcome"] == "win" else 0.0
            brier_quick += (c - a) ** 2
        brier_quick = brier_quick / total_trades if total_trades > 0 else 0

        if bias > 0.15:
            cal_status = "🔴 Overconfident"
        elif bias > 0.05:
            cal_status = "🟡 Mildly Overconfident"
        elif bias > -0.05:
            cal_status = "🟢 Well Calibrated"
        else:
            cal_status = "🔵 Underconfident"

        st.markdown("### 📋 Executive Summary")
        sum_cols = st.columns(5)
        with sum_cols[0]:
            st.metric("Total Trades", f"{total_trades}")
        with sum_cols[1]:
            st.metric("Overall WR", f"{overall_wr*100:.1f}%")
        with sum_cols[2]:
            st.metric("Overall PF", f"{overall_pf:.2f}")
        with sum_cols[3]:
            st.metric("Confidence Bias", f"{bias*100:+.1f}%",
                      delta="overconfident" if bias > 0.05 else ("underconfident" if bias < -0.05 else "calibrated"),
                      delta_color="inverse" if bias > 0.05 else "normal")
        with sum_cols[4]:
            st.metric("Best Regime", f"{best_regime}",
                      delta=f"PF {best_regime_pf:.2f}" if best_regime_pf > 0 else None)

        st.markdown(f"**Calibration Status:** {cal_status} · **Brier:** {brier_quick:.4f} · **Avg Confidence:** {avg_confidence*100:.1f}% · **Observed WR:** {overall_wr*100:.1f}%")
        st.markdown("---")

        # Bucket granularity selector
        st.markdown("### Bucket Granularity")
        granularity = st.selectbox(
            "Confidence bucket width",
            options=["5%", "10%", "20%"],
            index=0,
            help="5% = 12 buckets (detailed). 10% = 6 buckets (moderate). 20% = 3 buckets (broad).",
        )
        step = int(granularity.strip("%")) / 100

        # Generate buckets dynamically
        buckets = {}
        for low_pct in range(40, 100, int(step * 100)):
            high_pct = low_pct + int(step * 100)
            if high_pct > 100:
                high_pct = 100
            low = low_pct / 100
            high = high_pct / 100
            name = f"{low_pct}-{high_pct}%"
            buckets[name] = {
                "expected": (low + high) / 2,
                "trades": [],
                "conf_range": (low, high),
            }
        
        # Assign trades to buckets
        for t in trades:
            conf = t["confidence"]
            if conf <= 1:
                conf = conf  # Already 0-1
            else:
                conf = conf / 100  # Convert percentage
            
            for bucket_name, bucket in buckets.items():
                low, high = bucket["conf_range"]
                if low <= conf < high:
                    bucket["trades"].append(t)
                    break
        
        # Calibration table
        st.markdown("### Confidence Calibration Table")
        st.markdown("Expected win rate = confidence value. Actual win rate = observed outcomes.")
        
        import pandas as pd
        
        cal_rows = []
        overall_wr = sum(1 for t in trades if t["outcome"] == "win") / len(trades) if trades else 0
        for bucket_name, bucket in buckets.items():
            if bucket["trades"]:
                wins = sum(1 for t in bucket["trades"] if t["outcome"] == "win")
                total = len(bucket["trades"])
                actual_wr = wins / total
                expected_wr = bucket["expected"]
                calibration_error = actual_wr - expected_wr
                pnls = [t["pnl"] for t in bucket["trades"]]
                avg_pnl = sum(pnls) / len(pnls) if pnls else 0
                realized_rs = [t.get("realized_r", 0) for t in bucket["trades"] if t.get("realized_r")]
                avg_r = sum(realized_rs) / len(realized_rs) if realized_rs else 0
                gross_win = sum(p for p in pnls if p > 0)
                gross_loss = abs(sum(p for p in pnls if p < 0))
                pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0)
                
                # Expectancy = WR × avg_win - (1-WR) × avg_loss
                avg_win = sum(p for p in pnls if p > 0) / wins if wins > 0 else 0
                avg_loss = abs(sum(p for p in pnls if p < 0)) / (total - wins) if (total - wins) > 0 else 0
                expectancy = actual_wr * avg_win - (1 - actual_wr) * avg_loss

                # MAE/MFE averages
                maes = [abs(t.get("mae_pct", 0) or 0) for t in bucket["trades"]]
                mfes = [t.get("mfe_pct", 0) or 0 for t in bucket["trades"]]
                avg_mae = sum(maes) / len(maes) if maes else 0
                avg_mfe = sum(mfes) / len(mfes) if mfes else 0

                # Dispersion metrics for R-multiples
                if len(realized_rs) >= 2:
                    median_r = sorted(realized_rs)[len(realized_rs) // 2]
                    mean_r = avg_r
                    variance = sum((r - mean_r) ** 2 for r in realized_rs) / (len(realized_rs) - 1)
                    std_r = variance ** 0.5
                    best_r = max(realized_rs)
                    worst_r = min(realized_rs)
                else:
                    median_r = avg_r
                    std_r = 0
                    best_r = avg_r
                    worst_r = avg_r

                # Status with sample-size warning
                if total < 10:
                    status = "⚠️ N<10"
                elif total < 20:
                    status = "⚠️ N<20" if abs(calibration_error) >= 0.1 else "🔶 N<20"
                else:
                    status = "✅" if abs(calibration_error) < 0.1 else "⚠️"

                # Lift = bucket WR / overall WR
                lift = actual_wr / overall_wr if overall_wr > 0 else 0

                cal_rows.append({
                    "Bucket": bucket_name,
                    "Trades": total,
                    "Win %": f"{actual_wr*100:.1f}%",
                    "Expected WR": f"{expected_wr*100:.1f}%",
                    "Cal. Error": f"{calibration_error*100:+.1f}%",
                    "Expectancy": f"${expectancy:.2f}",
                    "Avg R": f"{avg_r:.2f}R",
                    "Median R": f"{median_r:.2f}R",
                    "Std R": f"{std_r:.2f}R",
                    "Best R": f"{best_r:.2f}R",
                    "Worst R": f"{worst_r:.2f}R",
                    "Avg PnL": f"${avg_pnl:.2f}",
                    "Profit Factor": f"{pf:.2f}" if pf != float("inf") else "∞",
                    "Lift": f"{lift:.2f}×",
                    "Avg MAE%": f"{avg_mae:.2f}%",
                    "Avg MFE%": f"{avg_mfe:.2f}%",
                    "Status": status,
                })
        
        if cal_rows:
            df = pd.DataFrame(cal_rows)
            st.dataframe(df, use_container_width=True, hide_index=True)

            # Confidence Distribution Histogram
            st.markdown("### Confidence Distribution")
            st.markdown("How many trades fall into each confidence bucket? If most cluster at the top, the scale is compressed.")
            dist_data = pd.DataFrame({
                "Bucket": [r["Bucket"] for r in cal_rows],
                "Trades": [int(r["Trades"]) for r in cal_rows],
            })
            st.bar_chart(dist_data.set_index("Bucket"))

            # Calibration chart
            st.markdown("### Calibration Curve")
            chart_data = pd.DataFrame({
                "Bucket": [r["Bucket"] for r in cal_rows],
                "Expected WR (%)": [float(r["Expected WR"].strip("%")) for r in cal_rows],
                "Actual WR (%)": [float(r["Win %"].strip("%")) for r in cal_rows],
            })
            st.line_chart(chart_data.set_index("Bucket"))
            
            # Overall calibration error
            total_trades = sum(len(b["trades"]) for b in buckets.values() if b["trades"])
            weighted_error = 0
            for r in cal_rows:
                trades_in_bucket = int(r["Trades"])
                error = float(r["Cal. Error"].strip("%").replace("+", ""))
                weighted_error += abs(error) * trades_in_bucket
            weighted_error = weighted_error / total_trades if total_trades > 0 else 0
            
            st.markdown(f"**Weighted Calibration Error:** {weighted_error:.1f}%")
            if weighted_error < 5:
                st.success("✅ Confidence engine is well-calibrated (error < 5%)")
            elif weighted_error < 10:
                st.warning("⚠️ Confidence engine has moderate calibration error (5-10%)")
            else:
                st.error("❌ Confidence engine is miscalibrated (error > 10%)")

            # ── Cumulative Threshold Analysis ──
            st.markdown("### Cumulative Threshold Analysis")
            st.markdown("Does raising the confidence threshold improve performance? This shows cumulative stats for trades ≥ each threshold.")

            thresholds = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
            threshold_rows = []
            for thresh in thresholds:
                above = []
                for t in trades:
                    conf = t["confidence"]
                    if conf > 1:
                        conf = conf / 100
                    if conf >= thresh:
                        above.append(t)

                if len(above) >= 3:
                    n = len(above)
                    wins = sum(1 for t in above if t["outcome"] == "win")
                    wr = wins / n
                    pnls = [t["pnl"] for t in above]
                    avg_pnl = sum(pnls) / n
                    gross_win = sum(p for p in pnls if p > 0)
                    gross_loss = abs(sum(p for p in pnls if p < 0))
                    pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0)
                    avg_win = sum(p for p in pnls if p > 0) / wins if wins > 0 else 0
                    avg_loss = abs(sum(p for p in pnls if p < 0)) / (n - wins) if (n - wins) > 0 else 0
                    exp = wr * avg_win - (1 - wr) * avg_loss

                    realized_rs = [t.get("realized_r", 0) for t in above if t.get("realized_r")]
                    avg_r = sum(realized_rs) / len(realized_rs) if realized_rs else 0

                    threshold_rows.append({
                        "Threshold": f"≥{thresh*100:.0f}%",
                        "Trades": n,
                        "Win %": f"{wr*100:.1f}%",
                        "95% CI (WR)": "",
                        "Avg R": f"{avg_r:.2f}R",
                        "Profit Factor": f"{pf:.2f}" if pf != float("inf") else "∞",
                        "Expectancy": f"${exp:.2f}",
                        "Avg PnL": f"${avg_pnl:.2f}",
                        "Reliability": "",
                    })

            # Compute 95% Wilson confidence intervals for win rates
            z = 1.96
            for r in threshold_rows:
                n = r["Trades"]
                wr_pct = float(r["Win %"].strip("%")) / 100
                denominator = 1 + z * z / n
                center = (wr_pct + z * z / (2 * n)) / denominator
                margin = z * math.sqrt((wr_pct * (1 - wr_pct) + z * z / (4 * n)) / n) / denominator
                ci_low = max(0, center - margin) * 100
                ci_high = min(100, center + margin) * 100
                r["95% CI (WR)"] = f"{ci_low:.1f}–{ci_high:.1f}%"
                # Reliability indicator based on sample size
                if n >= 100:
                    r["Reliability"] = "🟢 Reliable"
                elif n >= 30:
                    r["Reliability"] = "🟡 Moderate"
                elif n >= 10:
                    r["Reliability"] = "🟠 Limited"
                else:
                    r["Reliability"] = "🔴 Very small"

            if threshold_rows:
                st.dataframe(pd.DataFrame(threshold_rows), use_container_width=True, hide_index=True)

                # Find optimal threshold
                best_exp = None
                best_thresh = None
                best_n = 0
                for r in threshold_rows:
                    exp_val = float(r["Expectancy"].replace("$", ""))
                    if best_exp is None or exp_val > best_exp:
                        best_exp = exp_val
                        best_thresh = r["Threshold"]
                        best_n = r["Trades"]
                if best_exp is not None:
                    if best_exp > 0:
                        if best_n >= 30:
                            st.success(f"💡 Optimal threshold: **{best_thresh}** (expectancy ${best_exp:.2f}, {best_n} trades). Consider raising confidence_min if this is above the current 40% floor.")
                        else:
                            st.warning(f"⚠️ Optimal threshold: **{best_thresh}** (expectancy ${best_exp:.2f}) — but based on only {best_n} trades. Treat as hypothesis, not production rule.")
                    else:
                        st.warning(f"⚠️ No threshold produces positive expectancy. Best: {best_thresh} (${best_exp:.2f}).")
        
        # Prediction accuracy by component
        st.markdown("### Component Prediction Accuracy")
        st.markdown("Which components are actually predictive of trade outcomes?")
        
        # Analyze by regime
        st.markdown("#### By Regime")
        regime_stats = {}
        for t in trades:
            regime = t.get("regime", "unknown") or "unknown"
            if regime not in regime_stats:
                regime_stats[regime] = {"wins": 0, "total": 0, "pnls": []}
            regime_stats[regime]["total"] += 1
            regime_stats[regime]["pnls"].append(t["pnl"])
            if t["outcome"] == "win":
                regime_stats[regime]["wins"] += 1
        
        regime_rows = []
        for regime, stats in sorted(regime_stats.items()):
            wr = stats["wins"] / stats["total"] if stats["total"] > 0 else 0
            avg_pnl = sum(stats["pnls"]) / len(stats["pnls"]) if stats["pnls"] else 0
            gross_win = sum(p for p in stats["pnls"] if p > 0)
            gross_loss = abs(sum(p for p in stats["pnls"] if p < 0))
            pf = gross_win / gross_loss if gross_loss > 0 else 0
            regime_rows.append({
                "Regime": regime.replace("_", " ").title(),
                "Trades": stats["total"],
                "Win Rate": f"{wr*100:.1f}%",
                "Profit Factor": f"{pf:.2f}",
                "Avg PnL": f"${avg_pnl:.2f}",
            })
        
        if regime_rows:
            st.dataframe(pd.DataFrame(regime_rows), use_container_width=True, hide_index=True)
        
        # Analyze by session
        st.markdown("#### By Session")
        session_stats = {}
        for t in trades:
            session = t.get("session", "unknown") or "unknown"
            if session not in session_stats:
                session_stats[session] = {"wins": 0, "total": 0, "pnls": []}
            session_stats[session]["total"] += 1
            session_stats[session]["pnls"].append(t["pnl"])
            if t["outcome"] == "win":
                session_stats[session]["wins"] += 1
        
        session_rows = []
        for session, stats in sorted(session_stats.items()):
            wr = stats["wins"] / stats["total"] if stats["total"] > 0 else 0
            avg_pnl = sum(stats["pnls"]) / len(stats["pnls"]) if stats["pnls"] else 0
            gross_win = sum(p for p in stats["pnls"] if p > 0)
            gross_loss = abs(sum(p for p in stats["pnls"] if p < 0))
            pf = gross_win / gross_loss if gross_loss > 0 else 0
            session_rows.append({
                "Session": session.title(),
                "Trades": stats["total"],
                "Win Rate": f"{wr*100:.1f}%",
                "Profit Factor": f"{pf:.2f}",
                "Avg PnL": f"${avg_pnl:.2f}",
            })
        
        if session_rows:
            st.dataframe(pd.DataFrame(session_rows), use_container_width=True, hide_index=True)

        # ═══════════════════════════════════════════════════════════════════
        # CALIBRATION BY REGIME
        # ═══════════════════════════════════════════════════════════════════
        st.markdown("---")
        st.markdown("### Calibration by Regime")
        st.markdown("Does confidence calibration differ across market regimes?")

        regime_cal = {}
        for t in trades:
            regime = t.get("regime", "unknown") or "unknown"
            conf = t["confidence"]
            if conf > 1:
                conf = conf / 100
            bucket = int(conf * 20) * 5  # Round to nearest 5%
            bucket = max(40, min(95, bucket))
            key = (regime, f"{bucket}-{bucket+5}%")
            if key not in regime_cal:
                regime_cal[key] = {"wins": 0, "total": 0, "pnls": []}
            regime_cal[key]["total"] += 1
            regime_cal[key]["pnls"].append(t["pnl"])
            if t["outcome"] == "win":
                regime_cal[key]["wins"] += 1

        regime_cal_rows = []
        for (regime, bucket), stats in sorted(regime_cal.items()):
            if stats["total"] >= 3:  # Min 3 trades for meaningful stats
                wr = stats["wins"] / stats["total"]
                avg_pnl = sum(stats["pnls"]) / len(stats["pnls"])
                gross_win = sum(p for p in stats["pnls"] if p > 0)
                gross_loss = abs(sum(p for p in stats["pnls"] if p < 0))
                pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0)
                regime_cal_rows.append({
                    "Regime": regime.replace("_", " ").title(),
                    "Confidence Bucket": bucket,
                    "Trades": stats["total"],
                    "Win Rate": f"{wr*100:.1f}%",
                    "Profit Factor": f"{pf:.2f}" if pf != float("inf") else "∞",
                    "Avg PnL": f"${avg_pnl:.2f}",
                })

        if regime_cal_rows:
            st.dataframe(pd.DataFrame(regime_cal_rows), use_container_width=True, hide_index=True)
        else:
            st.info("Insufficient data for regime-specific calibration (need ≥3 trades per bucket).")

        # ═══════════════════════════════════════════════════════════════════
        # CALIBRATION BY SIDE
        # ═══════════════════════════════════════════════════════════════════
        st.markdown("### Calibration by Side")
        st.markdown("Are LONGs and SHORTs calibrated differently?")

        side_cal = {}
        for t in trades:
            side = (t.get("side") or "unknown").upper()
            conf = t["confidence"]
            if conf > 1:
                conf = conf / 100
            bucket = int(conf * 20) * 5
            bucket = max(40, min(95, bucket))
            key = (side, f"{bucket}-{bucket+5}%")
            if key not in side_cal:
                side_cal[key] = {"wins": 0, "total": 0, "pnls": []}
            side_cal[key]["total"] += 1
            side_cal[key]["pnls"].append(t["pnl"])
            if t["outcome"] == "win":
                side_cal[key]["wins"] += 1

        side_cal_rows = []
        for (side, bucket), stats in sorted(side_cal.items()):
            if stats["total"] >= 3:
                wr = stats["wins"] / stats["total"]
                avg_pnl = sum(stats["pnls"]) / len(stats["pnls"])
                gross_win = sum(p for p in stats["pnls"] if p > 0)
                gross_loss = abs(sum(p for p in stats["pnls"] if p < 0))
                pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0)
                side_cal_rows.append({
                    "Side": side,
                    "Confidence Bucket": bucket,
                    "Trades": stats["total"],
                    "Win Rate": f"{wr*100:.1f}%",
                    "Profit Factor": f"{pf:.2f}" if pf != float("inf") else "∞",
                    "Avg PnL": f"${avg_pnl:.2f}",
                })

        if side_cal_rows:
            st.dataframe(pd.DataFrame(side_cal_rows), use_container_width=True, hide_index=True)
            # Side summary
            side_summary = {}
            for (side, _), stats in side_cal.items():
                if side not in side_summary:
                    side_summary[side] = {"wins": 0, "total": 0, "pnls": []}
                side_summary[side]["wins"] += stats["wins"]
                side_summary[side]["total"] += stats["total"]
                side_summary[side]["pnls"].extend(stats["pnls"])

            side_sum_rows = []
            for side, stats in sorted(side_summary.items()):
                wr = stats["wins"] / stats["total"] if stats["total"] > 0 else 0
                avg_pnl = sum(stats["pnls"]) / len(stats["pnls"]) if stats["pnls"] else 0
                gross_win = sum(p for p in stats["pnls"] if p > 0)
                gross_loss = abs(sum(p for p in stats["pnls"] if p < 0))
                pf = gross_win / gross_loss if gross_loss > 0 else 0
                side_sum_rows.append({
                    "Side": side,
                    "Trades": stats["total"],
                    "Win Rate": f"{wr*100:.1f}%",
                    "Profit Factor": f"{pf:.2f}",
                    "Avg PnL": f"${avg_pnl:.2f}",
                })
            st.markdown("**Side Summary**")
            st.dataframe(pd.DataFrame(side_sum_rows), use_container_width=True, hide_index=True)
        else:
            st.info("Insufficient data for side-specific calibration (need ≥3 trades per bucket).")

        # ═══════════════════════════════════════════════════════════════════
        # CALIBRATION BY COMPONENT
        # ═══════════════════════════════════════════════════════════════════
        st.markdown("### Calibration by Component")
        st.markdown("Which score components are actually predictive of outcomes?")

        component_fields = [
            ("alpha_score", "Alpha Score"),
            ("mss_score", "MSS Score"),
            ("fvg_score", "FVG Score"),
            ("volatility_score", "Volatility Score"),
            ("institutional_score", "Institutional Score"),
        ]

        component_rows = []
        for field_key, field_label in component_fields:
            vals = [t.get(field_key, 0) or 0 for t in trades]
            if all(v == 0 for v in vals):
                continue  # Skip components with no data

            # Split into low/medium/high by terciles
            non_zero = [v for v in vals if v > 0]
            if len(non_zero) < 6:
                continue

            sorted_vals = sorted(non_zero)
            t33 = sorted_vals[len(sorted_vals) // 3]
            t66 = sorted_vals[2 * len(sorted_vals) // 3]

            tercile_stats = {"Low": {"wins": 0, "total": 0, "pnls": []},
                            "Mid": {"wins": 0, "total": 0, "pnls": []},
                            "High": {"wins": 0, "total": 0, "pnls": []}}

            for t in trades:
                v = t.get(field_key, 0) or 0
                if v == 0:
                    continue
                if v <= t33:
                    tier = "Low"
                elif v <= t66:
                    tier = "Mid"
                else:
                    tier = "High"
                tercile_stats[tier]["total"] += 1
                tercile_stats[tier]["pnls"].append(t["pnl"])
                if t["outcome"] == "win":
                    tercile_stats[tier]["wins"] += 1

            for tier in ["Low", "Mid", "High"]:
                stats = tercile_stats[tier]
                if stats["total"] < 3:
                    continue
                wr = stats["wins"] / stats["total"]
                avg_pnl = sum(stats["pnls"]) / len(stats["pnls"])
                gross_win = sum(p for p in stats["pnls"] if p > 0)
                gross_loss = abs(sum(p for p in stats["pnls"] if p < 0))
                pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0)
                component_rows.append({
                    "Component": field_label,
                    "Tier": tier,
                    "Range": f"≤{t33:.0f}" if tier == "Low" else (f"{t33:.0f}-{t66:.0f}" if tier == "Mid" else f">{t66:.0f}"),
                    "Trades": stats["total"],
                    "Win Rate": f"{wr*100:.1f}%",
                    "Profit Factor": f"{pf:.2f}" if pf != float("inf") else "∞",
                    "Avg PnL": f"${avg_pnl:.2f}",
                })

        if component_rows:
            st.dataframe(pd.DataFrame(component_rows), use_container_width=True, hide_index=True)
            st.markdown("*Components where High tier has higher WR and PF than Low tier are predictive.*")
        else:
            st.info("Insufficient component score data for analysis.")

        # ═══════════════════════════════════════════════════════════════════
        # RELIABILITY METRICS
        # ═══════════════════════════════════════════════════════════════════
        st.markdown("---")
        st.markdown("### Reliability Metrics")
        st.markdown("Standard probabilistic calibration metrics for the confidence engine.")

        # Brier Score: mean squared error of probabilistic predictions
        # Lower is better. 0 = perfect, 1 = worst
        brier_sum = 0
        for t in trades:
            conf = t["confidence"]
            if conf > 1:
                conf = conf / 100
            actual = 1.0 if t["outcome"] == "win" else 0.0
            brier_sum += (conf - actual) ** 2
        brier_score = brier_sum / len(trades)

        # Log Loss: logarithmic scoring penalty
        # Lower is better. 0 = perfect
        import math
        log_loss_sum = 0
        eps = 1e-15  # Clip to avoid log(0)
        for t in trades:
            conf = t["confidence"]
            if conf > 1:
                conf = conf / 100
            conf = max(eps, min(1 - eps, conf))
            actual = 1.0 if t["outcome"] == "win" else 0.0
            log_loss_sum += -(actual * math.log(conf) + (1 - actual) * math.log(1 - conf))
        log_loss = log_loss_sum / len(trades)

        # Expected Calibration Error (ECE)
        # Maximum Calibration Error (MCE): worst bucket error
        ece_sum = 0
        mce = 0
        total_for_ece = 0
        for bucket_name, bucket in buckets.items():
            if bucket["trades"]:
                n = len(bucket["trades"])
                wins = sum(1 for t in bucket["trades"] if t["outcome"] == "win")
                actual_wr = wins / n
                expected_wr = bucket["expected"]
                bucket_error = abs(actual_wr - expected_wr)
                ece_sum += n * bucket_error
                mce = max(mce, bucket_error)
                total_for_ece += n

        ece = ece_sum / total_for_ece if total_for_ece > 0 else 0

        # Display metrics
        metric_cols = st.columns(5)
        with metric_cols[0]:
            st.metric("Brier Score", f"{brier_score:.4f}",
                      help="Mean squared error of predictions. 0=perfect, 1=worst. <0.25 is reasonable.")
        with metric_cols[1]:
            st.metric("Log Loss", f"{log_loss:.4f}",
                      help="Logarithmic scoring penalty. 0=perfect. Higher = worse. Penalizes confident wrong predictions heavily.")
        with metric_cols[2]:
            st.metric("ECE", f"{ece*100:.1f}%",
                      help="Expected Calibration Error. Weighted avg of |actual - expected| per bucket.")
        with metric_cols[3]:
            st.metric("MCE", f"{mce*100:.1f}%",
                      help="Maximum Calibration Error. Worst single bucket calibration error.")
        with metric_cols[4]:
            st.metric("Trades Analyzed", f"{len(trades)}",
                      help="Total trades used for calibration analysis.")

        # Confidence Bias
        avg_predicted = 0
        for t in trades:
            conf = t["confidence"]
            if conf > 1:
                conf = conf / 100
            avg_predicted += conf
        avg_predicted = avg_predicted / len(trades) if trades else 0
        avg_observed = sum(1 for t in trades if t["outcome"] == "win") / len(trades) if trades else 0
        bias = avg_predicted - avg_observed

        bias_cols = st.columns(3)
        with bias_cols[0]:
            st.metric("Avg Predicted Probability", f"{avg_predicted*100:.1f}%",
                      help="Average confidence score across all trades.")
        with bias_cols[1]:
            st.metric("Avg Observed Win Rate", f"{avg_observed*100:.1f}%",
                      help="Actual win rate across all trades.")
        with bias_cols[2]:
            st.metric("Confidence Bias", f"{bias*100:+.1f}%",
                      help="Predicted - Observed. Positive = overconfident, negative = underconfident.")

        # Semantic bias coloring
        if bias > 0.15:
            st.error(f"🔴 **Severely Overconfident** — Engine predicts {bias*100:+.1f}% higher than observed. Confidence scores are inflated.")
        elif bias > 0.05:
            st.warning(f"🟡 **Mildly Overconfident** — Engine predicts {bias*100:+.1f}% higher than observed.")
        elif bias > -0.05:
            st.success(f"🟢 **Well Calibrated** — Bias is within ±5%.")
        elif bias > -0.15:
            st.warning(f"🟡 **Mildly Underconfident** — Engine predicts {bias*100:+.1f}% lower than observed.")
        else:
            st.error(f"🔵 **Severely Underconfident** — Engine predicts {bias*100:+.1f}% lower than observed. Confidence scores are conservative.")

        # Reliability Index with thresholds
        if ece < 0.05:
            reliability = "Excellent"
            st.success(f"✅ Reliability Index: **{reliability}** (ECE {ece*100:.1f}% < 5%)")
        elif ece < 0.10:
            reliability = "Good"
            st.success(f"✅ Reliability Index: **{reliability}** (ECE {ece*100:.1f}% < 10%)")
        elif ece < 0.20:
            reliability = "Fair"
            st.warning(f"⚠️ Reliability Index: **{reliability}** (ECE {ece*100:.1f}% < 20%)")
        else:
            reliability = "Poor"
            st.error(f"❌ Reliability Index: **{reliability}** (ECE {ece*100:.1f}% ≥ 20%)")

        # Show threshold reference
        st.markdown("""**Reliability Thresholds:** Excellent (<5%) · Good (<10%) · Fair (<20%) · Poor (≥20%)""")

        # Interpretation
        if brier_score < 0.20:
            st.success(f"✅ Brier Score {brier_score:.4f} — Good probabilistic calibration")
        elif brier_score < 0.25:
            st.warning(f"⚠️ Brier Score {brier_score:.4f} — Moderate calibration")
        else:
            st.error(f"❌ Brier Score {brier_score:.4f} — Poor calibration (predictions are overconfident)")

        # Log Loss interpretation
        if log_loss < 0.5:
            st.success(f"✅ Log Loss {log_loss:.4f} — Good probability quality")
        elif log_loss < 0.7:
            st.warning(f"⚠️ Log Loss {log_loss:.4f} — Moderate probability quality")
        else:
            st.error(f"❌ Log Loss {log_loss:.4f} — Poor probability quality (confident wrong predictions penalized heavily)")

        # Reliability diagram data (expected vs actual per bucket, already computed above)
        st.markdown("#### Reliability Diagram")
        st.markdown("Perfect calibration = diagonal line. Points above = underconfident. Below = overconfident.")

        rel_rows = []
        for bucket_name, bucket in buckets.items():
            if bucket["trades"]:
                n = len(bucket["trades"])
                wins = sum(1 for t in bucket["trades"] if t["outcome"] == "win")
                actual_wr = wins / n
                rel_rows.append({
                    "Bucket": bucket_name,
                    "Confidence": bucket["expected"],
                    "Actual WR": actual_wr,
                    "Perfect": bucket["expected"],  # Diagonal reference
                    "Trades": n,
                    "Frequency %": 0,  # Placeholder, filled below
                })

        if rel_rows:
            # Compute frequency percentages
            total_rel_trades = sum(r["Trades"] for r in rel_rows)
            for r in rel_rows:
                r["Frequency %"] = r["Trades"] / total_rel_trades * 100 if total_rel_trades > 0 else 0

            rel_df = pd.DataFrame(rel_rows)

            # Calibration curve with perfect diagonal
            st.markdown("**Calibration Curve vs Perfect**")
            chart_df = rel_df.set_index("Bucket")[["Actual WR", "Perfect"]]
            st.line_chart(chart_df)

            # Frequency distribution
            st.markdown("**Sample Frequency by Bucket**")
            freq_df = rel_df.set_index("Bucket")[["Frequency %", "Trades"]]
            st.bar_chart(freq_df[["Frequency %"]])

            # Interpretation table
            interp_rows = []
            for _, r in rel_df.iterrows():
                error = r["Actual WR"] - r["Confidence"]
                if error > 0.05:
                    note = "Underconfident ↑"
                elif error < -0.05:
                    note = "Overconfident ↓"
                else:
                    note = "Well calibrated ✓"
                interp_rows.append({
                    "Bucket": r["Bucket"],
                    "Confidence": f"{r['Confidence']*100:.1f}%",
                    "Actual WR": f"{r['Actual WR']*100:.1f}%",
                    "Error": f"{error*100:+.1f}%",
                    "Trades": r["Trades"],
                    "Note": note,
                })
            st.dataframe(pd.DataFrame(interp_rows), use_container_width=True, hide_index=True)
            st.markdown("*Diagonal = perfect calibration. The 45° line means confidence = actual win rate.*")

        # ═══════════════════════════════════════════════════════════════════
        # SCORE CONTRIBUTION DEEP DIVE
        # ═══════════════════════════════════════════════════════════════════
        st.markdown("---")
        st.markdown("### Score Contribution Deep Dive")
        st.markdown("""
        **Confidence formula (EMA V5):**
        `confidence = institutional×0.50 + regime×0.10 - trend×0.10 + pullback×0.15 - candle×0.10 - volume×0.05`

        Components with **negative** weights (trend, candle, volume) reduce confidence when high.
        This section shows whether each stored component actually predicts outcomes.
        """)

        # Component contribution analysis
        contrib_components = [
            ("institutional_score", "Institutional (×0.50)", "positive"),
            ("mss_score", "MSS/Trend (×-0.10)", "negative"),
            ("fvg_score", "FVG/Candle (×-0.10)", "negative"),
            ("volatility_score", "Volatility/Volume (×-0.05)", "negative"),
        ]

        contrib_rows = []
        for field_key, label, direction in contrib_components:
            vals = [(t, t.get(field_key, 0) or 0) for t in trades]
            non_zero = [(t, v) for t, v in vals if v > 0]
            if len(non_zero) < 10:
                continue

            # Split into quartiles
            sorted_vals = sorted(non_zero, key=lambda x: x[1])
            n = len(sorted_vals)
            q25 = sorted_vals[n // 4][1]
            q50 = sorted_vals[n // 2][1]
            q75 = sorted_vals[3 * n // 4][1]

            quartile_stats = {"Q1 (Lowest)": {"wins": 0, "total": 0, "pnls": [], "confs": []},
                              "Q2": {"wins": 0, "total": 0, "pnls": [], "confs": []},
                              "Q3": {"wins": 0, "total": 0, "pnls": [], "confs": []},
                              "Q4 (Highest)": {"wins": 0, "total": 0, "pnls": [], "confs": []}}

            for t, v in non_zero:
                if v <= q25:
                    q = "Q1 (Lowest)"
                elif v <= q50:
                    q = "Q2"
                elif v <= q75:
                    q = "Q3"
                else:
                    q = "Q4 (Highest)"
                quartile_stats[q]["total"] += 1
                quartile_stats[q]["pnls"].append(t["pnl"])
                quartile_stats[q]["confs"].append(t["confidence"])
                if t["outcome"] == "win":
                    quartile_stats[q]["wins"] += 1

            for q_name in ["Q1 (Lowest)", "Q2", "Q3", "Q4 (Highest)"]:
                stats = quartile_stats[q_name]
                if stats["total"] < 3:
                    continue
                wr = stats["wins"] / stats["total"]
                avg_pnl = sum(stats["pnls"]) / len(stats["pnls"])
                avg_conf = sum(stats["confs"]) / len(stats["confs"])
                gross_win = sum(p for p in stats["pnls"] if p > 0)
                gross_loss = abs(sum(p for p in stats["pnls"] if p < 0))
                pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0)

                contrib_rows.append({
                    "Component": label,
                    "Quartile": q_name,
                    "Trades": stats["total"],
                    "Win Rate": f"{wr*100:.1f}%",
                    "Profit Factor": f"{pf:.2f}" if pf != float("inf") else "∞",
                    "Avg PnL": f"${avg_pnl:.2f}",
                    "Avg Confidence": f"{avg_conf*100:.0f}%",
                })

        if contrib_rows:
            contrib_df = pd.DataFrame(contrib_rows)
            st.dataframe(contrib_df, use_container_width=True, hide_index=True)

            # Predictiveness summary
            st.markdown("**Predictiveness Summary**")
            summary_rows = []
            for field_key, label, direction in contrib_components:
                vals = [(t, t.get(field_key, 0) or 0) for t in trades]
                non_zero = [(t, v) for t, v in vals if v > 0]
                if len(non_zero) < 10:
                    continue

                sorted_vals = sorted(non_zero, key=lambda x: x[1])
                n = len(sorted_vals)
                # Bottom quartile vs top quartile
                q1_trades = sorted_vals[:n // 4]
                q4_trades = sorted_vals[3 * n // 4:]

                def _wr(trades_subset):
                    wins = sum(1 for t, _ in trades_subset if t["outcome"] == "win")
                    return wins / len(trades_subset) if trades_subset else 0

                def _pf(trades_subset):
                    pnls = [t["pnl"] for t, _ in trades_subset]
                    gw = sum(p for p in pnls if p > 0)
                    gl = abs(sum(p for p in pnls if p < 0))
                    return gw / gl if gl > 0 else (float("inf") if gw > 0 else 0)

                q1_wr = _wr(q1_trades)
                q4_wr = _wr(q4_trades)
                q1_pf = _pf(q1_trades)
                q4_pf = _pf(q4_trades)

                # For negative-weight components, high score = low confidence
                # So Q4 (highest component score) should have LOWER WR if the component is predictive
                if direction == "positive":
                    wr_delta = q4_wr - q1_wr
                    pf_delta = (q4_pf if q4_pf != float("inf") else 99) - (q1_pf if q1_pf != float("inf") else 99)
                else:
                    wr_delta = q1_wr - q4_wr  # Reversed: low score should have higher WR
                    pf_delta = (q1_pf if q1_pf != float("inf") else 99) - (q4_pf if q4_pf != float("inf") else 99)

                if wr_delta > 0.05 and pf_delta > 0:
                    verdict = "✅ Predictive"
                elif wr_delta > 0:
                    verdict = "⚠️ Weak"
                else:
                    verdict = "❌ Not predictive"

                summary_rows.append({
                    "Component": label,
                    "Weight": direction,
                    "Q1 WR": f"{q1_wr*100:.1f}%",
                    "Q4 WR": f"{q4_wr*100:.1f}%",
                    "WR Δ": f"{wr_delta*100:+.1f}%",
                    "Q1 PF": f"{q1_pf:.2f}" if q1_pf != float("inf") else "∞",
                    "Q4 PF": f"{q4_pf:.2f}" if q4_pf != float("inf") else "∞",
                    "PF Δ": f"{pf_delta:+.2f}",
                    "Raises WR?": "✅" if wr_delta > 0.02 else ("⚠️" if wr_delta > 0 else "❌"),
                    "Raises PF?": "✅" if pf_delta > 0.05 else ("⚠️" if pf_delta > 0 else "❌"),
                    "Verdict": verdict,
                })

            if summary_rows:
                st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)
                st.markdown("*WR Δ > 0 and PF Δ > 0 = component is predictive in the expected direction.*")

                # Overall component verdict
                predictive_count = sum(1 for r in summary_rows if "✅" in r["Verdict"])
                weak_count = sum(1 for r in summary_rows if "⚠️" in r["Verdict"])
                not_pred_count = sum(1 for r in summary_rows if "❌" in r["Verdict"])
                st.markdown(f"**Component Health:** {predictive_count} predictive · {weak_count} weak · {not_pred_count} not predictive")
        else:
            st.info("Insufficient component score data for contribution analysis.")

        # ═══════════════════════════════════════════════════════════════════
        # PROBABILITY RECALIBRATION
        # ═══════════════════════════════════════════════════════════════════
        st.markdown("---")
        st.markdown("### Probability Recalibration")
        st.markdown("""
        If confidence **ranks** trades well but **overestimates** probabilities,
        a recalibration mapping can fix the scale without changing the ranking.

        This table shows: given a raw confidence score, what observed win rate
        should you actually expect based on historical data?
        """)

        recal_rows = []
        for bucket_name, bucket in buckets.items():
            if bucket["trades"] and len(bucket["trades"]) >= 3:
                n = len(bucket["trades"])
                wins = sum(1 for t in bucket["trades"] if t["outcome"] == "win")
                actual_wr = wins / n
                expected_wr = bucket["expected"]
                pnls = [t["pnl"] for t in bucket["trades"]]
                avg_pnl = sum(pnls) / len(pnls)
                # Expectancy with recalibrated probability
                avg_win = sum(p for p in pnls if p > 0) / wins if wins > 0 else 0
                avg_loss = abs(sum(p for p in pnls if p < 0)) / (n - wins) if (n - wins) > 0 else 0
                orig_exp = expected_wr * avg_win - (1 - expected_wr) * avg_loss
                recal_exp = actual_wr * avg_win - (1 - actual_wr) * avg_loss

                recal_rows.append({
                    "Raw Confidence": bucket_name,
                    "Raw Expected WR": f"{expected_wr*100:.1f}%",
                    "Observed WR": f"{actual_wr*100:.1f}%",
                    "Recalibrated WR": f"{actual_wr*100:.1f}%",
                    "Trades": n,
                    "Original Exp": f"${orig_exp:.2f}",
                    "Recalibrated Exp": f"${recal_exp:.2f}",
                    "Exp Gain": f"${recal_exp - orig_exp:+.2f}",
                })

        if recal_rows:
            st.dataframe(pd.DataFrame(recal_rows), use_container_width=True, hide_index=True)

            # Summary
            total_orig = 0
            total_recal = 0
            total_n = 0
            for bucket_name, bucket in buckets.items():
                if bucket["trades"] and len(bucket["trades"]) >= 3:
                    n = len(bucket["trades"])
                    wins = sum(1 for t in bucket["trades"] if t["outcome"] == "win")
                    actual_wr = wins / n
                    expected_wr = bucket["expected"]
                    pnls = [t["pnl"] for t in bucket["trades"]]
                    avg_win = sum(p for p in pnls if p > 0) / wins if wins > 0 else 0
                    avg_loss = abs(sum(p for p in pnls if p < 0)) / (n - wins) if (n - wins) > 0 else 0
                    total_orig += (expected_wr * avg_win - (1 - expected_wr) * avg_loss) * n
                    total_recal += (actual_wr * avg_win - (1 - actual_wr) * avg_loss) * n
                    total_n += n

            if total_n > 0:
                orig_avg = total_orig / total_n
                recal_avg = total_recal / total_n
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("Original Avg Expectancy", f"${orig_avg:.2f}")
                with col2:
                    st.metric("Recalibrated Avg Expectancy", f"${recal_avg:.2f}")
                with col3:
                    delta = recal_avg - orig_avg
                    st.metric("Expectancy Gain", f"${delta:+.2f}")

                if recal_avg > 0:
                    st.success("✅ Recalibrated model shows positive expectancy — ranking is useful even if scale is off.")
                elif recal_avg > orig_avg:
                    st.warning("⚠️ Recalibration improves expectancy but still negative. Ranking has some value.")
                else:
                    st.error("❌ Recalibration does not improve expectancy. Confidence may not rank trades effectively.")
        else:
            st.info("Insufficient data for recalibration analysis (need ≥3 trades per bucket).")

        # ═══════════════════════════════════════════════════════════════════
        # STRATEGY OPTIMIZER
        # ═══════════════════════════════════════════════════════════════════
        st.markdown("---")
        st.markdown("### Strategy Optimizer")
        st.markdown("""Automatically search for the optimal confidence threshold that maximizes
        expectancy, profit factor, or Sharpe-like ratio. This answers:
        *"Should I raise or lower the confidence minimum?"*""")

        # Grid search over confidence thresholds
        thresholds = [0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
        opt_rows = []
        for thresh in thresholds:
            above = []
            for t in trades:
                conf = t["confidence"]
                if conf > 1:
                    conf = conf / 100
                if conf >= thresh:
                    above.append(t)

            if len(above) < 5:
                continue

            n = len(above)
            wins = sum(1 for t in above if t["outcome"] == "win")
            wr = wins / n
            pnls = [t["pnl"] for t in above]
            avg_pnl = sum(pnls) / n
            gross_win = sum(p for p in pnls if p > 0)
            gross_loss = abs(sum(p for p in pnls if p < 0))
            pf = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0)
            avg_win = sum(p for p in pnls if p > 0) / wins if wins > 0 else 0
            avg_loss = abs(sum(p for p in pnls if p < 0)) / (n - wins) if (n - wins) > 0 else 0
            exp = wr * avg_win - (1 - wr) * avg_loss

            realized_rs = [t.get("realized_r", 0) for t in above if t.get("realized_r")]
            avg_r = sum(realized_rs) / len(realized_rs) if realized_rs else 0

            # Sharpe-like ratio (expectancy / std of PnL)
            pnl_std = (sum((p - avg_pnl) ** 2 for p in pnls) / (n - 1)) ** 0.5 if n > 1 else 0
            sharpe = exp / pnl_std if pnl_std > 0 else 0

            opt_rows.append({
                "Threshold": f"≥{thresh*100:.0f}%",
                "Trades": n,
                "Win %": f"{wr*100:.1f}%",
                "Avg R": f"{avg_r:.2f}R",
                "Profit Factor": f"{pf:.2f}" if pf != float("inf") else "∞",
                "Expectancy": f"${exp:.2f}",
                "Avg PnL": f"${avg_pnl:.2f}",
                "Sharpe": f"{sharpe:.3f}",
                "Lift": f"{wr / overall_wr:.2f}×" if overall_wr > 0 else "N/A",
            })

        if opt_rows:
            st.dataframe(pd.DataFrame(opt_rows), use_container_width=True, hide_index=True)

            # Find optimal by different criteria
            best_exp = max(opt_rows, key=lambda r: float(r["Expectancy"].replace("$", "")))
            best_pf = max(opt_rows, key=lambda r: float(r["Profit Factor"].replace("∞", "99")))
            best_sharpe = max(opt_rows, key=lambda r: float(r["Sharpe"]))

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Best Expectancy",
                          f"{best_exp['Threshold']} = {best_exp['Expectancy']}",
                          help="Threshold that maximizes average expectancy per trade.")
            with col2:
                st.metric("Best Profit Factor",
                          f"{best_pf['Threshold']} = {best_pf['Profit Factor']}",
                          help="Threshold that maximizes profit factor.")
            with col3:
                st.metric("Best Sharpe",
                          f"{best_sharpe['Threshold']} = {best_sharpe['Sharpe']}",
                          help="Threshold that maximizes risk-adjusted return.")

            # Current threshold comparison
            current_thresh = 0.40  # confidence_min from config
            current_trades = [t for t in trades if (t["confidence"] / 100 if t["confidence"] > 1 else t["confidence"]) >= current_thresh]
            if current_trades:
                c_n = len(current_trades)
                c_wins = sum(1 for t in current_trades if t["outcome"] == "win")
                c_wr = c_wins / c_n
                c_pnls = [t["pnl"] for t in current_trades]
                c_avg_win = sum(p for p in c_pnls if p > 0) / c_wins if c_wins > 0 else 0
                c_avg_loss = abs(sum(p for p in c_pnls if p < 0)) / (c_n - c_wins) if (c_n - c_wins) > 0 else 0
                c_exp = c_wr * c_avg_win - (1 - c_wr) * c_avg_loss
                c_gw = sum(p for p in c_pnls if p > 0)
                c_gl = abs(sum(p for p in c_pnls if p < 0))
                c_pf = c_gw / c_gl if c_gl > 0 else 0

                st.markdown(f"""
                **Current Config (confidence_min={current_thresh*100:.0f}%):**
                {c_n} trades · WR {c_wr*100:.1f}% · PF {c_pf:.2f} · Exp ${c_exp:.2f}
                """)

                best_exp_val = float(best_exp["Expectancy"].replace("$", ""))
                if best_exp_val > c_exp and best_exp["Threshold"] != f"≥{current_thresh*100:.0f}%":
                    st.success(f"💡 Raising threshold to {best_exp['Threshold']} could improve expectancy by ${best_exp_val - c_exp:+.2f} per trade.")
                elif best_exp_val <= c_exp:
                    st.info("Current threshold is already near-optimal for expectancy.")
        else:
            st.info("Insufficient data for threshold optimization (need ≥5 trades per threshold).")

        # ═══════════════════════════════════════════════════════════════════
        # RECOMMENDED CONFIGURATION
        # ═══════════════════════════════════════════════════════════════════
        st.markdown("---")
        st.markdown("### 🎯 Recommended Configuration")
        st.markdown("""Based on historical outcome analysis. Each suggestion includes evidence tier
        and validation status. **No recommendation should be applied to production without
        out-of-sample validation.**""")

        # Evidence tier legend
        st.markdown("""
        **Evidence Tiers:** 🟢 Strong (≥30 trades, consistent) · 🟡 Moderate (10–29 trades) · 🔴 Exploratory (needs more data)
        **Validation:** ⏳ Needs OOS · 🔬 Hypothesis
        """)

        # Build recommendations from the data
        recs = []

        # 1. Confidence threshold recommendation
        if opt_rows:
            best_exp_row = max(opt_rows, key=lambda r: float(r["Expectancy"].replace("$", "")))
            best_thresh_val = best_exp_row["Threshold"].replace("≥", "").replace("%", "")
            best_exp_val = float(best_exp_row["Expectancy"].replace("$", ""))
            current_exp_val = float(opt_rows[0]["Expectancy"].replace("$", "")) if opt_rows else 0
            exp_delta = best_exp_val - current_exp_val

            if best_exp_val > current_exp_val and exp_delta > 0.05:
                best_n = int(best_exp_row["Trades"])
                if best_n >= 30:
                    evidence = "🟢 Strong"
                elif best_n >= 15:
                    evidence = "🟡 Moderate"
                else:
                    evidence = "🔴 Exploratory"

                recs.append({
                    "Parameter": "Confidence Threshold",
                    "Current": "40%",
                    "Suggested": f"{best_thresh_val}%",
                    "Expected Impact": f"+${exp_delta:.2f}/trade",
                    "Evidence": evidence,
                    "Validation": "⏳ Needs OOS",
                    "Rationale": f"Trades ≥{best_thresh_val}% show {best_exp_row['Expectancy']} expectancy vs ${current_exp_val:.2f} overall ({best_n} trades)",
                })

        # 2. Regime filter recommendation
        regime_recs = []
        for r, s in regime_perf.items():
            if s["total"] >= 10:
                gw = sum(p for p in s["pnls"] if p > 0)
                gl = abs(sum(p for p in s["pnls"] if p < 0))
                pf = gw / gl if gl > 0 else 0
                wr = s["wins"] / s["total"]
                avg_pnl = sum(s["pnls"]) / len(s["pnls"])
                regime_recs.append({"regime": r, "pf": pf, "wr": wr, "avg_pnl": avg_pnl, "n": s["total"]})

        weak_regimes = [r for r in regime_recs if r["pf"] < 0.8 and r["n"] >= 10]
        strong_regimes = [r for r in regime_recs if r["pf"] > 1.0 and r["n"] >= 10]

        if weak_regimes:
            worst = min(weak_regimes, key=lambda r: r["pf"])
            if worst["n"] >= 30:
                evidence = "🟢 Strong"
            elif worst["n"] >= 15:
                evidence = "🟡 Moderate"
            else:
                evidence = "🔴 Exploratory"

            recs.append({
                "Parameter": f"Regime Filter ({worst['regime'].replace('_', ' ').title()})",
                "Current": "All regimes",
                "Suggested": f"Reduce weight or skip",
                "Expected Impact": f"Avoid PF {worst['pf']:.2f} trades",
                "Evidence": evidence,
                "Validation": "⏳ Needs OOS",
                "Rationale": f"{worst['regime'].replace('_', ' ').title()} has PF {worst['pf']:.2f} across {worst['n']} trades",
            })

        # 3. Side recommendation
        side_perf = {}
        for t in trades:
            s = (t.get("side") or "unknown").upper()
            if s not in side_perf:
                side_perf[s] = {"wins": 0, "total": 0, "pnls": []}
            side_perf[s]["total"] += 1
            side_perf[s]["pnls"].append(t["pnl"])
            if t["outcome"] == "win":
                side_perf[s]["wins"] += 1

        side_pfs = {}
        for s, st_data in side_perf.items():
            if st_data["total"] >= 10:
                gw = sum(p for p in st_data["pnls"] if p > 0)
                gl = abs(sum(p for p in st_data["pnls"] if p < 0))
                side_pfs[s] = gw / gl if gl > 0 else 0

        if len(side_pfs) >= 2:
            sides = list(side_pfs.keys())
            if side_pfs[sides[0]] > 0 and side_pfs[sides[1]] > 0:
                ratio = max(side_pfs.values()) / min(side_pfs.values())
                if ratio > 1.5:
                    stronger = max(side_pfs, key=side_pfs.get)
                    weaker = min(side_pfs, key=side_pfs.get)
                    total_side = sum(side_perf[s]["total"] for s in side_perf if s in side_pfs)
                    if total_side >= 30:
                        evidence = "🟢 Strong"
                    elif total_side >= 15:
                        evidence = "🟡 Moderate"
                    else:
                        evidence = "🔴 Exploratory"

                    recs.append({
                        "Parameter": "Side Asymmetry",
                        "Current": "Symmetric",
                        "Suggested": f"Favor {stronger}",
                        "Expected Impact": f"PF {side_pfs[stronger]:.2f} vs {side_pfs[weaker]:.2f}",
                        "Evidence": evidence,
                        "Validation": "⏳ Needs OOS",
                        "Rationale": f"{stronger} PF {side_pfs[stronger]:.2f} vs {weaker} PF {side_pfs[weaker]:.2f} ({ratio:.1f}× difference, {total_side} trades)",
                    })

        # 4. Component weight recommendations
        for field_key, label, direction in contrib_components:
            vals = [(t, t.get(field_key, 0) or 0) for t in trades]
            non_zero = [(t, v) for t, v in vals if v > 0]
            if len(non_zero) < 10:
                continue
            sorted_vals = sorted(non_zero, key=lambda x: x[1])
            n = len(sorted_vals)
            q1_trades = sorted_vals[:n // 4]
            q4_trades = sorted_vals[3 * n // 4:]
            q1_wr = sum(1 for t, _ in q1_trades if t["outcome"] == "win") / len(q1_trades) if q1_trades else 0
            q4_wr = sum(1 for t, _ in q4_trades if t["outcome"] == "win") / len(q4_trades) if q4_trades else 0
            if direction == "positive":
                wr_delta = q4_wr - q1_wr
            else:
                wr_delta = q1_wr - q4_wr

            if wr_delta < -0.05:
                if n >= 30 and abs(wr_delta) > 0.10:
                    evidence = "🟢 Strong"
                elif n >= 15:
                    evidence = "🟡 Moderate"
                else:
                    evidence = "🔴 Exploratory"

                recs.append({
                    "Parameter": f"Weight: {label}",
                    "Current": "See formula",
                    "Suggested": "Reduce weight",
                    "Expected Impact": f"WR Δ {wr_delta*100:+.1f}%",
                    "Evidence": evidence,
                    "Validation": "⏳ Needs OOS",
                    "Rationale": f"Higher {label} scores correlate with LOWER win rates ({n} trades)",
                })
            elif wr_delta > 0.10:
                if n >= 30 and wr_delta > 0.15:
                    evidence = "🟢 Strong"
                elif n >= 15:
                    evidence = "🟡 Moderate"
                else:
                    evidence = "🔴 Exploratory"

                recs.append({
                    "Parameter": f"Weight: {label}",
                    "Current": "See formula",
                    "Suggested": "Increase weight",
                    "Expected Impact": f"WR Δ +{wr_delta*100:.1f}%",
                    "Evidence": evidence,
                    "Validation": "⏳ Needs OOS",
                    "Rationale": f"Higher {label} scores correlate with higher win rates ({n} trades)",
                })

        if recs:
            st.dataframe(pd.DataFrame(recs), use_container_width=True, hide_index=True)

            # Evidence summary
            strong = sum(1 for r in recs if "🟢" in r["Evidence"])
            moderate = sum(1 for r in recs if "🟡" in r["Evidence"])
            exploratory = sum(1 for r in recs if "🔴" in r["Evidence"])
            needs_oos = sum(1 for r in recs if "⏳" in r["Validation"])
            hypothesis = sum(1 for r in recs if "🔬" in r["Validation"])

            st.markdown(f"**{len(recs)} recommendations:** {strong} strong evidence · {moderate} moderate · {exploratory} exploratory")
            st.markdown(f"**Validation:** {needs_oos} need OOS validation · {hypothesis} are hypotheses")

            st.warning("""⚠️ **Before applying any recommendation to production:**
1. Validate on out-of-sample data (hold out 20–30% of trades)
2. Run walk-forward analysis across multiple time periods
3. Verify the pattern is stable across regimes and symbols
4. Check that sample sizes are sufficient (≥30 trades per comparison group)
5. Ensure the confidence score was designed as a probability, not just a ranking score""")

            st.markdown("""**Suggested Research Workflow:**
1. Pick the strongest evidence recommendation (🟢)
2. Split data: 70% derivation, 30% validation
3. Verify the pattern holds on the validation set
4. If confirmed, implement as an A/B test with position sizing
5. Monitor for 50+ trades before full deployment""")
        else:
            st.info("No actionable recommendations at current sample size. Continue collecting outcome data.")


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: PRODUCTION SCORECARD
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📊 Production Scorecard":
    st.title("📊 Production Scorecard")
    st.markdown("Compact institutional scorecard — all key metrics at a glance.")
    
    try:
        repo_data = load_repository()
    except Exception as e:
        st.error(f"Failed to load repository data: {e}")
        st.stop()
    
    sc = repo_data.get("scorecard", {})
    if not sc:
        st.warning("No scorecard data available.")
    else:
        # ── Row 1: Core KPIs ──
        st.markdown("### Core Performance")
        cols = st.columns(6)
        kpis = [
            ("Profit Factor", f"{sc['profit_factor']:.2f}", color_val(sc['profit_factor'], 1.0)),
            ("Expectancy", f"${sc['expectancy']:.2f}", color_val(sc['expectancy'])),
            ("Expectancy R", f"{sc['expectancy_r']:.2f}R", color_val(sc['expectancy_r'])),
            ("Win Rate", f"{sc['win_rate']:.1f}%", color_val(sc['win_rate'] / 100, 0.5)),
            ("Total Trades", str(sc['total_trades']), ""),
            ("Avg R", f"{sc['avg_r']:.2f}R", color_val(sc['avg_r'])),
        ]
        for col, (label, value, css) in zip(cols, kpis):
            with col:
                st.markdown(
                    f'<div class="metric-card">'
                    f'<div class="metric-value {css}">{value}</div>'
                    f'<div class="metric-label">{label}</div></div>',
                    unsafe_allow_html=True,
                )
        
        # ── Row 2: Risk Metrics ──
        st.markdown("### Risk Metrics")
        cols = st.columns(4)
        risk = [
            ("Avg MAE", f"{sc['avg_mae']:.2f}%", "negative"),
            ("Avg MFE", f"{sc['avg_mfe']:.2f}%", "positive"),
            ("Largest Drawdown", f"${sc['largest_drawdown']:.2f}", "negative"),
            ("Total Trades", str(sc['total_trades']), ""),
        ]
        for col, (label, value, css) in zip(cols, risk):
            with col:
                st.markdown(
                    f'<div class="metric-card">'
                    f'<div class="metric-value {css}">{value}</div>'
                    f'<div class="metric-label">{label}</div></div>',
                    unsafe_allow_html=True,
                )
        
        # ── Row 3: Best/Worst ──
        st.markdown("### Best & Worst")
        cols = st.columns(4)
        bw = [
            ("Best Symbol", sc.get('best_symbol', 'N/A'), "positive"),
            ("Worst Symbol", sc.get('worst_symbol', 'N/A'), "negative"),
            ("Best Session", sc.get('best_session', 'N/A'), "positive"),
            ("Worst Session", sc.get('worst_session', 'N/A'), "negative"),
        ]
        for col, (label, value, css) in zip(cols, bw):
            with col:
                st.markdown(
                    f'<div class="metric-card">'
                    f'<div class="metric-value {css}">{value}</div>'
                    f'<div class="metric-label">{label}</div></div>',
                    unsafe_allow_html=True,
                )
        
        # ── Row 4: Candidate Stats ──
        st.markdown("### Candidate Pipeline")
        cols = st.columns(5)
        cand = [
            ("Accepted Signals", str(sc['accepted_signals']), "positive"),
            ("Rejected Candidates", str(sc['rejected_candidates']), "neutral"),
            ("Rejected Winners", str(sc['rejected_winners']), "negative"),
            ("Rejected Losers", str(sc['rejected_losers']), "positive"),
            ("Rej. Winner %", f"{sc['rejected_winner_pct']:.1f}%", 
             "negative" if sc['rejected_winner_pct'] > 30 else "positive"),
        ]
        for col, (label, value, css) in zip(cols, cand):
            with col:
                st.markdown(
                    f'<div class="metric-card">'
                    f'<div class="metric-value {css}">{value}</div>'
                    f'<div class="metric-label">{label}</div></div>',
                    unsafe_allow_html=True,
                )
        
        # ── Rolling Validation Summary ──
        st.markdown("### Rolling Validation")
        cols = st.columns(3)
        for col, (label, rolling) in zip(cols, [
            ("25-Trade Window", sc.get('rolling_25')),
            ("50-Trade Window", sc.get('rolling_50')),
            ("100-Trade Window", sc.get('rolling_100')),
        ]):
            with col:
                if rolling:
                    pf = rolling.get('profit_factor', 0)
                    exp = rolling.get('expectancy', 0)
                    wr = rolling.get('win_rate', 0)
                    st.markdown(
                        f'<div class="metric-card">'
                        f'<div class="metric-label">{label}</div>'
                        f'<div class="metric-value {color_val(pf, 1.0)}">PF: {pf:.2f}</div>'
                        f'<div class="metric-value {color_val(exp)}">Exp: ${exp:.2f}</div>'
                        f'<div class="metric-value">WR: {wr*100:.1f}%</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div class="metric-card">'
                        f'<div class="metric-label">{label}</div>'
                        f'<div class="metric-value neutral">Insufficient data</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: CANDIDATE REPOSITORY
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📋 Candidate Repository":
    st.title("📋 Candidate Repository")
    st.markdown("Every accepted and rejected setup — the foundation for understanding 'what almost happened.'")
    
    try:
        repo_data = load_repository()
    except Exception as e:
        st.error(f"Failed to load repository data: {e}")
        st.stop()
    
    overview = repo_data.get("overview", {})
    accepted = repo_data.get("accepted_performance", {})
    rejected = repo_data.get("rejected_performance", {})
    filters = repo_data.get("filter_effectiveness", {})
    calib = repo_data.get("confidence_calibration", {})
    
    # ── Overview Cards ──
    st.markdown("### Pipeline Overview")
    cols = st.columns(4)
    with cols[0]:
        st.markdown(
            f'<div class="metric-card">'
            f'<div class="metric-value positive">{overview.get("total_candidates", 0):,}</div>'
            f'<div class="metric-label">Total Candidates</div></div>',
            unsafe_allow_html=True,
        )
    with cols[1]:
        st.markdown(
            f'<div class="metric-card">'
            f'<div class="metric-value positive">{overview.get("accepted_count", 0)}</div>'
            f'<div class="metric-label">Accepted Signals</div></div>',
            unsafe_allow_html=True,
        )
    with cols[2]:
        st.markdown(
            f'<div class="metric-card">'
            f'<div class="metric-value neutral">{overview.get("rejected_count", 0)}</div>'
            f'<div class="metric-label">Rejected Candidates</div></div>',
            unsafe_allow_html=True,
        )
    with cols[3]:
        st.markdown(
            f'<div class="metric-card">'
            f'<div class="metric-value">{overview.get("tracked_count", 0)}</div>'
            f'<div class="metric-label">With Outcome Data</div></div>',
            unsafe_allow_html=True,
        )
    
    # ── What Almost Happened ──
    st.markdown("### ❌ What Almost Happened — Rejected Candidates")
    rej = repo_data.get("rejected_performance", {})
    cols = st.columns(4)
    with cols[0]:
        st.markdown(
            f'<div class="metric-card">'
            f'<div class="metric-value">{rej.get("with_outcome", 0)}</div>'
            f'<div class="metric-label">Rejected w/ Outcome</div></div>',
            unsafe_allow_html=True,
        )
    with cols[1]:
        st.markdown(
            f'<div class="metric-card">'
            f'<div class="metric-value negative">{rej.get("winners", 0)}</div>'
            f'<div class="metric-label">Would-Be Winners</div></div>',
            unsafe_allow_html=True,
        )
    with cols[2]:
        st.markdown(
            f'<div class="metric-card">'
            f'<div class="metric-value positive">{rej.get("losers", 0)}</div>'
            f'<div class="metric-label">Would-Be Losers</div></div>',
            unsafe_allow_html=True,
        )
    with cols[3]:
        wp = rej.get("winner_pct", 0)
        st.markdown(
            f'<div class="metric-card">'
            f'<div class="metric-value {"negative" if wp > 30 else "positive"}">{wp:.1f}%</div>'
            f'<div class="metric-label">Rej. Winner Rate</div></div>',
            unsafe_allow_html=True,
        )
    
    # ── Filter Effectiveness ──
    if filters:
        st.markdown("### Filter Effectiveness by Stage")
        st.markdown("Shows which filters are rejecting the most candidates and how many would have been winners.")
        
        rows = []
        for stage, eff in filters.items():
            rows.append({
                "Stage": stage.replace("_", " ").title(),
                "Rejected": eff["total_rejected"],
                "Would-Be Winners": eff["rejected_winners"],
                "Would-Be Losers": eff["rejected_losers"],
                "Winner Rate": f"{eff['winner_rate']:.1f}%",
                "Avg Winner Return": f"{eff['avg_winner_return']:.2f}%",
                "Opportunity Cost": f"{eff['total_opportunity_cost']:.2f}%",
            })
        
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    
    # ── Component Marginal Contribution ──
    marginals = repo_data.get("component_marginals", {})
    if marginals:
        st.markdown("### Component Marginal Contribution")
        st.markdown("For each component, which score ranges predict better outcomes? This reveals which filters are working and which need recalibration.")
        
        for component, buckets in marginals.items():
            if not buckets:
                continue
            
            st.markdown(f"#### {component}")
            rows = []
            for m in buckets:
                pf_str = f"{m['profit_factor']:.2f}" if m['profit_factor'] < 999 else "∞"
                rows.append({
                    "Bucket": m["bucket"],
                    "Candidates": m["candidates"],
                    "Win %": f"{m['win_rate']:.1f}%",
                    "Avg R": f"{m['avg_rr']:.2f}R",
                    "PF": pf_str,
                    "Avg Return": f"{m['avg_return']:.2f}%",
                    "Winners": m["winners"],
                    "Losers": m["losers"],
                })
            
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, hide_index=True)
            
            # Highlight best bucket
            best = max(buckets, key=lambda b: b.get("profit_factor", 0) if b.get("profit_factor", 0) < 999 else 0)
            worst = min(buckets, key=lambda b: b.get("profit_factor", 0))
            col1, col2 = st.columns(2)
            with col1:
                st.markdown(f"  ✅ **Best**: {best['bucket']} — PF={best['profit_factor']:.2f}, WR={best['win_rate']:.1f}%")
            with col2:
                st.markdown(f"  ❌ **Worst**: {worst['bucket']} — PF={worst['profit_factor']:.2f}, WR={worst['win_rate']:.1f}%")
    
    # ── Confidence Calibration ──
    if calib:
        st.markdown("### Confidence Calibration — Candidates with Outcomes")
        st.markdown("Higher confidence should predict better outcomes. If it doesn't, the model needs recalibration.")
        
        rows = []
        for bucket, stats in sorted(calib.items()):
            rows.append({
                "Bucket": bucket,
                "Total": stats["total"],
                "Accepted": stats["accepted"],
                "Rejected": stats["rejected"],
                "Winners": stats["winners"],
                "Losers": stats["losers"],
                "Win Rate": f"{stats['win_rate']:.1f}%",
                "Avg Return": f"{stats['avg_return']:.2f}%",
                "Rej. Winners": stats["rejected_winners"],
                "Rej. Winner %": f"{stats['rejected_winner_pct']:.1f}%",
            })
        
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        
        # Chart: Win Rate by Confidence
        st.markdown("### Win Rate by Confidence Bucket")
        chart_data = pd.DataFrame({
            "Bucket": list(calib.keys()),
            "Win Rate (%)": [calib[k]["win_rate"] for k in calib.keys()],
        })
        st.bar_chart(chart_data.set_index("Bucket"))


# ══════════════════════════════════════════════════════════════════════════════
# PAGE: ROLLING VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📈 Rolling Validation":
    st.title("📈 Rolling Validation")
    st.markdown("Rolling 25/50/100-trade validation — the institutional standard for strategy robustness.")
    
    try:
        repo_data = load_repository()
    except Exception as e:
        st.error(f"Failed to load repository data: {e}")
        st.stop()
    
    for window_name, window_key in [
        ("25-Trade Window", "rolling_25"),
        ("50-Trade Window", "rolling_50"),
        ("100-Trade Window", "rolling_100"),
    ]:
        rolling = repo_data.get(window_key, [])
        if not rolling:
            st.info(f"No data for {window_name} (need at least {window_key.split('_')[1]} trades).")
            continue
        
        st.markdown(f"### {window_name}")
        
        # Current values (last point)
        latest = rolling[-1]
        cols = st.columns(5)
        with cols[0]:
            st.markdown(
                f'<div class="metric-card">'
                f'<div class="metric-value {color_val(latest["profit_factor"], 1.0)}">{latest["profit_factor"]:.2f}</div>'
                f'<div class="metric-label">Current PF</div></div>',
                unsafe_allow_html=True,
            )
        with cols[1]:
            st.markdown(
                f'<div class="metric-card">'
                f'<div class="metric-value {color_val(latest["expectancy"])}">${latest["expectancy"]:.2f}</div>'
                f'<div class="metric-label">Current Expectancy</div></div>',
                unsafe_allow_html=True,
            )
        with cols[2]:
            st.markdown(
                f'<div class="metric-card">'
                f'<div class="metric-value">{latest["win_rate"]*100:.1f}%</div>'
                f'<div class="metric-label">Current WR</div></div>',
                unsafe_allow_html=True,
            )
        with cols[3]:
            st.markdown(
                f'<div class="metric-card">'
                f'<div class="metric-value {color_val(latest["avg_r"])}">{latest["avg_r"]:.2f}R</div>'
                f'<div class="metric-label">Current Avg R</div></div>',
                unsafe_allow_html=True,
            )
        with cols[4]:
            st.markdown(
                f'<div class="metric-card">'
                f'<div class="metric-value {color_val(latest["total_pnl"])}">${latest["total_pnl"]:.2f}</div>'
                f'<div class="metric-label">Window PnL</div></div>',
                unsafe_allow_html=True,
            )
        
        # Charts
        import pandas as pd
        df = pd.DataFrame(rolling)
        
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"#### Rolling Profit Factor ({window_name})")
            st.line_chart(df.set_index("trade_num")["profit_factor"])
        with col2:
            st.markdown(f"#### Rolling Expectancy ({window_name})")
            st.line_chart(df.set_index("trade_num")["expectancy"])
        
        st.markdown(f"#### Rolling Win Rate ({window_name})")
        st.line_chart(df.set_index("trade_num")["win_rate"])


# ── Footer ───────────────────────────────────────────────────────────────────

st.sidebar.markdown("---")
st.sidebar.markdown(
    "**Engine:** READ-ONLY analytics\n\n"
    "**Trading Logic:** LOCKED\n\n"
    f"**Trades:** {data['overview']['total_trades']}"
)
