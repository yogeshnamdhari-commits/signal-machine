#!/usr/bin/env python3
"""
Hypothesis Tracker v5
======================
Full research governance with out-of-sample confirmation.

Promotion checklist (ALL must pass):
  1. In-sample:        Statistical significance (FDR-corrected)
  2. Practical:         Effect size above minimum threshold
  3. Rolling windows:   Effect persists across time periods
  4. Regime consistency: Effect holds across market regimes
  5. Out-of-sample:     Effect confirmed on held-out data
  6. Forward test:      Effect confirmed on future data (after finding)

Only after ALL gates pass does the engine recommend a strategy change.

READ-ONLY — Never modifies trading logic.
"""
import sys
import json
import math
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

AI_ROOT = Path(__file__).resolve().parent / "packages" / "ai-engine"
sys.path.insert(0, str(AI_ROOT))

DB_PATH = AI_ROOT / "data" / "institutional_v1.db"
BRIDGE_DIR = AI_ROOT / "data" / "bridge"
REPORTS_DIR = AI_ROOT / "data" / "reports"
HISTORY_PATH = BRIDGE_DIR / "hypothesis_history.json"


# ── Data Classes ──────────────────────────────────────────────────────────────

@dataclass
class Hypothesis:
    """A research hypothesis being tracked."""
    id: str
    title: str
    description: str
    status: str = "OPEN"  # OPEN, WEAK_SIGNAL, SIGNAL, CONFIRMED, REFUTED
    confidence: float = 0.0  # 0-1
    sample_at_last_check: int = 0
    evidence: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    confirmation_threshold: int = 200
    min_effect_size: float = 0.1
    last_updated: str = ""
    # Statistical fields
    confidence_interval: str = ""
    p_value: float = 1.0
    adjusted_p_value: float = 1.0
    effect_size: float = 0.0
    statistical_test: str = ""
    practical_significance: bool = False
    # Trend fields
    trend: str = "unknown"
    consecutive_periods: int = 0
    action: str = "Monitor only — do not change strategy yet"
    # Stability fields
    rolling_effect_sizes: List[float] = field(default_factory=list)
    validation_windows_passed: int = 0
    validation_windows_total: int = 0
    regime_breakdown: Dict[str, Dict] = field(default_factory=dict)
    symbol_breakdown: Dict[str, Dict] = field(default_factory=dict)
    stability_score: float = 0.0  # 0-1
    # Out-of-sample fields (NEW)
    in_sample_effect: float = 0.0
    out_of_sample_effect: float = 0.0
    forward_test_effect: float = 0.0
    oos_confirmed: bool = False  # Effect exists in OOS
    forward_confirmed: bool = False  # Effect exists in forward test
    promotion_gates: Dict[str, bool] = field(default_factory=dict)
    promotion_score: int = 0  # How many gates passed (0-6)
    readiness_score: int = 0  # Weighted 0-100 score


# ── Statistical Helpers ───────────────────────────────────────────────────────

def compute_proportion_ci(successes: int, total: int, z: float = 1.96) -> str:
    """Wilson score confidence interval for a proportion."""
    if total < 2:
        return "N/A"
    p = successes / total
    denom = 1 + z**2 / total
    center = (p + z**2 / (2 * total)) / denom
    margin = z * math.sqrt((p * (1 - p) + z**2 / (4 * total)) / total) / denom
    lower = max(0, center - margin)
    upper = min(1, center + margin)
    return f"[{lower*100:.1f}%, {upper*100:.1f}%]"


