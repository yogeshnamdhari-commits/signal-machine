#!/usr/bin/env python3
"""
Checklist Forensic Audit — Extract all CHECKLIST_REJECTED entries and analyze failure patterns.
Processes the massive log file efficiently using streaming.
"""
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

LOG_FILE = Path("data/logs/engine_2026-06-14.log")

# Pattern: LONG/SHORT SYMBOL CHECKLIST_REJECTED: X/Y | skipped=Z | FAIL1; FAIL2; FAIL3
# Match on the text pattern, not the emoji (encoding issues with multi-byte chars)
REJECTED_RE = re.compile(
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?'
    r'(LONG|SHORT)\s+(\S+)\s+CHECKLIST_REJECTED:\s+(\d+)/(\d+)\s+\|\s+skipped=(\d+)\s+\|\s+(.*)'
)
PASSED_RE = re.compile(
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?'
    r'(LONG|SHORT)\s+(\S+)\s+CHECKLIST_PASSED:\s+(\d+)/(\d+)'
)

# Parse individual failure reasons
FAILURE_RE = re.compile(r'([A-Z_]+):\s*(.*)')


def parse_failure(failure_str):
    """Parse 'REGIME: range conf=0; MSS: no Market Structure Shift' into list of (rule, detail) tuples."""
    parts = [p.strip() for p in failure_str.split(';')]
    results = []
    for part in parts:
        m = FAILURE_RE.match(part)
        if m:
            results.append((m.group(1), m.group(2).strip()))
        else:
            results.append(("UNKNOWN", part))
    return results


