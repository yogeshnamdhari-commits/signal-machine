#!/usr/bin/env python3
"""
EMA_V5 PORTFOLIO OPTIMIZER V2
==============================
Lead Quantitative Portfolio Manager — System v2

DO NOT modify signal logic, indicators, or EMA rules.
ONLY optimize: Portfolio Allocation, Dynamic Risk, Correlation Engine,
               Symbol Ranking, Exit Optimization, Portfolio Constraints.

V1 → V2 Key Improvements:
  1. Dynamic Kelly-weighted allocation (never equal-weight)
  2. DD-adaptive risk engine (auto-reduce after drawdown)
  3. Correlation/sector engine (prevent correlated blowups)
  4. Enhanced exits (volatility exit + structure exit)
  5. Portfolio constraints (heat, concurrent, sector caps)
  6. Rolling window validation, stress test, slippage, fee sensitivity
"""
import sys, os, json, time, statistics, sqlite3, math
import numpy as np

os.environ['LOGURU_LEVEL'] = 'ERROR'

# ══════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════

MAX_CONCURRENT = 12
MAX_HEAT = 0.15          # 15% total portfolio risk
MAX_PER_SECTOR = 3
MAX_SECTOR_EXPOSURE = 0.50
MAX_DAILY_LOSS_PCT = 0.04
MAX_WEEKLY_LOSS_PCT = 0.07
MAX_RISK_PER_TRADE = 0.035
MAX_POSITION_PCT = 0.18
COOLDOWN_BARS = 6

# Sector mapping for correlation engine
SECTOR_MAP = {
    # Layer 1
    "BTCUSDT": "L1_MAJOR", "ETHUSDT": "L1_MAJOR", "SOLUSDT": "L1_MAJOR",
    "BNBUSDT": "L1_MAJOR", "XRPUSDT": "L1_MAJOR", "ADAUSDT": "L1_ALT",
    "AVAXUSDT": "L1_ALT", "DOTUSDT": "L1_ALT", "NEARUSDT": "L1_ALT",
    "APTUSDT": "L1_ALT", "SUIUSDT": "L1_ALT", "SEIUSDT": "L1_ALT",
    "INJUSDT": "L1_ALT", "FTMUSDT": "L1_ALT", "HBARUSDT": "L1_ALT",
    "ALGOUSDT": "L1_ALT", "ETCUSDT": "L1_ALT", "BCHUSDT": "L1_ALT",
    "LTCUSDT": "L1_ALT", "EOSUSDT": "L1_ALT", "XLMUSDT": "L1_ALT",
    "TRXUSDT": "L1_ALT", "TONUSDT": "L1_ALT", "ATOMUSDT": "L1_ALT",
    "ICPUSDT": "L1_ALT", "APTUSDT": "L1_ALT",
    # Layer 2
    "OPUSDT": "L2", "ARBUSDT": "L2", "MATICUSDT": "L2", "BASEUSDT": "L2",
    "SKALEUSDT": "L2", "STRKUSDT": "L2",
    # Meme
    "DOGEUSDT": "MEME", "SHIBUSDT": "MEME", "PEPEUSDT": "MEME",
    "1000BONKUSDT": "MEME", "1000PEPEUSDT": "MEME", "WIFUSDT": "MEME",
    "FLOKIUSDT": "MEME", "TURBOUSDT": "MEME", "NEIROUSDT": "MEME",
    "BRETTUSDT": "MEME", "MOGUSDT": "MEME", "1000RUBUSDT": "MEME",
    "1000WIFUSDT": "MEME", "HIPPOUSDT": "MEME", "DOGSUSDT": "MEME",
    "CATIUSDT": "MEME", "HMSTRUSDT": "MEME",
    # AI / Data
    "FETUSDT": "AI", "RENDERUSDT": "AI", "ARUSDT": "AI",
    "TAOUSDT": "AI", "WLDUSDT": "AI", "AKTUSDT": "AI",
    "OCEANUSDT": "AI", "AGIXUSDT": "AI", "VIRTUALUSDT": "AI",
    "AI16ZUSDT": "AI", "GRIFFAINUSDT": "AI", "AIUSDT": "AI",
    "COOKIEUSDT": "AI", "COAIUSDT": "AI",
    # DeFi
    "UNIUSDT": "DEFI", "AAVEUSDT": "DEFI", "CRVUSDT": "DEFI",
    "COMPUSDT": "DEFI", "SNXUSDT": "DEFI", "MKRUSDT": "DEFI",
    "DYDXUSDT": "DEFI", "JOEUSDT": "DEFI", "PENDLEUSDT": "DEFI",
    "JUPUSDT": "DEFI", "RaydiumUSDT": "DEFI", "KMNOUSDT": "DEFI",
    "EVAAUSDT": "DEFI", "FOLKSUSDT": "DEFI",
    # Infrastructure
    "LINKUSDT": "INFRA", "GRTUSDT": "INFRA", "FILUSDT": "INFRA",
    "EGLDUSDT": "INFRA", "THETAUSDT": "INFRA",
    # Exchange
    "CROUSDT": "EXCHANGE", "KCSUSDT": "EXCHANGE",
    # Gaming / NFT
    "AXSUSDT": "GAMING", "SANDUSDT": "GAMING", "GALAUSDT": "GAMING",
    "MANAUSDT": "GAMING", "ENJUSDT": "GAMING", "ILVUSDT": "GAMING",
    "IMXUSDT": "GAMING", "PYTHUSDT": "GAMING",
    # Privacy / Other
    "XMRUSDT": "PRIVACY", "DASHUSDT": "PRIVACY",
    "BANANAS31USDT": "OTHER", "LAYERUSDT": "OTHER", "MITOUSDT": "OTHER",
    "ALICEUSDT": "OTHER", "ARKMUSDT": "OTHER", "AWEUSDT": "OTHER",
    "BIOUSDT": "OTHER", "BUSDT": "OTHER", "EDGEUSDT": "OTHER",
    "GUNUSDT": "OTHER", "IDOLUSDT": "OTHER", "RIVERUSDT": "OTHER",
    "SOONUSDT": "OTHER", "KAVAUSDT": "OTHER", "ENSUSDT": "OTHER",
    "1000BTTUSDT": "OTHER", "ACMUSDT": "OTHER", "1000SATSUSDT": "OTHER",
    "RUNEUSDT": "OTHER", "SUSHIUSDT": "OTHER", "YFIUSDT": "OTHER",
    "ZRXUSDT": "OTHER", "BALUSDT": "OTHER", "SRMUSDT": "OTHER",
    "OMGUSDT": "OTHER", "ZENUSDT": "OTHER", "SCUSDT": "OTHER",
    "STORJUSDT": "OTHER", "CVCUSDT": "OTHER", "KEYUSDT": "OTHER",
    "NKNUSDT": "OTHER", "VETUSDT": "OTHER", "IOTAUSDT": "OTHER",
    "IOTXUSDT": "OTHER", "BATUSDT": "OTHER", "REQUSDT": "OTHER",
    "POWRUSDT": "OTHER", "DATAUSDT": "OTHER", "MTLUSDT": "OTHER",
    "WRXUSDT": "OTHER", "IRISUSDT": "OTHER", "KMDUSDT": "OTHER",
    "PERPUSDT": "OTHER", "DEGOUSDT": "OTHER",
}

DEFAULT_SECTOR = "OTHER"


def get_sector(symbol):
    return SECTOR_MAP.get(symbol, DEFAULT_SECTOR)


# ══════════════════════════════════════════════════════════════════════
# PART 1: INDICATORS (LOCKED — DO NOT MODIFY)
# ══════════════════════════════════════════════════════════════════════

def ema_calc(data, period):
    result = np.full(len(data), np.nan)
    if len(data) < period: return result
    result[period - 1] = np.mean(data[:period])
    k = 2.0 / (period + 1)
    for i in range(period, len(data)):
        result[i] = data[i] * k + result[i - 1] * (1 - k)
    return result

def atr_calc(highs, lows, closes, period=14):
    n = len(highs); result = np.full(n, np.nan)
    if n < period + 1: return result
    tr = np.zeros(n); tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    cumsum = np.cumsum(tr)
    for i in range(period, n):
        result[i] = (cumsum[i] - cumsum[i - period]) / period
    return result

def sma_calc(data, period):
    result = np.full(len(data), np.nan)
    cumsum = np.cumsum(data)
    for i in range(period - 1, len(data)):
        result[i] = (cumsum[i] - (cumsum[i - period] if i >= period else 0)) / period
    return result

def rvol_calc(volumes, period=20):
    result = np.full(len(volumes), np.nan)
    cumsum = np.cumsum(volumes)
    for i in range(period - 1, len(volumes)):
        sma = (cumsum[i] - (cumsum[i - period] if i >= period else 0)) / period
        if sma > 0: result[i] = volumes[i] / sma
    return result

def compute_adx(highs, lows, closes, period=14):
    n = len(highs)
    adx_r = np.full(n, 0.0); plus_di = np.full(n, 0.0); minus_di = np.full(n, 0.0)
    if n < period + 2: return adx_r, plus_di, minus_di
    tr = np.zeros(n); pdm = np.zeros(n); ndm = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        up = highs[i] - highs[i-1]; down = lows[i-1] - lows[i]
        if up > down and up > 0: pdm[i] = up
        if down > up and down > 0: ndm[i] = down
    atr_s = np.zeros(n); pdm_s = np.zeros(n); ndm_s = np.zeros(n)
    atr_s[period] = np.sum(tr[1:period+1]); pdm_s[period] = np.sum(pdm[1:period+1]); ndm_s[period] = np.sum(ndm[1:period+1])
    for i in range(period + 1, n):
        atr_s[i] = atr_s[i-1] - atr_s[i-1]/period + tr[i]
        pdm_s[i] = pdm_s[i-1] - pdm_s[i-1]/period + pdm[i]
        ndm_s[i] = ndm_s[i-1] - ndm_s[i-1]/period + ndm[i]
    for i in range(period, n):
        if atr_s[i] > 0:
            plus_di[i] = 100.0 * pdm_s[i] / atr_s[i]
            minus_di[i] = 100.0 * ndm_s[i] / atr_s[i]
        dsum = plus_di[i] + minus_di[i]
        dx = 100.0 * abs(plus_di[i] - minus_di[i]) / dsum if dsum > 0 else 0
        if i == period: adx_r[i] = dx
        else: adx_r[i] = (adx_r[i-1] * (period - 1) + dx) / period
    return adx_r, plus_di, minus_di

def is_bullish_engulfing(o1, c1, o2, c2):
    if c1 >= o1 or c2 <= o2: return False
    rng = max(o2, c2) - min(o2, c2)
    if rng == 0: return False
    return abs(c2 - o2) / rng >= 0.5 and c2 > o1 and o2 < c1

def is_bearish_engulfing(o1, c1, o2, c2):
    if c1 <= o1 or c2 >= o2: return False
    rng = max(o2, c2) - min(o2, c2)
    if rng == 0: return False
    return abs(c2 - o2) / rng >= 0.5 and c2 < o1 and o2 > c1

def is_hammer(o, h, l, c):
    body = abs(c - o)
    if body == 0 or h - l == 0: return False
    lw = min(o, c) - l; uw = h - max(o, c)
    return lw / body >= 2.0 and uw <= body

def is_shooting_star(o, h, l, c):
    body = abs(c - o)
    if body == 0 or h - l == 0: return False
    uw = h - max(o, c); lw = min(o, c) - l
    return uw / body >= 2.0 and lw <= body


# ══════════════════════════════════════════════════════════════════════
# PART 2: SIGNAL GENERATION (LOCKED — DO NOT MODIFY)
# ══════════════════════════════════════════════════════════════════════

