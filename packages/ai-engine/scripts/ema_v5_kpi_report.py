#!/usr/bin/env python3
"""
EMA V5 Performance KPI Dashboard — Terminal-based monitoring script.

Run periodically to track:
  - Signal frequency
  - Win rate
  - Profit factor
  - Expectancy
  - Average R multiple
  - Maximum drawdown
  - Average confidence of winners vs losers
  - Directional bias
"""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timezone

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "ema_v5_calibration.db"
TRADES_DB = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


def run_kpi_report():
    """Generate comprehensive KPI report."""
    print()
    print("═" * 70)
    print("  EMA V5 PERFORMANCE KPI REPORT")
    print(f"  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("═" * 70)

    # ═══ CALIBRATION DATABASE ═══
    if not DB_PATH.exists():
        print("  ⚠️  Calibration database not found")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # ── 1. SIGNAL FREQUENCY ──
    cur.execute("SELECT COUNT(*) FROM candidates")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM candidates WHERE passed=1")
    passed = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM candidates WHERE outcome_tracked=1")
    tracked = cur.fetchone()[0]

    print()
    print("═══ 1. SIGNAL FREQUENCY ═══")
    print(f"  Total candidates evaluated: {total}")
    print(f"  Passed confidence gate:     {passed} ({passed/max(total,1)*100:.1f}%)")
    print(f"  Outcome tracked:            {tracked}")

    # ── 2. WIN RATE ──
    cur.execute("""
        SELECT COUNT(*), SUM(CASE WHEN return_pct > 0 THEN 1 ELSE 0 END)
        FROM candidates WHERE outcome_tracked=1 AND return_pct IS NOT NULL
    """)
    row = cur.fetchone()
    n_trades = row[0]
    n_wins = row[1] or 0
    win_rate = n_wins / max(n_trades, 1) * 100

    print()
    print("═══ 2. WIN RATE ═══")
    print(f"  Trades with outcomes: {n_trades}")
    print(f"  Wins:                 {n_wins} ({win_rate:.1f}%)")
    print(f"  Losses:               {n_trades - n_wins} ({100 - win_rate:.1f}%)")

    # ── 3. PROFIT FACTOR ──
    cur.execute("""
        SELECT 
            SUM(CASE WHEN return_pct > 0 THEN return_pct ELSE 0 END) as gross_profit,
            SUM(CASE WHEN return_pct <= 0 THEN ABS(return_pct) ELSE 0 END) as gross_loss,
            AVG(return_pct) as avg_return
        FROM candidates WHERE outcome_tracked=1 AND return_pct IS NOT NULL
    """)
    row = cur.fetchone()
    gp = row[0] or 0
    gl = row[1] or 0.001
    pf = gp / gl
    avg_ret = row[2] or 0

    print()
    print("═══ 3. PROFIT FACTOR ═══")
    print(f"  Gross Profit: {gp:+.3f}%")
    print(f"  Gross Loss:   {gl:+.3f}%")
    print(f"  Profit Factor: {pf:.2f} {'✅' if pf >= 1.0 else '❌'}")
    print(f"  Expectancy:    {avg_ret:+.3f}% per trade")

    # ── 4. AVERAGE R MULTIPLE ──
    cur.execute("""
        SELECT 
            AVG(CASE WHEN return_pct > 0 THEN return_pct ELSE 0 END) as avg_win,
            AVG(CASE WHEN return_pct <= 0 THEN return_pct ELSE 0 END) as avg_loss,
            AVG(mfe) as avg_mfe,
            AVG(mae) as avg_mae
        FROM candidates WHERE outcome_tracked=1 AND return_pct IS NOT NULL
    """)
    row = cur.fetchone()
    avg_win = row[0] or 0
    avg_loss = abs(row[1] or 0)
    avg_rr = avg_win / max(avg_loss, 0.001)

    print()
    print("═══ 4. R MULTIPLE ═══")
    print(f"  Avg Win:  {avg_win:+.3f}%")
    print(f"  Avg Loss: {-avg_loss:+.3f}%")
    print(f"  Avg R:R:  {avg_rr:.2f}")
    print(f"  Avg MFE:  {row[2]:+.3f}% (favorable excursion)")
    print(f"  Avg MAE:  {row[3]:+.3f}% (adverse excursion)")

    # ── 5. MAXIMUM DRAWDOWN ──
    cur.execute("""
        SELECT return_pct FROM candidates 
        WHERE outcome_tracked=1 AND return_pct IS NOT NULL
        ORDER BY timestamp
    """)
    returns = [r[0] for r in cur.fetchall()]
    if returns:
        equity = 100.0
        peak = 100.0
        max_dd = 0
        for r in returns:
            equity *= (1 + r / 100)
            peak = max(peak, equity)
            dd = (peak - equity) / peak * 100
            max_dd = max(max_dd, dd)
    else:
        max_dd = 0

    print()
    print("═══ 5. MAXIMUM DRAWDOWN ═══")
    print(f"  Max Drawdown: {max_dd:.2f}%")

    # ── 6. CONFIDENCE: WINNERS vs LOSERS ──
    cur.execute("""
        SELECT 
            CASE WHEN return_pct > 0 THEN 'WINNERS' ELSE 'LOSERS' END as outcome,
            COUNT(*) as cnt,
            AVG(confidence) as avg_conf,
            AVG(trend_score) as avg_trend,
            AVG(volume_score) as avg_vol,
            AVG(candle_score) as avg_candle
        FROM candidates 
        WHERE outcome_tracked=1 AND return_pct IS NOT NULL
        GROUP BY outcome
    """)
    rows = {r['outcome']: dict(r) for r in cur.fetchall()}

    print()
    print("═══ 6. CONFIDENCE: WINNERS vs LOSERS ═══")
    w = rows.get('WINNERS', {})
    l = rows.get('LOSERS', {})
    print(f"  Winners  N={w.get('cnt',0):>4d} Conf={w.get('avg_conf',0):.1f} Trend={w.get('avg_trend',0):.1f} Vol={w.get('avg_vol',0):.1f} Candle={w.get('avg_candle',0):.1f}")
    print(f"  Losers   N={l.get('cnt',0):>4d} Conf={l.get('avg_conf',0):.1f} Trend={l.get('avg_trend',0):.1f} Vol={l.get('avg_vol',0):.1f} Candle={l.get('avg_candle',0):.1f}")

    # ── 7. DIRECTIONAL BIAS ──
    cur.execute("""
        SELECT 
            direction,
            COUNT(*) as cnt,
            SUM(CASE WHEN return_pct > 0 THEN 1 ELSE 0 END) as wins,
            AVG(return_pct) as avg_return,
            AVG(confidence) as avg_conf
        FROM candidates 
        WHERE outcome_tracked=1 AND return_pct IS NOT NULL
        GROUP BY direction
    """)
    print()
    print("═══ 7. DIRECTIONAL BIAS ═══")
    for row in cur.fetchall():
        wr = (row['wins'] or 0) / max(row['cnt'], 1) * 100
        print(f"  {row['direction']:<8s} N={row['cnt']:>4d} WR={wr:.1f}% Avg={row['avg_return']:+.3f}% Conf={row['avg_conf']:.1f}")

    # ── 8. THRESHOLD SIMULATION ──
    cur.execute("SELECT confidence, return_pct FROM candidates WHERE outcome_tracked=1 AND return_pct IS NOT NULL")
    all_rows = cur.fetchall()

    print()
    print("═══ 8. THRESHOLD SIMULATION ═══")
    print(f"  {'Threshold':>10s} {'N':>6s} {'WinRate':>8s} {'PF':>6s} {'AvgReturn':>10s} {'Verdict':>10s}")
    print(f"  {'─'*10} {'─'*6} {'─'*8} {'─'*6} {'─'*10} {'─'*10}")
    for threshold in [80, 82, 84, 86, 88, 90, 92, 95]:
        accepted = [(c, r) for c, r in all_rows if c >= threshold]
        if accepted and len(accepted) >= 10:
            wins = [r for _, r in accepted if r > 0]
            losses = [r for _, r in accepted if r <= 0]
            wr = len(wins) / len(accepted) * 100
            gp = sum(wins) if wins else 0
            gl = abs(sum(losses)) if losses else 0.001
            pf_val = gp / gl
            avg = sum(r for _, r in accepted) / len(accepted)
            verdict = "✅ BEST" if pf_val >= 1.0 else ("⚠️ MARGINAL" if pf_val >= 0.8 else "❌ NEGATIVE")
            print(f"  {threshold:>10d} {len(accepted):>6d} {wr:>7.1f}% {pf_val:>6.2f} {avg:>+9.3f}% {verdict}")
        elif accepted:
            print(f"  {threshold:>10d} {len(accepted):>6d}  (insufficient sample)")
        else:
            print(f"  {threshold:>10d}      0")

    conn.close()

    print()
    print("═" * 70)
    print("  RECOMMENDATION:")
    print("  Collect 100+ trades before adjusting thresholds.")
    print("  Current data suggests the confidence formula")
    print("  may not be predictive of actual returns.")
    print("═" * 70)
    print()


if __name__ == "__main__":
    run_kpi_report()
