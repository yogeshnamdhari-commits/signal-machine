import { Router, Request, Response, NextFunction } from 'express';
import { binanceService } from '../services/binance';
import { signalEngine } from '../services/signalEngine';
import { riskManager } from '../services/riskManager';
import { indicatorService } from '../services/indicators';
import { marketScanner } from '../services/marketScanner';
import { tradeSimulator } from '../services/tradeSimulator';

const router = Router();

router.get('/', (req: Request, res: Response) => {
  res.json({
    success: true,
    name: 'DeltaTerminal API',
    version: '1.0.0',
    status: 'running',
    timestamp: new Date().toISOString(),
  });
});

router.get('/market/tickers', async (req: Request, res: Response, next: NextFunction) => {
  try { res.json({ success: true, data: await binanceService.getAllTickerPrices() }); } catch (error) { next(error); }
});

router.get('/market/top', async (req: Request, res: Response, next: NextFunction) => {
  try { res.json({ success: true, data: await binanceService.getTopSymbols(parseInt(req.query.limit as string) || 20) }); } catch (error) { next(error); }
});

router.get('/market/price/:symbol', async (req: Request, res: Response, next: NextFunction) => {
  try { res.json({ success: true, data: await binanceService.getSymbolPrice(req.params.symbol) }); } catch (error) { next(error); }
});

router.get('/market/orderbook/:symbol', async (req: Request, res: Response, next: NextFunction) => {
  try { res.json({ success: true, data: await binanceService.getOrderBook(req.params.symbol, parseInt(req.query.limit as string) || 20) }); } catch (error) { next(error); }
});

router.get('/market/klines/:symbol', async (req: Request, res: Response, next: NextFunction) => {
  try { res.json({ success: true, data: await binanceService.getKlines(req.params.symbol, req.query.interval as string || '1h', parseInt(req.query.limit as string) || 100) }); } catch (error) { next(error); }
});

router.get('/market/funding/:symbol', async (req: Request, res: Response, next: NextFunction) => {
  try { res.json({ success: true, data: await binanceService.getFundingRate(req.params.symbol, parseInt(req.query.limit as string) || 10) }); } catch (error) { next(error); }
});

router.get('/market/openinterest/:symbol', async (req: Request, res: Response, next: NextFunction) => {
  try { res.json({ success: true, data: await binanceService.getOpenInterest(req.params.symbol) }); } catch (error) { next(error); }
});

// Only real aggTrades are eligible for order-flow/CVD. Missing data stays unknown.
router.get('/market/orderflow', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const limit = parseInt(req.query.limit as string) || 50;
    const tickers = await binanceService.getTopSymbols(limit);
    const topSymbols = tickers.slice(0, 20);
    const orderflow = await Promise.all(topSymbols.map(async (t: any) => {
      const sym = t.symbol;
      const price = parseFloat(t.lastPrice || '0');
      try {
        const trades = await binanceService.getAggTrades(sym, 1000);
        if (!trades || trades.length === 0) {
          return { symbol: sym, price, volume: parseFloat(t.quoteVolume || '0'), takerBuyVol: null, takerSellVol: null, delta: null, cvd: null, buyRatio: null, priceChangePercent: parseFloat(t.priceChangePercent || '0'), dataQuality: 'UNAVAILABLE', provenance: 'no_aggTrades' };
        }
        let takerBuy = 0;
        let takerSell = 0;
        for (const tr of trades) {
          const qty = parseFloat(tr.q || '0') * parseFloat(tr.p || '0');
          if (tr.m) takerSell += qty; else takerBuy += qty;
        }
        const delta = takerBuy - takerSell;
        const total = takerBuy + takerSell;
        return { symbol: sym, price, volume: parseFloat(t.quoteVolume || '0'), takerBuyVol: takerBuy, takerSellVol: takerSell, delta, cvd: delta, buyRatio: total > 0 ? takerBuy / total : null, priceChangePercent: parseFloat(t.priceChangePercent || '0'), dataQuality: 'REAL', provenance: 'binance_aggTrades' };
      } catch {
        return { symbol: sym, price, volume: parseFloat(t.quoteVolume || '0'), takerBuyVol: null, takerSellVol: null, delta: null, cvd: null, buyRatio: null, priceChangePercent: parseFloat(t.priceChangePercent || '0'), dataQuality: 'UNAVAILABLE', provenance: 'aggTrades_request_failed' };
      }
    }));

    for (const t of tickers.slice(20)) {
      orderflow.push({ symbol: t.symbol, price: parseFloat(t.lastPrice || '0'), volume: parseFloat(t.quoteVolume || '0'), takerBuyVol: null, takerSellVol: null, delta: null, cvd: null, buyRatio: null, priceChangePercent: parseFloat(t.priceChangePercent || '0'), dataQuality: 'UNAVAILABLE', provenance: 'aggTrades_not_requested_for_symbol' });
    }
    res.json({ success: true, data: orderflow });
  } catch (error) { next(error); }
});

