#!/usr/bin/env python3
"""Last 48 signals performance report with PnL and win rate."""
import sqlite3
import json

db = sqlite3.connect('data/institutional_v1.db', timeout=10)

# ── 1. ARCHIVE (closed trades) ──
archive = db.execute('''
    SELECT symbol, side, entry_price, stop_loss, pnl, confidence,
           institutional_score, regime, hold_minutes, exit_reason, risk_reward
    FROM positions_archive ORDER BY closed_at DESC
''').fetchall()

print(f'=== CLOSED TRADES: {len(archive)} ===')
print()

total_pnl = 0.0
wins = losses = be = longs = shorts = 0
long_pnl = short_pnl = winning_pnl = losing_pnl = 0.0

for r in archive:
    sym, side, entry, sl, pnl, conf, inst, regime, hold, exit_r, rr = r
    pv = pnl or 0
    total_pnl += pv

    if pv > 0:
        wins += 1; winning_pnl += pv; tag = '✅'
    elif pv < 0:
        losses += 1; losing_pnl += pv; tag = '❌'
    else:
        be += 1; tag = '➖'

    if side == 'LONG':
        longs += 1; long_pnl += pv
    else:
        shorts += 1; short_pnl += pv

    print(f'  {tag} {sym:14s} {side:5s} entry={entry:10.5f} pnl=${pv:+8.2f} regime={regime or "?":15s} hold={hold or 0:.0f}m exit={exit_r or "?"}')

# ── 2. LIVE (bridge) ──
print()
print('=== LIVE POSITIONS (bridge) ===')
try:
    with open('data/bridge/positions.json') as f:
        d = json.load(f)
    pos = d if isinstance(d, list) else d.get('positions', [])
    if not pos:
        print('  (none)')
    for p in pos:
        sym = p.get('symbol', '?')
        side = p.get('side', '?')
        entry = p.get('entry_price', 0)
        sl = p.get('stop_loss', 0)
        conf = p.get('confidence', 0)
        inst = p.get('institutional_score', 0)
        regime = p.get('market_regime', '?')
        sl_dist = abs(entry - sl) / entry * 100 if entry and sl else 0
        print(f'  🟡 {sym:14s} {side:5s} entry={entry:10.4f} sl_dist={sl_dist:.2f}% conf={conf:.0%} inst={inst:.1f} regime={regime}')
except Exception as e:
    print(f'  Error: {e}')

# ── 3. LAST 48 SIGNALS ──
print()
print('=== LAST 48 SIGNALS ===')
sigs = db.execute('''
    SELECT symbol, side, confidence, institutional_score, market_regime,
           status, timestamp, entry, stop_loss, take_profit
    FROM signals ORDER BY timestamp DESC LIMIT 48
''').fetchall()

emitted = blocked = 0
for s in sigs:
    sym, side, conf, inst, regime, status, ts, entry, sl, tp = s
    if status == 'emitted':
        emitted += 1; icon = '📡'
    else:
        blocked += 1; icon = '🚫'
    conf_s = f'{conf*100:.0f}%' if conf and 0 < conf <= 1 else f'{conf}' if conf else '?'
    inst_s = f'{inst:.1f}' if inst else '?'
    print(f'  {icon} {sym:14s} {side:5s} conf={conf_s:5s} inst={inst_s:5s} regime={regime or "?":15s} status={status}')

# ── SUMMARY ──
t = wins + losses + be
wr = wins / t * 100 if t else 0
aw = winning_pnl / wins if wins else 0
al = losing_pnl / losses if losses else 0
pf = abs(winning_pnl / losing_pnl) if losing_pnl else float('inf')
wl_ratio = abs(aw / al) if al else float('inf')

print()
print('=' * 70)
print(f'  SUMMARY ({t} closed trades)')
print(f'  ✅ Wins:       {wins} ({wr:.1f}%)')
print(f'  ❌ Losses:     {losses} ({100 - wr:.1f}%)')
print(f'  ➖ Break-even: {be}')
print(f'  💰 Total PnL:  ${total_pnl:+.2f}')
print(f'  📈 Avg Win:    ${aw:+.2f}')
print(f'  📉 Avg Loss:   ${al:+.2f}')
print(f'  ⚖️  Profit Factor: {pf:.2f}')
print(f'  📊 Win/Loss Ratio: {wl_ratio:.2f}')
print()
print(f'  🔵 LONG:  {longs} trades  PnL=${long_pnl:+.2f}')
print(f'  🔴 SHORT: {shorts} trades  PnL=${short_pnl:+.2f}')
print()
print(f'  📡 SIGNALS (last 48): {emitted} emitted, {blocked} blocked')

db.close()
