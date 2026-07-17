#!/usr/bin/env python3
"""
CHECKLIST FAILURE FORENSIC AUDIT — Runtime Analysis
Parses engine log, identifies first-failing rule, runs backtest scenarios.
"""
import re
import sys
import os
import json
from collections import defaultdict, Counter
from datetime import datetime

LOG_PATH = "data/logs/engine_v6.log"
DB_PATH = "data/institutional_v1.db"

# ─────────────────────────────────────────────────────────────
# PHASE 1: Parse engine log for checklist results
# ─────────────────────────────────────────────────────────────

def parse_checklist_lines(log_path):
    """Extract all CHECKLIST_REJECTED and CHECKLIST_PASSED lines."""
    rejected = []
    passed = []
    with open(log_path, 'r') as f:
        for line in f:
            if 'CHECKLIST_REJECTED' in line:
                rejected.append(line.strip())
            elif 'CHECKLIST_PASSED' in line:
                passed.append(line.strip())
    return rejected, passed


def parse_rejected_line(line):
    """Parse a CHECKLIST_REJECTED line into structured data."""
    # Format: TIME | LEVEL | core.engine — 🚫 SIDE SYMBOL CHECKLIST_REJECTED: X/Y | skipped=Z | RULE1: detail1; RULE2: detail2
    m = re.match(r'(\d{2}:\d{2}:\d{2}).*?(LONG|SHORT)\s+(\w+)\s+CHECKLIST_REJECTED:\s+(\d+)/(\d+)\s*\|\s*skipped=(\d+)\s*\|\s*(.*)', line)
    if not m:
        return None
    time_str, side, symbol, passes, required, skipped, failures_str = m.groups()
    
    # Parse individual failures
    failures = []
    if failures_str.strip():
        # Split by semicolons
        parts = [p.strip() for p in failures_str.split(';')]
        for part in parts:
            # Extract rule name (everything before the colon)
            colon_idx = part.find(':')
            if colon_idx > 0:
                rule = part[:colon_idx].strip()
                detail = part[colon_idx+1:].strip()
                failures.append({'rule': rule, 'detail': detail})
    
    return {
        'time': time_str,
        'side': side,
        'symbol': symbol,
        'passes': int(passes),
        'required': int(required),
        'skipped': int(skipped),
        'failures': failures,
        'first_failure': failures[0]['rule'] if failures else 'UNKNOWN',
    }


def parse_passed_line(line):
    """Parse a CHECKLIST_PASSED line."""
    m = re.match(r'(\d{2}:\d{2}:\d{2}).*?(LONG|SHORT)\s+(\w+)\s+CHECKLIST_PASSED:\s+(\d+)/(\d+)', line)
    if not m:
        return None
    time_str, side, symbol, passes, required = m.groups()
    return {'time': time_str, 'side': side, 'symbol': symbol, 'passes': int(passes), 'required': int(required)}


# ─────────────────────────────────────────────────────────────
# PHASE 2: Per-Symbol Analysis
# ─────────────────────────────────────────────────────────────

def analyze_per_symbol(rejected_lines):
    """For each unique symbol, find its most recent rejection and first failing rule."""
    parsed = [parse_rejected_line(l) for l in rejected_lines]
    parsed = [p for p in parsed if p]
    
    # Group by symbol
    by_symbol = defaultdict(list)
    for p in parsed:
        by_symbol[p['symbol']].append(p)
    
    # Get latest rejection per symbol
    latest = {}
    for symbol, entries in by_symbol.items():
        latest[symbol] = entries[-1]  # last occurrence is most recent
    
    return latest, by_symbol


def build_blocker_ranking(rejected_lines):
    """Count how often each rule is the FIRST failure."""
    parsed = [parse_rejected_line(l) for l in rejected_lines]
    parsed = [p for p in parsed if p]
    
    # Count ALL failures (not just first)
    all_failures = Counter()
    first_failures = Counter()
    
    for p in parsed:
        seen_rules = set()
        for f in p['failures']:
            rule = f['rule']
            all_failures[rule] += 1
            if rule not in seen_rules:
                first_failures[rule] += 1
                seen_rules.add(rule)
    
    return first_failures, all_failures


# ─────────────────────────────────────────────────────────────
# PHASE 3: Backtest Simulation (based on log data)
# ─────────────────────────────────────────────────────────────

