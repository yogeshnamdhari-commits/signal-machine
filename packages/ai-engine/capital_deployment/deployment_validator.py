"""
Deployment Validator — Final Deployment Checklist
==================================================
PASS ONLY IF:
  PF > 1.3, WR > 48%, DD < 10%, Risk Breach = 0,
  Position Mismatch = 0, Recovery Success = 100%, Uptime > 99%
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "capital"
REPORTS_DIR = Path(__file__).parent.parent / "data" / "reports"


# ─── Data Classes ────────────────────────────────────────────────────────────
@dataclass
class CheckItem:
    """Individual deployment check."""
    name: str
    description: str
    threshold: str
    actual_value: str
    passed: bool
    severity: str  # REQUIRED, RECOMMENDED, OPTIONAL
    details: str = ""


@dataclass
class DeploymentChecklist:
    """Complete deployment checklist."""
    timestamp: str
    overall_pass: bool
    overall_score: float  # 0-100
    checks: list
    passed_count: int
    failed_count: int
    required_passed: int
    required_total: int
    recommendation: str
    blockers: list
    warnings: list


# ─── Deployment Validator ────────────────────────────────────────────────────
class DeploymentValidator:
    """
    Final deployment readiness validator.

    Usage:
        validator = DeploymentValidator()
        checklist = validator.validate(
            profit_factor=1.7,
            win_rate=0.55,
            max_drawdown=0.08,
            risk_breaches=0,
            position_mismatches=0,
            recovery_success_rate=1.0,
            uptime=0.995,
        )
    """

    def __init__(self):
        self._history: list[dict] = []
        self._load_state()
        logger.info("DeploymentValidator initialized")

    # ── Main Validation ───────────────────────────────────────────────────────
    def validate(
        self,
        profit_factor: float = 0.0,
        win_rate: float = 0.0,
        max_drawdown: float = 1.0,
        risk_breaches: int = 999,
        position_mismatches: int = 999,
        recovery_success_rate: float = 0.0,
        uptime: float = 0.0,
        sharpe_ratio: float = 0.0,
        slippage_bps: float = 0.0,
        consecutive_losses: int = 0,
        trades_completed: int = 0,
        avg_execution_latency_ms: float = 0.0,
        margin_usage_pct: float = 0.0,
        kill_switch_activations: int = 0,
        days_running: int = 0,
    ) -> DeploymentChecklist:
        """Run complete deployment validation."""
        checks = []
        blockers = []
        warnings = []

        # ── Required Checks ───────────────────────────────────────────────────

        # 1. Profit Factor
        pf_pass = profit_factor > 1.3
        checks.append(CheckItem(
            name="Profit Factor",
            description="Strategy must be profitable after fees",
            threshold="> 1.3",
            actual_value=f"{profit_factor:.2f}",
            passed=pf_pass,
            severity="REQUIRED",
        ))
        if not pf_pass:
            blockers.append(f"PF {profit_factor:.2f} < 1.3")

        # 2. Win Rate
        wr_pass = win_rate > 0.48
        checks.append(CheckItem(
            name="Win Rate",
            description="Minimum acceptable win rate",
            threshold="> 48%",
            actual_value=f"{win_rate:.1%}",
            passed=wr_pass,
            severity="REQUIRED",
        ))
        if not wr_pass:
            blockers.append(f"WR {win_rate:.1%} < 48%")

        # 3. Max Drawdown
        dd_pass = max_drawdown < 0.10
        checks.append(CheckItem(
            name="Max Drawdown",
            description="Maximum acceptable drawdown",
            threshold="< 10%",
            actual_value=f"{max_drawdown:.1%}",
            passed=dd_pass,
            severity="REQUIRED",
        ))
        if not dd_pass:
            blockers.append(f"DD {max_drawdown:.1%} >= 10%")

        # 4. Risk Breaches
        rb_pass = risk_breaches == 0
        checks.append(CheckItem(
            name="Risk Breaches",
            description="Zero tolerance for risk limit breaches",
            threshold="= 0",
            actual_value=str(risk_breaches),
            passed=rb_pass,
            severity="REQUIRED",
        ))
        if not rb_pass:
            blockers.append(f"{risk_breaches} risk breaches")

        # 5. Position Mismatches
        pm_pass = position_mismatches == 0
        checks.append(CheckItem(
            name="Position Mismatches",
            description="Internal state must match exchange",
            threshold="= 0",
            actual_value=str(position_mismatches),
            passed=pm_pass,
            severity="REQUIRED",
        ))
        if not pm_pass:
            blockers.append(f"{position_mismatches} position mismatches")

        # 6. Recovery Success
        rs_pass = recovery_success_rate >= 1.0
        checks.append(CheckItem(
            name="Recovery Success Rate",
            description="100% recovery from crashes/restarts",
            threshold="= 100%",
            actual_value=f"{recovery_success_rate:.0%}",
            passed=rs_pass,
            severity="REQUIRED",
        ))
        if not rs_pass:
            blockers.append(f"Recovery rate {recovery_success_rate:.0%} < 100%")

        # 7. Uptime
        up_pass = uptime > 0.99
        checks.append(CheckItem(
            name="System Uptime",
            description="Minimum operational uptime",
            threshold="> 99%",
            actual_value=f"{uptime:.1%}",
            passed=up_pass,
            severity="REQUIRED",
        ))
        if not up_pass:
            blockers.append(f"Uptime {uptime:.1%} < 99%")

        # ── Recommended Checks ────────────────────────────────────────────────

        # 8. Sharpe Ratio
        sharpe_pass = sharpe_ratio > 1.0
        checks.append(CheckItem(
            name="Sharpe Ratio",
            description="Risk-adjusted return metric",
            threshold="> 1.0",
            actual_value=f"{sharpe_ratio:.2f}",
            passed=sharpe_pass,
            severity="RECOMMENDED",
        ))
        if not sharpe_pass and sharpe_ratio > 0:
            warnings.append(f"Sharpe {sharpe_ratio:.2f} below 1.0")

        # 9. Slippage
        slip_pass = slippage_bps < 20
        checks.append(CheckItem(
            name="Average Slippage",
            description="Execution quality indicator",
            threshold="< 20 bps",
            actual_value=f"{slippage_bps:.1f} bps",
            passed=slip_pass,
            severity="RECOMMENDED",
        ))
        if not slip_pass:
            warnings.append(f"Slippage {slippage_bps:.1f} bps > 20")

        # 10. Execution Latency
        lat_pass = avg_execution_latency_ms < 500
        checks.append(CheckItem(
            name="Execution Latency",
            description="Order execution speed",
            threshold="< 500ms",
            actual_value=f"{avg_execution_latency_ms:.0f}ms",
            passed=lat_pass,
            severity="RECOMMENDED",
        ))

        # 11. Kill Switch Activations
        ks_pass = kill_switch_activations == 0
        checks.append(CheckItem(
            name="Kill Switch Activations",
            description="Emergency halt events",
            threshold="= 0",
            actual_value=str(kill_switch_activations),
            passed=ks_pass,
            severity="RECOMMENDED",
        ))
        if not ks_pass:
            warnings.append(f"{kill_switch_activations} kill switch activations")

        # 12. Minimum Trades
        trades_pass = trades_completed >= 30
        checks.append(CheckItem(
            name="Minimum Trades",
            description="Statistical significance threshold",
            threshold=">= 30",
            actual_value=str(trades_completed),
            passed=trades_pass,
            severity="RECOMMENDED",
        ))

        # 13. Days Running
        days_pass = days_running >= 14
        checks.append(CheckItem(
            name="Days Running",
            description="Minimum observation period",
            threshold=">= 14 days",
            actual_value=f"{days_running} days",
            passed=days_pass,
            severity="RECOMMENDED",
        ))

        # ── Scoring ───────────────────────────────────────────────────────────
        passed = sum(1 for c in checks if c.passed)
        failed = sum(1 for c in checks if not c.passed)
        required_checks = [c for c in checks if c.severity == "REQUIRED"]
        required_passed = sum(1 for c in required_checks if c.passed)
        required_total = len(required_checks)

        # Weighted score
        required_weight = 0.7
        recommended_weight = 0.3

        req_score = (required_passed / required_total * 100) if required_total > 0 else 0
        rec_checks = [c for c in checks if c.severity == "RECOMMENDED"]
        rec_passed = sum(1 for c in rec_checks if c.passed)
        rec_score = (rec_passed / len(rec_checks) * 100) if rec_checks else 100

        overall_score = req_score * required_weight + rec_score * recommended_weight

        # Overall pass = all required pass
        overall_pass = required_passed == required_total

        # Recommendation
        if overall_pass and overall_score >= 85:
            recommendation = "✅ READY FOR LIVE TRADING"
        elif overall_pass:
            recommendation = "⚠️ READY FOR PAPER TRADING — Monitor closely"
        elif required_passed >= required_total - 1:
            recommendation = "🔶 NEAR READY — Fix remaining blocker(s)"
        else:
            recommendation = "❌ NOT READY — Multiple critical issues"

        checklist = DeploymentChecklist(
            timestamp=datetime.now(timezone.utc).isoformat(),
            overall_pass=overall_pass,
            overall_score=round(overall_score, 1),
            checks=[asdict(c) for c in checks],
            passed_count=passed,
            failed_count=failed,
            required_passed=required_passed,
            required_total=required_total,
            recommendation=recommendation,
            blockers=blockers,
            warnings=warnings,
        )

        self._history.append(asdict(checklist))
        self._save_state()
        self._save_report(checklist)

        return checklist

    # ── Quick Check ───────────────────────────────────────────────────────────
    def quick_check(
        self,
        pf: float, wr: float, dd: float,
        breaches: int, mismatches: int
    ) -> tuple[bool, str]:
        """Quick pass/fail check."""
        if pf <= 1.3:
            return False, f"PF {pf:.2f} < 1.3"
        if wr <= 0.48:
            return False, f"WR {wr:.1%} < 48%"
        if dd >= 0.10:
            return False, f"DD {dd:.1%} >= 10%"
        if breaches > 0:
            return False, f"{breaches} risk breaches"
        if mismatches > 0:
            return False, f"{mismatches} position mismatches"
        return True, "All checks passed"

    # ── Save Report ───────────────────────────────────────────────────────────
    def _save_report(self, checklist: DeploymentChecklist):
        """Save deployment checklist report."""
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filename = f"deployment_checklist_{date_str}.json"
        (REPORTS_DIR / filename).write_text(json.dumps(asdict(checklist), indent=2, default=str))
        logger.info("Saved deployment checklist: %s", filename)

    # ── State Persistence ─────────────────────────────────────────────────────
    def _save_state(self):
        """Save validator state."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "total_validations": len(self._history),
            "history": self._history[-50:],
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        (DATA_DIR / "deployment_validator_state.json").write_text(json.dumps(state, indent=2, default=str))

    def _load_state(self):
        """Load persisted state."""
        path = DATA_DIR / "deployment_validator_state.json"
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text())
            self._history = state.get("history", [])
        except Exception as e:
            logger.error("Failed to load deployment validator state: %s", e)

    def get_stats(self) -> dict:
        """Get validator statistics."""
        last = self._history[-1] if self._history else None
        return {
            "total_validations": len(self._history),
            "last_pass": last.get("overall_pass") if last else None,
            "last_score": last.get("overall_score") if last else None,
            "last_recommendation": last.get("recommendation") if last else None,
        }
