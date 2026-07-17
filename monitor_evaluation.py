#!/usr/bin/env python3
"""
EMA V5 Evaluation Period Monitor
Runs daily to track strategy performance metrics during evaluation period.

Metrics tracked:
- Signals generated
- Win rate
- Profit factor
- Average R multiple
- Missed opportunities (rejected setups that later met profit criteria)

Usage:
    python monitor_evaluation.py              # Daily report
    python monitor_evaluation.py --live       # Live dashboard
"""
import sqlite3
import statistics
import sys
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path("packages/ai-engine/data/ema_v5_calibration.db")
FORWARD_DB = Path("packages/ai-engine/data/forward_test.db")


def get_calibration_stats():
    """Get calibration DB statistics."""
    if not DB_PATH.exists():
        return None
    
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    
    stats = {}
    
    # Total candidates
    c.execute("SELECT COUNT(*) FROM candidates")
    stats["total_candidates"] = c.fetchone()[0]
    
    # Passed vs rejected
    c.execute("SELECT COUNT(*) FROM candidates WHERE passed = 1")
    stats["passed"] = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM candidates WHERE passed = 0")
    stats["rejected"] = c.fetchone()[0]
    
    # With outcomes
    c.execute("SELECT COUNT(*) FROM candidates WHERE outcome_tracked = 1")
    stats["tracked"] = c.fetchone()[0]
    
    # Rejected with outcomes
    c.execute("""
        SELECT COUNT(*), AVG(return_pct), 
               SUM(CASE WHEN return_pct > 0 THEN 1 ELSE 0 END),
               AVG(mfe), AVG(mae)
        FROM candidates 
        WHERE passed = 0 AND outcome_tracked = 1 AND return_pct IS NOT NULL
    """)
    row = c.fetchone()
    stats["rejected_tracked"] = row[0]
    stats["rejected_mean_return"] = row[1]
    stats["rejected_profitable"] = row[2]
    stats["rejected_mean_mfe"] = row[3]
    stats["rejected_mean_mae"] = row[4]
    
    conn.close()
    return stats


def get_forward_trade_stats():
    """Get forward trade statistics."""
    if not FORWARD_DB.exists():
        return None
    
    conn = sqlite3.connect(str(FORWARD_DB))
    c = conn.cursor()
    
    stats = {}
    
    # Total signals
    c.execute("SELECT COUNT(*) FROM forward_signals")
    stats["total_signals"] = c.fetchone()[0]
    
    # Total trades
    c.execute("SELECT COUNT(*) FROM forward_trades")
    stats["total_trades"] = c.fetchone()[0]
    
    if stats["total_trades"] > 0:
        # Win rate
        c.execute("""
            SELECT COUNT(*) FROM forward_trades 
            WHERE net_pnl > 0
        """)
        stats["winning_trades"] = c.fetchone()[0]
        stats["win_rate"] = stats["winning_trades"] / stats["total_trades"] * 100
        
        # Profit factor
        c.execute("""
            SELECT SUM(CASE WHEN net_pnl > 0 THEN net_pnl ELSE 0 END),
                   SUM(CASE WHEN net_pnl < 0 THEN ABS(net_pnl) ELSE 0 END)
            FROM forward_trades
        """)
        gross_profit, gross_loss = c.fetchone()
        stats["profit_factor"] = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # Average R multiple
        c.execute("SELECT AVG(realized_r) FROM forward_trades WHERE realized_r IS NOT NULL")
        stats["avg_r"] = c.fetchone()[0]
        
        # Average PnL
        c.execute("SELECT AVG(net_pnl) FROM forward_trades")
        stats["avg_pnl"] = c.fetchone()[0]
        
        # Total PnL
        c.execute("SELECT SUM(net_pnl) FROM forward_trades")
        stats["total_pnl"] = c.fetchone()[0]
    
    conn.close()
    return stats


def print_report():
    """Print evaluation period report."""
    print("=" * 60)
    print("  EMA V5 EVALUATION PERIOD REPORT")
    print(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print()
    
    cal = get_calibration_stats()
    fwd = get_forward_trade_stats()
    
    if cal:
        print("═══ CALIBRATION DATABASE ═══")
        print(f"  Total candidates:     {cal['total_candidates']}")
        print(f"  Passed:               {cal['passed']}")
        print(f"  Rejected:             {cal['rejected']}")
        print(f"  With outcomes:        {cal['tracked']}")
        print()
        
        if cal['rejected_tracked'] > 0:
            print("  Rejected candidates (with 24h outcomes):")
            print(f"    Count:              {cal['rejected_tracked']}")
            print(f"    Mean return:        {cal['rejected_mean_return']:+.2f}%")
            print(f"    Profitable:         {cal['rejected_profitable']}/{cal['rejected_tracked']} "
                  f"({cal['rejected_profitable']/cal['rejected_tracked']*100:.1f}%)")
            print(f"    Mean MFE:           {cal['rejected_mean_mfe']:+.2f}%")
            print(f"    Mean MAE:           {cal['rejected_mean_mae']:+.2f}%")
        print()
    
    if fwd:
        print("═══ FORWARD TRADES ═══")
        print(f"  Total signals:        {fwd['total_signals']}")
        print(f"  Total trades:         {fwd['total_trades']}")
        
        if fwd['total_trades'] > 0:
            print(f"  Win rate:             {fwd['win_rate']:.1f}%")
            print(f"  Profit factor:        {fwd['profit_factor']:.2f}")
            print(f"  Average R:            {fwd['avg_r']:+.2f}")
            print(f"  Average PnL:          ${fwd['avg_pnl']:+.2f}")
            print(f"  Total PnL:            ${fwd['total_pnl']:+.2f}")
        print()
    
    print("═══ EVALUATION CRITERIA ═══")
    print()
    print("  Monitor these metrics over the evaluation period:")
    print("  1. Signal frequency (signals/week)")
    print("  2. Win rate (target: >50%)")
    print("  3. Profit factor (target: >1.5)")
    print("  4. Average R multiple (target: >1.0)")
    print("  5. Missed opportunities (rejected setups that later met profit criteria)")
    print()
    print("  Decision points:")
    print("  - If signals are too rare AND missed opportunities are high:")
    print("    → Consider recalibrating thresholds")
    print("  - If signals are rare BUT trades are profitable:")
    print("    → Strategy is working as designed")
    print("  - If signals are frequent BUT win rate is low:")
    print("    → Consider tightening thresholds")
    print()


if __name__ == "__main__":
    print_report()
