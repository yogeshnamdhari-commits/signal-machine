"""
EMA_V5 Exporter — CSV, JSON, Excel export without modifying existing exporters.
Produces files in an isolated output directory.
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .database import EMAv5Database
from .excel_writer import EMAv5ExcelWriter


class EMAv5Exporter:
    """Export EMA_V5 data to CSV, JSON, or Excel. Isolated output directory."""

    def __init__(self, db: Optional[EMAv5Database] = None) -> None:
        self._db = db or EMAv5Database()
        self._excel = EMAv5ExcelWriter()
        _root = Path(__file__).resolve().parent.parent.parent.parent / "data"
        self._export_dir = _root / "ema_v5_exports"
        self._export_dir.mkdir(parents=True, exist_ok=True)

    def export_csv(self, signals: Optional[List[Dict[str, Any]]] = None,
                   filename: Optional[str] = None) -> str:
        """Export signals to CSV. Returns file path."""
        if signals is None:
            signals = self._db.get_all_signals()

        if not signals:
            logger.warning("EMAv5 CSV export: no signals to export")
            return ""

        ts = time.strftime("%Y%m%d_%H%M%S")
        target = filename or f"ema_v5_signals_{ts}.csv"
        filepath = self._export_dir / target

        headers = [
            "Date", "Time", "Exchange", "Symbol", "Side", "Trend",
            "Current State", "EMA20", "EMA50", "EMA144", "EMA200",
            "Entry", "Stop Loss", "TP1", "TP2", "TP3",
            "Volume", "Confidence", "Reason", "Pattern",
            "Result", "PnL", "Hold Time", "Strategy Version",
        ]

        try:
            with open(filepath, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
                writer.writeheader()

                for sig in signals:
                    row = {
                        "Date": sig.get("date", ""),
                        "Time": sig.get("time", ""),
                        "Exchange": sig.get("exchange", "Binance"),
                        "Symbol": sig.get("symbol", ""),
                        "Side": sig.get("side", ""),
                        "Trend": sig.get("trend", ""),
                        "Current State": sig.get("current_state", ""),
                        "EMA20": round(sig.get("ema20") or 0, 6),
                        "EMA50": round(sig.get("ema50") or 0, 6),
                        "EMA144": round(sig.get("ema144") or 0, 6),
                        "EMA200": round(sig.get("ema200") or 0, 6),
                        "Entry": round(sig.get("entry") or 0, 6),
                        "Stop Loss": round(sig.get("stop_loss") or 0, 6),
                        "TP1": round(sig.get("tp1") or 0, 6),
                        "TP2": round(sig.get("tp2") or 0, 6),
                        "TP3": round(sig.get("tp3") or 0, 6),
                        "Volume": "Yes" if sig.get("volume") else "No",
                        "Confidence": f"{(sig.get('confidence', 0) or 0):.1f}%",
                        "Reason": sig.get("reason", ""),
                        "Pattern": sig.get("pattern", ""),
                        "Result": sig.get("result", "") or "—",
                        "PnL": f"{sig.get('pnl', 0):.4f}" if sig.get("pnl") else "—",
                        "Hold Time": f"{sig.get('hold_time', 0):.0f}m" if sig.get("hold_time") else "—",
                        "Strategy Version": sig.get("strategy_version", "ema_v5"),
                    }
                    writer.writerow(row)

            logger.info("📊 EMA_V5 CSV exported: {} ({} signals)", filepath, len(signals))
            return str(filepath)
        except Exception as e:
            logger.error("EMAv5 CSV export failed: {}", e)
            return ""

    def export_json(self, signals: Optional[List[Dict[str, Any]]] = None,
                    filename: Optional[str] = None) -> str:
        """Export signals to JSON. Returns file path."""
        if signals is None:
            signals = self._db.get_all_signals()

        if not signals:
            logger.warning("EMAv5 JSON export: no signals to export")
            return ""

        ts = time.strftime("%Y%m%d_%H%M%S")
        target = filename or f"ema_v5_signals_{ts}.json"
        filepath = self._export_dir / target

        try:
            export_data = {
                "version": "1.0.0",
                "exported_at": time.time(),
                "count": len(signals),
                "signals": signals,
            }
            with open(filepath, "w") as f:
                json.dump(export_data, f, indent=2, default=str)

            logger.info("📊 EMA_V5 JSON exported: {} ({} signals)", filepath, len(signals))
            return str(filepath)
        except Exception as e:
            logger.error("EMAv5 JSON export failed: {}", e)
            return ""

    def export_excel(self, signals: Optional[List[Dict[str, Any]]] = None,
                     filename: Optional[str] = None) -> str:
        """Export signals to Excel. Returns file path."""
        if signals is None:
            signals = self._db.get_all_signals()

        if not signals:
            logger.warning("EMAv5 Excel export: no signals to export")
            return ""

        ts = time.strftime("%Y%m%d_%H%M%S")
        target = filename or f"ema_v5_signals_{ts}.xlsx"
        return self._excel.write(signals, filename=target)

    def export_all(self, signals: Optional[List[Dict[str, Any]]] = None) -> Dict[str, str]:
        """Export to all formats. Returns dict of format → filepath."""
        return {
            "csv": self.export_csv(signals),
            "json": self.export_json(signals),
            "excel": self.export_excel(signals),
        }

    def get_export_dir(self) -> str:
        """Return the export directory path."""
        return str(self._export_dir)
