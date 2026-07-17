export interface SymbolSheetData {
  symbol: string;
  price: number;
  openInterest: number;
  openInterestChange: number;
  oiDirection: 'buy' | 'sell';
  fundingRate: number;
  fundingDirection: 'buy' | 'sell';
  volume24h: number;
  volumeDirection: 'buy' | 'sell';
  exchangeFlow: number;
  exchangeFlowDirection: 'in' | 'out';
  signal: 'buy' | 'sell' | 'neutral';
  signalConfidence: number;
  timestamp: number;
}
