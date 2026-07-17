"""
Smooth Risk Scaler — Continuous risk adjustment instead of hard thresholds.

Per Executive Assessment v11:
    "Avoid abrupt transitions.
     Instead of:
         PF 0.86 → 100%
         PF 0.84 → 0%
     use a smooth function.
         Risk % = continuous function of Rolling PF
     This avoids oscillation around thresholds."

Key Innovation:
    v15 used: Hard thresholds (step function)
    v16 uses: Smooth sigmoid function (continuous)

    This prevents:
        - Oscillation around threshold boundaries
        - Abrupt position size changes
        - Unnecessary trading frequency changes

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


# ═══════════════════════════════════════════════════════════════
# SMOOTH RISK CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Sigmoid function parameters
# Risk(PF) = 100 / (1 + exp(-k × (PF - midpoint)))
# This produces a smooth S-curve from 0% to 100%

SIGMOID_MIDPOINT = 1.0    # PF value where risk = 50%
SIGMOID_K = 8.0           # Steepness (higher = sharper transition)
SIGMOID_MIN = 0.0         # Minimum risk %
SIGMOID_MAX = 100.0       # Maximum risk %

# Rolling window for PF calculation
ROLLING_WINDOW = 50

# Minimum trades for reliable PF calculation
MIN_TRADES_FOR_PF = 20

# Pause threshold (below this PF, risk = 0 regardless of sigmoid)
PAUSE_PF_THRESHOLD = 0.75


@dataclass
class SmoothRiskState:
    """Current smooth risk scaling state."""
    rolling_pf: float = 0.0
    rolling_ev_r: float = 0.0
    rolling_win_rate: float = 0.0
    trade_count: int = 0

    # Risk state
    risk_pct: float = 100.0       # Current risk as % (smooth)
    risk_multiplier: float = 1.0  # Risk multiplier (0-1)
    risk_label: str = ""          # Human-readable label

    # Degradation tracking
    consecutive_low_pf: int = 0
    degradation_detected: bool = False

    # Recovery
    consecutive_high_pf: int = 0
    recovery_detected: bool = False

    def to_dict(self) -> Dict:
        return {
            "rolling_pf": round(self.rolling_pf, 3),
            "rolling_ev_r": round(self.rolling_ev_r, 3),
            "rolling_win_rate": round(self.rolling_win_rate, 3),
            "trade_count": self.trade_count,
            "risk_pct": round(self.risk_pct, 1),
            "risk_multiplier": round(self.risk_multiplier, 3),
            "risk_label": self.risk_label,
            "consecutive_low_pf": self.consecutive_low_pf,
            "degradation_detected": self.degradation_detected,
            "consecutive_high_pf": self.consecutive_high_pf,
            "recovery_detected": self.recovery_detected,
        }


@dataclass
class SmoothRiskResult:
    """Result from smooth risk scaling."""
    timestamp: float = 0.0
    state: SmoothRiskState = field(default_factory=SmoothRiskState)
    action: str = ""              # MAINTAIN / REDUCE / PAUSE / RECOVER
    reason: str = ""
    previous_risk_pct: float = 100.0
    new_risk_pct: float = 100.0

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "state": self.state.to_dict(),
            "action": self.action,
            "reason": self.reason,
            "previous_risk_pct": round(self.previous_risk_pct, 1),
            "new_risk_pct": round(self.new_risk_pct, 1),
        }


class SmoothRiskScaler:
    """
    Continuous risk adjustment using sigmoid function.

    Per Executive Assessment v11:
        "Use a smooth function. This avoids oscillation
         around thresholds."

    This engine:
        1. Calculates rolling PF
        2. Applies sigmoid function for smooth risk scaling
        3. Avoids hard thresholds
        4. Prevents oscillation
        5. Provides continuous risk multiplier

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0
        self._state = SmoothRiskState()
        self._last_risk_pct = 100.0

    def _ensure_loaded(self) -> None:
        """Load trades from DB if stale."""
        if time.time() - self._last_load < 300:
            return
        self._load_trades()

    def _load_trades(self) -> None:
        """Load all closed trades."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol, side, realized_r, pnl, closed_at
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load smooth risk scaler: {}", e)

    def evaluate(self) -> SmoothRiskResult:
        """
        Evaluate current risk scaling using smooth sigmoid.

        Returns:
            SmoothRiskResult with smooth risk adjustment
        """
        self._ensure_loaded()

        result = SmoothRiskResult(timestamp=time.time())
        result.previous_risk_pct = self._last_risk_pct

        if not self._trades or len(self._trades) < MIN_TRADES_FOR_PF:
            result.state.risk_pct = 100.0
            result.state.risk_multiplier = 1.0
            result.state.risk_label = "FULL (insufficient data)"
            result.action = "MAINTAIN"
            result.reason = "Insufficient data for risk scaling"
            result.new_risk_pct = 100.0
            return result

        # ── Calculate rolling PF ──
        rolling = self._trades[:ROLLING_WINDOW]
        wins = [t.get("realized_r", 0) or 0 for t in rolling if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in rolling if (t.get("realized_r", 0) or 0) < 0]

        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        self._state.rolling_pf = gross_profit / max(0.01, gross_loss)

        all_r = [t.get("realized_r", 0) or 0 for t in rolling]
        self._state.rolling_ev_r = sum(all_r) / max(1, len(all_r))
        self._state.rolling_win_rate = len(wins) / max(1, len(rolling))
        self._state.trade_count = len(rolling)

        # ── Apply smooth sigmoid ──
        previous_risk = self._state.risk_pct

        if self._state.rolling_pf < PAUSE_PF_THRESHOLD:
            # Below pause threshold — force to 0
            self._state.risk_pct = 0.0
            self._state.risk_label = "PAUSED"
        else:
            # Smooth sigmoid: Risk = 100 / (1 + exp(-k × (PF - midpoint)))
            risk_pct = 100.0 / (1.0 + math.exp(-SIGMOID_K * (self._state.rolling_pf - SIGMOID_MIDPOINT)))
            risk_pct = max(SIGMOID_MIN, min(SIGMOID_MAX, risk_pct))
            self._state.risk_pct = risk_pct

            # Label
            if risk_pct >= 90:
                self._state.risk_label = "FULL"
            elif risk_pct >= 70:
                self._state.risk_label = "HIGH"
            elif risk_pct >= 50:
                self._state.risk_label = "MODERATE"
            elif risk_pct >= 30:
                self._state.risk_label = "LOW"
            else:
                self._state.risk_label = "MINIMAL"

        self._state.risk_multiplier = self._state.risk_pct / 100.0

        # ── Degradation tracking ──
        if self._state.rolling_pf < 1.0:
            self._state.consecutive_low_pf += 1
            self._state.consecutive_high_pf = 0
        else:
            self._state.consecutive_low_pf = 0
            self._state.consecutive_high_pf += 1

        self._state.degradation_detected = self._state.consecutive_low_pf >= 3
        self._state.recovery_detected = self._state.consecutive_high_pf >= 3

        # ── Determine action ──
        if self._state.rolling_pf < PAUSE_PF_THRESHOLD:
            result.action = "PAUSE"
            result.reason = f"PF={self._state.rolling_pf:.3f} < {PAUSE_PF_THRESHOLD} — pausing"
        elif self._state.degradation_detected:
            result.action = "REDUCE"
            result.reason = f"PF declining ({self._state.consecutive_low_pf} consecutive low windows)"
        elif self._state.recovery_detected and previous_risk < 50:
            result.action = "RECOVER"
            result.reason = f"PF recovering ({self._state.consecutive_high_pf} consecutive high windows)"
        else:
            result.action = "MAINTAIN"
            result.reason = f"PF={self._state.rolling_pf:.3f} — risk={self._state.risk_pct:.1f}%"

        result.new_risk_pct = self._state.risk_pct
        self._last_risk_pct = self._state.risk_pct

        return result

    def get_risk_multiplier(self) -> float:
        """Get current risk multiplier (0-1)."""
        self._ensure_loaded()
        return self._state.risk_multiplier

    def is_paused(self) -> bool:
        """Check if trading is paused."""
        return self._state.risk_pct < 1.0

    def get_state(self) -> SmoothRiskState:
        """Get current risk scaling state."""
        return self._state

    def get_risk_curve(self, pf_range: tuple = (0.5, 1.5)) -> List[Dict]:
        """Get the sigmoid risk curve for visualization."""
        points = []
        pf_min, pf_max = pf_range
        for i in range(21):
            pf = pf_min + (pf_max - pf_min) * i / 20
            risk = 100.0 / (1.0 + math.exp(-SIGMOID_K * (pf - SIGMOID_MIDPOINT)))
            points.append({"pf": round(pf, 3), "risk_pct": round(risk, 1)})
        return points

    def get_summary(self) -> Dict[str, Any]:
        """Get risk scaling summary."""
        self._ensure_loaded()
        return {
            "rolling_pf": round(self._state.rolling_pf, 3),
            "risk_pct": round(self._state.risk_pct, 1),
            "risk_label": self._state.risk_label,
            "degradation": self._state.degradation_detected,
            "recovery": self._state.recovery_detected,
        }
