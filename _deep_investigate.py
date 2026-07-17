import sqlite3, time, json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

db = sqlite3.connect(str(Path('packages/ai-engine/data/institutional_v1.db')))
db.row_factory = sqlite3.Row

now = time.time()
yesterday = now - (24 * 3600)

# Get ALL Day 2 trades
rows = db.execute(
    'SELECT * FROM positions_archive WHERE closed_at > ? ORDER BY closed_at',
    (yesterday,)
).fetchall()

print("=" * 100)
print(f"🔍 DEEP INVESTIGATION: DAY 2 LOSS ROOT CAUSE ({len(rows)} trades)")
print("=" * 100)

# 1. EVERY SINGLE TRADE
print("\n" + "=" * 100)
print("📋 SECTION 1: EVERY DAY 2 TRADE (FULL DETAILS)")
print("=" * 100)

for i, r in enumerate(rows, 1):
    pnl = r['pnl'] or 0
    fees = r['fees'] or 0
    conf = (r['confidence'] or 0) * 100
    inst = r['institutional_score'] or 0
    entry = r['entry_price'] or 0
    sl = r['stop_loss'] or 0
    tp = r['take_profit'] or 0
    qty = r['quantity'] or 0
    lev = r['leverage'] or 10
    side = r['side'] or '?'
    sym = r['symbol'] or '?'
    regime = r['regime'] or '?'
    session = r['session'] or '?'
    exit_reason = r['exit_reason'] or '?'
    hold = r['hold_minutes'] or 0
    rr = r['risk_reward'] or 0
    outcome = r['outcome'] or '?'
    mfe = r['mfe_pct'] or 0
    mae = r['mae_pct'] or 0
    realized_r = r['realized_r'] or 0
    planned_rr = r['planned_rr'] or 0
    vol_score = r['volatility_score'] or 0
    at_open_regime = r['at_open_regime'] or '?'
    at_open_session = r['at_open_session'] or '?'
    entry_reason = r['entry_reason'] or '?'
    mss = r['mss_score'] or 0
    fvg = r['fvg_score'] or 0
    alpha_tier = r['alpha_tier'] or '?'
    alpha_score = r['alpha_score'] or 0
    opened = datetime.fromtimestamp(r['opened_at']).strftime('%H:%M') if r['opened_at'] else '?'
    closed = datetime.fromtimestamp(r['closed_at']).strftime('%H:%M') if r['closed_at'] else '?'
    
    sl_dist = abs(entry - sl) / entry * 100 if entry and sl else 0
    margin = entry * qty / lev if entry and qty and lev else 0
    
    icon = "🟢" if pnl > 0 else "🔴"
    print(f"\n{icon} #{i:02d} {side:5s} {sym:20s} | Entry: ${entry:.6f} → Exit: ${r['exit_price'] if 'exit_price' in r.keys() else '?'}")
    print(f"    PnL: ${pnl:+.2f} | Fees: ${fees:.2f} | Net: ${pnl-fees:+.2f} | R: {realized_r:+.2f}R")
    print(f"    SL: ${sl:.6f} ({sl_dist:.1f}% away) | TP: ${tp:.6f} | R:R: {rr:.1f}")
    print(f"    Conf: {conf:.0f}% | Score: {inst:.0f} | Alpha: {alpha_score:.0f} ({alpha_tier})")
    print(f"    Regime: {regime} (at open: {at_open_regime}) | Session: {session} (at open: {at_open_session})")
    print(f"    Entry reason: {entry_reason} | Exit: {exit_reason}")
    print(f"    Hold: {hold:.0f}min | MFE: {mfe:.1f}% | MAE: {mae:.1f}%")
    print(f"    MSS: {mss:.0f} | FVG: {fvg:.0f} | Vol: {vol_score:.0f} | Margin: ${margin:.2f}")

# 2. LOSS CLUSTER ANALYSIS
print("\n" + "=" * 100)
print("📊 SECTION 2: LOSS CLUSTER ANALYSIS")
print("=" * 100)

losing = [r for r in rows if (r['pnl'] or 0) <= 0]
winning = [r for r in rows if (r['pnl'] or 0) > 0]