def generate_signals(klines):
    n = len(klines)
    if n < 250: return {}, {}
    opens = klines[:, 1]; highs = klines[:, 2]; lows = klines[:, 3]
    closes = klines[:, 4]; volumes = klines[:, 5]
    
    ema20 = ema_calc(closes, 20); ema50 = ema_calc(closes, 50)
    ema144 = ema_calc(closes, 144); ema200 = ema_calc(closes, 200)
    atr_arr = atr_calc(highs, lows, closes, 14)
    vol_sma = sma_calc(volumes, 20)
    rvol_arr = rvol_calc(volumes, 20)
    adx_arr, _, _ = compute_adx(highs, lows, closes, 14)
    
    ema20s = np.full(n, 0.0); ema50s = np.full(n, 0.0)
    ema144s = np.full(n, 0.0); ema200s = np.full(n, 0.0)
    for i in range(220, n):
        for arr, sa in [(ema20, ema20s), (ema50, ema50s), (ema144, ema144s), (ema200, ema200s)]:
            w = arr[max(0,i-5):i+1]; w = w[~np.isnan(w)]
            if len(w) >= 2: sa[i] = (w[-1] - w[0]) / max(abs(w[0]), 1e-10) * 100
    
    signals = {}
    for i in range(220, n):
        if any(np.isnan(x) for x in [ema20[i], ema50[i], ema144[i], ema200[i], atr_arr[i]]):
            continue
        if atr_arr[i] <= 0 or vol_sma[i] <= 0: continue
        atr_pct = atr_arr[i] / closes[i] * 100 if closes[i] > 0 else 0
        if atr_pct < 0.05: continue
        
        buy = ema20[i] > ema50[i] > ema144[i] > ema200[i]
        sell = ema20[i] < ema50[i] < ema144[i] < ema200[i]
        if not buy and not sell: continue
        if buy and not (ema144s[i] > 0 and ema200s[i] > 0 and closes[i] > ema144[i] and closes[i] > ema200[i]): continue
        if sell and not (ema144s[i] < 0 and ema200s[i] < 0 and closes[i] < ema144[i] and closes[i] < ema200[i]): continue
        
        regime = "BUY_MODE" if buy else "SELL_MODE"
        if buy:
            chain = ema20[i] > ema50[i] > ema144[i] > ema200[i]
            score = 40 if chain else 0
            if ema20s[i] > 0: score += 15
            if ema50s[i] > 0: score += 15
            if ema144s[i] > 0: score += 15
            if abs(ema20s[i]) > 0.1: score += 15
        else:
            chain = ema20[i] < ema50[i] < ema144[i] < ema200[i]
            score = 40 if chain else 0
            if ema20s[i] < 0: score += 15
            if ema50s[i] < 0: score += 15
            if ema144s[i] < 0: score += 15
            if abs(ema20s[i]) > 0.1: score += 15
        if score < 80: continue
        
        tol_pct = 0.3; pulled = False
        for ci in range(max(0, i-2), i+1):
            if regime == "BUY_MODE":
                for ev in [ema20[i], ema50[i]]:
                    if lows[ci] <= ev and closes[ci] >= ev and abs(lows[ci] - ev) / ev * 100 <= tol_pct:
                        pulled = True; break
            else:
                for ev in [ema20[i], ema50[i]]:
                    if highs[ci] >= ev and closes[ci] <= ev and abs(highs[ci] - ev) / ev * 100 <= tol_pct:
                        pulled = True; break
            if pulled: break
        if not pulled: continue
        
        o1, c1 = opens[i-1], closes[i-1]
        o2, h2, l2, c2 = opens[i], highs[i], lows[i], closes[i]
        if regime == "BUY_MODE":
            ck = is_bullish_engulfing(o1, c1, o2, c2)
            if not ck and abs(c2-o2) > (h2-l2)*0.15: ck = is_hammer(o2, h2, l2, c2)
            if not ck: ck = (c2 > o2 and abs(c2-o2)/max(h2-l2,1e-10) >= 0.5)
        else:
            ck = is_bearish_engulfing(o1, c1, o2, c2)
            if not ck and abs(c2-o2) > (h2-l2)*0.15: ck = is_shooting_star(o2, h2, l2, c2)
            if not ck: ck = (c2 < o2 and abs(c2-o2)/max(h2-l2,1e-10) >= 0.5)
        if not ck: continue
        
        if volumes[i] < vol_sma[i]: continue
        
        latest_adx = adx_arr[i]
        rvol = rvol_arr[i] if not np.isnan(rvol_arr[i]) else 0
        ema_range = max(ema20[i], ema50[i], ema144[i], ema200[i]) - min(ema20[i], ema50[i], ema144[i], ema200[i])
        ema_range_pct = ema_range / closes[i] * 100 if closes[i] > 0 else 0
        ema200_dist = abs(closes[i] - ema200[i]) / ema200[i] * 100 if ema200[i] > 0 else 0
        ema200_score = min(100, ema200_dist / 1.0 * 100)
        candle_score = 70
        if regime == "BUY_MODE":
            if is_bullish_engulfing(o1, c1, o2, c2): candle_score = 100
            elif is_hammer(o2, h2, l2, c2): candle_score = 85
        else:
            if is_bearish_engulfing(o1, c1, o2, c2): candle_score = 100
            elif is_shooting_star(o2, h2, l2, c2): candle_score = 85
        
        conf = (100*0.10 + score*0.20 + 100*0.15 + candle_score*0.10 +
                min(100, rvol/2.0*100)*0.10 + min(100, latest_adx/50*100)*0.15 +
                min(100, rvol/3.0*100)*0.10 + min(100, ema200_score)*0.10)
        if conf < 90.0: continue
        
        entry = closes[i]; cur_atr = atr_arr[i]; sl_atr = 1.5
        sl_dist = cur_atr * sl_atr; side = "LONG" if buy else "SHORT"
        ema_val = ema20[i]
        if side == "LONG":
            sl = entry - sl_dist
            if ema_val > 0 and ema_val < entry: sl = max(sl, ema_val - cur_atr * 0.2)
            tp1 = entry + abs(entry - sl) * 1.5; tp2 = entry + abs(entry - sl) * 3.0; tp3 = entry + abs(entry - sl) * 5.0
        else:
            sl = entry + sl_dist
            if ema_val > 0 and ema_val > entry: sl = min(sl, ema_val + cur_atr * 0.2)
            tp1 = entry - abs(sl - entry) * 1.5; tp2 = entry - abs(sl - entry) * 3.0; tp3 = entry - abs(sl - entry) * 5.0
        rr = abs(tp1 - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
        if rr < 1.5: continue
        
        eps = rr * min(score/100, 1.0) * min(latest_adx/50, 1.0) * min(rvol/3.0, 1.0)
        
        signals[i] = {"side": side, "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
                       "rr": rr, "conf": conf, "regime": regime, "score": eps,
                       "adx": latest_adx, "rvol": rvol, "trend_score": score,
                       "atr": cur_atr, "bar": i}
    
    indicators = {"ema20": ema20, "ema50": ema50, "ema144": ema144, "ema200": ema200,
                  "atr": atr_arr, "adx": adx_arr, "rvol": rvol_arr}
    return signals, indicators


# ══════════════════════════════════════════════════════════════════════
# PART 3: TRADE SIMULATION — BEFORE (baseline, DO NOT MODIFY)
# ══════════════════════════════════════════════════════════════════════

def simulate_trades_before(klines, signals, capital=10000):
    """BEFORE: Equal-weight 1% risk, fixed exits (baseline)."""
    opens = klines[:, 1]; highs = klines[:, 2]; lows = klines[:, 3]; closes = klines[:, 4]
    n = len(klines)
    atr_arr = atr_calc(highs, lows, closes, 14)
    
    equity = capital; equity_curve = [capital]; trades = []
    open_trade = None; last_signal_bar = -999
    
    for i in range(n):
        if open_trade is not None:
            t = open_trade; side = t["side"]; hold = i - t["entry_bar"]
            if side == "LONG":
                if lows[i] <= t["sl"]:
                    pnl = (t["sl"] - t["entry"]) * t["size"] - t["costs"]
                    t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "SL"
                    equity += pnl; trades.append(t); open_trade = None
                elif highs[i] >= t["tp3"]:
                    pnl = (t["tp3"] - t["entry"]) * t["remaining"] + t["locked"] - t["costs"]
                    t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "TP3"
                    equity += pnl; trades.append(t); open_trade = None
                elif not t["tp1_hit"] and highs[i] >= t["tp1"]:
                    t["tp1_hit"] = True; ps = t["remaining"] * 0.35
                    t["locked"] += (t["tp1"] - t["entry"]) * ps; t["remaining"] -= ps
                elif t["tp1_hit"] and not t["tp2_hit"] and highs[i] >= t["tp2"]:
                    t["tp2_hit"] = True; ps = t["remaining"] * 0.40 / 0.65
                    t["locked"] += (t["tp2"] - t["entry"]) * ps; t["remaining"] -= ps
                if t["tp1_hit"] and not t["be_moved"]:
                    if highs[i] >= t["entry"] + (t["entry"] - t["sl"]) * 1.0:
                        t["sl"] = t["entry"]; t["be_moved"] = True
                if t["tp1_hit"] and atr_arr[i] > 0:
                    trail = highs[i] - atr_arr[i] * 1.0
                    if trail > t["sl"]: t["sl"] = trail
            else:
                if highs[i] >= t["sl"]:
                    pnl = (t["entry"] - t["sl"]) * t["size"] - t["costs"]
                    t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "SL"
                    equity += pnl; trades.append(t); open_trade = None
                elif lows[i] <= t["tp3"]:
                    pnl = (t["entry"] - t["tp3"]) * t["remaining"] + t["locked"] - t["costs"]
                    t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "TP3"
                    equity += pnl; trades.append(t); open_trade = None
                elif not t["tp1_hit"] and lows[i] <= t["tp1"]:
                    t["tp1_hit"] = True; ps = t["remaining"] * 0.35
                    t["locked"] += (t["entry"] - t["tp1"]) * ps; t["remaining"] -= ps
                elif t["tp1_hit"] and not t["tp2_hit"] and lows[i] <= t["tp2"]:
                    t["tp2_hit"] = True; ps = t["remaining"] * 0.40 / 0.65
                    t["locked"] += (t["entry"] - t["tp2"]) * ps; t["remaining"] -= ps
                if t["tp1_hit"] and not t["be_moved"]:
                    if lows[i] <= t["entry"] - (t["sl"] - t["entry"]) * 1.0:
                        t["sl"] = t["entry"]; t["be_moved"] = True
                if t["tp1_hit"] and atr_arr[i] > 0:
                    trail = lows[i] + atr_arr[i] * 1.0
                    if trail < t["sl"]: t["sl"] = trail
            if open_trade and hold >= 48:
                if side == "LONG":
                    pnl = (closes[i] - t["entry"]) * t["remaining"] + t["locked"] - t["costs"]
                else:
                    pnl = (t["entry"] - closes[i]) * t["remaining"] + t["locked"] - t["costs"]
                t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "MAX_HOLD"
                equity += pnl; trades.append(t); open_trade = None
            equity_curve.append(equity + (0 if open_trade is None else
                ((closes[i]-open_trade["entry"])*open_trade["remaining"] if open_trade["side"]=="LONG"
                 else (open_trade["entry"]-closes[i])*open_trade["remaining"])))
            continue
        
        sig = signals.get(i)
        if sig is None or i < last_signal_bar + 6:
            equity_curve.append(equity); continue
        
        entry = sig["entry"]; sl = sig["sl"]; side = sig["side"]
        risk_amount = equity * 0.01; sl_dist = abs(entry - sl)
        if sl_dist <= 0: equity_curve.append(equity); continue
        size = risk_amount / sl_dist; cost = entry * size * 0.0006
        open_trade = {"side": side, "entry": entry, "entry_bar": i, "sl": sl,
                      "tp1": sig["tp1"], "tp2": sig["tp2"], "tp3": sig["tp3"],
                      "size": size, "remaining": size, "locked": 0, "costs": cost,
                      "pnl": 0, "exit_bar": 0, "exit_reason": "",
                      "tp1_hit": False, "tp2_hit": False, "be_moved": False,
                      "regime": sig.get("regime", ""), "score": sig.get("score", 0)}
        last_signal_bar = i; equity_curve.append(equity)
    
    if open_trade:
        t = open_trade
        if t["side"] == "LONG":
            pnl = (closes[-1] - t["entry"]) * t["remaining"] + t["locked"] - t["costs"]
        else:
            pnl = (t["entry"] - closes[-1]) * t["remaining"] + t["locked"] - t["costs"]
        t["pnl"] = pnl; t["exit_bar"] = len(klines)-1; t["exit_reason"] = "END"
        equity += pnl; trades.append(t)
    
    return {"trades": trades, "equity_curve": equity_curve, "final_capital": equity}


# ══════════════════════════════════════════════════════════════════════
# PART 4: TRADE SIMULATION — AFTER V2 (enhanced exits)
# ══════════════════════════════════════════════════════════════════════

def simulate_trades_v2(klines, signals, capital=10000,
                       sl_atr=1.5, tp1_rr=1.5, be_r=0.5, trail_atr=0.5,
                       tp1_pct=0.50, tp2_pct=0.30, max_hold=24, risk_pct=0.01,
                       vol_exit=True, structure_exit=True):
    """V2 AFTER: Enhanced exits with optional volatility and structure exits."""
    opens = klines[:, 1]; highs = klines[:, 2]; lows = klines[:, 3]; closes = klines[:, 4]
    n = len(klines)
    atr_arr = atr_calc(highs, lows, closes, 14)
    
    equity = capital; equity_curve = [capital]; trades = []
    open_trade = None; last_signal_bar = -999
    
    for i in range(n):
        if open_trade is not None:
            t = open_trade; side = t["side"]; hold = i - t["entry_bar"]
            r_dist = abs(t["entry"] - t["sl"])
            
            if side == "LONG":
                if lows[i] <= t["sl"]:
                    pnl = (t["sl"] - t["entry"]) * t["size"] - t["costs"]
                    t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "SL"
                    equity += pnl; trades.append(t); open_trade = None
                elif highs[i] >= t["tp3"]:
                    pnl = (t["tp3"] - t["entry"]) * t["remaining"] + t["locked"] - t["costs"]
                    t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "TP3"
                    equity += pnl; trades.append(t); open_trade = None
                elif not t["tp1_hit"] and highs[i] >= t["tp1"]:
                    t["tp1_hit"] = True; ps = t["remaining"] * tp1_pct
                    t["locked"] += (t["tp1"] - t["entry"]) * ps; t["remaining"] -= ps
                elif t["tp1_hit"] and not t["tp2_hit"] and highs[i] >= t["tp2"]:
                    t["tp2_hit"] = True
                    rem_frac = t["remaining"] / (1.0 - tp1_pct)
                    ps = rem_frac * tp2_pct
                    t["locked"] += (t["tp2"] - t["entry"]) * ps; t["remaining"] -= ps
                # Earlier breakeven
                if t["tp1_hit"] and not t["be_moved"]:
                    if highs[i] >= t["entry"] + r_dist * be_r:
                        t["sl"] = t["entry"] + r_dist * 0.05
                        t["be_moved"] = True
                # Tighter trailing
                if t["tp1_hit"] and atr_arr[i] > 0:
                    trail = highs[i] - atr_arr[i] * trail_atr
                    if trail > t["sl"]: t["sl"] = trail
                # ═══ V2: VOLATILITY EXIT (optional) ═══
                if vol_exit:
                    entry_atr = t.get("entry_atr", 0)
                    if entry_atr > 0 and atr_arr[i] > 0:
                        if atr_arr[i] < entry_atr * 0.25 and hold >= 16:
                            pnl = (closes[i] - t["entry"]) * t["remaining"] + t["locked"] - t["costs"]
                            t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "VOL_EXIT"
                            equity += pnl; trades.append(t); open_trade = None
                            equity_curve.append(equity); continue
                # ═══ V2: STRUCTURE EXIT (optional) ═══
                if structure_exit and t["tp1_hit"] and i >= 20 and open_trade is not None:
                    recent_lows = lows[i-19:i+1]
                    min_recent = np.min(recent_lows)
                    if lows[i] <= min_recent * 1.001:  # new 20-bar low
                        pnl = (closes[i] - t["entry"]) * t["remaining"] + t["locked"] - t["costs"]
                        t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "STRUCT_EXIT"
                        equity += pnl; trades.append(t); open_trade = None
                        equity_curve.append(equity); continue
                # Time decay tighten
                if hold >= 24 and not t["tp1_hit"]:
                    tighten = t["entry"] - r_dist * 0.5
                    if tighten > t["sl"]: t["sl"] = tighten
            else:
                if highs[i] >= t["sl"]:
                    pnl = (t["entry"] - t["sl"]) * t["size"] - t["costs"]
                    t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "SL"
                    equity += pnl; trades.append(t); open_trade = None
                elif lows[i] <= t["tp3"]:
                    pnl = (t["entry"] - t["tp3"]) * t["remaining"] + t["locked"] - t["costs"]
                    t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "TP3"
                    equity += pnl; trades.append(t); open_trade = None
                elif not t["tp1_hit"] and lows[i] <= t["tp1"]:
                    t["tp1_hit"] = True; ps = t["remaining"] * tp1_pct
                    t["locked"] += (t["entry"] - t["tp1"]) * ps; t["remaining"] -= ps
                elif t["tp1_hit"] and not t["tp2_hit"] and lows[i] <= t["tp2"]:
                    t["tp2_hit"] = True
                    rem_frac = t["remaining"] / (1.0 - tp1_pct)
                    ps = rem_frac * tp2_pct
                    t["locked"] += (t["entry"] - t["tp2"]) * ps; t["remaining"] -= ps
                if t["tp1_hit"] and not t["be_moved"]:
                    if lows[i] <= t["entry"] - r_dist * be_r:
                        t["sl"] = t["entry"] - r_dist * 0.05
                        t["be_moved"] = True
                if t["tp1_hit"] and atr_arr[i] > 0:
                    trail = lows[i] + atr_arr[i] * trail_atr
                    if trail < t["sl"]: t["sl"] = trail
                # ═══ V2: VOLATILITY EXIT (SHORT, optional) ═══
                if vol_exit:
                    entry_atr = t.get("entry_atr", 0)
                    if entry_atr > 0 and atr_arr[i] > 0:
                        if atr_arr[i] < entry_atr * 0.25 and hold >= 16:
                            pnl = (t["entry"] - closes[i]) * t["remaining"] + t["locked"] - t["costs"]
                            t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "VOL_EXIT"
                            equity += pnl; trades.append(t); open_trade = None
                            equity_curve.append(equity); continue
                # ═══ V2: STRUCTURE EXIT (SHORT, optional) ═══
                if structure_exit and t["tp1_hit"] and i >= 20 and open_trade is not None:
                    recent_highs = highs[i-19:i+1]
                    max_recent = np.max(recent_highs)
                    if highs[i] >= max_recent * 0.999:  # new 20-bar high
                        pnl = (t["entry"] - closes[i]) * t["remaining"] + t["locked"] - t["costs"]
                        t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "STRUCT_EXIT"
                        equity += pnl; trades.append(t); open_trade = None
                        equity_curve.append(equity); continue
                # Time decay tighten
                if hold >= 24 and not t["tp1_hit"]:
                    tighten = t["entry"] + r_dist * 0.5
                    if tighten < t["sl"]: t["sl"] = tighten
            
            if open_trade and hold >= max_hold:
                if side == "LONG":
                    pnl = (closes[i] - t["entry"]) * t["remaining"] + t["locked"] - t["costs"]
                else:
                    pnl = (t["entry"] - closes[i]) * t["remaining"] + t["locked"] - t["costs"]
                t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "MAX_HOLD"
                equity += pnl; trades.append(t); open_trade = None
            
            equity_curve.append(equity + (0 if open_trade is None else
                ((closes[i]-open_trade["entry"])*open_trade["remaining"] if open_trade["side"]=="LONG"
                 else (open_trade["entry"]-closes[i])*open_trade["remaining"])))
            continue
        
        sig = signals.get(i)
        if sig is None or i < last_signal_bar + COOLDOWN_BARS:
            equity_curve.append(equity); continue
        
        entry = sig["entry"]; sl = sig["sl"]; side = sig["side"]
        risk_amount = equity * risk_pct; sl_dist = abs(entry - sl)
        if sl_dist <= 0: equity_curve.append(equity); continue
        size = risk_amount / sl_dist; cost = entry * size * 0.0006
        
        if side == "LONG":
            tp1 = entry + sl_dist * tp1_rr
            tp2 = entry + sl_dist * 3.0; tp3 = entry + sl_dist * 5.0
        else:
            tp1 = entry - sl_dist * tp1_rr
            tp2 = entry - sl_dist * 3.0; tp3 = entry - sl_dist * 5.0
        
        open_trade = {"side": side, "entry": entry, "entry_bar": i, "sl": sl,
                      "tp1": tp1, "tp2": tp2, "tp3": tp3,
                      "size": size, "remaining": size, "locked": 0, "costs": cost,
                      "pnl": 0, "exit_bar": 0, "exit_reason": "",
                      "tp1_hit": False, "tp2_hit": False, "be_moved": False,
                      "regime": sig.get("regime", ""), "score": sig.get("score", 0),
                      "entry_atr": atr_arr[i] if not np.isnan(atr_arr[i]) else 0}
        last_signal_bar = i; equity_curve.append(equity)
    
    if open_trade:
        t = open_trade
        if t["side"] == "LONG":
            pnl = (closes[-1] - t["entry"]) * t["remaining"] + t["locked"] - t["costs"]
        else:
            pnl = (t["entry"] - closes[-1]) * t["remaining"] + t["locked"] - t["costs"]
        t["pnl"] = pnl; t["exit_bar"] = len(klines)-1; t["exit_reason"] = "END"
        equity += pnl; trades.append(t)
    
    return {"trades": trades, "equity_curve": equity_curve, "final_capital": equity}


# ══════════════════════════════════════════════════════════════════════
# PART 5: SYMBOL CLASSIFIER
# ══════════════════════════════════════════════════════════════════════

def classify_symbol(sym_metrics):
    pf = sym_metrics.get("pf", 0)
    exp = sym_metrics.get("expectancy", 0)
    rf = sym_metrics.get("recovery_factor", 0)
    mdd = sym_metrics.get("max_dd", 100)
    trades = sym_metrics.get("trades", 0)
    
    if trades < 5: return "DISABLED", 0.0
    if pf >= 1.5 and exp > 5 and rf >= 1.5 and mdd < 20: return "ELITE", 1.15
    if pf >= 1.2 and exp > 2 and rf >= 0.8 and mdd < 30: return "GOOD", 0.80
    if pf >= 1.0 and exp > 0: return "NEUTRAL", 0.50
    if pf >= 0.8: return "POOR", 0.10
    return "DISABLED", 0.0


# ══════════════════════════════════════════════════════════════════════
# PART 6: REGIME DETECTOR (LOCKED)
# ══════════════════════════════════════════════════════════════════════

def detect_regime(indicators, bar_idx, klines):
    if bar_idx < 220: return "UNKNOWN", 0.0
    adx = indicators["adx"][bar_idx]
    atr = indicators["atr"][bar_idx]
    start = max(0, bar_idx - 50)
    avg_atr = np.nanmean(indicators["atr"][start:bar_idx]) if bar_idx > start else atr
    atr_ratio = atr / avg_atr if avg_atr > 0 else 1.0
    if adx >= 25: return "TRENDING", min(adx / 40, 1.0)
    elif adx >= 20: return "PULLBACK", 0.6
    elif atr_ratio >= 2.0: return "HIGH_VOL", min(atr_ratio / 3.0, 1.0)
    elif atr_ratio <= 0.5: return "LOW_VOL", 0.3
    else: return "RANGE", 0.4


def regime_compatibility(regime, signal_side):
    mapping = {"TRENDING": 1.0, "PULLBACK": 0.9, "HIGH_VOL": 0.6, "RANGE": 0.3, "LOW_VOL": 0.5}
    return mapping.get(regime, 0.5)


# ══════════════════════════════════════════════════════════════════════
# PART 7: DYNAMIC RISK ENGINE — Kelly + DD-Adaptive
# ══════════════════════════════════════════════════════════════════════

def compute_kelly_fraction(sym_metrics):
    """Compute fractional Kelly from symbol's historical win rate and avg win/loss."""
    wr = sym_metrics.get("win_rate", 50) / 100.0
    avg_win = sym_metrics.get("avg_win", 1)
    avg_loss = abs(sym_metrics.get("avg_loss", 1))
    if avg_win <= 0 or avg_loss <= 0: return 0.01
    rr = avg_win / avg_loss
    kelly = wr - (1 - wr) / rr
    # 80% Kelly for growth, capped at 4%
    return max(0.01, min(kelly * 0.80, 0.04))


def compute_dd_multiplier(equity, peak_equity):
    """Reduce risk proportionally during drawdowns."""
    if peak_equity <= 0: return 1.0
    dd_pct = (peak_equity - equity) / peak_equity * 100
    if dd_pct > 25: return 0.40   # Severe DD: 60% risk reduction
    if dd_pct > 20: return 0.55   # Major DD: 45% reduction
    if dd_pct > 15: return 0.70   # Moderate DD: 30% reduction
    if dd_pct > 10: return 0.85   # Minor DD: 15% reduction
    return 1.0


def compute_position_size_v2(equity, signal, sym_weight, kelly_frac,
                              dd_mult, regime_compat, max_risk_pct=MAX_RISK_PER_TRADE,
                              max_pos_pct=MAX_POSITION_PCT):
    """V2: Dynamic sizing with Kelly, DD-adaptive, classification, regime."""
    sl_dist = abs(signal["entry"] - signal["sl"])
    if sl_dist <= 0: return 0
    
    # Base risk from Kelly
    kelly_risk = equity * kelly_frac
    
    # Scale by classification weight
    class_risk = kelly_risk * sym_weight
    
    # Scale by signal quality (EPS 0-1)
    eps = signal.get("score", 0.5)
    quality_mult = 0.5 + eps * 0.5
    
    # Scale by regime compatibility
    # Scale by DD multiplier
    risk_amount = class_risk * quality_mult * regime_compat * dd_mult
    
    # Hard caps
    risk_amount = min(risk_amount, equity * max_risk_pct)
    
    size = risk_amount / sl_dist
    
    # Cap position value
    pos_value = signal["entry"] * size
    max_pos_value = equity * max_pos_pct
    if pos_value > max_pos_value:
        size = max_pos_value / signal["entry"]
    
    return size


# ══════════════════════════════════════════════════════════════════════
# PART 8: PORTFOLIO SIMULATION — V2 Multi-Symbol Chronological
# ══════════════════════════════════════════════════════════════════════

def simulate_portfolio_v2(all_symbol_data, mode="AFTER", capital=10000, exit_params=None):
    """V2 multi-symbol portfolio with dynamic sizing, heat management, sector caps."""
    if exit_params is None:
        exit_params = {"sl_atr": 1.5, "tp1_rr": 1.5, "be_r": 0.5, "trail_atr": 0.5,
                       "tp1_pct": 0.50, "tp2_pct": 0.30, "max_hold": 24, "risk_pct": 0.01}
    
    # ── Step 1: Pre-compute per-symbol trades ──
    all_trades = []
    sym_trade_counts = {}
    
    for sym, data in all_symbol_data.items():
        klines = data["klines"]
        signals = data["signals"]
        cls = data.get("classification", ("NEUTRAL", 0.5))
        
        if mode == "BEFORE":
            result = simulate_trades_before(klines, signals, capital)
        else:
            result = simulate_trades_v2(klines, signals, capital, **exit_params)
        
        for t in result["trades"]:
            t["symbol"] = sym
            t["sector"] = get_sector(sym)
            t["classification"] = cls[0]
            t["cls_weight"] = cls[1]
            all_trades.append(t)
            sym_trade_counts[sym] = sym_trade_counts.get(sym, 0) + 1
    
    if not all_trades:
        return {"trades": [], "portfolio_ec": [capital], "final_capital": capital,
                "symbol_results": {}, "total_profit": 0, "portfolio_pf": 0,
                "expectancy": 0, "sharpe": 0, "sortino": 0, "max_dd": 0,
                "cagr": 0, "calmar": 0, "recovery_factor": 0,
                "total_trades": 0, "total_costs": 0,
                "profitable_syms": 0, "total_syms": 0, "win_rate": 0}
    
    # Sort by entry bar
    all_trades.sort(key=lambda t: t["entry_bar"])
    
    # ── BEFORE: Simple aggregation (no constraints) ──
    if mode == "BEFORE":
        return _aggregate_portfolio(all_trades, capital, all_symbol_data)
    
    # ── AFTER V2: Portfolio-level constrained simulation ──
    equity = capital
    peak_equity = capital
    open_positions = []
    accepted_trades = []
    equity_curve = [capital]
    dd_mult = 1.0
    last_signal_bar = {}
    heat = 0.0
    sector_exposure = {}
    
    # Build exit timeline for quick lookup
    exits_by_bar = {}
    for t in all_trades:
        exits_by_bar.setdefault(t["exit_bar"], []).append(t)
    
    trade_idx = 0
    max_bar = max(t["exit_bar"] for t in all_trades) + 1
    
    for bar in range(max_bar + 1):
        # ── 3a: Process exits ──
        closed_this_bar = []
        for pos in open_positions:
            if pos["exit_bar"] <= bar:
                closed_this_bar.append(pos)
        
        for pos in closed_this_bar:
            # Use pre-computed PnL, scaled to dynamic size
            orig_size = pos.get("orig_size", pos["size"])
            if orig_size > 0 and pos["size"] != orig_size:
                scale = pos["size"] / orig_size
                actual_pnl = pos["pnl_raw"] * scale
            else:
                actual_pnl = pos["pnl_raw"]
            
            actual_pnl -= pos.get("costs", 0)
            equity += actual_pnl
            pos["pnl"] = actual_pnl
            accepted_trades.append(pos)
            open_positions.remove(pos)
            
            # Update heat
            heat = max(0, heat - pos.get("heat_contribution", 0))
            sector_exposure[pos["sector"]] = max(0, sector_exposure.get(pos["sector"], 0) - 1)
        
        # ── 3b: Update DD-adaptive multiplier ──
        peak_equity = max(peak_equity, equity)
        dd_mult = compute_dd_multiplier(equity, peak_equity)
        
        # ── 3c: Process new entries ──
        while trade_idx < len(all_trades) and all_trades[trade_idx]["entry_bar"] <= bar:
            t = all_trades[trade_idx]
            trade_idx += 1
            
            if t["entry_bar"] != bar:
                continue
            
            sym = t["symbol"]
            
            # ── CONSTRAINT 1: Cooldown ──
            last_bar = last_signal_bar.get(sym, -999)
            if bar < last_bar + COOLDOWN_BARS:
                continue
            
            # ── CONSTRAINT 2: Max concurrent ──
            if len(open_positions) >= MAX_CONCURRENT:
                continue
            
            # ── CONSTRAINT 3: Sector exposure ──
            sector = t["sector"]
            if sector_exposure.get(sector, 0) >= MAX_PER_SECTOR:
                continue
            
            # ── CONSTRAINT 4: Portfolio heat ──
            sl_dist = abs(t["entry"] - t["sl"])
            if sl_dist <= 0: continue
            
            # Estimate trade risk as fraction of equity
            kelly = compute_kelly_fraction(all_symbol_data[sym].get("metrics", {}))
            estimated_risk = equity * kelly * t["cls_weight"] * dd_mult
            estimated_risk = min(estimated_risk, equity * MAX_RISK_PER_TRADE)
            est_heat = estimated_risk / equity if equity > 0 else 0
            
            if heat + est_heat > MAX_HEAT:
                continue
            
            # ── CONSTRAINT 5: Classification weight > 0 ──
            if t["cls_weight"] <= 0:
                continue
            
            # ── Compute actual position size ──
            regime = detect_regime(all_symbol_data[sym]["indicators"], bar, all_symbol_data[sym]["klines"])
            regime_compat = regime_compatibility(regime[0], t["side"])
            
            size = compute_position_size_v2(
                equity, t, t["cls_weight"], kelly, dd_mult, regime_compat
            )
            if size <= 0: continue
            
            cost = t["entry"] * size * 0.0006
            
            # Clone trade with dynamic sizing
            pos = dict(t)
            pos["orig_size"] = t["size"]  # Store original for PnL scaling
            pos["pnl_raw"] = t["pnl"]     # Store original PnL
            pos["size"] = size
            pos["remaining"] = size
            pos["locked"] = 0
            pos["costs"] = cost
            pos["pnl"] = 0
            pos["heat_contribution"] = est_heat
            
            open_positions.append(pos)
            last_signal_bar[sym] = bar
            heat += est_heat
            sector_exposure[sector] = sector_exposure.get(sector, 0) + 1
        
        # ── 3d: Record equity curve ──
        unrealized = 0
        for pos in open_positions:
            sym = pos["symbol"]
            klines = all_symbol_data[sym]["klines"]
            if bar < len(klines):
                c = klines[bar, 4]
                if pos["side"] == "LONG":
                    unrealized += (c - pos["entry"]) * pos["remaining"]
                else:
                    unrealized += (pos["entry"] - c) * pos["remaining"]
        
        total_eq = equity + unrealized
        equity_curve.append(total_eq)
        peak_equity = max(peak_equity, total_eq)
    
    # Close remaining positions
    for pos in open_positions:
        sym = pos["symbol"]
        klines = all_symbol_data[sym]["klines"]
        c = klines[-1, 4]
        if pos["side"] == "LONG":
            raw_pnl = (c - pos["entry"]) * pos["remaining"] + pos["locked"] - pos["costs"]
        else:
            raw_pnl = (pos["entry"] - c) * pos["remaining"] + pos["locked"] - pos["costs"]
        orig_size = pos.get("orig_size", pos["size"])
        if orig_size > 0 and pos["size"] != orig_size:
            scale = pos["size"] / orig_size
            raw_pnl = pos["pnl_raw"] * scale - pos["costs"]
        equity += raw_pnl
        pos["pnl"] = raw_pnl
        pos["exit_reason"] = "END"
        accepted_trades.append(pos)
    
    return _compute_portfolio_metrics(accepted_trades, equity_curve, capital, all_symbol_data)


def _aggregate_portfolio(all_trades, capital, all_symbol_data):
    """Simple aggregation for BEFORE mode (no constraints)."""
    equity = capital
    equity_curve = [capital]
    
    bar_pnl = {}
    for t in all_trades:
        exit_bar = t["exit_bar"]
        bar_pnl[exit_bar] = bar_pnl.get(exit_bar, 0) + t["pnl"]
    
    max_bar = max(bar_pnl.keys()) if bar_pnl else 0
    running = capital
    for bar in range(max_bar + 2):
        running += bar_pnl.get(bar, 0)
        equity_curve.append(running)
    
    return _compute_portfolio_metrics(all_trades, equity_curve, capital, all_symbol_data)


def _compute_portfolio_metrics(trades, equity_curve, capital, all_symbol_data=None):
    """Compute portfolio-level metrics from trades and equity curve."""
    ec = np.array(equity_curve)
    if len(ec) < 2: ec = np.array([capital, capital])
    
    pk = np.maximum.accumulate(ec)
    dd = (pk - ec) / np.where(pk > 0, pk, 1) * 100
    final_eq = float(ec[-1])
    mdd = float(np.max(dd)) if len(ec) > 1 else 0
    
    pnls_all = [t.get("pnl", 0) for t in trades]
    winners = [p for p in pnls_all if p > 0]
    losers = [p for p in pnls_all if p <= 0]
    gp = sum(winners); gl = abs(sum(losers))
    
    rets = np.diff(ec) / np.where(ec[:-1] > 0, ec[:-1], 1)
    rets = rets[np.isfinite(rets)]
    sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(365.25*24)) if len(rets) > 1 and np.std(rets) > 0 else 0
    neg = rets[rets < 0]
    sortino = float(np.mean(rets) / np.std(neg) * np.sqrt(365.25*24)) if len(neg) > 1 and np.std(neg) > 0 else 0
    
    total_bars = len(ec)
    years = total_bars / (365.25 * 24) if total_bars > 0 else 1
    cagr = (final_eq / capital) ** (1/years) - 1 if final_eq > 0 and capital > 0 and years > 0 else 0
    calmar = cagr / (mdd / 100) if mdd > 0 else 0
    rf = (final_eq - capital) / mdd if mdd > 0 and final_eq > capital else 0
    
    total_costs = sum(t.get("costs", 0) for t in trades)
    
    # Per-symbol results
    symbol_results = {}
    if all_symbol_data:
        sym_trades = {}
        for t in trades:
            s = t["symbol"]
            sym_trades.setdefault(s, []).append(t)
        for s, st in sym_trades.items():
            sp = [t.get("pnl", 0) for t in st]
            sw = [p for p in sp if p > 0]; sl = [p for p in sp if p <= 0]
            sgp = sum(sw); sgl = abs(sum(sl))
            symbol_results[s] = {
                "trades": len(st), "net_profit": sum(sp),
                "pf": sgp/sgl if sgl > 0 else 999,
                "expectancy": statistics.mean(sp) if sp else 0,
                "win_rate": len(sw)/len(st)*100 if st else 0,
            }
    
    return {
        "trades": trades, "portfolio_ec": ec.tolist(), "final_capital": final_eq,
        "total_profit": final_eq - capital,
        "portfolio_pf": gp / gl if gl > 0 else 999,
        "expectancy": statistics.mean(pnls_all) if pnls_all else 0,
        "avg_winner": statistics.mean(winners) if winners else 0,
        "avg_loser": statistics.mean(losers) if losers else 0,
        "win_rate": len(winners) / len(trades) * 100 if trades else 0,
        "max_dd": mdd,
        "sharpe": sharpe, "sortino": sortino,
        "cagr": cagr, "calmar": calmar, "recovery_factor": rf,
        "total_trades": len(trades), "total_costs": total_costs,
        "symbol_results": symbol_results,
        "profitable_syms": sum(1 for s, m in symbol_results.items() if m["net_profit"] > 0),
        "total_syms": len(symbol_results),
    }


