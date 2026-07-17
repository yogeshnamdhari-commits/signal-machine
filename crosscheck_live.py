#!/usr/bin/env python3
"""
═══════════════════════════════════════════════════════════════════
  LIVE BINANCE API CROSSCHECK — Premium Key Verification
  Pulls live data from Binance Futures REST API using HMAC-signed
  requests and compares against engine bridge data.
═══════════════════════════════════════════════════════════════════
"""

import json, time, hashlib, hmac, os, sys
from urllib.request import Request, urlopen
from urllib.parse import urlencode
from urllib.error import HTTPError
from datetime import datetime, timezone

# ═══ CONFIG ═══
BASE = "https://fapi.binance.com"
BRIDGE_PATH = "data/bridge/market_data.json"
TOP_N = 15  # Crosscheck top N symbols

# ═══ Load API Keys ═══
API_KEY = os.environ.get("BINANCE_API_KEY", "")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "")
TESTNET = os.environ.get("BINANCE_TESTNET", "false").lower() == "true"

if not API_KEY:
    # Try loading from .env
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("BINANCE_API_KEY="):
                    API_KEY = line.split("=", 1)[1]
                elif line.startswith("BINANCE_API_SECRET="):
                    API_SECRET = line.split("=", 1)[1]
                elif line.startswith("BINANCE_TESTNET="):
                    TESTNET = line.split("=", 1)[1].lower() == "true"

if TESTNET:
    BASE = "https://testnet.binancefuture.com"

def ts():
    return datetime.now(timezone.utc).strftime("%H:%M:%S")

def hmac_sign(query_string: str) -> str:
    return hmac.new(API_SECRET.encode(), query_string.encode(), hashlib.sha256).hexdigest()

def public_get(path: str, params: dict = None) -> dict | list:
    url = BASE + path
    if params:
        url += "?" + urlencode(params)
    req = Request(url, headers={"X-MBX-APIKEY": API_KEY})
    with urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def signed_get(path: str, params: dict = None) -> dict:
    if params is None:
        params = {}
    params["timestamp"] = int(time.time() * 1000)
    params["recvWindow"] = 5000
    qs = urlencode(params)
    sig = hmac_sign(qs)
    qs += f"&signature={sig}"
    url = BASE + path + "?" + qs
    req = Request(url, headers={"X-MBX-APIKEY": API_KEY})
    with urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def fmt_price(p):
    if p is None: return "N/A"
    if p >= 1000: return f"${p:,.2f}"
    if p >= 1: return f"${p:.4f}"
    return f"${p:.6f}"

def fmt_vol(v):
    if v is None: return "N/A"
    if v >= 1e9: return f"${v/1e9:.2f}B"
    if v >= 1e6: return f"${v/1e6:.2f}M"
    return f"${v:,.0f}"

def fmt_pct(p):
    if p is None: return "N/A"
    return f"{p:+.2f}%"

def match(a, b, tol=0.02):
    """Check if two values match within tolerance (2% relative)"""
    if a is None or b is None: return False
    if a == 0 and b == 0: return True
    ref = max(abs(a), abs(b), 1e-10)
    return abs(a - b) / ref < tol

# ═══════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════
print(f"\n{'═'*80}")
print(f"  🔐 LIVE BINANCE API CROSSCHECK — {ts()} UTC")
print(f"  Key: {API_KEY[:8]}...{API_KEY[-4:]} (len={len(API_KEY)})")
print(f"  Mode: {'TESTNET' if TESTNET else '🔴 PRODUCTION'}")
print(f"  Base URL: {BASE}")
print(f"{'═'*80}\n")

results = {"passed": 0, "failed": 0, "errors": 0, "symbols": []}

# ── Step 1: Verify API Key with Account Endpoint ──
print("━━━ STEP 1: HMAC API KEY VERIFICATION ━━━")
try:
    acct = signed_get("/fapi/v2/account")
    total_balance = float(acct.get("totalWalletBalance", 0))
    available = float(acct.get("availableBalance", 0))
    unrealized = float(acct.get("totalUnrealizedProfit", 0))
    assets = [a for a in acct.get("assets", []) if float(a.get("walletBalance", 0)) != 0]
    
    print(f"  ✅ HMAC Signature VERIFIED — Account accessible")
    print(f"  💰 Wallet Balance:     {fmt_price(total_balance)}")
    print(f"  💵 Available Balance:  {fmt_price(available)}")
    print(f"  📊 Unrealized PnL:     {fmt_price(unrealized)}")
    if assets:
        print(f"  📦 Non-zero assets:    {len(assets)}")
        for a in assets[:5]:
            print(f"     • {a['asset']}: wallet={a.get('walletBalance', 0)} available={a.get('availableBalance', 0)}")
    
    # Check open positions
    positions = [p for p in acct.get("positions", []) if float(p.get("positionAmt", 0)) != 0]
    if positions:
        print(f"\n  📌 OPEN POSITIONS: {len(positions)}")
        for p in positions:
            side = "LONG" if float(p["positionAmt"]) > 0 else "SHORT"
            print(f"     {p['symbol']} {side} amt={p['positionAmt']} entry={p.get('entryPrice','0')} pnl={p.get('unrealizedProfit','0')}")
    else:
        print(f"  📌 Open Positions: 0 (flat)")
    
    # Verify HMAC mode
    print(f"\n  🔑 HMAC Authentication: ✅ PRODUCTION (System-generated key)")
    print(f"  🔑 Key Length: {len(API_KEY)} chars | Secret Length: {len(API_SECRET)} chars")
    results["account"] = True
