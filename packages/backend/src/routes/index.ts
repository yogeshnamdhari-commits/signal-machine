import { Router, Request, Response, NextFunction } from 'express';
import { binanceService } from '../services/binance';
import { signalEngine, ScanConfig } from '../services/signalEngine';
import { riskManager } from '../services/riskManager';
import { indicatorService } from '../services/indicators';
import { marketScanner } from '../services/marketScanner';
import { tradeSimulator } from '../services/tradeSimulator';
import { logger } from '../utils/logger';
import { AppError } from '../utils/errors';

const router = Router();

// API root — status + available endpoints
router.get('/', (req: Request, res: Response) => {
  res.json({
    success: true,
    name: 'DeltaTerminal API',
    version: '1.0.0',
    status: 'running',
    timestamp: new Date().toISOString(),
    endpoints: [
      'GET  /api/health',
      'GET  /api/market/tickers',
      'GET  /api/market/top?limit=N',
      'GET  /api/market/price/:symbol',
      'GET  /api/market/orderbook/:symbol',
      'GET  /api/market/klines/:symbol?interval=1h&limit=100',
      'GET  /api/market/funding/:symbol',
      'GET  /api/market/openinterest/:symbol',
      'GET  /api/signals',
      'POST /api/signals/scan',
      'GET  /api/risk/params',
      'GET  /api/risk/portfolio',
      'GET  /api/risk/history',
      'GET  /api/simulator/stats',
      'GET  /api/simulator/trades',
      'GET  /api/simulator/open',
      'POST /api/simulator/reset',
    ],
  });
});

// Market Data Routes
router.get('/market/tickers', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const tickers = await binanceService.getAllTickerPrices();
    res.json({ success: true, data: tickers });
  } catch (error) {
    next(error);
  }
});

router.get('/market/top', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const limit = parseInt(req.query.limit as string) || 20;
    const symbols = await binanceService.getTopSymbols(limit);
    res.json({ success: true, data: symbols });
  } catch (error) {
    next(error);
  }
});

router.get('/market/price/:symbol', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const { symbol } = req.params;
    const price = await binanceService.getSymbolPrice(symbol);
    res.json({ success: true, data: price });
  } catch (error) {
    next(error);
  }
});

router.get('/market/orderbook/:symbol', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const { symbol } = req.params;
    const limit = parseInt(req.query.limit as string) || 20;
    const orderbook = await binanceService.getOrderBook(symbol, limit);
    res.json({ success: true, data: orderbook });
  } catch (error) {
    next(error);
  }
});

router.get('/market/klines/:symbol', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const { symbol } = req.params;
    const interval = req.query.interval as string || '1h';
    const limit = parseInt(req.query.limit as string) || 100;
    const klines = await binanceService.getKlines(symbol, interval, limit);
    res.json({ success: true, data: klines });
  } catch (error) {
    next(error);
  }
});

router.get('/market/funding/:symbol', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const { symbol } = req.params;
    const limit = parseInt(req.query.limit as string) || 10;
    const fundingRate = await binanceService.getFundingRate(symbol, limit);
    res.json({ success: true, data: fundingRate });
  } catch (error) {
    next(error);
  }
});

router.get('/market/openinterest/:symbol', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const { symbol } = req.params;
    const openInterest = await binanceService.getOpenInterest(symbol);
    res.json({ success: true, data: openInterest });
  } catch (error) {
    next(error);
  }
});

// Order Flow — computed from recent aggTrades per symbol
router.get('/market/orderflow', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const limit = parseInt(req.query.limit as string) || 50;
    const tickers = await binanceService.getTopSymbols(limit);

    // Fetch recent trades for top symbols in parallel (batch of 10)
    const topSymbols = tickers.slice(0, 20);
    const orderflow = await Promise.all(
      topSymbols.map(async (t: any) => {
        const sym = t.symbol;
        const totalVol = parseFloat(t.quoteVolume || '0');
        const price = parseFloat(t.lastPrice || '0');
        let takerBuy = 0;
        let takerSell = 0;

        try {
          // Fetch last 1000 aggTrades
          const trades = await binanceService.getAggTrades(sym, 1000);
          for (const tr of trades) {
            const qty = parseFloat(tr.q || '0') * parseFloat(tr.p || '0');
            if (tr.m) {
              // m=true means buyer is maker → seller is taker (aggressive sell)
              takerSell += qty;
            } else {
              // m=false → buyer is taker (aggressive buy)
              takerBuy += qty;
            }
          }
        } catch {
          // Fallback: estimate 50/50 split
          takerBuy = totalVol * 0.5;
          takerSell = totalVol * 0.5;
        }

        const delta = takerBuy - takerSell;
        return {
          symbol: sym,
          price,
          volume: totalVol,
          takerBuyVol: takerBuy,
          takerSellVol: takerSell,
          delta,
          cvd: delta,
          buyRatio: (takerBuy + takerSell) > 0 ? takerBuy / (takerBuy + takerSell) : 0.5,
          priceChangePercent: parseFloat(t.priceChangePercent || '0'),
        };
      })
    );

    // Fill remaining symbols with volume-only data
    for (const t of tickers.slice(20)) {
      const vol = parseFloat(t.quoteVolume || '0');
      orderflow.push({
        symbol: t.symbol,
        price: parseFloat(t.lastPrice || '0'),
        volume: vol,
        takerBuyVol: vol * 0.5,
        takerSellVol: vol * 0.5,
        delta: 0,
        cvd: 0,
        buyRatio: 0.5,
        priceChangePercent: parseFloat(t.priceChangePercent || '0'),
      });
    }

    res.json({ success: true, data: orderflow });
  } catch (error) {
    next(error);
  }
});