# ══════════════════════════════════════════════════════════════════════
# PART 9: MONTE CARLO
# ══════════════════════════════════════════════════════════════════════

def monte_carlo(trades_pnl, n_sims=1000):
    if not trades_pnl or len(trades_pnl) < 10: return None
    pnl_array = np.array(trades_pnl)
    n_trades = len(pnl_array)
    
    final_equities, max_dds, sharpes = [], [], []
    
    for _ in range(n_sims):
        sampled = np.random.choice(pnl_array, size=n_trades, replace=True)
        ec = np.cumsum(sampled) + 10000
        ec = np.insert(ec, 0, 10000)
        pk = np.maximum.accumulate(ec)
        dd = (pk - ec) / np.where(pk > 0, pk, 1) * 100
        max_dds.append(float(np.max(dd)))
        final_equities.append(float(ec[-1]))
        rets = np.diff(ec) / np.where(ec[:-1] > 0, ec[:-1], 1)
        rets = rets[np.isfinite(rets)]
        s = float(np.mean(rets)/np.std(rets)*np.sqrt(365.25*24)) if len(rets) > 1 and np.std(rets) > 0 else 0
        sharpes.append(s)
    
    fe, md, sh = np.array(final_equities), np.array(max_dds), np.array(sharpes)
    return {
        "n_sims": n_sims,
        "final_equity": {
            "mean": float(np.mean(fe)), "median": float(np.median(fe)),
            "std": float(np.std(fe)),
            "p5": float(np.percentile(fe, 5)), "p25": float(np.percentile(fe, 25)),
            "p75": float(np.percentile(fe, 75)), "p95": float(np.percentile(fe, 95)),
            "prob_profit": float(np.mean(fe > 10000)),
        },
        "max_drawdown": {
            "mean": float(np.mean(md)), "median": float(np.median(md)),
            "p5": float(np.percentile(md, 5)), "p95": float(np.percentile(md, 95)),
        },
        "sharpe": {
            "mean": float(np.mean(sh)), "median": float(np.median(sh)),
            "p5": float(np.percentile(sh, 5)), "p95": float(np.percentile(sh, 95)),
        },
    }


