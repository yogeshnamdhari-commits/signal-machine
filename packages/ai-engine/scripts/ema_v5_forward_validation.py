#!/usr/bin/env python3
"""
EMA V5 Forward Validation — Live Trade Monitoring.

Tracks live forward testing of V2 confidence model.
Validates whether the model performs as well in production
as it did on historical data.

Milestones:
  - 50 trades: Initial signal (PF > 1.0 = promising)
  - 100 trades: First checkpoint (PF > 1.2 = strong)
  - 200 trades: Confirmation (PF > 1.3 = production ready)
  - 500 trades: Full validation (statistically significant)
"""
import sqlite3
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "ema_v5_calibration.db"
TRADES_DB = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


def run_forward_validation():
    """Run forward validation analysis on live trades."""
    print()
    print("=" * 70)
    print("  EMA V5 FORWARD VALIDATION — Live Trade Monitoring")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 70)

    # ═══ LOAD LIVE TRADES ═══
    if not TRADES_DB.exists():
        print("  Trades database not found")
        return

    conn = sqlite3.connect(str(TRADES_DB))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # Get all closed EMA V5 trades
    cur.execute("""
        SELECT 
            symbol, side, entry_price, pnl, 
            opened_at, closed_at, regime, confidence,
            take_profit, stop_loss, strategy_version,
            exit_reason, realized_r, mfe_pct, mae_pct
        FROM positions 
        WHERE status='closed' AND strategy_version='ema_v5'
        ORDER BY closed_at DESC
    """)
    trades = [dict(r) for r in cur.fetchall()]

    # Get all open EMA V5 trades
    cur.execute("""
        SELECT 
            symbol, side, entry_price, quantity,
            opened_at, regime, confidence, strategy_version
        FROM positions 
        WHERE status='open' AND strategy_version='ema_v5'
    """)
    open_trades = [dict(r) for r in cur.fetchall()]

    conn.close()

    print()
    print("═══ TRADE SUMMARY ═══")
    print(f"  Closed trades: {len(trades)}")
    print(f"  Open trades:   {len(open_trades)}")
    print(f"  Total:         {len(trades) + len(open_trades)}")

    if not trades:
        print()
        print("  No closed trades yet. Forward testing has not started.")
        print("  The engine needs to close trades before we can validate.")
        
        # Show open trades as context
        if open_trades:
            print()
            print("═══ OPEN TRADES (awaiting closure) ═══")
            for t in open_trades:
                print(f"  {t['symbol']:<12s} {t['side']:<6s} Entry={t['entry_price']:.4f} Conf={t.get('confidence', 0):.1f}")
            print()
            print(f"  These {len(open_trades)} trades will provide the first validation data")
            print("  once they hit TP/SL or are closed by the trade manager.")
        return

    # ═══ PERFORMANCE METRICS ═══
    print()
    print("═══ PERFORMANCE METRICS ═══")

    # Win rate
    wins = [t for t in trades if (t['pnl'] or 0) > 0]
    losses = [t for t in trades if (t['pnl'] or 0) <= 0]
    win_rate = len(wins) / len(trades) * 100

    # Profit factor
    gross_profit = sum(t['pnl'] or 0 for t in wins)
    gross_loss = abs(sum(t['pnl'] or 0 for t in losses))
    profit_factor = gross_profit / max(gross_loss, 0.01)

    # Expectancy
    avg_pnl = sum(t['pnl'] or 0 for t in trades) / len(trades)

    # Average win/loss
    avg_win = sum(t['pnl'] or 0 for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t['pnl'] or 0 for t in losses) / len(losses) if losses else 0

    # R:R ratio
    avg_rr = avg_win / max(abs(avg_loss), 0.01)

    print(f"  Win Rate:       {win_rate:.1f}%")
    print(f"  Profit Factor:  {profit_factor:.2f} {'✅' if profit_factor >= 1.3 else '⚠️' if profit_factor >= 1.0 else '❌'}")
    print(f"  Expectancy:     ${avg_pnl:+.2f} per trade {'✅' if avg_pnl > 0 else '❌'}")
    print(f"  Avg Win:        ${avg_win:+.2f}")
    print(f"  Avg Loss:       ${avg_loss:+.2f}")
    print(f"  Avg R:R:        {avg_rr:.2f}")

    # ═══ DIRECTIONAL BREAKDOWN ═══
    print()
    print("═══ DIRECTIONAL BREAKDOWN ═══")
    for side in ['LONG', 'SHORT']:
        side_trades = [t for t in trades if t['side'] == side]
        if side_trades:
            side_wins = [t for t in side_trades if (t['pnl'] or 0) > 0]
            side_wr = len(side_wins) / len(side_trades) * 100
            side_pnl = sum(t['pnl'] or 0 for t in side_trades)
            side_avg = side_pnl / len(side_trades)
            print(f"  {side}: N={len(side_trades)} WR={side_wr:.1f}% Total=${side_pnl:+.2f} Avg=${side_avg:+.2f}")

    # ═══ CONFIDENCE ANALYSIS ═══
    print()
    print("═══ CONFIDENCE ANALYSIS ═══")
    conf_trades = [t for t in trades if t.get('confidence')]
    if conf_trades:
        win_confs = [t['confidence'] for t in conf_trades if (t['pnl'] or 0) > 0]
        loss_confs = [t['confidence'] for t in conf_trades if (t['pnl'] or 0) <= 0]

        avg_conf_all = sum(t['confidence'] for t in conf_trades) / len(conf_trades)
        avg_conf_win = sum(win_confs) / len(win_confs) if win_confs else 0
        avg_conf_loss = sum(loss_confs) / len(loss_confs) if loss_confs else 0

        print(f"  Avg confidence (all):    {avg_conf_all:.1f}")
        print(f"  Avg confidence (wins):   {avg_conf_win:.1f}")
        print(f"  Avg confidence (losses): {avg_conf_loss:.1f}")
        print(f"  Confidence correlation:  {'✅ Higher conf = better' if avg_conf_win > avg_conf_loss else '⚠️ No clear correlation'}")

    # ═══ VALIDATION MILESTONES ═══
    print()
    print("═══ VALIDATION MILESTONES ═══")
    n = len(trades)

    milestones = [
        (50, "Initial signal"),
        (100, "First checkpoint"),
        (200, "Confirmation"),
        (500, "Full validation"),
    ]

    for target, desc in milestones:
        if n >= target:
            # Calculate metrics for first N trades
            subset = trades[:target]
            subset_wins = [t for t in subset if (t['pnl'] or 0) > 0]
            subset_wr = len(subset_wins) / len(subset) * 100
            subset_gp = sum(t['pnl'] or 0 for t in subset_wins)
            subset_gl = abs(sum(t['pnl'] or 0 for t in subset if (t['pnl'] or 0) <= 0))
            subset_pf = subset_gp / max(subset_gl, 0.01)

            status = "✅ PASSED" if subset_pf >= 1.3 else ("⚠️ MARGINAL" if subset_pf >= 1.0 else "❌ FAILED")
            print(f"  {target} trades ({desc}): {status} PF={subset_pf:.2f} WR={subset_wr:.1f}%")
        else:
            remaining = target - n
            print(f"  {target} trades ({desc}): ⏳ {remaining} trades remaining")

    # ═══ RECENT TRADES ═══
    print()
    print("═══ RECENT TRADES (last 10) ═══")
    for t in trades[:10]:
        pnl = t['pnl'] or 0
        icon = "✅" if pnl > 0 else "❌"
        conf = t.get('confidence', 0) or 0
        print(f"  {icon} {t['symbol']:<12s} {t['side']:<6s} PnL=${pnl:>+8.2f} Conf={conf:.1f}")

    # ═══ FORWARD TESTING STATUS ═══
    print()
    print("═══ FORWARD TESTING STATUS ═══")
    if n < 50:
        print(f"  Phase: COLLECTING DATA ({n}/50 trades)")
        print(f"  Status: Insufficient sample size for meaningful validation")
        print(f"  Action: Continue running, do not adjust parameters")
    elif n < 100:
        print(f"  Phase: INITIAL VALIDATION ({n}/100 trades)")
        if profit_factor >= 1.0:
            print(f"  Status: PROMISING — PF={profit_factor:.2f} (positive expectancy)")
            print(f"  Action: Continue collecting, monitor for consistency")
        else:
            print(f"  Status: CONCERNING — PF={profit_factor:.2f} (negative expectancy)")
            print(f"  Action: Investigate signal quality, consider parameter adjustment")
    elif n < 200:
        print(f"  Phase: CONFIRMATION ({n}/200 trades)")
        if profit_factor >= 1.2:
            print(f"  Status: STRONG — PF={profit_factor:.2f}")
            print(f"  Action: Prepare for production scaling")
        elif profit_factor >= 1.0:
            print(f"  Status: MARGINAL — PF={profit_factor:.2f}")
            print(f"  Action: Continue monitoring, optimize weak signals")
        else:
            print(f"  Status: WEAK — PF={profit_factor:.2f}")
            print(f"  Action: Re-evaluate strategy parameters")
    else:
        print(f"  Phase: FULL VALIDATION ({n} trades)")
        if profit_factor >= 1.3:
            print(f"  Status: PRODUCTION READY — PF={profit_factor:.2f}")
            print(f"  Action: Scale position sizing, expand symbol universe")
        elif profit_factor >= 1.0:
            print(f"  Status: VIABLE — PF={profit_factor:.2f}")
            print(f"  Action: Optimize parameters, focus on high-PF symbols")
        else:
            print(f"  Status: NEEDS IMPROVEMENT — PF={profit_factor:.2f}")
            print(f"  Action: Re-evaluate confidence model weights")

    print()
    print("=" * 70)


if __name__ == "__main__":
    run_forward_validation()
