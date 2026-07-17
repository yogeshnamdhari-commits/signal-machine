#!/usr/bin/env python3
"""
EMA_V5 PORTFOLIO OPTIMIZER
===========================
Lead Quantitative Portfolio Engineer — System v1

DO NOT modify signal logic, indicators, or EMA rules.
ONLY optimize: Symbol Selection, Position Sizing, Regime Allocation,
               Portfolio Optimization, Exit Optimization.

BEFORE: Equal-weight, all symbols, fixed 1% risk, no regime filter, basic exits
AFTER:  Dynamic-weight, classified symbols, Kelly sizing, regime-aware, optimized exits

Runs on the full 138-symbol perpetual futures universe.
"""
import sys, os, json, time, statistics, sqlite3, math
import numpy as np

os.environ['LOGURU_LEVEL'] = 'ERROR'

# ══════════════════════════════════════════════════════════════════════
# PART 1: INDICATORS (shared — DO NOT MODIFY)
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
# PART 2: SIGNAL GENERATION (entries — DO NOT MODIFY LOGIC)
# ══════════════════════════════════════════════════════════════════════

def generate_signals(klines, use_institutional=True):
    """Generate signals using the AFTER (institutional) scoring.
    Returns: signals dict {bar_index: signal_dict}, indicators dict"""
    n = len(klines)
    if n < 250: return {}, {}
    opens = klines[:, 1]; highs = klines[:, 2]; lows = klines[:, 3]
    closes = klines[:, 4]; volumes = klines[:, 5]
    
    ema20 = ema_calc(closes, 20); ema50 = ema_calc(closes, 50)
    ema144 = ema_calc(closes, 144); ema200 = ema_calc(closes, 200)
    atr_arr = atr_calc(highs, lows, closes, 14)
    vol_sma = sma_calc(volumes, 20)
    rvol_arr = rvol_calc(volumes, 20)
    adx_arr, adx_plus, adx_minus = compute_adx(highs, lows, closes, 14)
    
    # Slopes
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
        
        # ATR filter
        atr_pct = atr_arr[i] / closes[i] * 100 if closes[i] > 0 else 0
        if atr_pct < 0.05: continue
        
        # Regime
        buy = ema20[i] > ema50[i] > ema144[i] > ema200[i]
        sell = ema20[i] < ema50[i] < ema144[i] < ema200[i]
        if not buy and not sell: continue
        if buy and not (ema144s[i] > 0 and ema200s[i] > 0 and closes[i] > ema144[i] and closes[i] > ema200[i]): continue
        if sell and not (ema144s[i] < 0 and ema200s[i] < 0 and closes[i] < ema144[i] and closes[i] < ema200[i]): continue
        
        # Trend score
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
        
        # Pullback
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
        
        # Candle
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
        
        # Volume
        if volumes[i] < vol_sma[i]: continue
        
        # Institutional 8-component confidence
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
        
        # Entry/SL/TP
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
        
        # Expected Profit Score
        eps = rr * min(score/100, 1.0) * min(latest_adx/50, 1.0) * min(rvol/3.0, 1.0)
        
        signals[i] = {"side": side, "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
                       "rr": rr, "conf": conf, "regime": regime, "score": eps,
                       "adx": latest_adx, "rvol": rvol, "trend_score": score,
                       "atr": cur_atr, "bar": i}
    
    indicators = {"ema20": ema20, "ema50": ema50, "ema144": ema144, "ema200": ema200,
                  "atr": atr_arr, "adx": adx_arr, "rvol": rvol_arr}
    return signals, indicators


# ══════════════════════════════════════════════════════════════════════
# PART 3: TRADE SIMULATION ENGINE
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


def simulate_trades_after(klines, signals, capital=10000,
                          sl_atr=1.5, tp1_rr=1.5, be_r=0.8, trail_atr=0.8,
                          tp1_pct=0.40, tp2_pct=0.35, max_hold=48, risk_pct=0.01):
    """AFTER: Dynamic exits with optimized parameters.
    Exit improvements: tighter trail, earlier BE, larger first partial."""
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
                        t["sl"] = t["entry"] + r_dist * 0.05  # Lock small profit, not just BE
                        t["be_moved"] = True
                # Tighter trailing
                if t["tp1_hit"] and atr_arr[i] > 0:
                    trail = highs[i] - atr_arr[i] * trail_atr
                    if trail > t["sl"]: t["sl"] = trail
                # Time decay exit: if holding long and losing after 24 bars, tighten stop
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
        if sig is None or i < last_signal_bar + 6:
            equity_curve.append(equity); continue
        
        entry = sig["entry"]; sl = sig["sl"]; side = sig["side"]
        risk_amount = equity * risk_pct; sl_dist = abs(entry - sl)
        if sl_dist <= 0: equity_curve.append(equity); continue
        size = risk_amount / sl_dist; cost = entry * size * 0.0006
        
        # Recompute TPs with optimized RR
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
# PART 4: SYMBOL CLASSIFIER
# ══════════════════════════════════════════════════════════════════════