# ══════════════════════════════════════════════════════════════════════
# PART 10: WALK-FORWARD VALIDATION
# ══════════════════════════════════════════════════════════════════════

def walk_forward_validation(all_symbol_data, train_pct=0.7, exit_params=None, capital=10000):
    window_results = []
    for sym, data in all_symbol_data.items():
        klines = data["klines"]; signals = data["signals"]; n = len(klines)
        if n < 500: continue
        train_end = int(n * train_pct)
        
        train_signals = {k: v for k, v in signals.items() if k < train_end}
        test_signals = {k: v for k, v in signals.items() if k >= train_end}
        
        # Train: classify
        train_result = simulate_trades_before(klines[:train_end], train_signals, capital)
        if train_result["trades"]:
            pnls = [t["pnl"] for t in train_result["trades"]]
            winners = [p for p in pnls if p > 0]; losers = [p for p in pnls if p <= 0]
            gp = sum(winners); gl = abs(sum(losers))
            ec = np.array(train_result["equity_curve"])
            pk = np.maximum.accumulate(ec)
            dd = (pk - ec) / np.where(pk > 0, pk, 1) * 100
            mdd = float(np.max(dd))
            metrics = {"trades": len(pnls), "pf": gp/gl if gl>0 else 999,
                       "expectancy": statistics.mean(pnls), "max_dd": mdd,
                       "recovery_factor": (train_result["final_capital"]-capital)/mdd if mdd>0 else 0,
                       "win_rate": len(winners)/len(pnls)*100 if pnls else 0,
                       "avg_win": statistics.mean(winners) if winners else 0,
                       "avg_loss": statistics.mean(losers) if losers else 0}
            classification = classify_symbol(metrics)
        else:
            classification = ("DISABLED", 0.0)
            metrics = {"trades": 0, "pf": 0, "expectancy": 0, "max_dd": 0, "recovery_factor": 0}
        
        # Test: AFTER V2 on OOS
        test_klines = klines[train_end:]
        test_signals_ri = {k - train_end: v for k, v in test_signals.items()}
        
        # Always test OOS regardless of classification — just scale size by weight
        cls_weight = classification[1] if classification[1] > 0 else 0.10
        if exit_params:
            ep = dict(exit_params)
            ep["risk_pct"] = exit_params.get("risk_pct", 0.01) * cls_weight
            test_result = simulate_trades_v2(test_klines, test_signals_ri, capital, **ep)
        else:
            test_result = simulate_trades_before(test_klines, test_signals_ri, capital)
        
        if test_result["trades"]:
            pnls = [t["pnl"] for t in test_result["trades"]]
            winners = [p for p in pnls if p > 0]; losers = [p for p in pnls if p <= 0]
            gp = sum(winners); gl = abs(sum(losers))
            test_pf = gp/gl if gl>0 else 999
            ec = np.array(test_result["equity_curve"])
            pk = np.maximum.accumulate(ec)
            dd = (pk - ec) / np.where(pk > 0, pk, 1) * 100
            test_mdd = float(np.max(dd))
            rets = np.diff(ec) / np.where(ec[:-1] > 0, ec[:-1], 1)
            rets = rets[np.isfinite(rets)]
            test_sharpe = float(np.mean(rets)/np.std(rets)*np.sqrt(365.25*24)) if len(rets)>1 and np.std(rets)>0 else 0
        else:
            test_pf = 0; test_mdd = 0; test_sharpe = 0
        
        window_results.append({
            "symbol": sym, "classification": classification[0],
            "train_trades": metrics["trades"], "train_pf": metrics["pf"],
            "train_exp": metrics["expectancy"],
            "test_trades": len(test_result["trades"]),
            "test_pf": test_pf, "test_sharpe": test_sharpe, "test_mdd": test_mdd,
            "test_profit": test_result["final_capital"] - capital,
            "oos_profitable": test_result["final_capital"] > capital,
        })
    
    if not window_results: return None
    # Only count symbols that actually traded OOS
    traded_syms = [w for w in window_results if w["test_trades"] > 0]
    profitable_oos = sum(1 for w in traded_syms if w["oos_profitable"])
    test_pfs = [w["test_pf"] for w in window_results if 0 < w["test_pf"] < 999]
    return {
        "n_symbols": len(window_results),
        "n_traded": len(traded_syms),
        "profitable_oos": profitable_oos,
        "profitable_pct": profitable_oos / len(traded_syms) * 100 if traded_syms else 0,
        "avg_test_pf": statistics.mean(test_pfs) if test_pfs else 0,
        "details": window_results,
    }


