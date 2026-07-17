"""
Expectancy Engine — Rank signals by Expected Value, not confidence.

READ-ONLY with respect to upstream data. Never modifies signals.

Per Master Directive:
    "Instead of ranking by confidence alone, rank by Expected Value.
     Expected Value = P(Win) × Avg Winner − P(Loss) × Avg Loser.
     Sort by Expectancy → Trade."

This is the single biggest improvement that does not require changing
EMA or Smart Money logic.

Historical Calibration:
    Uses the last N completed trades to calibrate per-symbol, per-regime,
    and per-confidence-bucket win rates and average outcomes.

Signal Ranking:
    Each signal receives an Expected Value score that incorporates:
    1. Historical win rate for similar conditions
    2. Average winner size for similar conditions
    3. Average loser size for similar conditions
    4. Current R:R ratio
    5. Confidence-adjusted probability
"""
from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

# Minimum historical trades needed for reliable calibration
MIN_TRADES_FOR_CALIBRATION = 20

# Confidence bucket boundaries (same as Trade Quality Engine)
CONF_BUCKETS = [
    (95, 100, "95-100"),
    (90, 95, "90-95"),
    (85, 90, "85-90"),
    (80, 85, "80-85"),
    (75, 80, "75-80"),
    (0, 75, "<75"),
]

# Minimum EV threshold (in R-multiples) to consider a trade
MIN_EV_THRESHOLD = 0.5


@dataclass
class CalibrationData:
    """Historical calibration for a specific condition set."""
    symbol: str = ""
    regime: str = ""
    conf_bucket: str = ""
    side: str = ""
    sample_size: int = 0
    win_rate: float = 0.0
    avg_winner_r: float = 0.0
    avg_loser_r: float = 0.0
    avg_r: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "regime": self.regime,
            "conf_bucket": self.conf_bucket,
            "side": self.side,
            "sample_size": self.sample_size,
            "win_rate": round(self.win_rate, 3),
            "avg_winner_r": round(self.avg_winner_r, 3),
            "avg_loser_r": round(self.avg_loser_r, 3),
            "avg_r": round(self.avg_r, 3),
            "profit_factor": round(self.profit_factor, 2),
            "expectancy_r": round(self.expectancy_r, 3),
        }


@dataclass
class ExpectancyResult:
    """Expected value result for a signal."""
    symbol: str = ""
    side: str = ""
    expected_value_r: float = 0.0
    expected_value_usd: float = 0.0
    calibrated_win_rate: float = 0.0
    calibrated_avg_winner: float = 0.0
    calibrated_avg_loser: float = 0.0
    signal_rr: float = 0.0
    confidence_adjusted_prob: float = 0.0
    calibration_data: Optional[CalibrationData] = None
    is_positive_ev: bool = False
    rank_score: float = 0.0  # Combined score for ranking

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "side": self.side,
            "expected_value_r": round(self.expected_value_r, 3),
            "expected_value_usd": round(self.expected_value_usd, 2),
            "calibrated_win_rate": round(self.calibrated_win_rate, 3),
            "calibrated_avg_winner": round(self.calibrated_avg_winner, 3),
            "calibrated_avg_loser": round(self.calibrated_avg_loser, 3),
            "signal_rr": round(self.signal_rr, 2),
            "confidence_adjusted_prob": round(self.confidence_adjusted_prob, 3),
            "is_positive_ev": self.is_positive_ev,
            "rank_score": round(self.rank_score, 2),
        }


