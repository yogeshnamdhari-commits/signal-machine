import { useState, useMemo, useCallback } from 'react';
import { useRealTimeSheetData } from '../hooks/useSheetData';
import { SymbolSheetData } from '../types/sheet';
import { RefreshCw, Search, ArrowUpDown, Filter, Wifi, WifiOff, TrendingUp, TrendingDown, Minus } from 'lucide-react';

type SortKey = keyof SymbolSheetData;

export default function DataSheet() {
  const { data, loading, lastUpdate, refresh } = useRealTimeSheetData();
  const [search, setSearch] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('signalConfidence');
  const [sortAsc, setSortAsc] = useState(false);
  const [signalFilter, setSignalFilter] = useState<'all' | 'buy' | 'sell'>('all');
  const [autoRefresh, setAutoRefresh] = useState(true);

  // Auto-refresh via socket is handled by the hook

  const handleSort = useCallback((key: SortKey) => {
    if (sortKey === key) {
      setSortAsc(!sortAsc);
    } else {
      setSortKey(key);
      setSortAsc(false);
    }
  }, [sortKey, sortAsc]);

  const filtered = useMemo(() => {
    let result = data;

    // Search filter
    if (search) {
      const q = search.toUpperCase();
      result = result.filter(d => d.symbol.includes(q));
    }

    // Signal filter
    if (signalFilter !== 'all') {
      result = result.filter(d => d.signal === signalFilter);
    }

    // Sort
    result = [...result].sort((a, b) => {
      const aVal = a[sortKey] ?? 0;
      const bVal = b[sortKey] ?? 0;
      if (typeof aVal === 'string' && typeof bVal === 'string') {
        return sortAsc ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal);
      }
      return sortAsc ? (aVal as number) - (bVal as number) : (bVal as number) - (aVal as number);
    });

    return result;
  }, [data, search, sortKey, sortAsc, signalFilter]);

  const formatNumber = (n: number | null | undefined, decimals = 2) => {
    if (n == null || isNaN(n)) return '—';
    if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(2)}B`;
    if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
    if (Math.abs(n) >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
    return n.toFixed(decimals);
  };

  const formatPrice = (p: number | null | undefined) => {
    if (p == null || isNaN(p)) return '—';
    if (p >= 1000) return p.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (p >= 1) return p.toFixed(4);
    return p.toFixed(6);
  };

  const formatFunding = (r: number | null | undefined) => {
    if (r == null || isNaN(r)) return '—';
    return (r * 100).toFixed(4) + '%';
  };

  const isLive = lastUpdate > 0 && (Date.now() - lastUpdate) < 30000;

  const SortHeader = ({ label, sortField, className = '' }: { label: string; sortField: SortKey; className?: string }) => (
    <th
      className={`px-3 py-2 text-left text-xs font-medium text-dark-400 uppercase cursor-pointer hover:text-white transition-colors select-none ${className}`}
      onClick={() => handleSort(sortField)}
    >
      <div className="flex items-center space-x-1">
        <span>{label}</span>
        <ArrowUpDown className={`w-3 h-3 ${sortKey === sortField ? 'text-primary-400' : 'text-dark-600'}`} />
      </div>
    </th>
  );

  return (
    <div className="bg-dark-900 rounded-xl border border-dark-700 overflow-hidden">
      {/* Header */}
      <div className="p-4 border-b border-dark-700">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center space-x-3">
            <h2 className="text-lg font-semibold text-white">Full Market Scanner</h2>
            <div className="flex items-center space-x-1">
              {isLive ? <Wifi className="w-4 h-4 text-success" /> : <WifiOff className="w-4 h-4 text-danger" />}
              <span className={`text-xs ${isLive ? 'text-success' : 'text-danger'}`}>
                {isLive ? 'LIVE' : 'OFFLINE'}
              </span>
            </div>
            <span className="text-xs text-dark-400">
              {filtered.length} / {data.length} symbols
            </span>
          </div>
          <div className="flex items-center space-x-2">
            {lastUpdate > 0 && (
              <span className="text-xs text-dark-500">
                Updated {new Date(lastUpdate).toLocaleTimeString()}
              </span>
            )}
            <button
              onClick={refresh}
              className="p-2 rounded-lg bg-dark-800 hover:bg-dark-700 text-dark-300 hover:text-white transition-colors"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {/* Filters */}
        <div className="flex items-center space-x-3">
          <div className="relative flex-1 max-w-xs">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-dark-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search symbol..."
              className="w-full pl-9 pr-3 py-1.5 bg-dark-800 border border-dark-600 rounded-lg text-sm text-white placeholder-dark-500 focus:outline-none focus:border-primary-500"
            />
          </div>
          <div className="flex items-center space-x-1">
            {(['all', 'buy', 'sell'] as const).map((f) => (
              <button
                key={f}
                onClick={() => setSignalFilter(f)}
                className={`px-3 py-1 text-xs rounded-lg font-medium transition-colors ${
                  signalFilter === f
                    ? f === 'buy' ? 'bg-success text-white'
                      : f === 'sell' ? 'bg-danger text-white'
                      : 'bg-primary-600 text-white'
                    : 'bg-dark-800 text-dark-400 hover:text-white'
                }`}
              >
                {f.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto max-h-[70vh] overflow-y-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-dark-900 z-10 border-b border-dark-700">
            <tr>
              <SortHeader label="Symbol" sortField="symbol" />
              <SortHeader label="Price" sortField="price" className="text-right" />
              <SortHeader label="Open Interest" sortField="openInterest" className="text-right" />
              <SortHeader label="OI Δ" sortField="openInterestChange" className="text-right" />
              <SortHeader label="OI Dir" sortField="oiDirection" />
              <SortHeader label="Funding" sortField="fundingRate" className="text-right" />
              <SortHeader label="Fund Dir" sortField="fundingDirection" />
              <SortHeader label="Volume 24h" sortField="volume24h" className="text-right" />
              <SortHeader label="Vol Dir" sortField="volumeDirection" />
              <SortHeader label="Exchange Flow" sortField="exchangeFlow" className="text-right" />
              <SortHeader label="Flow Dir" sortField="exchangeFlowDirection" />
              <SortHeader label="Signal" sortField="signal" />
              <SortHeader label="Conf" sortField="signalConfidence" className="text-right" />
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={13} className="text-center py-12 text-dark-400">
                  {loading ? 'Scanning market data...' : 'No symbols match filters'}
                </td>
              </tr>
            ) : (
              filtered.map((row) => (
                <tr
                  key={row.symbol}
                  className={`border-b border-dark-800 hover:bg-dark-800/50 transition-colors ${
                    row.signal === 'buy' ? 'bg-success/5' : row.signal === 'sell' ? 'bg-danger/5' : ''
                  }`}
                >
                  {/* Symbol */}
                  <td className="px-3 py-2 font-medium text-white">
                    {row.symbol.replace('USDT', '')}
                    <span className="text-dark-500 text-xs">/USDT</span>
                  </td>
                  {/* Price */}
                  <td className="px-3 py-2 text-right font-mono text-white">
                    ${formatPrice(row.price)}
                  </td>
                  {/* Open Interest */}
                  <td className="px-3 py-2 text-right font-mono text-dark-200">
                    {formatNumber(row.openInterest)}
                  </td>
                  {/* OI Change */}
                  <td className={`px-3 py-2 text-right font-mono ${row.openInterestChange >= 0 ? 'text-success' : 'text-danger'}`}>
                    {row.openInterestChange >= 0 ? '+' : ''}{row.openInterestChange.toFixed(2)}%
                  </td>
                  {/* OI Direction */}
                  <td className="px-3 py-2">
                    <DirectionBadge direction={row.oiDirection} />
                  </td>
                  {/* Funding */}
                  <td className={`px-3 py-2 text-right font-mono ${row.fundingRate >= 0 ? 'text-danger' : 'text-success'}`}>
                    {formatFunding(row.fundingRate)}
                  </td>
                  {/* Funding Direction */}
                  <td className="px-3 py-2">
                    <DirectionBadge direction={row.fundingDirection} />
                  </td>
                  {/* Volume */}
                  <td className="px-3 py-2 text-right font-mono text-dark-200">
                    {formatNumber(row.volume24h)}
                  </td>
                  {/* Volume Direction */}
                  <td className="px-3 py-2">
                    <DirectionBadge direction={row.volumeDirection} />
                  </td>
                  {/* Exchange Flow */}
                  <td className={`px-3 py-2 text-right font-mono ${row.exchangeFlow >= 0 ? 'text-success' : 'text-danger'}`}>
                    {formatNumber(row.exchangeFlow)}
                  </td>
                  {/* Flow Direction */}
                  <td className="px-3 py-2">
                    <FlowBadge direction={row.exchangeFlowDirection} />
                  </td>
                  {/* Signal */}
                  <td className="px-3 py-2">
                    <SignalBadge signal={row.signal} />
                  </td>
                  {/* Confidence */}
                  <td className="px-3 py-2 text-right font-mono text-dark-200">
                    {row.signal !== 'neutral' ? `${(row.signalConfidence * 100).toFixed(0)}%` : '—'}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DirectionBadge({ direction }: { direction: 'buy' | 'sell' }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs rounded font-medium ${
      direction === 'buy'
        ? 'bg-success/20 text-success'
        : 'bg-danger/20 text-danger'
    }`}>
      {direction === 'buy' ? <TrendingUp className="w-3 h-3 mr-1" /> : <TrendingDown className="w-3 h-3 mr-1" />}
      {direction.toUpperCase()}
    </span>
  );
}

function FlowBadge({ direction }: { direction: 'in' | 'out' }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs rounded font-medium ${
      direction === 'in'
        ? 'bg-success/20 text-success'
        : 'bg-danger/20 text-danger'
    }`}>
      {direction === 'in' ? 'INFLOW' : 'OUTFLOW'}
    </span>
  );
}

function SignalBadge({ signal }: { signal: 'buy' | 'sell' | 'neutral' }) {
  if (signal === 'neutral') {
    return (
      <span className="inline-flex items-center px-2 py-0.5 text-xs rounded bg-dark-700 text-dark-400">
        <Minus className="w-3 h-3 mr-1" />
        NEUTRAL
      </span>
    );
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs rounded font-bold ${
      signal === 'buy'
        ? 'bg-success text-white'
        : 'bg-danger text-white'
    }`}>
      {signal === 'buy' ? <TrendingUp className="w-3 h-3 mr-1" /> : <TrendingDown className="w-3 h-3 mr-1" />}
      {signal.toUpperCase()}
    </span>
  );
}
