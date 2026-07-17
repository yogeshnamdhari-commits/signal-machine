"""
📡 Live Sheet — Real-Time Data Feed
Standalone page with aggressive auto-refresh (every 1s) showing live market data,
positions, signals, and system health. This sheet runs independently and NEVER stops.

Auto-refreshes using streamlit-autorefresh (JavaScript timer) for reliable browser-level refresh.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from streamlit_autorefresh import st_autorefresh

# ── Path Setup ───────────────────────────────────────────────────
_ai_root = Path(__file__).resolve().parent.parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from dashboard.data_bridge import reader as bridge_reader


def _format_large_number(val: float, prefix: str = "", suffix: str = "") -> str:
    """Format large numbers with K/M/B/T suffixes."""
    if val is None:
        return f"{prefix}0{suffix}"
    abs_val = abs(val)
    if abs_val >= 1e12:
        return f"{prefix}{val/1e12:.2f}T{suffix}"
    elif abs_val >= 1e9:
        return f"{prefix}{val/1e9:.2f}B{suffix}"
    elif abs_val >= 1e6:
        return f"{prefix}{val/1e6:.2f}M{suffix}"
    elif abs_val >= 1e3:
        return f"{prefix}{val/1e3:.2f}K{suffix}"
    else:
        return f"{prefix}{val:.2f}{suffix}"


def _compute_oi_bias(row: Dict) -> str:
    """Compute OI bias from engine oi_bias + oi_change_pct + price change."""
    # Primary: use engine-computed oi_bias if non-neutral
    engine_bias = str(row.get("oi_bias", "neutral"))
    if engine_bias in ("buy", "sell"):
        return engine_bias
    # Secondary: compute from oi_change_pct + price direction
    oi_chg = row.get("oi_change_pct", 0) or 0
    price_chg = row.get("change_24h", 0) or 0
    oi_regime = str(row.get("oi_regime", "neutral_oi"))
    oi_positioning = str(row.get("oi_positioning", "neutral"))
    oi_strength = row.get("oi_strength", 50) or 50
    # If engine has a non-neutral OI regime, use it
    if oi_regime == "bullish_oi":
        return "buy" if oi_chg >= 0 else "sell"
    if oi_regime == "bearish_oi":
        return "sell" if oi_chg >= 0 else "buy"
    # If engine has positioning signal, use it
    if oi_positioning in ("long_buildup", "short_covering"):
        return "buy"
    if oi_positioning in ("short_buildup", "long_unwinding"):
        return "sell"
    # Fallback: compute from oi_change_pct (lowered threshold)
    if abs(oi_chg) < 0.005:
        return "neutral"
    if oi_chg > 0:
        return "buy" if price_chg >= 0 else "sell"
    else:
        return "sell" if price_chg >= 0 else "buy"


def _compute_implied_signal(row: Dict) -> str:
    """Compute implied signal when engine signal is blank.
    Requires 3+ factors agreeing AND price action confirmation.
    Flow signal is weighted 2x (most reliable)."""
    votes = 0
    total = 0
    # Flow signal (weighted 2x — most reliable)
    fs = row.get("flow_signal", "")
    if fs and fs not in ("neutral", ""):
        total += 2
        if "buy" in fs:
            votes += 2
        elif "sell" in fs:
            votes -= 2
    # CVD bias (weighted 1.5x — second most reliable)
    cb = row.get("cvd_bias", "")
    if cb and cb not in ("neutral", ""):
        total += 1.5
        if "bullish" in cb:
            votes += 1.5
        elif "bearish" in cb:
            votes -= 1.5
    # Volume bias (weighted 1x)
    vb = row.get("vol_bias", "")
    if vb and vb not in ("neutral", ""):
        total += 1
        if vb == "buy":
            votes += 1
        elif vb == "sell":
            votes -= 1
    # Imbalance (weighted 1x)
    imb = row.get("imbalance", 0) or 0
    if imb != 0:
        total += 1
        if imb > 0.3:
            votes += 1
        elif imb < -0.3:
            votes -= 1
    # OI bias (computed) — only directional, not contrarian
    ob = _compute_oi_bias(row)
    if ob != "neutral":
        total += 1
        if ob == "buy":
            votes += 1
        elif ob == "sell":
            votes -= 1
    # Price action filter: 24h change must support direction
    price_chg = row.get("change_24h", 0) or 0
    if total < 3:
        return ""
    ratio = votes / total if total else 0
    if ratio >= 0.5:
        # Only LONG if price is not crashing hard (not contrarian)
        if price_chg < -8:
            return ""  # Too much selling = risky long
        return "long"
    elif ratio <= -0.5:
        # Only SHORT if price is not mooning (not contrarian)
        if price_chg > 8:
            return ""  # Too much buying = risky short
        return "short"
    return ""


def _estimate_liq_price(row: Dict, default_leverage: float = 20) -> Dict:
    """Estimate liquidation price zones from leverage and liquidation cluster data."""
    price = row.get("price", 0) or 0
    if price <= 0:
        return {"long_liq": 0, "short_liq": 0}
    maint_margin = 0.004  # Binance 0.4% for most pairs
    # Estimated liq zones for typical leverage
    long_liq = price * (1 - 1/default_leverage + maint_margin)
    short_liq = price * (1 + 1/default_leverage - maint_margin)
    return {"long_liq": long_liq, "short_liq": short_liq}


def _position_liq_price(entry: float, side: str, leverage: int) -> float:
    """Calculate position liquidation price."""
    if not entry or not leverage or leverage <= 0:
        return 0
    maint_margin = 0.004  # Binance 0.4%
    if side == "LONG":
        return entry * (1 - 1/leverage + maint_margin)
    else:
        return entry * (1 + 1/leverage - maint_margin)


def _render_score_breakdown(breakdown: dict, total_score: float) -> str:
    """Render a compact score breakdown bar below the position card."""
    if not breakdown:
        return ""
    pills = []
    for label, val in sorted(breakdown.items(), key=lambda x: -x[1]):
        if val >= 12:
            bg, fg = "#1e3a5f", "#60a5fa"
        elif val >= 8:
            bg, fg = "#1e293b", "#94a3b8"
        else:
            bg, fg = "#0f172a", "#475569"
        pills.append(
            f'<span style="background:{bg}; padding:2px 8px; border-radius:4px; '
            f'font-size:0.78rem; color:{fg};">{label} <strong>{val:.0f}</strong></span>'
        )
    pills_html = " ".join(pills)
    return (
        f'<div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:6px; '
        f'align-items:center;">'
        f'<span style="font-size:0.75rem; color:#64748b; margin-right:4px; font-weight:600;">SCORE BREAKDOWN</span>'
        f'{pills_html}'
        f'<span style="font-size:0.78rem; color:#94a3b8; margin-left:6px; font-weight:700;">= {total_score:.0f}</span>'
        f'</div>'
    )


# ── Page Config ──────────────────────────────────────────────────
st.set_page_config(
    page_title="📡 Live Sheet — YOG'Z INSTITUTIONAL TRADING CO.",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Custom CSS ───────────────────────────────────────────────────
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&display=swap');
    * { font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }

    :root {
        --bg-primary: #07090f; --bg-secondary: #0c1018;
        --bg-card: #0f1420; --bg-elevated: #141926;
        --border-subtle: rgba(255,255,255,0.06);
        --border-accent: rgba(0,255,136,0.15);
        --green: #00ff88; --green-glow: rgba(0,255,136,0.15);
        --red: #ff3b5c; --red-glow: rgba(255,59,92,0.15);
        --gold: #f5a623; --blue: #3b82f6; --purple: #a855f7;
        --text-primary: #e8ecf1; --text-secondary: #8892a4; --text-muted: #5a6478;
    }

    .block-container { padding: 0.4rem 0.6rem !important; max-width: 100% !important; margin: 0 !important; background: var(--bg-primary); }
    #MainMenu { visibility: hidden; } footer { visibility: hidden; } header { visibility: hidden; }

    .live-banner {
        background: linear-gradient(135deg, var(--bg-card) 0%, var(--bg-secondary) 100%);
        padding: 12px 22px; border-radius: 12px;
        border: 1px solid var(--border-subtle); margin-bottom: 10px;
        display: flex; justify-content: space-between; align-items: center;
        backdrop-filter: blur(8px);
    }
    .live-dot {
        display: inline-block; width: 10px; height: 10px;
        background: var(--green); border-radius: 50%;
        animation: pulse-glow 2s infinite; margin-right: 6px;
        box-shadow: 0 0 8px var(--green-glow);
    }
    @keyframes pulse-glow { 0%, 100% { box-shadow: 0 0 6px var(--green-glow); } 50% { box-shadow: 0 0 18px var(--green-glow); } }

    .metric-card {
        background: linear-gradient(135deg, var(--bg-card) 0%, var(--bg-secondary) 100%);
        padding: 16px 18px; border-radius: 12px;
        border: 1px solid var(--border-subtle); text-align: center;
        backdrop-filter: blur(8px); transition: all 0.25s;
    }
    .metric-card:hover { border-color: var(--border-accent); box-shadow: 0 4px 20px rgba(0,0,0,0.3); }
    .metric-card .val { font-size: 1.5rem; font-weight: 800; color: var(--text-primary); }
    .metric-card .lbl { font-size: 0.8rem; color: var(--text-secondary); margin-top: 4px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 500; }

    .ticker-row {
        display: flex; gap: 20px; padding: 10px 0;
        overflow-x: auto; white-space: nowrap;
        scrollbar-width: thin; scrollbar-color: var(--border-subtle) transparent;
    }
    .ticker-item { font-size: 0.88rem; color: var(--text-secondary); }
    .ticker-item .sym { font-weight: 700; color: var(--text-primary); }
    .ticker-item .up { color: var(--green); }
    .ticker-item .dn { color: var(--red); }

    .pos-card {
        padding: 12px 16px; border-radius: 10px; margin: 5px 0;
        font-size: 0.92rem; border-left: 3px solid;
        backdrop-filter: blur(8px); transition: all 0.2s;
    }
    .pos-long { border-color: var(--green); background: linear-gradient(90deg, var(--green-glow), transparent); }
    .pos-short { border-color: var(--red); background: linear-gradient(90deg, var(--red-glow), transparent); }
    .pos-card:hover { transform: translateX(2px); }

    .data-table { font-size: 0.88rem !important; }
    .data-table th { background: var(--bg-elevated) !important; color: var(--text-secondary) !important; font-weight: 600 !important; font-size: 0.82rem !important; text-transform: uppercase; letter-spacing: 0.06em; border-bottom: 2px solid var(--border-accent) !important; }
    .data-table td { padding: 8px 14px !important; border-bottom: 1px solid var(--border-subtle) !important; color: var(--text-primary); }
    .data-table tr:hover td { background: rgba(0,255,136,0.03) !important; }

    .section-title {
        font-size: 1.05rem; font-weight: 700; color: var(--text-primary);
        padding: 10px 16px; margin: 10px 0 6px 0;
        border: 1px solid var(--border-subtle);
        border-left: 3px solid var(--green);
        border-radius: 0 10px 10px 0;
        background: linear-gradient(90deg, var(--green-glow) 0%, var(--bg-secondary) 100%);
        letter-spacing: 0.02em;
    }

    .status-green { color: var(--green); }
    .status-red { color: var(--red); }
    .status-yellow { color: var(--gold); }
    .status-blue { color: var(--blue); }

    .countdown-bar { height: 3px; background: var(--bg-elevated); border-radius: 2px; overflow: hidden; margin-top: 4px; }
    .countdown-fill { height: 100%; background: linear-gradient(90deg, var(--blue), var(--green)); border-radius: 2px; transition: width 0.5s linear; }

    hr { border: none; height: 1px; background: linear-gradient(90deg, transparent, var(--border-accent), transparent); margin: 0.8rem 0 !important; }
</style>
""", unsafe_allow_html=True)


