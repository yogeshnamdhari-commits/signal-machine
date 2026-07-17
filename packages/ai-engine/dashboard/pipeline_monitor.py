"""
Production Pipeline Monitor — Complete signal lifecycle visibility.

Tasks:
  1. Extended Funnel (Session, Checklist, Generated, Emitted stages)
  2. Live Signal Trace (per-symbol pipeline drill-down)
  3. Session Diagnostics
  4. Checklist Diagnostics
  5. Generated Signals
  6. Emitted Signals
  7. First Signal ETA
  8. Top 20 Closest to Emission
  9. Pipeline Alerts
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st


# ══════════════════════════════════════════════════════════════
# CSS HELPERS
# ══════════════════════════════════════════════════════════════

_CSS_INJECTED = False


def _inject_css():
    global _CSS_INJECTED
    if _CSS_INJECTED:
        return
    _CSS_INJECTED = True
    st.markdown(
        "<style>"
        ".pipe-metric{background:#0f172a;border:1px solid #1e293b;border-radius:8px;"
        "padding:10px 14px;text-align:center;min-height:60px;}"
        ".pipe-metric .pm-val{font-size:1.4rem;font-weight:800;line-height:1.2;}"
        ".pipe-metric .pm-lbl{font-size:0.65rem;color:#64748b;text-transform:uppercase;"
        "letter-spacing:0.05em;margin-top:2px;}"
        ".pipe-metric .pm-delta{font-size:0.7rem;margin-top:2px;}"
        ".gate-pass{color:#22c55e;font-weight:700;}"
        ".gate-fail{color:#ef4444;font-weight:700;}"
        ".gate-pending{color:#f59e0b;font-weight:700;}"
        ".gate-na{color:#475569;}"
        ".trace-card{background:#0c1018;border:1px solid #1e293b;border-radius:10px;"
        "padding:14px 18px;margin:6px 0;}"
        ".trace-header{display:flex;justify-content:space-between;align-items:center;"
        "margin-bottom:8px;}"
        ".eta-badge{display:inline-block;padding:6px 16px;border-radius:20px;"
        "font-weight:800;font-size:1.1rem;text-align:center;}"
        "</style>",
        unsafe_allow_html=True,
    )


def _fmt(n) -> str:
    if isinstance(n, float):
        return f"{n:,.1f}"
    return f"{n:,}"


def _pct(part, whole) -> str:
    if not whole:
        return "0%"
    return f"{part / whole * 100:.1f}%"


def _gate_html(label: str, passed: Optional[bool], detail: str = "") -> str:
    if passed is True:
        icon, cls = "✅", "gate-pass"
    elif passed is False:
        icon, cls = "❌", "gate-fail"
    else:
        icon, cls = "—", "gate-na"
    det = f'<span style="color:#64748b;font-size:0.7rem;"> {detail}</span>' if detail else ""
    return (
        f'<div style="display:flex;align-items:center;gap:8px;padding:3px 0;">'
        f'<span style="width:130px;text-align:right;font-size:0.78rem;color:#94a3b8;font-weight:500;">{label}</span>'
        f'<span class="{cls}" style="font-size:0.85rem;">{icon}</span>'
        f'<span style="font-size:0.78rem;color:#e2e8f0;">{detail}</span>'
        f'</div>'
    )


def _bar_html(label: str, value: float, max_val: float, color: str, count: int, total: int) -> str:
    width = max(value / max(max_val, 1) * 100, 2) if max_val > 0 else 2
    return (
        f'<div style="display:flex;align-items:center;gap:8px;margin:2px 0;">'
        f'<div style="width:140px;text-align:right;font-size:0.75rem;color:#94a3b8;font-weight:500;">{label}</div>'
        f'<div style="flex:1;background:#1e293b;border-radius:4px;height:22px;position:relative;">'
        f'<div style="width:{width:.1f}%;background:{color};border-radius:4px;height:100%;opacity:0.85;"></div>'
        f'<span style="position:absolute;right:8px;top:2px;font-size:0.72rem;color:#e2e8f0;font-weight:700;">{_fmt(count)}</span>'
        f'</div>'
        f'<div style="width:55px;text-align:right;font-size:0.7rem;color:#64748b;">{_pct(count, total)}</div>'
        f'</div>'
    )


# ══════════════════════════════════════════════════════════════
# TASK 7 — FIRST SIGNAL ETA (prominent widget at top)
# ══════════════════════════════════════════════════════════════

def _gate_html_3state(label: str, status: str, detail: str = "") -> str:
    """Render a 3-state gate: pass / fail / skip (unavailable)."""
    if status == "pass":
        icon, cls = "✅", "gate-pass"
    elif status == "fail":
        icon, cls = "❌", "gate-fail"
    elif status == "skip":
        icon, cls = "⏭️", "gate-pending"
    else:
        icon, cls = "—", "gate-na"
    det = f'<span style="color:#64748b;font-size:0.7rem;"> {detail}</span>' if detail else ""
    return (
        f'<div style="display:flex;align-items:center;gap:8px;padding:3px 0;">'
        f'<span style="width:130px;text-align:right;font-size:0.78rem;color:#94a3b8;font-weight:500;">{label}</span>'
        f'<span class="{cls}" style="font-size:0.85rem;">{icon}</span>'
        f'<span style="font-size:0.78rem;color:#e2e8f0;">{detail}</span>'
        f'</div>'
    )


# ══════════════════════════════════════════════════════════════
# DATA HEALTH PANEL
# ══════════════════════════════════════════════════════════════

def render_data_health_panel(funnel: Dict) -> None:
    """Display live data source health status for all checklist data feeds."""
    _inject_css()

    traces = funnel.get("pipeline_traces", [])
    # Aggregate data_health from all checklist traces
    health_counts = {
        "oi": {"LIVE": 0, "SIMULATED": 0, "UNAVAILABLE": 0},
        "cvd": {"LIVE": 0, "SIMULATED": 0, "UNAVAILABLE": 0},
        "delta": {"LIVE": 0, "SIMULATED": 0, "UNAVAILABLE": 0},
        "volume": {"LIVE": 0, "SIMULATED": 0, "UNAVAILABLE": 0},
        "funding": {"LIVE": 0, "SIMULATED": 0, "UNAVAILABLE": 0},
    }

    for t in traces:
        cl = t.get("checklist", {})
        dh = cl.get("data_health", {})
        for source in health_counts:
            status = dh.get(source, "UNAVAILABLE")
            if status in health_counts[source]:
                health_counts[source][status] += 1

    # Determine per-source overall status (most recent trace wins)
    latest_health = {}
    for t in reversed(traces):
        cl = t.get("checklist", {})
        dh = cl.get("data_health", {})
        for source in health_counts:
            if source in dh and source not in latest_health:
                latest_health[source] = dh[source]

    # Build HTML
    source_labels = {
        "oi": ("📊 OI", "Open Interest"),
        "cvd": ("📈 CVD", "Cumulative Volume Delta"),
        "delta": ("📉 Delta", "Orderflow Delta"),
        "volume": ("🔊 Volume", "Volume Expansion"),
        "funding": ("💰 Funding", "Funding Rate"),
    }

    cells_html = ""
    for source, (icon_label, full_name) in source_labels.items():
        status = latest_health.get(source, "UNAVAILABLE")
        if status == "LIVE":
            badge_bg, badge_color, badge_border = "#22c55e22", "#22c55e", "#22c55e44"
            status_text = "🟢 LIVE"
        elif status == "SIMULATED":
            badge_bg, badge_color, badge_border = "#f59e0b22", "#f59e0b", "#f59e0b44"
            status_text = "🟡 SIMULATED"
        else:
            badge_bg, badge_color, badge_border = "#ef444422", "#ef4444", "#ef444444"
            status_text = "🔴 UNAVAILABLE"

        cells_html += (
            f'<div style="background:{badge_bg};border:1px solid {badge_border};border-radius:8px;'
            f'padding:10px 14px;text-align:center;min-width:120px;">'
            f'<div style="font-size:0.65rem;color:#64748b;text-transform:uppercase;letter-spacing:0.05em;">{icon_label}</div>'
            f'<div style="font-size:0.95rem;font-weight:800;color:{badge_color};margin-top:4px;">{status_text}</div>'
            f'<div style="font-size:0.65rem;color:#475569;margin-top:2px;">{full_name}</div>'
            f'</div>'
        )

    st.markdown(
        f'<div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:14px 18px;margin:6px 0;">'
        f'<div style="font-size:0.85rem;color:#e2e8f0;font-weight:700;margin-bottom:10px;">'
        f'🏥 DATA HEALTH — Source Availability</div>'
        f'<div style="display:flex;gap:10px;flex-wrap:wrap;">{cells_html}</div>'
        f'<div style="margin-top:8px;font-size:0.72rem;color:#64748b;">'
        f'ℹ️ UNAVAILABLE data is SKIPPED (not counted as failure). '
        f'SIMULATED data is evaluated but may be unreliable.</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════
# TASK 7 — FIRST SIGNAL ETA (prominent widget at top)
# ══════════════════════════════════════════════════════════════

def render_first_signal_eta(funnel: Dict, signals: List[Dict], session_diag: Dict) -> None:
    """Display the First Signal ETA widget."""
    _inject_css()

    emitted_count = funnel.get("signals_emitted", 0) + funnel.get("inst_signal_emitted", 0)
    traces = funnel.get("pipeline_traces", [])
    session_status = session_diag.get("session_status", "UNKNOWN")
    has_candidate = any(t.get("failed_gate") is None or t.get("emitted") for t in traces)
    high_checklist = any(
        t.get("checklist", {}).get("score", 0) >= 8
        for t in traces
    )

    # Determine ETA state
    if emitted_count > 0 or (signals and len(signals) > 0):
        badge_text = "🔴 LIVE TRADE ACTIVE"
        badge_bg = "linear-gradient(135deg,#dc2626,#991b1b)"
        badge_border = "#ef4444"
    elif has_candidate and session_status == "BLOCKING":
        badge_text = "🟡 CANDIDATE READY — Waiting for London Session"
        badge_bg = "linear-gradient(135deg,#f59e0b,#b45309)"
        badge_border = "#f59e0b"
    elif high_checklist:
        badge_text = "🟡 SIGNAL LIKELY NEXT SCAN"
        badge_bg = "linear-gradient(135deg,#f59e0b,#92400e)"
        badge_border = "#f59e0b"
    elif session_status == "BLOCKING":
        next_sess = session_diag.get("next_session", "london")
        remaining = session_diag.get("time_remaining", "?")
        badge_text = f"⏳ Waiting for {next_sess.upper()} Session ({remaining})"
        badge_bg = "linear-gradient(135deg,#1e3a5f,#0f172a)"
        badge_border = "#3b82f6"
    else:
        badge_text = "🔍 SCANNING — No candidate yet"
        badge_bg = "linear-gradient(135deg,#1e293b,#0f172a)"
        badge_border = "#475569"

    st.markdown(
        f'<div style="background:{badge_bg};border:1px solid {badge_border}44;'
        f'border-radius:12px;padding:16px 24px;text-align:center;margin:4px 0 12px 0;">'
        f'<div style="font-size:0.65rem;color:#8892a4;text-transform:uppercase;letter-spacing:0.1em;margin-bottom:4px;">'
        f'📡 FIRST SIGNAL ETA</div>'
        f'<div class="eta-badge" style="color:#fff;">{badge_text}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════
# TASK 1 — EXTENDED FUNNEL VISIBILITY
# ══════════════════════════════════════════════════════════════

def render_extended_funnel(funnel: Dict) -> None:
    """Display the full pipeline funnel with all 11 stages."""
    _inject_css()

    scanned = funnel.get("symbols_processed", 0)
    scorer = scanned - funnel.get("scorer_rejected", 0)
    phase1 = scorer - funnel.get("phase1_rejected", 0)
    regime = phase1 - funnel.get("regime_blocked", 0)
    session = regime - funnel.get("session_blocked", 0)
    # Sweep, OI, CVD are tracked differently in the old pipeline
    sweep = session - funnel.get("sweep_blocked", 0)
    oi = sweep - funnel.get("oi_blocked", 0)
    cvd = oi - funnel.get("cvd_blocked", 0)
    checklist = cvd - funnel.get("checklist_blocked", 0)
    generated = funnel.get("checklist_passed", 0)
    emitted = funnel.get("signals_emitted", 0) + funnel.get("inst_signal_emitted", 0)

    max_val = max(scanned, 1)
    cycle_dur = funnel.get("cycle_duration_sec", 0)

    st.markdown(
        '<div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:14px 18px;">'
        '<div style="font-size:0.85rem;color:#e2e8f0;font-weight:700;margin-bottom:10px;">'
        '📊 SIGNAL FUNNEL — COMPLETE PIPELINE</div></div>',
        unsafe_allow_html=True,
    )

    st.markdown(_bar_html("📥 Scanned", scanned, max_val, "#3b82f6", scanned, scanned), unsafe_allow_html=True)
    st.markdown(_bar_html("🧠 AI Scorer", scorer, max_val, "#f59e0b", scorer, scanned), unsafe_allow_html=True)
    st.markdown(_bar_html("🎯 Phase 1", phase1, max_val, "#8b5cf6", phase1, scanned), unsafe_allow_html=True)
    st.markdown(_bar_html("🔄 Regime", regime, max_val, "#06b6d4", regime, scanned), unsafe_allow_html=True)
    st.markdown(_bar_html("💧 Sweep", sweep, max_val, "#10b981", sweep, scanned), unsafe_allow_html=True)
    st.markdown(_bar_html("📈 OI", oi, max_val, "#f97316", oi, scanned), unsafe_allow_html=True)
    st.markdown(_bar_html("📊 CVD", cvd, max_val, "#ec4899", cvd, scanned), unsafe_allow_html=True)
    st.markdown(_bar_html("🔒 Session", session, max_val, "#eab308", session, scanned), unsafe_allow_html=True)
    st.markdown(_bar_html("📋 Checklist", checklist, max_val, "#14b8a6", checklist, scanned), unsafe_allow_html=True)
    st.markdown(_bar_html("⚡ Generated", generated, max_val, "#8b5cf6", generated, scanned), unsafe_allow_html=True)
    st.markdown(_bar_html("🚀 Emitted", emitted, max_val, "#00ff88", emitted, scanned), unsafe_allow_html=True)

    # Cycle stats row
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Cycle", f"{cycle_dur:.1f}s")
    with c2:
        st.metric("Session Block", f"{funnel.get('session_blocked', 0)}")
    with c3:
        st.metric("Checklist Block", f"{funnel.get('checklist_blocked', 0)}")
    with c4:
        st.metric("Checklist Pass", f"{funnel.get('checklist_passed', 0)}")
    with c5:
        st.metric("Pass Rate", _pct(emitted, scanned))


# ══════════════════════════════════════════════════════════════
# TASK 2 — LIVE SIGNAL TRACE PANEL
# ══════════════════════════════════════════════════════════════

def render_signal_trace(funnel: Dict) -> None:
    """Display per-symbol pipeline trace for every symbol surviving REGIME."""
    _inject_css()

    traces = funnel.get("pipeline_traces", [])
    if not traces:
        st.info("📭 No symbols survived REGIME gate this cycle.")
        return

    for t in traces:
        sym = t.get("symbol", "?")
        side = t.get("side", "?")
        conf = t.get("confidence", 0)
        inst_score = t.get("institutional_score", 0)
        regime = t.get("regime", "?")
        tf_ovr = t.get("tf_override", False)
        failed_gate = t.get("failed_gate")
        emitted = t.get("emitted", False)
        generated = t.get("generated", False)

        session_info = t.get("session", {})
        session_passed = session_info.get("passed")
        session_name = session_info.get("session", "?")

        checklist_info = t.get("checklist", {})
        checklist_passed = checklist_info.get("passed", False)
        checklist_score = checklist_info.get("score", 0)

        side_color = "#22c55e" if side == "LONG" else "#ef4444"
        status_color = "#00ff88" if emitted else ("#f59e0b" if generated else "#ef4444")
        status_text = "EMITTED" if emitted else ("GENERATED" if generated else f"BLOCKED: {failed_gate or '?'}")

        # Build gate trace HTML
        gates_html = ""
        gates_html += _gate_html("Scorer", True, f"conf={conf:.1f}")
        gates_html += _gate_html("Phase 1", True)
        regime_detail = f"{regime}" + (" (TF_OVERRIDE)" if tf_ovr else "")
        gates_html += _gate_html("Regime", True, regime_detail)
        gates_html += _gate_html("Session", session_passed, session_name)
        # Show checklist with 3-state score
        if session_passed:
            cl_score_str = checklist_info.get("score_str", f"{checklist_score:.0f}/13")
            cl_skipped = checklist_info.get("skipped", 0)
            cl_detail = f"{cl_score_str}" + (f" ({cl_skipped} skipped)" if cl_skipped > 0 else "")
            gates_html += _gate_html("Checklist", checklist_passed, cl_detail)
        else:
            gates_html += _gate_html("Checklist", None, "—")
        gates_html += _gate_html("Generated", generated, "✅" if generated else "—")
        gates_html += _gate_html("Emitted", emitted, "🚀" if emitted else "—")

        st.markdown(
            f'<div class="trace-card">'
            f'<div class="trace-header">'
            f'<div>'
            f'<span style="font-size:1.1rem;font-weight:800;color:#e2e8f0;">{sym}</span> '
            f'<span style="font-size:0.9rem;font-weight:700;color:{side_color};margin-left:8px;">{side}</span>'
            f'</div>'
            f'<div style="text-align:right;">'
            f'<span style="font-size:0.9rem;font-weight:800;color:{status_color};">{status_text}</span>'
            f'</div>'
            f'</div>'
            f'<div style="display:flex;gap:16px;margin-bottom:6px;">'
            f'<span style="font-size:0.75rem;color:#94a3b8;">Confidence: <b style="color:#e2e8f0;">{conf:.1f}</b></span>'
            f'<span style="font-size:0.75rem;color:#94a3b8;">Inst Score: <b style="color:#e2e8f0;">{inst_score:.1f}</b></span>'
            f'<span style="font-size:0.75rem;color:#94a3b8;">Regime: <b style="color:#e2e8f0;">{regime}</b></span>'
            f'</div>'
            f'{gates_html}'
            f'</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════
# TASK 3 — SESSION DIAGNOSTICS
# ══════════════════════════════════════════════════════════════

def render_session_diagnostics(session_diag: Dict) -> None:
    """Display the Session Quality Monitor."""
    _inject_css()

    if not session_diag:
        st.info("⏳ Waiting for session data...")
        return

    current = session_diag.get("current_session", "unknown")
    pf = session_diag.get("session_pf", 0)
    wr = session_diag.get("session_wr", 0)
    trades = session_diag.get("session_trades", 0)
    expectancy = session_diag.get("session_expectancy", "Unknown")
    status = session_diag.get("session_status", "UNKNOWN")
    next_sess = session_diag.get("next_session", "?")
    remaining = session_diag.get("time_remaining", "?")

    status_color = "#22c55e" if status == "ALLOWED" else "#ef4444"
    status_bg = "#22c55e15" if status == "ALLOWED" else "#ef444415"

    st.markdown(
        f'<div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:14px 18px;">'
        f'<div style="font-size:0.85rem;color:#e2e8f0;font-weight:700;margin-bottom:10px;">'
        f'🔒 SESSION QUALITY MONITOR</div>'
        f'<div style="display:flex;gap:20px;flex-wrap:wrap;">'
        f'<div style="text-align:center;">'
        f'<div style="font-size:0.65rem;color:#64748b;">CURRENT SESSION</div>'
        f'<div style="font-size:1.1rem;font-weight:800;color:#e2e8f0;">{current.upper()}</div></div>'
        f'<div style="text-align:center;">'
        f'<div style="font-size:0.65rem;color:#64748b;">SESSION PF</div>'
        f'<div style="font-size:1.1rem;font-weight:800;color:{"#22c55e" if pf >= 1.0 else "#ef4444"};">{pf:.2f}</div></div>'
        f'<div style="text-align:center;">'
        f'<div style="font-size:0.65rem;color:#64748b;">WIN RATE</div>'
        f'<div style="font-size:1.1rem;font-weight:800;color:#e2e8f0;">{wr:.0f}%</div></div>'
        f'<div style="text-align:center;">'
        f'<div style="font-size:0.65rem;color:#64748b;">TRADES</div>'
        f'<div style="font-size:1.1rem;font-weight:800;color:#e2e8f0;">{_fmt(trades)}</div></div>'
        f'<div style="text-align:center;">'
        f'<div style="font-size:0.65rem;color:#64748b;">EXPECTANCY</div>'
        f'<div style="font-size:1.1rem;font-weight:800;color:{"#22c55e" if "Positive" in expectancy or "positive" in expectancy else "#ef4444"};">{expectancy}</div></div>'
        f'<div style="text-align:center;">'
        f'<div style="font-size:0.65rem;color:#64748b;">STATUS</div>'
        f'<div style="font-size:1.1rem;font-weight:800;color:{status_color};">{status}</div></div>'
        f'</div>'
        f'<div style="margin-top:10px;padding-top:10px;border-top:1px solid #1e293b;">'
        f'<div style="display:flex;gap:20px;">'
        f'<div style="text-align:center;">'
        f'<div style="font-size:0.65rem;color:#64748b;">NEXT ALLOWED SESSION</div>'
        f'<div style="font-size:0.95rem;font-weight:700;color:#3b82f6;">{next_sess.upper()}</div></div>'
        f'<div style="text-align:center;">'
        f'<div style="font-size:0.65rem;color:#64748b;">TIME REMAINING</div>'
        f'<div style="font-size:0.95rem;font-weight:700;color:#f59e0b;">{remaining}</div></div>'
        f'</div></div></div>',
        unsafe_allow_html=True,
    )


# ══════════════════════════════════════════════════════════════
# TASK 4 — CHECKLIST DIAGNOSTICS
# ══════════════════════════════════════════════════════════════

def render_checklist_diagnostics(funnel: Dict) -> None:
    """Display checklist component results for each candidate."""
    _inject_css()

    traces = funnel.get("pipeline_traces", [])
    # Only show for symbols that reached the checklist gate
    checklist_traces = [
        t for t in traces
        if t.get("session", {}).get("passed") is True
    ]

    if not checklist_traces:
        st.info("📭 No symbols reached the CHECKLIST gate this cycle.")
        return

    CHECK_LABELS = {
        "regime": "Regime Filter",
        "sweep": "Liquidity Sweep",
        "mss": "MSS",
        "displacement": "Displacement",
        "delta": "Delta",
        "cvd": "CVD",
        "oi_expansion": "OI Expansion",
        "volume_expansion": "Volume",
        "fvg_retest": "FVG Retest",
        "risk_reward": "R:R ≥ 3.0",
    }

    for t in checklist_traces:
        sym = t.get("symbol", "?")
        cl = t.get("checklist", {})
        score = cl.get("score", 0)
        passed = cl.get("passed", False)
        checks = cl.get("checks", {})
        failures = cl.get("failures", [])
        data_status = cl.get("data_status", {})
        score_str = cl.get("score_str", f"{score:.0f}/13")
        required = cl.get("required_checks", 0)
        passes_count = cl.get("passes", 0)
        skipped = cl.get("skipped", 0)

        score_color = "#22c55e" if passed else "#ef4444"

        # 3-state check rendering
        checks_html = ""
        for key, label in CHECK_LABELS.items():
            val = checks.get(key)
            ds = data_status.get(key, "pass" if val else "fail")
            checks_html += _gate_html_3state(label, ds, ds.upper())

        # Status header
        status_text = "✅ PASSED" if passed else "❌ FAILED"
        skip_note = f" ({skipped} data sources unavailable)" if skipped > 0 else ""

        st.markdown(
            f'<div class="trace-card">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">'
            f'<span style="font-size:1rem;font-weight:800;color:#e2e8f0;">{sym} — Checklist</span>'
            f'<span style="font-size:1.1rem;font-weight:800;color:{score_color};">{score_str} '
            f'{status_text}</span>'
            f'</div>'
            f'<div style="font-size:0.72rem;color:#64748b;margin-bottom:4px;">'
            f'Required: {required} | Passed: {passes_count} | Skipped: {skipped}{skip_note}</div>'
            f'{checks_html}'
            f'</div>',
            unsafe_allow_html=True,
        )

        if failures:
            fail_text = " | ".join(failures[:5])
            st.markdown(
                f'<div style="font-size:0.75rem;color:#ef4444;padding:4px 0 8px 140px;">'
                f'Failures: {fail_text}</div>',
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════
# TASK 5 — GENERATED SIGNAL PANEL
# ══════════════════════════════════════════════════════════════

def render_generated_signals(funnel: Dict, signals: List[Dict]) -> None:
    """Display signals that passed all gates but haven't been emitted yet."""
    _inject_css()

    traces = funnel.get("pipeline_traces", [])
    generated = [t for t in traces if t.get("generated") and not t.get("emitted")]

    # Also check signals list for recently generated
    active_signals = [s for s in signals if s.get("status") == "active"]

    if not generated and not active_signals:
        st.info("📭 No generated signals this cycle.")
        return

    rows = []
    for t in generated:
        rows.append({
            "Time": time.strftime("%H:%M:%S", time.localtime(t.get("timestamp", 0))),
            "Symbol": t.get("symbol", "?"),
            "Side": t.get("side", "?"),
            "Confidence": f"{t.get('confidence', 0):.1f}",
            "Inst Score": f"{t.get('institutional_score', 0):.1f}",
            "Regime": t.get("regime", "?"),
            "Checklist": f"{t.get('checklist', {}).get('score_str', '?')}",
            "Status": "⚡ Generated",
        })

    for s in active_signals:
        rows.append({
            "Time": time.strftime("%H:%M:%S", time.localtime(s.get("created_at", 0))),
            "Symbol": s.get("symbol", "?"),
            "Side": s.get("side") or s.get("type", "?"),
            "Confidence": f"{s.get('confidence_100', s.get('confidence', 0) * 100):.1f}",
            "Inst Score": f"{s.get('institutional_score', 0):.1f}",
            "Regime": s.get("regime", "?"),
            "Checklist": f"{s.get('checklist', {}).get('score_str', '?')}",
            "Status": "⚡ Active",
        })

    if rows:
        st.markdown(
            f'<div style="font-size:0.85rem;color:#e2e8f0;font-weight:700;margin-bottom:6px;">'
            f'⚡ GENERATED SIGNALS ({len(rows)})</div>',
            unsafe_allow_html=True,
        )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


