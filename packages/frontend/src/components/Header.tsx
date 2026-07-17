import { TrendingUp, Zap, Wifi, WifiOff } from 'lucide-react';
import { ConnectionStatus } from '../services/socket';

interface HeaderProps {
  connectionStatus?: ConnectionStatus;
}

export default function Header({ connectionStatus = 'disconnected' }: HeaderProps) {
  const isConnected = connectionStatus === 'connected';
  const isConnecting = connectionStatus === 'connecting';

  const statusColor = isConnected
    ? 'text-success'
    : isConnecting
    ? 'text-warning'
    : 'text-danger';

  const statusLabel = isConnected
    ? 'Connected'
    : isConnecting
    ? 'Connecting…'
    : 'Disconnected';

  return (
    <header className="bg-dark-900 border-b border-dark-700 px-6 py-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center space-x-3">
          <div className="flex items-center justify-center w-10 h-10 bg-primary-600 rounded-lg">
            <Zap className="w-6 h-6 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-white">DeltaTerminal</h1>
            <p className="text-xs text-dark-400">AI-Powered Binance Futures Scanner</p>
          </div>
        </div>
        
        <div className="flex items-center space-x-6">
          <div className="flex items-center space-x-2 text-sm">
            {isConnected ? (
              <Wifi className={`w-4 h-4 ${statusColor}`} />
            ) : (
              <WifiOff className={`w-4 h-4 ${statusColor}`} />
            )}
            <span className={statusColor}>{statusLabel}</span>
          </div>
          <div className="flex items-center space-x-2 text-sm">
            <TrendingUp className="w-4 h-4 text-primary-400" />
            <span className="text-dark-300">Real-time Scanning</span>
          </div>
        </div>
      </div>
    </header>
  );
}
