/**
 * Trade Simulator — Paper Trading Engine
 * Simulates trades from signals, tracks PnL, calculates performance metrics.
 */
import { EventEmitter } from 'events';
import { v4 as uuidv4 } from 'uuid';
import { binanceService } from './binance';
import { signalEngine, Signal } from './signalEngine';
import { logger } from '../utils/logger';

export interface SimulatedTrade {
  id: string;
  signalId: string;
  symbol: string;
  side: 'LONG' | 'SHORT';
  entryPrice: number;
  stopLoss: number;
  takeProfit: number;
  quantity: number;       // simulated quantity (USD notional)
  riskReward: number;     // R:R ratio
  status: 'OPEN' | 'CLOSED_TP' | 'CLOSED_SL' | 'CLOSED_MANUAL';
  entryTime: number;
  exitTime: number | null;
  exitPrice: number | null;
  pnl: number;            // absolute PnL in USD
  pnlPercent: number;     // % return
  isWin: boolean | null;
  maxFavorable: number;   // max favorable excursion (MFE)
  maxAdverse: number;     // max adverse excursion (MAE)
  closeReason: string;
}

export interface SimulationStats {
  totalTrades: number;
  openTrades: number;
  closedTrades: number;
  wins: number;
  losses: number;
  winRate: number;          // %
  profitFactor: number;     // gross profit / gross loss
  avgRR: number;            // average risk:reward realized
  totalPnL: number;         // cumulative PnL
  avgWin: number;
  avgLoss: number;
  largestWin: number;
  largestLoss: number;
  maxDrawdown: number;      // max drawdown %
  maxDrawdownAbs: number;   // max drawdown in USD
  sharpeRatio: number;
  equityCurve: number[];
  recentTrades: SimulatedTrade[];
}

class TradeSimulator extends EventEmitter {
  private _trades: Map<string, SimulatedTrade> = new Map();
  private _closedTrades: SimulatedTrade[] = [];
  private _equityCurve: number[] = [10000]; // starting balance
  private _startingBalance = 10000;
  private _currentBalance = 10000;
  private _peakBalance = 10000;
  private _maxOpenTrades = 20;
  private _defaultRiskUSD = 100; // risk $100 per trade
  private _pricePollInterval: NodeJS.Timeout | null = null;
  private _isRunning = false;

  constructor() {
    super();
  }

  /** Start listening for signals and monitoring open trades */
  start(): void {
    if (this._isRunning) return;
    this._isRunning = true;

    // Listen for new signals
    signalEngine.on('signal', (signal: Signal) => {
      this._onSignal(signal);
    });

    // Poll prices every 10 seconds to check SL/TP hits
    this._pricePollInterval = setInterval(() => {
      this._checkOpenTrades();
    }, 10_000);

    logger.info('[Simulator] Trade simulator started');
  }

  stop(): void {
    this._isRunning = false;
    if (this._pricePollInterval) {
      clearInterval(this._pricePollInterval);
      this._pricePollInterval = null;
    }
    logger.info('[Simulator] Trade simulator stopped');
  }

