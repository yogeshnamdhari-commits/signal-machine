import { useEffect, useRef } from 'react';
import { createChart, IChartApi, ISeriesApi, CandlestickData, HistogramData } from 'lightweight-charts';
import { KlineData } from '../types';

interface PriceChartProps {
  data: KlineData[];
  symbol?: string;
  height?: number;
}

export default function PriceChart({ data, symbol = 'BTCUSDT', height = 400 }: PriceChartProps) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candlestickRef = useRef<ISeriesApi<'Candlestick'> | null>(null);
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    // Create chart
    const chart = createChart(chartContainerRef.current, {
      width: chartContainerRef.current.clientWidth,
      height,
      layout: {
        background: { color: '#0f172a' },
        textColor: '#94a3b8',
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      crosshair: {
        mode: 0,
      },
      rightPriceScale: {
        borderColor: '#1e293b',
      },
      timeScale: {
        borderColor: '#1e293b',
        timeVisible: true,
        secondsVisible: false,
      },
    });

    // Create candlestick series
    const candlestickSeries = chart.addCandlestickSeries({
      upColor: '#10b981',
      downColor: '#ef4444',
      borderUpColor: '#10b981',
      borderDownColor: '#ef4444',
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });

    // Create volume series
    const volumeSeries = chart.addHistogramSeries({
      color: '#3b82f6',
      priceFormat: {
        type: 'volume',
      },
      priceScaleId: 'volume',
    });

    // Store refs
    chartRef.current = chart;
    candlestickRef.current = candlestickSeries;
    volumeRef.current = volumeSeries;

    // Handle resize
    const handleResize = () => {
      if (chartContainerRef.current && chartRef.current) {
        chartRef.current.applyOptions({
          width: chartContainerRef.current.clientWidth,
        });
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, [height]);

  useEffect(() => {
    if (!candlestickRef.current || !volumeRef.current || !data.length) return;

    // Update chart data
    const candleData: CandlestickData[] = data.map((d) => ({
      time: d.time as any,
      open: d.open,
      high: d.high,
      low: d.low,
      close: d.close,
    }));

    const volumeData: HistogramData[] = data.map((d) => ({
      time: d.time as any,
      value: d.volume,
      color: d.close >= d.open ? 'rgba(16, 185, 129, 0.5)' : 'rgba(239, 68, 68, 0.5)',
    }));

    candlestickRef.current.setData(candleData);
    volumeRef.current.setData(volumeData);

    // Fit content
    chartRef.current?.timeScale().fitContent();
  }, [data]);

  return (
    <div className="bg-dark-900 rounded-xl border border-dark-700 p-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-white">{symbol}</h2>
        <div className="flex space-x-2">
          {['1m', '5m', '15m', '1h', '4h', '1d'].map((tf) => (
            <button
              key={tf}
              className="px-3 py-1 text-xs rounded-lg bg-dark-800 text-dark-300 hover:bg-dark-700 transition-colors"
            >
              {tf}
            </button>
          ))}
        </div>
      </div>
      <div ref={chartContainerRef} className="rounded-lg overflow-hidden" />
    </div>
  );
}