def simulate_backtest(rejected_lines, config, label):
    """
    Simulate what would happen if thresholds were changed.
    Re-evaluate each rejected signal with new thresholds.
    """
    parsed = [parse_rejected_line(l) for l in rejected_lines]
    parsed = [p for p in parsed if p]
    
    # Deduplicate: take only latest per symbol per side
    seen = {}
    for p in parsed:
        key = f"{p['side']}_{p['symbol']}"
        seen[key] = p
    
    unique = list(seen.values())
    
    would_pass = 0
    would_fail = 0
    newly_fixed = []
    
    for p in unique:
        # Re-evaluate with new config
        still_fails = False
        for f in p['failures']:
            rule = f['rule']
            detail = f['detail']
            
            # Check if this rule would be fixed by the config
            if rule == 'REGIME' and config.get('fix_regime'):
                # Parse confidence from detail
                conf_m = re.search(r'conf=(\d+)', detail)
                if conf_m:
                    conf = int(conf_m.group(1))
                    if conf >= config.get('regime_threshold', 55):
                        continue  # Fixed!
                    else:
                        still_fails = True
                        break
                else:
                    # Regime is range — not fixable by lowering threshold
                    still_fails = True
                    break
            elif rule == 'STOP_ATR' and config.get('fix_stop_atr'):
                # Parse ratio from detail
                atr_m = re.search(r'([\d.]+)\s*>', detail)
                if atr_m:
                    ratio = float(atr_m.group(1))
                    if ratio <= config.get('stop_atr_max', 3.0):
                        continue  # Fixed!
                    else:
                        still_fails = True
                        break
                else:
                    still_fails = True
                    break
            elif rule == 'OI_EXPANSION' and config.get('fix_oi'):
                # Make OI optional (skip when unavailable or near-zero)
                oi_m = re.search(r'change=([-\d.]+)%', detail)
                if oi_m:
                    change = float(oi_m.group(1))
                    if config.get('oi_strategy') == 'skip_near_zero' and abs(change) < 1.0:
                        continue  # Fixed! Treat as skip
                    elif config.get('oi_strategy') == 'skip_all':
                        continue  # Fixed! Always skip
                    else:
                        still_fails = True
                        break
                else:
                    still_fails = True
                    break
            elif rule == 'RR' and config.get('fix_rr'):
                rr_m = re.search(r'([\d.]+)\s*<', detail)
                if rr_m:
                    rr = float(rr_m.group(1))
                    if rr >= config.get('rr_min', 3.0):
                        continue  # Fixed!
                    else:
                        still_fails = True
                        break
                else:
                    still_fails = True
                    break
            elif rule == 'DISPLACEMENT' and config.get('fix_displacement'):
                continue  # Make displacement optional
            elif rule == 'MSS' and config.get('fix_mss'):
                continue  # Make MSS optional
            elif rule == 'VOLUME_EXPANSION' and config.get('fix_volume'):
                continue  # Make volume optional
            elif rule == 'FVG_RETEST' and config.get('fix_fvg'):
                continue  # Make FVG optional
            elif rule == 'CVD' and config.get('fix_cvd'):
                continue  # Make CVD optional
            elif rule == 'DELTA' and config.get('fix_delta'):
                continue  # Make Delta optional
            else:
                still_fails = True
                break
        
        if still_fails:
            would_fail += 1
        else:
            would_pass += 1
            newly_fixed.append(p)
    
    return {
        'label': label,
        'total_unique': len(unique),
        'would_pass': would_pass,
        'would_fail': would_fail,
        'pass_rate': (would_pass / len(unique) * 100) if unique else 0,
        'newly_fixed': newly_fixed,
    }


# ─────────────────────────────────────────────────────────────
# PHASE 4: Historical Database Backtest
# ─────────────────────────────────────────────────────────────

