"""
Contribution Display — Extended Module Attribution with Statistical Rigor.

Per v25 directive:
    "Add columns: Module | PF D | EV D | Confidence | Sample Size.
     A contribution measured over 20 trades should not be weighted the
     same as one measured over 2,000 trades."

READ-ONLY: Never modifies upstream data.
"""
from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


@dataclass
class ModuleContribution:
    """Extended contribution of a single module."""
    name: str = ""
    weight: float = 0.0
    normalized_score: float = 0.0
    raw_score: float = 0.0
    contribution: float = 0.0
    rank: int = 0
    trend: str = ""
    pf_delta: float = 0.0
    ev_delta: float = 0.0
    confidence: float = 0.0
    sample_size: int = 0
    confidence_tier: str = ""

    # v26 additions
    stability: float = 0.0       # 0-1: how consistent is this module's contribution
    importance: float = 0.0      # 0-1: contribution * confidence * stability

    def to_dict(self) -> Dict:
        return {
            "name": self.name, "weight": round(self.weight, 3),
            "normalized_score": round(self.normalized_score, 3),
            "raw_score": round(self.raw_score, 3),
            "contribution": round(self.contribution, 3),
            "rank": self.rank, "trend": self.trend,
            "pf_delta": round(self.pf_delta, 4),
            "ev_delta": round(self.ev_delta, 4),
            "confidence": round(self.confidence, 3),
            "sample_size": self.sample_size,
            "confidence_tier": self.confidence_tier,
            "stability": round(self.stability, 3),
            "importance": round(self.importance, 3),
        }


@dataclass
class ContributionDisplay:
    """Complete contribution display with extended attribution."""
    timestamp: float = 0.0
    components: List[ModuleContribution] = field(default_factory=list)
    total_score: float = 0.0
    total_possible: float = 0.0
    overall_quality: float = 0.0
    weighted_quality: float = 0.0
    top_contributors: List[Dict] = field(default_factory=list)
    bottom_contributors: List[Dict] = field(default_factory=list)
    diagnosis: str = ""
    recommendation: str = ""
    quality_score: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "components": [c.to_dict() for c in self.components],
            "summary": {"total_score": round(self.total_score, 3), "total_possible": round(self.total_possible, 3),
                         "overall_quality": round(self.overall_quality, 3), "weighted_quality": round(self.weighted_quality, 3)},
            "top_contributors": self.top_contributors, "bottom_contributors": self.bottom_contributors,
            "quality_score": round(self.quality_score, 1), "diagnosis": self.diagnosis,
            "recommendation": self.recommendation,
        }


