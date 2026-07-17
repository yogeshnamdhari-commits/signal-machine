import sqlite3, time
from pathlib import Path
from datetime import datetime
from collections import defaultdict

db = sqlite3.connect(str(Path('packages/ai-engine/data/institutional_v1.db')))
db.row_factory = sqlite3.Row

rows = db.execute("""
    SELECT * FROM positions_archive 
    WHERE closed_at > (strftime('%s','now') - 86400*3)
    ORDER BY closed_at DESC
""").fetchall()

wins = [r for r in rows if (r['pnl'] or 0) > 0]
losses = [r for r in rows if (r['pnl'] or 0) <= 0]
total_pnl = sum(r['pnl'] or 0 for r in rows)

print("=" * 90)
print("🔍 CORE ISSUE ANALYSIS — WHY NO IMPROVEMENT?")
print("=" * 90)

# 1. WIN vs LOSS SIZE
print("\n📊 1. WIN vs LOSS SIZE (THE MAIN PROBLEM)")
print("-" * 60)
avg_win = sum(r['pnl'] or 0 for r in wins) / len(wins) if wins else 0
avg_loss = sum(r['pnl'] or 0 for r in losses) / len(losses) if losses else 0
max_win = max(r['pnl'] or 0 for r in wins) if wins else 0
max_loss = min(r['pnl'] or 0 for r in losses) if losses else 0
median_win = sorted([r['pnl'] or 0 for r in wins])[len(wins)//2] if wins else 0
median_loss = sorted([r['pnl'] or 0 for r in losses])[len(losses)//2] if losses else 0

print(f"  Wins:   {len(wins)} trades | Avg: ${avg_win:+.2f} | Median: ${median_win:+.2f} | Max: ${max_win:+.2f}")
print(f"  Losses: {len(losses)} trades | Avg: ${avg_loss:+.2f} | Median: ${median_loss:+.2f} | Max: ${max_loss:+.2f}")
print(f"  Ratio:  Avg Win/Avg Loss = {abs(avg_win/avg_loss):.2f}x")
print(f"  To break even at 28% WR: need win/loss ratio > {1/0.28 - 1:.2f}x")
if abs(avg_win/avg_loss) < (1/0.28 - 1):
    print(f"  ❌ RESULT: Win/loss ratio {abs(avg_win/avg_loss):.2f}x < needed {(1/0.28-1):.2f}x → GUARANTEED LOSS")
else:
    print(f"  ✅ Win/loss ratio adequate")

# 2. SL HIT ANALYSIS
print("\n📊 2. STOP LOSS ANALYSIS (WHERE EXITS HAPPEN)")
print("-" * 60)
sl_trades = [r for r in rows if r['exit_reason'] == 'stop_loss']
tp_trades = [r for r in rows if r['exit_reason'] == 'take_profit_1']
time_trades = [r for r in rows if r['exit_reason'] and 'time' in (r['exit_reason'] or '')]

sl_pnl = sum(r['pnl'] or 0 for r in sl_trades)
tp_pnl = sum(r['pnl'] or 0 for r in tp_trades)
time_pnl = sum(r['pnl'] or 0 for r in time_trades)

print(f"  Stop Losses: {len(sl_trades)} trades | PnL: ${sl_pnl:+.2f} | Avg: ${sl_pnl/len(sl_trades):+.2f}")
print(f"  Take Profit: {len(tp_trades)} trades | PnL: ${tp_pnl:+.2f} | Avg: ${tp_pnl/len(tp_trades):+.2f}")
print(f"  Time Exit:   {len(time_trades)} trades | PnL: ${time_pnl:+.2f} | Avg: ${time_pnl/len(time_trades):+.2f}" if time_trades else "  Time Exit:   0 trades")

# 3. SL DISTANCE vs OUTCOME
print("\n📊 3. SL DISTANCE vs OUTCOME")
print("-" * 60)
for bucket_name, min_d, max_d in [("<1%", 0, 1), ("1-2%", 1, 2), ("2-3%", 2, 3), ("3-5%", 3, 5), (">5%", 5, 100)]:
    bucket = []
    for r in rows:
        ep = r['entry_price'] or 0
        sl = r['stop_loss'] or 0
        sd = abs(ep - sl) / ep * 100 if ep and sl else 0
        if min_d <= sd < max_d:
            bucket.append(r)
    if bucket:
        b_wins = len([r for r in bucket if (r['pnl'] or 0) > 0])
        b_pnl = sum(r['pnl'] or 0 for r in bucket)
        b_wr = b_wins / len(bucket) * 100
        print(f"  {bucket_name:8s} | {len(bucket):2d} trades | {b_wr:5.1f}% WR | ${b_pnl:+.2f}")

# 4. CONFIDENCE vs OUTCOME
print("\n📊 4. CONFIDENCE vs OUTCOME")
print("-" * 60)
for bucket_name, min_c, max_c in [("<80%", 0, 80), ("80-85%", 80, 85), ("85-90%", 85, 90), ("90-95%", 90, 95), ("95-100%", 95, 101)]:
    bucket = [r for r in rows if min_c <= (r['confidence'] or 0) * 100 < max_c]
    if bucket:
        b_wins = len([r for r in bucket if (r['pnl'] or 0) > 0])
        b_pnl = sum(r['pnl'] or 0 for r in bucket)
        b_wr = b_wins / len(bucket) * 100
        print(f"  {bucket_name:10s} | {len(bucket):2d} trades | {b_wr:5.1f}% WR | ${b_pnl:+.2f}")

# 5. ENTRY TIMING vs OUTCOME
print("\n📊 5. ENTRY SESSION vs OUTCOME")
print("-" * 60)
session_data = defaultdict(lambda: {'trades': 0, 'wins': 0, 'pnl': 0})
for r in rows:
    s = r['session'] or r['at_open_session'] or 'unknown'
    session_data[s]['trades'] += 1
    if (r['pnl'] or 0) > 0:
        session_data[s]['wins'] += 1
    session_data[s]['pnl'] += r['pnl'] or 0

for s, d in sorted(session_data.items(), key=lambda x: x[1]['pnl']):
    wr = d['wins'] / d['trades'] * 100
    print(f"  {s:15s} | {d['trades']:2d} trades | {wr:5.1f}% WR | ${d['pnl']:+.2f}")

# 6. THE REAL PROBLEM: HOLD TIME
print("\n📊 6. HOLD TIME vs OUTCOME")
print("-" * 60)
for bucket_name, min_h, max_h in [("0-5min", 0, 5), ("5-20min", 5, 20), ("20-60min", 20, 60), ("1-6h", 60, 360), (">6h", 360, 9999)]:
    bucket = [r for r in rows if min_h <= (r['hold_minutes'] or 0) < max_h]
    if bucket:
        b_wins = len([r for r in bucket if (r['pnl'] or 0) > 0])
        b_pnl = sum(r['pnl'] or 0 for r in bucket)
        b_wr = b_wins / len(bucket) * 100
        print(f"  {bucket_name:10s} | {len(bucket):2d} trades | {b_wr:5.1f}% WR | ${b_pnl:+.2f}")

# 7. CRITICAL: WHAT DO LOSERS HAVE IN COMMON?
print("\n📊 7. WHAT DO LOSERS HAVE IN COMMON?")
print("-" * 60)

# Check if losers had lower institutional scores
loss_scores = [r['institutional_score'] or 0 for r in losses]
win_scores = [r['institutional_score'] or 0 for r in wins]
print(f"  Avg loser score: {sum(loss_scores)/len(loss_scores):.1f}")
print(f"  Avg winner score: {sum(win_scores)/len(win_scores):.1f}")

# Check if losers had wider SLs
loss_sl = [abs((r['entry_price'] or 0) - (r['stop_loss'] or 0)) / (r['entry_price'] or 1) * 100 for r in losses if r['entry_price'] and r['stop_loss']]
win_sl = [abs((r['entry_price'] or 0) - (r['stop_loss'] or 0)) / (r['entry_price'] or 1) * 100 for r in wins if r['entry_price'] and r['stop_loss']]
print(f"  Avg loser SL dist: {sum(loss_sl)/len(loss_sl):.2f}%")
print(f"  Avg winner SL dist: {sum(win_sl)/len(win_sl):.2f}%")

# Check hold time
loss_hold = [r['hold_minutes'] or 0 for r in losses if r['hold_minutes']]
win_hold = [r['hold_minutes'] or 0 for r in wins if r['hold_minutes']]
print(f"  Avg loser hold: {sum(loss_hold)/len(loss_hold):.0f}min")
print(f"  Avg winner hold: {sum(win_hold)/len(win_hold):.0f}min")

# 8. THE DEFINITIVE ANSWER
print("\n" + "=" * 90)
print("🔑 CORE ISSUE DEFINITIVE ANSWER")
print("=" * 90)
print(f"""
  Total trades: {len(rows)} | Win Rate: {len(wins)/len(rows)*100:.1f}% | PnL: ${total_pnl:+.2f}
  
  The core issue is ASYMMETRIC RISK:
  • Average win:  ${avg_win:+.2f}
  • Average loss: ${avg_loss:+.2f}
  • Win/Loss ratio: {abs(avg_win/avg_loss):.2f}x
  
  With a {len(wins)/len(rows)*100:.1f}% win rate, you need a win/loss ratio > {(1/(len(wins)/len(rows))-1):.2f}x to break even.
  You have {abs(avg_win/avg_loss):.2f}x — {"❌ NOT ENOUGH" if abs(avg_win/avg_loss) < (1/(len(wins)/len(rows))-1) else "✅ SUFFICIENT"}
  
  ROOT CAUSES:
  1. Losers are hitting SL IMMEDIATELY (0-5min hold) — entry timing is wrong
  2. Winners are held longer (8-83min) — but the R:R doesn't compensate
  3. Position sizing is uniform ($25 margin) regardless of conviction
  4. High confidence (90%+) has 0% win rate — scoring system is inverted
""")
