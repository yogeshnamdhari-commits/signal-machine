import { useCallback, useEffect, useState } from 'react';
import { simulatorApi } from '../services/api';
import { Activity, BarChart3, RefreshCw, RotateCcw, Target } from 'lucide-react';

interface SimTrade {
  id: string;
  symbol: string;
  side: 'LONG' | 'SHORT';
  entryPrice: number;
  stopLoss: number;
  takeProfit: number;
  riskReward: number;
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
  maxDrawdown: number;
  sharpeRatio: number;
  equityCurve: number[];
}

const money = (value: number) => `${value < 0 ? '-' : ''}$${Math.abs(value).toFixed(2)}`;
const price = (value: number) => value >= 1 ? value.toFixed(4) : value.toFixed(6);

export default function SimulationDashboard() {
  const [stats, setStats] = useState<SimStats | null>(null);
  const [trades, setTrades] = useState<SimTrade[]>([]);
  const [openTrades, setOpenTrades] = useState<SimTrade[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<'overview' | 'trades' | 'open'>('overview');

  const fetchData = useCallback(async () => {
    try {
      const [statsResult, tradesResult, openResult] = await Promise.allSettled([
        simulatorApi.getStats(),
        simulatorApi.getTrades(50),
        simulatorApi.getOpenTrades(),
      ]);
      if (statsResult.status === 'fulfilled') setStats(statsResult.value);
      if (tradesResult.status === 'fulfilled') setTrades(tradesResult.value);
      if (openResult.status === 'fulfilled') setOpenTrades(openResult.value);
    } catch (error) {
      console.error('Simulation fetch error:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData();
    const interval = setInterval(() => void fetchData(), 10_000);
    return () => clearInterval(interval);
  }, [fetchData]);

  const reset = async () => {
    if (!window.confirm('Reset simulation history?')) return;
    await simulatorApi.reset();
    await fetchData();
  };

  if (loading) return <div className="flex h-64 items-center justify-center text-dark-400"><RefreshCw className="mr-3 h-7 w-7 animate-spin" />Loading simulation...</div>;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3"><BarChart3 className="h-6 w-6 text-primary-400" /><div><h2 className="text-xl font-bold text-white">Trade Simulation</h2><p className="text-xs text-dark-500">Historical simulation only; not live execution.</p></div></div>
        <div className="flex gap-2"><button onClick={() => void fetchData()} className="rounded-lg border border-dark-700 bg-dark-800 p-2"><RefreshCw className="h-4 w-4 text-dark-400" /></button><button onClick={() => void reset()} className="flex items-center gap-1 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-sm text-danger"><RotateCcw className="h-4 w-4" />Reset</button></div>
      </div>

      {stats && <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
        <Metric label="PnL" value={money(stats.totalPnL)} /><Metric label="Win Rate" value={`${stats.winRate.toFixed(1)}%`} /><Metric label="Profit Factor" value={Number.isFinite(stats.profitFactor) ? stats.profitFactor.toFixed(2) : '∞'} /><Metric label="Avg R:R" value={`${stats.avgRR.toFixed(2)}x`} /><Metric label="Drawdown" value={`${stats.maxDrawdown.toFixed(1)}%`} /><Metric label="Sharpe" value={stats.sharpeRatio.toFixed(2)} />
      </div>}

      <div className="flex gap-1 rounded-xl border border-dark-700 bg-dark-900 p-1 w-fit">
        {(['overview', 'trades', 'open'] as const).map((value) => <button key={value} onClick={() => setTab(value)} className={`rounded-lg px-4 py-2 text-sm ${tab === value ? 'bg-primary-600 text-white' : 'text-dark-400'}`}>{value === 'overview' ? 'Overview' : value === 'trades' ? `History (${trades.length})` : `Open (${openTrades.length})`}</button>)}
      </div>

      {tab === 'overview' && <Overview stats={stats} />}
      {tab === 'trades' && <TradeTable trades={trades} />}
      {tab === 'open' && <OpenTable trades={openTrades} />}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) { return <div className="rounded-xl border border-dark-700 bg-dark-900 p-3"><div className="text-xs text-dark-500">{label}</div><div className="mt-1 text-lg font-bold text-white">{value}</div></div>; }

function Overview({ stats }: { stats: SimStats | null }) {
  if (!stats || stats.equityCurve.length < 2) return <div className="rounded-xl border border-dark-700 bg-dark-900 p-10 text-center text-dark-400"><Activity className="mx-auto mb-3 h-10 w-10" />Insufficient simulation observations for an equity curve.</div>;
  const first = stats.equityCurve[0];
  const last = stats.equityCurve[stats.equityCurve.length - 1];
  return <div className="rounded-xl border border-dark-700 bg-dark-900 p-6"><div className="mb-3 flex justify-between text-sm text-dark-400"><span>Start: {money(first)}</span><span>End: {money(last)}</span></div><div className="grid grid-cols-2 gap-3 text-sm"><div>Wins: <span className="text-success">{stats.wins}</span></div><div>Losses: <span className="text-danger">{stats.losses}</span></div><div>Avg win: <span className="text-success">{money(stats.avgWin)}</span></div><div>Avg loss: <span className="text-danger">{money(stats.avgLoss)}</span></div><div>Closed trades: {stats.closedTrades}</div><div>Open trades: {stats.openTrades}</div></div></div>;
}

function TradeTable({ trades }: { trades: SimTrade[] }) {
  if (!trades.length) return <Empty label="No completed trades." />;
  return <div className="overflow-x-auto rounded-xl border border-dark-700 bg-dark-900"><table className="w-full text-sm"><thead className="border-b border-dark-700 text-xs uppercase text-dark-400"><tr><th className="px-3 py-3 text-left">Symbol</th><th className="px-3 py-3">Side</th><th className="px-3 py-3 text-right">Entry</th><th className="px-3 py-3 text-right">Exit</th><th className="px-3 py-3 text-right">PnL</th><th className="px-3 py-3">Result</th><th className="px-3 py-3">Reason</th></tr></thead><tbody>{trades.map((trade) => <tr key={trade.id} className="border-b border-dark-800"><td className="px-3 py-3 font-semibold text-white">{trade.symbol}</td><td className="px-3 py-3 text-center">{trade.side}</td><td className="px-3 py-3 text-right font-mono">{price(trade.entryPrice)}</td><td className="px-3 py-3 text-right font-mono">{trade.exitPrice == null ? '—' : price(trade.exitPrice)}</td><td className={`px-3 py-3 text-right font-mono ${trade.pnl >= 0 ? 'text-success' : 'text-danger'}`}>{money(trade.pnl)}</td><td className="px-3 py-3 text-center">{trade.isWin ? 'WIN' : 'LOSS'}</td><td className="px-3 py-3 text-xs text-dark-400">{trade.closeReason}</td></tr>)}</tbody></table></div>;
}

function OpenTable({ trades }: { trades: SimTrade[] }) {
  if (!trades.length) return <Empty label="No open simulated trades." />;
  return <div className="overflow-x-auto rounded-xl border border-dark-700 bg-dark-900"><table className="w-full text-sm"><thead className="border-b border-dark-700 text-xs uppercase text-dark-400"><tr><th className="px-3 py-3 text-left">Symbol</th><th className="px-3 py-3">Side</th><th className="px-3 py-3 text-right">Entry</th><th className="px-3 py-3 text-right">SL</th><th className="px-3 py-3 text-right">TP</th><th className="px-3 py-3 text-right">R:R</th></tr></thead><tbody>{trades.map((trade) => <tr key={trade.id} className="border-b border-dark-800"><td className="px-3 py-3 font-semibold text-white">{trade.symbol}</td><td className="px-3 py-3 text-center">{trade.side}</td><td className="px-3 py-3 text-right font-mono">{price(trade.entryPrice)}</td><td className="px-3 py-3 text-right font-mono">{price(trade.stopLoss)}</td><td className="px-3 py-3 text-right font-mono">{price(trade.takeProfit)}</td><td className="px-3 py-3 text-right">{trade.riskReward.toFixed(2)}x</td></tr>)}</tbody></table></div>;
}

function Empty({ label }: { label: string }) { return <div className="rounded-xl border border-dark-700 bg-dark-900 p-10 text-center text-dark-400"><Target className="mx-auto mb-3 h-10 w-10" />{label}</div>; }
