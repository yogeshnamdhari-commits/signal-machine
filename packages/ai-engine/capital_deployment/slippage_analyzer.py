"""
Slippage Analyzer — Execution Quality Tracking
===============================================
Tracks: Expected vs Actual Entry/Exit, Average Slippage,
        Worst Slippage, Slippage By Symbol
"""

import json
import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "capital"


# ─── Data Classes ────────────────────────────────────────────────────────────
@dataclass
class SlippageRecord:
    """Individual slippage measurement."""
    trade_id: str
    symbol: str
    side: str
    expected_price: float
    actual_price: float
    quantity: float
    slippage_abs: float       # Absolute difference
    slippage_bps: float       # Basis points
    slippage_pct: float       # Percentage
    slippage_usd: float       # USD impact
    is_entry: bool            # True = entry, False = exit
    timestamp: str


@dataclass
class SymbolSlippageStats:
    """Per-symbol slippage statistics."""
    symbol: str
    total_trades: int
    avg_slippage_bps: float
    median_slippage_bps: float
    worst_slippage_bps: float
    best_slippage_bps: float
    std_dev_bps: float
    total_slippage_usd: float
    entry_slippage_bps: float
    exit_slippage_bps: float
    positive_slippage_count: int  # Favors trader
    negative_slippage_count: int  # Against trader


@dataclass
class SlippageReport:
    """Complete slippage analysis report."""
    timestamp: str
    total_trades: int
    avg_slippage_bps: float
    median_slippage_bps: float
    worst_slippage_bps: float
    total_slippage_usd: float
    avg_entry_slippage_bps: float
    avg_exit_slippage_bps: float
    slippage_by_symbol: dict
    slippage_quality: str  # EXCELLENT, GOOD, FAIR, POOR
    recommendations: list


