"""
Forward Test Validator — determines production readiness.
Only validates when sufficient live data exists.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Dict, Optional
from loguru import logger


class ForwardTestValidator:
    """Validates whether the system is ready for production."""

    MIN_CLOSED_TRADES = 100
    MIN_PROFIT_FACTOR = 1.20
    MIN_EXPECTANCY = 0.0
    MAX_DRAWDOWN_PCT = 12.0
    MIN_WIN_RATE = 40.0
    REQUIRED_EXIT_REASONS = {
        "take_profit",
        "stop_loss",
        "time_exit",
        "trailing_stop",
        "duplicate_cleanup",
    }

    def __init__(self, report_path: Optional[Path] = None) -> None:
        self._report_path = report_path or Path(__file__).parent.parent / "data" / "production_audit.json"
        self._last_check: float = 0
        self._check_interval = 300

    def validate(self, trades: list, pnl_history: list = None) -> Dict:
        """Run full production-readiness validation with fail-closed data checks."""
        now = time.time()
        if now - self._last_check < self._check_interval:
            return self._last_result if hasattr(self, "_last_result") else {"ready": False, "reason": "not_checked"}

        self._last_check = now

        if not trades:
            result = {
                "ready": False,
                "reason": "no_trades",
                "closed_trades": 0,
                "required": self.MIN_CLOSED_TRADES,
                "production_ready": "NO",
                "timestamp": time.time(),
            }
            self._last_result = result
            self._save_report(result)
            return result

        non_finite_pnl = 0
        missing_pnl = 0
        unknown_exit_reasons = 0
        known_exit_reasons = 0
        normalized_trades = []

        for trade in trades:
            pnl = trade.get("pnl")
            if pnl is None:
                missing_pnl += 1
            elif not isinstance(pnl, (int, float)) or not math.isfinite(float(pnl)):
                non_finite_pnl += 1
            else:
                normalized_trades.append(float(pnl))

            reason = str(trade.get("exit_reason") or "").strip().lower()
            if reason in self.REQUIRED_EXIT_REASONS:
                known_exit_reasons += 1
            else:
                unknown_exit_reasons += 1

        pnls = normalized_trades
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total = len(pnls)
        wr = len(wins) / max(total, 1) * 100
        gross_loss = abs(sum(losses))
        pf = sum(wins) / gross_loss if gross_loss > 0 else float("inf") if wins else 0.0
        avg_win = sum(wins) / max(len(wins), 1)
        avg_loss = abs(sum(losses) / max(len(losses), 1))
        expectancy = (wr / 100 * avg_win) - ((1 - wr / 100) * avg_loss)

        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)

        initial_capital = 10000.0
        dd_pct = (max_dd / initial_capital * 100) if initial_capital > 0 else float("inf")

        data_quality_pass = missing_pnl == 0 and non_finite_pnl == 0 and total == len(trades)
        outcome_attribution_pass = unknown_exit_reasons == 0 and known_exit_reasons == len(trades)

        checks = {
            "data_quality": {
                "pass": data_quality_pass,
                "missing_pnl": missing_pnl,
                "non_finite_pnl": non_finite_pnl,
                "total_records": len(trades),
                "usable_pnl_records": total,
            },
            "outcome_attribution": {
                "pass": outcome_attribution_pass,
                "unknown_exit_reasons": unknown_exit_reasons,
                "known_exit_reasons": known_exit_reasons,
                "required_exit_reasons": sorted(self.REQUIRED_EXIT_REASONS),
            },
            "closed_trades": {
                "value": total,
                "required": self.MIN_CLOSED_TRADES,
                "pass": total >= self.MIN_CLOSED_TRADES,
            },
            "profit_factor": {
                "value": round(pf, 2) if math.isfinite(pf) else "inf",
                "required": self.MIN_PROFIT_FACTOR,
                "pass": math.isfinite(pf) and pf >= self.MIN_PROFIT_FACTOR,
            },
            "expectancy": {
                "value": round(expectancy, 2),
                "required": self.MIN_EXPECTANCY,
                "pass": math.isfinite(expectancy) and expectancy > self.MIN_EXPECTANCY,
            },
            "max_drawdown_pct": {
                "value": round(dd_pct, 1),
                "required": self.MAX_DRAWDOWN_PCT,
                "pass": math.isfinite(dd_pct) and dd_pct < self.MAX_DRAWDOWN_PCT,
            },
            "win_rate": {
                "value": round(wr, 1),
                "required": self.MIN_WIN_RATE,
                "pass": math.isfinite(wr) and wr >= self.MIN_WIN_RATE,
            },
        }

        all_pass = all(check["pass"] for check in checks.values())

        result = {
            "ready": all_pass,
            "production_ready": "YES" if all_pass else "NO",
            "checks": checks,
            "summary": {
                "closed_trades": total,
                "win_rate": round(wr, 1),
                "profit_factor": round(pf, 2) if math.isfinite(pf) else "inf",
                "expectancy": round(expectancy, 2),
                "max_drawdown_pct": round(dd_pct, 1),
                "total_pnl": round(sum(pnls), 2),
            },
            "timestamp": time.time(),
        }

        self._last_result = result
        self._save_report(result)

        if all_pass:
            logger.info("PRODUCTION READY: PF={:.2f} WR={:.1f}% Exp=${:.2f} DD={:.1f}%", pf, wr, expectancy, dd_pct)
        else:
            failed = [k for k, value in checks.items() if not value["pass"]]
            logger.warning("NOT PRODUCTION READY: failed={}", failed)

        return result

    def _save_report(self, result: Dict) -> None:
        """Save validation report to JSON."""
        try:
            self._report_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._report_path, "w") as f:
                json.dump(result, f, indent=2, default=str)
        except Exception as e:
            logger.error("Failed to save production audit: {}", e)


forward_test_validator = ForwardTestValidator()
