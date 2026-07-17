"""
Dashboard Alert System — in-dashboard alert panel with real-time notifications.
Uses REAL alerts from the bridge (alerts.json) + session-level alerts.
Provides Streamlit-native alert display, filtering, and management.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import streamlit as st
from loguru import logger

from dashboard.data_bridge import reader as bridge_reader


@dataclass
class DashboardAlert:
    id: str
    level: str  # "info", "success", "warning", "error"
    title: str
    message: str
    timestamp: float
    symbol: Optional[str] = None
    category: str = "system"
    read: bool = False
    data: Dict[str, Any] = field(default_factory=dict)


class DashboardAlertSystem:
    """
    In-dashboard alert system with:
    - Real-time alert display
    - Category and level filtering
    - Alert read/unread tracking
    - Sound notifications (browser)
    - Auto-dismiss for low-priority alerts
    - Alert grouping and deduplication
    """

    LEVEL_ICONS = {
        "info": "ℹ️",
        "success": "✅",
        "warning": "⚠️",
        "error": "🚨",
    }

    LEVEL_COLORS = {
        "info": "#3b82f6",
        "success": "#22c55e",
        "warning": "#f59e0b",
        "error": "#ef4444",
    }

    CATEGORY_ICONS = {
        "signal": "📊",
        "position": "📍",
        "risk": "🛡️",
        "system": "⚙️",
        "market": "💹",
        "performance": "📈",
    }

    def __init__(self) -> None:
        self._init_session_state()

    def _init_session_state(self) -> None:
        """Initialize session state for alerts."""
        if "dashboard_alerts" not in st.session_state:
            st.session_state.dashboard_alerts = []
        if "alert_filter" not in st.session_state:
            st.session_state.alert_filter = "all"
        if "alert_level_filter" not in st.session_state:
            st.session_state.alert_level_filter = "all"

    # ── Alert Management ─────────────────────────────────────────

    def add_alert(
        self,
        level: str,
        title: str,
        message: str,
        symbol: Optional[str] = None,
        category: str = "system",
        data: Optional[Dict] = None,
    ) -> DashboardAlert:
        """Add a new alert to the dashboard."""
        alert = DashboardAlert(
            id=f"alert_{int(time.time() * 1000)}",
            level=level,
            title=title,
            message=message,
            timestamp=time.time(),
            symbol=symbol,
            category=category,
            data=data or {},
        )

        st.session_state.dashboard_alerts.append(alert)

        # Keep max 200 alerts
        if len(st.session_state.dashboard_alerts) > 200:
            st.session_state.dashboard_alerts = st.session_state.dashboard_alerts[-200:]

        # Auto-toast for high priority
        if level in ("error", "warning"):
            icon = self.LEVEL_ICONS.get(level, "📢")
            st.toast(f"{icon} {title}", level=level)

        return alert

    def add_signal_alert(self, signal: Dict) -> None:
        """Add a signal alert."""
        sig_type = signal.get("type", "LONG")
        icon = "🟢" if sig_type == "LONG" else "🔴"
        self.add_alert(
            level="success" if signal.get("confidence", 0) >= 0.7 else "info",
            title=f"{icon} {sig_type} Signal: {signal.get('symbol', '?')}",
            message=(
                f"Entry: ${signal.get('entry_price', 0):,.4f} | "
                f"Confidence: {signal.get('confidence', 0):.0%} | "
                f"Regime: {signal.get('regime', 'N/A')}"
            ),
            symbol=signal.get("symbol"),
            category="signal",
            data=signal,
        )

    def add_position_alert(self, symbol: str, event: str, pnl: float = 0) -> None:
        """Add a position alert."""
        if event == "opened":
            self.add_alert("info", f"📍 Position Opened: {symbol}", f"New {symbol} position", symbol, "position")
        elif event == "closed":
            level = "success" if pnl > 0 else "warning"
            icon = "✅" if pnl > 0 else "❌"
            self.add_alert(level, f"{icon} Closed: {symbol}", f"PnL: ${pnl:+,.2f}", symbol, "position")

    def add_risk_alert(self, title: str, details: str) -> None:
        """Add a risk alert."""
        self.add_alert("warning", f"🛡️ {title}", details, category="risk")

    def add_system_alert(self, title: str, message: str, level: str = "info") -> None:
        """Add a system alert."""
        self.add_alert(level, f"⚙️ {title}", message, category="system")

    # ── Rendering ────────────────────────────────────────────────

    def render(self) -> None:
        """Render the full alerts panel — merges bridge alerts with session alerts."""
        # Merge bridge alerts into session state
        bridge_alerts = bridge_reader.read_alerts()
        if bridge_alerts:
            existing_ids = {a.id for a in st.session_state.dashboard_alerts}
            for ba in bridge_alerts:
                alert_id = ba.get("id", f"bridge_{ba.get('timestamp', 0)}")
                if alert_id not in existing_ids:
                    alert = DashboardAlert(
                        id=alert_id,
                        level=ba.get("level", "info"),
                        title=ba.get("title", "Alert"),
                        message=ba.get("message", ""),
                        timestamp=ba.get("timestamp", time.time()),
                        symbol=ba.get("symbol"),
                        category=ba.get("category", "system"),
                        data=ba.get("data", {}),
                    )
                    st.session_state.dashboard_alerts.append(alert)

        alerts = st.session_state.dashboard_alerts

        # Header with count
        col1, col2 = st.columns([3, 1])
        with col1:
            st.subheader(f"🔔 Alerts ({len(alerts)})")
        with col2:
            unread = sum(1 for a in alerts if not a.read)
            if unread > 0:
                st.badge(f"{unread} new", color="red")

        # Filters
        filter_col1, filter_col2, filter_col3 = st.columns([1, 1, 1])
        with filter_col1:
            category_filter = st.selectbox(
                "Category",
                ["all", "signal", "position", "risk", "system", "market", "performance"],
                key="alert_cat_filter",
            )
        with filter_col2:
            level_filter = st.selectbox(
                "Level",
                ["all", "info", "success", "warning", "error"],
                key="alert_lvl_filter",
            )
        with filter_col3:
            if st.button("🗑️ Clear All", key="clear_alerts"):
                st.session_state.dashboard_alerts = []
                st.rerun()

        # Filter alerts
        filtered = alerts
        if category_filter != "all":
            filtered = [a for a in filtered if a.category == category_filter]
        if level_filter != "all":
            filtered = [a for a in filtered if a.level == level_filter]

        # Render alerts
        if not filtered:
            st.info("No alerts matching filters.")
            return

        for alert in reversed(filtered[-50:]):  # Show most recent first
            self._render_alert(alert)

    def _render_alert(self, alert: DashboardAlert) -> None:
        """Render a single alert card."""
        icon = self.LEVEL_ICONS.get(alert.level, "📢")
        cat_icon = self.CATEGORY_ICONS.get(alert.category, "🔔")
        age = time.time() - alert.timestamp

        # Format age
        if age < 60:
            age_str = f"{int(age)}s ago"
        elif age < 3600:
            age_str = f"{int(age / 60)}m ago"
        else:
            age_str = f"{int(age / 3600)}h ago"

        # Unread indicator
        unread_dot = "● " if not alert.read else "  "

        # Color border
        border_color = self.LEVEL_COLORS.get(alert.level, "#888")

        with st.container():
            st.markdown(f"""
            <div style="border-left: 3px solid {border_color}; padding: 8px 12px; margin: 4px 0;
                        background: #1a1a2e; border-radius: 4px; font-size: 14px;">
                {unread_dot}<strong>{icon} {alert.title}</strong>
                <span style="color: #888; float: right; font-size: 12px;">
                    {cat_icon} {alert.category} | {age_str}
                </span><br>
                <span style="color: #ccc;">{alert.message}</span>
                {f'<br><span style="color: #888; font-size: 12px;">📌 {alert.symbol}</span>' if alert.symbol else ''}
            </div>
            """, unsafe_allow_html=True)

            # Mark as read
            if not alert.read:
                cols = st.columns([1, 1, 6])
                with cols[0]:
                    if st.button("👁️", key=f"read_{alert.id}", help="Mark as read"):
                        alert.read = True
                        st.rerun()

    # ── Stats ────────────────────────────────────────────────────

    def get_stats(self) -> Dict:
        """Get alert statistics."""
        alerts = st.session_state.dashboard_alerts
        now = time.time()
        return {
            "total": len(alerts),
            "unread": sum(1 for a in alerts if not a.read),
            "last_hour": sum(1 for a in alerts if now - a.timestamp < 3600),
            "by_level": {
                level: sum(1 for a in alerts if a.level == level)
                for level in ["info", "success", "warning", "error"]
            },
            "by_category": {
                cat: sum(1 for a in alerts if a.category == cat)
                for cat in ["signal", "position", "risk", "system", "market", "performance"]
            },
        }

    # ── Notification Sound ───────────────────────────────────────

    def _play_notification_sound(self) -> None:
        """Play browser notification sound for high-priority alerts."""
        st.markdown("""
        <script>
            function playAlertSound() {
                const audio = new Audio('data:audio/wav;base64,UklGRnoGAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQoGAACBhYqFbF1fdJivrJBhNjVgodDbsGczIj2NysijaTkmTaLC0bpqMxxLj8PNu3M3HlOhxM67czceV6vM0bpzNx5Xq8zRunM3HlerzNG6czceV6vM0bpzNw==');
                audio.volume = 0.3;
                audio.play();
            }
        </script>
        """, unsafe_allow_html=True)
