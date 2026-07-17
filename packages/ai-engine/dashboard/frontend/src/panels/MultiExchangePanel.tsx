/**
 * MultiExchangePanel — Multi-exchange status and health display.
 */
import React from 'react'
import { MetricCard } from '../components/MetricCard'
import { StatusBadge } from '../components/StatusBadge'
import { Gauge } from '../components/Gauge'
import { formatCurrency, formatLatency, statusColor } from '../utils/format'

interface ExchangeData {
  name: string
  balance: number
  available_margin: number
  used_margin: number
  open_positions: number
  open_orders: number
  funding_paid: number
  funding_received: number
  latency_ms: number
  avg_latency_ms: number
  api_status: string
  ws_status: string
  error_count: number
  reconnect_count: number
  health_score: number
}

interface MultiExchangePanelProps {
  data: {
    exchanges: Record<string, ExchangeData>
    total_exchanges: number
    connected_count: number
    avg_health: number
  } | null
}

export const MultiExchangePanel: React.FC<MultiExchangePanelProps> = ({ data }) => {
  if (!data) return <div className="card animate-pulse h-64 bg-neutral-800 rounded-lg" />

  const exchanges = Object.values(data.exchanges)

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">Multi-Exchange</h2>
        <div className="flex items-center gap-3">
          <span className="text-xs text-neutral-400">
            {data.connected_count}/{data.total_exchanges} connected
          </span>
          <span className="badge badge-info">Avg Health: {(data.avg_health ?? 0).toFixed(0)}%</span>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {exchanges.map((exch) => (
          <div key={exch.name} className="card space-y-3">
            {/* Header */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-base font-semibold text-white capitalize">{exch.name}</span>
                <StatusBadge status={exch.api_status} pulse={exch.api_status === 'connected'} />
              </div>
              <Gauge value={exch.health_score} size="sm" label="" color={
                exch.health_score > 80 ? 'profit' : exch.health_score > 50 ? 'warning' : 'loss'
              } />
            </div>

            {/* Balances */}
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div>
                <span className="text-neutral-500 text-xs">Balance</span>
                <div className="font-mono text-white">{formatCurrency(exch.balance)}</div>
              </div>
              <div>
                <span className="text-neutral-500 text-xs">Available</span>
                <div className="font-mono text-white">{formatCurrency(exch.available_margin)}</div>
              </div>
              <div>
                <span className="text-neutral-500 text-xs">Positions</span>
                <div className="font-mono text-white">{exch.open_positions}</div>
              </div>
              <div>
                <span className="text-neutral-500 text-xs">Orders</span>
                <div className="font-mono text-white">{exch.open_orders}</div>
              </div>
            </div>

            {/* Funding */}
            <div className="grid grid-cols-2 gap-2 text-sm">
              <div>
                <span className="text-neutral-500 text-xs">Funding Paid</span>
                <div className="font-mono text-loss">{formatCurrency(exch.funding_paid)}</div>
              </div>
              <div>
                <span className="text-neutral-500 text-xs">Funding Received</span>
                <div className="font-mono text-profit">{formatCurrency(exch.funding_received)}</div>
              </div>
            </div>

            {/* Performance */}
            <div className="flex items-center justify-between text-xs text-neutral-400 border-t border-neutral-700/30 pt-2">
              <span>Latency: {formatLatency(exch.avg_latency_ms)}</span>
              <span>Errors: {exch.error_count}</span>
              <span>Reconnects: {exch.reconnect_count}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
