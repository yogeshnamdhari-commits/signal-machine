/**
 * LiveSignalFeed — Real-time incoming signal stream.
 *
 * Displays signals as they arrive via WebSocket with:
 * - Animated entry (slide-in)
 * - Color-coded by side (LONG/SHORT)
 * - Confidence badge
 * - Source icon
 * - Pulse effect on new arrivals
 */
import React, { useEffect, useState } from 'react'

interface Signal {
  id?: string
  symbol: string
  side: string
  confidence: number
  source: string
  entry_price: number
  stop_loss: number
  take_profit: number
  risk_reward: number
  reasoning?: string
  timestamp?: number
  factors?: Record<string, any>
}

interface LiveSignalFeedProps {
  signals: Signal[]
  maxDisplay?: number
}

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
  absorption: '🧽',
  sweep: '🧹',
  liquidation_cascade: '⚡',
  funding_flip: '💱',
  oi_surge: '📈',
  smart_money: '🧠',
}

const SOURCE_COLORS: Record<string, string> = {
  volume_spike: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  momentum: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
  mean_reversion: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  breakout: 'bg-red-500/20 text-red-400 border-red-500/30',
  order_flow: 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30',
  institutional_flow: 'bg-indigo-500/20 text-indigo-400 border-indigo-500/30',
  liquidity_grab: 'bg-teal-500/20 text-teal-400 border-teal-500/30',
  delta_divergence: 'bg-pink-500/20 text-pink-400 border-pink-500/30',
  regime_shift: 'bg-orange-500/20 text-orange-400 border-orange-500/30',
}

export const LiveSignalFeed: React.FC<LiveSignalFeedProps> = ({ signals, maxDisplay = 15 }) => {
  const [newIds, setNewIds] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (signals.length > 0) {
      const latestId = signals[0].id || String(signals[0].timestamp)
      setNewIds((prev) => {
        const next = new Set(prev)
        next.add(latestId)
        return next
      })
      // Remove "new" state after 3 seconds
      const timer = setTimeout(() => {
        setNewIds((prev) => {
          const next = new Set(prev)
          next.delete(latestId)
          return next
        })
      }, 3000)
      return () => clearTimeout(timer)
    }
  }, [signals.length])

  const displaySignals = signals.slice(0, maxDisplay)

  if (displaySignals.length === 0) {
    return (
      <div className="card">
        <div className="card-header flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2 w-2 bg-accent"></span>
          </span>
          Live Signal Feed
        </div>
        <div className="flex items-center justify-center h-32 text-neutral-500 text-sm">
          <div className="text-center">
            <div className="text-2xl mb-2">📡</div>
            <div>Waiting for signals...</div>
            <div className="text-xs text-neutral-600 mt-1">Signals appear in real-time as they're generated</div>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="card">
      <div className="card-header flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="relative flex h-2 w-2">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-profit opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2 w-2 bg-profit"></span>
          </span>
          Live Signal Feed
        </div>
        <span className="text-xs text-neutral-500 font-mono">{signals.length} total</span>
      </div>

      <div className="space-y-2 max-h-[500px] overflow-y-auto pr-1">
        {displaySignals.map((sig, i) => {
          const isNew = newIds.has(sig.id || String(sig.timestamp))
          const isLong = sig.side === 'LONG'
          const confColor = sig.confidence >= 80 ? 'text-profit' : sig.confidence >= 65 ? 'text-warning' : 'text-neutral-400'
          const sourceIcon = SOURCE_ICONS[sig.source] || '📡'
          const sourceColor = SOURCE_COLORS[sig.source] || 'bg-neutral-700/40 text-neutral-400 border-neutral-600/30'

          return (
            <div
              key={sig.id || i}
              className={`
                p-3 rounded-lg border transition-all duration-500
                ${isNew
                  ? 'animate-slide-up border-accent/40 bg-accent/5 shadow-lg shadow-accent/10'
                  : 'border-neutral-700/30 bg-surface-overlay/30 hover:bg-surface-overlay/50'
                }
              `}
            >
              {/* Header row */}
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className={`text-xs font-bold px-2 py-0.5 rounded ${
                    isLong ? 'bg-profit/20 text-profit' : 'bg-loss/20 text-loss'
                  }`}>
                    {sig.side}
                  </span>
                  <span className="font-semibold text-white text-sm">{sig.symbol}</span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded border ${sourceColor}`}>
                    {sourceIcon} {sig.source.replace(/_/g, ' ')}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`text-xs font-mono font-bold ${confColor}`}>
                    {sig.confidence.toFixed(0)}%
                  </span>
                  {isNew && (
                    <span className="text-[10px] bg-accent/30 text-accent px-1.5 py-0.5 rounded animate-pulse">
                      NEW
                    </span>
                  )}
                </div>
              </div>

              {/* Price levels */}
              <div className="grid grid-cols-4 gap-2 text-xs">
                <div>
                  <div className="text-neutral-500">Entry</div>
                  <div className="font-mono text-white">${sig.entry_price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 8 })}</div>
                </div>
                <div>
                  <div className="text-neutral-500">Stop Loss</div>
                  <div className="font-mono text-loss">${sig.stop_loss.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 8 })}</div>
                </div>
                <div>
                  <div className="text-neutral-500">Take Profit</div>
                  <div className="font-mono text-profit">${sig.take_profit.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 8 })}</div>
                </div>
                <div>
                  <div className="text-neutral-500">R:R</div>
                  <div className="font-mono text-accent">{sig.risk_reward.toFixed(1)}x</div>
                </div>
              </div>

              {/* Reasoning */}
              {sig.reasoning && (
                <div className="mt-2 text-[11px] text-neutral-400 italic border-t border-neutral-700/30 pt-2">
                  {sig.reasoning}
                </div>
              )}

              {/* Confidence bar */}
              <div className="mt-2 flex items-center gap-2">
                <div className="flex-1 bg-neutral-700/50 rounded-full h-1">
                  <div
                    className={`h-1 rounded-full transition-all duration-1000 ${
                      sig.confidence >= 80 ? 'bg-profit' : sig.confidence >= 65 ? 'bg-warning' : 'bg-neutral-500'
                    }`}
                    style={{ width: `${sig.confidence}%` }}
                  />
                </div>
                <span className="text-[10px] text-neutral-500 font-mono w-16 text-right">
                  {sig.timestamp ? new Date(sig.timestamp * 1000).toLocaleTimeString() : ''}
                </span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
