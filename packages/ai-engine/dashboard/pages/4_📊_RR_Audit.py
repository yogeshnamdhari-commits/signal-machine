"""
📊 Risk:Reward Audit Dashboard
================================
Visualizes RR rejection patterns and root cause analysis.
Shows why signals are failing the RR gate.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List
from collections import defaultdict

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ── Path Setup ───────────────────────────────────────────────────
_ai_root = Path(__file__).resolve().parent.parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

# ── Page Config ──────────────────────────────────────────────────
st.set_page_config(
    page_title="📊 RR Audit — DeltaTerminal",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Custom CSS ───────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
    * { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }
    
    .block-container { padding: 0.5rem 1rem !important; max-width: 100% !important; }
    
    .metric-card {
        background: linear-gradient(135deg, #0f1420 0%, #0c1018 100%);
        padding: 16px 18px; border-radius: 12px;
        border: 1px solid rgba(255,255,255,0.06);
        text-align: center;
    }
    .metric-card .val { font-size: 1.5rem; font-weight: 800; color: #e8ecf1; }
    .metric-card .lbl { font-size: 0.8rem; color: #8892a4; margin-top: 4px; text-transform: uppercase; }
    
    .cause-card {
        background: #161b22; border: 1px solid #30363d;
        border-radius: 8px; padding: 12px 16px; margin: 8px 0;
    }
    .cause-high { border-left: 3px solid #f85149; }
    .cause-medium { border-left: 3px solid #d29922; }
    .cause-low { border-left: 3px solid #3fb950; }
    
    .rejection-row {
        display: flex; gap: 12px; padding: 8px 12px;
        background: #0d1117; border-radius: 6px; margin: 4px 0;
        border-left: 3px solid #f85149;
        font-size: 0.85rem;
    }
</style>
""", unsafe_allow_html=True)

# ── Auto-refresh (every 60s) ──
st_autorefresh(interval=60_000, key="rr_audit_refresh")


def _p(v):
    """Format price."""
    if v is None or v == 0:
        return "—"
    if v >= 100:
        return f"{v:.2f}"
    if v >= 1:
        return f"{v:.4f}"
    if v >= 0.01:
        return f"{v:.5f}"
    return f"{v:.6f}"


# ════════════════════════════════════════════════════════════════
# HEADER
# ════════════════════════════════════════════════════════════════
st.markdown("""
<div style="display:flex;gap:6px;align-items:center;background:#0d1117;padding:8px 16px;border-radius:8px;font-size:.85rem;margin-bottom:12px">
    <span style="color:#58a6ff;font-weight:bold">📊 RISK:REWARD AUDIT</span>│
    <span>🕐 {time}</span>│
    <span>⏱️ Auto-refresh 60s</span>│
    <span>🔍 Analyzing why signals fail RR gate</span>
</div>
""".format(
    time=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
), unsafe_allow_html=True)

# ════════════════════════════════════════════════════════════════
# DATA LAYER — Read from bridge JSON (written by engine process)
# ════════════════════════════════════════════════════════════════
BRIDGE_RR_AUDIT = Path(__file__).resolve().parent.parent.parent / "data" / "bridge" / "rr_audit.json"

@st.cache_data(ttl=2)
def load_rr_audit_bridge():
    """Load RR audit data from bridge JSON (written by engine)."""
    if not BRIDGE_RR_AUDIT.exists():
        return {"total": 0, "tracked": 0, "avg_rr": 0, "avg_sl_dist_pct": 0, 
                "near_miss_pct": 0, "top_symbols": [], "top_sessions": [], 
                "top_regimes": [], "rr_distribution": {}, "sl_distance_distribution": {},
                "csv_path": None}
    try:
        with open(BRIDGE_RR_AUDIT) as f:
            data = json.load(f)
        return data
    except Exception:
        return {"total": 0}

