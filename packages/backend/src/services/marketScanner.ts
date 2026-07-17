/**
 * Full Market Scanner — scans ALL USDT perpetual futures in real-time.
 * Fetches: price, open interest, funding rate, volume, exchange flow.
 * Computes entry signals and streams data to frontend via Socket.IO.
 */
import { EventEmitter } from 'events';
import axios from 'axios';
import { config } from '../config';
import { logger } from '../utils/logger';

export interface SymbolSheetData {
  symbol: string;
  price: number;
  // Open Interest
  openInterest: number;
  openInterestChange: number; // % change
  oiDirection: 'buy' | 'sell';
  // Funding Rate
  fundingRate: number;
  fundingDirection: 'buy' | 'sell';
  // Volume
  volume24h: number;
  volumeDirection: 'buy' | 'sell';
  // Exchange Flow (taker buy vs sell)
  exchangeFlow: number; // net taker buy volume
  exchangeFlowDirection: 'in' | 'out';
  // Final Signal
  signal: 'buy' | 'sell' | 'neutral';
  signalConfidence: number;
  timestamp: number;
}

class MarketScanner extends EventEmitter {
  private baseUrl: string;
  private _interval: NodeJS.Timeout | null = null;
  private _lastData: Map<string, SymbolSheetData> = new Map();
  private _prevOI: Map<string, number> = new Map();
  private _prevVolume: Map<string, number> = new Map();
  private _isRunning = false;
  private _symbols: string[] = [];

  constructor() {
    super();
    this.baseUrl = config.binance.testnet
      ? 'https://testnet.binancefuture.com'
      : 'https://fapi.binance.com';
  }

  /** Fetch all USDT-M perpetual symbols */
  async discoverSymbols(): Promise<string[]> {
    try {
      const { data } = await axios.get(`${this.baseUrl}/fapi/v1/exchangeInfo`);
      const symbols = data.symbols
        .filter((s: any) => s.contractType === 'PERPETUAL' && s.symbol.endsWith('USDT'))
        .map((s: any) => s.symbol);
      this._symbols = symbols;
      logger.info(`[Scanner] Discovered ${symbols.length} USDT perpetual symbols`);
      return symbols;
    } catch (error: any) {
      logger.error(`[Scanner] Failed to discover symbols: ${error.message}`);
      return this._symbols.length > 0 ? this._symbols : ['BTCUSDT'];
    }
  }

  /** Fetch 24h ticker for all symbols */
  async fetchAllTickers(): Promise<Map<string, any>> {
    const map = new Map<string, any>();
    try {
      const { data } = await axios.get(`${this.baseUrl}/fapi/v1/ticker/24hr`);
      for (const t of data) {
        if (t.symbol.endsWith('USDT')) {
          map.set(t.symbol, {
            symbol: t.symbol,
            price: parseFloat(t.lastPrice),
            volume: parseFloat(t.quoteVolume),
            priceChangePercent: parseFloat(t.priceChangePercent),
            takerBuyVolume: parseFloat(t.takerBuyQuoteVolume),
            takerSellVolume: parseFloat(t.volume) - parseFloat(t.takerBuyQuoteVolume),
          });
        }
      }
    } catch (error: any) {
      logger.error(`[Scanner] Ticker fetch failed: ${error.message}`);
    }
    return map;
  }

  /** Fetch open interest for a batch of symbols */
  async fetchOpenInterests(symbols: string[]): Promise<Map<string, number>> {
    const map = new Map<string, number>();
    // Fetch in parallel with concurrency limit
    const batch = symbols.slice(0, 50); // Binance rate limit safe batch
    const promises = batch.map(async (symbol) => {
      try {
        const { data } = await axios.get(`${this.baseUrl}/fapi/v1/openInterest`, {
          params: { symbol },
        });
        map.set(symbol, parseFloat(data.openInterest));
      } catch {
        // Skip failed symbols
      }
    });
    await Promise.allSettled(promises);
    return map;
  }

  /** Fetch funding rates for all symbols */
  async fetchFundingRates(): Promise<Map<string, number>> {
    const map = new Map<string, number>();
    try {
      const { data } = await axios.get(`${this.baseUrl}/fapi/v1/premiumIndex`);
      for (const item of data) {
        if (item.symbol.endsWith('USDT')) {
          map.set(item.symbol, parseFloat(item.lastFundingRate));
        }
      }
    } catch (error: any) {
      logger.error(`[Scanner] Funding rate fetch failed: ${error.message}`);
    }
    return map;
  }