class ExpectancyEngine:
    """
    Ranks signals by Expected Value using historical calibration.

    Per Master Directive:
        Expected Value = P(Win) × Avg Winner − P(Loss) × Avg Loser

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._calibration_cache: Dict[str, CalibrationData] = {}
        self._global_calibration: Optional[CalibrationData] = None
        self._last_calibration_load = 0.0

    def evaluate(
        self,
        signal: Dict[str, Any],
        risk_amount: float = 100.0,
    ) -> ExpectancyResult:
        """
        Calculate Expected Value for a signal.

        Args:
            signal: Live Sheet signal dict
            risk_amount: USD amount at risk per trade

        Returns:
            ExpectancyResult with EV calculation and ranking score
        """
        symbol = signal.get("symbol", "")
        side = signal.get("side", "")
        confidence = signal.get("confidence", 0)
        rr = signal.get("risk_reward", 0)
        regime = signal.get("regime", signal.get("market_regime", "unknown"))
        entry = signal.get("entry_price", signal.get("entry", 0))
        sl = signal.get("stop_loss", 0)
        tp = signal.get("take_profit", 0)

        result = ExpectancyResult(symbol=symbol, side=side, signal_rr=rr)

        # Ensure calibration data is loaded
        self._ensure_calibration_loaded()

        # ── Get calibration data ──
        conf_bucket = self._get_conf_bucket(confidence)
        cal = self._get_calibration(symbol, regime, conf_bucket, side)
        result.calibration_data = cal

        # ── Calculate win probability ──
        if cal and cal.sample_size >= MIN_TRADES_FOR_CALIBRATION:
            # Use historical win rate, adjusted by current confidence
            base_wr = cal.win_rate
            # Confidence adjustment: high confidence boosts probability
            conf_factor = confidence / 100 if confidence <= 1.0 else confidence / 100
            adjusted_wr = base_wr * (0.7 + 0.3 * conf_factor)
            adjusted_wr = min(adjusted_wr, 0.95)  # Cap at 95%
        else:
            # No calibration data — use confidence as rough probability proxy
            conf_val = confidence if confidence <= 1.0 else confidence / 100
            adjusted_wr = conf_val * 0.6  # Conservative: confidence overstates true WR

        result.calibrated_win_rate = adjusted_wr
        result.confidence_adjusted_prob = adjusted_wr

        # ── Calculate expected winner/loser ──
        if cal and cal.sample_size >= MIN_TRADES_FOR_CALIBRATION:
            avg_winner_r = cal.avg_winner_r
            avg_loser_r = abs(cal.avg_loser_r)
        else:
            # Use R:R ratio as estimate
            avg_winner_r = rr if rr > 0 else 1.5
            avg_loser_r = 1.0  # Standard 1R loss

        result.calibrated_avg_winner = avg_winner_r
        result.calibrated_avg_loser = avg_loser_r

        # ── Calculate Expected Value in R-multiples ──
        ev_r = (adjusted_wr * avg_winner_r) - ((1 - adjusted_wr) * avg_loser_r)
        result.expected_value_r = ev_r

        # ── Calculate Expected Value in USD ──
        result.expected_value_usd = ev_r * risk_amount

        # ── Positive EV? ──
        result.is_positive_ev = ev_r >= MIN_EV_THRESHOLD

        # ── Ranking Score ──
        # Combines EV with sample size confidence
        sample_confidence = min(cal.sample_size / 100, 1.0) if cal else 0.3
        result.rank_score = ev_r * (0.7 + 0.3 * sample_confidence)

        logger.debug(
            "EV: {} {} → EV={:.3f}R (${:.2f}) WR={:.1%} avgW={:.2f}R avgL={:.2f}R "
            "positive={} rank={:.2f} (cal={})",
            symbol, side, ev_r, result.expected_value_usd,
            adjusted_wr, avg_winner_r, avg_loser_r,
            result.is_positive_ev, result.rank_score,
            "historical" if cal and cal.sample_size >= MIN_TRADES_FOR_CALIBRATION else "default",
        )

        return result

    def rank_signals(
        self,
        signals: List[Dict[str, Any]],
        risk_amount: float = 100.0,
    ) -> List[ExpectancyResult]:
        """
        Rank multiple signals by Expected Value.

        Args:
            signals: List of signal dicts
            risk_amount: USD amount at risk per trade

        Returns:
            List of ExpectancyResult sorted by rank_score descending
        """
        results = []
        for sig in signals:
            result = self.evaluate(sig, risk_amount)
            results.append(result)

        # Sort by rank score descending (highest EV first)
        results.sort(key=lambda r: r.rank_score, reverse=True)

        return results

    # ── Calibration Methods ──────────────────────────────────────

    def _ensure_calibration_loaded(self) -> None:
        """Load calibration data from database if stale."""
        now = time.time()
        if now - self._last_calibration_load < 300:  # Refresh every 5 min
            return

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Get closed trades from positions table
            cur.execute("""
                SELECT symbol, side, regime, confidence, pnl, realized_r,
                       hold_minutes, exit_reason
                FROM positions WHERE status = 'closed'
                ORDER BY closed_at DESC
            """)
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()

            if not rows:
                self._last_calibration_load = now
                return

            # Build calibration cache
            self._calibration_cache.clear()

            # Group by symbol+regime+conf_bucket+side
            groups: Dict[str, List] = {}
            for row in rows:
                conf = (row.get("confidence") or 0) * 100
                bucket = self._get_conf_bucket(conf)
                key = f"{row.get('symbol', '')}_{row.get('regime', 'unknown')}_{bucket}_{row.get('side', '')}"
                if key not in groups:
                    groups[key] = []
                groups[key].append(row)

            # Calculate calibration for each group
            for key, trades in groups.items():
                cal = self._calculate_calibration(trades)
                if cal:
                    self._calibration_cache[key] = cal

            # Global calibration
            self._global_calibration = self._calculate_calibration(rows)

            self._last_calibration_load = now
            logger.debug(
                "Expectancy calibration loaded: {} groups, {} total trades, global WR={:.1%}",
                len(self._calibration_cache), len(rows),
                self._global_calibration.win_rate if self._global_calibration else 0,
            )

        except Exception as e:
            logger.warning("Expectancy calibration load error: {}", e)

    def _calculate_calibration(self, trades: List[Dict]) -> Optional[CalibrationData]:
        """Calculate calibration data from a group of trades."""
        if not trades:
            return None

        n = len(trades)
        pnls = [t.get("pnl", 0) or 0 for t in trades]
        rs = [t.get("realized_r", 0) or 0 for t in trades]

        wins = [r for r in rs if r > 0]
        losses = [r for r in rs if r <= 0]

        win_rate = len(wins) / n if n > 0 else 0
        avg_winner = sum(wins) / len(wins) if wins else 0
        avg_loser = sum(losses) / len(losses) if losses else 0
        avg_r = sum(rs) / n if n > 0 else 0

        gp = sum(wins) if wins else 0
        gl = sum(abs(l) for l in losses) if losses else 0
        pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)

        ev = (win_rate * avg_winner) - ((1 - win_rate) * abs(avg_loser))

        return CalibrationData(
            sample_size=n,
            win_rate=win_rate,
            avg_winner_r=avg_winner,
            avg_loser_r=avg_loser,
            avg_r=avg_r,
            profit_factor=pf,
            expectancy_r=ev,
        )

    def _get_calibration(
        self, symbol: str, regime: str, conf_bucket: str, side: str
    ) -> Optional[CalibrationData]:
        """Get calibration data for specific conditions."""
        # Try most specific match first
        key = f"{symbol}_{regime}_{conf_bucket}_{side}"
        if key in self._calibration_cache:
            return self._calibration_cache[key]

        # Try without side
        for k, v in self._calibration_cache.items():
            if k.startswith(f"{symbol}_{regime}_{conf_bucket}_"):
                return v

        # Try global
        return self._global_calibration

    @staticmethod
    def _get_conf_bucket(confidence: float) -> str:
        """Map confidence to bucket label."""
        for lo, hi, label in CONF_BUCKETS:
            if lo <= confidence <= hi:
                return label
        return "<75"

    def get_global_calibration(self) -> Optional[CalibrationData]:
        """Get global calibration data."""
        self._ensure_calibration_loaded()
        return self._global_calibration

    def force_reload(self) -> None:
        """Force calibration reload."""
        self._last_calibration_load = 0.0
