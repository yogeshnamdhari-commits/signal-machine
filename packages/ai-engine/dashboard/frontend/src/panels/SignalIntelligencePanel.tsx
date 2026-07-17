/**
 * SignalIntelligencePanel — Real-time signal intelligence display.
 *
 * Combines live signal feed, performance metrics, source distribution,
 * and historical signal table.
 */
import React from 'react'
import { MetricCard } from '../components/MetricCard'
import { LiveSignalFeed } from '../components/LiveSignalFeed'
import { DataTable } from '../components/DataTable'
import { formatCurrency, pnlColor } from '../utils/format'

const SOURCE_ICONS: Record<string, string> = {
  volume_spike: '📊',
  momentum: '🚀',
  mean_reversion: '🔄',
  breakout: '💥',
  order_flow: '🌊',
  institutional_flow: '🏦',
  liquidity_grab: '💧',
  delta_divergence: '📐',
  regime_shift: '🔀',
}

interface SignalData {
  active_signals: Array<Record<string, any>>
  recent_signals: Array<Record<string, any>>
  total_signals: number
  buy_signals: number
  sell_signals: number
  avg_confidence: number
  avg_quality: number
  market_regime: string
  signal_accuracy: number
  signal_win_rate: number
  signal_pf: number
  signal_expectancy: number
  total_pnl?: number
  avg_risk_reward?: number
  source_distribution?: Record<string, number>
  active_alerts?: number
}

interface SignalPanelProps {
  data: SignalData | null
  liveSignals?: Array<Record<string, any>>
  signalStats?: Record<string, any> | null
}

