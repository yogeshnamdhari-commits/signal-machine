import { Signal } from '../types';
import { TrendingUp, TrendingDown, Clock, Target, AlertTriangle } from 'lucide-react';
import { formatDistanceToNow } from 'date-fns';

interface SignalCardProps {
  signal: Signal;
  onClick?: () => void;
}

export default function SignalCard({ signal, onClick }: SignalCardProps) {
  const isBuy = signal.type === 'buy';
  const confidencePercent = Math.round(signal.confidence * 100);

  return (
    <div
      onClick={onClick}
      className={`p-4 rounded-xl border cursor-pointer transition-all duration-200 hover:scale-[1.02] ${
        isBuy
          ? 'bg-success/10 border-success/30 hover:border-success/50'
          : 'bg-danger/10 border-danger/30 hover:border-danger/50'
      }`}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center space-x-2">
          <div
            className={`flex items-center justify-center w-8 h-8 rounded-lg ${
              isBuy ? 'bg-success' : 'bg-danger'
            }`}
          >
            {isBuy ? (
              <TrendingUp className="w-5 h-5 text-white" />
            ) : (
              <TrendingDown className="w-5 h-5 text-white" />
            )}
          </div>
          <div>
            <h3 className="font-bold text-white">{signal.symbol}</h3>
            <p className={`text-xs font-medium ${isBuy ? 'text-success' : 'text-danger'}`}>
              {signal.type.toUpperCase()}
            </p>
          </div>
        </div>
        <span className={`px-2 py-1 text-xs rounded-full ${
          signal.status === 'active' ? 'bg-primary-600/20 text-primary-400' : 'bg-dark-600 text-dark-300'
        }`}>
          {signal.status}
        </span>
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-dark-400 text-sm">Confidence</span>
          <div className="flex items-center space-x-2">
            <div className="w-20 h-2 bg-dark-700 rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full ${
                  confidencePercent >= 70
                    ? 'bg-success'
                    : confidencePercent >= 40
                    ? 'bg-warning'
                    : 'bg-danger'
                }`}
                style={{ width: `${confidencePercent}%` }}
              />
            </div>
            <span className="text-white text-sm font-medium">{confidencePercent}%</span>
          </div>
        </div>

        <div className="flex items-center justify-between">
          <span className="text-dark-400 text-sm">Entry Price</span>
          <span className="text-white font-mono">${signal.price.toLocaleString()}</span>
        </div>

        {signal.targetPrice && (
          <div className="flex items-center justify-between">
            <span className="text-dark-400 text-sm flex items-center">
              <Target className="w-3 h-3 mr-1" /> Take Profit
            </span>
            <span className="text-success font-mono">${signal.targetPrice.toLocaleString()}</span>
          </div>
        )}

        {signal.stopLoss && (
          <div className="flex items-center justify-between">
            <span className="text-dark-400 text-sm flex items-center">
              <AlertTriangle className="w-3 h-3 mr-1" /> Stop Loss
            </span>
            <span className="text-danger font-mono">${signal.stopLoss.toLocaleString()}</span>
          </div>
        )}

        <div className="flex items-center justify-between pt-2 border-t border-dark-700">
          <div className="flex items-center text-dark-400 text-xs">
            <Clock className="w-3 h-3 mr-1" />
            {formatDistanceToNow(signal.timestamp, { addSuffix: true })}
          </div>
          <div className="flex space-x-1">
            {signal.timeframes.map((tf) => (
              <span key={tf} className="px-1.5 py-0.5 text-xs bg-dark-700 rounded text-dark-300">
                {tf}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
