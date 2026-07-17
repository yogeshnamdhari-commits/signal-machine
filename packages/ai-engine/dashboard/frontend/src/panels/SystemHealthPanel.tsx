/**
 * SystemHealthPanel — System health and infrastructure monitoring.
 */
import React from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import { MetricCard } from '../components/MetricCard'
import { Gauge } from '../components/Gauge'
import { StatusBadge } from '../components/StatusBadge'
import { formatLatency } from '../utils/format'

interface HealthData {
  cpu_pct: number
  cpu_count: number
  memory_mb: number
  memory_total_mb: number
  memory_pct: number
  disk_usage_pct: number
  disk_free_gb: number
  services: Record<string, { status: string; latency_ms: number; last_check: number }>
  exchanges: Record<string, { status: string; ws_connected: boolean; latency_ms: number }>
  error_count: number
  recovery_count: number
  queue_depth: number
  message_rate: number
  uptime_sec: number
  uptime_pct: number
  health_score: number
}

interface HealthPanelProps {
  data: HealthData | null
  history?: Array<Record<string, any>>
}

export const SystemHealthPanel: React.FC<HealthPanelProps> = ({ data, history = [] }) => {
  if (!data) return <div className="card animate-pulse h-64 bg-neutral-800 rounded-lg" />

  const uptimeStr = (() => {
    const h = Math.floor(data.uptime_sec / 3600)
    const m = Math.floor((data.uptime_sec % 3600) / 60)
    if (h > 24) return `${Math.floor(h / 24)}d ${h % 24}h`
    return `${h}h ${m}m`
  })()

  const chartData = history.slice(-100).map((s) => ({
    time: new Date((s.timestamp ?? 0) * 1000).toLocaleTimeString('en-US', { hour12: false }),
    cpu: s.cpu_pct,
    memory: s.memory_pct,
  }))

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">System Health</h2>
        <div className="flex items-center gap-3">
          <StatusBadge status={(data.health_score ?? 0) > 80 ? 'online' : (data.health_score ?? 0) > 50 ? 'warning' : 'error'} label={`Score: ${(data.health_score ?? 0).toFixed(0)}`} pulse />
          <span className="text-xs text-neutral-500">Uptime: {uptimeStr}</span>
        </div>
      </div>

      {/* Resource Gauges */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="card flex items-center justify-center">
          <Gauge value={data.cpu_pct} label="CPU Usage" color={data.cpu_pct < 50 ? 'profit' : data.cpu_pct < 80 ? 'warning' : 'loss'} />
        </div>
        <div className="card flex items-center justify-center">
          <Gauge value={data.memory_pct} label="Memory" color={data.memory_pct < 60 ? 'profit' : data.memory_pct < 85 ? 'warning' : 'loss'} />
        </div>
        <div className="card flex items-center justify-center">
          <Gauge value={data.disk_usage_pct} label="Disk Usage" color={data.disk_usage_pct < 70 ? 'profit' : data.disk_usage_pct < 90 ? 'warning' : 'loss'} />
        </div>
        <div className="card flex items-center justify-center">
          <Gauge value={data.uptime_pct} label="Uptime" color="profit" />
        </div>
      </div>

      {/* Resource History */}
      {chartData.length > 0 && (
        <div className="card">
          <div className="card-header">Resource History</div>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={chartData}>
              <defs>
                <linearGradient id="cpuGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#4a9eff" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="#4a9eff" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="memGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#00ff88" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="#00ff88" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1a1a3a" />
              <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#666' }} />
              <YAxis tick={{ fontSize: 10, fill: '#666' }} domain={[0, 100]} />
              <Tooltip contentStyle={{ background: '#12122a', border: '1px solid #2a2a4e', borderRadius: '8px', fontSize: '12px' }} />
              <Area type="monotone" dataKey="cpu" stroke="#4a9eff" fill="url(#cpuGrad)" name="CPU %" />
              <Area type="monotone" dataKey="memory" stroke="#00ff88" fill="url(#memGrad)" name="Memory %" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Services & Exchanges */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Services */}
        <div className="card">
          <div className="card-header">Services</div>
          <div className="space-y-2">
            {Object.entries(data.services).map(([name, svc]) => (
              <div key={name} className="flex items-center justify-between py-1.5 border-b border-neutral-700/20 last:border-0">
                <div className="flex items-center gap-2">
                  <StatusBadge status={svc.status} />
                  <span className="text-sm text-neutral-200 capitalize">{name}</span>
                </div>
                <span className="text-xs font-mono text-neutral-400">{formatLatency(svc.latency_ms)}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Exchange Status */}
        <div className="card">
          <div className="card-header">Exchange Connections</div>
          <div className="space-y-2">
            {Object.entries(data.exchanges).map(([name, exch]) => (
              <div key={name} className="flex items-center justify-between py-1.5 border-b border-neutral-700/20 last:border-0">
                <div className="flex items-center gap-2">
                  <StatusBadge status={exch.ws_connected ? 'online' : 'offline'} />
                  <span className="text-sm text-neutral-200 capitalize">{name}</span>
                </div>
                <div className="flex items-center gap-3 text-xs font-mono text-neutral-400">
                  <span>WS: {exch.ws_connected ? '✓' : '✗'}</span>
                  <span>{formatLatency(exch.latency_ms)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* System Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <MetricCard label="Errors" value={data.error_count} deltaColor={data.error_count > 0 ? 'loss' : 'neutral'} />
        <MetricCard label="Recoveries" value={data.recovery_count} />
        <MetricCard label="Queue Depth" value={data.queue_depth} />
        <MetricCard label="Message Rate" value={`${(data.message_rate ?? 0).toFixed(1)}/s`} />
      </div>
    </div>
  )
}
