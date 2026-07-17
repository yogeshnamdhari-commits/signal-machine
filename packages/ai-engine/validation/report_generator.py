#!/usr/bin/env python3
"""
Validation Report Generator — Daily metrics + Backtest Comparison.

Compares live paper trading results against the validated backtest baseline.
Raises alerts when metrics deviate materially.

DO NOT modify any signal logic. This is observation-only.

Usage:
    from validation.report_generator import ReportGenerator
    rg = ReportGenerator()
    report = rg.generate_full_report()
    rg.print_report(report)
"""
from __future__ import annotations

import json
import time
import sqlite3
import statistics
from pathlib import Path
from typing import Dict, List, Optional, Any
from loguru import logger

from .paper_trader import ValidationDB, DB_PATH

# ══════════════════════════════════════════════════════════════════════
# BACKTEST BASELINE (from V2f optimizer results)
# ══════════════════════════════════════════════════════════════════════

BACKTEST_BASELINE = {
    "strategy_version": "ema_v5_v2f",
    "net_profit_pct": 542.86,        # ($64,286 / $10,000 - 1) * 100
    "profit_factor": 1.55,
    "sharpe": 3.69,
    "sortino": 2.97,
    "calmar": 9.36,
    "recovery_factor": 7545.50,
    "max_drawdown_pct": 8.52,
    "win_rate_pct": 48.5,
    "expectancy_per_trade": 35.13,
    "avg_r_multiple": 1.41,
    "avg_hold_hours": 8.5,           # Estimated from max_hold=20h
    "avg_mae_pct": 3.2,              # Estimated from trail_atr=0.3
    "avg_mfe_pct": 8.5,              # Estimated from tp1=1.3R
    "total_trades": 1830,
    "profitable_symbols_pct": 60.7,
    # Deployment criteria thresholds
    "min_pf": 1.50,
    "min_sharpe": 2.5,
    "min_calmar": 4.0,
    "max_dd": 10.0,
    "min_wf_pct": 55.0,
    "min_oos_pct": 55.0,
}

# ══════════════════════════════════════════════════════════════════════
# DEVIATION THRESHOLDS (alert when exceeded)
# ══════════════════════════════════════════════════════════════════════

DEVIATION_THRESHOLDS = {
    "profit_factor": {"warn": 15, "critical": 25, "direction": "below"},
    "win_rate": {"warn": 10, "critical": 20, "direction": "below"},
    "expectancy": {"warn": 20, "critical": 40, "direction": "below"},
    "max_drawdown": {"warn": 30, "critical": 50, "direction": "above"},
    "avg_mae": {"warn": 25, "critical": 50, "direction": "above"},
    "entry_slippage": {"warn": 50, "critical": 100, "direction": "above"},
}

# Minimum sample sizes before alerting
MIN_SAMPLE_ALERT = 20
MIN_SAMPLE_CRITICAL = 50


# ══════════════════════════════════════════════════════════════════════
# REPORT GENERATOR
# ══════════════════════════════════════════════════════════════════════

