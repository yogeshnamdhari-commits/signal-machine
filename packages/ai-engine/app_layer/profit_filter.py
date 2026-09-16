"""
App Profit Filter v2 — diagnostic admission layer between LIVE SHEET and trade execution.

READ-ONLY sources: EMA V5, Smart Money, and every LIVE SHEET field.
This layer ONLY decides whether an existing signal should be executed, based on
agreement between the signal side and the LIVE SHEET (CVD / Delta / Flow /
Volume / OI / Funding / Liquidity / Sweep / FVG / Regime).

Mode:
    diagnostic (default):  every ACCEPT/WATCH/REJECT/NO_APP_DECISION decision is
                           recorded, nothing is blocked. Use this to measure how
                           many profitable trades would disappear before enabling
                           the filter in blocking mode.
    blocking:              non-ACCEPT decisions block the position open.

Scoring — App Quality Score v2 (100 pts, side-aware):
    CVD agreement       20
    OI / Funding        15
    Delta agreement     15
    Flow agreement      15
    Liquidity condition 10
    Sweep / FVG         10
    Regime confirmation 10
    Volume (modifier)    5
    ────────────────────100

v2 changes over v1:
  1. Data-freshness gate: stale LIVE SHEET data (OI/orderbook/CVD/flow) yields
     NO_APP_DECISION — a data-quality rejection, NEVER a trading rejection.
  2. Side-aware weights (CVD +5, OI/Funding +5, Sweep/FVG +5; Regime −10,
     Volume −5, Liquid/Sweep rebalanced). Every component scores the signal
     side against the LIVE SHEET (SHORT flips the bias axis).
  3. Admission bands: 80-100 ACCEPT · 70-79 ACCEPT only if no hard conflict ·
     60-69 WATCH · <60 REJECT.
  4. Hard-conflict = STRONG side-aware directional contradiction (regime, CVD,
     delta, flow, OI/funding, or liquidation cascade against the position).
     Volume is a confirmation modifier only and is NEVER a hard rejection.
  5. Blocking remains OFF by default (diagnostic mode). LIVE APP PERFORMANCE
     compares ALL vs ACCEPTED vs REJECTED vs WATCH before it is ever enabled.

Grades:
    80-100  A+  ACCEPT
    70-79   A   ACCEPT (unless hard conflict)
    60-69   B   WATCH / selective
    <60     C   REJECT
    STALE data → SD (NO_APP_DECISION / data-quality, not a trade decision)

Regime-confidence class (diagnostic-only unless enforce_regime_conf):
    >=70% HIGH · 60-69% ACCEPTABLE · 50-59% WATCH · <50% REJECT
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

from config.settings import config, DATA_DIR

# ── Defaults / paths ─────────────────────────────────────────────────────────
BRIDGE_PATH = DATA_DIR / "bridge" / "market_data.json"
LOG_PATH = DATA_DIR / "logs" / "app_profit_filter.jsonl"

SCORE_VERSION = "v2"
SCORE_VERSION_V3 = "v3"

# ── Weight breakdown (must sum to 1.0) ───────────────────────────────────────
WEIGHTS = {
    "cvd": 0.20,
    "oi_funding": 0.15,
    "delta": 0.15,
    "flow": 0.15,
    "liquidity": 0.10,
    "sweep_fvg": 0.10,
    "regime": 0.10,
    "volume": 0.05,
}
WEIGHT_POINTS = {k: round(v * 100) for k, v in WEIGHTS.items()}

# v3 weights (evidence-based): Delta becomes the strongest directional
# confirmation (20), Regime drops to 5 (regime direction is already baked into
# the signal side; overweighting it double-counts). Liquidity/Sweep hold.
WEIGHTS_V3 = {
    "cvd": 0.20,
    "oi_funding": 0.15,
    "delta": 0.20,
    "flow": 0.15,
    "liquidity": 0.10,
    "sweep_fvg": 0.10,
    "regime": 0.05,
    "volume": 0.05,
}
assert abs(sum(WEIGHTS_V3.values()) - 1.0) < 1e-9
WEIGHT_POINTS_V3 = {k: round(v * 100) for k, v in WEIGHTS_V3.items()}

# v3 asymmetric conflict penalties (score points, side-aware):
# For LONG: Delta strongly SHORT = strong penalty, CVD/OI-Funding/Flow SHORT =
# moderate, Volume SHORT = small. SHORT is the mirror. A component that is
# merely NEUTRAL is NOT a conflict (only an absence of confirmation), so it
# never triggers a penalty — that prevents "not agreeing" from destroying an
# otherwise strong setup (the operator's core anti-curve-fit rule).
CONFLICT_PENALTY_V3 = {
    "delta": 15.0,      # strongest directional signal — hard penalty
    "regime": 10.0,     # regime against the position
    "cvd": 8.0,         # CVD against the position
    "oi_funding": 8.0,  # OI/Funding crowded against the position
    "flow": 8.0,        # flow against the position
    "volume": 4.0,      # volume against = small/moderate modifier only
    "liquidity": 6.0,   # cascade against = penalty (not directional core)
    "sweep_fvg": 4.0,   # sweep/FVG against = weak modifier
}
# v3 HARD_CONFLICT veto: two+ STRONG directional contradictions (regime/CVD/
# Delta/Flow/OI-Funding) in the SAME direction against the position veto even a
# high raw score (REJECT_HARD_CONFLICT overrides 80+). A single conflict never
# vetoes — only severe multi-component disagreement (operator spec #6).
HARD_CONFLICT_VETO_V3 = ("regime", "cvd", "delta", "flow", "oi_funding")
HARD_CONFLICT_VETO_MIN = 2       # number of strong directional conflicts to veto
HARD_CONFLICT_VETO_SCORE = 45.0  # a component scoring below this, while conflicted,
                                 # counts as a STRONG directional contradiction

# ── Bias strength maps (0-100) ───────────────────────────────────────────────
BIAS_MAP = {
    "strong_bullish": 100, "bullish": 75, "buy": 75, "taker_buy": 75,
    "neutral": 50, "balanced": 50, "neutral_oi": 50, "ranging": 50,
    "bearish": 25, "sell": 25, "taker_sell": 25, "strong_bearish": 0,
    "": 40, "none": 40,
}

CVD_BULLISH = {"bullish", "strong_bullish", "buy"}
CVD_BEARISH = {"bearish", "strong_bearish", "sell"}

BULLISH_REGIMES = {
    "trending_up", "trending_bull", "breakout", "breakout_up", "reversal_up",
    "impulse_up", "rally", "strong_uptrend", "bull", "uptrend",
}
BEARISH_REGIMES = {
    "trending_down", "trending_bear", "breakout_down", "reversal_down",
    "impulse_down", "crash", "strong_downtrend", "bear", "downtrend",
}
NEUTRAL_REGIMES = {"range", "ranging", "compression", "", "unknown", "neutral"}

LIQ_MAP = {"low": 100, "medium": 60, "high": 20, "": 50}


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _num(row: Dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        v = row.get(key, default)
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _bias(row: Dict[str, Any], key: str, default: str = "neutral") -> str:
    return str(row.get(key, default) or default).lower()


# ── Data-freshness gate (v2 #1) ──────────────────────────────────────────────

def _row_timestamp(row: Dict[str, Any]) -> Optional[float]:
    """Best-effort freshness stamp for a LIVE SHEET row (row timestamp)."""
    ts = row.get("timestamp")
    if ts:
        try:
            return float(ts)
        except (TypeError, ValueError):
            return None
    return None


def _freshness_check(row: Dict[str, Any], fresh_flags: Optional[Dict[str, bool]] = None,
                     gated_fields: Optional[set] = None):
    """Determine whether the LIVE SHEET row is fresh enough to score.

    fresh_flags maps scored fields to freshness (True = fresh), sourced by the
    engine from DataQualityValidator. Any gated field flagged False (stale or
    never seen) forces a data-quality rejection — stale OI/funding/klines must
    never masquerade as a fresh trading signal.

    gated_fields: which engine-side fields are scored inputs for this scoring
    version. v2 gates OI/funding/klines only (orderbook/trade-stream staleness
    is informational, dashboard-only). v3 gates ALL scored fields — price
    (market_data), OI, funding, kline and orderbook (operator spec #2).

    Returns a dict:
        fresh    bool   True when all freshness signals are acceptable
        age_sec  float  seconds since the row snapshot (None if unknown)
        level    str    FRESH | STALE | UNVERIFIED
        fields   dict   per-field staleness flags (bridge + engine-side)
        reason   str    short machine reason for the gate
    """
    stale_fields: Dict[str, str] = {}
    # Bridge-flagged validity (flow volume, etc.). False ⇒ the field was NOT
    # freshly computed for this snapshot — stale by definition.
    for key in ("flow_vol_valid",):
        if row.get(key) is False or str(row.get(key, "")).lower() == "false":
            stale_fields[key] = "false"
    lvl_raw = str(row.get("data_quality_level", "") or "").lower()
    if lvl_raw and lvl_raw != "fresh":
        stale_fields["data_quality_level"] = lvl_raw
    # Engine-side per-field staleness (DataQualityValidator): a scored input
    # that is stale (or never seen) is gated as a data-quality condition.
    if fresh_flags:
        for field, is_fresh in fresh_flags.items():
            if gated_fields is not None and field not in gated_fields:
                continue
            if is_fresh is False:
                stale_fields[field] = "stale"

    now = time.time()
    ts = _row_timestamp(row)
    if ts is None:
        return {
            "fresh": False, "age_sec": None, "level": "UNVERIFIED",
            "fields": stale_fields, "reason": "NO_FRESHNESS_STAMP",
        }
    age = max(0.0, now - ts)
    max_age = config.profit_filter.max_data_age_sec
    if not config.profit_filter.require_freshness:
        return {
            "fresh": True, "age_sec": age, "level": "FRESH",
            "fields": stale_fields, "reason": "",
        }
    if age > max_age:
        return {
            "fresh": False, "age_sec": age, "level": "STALE",
            "fields": stale_fields, "reason": "DATA_STALE",
        }
    if stale_fields:
        return {
            "fresh": False, "age_sec": age, "level": "STALE",
            "fields": stale_fields, "reason": "DATA_STALE",
        }
    return {
        "fresh": True, "age_sec": age, "level": "FRESH",
        "fields": stale_fields, "reason": "",
    }


# ── LIVE SHEET access ────────────────────────────────────────────────────────

def load_live_sheet_rows(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Read the LIVE SHEET bridge file → {symbol: row}.

    This is the exact data rendered by the dashboard's 27-column Live Sheet.
    """
    p = Path(path) if path else BRIDGE_PATH
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.debug("ProfitFilter: LIVE SHEET read failed ({}): {}", p, e)
        return {}
    rows = raw.get("rows", []) if isinstance(raw, dict) else raw
    return {r.get("symbol"): r for r in rows if isinstance(r, dict) and r.get("symbol")}


def get_live_sheet_row(symbol: str, path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    return load_live_sheet_rows(path).get(symbol)


# ── Per-component scoring ────────────────────────────────────────────────────

def _score_regime(side: str, row: Dict[str, Any]) -> Dict[str, Any]:
    conf = _clamp(_num(row, "regime_confidence_pct", 50))
    regime = _bias(row, "regime")
    aligned = (side == "LONG" and regime in BULLISH_REGIMES) or (
        side == "SHORT" and regime in BEARISH_REGIMES)
    opposed = (side == "LONG" and regime in BEARISH_REGIMES) or (
        side == "SHORT" and regime in BULLISH_REGIMES)
    # Soften: regime DIRECTION confirms/contradicts; regime confidence only
    # scales it. Low confidence alone must NOT halve the whole App score
    # (regime-confidence classification is diagnostic-only per spec).
    if aligned:
        score = 100 * (0.5 + 0.5 * conf / 100.0)
    elif regime in NEUTRAL_REGIMES:
        score = 60 * (0.5 + 0.5 * conf / 100.0)
    else:
        score = 25 * (0.5 + 0.5 * conf / 100.0)
    score = _clamp(score)
    cls = "HIGH" if conf >= 70 else ("ACCEPTABLE" if conf >= 60 else (
        "WATCH" if conf >= 50 else "REJECT"))
    detail = f"regime={regime or '?'} conf={conf:.0f}%"
    return {
        "score": round(score, 1), "max": WEIGHT_POINTS["regime"],
        "agreed": score >= 60, "conflict": bool(opposed) and conf >= 55,
        "detail": detail, "regime": regime, "regime_class": cls,
        "regime_conf": round(conf, 1),
    }


def _score_cvd(side: str, row: Dict[str, Any]) -> Dict[str, Any]:
    cvd = _bias(row, "cvd_bias")
    base = BIAS_MAP.get(cvd, 40)
    if side == "SHORT":
        base = 100 - base
    conflict = bool(cvd in (CVD_BEARISH if side == "LONG" else CVD_BULLISH))
    return {
        "score": round(base, 1), "max": WEIGHT_POINTS["cvd"],
        "agreed": base >= 60, "conflict": conflict,
        "detail": f"cvd_bias={cvd}",
    }


def _score_delta(side: str, row: Dict[str, Any]) -> Dict[str, Any]:
    ratio = _num(row, "buy_sell_ratio", 0.5)
    net = _num(row, "net_delta", 0)
    # ratio/(1+ratio) maps 0.5→50, 1→50 (see below), large→100
    ratio_score = 100 * (ratio / (1.0 + ratio)) if ratio >= 0 else 50.0
    if side == "SHORT":
        ratio_score = 100 - ratio_score
    conflict = bool((side == "LONG" and ratio < 0.6) or (
        side == "SHORT" and ratio > 1.4))
    detail = f"buy_sell_ratio={ratio:.3f} net_delta={net:,.0f}"
    return {
        "score": round(_clamp(ratio_score), 1), "max": WEIGHT_POINTS["delta"],
        "agreed": ratio_score >= 60, "conflict": conflict,
        "detail": detail,
    }


def _score_flow(side: str, row: Dict[str, Any]) -> Dict[str, Any]:
    flow = _bias(row, "flow_signal")
    base = BIAS_MAP.get(flow, 40)
    if side == "SHORT":
        base = 100 - base
    conflict = bool((flow == "sell" and side == "LONG") or (
        flow == "buy" and side == "SHORT"))
    return {
        "score": round(base, 1), "max": WEIGHT_POINTS["flow"],
        "agreed": base >= 60, "conflict": conflict,
        "detail": f"flow_signal={flow}",
    }


def _score_volume(side: str, row: Dict[str, Any]) -> Dict[str, Any]:
    vb = _bias(row, "vol_bias")
    base = BIAS_MAP.get(vb, 40)
    if side == "SHORT":
        base = 100 - base
    conflict = bool((vb == "sell" and side == "LONG") or (
        vb == "buy" and side == "SHORT"))
    return {
        "score": round(base, 1), "max": WEIGHT_POINTS["volume"],
        "agreed": base >= 60, "conflict": conflict,
        "detail": f"vol_bias={vb}",
    }


def _score_oi_funding(side: str, row: Dict[str, Any]) -> Dict[str, Any]:
    oi = _bias(row, "oi_bias")
    oi_score = BIAS_MAP.get(oi, 50)
    if side == "SHORT":
        oi_score = 100 - oi_score
    funding = _num(row, "funding", 0)  # percent (e.g. 0.006)
    fz = _num(row, "funding_z", 0)
    # Crowding logic: |funding|>0.1% is extreme.
    if abs(funding) > 0.1:
        if side == "LONG":
            funding_score = 30 if funding > 0 else 75
        else:
            funding_score = 30 if funding < 0 else 75
    else:
        funding_score = 60
    oi_conflict = bool((side == "LONG" and oi in CVD_BEARISH) or (
        side == "SHORT" and oi in CVD_BULLISH))
    fund_conflict = bool(
        (side == "LONG" and funding > 0.2) or (side == "SHORT" and funding < -0.2))
    score = 0.6 * oi_score + 0.4 * funding_score
    detail = f"oi_bias={oi} funding={funding:.4f}% z={fz}"
    return {
        "score": round(score, 1), "max": WEIGHT_POINTS["oi_funding"],
        "agreed": score >= 60, "conflict": oi_conflict or fund_conflict,
        "detail": detail,
    }


def _score_liquidity(side: str, row: Dict[str, Any]) -> Dict[str, Any]:
    level = _bias(row, "liq_risk_level", "low")
    # liq_risk_level is a PERCENTILE rank across symbols — "high" just means
    # top-~30% risk today. Treat it as a penalty, not a directional conflict.
    score = float({"low": 100, "medium": 70, "high": 35}.get(level, 50))
    cascade = bool(row.get("cascade_active"))
    cascade_side = _bias(row, "cascade_side")
    cascade_conflict = bool(
        cascade and ((cascade_side == "long" and side == "LONG") or (
            cascade_side == "short" and side == "SHORT")))
    if cascade_conflict:
        score -= 35
    conflict = bool(cascade_conflict)  # cascade AGAINST position = hard conflict
    detail = f"liq_risk_level={level}"
    if cascade:
        detail += f" cascade={cascade_side}"
    return {
        "score": round(_clamp(score), 1), "max": WEIGHT_POINTS["liquidity"],
        "agreed": score >= 60, "conflict": conflict,
        "detail": detail,
    }


def _score_sweep_fvg(side: str, row: Dict[str, Any]) -> Dict[str, Any]:
    detected = bool(row.get("sweep_detected"))
    sdir = _bias(row, "sweep_direction")
    if not detected or sdir not in ("up", "down"):
        sweep_score = 50
    elif sdir == "down" and side == "LONG" or sdir == "up" and side == "SHORT":
        sweep_score = 100
    else:
        sweep_score = 30
    fvg_align = _bias(row, "fvg_alignment", "neutral")
    fvg_score = _clamp(_num(row, "fvg_score", 50))
    if side == "LONG" and fvg_align in ("bullish", "up"):
        fvg_base = fvg_score
    elif side == "SHORT" and fvg_align in ("bearish", "down"):
        fvg_base = fvg_score
    else:
        fvg_base = 100 - fvg_score if fvg_align in ("bullish", "up", "bearish", "down") else 50
    score = 0.5 * sweep_score + 0.5 * fvg_base
    conflict = bool(detected and sdir in ("up", "down") and not (
        (sdir == "down" and side == "LONG") or (sdir == "up" and side == "SHORT")))
    detail = f"sweep={'%s/%s' % (sdir, 'Y' if detected else 'N')} fvg={fvg_align}/{fvg_score:.0f}"
    return {
        "score": round(score, 1), "max": WEIGHT_POINTS["sweep_fvg"],
        "agreed": score >= 60, "conflict": conflict,
        "detail": detail,
    }


# ── Decision ─────────────────────────────────────────────────────────────────

def grade_from_score(score: float) -> str:
    if score >= 80:
        return "A+"
    if score >= 70:
        return "A"
    if score >= 60:
        return "B"
    return "C"


def evaluate(
    symbol: str,
    side: str,
    row: Dict[str, Any],
    signal: Optional[Dict[str, Any]] = None,
    fresh_flags: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    """Score a signal side against its LIVE SHEET row (App Quality Score v2).

    Returns a decision dict (see module docstring). Pure function — no I/O.

    fresh_flags (optional): per-field freshness from the engine's
    DataQualityValidator. Stale scored inputs force a data-quality rejection.

    When the LIVE SHEET data is stale/unverifiable the decision is
    NO_APP_DECISION (data-quality rejection, never a trading rejection).
    """
    side = str(side or "LONG").upper()
    if side not in ("LONG", "SHORT"):
        side = "LONG"

    # ── v2 #1: Data-freshness gate ──────────────────────────────────────────
    # v2 gates OI/funding/kline (scored inputs); orderbook/trade-stream
    # staleness is informational, dashboard-only (unchanged v2 behavior).
    freshness = _freshness_check(row, fresh_flags, gated_fields={"oi", "funding", "kline"})
    if freshness["fresh"] is False:
        decision = {
            "symbol": symbol,
            "side": side,
            "timestamp": time.time(),
            "score": 0.0,
            "grade": "SD",
            "decision": "NO_APP_DECISION",
            "blocked": False,
            "reason": freshness["reason"] or "DATA_STALE",
            "hard_conflict": False,
            "regime_class": "DATA",
            "scoring_version": SCORE_VERSION,
            "freshness": freshness,
            "components": {},
            "breakdown": {},
            "live_sheet": {},
            "signal": signal or {},
        }
        if config.profit_filter.blocking:
            decision["blocked"] = True
        return decision

    components = {
        "regime": _score_regime(side, row),
        "cvd": _score_cvd(side, row),
        "delta": _score_delta(side, row),
        "flow": _score_flow(side, row),
        "volume": _score_volume(side, row),
        "oi_funding": _score_oi_funding(side, row),
        "liquidity": _score_liquidity(side, row),
        "sweep_fvg": _score_sweep_fvg(side, row),
    }

    score = sum(components[k]["score"] * WEIGHTS[k] for k in components)
    score = round(_clamp(score), 1)
    grade = grade_from_score(score)

    # ── v2 #4: Hard conflict = strong side-aware DIRECTIONAL contradiction on
    # the core confirmation components (regime, CVD, delta, flow, OI/funding)
    # OR a liquidation cascade running against the position. VOLUME is a
    # confirmation modifier (v2 #5) and can NEVER hard-reject. Risk penalties
    # (high liq percentile) and sweep/FVG disagreements are scored but not
    # hard-rejected.
    _directional = ("regime", "cvd", "delta", "flow", "oi_funding")
    conflicts = {
        k: v["detail"] for k, v in components.items()
        if k in _directional and v["conflict"] and v["score"] < 45
    }
    _liq = components["liquidity"]
    if _liq["conflict"] and _liq["score"] < 45:
        conflicts["liquidity"] = _liq["detail"]
    hard_conflict = bool(conflicts)

    regime_class = components["regime"]["regime_class"]

    # ── v2 #3: Admission bands ──────────────────────────────────────────────
    #   80-100 ACCEPT · 70-79 ACCEPT only if no hard conflict · 60-69 WATCH · <60 REJECT
    reason = ""
    if score >= 80:
        decision = "ACCEPT"
    elif hard_conflict:
        worst = max(conflicts, key=lambda k: 50 - components[k]["score"])
        decision = "REJECT"
        reason = f"HARD_CONFLICT:{worst.upper()}"
    elif score >= config.profit_filter.min_execute_score:
        decision = "ACCEPT"
    elif score >= config.profit_filter.watch_min_score:
        decision = "WATCH"
        reason = "BAND:WATCH"
    else:
        decision = "REJECT"
        reason = "BAND:LOW_SCORE"

    # Blocking decision (used by the engine when blocking is enabled).
    # In diagnostic mode (Phase A) nothing is ever blocked — decisions are logged only.
    if config.profit_filter.blocking and decision != "ACCEPT":
        block = not (decision == "WATCH" and not config.profit_filter.block_watch)
    else:
        block = False
    if (
        config.profit_filter.enforce_regime_conf
        and regime_class == "REJECT"
        and config.profit_filter.blocking
    ):
        block = True
        decision = "REJECT"
        reason = "REGIME_CONFIDENCE"

    live_sheet_slice = {
        "regime": row.get("regime"),
        "regime_confidence_pct": row.get("regime_confidence_pct"),
        "cvd_bias": row.get("cvd_bias"),
        "net_delta": row.get("net_delta"),
        "buy_sell_ratio": row.get("buy_sell_ratio"),
        "exchange_bias": row.get("exchange_bias"),
        "flow_signal": row.get("flow_signal"),
        "vol_bias": row.get("vol_bias"),
        "oi_bias": row.get("oi_bias"),
        "funding": row.get("funding"),
        "funding_bias": row.get("funding_bias"),
        "liq_risk_level": row.get("liq_risk_level"),
        "cascade_active": row.get("cascade_active"),
        "cascade_side": row.get("cascade_side"),
        "sweep_detected": row.get("sweep_detected"),
        "sweep_direction": row.get("sweep_direction"),
        "fvg_alignment": row.get("fvg_alignment"),
        "fvg_score": row.get("fvg_score"),
        "data_source": row.get("data_source", ""),
    }

    return {
        "symbol": symbol,
        "side": side,
        "timestamp": time.time(),
        "score": score,
        "grade": grade,
        "decision": decision,
        "blocked": bool(block),
        "reason": reason,
        "hard_conflict": hard_conflict,
        "regime_class": regime_class,
        "scoring_version": SCORE_VERSION,
        "freshness": freshness,
        "components": components,
        "breakdown": {k: {"score": v["score"], "max": v["max"], "agreed": v["agreed"],
                          "conflict": v["conflict"], "detail": v["detail"]}
                      for k, v in components.items()},
        "live_sheet": live_sheet_slice,
        "signal": signal or {},
    }


def evaluate_v3(
    symbol: str,
    side: str,
    row: Dict[str, Any],
    signal: Optional[Dict[str, Any]] = None,
    fresh_flags: Optional[Dict[str, bool]] = None,
) -> Dict[str, Any]:
    """Score a signal side against its LIVE SHEET row — App Quality Score v3.

    v3 is the evidence-based upgrade over v2 (v2 is preserved byte-for-byte):
      1. Strict per-field freshness: price (market_data), OI, funding, kline and
         orderbook are ALL scored inputs. Any stale scored field → DATA_STALE
         (a data-quality rejection, never a directional score).
      2. Weights (sum 100): CVD 20, OI/Funding 15, Delta 20, Flow 15,
         Liquidity 10, Sweep+FVG 10, Regime 5, Volume 5.
      3. Asymmetric side-aware conflict penalties: a conflicted component loses
         points proportional to its importance (Delta hardest, Volume weakest).
         NEUTRAL is NOT a conflict — it only withholds confirmation.
      4. HARD_CONFLICT veto: 2+ strong directional contradictions among
         regime/CVD/Delta/Flow/OI-Funding REJECT even a raw score of 80+.
      5. APP-only risk gate: oversized stop-loss exposure (stop distance vs
         entry) downgrades ACCEPT/WATCH → REJECT RISK:OVERSIZED_STOP.
      6. No look-ahead: only entry-time data (LIVE SHEET + signal) is used.
      7. Attribution categories: ACCEPT · WATCH · REJECT ·
         REJECT_HARD_CONFLICT · NO_APP_DECISION (missing data) · DATA_STALE.
    """
    side = str(side or "LONG").upper()
    if side not in ("LONG", "SHORT"):
        side = "LONG"

    # ── v3 #1: strict per-field freshness (all scored inputs) ──────────────
    freshness = _freshness_check(
        row, fresh_flags, gated_fields={"oi", "funding", "kline", "orderbook", "market_data"})
    if freshness["fresh"] is False:
        reason = freshness["reason"] or "DATA_STALE"
        stale_keys = sorted(k for k, v in (freshness.get("fields") or {}).items()
                            if str(v) == "stale")
        if stale_keys:
            reason = "DATA_STALE:" + ",".join(stale_keys[:4])
        decision = {
            "symbol": symbol, "side": side, "timestamp": time.time(),
            "score": 0.0, "grade": "SD", "decision": "DATA_STALE",
            "blocked": False, "reason": reason,
            "hard_conflict": False, "regime_class": "DATA",
            "scoring_version": SCORE_VERSION_V3,
            "freshness": freshness, "components": {}, "breakdown": {},
            "live_sheet": {}, "signal": signal or {},
            "risk": {"stop_pct": 0.0, "oversized": False},
        }
        if config.profit_filter.blocking:
            decision["blocked"] = True
        return decision

    components = {
        "regime": _score_regime(side, row),
        "cvd": _score_cvd(side, row),
        "delta": _score_delta(side, row),
        "flow": _score_flow(side, row),
        "volume": _score_volume(side, row),
        "oi_funding": _score_oi_funding(side, row),
        "liquidity": _score_liquidity(side, row),
        "sweep_fvg": _score_sweep_fvg(side, row),
    }

    # ── v3 #3: asymmetric side-aware conflict penalties ─────────────────────
    # A conflicted component is penalized by its importance (Delta hardest).
    # NEUTRAL components (no conflict, low score) are NOT penalized further —
    # they simply contribute little.
    effective = {}
    for k, c in components.items():
        s = float(c["score"])
        if c["conflict"]:
            s = _clamp(s - CONFLICT_PENALTY_V3[k])
        effective[k] = s

    score = sum(effective[k] * WEIGHTS_V3[k] for k in components)
    score = round(_clamp(score), 1)
    grade = grade_from_score(score)
    regime_class = components["regime"]["regime_class"]

    # ── v3 #4: HARD_CONFLICT veto (overrides even 80+) ──────────────────────
    # Strong directional contradiction = conflicted AND scoring below the
    # veto threshold. 2+ such contradictions in the same direction veto.
    veto_conflicts = {
        k: components[k]["detail"] for k in HARD_CONFLICT_VETO_V3
        if components[k]["conflict"] and components[k]["score"] < HARD_CONFLICT_VETO_SCORE
    }
    vetoed = len(veto_conflicts) >= HARD_CONFLICT_VETO_MIN
    hard_conflict = bool(veto_conflicts)

    # ── v3 #5: APP-only risk gate (oversized stop-loss exposure) ────────────
    risk = {"stop_pct": 0.0, "max_stop_pct": 0.0, "oversized": False}
    sig = signal or {}
    _entry = _num(sig, "entry", 0) or _num(sig, "entry_price", 0)
    _sl = _num(sig, "sl", 0) or _num(sig, "stop_loss", 0)
    if _entry > 0 and _sl > 0:
        stop_pct = abs(_entry - _sl) / _entry * 100.0
        risk = {
            "stop_pct": round(stop_pct, 2),
            "max_stop_pct": config.profit_filter.risk_gate_max_stop_pct,
            "oversized": bool(stop_pct > config.profit_filter.risk_gate_max_stop_pct),
        }

    # ── v3 admission bands (veto evaluated FIRST) ───────────────────────────
    reason = ""
    if vetoed:
        decision = "REJECT_HARD_CONFLICT"
        _rank = sorted(veto_conflicts, key=lambda k: 50 - components[k]["score"])
        reason = "HARD_CONFLICT:" + ",".join(_rank[:3]).upper()
    elif score >= 80:
        decision = "ACCEPT"
    elif score >= config.profit_filter.min_execute_score:
        decision = "ACCEPT"
    elif score >= config.profit_filter.watch_min_score:
        decision = "WATCH"
        reason = "BAND:WATCH"
    else:
        decision = "REJECT"
        reason = "BAND:LOW_SCORE"

    # Risk gate downgrades ACCEPT/WATCH → REJECT (APP-only protection).
    if risk["oversized"] and decision in ("ACCEPT", "WATCH"):
        if config.profit_filter.risk_gate_blocking or not config.profit_filter.blocking:
            decision = "REJECT"
            reason = "RISK:OVERSIZED_STOP"

    if config.profit_filter.blocking and decision != "ACCEPT":
        block = not (decision == "WATCH" and not config.profit_filter.block_watch)
    else:
        block = False

    live_sheet_slice = {
        "regime": row.get("regime"),
        "regime_confidence_pct": row.get("regime_confidence_pct"),
        "cvd_bias": row.get("cvd_bias"),
        "net_delta": row.get("net_delta"),
        "buy_sell_ratio": row.get("buy_sell_ratio"),
        "exchange_bias": row.get("exchange_bias"),
        "flow_signal": row.get("flow_signal"),
        "vol_bias": row.get("vol_bias"),
        "oi_bias": row.get("oi_bias"),
        "funding": row.get("funding"),
        "funding_bias": row.get("funding_bias"),
        "liq_risk_level": row.get("liq_risk_level"),
        "cascade_active": row.get("cascade_active"),
        "cascade_side": row.get("cascade_side"),
        "sweep_detected": row.get("sweep_detected"),
        "sweep_direction": row.get("sweep_direction"),
        "fvg_alignment": row.get("fvg_alignment"),
        "fvg_score": row.get("fvg_score"),
        "data_source": row.get("data_source", ""),
    }

    return {
        "symbol": symbol, "side": side, "timestamp": time.time(),
        "score": score, "grade": grade, "decision": decision,
        "blocked": bool(block), "reason": reason,
        "hard_conflict": hard_conflict, "regime_class": regime_class,
        "scoring_version": SCORE_VERSION_V3,
        "freshness": freshness,
        "components": components,
        "breakdown": {k: {"score": v["score"], "max": v["max"], "agreed": v["agreed"],
                          "conflict": v["conflict"], "detail": v["detail"]}
                      for k, v in components.items()},
        "live_sheet": live_sheet_slice,
        "signal": sig,
        "risk": risk,
        "veto": {"vetoed": vetoed,
                 "strong_conflicts": {k: components[k]["detail"] for k in veto_conflicts}},
    }


def decision_to_record(decision: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a decision for DB/JSONL persistence."""
    sig = decision.get("signal") or {}
    fresh = decision.get("freshness") or {}
    return {
        "timestamp": decision["timestamp"],
        "symbol": decision["symbol"],
        "side": decision["side"],
        "signal_id": sig.get("id") or sig.get("signal_id") or "",
        "strategy_version": sig.get("strategy_version", ""),
        "quality_score": decision["score"],
        "grade": decision["grade"],
        "decision": decision["decision"],
        "reason": decision["reason"],
        "regime_class": decision["regime_class"],
        "signal_confidence": sig.get("confidence", 0),
        "breakdown": json.dumps(decision["breakdown"]),
        "live_sheet": json.dumps(decision["live_sheet"]),
        "scoring_version": decision.get("scoring_version", SCORE_VERSION),
        "data_fresh": fresh.get("level", ""),
        "data_age_sec": fresh.get("age_sec") or 0,
        "stale": int(not fresh.get("fresh", False)),
    }


def write_decision_log(decision: Dict[str, Any], path: Optional[Path] = None) -> None:
    """Append a decision to the JSONL forensics log (best-effort)."""
    p = Path(path) if path else LOG_PATH
    if not config.profit_filter.log_jsonl:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(decision, default=str) + "\n")
    except OSError as e:
        logger.debug("ProfitFilter: JSONL log write failed: {}", e)


# ── Convenience entry point for the engine ──────────────────────────────────

def filter_signal(
    symbol: str,
    side: str,
    signal: Optional[Dict[str, Any]] = None,
    row: Optional[Dict[str, Any]] = None,
    fresh_flags: Optional[Dict[str, bool]] = None,
    scoring_version: Optional[str] = None,
) -> Dict[str, Any]:
    """End-to-end admission decision for a signal.

    Loads the LIVE SHEET row for the symbol, evaluates it, records the decision,
    and returns the full decision dict. Never raises.

    scoring_version selects the scoring path (default: config):
        "v2"  — byte-for-byte current behavior (diagnostic v2).
        "v3"  — evidence-based upgrade (see evaluate_v3).

    fresh_flags (optional): per-field freshness from the engine's
    DataQualityValidator; stale scored inputs force DATA_STALE / NO_APP_DECISION.
    """
    if not config.profit_filter.enabled:
        return {"enabled": False, "decision": "EXECUTE", "blocked": False}
    version = (scoring_version or config.profit_filter.scoring_version or SCORE_VERSION).lower()
    if row is None:
        row = get_live_sheet_row(symbol)
    if not row:
        # No LIVE SHEET data → cannot verify. Diagnostic: log as
        # NO_APP_DECISION (missing data, like the freshness gate it is a
        # data-quality condition, NOT a trading rejection).
        decision = {
            "symbol": symbol, "side": side, "timestamp": time.time(),
            "score": 0.0, "grade": "SD", "decision": "NO_APP_DECISION",
            "blocked": False, "reason": "NO_LIVE_SHEET_DATA",
            "hard_conflict": False, "regime_class": "DATA",
            "scoring_version": version,
            "freshness": {"fresh": False, "age_sec": None, "level": "UNVERIFIED",
                          "fields": {}, "reason": "NO_LIVE_SHEET_DATA"},
            "components": {}, "breakdown": {},
            "live_sheet": {}, "signal": signal or {},
        }
        if config.profit_filter.blocking:
            decision["blocked"] = True
        write_decision_log(decision)
        return decision
    if version == "v3":
        decision = evaluate_v3(symbol, side, row, signal, fresh_flags=fresh_flags)
    else:
        decision = evaluate(symbol, side, row, signal, fresh_flags=fresh_flags)
    write_decision_log(decision)
    return decision
