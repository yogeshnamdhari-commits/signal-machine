#!/usr/bin/env python3
"""Exchange flow diagnostic"""
import json

d = json.load(open('packages/ai-engine/data/bridge/market_data.json'))
rows = d.get('rows', d) if isinstance(d, dict) else d
if not isinstance(rows, list) or len(rows) == 0:
    print("No data")
    exit()

print(f"Symbols: {len(rows)}")
print()

# Check specific symbols
for sym in ['BTCUSDT', 'ETHUSDT', 'ADAUSDT', 'DOGEUSDT', 'SOLUSDT']:
    r = next((r for r in rows if r['symbol'] == sym), None)
    if r:
        print(f'{sym}: exchange_flow={r.get("exchange_flow",0):+.0f} flow_strength={r.get("flow_strength",50)} flow_signal={r.get("flow_signal","neutral")} flow_total_trades={r.get("flow_total_trades",0)}')

print()
print("=== TOP 10 BY STRENGTH ===")
for r in sorted(rows, key=lambda x: x.get('flow_strength', 50), reverse=True)[:10]:
    fr = r.get('buy_sell_ratio', 0.5)
    fs = r.get('flow_strength', 50)
    ef = r.get('exchange_flow', 0)
    signal = r.get('flow_signal', 'neutral')
    trades = r.get('flow_total_trades', 0)
    print(f'  {r["symbol"]:15s} ratio={fr:.4f} str={fs:5.1f} flow=${ef:+12.0f} sig={signal:7s} trades={trades}')

print()
print("=== BOTTOM 5 (weakest) ===")
for r in sorted(rows, key=lambda x: x.get('flow_strength', 50))[:5]:
    fr = r.get('buy_sell_ratio', 0.5)
    fs = r.get('flow_strength', 50)
    ef = r.get('exchange_flow', 0)
    signal = r.get('flow_signal', 'neutral')
    trades = r.get('flow_total_trades', 0)
    print(f'  {r["symbol"]:15s} ratio={fr:.4f} str={fs:5.1f} flow=${ef:+12.0f} sig={signal:7s} trades={trades}')
