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

export interface MarketTicker {
  symbol: string;
  price: number;
  volume: number;
  quoteVolume?: number;
  timestamp: number;
  bid?: number;
  ask?: number;
  priceChange?: number;
  priceChangePercent?: number;
}

export interface Position {
  id: string;
  symbol: string;
  side: 'LONG' | 'SHORT';
  entryPrice: number;
  quantity: number;
  leverage: number;
  stopLoss: number;
  takeProfit: number;
  timestamp: number;
}

export interface Portfolio {
  balance: number;
  equity: number;
  unrealizedPnL: number;
  positions: Position[];
  dailyPnL: number;
  peakEquity: number;
}

export interface RiskParameters {
  maxPositionSize: number;
  maxLeverage: number;
  maxDailyLoss: number;
  maxDrawdown: number;
  riskPerTrade: number;
  maxOpenPositions: number;
}

export interface KlineData {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}
