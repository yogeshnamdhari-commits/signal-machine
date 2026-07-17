/**
 * Institutional Filters — Score multipliers for signal enhancement
 * Computes: Liquidation Clusters, Absorption, Spoofing, Liquidity Sweeps, Market Regime
 * Each returns a multiplier: >1.0 = boost, <1.0 = penalize, 1.0 = neutral
 */
import axios from 'axios';
import { config } from '../config';
import { logger } from '../utils/logger';

export interface FilterResult {
  name: string;
  multiplier: number;   // 0.5 – 1.5
  direction: 'buy' | 'sell' | 'neutral';
  strength: number;     // 0 – 1
  detail: string;
}

export interface InstitutionalMultipliers {
  liquidation: FilterResult;
  absorption: FilterResult;
  spoofing: FilterResult;
  liquiditySweep: FilterResult;
  marketRegime: FilterResult;
  combinedMultiplier: number;  // product of all multipliers
}

class InstitutionalFilters {
  private baseUrl: string;
  private _orderbookCache: Map<string, { bids: [string, string][]; asks: [string, string][]; ts: number }> = new Map();
  private _tradeCache: Map<string, { price: number; qty: number; isBuyerMaker: boolean; time: number }[]> = new Map();
  private _klineCache: Map<string, { high: number; low: number; close: number; volume: number }[]> = new Map();

  constructor() {
    this.baseUrl = config.binance.testnet
      ? 'https://testnet.binancefuture.com'
      : 'https://fapi.binance.com';
  }

  /** Fetch and cache orderbook */
  private async getOrderbook(symbol: string, depth: number = 20): Promise<{ bids: [string, string][]; asks: [string, string][] }> {
    const cached = this._orderbookCache.get(symbol);
    if (cached && Date.now() - cached.ts < 5000) return cached;

    try {
      const { data } = await axios.get(`${this.baseUrl}/fapi/v1/depth`, {
        params: { symbol, limit: depth },
      });
      const ob = { bids: data.bids, asks: data.asks, ts: Date.now() };
      this._orderbookCache.set(symbol, ob);
      return ob;
    } catch {
      return cached || { bids: [], asks: [] };
    }
  }

  /** Fetch and cache recent aggTrades */
  private async getRecentTrades(symbol: string, limit: number = 500): Promise<{ price: number; qty: number; isBuyerMaker: boolean; time: number }[]> {
    const cached = this._tradeCache.get(symbol);
    if (cached && Date.now() - (cached[0]?.time || 0) < 10000) return cached;

    try {
      const { data } = await axios.get(`${this.baseUrl}/fapi/v1/aggTrades`, {
        params: { symbol, limit },
      });
      const trades = data.map((t: any) => ({
        price: parseFloat(t.p),
        qty: parseFloat(t.q),
        isBuyerMaker: t.m,
        time: t.T,
      }));
      this._tradeCache.set(symbol, trades);
      return trades;
    } catch {
      return cached || [];
    }
  }

