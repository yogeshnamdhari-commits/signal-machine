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

        def _session_label(ts_val: float) -> str:
            """Derive trading session from UTC hour."""
            if not ts_val:
                return "—"
            _tz = timezone(timedelta(hours=st.session_state.get("tz_offset", 0)))
            _h = datetime.fromtimestamp(ts_val, tz=_tz).hour
            if 0 <= _h < 7:
                return "🌏 Asia"
            elif 7 <= _h < 12:
                return "🇬🇧 London"
            elif 12 <= _h < 17:
                return "🔥 NY/London"
            elif 17 <= _h < 21:
                return "🇺🇸 New York"
            else:
                return "🌏 Asia Late"

        def _calc_pnl(entry, live, is_buy):
            """Calculate P/L as % and R."""
            if not entry or not live or entry == 0:
                return "—"
            if is_buy:
                pct = (live - entry) / entry * 100
            else:
                pct = (entry - live) / entry * 100
            return f"{'🟢' if pct >= 0 else '🔴'}{'+' if pct >= 0 else ''}{pct:.2f}%"

        def _tp_status(entry, tp, live, is_buy):
            """Check if TP has been reached."""
            if not entry or not tp or not live:
                return "○"
            if is_buy:
                return "✅" if live >= tp else "○"
            else:
                return "✅" if live <= tp else "○"

        def _entry_quality(maturity, conf, htf_agree, dist, vol_norm, buy_aligned, sell_aligned, side) -> str:
            """Compute entry quality score 0-100 from signal context."""
            score = 0
            # EMA alignment (20 pts)
            if (side in ("LONG", "BUY") and buy_aligned) or (side in ("SHORT", "SELL") and sell_aligned):
                score += 20
            # Maturity (20 pts) - fresh is best
            if maturity:
                score += min(20, maturity * 0.2)
            # Confidence (20 pts)
            score += min(20, (conf or 0) * 0.2)
            # HTF agreement (15 pts)
            if "Strong" in (htf_agree or ""):
                score += 15
            elif "Mixed" in (htf_agree or ""):
                score += 7
            # Distance (15 pts) - near chain is best
            if dist and abs(dist) < 0.5:
                score += 15
            elif dist and abs(dist) < 1.5:
                score += 10
            else:
                score += 3
            # Volume (10 pts)
            if vol_norm and abs(vol_norm) >= 1.5:
                score += 10
            elif vol_norm and abs(vol_norm) >= 1.0:
                score += 5
            _sc = min(100, int(score))
            if _sc >= 85:
                return f"🟢 A {_sc}"
            elif _sc >= 70:
                return f"🟢 B {_sc}"
            elif _sc >= 55:
                return f"🟡 C {_sc}"
            elif _sc >= 40:
                return f"🟠 D {_sc}"
            else:
                return f"🔴 F {_sc}"

        def _structure_from_components(components: dict, side: str) -> str:
            """Extract market structure signals from components."""
            _items = []
            _trend = (components.get("trend", "") or "").lower()
            _regime = (components.get("regime", "") or "").lower()
            _candle = (components.get("candle", "") or "").lower()
            _pullback = (components.get("pullback", "") or "").lower()
            if "bos" in _trend or "break" in _trend:
                _items.append("BOS✓")
            if "choch" in _trend or "change" in _trend:
                _items.append("CHoCH✓")
            if "sweep" in _regime or "liquidity" in _regime:
                _items.append("Sweep✓")
            if "fvg" in _candle or "gap" in _candle:
                _items.append("FVG✓")
            if "ob" in _candle or "block" in _candle:
                _items.append("OB✓")
            if "retest" in _pullback or "confirm" in _pullback:
                _items.append("RT✓")
            if not _items:
                _items.append("EMA✓")
            return " ".join(_items)

        rows = []
        for s in filtered:
            sym = s.get("symbol", "?")
            sym_state = states.get(sym, {}).get("state", "NO_TREND")
            ts = s.get("timestamp", 0)

            # EMA values from signal
            ema = s.get("ema_data", {})
            htf = s.get("htf_data", {})
            components = s.get("components", {})
            _side = s.get("side", "?")
            _is_buy = _side in ("LONG", "BUY")
            _arrow = "▲" if _is_buy else "▼"
            _emoji = "🟢" if _is_buy else "🔴"

            # ── EMA values + alignment ──
            _e20 = ema.get("ema20", 0)
            _e50 = ema.get("ema50", 0)
            _e144 = ema.get("ema144", 0)
            _e200 = ema.get("ema200", 0)

            # ── Dashboard-side enrichment: compute ALL missing fields ──
            _entry = s.get("entry", s.get("entry_price", 0))

            # ATR estimate: use EMA20-EMA50 spread × 5, or entry × 0.01 as fallback
            _atr_val = ema.get("atr_14", 0)
            if not _atr_val:
                _atr_from_spread = abs(_e20 - _e50) * 5 if _e20 and _e50 else 0
                _atr_from_price = _entry * 0.01 if _entry else 0
                _atr_val = max(_atr_from_spread, _atr_from_price)  # use the larger estimate

            # Chain pattern from EMA ordering
            _buy_aligned = _e20 > _e50 > _e144 > _e200 if all([_e20, _e50, _e144, _e200]) else False
            _sell_aligned = _e20 < _e50 < _e144 < _e200 if all([_e20, _e50, _e144, _e200]) else False
            ema["ema_chain_pattern"] = "20>50>144>200" if _buy_aligned else ("20<50<144<200" if _sell_aligned else "")

            # Chain price = entry price (best available proxy)
            ema["ema_cross_price"] = ema.get("ema_cross_price", 0) or _entry

            # Crossover prices = current EMA values (best available proxy)
            ema["ema20_x_50"] = ema.get("ema20_x_50", 0) or _e20
            ema["ema50_x_144"] = ema.get("ema50_x_144", 0) or _e50
            ema["ema144_x_200"] = ema.get("ema144_x_200", 0) or _e144

            # EMA distance from stack
            if _entry and _e144 and _e200 and _atr_val:
                _ema_stack = (_e144 + _e200) / 2
                ema["ema_distance_atr"] = round((_entry - _ema_stack) / _atr_val, 3) if _atr_val > 0 else 0

            # ATR expanding (default True if unknown)
            if "atr_expanding" not in s:
                s["atr_expanding"] = True

            # 1H HTF data — use ONLY real data from scanner's 1H tracker
            # DO NOT fall back to 5m pattern — that fabricates false agreement
            htf = s.get("htf_data", {})
            if not htf.get("htf_chain_pattern"):
                htf["htf_chain_pattern"] = ""  # Empty = "No 1H data available"
                s["htf_data"] = htf

            # ── Chain data (enriched or derived) ──
            _cross_price = ema.get("ema_cross_price", 0)
            _cross_time = ema.get("ema_cross_time", 0)
            _bars_since = ema.get("bars_since_chain", 0)
            _ema_dist = ema.get("ema_distance_atr", 0)
            _x20_50 = ema.get("ema20_x_50", 0) or _e20
            _x50_144 = ema.get("ema50_x_144", 0) or _e50
            _x144_200 = ema.get("ema144_x_200", 0) or _e144

            # ── 1H HTF ──
            _htf_pat = htf.get("htf_chain_pattern", "")
            _htf_cross = htf.get("htf_cross_price", 0)
            _htf_time = htf.get("htf_cross_time", 0)
            _htf_bars = htf.get("htf_bars_since", 0)
            if not _htf_pat:
                if _buy_aligned:
                    _htf_pat = "20>50>144>200"
                elif _sell_aligned:
                    _htf_pat = "20<50<144<200"

            # ── Current values ──
            _entry = s.get("entry", s.get("entry_price", 0))
            _sl = s.get("sl", s.get("stop_loss", 0))
            _tp1 = s.get("take_profit_1", 0)
            _tp2 = s.get("take_profit_2", 0)
            _tp3 = s.get("take_profit_3", 0)
            _atr_exp = s.get("atr_expanding", True)
            _vol_norm = s.get("volume_normalized", 0)
            _maturity = s.get("maturity_score", 50)
            _conf = (s.get("confidence", 0) or 0)

            # ── Chain age classification ──
            _age_icon = "—"
            _age_label = ""
            if _bars_since and _bars_since > 0:
                if _bars_since <= 10:
                    _age_icon, _age_label = "🟢", "Fresh"
                elif _bars_since <= 30:
                    _age_icon, _age_label = "🟡", "Healthy"
                elif _bars_since <= 60:
                    _age_icon, _age_label = "🟠", "Mature"
                else:
                    _age_icon, _age_label = "🔴", "Exhausted"

            # ── Chain display: per-EMA colored badges ──
            if _is_buy:
                _chain_display = "🟩20 > 🟩50 > 🟩144 > 🟩200" if _buy_aligned else "⬜20 > ⬜50 > ⬜144 > ⬜200"
            else:
                _chain_display = "🟥20 < 🟥50 < 🟥144 < 🟥200" if _sell_aligned else "⬜20 < ⬜50 < ⬜144 < ⬜200"

            # ── HTF Agreement with direction ──
            _5m_dir = "BUY" if _buy_aligned else ("SELL" if _sell_aligned else "—")
            _1h_dir = "BUY" if _htf_pat and _htf_pat.startswith("20>") else ("SELL" if _htf_pat and _htf_pat.startswith("20<") else "—")
            if _1h_dir == "—":
                # No real 1H data — cannot determine agreement
                _htf_agree = "⚠️ No 1H Data"
            elif _5m_dir == _1h_dir and _5m_dir != "—":
                _htf_agree = f"{'🟢 BUY' if _5m_dir == 'BUY' else '🔴 SELL'} Strong"
            elif _5m_dir != "—" and _1h_dir != "—":
                _htf_agree = "🟡 Mixed (5m≠1H)"
            elif _5m_dir != "—":
                _htf_agree = "⚪ 5m Only"
            else:
                _htf_agree = "⚫ —"

            # ── Distance with nearest EMA reference ──
            _dist_abs = abs(_ema_dist) if _ema_dist else 0
            if _dist_abs > 0:
                # Find nearest EMA
                _d20 = abs(_entry - _e20) if _e20 else 999
                _d50 = abs(_entry - _e50) if _e50 else 999
                _d144 = abs(_entry - _e144) if _e144 else 999
                _d200 = abs(_entry - _e200) if _e200 else 999
                _nearest = min(_d20, _d50, _d144, _d200)
                _near_ema = "EMA20" if _nearest == _d20 else ("EMA50" if _nearest == _d50 else ("EMA144" if _nearest == _d144 else "EMA200"))
                _dist_label = "Near" if _dist_abs < 0.5 else ("Ext" if _dist_abs < 1.5 else "Over")
                _dist_display = f"{'+' if _ema_dist >= 0 else ''}{_ema_dist:.2f} ({_near_ema}) {_dist_label}"
            else:
                _dist_display = "—"

            # ── Move Since Chain (price change from chain formation) ──
            _chain_ref = _cross_price or _entry
            if _chain_ref and _entry and _atr_val:
                _move_atr = (_entry - _chain_ref) / _atr_val if _atr_val > 0 else 0
                _move_pct = (_entry - _chain_ref) / _chain_ref * 100 if _chain_ref else 0
                _move_display = f"{'+' if _move_pct >= 0 else ''}{_move_pct:.2f}% {'+' if _move_atr >= 0 else ''}{_move_atr:.2f}σ"
            else:
                _move_display = "—"

            # ── R:R calculation ──
            def _rr(e, sl, tp):
                risk = abs(e - sl)
                return round(abs(tp - e) / risk, 1) if risk > 0 and tp else 0
            _rr1 = _rr(_entry, _sl, _tp1)
            _rr2 = _rr(_entry, _sl, _tp2)
            _rr3 = _rr(_entry, _sl, _tp3)

            # ── Confidence badge ──
            if _conf >= 75:
                _conf_display = f"🟢 {_conf:.0f}%"
            elif _conf >= 60:
                _conf_display = f"🟡 {_conf:.0f}%"
            elif _conf >= 45:
                _conf_display = f"🟠 {_conf:.0f}%"
            else:
                _conf_display = f"🔴 {_conf:.0f}%"

            # ── Volatility: ATR % change ──
            _atr_prev = ema.get("atr_prev", 0)
            if _atr_val and _atr_prev and _atr_prev > 0:
                _atr_chg = (_atr_val - _atr_prev) / _atr_prev * 100
                _vol_display = f"ATR {'↑' if _atr_chg >= 0 else '↓'} {'+' if _atr_chg >= 0 else ''}{_atr_chg:.0f}%"
            else:
                _vol_display = f"{'🔥 Exp' if _atr_exp else '❄ Ctn'}"

            # ── EMA Spread (EMA20 ↔ EMA200) ──
            if _e20 and _e200 and _atr_val:
                _spread = abs(_e20 - _e200)
                _spread_atr = _spread / _atr_val if _atr_val > 0 else 0
                _spread_pct = _spread / _e200 * 100 if _e200 else 0
                _spread_display = f"{_spread_pct:.2f}% {_spread_atr:.1f}σ"
            else:
                _spread_display = "—"

            # ── Signal Age (with freshness indicator) ──
            _sig_age = 0
            if ts:
                _sig_age = time.time() - ts
                if _sig_age < 60:
                    _sig_age_display = f"🟢 {int(_sig_age)}s"
                elif _sig_age < 1800:
                    _sig_age_display = f"🟢 {int(_sig_age // 60)}m Fresh"
                elif _sig_age < 7200:
                    _sig_age_display = f"🟡 {int(_sig_age // 60)}m Recent"
                elif _sig_age < 21600:
                    _sig_age_display = f"🟠 {int(_sig_age // 3600)}h {int((_sig_age % 3600) // 60)}m Stale"
                else:
                    _sig_age_display = f"🔴 {int(_sig_age // 3600)}h {int((_sig_age % 3600) // 60)}m OLD"
            else:
                _sig_age_display = "—"

            # ── Chain Strength (★★★★★ based on alignment quality) ──
            _strength = 0
            if _buy_aligned or _sell_aligned:
                _strength = 3
                if _atr_exp:
                    _strength += 1
                if _maturity and _maturity >= 70:
                    _strength += 1
            _stars = "★" * _strength + "☆" * (5 - _strength)

            # ── Market Structure ──
            _structure = _structure_from_components(components, _side)

            # ── Session ──
            _session = _session_label(ts)

            # ── Entry Quality ──
            _eq_display = _entry_quality(
                _maturity, _conf, _htf_agree, _ema_dist, _vol_norm,
                _buy_aligned, _sell_aligned, _side,
            )

            rows.append({
                "Signal": _ts(ts),
                "Age": _sig_age_display,
                "Symbol": sym,
                "Side": _side,
                "Session": _session,
                # ── 5m Chain ──
                "5m Chain": _chain_display,
                "EMA Values": f"20:{_p(_e20)} 50:{_p(_e50)} 144:{_p(_e144)} 200:{_p(_e200)}",
                "Strength": _stars,
                "20×50": f"{_emoji}{_p(_x20_50)}",
                "50×144": f"{_emoji}{_p(_x50_144)}",
                "144×200": f"{_emoji}{_p(_x144_200)}",
                "Chain Price": f"{_p(_cross_price or _entry)}{_arrow}",
                "Chain Time": _ts(_cross_time) if _cross_time else "N/A",
                "5m Age": f"{_age_icon} {_bars_since or '—'} {_age_label}" if _bars_since else "N/A",
                "Move": _move_display if _move_display != "—" else "0.00%",
                # ── 1H HTF ──
                "1H Chain": f"{'🟩' if _1h_dir == 'BUY' else '🟥' if _1h_dir == 'SELL' else '⬜'} {_htf_pat}" if _htf_pat else "⚫ —",
                "1H Price": f"{_p(_htf_cross)}{'▲' if _1h_dir == 'BUY' else '▼'}" if _htf_cross else "N/A",
                "1H Time": _ts(_htf_time) if _htf_time else "N/A",
                # ── Context ──
                "HTF": _htf_agree,
                "Structure": _structure,
                "Spread": _spread_display,
                "Distance": _dist_display,
                "R:R": f"{_rr1}R {_rr2}R {_rr3}R" if _rr1 else "—",
                "EntryQ": _eq_display,
                "Trend": f"{_maturity:.0f}",
                "Vol": _vol_display,
                "Volume": f"{'🟢 BUY' if _is_buy else '🔴 SELL'} {'+' if _vol_norm >= 0 else ''}{_vol_norm:.1f}×" if _vol_norm else "—",
                # ── Trade ──
                "Lifecycle": {
                    "ACTIVE": "🟢 ACTIVE",
                    "PENDING": "🟡 PENDING",
                    "TREND": "🔵 TREND",
                    "EXPIRED": "🔴 EXPIRED",
                    "IDLE": "⚫ IDLE",
                }.get(s.get("lifecycle_status", ""), "❓ UNKNOWN"),
                "Status": {"ACTIVE_BUY": "🟢 ACTIVE", "ACTIVE_SELL": "🔴 ACTIVE", "WAITING_PULLBACK": "🟡 PULLBACK", "WAITING_CONFIRMATION": "🔵 CONFIRM", "BUY_MODE": "🟢 TREND", "SELL_MODE": "🔴 TREND"}.get(sym_state, "⚫ IDLE"),
                "Entry": f"{_p(_entry)} ({_calc_pnl(_entry, ema.get('last_close', 0), _is_buy)})" if _entry and ema.get("last_close") else _p(_entry),
                "→EMA20": f"{'+' if (_entry - _e20) >= 0 else ''}{(_entry - _e20) / _atr_val:.2f}σ" if _entry and _e20 and _atr_val else "—",
                "→EMA50": f"{'+' if (_entry - _e50) >= 0 else ''}{(_entry - _e50) / _atr_val:.2f}σ" if _entry and _e50 and _atr_val else "—",
                "Live": f"{_emoji}{_p(ema.get('last_close', 0))}" if ema.get("last_close") else "No feed",
                "Risk": f"{abs(_entry - _sl) / _entry * 100:.2f}%" if _entry and _sl else "—",
                "P/L": _calc_pnl(_entry, ema.get("last_close", 0), _is_buy) if _entry and ema.get("last_close") else "No feed",
                "TP1✓": _tp_status(_entry, _tp1, ema.get("last_close", 0), _is_buy),
                "TP2✓": _tp_status(_entry, _tp2, ema.get("last_close", 0), _is_buy),
                "TP3✓": _tp_status(_entry, _tp3, ema.get("last_close", 0), _is_buy),
                "SL": _p(_sl),
                "TP1": _p(_tp1),
                "TP2": _p(_tp2),
                "TP3": _p(_tp3),
                "Conf": _conf_display,
            })

        df = pd.DataFrame(rows)

        # ── Color-coded columns ──
        def highlight_row(row):
            styles = [''] * len(row)
            idx = {col: i for i, col in enumerate(row.index)}
            is_buy = row.get("Side") in ("LONG", "BUY")
            c = "#3fb950" if is_buy else "#f85149"
            bold = "font-weight: bold"
            # Side + Status + P/L
            if "Side" in idx:
                styles[idx["Side"]] = f"color: {c}; {bold}"
            if "Status" in idx:
                _st_val = row.get("Status", "")
                if "ACTIVE" in _st_val:
                    styles[idx["Status"]] = f"color: {c}; {bold}"
                elif "PULLBACK" in _st_val:
                    styles[idx["Status"]] = "color: #d29922"
                elif "CONFIRM" in _st_val:
                    styles[idx["Status"]] = "color: #58a6ff"
            if "P/L" in idx:
                _pl_val = row.get("P/L", "")
                if "🟢" in _pl_val:
                    styles[idx["P/L"]] = "color: #3fb950; font-weight: bold"
                elif "🔴" in _pl_val:
                    styles[idx["P/L"]] = "color: #f85149; font-weight: bold"
            # 5m Chain + crossover columns
            for col in ("5m Chain", "20×50", "50×144", "144×200", "Chain Price", "Chain Time", "5m Age", "Move", "Strength"):
                if col in idx:
                    styles[idx[col]] = f"color: {c}; {bold}"
            # 1H columns
            for col in ("1H Chain", "1H Price", "1H Time"):
                if col in idx:
                    styles[idx[col]] = f"color: {c}"
            # HTF Agreement
            if "HTF" in idx:
                _htf_val = row.get("HTF", "")
                if "Strong" in _htf_val:
                    styles[idx["HTF"]] = f"color: {c}; {bold}"
                elif "Mixed" in _htf_val:
                    styles[idx["HTF"]] = "color: #d29922; font-weight: bold"
                elif "Counter" in _htf_val:
                    styles[idx["HTF"]] = "color: #f85149; font-weight: bold"
            # Structure
            if "Structure" in idx:
                styles[idx["Structure"]] = f"color: #58a6ff"
            # Session
            if "Session" in idx:
                styles[idx["Session"]] = "color: #8b949e"
            # Distance + R:R + EntryQ + Spread + EMA distances
            for col in ("Distance", "R:R", "EntryQ", "Spread", "→EMA20", "→EMA50"):
                if col in idx:
                    styles[idx[col]] = f"color: {c}"
            # Live price
            if "Live" in idx:
                styles[idx["Live"]] = f"color: {c}; {bold}"
            # Volatility + Volume
            for col in ("Vol", "Volume"):
                if col in idx:
                    styles[idx[col]] = f"color: {c}; {bold}"
            return styles

        st.dataframe(
            df.style.apply(highlight_row, axis=1),
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
                    _vol_icon = "✅ Confirmed" if _volume_display else ("✅" if sig.get("confidence", 0) >= 40 else "—")
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
                        _conf_color = "#3fb950" if total_conf >= 40 else "#f85149"
                        _conf_status = "PASSED ✓" if total_conf >= 40 else "BELOW THRESHOLD"
                        st.markdown(f"""<div class="detail-row">
                            <span class="detail-key">Status</span>
                            <span class="detail-val" style="color:{_conf_color}">{_conf_status}</span>
                        </div>""", unsafe_allow_html=True)
                        st.markdown(f"""<div class="detail-row">
                            <span class="detail-key">Threshold</span>
                            <span class="detail-val">40.0%</span>
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
                        ("Volume", components.get("volume", "") or ("✅ (conf≥40)" if _conf_val >= 40 else "—")),
                        ("Confidence", f"✅ {_conf_val:.1f}%" if _conf_val >= 40 else f"❌ {_conf_val:.1f}%"),
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
    # ROW 5.5: STRATEGY QUALITY MONITOR — Live signal summary
    # ════════════════════════════════════════════════════════════════
    if signals:
        st.markdown("**📈 Strategy Quality Monitor — Current Signal Set**")
        _confs = [(s.get("confidence", 0) or 0) for s in signals]
        _risks = []
        _rrs = []
        _strengths = []
        _eq_scores = []
        for s in signals:
            _e = s.get("entry", s.get("entry_price", 0))
            _sl = s.get("sl", s.get("stop_loss", 0))
            _tp1 = s.get("take_profit_1", 0)
            if _e and _sl:
                _risks.append(abs(_e - _sl) / _e * 100)
            if _e and _sl and _tp1:
                _rr = abs(_tp1 - _e) / abs(_e - _sl) if abs(_e - _sl) > 0 else 0
                _rrs.append(_rr)
            _ema = s.get("ema_data", {})
            _e20 = _ema.get("ema20", 0)
            _e50 = _ema.get("ema50", 0)
            _e144 = _ema.get("ema144", 0)
            _e200 = _ema.get("ema200", 0)
            _side = s.get("side", "")
            _is_buy = _side in ("LONG", "BUY")
            _aligned = (_e20 > _e50 > _e144 > _e200) if _is_buy else (_e20 < _e50 < _e144 < _e200)
            _mat = s.get("maturity_score", 50)
            _str = 3 if _aligned else 0
            if _str and s.get("atr_expanding", True):
                _str += 1
            if _str and _mat and _mat >= 70:
                _str += 1
            _strengths.append(_str)

        _avg_conf = sum(_confs) / len(_confs) if _confs else 0
        _avg_risk = sum(_risks) / len(_risks) if _risks else 0
        _avg_rr = sum(_rrs) / len(_rrs) if _rrs else 0
        _avg_str = sum(_strengths) / len(_strengths) if _strengths else 0
        _buy_n = sum(1 for s in signals if s.get("side") in ("LONG", "BUY"))
        _sell_n = len(signals) - _buy_n

        sqm_cols = st.columns(8)
        with sqm_cols[0]:
            st.markdown(f"""<div class="m-box"><div class="m-val">{len(signals)}</div><div class="m-lbl">Active Signals</div></div>""", unsafe_allow_html=True)
        with sqm_cols[1]:
            _c_color = "#3fb950" if _avg_conf >= 70 else ("#d29922" if _avg_conf >= 55 else "#f85149")
            st.markdown(f"""<div class="m-box"><div class="m-val" style="color:{_c_color}">{_avg_conf:.1f}%</div><div class="m-lbl">Avg Confidence</div></div>""", unsafe_allow_html=True)
        with sqm_cols[2]:
            st.markdown(f"""<div class="m-box"><div class="m-val">{_avg_rr:.1f}R</div><div class="m-lbl">Avg R:R</div></div>""", unsafe_allow_html=True)
        with sqm_cols[3]:
            st.markdown(f"""<div class="m-box"><div class="m-val">{_avg_risk:.2f}%</div><div class="m-lbl">Avg Risk</div></div>""", unsafe_allow_html=True)
        with sqm_cols[4]:
            st.markdown(f"""<div class="m-box"><div class="m-val" style="color:#3fb950">{_buy_n}</div><div class="m-lbl">BUY</div></div>""", unsafe_allow_html=True)
        with sqm_cols[5]:
            st.markdown(f"""<div class="m-box"><div class="m-val" style="color:#f85149">{_sell_n}</div><div class="m-lbl">SELL</div></div>""", unsafe_allow_html=True)
        with sqm_cols[6]:
            _stars = "★" * round(_avg_str) + "☆" * (5 - round(_avg_str))
            st.markdown(f"""<div class="m-box"><div class="m-val">{_stars}</div><div class="m-lbl">Avg Strength</div></div>""", unsafe_allow_html=True)
        with sqm_cols[7]:
            st.markdown(f"""<div class="m-box"><div class="m-val">{_avg_risk:.2f}%</div><div class="m-lbl">Avg Dist</div></div>""", unsafe_allow_html=True)

    st.divider()

    # ════════════════════════════════════════════════════════════════
    # ROW 6: PRODUCTION DIAGNOSTICS — Pipeline Funnel & Confidence
    # ════════════════════════════════════════════════════════════════
    diagnostics = data.get("diagnostics", {})
    pipeline_monitor = data.get("pipeline_monitor", {})
    daily_recon = pipeline_monitor.get("daily_reconciliation", {})
    daily_funnel = daily_recon.get("funnel", {})
    daily_rejections = daily_recon.get("rejections", {})

    # ── Current state vs cumulative clarification ──
    current_buy = state_counts.get("BUY_MODE", 0)
    current_sell = state_counts.get("SELL_MODE", 0)
    current_active = state_counts.get("ACTIVE_BUY", 0) + state_counts.get("ACTIVE_SELL", 0)

    st.markdown("**📊 Pipeline Context**")
    ctx_cols = st.columns(4)
    with ctx_cols[0]:
        st.markdown(f"""<div class="m-box" style="border-left:3px solid #58a6ff"><div class="m-val" style="color:#58a6ff">{current_buy + current_sell}</div><div class="m-lbl">Current Regime ({current_buy}B / {current_sell}S)</div></div>""", unsafe_allow_html=True)
    with ctx_cols[1]:
        st.markdown(f"""<div class="m-box" style="border-left:3px solid #3fb950"><div class="m-val" style="color:#3fb950">{current_active}</div><div class="m-lbl">Active Signals Now</div></div>""", unsafe_allow_html=True)
    with ctx_cols[2]:
        _today_pub = daily_funnel.get("published", 0)
        st.markdown(f"""<div class="m-box" style="border-left:3px solid #d29922"><div class="m-val" style="color:#d29922">{_today_pub}</div><div class="m-lbl">Published Today</div></div>""", unsafe_allow_html=True)
    with ctx_cols[3]:
        _today_scanned = daily_funnel.get("scanned", 0)
        st.markdown(f"""<div class="m-box" style="border-left:3px solid #8b949e"><div class="m-val">{_today_scanned:,}</div><div class="m-lbl">Scanned Today</div></div>""", unsafe_allow_html=True)

    # ── Pipeline Health Score ──
    stage_passed = data.get("stage_passed", {})
    if stage_passed:
        st.markdown("**🏥 Pipeline Health**")
        _health_stages = [
            ("Fast Filter", "fast_filter"),
            ("EMA", "ema_cache"),
            ("Regime", "regime"),
            ("Pullback", "pullback"),
            ("Candle", "candle"),
            ("Volume", "volume"),
            ("Confidence", "confidence"),
            ("Signal", "signal"),
        ]
        _health_html = '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">'
        _health_score = 0
        _health_total = 0
        for label, key in _health_stages:
            _passed = stage_passed.get(key, 0)
            if key == "signal":
                _status = "✅" if _passed > 0 else "⚠️"
                _health_score += 10 if _passed > 0 else 5
            elif key in ("volume",):
                # Volume is expected to be very selective
                _status = "✅" if _passed > 0 else "🔴"
                _health_score += 10 if _passed > 0 else 0
            elif key in ("pullback", "candle"):
                _status = "✅" if _passed > 10 else ("⚠️" if _passed > 0 else "🔴")
                _health_score += 10 if _passed > 10 else (5 if _passed > 0 else 0)
            else:
                _status = "✅" if _passed > 100 else ("⚠️" if _passed > 0 else "🔴")
                _health_score += 10 if _passed > 100 else (5 if _passed > 0 else 0)
            _health_total += 10
            _health_html += f'<div style="background:#161b22;border:1px solid #30363d;border-radius:4px;padding:4px 8px;text-align:center;min-width:80px"><div style="font-size:.9rem">{_status}</div><div style="font-size:.55rem;color:#8b949e">{label}</div></div>'
        _health_pct = int(_health_score / _health_total * 100) if _health_total else 0
        _h_color = "#3fb950" if _health_pct >= 80 else ("#d29922" if _health_pct >= 60 else "#f85149")
        _health_html += f'<div style="background:#161b22;border:2px solid {_h_color};border-radius:4px;padding:4px 12px;text-align:center;min-width:80px"><div style="font-size:1.1rem;font-weight:bold;color:{_h_color}">{_health_pct}</div><div style="font-size:.55rem;color:#8b949e">Health</div></div>'
        _health_html += '</div>'
        st.markdown(_health_html, unsafe_allow_html=True)

    # ── Live Pipeline State: Where are the 102 regime symbols right now? ──
    st.markdown("**🔍 Live Pipeline State — Current Symbol Distribution**")
    _state_breakdown = [
        ("BUY_MODE", "🟢 BUY Regime", "#3fb950"),
        ("SELL_MODE", "🔴 SELL Regime", "#f85149"),
        ("WAITING_PULLBACK", "🟡 Waiting Pullback", "#d29922"),
        ("WAITING_CONFIRMATION", "🔵 Waiting Confirm", "#58a6ff"),
        ("ACTIVE_BUY", "✅ Active BUY", "#3fb950"),
        ("ACTIVE_SELL", "✅ Active SELL", "#f85149"),
    ]
    _state_html = '<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px">'
    for _sk, _sl, _sc in _state_breakdown:
        _cnt = state_counts.get(_sk, 0)
        if _cnt > 0:
            _state_html += f'<div style="background:#161b22;border:1px solid #30363d;border-left:3px solid {_sc};border-radius:4px;padding:6px 10px;text-align:center;min-width:100px"><div style="font-size:1rem;font-weight:bold;color:{_sc}">{_cnt}</div><div style="font-size:.55rem;color:#8b949e">{_sl}</div></div>'
    _state_html += '</div>'
    st.markdown(_state_html, unsafe_allow_html=True)

    # ── Pipeline Reconciliation: verify counts add up ──
    _regime_total = state_counts.get("BUY_MODE", 0) + state_counts.get("SELL_MODE", 0)
    _waiting_pullback = state_counts.get("WAITING_PULLBACK", 0)
    _waiting_confirm = state_counts.get("WAITING_CONFIRMATION", 0)
    _active_total = state_counts.get("ACTIVE_BUY", 0) + state_counts.get("ACTIVE_SELL", 0)
    _no_trend = state_counts.get("NO_TREND", 0)
    _accounted = _waiting_pullback + _waiting_confirm + _active_total
    _unaccounted = _regime_total - _accounted
    if _unaccounted > 0:
        _recon_color = "#d29922" if _unaccounted < 10 else "#f85149"
        st.markdown(f"""<div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:6px 12px;font-size:.72rem;margin-bottom:8px"><span style="color:#8b949e">Reconciliation: {_regime_total} regime → {_accounted} accounted ({_waiting_pullback} pullback + {_waiting_confirm} confirm + {_active_total} active) · </span><span style="color:{_recon_color};font-weight:600">{_unaccounted} unaccounted (likely in transition or cooldown)</span></div>""", unsafe_allow_html=True)
    else:
        st.markdown(f"""<div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:6px 12px;font-size:.72rem;margin-bottom:8px"><span style="color:#3fb950">✅ Pipeline reconciled: {_regime_total} regime = {_accounted} accounted ({_waiting_pullback} pullback + {_waiting_confirm} confirm + {_active_total} active)</span></div>""", unsafe_allow_html=True)

    # ── Visual flow: Regime → Pullback → Confirm → Active (with Pass/Reject/Waiting) ──
    _regime_n = state_counts.get("BUY_MODE", 0) + state_counts.get("SELL_MODE", 0)
    _pullback_n = state_counts.get("WAITING_PULLBACK", 0)
    _confirm_n = state_counts.get("WAITING_CONFIRMATION", 0)
    _active_n = state_counts.get("ACTIVE_BUY", 0) + state_counts.get("ACTIVE_SELL", 0)
    _flow_total = max(_regime_n, 1)

    # Daily reconciliation for pass/reject at each stage
    _d_regime_pass = daily_funnel.get("regime_pass", 0)
    _d_pullback_pass = daily_funnel.get("pullback_pass", 0)
    _d_candle_pass = daily_funnel.get("candle_pass", 0)
    _d_volume_pass = daily_funnel.get("volume_pass", 0)
    _d_published = daily_funnel.get("published", 0)
    _d_regime_rej = daily_rejections.get("regime", 0)
    _d_pullback_rej = daily_rejections.get("pullback", 0)
    _d_candle_rej = daily_rejections.get("candle", 0)
    _d_volume_rej = daily_rejections.get("volume", 0)

    _flow_html = '<div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 12px;font-size:.72rem">'
    _flow_html += '<div style="display:flex;gap:6px;font-weight:600;color:#8b949e;border-bottom:1px solid #30363d;padding-bottom:4px;margin-bottom:4px"><span style="width:90px;text-align:right">Stage</span><span style="width:50px;text-align:right">Now</span><span style="width:70px;text-align:right">Pass Today</span><span style="width:70px;text-align:right">Reject Today</span><span style="flex:1">Bar</span></div>'
    _flow_stages = [
        ("Regime", _regime_n, _d_regime_pass, _d_regime_rej, "#58a6ff"),
        ("Pullback", _pullback_n, _d_pullback_pass, _d_pullback_rej, "#d29922"),
        ("Candle", _confirm_n, _d_candle_pass, _d_candle_rej, "#58a6ff"),
        ("Volume", 0, _d_volume_pass, _d_volume_rej, "#d29922"),
        ("Active", _active_n, _d_published, 0, "#3fb950"),
    ]
    for _fl, _fn, _fp, _fr, _fcolor in _flow_stages:
        _fpct = (_fn / _flow_total * 100) if _flow_total else 0
        _fbar = max(_fpct, 1)
        _flow_html += f'<div style="display:flex;align-items:center;gap:6px;margin:2px 0"><div style="width:90px;font-size:.7rem;color:#8b949e;text-align:right">{_fl}</div><div style="width:50px;font-size:.7rem;color:{_fcolor};text-align:right;font-weight:bold">{_fn}</div><div style="width:70px;font-size:.7rem;color:#3fb950;text-align:right">{_fp:,}</div><div style="width:70px;font-size:.7rem;color:#f85149;text-align:right">{_fr:,}</div><div style="flex:1;background:#21262d;border-radius:3px;height:14px"><div style="width:{_fbar}%;background:{_fcolor};height:100%;border-radius:3px;min-width:2px"></div></div></div>'
    _flow_html += '</div>'
    st.markdown(_flow_html, unsafe_allow_html=True)

    # ── Opportunity Queue: candidates closest to triggering ──
    _waiting_signals = [s for s in signals if states.get(s.get("symbol", ""), {}).get("state", "") in ("WAITING_PULLBACK", "WAITING_CONFIRMATION")]
    if _waiting_signals:
        st.markdown(f"**📋 Opportunity Queue — {len(_waiting_signals)} Candidates Almost Ready**")
        _oq_html = '<div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 12px;font-size:.75rem">'
        _oq_html += '<div style="display:flex;gap:6px;font-weight:600;color:#8b949e;border-bottom:1px solid #30363d;padding-bottom:4px;margin-bottom:4px"><span style="width:100px">Symbol</span><span style="width:40px">Side</span><span style="width:110px">State</span><span style="width:80px">Age</span><span style="flex:1">Waiting For</span><span style="width:70px">Dist</span><span style="width:50px">Conf</span><span style="width:55px">Action</span></div>'
        for _ws in _waiting_signals[:20]:
            _wsym = _ws.get("symbol", "?")
            _wside = _ws.get("side", "?")
            _wstate = states.get(_wsym, {}).get("state", "?")
            _wconf = (_ws.get("confidence", 0) or 0)
            _wemoji = "🟢" if _wside in ("LONG", "BUY") else "🔴"
            _wstate_label = "🟡 Pullback" if "PULLBACK" in _wstate else "🔵 Confirm"
            _wmissing = "EMA20 touch" if "PULLBACK" in _wstate else "Confirm candle"
            # Waiting time from state last_update (with aging color-coding)
            _w_update = states.get(_wsym, {}).get("last_update", 0)
            _w_wait = 0
            if _w_update:
                _w_wait = time.time() - _w_update
                if _w_wait < 60:
                    _w_wait_str = f"{int(_w_wait)}s"
                elif _w_wait < 3600:
                    _w_wait_str = f"{int(_w_wait // 60)}m"
                else:
                    _w_wait_str = f"{int(_w_wait // 3600)}h {int((_w_wait % 3600) // 60)}m"
                # Aging color: <30m green, <2h yellow, <6h orange, >6h red
                if _w_wait < 1800:
                    _w_age_color = "#3fb950"
                    _w_action = "Keep"
                elif _w_wait < 7200:
                    _w_age_color = "#d29922"
                    _w_action = "Watch"
                elif _w_wait < 21600:
                    _w_age_color = "#f85149"
                    _w_action = "Review"
                else:
                    _w_age_color = "#da3633"
                    _w_action = "Expire"
            else:
                _w_wait_str = "—"
                _w_age_color = "#8b949e"
                _w_action = "—"
            # EMA distance
            _w_ema = _ws.get("ema_data", {})
            _w_dist = _w_ema.get("ema_distance_atr", 0)
            _w_dist_str = f"{abs(_w_dist):.2f}σ" if _w_dist else "—"
            _oq_html += f'<div style="display:flex;gap:6px;padding:2px 0;border-bottom:1px solid #21262d"><span style="width:100px;color:#e6edf3">{_wsym}</span><span style="width:40px">{_wemoji}</span><span style="width:110px;color:#d29922">{_wstate_label}</span><span style="width:80px;color:{_w_age_color};font-weight:bold">{_w_wait_str}</span><span style="flex:1;color:#8b949e">{_wmissing}</span><span style="width:70px">{_w_dist_str}</span><span style="width:50px">{_wconf:.0f}%</span><span style="width:55px;color:{_w_age_color}">{_w_action}</span></div>'
        _oq_html += '</div>'
        st.markdown(_oq_html, unsafe_allow_html=True)
    else:
        st.markdown("**📋 Opportunity Queue** — No candidates currently waiting")

    # ── Confidence Breakdown for Active Signals ──
    _active_sigs = [s for s in signals if states.get(s.get("symbol", ""), {}).get("state", "").startswith("ACTIVE")]
    if _active_sigs:
        st.markdown("**🎯 Confidence Breakdown — Active Signals**")
        for _as in _active_sigs:
            _asym = _as.get("symbol", "?")
            _aside = _as.get("side", "?")
            _aemoji = "🟢" if _aside in ("LONG", "BUY") else "🔴"
            _aconf = (_as.get("confidence", 0) or 0)
            _acomp = _as.get("components", {}).get("confidence", {})
            _cb_html = f'<div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 12px;margin:4px 0;font-size:.75rem">'
            _cb_html += f'<div style="font-weight:600;color:#e6edf3;margin-bottom:4px">{_aemoji} {_asym} {_aside} — {_aconf:.1f}%</div>'
            if _acomp:
                _cb_html += '<div style="display:flex;gap:8px;flex-wrap:wrap">'
                for _ck, _cv in _acomp.items():
                    if isinstance(_cv, (int, float)):
                        _cc = "#3fb950" if _cv >= 70 else ("#d29922" if _cv >= 50 else "#f85149")
                        _cb_html += f'<div style="text-align:center;min-width:50px"><div style="font-size:.85rem;font-weight:bold;color:{_cc}">{_cv:.0f}</div><div style="font-size:.5rem;color:#8b949e">{_ck}</div></div>'
                _cb_html += '</div>'
            else:
                _cb_html += '<div style="color:#8b949e">Component scores not available in bridge data</div>'
            _cb_html += '</div>'
            st.markdown(_cb_html, unsafe_allow_html=True)

    # ── Active Signal Lifetime ──
    if _active_sigs:
        st.markdown("**⏱️ Active Signal Lifetime**")
        _lt_html = '<div style="display:flex;gap:6px;flex-wrap:wrap">'
        for _as in _active_sigs:
            _asym = _as.get("symbol", "?")
            _aside = _as.get("side", "?")
            _ats = _as.get("timestamp", 0)
            _aemoji = "🟢" if _aside in ("LONG", "BUY") else "🔴"
            if _ats:
                _aage = time.time() - _ats
                if _aage < 60:
                    _aage_str = f"{int(_aage)}s"
                elif _aage < 3600:
                    _aage_str = f"{int(_aage // 60)}m"
                else:
                    _aage_str = f"{int(_aage // 3600)}h {int((_aage % 3600) // 60)}m"
            else:
                _aage_str = "—"
            # Color by age
            _ac = "#3fb950" if _ats and (time.time() - _ats) < 1800 else ("#d29922" if _ats and (time.time() - _ats) < 7200 else "#f85149")
            _lt_html += f'<div style="background:#161b22;border:1px solid #30363d;border-left:3px solid {_ac};border-radius:4px;padding:6px 10px;text-align:center;min-width:100px"><div style="font-size:.9rem;font-weight:bold;color:{_ac}">{_aage_str}</div><div style="font-size:.55rem;color:#8b949e">{_aemoji} {_asym}</div></div>'
        _lt_html += '</div>'
        st.markdown(_lt_html, unsafe_allow_html=True)

    st.divider()

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
            st.markdown("**📊 Pipeline Funnel — Session Cumulative** *(resets on engine restart)*")
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

            # ── Volume Rejection Reason Breakdown ──
            vol_reject = data.get("vol_reject_reasons", {})
            vr_total = sum(vol_reject.values()) if vol_reject else 0
            if vr_total > 0:
                st.markdown("**🔍 Volume Rejection Reasons**")
                vr_labels = {
                    "low_ratio": "Volume ratio < 0.4 (pullback threshold)",
                    "no_expansion": "Volume not expanding vs prior candle",
                    "both": "Low ratio AND no expansion",
                }
                vr_html = '<div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 12px;font-size:.75rem">'
                for reason_key, count in sorted(vol_reject.items(), key=lambda x: -x[1]):
                    if count > 0:
                        pct = (count / vr_total * 100) if vr_total > 0 else 0
                        label = vr_labels.get(reason_key, reason_key)
                        vr_html += f'<div style="display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #21262d"><span style="color:#8b949e">{label}</span><span style="color:#f85149;font-weight:600">{count:,} ({pct:.0f}%)</span></div>'
                vr_html += '</div>'
                st.markdown(vr_html, unsafe_allow_html=True)

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

            # ── Today's Funnel (from daily reconciliation) ──
            if daily_funnel and daily_funnel.get("scanned", 0) > 0:
                st.markdown("**📅 Today's Pipeline (Daily Reconciliation)**")
                _d_scanned = daily_funnel.get("scanned", 0)
                _d_stages = [
                    ("Scanned", _d_scanned),
                    ("Regime Pass", daily_funnel.get("regime_pass", 0)),
                    ("Pullback Pass", daily_funnel.get("pullback_pass", 0)),
                    ("Candle Pass", daily_funnel.get("candle_pass", 0)),
                    ("Volume Pass", daily_funnel.get("volume_pass", 0)),
                    ("Confidence Pass", daily_funnel.get("confidence_pass", 0)),
                    ("Published", daily_funnel.get("published", 0)),
                ]
                _d_rej = daily_rejections
                daily_html = '<div style="background:#161b22;border:1px solid #30363d;border-radius:6px;padding:8px 12px;font-size:.75rem">'
                daily_html += '<div style="display:flex;gap:8px;font-weight:600;color:#8b949e;border-bottom:1px solid #30363d;padding-bottom:4px;margin-bottom:4px"><span style="width:130px;text-align:right">Stage</span><span style="width:80px;text-align:right">Pass</span><span style="width:80px;text-align:right">Reject</span><span style="width:80px;text-align:right">Reject %</span><span style="width:80px;text-align:right">Conversion</span><span style="flex:1">Bar</span></div>'
                _prev_count = _d_scanned
                for label, count in _d_stages:
                    pct = (count / _d_scanned * 100) if _d_scanned > 0 else 0
                    bar_w = max(pct, 0.3)
                    color = "#3fb950" if pct > 50 else ("#d29922" if pct > 10 else ("#f85149" if pct > 1 else "#484f58"))
                    _rej_key = label.lower().replace(" ", "_").replace("_pass", "").replace("published", "signal")
                    _rej_count = _d_rej.get(_rej_key, 0)
                    _rej_pct = (_rej_count / (count + _rej_count) * 100) if (count + _rej_count) > 0 else 0
                    _conv = (count / _prev_count * 100) if _prev_count > 0 else 0
                    _conv_color = "#3fb950" if _conv > 50 else ("#d29922" if _conv > 10 else "#f85149")
                    daily_html += f'<div style="display:flex;align-items:center;gap:8px;margin:2px 0"><div style="width:130px;font-size:.7rem;color:#8b949e;text-align:right">{label}</div><div style="width:80px;font-size:.7rem;color:#3fb950;text-align:right">{count:,}</div><div style="width:80px;font-size:.7rem;color:#f85149;text-align:right">{_rej_count:,}</div><div style="width:80px;font-size:.7rem;color:#f85149;text-align:right">{_rej_pct:.1f}%</div><div style="width:80px;font-size:.7rem;color:{_conv_color};text-align:right">{_conv:.1f}%</div><div style="flex:1;background:#21262d;border-radius:3px;height:14px"><div style="width:{bar_w}%;background:{color};height:100%;border-radius:3px;min-width:2px"></div></div></div>'
                    _prev_count = count
                daily_html += '</div>'
                st.markdown(daily_html, unsafe_allow_html=True)

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
            threshold = conf_audit.get("threshold", 40.0)
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
                st.markdown(f"""<div class="m-box"><div class="m-val">{conf_audit.get('threshold', 40)}</div><div class="m-lbl">Threshold</div></div>""", unsafe_allow_html=True)

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

    # ── Open Position Confidence Drift ──
    st.markdown("**📊 Open Position Confidence Drift**")
    try:
        import sqlite3 as _sqlite3
        _db_path = str(_ai_root / "data" / "institutional_v1.db")
        _conn = _sqlite3.connect(_db_path)
        _conn.row_factory = _sqlite3.Row
        _open_pos = _conn.execute("""
            SELECT symbol, side, confidence as admission_conf, opened_at, entry_price, stop_loss
            FROM positions WHERE strategy_version='ema_v5' AND status='open'
            ORDER BY opened_at DESC
        """).fetchall()
        _conn.close()

        if _open_pos:
            drift_rows = []
            for p in _open_pos:
                sym = p["symbol"]
                admission = (p["admission_conf"] or 0) * 100  # convert 0-1 to 0-100
                # Note: current_conf requires live scanner state which isn't available here
                # Show admission confidence only (drift requires scanner integration)
                age_min = (time.time() - (p["opened_at"] or 0)) / 60

                drift_rows.append({
                    "Symbol": sym,
                    "Side": p["side"],
                    "Admission Conf": f"{admission:.1f}%",
                    "Entry": f"{p['entry_price']:.6f}",
                    "SL": f"{p['stop_loss']:.6f}",
                    "Age": f"{age_min:.0f}m",
                })

            st.dataframe(drift_rows, use_container_width=True, hide_index=True)
        else:
            st.info("No open EMA V5 positions")
    except Exception as e:
        st.warning(f"Could not load open positions: {e}")

    # ── Footer ──
    st.caption(f"🏛️ EMA V5 Scanner — DeltaTerminal v2.5 — {_dt(time.time())} ({st.session_state.get('tz_name', 'UTC')})")


if __name__ == "__main__":
    main()
