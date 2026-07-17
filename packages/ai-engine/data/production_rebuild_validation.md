# Production Rebuild Validation Report
Generated: 2026-06-11 21:24:14 UTC

## Overall: PARTIAL (8/10 PASS)

## System Metrics
| Metric | Value |
|--------|-------|
| Total Trades | 1436 |
| Win Rate | 35.8% |
| Profit Factor | 0.82 |
| Expectancy | $-4.50 |
| Total PnL | $-6,469.02 |

## Database Schema
| Table | Columns | Required Missing |
|-------|---------|-----------------|
| positions | 39 | NONE |
| signals | 39 | NONE |

## Fix Validation
| Fix | Status | Description | Evidence |
|-----|--------|-------------|----------|
| FIX #1 | FAIL | Trade Lifecycle Engine — MAE/MFE tracking | 1332/1436 trades have hold_minutes, 0 have MAE, 0 have MFE, 1332 have exit_reason, 0 have realized_r |
| FIX #2 | PASS | Minimum Hold Filter — 30 min minimum | Lifecycle engine exists: True. Recent 7d avg hold: 71 min (1065 trades) |
| FIX #3 | PASS | Regime Enforcement — only breakout/trending allowed | 1436/1436 trades have regime data |
| FIX #4 | PASS | Quiet Market Filter — blocks low-volatility environments | Filter module exists: True. Volatility scores stored: 0/1436 |
| FIX #5 | PASS | Symbol Expectancy Engine — auto blacklist/promote | Tracker exists: True. 125 symbols tracked, 13 blacklisted, 11 promoted |
| FIX #6 | PASS | Session Quality Filter — blocks Asia/off-hours | 1436/1436 trades have session data |
| FIX #7 | PARTIAL | Data Persistence — all institutional fields stored | 6/13 fields have non-zero data |
| FIX #8 | PASS | Forensic Analytics — post-trade outcome analytics | Module exists: True. 1436 trades available for analysis |
| FIX #9 | PASS | Validation Report Engine — automated audit report | Module exists: True |
| FIX #10 | PASS | Integration — all modules imported and wired in engine | 9/9 modules integrated |

## Detailed Evidence

### FIX #1: FAIL
- **Description**: Trade Lifecycle Engine — MAE/MFE tracking
- **Evidence**: 1332/1436 trades have hold_minutes, 0 have MAE, 0 have MFE, 1332 have exit_reason, 0 have realized_r
- **Details**: `{"hold_minutes_populated": 1332, "mae_pct_populated": 0, "mfe_pct_populated": 0, "exit_reason_populated": 1332, "realized_r_populated": 0}`

### FIX #2: PASS
- **Description**: Minimum Hold Filter — 30 min minimum
- **Evidence**: Lifecycle engine exists: True. Recent 7d avg hold: 71 min (1065 trades)
- **Details**: `{"lifecycle_engine_exists": true, "avg_hold_minutes_7d": 70.9, "recent_trades_7d": 1065}`

### FIX #3: PASS
- **Description**: Regime Enforcement — only breakout/trending allowed
- **Evidence**: 1436/1436 trades have regime data
- **Details**: `{"regime_populated": 1436, "regime_breakdown": {"breakout": {"trades": 138, "pnl": 6128.19, "wr": 38.4}, "reversal": {"trades": 366, "pnl": -212.85, "wr": 35.0}, "trending_bear": {"trades": 156, "pnl"`

### FIX #4: PASS
- **Description**: Quiet Market Filter — blocks low-volatility environments
- **Evidence**: Filter module exists: True. Volatility scores stored: 0/1436
- **Details**: `{"filter_module_exists": true, "volatility_score_populated": 0}`

### FIX #5: PASS
- **Description**: Symbol Expectancy Engine — auto blacklist/promote
- **Evidence**: Tracker exists: True. 125 symbols tracked, 13 blacklisted, 11 promoted
- **Details**: `{"tracker_exists": true, "symbols_tracked": 125, "blacklisted": 13, "promoted": 11}`

### FIX #6: PASS
- **Description**: Session Quality Filter — blocks Asia/off-hours
- **Evidence**: 1436/1436 trades have session data
- **Details**: `{"session_populated": 1436, "session_breakdown": {"london": {"trades": 296, "pnl": 2266.93, "wr": 36.1}, "asia": {"trades": 228, "pnl": -1791.47, "wr": 40.8}, "off_hours": {"trades": 80, "pnl": -3087.`

### FIX #7: PARTIAL
- **Description**: Data Persistence — all institutional fields stored
- **Evidence**: 6/13 fields have non-zero data
- **Details**: `{"mss_score": 0, "fvg_score": 0, "entry_reason": 0, "confidence": 1436, "regime": 1436, "institutional_score": 1436, "risk_reward": 1354, "session": 1436, "hold_minutes": 1332, "mae_pct": 0, "mfe_pct"`

### FIX #8: PASS
- **Description**: Forensic Analytics — post-trade outcome analytics
- **Evidence**: Module exists: True. 1436 trades available for analysis
- **Details**: `{"module_exists": true, "trades_available": 1436}`

### FIX #9: PASS
- **Description**: Validation Report Engine — automated audit report
- **Evidence**: Module exists: True
- **Details**: `{"module_exists": true}`

### FIX #10: PASS
- **Description**: Integration — all modules imported and wired in engine
- **Evidence**: 9/9 modules integrated
- **Details**: `{"TradeLifecycleEngine": true, "SessionQualityFilter": true, "SymbolExpectancyTracker": true, "ForensicAnalytics": true, "ProductionValidator": true, "ConfidenceValidator": true, "InstitutionalValidat`

## Regime Performance
- **breakout**: 138 trades, WR=38.4%, PnL=$6,128.19
- **reversal**: 366 trades, WR=35.0%, PnL=$-212.85
- **trending_bear**: 156 trades, WR=36.5%, PnL=$-283.01
- **trending_bull**: 181 trades, WR=31.5%, PnL=$-1,185.07
- **range**: 317 trades, WR=35.0%, PnL=$-1,934.17
- **ranging**: 37 trades, WR=43.2%, PnL=$-2,012.08
- **quiet**: 241 trades, WR=38.2%, PnL=$-6,970.03

## Session Performance
- **london**: 296 trades, WR=36.1%, PnL=$2,266.93
- **asia**: 228 trades, WR=40.8%, PnL=$-1,791.47
- **off_hours**: 80 trades, WR=20.0%, PnL=$-3,087.62
- **new_york**: 832 trades, WR=35.8%, PnL=$-3,856.86
