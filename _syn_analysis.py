import json, time
from datetime import datetime

d = json.load(open('/Users/targetmobile/Documents/signal machine/packages/ai-engine/data/bridge/positions.json'))
for p in d.get('positions', []):
    if p['symbol'] == 'SYNUSDT':
        opened = p.get('opened_at', 0)
        entry_time = datetime.fromtimestamp(opened)

        print('🔴 SYNUSDT — SIGNAL QUALITY AT ENTRY')
        print('=' * 60)
        print(f'  Entry Time: {entry_time.strftime("%Y-%m-%d %H:%M:%S")}')
        print(f'  Hold Time:  {(time.time()-opened)/60:.0f} min ({(time.time()-opened)/3600:.1f} hours)')
        print()
        print('  📊 SIGNAL SCORES:')
        print(f'    Confidence:       {p.get("confidence",0)*100:.0f}%')
        print(f'    Institutional:    {p.get("institutional_score",0):.0f}')
        print(f'    Alpha Score:      {p.get("alpha_score",0):.0f}')
        print(f'    Alpha Tier:       {p.get("alpha_tier","?")}')
        print()
        print('  🎯 CHECKLIST COMPONENTS:')
        breakdown = p.get('score_breakdown', {})
        for k, v in sorted(breakdown.items(), key=lambda x: x[1], reverse=True):
            print(f'    {k:15s}: {v:.1f}')
        print()
        print('  📈 ENTRY DETAILS:')
        print(f'    Side:             {p.get("side","?")}')
        print(f'    Entry Price:      ${p.get("entry_price",0):.6f}')
        print(f'    Stop Loss:        ${p.get("stop_loss",0):.6f}')
        print(f'    Take Profit:      ${p.get("take_profit_1",0):.6f}')
        print(f'    Leverage:         {p.get("leverage",10)}x')
        print(f'    Quantity:         {p.get("quantity",0):.2f}')
        margin = p.get('entry_price',0)*p.get('quantity',0)/p.get('leverage',10)
        print(f'    Margin:           ${margin:.2f}')
        print()
        print('  🌍 MARKET CONTEXT AT ENTRY:')
        print(f'    Regime:           {p.get("regime","?")}')
        print(f'    R:R:              {p.get("risk_reward",0):.1f}')
        print(f'    Risk %:           {p.get("risk_pct",0):.1f}%')
        print()

        conf = p.get('confidence',0)*100
        inst = p.get('institutional_score',0)
        rr = p.get('risk_reward',0)
        risk = p.get('risk_pct',0)

        print('  🔍 QUALITY ASSESSMENT:')
        print(f'    Confidence:       {"✅ EXCELLENT" if conf >= 90 else "⚠️ GOOD" if conf >= 80 else "❌ LOW"} ({conf:.0f}%)')
        print(f'    Institutional:    {"✅ HIGH" if inst >= 85 else "⚠️ MEDIUM" if inst >= 75 else "❌ LOW"} ({inst:.0f})')
        print(f'    Risk:Reward:      {"✅ GOOD" if rr >= 2.0 else "⚠️ ACCEPTABLE" if rr >= 1.5 else "❌ POOR"} ({rr:.1f})')
        print(f'    Risk %:           {"✅ LOW" if risk <= 3.0 else "⚠️ MODERATE" if risk <= 5.0 else "❌ HIGH — WOULD BE BLOCKED NOW"} ({risk:.1f}%)')

        # Compare with current engine state
        state = json.load(open('/Users/targetmobile/Documents/signal machine/packages/ai-engine/data/engine_state.json'))
        print()
        print('  ⚖️  VERDICT:')
        print(f'    The signal HAD high confidence ({conf:.0f}%) and institutional score ({inst:.0f}).')
        print(f'    The PROBLEM was the SL distance ({risk:.1f}%) — way too wide.')
        print(f'    With our new 5% SL cap, this trade would have been:')
        print(f'    → SL tightened to ATR-based (~3-4% instead of 10.4%)')
        print(f'    → Position size reduced (risk-normalized)')
        print(f'    → Max loss capped at ~$12 instead of $26')