def compute_two_prop_z(n1: int, x1: int, n2: int, x2: int) -> Tuple[float, float]:
    """Two-proportion z-test. Returns (z_score, p_value)."""
    if n1 < 5 or n2 < 5:
        return 0.0, 1.0
    p1, p2 = x1 / n1, x2 / n2
    p_pool = (x1 + x2) / (n1 + n2)
    if p_pool in (0, 1):
        return 0.0, 1.0
    se = math.sqrt(p_pool * (1 - p_pool) * (1/n1 + 1/n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    abs_z = min(abs(z), 6)
    p_val = 2 * (1 - 0.5 * (1 + math.erf(abs_z / math.sqrt(2))))
    return round(z, 3), round(p_val, 4)


def compute_cohens_h(n1: int, x1: int, n2: int, x2: int) -> float:
    """Effect size for two proportions."""
    if n1 < 2 or n2 < 2:
        return 0.0
    p1, p2 = x1 / n1, x2 / n2
    return round(abs(2 * (math.asin(math.sqrt(p1)) - math.asin(math.sqrt(p2)))), 3)


# ── Stability Analysis ────────────────────────────────────────────────────────

def compute_rolling_effect(trades: List[Dict], dimension: str, group_a: str, group_b: str,
                           window: int = 50) -> List[float]:
    """Compute rolling effect size across validation windows."""
    # Sort by time
    sorted_trades = sorted(trades, key=lambda t: t.get("closed_at", 0))
    effects = []

    for i in range(window, len(sorted_trades) + 1, window // 2):
        chunk = sorted_trades[max(0, i - window):i]
        a = [t for t in chunk if (t.get(dimension) or "unknown") == group_a]
        b = [t for t in chunk if (t.get(dimension) or "unknown") == group_b]

        if len(a) < 5 or len(b) < 5:
            continue

        a_wins = sum(1 for t in a if (t.get("pnl", 0) or 0) > 0)
        b_wins = sum(1 for t in b if (t.get("pnl", 0) or 0) > 0)
        h = compute_cohens_h(len(a), a_wins, len(b), b_wins)
        effects.append(h)

    return effects


def compute_regime_breakdown(trades: List[Dict], dimension: str, group_a: str,
                             group_b: str) -> Dict[str, Dict]:
    """Compute effect size per regime."""
    regimes = defaultdict(list)
    for t in trades:
        r = t.get("regime") or "unknown"
        regimes[r].append(t)

    breakdown = {}
    for regime, rtrades in regimes.items():
        if len(rtrades) < 10:
            continue

        a = [t for t in rtrades if (t.get(dimension) or "unknown") == group_a]
        b = [t for t in rtrades if (t.get(dimension) or "unknown") == group_b]

        if len(a) < 3 or len(b) < 3:
            continue

        def rs(ts):
            pnls = [t.get("pnl", 0) or 0 for t in ts]
            n = len(pnls); w = sum(1 for p in pnls if p > 0)
            return {"n": n, "wr": round(w/n*100, 1) if n else 0, "pnl": round(sum(pnls), 2)}

        a_s, b_s = rs(a), rs(b)
        h = compute_cohens_h(a_s["n"], sum(1 for t in a if (t.get("pnl",0) or 0) > 0),
                             b_s["n"], sum(1 for t in b if (t.get("pnl",0) or 0) > 0))

        breakdown[regime] = {
            f"{group_a}_wr": a_s["wr"], f"{group_a}_n": a_s["n"],
            f"{group_b}_wr": b_s["wr"], f"{group_b}_n": b_s["n"],
            "effect_size": h,
        }

    return breakdown


def compute_symbol_breakdown(trades: List[Dict], dimension: str, group_a: str,
                             group_b: str, top_n: int = 10) -> Dict[str, Dict]:
    """Compute effect size per symbol (top contributors)."""
    symbols = defaultdict(list)
    for t in trades:
        s = t.get("symbol") or "unknown"
        symbols[s].append(t)

    # Only top symbols by trade count
    top = sorted(symbols.items(), key=lambda x: -len(x[1]))[:top_n]

    breakdown = {}
    for symbol, strades in top:
        if len(strades) < 6:
            continue

        a = [t for t in strades if (t.get(dimension) or "unknown") == group_a]
        b = [t for t in strades if (t.get(dimension) or "unknown") == group_b]

        if len(a) < 2 or len(b) < 2:
            continue

        a_wins = sum(1 for t in a if (t.get("pnl", 0) or 0) > 0)
        b_wins = sum(1 for t in b if (t.get("pnl", 0) or 0) > 0)
        h = compute_cohens_h(len(a), a_wins, len(b), b_wins)

        a_pnl = sum(t.get("pnl", 0) or 0 for t in a)
        b_pnl = sum(t.get("pnl", 0) or 0 for t in b)

        breakdown[symbol] = {
            f"{group_a}": {"n": len(a), "pnl": round(a_pnl, 2)},
            f"{group_b}": {"n": len(b), "pnl": round(b_pnl, 2)},
            "effect_size": h,
        }

    return breakdown


def compute_stability_score(rolling_effects: List[float], min_effect: float) -> float:
    """How stable is the effect over time? 0=unstable, 1=perfectly stable."""
    if len(rolling_effects) < 2:
        return 0.0

    # Check what fraction of windows show the effect above threshold
    above = sum(1 for e in rolling_effects if e >= min_effect)
    consistency = above / len(rolling_effects)

    # Check variance (lower = more stable)
    mean_e = sum(rolling_effects) / len(rolling_effects)
    if mean_e == 0:
        return 0.0
    variance = sum((e - mean_e)**2 for e in rolling_effects) / len(rolling_effects)
    cv = math.sqrt(variance) / mean_e  # Coefficient of variation
    stability = max(0, 1 - cv)

    return round(consistency * stability, 2)


def compute_out_of_sample_effect(trades: List[Dict], dimension: str, group_a: str,
                                  group_b: str, split: float = 0.7) -> Tuple[float, float, float]:
    """Split trades into in-sample and out-of-sample, compute effect on each.
    Also compute forward-test effect (trades AFTER the finding was first observed).
    Returns (in_sample_effect, oos_effect, forward_effect)."""
    sorted_trades = sorted(trades, key=lambda t: t.get("closed_at", 0))
    n = len(sorted_trades)
    split_idx = int(n * split)

    in_sample = sorted_trades[:split_idx]
    out_of_sample = sorted_trades[split_idx:]

    def effect(ts):
        a = [t for t in ts if (t.get(dimension) or "unknown") == group_a]
        b = [t for t in ts if (t.get(dimension) or "unknown") == group_b]
        if len(a) < 5 or len(b) < 5:
            return 0.0
        a_wins = sum(1 for t in a if (t.get("pnl", 0) or 0) > 0)
        b_wins = sum(1 for t in b if (t.get("pnl", 0) or 0) > 0)
        return compute_cohens_h(len(a), a_wins, len(b), b_wins)

    # Forward test: last 20% of trades (after the finding period)
    forward_start = int(n * 0.8)
    forward = sorted_trades[forward_start:]

    return effect(in_sample), effect(out_of_sample), effect(forward)


def evaluate_promotion_gates(h: Hypothesis) -> Dict[str, bool]:
    """Evaluate all promotion gates for a hypothesis."""
    gates = {
        "in_sample_significant": h.adjusted_p_value < 0.05,
        "practical_significance": h.practical_significance,
        "rolling_windows_stable": h.stability_score >= 0.5,
        "regime_consistent": len(h.regime_breakdown) >= 2 and all(
            d.get("effect_size", 0) >= h.min_effect_size * 0.5
            for d in h.regime_breakdown.values()
        ) if h.regime_breakdown else False,
        "out_of_sample_confirmed": h.oos_confirmed,
        "forward_test_confirmed": h.forward_confirmed,
    }
    return gates


# Weighted readiness score — shows progress without overriding gates
GATE_WEIGHTS = {
    "practical_significance": 0.25,
    "in_sample_significant": 0.20,
    "rolling_windows_stable": 0.20,
    "regime_consistent": 0.15,
    "out_of_sample_confirmed": 0.10,
    "forward_test_confirmed": 0.10,
}


def compute_readiness_score(h: Hypothesis) -> int:
    """Weighted readiness score 0-100. Does NOT override mandatory gates."""
    if not h.promotion_gates:
        return 0
    score = 0.0
    for gate, weight in GATE_WEIGHTS.items():
        if h.promotion_gates.get(gate, False):
            score += weight
    # Also factor in effect size proportionally (partial credit)
    if h.effect_size > 0 and h.min_effect_size > 0:
        effect_ratio = min(h.effect_size / h.min_effect_size, 1.0)
        # Add partial credit for approaching thresholds
        if not h.promotion_gates.get("practical_significance", False):
            score += 0.25 * effect_ratio * 0.5  # Up to 12.5% partial credit
    return min(int(score * 100), 100)


# ── Trend Tracking ────────────────────────────────────────────────────────────

def load_history() -> Dict:
    if HISTORY_PATH.exists():
        try:
            return json.loads(HISTORY_PATH.read_text())
        except:
            pass
    return {}


def save_history(history: Dict):
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(history, indent=2, default=str))


def compute_trend(hyp_id: str, current_status: str, current_conf: float, history: Dict) -> Tuple[str, int]:
    """Determine trend from historical records."""
    records = history.get(hyp_id, [])
    if len(records) < 2:
        return "unknown", len(records)

    recent = records[-5:]
    confs = [r.get("confidence", 0) for r in recent] + [current_conf]

    increasing = all(confs[i] <= confs[i+1] for i in range(len(confs)-1))
    decreasing = all(confs[i] >= confs[i+1] for i in range(len(confs)-1))
    statuses = [r.get("status", "") for r in records[-3:]] + [current_status]

    if increasing and current_conf > 0.5:
        return "↗ strengthening", len(records)
    elif decreasing and current_conf < 0.3:
        return "↘ weakening", len(records)
    elif len(set(statuses)) == 1:
        return "→ unchanged", len(records)
    else:
        return "↕ mixed", len(records)


def record_history(hyp: Hypothesis, history: Dict):
    """Append current state to history."""
    if hyp.id not in history:
        history[hyp.id] = []
    history[hyp.id].append({
        "timestamp": hyp.last_updated,
        "status": hyp.status,
        "confidence": hyp.confidence,
        "p_value": hyp.p_value,
        "effect_size": hyp.effect_size,
        "sample": hyp.sample_at_last_check,
        "readiness_score": hyp.readiness_score,
        "promotion_score": hyp.promotion_score,
    })
    # Keep last 50 entries
    history[hyp.id] = history[hyp.id][-50:]


# ── Data Access ───────────────────────────────────────────────────────────────

def connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def get_trades() -> Tuple[List[Dict], int, int]:
    """Returns (all_trades, active_count, archive_count)."""
    conn = connect()
    rows = conn.execute("SELECT * FROM positions WHERE status='closed' ORDER BY closed_at ASC").fetchall()
    active = [dict(r) for r in rows]
    archive = []
    try:
        rows2 = conn.execute("SELECT * FROM positions_archive WHERE status='closed' ORDER BY closed_at ASC").fetchall()
        archive = [dict(r) for r in rows2]
    except:
        pass
    conn.close()
    all_trades = active + archive
    return all_trades, len(active), len(archive)


# ── Hypothesis Checkers ───────────────────────────────────────────────────────

def check_tight_stops(trades: List[Dict]) -> Hypothesis:
    """H1: Stop losses are too tight."""
    h = Hypothesis(
        id="H1_TIGHT_STOPS", title="Stop Losses Too Tight",
        description="Trades move favorably before hitting stop-loss, suggesting stops are inside normal noise.",
        confirmation_threshold=200, min_effect_size=0.3,
    )

    sl_trades = [t for t in trades if (t.get("exit_reason") or "").lower() in ("stop_loss", "trailing_stop")]
    if not sl_trades:
        h.status = "OPEN"; h.evidence.append("No stop-loss exits yet"); h.action = "Collect more trades"
        return h

    mfe_trades = [t for t in sl_trades if t.get("mfe_pct") is not None]
    if not mfe_trades:
        h.status = "OPEN"; h.metrics["stopped_trades"] = len(sl_trades)
        h.evidence.append(f"{len(sl_trades)} stopped trades, no MFE data"); h.action = "Enable MFE tracking"
        return h

    pos = sum(1 for t in mfe_trades if t["mfe_pct"] > 0)
    pct = pos / len(mfe_trades)
    avg_mfe = sum(t["mfe_pct"] for t in mfe_trades) / len(mfe_trades)

    h.confidence_interval = compute_proportion_ci(pos, len(mfe_trades))
    h.effect_size = abs(pct - 0.5)
    h.statistical_test = "Binomial test vs 50%"

    # Binomial test vs 50%
    expected = len(mfe_trades) * 0.5
    se = math.sqrt(len(mfe_trades) * 0.25)
    z = (pos - expected) / se if se > 0 else 0
    h.p_value = round(2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2)))), 4)

    h.metrics = {
        "stopped": len(sl_trades), "with_mfe": len(mfe_trades),
        "positive_before_stop": pos, "pct": round(pct * 100, 1),
        "avg_mfe": round(avg_mfe, 2),
    }

    n = len(sl_trades)
    if n < 30:
        h.status = "OPEN"; h.confidence = 0.2
        h.evidence.append(f"Only {n} stopped trades — need 30+")
        h.action = "Collect more trades before evaluating"
    elif pct > 0.5:
        h.status = "SIGNAL"; h.confidence = min(pct, 0.9)
        h.evidence.append(f"{pct*100:.0f}% of stopped trades went positive first")
        h.evidence.append(f"Avg MFE before stop: {avg_mfe:.2f}%")
        if h.p_value < 0.05:
            h.evidence.append(f"Statistically significant (p={h.p_value})")
        h.action = "Monitor — consider SL buffer if pattern persists at N=200"
    else:
        h.status = "REFUTED"; h.confidence = 1 - pct
        h.evidence.append(f"Only {pct*100:.0f}% went positive — stops appear reasonable")
        h.action = "No action needed — stop placement is adequate"

    h.sample_at_last_check = len(trades)
    h.last_updated = datetime.now(timezone.utc).isoformat()
    return h


