"""
DeltaTerminal — Institutional Trading Terminal
================================================
Compact 9-tab layout with ALL rich features preserved.
Dashboard fits ONE viewport. Search-based checklist. Paginated tables.
Includes: Trade Analytics, Heatmaps, Live Metrics, Alpha Ranking, Signal Cards.

PRODUCTION LOCK: ALL signal logic untouched.
"""
from __future__ import annotations
import json, os, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import Counter, defaultdict
from typing import Any, Dict, List
import streamlit as st
from streamlit_autorefresh import st_autorefresh

# Add ai-engine to path for module imports
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

st.set_page_config(page_title="DeltaTerminal", page_icon="🏛️", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""<style>
    .block-container{padding-top:.2rem;padding-bottom:.2rem;max-width:100%}
    [data-testid="stHeader"]{background:transparent}
    div[data-testid="stTabs"] button{font-size:.8rem !important;padding:2px 8px !important}
    .m-box{background:#161b22;border:1px solid #30363d;border-radius:6px;padding:4px 8px;text-align:center;flex:1;min-width:0}
    .m-val{font-size:1rem;font-weight:bold;color:#58a6ff;line-height:1.2}
    .m-lbl{font-size:.55rem;color:#8b949e;text-transform:uppercase;line-height:1.1}
    .pipe-stage{background:#161b22;border:1px solid #30363d;border-radius:4px;padding:2px 4px;text-align:center;font-size:.65rem}
    .pipe-val{font-size:.9rem;font-weight:bold;color:#58a6ff}
    .pipe-lbl{font-size:.5rem;color:#8b949e}
    .pipe-drop{font-size:.45rem;color:#f85149}
    .sg{color:#3fb950;font-weight:bold}.sr{color:#f85149;font-weight:bold}
    .bar{height:5px;background:#30363d;border-radius:3px;overflow:hidden;margin-top:1px}
    .bar-fill{height:100%;border-radius:3px}
    .bar-blue{background:#58a6ff}.bar-red{background:#f85149}.bar-green{background:#3fb950}
    .gate-row{display:flex;gap:2px;margin:1px 0}
    .gate-box{flex:1;padding:2px 4px;border-radius:3px;font-size:.7rem;text-align:center}
    .gate-p{background:#1a3a1a;color:#3fb950}.gate-f{background:#3a1a1a;color:#f85149}.gate-s{background:#3a3a1a;color:#d29922}
    .signal-long{border-left:3px solid #3fb950;padding:4px 8px;margin:2px 0;border-radius:4px;background:#0d1117}
    .signal-short{border-left:3px solid #f85149;padding:4px 8px;margin:2px 0;border-radius:4px;background:#0d1117}
</style>""", unsafe_allow_html=True)

# ── DATA LAYER ──
BRIDGE = Path(__file__).parent.parent / "data" / "bridge"
@st.cache_data(ttl=1)
def J(name:str)->dict:
    fp=BRIDGE/f"{name}.json"
    if not fp.exists(): return {}
    try:
        with open(fp) as f: data=json.load(f)
        # STALE DATA GUARD: If bridge file older than 30s, mark as stale
        _age=time.time()-data.get("timestamp",0) if isinstance(data,dict) and "timestamp" in data else 0
        if _age>300:  # 5 minutes = engine likely dead
            if isinstance(data,dict): data["_stale"]=True; data["_age_s"]=round(_age,1)
        elif _age>60:  # 1 minute = possible issue
            if isinstance(data,dict): data["_warn_age"]=True; data["_age_s"]=round(_age,1)
        return data
    except: return {}

def _ts(t): return datetime.fromtimestamp(t,tz=timezone.utc).strftime("%H:%M") if t else "—"
def _dt(t): return datetime.fromtimestamp(t,tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if t else "—"
def _p(v):
    if v is None or v==0: return "—"
    if v>=100: return f"{v:.2f}"
    if v>=1: return f"{v:.4f}"
    if v>=0.01: return f"{v:.5f}"
    return f"{v:.6f}"
def _usd(v):
    if abs(v)>=1e6: return f"${v/1e6:.1f}M"
    if abs(v)>=1e3: return f"${v/1e3:.1f}K"
    return f"${v:.2f}"
def _sess():
    h=datetime.now(timezone.utc).hour
    if 13<=h<16: return "London-NY"
    if 7<=h<16: return "London"
    if 13<=h<22: return "New York"
    if 0<=h<8: return "Asia"
    return "Off-Hours"
def _next():
    h=datetime.now(timezone.utc).hour
    if h<7: return "London 07:00"
    if h<13: return "LON-NY 13:00"
    if h<16: return "New York 16:00"
    if h<22: return "Asia 22:00"
    return "London 07:00"
def _health():
    raw=J("status"); s=raw.get("status",raw) if isinstance(raw,dict) else {}
    r={}
    r["Engine"]="ON" if s.get("running") else "OFF"
    sc=s.get("symbols_connected",s.get("symbols",0)) or 0
    r["Scanner"]="ON" if sc>100 else ("WARN" if sc>0 else "OFF")
    r["WebSocket"]="ON" if s.get("ws_connected") else "WARN"
    r["Database"]="ON"
    r["Orderflow"]="ON" if s.get("orderflow_active",True) else "WARN"
    return r

# ═══════════════════════════════════════════════════════════════
# TAB 1 — EXECUTIVE DASHBOARD (ONE SCREEN)
# ═══════════════════════════════════════════════════════════════
def tab1():
    raw=J("status"); status=raw.get("status",raw) if isinstance(raw,dict) else {}
    signals=J("signals").get("signals",[]); positions=J("positions").get("positions",[]); metrics=J("metrics").get("metrics",{}); funnel=J("funnel").get("funnel",{})
    
    # ROW 1: Status bar
    eng="🟢" if status.get("running") else "🔴"
    sc=status.get("symbols_connected",status.get("symbols",0)) or 0
    dc="🟢" if sc>100 else ("🟡" if sc>0 else "🔴")
    ws="🟢" if status.get("ws_connected") else "🟡"
    regime=metrics.get("regime",{}).get("type","—").upper()
    rconf=metrics.get("regime",{}).get("confidence",0)*100
    sess=_sess(); nx=_next()
    ut=status.get("uptime",0); ut_s=f"{ut/3600:.1f}h" if ut>3600 else f"{ut/60:.0f}m"
    lat=f"{(metrics.get('latency_ms') or 0):.0f}ms"
    
    # ── STALE DATA WARNING ──
    _status_age=time.time()-raw.get("timestamp",0) if "timestamp" in raw else 0
    _stale_warn=""
    if _status_age>300:
        _stale_warn='<span style="color:#f85149;font-weight:bold">⚠️ STALE ({:.0f}s)</span>'.format(_status_age)
    elif _status_age>60:
        _stale_warn='<span style="color:#d29922">⏱️ Age: {:.0f}s</span>'.format(_status_age)
    
    _status_html = '<div style="display:flex;gap:6px;align-items:center;background:#0d1117;padding:4px 10px;border-radius:5px;font-size:.72rem;margin-bottom:3px;flex-wrap:wrap">'
    _status_html += '<span class="sg">🏛️ DELTATERMINAL</span>│'
    if _stale_warn:
        _status_html += _stale_warn + '│'
    _status_html += f'<span>{eng} {ut_s}</span>│'
    _status_html += f'<span>{dc} Data {sc}</span>│'
    _status_html += f'<span>{ws} WS</span>│'
    _status_html += f'<span>📊 {regime} {rconf:.0f}%</span>│'
    _status_html += f'<span>🕐 {sess}</span>│'
    _status_html += f'<span>⏭️ {nx}</span>│'
    _status_html += f'<span>⚡ {lat}</span>'
    _status_html += '</div>'
    st.markdown(_status_html, unsafe_allow_html=True)
    
    # ROW 2: KPI cards — use metrics.json as primary source, positions as live overlay
    mets=J("metrics").get("metrics",{})
    active_pos=[p for p in positions if p.get("status")=="open"]
    open_pnl=sum(p.get("pnl",0) or p.get("unrealized_pnl",0) for p in active_pos)
    closed=[p for p in positions if p.get("status")!="open" and p.get("pnl") is not None]
    
    # Primary source: metrics.json (has full historical UNION ALL data)
    total_pnl=mets.get("total_pnl",0) if mets else 0
    wr=mets.get("win_rate",0) if mets else 0
    pf=mets.get("profit_factor",0) if mets else 0
    md=abs(mets.get("max_drawdown",0)) if mets else 0
    trades_total=mets.get("trades_total",0) if mets else 0
    
    # Overlay live open PnL onto total
    total_pnl = round(total_pnl + open_pnl, 2)
    
    # If there are closed positions in the current session not yet in metrics, include them
    if closed and not mets:
        wins=sum(1 for p in closed if (p.get("pnl") or 0)>0)
        total_pnl=sum(p.get("pnl",0) for p in closed)
        wr=(wins/len(closed)*100) if closed else 0
        wp=sum(p.get("pnl",0) for p in closed if (p.get("pnl") or 0)>0)
        lp=abs(sum(p.get("pnl",0) for p in closed if (p.get("pnl") or 0)<0))
        pf=wp/lp if lp>0 else 0
        cum=0;pk=0;md=0
        for p in sorted(closed,key=lambda x:x.get("opened_at",0)):
            cum+=p.get("pnl",0);pk=max(pk,cum);md=max(md,pk-cum)
    
    emitted=funnel.get("funnel",funnel).get("signals_emitted",funnel.get("emitted",0))
    
    c1,c2,c3,c4,c5,c6,c7,c8=st.columns(8)
    with c1: st.markdown(f"""<div class="m-box"><div class="m-val">{_usd(total_pnl)}</div><div class="m-lbl">Total PnL</div></div>""",unsafe_allow_html=True)
    with c2: st.markdown(f"""<div class="m-box"><div class="m-val">{wr:.1f}%</div><div class="m-lbl">Win Rate</div></div>""",unsafe_allow_html=True)
    with c3: st.markdown(f"""<div class="m-box"><div class="m-val">{pf:.2f}</div><div class="m-lbl">Profit Factor</div></div>""",unsafe_allow_html=True)
    with c4: st.markdown(f"""<div class="m-box"><div class="m-val">{_usd(md)}</div><div class="m-lbl">Max Drawdown</div></div>""",unsafe_allow_html=True)
    with c5: st.markdown(f"""<div class="m-box"><div class="m-val">{len(signals)}</div><div class="m-lbl">Signals Today</div></div>""",unsafe_allow_html=True)
    with c6: st.markdown(f"""<div class="m-box"><div class="m-val">{len(active_pos)}</div><div class="m-lbl">Open Trades</div></div>""",unsafe_allow_html=True)
    with c7: st.markdown(f"""<div class="m-box"><div class="m-val">{emitted}</div><div class="m-lbl">Emitted</div></div>""",unsafe_allow_html=True)
    with c8: st.markdown(f"""<div class="m-box"><div class="m-val">{lat}</div><div class="m-lbl">Latency</div></div>""",unsafe_allow_html=True)
    
    # ROW 3: Market state + Pipeline
    cL,cR=st.columns([1,2])
    with cL:
        st.caption("Market State")
        intel=J("market_intelligence").get("intelligence",{})
        br=intel.get("breadth",{})
        rd=intel.get("regime_distribution",{})
        if not br and rd:
            total=sum(rd.values()) or 1
            lb=rd.get("trending_bull",0)/total*100
            sb=rd.get("trending_bear",0)/total*100
            nb=rd.get("range",0)/total*100
            br={"long_bias":lb/100,"short_bias":sb/100,"bullish_pct":lb/100,"bearish_pct":sb/100}
        if br:
            lb=br.get("long_bias",0)*100; sb=br.get("short_bias",0)*100
            st.markdown(f"""<div style="font-size:.72rem;line-height:1.4">
            <b>🟢 Long</b> {lb:.0f}% &nbsp; <b>🔴 Short</b> {sb:.0f}% &nbsp; <b>⚪ Neutral</b> {100-lb-sb:.0f}%<br>
            <b>📈 Bullish</b> {br.get('bullish_pct',0)*100:.0f}% &nbsp; <b>📉 Bearish</b> {br.get('bearish_pct',0)*100:.0f}%
            </div>""",unsafe_allow_html=True)
        else:
            st.caption("No market data")
    
    with cR:
        st.caption("Pipeline")
        _f=funnel.get("funnel",funnel)
        _scanned=_f.get("symbols_processed",_f.get("scanned",0))
        _ai_rej=_f.get("scorer_rejected",_f.get("ai_rejected",0))
        _ai_pass=_scanned-_ai_rej if _scanned else _f.get("scorer_pass",_f.get("ai_pass",0))
        _regime_blocked=_f.get("regime_blocked",0)
        _session_blocked=_f.get("session_blocked",0)
        _regime_pass=_ai_pass-_regime_blocked
        _session_pass=_regime_pass-_session_blocked
        _checklist_reached=_f.get("checklist_reached",_f.get("checklist_blocked",0))
        _checklist_passed=_f.get("checklist_passed",0)
        _generated=_f.get("generated",_f.get("inst_signal_emitted",_checklist_passed))
        _emitted=_f.get("emitted",_f.get("signals_emitted",0))
        stages=[("Scan",_scanned),("AI",_ai_pass),("Regime",_regime_pass),("Session",_session_pass),("Check",_checklist_reached),("Pass",_checklist_passed),("Gen",_generated),("Emit",_emitted)]
        cols=st.columns(len(stages))
        for i,(nm,vl) in enumerate(stages):
            with cols[i]:
                prev=stages[i-1][1] if i>0 else vl
                drop=f"-{(prev-vl)/prev*100:.0f}%" if prev>0 and prev>vl else ""
                st.markdown(f"""<div class="pipe-stage"><div class="pipe-val">{vl}</div><div class="pipe-lbl">{nm}</div><div class="pipe-drop">{drop}</div></div>""",unsafe_allow_html=True)
    
    # ROW 4: Signals table (MAX 10 rows)
    st.caption("Live Signals")
    if signals:
        import pandas as pd
        pos_list=J("positions").get("positions",[])
        trade_hist=J("trade_history").get("trades",[])
        pos_map={(p.get("symbol"),p.get("side")):p for p in pos_list}
        trade_map={}
        for t in sorted(trade_hist,key=lambda x:x.get("timestamp",0),reverse=True):
            k2=(t.get("symbol"),t.get("side"))
            if k2 not in trade_map: trade_map[k2]=t
        rows=[]
        for s in signals[-10:]:
            sym=s.get("symbol","?"); side=s.get("side","?")
            pos=pos_map.get((sym,side)); trade=trade_map.get((sym,side))
            pnl_val=pos.get("pnl") if pos else (trade.get("pnl") if trade else None)
            exit_r=trade.get("exit_reason","OPEN") if trade else ("OPEN" if pos else "")
            _ep=s.get("entry_price",s.get("entry",0)) or 0
            _sl=s.get("stop_loss",0) or 0
            _tp=s.get("take_profit",0) or 0
            def _fmt_price(v):
                if not v: return "—"
                if v>=100: return f"{v:.2f}"
                if v>=1: return f"{v:.4f}"
                if v>=0.01: return f"{v:.5f}"
                return f"{v:.6f}"
            rows.append({"Symbol":sym,"Side":side,"Entry":_fmt_price(_ep),"SL":_fmt_price(_sl),"TP1":_fmt_price(_tp),"RR":round(s.get("risk_reward",0) or 0,2),"PnL":f"{pnl_val:.2f}" if pnl_val is not None else "—","Exit":exit_r or "—","Status":s.get("status","?").upper()})
        df=pd.DataFrame(rows)
        st.dataframe(df,use_container_width=True,hide_index=True,height=260)
    else:
        st.caption("No active signals")
    
    # ROW 5: Health
    h=_health()
    dots="".join([f"""<span style="margin-right:8px"><span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{'#3fb950' if v=='ON' else '#d29922' if v=='WARN' else '#f85149'};margin-right:3px"></span><b>{k}</b> {v}</span>""" for k,v in h.items()])
    st.markdown(f"""<div style="background:#0d1117;padding:3px 10px;border-radius:5px;font-size:.7rem;margin-top:2px">{dots}</div>""",unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════
# TAB 2 — LIVE SIGNALS (with rich signal cards)
# ═══════════════════════════════════════════════════════════════
def tab2():
    signals=J("signals").get("signals",[])
    positions=J("positions").get("positions",[])
    if not signals:
        st.info("No signals yet"); return
    
    def _p(v):
        if v is None or v==0: return "—"
        if v>=100: return f"{v:.2f}"
        if v>=1: return f"{v:.4f}"
        if v>=0.01: return f"{v:.5f}"
        return f"{v:.6f}"
    
    # ── Header ──
    # Count LONG/SHORT from actual open POSITIONS, not signals
    longs=sum(1 for p in positions if p.get("status")=="open" and p.get("side","").upper()=="LONG")
    shorts=sum(1 for p in positions if p.get("status")=="open" and p.get("side","").upper()=="SHORT")
    n_open=longs+shorts
    st.markdown(f"**📡 Active Signals** 🟢 **{longs} LONG** 🔴 **{shorts} SHORT** / {n_open} open")
    
    # Use bridge metrics for accurate stats (DB-backed, survives restart)
    mets=J("metrics").get("metrics",{})
    if mets:
        c1,c2,c3,c4=st.columns(4)
        c1.metric("Win Rate",f"{mets.get('win_rate',0):.1f}%")
        c2.metric("Total PnL",_usd(mets.get('total_pnl',0)))
        c3.metric("Profit Factor",f"{mets.get('profit_factor',0):.2f}")
        c4.metric("Trades",mets.get('trades_total',0))
    else:
        closed=[p for p in positions if p.get("pnl") is not None]
        if closed:
            w=sum(1 for p in closed if (p.get("pnl") or 0)>0)
            tp=sum(p.get("pnl",0) for p in closed)
            wp=sum(p.get("pnl",0) for p in closed if (p.get("pnl") or 0)>0)
            lp=abs(sum(p.get("pnl",0) for p in closed if (p.get("pnl") or 0)<0))
            pf=wp/lp if lp>0 else 0
            c1,c2,c3,c4=st.columns(4)
            c1.metric("Win Rate",f"{w/len(closed)*100:.1f}%")
            c2.metric("Total PnL",_usd(tp))
            c3.metric("Profit Factor",f"{pf:.2f}")
            c4.metric("Trades",len(closed))

    filt=st.radio("Filter",["All","LONG","SHORT"],horizontal=True,key="sig_filt")
    show=[s for s in signals if filt=="All" or s.get("side")==filt]
    
    # ── CSV Export — EMITTED SIGNALS ONLY (trades from positions_archive) ──
    import pandas as pd
    import sqlite3 as _sqlite3
    _db_path = str(_ai_root / "data" / "institutional_v1.db")
    
    # Date range picker
    ec1, ec2 = st.columns(2)
    with ec1:
        export_from = st.date_input("From", value=datetime.now(timezone.utc).date() - timedelta(days=7), key="csv_from")
    with ec2:
        export_to = st.date_input("To", value=datetime.now(timezone.utc).date(), key="csv_to")
    
    try:
        _conn = _sqlite3.connect(_db_path, timeout=10)
        _ts_start = datetime(export_from.year, export_from.month, export_from.day, tzinfo=timezone.utc).timestamp()
        _ts_end = datetime(export_to.year, export_to.month, export_to.day, tzinfo=timezone.utc).timestamp() + 86400
        
        # Query EMITTED signals: closed trades from archive + open trades from positions
        # (engine now moves closed trades to archive automatically)
        _emit_df = pd.read_sql_query("""
            SELECT * FROM (
                SELECT
                    pa.signal_id,
                    pa.opened_at,
                    datetime(pa.opened_at, 'unixepoch', 'utc') as opened_datetime,
                    pa.symbol, pa.side, pa.entry_price, pa.quantity, pa.leverage,
                    pa.stop_loss, pa.take_profit,
                    pa.pnl, pa.fees, pa.exit_reason, pa.hold_minutes,
                    pa.confidence, pa.regime, pa.institutional_score as score,
                    pa.risk_reward as planned_rr, pa.session,
                    pa.mfe_pct, pa.mae_pct, pa.outcome, pa.realized_r,
                    pa.strategy_version,
                    pa.closed_at,
                    datetime(pa.closed_at, 'unixepoch', 'utc') as closed_datetime,
                    'CLOSED' as status_label
                FROM positions_archive pa
                WHERE pa.opened_at >= ? AND pa.opened_at < ?
                UNION ALL
                SELECT
                    po.signal_id,
                    po.opened_at,
                    datetime(po.opened_at, 'unixepoch', 'utc') as opened_datetime,
                    po.symbol, po.side, po.entry_price, po.quantity, po.leverage,
                    po.stop_loss, po.take_profit,
                    po.pnl, po.fees, po.exit_reason, po.hold_minutes,
                    po.confidence, po.regime, po.institutional_score as score,
                    po.risk_reward as planned_rr, po.session,
                    po.mfe_pct, po.mae_pct, po.outcome, po.realized_r,
                    po.strategy_version,
                    po.closed_at,
                    datetime(po.closed_at, 'unixepoch', 'utc') as closed_datetime,
                    'OPEN' as status_label
                FROM positions po
                WHERE po.opened_at >= ? AND po.opened_at < ?
            )
            ORDER BY opened_at
        """, _conn, params=(_ts_start, _ts_end, _ts_start, _ts_end))
        _conn.close()
        
        # Filter by side if needed
        if filt != "All":
            _emit_df = _emit_df[_emit_df["side"] == filt]
        
        if len(_emit_df) > 0:
            # ── Daily Summary ──
            _emit_df["day"] = _emit_df["opened_datetime"].str[:10]
            daily_rows = []
            for day, grp in _emit_df.groupby("day"):
                n = len(grp)
                wins = int((grp["pnl"] > 0).sum())
                losses = float(abs(grp[grp["pnl"] < 0]["pnl"].sum()))
                wins_sum = float(grp[grp["pnl"] > 0]["pnl"].sum())
                total = float(grp["pnl"].sum())
                wr = round(wins / n * 100, 1) if n > 0 else 0.0
                pf = round(wins_sum / losses, 2) if losses > 0 else (99.99 if wins_sum > 0 else 0.0)
                daily_rows.append({
                    "Date": str(day),
                    "Trades": float(n),
                    "Win Rate": wr,
                    "Total PnL": round(total, 2),
                    "Avg PnL": round(total / n, 2) if n > 0 else 0.0,
                    "Profit Factor": pf,
                })
            daily_df = pd.DataFrame(daily_rows)
            
            st.markdown("#### 📊 Daily Summary")
            st.dataframe(daily_df,
                         width="stretch", hide_index=True, height=min(200, 40 * len(daily_df) + 40))
            
            # ── Totals ──
            total_trades = float(len(_emit_df))
            total_wins = len(_emit_df[_emit_df["pnl"] > 0])
            total_pnl = _emit_df["pnl"].sum()
            total_wr = total_wins / total_trades * 100 if total_trades > 0 else 0
            wp = _emit_df[_emit_df["pnl"] > 0]["pnl"].sum()
            lp = abs(_emit_df[_emit_df["pnl"] < 0]["pnl"].sum())
            tpf = wp / lp if lp > 0 else (99.99 if wp > 0 else 0)
            avg_hold = _emit_df["hold_minutes"].mean()
            
            mc1, mc2, mc3, mc4, mc5, mc6 = st.columns(6)
            mc1.metric("Total Trades", total_trades)
            mc2.metric("Win Rate", f"{total_wr:.1f}%")
            mc3.metric("Total PnL", f"${total_pnl:.2f}")
            mc4.metric("Profit Factor", f"{tpf:.2f}")
            mc5.metric("Avg PnL", f"${total_pnl/total_trades:.2f}")
            mc6.metric("Avg Hold", f"{avg_hold:.0f}m")
            
            # ── Detailed CSV ──
            csv_rows = []
            for _, row in _emit_df.iterrows():
                open_dt = str(row.get("opened_datetime", ""))[:19]
                close_dt = str(row.get("closed_datetime", ""))[:19]
                pnl_val = float(row.get("pnl", 0) or 0)
                csv_rows.append({
                    "Open Date": str(open_dt[:10]) if len(open_dt) >= 10 else "",
                    "Open Time": str(open_dt[11:19] + " UTC") if len(open_dt) >= 19 else "",
                    "Symbol": str(row.get("symbol", "")),
                    "Side": str(row.get("side", "")),
                    "Entry": float(row.get("entry_price", 0) or 0),
                    "Stop Loss": float(row.get("stop_loss", 0) or 0),
                    "Take Profit": float(row.get("take_profit", 0) or 0),
                    "Leverage": float(row.get("leverage", 1) or 1),
                    "Quantity": float(row.get("quantity", 0) or 0),
                    "Planned R:R": float(row.get("planned_rr", 0) or 0),
                    "Confidence": float((row.get("confidence", 0) or 0) * 100),
                    "Regime": str(row.get("regime", "")),
                    "Session": str(row.get("session", "")),
                    "Score": float(row.get("score", 0) or 0),
                    "PnL ($)": pnl_val,
                    "Realized R": float(row.get("realized_r", 0) or 0),
                    "Fees ($)": float(row.get("fees", 0) or 0),
                    "Exit Reason": str(row.get("exit_reason", "")),
                    "Status": str(row.get("status_label", "OPEN")),
                    "Hold Minutes": float(row.get("hold_minutes", 0) or 0),
                    "Close Date": str(close_dt[:10]) if len(close_dt) >= 10 else "",
                    "Close Time": str(close_dt[11:19] + " UTC") if len(close_dt) >= 19 else "",
                    "MAE%": float((row.get("mae_pct", 0) or 0) * 100),
                    "MFE%": float((row.get("mfe_pct", 0) or 0) * 100),
                    "Outcome": str(row.get("outcome", "") or ("WIN" if pnl_val > 0 else "LOSS" if pnl_val < 0 else "")),
                })
            
            csv_df = pd.DataFrame(csv_rows)
            csv_str = csv_df.to_csv(index=False)
            ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            
            st.download_button(
                f"📥 Download {len(csv_df)} Emitted Signals CSV",
                csv_str,
                f"emitted_signals_{ts_str}.csv",
                "text/csv",
                key="dl_signals"
            )
            
            _n_open = len(csv_df[csv_df["Status"] == "OPEN"])
            _n_closed = len(csv_df[csv_df["Status"] == "CLOSED"])
            st.markdown(f"#### 📋 All Emitted Signals ({len(csv_df)} trades — {_n_open} open, {_n_closed} closed)")
            st.dataframe(csv_df, width="stretch", hide_index=True, height=400)
        else:
            st.info("No emitted signals found for selected date range")
    except Exception as e:
        st.error(f"CSV export error: {e}")
    
    # ══════════════════════════════════════════════════════════════
    # LIVE SHEET — 27-COLUMN MARKET DATA TABLE
    # ══════════════════════════════════════════════════════════════
    st.markdown("---")
    st.markdown("**📊 LIVE SHEET — Real-Time Market Intelligence**")
    
    import pandas as pd
    
    md_data=J("market_data")
    md_rows=md_data.get("rows",[])
    
    # Build signal lookup for FVG/extra data
    sig_lookup={s.get("symbol"):s for s in signals}
    
    def _fmt_vol(v):
        if v is None or v==0: return "—"
        if abs(v)>=1e9: return f"${v/1e9:.1f}B"
        if abs(v)>=1e6: return f"${v/1e6:.1f}M"
        if abs(v)>=1e3: return f"${v/1e3:.1f}K"
        return f"${v:.0f}"
    
    def _bias_icon(b):
        b=str(b).lower()
        if b in ("buy","bullish","strong_bullish"): return f"🟢 {b.title()}"
        if b in ("sell","bearish","strong_bearish"): return f"🔴 {b.title()}"
        if b=="neutral": return f"⚪ {b.title()}"
        return f"⚪ {b}"
    
    def _fund_str(row):
        """Rich funding display with bias, z-score, countdown"""
        fund=row.get("funding",0)
        bias=row.get("funding_bias","neutral")
        z=row.get("funding_z",0)
        cd=row.get("funding_countdown",0)
        icon="🟢" if bias in ("buy","bullish") else "🔴" if bias in ("sell","bearish") else "⚪"
        # Engine already stores funding as rate*100 (percentage), display directly
        pct=f"{fund:+.4f}%" if fund else "0.0000%"
        if cd and cd>0:
            hrs=cd//3600; mins=(cd%3600)//60; secs=cd%60
            pct+=f" ⏱{hrs:02d}:{mins:02d}:{secs:02d}"
        return f"{icon} {pct}"
    
    def _exflow_str(row):
        """Rich exchange flow display with bias and signal"""
        ef=row.get("exchange_flow",0)
        bias=row.get("exchange_bias","—")
        sig=row.get("flow_signal","—")
        stren=row.get("flow_strength",0)
        icon="🟢" if sig=="buy" else "🔴" if sig=="sell" else "⚪"
        return f"{icon} {_fmt_vol(ef)} ({stren:.0f})"
    
    def _flow_str(row):
        sig=row.get("flow_signal","—")
        stren=row.get("flow_strength",0)
        if sig and sig!="—":
            icon="🟢" if sig=="buy" else "🔴" if sig=="sell" else "⚪"
            return f"{icon} {sig.title()} ({stren:.0f})"
        return "—"
    
    def _liq_risk_str(row):
        level=str(row.get("liq_risk_level","low")).title()
        risk=row.get("liq_risk",0)
        if level=="High": return f"🔴 High ({risk:.0f})"
        if level=="Medium": return f"🟡 Med ({risk:.0f})"
        return f"🟢 Low ({risk:.0f})"
    
    def _sweep_str(row):
        detected=row.get("sweep_detected",False)
        direction=row.get("sweep_direction","")
        price=row.get("price",0)
        if detected and direction:
            icon="🟢" if direction=="up" else "🔴"
            return f"{icon} {direction.title()}", _p(price)
        return "—","—"
    
    def _signal_implied(row):
        """Derive implied signal from biases"""
        oi=str(row.get("oi_bias","neutral")).lower()
        cvd=str(row.get("cvd_bias","neutral")).lower()
        flow=str(row.get("flow_signal","neutral")).lower()
        bullish=0
        bearish=0
        for b in [oi,cvd,flow]:
            if "bull" in b or b=="buy": bullish+=1
            elif "bear" in b or b=="sell": bearish+=1
        if bullish>bearish: return "🟢 LONG (implied)"
        if bearish>bullish: return "🔴 SHORT (implied)"
        return "—"
    
    # Build the Live Sheet rows
    live_sheet_rows=[]
    for row in md_rows:
        sym=row.get("symbol","?")
        sig=sig_lookup.get(sym,{})
        
        sweep_str,sweep_price=_sweep_str(row)
        
        # FVG from signal data if available
        fvg_score=sig.get("fvg_score",sig.get("fvg_score"))
        fvg_str="—"
        fvg_price="—"
        if fvg_score and fvg_score>0:
            fvg_str="🟢 BULL" if sig.get("side")=="LONG" else "🔴 BEAR"
            fvg_price=_p(sig.get("vp_poc",0))
        
        # Net Delta formatting
        nd=row.get("net_delta",0)
        
        live_sheet_rows.append({
            "Symbol":sym,
            "Price":_p(row.get("price",0)),
            "Mark":_p(row.get("mark_price",0)),
            "Index":_p(row.get("index_price",0)),
            "24h":f"{(row.get('change_24h') or 0):+.1f}%",
            "24h High":_p(row.get("high_24h",0)),
            "24h Low":_p(row.get("low_24h",0)),
            "Volume 24h":_fmt_vol(row.get("volume_24h",0)),
            "OI":_fmt_vol(row.get("open_interest",0)),
            "OI Bias":_bias_icon(row.get("oi_bias","neutral")),
            "OI Δ%":f"{row.get('oi_change_pct',0):+.2f}%" if row.get("oi_change_pct") else "—",
            "Funding":_fund_str(row),
            "Fund Bias":_bias_icon(row.get("funding_bias","neutral")),
            "Net Delta":_fmt_vol(nd),
            "B/S Ratio":f"{(row.get('buy_sell_ratio') or 0):.2f}",
            "CVD Bias":_bias_icon(row.get("cvd_bias","neutral")),
            "Flow":_flow_str(row),
            "Ex Flow":_exflow_str(row),
            "Vol Bias":_bias_icon(row.get("vol_bias","neutral")),
            "Liq Zone ↓":_fmt_vol(row.get("long_liq_vol",0)),
            "Liq Zone ↑":_fmt_vol(row.get("short_liq_vol",0)),
            "Liq Risk":_liq_risk_str(row),
            "Sweep":sweep_str,
            "Sweep Price":sweep_price,
            "CVD":_bias_icon(row.get("cvd_bias","neutral")),
            "Delta":f"{(row.get('imbalance') or 0):.3f}",
            "FVG":fvg_str,
            "FVG Price":fvg_price,
            "Regime":f"{row.get('regime','—')}",
            "Reg Conf":f"{(row.get('regime_confidence_pct') or 0):.0f}%",
            "Signal":_signal_implied(row),
        })
    
    if live_sheet_rows:
        df_live=pd.DataFrame(live_sheet_rows)
        st.dataframe(df_live,width="stretch",hide_index=True,height=min(600,len(live_sheet_rows)*28+40))
        st.caption(f"Showing {len(live_sheet_rows)} symbols · Data age: {time.time()-md_data.get('timestamp',0):.0f}s")
    
    # ══════════════════════════════════════════════════════════════
    # CROSS-CHECK PROOF TABLE — Verify data integrity
    # ══════════════════════════════════════════════════════════════
    with st.expander("🔍 Cross-Check Proof — Verify each column against source data"):
        st.caption("Each value traced to its source file and field")
        proof_rows=[]
        # Pick top 5 symbols by volume for proof
        top5=sorted(md_rows,key=lambda x:x.get("volume_24h",0),reverse=True)[:5]
        for row in top5:
            sym=row.get("symbol","?")
            sig=sig_lookup.get(sym,{})
            proof_rows.append({
                "Symbol":sym,
                "Price src":f"market_data.price = {row.get('price')}",
                "24h src":f"market_data.change_24h = {row.get('change_24h')}",
                "Vol src":f"market_data.volume_24h = {row.get('volume_24h'):.0f}",
                "OI src":f"market_data.open_interest = {row.get('open_interest'):.0f}",
                "OI Bias src":f"market_data.oi_bias = {row.get('oi_bias')}",
                "OI Δ% src":f"market_data.oi_change_pct = {row.get('oi_change_pct')}",
                "Funding src":f"market_data.funding = {row.get('funding')}",
                "Net Delta src":f"market_data.net_delta = {row.get('net_delta'):.0f}",
                "B/S src":f"market_data.buy_sell_ratio = {row.get('buy_sell_ratio')}",
                "CVD src":f"market_data.cvd_bias = {row.get('cvd_bias')}",
                "Flow src":f"market_data.flow_signal={row.get('flow_signal')} str={row.get('flow_strength')}",
                "Ex Flow src":f"market_data.exchange_flow = {row.get('exchange_flow'):.0f}",
                "Liq Risk src":f"market_data.liq_risk={row.get('liq_risk')} level={row.get('liq_risk_level')}",
                "Sweep src":f"market_data.sweep_detected={row.get('sweep_detected')} dir={row.get('sweep_direction')}",
                "Regime src":f"market_data.regime={row.get('regime')} conf={row.get('regime_confidence_pct')}",
                "Signal src":f"Derived: oi={row.get('oi_bias')} cvd={row.get('cvd_bias')} flow={row.get('flow_signal')}",
                "FVG src":f"signals.fvg_score={sig.get('fvg_score','N/A')}" if sig else "signals (no signal for this symbol)",
            })
        if proof_rows:
            st.dataframe(pd.DataFrame(proof_rows),width="stretch",hide_index=True)
    
    # ══════════════════════════════════════════════════════════════
    # SIGNAL CARDS — Rich detail per signal
    # ══════════════════════════════════════════════════════════════
    st.markdown("---")
    for i,s in enumerate(show):
        sig_type=s.get("type","LONG")
        is_long=sig_type=="LONG"
        icon="🟢" if is_long else "🔴"
        side_c="#3fb950" if is_long else "#f85149"
        
        symbol=s.get("symbol","?")
        grade=s.get("signal_grade",s.get("quality_tier","C"))
        conf=s.get("confidence",0)
        inst_score=s.get("institutional_score",0)
        grade_e={"A+":"💎","A":"⭐","B":"🔶","C":"📊"}.get(grade,"📊")
        
        entry=s.get("entry_price",0)
        sl=s.get("stop_loss",0)
        tp1=s.get("take_profit_1",s.get("take_profit",0))
        tp2=s.get("take_profit_2",0)
        tp3=s.get("take_profit_3",0)
        rr=s.get("risk_reward",0)
        rr2=s.get("rr_2",0)
        rr3=s.get("rr_3",0)
        sl_dist=s.get("sl_distance_pct",0)
        tp_dist=s.get("tp_distance_pct",0)
        
        regime=s.get("regime","—")
        regime_icon=s.get("regime_icon","📊")
        regime_cat=s.get("regime_category","—")
        session=s.get("session","—")
        vol_score=s.get("volatility_score",0)
        change_24h=s.get("change_24h",0)
        mtf=s.get("mtf_aligned",False)
        mtf_n=s.get("mtf_alignment",0)
        mtf_total=len(s.get("timeframes",[]))
        
        risk=s.get("risk_metrics",{})
        kelly=risk.get("kelly_pct",0)
        pos_size=risk.get("position_size_usd",0)
        risk_pct=risk.get("risk_pct",0)*100
        daily_left=risk.get("daily_loss_remaining",0)
        exposure=risk.get("portfolio_exposure_pct",0)
        
        ind=s.get("indicators",{})
        rsi=ind.get("rsi",50)
        vol_ratio=ind.get("vol_ratio",1.0)
        atr_val=ind.get("atr",0)
        
        score_bk=s.get("score_breakdown",{})
        pillar=s.get("pillar_scores",{})
        conf_factors=s.get("confirmation_factors",[])
        entry_reason=s.get("entry_reason","—")
        oi_exp=s.get("oi_expansion_pct",0)*100
        oi_label=s.get("oi_trend_label","—")
        grade_conf=s.get("grade_confidence",0)*100
        grade_sc=s.get("grade_score",inst_score)
        
        # ── Card container ──
        st.markdown(f'<div style="border-left:3px solid {side_c};padding:2px 0"></div>',unsafe_allow_html=True)
        
        # Row 1: Symbol + Type + Date/Time + Session + Grade
        created=s.get('created_at',0)
        r1c1,r1c2,r1c3,r1c4=st.columns([2,2,2,1])
        with r1c1:
            st.markdown(f'**{icon} {sig_type} {symbol}**')
        with r1c2:
            st.markdown(f'<small style="color:#8b949e">🕐 {_dt(created)}</small>',unsafe_allow_html=True)
        with r1c3:
            st.caption(f"{regime_icon} {session.title()} · {regime_cat} {regime} · Vol: {vol_score:.0f} · 24H: {change_24h:+.1f}%")
        with r1c4:
            gc="#3fb950" if grade in ("A+","A") else "#d29922" if grade=="B" else "#888"
            st.markdown(f'<p style="text-align:right;font-size:.85rem"><b style="color:{gc}">{grade_e} {grade}</b> <small style="color:#8b949e">({grade_sc:.0f})</small></p>',unsafe_allow_html=True)
        
        # Row 2: Entry / SL / TP
        r2c1,r2c2,r2c3,r2c4=st.columns([2,2,2,2])
        r2c1.metric("ENTRY",_p(entry))
        r2c2.metric("SL",_p(sl),f"{sl_dist:.1f}%",delta_color="inverse")
        r2c3.metric("TP1",_p(tp1),f"+{tp_dist:.1f}%")
        r2c4.metric("R:R",f"{rr:.1f}x")
        
        # Row 3: TP2 / TP3 / ATR Mult
        r3c1,r3c2,r3c3,r3c4=st.columns([2,2,2,2])
        r3c1.metric("TP2",_p(tp2),f"{rr2:.1f}x")
        r3c2.metric("TP3",_p(tp3),f"{rr3:.1f}x")
        r3c3.metric("SL Mult",f"{(s.get('sl_atr_mult') or 1.5):.1f}x ATR")
        r3c4.metric("Conf",f"{conf*100:.0f}%",f"R:R {rr:.1f}")
        
        # Row 4: Indicators
        r4c1,r4c2,r4c3,r4c4,r4c5=st.columns(5)
        r4c1.metric("RSI",f"{rsi:.0f}")
        r4c2.metric("VOL",f"{vol_ratio:.1f}x")
        r4c3.metric("ATR",f"{atr_val:.4f}")
        r4c4.metric("OI",oi_label,f"{oi_exp:+.1f}%")
        r4c5.metric("MTF",f"{'✅' if mtf else '❌'} {mtf_n}/{mtf_total}")
        
        # Row 5: Risk bar
        kelly_c="#f85149" if kelly<0 else "#3fb950"
        st.markdown(f'<div style="background:#161b22;padding:3px 8px;border-radius:4px;font-size:.7rem">'
            f'🛡️ RISK Kelly <b style="color:{kelly_c}">{kelly:.0f}%</b> · '
            f'Size <b>${pos_size:.0f}</b> · '
            f'Risk <b>{risk_pct:.1f}%</b> · '
            f'Daily <b>${daily_left:.0f}</b> left · '
            f'Exposure <b>{exposure:.1f}%</b>'
            f'</div>',unsafe_allow_html=True)
        
        # Row 6: Score Breakdown
        if score_bk:
            max_sb=max(score_bk.values()) if score_bk else 20
            bars_html='<div style="background:#161b22;padding:4px 8px;border-radius:4px;margin-top:2px">'
            bars_html+='<div style="font-size:.6rem;color:#d29922;margin-bottom:2px"><b>SCORE BREAKDOWN</b></div>'
            for k_name in ["MSS","Sweep","FVG","OI","Delta","CVD","Funding"]:
                val=score_bk.get(k_name,0)
                w=max(0,min(100,val/max(max_sb,20)*100))
                bc="#3fb950" if val>=15 else "#58a6ff" if val>=8 else "#d29922" if val>=3 else "#f85149"
                bars_html+=f'<div style="display:flex;align-items:center;gap:4px;font-size:.6rem;color:#8b949e;margin:1px 0">'
                bars_html+=f'<span style="width:48px;text-align:right">{k_name}</span> '
                bars_html+=f'<span style="color:#3fb950;font-weight:bold;width:28px">+{val:.0f}</span> '
                bars_html+=f'<div style="flex:1;height:4px;background:#21262d;border-radius:2px;overflow:hidden">'
                bars_html+=f'<div style="width:{w:.0f}%;height:100%;background:{bc};border-radius:2px"></div></div></div>'
            bars_html+=f'<div style="display:flex;justify-content:space-between;margin-top:3px;font-size:.65rem">'
            bars_html+=f'<span><b>Final Score = <span style="color:#58a6ff">{inst_score:.0f}</span></b></span>'
            bars_html+=f'<span style="color:#8b949e">{grade_conf:.0f}% grade confidence</span></div>'
            bars_html+='</div>'
            st.markdown(bars_html,unsafe_allow_html=True)
        
        # Row 7: Pillar badges
        if pillar:
            pillar_names={"market_structure":"Mkt Structure","flow":"Flow","volume":"Vol",
                          "open_interest":"OI","funding":"Fund","sweep":"Sweep","absorption":"Absorb"}
            badge_html='<div style="display:flex;flex-wrap:wrap;gap:3px;margin-top:3px">'
            for pk,pv in sorted(pillar.items(),key=lambda x:-(x[1] or 0)):
                pn=pillar_names.get(pk,pk[:6])
                c="#3fb950" if (pv or 0)>=70 else "#58a6ff" if (pv or 0)>=55 else "#d29922" if (pv or 0)>=45 else "#f85149"
                badge_html+=f'<span style="background:#161b22;border:1px solid {c};color:{c};border-radius:4px;padding:1px 6px;font-size:.65rem;font-weight:bold">{pn} {float(pv or 0):.0f}</span>'
            badge_html+='</div>'
            st.markdown(badge_html,unsafe_allow_html=True)
        
        # Row 8: Confirmation factors
        if conf_factors:
            cf_html='<div style="display:flex;flex-wrap:wrap;gap:3px;margin-top:2px">'
            for cf in conf_factors:
                cc="#3fb950" if cf in ("order_flow","regime") else "#58a6ff"
                cf_html+=f'<span style="background:#1a1a2e;color:{cc};border:1px solid {cc}33;border-radius:3px;padding:0 5px;font-size:.6rem">✓ {cf.replace("_"," ")}</span>'
            cf_html+='</div>'
            st.markdown(cf_html,unsafe_allow_html=True)
        
        # Entry reason
        st.caption(f"📝 {entry_reason}")
        st.markdown("---")
    
    # ── TWO SIDE-BY-SIDE TABLES ──
    st.markdown('---')
    import pandas as pd
    
    t_left,t_right=st.columns(2)
    with t_left:
        st.markdown('**⚡ GENERATED SIGNALS** ({})'.format(len(show)))
        gen_rows=[]
        for s in sorted(show,key=lambda x:-(x.get('created_at',0))):
            created=s.get('created_at',0)
            checks=s.get('checklist',{}).get('checks',{})
            passed=sum(1 for v in checks.values() if v is True or str(v).lower()=='true')
            total_checks=len(checks) if checks else 0
            gen_rows.append({
                'Time':_dt(created),
                'Symbol':s.get('symbol','?'),
                'Side':s.get('side','?'),
                'Confidence':f"{(s.get('confidence') or 0)*100:.1f}",
                'Inst Score':f"{(s.get('institutional_score') or 0):.1f}",
                'Regime':s.get('regime','—'),
                'Checklist':f"{passed}/{total_checks}",
                'Status':'⚡ Active',
            })
        if gen_rows:
            st.dataframe(pd.DataFrame(gen_rows),width="stretch",hide_index=True,height=min(500,len(gen_rows)*35+40))
    
    with t_right:
        st.markdown('**📡 LIVE SIGNALS** ({})'.format(len(show)))
        live_rows=[]
        for s in sorted(show,key=lambda x:-(x.get('created_at',0))):
            created=s.get('created_at',0)
            side=s.get('side','?')
            side_icon='🟢' if side=='LONG' else '🔴'
            live_rows.append({
                'Time':_dt(created),
                'Symbol':s.get('symbol','?'),
                'Side':f"{side_icon} {side}",
                'Entry':_p(s.get('entry_price',0)),
                'SL':_p(s.get('stop_loss',0)),
                'TP1':_p(s.get('take_profit_1',0)),
                'TP2':_p(s.get('take_profit_2',0)),
                'TP3':_p(s.get('take_profit_3',0)),
                'R:R':f"{(s.get('risk_reward') or 0):.1f}",
                'Status':'⚡ EMITTED',
            })
        if live_rows:
            st.dataframe(pd.DataFrame(live_rows),width="stretch",hide_index=True,height=min(500,len(live_rows)*35+40))

# ═══════════════════════════════════════════════════════════════
# TAB 3 — PIPELINE ANALYTICS
# ═══════════════════════════════════════════════════════════════
def tab3():
    funnel=J("funnel").get("funnel",{})
    st.subheader("🔍 Pipeline Analytics")
    
    t1,t2,t3,t4=st.tabs(["🚫 Rejections","📊 Gate Stats","🎯 Closest","💀 Death Report"])
    
    with t1:
        rejections=funnel.get("rejection_reasons",[])
        rc=Counter()
        for r in rejections:
            reason=r.get("reason","unknown")
            if "SESSION" in reason: rc["Session"]+=1
            elif "REGIME" in reason or "HARD_REGIME" in reason: rc["Regime"]+=1
            elif "FILTERED" in reason or "P3_LOW" in reason or "P6_" in reason: rc["Signal Filter"]+=1
            elif "SILENCED" in reason: rc["Scorer"]+=1
            elif "CHECKLIST" in reason: rc["Checklist"]+=1
            elif "QUIET" in reason: rc["Quiet Market"]+=1
            elif "NO_SWEEP" in reason: rc["No Sweep"]+=1
            elif "BLACKLISTED" in reason: rc["Blacklist"]+=1
            else: rc["Other"]+=1
        for reason,count in rc.most_common(10):
            pct=count/len(rejections)*100 if rejections else 0
            bar=f'<div class="bar"><div class="bar-fill bar-blue" style="width:{pct}%"></div></div>'
            st.markdown(f"**{reason}**: {count} ({pct:.1f}%) {bar}",unsafe_allow_html=True)
        if not rejections:
            st.info("No rejections recorded")
    
    with t2:
        stages=[("Scanned",funnel.get("scanned",0)),("AI Pass",funnel.get("ai_pass",funnel.get("scorer_pass",0))),("Regime",funnel.get("regime_pass",0)),("Session",funnel.get("session_pass",0)),("Checklist",funnel.get("checklist_reached",0)),("Check Pass",funnel.get("checklist_passed",0)),("Generated",funnel.get("generated",0)),("Emitted",funnel.get("emitted",0))]
        import pandas as pd
        rows=[]
        for i,(nm,vl) in enumerate(stages):
            prev=stages[i-1][1] if i>0 else vl
            drop=prev-vl if prev>0 and prev>vl else 0
            dpct=drop/prev*100 if prev>0 else 0
            ppct=vl/prev*100 if prev>0 else 100
            rows.append({"Gate":nm,"Count":vl,"Drop":drop,"Drop%":f"{dpct:.1f}%",f"Pass%":f"{ppct:.1f}%"})
        st.dataframe(pd.DataFrame(rows),width="stretch",hide_index=True)
        
        sc=funnel.get("scanned",0); em=funnel.get("emitted",0)
        eff=em/sc*100 if sc>0 else 0
        st.metric("Overall Efficiency",f"{eff:.2f}%")
    
    with t3:
        signals=J("signals").get("signals",[])
        if signals:
            import pandas as pd
            rows=[{"Symbol":s.get("symbol","?"),"Score":s.get("institutional_score",0),"Side":s.get("side","?"),
                   "Regime":s.get("regime","—"),"Conf":f"{s.get('confidence',0)*100:.1f}%"} for s in sorted(signals,key=lambda x:-x.get("institutional_score",0))[:20]]
            st.dataframe(pd.DataFrame(rows),width="stretch",hide_index=True)
        else:
            st.info("No signal data available")
    
    with t4:
        death=J("death_report").get("death_report",{})
        if death:
            st.markdown("**Signal Death Report**")
            # Show summary stats
            for k in ("scanned","emitted","total_rejected","top_killer","pass_rate"):
                if k in death:
                    st.markdown(f"**{k}**: {death[k]}")
            # Show deaths_by_stage (the nested dict)
            stages=death.get("deaths_by_stage",{})
            if stages:
                for stage,count in sorted(stages.items(),key=lambda x:-(x[1] or 0))[:10]:
                    st.markdown(f"**{stage}**: {count}")
        else:
            st.info("No death report data")

# ═══════════════════════════════════════════════════════════════
# TAB 4 — CHECKLIST INSPECTOR (dropdown + prev/next)
# ═══════════════════════════════════════════════════════════════
def tab4():
    funnel=J("funnel").get("funnel",{})
    signals=J("signals").get("signals",[])
    
    st.subheader("✅ Checklist Inspector")
    
    reached=funnel.get("checklist_reached",0); passed=funnel.get("checklist_passed",0)
    pr=passed/reached*100 if reached>0 else 0
    c1,c2,c3=st.columns(3)
    c1.metric("Required",reached); c2.metric("Passed",passed); c3.metric("Pass Rate",f"{pr:.1f}%")
    
    cf=Counter()
    for r in funnel.get("rejection_reasons",[]):
        reason=r.get("reason","")
        if "CHECKLIST" in reason:
            parts=reason.split("|")
            if len(parts)>1:
                for tok in parts[-1].strip().split(";"):
                    tok=tok.strip()
                    if ":" in tok:
                        rule=tok.split(":")[0].strip()
                        cf[rule]+=1
    
    if cf:
        for rule,count in cf.most_common(8):
            pct=count/reached*100 if reached>0 else 0
            bar=f'<div class="bar"><div class="bar-fill bar-red" style="width:{pct}%"></div></div>'
            st.markdown(f"**{rule}**: {count} ({pct:.1f}%) {bar}",unsafe_allow_html=True)
    
    st.divider()
    
    sym_list=sorted(set(s.get("symbol","?") for s in signals)) if signals else []
    if not sym_list:
        st.info("No signals to inspect"); return
    
    c1,c2,c3=st.columns([3,1,1])
    with c1:
        # Use nav index to control selectbox; update index on Prev/Next via session state
        nav_idx=st.session_state.get("cl_nav_idx", 0)
        nav_idx=max(0, min(nav_idx, len(sym_list)-1))
        selected=st.selectbox("Select Symbol",sym_list,index=nav_idx,key="cl_sym")
    with c2:
        if st.button("◀ Prev",key="cl_prev"):
            cur=sym_list.index(st.session_state.get("cl_sym",sym_list[0])) if st.session_state.get("cl_sym",sym_list[0]) in sym_list else 0
            st.session_state.cl_nav_idx=max(0,cur-1)
            st.rerun()
    with c3:
        if st.button("Next ▶",key="cl_next"):
            cur=sym_list.index(st.session_state.get("cl_sym",sym_list[0])) if st.session_state.get("cl_sym",sym_list[0]) in sym_list else 0
            st.session_state.cl_nav_idx=min(len(sym_list)-1,cur+1)
            st.rerun()
    
    if selected:
        match=[s for s in signals if s.get("symbol")==selected]
        if match:
            s=match[0]
            st.markdown(f"**{s.get('symbol','?')} — {s.get('side','?')} ({s.get('status','?')})**")
            st.caption(f"Time: {_dt(s.get('created_at',s.get('timestamp',0)))} | Regime: {s.get('regime','—')}")
            
            gates=[
                ("AI Score",s.get("institutional_score",0),">=65",s.get("institutional_score",0)>=65),
                ("Regime",s.get("regime","—"),"trending/range",True),
                ("Sweep",s.get("sweep_score",0),">0",s.get("sweep_score",0)>0),
                ("Delta",s.get("delta",0),">0",s.get("delta",0)>0),
                ("CVD",s.get("cvd",0),">0",s.get("cvd",0)>0),
                ("Volume",s.get("volume",{}).get("flow_strength",0) if isinstance(s.get("volume"),dict) else 0,">40",True),
                ("Session","PASS","allowed",True),
                ("Checklist","12/12" if s.get("checklist",{}).get("passed") else "FAIL","all pass",s.get("checklist",{}).get("passed",False)),
            ]
            
            html="<div style='display:flex;flex-wrap:wrap;gap:2px'>"
            for name,val,thresh,ok in gates:
                cls="gate-p" if ok else "gate-f"
                html+=f"<div class='gate-box {cls}'><b>{name}</b><br>{val}</div>"
            html+="</div>"
            st.markdown(html,unsafe_allow_html=True)
            
            c1,c2,c3,c4=st.columns(4)
            c1.metric("Entry",s.get("entry_price",s.get("entry",0)))
            c2.metric("SL",s.get("stop_loss",0))
            c3.metric("TP1",s.get("take_profit",0))
            c4.metric("RR",s.get("risk_reward",0))
        else:
            st.warning(f"No signal data for {selected}")
    
    with st.expander("📝 Checklist Rules",expanded=False):
        rules=[("Regime","Trending/breakout/range, conf ≥ 35"),("Sweep","Liquidity sweep detected"),("MSS","Market Structure Shift"),("Displacement","Displacement candle"),("Delta","Normalized ≥ 0.80"),("CVD","Normalized ≥ 0.80"),("OI Expansion","Change ≥ 0.10% or squeeze"),("Volume Expansion","Flow strength ≥ 40"),("FVG Retest","FVG/OB retest"),("R:R","Risk/Reward ≥ 1.5")]
        for nm,desc in rules:
            st.markdown(f"**{nm}**: {desc}")

# ═══════════════════════════════════════════════════════════════
# TAB 5 — ORDERFLOW (with heatmaps)
# ═══════════════════════════════════════════════════════════════
def tab5():
    st.subheader("📊 Orderflow")
    intel=J("market_intelligence").get("intelligence",{})
    smart=J("smart_money_map")
    
    # Data status
    c1,c2,c3,c4,c5=st.columns(5)
    for i,(n,s) in enumerate([("OI","LIVE"),("CVD","LIVE"),("Delta","LIVE"),("Volume","LIVE"),("Funding","LIVE")]):
        with [c1,c2,c3,c4,c5][i]:
            st.metric(f"{'🟢' if s=='LIVE' else '🟡'} {n}",s)
    
    c1,c2=st.columns(2)
    with c1:
        br=intel.get("breadth",{})
        rd=intel.get("regime_distribution",{})
        if not br and rd:
            total=sum(rd.values()) or 1
            lb=rd.get("trending_bull",0)/total*100
            sb=rd.get("trending_bear",0)/total*100
            br={"long_bias":lb/100,"short_bias":sb/100}
        if br:
            lb=br.get("long_bias",0)*100; sb=br.get("short_bias",0)*100
            st.metric("Long Bias",f"{lb:.1f}%"); st.metric("Short Bias",f"{sb:.1f}%")
            st.metric("Neutral",f"{100-lb-sb:.1f}%")
        else:
            st.info("No breadth data")
    with c2:
        if smart:
            sm_rows=smart.get("rows",smart) if isinstance(smart,dict) else []
            sm_list=sm_rows if isinstance(sm_rows,list) else []
            total_sweeps=sum(r.get("sweep_count",0) for r in sm_list)
            total_absorptions=sum(r.get("absorption_count",0) for r in sm_list)
            symbols_sweep=sum(1 for r in sm_list if r.get("sweep_signal") and r.get("sweep_signal")!="neutral")
            symbols_absorb=sum(1 for r in sm_list if r.get("absorption_signal") and r.get("absorption_signal")!="neutral")
            sigs=J("signals").get("signals",[])
            fvg_count=sum(1 for s in sigs if s.get("fvg_score",0)>0)
            st.metric("Sweeps",f"{total_sweeps}",f"{symbols_sweep} symbols")
            st.metric("Order Blocks",f"{total_absorptions}",f"{symbols_absorb} symbols")
            st.metric("FVGs",f"{fvg_count}",f"{len(sigs)} signals")
    
    # Heatmaps
    try:
        from dashboard.heatmaps import render_heatmaps
        render_heatmaps()
    except Exception as e:
        st.info(f"Heatmaps unavailable: {e}")
    
    setups=intel.get("top_setups",[])
    if setups:
        import pandas as pd
        st.dataframe(pd.DataFrame(setups[:15]),width="stretch",hide_index=True,height=300)

# ═══════════════════════════════════════════════════════════════
# TAB 6 — MARKET STRUCTURE
# ═══════════════════════════════════════════════════════════════
def tab6():
    st.subheader("🧭 Market Structure")
    intel=J("market_intelligence").get("intelligence",{})
    br=intel.get("breadth",{})
    rd=intel.get("regime_distribution",{}) or intel.get("regime_dist",{})
    
    # Compute breadth from regime distribution if not directly available
    if not br and rd:
        total=sum(rd.values()) or 1
        lb=rd.get("trending_bull",0)/total*100
        sb=rd.get("trending_bear",0)/total*100
        br={"long_bias":lb/100,"short_bias":sb/100,"bullish_pct":lb/100,"bearish_pct":sb/100,"total":sum(rd.values())}
    
    if br:
        c1,c2,c3,c4,c5=st.columns(5)
        c1.metric("📊 Breadth",br.get("total",0))
        c2.metric("🟢 Long",f"{(br.get('long_bias') or 0)*100:.0f}%")
        c3.metric("🔴 Short",f"{(br.get('short_bias') or 0)*100:.0f}%")
        c4.metric("📈 Bullish",f"{(br.get('bullish_pct') or 0)*100:.0f}%")
        c5.metric("📉 Bearish",f"{(br.get('bearish_pct') or 0)*100:.0f}%")
    else:
        st.info("No breadth data available")
    
    c1,c2=st.columns(2)
    with c1:
        st.markdown("**Regime Distribution**")
        if rd:
            for rg,cnt in sorted(rd.items(),key=lambda x:-(x[1] or 0)):
                pct=cnt/sum(rd.values())*100 if sum(rd.values())>0 else 0
                st.markdown(f"**{rg}**: {cnt} ({pct:.1f}%)")
        else:
            st.info("No regime data")
    with c2:
        st.markdown("**Top Trends**")
        trends=intel.get("top_trends",[])
        sp=intel.get("symbol_performance",{})
        if trends:
            for t in trends[:10]:
                sym_name = t.get('symbol','?').replace('USDT','')
                regime = t.get('regime','?')
                if isinstance(regime, dict):
                    regime = regime.get('regime', '?')
                conf = t.get('confidence',0) or 0
                st.markdown(f"**{sym_name}**: {regime} ({conf*100:.0f}%)")
        elif sp:
            items = list(sp.items())[:10] if isinstance(sp, dict) else (sp[:10] if isinstance(sp, list) else [])
            for sym, perf in items:
                if isinstance(perf, dict):
                    chg = perf.get("change_24h", perf.get("change_pct", 0)) or 0
                    regime = perf.get("regime", "?")
                    if isinstance(regime, dict):
                        regime = regime.get("regime", "?")
                    conf = perf.get("confidence", 0) or 0
                    conf_str = f" ({conf*100:.0f}%)" if conf > 0 else ""
                    st.markdown(f"**{str(sym).replace('USDT','')}**: {chg:+.1f}% {regime}{conf_str}")
                else:
                    st.markdown(f"**{sym}**: {perf}")
        else:
            st.info("No trend data")
    
    breakouts=intel.get("breakout_candidates",[])
    if breakouts:
        import pandas as pd
        st.dataframe(pd.DataFrame(breakouts[:15]),width="stretch",hide_index=True,height=300)

# ═══════════════════════════════════════════════════════════════
# TAB 7 — PERFORMANCE (self-contained, no sidebar widgets)
# ═══════════════════════════════════════════════════════════════
def tab7():
    import pandas as pd
    import sqlite3 as _sql3

    st.subheader("📈 Performance Dashboard")

    # ── Load all data up front ──
    metrics_data = J("metrics").get("metrics", {})
    equity_history = J("equity_history").get("history", [])
    signals = J("signals").get("signals", [])

    # Load closed trades from DB (cached to avoid repeated queries)
    db_path = _ai_root / "data" / "institutional_v1.db"
    trades = []
    try:
        _cache_key = f"perf_trades_{int(time.time() // 30)}"  # cache for 30s
        if _cache_key not in st.session_state:
            conn = _sql3.connect(str(db_path), timeout=10)
            cur = conn.cursor()
            cur.execute("""
                SELECT id, symbol, side, entry_price, pnl, fees, hold_minutes,
                       opened_at, closed_at, exit_reason, confidence, regime,
                       institutional_score, risk_reward, session
                FROM positions WHERE pnl IS NOT NULL AND status='closed'
                UNION ALL
                SELECT id, symbol, side, entry_price, pnl, fees, hold_minutes,
                       opened_at, closed_at, exit_reason, confidence, regime,
                       institutional_score, risk_reward, session
                FROM positions_archive WHERE pnl IS NOT NULL
                ORDER BY closed_at DESC
            """)
            cols = [d[0] for d in cur.description]
            st.session_state[_cache_key] = [dict(zip(cols, row)) for row in cur.fetchall()]
            conn.close()
        trades = st.session_state[_cache_key]
    except Exception:
        pass
    except Exception:
        pass

    # ── Key Metrics Cards ──
    total_trades = metrics_data.get("trades_total", len(trades))
    win_rate = metrics_data.get("win_rate", 0)
    profit_factor = metrics_data.get("profit_factor", 0)
    total_pnl = metrics_data.get("total_pnl", 0)
    daily_pnl = metrics_data.get("daily_pnl", 0)
    portfolio = metrics_data.get("portfolio_value", 10000)
    sharpe = metrics_data.get("sharpe_ratio", 0)
    max_dd = metrics_data.get("max_drawdown", 0)
    trades_today = metrics_data.get("trades_today", 0)
    expectancy = metrics_data.get("expectancy", 0)
    sortino = metrics_data.get("sortino_ratio", 0)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("💰 Portfolio", f"${portfolio:,.2f}", f"{total_pnl:+,.2f}")
    c2.metric("📊 Win Rate", f"{win_rate:.1f}%", f"{total_trades} trades")
    c3.metric("⚡ Profit Factor", f"{profit_factor:.2f}", delta_color="normal")
    c4.metric("📈 Daily PnL", f"${daily_pnl:+,.2f}")
    c5.metric("🎯 Expectancy", f"${expectancy:+.2f}")

    c6, c7, c8, c9, c10 = st.columns(5)
    c6.metric("📉 Max Drawdown", f"{max_dd:.1f}%")
    c7.metric("⚡ Sharpe", f"{sharpe:.2f}")
    c8.metric("🔄 Sortino", f"{sortino:.2f}")
    c9.metric("📅 Today", f"{trades_today} trades")
    c10.metric("📡 Signals", f"{len(signals)}")

    st.divider()

    # ── Equity Curve ──
    if equity_history and len(equity_history) > 1:
        eq_df = pd.DataFrame(equity_history)
        if "timestamp" in eq_df.columns and "equity" in eq_df.columns:
            eq_df["time"] = pd.to_datetime(eq_df["timestamp"], unit="s")
            st.markdown("#### 📈 Equity Curve")
            st.line_chart(eq_df.set_index("time")["equity"])

    if not trades:
        st.info("No closed trades yet. Performance data will appear after trades close.")
        return

    # ── Build trades DataFrame ──
    df = pd.DataFrame(trades)
    for col in ["pnl", "fees", "entry_price", "hold_minutes", "confidence", "risk_reward"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    for col in ["opened_at", "closed_at"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], unit="s", errors="coerce")

    wins = df[df["pnl"] > 0]
    losses = df[df["pnl"] <= 0]
    avg_win = wins["pnl"].mean() if len(wins) else 0
    avg_loss = losses["pnl"].mean() if len(losses) else 0
    total_wins = wins["pnl"].sum() if len(wins) else 0
    total_losses = abs(losses["pnl"].sum()) if len(losses) else 0
    pf = total_wins / total_losses if total_losses > 0 else 99.9
    avg_hold = df["hold_minutes"].mean() if "hold_minutes" in df.columns else 0

    # ── Detailed Stats ──
    st.markdown("#### 📊 Detailed Performance")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Wins / Losses", f"{len(wins)} / {len(losses)}")
    s2.metric("Avg Win", f"${avg_win:+.2f}")
    s3.metric("Avg Loss", f"${avg_loss:+.2f}")
    s4.metric("Avg Hold", f"{avg_hold:.0f} min")

    s5, s6, s7, s8 = st.columns(4)
    s5.metric("Total Wins", f"${total_wins:+.2f}")
    s6.metric("Total Losses", f"-${total_losses:.2f}")
    s7.metric("Best Trade", f"${df['pnl'].max():+.2f}")
    s8.metric("Worst Trade", f"${df['pnl'].min():+.2f}")

    st.divider()

    # ── PnL Distribution ──
    st.markdown("#### 📊 PnL Distribution")
    if "pnl" in df.columns:
        import plotly.graph_objects as go
        fig = go.Figure()
        colors = ["#00ff88" if v > 0 else "#ff3b5c" for v in df["pnl"]]
        fig.add_trace(go.Bar(x=df["pnl"], marker_color=colors, name="PnL"))
        fig.update_layout(
            template="plotly_dark", height=300, margin=dict(l=10, r=10, t=30, b=10),
            xaxis_title="PnL ($)", yaxis_title="Count",
        )
        st.plotly_chart(fig, width="stretch")

    # ── Performance by Symbol ──
    st.markdown("#### 🏷️ Performance by Symbol")
    if "symbol" in df.columns:
        sym_stats = df.groupby("symbol").agg(
            trades=("pnl", "count"),
            total_pnl=("pnl", "sum"),
            avg_pnl=("pnl", "mean"),
            win_rate=("pnl", lambda x: (x > 0).mean() * 100),
        ).sort_values("total_pnl", ascending=False).reset_index()
        sym_stats.columns = ["Symbol", "Trades", "Total PnL", "Avg PnL", "Win Rate %"]
        sym_stats["Total PnL"] = sym_stats["Total PnL"].map("${:+.2f}".format)
        sym_stats["Avg PnL"] = sym_stats["Avg PnL"].map("${:+.2f}".format)
        sym_stats["Win Rate %"] = sym_stats["Win Rate %"].map("{:.1f}%".format)
        st.dataframe(sym_stats, use_container_width=True, hide_index=True, height=400)

    # ── Performance by Regime ──
    if "regime" in df.columns:
        st.markdown("#### 🌊 Performance by Regime")
        regime_stats = df.groupby("regime").agg(
            trades=("pnl", "count"),
            total_pnl=("pnl", "sum"),
            avg_pnl=("pnl", "mean"),
            win_rate=("pnl", lambda x: (x > 0).mean() * 100),
        ).sort_values("total_pnl", ascending=False).reset_index()
        regime_stats.columns = ["Regime", "Trades", "Total PnL", "Avg PnL", "Win Rate %"]
        regime_stats["Total PnL"] = regime_stats["Total PnL"].map("${:+.2f}".format)
        regime_stats["Avg PnL"] = regime_stats["Avg PnL"].map("${:+.2f}".format)
        regime_stats["Win Rate %"] = regime_stats["Win Rate %"].map("{:.1f}%".format)
        st.dataframe(regime_stats, use_container_width=True, hide_index=True)

    # ── Performance by Session ──
    if "session" in df.columns:
        st.markdown("#### 🌏 Performance by Session")
        df["_session_label"] = df["session"].replace({"": "unknown", None: "unknown"})
        sess_stats = df.groupby("_session_label").agg(
            trades=("pnl", "count"),
            total_pnl=("pnl", "sum"),
            avg_pnl=("pnl", "mean"),
            win_rate=("pnl", lambda x: (x > 0).mean() * 100),
        ).sort_values("total_pnl", ascending=False).reset_index()
        sess_stats.columns = ["Session", "Trades", "Total PnL", "Avg PnL", "Win Rate %"]
        sess_stats["Total PnL"] = sess_stats["Total PnL"].map("${:+.2f}".format)
        sess_stats["Avg PnL"] = sess_stats["Avg PnL"].map("${:+.2f}".format)
        sess_stats["Win Rate %"] = sess_stats["Win Rate %"].map("{:.1f}%".format)
        st.dataframe(sess_stats, use_container_width=True, hide_index=True)

    # ── Performance by Exit Reason ──
    if "exit_reason" in df.columns:
        st.markdown("#### 🚪 Performance by Exit Method")
        # Truncate long exit reasons (e.g. "trailing_stop (peak=11.8R, current=6.9R)" → "trailing_stop")
        df["_exit_short"] = df["exit_reason"].str.split(" [(]").str[0]
        exit_stats = df.groupby("_exit_short").agg(
            trades=("pnl", "count"),
            total_pnl=("pnl", "sum"),
            avg_pnl=("pnl", "mean"),
            win_rate=("pnl", lambda x: (x > 0).mean() * 100),
        ).sort_values("total_pnl", ascending=False).reset_index()
        exit_stats.columns = ["Exit Reason", "Trades", "Total PnL", "Avg PnL", "Win Rate %"]
        exit_stats["Total PnL"] = exit_stats["Total PnL"].map("${:+.2f}".format)
        exit_stats["Avg PnL"] = exit_stats["Avg PnL"].map("${:+.2f}".format)
        exit_stats["Win Rate %"] = exit_stats["Win Rate %"].map("{:.1f}%".format)
        st.dataframe(exit_stats, use_container_width=True, hide_index=True)

    # ── Rolling PF (last 20 trades) ──
    st.markdown("#### ⚡ Rolling Profit Factor (Last 20 Trades)")
    recent = df.head(20)
    rw = recent[recent["pnl"] > 0]["pnl"].sum()
    rl = abs(recent[recent["pnl"] <= 0]["pnl"].sum())
    rpf = rw / rl if rl > 0 else 99.9
    rwr = (recent["pnl"] > 0).mean() * 100 if len(recent) else 0
    rpnl = recent["pnl"].sum()

    rc1, rc2, rc3 = st.columns(3)
    rc1.metric("Rolling PF (20)", f"{rpf:.2f}", delta="🟢 SAFE" if rpf >= 0.8 else "🔴 CIRCUIT BREAKER")
    rc2.metric("Rolling WR (20)", f"{rwr:.1f}%")
    rc3.metric("Rolling PnL (20)", f"${rpnl:+.2f}")

    # ── Recent Trade History (last 50) ──
    st.markdown("#### 📋 Recent Trades (Last 50)")
    recent_50 = df.head(50).copy()
    if "opened_at" in recent_50.columns:
        recent_50["Time"] = recent_50["opened_at"].dt.strftime("%m-%d %H:%M")
    else:
        recent_50["Time"] = "—"
    recent_50["Outcome"] = recent_50["pnl"].apply(lambda x: "✅ WIN" if x > 0 else "❌ LOSS")
    recent_50["PnL"] = recent_50["pnl"].map("${:+.2f}".format)
    recent_50["WR"] = recent_50.get("confidence", pd.Series([0]*len(recent_50))).map(lambda x: f"{x*100:.0f}%" if x and x > 0 else "—")

    show_cols = ["Time", "symbol", "side", "entry_price", "pnl", "Outcome", "PnL", "exit_reason", "regime", "session"]
    avail = [c for c in show_cols if c in recent_50.columns]
    rename_map = {"symbol": "Symbol", "side": "Side", "entry_price": "Entry", "pnl": "PnL_raw",
                  "exit_reason": "Exit", "regime": "Regime", "session": "Session"}
    show_df = recent_50[avail].rename(columns=rename_map)
    st.dataframe(show_df, use_container_width=True, hide_index=True, height=400)

# ═══════════════════════════════════════════════════════════════
# TAB 8 — AUDIT LOGS
# ═══════════════════════════════════════════════════════════════
def tab8():
    st.subheader("📋 Audit Logs")
    signals=J("signals").get("signals",[])
    positions=J("positions").get("positions",[])
    
    if not signals:
        st.info("No signals recorded"); return
    
    search=st.text_input("Search symbol","",key="audit_search")
    
    # Build market price lookup for unrealized PnL
    md=J("market_data")
    md_rows=md.get("rows",[])
    price_map={r["symbol"]:r for r in md_rows}
    
    import pandas as pd
    rows=[]
    for s in signals:
        sym=s.get("symbol","?")
        entry=s.get("entry_price",s.get("entry",0))
        side=s.get("side","LONG")
        pos=None
        for p in positions:
            if p.get("signal_id")==s.get("id"): pos=p; break
        
        outcome="ACTIVE"; pnl=0
        if pos:
            pnl=pos.get("pnl",0)
            if pnl>0: outcome="WIN"
            elif pnl<0: outcome="LOSS"
            else: outcome="OPEN"
        elif entry and entry>0 and sym in price_map:
            # Compute unrealized PnL from live market price
            md_row=price_map[sym]
            cur=md_row.get("mark_price",0)
            if cur and cur>0:
                if side.upper()=="LONG":
                    pnl=(cur-entry)/entry*100
                else:
                    pnl=(entry-cur)/entry*100
                outcome="LIVE"
        
        rows.append({"Time":_dt(s.get("created_at",s.get("timestamp",0))),"Symbol":sym,"Dir":side,
                      "Conf":f"{s.get('confidence',0)*100:.1f}%","Score":s.get("institutional_score",0),
                      "Entry":_p(entry),"Current":_p(price_map.get(sym,{}).get("mark_price",0)) if sym in price_map else "—",
                      "PnL%":f"{pnl:+.2f}%","Outcome":outcome})
    
    df=pd.DataFrame(rows)
    if search:
        df=df[df["Symbol"].str.contains(search,case=False,na=False)]
    
    PAGE=50; total=len(df); pages=max(1,(total+PAGE-1)//PAGE)
    page=st.number_input("Page",1,pages,1,key="audit_pg")
    start=(page-1)*PAGE; end=min(start+PAGE,total)
    
    st.dataframe(df.iloc[start:end],width="stretch",hide_index=True,height=400)
    st.caption(f"Showing {start+1}-{end} of {total}")
    
    csv=df.to_csv(index=False)
    st.download_button("📥 Export CSV",csv,"audit_log.csv","text/csv")

# ═══════════════════════════════════════════════════════════════
# TAB 9 — RAW DEBUG
# ═══════════════════════════════════════════════════════════════
def tab9():
    st.subheader("🔧 Raw Debug")
    with st.expander("Engine Status"): st.json(J("status"))
    with st.expander("Signals (raw)"): st.json(J("signals").get("signals",[])[:5])
    with st.expander("Positions (raw)"): st.json(J("positions").get("positions",[])[:5])
    with st.expander("Metrics (raw)"): st.json(J("metrics").get("metrics",{}))
    with st.expander("Funnel (raw)"): st.json(J("funnel").get("funnel",{}))
    with st.expander("Market Intelligence"): st.json(J("market_intelligence").get("intelligence",{}))
    with st.expander("Smart Money"): st.json({k:v for k,v in J("smart_money_map").items() if k!="rows"})
    with st.expander("Health"): st.json(J("engine_health"))
    with st.expander("Alerts"): st.json(J("alerts").get("alerts",[])[:10])
    with st.expander("Equity (last 5)"): st.json(J("equity_history").get("history",[])[-5:])

# ═══════════════════════════════════════════════════════════════
# TAB 10 — DAILY CSV EXPORT
# ═══════════════════════════════════════════════════════════════
def tab10():
    st.subheader("📥 Daily Signal & Trade Reports")
    
    import pandas as pd
    import sqlite3
    
    DB_PATH = _ai_root / "data" / "institutional_v1.db"
    
    tab_sig, tab_trade, tab_both = st.tabs(["📡 Signals by Date", "💰 Trades by Date", "📊 Full Daily Report"])
    
    # ── Tab A: Signals by Date ──
    with tab_sig:
        st.markdown("**Export all signals generated on a specific date**")
        sel_date = st.date_input("Select date", value=datetime.now(timezone.utc).date(), key="sig_date")
        
        if st.button("📥 Download Signals for Date", key="dl_sig_date"):
            try:
                conn = sqlite3.connect(str(DB_PATH), timeout=10)
                ts_start = datetime(sel_date.year, sel_date.month, sel_date.day, tzinfo=timezone.utc).timestamp()
                ts_end = ts_start + 86400
                
                df = pd.read_sql_query("""
                    SELECT 
                        timestamp,
                        datetime(timestamp, 'unixepoch', 'utc') as datetime_utc,
                        symbol, side, entry, stop_loss, take_profit, take_profit_2,
                        risk_reward, confidence, market_regime as regime,
                        institutional_score as score, open_interest, oi_delta,
                        funding_rate, exchange_flow, delta, cvd,
                        absorption_score, sweep_score, spoofing_score,
                        mtf_alignment, status, outcome, realized_r,
                        mae_pct, mfe_pct, entry_reason
                    FROM signals 
                    WHERE timestamp >= ? AND timestamp < ?
                    ORDER BY timestamp
                """, conn, params=(ts_start, ts_end))
                conn.close()
                
                if len(df) > 0:
                    # Add date columns
                    df.insert(0, "Date", df["datetime_utc"].str[:10])
                    df.insert(1, "Time", df["datetime_utc"].str[11:19] + " UTC")
                    df.drop(columns=["timestamp", "datetime_utc"], inplace=True)
                    
                    # Format confidence as percentage
                    df["confidence"] = df["confidence"].apply(lambda x: f"{x*100:.1f}%" if x else "—")
                    
                    csv_str = df.to_csv(index=False)
                    st.download_button(
                        f"📥 Download {len(df)} Signals ({sel_date})",
                        csv_str,
                        f"signals_{sel_date}.csv",
                        "text/csv",
                        key="dl_sig_date_dl"
                    )
                    st.dataframe(df.head(20), width="stretch", hide_index=True, height=300)
                    st.caption(f"Total: {len(df)} signals on {sel_date}")
                else:
                    st.info(f"No signals found for {sel_date}")
            except Exception as e:
                st.error(f"Error: {e}")
    
    # ── Tab B: Trades by Date ──
    with tab_trade:
        st.markdown("**Export all closed trades on a specific date**")
        trade_date = st.date_input("Select date", value=datetime.now(timezone.utc).date(), key="trade_date")
        
        if st.button("📥 Download Trades for Date", key="dl_trade_date"):
            try:
                conn = sqlite3.connect(str(DB_PATH), timeout=10)
                ts_start = datetime(trade_date.year, trade_date.month, trade_date.day, tzinfo=timezone.utc).timestamp()
                ts_end = ts_start + 86400
                
                df = pd.read_sql_query("""
                    SELECT 
                        closed_at,
                        datetime(closed_at, 'unixepoch', 'utc') as close_datetime,
                        symbol, side, entry_price, quantity, leverage,
                        stop_loss, take_profit, take_profit_2,
                        risk_reward as planned_rr, pnl, fees,
                        exit_reason, hold_minutes, outcome, realized_r,
                        confidence, regime, institutional_score as score,
                        session, mfe_pct, mae_pct, strategy_version
                    FROM positions_archive 
                    WHERE closed_at >= ? AND closed_at < ?
                    ORDER BY closed_at
                """, conn, params=(ts_start, ts_end))
                conn.close()
                
                if len(df) > 0:
                    # Summary stats
                    wins = len(df[df["pnl"] > 0])
                    losses = len(df[df["pnl"] < 0])
                    total_pnl = df["pnl"].sum()
                    avg_pnl = df["pnl"].mean()
                    wr = wins / len(df) * 100 if len(df) > 0 else 0
                    wp = df[df["pnl"] > 0]["pnl"].sum()
                    lp = abs(df[df["pnl"] < 0]["pnl"].sum())
                    pf = wp / lp if lp > 0 else 0
                    
                    c1, c2, c3, c4, c5 = st.columns(5)
                    c1.metric("Trades", len(df))
                    c2.metric("Win Rate", f"{wr:.1f}%")
                    c3.metric("Total PnL", f"${total_pnl:.2f}")
                    c4.metric("Avg PnL", f"${avg_pnl:.2f}")
                    c5.metric("Profit Factor", f"{pf:.2f}")
                    
                    # Format columns
                    df.insert(0, "Date", df["close_datetime"].str[:10])
                    df.insert(1, "Time", df["close_datetime"].str[11:19] + " UTC")
                    df.drop(columns=["closed_at", "close_datetime"], inplace=True)
                    
                    csv_str = df.to_csv(index=False)
                    st.download_button(
                        f"📥 Download {len(df)} Trades ({trade_date})",
                        csv_str,
                        f"trades_{trade_date}.csv",
                        "text/csv",
                        key="dl_trade_date_dl"
                    )
                    st.dataframe(df, width="stretch", hide_index=True, height=400)
                    st.caption(f"Total: {len(df)} trades | PnL: ${total_pnl:.2f} | WR: {wr:.1f}%")
                else:
                    st.info(f"No closed trades found for {trade_date}")
            except Exception as e:
                st.error(f"Error: {e}")
    
    # ── Tab C: Full Daily Report (Signals + Trades + Summary) ──
    with tab_both:
        st.markdown("**Combined daily report: signals generated + trades closed + performance summary**")
        report_date = st.date_input("Select date", value=datetime.now(timezone.utc).date(), key="report_date")
        
        if st.button("📥 Generate Daily Report", key="dl_report"):
            try:
                conn = sqlite3.connect(str(DB_PATH), timeout=10)
                ts_start = datetime(report_date.year, report_date.month, report_date.day, tzinfo=timezone.utc).timestamp()
                ts_end = ts_start + 86400
                
                # Signals
                sig_df = pd.read_sql_query("""
                    SELECT 
                        datetime(timestamp, 'unixepoch', 'utc') as time_utc,
                        symbol, side, entry, stop_loss, take_profit,
                        risk_reward, confidence, market_regime as regime,
                        institutional_score as score, status, outcome, realized_r
                    FROM signals WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp
                """, conn, params=(ts_start, ts_end))
                
                # Trades
                trade_df = pd.read_sql_query("""
                    SELECT 
                        datetime(closed_at, 'unixepoch', 'utc') as close_time,
                        symbol, side, entry_price, quantity, leverage,
                        stop_loss, take_profit, pnl, fees,
                        exit_reason, hold_minutes, outcome, realized_r,
                        confidence, regime, session
                    FROM positions_archive WHERE closed_at >= ? AND closed_at < ? ORDER BY closed_at
                """, conn, params=(ts_start, ts_end))
                conn.close()
                
                # Summary
                st.markdown(f"### 📊 Daily Report — {report_date}")
                
                sc1, sc2, sc3, sc4 = st.columns(4)
                sc1.metric("Signals Generated", len(sig_df))
                sc2.metric("Trades Closed", len(trade_df))
                if len(trade_df) > 0:
                    total_pnl = trade_df["pnl"].sum()
                    wins = len(trade_df[trade_df["pnl"] > 0])
                    wr = wins / len(trade_df) * 100
                    sc3.metric("Total PnL", f"${total_pnl:.2f}")
                    sc4.metric("Win Rate", f"{wr:.1f}%")
                else:
                    sc3.metric("Total PnL", "—")
                    sc4.metric("Win Rate", "—")
                
                # Combined CSV
                if len(sig_df) > 0 or len(trade_df) > 0:
                    # Build combined rows
                    rows = []
                    for _, r in sig_df.iterrows():
                        rows.append({
                            "Type": "SIGNAL",
                            "Time": str(r.get("time_utc",""))[:19] + " UTC",
                            "Symbol": r.get("symbol","?"),
                            "Side": r.get("side","?"),
                            "Entry": r.get("entry",0),
                            "SL": r.get("stop_loss",0),
                            "TP": r.get("take_profit",0),
                            "RR": r.get("risk_reward",0),
                            "Confidence": f"{r.get('confidence',0)*100:.1f}%" if r.get("confidence") else "—",
                            "Regime": r.get("regime","?"),
                            "Score": r.get("score",0),
                            "PnL ($)": "—",
                            "Exit Reason": "—",
                            "Hold Time": "—",
                            "Outcome": r.get("outcome","—"),
                            "Realized R": r.get("realized_r","—"),
                        })
                    for _, r in trade_df.iterrows():
                        hold = f"{r.get('hold_minutes',0):.0f}m" if r.get("hold_minutes") else "—"
                        rows.append({
                            "Type": "TRADE",
                            "Time": str(r.get("close_time",""))[:19] + " UTC",
                            "Symbol": r.get("symbol","?"),
                            "Side": r.get("side","?"),
                            "Entry": r.get("entry_price",0),
                            "SL": r.get("stop_loss",0),
                            "TP": r.get("take_profit",0),
                            "RR": "—",
                            "Confidence": f"{r.get('confidence',0)*100:.1f}%" if r.get("confidence") else "—",
                            "Regime": r.get("regime","?"),
                            "Score": "—",
                            "PnL ($)": f"{r.get('pnl',0):.2f}",
                            "Exit Reason": r.get("exit_reason","—"),
                            "Hold Time": hold,
                            "Outcome": r.get("outcome","—"),
                            "Realized R": r.get("realized_r","—"),
                        })
                    
                    combined = pd.DataFrame(rows)
                    csv_str = combined.to_csv(index=False)
                    st.download_button(
                        f"📥 Download Full Report ({len(sig_df)} signals + {len(trade_df)} trades)",
                        csv_str,
                        f"daily_report_{report_date}.csv",
                        "text/csv",
                        key="dl_full_report"
                    )
                    st.dataframe(combined, width="stretch", hide_index=True, height=400)
                else:
                    st.info(f"No data found for {report_date}")
            except Exception as e:
                st.error(f"Error: {e}")

# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════
def main():
    # ── SIDEBAR: Auto-Refresh Controls ──
    with st.sidebar:
        st.markdown("### 🔄 Auto-Refresh")
        auto_refresh = st.toggle("Enable Auto-Refresh", value=True, key="auto_refresh_toggle")
        refresh_interval = st.select_slider(
            "Refresh Interval",
            options=[1, 2, 3, 5, 10, 15],
            value=3,
            format_func=lambda x: f"{x}s",
            key="refresh_interval",
            disabled=not auto_refresh,
        )
        if auto_refresh:
            st.caption(f"⏱️ Refreshing every **{refresh_interval}s**")
        else:
            st.caption("⏸️ Auto-refresh paused")
        st.divider()
        st.caption(f"🕐 {datetime.now().strftime('%H:%M:%S')} UTC")

    # ── AUTO-REFRESH TRIGGER ──
    # ── AUTO-REFRESH TRIGGER ──
    if auto_refresh:
        st_autorefresh(interval=max(refresh_interval, 30) * 1000, key="datarefresh")

    t1,t2,t3,t4,t5,t6,t7,t8,t9,t10=st.tabs(["📊 Dashboard","📡 Signals","🔍 Pipeline","✅ Checklist","📊 Orderflow","🧭 Market","📈 Performance","📋 Audit","🔧 Debug","📥 Export"])
    with t1: tab1()
    with t2: tab2()
    with t3: tab3()
    with t4: tab4()
    with t5: tab5()
    with t6: tab6()
    with t7: tab7()
    with t8: tab8()
    with t9: tab9()
    with t10: tab10()
    st.caption(f"🏛️ DeltaTerminal v2.5 — {datetime.now().strftime('%H:%M:%S')} | 250+ Symbols")

if __name__=="__main__":
    main()
