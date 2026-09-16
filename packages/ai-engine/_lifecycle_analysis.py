#!/usr/bin/env python3
"""
EMA V5 Candidate Lifecycle Analysis — Outcome-based validation.

This script analyzes the candidate_lifecycle database to answer:
1. Are rejected candidates profitable or losing?
2. Which rejection stage kills the most profitable setups?
3. Would lowering thresholds improve overall profitability?

Usage:
    python _lifecycle_analysis.py
    python _lifecycle_analysis.py --hours 48
"""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime, timedelta


DB_PATH = Path("data/ema_v5_lifecycle.db")


def analyze_lifecycle(hours: int = 24) -> str:
    """Analyze candidate lifecycle outcomes."""
    if not DB_PATH.exists():
        return f"❌ Database not found: {DB_PATH}\n   Run the scanner with lifecycle tracking enabled first."
    
    conn = sqlite3.connect(str(DB_PATH))
    cutoff = datetime.now().timestamp() - (hours * 3600)
    
    lines = []
    lines.append("=" * 80)
    lines.append(f"EMA V5 CANDIDATE LIFECYCLE ANALYSIS (Last {hours} hours)")
    lines.append("=" * 80)
    lines.append("")
    
    # 1. Funnel Summary
    cur = conn.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(regime_pass) as regime_pass,
            SUM(trend_pass) as trend_pass,
            SUM(pullback_pass) as pullback_pass,
            SUM(candle_pass) as candle_pass,
            SUM(volume_pass) as volume_pass,
            SUM(confidence_pass) as confidence_pass,
            SUM(signal_pass) as signal_pass
        FROM candidate_lifecycle
        WHERE timestamp > ?
    """, (cutoff,))
    
    row = cur.fetchone()
    total = row[0] or 0
    
    lines.append("📊 FUNNEL SUMMARY")
    lines.append("-" * 40)
    lines.append(f"  Total candidates:        {total:>6}")
    lines.append(f"  Regime pass:             {row[1] or 0:>6} ({(row[1] or 0)/max(total,1)*100:.1f}%)")
    lines.append(f"  Trend pass:              {row[2] or 0:>6} ({(row[2] or 0)/max(total,1)*100:.1f}%)")
    lines.append(f"  Pullback pass:           {row[3] or 0:>6} ({(row[3] or 0)/max(total,1)*100:.1f}%)")
    lines.append(f"  Candle pass:             {row[4] or 0:>6} ({(row[4] or 0)/max(total,1)*100:.1f}%)")
    lines.append(f"  Volume pass:             {row[5] or 0:>6} ({(row[5] or 0)/max(total,1)*100:.1f}%)")
    lines.append(f"  Confidence pass:         {row[6] or 0:>6} ({(row[6] or 0)/max(total,1)*100:.1f}%)")
    lines.append(f"  Signal generated:        {row[7] or 0:>6} ({(row[7] or 0)/max(total,1)*100:.1f}%)")
    lines.append("")
    
    # 2. Rejection Analysis with Outcomes
    cur = conn.execute("""
        SELECT 
            rejection_stage,
            COUNT(*) as count,
            AVG(trend_score) as avg_trend,
            AVG(volume_score) as avg_volume,
            AVG(confidence) as avg_confidence,
            SUM(CASE WHEN outcome = 'profitable' THEN 1 ELSE 0 END) as profitable,
            SUM(CASE WHEN outcome = 'losing' THEN 1 ELSE 0 END) as losing,
            SUM(CASE WHEN outcome = 'breakeven' THEN 1 ELSE 0 END) as breakeven,
            AVG(return_1h_pct) as avg_return_1h,
            AVG(return_4h_pct) as avg_return_4h,
            AVG(would_be_mfe) as avg_mfe,
            AVG(would_be_mae) as avg_mae
        FROM candidate_lifecycle
        WHERE timestamp > ? AND outcome_tracked = 1
        GROUP BY rejection_stage
        ORDER BY count DESC
    """, (cutoff,))
    
    lines.append("🔍 REJECTION ANALYSIS WITH OUTCOMES")
    lines.append("-" * 80)
    lines.append(f"  {'Stage':<15} {'Count':>6} {'Profitable':>10} {'Losing':>10} {'Avg 1h%':>8} {'Avg MFE':>8} {'Avg MAE':>8}")
    lines.append("  " + "-" * 76)
    
    for row in cur.fetchall():
        stage = row[0] or "passed"
        count = row[1] or 0
        profitable = row[5] or 0
        losing = row[6] or 0
        avg_return = row[8] or 0
        avg_mfe = row[10] or 0
        avg_mae = row[11] or 0
        
        # Calculate win rate
        tracked = profitable + losing + (row[7] or 0)
        win_rate = profitable / max(tracked, 1) * 100
        
        lines.append(
            f"  {stage:<15} {count:>6} {profitable:>5} ({win_rate:>4.0f}%) "
            f"{losing:>5} ({100-win_rate:>4.0f}%) "
            f"{avg_return:>+7.2f}% {avg_mfe:>+7.2f}% {avg_mae:>+7.2f}%"
        )
    lines.append("")
    
    # 3. Would-Be Trade Analysis (Rejected candidates)
    lines.append("💰 WOULD-BE TRADE ANALYSIS (Rejected Candidates)")
    lines.append("-" * 80)
    
    cur = conn.execute("""
        SELECT 
            rejection_stage,
            COUNT(*) as count,
            AVG(would_be_mfe) as avg_mfe,
            AVG(would_be_mae) as avg_mae,
            AVG(return_1h_pct) as avg_1h,
            AVG(return_4h_pct) as avg_4h,
            MAX(return_1h_pct) as best_1h,
            MIN(return_1h_pct) as worst_1h
        FROM candidate_lifecycle
        WHERE timestamp > ? AND outcome_tracked = 1 AND rejection_stage IS NOT NULL
        GROUP BY rejection_stage
        ORDER BY avg_mfe DESC
    """, (cutoff,))
    
    lines.append(f"  {'Stage':<15} {'Count':>6} {'Avg MFE':>8} {'Avg MAE':>8} {'Avg 1h':>8} {'Best 1h':>8} {'Worst 1h':>8}")
    lines.append("  " + "-" * 76)
    
    for row in cur.fetchall():
        stage = row[0] or "passed"
        count = row[1] or 0
        avg_mfe = row[2] or 0
        avg_mae = row[3] or 0
        avg_1h = row[4] or 0
        best_1h = row[6] or 0
        worst_1h = row[7] or 0
        
        lines.append(
            f"  {stage:<15} {count:>6} {avg_mfe:>+7.2f}% {avg_mae:>+7.2f}% "
            f"{avg_1h:>+7.2f}% {best_1h:>+7.2f}% {worst_1h:>+7.2f}%"
        )
    lines.append("")
    
    # 4. Threshold Impact Analysis
    lines.append("📊 THRESHOLD IMPACT ANALYSIS")
    lines.append("-" * 80)
    
    # Volume threshold impact
    cur = conn.execute("""
        SELECT 
            CASE 
                WHEN volume_ratio < 0.25 THEN '0-0.25'
                WHEN volume_ratio < 0.40 THEN '0.25-0.40'
                WHEN volume_ratio < 0.60 THEN '0.40-0.60'
                WHEN volume_ratio < 0.80 THEN '0.60-0.80'
                ELSE '0.80+'
            END as vol_bucket,
            COUNT(*) as count,
            SUM(CASE WHEN outcome = 'profitable' THEN 1 ELSE 0 END) as profitable,
            SUM(CASE WHEN outcome = 'losing' THEN 1 ELSE 0 END) as losing,
            AVG(return_1h_pct) as avg_return
        FROM candidate_lifecycle
        WHERE timestamp > ? AND outcome_tracked = 1
        GROUP BY vol_bucket
        ORDER BY vol_bucket
    """, (cutoff,))
    
    lines.append("  Volume Ratio Impact:")
    lines.append(f"  {'Bucket':<12} {'Count':>6} {'Profitable':>10} {'Losing':>10} {'Avg 1h%':>8}")
    lines.append("  " + "-" * 56)
    
    for row in cur.fetchall():
        bucket = row[0] or "?"
        count = row[1] or 0
        profitable = row[2] or 0
        losing = row[3] or 0
        avg_return = row[4] or 0
        
        lines.append(
            f"  {bucket:<12} {count:>6} {profitable:>5} ({profitable/max(count,1)*100:>4.0f}%) "
            f"{losing:>5} ({losing/max(count,1)*100:>4.0f}%) {avg_return:>+7.2f}%"
        )
    lines.append("")
    
    # 5. Recommendations
    lines.append("=" * 80)
    lines.append("📋 RECOMMENDATIONS")
    lines.append("=" * 80)
    lines.append("")
    
    # Analyze if volume filter is killing profitable setups
    cur = conn.execute("""
        SELECT 
            COUNT(*) as total_rejected,
            AVG(return_1h_pct) as avg_return,
            SUM(CASE WHEN return_1h_pct > 0.5 THEN 1 ELSE 0 END) as would_have_profited
        FROM candidate_lifecycle
        WHERE timestamp > ? AND outcome_tracked = 1 
        AND rejection_stage = 'volume'
    """, (cutoff,))
    
    vol_row = cur.fetchone()
    vol_rejected = vol_row[0] or 0
    vol_avg_return = vol_row[1] or 0
    vol_would_profit = vol_row[2] or 0
    
    if vol_rejected > 0:
        vol_win_rate = vol_would_profit / vol_rejected * 100
        
        lines.append(f"1. VOLUME FILTER ANALYSIS")
        lines.append(f"   - Candidates rejected by volume: {vol_rejected}")
        lines.append(f"   - Would have been profitable (1h): {vol_would_profit} ({vol_win_rate:.0f}%)")
        lines.append(f"   - Average 1h return if held: {vol_avg_return:+.2f}%")
        lines.append("")
        
        if vol_win_rate > 50:
            lines.append(f"   ⚠️  FINDING: Volume filter is killing {vol_win_rate:.0f}% profitable setups")
            lines.append(f"   → Consider lowering volume threshold")
        elif vol_win_rate < 30:
            lines.append(f"   ✅ FINDING: Volume filter is correctly rejecting losing setups ({100-vol_win_rate:.0f}% losing)")
            lines.append(f"   → Keep current threshold")
        else:
            lines.append(f"   ⚖️  FINDING: Volume filter has mixed results ({vol_win_rate:.0f}% profitable)")
            lines.append(f"   → Needs more data to determine optimal threshold")
        lines.append("")
    
    # 6. Decision Latency Analysis
    lines.append("⏱️  DECISION LATENCY ANALYSIS")
    lines.append("-" * 80)
    
    cur = conn.execute("""
        SELECT 
            rejection_stage,
            COUNT(*) as count,
            AVG(latency_total) as avg_latency,
            AVG(latency_regime) as avg_regime,
            AVG(latency_trend) as avg_trend,
            AVG(latency_pullback) as avg_pullback,
            AVG(latency_candle) as avg_candle,
            AVG(latency_volume) as avg_volume,
            AVG(latency_confidence) as avg_confidence,
            AVG(price_slippage_pct) as avg_slippage
        FROM candidate_lifecycle
        WHERE timestamp > ? AND latency_total > 0
        GROUP BY rejection_stage
        ORDER BY avg_latency DESC
    """, (cutoff,))
    
    lines.append(f"  {'Stage':<15} {'Count':>6} {'Total':>8} {'Regime':>8} {'Trend':>8} {'Pullback':>8} {'Candle':>8} {'Volume':>8}")
    lines.append("  " + "-" * 76)
    
    for row in cur.fetchall():
        stage = row[0] or "passed"
        count = row[1] or 0
        total = row[2] or 0
        regime_lat = row[3] or 0
        trend_lat = row[4] or 0
        pullback_lat = row[5] or 0
        candle_lat = row[6] or 0
        volume_lat = row[7] or 0
        
        lines.append(
            f"  {stage:<15} {count:>6} {total:>7.1f}s {regime_lat:>7.1f}s {trend_lat:>7.1f}s "
            f"{pullback_lat:>7.1f}s {candle_lat:>7.1f}s {volume_lat:>7.1f}s"
        )
    lines.append("")
    
    # Latency distribution
    lines.append("  Latency Distribution (Total):")
    cur = conn.execute("""
        SELECT 
            CASE 
                WHEN latency_total < 1 THEN '<1s'
                WHEN latency_total < 5 THEN '1-5s'
                WHEN latency_total < 30 THEN '5-30s'
                WHEN latency_total < 300 THEN '30s-5m'
                ELSE '>5m'
            END as latency_bucket,
            COUNT(*) as count,
            AVG(return_1h_pct) as avg_return,
            SUM(CASE WHEN outcome = 'profitable' THEN 1 ELSE 0 END) as profitable
        FROM candidate_lifecycle
        WHERE timestamp > ? AND latency_total > 0 AND outcome_tracked = 1
        GROUP BY latency_bucket
        ORDER BY 
            CASE latency_bucket
                WHEN '<1s' THEN 1
                WHEN '1-5s' THEN 2
                WHEN '5-30s' THEN 3
                WHEN '30s-5m' THEN 4
                ELSE 5
            END
    """, (cutoff,))
    
    for row in cur.fetchall():
        bucket = row[0] or "?"
        count = row[1] or 0
        avg_return = row[2] or 0
        profitable = row[3] or 0
        win_rate = profitable / max(count, 1) * 100
        
        lines.append(f"    {bucket:<10} {count:>5} candidates | Avg 1h: {avg_return:>+6.2f}% | Win rate: {win_rate:.0f}%")
    lines.append("")
    
    # 7. Price Slippage Analysis
    lines.append("📉 PRICE SLIPPAGE ANALYSIS")
    lines.append("-" * 80)
    
    cur = conn.execute("""
        SELECT 
            COUNT(*) as count,
            AVG(price_slippage_pct) as avg_slippage,
            AVG(best_achievable_entry) as avg_best,
            AVG(entry_price) as avg_entry,
            SUM(CASE WHEN price_slippage_pct > 0.1 THEN 1 ELSE 0 END) as high_slippage
        FROM candidate_lifecycle
        WHERE timestamp > ? AND price_slippage_pct IS NOT NULL AND signal_pass = 1
    """, (cutoff,))
    
    row = cur.fetchone()
    if row and row[0] > 0:
        count = row[0] or 0
        avg_slippage = row[1] or 0
        avg_best = row[2] or 0
        avg_entry = row[3] or 0
        high_slippage = row[4] or 0
        
        lines.append(f"  Signals with slippage data: {count}")
        lines.append(f"  Average slippage: {avg_slippage:.3f}%")
        lines.append(f"  High slippage (>0.1%): {high_slippage} ({high_slippage/max(count,1)*100:.0f}%)")
        lines.append(f"  Avg entry price: {avg_entry:.4f}")
        lines.append(f"  Avg best achievable: {avg_best:.4f}")
        lines.append("")
        
        if avg_slippage > 0.1:
            lines.append(f"  ⚠️  FINDING: Average slippage is {avg_slippage:.3f}% — signals may be arriving late")
            lines.append(f"   → Consider reducing pipeline stages or optimizing execution")
        else:
            lines.append(f"  ✅ FINDING: Slippage is acceptable ({avg_slippage:.3f}%)")
    lines.append("")
    
    # 7. Precision and Recall Analysis
    lines.append("🎯 SIGNAL QUALITY: PRECISION & RECALL")
    lines.append("-" * 80)
    
    # Calculate precision and recall
    cur = conn.execute("""
        SELECT 
            signal_pass,
            return_4h_pct,
            opportunity_type
        FROM candidate_lifecycle
        WHERE timestamp > ? AND outcome_tracked = 1
    """, (cutoff,))
    
    rows = cur.fetchall()
    
    SUCCESS_THRESHOLD = 1.0  # 1% return in favorable direction within 4h
    
    signals_generated = 0
    signals_profitable = 0
    opportunities_profitable = 0
    
    true_positives = 0
    false_positives = 0
    true_negatives = 0
    false_negatives = 0
    
    for signal_pass, return_4h, opp_type in rows:
        is_profitable = (return_4h or 0) >= SUCCESS_THRESHOLD
        
        if is_profitable:
            opportunities_profitable += 1
        
        if signal_pass:
            signals_generated += 1
            if is_profitable:
                signals_profitable += 1
                true_positives += 1
            else:
                false_positives += 1
        else:
            if is_profitable:
                false_negatives += 1
            else:
                true_negatives += 1
    
    # Calculate metrics
    precision = true_positives / max(signals_generated, 1) * 100
    recall = true_positives / max(opportunities_profitable, 1) * 100
    f1 = 2 * precision * recall / max(precision + recall, 0.01)
    
    lines.append(f"  Success Criteria: Price moved ≥+{SUCCESS_THRESHOLD}% in favorable direction within 4h")
    lines.append("")
    lines.append(f"  Total candidates tracked: {len(rows)}")
    lines.append(f"  Signals generated: {signals_generated}")
    lines.append(f"  Profitable opportunities: {opportunities_profitable}")
    lines.append("")
    
    lines.append("  Confusion Matrix:")
    lines.append(f"                    Actually Profitable    Actually Not Profitable")
    lines.append(f"    Signal Generated    {true_positives:>5} (TP)              {false_positives:>5} (FP)")
    lines.append(f"    Signal Rejected     {false_negatives:>5} (FN)              {true_negatives:>5} (TN)")
    lines.append("")
    
    lines.append("  Key Metrics:")
    lines.append(f"    Precision:  {precision:.1f}%  (Of signals generated, {signals_profitable}/{signals_generated} were profitable)")
    lines.append(f"    Recall:     {recall:.1f}%  (Of profitable opportunities, {true_positives}/{opportunities_profitable} were captured)")
    lines.append(f"    F1 Score:   {f1:.1f}%  (Harmonic mean of precision and recall)")
    lines.append("")
    
    # Interpretation
    lines.append("  Interpretation:")
    if not rows:
        lines.append("    No data available yet")
    elif signals_generated < 100:
        lines.append(f"    ⏳ Sample size too small ({signals_generated} signals, need ≥100)")
        lines.append("       These values are PROVISIONAL — do not use for strategy decisions")
    elif precision > 70 and recall > 70:
        lines.append("    ✅ HIGH PRECISION + HIGH RECALL → Target state")
    elif precision > 70:
        lines.append("    ⚖️  HIGH PRECISION + LOW RECALL → Very selective; misses many good trades")
        lines.append("       Consider relaxing filters to capture more opportunities")
    elif recall > 70:
        lines.append("    ⚖️  LOW PRECISION + HIGH RECALL → Generates many trades, but quality suffers")
        lines.append("       Consider tightening filters to improve signal quality")
    else:
        lines.append("    ⚠️  LOW PRECISION + LOW RECALL → Both selectivity and quality need improvement")
    lines.append("")
    
    # 8. Profit Expectancy
    lines.append("💰 PROFIT EXPECTANCY")
    lines.append("-" * 80)
    
    cur = conn.execute("""
        SELECT 
            return_4h_pct,
            would_be_mfe,
            would_be_mae,
            direction,
            entry_price,
            atr_14
        FROM candidate_lifecycle
        WHERE timestamp > ? AND outcome_tracked = 1 AND signal_pass = 1
    """, (cutoff,))
    
    trades = cur.fetchall()
    
    winners_r = []
    losers_r = []
    
    for return_4h, mfe, mae, direction, entry, atr in trades:
        risk = (atr or 0) * 1.5
        
        if risk <= 0:
            continue
        
        if direction == "LONG":
            pnl_r = (return_4h or 0) / 100 * entry / risk
        else:
            pnl_r = -(return_4h or 0) / 100 * entry / risk
        
        if pnl_r > 0:
            winners_r.append(pnl_r)
        else:
            losers_r.append(abs(pnl_r))
    
    total_trades = len(winners_r) + len(losers_r)
    
    if total_trades > 0:
        win_rate = len(winners_r) / total_trades * 100
        avg_winner = sum(winners_r) / len(winners_r) if winners_r else 0
        avg_loser = sum(losers_r) / len(losers_r) if losers_r else 0
        expectancy = (win_rate / 100 * avg_winner) - ((100 - win_rate) / 100 * avg_loser)
        gross_profit = sum(winners_r)
        gross_loss = sum(losers_r)
        profit_factor = gross_profit / max(gross_loss, 0.01)
        
        lines.append(f"  Trades analyzed: {total_trades}")
        lines.append(f"  Win rate: {win_rate:.1f}%")
        lines.append(f"  Avg winner: +{avg_winner:.2f}R")
        lines.append(f"  Avg loser: -{avg_loser:.2f}R")
        lines.append(f"  Expectancy: {expectancy:+.3f}R per trade")
        lines.append(f"  Profit factor: {profit_factor:.2f}")
        lines.append("")
        
        lines.append("  Interpretation:")
        if total_trades < 100:
            lines.append(f"    ⏳ Sample size too small ({total_trades} trades, need ≥100)")
            lines.append("       These values are PROVISIONAL")
        elif expectancy > 0.1:
            lines.append(f"    ✅ POSITIVE EXPECTANCY ({expectancy:+.3f}R) → Strategy makes money over time")
        elif expectancy > 0:
            lines.append(f"    ⚖️  MARGINAL EXPECTANCY ({expectancy:+.3f}R) → Slightly profitable, needs more data")
        else:
            lines.append(f"    ❌ NEGATIVE EXPECTANCY ({expectancy:+.3f}R) → Strategy loses money over time")
    else:
        lines.append(f"  No completed trades yet")
    lines.append("")
    
    # 9. Production Readiness
    lines.append("📊 PRODUCTION READINESS GATES")
    lines.append("-" * 80)
    
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 1
    """, (cutoff,))
    sig_count = cur.fetchone()[0] or 0
    
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND outcome_tracked = 1 AND signal_pass = 1
    """, (cutoff,))
    trade_count = cur.fetchone()[0] or 0
    
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND outcome_tracked = 1 AND return_4h_pct >= 1.0
    """, (cutoff,))
    profitable_count = cur.fetchone()[0] or 0
    
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND (regime = 'BUY_MODE' OR regime = 'SELL_MODE')
    """, (cutoff,))
    trending_count = cur.fetchone()[0] or 0
    
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND regime = 'NO_TREND'
    """, (cutoff,))
    ranging_count = cur.fetchone()[0] or 0
    
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 1 AND direction = 'LONG'
    """, (cutoff,))
    long_signals = cur.fetchone()[0] or 0
    
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 1 AND direction = 'SHORT'
    """, (cutoff,))
    short_signals = cur.fetchone()[0] or 0
    
    hours_collected = hours
    days_collected = hours_collected / 24
    
    gates = [
        ("Completed trades", trade_count, 100),
        ("Trending market data", trending_count, 50),
        ("Ranging market data", ranging_count, 50),
        ("LONG signals", long_signals, 10),
        ("SHORT signals", short_signals, 10),
        ("Data collection (days)", days_collected, 7),
    ]
    
    all_passed = True
    for name, current, required in gates:
        passed = current >= required
        if not passed:
            all_passed = False
        status = "✅" if passed else "⏳"
        lines.append(f"  {status} {name}: {current} / {required}")
    lines.append("")
    
    if all_passed:
        lines.append("  ✅ SUFFICIENT DATA — Safe to make strategy changes")
    else:
        lines.append("  ⏳ INSUFFICIENT DATA — Continue collecting before making changes")
        lines.append("     Strategy changes now would be based on noise, not signal")
    lines.append("")
    
    # 10. Signal Efficiency
    lines.append("📈 SIGNAL EFFICIENCY")
    lines.append("-" * 80)
    
    winning_signals = profitable_count
    missed_profitable = 0
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 0 AND return_4h_pct >= 1.0
    """, (cutoff,))
    missed_profitable = cur.fetchone()[0] or 0
    
    total_opportunities = winning_signals + missed_profitable
    efficiency = winning_signals / max(total_opportunities, 1) * 100
    
    lines.append(f"  Winning signals: {winning_signals}")
    lines.append(f"  Missed profitable opportunities: {missed_profitable}")
    lines.append(f"  Total opportunities: {total_opportunities}")
    lines.append(f"  Efficiency: {efficiency:.1f}%")
    lines.append("")
    
    if efficiency > 50:
        lines.append("  ✅ Good balance — capturing majority of profitable opportunities")
    elif efficiency > 30:
        lines.append("  ⚖️  Moderate — missing some profitable opportunities")
    else:
        lines.append("  ⚠️  Low efficiency — missing most profitable opportunities")
    lines.append("")
    
    # 11. Post-Confidence Pipeline
    lines.append("🔍 POST-CONFIDENCE PIPELINE")
    lines.append("-" * 80)
    
    # Count candidates at each stage
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND confidence_pass = 1
    """, (cutoff,))
    confidence_passed = cur.fetchone()[0] or 0
    
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND gate_final = 'passed'
    """, (cutoff,))
    signal_engine_passed = cur.fetchone()[0] or 0
    
    cur = conn.execute("""
        SELECT COUNT(*) FROM candidate_lifecycle
        WHERE timestamp > ? AND signal_pass = 1
    """, (cutoff,))
    published = cur.fetchone()[0] or 0
    
    lines.append(f"  Confidence passed:     {confidence_passed:>6}")
    lines.append(f"  Signal engine passed:  {signal_engine_passed:>6}")
    lines.append(f"  Published:             {published:>6}")
    lines.append(f"  Loss after confidence: {confidence_passed - published:>6} ({(confidence_passed - published) / max(confidence_passed, 1) * 100:.0f}%)")
    lines.append("")
    
    # Per-gate breakdown with passed vs rejected
    lines.append("  Signal Engine Gates (Passed vs Rejected):")
    lines.append(f"  {'Gate':<15} {'Passed':>8} {'Rejected':>10} {'Reject%':>8}")
    lines.append("  " + "-" * 50)
    
    for gate in ["duplicate", "cooldown", "entry_atr", "momentum", "rr"]:
        # Count rejected at this gate
        cur = conn.execute(f"""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND gate_{gate} = 1
        """, (cutoff,))
        rejected = cur.fetchone()[0] or 0
        
        # Count passed (reached this gate but not rejected here)
        # A candidate "passed" a gate if it reached it (confidence_pass=1) and wasn't rejected at this gate
        # But we need to account for candidates rejected at earlier gates
        # For simplicity: passed = confidence_passed - rejected at this or earlier gates
        cur = conn.execute(f"""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND confidence_pass = 1 AND (gate_{gate} IS NULL OR gate_{gate} = 0)
        """, (cutoff,))
        passed = cur.fetchone()[0] or 0
        
        reject_pct = rejected / max(confidence_passed, 1) * 100
        
        lines.append(f"  {gate:<15} {passed:>8} {rejected:>10} {reject_pct:>7.1f}%")
    lines.append("")
    
    lines.append("  Execution Gates (Passed vs Rejected):")
    lines.append(f"  {'Gate':<15} {'Passed':>8} {'Rejected':>10} {'Reject%':>8}")
    lines.append("  " + "-" * 50)
    
    for gate in ["session", "cycle_limit", "risk", "position_limit"]:
        cur = conn.execute(f"""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND gate_{gate} = 1
        """, (cutoff,))
        rejected = cur.fetchone()[0] or 0
        
        cur = conn.execute(f"""
            SELECT COUNT(*) FROM candidate_lifecycle
            WHERE timestamp > ? AND confidence_pass = 1 AND (gate_{gate} IS NULL OR gate_{gate} = 0)
        """, (cutoff,))
        passed = cur.fetchone()[0] or 0
        
        reject_pct = rejected / max(confidence_passed, 1) * 100
        
        lines.append(f"  {gate:<15} {passed:>8} {rejected:>10} {reject_pct:>7.1f}%")
    lines.append("")
    
    # Summary interpretation
    lines.append("  Interpretation:")
    if confidence_passed > 0:
        # Find the gate with most rejections
        max_gate = "none"
        max_count = 0
        for gate in ["duplicate", "cooldown", "entry_atr", "momentum", "rr",
                      "session", "cycle_limit", "risk", "position_limit"]:
            cur = conn.execute(f"""
                SELECT COUNT(*) FROM candidate_lifecycle
                WHERE timestamp > ? AND gate_{gate} = 1
            """, (cutoff,))
            count = cur.fetchone()[0] or 0
            if count > max_count:
                max_count = count
                max_gate = gate
        
        if max_count > 0:
            lines.append(f"  → Primary gate: {max_gate} ({max_count} rejections)")
        if published < confidence_passed:
            lines.append(f"  → {confidence_passed - published} candidates lost after confidence (expected operational filtering)")
        else:
            lines.append(f"  → All confidence-passed candidates became signals")
    lines.append("")
    
    # 12. Regime Segmentation
    lines.append("📊 REGIME SEGMENTATION")
    lines.append("-" * 80)
    
    cur = conn.execute("""
        SELECT 
            regime,
            COUNT(*) as candidates,
            SUM(signal_pass) as signals,
            AVG(return_4h_pct) as avg_return,
            SUM(CASE WHEN return_4h_pct >= 1.0 THEN 1 ELSE 0 END) as profitable
        FROM candidate_lifecycle
        WHERE timestamp > ? AND outcome_tracked = 1
        GROUP BY regime
    """, (cutoff,))
    
    lines.append(f"  {'Regime':<15} {'Candidates':>10} {'Signals':>8} {'Avg 4h%':>8} {'Profitable':>10}")
    lines.append("  " + "-" * 60)
    
    for row in cur.fetchall():
        regime = row[0] or "unknown"
        candidates = row[1] or 0
        signals = row[2] or 0
        avg_return = row[3] or 0
        profitable = row[4] or 0
        
        lines.append(f"  {regime:<15} {candidates:>10} {signals:>8} {avg_return:>+7.2f}% {profitable:>10}")
    lines.append("")
    
    lines.append("  Why this matters:")
    lines.append("  A strategy can look excellent overall while actually making all of its")
    lines.append("  profits in one regime and consistently underperforming in another.")
    lines.append("")
    
    # 12. Filter Contribution
    lines.append("🔧 FILTER CONTRIBUTION")
    lines.append("-" * 80)
    
    for stage in ["pullback", "candle", "volume", "confidence"]:
        cur = conn.execute("""
            SELECT 
                COUNT(*) as rejected,
                SUM(CASE WHEN return_4h_pct >= 1.0 THEN 1 ELSE 0 END) as would_profit,
                SUM(CASE WHEN return_4h_pct < 0 THEN 1 ELSE 0 END) as would_lose
            FROM candidate_lifecycle
            WHERE timestamp > ? AND rejection_stage = ? AND outcome_tracked = 1
        """, (cutoff, stage))
        
        row = cur.fetchone()
        rejected = row[0] or 0
        would_profit = row[1] or 0
        would_lose = row[2] or 0
        
        if rejected > 0:
            accuracy = would_lose / rejected * 100
            if accuracy > 70:
                status = "✅"
            elif accuracy < 50:
                status = "⚠️"
            else:
                status = "⚖️"
        else:
            accuracy = 0
            status = "—"
        
        lines.append(f"  {status} {stage:<12} Rejected: {rejected:>5} | Would profit: {would_profit:>5} | Would lose: {would_lose:>5} | Accuracy: {accuracy:.0f}%")
    lines.append("")
    
    lines.append("  Why this matters:")
    lines.append("  Each filter should be rejecting mostly losers. If a filter is rejecting")
    lines.append("  mostly winners, it may be reducing trade frequency without adding value.")
    lines.append("")
    
    # 13. Primary Objective
    lines.append("🎯 PRIMARY OBJECTIVE")
    lines.append("-" * 80)
    lines.append("  Trading outcomes are the primary success metric:")
    lines.append("  - Positive expectancy")
    lines.append("  - Acceptable drawdown")
    lines.append("  - Stable profit factor")
    lines.append("  - Consistent performance across market conditions")
    lines.append("")
    lines.append("  Diagnostic metrics (precision, recall, latency) explain WHY")
    lines.append("  the strategy performs the way it does, not replace trading")
    lines.append("  performance as the primary objective.")
    lines.append("")
    
    lines.append("2. NEXT STEPS")
    lines.append("   - Continue collecting data until all gates pass")
    lines.append("   - Re-run this analysis daily")
    lines.append("   - Only adjust thresholds after gates pass")
    lines.append("   - Change ONE parameter at a time to measure impact")
    lines.append("   - Focus on trading outcomes, not diagnostic metrics alone")
    lines.append("")
    
    conn.close()
    return "\n".join(lines)


def main():
    """Main entry point."""
    hours = 24
    
    # Parse arguments
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--hours" and i < len(sys.argv) - 1:
            hours = int(sys.argv[i + 1])
    
    report = analyze_lifecycle(hours)
    print(report)
    
    # Save report
    report_file = Path(f"data/logs/lifecycle_analysis_{hours}h.txt")
    report_file.parent.mkdir(parents=True, exist_ok=True)
    with open(report_file, "w") as f:
        f.write(report)
    print(f"\n📄 Report saved to: {report_file}")


if __name__ == "__main__":
    main()
