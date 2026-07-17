import { useState, useEffect, useCallback } from 'react';
import { marketApi, signalApi } from '../services/api';
import { Signal } from '../types';
import {
  TrendingUp,
  TrendingDown,
  Activity,
  Zap,
  RefreshCw,
  ArrowUpRight,
  ArrowDownRight,
  Minus,
  Search,
} from 'lucide-react';

interface OrderFlowData {
  symbol: string;
  price: number;
  volume: number;
  takerBuyVol: number;
  takerSellVol: number;
  delta: number;
  cvd: number;
  buyRatio: number;
  priceChangePercent: number;
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
      const [tickers, orderflow, signalsData] = await Promise.allSettled([
        marketApi.getTopSymbols(50),
        marketApi.getOrderFlow(50),
        signalApi.getSignals(),
      ]);

      const tickerList = tickers.status === 'fulfilled' ? tickers.value : [];
      const orderflowList = orderflow.status === 'fulfilled' ? orderflow.value : [];
      const signalList = signalsData.status === 'fulfilled' ? signalsData.value : [];

      setSignals(signalList);

      // Build signal map (latest per symbol)
      const signalMap = new Map<string, Signal>();
      for (const s of signalList) {
        const existing = signalMap.get(s.symbol);
        if (!existing || s.timestamp > existing.timestamp) {
          signalMap.set(s.symbol, s);
        }
      }

      // Build order flow map
      const ofMap = new Map<string, OrderFlowData>();
      for (const of of orderflowList) {
        ofMap.set(of.symbol, of);
      }

      // Merge data
      const merged: DashboardRow[] = tickerList.map((t: any) => {
        const sym = t.symbol;
        return {
          symbol: sym,
          base: sym.replace('USDT', ''),
          price: parseFloat(t.lastPrice || t.price || '0'),
          priceChange: parseFloat(t.priceChangePercent || '0'),
          volume: parseFloat(t.quoteVolume || '0'),
          fundingRate: '—',
          openInterest: '—',
          signal: signalMap.get(sym) || null,
          orderFlow: ofMap.get(sym) || null,
        };
      });

