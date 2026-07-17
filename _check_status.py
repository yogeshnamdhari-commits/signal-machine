#!/usr/bin/env python3
"""Quick system status check."""
import json, time, os, subprocess

print("=" * 60)
print("  SYSTEM STATUS CHECK")
print("=" * 60)

# Bridge files
for name, path in [
    ("market_data.json", "data/bridge/market_data.json"),
    ("funnel.json", "data/bridge/funnel.json"),
    ("positions.json", "data/bridge/positions.json"),
    ("smart_money_map.json", "data/bridge/smart_money_map.json"),
    ("trade_history.json", "data/bridge/trade_history.json"),
]:
    if os.path.exists(path):
        age = time.time() - os.stat(path).st_mtime
        with open(path) as f:
            data = json.load(f)
        if name == "market_data.json":
            rows = data.get("rows", [])
            active = sum(1 for r in rows if r.get("total_trades", 0) > 0 or r.get("of_total_trades", 0) > 0)
            print(f"  {name}: {len(rows)} rows ({active} with trades), age={age:.0f}s")
        elif name == "funnel.json":
            fd = data.get("funnel", {})
            print(f"  {name}: processed={fd.get('symbols_processed',0)} emitted={fd.get('signals_emitted',0)}, age={age:.0f}s")
        elif name == "trade_history.json":
            trades = data.get("trades", [])
            print(f"  {name}: {len(trades)} trades, age={age:.0f}s")
        else:
            print(f"  {name}: age={age:.0f}s")
    else:
        print(f"  {name}: MISSING")

# Engine process
r = subprocess.run(["pgrep", "-f", "main.py.*engine"], capture_output=True, text=True)
pids = [p for p in r.stdout.strip().split("\n") if p]
print(f"\n  Engine PIDs: {pids}")

r2 = subprocess.run(["pgrep", "-f", "supervisor"], capture_output=True, text=True)
spids = [p for p in r2.stdout.strip().split("\n") if p]
print(f"  Supervisor PIDs: {spids}")

# Dashboard
r3 = subprocess.run(["pgrep", "-f", "streamlit"], capture_output=True, text=True)
dpids = [p for p in r3.stdout.strip().split("\n") if p]
print(f"  Dashboard PIDs: {dpids}")

# caffeinate
r4 = subprocess.run(["pgrep", "-x", "caffeinate"], capture_output=True, text=True)
cpids = [p for p in r4.stdout.strip().split("\n") if p]
print(f"  Caffeinate: {'ACTIVE' if cpids else 'NOT RUNNING'}")

# Last engine log entries
log_path = "packages/ai-engine/data/logs/supervisor.log"
if os.path.exists(log_path):
    with open(log_path) as f:
        lines = f.readlines()
    print(f"\n  Last supervisor entries:")
    for line in lines[-5:]:
        print(f"    {line.strip()}")

# REST trade poller status from engine log
engine_log = "packages/ai-engine/data/logs/engine_service.log"
if os.path.exists(engine_log):
    with open(engine_log) as f:
        all_lines = f.readlines()
    # Find REST trade poll lines
    rest_lines = [l.strip() for l in all_lines if "REST trade" in l or "fed trades" in l]
    if rest_lines:
        print(f"\n  REST trade poller ({len(rest_lines)} entries):")
        for line in rest_lines[-5:]:
            print(f"    {line[:120]}")
    # Find WS status
    ws_lines = [l.strip() for l in all_lines if "WS connect" in l and "ERROR" in l]
    if ws_lines:
        print(f"\n  WS errors ({len(ws_lines)} total):")
        for line in ws_lines[-3:]:
            print(f"    {line[:120]}")
    # Find symbols with trades
    trade_lines = [l.strip() for l in all_lines if "trades=" in l and "5m_klines=" in l]
    if trade_lines:
        has_data = [l for l in trade_lines[-20:] if "trades=0" not in l]
        print(f"\n  Symbols with trade data (last 20 scans): {len(has_data)}")
        for line in has_data[-5:]:
            print(f"    {line[:120]}")

print("\n" + "=" * 60)
