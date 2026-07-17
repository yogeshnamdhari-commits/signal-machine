# EMA_V5 v1.0.0 — Deployment Guide

**Version:** EMA_V5 v1.0.0
**Date:** 2026-06-26
**Status:** PRODUCTION RELEASE LOCKED

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | 3.14.5 |
| OS | macOS / Linux |
| RAM | 4GB minimum |
| Disk | 1GB free |
| Network | Stable internet (Binance WebSocket) |

---

## Installation

### 1. Clone Repository
```bash
git clone <repository-url>
cd "signal machine"
```

### 2. Create Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
cd packages/ai-engine
pip install -r requirements.txt
```

### 4. Verify Installation
```bash
source "../../.venv/bin/activate"
python -c "from scanner.ema_v5 import *; print('EMA_V5 loaded successfully')"
```

---

## Configuration

### Bridge Configuration
Bridge files are located in `data/bridge/`. The system auto-creates these on first run.

### Database
- **Signal DB:** `data/ema_v5_signals.db` (auto-created)
- **Historical DB:** `data/database/historical_klines.db` (pre-populated)

### Strategy Parameters
Located in `scanner/ema_v5/config/`:
- `initial_balance`: 10000 USDT
- `risk_per_trade_pct`: 1.0%
- `max_positions`: 3
- `timeframe`: 1h
- `min_confidence`: 0.50

---

## Deployment Modes

### Production Mode (Recommended)
```bash
cd "/Users/targetmobile/Documents/signal machine"
bash start_production.sh
```
- Runs engine with supervisor
- Auto-restart on failure
- Bridge synchronization enabled

### Service Mode (macOS LaunchAgent)
```bash
bash service/install_service.sh
```
- Installs as macOS LaunchAgent
- Auto-start on login
- Managed by `service/supervisor.py`

### Development Mode
```bash
cd packages/ai-engine
source "../../.venv/bin/activate"
python -m scanner.ema_v5.core.engine
```
- Direct engine execution
- Manual restart required

---

## Post-Deployment Verification

Run the certification script:
```bash
cd packages/ai-engine
source "../../.venv/bin/activate"
python << 'EOF'
import json, time, os

checks = []
for f in ['ema_v5.json', 'status.json', 'engine_health.json', 'market_data.json']:
    path = f'../../data/bridge/{f}'
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        checks.append(age < 300)

with open('../../data/bridge/status.json') as f:
    status = json.load(f)
    checks.append(status.get('status', {}).get('running', False))
    checks.append(status.get('status', {}).get('ws_connected', False))

passed = sum(checks)
total = len(checks)
print(f"Post-Deployment: {passed}/{total} checks passed")
EOF
```

---

## File Structure
```
signal machine/
├── .venv/                          # Python virtual environment
├── data/
│   ├── bridge/                     # Bridge JSON files (17)
│   ├── database/                   # Historical kline data
│   ├── ema_v5_signals.db          # Signal database
│   └── releases/                   # Release certifications
├── packages/
│   └── ai-engine/
│       ├── scanner/
│       │   └── ema_v5/            # EMA_V5 module (29 packages, 162 files)
│       └── requirements.txt
├── service/
│   ├── supervisor.py              # Process supervisor
│   ├── install_service.sh         # LaunchAgent installer
│   └── uninstall_service.sh       # LaunchAgent uninstaller
├── start_production.sh            # Production startup script
└── start_dashboard.sh             # Dashboard startup script
```

---

## Rollback Procedure

EMA_V5 is a pure additive module. To rollback:

1. Stop the EMA_V5 scanner
2. Remove `scanner/ema_v5/` directory
3. Remove EMA_V5 data files:
   ```bash
   rm -f data/ema_v5_signals.db
   rm -f data/bridge/ema_v5.json
   ```
4. Existing systems continue unchanged

---

## Operator Checklist

- [x] All 63 tests pass
- [x] Production checks 15/15
- [x] Security checks 7/7
- [x] Bridge files fresh (< 30s)
- [x] WebSocket connected
- [x] Engine running
- [x] Live Sheet ONLINE
- [ ] Paper trading run for 2+ weeks (RECOMMENDED)
- [ ] Confidence threshold reviewed (85% dynamic default)
- [ ] Max drawdown limits reviewed