# ══════════════════════════════════════════════════════════════
# TASK 6 — EMITTED SIGNAL PANEL
# ══════════════════════════════════════════════════════════════

def render_emitted_signals(signals: List[Dict], positions: List[Dict]) -> None:
    """Display live emitted signals with full details."""
    _inject_css()

    if not signals:
        st.info("📭 No live signals.")
        return

    rows = []
    for s in signals:
        side = s.get("side") or s.get("type", "?")
        side_color = "🟢" if side == "LONG" else "🔴"
        entry = s.get("entry_price", 0)
        sl = s.get("stop_loss", 0)
        tp1 = s.get("take_profit_1", s.get("take_profit", 0))
        tp2 = s.get("take_profit_2", 0)
        tp3 = s.get("take_profit_3", 0)
        rr = s.get("risk_reward", 0)

        # Check if there's an open position for this signal
        has_position = any(p.get("symbol") == s.get("symbol") for p in positions)
        status = "🟢 LIVE" if has_position else "⚡ EMITTED"

        rows.append({
            "Time": time.strftime("%H:%M:%S", time.localtime(s.get("created_at", 0))),
            "Symbol": s.get("symbol", "?"),
            "Side": f"{side_color} {side}",
            "Entry": f"${entry:.6f}" if entry < 1 else f"${entry:,.2f}",
            "SL": f"${sl:.6f}" if sl < 1 else f"${sl:,.2f}",
            "TP1": f"${tp1:.6f}" if tp1 < 1 else f"${tp1:,.2f}",
            "TP2": f"${tp2:.6f}" if tp2 < 1 else f"${tp2:,.2f}",
            "TP3": f"${tp3:.6f}" if tp3 < 1 else f"${tp3:,.2f}",
            "R:R": f"{rr:.1f}",
            "Status": status,
        })

    if rows:
        st.markdown(
            f'<div style="font-size:0.85rem;color:#00ff88;font-weight:700;margin-bottom:6px;">'
            f'🚀 LIVE SIGNALS ({len(rows)})</div>',
            unsafe_allow_html=True,
        )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