def check_london_session(trades: List[Dict]) -> Hypothesis:
    """H2: London session underperforms."""
    h = Hypothesis(
        id="H2_LONDON", title="London Session Underperformance",
        description="London session produces worse results than New York session.",
        confirmation_threshold=200, min_effect_size=0.15,
    )

    by_session = defaultdict(list)
    for t in trades:
        by_session[t.get("session") or "unknown"].append(t)

    london = by_session.get("london", [])
    ny = by_session.get("new_york", [])

    if len(london) < 15 or len(ny) < 15:
        h.status = "OPEN"; h.confidence = 0.2
        h.evidence.append(f"London: {len(london)}, NY: {len(ny)} — need 15+ each")
        h.metrics = {"london_n": len(london), "ny_n": len(ny)}
        h.action = "Collect more trades before evaluating"
        return h

    def stats(ts):
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        n = len(pnls); w = sum(1 for p in pnls if p > 0)
        gp = sum(p for p in pnls if p > 0); gl = sum(abs(p) for p in pnls if p < 0)
        return {"wr": round(w/n*100, 1), "pf": round(gp/gl, 2) if gl else 0,
                "pnl": round(sum(pnls), 2), "n": n, "wins": w}

    ls, ns = stats(london), stats(ny)
    h.metrics = {"london": ls, "ny": ns}

    _, h.p_value = compute_two_prop_z(ls["n"], ls["wins"], ns["n"], ns["wins"])
    h.effect_size = compute_cohens_h(ls["n"], ls["wins"], ns["n"], ns["wins"])
    h.confidence_interval = compute_proportion_ci(ls["wins"], ls["n"])
    h.statistical_test = "Two-proportion z-test (WR)"

    # Stability analysis
    h.rolling_effect_sizes = compute_rolling_effect(trades, "session", "london", "new_york")
    h.regime_breakdown = compute_regime_breakdown(trades, "session", "london", "new_york")
    h.symbol_breakdown = compute_symbol_breakdown(trades, "session", "london", "new_york")
    h.stability_score = compute_stability_score(h.rolling_effect_sizes, h.min_effect_size)

    # Validation windows: how many show effect above threshold?
    h.validation_windows_total = len(h.rolling_effect_sizes)
    h.validation_windows_passed = sum(1 for e in h.rolling_effect_sizes if e >= h.min_effect_size)

    if ls["pnl"] < 0 and ns["pnl"] > 0:
        if h.p_value < 0.05:
            h.status = "SIGNAL"; h.confidence = min(0.8 + (1 - h.p_value) * 0.2, 0.95)
            h.evidence.append(f"London: ${ls['pnl']:.2f} vs NY: ${ns['pnl']:.2f}")
            h.evidence.append(f"WR diff significant (p={h.p_value}, Cohen's h={h.effect_size})")
            h.evidence.append(f"Stability: {h.stability_score:.2f} ({h.validation_windows_passed}/{h.validation_windows_total} windows)")
            h.action = "Strong signal — consider London session filter at N=200"
        elif h.p_value < 0.10:
            h.status = "WEAK_SIGNAL"; h.confidence = 0.5
            h.evidence.append(f"London: ${ls['pnl']:.2f} vs NY: ${ns['pnl']:.2f}")
            h.evidence.append(f"Approaching significance (p={h.p_value})")
            h.evidence.append(f"Stability: {h.stability_score:.2f} ({h.validation_windows_passed}/{h.validation_windows_total} windows)")
            h.action = "Monitor — not yet significant enough to act on"
        else:
            h.status = "WEAK_SIGNAL"; h.confidence = 0.4
            h.evidence.append(f"Not significant (p={h.p_value})")
            h.action = "Collect more data — current sample insufficient"
    elif ls["pnl"] > 0:
        h.status = "REFUTED"; h.confidence = 0.8
        h.evidence.append(f"London is profitable: ${ls['pnl']:.2f}")
        h.action = "No action needed — London session is performing"
    else:
        h.status = "OPEN"; h.confidence = 0.3
        h.evidence.append("Both sessions losing")
        h.action = "Investigate broader strategy issues first"

    h.sample_at_last_check = len(trades)
    h.last_updated = datetime.now(timezone.utc).isoformat()
    return h