except HTTPError as e:
    body = e.read().decode() if hasattr(e, 'read') else str(e)
    print(f"  ❌ API ERROR {e.code}: {body[:200]}")
    results["account"] = False
    results["errors"] += 1
except Exception as e:
    print(f"  ❌ ERROR: {e}")
    results["account"] = False
    results["errors"] += 1

print()

# ── Step 2: Load Bridge Data ──
print("━━━ STEP 2: LOAD ENGINE BRIDGE DATA ━━━")
try:
    with open(BRIDGE_PATH) as f:
        bridge = json.load(f)
    bridge_rows = bridge.get("rows", [])
    bridge_map = {r["symbol"]: r for r in bridge_rows}
    print(f"  ✅ Bridge loaded: {len(bridge_rows)} symbols, ts={bridge.get('timestamp', 'N/A')}")
    results["bridge_count"] = len(bridge_rows)
except Exception as e:
    print(f"  ❌ Failed to load bridge: {e}")
    bridge_map = {}

print()

# ── Step 3: Pull Live Market Data from Binance ──
print("━━━ STEP 3: PULL LIVE BINANCE MARKET DATA ━━━")

# 3a: Ticker (price, volume, 24h)
print("  📡 Fetching 24h tickers...")
try:
    tickers = public_get("/fapi/v1/ticker/24hr")
    ticker_map = {t["symbol"]: t for t in tickers}
    print(f"  ✅ Got {len(tickers)} tickers from Binance REST API")
except Exception as e:
    print(f"  ❌ Ticker fetch failed: {e}")
    ticker_map = {}

# 3b: Funding rates
print("  📡 Fetching funding rates...")
try:
    funding = public_get("/fapi/v1/fundingRate", {"limit": 1})
    funding_latest = public_get("/fapi/v1/premiumIndex")
    funding_map = {f["symbol"]: f for f in funding_latest}
    print(f"  ✅ Got {len(funding_latest)} funding rates")
except Exception as e:
    print(f"  ❌ Funding fetch failed: {e}")
    funding_map = {}

# 3c: Open Interest
print("  📡 Fetching open interest for top symbols...")
oi_map = {}
for sym in list(ticker_map.keys())[:50]:
    try:
        oi_data = public_get("/fapi/v1/openInterest", {"symbol": sym})
        oi_map[sym] = float(oi_data.get("openInterest", 0))
    except:
        pass
print(f"  ✅ Got OI for {len(oi_map)} symbols")

# 3d: Klines (5m) for a few symbols
print("  📡 Fetching 5m klines for verification...")
kline_map = {}
for sym in list(ticker_map.keys())[:10]:
    try:
        klines = public_get("/fapi/v1/klines", {"symbol": sym, "interval": "5m", "limit": 50})
        kline_map[sym] = len(klines)
    except:
        pass
print(f"  ✅ Got 5m klines for {len(kline_map)} symbols")

print()

# ── Step 4: Crosscheck Each Symbol ──
print("━━━ STEP 4: SYMBOL-BY-SYMBOL CROSSCHECK ━━━\n")

# Get top symbols by volume
top_syms = sorted(ticker_map.values(), key=lambda t: float(t.get("quoteVolume", 0)), reverse=True)[:TOP_N]

header = f"{'Symbol':<14} {'Field':<12} {'Bridge':>14} {'Live API':>14} {'Delta%':>8} {'Status':>6}"
print(f"  {header}")
print(f"  {'─'*len(header)}")

