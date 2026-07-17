/**
 * StatusBadge — Displays a status indicator with label.
 */
import React from 'react'
import { clsx } from 'clsx'

interface StatusBadgeProps {
  status: 'online' | 'offline' | 'warning' | 'error' | string
  label?: string
  pulse?: boolean
  className?: string
}

export const StatusBadge: React.FC<StatusBadgeProps> = ({
  status,
  label,
  pulse = false,
  className,
}) => {
  const colorMap: Record<string, string> = {
    online: 'bg-profit',
    connected: 'bg-profit',
    healthy: 'bg-profit',
    active: 'bg-profit',
    offline: 'bg-neutral-500',
    disconnected: 'bg-neutral-500',
    warning: 'bg-warning',
    elevated: 'bg-warning',
    error: 'bg-loss',
    critical: 'bg-loss',
  }

  return (
    <span className={clsx('inline-flex items-center gap-1.5', className)}>
      <span
        className={clsx(
          'w-2 h-2 rounded-full',
          colorMap[status] || 'bg-neutral-500',
          pulse && 'animate-pulse-slow',
        )}
      />
      {label && (
        <span className="text-xs text-neutral-300 capitalize">{label || status}</span>
      )}
    </span>
  )
}
