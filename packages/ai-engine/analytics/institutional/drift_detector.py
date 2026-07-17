"""
Strategy Drift Detector — Automatic Performance Degradation Detection
======================================================================
Phase 10: Monitors for falling win rate, confidence, expectancy, etc.

READ-ONLY — Never modifies trading logic.

Detects:
- Falling win rate
- Falling confidence
- Rising drawdown
- Lower expectancy
- Increasing RR rejection rate
- Increasing SL distance
- Symbol degradation
- Session degradation
- Market regime degradation

Raises warnings automatically when thresholds are breached.
"""
from __future__ import annotations

import math
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from loguru import logger


class AlertSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class DriftAlert:
    """A drift detection alert."""
    metric: str
    current_value: float
    baseline_value: float
    change_pct: float
    severity: AlertSeverity
    message: str
    timestamp: float
    details: Dict[str, Any]


class DriftDetector:
    """
    Monitors strategy performance for degradation.
    
    Compares recent performance (last N trades) against
    historical baseline to detect drift.
    
    READ-ONLY: Never modifies trading logic.
    """
    
    DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "institutional_v1.db"
    
    # Default thresholds for drift detection
    THRESHOLDS = {
        "win_rate_drop_pct": 10.0,      # Alert if WR drops >10%
        "confidence_drop_pct": 15.0,     # Alert if confidence drops >15%
        "drawdown_increase_pct": 50.0,   # Alert if DD increases >50%
        "expectancy_drop_pct": 30.0,     # Alert if expectancy drops >30%
        "rr_rejection_increase_pct": 20.0,  # Alert if RR rejections increase >20%
        "sl_distance_increase_pct": 25.0,   # Alert if SL distance increases >25%
        "min_trades_for_alert": 20,      # Minimum trades before alerting
        "lookback_window": 50,           # Number of recent trades to analyze
        "baseline_window": 200,          # Number of trades for baseline
    }
    
    def __init__(self, db_path: Optional[Path] = None, thresholds: Optional[Dict] = None):
        self._db_path = db_path or self.DB_PATH
        self._thresholds = {**self.THRESHOLDS, **(thresholds or {})}
        self._alerts: List[DriftAlert] = []
    
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _get_trades(self, limit: int = 200) -> List[Dict]:
        """Get recent closed trades."""
        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT * FROM positions 
                WHERE status = 'closed' 
                ORDER BY closed_at DESC 
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    
    def detect_drift(self) -> List[DriftAlert]:
        """Run all drift detection checks and return alerts."""
        self._alerts = []
        
        trades = self._get_trades(self._thresholds["baseline_window"])
        if len(trades) < self._thresholds["min_trades_for_alert"]:
            logger.info("DRIFT: Not enough trades ({}) for drift detection", len(trades))
            return self._alerts
        
        # Split into baseline and recent
        lookback = self._thresholds["lookback_window"]
        baseline = trades[lookback:]  # Older trades
        recent = trades[:lookback]    # Recent trades
        
        if len(baseline) < 10 or len(recent) < 10:
            return self._alerts
        
        # Run checks
        self._check_win_rate(baseline, recent)
        self._check_confidence(baseline, recent)
        self._check_drawdown(baseline, recent)
        self._check_expectancy(baseline, recent)
        self._check_rr_metrics(baseline, recent)
        self._check_sl_distance(baseline, recent)
        self._check_symbol_degradation(trades)
        self._check_session_degradation(trades)
        
        # Log summary
        if self._alerts:
            logger.warning("DRIFT: {} alerts generated", len(self._alerts))
            for alert in self._alerts:
                logger.warning("  {} {}: {}", alert.severity.value.upper(), alert.metric, alert.message)
        
        return self._alerts
    
    def _check_win_rate(self, baseline: List[Dict], recent: List[Dict]) -> None:
        """Check for declining win rate."""
        base_wr = self._calc_win_rate(baseline)
        recent_wr = self._calc_win_rate(recent)
        
        if base_wr > 0:
            change_pct = (recent_wr - base_wr) / base_wr * 100
            if change_pct < -self._thresholds["win_rate_drop_pct"]:
                severity = AlertSeverity.CRITICAL if change_pct < -20 else AlertSeverity.WARNING
                self._alerts.append(DriftAlert(
                    metric="win_rate",
                    current_value=recent_wr,
                    baseline_value=base_wr,
                    change_pct=round(change_pct, 1),
                    severity=severity,
                    message=f"Win rate dropped {abs(change_pct):.1f}%: {base_wr:.1f}% → {recent_wr:.1f}%",
                    timestamp=time.time(),
                    details={"baseline_trades": len(baseline), "recent_trades": len(recent)},
                ))
    
    def _check_confidence(self, baseline: List[Dict], recent: List[Dict]) -> None:
        """Check for declining confidence."""
        base_conf = self._avg_confidence(baseline)
        recent_conf = self._avg_confidence(recent)
        
        if base_conf > 0:
            change_pct = (recent_conf - base_conf) / base_conf * 100
            if change_pct < -self._thresholds["confidence_drop_pct"]:
                self._alerts.append(DriftAlert(
                    metric="confidence",
                    current_value=recent_conf,
                    baseline_value=base_conf,
                    change_pct=round(change_pct, 1),
                    severity=AlertSeverity.WARNING,
                    message=f"Average confidence dropped {abs(change_pct):.1f}%",
                    timestamp=time.time(),
                    details={},
                ))
    
    def _check_drawdown(self, baseline: List[Dict], recent: List[Dict]) -> None:
        """Check for increasing drawdown."""
        base_dd = self._calc_max_drawdown(baseline)
        recent_dd = self._calc_max_drawdown(recent)
        
        if base_dd > 0:
            change_pct = (recent_dd - base_dd) / base_dd * 100
            if change_pct > self._thresholds["drawdown_increase_pct"]:
                self._alerts.append(DriftAlert(
                    metric="drawdown",
                    current_value=recent_dd,
                    baseline_value=base_dd,
                    change_pct=round(change_pct, 1),
                    severity=AlertSeverity.CRITICAL,
                    message=f"Max drawdown increased {change_pct:.1f}%",
                    timestamp=time.time(),
                    details={},
                ))
    
    def _check_expectancy(self, baseline: List[Dict], recent: List[Dict]) -> None:
        """Check for declining expectancy."""
        base_exp = self._calc_expectancy(baseline)
        recent_exp = self._calc_expectancy(recent)
        
        if base_exp > 0:
            change_pct = (recent_exp - base_exp) / base_exp * 100
            if change_pct < -self._thresholds["expectancy_drop_pct"]:
                self._alerts.append(DriftAlert(
                    metric="expectancy",
                    current_value=recent_exp,
                    baseline_value=base_exp,
                    change_pct=round(change_pct, 1),
                    severity=AlertSeverity.WARNING,
                    message=f"Expectancy dropped {abs(change_pct):.1f}%",
                    timestamp=time.time(),
                    details={},
                ))
    
    def _check_rr_metrics(self, baseline: List[Dict], recent: List[Dict]) -> None:
        """Check for changing RR metrics."""
        base_rr = self._avg_rr(baseline)
        recent_rr = self._avg_rr(recent)
        
        if base_rr > 0:
            change_pct = (recent_rr - base_rr) / base_rr * 100
            if change_pct < -15:  # RR dropped significantly
                self._alerts.append(DriftAlert(
                    metric="risk_reward",
                    current_value=recent_rr,
                    baseline_value=base_rr,
                    change_pct=round(change_pct, 1),
                    severity=AlertSeverity.WARNING,
                    message=f"Average RR dropped {abs(change_pct):.1f}%",
                    timestamp=time.time(),
                    details={},
                ))
    
    def _check_sl_distance(self, baseline: List[Dict], recent: List[Dict]) -> None:
        """Check for increasing SL distance."""
        base_sl = self._avg_sl_distance(baseline)
        recent_sl = self._avg_sl_distance(recent)
        
        if base_sl > 0:
            change_pct = (recent_sl - base_sl) / base_sl * 100
            if change_pct > self._thresholds["sl_distance_increase_pct"]:
                self._alerts.append(DriftAlert(
                    metric="sl_distance",
                    current_value=recent_sl,
                    baseline_value=base_sl,
                    change_pct=round(change_pct, 1),
                    severity=AlertSeverity.WARNING,
                    message=f"Average SL distance increased {change_pct:.1f}%",
                    timestamp=time.time(),
                    details={},
                ))
    
    def _check_symbol_degradation(self, trades: List[Dict]) -> None:
        """Check for symbols with degrading performance."""
        symbol_trades = {}
        for t in trades:
            sym = t.get("symbol", "")
            if sym not in symbol_trades:
                symbol_trades[sym] = []
            symbol_trades[sym].append(t)
        
        for sym, sym_trades in symbol_trades.items():
            if len(sym_trades) < 10:
                continue
            
            # Split into halves
            mid = len(sym_trades) // 2
            first_half = sym_trades[:mid]
            second_half = sym_trades[mid:]
            
            first_wr = self._calc_win_rate(first_half)
            second_wr = self._calc_win_rate(second_half)
            
            if first_wr > 0 and second_wr < first_wr * 0.7:  # 30% drop
                self._alerts.append(DriftAlert(
                    metric=f"symbol_{sym}",
                    current_value=second_wr,
                    baseline_value=first_wr,
                    change_pct=round((second_wr - first_wr) / first_wr * 100, 1),
                    severity=AlertSeverity.WARNING,
                    message=f"{sym} win rate degraded: {first_wr:.1f}% → {second_wr:.1f}%",
                    timestamp=time.time(),
                    details={"symbol": sym},
                ))
    
    def _check_session_degradation(self, trades: List[Dict]) -> None:
        """Check for sessions with degrading performance."""
        session_trades = {}
        for t in trades:
            sess = t.get("session", "unknown")
            if sess not in session_trades:
                session_trades[sess] = []
            session_trades[sess].append(t)
        
        for sess, sess_trades in session_trades.items():
            if len(sess_trades) < 10:
                continue
            
            mid = len(sess_trades) // 2
            first_half = sess_trades[:mid]
            second_half = sess_trades[mid:]
            
            first_wr = self._calc_win_rate(first_half)
            second_wr = self._calc_win_rate(second_half)
            
            if first_wr > 0 and second_wr < first_wr * 0.7:
                self._alerts.append(DriftAlert(
                    metric=f"session_{sess}",
                    current_value=second_wr,
                    baseline_value=first_wr,
                    change_pct=round((second_wr - first_wr) / first_wr * 100, 1),
                    severity=AlertSeverity.WARNING,
                    message=f"{sess} session win rate degraded: {first_wr:.1f}% → {second_wr:.1f}%",
                    timestamp=time.time(),
                    details={"session": sess},
                ))
    
    # ── Helper Calculations ───────────────────────────────────────
    
    def _calc_win_rate(self, trades: List[Dict]) -> float:
        if not trades:
            return 0
        wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
        return wins / len(trades) * 100
    
    def _avg_confidence(self, trades: List[Dict]) -> float:
        if not trades:
            return 0
        confs = [t.get("confidence", 0) or 0 for t in trades]
        return sum(confs) / len(confs) * 100
    
    def _calc_max_drawdown(self, trades: List[Dict]) -> float:
        pnls = [t.get("pnl") or 0 for t in trades]
        cum = 0
        peak = 0
        max_dd = 0
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
        return max_dd
    
    def _calc_expectancy(self, trades: List[Dict]) -> float:
        pnls = [t.get("pnl") or 0 for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p < 0]
        n = len(pnls)
        wr = len(wins) / n * 100 if n else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        return (wr / 100 * avg_win) - ((100 - wr) / 100 * avg_loss)
    
    def _avg_rr(self, trades: List[Dict]) -> float:
        rrs = [t.get("risk_reward", 0) or 0 for t in trades if t.get("risk_reward")]
        return sum(rrs) / len(rrs) if rrs else 0
    
    def _avg_sl_distance(self, trades: List[Dict]) -> float:
        dists = []
        for t in trades:
            entry = t.get("entry_price", 0)
            sl = t.get("stop_loss", 0)
            if entry and sl:
                dists.append(abs(entry - sl) / entry * 100)
        return sum(dists) / len(dists) if dists else 0
    
    def get_alerts(self) -> List[Dict]:
        """Get current alerts as dictionaries."""
        return [
            {
                "metric": a.metric,
                "current_value": a.current_value,
                "baseline_value": a.baseline_value,
                "change_pct": a.change_pct,
                "severity": a.severity.value,
                "message": a.message,
                "timestamp": a.timestamp,
            }
            for a in self._alerts
        ]
    
    def get_status(self) -> Dict:
        """Get drift detection status."""
        return {
            "alerts_count": len(self._alerts),
            "critical_count": sum(1 for a in self._alerts if a.severity == AlertSeverity.CRITICAL),
            "warning_count": sum(1 for a in self._alerts if a.severity == AlertSeverity.WARNING),
            "last_check": time.time(),
            "thresholds": self._thresholds,
        }


# Global singleton
_detector: Optional[DriftDetector] = None

def get_drift_detector() -> DriftDetector:
    """Get or create the global drift detector."""
    global _detector
    if _detector is None:
        _detector = DriftDetector()
    return _detector
