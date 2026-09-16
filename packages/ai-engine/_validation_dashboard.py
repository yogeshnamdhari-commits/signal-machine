#!/usr/bin/env python3
"""
EMA V5 Validation Dashboard — Outcome-based pipeline analysis.

This script provides a comprehensive view of:
1. Current pipeline status
2. Rejection patterns with profitability data
3. Would-be trade analysis
4. Threshold recommendations based on outcomes

Usage:
    python _validation_dashboard.py
    python _validation_dashboard.py --hours 48
"""
import sqlite3
import sys
from pathlib import Path
from datetime import datetime


DB_PATH = Path("data/ema_v5_lifecycle.db")


def generate_dashboard(hours: int = 24) -> str:
    """Generate comprehensive validation dashboard."""
    if not DB_PATH.exists():
        return f"❌ Database not found: {DB_PATH}\n   Run the scanner with lifecycle tracking enabled first."
    
    conn = sqlite3.connect(str(DB_PATH))
    cutoff = datetime.now().timestamp() - (hours * 3600)
    
    lines = []
    lines.append("=" * 80)
    lines.append(f"EMA V5 VALIDATION DASHBOARD (Last {hours} hours)")
    lines.append("=" * 80)
    lines.append("")
    
    # 1. Pipeline Funnel
    lines.append("📊 PIPELINE FUNNEL")
    lines.append("-" * 60)
    
    cur = conn.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(regime_pass) as regime,
            SUM(trend_pass) as trend,
            SUM(pullback_pass) as pullback,
            SUM(candle_pass) as candle,
            SUM(volume_pass) as volume,
            SUM(confidence_pass) as confidence,
            SUM(signal_pass) as signal
        FROM candidate_lifecycle
        WHERE timestamp > ?
    """, (cutoff,))
    
    row = cur.fetchone()
    total = row[0] or 0
    
    stages = [
        ("Total candidates", total, 100),
        ("Regime pass", row[1] or 0, (row[1] or 0)/max(total,1)*100),
        ("Trend pass", row[2] or 0, (row[2] or 0)/max(total,1)*100),
        ("Pullback pass", row[3] or 0, (row[3] or 0)/max(total,1)*100),
        ("Candle pass", row[4] or 0, (row[4] or 0)/max(total,1)*100),
        ("Volume pass", row[5] or 0, (row[5] or 0)/max(total,1)*100),
        ("Confidence pass", row[6] or 0, (row[6] or 0)/max(total,1)*100),
        ("Signal generated", row[7] or 0, (row[7] or 0)/max(total,1)*100),
    ]
    
    for name, count, pct in stages:
        bar = "█" * int(pct / 2)
        lines.append(f"  {name:<20} {count:>6} ({pct:>5.1f}%) {bar}")
    lines.append("")
    
    # 2. Rejection Analysis with Outcomes
    lines.append("🔍 REJECTION ANALYSIS WITH OUTCOMES")
    lines.append("-" * 80)
    
    cur = conn.execute("""
        SELECT 
            rejection_stage,
            COUNT(*) as count,
            SUM(CASE WHEN outcome = 'profitable' THEN 1 ELSE 0 END) as profitable,
            SUM(CASE WHEN outcome = 'losing' THEN 1 ELSE 0 END) as losing,
            SUM(CASE WHEN outcome = 'breakeven' THEN 1 ELSE 0 END) as breakeven,
            AVG(return_1h_pct) as avg_1h,
            AVG(would_be_mfe) as avg_mfe,
            AVG(would_be_mae) as avg_mae
        FROM candidate_lifecycle
        WHERE timestamp > ? AND outcome_tracked = 1 AND rejection_stage IS NOT NULL
        GROUP BY rejection_stage
        ORDER BY count DESC
    """, (cutoff,))
    
    lines.append(f"  {'Stage':<15} {'Count':>6} {'Profit':>8} {'Losing':>8} {'Win%':>6} {'Avg 1h':>8} {'MFE':>8} {'MAE':>8}")
    lines.append("  " + "-" * 76)
    
    for row in cur.fetchall():
        stage = row[0] or "?"
        count = row[1] or 0
        profitable = row[2] or 0
        losing = row[3] or 0
        avg_1h = row[5] or 0
        avg_mfe = row[6] or 0
        avg_mae = row[7] or 0
        
        tracked = profitable + losing + (row[4] or 0)
        win_rate = profitable / max(tracked, 1) * 100
        
        lines.append(
            f"  {stage:<15} {count:>6} {profitable:>5} ({win_rate:>4.0f}%) "
            f"{losing:>5} ({100-win_rate:>4.0f}%) "
            f"{avg_1h:>+7.2f}% {avg_mfe:>+7.2f}% {avg_mae:>+7.2f}%"
        )
    lines.append("")
    
    # 3. Would-Be Trade Analysis
    lines.append("💰 WOULD-BE TRADE ANALYSIS")
    lines.append("-" * 80)
    lines.append("  If we had entered at each candidate's entry price...")
    lines.append("")
    
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
        stage = row[0] or "?"
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
    
    # 4. Volume Threshold Impact
    lines.append("📊 VOLUME THRESHOLD IMPACT ANALYSIS")
    lines.append("-" * 80)
    
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
            COUNT(*) as count,
            AVG(latency_total) as avg_total,
            MIN(latency_total) as min_total,
            MAX(latency_total) as max_total,
            AVG(latency_regime) as avg_regime,
            AVG(latency_trend) as avg_trend,
            AVG(latency_pullback) as avg_pullback,
            AVG(latency_candle) as avg_candle,
            AVG(latency_volume) as avg_volume,
            AVG(latency_confidence) as avg_confidence,
            AVG(price_slippage_pct) as avg_slippage
        FROM candidate_lifecycle
        WHERE timestamp > ? AND latency_total > 0
    """, (cutoff,))
    
    row = cur.fetchone()
    if row and row[0] > 0:
        count = row[0] or 0
        avg_total = row[1] or 0
        min_total = row[2] or 0
        max_total = row[3] or 0
        
        lines.append(f"  Total candidates with latency data: {count}")
        lines.append(f"  Average pipeline latency: {avg_total:.1f}s")
        lines.append(f"  Min/Max latency: {min_total:.1f}s / {max_total:.1f}s")
        lines.append("")
        
        # Stage breakdown
        lines.append("  Average latency by stage:")
        stages = [
            ("Regime", row[4] or 0),
            ("Trend", row[5] or 0),
            ("Pullback", row[6] or 0),
            ("Candle", row[7] or 0),
            ("Volume", row[8] or 0),
            ("Confidence", row[9] or 0),
        ]
        
        for stage, latency in stages:
            bar = "█" * int(latency / 0.5)
            lines.append(f"    {stage:<12} {latency:>6.1f}s {bar}")
        lines.append("")
        
        # Slippage
        avg_slippage = row[10] or 0
        lines.append(f"  Price slippage: {avg_slippage:.3f}%")
        if avg_slippage > 0.1:
            lines.append(f"  ⚠️  High slippage — signals may be arriving late")
        else:
            lines.append(f"  ✅ Slippage within acceptable range")
    lines.append("")
    
    # 7. Precision and Recall Analysis
    lines.append("🎯 SIGNAL QUALITY: PRECISION & RECALL")
    lines.append("-" * 80)
    
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
    
    report = generate_dashboard(hours)
    print(report)
    
    # Save report
    report_file = Path(f"data/logs/validation_dashboard_{hours}h.txt")
    report_file.parent.mkdir(parents=True, exist_ok=True)
    with open(report_file, "w") as f:
        f.write(report)
    print(f"\n📄 Report saved to: {report_file}")


if __name__ == "__main__":
    main()
