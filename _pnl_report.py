import sqlite3
import time
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

db = sqlite3.connect(str(Path('packages/ai-engine/data/institutional_v1.db')))
db.row_factory = sqlite3.Row

now = time.time()
two_days_ago = now - (2 * 24 * 3600)
yesterday = now - (24 * 3600)

# Get all closed positions from last 2 days
rows = db.execute(
    'SELECT * FROM positions_archive WHERE closed_at > ? ORDER BY closed_at',
    (two_days_ago,)
).fetchall()

print("=" * 90)
print(f"📊 PnL & WIN RATE REPORT — Last 2 Days ({datetime.fromtimestamp(two_days_ago).strftime('%b %d')} → {datetime.now().strftime('%b %d')})")
print("=" * 90)
print(f"Total closed trades: {len(rows)}")
print()

if not rows:
    print("No closed trades found in positions_archive.")
    # Check signals table for recent activity
    sig_rows = db.execute(
        'SELECT * FROM signals WHERE created_at > ? ORDER BY created_at DESC LIMIT 20',
        (two_days_ago,)
    ).fetchall()
    if sig_rows:
        print(f"\nHowever, {len(sig_rows)} signals were generated. Checking signals table...")
        print(f"Sample: {dict(sig_rows[0]) if sig_rows else 'none'}")
    exit()

# Separate by day
day1_trades = [r for r in rows if r['closed_at'] > yesterday]
day2_trades = [r for r in rows if r['closed_at'] <= yesterday]

def analyze_day(trades, label):
    if not trades:
        print(f"\n{'─' * 60}")
        print(f"📅 {label}: No trades")
        return
    
    wins = [t for t in trades if (t['pnl'] or 0) > 0]
    losses = [t for t in trades if (t['pnl'] or 0) <= 0]
    
    total_pnl = sum(t['pnl'] or 0 for t in trades)
    total_fees = sum(t['fees'] or 0 for t in trades)
    win_pnl = sum(t['pnl'] or 0 for t in wins)
    loss_pnl = sum(t['pnl'] or 0 for t in losses)
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    
    avg_win = win_pnl / len(wins) if wins else 0
    avg_loss = abs(loss_pnl) / len(losses) if losses else 0
    profit_factor = win_pnl / abs(loss_pnl) if loss_pnl != 0 else float('inf')
    
    # By side
    longs = [t for t in trades if t['side'] == 'LONG']
    shorts = [t for t in trades if t['side'] == 'SHORT']
    long_pnl = sum(t['pnl'] or 0 for t in longs)
    short_pnl = sum(t['pnl'] or 0 for t in shorts)
    long_wins = len([t for t in longs if (t['pnl'] or 0) > 0])
    short_wins = len([t for t in shorts if (t['pnl'] or 0) > 0])
    
    # By regime
    regimes = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0})
    for t in trades:
        r = t['regime'] or 'unknown'
        regimes[r]['count'] += 1
        regimes[r]['pnl'] += t['pnl'] or 0
        if (t['pnl'] or 0) > 0:
            regimes[r]['wins'] += 1
    
    # By exit reason
    exits = defaultdict(lambda: {'count': 0, 'pnl': 0})
    for t in trades:
        e = t['exit_reason'] or 'unknown'
        exits[e]['count'] += 1
        exits[e]['pnl'] += t['pnl'] or 0
    
    # By confidence tier
    high_conf = [t for t in trades if (t['confidence'] or 0) >= 0.90]
    med_conf = [t for t in trades if 0.70 <= (t['confidence'] or 0) < 0.90]
    low_conf = [t for t in trades if (t['confidence'] or 0) < 0.70]
    
    print(f"\n{'─' * 60}")
    print(f"📅 {label} ({len(trades)} trades)")
    print(f"{'─' * 60}")
    print(f"  Win Rate:     {win_rate:.1f}% ({len(wins)}W / {len(losses)}L)")
    print(f"  Total PnL:    ${total_pnl:+.2f}")
    print(f"  Total Fees:   ${total_fees:.2f}")
    print(f"  Net PnL:      ${total_pnl - total_fees:+.2f}")
    print(f"  Profit Factor: {profit_factor:.2f}")
    print(f"  Avg Win:      ${avg_win:+.2f}")
    print(f"  Avg Loss:     ${-avg_loss:.2f}")
    print(f"  Expectancy:   ${total_pnl/len(trades):+.2f} per trade")
    
    print(f"\n  📈 LONG:  {len(longs)} trades | {long_wins}W/{len(longs)-long_wins}L | ${long_pnl:+.2f}")
    print(f"  📉 SHORT: {len(shorts)} trades | {short_wins}W/{len(shorts)-short_wins}L | ${short_pnl:+.2f}")
    
    print(f"\n  🎯 By Regime:")
    for r, data in sorted(regimes.items(), key=lambda x: x[1]['pnl'], reverse=True):
        wr = data['wins'] / data['count'] * 100 if data['count'] else 0
        print(f"    {r:20s} {data['count']:3d} trades | {wr:5.1f}% WR | ${data['pnl']:+.2f}")
    
    print(f"\n  🚪 By Exit Reason:")
    for e, data in sorted(exits.items(), key=lambda x: x[1]['count'], reverse=True):
        print(f"    {e:25s} {data['count']:3d} trades | ${data['pnl']:+.2f}")
    
    if high_conf:
        hc_wr = len([t for t in high_conf if (t['pnl'] or 0) > 0]) / len(high_conf) * 100
        hc_pnl = sum(t['pnl'] or 0 for t in high_conf)
        print(f"\n  🏆 High Confidence (≥90%): {len(high_conf)} trades | {hc_wr:.1f}% WR | ${hc_pnl:+.2f}")
    if med_conf:
        mc_wr = len([t for t in med_conf if (t['pnl'] or 0) > 0]) / len(med_conf) * 100
        mc_pnl = sum(t['pnl'] or 0 for t in med_conf)
        print(f"  ⚡ Medium Confidence (70-90%): {len(med_conf)} trades | {mc_wr:.1f}% WR | ${mc_pnl:+.2f}")
    
    # Top winners and losers
    sorted_by_pnl = sorted(trades, key=lambda t: t['pnl'] or 0, reverse=True)
    print(f"\n  🏆 Top Winners:")
    for t in sorted_by_pnl[:3]:
        print(f"    {t['side']:5s} {t['symbol']:20s} ${t['pnl']:+.2f} | conf={t['confidence']*100:.0f}% | {t['exit_reason']}")
    print(f"  💀 Top Losers:")
    for t in sorted_by_pnl[-3:]:
        print(f"    {t['side']:5s} {t['symbol']:20s} ${t['pnl']:+.2f} | conf={t['confidence']*100:.0f}% | {t['exit_reason']}")

