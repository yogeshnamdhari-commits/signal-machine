# DeltaTerminal — AI-Powered Binance Futures Scanner

![DeltaTerminal](https://img.shields.io/badge/DeltaTerminal-v2.0.0-blue) ![Python](https://img.shields.io/badge/Python-3.11+-3776ab) ![TypeScript](https://img.shields.io/badge/TypeScript-5.x-3178c6) ![React](https://img.shields.io/badge/React-18.x-61dafb)

Institutional-grade cryptocurrency trading platform with real-time market scanning, AI-powered signal detection, backtesting, and production dashboard.

## ⚡ Features

- **15+ Detection Engines** — OrderFlow, Institutional Patterns, Smart Money, Regime, Liquidation, Sweep, Absorption
- **AI Confidence Scoring** — Multi-factor weighted signal generation with regime-adaptive parameters
- **Backtesting Framework** — Event-driven backtesting, walk-forward optimization, Monte Carlo simulation
- **AI Optimization** — Bayesian optimization, CMA-ES, regime-adaptive parameter tuning
- **50+ Performance Metrics** — Sharpe, Sortino, VaR, CVaR, Kelly criterion, drawdown analysis
- **Production Dashboard** — Streamlit + Plotly with live metrics, heatmaps, trade analytics
- **Smart Alerts** — Rate-limited Telegram alerts with structured formatting
- **Real-time WebSocket** — Auto-reconnecting Binance Futures data feeds

## � Project Structure

```
packages/
├── ai-engine/                    # Python — Core AI/scanning engine
│   ├── core/engine.py            # Self-healing async orchestrator
│   ├── scanner/                  # 15+ detection engines
│   │   ├── orderflow.py              # Buy/sell pressure, delta, VWAP
│   │   ├── cumulative_delta.py       # Cumulative delta analysis
│   │   ├── institutional.py          # Iceberg, spoofing, absorption, sweep
│   │   ├── smart_money.py            # Stealth accumulation/distribution
│   │   ├── regime.py                 # Market regime classification
│   │   ├── ai_scorer.py              # Multi-factor confidence scoring
│   │   ├── ranking.py                # TOP-10 composite ranking
│   │   ├── dom_analytics.py          # Depth-of-market analytics
│   │   ├── funding_rate.py           # Funding rate analysis
│   │   ├── open_interest.py          # Open interest tracking
│   │   ├── exchange_flow.py          # Exchange deposit/withdrawal flow
│   │   ├── liquidation.py            # Liquidation cascade detection
│   │   ├── sweep_detector.py         # Liquidity sweep / stop hunt
│   │   ├── absorption_detector.py    # Order absorption detection
│   │   ├── spoofing_iceberg.py       # Spoofing & iceberg orders
│   │   ├── liquidity_map.py          # Liquidity zone mapping
│   │   ├── symbol_scanner.py         # Dynamic symbol discovery
│   │   ├── fake_breakout_filter.py   # False breakout filtering
│   │   ├── entry_confirmation.py     # Entry signal confirmation
│   │   └── position_sizing.py        # Position sizing algorithms
│   ├── backtesting/              # Backtesting + Optimization
│   │   ├── historical_data.py        # OHLCV data (Binance API + SQLite cache)
│   │   ├── backtester.py             # Event-driven backtesting engine
│   │   ├── walk_forward.py           # Walk-forward optimization
│   │   ├── monte_carlo.py            # Monte Carlo simulation (10k scenarios)
│   │   ├── optimizer.py              # AI adaptive optimization
│   │   └── analytics.py              # 50+ performance metrics
│   ├── dashboard/                # UI + Alerts
│   │   ├── app.py                    # Main Streamlit dashboard
│   │   ├── heatmaps.py               # Plotly correlation/volume/regime heatmaps
│   │   ├── telegram_engine.py        # Enhanced Telegram alerts
│   │   ├── alert_system.py           # In-dashboard alert panel
│   │   ├── live_metrics.py           # Real-time metrics with sparklines
│   │   └── trade_analytics_panel.py  # Trade analysis charts
│   ├── execution/risk_engine.py  # Risk management
│   ├── exchanges/binance_ws.py   # Binance WebSocket + REST
│   ├── database/db.py            # Async SQLite database
│   ├── alerts/telegram.py        # Telegram notifications
│   ├── config/settings.py        # Immutable env-driven config
│   └── infrastructure/           # Logging, utilities
├── backend/                      # Node.js + TypeScript API
│   ├── src/
│   │   ├── config/               # Configuration management
│   │   ├── middleware/            # Express middleware
│   │   ├── routes/               # API endpoints
│   │   ├── services/             # Business logic
│   │   └── utils/                # Utilities
│   └── prisma/                   # Database schema
├── frontend/                     # React + Vite + TailwindCSS
│   └── src/
│       ├── components/           # React components
│       ├── hooks/                # Custom React hooks
│       ├── services/             # API & WebSocket
│       └── types/                # TypeScript types
```

## � Quick Start

### Prerequisites
- Python 3.11+
- Node.js 20+
- Binance Futures API keys (testnet or production)

### Installation

```bash
# Clone and install
git clone <repo-url> && cd signal\ machine

# Python AI Engine
cd packages/ai-engine
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Node.js Backend
cd ../backend && npm install

# React Frontend
cd ../frontend && npm install
```

### Configuration

Create `.env` in `packages/ai-engine/`:

```env
# Binance
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret
BINANCE_TESTNET=true

# Telegram (optional)
TELEGRAM_ENABLED=false
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Logging
LOG_LEVEL=INFO
APP_ENV=development
```

### Running

```bash
cd packages/ai-engine

# Scanner engine
python main.py --mode engine --testnet

# Dashboard only
python main.py --mode dashboard

# Both
python main.py --mode both --testnet

# Or Streamlit directly
streamlit run dashboard/app.py
```

## � Scanner Engine (15+ Detection Systems)

| Engine | What It Detects |
|--------|----------------|
| **OrderFlow** | Buy/sell pressure, cumulative delta, VWAP deviation |
| **Institutional** | Iceberg orders, spoofing, absorption, liquidity sweeps |
| **Smart Money** | Stealth accumulation, hidden orders, institutional flow |
| **Regime** | Trending, ranging, volatile, breakout, reversal |
| **AI Scorer** | Multi-factor weighted confidence scoring |
| **Funding Rate** | Extreme funding → reversal signals |
| **Open Interest** | OI divergence, long/short ratio |
| **Liquidation** | Cascade detection, forced selling |
| **Sweep Detector** | Stop hunts, wick rejections |
| **Absorption** | Large order absorption at key levels |

## 🧪 Backtesting Framework

### Historical Data Engine
```python
from backtesting import HistoricalDataEngine

engine = HistoricalDataEngine()
await engine.initialize()
data = await engine.get_historical_data("BTCUSDT", interval="5m", days=90)
data = engine.add_indicators(data)
```

### Backtesting Engine
```python
from backtesting import BacktestEngine, BacktestConfig

config = BacktestConfig(initial_capital=10_000, leverage=10, risk_per_trade_pct=0.02)
engine = BacktestEngine(config)
result = await engine.run("BTCUSDT", data, my_strategy)
print(result.summary())
```

### Walk-Forward Optimization
```python
from backtesting import WalkForwardEngine

wfo = WalkForwardEngine(WalkForwardConfig(in_sample_days=60, out_sample_days=20))
result = await wfo.run("BTCUSDT", data, strategy_factory)
print(wfo.summary(result))
```

### Monte Carlo Simulation
```python
from backtesting import MonteCarloEngine

mc = MonteCarloEngine(MonteCarloConfig(n_simulations=10_000))
result = await mc.bootstrap_trades(trade_pnls)
print(f"P(profit): {result.probability_of_profit:.1%}")
print(f"95% CI: [{result.return_ci_95[0]:.1%}, {result.return_ci_95[1]:.1%}]")
```

### AI Adaptive Optimization
```python
from backtesting import AIAdaptiveOptimizer

optimizer = AIAdaptiveOptimizer(AIAdaptiveConfig(method="bayesian", n_iterations=200))
result = await optimizer.optimize(objective_function, current_regime="trending_up")
```

### Performance Analytics (50+ Metrics)
```python
from backtesting import PerformanceAnalyticsEngine

analytics = PerformanceAnalyticsEngine(initial_capital=10_000)
report = await analytics.analyze("BTCUSDT", trades)
print(report.summary())
# Sharpe, Sortino, Calmar, VaR, CVaR, Kelly, win rate, profit factor, ...
```

## 📈 Dashboard

```bash
streamlit run packages/ai-engine/dashboard/app.py
```

| Tab | Features |
|-----|----------|
| **📊 Signals** | Live signal cards with confidence bars, regime, R:R |
| **🔥 Heatmaps** | Cross-symbol correlation, volume profile, regime map, flow |
| **📈 Analytics** | PnL distribution, time analysis, symbol breakdown |
| **🔔 Alerts** | Real-time alert panel with filtering and read/unread tracking |

## 🧪 Testing

```bash
cd packages/ai-engine

# Backtesting tests
.venv/bin/python test_backtesting.py

# UI/Alerts tests
.venv/bin/python test_ui_alerts.py

# Full validation (integration, imports, runtime, performance, memory, async)
.venv/bin/python validate_phase7.py
```

## 📋 Implementation Summary

| Phase | Modules | Description |
|-------|---------|-------------|
| **Phase 1** | 8 engines | Core Data: orderflow, delta, DOM, funding, OI, exchange flow, liquidation, symbol scanner |
| **Phase 2** | 6 engines | Detection: institutional, smart money, sweep, absorption, spoofing, liquidity map |
| **Phase 3** | 4 engines | AI & Risk: scorer, ranking, regime, risk engine |
| **Phase 4** | 5 modules | Infrastructure: database, WebSocket, Telegram, config, logging |
| **Phase 5** | 6 modules | Backtesting: historical data, backtester, walk-forward, Monte Carlo, optimizer, analytics |
| **Phase 6** | 6 modules | UI: Streamlit dashboard, heatmaps, Telegram engine, alerts, live metrics, trade analytics |
| **Phase 7** | 10 tasks | Validation: integration testing, import/runtime validation, optimization, README |

## 🔧 Tech Stack

| Layer | Technology |
|-------|-----------|
| **AI Engine** | Python 3.11+, asyncio, aiohttp, websockets |
| **Data/ML** | pandas, numpy, scipy, scikit-learn |
| **Database** | aiosqlite (async SQLite) |
| **Dashboard** | Streamlit, Plotly |
| **Alerts** | Telegram Bot API, httpx |
| **Backend** | Node.js, TypeScript, Express, Socket.IO, Prisma |
| **Frontend** | React 18, Vite, TailwindCSS, TradingView Charts |

## ⚠️ Disclaimer

This software is for educational purposes only. Trading cryptocurrencies involves substantial risk of loss. Always do your own research before trading.
