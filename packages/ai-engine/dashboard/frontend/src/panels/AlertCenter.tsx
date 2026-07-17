/**
 * AlertCenter — Alert display and management panel.
 */
import React, { useState } from 'react'
import { MetricCard } from '../components/MetricCard'
import { clsx } from 'clsx'

interface Alert {
  id: string
  level: string
  category: string
  title: string
  message: string
  timestamp: number
  read: boolean
  acknowledged: boolean
}

interface AlertStats {
  total_alerts: number
  unread_count: number
  by_level: Record<string, number>
  by_category: Record<string, number>
}

interface AlertCenterProps {
  alerts: Alert[]
  stats: AlertStats | null
  onAcknowledge?: (id: string) => void
  onMarkAllRead?: () => void
}

const LEVEL_STYLES: Record<string, { icon: string; color: string; bg: string }> = {
  info: { icon: 'ℹ️', color: 'text-info', bg: 'bg-info/10 border-info/30' },
  warning: { icon: '⚠️', color: 'text-warning', bg: 'bg-warning/10 border-warning/30' },
  critical: { icon: '🚨', color: 'text-loss', bg: 'bg-loss/10 border-loss/30' },
  emergency: { icon: '🔴', color: 'text-red-600', bg: 'bg-red-600/10 border-red-600/30' },
}

export const AlertCenter: React.FC<AlertCenterProps> = ({
  alerts,
  stats,
  onAcknowledge,
  onMarkAllRead,
}) => {
  const [levelFilter, setLevelFilter] = useState('all')
  const [categoryFilter, setCategoryFilter] = useState('all')

  const filtered = alerts.filter((a) => {
    if (levelFilter !== 'all' && a.level !== levelFilter) return false
    if (categoryFilter !== 'all' && a.category !== categoryFilter) return false
    return true
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">Alert Center</h2>
        <div className="flex items-center gap-2">
          {stats && stats.unread_count > 0 && (
            <span className="badge badge-loss">{stats.unread_count} unread</span>
          )}
          <button onClick={onMarkAllRead} className="btn btn-secondary text-xs">
            Mark All Read
          </button>
        </div>
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <MetricCard label="Total Alerts" value={stats.total_alerts} />
          <MetricCard label="Unread" value={stats.unread_count} deltaColor={stats.unread_count > 0 ? 'loss' : 'neutral'} />
          <MetricCard label="Critical" value={stats.by_level.critical || 0} deltaColor="loss" />
          <MetricCard label="Warnings" value={stats.by_level.warning || 0} deltaColor="neutral" />
        </div>
      )}

      {/* Filters */}
      <div className="flex items-center gap-2">
        {['all', 'info', 'warning', 'critical', 'emergency'].map((level) => (
          <button
            key={level}
            onClick={() => setLevelFilter(level)}
            className={clsx(
              'btn text-xs',
              levelFilter === level ? 'btn-primary' : 'btn-secondary',
            )}
          >
            {level === 'all' ? 'All' : level.charAt(0).toUpperCase() + level.slice(1)}
          </button>
        ))}
      </div>

      {/* Alert List */}
      <div className="space-y-2 max-h-[600px] overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="card text-center text-neutral-500 py-8">No alerts</div>
        ) : (
          filtered.map((alert) => {
            const style = LEVEL_STYLES[alert.level] || LEVEL_STYLES.info
            return (
              <div
                key={alert.id}
                className={clsx(
                  'border rounded-lg p-3 transition-all',
                  style.bg,
                  alert.read ? 'opacity-60' : 'opacity-100',
                )}
              >
                <div className="flex items-start justify-between">
                  <div className="flex items-start gap-2">
                    <span className="text-lg">{style.icon}</span>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-semibold text-white">{alert.title}</span>
                        <span className="badge bg-neutral-700 text-neutral-300 text-[10px]">{alert.category}</span>
                      </div>
                      <p className="text-xs text-neutral-400 mt-0.5">{alert.message}</p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[10px] text-neutral-500">
                      {new Date((alert.timestamp ?? 0) * 1000).toLocaleTimeString()}
                    </span>
                    {!alert.acknowledged && (
                      <button
                        onClick={() => onAcknowledge?.(alert.id)}
                        className="text-xs text-accent hover:text-accent-hover"
                      >
                        ACK
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
