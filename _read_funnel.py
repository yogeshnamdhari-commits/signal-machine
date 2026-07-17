#!/usr/bin/env python3
"""Read live funnel.json and dump all pipeline state."""
import json, sys

try:
    with open('/Users/targetmobile/Documents/signal machine/packages/ai-engine/data/bridge/funnel.json') as f:
        d = json.load(f)
except Exception as e:
    print(f"ERROR: {e}")
    sys.exit(1)

funnel = d.get('funnel', d)
print("TOP KEYS:", list(d.keys()))
print()

if isinstance(funnel, dict):
    print("FUNNEL CONTENTS:")
    for k, v in funnel.items():
        if isinstance(v, (int, float, str, bool)):
            print(f"  {k}: {v}")
        elif isinstance(v, list):
            print(f"  {k}: list[{len(v)}]")
        elif isinstance(v, dict):
            print(f"  {k}: dict[{len(v)} keys]")
        else:
            print(f"  {k}: {type(v).__name__}")
    
    # Show pipeline traces
    traces = funnel.get('pipeline_traces', [])
    print(f"\nPIPELINE TRACES ({len(traces)} total):")
    for i, t in enumerate(traces[:5]):
        print(f"  TRACE {i}: {json.dumps(t)}")
    
    # Show rejection reasons
    rejections = funnel.get('rejection_reasons', [])
    print(f"\nREJECTION REASONS ({len(rejections)} total):")
    for i, r in enumerate(rejections[:5]):
        print(f"  REJECT {i}: {json.dumps(r)}")
    
    # Show session diagnostics
    sd = funnel.get('session_diagnostics', {})
    print(f"\nSESSION DIAGNOSTICS:")
    print(f"  {json.dumps(sd, indent=2)}")
else:
    print("Funnel is not a dict:", type(funnel))
    # Print first 1000 chars
    print(str(d)[:1000])
