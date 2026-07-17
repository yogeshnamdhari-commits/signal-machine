"""
Live Metrics Panel — real-time metrics display with charts, gauges, and sparklines.
Uses REAL data from the bridge (metrics.json, equity_history.json).
Falls back to demo data only when bridge data is unavailable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from dashboard.data_bridge import reader as bridge_reader


@dataclass
class LiveMetrics:
    """Real-time metrics snapshot."""
    timestamp: float
    portfolio_value: float
    daily_pnl: float
    total_pnl: float
    open_positions: int
    active_signals: int
    win_rate: float
    sharpe_ratio: float
    max_drawdown: float
    current_drawdown: float
    trades_today: int
    win_streak: int
    loss_streak: int
    # System metrics
    uptime_sec: float
    symbols_scanned: int
    ws_connected: bool
    cpu_usage: float = 0
    memory_usage: float = 0


class LiveMetricsPanel:
    """
    Live metrics panel with:
    - Real-time metric cards with deltas
    - Equity curve chart (live updating)
    - Drawdown gauge
    - Performance sparklines
    - System health indicators
    - Auto-refresh support
    """

    def __init__(self) -> None:
        self._metrics_history: List[LiveMetrics] = []
        self._init_session()

    def _init_session(self) -> None:
        if "live_metrics_history" not in st.session_state:
            st.session_state.live_metrics_history = []
        if "equity_curve_live" not in st.session_state:
            st.session_state.equity_curve_live = [10000]

    def update(self, metrics: LiveMetrics) -> None:
        """Update with new metrics snapshot."""
        self._metrics_history.append(metrics)
        st.session_state.live_metrics_history = self._metrics_history[-500:]
        st.session_state.equity_curve_live.append(metrics.portfolio_value)

    # ── Main Render ──────────────────────────────────────────────

    def render(self) -> None:
        """Render the live metrics panel."""
        metrics = self._get_current_metrics()

        # Top row: Key metrics
        self._render_key_metrics(metrics)

        # Middle row: Charts
        col_chart, col_gauge = st.columns([3, 1])

        with col_chart:
            self._render_equity_chart()

        with col_gauge:
            self._render_drawdown_gauge(metrics)
            self._render_system_health(metrics)

        # Bottom row: Sparklines
        self._render_sparklines(metrics)

    def _get_current_metrics(self) -> LiveMetrics:
        """Get current metrics from bridge (real data) or fallback to demo."""
        # Try to read real metrics from bridge
        bridge_metrics = bridge_reader.read_metrics()
        status = bridge_reader.read_status()

        if bridge_metrics:
            # Build LiveMetrics from real bridge data
            equity_history = bridge_reader.read_equity_history()
            current_drawdown = 0.0
            if equity_history:
                equities = [h.get("equity", 10000) for h in equity_history]
                if len(equities) >= 2:
                    peak = max(equities)
                    current = equities[-1]
                    current_drawdown = ((peak - current) / peak * 100) if peak > 0 else 0

            return LiveMetrics(
                timestamp=time.time(),
                portfolio_value=bridge_metrics.get("portfolio_value", 10000),
                daily_pnl=bridge_metrics.get("daily_pnl", 0),
                total_pnl=bridge_metrics.get("total_pnl", 0),
                open_positions=bridge_metrics.get("open_positions", 0),
                active_signals=bridge_metrics.get("symbols_scanned", 0),
                win_rate=bridge_metrics.get("win_rate", 0),
                sharpe_ratio=bridge_metrics.get("sharpe_ratio", 0),
                max_drawdown=bridge_metrics.get("max_drawdown", 0),
                current_drawdown=current_drawdown,
                trades_today=bridge_metrics.get("trades_today", 0),
                win_streak=0,
                loss_streak=0,
                uptime_sec=status.uptime if status else 0,
                symbols_scanned=bridge_metrics.get("symbols_scanned", 0),
                ws_connected=status.ws_connected if status else False,
            )

        if self._metrics_history:
            return self._metrics_history[-1]

        # Last resort demo data
        return LiveMetrics(
            timestamp=time.time(),
            portfolio_value=10240.50,
            daily_pnl=245.30,
            total_pnl=2450.80,
            open_positions=3,
            active_signals=7,
            win_rate=64.2,
            sharpe_ratio=1.85,
            max_drawdown=3.2,
            current_drawdown=1.1,
            trades_today=12,
            win_streak=4,
            loss_streak=1,
            uptime_sec=43200,
            symbols_scanned=85,
            ws_connected=True,
        )

    # ── Key Metrics Row ──────────────────────────────────────────

    def _render_key_metrics(self, m: LiveMetrics) -> None:
        """Render top-level metric cards."""
        cols = st.columns(8)

        metrics_data = [
            ("💰 Portfolio", f"${m.portfolio_value:,.2f}", f"{m.total_pnl:+,.2f}", "normal"),
            ("📈 Daily PnL", f"${m.daily_pnl:+,.2f}", None, "normal"),
            ("🎯 Win Rate", f"{m.win_rate:.1f}%", None, "normal"),
            ("⚡ Sharpe", f"{m.sharpe_ratio:.2f}", None, "normal"),
            ("📉 Drawdown", f"{m.current_drawdown:.1f}%", f"Max: {m.max_drawdown:.1f}%", "inverse"),
            ("🔄 Trades", f"{m.trades_today}", None, "normal"),
            ("🔥 Streak", f"W:{m.win_streak} L:{m.loss_streak}", None, "normal"),
            ("🏢 Positions", f"{m.open_positions}", None, "normal"),
        ]

        for col, (label, value, delta, delta_type) in zip(cols, metrics_data):
            with col:
                st.metric(label, value, delta=delta, delta_color=delta_type)

    # ── Equity Chart ─────────────────────────────────────────────

    def _render_equity_chart(self) -> None:
        """Render live equity curve chart from bridge data."""
        # Try bridge equity history first
        equity_history = bridge_reader.read_equity_history()
        if equity_history:
            equity = [h.get("equity", 10000) for h in equity_history]
        else:
            equity = st.session_state.equity_curve_live

        if len(equity) < 2:
            st.info("Accumulating equity data...")
            return

        fig = go.Figure()

        # Equity line
        fig.add_trace(go.Scatter(
            y=equity,
            mode="lines",
            name="Equity",
            line=dict(color="#00ff88", width=2),
            fill="tozeroy",
            fillcolor="rgba(0,255,136,0.1)",
        ))

        # Initial capital line
        fig.add_hline(y=equity[0], line_dash="dash", line_color="#666", annotation_text="Initial")

        fig.update_layout(
            title="📈 Live Equity Curve",
            height=300,
            template="plotly_dark",
            margin=dict(l=10, r=10, t=40, b=10),
            showlegend=False,
            xaxis_title="",
            yaxis_title="Equity ($)",
            yaxis=dict(tickprefix="$"),
        )

        st.plotly_chart(fig, width="stretch")

    # ── Drawdown Gauge ───────────────────────────────────────────

    def _render_drawdown_gauge(self, m: LiveMetrics) -> None:
        """Render drawdown gauge meter."""
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=m.current_drawdown,
            number={"suffix": "%", "font": {"size": 24, "color": "#fff"}},
            title={"text": "Drawdown", "font": {"size": 14}},
            gauge={
                "axis": {"range": [0, 20], "tickwidth": 1},
                "bar": {"color": "#ff4444"},
                "bgcolor": "#1a1a2e",
                "steps": [
                    {"range": [0, 5], "color": "#22c55e"},
                    {"range": [5, 10], "color": "#f59e0b"},
                    {"range": [10, 20], "color": "#ef4444"},
                ],
                "threshold": {
                    "line": {"color": "red", "width": 3},
                    "thickness": 0.8,
                    "value": m.max_drawdown,
                },
            },
        ))

        fig.update_layout(
            height=200,
            template="plotly_dark",
            margin=dict(l=20, r=20, t=40, b=10),
        )

        st.plotly_chart(fig, width="stretch")

    # ── System Health ────────────────────────────────────────────

    def _render_system_health(self, m: LiveMetrics) -> None:
        """Render system health indicators."""
        uptime_h = m.uptime_sec / 3600

        st.markdown("#### ⚙️ System Health")

        health_items = [
            ("WebSocket", "🟢 Connected" if m.ws_connected else "🔴 Disconnected"),
            ("Uptime", f"⏱️ {uptime_h:.1f}h"),
            ("Symbols", f"📊 {m.symbols_scanned}"),
            ("Signals", f"📡 {m.active_signals}"),
        ]

        for label, value in health_items:
            st.markdown(f"**{label}:** {value}")

    # ── Sparklines ───────────────────────────────────────────────

    def _render_sparklines(self, m: LiveMetrics) -> None:
        """Render mini sparkline charts for key metrics."""
        history = self._metrics_history[-30:]  # Last 30 snapshots

        if len(history) < 3:
            return

        st.markdown("#### 📊 Trending Metrics")

        cols = st.columns(4)

        sparkline_data = [
            ("Portfolio Value", [h.portfolio_value for h in history], "#00ff88"),
            ("Daily PnL", [h.daily_pnl for h in history], "#3b82f6"),
            ("Win Rate", [h.win_rate for h in history], "#f59e0b"),
            ("Sharpe Ratio", [h.sharpe_ratio for h in history], "#a855f7"),
        ]

        for col, (title, data, color) in zip(cols, sparkline_data):
            with col:
                fig = go.Figure(go.Scatter(
                    y=data,
                    mode="lines",
                    line=dict(color=color, width=2),
                    fill="tozeroy",
                    fillcolor=f"rgba({int(color[1:3],16)},{int(color[3:5],16)},{int(color[5:7],16)},0.1)",
                ))

                fig.update_layout(
                    height=100,
                    template="plotly_dark",
                    margin=dict(l=5, r=5, t=5, b=5),
                    showlegend=False,
                    xaxis=dict(visible=False),
                    yaxis=dict(visible=False),
                )

                st.plotly_chart(fig, width="stretch")
                st.caption(f"{title}: {data[-1]:.2f}")

    # ── Demo Data Generator ──────────────────────────────────────

    def generate_demo_update(self) -> LiveMetrics:
        """Generate a demo metrics update for testing."""
        last = self._metrics_history[-1] if self._metrics_history else None
        base_equity = last.portfolio_value if last else 10000

        m = LiveMetrics(
            timestamp=time.time(),
            portfolio_value=base_equity + np.random.normal(50, 200),
            daily_pnl=np.random.normal(100, 300),
            total_pnl=base_equity - 10000 + np.random.normal(0, 100),
            open_positions=np.random.randint(0, 5),
            active_signals=np.random.randint(2, 15),
            win_rate=55 + np.random.uniform(-5, 10),
            sharpe_ratio=1.5 + np.random.normal(0, 0.3),
            max_drawdown=max(0, 3 + np.random.normal(0, 1)),
            current_drawdown=max(0, 1 + np.random.normal(0, 0.5)),
            trades_today=np.random.randint(5, 20),
            win_streak=max(0, int(np.random.normal(3, 2))),
            loss_streak=max(0, int(np.random.normal(1, 1))),
            uptime_sec=43200 + len(self._metrics_history) * 30,
            symbols_scanned=np.random.randint(70, 100),
            ws_connected=True,
        )

        self.update(m)
        return m
