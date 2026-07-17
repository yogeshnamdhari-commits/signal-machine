#!/usr/bin/env python3
"""Debug bridge reader."""
import sys, json, time
sys.path.insert(0, "packages/ai-engine")
from dashboard.data_bridge import reader, MARKET_DATA_FILE

print(f"MARKET_DATA_FILE: {MARKET_DATA_FILE}")
print(f"Exists: {MARKET_DATA_FILE.exists()}")

md = reader.read_market_data()
print(f"read_market_data() returned: {len(md)} rows")

if not md:
    with open(MARKET_DATA_FILE) as f:
        raw = json.load(f)
    rows = raw.get("rows", [])
    ts = raw.get("timestamp", 0)
    age = time.time() - ts
    print(f"File has: {len(rows)} rows, timestamp={ts}, age={age:.0f}s")
    print(f"Age > 600? {age > 600}")
    if rows:
        print(f"First row: symbol={rows[0].get('symbol')} price={rows[0].get('price')}")
