"""
Confidence Validation Module — Phase 5: Track confidence predictiveness.

Measures whether higher confidence actually produces better trades.
Updates automatically after every 100 closed trades.

NO threshold changes. Pure measurement.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Dict, List
from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"
_REPORT_PATH = Path(__file__).resolve().parent.parent / "data" / "confidence_validation_report.json"

# Confidence buckets for analysis
BUCKETS = [
    (0, 0.50, "0.00-0.50"),
    (0.50, 0.55, "0.50-0.55"),
    (0.55, 0.60, "0.55-0.60"),
    (0.60, 0.65, "0.60-0.65"),
    (0.65, 0.70, "0.65-0.70"),
    (0.70, 0.75, "0.70-0.75"),
    (0.75, 1.01, "0.75+"),
]

# Auto-update threshold
UPDATE_INTERVAL = 100  # Re-analyze every 100 closed trades


class ConfidenceValidator:
    """
    Phase 5: Measures confidence predictiveness.
    
    For each confidence bucket:
    - Trades count
    - Win Rate
    - Profit Factor
    - Expectancy
    - Total PnL
    
    Answer: Does higher confidence = better trades?
    """
    
    def __init__(self) -> None:
        self._last_trade_count = 0
        self._last_analysis: float = 0
        self._cached_results: Dict = {}
    
    def analyze(self, force: bool = False) -> Dict:
        """Run confidence validation analysis."""
        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            db.row_factory = sqlite3.Row
            
            total = db.execute(
                "SELECT COUNT(*) FROM positions WHERE status='closed' AND pnl IS NOT NULL"
            ).fetchone()[0]
            
            if not force and total == self._last_trade_count:
                return self._cached_results
            
            self._last_trade_count = total
            
            results = {
                "timestamp": time.time(),
                "total_trades": total,
                "buckets": {},
                "best_bucket": "",
                "worst_bucket": "",
                "is_predictive": False,
                "correlation": 0,
            }
            
            for lo, hi, label in BUCKETS:
                rows = db.execute("""
                    SELECT pnl, confidence, institutional_score, hold_minutes
                    FROM positions
                    WHERE status='closed' AND pnl IS NOT NULL
                    AND confidence >= ? AND confidence < ?
                """, (lo, hi)).fetchall()
                
                if not rows:
                    results["buckets"][label] = {
                        "trades": 0, "wr": 0, "pf": 0, "exp": 0, "pnl": 0
                    }
                    continue
                
                trades = [dict(r) for r in rows]
                n = len(trades)
                wins = [t for t in trades if t["pnl"] > 0]
                losses = [t for t in trades if t["pnl"] <= 0]
                tp = sum(t["pnl"] for t in trades)
                wp = sum(t["pnl"] for t in wins)
                lp = abs(sum(t["pnl"] for t in losses))
                
                results["buckets"][label] = {
                    "trades": n,
                    "wr": len(wins) / n * 100 if n else 0,
                    "pf": wp / lp if lp else 99.9,
                    "exp": tp / n if n else 0,
                    "pnl": tp,
                }
            
            # Find best/worst buckets
            valid_buckets = {k: v for k, v in results["buckets"].items() if v["trades"] >= 10}
            if valid_buckets:
                best = max(valid_buckets.items(), key=lambda x: x[1]["exp"])
                worst = min(valid_buckets.items(), key=lambda x: x[1]["exp"])
                results["best_bucket"] = best[0]
                results["worst_bucket"] = worst[0]
                
                # Check if confidence is predictive (monotonic improvement)
                sorted_by_exp = sorted(valid_buckets.items(), key=lambda x: float(x[0].split("-")[0]) if "-" in x[0] else 0)
                improving = sum(1 for i in range(1, len(sorted_by_exp)) if sorted_by_exp[i][1]["exp"] > sorted_by_exp[i-1][1]["exp"])
                results["is_predictive"] = improving >= len(sorted_by_exp) * 0.6  # 60% monotonic
            
            db.close()
            self._last_analysis = time.time()
            self._cached_results = results
            
            # Save report
            try:
                import json
                _REPORT_PATH.write_text(json.dumps(results, indent=2))
            except:
                pass
            
            return results
            
        except Exception as e:
            logger.warning("ConfidenceValidator failed: {}", e)
            return {}
    
    def get_report(self) -> Dict:
        """Get latest validation report."""
        return self.analyze()
    
    def print_report(self) -> None:
        """Print formatted validation report."""
        results = self.analyze()
        if not results:
            print("  No data available")
            return
        
        print(f"\n  CONFIDENCE VALIDATION REPORT ({results['total_trades']} trades)")
        print(f"  {'─'*60}")
        print(f"  {'Bucket':<15} {'Trades':>7} {'WR%':>7} {'PF':>7} {'Expect':>10} {'PnL':>12}")
        print(f"  {'─'*15} {'─'*7} {'─'*7} {'─'*7} {'─'*10} {'─'*12}")
        
        for label in sorted(results["buckets"].keys()):
            b = results["buckets"][label]
            if b["trades"] == 0:
                continue
            marker = " ◄ BEST" if label == results["best_bucket"] else ""
            marker = " ◄ WORST" if label == results["worst_bucket"] else marker
            print(f"  {label:<15} {b['trades']:>7} {b['wr']:>6.1f}% {b['pf']:>7.2f} ${b['exp']:>9.2f} ${b['pnl']:>11.2f}{marker}")
        
        print(f"\n  Confidence Predictive: {'✅ YES' if results['is_predictive'] else '❌ NO'}")
        print(f"  Best Bucket: {results['best_bucket']}")
        print(f"  Worst Bucket: {results['worst_bucket']}")
