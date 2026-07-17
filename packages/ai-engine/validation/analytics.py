#!/usr/bin/env python3
"""
EMA_V5 Production Analytics — Evidence-Based Trading Intelligence.

CODE FREEZE: NO strategy changes. This is observation and analysis only.

Priority 1: Signal Journal — Record every trade with 23+ fields
Priority 2: Evidence Dashboard — Performance metrics, not just counts
Priority 3: Confidence Calibration — Validate confidence vs outcomes
Priority 4: Weakness Detection — Auto-identify underperforming patterns
Priority 5: Promotion Gates — Statistical validation before live

All modules read from the existing validation.db trades table.
No new database tables required for core analytics.
"""
from __future__ import annotations

import json
import time
import sqlite3
import statistics
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from loguru import logger


# ══════════════════════════════════════════════════════════════════════
# PATHS
# ══════════════════════════════════════════════════════════════════════

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "validation.db"
BRIDGE_PATH = Path(__file__).resolve().parent.parent / "data" / "bridge"


# ══════════════════════════════════════════════════════════════════════
# PRIORITY 1: SIGNAL JOURNAL
# ══════════════════════════════════════════════════════════════════════

class SignalJournal:
    """
    Every executed trade automatically records:
    
    DateTime | Symbol | Direction | Entry | SL | TP | Exit | PnL |
    Fees | Funding | Slippage | Confidence | Regime |
    HTF Trend | Liquidity Sweep | Order Block | FVG | OI | Funding | CVD |
    ATR | Reason for Entry | Reason for Exit |
    Expected Profit | Actual Profit | R Multiple | MFE | MAE |
    Gross PnL | Net PnL | Holding Time
    
    This is the ground truth for all optimization.
    """

    # Extended fields beyond the base trades table
    EXTENDED_FIELDS = [
        "htf_trend",           # Higher timeframe trend (1h, 4h, daily)
        "liquidity_sweep",     # Whether liquidity sweep preceded entry
        "order_block",         # Whether order block present
        "fvg",                 # Fair value gap present
        "oi_change",           # Open interest change at entry
        "cvd",                 # Cumulative volume delta
        "reason_entry",        # Human-readable entry reason
        "reason_exit",         # Human-readable exit reason
        "session",             # Trading session (asia/london/new_york/overlap)
        "day_of_week",         # Day of week
        "hour_of_day",         # Hour of day (UTC)
    ]

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._ensure_extended_columns()

    def _ensure_extended_columns(self) -> None:
        """Add extended journal columns to trades table if missing."""
        db = sqlite3.connect(str(self.db_path), timeout=10)
        migrations = [
            ("trades", "htf_trend", "TEXT DEFAULT ''"),
            ("trades", "liquidity_sweep", "INTEGER DEFAULT 0"),
            ("trades", "order_block", "INTEGER DEFAULT 0"),
            ("trades", "fvg", "INTEGER DEFAULT 0"),
            ("trades", "oi_change", "REAL DEFAULT 0"),
            ("trades", "cvd", "REAL DEFAULT 0"),
            ("trades", "reason_entry", "TEXT DEFAULT ''"),
            ("trades", "reason_exit", "TEXT DEFAULT ''"),
            ("trades", "session", "TEXT DEFAULT ''"),
            ("trades", "day_of_week", "TEXT DEFAULT ''"),
            ("trades", "hour_of_day", "INTEGER DEFAULT 0"),
        ]
        for table, col, typedef in migrations:
            try:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass
        db.commit()
        db.close()

    def record_signal(self, trade: Dict) -> None:
        """Record a trade with extended journal fields."""
        db = sqlite3.connect(str(self.db_path), timeout=10)
        
        # Compute session from entry time
        entry_time = trade.get("entry_time", time.time())
        hour = time.gmtime(entry_time).tm_hour
        session = self._classify_session(hour)
        dow = time.strftime("%A", time.localtime(entry_time))
        
        # Build extended fields
        extended = {
            "htf_trend": trade.get("htf_trend", ""),
            "liquidity_sweep": 1 if trade.get("liquidity_sweep") else 0,
            "order_block": 1 if trade.get("order_block") else 0,
            "fvg": 1 if trade.get("fvg") else 0,
            "oi_change": trade.get("oi_change", 0),
            "cvd": trade.get("cvd", 0),
            "reason_entry": trade.get("reason_entry", ""),
            "reason_exit": trade.get("reason_exit", trade.get("exit_reason", "")),
            "session": session,
            "day_of_week": dow,
            "hour_of_day": hour,
        }
        
        # Update existing trade record
        set_clauses = ", ".join(f"{k}=?" for k in extended.keys())
        values = list(extended.values())
        
        # Find the most recent trade for this symbol
        cursor = db.execute(
            f"UPDATE trades SET {set_clauses} WHERE id=(SELECT MAX(id) FROM trades WHERE symbol=?)",
            values + [trade.get("symbol", "")]
        )
        db.commit()
        db.close()
        
        logger.debug("📝 Journal: {} updated with session={} dow={}", 
                     trade.get("symbol", ""), session, dow)

    def _classify_session(self, hour_utc: int) -> str:
        """Classify trading session from UTC hour."""
        if 0 <= hour_utc < 8:
            return "asia"
        elif 8 <= hour_utc < 14:
            return "london"
        elif 14 <= hour_utc < 21:
            return "new_york"
        else:
            return "overlap"

    def get_journal(self, limit: int = 100, symbol: str = "", 
                    session: str = "", side: str = "") -> List[Dict]:
        """Retrieve signal journal entries with all fields."""
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        
        where_parts = ["1=1"]
        params = []
        
        if symbol:
            where_parts.append("symbol=?")
            params.append(symbol)
        if session:
            where_parts.append("session=?")
            params.append(session)
        if side:
            where_parts.append("direction=?")
            params.append(side)
        
        where_str = " AND ".join(where_parts)
        rows = db.execute(
            f"SELECT * FROM trades WHERE {where_str} ORDER BY entry_time DESC LIMIT ?",
            params + [limit]
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════
# PRIORITY 2: EVIDENCE DASHBOARD
# ══════════════════════════════════════════════════════════════════════

class EvidenceDashboard:
    """
    Instead of displaying "31 Buy 40 Sell 84 Waiting",
    display actionable performance metrics:
    
    Buy Win Rate | Sell Win Rate | Average Hold Time |
    Average Profit | Average Loss | Profit Factor |
    Expectancy | Recovery Factor | Sharpe | Max Drawdown |
    Best/Worst Session | Best/Worst Symbol |
    Best/Worst Confidence Range
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    def get_evidence_metrics(self, lookback_days: int = 30) -> Dict:
        """Generate complete evidence dashboard metrics."""
        trades = self._get_trades(lookback_days)
        if not trades:
            return {"status": "NO_DATA", "message": "No trades in lookback period"}
        
        n = len(trades)
        all_pnls = [t.get("net_profit", 0) for t in trades]
        wins = [p for p in all_pnls if p > 0]
        losses = [p for p in all_pnls if p <= 0]
        
        # ── Direction Breakdown ──
        longs = [t for t in trades if t.get("direction") == "LONG"]
        shorts = [t for t in trades if t.get("direction") == "SHORT"]
        
        long_pnls = [t.get("net_profit", 0) for t in longs]
        short_pnls = [t.get("net_profit", 0) for t in shorts]
        
        long_wins = [p for p in long_pnls if p > 0]
        short_wins = [p for p in short_pnls if p > 0]
        
        buy_wr = len(long_wins) / len(longs) * 100 if longs else 0
        sell_wr = len(short_wins) / len(shorts) * 100 if shorts else 0
        
        # ── Profit/Loss Averages ──
        avg_profit = statistics.mean(wins) if wins else 0
        avg_loss = statistics.mean(losses) if losses else 0
        
        # ── Holding Time ──
        holds = [t.get("hold_minutes", 0) for t in trades if t.get("hold_minutes", 0) > 0]
        avg_hold = statistics.mean(holds) if holds else 0
        
        # ── Session Performance ──
        session_stats = defaultdict(lambda: {"trades": 0, "pnl": 0, "wins": 0})
        for t in trades:
            s = t.get("session", "unknown")
            session_stats[s]["trades"] += 1
            session_stats[s]["pnl"] += t.get("net_profit", 0)
            if t.get("net_profit", 0) > 0:
                session_stats[s]["wins"] += 1
        
        for s in session_stats:
            st = session_stats[s]
            st["win_rate"] = st["wins"] / st["trades"] * 100 if st["trades"] > 0 else 0
            st["avg_pnl"] = st["pnl"] / st["trades"] if st["trades"] > 0 else 0
        
        best_session = max(session_stats.items(), key=lambda x: x[1]["pnl"]) if session_stats else ("N/A", {})
        worst_session = min(session_stats.items(), key=lambda x: x[1]["pnl"]) if session_stats else ("N/A", {})
        
        # ── Symbol Performance ──
        sym_stats = defaultdict(lambda: {"trades": 0, "pnl": 0, "wins": 0})
        for t in trades:
            s = t.get("symbol", "")
            sym_stats[s]["trades"] += 1
            sym_stats[s]["pnl"] += t.get("net_profit", 0)
            if t.get("net_profit", 0) > 0:
                sym_stats[s]["wins"] += 1
        
        for s in sym_stats:
            st = sym_stats[s]
            st["win_rate"] = st["wins"] / st["trades"] * 100 if st["trades"] > 0 else 0
        
        best_sym = max(sym_stats.items(), key=lambda x: x[1]["pnl"]) if sym_stats else ("N/A", {})
        worst_sym = min(sym_stats.items(), key=lambda x: x[1]["pnl"]) if sym_stats else ("N/A", {})
        
        # ── Confidence Range Performance ──
        conf_ranges = [
            (50, 60), (60, 70), (70, 80), (80, 90), (90, 100)
        ]
        conf_stats = {}
        for lo, hi in conf_ranges:
            range_trades = [t for t in trades if lo <= t.get("signal_confidence", 0) < hi]
            if range_trades:
                rp = [t.get("net_profit", 0) for t in range_trades]
                rw = [p for p in rp if p > 0]
                rr_vals = [t.get("r_multiple", 0) for t in range_trades]
                conf_stats[f"{lo}-{hi}"] = {
                    "trades": len(range_trades),
                    "win_rate": len(rw) / len(range_trades) * 100,
                    "avg_r": statistics.mean(rr_vals) if rr_vals else 0,
                    "total_pnl": sum(rp),
                }
        
        best_conf = max(conf_stats.items(), key=lambda x: x[1]["avg_r"]) if conf_stats else ("N/A", {})
        worst_conf = min(conf_stats.items(), key=lambda x: x[1]["avg_r"]) if conf_stats else ("N/A", {})
        
        # ── Equity Curve Metrics ──
        ec = [10000]
        for p in all_pnls:
            ec.append(ec[-1] + p)
        peak = ec[0]
        max_dd = 0
        for eq in ec:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        # Sharpe
        rets = []
        for i in range(1, len(ec)):
            if ec[i-1] > 0:
                rets.append((ec[i] - ec[i-1]) / ec[i-1])
        sharpe = 0
        if len(rets) > 2:
            std = statistics.stdev(rets)
            if std > 0:
                sharpe = statistics.mean(rets) / std * math.sqrt(365.25 * 24)
        
        # PF, RF, Expectancy
        gp = sum(wins) if wins else 0
        gl = abs(sum(losses)) if losses else 0.001
        pf = gp / gl
        rf = sum(all_pnls) / (max_dd / 100 * 10000) if max_dd > 0 else 0
        exp = sum(all_pnls) / n if n > 0 else 0
        
        # ── Regime Performance ──
        regime_stats = defaultdict(lambda: {"trades": 0, "pnl": 0, "wins": 0})
        for t in trades:
            r = t.get("market_regime", "unknown")
            regime_stats[r]["trades"] += 1
            regime_stats[r]["pnl"] += t.get("net_profit", 0)
            if t.get("net_profit", 0) > 0:
                regime_stats[r]["wins"] += 1
        for r in regime_stats:
            st = regime_stats[r]
            st["win_rate"] = st["wins"] / st["trades"] * 100 if st["trades"] > 0 else 0
        
        return {
            "status": "OK",
            "lookback_days": lookback_days,
            "total_trades": n,
            "direction": {
                "buy_win_rate": round(buy_wr, 1),
                "sell_win_rate": round(sell_wr, 1),
                "buy_trades": len(longs),
                "sell_trades": len(shorts),
                "buy_pnl": round(sum(long_pnls), 2),
                "sell_pnl": round(sum(short_pnls), 2),
            },
            "profitability": {
                "net_pnl": round(sum(all_pnls), 2),
                "profit_factor": round(pf, 2),
                "expectancy": round(exp, 2),
                "recovery_factor": round(rf, 2),
                "avg_profit": round(avg_profit, 2),
                "avg_loss": round(avg_loss, 2),
                "win_rate": round(len(wins) / n * 100, 1),
            },
            "risk": {
                "sharpe": round(sharpe, 2),
                "max_drawdown_pct": round(max_dd, 2),
            },
            "timing": {
                "avg_hold_minutes": round(avg_hold, 1),
                "avg_hold_hours": round(avg_hold / 60, 1),
            },
            "sessions": {
                "best": {"name": best_session[0], **{k: round(v, 2) if isinstance(v, float) else v for k, v in best_session[1].items()}} if isinstance(best_session[1], dict) else {},
                "worst": {"name": worst_session[0], **{k: round(v, 2) if isinstance(v, float) else v for k, v in worst_session[1].items()}} if isinstance(worst_session[1], dict) else {},
                "all": {k: {kk: round(vv, 2) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in session_stats.items()},
            },
            "symbols": {
                "best": {"name": best_sym[0], **{k: round(v, 2) if isinstance(v, float) else v for k, v in best_sym[1].items()}} if isinstance(best_sym[1], dict) else {},
                "worst": {"name": worst_sym[0], **{k: round(v, 2) if isinstance(v, float) else v for k, v in worst_sym[1].items()}} if isinstance(worst_sym[1], dict) else {},
                "top_10": sorted(
                    [{"symbol": s, **{k: round(v, 2) if isinstance(v, float) else v for k, v in st.items()}} for s, st in sym_stats.items()],
                    key=lambda x: x["pnl"], reverse=True
                )[:10],
                "bottom_5": sorted(
                    [{"symbol": s, **{k: round(v, 2) if isinstance(v, float) else v for k, v in st.items()}} for s, st in sym_stats.items()],
                    key=lambda x: x["pnl"]
                )[:5],
            },
            "confidence": {
                "ranges": conf_stats,
                "best": {"range": best_conf[0], **{k: round(v, 2) if isinstance(v, float) else v for k, v in best_conf[1].items()}} if isinstance(best_conf[1], dict) else {},
                "worst": {"range": worst_conf[0], **{k: round(v, 2) if isinstance(v, float) else v for k, v in worst_conf[1].items()}} if isinstance(worst_conf[1], dict) else {},
            },
            "regimes": {k: {kk: round(vv, 2) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in regime_stats.items()},
        }

    def _get_trades(self, lookback_days: int) -> List[Dict]:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        cutoff = time.time() - (lookback_days * 86400)
        rows = db.execute(
            "SELECT * FROM trades WHERE entry_time > ? AND exit_time > 0 ORDER BY entry_time ASC",
            (cutoff,)
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def print_evidence(self, metrics: Dict) -> None:
        """Pretty-print the evidence dashboard."""
        if metrics.get("status") == "NO_DATA":
            print("\n  ⏳ No trades for evidence dashboard.\n")
            return
        
        d = metrics["direction"]
        p = metrics["profitability"]
        r = metrics["risk"]
        t = metrics["timing"]
        s = metrics["sessions"]
        sym = metrics["symbols"]
        c = metrics["confidence"]
        reg = metrics.get("regimes", {})
        
        print(f"\n{'═' * 100}")
        print(f"  EMA_V5 EVIDENCE DASHBOARD — {metrics['lookback_days']}d Lookback | {metrics['total_trades']} Trades")
        print(f"{'═' * 100}")
        
        print(f"\n  {'Direction':<30} {'Buy':>12} {'Sell':>12}")
        print(f"  {'─' * 58}")
        print(f"  {'Win Rate':<30} {d['buy_win_rate']:>11.1f}% {d['sell_win_rate']:>11.1f}%")
        print(f"  {'Trades':<30} {d['buy_trades']:>12} {d['sell_trades']:>12}")
        print(f"  {'PnL':<30} ${d['buy_pnl']:>10,.2f} ${d['sell_pnl']:>10,.2f}")
        
        print(f"\n  {'Profitability':<30} {'Value':>12}")
        print(f"  {'─' * 46}")
        print(f"  {'Net PnL':<30} ${p['net_pnl']:>10,.2f}")
        print(f"  {'Profit Factor':<30} {p['profit_factor']:>12.2f}")
        print(f"  {'Expectancy':<30} ${p['expectancy']:>10,.2f}")
        print(f"  {'Recovery Factor':<30} {p['recovery_factor']:>12.2f}")
        print(f"  {'Avg Profit':<30} ${p['avg_profit']:>10,.2f}")
        print(f"  {'Avg Loss':<30} ${p['avg_loss']:>10,.2f}")
        print(f"  {'Win Rate':<30} {p['win_rate']:>11.1f}%")
        
        print(f"\n  {'Risk':<30} {'Value':>12}")
        print(f"  {'─' * 46}")
        print(f"  {'Sharpe':<30} {r['sharpe']:>12.2f}")
        print(f"  {'Max Drawdown':<30} {r['max_drawdown_pct']:>11.1f}%")
        
        print(f"\n  {'Timing':<30} {'Value':>12}")
        print(f"  {'─' * 46}")
        print(f"  {'Avg Hold Time':<30} {t['avg_hold_minutes']:>10.0f} min")
        print(f"  {'Avg Hold Hours':<30} {t['avg_hold_hours']:>11.1f}h")
        
        if s.get("best", {}).get("name"):
            print(f"\n  {'Session':<30} {'Best':>12} {'Worst':>12}")
            print(f"  {'─' * 58}")
            print(f"  {'Name':<30} {s['best']['name']:>12} {s['worst']['name']:>12}")
            print(f"  {'PnL':<30} ${s['best'].get('pnl', 0):>10,.2f} ${s['worst'].get('pnl', 0):>10,.2f}")
            print(f"  {'Win Rate':<30} {s['best'].get('win_rate', 0):>11.1f}% {s['worst'].get('win_rate', 0):>11.1f}%")
        
        if sym.get("best", {}).get("name"):
            print(f"\n  {'Symbol':<30} {'Best':>12} {'Worst':>12}")
            print(f"  {'─' * 58}")
            print(f"  {'Name':<30} {sym['best']['name']:>12} {sym['worst']['name']:>12}")
            print(f"  {'PnL':<30} ${sym['best'].get('pnl', 0):>10,.2f} ${sym['worst'].get('pnl', 0):>10,.2f}")
            print(f"  {'Win Rate':<30} {sym['best'].get('win_rate', 0):>11.1f}% {sym['worst'].get('win_rate', 0):>11.1f}%")
        
        if c.get("best", {}).get("range"):
            print(f"\n  {'Confidence Range':<30} {'Best':>12} {'Worst':>12}")
            print(f"  {'─' * 58}")
            print(f"  {'Range':<30} {c['best']['range']:>12} {c['worst']['range']:>12}")
            print(f"  {'Avg R':<30} {c['best'].get('avg_r', 0):>11.2f}R {c['worst'].get('avg_r', 0):>11.2f}R")
            print(f"  {'Win Rate':<30} {c['best'].get('win_rate', 0):>11.1f}% {c['worst'].get('win_rate', 0):>11.1f}%")
        
        # Confidence breakdown table
        if c.get("ranges"):
            print(f"\n  {'Conf Range':<15} {'Trades':>8} {'Win Rate':>10} {'Avg R':>8} {'Total PnL':>12}")
            print(f"  {'─' * 58}")
            for rng, st in sorted(c["ranges"].items()):
                print(f"  {rng:<15} {st['trades']:>8} {st['win_rate']:>9.1f}% {st['avg_r']:>7.2f}R ${st['total_pnl']:>10,.2f}")
        
        if reg:
            print(f"\n  {'Regime':<20} {'Trades':>8} {'Win Rate':>10} {'PnL':>12}")
            print(f"  {'─' * 54}")
            for r_name, r_st in sorted(reg.items(), key=lambda x: x[1].get("pnl", 0), reverse=True):
                print(f"  {r_name:<20} {r_st.get('trades', 0):>8} {r_st.get('win_rate', 0):>9.1f}% ${r_st.get('pnl', 0):>10,.2f}")
        
        print(f"\n{'═' * 100}")


# ══════════════════════════════════════════════════════════════════════
# PRIORITY 3: CONFIDENCE CALIBRATION
# ══════════════════════════════════════════════════════════════════════

class ConfidenceCalibration:
    """
    Validate whether higher confidence actually corresponds to better outcomes.
    
    If not, recalibrate the scoring rather than assuming higher scores are better.
    
    Expected: Confidence 50-60 → WR=44%, R=0.4R
              Confidence 90-100 → WR=82%, R=3.3R
    """

    # Confidence bins for calibration
    BINS = [
        (50, 60, "50-60"),
        (60, 70, "60-70"),
        (70, 80, "70-80"),
        (80, 90, "80-90"),
        (90, 100, "90-100"),
    ]

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    def calibrate(self, lookback_days: int = 30) -> Dict:
        """Run full confidence calibration analysis."""
        trades = self._get_trades(lookback_days)
        if not trades:
            return {"status": "NO_DATA", "message": "No trades for calibration"}
        
        # Bin trades by confidence
        binned = defaultdict(list)
        all_conf = []
        
        for t in trades:
            conf = t.get("signal_confidence", 0)
            all_conf.append(conf)
            for lo, hi, label in self.BINS:
                if lo <= conf < hi:
                    binned[label].append(t)
                    break
            else:
                # Edge case: conf >= 100
                if conf >= 100:
                    binned["90-100"].append(t)
        
        # Compute per-bin metrics
        calibration = {}
        for lo, hi, label in self.BINS:
            bin_trades = binned.get(label, [])
            if not bin_trades:
                calibration[label] = {
                    "trades": 0, "win_rate": 0, "avg_r": 0,
                    "total_pnl": 0, "avg_pnl": 0, "profit_factor": 0,
                    "avg_mae": 0, "avg_mfe": 0, "avg_hold": 0,
                }
                continue
            
            pnls = [t.get("net_profit", 0) for t in bin_trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            gp = sum(wins) if wins else 0
            gl = abs(sum(losses)) if losses else 0.001
            
            r_vals = [t.get("r_multiple", 0) for t in bin_trades]
            mae_vals = [t.get("mae_pct", 0) for t in bin_trades]
            mfe_vals = [t.get("mfe_pct", 0) for t in bin_trades]
            hold_vals = [t.get("hold_minutes", 0) for t in bin_trades]
            
            calibration[label] = {
                "trades": len(bin_trades),
                "win_rate": round(len(wins) / len(bin_trades) * 100, 1),
                "avg_r": round(statistics.mean(r_vals), 2) if r_vals else 0,
                "total_pnl": round(sum(pnls), 2),
                "avg_pnl": round(statistics.mean(pnls), 2) if pnls else 0,
                "profit_factor": round(gp / gl, 2) if gl > 0 else 999,
                "avg_mae": round(statistics.mean(mae_vals), 2) if mae_vals else 0,
                "avg_mfe": round(statistics.mean(mfe_vals), 2) if mfe_vals else 0,
                "avg_hold": round(statistics.mean(hold_vals), 1) if hold_vals else 0,
            }
        
        # Check calibration quality
        # A well-calibrated model should show monotonically increasing WR and R
        cal_values = [(label, calibration[label]["avg_r"]) for lo, hi, label in self.BINS 
                      if calibration[label]["trades"] > 0]
        
        is_monotonic = True
        if len(cal_values) >= 2:
            for i in range(1, len(cal_values)):
                if cal_values[i][1] < cal_values[i-1][1]:
                    is_monotonic = False
                    break
        
        # Compute correlation between confidence and R
        conf_r_pairs = [(t.get("signal_confidence", 0), t.get("r_multiple", 0)) 
                        for t in trades if t.get("signal_confidence", 0) > 0]
        correlation = 0
        if len(conf_r_pairs) > 5:
            confs = [p[0] for p in conf_r_pairs]
            rs = [p[1] for p in conf_r_pairs]
            if statistics.stdev(confs) > 0 and statistics.stdev(rs) > 0:
                correlation = statistics.correlation(confs, rs) if hasattr(statistics, 'correlation') else 0
        
        # Overall stats
        all_r = [t.get("r_multiple", 0) for t in trades]
        all_wr = len([p for p in [t.get("net_profit", 0) for t in trades] if p > 0]) / len(trades) * 100 if trades else 0
        
        return {
            "status": "OK",
            "lookback_days": lookback_days,
            "total_trades": len(trades),
            "calibration": calibration,
            "is_monotonic": is_monotonic,
            "confidence_r_correlation": round(correlation, 3),
            "overall": {
                "win_rate": round(all_wr, 1),
                "avg_r": round(statistics.mean(all_r), 2) if all_r else 0,
                "median_confidence": round(statistics.median(all_conf), 1) if all_conf else 0,
            },
            "assessment": self._assess_calibration(calibration, is_monotonic, correlation),
        }

    def _assess_calibration(self, calibration: Dict, monotonic: bool, corr: float) -> Dict:
        """Assess whether confidence scoring is well-calibrated."""
        issues = []
        recommendations = []
        
        if not monotonic:
            issues.append("Confidence does not monotonically predict R-multiple")
            recommendations.append("Review confidence weights — some components may not correlate with profitability")
        
        if corr < 0.3:
            issues.append(f"Low confidence-R correlation ({corr:.3f})")
            recommendations.append("Confidence scoring may need recalibration")
        
        # Check if highest bin actually has best performance
        best_bin = max(calibration.items(), key=lambda x: x[1]["avg_r"] if x[1]["trades"] > 5 else -999)
        worst_bin = min(calibration.items(), key=lambda x: x[1]["avg_r"] if x[1]["trades"] > 5 else 999)
        
        if best_bin[0] != "90-100":
            issues.append(f"Best performing confidence range is {best_bin[0]}, not 90-100")
            recommendations.append(f"Consider adjusting min_confidence threshold")
        
        if worst_bin[1]["avg_r"] < -0.5 and worst_bin[1]["trades"] > 10:
            issues.append(f"Confidence range {worst_bin[0]} has significantly negative R ({worst_bin[1]['avg_r']:.2f})")
            recommendations.append(f"Consider disabling signals in {worst_bin[0]} range")
        
        is_calibrated = len(issues) == 0
        
        return {
            "is_calibrated": is_calibrated,
            "issues": issues,
            "recommendations": recommendations,
            "best_bin": best_bin[0],
            "worst_bin": worst_bin[0],
            "score": max(0, 100 - len(issues) * 20 - (0 if monotonic else 15) - max(0, (0.3 - corr) * 100)),
        }

    def _get_trades(self, lookback_days: int) -> List[Dict]:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        cutoff = time.time() - (lookback_days * 86400)
        rows = db.execute(
            "SELECT * FROM trades WHERE entry_time > ? AND exit_time > 0 ORDER BY entry_time ASC",
            (cutoff,)
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def print_calibration(self, result: Dict) -> None:
        """Pretty-print calibration results."""
        if result.get("status") == "NO_DATA":
            print("\n  ⏳ No trades for calibration.\n")
            return
        
        cal = result["calibration"]
        assess = result["assessment"]
        
        print(f"\n{'═' * 100}")
        print(f"  CONFIDENCE CALIBRATION — {result['lookback_days']}d | {result['total_trades']} Trades")
        print(f"{'═' * 100}")
        
        print(f"\n  {'Range':<12} {'Trades':>8} {'Win Rate':>10} {'Avg R':>8} {'PnL':>12} {'PF':>8} {'MAE':>8} {'MFE':>8}")
        print(f"  {'─' * 80}")
        for label in ["50-60", "60-70", "70-80", "80-90", "90-100"]:
            st = cal.get(label, {})
            if st.get("trades", 0) > 0:
                print(f"  {label:<12} {st['trades']:>8} {st['win_rate']:>9.1f}% {st['avg_r']:>7.2f}R ${st['total_pnl']:>10,.2f} {st['profit_factor']:>7.2f} {st['avg_mae']:>7.1f}% {st['avg_mfe']:>7.1f}%")
            else:
                print(f"  {label:<12} {'—':>8}")
        
        print(f"\n  Assessment:")
        print(f"    Monotonic:     {'✅ Yes' if result['is_monotonic'] else '❌ No'}")
        print(f"    Correlation:   {result['confidence_r_correlation']:.3f}")
        print(f"    Best Bin:      {assess['best_bin']}")
        print(f"    Worst Bin:     {assess['worst_bin']}")
        print(f"    Score:         {assess['score']}/100")
        print(f"    Calibrated:    {'✅ Yes' if assess['is_calibrated'] else '❌ No'}")
        
        if assess["issues"]:
            print(f"\n  Issues:")
            for issue in assess["issues"]:
                print(f"    ⚠️  {issue}")
        
        if assess["recommendations"]:
            print(f"\n  Recommendations:")
            for rec in assess["recommendations"]:
                print(f"    → {rec}")
        
        print(f"\n{'═' * 100}")


# ══════════════════════════════════════════════════════════════════════
# PRIORITY 4: AUTOMATIC WEAKNESS DETECTION
# ══════════════════════════════════════════════════════════════════════

class WeaknessDetector:
    """
    Automatically identify patterns such as:
    - BTC performs poorly in ranging markets
    - ETH longs underperform during Asian session
    - SOL shorts lose when funding is strongly positive
    - Certain confidence bands underperform
    - Specific sessions underperform
    - Regime-specific weaknesses
    """

    # Minimum trades for a pattern to be statistically meaningful
    MIN_TRADES = 10
    
    # Threshold for "significantly underperforming"
    UNDERPERFORMANCE_THRESHOLD = -0.5  # R-multiple
    LOW_WR_THRESHOLD = 35  # Win rate %
    HIGH_DD_THRESHOLD = 15  # Max drawdown %

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    def scan(self, lookback_days: int = 30) -> Dict:
        """Scan all trades for weaknesses. Returns identified patterns."""
        trades = self._get_trades(lookback_days)
        if not trades:
            return {"status": "NO_DATA", "weaknesses": [], "strengths": []}
        
        weaknesses = []
        strengths = []
        
        # ── 1. Symbol-Level Weaknesses ──
        sym_stats = self._compute_group_stats(trades, "symbol")
        for sym, stats in sym_stats.items():
            if stats["trades"] >= self.MIN_TRADES:
                if stats["avg_r"] < self.UNDERPERFORMANCE_THRESHOLD:
                    weaknesses.append({
                        "type": "symbol_underperformance",
                        "symbol": sym,
                        "metric": "avg_r",
                        "value": stats["avg_r"],
                        "trades": stats["trades"],
                        "win_rate": stats["win_rate"],
                        "pnl": stats["pnl"],
                        "message": f"{sym} underperforming: avg R={stats['avg_r']:.2f} over {stats['trades']} trades",
                    })
                elif stats["avg_r"] > 1.0:
                    strengths.append({
                        "type": "symbol_strength",
                        "symbol": sym,
                        "metric": "avg_r",
                        "value": stats["avg_r"],
                        "trades": stats["trades"],
                        "message": f"{sym} strong: avg R={stats['avg_r']:.2f} over {stats['trades']} trades",
                    })
        
        # ── 2. Session-Level Weaknesses ──
        session_stats = self._compute_group_stats(trades, "session")
        for sess, stats in session_stats.items():
            if stats["trades"] >= self.MIN_TRADES:
                if stats["avg_r"] < self.UNDERPERFORMANCE_THRESHOLD:
                    weaknesses.append({
                        "type": "session_underperformance",
                        "session": sess,
                        "metric": "avg_r",
                        "value": stats["avg_r"],
                        "trades": stats["trades"],
                        "win_rate": stats["win_rate"],
                        "message": f"Session '{sess}' underperforming: avg R={stats['avg_r']:.2f}",
                    })
        
        # ── 3. Direction × Session Weaknesses ──
        for direction in ["LONG", "SHORT"]:
            dir_trades = [t for t in trades if t.get("direction") == direction]
            dir_session_stats = self._compute_group_stats(dir_trades, "session")
            for sess, stats in dir_session_stats.items():
                if stats["trades"] >= self.MIN_TRADES:
                    if stats["avg_r"] < self.UNDERPERFORMANCE_THRESHOLD:
                        weaknesses.append({
                            "type": "direction_session_weakness",
                            "direction": direction,
                            "session": sess,
                            "value": stats["avg_r"],
                            "trades": stats["trades"],
                            "message": f"{direction} trades in '{sess}' session underperforming: R={stats['avg_r']:.2f}",
                        })
        
        # ── 4. Direction × Symbol Weaknesses ──
        for direction in ["LONG", "SHORT"]:
            dir_trades = [t for t in trades if t.get("direction") == direction]
            dir_sym_stats = self._compute_group_stats(dir_trades, "symbol")
            for sym, stats in dir_sym_stats.items():
                if stats["trades"] >= self.MIN_TRADES:
                    if stats["avg_r"] < self.UNDERPERFORMANCE_THRESHOLD:
                        weaknesses.append({
                            "type": "direction_symbol_weakness",
                            "direction": direction,
                            "symbol": sym,
                            "value": stats["avg_r"],
                            "trades": stats["trades"],
                            "message": f"{direction} trades on {sym} underperforming: R={stats['avg_r']:.2f}",
                        })
        
        # ── 5. Regime-Level Weaknesses ──
        regime_stats = self._compute_group_stats(trades, "market_regime")
        for regime, stats in regime_stats.items():
            if stats["trades"] >= self.MIN_TRADES:
                if stats["avg_r"] < self.UNDERPERFORMANCE_THRESHOLD:
                    weaknesses.append({
                        "type": "regime_weakness",
                        "regime": regime,
                        "value": stats["avg_r"],
                        "trades": stats["trades"],
                        "message": f"Regime '{regime}' underperforming: R={stats['avg_r']:.2f}",
                    })
        
        # ── 6. Confidence Band Weaknesses ──
        conf_bins = [(50, 60), (60, 70), (70, 80), (80, 90), (90, 100)]
        for lo, hi in conf_bins:
            bin_trades = [t for t in trades if lo <= t.get("signal_confidence", 0) < hi]
            if len(bin_trades) >= self.MIN_TRADES:
                stats = self._compute_basic_stats(bin_trades)
                if stats["avg_r"] < self.UNDERPERFORMANCE_THRESHOLD:
                    weaknesses.append({
                        "type": "confidence_weakness",
                        "confidence_range": f"{lo}-{hi}",
                        "value": stats["avg_r"],
                        "trades": stats["trades"],
                        "message": f"Confidence {lo}-{hi} underperforming: R={stats['avg_r']:.2f}",
                    })
        
        # ── 7. Day-of-Week Weaknesses ──
        dow_stats = self._compute_group_stats(trades, "day_of_week")
        for dow, stats in dow_stats.items():
            if stats["trades"] >= self.MIN_TRADES:
                if stats["avg_r"] < self.UNDERPERFORMANCE_THRESHOLD:
                    weaknesses.append({
                        "type": "day_weakness",
                        "day": dow,
                        "value": stats["avg_r"],
                        "trades": stats["trades"],
                        "message": f"{dow} underperforming: R={stats['avg_r']:.2f}",
                    })
        
        # ── 8. Hour-of-Day Weaknesses ──
        hour_stats = self._compute_group_stats(trades, "hour_of_day")
        for hour, stats in hour_stats.items():
            if isinstance(hour, (int, float)) and stats["trades"] >= self.MIN_TRADES:
                if stats["avg_r"] < self.UNDERPERFORMANCE_THRESHOLD:
                    weaknesses.append({
                        "type": "hour_weakness",
                        "hour": int(hour),
                        "value": stats["avg_r"],
                        "trades": stats["trades"],
                        "message": f"Hour {int(hour):02d}:00 UTC underperforming: R={stats['avg_r']:.2f}",
                    })
        
        # Sort weaknesses by severity (most negative R first)
        weaknesses.sort(key=lambda x: x.get("value", 0))
        strengths.sort(key=lambda x: x.get("value", 0), reverse=True)
        
        return {
            "status": "OK",
            "lookback_days": lookback_days,
            "total_trades": len(trades),
            "weaknesses": weaknesses,
            "strengths": strengths[:10],
            "summary": {
                "total_weaknesses": len(weaknesses),
                "total_strengths": len(strengths),
                "severity": "critical" if any(w["value"] < -1.0 for w in weaknesses) else
                           "warning" if weaknesses else "healthy",
            },
        }

    def _compute_group_stats(self, trades: List[Dict], field: str) -> Dict:
        """Compute stats grouped by a field."""
        groups = defaultdict(list)
        for t in trades:
            key = t.get(field, "unknown")
            groups[key].append(t)
        
        result = {}
        for key, group_trades in groups.items():
            result[key] = self._compute_basic_stats(group_trades)
        return result

    def _compute_basic_stats(self, trades: List[Dict]) -> Dict:
        """Compute basic stats for a group of trades."""
        if not trades:
            return {"trades": 0, "pnl": 0, "wins": 0, "win_rate": 0, "avg_r": 0}
        
        pnls = [t.get("net_profit", 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        r_vals = [t.get("r_multiple", 0) for t in trades]
        
        return {
            "trades": len(trades),
            "pnl": round(sum(pnls), 2),
            "wins": len(wins),
            "win_rate": round(len(wins) / len(trades) * 100, 1),
            "avg_r": round(statistics.mean(r_vals), 2) if r_vals else 0,
            "avg_pnl": round(statistics.mean(pnls), 2) if pnls else 0,
        }

    def _get_trades(self, lookback_days: int) -> List[Dict]:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        cutoff = time.time() - (lookback_days * 86400)
        rows = db.execute(
            "SELECT * FROM trades WHERE entry_time > ? AND exit_time > 0 ORDER BY entry_time ASC",
            (cutoff,)
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def print_weaknesses(self, result: Dict) -> None:
        """Pretty-print weakness detection results."""
        if result.get("status") == "NO_DATA":
            print("\n  ⏳ No trades for weakness detection.\n")
            return
        
        print(f"\n{'═' * 100}")
        print(f"  WEAKNESS DETECTION — {result['lookback_days']}d | {result['total_trades']} Trades")
        print(f"  Status: {result['summary']['severity'].upper()}")
        print(f"{'═' * 100}")
        
        if result["weaknesses"]:
            print(f"\n  ⚠️  WEAKNESSES ({len(result['weaknesses'])} found):")
            for w in result["weaknesses"]:
                print(f"    ❌ {w['message']}")
        else:
            print(f"\n  ✅ No weaknesses detected")
        
        if result["strengths"]:
            print(f"\n  💪 STRENGTHS ({len(result['strengths'])} found):")
            for s in result["strengths"][:5]:
                print(f"    ✅ {s['message']}")
        
        print(f"\n{'═' * 100}")


# ══════════════════════════════════════════════════════════════════════
# PRIORITY 5: PROMOTION GATES
# ══════════════════════════════════════════════════════════════════════

class PromotionGates:
    """
    Before a signal is eligible for live trading, require:
    - Positive expectancy
    - Profit Factor above target
    - Recovery Factor above target
    - Max drawdown within limits
    - Sufficient sample size
    - Walk-forward validation passed
    - Paper trading validation passed
    """

    # Gate requirements per phase
    GATES = {
        "PAPER_TO_SMALL": {
            "min_trades": 50,
            "min_days": 14,
            "min_pf": 1.3,
            "min_rf": 500,
            "max_dd": 12.0,
            "min_sharpe": 1.0,
            "min_win_rate": 40.0,
            "min_expectancy": 0,
            "requires_monotonic_calibration": False,
        },
        "SMALL_TO_MEDIUM": {
            "min_trades": 200,
            "min_days": 30,
            "min_pf": 1.4,
            "min_rf": 1000,
            "max_dd": 10.0,
            "min_sharpe": 1.5,
            "min_win_rate": 42.0,
            "min_expectancy": 5.0,
            "requires_monotonic_calibration": True,
        },
        "MEDIUM_TO_FULL": {
            "min_trades": 500,
            "min_days": 60,
            "min_pf": 1.5,
            "min_rf": 2000,
            "max_dd": 8.5,
            "min_sharpe": 2.0,
            "min_win_rate": 45.0,
            "min_expectancy": 10.0,
            "requires_monotonic_calibration": True,
        },
    }

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.calibrator = ConfidenceCalibration(db_path)

    def check_all_gates(self, lookback_days: int = 90) -> Dict:
        """Check all promotion gates. Returns eligibility for each phase."""
        trades = self._get_trades(lookback_days)
        if not trades:
            return {"status": "NO_DATA", "gates": {}}
        
        metrics = self._compute_metrics(trades)
        calibration = self.calibrator.calibrate(lookback_days)
        
        results = {}
        for gate_name, requirements in self.GATES.items():
            results[gate_name] = self._check_gate(gate_name, requirements, metrics, calibration)
        
        # Overall assessment
        current_phase = "PAPER"
        for gate_name in ["PAPER_TO_SMALL", "SMALL_TO_MEDIUM", "MEDIUM_TO_FULL"]:
            if results[gate_name]["eligible"]:
                phase = gate_name.split("_TO_")[-1]
                current_phase = phase
        
        return {
            "status": "OK",
            "lookback_days": lookback_days,
            "total_trades": len(trades),
            "current_phase": current_phase,
            "metrics": metrics,
            "gates": results,
        }

    def _check_gate(self, gate_name: str, reqs: Dict, metrics: Dict, 
                    calibration: Dict) -> Dict:
        """Check a single promotion gate."""
        checks = {}
        all_met = True
        
        # Trade count
        n = metrics.get("total_trades", 0)
        checks["min_trades"] = {"value": n, "required": reqs["min_trades"], 
                                "met": n >= reqs["min_trades"]}
        if n < reqs["min_trades"]: all_met = False
        
        # Profit Factor
        pf = metrics.get("profit_factor", 0)
        checks["min_pf"] = {"value": pf, "required": reqs["min_pf"],
                            "met": pf >= reqs["min_pf"]}
        if pf < reqs["min_pf"]: all_met = False
        
        # Recovery Factor
        rf = metrics.get("recovery_factor", 0)
        checks["min_rf"] = {"value": rf, "required": reqs["min_rf"],
                            "met": rf >= reqs["min_rf"]}
        if rf < reqs["min_rf"]: all_met = False
        
        # Max Drawdown
        dd = metrics.get("max_drawdown_pct", 100)
        checks["max_dd"] = {"value": dd, "required": reqs["max_dd"],
                            "met": dd <= reqs["max_dd"]}
        if dd > reqs["max_dd"]: all_met = False
        
        # Sharpe
        sharpe = metrics.get("sharpe", 0)
        checks["min_sharpe"] = {"value": sharpe, "required": reqs["min_sharpe"],
                                "met": sharpe >= reqs["min_sharpe"]}
        if sharpe < reqs["min_sharpe"]: all_met = False
        
        # Win Rate
        wr = metrics.get("win_rate", 0)
        checks["min_win_rate"] = {"value": wr, "required": reqs["min_win_rate"],
                                  "met": wr >= reqs["min_win_rate"]}
        if wr < reqs["min_win_rate"]: all_met = False
        
        # Expectancy
        exp = metrics.get("expectancy", 0)
        checks["min_expectancy"] = {"value": exp, "required": reqs["min_expectancy"],
                                    "met": exp >= reqs["min_expectancy"]}
        if exp < reqs["min_expectancy"]: all_met = False
        
        # Calibration (if required)
        if reqs.get("requires_monotonic_calibration"):
            mono = calibration.get("is_monotonic", False)
            checks["monotonic_calibration"] = {"value": mono, "required": True, "met": mono}
            if not mono: all_met = False
        
        return {
            "eligible": all_met,
            "gate": gate_name,
            "checks": checks,
            "passed": sum(1 for c in checks.values() if c["met"]),
            "total": len(checks),
        }

    def _compute_metrics(self, trades: List[Dict]) -> Dict:
        """Compute all metrics needed for gate checks."""
        n = len(trades)
        pnls = [t.get("net_profit", 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gp = sum(wins) if wins else 0
        gl = abs(sum(losses)) if losses else 0.001
        
        # Equity curve
        ec = [10000]
        for p in pnls:
            ec.append(ec[-1] + p)
        peak = ec[0]
        max_dd = 0
        for eq in ec:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        # Sharpe
        rets = []
        for i in range(1, len(ec)):
            if ec[i-1] > 0:
                rets.append((ec[i] - ec[i-1]) / ec[i-1])
        sharpe = 0
        if len(rets) > 2:
            std = statistics.stdev(rets)
            if std > 0:
                sharpe = statistics.mean(rets) / std * math.sqrt(365.25 * 24)
        
        # RF
        rf = sum(pnls) / (max_dd / 100 * 10000) if max_dd > 0 else 0
        
        # Days
        if trades:
            first = min(t.get("entry_time", time.time()) for t in trades)
            last = max(t.get("entry_time", time.time()) for t in trades)
            days = max((last - first) / 86400, 1)
        else:
            days = 0
        
        return {
            "total_trades": n,
            "profit_factor": round(gp / gl, 2),
            "recovery_factor": round(rf, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe": round(sharpe, 2),
            "win_rate": round(len(wins) / n * 100, 1) if n > 0 else 0,
            "expectancy": round(sum(pnls) / n, 2) if n > 0 else 0,
            "days": round(days, 1),
        }

    def _get_trades(self, lookback_days: int) -> List[Dict]:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        cutoff = time.time() - (lookback_days * 86400)
        rows = db.execute(
            "SELECT * FROM trades WHERE entry_time > ? AND exit_time > 0 ORDER BY entry_time ASC",
            (cutoff,)
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]

    def print_gates(self, result: Dict) -> None:
        """Pretty-print promotion gate results."""
        if result.get("status") == "NO_DATA":
            print("\n  ⏳ No trades for promotion gate check.\n")
            return
        
        print(f"\n{'═' * 100}")
        print(f"  PROMOTION GATES — {result['lookback_days']}d | {result['total_trades']} Trades | Phase: {result['current_phase']}")
        print(f"{'═' * 100}")
        
        for gate_name, gate in result["gates"].items():
            phase_from, phase_to = gate_name.split("_TO_")
            eligible = gate["eligible"]
            icon = "🟢" if eligible else "🔴"
            
            print(f"\n  {icon} {phase_from} → {phase_to} ({gate['passed']}/{gate['total']} passed)")
            
            for check_name, check in gate["checks"].items():
                status = "✅" if check["met"] else "❌"
                val = check["value"]
                req = check["required"]
                
                if isinstance(val, bool):
                    val_str = "Yes" if val else "No"
                    req_str = "Yes" if req else "N/A"
                elif isinstance(val, float):
                    val_str = f"{val:.2f}"
                    req_str = f"{req:.2f}"
                else:
                    val_str = str(val)
                    req_str = str(req)
                
                print(f"    {status} {check_name:<25} = {val_str:>10} (need {req_str})")
        
        print(f"\n{'═' * 100}")


# ══════════════════════════════════════════════════════════════════════
# UNIFIED ANALYTICS ENGINE
# ══════════════════════════════════════════════════════════════════════

class AnalyticsEngine:
    """
    Unified analytics engine combining all 5 priorities.
    
    Usage:
        analytics = AnalyticsEngine()
        
        # Priority 1: Signal Journal
        analytics.journal.record_signal(trade_dict)
        entries = analytics.journal.get_journal(limit=100)
        
        # Priority 2: Evidence Dashboard
        evidence = analytics.evidence.get_evidence_metrics(lookback_days=30)
        analytics.evidence.print_evidence(evidence)
        
        # Priority 3: Confidence Calibration
        calibration = analytics.calibration.calibrate(lookback_days=30)
        analytics.calibration.print_calibration(calibration)
        
        # Priority 4: Weakness Detection
        weaknesses = analytics.weakness.scan(lookback_days=30)
        analytics.weakness.print_weaknesses(weaknesses)
        
        # Priority 5: Promotion Gates
        gates = analytics.promotion.check_all_gates(lookback_days=90)
        analytics.promotion.print_gates(gates)
        
        # Full report
        analytics.full_report()
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self.journal = SignalJournal(db_path)
        self.evidence = EvidenceDashboard(db_path)
        self.calibration = ConfidenceCalibration(db_path)
        self.weakness = WeaknessDetector(db_path)
        self.promotion = PromotionGates(db_path)
        logger.info("✅ AnalyticsEngine initialized (5 priorities active)")

    def full_report(self, lookback_days: int = 30) -> Dict:
        """Generate complete analytics report across all 5 priorities."""
        evidence = self.evidence.get_evidence_metrics(lookback_days)
        calibration = self.calibration.calibrate(lookback_days)
        weaknesses = self.weakness.scan(lookback_days)
        gates = self.promotion.check_all_gates(lookback_days)
        
        # Print all
        self.evidence.print_evidence(evidence)
        self.calibration.print_calibration(calibration)
        self.weakness.print_weaknesses(weaknesses)
        self.promotion.print_gates(gates)
        
        # Export to bridge
        self._export_bridge({
            "evidence": evidence,
            "calibration": calibration,
            "weaknesses": weaknesses,
            "gates": gates,
        })
        
        return {
            "evidence": evidence,
            "calibration": calibration,
            "weaknesses": weaknesses,
            "gates": gates,
        }

    def _export_bridge(self, data: Dict) -> None:
        """Export analytics to bridge for dashboard consumption."""
        try:
            bridge_file = BRIDGE_PATH / "analytics_report.json"
            bridge_file.parent.mkdir(parents=True, exist_ok=True)
            with open(bridge_file, "w") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.error("Bridge export failed: {}", e)


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    
    engine = AnalyticsEngine()
    engine.full_report(lookback_days=days)
