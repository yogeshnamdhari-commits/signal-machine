"""
EMA_V5 Volume Outcome Audit — persistent diagnostic collection.

Records EVERY candle-qualified candidate at the volume stage (both volume
PASS and volume REJECT) with full volume/candle diagnostics, then associates
the subsequent trade outcome when available.

Purpose: answer "does the VolumeEngine improve or damage signal quality?"
without changing any strategy thresholds or scoring logic.

Persists to data/volume_outcome_audit.json so it survives engine restarts
and can accumulate a 30+ sample set before any filter redesign.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
AUDIT_FILE = _DATA_DIR / "volume_outcome_audit.json"

_instance: Optional["VolumeOutcomeAudit"] = None


def get_volume_outcome_audit() -> "VolumeOutcomeAudit":
    global _instance
    if _instance is None:
        _instance = VolumeOutcomeAudit()
    return _instance


class VolumeOutcomeAudit:
    """Collects candle-qualified candidates + trade outcomes for volume analysis."""

    def __init__(self, audit_file: Optional[str] = None) -> None:
        self._file = Path(audit_file) if audit_file else AUDIT_FILE
        self._file.parent.mkdir(parents=True, exist_ok=True)
        self._records: List[Dict[str, Any]] = []
        self._load()

    # ── Persistence ────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if self._file.exists():
                with open(self._file) as f:
                    self._records = json.load(f)
        except Exception as e:
            logger.warning("VolumeOutcomeAudit load failed: {}", e)
            self._records = []

    def _save(self) -> None:
        try:
            tmp = self._file.with_suffix(".json.tmp")
            with open(tmp, "w") as f:
                json.dump(self._records, f, indent=2)
            os.replace(tmp, self._file)
        except Exception as e:
            logger.debug("VolumeOutcomeAudit save failed: {}", e)

    # ── Candidate recording (called at volume stage) ───────────────

    def record_candidate(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """Record a candle-qualified candidate that reached the volume stage.

        Only records candidates with valid completed candle data. Dedupes by
        (symbol, closed_candle_time) so repeated scan cycles don't inflate counts.
        """
        symbol = kwargs.get("symbol", "")
        closed_candle_time = kwargs.get("closed_candle_time", 0)

        # Data validity guard — reject polluted samples
        if not symbol or not closed_candle_time:
            return None
        last_volume = kwargs.get("last_volume", 0)
        vol_sma20 = kwargs.get("vol_sma20", 0)
        prev_volume = kwargs.get("prev_volume", 0)
        last_candle = kwargs.get("last_candle") or {}
        if not all([
            last_volume and last_volume > 0,
            vol_sma20 and vol_sma20 > 0,
            last_candle.get("open", 0) > 0,
            last_candle.get("high", 0) > 0,
            last_candle.get("low", 0) > 0,
            last_candle.get("close", 0) > 0,
        ]):
            return None

        # Dedupe: same symbol + same closed candle → skip re-record
        for r in self._records:
            if r.get("symbol") == symbol and r.get("closed_candle_time") == closed_candle_time:
                return r

        volume_ratio = kwargs.get("volume_ratio", 0)
        expansion = bool(kwargs.get("expansion", False))
        decision_ok = bool(kwargs.get("volume_ok", False))

        record: Dict[str, Any] = {
            # Identity
            "symbol": symbol,
            "side": kwargs.get("side", "?"),
            "regime": kwargs.get("regime", "?"),
            "confirmation_price": kwargs.get("confirmation_price", 0),
            "closed_candle_time": closed_candle_time,
            "timestamp": time.time(),
            # Volume measurements
            "last_volume": round(float(last_volume), 2),
            "prev_volume": round(float(prev_volume), 2),
            "vol_sma20": round(float(vol_sma20), 2),
            "volume_ratio": round(float(volume_ratio), 3),
            "pullback_vol_ratio": round(kwargs.get("pullback_vol_ratio", 0), 3),
            "confirm_vol_ratio": round(kwargs.get("confirm_vol_ratio", 0), 3),
            "confirm_over_pullback": round(kwargs.get("confirm_over_pullback", 0), 3),
            "expansion": expansion,
            # Candle characteristics
            "candle_direction": kwargs.get("candle_direction", "?"),
            "candle_pattern": kwargs.get("candle_pattern", "?"),
            "candle_body_pct": round(kwargs.get("candle_body_pct", 0), 2),
            "lower_wick_pct": round(kwargs.get("lower_wick_pct", 0), 2),
            "upper_wick_pct": round(kwargs.get("upper_wick_pct", 0), 2),
            # Engine decision
            "volume_ok": decision_ok,
            "rejection_reason": kwargs.get("rejection_reason", "none"),
            "rejected_by_ratio": float(volume_ratio) < 0.4,
            "rejected_by_expansion": not expansion,
            # Outcome (filled on trade close)
            "outcome": None,          # "win" / "loss" / "open"
            "pnl": None,
            "realized_r": None,
            "exit_reason": None,
            "exit_time": None,
            "hold_minutes": None,
        }
        self._records.append(record)
        self._save()
        return record

    # ── Outcome association (called when an EMA_V5 trade closes) ──

    def associate_outcome(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        pnl: float,
        realized_r: float,
        exit_reason: str,
        exit_time: float,
        hold_minutes: Optional[float] = None,
        entry_time: Optional[float] = None,
        exit_price: Optional[float] = None,
        mfe_pct: Optional[float] = None,
        mae_pct: Optional[float] = None,
        confidence: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Match a closed trade back to its volume-stage candidate.

        Matching: symbol + side + entry price proximity (<= 2%) + the candidate
        must have been recorded before trade entry (within 12h window).

        Outcome fields recorded (for R-multiple / MFE / MAE quality analysis):
        symbol, side, entry_price, entry_time, exit_price, exit_time, PnL,
        R-multiple, MFE%, MAE%, holding time, volume_ratio, expansion, confidence.
        """
        if not symbol or not entry_price or entry_price <= 0:
            return None

        best: Optional[Dict[str, Any]] = None
        best_score = float("inf")
        for r in self._records:
            if r.get("symbol") != symbol or r.get("side") != side:
                continue
            if r.get("outcome") is not None:
                continue  # already resolved
            cand_ts = r.get("closed_candle_time", 0)
            # Candidate must predate the trade
            if entry_time and cand_ts > entry_time:
                continue
            if not entry_time and exit_time and cand_ts > exit_time:
                continue
            if exit_time and cand_ts < exit_time - 12 * 3600:
                continue
            conf_price = r.get("confirmation_price", 0)
            if conf_price <= 0:
                continue
            price_gap = abs(conf_price - entry_price) / entry_price
            if price_gap > 0.02:
                continue
            if price_gap < best_score:
                best_score = price_gap
                best = r

        if best is None:
            logger.debug(
                "VolumeOutcomeAudit: no candidate match for {} {} @ {}", symbol, side, entry_price
            )
            return None

        best["outcome"] = "win" if pnl > 0 else "loss"
        best["pnl"] = round(float(pnl), 2)
        best["realized_r"] = round(float(realized_r), 2) if realized_r is not None else None
        best["exit_reason"] = exit_reason
        best["entry_time"] = entry_time if entry_time else best.get("entry_time")
        best["exit_time"] = exit_time
        best["exit_price"] = round(float(exit_price), 8) if exit_price else None
        best["hold_minutes"] = round(float(hold_minutes), 1) if hold_minutes else None
        best["mfe_pct"] = round(float(mfe_pct), 4) if mfe_pct is not None else None
        best["mae_pct"] = round(float(mae_pct), 4) if mae_pct is not None else None
        best["confidence"] = round(float(confidence), 1) if confidence is not None else None
        self._save()
        return best

    # ── Summary ────────────────────────────────────────────────────

    def get_summary(self) -> Dict[str, Any]:
        """Compute the 7 requested metrics from collected records."""
        total = len(self._records)
        resolved = [r for r in self._records if r.get("outcome") in ("win", "loss")]
        if total == 0:
            return {"total_candidates": 0, "status": "collecting"}

        rejected_ratio = sum(1 for r in self._records if r.get("rejected_by_ratio"))
        rejected_exp = sum(1 for r in self._records if r.get("rejected_by_expansion"))
        passed_both = sum(
            1 for r in self._records
            if not r.get("rejected_by_ratio") and not r.get("rejected_by_expansion")
        )

        winners = [r for r in resolved if r.get("outcome") == "win"]
        losers = [r for r in resolved if r.get("outcome") == "loss"]

        def _avg(records: List[Dict[str, Any]], key: str) -> float:
            vals = [r.get(key) for r in records if isinstance(r.get(key), (int, float))]
            return round(sum(vals) / len(vals), 3) if vals else 0.0

        exp_winners = [r for r in winners if r.get("expansion")]
        exp_losers = [r for r in losers if r.get("expansion")]
        win_rate_exp = len(exp_winners) / max(len(winners), 1) * 100
        win_rate_noexp = len([r for r in winners if not r.get("expansion")]) / max(
            len([r for r in losers if not r.get("expansion")]) + len([r for r in winners if not r.get("expansion")]), 1
        ) * 100 if any(not r.get("expansion") for r in resolved) else 0.0

        return {
            "total_candidates": total,
            "resolved_outcomes": len(resolved),
            "status": "collecting" if total < 30 else "ready",
            # 1-3: rejection decomposition
            "pct_rejected_by_ratio": round(rejected_ratio / total * 100, 1),
            "pct_rejected_by_expansion": round(rejected_exp / total * 100, 1),
            "pct_passed_both": round(passed_both / total * 100, 1),
            "count_rejected_by_ratio": rejected_ratio,
            "count_rejected_by_expansion": rejected_exp,
            "count_passed_both": passed_both,
            # 4-6: winner/loser volume stats
            "avg_volume_ratio_winners": _avg(winners, "volume_ratio"),
            "avg_volume_ratio_losers": _avg(losers, "volume_ratio"),
            "avg_expansion_ratio_winners": _avg(winners, "confirm_over_pullback"),
            "avg_expansion_ratio_losers": _avg(losers, "confirm_over_pullback"),
            # 7: does expansion improve expectancy?
            "win_rate_when_expanding": round(win_rate_exp, 1),
            "win_rate_when_not_expanding": round(win_rate_noexp, 1),
            "n_winners": len(winners),
            "n_losers": len(losers),
        }

    def get_records(self) -> List[Dict[str, Any]]:
        return list(self._records)

    def log_report(self) -> None:
        """Print a human-readable table of records + summary."""
        summary = self.get_summary()
        logger.info("=" * 90)
        logger.info("📊 VOLUME OUTCOME AUDIT REPORT")
        logger.info("=" * 90)
        logger.info(
            "Candidates: {} (target 30) | resolved: {} | status: {}",
            summary.get("total_candidates", 0),
            summary.get("resolved_outcomes", 0),
            summary.get("status", "collecting"),
        )
        logger.info("Rejection decomposition:")
        logger.info("  Rejected by volume ratio (< 0.4x):  {} ({}%)",
                    summary.get("count_rejected_by_ratio", 0),
                    summary.get("pct_rejected_by_ratio", 0))
        logger.info("  Rejected by expansion requirement:  {} ({}%)",
                    summary.get("count_rejected_by_expansion", 0),
                    summary.get("pct_rejected_by_expansion", 0))
        logger.info("  Passed BOTH ratio + expansion:      {} ({}%)",
                    summary.get("count_passed_both", 0),
                    summary.get("pct_passed_both", 0))
        logger.info("Winner/loser volume stats:")
        logger.info("  avg volume ratio    winners={}  losers={}",
                    summary.get("avg_volume_ratio_winners", 0),
                    summary.get("avg_volume_ratio_losers", 0))
        logger.info("  avg confirm/pullback winners={}  losers={}",
                    summary.get("avg_expansion_ratio_winners", 0),
                    summary.get("avg_expansion_ratio_losers", 0))
        logger.info("  win rate expanding={}%  not-expanding={}%",
                    summary.get("win_rate_when_expanding", 0),
                    summary.get("win_rate_when_not_expanding", 0))
        logger.info("Recent candidates:")
        for r in self._records[-12:]:
            logger.info(
                "  {} {} ratio={:.2f} pb_ratio={:.2f} c/p={:.2f} exp={} body={:.1f}% "
                "dec={} reason={} outcome={}",
                r.get("symbol"), r.get("side"), r.get("volume_ratio", 0),
                r.get("pullback_vol_ratio", 0), r.get("confirm_over_pullback", 0),
                r.get("expansion", False), r.get("candle_body_pct", 0),
                "PASS" if r.get("volume_ok") else "REJECT",
                r.get("rejection_reason", ""), r.get("outcome"),
            )
        logger.info("=" * 90)
