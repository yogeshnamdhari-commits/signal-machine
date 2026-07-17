import { EventEmitter } from 'events';
import { v4 as uuidv4 } from 'uuid';
import { binanceService, Kline } from './binance';
import { indicatorService, OHLCV } from './indicators';
import { institutionalFilters } from './institutionalFilters';
import { logger } from '../utils/logger';

export interface Signal {
  id: string;
  symbol: string;
  type: 'buy' | 'sell';
  confidence: number;
  price: number;
  timestamp: number;
  indicators: Record<string, any>;
  timeframes: string[];
  status: 'active' | 'triggered' | 'expired';
  targetPrice?: number;
  stopLoss?: number;
  takeProfit?: number;
}

export interface ScanConfig {
  symbols?: string[];
  timeframes?: string[];
  minVolume?: number;
  minConfidence?: number;
  maxSignals?: number;
}

class SignalEngine extends EventEmitter {
  private activeSignals: Map<string, Signal> = new Map();
  private scanInterval: NodeJS.Timeout | null = null;
  private isScanning = false;
  private defaultTimeframes = ['1m', '5m', '15m', '1h', '4h'];

  // ── Deduplication: symbol+type → last emitted timestamp ──
  private _cooldownMap: Map<string, number> = new Map();
  private _cooldownMs: number = 300_000; // 5 minutes

  private _cooldownKey(symbol: string, type: string): string {
    return `${symbol}:${type}`;
  }

  private _isDuplicate(symbol: string, type: string): boolean {
    const key = this._cooldownKey(symbol, type);
    const last = this._cooldownMap.get(key);
    if (last && Date.now() - last < this._cooldownMs) {
      return true;
    }
    return false;
  }

  private _recordSignal(symbol: string, type: string): void {
    const key = this._cooldownKey(symbol, type);
    this._cooldownMap.set(key, Date.now());
  }

  async scanMarket(config: ScanConfig = {}): Promise<Signal[]> {
    if (this.isScanning) {
      logger.warn('Market scan already in progress');
      return [];
    }

    this.isScanning = true;
    const signals: Signal[] = [];

    try {
      const symbols = config.symbols || await this.getTopSymbols(config.minVolume);
      const timeframes = config.timeframes || this.defaultTimeframes;

      logger.info(`Scanning ${symbols.length} symbols across ${timeframes.length} timeframes`);

      for (const symbol of symbols) {
        // Track best signal per symbol across timeframes
        let bestSignal: Signal | null = null;

        for (const timeframe of timeframes) {
          try {
            const signal = await this.analyzeSymbol(symbol, timeframe);
            if (signal && (!config.minConfidence || signal.confidence >= config.minConfidence)) {
              // Keep the highest-confidence signal per symbol
              if (!bestSignal || signal.confidence > bestSignal.confidence) {
                bestSignal = signal;
              }

              if (config.maxSignals && signals.length >= config.maxSignals) {
                break;
              }
            }
          } catch (error) {
            logger.error(`Error analyzing ${symbol} ${timeframe}:`, error);
          }
        }

        // Emit only one signal per symbol (best across timeframes), with cooldown
        if (bestSignal) {
          if (!this._isDuplicate(bestSignal.symbol, bestSignal.type)) {
            signals.push(bestSignal);
            this._recordSignal(bestSignal.symbol, bestSignal.type);
          } else {
            logger.debug(`Deduped ${bestSignal.type.toUpperCase()} ${bestSignal.symbol} (cooldown)`);
          }
        }

        if (config.maxSignals && signals.length >= config.maxSignals) {
          break;
        }
      }

      // Sort by confidence
      signals.sort((a, b) => b.confidence - a.confidence);

      // Apply institutional filters ONLY to top 5 signals (rate limit protection)
      const topSignals = signals.slice(0, 5);
      for (const signal of topSignals) {
        try {
          const filters = await institutionalFilters.analyze(signal.symbol, signal.type, signal.price);
          const boosted = Math.min(signal.confidence * filters.combinedMultiplier, 1);
          signal.confidence = boosted;
          signal.indicators.institutional = {
            liquidation: filters.liquidation,
            absorption: filters.absorption,
            spoofing: filters.spoofing,
            liquiditySweep: filters.liquiditySweep,
            marketRegime: filters.marketRegime,
            instMultiplier: filters.combinedMultiplier,
          };
          const alignedFilters = [filters.liquidation, filters.absorption, filters.spoofing, filters.liquiditySweep, filters.marketRegime]
              .filter(f => f.direction === signal.type)
              .map(f => f.name)
              .join('+') || 'none';
          logger.info(`${signal.symbol} institutional mult=${filters.combinedMultiplier.toFixed(2)} (${alignedFilters})`);
        } catch (err) {
          logger.debug('Institutional filters failed for {}: {}', signal.symbol, err);
        }
      }

      // Emit signals
      for (const signal of signals) {
        this.emit('signal', signal);
        this.activeSignals.set(signal.id, signal);
      }

      logger.info(`Found ${signals.length} signals`);
    } catch (error) {
      logger.error('Market scan failed:', error);
    } finally {
      this.isScanning = false;
    }

    return signals;
  }

