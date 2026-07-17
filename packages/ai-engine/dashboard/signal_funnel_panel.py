"""
Signal Funnel Analytics Panel — Visual pipeline showing why signals are rejected.
Displays: Market Breadth, Signal Funnel, Signal Death Report, Active Thresholds, Rejection Log.
Upgraded with:
  - Adaptive Threshold display (Phase-1 + Regime thresholds per market state)
  - Signal Death Report (exact kill counts per pipeline stage)
  - Range Reversal Mode indicator
  - Market State card
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

import streamlit as st


def _fmt(n: int) -> str:
    """Format large numbers with commas."""
    return f"{n:,}"


def _pct(part: int, whole: int) -> str:
    """Format as percentage."""
    if whole == 0:
        return "0%"
    return f"{part / whole * 100:.1f}%"


def _bar_markdown(value: float, max_val: float, color: str, label: str, count: int) -> str:
    """Return a single-line-safe HTML bar for st.markdown(..., unsafe_allow_html=True)."""
    width = max(value / max(max_val, 1) * 100, 2) if max_val > 0 else 2
    return (
        f'<div style="display:flex; align-items:center; gap:8px; margin:3px 0;">'
        f'<div style="width:160px; text-align:right; font-size:0.75rem; color:#94a3b8; font-weight:500;">{label}</div>'
        f'<div style="flex:1; background:#1e293b; border-radius:4px; height:22px; position:relative;">'
        f'<div style="width:{width:.1f}%; background:{color}; border-radius:4px; height:100%; opacity:0.85;"></div>'
        f'<span style="position:absolute; right:8px; top:2px; font-size:0.72rem; color:#e2e8f0; font-weight:700;">{_fmt(count)}</span>'
        f'</div>'
        f'<div style="width:50px; text-align:right; font-size:0.7rem; color:#64748b;">{_pct(count, max_val)}</div>'
        f'</div>'
    )


def _death_bar(stage: str, killed: int, total: int, color: str) -> str:
    """Render a death report bar (rejection count per stage)."""
    width = max(killed / max(total, 1) * 100, 1) if total > 0 else 1
    return (
        f'<div style="display:flex; align-items:center; gap:8px; margin:2px 0;">'
        f'<div style="width:120px; text-align:right; font-size:0.75rem; color:#94a3b8; font-weight:500;">{stage}</div>'
        f'<div style="flex:1; background:#1e293b; border-radius:4px; height:18px; position:relative;">'
        f'<div style="width:{width:.1f}%; background:{color}; border-radius:4px; height:100%; opacity:0.9;"></div>'
        f'<span style="position:absolute; right:8px; top:1px; font-size:0.7rem; color:#e2e8f0; font-weight:700;">{_fmt(killed)}</span>'
        f'</div>'
        f'</div>'
    )


def render_signal_funnel(funnel: Dict, market_data: List[Dict], death_data: Dict = None) -> None:
    """Render the complete Signal Funnel Analytics panel with Death Report & Adaptive Thresholds."""
    if not funnel:
        st.info("⏳ Waiting for funnel data from engine...")
        return

    death_report = (death_data or {}).get("death_report", {})
    adaptive = (death_data or {}).get("adaptive_thresholds", {})

    # ══════════════════════════════════════════════════════════════
    # SECTION 0: ADAPTIVE THRESHOLDS & MARKET STATE (TOP)
    # ══════════════════════════════════════════════════════════════
    if adaptive:
        state_label = adaptive.get("market_state_label", "📊 Moderate")
        state_color = adaptive.get("market_state_color", "#f59e0b")
        p1_thresh = adaptive.get("phase1_threshold", 60)
        rg_thresh = adaptive.get("regime_threshold", 65)
        total_syms = adaptive.get("total_symbols", 0)
        avg_rate = adaptive.get("avg_pass_rate", 0)

        st.markdown(
            f'<div style="background:linear-gradient(135deg,#0f172a,#0c1018);'
            f'border:1px solid {state_color}33;border-radius:10px;padding:12px 16px;margin-bottom:8px;">'
            f'<div style="display:flex;align-items:center;justify-content:space-between;">'
            f'<div>'
            f'<span style="font-size:0.7rem;color:#8892a4;text-transform:uppercase;letter-spacing:0.08em;">🎛️ Adaptive Thresholds Active</span>'
            f'</div>'
            f'<div style="display:flex;gap:16px;">'
            f'<div style="text-align:center;">'
            f'<div style="font-size:0.65rem;color:#64748b;">MARKET STATE</div>'
            f'<div style="font-size:1.0rem;font-weight:800;color:{state_color};">{state_label}</div>'
            f'</div>'
            f'<div style="text-align:center;">'
            f'<div style="font-size:0.65rem;color:#64748b;">PHASE-1</div>'
            f'<div style="font-size:1.0rem;font-weight:800;color:#3b82f6;">{p1_thresh:.0f}</div>'
            f'</div>'
            f'<div style="text-align:center;">'
            f'<div style="font-size:0.65rem;color:#64748b;">REGIME</div>'
            f'<div style="font-size:1.0rem;font-weight:800;color:#a855f7;">{rg_thresh:.0f}</div>'
            f'</div>'
            f'<div style="text-align:center;">'
            f'<div style="font-size:0.65rem;color:#64748b;">AVG PASS RATE</div>'
            f'<div style="font-size:1.0rem;font-weight:800;color:#22c55e;">{avg_rate:.1f}%</div>'
            f'</div>'
            f'<div style="text-align:center;">'
            f'<div style="font-size:0.65rem;color:#64748b;">SYMBOLS</div>'
            f'<div style="font-size:1.0rem;font-weight:800;color:#e8ecf1;">{total_syms}</div>'
            f'</div>'
            f'</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ══════════════════════════════════════════════════════════════
    # SECTION 1: MARKET BREADTH
    # ══════════════════════════════════════════════════════════════
    st.markdown('<div class="section-header">📊 Market Breadth</div>', unsafe_allow_html=True)

    if market_data:
        total = len(market_data)
        long_bias = sum(1 for r in market_data if r.get("signal") == "long")
        short_bias = sum(1 for r in market_data if r.get("signal") == "short")
        neutral = total - long_bias - short_bias

        regimes = {}
        for r in market_data:
            rg = r.get("regime", "range")
            regimes[rg] = regimes.get(rg, 0) + 1

        bullish_regime = regimes.get("trending_bull", 0)
        bearish_regime = regimes.get("trending_bear", 0)
        ranging = regimes.get("range", 0)
        compression = regimes.get("compression", 0)
        volatile = regimes.get("volatile", 0)
        breakout = regimes.get("breakout", 0)

        bc1, bc2, bc3, bc4, bc5, bc6 = st.columns(6)
        with bc1:
            st.metric("🟢 LONG Bias", f"{long_bias}", f"{_pct(long_bias, total)}")
        with bc2:
            st.metric("🔴 SHORT Bias", f"{short_bias}", f"{_pct(short_bias, total)}")
        with bc3:
            st.metric("⚪ Neutral", f"{neutral}", f"{_pct(neutral, total)}")
        with bc4:
            st.metric("📈 Bullish", f"{bullish_regime}", f"{_pct(bullish_regime, total)}")
        with bc5:
            st.metric("📉 Bearish", f"{bearish_regime}", f"{_pct(bearish_regime, total)}")
        with bc6:
            st.metric("➡️ Ranging", f"{ranging + compression}", f"{_pct(ranging + compression, total)}")

        # Range Reversal Mode indicator
        ranging_total = ranging + compression
        if total > 0 and ranging_total / total > 0.50:
            st.markdown(
                f'<div style="background:linear-gradient(90deg,rgba(139,92,246,0.15),transparent);'
                f'border:1px solid rgba(139,92,246,0.3);border-radius:8px;padding:8px 12px;margin:6px 0;">'
                f'<span style="color:#a855f7;font-weight:700;">🔄 RANGE REVERSAL MODE ACTIVE</span> — '
                f'<span style="color:#94a3b8;">{ranging_total}/{total} symbols ({_pct(ranging_total, total)}) are ranging. '
                f'Mean-reversion signals allowed at range boundaries.</span>'
                f'</div>',
                unsafe_allow_html=True,
            )

    # ══════════════════════════════════════════════════════════════
    # SECTION 2: SIGNAL DEATH REPORT (THE KILLER DEBUG PANEL)
    # ══════════════════════════════════════════════════════════════
    st.markdown('<div class="section-header">💀 Signal Death Report</div>', unsafe_allow_html=True)

    if death_report:
        scanned = death_report.get("scanned", funnel.get("symbols_processed", 0))
        emitted = death_report.get("emitted", funnel.get("signals_emitted", 0))
        deaths = death_report.get("deaths_by_stage", {})
        top_killer = death_report.get("top_killer", "unknown")
        top_count = death_report.get("top_killer_count", 0)
        pass_rate = death_report.get("pass_rate", 0)

        # Death report header card
        kill_color = "#ef4444" if scanned > 0 and emitted == 0 else ("#f59e0b" if emitted < 3 else "#22c55e")
        st.markdown(
            f'<div style="background:linear-gradient(135deg,#0f172a,#0c1018);'
            f'border:1px solid {kill_color}33;border-radius:10px;padding:12px 16px;margin:6px 0;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;">'
            f'<div>'
            f'<span style="font-size:0.7rem;color:#8892a4;text-transform:uppercase;">Scanned</span> '
            f'<span style="font-size:1.2rem;font-weight:800;color:#3b82f6;">{_fmt(scanned)}</span>'
            f'</div>'
            f'<div style="text-align:center;">'
            f'<span style="font-size:0.7rem;color:#8892a4;">Top Killer:</span> '
            f'<span style="font-size:1.0rem;font-weight:800;color:#ef4444;">{top_killer.upper()}</span> '
            f'<span style="font-size:0.8rem;color:#94a3b8;">({_fmt(top_count)} kills)</span>'
            f'</div>'
            f'<div style="text-align:right;">'
            f'<span style="font-size:0.7rem;color:#8892a4;">Elite Signals</span> '
            f'<span style="font-size:1.2rem;font-weight:800;color:{kill_color};">{_fmt(emitted)}</span>'
            f'</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        # Death bars per pipeline stage
        kill_colors = {
            "scorer": "#6b7280", "phase1": "#ef4444", "regime": "#f59e0b",
            "sweep": "#8b5cf6", "oi": "#f97316", "cvd": "#ec4899",
            "filter": "#06b6d4", "other": "#64748b",
        }
        stage_labels = {
            "scorer": "🧠 AI Scorer", "phase1": "🎯 Phase-1 Gate", "regime": "🔄 Regime Filter",
            "sweep": "💧 Sweep Validation", "oi": "📈 OI Validation", "cvd": "📊 CVD Divergence",
            "filter": "🔧 Signal Filter", "other": "📌 Other",
        }

        max_deaths = max(deaths.values()) if deaths else 1
        for stage in ["scorer", "phase1", "regime", "sweep", "oi", "cvd", "filter", "other"]:
            count = deaths.get(stage, 0)
            label = stage_labels.get(stage, stage)
            color = kill_colors.get(stage, "#64748b")
            is_top = stage == top_killer
            border = "border:1px solid #ef444444;" if is_top and count > 0 else ""
            st.markdown(
                f'<div style="{border}border-radius:4px;padding:1px 4px;">'
                f'{_death_bar(label, count, max(max_deaths, 1), color)}'
                f'</div>',
                unsafe_allow_html=True,
            )

        # Pass rate gauge
        pass_color = "#22c55e" if pass_rate >= 1 else ("#f59e0b" if pass_rate >= 0.5 else "#ef4444")
        st.markdown(
            f'<div style="text-align:center;margin:4px 0;">'
            f'<span style="font-size:0.75rem;color:#8892a4;">Pass Rate: </span>'
            f'<span style="font-size:1.0rem;font-weight:800;color:{pass_color};">{pass_rate:.2f}%</span>'
            f'<span style="font-size:0.7rem;color:#64748b;"> (target: 1%–5%)</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
    else:
        # Fallback: use funnel data only
        scanned = funnel.get("symbols_processed", 0)
        scorer_killed = funnel.get("scorer_rejected", 0)
        phase1_killed = funnel.get("phase1_rejected", 0)
        regime_killed = funnel.get("regime_blocked", 0)
        sweep_killed = funnel.get("sweep_blocked", 0)
        oi_killed = funnel.get("oi_blocked", 0)
        cvd_killed = funnel.get("cvd_blocked", 0)
        emitted = funnel.get("signals_emitted", 0)

        deaths = {
            "scorer": scorer_killed, "phase1": phase1_killed, "regime": regime_killed,
            "sweep": sweep_killed, "oi": oi_killed, "cvd": cvd_killed,
        }
        max_deaths = max(deaths.values()) if deaths else 1
        top_killer = max(deaths, key=deaths.get) if deaths else "none"
        top_count = deaths.get(top_killer, 0)

        kill_colors = {
            "scorer": "#6b7280", "phase1": "#ef4444", "regime": "#f59e0b",
            "sweep": "#8b5cf6", "oi": "#f97316", "cvd": "#ec4899",
        }
        stage_labels = {
            "scorer": "🧠 AI Scorer", "phase1": "🎯 Phase-1 Gate", "regime": "🔄 Regime Filter",
            "sweep": "💧 Sweep Validation", "oi": "📈 OI Validation", "cvd": "📊 CVD Divergence",
        }

        st.markdown(
            f'<div style="background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:12px;">'
            f'<div style="font-size:0.85rem;color:#e2e8f0;font-weight:700;">💀 Signal Death Report</div>'
            f'<div style="font-size:0.75rem;color:#94a3b8;margin-top:4px;">Top Killer: <b style="color:#ef4444;">{top_killer.upper()}</b> ({_fmt(top_count)} kills) | '
            f'Pass Rate: {_pct(emitted, scanned)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        for stage in ["scorer", "phase1", "regime", "sweep", "oi", "cvd"]:
            count = deaths.get(stage, 0)
            label = stage_labels.get(stage, stage)
            color = kill_colors.get(stage, "#64748b")
            st.markdown(_death_bar(label, count, max(max_deaths, 1), color), unsafe_allow_html=True)

    # ══════════════════════════════════════════════════════════════
    # SECTION 3: SIGNAL FUNNEL (visual pipeline)
    # ══════════════════════════════════════════════════════════════
    st.markdown('<div class="section-header">🎯 Signal Funnel — Last Cycle</div>', unsafe_allow_html=True)

    processed = funnel.get("symbols_processed", 0)
    scorer_killed = funnel.get("scorer_rejected", 0)
    phase1_killed = funnel.get("phase1_rejected", 0)
    regime_killed = funnel.get("regime_blocked", 0)
    sweep_killed = funnel.get("sweep_blocked", 0)
    oi_killed = funnel.get("oi_blocked", 0)
    cvd_killed = funnel.get("cvd_blocked", 0)
    emitted = funnel.get("signals_emitted", 0)
    cycle_dur = funnel.get("cycle_duration_sec", 0)

    after_scorer = processed - scorer_killed
    after_phase1 = max(after_scorer - phase1_killed, 0)
    after_regime = max(after_phase1 - regime_killed, 0)
    after_sweep = max(after_regime - sweep_killed, 0)
    after_oi = max(after_sweep - oi_killed, 0)
    after_cvd = max(after_oi - cvd_killed, 0)
    # Extended pipeline stages
    session_killed = funnel.get("session_blocked", 0)
    checklist_killed = funnel.get("checklist_blocked", 0)
    generated = funnel.get("checklist_passed", 0)
    after_session = max(after_cvd - session_killed, 0)
    after_checklist = max(after_session - checklist_killed, 0)

    max_val = max(processed, 1)

    # Get current thresholds for display
    p1_label = f"{adaptive.get('phase1_threshold', 60):.0f}" if adaptive else "60"
    rg_label = f"{adaptive.get('regime_threshold', 65):.0f}" if adaptive else "65"

    st.markdown(
        '<div style="background:#0f172a; border:1px solid #1e293b; border-radius:10px; padding:16px;">'
        '<div style="font-size:0.85rem; color:#e2e8f0; font-weight:700; margin-bottom:10px;">📊 SIGNAL FUNNEL PIPELINE</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown(_bar_markdown(processed, max_val, "#3b82f6", "📥 Symbols Scanned", processed), unsafe_allow_html=True)
    st.markdown(_bar_markdown(after_scorer, max_val, "#f59e0b", "🧠 AI Scorer", after_scorer), unsafe_allow_html=True)
    st.markdown(_bar_markdown(after_phase1, max_val, "#8b5cf6", f"🎯 Phase 1 ({p1_label}+)", after_phase1), unsafe_allow_html=True)
    st.markdown(_bar_markdown(after_regime, max_val, "#06b6d4", f"🔄 Regime ({rg_label}+)", after_regime), unsafe_allow_html=True)
    st.markdown(_bar_markdown(after_sweep, max_val, "#10b981", "💧 Sweep Validated", after_sweep), unsafe_allow_html=True)
    st.markdown(_bar_markdown(after_oi, max_val, "#f97316", "📈 OI Confirmed", after_oi), unsafe_allow_html=True)
    st.markdown(_bar_markdown(after_cvd, max_val, "#ec4899", "📊 CVD Confirmed", after_cvd), unsafe_allow_html=True)
    st.markdown(_bar_markdown(after_session, max_val, "#eab308", "🔒 Session Passed", after_session), unsafe_allow_html=True)
    st.markdown(_bar_markdown(after_checklist, max_val, "#14b8a6", "📋 Checklist Passed", after_checklist), unsafe_allow_html=True)
    st.markdown(_bar_markdown(generated, max_val, "#8b5cf6", "⚡ Generated", generated), unsafe_allow_html=True)
    st.markdown(_bar_markdown(emitted, max_val, "#00ff88", "🚀 EMITTED", emitted), unsafe_allow_html=True)

    # Cycle stats
    fc1, fc2, fc3, fc4, fc5, fc6, fc7 = st.columns(7)
    with fc1:
        st.metric("Cycle Time", f"{cycle_dur:.1f}s")
    with fc2:
        st.metric("FVGs Detected", f"{_fmt(funnel.get('fvg_count', 0))}")
    with fc3:
        st.metric("Session Block", f"{session_killed}")
    with fc4:
        st.metric("Checklist Block", f"{checklist_killed}")
    with fc5:
        st.metric("Generated", f"{generated}")
    with fc6:
        st.metric("Emitted", f"{emitted}")
    with fc7:
        st.metric("Pass Rate", f"{_pct(emitted, max(processed, 1))}")

    # ══════════════════════════════════════════════════════════════
    # SECTION 4: REJECTION LOG (last 20 reasons)
    # ══════════════════════════════════════════════════════════════
    st.markdown('<div class="section-header">🚫 Rejection Log (Last 20)</div>', unsafe_allow_html=True)

    reasons = funnel.get("rejection_reasons", [])
    if reasons:
        reason_counts: Dict[str, int] = {}
        for r in reasons[-100:]:
            key = r.get("reason", "unknown").split(":")[0]
            reason_counts[key] = reason_counts.get(key, 0) + 1

        rc1, rc2 = st.columns(2)
        with rc1:
            st.markdown("**Top Rejection Reasons:**")
            for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1])[:8]:
                st.markdown(f"- `{reason}` — **{count}** rejections")
        with rc2:
            st.markdown("**Recent Rejections:**")
            for r in reasons[-10:]:
                sym = r.get("symbol", "?")
                reason = r.get("reason", "unknown")
                ts = time.strftime("%H:%M:%S", time.localtime(r.get("time", 0))) if r.get("time") else "?"
                st.markdown(f"- `{ts}` **{sym}** — {reason}")
    else:
        st.success("✅ No rejections this cycle — all signals passed!")

    # ══════════════════════════════════════════════════════════════
    # SECTION 5: TOP SCORES (closest to threshold)
    # ══════════════════════════════════════════════════════════════
    top_scores = funnel.get("top_scores", [])
    if top_scores:
        st.markdown('<div class="section-header">🏆 Top Scores This Cycle</div>', unsafe_allow_html=True)
        score_rows = []
        threshold = adaptive.get("phase1_threshold", 60) if adaptive else 60
        for s in top_scores[:10]:
            score_rows.append({
                "Symbol": s.get("symbol", "?"),
                "Side": s.get("side", "?"),
                "Confidence": f"{s.get('confidence', 0):.1f}",
                "Inst. Score": f"{s.get('institutional_score', 0):.1f}",
                "Status": "✅ EMITTED" if s.get("confidence", 0) >= threshold else f"❌ Below {threshold:.0f}",
            })
        import pandas as pd
        st.dataframe(pd.DataFrame(score_rows), width="stretch", hide_index=True)
