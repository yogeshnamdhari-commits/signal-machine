"""
READ-ONLY per-trade market-data freshness audit using engine_service.log.

For each post-rebaseline position, scan the log window [open, close] and
extract:
  - data-health snapshots (📊 SYM trades=N 5m_klines=M of=X rg=Y)
  - scorer rejections
  - WS disconnect/reconnect events
  - REST timeout/fallback events
  - POSITION OPENED / TRADE CLOSED / LIFECYCLE CLOSED / PARTIAL EXIT lines
"""
from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

DB = "data/institutional_v1.db"
LOG = "data/logs/engine_service.log"
REBASELINE_AT = 1786441147.773142

# Local date(s) covered by the dataset (post-rebaseline trades open/close)
DATELINE = datetime.fromtimestamp(REBASELINE_AT).strftime("%Y-%m-%d")


def load_positions() -> List[dict]:
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """
        SELECT pa.id, pa.symbol, pa.side, pa.entry_price, pa.stop_loss,
               pa.take_profit, pa.quantity, pa.opened_at, pa.closed_at,
               pa.exit_reason, pa.pnl, pa.mae_pct, pa.mfe_pct, pa.realized_r,
               pa.hold_minutes, pa.confidence, pa.regime,
               s.stop_loss AS sig_sl, s.take_profit AS sig_tp, s.timestamp AS sig_ts
        FROM positions_archive pa
        LEFT JOIN signals s ON s.id = pa.signal_id
        WHERE pa.opened_at >= ?
        ORDER BY pa.opened_at, pa.id
        """,
        (REBASELINE_AT,),
    ).fetchall()
    return [dict(r) for r in rows]