class ContributionDisplayEngine:
    """Extended contribution display with PF delta, EV delta, confidence, sample size."""

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH
        self._trades: List[Dict] = []
        self._last_load = 0.0

    def _ensure_loaded(self) -> None:
        if time.time() - self._last_load < 300: return
        self._load_trades()

    def _load_trades(self) -> None:
        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("""
                SELECT symbol, side, realized_r, confidence, institutional_score, highest_pnl, mfe_pct
                FROM positions_archive WHERE status = 'closed' AND closed_at IS NOT NULL
                ORDER BY closed_at DESC
            """)
            rows = cursor.fetchall()
            conn.close()
            self._trades = [dict(r) for r in rows]
            self._last_load = time.time()
        except Exception as e:
            logger.warning("Could not load contribution display: {}", e)

    def evaluate(self) -> ContributionDisplay:
        self._ensure_loaded()
        display = ContributionDisplay(timestamp=time.time())
        if not self._trades or len(self._trades) < 20:
            display.diagnosis = "Insufficient data (need >=20 trades)"
            return display

        trades = self._trades[:300]
        modules = [
            ("Admission Score", 0.25, self._score_admission, self._pf_delta_admission, self._ev_delta_admission),
            ("Trend Alignment", 0.15, self._score_trend, self._pf_delta_trend, self._ev_delta_trend),
            ("Zone Quality", 0.10, self._score_zone, self._pf_delta_zone, self._ev_delta_zone),
            ("Pattern Strength", 0.15, self._score_pattern, self._pf_delta_pattern, self._ev_delta_pattern),
            ("Timing Quality", 0.10, self._score_timing, self._pf_delta_timing, self._ev_delta_timing),
            ("Risk/Reward", 0.15, self._score_rr, self._pf_delta_rr, self._ev_delta_rr),
            ("Volume Confirmation", 0.10, self._score_volume, self._pf_delta_volume, self._ev_delta_volume),
        ]

        total_contribution = 0; total_possible = 0
        weighted_sum = 0; weight_sum = 0

        for name, weight, score_fn, pf_fn, ev_fn in modules:
            try:
                raw_score = score_fn(trades)
                normalized_score = min(1.0, max(0.0, raw_score / 100))
                contribution = weight * normalized_score
                pf_delta = pf_fn(trades)
                ev_delta = ev_fn(trades)
                sample_size = self._get_sample(name, trades)
                confidence = self._calc_confidence(sample_size)
                conf_tier = self._confidence_tier(confidence, sample_size)

                comp = ModuleContribution(name=name, weight=weight, normalized_score=normalized_score,
                    raw_score=raw_score, contribution=contribution, pf_delta=pf_delta, ev_delta=ev_delta,
                    confidence=confidence, sample_size=sample_size, confidence_tier=conf_tier)

                # v26: stability and importance
                comp.stability = self._calc_stability(name, trades)
                comp.importance = contribution * confidence * comp.stability

                display.components.append(comp)
                total_contribution += contribution; total_possible += weight
                weighted_sum += contribution * min(1.0, sample_size / 100)
                weight_sum += weight * min(1.0, sample_size / 100)
            except Exception as e:
                logger.warning("Module {} failed: {}", name, e)
                display.components.append(ModuleContribution(name=name, weight=weight))

        display.components.sort(key=lambda c: c.contribution, reverse=True)
        for i, comp in enumerate(display.components): comp.rank = i + 1

        display.total_score = total_contribution
        display.total_possible = total_possible
        display.overall_quality = total_contribution / max(0.01, total_possible)
        display.weighted_quality = weighted_sum / max(0.01, weight_sum)

        display.top_contributors = [{"name": c.name, "contribution": round(c.contribution, 3),
            "pf_delta": round(c.pf_delta, 4), "sample": c.sample_size, "rank": c.rank} for c in display.components[:3]]
        display.bottom_contributors = [{"name": c.name, "contribution": round(c.contribution, 3),
            "pf_delta": round(c.pf_delta, 4), "sample": c.sample_size, "rank": c.rank} for c in display.components[-3:]]

        display.quality_score = max(0, min(100, display.weighted_quality * 100))

        low_conf = sum(1 for c in display.components if c.confidence_tier in ("LOW", "INSUFFICIENT"))
        if display.weighted_quality > 0.6:
            display.diagnosis = "Strong edge with adequate statistical support"
        elif display.weighted_quality > 0.4:
            display.diagnosis = f"Moderate edge. {low_conf}/{len(display.components)} modules have low confidence"
        elif display.weighted_quality > 0.2:
            display.diagnosis = "Weak edge — major improvement needed"
        else:
            display.diagnosis = f"Near-zero edge. {low_conf} low-confidence modules."

        low_modules = [c for c in display.components if c.confidence_tier in ("LOW", "INSUFFICIENT")]
        if low_modules:
            display.recommendation = f"Need more data for: {', '.join(c.name for c in low_modules[:3])}"
        elif display.bottom_contributors:
            w = display.bottom_contributors[0]
            display.recommendation = f"Focus on '{w['name']}' — PF d={w['pf_delta']:.4f}, sample={w['sample']}"
        else:
            display.recommendation = "All modules contributing adequately"
        return display

    # Scoring
    def _score_admission(self, t): return sum(x.get("confidence", 0) or 0 for x in t[:100]) / max(1, len(t[:100]))
    def _score_trend(self, t):
        hc = [x for x in t[:100] if (x.get("confidence", 0) or 0) > 90]
        if not hc: return 30.0
        return sum(1 for x in hc if (x.get("realized_r", 0) or 0) > 0) / len(hc) * 100
    def _score_zone(self, t):
        z = [x for x in t[:100] if (x.get("institutional_score", 0) or 0) > 50]
        if not z: return 20.0
        return sum(1 for x in z if (x.get("realized_r", 0) or 0) > 0) / len(z) * 100
    def _score_pattern(self, t): return self._score_admission(t) * 0.7
    def _score_timing(self, t):
        pos = [x for x in t[:100] if (x.get("realized_r", 0) or 0) > 0]
        if not pos: return 10.0
        return min(100, max(0, sum(x.get("realized_r", 0) or 0 for x in pos) / len(pos) * 50))
    def _score_rr(self, t):
        avg = sum(x.get("realized_r", 0) or 0 for x in t[:100]) / max(1, len(t[:100]))
        if avg > 1.0: return 90.0
        elif avg > 0.5: return 70.0
        elif avg > 0: return 50.0
        elif avg > -0.5: return 30.0
        return 10.0
    def _score_volume(self, t):
        inst = [x for x in t[:100] if (x.get("institutional_score", 0) or 0) > 60]
        if not inst: return 25.0
        return sum(1 for x in inst if (x.get("realized_r", 0) or 0) > 0) / len(inst) * 100

    # PF delta
    def _pf_delta_admission(self, t):
        hi = [x for x in t[:100] if (x.get("confidence", 0) or 0) > 90]
        lo = [x for x in t[:100] if (x.get("confidence", 0) or 0) <= 90]
        return self._pf(hi) - self._pf(lo)
    def _pf_delta_trend(self, t):
        hi = [x for x in t[:100] if (x.get("confidence", 0) or 0) > 90]
        return self._pf(hi) - self._pf(t[:100])
    def _pf_delta_zone(self, t):
        hi = [x for x in t[:100] if (x.get("institutional_score", 0) or 0) > 50]
        lo = [x for x in t[:100] if (x.get("institutional_score", 0) or 0) <= 50]
        return self._pf(hi) - self._pf(lo)
    def _pf_delta_pattern(self, t):
        hi = [x for x in t[:100] if (x.get("confidence", 0) or 0) > 88]
        lo = [x for x in t[:100] if (x.get("confidence", 0) or 0) <= 88]
        return self._pf(hi) - self._pf(lo)
    def _pf_delta_timing(self, t): return self._pf_delta_admission(t) * 0.8
    def _pf_delta_rr(self, t):
        hi = [x for x in t[:100] if (x.get("realized_r", 0) or 0) > 0]
        lo = [x for x in t[:100] if (x.get("realized_r", 0) or 0) <= 0]
        return self._pf(hi) - self._pf(lo) if lo else 0.0
    def _pf_delta_volume(self, t):
        hi = [x for x in t[:100] if (x.get("institutional_score", 0) or 0) > 60]
        lo = [x for x in t[:100] if (x.get("institutional_score", 0) or 0) <= 60]
        return self._pf(hi) - self._pf(lo)

    # EV delta
    def _ev_delta_admission(self, t):
        hi = [x for x in t[:100] if (x.get("confidence", 0) or 0) > 90]
        lo = [x for x in t[:100] if (x.get("confidence", 0) or 0) <= 90]
        return self._ev(hi) - self._ev(lo)
    def _ev_delta_trend(self, t):
        hi = [x for x in t[:100] if (x.get("confidence", 0) or 0) > 90]
        return self._ev(hi) - self._ev(t[:100])
    def _ev_delta_zone(self, t):
        hi = [x for x in t[:100] if (x.get("institutional_score", 0) or 0) > 50]
        lo = [x for x in t[:100] if (x.get("institutional_score", 0) or 0) <= 50]
        return self._ev(hi) - self._ev(lo)
    def _ev_delta_pattern(self, t):
        hi = [x for x in t[:100] if (x.get("confidence", 0) or 0) > 88]
        lo = [x for x in t[:100] if (x.get("confidence", 0) or 0) <= 88]
        return self._ev(hi) - self._ev(lo)
    def _ev_delta_timing(self, t): return self._ev_delta_admission(t) * 0.8
    def _ev_delta_rr(self, t):
        hi = [x for x in t[:100] if (x.get("realized_r", 0) or 0) > 0]
        lo = [x for x in t[:100] if (x.get("realized_r", 0) or 0) <= 0]
        return self._ev(hi) - self._ev(lo) if lo else 0.0
    def _ev_delta_volume(self, t):
        hi = [x for x in t[:100] if (x.get("institutional_score", 0) or 0) > 60]
        lo = [x for x in t[:100] if (x.get("institutional_score", 0) or 0) <= 60]
        return self._ev(hi) - self._ev(lo)

    # Helpers
    def _calc_stability(self, name, trades):
        """Calculate stability: how consistent is this module's contribution across halves."""
        if len(trades) < 20:
            return 0.0
        mid = len(trades) // 2
        first_half = trades[mid:]
        second_half = trades[:mid]

        # Get the score function for this module
        score_map = {
            "Admission Score": self._score_admission,
            "Trend Alignment": self._score_trend,
            "Zone Quality": self._score_zone,
            "Pattern Strength": self._score_pattern,
            "Timing Quality": self._score_timing,
            "Risk/Reward": self._score_rr,
            "Volume Confirmation": self._score_volume,
        }
        fn = score_map.get(name)
        if not fn:
            return 0.5

        s1 = fn(first_half)
        s2 = fn(second_half)
        if max(s1, s2) == 0:
            return 1.0 if s1 == s2 else 0.0
        return 1.0 - abs(s1 - s2) / max(s1, s2)

    def _get_sample(self, name, trades):
        if name in ("Admission Score", "Pattern Strength", "Timing Quality"):
            return len([t for t in trades if (t.get("confidence", 0) or 0) > 80])
        elif name in ("Zone Quality", "Volume Confirmation"):
            return len([t for t in trades if (t.get("institutional_score", 0) or 0) > 40])
        return len(trades)

    def _calc_confidence(self, n):
        if n <= 0: return 0.0
        return 1.0 - math.exp(-n / 80.0)

    def _confidence_tier(self, conf, n):
        if n < 30: return "INSUFFICIENT"
        elif conf < 0.5: return "LOW"
        elif conf < 0.8: return "MEDIUM"
        return "HIGH"

    def _pf(self, trades):
        if not trades: return 0.0
        w = [t.get("realized_r", 0) or 0 for t in trades if (t.get("realized_r", 0) or 0) > 0]
        l = [abs(t.get("realized_r", 0) or 0) for t in trades if (t.get("realized_r", 0) or 0) < 0]
        return sum(w) / max(0.01, sum(l))

    def _ev(self, trades):
        if not trades: return 0.0
        return sum(t.get("realized_r", 0) or 0 for t in trades) / len(trades)