  /** Process a new signal → open trade if conditions met */
  private async _onSignal(signal: Signal): Promise<void> {
    try {
      // Don't exceed max open trades
      if (this._trades.size >= this._maxOpenTrades) {
        logger.debug('[Simulator] Max open trades reached, skipping {}', signal.symbol);
        return;
      }

      // Don't open duplicate trades on same symbol+side
      const existingKey = `${signal.symbol}:${signal.type}`;
      for (const t of this._trades.values()) {
        if (`${t.symbol}:${t.side === 'LONG' ? 'buy' : 'sell'}` === existingKey) {
          logger.debug('[Simulator] Already have open {} {}', signal.type, signal.symbol);
          return;
        }
      }

      const entryPrice = signal.price;
      const stopLoss = signal.stopLoss || (signal.type === 'buy'
        ? entryPrice * 0.99   // 1% SL
        : entryPrice * 1.01);
      const takeProfit = signal.targetPrice || signal.takeProfit || (signal.type === 'buy'
        ? entryPrice * 1.02   // 2% TP
        : entryPrice * 0.98);

      // Calculate R:R
      const risk = Math.abs(entryPrice - stopLoss);
      const reward = Math.abs(takeProfit - entryPrice);
      const rr = risk > 0 ? reward / risk : 0;

      // Calculate position size (fixed risk in USD)
      const quantity = risk > 0 ? this._defaultRiskUSD / risk : 0;

      const trade: SimulatedTrade = {
        id: uuidv4(),
        signalId: signal.id,
        symbol: signal.symbol,
        side: signal.type === 'buy' ? 'LONG' : 'SHORT',
        entryPrice,
        stopLoss,
        takeProfit,
        quantity,
        riskReward: rr,
        status: 'OPEN',
        entryTime: Date.now(),
        exitTime: null,
        exitPrice: null,
        pnl: 0,
        pnlPercent: 0,
        isWin: null,
        maxFavorable: 0,
        maxAdverse: 0,
        closeReason: '',
      };

      this._trades.set(trade.id, trade);
      this.emit('trade_opened', trade);
      logger.info(`[Simulator] OPEN ${trade.side} ${trade.symbol} @ ${entryPrice} SL=${stopLoss} TP=${takeProfit} R:R=${rr.toFixed(2)}`);
    } catch (err) {
      logger.error('[Simulator] Error processing signal: {}', err);
    }
  }

  /** Check all open trades against current prices */
  private async _checkOpenTrades(): Promise<void> {
    if (this._trades.size === 0) return;

    // Group trades by symbol to batch price fetches
    const symbolTrades = new Map<string, SimulatedTrade[]>();
    for (const trade of this._trades.values()) {
      const list = symbolTrades.get(trade.symbol) || [];
      list.push(trade);
      symbolTrades.set(trade.symbol, list);
    }

    for (const [symbol, trades] of symbolTrades) {
      try {
        const priceData = await binanceService.getSymbolPrice(symbol);
        const currentPrice = parseFloat(priceData.price);

        for (const trade of trades) {
          this._updateTrade(trade, currentPrice);
        }
      } catch (err) {
        logger.error('[Simulator] Price fetch failed for {}: {}', symbol, err);
      }
    }
  }

  /** Update a single trade with current price — check SL/TP, track MFE/MAE */
  private _updateTrade(trade: SimulatedTrade, currentPrice: number): void {
    const isLong = trade.side === 'LONG';

    // Calculate unrealized PnL
    const priceDiff = isLong
      ? currentPrice - trade.entryPrice
      : trade.entryPrice - currentPrice;
    const unrealizedPnL = priceDiff * trade.quantity;
    const unrealizedPct = (priceDiff / trade.entryPrice) * 100;

    // Track MFE (max favorable) and MAE (max adverse)
    if (unrealizedPnL > trade.maxFavorable) {
      trade.maxFavorable = unrealizedPnL;
    }
    if (unrealizedPnL < -trade.maxAdverse) {
      trade.maxAdverse = Math.abs(unrealizedPnL);
    }

    // Check SL hit
    if (isLong ? currentPrice <= trade.stopLoss : currentPrice >= trade.stopLoss) {
      this._closeTrade(trade, trade.stopLoss, 'CLOSED_SL', 'Stop loss hit');
      return;
    }

    // Check TP hit
    if (isLong ? currentPrice >= trade.takeProfit : currentPrice <= trade.takeProfit) {
      this._closeTrade(trade, trade.takeProfit, 'CLOSED_TP', 'Take profit hit');
      return;
    }
  }

  /** Close a trade and record results */
  private _closeTrade(
    trade: SimulatedTrade,
    exitPrice: number,
    status: SimulatedTrade['status'],
    reason: string
  ): void {
    const isLong = trade.side === 'LONG';
    const priceDiff = isLong
      ? exitPrice - trade.entryPrice
      : trade.entryPrice - exitPrice;

    trade.status = status;
    trade.exitPrice = exitPrice;
    trade.exitTime = Date.now();
    trade.pnl = priceDiff * trade.quantity;
    trade.pnlPercent = (priceDiff / trade.entryPrice) * 100;
    trade.isWin = trade.pnl > 0;
    trade.closeReason = reason;

    // Update balance
    this._currentBalance += trade.pnl;
    if (this._currentBalance > this._peakBalance) {
      this._peakBalance = this._currentBalance;
    }
    this._equityCurve.push(this._currentBalance);

    // Move from open to closed
    this._trades.delete(trade.id);
    this._closedTrades.push(trade);

    this.emit('trade_closed', trade);
    logger.info(`[Simulator] ${trade.isWin ? '✅ WIN' : '❌ LOSS'} ${trade.side} ${trade.symbol} @ ${exitPrice} → PnL=$${trade.pnl.toFixed(2)} (${trade.pnlPercent.toFixed(2)}%) | Reason: ${reason}`);
  }

