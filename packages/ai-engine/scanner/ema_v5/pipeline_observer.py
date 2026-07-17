"""
EMA_V5 Pipeline Observer — Stage-by-stage diagnostics for signal pipeline.
Logs rejection reasons, component scores, latency, and produces summary statistics.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from .config import ema_v5_config


class PipelineObserver:
    """Tracks every candidate through the pipeline and records why it was rejected."""

    STAGES = [
        "fast_filter",
        "ema_cache",
        "regime",
        "trend",
        "pullback",
        "candle",
        "volume",
        "confidence",
        "signal_engine",
    ]

    def __init__(self, report_interval: int = 200, log_dir: str = "data/logs") -> None:
        self._report_interval = report_interval
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)

        # Counters
        self._total_candidates: int = 0
        self._stage_rejections: Dict[str, int] = {s: 0 for s in self.STAGES}
        self._signals_generated: int = 0

        # Component scores for candidates reaching confidence stage
        self._confidence_candidates: List[Dict] = []

        # Candle diagnostics (body_ratio, wick_ratio)
        self._candle_diagnostics: List[Dict] = []

        # Full rejection log (last N)
        self._rejection_log: List[Dict] = []
        self._max_log_size: int = 2000

        # Histogram bins for confidence scores
        self._confidence_bins: Dict[str, int] = defaultdict(int)

        # ── LATENCY: Per-stage timing ──
        self._stage_latencies: Dict[str, List[float]] = {s: [] for s in self.STAGES}
        self._max_latency_samples: int = 200

        # ── REJECTION REASONS: Top-N tracking ──
        self._rejection_reasons: Dict[str, int] = defaultdict(int)

        # ── FUNNEL: Pipeline funnel data for dashboard ──
        self._funnel = {s: 0 for s in self.STAGES}
        self._funnel["signals_generated"] = 0
        self._funnel["total_in"] = 0

        self._start_time: float = time.time()

    def start_candidate(self, symbol: str) -> float:
        """Start timing a candidate evaluation. Returns start timestamp."""
        return time.monotonic()

    def record_stage_latency(self, stage: str, latency_ms: float) -> None:
        """Record latency for a pipeline stage in milliseconds."""
        if stage in self._stage_latencies:
            samples = self._stage_latencies[stage]
            samples.append(latency_ms)
            if len(samples) > self._max_latency_samples:
                self._stage_latencies[stage] = samples[-self._max_latency_samples:]

    def record_rejection(
        self,
        symbol: str,
        stage: str,
        regime: str = "",
        component_scores: Optional[Dict] = None,
        reason: str = "",
    ) -> None:
        """Record a rejection at a specific stage."""
        self._total_candidates += 1
        if stage in self._stage_rejections:
            self._stage_rejections[stage] += 1
        self._funnel[stage] = self._funnel.get(stage, 0) + 1

        # Track explicit rejection reasons
        _reason_key = f"{stage}:{reason}" if reason else stage
        self._rejection_reasons[_reason_key] += 1

        entry = {
            "symbol": symbol,
            "stage": stage,
            "regime": regime,
            "reason": reason,
            "timestamp": time.time(),
        }
        if component_scores:
            entry["scores"] = component_scores
            self._confidence_candidates.append(component_scores)

        # Track candle diagnostics
        if stage == "candle" and reason:
            try:
                parts = reason.split()
                body_ratio = float(parts[0].split("=")[1]) if len(parts) > 0 else 0
                wick_ratio = float(parts[1].split("=")[1]) if len(parts) > 1 else 0
                self._candle_diagnostics.append({
                    "symbol": symbol,
                    "body_ratio": body_ratio,
                    "wick_ratio": wick_ratio,
                    "regime": regime,
                    "timestamp": time.time(),
                })
            except (IndexError, ValueError):
                pass

        self._rejection_log.append(entry)

        # Trim log if too large
        if len(self._rejection_log) > self._max_log_size:
            self._rejection_log = self._rejection_log[-self._max_log_size:]

        # Confidence histogram
        if component_scores and "confidence" in component_scores:
            conf = component_scores["confidence"]
            bin_label = self._confidence_bin_label(conf)
            self._confidence_bins[bin_label] += 1

        # Log individual rejection (debug level to avoid noise)
        if component_scores:
            trend_s = component_scores.get("trend", 0)
            pullback_s = component_scores.get("pullback", 0)
            candle_s = component_scores.get("candle", 0)
            volume_s = component_scores.get("volume", 0)
            conf_s = component_scores.get("confidence", 0)
            logger.debug(
                "🔍 REJECT {} | stage={} | T={:.0f} P={:.0f} C={:.0f} V={:.0f} | conf={:.1f} | {}",
                symbol, stage, trend_s, pullback_s, candle_s, volume_s, conf_s, reason,
            )
        else:
            logger.debug("🔍 REJECT {} | stage={} | {}", symbol, stage, reason)

        # Auto-report at interval
        if self._total_candidates % self._report_interval == 0:
            self._emit_report()

    def record_signal(self, symbol: str, confidence: float) -> None:
        """Record a successfully generated signal."""
        self._total_candidates += 1
        self._signals_generated += 1
        self._funnel["signals_generated"] = self._funnel.get("signals_generated", 0) + 1
        logger.info(
            "✅ SIGNAL RECORDED: {} conf={:.1f} (total_signals={}/{} candidates)",
            symbol, confidence, self._signals_generated, self._total_candidates,
        )

    def _confidence_bin_label(self, score: float) -> str:
        """Map confidence score to histogram bin."""
        if score >= 95:
            return "95-100"
        elif score >= 90:
            return "90-95"
        elif score >= 85:
            return "85-90"
        elif score >= 80:
            return "80-85"
        elif score >= 75:
            return "75-80"
        elif score >= 70:
            return "70-75"
        elif score >= 60:
            return "60-70"
        else:
            return "<60"

    def _emit_report(self) -> None:
        """Emit a summary report to log and file."""
        report = self.build_summary()
        self._save_report(report)

        logger.info(
            "📊 PIPELINE REPORT ({} candidates, {:.0f}s uptime):\n{}",
            self._total_candidates,
            time.time() - self._start_time,
            report["text"],
        )

    def build_summary(self) -> Dict:
        """Build a summary report of all pipeline diagnostics."""
        total = max(self._total_candidates, 1)
        uptime = time.time() - self._start_time

        # Stage rejection rates
        stage_lines = []
        for stage in self.STAGES:
            count = self._stage_rejections.get(stage, 0)
            pct = count / total * 100
            stage_lines.append(f"  {stage:<20s} {count:>6d} ({pct:5.1f}%)")

        # Component score averages (from candidates that reached confidence)
        score_lines = []
        avg_scores = self._average_scores()
        if avg_scores:
            for key in ["trend", "pullback", "candle", "volume", "confidence"]:
                val = avg_scores.get(key, 0)
                score_lines.append(f"  {key:<20s} {val:>6.1f}")

        # Candle diagnostics
        candle_lines = []
        if self._candle_diagnostics:
            body_ratios = [d["body_ratio"] for d in self._candle_diagnostics]
            wick_ratios = [d["wick_ratio"] for d in self._candle_diagnostics]
            avg_body = sum(body_ratios) / len(body_ratios) if body_ratios else 0
            avg_wick = sum(wick_ratios) / len(wick_ratios) if wick_ratios else 0
            max_body = max(body_ratios) if body_ratios else 0
            max_wick = max(wick_ratios) if wick_ratios else 0
            candle_lines.append(f"  body_ratio: avg={avg_body:.3f} max={max_body:.3f} (min={ema_v5_config.candle.body_ratio_min})")
            candle_lines.append(f"  wick_ratio: avg={avg_wick:.2f} max={max_wick:.2f} (min={ema_v5_config.candle.wick_ratio_min})")
            candle_lines.append(f"  samples: {len(self._candle_diagnostics)}")

        # Confidence histogram
        hist_lines = []
        for bin_label in ["<60", "60-70", "70-75", "75-80", "80-85", "85-90", "90-95", "95-100"]:
            count = self._confidence_bins.get(bin_label, 0)
            bar = "█" * min(count, 50)
            hist_lines.append(f"  {bin_label:>6s} │ {count:>5d} {bar}")

        # ── LATENCY: Per-stage average latency ──
        latency_lines = []
        for stage in self.STAGES:
            samples = self._stage_latencies.get(stage, [])
            if samples:
                avg_ms = sum(samples) / len(samples)
                max_ms = max(samples)
                latency_lines.append(f"  {stage:<20s} avg={avg_ms:.2f}ms  max={max_ms:.2f}ms  n={len(samples)}")

        # ── TOP REJECTION REASONS ──
        top_reasons = sorted(self._rejection_reasons.items(), key=lambda x: x[1], reverse=True)[:10]
        reason_lines = [f"  {r:<40s} {c:>6d}" for r, c in top_reasons]

        # Build text report
        text = f"""
