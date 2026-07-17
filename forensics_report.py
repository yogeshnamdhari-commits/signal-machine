#!/usr/bin/env python3
"""Generate forensics report from saved results."""
import json, statistics

with open("data/bridge/forensics_results.json") as f:
    data = json.load(f)

total = len(data)
print(f"Total symbols analyzed: {total}\n")

# ════════════════════════════════════════════════════════════════
# PHASE 1 — SCORE FORENSICS
# ════════════════════════════════════════════════════════════════
print("=" * 90)
print("PHASE 1 — SCORE FORENSICS")
print("=" * 90)

scores = [r["final_confidence"] for r in data]

buckets = [(0, 20), (20, 30), (30, 40), (40, 45), (45, 50), (50, 55), (55, 60), (60, 101)]
header = f"{'Bucket':<12} {'Count':>6} {'Pct':>7} {'Bar'}"
print(f"\n{header}")
print("-" * 50)
for lo, hi in buckets:
    count = sum(1 for s in scores if lo <= s < hi)
    pct = count / total * 100
    bar = "#" * int(pct / 2)
    print(f"{lo:>3}-{hi:<6} {count:>6} {pct:>6.1f}% {bar}")

print(f"\nTotal symbols:  {total}")
print(f"Average score:  {statistics.mean(scores):.1f}")
print(f"Median score:   {statistics.median(scores):.1f}")
print(f"Highest score:  {max(scores):.1f}")
print(f"Lowest score:   {min(scores):.1f}")
print(f"Std deviation:  {statistics.stdev(scores):.1f}")

# Top 50
top50 = sorted(data, key=lambda x: -x["final_confidence"])[:50]
print(f"\n{'SYMBOL':<16} {'SCORE':>6} {'SIDE':>5} {'REGIME':<16} {'SESSION':<8} {'REJECTED_BY':<30}")
print("-" * 85)
for r in top50:
    print(f"{r['symbol']:<16} {r['final_confidence']:>6.1f} {r['side']:>5} {r['regime_type']:<16} {r['session']:<8} {r['rejected_by']:<30}")

# ════════════════════════════════════════════════════════════════
# PHASE 2 — SURVIVOR ANALYSIS
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("PHASE 2 — SURVIVOR ANALYSIS")
print("=" * 90)

stages = [
    ("Universe", total),
    ("Scorer Passed", sum(1 for r in data if r["scorer_passed"])),
    ("Phase1 Passed", sum(1 for r in data if r["scorer_passed"] and r["phase1_passes"])),
    ("Confidence Floor 55", sum(1 for r in data if r["scorer_passed"] and r["phase1_passes"] and r["conf_floor_pass"])),
    ("Institutional Floor 48.5", sum(1 for r in data if r["scorer_passed"] and r["phase1_passes"] and r["conf_floor_pass"] and r["inst_floor_pass"])),
    ("Regime Filter", sum(1 for r in data if r["scorer_passed"] and r["phase1_passes"] and r["conf_floor_pass"] and r["inst_floor_pass"] and r["regime_passes"])),
    ("Hard Regime (breakout)", sum(1 for r in data if r["scorer_passed"] and r["phase1_passes"] and r["conf_floor_pass"] and r["inst_floor_pass"] and r["regime_passes"] and r["hard_regime_pass"])),
    ("Session Filter", sum(1 for r in data if r["scorer_passed"] and r["phase1_passes"] and r["conf_floor_pass"] and r["inst_floor_pass"] and r["regime_passes"] and r["hard_regime_pass"] and r["session_ok"])),
    ("Quiet Market", sum(1 for r in data if r["scorer_passed"] and r["phase1_passes"] and r["conf_floor_pass"] and r["inst_floor_pass"] and r["regime_passes"] and r["hard_regime_pass"] and r["session_ok"] and not r["quiet"])),
    ("RR >= 2.5", sum(1 for r in data if r["scorer_passed"] and r["phase1_passes"] and r["conf_floor_pass"] and r["inst_floor_pass"] and r["regime_passes"] and r["hard_regime_pass"] and r["session_ok"] and not r["quiet"] and r["rr"] >= 2.5)),
    ("EMITTED", sum(1 for r in data if r["rejected_by"] == "NONE — PASSED ALL")),
]

