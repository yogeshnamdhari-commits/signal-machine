#!/usr/bin/env python3
"""
Paper Trading Validator — Production Validation Phase 1

Records every live EMA_V5 signal and simulates paper execution.
Tracks: Entry, Exit, MFE, MAE, R-Multiple, Holding Time, Net PnL.

DO NOT modify any signal logic. This is observation-only.

Usage:
    from validation.paper_trader import PaperTrader
    trader = PaperTrader()
    trader.on_signal(signal_dict)      # When scanner emits
    trader.on_priceUpdate(symbol, price) # Every tick
    trader.on_trade_closed(symbol, exit_price, reason)
"""
from __future__ import annotations

import json
import time
import sqlite3
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any
from loguru import logger

# ══════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "validation.db"
BRIDGE_PATH = Path(__file__).resolve().parent.parent / "data" / "bridge"
FEE_RATE = 0.0006        # 6 bps taker fee
SLIPPAGE_BPS = 2          # 2 bps assumed slippage
MAX_HOLD_HOURS = 24       # Maximum holding period
MIN_HOLD_MINUTES = 20     # Minimum hold (from forensic audit)
RISK_PCT = 0.01           # 1% account risk per trade (Phase 1 paper)
STARTING_CAPITAL = 10000  # Paper trading capital


# ══════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════════

@dataclass
class PaperPosition:
    """Active paper position."""
    signal_id: int
    symbol: str
    side: str            # LONG / SHORT
    entry_price: float
    entry_time: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    size: float          # Position size in base asset
    cost: float          # Entry fee + slippage
    # Tracking
    current_price: float = 0.0
    mae_price: float = 0.0
    mfe_price: float = 0.0
    mae_pct: float = 0.0
    mfe_pct: float = 0.0
    unrealized_pnl: float = 0.0
    unrealized_r: float = 0.0
    hold_minutes: float = 0.0
    # Exit state
    tp1_hit: bool = False
    tp2_hit: bool = False
    be_moved: bool = False
    remaining: float = 0.0
    locked_profit: float = 0.0


@dataclass
class ClosedTrade:
    """Completed paper trade for recording."""
    signal_id: int
    symbol: str
    side: str
    entry_price: float
    entry_time: float
    exit_price: float
    exit_time: float
    exit_reason: str
    size: float
    pnl: float
    fees: float
    net_pnl: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    realized_r: float
    hold_minutes: float
    mae_pct: float
    mfe_pct: float
    mae_price: float
    mfe_price: float
    regime: str
    session: str
    confidence: float
    score: float
    # Backtest comparison fields
    bt_entry_price: float = 0.0
    bt_exit_price: float = 0.0
    bt_pnl: float = 0.0
    deviation_pct: float = 0.0


# ══════════════════════════════════════════════════════════════════════
# DATABASE LAYER
# ══════════════════════════════════════════════════════════════════════