  /** Compute signal from all factors */
  computeSignal(data: SymbolSheetData): { signal: 'buy' | 'sell' | 'neutral'; confidence: number } {
    let buyScore = 0;
    let sellScore = 0;
    let totalWeight = 0;

    const weight = {
      oi: 0.25,
      funding: 0.25,
      volume: 0.25,
      flow: 0.25,
    };

    // OI Signal: rising OI + rising price = bullish, rising OI + falling price = bearish
    if (data.oiDirection === 'buy') buyScore += weight.oi;
    else sellScore += weight.oi;
    totalWeight += weight.oi;

    // Funding Signal: extreme negative funding = buy opportunity, extreme positive = sell
    if (data.fundingRate < -0.0001) buyScore += weight.funding;
    else if (data.fundingRate > 0.0001) sellScore += weight.funding;
    totalWeight += weight.funding;

    // Volume Signal: rising volume confirms trend
    if (data.volumeDirection === 'buy') buyScore += weight.volume;
    else sellScore += weight.volume;
    totalWeight += weight.volume;

    // Exchange Flow Signal: net taker buying = bullish
    if (data.exchangeFlowDirection === 'in') buyScore += weight.flow;
    else sellScore += weight.flow;
    totalWeight += weight.flow;

    const confidence = Math.abs(buyScore - sellScore) / totalWeight;

    if (buyScore > sellScore && confidence > 0.1) {
      return { signal: 'buy', confidence: Math.min(confidence, 1) };
    } else if (sellScore > buyScore && confidence > 0.1) {
      return { signal: 'sell', confidence: Math.min(confidence, 1) };
    }
    return { signal: 'neutral', confidence: 0 };
  }

  /** Run a full scan cycle */
  async scanAll(): Promise<SymbolSheetData[]> {
    if (this._isRunning) return Array.from(this._lastData.values());
    this._isRunning = true;

    try {
      // 1. Ensure we have symbols
      if (this._symbols.length === 0) {
        await this.discoverSymbols();
      }

      // 2. Fetch all market data in parallel
      const [tickers, fundingRates] = await Promise.all([
        this.fetchAllTickers(),
        this.fetchFundingRates(),
      ]);

      // 3. Fetch OI in batches to avoid rate limits
      const allSymbols = Array.from(tickers.keys());
      const oiData = await this.fetchOpenInterests(allSymbols);

      // 4. Build sheet data for each symbol
      const results: SymbolSheetData[] = [];

      for (const symbol of allSymbols) {
        const ticker = tickers.get(symbol);
        if (!ticker || ticker.volume < 100000) continue; // Skip low volume

        const currentOI = oiData.get(symbol) || 0;
        const prevOI = this._prevOI.get(symbol) || currentOI;
        const oiChange = prevOI > 0 ? ((currentOI - prevOI) / prevOI) * 100 : 0;

        const currentVol = ticker.volume;
        const prevVol = this._prevVolume.get(symbol) || currentVol;
        const volChange = prevVol > 0 ? ((currentVol - prevVol) / prevVol) * 100 : 0;

        const fundingRate = fundingRates.get(symbol) || 0;

        // Exchange flow: net taker buy vs sell
        const netFlow = ticker.takerBuyVolume - ticker.takerSellVolume;

        const sheetData: SymbolSheetData = {
          symbol,
          price: ticker.price,
          openInterest: currentOI,
          openInterestChange: oiChange,
          oiDirection: ticker.priceChangePercent > 0 ? 'buy' : 'sell',
          fundingRate,
          fundingDirection: fundingRate < 0 ? 'buy' : fundingRate > 0 ? 'sell' : 'buy',
          volume24h: currentVol,
          volumeDirection: volChange > 0 ? 'buy' : 'sell',
          exchangeFlow: netFlow,
          exchangeFlowDirection: netFlow > 0 ? 'in' : 'out',
          signal: 'neutral',
          signalConfidence: 0,
          timestamp: Date.now(),
        };

        // Compute signal
        const { signal, confidence } = this.computeSignal(sheetData);
        sheetData.signal = signal;
        sheetData.signalConfidence = confidence;

        results.push(sheetData);

        // Store for next comparison
        this._prevOI.set(symbol, currentOI);
        this._prevVolume.set(symbol, currentVol);
        this._lastData.set(symbol, sheetData);
      }

      // Sort by signal confidence descending, then by volume
      results.sort((a, b) => {
        if (a.signal !== 'neutral' && b.signal === 'neutral') return -1;
        if (a.signal === 'neutral' && b.signal !== 'neutral') return 1;
        return b.signalConfidence - a.signalConfidence || b.volume24h - a.volume24h;
      });

      logger.info(`[Scanner] Scan complete: ${results.length} symbols, ${results.filter(r => r.signal !== 'neutral').length} with signals`);
      this._isRunning = false;
      return results;
    } catch (error: any) {
      logger.error(`[Scanner] Scan failed: ${error.message}`);
      this._isRunning = false;
      return Array.from(this._lastData.values());
    }
  }

  /** Start continuous scanning */
  start(intervalMs: number = 15000): void {
    if (this._interval) return;
    logger.info(`[Scanner] Starting continuous scan every ${intervalMs}ms`);

    // Run immediately
    this.scanAll().then((data) => {
      this.emit('scan', data);
    });

    this._interval = setInterval(() => {
      this.scanAll().then((data) => {
        this.emit('scan', data);
      });
    }, intervalMs);
  }

  stop(): void {
    if (this._interval) {
      clearInterval(this._interval);
      this._interval = null;
      logger.info('[Scanner] Stopped');
    }
  }

  getLastData(): SymbolSheetData[] {
    return Array.from(this._lastData.values());
  }
}

export const marketScanner = new MarketScanner();
