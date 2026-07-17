"""
Validation Framework — Walk-forward analysis and attribution.

Per Executive Assessment v6:
    "Run evaluations such as:
        1. Rolling walk-forward analysis (50 or 100-trade windows)
        2. Symbol-by-symbol attribution
        3. Exit attribution
        4. Capital allocation attribution
        5. Regime attribution

     These analyses will show whether the new modules are improving
     decisions or merely adding complexity."

Key Features:
    1. Walk-Forward Analysis — rolling PF/EV across time windows
    2. Symbol Attribution — which symbols contribute profit/loss
    3. Exit Attribution — which exits capture most profit
    4. Regime Attribution — performance by market condition
    5. Stability Metrics — is performance consistent or variable?

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


@dataclass
class WalkForwardWindow:
    """Metrics for a single walk-forward window."""
    window_start: int = 0
    window_end: int = 0
    trade_count: int = 0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    win_rate: float = 0.0
    avg_r: float = 0.0
    total_pnl: float = 0.0
    max_drawdown_r: float = 0.0
    sharpe_ratio: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "window": f"{self.window_start}-{self.window_end}",
            "trades": self.trade_count,
            "pf": round(self.profit_factor, 2),
            "ev_r": round(self.expectancy_r, 3),
            "win_rate": round(self.win_rate, 3),
            "avg_r": round(self.avg_r, 3),
            "total_pnl": round(self.total_pnl, 2),
            "max_dd_r": round(self.max_drawdown_r, 3),
            "sharpe": round(self.sharpe_ratio, 3),
        }


@dataclass
class SymbolAttribution:
    """Attribution data for a single symbol."""
    symbol: str = ""
    trade_count: int = 0
    total_pnl: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    win_rate: float = 0.0
    avg_r: float = 0.0
    contribution_pct: float = 0.0  # % of total PnL
    avg_mfe_r: float = 0.0
    avg_mae_r: float = 0.0
    profit_capture_pct: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "symbol": self.symbol,
            "trades": self.trade_count,
            "total_pnl": round(self.total_pnl, 2),
            "pf": round(self.profit_factor, 2),
            "ev_r": round(self.expectancy_r, 3),
            "win_rate": round(self.win_rate, 3),
            "avg_r": round(self.avg_r, 3),
            "contribution_pct": round(self.contribution_pct, 1),
            "profit_capture_pct": round(self.profit_capture_pct, 1),
        }


@dataclass
class ExitAttribution:
    """Attribution data for a single exit reason."""
    exit_reason: str = ""
    trade_count: int = 0
    total_pnl: float = 0.0
    profit_factor: float = 0.0
    avg_r: float = 0.0
    avg_mfe_r: float = 0.0
    profit_capture_pct: float = 0.0
    contribution_pct: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "exit_reason": self.exit_reason,
            "trades": self.trade_count,
            "total_pnl": round(self.total_pnl, 2),
            "pf": round(self.profit_factor, 2),
            "avg_r": round(self.avg_r, 3),
            "avg_mfe_r": round(self.avg_mfe_r, 3),
            "profit_capture_pct": round(self.profit_capture_pct, 1),
            "contribution_pct": round(self.contribution_pct, 1),
        }


@dataclass
class RegimeAttribution:
    """Attribution data for a single regime."""
    regime: str = ""
    trade_count: int = 0
    total_pnl: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    win_rate: float = 0.0
    contribution_pct: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "regime": self.regime,
            "trades": self.trade_count,
            "total_pnl": round(self.total_pnl, 2),
            "pf": round(self.profit_factor, 2),
            "ev_r": round(self.expectancy_r, 3),
            "win_rate": round(self.win_rate, 3),
            "contribution_pct": round(self.contribution_pct, 1),
        }


@dataclass
class ValidationReport:
    """Complete validation report."""
    timestamp: float = 0.0

    # Walk-forward
    walk_forward_windows: List[WalkForwardWindow] = field(default_factory=list)
    pf_stability: float = 0.0      # Std dev of PF across windows (lower = more stable)
    ev_stability: float = 0.0      # Std dev of EV across windows

    # Symbol attribution
    symbol_attributions: List[SymbolAttribution] = field(default_factory=list)
    top_profit_symbols: List[SymbolAttribution] = field(default_factory=list)
    top_loss_symbols: List[SymbolAttribution] = field(default_factory=list)

    # Exit attribution
    exit_attributions: List[ExitAttribution] = field(default_factory=list)

    # Regime attribution
    regime_attributions: List[RegimeAttribution] = field(default_factory=list)

    # Overall
    total_trades: int = 0
    overall_pf: float = 0.0
    overall_ev_r: float = 0.0
    overall_win_rate: float = 0.0
    overall_sharpe: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "overall": {
                "trades": self.total_trades,
                "pf": round(self.overall_pf, 2),
                "ev_r": round(self.overall_ev_r, 3),
                "win_rate": round(self.overall_win_rate, 3),
                "sharpe": round(self.overall_sharpe, 3),
            },
            "stability": {
                "pf_stability": round(self.pf_stability, 3),
                "ev_stability": round(self.ev_stability, 3),
            },
            "walk_forward": [w.to_dict() for w in self.walk_forward_windows],
            "top_profit_symbols": [s.to_dict() for s in self.top_profit_symbols[:5]],
            "top_loss_symbols": [s.to_dict() for s in self.top_loss_symbols[:5]],
            "exit_attribution": [e.to_dict() for e in self.exit_attributions],
            "regime_attribution": [r.to_dict() for r in self.regime_attributions],
        }


class ValidationFramework:
    """
    Walk-forward analysis and performance attribution.

    Per Executive Assessment v6:
        "These analyses will show whether the new modules are
         improving decisions or merely adding complexity."

    This engine:
        1. Performs rolling walk-forward analysis
        2. Attributes PnL to symbols, exits, and regimes
        3. Measures performance stability
        4. Identifies edge decay or improvement

    READ-ONLY: Never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        """Load trades from DB if stale."""
        if time.time() - self._last_load < 300:
            return
        self._load_trades()

    def _load_trades(self) -> None:
        """Load all closed trades."""
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol, side, realized_r, pnl, mfe_pct, mae_pct,
                       exit_reason, session, regime, hold_minutes, closed_at
                FROM positions_archive
                WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()

            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()

        except Exception as e:
            logger.warning("Could not load validation framework: {}", e)

    def generate_report(
        self,
        walk_forward_window: int = 50,
    ) -> ValidationReport:
        """
        Generate complete validation report.

        Args:
            walk_forward_window: Number of trades per walk-forward window

        Returns:
            ValidationReport with all analyses
        """
        self._ensure_loaded()

        report = ValidationReport(timestamp=time.time())

        if not self._trades:
            return report

        report.total_trades = len(self._trades)

        # ── Walk-Forward Analysis ──
        report.walk_forward_windows = self._walk_forward_analysis(walk_forward_window)

        # Stability metrics
        if len(report.walk_forward_windows) >= 3:
            pf_values = [w.profit_factor for w in report.walk_forward_windows]
            ev_values = [w.expectancy_r for w in report.walk_forward_windows]
            report.pf_stability = self._calc_std_dev(pf_values)
            report.ev_stability = self._calc_std_dev(ev_values)

        # ── Overall metrics ──
        wins = [t for t in self._trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [t for t in self._trades if (t.get("realized_r", 0) or 0) < 0]
        report.overall_win_rate = len(wins) / max(1, len(self._trades))

        gross_profit = sum(t.get("realized_r", 0) or 0 for t in wins)
        gross_loss = sum(abs(t.get("realized_r", 0) or 0) for t in losses)
        report.overall_pf = gross_profit / max(0.01, gross_loss)

        all_r = [t.get("realized_r", 0) or 0 for t in self._trades]
        report.overall_ev_r = sum(all_r) / max(1, len(all_r))

        if len(all_r) > 1:
            mean_r = sum(all_r) / len(all_r)
            std_r = math.sqrt(sum((r - mean_r) ** 2 for r in all_r) / len(all_r))
            report.overall_sharpe = mean_r / max(0.001, std_r)

        # ── Symbol Attribution ──
        report.symbol_attributions = self._symbol_attribution()
        report.top_profit_symbols = sorted(
            report.symbol_attributions, key=lambda s: s.total_pnl, reverse=True
        )[:5]
        report.top_loss_symbols = sorted(
            report.symbol_attributions, key=lambda s: s.total_pnl
        )[:5]

        # ── Exit Attribution ──
        report.exit_attributions = self._exit_attribution()

        # ── Regime Attribution ──
        report.regime_attributions = self._regime_attribution()

        return report

    def _walk_forward_analysis(self, window_size: int) -> List[WalkForwardWindow]:
        """Perform rolling walk-forward analysis."""
        windows = []

        for i in range(0, len(self._trades), window_size):
            window_trades = self._trades[i:i + window_size]
            if len(window_trades) < 10:
                continue

            metrics = self._calc_window_metrics(window_trades)
            metrics.window_start = i + 1
            metrics.window_end = min(i + window_size, len(self._trades))
            windows.append(metrics)

        return windows

    def _calc_window_metrics(self, trades: List[Dict]) -> WalkForwardWindow:
        """Calculate metrics for a window of trades."""
        metrics = WalkForwardWindow(trade_count=len(trades))

        if not trades:
            return metrics

        wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
        losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]

        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(losses) if losses else 0.01
        metrics.profit_factor = gross_profit / max(0.01, gross_loss)

        all_r = [t.get("realized_r", 0) or 0 for t in trades]
        metrics.avg_r = sum(all_r) / max(1, len(all_r))
        metrics.expectancy_r = metrics.avg_r
        metrics.win_rate = len(wins) / max(1, len(trades))
        metrics.total_pnl = sum(t.get("pnl", 0) or 0 for t in trades)

        # Max drawdown
        equity = 0
        peak = 0
        max_dd = 0
        for r in all_r:
            equity += r
            if equity > peak:
                peak = equity
            dd = peak - equity
            max_dd = max(max_dd, dd)
        metrics.max_drawdown_r = max_dd

        # Sharpe
        if len(all_r) > 1:
            mean_r = sum(all_r) / len(all_r)
            std_r = math.sqrt(sum((r - mean_r) ** 2 for r in all_r) / len(all_r))
            metrics.sharpe_ratio = mean_r / max(0.001, std_r)

        return metrics

    def _symbol_attribution(self) -> List[SymbolAttribution]:
        """Calculate attribution by symbol."""
        by_symbol: Dict[str, List[Dict]] = defaultdict(list)
        for t in self._trades:
            by_symbol[t.get("symbol", "")].append(t)

        total_pnl = sum(t.get("pnl", 0) or 0 for t in self._trades)

        attributions = []
        for sym, trades in by_symbol.items():
            wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
            losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]

            gross_profit = sum(wins) if wins else 0
            gross_loss = sum(losses) if losses else 0.01

            all_r = [t.get("realized_r", 0) or 0 for t in trades]
            sym_pnl = sum(t.get("pnl", 0) or 0 for t in trades)

            # MFE/MAE
            mfe_vals = [t.get("highest_pnl", 0) or 0 for t in trades if (t.get("highest_pnl", 0) or 0) > 0]
            mae_vals = [abs(t.get("mae_pct", 0) or 0) for t in trades if (t.get("mae_pct", 0) or 0) > 0]

            capture_vals = []
            for t in trades:
                mfe = t.get("highest_pnl", 0) or 0
                r = t.get("realized_r", 0) or 0
                if mfe > 0:
                    capture_vals.append((r / mfe) * 100)

            attr = SymbolAttribution(
                symbol=sym,
                trade_count=len(trades),
                total_pnl=sym_pnl,
                profit_factor=gross_profit / max(0.01, gross_loss),
                expectancy_r=sum(all_r) / max(1, len(all_r)),
                win_rate=len(wins) / max(1, len(trades)),
                avg_r=sum(all_r) / max(1, len(all_r)),
                contribution_pct=(sym_pnl / max(0.01, abs(total_pnl))) * 100 if total_pnl != 0 else 0,
                avg_mfe_r=sum(mfe_vals) / max(1, len(mfe_vals)),
                avg_mae_r=sum(mae_vals) / max(1, len(mae_vals)),
                profit_capture_pct=sum(capture_vals) / max(1, len(capture_vals)),
            )
            attributions.append(attr)

        return sorted(attributions, key=lambda a: a.total_pnl, reverse=True)

    def _exit_attribution(self) -> List[ExitAttribution]:
        """Calculate attribution by exit reason."""
        by_reason: Dict[str, List[Dict]] = defaultdict(list)
        for t in self._trades:
            by_reason[t.get("exit_reason", "unknown")].append(t)

        total_pnl = sum(t.get("pnl", 0) or 0 for t in self._trades)

        attributions = []
        for reason, trades in by_reason.items():
            wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
            losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]

            gross_profit = sum(wins) if wins else 0
            gross_loss = sum(losses) if losses else 0.01

            all_r = [t.get("realized_r", 0) or 0 for t in trades]
            reason_pnl = sum(t.get("pnl", 0) or 0 for t in trades)

            mfe_vals = [t.get("highest_pnl", 0) or 0 for t in trades if (t.get("highest_pnl", 0) or 0) > 0]
            capture_vals = []
            for t in trades:
                mfe = t.get("highest_pnl", 0) or 0
                r = t.get("realized_r", 0) or 0
                if mfe > 0:
                    capture_vals.append((r / mfe) * 100)

            attr = ExitAttribution(
                exit_reason=reason,
                trade_count=len(trades),
                total_pnl=reason_pnl,
                profit_factor=gross_profit / max(0.01, gross_loss),
                avg_r=sum(all_r) / max(1, len(all_r)),
                avg_mfe_r=sum(mfe_vals) / max(1, len(mfe_vals)),
                profit_capture_pct=sum(capture_vals) / max(1, len(capture_vals)),
                contribution_pct=(reason_pnl / max(0.01, abs(total_pnl))) * 100 if total_pnl != 0 else 0,
            )
            attributions.append(attr)

        return sorted(attributions, key=lambda a: a.total_pnl, reverse=True)

    def _regime_attribution(self) -> List[RegimeAttribution]:
        """Calculate attribution by regime."""
        by_regime: Dict[str, List[Dict]] = defaultdict(list)
        for t in self._trades:
            by_regime[t.get("regime", "unknown")].append(t)

        total_pnl = sum(t.get("pnl", 0) or 0 for t in self._trades)

        attributions = []
        for regime, trades in by_regime.items():
            wins = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
            losses = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]

            gross_profit = sum(wins) if wins else 0
            gross_loss = sum(losses) if losses else 0.01

            all_r = [t.get("realized_r", 0) or 0 for t in trades]
            regime_pnl = sum(t.get("pnl", 0) or 0 for t in trades)

            attr = RegimeAttribution(
                regime=regime,
                trade_count=len(trades),
                total_pnl=regime_pnl,
                profit_factor=gross_profit / max(0.01, gross_loss),
                expectancy_r=sum(all_r) / max(1, len(all_r)),
                win_rate=len(wins) / max(1, len(trades)),
                contribution_pct=(regime_pnl / max(0.01, abs(total_pnl))) * 100 if total_pnl != 0 else 0,
            )
            attributions.append(attr)

        return sorted(attributions, key=lambda a: a.total_pnl, reverse=True)

    def _calc_std_dev(self, values: List[float]) -> float:
        """Calculate standard deviation."""
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return math.sqrt(variance)
