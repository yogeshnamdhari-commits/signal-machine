/**
 * App — Main Dashboard Application.
 *
 * Institutional trading operations center with:
 * - Sidebar navigation
 * - Real-time WebSocket data
 * - All 10+ panels
 * - RBAC-aware UI
 */
import React, { useState } from 'react'
import { useDashboardWS } from './hooks/useWebSocket'
import { ExecutiveOverview } from './panels/ExecutiveOverview'
import { MultiExchangePanel } from './panels/MultiExchangePanel'
import { RiskManagementPanel } from './panels/RiskManagementPanel'
import { ExecutionMonitorPanel } from './panels/ExecutionMonitorPanel'
import { SignalIntelligencePanel } from './panels/SignalIntelligencePanel'
import { CapitalAllocationPanel } from './panels/CapitalAllocationPanel'
import { ArbitragePanel } from './panels/ArbitragePanel'
import { SystemHealthPanel } from './panels/SystemHealthPanel'
import { AlertCenter } from './panels/AlertCenter'
import { PositionManagementPanel } from './panels/PositionManagementPanel'
import { StatusBadge } from './components/StatusBadge'
import { MarketTicker } from './components/MarketTicker'

type Panel =
  | 'executive'
  | 'exchanges'
  | 'portfolio'
  | 'positions'
  | 'signals'
  | 'allocation'
  | 'risk'
  | 'arbitrage'
  | 'execution'
  | 'health'
  | 'alerts'

const NAV_ITEMS: { id: Panel; label: string; icon: string }[] = [
  { id: 'executive', label: 'Executive', icon: '📊' },
  { id: 'exchanges', label: 'Exchanges', icon: '🏦' },
  { id: 'portfolio', label: 'Portfolio', icon: '📈' },
  { id: 'positions', label: 'Positions', icon: '📍' },
  { id: 'signals', label: 'Signals', icon: '📡' },
  { id: 'allocation', label: 'Allocation', icon: '💰' },
  { id: 'risk', label: 'Risk', icon: '🛡️' },
  { id: 'arbitrage', label: 'Arbitrage', icon: '⚡' },
  { id: 'execution', label: 'Execution', icon: '🎯' },
  { id: 'health', label: 'Health', icon: '💻' },
  { id: 'alerts', label: 'Alerts', icon: '🔔' },
]

export default function App() {
  const [activePanel, setActivePanel] = useState<Panel>('executive')
  const { connected, channelData, latency } = useDashboardWS()

  const renderPanel = () => {
    switch (activePanel) {
      case 'executive':
        return (
          <ExecutiveOverview
            data={channelData.portfolio || null}
            equityHistory={channelData.portfolio?.equity_history}
          />
        )
      case 'exchanges':
        return <MultiExchangePanel data={channelData.exchanges || null} />
      case 'portfolio':
        return <CapitalAllocationPanel data={channelData.allocation || null} />
      case 'positions':
        return <PositionManagementPanel positions={channelData.positions?.positions || []} />
      case 'signals':
        return (
          <SignalIntelligencePanel
            data={channelData.signals || null}
            liveSignals={channelData.live_signals || []}
            signalStats={channelData.signal_stats || null}
          />
        )
      case 'allocation':
        return <CapitalAllocationPanel data={channelData.allocation || null} />
      case 'risk':
        return <RiskManagementPanel data={channelData.risk || null} />
      case 'arbitrage':
        return <ArbitragePanel data={channelData.arbitrage || null} />
      case 'execution':
        return <ExecutionMonitorPanel data={channelData.execution || null} />
      case 'health':
        return (
          <SystemHealthPanel
            data={channelData.health || null}
            history={channelData.health?.history || []}
          />
        )
      case 'alerts':
        return (
          <AlertCenter
            alerts={channelData.alerts?.alerts || []}
            stats={channelData.alerts?.stats || null}
          />
        )
      default:
        return <ExecutiveOverview data={channelData.portfolio || null} />
    }
  }

  return (
    <div className="flex h-screen overflow-hidden bg-surface">
      {/* Sidebar */}
      <aside className="w-56 flex-shrink-0 bg-surface-raised border-r border-neutral-700/50 flex flex-col">
        {/* Logo */}
        <div className="px-4 py-4 border-b border-neutral-700/50">
          <div className="flex items-center gap-2">
            <span className="text-xl">⚡</span>
            <div>
              <div className="text-sm font-bold text-white">DeltaTerminal</div>
              <div className="text-[10px] text-neutral-500">Institutional Dashboard</div>
            </div>
          </div>
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-2 overflow-y-auto">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              onClick={() => setActivePanel(item.id)}
              className={`w-full flex items-center gap-2.5 px-4 py-2 text-sm transition-colors ${
                activePanel === item.id
                  ? 'bg-accent/10 text-accent border-r-2 border-accent'
                  : 'text-neutral-400 hover:text-neutral-200 hover:bg-surface-overlay'
              }`}
            >
              <span>{item.icon}</span>
              <span>{item.label}</span>
              {item.id === 'alerts' && channelData.alerts?.stats?.unread_count > 0 && (
                <span className="ml-auto bg-loss text-white text-[10px] font-bold rounded-full w-4 h-4 flex items-center justify-center">
                  {Math.min(channelData.alerts.stats.unread_count, 9)}
                </span>
              )}
            </button>
          ))}
        </nav>

        {/* Footer */}
        <div className="px-4 py-3 border-t border-neutral-700/50">
          <div className="flex items-center justify-between">
            <StatusBadge
              status={connected ? 'online' : 'offline'}
              label={connected ? 'Live' : 'Offline'}
              pulse={connected}
            />
            <span className="text-[10px] font-mono text-neutral-500">
              {latency}ms
            </span>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-y-auto p-4 lg:p-6 space-y-4">
        {/* Market Ticker Bar */}
        <MarketTicker
          ticks={channelData.market_ticker?.ticks || []}
          overview={channelData.market_ticker?.overview || null}
        />
        {renderPanel()}
      </main>
    </div>
  )
}