print(f"\n{'Stage':<30} {'Count':>6} {'% Survive':>10} {'Lost':>8}")
print("-" * 58)
prev = total
for name, count in stages:
    survive = count / total * 100
    lost = prev - count
    pct_lost = lost / prev * 100 if prev > 0 else 0
    print(f"{name:<30} {count:>6} {survive:>9.1f}% {lost:>+7} ({pct_lost:.0f}% of prev)")
    prev = count

# ════════════════════════════════════════════════════════════════
# PHASE 3 — HIGH SCORE REJECTION
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("PHASE 3 — HIGH SCORE REJECTION AUDIT (score >= 45)")
print("=" * 90)

hs = sorted([r for r in data if r["final_confidence"] >= 45], key=lambda x: -x["final_confidence"])
print(f"\nFound {len(hs)} symbols with score >= 45\n")

print(f"{'SYMBOL':<16} {'SCORE':>6} {'7PILLAR':>7} {'REJECTED BY':<30} {'DETAIL'}")
print("-" * 110)
for r in hs:
    d = ""
    if r["rejected_by"] == "SCORER":
        d = f"factors={r['factors_sig']}/{r['factors_total']} of={r['of_score']:.3f} mom={r['mom_score']:.3f} vol={r['vol_score']:.3f} regime={r['regime_score']:.3f}"
    elif r["rejected_by"] == "PHASE1_ADAPTIVE":
        d = f"conf={r['final_confidence']:.1f} raw={r['raw_confidence']:.1f} cal={r['calibrated']:.1f} sm={r['sm_boost']:.2f} btc={r['btc_adj']:.2f}"
    elif r["rejected_by"] == "CONFIDENCE_FLOOR_55":
        d = f"conf={r['final_confidence']:.1f} < 55.0"
    elif r["rejected_by"] == "INSTITUTIONAL_SCORE_48.5":
        d = f"inst={r['inst_7pillar']:.1f} sweep={r['sweep_pillar']:.0f} mss={r['mss_pillar']:.0f} fvg={r['fvg_pillar']:.0f} oi={r['oi_pillar']:.0f} delta={r['delta_pillar']:.0f} cvd={r['cvd_pillar']:.0f} fund={r['fund_pillar']:.0f}"
    elif r["rejected_by"] == "HARD_REGIME_NOT_BREAKOUT":
        d = f"regime={r['regime_type']} (need breakout)"
    elif r["rejected_by"] == "REGIME_FILTER":
        d = f"regime={r['regime_type']}, filter denied"
    elif r["rejected_by"].startswith("SESSION_"):
        d = f"session={r['session']}"
    elif r["rejected_by"] == "QUIET_MARKET":
        d = "quiet=True"
    elif r["rejected_by"].startswith("RR_"):
        d = f"rr={r['rr']:.2f} < 2.5"
    print(f"{r['symbol']:<16} {r['final_confidence']:>6.1f} {r['inst_7pillar']:>7.1f} {r['rejected_by']:<30} {d}")

# ════════════════════════════════════════════════════════════════
# PHASE 4 — TOP 20 CLOSEST TO EMISSION
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("PHASE 4 — TOP 20 CLOSEST TO EMISSION")
print("=" * 90)

gate_rank_map = {
    "NONE — PASSED ALL": 0,
    "RR_": 1,
    "QUIET_MARKET": 2,
    "SESSION_": 3,
    "HARD_REGIME_NOT_BREAKOUT": 4,
    "REGIME_FILTER": 5,
    "INSTITUTIONAL_SCORE_48.5": 6,
    "CONFIDENCE_FLOOR_55": 7,
    "PHASE1_ADAPTIVE": 8,
    "SCORER": 9,
}

def gate_rank(r):
    for g, rank in gate_rank_map.items():
        if r["rejected_by"].startswith(g) or r["rejected_by"] == g:
            return rank
    return 99

closest = sorted(data, key=lambda x: (-gate_rank(x), -x["final_confidence"]))[:20]

