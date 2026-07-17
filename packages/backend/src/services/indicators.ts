import { logger } from '../utils/logger';

export interface OHLCV {
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface IndicatorResult {
  value: number;
  signal?: 'buy' | 'sell' | 'neutral';
  strength?: number;
}

class IndicatorService {
  // Simple Moving Average
  sma(data: number[], period: number): number[] {
    const result: number[] = [];
    for (let i = 0; i < data.length; i++) {
      if (i < period - 1) {
        result.push(NaN);
      } else {
        const slice = data.slice(i - period + 1, i + 1);
        result.push(slice.reduce((a, b) => a + b, 0) / period);
      }
    }
    return result;
  }

  // Exponential Moving Average
  ema(data: number[], period: number): number[] {
    const result: number[] = [];
    const multiplier = 2 / (period + 1);
    
    // First EMA is SMA
    const firstSlice = data.slice(0, period);
    let prevEma = firstSlice.reduce((a, b) => a + b, 0) / period;
    
    for (let i = 0; i < data.length; i++) {
      if (i < period - 1) {
        result.push(NaN);
      } else if (i === period - 1) {
        result.push(prevEma);
      } else {
        const ema = (data[i] - prevEma) * multiplier + prevEma;
        result.push(ema);
        prevEma = ema;
      }
    }
    return result;
  }

