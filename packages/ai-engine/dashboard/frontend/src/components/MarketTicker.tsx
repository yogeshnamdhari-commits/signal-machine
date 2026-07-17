/**
 * MarketTicker — Live market data ticker bar.
 *
 * Shows real-time prices for top symbols in a horizontal scrolling ticker.
 * Displays: symbol, price, change%, volume — color-coded for gains/losses.
 */
import React, { useEffect, useRef } from 'react'

interface MarketTick {
  symbol: string
  base: string
  category: string
  price: number
  volume_24h: number
  change_1m: number
  change_5m: number
  change_1h: number
  change_24h: number
  regime: string
  funding_rate: number
  open_interest: number
  trade_count: number
  timestamp: number
}

interface MarketTickerProps {
  ticks: MarketTick[]
  overview?: {
    total_volume_24h: number
    avg_change_24h: number
    gainers: number
    losers: number
    market_regime: string
    regime_distribution: Record<string, number>
    symbols_tracked: number
  } | null
}

const REGIME_BADGE: Record<string, { icon: string; color: string }> = {
  trending_up: { icon: '📈', color: 'text-profit bg-profit/10' },
  trending_down: { icon: '📉', color: 'text-loss bg-loss/10' },
  ranging: { icon: '↔️', color: 'text-neutral-400 bg-neutral-700/30' },
  volatile: { icon: '⚡', color: 'text-orange-400 bg-orange-500/10' },
  breakout: { icon: '💥', color: 'text-purple-400 bg-purple-500/10' },
}

function formatPrice(price: number): string {
  if (price >= 10000) return price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  if (price >= 100) return price.toFixed(2)
  if (price >= 1) return price.toFixed(3)
  return price.toFixed(4)
}

function formatVolume(vol: number): string {
  if (vol >= 1_000_000_000) return `$${(vol / 1_000_000_000).toFixed(1)}B`
  if (vol >= 1_000_000) return `$${(vol / 1_000_000).toFixed(0)}M`
  if (vol >= 1_000) return `$${(vol / 1_000).toFixed(0)}K`
  return `$${vol.toFixed(0)}`
}

