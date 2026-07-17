"""
Demo Data Generator — populates the data bridge with realistic test data.
Useful for testing the dashboard without running the full engine.
"""
from __future__ import annotations

import json
import random
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np


BRIDGE_DIR = Path("data/bridge")
BRIDGE_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT"]
REGIMES = ["trending_up", "trending_down", "ranging", "volatile", "breakout", "reversal", "quiet"]


def generate_demo_signals(count: int = 5) -> list:
    """Generate realistic demo signals."""
    signals = []
    now = time.time()

    for i in range(count):
        symbol = random.choice(SYMBOLS[:5])
        side = random.choice(["LONG", "SHORT"])
        base_price = {"BTCUSDT": 68000, "ETHUSDT": 3600, "SOLUSDT": 175, "BNBUSDT": 600, "XRPUSDT": 0.52}.get(symbol, 100)
        price = base_price * (1 + random.uniform(-0.02, 0.02))
        sl_pct = random.uniform(0.01, 0.03)
        tp_pct = random.uniform(0.02, 0.05)

        if side == "LONG":
            sl = price * (1 - sl_pct)
            tp = price * (1 + tp_pct)
        else:
            sl = price * (1 + sl_pct)
            tp = price * (1 - tp_pct)

        confidence = random.uniform(0.55, 0.95)
        regime = random.choice(REGIMES)

        signals.append({
            "id": f"demo_{i}_{int(now)}",
            "type": side,
            "symbol": symbol,
            "entry_price": round(price, 4),
            "stop_loss": round(sl, 4),
            "take_profit": round(tp, 4),
            "confidence": round(confidence, 3),
            "regime": regime,
            "status": "active",
            "created_at": now - random.uniform(0, 300),
            "risk_adjusted": {
                "quantity": round(random.uniform(0.01, 0.5), 4),
                "position_value": round(random.uniform(500, 5000), 2),
                "margin_required": round(random.uniform(50, 500), 2),
                "risk_reward": round(tp_pct / sl_pct, 2),
            },
        })

    return signals


def generate_demo_metrics() -> dict:
    """Generate realistic demo metrics."""
    return {
        "portfolio_value": round(10000 + random.uniform(-500, 1500), 2),
        "total_pnl": round(random.uniform(-500, 2000), 2),
        "daily_pnl": round(random.uniform(-300, 500), 2),
        "win_rate": round(random.uniform(55, 72), 1),
        "sharpe_ratio": round(random.uniform(1.2, 2.5), 2),
        "max_drawdown": round(random.uniform(2, 8), 1),
        "current_drawdown": round(random.uniform(0, 3), 1),
        "trades_total": random.randint(100, 300),
        "trades_today": random.randint(5, 25),
        "open_positions": random.randint(0, 5),
        "symbols_scanned": random.randint(70, 120),
    }


def generate_demo_alerts(count: int = 3) -> list:
    """Generate demo alerts."""
    alerts = []
    now = time.time()

    types = [
        ("success", "Signal Detected", "BTCUSDT LONG @ 68,450"),
        ("info", "Engine Started", "DeltaTerminal running on testnet"),
        ("warning", "High Volatility", "BTC 1h volatility above threshold"),
        ("success", "Position Closed", "ETHUSDT SHORT +$125.50"),
        ("info", "Ranking Update", "TOP-3: BTC, ETH, SOL"),
    ]

    for i in range(count):
        level, title, message = types[i % len(types)]
        alerts.append({
            "id": f"alert_{i}",
            "level": level,
            "title": title,
            "message": message,
            "timestamp": now - random.uniform(0, 3600),
            "category": "signal" if "Signal" in title else "system",
        })

    return alerts


def write_demo_data() -> None:
    """Write all demo data to the bridge."""
    now = time.time()

    # Signals
    signals = generate_demo_signals(5)
    with open(BRIDGE_DIR / "signals.json", "w") as f:
        json.dump({"signals": signals, "timestamp": now, "count": len(signals)}, f, indent=2)

    # Metrics
    metrics = generate_demo_metrics()
    with open(BRIDGE_DIR / "metrics.json", "w") as f:
        json.dump({"metrics": metrics, "timestamp": now}, f, indent=2)

    # Alerts
    alerts = generate_demo_alerts(3)
    with open(BRIDGE_DIR / "alerts.json", "w") as f:
        json.dump({"alerts": alerts, "timestamp": now}, f, indent=2)

    # Status
    status = {
        "running": True,
        "symbols": 85,
        "signals": len(signals),
        "alerts": len(alerts),
        "uptime": random.randint(3600, 86400),
        "last_update": now,
        "ws_connected": True,
    }
    with open(BRIDGE_DIR / "status.json", "w") as f:
        json.dump({"status": status, "timestamp": now}, f, indent=2)

    print(f"✅ Demo data written to {BRIDGE_DIR}")
    print(f"   Signals: {len(signals)}")
    print(f"   Metrics: {len(metrics)} keys")
    print(f"   Alerts: {len(alerts)}")


if __name__ == "__main__":
    write_demo_data()
