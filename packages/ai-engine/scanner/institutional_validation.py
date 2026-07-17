"""
Institutional Validation Module — Phase 6: Track MSS/Sweep/FVG performance.

Measures whether institutional factors actually improve trade quality.
Does NOT alter any logic. Pure measurement.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Dict, List
from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"
_REPORT_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_validation_report.json"


class InstitutionalValidator:
    """
    Phase 6: Measures MSS/Sweep/FVG performance.
    
    For each institutional factor:
    - Trades with factor
    - Trades without factor
    - Win Rate comparison
    - PF comparison
    - Expectancy comparison
    """
    
    def __init__(self) -> None:
        self._last_analysis: float = 0
        self._cached_results: Dict = {}
    
    def analyze(self, force: bool = False) -> Dict:
        """Run institutional validation analysis."""
        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            db.row_factory = sqlite3.Row
            
            results = {
                "timestamp": time.time(),
                "total_trades": 0,
                "factors": {},
                "validation_status": "CANNOT_VALIDATE",
            }
            
            total = db.execute(
                "SELECT COUNT(*) FROM positions WHERE status='closed' AND pnl IS NOT NULL"
            ).fetchone()[0]
            results["total_trades"] = total
            
            # For each factor, compare trades with vs without
            # NOTE: Most factors are 0% in historical data
            factors = [
                ("mss_score", "MSS"),
                ("sweep_score", "Sweep"),
                ("fvg_score", "FVG"),
            ]
            
            has_data = False
            for col, label in factors:
                # Trades with factor (non-zero)
                with_rows = db.execute(f"""
                    SELECT p.pnl, s.{col} as factor_val
                    FROM positions p
                    LEFT JOIN signals s ON p.signal_id = s.id
                    WHERE p.status='closed' AND p.pnl IS NOT NULL
                    AND s.{col} IS NOT NULL AND s.{col} != 0
                """).fetchall()
                
                # Trades without factor (zero or null)
                without_rows = db.execute(f"""
                    SELECT p.pnl
                    FROM positions p
                    LEFT JOIN signals s ON p.signal_id = s.id
                    WHERE p.status='closed' AND p.pnl IS NOT NULL
                    AND (s.{col} IS NULL OR s.{col} = 0)
                """).fetchall()
                
                with_trades = [dict(r) for r in with_rows]
                without_trades = [dict(r) for r in without_rows]
                
                def compute_stats(trades):
                    if not trades:
                        return {"trades": 0, "wr": 0, "pf": 0, "exp": 0, "pnl": 0}
                    n = len(trades)
                    wins = [t for t in trades if t["pnl"] > 0]
                    losses = [t for t in trades if t["pnl"] <= 0]
                    tp = sum(t["pnl"] for t in trades)
                    wp = sum(t["pnl"] for t in wins)
                    lp = abs(sum(t["pnl"] for t in losses))
                    return {
                        "trades": n,
                        "wr": len(wins) / n * 100 if n else 0,
                        "pf": wp / lp if lp else 99.9,
                        "exp": tp / n if n else 0,
                        "pnl": tp,
                    }
                
                with_stats = compute_stats(with_trades)
                without_stats = compute_stats(without_trades)
                
                if with_stats["trades"] > 0:
                    has_data = True
                
                results["factors"][label] = {
                    "with_factor": with_stats,
                    "without_factor": without_stats,
                    "data_available": with_stats["trades"] > 0,
                }
            
            if has_data:
                results["validation_status"] = "PARTIAL_DATA"
            else:
                results["validation_status"] = "CANNOT_VALIDATE"
            
            db.close()
            self._last_analysis = time.time()
            self._cached_results = results
            
            # Save report
            try:
                import json
                _REPORT_PATH.write_text(json.dumps(results, indent=2, default=str))
            except:
                pass
            
            return results
            
        except Exception as e:
            logger.warning("InstitutionalValidator failed: {}", e)
            return {}
    
    def print_report(self) -> None:
        """Print formatted validation report."""
        results = self.analyze()
        if not results:
            print("  No data available")
            return
        
        print(f"\n  INSTITUTIONAL VALIDATION REPORT ({results['total_trades']} trades)")
        print(f"  Status: {results['validation_status']}")
        print(f"  {'─'*70}")
        
        for factor, data in results["factors"].items():
            with_s = data["with_factor"]
            without_s = data["without_factor"]
            
            print(f"\n  {factor}:")
            print(f"    WITH factor:    {with_s['trades']:>5} trades WR={with_s['wr']:.1f}% PF={with_s['pf']:.2f} Exp=${with_s['exp']:.2f}")
            print(f"    WITHOUT factor: {without_s['trades']:>5} trades WR={without_s['wr']:.1f}% PF={without_s['pf']:.2f} Exp=${without_s['exp']:.2f}")
            
            if with_s["trades"] > 0 and without_s["trades"] > 0:
                diff = with_s["exp"] - without_s["exp"]
                print(f"    EDGE: ${diff:+.2f} expectancy {'✅ POSITIVE' if diff > 0 else '❌ NEGATIVE'}")
            else:
                print(f"    EDGE: CANNOT VALIDATE (insufficient data)")
