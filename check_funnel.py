import json
d = json.load(open('packages/ai-engine/data/bridge/funnel.json'))
f = d['funnel']
print('=== FUNNEL ===')
for k in ['symbols_processed','scorer_rejected','phase1_rejected','regime_blocked','sweep_blocked','signals_emitted']:
    print(f'  {k}: {f.get(k,0)}')
print()
print('=== TOP SCORES ===')
for s in f.get('top_scores',[])[:10]:
    print(f"  {s['symbol']} {s['side']} conf={s['confidence']:.1f} inst={s['institutional_score']:.1f}")
print()
print('=== REJECTION REASONS (last 10) ===')
for r in f.get('rejection_reasons',[])[-10:]:
    print(f"  {r['symbol']}: {r['reason']}")

# Also check signals
try:
    d2 = json.load(open('packages/ai-engine/data/bridge/signals.json'))
    print(f"\n=== SIGNALS: {d2.get('count', 0)} ===")
    for s in d2.get('signals', [])[:5]:
        print(f"  {s.get('side','?')} {s.get('symbol','?')} conf={s.get('confidence_100',0):.1f} score={s.get('institutional_score',0):.1f}")
except:
    print("\n=== SIGNALS: 0 ===")