# ── Session State ────────────────────────────────────────────────
if "live_price_history" not in st.session_state:
    st.session_state.live_price_history = {}
if "live_equity_snapshots" not in st.session_state:
    st.session_state.live_equity_snapshots = []


# ── Refresh Interval ─────────────────────────────────────────────
REFRESH_INTERVAL = 1  # seconds — near-zero lag real-time


# ── Data Loaders (no cache — always fresh) ───────────────────────
def get_market_data() -> List[Dict]:
    """Read market data — multiple fallback layers for reliability."""
    import os as _os
    # Layer 1: Direct file read (most reliable)
    try:
        _md_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "bridge", "market_data.json")
        if _os.path.exists(_md_path):
            import json as _json
            with open(_md_path) as _f:
                raw = _json.load(_f)
            rows = raw.get("rows", [])
            if rows:
                return rows
    except Exception:
        pass
    # Layer 2: Bridge reader
    try:
        data = bridge_reader.read_market_data()
        if data:
            return data
    except Exception:
        pass
    # Layer 3: Alternative path
    try:
        from dashboard.data_bridge import MARKET_DATA_FILE
        import json as _json
        with open(MARKET_DATA_FILE) as _f:
            raw = _json.load(_f)
        rows = raw.get("rows", [])
        if rows:
            return rows
    except Exception:
        pass
    return []

def get_signals() -> List[Dict]:
    return bridge_reader.read_signals() or []

def get_positions() -> List[Dict]:
    return bridge_reader.read_positions() or []

def get_metrics() -> Dict:
    """Read metrics with direct file fallback."""
    data = bridge_reader.read_metrics()
    if data:
        return data
    # Fallback: direct file read
    try:
        from dashboard.data_bridge import METRICS_FILE
        import json as _json
        with open(METRICS_FILE) as _f:
            raw = _json.load(_f)
        return raw.get("metrics", {})
    except Exception:
        pass
    return {}

def get_status() -> Dict:
    s = bridge_reader.read_status()
    return {
        "running": s.running,
        "symbols": s.symbols,
        "signals": s.signals,
        "uptime": s.uptime,
        "last_update": s.last_update,
        "ws_connected": s.ws_connected,
    }

def get_alerts() -> List[Dict]:
    return bridge_reader.read_alerts() or []

def get_equity_history() -> List[Dict]:
    return bridge_reader.read_equity_history() or []


# ── Auto-Refresh Engine ─────────────────────────────────────────
# Uses streamlit-autorefresh (JavaScript timer) for reliable browser-level refresh.
# This is FAR more reliable than st.rerun() which can get suppressed by Streamlit.
def trigger_refresh():
    """Auto-refresh using streamlit-autorefresh JS timer. Called once per script run."""
    st_autorefresh(interval=REFRESH_INTERVAL * 1000, key="live_sheet_autorefresh")


