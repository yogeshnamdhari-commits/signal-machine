"""Verify all dashboard labels against bridge data."""
import json, urllib.request, sys

# Load bridge data
with open("data/bridge/market_data.json") as f:
    md = json.load(f)
rows = md.get("rows", [])

# Load signals
try:
    with open("data/bridge/signals.json") as f:
        raw = json.load(f)
    sigs = raw if isinstance(raw, list) else raw.get("signals", []) if isinstance(raw, dict) else []
except:
    sigs = []

# Load status
try:
    with open("data/bridge/status.json") as f:
        status = json.load(f)
except:
    status = {}

print(f"Bridge: {len(rows)} symbols, {len(sigs)} signals")
print(f"Status: running={status.get('running')}, symbols={status.get('symbols')}")
print()

# === CHECK CRITICAL BRIDGE KEYS ===
print("=" * 70)
print("CRITICAL BRIDGE KEY VERIFICATION")
print("=" * 70)

required_keys = [
    "symbol", "price", "mark_price", "index_price", "funding",
    "open_interest", "volume", "volume_btc", "change_24h",
    "high_24h", "low_24h", "open_24h", "trades_24h",
    "signal", "regime",
    # Exchange flow
    "exchange_flow", "exchange_bias", "aggressive_buy_vol", "aggressive_sell_vol",
    "buy_sell_ratio", "flow_strength", "flow_signal", "flow_total_trades",
    # Order flow
    "of_buy_volume", "of_sell_volume", "of_flow_ratio", "of_flow_signal",
    "of_flow_strength", "of_total_trades", "of_absorption", "of_sweep",
    # CVD
    "cvd_bias", "cvd_bias_1m", "cvd_bias_5m", "cvd_bias_15m", "cvd_bias_1h", "cvd_bias_4h",
    "cvd_5m", "cvd_1h", "cvd_4h", "cvd_divergence_5m", "cvd_divergence_15m", "cvd_buy_ratio_5m",
    # OI
    "oi_bias", "oi_change_pct", "oi_regime", "oi_positioning",
    # Funding
    "funding_bias", "funding_z",
    # Liquidation
    "liq_risk", "liq_risk_level", "cluster_count", "sweep_detected",
    # Vol
    "vol_bias", "imbalance",
]

if rows:
    r = rows[0]
    missing = [k for k in required_keys if k not in r]
    present = [k for k in required_keys if k in r]
    print(f"Present: {len(present)}/{len(required_keys)}")
    if missing:
        print(f"MISSING: {missing}")
    print()

# === CHECK VALUES FOR TOP 5 SYMBOLS ===
print("=" * 70)
print("TOP 5 SYMBOLS - KEY VALUES")
print("=" * 70)

for r in rows[:5]:
    sym = r.get("symbol", "?")
    print(f"\n{sym}:")
    print(f"  Price: ${r.get('price', 0):,.2f}")
    print(f"  Mark: ${r.get('mark_price', 0):,.2f}")
    print(f"  Index: ${r.get('index_price', 0):,.2f}")
    print(f"  Funding: {r.get('funding', 0):.6f}%")
    print(f"  OI: ${r.get('open_interest', 0):,.0f}")
    print(f"  Vol: ${r.get('volume', 0):,.0f}")
    print(f"  Change 24h: {r.get('change_24h', 0):+.2f}%")
    print(f"  Signal: {r.get('signal', 'N/A')}")
    print(f"  Regime: {r.get('regime', 'N/A')}")
    print(f"  Flow Signal: {r.get('flow_signal', 'N/A')} (trades={r.get('flow_total_trades', 0)})")
    print(f"  Order Flow: ratio={r.get('of_flow_ratio', 0.5):.3f} signal={r.get('of_flow_signal', 'N/A')} trades={r.get('of_total_trades', 0)}")
    print(f"  CVD 5m: {r.get('cvd_5m', 0):.2f}")
    print(f"  CVD Bias: {r.get('cvd_bias', 'N/A')}")
    print(f"  Absorption: {r.get('of_absorption', 'N/A')}")
    print(f"  Sweep: {r.get('of_sweep', 'N/A')}")

# === CHECK SIGNALS ===
print()
print("=" * 70)
print(f"SIGNALS: {len(sigs)} total")
print("=" * 70)
if sigs:
    for s in sigs[:5]:
        print(f"  {s.get('symbol')}: {s.get('type')} conf={s.get('confidence', 0):.2f} score={s.get('institutional_score', 0):.0f}")
    # Check signal distribution
    longs = sum(1 for s in sigs if s.get("type") == "LONG")
    shorts = sum(1 for s in sigs if s.get("type") == "SHORT")
    print(f"\n  LONG: {longs}, SHORT: {shorts}")

# === CROSS-VALIDATE WITH BINANCE ===
print()
print("=" * 70)
print("CROSS-VALIDATION WITH BINANCE LIVE API")
print("=" * 70)

test_syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
for sym in test_syms:
    r = next((r for r in rows if r.get("symbol") == sym), None)
    if not r:
        print(f"  {sym}: NOT IN BRIDGE")
        continue
    try:
        t = json.loads(urllib.request.urlopen(f"https://fapi.binance.com/fapi/v1/ticker/24hr?symbol={sym}", timeout=10).read())
        live_price = float(t["lastPrice"])
        live_vol = float(t["quoteVolume"])
        live_change = float(t["priceChangePercent"])
        
        br_price = r.get("price", 0)
        br_vol = r.get("volume", 0)
        br_change = r.get("change_24h", 0)
        
        price_diff = abs(live_price - br_price) / live_price * 100
        vol_diff = abs(live_vol - br_vol) / live_vol * 100 if live_vol > 0 else 0
        change_diff = abs(live_change - br_change)
        
        price_ok = "OK" if price_diff < 0.1 else "DRIFT"
        vol_ok = "OK" if vol_diff < 5 else "DRIFT"
        change_ok = "OK" if change_diff < 0.5 else "DRIFT"
        
        print(f"  {sym}: Price {price_ok} ({price_diff:.3f}%) Vol {vol_ok} ({vol_diff:.1f}%) Change {change_ok} ({change_diff:.2f}%)")
    except Exception as e:
        print(f"  {sym}: ERROR - {e}")
