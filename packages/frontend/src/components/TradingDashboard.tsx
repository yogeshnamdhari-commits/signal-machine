import { useCallback, useEffect, useMemo, useState } from 'react';
import { marketApi, signalApi } from '../services/api';
import { Signal } from '../types';
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  Minus,
  RefreshCw,
  Search,
  TrendingDown,
  TrendingUp,
  Zap,
} from 'lucide-react';

interface OrderFlowData {
  symbol: string;
  price: number;
  volume: number;
  takerBuyVol: number;
  takerSellVol: number;
  delta: number;
  cvd: number;
}

interface DashboardRow {
  symbol: string;
  base: string;
  price: number;
  priceChange: number;
  volume: number;
  fundingRate: string;
  openInterest: string;
  signal: Signal | null;
  orderFlow: OrderFlowData | null;
}

const fmtPrice = (value: number) => {
  if (value >= 10_000) return value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (value >= 1) return value.toFixed(4);
  return value.toFixed(6);
};

const fmtUsd = (value: number) => {
  const abs = Math.abs(value);
  const sign = value < 0 ? '-' : value > 0 ? '+' : '';
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(0)}`;
};

export default function TradingDashboard() {
  const [rows, setRows] = useState<DashboardRow[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState<'symbol' | 'volume' | 'delta' | 'confidence'>('volume');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const [lastUpdate, setLastUpdate] = useState<Date>(new Date());

  const fetchData = useCallback(async () => {
    try {
      const [tickersResult, flowResult, signalsResult] = await Promise.allSettled([
        marketApi.getTopSymbols(50),
        marketApi.getOrderFlow(50),
        signalApi.getSignals(),
      ]);

      const tickerList = tickersResult.status === 'fulfilled' ? tickersResult.value : [];
      const flowList = flowResult.status === 'fulfilled' ? flowResult.value : [];
      const signalList = signalsResult.status === 'fulfilled' ? signalsResult.value : [];
      setSignals(signalList);

      const signalMap = new Map(signalList.map((signal) => [signal.symbol, signal]));
      const flowMap = new Map<string, OrderFlowData>();
      for (const flow of flowList as OrderFlowData[]) flowMap.set(flow.symbol, flow);

      const merged: DashboardRow[] = tickerList.map((ticker) => ({
        symbol: ticker.symbol,
        base: ticker.symbol.replace('USDT', ''),
        price: ticker.price ?? 0,
        priceChange: ticker.priceChangePercent ?? 0,
        volume: ticker.quoteVolume ?? ticker.volume ?? 0,
        fundingRate: 'UNAVAILABLE',
        openInterest: 'UNAVAILABLE',
        signal: signalMap.get(ticker.symbol) ?? null,
        orderFlow: flowMap.get(ticker.symbol) ?? null,
      }));

      setRows(merged);
      setLastUpdate(new Date());
    } catch (error) {
      console.error('Dashboard fetch error:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData();
    const interval = setInterval(() => void fetchData(), 15_000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    const output = rows.filter((row) => !q || row.symbol.toLowerCase().includes(q));
    output.sort((a, b) => {
      const av = sortBy === 'symbol' ? a.symbol : sortBy === 'volume' ? a.volume : sortBy === 'delta' ? (a.orderFlow?.delta ?? 0) : (a.signal?.confidence ?? 0);
      const bv = sortBy === 'symbol' ? b.symbol : sortBy === 'volume' ? b.volume : sortBy === 'delta' ? (b.orderFlow?.delta ?? 0) : (b.signal?.confidence ?? 0);
      const cmp = typeof av === 'string' ? av.localeCompare(bv as string) : av - (bv as number);
      return sortDir === 'desc' ? -cmp : cmp;
    });
    return output;
  }, [rows, search, sortBy, sortDir]);

  const handleSort = (column: typeof sortBy) => {
    if (sortBy === column) setSortDir((dir) => (dir === 'desc' ? 'asc' : 'desc'));
    else { setSortBy(column); setSortDir('desc'); }
  };

  const netDelta = rows.reduce((sum, row) => sum + (row.orderFlow?.delta ?? 0), 0);
  const buySignals = signals.filter((signal) => signal.type === 'buy').length;
  const sellSignals = signals.filter((signal) => signal.type === 'sell').length;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <Activity className="h-6 w-6 text-primary-400" />
          <div>
            <h2 className="text-xl font-bold text-white">Trading Dashboard</h2>
            <p className="text-xs text-dark-500">Python engine is the canonical signal authority.</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-dark-500" />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search symbol..." className="w-48 rounded-lg border border-dark-700 bg-dark-800 py-2 pl-9 pr-3 text-sm text-white focus:outline-none focus:border-primary-500" />
          </div>
          <button onClick={() => void fetchData()} className="rounded-lg border border-dark-700 bg-dark-800 p-2 hover:border-primary-500" aria-label="Refresh">
            <RefreshCw className={`h-4 w-4 text-dark-400 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <SummaryCard label="Total Signals" value={String(signals.length)} icon={<Zap className="h-5 w-5 text-primary-400" />} />
        <SummaryCard label="Buy Signals" value={String(buySignals)} color="text-success" icon={<TrendingUp className="h-5 w-5 text-success" />} />
        <SummaryCard label="Sell Signals" value={String(sellSignals)} color="text-danger" icon={<TrendingDown className="h-5 w-5 text-danger" />} />
        <SummaryCard label="Net Delta" value={fmtUsd(netDelta)} color={netDelta >= 0 ? 'text-success' : 'text-danger'} icon={<Activity className="h-5 w-5 text-warning" />} />
      </div>

      <div className="overflow-hidden rounded-xl border border-dark-700 bg-dark-900">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1100px] text-sm">
            <thead className="border-b border-dark-700 text-xs uppercase text-dark-400">
              <tr>
                <Header label="Symbol" column="symbol" sortBy={sortBy} onSort={handleSort} />
                <th className="px-3 py-3 text-right">Price</th>
                <th className="px-3 py-3 text-right">24h %</th>
                <Header label="Volume" column="volume" sortBy={sortBy} onSort={handleSort} />
                <th className="px-3 py-3">Signal</th>
                <Header label="Confidence" column="confidence" sortBy={sortBy} onSort={handleSort} />
                <Header label="Delta" column="delta" sortBy={sortBy} onSort={handleSort} />
                <th className="px-3 py-3 text-right">CVD</th>
                <th className="px-3 py-3 text-right">Agg Buy</th>
                <th className="px-3 py-3 text-right">Agg Sell</th>
                <th className="px-3 py-3">Authority</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => {
                const signal = row.signal;
                const flow = row.orderFlow;
                const isBuy = signal?.type === 'buy';
                const confidence = signal ? Math.round(signal.confidence * 100) : 0;
                return (
                  <tr key={row.symbol} className="border-b border-dark-800 hover:bg-dark-800/50">
                    <td className="px-3 py-3 font-semibold text-white">{row.base}<span className="ml-1 text-xs text-dark-500">/USDT</span></td>
                    <td className="px-3 py-3 text-right font-mono text-white">${fmtPrice(row.price)}</td>
                    <td className={`px-3 py-3 text-right font-mono ${row.priceChange >= 0 ? 'text-success' : 'text-danger'}`}>{row.priceChange >= 0 ? '+' : ''}{row.priceChange.toFixed(2)}%</td>
                    <td className="px-3 py-3 text-right font-mono text-dark-300">{fmtUsd(row.volume)}</td>
                    <td className="px-3 py-3">{signal ? <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-bold ${isBuy ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'}`}>{isBuy ? <ArrowUpRight className="h-3 w-3" /> : <ArrowDownRight className="h-3 w-3" />}{signal.type.toUpperCase()}</span> : <span className="text-xs text-dark-600">NO_SIGNAL</span>}</td>
                    <td className="px-3 py-3 text-right font-mono text-dark-300">{signal ? `${confidence}%` : '—'}</td>
                    <td className="px-3 py-3 text-right font-mono text-dark-300">{flow ? fmtUsd(flow.delta) : 'UNAVAILABLE'}</td>
                    <td className="px-3 py-3 text-right font-mono text-dark-300">{flow ? fmtUsd(flow.cvd) : 'UNAVAILABLE'}</td>
                    <td className="px-3 py-3 text-right font-mono text-success">{flow ? fmtUsd(flow.takerBuyVol) : 'UNAVAILABLE'}</td>
                    <td className="px-3 py-3 text-right font-mono text-danger">{flow ? fmtUsd(flow.takerSellVol) : 'UNAVAILABLE'}</td>
                    <td className="px-3 py-3 text-xs text-dark-400">{signal ? 'Python engine' : 'none'}</td>
                  </tr>
                );
              })}
              {!filtered.length && <tr><td colSpan={11} className="px-3 py-12 text-center text-dark-400">{loading ? 'Loading market data...' : 'No symbols match the current filter.'}</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Header({ label, column, sortBy, onSort }: { label: string; column: 'symbol' | 'volume' | 'delta' | 'confidence'; sortBy: string; onSort: (column: 'symbol' | 'volume' | 'delta' | 'confidence') => void }) {
  return <th onClick={() => onSort(column)} className="cursor-pointer px-3 py-3 text-left hover:text-white">{label}{sortBy === column ? ' ↕' : ''}</th>;
}

function SummaryCard({ label, value, icon, color = 'text-white' }: { label: string; value: string; icon: React.ReactNode; color?: string }) {
  return <div className="rounded-xl border border-dark-700 bg-dark-900 p-4"><div className="flex items-center justify-between"><div><p className="text-xs text-dark-400">{label}</p><p className={`text-xl font-bold ${color}`}>{value}</p></div>{icon}</div></div>;
}
