/**
 * MetricCard — Displays a single metric with label, value, and optional delta.
 */
import React from 'react'
import { clsx } from 'clsx'

interface MetricCardProps {
  label: string
  value: string | number
  delta?: string
  deltaColor?: 'profit' | 'loss' | 'neutral'
  icon?: React.ReactNode
  className?: string
  glow?: boolean
}

export const MetricCard: React.FC<MetricCardProps> = ({
  label,
  value,
  delta,
  deltaColor = 'neutral',
  icon,
  className,
  glow = false,
}) => {
  return (
    <div
      className={clsx(
        'card flex flex-col gap-1 animate-slide-up',
        glow && 'glow-accent',
        className,
      )}
    >
      <div className="flex items-center justify-between">
        <span className="metric-label">{label}</span>
        {icon && <span className="text-neutral-500">{icon}</span>}
      </div>
      <span className="metric-value">{value}</span>
      {delta && (
        <span
          className={clsx('text-xs font-medium', {
            'text-profit': deltaColor === 'profit',
            'text-loss': deltaColor === 'loss',
            'text-neutral-400': deltaColor === 'neutral',
          })}
        >
          {delta}
        </span>
      )}
    </div>
  )
}