  // ── Public API ──

  getOpenTrades(): SimulatedTrade[] {
    return Array.from(this._trades.values());
  }

  getClosedTrades(limit: number = 100): SimulatedTrade[] {
    return this._closedTrades.slice(-limit);
  }

  getAllTrades(): SimulatedTrade[] {
    return [...this.getOpenTrades(), ...this._closedTrades];
  }

  getStats(): SimulationStats {
    const closed = this._closedTrades;
    const wins = closed.filter(t => t.isWin);
    const losses = closed.filter(t => !t.isWin);

    const totalPnL = closed.reduce((s, t) => s + t.pnl, 0);
    const grossProfit = wins.reduce((s, t) => s + t.pnl, 0);
    const grossLoss = Math.abs(losses.reduce((s, t) => s + t.pnl, 0));

    const winRate = closed.length > 0 ? (wins.length / closed.length) * 100 : 0;
    const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;
    const avgRR = closed.length > 0
      ? closed.reduce((s, t) => s + t.riskReward, 0) / closed.length
      : 0;
    const avgWin = wins.length > 0 ? grossProfit / wins.length : 0;
    const avgLoss = losses.length > 0 ? grossLoss / losses.length : 0;

    // Max drawdown
    let maxDD = 0;
    let maxDDAbs = 0;
    let peak = this._startingBalance;
    for (const eq of this._equityCurve) {
      if (eq > peak) peak = eq;
      const dd = (peak - eq) / peak * 100;
      const ddAbs = peak - eq;
      if (dd > maxDD) maxDD = dd;
      if (ddAbs > maxDDAbs) maxDDAbs = ddAbs;
    }

    // Sharpe ratio (simplified — daily returns)
    const returns = this._equityCurve.length > 1
      ? this._equityCurve.slice(1).map((v, i) => (v - this._equityCurve[i]) / this._equityCurve[i])
      : [];
    const avgReturn = returns.length > 0 ? returns.reduce((a, b) => a + b, 0) / returns.length : 0;
    const stdReturn = returns.length > 1
      ? Math.sqrt(returns.reduce((s, r) => s + (r - avgReturn) ** 2, 0) / (returns.length - 1))
      : 1;
    const sharpe = stdReturn > 0 ? (avgReturn / stdReturn) * Math.sqrt(252) : 0;

    return {
      totalTrades: closed.length + this._trades.size,
      openTrades: this._trades.size,
      closedTrades: closed.length,
      wins: wins.length,
      losses: losses.length,
      winRate,
      profitFactor,
      avgRR,
      totalPnL,
      avgWin,
      avgLoss,
      largestWin: wins.length > 0 ? Math.max(...wins.map(t => t.pnl)) : 0,
      largestLoss: losses.length > 0 ? Math.min(...losses.map(t => t.pnl)) : 0,
      maxDrawdown: maxDD,
      maxDrawdownAbs: maxDDAbs,
      sharpeRatio: sharpe,
      equityCurve: this._equityCurve.slice(-50),
      recentTrades: closed.slice(-20).reverse(),
    };
  }

  /** Manually close a trade by ID */
  closeTradeManually(tradeId: string): boolean {
    const trade = this._trades.get(tradeId);
    if (!trade) return false;

    // Use last known price (entry for now — will be updated on next tick)
    this._closeTrade(trade, trade.entryPrice, 'CLOSED_MANUAL', 'Manual close');
    return true;
  }

  /** Reset simulation */
  reset(): void {
    this._trades.clear();
    this._closedTrades = [];
    this._currentBalance = this._startingBalance;
    this._peakBalance = this._startingBalance;
    this._equityCurve = [this._startingBalance];
    logger.info('[Simulator] Simulation reset');
  }
}

export const tradeSimulator = new TradeSimulator();
