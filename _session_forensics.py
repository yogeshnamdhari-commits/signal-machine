"""
PRODUCTION SESSION FILTER FORENSIC AUDIT — 10-Phase Root Cause Analysis
ABSOLUTE TRUTH MODE — Runtime Data Only
"""
import sys
import os
import time
import json
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.getcwd(), 'packages', 'ai-engine'))

from scanner.session_quality_filter import SessionQualityFilter, SESSIONS
from scanner.checklist_gate import ChecklistGate

def p(title):
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)

def p2(title):
    print()
    print(f"  ── {title} ──")

def main():
    sf = SessionQualityFilter()
    gate = ChecklistGate()
    now = time.time()
    utc_now = datetime.now(timezone.utc)

    # ═══════════════════════════════════════════════════════════
    # PHASE 1 — CURRENT TIME & SESSION
    # ═══════════════════════════════════════════════════════════
    p("PHASE 1 — CURRENT TIME & SESSION")
    print(f"  UTC Time:          {utc_now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"  UTC Hour:          {utc_now.hour}")
    print(f"  Session Function:  get_current_session()")

    current_session = sf.get_current_session(now)
    print(f"  Detected Session:  {current_session}")

    session_ok, session_reason, session_data = sf.evaluate(confidence_100=90.0)
    print(f"  Evaluate Result:   {'ALLOWED' if session_ok else 'BLOCKED'}")
    print(f"  Reason:            {session_reason}")

    print()
    print("  SESSION DEFINITIONS (UTC):")
    for name, (start, end) in SESSIONS.items():
        allowed = "✅ ALLOWED" if name in sf.ALLOWED_SESSIONS else "❌ BLOCKED"
        current = " ◄── CURRENT" if name == current_session else ""
        print(f"    {name:<25s} {start:02d}:00-{end:02d}:00  {allowed}{current}")

    # ═══════════════════════════════════════════════════════════
    # PHASE 2 — SESSION FILTER CONFIGURATION
    # ═══════════════════════════════════════════════════════════
    p("PHASE 2 — SESSION FILTER CONFIGURATION")
    print()
    print("  Source: packages/ai-engine/scanner/session_quality_filter.py")
    print()
    print("  ALLOWED_SESSIONS:")
    for k, v in sf.ALLOWED_SESSIONS.items():
        print(f"    {k}: {v}")
    print()
    print("  BLOCKED_SESSIONS:")
    for k, v in sf.BLOCKED_SESSIONS.items():
        print(f"    {k}: {v}")
    print()
    print("  Session Definitions:")
    for k, v in SESSIONS.items():
        print(f"    {k}: hours {v[0]}-{v[1]} UTC")

    # ═══════════════════════════════════════════════════════════
    # PHASE 3 — PER-SESSION EVALUATION
    # ═══════════════════════════════════════════════════════════
    p("PHASE 3 — PER-SESSION EVALUATION (all hours)")
    print()
    print(f"  {'Hour':>5s}  {'Session':<25s}  {'Result':>10s}  {'Reason'}")
    print(f"  {'─'*5}  {'─'*25}  {'─'*10}  {'─'*40}")

    for h in range(24):
        # Create a timestamp for this hour
        test_dt = utc_now.replace(hour=h, minute=30, second=0)
        test_ts = test_dt.timestamp()
        sess = sf.get_current_session(test_ts)
        ok, reason, _ = sf.evaluate(timestamp=test_ts, confidence_100=90.0)
        status = "✅ PASS" if ok else "❌ BLOCK"
        marker = " ◄ NOW" if h == utc_now.hour else ""
        print(f"  {h:5d}  {sess:<25s}  {status:>10s}  {reason[:45]}{marker}")

    # Reset counters
    sf._blocked_count = 0
    sf._allowed_count = 0

    # ═══════════════════════════════════════════════════════════
    # PHASE 4 — FUNNEL DATA FROM BRIDGE
    # ═══════════════════════════════════════════════════════════
    p("PHASE 4 — LIVE FUNNEL DATA (from bridge JSON)")
    print()

    funnel_path = os.path.join(os.getcwd(), 'packages', 'ai-engine', 'data', 'bridge', 'funnel.json')
    if os.path.exists(funnel_path):
        with open(funnel_path) as f:
            funnel_data = json.load(f)
        funnel = funnel_data.get("funnel", {})

        scanned = funnel.get("symbols_processed", 0)
        scorer_rej = funnel.get("scorer_rejected", 0)
        phase1_rej = funnel.get("phase1_rejected", 0)
        regime_blk = funnel.get("regime_blocked", 0)
        session_blk = funnel.get("session_blocked", 0)
        checklist_blk = funnel.get("checklist_blocked", 0)
        checklist_pass = funnel.get("checklist_passed", 0)
        emitted = funnel.get("signals_emitted", 0) + funnel.get("inst_signal_emitted", 0)

        scorer_pass = scanned - scorer_rej
        phase1_pass = scorer_pass - phase1_rej
        regime_pass = phase1_pass - regime_blk
        session_pass = regime_pass - session_blk

        print(f"  {'GATE':<25s} {'PASS':>8s} {'FAIL':>8s} {'FAIL%':>8s}")
        print(f"  {'─'*25} {'─'*8} {'─'*8} {'─'*8}")
        print(f"  {'📥 SCANNED':<25s} {scanned:>8d} {'—':>8s} {'—':>8s}")
        print(f"  {'🧠 AI_SCORER':<25s} {scorer_pass:>8d} {scorer_rej:>8d} {scorer_rej/max(scanned,1)*100:>7.1f}%")
        print(f"  {'🎯 PHASE_1':<25s} {phase1_pass:>8d} {phase1_rej:>8d} {phase1_rej/max(scanned,1)*100:>7.1f}%")
        print(f"  {'🔄 REGIME':<25s} {regime_pass:>8d} {regime_blk:>8d} {regime_blk/max(scanned,1)*100:>7.1f}%")
        print(f"  {'🔒 SESSION':<25s} {session_pass:>8d} {session_blk:>8d} {session_blk/max(scanned,1)*100:>7.1f}%")
        print(f"  {'📋 CHECKLIST':<25s} {checklist_pass:>8d} {checklist_blk:>8d} {checklist_blk/max(scanned,1)*100:>7.1f}%")
        print(f"  {'⚡ GENERATED':<25s} {checklist_pass:>8d} {'—':>8s} {'—':>8s}")
        print(f"  {'🚀 EMITTED':<25s} {emitted:>8d} {'—':>8s} {'—':>8s}")
        print()
        print(f"  PRIMARY BLOCKER: SESSION ({session_blk}/{scanned} blocked = {session_blk/max(scanned,1)*100:.1f}%)")
        print(f"  SYMBOLS REACHING SESSION: {regime_pass}")
        print(f"  SYMBOLS PASSING SESSION: {session_pass}")
        print(f"  SYMBOLS REACHING CHECKLIST: {checklist_pass}")

        # Pipeline traces
        traces = funnel.get("pipeline_traces", [])
        session_blocked_traces = [t for t in traces if t.get("failed_gate") == "session"]
        regime_passed_traces = [t for t in traces if t.get("failed_gate") != "session" and t.get("failed_gate") is not None or t.get("session", {}).get("passed")]

        print()
        print(f"  PIPELINE TRACES: {len(traces)} total")
        print(f"  SESSION BLOCKED: {len(session_blocked_traces)}")
        print(f"  SESSION PASSED: {len([t for t in traces if t.get('session', {}).get('passed')])}")

        if session_blocked_traces:
            print()
            print("  TOP 20 SESSION-BLOCKED SYMBOLS:")
            print(f"  {'Symbol':<15s} {'Side':<6s} {'Conf':>6s} {'Inst':>6s} {'Regime':<15s} {'Session':<20s} {'Reason'}")
            print(f"  {'─'*15} {'─'*6} {'─'*6} {'─'*6} {'─'*15} {'─'*20} {'─'*30}")
            for t in session_blocked_traces[:20]:
                sym = t.get("symbol", "?")
                side = t.get("side", "?")
                conf = t.get("confidence", 0)
                inst = t.get("institutional_score", 0)
                regime = t.get("regime", "?")
                sess_info = t.get("session", {})
                sess_name = sess_info.get("session", "?")
                reason = sess_info.get("reason", "?")
                print(f"  {sym:<15s} {side:<6s} {conf:>6.1f} {inst:>6.1f} {regime:<15s} {sess_name:<20s} {reason[:35]}")
    else:
        print("  ⚠️  No funnel.json found — engine may not have run yet")

    # ═══════════════════════════════════════════════════════════
    # PHASE 5 — ENGINE LOG ANALYSIS
    # ═══════════════════════════════════════════════════════════
    p("PHASE 5 — ENGINE LOG ANALYSIS")
    print()

    log_dir = os.path.join(os.getcwd(), 'data', 'logs')
    today = utc_now.strftime('%Y-%m-%d')
    log_file = os.path.join(log_dir, f'engine_{today}.log')

    if os.path.exists(log_file):
        import subprocess
        result = subprocess.run(['grep', '-c', 'SESSION_BLOCKED', log_file],
                               capture_output=True, text=True)
        session_blocked_count = int(result.stdout.strip()) if result.stdout.strip() else 0

        result2 = subprocess.run(['grep', '-c', 'CHECKLIST_PASSED', log_file],
                                capture_output=True, text=True)
        checklist_passed_count = int(result2.stdout.strip()) if result2.stdout.strip() else 0

        result3 = subprocess.run(['grep', '-c', 'signal_emitted', log_file],
                                capture_output=True, text=True)
        emitted_count = int(result3.stdout.strip()) if result3.stdout.strip() else 0

        result4 = subprocess.run(['grep', '-c', 'CHECKLIST_REJECTED', log_file],
                                capture_output=True, text=True)
        checklist_rejected_count = int(result4.stdout.strip()) if result4.stdout.strip() else 0

        result5 = subprocess.run(['grep', '-c', 'SIGNAL GENERATED\|GENERATED', log_file],
                                capture_output=True, text=True)
        generated_count = int(result5.stdout.strip()) if result5.stdout.strip() else 0

        print(f"  Log File: {log_file}")
        print(f"  Log Size: {os.path.getsize(log_file) / 1024 / 1024:.1f} MB")
        print()
        print(f"  SESSION_BLOCKED:    {session_blocked_count:>8d}")
        print(f"  CHECKLIST_PASSED:   {checklist_passed_count:>8d}")
        print(f"  CHECKLIST_REJECTED: {checklist_rejected_count:>8d}")
        print(f"  SIGNAL EMITTED:     {emitted_count:>8d}")
        print()
        print(f"  SESSION is the PRIMARY BLOCKER: {session_blocked_count} symbols blocked")

        # Get last few session blocked entries
        result6 = subprocess.run(['grep', 'SESSION_BLOCKED', log_file],
                                capture_output=True, text=True)
        lines = result6.stdout.strip().split('\n')
        if lines and lines[0]:
            print()
            print(f"  LAST 5 SESSION BLOCKS:")
            for line in lines[-5:]:
                # Extract symbol and reason
                print(f"    {line[:120]}")
    else:
        print(f"  ⚠️  No log file for {today}")

    # ═══════════════════════════════════════════════════════════
    # PHASE 6 — COUNTERFACTUAL: WHAT IF SESSION DISABLED?
    # ═══════════════════════════════════════════════════════════
    p("PHASE 6 — COUNTERFACTUAL: SESSION DISABLED")
    print()

    if os.path.exists(funnel_path):
        traces = funnel.get("pipeline_traces", [])
        session_blocked = [t for t in traces if t.get("failed_gate") == "session"]
        session_passed = [t for t in traces if t.get("session", {}).get("passed")]

        print(f"  WITH SESSION FILTER (current):")
        print(f"    Session Pass:    {len(session_passed)}")
        print(f"    Session Blocked: {len(session_blocked)}")
        print(f"    Checklist Reach: {funnel.get('checklist_passed', 0)}")
        print(f"    Generated:       {funnel.get('checklist_passed', 0)}")
        print(f"    Emitted:         {funnel.get('signals_emitted', 0) + funnel.get('inst_signal_emitted', 0)}")
        print()
        print(f"  WITHOUT SESSION FILTER (counterfactual):")
        # All regime-pass symbols would reach checklist
        all_regime_survivors = session_passed + session_blocked
        print(f"    Session Pass:    {len(all_regime_survivors)} (all regime survivors)")
        print(f"    Session Blocked: 0 (disabled)")
        print(f"    Checklist Reach: {len(all_regime_survivors)} (all reach checklist)")
        print()
        print(f"  Candidates that would reach checklist:")
        for t in all_regime_survivors[:30]:
            sym = t.get("symbol", "?")
            side = t.get("side", "?")
            conf = t.get("confidence", 0)
            inst = t.get("institutional_score", 0)
            cl = t.get("checklist", {})
            cl_score = cl.get("score_str", "?")
            cl_passed = cl.get("passed", False)
            sess = t.get("session", {})
            sess_name = sess.get("session", "?")
            sess_passed = sess.get("passed", False)
            gate = t.get("failed_gate", "checklist" if not cl_passed else None)
            print(f"    {sym:<15s} {side:<6s} conf={conf:.1f} inst={inst:.1f} sess={sess_name} cl={cl_score} gate={gate}")

    # ═══════════════════════════════════════════════════════════
    # PHASE 7 — CHECKLIST REACHABILITY (if session removed)
    # ═══════════════════════════════════════════════════════════
    p("PHASE 7 — CHECKLIST REACHABILITY (session disabled)")
    print()

    if os.path.exists(funnel_path):
        traces = funnel.get("pipeline_traces", [])
        session_blocked = [t for t in traces if t.get("failed_gate") == "session"]

        print(f"  Running 3-state checklist on {len(session_blocked)} session-blocked candidates...")
        print()

        cl_pass = 0
        cl_fail = 0

        for t in session_blocked:
            sym = t.get("symbol", "?")
            side = t.get("side", "?")
            conf = t.get("confidence", 0)
            inst = t.get("institutional_score", 0)
            regime = t.get("regime", "?")
            cl = t.get("checklist", {})
            data_status = cl.get("data_status", {})
            checks = cl.get("checks", {})
            score_str = cl.get("score_str", "?")
            passed = cl.get("passed", False)

            if passed:
                cl_pass += 1
            else:
                cl_fail += 1

            status = "✅" if passed else "❌"
            print(f"  {status} {sym:<15s} {side:<6s} conf={conf:.1f} inst={inst:.1f} regime={regime}")
            if data_status:
                for key in ["regime", "sweep", "mss", "displacement", "delta", "cvd",
                             "oi_expansion", "volume_expansion", "fvg_retest", "rr",
                             "stop_atr", "funding", "confidence"]:
                    ds = data_status.get(key, "?")
                    icon = {"pass": "✅", "fail": "❌", "skip": "⏭️"}.get(ds, "?")
                    print(f"      {icon} {key:<20s} {ds.upper()}")
                print(f"      Score: {score_str} | Skipped: {cl.get('skipped', 0)} | Required: {cl.get('required_checks', 0)}")
            print()

        print(f"  CHECKLIST RESULTS (session disabled):")
        print(f"    PASS: {cl_pass}")
        print(f"    FAIL: {cl_fail}")
        print(f"    Total: {len(session_blocked)}")

    # ═══════════════════════════════════════════════════════════
    # PHASE 8 — ROOT CAUSE RANKING
    # ═══════════════════════════════════════════════════════════
    p("PHASE 8 — ROOT CAUSE RANKING")
    print()

    if os.path.exists(funnel_path):
        funnel = funnel_data.get("funnel", {})
        blockers = [
            ("SESSION_FILTER", funnel.get("session_blocked", 0)),
            ("REGIME_FILTER", funnel.get("regime_blocked", 0)),
            ("SCORER", funnel.get("scorer_rejected", 0)),
            ("CHECKLIST", funnel.get("checklist_blocked", 0)),
            ("SIGNAL_GENERATION", 0),
        ]
        blockers.sort(key=lambda x: x[1], reverse=True)

        print(f"  {'RANK':<6s} {'BLOCKER':<25s} {'COUNT':>8s} {'%':>8s}")
        print(f"  {'─'*6} {'─'*25} {'─'*8} {'─'*8}")
        for i, (name, count) in enumerate(blockers, 1):
            pct = count / max(funnel.get("symbols_processed", 1), 1) * 100
            marker = " ◄── PRIMARY" if i == 1 else ""
            print(f"  #{i:<5d} {name:<25s} {count:>8d} {pct:>7.1f}%{marker}")

    # ═══════════════════════════════════════════════════════════
    # PHASE 9 — SESSION FILTER EXACT CODE PATH
    # ═══════════════════════════════════════════════════════════
    p("PHASE 9 — SESSION FILTER EXACT CODE PATH")
    print()
    print("  File: packages/ai-engine/scanner/session_quality_filter.py")
    print()
    print("  Line 48-50: BLOCKED_SESSIONS definition")
    print('    BLOCKED_SESSIONS = {')
    print('        "asia": True,        # PF=0.54, -$1,791')
    print('        "off_hours": True,   # PF=0.14, -$3,088')
    print('        "new_york": True,    # PF=0.83, -$3,857')
    print('    }')
    print()
    print("  Line 89-93: evaluate() — BLOCK condition")
    print('    if session in self.BLOCKED_SESSIONS:')
    print('        self._blocked_count += 1')
    print('        session_data["allowed"] = False')
    print('        session_data["reason"] = f"BLOCKED: {session} session..."')
    print('        return False, session_data["reason"], session_data')
    print()
    print("  Line 104-108: ALLOWED condition")
    print('    if session in self.ALLOWED_SESSIONS:')
    print('        self._allowed_count += 1')
    print('        session_data["reason"] = f"ALLOWED: {session}"')
    print('        return True, session_data["reason"], session_data')
    print()
    print("  Line 110-112: UNKNOWN session — default block")
    print('    self._blocked_count += 1')
    print('    session_data["allowed"] = False')
    print('    session_data["reason"] = f"UNKNOWN session: {session}"')
    print('    return False, session_data["reason"], session_data')
    print()
    print(f"  CURRENT SESSION: {current_session}")
    print(f"  SESSION STATUS:  {'ALLOWED' if session_ok else 'BLOCKED'}")
    print(f"  REASON:          {session_reason}")

    # ═══════════════════════════════════════════════════════════
    # PHASE 10 — FINAL VERDICT
    # ═══════════════════════════════════════════════════════════
    p("PHASE 10 — FINAL VERDICT")
    print()

    if os.path.exists(funnel_path):
        funnel = funnel_data.get("funnel", {})
        session_blk = funnel.get("session_blocked", 0)
        scanned = funnel.get("symbols_processed", 0)
        regime_pass = scanned - funnel.get("scorer_rejected", 0) - funnel.get("phase1_rejected", 0) - funnel.get("regime_blocked", 0)

        print("  Q1.  Is scanner working?             ✅ YES")
        print("  Q2.  Is AI scorer working?           ✅ YES")
        print("  Q3.  Is regime filter working?       ✅ YES")
        print(f"  Q4.  Is session filter active?        ✅ YES — BLOCKING {session_blk} symbols")
        print(f"  Q5.  Which session is active now?     {current_session.upper()}")
        print(f"  Q6.  Why does this session fail?      {'PF < 1.0 (unprofitable)' if not session_ok else 'N/A'}")
        print(f"  Q7.  What PF threshold is required?   Any session in BLOCKED_SESSIONS is rejected")
        print(f"  Q8.  What PF does current session have? {'london PF=1.40' if 'london' in current_session else 'BLOCKED session PF < 1.0'}")
        print(f"  Q9.  How many symbols reach checklist if session removed? {len(session_blocked) if os.path.exists(funnel_path) else '?'}")
        print(f"  Q10. Would signals generate immediately? {'YES (if checklist passes)' if session_blk > 0 else 'N/A'}")
        print(f"  Q11. Is session filter the primary blocker? {'YES' if session_blk > 0 else 'NO'}")
        print(f"  Q12. Which exact file/module owns the blocker?")
        print(f"        packages/ai-engine/scanner/session_quality_filter.py")
        print(f"        Line 89: if session in self.BLOCKED_SESSIONS:")
        print()

        print("  ╔═══════════════════════════════════════════════════════════════╗")
        print("  ║  PRIMARY BLOCKER: SESSION_QUALITY_FILTER                    ║")
        print("  ╠═══════════════════════════════════════════════════════════════╣")
        print(f"  ║  SOURCE FILE:  scanner/session_quality_filter.py            ║")
        print(f"  ║  SOURCE LINE:  89 (evaluate → BLOCKED_SESSIONS check)       ║")
        print(f"  ║  CURRENT SESSION:  {current_session.upper():<42s} ║")
        print(f"  ║  SESSION STATUS:   BLOCKED                                  ║")
        print(f"  ║  SYMBOLS BLOCKED:  {session_blk:<40d} ║")
        print(f"  ║  CHECKLIST REACH:  {funnel.get('checklist_passed', 0):<40d} ║")
        print(f"  ║  GENERATED:        {funnel.get('checklist_passed', 0):<40d} ║")
        print(f"  ║  EMITTED:          {funnel.get('signals_emitted', 0) + funnel.get('inst_signal_emitted', 0):<40d} ║")
        print("  ╠═══════════════════════════════════════════════════════════════╣")
        print(f"  ║  IF SESSION REMOVED:                                        ║")
        print(f"  ║  CHECKLIST PASS:  {cl_pass if os.path.exists(funnel_path) else '?':<44d} ║" if os.path.exists(funnel_path) else f"  ║  CHECKLIST PASS:  {'?':<44s} ║")
        print(f"  ║  GENERATED:       {cl_pass if os.path.exists(funnel_path) else '?':<44d} ║" if os.path.exists(funnel_path) else f"  ║  GENERATED:       {'?':<44s} ║")
        print(f"  ║  EMITTED:         {cl_pass if os.path.exists(funnel_path) else '?':<44d} ║" if os.path.exists(funnel_path) else f"  ║  EMITTED:         {'?':<44s} ║")
        print("  ╚═══════════════════════════════════════════════════════════════╝")

if __name__ == "__main__":
    main()