print(f"\nTotal trades: {len(rows)} | Wins: {len(winning)} | Losses: {len(losing)}")
print(f"Win PnL: ${sum(r['pnl'] or 0 for r in winning):+.2f}")
print(f"Loss PnL: ${sum(r['pnl'] or 0 for r in losing):+.2f}")

# Time clustering
print("\n⏰ TIME CLUSTERING (when did losses happen?):")
by_hour = defaultdict(lambda: {'wins': 0, 'losses': 0, 'pnl': 0})
for r in rows:
    h = datetime.fromtimestamp(r['opened_at']).hour if r['opened_at'] else 0
    pnl = r['pnl'] or 0
    if pnl > 0:
        by_hour[h]['wins'] += 1
    else:
        by_hour[h]['losses'] += 1
    by_hour[h]['pnl'] += pnl

for h in sorted(by_hour.keys()):
    d = by_hour[h]
    total = d['wins'] + d['losses']
    wr = d['wins'] / total * 100 if total else 0
    bar = "█" * d['wins'] + "░" * d['losses']
    print(f"  {h:02d}:00  {bar}  {d['wins']}W/{d['losses']}L ({wr:.0f}% WR) ${d['pnl']:+.2f}")

# 3. REGIME ACCURACY
print("\n" + "=" * 100)
print("🎯 SECTION 3: REGIME DETECTION ACCURACY")
print("=" * 100)

regime_analysis = defaultdict(lambda: {'trades': 0, 'wins': 0, 'pnl': 0, 'sides': defaultdict(int)})
for r in rows:
    regime = r['at_open_regime'] or r['regime'] or 'unknown'
    side = r['side'] or '?'
    pnl = r['pnl'] or 0
    regime_analysis[regime]['trades'] += 1
    regime_analysis[regime]['sides'][side] += 1
    if pnl > 0:
        regime_analysis[regime]['wins'] += 1
    regime_analysis[regime]['pnl'] += pnl

for regime, data in sorted(regime_analysis.items(), key=lambda x: x[1]['pnl']):
    wr = data['wins'] / data['trades'] * 100 if data['trades'] else 0
    sides = dict(data['sides'])
    print(f"  {regime:20s} | {data['trades']:2d} trades | {wr:5.1f}% WR | ${data['pnl']:+.2f} | sides: {sides}")

# 4. EXIT REASON BREAKDOWN
print("\n" + "=" * 100)
print("🚪 SECTION 4: EXIT REASON ANALYSIS")
print("=" * 100)

exit_analysis = defaultdict(lambda: {'trades': 0, 'wins': 0, 'pnl': 0, 'avg_hold': 0})
for r in rows:
    exit_reason = r['exit_reason'] or 'unknown'
    pnl = r['pnl'] or 0
    hold = r['hold_minutes'] or 0
    exit_analysis[exit_reason]['trades'] += 1
    exit_analysis[exit_reason]['avg_hold'] += hold
    if pnl > 0:
        exit_analysis[exit_reason]['wins'] += 1
    exit_analysis[exit_reason]['pnl'] += pnl

for reason, data in sorted(exit_analysis.items(), key=lambda x: x[1]['pnl']):
    wr = data['wins'] / data['trades'] * 100 if data['trades'] else 0
    avg_hold = data['avg_hold'] / data['trades'] if data['trades'] else 0
    print(f"  {reason:35s} | {data['trades']:2d} trades | {wr:5.1f}% WR | ${data['pnl']:+.2f} | avg hold: {avg_hold:.0f}min")

# 5. CONFIDENCE vs OUTCOME
print("\n" + "=" * 100)
print("📈 SECTION 5: CONFIDENCE vs OUTCOME")
print("=" * 100)

conf_buckets = defaultdict(lambda: {'trades': 0, 'wins': 0, 'pnl': 0})
for r in rows:
    conf = (r['confidence'] or 0) * 100
    if conf >= 95:
        bucket = "95-100%"
    elif conf >= 90:
        bucket = "90-95%"
    elif conf >= 85:
        bucket = "85-90%"
    elif conf >= 80:
        bucket = "80-85%"
    elif conf >= 75:
        bucket = "75-80%"
    else:
        bucket = "<75%"
    
    pnl = r['pnl'] or 0
    conf_buckets[bucket]['trades'] += 1
    if pnl > 0:
        conf_buckets[bucket]['wins'] += 1
    conf_buckets[bucket]['pnl'] += pnl