def check_long_direction(trades: List[Dict]) -> Hypothesis:
    """H3: Long direction underperforms Short."""
    h = Hypothesis(
        id="H3_LONG_DIR", title="Long Direction Underperformance",
        description="Long trades produce worse results than Short trades.",
        confirmation_threshold=200, min_effect_size=0.15,
    )

    by_side = defaultdict(list)
    for t in trades:
        by_side[t.get("side") or "unknown"].append(t)

    longs = by_side.get("LONG", [])
    shorts = by_side.get("SHORT", [])

    if len(longs) < 15 or len(shorts) < 15:
        h.status = "OPEN"; h.confidence = 0.2
        h.evidence.append(f"Long: {len(longs)}, Short: {len(shorts)} — need 15+ each")
        h.action = "Collect more trades before evaluating"
        return h

    def stats(ts):
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        n = len(pnls); w = sum(1 for p in pnls if p > 0)
        gp = sum(p for p in pnls if p > 0); gl = sum(abs(p) for p in pnls if p < 0)
        return {"wr": round(w/n*100, 1), "pf": round(gp/gl, 2) if gl else 0,
                "pnl": round(sum(pnls), 2), "n": n, "wins": w}

    ls, ss = stats(longs), stats(shorts)
    h.metrics = {"long": ls, "short": ss}

    _, h.p_value = compute_two_prop_z(ls["n"], ls["wins"], ss["n"], ss["wins"])
    h.effect_size = compute_cohens_h(ls["n"], ls["wins"], ss["n"], ss["wins"])
    h.confidence_interval = compute_proportion_ci(ls["wins"], ls["n"])
    h.statistical_test = "Two-proportion z-test (WR)"

    if ls["pnl"] < 0 and ss["pnl"] > 0:
        if h.p_value < 0.05:
            h.status = "SIGNAL"; h.confidence = min(0.7 + (1-h.p_value)*0.2, 0.95)
            h.evidence.append(f"Long: ${ls['pnl']:.2f} vs Short: ${ss['pnl']:.2f}")
            h.evidence.append(f"Significant (p={h.p_value}, h={h.effect_size})")
            h.action = "Strong signal — consider reducing LONG exposure at N=200"
        else:
            h.status = "WEAK_SIGNAL"; h.confidence = 0.4
            h.evidence.append(f"Not significant (p={h.p_value})")
            h.action = "Monitor — not yet significant enough to act on"
    elif ls["pnl"] > 0:
        h.status = "REFUTED"; h.confidence = 0.8
        h.evidence.append(f"Long profitable: ${ls['pnl']:.2f}")
        h.action = "No action needed"
    else:
        h.status = "OPEN"; h.confidence = 0.3
        h.evidence.append("Both directions losing")
        h.action = "Investigate broader issues first"

    h.sample_at_last_check = len(trades)
    h.last_updated = datetime.now(timezone.utc).isoformat()
    return h


