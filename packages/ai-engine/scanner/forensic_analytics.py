"""
Forensic Analytics — FIX #7: Post-trade analytics for production monitoring.

Generates comprehensive analytics from completed trades.
Dashboard-ready output for monitoring pipeline health.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional
from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


class ForensicAnalytics:
    """
    FIX #7: Post-trade forensic analytics.
    """
    
    def __init__(self) -> None:
        self._last_analysis: float = 0
        self._analysis_interval: float = 300  # Re-analyze every 5 min
        self._cached_results: Dict = {}
    
    def analyze(self, force: bool = False) -> Dict:
        """Run full forensic analysis on completed trades."""
        now = time.time()
        if not force and (now - self._last_analysis) < self._analysis_interval:
            return self._cached_results
        
        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            db.row_factory = sqlite3.Row
            
            trades = db.execute("""
                SELECT symbol, side, entry_price, quantity, leverage,
                       stop_loss, take_profit, pnl, fees, status,
                       opened_at, closed_at, exit_reason, strategy_version,
                       confidence, regime, institutional_score, risk_reward,
                       hold_minutes, session, mfe_pct, mae_pct,
                       alpha_score, alpha_tier
                FROM positions
                WHERE status = 'closed' AND pnl IS NOT NULL
                ORDER BY opened_at
            """).fetchall()
            
            if not trades:
                db.close()
                return {}
            
            trades = [dict(t) for t in trades]
            n = len(trades)
            wins = [t for t in trades if t["pnl"] > 0]
            losses = [t for t in trades if t["pnl"] <= 0]
            total_pnl = sum(t["pnl"] for t in trades)
            win_pnl = sum(t["pnl"] for t in wins)
            loss_pnl = abs(sum(t["pnl"] for t in losses))
            
            results = {
                "timestamp": now,
                "total_trades": n,
                "win_count": len(wins),
                "loss_count": len(losses),
                "win_rate": len(wins) / n * 100,
                "profit_factor": win_pnl / loss_pnl if loss_pnl > 0 else 99.9,
                "expectancy": total_pnl / n,
                "total_pnl": total_pnl,
                "avg_winner": win_pnl / len(wins) if wins else 0,
                "avg_loser": -loss_pnl / len(losses) if losses else 0,
            }
            
            # ── Holding Time Analysis ──
            hold_buckets = {"0-15m": [], "15-30m": [], "30-60m": [], "1-2hr": [], "2-4hr": [], "4hr+": []}
            for t in trades:
                h = t["hold_minutes"] or 0
                if h < 15: hold_buckets["0-15m"].append(t)
                elif h < 30: hold_buckets["15-30m"].append(t)
                elif h < 60: hold_buckets["30-60m"].append(t)
                elif h < 120: hold_buckets["1-2hr"].append(t)
                elif h < 240: hold_buckets["2-4hr"].append(t)
                else: hold_buckets["4hr+"].append(t)
            
            results["holding_time"] = {}
            for bucket, group in hold_buckets.items():
                if group:
                    w = [t for t in group if t["pnl"] > 0]
                    l = [t for t in group if t["pnl"] <= 0]
                    wp = sum(t["pnl"] for t in w)
                    lp = abs(sum(t["pnl"] for t in l))
                    results["holding_time"][bucket] = {
                        "trades": len(group),
                        "win_rate": len(w) / len(group) * 100,
                        "pf": wp / lp if lp > 0 else 99.9,
                        "expectancy": sum(t["pnl"] for t in group) / len(group),
                        "pnl": sum(t["pnl"] for t in group),
                    }
            
            # ── Regime Analysis ──
            regimes = set(t["regime"] for t in trades if t["regime"])
            results["regime"] = {}
            for regime in regimes:
                group = [t for t in trades if t["regime"] == regime]
                if group:
                    w = [t for t in group if t["pnl"] > 0]
                    l = [t for t in group if t["pnl"] <= 0]
                    wp = sum(t["pnl"] for t in w)
                    lp = abs(sum(t["pnl"] for t in l))
                    results["regime"][regime] = {
                        "trades": len(group),
                        "win_rate": len(w) / len(group) * 100,
                        "pf": wp / lp if lp > 0 else 99.9,
                        "expectancy": sum(t["pnl"] for t in group) / len(group),
                        "pnl": sum(t["pnl"] for t in group),
                    }
            
            # ── Session Analysis ──
            sessions = set(t["session"] for t in trades if t["session"])
            results["session"] = {}
            for session in sessions:
                group = [t for t in trades if t["session"] == session]
                if group:
                    w = [t for t in group if t["pnl"] > 0]
                    l = [t for t in group if t["pnl"] <= 0]
                    wp = sum(t["pnl"] for t in w)
                    lp = abs(sum(t["pnl"] for t in l))
                    results["session"][session] = {
                        "trades": len(group),
                        "win_rate": len(w) / len(group) * 100,
                        "pf": wp / lp if lp > 0 else 99.9,
                        "expectancy": sum(t["pnl"] for t in group) / len(group),
                        "pnl": sum(t["pnl"] for t in group),
                    }
            
            # ── Symbol Top/Bottom 10 ──
            sym_stats = defaultdict(lambda: {"trades": 0, "pnl": 0, "wins": 0})
            for t in trades:
                s = t["symbol"]
                sym_stats[s]["trades"] += 1
                sym_stats[s]["pnl"] += t["pnl"]
                sym_stats[s]["wins"] += 1 if t["pnl"] > 0 else 0
            
            for s in sym_stats:
                d = sym_stats[s]
                d["win_rate"] = d["wins"] / d["trades"] * 100 if d["trades"] > 0 else 0
                d["expectancy"] = d["pnl"] / d["trades"] if d["trades"] > 0 else 0
            
            sorted_syms = sorted(sym_stats.items(), key=lambda x: x[1]["expectancy"], reverse=True)
            results["top_symbols"] = [{"symbol": s, **d} for s, d in sorted_syms[:10]]
            results["bottom_symbols"] = [{"symbol": s, **d} for s, d in sorted_syms[-10:]]
            
            # ── Confidence Bucket Analysis ──
            conf_buckets = defaultdict(lambda: {"trades": 0, "pnl": 0, "wins": 0})
            for t in trades:
                c = t["confidence"] or 0
                bucket = f"{int(c * 10) / 10:.1f}-{int(c * 10 + 1) / 10:.1f}"
                conf_buckets[bucket]["trades"] += 1
                conf_buckets[bucket]["pnl"] += t["pnl"]
                conf_buckets[bucket]["wins"] += 1 if t["pnl"] > 0 else 0
            
            results["confidence_buckets"] = {}
            for bucket, d in sorted(conf_buckets.items()):
                d["win_rate"] = d["wins"] / d["trades"] * 100 if d["trades"] > 0 else 0
                d["expectancy"] = d["pnl"] / d["trades"] if d["trades"] > 0 else 0
                results["confidence_buckets"][bucket] = d

            # ── Exit Reason Analysis ──
            exit_reasons = defaultdict(lambda: {"trades": 0, "pnl": 0, "wins": 0})
            for t in trades:
                r = t.get("exit_reason", "unknown") or "unknown"
                exit_reasons[r]["trades"] += 1
                exit_reasons[r]["pnl"] += t["pnl"]
                exit_reasons[r]["wins"] += 1 if t["pnl"] > 0 else 0
            
            results["exit_reasons"] = {}
            for r, d in exit_reasons.items():
                d["win_rate"] = d["wins"] / d["trades"] * 100 if d["trades"] > 0 else 0
                d["expectancy"] = d["pnl"] / d["trades"] if d["trades"] > 0 else 0
                results["exit_reasons"][r] = d

            # ── RR Analysis ──
            rr_buckets = {"<1.0": (0, 1.0), "1.0-1.5": (1.0, 1.5), "1.5-2.0": (1.5, 2.0),
                         "2.0-2.5": (2.0, 2.5), "2.5-3.0": (2.5, 3.0), "3.0+": (3.0, 999)}
            results["rr_analysis"] = {}
            for bucket, (lo, hi) in rr_buckets.items():
                group = [t for t in trades if lo <= (t.get("risk_reward") or 0) < hi]
                if group:
                    w = [t for t in group if t["pnl"] > 0]
                    l = [t for t in group if t["pnl"] <= 0]
                    wp = sum(t["pnl"] for t in w)
                    lp = abs(sum(t["pnl"] for t in l))
                    results["rr_analysis"][bucket] = {
                        "trades": len(group),
                        "win_rate": len(w) / len(group) * 100,
                        "pf": wp / lp if lp > 0 else 99.9,
                        "expectancy": sum(t["pnl"] for t in group) / len(group),
                        "pnl": sum(t["pnl"] for t in group),
                    }
            
            # ── MAE/MFE Analysis ──
            mae_vals = [t["mae_pct"] for t in trades if t["mae_pct"] and t["mae_pct"] > 0]
            mfe_vals = [t["mfe_pct"] for t in trades if t["mfe_pct"] and t["mfe_pct"] > 0]
            if mae_vals:
                results["mae"] = {
                    "avg": sum(mae_vals) / len(mae_vals),
                    "max": max(mae_vals),
                    "median": sorted(mae_vals)[len(mae_vals) // 2],
                }
            if mfe_vals:
                results["mfe"] = {
                    "avg": sum(mfe_vals) / len(mfe_vals),
                    "max": max(mfe_vals),
                    "median": sorted(mfe_vals)[len(mfe_vals) // 2],
                }
            
            db.close()
            self._last_analysis = now
            self._cached_results = results
            return results
            
        except Exception as e:
            logger.warning("ForensicAnalytics failed: {}", e)
            return {}
    
    def get_dashboard_data(self) -> Dict:
        """Get formatted data for dashboard display."""
        analysis = self.analyze()
        if not analysis:
            return {"error": "No data available"}
        return analysis
