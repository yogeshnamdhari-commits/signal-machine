/**
 * Format utilities for the dashboard.
 */

export function formatCurrency(value: number, decimals = 2): string {
  if (value == null || isNaN(value)) return '$0.00'
  if (Math.abs(value) >= 1_000_000) {
    return `$${(value / 1_000_000).toFixed(2)}M`
  }
  if (Math.abs(value) >= 1_000) {
    return `$${(value / 1_000).toFixed(1)}K`
  }
  return `$${value.toFixed(decimals)}`
}

export function formatPercent(value: number, decimals = 2): string {
  if (value == null || isNaN(value)) return '0.00%'
  return `${value >= 0 ? '+' : ''}${value.toFixed(decimals)}%`
}

export function formatNumber(value: number, decimals = 2): string {
  if (value == null || isNaN(value)) return '0'
  if (Math.abs(value) >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(2)}M`
  }
  if (Math.abs(value) >= 1_000) {
    return `${(value / 1_000).toFixed(1)}K`
  }
  return value.toFixed(decimals)
}

export function formatLatency(ms: number): string {
  if (ms == null || isNaN(ms)) return '0ms'
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  return `${ms.toFixed(0)}ms`
}

export function formatTime(timestamp: number): string {
  const date = new Date(timestamp * 1000)
  return date.toLocaleTimeString('en-US', { hour12: false })
}

export function formatDateTime(timestamp: number): string {
  const date = new Date(timestamp * 1000)
  return date.toLocaleString('en-US', { hour12: false })
}

export function pnlColor(value: number): string {
  if (value > 0) return 'text-profit'
  if (value < 0) return 'text-loss'
  return 'text-neutral-300'
}

export function pnlBg(value: number): string {
  if (value > 0) return 'bg-profit/10'
  if (value < 0) return 'bg-loss/10'
  return 'bg-neutral-700/10'
}

export function statusColor(status: string): string {
  switch (status.toLowerCase()) {
    case 'connected':
    case 'healthy':
    case 'active':
      return 'text-profit'
    case 'disconnected':
    case 'error':
    case 'critical':
      return 'text-loss'
    case 'warning':
    case 'elevated':
      return 'text-warning'
    default:
      return 'text-neutral-400'
  }
}

export function riskLevelColor(level: string): string {
  switch (level) {
    case 'NORMAL': return 'text-profit'
    case 'ELEVATED': return 'text-warning'
    case 'HIGH': return 'text-orange-500'
    case 'CRITICAL': return 'text-loss'
    case 'BREACH': return 'text-red-600'
    default: return 'text-neutral-400'
  }
}

export function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}