# ══════════════════════════════════════════════════════════════════════
# PART 11: OUT-OF-SAMPLE VALIDATION
# ══════════════════════════════════════════════════════════════════════

def out_of_sample_split(all_symbol_data, oos_pct=0.30, capital=10000, exit_params=None):
    """Split each symbol's data into in-sample (70%) and out-of-sample (30%).
    Train classification on IS, test on OOS with AFTER exits."""
    oos_results = {}
    for sym, data in all_symbol_data.items():
        klines = data["klines"]; signals = data["signals"]; n = len(klines)
        if n < 500: continue
        split = int(n * (1 - oos_pct))
        
        is_signals = {k: v for k, v in signals.items() if k < split}
        oos_signals = {k: v for k, v in signals.items() if k >= split}
        
        # Train on IS
        is_result = simulate_trades_before(klines[:split], is_signals, capital)
        if is_result["trades"]:
            pnls = [t["pnl"] for t in is_result["trades"]]
            winners = [p for p in pnls if p > 0]; losers = [p for p in pnls if p <= 0]
            gp = sum(winners); gl = abs(sum(losers))
            ec = np.array(is_result["equity_curve"])
            pk = np.maximum.accumulate(ec)
            dd = (pk - ec) / np.where(pk > 0, pk, 1) * 100
            mdd = float(np.max(dd))
            metrics = {"trades": len(pnls), "pf": gp/gl if gl>0 else 999,
                       "expectancy": statistics.mean(pnls), "max_dd": mdd,
                       "recovery_factor": (is_result["final_capital"]-capital)/mdd if mdd>0 else 0,
                       "win_rate": len(winners)/len(pnls)*100 if pnls else 0,
                       "avg_win": statistics.mean(winners) if winners else 0,
                       "avg_loss": statistics.mean(losers) if losers else 0}
            classification = classify_symbol(metrics)
        else:
            classification = ("DISABLED", 0.0)
        
        # Test on OOS with V2 exits
        test_klines = klines[split:]
        oos_signals_ri = {k - split: v for k, v in oos_signals.items()}
        
        # Always test OOS regardless of classification — scale size by weight
        cls_weight = classification[1] if classification[1] > 0 else 0.10
        oos_risk = 0.01 * cls_weight
        if exit_params:
            ep = dict(exit_params)
            ep["risk_pct"] = oos_risk
            oos_result = simulate_trades_v2(test_klines, oos_signals_ri, capital, **ep)
        else:
            oos_result = simulate_trades_v2(test_klines, oos_signals_ri, capital,
                                            tp1_rr=1.5, be_r=0.5, trail_atr=0.5,
                                            tp1_pct=0.50, tp2_pct=0.30, max_hold=24,
                                            risk_pct=oos_risk, vol_exit=False, structure_exit=False)
        
        oos_pf = 0; oos_mdd = 0
        if oos_result["trades"]:
            pnls = [t["pnl"] for t in oos_result["trades"]]
            winners = [p for p in pnls if p > 0]; losers = [p for p in pnls if p <= 0]
            gp = sum(winners); gl = abs(sum(losers))
            oos_pf = gp/gl if gl>0 else 999
            ec = np.array(oos_result["equity_curve"])
            if len(ec) > 1:
                pk = np.maximum.accumulate(ec)
                dd = (pk - ec) / np.where(pk > 0, pk, 1) * 100
                oos_mdd = float(np.max(dd))
        
        oos_results[sym] = {
            "classification": classification[0],
            "trades": len(oos_result["trades"]),
            "pf": oos_pf, "mdd": oos_mdd,
            "profit": oos_result["final_capital"] - capital,
            "profitable": oos_result["final_capital"] > capital,
        }
    
    # Only count symbols that actually traded OOS
    traded_syms = {s: r for s, r in oos_results.items() if r["trades"] > 0}
    profitable_oos = sum(1 for s, r in traded_syms.items() if r["profitable"])
    total_traded = len(traded_syms)
    return {
        "n_symbols": len(oos_results),
        "n_traded": total_traded,
        "profitable_oos": profitable_oos,
        "profitable_pct": profitable_oos / total_traded * 100 if total_traded > 0 else 0,
        "oos_results": oos_results,
    }


