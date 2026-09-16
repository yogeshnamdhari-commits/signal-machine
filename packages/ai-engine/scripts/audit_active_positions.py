#!/usr/bin/env python3
"""
ACTIVE POSITION AUDIT — read-only.

Checks each currently-open EMA V5 position against the clean forward-test
cutoff (rebaseline_at from regime_state.json) and reports strategy metadata
(EMAs, pullback/candle/volume components, confidence) plus live unrealized PnL.

Makes NO changes to the engine, strategy, positions, or databases.
Run:  python3 scripts/audit_active_positions.py
"""
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

PKG = Path(__file__).resolve().parent.parent
INST_DB = PKG / "data" / "institutional_v1.db"
REGIME_STATE = PKG / "data" / "regime_state.json"
MARKET_DATA = PKG / "data" / "bridge" / "market_data.json"


def load_cutoff() -> float:
    try:
        with open(REGIME_STATE) as f:
            return float(json.load(f).get("rebaseline_at") or 0)
    except Exception:
        return 0.0


def load_mark_prices():
    try:
        with open(MARKET_DATA) as f:
            d = json.load(f)
        return {r["symbol"]: (r.get("mark_price") or 0) for r in d.get("rows", [])}
    except Exception:
        return {}


def parse_metadata(raw):
    """signals.metadata is a double-JSON-encoded string."""
    if not raw:
        return {}
    try:
        inner = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(inner, str):
            inner = json.loads(inner)
        return inner if isinstance(inner, dict) else {}
    except Exception:
        return {}


def fmt_ts(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%m-%d %H:%M:%S") if ts else "—"


def main():
    cutoff = load_cutoff()
    prices = load_mark_prices()

    con = sqlite3.connect(str(INST_DB), timeout=10)
    con.row_factory = sqlite3.Row
    c = con.cursor()
    positions = c.execute(
        """SELECT id, signal_id, symbol, side, entry_price, quantity, stop_loss,
                  take_profit, confidence, regime, opened_at, at_open_regime,
                  mfe_pct, mae_pct, highest_pnl, current_tp_index
           FROM positions WHERE status!='closed' ORDER BY opened_at DESC"""
    ).fetchall()

    # signals metadata lookup
    sig_map = {}
    for s in c.execute("SELECT id, metadata, entry_reason, timestamp FROM signals").fetchall():
        sig_map[s["id"]] = s
    con.close()

    print()
    print("=" * 90)
    print("  ACTIVE EMA V5 POSITION AUDIT — clean forward-test classification")
    print("=" * 90)
    print(f"  Re-baseline cutoff: {cutoff} "
          f"({fmt_ts(cutoff)} UTC)")

    header = (f"  {'symbol':<12s}{'pos_id':>6s} {'side':<6s}{'signal_time':<19s}"
              f"{'opened_at':<19s} clean?  {'entry':>10s}{'now':>10s}"
              f"{'uPnL$':>9s}{'uPnL%':>8s}  {'conf':>5s}{'regime':<10s}")
    print(header)
    print("  " + "-" * 86)

    clean = pre = 0
    rows_out = []
    for p in positions:
        opened = p["opened_at"] or 0
        is_clean = opened >= cutoff and opened > 0
        if is_clean:
            clean += 1
        else:
            pre += 1

        sym = p["symbol"]
        entry = p["entry_price"] or 0
        now = prices.get(sym, 0)
        qty = p["quantity"] or 0
        side = (p["side"] or "LONG").upper()
        if now and entry:
            upnl = (now - entry) * qty if side == "LONG" else (entry - now) * qty
            upnl_pct = ((now - entry) / entry * 100) if side == "LONG" else ((entry - now) / entry * 100)
        else:
            upnl = upnl_pct = 0.0

        sig = sig_map.get(p["signal_id"])
        meta = parse_metadata(sig["metadata"]) if sig else {}
        ema = meta.get("ema_data", {})
        comp = meta.get("components", {})

        rows_out.append({
            "symbol": sym, "pos_id": p["id"], "side": side,
            "signal_time": fmt_ts(sig["timestamp"] if sig else 0),
            "opened_at": fmt_ts(opened), "is_clean": is_clean,
            "entry_price": entry, "current_price": now,
            "current_unrealized_pnl": upnl, "upnl_pct": upnl_pct,
            "confidence_at_entry": p["confidence"] or 0,
            "regime": p["regime"] or "unknown",
            "ema20": ema.get("ema20"), "ema50": ema.get("ema50"),
            "ema144": ema.get("ema144"), "ema200": ema.get("ema200"),
            "pullback_result": comp.get("pullback"),
            "candle_confirmation": comp.get("candle"),
            "volume_result": comp.get("volume"),
            "entry_reason": (sig["entry_reason"] if sig else ""),
            "at_open_regime": p["at_open_regime"] or "",
            "mfe_pct": p["mfe_pct"] or 0, "mae_pct": p["mae_pct"] or 0,
            "highest_pnl": p["highest_pnl"] or 0,
            "tp_index": p["current_tp_index"] or 1,
        })

        print(f"  {sym:<12s}{p['id']:>6d} {side:<6s}{rows_out[-1]['signal_time']:<19s}"
              f"{rows_out[-1]['opened_at']:<19s}"
              f"{'CLEAN' if is_clean else 'PRE' :<7s}"
              f"{entry:>10.5f}{now:>10.5f}{upnl:>9.2f}{upnl_pct:>7.1f}%  "
              f"{p['confidence'] or 0:<5.2f}{(p['regime'] or 'unknown')[:9]}")

    print("  " + "-" * 86)
    print()
    print("  ── TOTALS ──")
    print(f"    TOTAL ACTIVE POSITIONS:              {len(positions)}")
    print(f"    POST-REBASELINE (clean):             {clean}")
    print(f"    PRE-REBASELINE (excluded):           {pre}")
    print(f"    CLEAN FORWARD-TEST ACTIVE POSITIONS: {clean}")

    print()
    print("  ── DETAIL (strategy metadata) ──")
    for r in rows_out:
        print(f"  [{r['symbol']} / pos #{r['pos_id']}] {r['side']} "
              f"{'CLEAN' if r['is_clean'] else 'PRE-REBASELINE'}")
        print(f"      signal_time={r['signal_time']}  opened_at={r['opened_at']}")
        print(f"      entry={r['entry_price']:.6f}  now={r['current_price']:.6f}  "
              f"uPnL=${r['current_unrealized_pnl']:.2f} ({r['upnl_pct']:+.2f}%)  "
              f"conf={r['confidence_at_entry']*100:.1f}%  regime={r['regime']} "
              f"tp_index={r['tp_index']}")
        print(f"      EMA20={r['ema20']:.6f}  EMA50={r['ema50']:.6f}  "
              f"EMA144={r['ema144']:.6f}  EMA200={r['ema200']:.6f}")
        print(f"      pullback_result={r['pullback_result']}")
        print(f"      candle_confirmation={r['candle_confirmation']}")
        print(f"      volume_result={r['volume_result']}")
        print(f"      entry_reason={r['entry_reason']}  at_open_regime={r['at_open_regime']}")
        print(f"      MFE={r['mfe_pct']:.2f}%  MAE={r['mae_pct']:.2f}%  "
              f"highest_pnl=${r['highest_pnl']:.2f}")
        print()

    if cutoff <= 0:
        print("⚠️  No re-baseline cutoff found — clean classification disabled.")


if __name__ == "__main__":
    main()