def ts_to_hm(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def mark_dups(positions: List[dict]) -> Dict[int, int]:
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    by = {}
    for p in positions:
        sid = p.get("signal_id")
        if not sid:
            continue
        r = db.execute("SELECT * FROM signals WHERE id=?", (sid,)).fetchone()
        if r:
            by[sid] = (r["symbol"], r["side"], round(r["entry"] or 0, 8),
                       round(r["stop_loss"] or 0, 8), round(r["take_profit"] or 0, 8),
                       round(r["confidence"] or 0, 4))
    seen, dup = {}, {}
    for p in positions:
        s = by.get(p.get("signal_id"))
        if not s:
            continue
        if s in seen:
            dup[p["id"]] = seen[s]
        else:
            seen[s] = p["id"]
    return dup


def parse_time(line: str) -> Optional[float]:
    """Extract local timestamp from a log line. Supports:
       '18:52:27 | ...'   -> today (relative to dateline)
       '2026-08-10 18:46:20.311 | ...'
    Returns epoch or None."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}):(\d{2}):(\d{2})", line)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                            int(m.group(4)[:2]), int(m.group(4)[3:5]) if False else int(m.group(5)) if False else 0).timestamp()
        except Exception:
            return None
    # bare HH:MM:SS prefix
    m = re.match(r"(\d{2}):(\d{2}):(\d{2})", line)
    if m:
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        base = datetime.fromtimestamp(REBASELINE_AT).replace(hour=0, minute=0, second=0, microsecond=0)
        # if hour > some late threshold, it might be next day — assume same day if hour >= open hour
        return base.replace(hour=h, minute=mi, second=s).timestamp()
    return None


def main() -> None:
    positions = load_positions()
    dups = mark_dups(positions)
    sym_pos: Dict[str, List[dict]] = defaultdict(list)
    for p in positions:
        sym_pos[p["symbol"]].append(p)

    # Build time window -> which positions are open
    events: Dict[Tuple[str, str], List[str]] = defaultdict(list)  # (sym, kind) -> lines

    print("Scanning log (this may take a moment)...", flush=True)
    buf_size = 0
    with open(LOG, "r", errors="replace") as f:
        for line in f:
            buf_size += len(line)
            t = parse_time(line)
            if t is None:
                continue
            # Only consider the dateline day (post-rebaseline)
            day = datetime.fromtimestamp(t).strftime("%Y-%m-%d")
            if day not in ("2026-08-11", "2026-08-12"):
                continue
            hm = datetime.fromtimestamp(t).strftime("%m-%d %H:%M:%S")
            # Determine symbol from line (data-health + symbol-specific events)
            # data-health: 📊 SYM trades=N
            m = re.search(r"📊\s+([A-Z0-9]+USDT)\s+trades=(\d+) 5m_klines=(\d+) of=(\w+) rg=(\w+)", line)
            if m:
                sym = m.group(1)
                # only if symbol is in our set
                if sym in sym_pos:
                    events[(sym, "health")].append(
                        f"{hm} trades={m.group(2)} 5m={m.group(3)} of={m.group(4)} rg={m.group(5)}"
                    )
                continue
            m = re.search(r"⏭️\s+([A-Z0-9]+USDT)\s+—\s+scorer rejected", line)
            if m and m.group(1) in sym_pos:
                events[(m.group(1), "reject")].append(hm)
                continue
            # WS events
            if "WS connect failed" in line or "WS connecting" in line:
                events[("__WS__", "ws")].append(hm)
                continue
            if "REST timeout" in line or "REST fallback" in line:
                events[("__REST__", "rest")].append(hm)
                continue
            # position events for our symbols
            m = re.search(r"(POSITION OPENED|TRADE CLOSED|LIFECYCLE CLOSED|PARTIAL EXIT)[^\n]*([A-Z0-9]+USDT)", line)
            if m:
                sym = m.group(2)
                if sym in sym_pos:
                    events[(sym, "trade")].append(f"{hm} {line.strip()}")
                continue

    # Report per position
    print("\n" + "=" * 118)
    print("PER-TRADE DATA-FRESHNESS REPORT")
    print("=" * 118)

    for p in positions:
        pid = p["id"]
        sym = p["symbol"]
        o, c = p["opened_at"], p["closed_at"]
        o_hm, c_hm = ts_to_hm(o), ts_to_hm(c)
        hold = p["hold_minutes"] or 0
        mae, mfe = p["mae_pct"] or 0, p["mfe_pct"] or 0
        r_m = p["realized_r"] or 0
        dup = f" DUP->{dups[pid]}" if pid in dups else ""

        # filter health lines within window (local wall-clock, date-aware)
        pos_date = datetime.fromtimestamp(o).strftime("%m-%d")

        def inwin(t):
            # t = "MM-DD HH:MM:SS"
            d, hms = t.split()
            h, mi, s = (int(x) for x in hms.split(":"))
            secs = h * 3600 + mi * 60 + s
            osecs = datetime.fromtimestamp(o).hour * 3600 + datetime.fromtimestamp(o).minute * 60 + datetime.fromtimestamp(o).second
            csecs = datetime.fromtimestamp(c).hour * 3600 + datetime.fromtimestamp(c).minute * 60 + datetime.fromtimestamp(c).second
            return d == pos_date and osecs - 60 <= secs <= csecs + 30

        health = [e for e in events.get((sym, "health"), []) if inwin(e.split()[0] + " " + e.split()[1])]
        rejects = [e for e in events.get((sym, "reject"), []) if inwin(e)]
        trade_lines = [e for e in events.get((sym, "trade"), []) if inwin(e.split()[0] + " " + e.split()[1])]

        # WS/REST events in window (global)
        ws_ev = [e for e in events.get(("__WS__", "ws"), []) if inwin(e)]
        rest_ev = [e for e in events.get(("__REST__", "rest"), []) if inwin(e)]

        zero_health = [e for e in health if "trades=0" in e and "5m=0" in e and "of=False" in e and "rg=False" in e]
        no_trades = [e for e in health if "trades=0" in e]
        n_health = len(health)

        # summary flags
        flags = []
        if zero_health:
            flags.append(f"NO_DATA({len(zero_health)})")
        elif no_trades:
            flags.append(f"LOW_DATA(trades=0 x{len(no_trades)})")
        if ws_ev:
            flags.append(f"WS_EVENTS={len(ws_ev)}")
        if rest_ev:
            flags.append(f"REST_FALLBACK={len(rest_ev)}")
        if rejects:
            flags.append(f"SCORER_REJECTS={len(rejects)}")

        # Overlap with the known global outage window 18:51-19:04
        def secs_of(hm):
            h, mi, s = (int(x) for x in hm.split(":"))
            return h * 3600 + mi * 60 + s
        osecs = datetime.fromtimestamp(o).hour * 3600 + datetime.fromtimestamp(o).minute * 60 + datetime.fromtimestamp(o).second
        csecs = datetime.fromtimestamp(c).hour * 3600 + datetime.fromtimestamp(c).minute * 60 + datetime.fromtimestamp(c).second
        OUTAGE_START, OUTAGE_END = secs_of("18:51:21"), secs_of("19:04:16")
        outage_overlap = (osecs < OUTAGE_END and csecs > OUTAGE_START) and pos_date == "08-11"
        if outage_overlap:
            flags.append("IN_OUTAGE_WINDOW(18:51-19:04)")

        # expected SL distance
        e_price = p["entry_price"] or 0
        sl = p["sig_sl"] or 0
        sl_dist = abs(e_price - sl) / e_price * 100 if (e_price and sl) else 0
        ratio = mae / sl_dist if sl_dist > 0 else 0

        print(f"\n--- pos {pid} {sym} {p['side']} open={o_hm} close={c_hm} hold={hold:.0f}m "
              f"reason={p['exit_reason']} pnl=${p['pnl']:.2f}{dup}")
        print(f"    entry={e_price} intendedSL={sl} SLdist={sl_dist:.2f}% "
              f"MAE={mae:.2f}% MFE={mfe:.2f}% R={r_m:.1f} mae/SL={ratio:.2f}")
        if flags:
            print(f"    FLAGS: {', '.join(flags)}")
        if zero_health:
            print(f"    ZERO-DATA snapshots ({len(zero_health)}): {zero_health[:5]}{' ...' if len(zero_health)>5 else ''}")
        elif n_health:
            print(f"    data snapshots ({n_health}): {health[0] if health else ''} | {health[-1] if health else ''}")
        if ws_ev:
            print(f"    WS events in window ({len(ws_ev)}): {ws_ev[:4]}{' ...' if len(ws_ev)>4 else ''}")
        if rest_ev:
            print(f"    REST fallback in window ({len(rest_ev)}): {rest_ev[:3]}")
        for tl in trade_lines[:6]:
            print(f"    {tl[:150]}")
        if not n_health and not zero_health and not trade_lines and not ws_ev:
            print("    (no health/trade/ws lines found in window)")


if __name__ == "__main__":
    main()
