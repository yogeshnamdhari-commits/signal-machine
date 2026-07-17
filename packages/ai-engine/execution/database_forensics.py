"""
Database Forensics — integrity audit and auto-repair.
Checks: duplicates, orphans, missing exits, null values.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict, List
from loguru import logger


class DatabaseForensics:
    """Runs integrity checks on the trading database."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._report_path = Path(db_path).parent / "database_forensics.json"

    def run_audit(self) -> Dict:
        """Run full database integrity audit."""
        results = {
            "timestamp": time.time(),
            "checks": {},
            "repairs": [],
            "overall": "PASS",
        }

        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            cur = conn.cursor()

            # 1. Duplicate signals
            dup_sigs = cur.execute("""
                SELECT symbol, side, COUNT(*) as cnt
                FROM signals WHERE status='active'
                GROUP BY symbol, side, CAST(timestamp AS INTEGER)
                HAVING cnt > 1
            """).fetchall()
            results["checks"]["duplicate_signals"] = {
                "count": len(dup_sigs),
                "status": "PASS" if len(dup_sigs) == 0 else "FAIL",
            }

            # 2. Duplicate open positions
            dup_pos = cur.execute("""
                SELECT symbol, side, COUNT(*) as cnt
                FROM positions WHERE status='open'
                GROUP BY symbol, side HAVING cnt > 1
            """).fetchall()
            results["checks"]["duplicate_positions"] = {
                "count": len(dup_pos),
                "status": "PASS" if len(dup_pos) == 0 else "FAIL",
            }
            if dup_pos:
                for sym, side, cnt in dup_pos:
                    results["repairs"].append(f"Duplicate: {side} {sym} x{cnt}")

            # 3. Orphan positions (no signal_id)
            orphans = cur.execute("SELECT COUNT(*) FROM positions WHERE signal_id IS NULL").fetchone()[0]
            results["checks"]["orphan_positions"] = {
                "count": orphans,
                "status": "PASS" if orphans == 0 else "FAIL",
            }

            # 4. Missing exit_reason on closed trades
            no_exit = cur.execute("""
                SELECT COUNT(*) FROM positions_archive WHERE status='closed'
                AND (exit_reason IS NULL OR exit_reason = '' OR exit_reason = 'unknown')
            """).fetchone()[0]
            total_closed = cur.execute("SELECT COUNT(*) FROM positions_archive WHERE status='closed'").fetchone()[0]
            exit_pct = (total_closed - no_exit) / max(total_closed, 1) * 100
            results["checks"]["exit_reason_tracking"] = {
                "with_exit_reason": total_closed - no_exit,
                "without_exit_reason": no_exit,
                "coverage_pct": round(exit_pct, 1),
                "status": "PASS" if exit_pct > 99 else "FAIL",
            }

            # 5. Null PnL on closed trades
            null_pnl = cur.execute("SELECT COUNT(*) FROM positions_archive WHERE status='closed' AND pnl IS NULL").fetchone()[0]
            results["checks"]["null_pnl"] = {
                "count": null_pnl,
                "status": "PASS" if null_pnl == 0 else "FAIL",
            }

            # 6. Null SL on open positions
            no_sl = cur.execute("SELECT COUNT(*) FROM positions WHERE status='open' AND (stop_loss IS NULL OR stop_loss = 0)").fetchone()[0]
            results["checks"]["missing_sl"] = {
                "count": no_sl,
                "status": "PASS" if no_sl == 0 else "FAIL",
            }

            # 7. Null TP on open positions
            no_tp = cur.execute("SELECT COUNT(*) FROM positions WHERE status='open' AND (take_profit IS NULL OR take_profit = 0)").fetchone()[0]
            results["checks"]["missing_tp"] = {
                "count": no_tp,
                "status": "PASS" if no_tp == 0 else "FAIL",
            }

            # Auto-repair: remove duplicate open positions (keep newest)
            if dup_pos:
                for sym, side, cnt in dup_pos:
                    # Find IDs to delete (keep the newest)
                    ids = cur.execute(
                        "SELECT id FROM positions WHERE symbol=? AND side=? AND status='open' ORDER BY opened_at DESC",
                        (sym, side)
                    ).fetchall()
                    if len(ids) > 1:
                        delete_ids = [r[0] for r in ids[1:]]  # Keep first (newest), delete rest
                        placeholders = ",".join("?" * len(delete_ids))
                        cur.execute(f"DELETE FROM positions WHERE id IN ({placeholders})", delete_ids)
                        results["repairs"].append(f"Repaired: deleted {len(delete_ids)} duplicate {side} {sym}")

            conn.commit()
            conn.close()

            # Overall status
            failed = [k for k, v in results["checks"].items() if v["status"] == "FAIL"]
            results["overall"] = "PASS" if not failed else "FAIL"
            results["failed_checks"] = failed

        except Exception as e:
            results["overall"] = "ERROR"
            results["error"] = str(e)
            logger.error("Database forensics error: {}", e)

        # Save report
        self._save_report(results)
        return results

    def _save_report(self, results: Dict) -> None:
        try:
            self._report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._report_path, "w") as f:
                json.dump(results, f, indent=2, default=str)
        except Exception as e:
            logger.error("Failed to save forensics report: {}", e)