// Signal Routes
router.get('/signals', (req: Request, res: Response) => {
  const signals = signalEngine.getActiveSignals();
  res.json({ success: true, data: signals });
});

router.post('/signals/scan', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const config: ScanConfig = req.body;
    const signals = await signalEngine.scanMarket(config);
    res.json({ success: true, data: signals, count: signals.length });
  } catch (error) {
    next(error);
  }
});

router.put('/signals/:id/status', (req: Request, res: Response) => {
  const { id } = req.params;
  const { status } = req.body;
  
  const updated = signalEngine.updateSignalStatus(id, status);
  if (updated) {
    res.json({ success: true, message: 'Signal status updated' });
  } else {
    res.status(404).json({ success: false, message: 'Signal not found' });
  }
});

// Indicators Routes
router.post('/indicators/calculate', (req: Request, res: Response) => {
  const { closes, highs, lows, volumes, indicators: requestedIndicators } = req.body;

  if (!closes || !Array.isArray(closes)) {
    return res.status(400).json({ success: false, message: 'Invalid data: closes array required' });
  }

  const results: Record<string, any> = {};

  if (!requestedIndicators || requestedIndicators.includes('rsi')) {
    results.rsi = indicatorService.rsi(closes);
  }
  if (!requestedIndicators || requestedIndicators.includes('macd')) {
    results.macd = indicatorService.macd(closes);
  }
  if (!requestedIndicators || requestedIndicators.includes('bollingerBands')) {
    results.bollingerBands = indicatorService.bollingerBands(closes);
  }
  if (!requestedIndicators || requestedIndicators.includes('sma')) {
    results.sma20 = indicatorService.sma(closes, 20);
    results.sma50 = indicatorService.sma(closes, 50);
  }
  if (!requestedIndicators || requestedIndicators.includes('ema')) {
    results.ema12 = indicatorService.ema(closes, 12);
    results.ema26 = indicatorService.ema(closes, 26);
  }
  if (highs && lows && closes && (!requestedIndicators || requestedIndicators.includes('atr'))) {
    results.atr = indicatorService.atr(highs, lows, closes);
  }

  res.json({ success: true, data: results });
});

router.post('/indicators/signal', (req: Request, res: Response) => {
  const { closes, highs, lows, volumes } = req.body;

  if (!closes || !highs || !lows) {
    return res.status(400).json({ success: false, message: 'closes, highs, and lows arrays required' });
  }

  const signal = indicatorService.generateSignal(closes, highs, lows, volumes || []);
  res.json({ success: true, data: signal });
});

// Risk Management Routes
router.get('/risk/params', (req: Request, res: Response) => {
  const params = riskManager.getParams();
  res.json({ success: true, data: params });
});

router.put('/risk/params', (req: Request, res: Response) => {
  riskManager.updateParams(req.body);
  res.json({ success: true, message: 'Risk parameters updated' });
});

router.get('/risk/portfolio', (req: Request, res: Response) => {
  const portfolio = riskManager.getPortfolio();
  res.json({ success: true, data: portfolio });
});

router.post('/risk/position/check', (req: Request, res: Response) => {
  const { symbol, side, entryPrice, quantity, leverage } = req.body;
  const result = riskManager.canOpenPosition(symbol, side, entryPrice, quantity, leverage);
  res.json({ success: true, data: result });
});

router.post('/risk/position/size', (req: Request, res: Response) => {
  const { entryPrice, stopLoss, leverage } = req.body;
  const result = riskManager.calculatePositionSize(entryPrice, stopLoss, leverage);
  res.json({ success: true, data: result });
});

router.get('/risk/history', (req: Request, res: Response) => {
  const history = riskManager.getTradeHistory();
  res.json({ success: true, data: history });
});

// Full Market Scanner Routes
router.get('/scanner/data', (req: Request, res: Response) => {
  const data = marketScanner.getLastData();
  res.json({ success: true, data });
});

router.post('/scanner/scan', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const data = await marketScanner.scanAll();
    res.json({ success: true, data, count: data.length });
  } catch (error) {
    next(error);
  }
});

// ── Trade Simulator Routes ──

router.get('/simulator/stats', (req: Request, res: Response) => {
  const stats = tradeSimulator.getStats();
  res.json({ success: true, data: stats });
});

router.get('/simulator/trades', (req: Request, res: Response) => {
  const limit = parseInt(req.query.limit as string) || 100;
  const trades = tradeSimulator.getClosedTrades(limit);
  res.json({ success: true, data: trades, count: trades.length });
});

router.get('/simulator/open', (req: Request, res: Response) => {
  const trades = tradeSimulator.getOpenTrades();
  res.json({ success: true, data: trades, count: trades.length });
});

router.post('/simulator/reset', (req: Request, res: Response) => {
  tradeSimulator.reset();
  res.json({ success: true, message: 'Simulation reset' });
});

// Health check
router.get('/health', (req: Request, res: Response) => {
  res.json({ 
    success: true, 
    status: 'healthy',
    timestamp: new Date().toISOString(),
    uptime: process.uptime(),
  });
});

export default router;
