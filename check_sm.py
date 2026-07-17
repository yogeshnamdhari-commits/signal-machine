#!/usr/bin/env python3
"""Full Smart Money + Signals + Positions diagnostic"""
import json
import datetime
from collections import Counter

d = json.load(open('packages/ai-engine/data/bridge/smart_money_map.json'))
rows = d.get('rows', [])
print(f"Total symbols: {len(rows)}")

sides = Counter(r.get('smart_money_side', '?') for r in rows)
print(f"Side distribution: {dict(sides)}")

stealth_buys = sum(r.get('stealth_buys', 0) for r in rows)
stealth_sells = sum(r.get('stealth_sells', 0) for r in rows)
print(f"Total stealth buys: {stealth_buys} | sells: {stealth_sells}")

has_inst = sum(1 for r in rows if r.get('inst_probability', 0) > 0)
has_accum = sum(1 for r in rows if r.get('accum_probability', 0) > 0)
has_whale = sum(1 for r in rows if r.get('whale_probability', 0) > 0)
print(f"Probabilities: inst={has_inst}/50 accum={has_accum}/50 whale={has_whale}/50")

has_levels = sum(1 for r in rows if r.get('price_levels', []))
print(f"Symbols with price_levels: {has_levels}/50")

strengths = [r.get('smart_money_strength', 0) for r in rows]
print(f"Strength: range={min(strengths):.1f}-{max(strengths):.1f} mean={sum(strengths)/len(strengths):.1f}")
print(f"  >=60 strong: {sum(1 for s in strengths if s >= 60)} | >=30 moderate: {sum(1 for s in strengths if s >= 30)} | <30 weak: {sum(1 for s in strengths if s < 30)}")

print()
print("=== TOP 10 SM STRENGTH ===")
sr = sorted(rows, key=lambda r: r.get('smart_money_strength', 0), reverse=True)
for r in sr[:10]:
    print(f'  {r["symbol"]:15s} str={r.get("smart_money_strength",0):5.1f} side={r.get("smart_money_side","?")} accum={r.get("accumulation_score",0):.3f} dist={r.get("distribution_score",0):.3f} signals={r.get("active_signals",[])}')

print()
print("=== SIGNALS ===")
d2 = json.load(open('packages/ai-engine/data/bridge/signals.json'))
sigs = d2.get('signals', [])
print(f'Active signals: {len(sigs)}')
for s in sigs[:10]:
    t = datetime.datetime.fromtimestamp(s.get('created_at',0)).strftime('%H:%M')
    print(f'  {s["symbol"]:15s} {s["side"]:5s} score={s.get("institutional_score",0):5.1f} conf={s.get("confidence",0):.2f} at {t}')

print()
print("=== POSITIONS ===")
d3 = json.load(open('packages/ai-engine/data/bridge/positions.json'))
ps = d3.get('positions', [])
print(f'Open: {len(ps)}')
total_pnl = sum(p.get('unrealized_pnl',0) for p in ps)
for p in ps[:10]:
    print(f'  {p["symbol"]:15s} {p["side"]:5s} entry={p.get("entry_price","?")} pnl=${p.get("unrealized_pnl",0):+.2f}')
print(f'  Total unrealized: ${total_pnl:+.2f}')

print()
print("=== PORTFOLIO ===")
d4 = json.load(open('packages/ai-engine/data/bridge/metrics.json'))
m = d4.get('metrics', {})
print(f'Portfolio: ${m.get("portfolio_value",0):,.2f} | PnL: ${m.get("total_pnl",0):.2f} | Trades: {m.get("trades_total",0)} | Scanned: {m.get("symbols_scanned",0)}')