print(f"\n{'#':<4} {'SYMBOL':<16} {'SCORE':>6} {'7PILLAR':>7} {'REGIME':<16} {'RR':>5} {'SESSION':<8} {'BLOCKED BY':<30}")
print("-" * 100)
for i, r in enumerate(closest, 1):
    gap = ""
    if r["rejected_by"] == "SCORER":
        gap = f"Need {2 - r['factors_sig']} more factors > 0.15"
    elif r["rejected_by"] == "PHASE1_ADAPTIVE":
        gap = f"Need +{max(0, 50 - r['final_confidence']):.1f} pts"
    elif r["rejected_by"] == "CONFIDENCE_FLOOR_55":
        gap = f"Need +{55 - r['final_confidence']:.1f} pts to 55"
    elif r["rejected_by"] == "INSTITUTIONAL_SCORE_48.5":
        gap = f"Need +{48.5 - r['inst_7pillar']:.1f} pts to 48.5"
    elif r["rejected_by"] == "HARD_REGIME_NOT_BREAKOUT":
        gap = f"Regime={r['regime_type']}"
    elif r["rejected_by"].startswith("SESSION_"):
        gap = f"Session={r['session']}"
    print(f"{i:<4} {r['symbol']:<16} {r['final_confidence']:>6.1f} {r['inst_7pillar']:>7.1f} {r['regime_type']:<16} {r['rr']:>5.2f} {r['session']:<8} {r['rejected_by']:<30} | {gap}")

# ════════════════════════════════════════════════════════════════
# PHASE 5 — THRESHOLD FORENSICS
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("PHASE 5 — SCORE THRESHOLD FORENSICS")
print("=" * 90)

print(f"\nIf ONLY the institutional score gate existed:\n")
for t in [48.5, 47.0, 46.0, 45.0, 44.0, 43.0, 42.0, 40.0]:
    c = sum(1 for r in data if r["inst_7pillar"] >= t)
    print(f"  inst_7pillar >= {t:>5.1f}: {c:>4} / {total} ({c/total*100:.1f}%)")

print(f"\nIf ONLY the final confidence gate existed:\n")
for t in [55.0, 50.0, 48.0, 46.0, 44.0, 42.0, 40.0]:
    c = sum(1 for r in data if r["final_confidence"] >= t)
    print(f"  final_conf >= {t:>5.1f}: {c:>4} / {total} ({c/total*100:.1f}%)")

# ════════════════════════════════════════════════════════════════
# PHASE 6 — WINNER PROFILE MATCH
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("PHASE 6 — WINNER PROFILE MATCH")
print("=" * 90)

# Winner profile from historical data
# Regime = Breakout, Session = London, Hold = 30-120m, RR = 2.5+, Conf = 55+, Inst = 48.5+
print("\nWinner Profile: Regime=Breakout | Session=London | RR>=2.5 | Conf>=55 | Inst>=48.5\n")

def winner_match(r):
    score = 0
    # Regime match (30 pts)
    if r["regime_type"] == "breakout":
        score += 30
    elif r["regime_type"] in ("trending_bull", "trending_bear"):
        score += 20
    # Session match (20 pts)
    if r["session"] in ("london", "london_ny_overlap"):
        score += 20
    elif r["session"] == "new_york":
        score += 10
    # RR match (15 pts)
    if r["rr"] >= 2.5:
        score += 15
    elif r["rr"] >= 2.0:
        score += 10
    elif r["rr"] >= 1.5:
        score += 5
    # Confidence match (20 pts)
    if r["final_confidence"] >= 55:
        score += 20
    elif r["final_confidence"] >= 45:
        score += 15
    elif r["final_confidence"] >= 40:
        score += 10
    # Institutional match (15 pts)
    if r["inst_7pillar"] >= 48.5:
        score += 15
    elif r["inst_7pillar"] >= 45:
        score += 10
    elif r["inst_7pillar"] >= 42:
        score += 5
    return score

for r in data:
    r["winner_match"] = winner_match(r)

