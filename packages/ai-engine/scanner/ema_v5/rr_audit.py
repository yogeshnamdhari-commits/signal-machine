"""
EMA_V5 Risk:Reward Audit System
================================
Comprehensive logging of every RR rejection with full diagnostic data.
Answers: WHY are signals failing the RR gate?

Tracks per-rejection:
- Symbol, side, entry, SL, TP1, TP2, TP3
- Risk (SL distance), Reward (TP distance)
- Actual RR, Required RR
- ATR value, SL multiplier used
- Session, regime, confidence
- Timestamp and rejection reason category

Stores last 1000 rejections in memory + writes to CSV for analysis.
"""
from __future__ import annotations

import csv
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


@dataclass
class RRRejection:
    """Single RR rejection record with full diagnostic context."""
    timestamp: float
    symbol: str
    side: str  # LONG / SHORT
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    risk: float          # abs(entry - sl)
    reward: float        # abs(tp1 - entry)
    rr_actual: float     # reward / risk
    rr_required: float   # minimum RR threshold
    rr_deficit: float    # rr_required - rr_actual (how far short)
    atr_value: float
    sl_atr_mult: float   # ATR multiplier used for SL
    sl_dist_pct: float   # SL distance as % of entry
    tp1_rr_mult: float   # TP1 multiplier relative to risk
    session: str
    regime: str
    confidence: float
    rejection_source: str  # "signal_engine" / "engine_rr_filter" / "priority"
    rejection_reason: str  # Detailed reason

    @property
    def risk_pct(self) -> float:
        """Risk as percentage of entry price."""
        return (self.risk / self.entry * 100) if self.entry > 0 else 0

    @property
    def reward_pct(self) -> float:
        """Reward as percentage of entry price."""
        return (self.reward / self.entry * 100) if self.entry > 0 else 0

    @property
    def rr_gap(self) -> float:
        """How much additional reward would be needed to pass."""
        if self.risk <= 0:
            return 0
        needed_reward = self.rr_required * self.risk
        return needed_reward - self.reward

    def to_dict(self) -> Dict:
        """Convert to dictionary for CSV/JSON export."""
        d = asdict(self)
        d["risk_pct"] = round(self.risk_pct, 4)
        d["reward_pct"] = round(self.reward_pct, 4)
        d["rr_gap"] = round(self.rr_gap, 6)
        d["timestamp_iso"] = datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat()
        # Remove raw timestamp for CSV (use timestamp_iso instead)
        del d["timestamp"]
        return d


