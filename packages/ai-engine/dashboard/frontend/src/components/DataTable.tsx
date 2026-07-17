/**
 * DataTable — Reusable data table with sorting and filtering.
 */
import React, { useMemo, useState } from 'react'
import { clsx } from 'clsx'

interface Column<T> {
  key: string
  header: string
  render?: (row: T) => React.ReactNode
  sortable?: boolean
  className?: string
}

interface DataTableProps<T> {
  columns: Column<T>[]
  data: T[]
  maxRows?: number
  onRowClick?: (row: T) => void
  className?: string
  emptyMessage?: string
}

export function DataTable<T extends Record<string, any>>({
  columns,
  data,
  maxRows = 50,
  onRowClick,
  className,
  emptyMessage = 'No data available',
}: DataTableProps<T>) {
  const [sortKey, setSortKey] = useState<string | null>(null)
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const sorted = useMemo(() => {
    if (!sortKey) return data.slice(0, maxRows)
    return [...data]
      .sort((a, b) => {
        const va = a[sortKey]
        const vb = b[sortKey]
        if (typeof va === 'number' && typeof vb === 'number') {
          return sortDir === 'asc' ? va - vb : vb - va
        }
        return sortDir === 'asc'
          ? String(va).localeCompare(String(vb))
          : String(vb).localeCompare(String(va))
      })
      .slice(0, maxRows)
  }, [data, sortKey, sortDir, maxRows])

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('desc')
    }
  }

  if (data.length === 0) {
    return (
      <div className={clsx('card text-center text-neutral-500 py-8', className)}>
        {emptyMessage}
      </div>
    )
  }

  return (
    <div className={clsx('overflow-x-auto', className)}>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-neutral-700">
            {columns.map((col) => (
              <th
                key={col.key}
                onClick={() => col.sortable && handleSort(col.key)}
                className={clsx(
                  'px-3 py-2 text-left text-xs font-semibold text-neutral-400 uppercase tracking-wider',
                  col.sortable && 'cursor-pointer hover:text-neutral-200',
                  col.className,
                )}
              >
                <span className="flex items-center gap-1">
                  {col.header}
                  {sortKey === col.key && (
                    <span className="text-accent">{sortDir === 'asc' ? '↑' : '↓'}</span>
                  )}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row, i) => (
            <tr
              key={i}
              onClick={() => onRowClick?.(row)}
              className={clsx(
                'table-row',
                onRowClick && 'cursor-pointer',
              )}
            >
              {columns.map((col) => (
                <td key={col.key} className={clsx('px-3 py-2', col.className)}>
                  {col.render ? col.render(row) : row[col.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
