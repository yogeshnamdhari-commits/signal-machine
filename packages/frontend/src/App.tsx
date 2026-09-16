import { useState, useEffect } from 'react';
import Header from './components/Header';
import SignalCard from './components/SignalCard';
import MarketOverview from './components/MarketOverview';
import PortfolioCard from './components/PortfolioCard';
import PriceChart from './components/PriceChart';
import DataSheet from './components/DataSheet';
import TradingDashboard from './components/TradingDashboard';
import SimulationDashboard from './components/SimulationDashboard';
import { useKlines } from './hooks/useMarketData';
import {
  useSocketConnection,
  useRealTimeTickers,
  useRealTimeSignals,
  useRealTimePortfolio,
} from './hooks/useSocket';
import { signalApi, riskApi } from './services/api';
import { Zap, RefreshCw, Activity, Settings, Table, LayoutDashboard, TrendingUp } from 'lucide-react';

type TabView = 'dashboard' | 'scanner' | 'trading' | 'simulation';

function App() {
  const [selectedSymbol, setSelectedSymbol] = useState('BTCUSDT');
  const [isScanning, setIsScanning] = useState(false);
  const [activeTab, setActiveTab] = useState<TabView>('dashboard');

  const connectionStatus = useSocketConnection();
  const { tickers } = useRealTimeTickers([]);
  const { signals, setSignals } = useRealTimeSignals([]);
  const { portfolio, setPortfolio } = useRealTimePortfolio(null);
  const { klines } = useKlines(selectedSymbol, '1h', 100);

  useEffect(() => {
    const fetchInitialData = async () => {
      try {
        const [signalsData, portfolioData] = await Promise.allSettled([
          signalApi.getSignals(),
          riskApi.getPortfolio(),
        ]);
        if (signalsData.status === 'fulfilled') setSignals(signalsData.value);
        if (portfolioData.status === 'fulfilled') setPortfolio(portfolioData.value);
      } catch (error) {
        console.error('Failed to fetch initial data:', error);
      }
    };
    fetchInitialData();
  }, []);

  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const data = await signalApi.getSignals();
        setSignals(data);
      } catch {
        // silent — WS should be primary
      }
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const data = await riskApi.getPortfolio();
        setPortfolio(data);
      } catch {
        // silent — WS should be primary
      }
    }, 10000);
    return () => clearInterval(interval);
  }, []);

  const handleScanMarket = async () => {
    setIsScanning(true);
    try {
      const newSignals = await signalApi.scanMarket({
        minConfidence: 0.5,
        maxSignals: 10,
      });
      setSignals(newSignals);
    } catch (error) {
      console.error('Scan failed:', error);
    } finally {
      setIsScanning(false);
    }
  };

  const isConnected = connectionStatus === 'connected';

  return (
    <div className="min-h-screen bg-dark-950 text-white">
      <Header connectionStatus={connectionStatus} />
      <main className="container mx-auto px-4 py-6">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <div className="bg-dark-900 rounded-xl border border-dark-700 p-4">
            <div className="flex items-center justify-between">
              <div><p className="text-dark-400 text-sm">Active Signals</p><p className="text-2xl font-bold text-white">{signals.length}</p></div>
              <Zap className="w-8 h-8 text-primary-400" />
            </div>
          </div>
          <div className="bg-dark-900 rounded-xl border border-dark-700 p-4">
            <div className="flex items-center justify-between">
              <div><p className="text-dark-400 text-sm">Market Scanning</p><p className={`text-lg font-bold ${isConnected ? 'text-success' : 'text-danger'}`}>{isConnected ? 'Live' : 'Offline'}</p></div>
              <Activity className={`w-8 h-8 ${isConnected ? 'text-success' : 'text-danger'}`} />
            </div>
          </div>
          <div className="bg-dark-900 rounded-xl border border-dark-700 p-4">
            <div className="flex items-center justify-between">
              <div><p className="text-dark-400 text-sm">Symbols Tracked</p><p className="text-2xl font-bold text-white">{tickers.length}</p></div>
              <RefreshCw className="w-8 h-8 text-dark-400" />
            </div>
          </div>
          <div className="bg-dark-900 rounded-xl border border-dark-700 p-4">
            <div className="flex items-center justify-between">
              <div><p className="text-dark-400 text-sm">Selected Symbol</p><p className="text-lg font-bold text-primary-400">{selectedSymbol}</p></div>
              <Settings className="w-8 h-8 text-dark-400" />
            </div>
          </div>
        </div>

        <div className="flex items-center space-x-1 mb-6 bg-dark-900 rounded-xl border border-dark-700 p-1 w-fit">
          <button onClick={() => setActiveTab('dashboard')} className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${activeTab === 'dashboard' ? 'bg-primary-600 text-white' : 'text-dark-400 hover:text-white hover:bg-dark-800'}`}>
            <LayoutDashboard className="w-4 h-4" /><span>Dashboard</span>
          </button>
          <button onClick={() => setActiveTab('scanner')} className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${activeTab === 'scanner' ? 'bg-primary-600 text-white' : 'text-dark-400 hover:text-white hover:bg-dark-800'}`}>
            <Table className="w-4 h-4" /><span>Full Scanner</span>
          </button>
          <button onClick={() => setActiveTab('trading')} className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${activeTab === 'trading' ? 'bg-primary-600 text-white' : 'text-dark-400 hover:text-white hover:bg-dark-800'}`}>
            <Activity className="w-4 h-4" /><span>Trading</span>
          </button>
          <button onClick={() => setActiveTab('simulation')} className={`flex items-center space-x-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${activeTab === 'simulation' ? 'bg-primary-600 text-white' : 'text-dark-400 hover:text-white hover:bg-dark-800'}`}>
            <TrendingUp className="w-4 h-4" /><span>Simulation</span>
          </button>
        </div>

        {activeTab === 'dashboard' ? (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            <div className="lg:col-span-2 space-y-6">
              <PriceChart data={klines} symbol={selectedSymbol} height={450} />
              <div className="bg-dark-900 rounded-xl border border-dark-700 p-4">
                <div className="flex items-center justify-between mb-4">
                  <h2 className="text-lg font-semibold text-white flex items-center"><Zap className="w-5 h-5 mr-2 text-primary-400" />AI Signals</h2>
                  <button onClick={handleScanMarket} disabled={isScanning} className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${isScanning ? 'bg-dark-700 text-dark-400 cursor-not-allowed' : 'bg-primary-600 hover:bg-primary-700 text-white'}`}>
                    {isScanning ? <span className="flex items-center"><RefreshCw className="w-4 h-4 mr-2 animate-spin" />Scanning...</span> : 'Scan Market'}
                  </button>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {signals.length === 0 ? <div className="col-span-2 text-center py-8 text-dark-400">No active signals. Click "Scan Market" to analyze.</div> : signals.map((signal) => <SignalCard key={signal.id} signal={signal} onClick={() => setSelectedSymbol(signal.symbol)} />)}
                </div>
              </div>
            </div>
            <div className="space-y-6">
              <MarketOverview tickers={tickers} onSelectSymbol={setSelectedSymbol} selectedSymbol={selectedSymbol} />
              {portfolio && <PortfolioCard portfolio={portfolio} />}
            </div>
          </div>
        ) : activeTab === 'scanner' ? <DataSheet /> : activeTab === 'trading' ? <TradingDashboard /> : <SimulationDashboard />}
      </main>
      <footer className="bg-dark-900 border-t border-dark-700 mt-8 py-4">
        <div className="container mx-auto px-4 text-center text-dark-400 text-sm">DeltaTerminal © 2024 | AI-Powered Binance Futures Scanner</div>
      </footer>
    </div>
  );
}

export default App;