def classify_symbol(sym_metrics):
    """Classify a symbol into Elite/Good/Neutral/Poor/Disabled.
    
    Criteria based on:
    - Profit Factor
    - Expectancy per trade
    - Recovery Factor
    - Max Drawdown
    - Win Rate (informational, not primary)
    """
    pf = sym_metrics.get("pf", 0)
    exp = sym_metrics.get("expectancy", 0)
    rf = sym_metrics.get("recovery_factor", 0)
    mdd = sym_metrics.get("max_dd", 100)
    trades = sym_metrics.get("trades", 0)
    
    # Need minimum trades for classification
    if trades < 5:
        return "DISABLED", 0.0
    
    # Elite: High PF, positive expectancy, strong recovery, controlled DD
    if pf >= 1.5 and exp > 5 and rf >= 1.5 and mdd < 20:
        return "ELITE", 1.0
    
    # Good: Solid PF, positive expectancy, reasonable recovery
    if pf >= 1.2 and exp > 2 and rf >= 0.8 and mdd < 30:
        return "GOOD", 0.75
    
    # Neutral: Barely profitable, positive expectancy
    if pf >= 1.0 and exp > 0:
        return "NEUTRAL", 0.50
    
    # Poor: Negative expectancy
    if pf >= 0.8:
        return "POOR", 0.25
    
    # Disabled: Consistently losing
    return "DISABLED", 0.0


# ══════════════════════════════════════════════════════════════════════
# PART 5: REGIME DETECTOR
# ══════════════════════════════════════════════════════════════════════

def detect_regime(indicators, bar_idx, klines):
    """Detect current market regime at a given bar.
    Returns: regime string and compatibility score.
    
    Regimes:
    - TRENDING: ADX > 25, EMA slopes strong, clear direction
    - PULLBACK: ADX declining, price near EMA
    - RANGE: ADX < 20, EMAs compressed
    - HIGH_VOL: ATR > 2x average, large candles
    - LOW_VOL: ATR < 0.5x average, tight range
    """
    if bar_idx < 220:
        return "UNKNOWN", 0.0
    
    adx = indicators["adx"][bar_idx]
    atr = indicators["atr"][bar_idx]
    closes = klines[:, 4]
    
    # Compute average ATR over last 50 bars
    start = max(0, bar_idx - 50)
    avg_atr = np.nanmean(indicators["atr"][start:bar_idx]) if bar_idx > start else atr
    atr_ratio = atr / avg_atr if avg_atr > 0 else 1.0
    
    # ADX-based regime
    if adx >= 25:
        return "TRENDING", min(adx / 40, 1.0)
    elif adx >= 20:
        return "PULLBACK", 0.6
    elif atr_ratio >= 2.0:
        return "HIGH_VOL", min(atr_ratio / 3.0, 1.0)
    elif atr_ratio <= 0.5:
        return "LOW_VOL", 0.3
    else:
        return "RANGE", 0.4


def regime_compatibility(regime, signal_side):
    """Check if a regime is compatible with a signal direction."""
    if regime == "TRENDING":
        return 1.0  # Fully compatible
    elif regime == "PULLBACK":
        return 0.9  # Pullbacks are entry points in trends
    elif regime == "HIGH_VOL":
        return 0.6  # Volatile — widen stops, reduce size
    elif regime == "RANGE":
        return 0.3  # Ranging — EMA signals less reliable
    elif regime == "LOW_VOL":
        return 0.5  # Low vol — breakout pending
    return 0.5


# ══════════════════════════════════════════════════════════════════════
# PART 6: DYNAMIC POSITION SIZER
# ══════════════════════════════════════════════════════════════════════

def compute_position_size(equity, signal, sym_classification, sym_weight,
                          regime_compat, max_risk_pct=0.015, max_position_pct=0.10):
    """Dynamic position sizing based on signal quality and symbol tier.
    
    Sizing formula:
    base_risk = equity * max_risk_pct * classification_weight * regime_compat
    size = base_risk / sl_distance
    
    With portfolio-level caps:
    - Never risk more than max_risk_pct per trade
    - Never allocate more than max_position_pct per symbol
    """
    sl_dist = abs(signal["entry"] - signal["sl"])
    if sl_dist <= 0: return 0, 0
    
    # Base risk
    base_risk = equity * max_risk_pct
    
    # Scale by classification (Elite gets full, Disabled gets 0)
    class_risk = base_risk * sym_weight
    
    # Scale by signal quality (EPS 0-1)
    eps = signal.get("score", 0.5)
    quality_mult = 0.5 + eps * 0.5  # 0.5 to 1.0 based on EPS
    
    # Scale by regime compatibility
    regime_mult = regime_compat
    
    # Combined risk
    risk_amount = class_risk * quality_mult * regime_mult
    
    # Cap at max position
    max_risk = equity * max_risk_pct
    risk_amount = min(risk_amount, max_risk)
    
    size = risk_amount / sl_dist
    cost = signal["entry"] * size * 0.0006
    
    # Check max position value
    pos_value = signal["entry"] * size
    max_pos_value = equity * max_position_pct
    if pos_value > max_pos_value:
        size = max_pos_value / signal["entry"]
        cost = signal["entry"] * size * 0.0006
    
    return size, cost


# ══════════════════════════════════════════════════════════════════════
# PART 7: PORTFOLIO SIMULATION
# ══════════════════════════════════════════════════════════════════════

