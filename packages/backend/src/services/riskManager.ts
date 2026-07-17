import { logger } from '../utils/logger';

export interface RiskParameters {
  maxPositionSize: number;      // Maximum position size in USD
  maxLeverage: number;          // Maximum leverage allowed
  maxDailyLoss: number;         // Maximum daily loss in USD
  maxDrawdown: number;          // Maximum drawdown percentage
  riskPerTrade: number;         // Risk per trade as percentage of portfolio
  maxOpenPositions: number;     // Maximum number of open positions
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

class RiskManager {
  private params: RiskParameters;
  private portfolio: Portfolio;
  private tradeHistory: any[] = [];
  private equityCurve: number[] = [];
  private maxEquity: number = 10000;
  private dailyTrades: number = 0;
  private correlationMatrix: Map<string, string[]> = new Map();

  // Kelly Criterion: optimal fraction of bankroll to wager
  private kellyCriterion(winRate: number, avgWin: number, avgLoss: number): number {
    if (avgLoss === 0 || winRate === 0) return 0;
    const W = winRate;
    const R = avgWin / avgLoss;
    const kelly = (W * R - (1 - W)) / R;
    return Math.max(0, Math.min(kelly * 0.5, 0.25)); // Half-Kelly, max 25%
  }

  // Win rate from trade history
  private getWinRate(): number {
    if (this.tradeHistory.length === 0) return 0.5;
    const wins = this.tradeHistory.filter(t => t.pnl > 0).length;
    return wins / this.tradeHistory.length;
  }

  // Average win / average loss
  private getAvgWin(): number {
    const wins = this.tradeHistory.filter(t => t.pnl > 0);
    return wins.length > 0 ? wins.reduce((s, t) => s + t.pnl, 0) / wins.length : 100;
  }

  private getAvgLoss(): number {
    const losses = this.tradeHistory.filter(t => t.pnl < 0);
    return losses.length > 0 ? Math.abs(losses.reduce((s, t) => s + t.pnl, 0) / losses.length) : 100;
  }

  // Sharpe ratio of portfolio
  getSharpeRatio(): number {
    if (this.equityCurve.length < 10) return 0;
    const returns: number[] = [];
    for (let i = 1; i < this.equityCurve.length; i++) {
      returns.push((this.equityCurve[i] - this.equityCurve[i - 1]) / this.equityCurve[i - 1]);
    }
    const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
    const std = Math.sqrt(returns.reduce((s, r) => s + (r - mean) ** 2, 0) / returns.length);
    return std > 0 ? (mean / std) * Math.sqrt(252) : 0; // Annualized
  }

  // Maximum drawdown
  getMaxDrawdown(): number {
    if (this.equityCurve.length < 2) return 0;
    let peak = this.equityCurve[0];
    let maxDD = 0;
    for (const eq of this.equityCurve) {
      peak = Math.max(peak, eq);
      const dd = (peak - eq) / peak * 100;
      maxDD = Math.max(maxDD, dd);
    }
    return maxDD;
  }

  // Position correlation check (simplified: same quote currency)
  private hasHighCorrelation(symbol: string): boolean {
    const base = symbol.replace('USDT', '');
    const highCorr = ['BTC', 'ETH', 'SOL', 'BNB', 'DOGE', 'XRP'];
    const group = highCorr.filter(s => {
      if (s === base) return true;
      return false;
    });
    if (group.length === 0) return false;
    const openInGroup = this.portfolio.positions.filter(p => highCorr.includes(p.symbol.replace('USDT', '')));
    return openInGroup.length >= 2;
  }

  constructor() {
    this.params = {
      maxPositionSize: 10000,
      maxLeverage: 20,
      maxDailyLoss: 500,
      maxDrawdown: 10,
      riskPerTrade: 2,
      maxOpenPositions: 5,
    };

    this.portfolio = {
      balance: 10000,
      equity: 10000,
      unrealizedPnL: 0,
      positions: [],
      dailyPnL: 0,
      peakEquity: 10000,
    };

    this.equityCurve.push(10000);
  }

  updateParams(params: Partial<RiskParameters>): void {
    this.params = { ...this.params, ...params };
    logger.info('Risk parameters updated:', this.params);
  }

  getParams(): RiskParameters {
    return { ...this.params };
  }

  getPortfolio(): Portfolio {
    return { ...this.portfolio };
  }