class ValidationDB:
    """SQLite persistence for paper trading validation data."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        db = sqlite3.connect(str(self.db_path), timeout=10)

        # Paper trades table
        db.execute("""
            CREATE TABLE IF NOT EXISTS paper_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                entry_price REAL NOT NULL,
                entry_time REAL NOT NULL,
                exit_price REAL DEFAULT 0,
                exit_time REAL DEFAULT 0,
                exit_reason TEXT DEFAULT '',
                size REAL DEFAULT 0,
                pnl REAL DEFAULT 0,
                fees REAL DEFAULT 0,
                net_pnl REAL DEFAULT 0,
                stop_loss REAL DEFAULT 0,
                take_profit REAL DEFAULT 0,
                risk_reward REAL DEFAULT 0,
                realized_r REAL DEFAULT 0,
                hold_minutes REAL DEFAULT 0,
                mae_pct REAL DEFAULT 0,
                mfe_pct REAL DEFAULT 0,
                mae_price REAL DEFAULT 0,
                mfe_price REAL DEFAULT 0,
                regime TEXT DEFAULT '',
                session TEXT DEFAULT '',
                confidence REAL DEFAULT 0,
                score REAL DEFAULT 0,
                bt_entry_price REAL DEFAULT 0,
                bt_exit_price REAL DEFAULT 0,
                bt_pnl REAL DEFAULT 0,
                deviation_pct REAL DEFAULT 0,
                outcome TEXT DEFAULT '',
                strategy_version TEXT DEFAULT 'ema_v5_v2f',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Daily summary table
        db.execute("""
            CREATE TABLE IF NOT EXISTS daily_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL UNIQUE,
                total_signals INTEGER DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                winning_trades INTEGER DEFAULT 0,
                losing_trades INTEGER DEFAULT 0,
                net_pnl REAL DEFAULT 0,
                profit_factor REAL DEFAULT 0,
                sharpe REAL DEFAULT 0,
                calmar REAL DEFAULT 0,
                recovery_factor REAL DEFAULT 0,
                max_drawdown_pct REAL DEFAULT 0,
                win_rate REAL DEFAULT 0,
                expectancy REAL DEFAULT 0,
                avg_r_multiple REAL DEFAULT 0,
                avg_hold_minutes REAL DEFAULT 0,
                mae_avg REAL DEFAULT 0,
                mfe_avg REAL DEFAULT 0,
                deviation_from_bt REAL DEFAULT 0,
                equity REAL DEFAULT 0,
                peak_equity REAL DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)

        # Deviation alerts table
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

        # Indexes
        db.execute("CREATE INDEX IF NOT EXISTS idx_pt_symbol ON paper_trades(symbol)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_pt_time ON paper_trades(entry_time)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_pt_outcome ON paper_trades(outcome)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ds_date ON daily_summary(date)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_da_type ON deviation_alerts(alert_type)")

        db.commit()
        db.close()
        logger.info("ValidationDB initialized at {}", self.db_path)

    def record_trade(self, trade: ClosedTrade) -> int:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        outcome = "win" if trade.net_pnl > 0 else "loss"
        cursor = db.execute("""
            INSERT INTO paper_trades (
                signal_id, symbol, side, entry_price, entry_time,
                exit_price, exit_time, exit_reason, size, pnl, fees, net_pnl,
                stop_loss, take_profit, risk_reward, realized_r,
                hold_minutes, mae_pct, mfe_pct, mae_price, mfe_price,
                regime, session, confidence, score,
                bt_entry_price, bt_exit_price, bt_pnl, deviation_pct, outcome
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade.signal_id, trade.symbol, trade.side,
            trade.entry_price, trade.entry_time,
            trade.exit_price, trade.exit_time, trade.exit_reason,
            trade.size, trade.pnl, trade.fees, trade.net_pnl,
            trade.stop_loss, trade.take_profit, trade.risk_reward, trade.realized_r,
            trade.hold_minutes, trade.mae_pct, trade.mfe_pct,
            trade.mae_price, trade.mfe_price,
            trade.regime, trade.session, trade.confidence, trade.score,
            trade.bt_entry_price, trade.bt_exit_price, trade.bt_pnl,
            trade.deviation_pct, outcome,
        ))
        db.commit()
        row_id = cursor.lastrowid
        db.close()
        return row_id

    def record_daily_summary(self, summary: Dict) -> int:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        cursor = db.execute("""
            INSERT OR REPLACE INTO daily_summary (
                date, total_signals, total_trades, winning_trades, losing_trades,
                net_pnl, profit_factor, sharpe, calmar, recovery_factor,
                max_drawdown_pct, win_rate, expectancy, avg_r_multiple,
                avg_hold_minutes, mae_avg, mfe_avg, deviation_from_bt,
                equity, peak_equity
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            summary["date"], summary["total_signals"], summary["total_trades"],
            summary["winning_trades"], summary["losing_trades"],
            summary["net_pnl"], summary["profit_factor"], summary["sharpe"],
            summary["calmar"], summary["recovery_factor"],
            summary["max_drawdown_pct"], summary["win_rate"], summary["expectancy"],
            summary["avg_r_multiple"], summary["avg_hold_minutes"],
            summary["mae_avg"], summary["mfe_avg"], summary["deviation_from_bt"],
            summary["equity"], summary["peak_equity"],
        ))
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            alert["timestamp"], alert["alert_type"], alert["metric_name"],
            alert["expected_value"], alert["actual_value"],
            alert["deviation_pct"], alert["sample_size"],
            alert["severity"], alert["message"],
        ))
        db.commit()
        row_id = cursor.lastrowid
        db.close()
        return row_id

    def get_recent_trades(self, limit: int = 100) -> List[Dict]:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT * FROM paper_trades ORDER BY entry_time DESC LIMIT ?", (limit,)
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def get_open_positions(self) -> List[Dict]:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT * FROM paper_trades WHERE exit_time = 0 OR exit_reason = '' ORDER BY entry_time DESC"
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def get_all_trades(self) -> List[Dict]:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM paper_trades WHERE exit_time > 0 ORDER BY entry_time ASC").fetchall()
        db.close()
        return [dict(r) for r in rows]

    def get_daily_summaries(self, limit: int = 30) -> List[Dict]:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT * FROM daily_summary ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def get_trade_count(self) -> int:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        count = db.execute("SELECT COUNT(*) FROM paper_trades WHERE exit_time > 0").fetchone()[0]
        db.close()
        return count

    def get_signal_count(self) -> int:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        count = db.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]
        db.close()
        return count


# ══════════════════════════════════════════════════════════════════════
# PAPER TRADER
# ══════════════════════════════════════════════════════════════════════

class PaperTrader:
    """
    Paper Trading Validator — Observation-only system.
    
    Records every EMA_V5 signal and tracks it through to closure.
    Computes MFE, MAE, R-Multiple, and compares with backtest expectations.
    
    Usage:
        trader = PaperTrader()
        
        # When scanner emits a signal:
        trader.on_signal(signal_dict)
        
        # On every price tick:
        trader.on_price_update(symbol, bid, ask)
        
        # When trade is closed (by signal engine or timeout):
        trader.on_trade_closed(symbol, exit_price, reason)
        
        # End of day:
        summary = trader.generate_daily_report()
    """

    def __init__(self, capital: float = STARTING_CAPITAL, risk_pct: float = RISK_PCT):
        self.db = ValidationDB()
        self.capital = capital
        self.peak_equity = capital
        self.risk_pct = risk_pct
        self._positions: Dict[str, PaperPosition] = {}
        self._equity_curve: List[float] = [capital]
        self._daily_pnl: Dict[str, float] = {}
        self._start_time = time.time()
        logger.info("📝 PaperTrader initialized | Capital=${:,.2f} | Risk={:.1f}%".format(
            capital, risk_pct * 100))

    # ── Signal Reception ──

    def on_signal(self, signal: Dict[str, Any]) -> Optional[int]:
        """
        Called when the EMA_V5 scanner emits a signal.
        
        signal keys: symbol, side, entry, sl, tp1, tp2, tp3, rr, conf, regime, score, atr
        """
        symbol = signal.get("symbol", "")
        side = signal.get("side", "")
        entry = signal.get("entry", 0)
        sl = signal.get("sl", 0)
        tp = signal.get("tp1", signal.get("tp", 0))
        rr = signal.get("rr", 0)
        conf = signal.get("conf", signal.get("confidence", 0))
        regime = signal.get("regime", "")
        score = signal.get("score", 0)

        if not symbol or not side or entry <= 0 or sl <= 0:
            return None

        # Skip if already have position in this symbol
        if symbol in self._positions:
            logger.debug("SKIP {} — already have position", symbol)
            return None

        # Compute position size (risk-based)
        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            return None

        risk_amount = self.capital * self.risk_pct
        size = risk_amount / sl_dist

        # Apply slippage to entry
        slippage = entry * SLIPPAGE_BPS / 10000
        actual_entry = entry + slippage if side == "LONG" else entry - slippage

        # Compute fees
        fee = actual_entry * size * FEE_RATE

        pos = PaperPosition(
            signal_id=0,  # Will be set after DB insert
            symbol=symbol,
            side=side,
            entry_price=actual_entry,
            entry_time=time.time(),
            stop_loss=sl,
            take_profit=tp,
            risk_reward=rr,
            size=size,
            cost=fee,
            current_price=actual_entry,
            mae_price=actual_entry,
            mfe_price=actual_entry,
            remaining=size,
        )

        # Record in DB
        trade = ClosedTrade(
            signal_id=0,
            symbol=symbol,
            side=side,
            entry_price=actual_entry,
            entry_time=time.time(),
            exit_price=0,
            exit_time=0,
            exit_reason="",
            size=size,
            pnl=0,
            fees=fee,
            net_pnl=0,
            stop_loss=sl,
            take_profit=tp,
            risk_reward=rr,
            realized_r=0,
            hold_minutes=0,
            mae_pct=0,
            mfe_pct=0,
            mae_price=actual_entry,
            mfe_price=actual_entry,
            regime=regime,
            session="",
            confidence=conf,
            score=score,
            bt_entry_price=entry,
        )
        signal_id = self.db.record_trade(trade)
        pos.signal_id = signal_id

        self._positions[symbol] = pos

        logger.info("📝 PAPER ENTRY: {} {} @ {:.4f} SL={:.4f} RR={:.1f} conf={:.1f} | id={}",
                     side, symbol, actual_entry, sl, rr, conf, signal_id)
        return signal_id

    # ── Price Updates ──

    def on_price_update(self, symbol: str, bid: float, ask: float) -> Optional[Dict]:
        """
        Called on every price tick. Updates MFE/MAE and checks exit conditions.
        Returns exit dict if trade closed, None otherwise.
        """
        if symbol not in self._positions:
            return None

        pos = self._positions[symbol]
        mid = (bid + ask) / 2
        pos.current_price = mid
        pos.hold_minutes = (time.time() - pos.entry_time) / 60.0

        # Compute unrealized PnL and R
        if pos.side == "LONG":
            price_diff = mid - pos.entry_price
        else:
            price_diff = pos.entry_price - mid

        sl_dist = abs(pos.entry_price - pos.stop_loss)
        pos.unrealized_r = price_diff / sl_dist if sl_dist > 0 else 0
        pos.unrealized_pnl = price_diff * pos.size

        # Update MAE/MFE
        if pos.side == "LONG":
            if mid > pos.mfe_price or pos.mfe_price == pos.entry_price:
                pos.mfe_price = mid
                pos.mfe_pct = (mid - pos.entry_price) / pos.entry_price * 100
            if mid < pos.mae_price or pos.mae_price == pos.entry_price:
                pos.mae_price = mid
                pos.mae_pct = (pos.entry_price - mid) / pos.entry_price * 100
        else:
            if mid < pos.mfe_price or pos.mfe_price == pos.entry_price:
                pos.mfe_price = mid
                pos.mfe_pct = (pos.entry_price - mid) / pos.entry_price * 100
            if mid > pos.mae_price or pos.mae_price == pos.entry_price:
                pos.mae_price = mid
                pos.mae_pct = (mid - pos.entry_price) / pos.entry_price * 100

        # ── Exit Logic (mirrors V2f optimizer) ──
        exit_reason = None
        exit_price = mid

        if pos.side == "LONG":
            # Hard stop loss
            if bid <= pos.stop_loss:
                exit_reason = "SL"
                exit_price = pos.stop_loss - slippage
            # Take profit 3
            elif ask >= pos.take_profit:
                exit_reason = "TP3"
                exit_price = pos.take_profit
            # Time exit
            elif pos.hold_minutes >= MAX_HOLD_HOURS * 60:
                exit_reason = "TIME"
                exit_price = mid
        else:
            # Hard stop loss
            if ask >= pos.stop_loss:
                exit_reason = "SL"
                exit_price = pos.stop_loss + slippage
            # Take profit 3
            elif bid <= pos.take_profit:
                exit_reason = "TP3"
                exit_price = pos.take_profit
            # Time exit
            elif pos.hold_minutes >= MAX_HOLD_HOURS * 60:
                exit_reason = "TIME"
                exit_price = mid

        if exit_reason:
            return self._close_trade(symbol, exit_price, exit_reason)

        return None

    # ── Trade Closure ──

    def on_trade_closed(self, symbol: str, exit_price: float, reason: str) -> Optional[Dict]:
        """Called externally when the signal engine closes a trade."""
        if symbol not in self._positions:
            return None
        return self._close_trade(symbol, exit_price, reason)

    def _close_trade(self, symbol: str, exit_price: float, reason: str) -> Dict:
        """Internal: close a position and record the result."""
        pos = self._positions[symbol]

        # Apply slippage to exit
        slippage = exit_price * SLIPPAGE_BPS / 10000
        if pos.side == "LONG":
            actual_exit = exit_price - slippage
        else:
            actual_exit = exit_price + slippage

        # Compute PnL
        if pos.side == "LONG":
            raw_pnl = (actual_exit - pos.entry_price) * pos.remaining
        else:
            raw_pnl = (pos.entry_price - actual_exit) * pos.remaining

        # Exit fees
        exit_fee = actual_exit * pos.remaining * FEE_RATE
        total_fees = pos.cost + exit_fee
        net_pnl = raw_pnl - total_fees

        # Realized R
        sl_dist = abs(pos.entry_price - pos.stop_loss)
        realized_r = raw_pnl / (sl_dist * pos.remaining) if sl_dist > 0 else 0

        # Update equity
        self.capital += net_pnl
        self.peak_equity = max(self.peak_equity, self.capital)
        self._equity_curve.append(self.capital)

        # Daily PnL tracking
        day_str = time.strftime("%Y-%m-%d", time.localtime(pos.entry_time))
        self._daily_pnl[day_str] = self._daily_pnl.get(day_str, 0) + net_pnl

        # Compute deviation from backtest
        bt_pnl = 0
        deviation = 0
        if pos.risk_reward > 0 and sl_dist > 0:
            # Backtest would have used clean entry at signal price
            bt_entry = pos.entry_price  # Approximate
            if reason == "SL":
                bt_exit = pos.stop_loss
            elif reason == "TP3":
                bt_exit = pos.take_profit
            else:
                bt_exit = actual_exit

            if pos.side == "LONG":
                bt_pnl = (bt_exit - bt_entry) * pos.size - pos.cost
            else:
                bt_pnl = (bt_entry - bt_exit) * pos.size - pos.cost

            if abs(bt_pnl) > 0.01:
                deviation = abs(net_pnl - bt_pnl) / abs(bt_pnl) * 100

        # Build result
        result = {
            "symbol": symbol,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "exit_price": actual_exit,
            "exit_reason": reason,
            "pnl": raw_pnl,
            "fees": total_fees,
            "net_pnl": net_pnl,
            "realized_r": realized_r,
            "hold_minutes": pos.hold_minutes,
            "mae_pct": pos.mae_pct,
            "mfe_pct": pos.mfe_pct,
            "equity": self.capital,
            "bt_pnl": bt_pnl,
            "deviation_pct": deviation,
        }

        # Update DB record
        db = sqlite3.connect(str(self.db.db_path), timeout=10)
        outcome = "win" if net_pnl > 0 else "loss"
        db.execute("""
            UPDATE paper_trades SET
                exit_price=?, exit_time=?, exit_reason=?, pnl=?, fees=?, net_pnl=?,
                realized_r=?, hold_minutes=?, mae_pct=?, mfe_pct=?,
                mae_price=?, mfe_price=?, bt_pnl=?, deviation_pct=?, outcome=?
            WHERE id=?
        """, (
            actual_exit, time.time(), reason, raw_pnl, total_fees, net_pnl,
            realized_r, pos.hold_minutes, pos.mae_pct, pos.mfe_pct,
            pos.mae_price, pos.mfe_price, bt_pnl, deviation, outcome,
            pos.signal_id,
        ))
        db.commit()
        db.close()

        # Remove from active positions
        del self._positions[symbol]

        emoji = "✅" if net_pnl > 0 else "❌"
        logger.info("📝 PAPER EXIT: {} {} {} @ {:.4f} | PnL=${:+.2f} R={:+.1f} | {} | Equity=${:,.2f}",
                     emoji, pos.side, symbol, actual_exit, net_pnl, realized_r,
                     reason, self.capital)

        # Write to bridge for dashboard
        self._write_bridge_update(result)

        return result

    # ── Bridge Updates ──

    def _write_bridge_update(self, trade_result: Dict) -> None:
        """Write latest trade to bridge for dashboard consumption."""
        try:
            bridge_file = BRIDGE_PATH / "paper_trading.json"
            bridge_file.parent.mkdir(parents=True, exist_ok=True)

            # Load existing or create new
            data = {"trades": [], "stats": {}, "equity": self.capital}
            if bridge_file.exists():
                try:
                    with open(bridge_file) as f:
                        data = json.load(f)
                except Exception:
                    pass

            # Append latest trade (keep last 200)
            data["trades"].append(trade_result)
            data["trades"] = data["trades"][-200:]

            # Update stats
            all_trades = data["trades"]
            if all_trades:
                pnls = [t["net_pnl"] for t in all_trades]
                wins = [p for p in pnls if p > 0]
                losses = [p for p in pnls if p <= 0]
                gp = sum(wins) if wins else 0
                gl = abs(sum(losses)) if losses else 1
                data["stats"] = {
                    "total_trades": len(all_trades),
                    "winning": len(wins),
                    "losing": len(losses),
                    "win_rate": len(wins) / len(all_trades) * 100,
                    "net_pnl": sum(pnls),
                    "profit_factor": gp / gl if gl > 0 else 999,
                    "avg_pnl": sum(pnls) / len(pnls),
                    "equity": self.capital,
                    "peak_equity": self.peak_equity,
                    "drawdown_pct": (self.peak_equity - self.capital) / self.peak_equity * 100,
                    "last_update": time.time(),
                }

            with open(bridge_file, "w") as f:
                json.dump(data, f, indent=2, default=str)

        except Exception as e:
            logger.error("Failed to write bridge update: {}", e)

    # ── Daily Report ──

    def generate_daily_report(self, date_str: Optional[str] = None) -> Dict:
        """
        Generate daily validation report.
        Returns summary dict and saves to DB.
        """
        if date_str is None:
            date_str = time.strftime("%Y-%m-%d")

        trades = self.db.get_all_trades()

        # Filter to this day
        day_start = time.mktime(time.strptime(date_str, "%Y-%m-%d"))
        day_end = day_start + 86400
        day_trades = [t for t in trades if day_start <= t.get("entry_time", 0) < day_end]

        # Compute metrics
        total = len(day_trades)
        if total == 0:
            return {"date": date_str, "total_trades": 0, "message": "No trades today"}

        pnls = [t["net_pnl"] for t in day_trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gp = sum(wins) if wins else 0
        gl = abs(sum(losses)) if losses else 1

        r_multiples = [t["realized_r"] for t in day_trades]
        hold_times = [t["hold_minutes"] for t in day_trades]
        mae_vals = [t["mae_pct"] for t in day_trades]
        mfe_vals = [t["mfe_pct"] for t in day_trades]
        deviations = [t["deviation_pct"] for t in day_trades if t.get("deviation_pct", 0) > 0]

        # Cumulative equity for this day
        equity = self.capital
        peak = equity
        max_dd = 0
        running = equity - sum(pnls)  # Start from beginning-of-day equity
        for p in pnls:
            running += p
            peak = max(peak, running)
            dd = (peak - running) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

        summary = {
            "date": date_str,
            "total_signals": total,
            "total_trades": total,
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "net_pnl": sum(pnls),
            "profit_factor": gp / gl if gl > 0 else 999,
            "sharpe": 0,  # Needs multiple days
            "calmar": 0,
            "recovery_factor": 0,
            "max_drawdown_pct": max_dd,
            "win_rate": len(wins) / total * 100,
            "expectancy": sum(pnls) / total,
            "avg_r_multiple": sum(r_multiples) / len(r_multiples) if r_multiples else 0,
            "avg_hold_minutes": sum(hold_times) / len(hold_times) if hold_times else 0,
            "mae_avg": sum(mae_vals) / len(mae_vals) if mae_vals else 0,
            "mfe_avg": sum(mfe_vals) / len(mfe_vals) if mfe_vals else 0,
            "deviation_from_bt": sum(deviations) / len(deviations) if deviations else 0,
            "equity": self.capital,
            "peak_equity": self.peak_equity,
        }

        self.db.record_daily_summary(summary)

        logger.info("📊 DAILY REPORT {}: {} trades | PnL=${:+.2f} | PF={:.2f} | WR={:.1f}% | Dev={:.1f}%",
                     date_str, total, summary["net_pnl"], summary["profit_factor"],
                     summary["win_rate"], summary["deviation_from_bt"])

        return summary

    # ── Status ──

    def get_status(self) -> Dict:
        """Get current paper trading status."""
        return {
            "capital": self.capital,
            "peak_equity": self.peak_equity,
            "drawdown_pct": (self.peak_equity - self.capital) / self.peak_equity * 100 if self.peak_equity > 0 else 0,
            "open_positions": len(self._positions),
            "active_symbols": list(self._positions.keys()),
            "total_signals": self.db.get_signal_count(),
            "total_trades": self.db.get_trade_count(),
            "uptime_hours": (time.time() - self._start_time) / 3600,
        }
