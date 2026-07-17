#!/usr/bin/env python3
"""
EMA_V5 Production Validation Engine — Complete Trade Recording & Monitoring.

CODE FREEZE: NO strategy changes. This is observation-only.

Records every trade with 23 fields:
  Timestamp, Exchange, Symbol, Direction, Entry, Exit, Position Size,
  Fees, Funding, Slippage, MFE, MAE, Holding Time, Gross Profit,
  Net Profit, R Multiple, ATR at Entry, ATR at Exit, Expected Profit,
  Actual Profit, Signal Confidence, Market Regime, Strategy Version

Generates: Daily, Weekly, Monthly, Portfolio, Risk, Deviation Reports.

Monitors: PF, Sharpe, Calmar, RF, DD, Slippage, Fee, Expectancy deviation.

Promotion: Paper → Small → Medium → Full (statistical gate).
"""
from __future__ import annotations

import json
import time
import sqlite3
import statistics
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from loguru import logger


# ══════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "validation.db"
BRIDGE_PATH = Path(__file__).resolve().parent.parent / "data" / "bridge"
EXCHANGE = "BINANCE_FUTURES"
STRATEGY_VERSION = "ema_v5_v2f"

# Backtest baseline from V2f optimizer (8/8 APPROVED)
BASELINE = {
    "strategy_version": "ema_v5_v2f",
    "profit_factor": 1.55,
    "sharpe": 3.69,
    "sortino": 2.97,
    "calmar": 9.36,
    "recovery_factor": 7545.50,
    "max_drawdown_pct": 8.52,
    "win_rate_pct": 48.5,
    "expectancy_per_trade": 35.13,
    "avg_r_multiple": 1.41,
    "avg_hold_hours": 8.5,
    "avg_mae_pct": 3.2,
    "avg_mfe_pct": 8.5,
    "avg_slippage_bps": 2.0,
    "avg_fee_bps": 6.0,
    "net_profit_pct": 542.86,
    "total_trades": 1830,
}

# Deviation alert thresholds (from user spec)
DEVIATION_THRESHOLDS = {
    "profit_factor":    {"warn": 15, "critical": 25, "direction": "below", "min_trades": 20},
    "sharpe":           {"warn": 20, "critical": 30, "direction": "below", "min_trades": 30},
    "calmar":           {"warn": 20, "critical": 30, "direction": "below", "min_trades": 30},
    "recovery_factor":  {"warn": 20, "critical": 30, "direction": "below", "min_trades": 30},
    "max_drawdown":     {"warn": 20, "critical": 40, "direction": "above", "min_trades": 20},
    "expectancy":       {"warn": 15, "critical": 30, "direction": "below", "min_trades": 20},
    "avg_slippage":     {"warn": 30, "critical": 60, "direction": "above", "min_trades": 10},
    "total_fees":       {"warn": 25, "critical": 50, "direction": "above", "min_trades": 10},
    "win_rate":         {"warn": 15, "critical": 25, "direction": "below", "min_trades": 20},
    "avg_r_multiple":   {"warn": 20, "critical": 35, "direction": "below", "min_trades": 20},
}

# Promotion phases
PROMOTION_PHASES = {
    "PAPER":      {"risk_pct": 0.00, "min_trades": 0,    "min_days": 0,   "min_pf": 0,    "max_dd": 100},
    "SMALL":      {"risk_pct": 0.0025, "min_trades": 50,  "min_days": 14,  "min_pf": 1.3,  "max_dd": 12},
    "MEDIUM":     {"risk_pct": 0.005,  "min_trades": 200, "min_days": 30,  "min_pf": 1.4,  "max_dd": 10},
    "FULL":       {"risk_pct": 0.01,   "min_trades": 500, "min_days": 60,  "min_pf": 1.5,  "max_dd": 8.5},
}


# ══════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════

