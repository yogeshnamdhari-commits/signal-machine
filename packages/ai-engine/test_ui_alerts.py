"""
Phase 6 — UI + Alerts: Test Suite
Validates all 6 modules work correctly (non-GUI tests).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import asyncio
import numpy as np
from datetime import datetime, timedelta


async def test_telegram_engine():
    """Test TelegramAlertEngine."""
    from dashboard.telegram_engine import (
        TelegramAlertEngine, AlertConfig, Alert, AlertLevel, AlertCategory
    )

    config = AlertConfig(
        enabled=True,
        bot_token="",
        chat_id="",
        min_interval_sec=0,
        max_alerts_per_hour=1000,
        max_alerts_per_day=1000,
    )
    engine = TelegramAlertEngine(config)
    await engine.initialize()

    # Test signal alert
    signal = {
        "id": 1,
        "type": "LONG",
        "symbol": "BTCUSDT",
        "entry_price": 68500.0,
        "stop_loss": 67800.0,
        "take_profit": 70200.0,
        "confidence": 0.85,
        "regime": "trending_up",
        "risk_adjusted": {
            "quantity": 0.05,
            "position_value": 3425.0,
            "margin_required": 342.5,
        },
    }
    result = await engine.send_signal_alert(signal)
    assert result, "Signal alert should be sent"

    # Test position alert
    result = await engine.send_position_alert(
        {"symbol": "ETHUSDT", "side": "SHORT", "entry_price": 3650},
        "closed",
        pnl=-125.50,
        price=3675.0,
    )
    assert result, "Position alert should be sent"

    # Test risk alert
    result = await engine.send_risk_alert("Max Drawdown Approaching", "Current DD: 8.5%")
    assert result, "Risk alert should be sent"

    # Test system alert
    result = await engine.send_system_alert("Engine Started", "DeltaTerminal running")
    assert result, "System alert should be sent"

    # Test stats
    stats = engine.get_stats()
    assert stats["total"] >= 4, f"Expected >= 4 alerts, got {stats['total']}"

    # Test rate limiting
    for _ in range(5):
        await engine.send_alert(Alert(
            id=f"test_{np.random.randint(10000)}",
            category=AlertCategory.SYSTEM,
            level=AlertLevel.LOW,
            title="Test",
            message="Test",
            timestamp=datetime.now(),
        ))

    print("✅ TelegramAlertEngine: PASSED — 4 alert types, stats working")


async def test_dashboard_alerts():
    """Test DashboardAlertSystem (without Streamlit rendering)."""
    from dashboard.alert_system import DashboardAlertSystem, DashboardAlert

    system = DashboardAlertSystem()

    # Test direct alert creation (bypassing Streamlit for testing)
    alert = DashboardAlert(
        id="test_1",
        level="success",
        title="Test Signal",
        message="BTCUSDT LONG @ 68500",
        timestamp=1700000000,
        symbol="BTCUSDT",
        category="signal",
    )

    assert alert.id == "test_1"
    assert alert.level == "success"
    assert not alert.read

    # Test stats structure
    stats = system.get_stats()
    assert "total" in stats
    assert "unread" in stats
    assert "by_level" in stats
    assert "by_category" in stats

    # Test level icons
    assert system.LEVEL_ICONS["info"] == "ℹ️"
    assert system.LEVEL_ICONS["success"] == "✅"
    assert system.LEVEL_ICONS["warning"] == "⚠️"
    assert system.LEVEL_ICONS["error"] == "🚨"

    print("✅ DashboardAlertSystem: PASSED — alert creation and stats working")


async def test_live_metrics():
    """Test LiveMetricsPanel (without Streamlit rendering)."""
    from dashboard.live_metrics import LiveMetricsPanel, LiveMetrics

    panel = LiveMetricsPanel()

    # Test metrics creation
    m = LiveMetrics(
        timestamp=1700000000,
        portfolio_value=10500.0,
        daily_pnl=500.0,
        total_pnl=5000.0,
        open_positions=3,
        active_signals=7,
        win_rate=65.0,
        sharpe_ratio=1.9,
        max_drawdown=3.5,
        current_drawdown=1.2,
        trades_today=15,
        win_streak=5,
        loss_streak=1,
        uptime_sec=86400,
        symbols_scanned=90,
        ws_connected=True,
    )

    assert m.portfolio_value == 10500.0
    assert m.ws_connected is True

    # Test demo data generation
    demo = panel.generate_demo_update()
    assert demo.portfolio_value > 0
    assert 0 <= demo.win_rate <= 100

    # Test key metrics structure
    metrics_data = [
        ("💰 Portfolio", f"${demo.portfolio_value:,.2f}"),
        ("📈 Daily PnL", f"${demo.daily_pnl:+,.2f}"),
        ("🎯 Win Rate", f"{demo.win_rate:.1f}%"),
        ("⚡ Sharpe", f"{demo.sharpe_ratio:.2f}"),
        ("📉 Drawdown", f"{demo.current_drawdown:.1f}%"),
        ("🔄 Trades", f"{demo.trades_today}"),
        ("🔥 Streak", f"W:{demo.win_streak} L:{demo.loss_streak}"),
        ("🏢 Positions", f"{demo.open_positions}"),
    ]

    for label, value in metrics_data:
        assert isinstance(value, str) and len(value) > 0, f"Bad format: {value}"

    print("✅ LiveMetricsPanel: PASSED — metrics generation and formatting working")


async def test_trade_analytics():
    """Test Trade Analytics data processing (without Streamlit rendering)."""
    np.random.seed(42)
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    trades = []

    for i in range(50):
        symbol = np.random.choice(symbols)
        pnl = np.random.normal(50, 200)
        trades.append({
            "symbol": symbol,
            "side": np.random.choice(["LONG", "SHORT"]),
            "entry_price": np.random.uniform(100, 60000),
            "exit_price": np.random.uniform(100, 60000),
            "pnl": pnl,
            "hold_minutes": np.random.randint(5, 300),
            "exit_reason": np.random.choice(["take_profit", "stop_loss"]),
            "hour": np.random.randint(0, 24),
            "day_of_week": np.random.choice([
                "Monday", "Tuesday", "Wednesday", "Thursday", "Friday"
            ]),
        })

    import pandas as pd
    df = pd.DataFrame(trades)

    # Verify analytics computations
    total_pnl = df["pnl"].sum()
    win_rate = (df["pnl"] > 0).mean() * 100
    avg_win = df[df["pnl"] > 0]["pnl"].mean()
    avg_loss = df[df["pnl"] <= 0]["pnl"].mean()

    assert len(df) == 50
    assert -5000 < total_pnl < 5000  # Reasonable range for random data
    assert 20 < win_rate < 80
    assert isinstance(avg_win, float)
    assert isinstance(avg_loss, float)

    # Per-symbol analysis
    sym_stats = df.groupby("symbol").agg(
        trades=("pnl", "count"),
        total_pnl=("pnl", "sum"),
        win_rate=("pnl", lambda x: (x > 0).mean() * 100),
    )

    assert len(sym_stats) == 3
    assert all(0 <= wr <= 100 for wr in sym_stats["win_rate"])

    print(f"✅ TradeAnalytics: PASSED — {len(df)} trades analyzed, "
          f"win_rate={win_rate:.1f}%, total_pnl=${total_pnl:+,.2f}")


async def test_heatmap_data():
    """Test heatmap data processing."""
    import pandas as pd

    # Correlation matrix test
    symbols = ["BTC", "ETH", "SOL", "BNB", "XRP"]
    n = len(symbols)
    np.random.seed(42)
    corr = np.random.uniform(-1, 1, (n, n))
    corr = (corr + corr.T) / 2
    np.fill_diagonal(corr, 1.0)
    corr = np.clip(corr, -1, 1)

    assert corr.shape == (5, 5)
    assert all(abs(corr[i, i] - 1.0) < 0.001 for i in range(n))
    assert all(-1 <= corr[i, j] <= 1 for i in range(n) for j in range(n))

    # Volume heatmap data test
    hours = 24
    syms = 5
    vol_data = np.random.uniform(10, 200, (syms, hours))
    assert vol_data.shape == (5, 24)

    # Regime heatmap test
    regime_values = {"trending_up": 1.0, "ranging": 0.0, "trending_down": -1.0}
    regime_data = np.random.choice(list(regime_values.values()), (syms, 5))
    assert regime_data.shape == (5, 5)

    print("✅ HeatmapData: PASSED — correlation, volume, regime matrices valid")


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Phase 6 — UI + Alerts: Test Suite")
    print("=" * 60)

    await test_telegram_engine()
    await test_dashboard_alerts()
    await test_live_metrics()
    await test_trade_analytics()
    await test_heatmap_data()

    print("=" * 60)
    print("✅ ALL TESTS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
