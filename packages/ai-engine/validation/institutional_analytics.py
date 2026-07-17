#!/usr/bin/env python3
"""
Institutional Analytics Layer — EMA_V5 Production Intelligence.

CODE FREEZE: NO strategy changes. READ ONLY analytics.

Automatically discovers profitable market conditions from live and
historical trading data. Never modifies production logic.

Modules:
  1. Extended Signal Journal — 30+ fields per signal
  2. Alpha Discovery Engine — Find what works, kill what doesn't
  3. Confidence Calibration — 5-point bucket validation
  4. Symbol Ranking — Elite/Good/Neutral/Weak/Disabled
  5. Portfolio Analytics — Comprehensive metrics
  6. Live Performance Monitor — Paper vs Historical vs Live
  7. Report Generator — Alpha/Daily/Weekly/Monthly/Correlation
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
# PATHS & CONSTANTS
# ══════════════════════════════════════════════════════════════════════

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "validation.db"
BRIDGE_PATH = Path(__file__).resolve().parent.parent / "data" / "bridge"

MIN_SAMPLE_SIZE = 10       # Minimum trades for statistical significance
CONFIDENCE_BUCKET_SIZE = 5 # 5-point buckets: 50-55, 55-60, ..., 90+

# Backtest baseline
BASELINE = {
    "profit_factor": 1.55, "sharpe": 3.69, "sortino": 2.97,
    "calmar": 9.36, "recovery_factor": 7545.50,
    "max_drawdown_pct": 8.52, "win_rate_pct": 48.5,
    "expectancy_per_trade": 35.13, "avg_r_multiple": 1.41,
}


# ══════════════════════════════════════════════════════════════════════
# 1. EXTENDED SIGNAL JOURNAL
# ══════════════════════════════════════════════════════════════════════

class ExtendedJournal:
    """
    Records every signal with 30+ fields for institutional analysis.
    
    Fields recorded:
    Timestamp, Exchange, Symbol, Direction, Timeframe, Entry, Stop, TP,
    ATR, Volume, RVOL, Funding, Open Interest, CVD, Delta,
    Orderbook Imbalance, Liquidity Sweep, FVG, Order Block, Session,
    Market Regime, Trend, Confidence, Score, Risk, Expected RR, Actual RR,
    MFE, MAE, Holding Time, Slippage, Fees, Net Profit,
    HTF Trend, Day of Week, Hour of Day, Reason Entry, Reason Exit
    """

    # All fields for the extended journal
    FIELDS = [
        "timestamp", "exchange", "symbol", "direction", "timeframe",
        "entry_price", "stop_loss", "take_profit", "atr_entry", "atr_exit",
        "volume", "rvol", "funding_rate", "open_interest", "cvd", "delta",
        "orderbook_imbalance", "liquidity_sweep", "fvg", "order_block",
        "session", "market_regime", "trend", "confidence", "score",
        "risk_pct", "expected_rr", "actual_rr",
        "mfe_pct", "mae_pct", "hold_minutes",
        "slippage_bps", "fees", "net_profit",
        "htf_trend", "day_of_week", "hour_of_day",
        "reason_entry", "reason_exit",
    ]

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        """Add any missing columns to the trades table."""
        db = sqlite3.connect(str(self.db_path), timeout=10)
        extra_cols = [
            ("trades", "timeframe", "TEXT DEFAULT '1h'"),
            ("trades", "volume", "REAL DEFAULT 0"),
            ("trades", "rvol", "REAL DEFAULT 0"),
            ("trades", "funding_rate", "REAL DEFAULT 0"),
            ("trades", "open_interest", "REAL DEFAULT 0"),
            ("trades", "cvd", "REAL DEFAULT 0"),
            ("trades", "delta", "REAL DEFAULT 0"),
            ("trades", "orderbook_imbalance", "REAL DEFAULT 0"),
            ("trades", "liquidity_sweep", "INTEGER DEFAULT 0"),
            ("trades", "fvg", "INTEGER DEFAULT 0"),
            ("trades", "order_block", "INTEGER DEFAULT 0"),
            ("trades", "market_regime", "TEXT DEFAULT ''"),
            ("trades", "trend", "TEXT DEFAULT ''"),
            ("trades", "score", "REAL DEFAULT 0"),
            ("trades", "risk_pct", "REAL DEFAULT 0"),
            ("trades", "expected_rr", "REAL DEFAULT 0"),
            ("trades", "actual_rr", "REAL DEFAULT 0"),
            ("trades", "htf_trend", "TEXT DEFAULT ''"),
            ("trades", "day_of_week", "TEXT DEFAULT ''"),
            ("trades", "hour_of_day", "INTEGER DEFAULT 0"),
            ("trades", "reason_entry", "TEXT DEFAULT ''"),
            ("trades", "reason_exit", "TEXT DEFAULT ''"),
            ("trades", "session", "TEXT DEFAULT ''"),
        ]
        for table, col, typedef in extra_cols:
            try:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass
        db.commit()
        db.close()

    def record(self, trade: Dict) -> None:
        """Record a trade with all extended fields."""
        db = sqlite3.connect(str(self.db_path), timeout=10)
        
        # Auto-compute session from entry time
        entry_time = trade.get("entry_time", trade.get("timestamp", time.time()))
        hour = time.gmtime(entry_time).tm_hour
        session = self._classify_session(hour)
        dow = time.strftime("%A", time.localtime(entry_time))
        
        # Build update dict for extended fields
        updates = {
            "timeframe": trade.get("timeframe", "1h"),
            "volume": trade.get("volume", 0),
            "rvol": trade.get("rvol", 0),
            "funding_rate": trade.get("funding_rate", 0),
            "open_interest": trade.get("open_interest", 0),
            "cvd": trade.get("cvd", 0),
            "delta": trade.get("delta", 0),
            "orderbook_imbalance": trade.get("orderbook_imbalance", 0),
            "liquidity_sweep": 1 if trade.get("liquidity_sweep") else 0,
            "fvg": 1 if trade.get("fvg") else 0,
            "order_block": 1 if trade.get("order_block") else 0,
            "market_regime": trade.get("market_regime", trade.get("regime", "")),
            "trend": trade.get("trend", trade.get("htf_trend", "")),
            "score": trade.get("score", 0),
            "risk_pct": trade.get("risk_pct", 0),
            "expected_rr": trade.get("expected_rr", trade.get("risk_reward", 0)),
            "actual_rr": trade.get("actual_rr", trade.get("r_multiple", 0)),
            "htf_trend": trade.get("htf_trend", ""),
            "day_of_week": dow,
            "hour_of_day": hour,
            "session": session,
            "reason_entry": trade.get("reason_entry", ""),
            "reason_exit": trade.get("reason_exit", trade.get("exit_reason", "")),
        }
        
        # Update the most recent trade for this symbol
        sym = trade.get("symbol", "")
        if sym:
            set_clauses = ", ".join(f"{k}=?" for k in updates.keys())
            values = list(updates.values())
            db.execute(
                f"UPDATE trades SET {set_clauses} WHERE id=(SELECT MAX(id) FROM trades WHERE symbol=?)",
                values + [sym]
            )
        db.commit()
        db.close()

    def _classify_session(self, hour_utc: int) -> str:
        if 0 <= hour_utc < 8: return "asia"
        elif 8 <= hour_utc < 14: return "london"
        elif 14 <= hour_utc < 21: return "new_york"
        else: return "overlap"

    def get_all(self, lookback_days: int = 30) -> List[Dict]:
        """Get all trades with extended fields."""
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        cutoff = time.time() - (lookback_days * 86400)
        rows = db.execute(
            "SELECT * FROM trades WHERE entry_time > ? AND exit_time > 0 ORDER BY entry_time ASC",
            (cutoff,)
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════
# 2. ALPHA DISCOVERY ENGINE
# ══════════════════════════════════════════════════════════════════════

class AlphaDiscovery:
    """
    Automatically identifies which conditions consistently produce profits.
    
    Scans all possible condition combinations and ranks them by:
    - Profit Factor
    - Expectancy
    - Sharpe
    - R-Multiple
    - Trade Count (sample size)
    
    Marks underperforming conditions as WEAK/BAD/AVOID.
    """

    # Conditions to analyze
    CONDITION_DIMENSIONS = [
        "session",           # asia, london, new_york, overlap
        "day_of_week",       # Monday-Sunday
        "hour_of_day",       # 0-23 UTC
        "market_regime",     # BUY_MODE, SELL_MODE
        "trend",             # bullish, bearish, neutral
        "direction",         # LONG, SHORT
        "symbol",            # BTCUSDT, ETHUSDT, etc.
        "htf_trend",         # higher timeframe trend
        "liquidity_sweep",   # 0 or 1
        "order_block",       # 0 or 1
        "fvg",               # 0 or 1
    ]

    # Numeric conditions to bin
    NUMERIC_BINS = {
        "atr_entry": [(0, 0.5, "low"), (0.5, 2.0, "medium"), (2.0, 5.0, "high"), (5.0, 100, "extreme")],
        "rvol": [(0, 0.5, "low"), (0.5, 1.5, "normal"), (1.5, 3.0, "high"), (3.0, 100, "extreme")],
        "funding_rate": [(-100, -0.01, "negative"), (-0.01, 0.01, "neutral"), (0.01, 100, "positive")],
        "confidence": [(50, 60, "50-60"), (60, 70, "60-70"), (70, 80, "70-80"), (80, 90, "80-90"), (90, 100, "90+")],
        "score": [(0, 0.3, "low"), (0.3, 0.6, "medium"), (0.6, 1.0, "high")],
    }

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    def discover(self, lookback_days: int = 30) -> Dict:
        """Run full alpha discovery across all conditions."""
        journal = ExtendedJournal(self.db_path)
        trades = journal.get_all(lookback_days)
        
        if not trades:
            return {"status": "NO_DATA", "conditions": [], "weaknesses": []}
        
        all_conditions = []
        
        # ── Scan categorical dimensions ──
        for dim in self.CONDITION_DIMENSIONS:
            groups = self._group_by(trades, dim)
            for value, group_trades in groups.items():
                if len(group_trades) >= MIN_SAMPLE_SIZE:
                    stats = self._compute_stats(group_trades)
                    stats["dimension"] = dim
                    stats["value"] = str(value)
                    stats["condition"] = f"{dim}={value}"
                    all_conditions.append(stats)
        
        # ── Scan numeric bins ──
        for field, bins in self.NUMERIC_BINS.items():
            for lo, hi, label in bins:
                bin_trades = [t for t in trades if lo <= (t.get(field, 0) or 0) < hi]
                if len(bin_trades) >= MIN_SAMPLE_SIZE:
                    stats = self._compute_stats(bin_trades)
                    stats["dimension"] = field
                    stats["value"] = label
                    stats["condition"] = f"{field}={label}"
                    all_conditions.append(stats)
        
        # ── Scan 2-way combinations for high-value pairs ──
        key_dims = ["symbol", "session", "market_regime", "direction"]
        for i, dim1 in enumerate(key_dims):
            for dim2 in key_dims[i+1:]:
                groups = self._group_by_pair(trades, dim1, dim2)
                for (v1, v2), group_trades in groups.items():
                    if len(group_trades) >= MIN_SAMPLE_SIZE:
                        stats = self._compute_stats(group_trades)
                        stats["dimension"] = f"{dim1}×{dim2}"
                        stats["value"] = f"{v1}×{v2}"
                        stats["condition"] = f"{dim1}={v1} & {dim2}={v2}"
                        all_conditions.append(stats)
        
        # Sort by expectancy (best first)
        all_conditions.sort(key=lambda x: x.get("expectancy", 0), reverse=True)
        
        # Classify conditions
        best_conditions = []
        weak_conditions = []
        bad_conditions = []
        avoid_conditions = []
        
        for cond in all_conditions:
            pf = cond.get("profit_factor", 0)
            exp = cond.get("expectancy", 0)
            sharpe = cond.get("sharpe", 0)
            avg_r = cond.get("avg_r", 0)
            
            if pf >= 1.5 and exp > 0 and sharpe > 0.5:
                cond["rating"] = "BEST"
                best_conditions.append(cond)
            elif pf >= 1.2 and exp > 0:
                cond["rating"] = "GOOD"
            elif pf >= 1.0 and exp >= 0:
                cond["rating"] = "NEUTRAL"
            elif pf >= 0.8:
                cond["rating"] = "WEAK"
                weak_conditions.append(cond)
            elif pf >= 0.5:
                cond["rating"] = "BAD"
                bad_conditions.append(cond)
            else:
                cond["rating"] = "AVOID"
                avoid_conditions.append(cond)
        
        return {
            "status": "OK",
            "lookback_days": lookback_days,
            "total_trades": len(trades),
            "total_conditions_analyzed": len(all_conditions),
            "best_conditions": best_conditions[:20],
            "weak_conditions": weak_conditions[:10],
            "bad_conditions": bad_conditions[:10],
            "avoid_conditions": avoid_conditions[:10],
            "all_conditions": all_conditions,
            "summary": {
                "best_count": len(best_conditions),
                "weak_count": len(weak_conditions),
                "bad_count": len(bad_conditions),
                "avoid_count": len(avoid_conditions),
            },
        }

    def _group_by(self, trades: List[Dict], field: str) -> Dict:
        groups = defaultdict(list)
        for t in trades:
            key = t.get(field, "unknown")
            if key is None: key = "unknown"
            groups[key].append(t)
        return dict(groups)

    def _group_by_pair(self, trades: List[Dict], f1: str, f2: str) -> Dict:
        groups = defaultdict(list)
        for t in trades:
            v1 = t.get(f1, "unknown") or "unknown"
            v2 = t.get(f2, "unknown") or "unknown"
            groups[(v1, v2)].append(t)
        return dict(groups)

    def _compute_stats(self, trades: List[Dict]) -> Dict:
        if not trades:
            return {"trades": 0}
        
        pnls = [t.get("net_profit", 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gp = sum(wins) if wins else 0
        gl = abs(sum(losses)) if losses else 0.001
        
        r_vals = [t.get("actual_rr", t.get("r_multiple", 0)) for t in trades]
        mae_vals = [abs(t.get("mae_pct", 0)) for t in trades]
        mfe_vals = [abs(t.get("mfe_pct", 0)) for t in trades]
        hold_vals = [t.get("hold_minutes", 0) for t in trades]
        
        # Sharpe from trade returns
        if len(pnls) > 1:
            avg_ret = statistics.mean(pnls)
            std_ret = statistics.stdev(pnls) if statistics.stdev(pnls) > 0 else 1
            sharpe = avg_ret / std_ret * math.sqrt(252) if std_ret > 0 else 0
        else:
            sharpe = 0
        
        # Expectancy
        wr = len(wins) / len(trades) * 100
        exp = sum(pnls) / len(trades)
        
        # Drawdown
        ec = [10000]
        for p in pnls:
            ec.append(ec[-1] + p)
        peak = ec[0]
        max_dd = 0
        for eq in ec:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        return {
            "trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(wr, 1),
            "total_pnl": round(sum(pnls), 2),
            "avg_pnl": round(statistics.mean(pnls), 2),
            "profit_factor": round(gp / gl, 2),
            "expectancy": round(exp, 2),
            "avg_r": round(statistics.mean(r_vals), 2),
            "avg_mae": round(statistics.mean(mae_vals), 2),
            "avg_mfe": round(statistics.mean(mfe_vals), 2),
            "avg_hold": round(statistics.mean(hold_vals), 1) if hold_vals else 0,
            "sharpe": round(sharpe, 2),
            "max_dd": round(max_dd, 2),
        }


# ══════════════════════════════════════════════════════════════════════
# 3. CONFIDENCE CALIBRATION (5-point buckets)
# ══════════════════════════════════════════════════════════════════════

class ConfidenceCalibrator:
    """
    Fine-grained confidence calibration with 5-point buckets.
    
    Buckets: 50-55, 55-60, 60-65, 65-70, 70-75, 75-80, 80-85, 85-90, 90+
    
    For each bucket computes:
    PF, Expectancy, Sharpe, Avg R, Avg MAE, Avg MFE, Avg DD, Avg Hold, Count
    """

    BUCKET_SIZE = 5
    MIN_BUCKET = 50
    MAX_BUCKET = 100

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    def calibrate(self, lookback_days: int = 30) -> Dict:
        """Run fine-grained confidence calibration."""
        journal = ExtendedJournal(self.db_path)
        trades = journal.get_all(lookback_days)
        
        if not trades:
            return {"status": "NO_DATA", "buckets": [], "optimal_threshold": 90}
        
        # Build buckets
        buckets = {}
        for lo in range(self.MIN_BUCKET, self.MAX_BUCKET, self.BUCKET_SIZE):
            hi = lo + self.BUCKET_SIZE
            label = f"{lo}-{hi}"
            bucket_trades = [t for t in trades if lo <= (t.get("confidence", 0) or 0) < hi]
            buckets[label] = self._compute_bucket_stats(bucket_trades, label)
        
        # 90+ bucket (includes all above 90)
        bucket_90 = [t for t in trades if (t.get("confidence", 0) or 0) >= 90]
        buckets["90+"] = self._compute_bucket_stats(bucket_90, "90+")
        
        # Find optimal threshold (highest R with sufficient sample)
        best_bucket = None
        best_r = -999
        for label, stats in buckets.items():
            if stats["trades"] >= MIN_SAMPLE_SIZE and stats["avg_r"] > best_r:
                best_r = stats["avg_r"]
                best_bucket = label
        
        # Check monotonicity
        bucket_list = [(label, stats["avg_r"]) for label, stats in buckets.items() 
                       if stats["trades"] >= MIN_SAMPLE_SIZE]
        is_monotonic = all(bucket_list[i][1] <= bucket_list[i+1][1] 
                          for i in range(len(bucket_list)-1)) if len(bucket_list) > 1 else True
        
        # Compute correlation
        confs = [t.get("confidence", 0) or 0 for t in trades if (t.get("confidence", 0) or 0) > 0]
        rs = [t.get("actual_rr", t.get("r_multiple", 0)) or 0 for t in trades if (t.get("confidence", 0) or 0) > 0]
        correlation = 0
        if len(confs) > 5 and statistics.stdev(confs) > 0 and statistics.stdev(rs) > 0:
            correlation = statistics.correlation(confs, rs) if hasattr(statistics, 'correlation') else 0
        
        # Recommend optimal threshold
        recommended = self._recommend_threshold(buckets)
        
        return {
            "status": "OK",
            "lookback_days": lookback_days,
            "total_trades": len(trades),
            "buckets": buckets,
            "best_bucket": best_bucket,
            "best_r": best_r,
            "is_monotonic": is_monotonic,
            "correlation": round(correlation, 3),
            "optimal_threshold": recommended["threshold"],
            "recommendation": recommended,
        }

    def _compute_bucket_stats(self, trades: List[Dict], label: str) -> Dict:
        if not trades:
            return {"trades": 0, "label": label}
        
        pnls = [t.get("net_profit", 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gp = sum(wins) if wins else 0
        gl = abs(sum(losses)) if losses else 0.001
        
        r_vals = [t.get("actual_rr", t.get("r_multiple", 0)) or 0 for t in trades]
        mae_vals = [abs(t.get("mae_pct", 0)) for t in trades]
        mfe_vals = [abs(t.get("mfe_pct", 0)) for t in trades]
        hold_vals = [t.get("hold_minutes", 0) for t in trades]
        
        # Drawdown per bucket
        ec = [10000]
        for p in pnls:
            ec.append(ec[-1] + p)
        peak = ec[0]; max_dd = 0
        for eq in ec:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        # Sharpe
        if len(pnls) > 1:
            avg_ret = statistics.mean(pnls)
            std_ret = statistics.stdev(pnls) if statistics.stdev(pnls) > 0 else 1
            sharpe = avg_ret / std_ret * math.sqrt(252) if std_ret > 0 else 0
        else:
            sharpe = 0
        
        return {
            "label": label,
            "trades": len(trades),
            "wins": len(wins),
            "win_rate": round(len(wins) / len(trades) * 100, 1),
            "profit_factor": round(gp / gl, 2),
            "expectancy": round(sum(pnls) / len(trades), 2),
            "avg_r": round(statistics.mean(r_vals), 2),
            "avg_mae": round(statistics.mean(mae_vals), 2),
            "avg_mfe": round(statistics.mean(mfe_vals), 2),
            "avg_hold": round(statistics.mean(hold_vals), 1) if hold_vals else 0,
            "sharpe": round(sharpe, 2),
            "max_dd": round(max_dd, 2),
            "total_pnl": round(sum(pnls), 2),
        }

    def _recommend_threshold(self, buckets: Dict) -> Dict:
        """Recommend optimal confidence threshold based on data."""
        # Find highest bucket where PF > 1.0 and sufficient trades
        viable = [(label, stats) for label, stats in buckets.items()
                  if stats["trades"] >= MIN_SAMPLE_SIZE and stats.get("profit_factor", 0) > 1.0]
        
        if not viable:
            return {"threshold": 90, "reason": "No viable bucket found, keeping default 90"}
        
        # Find the lowest bucket with positive expectancy (most inclusive profitable threshold)
        best = min(viable, key=lambda x: int(x[0].split("-")[0]) if "-" in x[0] else 90)
        threshold = int(best[0].split("-")[0]) if "-" in best[0] else 90
        
        return {
            "threshold": threshold,
            "reason": f"Bucket {best[0]} has PF={best[1].get('profit_factor', 0):.2f} with {best[1]['trades']} trades",
            "bucket_stats": best[1],
        }


# ══════════════════════════════════════════════════════════════════════
# 4. SYMBOL RANKING
# ══════════════════════════════════════════════════════════════════════

class SymbolRanker:
    """
    Rank symbols continuously based on profitability statistics.
    
    Elite:    PF >= 1.5, Expectancy > 0, Sharpe > 1.0, DD < 15%
    Good:     PF >= 1.2, Expectancy > 0, Sharpe > 0.5
    Neutral:  PF >= 1.0, Expectancy >= 0
    Weak:     PF >= 0.8, Expectancy < 0
    Disabled: PF < 0.8 or trades < MIN_SAMPLE_SIZE
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    def rank(self, lookback_days: int = 30) -> Dict:
        """Rank all symbols by profitability."""
        journal = ExtendedJournal(self.db_path)
        trades = journal.get_all(lookback_days)
        
        if not trades:
            return {"status": "NO_DATA", "rankings": {}}
        
        # Group by symbol
        sym_groups = defaultdict(list)
        for t in trades:
            sym_groups[t.get("symbol", "unknown")].append(t)
        
        rankings = {}
        for sym, sym_trades in sym_groups.items():
            stats = self._compute_symbol_stats(sym_trades)
            stats["symbol"] = sym
            stats["tier"] = self._classify_tier(stats)
            rankings[sym] = stats
        
        # Sort by expectancy (best first)
        sorted_syms = sorted(rankings.items(), key=lambda x: x[1].get("expectancy", 0), reverse=True)
        
        # Build tier lists
        tiers = {"ELITE": [], "GOOD": [], "NEUTRAL": [], "WEAK": [], "DISABLED": []}
        for sym, stats in sorted_syms:
            tier = stats["tier"]
            tiers[tier].append({"symbol": sym, **stats})
        
        return {
            "status": "OK",
            "lookback_days": lookback_days,
            "total_trades": len(trades),
            "total_symbols": len(rankings),
            "rankings": dict(sorted_syms),
            "tiers": tiers,
            "tier_counts": {k: len(v) for k, v in tiers.items()},
        }

    def _compute_symbol_stats(self, trades: List[Dict]) -> Dict:
        if not trades:
            return {"trades": 0, "tier": "DISABLED"}
        
        pnls = [t.get("net_profit", 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gp = sum(wins) if wins else 0
        gl = abs(sum(losses)) if losses else 0.001
        
        r_vals = [t.get("actual_rr", t.get("r_multiple", 0)) or 0 for t in trades]
        
        # Sharpe
        if len(pnls) > 1:
            std = statistics.stdev(pnls)
            sharpe = statistics.mean(pnls) / std * math.sqrt(252) if std > 0 else 0
        else:
            sharpe = 0
        
        # Drawdown
        ec = [10000]
        for p in pnls: ec.append(ec[-1] + p)
        peak = ec[0]; max_dd = 0
        for eq in ec:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        return {
            "trades": len(trades),
            "wins": len(wins),
            "win_rate": round(len(wins) / len(trades) * 100, 1),
            "total_pnl": round(sum(pnls), 2),
            "expectancy": round(statistics.mean(pnls), 2),
            "profit_factor": round(gp / gl, 2),
            "avg_r": round(statistics.mean(r_vals), 2),
            "sharpe": round(sharpe, 2),
            "max_dd": round(max_dd, 2),
        }

    def _classify_tier(self, stats: Dict) -> str:
        pf = stats.get("profit_factor", 0)
        exp = stats.get("expectancy", 0)
        sharpe = stats.get("sharpe", 0)
        dd = stats.get("max_dd", 100)
        n = stats.get("trades", 0)
        
        if n < MIN_SAMPLE_SIZE:
            return "DISABLED"
        if pf >= 1.5 and exp > 0 and sharpe > 1.0 and dd < 15:
            return "ELITE"
        if pf >= 1.2 and exp > 0 and sharpe > 0.5:
            return "GOOD"
        if pf >= 1.0 and exp >= 0:
            return "NEUTRAL"
        if pf >= 0.8:
            return "WEAK"
        return "DISABLED"


# ══════════════════════════════════════════════════════════════════════
# 5. PORTFOLIO ANALYTICS
# ══════════════════════════════════════════════════════════════════════

class PortfolioAnalytics:
    """
    Comprehensive portfolio-level metrics.
    
    Net Profit, PF, Expectancy, Recovery Factor, Sharpe, Sortino,
    Calmar, Max DD, Exposure, Capital Usage, Avg Hold, Trade Frequency,
    Risk per Trade, Return per Risk.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    def compute(self, lookback_days: int = 30) -> Dict:
        """Compute all portfolio analytics."""
        journal = ExtendedJournal(self.db_path)
        trades = journal.get_all(lookback_days)
        
        if not trades:
            return {"status": "NO_DATA"}
        
        n = len(trades)
        pnls = [t.get("net_profit", 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gp = sum(wins) if wins else 0
        gl = abs(sum(losses)) if losses else 0.001
        
        # Equity curve
        ec = [10000]
        for p in pnls: ec.append(ec[-1] + p)
        peak = ec[0]; max_dd = 0
        for eq in ec:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        # Returns for Sharpe/Sortino
        rets = [(ec[i] - ec[i-1]) / ec[i-1] for i in range(1, len(ec)) if ec[i-1] > 0]
        sharpe = 0; sortino = 0
        if len(rets) > 2:
            std = statistics.stdev(rets)
            if std > 0: sharpe = statistics.mean(rets) / std * math.sqrt(365.25 * 24)
            neg = [r for r in rets if r < 0]
            if neg:
                neg_std = statistics.stdev(neg)
                if neg_std > 0: sortino = statistics.mean(rets) / neg_std * math.sqrt(365.25 * 24)
        
        # CAGR & Calmar
        if trades:
            first_time = min(t.get("entry_time", time.time()) for t in trades)
            last_time = max(t.get("entry_time", time.time()) for t in trades)
            days = max((last_time - first_time) / 86400, 1)
            years = days / 365.25
            final_eq = ec[-1]
            cagr = (final_eq / 10000) ** (1/years) - 1 if final_eq > 0 and years > 0 else 0
            calmar = cagr / (max_dd / 100) if max_dd > 0 else 0
        else:
            cagr = 0; calmar = 0; days = 0
        
        # Recovery Factor
        rf = sum(pnls) / (max_dd / 100 * 10000) if max_dd > 0 else 0
        
        # Risk per trade
        risk_vals = [abs(t.get("stop_loss", 0) - t.get("entry_price", 0)) / max(t.get("entry_price", 1), 1) * 100 
                     for t in trades if t.get("stop_loss", 0) > 0]
        avg_risk = statistics.mean(risk_vals) if risk_vals else 0
        
        # Return per risk
        avg_r = statistics.mean([t.get("actual_rr", t.get("r_multiple", 0)) or 0 for t in trades])
        
        # Trade frequency
        trades_per_day = n / max(days, 1) if days > 0 else 0
        
        # Holding time
        holds = [t.get("hold_minutes", 0) for t in trades if t.get("hold_minutes", 0) > 0]
        avg_hold = statistics.mean(holds) if holds else 0
        
        return {
            "status": "OK",
            "lookback_days": lookback_days,
            "total_trades": n,
            "net_profit": round(sum(pnls), 2),
            "profit_factor": round(gp / gl, 2),
            "expectancy": round(sum(pnls) / n, 2),
            "recovery_factor": round(rf, 2),
            "sharpe": round(sharpe, 2),
            "sortino": round(sortino, 2),
            "calmar": round(calmar, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "cagr_pct": round(cagr * 100, 2),
            "avg_hold_minutes": round(avg_hold, 1),
            "trades_per_day": round(trades_per_day, 2),
            "avg_risk_pct": round(avg_risk, 2),
            "return_per_risk": round(avg_r, 2),
            "win_rate": round(len(wins) / n * 100, 1),
            "avg_win": round(statistics.mean(wins), 2) if wins else 0,
            "avg_loss": round(statistics.mean(losses), 2) if losses else 0,
            "final_equity": round(ec[-1], 2),
        }


# ══════════════════════════════════════════════════════════════════════
# 6. LIVE PERFORMANCE MONITOR
# ══════════════════════════════════════════════════════════════════════

class LivePerformanceMonitor:
    """
    Continuously compare Paper vs Historical vs Walk-Forward vs OOS vs Live.
    Highlight deviations.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    def compare(self, lookback_days: int = 30) -> Dict:
        """Compare live performance against all baselines."""
        portfolio = PortfolioAnalytics(self.db_path)
        live = portfolio.compute(lookback_days)
        
        if live.get("status") == "NO_DATA":
            return {"status": "NO_DATA"}
        
        # Compare against baseline
        comparisons = {}
        for metric, bt_val in BASELINE.items():
            live_val = live.get(metric, live.get(metric.replace("_pct", ""), 0))
            if isinstance(live_val, (int, float)) and bt_val != 0:
                dev = (live_val - bt_val) / abs(bt_val) * 100
                comparisons[metric] = {
                    "live": round(live_val, 2),
                    "backtest": round(bt_val, 2),
                    "deviation_pct": round(dev, 1),
                    "status": "✅" if abs(dev) < 20 else "⚠️" if abs(dev) < 40 else "❌",
                }
        
        # Overall health
        deviations = [abs(c["deviation_pct"]) for c in comparisons.values()]
        avg_dev = statistics.mean(deviations) if deviations else 0
        max_dev = max(deviations) if deviations else 0
        
        health = "HEALTHY" if avg_dev < 15 else "WARNING" if avg_dev < 30 else "CRITICAL"
        
        return {
            "status": "OK",
            "lookback_days": lookback_days,
            "live_metrics": live,
            "comparisons": comparisons,
            "avg_deviation": round(avg_dev, 1),
            "max_deviation": round(max_dev, 1),
            "health": health,
        }


# ══════════════════════════════════════════════════════════════════════
# 7. REPORT GENERATOR
# ══════════════════════════════════════════════════════════════════════

class InstitutionalReportGenerator:
    """
    Generate comprehensive alpha reports.
    
    Daily Alpha Report, Weekly Alpha Report, Monthly Alpha Report,
    Best/Worst Symbols, Sessions, Confidence Levels, Regimes,
    Profit Heatmaps, Correlation Reports, Alpha Ranking Table.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.alpha = AlphaDiscovery(db_path)
        self.calibrator = ConfidenceCalibrator(db_path)
        self.ranker = SymbolRanker(db_path)
        self.portfolio = PortfolioAnalytics(db_path)
        self.monitor = LivePerformanceMonitor(db_path)

    def generate_full_report(self, lookback_days: int = 30) -> Dict:
        """Generate complete institutional analytics report."""
        alpha = self.alpha.discover(lookback_days)
        calibration = self.calibrator.calibrate(lookback_days)
        symbol_rankings = self.ranker.rank(lookback_days)
        portfolio = self.portfolio.compute(lookback_days)
        live_monitor = self.monitor.compare(lookback_days)
        
        report = {
            "status": "OK",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "lookback_days": lookback_days,
            "portfolio": portfolio,
            "alpha_discovery": {
                "total_conditions": alpha.get("total_conditions_analyzed", 0),
                "best_conditions": alpha.get("best_conditions", [])[:10],
                "weak_conditions": alpha.get("weak_conditions", [])[:5],
                "bad_conditions": alpha.get("bad_conditions", [])[:5],
                "avoid_conditions": alpha.get("avoid_conditions", [])[:5],
            },
            "confidence_calibration": {
                "buckets": calibration.get("buckets", {}),
                "optimal_threshold": calibration.get("optimal_threshold", 90),
                "is_monotonic": calibration.get("is_monotonic", False),
                "correlation": calibration.get("correlation", 0),
                "recommendation": calibration.get("recommendation", {}),
            },
            "symbol_rankings": {
                "tiers": {k: [{"symbol": s["symbol"], "pf": s.get("profit_factor", 0), 
                              "expectancy": s.get("expectancy", 0), "trades": s.get("trades", 0)}
                             for s in v[:5]] for k, v in symbol_rankings.get("tiers", {}).items()},
                "tier_counts": symbol_rankings.get("tier_counts", {}),
            },
            "live_monitor": {
                "health": live_monitor.get("health", "UNKNOWN"),
                "avg_deviation": live_monitor.get("avg_deviation", 0),
                "comparisons": live_monitor.get("comparisons", {}),
            },
        }
        
        # Export to bridge
        self._export_bridge(report)
        
        return report

    def _export_bridge(self, report: Dict) -> None:
        try:
            bridge_file = BRIDGE_PATH / "institutional_analytics.json"
            bridge_file.parent.mkdir(parents=True, exist_ok=True)
            with open(bridge_file, "w") as f:
                json.dump(report, f, indent=2, default=str)
        except Exception as e:
            logger.error("Bridge export failed: {}", e)

    def print_report(self, report: Dict) -> None:
        """Pretty-print the institutional report."""
        p = report.get("portfolio", {})
        a = report.get("alpha_discovery", {})
        c = report.get("confidence_calibration", {})
        s = report.get("symbol_rankings", {})
        m = report.get("live_monitor", {})
        
        print(f"\n{'═' * 110}")
        print(f"  INSTITUTIONAL ANALYTICS REPORT — {report.get('timestamp', '')} | {report.get('lookback_days', 30)}d")
        print(f"{'═' * 110}")
        
        # Portfolio
        if p.get("status") != "NO_DATA":
            print(f"\n  {'PORTFOLIO ANALYTICS':<50}")
            print(f"  {'─' * 60}")
            print(f"  {'Net Profit':<30} ${p.get('net_profit', 0):>12,.2f}")
            print(f"  {'Profit Factor':<30} {p.get('profit_factor', 0):>12.2f}")
            print(f"  {'Expectancy':<30} ${p.get('expectancy', 0):>11,.2f}")
            print(f"  {'Recovery Factor':<30} {p.get('recovery_factor', 0):>12.2f}")
            print(f"  {'Sharpe':<30} {p.get('sharpe', 0):>12.2f}")
            print(f"  {'Sortino':<30} {p.get('sortino', 0):>12.2f}")
            print(f"  {'Calmar':<30} {p.get('calmar', 0):>12.2f}")
            print(f"  {'Max Drawdown':<30} {p.get('max_drawdown_pct', 0):>11.1f}%")
            print(f"  {'Win Rate':<30} {p.get('win_rate', 0):>11.1f}%")
            print(f"  {'Trades/Day':<30} {p.get('trades_per_day', 0):>12.1f}")
            print(f"  {'Avg Hold':<30} {p.get('avg_hold_minutes', 0):>11.0f} min")
            print(f"  {'Return/Risk':<30} {p.get('return_per_risk', 0):>12.2f}R")
        
        # Alpha Discovery
        if a.get("best_conditions"):
            print(f"\n  {'TOP 10 ALPHA CONDITIONS':<50}")
            print(f"  {'─' * 100}")
            print(f"  {'Condition':<45} {'Trades':>7} {'PF':>7} {'Exp':>9} {'Avg R':>7} {'Sharpe':>7}")
            for cond in a["best_conditions"][:10]:
                print(f"  {cond.get('condition', ''):<45} {cond.get('trades', 0):>7} {cond.get('profit_factor', 0):>7.2f} ${cond.get('expectancy', 0):>7,.2f} {cond.get('avg_r', 0):>6.2f}R {cond.get('sharpe', 0):>7.2f}")
        
        if a.get("avoid_conditions"):
            print(f"\n  {'AVOID THESE CONDITIONS':<50}")
            print(f"  {'─' * 100}")
            for cond in a["avoid_conditions"][:5]:
                print(f"  ❌ {cond.get('condition', ''):<43} PF={cond.get('profit_factor', 0):.2f} Exp=${cond.get('expectancy', 0):,.2f}")
        
        # Confidence Calibration
        if c.get("buckets"):
            print(f"\n  {'CONFIDENCE CALIBRATION':<50}")
            print(f"  {'─' * 90}")
            print(f"  {'Bucket':<12} {'Trades':>7} {'WR':>7} {'PF':>7} {'Exp':>9} {'Avg R':>7} {'MAE':>7} {'MFE':>7}")
            for label in ["50-55", "55-60", "60-65", "65-70", "70-75", "75-80", "80-85", "85-90", "90+"]:
                st = c["buckets"].get(label, {})
                if st.get("trades", 0) > 0:
                    print(f"  {label:<12} {st['trades']:>7} {st.get('win_rate', 0):>6.1f}% {st.get('profit_factor', 0):>7.2f} ${st.get('expectancy', 0):>7,.2f} {st.get('avg_r', 0):>6.2f}R {st.get('avg_mae', 0):>6.1f}% {st.get('avg_mfe', 0):>6.1f}%")
            print(f"\n  Optimal Threshold: {c.get('optimal_threshold', 90)} | Monotonic: {'✅' if c.get('is_monotonic') else '❌'} | Correlation: {c.get('correlation', 0):.3f}")
            rec = c.get("recommendation", {})
            if rec.get("reason"):
                print(f"  Recommendation: {rec['reason']}")
        
        # Symbol Rankings
        if s.get("tier_counts"):
            print(f"\n  {'SYMBOL RANKINGS':<50}")
            print(f"  {'─' * 60}")
            for tier in ["ELITE", "GOOD", "NEUTRAL", "WEAK", "DISABLED"]:
                count = s["tier_counts"].get(tier, 0)
                syms = s["tiers"].get(tier, [])
                icon = {"ELITE": "👑", "GOOD": "✅", "NEUTRAL": "⚪", "WEAK": "⚠️", "DISABLED": "❌"}.get(tier, "")
                if syms:
                    sym_str = ", ".join([f"{x['symbol']}({x.get('pf', 0):.1f})" for x in syms[:5]])
                    if count > 5: sym_str += f" ... (+{count-5})"
                else:
                    sym_str = "—"
                print(f"  {icon} {tier:<12} ({count:>3}): {sym_str}")
        
        # Live Monitor
        if m.get("health"):
            print(f"\n  {'LIVE PERFORMANCE MONITOR':<50}")
            print(f"  {'─' * 60}")
            health_icon = {"HEALTHY": "✅", "WARNING": "⚠️", "CRITICAL": "❌"}.get(m["health"], "?")
            print(f"  Health: {health_icon} {m['health']} | Avg Deviation: {m.get('avg_deviation', 0):.1f}%")
            for metric, comp in m.get("comparisons", {}).items():
                name = metric.replace("_", " ").title()
                print(f"    {comp.get('status', '?')} {name:<25} Live={comp.get('live', 0):>10.2f} BT={comp.get('backtest', 0):>10.2f} ({comp.get('deviation_pct', 0):+.1f}%)")
        
        print(f"\n{'═' * 110}")


# ══════════════════════════════════════════════════════════════════════
# UNIFIED INSTITUTIONAL ANALYTICS ENGINE
# ══════════════════════════════════════════════════════════════════════

class InstitutionalAnalytics:
    """
    Unified institutional analytics engine.
    
    Usage:
        ia = InstitutionalAnalytics()
        
        # Record signal with extended fields
        ia.journal.record(trade_dict)
        
        # Run full analysis
        report = ia.full_analysis(lookback_days=30)
        ia.reporter.print_report(report)
        
        # Individual modules
        alpha = ia.alpha.discover(lookback_days=30)
        calibration = ia.calibrator.calibrate(lookback_days=30)
        rankings = ia.ranker.rank(lookback_days=30)
        portfolio = ia.portfolio.compute(lookback_days=30)
        live = ia.monitor.compare(lookback_days=30)
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self.journal = ExtendedJournal(db_path)
        self.alpha = AlphaDiscovery(db_path)
        self.calibrator = ConfidenceCalibrator(db_path)
        self.ranker = SymbolRanker(db_path)
        self.portfolio = PortfolioAnalytics(db_path)
        self.monitor = LivePerformanceMonitor(db_path)
        self.reporter = InstitutionalReportGenerator(db_path)
        logger.info("✅ InstitutionalAnalytics initialized")

    def full_analysis(self, lookback_days: int = 30) -> Dict:
        """Run complete institutional analysis."""
        return self.reporter.generate_full_report(lookback_days)


# ══════════════════════════════════════════════════════════════════════
# PRODUCTION MONITOR
# ══════════════════════════════════════════════════════════════════════

class ProductionMonitor:
    """
    Continuously track live performance and detect drift.
    
    Monitors:
    - Live vs Backtest Drift
    - Signal Acceptance Rate
    - Signal Rejection Reasons
    - Alpha Decay
    - Portfolio Performance
    - Regime Changes
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH

    def get_live_metrics(self, lookback_days: int = 7) -> Dict:
        """Get live performance metrics for the most recent period."""
        journal = ExtendedJournal(self.db_path)
        trades = journal.get_all(lookback_days)
        
        if not trades:
            return {"status": "NO_DATA", "message": f"No trades in last {lookback_days} days"}
        
        n = len(trades)
        pnls = [t.get("net_profit", 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gp = sum(wins) if wins else 0
        gl = abs(sum(losses)) if losses else 0.001
        
        # Session breakdown
        session_stats = defaultdict(lambda: {"trades": 0, "pnl": 0, "wins": 0})
        for t in trades:
            s = t.get("session", "unknown")
            session_stats[s]["trades"] += 1
            session_stats[s]["pnl"] += t.get("net_profit", 0)
            if t.get("net_profit", 0) > 0:
                session_stats[s]["wins"] += 1
        
        # Regime breakdown
        regime_stats = defaultdict(lambda: {"trades": 0, "pnl": 0, "wins": 0})
        for t in trades:
            r = t.get("market_regime", t.get("regime", "unknown"))
            regime_stats[r]["trades"] += 1
            regime_stats[r]["pnl"] += t.get("net_profit", 0)
            if t.get("net_profit", 0) > 0:
                regime_stats[r]["wins"] += 1
        
        # Symbol breakdown
        sym_stats = defaultdict(lambda: {"trades": 0, "pnl": 0, "wins": 0})
        for t in trades:
            s = t.get("symbol", "unknown")
            sym_stats[s]["trades"] += 1
            sym_stats[s]["pnl"] += t.get("net_profit", 0)
            if t.get("net_profit", 0) > 0:
                sym_stats[s]["wins"] += 1
        
        # Sharpe
        if len(pnls) > 1:
            std = statistics.stdev(pnls)
            sharpe = statistics.mean(pnls) / std * math.sqrt(252) if std > 0 else 0
        else:
            sharpe = 0
        
        # Drawdown
        ec = [10000]
        for p in pnls: ec.append(ec[-1] + p)
        peak = max(ec)
        max_dd = 0
        for eq in ec:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        return {
            "status": "OK",
            "period_days": lookback_days,
            "total_trades": n,
            "net_pnl": round(sum(pnls), 2),
            "profit_factor": round(gp / gl, 2),
            "expectancy": round(statistics.mean(pnls), 2),
            "win_rate": round(len(wins) / n * 100, 1),
            "sharpe": round(sharpe, 2),
            "max_drawdown": round(max_dd, 2),
            "avg_r": round(statistics.mean([t.get("actual_rr", t.get("r_multiple", 0)) or 0 for t in trades]), 2),
            "sessions": {k: {**v, "win_rate": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] > 0 else 0} for k, v in session_stats.items()},
            "regimes": {k: {**v, "win_rate": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] > 0 else 0} for k, v in regime_stats.items()},
            "top_symbols": sorted([{"symbol": s, **v} for s, v in sym_stats.items()], key=lambda x: x["pnl"], reverse=True)[:10],
            "bottom_symbols": sorted([{"symbol": s, **v} for s, v in sym_stats.items()], key=lambda x: x["pnl"])[:5],
        }

    def detect_drift(self, lookback_short: int = 7, lookback_long: int = 30) -> Dict:
        """Compare recent performance against longer-term baseline."""
        short = self.get_live_metrics(lookback_short)
        long = self.get_live_metrics(lookback_long)
        
        if short.get("status") == "NO_DATA" or long.get("status") == "NO_DATA":
            return {"status": "NO_DATA"}
        
        drift = {}
        for metric in ["profit_factor", "expectancy", "win_rate", "sharpe", "avg_r"]:
            short_val = short.get(metric, 0)
            long_val = long.get(metric, 0)
            if long_val != 0:
                dev = (short_val - long_val) / abs(long_val) * 100
                drift[metric] = {
                    "short": short_val,
                    "long": long_val,
                    "deviation_pct": round(dev, 1),
                    "status": "✅" if abs(dev) < 15 else "⚠️" if abs(dev) < 30 else "❌",
                }
        
        # Overall drift status
        deviations = [abs(d["deviation_pct"]) for d in drift.values()]
        avg_dev = statistics.mean(deviations) if deviations else 0
        health = "STABLE" if avg_dev < 10 else "DRIFTING" if avg_dev < 25 else "SIGNIFICANT_DRIFT"
        
        return {
            "status": "OK",
            "short_period": lookback_short,
            "long_period": lookback_long,
            "drift": drift,
            "avg_deviation": round(avg_dev, 1),
            "health": health,
        }

    def get_signal_quality(self, lookback_days: int = 30) -> Dict:
        """Analyze signal quality distribution."""
        journal = ExtendedJournal(self.db_path)
        trades = journal.get_all(lookback_days)
        
        if not trades:
            return {"status": "NO_DATA"}
        
        # Confidence distribution
        conf_buckets = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0})
        for t in trades:
            conf = t.get("confidence", 0)
            if conf >= 90: bucket = "90+"
            elif conf >= 80: bucket = "80-90"
            elif conf >= 70: bucket = "70-80"
            elif conf >= 60: bucket = "60-70"
            else: bucket = "<60"
            conf_buckets[bucket]["trades"] += 1
            if t.get("net_profit", 0) > 0:
                conf_buckets[bucket]["wins"] += 1
            conf_buckets[bucket]["pnl"] += t.get("net_profit", 0)
        
        # Exit reason distribution
        exit_reasons = defaultdict(int)
        for t in trades:
            reason = t.get("exit_reason", t.get("reason_exit", "unknown"))
            exit_reasons[reason] += 1
        
        # Decision distribution (if available)
        decisions = defaultdict(int)
        for t in trades:
            decision = t.get("decision", t.get("execution_decision", "unknown"))
            decisions[decision] += 1
        
        return {
            "status": "OK",
            "period_days": lookback_days,
            "total_trades": len(trades),
            "confidence_distribution": {k: {
                **v,
                "win_rate": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] > 0 else 0,
            } for k, v in sorted(conf_buckets.items())},
            "exit_reasons": dict(sorted(exit_reasons.items(), key=lambda x: x[1], reverse=True)),
            "decision_distribution": dict(decisions),
        }

    def print_monitor(self, lookback_days: int = 7) -> None:
        """Print production monitoring report."""
        live = self.get_live_metrics(lookback_days)
        drift = self.detect_drift()
        quality = self.get_signal_quality()
        
        print(f"\n{'═' * 100}")
        print(f"  PRODUCTION MONITOR — Last {lookback_days} Days")
        print(f"{'═' * 100}")
        
        if live.get("status") != "NO_DATA":
            print(f"\n  Live Performance:")
            print(f"    Trades: {live['total_trades']} | Net PnL: ${live['net_pnl']:,.2f}")
            print(f"    PF: {live['profit_factor']:.2f} | WR: {live['win_rate']:.1f}% | Sharpe: {live['sharpe']:.2f}")
            print(f"    Expectancy: ${live['expectancy']:.2f} | Avg R: {live['avg_r']:.2f}R | Max DD: {live['max_drawdown']:.1f}%")
            
            if live.get("top_symbols"):
                print(f"\n  Top Symbols:")
                for s in live["top_symbols"][:5]:
                    print(f"    {s['symbol']:<16} trades={s['trades']:>3} PnL=${s['pnl']:>+,.2f}")
        
        if drift.get("status") != "NO_DATA":
            print(f"\n  Drift Detection ({drift['short_period']}d vs {drift['long_period']}d):")
            print(f"    Status: {drift['health']} | Avg Deviation: {drift['avg_deviation']:.1f}%")
            for metric, d in drift.get("drift", {}).items():
                name = metric.replace("_", " ").title()
                print(f"    {d['status']} {name:<20} short={d['short']:.2f} long={d['long']:.2f} ({d['deviation_pct']:+.1f}%)")
        
        if quality.get("status") != "NO_DATA":
            print(f"\n  Signal Quality Distribution:")
            for bucket, stats in quality.get("confidence_distribution", {}).items():
                print(f"    {bucket:<10} trades={stats['trades']:>3} WR={stats['win_rate']:.1f}% PnL=${stats['pnl']:>+,.2f}")
        
        print(f"\n{'═' * 100}")


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    ia = InstitutionalAnalytics()
    report = ia.full_analysis(lookback_days=days)
    ia.reporter.print_report(report)