# Also try to read recent rejections from CSV
def load_recent_rejections_from_csv(count=50):
    """Load recent rejections from the daily CSV file."""
    csv_dir = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "rr_audit"
    if not csv_dir.exists():
        return []
    
    # Find today's CSV
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    csv_path = csv_dir / f"rr_rejections_{date_str}.csv"
    if not csv_path.exists():
        # Try to find any recent CSV
        csv_files = sorted(csv_dir.glob("rr_rejections_*.csv"), reverse=True)
        if not csv_files:
            return []
        csv_path = csv_files[0]
    
    try:
        import csv
        rejections = []
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Convert numeric fields
                for key in ["entry", "stop_loss", "tp1", "tp2", "tp3",
                            "risk", "reward", "rr_actual", "rr_required",
                            "rr_deficit", "risk_pct", "reward_pct", "rr_gap",
                            "atr_value", "sl_atr_mult", "sl_dist_pct",
                            "tp1_rr_mult", "confidence"]:
                    if key in row and row[key]:
                        try:
                            row[key] = float(row[key])
                        except (ValueError, TypeError):
                            row[key] = 0.0
                rejections.append(row)
        return rejections[-count:]
    except Exception:
        return []

stats = load_rr_audit_bridge()
recent_rejections = load_recent_rejections_from_csv(count=100)

# ════════════════════════════════════════════════════════════════
# ROW 1: SUMMARY KPIs
# ════════════════════════════════════════════════════════════════
c1, c2, c3, c4, c5, c6 = st.columns(6)

with c1:
    st.markdown(f"""<div class="metric-card">
        <div class="val" style="color:#f85149">{stats.get('total', 0)}</div>
        <div class="lbl">Total Rejections</div>
    </div>""", unsafe_allow_html=True)

with c2:
    avg_rr = stats.get('avg_rr', 0)
    color = "#3fb950" if avg_rr >= 1.2 else ("#d29922" if avg_rr >= 0.8 else "#f85149")
    st.markdown(f"""<div class="metric-card">
        <div class="val" style="color:{color}">{avg_rr:.2f}</div>
        <div class="lbl">Avg RR at Rejection</div>
    </div>""", unsafe_allow_html=True)

with c3:
    avg_sl = stats.get('avg_sl_dist_pct', 0)
    color = "#3fb950" if avg_sl <= 2.0 else ("#d29922" if avg_sl <= 3.0 else "#f85149")
    st.markdown(f"""<div class="metric-card">
        <div class="val" style="color:{color}">{avg_sl:.1f}%</div>
        <div class="lbl">Avg SL Distance</div>
    </div>""", unsafe_allow_html=True)

with c4:
    near_miss = stats.get('near_miss_pct', 0)
    color = "#f85149" if near_miss > 20 else ("#d29922" if near_miss > 10 else "#3fb950")
    st.markdown(f"""<div class="metric-card">
        <div class="val" style="color:{color}">{near_miss:.0f}%</div>
        <div class="lbl">Near Misses</div>
    </div>""", unsafe_allow_html=True)

with c5:
    csv_path = stats.get('csv_path', '')
    has_csv = "✅" if csv_path else "❌"
    st.markdown(f"""<div class="metric-card">
        <div class="val">{has_csv}</div>
        <div class="lbl">CSV Logging</div>
    </div>""", unsafe_allow_html=True)

with c6:
    tracked = stats.get('tracked', 0)
    st.markdown(f"""<div class="metric-card">
        <div class="val">{tracked}</div>
        <div class="lbl">In Memory</div>
    </div>""", unsafe_allow_html=True)

st.divider()

# ════════════════════════════════════════════════════════════════
# ROW 2: RR DISTRIBUTION + SL DISTANCE
# ════════════════════════════════════════════════════════════════
col1, col2 = st.columns(2)

with col1:
    st.markdown("**📊 RR Distribution at Rejection**")
    rr_dist = stats.get('rr_distribution', {})
    if rr_dist:
        # Create histogram data
        rr_values = []
        for rr_str, count in rr_dist.items():
            try:
                rr_val = float(rr_str)
                rr_values.extend([rr_val] * count)
            except ValueError:
                continue
        
        if rr_values:
            fig = px.histogram(
                x=rr_values,
                nbins=20,
                labels={"x": "Risk:Reward Ratio", "y": "Count"},
                color_discrete_sequence=["#f85149"],
            )
            fig.update_layout(
                showlegend=False,
                height=300,
                margin=dict(l=0, r=0, t=10, b=0),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font_color="#8892a4",
                xaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
                yaxis=dict(gridcolor="rgba(255,255,255,0.05)"),
            )
            # Add vertical line at min_rr threshold
            fig.add_vline(x=1.5, line_dash="dash", line_color="#58a6ff",
                         annotation_text="Min RR (1.5)")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No RR data available")
    else:
        st.info("No rejections recorded yet")