class ValidationDB:
    """SQLite persistence for production validation."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")

        # ── Master trade table (23+ fields per spec) ──
        db.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                -- Identity
                timestamp REAL NOT NULL,
                exchange TEXT DEFAULT 'BINANCE_FUTURES',
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                -- Entry/Exit
                entry_price REAL NOT NULL,
                entry_time REAL NOT NULL,
                exit_price REAL DEFAULT 0,
                exit_time REAL DEFAULT 0,
                exit_reason TEXT DEFAULT '',
                -- Sizing
                position_size REAL DEFAULT 0,
                -- Costs
                fees REAL DEFAULT 0,
                funding REAL DEFAULT 0,
                slippage_bps REAL DEFAULT 0,
                -- Excursion
                mfe_pct REAL DEFAULT 0,
                mae_pct REAL DEFAULT 0,
                mfe_price REAL DEFAULT 0,
                mae_price REAL DEFAULT 0,
                -- Timing
                hold_minutes REAL DEFAULT 0,
                -- P&L
                gross_profit REAL DEFAULT 0,
                net_profit REAL DEFAULT 0,
                r_multiple REAL DEFAULT 0,
                -- ATR
                atr_entry REAL DEFAULT 0,
                atr_exit REAL DEFAULT 0,
                -- Expected vs Actual
                expected_profit REAL DEFAULT 0,
                actual_profit REAL DEFAULT 0,
                -- Signal metadata
                signal_confidence REAL DEFAULT 0,
                market_regime TEXT DEFAULT '',
                strategy_version TEXT DEFAULT 'ema_v5_v2f',
                -- Backtest comparison
                bt_entry_price REAL DEFAULT 0,
                bt_exit_price REAL DEFAULT 0,
                bt_pnl REAL DEFAULT 0,
                deviation_pct REAL DEFAULT 0,
                -- Classification
                outcome TEXT DEFAULT '',
                stop_loss REAL DEFAULT 0,
                take_profit REAL DEFAULT 0,
                risk_reward REAL DEFAULT 0,
                -- Phase tracking
                promotion_phase TEXT DEFAULT 'PAPER',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # ── Periodic summary table ──
        db.execute("""
            CREATE TABLE IF NOT EXISTS periodic_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                total_signals INTEGER DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                winning_trades INTEGER DEFAULT 0,
                losing_trades INTEGER DEFAULT 0,
                net_pnl REAL DEFAULT 0,
                gross_pnl REAL DEFAULT 0,
                total_fees REAL DEFAULT 0,
                total_funding REAL DEFAULT 0,
                total_slippage REAL DEFAULT 0,
                profit_factor REAL DEFAULT 0,
                sharpe REAL DEFAULT 0,
                sortino REAL DEFAULT 0,
                calmar REAL DEFAULT 0,
                recovery_factor REAL DEFAULT 0,
                cagr REAL DEFAULT 0,
                max_drawdown_pct REAL DEFAULT 0,
                win_rate REAL DEFAULT 0,
                expectancy REAL DEFAULT 0,
                avg_r_multiple REAL DEFAULT 0,
                avg_hold_minutes REAL DEFAULT 0,
                avg_slippage_bps REAL DEFAULT 0,
                avg_fee_bps REAL DEFAULT 0,
                avg_mae_pct REAL DEFAULT 0,
                avg_mfe_pct REAL DEFAULT 0,
                capital_utilization REAL DEFAULT 0,
                equity_start REAL DEFAULT 0,
                equity_end REAL DEFAULT 0,
                peak_equity REAL DEFAULT 0,
                days REAL DEFAULT 0,
                deviation_from_bt REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # ── Deviation alerts ──
        db.execute("""
            CREATE TABLE IF NOT EXISTS deviation_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                alert_type TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                expected_value REAL DEFAULT 0,
                actual_value REAL DEFAULT 0,
                deviation_pct REAL DEFAULT 0,
                sample_size INTEGER DEFAULT 0,
                severity TEXT DEFAULT 'info',
                message TEXT DEFAULT '',
                acknowledged INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # ── Promotion log ──
        db.execute("""
            CREATE TABLE IF NOT EXISTS promotion_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                from_phase TEXT NOT NULL,
                to_phase TEXT NOT NULL,
                trigger_metric TEXT DEFAULT '',
                trigger_value REAL DEFAULT 0,
                met_criteria TEXT DEFAULT '{}',
                approved INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # ── Indexes ──
        db.execute("CREATE INDEX IF NOT EXISTS idx_trades_time ON trades(entry_time)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_trades_outcome ON trades(outcome)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_trades_phase ON trades(promotion_phase)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ps_period ON periodic_summary(period)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ps_start ON periodic_summary(period_start)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_da_type ON deviation_alerts(alert_type)")

        db.commit()
        db.close()
        # Schema migration: add missing columns to existing tables
        self._migrate()
        logger.info("ValidationDB initialized at {}", self.db_path)

    def _migrate(self) -> None:
        """Add missing columns to existing tables (safe no-ops if already present)."""
        migrations = [
            ("periodic_summary", "days", "REAL DEFAULT 0"),
            ("periodic_summary", "deviation_from_bt", "REAL DEFAULT 0"),
            ("trades", "atr_entry", "REAL DEFAULT 0"),
            ("trades", "atr_exit", "REAL DEFAULT 0"),
            ("trades", "expected_profit", "REAL DEFAULT 0"),
            ("trades", "actual_profit", "REAL DEFAULT 0"),
            ("trades", "bt_entry_price", "REAL DEFAULT 0"),
            ("trades", "bt_exit_price", "REAL DEFAULT 0"),
            ("trades", "bt_pnl", "REAL DEFAULT 0"),
            ("trades", "deviation_pct", "REAL DEFAULT 0"),
            ("trades", "funding", "REAL DEFAULT 0"),
            ("trades", "slippage_bps", "REAL DEFAULT 0"),
            ("trades", "mfe_price", "REAL DEFAULT 0"),
            ("trades", "mae_price", "REAL DEFAULT 0"),
            ("trades", "position_size", "REAL DEFAULT 0"),
            ("trades", "gross_profit", "REAL DEFAULT 0"),
            ("trades", "promotion_phase", "TEXT DEFAULT 'PAPER'"),
            ("trades", "strategy_version", "TEXT DEFAULT 'ema_v5_v2f'"),
        ]
        db = sqlite3.connect(str(self.db_path), timeout=10)
        for table, col, typedef in migrations:
            try:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass  # Column already exists
        db.commit()
        db.close()

    # ── Trade CRUD ──

    def record_trade(self, trade: Dict) -> int:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        outcome = "win" if trade.get("net_profit", 0) > 0 else "loss"
        cursor = db.execute("""
            INSERT INTO trades (
                timestamp, exchange, symbol, direction,
                entry_price, entry_time, exit_price, exit_time, exit_reason,
                position_size, fees, funding, slippage_bps,
                mfe_pct, mae_pct, mfe_price, mae_price,
                hold_minutes, gross_profit, net_profit, r_multiple,
                atr_entry, atr_exit, expected_profit, actual_profit,
                signal_confidence, market_regime, strategy_version,
                bt_entry_price, bt_exit_price, bt_pnl, deviation_pct,
                outcome, stop_loss, take_profit, risk_reward, promotion_phase
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            trade.get("timestamp", time.time()),
            trade.get("exchange", EXCHANGE),
            trade.get("symbol", ""),
            trade.get("direction", ""),
            trade.get("entry_price", 0),
            trade.get("entry_time", 0),
            trade.get("exit_price", 0),
            trade.get("exit_time", 0),
            trade.get("exit_reason", ""),
            trade.get("position_size", 0),
            trade.get("fees", 0),
            trade.get("funding", 0),
            trade.get("slippage_bps", 0),
            trade.get("mfe_pct", 0),
            trade.get("mae_pct", 0),
            trade.get("mfe_price", 0),
            trade.get("mae_price", 0),
            trade.get("hold_minutes", 0),
            trade.get("gross_profit", 0),
            trade.get("net_profit", 0),
            trade.get("r_multiple", 0),
            trade.get("atr_entry", 0),
            trade.get("atr_exit", 0),
            trade.get("expected_profit", 0),
            trade.get("actual_profit", 0),
            trade.get("signal_confidence", 0),
            trade.get("market_regime", ""),
            trade.get("strategy_version", STRATEGY_VERSION),
            trade.get("bt_entry_price", 0),
            trade.get("bt_exit_price", 0),
            trade.get("bt_pnl", 0),
            trade.get("deviation_pct", 0),
            outcome,
            trade.get("stop_loss", 0),
            trade.get("take_profit", 0),
            trade.get("risk_reward", 0),
            trade.get("promotion_phase", "PAPER"),
        ))
        db.commit()
        row_id = cursor.lastrowid
        db.close()
        return row_id

    def get_trades(self, where: str = "1=1", params: tuple = (), order: str = "entry_time DESC", limit: int = 10000) -> List[Dict]:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        rows = db.execute(f"SELECT * FROM trades WHERE {where} ORDER BY {order} LIMIT ?", params + (limit,)).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def get_trade_count(self, where: str = "1=1", params: tuple = ()) -> int:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        count = db.execute(f"SELECT COUNT(*) FROM trades WHERE {where}", params).fetchone()[0]
        db.close()
        return count

    def get_period_summaries(self, period: str, limit: int = 90) -> List[Dict]:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT * FROM periodic_summary WHERE period=? ORDER BY period_start DESC LIMIT ?",
            (period, limit)
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def record_period_summary(self, summary: Dict) -> int:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        # Filter to only columns that exist in the table
        valid_cols = {
            "period", "period_start", "period_end", "total_signals", "total_trades",
            "winning_trades", "losing_trades", "net_pnl", "gross_pnl", "total_fees",
            "total_funding", "total_slippage", "profit_factor", "sharpe", "sortino",
            "calmar", "recovery_factor", "cagr", "max_drawdown_pct", "win_rate",
            "expectancy", "avg_r_multiple", "avg_hold_minutes", "avg_slippage_bps",
            "avg_fee_bps", "avg_mae_pct", "avg_mfe_pct", "capital_utilization",
            "equity_start", "equity_end", "peak_equity", "days", "deviation_from_bt",
        }
        filtered = {k: v for k, v in summary.items() if k in valid_cols}
        cols = list(filtered.keys())
        vals = list(filtered.values())
        placeholders = ",".join(["?"] * len(cols))
        col_str = ",".join(cols)
        cursor = db.execute(f"INSERT INTO periodic_summary ({col_str}) VALUES ({placeholders})", vals)
        db.commit()
        row_id = cursor.lastrowid
        db.close()
        return row_id

    def record_alert(self, alert: Dict) -> int:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        cursor = db.execute("""
            INSERT INTO deviation_alerts (
                timestamp, alert_type, metric_name, expected_value,
                actual_value, deviation_pct, sample_size, severity, message
            ) VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            alert["timestamp"], alert["alert_type"], alert["metric_name"],
            alert["expected_value"], alert["actual_value"], alert["deviation_pct"],
            alert["sample_size"], alert["severity"], alert["message"],
        ))
        db.commit()
        row_id = cursor.lastrowid
        db.close()
        return row_id

    def record_promotion(self, record: Dict) -> int:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        cursor = db.execute("""
            INSERT INTO promotion_log (timestamp, from_phase, to_phase, trigger_metric, trigger_value, met_criteria, approved)
            VALUES (?,?,?,?,?,?,?)
        """, (
            record["timestamp"], record["from_phase"], record["to_phase"],
            record.get("trigger_metric", ""), record.get("trigger_value", 0),
            json.dumps(record.get("met_criteria", {})), record.get("approved", 0),
        ))
        db.commit()
        row_id = cursor.lastrowid
        db.close()
        return row_id

    def get_alerts(self, limit: int = 50) -> List[Dict]:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM deviation_alerts ORDER BY timestamp DESC LIMIT ?", (limit,)).fetchall()
        db.close()
        return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════
