import axios from 'axios';
import { Signal, MarketTicker, Portfolio, RiskParameters, KlineData } from '../types';

const api = axios.create({
  baseURL: '/api',
  timeout: 10000,
});

api.interceptors.response.use(
  (response) => response.data,
  (error) => {
    console.error('API Error:', error);
    throw error;
  }
);

export const marketApi = {
  getTickers: async (): Promise<MarketTicker[]> => {
    const { data } = await api.get('/market/tickers');
    return data;
  },

  getTopSymbols: async (limit: number = 20): Promise<MarketTicker[]> => {
    const { data } = await api.get(`/market/top?limit=${limit}`);
    return data;
  },

  getKlines: async (
    symbol: string,
    interval: string = '1h',
    limit: number = 100
  ): Promise<KlineData[]> => {
    const { data } = await api.get(`/market/klines/${symbol}?interval=${interval}&limit=${limit}`);
    return data.map((k: any) => ({
      time: k.openTime / 1000,
      open: parseFloat(k.open),
      high: parseFloat(k.high),
      low: parseFloat(k.low),
      close: parseFloat(k.close),
      volume: parseFloat(k.volume),
    }));
  },

  getFundingRate: async (symbol: string) => {
    const { data } = await api.get(`/market/funding/${symbol}`);
    return data;
  },

  getOpenInterest: async (symbol: string) => {
    const { data } = await api.get(`/market/openinterest/${symbol}`);
    return data;
  },

  getScannerData: async (): Promise<any[]> => {
    const { data } = await api.get('/scanner/data');
    return data;
  },

  triggerScan: async (): Promise<any[]> => {
    const { data } = await api.post('/scanner/scan');
    return data;
  },

  getOrderFlow: async (limit: number = 50): Promise<any[]> => {
    const { data } = await api.get(`/market/orderflow?limit=${limit}`);
    return data;
  },
};

export const signalApi = {
  getSignals: async (): Promise<Signal[]> => {
    const { data } = await api.get('/signals');
    return data;
  },

  scanMarket: async (config?: any): Promise<Signal[]> => {
    const { data } = await api.post('/signals/scan', config);
    return data;
  },

  updateSignalStatus: async (id: string, status: string) => {
    await api.put(`/signals/${id}/status`, { status });
  },
};

export const riskApi = {
  getParams: async (): Promise<RiskParameters> => {
    const { data } = await api.get('/risk/params');
    return data;
  },

  updateParams: async (params: Partial<RiskParameters>) => {
    await api.put('/risk/params', params);
  },

  getPortfolio: async (): Promise<Portfolio> => {
    const { data } = await api.get('/risk/portfolio');
    return data;
  },
};

export const simulatorApi = {
  getStats: async (): Promise<any> => {
    const { data } = await api.get('/simulator/stats');
    return data;
  },

  getTrades: async (limit: number = 100): Promise<any[]> => {
    const { data } = await api.get(`/simulator/trades?limit=${limit}`);
    return data;
  },

  getOpenTrades: async (): Promise<any[]> => {
    const { data } = await api.get('/simulator/open');
    return data;
  },

  reset: async () => {
    await api.post('/simulator/reset');
  },
};

export default api;