with col2:
    st.markdown("**📏 Stop Loss Distance Distribution**")
    sl_dist = stats.get('sl_distance_distribution', {})
    if sl_dist:
        # Create bar chart
        buckets = []
        counts = []
        for bucket, count in sorted(sl_dist.items()):
            buckets.append(bucket)
            counts.append(count)
        
        fig = go.Figure(data=[
            go.Bar(x=buckets, y=counts, marker_color="#f85149")
        ])
        fig.update_layout(
            showlegend=False,
            height=300,
            margin=dict(l=0, r=0, t=10, b=0),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font_color="#8892a4",
            xaxis=dict(title="SL Distance %", gridcolor="rgba(255,255,255,0.05)"),
            yaxis=dict(title="Count", gridcolor="rgba(255,255,255,0.05)"),
        )
        # Add threshold line at 3%
        fig.add_hline(y=0, line_dash="dash", line_color="#58a6ff")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No SL distance data available")

st.divider()

# ════════════════════════════════════════════════════════════════
# ROW 3: TOP REJECTED SYMBOLS + SESSION/REGIME BREAKDOWN
# ════════════════════════════════════════════════════════════════
col1, col2, col3 = st.columns(3)

with col1:
    st.markdown("**🔴 Top Rejected Symbols**")
    top_symbols = stats.get('top_symbols', [])
    if top_symbols:
        for sym, count in top_symbols[:10]:
            # Calculate percentage
            pct = count / stats.get('total', 1) * 100
            bar_width = int(pct / 2)
            bar = "█" * bar_width
            st.markdown(f"""
            <div style="display:flex;justify-content:space-between;align-items:center;padding:4px 8px;background:#0d1117;border-radius:4px;margin:2px 0">
                <span style="font-weight:600">{sym}</span>
                <span style="color:#f85149">{count} ({pct:.1f}%)</span>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No symbol data")

with col2:
    st.markdown("**🕐 By Session**")
    sessions = stats.get('top_sessions', [])
    if sessions:
        for sess, count in sessions:
            pct = count / stats.get('total', 1) * 100
            st.markdown(f"""
            <div style="display:flex;justify-content:space-between;align-items:center;padding:4px 8px;background:#0d1117;border-radius:4px;margin:2px 0">
                <span>{sess}</span>
                <span style="color:#58a6ff">{count} ({pct:.1f}%)</span>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No session data")

with col3:
    st.markdown("**📊 By Regime**")
    regimes = stats.get('top_regimes', [])
    if regimes:
        for regime, count in regimes:
            pct = count / stats.get('total', 1) * 100
            color = "#3fb950" if "bull" in regime.lower() else ("#f85149" if "bear" in regime.lower() else "#58a6ff")
            st.markdown(f"""
            <div style="display:flex;justify-content:space-between;align-items:center;padding:4px 8px;background:#0d1117;border-radius:4px;margin:2px 0">
                <span>{regime}</span>
                <span style="color:{color}">{count} ({pct:.1f}%)</span>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.info("No regime data")

st.divider()

# ════════════════════════════════════════════════════════════════
# ROW 4: ROOT CAUSE ANALYSIS
# ════════════════════════════════════════════════════════════════
st.markdown("**🔍 Root Cause Analysis**")

# Perform root cause analysis
rejections = recent_rejections
if rejections:
    # Analyze patterns
    wide_sl = [r for r in rejections if r.get('sl_dist_pct', 0) > 3.0]
    low_reward = [r for r in rejections if r.get('reward_pct', 0) < 1.0]
    near_miss = [r for r in rejections if 0 < r.get('rr_actual', 0) < r.get('rr_required', 1.5) * 1.1]
    
    total = len(rejections)
    
    if wide_sl:
        pct = len(wide_sl) / total * 100
        severity = "cause-high" if pct > 30 else "cause-medium"
        st.markdown(f"""
        <div class="cause-card {severity}">
            <div style="font-weight:700;margin-bottom:4px">⚠️ STOP LOSS TOO WIDE</div>
            <div>{pct:.1f}% of rejections have SL distance > 3%</div>
            <div style="color:#8892a4;font-size:0.85rem;margin-top:4px">
                Impact: Increases risk → decreases RR ratio → rejection
            </div>
        </div>
        """, unsafe_allow_html=True)
    
    if low_reward:
        pct = len(low_reward) / total * 100
        severity = "cause-high" if pct > 30 else "cause-medium"
        st.markdown(f"""
        <div class="cause-card {severity}">
            <div style="font-weight:700;margin-bottom:4px">⚠️ REWARD TOO LOW</div>
            <div>{pct:.1f}% of rejections have reward < 1%</div>
            <div style="color:#8892a4;font-size:0.85rem;margin-top:4px">
                Impact: TP1 too close to entry → insufficient profit potential
            </div>
        </div>
        """, unsafe_allow_html=True)
    
    if near_miss:
        pct = len(near_miss) / total * 100
        severity = "cause-medium" if pct > 20 else "cause-low"
        st.markdown(f"""
        <div class="cause-card {severity}">
            <div style="font-weight:700;margin-bottom:4px">⚠️ NEAR-MISS REJECTIONS</div>
            <div>{pct:.1f}% of rejections are within 10% of passing the RR threshold</div>
            <div style="color:#8892a4;font-size:0.85rem;margin-top:4px">
                Impact: Potentially profitable setups being rejected
            </div>
        </div>
        """, unsafe_allow_html=True)
    
    if not wide_sl and not low_reward and not near_miss:
        st.success("✅ No major root causes detected in recent rejections")
else:
    st.info("No rejections to analyze")

st.divider()

# ════════════════════════════════════════════════════════════════
# ROW 5: RECENT REJECTION DETAILS
# ════════════════════════════════════════════════════════════════
st.markdown("**📋 Recent Rejection Details**")

if recent_rejections:
    # Create DataFrame
    df_data = []
    for r in recent_rejections[-50:]:  # Show last 50
        # Parse timestamp - try timestamp_iso first, then timestamp
        ts_str = r.get('timestamp_iso', '')
        if ts_str:
            try:
                ts_display = datetime.fromisoformat(ts_str).strftime("%H:%M:%S")
            except:
                ts_display = str(ts_str)[:8]
        else:
            ts_display = "—"
        
        df_data.append({
            "Time": ts_display,
            "Symbol": r.get('symbol', '?'),
            "Side": r.get('side', '?'),
            "Entry": _p(r.get('entry', 0)),
            "SL": _p(r.get('stop_loss', 0)),
            "TP1": _p(r.get('tp1', 0)),
            "Risk": f"{r.get('risk', 0):.4f}",
            "Reward": f"{r.get('reward', 0):.4f}",
            "RR": f"{r.get('rr_actual', 0):.2f}",
            "Required": f"{r.get('rr_required', 0):.1f}",
            "SL Dist": f"{r.get('sl_dist_pct', 0):.2f}%",
            "ATR": f"{r.get('atr_value', 0):.6f}",
            "Conf": f"{r.get('confidence', 0):.0f}%",
            "Source": r.get('rejection_source', '?'),
        })
    
    if df_data:
        df = pd.DataFrame(df_data)
        
        # Style the DataFrame (use map for pandas >= 2.1, applymap for older)
        def highlight_side(val):
            if val == "LONG":
                return "color: #3fb950"
            elif val == "SHORT":
                return "color: #f85149"
            return ""
        
        def highlight_rr(val):
            try:
                rr = float(val)
                if rr >= 1.2:
                    return "color: #3fb950"
                elif rr >= 0.8:
                    return "color: #d29922"
                else:
                    return "color: #f85149"
            except:
                return ""
        
        # Use map() which works on pandas >= 2.1 (applymap was deprecated)
        try:
            styled_df = df.style.map(highlight_side, subset=["Side"])
            styled_df = styled_df.map(highlight_rr, subset=["RR"])
        except AttributeError:
            # Fallback for older pandas versions
            styled_df = df.style.applymap(highlight_side, subset=["Side"])
            styled_df = styled_df.applymap(highlight_rr, subset=["RR"])
        
        st.dataframe(
            styled_df,
            use_container_width=True,
            hide_index=True,
            height=min(400, 35 * len(df) + 40),
        )
    else:
        st.info("No recent rejections to display")
else:
    st.info("""
    **No RR rejections recorded yet.**
    
    The RR audit system will start recording rejections when:
    1. The EMA V5 scanner is running
    2. Signals are generated but fail the RR gate (RR < 1.5)
    3. The engine rejects signals in the RR filter
    
    Check back after the scanner has processed some symbols.
    """)

# ════════════════════════════════════════════════════════════════
# ROW 6: SYMBOL DEEP DIVE
# ════════════════════════════════════════════════════════════════
st.divider()
st.markdown("**🔍 Symbol Deep Dive**")

top_syms = stats.get('top_symbols', [])
if top_syms:
    sym_options = [sym for sym, _ in top_syms[:20]]
    selected_sym = st.selectbox("Select symbol for detailed analysis", sym_options, key="rr_sym_select")
    
    if selected_sym:
        # Find rejections for this symbol from CSV data
        sym_rejections = [r for r in recent_rejections if r.get('symbol') == selected_sym]
        
        if sym_rejections:
            sym_rr = [r.get('rr_actual', 0) for r in sym_rejections if r.get('rr_actual')]
            sym_sl = [r.get('sl_dist_pct', 0) for r in sym_rejections if r.get('sl_dist_pct')]
            
            sc1, sc2, sc3, sc4 = st.columns(4)
            
            with sc1:
                st.metric("Rejections", len(sym_rejections))
            with sc2:
                avg_rr = sum(sym_rr) / len(sym_rr) if sym_rr else 0
                st.metric("Avg RR", f"{avg_rr:.2f}")
            with sc3:
                avg_sl = sum(sym_sl) / len(sym_sl) if sym_sl else 0
                st.metric("Avg SL Distance", f"{avg_sl:.2f}%")
            with sc4:
                min_rr = min(sym_rr) if sym_rr else 0
                max_rr = max(sym_rr) if sym_rr else 0
                st.metric("RR Range", f"{min_rr:.2f} - {max_rr:.2f}")
            
            # Show recent rejections for this symbol
            st.markdown(f"**Recent rejections for {selected_sym}:**")
            for r in sym_rejections[-5:]:
                ts = r.get('timestamp_iso', '')
                if ts:
                    try:
                        ts = datetime.fromisoformat(ts).strftime("%H:%M:%S")
                    except:
                        ts = ts[:8]
                st.markdown(f"""
                <div class="rejection-row">
                    <span>{ts}</span>
                    <span>{r.get('side', '?')}</span>
                    <span>Entry: {_p(r.get('entry', 0))}</span>
                    <span>SL: {_p(r.get('stop_loss', 0))}</span>
                    <span>TP1: {_p(r.get('tp1', 0))}</span>
                    <span style="color:#f85149">RR: {r.get('rr_actual', 0):.2f} < {r.get('rr_required', 0):.1f}</span>
                </div>
                """, unsafe_allow_html=True)
        else:
            st.info(f"No rejections recorded for {selected_sym}")
else:
    st.info("No symbol data available")

# ════════════════════════════════════════════════════════════════
# SIDEBAR: ACTIONS
# ════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown("### 🛠️ Actions")
    
    if st.button("� Open CSV Directory"):
        csv_dir = Path(_ai_root) / "data" / "logs" / "rr_audit"
        if csv_dir.exists():
            st.code(f"open {csv_dir}")
        else:
            st.warning("CSV directory not found")
    
    if st.button("🔄 Refresh Data"):
        st.cache_data.clear()
        st.rerun()
    
    st.markdown("---")
    st.markdown("### 📖 How It Works")
    st.markdown("""
    **RR Audit tracks every signal rejected for low Risk:Reward.**
    
    For each rejection, it records:
    - Symbol, side, entry, SL, TP1-3
    - Risk & reward distances
    - Actual vs required RR
    - ATR value & multipliers
    
    **Root Causes:**
    - 🔴 SL too wide (>3%)
    - 🔴 Reward too low (<1%)
    - 🟡 Near-miss (within 10% of passing)
    
    **CSV files** are saved daily for offline analysis.
    **Bridge JSON** is updated every scan cycle.
    """)