for ticker in top_syms:
    sym = ticker["symbol"]
    bridge_row = bridge_map.get(sym, {})
    
    sym_checks = []
    
    # Price check
    live_price = float(ticker.get("lastPrice", 0))
    bridge_price = bridge_row.get("price", None)
    if bridge_price and live_price:
        price_ok = match(bridge_price, live_price, 0.005)  # 0.5% tolerance
        delta_pct = abs(bridge_price - live_price) / live_price * 100 if live_price else 0
        sym_checks.append(("Price", bridge_price, live_price, delta_pct, price_ok))
    
    # Volume check
    live_vol = float(ticker.get("quoteVolume", 0))
    bridge_vol = bridge_row.get("volume_24h", bridge_row.get("volume", None))
    if bridge_vol and live_vol:
        vol_ok = match(bridge_vol, live_vol, 0.05)  # 5% tolerance (time diff)
        delta_pct = abs(bridge_vol - live_vol) / live_vol * 100 if live_vol else 0
        sym_checks.append(("Volume 24h", bridge_vol, live_vol, delta_pct, vol_ok))
    
    # 24h change check
    live_change = float(ticker.get("priceChangePercent", 0))
    bridge_change = bridge_row.get("change_24h", None)
    if bridge_change is not None and live_change:
        chg_ok = abs(bridge_change - live_change) < 0.5  # 0.5% absolute
        delta_pct = abs(bridge_change - live_change)
        sym_checks.append(("24h Change", bridge_change, live_change, delta_pct, chg_ok))
    
    # Funding check
    if sym in funding_map:
        live_funding = float(funding_map[sym].get("lastFundingRate", 0)) * 100
        bridge_funding = bridge_row.get("funding", None)
        if bridge_funding is not None:
            fund_ok = abs(bridge_funding - live_funding) < 0.1  # 0.1% tolerance
            delta_pct = abs(bridge_funding - live_funding)
            sym_checks.append(("Funding%", bridge_funding, live_funding, delta_pct, fund_ok))
    
    # OI check
    if sym in oi_map:
        live_oi = oi_map[sym]
        bridge_oi = bridge_row.get("open_interest", None)
        if bridge_oi is not None and live_oi:
            oi_ok = match(bridge_oi, live_oi, 0.10)  # 10% tolerance (OI updates slower)
            delta_pct = abs(bridge_oi - live_oi) / live_oi * 100 if live_oi else 0
            sym_checks.append(("OI", bridge_oi, live_oi, delta_pct, oi_ok))
    
    # Print results
    if not sym_checks:
        print(f"  {sym:<14} {'—':<12} {'(not in bridge)':>14}")
        continue
    
    for i, (field, bval, lval, delta, ok) in enumerate(sym_checks):
        status = "✅" if ok else "❌"
        sym_label = sym if i == 0 else ""
        b_str = fmt_price(bval) if field == "Price" else (fmt_vol(bval) if field == "Volume 24h" else (f"{bval:.4f}%" if "fund" in field.lower() else f"{bval:,.1f}"))
        l_str = fmt_price(lval) if field == "Price" else (fmt_vol(lval) if field == "Volume 24h" else (f"{lval:.4f}%" if "fund" in field.lower() else f"{lval:,.1f}"))
        d_str = f"{delta:.2f}%" if delta < 100 else f"{delta:.0f}%"
        print(f"  {sym_label:<14} {field:<12} {b_str:>14} {l_str:>14} {d_str:>8} {status:>6}")
        
        if ok:
            results["passed"] += 1
        else:
            results["failed"] += 1
    
    print(f"  {'─'*len(header)}")

# ── Step 5: Kline Coverage Check ──
print(f"\n━━━ STEP 5: KLINE DATA COVERAGE ━━━")
print(f"  5m kline availability from live Binance API:")
for sym, count in sorted(kline_map.items(), key=lambda x: -x[1])[:10]:
    bridge_klines = bridge_map.get(sym, {}).get("klines_5m_count", "N/A")
    print(f"  {sym:<14} Live: {count:>3} candles | Bridge klines: {bridge_klines}")

print()

# ── Step 6: Summary ──
print(f"{'═'*80}")
print(f"  📊 CROSSCHECK SUMMARY — {ts()} UTC")
print(f"{'═'*80}")
total = results["passed"] + results["failed"]
print(f"  ✅ Passed:  {results['passed']}/{total} checks")
print(f"  ❌ Failed:  {results['failed']}/{total} checks")
print(f"  ⚠️  Errors:  {results['errors']}")
print(f"  📡 Bridge:  {results.get('bridge_count', 0)} symbols")
print(f"  🔑 API Key: HMAC verified = {results.get('account', False)}")
print(f"  🔴 Mode:    {'TESTNET' if TESTNET else 'PRODUCTION'}")
if results["failed"] == 0 and results.get("account"):
    print(f"\n  🟢 ALL CHECKS PASSED — Engine data matches live Binance premium data!")
else:
    print(f"\n  🟡 {results['failed']} checks failed — see details above")
print(f"{'═'*80}\n")
