"""
EMA_V5 Scanner — Main orchestrator for the EMA_V5 institutional strategy.
Coordinates all sub-engines to evaluate symbols and generate signals.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from .config import ema_v5_config, EMAv5Config
from .score_calibration.candidate_logger import CandidateLogger
from .cache import EMACache
from .regime_engine import RegimeEngine
from .trend_engine import TrendEngine
from .pullback_engine import PullbackEngine
from .candle_engine import CandleEngine
from .volume_engine import VolumeEngine
from .confidence_engine import ConfidenceEngine
from .signal_engine import SignalEngine
from .trade_manager import TradeManager
from .state_manager import StateManager, NO_TREND, BUY_MODE, SELL_MODE, WAITING_PULLBACK, WAITING_CONFIRMATION, ACTIVE_BUY, ACTIVE_SELL, TRADE_CLOSED
from .pipeline_observer import PipelineObserver
from .waiting_audit import WaitingConfirmationAudit
from .threshold_calibration import ThresholdCalibration
from .performance_metrics import PerformanceMetrics
from .failure_detector import FailureDetector
from .lifecycle_logger import lifecycle_log
from .production_analytics import ProductionAnalytics
from .monitor import PipelineMonitor

# Dedicated logger for EMA_V5
_log_file = Path(__file__).resolve().parent.parent.parent / ema_v5_config.log.log_file
_log_file.parent.mkdir(parents=True, exist_ok=True)
ema_logger = logger.bind(name="ema_v5")


class EMAv5Scanner:
    """Main EMA_V5 scanner — evaluates symbols and generates signals.

    Usage:
        scanner = EMAv5Scanner()
        signal = await scanner.evaluate(symbol, klines, current_regime)
        if signal:
            # Signal is ready for execution
    """

    def __init__(self) -> None:
        self.cache = EMACache()
        self.regime_engine = RegimeEngine()
        self.trend_engine = TrendEngine()
        self.pullback_engine = PullbackEngine()
        self.candle_engine = CandleEngine()
        self.volume_engine = VolumeEngine()
        self.confidence_engine = ConfidenceEngine()
        self.signal_engine = SignalEngine()
        self.trade_manager = TradeManager()
        self.state_manager = StateManager()
        self.observer = PipelineObserver(report_interval=200)
        # ── NEW: Production diagnostics modules ──
        self.waiting_audit = WaitingConfirmationAudit()
        self.threshold_calibration = ThresholdCalibration()
        self.perf_metrics = PerformanceMetrics()
        self.failure_detector = FailureDetector()
        # ── FINAL-STAGE REJECTION TRACKER ──
        self._rejection_summary: Dict[str, int] = {}
        self._scan_cycle_start: float = time.time()
        self._scan_cycle_candidates: int = 0
        self._scan_cycle_signals: int = 0
        self._scan_count = 0
        self._signal_count = 0
        self._start_time = time.time()
        # Persisted scan count file — survives engine restarts
        self._scan_count_file = Path(__file__).resolve().parent.parent.parent / "data" / "ema_v5_scan_count.json"
        self._load_scan_count()
        # Persistent signal history — survives engine cleanup cycles
        # Keeps signals for the lifetime of the scanner process
        self._signal_history: List[Dict] = []
        self._max_signal_history = 200
        # Score calibration — logs candidates ≥70 for threshold analysis
        self._calibration_logger = CandidateLogger()
        # ── DIAG: Pipeline stage counters ──
        self._stage_counts = {
            "total": 0, "fast_filter": 0, "ema_cache": 0, "regime": 0,
            "trend": 0, "pullback": 0, "candle": 0, "volume": 0,
            "confidence": 0, "signal": 0,
        }
        # ── Stage PASS counters (complement to rejection counters) ──
        self._stage_passed = {
            "fast_filter": 0, "ema_cache": 0, "regime": 0,
            "trend": 0, "pullback": 0, "candle": 0,
            "volume": 0, "confidence": 0, "signal": 0,
        }
        self._diag_report_interval = 500  # log every 500 candidates (was 5000)
        # ── DIAG: Fast Filter per-reason rejection counters ──
        self._fast_filter_reasons = {
            "no_klines": 0,
            "insufficient_candles": 0,
            "invalid_ohlcv": 0,
            "zero_volume": 0,
        }
        # ── Production Analytics ──
        self._prod_analytics = ProductionAnalytics()
        # ── Pipeline Monitor — permanent observability ──
        self.pipeline_monitor = PipelineMonitor()
        # ── AUDIT LOG: read-only candidate scoring breakdown ──
        self._audit_log_path = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "ema_v5_audit.log"
        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._audit_count = 0
        # ── AUDIT: state-transition journey tracker ──
        self._journey_log_path = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "ema_v5_journey.log"
        self._journey_log_path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("📊 EMA_V5 Scanner initialized — Pipeline observer enabled")

    def _load_scan_count(self) -> None:
        """Load persisted scan count from disk (survives engine restarts)."""
        try:
            if self._scan_count_file.exists():
                with open(self._scan_count_file) as f:
                    data = json.load(f)
                self._scan_count = data.get("scan_count", 0)
                self._signal_count = data.get("signal_count", 0)
                # Restore start_time to maintain accurate uptime
                saved_start = data.get("start_time", 0)
                if saved_start > 0:
                    self._start_time = saved_start
                logger.info("📊 EMA_V5 scan count restored: {} scans, {} signals", self._scan_count, self._signal_count)
        except Exception as e:
            logger.debug("EMA_V5 scan count load failed (will start fresh): {}", e)

    def _save_scan_count(self) -> None:
        """Persist scan count to disk (survives engine restarts)."""
        try:
            self._scan_count_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._scan_count_file.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump({
                    "scan_count": self._scan_count,
                    "signal_count": self._signal_count,
                    "start_time": self._start_time,
                    "saved_at": time.time(),
                }, f, indent=2)
            tmp.replace(self._scan_count_file)
        except Exception as e:
            logger.debug("EMA_V5 scan count save failed: {}", e)

    async def evaluate(
        self,
        symbol: str,
        market_data: Dict,
        regime_data: Optional[Dict] = None,
        orderflow: Optional[Dict] = None,
        cvd_data: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """Evaluate a symbol for EMA_V5 signal.

        This is the main entry point called by the engine.
        Returns a signal dict or None.
        """
        self._scan_count += 1
        # Persist scan count periodically (every 100 scans) to survive restarts
        if self._scan_count % 100 == 0:
            self._save_scan_count()
        _eval_start = time.monotonic()

        # Periodic diagnostics log
        if self._scan_count % 1000 == 0:
            logger.info(
                "📊 Pipeline stats: {} candidates observed, {} signals generated",
                self.observer._total_candidates, self._signal_count,
            )

        try:
            self._stage_counts["total"] += 1
            # ── AUDIT: build state-transition journey ──
            _pre_state = self.state_manager.get_state(symbol)
            _journey: List[str] = [_pre_state] if _pre_state != NO_TREND else []
            # ── Periodic pipeline report ──
            if self._stage_counts["total"] % self._diag_report_interval == 0:
                c = self._stage_counts
                p = self._stage_passed
                total = c["total"]
                if total > 0:
                    logger.info(
                        "🔍 PIPELINE_STAGES total={} fast={} ema={} regime={} trend={} pullback={} candle={} vol={} conf={} signal={}",
                        total,
                        f"{c['fast_filter']/total*100:.0f}%",
                        f"{c['ema_cache']/total*100:.0f}%",
                        f"{c['regime']/total*100:.0f}%",
                        f"{c['trend']/total*100:.0f}%",
                        f"{c['pullback']/total*100:.0f}%",
                        f"{c['candle']/total*100:.0f}%",
                        f"{c['volume']/total*100:.0f}%",
                        f"{c['confidence']/total*100:.0f}%",
                        c["signal"],
                    )
                    # ── Pass counts (conversion funnel) ──
                    logger.info(
                        "🔍 PIPELINE_PASS fast={}→ema={}→regime={}→trend={}→pullback={}→candle={}→vol={}→conf={}→signal={}",
                        p["fast_filter"], p["ema_cache"], p["regime"],
                        p["trend"], p["pullback"], p["candle"],
                        p["volume"], p["confidence"], p["signal"],
                    )

            # ── Track pipeline journey for trace logging ──
            _pgates: list = []  # [(gate, passed, detail)]

            self._scan_cycle_candidates += 1

            # ── LIFECYCLE: Log scan entry ──
            _pre_state = self.state_manager.get_state(symbol)
            _pre_regime = regime_data.get("regime", "unknown") if regime_data else "unknown"
            lifecycle_log.scan_entry(symbol, _pre_state, _pre_regime)

            # ── MONITOR: Start lifecycle audit ──
            _audit_side = "LONG" if _pre_regime in ("BUY_MODE", "trending_bull") else "SHORT"
            self.pipeline_monitor.lifecycle.start(symbol, _audit_side)
            self.pipeline_monitor.daily_recon.record_event("scanned")

            # ── Stage 0: Fast Filter ──
            if not self._fast_filter(symbol, market_data):
                self._stage_counts["fast_filter"] += 1
                self._track_rejection("fast_filter")
                self.observer.record_rejection(symbol, "fast_filter", reason="insufficient_data")
                _pgates.append(("fast", False, "insufficient_data"))
                self.pipeline_monitor.lifecycle.stage_reject(symbol, "fast_filter", "insufficient_data")
                self.pipeline_monitor.daily_recon.record_rejection("fast_filter")
                logger.debug("EMA_V5_TRACE sym={} gates=fast✗ reason=insufficient_klines", symbol)
                return None
            _pgates.append(("fast", True, ""))
            self._stage_passed["fast_filter"] += 1
            self.pipeline_monitor.lifecycle.stage_pass(symbol, "fast_filter")
            self.pipeline_monitor.daily_recon.record_event("fast_filter_pass")

            # ── ACTIVE STATE GUARD: skip re-evaluation for open positions ──
            _current_state = self.state_manager.get_state(symbol)
            if _current_state in (ACTIVE_BUY, ACTIVE_SELL):
                self._track_rejection("active_position")
                _pgates.append(("active_guard", False, _current_state))
                # ── LIFECYCLE: Log active guard ──
                lifecycle_log.transition(symbol, _current_state, _current_state, "active_guard_skip")
                logger.debug("EMA_V5_TRACE sym={} gates=fast✓→active_guard SKIP state={}", symbol, _current_state)
                return None

            # ── Compute EMAs from klines ──
            klines = market_data.get("klines", {}).get(ema_v5_config.primary_tf, [])
            if not klines:
                self._stage_counts["ema_cache"] += 1
                self._track_rejection("ema_cache")
                self.observer.record_rejection(symbol, "ema_cache", reason="no_klines")
                _pgates.append(("ema", False, "no_klines"))
                logger.debug("EMA_V5_TRACE sym={} gates=fast✓→ema✗ reason=no_klines", symbol)
                return None

            ema_data = self.cache.update(symbol, klines)
            if not ema_data:
                self._stage_counts["ema_cache"] += 1
                self._track_rejection("ema_cache")
                self.observer.record_rejection(symbol, "ema_cache", reason="ema_computation_failed")
                _pgates.append(("ema", False, "ema_computation_failed"))
                logger.debug("EMA_V5_TRACE sym={} gates=fast✓→ema✗ reason=ema_computation_failed", symbol)
                return None
            _pgates.append(("ema", True, f"klines={len(klines)}"))
            self._stage_passed["ema_cache"] += 1

            # ── Get external regime ──
            external_regime = "unknown"
            if regime_data:
                external_regime = regime_data.get("regime", "unknown")

            # ── Regime Classification ──
            regime_eval = self.regime_engine.evaluate(ema_data, external_regime)
            regime = regime_eval.get("regime", "NO_TREND")

            # ── State Machine ──
            current_state = self.state_manager.get_state(symbol)
            if regime not in ("NO_TREND",) and regime != current_state:
                if regime in (BUY_MODE, SELL_MODE):
                    _journey.append(regime)

            if regime == "NO_TREND":
                if current_state != NO_TREND:
                    self.state_manager.set_state(symbol, NO_TREND)
                    lifecycle_log.transition(symbol, current_state, NO_TREND, "regime_no_trend")
                self._stage_counts["regime"] += 1
                self._track_rejection("regime")
                _rreason = regime_eval.get("reason", "no_trend")
                self.observer.record_rejection(symbol, "regime", reason=_rreason)
                self.pipeline_monitor.lifecycle.stage_reject(symbol, "regime", _rreason)
                self.pipeline_monitor.daily_recon.record_rejection("regime")
                _gates_str = "→".join([f"{g}{'✓' if p else '✗'}" for g, p, _ in _pgates])
                logger.debug("EMA_V5_TRACE sym={} gates={}→regime✗ reason={}", symbol, _gates_str, _rreason)
                return None
            self._stage_passed["regime"] += 1
            self.pipeline_monitor.lifecycle.stage_pass(symbol, "regime", regime_eval.get("reason", ""))
            self.pipeline_monitor.daily_recon.record_event("regime_pass")

            # ── Trend Analysis ──
            trend_eval = self.trend_engine.evaluate(ema_data, regime)
            if not trend_eval.get("direction"):
                self._stage_counts["trend"] += 1
                self._track_rejection("trend")
                _tdetail = f"direction=None score={trend_eval.get('trend_score', 0):.0f}"
                self.observer.record_rejection(symbol, "trend", regime=regime, reason=_tdetail)
                self.pipeline_monitor.lifecycle.stage_reject(symbol, "trend", _tdetail)
                self.pipeline_monitor.daily_recon.record_rejection("trend")
                _gates_str = "→".join([f"{g}{'✓' if p else '✗'}" for g, p, _ in _pgates])
                logger.debug("EMA_V5_TRACE sym={} gates={}→trend✗ regime={} {}", symbol, _gates_str, regime, _tdetail)
                return None
            _pgates.append(("trend", True, f"dir={trend_eval.get('direction')}"))
            self._stage_passed["trend"] += 1
            self.pipeline_monitor.lifecycle.stage_pass(symbol, "trend")
            self.pipeline_monitor.daily_recon.record_event("trend_pass")

            # ── Pullback Detection ──
            pullback_eval = self.pullback_engine.evaluate(klines, ema_data, regime)
            if not pullback_eval.get("pullback_detected"):
                if current_state == NO_TREND:
                    self.state_manager.set_state(symbol, regime)
                    lifecycle_log.transition(symbol, NO_TREND, regime, "regime_classified")
                self._stage_counts["pullback"] += 1
                self._track_rejection("pullback")
                _preason = pullback_eval.get("reason", "no_pullback")
                self.observer.record_rejection(symbol, "pullback", regime=regime, reason=_preason)
                self.pipeline_monitor.lifecycle.stage_reject(symbol, "pullback", _preason)
                self.pipeline_monitor.daily_recon.record_rejection("pullback")
                _gates_str = "→".join([f"{g}{'✓' if p else '✗'}" for g, p, _ in _pgates])
                logger.debug("EMA_V5_TRACE sym={} gates={}→pullback✗ regime={} reason={}", symbol, _gates_str, regime, _preason)
                return None
            _pgates.append(("pullback", True, "detected"))
            self._stage_passed["pullback"] += 1
            self.pipeline_monitor.lifecycle.stage_pass(symbol, "pullback")
            self.pipeline_monitor.daily_recon.record_event("pullback_pass")

            # ── State transition: WAITING_PULLBACK ──
            _old_state = self.state_manager.get_state(symbol)
            self.state_manager.set_state(symbol, WAITING_PULLBACK)
            lifecycle_log.transition(symbol, _old_state, WAITING_PULLBACK, "pullback_detected")
            _journey.append("WAITING_PULLBACK")

            # ── Candlestick Pattern ──
            candle_eval = self.candle_engine.evaluate(klines, regime)
            if not candle_eval.get("pattern_found"):
                candle_diag = candle_eval.get("diagnostics", {})
                self._stage_counts["candle"] += 1
                _cdetail = f"body={candle_diag.get('body_ratio', 0):.2f} wick={candle_diag.get('wick_ratio', 0):.1f}"
                self._track_rejection("candle")
                self.observer.record_rejection(symbol, "candle", regime=regime, reason=_cdetail)
                self.pipeline_monitor.lifecycle.stage_reject(symbol, "candle", _cdetail)
                self.pipeline_monitor.daily_recon.record_rejection("candle")
                # ── LIFECYCLE: Log candle rejection (state stays WAITING_PULLBACK) ──
                _cur = self.state_manager.get_state(symbol)
                lifecycle_log.transition(symbol, _cur, _cur, f"candle_rejected: {_cdetail}")
                _gates_list = [f"{g}{'Y' if p else 'N'}" for g, p, _d in _pgates]
                _gates_str = "->".join(_gates_list)
                _gates_str = "->".join([f"{g}{'Y' if p else 'N'}" for g, p, _d in _pgates])
                logger.debug("EMA_V5_TRACE sym={} gates={}→candle✗ regime={} {}", symbol, _gates_str, regime, _cdetail)
                return None
            _pgates.append(("candle", True, f"pattern={candle_eval.get('pattern_name', '?')}"))
            self._stage_passed["candle"] += 1
            self.pipeline_monitor.lifecycle.stage_pass(symbol, "candle", candle_eval.get("pattern_name", ""))
            self.pipeline_monitor.daily_recon.record_event("candle_pass")

            # ── State transition: WAITING_CONFIRMATION ──
            _old_state2 = self.state_manager.get_state(symbol)
            self.state_manager.set_state(symbol, WAITING_CONFIRMATION)
            lifecycle_log.transition(symbol, _old_state2, WAITING_CONFIRMATION, "candle_pattern_found")
            _journey.append("WAITING_CONFIRMATION")
            # ── AUDIT: Track WAITING_CONFIRMATION entry ──
            self.waiting_audit.on_enter(
                symbol=symbol,
                regime=regime,
                confidence=0,  # Not computed yet
                side=trend_eval.get("direction", ""),
            )

            # ── Volume Confirmation ──
            volume_eval = self.volume_engine.evaluate(ema_data)
            _vol_ratio = volume_eval.get("volume_ratio", 0)
            _vol_ok = volume_eval.get("volume_ok", False)
            _vol_expanding = volume_eval.get("volume_expanding", False)
            _vol_threshold = 0.4  # Pullback-aware threshold from volume_engine
            _vol_gap = _vol_ratio - _vol_threshold
            _vol_result = "PASS" if _vol_ok else "REJECT"
            logger.info(
                "VOL_TRACE sym={} side={} regime={} ratio={:.3f} threshold={:.1f} gap={:+.3f} expand={} {}",
                symbol, trend_eval.get("direction", "?"), regime,
                _vol_ratio, _vol_threshold, _vol_gap, _vol_expanding, _vol_result
            )
            if not volume_eval.get("volume_ok"):
                last_vol = ema_data.get("last_volume", 0)
                vol_sma = ema_data.get("vol_sma20", 0)
                ratio = volume_eval.get("volume_ratio", 0)
                expanding = volume_eval.get("volume_expanding", False)
                logger.info(
                    "🔍 VOL REJECT {} | last_vol={:.0f} sma20={:.0f} ratio={:.2f} expand={} regime={}",
                    symbol, last_vol, vol_sma, ratio, expanding, regime,
                )
                # ── AUDIT: log volume rejection with full scoring ──
                vol_score = volume_eval.get("volume_score", 0)
                trend_score = trend_eval.get("trend_score", 0)
                candle_score = candle_eval.get("candle_score", 0)
                regime_score = 100 if regime in ("BUY_MODE", "SELL_MODE") else 0
                pullback_score = 100 if pullback_eval.get("pullback_detected") else 0
                # Pre-compute what confidence WOULD have been (weighted sum)
                cfg = ema_v5_config.confidence
                conf_projected = (
                    regime_score * cfg.regime_weight +
                    trend_score * cfg.trend_weight +
                    pullback_score * cfg.pullback_weight +
                    candle_score * cfg.candle_weight +
                    vol_score * cfg.volume_weight
                )
                self._write_audit(
                    symbol=symbol,
                    regime=regime,
                    trend_score=trend_score,
                    regime_score=regime_score,
                    pullback_score=pullback_score,
                    candle_score=candle_score,
                    volume_score=vol_score,
                    conf_projected=conf_projected,
                    conf_actual=0,
                    conf_required=cfg.min_confidence,
                    result="REJECTED:Volume",
                    extra=f"ratio={ratio:.2f} expand={expanding}",
                    journey=_journey,
                )
                self._stage_counts["volume"] += 1
                self._track_rejection("volume")
                self.observer.record_rejection(
                    symbol, "volume", regime=regime,
                    reason=f"ratio={ratio:.2f}_expand={'yes' if expanding else 'no'}",
                )                # ── LIFECYCLE: Log volume rejection (state stays WAITING_CONFIRMATION) ──
                _cur = self.state_manager.get_state(symbol)
                lifecycle_log.transition(symbol, _cur, _cur, f"volume_rejected: ratio={ratio:.2f}")
                _gates_str = "->".join([f"{g}{'Y' if p else 'N'}" for g, p, _d in _pgates])
                logger.debug("EMA_V5_TRACE sym={} gates={}→vol✗ regime={} ratio={:.2f} expand={}", symbol, _gates_str, regime, ratio, expanding)
                # ── AUDIT: WC exit — volume rejected ──
                self.waiting_audit.on_exit(symbol, "REJECTED:Volume")
                self.pipeline_monitor.lifecycle.stage_reject(symbol, "volume", f"ratio={ratio:.2f}")
                self.pipeline_monitor.daily_recon.record_rejection("volume")
                return None
            _pgates.append(("vol", True, f"ratio={volume_eval.get('volume_ratio', 0):.2f}"))
            self._stage_passed["volume"] += 1
            self.pipeline_monitor.lifecycle.stage_pass(symbol, "volume")
            self.pipeline_monitor.daily_recon.record_event("volume_pass")

            # ── Confidence Scoring ──
            confidence_eval = self.confidence_engine.compute(
                regime_eval, trend_eval, pullback_eval, candle_eval, volume_eval,
            )
            # ── Calibration Logger — capture candidates ≥70 ──
            try:
                self._calibration_logger.log_candidate(
                    symbol=symbol,
                    confidence_eval=confidence_eval,
                    regime_eval=regime_eval,
                    trend_eval=trend_eval,
                    pullback_eval=pullback_eval,
                    candle_eval=candle_eval,
                    volume_eval=volume_eval,
                    ema_data=ema_data,
                    entry_price=ema_data.get("last_close", 0) if ema_data else 0,
                    direction=trend_eval.get("direction", "") if trend_eval else "",
                    passed=confidence_eval.get("passed", False),
                    rejection_stage="confidence" if not confidence_eval.get("passed") else "",
                    rejection_reason=confidence_eval.get("reason", "") if not confidence_eval.get("passed") else "",
                )
            except Exception:
                pass  # Never block pipeline on calibration logging

            if not confidence_eval.get("passed"):
                breakdown = confidence_eval.get("breakdown", {})
                # ── FINAL-STAGE: Track confidence rejection ──
                _conf_val = confidence_eval.get('confidence', 0)
                _min_conf = ema_v5_config.confidence.min_confidence
                self._track_rejection(f"confidence_{_conf_val:.0f}_{_min_conf:.0f}")
                # ── AUDIT: log confidence rejection with full scoring ──
                self._write_audit(
                    symbol=symbol,
                    regime=regime,
                    trend_score=breakdown.get("trend", 0),
                    regime_score=breakdown.get("regime", 0),
                    pullback_score=breakdown.get("pullback", 0),
                    candle_score=breakdown.get("candle", 0),
                    volume_score=breakdown.get("volume", 0),
                    conf_projected=confidence_eval.get("confidence", 0),
                    conf_actual=confidence_eval.get("confidence", 0),
                    conf_required=ema_v5_config.confidence.min_confidence,
                    result="REJECTED:Confidence",
                    extra=f"gap={ema_v5_config.confidence.min_confidence - confidence_eval.get('confidence', 0):.1f}",
                    journey=_journey,
                )
                self._stage_counts["confidence"] += 1
                self.observer.record_rejection(
                    symbol, "confidence", regime=regime,
                    component_scores=breakdown,
                    reason=f"conf={confidence_eval.get('confidence', 0):.1f}_min={ema_v5_config.confidence.min_confidence}",
                )                # ── LIFECYCLE: Log confidence rejection (state stays WAITING_CONFIRMATION) ──
                _cur = self.state_manager.get_state(symbol)
                lifecycle_log.transition(symbol, _cur, _cur, 
                                        f"confidence_rejected: {confidence_eval.get('confidence', 0):.1f} < {ema_v5_config.confidence.min_confidence}")
                _gates_str = "->".join([f"{g}{'Y' if p else 'N'}" for g, p, _d in _pgates])
                _conf = confidence_eval.get('confidence', 0)
                _min = ema_v5_config.confidence.min_confidence
                logger.debug("EMA_V5_TRACE sym={} gates={}→conf✗ regime={} conf={:.1f} min={:.1f} breakdown={}", symbol, _gates_str, regime, _conf, _min, breakdown)
                # ── AUDIT: WC exit — confidence rejected ──
                self.waiting_audit.on_exit(symbol, "REJECTED:Confidence")
                self.pipeline_monitor.lifecycle.stage_reject(symbol, "confidence",
                    f"conf={confidence_eval.get('confidence', 0):.1f} < {ema_v5_config.confidence.min_confidence}")
                self.pipeline_monitor.daily_recon.record_rejection("confidence")
                return None
            _gates_str = "→".join([f"{g}{'✓' if p else '✗'}" for g, p, _ in _pgates])
            _conf = confidence_eval.get('confidence', 0)
            logger.info("EMA_V5_TRACE sym={} gates={}→conf✓→SIGNAL regime={} conf={:.1f}", symbol, _gates_str, regime, _conf)
            self._stage_passed["confidence"] += 1
            self.pipeline_monitor.lifecycle.stage_pass(symbol, "confidence", f"conf={_conf:.1f}")
            self.pipeline_monitor.daily_recon.record_event("confidence_pass")

            # ── Generate Signal ──
            signal = self.signal_engine.generate(
                symbol=symbol,
                regime=regime,
                regime_eval=regime_eval,
                trend_eval=trend_eval,
                pullback_eval=pullback_eval,
                candle_eval=candle_eval,
                volume_eval=volume_eval,
                confidence_eval=confidence_eval,
                ema_data=ema_data,
            )

            if signal:
                self._signal_count += 1
                self._stage_counts["signal"] += 1
                self._stage_passed["signal"] += 1
                self.observer.record_signal(symbol, signal.get("confidence", 0))
                # ── LIFECYCLE: Log signal generated ──
                lifecycle_log.transition(symbol, WAITING_CONFIRMATION, "SIGNAL_GENERATED", 
                                        f"conf={signal.get('confidence', 0):.1f} side={signal.get('side', '?')}",
                                        confidence=signal.get("confidence", 0), side=signal.get("side", ""))
                # ── AUDIT: log successful signal ──
                bd = confidence_eval.get("breakdown", {})
                _journey.append("SIGNAL_GENERATED")
                self._write_audit(
                    symbol=symbol,
                    regime=regime,
                    trend_score=bd.get("trend", 0),
                    regime_score=bd.get("regime", 0),
                    pullback_score=bd.get("pullback", 0),
                    candle_score=bd.get("candle", 0),
                    volume_score=bd.get("volume", 0),
                    conf_projected=confidence_eval.get("confidence", 0),
                    conf_actual=confidence_eval.get("confidence", 0),
                    conf_required=ema_v5_config.confidence.min_confidence,
                    result="PASSED:SIGNAL",
                    extra=f"side={signal.get('side', '?')} entry={signal.get('entry', 0):.4f}",
                    journey=_journey,
                )
                # ── FIX: Do NOT set ACTIVE here — engine will set it after validation ──
                # State stays at WAITING_CONFIRMATION until engine accepts the trade
                # self.state_manager.set_state(symbol, new_state)  # REMOVED
                self.trade_manager.open_trade(signal)
                # Persist signal in scanner's own history (survives engine cleanup)
                self._signal_history.append(signal)
                if len(self._signal_history) > self._max_signal_history:
                    self._signal_history = self._signal_history[-self._max_signal_history:]
                # ── AUDIT: WC exit — signal generated (will become ACTIVE in engine) ──
                self.waiting_audit.on_exit(symbol, "SIGNAL_GENERATED")
                # ── PERFORMANCE METRICS: Record signal ──
                self.perf_metrics.record_signal(f"ema_v5_{symbol}_{int(time.time())}")
                # ── FINAL-STAGE: Track signal generation ──
                self._track_rejection("signal_generated")
                self._scan_cycle_signals += 1
                # ── MONITOR: Record signal published ──
                self.pipeline_monitor.lifecycle.publish(symbol, "signal_engine")
                self.pipeline_monitor.stall_detector.record_signal(symbol)
                self.pipeline_monitor.daily_recon.record_event("published")
                # Persist scan+signal counts immediately when signal generated
                self._save_scan_count()
            else:
                # ── AUDIT: confidence passed but signal_engine rejected ──
                # The signal_engine has 4 internal gates (duplicate, cooldown, entry/ATR, R:R)
                # The specific gate is logged by signal_engine.generate() at INFO level.
                _journey.append("REJECTED:SignalGate")
                self._write_audit(
                    symbol=symbol,
                    regime=regime,
                    trend_score=trend_eval.get("score", 0),
                    regime_score=regime_eval.get("score", 0),
                    pullback_score=pullback_eval.get("score", 0),
                    candle_score=candle_eval.get("score", 0),
                    volume_score=volume_eval.get("score", 0),
                    conf_projected=confidence_eval.get("confidence", 0),
                    conf_actual=confidence_eval.get("confidence", 0),
                    conf_required=ema_v5_config.confidence.min_confidence,
                    result="REJECTED:SignalGate",
                    extra=f"conf={confidence_eval.get('confidence', 0):.1f}",
                    journey=_journey,
                )
                self.observer.record_rejection(
                    symbol, "signal_gate", regime=regime,
                    reason=f"conf={confidence_eval.get('confidence', 0):.1f}_passed_confidence_but_signal_engine_rejected",
                )
                self.pipeline_monitor.lifecycle.stage_reject(symbol, "signal_engine",
                    "passed_confidence_but_signal_engine_rejected")
                self.pipeline_monitor.daily_recon.record_rejection("signal_engine")
                logger.info(
                    "🟡 CONF_PASS_BUT_NO_SIGNAL: {} conf={:.1f} regime={}",
                    symbol, confidence_eval.get("confidence", 0), regime,
                )

            # ── PERFORMANCE METRICS: Record candidate evaluation latency ──
            _eval_latency_ms = (time.monotonic() - _eval_start) * 1000
            self.perf_metrics.record_candidate(
                confidence=confidence_eval.get("confidence", 0) if 'confidence_eval' in dir() else 0,
                latency_ms=_eval_latency_ms,
            )
            self.observer.record_stage_latency("confidence", _eval_latency_ms)

            return signal

        except Exception as e:
            # ── FAILURE DETECTOR: Record error ──
            self.failure_detector.record_error("scanner_evaluate", str(e))
            ema_logger.error("EMA_V5 evaluation error for {}: {}", symbol, e)
            return None

    def _fast_filter(self, symbol: str, market_data: Dict) -> bool:
        """Stage 0: Fast filter — reject invalid data before any calculation."""
        klines = market_data.get("klines", {}).get(ema_v5_config.primary_tf, [])
        if not klines:
            self._fast_filter_reasons["no_klines"] += 1
            return False
        if len(klines) < ema_v5_config.ema.min_candles:
            self._fast_filter_reasons["insufficient_candles"] += 1
            return False
        # Check last candle has valid OHLCV
        last = klines[-1]
        if not all([last.get("open"), last.get("high"), last.get("low"), last.get("close")]):
            self._fast_filter_reasons["invalid_ohlcv"] += 1
            return False
        if last.get("volume", 0) <= 0:
            self._fast_filter_reasons["zero_volume"] += 1
            return False
        return True

    def _write_audit(
        self, symbol: str, regime: str,
        trend_score: float, regime_score: float, pullback_score: float,
        candle_score: float, volume_score: float,
        conf_projected: float, conf_actual: float, conf_required: float,
        result: str, extra: str = "", journey: Optional[List[str]] = None,
    ) -> None:
        """Write a single audit line to the audit log file.

        Format:
          SYMBOL | Trend 85 | Regime 100 | Pullback 100 | Candle 90 | Vol 46.6/15pts | Conf 88.4/90.0 | REJECTED:Volume | extra | Journey: BUY_MODE → WAITING_PULLBACK → WAITING_CONFIRMATION → REJECTED:Volume
        READ-ONLY: does not affect scoring, thresholds, or signal generation.
        """
        self._audit_count += 1
        # Component contribution (weighted points toward confidence)
        cfg = ema_v5_config.confidence
        t_pts = trend_score * cfg.trend_weight
        r_pts = regime_score * cfg.regime_weight
        p_pts = pullback_score * cfg.pullback_weight
        c_pts = candle_score * cfg.candle_weight
        v_pts = volume_score * cfg.volume_weight

        # Build journey string
        _j = list(journey) if journey else []
        _outcome = result.replace("REJECTED:", "").replace("PASSED:", "")
        _j_final = _j + [_outcome]
        journey_str = " → ".join(_j_final) if _j_final else result

        line = (
            f"{symbol:<14} | "
            f"Trend {trend_score:>5.1f} ({t_pts:>5.1f}) | "
            f"Regime {regime_score:>5.1f} ({r_pts:>5.1f}) | "
            f"Pullback {pullback_score:>5.1f} ({p_pts:>5.1f}) | "
            f"Candle {candle_score:>5.1f} ({c_pts:>5.1f}) | "
            f"Vol {volume_score:>5.1f} ({v_pts:>5.1f}) | "
            f"Conf {conf_actual:>5.1f}/{conf_required:.0f} | "
            f"{result}"
        )
        if extra:
            line += f" | {extra}"
        line += f" | Journey: {journey_str}"

        # Write to dedicated audit log file (rotated daily by filename)
        try:
            import datetime
            today = datetime.date.today().isoformat()
            audit_path = self._audit_log_path.with_name(f"ema_v5_audit_{today}.log")
            with open(audit_path, "a") as f:
                f.write(line + "\n")
        except Exception:
            pass  # Never block pipeline on audit logging

        # Write journey to separate journey log (one line per candidate)
        try:
            import datetime
            today = datetime.date.today().isoformat()
            jpath = self._journey_log_path.with_name(f"ema_v5_journey_{today}.log")
            with open(jpath, "a") as f:
                f.write(f"{symbol}: {journey_str}\n")
        except Exception:
            pass

        # Also log to logger at DEBUG level (visible in engine log if DEBUG enabled)
        logger.debug("📋 AUDIT: {}", line)

    def _track_rejection(self, reason: str) -> None:
        """Track a rejection reason for the scan cycle summary."""
        self._rejection_summary[reason] = self._rejection_summary.get(reason, 0) + 1

    def _emit_scan_cycle_summary(self) -> None:
        """Emit a summary of the current scan cycle's rejections."""
        if self._scan_cycle_candidates == 0:
            return

        now = time.time()
        cycle_duration = now - self._scan_cycle_start

        # Build summary
        summary_parts = []
        summary_parts.append(f"SCAN_CYCLE_SUMMARY")
        summary_parts.append(f"duration={cycle_duration:.1f}s")
        summary_parts.append(f"candidates={self._scan_cycle_candidates}")
        summary_parts.append(f"signals={self._scan_cycle_signals}")

        # Group rejections by type
        rejection_groups = {}
        for reason, count in self._rejection_summary.items():
            # Extract the base type (e.g., "confidence_85_90" -> "confidence")
            base_type = reason.split("_")[0] if "_" in reason else reason
            rejection_groups[base_type] = rejection_groups.get(base_type, 0) + count

        # Add rejection breakdown
        for rtype, count in sorted(rejection_groups.items(), key=lambda x: x[1], reverse=True):
            summary_parts.append(f"rejected_{rtype}={count}")

        # ── Signal gate breakdown (confidence → publication) ──
        gate_stats = self.signal_engine.get_gate_stats()
        if gate_stats.get("passed", 0) > 0 or sum(gate_stats.values()) > 0:
            summary_parts.append(
                f"signal_gates: dup={gate_stats.get('duplicate', 0)} "
                f"cooldown={gate_stats.get('cooldown', 0)} "
                f"entry_atr={gate_stats.get('invalid_entry_atr', 0)} "
                f"rr={gate_stats.get('rr_too_low', 0)} "
                f"passed={gate_stats.get('passed', 0)}"
            )

        # ── Fast Filter per-reason breakdown ──
        ff = self._fast_filter_reasons
        ff_total = sum(ff.values())
        if ff_total > 0:
            summary_parts.append(
                f"fast_filter_reasons: no_klines={ff['no_klines']} "
                f"insufficient_candles={ff['insufficient_candles']} "
                f"invalid_ohlcv={ff['invalid_ohlcv']} "
                f"zero_volume={ff['zero_volume']}"
            )

        # Log the summary
        summary = " ".join(summary_parts)
        logger.info("📊 {}", summary)

        # Also write to a dedicated summary log
        try:
            import datetime
            today = datetime.date.today().isoformat()
            summary_path = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / f"ema_v5_scan_summary_{today}.log"
            summary_path.parent.mkdir(parents=True, exist_ok=True)
            with open(summary_path, "a") as f:
                f.write(f"{datetime.datetime.now().isoformat()} {summary}\n")
        except Exception:
            pass

    def on_trade_closed(self, symbol: str) -> None:
        """Called when a trade is closed — reset state and clear cooldown."""
        self.state_manager.reset(symbol)
        self.signal_engine.clear_cooldown(symbol)
        self.trade_manager.close_trade(symbol)

    def get_stats(self) -> Dict:
        """Get scanner statistics."""
        uptime = time.time() - self._start_time
        stats = {
            "scan_count": self._scan_count,
            "signal_count": self._signal_count,
            "signal_rate": self._signal_count / max(self._scan_count, 1),
            "uptime_sec": round(uptime, 1),
            "cache_size": self.cache.size,
            "open_trades": self.trade_manager.open_count,
        }
        # Include pipeline observer stats
        observer_stats = self.observer.get_stats()
        stats["pipeline"] = {
            "total_candidates": observer_stats["total_candidates"],
            "stage_rejections": observer_stats["stage_rejections"],
            "stage_passed": dict(self._stage_passed),
            "avg_scores": observer_stats["avg_scores"],
            "confidence_bins": observer_stats["confidence_bins"],
        }
        return stats

    def get_pipeline_report(self) -> str:
        """Get formatted pipeline diagnostics report."""
        return self.observer.build_summary()["text"]

    def get_diagnostics(self) -> Dict:
        """Get comprehensive diagnostics from all production modules.

        Returns a single dict containing data from:
          - Pipeline Observer (stage rejections, latency, funnel)
          - Confidence Engine (audit stats, score distribution)
          - WAITING_CONFIRMATION Audit (state transitions)
          - Performance Metrics (win rate, PF, Sharpe, etc.)
          - Failure Detector (alerts, error counts)
          - Threshold Calibration (threshold analysis)
        """
        return {
            "pipeline": self.observer.get_stats(),
            "confidence_audit": self.confidence_engine.get_audit_stats(),
            "waiting_confirmation": self.waiting_audit.get_stats(),
            "performance": self.perf_metrics.get_metrics(),
            "failures": self.failure_detector.get_stats(),
            "calibration": self.threshold_calibration.analyze(),
            "scanner": {
                "scan_count": self._scan_count,
                "signal_count": self._signal_count,
                "uptime_sec": round(time.time() - self._start_time, 1),
            },
            "stage_passed": dict(self._stage_passed),
        }

    # Maximum age (seconds) for a signal to appear in bridge data.
    # Signals older than this are stale and should not be displayed.
    _SIGNAL_BRIDGE_TTL_SEC = 24 * 3600  # 24 hours

    @staticmethod
    def _parse_components(metadata_str):
        """Parse signal metadata into components dict for dashboard display."""
        import json as _json
        try:
            meta = _json.loads(metadata_str) if metadata_str else {}
            if isinstance(meta, dict):
                return meta.get("components", {})
        except Exception:
            pass
        return {}

    @staticmethod
    def _parse_ema_data(metadata_str):
        """Parse signal metadata into ema_data dict for dashboard display."""
        import json as _json
        try:
            meta = _json.loads(metadata_str) if metadata_str else {}
            if isinstance(meta, dict):
                return meta.get("ema_data", {})
        except Exception:
            pass
        return {}

    def get_bridge_data(self) -> Dict:
        """Export full scanner state for dashboard bridge.

        Returns a dict with:
        - scanner: stats + timing
        - states: per-symbol state machine data
        - state_counts: aggregate counts per state
        - signals: active signal history (filtered by TTL, with DB fallback)
        """
        stats = self.get_stats()
        all_states = self.state_manager.get_all_states()
        state_counts = self.state_manager.get_state_counts()

        # Build clean symbol states for bridge
        symbol_states = {}
        for sym, sdata in all_states.items():
            symbol_states[sym] = {
                "state": sdata.get("state", "NO_TREND"),
                "last_update": sdata.get("last_update", 0),
                "previous": sdata.get("previous", ""),
            }

        # Filter signal history: only include signals within the active trade window.
        # This prevents stale signals from persisting in the bridge indefinitely.
        now = time.time()
        active_signals = [
            s for s in self._signal_history
            if (now - (s.get("timestamp", 0) or 0)) < self._SIGNAL_BRIDGE_TTL_SEC
        ]
        _pruned = len(self._signal_history) - len(active_signals)
        if _pruned > 0:
            logger.debug(
                "📊 EMA_V5 BRIDGE: pruned {} stale signals from bridge ({} remain)",
                _pruned, len(active_signals),
            )

        # DB FALLBACK: if in-memory history is empty (e.g. after engine restart),
        # load active EMA V5 positions from the database so the dashboard always
        # shows every open signal.
        # Also fix state: if a DB position is open but state is not ACTIVE, set it.
        if not active_signals:
            try:
                import sqlite3 as _sqlite3
                _db_path = str(
                    Path(__file__).resolve().parent.parent.parent
                    / "data" / "institutional_v1.db"
                )
                _conn = _sqlite3.connect(_db_path)
                _cur = _conn.cursor()
                _cur.execute(
                    """SELECT p.symbol, p.side, p.entry_price, p.stop_loss,
                       p.take_profit, p.confidence, p.strategy_version,
                       p.opened_at, p.regime,
                       s.metadata
                    FROM positions p
                    LEFT JOIN signals s ON s.symbol = p.symbol
                       AND s.status = 'active'
                    WHERE p.strategy_version = 'ema_v5' AND p.status = 'open'
                    """
                )
                for r in _cur.fetchall():
                    sym = r[0]
                    side = r[1]
                    # Fix state: if symbol has open position but state is not ACTIVE,
                    # set it to ACTIVE (the guard in evaluate() will keep it there)
                    sym_state = symbol_states.get(sym, {}).get("state", "")
                    if sym_state not in ("ACTIVE_BUY", "ACTIVE_SELL"):
                        _new_state = ACTIVE_BUY if side == "LONG" else ACTIVE_SELL
                        self.state_manager.set_state(sym, _new_state)
                        symbol_states[sym] = {
                            "state": _new_state,
                            "last_update": time.time(),
                            "previous": sym_state,
                        }
                        logger.info(
                            "📊 EMA_V5 BRIDGE: fixed state for {} → {} (DB has open position)",
                            sym, _new_state,
                        )
                    active_signals.append({
                        "symbol": sym,
                        "side": side,
                        "entry_price": r[2] or 0,
                        "entry": r[2] or 0,
                        "stop_loss": r[3] or 0,
                        "take_profit": r[4] or 0,
                        "confidence": (r[5] or 0) * 100,
                        "strategy_version": r[6],
                        "timestamp": r[7] or 0,
                        "regime": r[8] or "",
                        "status": "active",
                        "id": f"ema_v5_{sym}_{int(r[7] or 0)}",
                        "components": self._parse_components(r[9]),
                        "ema_data": self._parse_ema_data(r[9]),
                    })
                _conn.close()
                if active_signals:
                    logger.info(
                        "📊 EMA_V5 BRIDGE: loaded {} signals from DB fallback",
                        len(active_signals),
                    )
            except Exception as e:
                logger.debug("EMA V5 bridge DB fallback error: {}", e)

        # Persist scan count whenever bridge data is requested (dashboard refresh)
        self._save_scan_count()

        # ── Signal Rejection Tracker: Export execution path audit data ──
        _tracker_data = {}
        try:
            from scanner.ema_v5.signal_rejection_tracker import get_tracker
            _tracker = get_tracker()
            _summary = _tracker.get_daily_summary()
            _breakdown = _tracker.get_rejection_breakdown()
            _recent_rejections = [t.to_dict() for t in _tracker.get_recent_rejections(limit=10)]
            _recent_opened = [t.to_dict() for t in _tracker._opened[-5:]] if hasattr(_tracker, '_opened') else []
            _reconciliation = _tracker.run_reconciliation()
            _tracker_data = {
                "daily_summary": _summary,
                "breakdown": _breakdown,
                "recent_rejections": _recent_rejections,
                "recent_opened": _recent_opened,
                "reconciliation": _reconciliation,
            }
        except Exception as e:
            logger.debug("Signal rejection tracker export error: {}", e)

        return {
            "scanner": stats,
            "states": symbol_states,
            "state_counts": state_counts,
            "signals": active_signals,
            "signal_gates": self.signal_engine.get_gate_stats(),
            "stage_passed": dict(self._stage_passed),
            "fast_filter_reasons": dict(self._fast_filter_reasons),
            "pipeline_monitor": self.pipeline_monitor.to_bridge(),
            "analytics": self._prod_analytics.get_all(),
            "signal_rejection_tracker": _tracker_data,
        }