# ── Header Banner ────────────────────────────────────────────────
def render_header():
    status = get_status()
    is_live = status["running"] and time.time() - status["last_update"] < 300
    uptime_h = status["uptime"] / 3600 if status["uptime"] else 0
    now_str = datetime.now().strftime("%H:%M:%S")

    status_color = "#00ff88" if is_live else "#ff4444"
    status_text = "LIVE" if is_live else "OFFLINE"
    ws_icon = "🟢" if status["ws_connected"] else "🔴"

    st.markdown(f"""
    <div class="live-banner">
        <div style="display:flex; align-items:center; gap:14px;">
            <span class="live-dot" style="background:{status_color};"></span>
            <span style="font-size:1.25rem; font-weight:700; color:#f1f5f9;">📡 Live Sheet</span>
            <span style="font-size:0.92rem; color:{status_color}; font-weight:600;">{status_text}</span>
            <span style="font-size:0.88rem; color:#94a3b8;">
                {status['symbols']} symbols &middot; {status['signals']} signals &middot;
                Uptime {uptime_h:.1f}h &middot; WS {ws_icon}
            </span>
        </div>
        <div style="display:flex; align-items:center; gap:14px;">
            <span style="font-size:0.85rem; color:#94a3b8;">🔄 1s refresh</span>
            <span style="font-size:0.85rem; color:#64748b;">{now_str}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)


# ── Ticker Strip ─────────────────────────────────────────────────
def render_ticker(market_data: List[Dict]):
    """Scrolling ticker of top symbols with price and change."""
    if not market_data:
        return

    df = pd.DataFrame(market_data).nlargest(20, "volume") if "volume" in pd.DataFrame(market_data).columns else pd.DataFrame(market_data).head(20)

    items = []
    for _, row in df.iterrows():
        sym = row.get("symbol", "?").replace("USDT", "")
        price = row.get("price", 0)
        funding = row.get("funding", 0)
        signal = row.get("signal", "")
        color = "#00ff88" if signal == "long" else ("#ff4444" if signal == "short" else "#94a3b8")
        items.append(
            f'<span class="ticker-item">'
            f'<span class="sym">{sym}</span> '
            f'<span style="color:{color}">${price:,.4f}</span> '
            f'<span style="color:#64748b">{funding:+.4f}%</span>'
            f'</span>'
        )

    st.markdown(
        f'<div class="ticker-row">{"&nbsp;&nbsp;│&nbsp;&nbsp;".join(items)}</div>',
        unsafe_allow_html=True,
    )


# ── Top Metrics ──────────────────────────────────────────────────
def render_top_metrics(metrics: Dict, market_data: List[Dict], signals: List[Dict]):
    portfolio = metrics.get("portfolio_value", 10000)
    daily_pnl = metrics.get("daily_pnl", 0)
    total_pnl = metrics.get("total_pnl", 0)
    win_rate = metrics.get("win_rate", 0)
    open_pos = metrics.get("open_positions", 0)
    trades_today = metrics.get("trades_today", 0)

    # Count LONG/SHORT from actual open POSITIONS, not from signals
    positions = get_positions()
    n_long = sum(1 for p in positions if p.get("side", "").upper() == "LONG")
    n_short = sum(1 for p in positions if p.get("side", "").upper() == "SHORT")
    # If positions list is empty, fall back to metrics + position data
    if n_long == 0 and n_short == 0 and open_pos > 0:
        # Last resort: derive from positions bridge data
        pass  # keep metrics open_pos as-is

    total_vol = 0
    avg_funding = 0
    if market_data:
        df = pd.DataFrame(market_data)
        total_vol = df["volume"].sum() if "volume" in df.columns else 0
        avg_funding = df["funding"].mean() if "funding" in df.columns else 0

    cols = st.columns(10)
    card_data = [
        ("💰", "Portfolio", f"${portfolio:,.0f}"),
        ("📈", "Daily PnL", f"${daily_pnl:+,.0f}"),
        ("💎", "Total PnL", f"${total_pnl:+,.0f}"),
        ("🎯", "Win Rate", f"{win_rate:.1f}%"),
        ("📍", "Positions", f"{open_pos}"),
        ("🔄", "Trades Today", f"{trades_today}"),
        ("🟢", "LONG", f"{n_long}"),
        ("🔴", "SHORT", f"{n_short}"),
        ("📊", "Vol 24h", _format_large_number(total_vol, "$")),
        ("💰", "Avg Fund", f"{avg_funding:.4f}%"),
    ]

    for col, (icon, label, value) in zip(cols, card_data):
        with col:
            st.markdown(f"""
            <div class="metric-card">
                <div class="val">{icon} {value}</div>
                <div class="lbl">{label}</div>
            </div>
            """, unsafe_allow_html=True)


# ── Live Price Chart ─────────────────────────────────────────────
def render_live_price_chart(market_data: List[Dict]):
    """Live price chart — uses real 24h data immediately + accumulates live ticks."""
    if not market_data:
        st.info("📈 No market data available.")
        return

    df = pd.DataFrame(market_data)
    vol_col = "volume_24h" if "volume_24h" in df.columns else "volume"
    if vol_col in df.columns:
        df = df.nlargest(8, vol_col)
    else:
        df = df.head(8)

    if df.empty:
        return

    fig = go.Figure()
    colors = ["#00ff88", "#3b82f6", "#f59e0b", "#a855f7", "#ff3b5c", "#06b6d4", "#ec4899", "#84cc16"]
    now = time.time()

    for i, (_, row) in enumerate(df.iterrows()):
        sym = row.get("symbol", "?")
        price = float(row.get("price", 0) or 0)
        open_24h = float(row.get("open_24h", 0) or 0)
        high_24h = float(row.get("high_24h", 0) or 0)
        low_24h = float(row.get("low_24h", 0) or 0)
        change_24h = float(row.get("change_24h", 0) or 0)

        # Track live price in session state
        if sym not in st.session_state.live_price_history:
            st.session_state.live_price_history[sym] = []
        hist = st.session_state.live_price_history[sym]
        if not hist or abs(hist[-1]["p"] - price) > price * 0.0001 or (now - hist[-1]["t"]) > 5:
            hist.append({"t": now, "p": price})
            st.session_state.live_price_history[sym] = hist[-300:]

        short = sym.replace("USDT", "")

        # Prefer accumulated live history
        live_hist = st.session_state.live_price_history.get(sym, [])
        if len(live_hist) >= 2:
            base = live_hist[0]["p"]
            y_vals = [(h["p"] / base - 1) * 100 for h in live_hist]
            fig.add_trace(go.Scatter(
                x=list(range(len(y_vals))), y=y_vals,
                mode="lines", name=short,
                line=dict(color=colors[i % len(colors)], width=2),
            ))
        elif open_24h > 0 and price > 0:
            # Fallback: build 24h path from OHLC
            pts = []
            if low_24h > 0: pts.append((low_24h / open_24h - 1) * 100)
            pts.append(0)
            if high_24h > 0: pts.append((high_24h / open_24h - 1) * 100)
            pts.append(change_24h)
            y_sorted = sorted(pts)
            fig.add_trace(go.Scatter(
                x=list(range(len(y_sorted))), y=y_sorted,
                mode="lines+markers", name=f"{short} ({change_24h:+.1f}%)",
                line=dict(color=colors[i % len(colors)], width=2),
                marker=dict(size=4),
            ))

    fig.update_layout(
        title="📈 Live Price Movement (% Change)",
        height=300, template="plotly_dark",
        margin=dict(l=10, r=10, t=40, b=10),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font=dict(size=10)),
        yaxis=dict(ticksuffix="%", zeroline=True, zerolinecolor="#333"),
        xaxis=dict(title=""),
    )
    st.plotly_chart(fig, width="stretch")


# ── Live Equity Tracker ──────────────────────────────────────────
def render_equity_tracker(metrics: Dict):
    """Render equity curve from bridge equity_history.json (real historical data)."""
    import json as _json
    from pathlib import Path

    equity_data = []
    # Try bridge file first (has real historical data)
    bridge_eq = Path(__file__).resolve().parent.parent.parent / "data" / "bridge" / "equity_history.json"
    if bridge_eq.exists():
        try:
            with open(bridge_eq) as f:
                d = _json.load(f)
            equity_data = d.get("history", [])
        except Exception:
            pass

    # Fallback: also add current session snapshot
    current_equity = metrics.get("portfolio_value", 0) or metrics.get("equity", 0)
    if current_equity > 0:
        st.session_state.live_equity_snapshots.append({"t": time.time(), "e": current_equity})
        st.session_state.live_equity_snapshots = st.session_state.live_equity_snapshots[-500:]

    # Merge: bridge history + live session snapshots
    all_points = []
    if equity_data:
        for pt in equity_data:
            all_points.append({
                "t": pt.get("timestamp", 0),
                "e": pt.get("equity", 0),
                "pnl": pt.get("pnl", 0),
                "dd": pt.get("drawdown", 0),
                "peak": pt.get("peak_equity", 0),
            })
    # Append session snapshots (avoid duplicates)
    last_t = all_points[-1]["t"] if all_points else 0
    for snap in st.session_state.live_equity_snapshots:
        if snap["t"] > last_t:
            all_points.append({"t": snap["t"], "e": snap["e"], "pnl": 0, "dd": 0, "peak": 0})

    if not all_points:
        st.info("📊 No equity data yet. Will appear as trades execute.")
        return

    equities = [p["e"] for p in all_points]
    timestamps = [p["t"] for p in all_points]
    drawdowns = [p.get("dd", 0) for p in all_points]
    peaks = [p.get("peak", 0) for p in all_points]
    initial = equities[0] if equities else 0

    # Build time labels
    time_labels = []
    for t in timestamps:
        if t > 0:
            time_labels.append(datetime.fromtimestamp(t).strftime("%m/%d %H:%M"))
        else:
            time_labels.append("")

    fig = go.Figure()

    # Main equity line
    fig.add_trace(go.Scatter(
        x=time_labels, y=equities,
        mode="lines", name="Equity",
        line=dict(color="#00ff88", width=2),
        fill="tozeroy", fillcolor="rgba(0,255,136,0.06)",
        hovertemplate="<b>Equity:</b> $%{y:,.2f}<br>%{x}<extra></extra>",
    ))

    # Peak line
    if any(p > 0 for p in peaks):
        fig.add_trace(go.Scatter(
            x=time_labels, y=peaks,
            mode="lines", name="Peak",
            line=dict(color="#3b82f6", width=1, dash="dot"),
            hovertemplate="<b>Peak:</b> $%{y:,.2f}<extra></extra>",
        ))

    # Initial line
    fig.add_hline(y=initial, line_dash="dash", line_color="#666",
                  annotation_text=f"Start: ${initial:,.0f}",
                  annotation_position="top left")

    # Current value annotation
    if equities:
        current = equities[-1]
        change_pct = ((current / initial) - 1) * 100 if initial > 0 else 0
        change_color = "#00ff88" if change_pct >= 0 else "#ff4444"
        fig.add_annotation(
            x=time_labels[-1], y=current,
            text=f"${current:,.0f} ({change_pct:+.1f}%)",
            showarrow=True, arrowhead=2, arrowcolor=change_color,
            font=dict(size=12, color=change_color),
            bgcolor="rgba(0,0,0,0.6)", bordercolor=change_color,
        )

    fig.update_layout(
        title="💰 Live Equity Curve",
        height=300, template="plotly_dark",
        margin=dict(l=10, r=10, t=40, b=10),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        yaxis=dict(tickprefix="$"),
    )
    st.plotly_chart(fig, width="stretch")

    # Summary stats below chart
    if equities:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("💰 Equity", f"${current:,.2f}", f"{change_pct:+.1f}%")
        max_dd = min(drawdowns) if drawdowns else 0
        c2.metric("📉 Max DD", f"{max_dd:.1f}%")
        peak_eq = max(peaks) if peaks else max(equities)
        c3.metric("🏔️ Peak", f"${peak_eq:,.2f}")
        n_points = len(equities)
        c4.metric("📊 Snapshots", f"{n_points}")


def _safe_build_display_df(display_cols: Dict[str, Any]) -> pd.DataFrame:
    """Build a display DataFrame from column dict, safely handling any
    2-D array, DataFrame, or length-mismatch issues."""
    _SCALAR_TYPES = (str, int, float, bool, type(None))
    # Step 1: Convert everything to flat Python lists of scalars
    safe_cols: Dict[str, list] = {}
    for name, series in display_cols.items():
        if isinstance(series, pd.Series):
            safe_cols[name] = series.tolist()
        elif isinstance(series, (list, tuple, np.ndarray)):
            safe_cols[name] = list(series)
        else:
            safe_cols[name] = [series]
    # Step 2: Flatten any non-scalar values (2D arrays, DataFrames, dicts, etc.)
    for name in list(safe_cols):
        flat = []
        for v in safe_cols[name]:
            if isinstance(v, pd.DataFrame):
                flat.append(str(v.empty) if hasattr(v, 'empty') else "—")
            elif isinstance(v, pd.Series):
                flat.append(str(v.iloc[0]) if len(v) else "—")
            elif isinstance(v, (list, tuple, np.ndarray)):
                flat.append(str(v[0]) if len(v) else "—")
            elif isinstance(v, dict):
                flat.append(str(list(v.values())[0]) if v else "—")
            elif not isinstance(v, _SCALAR_TYPES):
                flat.append(str(v))
            else:
                flat.append(v)
        safe_cols[name] = flat
    # Step 3: Normalize all columns to the same length
    if not safe_cols:
        return pd.DataFrame()
    lengths = {k: len(v) for k, v in safe_cols.items()}
    target_len = max(lengths.values()) if lengths else 0
    for name in list(safe_cols):
        col_len = len(safe_cols[name])
        if col_len < target_len:
            safe_cols[name].extend(["—"] * (target_len - col_len))
        elif col_len > target_len:
            safe_cols[name] = safe_cols[name][:target_len]
    return pd.DataFrame(safe_cols)


# ── Live Market Data Table ───────────────────────────────────────
def render_market_table(market_data: List[Dict]):
    """Full live market data table with color coding."""
    if not market_data:
        st.info("⏳ No market data. Run scanner to populate.")
        return

    df = pd.DataFrame(market_data)

    # Filter controls
    fc1, fc2, fc3, fc4 = st.columns(4)
    with fc1:
        search = st.text_input("🔍 Filter", placeholder="BTC, ETH...", key="ls_search")
    with fc2:
        show_signals_only = st.checkbox("🎯 Signals Only", key="ls_sig_only")
    with fc3:
        sort_by = st.selectbox("Sort", ["volume", "price", "open_interest", "funding"], key="ls_sort")
    with fc4:
        max_rows = st.slider("Rows", 10, 200, 50, key="ls_max_rows")

    # Apply filters
    if search:
        terms = [t.strip().upper() for t in search.split(",") if t.strip()]
        df = df[df["symbol"].apply(lambda s: any(t in s.upper() for t in terms))]
    if show_signals_only and "signal" in df.columns:
        df = df[df["signal"].isin(["long", "short"])]
    if sort_by in df.columns:
        df = df.sort_values(sort_by, ascending=False)
    df = df.head(max_rows).reset_index(drop=True)

    # Build display
    display_cols = {}
    if "symbol" in df.columns:
        display_cols["Symbol"] = df["symbol"].str.replace("USDT", "")
    if "price" in df.columns:
        display_cols["Price"] = df["price"].apply(lambda x: f"${x:,.4f}")
    if "change_24h" in df.columns:
        def _fmt_chg(v):
            if v is None or v != v:  # None or NaN
                return "—"
            color = "#00ff88" if v > 0 else ("#ff4444" if v < 0 else "#94a3b8")
            sign = "+" if v > 0 else ""
            return f"{sign}{v:.2f}%"
        display_cols["24h"] = df["change_24h"].apply(_fmt_chg)
    if "volume" in df.columns:
        display_cols["Volume 24h"] = df["volume"].apply(lambda x: _format_large_number(x, "$"))
    if "open_interest" in df.columns:
        display_cols["OI"] = df["open_interest"].apply(lambda x: _format_large_number(x, "$"))
    # OI Bias — COMPUTED from oi_change_pct + price change
    display_cols["OI Bias"] = df.apply(
        lambda r: "BUY" if _compute_oi_bias(r) == "buy" else ("SELL" if _compute_oi_bias(r) == "sell" else "Neutral"),
        axis=1,
    )
    if "oi_change_pct" in df.columns:
        display_cols["OI Δ%"] = df["oi_change_pct"].apply(lambda x: f"{x:+.2f}%" if x else "—")
    if "funding" in df.columns:
        display_cols["Funding"] = df["funding"].apply(lambda x: f"{x:+.4f}%")
    if "funding_bias" in df.columns:
        display_cols["Fund Bias"] = df["funding_bias"].apply(
            lambda x: "BUY" if str(x) in ("buy", "long_bias") else ("SELL" if str(x) in ("sell", "short_bias") else "—")
        )
    if "net_delta" in df.columns:
        display_cols["Net Delta"] = df["net_delta"].apply(lambda x: _format_large_number(x, "$"))
    if "buy_sell_ratio" in df.columns:
        display_cols["B/S Ratio"] = df["buy_sell_ratio"].apply(lambda x: f"{x:.2f}")
    if "cvd_bias" in df.columns:
        def _fmt_cvd_bias(r):
            """CVD Bias: use CVD bias if non-neutral, else derive from exchange flow buy/sell."""
            cvd = str(r.get('cvd_bias', 'neutral'))
            if cvd in ('bullish', 'strong_bullish'):
                return f"BULL {cvd.replace('_', ' ').title()}"
            if cvd in ('bearish', 'strong_bearish'):
                return f"BEAR {cvd.replace('_', ' ').title()}"
            # CVD neutral — derive from exchange flow aggressive volumes
            buy_vol = r.get('aggressive_buy_vol', 0) or 0
            sell_vol = r.get('aggressive_sell_vol', 0) or 0
            total = buy_vol + sell_vol
            if total > 0:
                ratio = buy_vol / total
                if ratio > 0.55:
                    return f"BUY ({ratio:.0%})"
                if ratio < 0.45:
                    return f"SELL ({ratio:.0%})"
            return "Neutral"
        display_cols["CVD Bias"] = df.apply(_fmt_cvd_bias, axis=1)
    if "flow_signal" in df.columns:
        def _fs_emoji(val):
            m = {"strong_buy": "BUY>>", "buy": "BUY>", "strong_sell": "SELL<<", "sell": "SELL<"}
            return m.get(str(val), "-")
        def _fmt_flow(r):
            raw = r.get('flow_signal', '')
            val = str(raw) if not isinstance(raw, str) and raw is not None else (raw or '')
            strength = r.get('flow_strength', 50)
            if not isinstance(strength, (int, float)):
                try: strength = float(strength)
                except: strength = 50
            return f"{_fs_emoji(val)} {val.replace('_', ' ').title()} ({strength:.0f})" if val else "—"
        display_cols["Flow"] = df.apply(_fmt_flow, axis=1)
    if "exchange_flow" in df.columns:
        display_cols["Ex Flow"] = df["exchange_flow"].apply(lambda x: _format_large_number(x, "$"))
    if "vol_bias" in df.columns:
        display_cols["Vol Bias"] = df["vol_bias"].apply(
            lambda x: "BUY" if str(x) in ("buy", "long_bias") else ("SELL" if str(x) in ("sell", "short_bias") else "Neutral")
        )
    # Liquidation Risk Price Zones
    display_cols["Liq Zone ↓"] = df.apply(
        lambda r: f"${_estimate_liq_price(r)['long_liq']:,.4f}", axis=1
    )
    display_cols["Liq Zone ↑"] = df.apply(
        lambda r: f"${_estimate_liq_price(r)['short_liq']:,.4f}", axis=1
    )
    # Liquidation Risk column
    if "liq_risk_level" in df.columns:
        def _lr_label(val):
            m = {"high": "HIGH", "medium": "MED", "low": "LOW"}
            return m.get(val, "?")
        display_cols["Liq Risk"] = df.apply(
            lambda r: f"{_lr_label(r['liq_risk_level'])} "
                      f"({r.get('liq_risk', 0):.0f})",
            axis=1,
        )
    # ── NEW PARAMETERS (user requested) ──
    # Sweep — uses sweep detector (price action) + liquidation engine (clusters) with confirmation
    def _sweep_display(r):
        # Priority: liquidation sweep > sweep detector signal
        liq_sweep = r.get("sweep_detected", False)
        liq_dir = r.get("sweep_direction", "")
        sw_signal = str(r.get("sw_signal", "neutral"))
        sw_count = r.get("sw_recent_count", 0)
        sw_conf = r.get("sw_avg_confidence", 0)
        if liq_sweep:
            label = "BUY" if liq_dir == "down" else ("SELL" if liq_dir == "up" else "—")
            return f"{label} ({sw_conf:.0%})"
        if sw_signal == "bullish_rejection":
            return f"BUY ({sw_count}x, {sw_conf:.0%})"
        if sw_signal == "bearish_rejection":
            return f"SELL ({sw_count}x, {sw_conf:.0%})"
        # Fallback: soft sweep near 24h extremes
        price = r.get("price", 0)
        low = r.get("low_24h", 0)
        high = r.get("high_24h", 0)
        if price and low and high and high > low:
            rng = high - low
            if (price - low) / rng < 0.12:
                return "BUY"
            if (high - price) / rng < 0.12:
                return "SELL"
        return "—"
    if "sw_signal" in df.columns or "sweep_detected" in df.columns:
        display_cols["Sweep"] = df.apply(_sweep_display, axis=1)

    # Sweep Price — use REAL sweep_price from detector, fallback to 24h extremes
    def _sweep_price_display(r):
        # Primary: use real sweep_price from detector
        sw_price = r.get("sweep_price", 0)
        if sw_price and sw_price > 0:
            conf = r.get("sw_avg_confidence", 0)
            return f"${sw_price:,.4f} ({conf:.0%})" if conf else f"${sw_price:,.4f}"
        # Fallback: use sweep direction + 24h extremes
        liq_sweep = r.get("sweep_detected", False)
        liq_dir = r.get("sweep_direction", "")
        sw_signal = str(r.get("sw_signal", "neutral"))
        low = r.get("low_24h", 0)
        high = r.get("high_24h", 0)
        if liq_sweep and liq_dir == "down":
            return f"${low:,.4f}" if low else "—"
        if liq_sweep and liq_dir == "up":
            return f"${high:,.4f}" if high else "—"
        if sw_signal == "bullish_rejection":
            return f"${low:,.4f}" if low else "—"
        if sw_signal == "bearish_rejection":
            return f"${high:,.4f}" if high else "—"
        return "—"
    if "sw_signal" in df.columns or "sweep_detected" in df.columns:
        display_cols["Sweep Price"] = df.apply(_sweep_price_display, axis=1)
    # CVD (buy/sell direction) — uses exchange flow buy/sell ratio (real data)
    if "aggressive_buy_vol" in df.columns or "cvd_bias" in df.columns:
        def _fmt_cvd_display(r):
            # Use exchange flow buy/sell volumes (always populated)
            buy_vol = r.get("aggressive_buy_vol", 0) or 0
            sell_vol = r.get("aggressive_sell_vol", 0) or 0
            total = buy_vol + sell_vol
            if total > 0:
                ratio = buy_vol / total
                if ratio > 0.55:
                    return f"BUY {ratio:.0%}"
                if ratio < 0.45:
                    return f"SELL {ratio:.0%}"
                return f"EQ {ratio:.0%}"
            # Fallback: CVD bias
            bias = str(r.get("cvd_bias", "neutral"))
            if "bullish" in bias:
                return "BUY"
            if "bearish" in bias:
                return "SELL"
            return "Neutral"
        display_cols["CVD"] = df.apply(_fmt_cvd_display, axis=1)
    # Delta (buy/sell)
    if "net_delta" in df.columns:
        display_cols["Delta"] = df["net_delta"].apply(
            lambda x: "BUY" if x > 0 else ("SELL" if x < 0 else "—")
        )
    # FVG — uses real FVG detector data with confirmation score
    def _fvg_display(r):
        alignment = str(r.get("fvg_alignment", "neutral"))
        bull_ct = r.get("fvg_bull_count", 0)
        bear_ct = r.get("fvg_bear_count", 0)
        score = r.get("fvg_score", 50) or 50
        if alignment == "bullish" and bull_ct > 0:
            return f"BULL ({bull_ct}) [{score:.0f}]"
        if alignment == "bearish" and bear_ct > 0:
            return f"BEAR ({bear_ct}) [{score:.0f}]"
        if bull_ct > 0 and bull_ct > bear_ct:
            return f"BULL ({bull_ct}) [{score:.0f}]"
        if bear_ct > 0 and bear_ct > bull_ct:
            return f"BEAR ({bear_ct}) [{score:.0f}]"
        return f"EQ [{score:.0f}]"
    if "fvg_alignment" in df.columns:
        display_cols["FVG"] = df.apply(_fvg_display, axis=1)

    # FVG Price — use REAL gap boundaries from detector, show confirmation score
    def _fvg_price_display(r):
        # Primary: use real FVG gap prices from detector
        gap_high = r.get("fvg_gap_high", 0) or 0
        gap_low = r.get("fvg_gap_low", 0) or 0
        if gap_high > 0 and gap_low > 0:
            strength = r.get("fvg_latest_strength", 0) or 0
            return f"${gap_low:,.4f}-${gap_high:,.4f} ({strength:.0%})"
        # Fallback: use alignment + 24h extremes
        alignment = str(r.get("fvg_alignment", "neutral"))
        bull_ct = r.get("fvg_bull_count", 0)
        bear_ct = r.get("fvg_bear_count", 0)
        low = r.get("low_24h", 0)
        high = r.get("high_24h", 0)
        if alignment == "bullish" or (bull_ct > 0 and bull_ct > bear_ct):
            return f"${low:,.4f}" if low else "—"
        if alignment == "bearish" or (bear_ct > 0 and bear_ct > bull_ct):
            return f"${high:,.4f}" if high else "—"
        return "—"
    if "fvg_alignment" in df.columns:
        display_cols["FVG Price"] = df.apply(_fvg_price_display, axis=1)
    if "regime" in df.columns:
        def _regime_label(val):
            m = {"trending_bull": "[UP]", "trending_bear": "[DN]", "breakout": "[BO]",
                 "range": "[RG]", "volatile": "[VO]", "compression": "[CP]"}
            return m.get(str(val), "?")
        def _fmt_regime(r):
            val = r.get('regime', '')
            if not isinstance(val, str):
                try:
                    val = str(val) if val is not None else ''
                except Exception:
                    val = ''
            return f"{_regime_label(val)} {val.replace('_', ' ').title()}" if val else "—"
        display_cols["Regime"] = df.apply(_fmt_regime, axis=1)
    if "regime_confidence_pct" in df.columns:
        display_cols["Reg Conf"] = df["regime_confidence_pct"].apply(lambda x: f"{x:.0f}%")
    # Signal — show engine signal or computed implied signal
    def _get_signal(r):
        sig = r.get("signal", "")
        if sig:
            return "LONG" if sig == "long" else "SHORT"
        implied = _compute_implied_signal(r)
        if implied:
            return f"{implied.upper()} (implied)"
        return "—"
    display_cols["Signal"] = df.apply(_get_signal, axis=1)

    display = _safe_build_display_df(display_cols)

    st.markdown(f'<div class="section-title">📊 Live Market Data ({len(display)} symbols)</div>', unsafe_allow_html=True)

    st.dataframe(
        display,
        width="stretch",
        height=min(500, 40 + len(display) * 33),
        hide_index=True,
    )


# ── Live Positions ───────────────────────────────────────────────
def render_live_positions(positions: List[Dict]):
    """Show open positions with live PnL, adaptive SL/TP, and risk metrics."""
    st.markdown(f'<div class="section-title">📍 Open Positions ({len(positions)})</div>', unsafe_allow_html=True)

    if not positions:
        st.info("📭 No open positions")
        return

    total_pnl = sum(p.get("unrealized_pnl", 0) for p in positions)

    for pos in positions:
        sym = pos.get("symbol", "?")
        side = pos.get("side", "LONG")
        entry = pos.get("entry_price", 0)
        current = pos.get("current_price", 0)
        qty = pos.get("quantity", 0)
        pnl = pos.get("unrealized_pnl", 0)
        sl = pos.get("stop_loss", 0)
        tp = pos.get("take_profit", 0)
        leverage = pos.get("leverage", 1)

        # ── Professional metrics ──
        r_multiple = pos.get("r_multiple", 0)
        risk_pct = pos.get("risk_pct", 0)
        risk_reward = pos.get("risk_reward", 0)
        confidence = pos.get("confidence", 0)
        inst_score = pos.get("institutional_score", 0)
        score_breakdown = pos.get("score_breakdown", {})
        regime = pos.get("regime", "unknown")

        icon = "🟢" if side == "LONG" else "🔴"
        css = "pos-long" if side == "LONG" else "pos-short"
        pnl_color = "#00ff88" if pnl >= 0 else "#ff4444"
        pnl_pct = (pnl / (entry * qty) * 100) if (entry * qty) > 0 else 0

        # Distance to SL/TP as percentage
        sl_dist = abs(entry - sl) / entry * 100 if entry and sl else 0
        tp_dist = abs(tp - entry) / entry * 100 if tp and entry else 0

        # Liquidation price
        liq_price = _position_liq_price(entry, side, leverage)
        liq_dist = abs(liq_price - current) / current * 100 if liq_price and current else 0
        liq_color = "#ff4444" if liq_dist < 5 else ("#f59e0b" if liq_dist < 15 else "#94a3b8")

        # Visual progress bar: position between SL and TP
        if side == "LONG" and sl < entry < tp and entry != sl:
            progress = max(0, min(1, (current - sl) / (tp - sl))) if tp != sl else 0.5
        elif side == "SHORT" and tp < entry < sl and entry != sl:
            progress = max(0, min(1, (sl - current) / (sl - tp))) if sl != tp else 0.5
        else:
            progress = 0.5

        # R-Multiple color
        r_color = "#00ff88" if r_multiple >= 1 else ("#f59e0b" if r_multiple >= 0 else "#ff4444")
        # Regime icon
        regime_icons = {"trending_bull": "📈", "trending_bear": "📉", "breakout": "🚀",
                        "range": "↔️", "volatile": "⚡", "compression": "🔍",
                        # Legacy compat
                        "trending_up": "📈", "trending_down": "📉",
                        "ranging": "↔️", "quiet": "😴", "reversal": "🔄",
                        "consolidation": "🔄", "unknown": "❓"}
        regime_icon = regime_icons.get(regime, "❓")
        # Confidence bar
        conf_bar = "█" * int(confidence * 10) + "░" * (10 - int(confidence * 10))
        # Position value + margin
        pos_value = entry * qty
        margin = pos_value / max(leverage, 1)

        # ── Card Part 1: Header ──
        st.markdown(f"""
        <div class="pos-card {css}" style="padding:8px 12px; margin:2px 0;">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div style="display:flex; align-items:center; gap:5px;">
                    <strong style="font-size:0.88rem;">{icon} {side} {sym}</strong>
                    <span style="background:#1e293b; padding:1px 4px; border-radius:3px; font-size:0.6rem; color:#94a3b8;">{leverage}x</span>
                    <span style="font-size:0.62rem; color:#64748b;">{regime_icon}</span>
                </div>
                <span style="color:{pnl_color}; font-weight:bold; font-size:0.88rem;">${pnl:+,.2f} ({pnl_pct:+.1f}%)</span>
            </div>
            <div style="font-size:0.7rem; color:#94a3b8; margin-top:2px; display:flex; gap:8px; flex-wrap:wrap;">
                <span>E: <strong>${entry:,.4f}</strong></span>
                <span>C: <strong>${current:,.4f}</strong></span>
                <span style="color:#ff4444;">SL: <strong>${sl:,.4f}</strong> ({sl_dist:.1f}%)</span>
                <span style="color:#00ff88;">TP: <strong>${tp:,.4f}</strong> (+{tp_dist:.1f}%)</span>
                <span style="color:{liq_color};">🚨 LIQ: <strong>${liq_price:,.4f}</strong> ({liq_dist:.1f}%)</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ── Card Part 2: Progress + Metrics + Score (iframe for reliable rendering) ──
        _pills = []
        _pills.append(f'<span style="background:#1e293b;padding:1px 4px;border-radius:3px;color:#94a3b8;font-size:0.65rem;">R:R <span style="color:{"#00ff88" if risk_reward >= 2 else "#f59e0b" if risk_reward >= 1.5 else "#ff4444"};">{risk_reward:.1f}x</span></span>')
        _pills.append(f'<span style="background:#1e293b;padding:1px 4px;border-radius:3px;color:#94a3b8;font-size:0.65rem;">Risk <span style="color:#ff4444;">{risk_pct:.2f}%</span></span>')
        _pills.append(f'<span style="background:#1e293b;padding:1px 4px;border-radius:3px;color:#94a3b8;font-size:0.65rem;">Conf <span style="color:{"#00ff88" if confidence >= 0.6 else "#f59e0b"};">{confidence:.0%}</span></span>')
        _pills.append(f'<span style="background:#1e293b;padding:1px 4px;border-radius:3px;color:#94a3b8;font-size:0.65rem;">Score <span style="color:#f1f5f9;font-weight:bold;">{inst_score:.0f}</span></span>')
        _pills.append(f'<span style="background:#1e293b;padding:1px 4px;border-radius:3px;color:#94a3b8;font-size:0.65rem;">Margin <span style="color:#f1f5f9;">{_format_large_number(margin, "$")}</span></span>')
        _pills.append(f'<span style="background:#1e293b;padding:1px 4px;border-radius:3px;color:#94a3b8;font-size:0.65rem;">Qty <span style="color:#f1f5f9;">{qty:,.0f}</span></span>')
        _pills_html = " ".join(_pills)
        _score_html = _render_score_breakdown(score_breakdown, inst_score)

        _card_part2 = f"""<!DOCTYPE html><html><head><style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; background: transparent; color: #ccc; padding: 0 4px; }}
</style></head><body>
<div style="height:3px;background:#1e293b;border-radius:2px;">
    <div style="height:100%;width:{progress*100:.0f}%;background:linear-gradient(90deg,#ff4444,#f59e0b 50%,#00ff88);border-radius:2px;"></div>
</div>
<div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:3px;align-items:center;">
    {_pills_html}
</div>
{_score_html}
</body></html>"""
        components.html(_card_part2, height=55, scrolling=False)

    # Total PnL bar
    pnl_color = "#00ff88" if total_pnl >= 0 else "#ff4444"
    total_margin = sum(p.get("entry_price", 0) * p.get("quantity", 0) / max(p.get("leverage", 1), 1) for p in positions)
    total_margin_pct = (total_pnl / total_margin * 100) if total_margin > 0 else 0
    st.markdown(f"""
    <div style="display:flex; justify-content:space-between; padding:8px 0; font-weight:bold; font-size:1.1rem;">
        <span style="color:#94a3b8; font-size:0.85rem;">{len(positions)} positions | Margin: {_format_large_number(total_margin, "$")}</span>
        <span style="color:{pnl_color};">Total Unrealized: ${total_pnl:+,.2f} ({total_margin_pct:+.2f}%)</span>
    </div>
    """, unsafe_allow_html=True)


# ── Live Signals Feed ────────────────────────────────────────────
def render_live_signals(signals: List[Dict]):
    """Show live signal feed with intraday quality metrics, adaptive SL/TP, and session context."""
    st.markdown(f'<div class="section-title">🎯 Intraday Signals ({len(signals)})</div>', unsafe_allow_html=True)

    if not signals:
        st.info("🔍 No active signals")
        return

    # Summary row: count by quality tier
    tier_counts = {"A": 0, "B": 0, "C": 0}
    session_counts = {}
    for sig in signals:
        intraday = sig.get("intraday", {})
        tier = intraday.get("quality_tier", "C")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        sess = intraday.get("session", "unknown")
        session_counts[sess] = session_counts.get(sess, 0) + 1
    
    # Compact tier summary
    tier_summary = " | ".join([f"<span style='color:{'#00ff88' if t == 'A' else '#f59e0b' if t == 'B' else '#888'};'>{t}: {c}</span>" for t, c in tier_counts.items() if c > 0])
    if tier_summary:
        st.markdown(f'<div style="font-size:0.75rem; margin-bottom:6px;">Quality: {tier_summary}</div>', unsafe_allow_html=True)

    for sig in signals[:15]:
        sym = sig.get("symbol", "?")
        sig_type = sig.get("type", "LONG")
        conf = sig.get("confidence", 0)
        entry = sig.get("entry_price", 0)
        sl = sig.get("stop_loss", 0)
        tp = sig.get("take_profit", 0)
        tp1 = sig.get("take_profit_1", tp)
        tp2 = sig.get("take_profit_2", 0)
        tp3 = sig.get("take_profit_3", 0)
        regime = sig.get("regime", "unknown")
        inst_score = sig.get("institutional_score", 0)
        score_breakdown = sig.get("score_breakdown", {})
        rr = sig.get("risk_reward", 0)
        rr_1 = sig.get("rr_1", 0)
        rr_2 = sig.get("rr_2", 0)
        rr_3 = sig.get("rr_3", 0)
        sl_source = sig.get("sl_source", "")
        tp1_source = sig.get("tp1_source", "")
        if rr <= 0:
            risk = abs(entry - sl) if entry and sl else 0
            reward = abs(tp - entry) if tp and entry else 0
            rr = round(reward / risk, 1) if risk > 0 else 0

        # Intraday quality data
        intraday = sig.get("intraday", {})
        quality_score = intraday.get("quality_score", 0)
        quality_tier = intraday.get("quality_tier", "C")
        vol_regime = intraday.get("volatility_regime", "")
        session = intraday.get("session", "")
        mtf_aligned = intraday.get("mtf_aligned", False)
        mtf_score = intraday.get("mtf_score", 0)
        trend = intraday.get("trend", "")
        trend_strength = intraday.get("trend_strength", 0)
        nearest_support = intraday.get("nearest_support", 0)
        nearest_resistance = intraday.get("nearest_resistance", 0)
        atr_5m = intraday.get("atr_5m", 0)
        suggested_lev = intraday.get("suggested_leverage", 5)
        risk_pct = intraday.get("risk_pct", 1.0)

        icon = "🟢" if sig_type == "LONG" else "🔴"
        conf_bar = int(conf * 100)

        # Quality tier styling
        tier_border = {"A": "#00ff88", "B": "#f59e0b", "C": "#334155"}.get(quality_tier, "#334155")
        tier_bg = {"A": "rgba(0,255,136,0.08)", "B": "rgba(245,158,11,0.06)", "C": "transparent"}.get(quality_tier, "transparent")
        
        # MTF indicator
        mtf_html = f'<span style="color:#00ff88;">✅ MTF</span>' if mtf_aligned else f'<span style="color:#f59e0b;">⚠️ MTF {mtf_score:.0%}</span>'
        
        # Trend indicator
        trend_icons = {"bullish": "📈", "bearish": "📉", "sideways": "➡️"}
        trend_html = f'{trend_icons.get(trend, "➡️")} {trend.title()} ({trend_strength:.0%})' if trend else ""
        
        # Key levels
        levels_html = ""
        if nearest_support > 0 or nearest_resistance > 0:
            parts = []
            if nearest_support > 0:
                sup_dist = abs(entry - nearest_support) / entry * 100 if entry else 0
                parts.append(f'<span style="color:#3b82f6;">🔵 S ${nearest_support:,.2f} ({sup_dist:.1f}%)</span>')
            if nearest_resistance > 0:
                res_dist = abs(nearest_resistance - entry) / entry * 100 if entry else 0
                parts.append(f'<span style="color:#f59e0b;">🟡 R ${nearest_resistance:,.2f} ({res_dist:.1f}%)</span>')
            levels_html = " &nbsp; ".join(parts)

        # SL/TP distance visual
        sl_dist = abs(entry - sl) / entry * 100 if entry and sl else 0
        tp_dist = abs(tp - entry) / entry * 100 if tp and entry else 0

        # Probability-based detector outputs
        inst_prob = sig.get("institutional_probability", 0)
        accum_prob = sig.get("accumulation_probability", 0)
        whale_prob = sig.get("whale_probability", 0)
        inst_prob_conf = sig.get("inst_prob_confidence", 0)
        accum_prob_conf = sig.get("accum_prob_confidence", 0)
        whale_prob_conf = sig.get("whale_prob_confidence", 0)
        inst_p_color = '#00ff88' if inst_prob > 0.5 else ('#f59e0b' if inst_prob > 0.3 else '#64748b')
        accum_p_color = '#00ff88' if accum_prob > 0.5 else ('#f59e0b' if accum_prob > 0.3 else '#64748b')
        whale_p_color = '#00ff88' if whale_prob > 0.5 else ('#f59e0b' if whale_prob > 0.3 else '#64748b')

        st.markdown(f"""
        <div style="padding:8px 12px; margin:4px 0; background:{tier_bg}; border-radius:8px;
                    display:flex; flex-direction:column; gap:4px; font-size:0.85rem;
                    border-left: 3px solid {tier_border};">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <div>
                    {icon} <strong style="font-size:0.95rem;">{sym}</strong>
                    <span style="color:#64748b; margin-left:6px; font-size:0.75rem;">{regime}</span>
                </div>
                <div style="display:flex; gap:8px; align-items:center;">
                    <span style="color:{tier_border}; font-size:0.7rem; font-weight:600;">{quality_tier}-TIER {quality_score:.0f}</span>
                    <span style="background:#1e293b; padding:2px 6px; border-radius:4px; font-size:0.7rem; color:#94a3b8;">{session.title()}</span>
                </div>
            </div>
            <div style="display:flex; gap:12px; font-size:0.82rem; flex-wrap:wrap;">
                <span>Entry: <strong>${entry:,.4f}</strong></span>
                <span style="color:#ff4444;">SL: <strong>${sl:,.4f}</strong> <span style="font-size:0.72rem;">({sl_dist:.2f}%)</span>{f' <span style="font-size:0.6rem;color:#666;">{sl_source}</span>' if sl_source else ''}</span>
                <span style="color:#00ff88;">TP: <strong>${tp:,.4f}</strong> <span style="font-size:0.72rem;">({tp_dist:.2f}%)</span>{f' <span style="font-size:0.6rem;color:#666;">{tp1_source}</span>' if tp1_source else ''}</span>
            </div>
            {f'''<div style="display:flex; gap:8px; font-size:0.72rem; margin-top:1px; flex-wrap:wrap;">
                {f"<span style=\"color:#3b82f6;\">🎯 TP2 ${tp2:,.4f} ({rr_2:.1f}x)</span>" if tp2 > 0 else ""}
                {f"<span style=\"color:#8b5cf6;\">🚀 TP3 ${tp3:,.4f} ({rr_3:.1f}x)</span>" if tp3 > 0 else ""}
            </div>''' if tp2 > 0 else ''}
            <div style="display:flex; gap:10px; font-size:0.75rem; color:#94a3b8; flex-wrap:wrap;">
                <span>Conf: <strong style="color:#fff;">{conf:.0%}</strong></span>
                <span>R:R: <strong style="color:{'#00ff88' if rr >= 2 else '#f59e0b' if rr >= 1.5 else '#ff4444'};">{rr:.1f}x</strong></span>
                {f'<span style="font-size:0.68rem;color:#3b82f6;">→{rr_2:.1f}x</span>' if rr_2 > 0 else ''}
                {f'<span style="font-size:0.68rem;color:#8b5cf6;">→{rr_3:.1f}x</span>' if rr_3 > 0 else ''}
                {mtf_html}
                <span>{trend_html}</span>
                <span>Vol: <span style="color:{'#ff4444' if vol_regime == 'extreme' else '#f59e0b' if vol_regime == 'high' else '#3b82f6' if vol_regime == 'low' else '#94a3b8'};">{vol_regime.title()}</span></span>
                <span>ATR: <strong>{atr_5m:.4f}</strong></span>
                <span>Lev: <strong>{suggested_lev}x</strong></span>
                <span>Risk: <strong>{risk_pct:.1f}%</strong></span>
            </div>
            <div style="display:flex; gap:10px; font-size:0.73rem; flex-wrap:wrap; margin-top:2px;">
                <span style="color:{inst_p_color};">🏦 Inst <strong>{inst_prob:.0%}</strong> <span style="color:#64748b;">(±{inst_prob_conf:.0%})</span></span>
                <span style="color:{accum_p_color};">📈 Accum <strong>{accum_prob:.0%}</strong> <span style="color:#64748b;">(±{accum_prob_conf:.0%})</span></span>
                <span style="color:{whale_p_color};">🐋 Whale <strong>{whale_prob:.0%}</strong> <span style="color:#64748b;">(±{whale_prob_conf:.0%})</span></span>
            </div>
            {f'<div style="display:flex; gap:8px; font-size:0.72rem; margin-top:2px;">{levels_html}</div>' if levels_html else ''}
            {_render_score_breakdown(score_breakdown, inst_score)}
        </div>
        """, unsafe_allow_html=True)


# ── Live Alerts Feed ─────────────────────────────────────────────
def render_live_alerts(alerts: List[Dict]):
    """Show recent alerts."""
    st.markdown(f'<div class="section-title">🔔 Recent Alerts ({len(alerts)})</div>', unsafe_allow_html=True)

    if not alerts:
        st.info("🔕 No recent alerts")
        return

    for alert in alerts[:10]:
        level = alert.get("level", "info")
        msg = alert.get("message", "")
        ts = alert.get("timestamp", 0)
        time_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "—"

        level_icons = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️", "success": "✅"}
        icon = level_icons.get(level, "ℹ️")
        level_colors = {"critical": "#ef4444", "warning": "#f59e0b", "info": "#3b82f6", "success": "#22c55e"}
        color = level_colors.get(level, "#94a3b8")

        st.markdown(f"""
        <div style="padding:5px 10px; margin:2px 0; background:#0f172a; border-radius:5px;
                    border-left:3px solid {color}; font-size:0.82rem;">
            {icon} <span style="color:{color};">[{level.upper()}]</span> {msg}
            <span style="float:right; color:#64748b;">{time_str}</span>
        </div>
        """, unsafe_allow_html=True)


# ── System Health ────────────────────────────────────────────────
def render_system_health(status: Dict):
    """Compact system health indicators."""
    ws_ok = status.get("ws_connected", False)
    running = status.get("running", False)
    uptime = status.get("uptime", 0)

    import psutil
    cpu = psutil.cpu_percent(interval=0.1)
    mem = psutil.virtual_memory().percent

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.markdown(f'<div class="metric-card"><div class="val">{"🟢" if running else "🔴"}</div><div class="lbl">Engine</div></div>', unsafe_allow_html=True)
    with c2:
        st.markdown(f'<div class="metric-card"><div class="val">{"🟢" if ws_ok else "🔴"}</div><div class="lbl">WebSocket</div></div>', unsafe_allow_html=True)
    with c3:
        st.markdown(f'<div class="metric-card"><div class="val">{uptime/3600:.1f}h</div><div class="lbl">Uptime</div></div>', unsafe_allow_html=True)
    with c4:
        cpu_color = "#00ff88" if cpu < 70 else ("#f59e0b" if cpu < 90 else "#ff4444")
        st.markdown(f'<div class="metric-card"><div class="val" style="color:{cpu_color}">{cpu:.0f}%</div><div class="lbl">CPU</div></div>', unsafe_allow_html=True)
    with c5:
        mem_color = "#00ff88" if mem < 70 else ("#f59e0b" if mem < 90 else "#ff4444")
        st.markdown(f'<div class="metric-card"><div class="val" style="color:{mem_color}">{mem:.0f}%</div><div class="lbl">Memory</div></div>', unsafe_allow_html=True)


# ── Main Render ──────────────────────────────────────────────────
def main():
    render_start = time.time()

    # ── Auto-Refresh: Set up JS timer FIRST (before any rendering) ──
    # This injects a JavaScript timer that triggers a full page reload every REFRESH_INTERVAL.
    # Much more reliable than st.rerun() which can get suppressed by Streamlit.
    trigger_refresh()

    # ── Load all data ──
    market_data = get_market_data()
    signals = get_signals()
    positions = get_positions()
    metrics = get_metrics()
    status = get_status()
    alerts = get_alerts()

    # ── Header ──
    render_header()

    # ── Ticker Strip ──
    render_ticker(market_data)

    # ── Top Metrics ──
    render_top_metrics(metrics, market_data, signals)

    # ── Data Provenance & Freshness ──
    try:
        freshness = bridge_reader.read_market_data_freshness()
        if freshness.get("age", 9999) > 0:
            age_s = freshness.get("age", 0)
            if age_s < 60:
                fresh_badge = "🟢 LIVE"
            elif age_s < 300:
                fresh_badge = "🟡 STALE"
            else:
                fresh_badge = "🔴 OFFLINE"
            bridge_time = freshness.get("bridge_time", "?")
            n_rows = freshness.get("rows", 0)
            st.caption(
                f"{fresh_badge} Data: {bridge_time} "
                f"| {age_s:.0f}s ago | {n_rows} symbols | "
                f"Sources: WS ticker + markPrice + forceOrder | REST OI + aggTrade"
            )
    except Exception:
        pass

    # ── Layout: Charts + Positions + Signals ──
    col_left, col_right = st.columns([2, 1])

    with col_left:
        # Live Price Chart
        render_live_price_chart(market_data)

        # Equity Tracker
        render_equity_tracker(metrics)

        # Market Data Table
        render_market_table(market_data)

    with col_right:
        # Positions
        render_live_positions(positions)

        # Signals
        render_live_signals(signals)

        # Alerts
        render_live_alerts(alerts)

    # ── System Health (bottom) ──
    st.markdown('<div class="section-title">⚙️ System Health</div>', unsafe_allow_html=True)
    render_system_health(status)

    # ── Data Health Widget ──
    data_quality = bridge_reader.read_data_quality()
    if data_quality:
        dq_overall = data_quality.get("overall_score", 100)
        dq_md = data_quality.get("market_data_status", "OK")
        dq_fund = data_quality.get("funding_status", "OK")
        dq_oi = data_quality.get("oi_status", "OK")
        dq_ef = data_quality.get("exchange_flow_status", "OK")
        dq_issues = data_quality.get("total_issues", 0)
        dq_syms = data_quality.get("total_symbols", 0)

        def _dq_color(s):
            return {"OK": "#00ff88", "DEGRADED": "#f59e0b", "WARN": "#ff8800", "CRITICAL": "#ff4444"}.get(s, "#888")

        def _dq_icon(s):
            return {"OK": "✅", "DEGRADED": "⚠️", "WARN": "🔶", "CRITICAL": "🔴"}.get(s, "❓")

        overall_color = _dq_color(
            "OK" if dq_overall >= 90 else "DEGRADED" if dq_overall >= 70 else "WARN" if dq_overall >= 40 else "CRITICAL"
        )

        st.markdown(f"""
        <div style="display:flex; gap:16px; align-items:center; padding:8px 16px;
                    background:#1a1a2e; border-radius:8px; border:1px solid #2a2a4e;
                    margin-top:8px; font-size:0.82rem;">
            <span style="font-weight:600; color:{overall_color};">🩺 Data Health {dq_overall:.0f}</span>
            <span style="color:#555;">│</span>
            <span>{_dq_icon(dq_md)} Market Data: <strong style="color:{_dq_color(dq_md)};">{dq_md}</strong></span>
            <span>{_dq_icon(dq_fund)} Funding: <strong style="color:{_dq_color(dq_fund)};">{dq_fund}</strong></span>
            <span>{_dq_icon(dq_oi)} Open Interest: <strong style="color:{_dq_color(dq_oi)};">{dq_oi}</strong></span>
            <span>{_dq_icon(dq_ef)} Exchange Flow: <strong style="color:{_dq_color(dq_ef)};">{dq_ef}</strong></span>
            <span style="color:#555;">│</span>
            <span style="color:#64748b;">{dq_syms} symbols · {dq_issues} issues</span>
        </div>
        """, unsafe_allow_html=True)

    # ── Render time ──
    render_ms = (time.time() - render_start) * 1000
    st.caption(f"⚡ Rendered in {render_ms:.0f}ms | Auto-refresh every {REFRESH_INTERVAL}s | {datetime.now().strftime('%H:%M:%S')}")


if __name__ == "__main__":
    main()