for bucket in ["95-100%", "90-95%", "85-90%", "80-85%", "75-80%", "<75%"]:
    if bucket in conf_buckets:
        data = conf_buckets[bucket]
        wr = data['wins'] / data['trades'] * 100 if data['trades'] else 0
        print(f"  {bucket:10s} | {data['trades']:2d} trades | {wr:5.1f}% WR | ${data['pnl']:+.2f}")

# 6. SL DISTANCE ANALYSIS
print("\n" + "=" * 100)
print("📏 SECTION 6: SL DISTANCE vs OUTCOME")
print("=" * 100)

sl_buckets = defaultdict(lambda: {'trades': 0, 'wins': 0, 'pnl': 0})
for r in rows:
    entry = r['entry_price'] or 0
    sl = r['stop_loss'] or 0
    sl_dist = abs(entry - sl) / entry * 100 if entry and sl else 0
    
    if sl_dist > 8:
        bucket = ">8% (WIDE)"
    elif sl_dist > 5:
        bucket = "5-8%"
    elif sl_dist > 3:
        bucket = "3-5%"
    elif sl_dist > 1:
        bucket = "1-3%"
    else:
        bucket = "<1%"
    
    pnl = r['pnl'] or 0
    sl_buckets[bucket]['trades'] += 1
    if pnl > 0:
        sl_buckets[bucket]['wins'] += 1
    sl_buckets[bucket]['pnl'] += pnl

for bucket in [">8% (WIDE)", "5-8%", "3-5%", "1-3%", "<1%"]:
    if bucket in sl_buckets:
        data = sl_buckets[bucket]
        wr = data['wins'] / data['trades'] * 100 if data['trades'] else 0
        print(f"  {bucket:15s} | {data['trades']:2d} trades | {wr:5.1f}% WR | ${data['pnl']:+.2f}")

# 7. SIDE x DIRECTION ACCURACY
print("\n" + "=" * 100)
print("🔄 SECTION 7: SIDE x REGIME MATRIX")
print("=" * 100)

matrix = defaultdict(lambda: {'trades': 0, 'wins': 0, 'pnl': 0})
for r in rows:
    side = r['side'] or '?'
    regime = r['at_open_regime'] or r['regime'] or '?'
    key = f"{side} in {regime}"
    pnl = r['pnl'] or 0
    matrix[key]['trades'] += 1
    if pnl > 0:
        matrix[key]['wins'] += 1
    matrix[key]['pnl'] += pnl

for key, data in sorted(matrix.items(), key=lambda x: x[1]['pnl']):
    wr = data['wins'] / data['trades'] * 100 if data['trades'] else 0
    icon = "✅" if data['pnl'] > 0 else "❌"
    print(f"  {icon} {key:35s} | {data['trades']:2d} trades | {wr:5.1f}% WR | ${data['pnl']:+.2f}")

# 8. WORST TRADES DEEP DIVE
print("\n" + "=" * 100)
print("💀 SECTION 8: WORST 5 TRADES — DEEP DIVE")
print("=" * 100)

worst = sorted(rows, key=lambda r: r['pnl'] or 0)[:5]
for i, r in enumerate(worst, 1):
    entry = r['entry_price'] or 0
    sl = r['stop_loss'] or 0
    tp = r['take_profit'] or 0
    sl_dist = abs(entry - sl) / entry * 100 if entry and sl else 0
    hold = r['hold_minutes'] or 0
    
    print(f"\n💀 #{i}: {r['side']} {r['symbol']} — ${r['pnl']:+.2f}")
    print(f"   Entry: ${entry:.6f} | SL: ${sl:.6f} ({sl_dist:.1f}%) | TP: ${tp:.6f}")
    print(f"   Conf: {(r['confidence'] or 0)*100:.0f}% | Score: {r['institutional_score'] or 0:.0f}")
    print(f"   Regime: {r['at_open_regime']} | Session: {r['at_open_session']}")
    print(f"   Exit: {r['exit_reason']} | Hold: {hold:.0f}min")
    print(f"   MFE: {r['mfe_pct'] or 0:.1f}% | MAE: {r['mae_pct'] or 0:.1f}%")
    print(f"   Planned R:R: {r['planned_rr'] or 0:.1f} | Realized R: {r['realized_r'] or 0:.1f}")
    print(f"   MSS: {r['mss_score'] or 0:.0f} | FVG: {r['fvg_score'] or 0:.0f}")

