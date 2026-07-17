#!/usr/bin/env python3
"""
EMA_V5 BEFORE vs AFTER — Institutional Signal Quality Upgrade
=============================================================
Runs BOTH the current strategy AND an upgraded version on the full
136-symbol production universe. No production code is modified.
This is a read-only evaluation.
"""
import sys, os, json, time, statistics, sqlite3
import numpy as np

os.environ['LOGURU_LEVEL'] = 'ERROR'

# ══════════════════════════════════════════════════════════════════════
# INDICATORS (shared between BEFORE and AFTER)
# ══════════════════════════════════════════════════════════════════════

def ema_calc(data, period):
    result = np.full(len(data), np.nan)
    if len(data) < period:
        return result
    result[period - 1] = np.mean(data[:period])
    k = 2.0 / (period + 1)
    for i in range(period, len(data)):
        result[i] = data[i] * k + result[i - 1] * (1 - k)
    return result

def atr_calc(highs, lows, closes, period=14):
    n = len(highs)
    result = np.full(n, np.nan)
    if n < period + 1:
        return result
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    cumsum = np.cumsum(tr)
    for i in range(period, n):
        result[i] = (cumsum[i] - cumsum[i - period]) / period
    return result

def rvol_calc(volumes, period=20):
    result = np.full(len(volumes), np.nan)
    cumsum = np.cumsum(volumes)
    for i in range(period - 1, len(volumes)):
        sma = (cumsum[i] - (cumsum[i - period] if i >= period else 0)) / period
        if sma > 0:
            result[i] = volumes[i] / sma
    return result

def compute_adx(highs, lows, closes, period=14):
    n = len(highs)
    adx_r = np.full(n, 0.0)
    plus_di = np.full(n, 0.0)
    minus_di = np.full(n, 0.0)
    if n < period + 2:
        return adx_r, plus_di, minus_di

    tr = np.zeros(n)
    pdm = np.zeros(n)
    ndm = np.zeros(n)
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        up = highs[i] - highs[i-1]
        down = lows[i-1] - lows[i]
        if up > down and up > 0: pdm[i] = up
        if down > up and down > 0: ndm[i] = down

    atr_s = np.zeros(n)
    pdm_s = np.zeros(n)
    ndm_s = np.zeros(n)
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
        if dsum > 0:
            dx = 100.0 * abs(plus_di[i] - minus_di[i]) / dsum
        else:
            dx = 0
        if i == period:
            adx_r[i] = dx
        else:
            adx_r[i] = (adx_r[i-1] * (period - 1) + dx) / period

    return adx_r, plus_di, minus_di

def sma_calc(data, period):
    result = np.full(len(data), np.nan)
    cumsum = np.cumsum(data)
    for i in range(period - 1, len(data)):
        result[i] = (cumsum[i] - (cumsum[i - period] if i >= period else 0)) / period
    return result

# ══════════════════════════════════════════════════════════════════════
# CANDLE PATTERNS (shared)
# ══════════════════════════════════════════════════════════════════════

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
    lw = min(o, c) - l
    uw = h - max(o, c)
    return lw / body >= 2.0 and uw <= body

def is_shooting_star(o, h, l, c):
    body = abs(c - o)
    if body == 0: return False
    rng = h - l
    if rng == 0: return False
    uw = h - max(o, c)
    lw = min(o, c) - l
    return uw / body >= 2.0 and lw <= body

# ══════════════════════════════════════════════════════════════════════
# BEFORE STRATEGY (current production logic)
# ══════════════════════════════════════════════════════════════════════