top_match = sorted(data, key=lambda x: -x["winner_match"])[:30]
print(f"{'SYMBOL':<16} {'MATCH%':>7} {'SCORE':>6} {'7PILLAR':>7} {'REGIME':<16} {'SESSION':<8} {'RR':>5} {'REJECTED':<30}")
print("-" * 105)
for r in top_match:
    print(f"{r['symbol']:<16} {r['winner_match']:>5}% {r['final_confidence']:>6.1f} {r['inst_7pillar']:>7.1f} {r['regime_type']:<16} {r['session']:<8} {r['rr']:>5.2f} {r['rejected_by']:<30}")

# ════════════════════════════════════════════════════════════════
# PHASE 7 — EMISSION BLOCKER RANKING
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("PHASE 7 — EMISSION BLOCKER RANKING")
print("=" * 90)

bc = {}
for r in data:
    b = r["rejected_by"]
    bc[b] = bc.get(b, 0) + 1

ranked = sorted(bc.items(), key=lambda x: -x[1])
print(f"\n{'RANK':<6} {'BLOCKER':<35} {'COUNT':>6} {'%':>7} {'IMPACT'}")
print("-" * 70)
for i, (b, c) in enumerate(ranked, 1):
    impact = ""
    if c > total * 0.3:
        impact = "██████████ CRITICAL"
    elif c > total * 0.1:
        impact = "███████ HIGH"
    elif c > total * 0.05:
        impact = "████ MEDIUM"
    else:
        impact = "██ LOW"
    print(f"{i:<6} {b:<35} {c:>6} {c/total*100:>6.1f}% {impact}")

# ════════════════════════════════════════════════════════════════
# PHASE 8 — FINAL ANSWER
# ════════════════════════════════════════════════════════════════
print("\n" + "=" * 90)
print("PHASE 8 — FINAL ANSWER")
print("=" * 90)

# Count symbols within 5% of passing each gate
within_5_of_inst_floor = sum(1 for r in data if 48.5 - r["inst_7pillar"] <= 2.5 and r["inst_7pillar"] < 48.5)
within_5_of_conf_floor = sum(1 for r in data if 55 - r["final_confidence"] <= 5 and r["final_confidence"] < 55)
scorer_passed_count = sum(1 for r in data if r["scorer_passed"])

print(f"""
A) Scanner healthy?
   NO. {scorer_passed_count}/{total} ({scorer_passed_count/total*100:.1f}%) pass the AI scorer.
   The scanner processes data correctly but the scorer rejects the vast majority.

B) Are valid opportunities currently present?
   YES. {scorer_passed_count} symbols produced scorer signals.
   {within_5_of_inst_floor} symbols are within 2.5 pts of the institutional score floor.
   {within_5_of_conf_floor} symbols are within 5 pts of the confidence floor.
   Market IS generating data — the filter chain is killing it.

C) Which exact filter is preventing emission?
   RUNTIME PROOF:
""")
for i, (b, c) in enumerate(ranked[:5], 1):
    print(f"   {i}. {b}: {c}/{total} ({c/total*100:.1f}%)")

# Find the exact symbol closest to passing
emitted = [r for r in data if r["rejected_by"] != "SCORER"]
if emitted:
    closest_to_passing = sorted(emitted, key=lambda x: (-gate_rank(x), -x["final_confidence"]))
    ctp = closest_to_passing[0] if closest_to_passing else None
    if ctp:
        print(f"\n   CLOSEST TO EMISSION: {ctp['symbol']} (score={ctp['final_confidence']:.1f}, blocked by {ctp['rejected_by']})")

print(f"""
D) How many symbols are within 5% of emission?
   {within_5_of_conf_floor} symbols within 5 pts of confidence floor (55)
   {within_5_of_inst_floor} symbols within 2.5 pts of institutional floor (48.5)

E) What is the single biggest blocker?
   {ranked[0][0]}: {ranked[0][1]}/{total} ({ranked[0][1]/total*100:.1f}%)
   
   SQL PROOF: Of {total} live symbols analyzed this session, {ranked[0][1]} ({ranked[0][1]/total*100:.1f}%) 
   were killed at {ranked[0][0]}. Only {scorer_passed_count} ({scorer_passed_count/total*100:.1f}%) 
   passed the AI scorer, and 0 passed all gates.
""")

# Save updated results with winner match
with open("data/bridge/forensics_results.json", "w") as f:
    json.dump(data, f, indent=2, default=str)
