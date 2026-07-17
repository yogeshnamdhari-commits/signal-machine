"""
Production Validator — FIX #10: Generate validation report after deployment.

Outputs production_validation_report.md with all required metrics.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Dict
from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


class ProductionValidator:
    """
    FIX #10: Production validation report generator.
    """
    
    def __init__(self) -> None:
        self._report_path = Path(__file__).resolve().parent.parent / "data" / "production_validation_report.md"
    
    def generate_report(self, funnel: Dict = None, signal_filter_stats: Dict = None,
                        session_stats: Dict = None, symbol_stats: Dict = None) -> str:
        """Generate full production validation report."""
        try:
            db = sqlite3.connect(str(_DB_PATH), timeout=10)
            db.row_factory = sqlite3.Row
            
            trades = db.execute("""
                SELECT * FROM positions
                WHERE status = 'closed' AND pnl IS NOT NULL
                ORDER BY opened_at
            """).fetchall()
            trades = [dict(t) for t in trades]
            db.close()
            
            if not trades:
                return "No trades available for validation."
            
            n = len(trades)
            wins = [t for t in trades if t["pnl"] > 0]
            losses = [t for t in trades if t["pnl"] <= 0]
            total_pnl = sum(t["pnl"] for t in trades)
            win_pnl = sum(t["pnl"] for t in wins)
            loss_pnl = abs(sum(t["pnl"] for t in losses))
            wr = len(wins) / n * 100
            pf = win_pnl / loss_pnl if loss_pnl > 0 else 99.9
            exp = total_pnl / n
            
            # Holding time
            holds = [t["hold_minutes"] for t in trades if t["hold_minutes"]]
            avg_hold = sum(holds) / len(holds) if holds else 0
            
            # Regime breakdown
            regime_counts = {}
            for t in trades:
                r = t["regime"] or "unknown"
                if r not in regime_counts:
                    regime_counts[r] = {"trades": 0, "pnl": 0, "wins": 0}
                regime_counts[r]["trades"] += 1
                regime_counts[r]["pnl"] += t["pnl"]
                regime_counts[r]["wins"] += 1 if t["pnl"] > 0 else 0
            
            # Session breakdown
            session_counts = {}
            for t in trades:
                s = t["session"] or "unknown"
                if s not in session_counts:
                    session_counts[s] = {"trades": 0, "pnl": 0, "wins": 0}
                session_counts[s]["trades"] += 1
                session_counts[s]["pnl"] += t["pnl"]
                session_counts[s]["wins"] += 1 if t["pnl"] > 0 else 0
            
            # Symbol breakdown
            sym_counts = {}
            for t in trades:
                s = t["symbol"]
                if s not in sym_counts:
                    sym_counts[s] = {"trades": 0, "pnl": 0, "wins": 0}
                sym_counts[s]["trades"] += 1
                sym_counts[s]["pnl"] += t["pnl"]
                sym_counts[s]["wins"] += 1 if t["pnl"] > 0 else 0
            
            # Build report
            report = f"""# Production Validation Report
Generated: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}

## System Overview
| Metric | Value |
|--------|-------|
| Total Trades | {n} |
| Win Rate | {wr:.1f}% |
| Profit Factor | {pf:.2f} |
| Expectancy | ${exp:.2f} |
| Total PnL | ${total_pnl:,.2f} |
| Avg Winner | ${win_pnl/len(wins):.2f} |
| Avg Loser | ${-loss_pnl/len(losses):.2f} |
| Avg Hold Time | {avg_hold:.0f} min |

