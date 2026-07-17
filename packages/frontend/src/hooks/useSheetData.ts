import { useEffect, useState, useCallback } from 'react';
import { socketService } from '../services/socket';
import { SymbolSheetData } from '../types/sheet';
import { marketApi } from '../services/api';

/**
 * Real-time sheet data — receives full market scan results via Socket.IO.
 * Falls back to REST API for initial load.
 */
export function useRealTimeSheetData() {
  const [data, setData] = useState<SymbolSheetData[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState<number>(0);

  useEffect(() => {
    socketService.connect();

    const handler = (scanData: SymbolSheetData[]) => {
      if (Array.isArray(scanData)) {
        setData(scanData);
        setLastUpdate(Date.now());
        setLoading(false);
      }
    };

    socketService.on('sheet:data', handler);

    // REST fallback — fetch initial data immediately
    marketApi.getScannerData()
      .then((scannerData) => {
        if (Array.isArray(scannerData) && scannerData.length > 0) {
          setData(scannerData);
          setLastUpdate(Date.now());
          setLoading(false);
        }
      })
      .catch(() => {});

    return () => {
      socketService.off('sheet:data', handler);
    };
  }, []);

  const refresh = useCallback(async () => {
    try {
      const scannerData = await marketApi.getScannerData();
      if (Array.isArray(scannerData)) {
        setData(scannerData);
        setLastUpdate(Date.now());
      }
    } catch {}
  }, []);

  return { data, loading, lastUpdate, refresh };
}