# 9. FEES IMPACT
print("\n" + "=" * 100)
print("💸 SECTION 9: FEES IMPACT ANALYSIS")
print("=" * 100)

total_fees = sum(r['fees'] or 0 for r in rows)
total_pnl = sum(r['pnl'] or 0 for r in rows)
fee_pct = total_fees / abs(total_pnl) * 100 if total_pnl != 0 else 0
print(f"  Total Fees: ${total_fees:.2f}")
print(f"  Total PnL:  ${total_pnl:+.2f}")
print(f"  Fees as % of |PnL|: {fee_pct:.1f}%")
print(f"  Fees per trade: ${total_fees/len(rows):.2f}")

# Average hold time
avg_hold = sum(r['hold_minutes'] or 0 for r in rows) / len(rows)
print(f"  Average hold: {avg_hold:.0f}min")

# 10. KEY EVIDENCE SUMMARY
print("\n" + "=" * 100)
print("🔑 SECTION 10: KEY EVIDENCE SUMMARY")
print("=" * 100)

# Check for regime mismatch
short_in_bull = [(r['symbol'], r['pnl'] or 0) for r in rows if r['side'] == 'SHORT' and 'bull' in (r['at_open_regime'] or r['regime'] or '')]
long_in_bear = [(r['symbol'], r['pnl'] or 0) for r in rows if r['side'] == 'LONG' and 'bear' in (r['at_open_regime'] or r['regime'] or '')]

if short_in_bull:
    print(f"\n  ❌ EVIDENCE 1: SHORT in BULL regime — {len(short_in_bull)} trades")
    for sym, pnl in short_in_bull:
        print(f"    {sym}: ${pnl:+.2f}")

if long_in_bear:
    print(f"\n  ❌ EVIDENCE 2: LONG in BEAR regime — {len(long_in_bear)} trades")
    for sym, pnl in long_in_bear:
        print(f"    {sym}: ${pnl:+.2f}")

# Check wide SLs
wide_sl = [(r['symbol'], r['side'], abs((r['entry_price'] or 0) - (r['stop_loss'] or 0)) / (r['entry_price'] or 1) * 100, r['pnl'] or 0) for r in rows if abs((r['entry_price'] or 0) - (r['stop_loss'] or 0)) / (r['entry_price'] or 1) * 100 > 5]
if wide_sl:
    print(f"\n  ❌ EVIDENCE 3: Wide SLs (>5%) — {len(wide_sl)} trades")
    for sym, side, dist, pnl in wide_sl:
        print(f"    {side} {sym}: SL={dist:.1f}% → ${pnl:+.2f}")

# Check high confidence losers
high_conf_losers = [(r['symbol'], r['side'], (r['confidence'] or 0)*100, r['pnl'] or 0) for r in rows if (r['confidence'] or 0) >= 0.90 and (r['pnl'] or 0) < 0]
if high_conf_losers:
    print(f"\n  ❌ EVIDENCE 4: High confidence (≥90%) losers — {len(high_conf_losers)} trades")
    for sym, side, conf, pnl in high_conf_losers:
        print(f"    {side} {sym}: conf={conf:.0f}% → ${pnl:+.2f}")

# Check rapid-fire entries
timestamps = sorted([r['opened_at'] or 0 for r in rows])
rapid_pairs = []
for i in range(len(timestamps) - 1):
    diff = (timestamps[i+1] - timestamps[i]) / 60  # minutes
    if diff < 2:  # within 2 minutes
        rapid_pairs.append((timestamps[i], timestamps[i+1], diff))
if rapid_pairs:
    print(f"\n  ⚠️ EVIDENCE 5: Rapid-fire entries (<2min apart) — {len(rapid_pairs)} instances")
    for t1, t2, diff in rapid_pairs[:5]:
        print(f"    {datetime.fromtimestamp(t1).strftime('%H:%M:%S')} → {datetime.fromtimestamp(t2).strftime('%H:%M:%S')} ({diff:.1f}min)")
