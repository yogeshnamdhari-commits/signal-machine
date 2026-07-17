import json

with open('packages/ai-engine/data/bridge/market_data.json') as f:
    d = json.load(f)
rows = d.get('rows', [])
btc = next((r for r in rows if r.get('symbol') == 'BTCUSDT'), {})

# Check all sweep/fvg related fields
print('=== SWEEP/FVG FIELDS IN BRIDGE ===')
for k in sorted(btc.keys()):
    if any(x in k.lower() for x in ['sweep', 'fvg', 'cascade', 'liq']):
        print(f'  {k:30s}: {btc[k]}')

# Check if sweep_detected column exists
print(f'\n  sweep_detected column exists: {"sweep_detected" in btc}')
print(f'  sweep_direction column exists: {"sweep_direction" in btc}')
print(f'  of_sweep column exists: {"of_sweep" in btc}')

# Count how many have sweep_detected=True
sweep_true = sum(1 for r in rows if r.get('sweep_detected'))
of_sweep_non_none = sum(1 for r in rows if r.get('of_sweep') and r.get('of_sweep') != 'none')
cascade_active = sum(1 for r in rows if r.get('cascade_active'))
print(f'\n  sweep_detected=True: {sweep_true}/{len(rows)}')
print(f'  of_sweep (non-none): {of_sweep_non_none}/{len(rows)}')
print(f'  cascade_active: {cascade_active}/{len(rows)}')

# Show unique of_sweep values
of_vals = set(r.get('of_sweep', '') for r in rows)
print(f'\n  Unique of_sweep values: {of_vals}')

# Show what orderflow sweep data exists
of_sweep_fields = [k for k in btc.keys() if 'sweep' in k.lower() or 'fvg' in k.lower()]
print(f'\n  All sweep/fvg fields: {of_sweep_fields}')
