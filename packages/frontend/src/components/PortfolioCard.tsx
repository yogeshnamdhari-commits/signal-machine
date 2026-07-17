import { Portfolio } from '../types';
import { Wallet, Activity } from 'lucide-react';

interface PortfolioCardProps {
  portfolio: Portfolio;
}

export default function PortfolioCard({ portfolio }: PortfolioCardProps) {
  const pnlColor = portfolio.unrealizedPnL >= 0 ? 'text-success' : 'text-danger';
  const dailyPnlColor = portfolio.dailyPnL >= 0 ? 'text-success' : 'text-danger';
  const drawdown = ((portfolio.peakEquity - portfolio.equity) / portfolio.peakEquity) * 100;

  return (
    <div className="bg-dark-900 rounded-xl border border-dark-700 p-6">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-lg font-semibold text-white flex items-center">
          <Wallet className="w-5 h-5 mr-2 text-primary-400" />
          Portfolio
        </h2>
        <span className="text-xs text-dark-400">Real-time</span>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        <div className="bg-dark-800 rounded-lg p-4">
          <p className="text-dark-400 text-sm mb-1">Balance</p>
          <p className="text-xl font-bold text-white">${portfolio.balance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</p>
        </div>
        <div className="bg-dark-800 rounded-lg p-4">
          <p className="text-dark-400 text-sm mb-1">Equity</p>
          <p className="text-xl font-bold text-white">${portfolio.equity.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</p>
        </div>
        <div className="bg-dark-800 rounded-lg p-4">
          <p className="text-dark-400 text-sm mb-1">Unrealized PnL</p>
          <p className={`text-xl font-bold ${pnlColor}`}>
            {portfolio.unrealizedPnL >= 0 ? '+' : ''}${portfolio.unrealizedPnL.toFixed(2)}
          </p>
        </div>
        <div className="bg-dark-800 rounded-lg p-4">
          <p className="text-dark-400 text-sm mb-1">Daily PnL</p>
          <p className={`text-xl font-bold ${dailyPnlColor}`}>
            {portfolio.dailyPnL >= 0 ? '+' : ''}${portfolio.dailyPnL.toFixed(2)}
          </p>
        </div>
      </div>

      {/* Drawdown Indicator */}
      <div className="mb-6">
        <div className="flex items-center justify-between mb-2">
          <span className="text-dark-400 text-sm">Drawdown</span>
          <span className="text-white text-sm font-medium">{drawdown.toFixed(2)}%</span>
        </div>
        <div className="w-full h-2 bg-dark-700 rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-300 ${
              drawdown > 7 ? 'bg-danger' : drawdown > 4 ? 'bg-warning' : 'bg-success'
            }`}
            style={{ width: `${Math.min(drawdown * 10, 100)}%` }}
          />
        </div>
      </div>

      {/* Open Positions */}
      <div>
        <h3 className="text-sm font-medium text-dark-400 mb-3 flex items-center">
          <Activity className="w-4 h-4 mr-1" />
          Open Positions ({portfolio.positions.length})
        </h3>
        {portfolio.positions.length === 0 ? (
          <div className="text-center py-4 text-dark-500 text-sm">
            No open positions
          </div>
        ) : (
          <div className="space-y-2">
            {portfolio.positions.map((position) => (
              <div
                key={position.id}
                className="flex items-center justify-between bg-dark-800 rounded-lg px-3 py-2"
              >
                <div className="flex items-center space-x-2">
                  <span className={`px-2 py-0.5 text-xs rounded ${
                    position.side === 'LONG' ? 'bg-success/20 text-success' : 'bg-danger/20 text-danger'
                  }`}>
                    {position.side}
                  </span>
                  <span className="font-medium text-white text-sm">{position.symbol}</span>
                </div>
                <div className="text-right">
                  <p className="text-white text-sm font-mono">{position.entryPrice.toFixed(2)}</p>
                  <p className="text-dark-400 text-xs">{position.quantity} @ {position.leverage}x</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
