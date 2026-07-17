"""
Adaptive Alpha Ranking Engine — symbol-level intelligence for smart capital allocation.

Calculates ALPHA_SCORE per symbol from historical trade performance.
Ranks symbols into S/A/B/C tiers.
Drives scanner priority, position sizing, and auto-blacklist/promotion.

DO NOT MODIFY SIGNAL ENGINE.
DO NOT MODIFY SMART MONEY MODULE.
Reads from institutional_v1.db. Writes alpha_ranking.json to bridge.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ──────────────────────────────────────────────────────────────
# PATHS
# ──────────────────────────────────────────────────────────────
_AI_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _AI_ROOT / "data" / "institutional_v1.db"
_BRIDGE_DIR = _AI_ROOT / "data" / "bridge"
_ALPHA_FILE = _BRIDGE_DIR / "alpha_ranking.json"

# ──────────────────────────────────────────────────────────────
# ALPHA SCORE WEIGHTS
# ──────────────────────────────────────────────────────────────
_ALPHA_WEIGHTS = {
    "win_rate":          0.20,
    "profit_factor":     0.25,
    "expectancy":        0.25,
    "avg_r":             0.10,
    "consistency":       0.10,
    "smart_money_acc":   0.05,
    "sample_quality":    0.05,
}

# ──────────────────────────────────────────────────────────────
# TIER THRESHOLDS
# ──────────────────────────────────────────────────────────────
_TIER_THRESHOLDS = {
    "S": 80,   # ALPHA_SCORE >= 80
    "A": 60,   # ALPHA_SCORE >= 60
    "B": 40,   # ALPHA_SCORE >= 40
    "C": 0,    # everything else
}

# Scanner priority per tier
TIER_SCANNER_PRIORITY = {
    "S": 1.00,   # 100% — scan every cycle
    "A": 0.75,   # 75% — scan 3 out of 4 cycles
    "B": 0.50,   # 50% — scan every other cycle
    "C": 0.00,   # ignore — don't scan
}

# Position size multiplier per tier
TIER_POSITION_MULTIPLIER = {
    "S": 3.0,
    "A": 2.0,
    "B": 1.0,
    "C": 0.0,
}

# Auto-blacklist thresholds
BLACKLIST_MIN_TRADES = 50
BLACKLIST_MAX_PF = 1.0
BLACKLIST_MAX_EXPECTANCY = 0.0

# Auto-promotion thresholds
PROMOTION_MIN_TRADES = 20
PROMOTION_MIN_PF = 1.5
PROMOTION_MIN_EXPECTANCY = 0.0

# Minimum trades to be ranked
MIN_TRADES_FOR_RANKING = 5


@dataclass
class SymbolAlpha:
    """Alpha profile for a single symbol."""
    symbol: str
    tier: str = "C"
    alpha_score: float = 0.0

    # Core metrics
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    avg_r: float = 0.0
    avg_hold_minutes: float = 0.0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0

    # Accuracy metrics (from signals table)
    smart_money_accuracy: float = 0.0
    sweep_accuracy: float = 0.0
    fvg_accuracy: float = 0.0
    mss_accuracy: float = 0.0

    # Scanner integration
    scanner_priority: float = 0.0
    position_multiplier: float = 1.0

    # Time-windowed performance
    pnl_today: float = 0.0
    pnl_week: float = 0.0
    pnl_month: float = 0.0
    wr_today: float = 0.0
    wr_week: float = 0.0
    wr_month: float = 0.0
    trades_today: int = 0
    trades_week: int = 0
    trades_month: int = 0

    # Blacklist / promotion status
    is_blacklisted: bool = False
    blacklist_reason: str = ""
    is_promoted: bool = False
    promotion_reason: str = ""

    # Consistency (lower std dev of returns = more consistent)
    consistency_score: float = 0.0
    sample_quality: float = 0.0


@dataclass
class AlphaRankingSnapshot:
    """Complete snapshot of all symbol rankings."""
    timestamp: float = 0
    total_symbols: int = 0
    tier_counts: Dict[str, int] = field(default_factory=dict)
    symbols: List[SymbolAlpha] = field(default_factory=list)
    blacklisted: List[Dict] = field(default_factory=list)
    promoted: List[Dict] = field(default_factory=list)
    best_today: List[Dict] = field(default_factory=list)
    best_week: List[Dict] = field(default_factory=list)
    best_month: List[Dict] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════
# DATABASE MIGRATION
# ══════════════════════════════════════════════════════════════
def migrate_alpha_db() -> None:
    """Add alpha_score and tier columns to positions table if missing."""
    if not _DB_PATH.exists():
        return

    db = sqlite3.connect(str(_DB_PATH), timeout=10)
    cur = db.cursor()

    cur.execute("PRAGMA table_info(positions)")
    existing = {c[1] for c in cur.fetchall()}

    migrations = [
        ("alpha_score", "REAL", "0"),
        ("alpha_tier",  "TEXT", "'C'"),
    ]

    for col_name, col_type, default in migrations:
        if col_name not in existing:
            try:
                cur.execute(f"ALTER TABLE positions ADD COLUMN {col_name} {col_type} DEFAULT {default}")
            except sqlite3.OperationalError:
                pass

    db.commit()
    db.close()


# ══════════════════════════════════════════════════════════════
# ALPHA RANKING ENGINE
# ══════════════════════════════════════════════════════════════
class AlphaRankingEngine:
    """
    Calculates ALPHA_SCORE per symbol and maintains tier rankings.
    
    ALPHA_SCORE formula (0-100):
        WR (0-100) * 0.20 + PF_score (0-100) * 0.25 + expectancy_score (0-100) * 0.25
        + R_score (0-100) * 0.10 + consistency (0-100) * 0.10
        + SM_accuracy (0-100) * 0.05 + sample_quality (0-100) * 0.05
    """

    def __init__(self) -> None:
        self._profiles: Dict[str, SymbolAlpha] = {}
        self._blacklist: Dict[str, str] = {}  # symbol -> reason
        self._last_update: float = 0
        self._update_interval: float = 300  # 5 minutes

    async def initialize(self) -> None:
        """Initialize the alpha ranking engine."""
        migrate_alpha_db()
        self.update()
        print(f"AlphaRankingEngine ready: {len(self._profiles)} symbols ranked")

    def update(self) -> AlphaRankingSnapshot:
        """Recalculate all rankings from database. Returns full snapshot."""
        now = time.time()
        if now - self._last_update < self._update_interval:
            return self._build_snapshot()
        self._last_update = now

        if not _DB_PATH.exists():
            return self._build_snapshot()

        db = sqlite3.connect(str(_DB_PATH), timeout=10)
        db.row_factory = sqlite3.Row
        cur = db.cursor()

        # ── Load all closed trades grouped by symbol ──
        cur.execute("""
            SELECT symbol, side, pnl, entry_price, quantity, leverage,
                   stop_loss, take_profit, opened_at, closed_at, hold_minutes,
                   confidence, regime, institutional_score, risk_reward,
                   strategy_version, exit_reason
            FROM positions
            WHERE status = 'closed' AND pnl IS NOT NULL
            ORDER BY closed_at ASC
        """)
        all_trades = cur.fetchall()
        db.close()

        if not all_trades:
            return self._build_snapshot()

        # Group by symbol
        sym_trades: Dict[str, list] = {}
        for t in all_trades:
            sym = t["symbol"]
            if sym not in sym_trades:
                sym_trades[sym] = []
            sym_trades[sym].append(dict(t))

        now_dt = datetime.now(timezone.utc)
        today_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        week_start = (now_dt - timedelta(days=7)).timestamp()
        month_start = (now_dt - timedelta(days=30)).timestamp()

        # ── Calculate alpha score for each symbol ──
        self._profiles.clear()
        self._blacklist.clear()

        for sym, trades in sym_trades.items():
            profile = self._calc_symbol_alpha(sym, trades, today_start, week_start, month_start)
            self._profiles[sym] = profile

            # Auto-blacklist check
            if profile.total_trades >= BLACKLIST_MIN_TRADES:
                if profile.profit_factor < BLACKLIST_MAX_PF:
                    profile.is_blacklisted = True
                    profile.blacklist_reason = f"PF {profile.profit_factor:.2f} < {BLACKLIST_MAX_PF}"
                    self._blacklist[sym] = profile.blacklist_reason
                elif profile.expectancy < BLACKLIST_MAX_EXPECTANCY:
                    profile.is_blacklisted = True
                    profile.blacklist_reason = f"Exp ${profile.expectancy:.2f} < $0"
                    self._blacklist[sym] = profile.blacklist_reason

            # Auto-promotion check
            if (profile.total_trades >= PROMOTION_MIN_TRADES
                    and profile.profit_factor >= PROMOTION_MIN_PF
                    and profile.expectancy >= PROMOTION_MIN_EXPECTANCY
                    and not profile.is_blacklisted):
                profile.is_promoted = True
                profile.promotion_reason = f"PF {profile.profit_factor:.2f} >= {PROMOTION_MIN_PF}, Exp ${profile.expectancy:.2f} >= $0"

        # ── Assign tiers ──
        self._assign_tiers()

        # ── Write to bridge ──
        self._write_bridge()

        return self._build_snapshot()

    def _calc_symbol_alpha(
        self, symbol: str, trades: List[Dict],
        today_start: float, week_start: float, month_start: float,
    ) -> SymbolAlpha:
        """Calculate comprehensive alpha profile for a symbol."""
        pnls = np.array([t["pnl"] for t in trades])
        n = len(pnls)

        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]

        win_rate = len(wins) / n * 100 if n > 0 else 0
        profit_factor = abs(wins.sum() / losses.sum()) if len(losses) > 0 and losses.sum() != 0 else 999.0
        expectancy = float(pnls.mean()) if n > 0 else 0
        total_pnl = float(pnls.sum())

        # Average R-multiple
        r_multiples = []
        for t in trades:
            entry = t.get("entry_price", 0)
            sl = t.get("stop_loss", 0)
            pnl = t.get("pnl", 0)
            qty = t.get("quantity", 0)
            risk = abs(entry - sl) if entry and sl else abs(entry * 0.009)
            if risk > 0 and qty > 0:
                r = pnl / (risk * qty)
                r_multiples.append(r)
        avg_r = float(np.mean(r_multiples)) if r_multiples else 0

        # Average hold time
        hold_minutes = [t.get("hold_minutes", 0) for t in trades if t.get("hold_minutes")]
        avg_hold = float(np.mean(hold_minutes)) if hold_minutes else 0

        # Max drawdown
        cum = np.cumsum(pnls)
        peak = np.maximum.accumulate(cum)
        dd = cum - peak
        max_dd = float(abs(dd.min())) if len(dd) > 0 else 0

        # Consistency score (inverse of return std dev, normalized 0-100)
        if n >= 3 and pnls.std() > 0:
            cv = abs(pnls.mean()) / pnls.std()  # coefficient of variation
            consistency = min(cv * 50, 100)
        else:
            consistency = 0

        # Sample quality (more trades = higher quality, capped at 100)
        sample_quality = min(n / 50 * 100, 100)

        # Time-windowed performance
        trades_today = [t for t in trades if (t.get("closed_at") or 0) >= today_start]
        trades_week = [t for t in trades if (t.get("closed_at") or 0) >= week_start]
        trades_month = [t for t in trades if (t.get("closed_at") or 0) >= month_start]

        pnl_today = sum(t["pnl"] for t in trades_today)
        pnl_week = sum(t["pnl"] for t in trades_week)
        pnl_month = sum(t["pnl"] for t in trades_month)

        wr_today = (sum(1 for t in trades_today if t["pnl"] > 0) / len(trades_today) * 100) if trades_today else 0
        wr_week = (sum(1 for t in trades_week if t["pnl"] > 0) / len(trades_week) * 100) if trades_week else 0
        wr_month = (sum(1 for t in trades_month if t["pnl"] > 0) / len(trades_month) * 100) if trades_month else 0

        # ── Signal accuracy metrics (from signals table) ──
        sm_acc, sweep_acc, fvg_acc, mss_acc = self._calc_signal_accuracy(symbol)

        # ── ALPHA SCORE CALCULATION ──
        alpha_score = self._calc_alpha_score(
            win_rate, profit_factor, expectancy, avg_r,
            consistency, sm_acc, sample_quality,
        )

        return SymbolAlpha(
            symbol=symbol,
            alpha_score=round(alpha_score, 1),
            total_trades=n,
            win_rate=round(win_rate, 1),
            profit_factor=round(profit_factor, 2),
            expectancy=round(expectancy, 2),
            avg_r=round(avg_r, 2),
            avg_hold_minutes=round(avg_hold, 1),
            total_pnl=round(total_pnl, 2),
            max_drawdown=round(max_dd, 2),
            smart_money_accuracy=round(sm_acc, 1),
            sweep_accuracy=round(sweep_acc, 1),
            fvg_accuracy=round(fvg_acc, 1),
            mss_accuracy=round(mss_acc, 1),
            consistency_score=round(consistency, 1),
            sample_quality=round(sample_quality, 1),
            pnl_today=round(pnl_today, 2),
            pnl_week=round(pnl_week, 2),
            pnl_month=round(pnl_month, 2),
            wr_today=round(wr_today, 1),
            wr_week=round(wr_week, 1),
            wr_month=round(wr_month, 1),
            trades_today=len(trades_today),
            trades_week=len(trades_week),
            trades_month=len(trades_month),
        )

    def _calc_alpha_score(
        self, win_rate: float, profit_factor: float,
        expectancy: float, avg_r: float,
        consistency: float, sm_accuracy: float,
        sample_quality: float,
    ) -> float:
        """
        Calculate ALPHA_SCORE (0-100).
        
        Each component is normalized to 0-100:
        - win_rate: already 0-100
        - profit_factor: PF=1 → 50, PF=2 → 100, PF=0 → 0 (linear 0-2 range)
        - expectancy: normalize by $50 scale ($-50 → 0, $0 → 50, $50 → 100)
        - avg_r: normalize by 2R scale (-1R → 25, 0R → 50, 1R → 75, 2R → 100)
        - consistency: already 0-100
        - sm_accuracy: already 0-100
        - sample_quality: already 0-100
        """
        # Normalize each component to 0-100
        wr_score = max(0, min(win_rate, 100))

        # PF: 0→0, 1→50, 2→100 (capped at 2 for scoring)
        pf_score = max(0, min(profit_factor / 2 * 100, 100))

        # Expectancy: -50→0, 0→50, +50→100
        exp_score = max(0, min((expectancy + 50) / 100 * 100, 100))

        # Avg R: -1→25, 0→50, 1→75, 2→100
        r_score = max(0, min((avg_r + 1) / 3 * 100, 100))

        # Weighted sum
        alpha = (
            wr_score * _ALPHA_WEIGHTS["win_rate"]
            + pf_score * _ALPHA_WEIGHTS["profit_factor"]
            + exp_score * _ALPHA_WEIGHTS["expectancy"]
            + r_score * _ALPHA_WEIGHTS["avg_r"]
            + consistency * _ALPHA_WEIGHTS["consistency"]
            + sm_accuracy * _ALPHA_WEIGHTS["smart_money_acc"]
            + sample_quality * _ALPHA_WEIGHTS["sample_quality"]
        )

        return max(0, min(alpha, 100))

    def _calc_signal_accuracy(self, symbol: str) -> Tuple[float, float, float, float]:
        """Calculate signal accuracy metrics for a symbol from the signals table.
        Returns (smart_money_accuracy, sweep_accuracy, fvg_accuracy, mss_accuracy).
        Each is 0-100 based on how often the signal's prediction matched actual PnL.
        """
        if not _DB_PATH.exists():
            return 0, 0, 0, 0

        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            db.row_factory = sqlite3.Row
            cur = db.cursor()

            # Get signals for this symbol that became trades
            cur.execute("""
                SELECT s.sweep_score, s.absorption_score, s.institutional_score,
                       s.confidence, s.side, p.pnl
                FROM signals s
                JOIN positions p ON s.id = p.signal_id
                WHERE s.symbol = ? AND p.pnl IS NOT NULL AND p.status = 'closed'
                ORDER BY s.timestamp DESC
                LIMIT 100
            """, (symbol,))
            rows = cur.fetchall()
            db.close()

            if not rows:
                return 0, 0, 0, 0

            # Smart Money accuracy: signals with high SM score that were winners
            sm_signals = [r for r in rows if (r["absorption_score"] or 0) > 0.5]
            sm_correct = sum(1 for r in sm_signals if r["pnl"] > 0) if sm_signals else 0
            sm_acc = (sm_correct / len(sm_signals) * 100) if sm_signals else 0

            # Sweep accuracy: signals with high sweep score that were winners
            sweep_signals = [r for r in rows if (r["sweep_score"] or 0) > 0.5]
            sweep_correct = sum(1 for r in sweep_signals if r["pnl"] > 0) if sweep_signals else 0
            sweep_acc = (sweep_correct / len(sweep_signals) * 100) if sweep_signals else 0

            # For FVG and MSS, we use institutional_score as proxy
            inst_signals = [r for r in rows if (r["institutional_score"] or 0) > 60]
            inst_correct = sum(1 for r in inst_signals if r["pnl"] > 0) if inst_signals else 0
            fvg_acc = (inst_correct / len(inst_signals) * 100) if inst_signals else 0
            mss_acc = fvg_acc  # Same proxy for now

            return sm_acc, sweep_acc, fvg_acc, mss_acc

        except Exception:
            return 0, 0, 0, 0

    def _assign_tiers(self) -> None:
        """Assign S/A/B/C tiers based on ALPHA_SCORE thresholds."""
        for profile in self._profiles.values():
            # Blacklisted symbols always get C tier
            if profile.is_blacklisted:
                profile.tier = "C"
                profile.scanner_priority = 0.0
                profile.position_multiplier = 0.0
                continue

            score = profile.alpha_score
            if score >= _TIER_THRESHOLDS["S"]:
                profile.tier = "S"
            elif score >= _TIER_THRESHOLDS["A"]:
                profile.tier = "A"
            elif score >= _TIER_THRESHOLDS["B"]:
                profile.tier = "B"
            else:
                profile.tier = "C"

            profile.scanner_priority = TIER_SCANNER_PRIORITY[profile.tier]
            profile.position_multiplier = TIER_POSITION_MULTIPLIER[profile.tier]

    def _build_snapshot(self) -> AlphaRankingSnapshot:
        """Build a complete ranking snapshot."""
        profiles = sorted(self._profiles.values(), key=lambda p: p.alpha_score, reverse=True)

        tier_counts = {"S": 0, "A": 0, "B": 0, "C": 0}
        for p in profiles:
            tier_counts[p.tier] = tier_counts.get(p.tier, 0) + 1

        # Best symbols by time window
        best_today = sorted(
            [p for p in profiles if p.trades_today > 0],
            key=lambda p: p.pnl_today, reverse=True,
        )[:10]
        best_week = sorted(
            [p for p in profiles if p.trades_week > 0],
            key=lambda p: p.pnl_week, reverse=True,
        )[:10]
        best_month = sorted(
            [p for p in profiles if p.trades_month > 0],
            key=lambda p: p.pnl_month, reverse=True,
        )[:10]

        blacklisted = [
            {"symbol": p.symbol, "reason": p.blacklist_reason,
             "trades": p.total_trades, "pf": p.profit_factor, "pnl": p.total_pnl}
            for p in profiles if p.is_blacklisted
        ]
        promoted = [
            {"symbol": p.symbol, "reason": p.promotion_reason,
             "trades": p.total_trades, "pf": p.profit_factor, "pnl": p.total_pnl}
            for p in profiles if p.is_promoted
        ]

        return AlphaRankingSnapshot(
            timestamp=time.time(),
            total_symbols=len(profiles),
            tier_counts=tier_counts,
            symbols=profiles,
            blacklisted=blacklisted,
            promoted=promoted,
            best_today=_snap_to_dicts(best_today),
            best_week=_snap_to_dicts(best_week),
            best_month=_snap_to_dicts(best_month),
        )

    def _write_bridge(self) -> None:
        """Write ranking data to bridge JSON for dashboard."""
        snapshot = self._build_snapshot()
        data = {
            "timestamp": snapshot.timestamp,
            "total_symbols": snapshot.total_symbols,
            "tier_counts": snapshot.tier_counts,
            "blacklisted": snapshot.blacklisted,
            "promoted": snapshot.promoted,
            "best_today": snapshot.best_today,
            "best_week": snapshot.best_week,
            "best_month": snapshot.best_month,
            "symbols": [
                {
                    "symbol": p.symbol,
                    "tier": p.tier,
                    "alpha_score": p.alpha_score,
                    "total_trades": p.total_trades,
                    "win_rate": p.win_rate,
                    "profit_factor": p.profit_factor,
                    "expectancy": p.expectancy,
                    "avg_r": p.avg_r,
                    "avg_hold_minutes": p.avg_hold_minutes,
                    "total_pnl": p.total_pnl,
                    "max_drawdown": p.max_drawdown,
                    "sm_accuracy": p.smart_money_accuracy,
                    "sweep_accuracy": p.sweep_accuracy,
                    "fvg_accuracy": p.fvg_accuracy,
                    "mss_accuracy": p.mss_accuracy,
                    "scanner_priority": p.scanner_priority,
                    "position_multiplier": p.position_multiplier,
                    "pnl_today": p.pnl_today,
                    "pnl_week": p.pnl_week,
                    "pnl_month": p.pnl_month,
                    "wr_today": p.wr_today,
                    "wr_week": p.wr_week,
                    "wr_month": p.wr_month,
                    "trades_today": p.trades_today,
                    "trades_week": p.trades_week,
                    "trades_month": p.trades_month,
                    "is_blacklisted": p.is_blacklisted,
                    "blacklist_reason": p.blacklist_reason,
                    "is_promoted": p.is_promoted,
                    "promotion_reason": p.promotion_reason,
                    "consistency": p.consistency_score,
                    "sample_quality": p.sample_quality,
                }
                for p in snapshot.symbols
            ],
        }

        _BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _ALPHA_FILE.with_suffix(".tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(data, f, default=str, indent=2)
                f.flush()
                import os
                os.fsync(f.fileno())
            import os
            os.replace(str(tmp), str(_ALPHA_FILE))
        except Exception as e:
            print(f"Alpha ranking bridge write error: {e}")

    # ── Public API for engine integration ──

    def get_tier(self, symbol: str) -> str:
        """Get the tier for a symbol."""
        profile = self._profiles.get(symbol)
        return profile.tier if profile else "C"

    def get_position_multiplier(self, symbol: str) -> float:
        """Get the position size multiplier for a symbol."""
        profile = self._profiles.get(symbol)
        return profile.position_multiplier if profile else 1.0

    def get_scanner_priority(self, symbol: str) -> float:
        """Get the scanner priority (0-1) for a symbol."""
        profile = self._profiles.get(symbol)
        return profile.scanner_priority if profile else 0.0

    def should_scan(self, symbol: str, cycle_count: int = 0) -> bool:
        """Determine if a symbol should be scanned this cycle.
        Uses modular cycling based on scanner priority."""
        priority = self.get_scanner_priority(symbol)
        if priority >= 1.0:
            return True
        if priority <= 0.0:
            return False
        return (cycle_count % max(1, int(1 / priority))) == 0

    def is_blacklisted(self, symbol: str) -> bool:
        """Check if a symbol is blacklisted."""
        profile = self._profiles.get(symbol)
        return profile.is_blacklisted if profile else False

    def get_profile(self, symbol: str) -> Optional[SymbolAlpha]:
        """Get the full alpha profile for a symbol."""
        return self._profiles.get(symbol)

    def get_all_profiles(self) -> List[SymbolAlpha]:
        """Get all profiles sorted by alpha score descending."""
        return sorted(self._profiles.values(), key=lambda p: p.alpha_score, reverse=True)

    def get_tier_symbols(self, tier: str) -> List[str]:
        """Get all symbols in a given tier."""
        return [p.symbol for p in self._profiles.values() if p.tier == tier]


def _snap_to_dicts(profiles: List[SymbolAlpha]) -> List[Dict]:
    """Convert SymbolAlpha list to dict list for JSON serialization."""
    return [
        {
            "symbol": p.symbol,
            "tier": p.tier,
            "alpha_score": p.alpha_score,
            "total_pnl": p.total_pnl,
            "win_rate": p.win_rate,
            "profit_factor": p.profit_factor,
            "expectancy": p.expectancy,
            "total_trades": p.total_trades,
        }
        for p in profiles
    ]


# ══════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import asyncio

    async def main():
        engine = AlphaRankingEngine()
        await engine.initialize()

        snapshot = engine.update()

        print(f"\n{'='*60}")
        print(f"  ADAPTIVE ALPHA RANKING — {snapshot.total_symbols} symbols")
        print(f"{'='*60}")
        print(f"\n  Tier Distribution: {snapshot.tier_counts}")

        print(f"\n  {'Symbol':<16} {'Tier':<5} {'Score':<7} {'Trades':<7} {'WR%':<7} {'PF':<7} {'Exp$':<10} {'Total PnL':<12}")
        print(f"  {'-'*78}")
        for p in snapshot.symbols[:20]:
            tier_icon = {"S": "🟢", "A": "🔵", "B": "🟡", "C": "⚫"}.get(p.tier, "⚫")
            print(f"  {p.symbol:<16} {tier_icon}{p.tier:<4} {p.alpha_score:<7.1f} {p.total_trades:<7} {p.win_rate:<7.1f} {p.profit_factor:<7.2f} ${p.expectancy:<9.2f} ${p.total_pnl:<11.2f}")

        if snapshot.blacklisted:
            print(f"\n  ⛔ Auto-Blacklisted ({len(snapshot.blacklisted)}):")
            for b in snapshot.blacklisted:
                print(f"    {b['symbol']}: {b['reason']} ({b['trades']} trades, PF={b['pf']:.2f})")

        if snapshot.promoted:
            print(f"\n  ⭐ Auto-Promoted ({len(snapshot.promoted)}):")
            for p in snapshot.promoted:
                print(f"    {p['symbol']}: {p['reason']} ({p['trades']} trades, PF={p['pf']:.2f})")

        print(f"\n  🏆 Best Today:")
        for b in snapshot.best_today[:5]:
            print(f"    {b['symbol']}: ${b['total_pnl']:+.2f} PnL, {b['win_rate']}% WR")

        print(f"\n  📅 Best Week:")
        for b in snapshot.best_week[:5]:
            print(f"    {b['symbol']}: ${b['total_pnl']:+.2f} PnL, {b['win_rate']}% WR")

        print(f"\n  📆 Best Month:")
        for b in snapshot.best_month[:5]:
            print(f"    {b['symbol']}: ${b['total_pnl']:+.2f} PnL, {b['win_rate']}% WR")

        # Scanner priority summary
        s_tier = engine.get_tier_symbols("S")
        a_tier = engine.get_tier_symbols("A")
        print(f"\n  Scanner Priority:")
        print(f"    S Tier ({len(s_tier)}): {', '.join(s_tier[:10])} — 100% scan")
        print(f"    A Tier ({len(a_tier)}): {', '.join(a_tier[:10])} — 75% scan")
        print(f"    Position sizing: S=3x, A=2x, B=1x, C=0x")

        print(f"\n{'='*60}")

    asyncio.run(main())
