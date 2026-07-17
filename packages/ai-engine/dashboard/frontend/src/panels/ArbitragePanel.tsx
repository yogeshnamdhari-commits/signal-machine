/**
 * ArbitragePanel — Arbitrage opportunities and performance display.
 */
import React from 'react'
import { MetricCard } from '../components/MetricCard'
import { DataTable } from '../components/DataTable'
import { formatCurrency, pnlColor } from '../utils/format'

interface ArbitrageData {
  active_arbitrages: Array<Record<string, any>>
  active_count: number
  historical: Array<Record<string, any>>
  historical_count: number
  metrics: {
    total_scans: number
    opportunities_found: number
    opportunities_executed: number
    total_expected_profit: number
    total_realized_profit: number
    avg_spread_bps: number
    avg_funding_bps: number
    avg_basis_bps: number
    avg_confidence: number
    win_rate: number
    by_type: Record<string, { count: number; profit: number }>
  }
}

interface ArbitragePanelProps {
  data: ArbitrageData | null
}

export const ArbitragePanel: React.FC<ArbitragePanelProps> = ({ data }) => {
  if (!data) return <div className="card animate-pulse h-64 bg-neutral-800 rounded-lg" />

  const { metrics } = data

  const oppColumns = [
    { key: 'detected_at', header: 'Time', render: (r: any) => new Date(((r.detected_at || r.timestamp) ?? 0) * 1000).toLocaleTimeString() },
    { key: 'arb_type', header: 'Type', render: (r: any) => <span className="capitalize">{(r.arb_type || '').replace(/_/g, ' ')}</span> },
    { key: 'symbol', header: 'Symbol' },
    { key: 'long_exchange', header: 'Long', render: (r: any) => <span className="text-profit capitalize">{r.long_exchange}</span> },
    { key: 'short_exchange', header: 'Short', render: (r: any) => <span className="text-loss capitalize">{r.short_exchange}</span> },
    { key: 'entry_spread_bps', header: 'Spread', render: (r: any) => `${(r.entry_spread_bps || 0).toFixed(1)} bps`, sortable: true },
    { key: 'net_edge_bps', header: 'Edge', render: (r: any) => `${(r.net_edge_bps || 0).toFixed(1)} bps`, sortable: true },
    { key: 'confidence', header: 'Confidence', render: (r: any) => `${((r.confidence || 0) * 100).toFixed(0)}%`, sortable: true },
    { key: 'expected_profit_usd', header: 'Expected $', render: (r: any) => formatCurrency(r.expected_profit_usd || 0), sortable: true },
  ]

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-white">Arbitrage</h2>

      {/* Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
        <MetricCard label="Active" value={data.active_count} glow={data.active_count > 0} />
        <MetricCard label="Opportunities" value={metrics.opportunities_found} />
        <MetricCard label="Executed" value={metrics.opportunities_executed} />
        <MetricCard label="Win Rate" value={`${(metrics.win_rate ?? 0).toFixed(1)}%`} deltaColor={(metrics.win_rate ?? 0) > 50 ? 'profit' : 'loss'} />
        <MetricCard label="Realized Profit" value={formatCurrency(metrics.total_realized_profit ?? 0)} deltaColor={(metrics.total_realized_profit ?? 0) > 0 ? 'profit' : 'loss'} />
        <MetricCard label="Avg Confidence" value={`${((metrics.avg_confidence ?? 0) * 100).toFixed(0)}%`} />
      </div>

      {/* By Type */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {Object.entries(metrics.by_type).map(([type, stats]) => (
          <div key={type} className="card">
            <div className="text-xs text-neutral-500 uppercase mb-1">{type.replace(/_/g, ' ')}</div>
            <div className="text-lg font-bold font-mono text-white">{stats.count}</div>
            <div className={`text-sm font-mono ${pnlColor(stats.profit)}`}>
              {formatCurrency(stats.profit)}
            </div>
          </div>
        ))}
      </div>

      {/* Active Arbitrages */}
      {data.active_arbitrages.length > 0 && (
        <div className="card">
          <div className="card-header">Active Arbitrages</div>
          <DataTable columns={oppColumns} data={data.active_arbitrages} maxRows={10} />
        </div>
      )}

      {/* Historical */}
      <div className="card">
        <div className="card-header">Historical Arbitrages</div>
        <DataTable columns={oppColumns} data={data.historical} maxRows={15} />
      </div>
    </div>
  )
}