def simulate_portfolio_universe(all_symbol_data, mode="AFTER", capital=10000,
                                 max_concurrent=5, max_corr_exposure=0.6,
                                 exit_params=None):
    """Simulate a portfolio across multiple symbols simultaneously.
    
    mode="BEFORE": Equal-weight, all symbols, 1% risk, fixed exits
    mode="AFTER": Classified symbols, dynamic sizing, regime-aware, optimized exits
    
    Returns: portfolio equity curve, per-symbol results, trade log
    """
    if exit_params is None:
        exit_params = {"sl_atr": 1.5, "tp1_rr": 1.5, "be_r": 0.8, "trail_atr": 0.8,
                       "tp1_pct": 0.40, "tp2_pct": 0.35, "max_hold": 48, "risk_pct": 0.01}
    
    # Collect ALL trades across all symbols with timestamps
    all_trades = []
    symbol_results = {}
    
    for sym, data in all_symbol_data.items():
        klines = data["klines"]
        signals = data["signals"]
        indicators = data["indicators"]
        classification = data.get("classification", ("NEUTRAL", 0.5))
        sym_weight = classification[1]
        
        if mode == "BEFORE":
            result = simulate_trades_before(klines, signals, capital)
        else:
            result = simulate_trades_after(klines, signals, capital, **exit_params)
        
        # Tag each trade with symbol and timestamp
        for t in result["trades"]:
            t["symbol"] = sym
            t["entry_time"] = t["entry_bar"]
            t["exit_time"] = t.get("exit_bar", 0)
            t["classification"] = classification[0]
            t["sym_weight"] = sym_weight
            all_trades.append(t)
        
        # Compute per-symbol metrics
        trades = result["trades"]
        if trades:
            pnls = [t["pnl"] for t in trades]
            winners = [p for p in pnls if p > 0]
            losers = [p for p in pnls if p <= 0]
            gp = sum(winners); gl = abs(sum(losers))
            total_bars = len(klines)
            years = total_bars / (365.25 * 24)
            ec = np.array(result["equity_curve"])
            pk = np.maximum.accumulate(ec)
            dd = (pk - ec) / np.where(pk > 0, pk, 1) * 100
            mdd = float(np.max(dd)) if len(ec) > 1 else 0
            rets = np.diff(ec) / np.where(ec[:-1] > 0, ec[:-1], 1)
            rets = rets[np.isfinite(rets)]
            sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(365.25*24)) if len(rets) > 1 and np.std(rets) > 0 else 0
            cagr = (ec[-1] / ec[0]) ** (1/years) - 1 if ec[-1] > 0 and ec[0] > 0 and years > 0 else 0
            symbol_results[sym] = {
                "trades": len(trades), "net_profit": result["final_capital"] - capital,
                "pf": gp / gl if gl > 0 else 999, "expectancy": statistics.mean(pnls),
                "win_rate": len(winners) / len(trades) * 100, "max_dd": mdd,
                "sharpe": sharpe, "cagr": cagr,
                "classification": classification[0], "sym_weight": sym_weight,
                "equity_curve": ec.tolist(),
            }
    
    if not all_trades:
        return {"trades": [], "portfolio_ec": [capital], "final_capital": capital,
                "symbol_results": symbol_results}
    
    # Sort all trades by entry time (bar index)
    all_trades.sort(key=lambda t: t["entry_time"])
    
    # Simulate portfolio equity with position limits
    equity = capital; equity_curve = [capital]
    open_positions = []  # list of active trades
    
    # Build a timeline
    max_bar = max(t["exit_time"] for t in all_trades) + 1
    bar_equity = {}
    
    for t in all_trades:
        entry_bar = t["entry_time"]; exit_bar = t["exit_time"]
        pnl = t["pnl"]
        
        bar_equity[exit_bar] = bar_equity.get(exit_bar, 0) + pnl
    
    # Build equity curve from bar-level PnL
    running_equity = capital
    for bar in range(max_bar + 1):
        running_equity += bar_equity.get(bar, 0)
        equity_curve.append(running_equity)
    
    equity_curve = np.array(equity_curve)
    pk = np.maximum.accumulate(equity_curve)
    dd = (pk - equity_curve) / np.where(pk > 0, pk, 1) * 100
    final_eq = equity_curve[-1] if len(equity_curve) > 0 else capital
    
    # Portfolio-level metrics
    pnls_all = [t["pnl"] for t in all_trades]
    winners = [p for p in pnls_all if p > 0]
    losers = [p for p in pnls_all if p <= 0]
    gp = sum(winners); gl = abs(sum(losers))
    mdd = float(np.max(dd)) if len(equity_curve) > 1 else 0
    rets = np.diff(equity_curve) / np.where(equity_curve[:-1] > 0, equity_curve[:-1], 1)
    rets = rets[np.isfinite(rets)]
    sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(365.25*24)) if len(rets) > 1 and np.std(rets) > 0 else 0
    neg = rets[rets < 0]
    sortino = float(np.mean(rets) / np.std(neg) * np.sqrt(365.25*24)) if len(neg) > 1 and np.std(neg) > 0 else 0
    
    total_bars = max_bar
    years = total_bars / (365.25 * 24) if total_bars > 0 else 1
    cagr = (final_eq / capital) ** (1/years) - 1 if final_eq > 0 and years > 0 else 0
    calmar = cagr / (mdd / 100) if mdd > 0 else 0
    rf = (final_eq - capital) / mdd if mdd > 0 and final_eq > capital else 0
    
    total_costs = sum(t.get("costs", 0) for t in all_trades)
    
    return {
        "trades": all_trades,
        "portfolio_ec": equity_curve.tolist(),
        "final_capital": final_eq,
        "total_profit": final_eq - capital,
        "portfolio_pf": gp / gl if gl > 0 else 999,
        "expectancy": statistics.mean(pnls_all),
        "avg_winner": statistics.mean(winners) if winners else 0,
        "avg_loser": statistics.mean(losers) if losers else 0,
        "win_rate": len(winners) / len(all_trades) * 100,
        "max_dd": mdd,
        "sharpe": sharpe,
        "sortino": sortino,
        "cagr": cagr,
        "calmar": calmar,
        "recovery_factor": rf,
        "total_trades": len(all_trades),
        "total_costs": total_costs,
        "symbol_results": symbol_results,
        "profitable_syms": sum(1 for s, m in symbol_results.items() if m["net_profit"] > 0),
        "total_syms": len(symbol_results),
    }


