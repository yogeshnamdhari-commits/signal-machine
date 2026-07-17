"""
Exit Forensics Module — Phase 7: Track exit mechanism performance.

Classifies exits by type and measures which exit mechanism is most profitable.
Does NOT alter exit logic. Pure measurement.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Dict
from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"
_REPORT_PATH = Path(__file__).resolve().parent.parent / "data" / "exit_forensics_report.json"


class ExitForensics:
    """
    Phase 7: Measures exit mechanism performance.
    
    Classifies exits by:
    - SL (stop loss hit)
    - TP (take profit hit)
    - Timeout (max hold exceeded)
    - Trailing (trailing stop)
    - Emergency (risk event)
    - Unknown (unclassified)
    """
    
    def __init__(self) -> None:
        self._last_analysis: float = 0
        self._cached_results: Dict = {}
    
    def classify_exit(self, trade: Dict) -> str:
        """Classify exit type from trade data."""
        reason = trade.get("exit_reason", "unknown")
        hold = trade.get("hold_minutes", 0) or 0
        pnl = trade.get("pnl", 0)
        sl = trade.get("stop_loss", 0)
        tp = trade.get("take_profit", 0)
        entry = trade.get("entry_price", 0)
        
        if reason and reason != "unknown":
            if "sl" in reason.lower() or "stop" in reason.lower():
                return "SL"
            elif "tp" in reason.lower() or "profit" in reason.lower():
                return "TP"
            elif "timeout" in reason.lower() or "expire" in reason.lower():
                return "Timeout"
            elif "trail" in reason.lower():
                return "Trailing"
            elif "emergency" in reason.lower():
                return "Emergency"
        
        # Heuristic classification when reason is "unknown"
        if entry > 0 and sl > 0:
            # Check if price hit SL
            if pnl < 0 and abs(pnl) < entry * 0.05:
                return "SL"
        
        if hold < 15:
            return "Quick Exit"
        elif hold > 240:
            return "Long Hold"
        
        return "Unknown"
    
    def analyze(self, force: bool = False) -> Dict:
        """Run exit forensics analysis."""
        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            db.row_factory = sqlite3.Row
            
            rows = db.execute("""
                SELECT * FROM positions
                WHERE status='closed' AND pnl IS NOT NULL
                ORDER BY opened_at
            """).fetchall()
            
            trades = [dict(r) for r in rows]
            
            results = {
                "timestamp": time.time(),
                "total_trades": len(trades),
                "exit_types": {},
                "best_exit": "",
                "worst_exit": "",
            }
            
            # Classify each exit
            exit_groups = {}
            for t in trades:
                exit_type = self.classify_exit(t)
                if exit_type not in exit_groups:
                    exit_groups[exit_type] = []
                exit_groups[exit_type].append(t)
            
            # Compute stats for each exit type
            for exit_type, group in exit_groups.items():
                n = len(group)
                wins = [t for t in group if t["pnl"] > 0]
                losses = [t for t in group if t["pnl"] <= 0]
                tp = sum(t["pnl"] for t in group)
                wp = sum(t["pnl"] for t in wins)
                lp = abs(sum(t["pnl"] for t in losses))
                
                results["exit_types"][exit_type] = {
                    "trades": n,
                    "pct": n / len(trades) * 100 if trades else 0,
                    "wr": len(wins) / n * 100 if n else 0,
                    "pf": wp / lp if lp else 99.9,
                    "exp": tp / n if n else 0,
                    "pnl": tp,
                }
            
            # Find best/worst
            valid = {k: v for k, v in results["exit_types"].items() if v["trades"] >= 5}
            if valid:
                best = max(valid.items(), key=lambda x: x[1]["exp"])
                worst = min(valid.items(), key=lambda x: x[1]["exp"])
                results["best_exit"] = best[0]
                results["worst_exit"] = worst[0]
            
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
            logger.warning("ExitForensics failed: {}", e)
            return {}
    
    def print_report(self) -> None:
        """Print formatted forensics report."""
        results = self.analyze()
        if not results:
            print("  No data available")
            return
        
        print(f"\n  EXIT FORENSICS REPORT ({results['total_trades']} trades)")
        print(f"  {'─'*65}")
        print(f"  {'Exit Type':<20} {'Trades':>7} {'%':>7} {'WR%':>7} {'PF':>7} {'Expect':>10} {'PnL':>12}")
        print(f"  {'─'*20} {'─'*7} {'─'*7} {'─'*7} {'─'*7} {'─'*10} {'─'*12}")
        
        for exit_type, data in sorted(results["exit_types"].items(), key=lambda x: -x[1]["pnl"]):
            marker = " ◄ BEST" if exit_type == results["best_exit"] else ""
            marker = " ◄ WORST" if exit_type == results["worst_exit"] else marker
            print(f"  {exit_type:<20} {data['trades']:>7} {data['pct']:>6.1f}% {data['wr']:>6.1f}% {data['pf']:>7.2f} ${data['exp']:>9.2f} ${data['pnl']:>11.2f}{marker}")
        
        print(f"\n  Best Exit: {results['best_exit']}")
        print(f"  Worst Exit: {results['worst_exit']}")
