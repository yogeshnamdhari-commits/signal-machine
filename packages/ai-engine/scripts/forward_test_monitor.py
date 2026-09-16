#!/usr/bin/env python3
"""
EMA V5 Forward Test Monitor v2
Tracks full feature set for every completed trade to identify
which combinations of features consistently predict profitable trades.

Run periodically (e.g., daily) to generate validation reports.
Does NOT modify the scanner or production logic.
"""
import sqlite3
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "institutional_v1.db"
REPORT_PATH = Path(__file__).parent.parent / "data" / "forward_test_validation.md"
TRADE_LOG_PATH = Path(__file__).parent.parent / "data" / "forward_test_trades.jsonl"


def get_trades_with_features():
    conn = sqlite3.connect(str(DB_PATH))
    cur = conn.cursor()
    cur.execute("""
        SELECT p.symbol, p.side, p.entry_price, p.stop_loss, p.take_profit,
               p.pnl, p.exit_reason, p.confidence, p.regime, p.opened_at,
               p.mfe_pct, p.mae_pct, p.hold_minutes, p.quantity, s.metadata
        FROM positions_archive p
        LEFT JOIN signals s ON p.symbol = s.symbol 
        WHERE p.strategy_version = 'ema_v5'
        AND s.metadata IS NOT NULL AND json_valid(s.metadata)
        ORDER BY p.opened_at
    """)
    raw = cur.fetchall()
    conn.close()

    trades = []
    for r in raw:
        sym, side, entry, sl, tp, pnl, exit_r, conf, regime, opened, mfe, mae, hold, qty, meta_raw = r
        try:
            meta = json.loads(meta_raw)
        except:
            continue
        if not isinstance(meta, dict):
            continue

        ema = meta.get('ema_data', {})
        comp = meta.get('components', {})
        if not isinstance(ema, dict): ema = {}
        if not isinstance(comp, dict): comp = {}

        e20 = float(ema.get('ema20', 0) or 0)
        e50 = float(ema.get('ema50', 0) or 0)
        e144 = float(ema.get('ema144', 0) or 0)
        e200 = float(ema.get('ema200', 0) or 0)
        atr = float(ema.get('atr_14', 0) or 0)

        trend_score = 0
        volume_ratio = 0
        trend_str = str(comp.get('trend', ''))
        if 'score=' in trend_str:
            try: trend_score = float(trend_str.split('score=')[1].split('_')[0])
            except: pass
        vol_str = str(comp.get('volume', ''))
        if 'ratio=' in vol_str:
            try: volume_ratio = float(vol_str.split('ratio=')[1].split('_')[0])
            except: pass

        sp_20_50 = abs(e20 - e50) / entry * 100 if entry and e20 and e50 else 0
        sp_50_144 = abs(e50 - e144) / entry * 100 if entry and e50 and e144 else 0
        sp_144_200 = abs(e144 - e200) / entry * 100 if entry and e144 and e200 else 0
        total_spread = sp_20_50 + sp_50_144 + sp_144_200
        atr_pct = atr / entry * 100 if entry and atr else 0
        sl_dist = abs(entry - sl) / entry * 100 if entry and sl else 0
        risk = abs(entry - sl) if entry and sl else 0
        reward = abs(tp - entry) if entry and tp else 0
        rr = reward / risk if risk > 0 else 0

        dt = datetime.fromtimestamp(opened, tz=timezone.utc)
        hour = dt.hour
        session = 'new_york' if 13 <= hour < 21 else ('london' if 7 <= hour < 15 else 'asia')

        trades.append({
            'symbol': sym, 'side': side, 'entry': entry, 'pnl': pnl or 0,
            'exit_reason': exit_r, 'confidence': conf or 0, 'trend_score': trend_score,
            'volume_ratio': volume_ratio, 'regime': regime, 'session': session,
            'mfe': mfe or 0, 'mae': mae or 0, 'hold_minutes': hold or 0,
            'ema_spread_20_50': sp_20_50, 'ema_spread_50_144': sp_50_144,
            'ema_spread_144_200': sp_144_200, 'ema_spread_total': total_spread,
            'atr_pct': atr_pct, 'sl_dist': sl_dist, 'rr': rr, 'hour': hour,
            'win': 1 if pnl and pnl > 0 else 0,
        })
    return trades


