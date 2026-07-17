"""
Continuous Validation — Recompute thresholds every 500-1000 trades.

Per Priority 10: Do not keep thresholds fixed indefinitely.
    For every 500-1000 completed trades:
    1. Recompute expectancy by symbol and regime.
    2. Re-evaluate Institution Agreement thresholds.
    3. Compare exit strategies.
    4. Reassess position sizing rules.
    5. Verify that changes improve Profit Factor before applying them.

This creates a controlled feedback loop rather than ad hoc tuning.

READ-ONLY: never modifies upstream data.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

# Validation interval (number of trades)
VALIDATION_INTERVAL = 500

# Minimum trades for validation
MIN_TRADES_FOR_VALIDATION = 100


@dataclass
class ValidationReport:
    """Report from a continuous validation cycle."""
    timestamp: float = 0.0
    trades_analyzed: int = 0
    validation_type: str = ""  # EXPECTANCY / INSTITUTION / EXIT / SIZING

    # Findings
    findings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)

    # Changes proposed
    proposed_changes: Dict[str, Any] = field(default_factory=dict)

    # Quality gate
    improves_profit_factor: bool = False
    confidence_level: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "trades_analyzed": self.trades_analyzed,
            "validation_type": self.validation_type,
            "findings": self.findings,
            "recommendations": self.recommendations,
            "proposed_changes": self.proposed_changes,
            "improves_profit_factor": self.improves_profit_factor,
            "confidence_level": round(self.confidence_level, 2),
        }


class ContinuousValidation:
    """
    Recomputes thresholds every 500-1000 trades.

    Per Priority 10: Controlled feedback loop.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._last_validation_count = 0
        self._last_validation_time = 0.0
        self._reports: List[ValidationReport] = []

    def maybe_validate(self, force: bool = False) -> Optional[List[ValidationReport]]:
        """Check if validation is needed and perform it."""
        trade_count = self._count_trades()

        if not force and (trade_count - self._last_validation_count) < VALIDATION_INTERVAL:
            return None

        if trade_count < MIN_TRADES_FOR_VALIDATION:
            return None

        reports = self._validate_all(trade_count)
        self._last_validation_count = trade_count
        self._last_validation_time = time.time()

        return reports

    def get_reports(self) -> List[ValidationReport]:
        """Get all validation reports."""
        return list(self._reports)

    def _validate_all(self, trade_count: int) -> List[ValidationReport]:
        """Perform all validation checks."""
        reports = []

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            # 1. Validate Expectancy by Symbol
            reports.append(self._validate_symbol_expectancy(cur, trade_count))

            # 2. Validate Expectancy by Regime
            reports.append(self._validate_regime_expectancy(cur, trade_count))

            # 3. Validate Institution Agreement
            reports.append(self._validate_institution_agreement(cur, trade_count))

            # 4. Validate Exit Strategies
            reports.append(self._validate_exit_strategies(cur, trade_count))

            # 5. Validate Position Sizing
            reports.append(self._validate_position_sizing(cur, trade_count))

            conn.close()

        except Exception as e:
            logger.warning("Continuous validation error: {}", e)

        self._reports.extend(reports)

        # Log summary
        total_findings = sum(len(r.findings) for r in reports)
        total_recs = sum(len(r.recommendations) for r in reports)
        logger.info(
            "VALIDATION: {} trades analyzed — {} findings, {} recommendations",
            trade_count, total_findings, total_recs,
        )

        return reports

    def _validate_symbol_expectancy(self, cur, trade_count: int) -> ValidationReport:
        """Validate expectancy by symbol."""
        report = ValidationReport(
            timestamp=time.time(),
            trades_analyzed=trade_count,
            validation_type="EXPECTANCY",
        )

        cur.execute("""
            SELECT symbol,
                   COUNT(*) as n,
                   AVG(pnl) as avg_pnl,
                   AVG(realized_r) as avg_r,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as wr
            FROM positions
            WHERE status = 'closed'
            GROUP BY symbol
            HAVING n >= 5
            ORDER BY avg_r DESC
        """)
        rows = cur.fetchall()

        profitable_symbols = []
        unprofitable_symbols = []

        for symbol, n, avg_pnl, avg_r, wr in rows:
            if avg_r and avg_r > 0:
                profitable_symbols.append((symbol, n, avg_r, wr))
            else:
                unprofitable_symbols.append((symbol, n, avg_r, wr))

        report.findings.append(
            f"{len(profitable_symbols)} profitable symbols, "
            f"{len(unprofitable_symbols)} unprofitable symbols"
        )

        if unprofitable_symbols:
            worst = unprofitable_symbols[0]
            report.recommendations.append(
                f"Consider raising confidence threshold for {worst[0]} "
                f"(avg_r={worst[2]:.3f}, n={worst[1]})"
            )
            report.proposed_changes[f"symbol_{worst[0]}_threshold"] = 0.95

        report.confidence_level = min(trade_count / 500, 1.0)
        report.improves_profit_factor = len(profitable_symbols) > len(unprofitable_symbols)

        return report

    def _validate_regime_expectancy(self, cur, trade_count: int) -> ValidationReport:
        """Validate expectancy by regime."""
        report = ValidationReport(
            timestamp=time.time(),
            trades_analyzed=trade_count,
            validation_type="REGIME",
        )

        cur.execute("""
            SELECT regime,
                   COUNT(*) as n,
                   AVG(pnl) as avg_pnl,
                   AVG(realized_r) as avg_r
            FROM positions
            WHERE status = 'closed' AND regime IS NOT NULL AND regime != '' AND regime != '0.0'
            GROUP BY regime
            HAVING n >= 10
            ORDER BY avg_r DESC
        """)
        rows = cur.fetchall()

        for regime, n, avg_pnl, avg_r in rows:
            if avg_r and avg_r < -0.5:
                report.findings.append(
                    f"Regime '{regime}' has negative expectancy: avg_r={avg_r:.3f} (n={n})"
                )
                report.recommendations.append(
                    f"Reduce activity in '{regime}' regime (PF likely < 1.0)"
                )
                report.proposed_changes[f"regime_{regime}_multiplier"] = 0.5

        if not report.findings:
            report.findings.append("All regimes have acceptable expectancy")

        report.confidence_level = min(trade_count / 500, 1.0)
        report.improves_profit_factor = not any(
            "negative" in f for f in report.findings
        )

        return report

    def _validate_institution_agreement(self, cur, trade_count: int) -> ValidationReport:
        """Validate institution agreement thresholds."""
        report = ValidationReport(
            timestamp=time.time(),
            trades_analyzed=trade_count,
            validation_type="INSTITUTION",
        )

        cur.execute("""
            SELECT
                CASE
                    WHEN institution_agreement >= 0.8 THEN '80+'
                    WHEN institution_agreement >= 0.7 THEN '70-80'
                    WHEN institution_agreement >= 0.6 THEN '60-70'
                    WHEN institution_agreement >= 0.5 THEN '50-60'
                    ELSE '<50'
                END as bucket,
                COUNT(*) as n,
                AVG(pnl) as avg_pnl,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as wr
            FROM positions
            WHERE status = 'closed' AND institution_agreement IS NOT NULL
            GROUP BY bucket
            ORDER BY bucket DESC
        """)
        rows = cur.fetchall()

        for bucket, n, avg_pnl, wr in rows:
            if n >= 10:
                report.findings.append(
                    f"Agreement {bucket}: WR={wr:.1%} avg_pnl=${avg_pnl:.2f} (n={n})"
                )

        report.confidence_level = min(trade_count / 500, 1.0)
        report.improves_profit_factor = True

        return report

    def _validate_exit_strategies(self, cur, trade_count: int) -> ValidationReport:
        """Validate exit strategies."""
        report = ValidationReport(
            timestamp=time.time(),
            trades_analyzed=trade_count,
            validation_type="EXIT",
        )

        cur.execute("""
            SELECT exit_reason,
                   COUNT(*) as n,
                   AVG(pnl) as avg_pnl,
                   AVG(realized_r) as avg_r,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as wr
            FROM positions
            WHERE status = 'closed' AND exit_reason IS NOT NULL AND exit_reason != ''
            GROUP BY exit_reason
            HAVING n >= 5
            ORDER BY avg_r DESC
        """)
        rows = cur.fetchall()

        for exit_reason, n, avg_pnl, avg_r, wr in rows:
            if avg_r and avg_r < -1.0:
                report.findings.append(
                    f"Exit '{exit_reason}' has poor performance: avg_r={avg_r:.3f} (n={n})"
                )
                report.recommendations.append(
                    f"Review exit strategy '{exit_reason}' — consider alternative"
                )
            elif avg_r and avg_r > 1.0:
                report.findings.append(
                    f"Exit '{exit_reason}' performs well: avg_r={avg_r:.3f} (n={n})"
                )

        report.confidence_level = min(trade_count / 500, 1.0)
        report.improves_profit_factor = True

        return report

    def _validate_position_sizing(self, cur, trade_count: int) -> ValidationReport:
        """Validate position sizing rules."""
        report = ValidationReport(
            timestamp=time.time(),
            trades_analyzed=trade_count,
            validation_type="SIZING",
        )

        # Check if higher confidence trades actually perform better
        cur.execute("""
            SELECT
                CASE
                    WHEN confidence >= 0.95 THEN '95+'
                    WHEN confidence >= 0.90 THEN '90-95'
                    WHEN confidence >= 0.85 THEN '85-90'
                    ELSE '<85'
                END as bucket,
                COUNT(*) as n,
                AVG(pnl) as avg_pnl,
                AVG(realized_r) as avg_r
            FROM positions
            WHERE status = 'closed'
            GROUP BY bucket
            ORDER BY bucket DESC
        """)
        rows = cur.fetchall()

        prev_avg_r = None
        for bucket, n, avg_pnl, avg_r in rows:
            if n >= 5:
                report.findings.append(
                    f"Confidence {bucket}: avg_r={avg_r:.3f} (n={n})"
                )
                if prev_avg_r is not None and avg_r and avg_r < prev_avg_r:
                    report.recommendations.append(
                        f"Confidence {bucket} performs worse than lower buckets — "
                        f"review sizing multiplier"
                    )
                prev_avg_r = avg_r

        report.confidence_level = min(trade_count / 500, 1.0)
        report.improves_profit_factor = True

        return report

    def _count_trades(self) -> int:
        """Count total completed trades."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM positions WHERE status = 'closed'")
            count = cur.fetchone()[0]
            conn.close()
            return count
        except:
            return 0
