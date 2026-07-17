import { useState, useEffect, useCallback } from 'react';
import { simulatorApi } from '../services/api';
import {
  TrendingUp,
  TrendingDown,
  Trophy,
  Target,
  Activity,
  RefreshCw,
  RotateCcw,
  ArrowUpRight,
  ArrowDownRight,
  Clock,
  DollarSign,
  BarChart3,
  AlertTriangle,
  CheckCircle,
  XCircle,
} from 'lucide-react';

interface SimTrade {
  id: string;
  symbol: string;
  side: 'LONG' | 'SHORT';
  entryPrice: number;
  stopLoss: number;
  takeProfit: number;
  riskReward: number;
  status: string;
  entryTime: number;
  exitTime: number | null;
  exitPrice: number | null;
  pnl: number;
  pnlPercent: number;
  isWin: boolean | null;
  closeReason: string;
}

interface SimStats {
  totalTrades: number;
  openTrades: number;
  closedTrades: number;
  wins: number;
  losses: number;
  winRate: number;
  profitFactor: number;
  avgRR: number;
  totalPnL: number;
  avgWin: number;
  avgLoss: number;
  largestWin: number;
  largestLoss: number;
  maxDrawdown: number;
  maxDrawdownAbs: number;
  sharpeRatio: number;
  equityCurve: number[];
  recentTrades: SimTrade[];
}