def calc(trades):
    if not trades: return None
    n = len(trades)
    w = [t for t in trades if t['win']]
    l = [t for t in trades if not t['win']]
    gp = sum(t['pnl'] for t in w)
    gl = abs(sum(t['pnl'] for t in l))
    return {
        'n': n, 'wins': len(w), 'losses': len(l),
        'wr': len(w)/n*100, 'pnl': sum(t['pnl'] for t in trades),
        'avg': sum(t['pnl'] for t in trades)/n,
        'pf': gp/gl if gl > 0 else float('inf'),
    }


def corr(x, y):
    n = len(x)
    if n < 3: return 0
    mx, my = sum(x)/n, sum(y)/n
    sx = math.sqrt(sum((xi-mx)**2 for xi in x)/n) or 1e-10
    sy = math.sqrt(sum((yi-my)**2 for yi in y)/n) or 1e-10
    return sum((xi-mx)*(yi-my) for xi, yi in zip(x,y))/(n*sx*sy)


def generate_report():
    trades = get_trades_with_features()
    if not trades: return "No trades found."
    overall = calc(trades)
    pnls = [t['pnl'] for t in trades]
    wins = [t['win'] for t in trades]

    r = []
    r.append("# EMA V5 Forward Test Validation Report")
    r.append(f"\nGenerated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    # Summary
    r.append(f"\n## 1. Summary")
    r.append(f"- Trades: {len(trades)} | PF: {overall['pf']:.2f} | WR: {overall['wr']:.1f}% | PnL: ${overall['pnl']:+.2f}")

    # Filter replay
    r.append(f"\n## 2. Candidate Filter Replay")
    r.append(f"\n| Filter | Trades | Wins | WR% | PnL | PF | Status |")
    r.append(f"|---|---|---|---|---|---|---|")
    for name, fn in [
        ('ALL', lambda t: True),
        ('CONF≥0.93', lambda t: t['confidence'] >= 0.93),
        ('TREND≥88', lambda t: t['trend_score'] >= 88),
        ('CONF≥0.93+TREND≥88', lambda t: t['confidence'] >= 0.93 and t['trend_score'] >= 88),
    ]:
        f = [t for t in trades if fn(t)]
        m = calc(f)
        if not m: r.append(f"| {name} | 0 | — | — | — | — | — |"); continue
        pf = f"{m['pf']:.2f}" if m['pf'] != float('inf') else "∞"
        st = f"⚠️ Need {20-m['n']}" if m['n'] < 20 else ("✅" if m['pf'] >= 1.5 else "🟡" if m['pf'] >= 1.0 else "❌")
        r.append(f"| {name} | {m['n']} | {m['wins']} | {m['wr']:.0f}% | ${m['pnl']:+.2f} | {pf} | {st} |")

    # Feature correlation
    r.append(f"\n## 3. Feature Correlation with PnL")
    r.append(f"\n| Feature | r(PnL) | r(Win) | Mean(W) | Mean(L) | Strength |")
    r.append(f"|---|---|---|---|---|---|")
    corrs = []
    for fk, fn in [('confidence','Confidence'),('trend_score','Trend Score'),('volume_ratio','Volume Ratio'),
                    ('ema_spread_20_50','EMA 20-50'),('ema_spread_50_144','EMA 50-144'),('ema_spread_total','Total Spread'),
                    ('atr_pct','ATR%'),('sl_dist','SL Dist%'),('rr','R:R'),('mfe','MFE%'),('mae','MAE%')]:
        vals = [t[fk] for t in trades]
        rp = corr(vals, pnls)
        rw = corr(vals, wins)
        mw = statistics.mean([t[fk] for t in trades if t['win']]) if any(t['win'] for t in trades) else 0
        ml = statistics.mean([t[fk] for t in trades if not t['win']]) if any(not t['win'] for t in trades) else 0
        st = "**STRONG**" if abs(rp) > 0.3 else ("*moderate*" if abs(rp) > 0.15 else "weak")
        corrs.append((fn, rp, st))
        r.append(f"| {fn} | {rp:+.3f} | {rw:+.3f} | {mw:.3f} | {ml:.3f} | {st} |")

    # Rankings
    r.append(f"\n## 4. Top Predictors")
    corrs.sort(key=lambda x: abs(x[1]), reverse=True)
    r.append(f"\n| Rank | Feature | r(PnL) |")
    r.append(f"|---|---|---|")
    for i, (fn, rp, st) in enumerate(corrs[:5], 1):
        r.append(f"| {i} | {fn} | {rp:+.3f} |")

    # Feature Importance: actual performance by threshold
    r.append(f"\n## 5. Feature Importance by Threshold")
    r.append(f"\n*Which thresholds actually improve performance?*")
    r.append(f"\n| Feature | Threshold | Trades | Wins | WR% | PF | Avg PnL | vs Baseline |")
    r.append(f"|---|---|---|---|---|---|---|---|")

    baseline_m = calc(trades)
    baseline_pf = baseline_m['pf'] if baseline_m else 0

    thresholds = [
        ('Confidence', 'confidence', [(0.90, '≥0.90'), (0.93, '≥0.93'), (0.95, '≥0.95')]),
        ('Trend Score', 'trend_score', [(80, '≥80'), (85, '≥85'), (88, '≥88'), (100, '≥100')]),
        ('Volume Ratio', 'volume_ratio', [(1.0, '≥1.0'), (1.5, '≥1.5'), (2.0, '≥2.0')]),
        ('EMA Spread', 'ema_spread_total', [(1.0, '≥1.0%'), (2.0, '≥2.0%'), (3.0, '≥3.0%')]),
        ('SL Distance', 'sl_dist', [(0.3, '<0.3%'), (0.5, '<0.5%'), (0.0, '=0%')]),
        ('MFE', 'mfe', [(0.5, '≥0.5%'), (1.0, '≥1.0%'), (2.0, '≥2.0%')]),
    ]

    for feat_name, feat_key, thrs in thresholds:
        for thr_val, thr_label in thrs:
            if '=' in thr_label and '<' not in thr_label:
                filtered = [t for t in trades if t[feat_key] == thr_val]
            elif '<' in thr_label:
                filtered = [t for t in trades if t[feat_key] < thr_val]
            else:
                filtered = [t for t in trades if t[feat_key] >= thr_val]

            m = calc(filtered)
            if not m or m['n'] < 2:
                continue

            pf_str = f"{m['pf']:.2f}" if m['pf'] != float('inf') else "∞"
            delta = m['pf'] - baseline_pf if m['pf'] != float('inf') else 999
            delta_str = f"{delta:+.2f}" if delta != 999 else "∞"
            improvement = "✅" if m['pf'] > baseline_pf and m['pf'] >= 1.0 else "🟡" if m['pf'] > baseline_pf else "❌"

            r.append(f"| {feat_name} {thr_label} | {thr_label} | {m['n']} | {m['wins']} | {m['wr']:.0f}% | {pf_str} | ${m['avg']:+.2f} | {delta_str} {improvement} |")

    # Validation progress
    r.append(f"\n## 6. Validation Progress")
    r.append(f"\n*Criteria: ≥20 trades, PF≥1.5, WR≥50%*")
    for name, fn in [('CONF≥0.93', lambda t: t['confidence']>=0.93), ('TREND≥88', lambda t: t['trend_score']>=88)]:
        n = len([t for t in trades if fn(t)])
        bar = '█' * n + '░' * max(0, 20-n)
        r.append(f"- {name}: {n}/20 [{bar}]")

    # Recent trades
    r.append(f"\n## 7. Recent Trades")
    r.append(f"\n| Symbol | Side | PnL | Exit | Conf | Trend | Spread | MFE |")
    r.append(f"|---|---|---|---|---|---|---|---|")
    for t in trades[-10:]:
        r.append(f"| {t['symbol']} | {t['side']} | ${t['pnl']:+.2f} | {t['exit_reason']} | {t['confidence']:.2f} | {t['trend_score']:.0f} | {t['ema_spread_total']:.2f}% | {t['mfe']:.2f}% |")

    return "\n".join(r)


if __name__ == "__main__":
    trades = get_trades_with_features()
    TRADE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADE_LOG_PATH, "w") as f:
        for t in trades: f.write(json.dumps(t) + "\n")

    report = generate_report()
    print(report)

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        f.write(report)
    print(f"\nReport: {REPORT_PATH}")
    print(f"Trade log: {TRADE_LOG_PATH}")