router.get('/signals', (req: Request, res: Response) => {
  res.json({ success: true, data: signalEngine.getActiveSignals(), authority: 'python' });
});
router.post('/signals/scan', (req: Request, res: Response) => {
  res.status(409).json({ success: false, error: { code: 'PYTHON_CANONICAL_AUTHORITY', message: 'Executable signals are generated only by the Python engine.' } });
});
router.put('/signals/:id/status', (req: Request, res: Response) => {
  res.status(409).json({ success: false, error: { code: 'PYTHON_CANONICAL_AUTHORITY', message: 'Signal lifecycle is controlled by the Python engine.' } });
});

router.post('/indicators/calculate', (req: Request, res: Response) => {
  const { closes, highs, lows, indicators: requestedIndicators } = req.body;
  if (!closes || !Array.isArray(closes)) return res.status(400).json({ success: false, message: 'Invalid data: closes array required' });
  const results: Record<string, any> = {};
  if (!requestedIndicators || requestedIndicators.includes('rsi')) results.rsi = indicatorService.rsi(closes);
  if (!requestedIndicators || requestedIndicators.includes('macd')) results.macd = indicatorService.macd(closes);
  if (!requestedIndicators || requestedIndicators.includes('bollingerBands')) results.bollingerBands = indicatorService.bollingerBands(closes);
  if (!requestedIndicators || requestedIndicators.includes('sma')) { results.sma20 = indicatorService.sma(closes, 20); results.sma50 = indicatorService.sma(closes, 50); }
  if (!requestedIndicators || requestedIndicators.includes('ema')) { results.ema12 = indicatorService.ema(closes, 12); results.ema26 = indicatorService.ema(closes, 26); }
  if (highs && lows && (!requestedIndicators || requestedIndicators.includes('atr'))) results.atr = indicatorService.atr(highs, lows, closes);
  res.json({ success: true, data: results });
});
router.post('/indicators/signal', (req: Request, res: Response) => {
  res.status(409).json({ success: false, error: { code: 'PYTHON_CANONICAL_AUTHORITY', message: 'Executable signals are not generated by the Node layer.' } });
});

router.get('/risk/params', (req: Request, res: Response) => res.json({ success: true, data: riskManager.getParams(), authority: 'python' }));
router.put('/risk/params', (req: Request, res: Response) => res.status(409).json({ success: false, error: { code: 'PYTHON_CANONICAL_AUTHORITY', message: 'Risk configuration is owned by the Python engine.' } }));
router.get('/risk/portfolio', (req: Request, res: Response) => res.json({ success: true, data: riskManager.getPortfolio(), authority: 'python' }));
router.post('/risk/position/check', (req: Request, res: Response) => res.status(409).json({ success: false, error: { code: 'PYTHON_CANONICAL_AUTHORITY', message: 'Position admission is owned by the Python engine.' } }));
router.post('/risk/position/size', (req: Request, res: Response) => res.status(409).json({ success: false, error: { code: 'PYTHON_CANONICAL_AUTHORITY', message: 'Position sizing is owned by the Python engine.' } }));
router.get('/risk/history', (req: Request, res: Response) => res.json({ success: true, data: riskManager.getTradeHistory(), authority: 'python' }));

router.get('/scanner/data', (req: Request, res: Response) => res.json({ success: true, data: marketScanner.getLastData(), authority: 'python' }));
router.post('/scanner/scan', (req: Request, res: Response) => res.status(409).json({ success: false, error: { code: 'PYTHON_CANONICAL_AUTHORITY', message: 'Executable market scanning is owned by the Python engine.' } }));

router.get('/simulator/stats', (req: Request, res: Response) => res.json({ success: true, data: tradeSimulator.getStats(), authority: 'python' }));
router.get('/simulator/trades', (req: Request, res: Response) => res.json({ success: true, data: tradeSimulator.getClosedTrades(parseInt(req.query.limit as string) || 100), authority: 'python' }));
router.get('/simulator/open', (req: Request, res: Response) => res.json({ success: true, data: tradeSimulator.getOpenTrades(), authority: 'python' }));
router.post('/simulator/reset', (req: Request, res: Response) => res.status(409).json({ success: false, error: { code: 'PYTHON_CANONICAL_AUTHORITY', message: 'Simulator state is not mutable from the Node layer.' } }));

router.get('/health', (req: Request, res: Response) => res.json({ success: true, status: 'healthy', timestamp: new Date().toISOString(), uptime: process.uptime(), authority: 'python' }));

export default router;