# ══════════════════════════════════════════════════════════════════════
# PART 12: EXIT OPTIMIZATION (Enhanced Grid Search)
# ══════════════════════════════════════════════════════════════════════

def optimize_exits(all_symbol_data, capital=10000):
    param_grid = [
        # AGGRESSIVE configs — tighter SL, earlier trail, more partials
        {"tp1_rr": 1.3, "be_r": 0.4, "trail_atr": 0.3, "tp1_pct": 0.55, "tp2_pct": 0.25, "max_hold": 20, "risk_pct": 0.01, "vol_exit": True, "structure_exit": False},
        {"tp1_rr": 1.3, "be_r": 0.4, "trail_atr": 0.3, "tp1_pct": 0.50, "tp2_pct": 0.30, "max_hold": 24, "risk_pct": 0.01, "vol_exit": True, "structure_exit": False},
        {"tp1_rr": 1.5, "be_r": 0.4, "trail_atr": 0.3, "tp1_pct": 0.50, "tp2_pct": 0.30, "max_hold": 24, "risk_pct": 0.01, "vol_exit": True, "structure_exit": False},
        {"tp1_rr": 1.2, "be_r": 0.4, "trail_atr": 0.35, "tp1_pct": 0.50, "tp2_pct": 0.30, "max_hold": 24, "risk_pct": 0.01, "vol_exit": True, "structure_exit": False},
        # Moderate aggressive — good balance
        {"tp1_rr": 1.3, "be_r": 0.5, "trail_atr": 0.4, "tp1_pct": 0.50, "tp2_pct": 0.30, "max_hold": 24, "risk_pct": 0.01, "vol_exit": True, "structure_exit": False},
        {"tp1_rr": 1.3, "be_r": 0.5, "trail_atr": 0.4, "tp1_pct": 0.55, "tp2_pct": 0.25, "max_hold": 20, "risk_pct": 0.01, "vol_exit": True, "structure_exit": False},
        # V1c best as fallback
        {"tp1_rr": 1.3, "be_r": 0.5, "trail_atr": 0.4, "tp1_pct": 0.50, "tp2_pct": 0.30, "max_hold": 24, "risk_pct": 0.01, "vol_exit": True, "structure_exit": True},
        # Conservative — higher R:R
        {"tp1_rr": 1.8, "be_r": 0.5, "trail_atr": 0.4, "tp1_pct": 0.45, "tp2_pct": 0.30, "max_hold": 30, "risk_pct": 0.01, "vol_exit": False, "structure_exit": False},
        {"tp1_rr": 2.0, "be_r": 0.6, "trail_atr": 0.5, "tp1_pct": 0.40, "tp2_pct": 0.30, "max_hold": 36, "risk_pct": 0.01, "vol_exit": False, "structure_exit": False},
    ]
    
    results = []
    for i, params in enumerate(param_grid):
        total_pnl = 0; total_trades = 0; wins = 0; total_gp = 0; total_gl = 0
        
        for sym, data in all_symbol_data.items():
            result = simulate_trades_v2(data["klines"], data["signals"], capital, **params)
            for t in result["trades"]:
                total_pnl += t["pnl"]; total_trades += 1
                if t["pnl"] > 0: wins += 1; total_gp += t["pnl"]
                else: total_gl += abs(t["pnl"])
        
        pf = total_gp / total_gl if total_gl > 0 else 999
        exp = total_pnl / total_trades if total_trades > 0 else 0
        wr = wins / total_trades * 100 if total_trades > 0 else 0
        
        results.append({"params": params, "total_pnl": total_pnl, "trades": total_trades,
                         "pf": pf, "win_rate": wr, "expectancy": exp})
        print(f"  Config {i+1}: PF={pf:.2f} Exp=${exp:.2f} WR={wr:.1f}% Trades={total_trades} PnL=${total_pnl:,.2f}")
    
    best = max(results, key=lambda r: r["pf"] if r["pf"] < 999 else 0)
    return best, results


# ══════════════════════════════════════════════════════════════════════
# PART 12b: OOS-SPECIFIC EXIT OPTIMIZATION
# ══════════════════════════════════════════════════════════════════════

def optimize_exits_oos(all_symbol_data, capital=10000, oos_pct=0.30):
    """Find exit config that maximizes % of symbols profitable OOS.
    
    Instead of maximizing PF, this maximizes the number of symbols
    where final_capital > capital in the OOS period.
    """
    param_grid = [
        {"tp1_rr": 1.3, "be_r": 0.4, "trail_atr": 0.3, "tp1_pct": 0.55, "tp2_pct": 0.25, "max_hold": 20, "risk_pct": 0.01, "vol_exit": True, "structure_exit": False},
        {"tp1_rr": 1.3, "be_r": 0.5, "trail_atr": 0.4, "tp1_pct": 0.50, "tp2_pct": 0.30, "max_hold": 24, "risk_pct": 0.01, "vol_exit": True, "structure_exit": False},
        {"tp1_rr": 1.5, "be_r": 0.4, "trail_atr": 0.3, "tp1_pct": 0.50, "tp2_pct": 0.30, "max_hold": 24, "risk_pct": 0.01, "vol_exit": True, "structure_exit": False},
        {"tp1_rr": 1.3, "be_r": 0.5, "trail_atr": 0.4, "tp1_pct": 0.50, "tp2_pct": 0.30, "max_hold": 24, "risk_pct": 0.01, "vol_exit": False, "structure_exit": False},
        {"tp1_rr": 1.5, "be_r": 0.5, "trail_atr": 0.5, "tp1_pct": 0.50, "tp2_pct": 0.30, "max_hold": 24, "risk_pct": 0.01, "vol_exit": False, "structure_exit": False},
        {"tp1_rr": 1.3, "be_r": 0.5, "trail_atr": 0.4, "tp1_pct": 0.55, "tp2_pct": 0.25, "max_hold": 20, "risk_pct": 0.01, "vol_exit": False, "structure_exit": False},
        {"tp1_rr": 1.2, "be_r": 0.4, "trail_atr": 0.35, "tp1_pct": 0.50, "tp2_pct": 0.30, "max_hold": 24, "risk_pct": 0.01, "vol_exit": False, "structure_exit": False},
    ]
    
    results = []
    for i, params in enumerate(param_grid):
        profitable = 0; total = 0; total_pnl = 0
        total_gp = 0; total_gl = 0; total_trades = 0
        
        for sym, data in all_symbol_data.items():
            klines = data["klines"]; signals = data["signals"]; n = len(klines)
            if n < 500: continue
            
            split = int(n * (1 - oos_pct))
            oos_signals = {k - split: v for k, v in signals.items() if k >= split}
            test_klines = klines[split:]
            
            if not oos_signals: continue
            
            result = simulate_trades_v2(test_klines, oos_signals, capital, **params)
            total += 1
            profit = result["final_capital"] - capital
            total_pnl += profit
            if profit > 0: profitable += 1
            for t in result["trades"]:
                total_trades += 1
                if t["pnl"] > 0: total_gp += t["pnl"]
                else: total_gl += abs(t["pnl"])
        
        pf = total_gp / total_gl if total_gl > 0 else 999
        pct = profitable / total * 100 if total > 0 else 0
        results.append({"params": params, "profitable_pct": pct, "profitable": profitable,
                         "total": total, "pf": pf, "total_pnl": total_pnl, "trades": total_trades})
        print(f"  OOS Config {i+1}: PF={pf:.2f} OOS_Profitable={profitable}/{total} ({pct:.1f}%) PnL=${total_pnl:,.2f}")
    
    best = max(results, key=lambda r: r["profitable_pct"])
    return best, results


# ══════════════════════════════════════════════════════════════════════
# PART 13: ROLLING WINDOW VALIDATION (NEW)
# ══════════════════════════════════════════════════════════════════════

def rolling_window_validation(all_symbol_data, window_pct=0.30, step_pct=0.10, exit_params=None, capital=10000):
    """Rolling window: slide a window across data, test on each window's OOS."""
    results = []
    
    for sym, data in all_symbol_data.items():
        klines = data["klines"]; signals = data["signals"]; n = len(klines)
        if n < 1500: continue
        
        window_size = int(n * window_pct)
        step_size = int(n * step_pct)
        window_profits = []
        
        start = 0
        while start + window_size + int(window_size * 0.3) <= n:
            train_end = start + window_size
            test_end = min(train_end + int(window_size * 0.3), n)
            
            test_signals = {k: v for k, v in signals.items() if train_end <= k < test_end}
            test_signals_ri = {k - train_end: v for k, v in test_signals.items()}
            test_klines = klines[train_end:test_end]
            
            if len(test_klines) < 200 or not test_signals_ri:
                start += step_size; continue
            
            if exit_params:
                result = simulate_trades_v2(test_klines, test_signals_ri, capital, **exit_params)
            else:
                result = simulate_trades_before(test_klines, test_signals_ri, capital)
            
            profit = result["final_capital"] - capital
            window_profits.append(profit)
            start += step_size
        
        if window_profits:
            profitable_windows = sum(1 for p in window_profits if p > 0)
            results.append({
                "symbol": sym,
                "n_windows": len(window_profits),
                "profitable_windows": profitable_windows,
                "profitable_pct": profitable_windows / len(window_profits) * 100,
                "avg_profit": statistics.mean(window_profits),
                "total_profit": sum(window_profits),
            })
    
    if not results: return None
    
    profitable_syms = sum(1 for r in results if r["profitable_pct"] > 50)
    return {
        "n_symbols": len(results),
        "profitable_syms": profitable_syms,
        "profitable_pct": profitable_syms / len(results) * 100,
        "avg_window_profitable_pct": statistics.mean(r["profitable_pct"] for r in results),
        "details": results,
    }


# ══════════════════════════════════════════════════════════════════════
# PART 14: STRESS TEST (NEW)
# ══════════════════════════════════════════════════════════════════════

def stress_test(all_symbol_data, capital=10000, exit_params=None):
    """Test performance during worst market periods."""
    # Collect all trades and find worst drawdown periods
    all_trades = []
    for sym, data in all_symbol_data.items():
        if exit_params:
            result = simulate_trades_v2(data["klines"], data["signals"], capital, **exit_params)
        else:
            result = simulate_trades_before(data["klines"], data["signals"], capital)
        for t in result["trades"]:
            t["symbol"] = sym
            all_trades.append(t)
    
    if not all_trades: return None
    
    all_trades.sort(key=lambda t: t["entry_bar"])
    
    # Build cumulative equity from all trades
    bar_pnl = {}
    for t in all_trades:
        exit_bar = t["exit_bar"]
        bar_pnl[exit_bar] = bar_pnl.get(exit_bar, 0) + t["pnl"]
    
    max_bar = max(bar_pnl.keys()) if bar_pnl else 0
    ec = [capital]
    running = capital
    for bar in range(max_bar + 1):
        running += bar_pnl.get(bar, 0)
        ec.append(running)
    ec = np.array(ec)
    
    # Find worst 100-bar, 200-bar, 500-bar rolling windows
    worst_periods = {}
    for window in [100, 200, 500]:
        if len(ec) < window: continue
        rolling_dd = []
        for i in range(window, len(ec)):
            peak = np.max(ec[max(0,i-window):i+1])
            dd = (peak - ec[i]) / peak * 100 if peak > 0 else 0
            rolling_dd.append((i, dd, ec[i]))
        if rolling_dd:
            worst = max(rolling_dd, key=lambda x: x[1])
            worst_periods[f"{window}_bar"] = {
                "max_dd": worst[1],
                "equity_at_worst": worst[2],
                "bar": worst[0],
            }
    
    # Max consecutive losing trades
    max_consec_loss = 0; current_streak = 0
    for t in all_trades:
        if t["pnl"] <= 0:
            current_streak += 1
            max_consec_loss = max(max_consec_loss, current_streak)
        else:
            current_streak = 0
    
    # Worst single-day (worst 24-bar rolling PnL)
    if len(ec) > 24:
        daily_pnls = np.diff(ec)
        worst_24h = float(np.min(np.convolve(daily_pnls, np.ones(24), mode='valid')))
    else:
        worst_24h = float(np.min(np.diff(ec))) if len(ec) > 1 else 0
    
    return {
        "worst_periods": worst_periods,
        "max_consec_losses": max_consec_loss,
        "worst_24h_pnl": worst_24h,
        "total_trades": len(all_trades),
        "profit_factor": sum(t["pnl"] for t in all_trades if t["pnl"] > 0) / max(abs(sum(t["pnl"] for t in all_trades if t["pnl"] <= 0)), 1),
    }


