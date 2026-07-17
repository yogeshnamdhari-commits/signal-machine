#!/usr/bin/env python3
"""Debug: which filter kills all AFTER signals?"""
import sys, os, sqlite3, numpy as np
os.environ['LOGURU_LEVEL'] = 'ERROR'

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
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
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
    atr_s[period] = np.sum(tr[1:period+1])
    pdm_s[period] = np.sum(pdm[1:period+1])
    ndm_s[period] = np.sum(ndm[1:period+1])
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
    if c1 >= o1: return False
    if c2 <= o2: return False
    rng = max(o2, c2) - min(o2, c2)
    if rng == 0: return False
    body2 = abs(c2 - o2)
    return body2 / rng >= 0.5 and c2 > o1 and o2 < c1

def is_bearish_engulfing(o1, c1, o2, c2):
    if c1 <= o1: return False
    if c2 >= o2: return False
    rng = max(o2, c2) - min(o2, c2)
    if rng == 0: return False
    body2 = abs(c2 - o2)
    return body2 / rng >= 0.5 and c2 < o1 and o2 > c1

def is_hammer(o, h, l, c):
    body = abs(c - o)
    if body == 0: return False
    rng = h - l
    if rng == 0: return False
    lw = min(o, c) - l; uw = h - max(o, c)
    return lw / body >= 2.0 and uw <= body

def is_shooting_star(o, h, l, c):
    body = abs(c - o)
    if body == 0: return False
    rng = h - l
    if rng == 0: return False
    uw = h - max(o, c); lw = min(o, c) - l
    return uw / body >= 2.0 and lw <= body

