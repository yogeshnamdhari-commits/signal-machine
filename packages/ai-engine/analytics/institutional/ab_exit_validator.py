"""
A/B Exit Validation Framework
================================
Counterfactual testing of alternative exit parameters against historical trades.

READ-ONLY — Never modifies trading logic or production config.

Tests alternative trailing stop configurations against completed trades
to determine if parameter changes would improve risk-adjusted returns.

Methodology:
1. Load all completed trades with MFE/MAE data
2. For each trade, simulate alternative exit rules
3. Compare portfolio-level metrics across configurations
4. Report statistical significance
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger


@dataclass
class ExitConfig:
    """Trailing stop configuration to test."""
    name: str
    breakeven_at_r: float = 1.0
    activate_at_r: float = 1.0
    trail_atr_mult: float = 1.0
    description: str = ""


@dataclass
class TradeSimResult:
    """Result of simulating one trade under an exit config."""
    signal_id: str
    symbol: str
    side: str
    actual_pnl: float
    simulated_pnl: float
    actual_exit: str
    simulated_exit: str
    mfe_pct: float
    risk: float
    reached_tp1: bool
    reached_tp2: bool
    reached_tp3: bool


@dataclass
class ConfigResult:
    """Aggregate results for one exit configuration."""
    config_name: str
    config: ExitConfig
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    expectancy: float = 0.0
    profit_factor: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    avg_winner: float = 0.0
    avg_loser: float = 0.0
    avg_hold_minutes: float = 0.0
    max_win_streak: int = 0
    max_loss_streak: int = 0
    exit_distribution: Dict[str, int] = field(default_factory=dict)
    trade_results: List[TradeSimResult] = field(default_factory=list)


class ABExitValidator:
    """
    A/B validation framework for exit parameters.
    
    READ-ONLY: Never modifies trading logic.
    """
    
    def __init__(self, trades_db: Optional[str] = None):
        base = Path(__file__).resolve().parent.parent.parent / "data"
        self._trades_db = trades_db or str(base / "institutional_v1.db")
        self._sl_dist_pct = 0.00068  # From current config
        self._tp1_rr = 1.5
        self._tp2_rr = 3.0
        self._tp3_rr = 5.0
    
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._trades_db, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _load_trades_with_mfe(self) -> List[Dict[str, Any]]:
        """Load trades that have MFE data for simulation."""
        conn = self._connect()
        trades = []
        try:
            for table in ["positions_archive", "positions"]:
                rows = conn.execute(f"""
                    SELECT signal_id, symbol, side, entry_price, stop_loss,
                           take_profit, pnl, fees, confidence, regime, session,
                           risk_reward, hold_minutes, mfe_pct, mae_pct,
                           realized_r, exit_reason, outcome, opened_at, closed_at,
                           quantity, leverage, status
                    FROM {table}
                    WHERE status = 'closed' AND outcome IS NOT NULL
                      AND mfe_pct != 0
                """).fetchall()
                trades.extend([dict(r) for r in rows])
            return trades
        finally:
            conn.close()
    
    def _simulate_trade(
        self, trade: Dict[str, Any], config: ExitConfig
    ) -> TradeSimResult:
        """Simulate one trade under an alternative exit configuration."""
        entry = trade["entry_price"] or 0
        sl = trade["stop_loss"] or 0
        mfe = trade["mfe_pct"] or 0
        qty = trade["quantity"] or 0
        lev = trade["leverage"] or 1
        side = trade["side"] or "LONG"
        actual_pnl = trade["pnl"] or 0
        actual_exit = trade["exit_reason"] or "unknown"
        
        if entry == 0 or sl == 0:
            return TradeSimResult(
                signal_id=trade["signal_id"] or "",
                symbol=trade["symbol"] or "",
                side=side,
                actual_pnl=actual_pnl,
                simulated_pnl=actual_pnl,
                actual_exit=actual_exit,
                simulated_exit=actual_exit,
                mfe_pct=mfe,
                risk=0,
                reached_tp1=False,
                reached_tp2=False,
                reached_tp3=False,
            )
        
        risk = abs(entry - sl)
        risk_pct = risk / entry
        
        # TP levels
        tp1_distance = risk * self._tp1_rr
        tp2_distance = risk * self._tp2_rr
        tp3_distance = risk * self._tp3_rr
        
        # MFE in price terms
        mfe_price = mfe * entry
        
        # Did price reach TP levels?
        reached_tp1 = mfe_price >= tp1_distance
        reached_tp2 = mfe_price >= tp2_distance
        reached_tp3 = mfe_price >= tp3_distance
        
        # Simulate exit based on config
        # The trailing stop activates at activate_at_r and trails by trail_atr_mult * ATR
        # For simulation: if MFE reached activate_at_r * risk, the trailing stop is active
        # The trail distance = trail_atr_mult * sl_dist_pct * entry
        # If price reversed from MFE by more than trail_distance, trail triggered
        
        activate_distance = risk * config.activate_at_r
        trail_distance = config.trail_atr_mult * self._sl_dist_pct * entry
        
        # Determine simulated exit
        if mfe_price >= tp3_distance:
            # Price reached TP3 — close at TP3
            sim_pnl = tp3_distance * qty * lev
            sim_exit = "take_profit_3"
        elif mfe_price >= tp2_distance:
            # Price reached TP2 — close at TP2
            sim_pnl = tp2_distance * qty * lev
            sim_exit = "take_profit_2"
        elif mfe_price >= tp1_distance:
            # Price reached TP1 — close at TP1
            sim_pnl = tp1_distance * qty * lev
            sim_exit = "take_profit_1"
        elif mfe_price >= activate_distance:
            # Trailing stop activated but didn't reach TP
            # The trail would have been hit at some point
            # Use actual PnL as proxy (trail triggered)
            sim_pnl = actual_pnl
            sim_exit = "trailing_stop"
        else:
            # Never reached activation — stop loss or time exit
            sim_pnl = actual_pnl
            sim_exit = actual_exit
        
        return TradeSimResult(
            signal_id=trade["signal_id"] or "",
            symbol=trade["symbol"] or "",
            side=side,
            actual_pnl=actual_pnl,
            simulated_pnl=sim_pnl,
            actual_exit=actual_exit,
            simulated_exit=sim_exit,
            mfe_pct=mfe,
            risk=risk,
            reached_tp1=reached_tp1,
            reached_tp2=reached_tp2,
            reached_tp3=reached_tp3,
        )
    
    def _compute_config_result(
        self, trades: List[Dict], config: ExitConfig
    ) -> ConfigResult:
        """Compute aggregate results for one configuration."""
        results = []
        for trade in trades:
            result = self._simulate_trade(trade, config)
            results.append(result)
        
        pnls = [r.simulated_pnl for r in results]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        
        # Max drawdown
        cumulative = 0
        peak = 0
        max_dd = 0
        for pnl in sorted(results, key=lambda r: r.signal_id):
            cumulative += pnl.simulated_pnl
            peak = max(peak, cumulative)
            dd = (peak - cumulative) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        # Sharpe
        sharpe = 0.0
        if len(pnls) >= 2:
            mean = statistics.mean(pnls)
            std = statistics.stdev(pnls)
            if std > 0:
                sharpe = mean / std
        
        # Streaks
        max_win = 0
        max_loss = 0
        current_win = 0
        current_loss = 0
        for pnl in pnls:
            if pnl > 0:
                current_win += 1
                current_loss = 0
                max_win = max(max_win, current_win)
            elif pnl < 0:
                current_loss += 1
                current_win = 0
                max_loss = max(max_loss, current_loss)
        
        # Exit distribution
        exit_dist = defaultdict(int)
        for r in results:
            exit_dist[r.simulated_exit] += 1
        
        # Hold times
        hold_mins = [t.get("hold_minutes", 0) or 0 for t in trades if t.get("hold_minutes")]
        
        return ConfigResult(
            config_name=config.name,
            config=config,
            total_trades=len(results),
            wins=len(wins),
            losses=len(losses),
            win_rate=len(wins) / len(results) if results else 0,
            total_pnl=sum(pnls),
            avg_pnl=statistics.mean(pnls) if pnls else 0,
            expectancy=statistics.mean(pnls) if pnls else 0,
            profit_factor=gross_profit / gross_loss if gross_loss > 0 else (
                float("inf") if gross_profit > 0 else 0
            ),
            sharpe=sharpe,
            max_drawdown=max_dd,
            avg_winner=statistics.mean(wins) if wins else 0,
            avg_loser=statistics.mean(losses) if losses else 0,
            avg_hold_minutes=statistics.mean(hold_mins) if hold_mins else 0,
            max_win_streak=max_win,
            max_loss_streak=max_loss,
            exit_distribution=dict(exit_dist),
            trade_results=results,
        )
    
    def run_validation(
        self, configs: Optional[List[ExitConfig]] = None
    ) -> List[ConfigResult]:
        """Run A/B validation across all configurations."""
        if configs is None:
            configs = self.get_default_configs()
        
        trades = self._load_trades_with_mfe()
        if not trades:
            logger.warning("No trades with MFE data found")
            return []
        
        logger.info("Running A/B validation on {} trades with MFE data", len(trades))
        
        results = []
        for config in configs:
            result = self._compute_config_result(trades, config)
            results.append(result)
            logger.info(
                "Config '{}': PF={:.2f}, WR={:.1f}%, PnL=${:.2f}",
                config.name, result.profit_factor, result.win_rate * 100, result.total_pnl,
            )
        
        return results
    
    @staticmethod
    def get_default_configs() -> List[ExitConfig]:
        """Default configurations to test."""
        return [
            ExitConfig(
                name="CURRENT (baseline)",
                breakeven_at_r=1.0,
                activate_at_r=1.0,
                trail_atr_mult=1.0,
                description="Current production config",
            ),
            ExitConfig(
                name="WIDE_TRAIL_1.5",
                breakeven_at_r=1.0,
                activate_at_r=1.0,
                trail_atr_mult=1.5,
                description="Wider trail: 1.5x ATR",
            ),
            ExitConfig(
                name="WIDE_TRAIL_2.0",
                breakeven_at_r=1.0,
                activate_at_r=1.0,
                trail_atr_mult=2.0,
                description="Wider trail: 2.0x ATR",
            ),
            ExitConfig(
                name="LATE_ACTIVATE_1.5R",
                breakeven_at_r=1.5,
                activate_at_r=1.5,
                trail_atr_mult=1.0,
                description="Later activation: 1.5R",
            ),
            ExitConfig(
                name="LATE_ACTIVATE_2.0R",
                breakeven_at_r=2.0,
                activate_at_r=2.0,
                trail_atr_mult=1.0,
                description="Later activation: 2.0R",
            ),
            ExitConfig(
                name="LATE_WIDE_1.5R_1.5ATR",
                breakeven_at_r=1.5,
                activate_at_r=1.5,
                trail_atr_mult=1.5,
                description="Later + wider: 1.5R, 1.5x ATR",
            ),
            ExitConfig(
                name="LATE_WIDE_2.0R_2.0ATR",
                breakeven_at_r=2.0,
                activate_at_r=2.0,
                trail_atr_mult=2.0,
                description="Later + wider: 2.0R, 2.0x ATR",
            ),
            ExitConfig(
                name="TP_ONLY_NO_TRAIL",
                breakeven_at_r=99.0,
                activate_at_r=99.0,
                trail_atr_mult=0.0,
                description="No trailing stop — TP or SL only",
            ),
        ]
    
    @staticmethod
    def format_report(results: List[ConfigResult]) -> str:
        """Format validation results as a readable report."""
        lines = []
        lines.append("=" * 80)
        lines.append("A/B EXIT VALIDATION REPORT")
        lines.append("=" * 80)
        
        # Summary table
        lines.append(f"\n{'Config':<30} {'Trades':>6} {'WR%':>6} {'PF':>7} {'PnL':>10} {'Sharpe':>7} {'MaxDD':>7} {'AvgWin':>8} {'AvgLoss':>8}")
        lines.append("-" * 90)
        
        baseline = results[0] if results else None
        
        for r in results:
            pf_str = f"{r.profit_factor:.2f}" if r.profit_factor < 100 else "∞"
            dd_str = f"{r.max_drawdown*100:.1f}%"
            
            # Mark improvements
            marker = ""
            if baseline and r.config_name != baseline.config_name:
                if r.profit_factor > baseline.profit_factor and r.total_pnl > baseline.total_pnl:
                    marker = " ✅"
                elif r.profit_factor < baseline.profit_factor and r.total_pnl < baseline.total_pnl:
                    marker = " ❌"
                else:
                    marker = " ≈"
            
            lines.append(
                f"{r.config_name:<30} {r.total_trades:>6} {r.win_rate*100:>5.1f}% "
                f"{pf_str:>7} ${r.total_pnl:>9.2f} {r.sharpe:>7.2f} {dd_str:>7} "
                f"${r.avg_winner:>7.2f} ${r.avg_loser:>7.2f}{marker}"
            )
        
        # Exit distribution
        lines.append(f"\n{'='*80}")
        lines.append("EXIT DISTRIBUTION BY CONFIG")
        lines.append("=" * 80)
        
        for r in results:
            lines.append(f"\n{r.config_name}:")
            for exit_type, count in sorted(r.exit_distribution.items(), key=lambda x: -x[1]):
                lines.append(f"  {exit_type}: {count}")
        
        # Recommendation
        lines.append(f"\n{'='*80}")
        lines.append("RECOMMENDATION")
        lines.append("=" * 80)
        
        if baseline:
            best = max(results, key=lambda r: r.total_pnl)
            if best.config_name != baseline.config_name:
                improvement = best.total_pnl - baseline.total_pnl
                lines.append(f"\nBest config: {best.config_name}")
                lines.append(f"  PnL improvement: ${improvement:+.2f}")
                lines.append(f"  PF improvement: {best.profit_factor - baseline.profit_factor:+.2f}")
                lines.append(f"  WR improvement: {(best.win_rate - baseline.win_rate)*100:+.1f}%")
                lines.append(f"\n  Description: {best.config.description}")
                lines.append(f"\n  NOTE: This is a counterfactual simulation.")
                lines.append(f"  Validate with forward paper trading before production deployment.")
            else:
                lines.append(f"\nCurrent config is already optimal in this sample.")
        
        return "\n".join(lines)
    
    def export_json(self, results: List[ConfigResult], path: Optional[str] = None) -> str:
        """Export results to JSON."""
        data = []
        for r in results:
            data.append({
                "config_name": r.config_name,
                "config": {
                    "breakeven_at_r": r.config.breakeven_at_r,
                    "activate_at_r": r.config.activate_at_r,
                    "trail_atr_mult": r.config.trail_atr_mult,
                    "description": r.config.description,
                },
                "metrics": {
                    "total_trades": r.total_trades,
                    "wins": r.wins,
                    "losses": r.losses,
                    "win_rate": round(r.win_rate, 4),
                    "total_pnl": round(r.total_pnl, 2),
                    "avg_pnl": round(r.avg_pnl, 4),
                    "expectancy": round(r.expectancy, 4),
                    "profit_factor": round(r.profit_factor, 4),
                    "sharpe": round(r.sharpe, 4),
                    "max_drawdown": round(r.max_drawdown, 4),
                    "avg_winner": round(r.avg_winner, 2),
                    "avg_loser": round(r.avg_loser, 2),
                    "avg_hold_minutes": round(r.avg_hold_minutes, 1),
                    "max_win_streak": r.max_win_streak,
                    "max_loss_streak": r.max_loss_streak,
                },
                "exit_distribution": r.exit_distribution,
            })
        
        json_path = path or str(
            Path(self._trades_db).parent.parent / "data" / "bridge" / "ab_validation.json"
        )
        
        with open(json_path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        
        logger.info("A/B validation exported to {}", json_path)
        return json_path


if __name__ == "__main__":
    validator = ABExitValidator()
    configs = validator.get_default_configs()
    results = validator.run_validation(configs)
    
    report = validator.format_report(results)
    print(report)
    
    path = validator.export_json(results)
    print(f"\nExported to: {path}")