  // Relative Strength Index
  rsi(closes: number[], period: number = 14): number[] {
    const result: number[] = [];
    const gains: number[] = [];
    const losses: number[] = [];

    for (let i = 1; i < closes.length; i++) {
      const change = closes[i] - closes[i - 1];
      gains.push(change > 0 ? change : 0);
      losses.push(change < 0 ? Math.abs(change) : 0);
    }

    for (let i = 0; i < gains.length; i++) {
      if (i < period - 1) {
        result.push(NaN);
      } else {
        const avgGain = gains.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0) / period;
        const avgLoss = losses.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0) / period;
        const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
        result.push(100 - 100 / (1 + rs));
      }
    }
    return result;
  }

  // MACD
  macd(
    closes: number[],
    fastPeriod: number = 12,
    slowPeriod: number = 26,
    signalPeriod: number = 9
  ): { macd: number[]; signal: number[]; histogram: number[] } {
    const fastEma = this.ema(closes, fastPeriod);
    const slowEma = this.ema(closes, slowPeriod);
    
    const macdLine: number[] = [];
    for (let i = 0; i < closes.length; i++) {
      if (isNaN(fastEma[i]) || isNaN(slowEma[i])) {
        macdLine.push(NaN);
      } else {
        macdLine.push(fastEma[i] - slowEma[i]);
      }
    }

    const validMacd = macdLine.filter((v) => !isNaN(v));
    const signalLine = this.ema(validMacd, signalPeriod);
    
    // Pad signal line to match MACD length
    const paddedSignal: number[] = [];
    let signalIdx = 0;
    for (let i = 0; i < macdLine.length; i++) {
      if (isNaN(macdLine[i])) {
        paddedSignal.push(NaN);
      } else {
        paddedSignal.push(signalLine[signalIdx] || NaN);
        signalIdx++;
      }
    }

    const histogram: number[] = [];
    for (let i = 0; i < macdLine.length; i++) {
      if (isNaN(macdLine[i]) || isNaN(paddedSignal[i])) {
        histogram.push(NaN);
      } else {
        histogram.push(macdLine[i] - paddedSignal[i]);
      }
    }

    return { macd: macdLine, signal: paddedSignal, histogram };
  }

  // Bollinger Bands
  bollingerBands(
    closes: number[],
    period: number = 20,
    stdDev: number = 2
  ): { upper: number[]; middle: number[]; lower: number[] } {
    const middle = this.sma(closes, period);
    const upper: number[] = [];
    const lower: number[] = [];

    for (let i = 0; i < closes.length; i++) {
      if (i < period - 1) {
        upper.push(NaN);
        lower.push(NaN);
      } else {
        const slice = closes.slice(i - period + 1, i + 1);
        const mean = middle[i];
        const variance = slice.reduce((sum, val) => sum + Math.pow(val - mean, 2), 0) / period;
        const std = Math.sqrt(variance);
        upper.push(mean + stdDev * std);
        lower.push(mean - stdDev * std);
      }
    }

    return { upper, middle, lower };
  }

  // Volume Profile
  volumeProfile(ohlcv: OHLCV[], bins: number = 24): Map<number, number> {
    const volumes = new Map<number, number>();
    const minPrice = Math.min(...ohlcv.map((c) => c.low));
    const maxPrice = Math.max(...ohlcv.map((c) => c.high));
    const binSize = (maxPrice - minPrice) / bins;

    for (const candle of ohlcv) {
      const avgPrice = (candle.high + candle.low + candle.close) / 3;
      const bin = Math.floor((avgPrice - minPrice) / binSize);
      const binPrice = minPrice + bin * binSize + binSize / 2;
      volumes.set(binPrice, (volumes.get(binPrice) || 0) + candle.volume);
    }

    return volumes;
  }

  // Average True Range (ATR)
  atr(highs: number[], lows: number[], closes: number[], period: number = 14): number[] {
    const trueRanges: number[] = [];
    
    for (let i = 0; i < highs.length; i++) {
      if (i === 0) {
        trueRanges.push(highs[i] - lows[i]);
      } else {
        const tr = Math.max(
          highs[i] - lows[i],
          Math.abs(highs[i] - closes[i - 1]),
          Math.abs(lows[i] - closes[i - 1])
        );
        trueRanges.push(tr);
      }
    }

    const result: number[] = [];
    for (let i = 0; i < trueRanges.length; i++) {
      if (i < period - 1) {
        result.push(NaN);
      } else if (i === period - 1) {
        result.push(trueRanges.slice(0, period).reduce((a, b) => a + b, 0) / period);
      } else {
        result.push((result[i - 1] * (period - 1) + trueRanges[i]) / period);
      }
    }
    return result;
  }

  // Generate trading signal from multiple indicators
  generateSignal(
    closes: number[],
    highs: number[],
    lows: number[],
    volumes: number[]
  ): { signal: 'buy' | 'sell' | 'neutral'; confidence: number; indicators: Record<string, any> } {
    const rsiValues = this.rsi(closes, 14);
    const macdResult = this.macd(closes);
    const bbResult = this.bollingerBands(closes);
    const sma20 = this.sma(closes, 20);
    const sma50 = this.sma(closes, 50);
    const atrValues = this.atr(highs, lows, closes);

    const latestIdx = closes.length - 1;
    const latestRsi = rsiValues[latestIdx] || 50;
    const latestMacd = macdResult.macd[latestIdx] || 0;
    const latestSignal = macdResult.signal[latestIdx] || 0;
    const latestHistogram = macdResult.histogram[latestIdx] || 0;
    const latestClose = closes[latestIdx];
    const latestBBUpper = bbResult.upper[latestIdx] || latestClose;
    const latestBBLower = bbResult.lower[latestIdx] || latestClose;
    const latestSma20 = sma20[latestIdx] || latestClose;
    const latestSma50 = sma50[latestIdx] || latestClose;
    const latestAtr = atrValues[latestIdx] || 0;

    let buySignals = 0;
    let sellSignals = 0;

    // RSI
    if (latestRsi < 30) buySignals++;
    else if (latestRsi > 70) sellSignals++;

    // MACD
    if (latestHistogram > 0 && latestMacd > latestSignal) buySignals++;
    else if (latestHistogram < 0 && latestMacd < latestSignal) sellSignals++;

    // Bollinger Bands
    if (latestClose < latestBBLower) buySignals++;
    else if (latestClose > latestBBUpper) sellSignals++;

    // Moving Average Cross
    if (latestSma20 > latestSma50) buySignals++;
    else if (latestSma20 < latestSma50) sellSignals++;

    const totalSignals = buySignals + sellSignals;
    const confidence = totalSignals > 0 ? Math.max(buySignals, sellSignals) / totalSignals : 0;

    let signal: 'buy' | 'sell' | 'neutral' = 'neutral';
    if (buySignals > sellSignals && buySignals >= 2) signal = 'buy';
    else if (sellSignals > buySignals && sellSignals >= 2) signal = 'sell';

    return {
      signal,
      confidence,
      indicators: {
        rsi: latestRsi,
        macd: { value: latestMacd, signal: latestSignal, histogram: latestHistogram },
        bollingerBands: { upper: latestBBUpper, lower: latestBBLower },
        sma20: latestSma20,
        sma50: latestSma50,
        atr: latestAtr,
      },
    };
  }

  // ── Institutional-Grade Indicators ──────────────────────────

  // On Balance Volume — volume-weighted price momentum
  obv(closes: number[], volumes: number[]): number[] {
    const result: number[] = [0];
    for (let i = 1; i < closes.length; i++) {
      if (closes[i] > closes[i - 1]) result.push(result[i - 1] + volumes[i]);
      else if (closes[i] < closes[i - 1]) result.push(result[i - 1] - volumes[i]);
      else result.push(result[i - 1]);
    }
    return result;
  }

  // Volume Weighted Average Price (session-based approximation)
  vwap(highs: number[], lows: number[], closes: number[], volumes: number[]): number[] {
    const result: number[] = [];
    let cumPV = 0;
    let cumV = 0;
    const period = 20; // Rolling VWAP window
    for (let i = 0; i < closes.length; i++) {
      const typicalPrice = (highs[i] + lows[i] + closes[i]) / 3;
      cumPV += typicalPrice * volumes[i];
      cumV += volumes[i];
      if (i >= period) {
        cumPV -= ((highs[i - period] + lows[i - period] + closes[i - period]) / 3) * volumes[i - period];
        cumV -= volumes[i - period];
      }
      result.push(cumV > 0 ? cumPV / cumV : closes[i]);
    }
    return result;
  }

  // Average Directional Index — trend strength
  adx(highs: number[], lows: number[], closes: number[], period: number = 14): { adx: number[]; plusDI: number[]; minusDI: number[] } {
    const len = closes.length;
    const plusDM: number[] = [];
    const minusDM: number[] = [];
    const trArr: number[] = [];

    for (let i = 1; i < len; i++) {
      const upMove = highs[i] - highs[i - 1];
      const downMove = lows[i - 1] - lows[i];
      plusDM.push(upMove > downMove && upMove > 0 ? upMove : 0);
      minusDM.push(downMove > upMove && downMove > 0 ? downMove : 0);
      trArr.push(Math.max(highs[i] - lows[i], Math.abs(highs[i] - closes[i - 1]), Math.abs(lows[i] - closes[i - 1])));
    }

    // Smooth with Wilder's method
    const smoothTR = [trArr.slice(0, period).reduce((a, b) => a + b, 0)];
    const smoothPlusDM = [plusDM.slice(0, period).reduce((a, b) => a + b, 0)];
    const smoothMinusDM = [minusDM.slice(0, period).reduce((a, b) => a + b, 0)];

    for (let i = period; i < trArr.length; i++) {
      smoothTR.push(smoothTR[smoothTR.length - 1] - smoothTR[smoothTR.length - 1] / period + trArr[i]);
      smoothPlusDM.push(smoothPlusDM[smoothPlusDM.length - 1] - smoothPlusDM[smoothPlusDM.length - 1] / period + plusDM[i]);
      smoothMinusDM.push(smoothMinusDM[smoothMinusDM.length - 1] - smoothMinusDM[smoothMinusDM.length - 1] / period + minusDM[i]);
    }

    const plusDI: number[] = [];
    const minusDI: number[] = [];
    const dxArr: number[] = [];

    for (let i = 0; i < smoothTR.length; i++) {
      const pdi = smoothTR[i] > 0 ? (smoothPlusDM[i] / smoothTR[i]) * 100 : 0;
      const mdi = smoothTR[i] > 0 ? (smoothMinusDM[i] / smoothTR[i]) * 100 : 0;
      plusDI.push(pdi);
      minusDI.push(mdi);
      const sum = pdi + mdi;
      dxArr.push(sum > 0 ? (Math.abs(pdi - mdi) / sum) * 100 : 0);
    }

    // ADX = smoothed DX
    const adxArr: number[] = [];
    if (dxArr.length >= period) {
      let adxVal = dxArr.slice(0, period).reduce((a, b) => a + b, 0) / period;
      for (let i = 0; i < period; i++) adxArr.push(NaN);
      adxArr.push(adxVal);
      for (let i = period; i < dxArr.length; i++) {
        adxVal = (adxVal * (period - 1) + dxArr[i]) / period;
        adxArr.push(adxVal);
      }
    }

    return { adx: adxArr, plusDI, minusDI };
  }

  // Supertrend — trend following with ATR-based bands
  supertrend(highs: number[], lows: number[], closes: number[], period: number = 10, multiplier: number = 3): { supertrend: number[]; direction: number[] } {
    const atrVals = this.atr(highs, lows, closes, period);
    const len = closes.length;
    const st: number[] = [];
    const dir: number[] = [];

    const hl2 = highs.map((h, i) => (h + lows[i]) / 2);
    const upperBand: number[] = [];
    const lowerBand: number[] = [];

    for (let i = 0; i < len; i++) {
      const atr = atrVals[i] || 0;
      upperBand.push(hl2[i] + multiplier * atr);
      lowerBand.push(hl2[i] - multiplier * atr);
      st.push(0);
      dir.push(1);
    }

    for (let i = 1; i < len; i++) {
      if (lowerBand[i] > (st[i - 1] || lowerBand[i]) && closes[i - 1] > (st[i - 1] || 0)) {
        lowerBand[i] = Math.max(lowerBand[i], st[i - 1] || 0);
      }
      if (upperBand[i] < (st[i - 1] || upperBand[i]) && closes[i - 1] < (st[i - 1] || Infinity)) {
        upperBand[i] = Math.min(upperBand[i], st[i - 1] || Infinity);
      }

      if (st[i - 1] === 0) {
        st[i] = closes[i] > upperBand[i] ? lowerBand[i] : upperBand[i];
      } else if (st[i - 1] === (st[i - 1] > 0 ? st[i - 1] : 0) && closes[i - 1] > st[i - 1]) {
        st[i] = lowerBand[i];
      } else {
        st[i] = upperBand[i];
      }

      if (st[i] === lowerBand[i]) dir[i] = 1;
      else dir[i] = -1;
    }

    return { supertrend: st, direction: dir };
  }

  // Stochastic RSI — momentum oscillator
  stochasticRSI(closes: number[], rsiPeriod: number = 14, stochPeriod: number = 14, kSmooth: number = 3, dSmooth: number = 3): { k: number[]; d: number[] } {
    const rsiVals = this.rsi(closes, rsiPeriod);
    const stochK: number[] = [];

    for (let i = 0; i < rsiVals.length; i++) {
      if (i < rsiPeriod + stochPeriod - 1 || isNaN(rsiVals[i])) {
        stochK.push(NaN);
      } else {
        const window = rsiVals.slice(i - stochPeriod + 1, i + 1).filter(v => !isNaN(v));
        if (window.length < 2) { stochK.push(NaN); continue; }
        const min = Math.min(...window);
        const max = Math.max(...window);
        stochK.push(max !== min ? ((rsiVals[i] - min) / (max - min)) * 100 : 50);
      }
    }

    const kSmoothed = this.sma(stochK.filter(v => !isNaN(v)), kSmooth);
    const dSmoothed = this.sma(kSmoothed, dSmooth);

    return { k: kSmoothed, d: dSmoothed };
  }

  // Ichimoku Cloud — trend, support, resistance
  ichimoku(highs: number[], lows: number[], closes: number[], tenkan: number = 9, kijun: number = 26, senkou: number = 52): { tenkanSen: number[]; kijunSen: number[]; senkouA: number[]; senkouB: number[] } {
    const period = Math.max(tenkan, kijun, senkou);
    const tenkanSen: number[] = [];
    const kijunSen: number[] = [];
    const senkouA: number[] = [];
    const senkouB: number[] = [];

    for (let i = 0; i < highs.length; i++) {
      if (i < tenkan - 1) { tenkanSen.push(NaN); kijunSen.push(NaN); senkouA.push(NaN); senkouB.push(NaN); continue; }
      const tenkanHigh = Math.max(...highs.slice(i - tenkan + 1, i + 1));
      const tenkanLow = Math.min(...lows.slice(i - tenkan + 1, i + 1));
      tenkanSen.push((tenkanHigh + tenkanLow) / 2);

      if (i < kijun - 1) { kijunSen.push(NaN); senkouA.push(NaN); senkouB.push(NaN); continue; }
      const kijunHigh = Math.max(...highs.slice(i - kijun + 1, i + 1));
      const kijunLow = Math.min(...lows.slice(i - kijun + 1, i + 1));
      kijunSen.push((kijunHigh + kijunLow) / 2);

      const a = (tenkanSen[i] + kijunSen[i]) / 2;
      senkouA.push(a);

      if (i < senkou - 1) { senkouB.push(NaN); continue; }
      const sHigh = Math.max(...highs.slice(i - senkou + 1, i + 1));
      const sLow = Math.min(...lows.slice(i - senkou + 1, i + 1));
      senkouB.push((sHigh + sLow) / 2);
    }

    return { tenkanSen, kijunSen, senkouA, senkouB };
  }

  // VWMA — Volume Weighted Moving Average
  vwma(data: number[], volumes: number[], period: number): number[] {
    const result: number[] = [];
    for (let i = 0; i < data.length; i++) {
      if (i < period - 1) { result.push(NaN); continue; }
      let pvSum = 0, vSum = 0;
      for (let j = i - period + 1; j <= i; j++) { pvSum += data[j] * volumes[j]; vSum += volumes[j]; }
      result.push(vSum > 0 ? pvSum / vSum : data[i]);
    }
    return result;
  }

  // Institutional multi-factor signal generator
  generateInstitutionalSignal(
    opens: number[], highs: number[], lows: number[], closes: number[], volumes: number[]
  ): {
    signal: 'buy' | 'sell' | 'neutral';
    confidence: number;
    score: number;
    factors: { name: string; value: number; weight: number; direction: 'buy' | 'sell' | 'neutral' }[];
    indicators: Record<string, any>;
  } {
    const latestIdx = closes.length - 1;
    const factors: { name: string; value: number; weight: number; direction: 'buy' | 'sell' | 'neutral' }[] = [];
    let buyScore = 0, sellScore = 0, totalWeight = 0;

    // 1. RSI — Weight: 15%
    const rsiVals = this.rsi(closes, 14);
    const rsi = rsiVals[latestIdx] || 50;
    const rsiW = 0.15;
    const rsiDir = rsi < 30 ? 'buy' as const : rsi > 70 ? 'sell' as const : 'neutral' as const;
    const rsiStrength = rsi < 30 ? (30 - rsi) / 30 : rsi > 70 ? (rsi - 70) / 30 : 0;
    factors.push({ name: 'RSI', value: rsi, weight: rsiW, direction: rsiDir });
    buyScore += rsiDir === 'buy' ? rsiW * rsiStrength : 0;
    sellScore += rsiDir === 'sell' ? rsiW * rsiStrength : 0;
    totalWeight += rsiW;

    // 2. MACD — Weight: 15%
    const macdRes = this.macd(closes);
    const macdVal = macdRes.histogram[latestIdx] || 0;
    const prevHist = macdRes.histogram[latestIdx - 1] || 0;
    const macdW = 0.15;
    const macdDir = macdVal > 0 && macdVal > prevHist ? 'buy' as const : macdVal < 0 && macdVal < prevHist ? 'sell' as const : 'neutral' as const;
    factors.push({ name: 'MACD', value: macdVal, weight: macdW, direction: macdDir });
    buyScore += macdDir === 'buy' ? macdW : 0;
    sellScore += macdDir === 'sell' ? macdW : 0;
    totalWeight += macdW;

    // 3. Bollinger Bands — Weight: 10%
    const bb = this.bollingerBands(closes);
    const bbUp = bb.upper[latestIdx] || closes[latestIdx];
    const bbLow = bb.lower[latestIdx] || closes[latestIdx];
    const bbRange = bbUp - bbLow;
    const bbPos = bbRange > 0 ? (closes[latestIdx] - bbLow) / bbRange : 0.5;
    const bbW = 0.10;
    const bbDir = bbPos < 0.1 ? 'buy' as const : bbPos > 0.9 ? 'sell' as const : 'neutral' as const;
    factors.push({ name: 'BB', value: bbPos, weight: bbW, direction: bbDir });
    buyScore += bbDir === 'buy' ? bbW : 0;
    sellScore += bbDir === 'sell' ? bbW : 0;
    totalWeight += bbW;

    // 4. Supertrend — Weight: 15%
    const stRes = this.supertrend(highs, lows, closes, 10, 3);
    const stDir = stRes.direction[latestIdx] || 1;
    const stW = 0.15;
    const stSignal = stDir === 1 ? 'buy' as const : 'sell' as const;
    factors.push({ name: 'Supertrend', value: stRes.supertrend[latestIdx], weight: stW, direction: stSignal });
    if (stSignal === 'buy') buyScore += stW; else sellScore += stW;
    totalWeight += stW;

    // 5. ADX — Weight: 10%
    const adxRes = this.adx(highs, lows, closes, 14);
    const adxVal = adxRes.adx[adxRes.adx.length - 1] || 0;
    const plusDI = adxRes.plusDI[adxRes.plusDI.length - 1] || 0;
    const minusDI = adxRes.minusDI[adxRes.minusDI.length - 1] || 0;
    const adxW = 0.10;
    const adxDir = adxVal > 25 && plusDI > minusDI ? 'buy' as const : adxVal > 25 && minusDI > plusDI ? 'sell' as const : 'neutral' as const;
    const adxStrength = Math.min(adxVal / 50, 1);
    factors.push({ name: 'ADX', value: adxVal, weight: adxW, direction: adxDir });
    buyScore += adxDir === 'buy' ? adxW * adxStrength : 0;
    sellScore += adxDir === 'sell' ? adxW * adxStrength : 0;
    totalWeight += adxW;

    // 6. OBV trend — Weight: 10%
    const obvVals = this.obv(closes, volumes);
    const obvSMA = this.sma(obvVals, 20);
    const obvTrend = obvVals[latestIdx] > (obvSMA[latestIdx] || 0);
    const obvW = 0.10;
    const obvDir = obvTrend ? 'buy' as const : 'sell' as const;
    factors.push({ name: 'OBV', value: obvVals[latestIdx], weight: obvW, direction: obvDir });
    if (obvDir === 'buy') buyScore += obvW; else sellScore += obvW;
    totalWeight += obvW;

    // 7. VWAP position — Weight: 10%
    const vwapVals = this.vwap(highs, lows, closes, volumes);
    const vwapNow = vwapVals[latestIdx] || closes[latestIdx];
    const vwapW = 0.10;
    const vwapDir = closes[latestIdx] > vwapNow * 1.002 ? 'buy' as const : closes[latestIdx] < vwapNow * 0.998 ? 'sell' as const : 'neutral' as const;
    factors.push({ name: 'VWAP', value: vwapNow, weight: vwapW, direction: vwapDir });
    if (vwapDir === 'buy') buyScore += vwapW; else if (vwapDir === 'sell') sellScore += vwapW;
    totalWeight += vwapW;

    // 8. Ichimoku — Weight: 15%
    const ichi = this.ichimoku(highs, lows, closes);
    const tenkan = ichi.tenkanSen[latestIdx];
    const kijun = ichi.kijunSen[latestIdx];
    const senkouA = ichi.senkouA[latestIdx];
    const senkouB = ichi.senkouB[latestIdx];
    const ichiW = 0.15;
    let ichiDir: 'buy' | 'sell' | 'neutral' = 'neutral';
    if (!isNaN(tenkan) && !isNaN(kijun) && !isNaN(senkouA) && !isNaN(senkouB)) {
      const cloudTop = Math.max(senkouA, senkouB);
      const cloudBot = Math.min(senkouA, senkouB);
      if (closes[latestIdx] > cloudTop && tenkan > kijun) ichiDir = 'buy';
      else if (closes[latestIdx] < cloudBot && tenkan < kijun) ichiDir = 'sell';
    }
    factors.push({ name: 'Ichimoku', value: tenkan || 0, weight: ichiW, direction: ichiDir });
    if (ichiDir === 'buy') buyScore += ichiW; else if (ichiDir === 'sell') sellScore += ichiW;
    totalWeight += ichiW;

    // Final score
    const diff = buyScore - sellScore;
    const absDiff = Math.abs(diff);
    const confidence = totalWeight > 0 ? Math.min(absDiff / (totalWeight * 0.4), 1) : 0;
    const score = diff / totalWeight;

    let signal: 'buy' | 'sell' | 'neutral' = 'neutral';
    if (diff > 0 && confidence > 0.3) signal = 'buy';
    else if (diff < 0 && confidence > 0.3) signal = 'sell';

    return {
      signal,
      confidence,
      score,
      factors,
      indicators: {
        rsi, macd: { value: macdVal, signal: macdRes.signal[latestIdx] || 0, histogram: macdVal },
        bollingerBands: { upper: bbUp, lower: bbLow, middle: bb.middle[latestIdx] || closes[latestIdx] },
        supertrend: { value: stRes.supertrend[latestIdx], direction: stDir },
        adx: { value: adxVal, plusDI, minusDI },
        obv: obvVals[latestIdx],
        vwap: vwapNow,
        ichimoku: { tenkanSen: tenkan, kijunSen: kijun, senkouA, senkouB },
        atr: this.atr(highs, lows, closes)[latestIdx] || 0,
        sma20: this.sma(closes, 20)[latestIdx] || closes[latestIdx],
        sma50: this.sma(closes, 50)[latestIdx] || closes[latestIdx],
      },
    };
  }
}

export const indicatorService = new IndicatorService();
