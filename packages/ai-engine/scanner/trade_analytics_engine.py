"""
Trade Analytics Engine — Versioned Analytics, Hold Time Optimizer, Exit Optimizer,
Confidence Accuracy, Symbol Expectancy, Session Analytics, Live Performance, Auto Recommendations.

DO NOT MODIFY SIGNAL ENGINE.
DO NOT MODIFY SMART MONEY MODULE.
Analytics-only module that reads from institutional_v1.db and provides enhanced metrics.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ──────────────────────────────────────────────────────────────
# PATHS
# ──────────────────────────────────────────────────────────────
_AI_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _AI_ROOT / "data" / "institutional_v1.db"

# Guard: migrate_db() runs only once per process lifetime
_migrated: bool = False

# ──────────────────────────────────────────────────────────────
# VERSION BOUNDARIES (timestamp-based classification)
# Trades opened before these dates get the corresponding version label.
# Updated as new versions are deployed.
# ──────────────────────────────────────────────────────────────
_VERSION_BOUNDARIES: List[Tuple[str, float]] = [
    # (version_label, min_opened_at_timestamp)
    # Trades BEFORE the next boundary fall into the previous version
    # Timeline: Jun 4-5 = legacy, Jun 6-7 = inst_v1, Jun 8 = inst_v2, Jun 9+ = current
    ("current",   1780963200),   # Jun 9 00:00 UTC 2026
    ("inst_v2",   1780876800),   # Jun 8 00:00 UTC 2026
    ("inst_v1",   1780704000),   # Jun 6 00:00 UTC 2026
    ("legacy",    0),            # everything before inst_v1
]

# Session boundaries (UTC hours)
_SESSION_MAP = {
    "asia":      (0, 8),     # 00:00 - 08:00 UTC
    "london":    (7, 15),    # 07:00 - 15:00 UTC
    "new_york":  (13, 21),   # 13:00 - 21:00 UTC
    "off_hours": (21, 24),   # 21:00 - 24:00 UTC
}

# Hold-time buckets
_HOLD_BUCKETS = [
    ("⚡ Scalp (<5m)",    0,    5),
    ("🔄 Quick (5-30m)",  5,   30),
    ("📊 Short (30-60m)", 30,  60),
    ("📈 Medium (1-2h)",  60, 120),
    ("🏗️ Swing (2-4h)",  120, 240),
    ("🦅 Position (4h+)", 240, 999999),
]

# Confidence buckets
_CONF_BUCKETS = [
    ("40-50%", 0.40, 0.50),
    ("50-60%", 0.50, 0.60),
    ("60-70%", 0.60, 0.70),
    ("70-80%", 0.70, 0.80),
    ("80-90%", 0.80, 0.90),
    ("90-100%", 0.90, 1.01),
]


# ══════════════════════════════════════════════════════════════
# DB MIGRATION — add missing columns if they don't exist
# ══════════════════════════════════════════════════════════════
def migrate_db() -> None:
    """Add analytics columns to positions table if missing. Runs once per process."""
    global _migrated
    if _migrated:
        return
    if not _DB_PATH.exists():
        return

    db = sqlite3.connect(str(_DB_PATH), timeout=10)
    cur = db.cursor()

    # Get existing columns
    cur.execute("PRAGMA table_info(positions)")
    existing = {c[1] for c in cur.fetchall()}

    migrations = [
        ("exit_reason",         "TEXT",    "'unknown'"),
        ("strategy_version",    "TEXT",    "'legacy'"),
        ("confidence",          "REAL",    "0"),
        ("regime",              "TEXT",    "'unknown'"),
        ("institutional_score", "REAL",    "0"),
        ("risk_reward",         "REAL",    "0"),
        ("hold_minutes",        "REAL",    "0"),
        ("session",             "TEXT",    "'off_hours'"),
        ("mfe_pct",             "REAL",    "0"),
        ("mae_pct",             "REAL",    "0"),
    ]

    for col_name, col_type, default in migrations:
        if col_name not in existing:
            try:
                cur.execute(f"ALTER TABLE positions ADD COLUMN {col_name} {col_type} DEFAULT {default}")
                print(f"  ✅ Added column: {col_name}")
            except sqlite3.OperationalError:
                pass  # already exists

    db.commit()

    # Backfill from signals table where possible
    _backfill_from_signals(db)
    _backfill_version_from_timestamp(db)
    _backfill_session(db)
    _backfill_hold_minutes(db)

    db.close()
    _migrated = True


def _backfill_from_signals(db: sqlite3.Connection) -> None:
    """Join with signals table to fill confidence, regime, institutional_score, risk_reward."""
    cur = db.cursor()

    # Check if signals table has the data we need
    cur.execute("PRAGMA table_info(signals)")
    sig_cols = {c[1] for c in cur.fetchall()}

    needed = {"confidence", "market_regime", "institutional_score", "risk_reward"}
    if not needed.issubset(sig_cols):
        return

    # Only update rows where the signal table has actual data to provide
    cur.execute("""
        UPDATE positions
        SET
            confidence = COALESCE(
                (SELECT s.confidence FROM signals s WHERE s.id = positions.signal_id),
                positions.confidence
            ),
            regime = COALESCE(
                (SELECT s.market_regime FROM signals s WHERE s.id = positions.signal_id),
                positions.regime
            ),
            institutional_score = COALESCE(
                (SELECT s.institutional_score FROM signals s WHERE s.id = positions.signal_id),
                positions.institutional_score
            ),
            risk_reward = COALESCE(
                (SELECT s.risk_reward FROM signals s WHERE s.id = positions.signal_id),
                positions.risk_reward
            )
        WHERE positions.confidence = 0
          AND positions.pnl IS NOT NULL
          AND positions.status = 'closed'
          AND positions.signal_id IS NOT NULL
          AND EXISTS (SELECT 1 FROM signals s WHERE s.id = positions.signal_id AND s.confidence > 0)
    """)
    updated = cur.rowcount
    db.commit()
    if updated > 0:
        print(f"  ✅ Backfilled signal data for {updated} trades")


def _backfill_version_from_timestamp(db: sqlite3.Connection) -> None:
    """Classify trades into strategy versions based on opened_at timestamp."""
    cur = db.cursor()

    cur.execute("SELECT id, opened_at FROM positions WHERE strategy_version = 'legacy' AND opened_at IS NOT NULL AND pnl IS NOT NULL AND status = 'closed'")
    rows = cur.fetchall()

    updates = []
    for trade_id, opened_at in rows:
        version = _classify_version(opened_at)
        if version != "legacy":
            updates.append((version, trade_id))

    if updates:
        cur.executemany("UPDATE positions SET strategy_version = ? WHERE id = ?", updates)
        db.commit()
        print(f"  ✅ Versioned {len(updates)} trades")


def _backfill_session(db: sqlite3.Connection) -> None:
    """Fill session field from opened_at hour."""
    cur = db.cursor()
    cur.execute("SELECT id, opened_at FROM positions WHERE session = 'off_hours' AND opened_at IS NOT NULL AND pnl IS NOT NULL AND status = 'closed'")
    rows = cur.fetchall()

    updates = []
    for trade_id, opened_at in rows:
        dt = datetime.fromtimestamp(opened_at, tz=timezone.utc)
        session = _classify_session(dt.hour)
        updates.append((session, trade_id))

    if updates:
        cur.executemany("UPDATE positions SET session = ? WHERE id = ?", updates)
        db.commit()
        print(f"  ✅ Session-classified {len(updates)} trades")


def _backfill_hold_minutes(db: sqlite3.Connection) -> None:
    """Compute hold_minutes from opened_at and closed_at."""
    cur = db.cursor()
    cur.execute("""
        SELECT id, opened_at, closed_at
        FROM positions
        WHERE hold_minutes = 0 AND opened_at IS NOT NULL AND closed_at IS NOT NULL AND pnl IS NOT NULL AND status = 'closed'
    """)
    rows = cur.fetchall()

    updates = []
    for trade_id, opened, closed in rows:
        hold = (closed - opened) / 60.0 if closed and opened else 0
        updates.append((round(hold, 1), trade_id))

    if updates:
        cur.executemany("UPDATE positions SET hold_minutes = ? WHERE id = ?", updates)
        db.commit()
        print(f"  ✅ Computed hold_minutes for {len(updates)} trades")


# ══════════════════════════════════════════════════════════════
# CLASSIFICATION HELPERS
# ══════════════════════════════════════════════════════════════
def _classify_version(opened_at: float) -> str:
    """Classify trade into strategy version by timestamp."""
    for label, min_ts in _VERSION_BOUNDARIES:
        if opened_at >= min_ts:
            return label
    return "legacy"


def _classify_session(hour: int) -> str:
    """Classify UTC hour into trading session."""
    for session, (start, end) in _SESSION_MAP.items():
        if start <= hour < end:
            return session
    return "off_hours"


def _hold_bucket(minutes: float) -> str:
    """Bucket hold time into named zone."""
    for name, lo, hi in _HOLD_BUCKETS:
        if lo <= minutes < hi:
            return name
    return "🦅 Position (4h+)"


# ══════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════
@dataclass
class TradeRecord:
    """Lightweight trade record for analytics."""
    id: int
    symbol: str
    side: str
    entry_price: float
    pnl: float
    fees: float
    hold_minutes: float
    opened_at: float
    closed_at: float
    exit_reason: str
    strategy_version: str
    confidence: float
    regime: str
    institutional_score: float
    risk_reward: float
    session: str
    mfe_pct: float
    mae_pct: float
    leverage: int = 10


def load_trades(since_days: Optional[int] = None) -> List[TradeRecord]:
    """Load closed trades with analytics fields.

    Args:
        since_days: If set, only load trades opened within the last N days.
                    If None, load ALL trades (including archive).
    """
    if not _DB_PATH.exists():
        return []

    # Run migration first
    migrate_db()

    db = sqlite3.connect(str(_DB_PATH), timeout=10)
    db.row_factory = sqlite3.Row
    cur = db.cursor()

    if since_days is not None:
        import time
        cutoff = time.time() - (since_days * 86400)
        cur.execute("""
            SELECT id, symbol, side, entry_price, pnl, fees, hold_minutes,
                   opened_at, closed_at, exit_reason, strategy_version,
                   confidence, regime, institutional_score, risk_reward,
                   session, mfe_pct, mae_pct, leverage
            FROM positions
            WHERE pnl IS NOT NULL AND status = 'closed'
              AND opened_at >= ?
            UNION ALL
            SELECT id, symbol, side, entry_price, pnl, fees, hold_minutes,
                   opened_at, closed_at, exit_reason, strategy_version,
                   confidence, regime, institutional_score, risk_reward,
                   session, mfe_pct, mae_pct, leverage
            FROM positions_archive
            WHERE pnl IS NOT NULL
              AND opened_at >= ?
            ORDER BY closed_at ASC
        """, (cutoff, cutoff))
    else:
        cur.execute("""
            SELECT id, symbol, side, entry_price, pnl, fees, hold_minutes,
                   opened_at, closed_at, exit_reason, strategy_version,
                   confidence, regime, institutional_score, risk_reward,
                   session, mfe_pct, mae_pct, leverage
            FROM positions
            WHERE pnl IS NOT NULL AND status = 'closed'
            UNION ALL
            SELECT id, symbol, side, entry_price, pnl, fees, hold_minutes,
                   opened_at, closed_at, exit_reason, strategy_version,
                   confidence, regime, institutional_score, risk_reward,
                   session, mfe_pct, mae_pct, leverage
            FROM positions_archive
            WHERE pnl IS NOT NULL
            ORDER BY closed_at ASC
        """)

    trades = []
    for r in cur.fetchall():
        trades.append(TradeRecord(
            id=r["id"],
            symbol=r["symbol"] or "UNKNOWN",
            side=r["side"] or "LONG",
            entry_price=float(r["entry_price"] or 0),
            pnl=float(r["pnl"] or 0),
            fees=float(r["fees"] or 0),
            hold_minutes=float(r["hold_minutes"] or 0),
            opened_at=float(r["opened_at"] or 0),
            closed_at=float(r["closed_at"] or 0),
            exit_reason=r["exit_reason"] or "unknown",
            strategy_version=r["strategy_version"] or "legacy",
            confidence=float(r["confidence"] or 0),
            regime=r["regime"] or "unknown",
            institutional_score=float(r["institutional_score"] or 0),
            risk_reward=float(r["risk_reward"] or 0),
            session=r["session"] or "off_hours",
            mfe_pct=float(r["mfe_pct"] or 0),
            mae_pct=float(r["mae_pct"] or 0),
            leverage=int(r["leverage"] or 10),
        ))

    db.close()
    return trades


# ══════════════════════════════════════════════════════════════
# ANALYTICS FUNCTIONS
# ══════════════════════════════════════════════════════════════

def _compute_group_stats(trades: List[TradeRecord]) -> Dict[str, Any]:
    """Compute standard stats for a group of trades."""
    if not trades:
        return {
            "trades": 0, "win_rate": 0, "profit_factor": 0,
            "total_pnl": 0, "expectancy": 0, "avg_win": 0,
            "avg_loss": 0, "sharpe": 0, "max_dd_pct": 0,
        }

    pnls = np.array([t.pnl for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]

    n = len(pnls)
    wr = len(wins) / n * 100 if n > 0 else 0
    pf = abs(wins.sum() / losses.sum()) if len(losses) > 0 and losses.sum() != 0 else 999.0
    expectancy = pnls.mean()
    avg_win = wins.mean() if len(wins) > 0 else 0
    avg_loss = losses.mean() if len(losses) > 0 else 0

    # Sharpe
    sharpe = 0.0
    if n >= 2 and pnls.std() > 0:
        sharpe = float(pnls.mean() / pnls.std() * np.sqrt(min(n, 252)))

    # Max drawdown
    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    max_dd = abs(dd.min())
    peak_equity = 10000 + peak.max()
    max_dd_pct = (max_dd / peak_equity * 100) if peak_equity > 0 else 0

    return {
        "trades": n,
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "total_pnl": round(float(pnls.sum()), 2),
        "expectancy": round(float(expectancy), 2),
        "avg_win": round(float(avg_win), 2),
        "avg_loss": round(float(avg_loss), 2),
        "sharpe": round(sharpe, 2),
        "max_dd_pct": round(max_dd_pct, 2),
    }


# ──────────────────────────────────────────────────────────────
# 1. VERSIONED ANALYTICS
# ──────────────────────────────────────────────────────────────
def versioned_analytics(trades: List[TradeRecord]) -> List[Dict[str, Any]]:
    """Group trades by strategy_version and compute metrics for each."""
    version_order = ["legacy", "inst_v1", "inst_v2", "current"]
    version_labels = {
        "legacy": "🪦 Legacy Strategy",
        "inst_v1": "🏛️ Institutional V1",
        "inst_v2": "⚡ Institutional V2",
        "current": "🚀 Current Engine",
    }

    results = []
    for ver in version_order:
        ver_trades = [t for t in trades if t.strategy_version == ver]
        if not ver_trades:
            continue
        stats = _compute_group_stats(ver_trades)
        stats["version"] = ver
        stats["label"] = version_labels.get(ver, ver)
        results.append(stats)

    return results


# ──────────────────────────────────────────────────────────────
# 2. HOLD TIME OPTIMIZER
# ──────────────────────────────────────────────────────────────
def hold_time_optimizer(trades: List[TradeRecord]) -> List[Dict[str, Any]]:
    """Analyze performance by hold-time bucket."""
    results = []
    for name, lo, hi in _HOLD_BUCKETS:
        bucket_trades = [t for t in trades if lo <= t.hold_minutes < hi]
        if not bucket_trades:
            continue
        stats = _compute_group_stats(bucket_trades)
        stats["zone"] = name
        results.append(stats)

    # Find best zone
    if results:
        best = max(results, key=lambda x: x["total_pnl"])
        for r in results:
            r["is_best"] = (r["zone"] == best["zone"] and best["total_pnl"] > 0)

    return results


# ──────────────────────────────────────────────────────────────
# 3. EXIT OPTIMIZER
# ──────────────────────────────────────────────────────────────
def exit_optimizer(trades: List[TradeRecord]) -> List[Dict[str, Any]]:
    """Analyze performance by exit reason."""
    # Map raw exit reasons to readable labels
    exit_labels = {
        "take_profit": "🎯 Take Profit",
        "stop_loss": "🛑 Stop Loss",
        "trailing_stop": "📈 Trailing Stop",
        "breakeven_stop": "⚖️ Breakeven Stop",
        "timeout": "⏰ Time-Based Exit",
        "partial_profit_2.5R": "💰 Partial TP1 (2.5R)",
        "partial_profit_3R": "💰 Partial TP2 (3R)",
        "liquidation": "💥 Liquidation",
        "unknown": "📌 Unknown",
    }

    reason_groups = {}
    for t in trades:
        reason = t.exit_reason or "unknown"
        if reason not in reason_groups:
            reason_groups[reason] = []
        reason_groups[reason].append(t)

    results = []
    for reason, reason_trades in sorted(reason_groups.items(), key=lambda x: -len(x[1])):
        stats = _compute_group_stats(reason_trades)
        stats["exit_reason"] = reason
        stats["label"] = exit_labels.get(reason, f"📌 {reason}")
        results.append(stats)

    # Find best exit method
    if results:
        best = max(results, key=lambda x: x["expectancy"])
        for r in results:
            r["is_best"] = (r["exit_reason"] == best["exit_reason"] and best["expectancy"] > 0)

    return results


# ──────────────────────────────────────────────────────────────
# 4. CONFIDENCE ACCURACY TABLE
# ──────────────────────────────────────────────────────────────
def confidence_accuracy(trades: List[TradeRecord]) -> List[Dict[str, Any]]:
    """Show actual win rate and PnL by confidence bucket."""
    results = []
    for label, lo, hi in _CONF_BUCKETS:
        bucket_trades = [t for t in trades if lo <= t.confidence < hi]
        if not bucket_trades:
            continue
        stats = _compute_group_stats(bucket_trades)

        # Calibration error: how far is actual WR from raw confidence?
        avg_conf = np.mean([t.confidence for t in bucket_trades]) * 100
        stats["bucket"] = label
        stats["avg_confidence"] = round(float(avg_conf), 1)
        stats["calibration_error"] = round(float(stats["win_rate"] - avg_conf), 1)

        # Determine if overconfident or underconfident
        if stats["calibration_error"] < -5:
            stats["calibration_note"] = "⚠️ Overconfident"
        elif stats["calibration_error"] > 5:
            stats["calibration_note"] = "📈 Underconfident"
        else:
            stats["calibration_note"] = "✅ Well-calibrated"

        results.append(stats)

    return results


# ──────────────────────────────────────────────────────────────
# 5. SYMBOL EXPECTANCY TABLE
# ──────────────────────────────────────────────────────────────
def symbol_expectancy(trades: List[TradeRecord], min_trades: int = 2) -> List[Dict[str, Any]]:
    """Per-symbol expectancy, PF, avg R, max drawdown. Hide negative expectancy."""
    from collections import defaultdict

    sym_groups = defaultdict(list)
    for t in trades:
        sym_groups[t.symbol].append(t)

    results = []
    for sym, sym_trades in sym_groups.items():
        stats = _compute_group_stats(sym_trades)

        # Avg R
        r_multiples = []
        for t in sym_trades:
            if t.risk_reward != 0:
                r_multiples.append(t.pnl / (t.entry_price * 0.009 * 10))  # approximate R
        stats["avg_r"] = round(float(np.mean(r_multiples)), 2) if r_multiples else 0

        # Max drawdown for this symbol
        pnls = np.array([t.pnl for t in sym_trades])
        cum = np.cumsum(pnls)
        peak = np.maximum.accumulate(cum)
        dd = (cum - peak)
        stats["max_dd"] = round(float(abs(dd.min())), 2)

        stats["symbol"] = sym
        stats["avg_pnl"] = round(float(np.mean(pnls)), 2)
        stats["is_positive"] = stats["expectancy"] > 0

        results.append(stats)

    # Sort by expectancy descending
    results.sort(key=lambda x: x["expectancy"], reverse=True)

    # Filter: hide negative expectancy with fewer than 5 trades
    filtered = [r for r in results if r["is_positive"] or r["trades"] >= min_trades]

    return filtered


# ──────────────────────────────────────────────────────────────
# 6. SESSION ANALYTICS
# ──────────────────────────────────────────────────────────────
def session_analytics(trades: List[TradeRecord]) -> List[Dict[str, Any]]:
    """Performance by trading session (Asia, London, New York, Off-Hours)."""
    session_labels = {
        "asia": "🌏 Asia (00-08 UTC)",
        "london": "🇬🇧 London (07-15 UTC)",
        "new_york": "🇺🇸 New York (13-21 UTC)",
        "off_hours": "🌙 Off-Hours (21-00 UTC)",
    }

    results = []
    for session_name in ["asia", "london", "new_york", "off_hours"]:
        session_trades = [t for t in trades if t.session == session_name]
        if not session_trades:
            continue
        stats = _compute_group_stats(session_trades)
        stats["session"] = session_name
        stats["label"] = session_labels.get(session_name, session_name)

        # Best/worst symbols in this session
        from collections import defaultdict
        sym_pnl = defaultdict(float)
        for t in session_trades:
            sym_pnl[t.symbol] += t.pnl
        if sym_pnl:
            stats["best_symbol"] = max(sym_pnl, key=sym_pnl.get)
            stats["best_symbol_pnl"] = round(sym_pnl[stats["best_symbol"]], 2)

        results.append(stats)

    # Find best session
    if results:
        best = max(results, key=lambda x: x["expectancy"])
        for r in results:
            r["is_best"] = (r["session"] == best["session"] and best["expectancy"] > 0)

    return results


# ──────────────────────────────────────────────────────────────
# 7. LIVE ENGINE PERFORMANCE (rolling windows)
# ──────────────────────────────────────────────────────────────
def live_engine_performance(trades: List[TradeRecord]) -> Dict[str, Any]:
    """Compute rolling performance for last 20, 50, 100 trades."""
    windows = {}
    for n in [20, 50, 100]:
        recent = trades[-n:] if len(trades) >= n else trades
        if recent:
            stats = _compute_group_stats(recent)
            stats["window"] = f"Last {len(recent)} trades"
            # Time range
            stats["from"] = datetime.fromtimestamp(recent[0].opened_at).strftime("%Y-%m-%d %H:%M") if recent[0].opened_at else "—"
            stats["to"] = datetime.fromtimestamp(recent[-1].closed_at).strftime("%Y-%m-%d %H:%M") if recent[-1].closed_at else "—"
            windows[f"last_{n}"] = stats

    # Overall
    if trades:
        overall = _compute_group_stats(trades)
        overall["window"] = "All Trades"
        overall["from"] = datetime.fromtimestamp(trades[0].opened_at).strftime("%Y-%m-%d %H:%M") if trades[0].opened_at else "—"
        overall["to"] = datetime.fromtimestamp(trades[-1].closed_at).strftime("%Y-%m-%d %H:%M") if trades[-1].closed_at else "—"
        windows["all"] = overall

    return windows


# ──────────────────────────────────────────────────────────────
# 8. AUTO RECOMMENDATIONS
# ──────────────────────────────────────────────────────────────
def auto_recommendations(trades: List[TradeRecord]) -> List[Dict[str, Any]]:
    """Generate actionable recommendations from historical data."""
    recs = []

    if len(trades) < 10:
        return [{"priority": "ℹ️ INFO", "recommendation": "Need at least 10 trades for recommendations.",
                 "detail": "Continue trading to build data.", "impact": "—" }]

    # ── 1. Confidence threshold ──
    conf_stats = confidence_accuracy(trades)
    if conf_stats:
        # Find sweet spot: highest expectancy bucket
        best_conf = max(conf_stats, key=lambda x: x["expectancy"])
        if best_conf["expectancy"] > 0:
            recs.append({
                "priority": "🎯 CONFIDENCE",
                "recommendation": f"Set minimum confidence to {best_conf['bucket']}",
                "detail": f"Trades with {best_conf['bucket']} confidence have {best_conf['win_rate']}% WR, ${best_conf['expectancy']:+.2f} expectancy, PF {best_conf['profit_factor']}",
                "impact": f"Would have taken {best_conf['trades']} trades with ${best_conf['total_pnl']:+.2f} total PnL",
            })

    # ── 2. Minimum R:R ──
    rr_ranges = [
        ("R:R 0.5-1.0", 0.5, 1.0),
        ("R:R 1.0-1.5", 1.0, 1.5),
        ("R:R 1.5-2.0", 1.5, 2.0),
        ("R:R 2.0-3.0", 2.0, 3.0),
        ("R:R 3.0+",    3.0, 999.0),
    ]
    rr_results = []
    for label, lo, hi in rr_ranges:
        rr_trades = [t for t in trades if lo <= t.risk_reward < hi]
        if rr_trades and len(rr_trades) >= 3:
            stats = _compute_group_stats(rr_trades)
            stats["range"] = label
            stats["min_rr"] = lo
            rr_results.append(stats)

    if rr_results:
        best_rr = max(rr_results, key=lambda x: x["expectancy"])
        if best_rr["expectancy"] > 0:
            recs.append({
                "priority": "⚖️ RISK:REWARD",
                "recommendation": f"Target minimum R:R of {best_rr['min_rr']:.1f}",
                "detail": f"{best_rr['range']} gives {best_rr['win_rate']}% WR, ${best_rr['expectancy']:+.2f} expectancy, PF {best_rr['profit_factor']}",
                "impact": f"Would have taken {best_rr['trades']} trades with ${best_rr['total_pnl']:+.2f} total PnL",
            })
        else:
            best_rr = max(rr_results, key=lambda x: x["expectancy"])
            recs.append({
                "priority": "⚖️ RISK:REWARD",
                "recommendation": f"Best R:R zone: {best_rr['range']} (still negative — improve entries)",
                "detail": f"{best_rr['win_rate']}% WR, ${best_rr['expectancy']:+.2f} expectancy",
                "impact": "All R:R zones losing — focus on entry quality",
            })

    # ── 3. Minimum hold time ──
    hold_stats = hold_time_optimizer(trades)
    if hold_stats:
        # Find first profitable zone
        profitable_zones = [h for h in hold_stats if h["total_pnl"] > 0 and h["trades"] >= 3]
        if profitable_zones:
            best_hold = profitable_zones[0]  # already sorted by zone order
            recs.append({
                "priority": "⏱️ HOLD TIME",
                "recommendation": f"Minimum hold time: {best_hold['zone']}",
                "detail": f"{best_hold['win_rate']}% WR, ${best_hold['expectancy']:+.2f} expectancy, PF {best_hold['profit_factor']}",
                "impact": f"Would have taken {best_hold['trades']} trades with ${best_hold['total_pnl']:+.2f} total PnL",
            })
        else:
            # Find zone with least losses
            best_zone = max(hold_stats, key=lambda x: x["expectancy"])
            recs.append({
                "priority": "⏱️ HOLD TIME",
                "recommendation": f"Best zone: {best_zone['zone']} (still negative — improve entries)",
                "detail": f"{best_zone['win_rate']}% WR, ${best_zone['expectancy']:+.2f} expectancy",
                "impact": "All hold-time zones are losing — focus on entry quality first",
            })

    # ── 4. Best session ──
    sess_stats = session_analytics(trades)
    if sess_stats:
        profitable_sessions = [s for s in sess_stats if s["total_pnl"] > 0 and s["trades"] >= 5]
        if profitable_sessions:
            best_sess = max(profitable_sessions, key=lambda x: x["expectancy"])
            recs.append({
                "priority": "🌏 SESSION",
                "recommendation": f"Focus on {best_sess['label']}",
                "detail": f"{best_sess['win_rate']}% WR, ${best_sess['expectancy']:+.2f} expectancy, {best_sess['trades']} trades",
                "impact": f"${best_sess['total_pnl']:+.2f} total PnL in this session",
            })
        else:
            worst_sessions = sorted(sess_stats, key=lambda x: x["expectancy"])
            if worst_sessions:
                recs.append({
                    "priority": "🌏 SESSION",
                    "recommendation": f"Avoid {worst_sessions[0]['label']} (worst session)",
                    "detail": f"{worst_sessions[0]['win_rate']}% WR, ${worst_sessions[0]['expectancy']:+.2f} expectancy",
                    "impact": f"Avoiding saves ${abs(worst_sessions[0]['total_pnl']):.2f} in losses",
                })

    # ── 5. Best symbols ──
    sym_stats = symbol_expectancy(trades)
    profitable_syms = [s for s in sym_stats if s["is_positive"] and s["trades"] >= 3]
    if profitable_syms:
        top5 = profitable_syms[:5]
        recs.append({
            "priority": "🏷️ SYMBOLS",
            "recommendation": f"Top symbols: {', '.join(s['symbol'] for s in top5)}",
            "detail": f"Positive expectancy symbols with ≥3 trades",
            "impact": f"Combined ${sum(s['total_pnl'] for s in top5):+,.2f} PnL across {sum(s['trades'] for s in top5)} trades",
        })

    # ── 6. Version improvement ──
    ver_stats = versioned_analytics(trades)
    if len(ver_stats) >= 2:
        latest = ver_stats[-1]
        prev = ver_stats[-2]
        if latest["expectancy"] > prev["expectancy"]:
            recs.append({
                "priority": "📈 VERSION",
                "recommendation": f"Latest version ({latest['label']}) is improving",
                "detail": f"Expectancy: ${latest['expectancy']:+.2f} vs ${prev['expectancy']:+.2f} (previous)",
                "impact": f"WR: {latest['win_rate']}% vs {prev['win_rate']}%",
            })
        elif latest["expectancy"] < prev["expectancy"]:
            recs.append({
                "priority": "⚠️ VERSION",
                "recommendation": f"Latest version ({latest['label']}) regression detected",
                "detail": f"Expectancy: ${latest['expectancy']:+.2f} vs ${prev['expectancy']:+.2f} (previous)",
                "impact": "Investigate recent changes",
            })

    # ── 7. Exit optimization ──
    exit_stats = exit_optimizer(trades)
    if exit_stats:
        best_exit = max(exit_stats, key=lambda x: x["expectancy"])
        if best_exit["expectancy"] > 0:
            recs.append({
                "priority": "🚪 EXIT",
                "recommendation": f"Best exit: {best_exit['label']}",
                "detail": f"{best_exit['win_rate']}% WR, ${best_exit['expectancy']:+.2f} expectancy, PF {best_exit['profit_factor']}",
                "impact": f"{best_exit['trades']} trades using this exit",
            })

    if not recs:
        recs.append({
            "priority": "ℹ️ INFO",
            "recommendation": "No strong recommendations yet",
            "detail": "Need more data or better trade outcomes",
            "impact": "Continue collecting data",
        })

    return recs


# ══════════════════════════════════════════════════════════════
# UNIFIED ORCHESTRATOR
# ══════════════════════════════════════════════════════════════
class TradeAnalyticsOrchestrator:
    """Runs all analytics modules and returns a unified result dict."""

    def __init__(self):
        self.trades = load_trades()

    def run_all(self) -> Dict[str, Any]:
        """Run all analytics and return structured results."""
        return {
            "total_trades": len(self.trades),
            "versions": versioned_analytics(self.trades),
            "hold_time": hold_time_optimizer(self.trades),
            "exits": exit_optimizer(self.trades),
            "confidence": confidence_accuracy(self.trades),
            "symbols": symbol_expectancy(self.trades),
            "sessions": session_analytics(self.trades),
            "live_performance": live_engine_performance(self.trades),
            "recommendations": auto_recommendations(self.trades),
        }


# ══════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("🔄 Running Trade Analytics Engine...")
    migrate_db()
    print(f"\n✅ Migration complete. Loading trades...")
    
    orch = TradeAnalyticsOrchestrator()
    print(f"📊 Loaded {len(orch.trades)} trades\n")

    results = orch.run_all()

    print("═══ VERSIONED ANALYTICS ═══")
    for v in results["versions"]:
        print(f"  {v['label']}: {v['trades']} trades, WR={v['win_rate']}%, PF={v['profit_factor']}, PnL=${v['total_pnl']:+,.2f}")

    print("\n═══ HOLD TIME OPTIMIZER ═══")
    for h in results["hold_time"]:
        marker = " ⭐" if h.get("is_best") else ""
        print(f"  {h['zone']}: {h['trades']} trades, WR={h['win_rate']}%, PF={h['profit_factor']}, PnL=${h['total_pnl']:+,.2f}{marker}")

    print("\n═══ EXIT OPTIMIZER ═══")
    for e in results["exits"]:
        marker = " ⭐" if e.get("is_best") else ""
        print(f"  {e['label']}: {e['trades']} trades, WR={e['win_rate']}%, Exp=${e['expectancy']:+.2f}{marker}")

    print("\n═══ CONFIDENCE ACCURACY ═══")
    for c in results["confidence"]:
        print(f"  {c['bucket']}: {c['trades']} trades, WR={c['win_rate']}%, Conf={c['avg_confidence']}%, Error={c['calibration_error']:+.1f}% {c['calibration_note']}")

    print("\n═══ SESSION ANALYTICS ═══")
    for s in results["sessions"]:
        marker = " ⭐" if s.get("is_best") else ""
        print(f"  {s['label']}: {s['trades']} trades, WR={s['win_rate']}%, PnL=${s['total_pnl']:+,.2f}{marker}")

    print("\n═══ LIVE ENGINE PERFORMANCE ═══")
    for k, v in results["live_performance"].items():
        print(f"  {v['window']}: {v['trades']} trades, WR={v['win_rate']}%, PF={v['profit_factor']}, Exp=${v['expectancy']:+.2f}")

    print("\n═══ AUTO RECOMMENDATIONS ═══")
    for r in results["recommendations"]:
        print(f"  {r['priority']}: {r['recommendation']}")
        print(f"    → {r['detail']}")
        print(f"    📈 {r['impact']}")
        print()