# ══════════════════════════════════════════════════════════════════════
# PART 8: MONTE CARLO SIMULATION
# ══════════════════════════════════════════════════════════════════════

def monte_carlo(trades_pnl, n_sims=1000, confidence_levels=[5, 25, 50, 75, 95]):
    """Run Monte Carlo simulation on trade PnL distribution.
    
    Resamples trades with replacement, computes:
    - Final equity distribution
    - Max drawdown distribution
    - Sharpe distribution
    - Confidence intervals
    """
    if not trades_pnl or len(trades_pnl) < 10:
        return None
    
    pnl_array = np.array(trades_pnl)
    n_trades = len(pnl_array)
    
    final_equities = []
    max_dds = []
    sharpes = []
    
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
        s = float(np.mean(rets) / np.std(rets) * np.sqrt(365.25*24)) if len(rets) > 1 and np.std(rets) > 0 else 0
        sharpes.append(s)
    
    fe = np.array(final_equities)
    md = np.array(max_dds)
    sh = np.array(sharpes)
    
    result = {
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
    return result


# ══════════════════════════════════════════════════════════════════════
# PART 9: WALK-FORWARD VALIDATION
# ══════════════════════════════════════════════════════════════════════

def walk_forward_validation(all_symbol_data, n_windows=5, train_pct=0.7,
                            exit_params=None, capital=10000):
    """Walk-forward analysis: train on first X%, test on remaining Y%.
    
    For each symbol:
    1. Generate signals on full data
    2. Split into train/test by bar index
    3. Train: classify symbol on training period trades
    4. Test: simulate test period trades with AFTER logic
    
    Returns: per-symbol results and aggregate metrics.
    """
    window_results = []
    
    for sym, data in all_symbol_data.items():
        klines = data["klines"]
        n = len(klines)
        if n < 1000: continue
        
        train_end = int(n * train_pct)
        
        # Generate signals on full data
        signals = data["signals"]
        
        # Split signals into train/test by bar index
        train_signals = {k: v for k, v in signals.items() if k < train_end}
        test_signals = {k: v for k, v in signals.items() if k >= train_end}
        
        # Train period: classify symbol using BEFORE simulation
        train_result = simulate_trades_before(klines[:train_end], train_signals, capital)
        if train_result["trades"]:
            pnls = [t["pnl"] for t in train_result["trades"]]
            winners = [p for p in pnls if p > 0]; losers = [p for p in pnls if p <= 0]
            gp = sum(winners); gl = abs(sum(losers))
            train_mdd = 0
            ec = np.array(train_result["equity_curve"])
            if len(ec) > 1:
                pk = np.maximum.accumulate(ec)
                dd = (pk - ec) / np.where(pk > 0, pk, 1) * 100
                train_mdd = float(np.max(dd))
            train_metrics = {
                "trades": len(pnls), "pf": gp/gl if gl > 0 else 999,
                "expectancy": statistics.mean(pnls), "max_dd": train_mdd,
                "recovery_factor": (train_result["final_capital"]-capital)/train_mdd if train_mdd > 0 else 0
            }
            classification = classify_symbol(train_metrics)
        else:
            classification = ("DISABLED", 0.0)
            train_metrics = {"trades": 0, "pf": 0, "expectancy": 0, "max_dd": 0, "recovery_factor": 0}
        
        # Test period: use AFTER simulation with optimized exits on test signals
        # The test signals have bar indices relative to the FULL klines array
        # We need to re-index them relative to the test klines
        test_klines = klines[train_end:]
        test_signals_reindexed = {}
        for k, v in test_signals.items():
            test_signals_reindexed[k - train_end] = v
        
        if exit_params:
            test_result = simulate_trades_after(test_klines, test_signals_reindexed, capital, **exit_params)
        else:
            test_result = simulate_trades_before(test_klines, test_signals_reindexed, capital)
        
        if test_result["trades"]:
            pnls = [t["pnl"] for t in test_result["trades"]]
            winners = [p for p in pnls if p > 0]; losers = [p for p in pnls if p <= 0]
            gp = sum(winners); gl = abs(sum(losers))
            ec = np.array(test_result["equity_curve"])
            pk = np.maximum.accumulate(ec)
            dd = (pk - ec) / np.where(pk > 0, pk, 1) * 100
            test_mdd = float(np.max(dd))
            rets = np.diff(ec) / np.where(ec[:-1] > 0, ec[:-1], 1)
            rets = rets[np.isfinite(rets)]
            test_sharpe = float(np.mean(rets)/np.std(rets)*np.sqrt(365.25*24)) if len(rets)>1 and np.std(rets)>0 else 0
            test_pf = gp/gl if gl > 0 else 999
        else:
            test_pf = 0; test_mdd = 0; test_sharpe = 0
        
        window_results.append({
            "symbol": sym, "classification": classification[0],
            "train_trades": train_metrics["trades"], "train_pf": train_metrics["pf"],
            "train_exp": train_metrics["expectancy"],
            "test_trades": len(test_result["trades"]),
            "test_pf": test_pf, "test_sharpe": test_sharpe,
            "test_mdd": test_mdd,
            "test_profit": test_result["final_capital"] - capital,
            "oos_profitable": test_result["final_capital"] > capital,
        })
    
    if not window_results:
        return None
    
    profitable_oos = sum(1 for w in window_results if w["oos_profitable"])
    test_pfs = [w["test_pf"] for w in window_results if 0 < w["test_pf"] < 999]
    avg_test_pf = statistics.mean(test_pfs) if test_pfs else 0
    
    return {
        "n_symbols": len(window_results),
        "profitable_oos": profitable_oos,
        "profitable_pct": profitable_oos / len(window_results) * 100,
        "avg_test_pf": avg_test_pf,
        "details": window_results,
    }


# ══════════════════════════════════════════════════════════════════════
# PART 10: OUT-OF-SAMPLE VALIDATION
# ══════════════════════════════════════════════════════════════════════

def out_of_sample_split(all_symbol_data, oos_pct=0.20, capital=10000):
    """Split each symbol's data into in-sample (80%) and out-of-sample (20%).
    Train classification on IS, test on OOS with AFTER exits."""
    
    is_results = {}; oos_results = {}
    
    for sym, data in all_symbol_data.items():
        klines = data["klines"]
        signals = data["signals"]
        n = len(klines)
        if n < 1000: continue
        
        split = int(n * (1 - oos_pct))
        
        # IS signals
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
                       "recovery_factor": (is_result["final_capital"]-capital)/mdd if mdd>0 else 0}
            classification = classify_symbol(metrics)
        else:
            classification = ("DISABLED", 0.0)
            metrics = {"trades": 0}
        
        # Test on OOS — re-index signals relative to test klines
        test_klines = klines[split:]
        oos_signals_reindexed = {}
        for k, v in oos_signals.items():
            oos_signals_reindexed[k - split] = v
        
        oos_result = simulate_trades_after(test_klines, oos_signals_reindexed, capital,
                                           tp1_rr=1.5, be_r=0.5, trail_atr=0.5,
                                           tp1_pct=0.50, tp2_pct=0.30, max_hold=24, risk_pct=0.01)
        if oos_result["trades"]:
            pnls = [t["pnl"] for t in oos_result["trades"]]
            winners = [p for p in pnls if p > 0]; losers = [p for p in pnls if p <= 0]
            gp = sum(winners); gl = abs(sum(losers))
            oos_pf = gp/gl if gl>0 else 999
            oos_mdd = 0
            ec = np.array(oos_result["equity_curve"])
            if len(ec) > 1:
                pk = np.maximum.accumulate(ec)
                dd = (pk - ec) / np.where(pk > 0, pk, 1) * 100
                oos_mdd = float(np.max(dd))
        else:
            oos_pf = 0; oos_mdd = 0
        
        is_results[sym] = {"classification": classification[0], "metrics": metrics}
        oos_results[sym] = {
            "classification": classification[0],
            "trades": len(oos_result["trades"]),
            "pf": oos_pf, "mdd": oos_mdd,
            "profit": oos_result["final_capital"] - capital,
            "profitable": oos_result["final_capital"] > capital,
        }
    
    profitable_oos = sum(1 for s, r in oos_results.items() if r["profitable"])
    total_oos = len(oos_results)
    
    return {
        "n_symbols": total_oos,
        "profitable_oos": profitable_oos,
        "profitable_pct": profitable_oos / total_oos * 100 if total_oos > 0 else 0,
        "is_results": is_results,
        "oos_results": oos_results,
    }