═══════════════════════════════════════════════════
  EMA_V5 Pipeline Diagnostics — {self._total_candidates} candidates
  Uptime: {uptime/3600:.1f}h | Signals: {self._signals_generated}
═══════════════════════════════════════════════════

STAGE REJECTIONS:
{chr(10).join(stage_lines)}

CONFIDENCE STAGE SCORES (candidates that reached confidence):
{chr(10).join(score_lines) if score_lines else "  No data yet"}

CANDLE DIAGNOSTICS:
{chr(10).join(candle_lines) if candle_lines else "  No data yet"}

CONFIDENCE DISTRIBUTION:
{chr(10).join(hist_lines)}

PIPELINE LATENCY:
{chr(10).join(latency_lines) if latency_lines else "  No latency data yet"}

TOP REJECTION REASONS:
{chr(10).join(reason_lines) if reason_lines else "  No rejections yet"}

REJECTION CHAIN (top bottlenecks):
{self._bottleneck_analysis()}
═══════════════════════════════════════════════════"""

        return {
            "text": text,
            "total_candidates": self._total_candidates,
            "signals_generated": self._signals_generated,
            "stage_rejections": dict(self._stage_rejections),
            "avg_scores": avg_scores,
            "confidence_bins": dict(self._confidence_bins),
            "stage_latencies": {s: {
                "avg_ms": round(sum(samples) / len(samples), 2) if samples else 0,
                "max_ms": round(max(samples), 2) if samples else 0,
                "samples": len(samples),
            } for s, samples in self._stage_latencies.items()},
            "top_rejection_reasons": dict(top_reasons),
            "funnel": dict(self._funnel),
        }

    def _average_scores(self) -> Dict[str, float]:
        """Compute average component scores from candidates that reached confidence stage."""
        if not self._confidence_candidates:
            return {}

        keys = ["trend", "pullback", "candle", "volume", "confidence"]
        sums = {k: 0.0 for k in keys}
        count = len(self._confidence_candidates)

        for scores in self._confidence_candidates:
            for k in keys:
                sums[k] += scores.get(k, 0)

        return {k: round(sums[k] / count, 1) for k in keys}

    def _bottleneck_analysis(self) -> str:
        """Identify the top rejection bottlenecks."""
        if not self._stage_rejections:
            return "  No data"

        sorted_stages = sorted(
            self._stage_rejections.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        lines = []
        for stage, count in sorted_stages[:3]:
            if count > 0:
                pct = count / max(self._total_candidates, 1) * 100
                lines.append(f"  → {stage}: {count} rejected ({pct:.1f}%)")

        return "\n".join(lines) if lines else "  No rejections yet"

    def _save_report(self, report: Dict) -> None:
        """Save report to file."""
        try:
            report_path = self._log_dir / "pipeline_diagnostics.json"
            with open(report_path, "w") as f:
                json.dump(report, f, indent=2, default=str)

            # Also save text report
            text_path = self._log_dir / "pipeline_diagnostics.txt"
            with open(text_path, "w") as f:
                f.write(report["text"])
        except Exception as e:
            logger.debug("Failed to save pipeline report: {}", e)

    def get_stats(self) -> Dict:
        """Get current observer stats for external access."""
        return {
            "total_candidates": self._total_candidates,
            "signals_generated": self._signals_generated,
            "signal_rate": self._signals_generated / max(self._total_candidates, 1),
            "stage_rejections": dict(self._stage_rejections),
            "avg_scores": self._average_scores(),
            "confidence_bins": dict(self._confidence_bins),
            "stage_latencies": {s: {
                "avg_ms": round(sum(samples) / len(samples), 2) if samples else 0,
                "max_ms": round(max(samples), 2) if samples else 0,
                "samples": len(samples),
            } for s, samples in self._stage_latencies.items()},
            "top_rejection_reasons": dict(sorted(
                self._rejection_reasons.items(), key=lambda x: x[1], reverse=True
            )[:10]),
            "funnel": dict(self._funnel),
        }

    def get_rejected_candidates(self, limit: int = 50) -> List[Dict]:
        """Get the most recent rejected candidates for dashboard explorer."""
        return self._rejection_log[-limit:]

    def get_confidence_histogram(self) -> Dict[str, int]:
        """Get confidence score distribution for dashboard."""
        return dict(self._confidence_bins)