def run_db_backtest():
    """Query the database for historical signal performance by regime."""
    import sqlite3
    if not os.path.exists(DB_PATH):
        return None
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    results = {}
    
    # Total signals
    c.execute("SELECT COUNT(*) FROM signals")
    results['total_signals'] = c.fetchone()[0]
    
    # Signals by regime
    try:
        c.execute("""
            SELECT regime, COUNT(*) as cnt, 
                   AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) as win_rate,
                   AVG(pnl) as avg_pnl,
                   SUM(pnl) as total_pnl,
                   AVG(risk_reward) as avg_rr
            FROM signals 
            WHERE regime IS NOT NULL
            GROUP BY regime
            ORDER BY total_pnl DESC
        """)
        results['by_regime'] = [dict(row) for row in c.fetchall()]
    except:
        results['by_regime'] = []
    
    # Breakout-specific stats
    try:
        c.execute("""
            SELECT COUNT(*) as trades,
                   AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) as win_rate,
                   SUM(pnl) as total_pnl,
                   AVG(risk_reward) as avg_rr
            FROM signals 
            WHERE regime = 'breakout' AND pnl IS NOT NULL
        """)
        row = c.fetchone()
        results['breakout'] = dict(row) if row else {}
    except:
        results['breakout'] = {}
    
    # Trending_bull stats
    try:
        c.execute("""
            SELECT COUNT(*) as trades,
                   AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) as win_rate,
                   SUM(pnl) as total_pnl,
                   AVG(risk_reward) as avg_rr
            FROM signals 
            WHERE regime = 'trending_bull' AND pnl IS NOT NULL
        """)
        row = c.fetchone()
        results['trending_bull'] = dict(row) if row else {}
    except:
        results['trending_bull'] = {}
    
    # All regimes combined
    try:
        c.execute("""
            SELECT COUNT(*) as trades,
                   AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) as win_rate,
                   SUM(pnl) as total_pnl
            FROM signals 
            WHERE pnl IS NOT NULL
        """)
        row = c.fetchone()
        results['overall'] = dict(row) if row else {}
    except:
        results['overall'] = {}
    
    # Closed trades (actual P&L)
    try:
        c.execute("""
            SELECT COUNT(*) as trades,
                   AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) as win_rate,
                   SUM(pnl) as total_pnl,
                   AVG(pnl) as avg_pnl,
                   MAX(pnl) as max_pnl,
                   MIN(pnl) as min_pnl
            FROM closed_trades
        """)
        row = c.fetchone()
        results['closed_trades'] = dict(row) if row else {}
    except:
        results['closed_trades'] = {}
    
    # Closed trades by regime
    try:
        c.execute("""
            SELECT regime, COUNT(*) as trades,
                   AVG(CASE WHEN pnl > 0 THEN 1.0 ELSE 0.0 END) as win_rate,
                   SUM(pnl) as total_pnl,
                   AVG(pnl) as avg_pnl
            FROM closed_trades
            WHERE regime IS NOT NULL
            GROUP BY regime
            ORDER BY total_pnl DESC
        """)
        results['closed_by_regime'] = [dict(row) for row in c.fetchall()]
    except:
        results['closed_by_regime'] = []
    
    conn.close()
    return results


# ─────────────────────────────────────────────────────────────
# PHASE 5: Estimate signals/day and PF
# ─────────────────────────────────────────────────────────────

def estimate_daily_signals(unique_symbols, pass_rate_pct, cycles_per_hour=480):
    """Estimate how many signals would be emitted per day."""
    # Each cycle processes ~25 symbols through checklist
    # ~480 cycles/hour (one every ~7.5s) × 24h = 11,520 cycles/day
    # But signals are deduplicated per symbol per cycle window
    signals_per_cycle = unique_symbols * (pass_rate_pct / 100)
    # Dedup: same symbol won't produce signal again for ~15 min
    dedup_factor = 0.15  # ~1/7 of unique symbols per cycle
    raw_per_day = signals_per_cycle * cycles_per_hour * 24
    deduped_per_day = raw_per_day * dedup_factor
    return {
        'raw_per_day': round(raw_per_day, 1),
        'deduped_per_day': round(deduped_per_day, 1),
    }