export const SignalIntelligencePanel: React.FC<SignalPanelProps> = ({
  data,
  liveSignals = [],
  signalStats = null,
}) => {
  if (!data && liveSignals.length === 0) {
    return <div className="card animate-pulse h-64 bg-neutral-800 rounded-lg" />
  }

  const panelData = data || {
    active_signals: [],
    recent_signals: [],
    total_signals: 0,
    buy_signals: 0,
    sell_signals: 0,
    avg_confidence: 0,
    avg_quality: 0,
    market_regime: 'unknown',
    signal_accuracy: 0,
    signal_win_rate: 0,
    signal_pf: 0,
    signal_expectancy: 0,
  }

  const stats = signalStats || {}
  const totalSignals = stats.total_generated || panelData.total_signals || liveSignals.length
  const winRate = stats.win_rate ?? panelData.signal_accuracy ?? 0
  const avgConf = stats.avg_confidence ?? panelData.avg_confidence ?? 0
  const totalPnl = stats.total_pnl ?? panelData.total_pnl ?? 0

  const signalColumns = [
    {
      key: 'timestamp', header: 'Time',
      render: (r: any) => {
        const ts = r.timestamp || r.recorded_at
        return ts ? new Date(ts * 1000).toLocaleTimeString() : '—'
      },
    },
    { key: 'symbol', header: 'Symbol' },
    {
      key: 'side', header: 'Side',
      render: (r: any) => (
        <span className={`text-xs font-bold px-2 py-0.5 rounded ${
          r.side === 'LONG' ? 'bg-profit/20 text-profit' : 'bg-loss/20 text-loss'
        }`}>{r.side}</span>
      ),
    },
    {
      key: 'confidence', header: 'Confidence', sortable: true,
      render: (r: any) => (
        <div className="flex items-center gap-2">
          <div className="w-16 bg-neutral-700 rounded-full h-1.5">
            <div className={`h-1.5 rounded-full ${
              (r.confidence || 0) >= 80 ? 'bg-profit' : (r.confidence || 0) >= 65 ? 'bg-warning' : 'bg-neutral-500'
            }`} style={{ width: `${(r.confidence || 0)}%` }} />
          </div>
          <span className="text-xs font-mono">{(r.confidence || 0).toFixed(0)}%</span>
        </div>
      ),
    },
    {
      key: 'source', header: 'Source',
      render: (r: any) => {
        const src = r.source || 'unknown'
        const icon = SOURCE_ICONS[src] || '📡'
        return (
          <span className="text-[11px] px-1.5 py-0.5 rounded bg-neutral-700/40 text-neutral-300 border border-neutral-600/30">
            {icon} {src.replace(/_/g, ' ')}
          </span>
        )
      },
    },
    {
      key: 'entry_price', header: 'Entry',
      render: (r: any) => (
        <span className="font-mono text-xs">
          ${(r.entry_price || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}
        </span>
      ),
    },
    {
      key: 'risk_reward', header: 'R:R',
      render: (r: any) => <span className="font-mono text-xs text-accent">{(r.risk_reward || 0).toFixed(1)}x</span>,
    },
    {
      key: 'pnl', header: 'PnL',
      render: (r: any) => {
        if (r.pnl == null) return <span className="text-neutral-600 text-xs">—</span>
        return <span className={`font-mono text-xs ${pnlColor(r.pnl)}`}>{formatCurrency(r.pnl)}</span>
      },
    },
  ]

  const sourceDistribution = panelData.source_distribution || {}

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
          <span className="relative flex h-3 w-3">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-75"></span>
            <span className="relative inline-flex rounded-full h-3 w-3 bg-accent"></span>
          </span>
          Signal Intelligence
          <span className="text-xs text-neutral-500 font-normal ml-2">Real-Time</span>
        </h2>
        {stats.symbols_tracked > 0 && (
          <span className="text-[10px] text-neutral-500 font-mono">
            {stats.symbols_tracked} symbols | {stats.total_processed} ticks
          </span>
        )}
      </div>

      {/* Metrics Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-8 gap-3">
        <MetricCard label="Total Signals" value={totalSignals} />
        <MetricCard label="Active" value={stats.active_count ?? 0} />
        <MetricCard label="BUY / LONG" value={panelData.buy_signals || 0} deltaColor="profit" />
        <MetricCard label="SELL / SHORT" value={panelData.sell_signals || 0} deltaColor="loss" />
        <MetricCard label="Win Rate" value={`${winRate.toFixed(1)}%`} deltaColor={winRate > 50 ? 'profit' : 'loss'} />
        <MetricCard label="Avg Confidence" value={`${avgConf.toFixed(0)}%`} deltaColor={avgConf >= 70 ? 'profit' : 'neutral'} />
        <MetricCard label="Signal PF" value={(panelData.signal_pf ?? 0).toFixed(2)} deltaColor={(panelData.signal_pf ?? 0) > 1 ? 'profit' : 'loss'} />
        <MetricCard label="Total PnL" value={formatCurrency(totalPnl)} deltaColor={totalPnl > 0 ? 'profit' : totalPnl < 0 ? 'loss' : 'neutral'} />
      </div>

      {/* Two-column: Live Feed + Sidebar */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2">
          <LiveSignalFeed signals={liveSignals} maxDisplay={12} />
        </div>

        <div className="space-y-4">
          {/* Source Distribution */}
          {Object.keys(sourceDistribution).length > 0 && (
            <div className="card">
              <div className="card-header">Signal Sources</div>
              <div className="space-y-2 mt-2">
                {Object.entries(sourceDistribution)
                  .sort(([, a], [, b]) => b - a)
                  .map(([source, count]) => {
                    const total = Object.values(sourceDistribution).reduce((s, v) => s + v, 0)
                    const pct = total > 0 ? (count / total) * 100 : 0
                    const icon = SOURCE_ICONS[source] || '📡'
                    return (
                      <div key={source} className="flex items-center gap-2">
                        <span className="text-xs w-4">{icon}</span>
                        <span className="text-xs text-neutral-400 flex-1 truncate">{source.replace(/_/g, ' ')}</span>
                        <div className="w-20 bg-neutral-700/50 rounded-full h-1.5">
                          <div className="bg-accent h-1.5 rounded-full" style={{ width: `${pct}%` }} />
                        </div>
                        <span className="text-xs font-mono text-neutral-300 w-8 text-right">{count}</span>
                      </div>
                    )
                  })}
              </div>
            </div>
          )}

          {/* Risk/Reward */}
          {(panelData.avg_risk_reward ?? 0) > 0 && (
            <div className="card">
              <div className="card-header">Risk / Reward</div>
              <div className="mt-2 space-y-2 text-xs">
                <div className="flex justify-between">
                  <span className="text-neutral-400">Avg R:R</span>
                  <span className="font-mono text-accent">{(panelData.avg_risk_reward ?? 0).toFixed(2)}x</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-neutral-400">Expectancy</span>
                  <span className={`font-mono ${(panelData.signal_expectancy ?? 0) >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {formatCurrency(panelData.signal_expectancy ?? 0)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-neutral-400">Profit Factor</span>
                  <span className={`font-mono ${(panelData.signal_pf ?? 0) >= 1 ? 'text-profit' : 'text-loss'}`}>
                    {(panelData.signal_pf ?? 0).toFixed(2)}
                  </span>
                </div>
              </div>
            </div>
          )}

          {/* Engine Health */}
          <div className="card">
            <div className="card-header">Engine Status</div>
            <div className="mt-2 space-y-2">
              {[
                { color: 'bg-profit', label: 'Engine Active' },
                { color: 'bg-accent', label: '16 symbols streaming' },
                { color: 'bg-blue-400', label: '8 signal generators' },
                { color: 'bg-purple-400', label: 'Real-time market feed' },
              ].map((item) => (
                <div key={item.label} className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full ${item.color} animate-pulse`}></span>
                  <span className="text-xs text-neutral-300">{item.label}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* Historical Signals */}
      {panelData.recent_signals && panelData.recent_signals.length > 0 && (
        <div className="card">
          <div className="card-header">Signal History</div>
          <DataTable columns={signalColumns} data={panelData.recent_signals} maxRows={20} />
        </div>
      )}
    </div>
  )
}
