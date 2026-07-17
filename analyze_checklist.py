#!/usr/bin/env python3
"""Fast analysis of extracted checklist lines."""
import re
from collections import Counter, defaultdict

# Parse CHECKLIST_REJECTED: side symbol X/Y skipped=Z | failures
REJ_RE = re.compile(
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?(LONG|SHORT)\s+(\S+)\s+CHECKLIST_REJECTED:\s+(\d+)/(\d+)\s+\|\s+skipped=(\d+)\s+\|\s+(.*)'
)
PASSED_RE = re.compile(
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?(LONG|SHORT)\s+(\S+)\s+CHECKLIST_PASSED:\s+(\d+)/(\d+)'
)
FAIL_RE = re.compile(r'([A-Z_]+):\s*(.*?)(?=;\s*$|$)')

def parse_failures(s):
    return [(m.group(1), m.group(2).strip()) for m in FAIL_RE.finditer(s)]

rejected = []
passed = []

with open('/tmp/checklist_rejected.txt', 'r', errors='replace') as f:
    for line in f:
        m = REJ_RE.search(line)
        if m:
            ts, side, sym, passes, required, skipped, fstr = m.groups()
            rejected.append({
                'time': ts, 'side': side, 'symbol': sym,
                'passes': int(passes), 'required': int(required),
                'skipped': int(skipped), 'failures': parse_failures(fstr),
                'fail_str': fstr.strip()
            })

with open('/tmp/checklist_passed.txt', 'r', errors='replace') as f:
    for line in f:
        m = PASSED_RE.search(line)
        if m:
            ts, side, sym, passes, required = m.groups()
            passed.append({'time': ts, 'side': side, 'symbol': sym,
                          'passes': int(passes), 'required': int(required)})

TOTAL_REJ = len(rejected)
TOTAL_PAS = len(passed)

print("=" * 90)
print("  CHECKLIST PASS FORENSIC AUDIT — FINAL REPORT")
print("=" * 90)
print(f"\n  Total CHECKLIST_REJECTED: {TOTAL_REJ:,}")
print(f"  Total CHECKLIST_PASSED:   {TOTAL_PAS:,}")
print(f"  False-negative rate:      {TOTAL_REJ/(TOTAL_REJ+TOTAL_PAS)*100:.1f}%")

# ═══════════════════════════════════════════════
# SECTION 1: Per-Symbol Trace (Top 20)
# ═══════════════════════════════════════════════
sym_data = defaultdict(lambda: {'count': 0, 'fails': Counter(), 'sides': Counter(), 'regimes': Counter()})
for e in rejected:
    d = sym_data[e['symbol']]
    d['count'] += 1
    d['sides'][e['side']] += 1
    for rule, detail in e['failures']:
        d['fails'][rule] += 1
    for rule, detail in e['failures']:
        if rule == 'REGIME':
            rm = re.match(r'(\w+)', detail)
            if rm: d['regimes'][rm.group(1)] += 1

print("\n" + "=" * 90)
print("  SECTION 1: TOP 20 SYMBOLS BY CHECKLIST REJECTION COUNT")
print("=" * 90)
print(f"\n  {'SYMBOL':<22s} {'N':>5s} {'TOP FAILURE':<22s} {'#2 FAILURE':<22s} {'SIDE':>8s}")
print("  " + "-" * 82)
for sym, d in sorted(sym_data.items(), key=lambda x: -x[1]['count'])[:20]:
    top2 = d['fails'].most_common(2)
    t1 = f"{top2[0][0]}({top2[0][1]})" if len(top2) > 0 else "N/A"
    t2 = f"{top2[1][0]}({top2[1][1]})" if len(top2) > 1 else ""
    side = ", ".join(f"{s}:{c}" for s, c in d['sides'].most_common())
    print(f"  {sym:<22s} {d['count']:>5d} {t1:<22s} {t2:<22s} {side:>8s}")

# ═══════════════════════════════════════════════
# SECTION 2: Failure Rule Ranking
# ═══════════════════════════════════════════════
rule_count = Counter()
rule_detail = defaultdict(Counter)
for e in rejected:
    for rule, detail in e['failures']:
        rule_count[rule] += 1
        rule_detail[rule][detail[:50]] += 1

print("\n" + "=" * 90)
print("  SECTION 2: FAILURE RULE RANKING (how often each rule blocks)")
print("=" * 90)
print(f"\n  {'RANK':<6s} {'RULE':<22s} {'FAILS':>8s} {'% REJ':>8s} {'% ALL':>8s}")
print("  " + "-" * 50)
for rank, (rule, cnt) in enumerate(rule_count.most_common(), 1):
    pct_rej = cnt / TOTAL_REJ * 100
    pct_all = cnt / (TOTAL_REJ + TOTAL_PAS) * 100
    print(f"  #{rank:<5d} {rule:<22s} {cnt:>8d} {pct_rej:>7.1f}% {pct_all:>7.1f}%")

# ═══════════════════════════════════════════════
# SECTION 3: Failure Detail Breakdown
# ═══════════════════════════════════════════════
print("\n" + "=" * 90)
print("  SECTION 3: FAILURE DETAIL BREAKDOWN")
print("=" * 90)
for rule, cnt in rule_count.most_common(8):
    print(f"\n  {rule} ({cnt} failures):")
    for detail, dc in rule_detail[rule].most_common(5):
        pct = dc / cnt * 100
        print(f"    {dc:>6d} ({pct:>5.1f}%)  {detail}")