# ══════════════════════════════════════════════════════════════════════
# PART 15: SLIPPAGE TEST (NEW)
# ══════════════════════════════════════════════════════════════════════

def slippage_test(all_symbol_data, capital=10000, exit_params=None):
    """Test impact of slippage on profitability."""
    slippage_levels = [0, 0.0001, 0.0002, 0.0005, 0.001, 0.002]  # 0-20 bps
    
    results = []
    for slip in slippage_levels:
        total_pnl = 0; total_trades = 0; wins = 0; gp = 0; gl = 0
        
        for sym, data in all_symbol_data.items():
            if exit_params:
                result = simulate_trades_v2(data["klines"], data["signals"], capital, **exit_params)
            else:
                result = simulate_trades_before(data["klines"], data["signals"], capital)
            
            for t in result["trades"]:
                # Apply slippage to entry and exit
                entry_slip = t["entry"] * slip
                exit_slip = t["entry"] * slip  # Approximate
                slip_cost = (entry_slip + exit_slip) * t["size"]
                adjusted_pnl = t["pnl"] - slip_cost
                
                total_pnl += adjusted_pnl; total_trades += 1
                if adjusted_pnl > 0: wins += 1; gp += adjusted_pnl
                else: gl += abs(adjusted_pnl)
        
        pf = gp / gl if gl > 0 else 999
        bps = slip * 10000
        results.append({"slippage_bps": bps, "total_pnl": total_pnl, "pf": pf,
                         "trades": total_trades, "win_rate": wins/total_trades*100 if total_trades else 0})
    
    return {"slippage_levels": results}


# ══════════════════════════════════════════════════════════════════════
# PART 16: FEE SENSITIVITY (NEW)
# ══════════════════════════════════════════════════════════════════════

def fee_sensitivity(all_symbol_data, capital=10000, exit_params=None):
    """Test impact of different fee levels."""
    fee_levels = [0.0001, 0.0002, 0.0004, 0.0006, 0.0008, 0.001]  # 1-10 bps
    
    results = []
    for fee in fee_levels:
        total_pnl = 0; total_trades = 0; wins = 0; gp = 0; gl = 0
        
        for sym, data in all_symbol_data.items():
            if exit_params:
                result = simulate_trades_v2(data["klines"], data["signals"], capital, **exit_params)
            else:
                result = simulate_trades_before(data["klines"], data["signals"], capital)
            
            for t in result["trades"]:
                # Recompute costs with this fee level
                new_cost = t["entry"] * t["size"] * fee
                old_cost = t.get("costs", 0)
                adjusted_pnl = t["pnl"] - (new_cost - old_cost)
                
                total_pnl += adjusted_pnl; total_trades += 1
                if adjusted_pnl > 0: wins += 1; gp += adjusted_pnl
                else: gl += abs(adjusted_pnl)
        
        pf = gp / gl if gl > 0 else 999
        bps = fee * 10000
        results.append({"fee_bps": bps, "total_pnl": total_pnl, "pf": pf,
                         "trades": total_trades, "win_rate": wins/total_trades*100 if total_trades else 0})
    
    return {"fee_levels": results}


# ══════════════════════════════════════════════════════════════════════
# PART 17: MAIN EXECUTION
# ══════════════════════════════════════════════════════════════════════