## Holding Time Analysis
"""
            hold_buckets = {"0-15m": (0, 15), "15-30m": (15, 30), "30-60m": (30, 60),
                          "1-2hr": (60, 120), "2-4hr": (120, 240), "4hr+": (240, 9999)}
            report += "| Bucket | Trades | Win% | PF | Expectancy | PnL |\n|--------|--------|------|----|-----------|----|\n"
            for bucket, (lo, hi) in hold_buckets.items():
                group = [t for t in trades if lo <= (t["hold_minutes"] or 0) < hi]
                if group:
                    gw = [t for t in group if t["pnl"] > 0]
                    gl = [t for t in group if t["pnl"] <= 0]
                    gwp = sum(t["pnl"] for t in gw)
                    glp = abs(sum(t["pnl"] for t in gl))
                    gpf = gwp / glp if glp > 0 else 99.9
                    gexp = sum(t["pnl"] for t in group) / len(group)
                    gwr = len(gw) / len(group) * 100
                    gpnl = sum(t["pnl"] for t in group)
                    report += f"| {bucket} | {len(group)} | {gwr:.1f}% | {gpf:.2f} | ${gexp:.2f} | ${gpnl:,.2f} |\n"
            
            report += "\n## Regime Breakdown\n"
            report += "| Regime | Trades | Win% | PnL |\n|--------|--------|------|----|\n"
            for r, d in sorted(regime_counts.items(), key=lambda x: x[1]["pnl"], reverse=True):
                wr_r = d["wins"] / d["trades"] * 100 if d["trades"] > 0 else 0
                report += f"| {r} | {d['trades']} | {wr_r:.1f}% | ${d['pnl']:,.2f} |\n"
            
            report += "\n## Session Breakdown\n"
            report += "| Session | Trades | Win% | PnL |\n|---------|--------|------|----|\n"
            for s, d in sorted(session_counts.items(), key=lambda x: x[1]["pnl"], reverse=True):
                wr_s = d["wins"] / d["trades"] * 100 if d["trades"] > 0 else 0
                report += f"| {s} | {d['trades']} | {wr_s:.1f}% | ${d['pnl']:,.2f} |\n"
            
            report += "\n## Top 10 Symbols\n"
            report += "| Symbol | Trades | Win% | PnL |\n|--------|--------|------|----|\n"
            for s, d in sorted(sym_counts.items(), key=lambda x: x[1]["pnl"], reverse=True)[:10]:
                wr_s = d["wins"] / d["trades"] * 100 if d["trades"] > 0 else 0
                report += f"| {s} | {d['trades']} | {wr_s:.1f}% | ${d['pnl']:,.2f} |\n"
            
            report += "\n## Bottom 10 Symbols\n"
            report += "| Symbol | Trades | Win% | PnL |\n|--------|--------|------|----|\n"
            for s, d in sorted(sym_counts.items(), key=lambda x: x[1]["pnl"])[:10]:
                wr_s = d["wins"] / d["trades"] * 100 if d["trades"] > 0 else 0
                report += f"| {s} | {d['trades']} | {wr_s:.1f}% | ${d['pnl']:,.2f} |\n"

            # ── RR Analysis ──
            rr_buckets = {"<1.0": (0, 1.0), "1.0-1.5": (1.0, 1.5), "1.5-2.0": (1.5, 2.0),
                         "2.0-2.5": (2.0, 2.5), "2.5-3.0": (2.5, 3.0), "3.0+": (3.0, 999)}
            report += "\n## Risk-Reward Analysis\n"
            report += "| RR Bucket | Trades | Win% | PF | Expectancy | PnL |\n|-----------|--------|------|----|-----------|----|\n"
            for bucket, (lo, hi) in rr_buckets.items():
                group = [t for t in trades if lo <= (t.get("risk_reward") or 0) < hi]
                if group:
                    gw = [t for t in group if t["pnl"] > 0]
                    gl = [t for t in group if t["pnl"] <= 0]
                    gwp = sum(t["pnl"] for t in gw)
                    glp = abs(sum(t["pnl"] for t in gl))
                    gpf = gwp / glp if glp > 0 else 99.9
                    gexp = sum(t["pnl"] for t in group) / len(group)
                    gwr = len(gw) / len(group) * 100
                    gpnl = sum(t["pnl"] for t in group)
                    report += f"| {bucket} | {len(group)} | {gwr:.1f}% | {gpf:.2f} | ${gexp:.2f} | ${gpnl:,.2f} |\n"

            # ── Exit Reason Analysis ──
            exit_reasons = {}
            for t in trades:
                r = t.get("exit_reason", "unknown") or "unknown"
                if r not in exit_reasons:
                    exit_reasons[r] = {"trades": 0, "pnl": 0, "wins": 0}
                exit_reasons[r]["trades"] += 1
                exit_reasons[r]["pnl"] += t["pnl"]
                exit_reasons[r]["wins"] += 1 if t["pnl"] > 0 else 0

            report += "\n## Exit Reason Analysis\n"
            report += "| Reason | Trades | Win% | PnL |\n|--------|--------|------|----|\n"
            for r, d in sorted(exit_reasons.items(), key=lambda x: x[1]["pnl"], reverse=True):
                wr_r = d["wins"] / d["trades"] * 100 if d["trades"] > 0 else 0
                report += f"| {r} | {d['trades']} | {wr_r:.1f}% | ${d['pnl']:,.2f} |\n"
            
            if funnel:
                report += f"\n## Latest Cycle Funnel\n"
                report += f"| Gate | Count |\n|------|-------|\n"
                for k, v in funnel.items():
                    if isinstance(v, (int, float)):
                        report += f"| {k} | {v} |\n"
            
            if session_stats:
                report += f"\n## Session Filter Stats\n"
                report += json.dumps(session_stats, indent=2)
            
            if symbol_stats:
                report += f"\n## Symbol Expectancy Stats\n"
                report += json.dumps(symbol_stats, indent=2)
            
            # Write report
            self._report_path.write_text(report)
            logger.info("📊 Production validation report saved to {}", self._report_path)
            
            return report
            
        except Exception as e:
            logger.warning("ProductionValidator failed: {}", e)
            return f"Error generating report: {e}"
