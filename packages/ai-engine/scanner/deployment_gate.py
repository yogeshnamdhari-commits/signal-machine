"""
Deployment Gate — Controls whether system may go live.

Rules (ALL must pass):
  1. Forward Signals >= 500
  2. Forward Closed Trades >= 100
  3. Forward PF > 1.20
  4. Forward Expectancy > 0
  5. Forward Net PnL > 0

If any fail: deployment_status = "DO NOT DEPLOY"
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Dict, Tuple
from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "forward_test.db"


class DeploymentGate:
    """
    Production deployment gate.
    ONLY allows deployment when ALL criteria are met.
    """
    
    # ── Deployment Requirements ──
    MIN_FORWARD_SIGNALS = 500
    MIN_FORWARD_CLOSED = 100
    MIN_FORWARD_PF = 1.20
    MIN_FORWARD_EXPECTANCY = 0.0
    MIN_FORWARD_PNL = 0.0
    
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else _DB_PATH
    
    def evaluate(self) -> Dict:
        """
        Evaluate all deployment criteria.
        
        Returns:
            Dict with:
                - status: "DEPLOY" | "DO NOT DEPLOY"
                - criteria: dict of individual check results
                - passed: int (count of passed criteria)
                - total: int (total criteria)
                - reason: str (if DO NOT DEPLOY)
        """
        criteria = {}
        
        if not self.db_path.exists():
            return {
                "status": "DO NOT DEPLOY",
                "reason": "forward_test.db does not exist",
                "criteria": {k: "FAIL" for k in self._criterion_names()},
                "passed": 0,
                "total": 5,
            }
        
        db = sqlite3.connect(str(self.db_path), timeout=10)
        
        # 1. Forward Signals
        sig_count = db.execute("SELECT COUNT(*) FROM forward_signals").fetchone()[0]
        criteria["Forward Signals >= 500"] = sig_count >= self.MIN_FORWARD_SIGNALS
        criteria["Forward Signals Count"] = sig_count
        
        # 2. Forward Closed Trades
        closed_count = db.execute("SELECT COUNT(*) FROM forward_trades WHERE outcome != ''").fetchone()[0]
        criteria["Forward Closed >= 100"] = closed_count >= self.MIN_FORWARD_CLOSED
        criteria["Forward Closed Count"] = closed_count
        
        if closed_count == 0:
            db.close()
            return {
                "status": "DO NOT DEPLOY",
                "reason": "INSUFFICIENT LIVE EVIDENCE — 0 closed trades",
                "criteria": criteria,
                "passed": sum(1 for k, v in criteria.items() if v is True),
                "total": 5,
            }
        
        # 3. Forward PF
        tw = db.execute("SELECT COALESCE(SUM(CASE WHEN net_pnl > 0 THEN net_pnl ELSE 0 END), 0) FROM forward_trades WHERE outcome != ''").fetchone()[0]
        tl = db.execute("SELECT COALESCE(SUM(CASE WHEN net_pnl <= 0 THEN ABS(net_pnl) ELSE 0 END), 0) FROM forward_trades WHERE outcome != ''").fetchone()[0]
        pf = tw / tl if tl > 0 else (99.9 if tw > 0 else 0)
        criteria["Forward PF > 1.20"] = pf > self.MIN_FORWARD_PF
        criteria["Forward PF"] = round(pf, 2)
        
        # 4. Forward Expectancy
        pnl = db.execute("SELECT COALESCE(SUM(net_pnl), 0) FROM forward_trades WHERE outcome != ''").fetchone()[0]
        expectancy = pnl / closed_count if closed_count else 0
        criteria["Forward Expectancy > 0"] = expectancy > self.MIN_FORWARD_EXPECTANCY
        criteria["Forward Expectancy"] = round(expectancy, 2)
        
        # 5. Forward Net PnL
        criteria["Forward PnL > 0"] = pnl > self.MIN_FORWARD_PNL
        criteria["Forward Net PnL"] = round(pnl, 2)
        
        db.close()
        
        passed = sum(1 for k, v in criteria.items() if v is True)
        total = 5
        
        if passed == total:
            status = "DEPLOY"
            reason = "All criteria met"
        else:
            status = "DO NOT DEPLOY"
            failed = [k for k, v in criteria.items() if v is False]
            reason = f"Failed: {', '.join(failed)}"
        
        return {
            "status": status,
            "reason": reason,
            "criteria": criteria,
            "passed": passed,
            "total": total,
        }
    
    def _criterion_names(self) -> list:
        return [
            "Forward Signals >= 500",
            "Forward Closed >= 100",
            "Forward PF > 1.20",
            "Forward Expectancy > 0",
            "Forward PnL > 0",
        ]
    
    def log_status(self) -> None:
        """Log deployment gate status."""
        result = self.evaluate()
        if result["status"] == "DEPLOY":
            logger.info("🟢 DEPLOYMENT GATE: {} — {}", result["status"], result["reason"])
        else:
            logger.warning("🔴 DEPLOYMENT GATE: {} — {}", result["status"], result["reason"])


# Global singleton
deployment_gate = DeploymentGate()