# ══════════════════════════════════════════════════════════════
# TASK 8 — TOP 20 CLOSEST TO EMISSION
# ══════════════════════════════════════════════════════════════

def render_top_20_candidates(funnel: Dict, signals: List[Dict]) -> None:
    """Display top 20 symbols ranked by readiness to emit."""
    _inject_css()

    traces = funnel.get("pipeline_traces", [])
    top_scores = funnel.get("top_scores", [])
    rejection_reasons = funnel.get("rejection_reasons", [])

    # Build readiness scores from traces
    candidates = []
    for t in traces:
        sym = t.get("symbol", "?")
        failed = t.get("failed_gate")
        conf = t.get("confidence", 0)
        inst = t.get("institutional_score", 0)

        # Readiness: higher = closer to emission
        # Emitted = 100, Generated = 90, Checklist pass = 80, Session pass = 70, etc.
        gate_scores = {
            None: 100,  # emitted
            "emitted": 100,
            "directional_cap": 85,
            "checklist": 70,
            "session": 60,
            "rr_filter": 55,
            "signal_filter": 50,
            "oi_validation": 45,
            "cvd_phase1": 40,
            "sweep": 35,
            "quiet_market": 30,
            "oi_extreme": 25,
            "delta_extreme": 20,
            "blacklist": 15,
        }
        readiness = gate_scores.get(failed, 50)
        # Boost by confidence
        readiness += conf * 0.1

        candidates.append({
            "Symbol": sym,
            "Side": t.get("side", "?"),
            "Confidence": f"{conf:.1f}",
            "Inst Score": f"{inst:.1f}",
            "Regime": t.get("regime", "?"),
            "Current Gate": (failed or "EMITTED").upper(),
            "Readiness": round(readiness, 1),
            "Distance to Emit": f"{100 - readiness:.0f}%",
        })

    # Also add symbols from top_scores that aren't in traces
    traced_syms = {c["Symbol"] for c in candidates}
    for ts in top_scores[:20]:
        sym = ts.get("symbol", "?")
        if sym not in traced_syms:
            conf = ts.get("confidence", 0)
            inst = ts.get("institutional_score", 0)
            candidates.append({
                "Symbol": sym,
                "Side": ts.get("side", "?"),
                "Confidence": f"{conf:.1f}",
                "Inst Score": f"{inst:.1f}",
                "Regime": "—",
                "Current Gate": "SCORER/P1",
                "Readiness": round(conf * 0.5, 1),
                "Distance to Emit": f"{100 - conf * 0.5:.0f}%",
            })

    # Add recent rejections as candidates too
    seen_syms = {c["Symbol"] for c in candidates}
    for rr in rejection_reasons[-30:]:
        sym = rr.get("symbol", "?")
        if sym not in seen_syms:
            reason = rr.get("reason", "")
            candidates.append({
                "Symbol": sym,
                "Side": "—",
                "Confidence": f"{rr.get('confidence', 0):.1f}" if rr.get("confidence") else "—",
                "Inst Score": "—",
                "Regime": "—",
                "Current Gate": reason.split(":")[0] if reason else "?",
                "Readiness": 10,
                "Distance to Emit": "90%",
            })
            seen_syms.add(sym)

    # Sort by readiness descending
    candidates.sort(key=lambda x: x["Readiness"], reverse=True)
    candidates = candidates[:20]

    if not candidates:
        st.info("📭 No candidates this cycle.")
        return

    st.markdown(
        f'<div style="font-size:0.85rem;color:#e2e8f0;font-weight:700;margin-bottom:6px;">'
        f'🏆 TOP {len(candidates)} CLOSEST TO EMISSION</div>',
        unsafe_allow_html=True,
    )

    # Add rank
    for i, c in enumerate(candidates):
        c["Rank"] = i + 1

    try:
        df = pd.DataFrame(candidates)
        cols = ["Rank", "Symbol", "Side", "Confidence", "Inst Score",
                "Regime", "Current Gate", "Distance to Emit"]
        df = df[[c for c in cols if c in df.columns]]
        st.dataframe(df, width="stretch", hide_index=True, height=min(40 + len(candidates) * 35, 600))
    except Exception as e:
        st.warning(f"⚠️ Candidates display error: {e}")
        for c in candidates:
            st.markdown(
                f'<div style="padding:3px 8px;font-size:0.8rem;">'
                f'<b>{c.get("Rank","?")}. {c.get("Symbol","?")}</b> — '
                f'{c.get("Side","?")} | Conf: {c.get("Confidence","?")} | '
                f'Gate: {c.get("Current Gate","?")} | '
                f'Distance: {c.get("Distance to Emit","?")}</div>',
                unsafe_allow_html=True,
            )