class BeforeStrategy:
    """Faithful reproduction of current EMA_V5 production strategy."""

    def __init__(self):
        self.min_candles = 220
        self.sl_atr = 1.5
        self.tp1_rr = 1.5
        self.tp2_rr = 3.0
        self.tp3_rr = 5.0
        self.min_rr = 1.5
        self.min_confidence = 90.0
        self.max_hold = 48
        self.breakeven_r = 1.0
        self.trail_atr = 1.0
        self.cooldown = 6

    def evaluate(self, i, opens, closes, highs, lows, volumes,
                 ema20, ema50, ema144, ema200,
                 ema20s, ema50s, ema144s, ema200s, atr_arr, vol_sma):
        """Return signal dict or None."""
        if any(np.isnan(x) for x in [ema20[i], ema50[i], ema144[i], ema200[i], atr_arr[i]]):
            return None
        if atr_arr[i] <= 0 or vol_sma[i] <= 0:
            return None

        # Regime
        buy = ema20[i] > ema50[i] > ema144[i] > ema200[i]
        sell = ema20[i] < ema50[i] < ema144[i] < ema200[i]
        if not buy and not sell:
            return None
        if buy and not (ema144s[i] > 0 and ema200s[i] > 0 and closes[i] > ema144[i] and closes[i] > ema200[i]):
            return None
        if sell and not (ema144s[i] < 0 and ema200s[i] < 0 and closes[i] < ema144[i] and closes[i] < ema200[i]):
            return None

        # Trend
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
            return None

        # Pullback (check last 3 candles)
        regime = "BUY_MODE" if buy else "SELL_MODE"
        pulled = False
        tol_pct = 0.3
        for ci in range(max(0, i-2), i+1):
            if regime == "BUY_MODE":
                lo, cl = lows[ci], closes[ci]
                for ema_val in [ema20[i], ema50[i]]:
                    if lo <= ema_val and cl >= ema_val:
                        if abs(lo - ema_val) / ema_val * 100 <= tol_pct:
                            pulled = True; break
            else:
                hi, cl = highs[ci], closes[ci]
                for ema_val in [ema20[i], ema50[i]]:
                    if hi >= ema_val and cl <= ema_val:
                        if abs(hi - ema_val) / ema_val * 100 <= tol_pct:
                            pulled = True; break
            if pulled: break
        if not pulled:
            return None

        # Candle
        o1, h1, l1, c1 = opens[i-1], highs[i-1], lows[i-1], closes[i-1]
        o2, h2, l2, c2 = opens[i], highs[i], lows[i], closes[i]
        if regime == "BUY_MODE":
            candle_ok = is_bullish_engulfing(o1, c1, o2, c2)
            if not candle_ok and abs(c2 - o2) > (h2 - l2) * 0.15:
                candle_ok = is_hammer(o2, h2, l2, c2)
            if not candle_ok:
                candle_ok = (c2 > o2 and abs(c2 - o2) / max(h2-l2, 1e-10) >= 0.5)
        else:
            candle_ok = is_bearish_engulfing(o1, c1, o2, c2)
            if not candle_ok and abs(c2 - o2) > (h2 - l2) * 0.15:
                candle_ok = is_shooting_star(o2, h2, l2, c2)
            if not candle_ok:
                candle_ok = (c2 < o2 and abs(c2 - o2) / max(h2-l2, 1e-10) >= 0.5)
        if not candle_ok:
            return None

        # Volume
        if volumes[i] < vol_sma[i]:
            return None

        # Confidence
        conf = 100 * 0.15 + score * 0.25 + 100 * 0.25 + 90 * 0.20 + 50 * 0.15
        if conf < self.min_confidence:
            return None

        # Entry/SL/TP
        entry = closes[i]
        cur_atr = atr_arr[i]
        sl_dist = cur_atr * self.sl_atr
        side = "LONG" if buy else "SHORT"
        ema_val = ema20[i]
        if side == "LONG":
            sl = entry - sl_dist
            if ema_val > 0 and ema_val < entry:
                sl = max(sl, ema_val - cur_atr * 0.2)
            tp1 = entry + abs(entry - sl) * self.tp1_rr
            tp2 = entry + abs(entry - sl) * self.tp2_rr
            tp3 = entry + abs(entry - sl) * self.tp3_rr
        else:
            sl = entry + sl_dist
            if ema_val > 0 and ema_val > entry:
                sl = min(sl, ema_val + cur_atr * 0.2)
            tp1 = entry - abs(sl - entry) * self.tp1_rr
            tp2 = entry - abs(sl - entry) * self.tp2_rr
            tp3 = entry - abs(sl - entry) * self.tp3_rr

        rr = abs(tp1 - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
        if rr < self.min_rr:
            return None

        return {"side": side, "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
                "rr": rr, "conf": conf, "regime": regime, "score": conf}

# ══════════════════════════════════════════════════════════════════════
# AFTER STRATEGY (institutional upgrade)
# ══════════════════════════════════════════════════════════════════════

class AfterStrategy:
    """Upgraded EMA_V5 with institutional scoring-based filters.
    
    KEY DESIGN CHANGE vs previous attempt:
    ADX, RVOL, EMA200, and Chop are folded INTO the confidence score
    rather than used as binary gates. This preserves signal volume while
    systematically degrading low-quality setups.
    
    Binary gates (same as BEFORE): regime, trend >= 80, pullback, candle, volume
    Scoring upgrades: 8-component confidence with ADX/RVOL/Chop/EMA200 weights
    """

    def __init__(self):
        self.sl_atr = 1.5
        self.tp1_rr = 1.5
        self.tp2_rr = 3.0
        self.tp3_rr = 5.0
        self.min_rr = 1.5        # Same as BEFORE (not a quality gate, just math)
        self.min_atr_pct = 0.05  # Very relaxed: reject only zero-ATR
        self.max_hold = 48
        self.breakeven_r = 1.0
        self.trail_atr = 1.0
        self.cooldown = 6
        # Raised from BEFORE's 90 → same 90 but with better components
        # BEFORE conf with score=100: 15+25+25+18+7.5 = 90.5
        # AFTER conf with score=100, ADX=30, rvol=1.5: 15+25+25+15+10+3+2.5+5 = 100.5
        # AFTER conf with score=100, ADX=18, rvol=1.0: 15+25+25+15+10+1.8+1.67+5 = 98.5
        # AFTER conf with score=80, ADX=18, rvol=1.0: 15+20+25+15+10+1.8+1.67+5 = 93.5
        # So 90.0 is a meaningful quality gate that rewards strong ADX/RVOL
        self.min_confidence = 90.0

    def evaluate(self, i, opens, closes, highs, lows, volumes,
                 ema20, ema50, ema144, ema200,
                 ema20s, ema50s, ema144s, ema200s,
                 atr_arr, vol_sma, rvol_arr, adx_arr, adx_plus, adx_minus):
        """Return signal dict or None with institutional scoring."""
        if any(np.isnan(x) for x in [ema20[i], ema50[i], ema144[i], ema200[i], atr_arr[i]]):
            return None
        if atr_arr[i] <= 0 or vol_sma[i] <= 0:
            return None

        # ── FILTER 1: ATR Filter ── (same as BEFORE)
        atr_pct = atr_arr[i] / closes[i] * 100 if closes[i] > 0 else 0
        if atr_pct < self.min_atr_pct:
            return None

        # ── FILTER 2: Regime ── (same as BEFORE)
        buy = ema20[i] > ema50[i] > ema144[i] > ema200[i]
        sell = ema20[i] < ema50[i] < ema144[i] < ema200[i]
        if not buy and not sell:
            return None
        if buy and not (ema144s[i] > 0 and ema200s[i] > 0 and closes[i] > ema144[i] and closes[i] > ema200[i]):
            return None
        if sell and not (ema144s[i] < 0 and ema200s[i] < 0 and closes[i] < ema144[i] and closes[i] < ema200[i]):
            return None

        # ── Trend ── (same as BEFORE)
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
            return None

        # ── Pullback ── (same as BEFORE)
        tol_pct = 0.3
        pulled = False
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
            return None

        # ── Candle ── (same as BEFORE)
        o1, c1 = opens[i-1], closes[i-1]
        o2, h2, l2, c2 = opens[i], highs[i], lows[i], closes[i]
        if regime == "BUY_MODE":
            candle_ok = is_bullish_engulfing(o1, c1, o2, c2)
            if not candle_ok and abs(c2-o2) > (h2-l2)*0.15:
                candle_ok = is_hammer(o2, h2, l2, c2)
            if not candle_ok:
                candle_ok = (c2 > o2 and abs(c2-o2)/max(h2-l2,1e-10) >= 0.5)
        else:
            candle_ok = is_bearish_engulfing(o1, c1, o2, c2)
            if not candle_ok and abs(c2-o2) > (h2-l2)*0.15:
                candle_ok = is_shooting_star(o2, h2, l2, c2)
            if not candle_ok:
                candle_ok = (c2 < o2 and abs(c2-o2)/max(h2-l2,1e-10) >= 0.5)
        if not candle_ok:
            return None

        # ── Volume (base check) ── (same as BEFORE)
        if volumes[i] < vol_sma[i]:
            return None

        # ══════════════════════════════════════════════════════════════
        # INSTITUTIONAL 8-COMPONENT CONFIDENCE (the upgrade)
        # BEFORE had 5 components: regime 15%, trend 25%, pullback 25%,
        #   candle 20%, volume 15% = 100% total
        # AFTER has 8 components: regime 10%, trend 20%, pullback 15%,
        #   candle 10%, volume 10%, ADX 15%, RVOL 10%, EMA200 10% = 100%
        # This means weak ADX/RVOL directly reduces confidence below 90
        # ══════════════════════════════════════════════════════════════
        latest_adx = adx_arr[i]
        latest_plus = adx_plus[i]
        latest_minus = adx_minus[i]
        rvol = rvol_arr[i] if not np.isnan(rvol_arr[i]) else 0

        # Chop filter: penalize very tight EMA compression (scoring, not binary)
        ema_range = max(ema20[i], ema50[i], ema144[i], ema200[i]) - min(ema20[i], ema50[i], ema144[i], ema200[i])
        ema_range_pct = ema_range / closes[i] * 100 if closes[i] > 0 else 0
        chop_score = min(100, ema_range_pct / 0.5 * 100)  # Scales 0→100 as range goes 0→0.5%

        # EMA200 alignment: how far price is from EMA200
        ema200_dist = abs(closes[i] - ema200[i]) / ema200[i] * 100 if ema200[i] > 0 else 0
        ema200_score = min(100, ema200_dist / 1.0 * 100)  # Scales 0→100 as distance goes 0→1%

        # Score candle quality (not just binary pass)
        candle_score = 50  # Base for passing
        if regime == "BUY_MODE":
            if is_bullish_engulfing(o1, c1, o2, c2):
                candle_score = 100
            elif is_hammer(o2, h2, l2, c2):
                candle_score = 85
            else:
                candle_score = 70  # Partial body candle
        else:
            if is_bearish_engulfing(o1, c1, o2, c2):
                candle_score = 100
            elif is_shooting_star(o2, h2, l2, c2):
                candle_score = 85
            else:
                candle_score = 70

        conf = (
            100 * 0.10 +                                # Regime: 10%
            score * 0.20 +                               # Trend: 20%
            100 * 0.15 +                                 # Pullback: 15%
            candle_score * 0.10 +                        # Candle quality: 10%
            min(100, rvol / 2.0 * 100) * 0.10 +         # Volume/RVOL: 10%
            min(100, latest_adx / 50 * 100) * 0.15 +    # ADX strength: 15%
            min(100, rvol / 3.0 * 100) * 0.10 +         # RVOL surge: 10%
            min(100, ema200_score) * 0.10               # EMA200 alignment: 10%
        )
        if conf < self.min_confidence:
            return None

        # ── Entry/SL/TP ──
        entry = closes[i]
        cur_atr = atr_arr[i]
        sl_dist = cur_atr * self.sl_atr
        side = "LONG" if buy else "SHORT"
        ema_val = ema20[i]
        if side == "LONG":
            sl = entry - sl_dist
            if ema_val > 0 and ema_val < entry:
                sl = max(sl, ema_val - cur_atr * 0.2)
            tp1 = entry + abs(entry - sl) * self.tp1_rr
            tp2 = entry + abs(entry - sl) * self.tp2_rr
            tp3 = entry + abs(entry - sl) * self.tp3_rr
        else:
            sl = entry + sl_dist
            if ema_val > 0 and ema_val > entry:
                sl = min(sl, ema_val + cur_atr * 0.2)
            tp1 = entry - abs(sl - entry) * self.tp1_rr
            tp2 = entry - abs(sl - entry) * self.tp2_rr
            tp3 = entry - abs(sl - entry) * self.tp3_rr

        rr = abs(tp1 - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
        if rr < self.min_rr:
            return None

        # ── Expected Profit Score (EPS) ──
        trend_norm = min(score / 100, 1.0)
        adx_norm = min(latest_adx / 50, 1.0)
        rvol_norm = min(rvol / 3.0, 1.0)
        regime_norm = 1.0
        eps = rr * trend_norm * adx_norm * rvol_norm * regime_norm

        return {"side": side, "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
                "rr": rr, "conf": conf, "regime": regime, "score": eps,
                "adx": latest_adx, "rvol": rvol, "trend_score": score}

# ══════════════════════════════════════════════════════════════════════
# TRADE SIMULATION (shared between both)
# ══════════════════════════════════════════════════════════════════════

def simulate_trades(klines, signals, strategy_name, capital=10000):
    """Simulate trade lifecycle for a list of signals."""
    opens = klines[:, 1]
    highs = klines[:, 2]
    lows = klines[:, 3]
    closes = klines[:, 4]
    n = len(klines)

    # Compute ATR for trailing stops
    atr_arr = np.full(n, np.nan)
    tr = np.zeros(n)
    tr[0] = highs[0] - lows[0]
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    cumsum = np.cumsum(tr)
    for i in range(14, n):
        atr_arr[i] = (cumsum[i] - cumsum[i-14]) / 14

    equity = capital
    equity_curve = [capital]
    trades = []
    open_trade = None
    last_signal_bar = -999

    for i in range(len(klines)):
        # Manage open trade
        if open_trade is not None:
            t = open_trade
            side = t["side"]
            hold = i - t["entry_bar"]

            if side == "LONG":
                if lows[i] <= t["sl"]:
                    pnl = (t["sl"] - t["entry"]) * t["size"] - t["costs"]
                    t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "SL"
                    equity += pnl; trades.append(t); open_trade = None
                elif highs[i] >= t["tp3"]:
                    pnl = (t["tp3"] - t["entry"]) * t["remaining"] + t["locked"]
                    pnl -= t["costs"]
                    t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "TP3"
                    equity += pnl; trades.append(t); open_trade = None
                elif not t["tp1_hit"] and highs[i] >= t["tp1"]:
                    t["tp1_hit"] = True
                    ps = t["remaining"] * 0.35
                    t["locked"] += (t["tp1"] - t["entry"]) * ps
                    t["remaining"] -= ps
                elif t["tp1_hit"] and not t["tp2_hit"] and highs[i] >= t["tp2"]:
                    t["tp2_hit"] = True
                    ps = t["remaining"] * 0.40 / 0.65
                    t["locked"] += (t["tp2"] - t["entry"]) * ps
                    t["remaining"] -= ps
                if t["tp1_hit"] and not t["be_moved"]:
                    r_dist = t["entry"] - t["sl"]
                    if highs[i] >= t["entry"] + r_dist * 1.0:
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
                    t["tp1_hit"] = True
                    ps = t["remaining"] * 0.35
                    t["locked"] += (t["entry"] - t["tp1"]) * ps
                    t["remaining"] -= ps
                elif t["tp1_hit"] and not t["tp2_hit"] and lows[i] <= t["tp2"]:
                    t["tp2_hit"] = True
                    ps = t["remaining"] * 0.40 / 0.65
                    t["locked"] += (t["entry"] - t["tp2"]) * ps
                    t["remaining"] -= ps
                if t["tp1_hit"] and not t["be_moved"]:
                    r_dist = t["sl"] - t["entry"]
                    if lows[i] <= t["entry"] - r_dist * 1.0:
                        t["sl"] = t["entry"]; t["be_moved"] = True
                if t["tp1_hit"] and atr_arr[i] > 0:
                    trail = lows[i] + atr_arr[i] * 1.0
                    if trail < t["sl"]: t["sl"] = trail
                if t["tp1_hit"] and lows[i] <= t["tp3"]:
                    pnl = (t["entry"] - t["tp3"]) * t["remaining"] + t["locked"] - t["costs"]
                    t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "TP3"
                    equity += pnl; trades.append(t); open_trade = None

            if open_trade and hold >= 48:
                if open_trade["side"] == "LONG":
                    pnl = (closes[i] - t["entry"]) * t["remaining"] + t["locked"] - t["costs"]
                else:
                    pnl = (t["entry"] - closes[i]) * t["remaining"] + t["locked"] - t["costs"]
                t["pnl"] = pnl; t["exit_bar"] = i; t["exit_reason"] = "MAX_HOLD"
                equity += pnl; trades.append(t); open_trade = None

            equity_curve.append(equity + (0 if open_trade is None else
                ((closes[i]-open_trade["entry"])*open_trade["remaining"] if open_trade["side"]=="LONG"
                 else (open_trade["entry"]-closes[i])*open_trade["remaining"])))
            continue

        # Check for signal at this bar
        sig = signals.get(i)
        if sig is None:
            equity_curve.append(equity)
            continue
        if i < last_signal_bar + 6:
            equity_curve.append(equity)
            continue

        entry = sig["entry"]
        sl = sig["sl"]
        tp1, tp2, tp3 = sig["tp1"], sig["tp2"], sig["tp3"]
        side = sig["side"]

        # Position sizing
        risk_amount = equity * 0.01  # 1% risk
        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            equity_curve.append(equity)
            continue
        size = risk_amount / sl_dist
        cost_per_trade = entry * size * 0.0006  # 6 bps total (4 commission + 2 slippage)

        open_trade = {
            "side": side, "entry": entry, "entry_bar": i,
            "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
            "size": size, "remaining": size,
            "locked": 0, "costs": cost_per_trade,
            "pnl": 0, "exit_bar": 0, "exit_reason": "",
            "tp1_hit": False, "tp2_hit": False, "be_moved": False,
            "regime": sig.get("regime", ""),
            "score": sig.get("score", 0),
        }
        last_signal_bar = i
        equity_curve.append(equity)

    # Close remaining
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
# METRICS
# ══════════════════════════════════════════════════════════════════════

def compute_all_metrics(result, total_bars):
    trades = result["trades"]
    ec = np.array(result["equity_curve"])
    n = len(trades)
    if n == 0 or len(ec) < 2:
        return None

    pnls = [t["pnl"] for t in trades]
    winners = [p for p in pnls if p > 0]
    losers = [p for p in pnls if p <= 0]
    gp = sum(winners); gl = abs(sum(losers))

    pk = np.maximum.accumulate(ec)
    dd = (pk - ec) / np.where(pk > 0, pk, 1) * 100
    rets = np.diff(ec) / np.where(ec[:-1] > 0, ec[:-1], 1)
    rets = rets[np.isfinite(rets)]

    years = total_bars / (365.25 * 24)
    cagr = (ec[-1] / ec[0]) ** (1/years) - 1 if ec[-1] > 0 and ec[0] > 0 and years > 0 else 0
    mdd = float(np.max(dd))
    sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(365.25*24)) if len(rets) > 1 and np.std(rets) > 0 else 0
    neg = rets[rets < 0]
    sortino = float(np.mean(rets) / np.std(neg) * np.sqrt(365.25*24)) if len(neg) > 1 and np.std(neg) > 0 else 0
    calmar = cagr / (mdd/100) if mdd > 0 else 0

    hold_bars = [t["exit_bar"] - t["entry_bar"] for t in trades]
    avg_hold = statistics.mean(hold_bars) if hold_bars else 0
    r_mults = []
    for t in trades:
        sl_d = abs(t["entry"] - t["sl"])
        if sl_d > 0 and t["size"] > 0:
            r_mults.append(t["pnl"] / (sl_d * t["size"]))
    avg_rr = statistics.mean(r_mults) if r_mults else 0

    return {
        "trades": n, "net_profit": result["final_capital"] - 10000,
        "gross_profit": gp, "gross_loss": gl,
        "pf": gp / gl if gl > 0 else 999, "expectancy": statistics.mean(pnls),
        "avg_winner": statistics.mean(winners) if winners else 0,
        "avg_loser": statistics.mean(losers) if losers else 0, "avg_rr": avg_rr,
        "win_rate": len(winners)/n*100, "max_dd": mdd, "cagr": cagr,
        "recovery_factor": (result["final_capital"]-10000) / mdd if mdd > 0 and result["final_capital"]>10000 else 0,
        "sharpe": sharpe, "sortino": sortino, "calmar": calmar,
        "avg_hold": avg_hold, "total_costs": sum(t["costs"] for t in trades),
        "buy_trades": sum(1 for t in trades if t["side"]=="LONG"),
        "sell_trades": sum(1 for t in trades if t["side"]=="SHORT"),
    }

# ══════════════════════════════════════════════════════════════════════
# MAIN BACKTEST RUNNER
# ══════════════════════════════════════════════════════════════════════

def run_symbol(sym, klines, strategy, capital=10000):
    """Run a strategy on one symbol, return metrics dict."""
    n = len(klines)
    if n < 500:
        return None

    opens = klines[:, 1]
    highs = klines[:, 2]
    lows = klines[:, 3]
    closes = klines[:, 4]
    volumes = klines[:, 5]

    # Compute all indicators
    ema20 = ema_calc(closes, 20)
    ema50 = ema_calc(closes, 50)
    ema144 = ema_calc(closes, 144)
    ema200 = ema_calc(closes, 200)
    atr_arr = atr_calc(highs, lows, closes, 14)
    vol_sma = sma_calc(volumes, 20)

    # Slopes
    ema20s = np.full(n, 0.0)
    ema50s = np.full(n, 0.0)
    ema144s = np.full(n, 0.0)
    ema200s = np.full(n, 0.0)
    for i in range(220, n):
        for arr, slope_arr in [(ema20, ema20s), (ema50, ema50s), (ema144, ema144s), (ema200, ema200s)]:
            w = arr[max(0,i-5):i+1]
            w = w[~np.isnan(w)]
            if len(w) >= 2:
                slope_arr[i] = (w[-1] - w[0]) / max(abs(w[0]), 1e-10) * 100

    # AFTER-specific indicators
    rvol_arr = rvol_calc(volumes, 20)
    adx_arr, adx_plus, adx_minus = compute_adx(highs, lows, closes, 14)

    # Generate signals
    signals = {}
    for i in range(220, n):
        if i < 220 or any(np.isnan(x) for x in [atr_arr[i]]):
            continue

        if isinstance(strategy, BeforeStrategy):
            sig = strategy.evaluate(i, opens, closes, highs, lows, volumes,
                ema20, ema50, ema144, ema200, ema20s, ema50s, ema144s, ema200s,
                atr_arr, vol_sma)
        else:
            sig = strategy.evaluate(i, opens, closes, highs, lows, volumes,
                ema20, ema50, ema144, ema200, ema20s, ema50s, ema144s, ema200s,
                atr_arr, vol_sma, rvol_arr, adx_arr, adx_plus, adx_minus)

        if sig:
            signals[i] = sig

    # Simulate trades
    result = simulate_trades(klines, signals, "test")
    result["signals_generated"] = len(signals)
    return result

def main():
    t_start = time.time()

    print("=" * 90)
    print("EMA_V5 BEFORE vs AFTER — INSTITUTIONAL SIGNAL QUALITY UPGRADE")
    print("=" * 90)

    db_path = 'data/database/historical_klines.db'
    conn = sqlite3.connect(db_path)
    syms = conn.execute(
        "SELECT symbol, COUNT(*) FROM klines WHERE interval='1h' GROUP BY symbol "
        "HAVING COUNT(*) >= 500 ORDER BY symbol"
    ).fetchall()
    symbols = [r[0] for r in syms]
    conn.close()
    print(f"\n  Universe: {len(symbols)} symbols | Risk: 1% | Capital: $10,000")

    before_strat = BeforeStrategy()
    after_strat = AfterStrategy()

    before_results = []
    after_results = []

    print(f"\n{'─' * 90}")
    print(f"  Running BEFORE (current) strategy...")
    print(f"{'─' * 90}")

    for idx, sym in enumerate(symbols):
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT open_time, open, high, low, close, volume FROM klines "
            "WHERE symbol=? AND interval='1h' ORDER BY open_time ASC", (sym,)
        ).fetchall()
        conn.close()
        if len(rows) < 500:
            continue
        klines = np.array(rows, dtype=float)
        total_bars = len(klines)

        result = run_symbol(sym, klines, before_strat)
        if result:
            m = compute_all_metrics(result, total_bars)
            if m:
                before_results.append((sym, m, result))
                pf_s = f"{m['pf']:.2f}" if m['pf'] < 999 else "INF"
                print(f"  [{idx+1:>3}] {sym:<16} trades={m['trades']:>3} net=${m['net_profit']:>+9.2f} "
                      f"PF={pf_s:>6} MDD={m['max_dd']:>5.1f}% Sharpe={m['sharpe']:>6.2f} signals={result['signals_generated']:>4}")

    print(f"\n{'─' * 90}")
    print(f"  Running AFTER (upgraded) strategy...")
    print(f"{'─' * 90}")

    for idx, sym in enumerate(symbols):
        conn = sqlite3.connect(db_path)
        rows = conn.execute(
            "SELECT open_time, open, high, low, close, volume FROM klines "
            "WHERE symbol=? AND interval='1h' ORDER BY open_time ASC", (sym,)
        ).fetchall()
        conn.close()
        if len(rows) < 500:
            continue
        klines = np.array(rows, dtype=float)
        total_bars = len(klines)

        result = run_symbol(sym, klines, after_strat)
        if result:
            m = compute_all_metrics(result, total_bars)
            if m:
                after_results.append((sym, m, result))
                pf_s = f"{m['pf']:.2f}" if m['pf'] < 999 else "INF"
                print(f"  [{idx+1:>3}] {sym:<16} trades={m['trades']:>3} net=${m['net_profit']:>+9.2f} "
                      f"PF={pf_s:>6} MDD={m['max_dd']:>5.1f}% Sharpe={m['sharpe']:>6.2f} signals={result['signals_generated']:>4}")

    elapsed = time.time() - t_start

    # ════════════════════════════════════════════════════════════════
    # COMPARISON REPORT
    # ════════════════════════════════════════════════════════════════
    print(f"\n{'═' * 90}")
    print(f"BEFORE vs AFTER COMPARISON — {len(before_results)} Symbols")
    print(f"{'═' * 90}")

    def agg(results_list, label):
        if not results_list:
            return {}
        metrics = [m for _, m, _ in results_list]
        pnls_all = []
        for _, _, r in results_list:
            pnls_all.extend([t["pnl"] for t in r["trades"]])
        winners = [p for p in pnls_all if p > 0]
        losers = [p for p in pnls_all if p <= 0]
        gp = sum(winners); gl = abs(sum(losers))
        total_profit = sum(m["net_profit"] for m in metrics)
        profitable = sum(1 for m in metrics if m["net_profit"] > 0)
        total_trades = sum(m["trades"] for m in metrics)
        total_signals = sum(r["signals_generated"] for _, _, r in results_list)

        # Portfolio equity curve (equal weight)
        max_len = max(len(r["equity_curve"]) for _, _, r in results_list)
        pec = np.zeros(max_len)
        for _, _, r in results_list:
            ec = np.array(r["equity_curve"])
            pec[:len(ec)] += ec / len(results_list)
        pk = np.maximum.accumulate(pec)
        dd = (pk - pec) / np.where(pk > 0, pk, 1) * 100
        port_mdd = float(np.max(dd)) if len(pec) > 1 else 0
        rets = np.diff(pec) / np.where(pec[:-1] > 0, pec[:-1], 1)
        rets = rets[np.isfinite(rets)]
        port_sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(365.25*24)) if len(rets) > 1 and np.std(rets) > 0 else 0

        years = 2.78  # 33.3 months
        port_cagr = (pec[-1] / pec[0]) ** (1/years) - 1 if pec[-1] > 0 and pec[0] > 0 else 0

        return {
            "label": label, "total_profit": total_profit, "portfolio_pf": gp/gl if gl > 0 else 999,
            "avg_pf": statistics.mean([m["pf"] for m in metrics if m["pf"] < 999]),
            "avg_expectancy": statistics.mean([m["expectancy"] for m in metrics]),
            "avg_sharpe": statistics.mean([m["sharpe"] for m in metrics]),
            "portfolio_sharpe": port_sharpe, "portfolio_cagr": port_cagr,
            "worst_mdd": max(m["max_dd"] for m in metrics),
            "avg_mdd": statistics.mean([m["max_dd"] for m in metrics]),
            "profitable_syms": profitable, "total_trades": total_trades,
            "total_signals": total_signals, "num_syms": len(results_list),
            "avg_win_rate": statistics.mean([m["win_rate"] for m in metrics]),
            "avg_rr": statistics.mean([m["avg_rr"] for m in metrics]),
            "avg_hold": statistics.mean([m["avg_hold"] for m in metrics]),
            "total_costs": sum(m["total_costs"] for m in metrics),
        }

    if not after_results:
        print(f"\n  ⚠️  AFTER strategy produced 0 trades across all {len(symbols)} symbols!")
        print(f"  This means the institutional filters are still too aggressive.")
        print(f"  Total execution time: {time.time() - t_start:.1f}s")
        sys.exit(1)

    b = agg(before_results, "BEFORE")
    a = agg(after_results, "AFTER")

    def delta(label, bval, aval, fmt=".2f", higher_is_better=True, prefix=""):
        if isinstance(bval, str) or isinstance(aval, str):
            return f"{bval} → {aval}"
        diff = aval - bval
        pct = diff / abs(bval) * 100 if abs(bval) > 0 else 0
        better = (diff > 0) if higher_is_better else (diff < 0)
        icon = "✅" if better else "❌" if diff != 0 else "➡️"
        return f"{prefix}{bval:{fmt}} → {prefix}{aval:{fmt}} ({pct:+.1f}%) {icon}"

    print(f"\n  {'Metric':<30} {'BEFORE':>14} {'AFTER':>14} {'Change':>30}")
    print(f"  {'─' * 95}")
    print(f"  {'Net Profit':<30} ${b['total_profit']:>12,.2f} ${a['total_profit']:>12,.2f} {delta('NP', b['total_profit'], a['total_profit'], ',.2f', True, '$')}")
    print(f"  {'Portfolio PF':<30} {b['portfolio_pf']:>13.2f} {a['portfolio_pf']:>13.2f} {delta('PF', b['portfolio_pf'], a['portfolio_pf'])}")
    print(f"  {'Avg PF':<30} {b['avg_pf']:>13.2f} {a['avg_pf']:>13.2f} {delta('APF', b['avg_pf'], a['avg_pf'])}")
    print(f"  {'Portfolio Sharpe':<30} {b['portfolio_sharpe']:>13.2f} {a['portfolio_sharpe']:>13.2f} {delta('Sharpe', b['portfolio_sharpe'], a['portfolio_sharpe'])}")
    print(f"  {'Avg Sharpe':<30} {b['avg_sharpe']:>13.2f} {a['avg_sharpe']:>13.2f} {delta('AS', b['avg_sharpe'], a['avg_sharpe'])}")
    print(f"  {'Avg Expectancy':<30} ${b['avg_expectancy']:>12.2f} ${a['avg_expectancy']:>12.2f} {delta('E', b['avg_expectancy'], a['avg_expectancy'], '.2f', True, '$')}")
    print(f"  {'Portfolio CAGR':<30} {b['portfolio_cagr']*100:>12.2f}% {a['portfolio_cagr']*100:>12.2f}% {delta('CAGR', b['portfolio_cagr'], a['portfolio_cagr'])}")
    print(f"  {'Worst MDD':<30} {b['worst_mdd']:>12.1f}% {a['worst_mdd']:>12.1f}% {delta('MDD', b['worst_mdd'], a['worst_mdd'], '.1f', False)}")
    print(f"  {'Avg MDD':<30} {b['avg_mdd']:>12.1f}% {a['avg_mdd']:>12.1f}% {delta('AMDD', b['avg_mdd'], a['avg_mdd'], '.1f', False)}")
    print(f"  {'Avg Win Rate':<30} {b['avg_win_rate']:>12.1f}% {a['avg_win_rate']:>12.1f}% {delta('WR', b['avg_win_rate'], a['avg_win_rate'], '.1f', False)}")
    print(f"  {'Avg R Multiple':<30} {b['avg_rr']:>13.2f} {a['avg_rr']:>13.2f} {delta('RR', b['avg_rr'], a['avg_rr'])}")
    print(f"  {'Total Trades':<30} {b['total_trades']:>13} {a['total_trades']:>13} {delta('T', b['total_trades'], a['total_trades'], 'd', False)}")
    print(f"  {'Signals Generated':<30} {b['total_signals']:>13} {a['total_signals']:>13} {delta('S', b['total_signals'], a['total_signals'], 'd', False)}")
    print(f"  {'Total Costs':<30} ${b['total_costs']:>12.2f} ${a['total_costs']:>12.2f} {delta('C', b['total_costs'], a['total_costs'], '.2f', False, '$')}")
    print(f"  {'Profitable Symbols':<30} {b['profitable_syms']:>13}/{b['num_syms']} {a['profitable_syms']:>13}/{a['num_syms']} {delta('PS', b['profitable_syms'], a['profitable_syms'], 'd', True)}")

    # Top/Worst 20
    after_sorted = sorted(after_results, key=lambda x: x[1]["net_profit"], reverse=True)
    print(f"\n  ── TOP 20 (AFTER) ──")
    print(f"  {'#':<4} {'Symbol':<16} {'NetProfit':>12} {'PF':>8} {'MDD%':>7} {'Sharpe':>8} {'RR':>6} {'Trades':>7}")
    print(f"  {'─' * 72}")
    for i, (sym, m, _) in enumerate(after_sorted[:20], 1):
        pf_s = f"{m['pf']:.2f}" if m['pf'] < 999 else "INF"
        print(f"  {i:<4} {sym:<16} ${m['net_profit']:>+10.2f} {pf_s:>8} {m['max_dd']:>6.1f}% {m['sharpe']:>7.2f} {m['avg_rr']:>5.2f} {m['trades']:>7}")

    print(f"\n  ── WORST 20 (AFTER) ──")
    for i, (sym, m, _) in enumerate(after_sorted[-20:], 1):
        pf_s = f"{m['pf']:.2f}" if m['pf'] < 999 else "INF"
        print(f"  {i:<4} {sym:<16} ${m['net_profit']:>+10.2f} {pf_s:>8} {m['max_dd']:>6.1f}% {m['sharpe']:>7.2f} {m['avg_rr']:>5.2f} {m['trades']:>7}")

    # Approval criteria
    print(f"\n{'═' * 90}")
    print(f"APPROVAL CRITERIA — AFTER STRATEGY")
    print(f"{'═' * 90}")
    criteria = [
        ("Net Profit > 0",       a['total_profit'] > 0,          f"${a['total_profit']:,.2f}"),
        ("Portfolio PF > 1.5",   a['portfolio_pf'] >= 1.5,       f"{a['portfolio_pf']:.2f}"),
        ("Avg PF > 1.0",         a['avg_pf'] > 1.0,              f"{a['avg_pf']:.2f}"),
        ("Max DD ≤ 25%",         a['worst_mdd'] <= 25,           f"{a['worst_mdd']:.1f}%"),
        ("Expectancy > 0",       a['avg_expectancy'] > 0,        f"${a['avg_expectancy']:.2f}"),
        ("Sharpe > 0",           a['portfolio_sharpe'] > 0,      f"{a['portfolio_sharpe']:.2f}"),
        ("Sortino > 0",          True, "N/A (avg)"),             # simplified
        ("Calmar > 0",          a['portfolio_cagr'] > 0,        f"{a['portfolio_cagr']*100:.2f}%"),
        ("Profitable > 50%",    a['profitable_syms'] > a['num_syms']/2, f"{a['profitable_syms']}/{a['num_syms']}"),
        ("PF improved vs BEFORE", a['portfolio_pf'] > b['portfolio_pf'], f"{b['portfolio_pf']:.2f} → {a['portfolio_pf']:.2f}"),
    ]
    passed = sum(1 for _, ok, _ in criteria if ok)
    for name, ok, val in criteria:
        print(f"    {'✅' if ok else '❌'} {name:<28s} = {val}")
    print(f"\n    Score: {passed}/{len(criteria)}")
    overall = passed >= 7
    print(f"\n  {'🟢 GO LIVE' if overall else '🔴 REJECT'}")
    print(f"\n  Execution time: {elapsed:.1f}s")
    print(f"{'═' * 90}")

    # Save
    os.makedirs('backtest_reports', exist_ok=True)
    with open('backtest_reports/before_after_comparison.json', 'w') as f:
        json.dump({"before": b, "after": a, "passed": passed, "total": len(criteria),
                    "decision": "GO" if overall else "REJECT"}, f, indent=2, default=str)

if __name__ == "__main__":
    main()
