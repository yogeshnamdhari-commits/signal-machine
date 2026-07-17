/**
 * CapitalAllocationPanel — Capital allocation display.
 */
import React from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
} from 'recharts'
import { MetricCard } from '../components/MetricCard'
import { DataTable } from '../components/DataTable'

interface AllocationData {
  allocation_model: string
  recent_allocations: Array<Record<string, any>>
  total_allocations: number
  capital_usage_pct: number
  risk_usage_pct: number
  leverage_usage: number
  kelly_fraction: number
  volatility_target: number
  rejections: Array<Record<string, any>>
  rejection_count: number
  score_weighting: Record<string, number>
}

interface AllocationPanelProps {
  data: AllocationData | null
  portfolioData?: {
    exchange_allocation: Record<string, { value: number; pct: number }>
    symbol_allocation: Record<string, { value: number; pct: number }>
    target_allocation: Record<string, number>
  } | null
}

const COLORS = ['#4a9eff', '#00ff88', '#f59e0b', '#ff4444', '#8b5cf6', '#ec4899']

export const CapitalAllocationPanel: React.FC<AllocationPanelProps> = ({ data, portfolioData }) => {
  if (!data) return <div className="card animate-pulse h-64 bg-neutral-800 rounded-lg" />

  const scoreData = Object.entries(data.score_weighting).map(([name, weight]) => ({
    name: name.replace(/_/g, ' '),
    weight: weight * 100,
  }))

  const allocColumns = [
    { key: 'recorded_at', header: 'Time', render: (r: any) => new Date((r.recorded_at ?? 0) * 1000).toLocaleTimeString() },
    { key: 'symbol', header: 'Symbol' },
    { key: 'exchange', header: 'Exchange', render: (r: any) => <span className="capitalize">{r.exchange}</span> },
    { key: 'capital_usd', header: 'Capital', render: (r: any) => `$${(r.capital_usd || 0).toFixed(2)}`, sortable: true },
    { key: 'leverage', header: 'Leverage', render: (r: any) => `${(r.leverage || 0).toFixed(1)}x` },
    { key: 'model_used', header: 'Model' },
    { key: 'reason', header: 'Reason' },
  ]

  const exchangeData = portfolioData
    ? Object.entries(portfolioData.exchange_allocation).map(([name, d]) => ({ name, ...d }))
    : []

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">Capital Allocation</h2>
        <span className="badge badge-info">{data.allocation_model.replace(/_/g, ' ')}</span>
      </div>

      {/* Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
        <MetricCard label="Total Allocations" value={data.total_allocations} />
        <MetricCard label="Capital Usage" value={`${(data.capital_usage_pct ?? 0).toFixed(1)}%`} />
        <MetricCard label="Leverage" value={`${(data.leverage_usage ?? 0).toFixed(1)}x`} />
        <MetricCard label="Kelly Fraction" value={(data.kelly_fraction ?? 0).toFixed(4)} />
        <MetricCard label="Rejections" value={data.rejection_count ?? 0} deltaColor={(data.rejection_count ?? 0) > 0 ? 'loss' : 'neutral'} />
        <MetricCard label="Vol Target" value={`${((data.volatility_target ?? 0) * 100).toFixed(1)}%`} />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Score Weighting */}
        <div className="card">
          <div className="card-header">Score Weighting</div>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={scoreData} layout="vertical">
              <CartesianGrid strokeDasharray="3 3" stroke="#1a1a3a" />
              <XAxis type="number" tick={{ fontSize: 10, fill: '#666' }} />
              <YAxis dataKey="name" type="category" width={100} tick={{ fontSize: 10, fill: '#666' }} />
              <Tooltip contentStyle={{ background: '#12122a', border: '1px solid #2a2a4e', borderRadius: '8px', fontSize: '12px' }} />
              <Bar dataKey="weight" fill="#4a9eff" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Exchange Allocation */}
        {exchangeData.length > 0 && (
          <div className="card">
            <div className="card-header">Exchange Allocation</div>
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie data={exchangeData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={70} label={({ name, percent }) => `${name} ${((percent ?? 0) * 100).toFixed(0)}%`}>
                  {exchangeData.map((_, i) => (
                    <Cell key={i} fill={COLORS[i % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip contentStyle={{ background: '#12122a', border: '1px solid #2a2a4e', borderRadius: '8px', fontSize: '12px' }} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>

      {/* Recent Allocations */}
      <div className="card">
        <div className="card-header">Recent Allocations</div>
        <DataTable columns={allocColumns} data={data.recent_allocations} maxRows={15} />
      </div>

      {/* Rejections */}
      {data.rejections.length > 0 && (
        <div className="card">
          <div className="card-header">Rejection Log</div>
          <DataTable
            columns={[
              { key: 'timestamp', header: 'Time', render: (r: any) => new Date((r.timestamp ?? 0) * 1000).toLocaleTimeString() },
              { key: 'symbol', header: 'Symbol' },
              { key: 'reason', header: 'Reason' },
            ]}
            data={data.rejections}
            maxRows={10}
          />
        </div>
      )}
    </div>
  )
}
