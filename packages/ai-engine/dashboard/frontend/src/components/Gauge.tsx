/**
 * Gauge — Circular gauge component for metrics like risk, health, etc.
 */
import React from 'react'
import { clsx } from 'clsx'
import { clamp } from '../utils/format'

interface GaugeProps {
  value: number
  max?: number
  label: string
  unit?: string
  size?: 'sm' | 'md' | 'lg'
  color?: 'profit' | 'loss' | 'warning' | 'info' | 'accent'
  className?: string
}

export const Gauge: React.FC<GaugeProps> = ({
  value: rawValue,
  max = 100,
  label,
  unit = '%',
  size = 'md',
  color = 'accent',
  className,
}) => {
  const value = rawValue ?? 0
  const pct = clamp((value / max) * 100, 0, 100)
  const radius = size === 'lg' ? 70 : size === 'md' ? 50 : 35
  const stroke = size === 'lg' ? 10 : size === 'md' ? 8 : 6
  const circumference = 2 * Math.PI * radius
  const dashOffset = circumference - (pct / 100) * circumference

  const colorMap: Record<string, string> = {
    profit: '#00ff88',
    loss: '#ff4444',
    warning: '#f59e0b',
    info: '#3b82f6',
    accent: '#4a9eff',
  }

  const svgSize = (radius + stroke) * 2

  return (
    <div className={clsx('flex flex-col items-center gap-1', className)}>
      <svg width={svgSize} height={svgSize} className="transform -rotate-90">
        <circle
          cx={radius + stroke}
          cy={radius + stroke}
          r={radius}
          fill="none"
          stroke="#1a1a3a"
          strokeWidth={stroke}
        />
        <circle
          cx={radius + stroke}
          cy={radius + stroke}
          r={radius}
          fill="none"
          stroke={colorMap[color]}
          strokeWidth={stroke}
          strokeDasharray={circumference}
          strokeDashoffset={dashOffset}
          strokeLinecap="round"
          className="transition-all duration-700 ease-out"
        />
      </svg>
      <div className="absolute flex flex-col items-center justify-center" style={{ width: svgSize, height: svgSize }}>
        <span className="text-lg font-bold font-mono text-white">
          {value.toFixed(1)}
        </span>
        <span className="text-[10px] text-neutral-400">{unit}</span>
      </div>
      <span className="text-xs text-neutral-400 mt-1">{label}</span>
    </div>
  )
}
