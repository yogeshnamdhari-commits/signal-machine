"""
Adaptive Risk Scaler — Auto-reduce exposure during degradation.

Per Executive Assessment v10:
    "Add adaptive risk scaling driven by rolling performance
     so the engine automatically becomes more conservative
     during periods of degradation.

     Example:
         Rolling PF    Risk
         >1.20         100%
         1.05–1.20     80%
         0.95–1.05     50%
         <0.95         25%
         <0.85         Pause new trades

     This is entirely within the execution layer and does not
     modify Smart Money or EMA V5."

Key Features:
    1. Rolling PF Monitoring — track PF across windows
    2. Risk Scaling — adjust position size based on performance
    3. Automatic Degradation Detection — reduce exposure early
    4. Recovery Detection — restore exposure when PF improves
    5. Emergency Pause — halt trading during severe degradation

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
# RISK SCALING CONFIGURATION
# ═══════════════════════════════════════════════════════════════

# Rolling window for PF calculation
ROLLING_WINDOW = 50

# Risk scaling tiers
RISK_TIERS = [
    {"min_pf": 1.20, "risk_pct": 100, "label": "FULL"},
    {"min_pf": 1.05, "risk_pct": 80, "label": "REDUCED"},
    {"min_pf": 0.95, "risk_pct": 50, "label": "CONSERVATIVE"},
    {"min_pf": 0.85, "risk_pct": 25, "label": "MINIMAL"},
    {"min_pf": 0.00, "risk_pct": 0, "label": "PAUSED"},
]

# Minimum trades for reliable PF calculation
MIN_TRADES_FOR_PF = 20

# Cooldown after pause (seconds)
PAUSE_COOLDOWN = 3600  # 1 hour


@dataclass
class RiskScalingState:
    """Current risk scaling state."""
    rolling_pf: float = 0.0
    rolling_ev_r: float = 0.0
    rolling_win_rate: float = 0.0
    trade_count: int = 0

    # Risk state
    risk_pct: float = 100.0       # Current risk as % of normal
    risk_label: str = "FULL"      # Current risk tier label
    risk_multiplier: float = 1.0  # Risk multiplier (0-1)

    # Degradation tracking
    consecutive_low_pf: int = 0   # How many windows with PF < 1.0
    degradation_detected: bool = False
    pause_until: float = 0.0      # Timestamp when pause ends

    # Recovery
    consecutive_high_pf: int = 0  # How many windows with PF > 1.1
    recovery_detected: bool = False

    def to_dict(self) -> Dict:
        return {
            "rolling_pf": round(self.rolling_pf, 3),
            "rolling_ev_r": round(self.rolling_ev_r, 3),
            "rolling_win_rate": round(self.rolling_win_rate, 3),
            "trade_count": self.trade_count,
            "risk_pct": round(self.risk_pct, 1),
            "risk_label": self.risk_label,
            "risk_multiplier": round(self.risk_multiplier, 3),
            "consecutive_low_pf": self.consecutive_low_pf,
            "degradation_detected": self.degradation_detected,
            "consecutive_high_pf": self.consecutive_high_pf,
            "recovery_detected": self.recovery_detected,
        }


@dataclass
class AdaptiveRiskResult:
    """Result from adaptive risk scaling."""
    timestamp: float = 0.0
    state: RiskScalingState = field(default_factory=RiskScalingState)
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


class AdaptiveRiskScaler:
    """
    Automatically adjusts risk based on rolling performance.

    Per Executive Assessment v10:
        "The engine should automatically become more conservative
         during periods of degradation."

    This engine:
        1. Monitors rolling PF across windows
        2. Scales risk based on performance tier
        3. Detects degradation early
        4. Pauses trading during severe degradation
        5. Recovers exposure when PF improves

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0
        self._state = RiskScalingState()
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
            logger.warning("Could not load adaptive risk scaler: {}", e)

    def evaluate(self) -> AdaptiveRiskResult:
        """
        Evaluate current risk scaling.

        Returns:
            AdaptiveRiskResult with risk adjustment
        """
        self._ensure_loaded()

        result = AdaptiveRiskResult(timestamp=time.time())
        result.previous_risk_pct = self._last_risk_pct

        if not self._trades or len(self._trades) < MIN_TRADES_FOR_PF:
            result.state.risk_pct = 100.0
            result.state.risk_label = "FULL"
            result.state.risk_multiplier = 1.0
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

        # ── Determine risk tier ──
        previous_risk = self._state.risk_pct
        previous_label = self._state.risk_label

        for tier in RISK_TIERS:
            if self._state.rolling_pf >= tier["min_pf"]:
                self._state.risk_pct = tier["risk_pct"]
                self._state.risk_label = tier["label"]
                self._state.risk_multiplier = tier["risk_pct"] / 100.0
                break

        # ── Degradation detection ──
        if self._state.rolling_pf < 1.0:
            self._state.consecutive_low_pf += 1
            self._state.consecutive_high_pf = 0
        else:
            self._state.consecutive_low_pf = 0
            self._state.consecutive_high_pf += 1

        self._state.degradation_detected = self._state.consecutive_low_pf >= 3
        self._state.recovery_detected = self._state.consecutive_high_pf >= 3

        # ── Pause check ──
        now = time.time()
        if self._state.rolling_pf < 0.85:
            self._state.pause_until = now + PAUSE_COOLDOWN
            result.action = "PAUSE"
            result.reason = f"PF={self._state.rolling_pf:.3f} < 0.85 — pausing for {PAUSE_COOLDOWN/3600:.0f}h"
        elif self._state.degradation_detected:
            result.action = "REDUCE"
            result.reason = f"PF declining ({self._state.consecutive_low_pf} consecutive low windows)"
        elif self._state.recovery_detected and previous_risk < 100:
            result.action = "RECOVER"
            result.reason = f"PF recovering ({self._state.consecutive_high_pf} consecutive high windows)"
        else:
            result.action = "MAINTAIN"
            result.reason = f"PF={self._state.rolling_pf:.3f} — {self._state.risk_label}"

        result.new_risk_pct = self._state.risk_pct
        self._last_risk_pct = self._state.risk_pct

        return result

    def get_risk_multiplier(self) -> float:
        """Get current risk multiplier."""
        self._ensure_loaded()
        return self._state.risk_multiplier

    def is_paused(self) -> bool:
        """Check if trading is paused."""
        return time.time() < self._state.pause_until

    def get_state(self) -> RiskScalingState:
        """Get current risk scaling state."""
        return self._state

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
