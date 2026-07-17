"""
Shared UI helpers for consistent styled metric cards across all dashboard pages.
"""
import streamlit as st


def metric_card(icon: str, label: str, value: str, color: str = "#e8ecf1",
                badge: str = "", badge_color: str = "#00ff88", delta: str = "",
                delta_color: str = "#00ff88"):
    """Render a styled metric card with icon, label, value, and optional badge/delta.

    Usage:
        metric_card("📊", "Total Trades", "31", "#3b82f6")
        metric_card("💰", "PnL", "$+27.46", "#00ff88", badge="✓ Verified")
        metric_card("🎯", "Win Rate", "67.7%", "#00ff88", delta="+2.3%", delta_color="#00ff88")
    """
    delta_html = ""
    if delta:
        delta_html = (
            f'<div style="font-size:0.65rem;color:{delta_color};margin-top:2px;">{delta}</div>'
        )
    badge_html = ""
    if badge:
        badge_html = (
            f'<div style="font-size:0.58rem;color:{badge_color};margin-top:2px;'
            f'opacity:0.8;">{badge}</div>'
        )
    st.markdown(
        f'<div style="background:linear-gradient(135deg,#0f1420,#0c1018);'
        f'padding:12px 8px;border-radius:10px;'
        f'border:1px solid rgba(255,255,255,0.06);text-align:center;'
        f'min-height:68px;transition:border-color 0.2s;">'
        f'<div style="font-size:0.65rem;color:#8892a4;text-transform:uppercase;'
        f'letter-spacing:0.06em;margin-bottom:3px;">{icon} {label}</div>'
        f'<div style="font-size:1.3rem;font-weight:800;color:{color};'
        f'line-height:1.2;">{value}</div>'
        f'{delta_html}{badge_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def metric_row(cards: list, cols: int = 0):
    """Render a row of metric cards. Auto-sizes columns if cols=0.

    cards: list of dicts with keys: icon, label, value, color, badge, badge_color, delta, delta_color
    """
    n = cols or len(cards)
    columns = st.columns(n)
    for col, card in zip(columns, cards):
        with col:
            metric_card(**card)


def section_header(title: str, color: str = "#00ff88"):
    """Render a styled section header with left accent border."""
    st.markdown(
        f'<div style="font-size:1.05rem;font-weight:700;color:#e8ecf1;'
        f'padding:10px 16px;margin:10px 0 6px 0;'
        f'border:1px solid rgba(255,255,255,0.06);'
        f'border-left:3px solid {color};'
        f'border-radius:0 10px 10px 0;'
        f'background:linear-gradient(90deg,{color}15 0%,#0c1018 100%);'
        f'letter-spacing:0.02em;">'
        f'{title}</div>',
        unsafe_allow_html=True,
    )