# ─── Slippage Analyzer ──────────────────────────────────────────────────────
class SlippageAnalyzer:
    """
    Tracks and analyzes execution slippage across all trades.

    Usage:
        analyzer = SlippageAnalyzer()
        record = analyzer.record_slippage(
            trade_id="T1", symbol="BTCUSDT", side="BUY",
            expected=50000, actual=50010, qty=0.1, is_entry=True
        )
        report = analyzer.get_report()
    """

    # Quality thresholds (basis points)
    EXCELLENT_THRESHOLD = 5    # < 5 bps
    GOOD_THRESHOLD = 15        # < 15 bps
    FAIR_THRESHOLD = 30        # < 30 bps
    # > 30 bps = POOR

    def __init__(self):
        self._records: list[SlippageRecord] = []
        self._by_symbol: dict[str, list[SlippageRecord]] = {}
        self._load_state()
        logger.info("SlippageAnalyzer initialized: %d records", len(self._records))

    # ── Record Slippage ──────────────────────────────────────────────────────
    def record_slippage(
        self,
        trade_id: str,
        symbol: str,
        side: str,
        expected_price: float,
        actual_price: float,
        quantity: float,
        is_entry: bool,
    ) -> SlippageRecord:
        """Record slippage for a trade execution."""
        if expected_price <= 0:
            raise ValueError(f"Invalid expected price: {expected_price}")
        if actual_price <= 0:
            raise ValueError(f"Invalid actual price: {actual_price}")

        # Calculate slippage
        slippage_abs = actual_price - expected_price

        # For buys, positive slippage = paid more (bad)
        # For sells, positive slippage = received less (bad)
        if side.upper() == "BUY":
            slippage_abs = actual_price - expected_price
        else:
            slippage_abs = expected_price - actual_price

        slippage_bps = (slippage_abs / expected_price) * 10000
        slippage_pct = (slippage_abs / expected_price) * 100
        slippage_usd = abs(slippage_abs) * quantity

        record = SlippageRecord(
            trade_id=trade_id,
            symbol=symbol,
            side=side,
            expected_price=expected_price,
            actual_price=actual_price,
            quantity=quantity,
            slippage_abs=round(slippage_abs, 8),
            slippage_bps=round(slippage_bps, 2),
            slippage_pct=round(slippage_pct, 4),
            slippage_usd=round(slippage_usd, 2),
            is_entry=is_entry,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        self._records.append(record)
        if symbol not in self._by_symbol:
            self._by_symbol[symbol] = []
        self._by_symbol[symbol].append(record)

        self._save_state()

        # Log significant slippage
        if abs(slippage_bps) > self.GOOD_THRESHOLD:
            logger.warning("High slippage: %s %s %.1f bps ($%.2f)",
                           symbol, side, slippage_bps, slippage_usd)

        return record

    # ── Get Report ───────────────────────────────────────────────────────────
    def get_report(self) -> SlippageReport:
        """Generate comprehensive slippage report."""
        if not self._records:
            return SlippageReport(
                timestamp=datetime.now(timezone.utc).isoformat(),
                total_trades=0, avg_slippage_bps=0, median_slippage_bps=0,
                worst_slippage_bps=0, total_slippage_usd=0,
                avg_entry_slippage_bps=0, avg_exit_slippage_bps=0,
                slippage_by_symbol={}, slippage_quality="N/A",
                recommendations=["No trades recorded yet"],
            )

        all_bps = [r.slippage_bps for r in self._records]
        entry_bps = [r.slippage_bps for r in self._records if r.is_entry]
        exit_bps = [r.slippage_bps for r in self._records if not r.is_entry]

        avg_bps = sum(all_bps) / len(all_bps)
        median_bps = self._median(all_bps)
        worst_bps = max(all_bps, key=abs)
        total_usd = sum(r.slippage_usd for r in self._records)

        avg_entry = sum(entry_bps) / len(entry_bps) if entry_bps else 0
        avg_exit = sum(exit_bps) / len(exit_bps) if exit_bps else 0

        # Per-symbol stats
        sym_stats = {}
        for sym, records in self._by_symbol.items():
            sym_stats[sym] = self._calc_symbol_stats(sym, records)

        # Quality assessment
        quality = self._assess_quality(avg_bps, worst_bps)

        # Recommendations
        recs = self._generate_recommendations(avg_bps, worst_bps, sym_stats)

        return SlippageReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            total_trades=len(self._records),
            avg_slippage_bps=round(avg_bps, 2),
            median_slippage_bps=round(median_bps, 2),
            worst_slippage_bps=round(worst_bps, 2),
            total_slippage_usd=round(total_usd, 2),
            avg_entry_slippage_bps=round(avg_entry, 2),
            avg_exit_slippage_bps=round(avg_exit, 2),
            slippage_by_symbol=sym_stats,
            slippage_quality=quality,
            recommendations=recs,
        )

    # ── Per-Symbol Stats ─────────────────────────────────────────────────────
    def get_symbol_stats(self, symbol: str) -> Optional[SymbolSlippageStats]:
        """Get slippage stats for a specific symbol."""
        records = self._by_symbol.get(symbol, [])
        if not records:
            return None
        return self._calc_symbol_stats(symbol, records)

    def _calc_symbol_stats(self, symbol: str, records: list[SlippageRecord]) -> dict:
        """Calculate statistics for a symbol."""
        all_bps = [r.slippage_bps for r in records]
        entry_bps = [r.slippage_bps for r in records if r.is_entry]
        exit_bps = [r.slippage_bps for r in records if not r.is_entry]

        avg = sum(all_bps) / len(all_bps)
        std = math.sqrt(sum((b - avg) ** 2 for b in all_bps) / len(all_bps)) if len(all_bps) > 1 else 0

        return {
            "symbol": symbol,
            "trades": len(records),
            "avg_bps": round(avg, 2),
            "median_bps": round(self._median(all_bps), 2),
            "worst_bps": round(max(all_bps, key=abs), 2),
            "best_bps": round(min(all_bps, key=abs), 2),
            "std_bps": round(std, 2),
            "total_usd": round(sum(r.slippage_usd for r in records), 2),
            "entry_avg_bps": round(sum(entry_bps) / len(entry_bps), 2) if entry_bps else 0,
            "exit_avg_bps": round(sum(exit_bps) / len(exit_bps), 2) if exit_bps else 0,
            "positive_count": sum(1 for b in all_bps if b < 0),  # Negative = favorable
            "negative_count": sum(1 for b in all_bps if b > 0),
        }

    # ── Quality Assessment ───────────────────────────────────────────────────
    def _assess_quality(self, avg_bps: float, worst_bps: float) -> str:
        """Assess overall execution quality."""
        abs_avg = abs(avg_bps)
        if abs_avg < self.EXCELLENT_THRESHOLD:
            return "EXCELLENT"
        elif abs_avg < self.GOOD_THRESHOLD:
            return "GOOD"
        elif abs_avg < self.FAIR_THRESHOLD:
            return "FAIR"
        return "POOR"

    # ── Recommendations ──────────────────────────────────────────────────────
    def _generate_recommendations(
        self, avg_bps: float, worst_bps: float, sym_stats: dict
    ) -> list[str]:
        """Generate actionable recommendations."""
        recs = []

        if abs(avg_bps) > self.GOOD_THRESHOLD:
            recs.append("Consider using limit orders instead of market orders")

        if abs(worst_bps) > 50:
            recs.append("Investigate worst-slippage trades for patterns")

        # Find worst symbol
        if sym_stats:
            worst_sym = max(sym_stats.items(), key=lambda x: abs(x[1]["avg_bps"]))
            if abs(worst_sym[1]["avg_bps"]) > self.GOOD_THRESHOLD:
                recs.append(f"Symbol {worst_sym[0]} has high avg slippage ({worst_sym[1]['avg_bps']:.1f} bps) — consider reducing size")

        # Entry vs exit imbalance
        entry_avg = sum(
            s["entry_avg_bps"] for s in sym_stats.values()
        ) / len(sym_stats) if sym_stats else 0
        exit_avg = sum(
            s["exit_avg_bps"] for s in sym_stats.values()
        ) / len(sym_stats) if sym_stats else 0

        if abs(entry_avg) > abs(exit_avg) * 2:
            recs.append("Entry slippage significantly higher than exit — consider better entry timing")

        if not recs:
            recs.append("Execution quality is within acceptable parameters")

        return recs

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _median(values: list[float]) -> float:
        """Calculate median."""
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        if n % 2 == 0:
            return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2
        return sorted_vals[n // 2]

    # ── State Persistence ─────────────────────────────────────────────────────
    def _save_state(self):
        """Save slippage records."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "total_records": len(self._records),
            "records": [asdict(r) for r in self._records[-5000:]],
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        (DATA_DIR / "slippage_state.json").write_text(json.dumps(state, indent=2, default=str))

    def _load_state(self):
        """Load persisted state."""
        path = DATA_DIR / "slippage_state.json"
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text())
            for r in state.get("records", []):
                record = SlippageRecord(**r)
                self._records.append(record)
                sym = record.symbol
                if sym not in self._by_symbol:
                    self._by_symbol[sym] = []
                self._by_symbol[sym].append(record)
        except Exception as e:
            logger.error("Failed to load slippage state: %s", e)

    def get_stats(self) -> dict:
        """Get analyzer statistics."""
        report = self.get_report()
        return {
            "total_records": len(self._records),
            "symbols_tracked": len(self._by_symbol),
            "avg_slippage_bps": report.avg_slippage_bps,
            "worst_slippage_bps": report.worst_slippage_bps,
            "total_slippage_usd": report.total_slippage_usd,
            "quality": report.slippage_quality,
        }
