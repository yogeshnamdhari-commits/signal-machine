"""
Decision Audit Logger — Complete evidence trail for every trade decision.

READ-ONLY with respect to upstream data. Never modifies signals or positions.

Per Master Directive:
    "For every trade, log: Executed or Rejected, Reason, Trade Quality,
     Expectancy, Portfolio Heat, Correlation, Execution Quality, Final Decision.
     This creates a complete evidence trail for future optimization."

Logging:
    - Every signal processed through the pipeline
    - Every decision made (EXECUTE / REJECT / MONITOR)
    - Every engine's contribution to the decision
    - Every rejection reason
    - Full audit trail for compliance and optimization
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# DATABASE PATH
# ═══════════════════════════════════════════════════════════════

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


class DecisionAuditLogger:
    """
    Logs every trade decision with full audit trail.

    Per Master Directive: Complete evidence trail for future optimization.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._memory_log: List[Dict] = []
        self._ensure_table()

    def log_decision(
        self,
        signal: Dict[str, Any],
        decision: str,  # EXECUTE / REJECT / MONITOR
        priority: str,
        trade_quality_score: float = 0.0,
        expected_value_r: float = 0.0,
        institution_agreement: float = 0.0,
        regime_approved: bool = False,
        reward_approved: bool = False,
        portfolio_approved: bool = False,
        sizing_approved: bool = False,
        execution_quality: float = 0.0,
        correlation_reduction: float = 1.0,
        risk_state: str = "NORMAL",
        risk_multiplier: float = 1.0,
        rejection_reasons: Optional[List[str]] = None,
        execution_details: Optional[Dict] = None,
    ) -> None:
        """
        Log a complete trade decision.

        Args:
            signal: Original signal dict
            decision: Final decision (EXECUTE / REJECT / MONITOR)
            priority: Priority classification
            trade_quality_score: TQ composite score
            expected_value_r: Expected value in R-multiples
            institution_agreement: Institution agreement ratio
            regime_approved: Regime filter result
            reward_approved: Reward filter result
            portfolio_approved: Portfolio manager result
            sizing_approved: Position sizing result
            execution_quality: Execution quality score
            correlation_reduction: Correlation adjustment factor
            risk_state: Current risk governor state
            risk_multiplier: Risk governor multiplier
            rejection_reasons: List of rejection reasons
            execution_details: Execution details if executed
        """
        symbol = signal.get("symbol", "")
        side = signal.get("side", "")
        entry = signal.get("entry_price", signal.get("entry", 0))
        sl = signal.get("stop_loss", 0)
        tp = signal.get("take_profit", 0)
        rr = signal.get("risk_reward", 0)
        confidence = signal.get("confidence", 0)
        regime = signal.get("regime", signal.get("market_regime", "unknown"))
        inst_score = signal.get("institutional_score", 0)

        record = {
            "timestamp": time.time(),
            "symbol": symbol,
            "side": side,
            "decision": decision,
            "priority": priority,
            "entry_price": entry,
            "stop_loss": sl,
            "take_profit": tp,
            "risk_reward": rr,
            "confidence": confidence,
            "regime": regime,
            "institutional_score": inst_score,
            "trade_quality_score": trade_quality_score,
            "expected_value_r": expected_value_r,
            "institution_agreement": institution_agreement,
            "regime_approved": regime_approved,
            "reward_approved": reward_approved,
            "portfolio_approved": portfolio_approved,
            "sizing_approved": sizing_approved,
            "execution_quality": execution_quality,
            "correlation_reduction": correlation_reduction,
            "risk_state": risk_state,
            "risk_multiplier": risk_multiplier,
            "rejection_reasons": json.dumps(rejection_reasons or []),
            "execution_details": json.dumps(execution_details or {}),
        }

        # Store in memory
        self._memory_log.append(record)

        # Store in database
        self._store_record(record)

        logger.info(
            "AUDIT: {} {} → {} [{}] (TQ={:.1f} EV={:.3f}R inst={:.0%} "
            "regime={} reward={} portfolio={} sizing={} exec_q={:.1f} "
            "corr={:.0%} risk={}×{})",
            symbol, side, decision, priority,
            trade_quality_score, expected_value_r, institution_agreement,
            "✓" if regime_approved else "✗",
            "✓" if reward_approved else "✗",
            "✓" if portfolio_approved else "✗",
            "✓" if sizing_approved else "✗",
            execution_quality, correlation_reduction,
            risk_state, risk_multiplier,
        )

    def get_history(
        self,
        symbol: Optional[str] = None,
        decision: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict]:
        """
        Get audit history.

        Args:
            symbol: Filter by symbol
            decision: Filter by decision type
            limit: Maximum records to return

        Returns:
            List of audit records
        """
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            query = "SELECT * FROM decision_audit"
            params = []
            conditions = []

            if symbol:
                conditions.append("symbol = ?")
                params.append(symbol)
            if decision:
                conditions.append("decision = ?")
                params.append(decision)

            if conditions:
                query += " WHERE " + " AND ".join(conditions)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cur.execute(query, params)
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()

            return rows

        except Exception as e:
            logger.warning("Audit history error: {}", e)
            return self._memory_log[-limit:]

    def get_stats(self) -> Dict:
        """Get audit statistics."""
        total = len(self._memory_log)
        if total == 0:
            return {"total": 0}

        decisions = {}
        priorities = {}
        for r in self._memory_log:
            d = r.get("decision", "UNKNOWN")
            p = r.get("priority", "UNKNOWN")
            decisions[d] = decisions.get(d, 0) + 1
            priorities[p] = priorities.get(p, 0) + 1

        avg_tq = sum(r.get("trade_quality_score", 0) for r in self._memory_log) / total
        avg_ev = sum(r.get("expected_value_r", 0) for r in self._memory_log) / total

        return {
            "total": total,
            "decisions": decisions,
            "priorities": priorities,
            "avg_tq_score": round(avg_tq, 2),
            "avg_expected_value": round(avg_ev, 3),
            "execute_rate": round(decisions.get("EXECUTE", 0) / total * 100, 1),
        }

    def _ensure_table(self) -> None:
        """Create audit table if it doesn't exist."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            cur.execute("""
                CREATE TABLE IF NOT EXISTS decision_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL,
                    symbol TEXT,
                    side TEXT,
                    decision TEXT,
                    priority TEXT,
                    entry_price REAL,
                    stop_loss REAL,
                    take_profit REAL,
                    risk_reward REAL,
                    confidence REAL,
                    regime TEXT,
                    institutional_score REAL,
                    trade_quality_score REAL,
                    expected_value_r REAL,
                    institution_agreement REAL,
                    regime_approved INTEGER,
                    reward_approved INTEGER,
                    portfolio_approved INTEGER,
                    sizing_approved INTEGER,
                    execution_quality REAL,
                    correlation_reduction REAL,
                    risk_state TEXT,
                    risk_multiplier REAL,
                    rejection_reasons TEXT,
                    execution_details TEXT
                )
            """)

            # Create index for common queries
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_symbol
                ON decision_audit(symbol)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_decision
                ON decision_audit(decision)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_timestamp
                ON decision_audit(timestamp)
            """)

            conn.commit()
            conn.close()

        except Exception as e:
            logger.warning("Audit table creation error: {}", e)

    def _store_record(self, record: Dict) -> None:
        """Store audit record in database."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            cur.execute("""
                INSERT INTO decision_audit (
                    timestamp, symbol, side, decision, priority,
                    entry_price, stop_loss, take_profit, risk_reward,
                    confidence, regime, institutional_score,
                    trade_quality_score, expected_value_r, institution_agreement,
                    regime_approved, reward_approved, portfolio_approved, sizing_approved,
                    execution_quality, correlation_reduction,
                    risk_state, risk_multiplier, rejection_reasons, execution_details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record["timestamp"], record["symbol"], record["side"],
                record["decision"], record["priority"],
                record["entry_price"], record["stop_loss"], record["take_profit"],
                record["risk_reward"], record["confidence"], record["regime"],
                record["institutional_score"],
                record["trade_quality_score"], record["expected_value_r"],
                record["institution_agreement"],
                1 if record["regime_approved"] else 0,
                1 if record["reward_approved"] else 0,
                1 if record["portfolio_approved"] else 0,
                1 if record["sizing_approved"] else 0,
                record["execution_quality"], record["correlation_reduction"],
                record["risk_state"], record["risk_multiplier"],
                record["rejection_reasons"], record["execution_details"],
            ))

            conn.commit()
            conn.close()

        except Exception as e:
            logger.warning("Audit record storage error: {}", e)