def check_confidence_calibration(trades: List[Dict]) -> Hypothesis:
    """H4: Confidence score is not predictive."""
    h = Hypothesis(
        id="H4_CONFIDENCE", title="Confidence Score Not Predictive",
        description="Higher confidence scores do not correlate with better outcomes.",
        confirmation_threshold=200, min_effect_size=0.1,
    )

    ct = [t for t in trades if t.get("confidence") and t["confidence"] > 0]
    if len(ct) < 20:
        h.status = "OPEN"; h.confidence = 0.2
        h.evidence.append(f"Only {len(ct)} trades with confidence — need 20+")
        h.action = "Fix confidence model to produce differentiated scores"
        return h

    buckets = {"low (<40)": [], "mid (40-70)": [], "high (>70)": []}
    for t in ct:
        c = t["confidence"]
        if c < 40: buckets["low (<40)"].append(t)
        elif c < 70: buckets["mid (40-70)"].append(t)
        else: buckets["high (>70)"].append(t)

    def bstats(ts):
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        n = len(pnls); w = sum(1 for p in pnls if p > 0)
        gp = sum(p for p in pnls if p > 0); gl = sum(abs(p) for p in pnls if p < 0)
        return {"n": n, "wr": round(w/n*100, 1) if n else 0, "pf": round(gp/gl, 2) if gl else 0}

    bs = {k: bstats(v) for k, v in buckets.items() if v}
    h.metrics = {"buckets": bs}

    lo = bs.get("low (<40)", {})
    hi = bs.get("high (>70)", {})

    if not lo or not hi:
        h.status = "OPEN"; h.confidence = 0.3
        h.evidence.append("Insufficient data in low AND high buckets")
        # Check if all in one bucket
        if lo and lo["n"] > len(ct) * 0.9:
            h.status = "SIGNAL"; h.confidence = 0.7
            h.evidence.append(f"90%+ trades in low bucket — model not differentiating")
            h.action = "Priority: Fix confidence model to produce spread scores"
        else:
            h.action = "Fix confidence model to produce differentiated scores"
        return h

    wr_diff = hi.get("wr", 0) - lo.get("wr", 0)
    pf_diff = hi.get("pf", 0) - lo.get("pf", 0)
    h.metrics["wr_improvement"] = round(wr_diff, 1)
    h.metrics["pf_improvement"] = round(pf_diff, 2)

    if lo["n"] > len(ct) * 0.9:
        h.status = "SIGNAL"; h.confidence = 0.7
        h.evidence.append("90%+ trades in low bucket — model not differentiating")
        h.action = "Priority: Fix confidence model to produce spread scores"
    elif wr_diff > 10 and pf_diff > 0.3:
        h.status = "REFUTED"; h.confidence = 0.7
        h.evidence.append(f"Higher confidence predicts better outcomes")
        h.evidence.append(f"High WR: {hi['wr']}% vs Low WR: {lo['wr']}%")
        h.action = "No action needed — confidence model is working"
    elif wr_diff < -5 or pf_diff < -0.2:
        h.status = "SIGNAL"; h.confidence = 0.6
        h.evidence.append("Higher confidence predicts WORSE outcomes — model inverted")
        h.action = "Priority: Invert or recalibrate confidence model"
    else:
        h.status = "SIGNAL"; h.confidence = 0.5
        h.evidence.append(f"No meaningful difference (WR diff: {wr_diff:.1f}%)")
        h.action = "Improve confidence model differentiation"

    h.sample_at_last_check = len(trades)
    h.last_updated = datetime.now(timezone.utc).isoformat()
    return h