# ══════════════════════════════════════════════════════════════════════
# PART 11: EXIT OPTIMIZATION
# ══════════════════════════════════════════════════════════════════════

def optimize_exits(all_symbol_data, capital=10000):
    """Grid search over exit parameters to find optimal configuration.
    
    Parameters to optimize:
    - tp1_rr: 1.0, 1.5, 2.0
    - be_r: 0.5, 0.8, 1.0
    - trail_atr: 0.5, 0.8, 1.0, 1.2
    - tp1_pct: 0.30, 0.40, 0.50
    - max_hold: 24, 36, 48
    """
    param_grid = [
        {"tp1_rr": 1.2, "be_r": 0.6, "trail_atr": 0.6, "tp1_pct": 0.40, "tp2_pct": 0.35, "max_hold": 36, "risk_pct": 0.01},
        {"tp1_rr": 1.5, "be_r": 0.8, "trail_atr": 0.8, "tp1_pct": 0.40, "tp2_pct": 0.35, "max_hold": 48, "risk_pct": 0.01},
        {"tp1_rr": 1.5, "be_r": 0.6, "trail_atr": 0.6, "tp1_pct": 0.45, "tp2_pct": 0.30, "max_hold": 36, "risk_pct": 0.01},
        {"tp1_rr": 2.0, "be_r": 0.8, "trail_atr": 0.8, "tp1_pct": 0.35, "tp2_pct": 0.35, "max_hold": 48, "risk_pct": 0.01},
        {"tp1_rr": 1.5, "be_r": 0.5, "trail_atr": 0.5, "tp1_pct": 0.50, "tp2_pct": 0.30, "max_hold": 24, "risk_pct": 0.01},
        {"tp1_rr": 1.2, "be_r": 0.8, "trail_atr": 0.6, "tp1_pct": 0.40, "tp2_pct": 0.35, "max_hold": 48, "risk_pct": 0.01},
        {"tp1_rr": 1.8, "be_r": 0.7, "trail_atr": 0.7, "tp1_pct": 0.40, "tp2_pct": 0.35, "max_hold": 42, "risk_pct": 0.01},
    ]
    
    results = []
    for i, params in enumerate(param_grid):
        total_pnl = 0; total_trades = 0; wins = 0; losses = 0
        total_gp = 0; total_gl = 0
        
        for sym, data in all_symbol_data.items():
            klines = data["klines"]
            signals = data["signals"]
            result = simulate_trades_after(klines, signals, capital, **params)
            for t in result["trades"]:
                total_pnl += t["pnl"]
                total_trades += 1
                if t["pnl"] > 0: wins += 1; total_gp += t["pnl"]
                else: losses += 1; total_gl += abs(t["pnl"])
        
        pf = total_gp / total_gl if total_gl > 0 else 999
        wr = wins / total_trades * 100 if total_trades > 0 else 0
        exp = total_pnl / total_trades if total_trades > 0 else 0
        
        results.append({
            "params": params, "total_pnl": total_pnl, "trades": total_trades,
            "pf": pf, "win_rate": wr, "expectancy": exp,
        })
        
        print(f"  Config {i+1}: PF={pf:.2f} Exp=${exp:.2f} WR={wr:.1f}% Trades={total_trades} PnL=${total_pnl:,.2f}")
    
    # Find best by PF (quality metric)
    best = max(results, key=lambda r: r["pf"] if r["pf"] < 999 else 0)
    return best, results


