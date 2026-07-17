import { useEffect, useCallback, useState } from 'react';
import { socketService, ConnectionStatus } from '../services/socket';
import { Signal, MarketTicker } from '../types';
import { marketApi } from '../services/api';

/**
 * Connects to Socket.IO on mount, disconnects on unmount.
 * Returns live connection status.
 */
export function useSocketConnection() {
  const [status, setStatus] = useState<ConnectionStatus>(socketService.status);

  useEffect(() => {
    socketService.connect();
    const unsub = socketService.onStatus(setStatus);
    return () => {
      unsub();
    };
  }, []);

  return status;
}

/**
 * Subscribes to global ticker stream — tickers update in real-time via Socket.IO.
 * Merges incoming real-time data with an initial REST fetch.
 */
export function useRealTimeTickers(initialData: MarketTicker[] = []) {
  const [tickers, setTickers] = useState<Map<string, MarketTicker>>(() => {
    const m = new Map<string, MarketTicker>();
    initialData.forEach((t) => m.set(t.symbol, t));
    return m;
  });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Ensure connection
    socketService.connect();

    const processTicker = (t: any) => ({
      symbol: t.symbol,
      price: parseFloat(t.price ?? t.lastPrice ?? t.c ?? 0),
      volume: parseFloat(t.volume ?? t.v ?? 0),
      quoteVolume: parseFloat(t.quoteVolume ?? t.quoteVol ?? t.q ?? 0) || undefined,
      timestamp: t.timestamp || Date.now(),
      bid: parseFloat(t.bid ?? t.b ?? 0) || undefined,
      ask: parseFloat(t.ask ?? t.a ?? 0) || undefined,
      priceChange: parseFloat(t.priceChange ?? t.p ?? 0) || undefined,
      priceChangePercent: parseFloat(t.priceChangePercent ?? t.P ?? 0) || undefined,
    });

    const handler = (data: any) => {
      setTickers((prev) => {
        const next = new Map(prev);
        if (Array.isArray(data)) {
          for (const t of data) {
            next.set(t.symbol, processTicker(t));
          }
        } else if (data && data.symbol) {
          // Backend emits individual ticker objects from the ticker@arr stream
          next.set(data.symbol, processTicker(data));
        }
        return next;
      });
      setLoading(false);
    };

    socketService.on('ticker', handler);

    // REST fallback: populate tickers immediately while waiting for WS
    marketApi.getTopSymbols(50)
      .then((symbols) => {
        if (Array.isArray(symbols) && symbols.length > 0) {
          setTickers((prev) => {
            // Only seed if we have no WS data yet
            if (prev.size > 0) return prev;
            const next = new Map(prev);
            for (const s of symbols) {
              next.set(s.symbol, {
                symbol: s.symbol,
                price: parseFloat(s.lastPrice || s.price || '0'),
                volume: parseFloat(s.volume || '0'),
                quoteVolume: parseFloat(s.quoteVolume || '0') || undefined,
                timestamp: Date.now(),
                priceChange: parseFloat(s.priceChange || '0') || undefined,
                priceChangePercent: parseFloat(s.priceChangePercent || '0') || undefined,
              } as MarketTicker);
            }
            return next;
          });
          setLoading(false);
        }
      })
      .catch(() => {}); // silent — WS is primary

    return () => {
      socketService.off('ticker', handler);
    };
  }, []);

  const tickerArray = Array.from(tickers.values()).sort(
    (a, b) => (b.quoteVolume || 0) - (a.quoteVolume || 0)
  );

  return { tickers: tickerArray, loading, tickersMap: tickers };
}

/**
 * Real-time signals — receives push from the backend as signals are generated.
 */
export function useRealTimeSignals(initialSignals: Signal[] = []) {
  const [signals, setSignals] = useState<Signal[]>(initialSignals);

  useEffect(() => {
    socketService.connect();

    const onSignal = (signal: Signal) => {
      setSignals((prev) => {
        // Avoid duplicates by id
        const exists = prev.find((s) => s.id === signal.id);
        if (exists) return prev;
        return [signal, ...prev];
      });
    };

    const onSignalUpdate = (updated: Signal) => {
      setSignals((prev) =>
        prev.map((s) => (s.id === updated.id ? { ...s, ...updated } : s))
      );
    };

    const onSignals = (data: Signal[]) => {
      setSignals(data);
    };

    socketService.on('signal', onSignal);
    socketService.on('signal_update', onSignalUpdate);
    socketService.on('signals', onSignals);

    return () => {
      socketService.off('signal', onSignal);
      socketService.off('signal_update', onSignalUpdate);
      socketService.off('signals', onSignals);
    };
  }, []);

  const replaceSignals = useCallback((newSignals: Signal[]) => {
    setSignals(newSignals);
  }, []);

  return { signals, setSignals: replaceSignals };
}

/**
 * Real-time portfolio updates via Socket.IO.
 */
export function useRealTimePortfolio(initialPortfolio: any = null) {
  const [portfolio, setPortfolio] = useState<any>(initialPortfolio);

  useEffect(() => {
    socketService.connect();

    const handler = (data: any) => {
      setPortfolio(data);
    };

    socketService.on('portfolio', handler);

    return () => {
      socketService.off('portfolio', handler);
    };
  }, []);

  return { portfolio, setPortfolio };
}