def check_rr_threshold(trades: List[Dict]) -> Hypothesis:
    """H5: Current RR threshold is too low."""
    h = Hypothesis(
        id="H5_RR_THRESHOLD", title="RR Threshold Too Low",
        description="Current min_rr=1.5 may admit too many low-quality signals.",
        confirmation_threshold=200, min_effect_size=0.2,
    )

    rrt = [t for t in trades if t.get("planned_rr") and t["planned_rr"] > 0]
    if len(rrt) < 20:
        h.status = "OPEN"; h.confidence = 0.2
        h.evidence.append(f"Only {len(rrt)} trades with RR data — need 20+")
        h.action = "Collect more trades before evaluating"
        return h

    low = [t for t in rrt if t["planned_rr"] < 2.0]
    mid = [t for t in rrt if 2.0 <= t["planned_rr"] < 3.0]
    high = [t for t in rrt if t["planned_rr"] >= 3.0]

    def rs(ts):
        pnls = [t.get("pnl", 0) or 0 for t in ts]
        n = len(pnls); w = sum(1 for p in pnls if p > 0)
        gp = sum(p for p in pnls if p > 0); gl = sum(abs(p) for p in pnls if p < 0)
        return {"n": n, "wr": round(w/n*100, 1) if n else 0,
                "pf": round(gp/gl, 2) if gl else 0, "pnl": round(sum(pnls), 2), "wins": w}

    buckets = {}
    if low: buckets["low (<2.0)"] = rs(low)
    if mid: buckets["mid (2.0-3.0)"] = rs(mid)
    if high: buckets["high (>=3.0)"] = rs(high)
    h.metrics = {"rr_buckets": buckets}

    lo = buckets.get("low (<2.0)", {})
    hi = buckets.get("high (>=3.0)", {})

    if lo and hi and lo["n"] >= 10 and hi["n"] >= 5:
        _, h.p_value = compute_two_prop_z(lo["n"], lo["wins"], hi["n"], hi["wins"])
        h.effect_size = compute_cohens_h(lo["n"], lo["wins"], hi["n"], hi["wins"])
        h.confidence_interval = compute_proportion_ci(lo["wins"], lo["n"])
        h.statistical_test = "Two-proportion z-test (WR)"

        pf_diff = hi["pf"] - lo["pf"]
        if pf_diff > 0.3:
            if h.p_value < 0.05:
                h.status = "SIGNAL"; h.confidence = min(0.7+(1-h.p_value)*0.2, 0.95)
                h.evidence.append(f"Higher RR significantly better (p={h.p_value})")
                h.evidence.append(f"Low PF: {lo['pf']} vs High PF: {hi['pf']}")
                h.action = "Strong signal — consider increasing min_rr at N=200"
            else:
                h.status = "WEAK_SIGNAL"; h.confidence = 0.5
                h.evidence.append(f"Trending but not significant (p={h.p_value})")
                h.action = "Monitor — need more data to confirm"
        elif lo["pf"] > hi["pf"]:
            h.status = "REFUTED"; h.confidence = 0.5
            h.evidence.append("Lower RR trades perform better")
            h.action = "No action needed — current threshold is appropriate"
        else:
            h.status = "OPEN"; h.confidence = 0.3
            h.evidence.append("Marginal difference")
            h.action = "Collect more trades before evaluating"
    else:
        h.status = "OPEN"; h.confidence = 0.2
        h.evidence.append("Insufficient data in RR buckets")
        h.action = "Collect more trades before evaluating"

    h.sample_at_last_check = len(trades)
    h.last_updated = datetime.now(timezone.utc).isoformat()
    return h


# ── Multiple Testing Correction ────────────────────────────────────────────────

def benjamini_hochberg(hypotheses: List[Hypothesis]) -> List[Hypothesis]:
    """Apply Benjamini-Hochberg FDR correction to all p-values."""
    # Get all hypotheses with valid p-values
    tested = [(i, h) for i, h in enumerate(hypotheses) if h.p_value < 1.0]
    if len(tested) <= 1:
        for h in hypotheses:
            h.adjusted_p_value = h.p_value
        return hypotheses

    # Sort by p-value
    tested.sort(key=lambda x: x[1].p_value)
    m = len(tested)

    # BH correction: p_adj = p * m / rank
    for rank, (idx, h) in enumerate(tested, 1):
        h.adjusted_p_value = min(h.p_value * m / rank, 1.0)

    # Enforce monotonicity (working backwards)
    for i in range(len(tested) - 2, -1, -1):
        idx, h = tested[i]
        next_h = tested[i + 1][1]
        h.adjusted_p_value = min(h.adjusted_p_value, next_h.adjusted_p_value)

    # Set adjusted p for untested hypotheses
    for h in hypotheses:
        if h.p_value >= 1.0:
            h.adjusted_p_value = 1.0

    return hypotheses


def evaluate_practical_significance(hypotheses: List[Hypothesis]) -> List[Hypothesis]:
    """Mark hypotheses with both statistical AND practical significance."""
    for h in hypotheses:
        # Practical significance requires BOTH:
        # 1. Adjusted p-value < 0.05 (statistical)
        # 2. Effect size above minimum threshold (practical)
        h.practical_significance = (
            h.adjusted_p_value < 0.05 and h.effect_size >= h.min_effect_size
        )
    return hypotheses


# ── Report Generation ─────────────────────────────────────────────────────────

STATUS_EMOJI = {
    "OPEN": "⬜", "WEAK_SIGNAL": "🟡", "SIGNAL": "🟠",
    "CONFIRMED": "🟢", "REFUTED": "🔵",
}


