#!/usr/bin/env python3
"""Quick diagnostic: read bridge file and show signal gate stats."""
import json, time

with open('packages/ai-engine/data/bridge/ema_v5.json') as f:
    d = json.load(f)

ema = d['ema_v5']
scanner = ema.get('scanner', {})
pipeline = scanner.get('pipeline', {})
health = ema.get('health', {})
gates = ema.get('signal_gates', {})

age = time.time() - d.get('timestamp', 0)
print(f'Bridge age: {age:.0f}s')
print(f'scan_count: {scanner.get("scan_count")}')
print(f'signal_count: {scanner.get("signal_count")}')
print(f'uptime: {scanner.get("uptime_sec", 0):.0f}s')

total = pipeline.get('total_candidates', 0)
rejections = pipeline.get('stage_rejections', {})
print(f'\ntotal_candidates: {total}')
for stage, count in sorted(rejections.items(), key=lambda x: -x[1]):
    if count > 0:
        pct = count / total * 100 if total > 0 else 0
        print(f'  {stage}: {count} ({pct:.1f}%)')

print(f'\n=== SIGNAL GATE DIAGNOSTICS (confidence → publication) ===')
for gate, count in sorted(gates.items(), key=lambda x: -x[1]):
    if count > 0:
        print(f'  {gate}: {count}')

print(f'\nhalted: {health.get("halted")}')
print(f'engine_running: {health.get("engine_running")}')