# ══════════════════════════════════════════════════════════════
# TASK 9 — PIPELINE ALERTS
# ══════════════════════════════════════════════════════════════

def render_pipeline_alerts(funnel: Dict, alerts: List[Dict]) -> None:
    """Display pipeline event alerts."""
    _inject_css()

    pipeline_events = []

    # Check for recent pipeline events from rejection reasons
    reasons = funnel.get("rejection_reasons", [])
    for r in reasons[-20:]:
        reason = r.get("reason", "")
        ts = r.get("time", 0)
        sym = r.get("symbol", "?")

        if "SESSION:" in reason:
            pipeline_events.append({"time": ts, "event": "🔒 SESSION BLOCK", "symbol": sym, "detail": reason, "color": "#ef4444"})
        elif "CHECKLIST:" in reason:
            pipeline_events.append({"time": ts, "event": "📋 CHECKLIST FAIL", "symbol": sym, "detail": reason, "color": "#f59e0b"})
        elif "HARD_REGIME:" in reason:
            pipeline_events.append({"time": ts, "event": "🔄 REGIME BLOCK", "symbol": sym, "detail": reason, "color": "#f97316"})
        elif "TF_BREAKOUT" in reason.upper():
            pipeline_events.append({"time": ts, "event": "✅ TF OVERRIDE", "symbol": sym, "detail": reason, "color": "#22c55e"})

    # Check for emitted signals
    traces = funnel.get("pipeline_traces", [])
    for t in traces:
        if t.get("emitted"):
            pipeline_events.append({
                "time": t.get("timestamp", 0),
                "event": "🚨 SIGNAL EMITTED",
                "symbol": t.get("symbol", "?"),
                "detail": f"{t.get('side', '?')} conf={t.get('confidence', 0):.1f}",
                "color": "#00ff88",
            })
        elif t.get("generated"):
            pipeline_events.append({
                "time": t.get("timestamp", 0),
                "event": "⚡ SIGNAL GENERATED",
                "symbol": t.get("symbol", "?"),
                "detail": f"{t.get('side', '?')} conf={t.get('confidence', 0):.1f}",
                "color": "#8b5cf6",
            })

    # Add dashboard alerts
    for a in (alerts or [])[-10:]:
        pipeline_events.append({
            "time": a.get("timestamp", a.get("time", 0)),
            "event": a.get("type", a.get("event", "ℹ️ ALERT")),
            "symbol": a.get("symbol", "—"),
            "detail": a.get("message", a.get("detail", "")),
            "color": "#3b82f6",
        })

    if not pipeline_events:
        st.info("📭 No pipeline events this cycle.")
        return

    # Sort by time, most recent first
    pipeline_events.sort(key=lambda x: x.get("time", 0), reverse=True)
    pipeline_events = pipeline_events[:20]

    st.markdown(
        f'<div style="font-size:0.85rem;color:#e2e8f0;font-weight:700;margin-bottom:6px;">'
        f'🔔 PIPELINE ALERTS ({len(pipeline_events)})</div>',
        unsafe_allow_html=True,
    )

    for ev in pipeline_events:
        ts = ev.get("time", 0)
        ts_str = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "?"
        color = ev.get("color", "#3b82f6")
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:10px;padding:4px 8px;'
            f'border-left:3px solid {color};margin:3px 0;background:#0f172a;border-radius:0 6px 6px 0;">'
            f'<span style="font-size:0.7rem;color:#64748b;width:65px;">{ts_str}</span>'
            f'<span style="font-size:0.8rem;font-weight:700;color:{color};width:150px;">{ev.get("event", "?")}</span>'
            f'<span style="font-size:0.8rem;font-weight:600;color:#e2e8f0;width:100px;">{ev.get("symbol", "?")}</span>'
            f'<span style="font-size:0.75rem;color:#94a3b8;flex:1;">{str(ev.get("detail", ""))[:100]}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )


# ══════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ══════════════════════════════════════════════════════════════

def render_pipeline_monitor(
    funnel: Dict,
    signals: List[Dict],
    positions: List[Dict],
    alerts: List[Dict],
) -> None:
    """Render the complete Production Pipeline Monitor."""
    _inject_css()

    if not funnel:
        st.info("⏳ Waiting for engine data...")
        return

    session_diag = funnel.get("session_diagnostics", {})

    # Task 7: First Signal ETA (prominent at top)
    render_first_signal_eta(funnel, signals, session_diag)

    # Task 0: Data Health Panel (source availability)
    render_data_health_panel(funnel)

    # Task 1: Extended Funnel
    render_extended_funnel(funnel)

    st.markdown("---")

    # Task 3 + Task 4: Session + Checklist side by side
    col_s, col_c = st.columns(2)
    with col_s:
        render_session_diagnostics(session_diag)
    with col_c:
        render_checklist_diagnostics(funnel)

    st.markdown("---")

    # Task 2: Live Signal Trace
    st.markdown(
        '<div style="font-size:0.85rem;color:#e2e8f0;font-weight:700;margin:8px 0;">'
        '📡 LIVE SIGNAL TRACE — Per-Symbol Pipeline</div>',
        unsafe_allow_html=True,
    )
    render_signal_trace(funnel)

    st.markdown("---")

    # Task 5 + Task 6: Generated + Emitted side by side
    col_g, col_e = st.columns(2)
    with col_g:
        render_generated_signals(funnel, signals)
    with col_e:
        render_emitted_signals(signals, positions)

    st.markdown("---")

    # Task 8: Top 20 Closest to Emission
    render_top_20_candidates(funnel, signals)

    st.markdown("---")

    # Task 9: Pipeline Alerts
    render_pipeline_alerts(funnel, alerts)
