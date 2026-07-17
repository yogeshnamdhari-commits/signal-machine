# EMA_V5 v1.0.0 — Deployment Guide

---

## Prerequisites

- Python 3.14.5
- macOS / Linux
- 4GB RAM minimum
- 1GB disk free
- Stable internet (Binance WebSocket)

---

## Quick Start

```bash
cd "signal machine"
source .venv/bin/activate
bash start_production.sh
```

---

## Verification

```bash
# Check engine status
cat data/bridge/status.json | python3 -c "import sys,json;s=json.load(sys.stdin)['status'];print(f'Running={s[\"running\"]} WS={s[\"ws_connected\"]} Uptime={s[\"uptime\"]:.0f}s')"

# Check EMA_V5 state
cat data/bridge/ema_v5.json | python3 -c "import sys,json;e=json.load(sys.stdin)['ema_v5'];print(f'Scans={e[\"scanner\"][\"scan_count\"]} Cache={e[\"scanner\"][\"cache_size\"]}')"

# Run tests
cd packages/ai-engine && source "../../.venv/bin/activate"
python -c "from scanner.ema_v5.tests import EMAv5TestRunner; r=EMAv5TestRunner().run_all(); print(f'Tests: {r[\"summary\"][\"passed\"]}/{r[\"summary\"][\"total\"]}')"
```

---

## Architecture

```
signal machine/
├── .venv/                          Python environment
├── data/
│   ├── bridge/                     14 bridge JSON files
│   ├── database/                   Historical kline data
│   └── ema_v5_signals.db          Signal database
├── packages/ai-engine/
│   ├── scanner/ema_v5/            29 packages, 162 files
│   │   ├── core/                  Scanner, state machine
│   │   ├── detectors/             Regime, trend, pullback, candle, volume
│   │   ├── engines/               Confidence, signal
│   │   ├── storage/               Database, JSON, recovery
│   │   ├── security/              Sanitizer, SQL guard, monitor
│   │   ├── cache/                 EMA cache, memory, disk
│   │   ├── backtest/              Backtest engine
│   │   ├── execution/             Paper trader, risk manager
│   │   └── verification/          Verifier, diagnostics
│   └── core/engine.py            Main engine (EMA_V5 integration)
└── service/                       Supervisor, LaunchAgent
```

---

## Rollback

```bash
kill $(cat service/engine.pid) 2>/dev/null
rm -rf packages/ai-engine/scanner/ema_v5
rm -f data/ema_v5_signals.db data/bridge/ema_v5.json
bash start_production.sh
```