def main():
    db_path = 'data/database/historical_klines.db'
    conn = sqlite3.connect(db_path)
    # Test on 5 diverse symbols
    test_syms = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'FILUSDT', 'DOGEUSDT']
    
    for sym in test_syms:
        rows = conn.execute(
            "SELECT open_time, open, high, low, close, volume FROM klines "
            "WHERE symbol=? AND interval='1h' ORDER BY open_time ASC", (sym,)
        ).fetchall()
        if len(rows) < 500:
            print(f"{sym}: only {len(rows)} bars, skipping")
            continue
        klines = np.array(rows, dtype=float)
        n = len(klines)
        opens = klines[:, 1]; highs = klines[:, 2]; lows = klines[:, 3]
        closes = klines[:, 4]; volumes = klines[:, 5]
        
        ema20 = ema_calc(closes, 20); ema50 = ema_calc(closes, 50)
        ema144 = ema_calc(closes, 144); ema200 = ema_calc(closes, 200)
        atr_arr = atr_calc(highs, lows, closes, 14)
        vol_sma = sma_calc(volumes, 20)
        rvol_arr = rvol_calc(volumes, 20)
        adx_arr, adx_plus, adx_minus = compute_adx(highs, lows, closes, 14)
        
        ema20s = np.full(n, 0.0); ema50s = np.full(n, 0.0)
        ema144s = np.full(n, 0.0); ema200s = np.full(n, 0.0)
        for i in range(220, n):
            for arr, sa in [(ema20, ema20s), (ema50, ema50s), (ema144, ema144s), (ema200, ema200s)]:
                w = arr[max(0,i-5):i+1]; w = w[~np.isnan(w)]
                if len(w) >= 2: sa[i] = (w[-1] - w[0]) / max(abs(w[0]), 1e-10) * 100
        
        blocks = {}
        min_adx = 18.0; min_rvol = 1.0; min_atr_pct = 0.05; min_rr = 1.8
        min_confidence = 75.0; sl_atr = 1.5; tp1_rr = 1.5
        
        for i in range(220, n):
            if any(np.isnan(x) for x in [ema20[i], ema50[i], ema144[i], ema200[i], atr_arr[i]]):
                blocks['nan'] = blocks.get('nan', 0) + 1; continue
            if atr_arr[i] <= 0 or vol_sma[i] <= 0:
                blocks['zero'] = blocks.get('zero', 0) + 1; continue
            
            # Chop
            ema_range = max(ema20[i], ema50[i], ema144[i], ema200[i]) - min(ema20[i], ema50[i], ema144[i], ema200[i])
            ema_range_pct = ema_range / closes[i] * 100 if closes[i] > 0 else 0
            if ema_range_pct < 0.2:
                blocks['chop'] = blocks.get('chop', 0) + 1; continue
            
            # ATR
            atr_pct = atr_arr[i] / closes[i] * 100 if closes[i] > 0 else 0
            if atr_pct < min_atr_pct:
                blocks['atr'] = blocks.get('atr', 0) + 1; continue
            
            # Regime
            buy = ema20[i] > ema50[i] > ema144[i] > ema200[i]
            sell = ema20[i] < ema50[i] < ema144[i] < ema200[i]
            if not buy and not sell:
                blocks['regime'] = blocks.get('regime', 0) + 1; continue
            if buy and not (ema144s[i] > 0 and ema200s[i] > 0 and closes[i] > ema144[i] and closes[i] > ema200[i]):
                blocks['regime_strength'] = blocks.get('regime_strength', 0) + 1; continue
            if sell and not (ema144s[i] < 0 and ema200s[i] < 0 and closes[i] < ema144[i] and closes[i] < ema200[i]):
                blocks['regime_strength'] = blocks.get('regime_strength', 0) + 1; continue
            
            # EMA200
            if buy and closes[i] < ema200[i]:
                blocks['ema200'] = blocks.get('ema200', 0) + 1; continue
            if sell and closes[i] > ema200[i]:
                blocks['ema200'] = blocks.get('ema200', 0) + 1; continue
            
            # ADX
            latest_adx = adx_arr[i]
            if latest_adx < min_adx:
                blocks['adx'] = blocks.get('adx', 0) + 1; continue
            
            # RVOL
            rvol = rvol_arr[i] if not np.isnan(rvol_arr[i]) else 0
            if rvol < min_rvol:
                blocks['rvol'] = blocks.get('rvol', 0) + 1; continue
            
            # Trend
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
            if score < 80:
                blocks['trend'] = blocks.get('trend', 0) + 1; continue
            
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
            if not pulled:
                blocks['pullback'] = blocks.get('pullback', 0) + 1; continue
            
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
            if not ck:
                blocks['candle'] = blocks.get('candle', 0) + 1; continue
            
            # Volume
            if volumes[i] < vol_sma[i]:
                blocks['volume'] = blocks.get('volume', 0) + 1; continue
            
            # Confidence
            conf = (100 * 0.15 + score * 0.25 + 100 * 0.25 + min(100, 90) * 0.05 + min(100, 50) * 0.10
                    + min(100, latest_adx / 50 * 100) * 0.05 + min(100, rvol / 3.0 * 100) * 0.05 + 100 * 0.05)
            if conf < min_confidence:
                blocks['confidence'] = blocks.get('confidence', 0) + 1
                if blocks.get('confidence', 0) <= 3:
                    print(f"  CONF DEBUG: conf={conf:.2f}, score={score}, adx={latest_adx:.1f}, rvol={rvol:.2f}")
                continue
            
            # Entry/RR
            entry = closes[i]; cur_atr = atr_arr[i]
            sl_dist = cur_atr * sl_atr
            side = "LONG" if buy else "SHORT"
            ema_val = ema20[i]
            if side == "LONG":
                sl = entry - sl_dist
                if ema_val > 0 and ema_val < entry: sl = max(sl, ema_val - cur_atr * 0.2)
                tp1 = entry + abs(entry - sl) * tp1_rr
            else:
                sl = entry + sl_dist
                if ema_val > 0 and ema_val > entry: sl = min(sl, ema_val + cur_atr * 0.2)
                tp1 = entry - abs(sl - entry) * tp1_rr
            rr = abs(tp1 - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
            if rr < min_rr:
                blocks['rr'] = blocks.get('rr', 0) + 1; continue
            
            blocks['pass'] = blocks.get('pass', 0) + 1
        
        total = sum(blocks.values())
        print(f"\n{sym} ({n} bars, {n-220} evaluable):")
        for k, v in sorted(blocks.items(), key=lambda x: -x[1]):
            print(f"  {k:<20} {v:>6} ({v/max(total,1)*100:.1f}%)")
        print(f"  TOTAL_CHECKED       {total:>6}")
    conn.close()

if __name__ == "__main__":
    main()
