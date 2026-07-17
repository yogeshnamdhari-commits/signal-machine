import sqlite3, time
db = sqlite3.connect("packages/ai-engine/data/institutional_v1.db")
db.row_factory = sqlite3.Row

cutoff = time.time() - 7*86400
rows = db.execute("""
    SELECT symbol, side, entry_price, quantity, leverage, 
           institutional_score, confidence, pnl as realized_pnl, exit_reason,
           opened_at, closed_at, stop_loss, take_profit
    FROM positions_archive 
    WHERE closed_at > ? AND pnl IS NOT NULL
    ORDER BY closed_at DESC
""", (cutoff,)).fetchall()

print(f"=== CLOSED TRADES (7d): {len(rows)} ===")

tiers = {"90-100": [], "85-90": [], "70-85": [], "50-70": [], "<50": []}
for r in rows:
    score = r["institutional_score"] or 0
    if score >= 90: tiers["90-100"].append(r)
    elif score >= 85: tiers["85-90"].append(r)
    elif score >= 70: tiers["70-85"].append(r)
    elif score >= 50: tiers["50-70"].append(r)
    else: tiers["<50"].append(r)

print(f"\n{'Tier':10s} {'N':>5s} {'WR%':>7s} {'TotalPnL':>10s} {'AvgPnL':>9s} {'AvgMargin':>10s}")
print("-" * 60)
for tier, trades in tiers.items():
    if not trades:
        continue
    wins = [t for t in trades if (t["realized_pnl"] or 0) > 0]
    total_pnl = sum((t["realized_pnl"] or 0) for t in trades)
    avg_pnl = total_pnl / len(trades)
    wr = len(wins)/len(trades)*100
    avg_size = sum(((t["quantity"] or 0) * (t["entry_price"] or 0) / (t["leverage"] or 1)) for t in trades) / len(trades)
    print(f"  {tier:8s} {len(trades):5d} {wr:6.1f}% ${total_pnl:9.2f} ${avg_pnl:8.2f} ${avg_size:9.2f}")

quality = [r for r in rows if (r["institutional_score"] or 0) >= 85]
all_wins = [r for r in rows if (r["realized_pnl"] or 0) > 0]
q_wins = [r for r in quality if (r["realized_pnl"] or 0) > 0]
all_pnl = sum((r["realized_pnl"] or 0) for r in rows)
q_pnl = sum((r["realized_pnl"] or 0) for r in quality)
bad = [r for r in rows if (r["institutional_score"] or 0) < 85]
bad_wins = [r for r in bad if (r["realized_pnl"] or 0) > 0]
bad_pnl = sum((r["realized_pnl"] or 0) for r in bad)

print(f"\n=== ALL vs QUALITY (85+) ===")
print(f"All trades:     {len(rows):4d} trades | WR {len(all_wins)/len(rows)*100:.1f}% | PnL ${all_pnl:+.2f}")
if quality:
    print(f"Quality (85+):  {len(quality):4d} trades | WR {len(q_wins)/len(quality)*100:.1f}% | PnL ${q_pnl:+.2f}")
if bad:
    bw = len(bad_wins)/len(bad)*100 if bad else 0
    print(f"Bad (<85):      {len(bad):4d} trades | WR {bw:.1f}% | PnL ${bad_pnl:+.2f}")

balance = 10000
print(f"\n=== SIZING SIMULATION (balance=$10,000) ===")

# Risk-based: risk 1% ($100) per trade
risk_pnl = 0
for r in quality:
    entry = r["entry_price"] or 0
    sl = r["stop_loss"] or 0
    qty = r["quantity"] or 1
    pnl = r["realized_pnl"] or 0
    pnl_per_unit = pnl / qty
    sl_dist = abs(entry - sl) if sl else entry * 0.02
    if sl_dist > 0:
        qty_risk = (balance * 0.01) / sl_dist
        risk_pnl += pnl_per_unit * qty_risk

print(f"Flat $25/trade:  {len(quality)} trades PnL = ${q_pnl:+.2f}")
print(f"Risk 1%/trade:   {len(quality)} trades PnL = ${risk_pnl:+.2f}")

# Kelly criterion
q_losses = [r for r in quality if (r["realized_pnl"] or 0) <= 0]
if q_wins and q_losses:
    avg_win = sum((r["realized_pnl"] or 0) for r in q_wins) / len(q_wins)
    avg_loss = abs(sum((r["realized_pnl"] or 0) for r in q_losses) / len(q_losses))
    wr_q = len(q_wins) / len(quality)
    kelly = (wr_q * avg_win - (1-wr_q) * avg_loss) / avg_win if avg_win > 0 else 0
    print(f"\nAvg win: ${avg_win:.2f} | Avg loss: ${avg_loss:.2f} | WR: {wr_q:.1%}")
    print(f"Kelly fraction: {kelly:.2%} (half-Kelly: {kelly/2:.1%})")

    kelly_pnl = 0
    kelly_frac = kelly / 2
    for r in quality:
        entry = r["entry_price"] or 0
        sl = r["stop_loss"] or 0
        qty = r["quantity"] or 1
        pnl = r["realized_pnl"] or 0
        pnl_per_unit = pnl / qty
        sl_dist = abs(entry - sl) if sl else entry * 0.02
        if sl_dist > 0:
            qty_kelly = (balance * kelly_frac) / sl_dist
            kelly_pnl += pnl_per_unit * qty_kelly
    print(f"Half-Kelly:      {len(quality)} trades PnL = ${kelly_pnl:+.2f}")

print(f"\n=== IMPACT SUMMARY ===")
print(f"Current (flat $25, all trades):  ${all_pnl:+.2f}")
if quality:
    delta = q_pnl - all_pnl
    print(f"Quality gate only (85+):         ${q_pnl:+.2f}  ({delta:+.2f} improvement)")
if q_wins and q_losses:
    delta2 = risk_pnl - q_pnl
    print(f"Risk sizing (1% risk):           ${risk_pnl:+.2f}  ({delta2:+.2f} vs flat)")
    delta3 = kelly_pnl - q_pnl
    print(f"Half-Kelly sizing:               ${kelly_pnl:+.2f}  ({delta3:+.2f} vs flat)")
    print(f"\nCOMBINED EFFECT (quality gate + risk sizing): ${risk_pnl:+.2f}")
    print(f"  vs current all-trades flat:                ({risk_pnl - all_pnl:+.2f})")

db.close()
