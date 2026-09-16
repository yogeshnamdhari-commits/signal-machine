import { useEffect, useCallback, useState } from 'react';
import { socketService, ConnectionStatus } from '../services/socket';
import { Signal, MarketTicker } from '../types';
import { marketApi } from '../services/api';

export function useSocketConnection() {
  const [status, setStatus] = useState<ConnectionStatus>(socketService.status);

  useEffect(() => {
    socketService.connect();
    const unsub = socketService.onStatus(setStatus);
    return () => unsub();
  }, []);

  return status;
}

export function useRealTimeTickers(initialData: MarketTicker[] = []) {
  const [tickers, setTickers] = useState<Map<string, MarketTicker>>(() => {
    const map = new Map<string, MarketTicker>();
    initialData.forEach((ticker) => map.set(ticker.symbol, ticker));
    return map;
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    socketService.connect();

    const processTicker = (ticker: any): MarketTicker => ({
      symbol: ticker.symbol,
      price: Number(ticker.price ?? ticker.lastPrice ?? ticker.c ?? 0),
      volume: Number(ticker.volume ?? ticker.v ?? 0),
      quoteVolume: Number(ticker.quoteVolume ?? ticker.quoteVol ?? ticker.q ?? 0) || undefined,
      timestamp: Number(ticker.timestamp ?? Date.now()),
      bid: Number(ticker.bid ?? ticker.b ?? 0) || undefined,
      ask: Number(ticker.ask ?? ticker.a ?? 0) || undefined,
      priceChange: Number(ticker.priceChange ?? ticker.p ?? 0) || undefined,
      priceChangePercent: Number(ticker.priceChangePercent ?? ticker.P ?? 0) || undefined,
    });

    const handler = (data: any) => {
      setTickers((previous) => {
        const next = new Map(previous);
        if (Array.isArray(data)) data.forEach((ticker) => ticker?.symbol && next.set(ticker.symbol, processTicker(ticker)));
        else if (data?.symbol) next.set(data.symbol, processTicker(data));
        return next;
      });
      setLoading(false);
    };

    socketService.on('ticker', handler);

    marketApi.getTopSymbols(50)
      .then((symbols) => {
        if (!Array.isArray(symbols) || !symbols.length) return;
        setTickers((previous) => {
          if (previous.size > 0) return previous;
          const next = new Map(previous);
          for (const symbol of symbols) {
            next.set(symbol.symbol, {
              symbol: symbol.symbol,
              price: symbol.price ?? 0,
              volume: symbol.volume ?? 0,
              quoteVolume: symbol.quoteVolume,
              timestamp: Date.now(),
              priceChange: symbol.priceChange,
              priceChangePercent: symbol.priceChangePercent,
            });
          }
          return next;
        });
        setLoading(false);
      })
      .catch(() => undefined);

    return () => socketService.off('ticker', handler);
  }, []);

  const tickerArray = Array.from(tickers.values()).sort((a, b) => (b.quoteVolume ?? 0) - (a.quoteVolume ?? 0));
  return { tickers: tickerArray, loading, tickersMap: tickers };
}

export function useRealTimeSignals(initialSignals: Signal[] = []) {
  const [signals, setSignals] = useState<Signal[]>(initialSignals);

  useEffect(() => {
    socketService.connect();
    const onSignal = (signal: Signal) => setSignals((previous) => previous.some((item) => item.id === signal.id) ? previous : [signal, ...previous]);
    const onSignalUpdate = (updated: Signal) => setSignals((previous) => previous.map((item) => item.id === updated.id ? { ...item, ...updated } : item));
    const onSignals = (data: Signal[]) => setSignals(data);
    socketService.on('signal', onSignal);
    socketService.on('signal_update', onSignalUpdate);
    socketService.on('signals', onSignals);
    return () => {
      socketService.off('signal', onSignal);
      socketService.off('signal_update', onSignalUpdate);
      socketService.off('signals', onSignals);
    };
  }, []);

  const replaceSignals = useCallback((newSignals: Signal[]) => setSignals(newSignals), []);
  return { signals, setSignals: replaceSignals };
}

export function useRealTimePortfolio(initialPortfolio: any = null) {
  const [portfolio, setPortfolio] = useState<any>(initialPortfolio);

  useEffect(() => {
    socketService.connect();
    const handler = (data: any) => setPortfolio(data);
    socketService.on('portfolio', handler);
    return () => socketService.off('portfolio', handler);
  }, []);

  return { portfolio, setPortfolio };
}
