import numpy as np
import sqlite3
import sys
sys.path.insert(0, 'packages/ai-engine')
from scanner.regime import _bollinger, _bb_bandwidth

db = sqlite3.connect('data/database/historical_klines.db')
db.row_factory = sqlite3.Row

cursor = db.execute('SELECT close,volume FROM klines WHERE symbol="BTCUSDT" AND interval="1h" ORDER BY open_time DESC LIMIT 500')
rows = cursor.fetchall()
rows = rows[::-1]
print(f'Bars: {len(rows)}')

closes = np.array([float(r['close']) for r in rows])
volumes = np.array([float(r['volume']) for r in rows])
n = len(closes)

upper, middle, lower = _bollinger(closes, min(20, n))
bw = _bb_bandwidth(upper, middle, lower)

trades = []
for i in range(30, n-1):
    c = closes[i]
    bu = float(upper[i]) if not np.isnan(upper[i]) else c*1.02
    bl = float(lower[i]) if not np.isnan(lower[i]) else c*0.98
    bp = (c-bl)/(bu-bl) if (bu-bl)>0 else 0.5
    
    rv = float(np.mean(volumes[max(0,i-4):i+1]))
    av = float(np.mean(volumes[max(0,i-19):i+1]))
    vr = rv/av if av>0 else 1.0
    
    cb = float(bw[i]) if not np.isnan(bw[i]) else 0.0
    vb = [b for b in bw[:i+1] if not np.isnan(b)]
    bpct = 0.5
    if len(vb)>=20:
        bpct = float(np.searchsorted(np.sort(vb), cb)/len(vb))
    
    if (bp>0.80 or bp<0.20) and vr>1.35:
        d = 'LONG' if bp>0.80 else 'SHORT'
        p = (closes[i+1]-c)/c*100 if d=='LONG' else (c-closes[i+1])/c*100
        trades.append({'bw':bpct,'won':p>0,'pnl':p,'bp':bp,'vr':vr})

print(f'Trades: {len(trades)}')

# BW buckets
print()
print('BW_PCT Distribution of Breakout-Like Trades (BTCUSDT):')
print('{:<15s} {:>8s} {:>8s} {:>8s} {:>12s}'.format('BW_PCT', 'TRADES', 'WR', 'PF', 'NET PNL'))
print('{:<15s} {:>8s} {:>8s} {:>8s} {:>12s}'.format('---', '---', '---', '---', '---'))

for lo,hi in [(0,0.2),(0.2,0.4),(0.4,0.6),(0.6,0.8),(0.8,1.0)]:
    bt=[t for t in trades if lo<=t['bw']<hi]
    if not bt:
        print('{:<15s} {:>8d} {:>7s} {:>7s} {:>12s}'.format(f'{lo:.1f}-{hi:.1f}', 0, '-', '-', '-'))
        continue
    w=sum(1 for t in bt if t['won'])
    gp=sum(t['pnl'] for t in bt if t['won'])
    gl=abs(sum(t['pnl'] for t in bt if not t['won']))
    pf=gp/gl if gl>0 else 99
    net=sum(t['pnl'] for t in bt)
    print('{:<15s} {:>8d} {:>7.0f}% {:>7.2f} {:>+11.2f}%'.format(f'{lo:.1f}-{hi:.1f}', len(bt), w/len(bt)*100, pf, net))

# Key comparison
print()
print('Key Comparison:')
print('{:<25s} {:>8s} {:>7s} {:>7s} {:>12s}'.format('SEGMENT', 'TRADES', 'WR', 'PF', 'NET PNL'))
print('{:<25s} {:>8s} {:>7s} {:>7s} {:>12s}'.format('---', '---', '---', '---', '---'))

for name,seg in [('BW < 0.40 (current)', [t for t in trades if t['bw']<0.4]),
                  ('BW >= 0.40', [t for t in trades if t['bw']>=0.4]),
                  ('BW < 0.60', [t for t in trades if t['bw']<0.6]),
                  ('BW >= 0.60', [t for t in trades if t['bw']>=0.6])]:
    if not seg:
        print('{:<25s} {:>8d} {:>7s} {:>7s} {:>12s}'.format(name, 0, '-', '-', '-'))
        continue
    w=sum(1 for t in seg if t['won'])
    gp=sum(t['pnl'] for t in seg if t['won'])
    gl=abs(sum(t['pnl'] for t in seg if not t['won']))
    pf=gp/gl if gl>0 else 99
    net=sum(t['pnl'] for t in seg)
    print('{:<25s} {:>8d} {:>6.0f}% {:>7.2f} {:>+11.2f}%'.format(name, len(seg), w/len(seg)*100, pf, net))
