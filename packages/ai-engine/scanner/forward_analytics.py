"""
Forward Analytics Module — Live forward-test validation metrics.

Calculates ONLY from forward_test.db data.
No historical data. No projections. No estimates.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Dict, Optional
from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "forward_test.db"


class ForwardAnalytics:
    """
    Computes forward-test analytics from live data only.
    """
    
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else _DB_PATH
    
    def get_full_report(self) -> Dict:
        """Generate complete forward-test analytics report."""
        if not self.db_path.exists():
            return self._empty_report("forward_test.db does not exist")
        
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        
        report = {}
        
        # Basic counts
        report["forward_signals"] = db.execute("SELECT COUNT(*) FROM forward_signals").fetchone()[0]
        report["forward_trades"] = db.execute("SELECT COUNT(*) FROM forward_trades").fetchone()[0]
        report["forward_closed"] = db.execute("SELECT COUNT(*) FROM forward_trades WHERE outcome != ''").fetchone()[0]
        
        if report["forward_closed"] == 0:
            db.close()
            report["status"] = "INSUFFICIENT LIVE EVIDENCE"
            return report
        
        # Core metrics
        closed = db.execute("SELECT * FROM forward_trades WHERE outcome != ''").fetchall()
        closed = [dict(r) for r in closed]
        
        total = len(closed)
        wins = sum(1 for t in closed if t["outcome"] == "win")
        losses = total - wins
        
        total_win_pnl = sum(t["net_pnl"] for t in closed if t["outcome"] == "win")
        total_loss_pnl = abs(sum(t["net_pnl"] for t in closed if t["outcome"] != "win"))
        total_pnl = sum(t["net_pnl"] for t in closed)
        
        report["forward_win_rate"] = round(wins / total * 100, 1) if total else 0
        report["forward_pf"] = round(total_win_pnl / total_loss_pnl, 2) if total_loss_pnl > 0 else (99.9 if total_win_pnl > 0 else 0)
        report["forward_expectancy"] = round(total_pnl / total, 2) if total else 0
        report["forward_net_pnl"] = round(total_pnl, 2)
        report["forward_avg_win"] = round(total_win_pnl / wins, 2) if wins else 0
        report["forward_avg_loss"] = round(-total_loss_pnl / losses, 2) if losses else 0
        
        # MSS Validation
        report["mss"] = self._validate_feature(db, "mss_score", closed)
        report["sweep"] = self._validate_feature(db, "sweep_score", closed)
        report["fvg"] = self._validate_feature(db, "fvg_score", closed)
        
        # Confidence Validation
        report["confidence"] = self._validate_confidence(db, closed)
        
        # Regime Analysis
        report["regimes"] = self._analyze_group(db, "regime", closed)
        
        # Session Analysis
        report["sessions"] = self._analyze_group(db, "session", closed)
        
        # Hold Time Analysis
        report["hold_times"] = self._analyze_hold_times(closed)
        
        db.close()
        report["status"] = "VALID"
        return report
    
    def _validate_feature(self, db, col_name: str, closed: list) -> Dict:
        """Compare Present vs Absent for a feature."""
        present = [t for t in closed if (t.get(col_name) or 0) > 0]
        absent = [t for t in closed if (t.get(col_name) or 0) == 0]
        
        result = {"present_count": len(present), "absent_count": len(absent)}
        
        if len(present) >= 10:
            result["present_wr"] = round(sum(1 for t in present if t["outcome"] == "win") / len(present) * 100, 1)
            result["present_pnl"] = round(sum(t["net_pnl"] for t in present), 2)
            result["present_proven"] = result["present_pnl"] > 0
        else:
            result["present_proven"] = None  # NOT PROVEN
        
        if len(absent) >= 10:
            result["absent_wr"] = round(sum(1 for t in absent if t["outcome"] == "win") / len(absent) * 100, 1)
            result["absent_pnl"] = round(sum(t["net_pnl"] for t in absent), 2)
        
        result["improves"] = (result.get("present_pnl", 0) > result.get("absent_pnl", 0)) if result.get("present_proven") is not None else None
        return result
    
    def _validate_confidence(self, db, closed: list) -> Dict:
        """Bucket confidence and check predictiveness."""
        buckets = {}
        for t in closed:
            conf = t.get("confidence_100", 0) or 0
            if conf < 55: b = "0.50-0.55"
            elif conf < 60: b = "0.55-0.60"
            elif conf < 65: b = "0.60-0.65"
            elif conf < 70: b = "0.65-0.70"
            else: b = "0.70+"
            
            if b not in buckets:
                buckets[b] = {"count": 0, "wins": 0, "pnl": 0}
            buckets[b]["count"] += 1
            buckets[b]["pnl"] += t.get("net_pnl", 0) or 0
            if t.get("outcome") == "win":
                buckets[b]["wins"] += 1
        
        for b in buckets:
            buckets[b]["wr"] = round(buckets[b]["wins"] / buckets[b]["count"] * 100, 1) if buckets[b]["count"] else 0
            buckets[b]["pnl"] = round(buckets[b]["pnl"], 2)
        
        profitable = sum(1 for b in buckets.values() if b["pnl"] > 0)
        total_buckets = len(buckets)
        
        return {
            "buckets": buckets,
            "profitable_buckets": profitable,
            "total_buckets": total_buckets,
            "predictive": "YES" if profitable == total_buckets else ("PARTIALLY" if profitable > 0 else "NO"),
        }
    
    def _analyze_group(self, db, col: str, closed: list) -> Dict:
        """Group analysis by a column."""
        groups = {}
        for t in closed:
            g = t.get(col, "unknown") or "unknown"
            if g not in groups:
                groups[g] = {"count": 0, "wins": 0, "pnl": 0}
            groups[g]["count"] += 1
            groups[g]["pnl"] += t.get("net_pnl", 0) or 0
            if t.get("outcome") == "win":
                groups[g]["wins"] += 1
        
        for g in groups:
            groups[g]["wr"] = round(groups[g]["wins"] / groups[g]["count"] * 100, 1) if groups[g]["count"] else 0
            groups[g]["pnl"] = round(groups[g]["pnl"], 2)
        
        return groups
    
    def _analyze_hold_times(self, closed: list) -> Dict:
        """Analyze by hold time buckets."""
        buckets = {}
        for t in closed:
            hold = t.get("hold_minutes", 0) or 0
            if hold < 15: b = "0-15m"
            elif hold < 30: b = "15-30m"
            elif hold < 60: b = "30-60m"
            elif hold < 120: b = "60-120m"
            else: b = "120+m"
            
            if b not in buckets:
                buckets[b] = {"count": 0, "wins": 0, "pnl": 0}
            buckets[b]["count"] += 1
            buckets[b]["pnl"] += t.get("net_pnl", 0) or 0
            if t.get("outcome") == "win":
                buckets[b]["wins"] += 1
        
        for b in buckets:
            buckets[b]["wr"] = round(buckets[b]["wins"] / buckets[b]["count"] * 100, 1) if buckets[b]["count"] else 0
            buckets[b]["pnl"] = round(buckets[b]["pnl"], 2)
        
        return buckets
    
    def _empty_report(self, reason: str) -> Dict:
        return {
            "status": "INSUFFICIENT LIVE EVIDENCE",
            "reason": reason,
            "forward_signals": 0,
            "forward_trades": 0,
            "forward_closed": 0,
            "forward_win_rate": 0,
            "forward_pf": 0,
            "forward_expectancy": 0,
            "forward_net_pnl": 0,
        }
    
    def log_report(self) -> None:
        """Log a summary of the forward-test report."""
        report = self.get_full_report()
        logger.info("📊 FORWARD ANALYTICS: signals={} closed={} WR={} PF={} Exp=${} PnL=${}",
                     report.get("forward_signals", 0),
                     report.get("forward_closed", 0),
                     report.get("forward_win_rate", 0),
                     report.get("forward_pf", 0),
                     report.get("forward_expectancy", 0),
                     report.get("forward_net_pnl", 0))


# Global singleton
forward_analytics = ForwardAnalytics()
