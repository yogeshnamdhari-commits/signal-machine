"""
📊 EMA V5 Scanner — Institutional Dashboard
Isolated dashboard page for the EMA_V5 strategy.
Reads exclusively from the bridge file (ema_v5.json).
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

import streamlit as st
from streamlit_autorefresh import st_autorefresh

# ── Path Setup ───────────────────────────────────────────────────
_ai_root = Path(__file__).resolve().parent.parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

st.set_page_config(page_title="EMA V5 Scanner", page_icon="📊", layout="wide", initial_sidebar_state="collapsed")

# ══════════════════════════════════════════════════════════════════
# THEME — Matches main DeltaTerminal exactly
# ══════════════════════════════════════════════════════════════════
st.markdown("""<style>
    .block-container{padding-top:.2rem;padding-bottom:.2rem;max-width:100%}
    [data-testid="stHeader"]{background:transparent}
    .m-box{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:4px 8px;text-align:center;flex:1;min-width:0}
    .m-val{font-size:1rem;font-weight:bold;color:#58a6ff;line-height:1.2}
    .m-lbl{font-size:.55rem;color:#8b949e;text-transform:uppercase;line-height:1.1}
    .sg{color:#3fb950;font-weight:bold}.sr{color:#f85149;font-weight:bold}
    .bar{height:5px;background:#30363d;border-radius:3px;overflow:hidden;margin-top:1px}
    .bar-fill{height:100%;border-radius:3px}
    .bar-blue{background:#58a6ff}.bar-red{background:#f85149}.bar-green{background:#3fb950}
    .state-box{background:#161b22;border:1px solid #30363d;border-radius:4px;padding:4px 8px;text-align:center;font-size:.75rem}
    .state-val{font-size:1.1rem;font-weight:bold;line-height:1.3}
    .state-lbl{font-size:.55rem;color:#8b949e;text-transform:uppercase}
    .health-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px}
    .health-on{background:#3fb950}.health-warn{background:#d29922}.health-off{background:#f85149}
    .signal-long{border-left:3px solid #3fb950;padding:6px 10px;margin:2px 0;border-radius:4px;background:#0d1117}
    .signal-short{border-left:3px solid #f85149;padding:6px 10px;margin:2px 0;border-radius:4px;background:#0d1117}
    .detail-card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px 16px;margin:8px 0}
    .detail-title{font-size:.85rem;font-weight:bold;color:#c9d1d9;margin-bottom:6px}
    .detail-row{display:flex;justify-content:space-between;font-size:.75rem;padding:2px 0;border-bottom:1px solid #21262d}
    .detail-key{color:#8b949e}.detail-val{color:#c9d1d9;font-weight:500}
</style>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════
# DATA LAYER — Read from bridge only
# ══════════════════════════════════════════════════════════════════
BRIDGE = Path(__file__).resolve().parent.parent.parent / "data" / "bridge"


@st.cache_data(ttl=1)
def load_ema_v5() -> Dict:
    """Load EMA_V5 data from bridge file."""
    fp = BRIDGE / "ema_v5.json"
    if not fp.exists():
        return {}
    try:
        with open(fp) as f:
            data = json.load(f)
        return data.get("ema_v5", {})
    except Exception:
        return {}


@st.cache_data(ttl=1)
def load_engine_status() -> Dict:
    """Load engine status from bridge."""
    fp = BRIDGE / "status.json"
    if not fp.exists():
        return {}
    try:
        with open(fp) as f:
            data = json.load(f)
        return data.get("status", {})
    except Exception:
        return {}


def _ts(t: float) -> str:
    """Format timestamp to HH:MM in selected timezone."""
    if not t:
        return "—"
    tz = st.session_state.get("tz_offset", 0)
    tzinfo = timezone(timedelta(hours=tz))
    return datetime.fromtimestamp(t, tz=tzinfo).strftime("%H:%M")


def _dt(t: float) -> str:
    """Format timestamp to full datetime in selected timezone."""
    if not t:
        return "—"
    tz = st.session_state.get("tz_offset", 0)
    tzinfo = timezone(timedelta(hours=tz))
    return datetime.fromtimestamp(t, tz=tzinfo).strftime("%Y-%m-%d %H:%M:%S")


def _age(ts: float) -> str:
    """Format age as human-readable duration."""
    if not ts:
        return "—"
    diff = time.time() - ts
    if diff < 0:
        return "just now"
    mins = int(diff // 60)
    hours = mins // 60
    days = hours // 24
    if days > 0:
        return f"{days}d {hours % 24}h"
    if hours > 0:
        return f"{hours}h {mins % 60}m"
    return f"{mins}m"


def _p(v: float) -> str:
    """Format price with dynamic precision."""
    if v is None or v == 0:
        return "—"
    if v >= 100:
        return f"{v:.2f}"
    if v >= 1:
        return f"{v:.4f}"
    if v >= 0.01:
        return f"{v:.5f}"
    return f"{v:.6f}"


def _state_color(state: str) -> str:
    """Map state to color."""
    return {
        "BUY_MODE": "#3fb950",
        "SELL_MODE": "#f85149",
        "WAITING_PULLBACK": "#d29922",
        "WAITING_CONFIRMATION": "#58a6ff",
        "ACTIVE_BUY": "#3fb950",
        "ACTIVE_SELL": "#f85149",
        "TRADE_CLOSED": "#8b949e",
        "NO_TREND": "#484f58",
    }.get(state, "#484f58")


def _state_icon(state: str) -> str:
    """Map state to emoji icon."""
    return {
        "BUY_MODE": "🟢",
        "SELL_MODE": "🔴",
        "WAITING_PULLBACK": "🟡",
        "WAITING_CONFIRMATION": "🔵",
        "ACTIVE_BUY": "✅🟢",
        "ACTIVE_SELL": "✅🔴",
        "TRADE_CLOSED": "⬜",
        "NO_TREND": "⚫",
    }.get(state, "❓")


# ══════════════════════════════════════════════════════════════════
# MAIN PAGE
# ══════════════════════════════════════════════════════════════════
def main():
    # ── Auto-refresh every 120 seconds ──
    st_autorefresh(interval=120 * 1000, key="ema_v5_refresh")

    # ── Timezone selector ──
    TZ_OPTIONS = {
        "UTC": 0, "IST (India)": 5.5, "CST (China)": 8,
        "JST (Japan)": 9, "EST (US East)": -5, "PST (US West)": -8,
        "CET (Europe)": 1, "AEST (Australia)": 10,
    }
    if "tz_offset" not in st.session_state:
        st.session_state.tz_offset = 5.5  # Default IST
    tz_labels = list(TZ_OPTIONS.keys())
    tz_current = st.session_state.get("tz_name", "IST (India)")
    selected_tz = st.sidebar.selectbox(
        "🕐 Timezone", tz_labels,
        index=tz_labels.index(tz_current) if tz_current in tz_labels else 1,
        key="tz_selector",
    )
    st.session_state.tz_offset = TZ_OPTIONS[selected_tz]
    st.session_state.tz_name = selected_tz

    data = load_ema_v5()
    eng_status = load_engine_status()

    if not data or data.get("_stale"):
        st.warning("⚠️ EMA_V5 bridge data unavailable or stale. Waiting for engine to write data...")
        st.info("The EMA_V5 scanner writes to `data/bridge/ema_v5.json`. Ensure the engine is running.")
        st.stop()

    scanner = data.get("scanner", {})
    states = data.get("states", {})
    state_counts = data.get("state_counts", {})
    signals = data.get("signals", [])
    health = data.get("health", {})

    # ════════════════════════════════════════════════════════════════
    # DIAGNOSTIC: Engine running but 0 active symbols = broken scan loop
    # ════════════════════════════════════════════════════════════════
    _eng_symbols = eng_status.get("symbols", -1)
    _scan_count = scanner.get("scan_count", 0)
    _is_running = eng_status.get("running", False)
    if _is_running and _eng_symbols == 0:
        st.error(
            "🚨 **ENGINE HAS 0 ACTIVE SYMBOLS** — The scan loop is running but has nothing to scan. "
            "This means `_load_symbols()` failed to populate `active_symbols`. "
            "Check engine logs for `SYMBOL_LOAD` warnings. "
            "A restart is needed after fixing the symbol loading issue."
        )
    elif _is_running and _scan_count == 0 and _eng_symbols > 0:
        st.warning(
            f"⚠️ **SCAN COUNT IS 0** — Engine has {_eng_symbols} symbols but none have been scanned yet. "
            "This may indicate the scan loop just started, or `symbol_data` is not populated. "
            "Check engine logs for `SCAN_LOOP` warnings."
        )

    # ════════════════════════════════════════════════════════════════
    # HEADER
    # ════════════════════════════════════════════════════════════════
    st.markdown("""
    <div style="display:flex;gap:6px;align-items:center;background:#0d1117;padding:4px 10px;border-radius:5px;font-size:.72rem;margin-bottom:6px;flex-wrap:wrap">
        <span style="color:#3fb950;font-weight:bold">📊 EMA V5 SCANNER</span>│
        <span>🕐 {time}</span>│
        <span>⏱️ Auto-refresh 120s</span>│
        <span>📡 {scanned} symbols scanned</span>
    </div>
    """.format(
        time=_dt(time.time()),
        scanned=scanner.get("scan_count", 0),
    ), unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════
    # ROW 1: SUMMARY CARDS
    # ════════════════════════════════════════════════════════════════
    is_running = eng_status.get("running", False)
    is_halted = eng_status.get("halted", False)
    halt_reason = eng_status.get("halt_reason", "")
    if is_halted:
        status_label = f"🟡 Halted"
    elif is_running:
        status_label = "🟢 Running"
    else:
        status_label = "🔴 Stopped"

    buy_count = state_counts.get("BUY_MODE", 0)
    sell_count = state_counts.get("SELL_MODE", 0)
    wait_pullback = state_counts.get("WAITING_PULLBACK", 0)
    wait_confirm = state_counts.get("WAITING_CONFIRMATION", 0)
    active_buy = state_counts.get("ACTIVE_BUY", 0)
    active_sell = state_counts.get("ACTIVE_SELL", 0)

    buy_signals = [s for s in signals if s.get("side") == "LONG"]
    sell_signals = [s for s in signals if s.get("side") == "SHORT"]
    avg_conf = (sum(s.get("confidence", 0) for s in signals) / len(signals)) if signals else 0

    uptime = scanner.get("uptime_sec", 0)
    if uptime > 3600:
        uptime_str = f"{uptime / 3600:.1f}h"
    elif uptime > 60:
        uptime_str = f"{uptime / 60:.0f}m"
    else:
        uptime_str = f"{uptime:.0f}s"

    # Row 1a: Primary KPIs
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1:
        st.markdown(f"""<div class="m-box"><div class="m-val" style="color:{'#d29922' if is_halted else '#3fb950' if is_running else '#f85149'}">{status_label}</div><div class="m-lbl">Scanner Status</div></div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""<div class="m-box"><div class="m-val">{scanner.get('scan_count', 0)}</div><div class="m-lbl">Symbols Scanned</div></div>""", unsafe_allow_html=True)
    with c3:
        st.markdown(f"""<div class="m-box"><div class="m-val" style="color:#3fb950">{buy_count}</div><div class="m-lbl">BUY MODE</div></div>""", unsafe_allow_html=True)
    with c4:
        st.markdown(f"""<div class="m-box"><div class="m-val" style="color:#f85149">{sell_count}</div><div class="m-lbl">SELL MODE</div></div>""", unsafe_allow_html=True)
    with c5:
        st.markdown(f"""<div class="m-box"><div class="m-val">{len(buy_signals)}</div><div class="m-lbl">BUY Signals Today</div></div>""", unsafe_allow_html=True)
    with c6:
        st.markdown(f"""<div class="m-box"><div class="m-val">{len(sell_signals)}</div><div class="m-lbl">SELL Signals Today</div></div>""", unsafe_allow_html=True)

    # Row 1b: Secondary KPIs
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1:
        st.markdown(f"""<div class="m-box"><div class="m-val">{wait_pullback}</div><div class="m-lbl">Waiting Pullback</div></div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""<div class="m-box"><div class="m-val">{wait_confirm}</div><div class="m-lbl">Waiting Confirmation</div></div>""", unsafe_allow_html=True)
    with c3:
        st.markdown(f"""<div class="m-box"><div class="m-val" style="color:#3fb950">{active_buy}</div><div class="m-lbl">Active Buys</div></div>""", unsafe_allow_html=True)
    with c4:
        st.markdown(f"""<div class="m-box"><div class="m-val" style="color:#f85149">{active_sell}</div><div class="m-lbl">Active Sells</div></div>""", unsafe_allow_html=True)
    with c5:
        st.markdown(f"""<div class="m-box"><div class="m-val">{avg_conf:.1f}%</div><div class="m-lbl">Avg Confidence</div></div>""", unsafe_allow_html=True)
    with c6:
        st.markdown(f"""<div class="m-box"><div class="m-val">{uptime_str}</div><div class="m-lbl">Uptime</div></div>""", unsafe_allow_html=True)

    # Row 1c: Timing
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        last_scan = scanner.get("last_scan_time", 0)
        st.markdown(f"""<div class="m-box"><div class="m-val">{_ts(last_scan)}</div><div class="m-lbl">Last Scan</div></div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""<div class="m-box"><div class="m-val">{scanner.get('open_trades', 0)}</div><div class="m-lbl">Open Trades</div></div>""", unsafe_allow_html=True)
    with c3:
        st.markdown(f"""<div class="m-box"><div class="m-val">{scanner.get('cache_size', 0)}</div><div class="m-lbl">Cache Size</div></div>""", unsafe_allow_html=True)
    with c4:
        sr = scanner.get("signal_rate", 0)
        st.markdown(f"""<div class="m-box"><div class="m-val">{sr * 100:.2f}%</div><div class="m-lbl">Signal Rate</div></div>""", unsafe_allow_html=True)

    st.divider()

    # ════════════════════════════════════════════════════════════════
    # ROW 2: STATE VISUALIZATION
    # ════════════════════════════════════════════════════════════════
    st.markdown("**📊 Symbol State Distribution**")

    all_states = ["NO_TREND", "BUY_MODE", "SELL_MODE", "WAITING_PULLBACK", "WAITING_CONFIRMATION", "ACTIVE_BUY", "ACTIVE_SELL", "TRADE_CLOSED"]
    total_symbols = sum(state_counts.values()) or 1

    state_html = '<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px">'
    for s in all_states:
        cnt = state_counts.get(s, 0)
        pct = cnt / total_symbols * 100
        color = _state_color(s)
        icon = _state_icon(s)
        state_html += f'''<div class="state-box" style="border-color:{color};min-width:100px">
            <div class="state-val" style="color:{color}">{icon} {cnt}</div>
            <div class="state-lbl">{s.replace('_', ' ').title()}</div>
            <div style="font-size:.5rem;color:#8b949e">{pct:.1f}%</div>
        </div>'''
    state_html += '</div>'
    st.markdown(state_html, unsafe_allow_html=True)

    # Progress bar showing active vs total
    active_total = active_buy + active_sell
    waiting_total = wait_pullback + wait_confirm
    trend_total = buy_count + sell_count
    st.markdown(f"""<div style="margin-top:4px">
        <div style="display:flex;gap:2px;height:8px;border-radius:4px;overflow:hidden">
            <div style="width:{trend_total / max(total_symbols, 1) * 100}%;background:#58a6ff" title="Trend: {trend_total}"></div>
            <div style="width:{waiting_total / max(total_symbols, 1) * 100}%;background:#d29922" title="Waiting: {waiting_total}"></div>
            <div style="width:{active_total / max(total_symbols, 1) * 100}%;background:#3fb950" title="Active: {active_total}"></div>
        </div>
        <div style="font-size:.55rem;color:#8b949e;margin-top:2px">
            <span style="color:#58a6ff">■ Trend ({trend_total})</span> │
            <span style="color:#d29922">■ Waiting ({waiting_total})</span> │
            <span style="color:#3fb950">■ Active ({active_total})</span>
        </div>
    </div>""", unsafe_allow_html=True)

    st.divider()

    # ════════════════════════════════════════════════════════════════
    # ROW 3: LIVE CANDIDATE TABLE
    # ════════════════════════════════════════════════════════════════
    st.markdown("**📡 Live Candidate Table**")

    # ── Filters ──
    f1, f2, f3, f4, f5, f6 = st.columns(6)
    with f1:
        side_filter = st.selectbox("Side", ["ALL", "LONG", "SHORT"], key="ev5_side")
    with f2:
        state_filter = st.selectbox("State", ["ALL", "ACTIVE", "CLOSED", "WAITING"], key="ev5_state")
    with f3:
        conf_filter = st.selectbox("Confidence", ["ALL", "≥ 90", "≥ 95"], key="ev5_conf")
    with f4:
        time_filter = st.selectbox("Time", ["ALL", "Today", "This Week"], key="ev5_time")
    with f5:
        symbol_search = st.text_input("🔍 Search symbol", key="ev5_search", placeholder="BTCUSDT")
    with f6:
        st.markdown(f"""<div class="m-box" style="margin-top:22px"><div class="m-val">{len(signals)}</div><div class="m-lbl">Total Signals</div></div>""", unsafe_allow_html=True)

    # ── Apply Filters ──
    filtered = list(signals)

    if side_filter != "ALL":
        filtered = [s for s in filtered if s.get("side") == side_filter]

    if state_filter == "ACTIVE":
        filtered = [s for s in filtered if states.get(s.get("symbol", ""), {}).get("state", "").startswith("ACTIVE")]
    elif state_filter == "WAITING":
        filtered = [s for s in filtered if "WAITING" in states.get(s.get("symbol", ""), {}).get("state", "")]
    elif state_filter == "CLOSED":
        filtered = [s for s in filtered if "CLOSED" in states.get(s.get("symbol", ""), {}).get("state", "")]

    if conf_filter == "≥ 90":
        filtered = [s for s in filtered if (s.get("confidence", 0) or 0) >= 0.90]
    elif conf_filter == "≥ 95":
        filtered = [s for s in filtered if (s.get("confidence", 0) or 0) >= 0.95]

    if time_filter == "Today":
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).timestamp()
        filtered = [s for s in filtered if (s.get("timestamp", 0) or 0) >= today_start]
    elif time_filter == "This Week":
        week_start = datetime.now(timezone.utc) - timedelta(days=7)
        filtered = [s for s in filtered if (s.get("timestamp", 0) or 0) >= week_start.timestamp()]

    if symbol_search:
        search_upper = symbol_search.upper().strip()
        filtered = [s for s in filtered if search_upper in s.get("symbol", "").upper()]

    # ── Sort by timestamp descending ──
    filtered.sort(key=lambda s: s.get("timestamp", 0) or 0, reverse=True)

    if not filtered:
        st.info("No signals match the current filters.")
    else:
        # ── Build signal table ──
        import pandas as pd

        rows = []
        for s in filtered:
            sym = s.get("symbol", "?")
            sym_state = states.get(sym, {}).get("state", "NO_TREND")
            ts = s.get("timestamp", 0)

            # EMA values from signal
            ema = s.get("ema_data", {})
            components = s.get("components", {})

            rows.append({
                "Signal Time": _ts(ts),
                "Date": _dt(ts).split(' ')[0] if ts else "—",
                "Age": _age(ts),
                "Exchange": "Binance",
                "Symbol": sym,
                "Side": s.get("side", "?"),
                "Trend": components.get("trend", "—"),
                "State": sym_state,
                "Pattern": components.get("candle", "—")[:20],
                "EMA20": _p(ema.get("ema20", 0)),
                "EMA50": _p(ema.get("ema50", 0)),
                "EMA144": _p(ema.get("ema144", 0)),
                "EMA200": _p(ema.get("ema200", 0)),
                "Entry": _p(s.get("entry", s.get("entry_price", 0))),
                "Stop Loss": _p(s.get("sl", s.get("stop_loss", 0))),
                "TP1": _p(s.get("take_profit_1", 0)),
                "TP2": _p(s.get("take_profit_2", 0)),
                "TP3": _p(s.get("take_profit_3", 0)),
                "Confidence": f"{(s.get('confidence', 0) or 0):.1f}%",
                "Volume": "✅" if components.get("volume", "") else "—",
                "Reason": (components.get("regime", "") or "")[:30],
                "Status": sym_state,
                "Version": s.get("strategy_version", "ema_v5"),
            })

        df = pd.DataFrame(rows)

        # ── Color-coded side column ──
        def highlight_side(row):
            styles = [''] * len(row)
            if row.get("Side") == "LONG":
                styles[list(row.index).index("Side")] = "color: #3fb950; font-weight: bold"
            elif row.get("Side") == "SHORT":
                styles[list(row.index).index("Side")] = "color: #f85149; font-weight: bold"
            return styles

        st.dataframe(
            df.style.apply(highlight_side, axis=1),
            use_container_width=True,
            hide_index=True,
            height=min(500, 35 * len(df) + 40),
        )

    st.divider()

    # ════════════════════════════════════════════════════════════════
    # ROW 4: DETAIL PANEL (click a signal)
    # ════════════════════════════════════════════════════════════════
    if signals:
        st.markdown("**🔍 Signal Detail Panel**")

        sym_list = sorted(set(s.get("symbol", "?") for s in signals))
        selected_sym = st.selectbox("Select symbol for detail view", sym_list, key="ev5_detail_sym")

        if selected_sym:
            sig = next((s for s in signals if s.get("symbol") == selected_sym), None)
            sym_state = states.get(selected_sym, {})
            components = sig.get("components", {}) if sig else {}
            ema = sig.get("ema_data", {}) if sig else {}
            conf_breakdown = components.get("confidence", {})

            if sig:
                dc1, dc2, dc2b, dc3 = st.columns([1, 1, 1, 1])

                # ── Trend Classification ──
                with dc1:
                    st.markdown("""<div class="detail-card">
                        <div class="detail-title">📈 Trend Classification</div>""", unsafe_allow_html=True)
                    # Show components if available, otherwise show available signal data
                    _regime_display = components.get("regime", "") or sig.get("regime", "—")
                    _trend_display = components.get("trend", "") or "—"
                    _candle_display = components.get("candle", "") or "—"
                    _volume_display = components.get("volume", "")
                    _vol_icon = "✅ Confirmed" if _volume_display else ("✅" if sig.get("confidence", 0) >= 90 else "—")
                    trend_items = [
                        ("Direction", sig.get("side", "?")),
                        ("Regime", _regime_display),
                        ("Trend", _trend_display),
                        ("Pattern", _candle_display[:40]),
                        ("Volume", _vol_icon),
                    ]
                    for key, val in trend_items:
                        st.markdown(f"""<div class="detail-row">
                            <span class="detail-key">{key}</span>
                            <span class="detail-val">{val}</span>
                        </div>""", unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)

                # ── EMA Alignment ──
                with dc2:
                    st.markdown("""<div class="detail-card">
                        <div class="detail-title">📐 EMA Alignment</div>""", unsafe_allow_html=True)
                    ema_items = [
                        ("EMA20", _p(ema.get("ema20", 0))),
                        ("EMA50", _p(ema.get("ema50", 0))),
                        ("EMA144", _p(ema.get("ema144", 0))),
                        ("EMA200", _p(ema.get("ema200", 0))),
                        ("Entry", _p(sig.get("entry", sig.get("entry_price", 0)))),
                        ("SL", _p(sig.get("sl", sig.get("stop_loss", 0)))),
                        ("TP1", _p(sig.get("take_profit_1", 0))),
                        ("TP2", _p(sig.get("take_profit_2", 0))),
                        ("TP3", _p(sig.get("take_profit_3", 0))),
                    ]
                    for key, val in ema_items:
                        st.markdown(f"""<div class="detail-row">
                            <span class="detail-key">{key}</span>
                            <span class="detail-val">{val}</span>
                        </div>""", unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)

                # ── Confidence Breakdown ──
                with dc2b:
                    st.markdown("""<div class="detail-card">
                        <div class="detail-title">🎯 Confidence Breakdown</div>""", unsafe_allow_html=True)
                    total_conf = (sig.get("confidence", 0) or 0)
                    st.markdown(f"""<div class="detail-row">
                        <span class="detail-key">Total</span>
                        <span class="detail-val" style="color:#58a6ff;font-size:1.1em">{total_conf:.1f}%</span>
                    </div>""", unsafe_allow_html=True)

                    # Show breakdown if available, otherwise show regime as proxy
                    if conf_breakdown:
                        for comp, score in conf_breakdown.items():
                            if isinstance(score, (int, float)):
                                bar_pct = min(score, 100)
                                bar_color = "#3fb950" if score >= 80 else ("#d29922" if score >= 60 else "#f85149")
                                st.markdown(f"""<div class="detail-row">
                                    <span class="detail-key">{comp.replace('_', ' ').title()}</span>
                                    <span class="detail-val">{score:.1f}</span>
                                </div>
                                <div class="bar"><div class="bar-fill" style="width:{bar_pct}%;background:{bar_color}"></div></div>""", unsafe_allow_html=True)
                    else:
                        # Fallback: show confidence passed/failed status
                        _conf_color = "#3fb950" if total_conf >= 90 else "#f85149"
                        _conf_status = "PASSED ✓" if total_conf >= 90 else "BELOW THRESHOLD"
                        st.markdown(f"""<div class="detail-row">
                            <span class="detail-key">Status</span>
                            <span class="detail-val" style="color:{_conf_color}">{_conf_status}</span>
                        </div>""", unsafe_allow_html=True)
                        st.markdown(f"""<div class="detail-row">
                            <span class="detail-key">Threshold</span>
                            <span class="detail-val">90.0%</span>
                        </div>""", unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)

                # ── Reason Checklist ──
                with dc3:
                    st.markdown("""<div class="detail-card">
                        <div class="detail-title">✅ Reason Checklist</div>""", unsafe_allow_html=True)
                    _conf_val = sig.get("confidence", 0) or 0
                    reasons = [
                        ("Regime", components.get("regime", "") or sig.get("regime", "—")),
                        ("Trend", components.get("trend", "") or "—"),
                        ("Pullback", components.get("pullback", "") or "—"),
                        ("Candle", components.get("candle", "") or "—"),
                        ("Volume", components.get("volume", "") or ("✅ (conf≥90)" if _conf_val >= 90 else "—")),
                        ("Confidence", f"✅ {_conf_val:.1f}%" if _conf_val >= 90 else f"❌ {_conf_val:.1f}%"),
                    ]
                    for key, reason in reasons:
                        passed = reason and reason != "—" and "not" not in str(reason).lower()[:10] and "❌" not in str(reason)
                        icon = "✅" if passed else "—"
                        st.markdown(f"""<div class="detail-row">
                            <span class="detail-key">{icon} {key}</span>
                            <span class="detail-val">{str(reason)[:45]}</span>
                        </div>""", unsafe_allow_html=True)

                    # Trade lifecycle
                    st.markdown("<br>", unsafe_allow_html=True)
                    lifecycle_items = [
                        ("State", sym_state.get("state", "NO_TREND")),
                        ("Last Update", _dt(sym_state.get("last_update", 0))),
                        ("Previous", sym_state.get("previous", "—")),
                        ("R:R TP1", f"{sig.get('rr_1', 0):.2f}"),
                        ("R:R TP2", f"{sig.get('rr_2', 0):.2f}"),
                        ("R:R TP3", f"{sig.get('rr_3', 0):.2f}"),
                    ]
                    st.markdown('<div class="detail-title" style="margin-top:8px">🔄 Trade Lifecycle</div>', unsafe_allow_html=True)
                    for key, val in lifecycle_items:
                        st.markdown(f"""<div class="detail-row">
                            <span class="detail-key">{key}</span>
                            <span class="detail-val">{val}</span>
                        </div>""", unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)
            else:
                st.info(f"No signal data for {selected_sym}")

    st.divider()

    # ════════════════════════════════════════════════════════════════
    # ROW 5: SCANNER HEALTH
    # ════════════════════════════════════════════════════════════════
    st.markdown("**🏥 Scanner Health**")

    h1, h2, h3, h4, h5 = st.columns(5)

    with h1:
        st.markdown("""<div class="detail-card">
            <div class="detail-title">System</div>""", unsafe_allow_html=True)
        sys_items = [
            ("Engine Running", health.get("engine_running", False)),
            ("Engine Halted", eng_status.get("halted", False)),
            ("Halt Reason", eng_status.get("halt_reason", "") or "—"),
            ("API Connected", health.get("api_connected", True)),
            ("WebSocket", health.get("ws_connected", True)),
            ("Database", health.get("db_connected", True)),
        ]
        for key, val in sys_items:
            dot_class = "health-on" if val else "health-off"
            label = "ON" if val else "OFF"
            st.markdown(f"""<div class="detail-row">
                <span class="detail-key"><span class="health-dot {dot_class}"></span>{key}</span>
                <span class="detail-val">{label}</span>
            </div>""", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with h2:
        st.markdown("""<div class="detail-card">
            <div class="detail-title">Performance</div>""", unsafe_allow_html=True)
        perf_items = [
            ("Cache Size", str(scanner.get("cache_size", 0))),
            ("Scan Count", str(scanner.get("scan_count", 0))),
            ("Signal Count", str(scanner.get("signal_count", 0))),
            ("Signal Rate", f"{scanner.get('signal_rate', 0) * 100:.2f}%"),
            ("Open Trades", str(scanner.get("open_trades", 0))),
        ]
        for key, val in perf_items:
            st.markdown(f"""<div class="detail-row">
                <span class="detail-key">{key}</span>
                <span class="detail-val">{val}</span>
            </div>""", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with h3:
        st.markdown("""<div class="detail-card">
            <div class="detail-title">Errors</div>""", unsafe_allow_html=True)
        err_items = [
            ("Error Count", str(health.get("error_count", 0))),
            ("Reconnect Count", str(health.get("reconnect_count", 0))),
            ("Uptime", uptime_str),
        ]
        for key, val in err_items:
            err_val = int(val) if val.isdigit() else 0
            color = "#f85149" if err_val > 0 else "#3fb950"
            st.markdown(f"""<div class="detail-row">
                <span class="detail-key">{key}</span>
                <span class="detail-val" style="color:{color}">{val}</span>
            </div>""", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with h4:
        st.markdown("""<div class="detail-card">
            <div class="detail-title">State Summary</div>""", unsafe_allow_html=True)
        for s in all_states:
            cnt = state_counts.get(s, 0)
            color = _state_color(s)
            icon = _state_icon(s)
            st.markdown(f"""<div class="detail-row">
                <span class="detail-key">{icon} {s.replace('_', ' ').title()}</span>
                <span class="detail-val" style="color:{color}">{cnt}</span>
            </div>""", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with h5:
        st.markdown("""<div class="detail-card">
            <div class="detail-title">Timing</div>""", unsafe_allow_html=True)
        timing_items = [
            ("Last Scan", _ts(scanner.get("last_scan_time", 0))),
            ("Uptime", uptime_str),
            ("Cache TTL", "5 min"),
            ("Scan Interval", "~15s"),
        ]
        for key, val in timing_items:
            st.markdown(f"""<div class="detail-row">
                <span class="detail-key">{key}</span>
                <span class="detail-val">{val}</span>
            </div>""", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    st.divider()

    # ════════════════════════════════════════════════════════════════
    # ROW 6: PRODUCTION DIAGNOSTICS — Pipeline Funnel & Confidence
    # ════════════════════════════════════════════════════════════════
    diagnostics = data.get("diagnostics", {})
    if diagnostics:
        st.markdown("**🔬 Production Diagnostics — Pipeline Funnel & Confidence**")

        # Pipeline funnel
        pipeline = diagnostics.get("pipeline", {})
        funnel = pipeline.get("funnel", {})
        stage_rejections = pipeline.get("stage_rejections", {})
        stage_latencies = pipeline.get("stage_latencies", {})
        conf_bins = pipeline.get("confidence_bins", {})

        # ── Stage pass counters (from scanner._stage_passed) ──
        stage_passed = data.get("stage_passed", {})
        if stage_rejections:
            st.markdown("**📊 Pipeline Funnel — Conversion Rates**")
            # Calculate total scanned for percentage base
            scanner_stats = data.get("scanner", {})
            total_scanned = scanner_stats.get("scan_count", 0)
            stages_display = [
                ("fast_filter", "Fast Filter"),
                ("regime", "Regime"),
                ("trend", "Trend"),
                ("pullback", "Pullback"),
                ("candle", "Candle"),
                ("volume", "Volume"),
                ("confidence", "Confidence"),
                ("signal_engine", "Signal"),
            ]
            funnel_cols = st.columns(4)
            for i, (stage_key, stage_name) in enumerate(stages_display[:4]):
                passed = stage_passed.get(stage_key, 0)
                rejected = stage_rejections.get(stage_key, 0)
                total_in = passed + rejected
                conv_pct = (passed / total_in * 100) if total_in > 0 else 0
                with funnel_cols[i]:
                    color = "#3fb950" if conv_pct > 20 else ("#d29922" if conv_pct > 5 else "#f85149")
                    st.markdown(f"""<div class="m-box" style="border-left:3px solid {color}"><div class="m-val" style="color:{color}">✓ {passed:,}  ✗ {rejected:,}</div><div class="m-lbl">{stage_name} ({conv_pct:.1f}% pass)</div></div>""", unsafe_allow_html=True)

            funnel_cols2 = st.columns(4)
            for i, (stage_key, stage_name) in enumerate(stages_display[4:]):
                passed = stage_passed.get(stage_key, 0)
                rejected = stage_rejections.get(stage_key, 0)
                total_in = passed + rejected
                conv_pct = (passed / total_in * 100) if total_in > 0 else 0
                with funnel_cols2[i]:
                    color = "#3fb950" if conv_pct > 20 else ("#d29922" if conv_pct > 5 else "#f85149")
                    st.markdown(f"""<div class="m-box" style="border-left:3px solid {color}"><div class="m-val" style="color:{color}">✓ {passed:,}  ✗ {rejected:,}</div><div class="m-lbl">{stage_name} ({conv_pct:.1f}% pass)</div></div>""", unsafe_allow_html=True)

            # ── Visual funnel bar ──
            if total_scanned > 0:
                st.markdown("**🔽 Conversion Funnel**")
                funnel_stages = [
                    ("Scanned", total_scanned, total_scanned),
                    ("Fast Filter Pass", stage_passed.get("fast_filter", 0), total_scanned),
                    ("Regime Pass", stage_passed.get("regime", 0), total_scanned),
                    ("Pullback Pass", stage_passed.get("pullback", 0), total_scanned),
                    ("Candle Pass", stage_passed.get("candle", 0), total_scanned),
                    ("Volume Pass", stage_passed.get("volume", 0), total_scanned),
                    ("Confidence Pass", stage_passed.get("confidence", 0), total_scanned),
                    ("Signal Published", stage_passed.get("signal", 0), total_scanned),
                ]
                funnel_html = ''
                for label, count, base in funnel_stages:
                    pct = (count / base * 100) if base > 0 else 0
                    bar_w = max(pct, 0.3)
                    color = "#3fb950" if pct > 50 else ("#d29922" if pct > 10 else ("#f85149" if pct > 1 else "#484f58"))
                    funnel_html += f'<div style="display:flex;align-items:center;margin:2px 0"><div style="width:120px;font-size:.7rem;color:#8b949e;text-align:right;padding-right:8px">{label}</div><div style="flex:1;background:#21262d;border-radius:3px;height:18px;position:relative"><div style="width:{bar_w}%;background:{color};height:100%;border-radius:3px;min-width:2px"></div></div><div style="width:80px;font-size:.7rem;color:#e6edf3;padding-left:8px">{count:,} ({pct:.1f}%)</div></div>'
                st.markdown(funnel_html, unsafe_allow_html=True)

            # ── Rejection Breakdown Summary ──
            st.markdown("**🚫 Rejection Breakdown**")
            rejection_html = '<div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 12px;font-size:.75rem">'
            for stage_key, stage_name in stages_display:
                rejected = stage_rejections.get(stage_key, 0)
                passed = stage_passed.get(stage_key, 0)
                total_in = passed + rejected
                if rejected > 0:
                    pct_reject = (rejected / total_in * 100) if total_in > 0 else 0
                    rejection_html += f'<div style="display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #21262d"><span style="color:#8b949e">{stage_name} rejected</span><span style="color:#f85149;font-weight:600">✗ {rejected:,} ({pct_reject:.0f}%)</span></div>'
            if not any(stage_rejections.get(s, 0) > 0 for s, _ in stages_display):
                rejection_html += '<div style="color:#3fb950;text-align:center;padding:8px">✅ No rejections yet — pipeline is clean</div>'
            rejection_html += '</div>'
            st.markdown(rejection_html, unsafe_allow_html=True)

            # ── Fast Filter Reason Breakdown ──
            fast_filter_reasons = data.get("fast_filter_reasons", {})
            ff_total = sum(fast_filter_reasons.values()) if fast_filter_reasons else 0
            if ff_total > 0:
                st.markdown("**🔍 Fast Filter Rejection Reasons**")
                ff_html = '<div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 12px;font-size:.75rem">'
                reason_labels = {
                    "no_klines": "No kline data",
                    "insufficient_candles": "Insufficient candles",
                    "invalid_ohlcv": "Invalid OHLCV",
                    "zero_volume": "Zero volume",
                }
                for reason_key, count in sorted(fast_filter_reasons.items(), key=lambda x: -x[1]):
                    if count > 0:
                        pct = (count / ff_total * 100) if ff_total > 0 else 0
                        label = reason_labels.get(reason_key, reason_key)
                        ff_html += f'<div style="display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #21262d"><span style="color:#8b949e">{label}</span><span style="color:#f85149;font-weight:600">{count:,} ({pct:.0f}%)</span></div>'
                ff_html += '</div>'
                st.markdown(ff_html, unsafe_allow_html=True)

            # ── Waterfall: Candidates Remaining at Each Stage ──
            st.markdown("**🔽 Filter Waterfall — Candidates Remaining**")
            waterfall_stages = [
                ("Scanned", total_scanned),
                ("After Fast Filter", stage_passed.get("fast_filter", 0)),
                ("After Regime", stage_passed.get("regime", 0)),
                ("After Trend", stage_passed.get("trend", 0)),
                ("After Pullback", stage_passed.get("pullback", 0)),
                ("After Candle", stage_passed.get("candle", 0)),
                ("After Volume", stage_passed.get("volume", 0)),
                ("After Confidence", stage_passed.get("confidence", 0)),
                ("Published", stage_passed.get("signal", 0)),
            ]
            waterfall_html = '<div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 12px;font-size:.75rem">'
            for i, (label, count) in enumerate(waterfall_stages):
                pct = (count / total_scanned * 100) if total_scanned > 0 else 0
                bar_w = max(pct, 0.3)
                color = "#3fb950" if pct > 50 else ("#d29922" if pct > 10 else ("#f85149" if pct > 1 else "#484f58"))
                # Show drop from previous stage
                if i > 0:
                    prev_count = waterfall_stages[i-1][1]
                    drop = prev_count - count
                    drop_pct = (drop / prev_count * 100) if prev_count > 0 else 0
                    drop_text = f' (-{drop:,} / -{drop_pct:.0f}%)' if drop > 0 else ''
                else:
                    drop_text = ''
                waterfall_html += f'<div style="display:flex;align-items:center;margin:2px 0"><div style="width:130px;font-size:.7rem;color:#8b949e;text-align:right;padding-right:8px">{label}</div><div style="flex:1;background:#21262d;border-radius:3px;height:18px;position:relative"><div style="width:{bar_w}%;background:{color};height:100%;border-radius:3px;min-width:2px"></div></div><div style="width:120px;font-size:.7rem;color:#e6edf3;padding-left:8px">{count:,} ({pct:.1f}%){drop_text}</div></div>'
            waterfall_html += '</div>'
            st.markdown(waterfall_html, unsafe_allow_html=True)

        # Pipeline Latency
        if stage_latencies:
            st.markdown("**⏱️ Pipeline Latency (ms)**")
            lat_cols = st.columns(4)
            lat_stages = ["fast_filter", "regime", "candle", "confidence"]
            for i, stage in enumerate(lat_stages):
                lat_data = stage_latencies.get(stage, {})
                avg = lat_data.get("avg_ms", 0)
                mx = lat_data.get("max_ms", 0)
                with lat_cols[i]:
                    st.markdown(f"""<div class="m-box"><div class="m-val">{avg:.1f}</div><div class="m-lbl">{stage} avg (max {mx:.1f})</div></div>""", unsafe_allow_html=True)

        # Confidence Distribution — with threshold marker
        conf_audit = diagnostics.get("confidence_audit", {})
        conf_dist = conf_audit.get("distribution", conf_bins)
        if conf_dist:
            st.markdown("**🎯 Confidence Score Distribution**")
            threshold = conf_audit.get("threshold", 90.0)
            conf_html = '<div style="display:flex;gap:4px;align-items:end;height:100px;margin:4px 0">'
            max_count = max(conf_dist.values()) if conf_dist else 1
            # Use distribution from confidence_audit if available (better bins)
            bin_order = ["<70", "70-80", "80-85", "85-88", "88-90", "90-95", "95+"]
            if not any(k in conf_dist for k in bin_order):
                bin_order = ["<60", "60-70", "70-75", "75-80", "80-85", "85-90", "90-95", "95-100"]
            for bin_label in bin_order:
                count = conf_dist.get(bin_label, 0)
                height = max(count / max(max_count, 1) * 85, 2)
                # Color: green for above threshold, yellow near, red below
                is_above = bin_label in ("90-95", "95-100", "95+")
                is_near = bin_label in ("88-90", "85-90", "85-88", "90-95")
                color = "#3fb950" if is_above else ("#d29922" if is_near else "#f85149")
                # Add threshold indicator line
                border = 'border-top:2px solid #58a6ff' if bin_label == "90-95" or bin_label == "88-90" else ''
                conf_html += f'<div style="flex:1;display:flex;flex-direction:column;align-items:center;{border}"><div style="font-size:.6rem;color:#e6edf3;font-weight:600">{count}</div><div style="width:100%;height:{height}px;background:{color};border-radius:2px"></div><div style="font-size:.5rem;color:#8b949e">{bin_label}</div></div>'
            conf_html += '</div>'
            conf_html += f'<div style="font-size:.6rem;color:#58a6ff;margin-top:2px">⬆ Threshold = {threshold:.0f} (candidates above this line pass)</div>'
            st.markdown(conf_html, unsafe_allow_html=True)

        # Top Rejection Reasons
        top_reasons = pipeline.get("top_rejection_reasons", {})
        if top_reasons:
            st.markdown("**🔴 Top Rejection Reasons**")
            reasons_html = '<div style="display:flex;flex-wrap:wrap;gap:4px">'
            for reason, count in list(top_reasons.items())[:8]:
                reasons_html += f'<div class="m-box" style="min-width:150px"><div class="m-val" style="font-size:.8rem">{count}</div><div class="m-lbl" style="font-size:.5rem">{reason[:35]}</div></div>'
            reasons_html += '</div>'
            st.markdown(reasons_html, unsafe_allow_html=True)

        # Confidence Engine Audit
        conf_audit = diagnostics.get("confidence_audit", {})
        if conf_audit:
            st.markdown("**📊 Confidence Engine Audit**")
            ca1, ca2, ca3, ca4, ca5 = st.columns(5)
            with ca1:
                st.markdown(f"""<div class="m-box"><div class="m-val">{conf_audit.get('total_evaluations', 0)}</div><div class="m-lbl">Total Evaluations</div></div>""", unsafe_allow_html=True)
            with ca2:
                pr = conf_audit.get('pass_rate_pct', 0)
                st.markdown(f"""<div class="m-box"><div class="m-val" style="color:{'#3fb950' if pr > 0 else '#f85149'}">{pr:.1f}%</div><div class="m-lbl">Pass Rate</div></div>""", unsafe_allow_html=True)
            with ca3:
                st.markdown(f"""<div class="m-box"><div class="m-val">{conf_audit.get('avg_confidence', 0):.1f}</div><div class="m-lbl">Avg Confidence</div></div>""", unsafe_allow_html=True)
            with ca4:
                st.markdown(f"""<div class="m-box"><div class="m-val">{conf_audit.get('avg_gap_when_rejected', 0):+.1f}</div><div class="m-lbl">Avg Gap (Rejected)</div></div>""", unsafe_allow_html=True)
            with ca5:
                st.markdown(f"""<div class="m-box"><div class="m-val">{conf_audit.get('threshold', 90)}</div><div class="m-lbl">Threshold</div></div>""", unsafe_allow_html=True)

            # ── Component Contribution Averages ──
            comp_avgs = conf_audit.get("component_averages", {})
            if comp_avgs:
                st.markdown("**🧮 Component Contribution Averages (across all candidates)**")
                comp_html = '<div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 12px;font-size:.75rem">'
                components = [
                    ("Inst Score", "inst_score", True, 0.50),
                    ("Regime", "regime_contrib", True, None),
                    ("Trend", "trend_contrib", None, None),
                    ("Pullback", "pullback_contrib", True, None),
                    ("Candle", "candle_contrib", None, None),
                    ("Volume", "volume_contrib", None, None),
                ]
                for label, key, positive, weight in components:
                    val = comp_avgs.get(key, 0)
                    score = comp_avgs.get(key.replace("_contrib", ""), 0)
                    color = "#3fb950" if val > 0 else "#f85149"
                    w_str = f" (×{weight})" if weight else ""
                    comp_html += f'<div style="display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #21262d"><span style="color:#8b949e">{label}{w_str} — raw score: {score:.0f}</span><span style="color:{color};font-weight:600">{val:+.2f} pts</span></div>'
                base = comp_avgs.get("inst_score", 50) * 0.50
                comp_html += f'<div style="display:flex;justify-content:space-between;padding:4px 0;border-top:2px solid #30363d;margin-top:4px"><span style="color:#58a6ff;font-weight:600">Formula: inst×0.50 + regime + trend + pullback + candle + volume</span><span style="color:#58a6ff;font-weight:600">base={base:.1f}</span></div>'
                comp_html += '</div>'
                st.markdown(comp_html, unsafe_allow_html=True)

            # ── Recent Confidence Evaluations (last 20) ──
            recent_evals = conf_audit.get("recent_evaluations", [])
            if recent_evals:
                st.markdown(f"**📋 Recent Confidence Evaluations (last {len(recent_evals)})**")
                eval_html = '<div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px;font-size:.7rem">'
                eval_html += '<div style="display:flex;gap:4px;padding:2px 0;border-bottom:1px solid #30363d;font-weight:600;color:#8b949e">'
                eval_html += '<div style="width:50px">Result</div><div style="width:45px">Conf</div><div style="width:55px">InstScore</div>'
                eval_html += '<div style="width:65px">Regime</div><div style="width:65px">Trend</div><div style="width:65px">Pullback</div>'
                eval_html += '<div style="width:65px">Candle</div><div style="width:65px">Volume</div><div style="width:50px">Gap</div>'
                eval_html += '</div>'
                for ev in reversed(recent_evals):
                    passed = ev.get("passed", False)
                    result_icon = "✅" if passed else "❌"
                    result_color = "#3fb950" if passed else "#f85149"
                    conf = ev.get("confidence", 0)
                    eval_html += f'<div style="display:flex;gap:4px;padding:2px 0;border-bottom:1px solid #21262d">'
                    eval_html += f'<div style="width:50px;color:{result_color}">{result_icon}</div>'
                    eval_html += f'<div style="width:45px;color:{result_color};font-weight:600">{conf:.1f}</div>'
                    eval_html += f'<div style="width:55px">{ev.get("inst_score", 0):.0f}</div>'
                    # Show raw score and contribution for each component
                    for comp_key in ["regime", "trend", "pullback", "candle", "volume"]:
                        raw = ev.get(comp_key, 0)
                        contrib = ev.get(f"{comp_key}_contrib", 0)
                        c_color = "#3fb950" if contrib > 0 else "#f85149"
                        eval_html += f'<div style="width:65px"><span style="color:#8b949e">{raw:.0f}</span> <span style="color:{c_color}">({contrib:+.1f})</span></div>'
                    gap = ev.get("gap", 0)
                    gap_color = "#3fb950" if gap <= 0 else "#f85149"
                    eval_html += f'<div style="width:50px;color:{gap_color}">{gap:+.1f}</div>'
                    eval_html += '</div>'
                eval_html += '</div>'
                eval_html += '<div style="font-size:.6rem;color:#8b949e;margin-top:4px">Format: raw_score (weighted_contribution). Positive = helps pass, Negative = penalizes.</div>'
                st.markdown(eval_html, unsafe_allow_html=True)

        # WAITING_CONFIRMATION Audit
        wc_audit = diagnostics.get("waiting_confirmation", {})
        if wc_audit:
            st.markdown("**🔍 WAITING_CONFIRMATION Audit**")
            wc1, wc2, wc3, wc4 = st.columns(4)
            with wc1:
                st.markdown(f"""<div class="m-box"><div class="m-val">{wc_audit.get('total_entered', 0)}</div><div class="m-lbl">Total Entered</div></div>""", unsafe_allow_html=True)
            with wc2:
                st.markdown(f"""<div class="m-box"><div class="m-val">{wc_audit.get('total_completed', 0)}</div><div class="m-lbl">Completed</div></div>""", unsafe_allow_html=True)
            with wc3:
                st.markdown(f"""<div class="m-box"><div class="m-val">{wc_audit.get('currently_active', 0)}</div><div class="m-lbl">Currently Active</div></div>""", unsafe_allow_html=True)
            with wc4:
                st.markdown(f"""<div class="m-box"><div class="m-val">{wc_audit.get('total_timeouts', 0)}</div><div class="m-lbl">Timeouts</div></div>""", unsafe_allow_html=True)

            # Exit reasons
            exit_reasons = wc_audit.get("exit_reasons", {})
            if exit_reasons:
                st.markdown("**WC Exit Reasons:**")
                er_html = '<div style="display:flex;gap:4px;flex-wrap:wrap">'
                for reason, count in exit_reasons.items():
                    color = "#3fb950" if "ACTIVE" in reason else ("#d29922" if "SIGNAL" in reason else "#f85149")
                    er_html += f'<div class="m-box" style="border-color:{color}"><div class="m-val" style="color:{color}">{count}</div><div class="m-lbl">{reason}</div></div>'
                er_html += '</div>'
                st.markdown(er_html, unsafe_allow_html=True)

        # Performance Metrics (from in-memory tracker)
        perf = diagnostics.get("performance", {})
        if perf:
            st.markdown("**📈 Performance Metrics (In-Memory)**")
            p1, p2, p3, p4, p5, p6 = st.columns(6)
            with p1:
                st.markdown(f"""<div class="m-box"><div class="m-val">{perf.get('win_rate', 0):.1f}%</div><div class="m-lbl">Win Rate</div></div>""", unsafe_allow_html=True)
            with p2:
                pf = perf.get('profit_factor', 0)
                st.markdown(f"""<div class="m-box"><div class="m-val" style="color:{'#3fb950' if pf >= 1 else '#f85149'}">{pf:.2f}</div><div class="m-lbl">Profit Factor</div></div>""", unsafe_allow_html=True)
            with p3:
                st.markdown(f"""<div class="m-box"><div class="m-val">{perf.get('sharpe_ratio', 0):.2f}</div><div class="m-lbl">Sharpe Ratio</div></div>""", unsafe_allow_html=True)
            with p4:
                st.markdown(f"""<div class="m-box"><div class="m-val">{perf.get('max_drawdown_pct', 0):.1f}%</div><div class="m-lbl">Max Drawdown</div></div>""", unsafe_allow_html=True)
            with p5:
                st.markdown(f"""<div class="m-box"><div class="m-val">{perf.get('total_trades', 0)}</div><div class="m-lbl">Total Trades</div></div>""", unsafe_allow_html=True)
            with p6:
                st.markdown(f"""<div class="m-box"><div class="m-val">{perf.get('signal_frequency', 0):.2f}</div><div class="m-lbl">Signals/Hour</div></div>""", unsafe_allow_html=True)

        # ══════════════════════════════════════════════════════════
        # PRODUCTION ANALYTICS — Trade Performance from Database
        # ══════════════════════════════════════════════════════════
        analytics = data.get("analytics", {})
        if analytics and analytics.get("total_trades", 0) > 0:
            st.markdown("**📊 Production Analytics — Trade Performance**")
            ao = analytics.get("overall", {})
            # Row 1: Key metrics
            a1, a2, a3, a4, a5, a6 = st.columns(6)
            with a1:
                st.markdown(f"""<div class="m-box"><div class="m-val">{ao.get('total_trades', 0)}</div><div class="m-lbl">Total Trades</div></div>""", unsafe_allow_html=True)
            with a2:
                wr = ao.get('win_rate_pct', 0)
                st.markdown(f"""<div class="m-box"><div class="m-val" style="color:{'#3fb950' if wr >= 50 else '#d29922'}">{wr:.1f}%</div><div class="m-lbl">Win Rate</div></div>""", unsafe_allow_html=True)
            with a3:
                pf = ao.get('profit_factor', 0)
                st.markdown(f"""<div class="m-box"><div class="m-val" style="color:{'#3fb950' if pf >= 1 else '#f85149'}">{pf:.2f}</div><div class="m-lbl">Profit Factor</div></div>""", unsafe_allow_html=True)
            with a4:
                exp = ao.get('expectancy_r', 0)
                st.markdown(f"""<div class="m-box"><div class="m-val" style="color:{'#3fb950' if exp > 0 else '#f85149'}">{exp:+.2f}R</div><div class="m-lbl">Expectancy</div></div>""", unsafe_allow_html=True)
            with a5:
                st.markdown(f"""<div class="m-box"><div class="m-val">{ao.get('avg_r', 0):+.2f}R</div><div class="m-lbl">Avg R</div></div>""", unsafe_allow_html=True)
            with a6:
                st.markdown(f"""<div class="m-box"><div class="m-val">{ao.get('max_drawdown_pct', 0):.1f}%</div><div class="m-lbl">Max Drawdown</div></div>""", unsafe_allow_html=True)

            # Row 2: TP hit rates
            tp = analytics.get("tp_hit_rates", {})
            t1, t2, t3, t4, t5, t6 = st.columns(6)
            with t1:
                st.markdown(f"""<div class="m-box"><div class="m-val">{tp.get('tp1_reached_pct', 0):.0f}%</div><div class="m-lbl">TP1 Reached ({tp.get('tp1_reached', 0)})</div></div>""", unsafe_allow_html=True)
            with t2:
                st.markdown(f"""<div class="m-box"><div class="m-val">{tp.get('tp1_exit_pct', 0):.0f}%</div><div class="m-lbl">TP1 Exits ({tp.get('tp1_exits', 0)})</div></div>""", unsafe_allow_html=True)
            with t3:
                st.markdown(f"""<div class="m-box"><div class="m-val">{tp.get('sl_exit_pct', 0):.0f}%</div><div class="m-lbl">Stop Loss ({tp.get('sl_count', 0)})</div></div>""", unsafe_allow_html=True)
            with t4:
                st.markdown(f"""<div class="m-box"><div class="m-val">{tp.get('trailing_exit_pct', 0):.0f}%</div><div class="m-lbl">Trailing ({tp.get('trailing_count', 0)})</div></div>""", unsafe_allow_html=True)
            with t5:
                st.markdown(f"""<div class="m-box"><div class="m-val">{ao.get('avg_hold_minutes', 0):.0f}m</div><div class="m-lbl">Avg Hold</div></div>""", unsafe_allow_html=True)
            with t6:
                st.markdown(f"""<div class="m-box"><div class="m-val">{ao.get('best_streak', 0)}</div><div class="m-lbl">Best Streak</div></div>""", unsafe_allow_html=True)

            # Row 3: Rolling metrics
            rm = analytics.get("rolling_metrics", {})
            if rm.get("window", 0) > 0:
                r1, r2, r3 = st.columns(3)
                with r1:
                    rpf = rm.get('current_pf', 0)
                    st.markdown(f"""<div class="m-box" style="border-left:3px solid {'#3fb950' if rpf >= 1 else '#f85149'}"><div class="m-val">{rpf:.2f}</div><div class="m-lbl">Rolling PF ({rm.get('window', 20)}-trade)</div></div>""", unsafe_allow_html=True)
                with r2:
                    st.markdown(f"""<div class="m-box"><div class="m-val">{rm.get('current_wr', 0):.1f}%</div><div class="m-lbl">Rolling WR</div></div>""", unsafe_allow_html=True)
                with r3:
                    hrs = analytics.get('lifecycle', {}).get('hours_since_last_trade', -1)
                    hrs_str = f"{hrs:.1f}h" if hrs >= 0 else "N/A"
                    st.markdown(f"""<div class="m-box"><div class="m-val">{hrs_str}</div><div class="m-lbl">Since Last Trade</div></div>""", unsafe_allow_html=True)

            # Row 4: Performance by session
            by_session = analytics.get("by_session", {})
            if by_session:
                st.markdown("**🏟️ Performance by Session**")
                sess_html = '<div style="display:flex;gap:6px;flex-wrap:wrap">'
                for sess, m in sorted(by_session.items(), key=lambda x: x[1].get("trades", 0), reverse=True):
                    swr = m.get('win_rate_pct', 0)
                    spf = m.get('profit_factor', 0)
                    sc = "#3fb950" if spf >= 1 else ("#d29922" if spf >= 0.7 else "#f85149")
                    sess_html += f'<div class="m-box" style="border-left:3px solid {sc};min-width:140px"><div class="m-val" style="color:{sc}">{spf:.2f} PF</div><div class="m-lbl">{sess}: {m.get("trades",0)} trades, {swr:.0f}% WR, R={m.get("avg_r",0):.2f}</div></div>'
                sess_html += '</div>'
                st.markdown(sess_html, unsafe_allow_html=True)

            # Row 5: Performance by confidence bucket
            by_conf = analytics.get("by_confidence_bucket", {})
            if by_conf:
                st.markdown("**🎯 Performance by Confidence Bucket**")
                conf_html = '<div style="display:flex;gap:6px;flex-wrap:wrap">'
                for bucket, m in by_conf.items():
                    bpf = m.get('profit_factor', 0)
                    bwr = m.get('win_rate_pct', 0)
                    bc = "#3fb950" if bpf >= 1 else ("#d29922" if bpf >= 0.7 else "#f85149")
                    conf_html += f'<div class="m-box" style="border-left:3px solid {bc};min-width:120px"><div class="m-val" style="color:{bc}">{bpf:.2f} PF</div><div class="m-lbl">{bucket}: {m.get("trades",0)}T, {bwr:.0f}% WR</div></div>'
                conf_html += '</div>'
                st.markdown(conf_html, unsafe_allow_html=True)

        # Failure Alerts
        failures = diagnostics.get("failures", {})
        if failures:
            alerts = failures.get("recent_alerts", [])
            failure_counts = failures.get("failure_counts", {})
            if alerts:
                st.markdown(f"**🚨 Failure Alerts ({len(alerts)} recent)**")
                for alert in alerts[-5:]:
                    alert_type = alert.get("type", "UNKNOWN")
                    sym = alert.get("symbol", alert.get("component", "?"))
                    st.warning(f"⚠️ {alert_type}: {sym} — {alert.get('error', alert.get('reason', ''))[:80]}")
            if failure_counts:
                st.markdown("**Failure Summary:**")
                fs_html = '<div style="display:flex;gap:4px;flex-wrap:wrap">'
                for ftype, count in failure_counts.items():
                    if count > 0:
                        fs_html += f'<div class="m-box" style="border-color:#f85149"><div class="m-val" style="color:#f85149">{count}</div><div class="m-lbl">{ftype}</div></div>'
                fs_html += '</div>'
                st.markdown(fs_html, unsafe_allow_html=True)

    st.divider()

    # ════════════════════════════════════════════════════════════════
    # ROW 7: PRODUCTION KPI — Historical Signal Frequency
    # ════════════════════════════════════════════════════════════════
    st.markdown("**📈 Production KPI — Signal Frequency & Calibration Baseline**")

    try:
        import sqlite3 as _sqlite3
        _db_path = str(Path(__file__).resolve().parent.parent.parent / "data" / "institutional_v1.db")
        _cal_path = str(Path(__file__).resolve().parent.parent.parent / "data" / "ema_v5_calibration.db")

        # ── Signal frequency from positions DB ──
        _conn = _sqlite3.connect(_db_path)
        _cur = _conn.cursor()

        _today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).timestamp()
        _cur.execute("SELECT COUNT(*) FROM positions WHERE opened_at >= ? AND strategy_version = 'ema_v5'", (_today_start,))
        today_signals = _cur.fetchone()[0]

        _week_start = _today_start - 7 * 86400
        _cur.execute("SELECT COUNT(*) FROM positions WHERE opened_at >= ? AND strategy_version = 'ema_v5'", (_week_start,))
        week_signals = _cur.fetchone()[0]
        week_avg = week_signals / 7.0

        _month_start = _today_start - 30 * 86400
        _cur.execute("SELECT COUNT(*) FROM positions WHERE opened_at >= ? AND strategy_version = 'ema_v5'", (_month_start,))
        month_signals = _cur.fetchone()[0]
        month_avg = month_signals / 30.0

        _cur.execute("SELECT COUNT(*) FROM positions WHERE strategy_version = 'ema_v5'")
        alltime_signals = _cur.fetchone()[0]

        _cur.execute("SELECT COUNT(*) FROM positions WHERE opened_at >= ? AND strategy_version = 'production_v2'", (_today_start,))
        p2_today = _cur.fetchone()[0]
        _cur.execute("SELECT COUNT(*) FROM positions WHERE opened_at >= ? AND strategy_version = 'production_v2'", (_week_start,))
        p2_week = _cur.fetchone()[0]
        _conn.close()

        # ── Calibration metrics ──
        _conn2 = _sqlite3.connect(_cal_path)
        _cur2 = _conn2.cursor()

        _cal_week_start = _today_start - 7 * 86400
        _cur2.execute("""
            SELECT COUNT(*), SUM(CASE WHEN passed=1 THEN 1 ELSE 0 END),
                   AVG(confidence), AVG(volume_score), AVG(trend_score)
            FROM candidates WHERE timestamp >= ?
        """, (_cal_week_start,))
        cal_row = _cur2.fetchone()
        cal_total = cal_row[0] or 0
        cal_passed = cal_row[1] or 0
        cal_avg_conf = cal_row[2] or 0
        cal_avg_vol = cal_row[3] or 0
        cal_avg_trend = cal_row[4] or 0
        cal_pass_rate = (cal_passed / cal_total * 100) if cal_total > 0 else 0

        _cur2.execute("""
            SELECT COUNT(*), SUM(CASE WHEN passed=1 THEN 1 ELSE 0 END),
                   AVG(confidence), AVG(volume_score)
            FROM candidates WHERE timestamp >= ?
        """, (_today_start,))
        cal_today_row = _cur2.fetchone()
        cal_today_total = cal_today_row[0] or 0
        cal_today_passed = cal_today_row[1] or 0
        cal_today_conf = cal_today_row[2] or 0
        cal_today_vol = cal_today_row[3] or 0
        _conn2.close()

        # ── Signal frequency row ──
        k1, k2, k3, k4, k5, k6 = st.columns(6)
        with k1:
            st.markdown(f"""<div class="m-box"><div class="m-val" style="color:{'#3fb950' if today_signals > 0 else '#f85149'}">{today_signals}</div><div class="m-lbl">EMA V5 Today</div></div>""", unsafe_allow_html=True)
        with k2:
            st.markdown(f"""<div class="m-box"><div class="m-val">{week_avg:.1f}</div><div class="m-lbl">7-Day Avg/Day</div></div>""", unsafe_allow_html=True)
        with k3:
            st.markdown(f"""<div class="m-box"><div class="m-val">{month_avg:.1f}</div><div class="m-lbl">30-Day Avg/Day</div></div>""", unsafe_allow_html=True)
        with k4:
            st.markdown(f"""<div class="m-box"><div class="m-val">{alltime_signals}</div><div class="m-lbl">EMA V5 Total</div></div>""", unsafe_allow_html=True)
        with k5:
            st.markdown(f"""<div class="m-box"><div class="m-val">{p2_today}</div><div class="m-lbl">production_v2 Today</div></div>""", unsafe_allow_html=True)
        with k6:
            st.markdown(f"""<div class="m-box"><div class="m-val">{p2_week}</div><div class="m-lbl">prod_v2 7-Day</div></div>""", unsafe_allow_html=True)

        # ── Calibration baseline row ──
        cal1, cal2, cal3, cal4, cal5 = st.columns(5)
        with cal1:
            st.markdown(f"""<div class="m-box"><div class="m-val">{cal_today_total}</div><div class="m-lbl">Candidates Today</div></div>""", unsafe_allow_html=True)
        with cal2:
            st.markdown(f"""<div class="m-box"><div class="m-val">{cal_pass_rate:.1f}%</div><div class="m-lbl">7-Day Pass Rate</div></div>""", unsafe_allow_html=True)
        with cal3:
            st.markdown(f"""<div class="m-box"><div class="m-val">{cal_avg_conf:.1f}</div><div class="m-lbl">7-Day Avg Conf</div></div>""", unsafe_allow_html=True)
        with cal4:
            st.markdown(f"""<div class="m-box"><div class="m-val">{cal_avg_vol:.1f}</div><div class="m-lbl">7-Day Avg Vol</div></div>""", unsafe_allow_html=True)
        with cal5:
            st.markdown(f"""<div class="m-box"><div class="m-val">{cal_avg_trend:.1f}</div><div class="m-lbl">7-Day Avg Trend</div></div>""", unsafe_allow_html=True)

        # ── Context note ──
        if today_signals == 0 and week_avg < 1:
            st.warning("⚠️ EMA V5 has produced zero signals this week. Volume-gate calibration may be too restrictive for current market conditions.")
        elif today_signals == 0:
            st.info("ℹ️ No EMA V5 signals today yet. See 7-day average for baseline.")
    except Exception as e:
        st.caption(f"Production KPI unavailable: {e}")

    # ════════════════════════════════════════════════════════════════
    # ROW 8: PIPELINE MONITOR — Stall Detection & Daily Reconciliation
    # ════════════════════════════════════════════════════════════════
    pipeline_monitor = data.get("pipeline_monitor", {})
    if pipeline_monitor:
        st.markdown("**🛡️ Pipeline Monitor — Health & Reconciliation**")

        # Stall Detection
        stall = pipeline_monitor.get("stall_detector", {})
        pm1, pm2, pm3, pm4 = st.columns(4)
        with pm1:
            stalled = stall.get("stall_detected", False)
            sc = "#f85149" if stalled else "#3fb950"
            stall_text = "🚨 YES" if stalled else "✅ No"
            st.markdown(f"""<div class="m-box" style="border-left:3px solid {sc}"><div class="m-val" style="color:{sc}">{stall_text}</div><div class="m-lbl">Stall Detected</div></div>""", unsafe_allow_html=True)
        with pm2:
            st.markdown(f"""<div class="m-box"><div class="m-val">{stall.get('hours_since_last_signal', 0):.1f}h</div><div class="m-lbl">Since Last Signal</div></div>""", unsafe_allow_html=True)
        with pm3:
            st.markdown(f"""<div class="m-box"><div class="m-val">{stall.get('consecutive_zero_cycles', 0)}</div><div class="m-lbl">Zero-Signal Cycles</div></div>""", unsafe_allow_html=True)
        with pm4:
            st.markdown(f"""<div class="m-box"><div class="m-val">{stall.get('last_signal_symbol', 'N/A')}</div><div class="m-lbl">Last Signal</div></div>""", unsafe_allow_html=True)

        # State Age Monitoring — detect stuck candidates
        _now = time.time()
        _states_data = data.get("states", {})
        _wc_ages = []
        _wp_ages = []
        for _sym, _sdata in _states_data.items():
            _state = _sdata.get("state", "")
            _last_update = _sdata.get("last_update", 0)
            _age_min = (_now - _last_update) / 60 if _last_update > 0 else 0
            if _state == "WAITING_CONFIRMATION":
                _wc_ages.append((_sym, _age_min))
            elif _state == "WAITING_PULLBACK":
                _wp_ages.append((_sym, _age_min))

        _wc_max = max(_wc_ages, key=lambda x: x[1], default=("N/A", 0))
        _wp_max = max(_wp_ages, key=lambda x: x[1], default=("N/A", 0))
        _stuck_count = sum(1 for _, a in _wc_ages if a > 30) + sum(1 for _, a in _wp_ages if a > 60)

        sa1, sa2, sa3, sa4 = st.columns(4)
        with sa1:
            _wc_color = "#f85149" if _wc_max[1] > 30 else ("#d29922" if _wc_max[1] > 15 else "#3fb950")
            st.markdown(f"""<div class="m-box" style="border-left:3px solid {_wc_color}"><div class="m-val" style="color:{_wc_color}">{_wc_max[1]:.1f}m</div><div class="m-lbl">Oldest WAITING_CONFIRMATION ({_wc_max[0]})</div></div>""", unsafe_allow_html=True)
        with sa2:
            _wp_color = "#f85149" if _wp_max[1] > 60 else ("#d29922" if _wp_max[1] > 30 else "#3fb950")
            st.markdown(f"""<div class="m-box" style="border-left:3px solid {_wp_color}"><div class="m-val" style="color:{_wp_color}">{_wp_max[1]:.1f}m</div><div class="m-lbl">Oldest WAITING_PULLBACK ({_wp_max[0]})</div></div>""", unsafe_allow_html=True)
        with sa3:
            _sc_color = "#f85149" if _stuck_count > 0 else "#3fb950"
            st.markdown(f"""<div class="m-box" style="border-left:3px solid {_sc_color}"><div class="m-val" style="color:{_sc_color}">{_stuck_count}</div><div class="m-lbl">Stuck Candidates</div></div>""", unsafe_allow_html=True)
        with sa4:
            _tc = pipeline_monitor.get("transition_timeout", {})
            _timeouts = sum(_tc.get("timeouts_triggered", {}).values())
            st.markdown(f"""<div class="m-box"><div class="m-val">{_timeouts}</div><div class="m-lbl">Timeouts Triggered</div></div>""", unsafe_allow_html=True)

        # Daily Reconciliation
        recon = pipeline_monitor.get("daily_reconciliation", {})
        if recon:
            funnel = recon.get("funnel", {})
            st.markdown(f"**📅 Daily Reconciliation ({recon.get('date', 'today')})**")
            dr1, dr2, dr3, dr4, dr5, dr6 = st.columns(6)
            with dr1:
                st.markdown(f"""<div class="m-box"><div class="m-val">{funnel.get('scanned', 0)}</div><div class="m-lbl">Scanned</div></div>""", unsafe_allow_html=True)
            with dr2:
                st.markdown(f"""<div class="m-box"><div class="m-val">{funnel.get('regime_pass', 0)}</div><div class="m-lbl">Regime Pass</div></div>""", unsafe_allow_html=True)
            with dr3:
                st.markdown(f"""<div class="m-box"><div class="m-val">{funnel.get('pullback_pass', 0)}</div><div class="m-lbl">Pullback Pass</div></div>""", unsafe_allow_html=True)
            with dr4:
                st.markdown(f"""<div class="m-box"><div class="m-val">{funnel.get('candle_pass', 0)}</div><div class="m-lbl">Candle Pass</div></div>""", unsafe_allow_html=True)
            with dr5:
                st.markdown(f"""<div class="m-box"><div class="m-val">{funnel.get('confidence_pass', 0)}</div><div class="m-lbl">Confidence Pass</div></div>""", unsafe_allow_html=True)
            with dr6:
                st.markdown(f"""<div class="m-box"><div class="m-val">{funnel.get('published', 0)}</div><div class="m-lbl">Published</div></div>""", unsafe_allow_html=True)

            # Daily rejection breakdown
            rejections = recon.get("rejections", {})
            if rejections:
                st.markdown("**🚫 Today's Rejections:**")
                rej_html = '<div style="display:flex;gap:4px;flex-wrap:wrap">'
                for stage, count in sorted(rejections.items(), key=lambda x: -x[1]):
                    rej_html += f'<div class="m-box" style="border-color:#f85149;min-width:100px"><div class="m-val" style="color:#f85149">{count}</div><div class="m-lbl">{stage}</div></div>'
                rej_html += '</div>'
                st.markdown(rej_html, unsafe_allow_html=True)

    # ════════════════════════════════════════════════════════════════
    # ROW 9: SIGNAL REJECTION TRACKER — Execution Path Audit
    # ════════════════════════════════════════════════════════════════
    signal_trace = data.get("signal_rejection_tracker", {})
    if signal_trace:
        st.markdown("**🔍 Signal Rejection Tracker — Execution Path Audit**")
        
        # Summary metrics
        summary = signal_trace.get("daily_summary", {})
        sr1, sr2, sr3, sr4, sr5 = st.columns(5)
        with sr1:
            generated = summary.get("generated", 0)
            st.markdown(f"""<div class="m-box"><div class="m-val">{generated}</div><div class="m-lbl">Generated</div></div>""", unsafe_allow_html=True)
        with sr2:
            published = summary.get("published", 0)
            st.markdown(f"""<div class="m-box"><div class="m-val">{published}</div><div class="m-lbl">Published</div></div>""", unsafe_allow_html=True)
        with sr3:
            passed = summary.get("passed", 0)
            pass_rate = (passed / generated * 100) if generated > 0 else 0
            pc = "#3fb950" if pass_rate > 10 else "#d29922"
            st.markdown(f"""<div class="m-box" style="border-left:3px solid {pc}"><div class="m-val" style="color:{pc}">{passed} ({pass_rate:.1f}%)</div><div class="m-lbl">Positions Opened</div></div>""", unsafe_allow_html=True)
        with sr4:
            rejected = summary.get("rejected", 0)
            session_rej = summary.get("rejected_session", 0)
            total_rej = rejected + session_rej
            rc = "#f85149" if total_rej > 0 else "#3fb950"
            st.markdown(f"""<div class="m-box" style="border-left:3px solid {rc}"><div class="m-val" style="color:{rc}">{total_rej}</div><div class="m-lbl">Total Rejected</div></div>""", unsafe_allow_html=True)
        with sr5:
            # Identity check
            recon = signal_trace.get("reconciliation", {})
            overall = recon.get("overall", {})
            identity_ok = overall.get("identity_ok", True)
            ic = "#3fb950" if identity_ok else "#f85149"
            it = "✅ Balanced" if identity_ok else "❌ Discrepancy"
            st.markdown(f"""<div class="m-box" style="border-left:3px solid {ic}"><div class="m-val" style="color:{ic}">{it}</div><div class="m-lbl">Reconciliation</div></div>""", unsafe_allow_html=True)

        # Reconciliation identity panel
        recon = signal_trace.get("reconciliation", {})
        if recon:
            l1 = recon.get("level1", {})
            l2 = recon.get("level2", {})
            overall = recon.get("overall", {})
            
            st.markdown("**📊 Daily Reconciliation Identity:**")
            rc1, rc2, rc3 = st.columns(3)
            with rc1:
                l1_ok = l1.get("identity_ok", True)
                l1c = "#3fb950" if l1_ok else "#f85149"
                l1t = "✅" if l1_ok else "❌"
                st.markdown(f"""<div class="m-box" style="border-left:3px solid {l1c}">
                    <div class="m-val" style="color:{l1c}">{l1t} {l1.get('generated', 0)} = {l1.get('published', 0)} + {l1.get('session_rejected', 0)}</div>
                    <div class="m-lbl">Level 1: Scanner → Session Filter</div>
                </div>""", unsafe_allow_html=True)
            with rc2:
                l2_ok = l2.get("identity_ok", True)
                l2c = "#3fb950" if l2_ok else "#f85149"
                l2t = "✅" if l2_ok else "❌"
                st.markdown(f"""<div class="m-box" style="border-left:3px solid {l2c}">
                    <div class="m-val" style="color:{l2c}">{l2t} {l2.get('published', 0)} = {l2.get('opened', 0)} + {l2.get('execution_rejected', 0)}</div>
                    <div class="m-lbl">Level 2: Session → Execution</div>
                </div>""", unsafe_allow_html=True)
            with rc3:
                o_ok = overall.get("identity_ok", True)
                oc = "#3fb950" if o_ok else "#f85149"
                ot = "✅" if o_ok else "❌"
                st.markdown(f"""<div class="m-box" style="border-left:3px solid {oc}">
                    <div class="m-val" style="color:{oc}">{ot} {overall.get('generated', 0)} = {overall.get('opened', 0)} + {overall.get('total_rejected', 0)}</div>
                    <div class="m-lbl">Overall: Generated = Opened + Rejected</div>
                </div>""", unsafe_allow_html=True)

        # Rejection breakdown by gate
        breakdown = signal_trace.get("breakdown", {})
        if breakdown:
            st.markdown("**🚫 Rejection Breakdown by Gate:**")
            bk_html = '<div style="display:flex;gap:4px;flex-wrap:wrap">'
            # Include session rejections from summary
            session_rej = summary.get("rejected_session", 0)
            if session_rej > 0:
                bk_html += f'<div class="m-box" style="border-color:#f85149;min-width:120px"><div class="m-val" style="color:#f85149">{session_rej}</div><div class="m-lbl">session_filter</div></div>'
            for gate, count in sorted(breakdown.items(), key=lambda x: -x[1]):
                if gate != "session_filter":  # Already shown above
                    bk_html += f'<div class="m-box" style="border-color:#f85149;min-width:120px"><div class="m-val" style="color:#f85149">{count}</div><div class="m-lbl">{gate}</div></div>'
            bk_html += '</div>'
            st.markdown(bk_html, unsafe_allow_html=True)

        # Recent rejections table
        recent_rejections = signal_trace.get("recent_rejections", [])
        if recent_rejections:
            st.markdown("**📋 Recent Rejections (last 10):**")
            rej_rows = []
            for r in recent_rejections[:10]:
                rej_rows.append({
                    "Symbol": r.get("symbol", "?"),
                    "Side": r.get("side", "?"),
                    "Conf%": f"{r.get('confidence', 0):.1f}",
                    "Regime": r.get("regime", "?"),
                    "Rejection Gate": r.get("rejection_gate", "?"),
                    "Reason": r.get("rejection_reason", "?")[:60],
                })
            st.dataframe(rej_rows, use_container_width=True, hide_index=True)

        # Recent opened positions
        recent_opened = signal_trace.get("recent_opened", [])
        if recent_opened:
            st.markdown("**🚀 Recent Positions Opened (last 5):**")
            for o in recent_opened[:5]:
                st.markdown(f"- **{o.get('symbol', '?')}** {o.get('side', '?')} @ {o.get('entry_price', 0):.6f} (conf={o.get('confidence', 0):.1f}%, R:R={o.get('risk_reward', 0):.2f})")

    # ── Footer ──
    st.caption(f"🏛️ EMA V5 Scanner — DeltaTerminal v2.5 — {_dt(time.time())} ({st.session_state.get('tz_name', 'UTC')})")


if __name__ == "__main__":
    main()