def main():
    print("=" * 80)
    print("  CHECKLIST PASS FORENSIC AUDIT")
    print("  Streaming analysis of 700MB+ log file")
    print("=" * 80)
    print()

    rejected_entries = []
    passed_entries = []
    line_count = 0
    rejected_symbol_failures = defaultdict(lambda: {"count": 0, "failures": Counter(), "regimes": Counter(), "sides": Counter()})

    # For per-symbol per-cycle tracking
    cycle_failures = defaultdict(list)  # timestamp -> list of (symbol, side, failures)
    
    # Failure rule frequency
    rule_fail_count = Counter()
    rule_fail_detail = defaultdict(Counter)  # rule -> detail -> count
    
    # Regime type distribution for rejected signals
    rejected_regime_types = Counter()
    
    # Score distribution
    score_distribution = Counter()
    
    # Time-based failure rates
    hourly_rejected = Counter()
    hourly_passed = Counter()

    print("Streaming log file...")
    with open(LOG_FILE, 'r', errors='replace') as f:
        for line in f:
            line_count += 1
            if line_count % 500000 == 0:
                print(f"  ... processed {line_count:,} lines ...")
            
            # Check CHECKLIST_REJECTED
            m = REJECTED_RE.search(line)
            if m:
                ts, side, symbol, passes, required, skipped, failures_str = m.groups()
                passes = int(passes)
                required = int(required)
                skipped = int(skipped)
                
                failures = parse_failure(failures_str)
                entry = {
                    "time": ts,
                    "side": side,
                    "symbol": symbol,
                    "passes": passes,
                    "required": required,
                    "skipped": skipped,
                    "failures": failures,
                    "failure_str": failures_str,
                }
                rejected_entries.append(entry)
                
                hour = ts[11:13]
                hourly_rejected[hour] += 1
                
                # Track per-symbol
                rejected_symbol_failures[symbol]["count"] += 1
                rejected_symbol_failures[symbol]["sides"][side] += 1
                for rule, detail in failures:
                    rejected_symbol_failures[symbol]["failures"][rule] += 1
                
                # Track failure rules
                for rule, detail in failures:
                    rule_fail_count[rule] += 1
                    rule_fail_detail[rule][detail[:60]] += 1
                
                # Track regime from failure text
                for rule, detail in failures:
                    if rule == "REGIME":
                        # Extract regime type from detail like "range conf=0"
                        regime_match = re.match(r'(\w+)', detail)
                        if regime_match:
                            rejected_regime_types[regime_match.group(1)] += 1
                
                # Score distribution
                score_distribution[f"{passes}/{required}"] += 1
                continue
            
            # Check CHECKLIST_PASSED
            m2 = PASSED_RE.search(line)
            if m2:
                ts, side, symbol, passes, required = m2.groups()
                hour = ts[11:13]
                hourly_passed[hour] += 1
                passed_entries.append({
                    "time": ts, "side": side, "symbol": symbol,
                    "passes": int(passes), "required": int(required),
                })

    print(f"\nProcessed {line_count:,} lines total")
    print(f"Found {len(rejected_entries):,} CHECKLIST_REJECTED entries")
    print(f"Found {len(passed_entries):,} CHECKLIST_PASSED entries")
    
    # ═══════════════════════════════════════════════════════
    # SECTION 1: Per-Symbol Checklist Trace
    # ═══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  SECTION 1: TOP 20 SYMBOLS BY REJECTION COUNT")
    print("=" * 80)
    print(f"\n{'SYMBOL':<25s} {'REJECTS':>8s} {'TOP FAILURE':<25s} {'SIDE MIX':<20s}")
    print("-" * 80)
    
    top_symbols = sorted(rejected_symbol_failures.items(), key=lambda x: x[1]["count"], reverse=True)[:20]
    for sym, data in top_symbols:
        top_fail = data["failures"].most_common(1)[0] if data["failures"] else ("N/A", 0)
        side_mix = ", ".join(f"{s}:{c}" for s, c in data["sides"].most_common())
        print(f"{sym:<25s} {data['count']:>8d} {top_fail[0]:<25s} {side_mix:<20s}")

    # ═══════════════════════════════════════════════════════
    # SECTION 2: Failure Rule Ranking
    # ═══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  SECTION 2: CHECKLIST FAILURE RULE RANKING")
    print("=" * 80)
    print(f"\n{'RANK':<6s} {'RULE':<25s} {'FAIL COUNT':>10s} {'FAIL %':>8s}")
    print("-" * 55)
    
    total_rejections = len(rejected_entries)
    for rank, (rule, count) in enumerate(rule_fail_count.most_common(), 1):
        pct = (count / total_rejections * 100) if total_rejections > 0 else 0
        print(f"#{rank:<5d} {rule:<25s} {count:>10d} {pct:>7.1f}%")

    # ═══════════════════════════════════════════════════════
    # SECTION 3: Failure Detail Breakdown
    # ═══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  SECTION 3: FAILURE DETAIL BREAKDOWN (Top 5 per rule)")
    print("=" * 80)
    
    for rule, count in rule_fail_count.most_common(10):
        print(f"\n  {rule} ({count} failures):")
        for detail, cnt in rule_fail_detail[rule].most_common(5):
            pct = (cnt / count * 100) if count > 0 else 0
            print(f"    {cnt:>6d} ({pct:>5.1f}%)  {detail}")

    # ═══════════════════════════════════════════════════════
    # SECTION 4: Regime Distribution for Rejected Signals
    # ═══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  SECTION 4: REGIME TYPE DISTRIBUTION (REJECTED SIGNALS)")
    print("=" * 80)
    print(f"\n{'REGIME TYPE':<25s} {'COUNT':>8s} {'%':>8s}")
    print("-" * 45)
    for regime, count in rejected_regime_types.most_common():
        pct = (count / total_rejections * 100) if total_rejections > 0 else 0
        print(f"{regime:<25s} {count:>8d} {pct:>7.1f}%")

    # ═══════════════════════════════════════════════════════
    # SECTION 5: Score Distribution
    # ═══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  SECTION 5: SCORE DISTRIBUTION (passes/required)")
    print("=" * 80)
    print(f"\n{'SCORE':<12s} {'COUNT':>8s} {'%':>8s}")
    print("-" * 32)
    for score, count in sorted(score_distribution.items(), key=lambda x: -x[1])[:20]:
        pct = (count / total_rejections * 100) if total_rejections > 0 else 0
        print(f"{score:<12s} {count:>8d} {pct:>7.1f}%")

    # ═══════════════════════════════════════════════════════
    # SECTION 6: Hourly Throughput
    # ═══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  SECTION 6: HOURLY SIGNAL THROUGHPUT")
    print("=" * 80)
    print(f"\n{'HOUR':<8s} {'REJECTED':>10s} {'PASSED':>10s} {'PASS%':>8s}")
    print("-" * 40)
    all_hours = sorted(set(list(hourly_rejected.keys()) + list(hourly_passed.keys())))
    for hour in all_hours:
        r = hourly_rejected.get(hour, 0)
        p = hourly_passed.get(hour, 0)
        total = r + p
        pass_pct = (p / total * 100) if total > 0 else 0
        print(f"{hour}:00   {r:>10d} {p:>10d} {pass_pct:>7.1f}%")

    # ═══════════════════════════════════════════════════════
    # SECTION 7: FIRST FAILING RULE ANALYSIS
    # ═══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  SECTION 7: FIRST FAILING RULE IN FAILURE LIST")
    print("=" * 80)
    
    first_fail_count = Counter()
    for entry in rejected_entries:
        if entry["failures"]:
            first_fail_count[entry["failures"][0][0]] += 1
    
    print(f"\n{'RANK':<6s} {'FIRST FAIL RULE':<25s} {'COUNT':>8s} {'%':>8s}")
    print("-" * 50)
    for rank, (rule, count) in enumerate(first_fail_count.most_common(), 1):
        pct = (count / total_rejections * 100) if total_rejections > 0 else 0
        print(f"#{rank:<5d} {rule:<25s} {count:>8d} {pct:>7.1f}%")

    # ═══════════════════════════════════════════════════════
    # SECTION 8: COMBINATION FAILURE PATTERNS
    # ═══════════════════════════════════════════════════════
    print("\n" + "=" * 80)
    print("  SECTION 8: TOP 15 FAILURE COMBINATIONS")
    print("=" * 80)
    
    combo_counter = Counter()
    for entry in rejected_entries:
        rules = tuple(sorted(rule for rule, _ in entry["failures"]))
        combo_counter[rules] += 1
    
    print(f"\n{'#':<4s} {'COMBINATION':<55s} {'COUNT':>8s} {'%':>8s}")
    print("-" * 70)
    for rank, (combo, count) in enumerate(combo_counter.most_common(15), 1):
        pct = (count / total_rejections * 100) if total_rejections > 0 else 0
        combo_str = " + ".join(combo)
        if len(combo_str) > 54:
            combo_str = combo_str[:51] + "..."
        print(f"{rank:<4d} {combo_str:<55s} {count:>8d} {pct:>7.1f}%")

    print("\n" + "=" * 80)
    print("  AUDIT COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
