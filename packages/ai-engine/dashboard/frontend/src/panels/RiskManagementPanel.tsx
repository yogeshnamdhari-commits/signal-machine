/**
 * RiskManagementPanel — Portfolio risk management display.
 */
import React from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
} from 'recharts'
import { MetricCard } from '../components/MetricCard'
import { Gauge } from '../components/Gauge'
import { formatPercent, riskLevelColor } from '../utils/format'

interface RiskData {
  portfolio_risk_pct: number
  current_exposure: number
  net_exposure: number
  gross_exposure: number
  var_95: number
  cvar_95: number
  drawdown_pct: number
  max_drawdown_pct: number
  risk_of_ruin: number
  margin_utilization_pct: number
  risk_level: string
  exchange_risk: Record<string, number>
  symbol_risk: Record<string, number>
  stress_tests: Array<{
    scenario: string
    equity_impact: number
    drawdown_pct: number
    risk_rating: string
    estimated_recovery_days: number
  }>
}

interface RiskPanelProps {
  data: RiskData | null
}

const COLORS = ['#4a9eff', '#00ff88', '#f59e0b', '#ff4444', '#8b5cf6', '#ec4899']

export const RiskManagementPanel: React.FC<RiskPanelProps> = ({ data }) => {
  if (!data) return <div className="card animate-pulse h-64 bg-neutral-800 rounded-lg" />

  const exchangeData = Object.entries(data.exchange_risk).map(([name, value]) => ({
    name,
    value,
  }))
  const symbolData = Object.entries(data.symbol_risk).map(([name, value]) => ({
    name,
    value,
  }))

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">Risk Management</h2>
        <span className={`badge ${data.risk_level === 'NORMAL' ? 'badge-profit' : data.risk_level === 'CRITICAL' ? 'badge-loss' : 'badge-warning'}`}>
          {data.risk_level}
        </span>
      </div>

      {/* Risk Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
        <MetricCard label="Current Exposure" value={`$${((data.current_exposure ?? 0) / 1000).toFixed(1)}K`} />
        <MetricCard label="VaR (95%)" value={formatPercent(data.var_95 ?? 0)} deltaColor="loss" />
        <MetricCard label="CVaR (95%)" value={formatPercent(data.cvar_95 ?? 0)} deltaColor="loss" />
        <MetricCard label="Drawdown" value={formatPercent(data.drawdown_pct ?? 0)} deltaColor={(data.drawdown_pct ?? 0) > 5 ? 'loss' : 'neutral'} />
        <MetricCard label="Max Drawdown" value={formatPercent(data.max_drawdown_pct ?? 0)} deltaColor="loss" />
        <MetricCard label="Margin Usage" value={`${(data.margin_utilization_pct ?? 0).toFixed(1)}%`} />
      </div>

      {/* Charts Row */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Exchange Risk */}
        <div className="card">
          <div className="card-header">Exchange Risk Distribution</div>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={exchangeData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1a1a3a" />
              <XAxis dataKey="name" tick={{ fontSize: 10, fill: '#666' }} />
              <YAxis tick={{ fontSize: 10, fill: '#666' }} />
              <Tooltip contentStyle={{ background: '#12122a', border: '1px solid #2a2a4e', borderRadius: '8px', fontSize: '12px' }} />
              <Bar dataKey="value" fill="#4a9eff" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Symbol Risk Pie */}
        <div className="card">
          <div className="card-header">Symbol Risk Allocation</div>
          <ResponsiveContainer width="100%" height={200}>
            <PieChart>
              <Pie data={symbolData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={70} label={({ name, percent }) => `${name} ${((percent ?? 0) * 100).toFixed(0)}%`}>
                {symbolData.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip contentStyle={{ background: '#12122a', border: '1px solid #2a2a4e', borderRadius: '8px', fontSize: '12px' }} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Stress Tests */}
      {data.stress_tests && data.stress_tests.length > 0 && (
        <div className="card">
          <div className="card-header">Stress Test Results</div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
            {data.stress_tests.map((test, i) => (
              <div key={i} className="bg-surface-overlay rounded-lg p-3 border border-neutral-700/30">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-medium text-white">{test.scenario}</span>
                  <span className={`badge ${test.risk_rating === 'LOW' ? 'badge-profit' : test.risk_rating === 'MEDIUM' ? 'badge-warning' : 'badge-loss'}`}>
                    {test.risk_rating}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-1 text-xs">
                  <span className="text-neutral-500">Impact:</span>
                  <span className="text-loss font-mono">${(test.equity_impact ?? 0).toFixed(0)}</span>
                  <span className="text-neutral-500">Drawdown:</span>
                  <span className="text-neutral-300 font-mono">{(test.drawdown_pct ?? 0).toFixed(1)}%</span>
                  <span className="text-neutral-500">Recovery:</span>
                  <span className="text-neutral-300 font-mono">{(test.estimated_recovery_days ?? 0).toFixed(0)}d</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
