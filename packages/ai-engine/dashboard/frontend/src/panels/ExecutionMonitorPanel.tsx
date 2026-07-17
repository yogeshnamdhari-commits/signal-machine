/**
 * ExecutionMonitorPanel — Order execution and routing display.
 */
import React from 'react'
import {
  PieChart, Pie, Cell, ResponsiveContainer, Tooltip,
} from 'recharts'
import { MetricCard } from '../components/MetricCard'
import { DataTable } from '../components/DataTable'
import { formatCurrency, formatLatency, pnlColor } from '../utils/format'

interface ExecutionData {
  orders_submitted: number
  orders_filled: number
  orders_rejected: number
  orders_cancelled: number
  partial_fills: number
  fill_rate: number
  avg_slippage_bps: number
  total_execution_cost: number
  venue_distribution: Record<string, number>
  avg_latency_ms: number
  recent_orders: Array<Record<string, any>>
  recent_routing: Array<Record<string, any>>
}

interface ExecutionPanelProps {
  data: ExecutionData | null
}

const COLORS = ['#4a9eff', '#00ff88', '#f59e0b', '#ff4444']

export const ExecutionMonitorPanel: React.FC<ExecutionPanelProps> = ({ data }) => {
  if (!data) return <div className="card animate-pulse h-64 bg-neutral-800 rounded-lg" />

  const venueData = Object.entries(data.venue_distribution).map(([name, value]) => ({
    name,
    value,
  }))

  const routingColumns = [
    { key: 'timestamp', header: 'Time', render: (r: any) => new Date((r.timestamp ?? 0) * 1000).toLocaleTimeString() },
    { key: 'symbol', header: 'Symbol' },
    { key: 'side', header: 'Side', render: (r: any) => (
      <span className={r.side === 'BUY' ? 'text-profit' : 'text-loss'}>{r.side}</span>
    )},
    { key: 'exchange', header: 'Venue', render: (r: any) => <span className="capitalize">{r.exchange}</span> },
    { key: 'score', header: 'Score', render: (r: any) => <span className="font-mono">{(r.score || 0).toFixed(4)}</span>, sortable: true },
    { key: 'routing_reason', header: 'Reason' },
    { key: 'latency_ms', header: 'Latency', render: (r: any) => formatLatency(r.latency_ms || 0), sortable: true },
  ]

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-white">Execution Monitor</h2>

      {/* Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
        <MetricCard label="Submitted" value={data.orders_submitted} />
        <MetricCard label="Filled" value={data.orders_filled} deltaColor="profit" />
        <MetricCard label="Rejected" value={data.orders_rejected} deltaColor={data.orders_rejected > 0 ? 'loss' : 'neutral'} />
        <MetricCard label="Fill Rate" value={`${(data.fill_rate ?? 0).toFixed(1)}%`} deltaColor="profit" />
        <MetricCard label="Avg Slippage" value={`${(data.avg_slippage_bps ?? 0).toFixed(2)} bps`} />
        <MetricCard label="Avg Latency" value={formatLatency(data.avg_latency_ms ?? 0)} />
      </div>

      {/* Charts & Table */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* Venue Distribution */}
        <div className="card">
          <div className="card-header">Venue Distribution</div>
          <ResponsiveContainer width="100%" height={200}>
            <PieChart>
              <Pie data={venueData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={70} label={({ name, percent }) => `${name} ${((percent ?? 0) * 100).toFixed(0)}%`}>
                {venueData.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip contentStyle={{ background: '#12122a', border: '1px solid #2a2a4e', borderRadius: '8px', fontSize: '12px' }} />
            </PieChart>
          </ResponsiveContainer>
          <div className="text-center text-sm text-neutral-400 mt-2">
            Total Cost: {formatCurrency(data.total_execution_cost)}
          </div>
        </div>

        {/* Recent Routing Decisions */}
        <div className="lg:col-span-2 card">
          <div className="card-header">Recent Routing Decisions</div>
          <DataTable columns={routingColumns} data={data.recent_routing} maxRows={10} />
        </div>
      </div>
    </div>
  )
}
