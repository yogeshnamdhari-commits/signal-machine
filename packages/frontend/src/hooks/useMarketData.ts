import { useState, useEffect, useCallback } from 'react';
import { marketApi } from '../services/api';
import { MarketTicker, KlineData } from '../types';

export function useTickers(refreshInterval: number = 5000) {
  const [tickers, setTickers] = useState<MarketTicker[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchTickers = useCallback(async () => {
    try {
      const data = await marketApi.getTopSymbols(50);
      setTickers(data);
      setError(null);
    } catch (err) {
      setError(err as Error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchTickers();
    const interval = setInterval(fetchTickers, refreshInterval);
    return () => clearInterval(interval);
  }, [fetchTickers, refreshInterval]);

  return { tickers, loading, error, refetch: fetchTickers };
}

export function useKlines(symbol: string, interval: string = '1h', limit: number = 100) {
  const [klines, setKlines] = useState<KlineData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<Error | null>(null);

  const fetchKlines = useCallback(async () => {
    try {
      setLoading(true);
      const data = await marketApi.getKlines(symbol, interval, limit);
      setKlines(data);
      setError(null);
    } catch (err) {
      setError(err as Error);
    } finally {
      setLoading(false);
    }
  }, [symbol, interval, limit]);

  useEffect(() => {
    fetchKlines();
  }, [fetchKlines]);

  return { klines, loading, error, refetch: fetchKlines };
}
