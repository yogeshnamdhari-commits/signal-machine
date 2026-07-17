"""
Premium Market Heatmaps — DeltaTerminal Production-Grade
=========================================================
Advanced visualizations: Treemaps, Smart Money matrices, Multi-TF CVD,
Composite scoring, and real-time price heatmaps with institutional data.
All data sourced from bridge JSON files (real engine output).
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from plotly.subplots import make_subplots

from dashboard.data_bridge import reader as bridge_reader


# ── HELPERS ──
def _fmt(val: float, prefix: str = "", suffix: str = "") -> str:
    """Format large numbers with K/M/B/T suffixes."""
    if val is None: return f"{prefix}0{suffix}"
    a = abs(val)
    if a >= 1e12: return f"{prefix}{val/1e12:.2f}T{suffix}"
    if a >= 1e9: return f"{prefix}{val/1e9:.2f}B{suffix}"
    if a >= 1e6: return f"{prefix}{val/1e6:.2f}M{suffix}"
    if a >= 1e3: return f"{prefix}{val/1e3:.1f}K{suffix}"
    return f"{prefix}{val:.2f}{suffix}"

def _price(v):
    if v is None or v == 0: return "—"
    if v >= 100: return f"{v:,.2f}"
    if v >= 1: return f"{v:.4f}"
    if v >= 0.01: return f"{v:.5f}"
    return f"{v:.6f}"

def _pct(v):
    if v is None: return "0.0%"
    return f"{v:+.1f}%"

def _load_market_df() -> pd.DataFrame:
    """Load market data as DataFrame, sorted by volume."""
    md = bridge_reader.read_market_data()
    if not md:
        return pd.DataFrame()
    df = pd.DataFrame(md)
    for col in ["price", "volume_24h", "change_24h", "open_interest", "funding",
                 "exchange_flow", "net_delta", "cvd_bias", "oi_bias", "funding_bias",
                 "flow_signal", "flow_strength", "buy_sell_ratio", "taker_dominance",
                 "liq_risk", "liq_risk_level", "long_liq_vol", "short_liq_vol",
                 "sweep_detected", "sweep_direction", "regime", "regime_confidence_pct",
                 "change_1h", "change_4h", "vol_bias", "imbalance", "trades_24h",
                 "aggressive_buy_vol", "aggressive_sell_vol", "cvd_5m", "cvd_1h", "cvd_4h",
                 "regime_1m", "regime_5m", "regime_15m", "regime_1h", "regime_4h",
                 "regime_conf_1m", "regime_conf_5m", "regime_conf_15m", "regime_conf_1h", "regime_conf_4h",
                 "oi_change_pct", "funding_z", "institutional_score", "smart_money_score",
                 "regime_alignment", "spread", "absorption_score", "sweep_score",
                 "of_flow_signal", "of_flow_strength", "of_absorption", "long_liq_count", "short_liq_count",
                 "cluster_count", "cascade_active", "sweep_intensity", "high_24h", "low_24h",
                 "open_24h", "mark_price", "index_price", "volume_btc"]:
        if col not in df.columns:
            df[col] = 0 if any(k in col for k in ["score", "risk", "count", "vol", "conf"]) else ""
    if "volume_24h" not in df.columns and "volume" in df.columns:
        df["volume_24h"] = df["volume"]
    return df

def _load_smart_df() -> pd.DataFrame:
    """Load smart money data as DataFrame."""
    try:
        raw = bridge_reader.read_smart_money_map()
        if isinstance(raw, dict):
            raw = raw.get("rows", raw.get("data", []))
        if isinstance(raw, list) and raw:
            return pd.DataFrame(raw)
    except Exception:
        pass
    return pd.DataFrame()


# ═══════════════════════════════════════════════════════════════
# MAIN RENDERER — Called from app.py tab5
# ═══════════════════════════════════════════════════════════════
def render_heatmaps():
    """Render premium market heatmaps with 9 tabs."""
    st.markdown("### 🔥 Premium Market Intelligence")

    t1, t2, t3, t4, t5, t6, t7, t8, t9 = st.tabs([
        "🗺️ Price Treemap",
        "🏛️ Smart Money",
        "📊 Volume + OI Matrix",
        "🌀 Regime Map",
        "💹 Flow + CVD",
        "💥 Liquidation",
        "🔗 Correlation",
        "⚡ Multi-TF Sweep",
        "🎯 Composite Score",
    ])

    with t1: _render_price_treemap()
    with t2: _render_smart_money_matrix()
    with t3: _render_volume_oi_matrix()
    with t4: _render_regime_heatmap()
    with t5: _render_flow_cvd_matrix()
    with t6: _render_liquidation_heatmap()
    with t7: _render_correlation_heatmap()
    with t8: _render_multitf_sweep_matrix()
    with t9: _render_composite_score()


# ═══════════════════════════════════════════════════════════════
# TAB 1 — PRICE TREEMAP (Bloomberg-style)
# ═══════════════════════════════════════════════════════════════
def _render_price_treemap():
    """Treemap: sized by 24h volume, colored by price change, with prices."""
    df = _load_market_df()
    if df.empty:
        st.info("⏳ No market data available.")
        return

    n = st.slider("Symbols to show", 20, 250, 80, key="treemap_n")
    df = df.nlargest(n, "volume_24h").copy()
    df["sym"] = df["symbol"].str.replace("USDT", "")
    df["vol_B"] = df["volume_24h"] / 1e9
    df["chg"] = df["change_24h"].fillna(0).clip(-20, 20)

    df["label"] = df.apply(
        lambda r: f"{r['sym']}<br>${_price(r['price'])}<br>{r['chg']:+.1f}%", axis=1
    )

    total_vol = df["vol_B"].sum()

    fig = go.Figure(go.Treemap(
        labels=df["label"].tolist(),
        parents=[""] * len(df),
        values=df["vol_B"].tolist(),
        marker=dict(
            colors=df["chg"].tolist(),
            colorscale=[
                [0.0, "#dc2626"], [0.3, "#ef4444"], [0.45, "#f97316"],
                [0.5, "#374151"], [0.55, "#22c55e"], [0.7, "#16a34a"], [1.0, "#059669"],
            ],
            cmid=0,
            colorbar=dict(title="24h %", thickness=15, len=0.5),
        ),
        textfont=dict(size=11, color="white"),
        hovertemplate="<b>%{label}</b><br>Volume: $%{value:.2f}B<extra></extra>",
        textinfo="label",
        pathbar=dict(visible=False),
    ))

    fig.update_layout(height=600, template="plotly_dark", margin=dict(l=5, r=5, t=5, b=5))
    st.plotly_chart(fig, width="stretch")

    c1, c2, c3, c4, c5 = st.columns(5)
    gainers = df[df["chg"] > 0]
    losers = df[df["chg"] < 0]
    c1.metric("🟢 Gainers", len(gainers))
    c2.metric("🔴 Losers", len(losers))
    c3.metric("📊 Total Vol", _fmt(total_vol, "$", "B"))
    c4.metric("📈 Avg Change", f"{df['chg'].mean():+.1f}%")
    top = df.nlargest(1, "chg")
    if not top.empty:
        c5.metric("🏆 Top Mover", f"{top.iloc[0]['sym']} {top.iloc[0]['chg']:+.1f}%")


# ═══════════════════════════════════════════════════════════════
# TAB 2 — SMART MONEY MATRIX
# ═══════════════════════════════════════════════════════════════
def _render_smart_money_matrix():
    """Heatmap: Accumulation, Distribution, Institutional Flow, Whale, Absorption."""
    sm_df = _load_smart_df()
    md_df = _load_market_df()

    if sm_df.empty:
        st.info("⏳ No smart money data available.")
        return

    if not md_df.empty and "symbol" in md_df.columns:
        sm_df = sm_df.merge(
            md_df[["symbol", "price", "volume_24h", "change_24h"]],
            on="symbol", how="left", suffixes=("", "_md")
        )

    sm_df = sm_df.sort_values("smart_money_strength", ascending=False).head(30).copy()
    sm_df["sym"] = sm_df["symbol"].str.replace("USDT", "")

    metrics = [
        ("Accum Score", "accumulation_score"),
        ("Distrib Score", "distribution_score"),
        ("Inst Flow", "institutional_flow"),
        ("Whale Conf", "whale_confidence"),
        ("Absorb Score", "absorption_score"),
        ("Stealth Buys", "stealth_buys"),
        ("Stealth Sells", "stealth_sells"),
        ("SM Side", "smart_money_side"),
        ("SM Strength", "smart_money_strength"),
    ]

    data_matrix = []
    cell_text = []
    hover_text = []

    for _, row in sm_df.iterrows():
        sym = row.get("sym", "?")
        price = row.get("price", 0)
        chg = row.get("change_24h", 0)
        r_vals = []
        r_text = []
        r_hover = []

        for label, col in metrics:
            val = row.get(col, 0)
            if isinstance(val, str):
                cat_map = {
                    "accumulating": 0.9, "distributing": 0.1, "neutral": 0.5,
                    "strong_support": 0.8, "strong_resistance": 0.2, "none": 0.5,
                }
                num_val = cat_map.get(str(val).lower(), 0.5)
                r_vals.append(num_val)
                r_text.append(str(val).replace("_", " ").title()[:10])
            elif col == "institutional_flow":
                r_vals.append(abs(float(val or 0)))
                r_text.append(_fmt(val, "$"))
            elif col in ("stealth_buys", "stealth_sells"):
                r_vals.append(min(float(val or 0) / 500, 1.0))
                r_text.append(str(int(val or 0)))
            else:
                v = float(val or 0)
                r_vals.append(min(max(v, 0), 1.0))
                r_text.append(f"{v:.2f}")
            r_hover.append(f"{label}: {r_text[-1]}")

        data_matrix.append(r_vals)
        cell_text.append(r_text)
        hover_text.append([f"{sym} ${_price(price)} ({chg:+.1f}%)"] + r_hover)

    col_labels = [m[0] for m in metrics]
    y_labels = [f"{sm_df.iloc[i]['sym']} {_fmt(sm_df.iloc[i].get('price', 0), '$') if sm_df.iloc[i].get('price', 0) else ''}"
                for i in range(len(sm_df))]

    fig = go.Figure(data=go.Heatmap(
        z=data_matrix, x=col_labels, y=y_labels,
        text=cell_text, texttemplate="%{text}", textfont={"size": 10, "color": "white"},
        hovertext=hover_text, hovertemplate="%{hovertext}<extra></extra>",
        colorscale=[
            [0, "#1a0a0a"], [0.2, "#7f1d1d"], [0.35, "#b45309"],
            [0.5, "#374151"], [0.65, "#065f46"], [0.8, "#047857"], [1.0, "#10b981"],
        ],
        colorbar=dict(title="Score", thickness=12),
    ))
    fig.update_layout(
        height=max(400, len(sm_df) * 30 + 60), template="plotly_dark",
        margin=dict(l=10, r=10, t=30, b=30),
        xaxis=dict(tickfont=dict(size=10)), yaxis=dict(tickfont=dict(size=9)),
    )
    st.plotly_chart(fig, width="stretch")

    n_accum = sum(1 for _, r in sm_df.iterrows() if str(r.get("smart_money_side", "")).lower() == "accumulating")
    n_distrib = sum(1 for _, r in sm_df.iterrows() if str(r.get("smart_money_side", "")).lower() == "distributing")
    total_flow = sm_df["institutional_flow"].sum() if "institutional_flow" in sm_df.columns else 0
    st.markdown(f"🏛️ **Accumulating:** {n_accum} | **Distributing:** {n_distrib} | **Total Inst Flow:** {_fmt(total_flow, '$')}")


# ═══════════════════════════════════════════════════════════════
# TAB 3 — VOLUME + OI + FUNDING MATRIX
# ═══════════════════════════════════════════════════════════════
def _render_volume_oi_matrix():
    """Heatmap: Volume, OI, OI Change, Funding, Z-Score, Spread, 24h/1h Change."""
    df = _load_market_df()
    if df.empty:
        st.info("⏳ No market data available.")
        return

    n = st.slider("Top symbols", 10, 100, 30, key="vol_oi_n")
    df = df.nlargest(n, "volume_24h").copy()
    df["sym"] = df["symbol"].str.replace("USDT", "")

    metrics = [
        ("24h Vol", "volume_24h"), ("OI", "open_interest"), ("OI Δ%", "oi_change_pct"),
        ("Funding", "funding"), ("Fund Z", "funding_z"), ("Spread", "spread"),
        ("24h Chg%", "change_24h"), ("1h Chg%", "change_1h"), ("Trades", "trades_24h"),
    ]

    data_matrix = []
    cell_text = []
    hover_text = []

    for _, row in df.iterrows():
        sym = row.get("sym", "?")
        price = row.get("price", 0)
        r_vals = []
        r_text = []
        r_hover = []

        for label, col in metrics:
            val = float(row.get(col, 0) or 0)
            if "Vol" in label or col == "open_interest" or col == "trades_24h":
                max_v = df[col].max() if col in df.columns else 1
                norm = val / max_v if max_v > 0 else 0
            elif "Chg" in label or "Δ" in label:
                norm = (val + 10) / 20
            elif col == "funding":
                norm = (val + 0.01) / 0.02
            elif "Fund Z" in label:
                norm = (val + 3) / 6
            elif "Spread" in label:
                max_v = df[col].max() if col in df.columns else 1
                norm = val / max_v if max_v > 0 else 0
            else:
                norm = 0.5
            norm = max(0, min(1, norm))
            r_vals.append(norm)

            if "Vol" in label: r_text.append(_fmt(val, "$"))
            elif col == "open_interest": r_text.append(_fmt(val))
            elif "Δ" in label: r_text.append(f"{val:+.2f}%")
            elif col == "funding": r_text.append(f"{val:.4f}%")
            elif "Fund Z" in label: r_text.append(f"{val:.2f}")
            elif "Spread" in label: r_text.append(f"{val:.5f}")
            elif "Chg" in label: r_text.append(f"{val:+.1f}%")
            elif "Trades" in label: r_text.append(f"{int(val):,}")
            else: r_text.append(f"{val:.2f}")
            r_hover.append(f"{label}: {r_text[-1]}")

        data_matrix.append(r_vals)
        cell_text.append(r_text)
        hover_text.append([f"{sym} ${_price(price)}"] + r_hover)

    col_labels = [m[0] for m in metrics]
    y_labels = [f"{df.iloc[i]['sym']} {_fmt(df.iloc[i].get('price', 0), '$') if df.iloc[i].get('price', 0) else ''}"
                for i in range(len(df))]

    fig = go.Figure(data=go.Heatmap(
        z=data_matrix, x=col_labels, y=y_labels,
        text=cell_text, texttemplate="%{text}", textfont={"size": 10, "color": "white"},
        hovertext=hover_text, hovertemplate="%{hovertext}<extra></extra>",
        colorscale="RdYlGn", colorbar=dict(title="Normalized", thickness=12),
    ))
    fig.update_layout(
        height=max(400, len(df) * 28 + 60), template="plotly_dark",
        margin=dict(l=10, r=10, t=30, b=30),
        xaxis=dict(tickfont=dict(size=10)), yaxis=dict(tickfont=dict(size=9)),
    )
    st.plotly_chart(fig, width="stretch")

    total_vol = df["volume_24h"].sum()
    total_oi = df["open_interest"].sum()
    avg_fund = df["funding"].mean() if "funding" in df.columns else 0
    st.caption(f"📊 **Total Vol:** {_fmt(total_vol, '$')} | **Total OI:** {_fmt(total_oi)} | **Avg Funding:** {avg_fund:.4f}%")


# ═══════════════════════════════════════════════════════════════
# TAB 4 — REGIME MAP (Multi-TF)
# ═══════════════════════════════════════════════════════════════
def _render_regime_heatmap():
    """Multi-timeframe regime heatmap with confidence."""
    df = _load_market_df()
    if df.empty:
        st.info("⏳ No market data available.")
        return

    n = st.slider("Top symbols", 10, 60, 25, key="regime_n")
    df = df.nlargest(n, "volume_24h").copy()
    df["sym"] = df["symbol"].str.replace("USDT", "")

    regime_values = {
        "trending_bull": 1.0, "breakout": 0.85, "compression": 0.4,
        "range": 0.0, "volatile": -0.3, "trending_bear": -1.0,
        "trending_up": 1.0, "trending_down": -1.0,
        "ranging": 0.0, "quiet": -0.15, "reversal": 0.3,
    }
    regime_icons = {
        "trending_bull": "📈", "trending_bear": "📉", "range": "↔️",
        "volatile": "⚡", "breakout": "🚀", "compression": "🔍",
    }

    timeframes = ["1m", "5m", "15m", "1h", "4h"]
    data_values = []
    data_text = []
    data_hover = []

    for _, row in df.iterrows():
        sym = row.get("sym", "?")
        price = row.get("price", 0)
        row_vals = []
        row_text = []
        row_hover = []

        for tf in timeframes:
            regime_key = f"regime_{tf}"
            conf_key = f"regime_conf_{tf}"
            regime_val = str(row.get(regime_key, "") or row.get("regime", "range"))
            conf_val = float(row.get(conf_key, 0) or row.get("regime_confidence_pct", 50) / 100)

            if regime_val in regime_values:
                val = regime_values[regime_val]
                icon = regime_icons.get(regime_val, "")
                label = regime_val.replace("_", " ").title()
                row_vals.append(val)
                row_text.append(f"{icon} {label[:8]}")
                row_hover.append(f"{sym} {_fmt(price, '$')} | {tf}\nRegime: {label}\nConf: {conf_val*100:.0f}%")
            else:
                row_vals.append(0)
                row_text.append("—")
                row_hover.append(f"{sym} | {tf}: N/A")

        data_values.append(row_vals)
        data_text.append(row_text)
        data_hover.append(row_hover)

    y_labels = [f"{df.iloc[i]['sym']} {_fmt(df.iloc[i].get('price', 0), '$') if df.iloc[i].get('price', 0) else ''}"
                for i in range(len(df))]

    fig = go.Figure(data=go.Heatmap(
        z=data_values, x=timeframes, y=y_labels,
        text=data_text, texttemplate="%{text}", textfont={"size": 11, "color": "white"},
        hovertext=data_hover, hovertemplate="%{hovertext}<extra></extra>",
        colorscale=[
            [0, "#dc2626"], [0.3, "#f97316"], [0.5, "#eab308"],
            [0.7, "#84cc16"], [0.85, "#22c55e"], [1.0, "#06b6d4"],
        ],
        zmin=-1, zmax=1,
        colorbar=dict(title="Regime", thickness=12, len=0.5,
            tickvals=[-1, -0.3, 0, 0.4, 0.85, 1.0],
            ticktext=["Bear", "Volatile", "Range", "Compress", "Break", "Bull"]),
    ))
    fig.update_layout(
        height=max(400, len(df) * 30 + 60), template="plotly_dark",
        margin=dict(l=10, r=10, t=30, b=30),
        xaxis=dict(tickfont=dict(size=10)), yaxis=dict(tickfont=dict(size=9)),
    )
    st.plotly_chart(fig, width="stretch")

    if "regime_alignment" in df.columns:
        avg_align = df["regime_alignment"].mean()
        aligned = sum(1 for _, r in df.iterrows() if float(r.get("regime_alignment", 0) or 0) >= 0.6)
        st.caption(f"🎯 **Avg Alignment:** {avg_align:.0%} | **Aligned (≥60%):** {aligned}/{len(df)}")


# ═══════════════════════════════════════════════════════════════
# TAB 5 — FLOW + CVD MATRIX
# ═══════════════════════════════════════════════════════════════
def _render_flow_cvd_matrix():
    """Heatmap: Flow signal, CVD multi-TF, taker dominance, exchange flow."""
    df = _load_market_df()
    if df.empty:
        st.info("⏳ No market data available.")
        return

    n = st.slider("Top symbols", 10, 60, 30, key="flow_cvd_n")
    df = df.nlargest(n, "volume_24h").copy()
    df["sym"] = df["symbol"].str.replace("USDT", "")

    signal_map = {"strong_buy": 1.0, "buy": 0.75, "neutral": 0.5, "sell": 0.25, "strong_sell": 0.0}
    bias_map = {"strong_bullish": 1.0, "bullish": 0.75, "neutral": 0.5, "bearish": 0.25, "strong_bearish": 0.0,
                "buy": 0.75, "sell": 0.25, "taker_buy": 0.75, "taker_sell": 0.25}

    metrics = [
        ("Flow Sig", "flow_signal", signal_map), ("Flow Str", "flow_strength", None),
        ("CVD 5m", "cvd_5m", None), ("CVD 1h", "cvd_1h", None), ("CVD 4h", "cvd_4h", None),
        ("CVD Bias", "cvd_bias", bias_map), ("Taker Dom", "taker_dominance", None),
        ("Ex Flow", "exchange_flow", None), ("B/S Ratio", "buy_sell_ratio", None),
        ("OI Bias", "oi_bias", bias_map), ("Vol Bias", "vol_bias", bias_map),
    ]

    data_matrix = []
    cell_text = []
    hover_text = []

    for _, row in df.iterrows():
        sym = row.get("sym", "?")
        price = row.get("price", 0)
        r_vals = []
        r_text = []
        r_hover = []

        for label, col, cat_map in metrics:
            val = row.get(col, 0)
            if cat_map and isinstance(val, str):
                norm = cat_map.get(str(val).lower(), 0.5)
                r_text.append(str(val).replace("_", " ").title()[:10])
            elif col in ("cvd_5m", "cvd_1h", "cvd_4h"):
                v = float(val or 0)
                max_cvd = max(abs(df[col].max()) if col in df.columns else 1, abs(df[col].min()) if col in df.columns else 1, 1)
                norm = (v / max_cvd + 1) / 2
                r_text.append(_fmt(v))
            elif col == "exchange_flow":
                v = float(val or 0)
                max_ef = max(abs(df[col].max()) if col in df.columns else 1, abs(df[col].min()) if col in df.columns else 1, 1)
                norm = (v / max_ef + 1) / 2
                r_text.append(_fmt(v, "$"))
            elif col == "flow_strength":
                norm = float(val or 0) / 100
                r_text.append(f"{float(val or 0):.0f}")
            elif col == "taker_dominance":
                norm = float(val or 0.5)
                r_text.append(f"{float(val or 0):.1%}")
            elif col == "buy_sell_ratio":
                norm = float(val or 0.5)
                r_text.append(f"{float(val or 0):.2f}")
            else:
                norm = 0.5
                r_text.append(str(val)[:8] if val else "—")
            norm = max(0, min(1, norm))
            r_vals.append(norm)
            r_hover.append(f"{label}: {r_text[-1]}")

        data_matrix.append(r_vals)
        cell_text.append(r_text)
        hover_text.append([f"{sym} ${_price(price)}"] + r_hover)

    col_labels = [m[0] for m in metrics]
    y_labels = [f"{df.iloc[i]['sym']} {_fmt(df.iloc[i].get('price', 0), '$') if df.iloc[i].get('price', 0) else ''}"
                for i in range(len(df))]

    fig = go.Figure(data=go.Heatmap(
        z=data_matrix, x=col_labels, y=y_labels,
        text=cell_text, texttemplate="%{text}", textfont={"size": 9, "color": "white"},
        hovertext=hover_text, hovertemplate="%{hovertext}<extra></extra>",
        colorscale=[[0, "#dc2626"], [0.25, "#ef4444"], [0.5, "#374151"], [0.75, "#22c55e"], [1.0, "#059669"]],
        colorbar=dict(title="Signal", thickness=12),
    ))
    fig.update_layout(
        height=max(400, len(df) * 26 + 60), template="plotly_dark",
        margin=dict(l=10, r=10, t=30, b=30),
        xaxis=dict(tickfont=dict(size=10)), yaxis=dict(tickfont=dict(size=9)),
    )
    st.plotly_chart(fig, width="stretch")

    if "flow_signal" in df.columns:
        buy_n = sum(1 for v in df["flow_signal"] if str(v).lower() in ("buy", "strong_buy"))
        sell_n = sum(1 for v in df["flow_signal"] if str(v).lower() in ("sell", "strong_sell"))
        st.caption(f"💹 **Buy Flow:** {buy_n} | **Sell Flow:** {sell_n} | **Net:** {'🟢 Buyers' if buy_n > sell_n else '🔴 Sellers'}")


# ═══════════════════════════════════════════════════════════════
# TAB 6 — LIQUIDATION HEATMAP
# ═══════════════════════════════════════════════════════════════
def _render_liquidation_heatmap():
    """Liquidation risk heatmap with cascades and sweep data."""
    df = _load_market_df()
    if df.empty:
        st.info("⏳ No market data available.")
        return

    df = df.sort_values("liq_risk", ascending=False).head(30).copy()
    df["sym"] = df["symbol"].str.replace("USDT", "")

    metrics = [
        ("Liq Risk", "liq_risk"), ("Long Liq $", "long_liq_vol"), ("Short Liq $", "short_liq_vol"),
        ("Long Cnt", "long_liq_count"), ("Short Cnt", "short_liq_count"),
        ("Clusters", "cluster_count"), ("Cascade", "cascade_active"),
        ("Sweep", "sweep_detected"), ("Sweep Dir", "sweep_direction"), ("Sweep Int", "sweep_intensity"),
    ]

    data_matrix = []
    cell_text = []
    hover_text = []

    for _, row in df.iterrows():
        sym = row.get("sym", "?")
        price = row.get("price", 0)
        risk_level = row.get("liq_risk_level", "low")
        r_vals = []
        r_text = []
        r_hover = []

        for label, col in metrics:
            val = row.get(col, 0)
            if label == "Liq Risk":
                norm = float(val or 0) / 100
                r_text.append(f"{float(val or 0):.0f}")
            elif label in ("Long Liq $", "Short Liq $"):
                v = float(val or 0)
                max_v = max(float(row.get("long_liq_vol", 1) or 1), float(row.get("short_liq_vol", 1) or 1))
                norm = v / max_v if max_v > 0 else 0
                r_text.append(_fmt(v, "$"))
            elif "Cnt" in label:
                norm = min(float(val or 0) / 100, 1.0)
                r_text.append(str(int(val or 0)))
            elif "Clusters" in label:
                norm = min(float(val or 0) / 10, 1.0)
                r_text.append(str(int(val or 0)))
            elif label == "Cascade":
                norm = 1.0 if val else 0.0
                r_text.append("Yes" if val else "No")
            elif label == "Sweep":
                norm = 1.0 if val else 0.0
                r_text.append("Yes" if val else "No")
            elif label == "Sweep Dir":
                dm = {"up": 1.0, "down": 0.0}
                norm = dm.get(str(val).lower(), 0.5)
                r_text.append(str(val)[:4] if val else "—")
            elif label == "Sweep Int":
                norm = float(val or 0)
                r_text.append(f"{float(val or 0):.1f}")
            else:
                norm = 0.5
                r_text.append(str(val))
            r_vals.append(norm)
            r_hover.append(f"{label}: {r_text[-1]}")

        data_matrix.append(r_vals)
        cell_text.append(r_text)
        hover_text.append([f"{sym} ${_price(price)} | {risk_level.upper()} RISK"] + r_hover)

    col_labels = [m[0] for m in metrics]
    y_labels = [f"{df.iloc[i]['sym']} {_fmt(df.iloc[i].get('price', 0), '$') if df.iloc[i].get('price', 0) else ''} {'🔴' if df.iloc[i].get('liq_risk_level')=='high' else '🟡' if df.iloc[i].get('liq_risk_level')=='medium' else '🟢'}"
                for i in range(len(df))]

    fig = go.Figure(data=go.Heatmap(
        z=data_matrix, x=col_labels, y=y_labels,
        text=cell_text, texttemplate="%{text}", textfont={"size": 10, "color": "white"},
        hovertext=hover_text, hovertemplate="%{hovertext}<extra></extra>",
        colorscale=[[0, "#0a2e1a"], [0.3, "#22c55e"], [0.5, "#f59e0b"], [0.7, "#ff6b35"], [1.0, "#ff2222"]],
        colorbar=dict(title="Risk", thickness=12),
    ))
    fig.update_layout(
        height=max(400, len(df) * 28 + 60), template="plotly_dark",
        margin=dict(l=10, r=10, t=30, b=30),
        xaxis=dict(tickfont=dict(size=10)), yaxis=dict(tickfont=dict(size=9)),
    )
    st.plotly_chart(fig, width="stretch")

    n_high = sum(1 for _, r in df.iterrows() if r.get("liq_risk_level") == "high")
    n_sweep = sum(1 for _, r in df.iterrows() if r.get("sweep_detected"))
    total_liq = df["long_liq_vol"].sum() + df["short_liq_vol"].sum()
    st.markdown(f"💥 **High Risk:** {n_high} | **Active Sweeps:** {n_sweep} | **Total Liq Exposure:** {_fmt(total_liq, '$')}")


# ═══════════════════════════════════════════════════════════════
# TAB 7 — CORRELATION HEATMAP
# ═══════════════════════════════════════════════════════════════
def _render_correlation_heatmap():
    """Cross-symbol correlation from OI, funding, volume, flow."""
    df = _load_market_df()
    if df.empty:
        st.info("⏳ No market data available.")
        return

    n = st.slider("Top symbols", 5, 30, 15, key="corr_n")
    df = df.nlargest(n, "volume_24h").copy()
    df["sym"] = df["symbol"].str.replace("USDT", "")
    prices = df["price"].values
    symbols = df["sym"].tolist()
    n = len(symbols)

    if n < 2:
        st.info("Need at least 2 symbols.")
        return

    features = df[["open_interest", "funding", "volume_24h", "exchange_flow", "net_delta"]].fillna(0).values.astype(float)
    for col in range(features.shape[1]):
        std = features[:, col].std()
        if std > 0:
            features[:, col] = (features[:, col] - features[:, col].mean()) / std

    corr = np.corrcoef(features)
    corr = np.nan_to_num(corr, nan=0.0)
    corr = np.clip(corr, -1, 1)

    labels = [f"{symbols[i]} {_fmt(prices[i], '$')}" for i in range(n)]

    fig = go.Figure(data=go.Heatmap(
        z=corr, x=labels, y=labels,
        colorscale=[[0, "#ff4444"], [0.25, "#ff8800"], [0.5, "#ffff00"], [0.75, "#88ff00"], [1.0, "#00ff88"]],
        text=np.round(corr, 2), texttemplate="%{text}", textfont={"size": 10},
        hovertemplate="%{x} / %{y}: %{z:.3f}<extra></extra>",
        colorbar=dict(title="ρ", thickness=12),
    ))
    fig.update_layout(
        height=max(400, n * 32 + 60), template="plotly_dark",
        margin=dict(l=10, r=10, t=30, b=30),
        xaxis=dict(tickfont=dict(size=9)), yaxis=dict(tickfont=dict(size=9)),
    )
    st.plotly_chart(fig, width="stretch")

    high = [(symbols[i], symbols[j], corr[i, j]) for i in range(n) for j in range(i+1, n) if corr[i, j] > 0.8]
    if high:
        pairs = ", ".join(f"{a}/{b} ({v:.2f})" for a, b, v in high[:5])
        st.info(f"🔗 **High Correlation:** {pairs}")


# ═══════════════════════════════════════════════════════════════
# TAB 8 — MULTI-TF SWEEP MATRIX
# ═══════════════════════════════════════════════════════════════
def _render_multitf_sweep_matrix():
    """Multi-timeframe CVD bias + sweep detection + orderflow signals."""
    df = _load_market_df()
    if df.empty:
        st.info("⏳ No market data available.")
        return

    n = st.slider("Top symbols", 10, 50, 25, key="sweep_n")
    df = df.nlargest(n, "volume_24h").copy()
    df["sym"] = df["symbol"].str.replace("USDT", "")

    bias_map = {"strong_bullish": 1.0, "bullish": 0.75, "neutral": 0.5, "bearish": 0.25, "strong_bearish": 0.0}

    metrics = [
        ("CVD 1m", "cvd_bias_1m", bias_map), ("CVD 5m", "cvd_bias_5m", bias_map),
        ("CVD 15m", "cvd_bias_15m", bias_map), ("CVD 1h", "cvd_bias_1h", bias_map),
        ("CVD 4h", "cvd_bias_4h", bias_map), ("OF Sweep", "of_sweep", None),
        ("OF Absorb", "of_absorption", None), ("OF Signal", "of_flow_signal", None),
        ("OF Strength", "of_flow_strength", None), ("Sweep Score", "sweep_score", None),
        ("Imbalance", "imbalance", None),
    ]

    data_matrix = []
    cell_text = []
    hover_text = []

    for _, row in df.iterrows():
        sym = row.get("sym", "?")
        price = row.get("price", 0)
        r_vals = []
        r_text = []
        r_hover = []

        for label, col, cat_map in metrics:
            val = row.get(col, 0)
            if cat_map and isinstance(val, str):
                norm = cat_map.get(str(val).lower(), 0.5)
                r_text.append(str(val).replace("_", " ").title()[:10])
            elif isinstance(val, str):
                if "sweep" in str(val).lower() and val: norm = 0.8
                elif "absorption" in str(val).lower() and val: norm = 0.7
                elif "buy" in str(val).lower(): norm = 0.75
                elif "sell" in str(val).lower(): norm = 0.25
                else: norm = 0.5
                r_text.append(str(val).replace("_", " ").title()[:10] if val else "—")
            elif col == "imbalance":
                v = float(val or 0)
                norm = (v + 1) / 2
                r_text.append(f"{v:.3f}")
            elif col == "sweep_score":
                v = float(val or 0)
                norm = min(v / 100, 1.0) if v >= 0 else 0
                r_text.append(f"{v:.0f}")
            elif col == "of_flow_strength":
                norm = float(val or 0) / 100
                r_text.append(f"{float(val or 0):.1f}")
            else:
                norm = 0.5
                r_text.append(str(val)[:8] if val else "—")
            norm = max(0, min(1, norm))
            r_vals.append(norm)
            r_hover.append(f"{label}: {r_text[-1]}")

        data_matrix.append(r_vals)
        cell_text.append(r_text)
        hover_text.append([f"{sym} ${_price(price)}"] + r_hover)

    col_labels = [m[0] for m in metrics]
    y_labels = [f"{df.iloc[i]['sym']} {_fmt(df.iloc[i].get('price', 0), '$') if df.iloc[i].get('price', 0) else ''}"
                for i in range(len(df))]

    fig = go.Figure(data=go.Heatmap(
        z=data_matrix, x=col_labels, y=y_labels,
        text=cell_text, texttemplate="%{text}", textfont={"size": 9, "color": "white"},
        hovertext=hover_text, hovertemplate="%{hovertext}<extra></extra>",
        colorscale=[[0, "#dc2626"], [0.25, "#ef4444"], [0.5, "#374151"], [0.75, "#22c55e"], [1.0, "#059669"]],
        colorbar=dict(title="Signal", thickness=12),
    ))
    fig.update_layout(
        height=max(400, len(df) * 26 + 60), template="plotly_dark",
        margin=dict(l=10, r=10, t=30, b=30),
        xaxis=dict(tickfont=dict(size=10)), yaxis=dict(tickfont=dict(size=9)),
    )
    st.plotly_chart(fig, width="stretch")


# ═══════════════════════════════════════════════════════════════
# TAB 9 — COMPOSITE SCORE (All-in-One)
# ═══════════════════════════════════════════════════════════════
def _render_composite_score():
    """Combined institutional scoring: regime + flow + smart money + signal."""
    df = _load_market_df()
    sm_df = _load_smart_df()

    if df.empty:
        st.info("⏳ No market data available.")
        return

    n = st.slider("Top symbols", 10, 50, 30, key="comp_n")
    df = df.nlargest(n, "volume_24h").copy()
    df["sym"] = df["symbol"].str.replace("USDT", "")

    if not sm_df.empty and "symbol" in sm_df.columns:
        sm_cols = ["symbol", "accumulation_score", "distribution_score", "institutional_flow",
                    "smart_money_score", "smart_money_side", "whale_confidence", "absorption_score"]
        available = [c for c in sm_cols if c in sm_df.columns]
        df = df.merge(sm_df[available], on="symbol", how="left", suffixes=("", "_sm"))

    def _score(row):
        scores = {}
        rc = float(row.get("regime_confidence_pct", 0) or 0)
        scores["Regime"] = min(rc, 100)
        scores["Volume"] = 50
        oi_chg = float(row.get("oi_change_pct", 0) or 0)
        scores["OI Trend"] = min(max((oi_chg + 10) / 20 * 100, 0), 100)
        flow_str = float(row.get("flow_strength", 0) or 0)
        scores["Flow"] = flow_str
        sm_score = float(row.get("smart_money_score", 0) or row.get("accumulation_score", 0) or 0)
        scores["Smart Money"] = min(sm_score * 100, 100)
        whale = float(row.get("whale_confidence", 0) or 0)
        scores["Whale"] = whale * 100
        weights = {"Regime": 0.20, "Volume": 0.15, "OI Trend": 0.15, "Flow": 0.15, "Smart Money": 0.20, "Whale": 0.15}
        composite = sum(scores[k] * weights[k] for k in scores)
        return scores, composite

    all_scores = []
    composites = []
    for _, row in df.iterrows():
        sc, comp = _score(row)
        all_scores.append(sc)
        composites.append(comp)

    df["composite"] = composites
    df["_scores"] = all_scores
    df = df.sort_values("composite", ascending=False).copy()

    score_labels = ["Regime", "Volume", "OI Trend", "Flow", "Smart Money", "Whale"]

    data_matrix = []
    cell_text = []
    hover_text = []

    for idx, (_, row) in enumerate(df.iterrows()):
        sym = row.get("sym", "?")
        price = row.get("price", 0)
        comp = row["composite"]
        sc = row["_scores"]
        sm_side = row.get("smart_money_side", "—")

        r_vals = []
        r_text = []
        r_hover = [f"{sym} ${_price(price)} | Composite: {comp:.0f}/100 | SM: {sm_side}"]

        for label in score_labels:
            v = sc.get(label, 50)
            if label == "Volume":
                v = min(float(row.get("volume_24h", 0) or 0) / (df["volume_24h"].max() if "volume_24h" in df.columns else 1) * 100, 100)
            r_vals.append(v / 100)
            r_text.append(f"{v:.0f}")
            r_hover.append(f"{label}: {v:.0f}")

        r_vals.append(comp / 100)
        r_text.append(f"{comp:.0f}")
        r_hover.append(f"Composite: {comp:.0f}")

        data_matrix.append(r_vals)
        cell_text.append(r_text)
        hover_text.append(r_hover)

    col_labels = score_labels + ["⭐ Composite"]
    y_labels = [f"{'🥇' if i==0 else '🥈' if i==1 else '🥉' if i==2 else '  '}{df.iloc[i]['sym']} {_fmt(df.iloc[i].get('price', 0), '$') if df.iloc[i].get('price', 0) else ''}"
                for i in range(len(df))]

    fig = go.Figure(data=go.Heatmap(
        z=data_matrix, x=col_labels, y=y_labels,
        text=cell_text, texttemplate="%{text}", textfont={"size": 10, "color": "white"},
        hovertext=hover_text, hovertemplate="%{hovertext}<extra></extra>",
        colorscale=[
            [0, "#1a0a2e"], [0.2, "#7f1d1d"], [0.35, "#b45309"],
            [0.5, "#374151"], [0.65, "#065f46"], [0.8, "#047857"], [1.0, "#10b981"],
        ],
        colorbar=dict(title="Score", thickness=12),
    ))
    fig.update_layout(
        height=max(400, len(df) * 28 + 60), template="plotly_dark",
        margin=dict(l=10, r=10, t=30, b=30),
        xaxis=dict(tickfont=dict(size=10)), yaxis=dict(tickfont=dict(size=9)),
    )
    st.plotly_chart(fig, width="stretch")

    top3 = df.head(3)
    if not top3.empty:
        cols = st.columns(3)
        for i, (_, row) in enumerate(top3.iterrows()):
            with cols[i]:
                sym = row.get("sym", "?")
                comp = row["composite"]
                sc = row["_scores"]
                sm = row.get("smart_money_side", "—")
                emoji = ["🥇", "🥈", "🥉"][i]
                side_icon = "🟢" if "accum" in str(sm).lower() else "🔴" if "distrib" in str(sm).lower() else "⚪"
                st.markdown(
                    f"<div style='background:#161b22;border:1px solid #30363d;border-radius:8px;padding:12px;text-align:center'>"
                    f"<div style='font-size:1.5rem'>{emoji}</div>"
                    f"<div style='font-size:1.2rem;font-weight:bold;color:#58a6ff'>{sym}</div>"
                    f"<div style='font-size:0.9rem;color:#8b949e'>${_price(row.get('price', 0))}</div>"
                    f"<div style='font-size:1.8rem;font-weight:bold;color:#3fb950;margin:4px 0'>{comp:.0f}</div>"
                    f"<div style='font-size:0.7rem;color:#8b949e'>COMPOSITE SCORE</div>"
                    f"<div style='font-size:0.8rem;margin-top:4px'>{side_icon} {str(sm).title()}</div>"
                    f"<div style='font-size:0.65rem;color:#666;margin-top:4px'>"
                    f"R:{sc.get('Regime',0):.0f} V:{sc.get('Volume',0):.0f} O:{sc.get('OI Trend',0):.0f} "
                    f"F:{sc.get('Flow',0):.0f} S:{sc.get('Smart Money',0):.0f} W:{sc.get('Whale',0):.0f}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