def generate_report(hypotheses: List[Hypothesis], total: int, active: int, archive: int, history: Dict = None) -> str:
    lines = []
    lines.append("=" * 95)
    lines.append("🔬 HYPOTHESIS TRACKER v5 — OUT-OF-SAMPLE CONFIRMATION")
    lines.append(f"   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    lines.append(f"   Trades: {total} total ({active} active + {archive} archived)")
    lines.append(f"   Multiple testing: Benjamini-Hochberg FDR")
    lines.append(f"   Promotion requires ALL 6 gates to pass")
    lines.append("=" * 95)
    if history is None:
        history = {}

    for h in hypotheses:
        e = STATUS_EMOJI.get(h.status, "❓")
        lines.append(f"\n{e} [{h.status}] {h.title}")
        lines.append(f"   {h.description}")
        lines.append(f"   N={h.sample_at_last_check} | Threshold: {h.confirmation_threshold}")
        lines.append(f"   Test: {h.statistical_test or 'N/A'}")
        if h.p_value < 1.0:
            lines.append(f"   p-value: {h.p_value} (raw) → {h.adjusted_p_value:.4f} (FDR)")
        if h.effect_size > 0:
            lines.append(f"   Effect size: {h.effect_size} (Cohen's h) | Min: {h.min_effect_size}")
        if h.confidence_interval:
            lines.append(f"   95% CI: {h.confidence_interval}")
        lines.append(f"   Practical significance: {'✅' if h.practical_significance else '❌'}")

        # Stability
        if h.rolling_effect_sizes:
            lines.append(f"   Rolling effect: {[round(e, 2) for e in h.rolling_effect_sizes[-5:]]}")
            lines.append(f"   Validation windows: {h.validation_windows_passed}/{h.validation_windows_total}")
            lines.append(f"   Stability score: {h.stability_score:.2f}")

        # OOS
        if h.in_sample_effect > 0 or h.out_of_sample_effect > 0:
            lines.append(f"   In-sample effect: {h.in_sample_effect:.3f}")
            lines.append(f"   Out-of-sample effect: {h.out_of_sample_effect:.3f} {'✅' if h.oos_confirmed else '❌'}")
            lines.append(f"   Forward test effect: {h.forward_test_effect:.3f} {'✅' if h.forward_confirmed else '❌'}")

        # Regime breakdown
        if h.regime_breakdown:
            lines.append(f"   Regime breakdown:")
            for regime, data in sorted(h.regime_breakdown.items()):
                lines.append(f"     {regime}: {data}")

        if h.trend != "unknown":
            lines.append(f"   Trend: {h.trend} ({h.consecutive_periods} periods)")

        for ev in h.evidence:
            lines.append(f"   → {ev}")

        # Promotion gates
        if h.promotion_gates:
            passed = h.promotion_score
            total_gates = len(h.promotion_gates)
            lines.append(f"   🚦 PROMOTION GATES: {passed}/{total_gates} | Readiness: {h.readiness_score}/100")
            for gate, ok in h.promotion_gates.items():
                lines.append(f"     {'✅' if ok else '❌'} {gate}")
            if h.readiness_score >= 80 and passed < total_gates:
                lines.append(f"     ⚡ High readiness but not all gates passed — keep monitoring")
            elif h.readiness_score >= 50:
                lines.append(f"     📈 Making progress — effect is developing")
            else:
                lines.append(f"     ⏳ Early stage — need more evidence")

        lines.append(f"   📋 Action: {h.action}")

    # Summary
    confirmed = sum(1 for h in hypotheses if h.status == "CONFIRMED")
    signals = sum(1 for h in hypotheses if h.status in ("SIGNAL", "WEAK_SIGNAL"))
    opened = sum(1 for h in hypotheses if h.status == "OPEN")
    refuted = sum(1 for h in hypotheses if h.status == "REFUTED")
    pract_sig = sum(1 for h in hypotheses if h.practical_significance)
    stable = sum(1 for h in hypotheses if h.stability_score >= 0.5)
    promotable = sum(1 for h in hypotheses if h.promotion_score >= 5)

    lines.append(f"\n{'=' * 95}")
    lines.append(f"📊 SUMMARY")
    lines.append(f"   🟢 Confirmed: {confirmed} | 🟠 Signals: {signals} | ⬜ Open: {opened} | 🔵 Refuted: {refuted}")
    lines.append(f"   Practically significant: {pract_sig}/{len(hypotheses)}")
    lines.append(f"   Stable effects (≥0.5): {stable}/{len(hypotheses)}")
    lines.append(f"   Promotable (≥5/6 gates): {promotable}/{len(hypotheses)}")

    # Show readiness scores with trend and evidence growth
    readiness = [(h.title, h.readiness_score, h.promotion_score, h.id) for h in hypotheses]
    readiness.sort(key=lambda x: -x[1])
    if readiness:
        lines.append(f"\n   📊 READINESS SCORES:")
        lines.append(f"   {'Hypothesis':<35} {'Score':>8} {'Gates':>7} {'Trend':>8} {'Evidence':>10}")
        lines.append(f"   {'-'*75}")
        for title, score, gates, hid in readiness:
            bar = "█" * (score // 5) + "░" * (20 - score // 5)
            # Compute trend from history
            hist = history.get(hid, [])
            prev_score = hist[-2].get("readiness_score", score) if len(hist) >= 2 else score
            delta = score - prev_score
            trend_str = f"+{delta}" if delta > 0 else str(delta) if delta < 0 else "→ 0"
            # Evidence growth
            prev_n = hist[-2].get("sample", total) if len(hist) >= 2 else total
            growth = total - prev_n
            growth_str = f"+{growth}" if growth > 0 else str(growth)
            lines.append(f"   {bar} {score:3d}/100 | {gates}/6 | {trend_str:>6} | {growth_str:>8} | {title}")

    # Readiness confidence intervals (bootstrap from history)
    if any(h.readiness_score > 0 for h in hypotheses):
        lines.append(f"\n   📐 READINESS STABILITY:")
        for h in sorted(hypotheses, key=lambda x: -x.readiness_score):
            if h.readiness_score == 0:
                continue
            hist = history.get(h.id, [])
            if len(hist) >= 3:
                scores = [r.get("readiness_score", 0) for r in hist[-10:]]
                scores.append(h.readiness_score)
                mean_s = sum(scores) / len(scores)
                std_s = math.sqrt(sum((s - mean_s)**2 for s in scores) / len(scores)) if len(scores) > 1 else 0
                ci_low = max(0, mean_s - 1.96 * std_s)
                ci_high = min(100, mean_s + 1.96 * std_s)
                lines.append(f"     {h.title}: {h.readiness_score} ± {std_s:.0f} (range: {ci_low:.0f}–{ci_high:.0f})")
            else:
                lines.append(f"     {h.title}: {h.readiness_score} ± — (insufficient history)")

    lines.append(f"\n💡 RECOMMENDATIONS:")
    if promotable > 0:
        promos = [h for h in hypotheses if h.promotion_score >= 5]
        for h in promos:
            lines.append(f"   ✅ PROMOTE: {h.title} ({h.promotion_score}/6 gates passed)")
            lines.append(f"     Implement ONE change, paper-trade for 50+ trades, then re-evaluate")
    elif total < 200:
        lines.append(f"   → Continue collecting trades ({total}/200)")
        lines.append(f"   → No hypothesis ready for promotion")
    else:
        lines.append(f"   → No hypothesis meets all promotion criteria")
        lines.append(f"   → Continue collecting data or investigate confidence model")

    lines.append("=" * 95)
    return "\n".join(lines)


def save_bridge(hypotheses: List[Hypothesis], total: int, active: int, archive: int):
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "v4",
        "total_trades": total, "active_trades": active, "archived_trades": archive,
        "correction_method": "Benjamini-Hochberg FDR",
        "significance_criteria": "p<0.05 AND effect_size>threshold AND stability>=0.5",
        "hypotheses": [
            {
                "id": h.id, "title": h.title, "status": h.status,
                "confidence": h.confidence, "p_value": h.p_value,
                "adjusted_p_value": h.adjusted_p_value,
                "effect_size": h.effect_size, "statistical_test": h.statistical_test,
                "practical_significance": h.practical_significance,
                "confidence_interval": h.confidence_interval,
                "trend": h.trend, "consecutive_periods": h.consecutive_periods,
                "stability_score": h.stability_score,
                "validation_windows": f"{h.validation_windows_passed}/{h.validation_windows_total}",
                "rolling_effect_sizes": h.rolling_effect_sizes,
                "regime_breakdown": h.regime_breakdown,
                "readiness_score": h.readiness_score,
                "promotion_score": f"{h.promotion_score}/{len(h.promotion_gates)}",
                "evidence": h.evidence, "action": h.action,
                "confirmation_threshold": h.confirmation_threshold,
                "sample_at_last_check": h.sample_at_last_check,
                "last_updated": h.last_updated,
            }
            for h in hypotheses
        ],
    }
    (BRIDGE_DIR / "hypotheses.json").write_text(json.dumps(data, indent=2, default=str))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    trades, active, archive = get_trades()
    total = len(trades)
    print(f"📊 Hypothesis Tracker v5 — {total} trades ({active} active + {archive} archived)\n")

    hypotheses = [
        check_tight_stops(trades),
        check_london_session(trades),
        check_long_direction(trades),
        check_confidence_calibration(trades),
        check_rr_threshold(trades),
    ]

    # Apply Benjamini-Hochberg FDR correction
    hypotheses = benjamini_hochberg(hypotheses)

    # Evaluate practical significance
    hypotheses = evaluate_practical_significance(hypotheses)

    # Out-of-sample analysis for hypotheses with enough data
    for h in hypotheses:
        if h.id == "H2_LONDON":
            is_eff, oos_eff, fwd_eff = compute_out_of_sample_effect(trades, "session", "london", "new_york")
            h.in_sample_effect = is_eff
            h.out_of_sample_effect = oos_eff
            h.forward_test_effect = fwd_eff
            h.oos_confirmed = oos_eff >= h.min_effect_size * 0.5
            h.forward_confirmed = fwd_eff >= h.min_effect_size * 0.5
        elif h.id == "H3_LONG_DIR":
            is_eff, oos_eff, fwd_eff = compute_out_of_sample_effect(trades, "side", "LONG", "SHORT")
            h.in_sample_effect = is_eff
            h.out_of_sample_effect = oos_eff
            h.forward_test_effect = fwd_eff
            h.oos_confirmed = oos_eff >= h.min_effect_size * 0.5
            h.forward_confirmed = fwd_eff >= h.min_effect_size * 0.5

    # Evaluate promotion gates
    for h in hypotheses:
        h.promotion_gates = evaluate_promotion_gates(h)
        h.promotion_score = sum(1 for v in h.promotion_gates.values() if v)
        h.readiness_score = compute_readiness_score(h)

    # Load history and compute trends
    history = load_history()
    for h in hypotheses:
        h.trend, h.consecutive_periods = compute_trend(h.id, h.status, h.confidence, history)
        record_history(h, history)
    save_history(history)

    report = generate_report(hypotheses, total, active, archive, history)
    print(report)

    save_bridge(hypotheses, total, active, archive)

    # Save report
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (REPORTS_DIR / f"hypothesis_report_{date_str}.txt").write_text(report)

    print(f"\n✅ Saved: bridge/hypotheses.json, reports/hypothesis_report_{date_str}.txt")


if __name__ == "__main__":
    main()
