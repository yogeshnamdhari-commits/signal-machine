"""
READ-ONLY data-integrity audit of post-rebaseline trades (Aug 11 15:09+).

For every position, reconstruct intended risk plan from SIGNALS table
(archive SL/TP may be DB_GUARD-normalized to entry) and compare against
realized MAE/R to detect stale-price / delayed-exit execution failures.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

DB = "data/institutional_v1.db"
REBASELINE_AT = 1786441147.773142


@dataclass
class AuditTrade:
    id: int
    signal_id: str
    symbol: str
    side: str
    entry: float
    qty: float
    opened_at: float
    closed_at: float
    exit_reason: str
    pnl: float
    mae_pct: float
    mfe_pct: float
    realized_r: float
    conf: float
    regime: str
    hold_min: float
    # intended plan (from signals)
    sig_sl: float
    sig_tp: float
    sig_ts: float


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S") if ts else "?"


def load() -> List[AuditTrade]:
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """
        SELECT pa.id, pa.signal_id, pa.symbol, pa.side, pa.entry_price,
               pa.quantity, pa.opened_at, pa.closed_at, pa.exit_reason,
               pa.pnl, pa.mae_pct, pa.mfe_pct, pa.realized_r, pa.confidence,
               pa.regime, pa.hold_minutes
        FROM positions_archive pa
        WHERE pa.opened_at >= ?
        ORDER BY pa.opened_at, pa.id
        """,
        (REBASELINE_AT,),
    ).fetchall()

    out: List[AuditTrade] = []
    for r in rows:
        s = None
        if r["signal_id"]:
            s = db.execute("SELECT * FROM signals WHERE id=?", (r["signal_id"],)).fetchone()
        out.append(
            AuditTrade(
                id=r["id"], signal_id=r["signal_id"] or "", symbol=r["symbol"],
                side=r["side"], entry=r["entry_price"] or 0, qty=r["quantity"] or 0,
                opened_at=r["opened_at"] or 0, closed_at=r["closed_at"] or 0,
                exit_reason=r["exit_reason"], pnl=r["pnl"] or 0,
                mae_pct=r["mae_pct"] or 0, mfe_pct=r["mfe_pct"] or 0,
                realized_r=r["realized_r"] or 0, conf=r["confidence"] or 0,
                regime=r["regime"] or "", hold_min=r["hold_minutes"] or 0,
                sig_sl=(s["stop_loss"] if s else 0) or 0,
                sig_tp=(s["take_profit"] if s else 0) or 0,
                sig_ts=(s["timestamp"] if s else 0) or 0,
            )
        )
    return out


def mark_dups(trades: List[AuditTrade]) -> Dict[int, int]:
    sigs = sqlite3.connect(DB)
    sigs.row_factory = sqlite3.Row
    by = {}
    for t in trades:
        if not t.signal_id:
            continue
        s = sigs.execute("SELECT * FROM signals WHERE id=?", (t.signal_id,)).fetchone()
        if s:
            by[t.signal_id] = (s["symbol"], s["side"], round(s["entry"] or 0, 8),
                               round(s["stop_loss"] or 0, 8), round(s["take_profit"] or 0, 8),
                               round(s["confidence"] or 0, 4))
    seen, dup = {}, {}
    for t in trades:
        s = by.get(t.signal_id)
        if not s:
            continue
        if s in seen:
            dup[t.id] = seen[s]
        else:
            seen[s] = t.id
    return dup


def main() -> None:
    trades = load()
    dups = mark_dups(trades)

    print("=" * 110)
    print("DATA-INTEGRITY AUDIT — per-trade intended-risk vs realized excursion")
    print("=" * 110)
    print(f"{'id':>4} {'sym':<11} {'side':<5} {'entry':>9} {'sigSL':>9} {'sigTP':>9} "
          f"{'SLdist%':>7} {'MAE%':>7} {'MFE%':>7} {'R':>6} {'exit':>6} {'hold':>6} "
          f"{'mae/SL':>6} {'pnl':>8}  reason")
    print("-" * 110)
    for t in trades:
        sl_dist = 0.0
        if t.entry > 0 and t.sig_sl > 0:
            sl_dist = abs(t.entry - t.sig_sl) / t.entry * 100
        ratio = (t.mae_pct / sl_dist) if sl_dist > 0 else 0.0
        dup_mark = f"DUP->{dups[t.id]}" if t.id in dups else ""
        print(
            f"{t.id:>4} {t.symbol:<11} {t.side:<5} {t.entry:>9.6f} {t.sig_sl:>9.6f} {t.sig_tp:>9.6f} "
            f"{sl_dist:>7.2f} {t.mae_pct:>7.2f} {t.mfe_pct:>7.2f} {t.realized_r:>6.1f} "
            f"{_fmt(t.closed_at)[-8:]:>6} {t.hold_min:>6.1f} {ratio:>6.2f} {t.pnl:>8.2f}  "
            f"{t.exit_reason} {dup_mark}"
        )

    print("\n--- STALE/DELAYED-EXIT CANDIDATES (MAE > 1.2x intended SL distance) ---")
    print("Rule: if max adverse excursion materially exceeded the intended SL, the")
    print("protective stop was NOT honored at the intended level (gap or stale data).")
    print()
    for t in trades:
        sl_dist = abs(t.entry - t.sig_sl) / t.entry * 100 if (t.entry > 0 and t.sig_sl > 0) else 0
        ratio = (t.mae_pct / sl_dist) if sl_dist > 0 else 0
        if ratio > 1.2:
            print(
                f"  pos {t.id:>4} {t.symbol:<11} MAE={t.mae_pct:.2f}% vs intended SL={sl_dist:.2f}% "
                f"(x{ratio:.1f}) pnl=${t.pnl:.2f} {t.exit_reason}"
            )


if __name__ == "__main__":
    main()