export const MarketTicker: React.FC<MarketTickerProps> = ({ ticks, overview }) => {
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll effect
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const interval = setInterval(() => {
      if (el.scrollLeft >= el.scrollWidth - el.clientWidth) {
        el.scrollLeft = 0
      } else {
        el.scrollLeft += 1
      }
    }, 30)
    return () => clearInterval(interval)
  }, [])

  if (!ticks || ticks.length === 0) return null

  return (
    <div className="card p-0 overflow-hidden">
      {/* Market Overview Bar */}
      {overview && (
        <div className="flex items-center justify-between px-4 py-2 bg-surface-overlay border-b border-neutral-700/30">
          <div className="flex items-center gap-4 text-xs">
            <span className="text-neutral-500">Market</span>
            <span className="flex items-center gap-1">
              <span className="text-profit font-mono">▲ {overview.gainers}</span>
              <span className="text-neutral-600">/</span>
              <span className="text-loss font-mono">▼ {overview.losers}</span>
            </span>
            <span className="text-neutral-500">|</span>
            <span className="text-neutral-400">
              Vol: <span className="font-mono text-white">{formatVolume(overview.total_volume_24h)}</span>
            </span>
            <span className="text-neutral-500">|</span>
            <span className="text-neutral-400">
              Avg: <span className={`font-mono ${overview.avg_change_24h >= 0 ? 'text-profit' : 'text-loss'}`}>
                {overview.avg_change_24h >= 0 ? '+' : ''}{overview.avg_change_24h.toFixed(2)}%
              </span>
            </span>
          </div>
          <div className="flex items-center gap-2">
            {Object.entries(overview.regime_distribution || {}).map(([regime, count]) => {
              const badge = REGIME_BADGE[regime] || REGIME_BADGE.ranging
              return (
                <span key={regime} className={`text-[10px] px-2 py-0.5 rounded-full ${badge.color}`}>
                  {badge.icon} {count}
                </span>
              )
            })}
            <span className="text-[10px] text-neutral-600 font-mono">{overview.symbols_tracked} pairs</span>
          </div>
        </div>
      )}

      {/* Scrolling Ticker */}
      <div ref={scrollRef} className="flex gap-0 overflow-x-auto scrollbar-hide">
        {ticks.map((tick, i) => {
          const changeColor = tick.change_24h >= 0 ? 'text-profit' : 'text-loss'
          const changeBg = tick.change_24h >= 0 ? 'bg-profit/5' : 'bg-loss/5'
          const badge = REGIME_BADGE[tick.regime] || REGIME_BADGE.ranging

          return (
            <div
              key={tick.symbol}
              className={`flex items-center gap-3 px-4 py-2.5 border-r border-neutral-700/20 ${changeBg} min-w-[200px] flex-shrink-0 hover:bg-surface-overlay transition-colors`}
            >
              {/* Symbol */}
              <div className="flex flex-col">
                <span className="text-xs font-semibold text-white">{tick.base}</span>
                <span className="text-[10px] text-neutral-500">{tick.category}</span>
              </div>

              {/* Price */}
              <div className="flex flex-col items-end">
                <span className="text-xs font-mono text-white font-medium">${formatPrice(tick.price)}</span>
                <span className={`text-[10px] font-mono ${changeColor}`}>
                  {tick.change_24h >= 0 ? '+' : ''}{tick.change_24h.toFixed(2)}%
                </span>
              </div>

              {/* Mini changes */}
              <div className="flex flex-col items-end text-[9px] font-mono">
                <span className={tick.change_1m >= 0 ? 'text-profit/60' : 'text-loss/60'}>
                  1m {tick.change_1m >= 0 ? '+' : ''}{tick.change_1m.toFixed(2)}%
                </span>
                <span className={tick.change_1h >= 0 ? 'text-profit/60' : 'text-loss/60'}>
                  1h {tick.change_1h >= 0 ? '+' : ''}{tick.change_1h.toFixed(2)}%
                </span>
              </div>

              {/* Volume */}
              <div className="flex flex-col items-end">
                <span className="text-[10px] text-neutral-400 font-mono">{formatVolume(tick.volume_24h)}</span>
                <span className={`text-[9px] px-1 rounded ${badge.color}`}>{badge.icon}</span>
              </div>
            </div>
          )
        })}
        {/* Duplicate for seamless scroll */}
        {ticks.map((tick, i) => {
          const changeColor = tick.change_24h >= 0 ? 'text-profit' : 'text-loss'
          const changeBg = tick.change_24h >= 0 ? 'bg-profit/5' : 'bg-loss/5'
          const badge = REGIME_BADGE[tick.regime] || REGIME_BADGE.ranging

          return (
            <div
              key={`dup-${tick.symbol}`}
              className={`flex items-center gap-3 px-4 py-2.5 border-r border-neutral-700/20 ${changeBg} min-w-[200px] flex-shrink-0 hover:bg-surface-overlay transition-colors`}
            >
              <div className="flex flex-col">
                <span className="text-xs font-semibold text-white">{tick.base}</span>
                <span className="text-[10px] text-neutral-500">{tick.category}</span>
              </div>
              <div className="flex flex-col items-end">
                <span className="text-xs font-mono text-white font-medium">${formatPrice(tick.price)}</span>
                <span className={`text-[10px] font-mono ${changeColor}`}>
                  {tick.change_24h >= 0 ? '+' : ''}{tick.change_24h.toFixed(2)}%
                </span>
              </div>
              <div className="flex flex-col items-end text-[9px] font-mono">
                <span className={tick.change_1m >= 0 ? 'text-profit/60' : 'text-loss/60'}>
                  1m {tick.change_1m >= 0 ? '+' : ''}{tick.change_1m.toFixed(2)}%
                </span>
                <span className={tick.change_1h >= 0 ? 'text-profit/60' : 'text-loss/60'}>
                  1h {tick.change_1h >= 0 ? '+' : ''}{tick.change_1h.toFixed(2)}%
                </span>
              </div>
              <div className="flex flex-col items-end">
                <span className="text-[10px] text-neutral-400 font-mono">{formatVolume(tick.volume_24h)}</span>
                <span className={`text-[9px] px-1 rounded ${badge.color}`}>{badge.icon}</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
