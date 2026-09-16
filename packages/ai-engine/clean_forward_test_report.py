"""
Clean forward-test performance report.

Measurement ONLY — no strategy/logic changes, no state mutation.

Authoritative source: positions_archive (one row per position, full close,
partial slices accumulated into the row's `pnl` column).

Eligibility:
  1. opened_at >= rebaseline_at
  2. exclude all partial records (archive rows are full closes; partial
     slices are already accumulated into `pnl`)
  3. exclude pre-rebaseline trades
  4. one authoritative archive row per position
  5. reject duplicate position IDs
  6. use realized PnL from the authoritative archive (`pnl`, which is the
     accumulated position PnL incl. partial slices); do NOT reconstruct
     P/L from dashboard values
  7. do not count duplicate signal emissions as separate trades
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

DB = "data/institutional_v1.db"
REBASELINE_AT = 1786441147.773142  # 2026-08-11 15:09:07 local


@dataclass
class Trade:
    id: int
    signal_id: str
    symbol: str
    side: str
    entry: float
    exit_reason: str
    opened_at: float
    closed_at: float
    hold_minutes: float
    pnl: float            # authoritative accumulated position PnL
    realized_raw: float   # final-slice realized pnl (audit column)
    confidence: float
    regime: str
    volume_surge: Optional[bool]
    volume_ratio: Optional[float]
    volume_expand: Optional[bool]
    candle: str
    duplicate_of: Optional[int] = None  # id of first trade in duplicate pair


def _load() -> List[Trade]:
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """
        SELECT pa.id, pa.signal_id, pa.symbol, pa.side, pa.entry_price,
               pa.exit_reason, pa.opened_at, pa.closed_at, pa.hold_minutes,
               pa.pnl, pa.realized_pnl_raw, pa.confidence, pa.regime,
               s.metadata
        FROM positions_archive pa
        LEFT JOIN signals s ON s.id = pa.signal_id
        WHERE pa.opened_at >= ?
        ORDER BY pa.opened_at, pa.id
        """,
        (REBASELINE_AT,),
    ).fetchall()

    trades: List[Trade] = []
    for r in rows:
        meta: Dict = {}
        try:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
            if isinstance(meta, str):  # double-encoded JSON
                meta = json.loads(meta)
        except Exception:
            meta = {}
        comp = meta.get("components", {}) if isinstance(meta, dict) else {}
        vol = comp.get("volume", "")
        surge = None
        ratio = None
        expand = None
        if isinstance(vol, str) and "surge" in vol:
            for tok in vol.split("_"):
                if tok.startswith("ratio="):
                    try:
                        ratio = float(tok.split("=")[1])
                    except Exception:
                        ratio = None
                elif tok.startswith("surge="):
                    surge = tok.split("=")[1] == "yes"
                elif tok.startswith("expand="):
                    expand = tok.split("=")[1] == "yes"
        trades.append(
            Trade(
                id=r["id"],
                signal_id=r["signal_id"] or "",
                symbol=r["symbol"],
                side=r["side"],
                entry=r["entry_price"] or 0,
                exit_reason=r["exit_reason"],
                opened_at=r["opened_at"] or 0,
                closed_at=r["closed_at"] or 0,
                hold_minutes=r["hold_minutes"] or 0,
                pnl=r["pnl"] or 0,
                realized_raw=r["realized_pnl_raw"] or 0,
                confidence=r["confidence"] or 0,
                regime=r["regime"] or "",
                volume_surge=surge,
                volume_ratio=ratio,
                volume_expand=expand,
                candle=str(comp.get("candle", "")) if isinstance(comp, dict) else "",
            )
        )
    return trades


def _mark_duplicates(trades: List[Trade]) -> List[Trade]:
    """Flag identical signal emissions as duplicates.

    A trade is a duplicate if the SAME symbol+side emits the identical
    signal content (entry/SL/TP/confidence within float tolerance) again
    within the same_symbol_sec cooldown window. Keep the first occurrence.
    """
    sigs = sqlite3.connect(DB)
    sigs.row_factory = sqlite3.Row
    by_signal = {}
    for t in trades:
        if not t.signal_id:
            continue
        row = sigs.execute("SELECT * FROM signals WHERE id=?", (t.signal_id,)).fetchone()
        if row:
            by_signal[t.signal_id] = dict(row)

    # Map position -> signal signature
    def sig(sid: str) -> Optional[Tuple]:
        if not sid or sid not in by_signal:
            return None
        s = by_signal[sid]
        return (
            s["symbol"],
            s["side"],
            round(s["entry"] or 0, 8),
            round(s["stop_loss"] or 0, 8),
            round(s["take_profit"] or 0, 8),
            round(s["confidence"] or 0, 4),
        )

    seen: Dict[Tuple, Trade] = {}
    for t in trades:
        s = sig(t.signal_id)
        if not s:
            continue
        if s in seen:
            t.duplicate_of = seen[s].id
        else:
            seen[s] = t
    return trades


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _bucket(conf: float) -> str:
    if conf < 0.50:
        return "conf_40_50"
    if conf < 0.60:
        return "conf_50_60"
    if conf < 0.70:
        return "conf_60_70"
    return "conf_70_plus"


def _stats(trades: List[Trade]) -> Dict:
    if not trades:
        return {"n": 0, "wins": 0, "losses": 0, "wr": 0, "gp": 0, "gl": 0,
                "pf": None, "expectancy": 0, "avg_win": 0, "avg_loss": 0,
                "max_cl": 0, "max_dd": 0, "net": 0}
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gp = sum(t.pnl for t in wins)
    gl = abs(sum(t.pnl for t in losses))
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0)
    net = gp - gl
    # max consecutive losses
    max_cl = cur_cl = 0
    # max drawdown from running equity
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in sorted(trades, key=lambda x: x.closed_at):
        if t.pnl <= 0:
            cur_cl += 1
            max_cl = max(max_cl, cur_cl)
        else:
            cur_cl = 0
        running += t.pnl
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
    return {
        "n": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "wr": len(wins) / len(trades) * 100,
        "gp": gp,
        "gl": gl,
        "pf": pf,
        "expectancy": net / len(trades),
        "avg_win": (gp / len(wins)) if wins else 0,
        "avg_loss": (-gl / len(losses)) if losses else 0,
        "max_cl": max_cl,
        "max_dd": max_dd,
        "net": net,
    }


def main() -> None:
    trades = _load()
    trades = _mark_duplicates(trades)
    clean = [t for t in trades if t.duplicate_of is None]

    print("=" * 78)
    print("CLEAN FORWARD-TEST REPORT — EMA V5 (measurement only)")
    print("=" * 78)
    print(f"rebaseline_at          : {_fmt(REBASELINE_AT)}")
    print(f"post-rebaseline rows   : {len(trades)}")
    print(f"duplicate emissions    : {len(trades) - len(clean)}")
    print(f"clean trades           : {len(clean)}")

    # Chronological table
    print("\n--- CHRONOLOGICAL TRADE TABLE (clean) ---")
    hdr = f"{'id':>5} {'symbol':<12} {'side':<5} {'entry':>10} {'exit_reason':<14} {'closed':<19} {'pnl':>8}"
    print(hdr)
    print("-" * len(hdr))
    for t in sorted(clean, key=lambda x: x.closed_at):
        print(
            f"{t.id:>5} {t.symbol:<12} {t.side:<5} {t.entry:>10.6f} "
            f"{t.exit_reason:<14} {_fmt(t.closed_at):<19} {t.pnl:>8.2f}"
        )

    # Overview stats
    s = _stats(clean)
    print("\n--- OVERVIEW ---")
    print(f"total clean trades     : {s['n']}")
    print(f"wins                   : {s['wins']}")
    print(f"losses                 : {s['losses']}")
    print(f"win rate               : {s['wr']:.1f}%")
    print(f"gross profit           : ${s['gp']:.2f}")
    print(f"gross loss             : ${s['gl']:.2f}")
    print(f"profit factor          : {s['pf'] if s['pf'] != float('inf') else 'INF'}")
    print(f"expectancy/trade       : ${s['expectancy']:.3f}")
    print(f"average win            : ${s['avg_win']:.2f}")
    print(f"average loss           : ${s['avg_loss']:.2f}")
    print(f"max consecutive losses : {s['max_cl']}")
    print(f"max drawdown           : ${s['max_dd']:.2f}")

    # By side
    print("\n--- BY SIDE ---")
    for side in ("LONG", "SHORT"):
        st = _stats([t for t in clean if t.side == side])
        print(
            f"{side:<6} n={st['n']} wr={st['wr']:.1f}% "
            f"pf={st['pf'] if st['pf'] != float('inf') else 'INF'} "
            f"pnl=${st['net']:.2f}"
        )

    # Confidence buckets
    print("\n--- BY CONFIDENCE BUCKET ---")
    for b in ("conf_40_50", "conf_50_60", "conf_60_70", "conf_70_plus"):
        st = _stats([t for t in clean if _bucket(t.confidence) == b])
        print(
            f"{b:<12} n={st['n']} wr={st['wr']:.1f}% "
            f"pf={st['pf'] if st['pf'] != float('inf') else 'INF'} "
            f"pnl=${st['net']:.2f}"
        )

    # Volume surge vs normal
    print("\n--- BY VOLUME (surge vs normal) ---")
    surge = [t for t in clean if t.volume_surge]
    normal = [t for t in clean if t.volume_surge is False]
    unknown = [t for t in clean if t.volume_surge is None]
    for label, grp in (("surge ", surge), ("normal", normal), ("unknown", unknown)):
        st = _stats(grp)
        print(
            f"{label:<6} n={st['n']} wr={st['wr']:.1f}% "
            f"pf={st['pf'] if st['pf'] != float('inf') else 'INF'} "
            f"pnl=${st['net']:.2f}"
        )

    # Regime
    print("\n--- BY REGIME ---")
    regimes: Dict[str, List[Trade]] = {}
    for t in clean:
        regimes.setdefault(t.regime or "unknown", []).append(t)
    for reg, grp in sorted(regimes.items()):
        st = _stats(grp)
        print(
            f"{reg:<18} n={st['n']} wr={st['wr']:.1f}% "
            f"pf={st['pf'] if st['pf'] != float('inf') else 'INF'} "
            f"pnl=${st['net']:.2f}"
        )

    # Duplicates detail
    if trades != clean:
        print("\n--- DUPLICATE EMISSIONS EXCLUDED ---")
        for t in trades:
            if t.duplicate_of is not None:
                print(
                    f"pos {t.id} {t.symbol} (dup of {t.duplicate_of}) "
                    f"signal={t.signal_id} pnl=${t.pnl:.2f}"
                )

    print("\nNOTE: archive `pnl` accumulates partial slices (authoritative "
          "per-position PnL). `realized_pnl_rounded` in the archive is the "
          "FINAL slice only and would understate/overstate partial positions.")


if __name__ == "__main__":
    main()