# METRICS ENGINE
# ══════════════════════════════════════════════════════════════════════

class MetricsEngine:
    """Compute all performance metrics from a list of trades."""

    @staticmethod
    def compute(trades: List[Dict], equity_start: float = 10000.0) -> Dict:
        """Compute comprehensive metrics from trade list."""
        if not trades:
            return {"total_trades": 0, "status": "NO_DATA"}

        n = len(trades)
        pnls = [t.get("net_profit", 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gp = sum(wins) if wins else 0
        gl = abs(sum(losses)) if losses else 0.001

        # Core
        net_pnl = sum(pnls)
        pf = gp / gl if gl > 0 else 999
        wr = len(wins) / n * 100
        exp = net_pnl / n
        gross_pnl = sum(t.get("gross_profit", 0) for t in trades)
        total_fees = sum(t.get("fees", 0) for t in trades)
        total_funding = sum(t.get("funding", 0) for t in trades)
        total_slippage = sum(t.get("slippage_bps", 0) for t in trades)

        # R-multiples
        r_vals = [t.get("r_multiple", 0) for t in trades]
        avg_r = statistics.mean(r_vals) if r_vals else 0

        # Holding time
        holds = [t.get("hold_minutes", 0) for t in trades]
        avg_hold = statistics.mean(holds) if holds else 0

        # MAE/MFE
        mae_vals = [t.get("mae_pct", 0) for t in trades]
        mfe_vals = [t.get("mfe_pct", 0) for t in trades]
        avg_mae = statistics.mean(mae_vals) if mae_vals else 0
        avg_mfe = statistics.mean(mfe_vals) if mfe_vals else 0

        # Slippage/Fees
        slip_vals = [t.get("slippage_bps", 0) for t in trades if t.get("slippage_bps", 0) > 0]
        fee_vals = [t.get("fees", 0) for t in trades if t.get("entry_price", 0) > 0]
        avg_slip = statistics.mean(slip_vals) if slip_vals else 0
        avg_fee_bps = 0
        if fee_vals and trades:
            total_position_value = sum(t.get("entry_price", 0) * t.get("position_size", 0) for t in trades if t.get("entry_price", 0) > 0)
            if total_position_value > 0:
                avg_fee_bps = (total_fees / total_position_value) * 10000

        # Equity curve & drawdown
        ec = [equity_start]
        for p in pnls:
            ec.append(ec[-1] + p)
        peak = ec[0]
        max_dd = 0
        for eq in ec:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

        equity_end = ec[-1] if ec else equity_start

        # Returns for Sharpe/Sortino
        rets = []
        for i in range(1, len(ec)):
            if ec[i-1] > 0:
                rets.append((ec[i] - ec[i-1]) / ec[i-1])

        sharpe = 0
        sortino = 0
        if len(rets) > 2:
            std = statistics.stdev(rets)
            if std > 0:
                sharpe = statistics.mean(rets) / std * math.sqrt(365.25 * 24)
            neg_rets = [r for r in rets if r < 0]
            if neg_rets:
                neg_std = statistics.stdev(neg_rets)
                if neg_std > 0:
                    sortino = statistics.mean(rets) / neg_std * math.sqrt(365.25 * 24)

        # Calmar & CAGR
        if len(trades) >= 2:
            first_time = trades[-1].get("entry_time", time.time())
            last_time = trades[0].get("entry_time", time.time())
            days = max((last_time - first_time) / 86400, 1)
            years = days / 365.25
            cagr = (equity_end / equity_start) ** (1 / years) - 1 if equity_end > 0 and years > 0 else 0
            calmar = cagr / (max_dd / 100) if max_dd > 0 else 0
        else:
            cagr = 0
            calmar = 0
            days = 0

        # Recovery Factor
        rf = net_pnl / (max_dd / 100 * equity_start) if max_dd > 0 and equity_start > 0 else 0

        # Deviation from baseline
        deviations = {}
        for metric in ["profit_factor", "sharpe", "calmar", "recovery_factor", "max_drawdown", "expectancy", "win_rate", "avg_r_multiple"]:
            bt_val = BASELINE.get(metric, 0)
            live_val = locals().get(metric.replace("max_drawdown", "max_dd").replace("win_rate", "wr").replace("avg_r_multiple", "avg_r"), 0)
            if bt_val > 0:
                deviations[metric] = round((live_val - bt_val) / abs(bt_val) * 100, 1)

        return {
            "total_trades": n,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "net_pnl": round(net_pnl, 2),
            "gross_pnl": round(gross_pnl, 2),
            "total_fees": round(total_fees, 2),
            "total_funding": round(total_funding, 2),
            "total_slippage": round(total_slippage, 2),
            "profit_factor": round(pf, 2),
            "sharpe": round(sharpe, 2),
            "sortino": round(sortino, 2),
            "calmar": round(calmar, 2),
            "recovery_factor": round(rf, 2),
            "cagr": round(cagr * 100, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "win_rate": round(wr, 1),
            "expectancy": round(exp, 2),
            "avg_r_multiple": round(avg_r, 2),
            "avg_hold_minutes": round(avg_hold, 1),
            "avg_slippage_bps": round(avg_slip, 1),
            "avg_fee_bps": round(avg_fee_bps, 1),
            "avg_mae_pct": round(avg_mae, 2),
            "avg_mfe_pct": round(avg_mfe, 2),
            "equity_start": equity_start,
            "equity_end": round(equity_end, 2),
            "peak_equity": round(peak, 2),
            "deviations": deviations,
            "days": round(days, 1),
        }


# ══════════════════════════════════════════════════════════════════════
# DEVIATION MONITOR
# ══════════════════════════════════════════════════════════════════════

class DeviationMonitor:
    """Check live metrics against baseline thresholds."""

    def __init__(self, db: ValidationDB):
        self.db = db

    def check(self, metrics: Dict) -> List[Dict]:
        """Check all metrics against thresholds. Returns alerts."""
        alerts = []
        n = metrics.get("total_trades", 0)

        checks = {
            "profit_factor":   metrics.get("profit_factor", 0),
            "sharpe":          metrics.get("sharpe", 0),
            "calmar":          metrics.get("calmar", 0),
            "recovery_factor": metrics.get("recovery_factor", 0),
            "max_drawdown":    metrics.get("max_drawdown_pct", 0),
            "expectancy":      metrics.get("expectancy", 0),
            "avg_slippage":    metrics.get("avg_slippage_bps", 0),
            "win_rate":        metrics.get("win_rate", 0),
            "avg_r_multiple":  metrics.get("avg_r_multiple", 0),
        }

        # Fee check: actual vs expected
        bt_fee_bps = BASELINE.get("avg_fee_bps", 6.0)
        live_fee_bps = metrics.get("avg_fee_bps", 0)
        if bt_fee_bps > 0 and live_fee_bps > 0:
            checks["total_fees"] = live_fee_bps

        for metric_name, live_val in checks.items():
            if metric_name not in DEVIATION_THRESHOLDS:
                continue
            thresh = DEVIATION_THRESHOLDS[metric_name]
            bt_val = BASELINE.get(metric_name, 0)

            # Map metric names to baseline keys
            if metric_name == "max_drawdown":
                bt_val = BASELINE.get("max_drawdown_pct", 0)
            elif metric_name == "win_rate":
                bt_val = BASELINE.get("win_rate_pct", 0)
            elif metric_name == "avg_r_multiple":
                bt_val = BASELINE.get("avg_r_multiple", 0)
            elif metric_name == "avg_slippage":
                bt_val = BASELINE.get("avg_slippage_bps", 0)
            elif metric_name == "total_fees":
                bt_val = BASELINE.get("avg_fee_bps", 0)

            if bt_val == 0 or n < thresh["min_trades"]:
                continue

            dev_pct = (live_val - bt_val) / abs(bt_val) * 100

            if thresh["direction"] == "below":
                warn = dev_pct < -thresh["warn"]
                crit = dev_pct < -thresh["critical"]
            else:
                warn = dev_pct > thresh["warn"]
                crit = dev_pct > thresh["critical"]

            if crit:
                severity = "critical"
            elif warn:
                severity = "warning"
            else:
                continue

            alert = {
                "timestamp": time.time(),
                "alert_type": f"{metric_name}_{severity}",
                "metric_name": metric_name,
                "expected_value": round(bt_val, 2),
                "actual_value": round(live_val, 2),
                "deviation_pct": round(dev_pct, 1),
                "sample_size": n,
                "severity": severity,
                "message": f"{metric_name}: live={live_val:.2f} vs bt={bt_val:.2f} ({dev_pct:+.1f}%) [n={n}]",
            }
            alerts.append(alert)
            self.db.record_alert(alert)

        return alerts


# ══════════════════════════════════════════════════════════════════════
# PROMOTION MANAGER
# ══════════════════════════════════════════════════════════════════════

class PromotionManager:
    """Manage paper → small → medium → full promotion gates."""

    PHASE_ORDER = ["PAPER", "SMALL", "MEDIUM", "FULL"]

    def __init__(self, db: ValidationDB):
        self.db = db

    def check_eligibility(self, current_phase: str, metrics: Dict) -> Dict:
        """Check if eligible for next phase."""
        idx = self.PHASE_ORDER.index(current_phase) if current_phase in self.PHASE_ORDER else 0
        if idx >= len(self.PHASE_ORDER) - 1:
            return {"eligible": False, "reason": "Already at FULL phase"}

        next_phase = self.PHASE_ORDER[idx + 1]
        reqs = PROMOTION_PHASES[next_phase]

        criteria = {}
        all_met = True

        # Trade count
        n = metrics.get("total_trades", 0)
        criteria["trades"] = {"value": n, "required": reqs["min_trades"], "met": n >= reqs["min_trades"]}
        if n < reqs["min_trades"]: all_met = False

        # Days (approximate from trade timestamps)
        days = metrics.get("days", 0)
        criteria["days"] = {"value": days, "required": reqs["min_days"], "met": days >= reqs["min_days"]}
        if days < reqs["min_days"]: all_met = False

        # Profit Factor
        pf = metrics.get("profit_factor", 0)
        criteria["profit_factor"] = {"value": pf, "required": reqs["min_pf"], "met": pf >= reqs["min_pf"]}
        if pf < reqs["min_pf"]: all_met = False

        # Max Drawdown
        dd = metrics.get("max_drawdown_pct", 100)
        criteria["max_drawdown"] = {"value": dd, "required": reqs["max_dd"], "met": dd <= reqs["max_dd"]}
        if dd > reqs["max_dd"]: all_met = False

        # Sharpe (additional gate)
        sharpe = metrics.get("sharpe", 0)
        min_sharpe = 1.0 if next_phase == "SMALL" else 1.5 if next_phase == "MEDIUM" else 2.0
        criteria["sharpe"] = {"value": sharpe, "required": min_sharpe, "met": sharpe >= min_sharpe}
        if sharpe < min_sharpe: all_met = False

        return {
            "eligible": all_met,
            "current_phase": current_phase,
            "next_phase": next_phase,
            "criteria": criteria,
            "risk_pct": reqs["risk_pct"],
        }

    def promote(self, current_phase: str, metrics: Dict) -> Optional[str]:
        """Attempt promotion. Returns new phase or None."""
        check = self.check_eligibility(current_phase, metrics)
        if not check["eligible"]:
            return None

        next_phase = check["next_phase"]

        record = {
            "timestamp": time.time(),
            "from_phase": current_phase,
            "to_phase": next_phase,
            "trigger_metric": "all_criteria_met",
            "trigger_value": metrics.get("profit_factor", 0),
            "met_criteria": {k: v for k, v in check["criteria"].items()},
            "approved": 1,
        }
        self.db.record_promotion(record)

        logger.info("🚀 PROMOTION: {} → {} | PF={:.2f} Sharpe={:.2f} DD={:.1f}%",
                     current_phase, next_phase,
                     metrics.get("profit_factor", 0),
                     metrics.get("sharpe", 0),
                     metrics.get("max_drawdown_pct", 0))

        return next_phase


# ══════════════════════════════════════════════════════════════════════
# PAPER TRADER (Enhanced)
# ══════════════════════════════════════════════════════════════════════

class PaperTrader:
    """
    Paper Trading Validator — Observation-only.
    
    Records every EMA_V5 signal with 23 fields.
    Tracks MFE/MAE/R/Slippage/Fees/Funding.
    """

    FEE_RATE = 0.0006
    SLIPPAGE_BPS = 2.0
    MAX_HOLD_HOURS = 24
    RISK_PCT = 0.01

    def __init__(self, capital: float = 10000.0, risk_pct: float = 0.01):
        self.db = ValidationDB()
        self.capital = capital
        self.peak_equity = capital
        self.risk_pct = risk_pct
        self._positions: Dict[str, Dict] = {}
        self._phase = "PAPER"
        self._start_time = time.time()
        logger.info("📝 PaperTrader initialized | Capital=${:,.2f} | Risk={:.1f}% | Phase={}",
                     capital, risk_pct * 100, self._phase)

    def on_signal(self, signal: Dict) -> Optional[int]:
        """Record signal and open paper position."""
        symbol = signal.get("symbol", "")
        side = signal.get("side", "")
        entry = signal.get("entry", 0)
        sl = signal.get("sl", 0)
        tp = signal.get("tp1", signal.get("tp", 0))
        rr = signal.get("rr", 0)
        conf = signal.get("conf", signal.get("confidence", 0))
        regime = signal.get("regime", "")
        score = signal.get("score", 0)
        atr = signal.get("atr", 0)

        if not symbol or not side or entry <= 0 or sl <= 0:
            return None
        if symbol in self._positions:
            return None

        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            return None

        risk_amount = self.capital * self.risk_pct
        size = risk_amount / sl_dist
        slippage = entry * self.SLIPPAGE_BPS / 10000
        actual_entry = entry + slippage if side == "LONG" else entry - slippage
        fee = actual_entry * size * self.FEE_RATE
        expected_profit = rr * risk_amount

        pos = {
            "symbol": symbol, "direction": side,
            "entry_price": actual_entry, "entry_time": time.time(),
            "stop_loss": sl, "take_profit": tp, "risk_reward": rr,
            "size": size, "cost": fee,
            "current_price": actual_entry,
            "mae_price": actual_entry, "mfe_price": actual_entry,
            "mae_pct": 0, "mfe_pct": 0,
            "hold_minutes": 0, "remaining": size,
            "signal_confidence": conf, "market_regime": regime,
            "atr_entry": atr, "atr_exit": 0,
            "expected_profit": expected_profit,
            "bt_entry_price": entry,
        }
        self._positions[symbol] = pos

        signal_id = self.db.get_trade_count() + 1
        logger.info("📝 PAPER: {} {} @ {:.4f} SL={:.4f} RR={:.1f} conf={:.1f} atr={:.1f}",
                     side, symbol, actual_entry, sl, rr, conf, atr)
        return signal_id

    def on_price_update(self, symbol: str, bid: float, ask: float) -> Optional[Dict]:
        """Update MFE/MAE and check exits."""
        if symbol not in self._positions:
            return None

        pos = self._positions[symbol]
        mid = (bid + ask) / 2
        pos["current_price"] = mid
        pos["hold_minutes"] = (time.time() - pos["entry_time"]) / 60.0

        sl_dist = abs(pos["entry_price"] - pos["stop_loss"])
        if pos["direction"] == "LONG":
            diff = mid - pos["entry_price"]
        else:
            diff = pos["entry_price"] - mid
        pos["r_current"] = diff / sl_dist if sl_dist > 0 else 0

        # MAE/MFE
        if pos["direction"] == "LONG":
            if mid >= pos["mfe_price"] or pos["mfe_price"] == pos["entry_price"]:
                pos["mfe_price"] = mid
                pos["mfe_pct"] = (mid - pos["entry_price"]) / pos["entry_price"] * 100
            if mid <= pos["mae_price"] or pos["mae_price"] == pos["entry_price"]:
                pos["mae_price"] = mid
                pos["mae_pct"] = (pos["entry_price"] - mid) / pos["entry_price"] * 100
        else:
            if mid <= pos["mfe_price"] or pos["mfe_price"] == pos["entry_price"]:
                pos["mfe_price"] = mid
                pos["mfe_pct"] = (pos["entry_price"] - mid) / pos["entry_price"] * 100
            if mid >= pos["mae_price"] or pos["mae_price"] == pos["entry_price"]:
                pos["mae_price"] = mid
                pos["mae_pct"] = (mid - pos["entry_price"]) / pos["entry_price"] * 100

        # Exit checks
        exit_reason = None
        exit_price = mid
        slippage = mid * self.SLIPPAGE_BPS / 10000

        if pos["direction"] == "LONG":
            if bid <= pos["stop_loss"]:
                exit_reason, exit_price = "SL", pos["stop_loss"] - slippage
            elif ask >= pos["take_profit"]:
                exit_reason, exit_price = "TP", pos["take_profit"]
            elif pos["hold_minutes"] >= self.MAX_HOLD_HOURS * 60:
                exit_reason, exit_price = "TIME", mid
        else:
            if ask >= pos["stop_loss"]:
                exit_reason, exit_price = "SL", pos["stop_loss"] + slippage
            elif bid <= pos["take_profit"]:
                exit_reason, exit_price = "TP", pos["take_profit"]
            elif pos["hold_minutes"] >= self.MAX_HOLD_HOURS * 60:
                exit_reason, exit_price = "TIME", mid

        if exit_reason:
            return self._close_trade(symbol, exit_price, exit_reason)
        return None

    def on_trade_closed(self, symbol: str, exit_price: float, reason: str) -> Optional[Dict]:
        """External close."""
        if symbol not in self._positions:
            return None
        return self._close_trade(symbol, exit_price, reason)

    def _close_trade(self, symbol: str, exit_price: float, reason: str) -> Dict:
        pos = self._positions[symbol]
        slippage_bps = self.SLIPPAGE_BPS

        if pos["direction"] == "LONG":
            actual_exit = exit_price - exit_price * slippage_bps / 10000
            gross = (actual_exit - pos["entry_price"]) * pos["remaining"]
        else:
            actual_exit = exit_price + exit_price * slippage_bps / 10000
            gross = (pos["entry_price"] - actual_exit) * pos["remaining"]

        exit_fee = actual_exit * pos["remaining"] * self.FEE_RATE
        total_fees = pos["cost"] + exit_fee
        net = gross - total_fees

        sl_dist = abs(pos["entry_price"] - pos["stop_loss"])
        r_mult = gross / (sl_dist * pos["remaining"]) if sl_dist > 0 else 0

        # ATR at exit (approximate from last known)
        atr_exit = pos.get("atr_entry", 0)

        # Backtest comparison
        bt_entry = pos["bt_entry_price"]
        if reason == "SL":
            bt_exit = pos["stop_loss"]
        elif reason == "TP":
            bt_exit = pos["take_profit"]
        else:
            bt_exit = actual_exit
        if pos["direction"] == "LONG":
            bt_pnl = (bt_exit - bt_entry) * pos["remaining"] - pos["cost"]
        else:
            bt_pnl = (bt_entry - bt_exit) * pos["remaining"] - pos["cost"]
        dev_pct = abs(net - bt_pnl) / abs(bt_pnl) * 100 if abs(bt_pnl) > 0.01 else 0

        self.capital += net
        self.peak_equity = max(self.peak_equity, self.capital)

        trade = {
            "timestamp": time.time(),
            "exchange": EXCHANGE,
            "symbol": symbol,
            "direction": pos["direction"],
            "entry_price": pos["entry_price"],
            "entry_time": pos["entry_time"],
            "exit_price": actual_exit,
            "exit_time": time.time(),
            "exit_reason": reason,
            "position_size": pos["remaining"],
            "fees": total_fees,
            "funding": 0,
            "slippage_bps": slippage_bps,
            "mfe_pct": pos["mfe_pct"],
            "mae_pct": pos["mae_pct"],
            "mfe_price": pos["mfe_price"],
            "mae_price": pos["mae_price"],
            "hold_minutes": pos["hold_minutes"],
            "gross_profit": gross,
            "net_profit": net,
            "r_multiple": r_mult,
            "atr_entry": pos.get("atr_entry", 0),
            "atr_exit": atr_exit,
            "expected_profit": pos.get("expected_profit", 0),
            "actual_profit": net,
            "signal_confidence": pos.get("signal_confidence", 0),
            "market_regime": pos.get("market_regime", ""),
            "strategy_version": STRATEGY_VERSION,
            "bt_entry_price": bt_entry,
            "bt_exit_price": actual_exit,
            "bt_pnl": bt_pnl,
            "deviation_pct": dev_pct,
            "outcome": "win" if net > 0 else "loss",
            "stop_loss": pos["stop_loss"],
            "take_profit": pos["take_profit"],
            "risk_reward": pos["risk_reward"],
            "promotion_phase": self._phase,
        }
        self.db.record_trade(trade)

        del self._positions[symbol]

        emoji = "✅" if net > 0 else "❌"
        logger.info("📝 {} {} {} @ {:.4f} | PnL=${:+.2f} R={:+.1f} | {} | Equity=${:,.2f}",
                     emoji, pos["direction"], symbol, actual_exit, net, r_mult, reason, self.capital)

        self._write_bridge(trade)
        return trade

    def _write_bridge(self, trade: Dict) -> None:
        try:
            bridge_file = BRIDGE_PATH / "paper_trading.json"
            bridge_file.parent.mkdir(parents=True, exist_ok=True)
            data = {"trades": [], "stats": {}, "equity": self.capital, "phase": self._phase}
            if bridge_file.exists():
                try:
                    with open(bridge_file) as f:
                        data = json.load(f)
                except Exception:
                    pass
            data["trades"].append({k: v for k, v in trade.items() if k not in ("bt_pnl", "bt_entry_price", "bt_exit_price")})
            data["trades"] = data["trades"][-500:]
            data["equity"] = self.capital
            data["phase"] = self._phase
            data["last_update"] = time.time()
            with open(bridge_file, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.error("Bridge write failed: {}", e)

    def get_status(self) -> Dict:
        return {
            "phase": self._phase,
            "capital": self.capital,
            "peak_equity": self.peak_equity,
            "drawdown_pct": (self.peak_equity - self.capital) / self.peak_equity * 100 if self.peak_equity > 0 else 0,
            "open_positions": len(self._positions),
            "active_symbols": list(self._positions.keys()),
            "total_signals": self.db.get_trade_count(),
            "total_trades": self.db.get_trade_count(where="exit_time > 0"),
            "uptime_hours": round((time.time() - self._start_time) / 3600, 1),
        }


# ══════════════════════════════════════════════════════════════════════
# REPORT GENERATOR (All report types)
# ══════════════════════════════════════════════════════════════════════

class ReportGenerator:
    """Generate all validation report types."""

    def __init__(self):
        self.db = ValidationDB()
        self.metrics_engine = MetricsEngine()
        self.deviation_monitor = DeviationMonitor(self.db)
        self.promotion_manager = PromotionManager(self.db)

    def _get_period_trades(self, period: str) -> Tuple[List[Dict], str, str]:
        """Get trades for a specific period."""
        now = time.time()
        if period == "daily":
            start = now - 86400
            period_start = time.strftime("%Y-%m-%d", time.localtime(start))
            period_end = time.strftime("%Y-%m-%d")
        elif period == "weekly":
            start = now - 7 * 86400
            period_start = time.strftime("%Y-%m-%d", time.localtime(start))
            period_end = time.strftime("%Y-%m-%d")
        elif period == "monthly":
            start = now - 30 * 86400
            period_start = time.strftime("%Y-%m-%d", time.localtime(start))
            period_end = time.strftime("%Y-%m-%d")
        else:  # all
            start = 0
            period_start = "beginning"
            period_end = time.strftime("%Y-%m-%d")

        trades = self.db.get_trades(where="entry_time > ?", params=(start,))
        return trades, period_start, period_end

    def generate(self, period: str = "daily") -> Dict:
        """Generate report for any period."""
        trades, period_start, period_end = self._get_period_trades(period)
        if not trades:
            return {"status": "NO_DATA", "period": period, "message": f"No trades in {period}"}

        metrics = self.metrics_engine.compute(trades)
        alerts = self.deviation_monitor.check(metrics)

        # Per-symbol breakdown
        sym_stats = {}
        for t in trades:
            s = t["symbol"]
            if s not in sym_stats:
                sym_stats[s] = {"trades": 0, "wins": 0, "pnl": 0, "r_total": 0}
            sym_stats[s]["trades"] += 1
            if t.get("net_profit", 0) > 0:
                sym_stats[s]["wins"] += 1
            sym_stats[s]["pnl"] += t.get("net_profit", 0)
            sym_stats[s]["r_total"] += t.get("r_multiple", 0)
        for s in sym_stats:
            st = sym_stats[s]
            st["win_rate"] = round(st["wins"] / st["trades"] * 100, 1) if st["trades"] > 0 else 0
            st["avg_r"] = round(st["r_total"] / st["trades"], 2) if st["trades"] > 0 else 0

        # Side breakdown
        longs = [t for t in trades if t.get("direction") == "LONG"]
        shorts = [t for t in trades if t.get("direction") == "SHORT"]

        # Regime breakdown
        regime_stats = {}
        for t in trades:
            r = t.get("market_regime", "unknown")
            if r not in regime_stats:
                regime_stats[r] = {"trades": 0, "pnl": 0}
            regime_stats[r]["trades"] += 1
            regime_stats[r]["pnl"] += t.get("net_profit", 0)

        # Save period summary
        summary = {
            "period": period,
            "period_start": period_start,
            "period_end": period_end,
            **{k: v for k, v in metrics.items() if k != "deviations"},
            "deviation_from_bt": statistics.mean(abs(v) for v in metrics.get("deviations", {}).values()) if metrics.get("deviations") else 0,
        }
        self.db.record_period_summary(summary)

        return {
            "status": "OK",
            "period": period,
            "period_start": period_start,
            "period_end": period_end,
            "metrics": metrics,
            "alerts": alerts,
            "top_symbols": sorted(sym_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)[:10],
            "bottom_symbols": sorted(sym_stats.items(), key=lambda x: x[1]["pnl"])[:5],
            "longs": {"count": len(longs), "pnl": round(sum(t.get("net_profit", 0) for t in longs), 2)},
            "shorts": {"count": len(shorts), "pnl": round(sum(t.get("net_profit", 0) for t in shorts), 2)},
            "regime_breakdown": regime_stats,
            "baseline_comparison": self._compare_baseline(metrics),
            "promotion": self.promotion_manager.check_eligibility("PAPER", metrics),
            "recent_trades": [
                {
                    "symbol": t["symbol"], "dir": t.get("direction", ""),
                    "entry": t.get("entry_price", 0), "exit": t.get("exit_price", 0),
                    "pnl": round(t.get("net_profit", 0), 2),
                    "r": round(t.get("r_multiple", 0), 1),
                    "reason": t.get("exit_reason", ""),
                    "mae": round(t.get("mae_pct", 0), 2),
                    "mfe": round(t.get("mfe_pct", 0), 2),
                    "hold": round(t.get("hold_minutes", 0), 0),
                }
                for t in trades[-20:]
            ],
        }

    def _compare_baseline(self, metrics: Dict) -> Dict:
        comp = {}
        for key, bt_val in BASELINE.items():
            if bt_val == 0 or key == "strategy_version":
                continue
            live_val = metrics.get(key, metrics.get(key.replace("_pct", ""), 0))
            if isinstance(live_val, (int, float)) and bt_val != 0:
                dev = (live_val - bt_val) / abs(bt_val) * 100
                if key == "max_drawdown_pct":
                    status = "✅" if live_val <= bt_val * 1.2 else "⚠️" if live_val <= bt_val * 1.5 else "❌"
                else:
                    status = "✅" if live_val >= bt_val * 0.85 else "⚠️" if live_val >= bt_val * 0.70 else "❌"
                comp[key] = {"live": round(live_val, 2), "backtest": round(bt_val, 2), "deviation_pct": round(dev, 1), "status": status}
        return comp

    def print_report(self, report: Dict) -> None:
        if report.get("status") == "NO_DATA":
            print(f"\n  ⏳ No trades for {report.get('period', 'unknown')} period.\n")
            return

        m = report["metrics"]
        p = report["period"].upper()

        print(f"\n{'═' * 100}")
        print(f"  EMA_V5 {p} VALIDATION REPORT — {report['period_start']} to {report['period_end']}")
        print(f"{'═' * 100}")

        print(f"\n  {'Metric':<30} {'Live':>12} {'Backtest':>12} {'Dev':>10} {'Status':>8}")
        print(f"  {'─' * 78}")
        for k, v in report.get("baseline_comparison", {}).items():
            name = k.replace("_", " ").title()
            print(f"  {name:<30} {v['live']:>12.2f} {v['backtest']:>12.2f} {v['deviation_pct']:>+9.1f}% {v['status']:>8}")

        print(f"\n  Summary:")
        print(f"    Net PnL:        ${m.get('net_pnl', 0):>+.2f}")
        print(f"    Gross PnL:      ${m.get('gross_pnl', 0):>+.2f}")
        print(f"    Total Fees:     ${m.get('total_fees', 0):.2f}")
        print(f"    Total Funding:  ${m.get('total_funding', 0):.2f}")
        print(f"    Expectancy:     ${m.get('expectancy', 0):.2f}/trade")
        print(f"    Avg R:          {m.get('avg_r_multiple', 0):.2f}R")
        print(f"    Avg Hold:       {m.get('avg_hold_minutes', 0):.0f} min")
        print(f"    Avg MAE:        {m.get('avg_mae_pct', 0):.2f}%")
        print(f"    Avg MFE:        {m.get('avg_mfe_pct', 0):.2f}%")
        print(f"    Avg Slippage:   {m.get('avg_slippage_bps', 0):.1f} bps")
        print(f"    Equity:         ${m.get('equity_end', 0):,.2f}")

        print(f"\n  Side: LONG={report['longs']['count']} (${report['longs']['pnl']:+.2f}) | SHORT={report['shorts']['count']} (${report['shorts']['pnl']:+.2f})")

        if report.get("top_symbols"):
            print(f"\n  Top 5:")
            for sym, st in report["top_symbols"][:5]:
                print(f"    {sym:<18} trades={st['trades']:>3} WR={st['win_rate']:.0f}% PnL=${st['pnl']:>+.2f} avgR={st['avg_r']:.1f}")

        if report.get("alerts"):
            print(f"\n  ⚠️  ALERTS:")
            for a in report["alerts"]:
                icon = "🔴" if a["severity"] == "critical" else "🟡"
                print(f"    {icon} {a['message']}")

        promo = report.get("promotion", {})
        if promo.get("eligible"):
            print(f"\n  🚀 ELIGIBLE FOR PROMOTION: {promo['current_phase']} → {promo['next_phase']}")
        elif promo.get("next_phase"):
            print(f"\n  🔒 Not yet eligible for {promo['next_phase']}:")
            for k, v in promo.get("criteria", {}).items():
                if not v.get("met"):
                    print(f"    ❌ {k}: {v['value']:.2f} (need {v['required']:.2f})")

        if report.get("recent_trades"):
            print(f"\n  Last 10 Trades:")
            for t in report["recent_trades"][-10:]:
                emoji = "✅" if t["pnl"] > 0 else "❌"
                print(f"    {emoji} {t['dir']:<6} {t['symbol']:<16} PnL=${t['pnl']:>+.2f} R={t['r']:+.1f} {t['reason']:<6} MAE={t['mae']:.1f}% MFE={t['mfe']:.1f}% hold={t['hold']:.0f}m")

        print(f"\n{'═' * 100}")

    def export_to_bridge(self, report: Dict) -> None:
        bridge_file = BRIDGE_PATH / "validation_report.json"
        bridge_file.parent.mkdir(parents=True, exist_ok=True)
        with open(bridge_file, "w") as f:
            json.dump(report, f, indent=2, default=str)


# ══════════════════════════════════════════════════════════════════════
# VALIDATION ENGINE (Main orchestrator)
# ══════════════════════════════════════════════════════════════════════

class ValidationEngine:
    """
    Main orchestrator for production validation.
    
    Usage:
        engine = ValidationEngine()
        
        # On signal:
        engine.on_signal(signal_dict)
        
        # On price tick:
        engine.on_price_update(symbol, bid, ask)
        
        # On trade close:
        engine.on_trade_closed(symbol, exit_price, reason)
        
        # Generate reports:
        daily = engine.report("daily")
        weekly = engine.report("weekly")
        monthly = engine.report("monthly")
        portfolio = engine.report("all")
        
        # Check promotions:
        engine.check_promotion()
    """

    def __init__(self, capital: float = 10000.0, risk_pct: float = 0.01):
        self.trader = PaperTrader(capital, risk_pct)
        self.reporter = ReportGenerator()
        self.promotion = PromotionManager(self.trader.db)
        self._last_daily = ""
        self._last_weekly = ""
        self._last_monthly = ""
        logger.info("✅ ValidationEngine initialized")

    def on_signal(self, signal: Dict) -> Optional[int]:
        return self.trader.on_signal(signal)

    def on_price_update(self, symbol: str, bid: float, ask: float) -> Optional[Dict]:
        return self.trader.on_price_update(symbol, bid, ask)

    def on_trade_closed(self, symbol: str, exit_price: float, reason: str) -> Optional[Dict]:
        return self.trader.on_trade_closed(symbol, exit_price, reason)

    def report(self, period: str = "daily") -> Dict:
        """Generate report for any period: daily, weekly, monthly, all."""
        r = self.reporter.generate(period)
        self.reporter.export_to_bridge(r)
        return r

    def check_promotion(self) -> Optional[str]:
        """Check if eligible for phase promotion."""
        trades = self.trader.db.get_trades(where="exit_time > 0")
        metrics = MetricsEngine.compute(trades)
        new_phase = self.promotion.promote(self.trader._phase, metrics)
        if new_phase:
            self.trader._phase = new_phase
            self.trader.risk_pct = PROMOTION_PHASES[new_phase]["risk_pct"]
            logger.info("🚀 Promoted to {} | New risk: {:.2f}%", new_phase, self.trader.risk_pct * 100)
        return new_phase

    def maybe_auto_reports(self) -> None:
        """Auto-generate reports at period boundaries."""
        now_str = time.strftime("%Y-%m-%d")
        week_str = time.strftime("%Y-W%W")

        if now_str != self._last_daily:
            r = self.report("daily")
            if r.get("status") == "OK":
                self._last_daily = now_str
                logger.info("📊 Daily report generated")

        if week_str != self._last_weekly:
            r = self.report("weekly")
            if r.get("status") == "OK":
                self._last_weekly = week_str
                logger.info("📊 Weekly report generated")

    def get_status(self) -> Dict:
        return {
            "phase": self.trader._phase,
            "capital": self.trader.capital,
            "peak_equity": self.trader.peak_equity,
            "drawdown_pct": (self.trader.peak_equity - self.trader.capital) / self.trader.peak_equity * 100 if self.trader.peak_equity > 0 else 0,
            "open_positions": len(self.trader._positions),
            "total_trades": self.trader.db.get_trade_count(where="exit_time > 0"),
            "baseline": BASELINE,
        }


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    period = sys.argv[1] if len(sys.argv) > 1 else "daily"
    engine = ValidationEngine()
    report = engine.report(period)
    engine.reporter.print_report(report)