# ═══════════════════════════════════════════════
# SECTION 4: Regime Distribution
# ═══════════════════════════════════════════════
regime_dist = Counter()
for e in rejected:
    for rule, detail in e['failures']:
        if rule == 'REGIME':
            rm = re.match(r'(\w+)', detail)
            if rm: regime_dist[rm.group(1)] += 1

print("\n" + "=" * 90)
print("  SECTION 4: REGIME TYPE DISTRIBUTION (in rejected signals)")
print("=" * 90)
print(f"\n  {'REGIME':<25s} {'COUNT':>8s} {'%':>8s}")
print("  " + "-" * 45)
for r, c in regime_dist.most_common():
    print(f"  {r:<25s} {c:>8d} {c/TOTAL_REJ*100:>7.1f}%")

# ═══════════════════════════════════════════════
# SECTION 5: First Failing Rule
# ═══════════════════════════════════════════════
first_fail = Counter()
for e in rejected:
    if e['failures']:
        first_fail[e['failures'][0][0]] += 1

print("\n" + "=" * 90)
print("  SECTION 5: FIRST FAILING RULE (entry order in failure string)")
print("=" * 90)
print(f"\n  {'RANK':<6s} {'FIRST FAIL RULE':<25s} {'COUNT':>8s} {'%':>8s}")
print("  " + "-" * 50)
for rank, (rule, cnt) in enumerate(first_fail.most_common(), 1):
    print(f"  #{rank:<5d} {rule:<25s} {cnt:>8d} {cnt/TOTAL_REJ*100:>7.1f}%")

# ═══════════════════════════════════════════════
# SECTION 6: Failure Combination Patterns
# ═══════════════════════════════════════════════
combo = Counter()
for e in rejected:
    rules = tuple(sorted(r for r, _ in e['failures']))
    combo[rules] += 1

print("\n" + "=" * 90)
print("  SECTION 6: TOP 15 FAILURE COMBINATION PATTERNS")
print("=" * 90)
print(f"\n  {'#':<4s} {'COMBINATION':<55s} {'COUNT':>8s} {'%':>8s}")
print("  " + "-" * 70)
for rank, (c, cnt) in enumerate(combo.most_common(15), 1):
    cs = " + ".join(c)
    if len(cs) > 54: cs = cs[:51] + "..."
    print(f"  {rank:<4d} {cs:<55s} {cnt:>8d} {cnt/TOTAL_REJ*100:>7.1f}%")

# ═══════════════════════════════════════════════
# SECTION 7: Score Distribution
# ═══════════════════════════════════════════════
score_dist = Counter()
for e in rejected:
    score_dist[f"{e['passes']}/{e['required']}"] += 1

print("\n" + "=" * 90)
print("  SECTION 7: SCORE DISTRIBUTION (passes/required)")
print("=" * 90)
print(f"\n  {'SCORE':<12s} {'COUNT':>8s} {'%':>8s}")
print("  " + "-" * 32)
for s, c in sorted(score_dist.items(), key=lambda x: -x[1])[:15]:
    print(f"  {s:<12s} {c:>8d} {c/TOTAL_REJ*100:>7.1f}%")

# ═══════════════════════════════════════════════
# SECTION 8: Hourly Throughput
# ═══════════════════════════════════════════════
hour_rej = Counter()
hour_pas = Counter()
for e in rejected:
    hour_rej[e['time'][11:13]] += 1
for e in passed:
    hour_pas[e['time'][11:13]] += 1

print("\n" + "=" * 90)
print("  SECTION 8: HOURLY SIGNAL THROUGHPUT")
print("=" * 90)
print(f"\n  {'HOUR':<8s} {'REJ':>8s} {'PASS':>8s} {'TOTAL':>8s} {'PASS%':>8s}")
print("  " + "-" * 40)
for h in sorted(set(list(hour_rej.keys()) + list(hour_pas.keys()))):
    r = hour_rej.get(h, 0)
    p = hour_pas.get(h, 0)
    t = r + p
    pp = p / t * 100 if t > 0 else 0
    print(f"  {h}:00   {r:>8d} {p:>8d} {t:>8d} {pp:>7.1f}%")

# ═══════════════════════════════════════════════
# SECTION 9: UNIQUE SYMBOLS
# ═══════════════════════════════════════════════
rej_syms = set(e['symbol'] for e in rejected)
pas_syms = set(e['symbol'] for e in passed)
both = rej_syms & pas_syms
only_rej = rej_syms - pas_syms
only_pas = pas_syms - rej_syms

print("\n" + "=" * 90)
print("  SECTION 9: SYMBOL ANALYSIS")
print("=" * 90)
print(f"\n  Unique symbols reaching checklist: {len(rej_syms | pas_syms)}")
print(f"  Symbols only rejected:             {len(only_rej)}")
print(f"  Symbols only passed:               {len(only_pas)}")
print(f"  Symbols both passed & rejected:    {len(both)}")
print(f"\n  Symbols that PASS checklist (success stories):")
for s in sorted(pas_syms):
    p_count = sum(1 for e in passed if e['symbol'] == s)
    r_count = sum(1 for e in rejected if e['symbol'] == s)
    print(f"    {s:<22s}  passed={p_count:>4d}  rejected={r_count:>4d}")

print("\n" + "=" * 90)
print("  AUDIT COMPLETE")
print("=" * 90)