  private async analyzeSymbol(symbol: string, timeframe: string): Promise<Signal | null> {
    const klines = await binanceService.getKlines(symbol, timeframe, 100);
    if (klines.length < 50) return null;

    const opens = klines.map((k) => parseFloat(k.open));
    const closes = klines.map((k) => parseFloat(k.close));
    const highs = klines.map((k) => parseFloat(k.high));
    const lows = klines.map((k) => parseFloat(k.low));
    const volumes = klines.map((k) => parseFloat(k.volume));

    // Use institutional-grade 8-factor signal generator
    const result = indicatorService.generateInstitutionalSignal(opens, highs, lows, closes, volumes);

    if (result.signal === 'neutral') return null;

    const currentPrice = closes[closes.length - 1];
    const atr = result.indicators.atr;

    // Multi-timeframe confluence bonus (+5% per confirming HTF)
    let tfBonus = 0;
    const higherTFs = ['5m', '15m', '1h', '4h', '1d'];
    const currentTFIdx = higherTFs.indexOf(timeframe);
    for (const htf of higherTFs.slice(currentTFIdx + 1, currentTFIdx + 3)) {
      try {
        const htfKlines = await binanceService.getKlines(symbol, htf, 100);
        if (htfKlines.length < 50) continue;
        const htfRes = indicatorService.generateInstitutionalSignal(
          htfKlines.map(k => parseFloat(k.open)),
          htfKlines.map(k => parseFloat(k.high)),
          htfKlines.map(k => parseFloat(k.low)),
          htfKlines.map(k => parseFloat(k.close)),
          htfKlines.map(k => parseFloat(k.volume)),
        );
        if (htfRes.signal === result.signal) tfBonus += 0.05;
      } catch { /* skip */ }
    }

    const finalConfidence = Math.min(result.confidence + tfBonus, 1);
    const atrVal = atr || currentPrice * 0.01;

    // Dynamic SL/TP with ATR + key levels
    const slDist = Math.max(atrVal * 1.5, currentPrice * 0.008);
    const tpDist = Math.max(atrVal * 3.0, currentPrice * 0.015);

    const signal: Signal = {
      id: uuidv4(),
      symbol,
      type: result.signal,
      confidence: finalConfidence,
      price: currentPrice,
      timestamp: Date.now(),
      indicators: {
        ...result.indicators,
        factors: result.factors,
        score: result.score,
        mtfConfluence: tfBonus > 0,
      },
      timeframes: [timeframe],
      status: 'active',
      targetPrice: result.signal === 'buy' ? currentPrice + tpDist : currentPrice - tpDist,
      stopLoss: result.signal === 'buy' ? currentPrice - slDist : currentPrice + slDist,
      takeProfit: result.signal === 'buy' ? currentPrice + tpDist * 1.5 : currentPrice - tpDist * 1.5,
    };

    return signal;
  }

  private async getTopSymbols(minVolume?: number): Promise<string[]> {
    const tickers = await binanceService.getAllTickerPrices();
    
    let filtered = tickers
      .filter((t) => t.symbol.endsWith('USDT'))
      .sort((a, b) => parseFloat(b.quoteVolume) - parseFloat(a.quoteVolume))
      .slice(0, 50);

    if (minVolume) {
      filtered = filtered.filter((t) => parseFloat(t.quoteVolume) >= minVolume);
    }

    return filtered.map((t) => t.symbol);
  }

  startContinuousScan(intervalMs: number = 60000, config: ScanConfig = {}): void {
    if (this.scanInterval) {
      this.stopContinuousScan();
    }

    logger.info(`Starting continuous scan every ${intervalMs}ms`);
    this.scanInterval = setInterval(async () => {
      await this.scanMarket(config);
    }, intervalMs);

    // Initial scan
    this.scanMarket(config);
  }

  stopContinuousScan(): void {
    if (this.scanInterval) {
      clearInterval(this.scanInterval);
      this.scanInterval = null;
      logger.info('Continuous scan stopped');
    }
  }

  getActiveSignals(): Signal[] {
    return Array.from(this.activeSignals.values());
  }

  updateSignalStatus(signalId: string, status: Signal['status']): boolean {
    const signal = this.activeSignals.get(signalId);
    if (signal) {
      signal.status = status;
      this.emit('signal_update', signal);
      return true;
    }
    return false;
  }

  clearExpiredSignals(maxAgeMs: number = 24 * 60 * 60 * 1000): void {
    const now = Date.now();
    for (const [id, signal] of this.activeSignals) {
      if (now - signal.timestamp > maxAgeMs) {
        this.activeSignals.delete(id);
      }
    }
    // Clean up expired cooldown entries
    for (const [key, ts] of this._cooldownMap) {
      if (now - ts > this._cooldownMs * 2) {
        this._cooldownMap.delete(key);
      }
    }
  }
}

export const signalEngine = new SignalEngine();