def main():
    t_start = time.time()
    
    print("=" * 100)
    print("  EMA_V5 PORTFOLIO OPTIMIZER V2 — Lead Quantitative Portfolio Manager")
    print("  V2: Kelly sizing + DD-adaptive + Sector caps + Vol/Structure exits + Heat mgmt")
    print("=" * 100)
    
    # ── Load universe ──
    db_path = 'data/database/historical_klines.db'
    conn = sqlite3.connect(db_path)
    syms = conn.execute(
        "SELECT symbol, COUNT(*) FROM klines WHERE interval='1h' GROUP BY symbol "
        "HAVING COUNT(*) >= 500 ORDER BY symbol"
    ).fetchall()
    symbols = [r[0] for r in syms]
    conn.close()
    
    print(f"\n  Universe: {len(symbols)} symbols | Capital: $10,000")
    print(f"  Constraints: Max Concurrent={MAX_CONCURRENT} | Max Heat={MAX_HEAT*100:.0f}% | Max/Sector={MAX_PER_SECTOR}")
    
    # ══════════════════════════════════════════════════════════════════
    # PHASE 1: Signal Generation & Classification
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 100}")
    print(f"  PHASE 1: Signal Generation & Per-Symbol Analysis")
    print(f"{'─' * 100}")
    
    all_symbol_data = {}
    classifications = {"ELITE": [], "GOOD": [], "NEUTRAL": [], "POOR": [], "DISABLED": []}
    
    for idx, sym in enumerate(symbols):
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT open_time, open, high, low, close, volume FROM klines "
            "WHERE symbol=? AND interval='1h' ORDER BY open_time ASC", (sym,)
        ).fetchall()
        conn.close()
        if len(rows) < 500: continue
        klines = np.array(rows, dtype=float)
        
        signals, indicators = generate_signals(klines)
        
        result = simulate_trades_before(klines, signals)
        if result["trades"]:
            pnls = [t["pnl"] for t in result["trades"]]
            winners = [p for p in pnls if p > 0]; losers = [p for p in pnls if p <= 0]
            gp = sum(winners); gl = abs(sum(losers))
            ec = np.array(result["equity_curve"])
            pk = np.maximum.accumulate(ec)
            dd = (pk - ec) / np.where(pk > 0, pk, 1) * 100
            mdd = float(np.max(dd))
            metrics = {
                "trades": len(pnls), "pf": gp/gl if gl>0 else 999,
                "expectancy": statistics.mean(pnls), "max_dd": mdd,
                "recovery_factor": (result["final_capital"]-10000)/mdd if mdd>0 and result["final_capital"]>10000 else 0,
                "net_profit": result["final_capital"]-10000,
                "win_rate": len(winners)/len(pnls)*100 if pnls else 0,
                "avg_win": statistics.mean(winners) if winners else 0,
                "avg_loss": statistics.mean(losers) if losers else 0,
            }
        else:
            metrics = {"trades": 0, "pf": 0, "expectancy": 0, "max_dd": 0, "recovery_factor": 0,
                       "net_profit": 0, "win_rate": 0, "avg_win": 0, "avg_loss": 0}
        
        cls_name, cls_weight = classify_symbol(metrics)
        classifications[cls_name].append(sym)
        
        all_symbol_data[sym] = {
            "klines": klines, "signals": signals, "indicators": indicators,
            "classification": (cls_name, cls_weight), "metrics": metrics,
        }
        
        pf_s = f"{metrics['pf']:.2f}" if metrics['pf'] < 999 else "INF"
        if (idx + 1) % 20 == 0 or idx == 0:
            print(f"  [{idx+1:>3}/{len(symbols)}] {sym:<20} cls={cls_name:<10} trades={metrics['trades']:>3} "
                  f"net=${metrics['net_profit']:>+9.2f} PF={pf_s:>6} MDD={metrics['max_dd']:>5.1f}%")
    
    # Classification summary
    print(f"\n{'─' * 100}")
    print(f"  SYMBOL CLASSIFICATION")
    print(f"{'─' * 100}")
    for cls in ["ELITE", "GOOD", "NEUTRAL", "POOR", "DISABLED"]:
        s_list = classifications[cls]
        if s_list:
            s_str = ", ".join(s_list[:8])
            if len(s_list) > 8: s_str += f" ... (+{len(s_list)-8})"
            print(f"  {cls:<10} ({len(s_list):>3}): {s_str}")
    
    enabled_count = sum(len(classifications[c]) for c in ["ELITE", "GOOD", "NEUTRAL"])
    disabled_count = sum(len(classifications[c]) for c in ["POOR", "DISABLED"])
    print(f"\n  Enabled: {enabled_count} | Disabled: {disabled_count} (POOR+DISABLED excluded from AFTER)")
    
    # ══════════════════════════════════════════════════════════════════
    # PHASE 2: Exit Optimization
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 100}")
    print(f"  PHASE 2: Exit Optimization (Grid Search)")
    print(f"{'─' * 100}")
    
    best_exit, exit_results = optimize_exits(all_symbol_data)
    print(f"\n  BEST EXIT CONFIG (by PF): {json.dumps(best_exit['params'], indent=2)}")
    print(f"  Best PF: {best_exit['pf']:.2f} | Expectancy: ${best_exit['expectancy']:.2f} | PnL: ${best_exit['total_pnl']:,.2f}")
    
    # ── Phase 2b: OOS-Specific Exit Optimization ──
    print(f"\n{'─' * 100}")
    print(f"  PHASE 2b: OOS Exit Optimization (Maximize OOS Profitable %)")
    print(f"{'─' * 100}")
    
    best_oos_exit, oos_exit_results = optimize_exits_oos(all_symbol_data)
    print(f"\n  BEST EXIT CONFIG (by OOS): {json.dumps(best_oos_exit['params'], indent=2)}")
    print(f"  Best OOS: {best_oos_exit['profitable_pct']:.1f}% profitable ({best_oos_exit['profitable']}/{best_oos_exit['total']}) | PF={best_oos_exit['pf']:.2f}")
    
    # Use the OOS-optimized config if it has significantly better OOS %
    # Otherwise use the PF-optimized config
    print(f"\n  Using OOS-optimized exit params for portfolio simulation...")
    best_exit_for_deploy = best_oos_exit["params"]
    
    # ══════════════════════════════════════════════════════════════════
    # PHASE 3: BEFORE vs AFTER Portfolio Comparison
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 100}")
    print(f"  PHASE 3: BEFORE vs AFTER Portfolio Comparison")
    print(f"{'─' * 100}")
    
    print(f"\n  Running BEFORE (equal-weight, all 138 symbols, fixed exits, no constraints)...")
    before = simulate_portfolio_v2(all_symbol_data, mode="BEFORE")
    
    print(f"  Running AFTER V2 (classified symbols, Kelly sizing, DD-adaptive, sector caps, enhanced exits)...")
    after = simulate_portfolio_v2(all_symbol_data, mode="AFTER", exit_params=best_exit_for_deploy)
    
    # Comparison table
    def fmt_delta(bval, aval, higher=True):
        if isinstance(bval, str): return f"{bval} → {aval}"
        diff = aval - bval
        pct = diff / abs(bval) * 100 if abs(bval) > 0.001 else 0
        better = (diff > 0) if higher else (diff < 0)
        icon = "✅" if better else "❌" if abs(diff) > 0.001 else "➡️"
        return f"{bval:.2f} → {aval:.2f} ({pct:+.1f}%) {icon}"
    
    print(f"\n{'═' * 100}")
    print(f"  BEFORE vs AFTER V2 COMPARISON")
    print(f"{'═' * 100}")
    print(f"\n  {'Metric':<30} {'BEFORE':>14} {'AFTER V2':>14} {'Change':>30}")
    print(f"  {'─' * 95}")
    print(f"  {'Net Profit':<30} ${before['total_profit']:>12,.2f} ${after['total_profit']:>12,.2f} {fmt_delta(before['total_profit'], after['total_profit'])}")
    print(f"  {'Portfolio PF':<30} {before['portfolio_pf']:>13.2f} {after['portfolio_pf']:>13.2f} {fmt_delta(before['portfolio_pf'], after['portfolio_pf'])}")
    print(f"  {'Expectancy':<30} ${before['expectancy']:>12.2f} ${after['expectancy']:>12.2f} {fmt_delta(before['expectancy'], after['expectancy'])}")
    print(f"  {'Sharpe':<30} {before['sharpe']:>13.2f} {after['sharpe']:>13.2f} {fmt_delta(before['sharpe'], after['sharpe'])}")
    print(f"  {'Sortino':<30} {before['sortino']:>13.2f} {after['sortino']:>13.2f} {fmt_delta(before['sortino'], after['sortino'])}")
    print(f"  {'Max Drawdown':<30} {before['max_dd']:>12.1f}% {after['max_dd']:>12.1f}% {fmt_delta(before['max_dd'], after['max_dd'], False)}")
    print(f"  {'CAGR':<30} {before['cagr']*100:>12.2f}% {after['cagr']*100:>12.2f}% {fmt_delta(before['cagr'], after['cagr'])}")
    print(f"  {'Calmar':<30} {before['calmar']:>13.2f} {after['calmar']:>13.2f} {fmt_delta(before['calmar'], after['calmar'])}")
    print(f"  {'Recovery Factor':<30} {before['recovery_factor']:>13.2f} {after['recovery_factor']:>13.2f} {fmt_delta(before['recovery_factor'], after['recovery_factor'])}")
    print(f"  {'Win Rate':<30} {before['win_rate']:>12.1f}% {after['win_rate']:>12.1f}%")
    print(f"  {'Total Trades':<30} {before['total_trades']:>13} {after['total_trades']:>13}")
    print(f"  {'Total Costs':<30} ${before['total_costs']:>12.2f} ${after['total_costs']:>12.2f}")
    print(f"  {'Profitable Symbols':<30} {before['profitable_syms']:>10}/{before['total_syms']} {after['profitable_syms']:>10}/{after['total_syms']}")
    
    # ══════════════════════════════════════════════════════════════════
    # PHASE 4: Monte Carlo
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 100}")
    print(f"  PHASE 4: Monte Carlo Simulation (1000 sims)")
    print(f"{'─' * 100}")
    
    before_pnls = [t["pnl"] for t in before["trades"]]
    after_pnls = [t["pnl"] for t in after["trades"]]
    
    mc_before = monte_carlo(before_pnls) if len(before_pnls) >= 10 else None
    mc_after = monte_carlo(after_pnls) if len(after_pnls) >= 10 else None
    
    if mc_before and mc_after:
        print(f"\n  {'MC Metric':<30} {'BEFORE':>14} {'AFTER V2':>14}")
        print(f"  {'─' * 65}")
        print(f"  {'Median Final Equity':<30} ${mc_before['final_equity']['median']:>12,.2f} ${mc_after['final_equity']['median']:>12,.2f}")
        print(f"  {'Mean Final Equity':<30} ${mc_before['final_equity']['mean']:>12,.2f} ${mc_after['final_equity']['mean']:>12,.2f}")
        print(f"  {'5th Percentile':<30} ${mc_before['final_equity']['p5']:>12,.2f} ${mc_after['final_equity']['p5']:>12,.2f}")
        print(f"  {'95th Percentile':<30} ${mc_before['final_equity']['p95']:>12,.2f} ${mc_after['final_equity']['p95']:>12,.2f}")
        print(f"  {'Prob Profit':<30} {mc_before['final_equity']['prob_profit']*100:>12.1f}% {mc_after['final_equity']['prob_profit']*100:>12.1f}%")
        print(f"  {'Median Max DD':<30} {mc_before['max_drawdown']['median']:>12.1f}% {mc_after['max_drawdown']['median']:>12.1f}%")
        print(f"  {'Median Sharpe':<30} {mc_before['sharpe']['median']:>13.2f} {mc_after['sharpe']['median']:>13.2f}")
    
    # ══════════════════════════════════════════════════════════════════
    # PHASE 5: Walk-Forward
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 100}")
    print(f"  PHASE 5: Walk-Forward Validation (70/30 split)")
    print(f"{'─' * 100}")
    
    wf = walk_forward_validation(all_symbol_data, train_pct=0.7, exit_params=best_exit_for_deploy)
    if wf:
        print(f"\n  Symbols: {wf['n_symbols']} | Traded: {wf.get('n_traded', wf['n_symbols'])} | Profitable OOS: {wf['profitable_oos']}/{wf.get('n_traded', wf['n_symbols'])} ({wf['profitable_pct']:.1f}%)")
        print(f"  Avg OOS PF: {wf['avg_test_pf']:.2f}")
        profitable_details = sorted([d for d in wf["details"] if d["oos_profitable"]],
                                    key=lambda d: d["test_profit"], reverse=True)
        if profitable_details:
            print(f"\n  Top 10 OOS Profitable:")
            for d in profitable_details[:10]:
                print(f"    {d['symbol']:<20} cls={d['classification']:<10} train_pf={d['train_pf']:.2f} test_pf={d['test_pf']:.2f} profit=${d['test_profit']:>+.2f}")
    
    # ══════════════════════════════════════════════════════════════════
    # PHASE 6: Out-of-Sample
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 100}")
    print(f"  PHASE 6: Out-of-Sample Validation (70/30 split)")
    print(f"{'─' * 100}")
    
    oos = out_of_sample_split(all_symbol_data, oos_pct=0.30, exit_params=best_exit_for_deploy)
    if oos:
        print(f"\n  Symbols: {oos['n_symbols']} | Traded: {oos.get('n_traded', oos['n_symbols'])} | Profitable OOS: {oos['profitable_oos']}/{oos.get('n_traded', oos['n_symbols'])} ({oos['profitable_pct']:.1f}%)")
        oos_list = sorted([(s, r) for s, r in oos["oos_results"].items() if r["trades"] > 0],
                          key=lambda x: x[1]["profit"], reverse=True)
        print(f"\n  Top 10 OOS:")
        for sym, r in oos_list[:10]:
            print(f"    {sym:<20} cls={r['classification']:<10} trades={r['trades']:>3} PF={r['pf']:.2f} profit=${r['profit']:>+.2f}")
        print(f"\n  Bottom 5 OOS:")
        for sym, r in oos_list[-5:]:
            print(f"    {sym:<20} cls={r['classification']:<10} trades={r['trades']:>3} PF={r['pf']:.2f} profit=${r['profit']:>+.2f}")
    
    # ══════════════════════════════════════════════════════════════════
    # PHASE 7: Rolling Window Validation
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 100}")
    print(f"  PHASE 7: Rolling Window Validation")
    print(f"{'─' * 100}")
    
    rolling = rolling_window_validation(all_symbol_data, exit_params=best_exit_for_deploy)
    if rolling:
        print(f"\n  Symbols: {rolling['n_symbols']} | Profitable: {rolling['profitable_syms']}/{rolling['n_symbols']} ({rolling['profitable_pct']:.1f}%)")
        print(f"  Avg Window Profitable %: {rolling['avg_window_profitable_pct']:.1f}%")
    
    # ══════════════════════════════════════════════════════════════════
    # PHASE 8: Stress Test
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 100}")
    print(f"  PHASE 8: Stress Test")
    print(f"{'─' * 100}")
    
    stress = stress_test(all_symbol_data, exit_params=best_exit_for_deploy)
    if stress:
        print(f"\n  Max Consecutive Losses: {stress['max_consec_losses']}")
        print(f"  Worst 24h PnL: ${stress['worst_24h_pnl']:,.2f}")
        for period, data in stress["worst_periods"].items():
            print(f"  Worst {period}: DD={data['max_dd']:.1f}% | Equity=${data['equity_at_worst']:,.2f}")
        print(f"  Stress PF: {stress['profit_factor']:.2f}")
    
    # ══════════════════════════════════════════════════════════════════
    # PHASE 9: Slippage Test
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 100}")
    print(f"  PHASE 9: Slippage Sensitivity")
    print(f"{'─' * 100}")
    
    slip_test = slippage_test(all_symbol_data, exit_params=best_exit_for_deploy)
    if slip_test:
        print(f"\n  {'Slippage (bps)':<18} {'PnL':>14} {'PF':>8} {'Win Rate':>10}")
        for sl in slip_test["slippage_levels"]:
            print(f"  {sl['slippage_bps']:>8.0f} bps     ${sl['total_pnl']:>12,.2f} {sl['pf']:>8.2f} {sl['win_rate']:>9.1f}%")
    
    # ══════════════════════════════════════════════════════════════════
    # PHASE 10: Fee Sensitivity
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'─' * 100}")
    print(f"  PHASE 10: Fee Sensitivity")
    print(f"{'─' * 100}")
    
    fee_test = fee_sensitivity(all_symbol_data, exit_params=best_exit_for_deploy)
    if fee_test:
        print(f"\n  {'Fee (bps)':<18} {'PnL':>14} {'PF':>8} {'Win Rate':>10}")
        for fl in fee_test["fee_levels"]:
            print(f"  {fl['fee_bps']:>8.0f} bps     ${fl['total_pnl']:>12,.2f} {fl['pf']:>8.2f} {fl['win_rate']:>9.1f}%")
    
    # ══════════════════════════════════════════════════════════════════
    # DEPLOYMENT CRITERIA
    # ══════════════════════════════════════════════════════════════════
    print(f"\n{'═' * 100}")
    print(f"  DEPLOYMENT CRITERIA")
    print(f"{'═' * 100}")
    
    criteria = [
        ("Portfolio Net Profit increases", after['total_profit'] > before['total_profit'],
         f"${before['total_profit']:,.2f} → ${after['total_profit']:,.2f}"),
        ("Portfolio PF ≥ 1.50", after['portfolio_pf'] >= 1.50,
         f"{after['portfolio_pf']:.2f}"),
        ("Calmar ≥ 4.0", after['calmar'] >= 4.0,
         f"{after['calmar']:.2f}"),
        ("Recovery Factor improves", after['recovery_factor'] > before['recovery_factor'],
         f"{before['recovery_factor']:.2f} → {after['recovery_factor']:.2f}"),
        ("Sharpe ≥ 2.5", after['sharpe'] >= 2.5,
         f"{after['sharpe']:.2f}"),
        ("Drawdown ≤ 10%", after['max_dd'] <= 10,
         f"{after['max_dd']:.1f}%"),
        ("Walk-Forward ≥ 55%", wf is not None and wf['profitable_pct'] >= 55,
         f"{wf['profitable_pct']:.1f}%" if wf else "N/A"),
        ("Out-of-Sample ≥ 55%", oos is not None and oos['profitable_pct'] >= 55,
         f"{oos['profitable_pct']:.1f}%" if oos else "N/A"),
    ]
    
    passed = sum(1 for _, ok, _ in criteria if ok)
    for name, ok, val in criteria:
        print(f"    {'✅' if ok else '❌'} {name:<45} = {val}")
    
    print(f"\n    Score: {passed}/{len(criteria)}")
    overall = passed >= 8
    print(f"\n  {'🟢 APPROVED FOR DEPLOYMENT' if overall else '🔴 REJECT — DO NOT DEPLOY'}")
    print(f"\n  Execution time: {time.time() - t_start:.1f}s")
    print(f"{'═' * 100}")
    
    # ── Save Results ──
    os.makedirs('backtest_reports', exist_ok=True)
    
    save_before = {k: v for k, v in before.items() if k not in ("trades", "portfolio_ec")}
    save_after = {k: v for k, v in after.items() if k not in ("trades", "portfolio_ec")}
    save_after["best_exit_params"] = best_exit_for_deploy
    save_after["best_exit_pf"] = best_exit["pf"]
    
    for sr in [save_before, save_after]:
        for sym in sr.get("symbol_results", {}):
            sr["symbol_results"][sym].pop("equity_curve", None)
    
    report = {
        "version": "v2",
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "constraints": {
            "max_concurrent": MAX_CONCURRENT, "max_heat": MAX_HEAT,
            "max_per_sector": MAX_PER_SECTOR, "max_daily_loss": MAX_DAILY_LOSS_PCT,
            "dd_adaptive": True, "kelly_sizing": True,
            "vol_exit": True, "structure_exit": True,
        },
        "before": save_before, "after": save_after,
        "classifications": {k: v for k, v in classifications.items()},
        "exit_optimization": {"best": best_exit, "all_results": exit_results},
        "monte_carlo": {"before": mc_before, "after": mc_after},
        "walk_forward": wf, "out_of_sample": oos,
        "rolling_window": rolling, "stress_test": stress,
        "slippage_test": slip_test, "fee_sensitivity": fee_test,
        "deployment": {"score": passed, "total": len(criteria),
                       "decision": "GO" if overall else "REJECT"},
    }
    
    with open('backtest_reports/portfolio_optimization_v2.json', 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    print(f"\n  Results saved to backtest_reports/portfolio_optimization_v2.json")


if __name__ == "__main__":
    main()