export default function SimulationDashboard() {
  const [stats, setStats] = useState<SimStats | null>(null);
  const [trades, setTrades] = useState<SimTrade[]>([]);
  const [openTrades, setOpenTrades] = useState<SimTrade[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<'overview' | 'trades' | 'open'>('overview');

  const fetchData = useCallback(async () => {
    try {
      const [statsData, tradesData, openData] = await Promise.allSettled([
        simulatorApi.getStats(),
        simulatorApi.getTrades(50),
        simulatorApi.getOpenTrades(),
      ]);
      if (statsData.status === 'fulfilled') setStats(statsData.value);
      if (tradesData.status === 'fulfilled') setTrades(tradesData.value);
      if (openData.status === 'fulfilled') setOpenTrades(openData.value);
    } catch (err) {
      console.error('Simulator fetch error:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 10000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const handleReset = async () => {
    if (!confirm('Reset simulation? All trade history will be cleared.')) return;
    await simulatorApi.reset();
    fetchData();
  };

  const fmtUSD = (v: number) => {
    const abs = Math.abs(v);
    if (abs >= 1e6) return `${v < 0 ? '-' : ''}$${(abs / 1e6).toFixed(2)}M`;
    if (abs >= 1e3) return `${v < 0 ? '-' : ''}$${(abs / 1e3).toFixed(1)}K`;
    return `${v < 0 ? '-' : ''}$${abs.toFixed(2)}`;
  };

  const fmtPrice = (p: number) => {
    if (p >= 10000) return p.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    if (p >= 1) return p.toFixed(4);
    return p.toFixed(6);
  };

  const fmtTime = (ts: number) => new Date(ts).toLocaleTimeString();
  const fmtDuration = (start: number, end: number | null) => {
    if (!end) return 'Open';
    const ms = end - start;
    if (ms < 60000) return `${Math.round(ms / 1000)}s`;
    if (ms < 3600000) return `${Math.round(ms / 60000)}m`;
    return `${(ms / 3600000).toFixed(1)}h`;
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <RefreshCw className="w-8 h-8 text-primary-400 animate-spin" />
        <span className="ml-3 text-dark-400">Loading simulation...</span>
      </div>
    );
  }

  const s = stats;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <BarChart3 className="w-6 h-6 text-primary-400" />
          <h2 className="text-xl font-bold text-white">Trade Simulation</h2>
          {s && (
            <span className="text-xs text-dark-500 bg-dark-800 px-2 py-1 rounded-full">
              {s.closedTrades} trades
            </span>
          )}
        </div>
        <div className="flex items-center space-x-2">
          <button
            onClick={fetchData}
            className="p-2 rounded-lg bg-dark-800 border border-dark-700 hover:border-primary-500 transition-colors"
          >
            <RefreshCw className="w-4 h-4 text-dark-400" />
          </button>
          <button
            onClick={handleReset}
            className="flex items-center space-x-1 px-3 py-2 rounded-lg bg-danger/20 border border-danger/30 text-danger text-sm hover:bg-danger/30 transition-colors"
          >
            <RotateCcw className="w-4 h-4" />
            <span>Reset</span>
          </button>
        </div>
      </div>

      {/* Performance Cards */}
      {s && (
        <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
          <PerfCard label="Total PnL" value={fmtUSD(s.totalPnL)} color={s.totalPnL >= 0 ? 'text-success' : 'text-danger'} icon={<DollarSign className="w-5 h-5" />} />
          <PerfCard label="Win Rate" value={`${s.winRate.toFixed(1)}%`} color={s.winRate >= 50 ? 'text-success' : 'text-danger'} icon={<Trophy className="w-5 h-5" />} />
          <PerfCard label="Profit Factor" value={s.profitFactor === Infinity ? '∞' : s.profitFactor.toFixed(2)} color={s.profitFactor >= 1.5 ? 'text-success' : s.profitFactor >= 1 ? 'text-warning' : 'text-danger'} icon={<Target className="w-5 h-5" />} />
          <PerfCard label="Avg R:R" value={`${s.avgRR.toFixed(2)}x`} color={s.avgRR >= 2 ? 'text-success' : 'text-warning'} icon={<Activity className="w-5 h-5" />} />
          <PerfCard label="Max Drawdown" value={`${s.maxDrawdown.toFixed(1)}%`} color="text-danger" icon={<AlertTriangle className="w-5 h-5" />} />
          <PerfCard label="Sharpe Ratio" value={s.sharpeRatio.toFixed(2)} color={s.sharpeRatio >= 1 ? 'text-success' : 'text-warning'} icon={<BarChart3 className="w-5 h-5" />} />
        </div>
      )}

      {/* Win/Loss Breakdown */}
      {s && s.closedTrades > 0 && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div className="bg-dark-900 rounded-xl border border-dark-700 p-3 text-center">
            <div className="text-2xl font-bold text-success">{s.wins}</div>
            <div className="text-xs text-dark-400">Wins</div>
          </div>
          <div className="bg-dark-900 rounded-xl border border-dark-700 p-3 text-center">
            <div className="text-2xl font-bold text-danger">{s.losses}</div>
            <div className="text-xs text-dark-400">Losses</div>
          </div>
          <div className="bg-dark-900 rounded-xl border border-dark-700 p-3 text-center">
            <div className="text-lg font-bold text-success">{fmtUSD(s.avgWin)}</div>
            <div className="text-xs text-dark-400">Avg Win</div>
          </div>
          <div className="bg-dark-900 rounded-xl border border-dark-700 p-3 text-center">
            <div className="text-lg font-bold text-danger">{fmtUSD(s.avgLoss)}</div>
            <div className="text-xs text-dark-400">Avg Loss</div>
          </div>
        </div>
      )}

      {/* Tab Navigation */}
      <div className="flex items-center space-x-1 bg-dark-900 rounded-xl border border-dark-700 p-1 w-fit">
        {(['overview', 'trades', 'open'] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              tab === t ? 'bg-primary-600 text-white' : 'text-dark-400 hover:text-white hover:bg-dark-800'
            }`}
          >
            {t === 'overview' ? 'Equity Curve' : t === 'trades' ? `History (${trades.length})` : `Open (${openTrades.length})`}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {tab === 'overview' && s && (
        <EquityCurve data={s.equityCurve} startingBalance={10000} />
      )}

      {tab === 'trades' && (
        <TradeTable trades={trades} fmtUSD={fmtUSD} fmtPrice={fmtPrice} fmtTime={fmtTime} fmtDuration={fmtDuration} />
      )}

      {tab === 'open' && (
        <OpenTradesTable trades={openTrades} fmtUSD={fmtUSD} fmtPrice={fmtPrice} fmtTime={fmtTime} />
      )}
    </div>
  );
}

// ── Sub-components ──

function PerfCard({ label, value, color, icon }: { label: string; value: string; color: string; icon: React.ReactNode }) {
  return (
    <div className="bg-dark-900 rounded-xl border border-dark-700 p-3">
      <div className="flex items-center justify-between mb-1">
        <span className="text-dark-500 text-xs">{label}</span>
        <span className="text-dark-600">{icon}</span>
      </div>
      <div className={`text-lg font-bold ${color}`}>{value}</div>
    </div>
  );
}

function EquityCurve({ data, startingBalance }: { data: number[]; startingBalance: number }) {
  if (data.length < 2) {
    return (
      <div className="bg-dark-900 rounded-xl border border-dark-700 p-8 text-center text-dark-400">
        <Activity className="w-12 h-12 mx-auto mb-3 text-dark-600" />
        <p>Equity curve will appear after trades close.</p>
        <p className="text-sm mt-1">Waiting for SL/TP hits...</p>
      </div>
    );
  }

  const max = Math.max(...data);
  const min = Math.min(...data);
  const range = max - min || 1;
  const h = 200;

  // Build SVG path
  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * 100;
    const y = h - ((v - min) / range) * (h - 20) - 10;
    return `${x},${y}`;
  });
  const pathD = `M ${points.join(' L ')}`;
  const fillD = `${pathD} L 100,${h} L 0,${h} Z`;

  const isUp = data[data.length - 1] >= data[0];

  return (
    <div className="bg-dark-900 rounded-xl border border-dark-700 p-4">
      <h3 className="text-sm font-semibold text-dark-300 mb-3">Equity Curve</h3>
      <svg viewBox={`0 0 100 ${h}`} className="w-full h-48" preserveAspectRatio="none">
        <defs>
          <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={isUp ? '#22c55e' : '#ef4444'} stopOpacity="0.3" />
            <stop offset="100%" stopColor={isUp ? '#22c55e' : '#ef4444'} stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={fillD} fill="url(#eqGrad)" />
        <path d={pathD} fill="none" stroke={isUp ? '#22c55e' : '#ef4444'} strokeWidth="0.5" />
      </svg>
      <div className="flex justify-between text-xs text-dark-500 mt-1">
        <span>${startingBalance.toLocaleString()}</span>
        <span>Peak: ${max.toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
        <span>${data[data.length - 1].toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
      </div>
    </div>
  );
}

function TradeTable({ trades, fmtUSD, fmtPrice, fmtTime, fmtDuration }: any) {
  if (trades.length === 0) {
    return (
      <div className="bg-dark-900 rounded-xl border border-dark-700 p-8 text-center text-dark-400">
        <Clock className="w-12 h-12 mx-auto mb-3 text-dark-600" />
        <p>No completed trades yet.</p>
        <p className="text-sm mt-1">Trades close when SL or TP is hit.</p>
      </div>
    );
  }

  return (
    <div className="bg-dark-900 rounded-xl border border-dark-700 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full min-w-[900px]">
          <thead>
            <tr className="text-dark-400 text-xs uppercase border-b border-dark-700">
              <th className="text-left py-3 px-3">Symbol</th>
              <th className="text-center py-3 px-3">Side</th>
              <th className="text-right py-3 px-3">Entry</th>
              <th className="text-right py-3 px-3">Exit</th>
              <th className="text-right py-3 px-3">SL</th>
              <th className="text-right py-3 px-3">TP</th>
              <th className="text-right py-3 px-3">R:R</th>
              <th className="text-right py-3 px-3">PnL</th>
              <th className="text-right py-3 px-3">%</th>
              <th className="text-center py-3 px-3">Result</th>
              <th className="text-right py-3 px-3">Duration</th>
              <th className="text-left py-3 px-3">Reason</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t: SimTrade) => (
              <tr key={t.id} className="border-b border-dark-800 hover:bg-dark-800/50 transition-colors">
                <td className="py-3 px-3 font-semibold text-white">{t.symbol.replace('USDT', '')}</td>
                <td className="py-3 px-3 text-center">
                  <span className={`px-2 py-0.5 rounded text-xs font-bold ${
                    t.side === 'LONG' ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'
                  }`}>
                    {t.side}
                  </span>
                </td>
                <td className="py-3 px-3 text-right font-mono text-sm text-white">${fmtPrice(t.entryPrice)}</td>
                <td className="py-3 px-3 text-right font-mono text-sm text-white">{t.exitPrice ? `$${fmtPrice(t.exitPrice)}` : '—'}</td>
                <td className="py-3 px-3 text-right font-mono text-sm text-danger">${fmtPrice(t.stopLoss)}</td>
                <td className="py-3 px-3 text-right font-mono text-sm text-success">${fmtPrice(t.takeProfit)}</td>
                <td className="py-3 px-3 text-right text-sm text-warning">{t.riskReward.toFixed(2)}x</td>
                <td className={`py-3 px-3 text-right font-mono text-sm font-bold ${t.pnl >= 0 ? 'text-success' : 'text-danger'}`}>
                  {fmtUSD(t.pnl)}
                </td>
                <td className={`py-3 px-3 text-right text-sm ${t.pnlPercent >= 0 ? 'text-success' : 'text-danger'}`}>
                  {t.pnlPercent >= 0 ? '+' : ''}{t.pnlPercent.toFixed(2)}%
                </td>
                <td className="py-3 px-3 text-center">
                  {t.isWin ? (
                    <CheckCircle className="w-5 h-5 text-success mx-auto" />
                  ) : (
                    <XCircle className="w-5 h-5 text-danger mx-auto" />
                  )}
                </td>
                <td className="py-3 px-3 text-right text-sm text-dark-300">
                  {fmtDuration(t.entryTime, t.exitTime)}
                </td>
                <td className="py-3 px-3 text-xs text-dark-400">{t.closeReason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function OpenTradesTable({ trades, fmtUSD, fmtPrice, fmtTime }: any) {
  if (trades.length === 0) {
    return (
      <div className="bg-dark-900 rounded-xl border border-dark-700 p-8 text-center text-dark-400">
        <Target className="w-12 h-12 mx-auto mb-3 text-dark-600" />
        <p>No open trades.</p>
        <p className="text-sm mt-1">Trades will open when signals are generated.</p>
      </div>
    );
  }

  return (
    <div className="bg-dark-900 rounded-xl border border-dark-700 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="text-dark-400 text-xs uppercase border-b border-dark-700">
              <th className="text-left py-3 px-3">Symbol</th>
              <th className="text-center py-3 px-3">Side</th>
              <th className="text-right py-3 px-3">Entry</th>
              <th className="text-right py-3 px-3">SL</th>
              <th className="text-right py-3 px-3">TP</th>
              <th className="text-right py-3 px-3">R:R</th>
              <th className="text-right py-3 px-3">Opened</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t: SimTrade) => (
              <tr key={t.id} className="border-b border-dark-800 hover:bg-dark-800/50 transition-colors">
                <td className="py-3 px-3 font-semibold text-white">{t.symbol.replace('USDT', '')}</td>
                <td className="py-3 px-3 text-center">
                  <span className={`px-2 py-0.5 rounded text-xs font-bold ${
                    t.side === 'LONG' ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'
                  }`}>
                    {t.side}
                  </span>
                </td>
                <td className="py-3 px-3 text-right font-mono text-sm">${fmtPrice(t.entryPrice)}</td>
                <td className="py-3 px-3 text-right font-mono text-sm text-danger">${fmtPrice(t.stopLoss)}</td>
                <td className="py-3 px-3 text-right font-mono text-sm text-success">${fmtPrice(t.takeProfit)}</td>
                <td className="py-3 px-3 text-right text-sm text-warning">{t.riskReward.toFixed(2)}x</td>
                <td className="py-3 px-3 text-right text-sm text-dark-300">{fmtTime(t.entryTime)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