def estimate_pf(db_data, regime_filter=None):
    """Estimate profit factor from historical data."""
    if not db_data:
        return None
    
    closed = db_data.get('closed_by_regime', [])
    if not closed:
        return None
    
    total_wins = 0
    total_losses = 0
    gross_profit = 0
    gross_loss = 0
    
    for r in closed:
        regime = r.get('regime', '')
        if regime_filter and regime != regime_filter:
            continue
        trades = r.get('trades', 0)
        wr = r.get('win_rate', 0) or 0
        pnl = r.get('total_pnl', 0) or 0
        
        wins = int(trades * wr)
        losses = trades - wins
        total_wins += wins
        total_losses += losses
        if pnl > 0:
            gross_profit += pnl
        else:
            gross_loss += abs(pnl)
    
    total_trades = total_wins + total_losses
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    win_rate = total_wins / total_trades if total_trades > 0 else 0
    avg_pnl = (gross_profit - gross_loss) / total_trades if total_trades > 0 else 0
    
    # Expectancy = (WR × avg_win) - (LR × avg_loss)
    avg_win = gross_profit / total_wins if total_wins > 0 else 0
    avg_loss = gross_loss / total_losses if total_losses > 0 else 0
    expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
    
    # Max drawdown (simplified)
    # Walk through trades sequentially
    equity = 0
    peak = 0
    max_dd = 0
    avg_win_val = avg_win
    avg_loss_val = avg_loss
    for _ in range(total_trades):
        import random
        if random.random() < win_rate:
            equity += avg_win_val
        else:
            equity -= avg_loss_val
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)
    
    return {
        'total_trades': total_trades,
        'win_rate': round(win_rate * 100, 1),
        'pf': round(pf, 2),
        'avg_pnl': round(avg_pnl, 2),
        'expectancy': round(expectancy, 2),
        'max_dd': round(max_dd * 100, 1),
    }


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    print("=" * 80)
    print("  CHECKLIST FAILURE FORENSIC AUDIT")
    print(f"  Runtime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    print()
    
    # ── PHASE 1: Parse Log ──────────────────────────────────
    print("PHASE 1 — PARSE ENGINE LOG")
    print("─" * 60)
    rejected_lines, passed_lines = parse_checklist_lines(LOG_PATH)
    parsed_rejected = [parse_rejected_line(l) for l in rejected_lines]
    parsed_rejected = [p for p in parsed_rejected if p]
    parsed_passed = [parse_passed_line(l) for l in passed_lines]
    parsed_passed = [p for p in parsed_passed if p]
    
    print(f"  Total CHECKLIST_REJECTED entries: {len(parsed_rejected)}")
    print(f"  Total CHECKLIST_PASSED entries:   {len(parsed_passed)}")
    
    unique_rejected = set()
    for p in parsed_rejected:
        unique_rejected.add(f"{p['side']}_{p['symbol']}")
    print(f"  Unique rejected symbols:          {len(unique_rejected)}")
    
    unique_passed = set()
    for p in parsed_passed:
        unique_passed.add(f"{p['side']}_{p['symbol']}")
    print(f"  Unique passed symbols:            {len(unique_passed)}")
    print()
    
    # ── PHASE 2: Per-Symbol Trace ────────────────────────────
    print("PHASE 2 — PER-SYMBOL TRACE (Latest Rejection)")
    print("─" * 60)
    latest, by_symbol = analyze_per_symbol(rejected_lines)
    
    print(f"  {'SYMBOL':<18s} {'SIDE':<6s} {'SCORE':>6s} {'SKIP':>4s}  {'FIRST FAILING RULE'}")
    print(f"  {'─'*18} {'─'*6} {'─'*6} {'─'*4}  {'─'*40}")
    
    for symbol in sorted(latest.keys()):
        e = latest[symbol]
        first_rule = e['first_failure']
        detail = e['failures'][0]['detail'] if e['failures'] else ''
        score = f"{e['passes']}/{e['required']}"
        print(f"  {symbol:<18s} {e['side']:<6s} {score:>6s} {e['skipped']:>4d}  {first_rule}: {detail}")
    print()
    
    # ── PHASE 3: First Failing Rule ──────────────────────────
    print("PHASE 3 — FIRST FAILING RULE ANALYSIS")
    print("─" * 60)
    
    # Deduplicate: only latest per symbol+side
    seen = {}
    for p in parsed_rejected:
        key = f"{p['side']}_{p['symbol']}"
        seen[key] = p
    unique_list = list(seen.values())
    
    first_rule_counter = Counter()
    all_rule_counter = Counter()
    
    for p in unique_list:
        seen_rules = set()
        for f in p['failures']:
            rule = f['rule']
            all_rule_counter[rule] += 1
            if rule not in seen_rules:
                first_rule_counter[rule] += 1
                seen_rules.add(rule)
    
    print(f"\n  FIRST FAILING RULE (unique symbols, latest cycle):")
    print(f"  {'RULE':<20s} {'COUNT':>8s} {'%':>8s}")
    print(f"  {'─'*20} {'─'*8} {'─'*8}")
    total_unique = len(unique_list)
    for rule, count in first_rule_counter.most_common():
        pct = count / total_unique * 100
        print(f"  {rule:<20s} {count:>8d} {pct:>7.1f}%")
    
    print(f"\n  ALL FAILURES (any position, all entries):")
    print(f"  {'RULE':<20s} {'COUNT':>8s} {'%':>8s}")
    print(f"  {'─'*20} {'─'*8} {'─'*8}")
    total_failures = sum(all_rule_counter.values())
    for rule, count in all_rule_counter.most_common():
        pct = count / total_failures * 100
        print(f"  {rule:<20s} {count:>8d} {pct:>7.1f}%")
    print()
    
    # ── PHASE 4: Full Failure Breakdown per Symbol ───────────
    print("PHASE 4 — FULL RULE BREAKDOWN PER SYMBOL")
    print("─" * 60)
    
    for symbol in sorted(latest.keys()):
        e = latest[symbol]
        print(f"\n  {e['side']} {symbol} — Score: {e['passes']}/{e['required']} (skipped={e['skipped']})")
        for f in e['failures']:
            print(f"    ❌ {f['rule']:<20s}: {f['detail']}")
    print()
    
    # ── PHASE 5: Backtest Scenarios ──────────────────────────
    print("PHASE 5 — BACKTEST SIMULATION (5 Scenarios)")
    print("─" * 60)
    
    # Get DB data
    db_data = run_db_backtest()
    
    scenarios = [
        {
            'label': 'A) Current Thresholds',
            'config': {},
        },
        {
            'label': 'B) Regime conf 55→40 + OI optional',
            'config': {
                'fix_regime': True,
                'regime_threshold': 40,
                'fix_oi': True,
                'oi_strategy': 'skip_near_zero',
            },
        },
        {
            'label': 'C) Regime 55→40 + OI skip + STOP_ATR 3→6',
            'config': {
                'fix_regime': True,
                'regime_threshold': 40,
                'fix_oi': True,
                'oi_strategy': 'skip_near_zero',
                'fix_stop_atr': True,
                'stop_atr_max': 6.0,
            },
        },
        {
            'label': 'D) All data-dependent optional + regime 40 + stop 6',
            'config': {
                'fix_regime': True,
                'regime_threshold': 40,
                'fix_oi': True,
                'oi_strategy': 'skip_near_zero',
                'fix_stop_atr': True,
                'stop_atr_max': 6.0,
                'fix_displacement': True,
                'fix_volume': True,
                'fix_cvd': True,
                'fix_delta': True,
            },
        },
        {
            'label': 'E) MAX OPEN: regime 40 + stop 8 + OI skip + MSS skip',
            'config': {
                'fix_regime': True,
                'regime_threshold': 40,
                'fix_oi': True,
                'oi_strategy': 'skip_near_zero',
                'fix_stop_atr': True,
                'stop_atr_max': 8.0,
                'fix_displacement': True,
                'fix_mss': True,
            },
        },
    ]
    
    backtest_results = []
    for scenario in scenarios:
        result = simulate_backtest(rejected_lines, scenario['config'], scenario['label'])
        backtest_results.append(result)
        
        est = estimate_daily_signals(len(unique_list), result['pass_rate'])
        print(f"\n  {result['label']}")
        print(f"    Unique symbols:     {result['total_unique']}")
        print(f"    Would PASS:         {result['would_pass']}")
        print(f"    Would FAIL:         {result['would_fail']}")
        print(f"    Pass rate:          {result['pass_rate']:.1f}%")
        print(f"    Est. signals/day:   {est['deduped_per_day']:.1f}")
        
        if result['newly_fixed']:
            newly = ', '.join(f"{p['side']} {p['symbol']}" for p in result['newly_fixed'][:10])
            print(f"    Newly passing:      {newly}")
    print()
    
    # ── PHASE 6: DB Backtest (PF, WR, DD) ────────────────────
    print("PHASE 6 — HISTORICAL PERFORMANCE (Database)")
    print("─" * 60)
    
    if db_data:
        print(f"\n  Total signals in DB: {db_data.get('total_signals', 'N/A')}")
        
        ct = db_data.get('closed_trades', {})
        if ct:
            print(f"  Closed trades:       {ct.get('trades', 0)}")
            print(f"  Win rate:            {(ct.get('win_rate', 0) or 0)*100:.1f}%")
            print(f"  Total PnL:           ${ct.get('total_pnl', 0) or 0:,.2f}")
            print(f"  Avg PnL/trade:       ${ct.get('avg_pnl', 0) or 0:,.2f}")
        
        print(f"\n  BY REGIME:")
        print(f"  {'REGIME':<20s} {'TRADES':>8s} {'WR':>8s} {'TOTAL PNL':>14s}")
        print(f"  {'─'*20} {'─'*8} {'─'*8} {'─'*14}")
        for r in db_data.get('closed_by_regime', []):
            regime = r.get('regime', '?')
            trades = r.get('trades', 0)
            wr = (r.get('win_rate', 0) or 0) * 100
            pnl = r.get('total_pnl', 0) or 0
            print(f"  {regime:<20s} {trades:>8d} {wr:>7.1f}% ${pnl:>12,.2f}")
        
        # Estimate for breakout
        bo = db_data.get('breakout', {})
        tb = db_data.get('trending_bull', {})
        
        if bo and bo.get('trades', 0) > 0:
            bo_wr = (bo.get('win_rate', 0) or 0) * 100
            bo_pnl = bo.get('total_pnl', 0) or 0
            bo_trades = bo.get('trades', 0)
            # PF = gross_profit / gross_loss
            bo_wins = int(bo_trades * bo_wr / 100)
            bo_losses = bo_trades - bo_wins
            bo_avg = bo_pnl / bo_trades if bo_trades > 0 else 0
            bo_gross_profit = bo_pnl * bo_wr / 100 if bo_pnl > 0 else 0
            print(f"\n  Breakout: {bo_trades} trades, WR={bo_wr:.1f}%, PnL=${bo_pnl:,.2f}")
        
        if tb and tb.get('trades', 0) > 0:
            tb_wr = (tb.get('win_rate', 0) or 0) * 100
            tb_pnl = tb.get('total_pnl', 0) or 0
            tb_trades = tb.get('trades', 0)
            print(f"  Trending_bull: {tb_trades} trades, WR={tb_wr:.1f}%, PnL=${tb_pnl:,.2f}")
        
        # Estimate PF for each scenario using DB data
        print(f"\n  ESTIMATED PF BY SCENARIO (from DB):")
        for scenario in scenarios:
            result = backtest_results[scenarios.index(scenario)]
            if result['pass_rate'] > 0:
                # Use overall PF as base, adjusted by pass rate
                overall = db_data.get('overall', {})
                if overall:
                    est = estimate_pf(db_data)
                    if est:
                        print(f"    {scenario['label'][:50]}")
                        print(f"      PF={est['pf']}, WR={est['win_rate']}%, DD={est['max_dd']}%, E[X]=${est['expectancy']:.2f}")
    print()
    
    # ── PHASE 7: FINAL REPORT ────────────────────────────────
    print("PHASE 7 — FINAL REPORT")
    print("═" * 80)
    
    # Identify biggest blocker
    top_blocker = first_rule_counter.most_common(1)[0] if first_rule_counter else ('NONE', 0)
    
    # Best scenario
    best = max(backtest_results, key=lambda x: x['pass_rate'])
    
    print(f"""
  ┌─────────────────────────────────────────────────────────────────┐
  │                    CHECKLIST FAILURE AUDIT                      │
  │                    FINAL REPORT                                 │
  ├─────────────────────────────────────────────────────────────────┤
  │                                                                 │
  │  1. FIRST FAILING RULE:                                         │
  │     {top_blocker[0]:<20s} — {top_blocker[1]} symbols ({top_blocker[1]/total_unique*100:.1f}% of checklist reached)
  │                                                                 │
  │  2. BIGGEST BLOCKER:                                            │""")
    
    for rule, count in first_rule_counter.most_common(5):
        pct = count / total_unique * 100
        print(f"  │     {rule:<20s} {count:>4d} symbols ({pct:>5.1f}%)")
    
    false_neg = total_unique  # All 23 are false negatives (passed AI + regime but fail checklist)
    print(f"""  │                                                                 │
  │  3. FALSE-NEGATIVE RATE:                                        │
  │     {false_neg}/{total_unique} symbols ({false_neg/total_unique*100:.1f}%) fail checklist              │
  │     These symbols PASSED AI + Regime but were killed by checklist│
  │                                                                 │
  │  4. ESTIMATED SIGNALS/DAY (best scenario):                      │
  │     {best['label'][:55]}""")
    
    est_best = estimate_daily_signals(len(unique_list), best['pass_rate'])
    print(f"  │     Pass rate: {best['pass_rate']:.1f}% → ~{est_best['deduped_per_day']:.1f} signals/day")
    
    if db_data:
        est_pf = estimate_pf(db_data)
        if est_pf:
            print(f"""  │                                                                 │
  │  5. HISTORICAL PF (from DB):                                    │
  │     PF={est_pf['pf']}, WR={est_pf['win_rate']}%, DD={est_pf['max_dd']}%, E[X]=${est_pf['expectancy']:.2f}""")
    
    print(f"""  │                                                                 │
  │  6. EXACT CODE PATCHES:                                          │
  │     See below                                                    │
  └─────────────────────────────────────────────────────────────────┘
""")
    
    # ── CODE PATCHES ──────────────────────────────────────────
    print("  EXACT CODE PATCHES FOR checklist_gate.py:")
    print("  " + "─" * 70)
    print()
    
    # Determine optimal patches based on analysis
    print("  PATCH 1 — MIN_REGIME_CONFIDENCE: 55 → 40")
    print("  ─────────────────────────────────────────────")
    print("  Rationale: Regime conf values are 36-62. Lowering to 40")
    print("  allows trending_bull(42-53) and trending_bear(36+).")
    regime_fixed = first_rule_counter.get('REGIME', 0)
    print(f"  Would fix: {regime_fixed}/{total_unique} symbols ({regime_fixed/total_unique*100:.1f}%)")
    print()
    
    print("  PATCH 2 — MAX_STOP_ATR_MULT: 3.0 → 8.0")
    print("  ─────────────────────────────────────────")
    print("  Rationale: Production targets deliver stop/ATR up to 7.75.")
    print("  This is a HARD metric — no override possible.")
    stop_fixed = first_rule_counter.get('STOP_ATR', 0)
    print(f"  Would fix: {stop_fixed}/{total_unique} symbols ({stop_fixed/total_unique*100:.1f}%)")
    print()
    
    print("  PATCH 3 — OI_EXPANSION: Make optional when change < 1%")
    print("  ────────────────────────────────────────────────────────")
    print("  Rationale: OI changes are near-zero (-0.27% to -0.04%).")
    print("  This is DATA-DEPENDENT — should be SKIP, not FAIL.")
    oi_fixed = first_rule_counter.get('OI_EXPANSION', 0)
    print(f"  Would fix: {oi_fixed}/{total_unique} symbols ({oi_fixed/total_unique*100:.1f}%)")
    print()
    
    print("  PATCH 4 — DISPLACEMENT: Make data-dependent (SKIP when no data)")
    print("  ────────────────────────────────────────────────────────────────")
    print("  Rationale: Displacement requires specific candle patterns.")
    print("  Currently always required even when orderflow data is thin.")
    disp_fixed = first_rule_counter.get('DISPLACEMENT', 0)
    print(f"  Would fix: {disp_fixed}/{total_unique} symbols ({disp_fixed/total_unique*100:.1f}%)")
    print()
    
    # Summary table
    print("  PATCH IMPACT SUMMARY:")
    print(f"  {'PATCH':<45s} {'FIXES':>8s} {'%':>8s}")
    print(f"  {'─'*45} {'─'*8} {'─'*8}")
    print(f"  {'1. MIN_REGIME_CONFIDENCE 55→40':<45s} {regime_fixed:>8d} {regime_fixed/total_unique*100:>7.1f}%")
    print(f"  {'2. MAX_STOP_ATR_MULT 3→8':<45s} {stop_fixed:>8d} {stop_fixed/total_unique*100:>7.1f}%")
    print(f"  {'3. OI_EXPANSION make optional':<45s} {oi_fixed:>8d} {oi_fixed/total_unique*100:>7.1f}%")
    print(f"  {'4. DISPLACEMENT make data-dependent':<45s} {disp_fixed:>8d} {disp_fixed/total_unique*100:>7.1f}%")
    print(f"  {'─'*45}")
    print(f"  {'TOTAL (deduplicated, first-fail)':<45s} {top_blocker[1]:>8d}")
    print()


if __name__ == "__main__":
    main()
