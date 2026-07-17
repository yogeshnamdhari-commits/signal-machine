/**
 * ExecutiveOverview — Main executive overview panel.
 *
 * Displays:
 * - Total Equity, Daily/Weekly/Monthly PnL
 * - Portfolio health score
 * - Win rate, Sharpe, Sortino, Profit Factor
 * - Drawdown, Risk of Ruin
 * - Equity curve chart
 */
import React, { useMemo } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from 'recharts'
import { MetricCard } from '../components/MetricCard'
import { Gauge } from '../components/Gauge'
import { formatCurrency, formatPercent, pnlColor } from '../utils/format'

interface ExecutiveData {
  total_equity: number
  daily_pnl: number
  weekly_pnl: number
  monthly_pnl: number
  total_pnl: number
  portfolio_value: number
  available_capital: number
  used_capital: number
  current_drawdown: number
  max_drawdown: number
  profit_factor: number
  win_rate: number
  sharpe_ratio: number
  sortino_ratio: number
  expectancy: number
  risk_of_ruin: number
  trade_count: number
  health_score: number
}

interface ExecutiveOverviewProps {
  data: ExecutiveData | null
  equityHistory?: Array<{ timestamp: number; equity: number; drawdown: number }>
}

export const ExecutiveOverview: React.FC<ExecutiveOverviewProps> = ({
  data,
  equityHistory = [],
}) => {
  if (!data) {
    return (
      <div className="card animate-pulse">
        <div className="h-64 bg-neutral-800 rounded-lg" />
      </div>
    )
  }

  const chartData = useMemo(() =>
    equityHistory.slice(-200).map((e) => ({
      time: new Date(e.timestamp * 1000).toLocaleTimeString('en-US', { hour12: false }),
      equity: e.equity,
      drawdown: e.drawdown,
    })),
    [equityHistory],
  )

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">Executive Overview</h2>
        <div className="flex items-center gap-2">
          <span className="badge badge-info">Live</span>
          <span className="text-xs text-neutral-500">
            {new Date().toLocaleTimeString('en-US', { hour12: false })}
          </span>
        </div>
      </div>

      {/* Top Metrics Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
        <MetricCard
          label="Total Equity"
          value={formatCurrency(data.total_equity)}
          delta={formatCurrency(data.total_pnl)}
          deltaColor={data.total_pnl >= 0 ? 'profit' : 'loss'}
          glow
        />
        <MetricCard
          label="Daily PnL"
          value={formatCurrency(data.daily_pnl)}
          deltaColor={data.daily_pnl >= 0 ? 'profit' : 'loss'}
        />
        <MetricCard
          label="Weekly PnL"
          value={formatCurrency(data.weekly_pnl)}
          deltaColor={data.weekly_pnl >= 0 ? 'profit' : 'loss'}
        />
        <MetricCard
          label="Monthly PnL"
          value={formatCurrency(data.monthly_pnl)}
          deltaColor={data.monthly_pnl >= 0 ? 'profit' : 'loss'}
        />
        <MetricCard
          label="Win Rate"
          value={`${(data.win_rate ?? 0).toFixed(1)}%`}
          delta={`${data.trade_count ?? 0} trades`}
        />
        <MetricCard
          label="Sharpe Ratio"
          value={(data.sharpe_ratio ?? 0).toFixed(2)}
          deltaColor={(data.sharpe_ratio ?? 0) >= 1 ? 'profit' : 'loss'}
        />
      </div>

      {/* Equity Curve */}
      {chartData.length > 0 && (
        <div className="card">
          <div className="card-header">Equity Curve</div>
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={chartData}>
              <defs>
                <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#4a9eff" stopOpacity={0.3} />
                  <stop offset="100%" stopColor="#4a9eff" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#1a1a3a" />
              <XAxis dataKey="time" tick={{ fontSize: 10, fill: '#666' }} />
              <YAxis tick={{ fontSize: 10, fill: '#666' }} />
              <Tooltip
                contentStyle={{
                  background: '#12122a',
                  border: '1px solid #2a2a4e',
                  borderRadius: '8px',
                  fontSize: '12px',
                }}
              />
              <Area
                type="monotone"
                dataKey="equity"
                stroke="#4a9eff"
                fill="url(#equityGrad)"
                strokeWidth={2}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Gauges Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="card flex items-center justify-center">
          <Gauge
            value={data.health_score ?? 0}
            label="Portfolio Health"
            color={(data.health_score ?? 0) > 70 ? 'profit' : (data.health_score ?? 0) > 40 ? 'warning' : 'loss'}
          />
        </div>
        <div className="card flex items-center justify-center">
          <Gauge
            value={data.current_drawdown ?? 0}
            max={20}
            label="Current Drawdown"
            color={(data.current_drawdown ?? 0) < 5 ? 'profit' : (data.current_drawdown ?? 0) < 10 ? 'warning' : 'loss'}
          />
        </div>
        <div className="card flex flex-col items-center justify-center gap-2">
          <span className="metric-label">Profit Factor</span>
          <span className={`text-2xl font-bold font-mono ${pnlColor((data.profit_factor ?? 0) - 1)}`}>
            {(data.profit_factor ?? 0).toFixed(2)}
          </span>
          <span className="text-xs text-neutral-500">
            Sortino: {(data.sortino_ratio ?? 0).toFixed(2)}
          </span>
        </div>
        <div className="card flex flex-col items-center justify-center gap-2">
          <span className="metric-label">Risk of Ruin</span>
          <span className={`text-2xl font-bold font-mono ${(data.risk_of_ruin ?? 0) < 0.01 ? 'text-profit' : (data.risk_of_ruin ?? 0) < 0.05 ? 'text-warning' : 'text-loss'}`}>
            {((data.risk_of_ruin ?? 0) * 100).toFixed(4)}%
          </span>
          <span className="text-xs text-neutral-500">
            Expectancy: {formatCurrency(data.expectancy ?? 0)}
          </span>
        </div>
      </div>
    </div>
  )
}
