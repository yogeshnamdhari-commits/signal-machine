"""
Directional Neutralizer — per-cycle imbalance detection and score adjustment.

Detects when the signal generator leans too heavily in one direction and
applies graduated penalties to over-represented signals while rewarding
contrarian (divergence) opportunities.

Target: no more than 70% of signals in a single direction per scan cycle.
"""
from __future__ import annotations

from typing import Dict, List, Optional
from loguru import logger


class DirectionalNeutralizer:
    """
    Tracks per-cycle signal directions and computes bias penalties / divergence bonuses.

    Lifecycle per scan cycle:
        1. begin_cycle()         — reset counters
        2. record_signal(side)   — called for each symbol that produces a signal
        3. get_bias_penalty(s)   — multiplier [0.4–1.0] applied to over-represented side
        4. get_divergence_bonus(s)— additive bonus for contrarian signals
        5. end_cycle()           — returns final stats for logging
    """

    def __init__(
        self,
        max_direction_ratio: float = 0.70,
        penalty_floor: float = 0.40,
        penalty_ceiling: float = 1.0,
        divergence_threshold: float = 0.55,
        divergence_bonus_max: float = 0.10,
        extreme_imbalance_ratio: float = 0.85,
        extreme_penalty: float = 0.30,
        uniform_direction_bonus: float = 0.08,
    ) -> None:
        """
        Args:
            max_direction_ratio:     Max fraction of signals allowed in one direction
                                    before penalties kick in (0.70 = 70%).
            penalty_floor:           Minimum multiplier for the most penalised direction.
            penalty_ceiling:         Multiplier when there's no imbalance (1.0 = no penalty).
            divergence_threshold:    Below this ratio, signals are considered "contrarian"
                                    and receive a bonus.
            divergence_bonus_max:    Maximum additive bonus for contrarian signals.
            extreme_imbalance_ratio: Ratio above which an *extreme* penalty is applied
                                    (e.g. 85%+ longs).
            extreme_penalty:         Multiplier applied at extreme imbalance.
            uniform_direction_bonus: Bonus added to contrarian signals when ALL tracked
                                     signals are the same direction (maximum divergence).
        """
        self.max_ratio = max_direction_ratio
        # Phase 10: disabled directional cap — one-sided markets legitimately
        # produce all LONG signals; the cap rejects valid signals when SHORT
        # signals are blocked by orderflow conflict
        self.max_ratio = 1.0  # Effectively disables the cap
        self.penalty_floor = penalty_floor
        self.penalty_ceiling = penalty_ceiling
        self.divergence_threshold = divergence_threshold
        self.divergence_bonus_max = divergence_bonus_max
        self.extreme_ratio = extreme_imbalance_ratio
        self.extreme_penalty = extreme_penalty
        self.uniform_direction_bonus = uniform_direction_bonus

        # Per-cycle state — scanned (AI scorer produced)
        self._cycle_directions: List[str] = []
        self._cycle_penalties_applied: int = 0
        self._cycle_bonuses_applied: int = 0
        # Per-cycle state — saved (passed all filters + cap)
        self._saved_long: int = 0
        self._saved_short: int = 0
        self._saved_rejected: int = 0

    # ── Lifecycle ────────────────────────────────────────────────

    def begin_cycle(self) -> None:
        """Reset state for a new scan cycle."""
        self._cycle_directions.clear()
        self._cycle_penalties_applied = 0
        self._cycle_bonuses_applied = 0
        self._saved_long = 0
        self._saved_short = 0
        self._saved_rejected = 0

    def record_signal(self, direction: str) -> None:
        """Record a signal's direction (LONG / SHORT) for the current cycle."""
        self._cycle_directions.append(direction.upper())

    def end_cycle(self) -> Dict:
        """Return final cycle statistics for logging / dashboard bridge."""
        stats = self.get_ratios()
        stats["penalties_applied"] = self._cycle_penalties_applied
        stats["bonuses_applied"] = self._cycle_bonuses_applied
        stats["imbalance_detected"] = self._is_imbalanced()
        stats["saved_long"] = self._saved_long
        stats["saved_short"] = self._saved_short
        stats["saved_rejected"] = self._saved_rejected
        saved_total = self._saved_long + self._saved_short
        saved_long_pct = (self._saved_long / saved_total * 100) if saved_total else 0
        saved_short_pct = (self._saved_short / saved_total * 100) if saved_total else 0
        logger.info(
            "🧭 Directional Balance: LONG {} ({:.0%}) | SHORT {} ({:.0%}) | "
            "saved: L{} S{} rejected={} | penalties={} bonuses={}",
            stats["long_count"], stats["long_ratio"],
            stats["short_count"], stats["short_ratio"],
            self._saved_long, self._saved_short, self._saved_rejected,
            self._cycle_penalties_applied, self._cycle_bonuses_applied,
        )
        return stats

    # ── Ratio queries ────────────────────────────────────────────

    def get_ratios(self) -> Dict:
        """Return current cycle direction counts and ratios."""
        total = len(self._cycle_directions)
        if total == 0:
            return {
                "long_count": 0, "short_count": 0,
                "long_ratio": 0.5, "short_ratio": 0.5,
                "total": 0,
            }
        longs = sum(1 for d in self._cycle_directions if d == "LONG")
        shorts = total - longs
        return {
            "long_count": longs,
            "short_count": shorts,
            "long_ratio": longs / total,
            "short_ratio": shorts / total,
            "total": total,
        }

    def _is_imbalanced(self) -> bool:
        """True if either direction exceeds the max allowed ratio."""
        r = self.get_ratios()
        return r["long_ratio"] > self.max_ratio or r["short_ratio"] > self.max_ratio

    # ── Score adjustments ────────────────────────────────────────

    def get_bias_penalty(self, direction: str) -> float:
        """
        Returns a multiplier [penalty_floor … 1.0] to apply to the signal's
        institutional score / confidence.

        Logic:
            - If the direction's ratio ≤ max_ratio → 1.0 (no penalty)
            - If between max_ratio and extreme_ratio → linear interpolation
              from 1.0 down to extreme_penalty
            - If ≥ extreme_ratio → fixed extreme_penalty
        """
        direction = direction.upper()
        ratios = self.get_ratios()
        total = ratios["total"]

        # Need at least 3 signals before directional penalties apply
        # (prevents penalising early signals before the ratio is meaningful)
        if total < 3:
            return self.penalty_ceiling

        ratio = ratios["long_ratio"] if direction == "LONG" else ratios["short_ratio"]

        if ratio <= self.max_ratio:
            return self.penalty_ceiling  # 1.0 — no penalty

        if ratio >= self.extreme_ratio:
            self._cycle_penalties_applied += 1
            logger.debug(
                "🔴 EXTREME penalty: {} ratio={:.0%} (>= {:.0%}) → {}×",
                direction, ratio, self.extreme_ratio, self.extreme_penalty,
            )
            return self.extreme_penalty

        # Linear penalty: max_ratio → 1.0, extreme_ratio → extreme_penalty
        excess = ratio - self.max_ratio
        span = self.extreme_ratio - self.max_ratio
        penalty_range = self.penalty_ceiling - self.extreme_penalty
        penalty = self.penalty_ceiling - (excess / span) * penalty_range
        penalty = max(self.penalty_floor, min(self.penalty_ceiling, penalty))

        self._cycle_penalties_applied += 1
        logger.debug(
            "⚠️ Bias penalty: {} ratio={:.0%} → {:.2f}×",
            direction, ratio, penalty,
        )
        return penalty

    def get_divergence_bonus(self, direction: str) -> float:
        """
        Rewards contrarian signals — the minority direction gets a score boost.

        The more imbalanced the cycle, the bigger the reward for going against
        the crowd (divergence opportunity).

        Returns:
            Additive bonus in [0 … divergence_bonus_max].
            When ALL signals are the same direction, returns uniform_direction_bonus
            (maximum divergence reward).
        """
        direction = direction.upper()
        ratios = self.get_ratios()
        total = ratios["total"]

        if total < 3:
            return 0.0  # Not enough data

        ratio = ratios["long_ratio"] if direction == "LONG" else ratios["short_ratio"]

        # Not contrarian if this direction is the majority
        if ratio >= 0.5:
            return 0.0

        # Uniform direction case: ALL signals are the opposite → max divergence
        minority_count = ratios["long_count"] if direction == "LONG" else ratios["short_count"]
        if minority_count <= 1 and total >= 5:
            self._cycle_bonuses_applied += 1
            logger.debug(
                "💎 MAX divergence bonus: only {} {} out of {} → +{:.0%}",
                minority_count, direction, total, self.uniform_direction_bonus,
            )
            return self.uniform_direction_bonus

        # Graduated bonus based on how contrarian the signal is
        minority_ratio = 1.0 - ratio  # how dominant the OTHER direction is
        if minority_ratio >= self.divergence_threshold:
            bonus = min(
                self.divergence_bonus_max,
                (minority_ratio - 0.5) * self.divergence_bonus_max * 4,
            )
            self._cycle_bonuses_applied += 1
            logger.debug(
                "🎯 Divergence bonus: {} minority={:.0%} → +{:.3f}",
                direction, minority_ratio, bonus,
            )
            return bonus

        return 0.0

    def adjust_signal_score(
        self,
        direction: str,
        institutional_score: float,
        confidence: float,
    ) -> Dict:
        """
        Convenience method: applies both penalty and bonus, returns adjusted values.

        Args:
            direction:           "LONG" or "SHORT"
            institutional_score: Current score (0–100)
            confidence:          Current confidence (0–1)

        Returns:
            Dict with adjusted_score, adjusted_confidence, penalty, bonus, ratios.
        """
        penalty = self.get_bias_penalty(direction)
        bonus = self.get_divergence_bonus(direction)

        # Apply penalty multiplicatively to score
        adjusted_score = institutional_score * penalty
        # Apply bonus additively to confidence (capped at 1.0)
        adjusted_confidence = min(1.0, confidence + bonus)

        # If penalty was severe, also dampen confidence proportionally
        if penalty < 0.8:
            adjusted_confidence *= penalty

        ratios = self.get_ratios()
        return {
            "adjusted_score": round(adjusted_score, 2),
            "adjusted_confidence": round(adjusted_confidence, 4),
            "penalty_multiplier": round(penalty, 4),
            "divergence_bonus": round(bonus, 4),
            "long_ratio": round(ratios["long_ratio"], 4),
            "short_ratio": round(ratios["short_ratio"], 4),
        }

    # ── Hard directional cap ──────────────────────────────────────

    def check_directional_cap(self, direction: str) -> bool:
        """
        Hard gate: rejects excess signals in the over-represented direction.

        After score penalties, some signals still pass the threshold. This gate
        enforces a hard cap: no more than max_direction_ratio of SAVED signals
        can be in one direction per cycle.

        Returns:
            True  → allow signal (within cap or minority direction)
            False → reject signal (over-represented direction exceeded cap)
        """
        direction = direction.upper()

        # Phase 11: If cap is disabled (max_ratio >= 1.0), always allow
        if self.max_ratio >= 1.0:
            if direction == "LONG":
                self._saved_long += 1
            else:
                self._saved_short += 1
            return True

        # Minority direction is always allowed
        if direction == "SHORT" and self._saved_long >= self._saved_short:
            pass  # SHORT is minority or equal — allow
        elif direction == "LONG" and self._saved_short >= self._saved_long:
            pass  # LONG is minority or equal — allow
        else:
            # This direction is the majority — check cap
            saved_total = self._saved_long + self._saved_short
            if saved_total >= 3:  # Need at least 3 saved signals before capping
                if direction == "LONG":
                    current_ratio = self._saved_long / saved_total
                else:
                    current_ratio = self._saved_short / saved_total

                if current_ratio >= self.max_ratio:
                    self._saved_rejected += 1
                    logger.debug(
                        "🚫 DIRECTIONAL CAP: {} rejected ({:.0%} of {} saved, cap={:.0%})",
                        direction, current_ratio, saved_total, self.max_ratio,
                    )
                    return False

        # Record the saved signal
        if direction == "LONG":
            self._saved_long += 1
        else:
            self._saved_short += 1
        return True
