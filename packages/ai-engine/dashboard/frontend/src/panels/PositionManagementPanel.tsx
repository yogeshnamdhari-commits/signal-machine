/**
 * PositionManagementPanel — Position tracking and management.
 */
import React from 'react'
import { DataTable } from '../components/DataTable'
import { formatCurrency, pnlColor } from '../utils/format'

interface Position {
  position_id: string
  symbol: string
  side: string
  quantity: number
  entry_price: number
  current_price: number
  pnl: number
  pnl_pct: number
  risk_pct: number
  leverage: number
  stop_loss: number
  take_profit: number
  exchange: string
  age_sec: number
  status: string
}

interface PositionPanelProps {
  positions: Position[]
  pendingOrders?: Array<Record<string, any>>
  onAction?: (positionId: string, action: string) => void
}

export const PositionManagementPanel: React.FC<PositionPanelProps> = ({
  positions,
  pendingOrders = [],
  onAction,
}) => {
  const columns = [
    { key: 'symbol', header: 'Symbol', sortable: true },
    { key: 'side', header: 'Side', render: (r: Position) => (
      <span className={r.side === 'LONG' ? 'text-profit' : 'text-loss'}>{r.side}</span>
    )},
    { key: 'quantity', header: 'Size', render: (r: Position) => <span className="font-mono">{(r.quantity ?? 0).toFixed(4)}</span>, sortable: true },
    { key: 'entry_price', header: 'Entry', render: (r: Position) => <span className="font-mono">{formatCurrency(r.entry_price, 4)}</span>, sortable: true },
    { key: 'current_price', header: 'Current', render: (r: Position) => <span className="font-mono">{formatCurrency(r.current_price, 4)}</span>, sortable: true },
    { key: 'pnl', header: 'PnL', render: (r: Position) => (
      <span className={`font-mono font-semibold ${pnlColor(r.pnl)}`}>{formatCurrency(r.pnl)}</span>
    ), sortable: true },
    { key: 'leverage', header: 'Lev', render: (r: Position) => `${r.leverage}x` },
    { key: 'stop_loss', header: 'SL', render: (r: Position) => <span className="font-mono text-loss text-xs">{formatCurrency(r.stop_loss, 4)}</span> },
    { key: 'take_profit', header: 'TP', render: (r: Position) => <span className="font-mono text-profit text-xs">{formatCurrency(r.take_profit, 4)}</span> },
    { key: 'exchange', header: 'Exchange', render: (r: Position) => <span className="capitalize">{r.exchange}</span> },
    { key: 'actions', header: 'Actions', render: (r: Position) => (
      <div className="flex gap-1">
        <button onClick={() => onAction?.(r.position_id, 'close')} className="btn btn-danger text-[10px] px-2 py-0.5">Close</button>
        <button onClick={() => onAction?.(r.position_id, 'move_stop')} className="btn btn-secondary text-[10px] px-2 py-0.5">Move SL</button>
      </div>
    )},
  ]

  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-white">Position Management</h2>

      {/* Summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="card">
          <span className="metric-label">Open Positions</span>
          <div className="metric-value">{positions.length}</div>
        </div>
        <div className="card">
          <span className="metric-label">Total PnL</span>
          <div className={`metric-value ${pnlColor(positions.reduce((s, p) => s + (p.pnl ?? 0), 0))}`}>
            {formatCurrency(positions.reduce((s, p) => s + (p.pnl ?? 0), 0))}
          </div>
        </div>
        <div className="card">
          <span className="metric-label">Long Positions</span>
          <div className="metric-value text-profit">{positions.filter(p => p.side === 'LONG').length}</div>
        </div>
        <div className="card">
          <span className="metric-label">Short Positions</span>
          <div className="metric-value text-loss">{positions.filter(p => p.side === 'SHORT').length}</div>
        </div>
      </div>

      {/* Positions Table */}
      <div className="card">
        <div className="card-header">Open Positions</div>
        <DataTable columns={columns} data={positions} maxRows={25} emptyMessage="No open positions" />
      </div>

      {/* Pending Orders */}
      {pendingOrders.length > 0 && (
        <div className="card">
          <div className="card-header">Pending Orders</div>
          <DataTable
            columns={[
              { key: 'symbol', header: 'Symbol' },
              { key: 'side', header: 'Side' },
              { key: 'order_type', header: 'Type' },
              { key: 'quantity', header: 'Size', sortable: true },
              { key: 'price', header: 'Price', sortable: true },
              { key: 'status', header: 'Status' },
            ]}
            data={pendingOrders}
            maxRows={10}
          />
        </div>
      )}
    </div>
  )
}
