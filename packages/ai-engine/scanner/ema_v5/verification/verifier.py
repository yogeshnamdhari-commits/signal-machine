"""
EMA_V5 Verifier — Main signal verification engine.
Validates every signal against all conditions before release.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from .diagnostics import EMAv5Diagnostics, SignalDiagnostics, DiagnosticCheck
from ..config import ema_v5_config


class EMAv5Verifier:
    """Validates EMA_V5 signals against all conditions.
    
    Every signal passes through this verifier before release.
    Returns PASS, WARNING, or FAIL with full diagnostics.
    """

    def __init__(self) -> None:
        self._diagnostics = EMAv5Diagnostics()
        self._verification_count = 0
        self._pass_count = 0
        self._warning_count = 0
        self._fail_count = 0

    def verify(
        self,
        signal: Dict[str, Any],
        ema_data: Dict[str, Any],
        regime_eval: Dict[str, Any],
        trend_eval: Dict[str, Any],
        pullback_eval: Dict[str, Any],
        candle_eval: Dict[str, Any],
        volume_eval: Dict[str, Any],
        confidence_eval: Dict[str, Any],
        state: str = "NO_TREND",
    ) -> Tuple[str, SignalDiagnostics]:
        """Verify a signal against all conditions.
        
        Returns (verdict, diagnostics) where verdict is PASS, WARNING, or FAIL.
        """
        start = time.time()
        self._verification_count += 1

        symbol = signal.get("symbol", "")
        uuid = signal.get("uuid", "")

        diag = self._diagnostics.create(signal_uuid=uuid, symbol=symbol)
        diag.signal_data = signal

        checks = []
        warnings = []

        # ── 1. EMA Alignment ──
        check = self._check_ema_alignment(ema_data, signal.get("side", ""))
        checks.append(check)
        if not check.passed:
            diag.reasons_failed.append(f"EMA alignment: {check.reason}")

        # ── 2. Trend Direction ──
        check = self._check_trend_direction(trend_eval, signal.get("side", ""))
        checks.append(check)
        if not check.passed:
            diag.reasons_failed.append(f"Trend direction: {check.reason}")

        # ── 3. EMA Slopes ──
        check = self._check_ema_slopes(ema_data, signal.get("side", ""))
        checks.append(check)
        if not check.passed:
            diag.reasons_failed.append(f"EMA slopes: {check.reason}")
        elif isinstance(check.value, dict) and abs(check.value.get("slope_ema20", 0)) < 0.1:
            warnings.append(f"Weak slope: {check.value.get('slope_ema20', 0):.3f}")

        # ── 4. Pullback ──
        check = self._check_pullback(pullback_eval, signal.get("side", ""))
        checks.append(check)
        if not check.passed:
            diag.reasons_failed.append(f"Pullback: {check.reason}")

        # ── 5. Candlestick Pattern ──
        check = self._check_candlestick(candle_eval)
        checks.append(check)
        if not check.passed:
            diag.reasons_failed.append(f"Candlestick: {check.reason}")

        # ── 6. Volume ──
        check = self._check_volume(volume_eval)
        checks.append(check)
        if not check.passed:
            diag.reasons_failed.append(f"Volume: {check.reason}")

        # ── 7. Confidence ──
        check = self._check_confidence(confidence_eval)
        checks.append(check)
        if not check.passed:
            diag.reasons_failed.append(f"Confidence: {check.reason}")

        # ── 8. State Transition ──
        check = self._check_state_transition(state, signal.get("side", ""))
        checks.append(check)
        if not check.passed:
            diag.reasons_failed.append(f"State transition: {check.reason}")

        # ── 9. Duplicate Protection ──
        check = self._check_duplicate(signal)
        checks.append(check)
        if not check.passed:
            diag.reasons_failed.append(f"Duplicate: {check.reason}")

        # ── 10. R:R Validation ──
        check = self._check_risk_reward(signal)
        checks.append(check)
        if not check.passed:
            diag.reasons_failed.append(f"R:R: {check.reason}")

        # ── 11. Price Validity ──
        check = self._check_price_validity(signal)
        checks.append(check)
        if not check.passed:
            diag.reasons_failed.append(f"Price validity: {check.reason}")

        # ── 12. Trade Lifecycle ──
        check = self._check_trade_lifecycle(signal)
        checks.append(check)
        if not check.passed:
            diag.reasons_failed.append(f"Trade lifecycle: {check.reason}")

        # ── Determine Verdict ──
        failed_critical = [c for c in checks if not c.passed and c.name in (
            "ema_alignment", "trend_direction", "confidence", "price_validity"
        )]
        failed_optional = [c for c in checks if not c.passed and c.name not in (
            "ema_alignment", "trend_direction", "confidence", "price_validity"
        )]

        if failed_critical:
            verdict = "FAIL"
            self._fail_count += 1
        elif failed_optional or warnings:
            verdict = "WARNING"
            self._warning_count += 1
        else:
            verdict = "PASS"
            self._pass_count += 1

        # ── Build Diagnostics ──
        diag.checks = checks
        diag.verdict = verdict
        diag.reasons_passed = [c.reason for c in checks if c.passed and c.reason]
        diag.missing_conditions = [c.reason for c in checks if not c.passed]
        diag.confidence_score = confidence_eval.get("confidence", 0)
        diag.confidence_breakdown = confidence_eval.get("breakdown", {})
        diag.execution_time_ms = (time.time() - start) * 1000

        # Record
        self._diagnostics.record(diag)

        logger.debug("📊 EMA_V5 VERIFY: {} {} → {} ({:.1f}ms) failed={}",
                      symbol, signal.get("side", "?"), verdict,
                      diag.execution_time_ms, len(failed_critical) + len(failed_optional))

        return verdict, diag

    # ── Individual Checks ────────────────────────────────────────

    def _check_ema_alignment(self, ema_data: Dict, side: str) -> DiagnosticCheck:
        """Verify EMA chain alignment."""
        ema20 = ema_data.get("ema20", 0)
        ema50 = ema_data.get("ema50", 0)
        ema144 = ema_data.get("ema144", 0)
        ema200 = ema_data.get("ema200", 0)

        if side == "LONG":
            aligned = ema20 > ema50 > ema144 > ema200
            reason = "EMA20 > EMA50 > EMA144 > EMA200" if aligned else "EMA chain not aligned for LONG"
        else:
            aligned = ema20 < ema50 < ema144 < ema200
            reason = "EMA20 < EMA50 < EMA144 < EMA200" if aligned else "EMA chain not aligned for SHORT"

        return DiagnosticCheck(
            name="ema_alignment",
            passed=aligned,
            value=f"E20={ema20:.2f} E50={ema50:.2f} E144={ema144:.2f} E200={ema200:.2f}",
            threshold="strict chain",
            reason=reason,
        )

    def _check_trend_direction(self, trend_eval: Dict, side: str) -> DiagnosticCheck:
        """Verify trend direction matches signal side."""
        direction = trend_eval.get("direction", "")
        trend_score = trend_eval.get("trend_score", 0)

        if side == "LONG":
            passed = direction in ("BUY", "bullish", "strong_bullish")
            reason = f"Trend is {direction} (score={trend_score:.1f})" if passed else f"Trend not bullish: {direction}"
        else:
            passed = direction in ("SELL", "bearish", "strong_bearish")
            reason = f"Trend is {direction} (score={trend_score:.1f})" if passed else f"Trend not bearish: {direction}"

        return DiagnosticCheck(
            name="trend_direction",
            passed=passed,
            value=direction,
            threshold=f"{'bullish' if side == 'LONG' else 'bearish'}",
            reason=reason,
        )

    def _check_ema_slopes(self, ema_data: Dict, side: str) -> DiagnosticCheck:
        """Verify EMA slopes are positive/negative as expected."""
        slope_20 = ema_data.get("slope_ema20", 0)
        slope_50 = ema_data.get("slope_ema50", 0)

        if side == "LONG":
            passed = slope_20 > 0 and slope_50 > 0
            reason = f"Slopes positive: E20={slope_20:.3f} E50={slope_50:.3f}" if passed else f"Slopes not positive: E20={slope_20:.3f} E50={slope_50:.3f}"
        else:
            passed = slope_20 < 0 and slope_50 < 0
            reason = f"Slopes negative: E20={slope_20:.3f} E50={slope_50:.3f}" if passed else f"Slopes not negative: E20={slope_20:.3f} E50={slope_50:.3f}"

        return DiagnosticCheck(
            name="ema_slopes",
            passed=passed,
            value={"slope_ema20": slope_20, "slope_ema50": slope_50},
            threshold=f"{'positive' if side == 'LONG' else 'negative'}",
            reason=reason,
        )

    def _check_pullback(self, pullback_eval: Dict, side: str) -> DiagnosticCheck:
        """Verify pullback detection."""
        detected = pullback_eval.get("pullback_detected", False)
        touch_level = pullback_eval.get("touch_level", "")
        bounce = pullback_eval.get("bounce_confirmed", False)

        passed = detected and (touch_level in ("ema20", "ema50"))
        reason = f"Pullback to {touch_level}, bounce={'confirmed' if bounce else 'pending'}" if passed else "No valid pullback detected"

        return DiagnosticCheck(
            name="pullback",
            passed=passed,
            value={"detected": detected, "touch_level": touch_level, "bounce": bounce},
            threshold="ema20 or ema50 touch",
            reason=reason,
        )

    def _check_candlestick(self, candle_eval: Dict) -> DiagnosticCheck:
        """Verify candlestick pattern."""
        pattern_found = candle_eval.get("pattern_found", False)
        pattern_name = candle_eval.get("pattern", candle_eval.get("pattern_name", ""))
        candle_score = candle_eval.get("candle_score", 0)

        passed = pattern_found and candle_score > 0
        reason = f"Pattern: {pattern_name} (score={candle_score:.1f})" if passed else "No valid candlestick pattern"

        return DiagnosticCheck(
            name="candlestick",
            passed=passed,
            value={"pattern": pattern_name, "score": candle_score},
            threshold="pattern_found=True",
            reason=reason,
        )

    def _check_volume(self, volume_eval: Dict) -> DiagnosticCheck:
        """Verify volume confirmation."""
        volume_ok = volume_eval.get("volume_ok", False)
        volume_ratio = volume_eval.get("volume_ratio", 0)
        volume_score = volume_eval.get("volume_score", 0)

        passed = volume_ok and volume_ratio >= 1.0
        reason = f"Volume ratio: {volume_ratio:.2f}x (score={volume_score:.1f})" if passed else f"Volume insufficient: {volume_ratio:.2f}x"

        return DiagnosticCheck(
            name="volume",
            passed=passed,
            value={"ratio": volume_ratio, "score": volume_score},
            threshold="ratio >= 1.0",
            reason=reason,
        )

    def _check_confidence(self, confidence_eval: Dict) -> DiagnosticCheck:
        """Verify confidence meets minimum threshold."""
        confidence = confidence_eval.get("confidence", 0)
        passed_score = confidence_eval.get("passed", False)
        min_conf = ema_v5_config.confidence.min_confidence  # 0-100 scale

        passed = passed_score and confidence >= min_conf
        reason = f"Confidence: {confidence:.1f}% (min={min_conf:.0f}%)" if passed else f"Confidence too low: {confidence:.1f}% < {min_conf:.0f}%"

        return DiagnosticCheck(
            name="confidence",
            passed=passed,
            value=confidence,
            threshold=min_conf,
            reason=reason,
        )

    def _check_state_transition(self, current_state: str, side: str) -> DiagnosticCheck:
        """Verify state machine allows this transition."""
        from ..state_manager import _TRANSITIONS

        if side == "LONG":
            target_state = "ACTIVE_BUY"
        else:
            target_state = "ACTIVE_SELL"

        valid_transitions = _TRANSITIONS.get(current_state, set())
        passed = target_state in valid_transitions

        reason = f"State {current_state} → {target_state} ({'valid' if passed else 'invalid'})"

        return DiagnosticCheck(
            name="state_transition",
            passed=passed,
            value=current_state,
            threshold=f"allows → {target_state}",
            reason=reason,
        )

    def _check_duplicate(self, signal: Dict) -> DiagnosticCheck:
        """Check for duplicate signals."""
        symbol = signal.get("symbol", "")
        side = signal.get("side", "")
        entry = signal.get("entry", 0)

        # Simple duplicate check based on symbol+side+entry proximity
        recent = self._diagnostics.get_by_symbol(symbol)
        is_dup = False
        for d in recent[-5:]:
            sd = d.signal_data
            if (sd.get("side") == side and
                    entry > 0 and
                    abs(sd.get("entry", 0) - entry) / entry < 0.001):
                is_dup = True
                break

        reason = "No duplicate detected" if not is_dup else f"Potential duplicate: {symbol} {side} near {entry:.4f}"

        return DiagnosticCheck(
            name="duplicate",
            passed=not is_dup,
            value={"symbol": symbol, "side": side, "entry": entry},
            threshold="no recent duplicate",
            reason=reason,
        )

    def _check_risk_reward(self, signal: Dict) -> DiagnosticCheck:
        """Verify R:R meets minimum threshold."""
        entry = signal.get("entry", 0)
        sl = signal.get("sl", 0)
        tp1 = signal.get("take_profit_1", 0)

        if entry <= 0 or sl <= 0 or tp1 <= 0:
            return DiagnosticCheck(
                name="risk_reward",
                passed=False,
                value=0,
                threshold=ema_v5_config.signal.min_rr,
                reason="Invalid entry/SL/TP values",
            )

        risk = abs(entry - sl)
        reward = abs(tp1 - entry)
        rr = reward / risk if risk > 0 else 0

        min_rr = ema_v5_config.signal.min_rr
        passed = rr >= min_rr

        reason = f"R:R = {rr:.2f} (min={min_rr})" if passed else f"R:R too low: {rr:.2f} < {min_rr}"

        return DiagnosticCheck(
            name="risk_reward",
            passed=passed,
            value=rr,
            threshold=min_rr,
            reason=reason,
        )

    def _check_price_validity(self, signal: Dict) -> DiagnosticCheck:
        """Verify prices are valid and reasonable."""
        entry = signal.get("entry", 0)
        sl = signal.get("sl", 0)
        tp1 = signal.get("take_profit_1", 0)
        side = signal.get("side", "")

        issues = []
        if entry <= 0:
            issues.append("entry <= 0")
        if sl <= 0:
            issues.append("sl <= 0")
        if tp1 <= 0:
            issues.append("tp1 <= 0")

        if side == "LONG" and sl >= entry:
            issues.append("SL above entry for LONG")
        if side == "SHORT" and sl <= entry:
            issues.append("SL below entry for SHORT")

        passed = len(issues) == 0
        reason = "All prices valid" if passed else "; ".join(issues)

        return DiagnosticCheck(
            name="price_validity",
            passed=passed,
            value={"entry": entry, "sl": sl, "tp1": tp1},
            threshold="valid prices",
            reason=reason,
        )

    def _check_trade_lifecycle(self, signal: Dict) -> DiagnosticCheck:
        """Verify trade lifecycle is clean."""
        # Check that strategy_version is set
        version = signal.get("strategy_version", "")
        has_uuid = bool(signal.get("uuid"))
        has_timestamp = bool(signal.get("timestamp"))

        passed = version == "ema_v5" and has_uuid and has_timestamp
        reason = f"version={version}, uuid={'set' if has_uuid else 'missing'}, ts={'set' if has_timestamp else 'missing'}"

        return DiagnosticCheck(
            name="trade_lifecycle",
            passed=passed,
            value={"version": version, "uuid": has_uuid, "timestamp": has_timestamp},
            threshold="ema_v5 with uuid and timestamp",
            reason=reason,
        )

    # ── Statistics ───────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Get verification statistics."""
        total = self._verification_count
        return {
            "total_verifications": total,
            "pass_count": self._pass_count,
            "warning_count": self._warning_count,
            "fail_count": self._fail_count,
            "pass_rate": round(self._pass_count / max(total, 1) * 100, 1),
            "warning_rate": round(self._warning_count / max(total, 1) * 100, 1),
            "fail_rate": round(self._fail_count / max(total, 1) * 100, 1),
        }

    def get_diagnostics(self) -> EMAv5Diagnostics:
        """Get the diagnostics collector."""
        return self._diagnostics
