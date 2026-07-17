import WebSocket from 'ws';
import { EventEmitter } from 'events';
import { config } from '../config';
import { logger } from '../utils/logger';

export interface MarketData {
  symbol: string;
  price: number;
  volume: number;
  quoteVolume?: number;
  timestamp: number;
  bid?: number;
  ask?: number;
}

export interface KlineData {
  symbol: string;
  interval: string;
  openTime: number;
  closeTime: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  trades: number;
}

class WebSocketService extends EventEmitter {
  private ws: WebSocket | null = null;
  private subscriptions: Map<string, Set<string>> = new Map();
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 10;
  private reconnectTimeout: NodeJS.Timeout | null = null;
  private pingTimeout: NodeJS.Timeout | null = null;
  private isAlive = false;

  constructor() {
    super();
    this.setMaxListeners(50);
  }

  connect(streams: string[] = ['!ticker@arr']): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      logger.warn('WebSocket already connected');
      return;
    }

    const streamNames = streams.join('/');
    const url = `${config.binance.wsUrl}/stream?streams=${streamNames}`;
    
    logger.info(`Connecting to Binance WebSocket: ${url}`);
    this.ws = new WebSocket(url);

    this.ws.on('open', () => {
      logger.info('WebSocket connected');
      this.isAlive = true;
      this.reconnectAttempts = 0;
      this.startPing();
      this.emit('connected');
    });

    this.ws.on('message', (data: Buffer) => {
      try {
        const message = JSON.parse(data.toString());
        this.handleMessage(message);
      } catch (error) {
        logger.error('Failed to parse WebSocket message:', error);
      }
    });

    this.ws.on('close', (code: number, reason: Buffer) => {
      logger.warn(`WebSocket closed: ${code} - ${reason.toString()}`);
      this.isAlive = false;
      this.stopPing();
      this.emit('disconnected');
      this.attemptReconnect();
    });

    this.ws.on('error', (error: Error) => {
      logger.error('WebSocket error:', error.message);
      this.emit('error', error);
    });

    this.ws.on('pong', () => {
      this.isAlive = true;
    });
  }

  private startPing(): void {
    this.pingTimeout = setInterval(() => {
      if (!this.isAlive) {
        logger.warn('WebSocket heartbeat failed, reconnecting...');
        this.ws?.terminate();
        return;
      }
      this.isAlive = false;
      this.ws?.ping();
    }, config.websocket.pingInterval);
  }

  private stopPing(): void {
    if (this.pingTimeout) {
      clearInterval(this.pingTimeout);
      this.pingTimeout = null;
    }
  }

  private attemptReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      logger.error('Max reconnection attempts reached');
      this.emit('reconnect_failed');
      return;
    }

    const delay = Math.min(1000 * Math.pow(2, this.reconnectAttempts), 30000);
    logger.info(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts + 1})`);

    this.reconnectTimeout = setTimeout(() => {
      this.reconnectAttempts++;
      this.connect();
    }, delay);
  }

  private handleMessage(message: any): void {
    const stream = message.stream;
    const data = message.data;

    if (stream.includes('!ticker@arr')) {
      // All market tickers
      for (const ticker of data) {
        this.emit('ticker', {
          symbol: ticker.s,
          price: parseFloat(ticker.c),
          volume: parseFloat(ticker.v),
          quoteVolume: parseFloat(ticker.q),
          timestamp: Date.now(),
          bid: parseFloat(ticker.b),
          ask: parseFloat(ticker.a),
          priceChange: parseFloat(ticker.p),
          priceChangePercent: parseFloat(ticker.P),
        });
      }
    } else if (stream.includes('@kline')) {
      // Kline/candlestick data
      const kline = data.k;
      this.emit('kline', {
        symbol: kline.s,
        interval: kline.i,
        openTime: kline.t,
        closeTime: kline.T,
        open: parseFloat(kline.o),
        high: parseFloat(kline.h),
        low: parseFloat(kline.l),
        close: parseFloat(kline.c),
        volume: parseFloat(kline.v),
        trades: kline.n,
        isClosed: kline.x,
      });
    } else if (stream.includes('@depth')) {
      // Order book
      this.emit('depth', {
        symbol: data.s,
        bids: data.b,
        asks: data.a,
        timestamp: Date.now(),
      });
    } else if (stream.includes('@trade')) {
      // Trade data
      this.emit('trade', {
        symbol: data.s,
        price: parseFloat(data.p),
        quantity: parseFloat(data.q),
        time: data.T,
        isBuyerMaker: data.m,
      });
    }
  }

  subscribe(symbol: string, streams: string[]): void {
    if (!this.subscriptions.has(symbol)) {
      this.subscriptions.set(symbol, new Set());
    }

    const streamsWithSymbol = streams.map((s) => `${symbol.toLowerCase()}${s}`);
    streamsWithSymbol.forEach((s) => this.subscriptions.get(symbol)?.add(s));

    // Reconnect with new subscriptions if already connected
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.disconnect();
      this.connect(Array.from(this.subscriptions.values()).flatMap((s) => Array.from(s)));
    }
  }

  unsubscribe(symbol: string, streams: string[]): void {
    if (!this.subscriptions.has(symbol)) return;

    streams.forEach((s) => {
      const streamName = `${symbol.toLowerCase()}${s}`;
      this.subscriptions.get(symbol)?.delete(streamName);
    });

    if (this.subscriptions.get(symbol)?.size === 0) {
      this.subscriptions.delete(symbol);
    }
  }

  disconnect(): void {
    this.stopPing();
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
    if (this.ws) {
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
    }
  }

  getSubscriptions(): Map<string, Set<string>> {
    return new Map(this.subscriptions);
  }
}

export const websocketService = new WebSocketService();
