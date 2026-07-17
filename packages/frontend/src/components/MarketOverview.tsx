import { MarketTicker } from '../types';
import { ArrowUpRight, ArrowDownRight, DollarSign, BarChart3 } from 'lucide-react';

interface MarketOverviewProps {
  tickers: MarketTicker[];
  onSelectSymbol: (symbol: string) => void;
  selectedSymbol?: string;
}

export default function MarketOverview({ tickers, onSelectSymbol, selectedSymbol }: MarketOverviewProps) {
  const formatPrice = (price: number) => {
    if (price >= 1000) return `$${price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    return `$${price.toFixed(4)}`;
  };

  const formatVolume = (volume: number) => {
    if (volume >= 1e9) return `$${(volume / 1e9).toFixed(2)}B`;
    if (volume >= 1e6) return `$${(volume / 1e6).toFixed(2)}M`;
    return `$${(volume / 1e3).toFixed(2)}K`;
  };

  return (
    <div className="bg-dark-900 rounded-xl border border-dark-700 p-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-white flex items-center">
          <BarChart3 className="w-5 h-5 mr-2 text-primary-400" />
          Market Overview
        </h2>
        <span className="text-xs text-dark-400">{tickers.length} symbols</span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="text-dark-400 text-xs uppercase border-b border-dark-700">
              <th className="text-left py-2">Symbol</th>
              <th className="text-right py-2">Price</th>
              <th className="text-right py-2">24h Change</th>
              <th className="text-right py-2">Volume</th>
            </tr>
          </thead>
          <tbody>
            {tickers.map((ticker) => {
              const isPositive = (ticker.priceChangePercent || 0) >= 0;
              return (
                <tr
                  key={ticker.symbol}
                  onClick={() => onSelectSymbol(ticker.symbol)}
                  className={`cursor-pointer transition-colors hover:bg-dark-800 ${
                    selectedSymbol === ticker.symbol ? 'bg-primary-600/10' : ''
                  }`}
                >
                  <td className="py-3">
                    <div className="flex items-center space-x-2">
                      <DollarSign className="w-4 h-4 text-primary-400" />
                      <span className="font-medium text-white">{ticker.symbol.replace('USDT', '')}</span>
                      <span className="text-dark-500 text-xs">/USDT</span>
                    </div>
                  </td>
                  <td className="text-right py-3 font-mono text-white">
                    {formatPrice(ticker.price)}
                  </td>
                  <td className="text-right py-3">
                    <div className={`flex items-center justify-end space-x-1 ${
                      isPositive ? 'text-success' : 'text-danger'
                    }`}>
                      {isPositive ? (
                        <ArrowUpRight className="w-4 h-4" />
                      ) : (
                        <ArrowDownRight className="w-4 h-4" />
                      )}
                      <span className="font-medium">
                        {Math.abs(ticker.priceChangePercent || 0).toFixed(2)}%
                      </span>
                    </div>
                  </td>
                  <td className="text-right py-3 text-dark-300 font-mono text-sm">
                    {formatVolume(ticker.quoteVolume || ticker.volume * ticker.price)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
