"""
Backtest Stats Engine — Reads backtest reports and computes per-symbol statistics.

Data Source: data/backtest_reports/*.json
  - *_summary.json  → per-symbol or aggregate backtest results
  - *_trades.json   → individual trade records

Computes:
  - Win Rate per symbol
  - Sample Size (total trades)
  - Expected R (avg r-multiple)
  - Profit Factor
  - Expectancy

Used by dashboard to show backtest validation on every signal card.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class BacktestStatsEngine:
    """
    Reads backtest reports and provides per-symbol statistics.

    Usage:
        engine = BacktestStatsEngine()
        engine.load_reports()  # reads all JSON reports
        stats = engine.get_symbol_stats("BTCUSDT")
        all_stats = engine.get_all_stats()
    """

    def __init__(self, reports_dir: str = "") -> None:
        if not reports_dir:
            _ai_root = Path(__file__).resolve().parent.parent
            reports_dir = str(_ai_root / "data" / "backtest_reports")
        self._reports_dir = Path(reports_dir)
        self._symbol_stats: Dict[str, Dict[str, Any]] = {}
        self._last_load: float = 0.0
        self._load_interval: float = 60.0  # reload every 60s

    def load_reports(self) -> None:
        """Load all backtest report JSON files and compute per-symbol stats."""
        now = time.time()
        if now - self._last_load < self._load_interval and self._symbol_stats:
            return  # already loaded recently

        self._symbol_stats = {}

        if not self._reports_dir.exists():
            logger.debug("Backtest reports directory not found: {}", self._reports_dir)
            return

        # Load all summary files
        for summary_file in self._reports_dir.glob("*_summary.json"):
            try:
                self._load_summary(summary_file)
            except Exception as e:
                logger.debug("Error loading summary {}: {}", summary_file.name, e)

        # Load all trade files to compute per-symbol stats from individual trades
        for trades_file in self._reports_dir.glob("*_trades.json"):
            try:
                self._load_trades(trades_file)
            except Exception as e:
                logger.debug("Error loading trades {}: {}", trades_file.name, e)

        self._last_load = now
        if self._symbol_stats:
            logger.info("Backtest stats loaded: {} symbols", len(self._symbol_stats))

    def _load_summary(self, path: Path) -> None:
        """Load a summary JSON and merge into symbol stats."""
        with open(path) as f:
            data = json.load(f)

        symbol = data.get("symbol", "UNKNOWN")
        trades = data.get("total_trades", 0)
        win_rate = data.get("win_rate", 0)
        avg_r = data.get("avg_r_multiple", 0)
        pf = data.get("profit_factor", 0)
        expectancy = data.get("expectancy", 0)
        max_dd = data.get("max_drawdown_pct", 0)
        total_pnl = data.get("total_pnl", 0)
        exit_reasons = data.get("exit_reasons", {})

        if symbol in ("MULTI", "MULTI-5", "UNKNOWN"):
            # Multi-symbol reports — skip or use as aggregate
            return

        # Clean symbol name (remove _summary.json suffix artifacts)
        clean_sym = symbol.replace("_summary", "")

        existing = self._symbol_stats.get(clean_sym, {})
        # If we have multiple reports, prefer the one with more trades
        if existing and existing.get("sample_size", 0) >= trades:
            return

        self._symbol_stats[clean_sym] = {
            "symbol": clean_sym,
            "win_rate": round(win_rate * 100, 1),  # convert to percentage
            "sample_size": trades,
            "expected_r": round(avg_r, 2),
            "profit_factor": round(pf, 2),
            "expectancy": round(expectancy, 2),
            "max_drawdown_pct": round(max_dd, 1),
            "total_pnl": round(total_pnl, 2),
            "exit_reasons": exit_reasons,
            "report_file": path.name,
            "trade_type": "backtest",
        }

    def _load_trades(self, path: Path) -> None:
        """Load trades JSON and compute stats from individual trades."""
        with open(path) as f:
            trades = json.load(f)

        if not trades:
            return

        # Group trades by symbol (from entry_score or symbol field)
        # Most trades have symbol="BACKTEST" — try to infer from filename
        filename = path.stem.replace("_trades", "")

        # Compute stats from trades
        pnls = [t.get("net_pnl", 0) for t in trades]
        r_multiples = [t.get("r_multiple", 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        total = len(trades)
        win_count = len(wins)
        win_rate = (win_count / total * 100) if total > 0 else 0
        avg_r = sum(r_multiples) / total if total > 0 else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0
        profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else 0
        expectancy_val = (win_rate / 100 * avg_win) - ((1 - win_rate / 100) * avg_loss) if total > 0 else 0

        # Exit reason breakdown
        exit_reasons = {}
        for t in trades:
            reason = t.get("exit_reason", "unknown")
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

        # Compute score-based breakdown (group by entry_score ranges)
        score_buckets = {}
        for t in trades:
            score = t.get("entry_score", 0)
            bucket = "high" if score >= 70 else "medium" if score >= 50 else "low"
            if bucket not in score_buckets:
                score_buckets[bucket] = {"trades": 0, "wins": 0, "total_pnl": 0}
            score_buckets[bucket]["trades"] += 1
            if t.get("net_pnl", 0) > 0:
                score_buckets[bucket]["wins"] += 1
            score_buckets[bucket]["total_pnl"] += t.get("net_pnl", 0)

        # Add win rate per score bucket
        for bucket, stats in score_buckets.items():
            stats["win_rate"] = round(stats["wins"] / stats["trades"] * 100, 1) if stats["trades"] > 0 else 0

        # Only update if this file has more trades or doesn't exist yet
        existing = self._symbol_stats.get(filename, {})
        if existing and existing.get("sample_size", 0) >= total:
            return

        self._symbol_stats[filename] = {
            "symbol": filename,
            "win_rate": round(win_rate, 1),
            "sample_size": total,
            "expected_r": round(avg_r, 2),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy_val, 2),
            "max_drawdown_pct": 0,  # not computed from trades alone
            "total_pnl": round(sum(pnls), 2),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "exit_reasons": exit_reasons,
            "score_buckets": score_buckets,
            "report_file": path.name,
            "trade_type": "backtest",
        }

    def get_symbol_stats(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get backtest stats for a specific symbol."""
        self.load_reports()
        # Try exact match first, then without USDT suffix
        stats = self._symbol_stats.get(symbol)
        if not stats and symbol.endswith("USDT"):
            stats = self._symbol_stats.get(symbol.replace("USDT", ""))
        return stats

    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get backtest stats for all symbols."""
        self.load_reports()
        return dict(self._symbol_stats)

    def get_snapshot(self) -> Dict[str, Any]:
        """Get full snapshot for bridge / dashboard."""
        self.load_reports()
        return {
            "symbols": dict(self._symbol_stats),
            "total_symbols": len(self._symbol_stats),
            "last_update": self._last_load,
        }

    def enrich_signal(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich a signal dict with backtest stats if available."""
        symbol = signal.get("symbol", "")
        stats = self.get_symbol_stats(symbol)
        if stats:
            signal["backtest_win_rate"] = stats.get("win_rate", 0)
            signal["backtest_sample"] = stats.get("sample_size", 0)
            signal["backtest_expected_r"] = stats.get("expected_r", 0)
            signal["backtest_profit_factor"] = stats.get("profit_factor", 0)
            signal["backtest_trade_type"] = stats.get("trade_type", "backtest")
            signal["backtest_report"] = stats.get("report_file", "")
        else:
            signal["backtest_win_rate"] = None
            signal["backtest_sample"] = 0
            signal["backtest_expected_r"] = None
            signal["backtest_profit_factor"] = None
            signal["backtest_trade_type"] = None
            signal["backtest_report"] = ""
        return signal
