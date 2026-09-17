"""
Deployment Gate — Controls whether system may go live.

Rules (ALL must pass):
  1. Forward Signals >= 500
  2. Forward Closed Trades >= 100
  3. Forward PF > 1.20
  4. Forward Expectancy > 0
  5. Forward Net PnL > 0
  6. Every closed trade has internally consistent economic decomposition

If any fail: deployment_status = "DO NOT DEPLOY"
"""
from __future__ import annotations

import sqlite3
from math import isfinite
from pathlib import Path
from typing import Dict, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "forward_test.db"
_EPSILON = 1e-9


class DeploymentGate:
    """
    Production deployment gate.
    ONLY allows deployment when ALL criteria are met.
    """

    MIN_FORWARD_SIGNALS = 500
    MIN_FORWARD_CLOSED = 100
    MIN_FORWARD_PF = 1.20
    MIN_FORWARD_EXPECTANCY = 0.0
    MIN_FORWARD_PNL = 0.0

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else _DB_PATH

    def evaluate(self) -> Dict:
        """Evaluate all deployment criteria and fail closed on invalid evidence."""
        if not self.db_path.exists():
            return {
                "status": "DO NOT DEPLOY",
                "reason": "forward_test.db does not exist",
                "criteria": {k: "FAIL" for k in self._criterion_names()},
                "passed": 0,
                "total": 6,
            }

        try:
            with sqlite3.connect(str(self.db_path), timeout=10) as db:
                sig_count = db.execute("SELECT COUNT(*) FROM forward_signals").fetchone()[0]
                closed_count = db.execute(
                    "SELECT COUNT(*) FROM forward_trades WHERE outcome != ''"
                ).fetchone()[0]

                if (
                    not isinstance(sig_count, int)
                    or isinstance(sig_count, bool)
                    or sig_count < 0
                    or not isinstance(closed_count, int)
                    or isinstance(closed_count, bool)
                    or closed_count < 0
                ):
                    return self._invalid_evidence("invalid forward-test counts")

                criteria = {
                    "Forward Signals >= 500": sig_count >= self.MIN_FORWARD_SIGNALS,
                    "Forward Signals Count": sig_count,
                    "Forward Closed >= 100": closed_count >= self.MIN_FORWARD_CLOSED,
                    "Forward Closed Count": closed_count,
                }

                if closed_count == 0:
                    return {
                        "status": "DO NOT DEPLOY",
                        "reason": "INSUFFICIENT LIVE EVIDENCE — 0 closed trades",
                        "criteria": criteria,
                        "passed": sum(1 for k, v in criteria.items() if v is True),
                        "total": 6,
                    }

                total_wins = db.execute(
                    "SELECT COALESCE(SUM(CASE WHEN net_pnl > 0 THEN net_pnl ELSE 0 END), 0) "
                    "FROM forward_trades WHERE outcome != ''"
                ).fetchone()[0]
                total_losses = db.execute(
                    "SELECT COALESCE(SUM(CASE WHEN net_pnl <= 0 THEN ABS(net_pnl) ELSE 0 END), 0) "
                    "FROM forward_trades WHERE outcome != ''"
                ).fetchone()[0]
                pnl = db.execute(
                    "SELECT COALESCE(SUM(net_pnl), 0) FROM forward_trades WHERE outcome != ''"
                ).fetchone()[0]

                if not self._finite_number(total_wins) or not self._finite_number(total_losses):
                    return self._invalid_evidence("non-finite PnL aggregates")
                if not self._finite_number(pnl):
                    return self._invalid_evidence("non-finite net PnL aggregate")
                if total_wins < 0 or total_losses < 0:
                    return self._invalid_evidence("negative PnL aggregate")

                pf = total_wins / total_losses if total_losses > 0 else (99.9 if total_wins > 0 else 0)
                expectancy = pnl / closed_count if closed_count else 0

                if not self._finite_number(pf) or not self._finite_number(expectancy):
                    return self._invalid_evidence("non-finite deployment metrics")
                
                if not self._economic_decomposition_is_consistent(db):
                    criteria["Economic decomposition consistent"] = False
                    passed = sum(1 for k, v in criteria.items() if v is True)
                    return {
                        "status": "DO NOT DEPLOY",
                        "reason": "Failed: Economic decomposition consistency check",
                        "criteria": criteria,
                        "passed": passed,
                        "total": 6,
                    }

                criteria["Forward PF > 1.20"] = pf > self.MIN_FORWARD_PF
                criteria["Forward PF"] = round(pf, 2)
                criteria["Forward Expectancy > 0"] = expectancy > self.MIN_FORWARD_EXPECTANCY
                criteria["Forward Expectancy"] = round(expectancy, 2)
                criteria["Forward PnL > 0"] = pnl > self.MIN_FORWARD_PNL
                criteria["Forward Net PnL"] = round(pnl, 2)
                criteria["Economic decomposition consistent"] = True

                passed = sum(1 for k, v in criteria.items() if v is True)
                if passed == 6:
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
                    "total": 6,
                }
        except (sqlite3.DatabaseError, OSError) as exc:
            return self._invalid_evidence(f"invalid forward-test database: {exc}")

    @staticmethod
    def _finite_number(value: object) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value))

    @classmethod
    def _economic_decomposition_is_consistent(cls, db: sqlite3.Connection) -> bool:
        """Require every closed trade to satisfy net = gross - fees - funding - slippage."""
        columns = {row[1] for row in db.execute("PRAGMA table_info(forward_trades)").fetchall()}
        required = {"pnl", "fees", "funding", "slippage", "net_pnl"}
        if not required.issubset(columns):
            return False

        rows = db.execute(
            "SELECT pnl, fees, funding, slippage, net_pnl "
            "FROM forward_trades WHERE outcome != ''"
        ).fetchall()
        for gross, fees, funding, slippage, net in rows:
            values = (gross, fees, funding, slippage, net)
            if not all(cls._finite_number(value) for value in values):
                return False
            expected = float(gross) - float(fees) - float(funding) - float(slippage)
            if abs(float(net) - expected) > _EPSILON:
                return False
        return True

    def _invalid_evidence(self, reason: str) -> Dict:
        return {
            "status": "DO NOT DEPLOY",
            "reason": reason,
            "criteria": {k: "FAIL" for k in self._criterion_names()},
            "passed": 0,
            "total": 6,
        }

    def _criterion_names(self) -> list:
        return [
            "Forward Signals >= 500",
            "Forward Closed >= 100",
            "Forward PF > 1.20",
            "Forward Expectancy > 0",
            "Forward PnL > 0",
            "Economic decomposition consistent",
        ]

    def log_status(self) -> None:
        result = self.evaluate()
        if result["status"] == "DEPLOY":
            logger.info("🟢 DEPLOYMENT GATE: {} — {}", result["status"], result["reason"])
        else:
            logger.warning("🔴 DEPLOYMENT GATE: {} — {}", result["status"], result["reason"])


deployment_gate = DeploymentGate()