class RRAuditTracker:
    """
    Tracks and analyzes RR rejections with pattern detection.
    
    Usage:
        audit = RRAuditTracker()
        
        # When a signal is rejected for RR:
        audit.record_rejection(
            symbol="BTCUSDT", side="LONG",
            entry=108200, sl=107900, tp1=108650, ...
        )
        
        # Get analysis:
        report = audit.get_analysis_report()
        print(report)
    """

    def __init__(self, max_history: int = 1000, csv_dir: Optional[str] = None):
        self._rejections: List[RRRejection] = []
        self._max_history = max_history
        
        # Pattern counters
        self._by_symbol: Dict[str, int] = defaultdict(int)
        self._by_session: Dict[str, int] = defaultdict(int)
        self._by_regime: Dict[str, int] = defaultdict(int)
        self._by_source: Dict[str, int] = defaultdict(int)
        self._by_rr_bucket: Dict[str, int] = defaultdict(int)
        self._by_sl_dist_bucket: Dict[str, int] = defaultdict(int)
        
        # Running statistics
        self._total_rejections = 0
        self._total_rr_sum = 0.0
        self._total_risk_sum = 0.0
        self._total_reward_sum = 0.0
        self._rr_histogram: Dict[str, int] = defaultdict(int)
        
        # CSV output
        self._csv_dir = Path(csv_dir) if csv_dir else None
        self._csv_path: Optional[Path] = None
        self._csv_writer = None
        self._csv_file = None
        
        if self._csv_dir:
            self._init_csv()

    def _init_csv(self):
        """Initialize CSV file for persistent logging."""
        try:
            self._csv_dir.mkdir(parents=True, exist_ok=True)
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            self._csv_path = self._csv_dir / f"rr_rejections_{date_str}.csv"
            
            # Check if file exists AND has content (for append mode)
            file_exists = self._csv_path.exists()
            file_has_content = file_exists and self._csv_path.stat().st_size > 0
            
            self._csv_file = open(self._csv_path, "a", newline="")
            self._csv_writer = csv.DictWriter(self._csv_file, fieldnames=[
                "timestamp_iso", "symbol", "side", "entry", "stop_loss",
                "tp1", "tp2", "tp3", "risk", "reward", "rr_actual",
                "rr_required", "rr_deficit", "risk_pct", "reward_pct",
                "rr_gap", "atr_value", "sl_atr_mult", "sl_dist_pct",
                "tp1_rr_mult", "session", "regime", "confidence",
                "rejection_source", "rejection_reason",
            ])
            
            # Write header if file is new OR empty
            if not file_has_content:
                self._csv_writer.writeheader()
                self._csv_file.flush()
                
            logger.info("📝 RR_AUDIT: CSV logging initialized → {}", self._csv_path)
        except Exception as e:
            logger.warning("RR_AUDIT: Failed to initialize CSV: {}", e)
            self._csv_writer = None

    def record_rejection(
        self,
        symbol: str,
        side: str,
        entry: float,
        stop_loss: float,
        tp1: float,
        tp2: float = 0,
        tp3: float = 0,
        atr_value: float = 0,
        sl_atr_mult: float = 1.5,
        tp1_rr_mult: float = 1.5,
        session: str = "",
        regime: str = "",
        confidence: float = 0,
        rr_required: float = 1.5,
        rejection_source: str = "signal_engine",
        rejection_reason: str = "",
    ):
        """Record an RR rejection with full diagnostic context."""
        
        # Compute derived values
        risk = abs(entry - stop_loss) if entry > 0 and stop_loss > 0 else 0
        reward = abs(tp1 - entry) if entry > 0 and tp1 > 0 else 0
        rr_actual = reward / risk if risk > 0 else 0
        rr_deficit = rr_required - rr_actual
        sl_dist_pct = (risk / entry * 100) if entry > 0 else 0
        
        rejection = RRRejection(
            timestamp=time.time(),
            symbol=symbol,
            side=side,
            entry=entry,
            stop_loss=stop_loss,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            risk=risk,
            reward=reward,
            rr_actual=rr_actual,
            rr_required=rr_required,
            rr_deficit=rr_deficit,
            atr_value=atr_value,
            sl_atr_mult=sl_atr_mult,
            sl_dist_pct=sl_dist_pct,
            tp1_rr_mult=tp1_rr_mult,
            session=session,
            regime=regime,
            confidence=confidence,
            rejection_source=rejection_source,
            rejection_reason=rejection_reason,
        )
        
        # Store in memory
        self._rejections.append(rejection)
        if len(self._rejections) > self._max_history:
            self._rejections = self._rejections[-self._max_history:]
        
        # Update counters
        self._total_rejections += 1
        self._total_rr_sum += rr_actual
        self._total_risk_sum += risk
        self._total_reward_sum += reward
        
        self._by_symbol[symbol] += 1
        self._by_session[session or "unknown"] += 1
        self._by_regime[regime or "unknown"] += 1
        self._by_source[rejection_source] += 1
        
        # RR bucket (0.5 increments)
        rr_bucket = f"{int(rr_actual * 2) / 2:.1f}-{int(rr_actual * 2) / 2 + 0.5:.1f}"
        self._by_rr_bucket[rr_bucket] += 1
        
        # SL distance bucket (0.5% increments)
        sl_bucket = f"{int(sl_dist_pct * 2) / 2:.1f}%-{int(sl_dist_pct * 2) / 2 + 0.5:.1f}%"
        self._by_sl_dist_bucket[sl_bucket] += 1
        
        # RR histogram (0.1 increments)
        rr_hist_key = f"{rr_actual:.1f}"
        self._rr_histogram[rr_hist_key] += 1
        
        # Log to CSV
        if self._csv_writer:
            try:
                self._csv_writer.writerow(rejection.to_dict())
                self._csv_file.flush()
            except Exception as e:
                logger.debug("RR_AUDIT: CSV write failed: {}", e)
        
        # Log summary
        logger.info(
            "📊 RR_AUDIT: {} {} REJECTED | Entry={} SL={} TP1={} | "
            "Risk={:.4f} ({:.2f}%) Reward={:.4f} ({:.2f}%) | "
            "RR={:.2f} < {:.2f} (deficit={:.2f}) | ATR={:.6f} SL_mult={:.1f}",
            side, symbol, entry, stop_loss, tp1,
            risk, sl_dist_pct, reward, rejection.reward_pct,
            rr_actual, rr_required, rr_deficit,
            atr_value, sl_atr_mult,
        )

    def get_analysis_report(self) -> str:
        """Generate comprehensive analysis report of RR rejections."""
        if not self._rejections:
            return "No RR rejections recorded yet."
        
        lines = []
        lines.append("=" * 80)
        lines.append("📊 RISK:REWARD AUDIT REPORT")
        lines.append(f"   Total Rejections: {self._total_rejections}")
        lines.append(f"   Tracked in Memory: {len(self._rejections)}")
        lines.append("=" * 80)
        
        # ── Summary Statistics ──
        avg_rr = self._total_rr_sum / len(self._rejections) if self._rejections else 0
        avg_risk = self._total_risk_sum / len(self._rejections) if self._rejections else 0
        avg_reward = self._total_reward_sum / len(self._rejections) if self._rejections else 0
        
        lines.append("\n📈 SUMMARY STATISTICS")
        lines.append(f"   Average RR:      {avg_rr:.3f}")
        lines.append(f"   Average Risk:    {avg_risk:.6f}")
        lines.append(f"   Average Reward:  {avg_reward:.6f}")
        
        # ── RR Distribution ──
        lines.append("\n📊 RR DISTRIBUTION (actual RR at rejection)")
        for rr_val in sorted(self._rr_histogram.keys(), key=lambda x: float(x)):
            count = self._rr_histogram[rr_val]
            pct = count / len(self._rejections) * 100
            bar = "█" * int(pct / 2)
            lines.append(f"   RR={rr_val:>4s}: {count:>4d} ({pct:>5.1f}%) {bar}")
        
        # ── Top Rejected Symbols ──
        lines.append("\n🔴 TOP REJECTED SYMBOLS")
        for sym, count in sorted(self._by_symbol.items(), key=lambda x: -x[1])[:15]:
            # Get average RR for this symbol
            sym_rej = [r for r in self._rejections if r.symbol == sym]
            sym_avg_rr = sum(r.rr_actual for r in sym_rej) / len(sym_rej) if sym_rej else 0
            sym_avg_sl = sum(r.sl_dist_pct for r in sym_rej) / len(sym_rej) if sym_rej else 0
            lines.append(f"   {sym:<16s} {count:>4d} rejections | avg RR={sym_avg_rr:.2f} | avg SL dist={sym_avg_sl:.2f}%")
        
        # ── By Session ──
        lines.append("\n🕐 BY SESSION")
        for sess, count in sorted(self._by_session.items(), key=lambda x: -x[1]):
            pct = count / len(self._rejections) * 100
            lines.append(f"   {sess:<20s} {count:>4d} ({pct:>5.1f}%)")
        
        # ── By Regime ──
        lines.append("\n📊 BY REGIME")
        for regime, count in sorted(self._by_regime.items(), key=lambda x: -x[1]):
            pct = count / len(self._rejections) * 100
            lines.append(f"   {regime:<20s} {count:>4d} ({pct:>5.1f}%)")
        
        # ── SL Distance Analysis ──
        lines.append("\n📏 STOP LOSS DISTANCE ANALYSIS")
        for bucket in sorted(self._by_sl_dist_bucket.keys(), key=lambda x: float(x.split("-")[0].rstrip("%"))):
            count = self._by_sl_dist_bucket[bucket]
            pct = count / len(self._rejections) * 100
            bar = "█" * int(pct / 2)
            lines.append(f"   SL dist {bucket:>12s}: {count:>4d} ({pct:>5.1f}%) {bar}")
        
        # ── Root Cause Analysis ──
        lines.append("\n🔍 ROOT CAUSE ANALYSIS")
        
        # Check if SL is too wide
        wide_sl_count = sum(1 for r in self._rejections if r.sl_dist_pct > 3.0)
        if wide_sl_count > len(self._rejections) * 0.3:
            lines.append("   ⚠️  HIGH SL DISTANCE: {:.1f}% of rejections have SL > 3%".format(
                wide_sl_count / len(self._rejections) * 100))
            lines.append("      → Consider reducing sl_atr_mult from {:.1f}".format(
                self._rejections[0].sl_atr_mult if self._rejections else 1.5))
        
        # Check if reward is too low
        low_reward_count = sum(1 for r in self._rejections if r.reward_pct < 1.0)
        if low_reward_count > len(self._rejections) * 0.3:
            lines.append("   ⚠️  LOW REWARD: {:.1f}% of rejections have reward < 1%".format(
                low_reward_count / len(self._rejections) * 100))
            lines.append("      → TP1 may be too close to entry")
        
        # Check if RR threshold is too high
        close_rr_count = sum(1 for r in self._rejections if 0 < r.rr_actual < r.rr_required * 1.1)
        if close_rr_count > len(self._rejections) * 0.2:
            lines.append("   ⚠️  NEAR-MISS: {:.1f}% of rejections are within 10% of passing".format(
                close_rr_count / len(self._rejections) * 100))
            lines.append("      → Consider lowering min_rr from {:.1f}".format(
                self._rejections[0].rr_required if self._rejections else 1.5))
        
        # ── Recent Rejections (last 10) ──
        lines.append("\n📋 LAST 10 REJECTIONS")
        lines.append(f"   {'SYMBOL':<14s} {'SIDE':<6s} {'ENTRY':>12s} {'SL':>12s} {'TP1':>12s} {'RISK':>8s} {'REWARD':>8s} {'RR':>6s} {'REQ':>6s} {'SL%':>6s}")
        lines.append("   " + "-" * 100)
        for r in self._rejections[-10:]:
            lines.append(
                f"   {r.symbol:<14s} {r.side:<6s} {r.entry:>12.4f} {r.stop_loss:>12.4f} "
                f"{r.tp1:>12.4f} {r.risk:>8.4f} {r.reward:>8.4f} {r.rr_actual:>6.2f} "
                f"{r.rr_required:>6.2f} {r.sl_dist_pct:>5.2f}%"
            )
        
        lines.append("\n" + "=" * 80)
        return "\n".join(lines)

    def get_rejection_stats(self) -> Dict:
        """Get rejection statistics for dashboard display."""
        if not self._rejections:
            return {
                "total": 0,
                "avg_rr": 0,
                "top_symbols": [],
                "top_sessions": [],
                "top_regimes": [],
                "rr_distribution": {},
                "sl_distance_avg": 0,
                "near_miss_pct": 0,
            }
        
        avg_rr = self._total_rr_sum / len(self._rejections)
        avg_sl = sum(r.sl_dist_pct for r in self._rejections) / len(self._rejections)
        near_miss = sum(1 for r in self._rejections if 0 < r.rr_actual < r.rr_required * 1.1)
        near_miss_pct = near_miss / len(self._rejections) * 100
        
        return {
            "total": self._total_rejections,
            "tracked": len(self._rejections),
            "avg_rr": round(avg_rr, 3),
            "avg_sl_dist_pct": round(avg_sl, 2),
            "near_miss_pct": round(near_miss_pct, 1),
            "top_symbols": sorted(self._by_symbol.items(), key=lambda x: -x[1])[:10],
            "top_sessions": sorted(self._by_session.items(), key=lambda x: -x[1]),
            "top_regimes": sorted(self._by_regime.items(), key=lambda x: -x[1]),
            "rr_distribution": dict(sorted(self._rr_histogram.items())),
            "sl_distance_distribution": dict(sorted(self._by_sl_dist_bucket.items())),
            "csv_path": str(self._csv_path) if self._csv_path else None,
        }

    def get_recent_rejections(self, count: int = 50) -> List[Dict]:
        """Get recent rejections for detailed display."""
        return [r.to_dict() for r in self._rejections[-count:]]

    def get_symbol_analysis(self, symbol: str) -> Dict:
        """Get detailed analysis for a specific symbol."""
        sym_rej = [r for r in self._rejections if r.symbol == symbol]
        if not sym_rej:
            return {"symbol": symbol, "rejections": 0}
        
        return {
            "symbol": symbol,
            "rejections": len(sym_rej),
            "avg_rr": round(sum(r.rr_actual for r in sym_rej) / len(sym_rej), 3),
            "avg_sl_dist": round(sum(r.sl_dist_pct for r in sym_rej) / len(sym_rej), 2),
            "avg_risk": round(sum(r.risk for r in sym_rej) / len(sym_rej), 6),
            "avg_reward": round(sum(r.reward for r in sym_rej) / len(sym_rej), 6),
            "min_rr": round(min(r.rr_actual for r in sym_rej), 3),
            "max_rr": round(max(r.rr_actual for r in sym_rej), 3),
            "recent": [r.to_dict() for r in sym_rej[-5:]],
        }

    def reset(self):
        """Reset all counters (for new scan cycle)."""
        self._rejections.clear()
        self._by_symbol.clear()
        self._by_session.clear()
        self._by_regime.clear()
        self._by_source.clear()
        self._by_rr_bucket.clear()
        self._by_sl_dist_bucket.clear()
        self._total_rejections = 0
        self._total_rr_sum = 0
        self._total_risk_sum = 0
        self._total_reward_sum = 0
        self._rr_histogram.clear()

    def close(self):
        """Close CSV file handle."""
        if self._csv_file:
            try:
                self._csv_file.close()
            except Exception:
                pass


# Global singleton for engine-wide use
_rr_audit: Optional[RRAuditTracker] = None


def get_rr_audit() -> RRAuditTracker:
    """Get or create the global RR audit tracker."""
    global _rr_audit
    if _rr_audit is None:
        csv_dir = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "rr_audit"
        _rr_audit = RRAuditTracker(max_history=1000, csv_dir=str(csv_dir))
    return _rr_audit