# Analyze each day
analyze_day(day2_trades, f"Day 1 ({datetime.fromtimestamp(yesterday).strftime('%b %d')})")
analyze_day(day1_trades, f"Day 2 ({datetime.now().strftime('%b %d')})")

# Overall summary
print(f"\n{'=' * 90}")
print(f"📊 2-DAY TOTALS")
print(f"{'=' * 90}")
total_pnl = sum(r['pnl'] or 0 for r in rows)
total_fees = sum(r['fees'] or 0 for r in rows)
wins = len([r for r in rows if (r['pnl'] or 0) > 0])
losses = len([r for r in rows if (r['pnl'] or 0) <= 0])
wr = wins / len(rows) * 100 if rows else 0
pf = sum(r['pnl'] or 0 for r in rows if (r['pnl'] or 0) > 0) / abs(sum(r['pnl'] or 0 for r in rows if (r['pnl'] or 0) < 0)) if any((r['pnl'] or 0) < 0 for r in rows) else float('inf')

print(f"  Trades:      {len(rows)} ({wins}W / {losses}L)")
print(f"  Win Rate:    {wr:.1f}%")
print(f"  Total PnL:   ${total_pnl:+.2f}")
print(f"  Total Fees:  ${total_fees:.2f}")
print(f"  Net PnL:     ${total_pnl - total_fees:+.2f}")
print(f"  Profit Factor: {pf:.2f}")
print(f"  Balance:     $9,999.59 → ${9999.59 + total_pnl:.2f}")
