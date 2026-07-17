"""
🧠 Smart Money Price Map — Real-Time BUY/SELL Signals + Institutional Analysis
Shows live trading signals with entry/SL/TP, accumulation/distribution,
hidden orders, iceberg patterns, absorption levels, and liquidity map data.
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


# ── Auto-Refresh Config ───────────────────────────────────────
_SM_REFRESH_INTERVAL = 3  # seconds — aggressive real-time


def _trigger_sm_refresh():
    """Auto-refresh using streamlit-autorefresh JS timer."""
    st_autorefresh(interval=_SM_REFRESH_INTERVAL * 1000, key="sm_autorefresh")


def _fmt(val: float, prefix: str = "", suffix: str = "") -> str:
    """Format large numbers with K/M/B/T suffixes."""
    if val is None or (isinstance(val, float) and (val != val)):  # NaN check
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
        return f"{prefix}{val:.4f}{suffix}"


# ── Coin Names ──────────────────────────────────────────────────
_COIN_NAMES = {
    "BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum", "SOLUSDT": "Solana",
    "BNBUSDT": "BNB", "XRPUSDT": "XRP", "DOGEUSDT": "Dogecoin",
    "ADAUSDT": "Cardano", "AVAXUSDT": "Avalanche", "DOTUSDT": "Polkadot",
    "LINKUSDT": "Chainlink", "MATICUSDT": "Polygon", "UNIUSDT": "Uniswap",
    "LTCUSDT": "Litecoin", "ATOMUSDT": "Cosmos", "NEARUSDT": "NEAR",
    "APTUSDT": "Aptos", "SUIUSDT": "Sui", "ARBUSDT": "Arbitrum",
    "OPUSDT": "Optimism", "FILUSDT": "Filecoin", "INJUSDT": "Injective",
    "TIAUSDT": "Celestia", "SEIUSDT": "Sei", "JUPUSDT": "Jupiter",
    "WIFUSDT": "dogwifhat", "PEPEUSDT": "Pepe", "BONKUSDT": "Bonk",
    "1000PEPEUSDT": "1000PEPE", "SHIBUSDT": "Shiba Inu",
    "FETUSDT": "Fetch.ai", "RENDERUSDT": "Render", "GRTUSDT": "The Graph",
    "AAVEUSDT": "Aave", "MKRUSDT": "Maker", "CRVUSDT": "Curve",
    "DYDXUSDT": "dYdX", "GMXUSDT": "GMX", "PENDLEUSDT": "Pendle",
    "ENAUSDT": "Ethena", "WLDUSDT": "Worldcoin", "ONDOUSDT": "Ondo",
    "TRXUSDT": "Tron", "LDOUSDT": "Lido", "IMXUSDT": "Immutable X",
    "ALGOUSDT": "Algorand", "SANDUSDT": "The Sandbox",
    "GALAUSDT": "Gala", "AXSUSDT": "Axie Infinity", "FLOWUSDT": "Flow",
    "CHZUSDT": "Chiliz",
}


def _calc_rr(sig: Dict) -> float:
    """Calculate risk:reward ratio from a signal."""
    entry = sig.get("entry_price", 0)
    sl = sig.get("stop_loss", 0)
    tp = sig.get("take_profit", 0)
    if not entry or not sl or not tp:
        return 0
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    return round(reward / risk, 2) if risk > 0 else 0


def _render_sm_signal_card(sig: Dict, sm_data: Dict, rank: int) -> None:
    """Render a single Smart Money signal card with entry, SL/TP, grade, and SM backing."""
    sig_type = sig.get("side", sig.get("type", "LONG"))
    icon = "🟢 BUY" if sig_type == "LONG" else "🔴 SHORT"
    border_color = '#00ff88' if sig_type == 'LONG' else '#ff4444'
    bg_gradient = 'rgba(0,255,136,0.06)' if sig_type == 'LONG' else 'rgba(255,68,68,0.06)'

    confidence = sig.get("confidence", 0)
    entry = sig.get("entry_price", 0)
    sl = sig.get("stop_loss", 0)
    tp = sig.get("take_profit", 0)
    tp1 = sig.get("take_profit_1", tp)
    tp2 = sig.get("take_profit_2", 0)
    tp3 = sig.get("take_profit_3", 0)
    sl_dist = sig.get("sl_distance_pct", abs(entry - sl) / entry * 100 if entry else 0)
    tp_dist = sig.get("tp_distance_pct", abs(tp - entry) / entry * 100 if entry else 0)
    rr = sig.get("risk_reward", _calc_rr(sig))
    rr_1 = sig.get("rr_1", 0)
    rr_2 = sig.get("rr_2", 0)
    rr_3 = sig.get("rr_3", 0)
    sl_source = sig.get("sl_source", "")
    tp1_source = sig.get("tp1_source", "")
    inst_score = sig.get("institutional_score", 0)
    signal_grade = sig.get("signal_grade", "C")
    grade_score = sig.get("grade_score", 0)
    chg_24h = sig.get("change_24h", 0) or 0
    chg_color = "#00ff88" if chg_24h >= 0 else "#ff4444"
    ind = sig.get("indicators", {})
    rsi = ind.get("rsi", 50)
    vol_ratio = ind.get("vol_ratio", 1)
    factors = sig.get("confirmation_factors", [])

    # Signal grade badge
    grade_colors = {"A+": "#00ff88", "A": "#00cc66", "B": "#f59e0b", "C": "#888"}
    grade_color = grade_colors.get(signal_grade, "#888")
    grade_emojis = {"A+": "💎", "A": "⭐", "B": "🔶", "C": "📊"}
    grade_emoji = grade_emojis.get(signal_grade, "📊")

    # Entry time
    created_at = sig.get("created_at", 0)
    entry_time = datetime.fromtimestamp(created_at).strftime('%H:%M') if created_at else ""

    # Smart money backing data
    sm_side = sm_data.get("smart_money_side", "neutral") if sm_data else "neutral"
    sm_strength = sm_data.get("smart_money_strength", 0) if sm_data else 0
    sm_accum = sm_data.get("accumulation_score", 0) if sm_data else 0
    sm_distrib = sm_data.get("distribution_score", 0) if sm_data else 0
    inst_prob = sm_data.get("inst_probability", 0) if sm_data else 0
    accum_prob = sm_data.get("accum_probability", 0) if sm_data else 0
    whale_prob = sm_data.get("whale_probability", 0) if sm_data else 0
    sm_active = sm_data.get("active_signals", []) if sm_data else []

    # SM backing indicator
    if sig_type == "LONG" and sm_side == "accumulating":
        sm_badge = '<span style="background:#00ff8822;color:#00ff88;padding:1px 6px;border-radius:4px;font-size:0.7rem;border:1px solid #00ff8844;">🧠 SM ACCUMULATING ✓</span>'
    elif sig_type == "SHORT" and sm_side == "distributing":
        sm_badge = '<span style="background:#ff444422;color:#ff4444;padding:1px 6px;border-radius:4px;font-size:0.7rem;border:1px solid #ff444444;">🧠 SM DISTRIBUTING ✓</span>'
    else:
        sm_badge = f'<span style="background:#94a3b822;color:#94a3b8;padding:1px 6px;border-radius:4px;font-size:0.7rem;border:1px solid #94a3b844;">🧠 SM: {sm_side}</span>'

    # Confirmation factors tags
    conf_tags = ""
    if factors:
        conf_tags = " ".join([
            f'<span style="background:#1e293b;color:#94a3b8;padding:1px 5px;border-radius:3px;font-size:0.65rem;">{f}</span>'
            for f in factors[:5]
        ])

    # SM active signals tags
    SIGNAL_EMOJI = {
        'accumulation': '🟢 ACC', 'distribution': '🔴 DIST',
        'reaccumulation': '♻️ RE-ACC', 'redistribution': '🔀 RE-DIST',
        'sweep_active': '⚡ SWEEP', 'absorption_active': '🔄 ABSORB',
        'iceberg_active': '🧊 ICE', 'flow_buying': '📈 BUY FLOW',
        'flow_selling': '📉 SELL FLOW',
    }
    sm_tags = " ".join([
        f'<span style="background:#1e293b;color:#94a3b8;padding:1px 5px;border-radius:3px;font-size:0.65rem;">{SIGNAL_EMOJI.get(s, s)}</span>'
        for s in sm_active[:5]
    ])

    # SL/TP bar widths
    sl_bar = min(sl_dist / 5 * 100, 100)
    tp_bar = min(tp_dist / 10 * 100, 100)

    # R:R color
    rr_color = '#00ff88' if rr >= 2.0 else '#f59e0b' if rr >= 1.5 else '#ff4444'

    card_html = f"""<!DOCTYPE html><html><head><style>
    * {{ margin:0; padding:0; box-sizing:border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
           background: transparent; color: #ccc; font-size: 12px; padding: 4px; overflow-wrap: anywhere; }}
    code {{ font-family: 'SF Mono', 'Fira Code', monospace; font-size: 0.82rem; }}
    </style></head><body>
    <div style="border-left:4px solid {border_color};padding:10px 12px;
        background:linear-gradient(90deg,{bg_gradient} 0%,transparent 100%);
        border-radius:6px;width:100%;position:relative;">
        <!-- Grade badge -->
        <div style="position:absolute;top:6px;right:6px;background:{grade_color}22;
            color:{grade_color};padding:1px 6px;border-radius:10px;font-size:0.7rem;
            font-weight:600;border:1px solid {grade_color}44;">{grade_emoji} {signal_grade} ({grade_score:.0f})</div>
        <!-- Header row -->
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;flex-wrap:wrap;gap:4px;">
            <div>
                <span style="font-size:0.9rem;color:#fff;font-weight:700;">#{rank} {icon}</span>
                <span style="font-size:0.88rem;color:#fff;font-weight:600;margin-left:4px;">{sig.get('symbol','?').replace('USDT','')}</span>
                <span style="font-size:0.75rem;color:#888;margin-left:3px;">USDT</span>
            </div>
            <div style="display:flex;gap:6px;align-items:center;">
                {sm_badge}
                <span style="font-size:0.68rem;color:#666;">{entry_time}</span>
            </div>
        </div>
        <!-- Entry / SL / TP row -->
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:4px;">
            <div>
                <span style="color:#aaa;font-size:0.7rem;font-weight:600;">ENTRY</span>
                <code style="color:#fff;background:#1a1a2e;padding:1px 5px;border-radius:3px;margin-left:3px;">${entry:,.4f}</code>
            </div>
            <div>
                <span style="color:#ff4444;font-size:0.7rem;font-weight:600;">SL</span>
                <code style="color:#ff4444;background:#1a1a2e;padding:1px 5px;border-radius:3px;margin-left:3px;">${sl:,.4f}</code>
                <span style="font-size:0.68rem;color:#666;margin-left:2px;">({sl_dist:.2f}%)</span>
            </div>
            <div>
                <span style="color:#00ff88;font-size:0.7rem;font-weight:600;">TP</span>
                <code style="color:#00ff88;background:#1a1a2e;padding:1px 5px;border-radius:3px;margin-left:3px;">${tp:,.4f}</code>
                <span style="font-size:0.68rem;color:#666;margin-left:2px;">(+{tp_dist:.2f}%)</span>
            </div>
        </div>
        {f'''<div style="display:flex;gap:8px;font-size:0.68rem;margin-top:1px;flex-wrap:wrap;">
            {f"<span style=\"color:#3b82f6;\">🎯 TP2 ${tp2:,.4f} ({rr_2:.1f}x)</span>" if tp2 > 0 else ""}
            {f"<span style=\"color:#8b5cf6;\">🚀 TP3 ${tp3:,.4f} ({rr_3:.1f}x)</span>" if tp3 > 0 else ""}
        </div>''' if tp2 > 0 else ''}
        <!-- SL/TP distance bars -->
        <div style="display:flex;gap:6px;margin:3px 0;align-items:center;">
            <span style="font-size:0.65rem;color:#555;width:16px;">SL</span>
            <div style="flex:1;height:2px;background:#1a1a2e;border-radius:2px;max-width:80px;">
                <div style="height:100%;width:{sl_bar:.0f}%;background:linear-gradient(90deg,#ff444488,#ff4444);border-radius:2px;"></div>
            </div>
            <span style="font-size:0.65rem;color:#555;width:16px;">TP</span>
            <div style="flex:1;height:2px;background:#1a1a2e;border-radius:2px;max-width:80px;">
                <div style="height:100%;width:{tp_bar:.0f}%;background:linear-gradient(90deg,#00ff8888,#00ff88);border-radius:2px;"></div>
            </div>
        </div>
        <!-- Metrics row -->
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:3px;">
            <span><span style="color:#aaa;font-size:0.7rem;">INST</span> <code style="color:#fff;font-size:0.78rem;">{inst_score:.0f}</code></span>
            <span><span style="color:#aaa;font-size:0.7rem;">CONF</span> <code style="color:#fff;font-size:0.78rem;">{confidence:.0%}</code></span>
            <span><span style="color:#aaa;font-size:0.7rem;">R:R</span> <code style="color:{rr_color};font-size:0.78rem;">{rr:.1f}x</code></span>
            {f'<span style="font-size:0.65rem;color:#3b82f6;">→{rr_2:.1f}x</span>' if rr_2 > 0 else ''}
            {f'<span style="font-size:0.65rem;color:#8b5cf6;">→{rr_3:.1f}x</span>' if rr_3 > 0 else ''}
            <span><span style="color:#aaa;font-size:0.7rem;">RSI</span> <code style="color:{'#ff4444' if rsi>70 else '#00ff88' if rsi<30 else '#fff'};font-size:0.78rem;">{rsi:.0f}</code></span>
            <span><span style="color:#aaa;font-size:0.7rem;">VOL</span> <code style="color:#fff;font-size:0.78rem;">{vol_ratio:.1f}x</code></span>
            <span><span style="color:#aaa;font-size:0.7rem;">24h</span> <code style="color:{chg_color};font-size:0.78rem;">{chg_24h:+.1f}%</code></span>
        </div>
        <!-- SM Probability row -->
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:3px;">
            <span style="font-size:0.68rem;"><span style="color:#aaa;">Inst%</span> <span style="color:{'#00ff88' if inst_prob>0.5 else '#f59e0b' if inst_prob>0.3 else '#666'};">{inst_prob:.0%}</span></span>
            <span style="font-size:0.68rem;"><span style="color:#aaa;">Accum%</span> <span style="color:{'#00ff88' if accum_prob>0.5 else '#f59e0b' if accum_prob>0.3 else '#666'};">{accum_prob:.0%}</span></span>
            <span style="font-size:0.68rem;"><span style="color:#aaa;">Whale%</span> <span style="color:{'#00ff88' if whale_prob>0.5 else '#f59e0b' if whale_prob>0.3 else '#666'};">{whale_prob:.0%}</span></span>
            <span style="font-size:0.68rem;"><span style="color:#aaa;">SM Str</span> <span style="color:{'#00ff88' if sm_strength>60 else '#f59e0b' if sm_strength>30 else '#666'};">{sm_strength:.0f}</span></span>
        </div>
        <!-- Factors & SM signals -->
        <div style="margin-top:4px;display:flex;flex-wrap:wrap;gap:3px;">
            {conf_tags}
            {sm_tags}
        </div>
    </div>
    </body></html>"""

    components.html(card_html, height=170, scrolling=False)


def _compute_quality_score(sig: Dict, sm_data: Dict) -> float:
    """Compute a composite quality score (0-100) for ranking signals.
    
    Components:
    - Signal grade score (30%): from weighted institutional model
    - Grade confidence (15%): how confident the grading model is
    - Institutional score (20%): raw institutional analysis score
    - R:R ratio (15%): risk-adjusted reward potential
    - Confidence (10%): blended AI + institutional confidence
    - SM backing bonus (10%): alignment with smart money flow
    """
    grade_score = sig.get("grade_score", 0)
    grade_conf = sig.get("grade_confidence", 0)
    inst_score = sig.get("institutional_score", 0)
    confidence = sig.get("confidence", 0)

    # R:R calculation
    entry = sig.get("entry_price", 0)
    sl = sig.get("stop_loss", 0)
    tp = sig.get("take_profit", 0)
    risk = abs(entry - sl) if entry and sl else 0
    reward = abs(tp - entry) if tp and entry else 0
    rr = reward / risk if risk > 0 else 0
    rr_score = min(rr / 3.0 * 100, 100)  # 3.0x R:R = max score

    # Pillar consistency bonus
    pillars = sig.get("pillar_scores", {})
    pillar_avg = sum(pillars.values()) / len(pillars) if pillars else 0

    # SM backing bonus
    sm_bonus = 0
    if sm_data:
        sm_side = sm_data.get("smart_money_side", "neutral")
        sig_side = sig.get("side", "LONG")
        if (sig_side == "LONG" and sm_side == "accumulating") or \
           (sig_side == "SHORT" and sm_side == "distributing"):
            sm_strength = sm_data.get("smart_money_strength", 0)
            sm_bonus = min(sm_strength, 30)  # max 30 points from SM

    score = (
        grade_score * 0.30 +       # 0-30 from grade model
        grade_conf * 15 +          # 0-15 from grade confidence
        inst_score * 0.20 +        # 0-20 from institutional score
        rr_score * 0.15 +          # 0-15 from R:R
        confidence * 10 +          # 0-10 from confidence
        sm_bonus                   # 0-30 from SM alignment
    )
    return min(round(score, 1), 100)


def _is_high_quality(sig: Dict) -> bool:
    """Filter: only pass signals that meet ALL high-quality criteria."""
    # 1. Must be A or A+ grade
    grade = sig.get("signal_grade", "C")
    if grade not in ("A", "A+"):
        return False

    # 2. Grade score must be >= 65 (out of 100)
    grade_score = sig.get("grade_score", 0)
    if grade_score < 65:
        return False

    # 3. Grade confidence must be >= 0.90
    grade_conf = sig.get("grade_confidence", 0)
    if grade_conf < 0.90:
        return False

    # 4. R:R must be >= 1.8x
    entry = sig.get("entry_price", 0)
    sl = sig.get("stop_loss", 0)
    tp = sig.get("take_profit", 0)
    risk = abs(entry - sl) if entry and sl else 0
    reward = abs(tp - entry) if tp and entry else 0
    rr = reward / risk if risk > 0 else 0
    if rr < 1.8:
        return False

    # 5. Confidence must be >= 0.50
    confidence = sig.get("confidence", 0)
    if confidence < 0.50:
        return False

    # 6. Must have at least 2 confirmation factors
    factors = sig.get("confirmation_factors", [])
    if len(factors) < 2:
        return False

    # 7. Pillar scores must be consistent (avg >= 55)
    pillars = sig.get("pillar_scores", {})
    pillar_avg = sum(pillars.values()) / len(pillars) if pillars else 0
    if pillar_avg < 55:
        return False

    return True


def _render_buy_sell_signals(signals: List[Dict], sm_rows: List[Dict]) -> None:
    """Render only the highest quality BUY/SELL signal cards with smart money backing."""
    if not signals:
        st.info("⏳ No active signals. Engine generates signals from multi-factor institutional analysis.")
        return

    # Build SM lookup by symbol
    sm_lookup = {r["symbol"]: r for r in sm_rows}

    # ── QUALITY FILTER: only highest quality signals ──
    high_quality = [s for s in signals if _is_high_quality(s)]

    # Compute composite quality score for ranking
    for sig in high_quality:
        sym = sig.get("symbol", "")
        sm_d = sm_lookup.get(sym, {})
        sig["_quality_score"] = _compute_quality_score(sig, sm_d)

    # Sort by composite quality score (highest first)
    sorted_sigs = sorted(high_quality, key=lambda x: x.get("_quality_score", 0), reverse=True)

    # Summary bar
    longs = sum(1 for s in sorted_sigs if s.get("side") == "LONG")
    shorts = sum(1 for s in sorted_sigs if s.get("side") == "SHORT")
    avg_score = np.mean([s.get("institutional_score", 0) for s in sorted_sigs]) if sorted_sigs else 0
    avg_conf = np.mean([s.get("confidence", 0) for s in sorted_sigs]) if sorted_sigs else 0
    avg_qscore = np.mean([s.get("_quality_score", 0) for s in sorted_sigs]) if sorted_sigs else 0

    # SM-backed signals
    sm_backed = 0
    for s in sorted_sigs:
        sym = s.get("symbol", "")
        sm_d = sm_lookup.get(sym, {})
        sm_s = sm_d.get("smart_money_side", "neutral")
        if (s.get("side") == "LONG" and sm_s == "accumulating") or \
           (s.get("side") == "SHORT" and sm_s == "distributing"):
            sm_backed += 1

    # Summary metrics row
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("🟢 LONG", f"{longs}")
    m2.metric("🔴 SHORT", f"{shorts}")
    m3.metric("📊 Avg Score", f"{avg_score:.0f}")
    m4.metric("🎯 Avg Conf", f"{avg_conf:.0%}")
    m5.metric("⭐ Quality", f"{avg_qscore:.0f}")
    m6.metric("🧠 SM Backed", f"{sm_backed}/{len(sorted_sigs)}")

    # Quality info
    filtered_out = len(signals) - len(sorted_sigs)
    if filtered_out > 0:
        st.markdown(f'<div style="background:#1e293b;padding:6px 12px;border-radius:6px;border:1px solid #334155;font-size:0.8rem;color:#94a3b8;margin-bottom:8px;">'
                    f'🔍 <b>Quality Filter:</b> Showing <b>{len(sorted_sigs)}</b> elite signals '
                    f'(filtered out {filtered_out} lower-quality signals) · '
                    f'Criteria: Grade ≥ A, Grade Score ≥ 65, Grade Conf ≥ 90%, R:R ≥ 1.8x, '
                    f'Confidence ≥ 50%, Factors ≥ 2, Pillars ≥ 55'
                    f'</div>', unsafe_allow_html=True)

    if not sorted_sigs:
        st.warning("⏳ No signals currently meet the highest quality criteria. "
                   "The engine is scanning for elite setups across 50 symbols.")
        return

    st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)

    # Render signal cards in 2 columns
    for i in range(0, len(sorted_sigs), 2):
        cols = st.columns(2)
        for j, col in enumerate(cols):
            idx = i + j
            if idx < len(sorted_sigs):
                sig = sorted_sigs[idx]
                sym = sig.get("symbol", "")
                sm_d = sm_lookup.get(sym, {})
                with col:
                    _render_sm_signal_card(sig, sm_d, idx + 1)


# ── Page Config ──────────────────────────────────────────────────
st.set_page_config(
    page_title="🧠 Smart Money — DeltaTerminal",
    page_icon="🧠",
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
        --gold: #f5a623; --gold-glow: rgba(245,166,35,0.12);
        --blue: #3b82f6; --purple: #a855f7;
        --text-primary: #e8ecf1; --text-secondary: #8892a4; --text-muted: #5a6478;
    }

    .block-container { padding: 0.3rem 0.5rem !important; max-width: 100% !important; margin: 0 !important; background: var(--bg-primary); }
    #MainMenu { visibility: hidden; } footer { visibility: hidden; } header { visibility: hidden; }

    .sm-card {
        background: linear-gradient(135deg, var(--bg-card) 0%, var(--bg-secondary) 100%);
        padding: 14px 16px; border-radius: 12px;
        border: 1px solid var(--border-subtle); margin-bottom: 10px;
        backdrop-filter: blur(8px); transition: all 0.2s;
    }
    .sm-card:hover { border-color: var(--border-accent); box-shadow: 0 4px 16px rgba(0,0,0,0.3); }
    .sm-title { color: var(--text-secondary); font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 600; }
    .sm-value { color: var(--text-primary); font-size: 1.6rem; font-weight: 800; }
    .sm-sub { color: var(--text-muted); font-size: 0.82rem; }
    .accum { color: var(--green); }
    .distrib { color: var(--red); }
    .neutral { color: var(--text-secondary); }
    .level-buy { background: linear-gradient(90deg, var(--green-glow), transparent); border-left: 3px solid var(--green); padding: 8px 14px; margin: 4px 0; border-radius: 0 10px 10px 0; font-size: 0.88rem; }
    .level-sell { background: linear-gradient(90deg, var(--red-glow), transparent); border-left: 3px solid var(--red); padding: 8px 14px; margin: 4px 0; border-radius: 0 10px 10px 0; font-size: 0.88rem; }
    .level-neutral { background: linear-gradient(90deg, rgba(138,146,164,0.08), transparent); border-left: 3px solid var(--text-muted); padding: 8px 14px; margin: 4px 0; border-radius: 0 10px 10px 0; font-size: 0.88rem; }
    .sm-section-header {
        font-size: 0.95rem; font-weight: 700; color: var(--text-primary);
        padding: 6px 12px; margin: 6px 0 4px 0;
        border: 1px solid var(--border-subtle);
        border-left: 3px solid var(--gold);
        border-radius: 0 8px 8px 0;
        background: linear-gradient(90deg, var(--gold-glow) 0%, var(--bg-secondary) 100%);
        letter-spacing: 0.02em;
    }
    .sm-signal-section {
        border: 1px solid var(--border-subtle);
        border-left: 3px solid var(--green);
        border-radius: 0 8px 8px 0;
        background: linear-gradient(90deg, var(--green-glow) 0%, var(--bg-secondary) 100%);
        padding: 8px 12px;
        margin: 8px 0;
    }
    .sm-analysis-section {
        border: 1px solid var(--border-subtle);
        border-left: 3px solid var(--blue);
        border-radius: 0 8px 8px 0;
        background: linear-gradient(90deg, rgba(59,130,246,0.1) 0%, var(--bg-secondary) 100%);
        padding: 8px 12px;
        margin: 8px 0;
    }
    .sm-maps-section {
        border: 1px solid var(--border-subtle);
        border-left: 3px solid var(--purple);
        border-radius: 0 8px 8px 0;
        background: linear-gradient(90deg, rgba(168,85,247,0.1) 0%, var(--bg-secondary) 100%);
        padding: 8px 12px;
        margin: 8px 0;
    }
</style>
""", unsafe_allow_html=True)


def _render_price_map(sym: str, row: Dict, price: float) -> None:
    """Render a clean, readable price map for a single symbol."""
    levels = row.get("price_levels", [])
    if not levels:
        st.caption("No price levels detected")
        return

    # Source colors and styles
    SOURCE_STYLE = {
        "smart_money":    {"color": "#a855f7", "dash": "solid",   "label": "🧠"},
        "institutional":  {"color": "#3b82f6", "dash": "solid",   "label": "🏦"},
        "absorption":     {"color": "#f97316", "dash": "dashdot", "label": "🔄"},
        "liquidity_map":  {"color": "#64748b", "dash": "dot",     "label": "📍"},
    }
    SIDE_COLOR = {"buy": "#00ff88", "sell": "#ff4444", "neutral": "#94a3b8"}

    # Deduplicate: merge levels within 0.05% of each other
    merged: List[Dict] = []
    sorted_levels = sorted(levels, key=lambda x: x["price"])
    for lvl in sorted_levels:
        if merged and abs(lvl["price"] - merged[-1]["price"]) / max(merged[-1]["price"], 0.0001) < 0.0005:
            # Merge into previous — keep stronger
            if lvl.get("strength", 0) > merged[-1].get("strength", 0):
                merged[-1] = lvl
        else:
            merged.append(lvl)

    # Sort by strength and take top 6 for chart clarity (less congestion)
    top_levels = sorted(merged, key=lambda x: x.get("strength", 0), reverse=True)[:6]

    fig = go.Figure()

    # Current price line
    fig.add_hline(y=price, line_dash="dash", line_color="#f59e0b", line_width=2,
                  annotation_text=f"NOW ${price:,.4f}",
                  annotation_position="top right",
                  annotation_font=dict(size=13, color="#f59e0b"))

    # Add each level as a line with hover tooltip
    for i, lvl in enumerate(top_levels):
        src = lvl.get("source", "liquidity_map")
        side = lvl.get("side", "neutral")
        style = SOURCE_STYLE.get(src, SOURCE_STYLE["liquidity_map"])
        s_color = SIDE_COLOR.get(side, "#94a3b8")
        strength = lvl.get("strength", 0.5)
        line_w = max(1, min(int(strength * 5), 5))

        fig.add_hline(
            y=lvl["price"],
            line_color=s_color,
            line_dash=style["dash"],
            line_width=line_w,
            opacity=max(0.4, min(strength, 1.0)),
            annotation_text=f"{style['label']} {lvl['type']}",
            annotation_position="left" if i % 2 == 0 else "right",
            annotation_font=dict(size=10, color=s_color),
        )

        # Hover tooltip via scatter
        fig.add_scatter(
            x=[None], y=[lvl["price"]],
            mode="markers",
            marker=dict(size=8, color=s_color, symbol="diamond"),
            hovertemplate=(
                f"<b>{style['label']} {lvl['type'].upper()}</b><br>"
                f"Price: ${lvl['price']:,.6f}<br>"
                f"Source: {src}<br>"
                f"Side: {side}<br>"
                f"Strength: {strength:.2f}<br>"
                f"<extra></extra>"
            ),
            showlegend=False,
        )

    # Y-axis range with padding
    all_prices = [l["price"] for l in top_levels] + [price]
    if len(all_prices) > 1:
        p_min, p_max = min(all_prices), max(all_prices)
        margin = max((p_max - p_min) * 0.15, p_min * 0.003)
        fig.update_layout(yaxis_range=[p_min - margin, p_max + margin])

    fig.update_layout(
        height=200,
        margin=dict(l=10, r=10, t=5, b=5),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(15,23,42,0.4)",
        xaxis=dict(visible=False),
        yaxis=dict(
            title="", color="#94a3b8",
            gridcolor="rgba(51,65,85,0.2)",
            tickformat="$.2f" if price > 1 else "$,.6f",
        ),
        showlegend=False,
        hovermode="closest",
    )
    st.plotly_chart(fig, width="stretch", key=f"chart_{sym}")

    # Compact levels table below chart (top 4 only)
    if len(merged) > 0:
        display_levels = sorted(merged, key=lambda x: x.get("strength", 0), reverse=True)[:4]
        rows_html = ""
        for lvl in display_levels:
            src = lvl.get("source", "?")
            side = lvl.get("side", "neutral")
            style = SOURCE_STYLE.get(src, {"color": "#94a3b8", "label": "?"})
            s_color = SIDE_COLOR.get(side, "#94a3b8")
            marker = "▲" if side == "buy" else ("▼" if side == "sell" else "◆")
            rows_html += (
                f'<div style="display:flex;justify-content:space-between;padding:3px 8px;'
                f'border-left:3px solid {s_color};margin:1px 0;border-radius:0 4px 4px 0;'
                f'background:rgba(255,255,255,0.03);font-size:0.78rem;">'
                f'<span style="color:{s_color};">{marker} {lvl["type"]}</span>'
                f'<span style="color:#e2e8f0;">${lvl["price"]:,.4f}</span>'
                f'<span style="color:#94a3b8;">str:{lvl.get("strength",0):.2f}</span>'
                f'<span style="color:#64748b;">{src}</span>'
                f'</div>'
            )
        st.markdown(rows_html, unsafe_allow_html=True)


def _render_summary_cards(rows: List[Dict]) -> None:
    """Render summary metric cards with probability-based display."""
    total_accum = sum(r.get("accumulation_score", 0) for r in rows)
    total_distrib = sum(r.get("distribution_score", 0) for r in rows)
    total_stealth_buys = sum(r.get("stealth_buys", 0) for r in rows)
    total_stealth_sells = sum(r.get("stealth_sells", 0) for r in rows)
    total_absorptions = sum(r.get("absorption_count", 0) for r in rows)
    total_sweeps = sum(r.get("sweep_count", 0) for r in rows)
    total_patterns = sum(r.get("pattern_count", 0) for r in rows)
    accum_symbols = sum(1 for r in rows if r.get("smart_money_side") == "accumulating")
    distrib_symbols = sum(1 for r in rows if r.get("smart_money_side") == "distributing")
    total_hidden = sum(r.get("hidden_order_depth", 0) for r in rows)
    reaccum = sum(1 for r in rows if r.get("reaccumulation_score", 0) > 0.2)
    redistrib = sum(1 for r in rows if r.get("redistribution_score", 0) > 0.2)
    avg_strength = np.mean([r.get("smart_money_strength", 0) for r in rows]) if rows else 0
    strong_symbols = sum(1 for r in rows if r.get("strength_level") == "strong")

    # Probability averages (NaN-safe)
    def _safe_mean(vals):
        clean = [v for v in vals if isinstance(v, (int, float)) and v == v]  # filter NaN
        return float(np.mean(clean)) if clean else 0.0

    avg_inst_prob = _safe_mean([r.get("inst_probability", 0) for r in rows])
    avg_accum_prob = _safe_mean([r.get("accum_probability", 0) for r in rows])
    avg_whale_prob = _safe_mean([r.get("whale_probability", 0) for r in rows])
    high_inst = sum(1 for r in rows if isinstance(r.get("inst_probability", 0), (int, float)) and r.get("inst_probability", 0) == r.get("inst_probability", 0) and r.get("inst_probability", 0) > 0.5)
    high_accum = sum(1 for r in rows if isinstance(r.get("accum_probability", 0), (int, float)) and r.get("accum_probability", 0) == r.get("accum_probability", 0) and r.get("accum_probability", 0) > 0.5)
    high_whale = sum(1 for r in rows if isinstance(r.get("whale_probability", 0), (int, float)) and r.get("whale_probability", 0) == r.get("whale_probability", 0) and r.get("whale_probability", 0) > 0.5)

    # Single row: 3 probability cards (compact)
    p1, p2, p3 = st.columns(3)
    with p1:
        inst_color = "#00ff88" if avg_inst_prob > 0.5 else ("#f59e0b" if avg_inst_prob > 0.3 else "#64748b")
        st.markdown(f"""
        <div style="background:#0f172a;padding:8px 12px;border-radius:8px;border:1px solid #1e293b;text-align:center;">
            <div style="color:#64748b;font-size:0.75rem;">🏦 Institutional</div>
            <div style="color:{inst_color};font-size:1.5rem;font-weight:700;">{avg_inst_prob:.0%}</div>
            <div style="color:#475569;font-size:0.7rem;">{high_inst} &gt; 50%</div>
        </div>
        """, unsafe_allow_html=True)
    with p2:
        accum_color = "#00ff88" if avg_accum_prob > 0.5 else ("#f59e0b" if avg_accum_prob > 0.3 else "#64748b")
        st.markdown(f"""
        <div style="background:#0f172a;padding:8px 12px;border-radius:8px;border:1px solid #1e293b;text-align:center;">
            <div style="color:#64748b;font-size:0.75rem;">📈 Accumulation</div>
            <div style="color:{accum_color};font-size:1.5rem;font-weight:700;">{avg_accum_prob:.0%}</div>
            <div style="color:#475569;font-size:0.7rem;">{high_accum} &gt; 50%</div>
        </div>
        """, unsafe_allow_html=True)
    with p3:
        whale_color = "#00ff88" if avg_whale_prob > 0.5 else ("#f59e0b" if avg_whale_prob > 0.3 else "#64748b")
        st.markdown(f"""
        <div style="background:#0f172a;padding:8px 12px;border-radius:8px;border:1px solid #1e293b;text-align:center;">
            <div style="color:#64748b;font-size:0.75rem;">🐋 Whale</div>
            <div style="color:{whale_color};font-size:1.5rem;font-weight:700;">{avg_whale_prob:.0%}</div>
            <div style="color:#475569;font-size:0.7rem;">{high_whale} &gt; 50%</div>
        </div>
        """, unsafe_allow_html=True)

    # Row 1: Key metrics (single row, 6 columns)
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("💪 Strength", f"{avg_strength:.0f}/100", f"{strong_symbols} strong")
    c2.metric("🟢 Accum", f"{accum_symbols}")
    c3.metric("🔴 Distrib", f"{distrib_symbols}")
    c4.metric("🤫 Stealth", f"{total_stealth_buys}B/{total_stealth_sells}S")
    c5.metric("📦 Hidden", f"{total_hidden}")
    c6.metric("♻️ Re/Redist", f"{reaccum + redistrib}")


def main() -> None:
    # ── Auto-Refresh: Set up JS timer FIRST ──
    _trigger_sm_refresh()

    # ── Header ──────────────────────────────────────────────────
    st.markdown("""
    <div style="background:linear-gradient(90deg,#0f172a,#1e293b);padding:10px 20px;border-radius:10px;border:1px solid #334155;margin-bottom:12px;">
        <span style="color:#f59e0b;font-size:1.2rem;font-weight:700;">🧠 Smart Money Signals</span>
        <span style="color:#94a3b8;font-size:0.85rem;margin-left:12px;">Real-Time BUY/SELL · Institutional Score · Smart Money Backing</span>
    </div>
    """, unsafe_allow_html=True)

    # ── Load Data ────────────────────────────────────────────────
    rows = bridge_reader.read_smart_money_map()
    signals = bridge_reader.read_signals()

    if not rows and not signals:
        st.warning("⏳ Smart money data not yet available. Engine needs ~2 minutes to populate.")
        st.info("The engine collects institutional patterns from WebSocket orderbook data. "
                "Run the engine and wait for data accumulation.")
        return

    # ══════════════════════════════════════════════════════════════
    # SECTION 1: 🔥 REAL-TIME BUY / SELL SIGNALS
    # ══════════════════════════════════════════════════════════════
    st.markdown('<div class="sm-signal-section">', unsafe_allow_html=True)
    st.markdown('<div class="sm-section-header">🔥 Real-Time Smart Money Signals</div>', unsafe_allow_html=True)
    _render_buy_sell_signals(signals, rows)
    st.markdown('</div>', unsafe_allow_html=True)

    st.divider()

    # ══════════════════════════════════════════════════════════════
    # SECTION 2: SUMMARY CARDS (Probabilities)
    # ══════════════════════════════════════════════════════════════
    if rows:
        _render_summary_cards(rows)

        st.divider()

        # ── Filters ─────────────────────────────────────────────────
        col_filter1, col_filter2, col_filter3 = st.columns(3)
        with col_filter1:
            side_filter = st.selectbox("Smart Money Side",
                                        ["All", "accumulating", "distributing", "neutral"],
                                        index=0)
        with col_filter2:
            sort_by = st.selectbox("Sort By",
                                    ["smart_money_strength", "accum_probability",
                                     "accumulation_score", "distribution_score",
                                     "institutional_flow", "hidden_order_depth"],
                                    index=0)
        with col_filter3:
            min_score = st.slider("Min Accumulation Score", 0.0, 1.0, 0.0, 0.05)

        # ── Apply Filters ───────────────────────────────────────────
        filtered = rows
        if side_filter != "All":
            filtered = [r for r in filtered if r.get("smart_money_side") == side_filter]
        if min_score > 0:
            filtered = [r for r in filtered if r.get("accumulation_score", 0) >= min_score]
        filtered.sort(key=lambda x: x.get(sort_by, 0), reverse=True)

        if not filtered:
            st.info("No symbols match the current filters.")
        else:
            # ── Overview Table ──────────────────────────────────────
            st.markdown('<div class="sm-analysis-section">', unsafe_allow_html=True)
            st.markdown('<div class="sm-section-header">📊 Smart Money Overview</div>', unsafe_allow_html=True)

            table_data = []
            for r in filtered:
                sm_side = r.get("smart_money_side", "neutral")
                side_emoji = "🟢" if sm_side == "accumulating" else ("🔴" if sm_side == "distributing" else "⚪")
                strength = r.get("smart_money_strength", 0)
                strength_level = r.get("strength_level", "weak")
                reaccum = r.get("reaccumulation_score", 0)
                redistrib = r.get("redistribution_score", 0)
                reaccum_tag = "♻️" if reaccum > 0.2 else ""
                redistrib_tag = "🔀" if redistrib > 0.2 else ""
                # Probability display with color coding
                inst_p = r.get("inst_probability", 0)
                accum_p = r.get("accum_probability", 0)
                whale_p = r.get("whale_probability", 0)
                whale_conf = r.get("whale_confidence", 0)
                absorb_score = r.get("absorption_score", 0)
                inst_pct = f"{'🟢' if inst_p > 0.5 else '🟠' if inst_p > 0.3 else '⚪'} {inst_p:.0%}"
                accum_pct = f"{'🟢' if accum_p > 0.5 else '🟠' if accum_p > 0.3 else '⚪'} {accum_p:.0%}"
                whale_pct = f"{'🟢' if whale_p > 0.5 else '🟠' if whale_p > 0.3 else '⚪'} {whale_p:.0%}"
                whale_conf_pct = f"{'🟢' if whale_conf > 0.5 else '🟠' if whale_conf > 0.3 else '⚪'} {whale_conf:.0%}"
                absorb_pct = f"{'🟢' if absorb_score > 0.5 else '🟠' if absorb_score > 0.3 else '⚪'} {absorb_score:.0%}"
                # Check if this symbol has an active signal
                has_signal = "✅" if any(s.get("symbol") == r["symbol"] for s in signals) else ""
                table_data.append({
                    "Sig": has_signal,
                    "Symbol": r["symbol"].replace("USDT", ""),
                    "Price": f"${r['price']:,.4f}",
                    "Side": f"{side_emoji} {sm_side}",
                    "Strength": f"{strength:.0f}",
                    "Accum": accum_pct,
                    "Inst Flow": _fmt(r.get("institutional_flow", 0), "$"),
                    "Whale": whale_conf_pct,
                    "Absorb": absorb_pct,
                    "Stealth": f"{r.get('stealth_buys', 0)}/{r.get('stealth_sells', 0)}",
                    "Levels": len(r.get("price_levels", [])),
                })

            df = pd.DataFrame(table_data)
            # Limit to top 40 rows by default for readability
            show_all = st.checkbox("Show all symbols", value=False)
            display_df = df if show_all else df.head(40)
            st.dataframe(display_df, width="stretch", hide_index=True, height=min(len(display_df) * 35 + 40, 600))
            if not show_all and len(df) > 40:
                st.caption(f"Showing top 40 of {len(df)} symbols — check 'Show all' to see more")

            st.markdown('</div>', unsafe_allow_html=True)
            st.divider()

            # ── Individual Symbol Price Maps ────────────────────────
            st.markdown('<div class="sm-maps-section">', unsafe_allow_html=True)
            st.markdown('<div class="sm-section-header">📍 Price Level Maps (Top 6)</div>', unsafe_allow_html=True)

            # Show top 6 symbols by Smart Money Strength Score (reduced from 12)
            top_symbols = sorted(filtered, key=lambda x: x.get("smart_money_strength", 0), reverse=True)[:6]

            for i in range(0, len(top_symbols), 2):
                cols = st.columns(2)
                for j, col in enumerate(cols):
                    if i + j < len(top_symbols):
                        r = top_symbols[i + j]
                        sym = r["symbol"]
                        with col:
                            sm_side = r.get("smart_money_side", "neutral")
                            side_color = "#00ff88" if sm_side == "accumulating" else ("#ff4444" if sm_side == "distributing" else "#94a3b8")
                            n_levels = len(r.get("price_levels", []))

                            # Smart Money Strength Score
                            strength = r.get('smart_money_strength', 0)
                            strength_level = r.get('strength_level', 'weak')
                            strength_color = '#00ff88' if strength_level == 'strong' else ('#f59e0b' if strength_level == 'moderate' else '#64748b')
                            reaccum = r.get('reaccumulation_score', 0)
                            redistrib = r.get('redistribution_score', 0)
                            reaccum_tag = '♻️ REACC' if reaccum > 0.2 else ''
                            redistrib_tag = '🔀 REDIST' if redistrib > 0.2 else ''
                            hidden = r.get('hidden_order_depth', 0)
                            active_signals = r.get('active_signals', [])
                            SIGNAL_EMOJI = {
                                'accumulation': '🟢 AC', 'distribution': '🔴 DI',
                                'reaccumulation': '♻️ RA', 'redistribution': '🔀 RD',
                                'sweep_active': '⚡ SW', 'absorption_active': '🔄 AB',
                                'iceberg_active': '🧊 IC', 'flow_buying': '📈 FB',
                                'flow_selling': '📉 FS',
                            }
                            signal_tags = ' '.join([
                                SIGNAL_EMOJI.get(s, f'• {s}') for s in active_signals[:5]
                            ])

                            inst_p = r.get('inst_probability', 0)
                            accum_p = r.get('accum_probability', 0)
                            whale_p = r.get('whale_probability', 0)
                            inst_p_color = '#00ff88' if inst_p > 0.5 else ('#f59e0b' if inst_p > 0.3 else '#64748b')
                            accum_p_color = '#00ff88' if accum_p > 0.5 else ('#f59e0b' if accum_p > 0.3 else '#64748b')
                            whale_p_color = '#00ff88' if whale_p > 0.5 else ('#f59e0b' if whale_p > 0.3 else '#64748b')

                            reaccum_html = f'<div style="margin-top:3px;font-size:0.7rem;color:#94a3b8;">{reaccum_tag} {redistrib_tag}</div>' if reaccum_tag or redistrib_tag else ''
                            signal_line = f'<div style="margin-top:2px;font-size:0.72rem;color:#64748b;">{signal_tags}</div>' if signal_tags else ''

                            # Check if this symbol has an active signal — highlight it
                            sym_signal = next((s for s in signals if s.get("symbol") == sym), None)
                            signal_indicator = ""
                            if sym_signal:
                                sig_side = sym_signal.get("side", "LONG")
                                sig_color = "#00ff88" if sig_side == "LONG" else "#ff4444"
                                signal_indicator = f'<span style="background:{sig_color}22;color:{sig_color};padding:1px 5px;border-radius:4px;font-size:0.68rem;border:1px solid {sig_color}44;margin-left:6px;">🔥 {sig_side} SIGNAL</span>'

                            header_html = f"""<!DOCTYPE html><html><head></head><body>
                            <div style="background:linear-gradient(135deg,#0f172a,#1a1a2e);
                                padding:8px 12px;border-radius:8px;border:1px solid #334155;margin:0;">
                                <div style="display:flex;justify-content:space-between;align-items:center;">
                                    <div>
                                        <span style="color:#f59e0b;font-size:1.1rem;font-weight:700;">{sym.replace('USDT', '')}</span>
                                        <span style="color:#94a3b8;font-size:0.85rem;margin-left:6px;">${r['price']:,.4f}</span>
                                        <span style="color:{side_color};font-size:0.75rem;margin-left:6px;text-transform:uppercase;">● {sm_side}</span>
                                        <span style="color:{strength_color};font-size:0.75rem;margin-left:6px;font-weight:600;">💪 {strength:.0f}</span>
                                        {signal_indicator}
                                    </div>
                                    <div style="font-size:0.7rem;display:flex;gap:8px;">
                                        <span style="color:{inst_p_color};" title="Institutional Probability">🏦 {inst_p:.0%}</span>
                                        <span style="color:{accum_p_color};" title="Accumulation Probability">📈 {accum_p:.0%}</span>
                                        <span style="color:{whale_p_color};" title="Whale Probability">🐋 {whale_p:.0%}</span>
                                        <span style="color:#64748b;" title="Hidden Orders">📦 {hidden}</span>
                                        <span style="color:#64748b;" title="Levels">📊 {n_levels}</span>
                                    </div>
                                </div>
                                {reaccum_html}
                                {signal_line}
                            </div>
                            </body></html>"""
                            components.html(header_html, height=60, scrolling=False)

                            _render_price_map(sym, r, r["price"])

            st.markdown('</div>', unsafe_allow_html=True)  # Close sm-maps-section

    # ── Legend ───────────────────────────────────────────────────
    st.divider()
    st.markdown("""
    <div style="display:flex;flex-wrap:wrap;gap:16px;font-size:0.8rem;padding:8px 12px;
        background:rgba(15,23,42,0.6);border-radius:8px;border:1px solid #334155;">
        <div><span style="color:#00ff88;">━</span> 🟢 BUY (LONG)</div>
        <div><span style="color:#ff4444;">━</span> 🔴 SELL (SHORT)</div>
        <div><span style="color:#a855f7;">━━</span> 🧠 Smart Money</div>
        <div><span style="color:#3b82f6;">━━</span> 🏦 Institutional</div>
        <div><span style="color:#f97316;">╌╌</span> 🔄 Absorption</div>
        <div><span style="color:#64748b;">····</span> 📍 Liquidity Map</div>
        <div><span style="color:#f59e0b;">╌╌</span> Current Price</div>
    </div>
    """, unsafe_allow_html=True)

    # ── Footer with auto-refresh status ──
    st.caption(
        f"⚡ Auto-refresh every {_SM_REFRESH_INTERVAL}s | "
        f"{datetime.now().strftime('%H:%M:%S')}"
    )


main()