      setRows(merged);
      setLastUpdate(new Date());
    } catch (err) {
      console.error('Dashboard fetch error:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 15000);
    return () => clearInterval(interval);
  }, [fetchData]);

  // ── Sorting & Filtering ──
  const filtered = rows
    .filter((r) => !search || r.symbol.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      let cmp = 0;
      switch (sortBy) {
        case 'symbol':
          cmp = a.symbol.localeCompare(b.symbol);
          break;
        case 'volume':
          cmp = a.volume - b.volume;
          break;
        case 'delta':
          cmp = (a.orderFlow?.delta || 0) - (b.orderFlow?.delta || 0);
          break;
        case 'confidence':
          cmp = (a.signal?.confidence || 0) - (b.signal?.confidence || 0);
          break;
      }
      return sortDir === 'desc' ? -cmp : cmp;
    });

  const handleSort = (col: typeof sortBy) => {
    if (sortBy === col) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      setSortBy(col);
      setSortDir('desc');
    }
  };

  // ── Formatters ──
  const fmtPrice = (p: number) => {
    if (p >= 10000) return p.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (p >= 1) return p.toFixed(4);
    return p.toFixed(6);
  };

  const fmtVol = (v: number) => {
    if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
    if (v >= 1e6) return `$${(v / 1e6).toFixed(1)}M`;
    if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
    return `$${v.toFixed(0)}`;
  };

  const fmtDelta = (d: number) => {
    const abs = Math.abs(d);
    let str: string;
    if (abs >= 1e9) str = `$${(abs / 1e9).toFixed(2)}B`;
    else if (abs >= 1e6) str = `$${(abs / 1e6).toFixed(1)}M`;
    else if (abs >= 1e3) str = `$${(abs / 1e3).toFixed(1)}K`;
    else str = `$${abs.toFixed(0)}`;
    return d >= 0 ? `+${str}` : `-${str}`;
  };

  const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

  const SortHeader = ({ col, label }: { col: typeof sortBy; label: string }) => (
    <th
      onClick={() => handleSort(col)}
      className="text-right py-3 px-3 cursor-pointer hover:text-white transition-colors select-none"
    >
      {label}
      {sortBy === col && (
        <span className="ml-1 text-primary-400">{sortDir === 'desc' ? '↓' : '↑'}</span>
      )}
    </th>
  );

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="w-8 h-8 text-primary-400 animate-spin" />
        <span className="ml-3 text-dark-400">Loading market data...</span>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* ── Header ── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <Activity className="w-6 h-6 text-primary-400" />
          <h2 className="text-xl font-bold text-white">Trading Dashboard</h2>
          <span className="text-xs text-dark-500 bg-dark-800 px-2 py-1 rounded-full">
            {filtered.length} symbols
          </span>
        </div>
        <div className="flex items-center space-x-3">
          <span className="text-xs text-dark-500">
            Updated {lastUpdate.toLocaleTimeString()}
          </span>
          <div className="relative">
            <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-dark-500" />
            <input
              type="text"
              placeholder="Search symbol..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="bg-dark-800 border border-dark-700 rounded-lg pl-9 pr-4 py-2 text-sm text-white placeholder-dark-500 focus:outline-none focus:border-primary-500 w-48"
            />
          </div>
          <button
            onClick={fetchData}
            className="p-2 rounded-lg bg-dark-800 border border-dark-700 hover:border-primary-500 transition-colors"
          >
            <RefreshCw className="w-4 h-4 text-dark-400" />
          </button>
        </div>
      </div>

      {/* ── Summary Cards ── */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <SummaryCard
          label="Total Signals"
          value={signals.length.toString()}
          icon={<Zap className="w-5 h-5 text-primary-400" />}
        />
        <SummaryCard
          label="Buy Signals"
          value={signals.filter((s) => s.type === 'buy').length.toString()}
          icon={<TrendingUp className="w-5 h-5 text-success" />}
          color="text-success"
        />
        <SummaryCard
          label="Sell Signals"
          value={signals.filter((s) => s.type === 'sell').length.toString()}
          icon={<TrendingDown className="w-5 h-5 text-danger" />}
          color="text-danger"
        />
        <SummaryCard
          label="Net Delta"
          value={fmtDelta(rows.reduce((sum, r) => sum + (r.orderFlow?.delta || 0), 0))}
          icon={<Activity className="w-5 h-5 text-warning" />}
          color={rows.reduce((sum, r) => sum + (r.orderFlow?.delta || 0), 0) >= 0 ? 'text-success' : 'text-danger'}
        />
      </div>

      {/* ── Main Table ── */}
      <div className="bg-dark-900 rounded-xl border border-dark-700 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1200px]">
            <thead>
              <tr className="text-dark-400 text-xs uppercase border-b border-dark-700 bg-dark-900/50">
                <SortHeader col="symbol" label="Symbol" />
                <th className="text-right py-3 px-3">Price</th>
                <th className="text-right py-3 px-3">24h %</th>
                <SortHeader col="volume" label="Volume" />
                <th className="text-right py-3 px-3">Funding</th>
                <th className="text-right py-3 px-3">OI</th>
                <th className="text-center py-3 px-3">Signal</th>
                <SortHeader col="confidence" label="Confidence" />
                <th className="text-center py-3 px-3">Inst. Score</th>
                <SortHeader col="delta" label="Delta" />
                <th className="text-right py-3 px-3">CVD</th>
                <th className="text-right py-3 px-3">Agg Buy</th>
                <th className="text-right py-3 px-3">Agg Sell</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => {
                const sig = row.signal;
                const of = row.orderFlow;
                const isBuy = sig?.type === 'buy';
                const confPct = sig ? Math.round(sig.confidence * 100) : 0;
                const instScore = sig?.indicators?.score ?? sig?.indicators?.factors?.length ?? null;
                const deltaPositive = (of?.delta || 0) >= 0;

                return (
                  <tr
                    key={row.symbol}
                    className="border-b border-dark-800 hover:bg-dark-800/50 transition-colors"
                  >
                    {/* Symbol */}
                    <td className="py-3 px-3">
                      <div className="flex items-center space-x-2">
                        <div className={`w-2 h-2 rounded-full ${sig ? (isBuy ? 'bg-success' : 'bg-danger') : 'bg-dark-600'}`} />
                        <span className="font-semibold text-white">{row.base}</span>
                        <span className="text-dark-500 text-xs">/USDT</span>
                      </div>
                    </td>

                    {/* Price */}
                    <td className="text-right py-3 px-3 font-mono text-white text-sm">
                      ${fmtPrice(row.price)}
                    </td>

                    {/* 24h Change */}
                    <td className="text-right py-3 px-3">
                      <span className={`text-sm font-medium ${row.priceChange >= 0 ? 'text-success' : 'text-danger'}`}>
                        {row.priceChange >= 0 ? '+' : ''}{row.priceChange.toFixed(2)}%
                      </span>
                    </td>

                    {/* Volume */}
                    <td className="text-right py-3 px-3 text-sm text-dark-300 font-mono">
                      {fmtVol(row.volume)}
                    </td>

                    {/* Funding Rate */}
                    <td className="text-right py-3 px-3 text-sm text-dark-400">
                      {row.fundingRate}
                    </td>

                    {/* Open Interest */}
                    <td className="text-right py-3 px-3 text-sm text-dark-400">
                      {row.openInterest}
                    </td>

                    {/* Signal */}
                    <td className="text-center py-3 px-3">
                      {sig ? (
                        <span
                          className={`inline-flex items-center space-x-1 px-2.5 py-1 rounded-full text-xs font-bold ${
                            isBuy
                              ? 'bg-success/20 text-success border border-success/30'
                              : 'bg-danger/20 text-danger border border-danger/30'
                          }`}
                        >
                          {isBuy ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                          <span>{sig.type.toUpperCase()}</span>
                        </span>
                      ) : (
                        <span className="text-dark-600 text-xs flex items-center justify-center">
                          <Minus className="w-3 h-3 mr-1" /> —
                        </span>
                      )}
                    </td>

                    {/* Confidence */}
                    <td className="text-right py-3 px-3">
                      {sig ? (
                        <div className="flex items-center justify-end space-x-2">
                          <div className="w-16 h-1.5 bg-dark-700 rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${
                                confPct >= 70 ? 'bg-success' : confPct >= 40 ? 'bg-warning' : 'bg-danger'
                              }`}
                              style={{ width: `${confPct}%` }}
                            />
                          </div>
                          <span className="text-xs text-dark-300 w-8 text-right">{confPct}%</span>
                        </div>
                      ) : (
                        <span className="text-dark-600 text-xs">—</span>
                      )}
                    </td>

                    {/* Institutional Score */}
                    <td className="text-center py-3 px-3">
                      {instScore !== null ? (
                        <span className={`text-sm font-mono ${
                          instScore >= 0.7 ? 'text-success' : instScore >= 0.4 ? 'text-warning' : 'text-dark-400'
                        }`}>
                          {typeof instScore === 'number' ? (instScore * 100).toFixed(0) : instScore}
                        </span>
                      ) : (
                        <span className="text-dark-600 text-xs">—</span>
                      )}
                    </td>

                    {/* Delta */}
                    <td className="text-right py-3 px-3">
                      <span className={`text-sm font-mono font-medium ${deltaPositive ? 'text-success' : 'text-danger'}`}>
                        {of ? fmtDelta(of.delta) : '—'}
                      </span>
                    </td>

                    {/* CVD */}
                    <td className="text-right py-3 px-3">
                      <span className={`text-sm font-mono ${deltaPositive ? 'text-success' : 'text-danger'}`}>
                        {of ? fmtDelta(of.cvd) : '—'}
                      </span>
                    </td>

                    {/* Aggressive Buyers */}
                    <td className="text-right py-3 px-3">
                      <span className="text-sm font-mono text-success">
                        {of ? fmtVol(of.takerBuyVol) : '—'}
                      </span>
                    </td>

                    {/* Aggressive Sellers */}
                    <td className="text-right py-3 px-3">
                      <span className="text-sm font-mono text-danger">
                        {of ? fmtVol(of.takerSellVol) : '—'}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function SummaryCard({
  label,
  value,
  icon,
  color = 'text-white',
}: {
  label: string;
  value: string;
  icon: React.ReactNode;
  color?: string;
}) {
  return (
    <div className="bg-dark-900 rounded-xl border border-dark-700 p-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-dark-400 text-xs mb-1">{label}</p>
          <p className={`text-xl font-bold ${color}`}>{value}</p>
        </div>
        {icon}
      </div>
    </div>
  );
}