class ReportGenerator:
    """
    Generates validation reports comparing live paper trading to backtest.
    
    Usage:
        rg = ReportGenerator()
        report = rg.generate_full_report()
        rg.print_report(report)
        alerts = rg.check_deviations()
    """

    def __init__(self):
        self.db = ValidationDB()
        self.baseline = BACKTEST_BASELINE

    # ── Full Report ──

    def generate_full_report(self, lookback_days: int = 30) -> Dict:
        """Generate comprehensive validation report."""
        trades = self.db.get_all_trades()
        if not trades:
            return {"status": "NO_DATA", "message": "No completed trades yet"}

        # Filter to lookback window
        cutoff = time.time() - (lookback_days * 86400)
        recent = [t for t in trades if t.get("entry_time", 0) >= cutoff]

        # ── Core Metrics ──
        pnls = [t["net_pnl"] for t in recent]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gp = sum(wins) if wins else 0
        gl = abs(sum(losses)) if losses else 1

        r_multiples = [t["realized_r"] for t in recent]
        hold_times = [t["hold_minutes"] for t in recent]
        mae_vals = [t["mae_pct"] for t in recent]
        mfe_vals = [t["mfe_pct"] for t in recent]
        deviations = [t["deviation_pct"] for t in recent if t.get("deviation_pct", 0) > 0]

        total = len(recent)
        net_pnl = sum(pnls)
        pf = gp / gl if gl > 0 else 999
        wr = len(wins) / total * 100 if total > 0 else 0
        exp = net_pnl / total if total > 0 else 0
        avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0
        avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0
        avg_mae = sum(mae_vals) / len(mae_vals) if mae_vals else 0
        avg_mfe = sum(mfe_vals) / len(mfe_vals) if mfe_vals else 0
        avg_dev = sum(deviations) / len(deviations) if deviations else 0

        # ── Equity Curve Metrics ──
        # Build equity curve from cumulative PnL
        ec = [10000]
        for p in pnls:
            ec.append(ec[-1] + p)
        ec_arr = ec

        peak = ec_arr[0]
        max_dd = 0
        for eq in ec_arr:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

        # Sharpe (annualized from hourly returns)
        if len(ec_arr) > 2:
            rets = [(ec_arr[i] - ec_arr[i-1]) / ec_arr[i-1] for i in range(1, len(ec_arr)) if ec_arr[i-1] > 0]
            if rets and statistics.stdev(rets) > 0:
                sharpe = statistics.mean(rets) / statistics.stdev(rets) * (365.25 * 24) ** 0.5
            else:
                sharpe = 0
        else:
            sharpe = 0

        # Sortino
        if len(ec_arr) > 2:
            rets = [(ec_arr[i] - ec_arr[i-1]) / ec_arr[i-1] for i in range(1, len(ec_arr)) if ec_arr[i-1] > 0]
            neg_rets = [r for r in rets if r < 0]
            if neg_rets and statistics.stdev(neg_rets) > 0:
                sortino = statistics.mean(rets) / statistics.stdev(neg_rets) * (365.25 * 24) ** 0.5
            else:
                sortino = sharpe
        else:
            sortino = 0

        # Calmar
        days = lookback_days
        years = days / 365.25 if days > 0 else 1
        total_equity = ec_arr[-1]
        cagr = (total_equity / 10000) ** (1 / years) - 1 if total_equity > 0 and years > 0 else 0
        calmar = cagr / (max_dd / 100) if max_dd > 0 else 0

        # Recovery Factor
        rf = net_pnl / max_dd if max_dd > 0 else 0

        # ── Per-Symbol Breakdown ──
        sym_stats = {}
        for t in recent:
            s = t["symbol"]
            if s not in sym_stats:
                sym_stats[s] = {"trades": 0, "wins": 0, "pnl": 0, "r_total": 0}
            sym_stats[s]["trades"] += 1
            if t["net_pnl"] > 0:
                sym_stats[s]["wins"] += 1
            sym_stats[s]["pnl"] += t["net_pnl"]
            sym_stats[s]["r_total"] += t.get("realized_r", 0)

        for s in sym_stats:
            st = sym_stats[s]
            st["win_rate"] = st["wins"] / st["trades"] * 100 if st["trades"] > 0 else 0
            st["avg_r"] = st["r_total"] / st["trades"] if st["trades"] > 0 else 0

        # ── Per-Side Breakdown ──
        longs = [t for t in recent if t["side"] == "LONG"]
        shorts = [t for t in recent if t["side"] == "SHORT"]

        # ── Build Report ──
        report = {
            "status": "OK",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "lookback_days": lookback_days,
            "sample_size": total,
            "metrics": {
                "net_pnl": net_pnl,
                "profit_factor": round(pf, 2),
                "sharpe": round(sharpe, 2),
                "sortino": round(sortino, 2),
                "calmar": round(calmar, 2),
                "recovery_factor": round(rf, 2),
                "max_drawdown_pct": round(max_dd, 2),
                "win_rate_pct": round(wr, 1),
                "expectancy": round(exp, 2),
                "avg_r_multiple": round(avg_r, 2),
                "avg_hold_minutes": round(avg_hold, 1),
                "avg_mae_pct": round(avg_mae, 2),
                "avg_mfe_pct": round(avg_mfe, 2),
                "avg_deviation_from_bt": round(avg_dev, 2),
                "cagr_pct": round(cagr * 100, 2),
                "final_equity": round(total_equity, 2),
            },
            "breakdown": {
                "longs": {"count": len(longs), "pnl": sum(t["net_pnl"] for t in longs)},
                "shorts": {"count": len(shorts), "pnl": sum(t["net_pnl"] for t in shorts)},
                "top_symbols": sorted(sym_stats.items(), key=lambda x: x[1]["pnl"], reverse=True)[:10],
                "bottom_symbols": sorted(sym_stats.items(), key=lambda x: x[1]["pnl"])[:5],
            },
            "baseline_comparison": self._compare_to_baseline({
                "profit_factor": pf,
                "sharpe": sharpe,
                "calmar": calmar,
                "recovery_factor": rf,
                "max_drawdown_pct": max_dd,
                "win_rate_pct": wr,
                "expectancy": exp,
            }),
            "recent_trades": [
                {
                    "symbol": t["symbol"], "side": t["side"],
                    "entry": t["entry_price"], "exit": t["exit_price"],
                    "pnl": round(t["net_pnl"], 2), "r": round(t.get("realized_r", 0), 1),
                    "reason": t["exit_reason"],
                    "mae": round(t["mae_pct"], 2), "mfe": round(t["mfe_pct"], 2),
                    "hold": round(t["hold_minutes"], 0),
                    "dev": round(t.get("deviation_pct", 0), 1),
                }
                for t in recent[-20:]  # Last 20 trades
            ],
        }

        return report

    def _compare_to_baseline(self, live_metrics: Dict) -> Dict:
        """Compare live metrics to backtest baseline."""
        comparison = {}
        for metric, live_val in live_metrics.items():
            bt_val = self.baseline.get(metric, 0)
            if bt_val == 0:
                comparison[metric] = {"live": live_val, "backtest": bt_val, "deviation_pct": 0, "status": "N/A"}
                continue

            dev = (live_val - bt_val) / abs(bt_val) * 100
            # Determine status
            if metric in ("max_drawdown_pct",):
                # Lower is better
                status = "✅" if live_val <= bt_val * 1.25 else "⚠️" if live_val <= bt_val * 1.5 else "❌"
            else:
                # Higher is better (PF, Sharpe, etc.)
                status = "✅" if live_val >= bt_val * 0.85 else "⚠️" if live_val >= bt_val * 0.70 else "❌"

            comparison[metric] = {
                "live": round(live_val, 2),
                "backtest": round(bt_val, 2),
                "deviation_pct": round(dev, 1),
                "status": status,
            }
        return comparison

    # ── Deviation Alerts ──

    def check_deviations(self) -> List[Dict]:
        """Check all metrics against deviation thresholds. Returns alerts."""
        trades = self.db.get_all_trades()
        if len(trades) < MIN_SAMPLE_ALERT:
            return []

        alerts = []
        n = len(trades)
        pnls = [t["net_pnl"] for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gp = sum(wins) if wins else 0
        gl = abs(sum(losses)) if losses else 1
        pf = gp / gl if gl > 0 else 999
        wr = len(wins) / n * 100
        exp = sum(pnls) / n

        # Build equity curve for DD
        ec = [10000]
        for p in pnls:
            ec.append(ec[-1] + p)
        peak = ec[0]
        max_dd = 0
        for eq in ec:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)

        # MAE
        mae_vals = [t["mae_pct"] for t in trades]
        avg_mae = sum(mae_vals) / len(mae_vals) if mae_vals else 0

        metrics_to_check = {
            "profit_factor": {"live": pf, "bt": self.baseline["profit_factor"]},
            "win_rate": {"live": wr, "bt": self.baseline["win_rate_pct"]},
            "expectancy": {"live": exp, "bt": self.baseline["expectancy_per_trade"]},
            "max_drawdown": {"live": max_dd, "bt": self.baseline["max_drawdown_pct"]},
            "avg_mae": {"live": avg_mae, "bt": self.baseline["avg_mae_pct"]},
        }

        for metric_name, values in metrics_to_check.items():
            if metric_name not in DEVIATION_THRESHOLDS:
                continue

            threshold = DEVIATION_THRESHOLDS[metric_name]
            live = values["live"]
            bt = values["bt"]

            if bt == 0:
                continue

            dev_pct = (live - bt) / abs(bt) * 100

            # Check direction
            if threshold["direction"] == "below":
                triggered_warn = dev_pct < -threshold["warn"]
                triggered_crit = dev_pct < -threshold["critical"]
            else:
                triggered_warn = dev_pct > threshold["warn"]
                triggered_crit = dev_pct > threshold["critical"]

            if triggered_crit:
                severity = "critical"
            elif triggered_warn:
                severity = "warning"
            else:
                continue

            alert = {
                "timestamp": time.time(),
                "alert_type": f"{metric_name}_{severity}",
                "metric_name": metric_name,
                "expected_value": bt,
                "actual_value": live,
                "deviation_pct": round(dev_pct, 1),
                "sample_size": n,
                "severity": severity,
                "message": f"{metric_name}: live={live:.2f} vs bt={bt:.2f} ({dev_pct:+.1f}%) [n={n}]",
            }
            alerts.append(alert)
            self.db.record_alert(alert)

        return alerts

    # ── Print Report ──

    def print_report(self, report: Dict) -> None:
        """Pretty-print the validation report."""
        if report.get("status") == "NO_DATA":
            print("\n  ⏳ No completed trades yet. Waiting for paper trading data.\n")
            return

        m = report["metrics"]
        bc = report["baseline_comparison"]

        print(f"\n{'═' * 100}")
        print(f"  EMA_V5 PAPER TRADING VALIDATION REPORT")
        print(f"  {report['timestamp']} | Lookback: {report['lookback_days']}d | Sample: {report['sample_size']} trades")
        print(f"{'═' * 100}")

        print(f"\n  {'Metric':<30} {'Live':>12} {'Backtest':>12} {'Deviation':>12} {'Status':>8}")
        print(f"  {'─' * 80}")

        for metric, comp in bc.items():
            name = metric.replace("_", " ").title()
            dev_str = f"{comp['deviation_pct']:+.1f}%"
            print(f"  {name:<30} {comp['live']:>12.2f} {comp['backtest']:>12.2f} {dev_str:>12} {comp['status']:>8}")

        print(f"\n  Additional Metrics:")
        print(f"    Net PnL:           ${m['net_pnl']:>+.2f}")
        print(f"    Expectancy:        ${m['expectancy']:.2f}/trade")
        print(f"    Avg R-Multiple:    {m['avg_r_multiple']:.2f}R")
        print(f"    Avg Hold Time:     {m['avg_hold_minutes']:.0f} min")
        print(f"    Avg MAE:           {m['avg_mae_pct']:.2f}%")
        print(f"    Avg MFE:           {m['avg_mfe_pct']:.2f}%")
        print(f"    Avg BT Deviation:  {m['avg_deviation_from_bt']:.1f}%")
        print(f"    Final Equity:      ${m['final_equity']:,.2f}")

        # Long/Short breakdown
        bd = report["breakdown"]
        print(f"\n  Side Breakdown:")
        print(f"    LONG:  {bd['longs']['count']} trades | PnL=${bd['longs']['pnl']:+.2f}")
        print(f"    SHORT: {bd['shorts']['count']} trades | PnL=${bd['shorts']['pnl']:+.2f}")

        # Top/Bottom symbols
        if bd["top_symbols"]:
            print(f"\n  Top 5 Symbols:")
            for sym, stats in bd["top_symbols"][:5]:
                print(f"    {sym:<20} trades={stats['trades']:>3} WR={stats['win_rate']:.0f}% PnL=${stats['pnl']:>+.2f} avgR={stats['avg_r']:.1f}")

        if bd["bottom_symbols"]:
            print(f"\n  Bottom 5 Symbols:")
            for sym, stats in bd["bottom_symbols"][:5]:
                print(f"    {sym:<20} trades={stats['trades']:>3} WR={stats['win_rate']:.0f}% PnL=${stats['pnl']:>+.2f} avgR={stats['avg_r']:.1f}")

        # Recent trades
        if report["recent_trades"]:
            print(f"\n  Last 10 Trades:")
            for t in report["recent_trades"][-10:]:
                emoji = "✅" if t["pnl"] > 0 else "❌"
                print(f"    {emoji} {t['side']:<6} {t['symbol']:<16} entry={t['entry']:.4f} exit={t['exit']:.4f} "
                      f"PnL=${t['pnl']:>+.2f} R={t['r']:+.1f} {t['reason']:<8} MAE={t['mae']:.1f}% MFE={t['mfe']:.1f}% "
                      f"hold={t['hold']:.0f}m dev={t['dev']:.1f}%")

        # Deviation alerts
        alerts = self.check_deviations()
        if alerts:
            print(f"\n  ⚠️  DEVIATION ALERTS:")
            for a in alerts:
                icon = "🔴" if a["severity"] == "critical" else "🟡"
                print(f"    {icon} {a['message']}")

        print(f"\n{'═' * 100}")

    # ── Bridge Export ──

    def export_to_bridge(self, report: Dict) -> None:
        """Export report to JSON bridge for dashboard consumption."""
        bridge_file = Path(__file__).resolve().parent.parent / "data" / "bridge" / "validation_report.json"
        bridge_file.parent.mkdir(parents=True, exist_ok=True)
        with open(bridge_file, "w") as f:
            json.dump(report, f, indent=2, default=str)


# ══════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    rg = ReportGenerator()
    report = rg.generate_full_report()
    rg.print_report(report)
    rg.export_to_bridge(report)