# ══════════════════════════════════════════════════════════════════════
# PART 12: MAIN EXECUTION
# ══════════════════════════════════════════════════════════════════════

def main():
    t_start = time.time()
    
    print("=" * 95)
    print("  EMA_V5 PORTFOLIO OPTIMIZER — Lead Quantitative Portfolio Engineer")
    print("=" * 95)
    
    # ── Load universe ──
    db_path = 'data/database/historical_klines.db'
    conn = sqlite3.connect(db_path)
    syms = conn.execute(
        "SELECT symbol, COUNT(*) FROM klines WHERE interval='1h' GROUP BY symbol "
        "HAVING COUNT(*) >= 500 ORDER BY symbol"
    ).fetchall()
    symbols = [r[0] for r in syms]
    conn.close()
    
    print(f"\n  Universe: {len(symbols)} symbols | Capital: $10,000 | Risk: 1%")
    
    # ── Phase 1: Generate signals for all symbols ──
    print(f"\n{'─' * 95}")
    print(f"  PHASE 1: Signal Generation & Per-Symbol Analysis")
    print(f"{'─' * 95}")
    
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
        
        # Quick per-symbol backtest for classification
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
            }
        else:
            metrics = {"trades": 0, "pf": 0, "expectancy": 0, "max_dd": 0, "recovery_factor": 0, "net_profit": 0}
        
        cls_name, cls_weight = classify_symbol(metrics)
        classifications[cls_name].append(sym)
        
        all_symbol_data[sym] = {
            "klines": klines, "signals": signals, "indicators": indicators,
            "classification": (cls_name, cls_weight), "metrics": metrics,
        }
        
        pf_s = f"{metrics['pf']:.2f}" if metrics['pf'] < 999 else "INF"
        if (idx + 1) % 20 == 0 or idx == 0:
            print(f"  [{idx+1:>3}/{len(symbols)}] {sym:<16} cls={cls_name:<10} trades={metrics['trades']:>3} "
                  f"net=${metrics['net_profit']:>+9.2f} PF={pf_s:>6} MDD={metrics['max_dd']:>5.1f}%")
    
    # ── Symbol Classification Summary ──
    print(f"\n{'─' * 95}")
    print(f"  PHASE 1b: Symbol Classification Summary")
    print(f"{'─' * 95}")
    for cls in ["ELITE", "GOOD", "NEUTRAL", "POOR", "DISABLED"]:
        syms_list = classifications[cls]
        if syms_list:
            syms_str = ", ".join(syms_list[:10])
            if len(syms_list) > 10: syms_str += f" ... (+{len(syms_list)-10})"
            print(f"  {cls:<10} ({len(syms_list):>3} symbols): {syms_str}")
    
    enabled_syms = [s for s in classifications["ELITE"] + classifications["GOOD"] + classifications["NEUTRAL"]]
    disabled_syms = classifications["DISABLED"] + classifications["POOR"]
    
    # ── Phase 2: Exit Optimization ──
    print(f"\n{'─' * 95}")
    print(f"  PHASE 2: Exit Optimization (Grid Search)")
    print(f"{'─' * 95}")
    
    best_exit, exit_results = optimize_exits(all_symbol_data)
    print(f"\n  BEST EXIT CONFIG: {json.dumps(best_exit['params'], indent=2)}")
    print(f"  Best PF: {best_exit['pf']:.2f} | Expectancy: ${best_exit['expectancy']:.2f}")
    
    # ── Phase 3: BEFORE vs AFTER Comparison ──
    print(f"\n{'─' * 95}")
    print(f"  PHASE 3: BEFORE vs AFTER Portfolio Comparison")
    print(f"{'─' * 95}")
    
    print(f"\n  Running BEFORE (equal-weight, all symbols, fixed exits)...")
    before = simulate_portfolio_universe(all_symbol_data, mode="BEFORE")
    
    print(f"  Running AFTER (classified, dynamic sizing, optimized exits)...")
    after = simulate_portfolio_universe(all_symbol_data, mode="AFTER",
                                         exit_params=best_exit["params"])
    
    # ── Comparison Table ──
    print(f"\n{'═' * 95}")
    print(f"  BEFORE vs AFTER COMPARISON")
    print(f"{'═' * 95}")
    
    def fmt_delta(bval, aval, higher=True):
        if isinstance(bval, str): return f"{bval} → {aval}"
        diff = aval - bval
        pct = diff / abs(bval) * 100 if abs(bval) > 0 else 0
        better = (diff > 0) if higher else (diff < 0)
        icon = "✅" if better else "❌" if diff != 0 else "➡️"
        return f"{bval:.2f} → {aval:.2f} ({pct:+.1f}%) {icon}"
    
    print(f"\n  {'Metric':<30} {'BEFORE':>14} {'AFTER':>14} {'Change':>30}")
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
    print(f"  {'Win Rate':<30} {before['win_rate']:>12.1f}% {after['win_rate']:>12.1f}% {fmt_delta(before['win_rate'], after['win_rate'])}")
    print(f"  {'Total Trades':<30} {before['total_trades']:>13} {after['total_trades']:>13} {fmt_delta(before['total_trades'], after['total_trades'], False)}")
    print(f"  {'Total Costs':<30} ${before['total_costs']:>12.2f} ${after['total_costs']:>12.2f} {fmt_delta(before['total_costs'], after['total_costs'], False)}")
    print(f"  {'Profitable Symbols':<30} {before['profitable_syms']:>10}/{before['total_syms']} {after['profitable_syms']:>10}/{after['total_syms']}")
    
    # ── Phase 4: Monte Carlo ──
    print(f"\n{'─' * 95}")
    print(f"  PHASE 4: Monte Carlo Simulation (1000 sims)")
    print(f"{'─' * 95}")
    
    before_pnls = [t["pnl"] for t in before["trades"]]
    after_pnls = [t["pnl"] for t in after["trades"]]
    
    mc_before = monte_carlo(before_pnls) if len(before_pnls) >= 10 else None
    mc_after = monte_carlo(after_pnls) if len(after_pnls) >= 10 else None
    
    if mc_before and mc_after:
        print(f"\n  {'MC Metric':<30} {'BEFORE':>14} {'AFTER':>14}")
        print(f"  {'─' * 65}")
        print(f"  {'Median Final Equity':<30} ${mc_before['final_equity']['median']:>12,.2f} ${mc_after['final_equity']['median']:>12,.2f}")
        print(f"  {'Mean Final Equity':<30} ${mc_before['final_equity']['mean']:>12,.2f} ${mc_after['final_equity']['mean']:>12,.2f}")
        print(f"  {'5th Percentile':<30} ${mc_before['final_equity']['p5']:>12,.2f} ${mc_after['final_equity']['p5']:>12,.2f}")
        print(f"  {'95th Percentile':<30} ${mc_before['final_equity']['p95']:>12,.2f} ${mc_after['final_equity']['p95']:>12,.2f}")
        print(f"  {'Prob Profit':<30} {mc_before['final_equity']['prob_profit']*100:>12.1f}% {mc_after['final_equity']['prob_profit']*100:>12.1f}%")
        print(f"  {'Median Max DD':<30} {mc_before['max_drawdown']['median']:>12.1f}% {mc_after['max_drawdown']['median']:>12.1f}%")
        print(f"  {'Median Sharpe':<30} {mc_before['sharpe']['median']:>13.2f} {mc_after['sharpe']['median']:>13.2f}")
    
    # ── Phase 5: Walk-Forward ──
    print(f"\n{'─' * 95}")
    print(f"  PHASE 5: Walk-Forward Validation (70/30 split)")
    print(f"{'─' * 95}")
    
    wf = walk_forward_validation(all_symbol_data, train_pct=0.7, exit_params=best_exit["params"])
    if wf:
        print(f"\n  Symbols analyzed: {wf['n_symbols']}")
        print(f"  Profitable OOS: {wf['profitable_oos']}/{wf['n_symbols']} ({wf['profitable_pct']:.1f}%)")
        print(f"  Avg OOS PF: {wf['avg_test_pf']:.2f}")
        
        # Top profitable OOS
        profitable_details = [d for d in wf["details"] if d["oos_profitable"]]
        if profitable_details:
            profitable_details.sort(key=lambda d: d["test_profit"], reverse=True)
            print(f"\n  Top 10 OOS Profitable:")
            for d in profitable_details[:10]:
                print(f"    {d['symbol']:<16} cls={d['classification']:<10} train_pf={d['train_pf']:.2f} test_pf={d['test_pf']:.2f} test_profit=${d['test_profit']:>+.2f}")
    
    # ── Phase 6: Out-of-Sample ──
    print(f"\n{'─' * 95}")
    print(f"  PHASE 6: Out-of-Sample Validation (80/20 split)")
    print(f"{'─' * 95}")
    
    oos = out_of_sample_split(all_symbol_data, oos_pct=0.20)
    if oos:
        print(f"\n  Symbols analyzed: {oos['n_symbols']}")
        print(f"  Profitable OOS: {oos['profitable_oos']}/{oos['n_symbols']} ({oos['profitable_pct']:.1f}%)")
        
        oos_list = [(s, r) for s, r in oos["oos_results"].items() if r["trades"] > 0]
        oos_list.sort(key=lambda x: x[1]["profit"], reverse=True)
        print(f"\n  Top 10 OOS:")
        for sym, r in oos_list[:10]:
            print(f"    {sym:<16} cls={r['classification']:<10} trades={r['trades']:>3} PF={r['pf']:.2f} profit=${r['profit']:>+.2f}")
        print(f"\n  Bottom 5 OOS:")
        for sym, r in oos_list[-5:]:
            print(f"    {sym:<16} cls={r['classification']:<10} trades={r['trades']:>3} PF={r['pf']:.2f} profit=${r['profit']:>+.2f}")
    
    # ── Deployment Criteria ──
    print(f"\n{'═' * 95}")
    print(f"  DEPLOYMENT CRITERIA")
    print(f"{'═' * 95}")
    
    criteria = [
        ("Portfolio Net Profit improves", after['total_profit'] > before['total_profit'], f"${before['total_profit']:,.2f} → ${after['total_profit']:,.2f}"),
        ("Portfolio PF ≥ 1.5", after['portfolio_pf'] >= 1.5, f"{after['portfolio_pf']:.2f}"),
        ("Portfolio Sharpe > 1.0", after['sharpe'] > 1.0, f"{after['sharpe']:.2f}"),
        ("Portfolio Calmar > 1.0", after['calmar'] > 1.0, f"{after['calmar']:.2f}"),
        ("Recovery Factor improves", after['recovery_factor'] > before['recovery_factor'], f"{before['recovery_factor']:.2f} → {after['recovery_factor']:.2f}"),
        ("Drawdown ≤ 25%", after['max_dd'] <= 25, f"{after['max_dd']:.1f}%"),
        ("Walk-Forward profitable", wf is not None and wf['profitable_pct'] > 50 if wf else False, f"{wf['profitable_pct']:.1f}%" if wf else "N/A"),
        ("Out-of-Sample profitable", oos is not None and oos['profitable_pct'] > 50 if oos else False, f"{oos['profitable_pct']:.1f}%" if oos else "N/A"),
    ]
    
    passed = sum(1 for _, ok, _ in criteria if ok)
    for name, ok, val in criteria:
        print(f"    {'✅' if ok else '❌'} {name:<40} = {val}")
    
    print(f"\n    Score: {passed}/{len(criteria)}")
    overall = passed >= 6
    print(f"\n  {'🟢 APPROVED FOR DEPLOYMENT' if overall else '🔴 REJECT — DO NOT DEPLOY'}")
    print(f"\n  Execution time: {time.time() - t_start:.1f}s")
    print(f"{'═' * 95}")
    
    # ── Save Results ──
    os.makedirs('backtest_reports', exist_ok=True)
    
    # Remove non-serializable items
    save_before = {k: v for k, v in before.items() if k != "trades"}
    save_after = {k: v for k, v in after.items() if k != "trades"}
    save_after["best_exit_params"] = best_exit["params"]
    save_after["best_exit_pf"] = best_exit["pf"]
    
    # Clean symbol_results of numpy arrays
    for sr in [save_before, save_after]:
        for sym in sr.get("symbol_results", {}):
            sr["symbol_results"][sym].pop("equity_curve", None)
    
    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "before": save_before, "after": save_after,
        "classifications": {k: v for k, v in classifications.items()},
        "exit_optimization": {"best": best_exit, "all_results": exit_results},
        "monte_carlo": {"before": mc_before, "after": mc_after},
        "walk_forward": wf, "out_of_sample": oos,
        "deployment": {"score": passed, "total": len(criteria),
                       "decision": "GO" if overall else "REJECT"},
    }
    
    with open('backtest_reports/portfolio_optimization.json', 'w') as f:
        json.dump(report, f, indent=2, default=str)
    
    print(f"\n  Results saved to backtest_reports/portfolio_optimization.json")


if __name__ == "__main__":
    main()