  /** Fetch klines for regime detection */
  private async getKlines(symbol: string, interval: string = '1h', limit: number = 50): Promise<{ high: number; low: number; close: number; volume: number }[]> {
    const cacheKey = `${symbol}:${interval}`;
    const cached = this._klineCache.get(cacheKey);
    if (cached && cached.length > 0) return cached;

    try {
      const { data } = await axios.get(`${this.baseUrl}/fapi/v1/klines`, {
        params: { symbol, interval, limit },
      });
      const klines = data.map((k: any[]) => ({
        high: parseFloat(k[2]),
        low: parseFloat(k[3]),
        close: parseFloat(k[4]),
        volume: parseFloat(k[5]),
      }));
      this._klineCache.set(cacheKey, klines);
      return klines;
    } catch {
      return cached || [];
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // 1. LIQUIDATION CLUSTERS
  // Detects price levels with high liquidation density
  // ═══════════════════════════════════════════════════════════════

  async detectLiquidationClusters(symbol: string, currentPrice: number): Promise<FilterResult> {
    try {
      const ob = await this.getOrderbook(symbol, 20);

      // Find clusters of large orders (potential liquidation walls)
      const bidSizes = ob.bids.map(([_, qty]) => parseFloat(qty));
      const askSizes = ob.asks.map(([_, qty]) => parseFloat(qty));
      const avgBid = bidSizes.reduce((a, b) => a + b, 0) / bidSizes.length;
      const avgAsk = askSizes.reduce((a, b) => a + b, 0) / askSizes.length;

      // Large bid wall below = long liquidation cluster → support
      const largeBids = ob.bids.filter(([_, qty]) => parseFloat(qty) > avgBid * 3);
      // Large ask wall above = short liquidation cluster → resistance
      const largeAsks = ob.asks.filter(([_, qty]) => parseFloat(qty) > avgAsk * 3);

      const bidWallStrength = largeBids.reduce((s, [_, q]) => s + parseFloat(q), 0);
      const askWallStrength = largeAsks.reduce((s, [_, q]) => s + parseFloat(q), 0);
      const totalWall = bidWallStrength + askWallStrength;

      if (totalWall === 0) {
        return { name: 'Liquidation', multiplier: 1.0, direction: 'neutral', strength: 0, detail: 'No clusters detected' };
      }

      const bidRatio = bidWallStrength / totalWall;
      const askRatio = askWallStrength / totalWall;

      if (bidRatio > 0.65) {
        // Strong bid wall = support → bullish
        const strength = Math.min((bidRatio - 0.5) * 4, 1);
        return {
          name: 'Liquidation',
          multiplier: 1.0 + strength * 0.2,
          direction: 'buy',
          strength,
          detail: `${largeBids.length} bid walls supporting (${(bidRatio * 100).toFixed(0)}% bid strength)`,
        };
      } else if (askRatio > 0.65) {
        // Strong ask wall = resistance → bearish
        const strength = Math.min((askRatio - 0.5) * 4, 1);
        return {
          name: 'Liquidation',
          multiplier: 1.0 + strength * 0.2,
          direction: 'sell',
          strength,
          detail: `${largeAsks.length} ask walls resisting (${(askRatio * 100).toFixed(0)}% ask strength)`,
        };
      }

      return { name: 'Liquidation', multiplier: 1.0, direction: 'neutral', strength: 0, detail: 'Balanced walls' };
    } catch {
      return { name: 'Liquidation', multiplier: 1.0, direction: 'neutral', strength: 0, detail: 'Data unavailable' };
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // 2. ABSORPTION
  // Detects when large orders absorb aggressive selling/buying
  // Price barely moves despite high volume = institutional absorption
  // ═══════════════════════════════════════════════════════════════

  async detectAbsorption(symbol: string): Promise<FilterResult> {
    try {
      const trades = await this.getRecentTrades(symbol, 300);
      if (trades.length < 50) {
        return { name: 'Absorption', multiplier: 1.0, direction: 'neutral', strength: 0, detail: 'Insufficient data' };
      }

      // Split into windows of 50 trades
      const windowSize = 50;
      const windows: { volDelta: number; priceDelta: number; totalVol: number }[] = [];

      for (let i = 0; i < trades.length - windowSize; i += windowSize) {
        const window = trades.slice(i, i + windowSize);
        const firstPrice = window[0].price;
        const lastPrice = window[windowSize - 1].price;
        const buyVol = window.filter(t => !t.isBuyerMaker).reduce((s, t) => s + t.qty * t.price, 0);
        const sellVol = window.filter(t => t.isBuyerMaker).reduce((s, t) => s + t.qty * t.price, 0);
        windows.push({
          volDelta: buyVol - sellVol,
          priceDelta: lastPrice - firstPrice,
          totalVol: buyVol + sellVol,
        });
      }

      // Absorption = high volume but low price movement
      // If heavy buying but price drops → sell absorption (bullish)
      // If heavy selling but price rises → buy absorption (bearish)
      let absorptionBuyScore = 0;
      let absorptionSellScore = 0;

      for (const w of windows) {
        const priceAbs = Math.abs(w.priceDelta);
        const volNorm = w.totalVol;
        if (volNorm === 0) continue;

        const pricePerVol = priceAbs / volNorm;
        const isHighVol = volNorm > 0;

        // Heavy sell volume but price holds → buy absorption
        if (w.volDelta < 0 && priceAbs < w.priceDelta * -0.3) {
          absorptionBuyScore += Math.abs(w.volDelta) / volNorm;
        }
        // Heavy buy volume but price drops → sell absorption
        if (w.volDelta > 0 && priceAbs < w.priceDelta * 0.3) {
          absorptionSellScore += w.volDelta / volNorm;
        }
      }

      if (absorptionBuyScore > 0.3) {
        const strength = Math.min(absorptionBuyScore, 1);
        return {
          name: 'Absorption',
          multiplier: 1.0 + strength * 0.25,
          direction: 'buy',
          strength,
          detail: `Sell absorption detected (score: ${absorptionBuyScore.toFixed(2)})`,
        };
      } else if (absorptionSellScore > 0.3) {
        const strength = Math.min(absorptionSellScore, 1);
        return {
          name: 'Absorption',
          multiplier: 1.0 + strength * 0.25,
          direction: 'sell',
          strength,
          detail: `Buy absorption detected (score: ${absorptionSellScore.toFixed(2)})`,
        };
      }

      return { name: 'Absorption', multiplier: 1.0, direction: 'neutral', strength: 0, detail: 'No absorption detected' };
    } catch {
      return { name: 'Absorption', multiplier: 1.0, direction: 'neutral', strength: 0, detail: 'Data unavailable' };
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // 3. SPOOFING DETECTION
  // Detects large orders that appear and disappear quickly
  // ═══════════════════════════════════════════════════════════════

  async detectSpoofing(symbol: string): Promise<FilterResult> {
    try {
      const ob = await this.getOrderbook(symbol, 20);
      const trades = await this.getRecentTrades(symbol, 200);

      if (ob.bids.length === 0 || ob.asks.length === 0) {
        return { name: 'Spoofing', multiplier: 1.0, direction: 'neutral', strength: 0, detail: 'No orderbook data' };
      }

      const bidSizes = ob.bids.map(([_, qty]) => parseFloat(qty));
      const askSizes = ob.asks.map(([_, qty]) => parseFloat(qty));
      const avgBid = bidSizes.reduce((a, b) => a + b, 0) / bidSizes.length;
      const avgAsk = askSizes.reduce((a, b) => a + b, 0) / askSizes.length;

      // Find oversized orders (>5x average) — potential spoofs
      const suspiciousBids = ob.bids.filter(([_, qty]) => parseFloat(qty) > avgBid * 5);
      const suspiciousAsks = ob.asks.filter(([_, qty]) => parseFloat(qty) > avgAsk * 5);

      // If large orders are far from mid price → likely spoof
      const bestBid = parseFloat(ob.bids[0][0]);
      const bestAsk = parseFloat(ob.asks[0][0]);
      const midPrice = (bestBid + bestAsk) / 2;

      let spoofBuyScore = 0;
      let spoofSellScore = 0;

      for (const [price, qty] of suspiciousBids) {
        const distFromMid = (midPrice - parseFloat(price)) / midPrice;
        if (distFromMid > 0.002) { // >0.2% from mid
          spoofBuyScore += parseFloat(qty) * distFromMid;
        }
      }

      for (const [price, qty] of suspiciousAsks) {
        const distFromMid = (parseFloat(price) - midPrice) / midPrice;
        if (distFromMid > 0.002) {
          spoofSellScore += parseFloat(qty) * distFromMid;
        }
      }

      // Spoofing in one direction → trade the opposite
      if (spoofBuyScore > 0 && spoofBuyScore > spoofSellScore * 2) {
        const strength = Math.min(spoofBuyScore / 100, 1);
        return {
          name: 'Spoofing',
          multiplier: 1.0 + strength * 0.15,
          direction: 'sell', // Large bid spoof → expect price to drop
          strength,
          detail: `Bid spoofing detected (score: ${spoofBuyScore.toFixed(1)})`,
        };
      } else if (spoofSellScore > 0 && spoofSellScore > spoofBuyScore * 2) {
        const strength = Math.min(spoofSellScore / 100, 1);
        return {
          name: 'Spoofing',
          multiplier: 1.0 + strength * 0.15,
          direction: 'buy', // Large ask spoof → expect price to rise
          strength,
          detail: `Ask spoofing detected (score: ${spoofSellScore.toFixed(1)})`,
        };
      }

      return { name: 'Spoofing', multiplier: 1.0, direction: 'neutral', strength: 0, detail: 'No spoofing detected' };
    } catch {
      return { name: 'Spoofing', multiplier: 1.0, direction: 'neutral', strength: 0, detail: 'Data unavailable' };
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // 4. LIQUIDITY SWEEPS
  // Detects rapid price moves through liquidity zones
  // ═══════════════════════════════════════════════════════════════

  async detectLiquiditySweeps(symbol: string): Promise<FilterResult> {
    try {
      const trades = await this.getRecentTrades(symbol, 500);
      if (trades.length < 100) {
        return { name: 'Sweep', multiplier: 1.0, direction: 'neutral', strength: 0, detail: 'Insufficient data' };
      }

      // Detect rapid price moves with high volume (sweeps)
      const windowSize = 30;
      let sweepBuy = 0;
      let sweepSell = 0;

      for (let i = windowSize; i < trades.length; i++) {
        const window = trades.slice(i - windowSize, i);
        const firstPrice = window[0].price;
        const lastPrice = window[windowSize - 1].price;
        const priceMove = (lastPrice - firstPrice) / firstPrice;
        const totalVol = window.reduce((s, t) => s + t.qty * t.price, 0);
        const buyVol = window.filter(t => !t.isBuyerMaker).reduce((s, t) => s + t.qty * t.price, 0);

        // Rapid upward move with high volume → buy sweep
        if (priceMove > 0.001 && totalVol > 100000) {
          sweepBuy += totalVol * priceMove;
        }
        // Rapid downward move → sell sweep
        if (priceMove < -0.001 && totalVol > 100000) {
          sweepSell += totalVol * Math.abs(priceMove);
        }
      }

      if (sweepBuy > sweepSell * 1.5 && sweepBuy > 1000) {
        const strength = Math.min(sweepBuy / 50000, 1);
        return {
          name: 'Sweep',
          multiplier: 1.0 + strength * 0.2,
          direction: 'buy',
          strength,
          detail: `Buy-side liquidity sweep (strength: ${(strength * 100).toFixed(0)}%)`,
        };
      } else if (sweepSell > sweepBuy * 1.5 && sweepSell > 1000) {
        const strength = Math.min(sweepSell / 50000, 1);
        return {
          name: 'Sweep',
          multiplier: 1.0 + strength * 0.2,
          direction: 'sell',
          strength,
          detail: `Sell-side liquidity sweep (strength: ${(strength * 100).toFixed(0)}%)`,
        };
      }

      return { name: 'Sweep', multiplier: 1.0, direction: 'neutral', strength: 0, detail: 'No sweeps detected' };
    } catch {
      return { name: 'Sweep', multiplier: 1.0, direction: 'neutral', strength: 0, detail: 'Data unavailable' };
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // 5. MARKET REGIME
  // Trending vs Ranging — affects signal confidence
  // ═══════════════════════════════════════════════════════════════

  async detectMarketRegime(symbol: string): Promise<FilterResult> {
    try {
      const klines = await this.getKlines(symbol, '1h', 50);
      if (klines.length < 30) {
        return { name: 'Regime', multiplier: 1.0, direction: 'neutral', strength: 0, detail: 'Insufficient data' };
      }

      const closes = klines.map(k => k.close);
      const highs = klines.map(k => k.high);
      const lows = klines.map(k => k.low);

      // ADX for trend strength
      const period = 14;
      const changes: number[] = [];
      for (let i = 1; i < closes.length; i++) {
        changes.push(Math.abs(closes[i] - closes[i - 1]));
      }
      const avgChange = changes.reduce((a, b) => a + b, 0) / changes.length;
      const totalRange = Math.max(...highs.slice(-20)) - Math.min(...lows.slice(-20));
      const avgPrice = closes.reduce((a, b) => a + b, 0) / closes.length;

      // Trend strength = avg daily move relative to range
      const trendStrength = totalRange > 0 ? (avgChange * 20) / totalRange : 0;

      // Direction from SMA20 vs SMA50
      const sma20 = closes.slice(-20).reduce((a, b) => a + b, 0) / 20;
      const sma50 = closes.slice(-50).reduce((a, b) => a + b, 0) / Math.min(50, closes.length);
      const direction = sma20 > sma50 ? 'buy' as const : sma20 < sma50 ? 'sell' as const : 'neutral' as const;

      // Regime classification
      if (trendStrength > 0.6) {
        // Strong trend → boost signals in trend direction
        const strength = Math.min(trendStrength, 1);
        return {
          name: 'Regime',
          multiplier: 1.0 + strength * 0.3,
          direction,
          strength,
          detail: `Strong ${direction} trend (strength: ${(trendStrength * 100).toFixed(0)}%)`,
        };
      } else if (trendStrength < 0.3) {
        // Ranging market → penalize trend signals
        return {
          name: 'Regime',
          multiplier: 0.8,  // Penalize in ranging market
          direction: 'neutral',
          strength: 1 - trendStrength,
          detail: `Ranging market (volatility: ${(trendStrength * 100).toFixed(0)}%)`,
        };
      }

      return {
        name: 'Regime',
        multiplier: 1.0,
        direction,
        strength: trendStrength,
        detail: `Moderate ${direction} trend`,
      };
    } catch {
      return { name: 'Regime', multiplier: 1.0, direction: 'neutral', strength: 0, detail: 'Data unavailable' };
    }
  }

  // ═══════════════════════════════════════════════════════════════
  // COMBINED — Run all filters, compute combined multiplier
  // ═══════════════════════════════════════════════════════════════

  async analyze(symbol: string, signalDirection: 'buy' | 'sell' | 'neutral', currentPrice: number): Promise<InstitutionalMultipliers> {
    const [liquidation, absorption, spoofing, liquiditySweep, marketRegime] = await Promise.all([
      this.detectLiquidationClusters(symbol, currentPrice),
      this.detectAbsorption(symbol),
      this.detectSpoofing(symbol),
      this.detectLiquiditySweeps(symbol),
      this.detectMarketRegime(symbol),
    ]);

    // Only apply multipliers that align with signal direction
    const filters = [liquidation, absorption, spoofing, liquiditySweep, marketRegime];
    let combinedMultiplier = 1.0;

    for (const f of filters) {
      if (f.direction === signalDirection && f.multiplier > 1.0) {
        // Aligned filter → apply boost
        combinedMultiplier *= f.multiplier;
      } else if (f.direction !== 'neutral' && f.direction !== signalDirection && f.multiplier > 1.0) {
        // Conflicting filter → apply penalty
        combinedMultiplier *= (2 - f.multiplier); // e.g., 1.2 boost becomes 0.8 penalty
      } else {
        // Neutral or ranging → apply as-is
        combinedMultiplier *= f.multiplier;
      }
    }

    // Clamp between 0.5 and 1.5
    combinedMultiplier = Math.max(0.5, Math.min(1.5, combinedMultiplier));

    return {
      liquidation,
      absorption,
      spoofing,
      liquiditySweep,
      marketRegime,
      combinedMultiplier,
    };
  }
}

export const institutionalFilters = new InstitutionalFilters();