  // Calculate optimal position size using Kelly + risk-based sizing
  calculateOptimalSize(
    entryPrice: number,
    stopLoss: number,
    confidence: number,
    leverage: number = 1,
  ): { size: number; margin: number; riskAmount: number; method: string } {
    const kelly = this.kellyCriterion(this.getWinRate(), this.getAvgWin(), this.getAvgLoss());
    const riskPct = this.params.riskPerTrade / 100;

    // Blend Kelly with fixed risk, weighted by confidence
    const blendedPct = kelly * confidence + riskPct * (1 - confidence);
    const riskAmount = this.portfolio.equity * Math.min(blendedPct, 0.05);

    const slDistance = Math.abs(entryPrice - stopLoss);
    const slPct = slDistance / entryPrice;
    const quantity = slPct > 0 ? riskAmount / (entryPrice * slPct) : 0;
    const size = quantity * entryPrice;
    const margin = size / leverage;

    return {
      size: Math.min(size, this.params.maxPositionSize),
      margin: Math.min(margin, this.portfolio.balance * 0.9),
      riskAmount,
      method: `Kelly(${(kelly * 100).toFixed(1)}%) × Confidence(${(confidence * 100).toFixed(0)}%)`,
    };
  }

  // Check if position is allowed (institutional-grade checks)
  canOpenPosition(
    symbol: string,
    side: 'LONG' | 'SHORT',
    entryPrice: number,
    quantity: number,
    leverage: number,
    confidence: number = 0.5,
  ): { allowed: boolean; reason?: string; adjustedQuantity?: number; sizing?: any } {
    // 1. Max open positions
    if (this.portfolio.positions.length >= this.params.maxOpenPositions) {
      return { allowed: false, reason: `Max positions (${this.params.maxOpenPositions}) reached` };
    }

    // 2. Daily trade limit
    if (this.dailyTrades >= 20) {
      return { allowed: false, reason: 'Daily trade limit reached (20)' };
    }

    // 3. Leverage check
    if (leverage > this.params.maxLeverage) {
      return { allowed: false, reason: `Leverage ${leverage}x > max ${this.params.maxLeverage}x` };
    }

    // 4. Drawdown check
    const currentDD = this.getMaxDrawdown();
    if (currentDD > this.params.maxDrawdown) {
      return { allowed: false, reason: `Drawdown ${currentDD.toFixed(1)}% > max ${this.params.maxDrawdown}%` };
    }

    // 5. Daily loss limit
    if (Math.abs(this.portfolio.dailyPnL) >= this.params.maxDailyLoss) {
      return { allowed: false, reason: `Daily loss limit $${this.params.maxDailyLoss} hit` };
    }

    // 6. Correlation check
    if (this.hasHighCorrelation(symbol)) {
      return { allowed: false, reason: 'High correlation with existing positions' };
    }

    // 7. Position size check
    const positionValue = entryPrice * quantity;
    if (positionValue > this.params.maxPositionSize) {
      return { allowed: false, reason: `Position $${positionValue.toFixed(0)} > max $${this.params.maxPositionSize}` };
    }

    // 8. Kelly-based sizing
    const sizing = this.calculateOptimalSize(entryPrice, entryPrice * (side === 'LONG' ? 0.99 : 1.01), confidence, leverage);
    const adjustedQuantity = Math.min(quantity, sizing.size / entryPrice);

    // 9. Available balance check
    const marginRequired = (adjustedQuantity * entryPrice) / leverage;
    if (marginRequired > this.portfolio.balance * 0.9) {
      return {
        allowed: false,
        reason: `Margin $${marginRequired.toFixed(0)} > 90% balance`,
        adjustedQuantity: (this.portfolio.balance * 0.9 * leverage) / entryPrice,
      };
    }

    // 10. Risk per trade check
    const maxRisk = this.portfolio.equity * (this.params.riskPerTrade / 100);
    const slDistance = Math.abs(entryPrice - (side === 'LONG' ? entryPrice * 0.992 : entryPrice * 1.008));
    const riskAmount = adjustedQuantity * slDistance;
    if (riskAmount > maxRisk) {
      const safeQty = maxRisk / slDistance;
      return {
        allowed: true,
        adjustedQuantity: safeQty,
        sizing: { ...sizing, size: safeQty * entryPrice },
      };
    }

    return {
      allowed: true,
      adjustedQuantity,
      sizing,
    };
  }

  // Record trade and update equity curve
  recordTrade(trade: any): void {
    this.tradeHistory.push(trade);
    this.portfolio.dailyPnL += trade.pnl || 0;
    this.portfolio.balance += trade.pnl || 0;
    this.portfolio.equity = this.portfolio.balance + this.portfolio.unrealizedPnL;
    this.maxEquity = Math.max(this.maxEquity, this.portfolio.equity);
    this.equityCurve.push(this.portfolio.equity);
    this.dailyTrades++;
    logger.info(`Trade recorded: PnL $${trade.pnl?.toFixed(2)}`);
  }

  getTradeHistory(): any[] {
    return this.tradeHistory;
  }

  calculatePositionSize(entryPrice: number, stopLoss: number, leverage: number = 1) {
    return this.calculateOptimalSize(entryPrice, stopLoss, 0.5, leverage);
  }
}

export const riskManager = new RiskManager();
