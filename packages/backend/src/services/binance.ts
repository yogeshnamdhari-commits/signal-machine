import axios, { AxiosInstance } from 'axios';
import crypto from 'crypto';
import { config } from '../config';
import { logger } from '../utils/logger';
import { BinanceAPIError } from '../utils/errors';

export interface BinanceSymbol {
  symbol: string;
  priceChange: string;
  priceChangePercent: string;
  weightedAvgPrice: string;
  prevClosePrice: string;
  lastPrice: string;
  volume: string;
  quoteVolume: string;
  openPrice: string;
  highPrice: string;
  lowPrice: string;
}

export interface OrderBook {
  lastUpdateId: number;
  bids: [string, string][];
  asks: [string, string][];
}

export interface Kline {
  openTime: number;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  closeTime: number;
  quoteVolume: string;
  trades: number;
}

class BinanceService {
  private client: AxiosInstance;
  private baseUrl: string;
  private _lastRequestTime = 0;
  private _minRequestInterval = 100; // 100ms between requests = 600/min max

  constructor() {
    this.baseUrl = config.binance.testnet
      ? 'https://testnet.binancefuture.com'
      : 'https://fapi.binance.com';

    this.client = axios.create({
      baseURL: this.baseUrl,
      timeout: 10000,
      headers: {
        'X-MBX-APIKEY': config.binance.apiKey,
      },
    });

    this.client.interceptors.request.use(async (config) => {
      const now = Date.now();
      const elapsed = now - this._lastRequestTime;
      if (elapsed < this._minRequestInterval) {
        await new Promise(r => setTimeout(r, this._minRequestInterval - elapsed));
      }
      this._lastRequestTime = Date.now();
      return config;
    });

    this.client.interceptors.response.use(
      (response) => response,
      async (error) => {
        if (error.response?.status === 429) {
          logger.warn('Binance rate limited — backing off 5s');
          await new Promise(r => setTimeout(r, 5000));
          // Retry once
          return this.client.request(error.config);
        }
        logger.error('Binance API Error:', error.response?.data || error.message);
        throw new BinanceAPIError(error.response?.data?.msg || 'Binance API request failed');
      }
    );
  }

  private sign(queryString: string): string {
    return crypto
      .createHmac('sha256', config.binance.apiSecret)
      .update(queryString)
      .digest('hex');
  }

  async getExchangeInfo() {
    const { data } = await this.client.get('/fapi/v1/exchangeInfo');
    return data;
  }

  async getAllTickerPrices(): Promise<BinanceSymbol[]> {
    const { data } = await this.client.get('/fapi/v1/ticker/24hr');
    return data;
  }

  async getSymbolPrice(symbol: string): Promise<{ symbol: string; price: string }> {
    const { data } = await this.client.get('/fapi/v1/ticker/price', {
      params: { symbol },
    });
    return data;
  }

  async getOrderBook(symbol: string, limit: number = 20): Promise<OrderBook> {
    const { data } = await this.client.get('/fapi/v1/depth', {
      params: { symbol, limit },
    });
    return data;
  }

  async getKlines(
    symbol: string,
    interval: string = '1h',
    limit: number = 100
  ): Promise<Kline[]> {
    const { data } = await this.client.get('/fapi/v1/klines', {
      params: { symbol, interval, limit },
    });
    
    return data.map((k: any[]) => ({
      openTime: k[0],
      open: k[1],
      high: k[2],
      low: k[3],
      close: k[4],
      volume: k[5],
      closeTime: k[6],
      quoteVolume: k[7],
      trades: k[8],
    }));
  }

  async getFundingRate(symbol: string, limit: number = 10) {
    const { data } = await this.client.get('/fapi/v1/fundingRate', {
      params: { symbol, limit },
    });
    return data;
  }

  async getOpenInterest(symbol: string) {
    const { data } = await this.client.get('/fapi/v1/openInterest', {
      params: { symbol },
    });
    return data;
  }

  async getTopSymbols(limit: number = 20): Promise<BinanceSymbol[]> {
    const tickers = await this.getAllTickerPrices();
    return tickers
      .sort((a, b) => parseFloat(b.quoteVolume) - parseFloat(a.quoteVolume))
      .slice(0, limit);
  }

  async getAggTrades(symbol: string, limit: number = 1000): Promise<any[]> {
    const { data } = await this.client.get('/fapi/v1/aggTrades', {
      params: { symbol, limit },
    });
    return data;
  }
}

export const binanceService = new BinanceService();
