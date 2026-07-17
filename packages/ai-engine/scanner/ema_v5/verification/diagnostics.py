"""
EMA_V5 Diagnostics — Per-signal diagnostic data collection.
Stores reasons passed, failed, missing conditions, and execution metadata.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


@dataclass
class DiagnosticCheck:
    """Single diagnostic check result."""
    name: str
    passed: bool
    value: Any = None
    threshold: Any = None
    reason: str = ""
    duration_ms: float = 0.0


@dataclass
class SignalDiagnostics:
    """Complete diagnostics for a single signal."""
    signal_uuid: str = ""
    symbol: str = ""
    timestamp: float = 0.0

    # Verdict
    verdict: str = "FAIL"  # PASS, WARNING, FAIL

    # Check results
    checks: List[DiagnosticCheck] = field(default_factory=list)

    # Aggregated
    reasons_passed: List[str] = field(default_factory=list)
    reasons_failed: List[str] = field(default_factory=list)
    missing_conditions: List[str] = field(default_factory=list)

    # Confidence breakdown
    confidence_score: float = 0.0
    confidence_breakdown: Dict[str, float] = field(default_factory=dict)

    # Metadata
    execution_time_ms: float = 0.0
    scanner_version: str = "ema_v5"
    strategy_version: str = "ema_v5"

    # Signal data (for replay)
    signal_data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "signal_uuid": self.signal_uuid,
            "symbol": self.symbol,
            "timestamp": self.timestamp,
            "verdict": self.verdict,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "value": c.value,
                    "threshold": c.threshold,
                    "reason": c.reason,
                    "duration_ms": c.duration_ms,
                }
                for c in self.checks
            ],
            "reasons_passed": self.reasons_passed,
            "reasons_failed": self.reasons_failed,
            "missing_conditions": self.missing_conditions,
            "confidence_score": self.confidence_score,
            "confidence_breakdown": self.confidence_breakdown,
            "execution_time_ms": self.execution_time_ms,
            "scanner_version": self.scanner_version,
            "strategy_version": self.strategy_version,
            "signal_data": self.signal_data,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SignalDiagnostics":
        """Load from dictionary."""
        checks = [
            DiagnosticCheck(
                name=c.get("name", ""),
                passed=c.get("passed", False),
                value=c.get("value"),
                threshold=c.get("threshold"),
                reason=c.get("reason", ""),
                duration_ms=c.get("duration_ms", 0),
            )
            for c in data.get("checks", [])
        ]
        return cls(
            signal_uuid=data.get("signal_uuid", ""),
            symbol=data.get("symbol", ""),
            timestamp=data.get("timestamp", 0),
            verdict=data.get("verdict", "FAIL"),
            checks=checks,
            reasons_passed=data.get("reasons_passed", []),
            reasons_failed=data.get("reasons_failed", []),
            missing_conditions=data.get("missing_conditions", []),
            confidence_score=data.get("confidence_score", 0),
            confidence_breakdown=data.get("confidence_breakdown", {}),
            execution_time_ms=data.get("execution_time_ms", 0),
            scanner_version=data.get("scanner_version", "ema_v5"),
            strategy_version=data.get("strategy_version", "ema_v5"),
            signal_data=data.get("signal_data", {}),
        )


class EMAv5Diagnostics:
    """Collects and manages diagnostics for EMA_V5 signals."""

    def __init__(self, max_diagnostics: int = 10000) -> None:
        self._diagnostics: List[SignalDiagnostics] = []
        self._max = max_diagnostics

    def create(self, signal_uuid: str = "", symbol: str = "") -> SignalDiagnostics:
        """Create a new diagnostics instance."""
        diag = SignalDiagnostics(
            signal_uuid=signal_uuid,
            symbol=symbol,
            timestamp=time.time(),
        )
        return diag

    def record(self, diag: SignalDiagnostics) -> None:
        """Record a completed diagnostics instance."""
        self._diagnostics.append(diag)
        # Trim if over limit
        if len(self._diagnostics) > self._max:
            self._diagnostics = self._diagnostics[-self._max:]

    def get_recent(self, n: int = 100) -> List[SignalDiagnostics]:
        """Get last N diagnostics."""
        return self._diagnostics[-n:]

    def get_by_symbol(self, symbol: str) -> List[SignalDiagnostics]:
        """Get diagnostics for a specific symbol."""
        return [d for d in self._diagnostics if d.symbol == symbol]

    def get_by_verdict(self, verdict: str) -> List[SignalDiagnostics]:
        """Get diagnostics by verdict (PASS, WARNING, FAIL)."""
        return [d for d in self._diagnostics if d.verdict == verdict]

    def get_count(self) -> int:
        """Total diagnostic count."""
        return len(self._diagnostics)

    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all diagnostics."""
        total = len(self._diagnostics)
        if total == 0:
            return {"total": 0, "pass": 0, "warning": 0, "fail": 0}

        pass_count = sum(1 for d in self._diagnostics if d.verdict == "PASS")
        warning_count = sum(1 for d in self._diagnostics if d.verdict == "WARNING")
        fail_count = sum(1 for d in self._diagnostics if d.verdict == "FAIL")

        avg_exec_time = sum(d.execution_time_ms for d in self._diagnostics) / total
        avg_confidence = sum(d.confidence_score for d in self._diagnostics) / total

        return {
            "total": total,
            "pass": pass_count,
            "warning": warning_count,
            "fail": fail_count,
            "pass_rate": round(pass_count / total * 100, 1) if total > 0 else 0,
            "avg_execution_time_ms": round(avg_exec_time, 2),
            "avg_confidence": round(avg_confidence, 1),
        }

    def clear(self) -> None:
        """Clear all diagnostics."""
        self._diagnostics.clear()
