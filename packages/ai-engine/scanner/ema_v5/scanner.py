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
from .trend_maturity_engine import TrendMaturityEngine
from .pullback_engine import PullbackEngine
from .candle_engine import CandleEngine
from .volume_engine import VolumeEngine
from .confidence_engine import ConfidenceEngine
from .signal_engine import SignalEngine
from .trade_manager import TradeManager
from .state_manager import StateManager, NO_TREND, BUY_MODE, SELL_MODE, WAITING_PULLBACK, WAITING_CONFIRMATION, ACTIVE_BUY, ACTIVE_SELL, TRADE_CLOSED
from .pipeline_observer import PipelineObserver
from .pipeline_integrity import PipelineIntegrity, get_pipeline_integrity
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
        self.trend_maturity_engine = TrendMaturityEngine()
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
        # ── EMA crossover event counters (market events, not strategy entries) ──
        self._ema_cross_counts = {
            "cross_up": 0,        # EMA20 crossed above EMA50
            "cross_down": 0,      # EMA20 crossed below EMA50
            "buy_mode_created": 0,
            "sell_mode_created": 0,
            "cross_total": 0,     # Total simple crosses detected
            "cross_chain_aligned": 0,  # Crosses that satisfied full chain
        }
        # ── Pipeline accounting counters (for integrity assertions) ──
        self._pipeline_accounting = {
            "pullback_entered": 0,
            "regime_lost_pre": 0,        # BUY_MODE → NO_TREND before pullback
            "expired_pre": 0,            # BUY_MODE timeout before pullback (policy-driven)
            "candle_evaluated": 0,
            "regime_lost_in_pull": 0,    # WAITING_PULLBACK → NO_TREND
            "timeout_in_pull": 0,        # WAITING_PULLBACK timeout (policy-driven)
            "candle_passed": 0,
            "candle_rejected": 0,
            "signal_emitted": 0,
            "signal_suppressed": 0,
            "signal_cancelled": 0,
            # ── In-flight counters: computed from current state counts ──
            # These are NOT accumulated — they reflect the live state snapshot
            "buy_mode_live": 0,          # Currently in BUY_MODE (not yet terminal)
            "sell_mode_live": 0,         # Currently in SELL_MODE (not yet terminal)
            "waiting_pullback_live": 0,  # Currently in WAITING_PULLBACK (not yet terminal)
        }
        # ── Stage PASS counters (complement to rejection counters) ──
        self._stage_passed = {
            "fast_filter": 0, "ema_cache": 0, "regime": 0,
            "trend": 0, "pullback": 0, "candle": 0,
            "volume": 0, "confidence": 0, "signal": 0,
        }
        # ── WAITING_PULLBACK reevaluation counters ──
        self._pullback_reeval = {
            "times_entered": 0,
            "candle_evaluated": 0,
            "candle_rejected": 0,
            "candle_passed": 0,
        }
        self._diag_report_interval = 500  # log every 500 candidates (was 5000)
        # ── DIAG: Fast Filter per-reason rejection counters ──
        self._fast_filter_reasons = {
            "no_klines": 0,
            "insufficient_candles": 0,
            "invalid_ohlcv": 0,
            "zero_volume": 0,
        }
        # ── Chain Formation Tracker: tracks EMA chain alignment history per symbol ──
        self._chain_tracker: Dict[str, Dict] = {}  # symbol → {chain_price, chain_time, bars_since, chain_regime}
        # ── 1H Chain Tracker: higher-timeframe EMA chain context ──
        self._htf_tracker: Dict[str, Dict] = {}  # symbol → {chain_pattern, chain_price, chain_time, bars_since, regime}
        # ── Volume Rejection Reasons: tracks why volume stage rejects ──
        self._vol_reject_reasons: Dict[str, int] = {
            "low_ratio": 0,       # volume_ratio < 0.4 (pullback threshold)
            "no_expansion": 0,    # volume not expanding vs previous candle
            "both": 0,            # both low ratio AND no expansion
        }
        # ── Pullback Rejection Audit: tracks why pullback stage rejects ──
        self._pullback_reject_reasons: Dict[str, int] = {
            "no_ema_touch": 0,
            "ema20_touched_outside_tolerance": 0,
            "ema50_touched_outside_tolerance": 0,
            "missing_data": 0,
            "no_trend": 0,
            "other": 0,
        }
        self._pullback_reject_details: List[Dict] = []  # Recent rejection details for audit
        self._max_pullback_reject_details = 100
        # ── Volume Rejection Audit: detailed diagnostics for candle-qualified candidates ──
        self._vol_reject_audit: List[Dict] = []
        self._max_vol_reject_audit = 200
        # ── State-Transition Audit: tracks WAITING_PULLBACK transitions ──
        self._state_transition_audit: List[Dict] = []
        self._max_state_transition_audit = 200
        # ── Production Analytics ──
        self._prod_analytics = ProductionAnalytics()
        # ── Pipeline Monitor — permanent observability ──
        self.pipeline_monitor = PipelineMonitor()
        # ── Pipeline Integrity — accounting identity assertions ──
        self.pipeline_integrity = get_pipeline_integrity()
        # ── PER-SYMBOL EVENT TIMELINE: last N events per tracked symbol ──
        self._symbol_event_log: Dict[str, List[Dict]] = {}  # symbol → [{time, stage, passed, detail, ema_snapshot}]
        self._max_events_per_symbol = 50
        self._tracked_symbols = {"ETHUSDT", "BTCUSDT", "SOLUSDT"}
        # ── CANDIDATE ID: unique identifier per candidate lifecycle ──
        self._current_candidate_id: Dict[str, str] = {}  # symbol → candidate_id
        # ── SCANNER METADATA: included in every event ──
        self._scanner_metadata = {
            "exchange": "binance",
            "timeframe": ema_v5_config.primary_tf,
            "scanner_version": "ema_v5",
            "strategy_version": "v33",
        }
        # ── LAST-SCAN SNAPSHOT: counters at end of most recent scan cycle ──
        self._last_scan_snapshot: Dict = {}
        self._last_scan_timestamp: float = 0
        # ── AUDIT LOG: read-only candidate scoring breakdown ──
        self._audit_log_path = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "ema_v5_audit.log"
        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._audit_count = 0
        # ── AUDIT: state-transition journey tracker ──
        self._journey_log_path = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "ema_v5_journey.log"
        self._journey_log_path.parent.mkdir(parents=True, exist_ok=True)
        # ── Refined candidate lifecycle tracer ──
        self._active_candidates = {}
        self._active_candidates_file = Path(__file__).resolve().parent.parent.parent / "data" / "ema_v5_active_candidates.json"
        self._bottleneck_metrics_file = Path(__file__).resolve().parent.parent.parent / "data" / "ema_v5_bottleneck_metrics.json"
        self._bottleneck_metrics = {"crosses": 0, "mode_created": 0, "waiting_pullback": 0, "waiting_confirmation": 0, "signals": 0, "expired": 0}
        self._load_active_candidates()
        self._load_bottleneck_metrics()
        logger.info("📊 EMA_V5 Scanner initialized — Pipeline observer enabled")

    def _load_active_candidates(self) -> None:
        try:
            if self._active_candidates_file.exists():
                with open(self._active_candidates_file) as f:
                    self._active_candidates = json.load(f)
                # Sync current candidate IDs
                for sym, cand in self._active_candidates.items():
                    self._current_candidate_id[sym] = cand["id"]
        except Exception as e:
            logger.debug("Failed to load active candidates: {}", e)
            self._active_candidates = {}

    def _save_active_candidates(self) -> None:
        try:
            self._active_candidates_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._active_candidates_file.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(self._active_candidates, f, default=str, indent=2)
            tmp.replace(self._active_candidates_file)
        except Exception as e:
            logger.debug("Failed to save active candidates: {}", e)

    def _load_bottleneck_metrics(self) -> None:
        try:
            if self._bottleneck_metrics_file.exists():
                with open(self._bottleneck_metrics_file) as f:
                    self._bottleneck_metrics = json.load(f)
        except Exception as e:
            logger.debug("Failed to load bottleneck metrics: {}", e)

    def _save_bottleneck_metrics(self) -> None:
        try:
            self._bottleneck_metrics_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._bottleneck_metrics_file.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(self._bottleneck_metrics, f, indent=2)
            tmp.replace(self._bottleneck_metrics_file)
        except Exception as e:
            logger.debug("Failed to save bottleneck metrics: {}", e)

    def _finalize_candidate(self, symbol: str, reason: str) -> None:
        if symbol in self._active_candidates:
            cand = self._active_candidates.pop(symbol)
            cand["status"] = "finalized"
            cand["final_reason"] = reason
            cand["elapsed"] = time.time() - cand["ema_cross_time"]
            
            # Update metrics
            if "timeout" in reason.lower() or "regime_lost" in reason.lower() or "no_trend" in reason.lower() or "expired" in reason.lower():
                self._bottleneck_metrics["expired"] = self._bottleneck_metrics.get("expired", 0) + 1
            elif "signal" in reason.lower():
                self._bottleneck_metrics["signals"] = self._bottleneck_metrics.get("signals", 0) + 1
            self._save_bottleneck_metrics()

            # Write to finalized candidates log (JSON lines)
            try:
                cand_log_path = Path(__file__).resolve().parent.parent.parent / "data" / "logs" / "ema_v5_candidates.json"
                cand_log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(cand_log_path, "a") as f:
                    f.write(json.dumps(cand) + "\n")
                logger.info("📋 CANDIDATE_FINALIZED sym={} id={} reason={} elapsed={:.1f}s", symbol, cand["id"], reason, cand["elapsed"])
            except Exception as e:
                logger.debug("Failed to log candidate to JSON: {}", e)
            self._save_active_candidates()
            if symbol in self._current_candidate_id:
                del self._current_candidate_id[symbol]

    def _compute_htf_chain(self, symbol: str, klines_1h: list) -> Dict:
        """Compute 1H EMA chain state for higher-timeframe context.

        Stores BOTH BUY and SELL 1H chains per symbol.
        Returns the current regime's chain dict.
        """
        from .utils import ema as ema_fn, ema_chain_aligned
        cfg = ema_v5_config.ema
        closes = [k.get("close", 0) for k in klines_1h]
        if len(closes) < cfg.min_candles:
            logger.debug("📊 HTF_CHAIN insufficient candles: {}/{}", len(closes), cfg.min_candles)
            return {}
        e20 = ema_fn(closes, cfg.fast)
        e50 = ema_fn(closes, cfg.medium)
        e144 = ema_fn(closes, cfg.institutional)
        e200 = ema_fn(closes, cfg.long_term)
        if not e20[-1] or not e50[-1] or not e144[-1] or not e200[-1]:
            return {}
        buy_chain = ema_chain_aligned(e20[-1], e50[-1], e144[-1], e200[-1], "BUY")
        sell_chain = ema_chain_aligned(e20[-1], e50[-1], e144[-1], e200[-1], "SELL")
        chain_regime = "BUY_MODE" if buy_chain else ("SELL_MODE" if sell_chain else "NO_TREND")

        if symbol not in self._htf_tracker:
            self._htf_tracker[symbol] = {}

        if chain_regime in ("BUY_MODE", "SELL_MODE"):
            _dir = "BUY" if chain_regime == "BUY_MODE" else "SELL"
            _dir_key = _dir.lower()
            existing = self._htf_tracker[symbol].get(_dir_key)
            if not existing or existing.get("regime") != chain_regime:
                result = {
                    "chain_pattern": "20>50>144>200" if _dir == "BUY" else "20<50<144<200",
                    "chain_price": closes[-1],
                    "chain_time": time.time(),
                    "bars_since": 0,
                    "regime": chain_regime,
                    "ema20": e20[-1],
                    "ema50": e50[-1],
                    "ema144": e144[-1],
                    "ema200": e200[-1],
                }
                self._htf_tracker[symbol][_dir_key] = result
                return result
            else:
                existing["bars_since"] = existing.get("bars_since", 0) + 1
                return existing
        else:
            return {"chain_pattern": "", "regime": "NO_TREND"}

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

            # ── Compute EMAs from klines (BEFORE active guard — cached, always cheap) ──
            klines = market_data.get("klines", {}).get(ema_v5_config.primary_tf, [])
            if not klines:
                self._stage_counts["ema_cache"] += 1
                self._track_rejection("ema_cache")
                self.observer.record_rejection(symbol, "ema_cache", reason="no_klines")
                _pgates.append(("ema", False, "no_klines"))
                logger.debug("EMA_V5_TRACE sym={} gates=fast✓→ema✗ reason=no_klines", symbol)
                return None

            # ── Extract closed candle timestamp for TradingView comparison ──
            _closed_candle_time = klines[-1].get("open_time", 0) / 1000.0 if klines else None  # ms → sec

            # ── OHLC snapshot of the evaluated candle ──
            _ohlc = None
            if klines and len(klines) > 0:
                _last_k = klines[-1]
                _ohlc = {
                    "open": float(_last_k.get("open", 0)),
                    "high": float(_last_k.get("high", 0)),
                    "low": float(_last_k.get("low", 0)),
                    "close": float(_last_k.get("close", 0)),
                    "volume": float(_last_k.get("volume", 0)),
                }
                # Derived candle metrics
                _c_range = _ohlc["high"] - _ohlc["low"]
                _c_body = abs(_ohlc["close"] - _ohlc["open"])
                _c_upper_wick = _ohlc["high"] - max(_ohlc["open"], _ohlc["close"])
                _c_lower_wick = min(_ohlc["open"], _ohlc["close"]) - _ohlc["low"]
                _ohlc["range"] = _c_range
                _ohlc["body"] = _c_body
                _ohlc["body_ratio"] = _c_body / _c_range if _c_range > 0 else 0
                _ohlc["upper_wick"] = _c_upper_wick
                _ohlc["lower_wick"] = _c_lower_wick
                _ohlc["is_bullish"] = _ohlc["close"] >= _ohlc["open"]

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

            # ── Extract close price early for market context ──
            _close_now = ema_data.get("last_close", 0)

            # ── Market context (available from ema_data after cache update) ──
            _market_ctx = {
                "atr_14": ema_data.get("atr_14", 0),
                "atr_pct": round(ema_data.get("atr_14", 0) / max(_close_now, 1) * 100, 3) if _close_now else 0,
                "volume_ratio": ema_data.get("last_volume", 0) / max(ema_data.get("vol_sma20", 1), 1),
                "ema_distance_atr": ema_data.get("ema_distance_atr", 0),
            }
            # Session detection (UTC hour)
            import datetime as _dt
            _utc_hour = _dt.datetime.utcnow().hour
            if 0 <= _utc_hour < 8:
                _market_ctx["session"] = "asian"
            elif 8 <= _utc_hour < 14:
                _market_ctx["session"] = "london"
            elif 14 <= _utc_hour < 21:
                _market_ctx["session"] = "new_york"
            else:
                _market_ctx["session"] = "off_hours"

            # ── Get external regime ──
            external_regime = "unknown"
            if regime_data:
                external_regime = regime_data.get("regime", "unknown")

            # ── Regime Classification (BEFORE active guard — lightweight) ──
            regime_eval = self.regime_engine.evaluate(ema_data, external_regime)
            regime = regime_eval.get("regime", "NO_TREND")

            # ── CROSS DETECTION DIAGNOSTIC: Full comparison chain for tracked symbols ──
            _e20_now = ema_data.get("ema20", 0)
            _e50_now = ema_data.get("ema50", 0)
            _e144_now = ema_data.get("ema144", 0)
            _e200_now = ema_data.get("ema200", 0)
            _e20_prev = ema_data.get("ema20_prev", 0)
            _e50_prev = ema_data.get("ema50_prev", 0)
            _e144_prev = ema_data.get("ema144_prev", 0)
            _e200_prev = ema_data.get("ema200_prev", 0)
            _close_now = ema_data.get("last_close", 0)
            _slope20 = ema_data.get("ema20_slope", 0)
            _slope50 = ema_data.get("ema50_slope", 0)
            _slope144 = ema_data.get("ema144_slope", 0)
            _slope200 = ema_data.get("ema200_slope", 0)
            _simple_cross_up = _e20_prev <= _e50_prev and _e20_now > _e50_now
            _simple_cross_down = _e20_prev >= _e50_prev and _e20_now < _e50_now
            _state_before = self.state_manager.get_state(symbol)
            _chain_aligned = regime_eval.get("ema_chain_aligned", False)

            # ── Track cross-to-chain-aligned ratio ──
            if _simple_cross_up or _simple_cross_down:
                self._ema_cross_counts["cross_total"] = self._ema_cross_counts.get("cross_total", 0) + 1
                if _chain_aligned:
                    self._ema_cross_counts["cross_chain_aligned"] = self._ema_cross_counts.get("cross_chain_aligned", 0) + 1

            # Full comparison chain for tracked symbols and any cross event
            if _simple_cross_up or _simple_cross_down or symbol in ("ETHUSDT", "BTCUSDT", "SOLUSDT"):
                # Chain ordering comparisons (using current values)
                _c20_50 = _e20_now > _e50_now
                _c50_144 = _e50_now > _e144_now
                _c144_200 = _e144_now > _e200_now
                _slope144_pos = _slope144 > 0
                _slope200_pos = _slope200 > 0
                _price_above = _close_now > _e144_now and _close_now > _e200_now

                # Numerical differences (positive = aligned for BUY)
                _diff_20_50 = _e20_now - _e50_now
                _diff_50_144 = _e50_now - _e144_now
                _diff_144_200 = _e144_now - _e200_now

                # Build failure reasons (only what failed)
                _fails = []
                if not _c20_50:
                    _fails.append(f"20>50 FAIL ({_e20_now:.4f}<={_e50_now:.4f} diff={_diff_20_50:.4f})")
                if not _c50_144:
                    _fails.append(f"50>144 FAIL ({_e50_now:.4f}<={_e144_now:.4f} diff={_diff_50_144:.4f})")
                if not _c144_200:
                    _fails.append(f"144>200 FAIL ({_e144_now:.4f}<={_e200_now:.4f} diff={_diff_144_200:.4f})")
                if not _slope144_pos:
                    _fails.append(f"slope144<=0 ({_slope144:.6f})")
                if not _slope200_pos:
                    _fails.append(f"slope200<=0 ({_slope200:.6f})")
                if not _price_above:
                    _fails.append(f"price<=EMA144/200 ({_close_now:.4f})")

                _verdict = "BUY_MODE" if (_c20_50 and _c50_144 and _c144_200 and _slope144_pos and _slope200_pos and _price_above) else "NO_TREND"
                _fail_str = " | ".join(_fails) if _fails else "ALL_PASS"
                # Log with prev/now values and numerical diffs for unambiguous comparison
                logger.info(
                    "🔍 FULL_CHAIN sym={} "
                    "prev: 20={:.4f} 50={:.4f} 144={:.4f} 200={:.4f} "
                    "now: 20={:.4f} 50={:.4f} 144={:.4f} 200={:.4f} "
                    "diffs: 20-50={:+.4f} 50-144={:+.4f} 144-200={:+.4f} "
                    "slopes: 20={:.6f} 50={:.6f} 144={:.6f} 200={:.6f} "
                    "price={:.4f} "
                    "cross_up={} cross_down={} chain={} regime={} verdict={} "
                    "fails=[{}]",
                    symbol,
                    _e20_prev, _e50_prev, _e144_prev, _e200_prev,
                    _e20_now, _e50_now, _e144_now, _e200_now,
                    _diff_20_50, _diff_50_144, _diff_144_200,
                    _slope20, _slope50, _slope144, _slope200,
                    _close_now,
                    _simple_cross_up, _simple_cross_down,
                    _chain_aligned, regime, _verdict,
                    _fail_str,
                )
                # ── PER-SYMBOL EVENT: Record regime decision with full context ──
                _ema_snap = {
                    "20": _e20_now, "50": _e50_now, "144": _e144_now, "200": _e200_now,
                    "close": _close_now,
                    "prev_20": _e20_prev, "prev_50": _e50_prev,
                    "prev_144": _e144_prev, "prev_200": _e200_prev,
                }
                _cross_extra = {
                    "direction": "UP" if _simple_cross_up else ("DOWN" if _simple_cross_down else None),
                    "prev20": _e20_prev, "prev50": _e50_prev,
                    "now20": _e20_now, "now50": _e50_now,
                    "crossed": _simple_cross_up or _simple_cross_down,
                }
                _chain_extra = {
                    "20_gt_50": _c20_50, "50_gt_144": _c50_144, "144_gt_200": _c144_200,
                    "slope144": _slope144, "slope200": _slope200,
                    "price_above": _price_above,
                    "diff_20_50": _diff_20_50, "diff_50_144": _diff_50_144, "diff_144_200": _diff_144_200,
                    "slopes": {"20": _slope20, "50": _slope50, "144": _slope144, "200": _slope200},
                    "fails": _fails if _fails else None,
                }
                # ── Config hash for reproducibility ──
                import hashlib as _hl
                _config_str = (
                    f"ema={ema_v5_config.ema.fast}/{ema_v5_config.ema.medium}/{ema_v5_config.ema.institutional}/{ema_v5_config.ema.long_term}"
                    f"|slope_thr={ema_v5_config.trend.slope_threshold}"
                    f"|min_conf={ema_v5_config.confidence.min_confidence}"
                    f"|min_rr={ema_v5_config.signal.min_rr}"
                    f"|sl_atr={ema_v5_config.signal.sl_atr_mult}"
                    f"|touch={ema_v5_config.pullback.touch_tolerance_pct}"
                )
                _config_hash = _hl.md5(_config_str.encode()).hexdigest()[:8]

                # ── Generate candidate_id when cross detected ──
                if _simple_cross_up or _simple_cross_down:
                    _direction = "BUY" if _simple_cross_up else "SELL"
                    _cid = self._generate_candidate_id(symbol, _direction)
                    self._current_candidate_id[symbol] = _cid
                    _extra_with_ctx = {**_cross_extra, "ohlc": _ohlc, "market": _market_ctx, "config_hash": _config_hash}
                    self._record_event(symbol, "ema_cross", True,
                                       f"direction={_cross_extra['direction']} 20={_e20_now:.4f} 50={_e50_now:.4f}",
                                       _ema_snap, _extra_with_ctx, _cid, _closed_candle_time)
                if _chain_aligned:
                    _extra_with_ctx = {**_chain_extra, "ohlc": _ohlc, "market": _market_ctx, "config_hash": _config_hash}
                    self._record_event(symbol, "chain_alignment", True, _verdict, _ema_snap, _extra_with_ctx,
                                       closed_candle_time=_closed_candle_time)
                else:
                    _extra_with_ctx = {**_chain_extra, "ohlc": _ohlc, "market": _market_ctx, "config_hash": _config_hash}
                    self._record_event(symbol, "chain_alignment", False,
                                       _fail_str if _fails else regime_eval.get("reason", ""),
                                       _ema_snap, _extra_with_ctx, closed_candle_time=_closed_candle_time)

            # ── 5m Chain Formation Tracking (BEFORE active guard — always runs) ──
            # Maintains BOTH BUY and SELL chain states per symbol so enrichment
            # can always find the chain data matching a signal's side.
            chain_aligned = regime_eval.get("ema_chain_aligned", False)
            _e20 = ema_data.get("ema20", 0)
            _e50 = ema_data.get("ema50", 0)
            _e144 = ema_data.get("ema144", 0)
            _e200 = ema_data.get("ema200", 0)
            _e20p = ema_data.get("ema20_prev", 0)
            _e50p = ema_data.get("ema50_prev", 0)
            _e144p = ema_data.get("ema144_prev", 0)
            _e200p = ema_data.get("ema200_prev", 0)

            if symbol not in self._chain_tracker:
                self._chain_tracker[symbol] = {}

            if chain_aligned and regime in (BUY_MODE, SELL_MODE):
                _dir = "BUY" if regime == BUY_MODE else "SELL"
                ct = self._chain_tracker[symbol].get(_dir)
                if not ct or ct.get("chain_regime") != regime:
                    self._chain_tracker[symbol][_dir] = {
                        "chain_price": ema_data.get("last_close", 0),
                        "chain_time": time.time(),
                        "bars_since": 0,
                        "chain_regime": regime,
                        "ema20_x_50": _e20,
                        "ema50_x_144": _e50,
                        "ema144_x_200": _e144,
                    }
                    # ── PIPELINE ACCOUNTING: Record EMA cross event ──
                    if regime == BUY_MODE:
                        self._ema_cross_counts["cross_up"] += 1
                    elif regime == SELL_MODE:
                        self._ema_cross_counts["cross_down"] += 1
                    # ── DIAGNOSTIC: Log cross event (no strategy change) ──
                    try:
                        from scanner.ema_v5.cross_logger import get_cross_logger
                        _cross_log = get_cross_logger()
                        _cross_log.record_cross(
                            symbol=symbol,
                            side=_dir,
                            cross_price=ema_data.get("last_close", 0),
                            cross_time=time.time(),
                            ema20=_e20,
                            ema50=_e50,
                            ema20_slope=ema_data.get("ema20_slope", 0),
                            ema50_slope=ema_data.get("ema50_slope", 0),
                            atr=ema_data.get("atr", 0),
                            volume_ratio=ema_data.get("volume_ratio", 0),
                            candle_body_ratio=ema_data.get("candle_body_ratio", 0),
                            regime=regime,
                        )
                    except Exception:
                        pass
                else:
                    ct["bars_since"] = ct.get("bars_since", 0) + 1
                    if regime == BUY_MODE:
                        if _e20 > _e50 and not (_e20p > _e50p):
                            ct["ema20_x_50"] = _e20
                        if _e50 > _e144 and not (_e50p > _e144p):
                            ct["ema50_x_144"] = _e50
                        if _e144 > _e200 and not (_e144p > _e200p):
                            ct["ema144_x_200"] = _e144
                    else:
                        if _e20 < _e50 and not (_e20p < _e50p):
                            ct["ema20_x_50"] = _e20
                        if _e50 < _e144 and not (_e50p < _e144p):
                            ct["ema50_x_144"] = _e50
                        if _e144 < _e200 and not (_e144p < _e200p):
                            ct["ema144_x_200"] = _e144

            # ── 1H Chain Tracking (BEFORE active guard) ──
            # Needs 220+ candles for EMA200 warmup (same as 5m min_candles)
            klines_1h = market_data.get("klines", {}).get("1h", [])
            if klines_1h and len(klines_1h) >= ema_v5_config.ema.min_candles:
                _htf = self._compute_htf_chain(symbol, klines_1h)
                if _htf:
                    logger.debug("📊 HTF_CHAIN sym={} pattern={} regime={}", symbol, _htf.get("chain_pattern", ""), _htf.get("regime", ""))
            elif klines_1h:
                logger.debug("📊 HTF_CHAIN sym={} insufficient_1h_candles={}/{}", symbol, len(klines_1h), ema_v5_config.ema.min_candles)

            # ── ACTIVE STATE GUARD ──
            _current_state = self.state_manager.get_state(symbol)
            if _current_state in (ACTIVE_BUY, ACTIVE_SELL):
                self._track_rejection("active_position")
                _pgates.append(("active_guard", False, _current_state))
                lifecycle_log.transition(symbol, _current_state, _current_state, "active_guard_skip")
                logger.debug("EMA_V5_TRACE sym={} gates=fast✓→active_guard SKIP state={}", symbol, _current_state)
                return None

            # ── State Machine ──
            current_state = self.state_manager.get_state(symbol)
            if regime not in ("NO_TREND",) and regime != current_state:
                if regime in (BUY_MODE, SELL_MODE):
                    _journey.append(regime)

            if regime == "NO_TREND":
                if current_state != NO_TREND:
                    self.state_manager.set_state(symbol, NO_TREND)
                    # Track pre-pullback regime loss (BUY_MODE/SELL_MODE → NO_TREND)
                    if current_state in (BUY_MODE, SELL_MODE):
                        self._pipeline_accounting["regime_lost_pre"] += 1
                    # Track in-pullback regime loss (WAITING_PULLBACK → NO_TREND)
                    elif current_state == WAITING_PULLBACK:
                        self._pipeline_accounting["regime_lost_in_pull"] += 1
                    lifecycle_log.transition(symbol, current_state, NO_TREND, "regime_no_trend")
                self._stage_counts["regime"] += 1
                self._track_rejection("regime")
                _rreason = regime_eval.get("reason", "no_trend")
                self.observer.record_rejection(symbol, "regime", reason=_rreason)
                self.pipeline_monitor.lifecycle.stage_reject(symbol, "regime", _rreason)
                self.pipeline_monitor.daily_recon.record_rejection("regime")
                # ── DIAGNOSTIC: Log rejection for cross analysis (no strategy change) ──
                try:
                    from scanner.ema_v5.cross_logger import get_cross_logger
                    _cross_log = get_cross_logger()
                    _ct = self._chain_tracker.get(symbol, {}).get("BUY" if regime == "BUY_MODE" else "SELL", {})
                    if _ct:
                        _cross_log.record_rejection(
                            symbol=symbol, side="BUY" if regime == "BUY_MODE" else "SELL",
                            cross_time=_ct.get("chain_time", 0), cross_price=_ct.get("chain_price", 0),
                            stage="regime", reason=_rreason, regime=_ct.get("chain_regime", ""),
                        )
                        # Also record as missed opportunity
                        _cross_log.record_missed_opportunity(
                            symbol=symbol, side="BUY" if regime == "BUY_MODE" else "SELL",
                            cross_time=_ct.get("chain_time", 0), cross_price=_ct.get("chain_price", 0),
                            rejection_stage="regime",
                        )
                except Exception:
                    pass
                _gates_str = "→".join([f"{g}{'✓' if p else '✗'}" for g, p, _ in _pgates])
                logger.debug("EMA_V5_TRACE sym={} gates={}→regime✗ reason={}", symbol, _gates_str, _rreason)
                return None
            self._stage_passed["regime"] += 1
            # ── Track regime CREATION (only on state transition, not every scan) ──
            _prev_state = self.state_manager.get_state(symbol)
            if regime == BUY_MODE and _prev_state != BUY_MODE:
                self._ema_cross_counts["buy_mode_created"] += 1
                logger.info("📊 REGIME_CREATE sym={} {} → {} (buy_mode_created={})", symbol, _prev_state, regime, self._ema_cross_counts["buy_mode_created"])
            elif regime == SELL_MODE and _prev_state != SELL_MODE:
                self._ema_cross_counts["sell_mode_created"] += 1
                logger.info("📊 REGIME_CREATE sym={} {} → {} (sell_mode_created={})", symbol, _prev_state, regime, self._ema_cross_counts["sell_mode_created"])
            self.pipeline_monitor.lifecycle.stage_pass(symbol, "regime", regime_eval.get("reason", ""))
            self.pipeline_monitor.daily_recon.record_event("regime_pass")

            # ── LIFECYCLE TRACKER: Log candidate entry when regime passes ──
            try:
                from .candidate_lifecycle_tracker import get_lifecycle_tracker
                _lc_tracker = get_lifecycle_tracker()
                _candidate_id = _lc_tracker.log_candidate_entry(
                    symbol=symbol,
                    direction="LONG" if regime == "BUY_MODE" else "SHORT",
                    regime=regime,
                    entry_price=ema_data.get("last_close", 0),
                    atr_14=ema_data.get("atr_14", 0),
                    ema20=ema_data.get("ema20", 0),
                    ema50=ema_data.get("ema50", 0),
                    ema144=ema_data.get("ema144", 0),
                    ema200=ema_data.get("ema200", 0),
                    volume_ratio=ema_data.get("last_volume", 0) / ema_data.get("vol_sma20", 1) if ema_data.get("vol_sma20", 0) > 0 else 0,
                )
                _lc_tracker.update_pipeline_stage(_candidate_id, "regime", passed=True)
            except Exception as _lc_err:
                _candidate_id = -1
                logger.debug("Lifecycle tracker error: {}", _lc_err)

            # ── Trend Analysis ──
            trend_eval = self.trend_engine.evaluate(ema_data, regime)
            if not trend_eval.get("direction"):
                self._stage_counts["trend"] += 1
                self._track_rejection("trend")
                _tdetail = f"direction=None score={trend_eval.get('trend_score', 0):.0f}"
                self.observer.record_rejection(symbol, "trend", regime=regime, reason=_tdetail)
                self.pipeline_monitor.lifecycle.stage_reject(symbol, "trend", _tdetail)
                self.pipeline_monitor.daily_recon.record_rejection("trend")
                # ── DIAGNOSTIC: Log rejection for cross analysis (no strategy change) ──
                try:
                    from scanner.ema_v5.cross_logger import get_cross_logger
                    _cross_log = get_cross_logger()
                    _ct = self._chain_tracker.get(symbol, {}).get("BUY" if regime == "BUY_MODE" else "SELL", {})
                    if _ct:
                        _cross_log.record_rejection(
                            symbol=symbol, side="BUY" if regime == "BUY_MODE" else "SELL",
                            cross_time=_ct.get("chain_time", 0), cross_price=_ct.get("chain_price", 0),
                            stage="trend", reason=_tdetail, regime=_ct.get("chain_regime", ""),
                        )
                        # Also record as missed opportunity
                        _cross_log.record_missed_opportunity(
                            symbol=symbol, side="BUY" if regime == "BUY_MODE" else "SELL",
                            cross_time=_ct.get("chain_time", 0), cross_price=_ct.get("chain_price", 0),
                            rejection_stage="trend",
                        )
                except Exception:
                    pass
                _gates_str = "→".join([f"{g}{'✓' if p else '✗'}" for g, p, _ in _pgates])
                logger.debug("EMA_V5_TRACE sym={} gates={}→trend✗ regime={} {}", symbol, _gates_str, regime, _tdetail)
                # ── LIFECYCLE TRACKER: Record trend rejection ──
                try:
                    _lc_tracker.update_pipeline_stage(_candidate_id, "trend", passed=False, reason=_tdetail)
                except Exception:
                    pass
                return None
            _pgates.append(("trend", True, f"dir={trend_eval.get('direction')}"))
            self._stage_passed["trend"] += 1
            self.pipeline_monitor.lifecycle.stage_pass(symbol, "trend")
            self.pipeline_monitor.daily_recon.record_event("trend_pass")
            # ── LIFECYCLE TRACKER: Record trend pass ──
            try:
                _lc_tracker.update_pipeline_stage(_candidate_id, "trend", passed=True)
                _lc_tracker.update_scores(
                    _candidate_id,
                    trend_score=trend_eval.get("trend_score", 0),
                )
            except Exception:
                pass

            # ── Trend Maturity Scoring (NEW) ──
            maturity_eval = self.trend_maturity_engine.evaluate(ema_data, klines, regime)
            _maturity_score = maturity_eval.get("maturity_score", 50)
            _maturity_class = maturity_eval.get("classification", "UNKNOWN")
            logger.info(
                "📊 TREND_MATURITY sym={} score={:.1f} class={} dist={:.1f}atr slope={:.2f} bars={}",
                symbol, _maturity_score, _maturity_class,
                maturity_eval.get("distance_atr", 0),
                maturity_eval.get("slope_ratio", 1.0),
                maturity_eval.get("consecutive_bars", 0),
            )
            # Note: Trend maturity is scored, not a hard gate
            # Score feeds into confidence engine for weighted decision

            # ── Pullback Detection ──
            pullback_eval = self.pullback_engine.evaluate(klines, ema_data, regime)
            
            # ── TRACE: Log pullback result with explicit reasons ──
            if symbol in self._tracked_symbols:
                _detected = pullback_eval.get("pullback_detected", False)
                _touch = pullback_eval.get("touch_level", None)
                _bounce = pullback_eval.get("bounce_confirmed", False)
                _reason = pullback_eval.get("reason", "?")
                _pb_diag = pullback_eval.get("diagnostics", {})
                _mode = _pb_diag.get("mode", "unknown")
                _atr = _pb_diag.get("atr_14", 0)
                _ema20_dist_atr = _pb_diag.get("ema20_dist_atr", 0)
                _ema50_dist_atr = _pb_diag.get("ema50_dist_atr", 0)
                _structure = pullback_eval.get("structure_intact", True)
                
                if _detected:
                    logger.info(
                        "🔍 LIFECYCLE sym={} PULLBACK_OK mode={} touch={} bounce={} structure={} ema20_dist={:.2f}atr ema50_dist={:.2f}atr regime={}",
                        symbol, _mode, _touch, _bounce, _structure,
                        _ema20_dist_atr, _ema50_dist_atr, regime,
                    )
                else:
                    logger.info(
                        "🔍 LIFECYCLE sym={} PULLBACK_FAIL mode={} reason={} ema20_dist={:.2f}atr ema50_dist={:.2f}atr atr={:.2f} regime={}",
                        symbol, _mode, _reason, _ema20_dist_atr, _ema50_dist_atr, _atr, regime,
                    )
            
            if not pullback_eval.get("pullback_detected"):
                if current_state == NO_TREND:
                    self.state_manager.set_state(symbol, regime)
                    lifecycle_log.transition(symbol, NO_TREND, regime, "regime_classified")
                self._stage_counts["pullback"] += 1
                self._track_rejection("pullback")
                _preason = pullback_eval.get("reason", "no_pullback")
                _pb_diag = pullback_eval.get("diagnostics", {})
                _mode = _pb_diag.get("mode", "unknown")
                
                # ── PULLBACK REJECTION AUDIT: Track detailed rejection reasons ──
                if "not_near_ema" in _preason or "outside_tolerance" in _preason:
                    self._pullback_reject_reasons["ema20_touched_outside_tolerance"] += 1
                elif "pullback_too_deep" in _preason:
                    self._pullback_reject_reasons["pullback_too_deep"] += 1
                elif "structure_broken" in _preason:
                    self._pullback_reject_reasons["structure_broken"] += 1
                elif "missing_data" in _preason:
                    self._pullback_reject_reasons["missing_data"] += 1
                elif "no_trend" in _preason:
                    self._pullback_reject_reasons["no_trend"] += 1
                else:
                    self._pullback_reject_reasons["other"] += 1
                
                # ── PULLBACK REJECTION DETAIL: Log for each regime candidate ──
                if len(self._pullback_reject_details) < self._max_pullback_reject_details:
                    _reject_detail = {
                        "symbol": symbol,
                        "regime": regime,
                        "reason": _preason,
                        "mode": _mode,
                        "ema20_dist_pct": _pb_diag.get("ema20_dist_pct", 0),
                        "ema50_dist_pct": _pb_diag.get("ema50_dist_pct", 0),
                        "ema20_dist_atr": _pb_diag.get("ema20_dist_atr", 0),
                        "ema50_dist_atr": _pb_diag.get("ema50_dist_atr", 0),
                        "near_ema20": _pb_diag.get("near_ema20", False),
                        "near_ema50": _pb_diag.get("near_ema50", False),
                        "atr_14": _pb_diag.get("atr_14", 0),
                        "atr_threshold": _pb_diag.get("atr_threshold", 0),
                        "last_close": _pb_diag.get("last_close", 0),
                        "candles_checked": _pb_diag.get("candles_checked", 0),
                        "timestamp": time.time(),
                    }
                    self._pullback_reject_details.append(_reject_detail)
                    # Log every 10th rejection to avoid spam
                    if sum(self._pullback_reject_reasons.values()) % 10 == 0:
                        logger.info(
                            "📊 PULLBACK_AUDIT sym={} mode={} regime={} reason={} ema20_dist={:.2f}atr ema50_dist={:.2f}atr atr={:.2f} threshold={:.2f}",
                            symbol, _mode, regime, _preason,
                            _pb_diag.get("ema20_dist_atr", 0),
                            _pb_diag.get("ema50_dist_atr", 0),
                            _pb_diag.get("atr_14", 0),
                            _pb_diag.get("atr_threshold", 0.5),
                        )
                
                self.observer.record_rejection(symbol, "pullback", regime=regime, reason=_preason)
                self.pipeline_monitor.lifecycle.stage_reject(symbol, "pullback", _preason)
                self.pipeline_monitor.daily_recon.record_rejection("pullback")
                # ── DIAGNOSTIC: Log rejection for cross analysis (no strategy change) ──
                try:
                    from scanner.ema_v5.cross_logger import get_cross_logger
                    _cross_log = get_cross_logger()
                    _ct = self._chain_tracker.get(symbol, {}).get("BUY" if regime == "BUY_MODE" else "SELL", {})
                    if _ct:
                        _cross_log.record_rejection(
                            symbol=symbol, side="BUY" if regime == "BUY_MODE" else "SELL",
                            cross_time=_ct.get("chain_time", 0), cross_price=_ct.get("chain_price", 0),
                            stage="pullback", reason=_preason, regime=_ct.get("chain_regime", ""),
                        )
                        # Also record as missed opportunity
                        _cross_log.record_missed_opportunity(
                            symbol=symbol, side="BUY" if regime == "BUY_MODE" else "SELL",
                            cross_time=_ct.get("chain_time", 0), cross_price=_ct.get("chain_price", 0),
                            rejection_stage="pullback",
                        )
                except Exception:
                    pass
                _gates_str = "→".join([f"{g}{'✓' if p else '✗'}" for g, p, _ in _pgates])
                logger.debug("EMA_V5_TRACE sym={} gates={}→pullback✗ regime={} reason={}", symbol, _gates_str, regime, _preason)
                # ── LIFECYCLE TRACKER: Record pullback rejection ──
                try:
                    _lc_tracker.update_pipeline_stage(_candidate_id, "pullback", passed=False, reason=_preason)
                except Exception:
                    pass
                return None
            _pgates.append(("pullback", True, "detected"))
            self._stage_passed["pullback"] += 1
            self.pipeline_monitor.lifecycle.stage_pass(symbol, "pullback")
            self.pipeline_monitor.daily_recon.record_event("pullback_pass")
            # ── LIFECYCLE TRACKER: Record pullback pass ──
            try:
                _lc_tracker.update_pipeline_stage(_candidate_id, "pullback", passed=True)
                _lc_tracker.update_scores(
                    _candidate_id,
                    pullback_score=100,
                )
            except Exception:
                pass

            # ── State transition: WAITING_PULLBACK ──
            _old_state = self.state_manager.get_state(symbol)
            self.state_manager.set_state(symbol, WAITING_PULLBACK)
            self._pullback_reeval["times_entered"] += 1
            self._pipeline_accounting["pullback_entered"] += 1
            lifecycle_log.transition(symbol, _old_state, WAITING_PULLBACK, "pullback_detected")
            _journey.append("WAITING_PULLBACK")

            # ── STATE-TRANSITION AUDIT: Log every pullback pass transition ──
            _pb_diag = pullback_eval.get("diagnostics", {})
            if len(self._state_transition_audit) < self._max_state_transition_audit:
                _transition_entry = {
                    "symbol": symbol,
                    "regime": regime,
                    "previous_state": _old_state,
                    "new_state": WAITING_PULLBACK,
                    "transition_reason": "pullback_detected",
                    "pullback_result": "PASS",
                    "pullback_touch_level": pullback_eval.get("touch_level", "none"),
                    "pullback_depth_pct": pullback_eval.get("pullback_depth_pct", 0),
                    "pullback_depth_atr": pullback_eval.get("pullback_depth_atr", 0),
                    "structure_intact": pullback_eval.get("structure_intact", True),
                    "ema20_dist_atr": _pb_diag.get("ema20_dist_atr", 0),
                    "ema50_dist_atr": _pb_diag.get("ema50_dist_atr", 0),
                    "timestamp": time.time(),
                }
                self._state_transition_audit.append(_transition_entry)
                # Log every 5th transition to avoid spam
                if len(self._state_transition_audit) % 5 == 0:
                    logger.info(
                        "📊 STATE_AUDIT sym={} regime={} {} → {} reason={} touch={} depth={:.2f}%",
                        symbol, regime, _old_state, WAITING_PULLBACK, "pullback_detected",
                        pullback_eval.get("touch_level", "none"),
                        pullback_eval.get("pullback_depth_pct", 0)
                    )

            # ── TRACE: Log pullback detection for tracked symbols ──
            if symbol in self._tracked_symbols:
                _touch = pullback_eval.get("touch_level", "?")
                _bounce = pullback_eval.get("bounce_confirmed", "?")
                _dist = pullback_eval.get("distance_pct", 0)
                logger.info(
                    "🔍 LIFECYCLE sym={} PULLBACK_OK touch={} bounce={} dist={:.4f}% regime={}",
                    symbol, _touch, _bounce, _dist, regime,
                )

            # ── Candlestick Pattern ──
            candle_eval = self.candle_engine.evaluate(klines, regime)
            
            # ── TRACE: Log candle evaluation for tracked symbols ──
            if symbol in self._tracked_symbols:
                _found = candle_eval.get("pattern_found", False)
                _pattern = candle_eval.get("pattern", "none")
                _diag = candle_eval.get("diagnostics", {})
                _body = _diag.get("body_ratio", 0)
                _wick = _diag.get("wick_ratio", 0)
                if _found:
                    logger.info(
                        "🔍 LIFECYCLE sym={} CANDLE_OK pattern={} body={:.3f} wick={:.2f}",
                        symbol, _pattern, _body, _wick,
                    )
                else:
                    logger.info(
                        "🔍 LIFECYCLE sym={} CANDLE_SKIP body={:.3f}<{:.3f} wick={:.2f}<{:.2f}",
                        symbol, _body, ema_v5_config.candle.body_ratio_min,
                        _wick, ema_v5_config.candle.wick_ratio_min,
                    )
            
            if not candle_eval.get("pattern_found"):
                candle_diag = candle_eval.get("diagnostics", {})
                self._stage_counts["candle"] += 1
                self._pipeline_accounting["candle_evaluated"] += 1
                self._pipeline_accounting["candle_rejected"] += 1
                _cdetail = f"body={candle_diag.get('body_ratio', 0):.2f} wick={candle_diag.get('wick_ratio', 0):.1f}"
                _creason = candle_diag.get("rejection_reason", "no_pattern")
                logger.info(
                    "🔍 CANDLE_REJECT {} | regime={} reason={} body={:.2f} wick={:.1f}",
                    symbol, regime, _creason,
                    candle_diag.get("body_ratio", 0), candle_diag.get("wick_ratio", 0),
                )
                self._track_rejection("candle")
                self.observer.record_rejection(symbol, "candle", regime=regime, reason=_cdetail)
                self.pipeline_monitor.lifecycle.stage_reject(symbol, "candle", _cdetail)
                self.pipeline_monitor.daily_recon.record_rejection("candle")
                # ── DIAGNOSTIC: Log rejection for cross analysis (no strategy change) ──
                try:
                    from scanner.ema_v5.cross_logger import get_cross_logger
                    _cross_log = get_cross_logger()
                    _ct = self._chain_tracker.get(symbol, {}).get("BUY" if regime == "BUY_MODE" else "SELL", {})
                    if _ct:
                        _cross_log.record_rejection(
                            symbol=symbol, side="BUY" if regime == "BUY_MODE" else "SELL",
                            cross_time=_ct.get("chain_time", 0), cross_price=_ct.get("chain_price", 0),
                            stage="candle", reason=_creason, regime=_ct.get("chain_regime", ""),
                        )
                        # Also record as missed opportunity
                        _cross_log.record_missed_opportunity(
                            symbol=symbol, side="BUY" if regime == "BUY_MODE" else "SELL",
                            cross_time=_ct.get("chain_time", 0), cross_price=_ct.get("chain_price", 0),
                            rejection_stage="candle",
                        )
                except Exception:
                    pass
                # ── LIFECYCLE: Log candle rejection (state stays WAITING_PULLBACK) ──
                self._pullback_reeval["candle_evaluated"] += 1
                self._pullback_reeval["candle_rejected"] += 1
                _cur = self.state_manager.get_state(symbol)
                lifecycle_log.transition(symbol, _cur, _cur, f"candle_rejected: {_cdetail}")
                _gates_list = [f"{g}{'Y' if p else 'N'}" for g, p, _d in _pgates]
                _gates_str = "->".join(_gates_list)
                _gates_str = "->".join([f"{g}{'Y' if p else 'N'}" for g, p, _d in _pgates])
                logger.debug("EMA_V5_TRACE sym={} gates={}→candle✗ regime={} {}", symbol, _gates_str, regime, _cdetail)
                # ── LIFECYCLE TRACKER: Record candle rejection ──
                try:
                    _lc_tracker.update_pipeline_stage(_candidate_id, "candle", passed=False, reason=_cdetail)
                except Exception:
                    pass
                return None
            self._pullback_reeval["candle_evaluated"] += 1
            self._pullback_reeval["candle_passed"] += 1
            _pgates.append(("candle", True, f"pattern={candle_eval.get('pattern_name', '?')}"))
            self._stage_passed["candle"] += 1
            self._pipeline_accounting["candle_evaluated"] += 1
            self._pipeline_accounting["candle_passed"] += 1
            self.pipeline_monitor.lifecycle.stage_pass(symbol, "candle", candle_eval.get("pattern_name", ""))
            self.pipeline_monitor.daily_recon.record_event("candle_pass")
            # ── LIFECYCLE TRACKER: Record candle pass ──
            try:
                _lc_tracker.update_pipeline_stage(_candidate_id, "candle", passed=True)
                _lc_tracker.update_scores(
                    _candidate_id,
                    candle_score=candle_eval.get("candle_score", 0),
                )
            except Exception:
                pass

            # ── State transition: WAITING_CONFIRMATION ──
            _old_state2 = self.state_manager.get_state(symbol)
            self.state_manager.set_state(symbol, WAITING_CONFIRMATION)
            lifecycle_log.transition(symbol, _old_state2, WAITING_CONFIRMATION, "candle_pattern_found")
            _journey.append("WAITING_CONFIRMATION")
            
            # ── STATE-TRANSITION AUDIT: Log pullback → confirmation transition ──
            if len(self._state_transition_audit) < self._max_state_transition_audit:
                _confirm_transition = {
                    "symbol": symbol,
                    "regime": regime,
                    "previous_state": _old_state2,
                    "new_state": WAITING_CONFIRMATION,
                    "transition_reason": "candle_pattern_found",
                    "candle_pattern": candle_eval.get("pattern_name", "unknown"),
                    "candle_score": candle_eval.get("candle_score", 0),
                    "pullback_touch_level": pullback_eval.get("touch_level", "none"),
                    "pullback_depth_pct": pullback_eval.get("pullback_depth_pct", 0),
                    "ema20_dist_atr": _pb_diag.get("ema20_dist_atr", 0),
                    "ema50_dist_atr": _pb_diag.get("ema50_dist_atr", 0),
                    "timestamp": time.time(),
                }
                self._state_transition_audit.append(_confirm_transition)
                logger.info(
                    "📊 STATE_AUDIT sym={} regime={} {} → {} reason={} candle={} score={:.0f}",
                    symbol, regime, _old_state2, WAITING_CONFIRMATION, "candle_pattern_found",
                    candle_eval.get("pattern_name", "unknown"), candle_eval.get("candle_score", 0)
                )
            
            # ── TRACE: Log transition to WAITING_CONFIRMATION ──
            if symbol in self._tracked_symbols:
                logger.info(
                    "🔍 TRANSITION_TRACE sym={} → WAITING_CONFIRMATION (candle passed)",
                    symbol,
                )
            
            # ── AUDIT: Track WAITING_CONFIRMATION entry ──
            self.waiting_audit.on_enter(
                symbol=symbol,
                regime=regime,
                confidence=0,  # Not computed yet
                side=trend_eval.get("direction", ""),
            )

            # ── Volume Confirmation ──
            volume_eval = self.volume_engine.evaluate(ema_data)
            
            # ── TRACE: Log volume evaluation for tracked symbols ──
            if symbol in self._tracked_symbols:
                logger.info(
                    "🔍 VOLUME_TRACE sym={} volume_ok={} ratio={:.3f} expanding={}",
                    symbol,
                    volume_eval.get("volume_ok", False),
                    volume_eval.get("volume_ratio", 0),
                    volume_eval.get("volume_expanding", False),
                )
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
            # ── VOLUME OUTCOME AUDIT: record every candle-qualified candidate ──
            # (pass AND reject) with full volume + candle diagnostics. Diagnostic only.
            try:
                _vol_audit_last_vol = ema_data.get("last_volume", 0)
                _vol_audit_prev_vol = ema_data.get("prev_volume", 0)
                _vol_audit_sma = ema_data.get("vol_sma20", 0)
                _va_klines = market_data.get("klines", {}).get(ema_v5_config.primary_tf, [])
                _va_last = _va_klines[-1] if _va_klines else {}
                _va_open = _va_last.get("open", 0)
                _va_close = _va_last.get("close", 0)
                _va_high = _va_last.get("high", 0)
                _va_low = _va_last.get("low", 0)
                _va_range = (_va_high - _va_low) if (_va_high and _va_low and _va_high > _va_low) else 0
                _va_body = abs(_va_close - _va_open) if _va_open else 0
                _va_candle_dir = "bullish" if _va_close > _va_open else ("bearish" if _va_close < _va_open else "doji")
                _va_lower_wick = (min(_va_open, _va_close) - _va_low) if _va_low and _va_open else 0
                _va_upper_wick = (_va_high - max(_va_open, _va_close)) if _va_high and _va_open else 0
                from scanner.ema_v5.volume_outcome_audit import get_volume_outcome_audit
                get_volume_outcome_audit().record_candidate(
                    symbol=symbol,
                    side="BUY" if regime == "BUY_MODE" else "SELL",
                    regime=regime,
                    confirmation_price=ema_data.get("last_close", 0),
                    closed_candle_time=_closed_candle_time,
                    last_volume=_vol_audit_last_vol,
                    prev_volume=_vol_audit_prev_vol,
                    vol_sma20=_vol_audit_sma,
                    volume_ratio=_vol_ratio,
                    pullback_vol_ratio=(_vol_audit_prev_vol / _vol_audit_sma) if _vol_audit_sma > 0 else 0,
                    confirm_vol_ratio=(_vol_audit_last_vol / _vol_audit_sma) if _vol_audit_sma > 0 else 0,
                    confirm_over_pullback=(_vol_audit_last_vol / _vol_audit_prev_vol) if _vol_audit_prev_vol > 0 else 0,
                    expansion=_vol_expanding,
                    candle_direction=_va_candle_dir,
                    candle_pattern=candle_eval.get("pattern_name", ""),
                    candle_body_pct=(_va_body / _va_range * 100) if _va_range > 0 else 0,
                    lower_wick_pct=(_va_lower_wick / _va_range * 100) if _va_range > 0 else 0,
                    upper_wick_pct=(_va_upper_wick / _va_range * 100) if _va_range > 0 else 0,
                    volume_ok=_vol_ok,
                    rejection_reason=volume_eval.get("reason", "unknown"),
                    last_candle=_va_last,
                )
            except Exception:
                pass
            if not volume_eval.get("volume_ok"):
                last_vol = ema_data.get("last_volume", 0)
                vol_sma = ema_data.get("vol_sma20", 0)
                ratio = volume_eval.get("volume_ratio", 0)
                expanding = volume_eval.get("volume_expanding", False)
                vol_reason = volume_eval.get("reason", "unknown")
                # Distinguish: zero_data vs low_volume vs threshold
                if vol_reason in ("zero_volume", "zero_sma", "zero_volume_and_sma"):
                    _vol_diag = f"DATA_ISSUE:{vol_reason}"
                elif ratio > 0:
                    _vol_diag = f"LOW_VOLUME:ratio={ratio:.2f}"
                else:
                    _vol_diag = f"UNKNOWN:{vol_reason}"
                logger.info(
                    "🔍 VOL REJECT {} | last_vol={:.0f} sma20={:.0f} ratio={:.2f} expand={} regime={} diag={}",
                    symbol, last_vol, vol_sma, ratio, expanding, regime, _vol_diag,
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
                # Track volume rejection reasons
                if not _vol_ok and not expanding:
                    self._vol_reject_reasons["both"] += 1
                elif not _vol_ok:
                    self._vol_reject_reasons["low_ratio"] += 1
                elif not expanding:
                    self._vol_reject_reasons["no_expansion"] += 1
                self.observer.record_rejection(
                    symbol, "volume", regime=regime,
                    reason=f"ratio={ratio:.2f}_expand={'yes' if expanding else 'no'}",
                )
                # ── DIAGNOSTIC: Log rejection for cross analysis (no strategy change) ──
                try:
                    from scanner.ema_v5.cross_logger import get_cross_logger
                    _cross_log = get_cross_logger()
                    _ct = self._chain_tracker.get(symbol, {}).get("BUY" if regime == "BUY_MODE" else "SELL", {})
                    if _ct:
                        _cross_log.record_rejection(
                            symbol=symbol, side="BUY" if regime == "BUY_MODE" else "SELL",
                            cross_time=_ct.get("chain_time", 0), cross_price=_ct.get("chain_price", 0),
                            stage="volume", reason=f"ratio={ratio:.2f} expanding={expanding}",
                            regime=_ct.get("chain_regime", ""),
                        )
                        # Also record as missed opportunity
                        _cross_log.record_missed_opportunity(
                            symbol=symbol, side="BUY" if regime == "BUY_MODE" else "SELL",
                            cross_time=_ct.get("chain_time", 0), cross_price=_ct.get("chain_price", 0),
                            rejection_stage="volume",
                        )
                except Exception:
                    pass                # ── LIFECYCLE: Log volume rejection (state stays WAITING_CONFIRMATION) ──
                _cur = self.state_manager.get_state(symbol)
                lifecycle_log.transition(symbol, _cur, _cur, f"volume_rejected: ratio={ratio:.2f}")
                _gates_str = "->".join([f"{g}{'Y' if p else 'N'}" for g, p, _d in _pgates])
                logger.debug("EMA_V5_TRACE sym={} gates={}→vol✗ regime={} ratio={:.2f} expand={}", symbol, _gates_str, regime, ratio, expanding)
                # ── AUDIT: WC exit — volume rejected ──
                self.waiting_audit.on_exit(symbol, "REJECTED:Volume")
                self._pipeline_accounting["signal_cancelled"] += 1
                self.pipeline_monitor.lifecycle.stage_reject(symbol, "volume", f"ratio={ratio:.2f}")
                self.pipeline_monitor.daily_recon.record_rejection("volume")
                # ── VOLUME REJECTION AUDIT: detailed diagnostics for candle-qualified candidates ──
                if len(self._vol_reject_audit) < self._max_vol_reject_audit:
                    _klines = market_data.get("klines", {}).get(ema_v5_config.primary_tf, [])
                    _last_candle = _klines[-1] if _klines else {}
                    _prev_candle = _klines[-2] if len(_klines) >= 2 else {}
                    _pullback_candle_vol = _prev_candle.get("volume", 0)  # pullback candle = previous
                    _confirm_candle_vol = _last_candle.get("volume", 0)   # confirmation candle = current
                    _vol_audit_entry = {
                        "symbol": symbol,
                        "side": "BUY" if regime == "BUY_MODE" else "SELL",
                        "regime": regime,
                        "last_volume": last_vol,
                        "vol_sma20": vol_sma,
                        "volume_ratio": ratio,
                        "threshold": 0.4,
                        "pullback_candle_vol": _pullback_candle_vol,
                        "confirm_candle_vol": _confirm_candle_vol,
                        "pullback_vol_ratio": round(_pullback_candle_vol / max(vol_sma, 1), 2),
                        "confirm_vol_ratio": round(_confirm_candle_vol / max(vol_sma, 1), 2),
                        "volume_expanding": expanding,
                        "rejection_reason": vol_reason,
                        "candle_pattern": candle_eval.get("pattern_name", "unknown"),
                        "timestamp": time.time(),
                    }
                    self._vol_reject_audit.append(_vol_audit_entry)
                    # Log every 5th volume rejection to avoid spam
                    if len(self._vol_reject_audit) % 5 == 0:
                        logger.info(
                            "📊 VOL_AUDIT sym={} side={} ratio={:.2f} threshold={:.2f} pullback_vol={:.0f} confirm_vol={:.0f} sma20={:.0f} expand={}",
                            symbol, _vol_audit_entry["side"], ratio, 0.4,
                            _pullback_candle_vol, _confirm_candle_vol, vol_sma, expanding
                        )
                # ── LIFECYCLE TRACKER: Record volume rejection ──
                try:
                    _lc_tracker.update_pipeline_stage(_candidate_id, "volume", passed=False, reason=f"ratio={ratio:.2f}")
                    _lc_tracker.update_scores(
                        _candidate_id,
                        volume_score=volume_eval.get("volume_score", 0),
                    )
                except Exception:
                    pass
                return None
            _pgates.append(("vol", True, f"ratio={volume_eval.get('volume_ratio', 0):.2f}"))
            self._stage_passed["volume"] += 1
            self.pipeline_monitor.lifecycle.stage_pass(symbol, "volume")
            self.pipeline_monitor.daily_recon.record_event("volume_pass")
            # ── LIFECYCLE TRACKER: Record volume pass ──
            try:
                _lc_tracker.update_pipeline_stage(_candidate_id, "volume", passed=True)
                _lc_tracker.update_scores(
                    _candidate_id,
                    volume_score=volume_eval.get("volume_score", 0),
                )
            except Exception:
                pass

            # ── Confidence Scoring ──
            confidence_eval = self.confidence_engine.compute(
                regime_eval, trend_eval, pullback_eval, candle_eval, volume_eval,
                maturity_eval=maturity_eval,
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
                # ── TRACE: Log confidence rejection for tracked symbols ──
                if symbol in self._tracked_symbols:
                    logger.info(
                        "🔍 CONFIDENCE_TRACE sym={} passed=False conf={:.1f} min={:.1f} reason={}",
                        symbol,
                        confidence_eval.get("confidence", 0),
                        ema_v5_config.confidence.min_confidence,
                        confidence_eval.get("reason", "?"),
                    )
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
                )
                # ── DIAGNOSTIC: Log rejection for cross analysis (no strategy change) ──
                try:
                    from scanner.ema_v5.cross_logger import get_cross_logger
                    _cross_log = get_cross_logger()
                    _ct = self._chain_tracker.get(symbol, {}).get("BUY" if regime == "BUY_MODE" else "SELL", {})
                    if _ct:
                        _cross_log.record_rejection(
                            symbol=symbol, side="BUY" if regime == "BUY_MODE" else "SELL",
                            cross_time=_ct.get("chain_time", 0), cross_price=_ct.get("chain_price", 0),
                            stage="confidence", reason=f"conf={confidence_eval.get('confidence', 0):.1f} min={ema_v5_config.confidence.min_confidence}",
                            filter_value=confidence_eval.get("confidence", 0),
                            threshold_value=ema_v5_config.confidence.min_confidence,
                            regime=_ct.get("chain_regime", ""),
                        )
                        # Also record as missed opportunity
                        _cross_log.record_missed_opportunity(
                            symbol=symbol, side="BUY" if regime == "BUY_MODE" else "SELL",
                            cross_time=_ct.get("chain_time", 0), cross_price=_ct.get("chain_price", 0),
                            rejection_stage="confidence",
                        )
                except Exception:
                    pass                # ── LIFECYCLE: Log confidence rejection (state stays WAITING_CONFIRMATION) ──
                _cur = self.state_manager.get_state(symbol)
                lifecycle_log.transition(symbol, _cur, _cur, 
                                        f"confidence_rejected: {confidence_eval.get('confidence', 0):.1f} < {ema_v5_config.confidence.min_confidence}")
                _gates_str = "->".join([f"{g}{'Y' if p else 'N'}" for g, p, _d in _pgates])
                _conf = confidence_eval.get('confidence', 0)
                _min = ema_v5_config.confidence.min_confidence
                logger.debug("EMA_V5_TRACE sym={} gates={}→conf✗ regime={} conf={:.1f} min={:.1f} breakdown={}", symbol, _gates_str, regime, _conf, _min, breakdown)
                # ── AUDIT: WC exit — confidence rejected ──
                self.waiting_audit.on_exit(symbol, "REJECTED:Confidence")
                self._pipeline_accounting["signal_cancelled"] += 1
                self.pipeline_monitor.lifecycle.stage_reject(symbol, "confidence",
                    f"conf={confidence_eval.get('confidence', 0):.1f} < {ema_v5_config.confidence.min_confidence}")
                self.pipeline_monitor.daily_recon.record_rejection("confidence")
                # ── LIFECYCLE TRACKER: Record confidence rejection ──
                try:
                    _lc_tracker.update_pipeline_stage(_candidate_id, "confidence", passed=False,
                        reason=f"conf={confidence_eval.get('confidence', 0):.1f} < {ema_v5_config.confidence.min_confidence}")
                    _lc_tracker.update_scores(
                        _candidate_id,
                        confidence=confidence_eval.get("confidence", 0),
                    )
                except Exception:
                    pass
                return None
            _gates_str = "→".join([f"{g}{'✓' if p else '✗'}" for g, p, _ in _pgates])
            _conf = confidence_eval.get('confidence', 0)
            logger.info("EMA_V5_TRACE sym={} gates={}→conf✓→SIGNAL regime={} conf={:.1f}", symbol, _gates_str, regime, _conf)
            self._stage_passed["confidence"] += 1
            self.pipeline_monitor.lifecycle.stage_pass(symbol, "confidence", f"conf={_conf:.1f}")
            self.pipeline_monitor.daily_recon.record_event("confidence_pass")
            # ── LIFECYCLE TRACKER: Record confidence pass ──
            try:
                _lc_tracker.update_pipeline_stage(_candidate_id, "confidence", passed=True)
                _lc_tracker.update_scores(
                    _candidate_id,
                    confidence=confidence_eval.get("confidence", 0),
                )
            except Exception:
                pass

            # ── Enrich ema_data with chain formation + display fields ──
            _dir = "BUY" if regime == "BUY_MODE" else "SELL"
            _ct = self._chain_tracker.get(symbol, {}).get(_dir, {})
            _htf_dir = "buy" if regime == "BUY_MODE" else "sell"
            _htf = self._htf_tracker.get(symbol, {}).get(_htf_dir, {})
            _atr_val = ema_data.get("atr_14", 0)
            _atr_prev = ema_data.get("atr_prev", 0)
            _buy_vol = ema_data.get("buy_volume", 0)
            _sell_vol = ema_data.get("sell_volume", 0)
            ema_data["ema_chain_pattern"] = (
                "20>50>144>200" if regime == "BUY_MODE" else "20<50<144<200"
            )
            ema_data["ema_cross_price"] = _ct.get("chain_price", 0)
            ema_data["ema_cross_time"] = _ct.get("chain_time", 0)
            ema_data["bars_since_chain"] = _ct.get("bars_since", 0)
            ema_data["ema20_x_50"] = _ct.get("ema20_x_50", 0)
            ema_data["ema50_x_144"] = _ct.get("ema50_x_144", 0)
            ema_data["ema144_x_200"] = _ct.get("ema144_x_200", 0)
            ema_data["htf_chain_pattern"] = _htf.get("chain_pattern", "")
            ema_data["htf_cross_price"] = _htf.get("chain_price", 0)
            ema_data["htf_cross_time"] = _htf.get("chain_time", 0)
            ema_data["htf_bars_since"] = _htf.get("bars_since", 0)
            ema_data["htf_regime"] = _htf.get("regime", "NO_TREND")
            ema_data["atr_expanding"] = _atr_val > _atr_prev if _atr_prev > 0 else True
            ema_data["volume_normalized"] = (
                round(_buy_vol / ema_data.get("vol_sma20", 1), 2) if regime == "BUY_MODE"
                else -round(_sell_vol / ema_data.get("vol_sma20", 1), 2)
            )

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
                maturity_eval=maturity_eval,
            )

            if signal:
                self._signal_count += 1
                self._stage_counts["signal"] += 1
                self._stage_passed["signal"] += 1
                self._pipeline_accounting["signal_emitted"] += 1
                self.observer.record_signal(symbol, signal.get("confidence", 0))
                # ── PER-SYMBOL EVENT: Signal emitted with full details ──
                self._record_event(symbol, "signal_emitted", True,
                    f"conf={signal.get('confidence', 0):.1f} side={signal.get('side', '?')} entry={signal.get('entry', 0):.4f}",
                    None, {
                        "confidence": signal.get("confidence", 0),
                        "side": signal.get("side", "?"),
                        "entry": signal.get("entry", 0),
                        "sl": signal.get("sl", 0),
                        "take_profit_1": signal.get("take_profit_1", 0),
                        "regime": regime,
                    }, closed_candle_time=_closed_candle_time)
                # ── LIFECYCLE TRACKER: Record signal pass ──
                try:
                    _lc_tracker.update_pipeline_stage(_candidate_id, "signal", passed=True)
                    _lc_tracker.update_scores(
                        _candidate_id,
                        confidence=signal.get("confidence", 0),
                    )
                    _lc_tracker.update_post_confidence_gates(
                        _candidate_id,
                        gate_duplicate=0,
                        gate_cooldown=0,
                        gate_entry_atr=0,
                        gate_momentum=0,
                        gate_rr=0,
                        gate_final="passed",
                    )
                except Exception:
                    pass
                # ── LIFECYCLE: Log signal generated ──
                lifecycle_log.transition(symbol, WAITING_CONFIRMATION, "SIGNAL_GENERATED", 
                                        f"conf={signal.get('confidence', 0):.1f} side={signal.get('side', '?')}",
                                        confidence=signal.get("confidence", 0), side=signal.get("side", ""))
                # ── DIAGNOSTIC: Log signal for cross analysis (no strategy change) ──
                try:
                    from scanner.ema_v5.cross_logger import get_cross_logger
                    _cross_log = get_cross_logger()
                    _cross_log.record_signal(
                        symbol=symbol,
                        signal_price=signal.get("entry", 0),
                        signal_time=time.time(),
                    )
                except Exception:
                    pass
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
                self._pipeline_accounting["signal_suppressed"] += 1
                self.pipeline_monitor.lifecycle.stage_reject(symbol, "signal_engine",
                    "passed_confidence_but_signal_engine_rejected")
                self.pipeline_monitor.daily_recon.record_rejection("signal_engine")
                # ── LIFECYCLE DB: Record which specific gate rejected ──
                _gate_reason = self.signal_engine.last_rejection_reason or "unknown"
                try:
                    _lc_tracker.update_post_confidence_gates(
                        _candidate_id,
                        gate_duplicate=1 if _gate_reason == "duplicate" else 0,
                        gate_cooldown=1 if _gate_reason == "cooldown" else 0,
                        gate_entry_atr=1 if _gate_reason == "invalid_entry_atr" else 0,
                        gate_momentum=1 if _gate_reason == "low_momentum" else 0,
                        gate_rr=1 if _gate_reason == "rr_too_low" else 0,
                        gate_final=_gate_reason,
                    )
                except Exception:
                    pass
                logger.info(
                    "🟡 CONF_PASS_BUT_NO_SIGNAL: {} conf={:.1f} regime={} gate={}",
                    symbol, confidence_eval.get("confidence", 0), regime, _gate_reason,
                )
                # ── PER-SYMBOL EVENT: Signal suppressed with gate details ──
                self._record_event(symbol, "signal_suppressed", False,
                    f"conf={confidence_eval.get('confidence', 0):.1f} gate={_gate_reason}",
                    None, {
                        "gate": _gate_reason,
                        "confidence": confidence_eval.get("confidence", 0),
                        "min_confidence": ema_v5_config.confidence.min_confidence,
                        "regime": regime,
                        "gate_stats": self.signal_engine.get_gate_stats(),
                    }, closed_candle_time=_closed_candle_time)

            # ── PERFORMANCE METRICS: Record candidate evaluation latency ──
            _eval_latency_ms = (time.monotonic() - _eval_start) * 1000
            self.perf_metrics.record_candidate(
                confidence=confidence_eval.get("confidence", 0) if 'confidence_eval' in dir() else 0,
                latency_ms=_eval_latency_ms,
            )
            self.observer.record_stage_latency("confidence", _eval_latency_ms)

            # ── FINALIZE: Snapshot counters at end of each evaluate() call ──
            self._finalize_scan_cycle()

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

    def _record_event(self, symbol: str, stage: str, passed: bool, detail: str = "",
                      ema_snapshot: Optional[Dict] = None, extra: Optional[Dict] = None,
                      candidate_id: Optional[str] = None,
                      closed_candle_time: Optional[float] = None) -> None:
        """Record a per-symbol pipeline event for the event timeline.

        Only records events for tracked symbols (ETHUSDT, BTCUSDT, SOLUSDT).
        Keeps the last N events per symbol.

        Event model:
            time: float         — processing timestamp
            stage: str          — pipeline stage name
            passed: bool        — whether stage passed
            detail: str         — human-readable summary
            ema_snapshot: dict  — full EMA values (prev + now)
            extra: dict         — stage-specific structured data
            candidate_id: str   — unique lifecycle identifier
            closed_candle_time: float — timestamp of the closed candle being evaluated
            metadata: dict      — scanner metadata (exchange, timeframe, version, etc.)
        """
        if symbol not in self._tracked_symbols:
            return
        if symbol not in self._symbol_event_log:
            self._symbol_event_log[symbol] = []
        event = {
            "time": time.time(),
            "stage": stage,
            "passed": passed,
            "detail": detail,
        }
        if ema_snapshot:
            event["ema_snapshot"] = ema_snapshot
        if extra:
            event["extra"] = extra
        if candidate_id:
            event["candidate_id"] = candidate_id
        elif symbol in self._current_candidate_id:
            event["candidate_id"] = self._current_candidate_id[symbol]
        if closed_candle_time:
            event["closed_candle_time"] = closed_candle_time
            event["closed_candle_dt"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(closed_candle_time))
        # Scanner metadata (exchange, timeframe, version)
        event["metadata"] = {
            **self._scanner_metadata,
            "symbol": symbol,
            "scan_id": f"{symbol}_{int(time.time())}",
        }
        self._symbol_event_log[symbol].append(event)
        # Trim to max
        if len(self._symbol_event_log[symbol]) > self._max_events_per_symbol:
            self._symbol_event_log[symbol] = self._symbol_event_log[symbol][-self._max_events_per_symbol:]

    def _generate_candidate_id(self, symbol: str, direction: str) -> str:
        """Generate a unique candidate ID for lifecycle tracking.

        Format: {symbol}_{YYYYMMDD}_{HHMMSS}_{direction}
        Example: ETHUSDT_20260729_214500_BUY
        """
        now = time.time()
        ts = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
        return f"{symbol}_{ts}_{direction.upper()}"

    def _finalize_scan_cycle(self) -> None:
        """Take a snapshot of counters at the end of a scan cycle for the dashboard."""
        self._last_scan_snapshot = {
            "stage_counts": dict(self._stage_counts),
            "stage_passed": dict(self._stage_passed),
            "ema_cross_counts": dict(self._ema_cross_counts),
            "pipeline_accounting": dict(self._pipeline_accounting),
            "rejection_summary": dict(self._rejection_summary),
            "signal_count": self._signal_count,
            "scan_count": self._scan_count,
            "pipeline_integrity": self.pipeline_integrity.get_health_summary(),
        }
        self._last_scan_timestamp = time.time()

    def get_symbol_events(self, symbol: str, n: int = 20) -> List[Dict]:
        """Get the last N events for a tracked symbol."""
        return self._symbol_event_log.get(symbol, [])[-n:]

    def get_all_symbol_events(self) -> Dict[str, List[Dict]]:
        """Get event timelines for all tracked symbols."""
        return {sym: events[-10:] for sym, events in self._symbol_event_log.items()}

    def get_last_scan_snapshot(self) -> Dict:
        """Get the last scan snapshot."""
        return {
            "snapshot": self._last_scan_snapshot,
            "timestamp": self._last_scan_timestamp,
            "age_sec": round(time.time() - self._last_scan_timestamp, 1) if self._last_scan_timestamp else None,
        }

    def get_pipeline_accounting(self) -> Dict:
        """Get the combined accounting counters for integrity evaluation.

        In-flight counters are computed from live state counts, not accumulated.
        This ensures the accounting identities can balance even with in-flight candidates.
        """
        counters = dict(self._ema_cross_counts)
        counters.update(self._pipeline_accounting)
        # ── Compute in-flight counts from current state snapshot ──
        state_counts = self.state_manager.get_state_counts()
        counters["buy_mode_live"] = state_counts.get(BUY_MODE, 0)
        counters["sell_mode_live"] = state_counts.get(SELL_MODE, 0)
        counters["waiting_pullback_live"] = state_counts.get(WAITING_PULLBACK, 0)

        # ── Symbol-level mutual exclusivity check ──
        # Each symbol must belong to exactly one lifecycle state.
        # Since the state manager uses a dict keyed by symbol, each symbol
        # has exactly one entry with one `state` field. We verify this by
        # confirming that every symbol's state is in the known set and
        # that get_state_counts() totals match the number of symbols.
        _all_states = self.state_manager.get_all_states()
        _violations = 0
        _known_states = {NO_TREND, BUY_MODE, SELL_MODE, WAITING_PULLBACK,
                         WAITING_CONFIRMATION, ACTIVE_BUY, ACTIVE_SELL, TRADE_CLOSED}
        _non_no_trend = 0
        for _sym, _sdata in _all_states.items():
            if not isinstance(_sdata, dict):
                _violations += 1
                logger.warning("⚠️ STATE_VIOLATION sym={} corrupt entry (not dict)", _sym)
                continue
            _state = _sdata.get("state", NO_TREND)
            if _state not in _known_states:
                _violations += 1
                logger.warning("⚠️ STATE_VIOLATION sym={} unknown state={}", _sym, _state)
            if _state != NO_TREND:
                _non_no_trend += 1
        # Cross-check: sum of non-NO_TREND states from get_state_counts should match
        _counted_non_no_trend = sum(v for k, v in state_counts.items() if k != NO_TREND and k in _known_states)
        if _non_no_trend != _counted_non_no_trend:
            _violations += 1
            logger.warning("⚠️ STATE_VIOLATION count mismatch: iterated={} counted={}", _non_no_trend, _counted_non_no_trend)
        counters["state_violations"] = _violations

        return counters

    def evaluate_integrity(self) -> Optional[Dict]:
        """Evaluate pipeline integrity and return the report dict.

        Call this after each scan cycle to verify all accounting identities.
        Returns the integrity report dict, or None if no data yet.
        """
        counters = self.get_pipeline_accounting()
        report = self.pipeline_integrity.evaluate(counters)
        return report.to_dict()

    def get_pullback_rejection_audit(self) -> Dict:
        """Get pullback rejection audit summary.

        Returns:
            {
                "reason_counts": {reason: count},
                "total_rejections": int,
                "recent_details": [list of recent rejection details],
                "dominant_reason": str,
                "dominant_reason_pct": float,
            }
        """
        total = sum(self._pullback_reject_reasons.values())
        dominant_reason = ""
        dominant_pct = 0
        
        if total > 0:
            # Find dominant reason
            sorted_reasons = sorted(
                self._pullback_reject_reasons.items(),
                key=lambda x: x[1],
                reverse=True
            )
            if sorted_reasons:
                dominant_reason = sorted_reasons[0][0]
                dominant_pct = sorted_reasons[0][1] / total * 100

        return {
            "reason_counts": dict(self._pullback_reject_reasons),
            "total_rejections": total,
            "recent_details": self._pullback_reject_details[-20:],  # Last 20
            "dominant_reason": dominant_reason,
            "dominant_reason_pct": round(dominant_pct, 1),
        }

    def log_pullback_audit_report(self) -> None:
        """Log a detailed pullback rejection audit report."""
        audit = self.get_pullback_rejection_audit()
        total = audit["total_rejections"]
        
        if total == 0:
            logger.info("📊 PULLBACK_AUDIT: No rejections recorded yet")
            return
        
        logger.info("=" * 80)
        logger.info("📊 PULLBACK REJECTION AUDIT REPORT")
        logger.info("=" * 80)
        logger.info(f"Total pullback rejections: {total}")
        logger.info(f"Dominant reason: {audit['dominant_reason']} ({audit['dominant_reason_pct']:.1f}%)")
        
        logger.info("\nReason breakdown:")
        for reason, count in sorted(audit["reason_counts"].items(), key=lambda x: x[1], reverse=True):
            pct = count / total * 100
            logger.info(f"  {reason}: {count} ({pct:.1f}%)")
        
        # Analyze recent details
        details = audit["recent_details"]
        if details:
            logger.info(f"\nRecent rejections ({len(details)} samples):")
            
            # Check mode distribution
            adaptive_count = sum(1 for d in details if d.get("mode") == "adaptive")
            legacy_count = sum(1 for d in details if d.get("mode") == "legacy")
            logger.info(f"  Mode: adaptive={adaptive_count} legacy={legacy_count}")
            
            # Distance distribution
            ema20_dists_atr = [d["ema20_dist_atr"] for d in details if d.get("ema20_dist_atr")]
            ema50_dists_atr = [d["ema50_dist_atr"] for d in details if d.get("ema50_dist_atr")]
            ema20_dists_pct = [d["ema20_dist_pct"] for d in details if d.get("ema20_dist_pct")]
            ema50_dists_pct = [d["ema50_dist_pct"] for d in details if d.get("ema50_dist_pct")]
            
            if ema20_dists_atr:
                avg_20_atr = sum(ema20_dists_atr) / len(ema20_dists_atr)
                min_20_atr = min(ema20_dists_atr)
                max_20_atr = max(ema20_dists_atr)
                logger.info(f"  EMA20 distance (ATR): avg={avg_20_atr:.2f} min={min_20_atr:.2f} max={max_20_atr:.2f}")
            
            if ema50_dists_atr:
                avg_50_atr = sum(ema50_dists_atr) / len(ema50_dists_atr)
                min_50_atr = min(ema50_dists_atr)
                max_50_atr = max(ema50_dists_atr)
                logger.info(f"  EMA50 distance (ATR): avg={avg_50_atr:.2f} min={min_50_atr:.2f} max={max_50_atr:.2f}")
            
            if ema20_dists_pct:
                avg_20_pct = sum(ema20_dists_pct) / len(ema20_dists_pct)
                min_20_pct = min(ema20_dists_pct)
                max_20_pct = max(ema20_dists_pct)
                logger.info(f"  EMA20 distance (%): avg={avg_20_pct:.2f}% min={min_20_pct:.2f}% max={max_20_pct:.2f}%")
            
            if ema50_dists_pct:
                avg_50_pct = sum(ema50_dists_pct) / len(ema50_dists_pct)
                min_50_pct = min(ema50_dists_pct)
                max_50_pct = max(ema50_dists_pct)
                logger.info(f"  EMA50 distance (%): avg={avg_50_pct:.2f}% min={min_50_pct:.2f}% max={max_50_pct:.2f}%")
            
            # Check if any candidates were near EMA in adaptive mode
            near_ema20 = [d for d in details if d.get("near_ema20")]
            near_ema50 = [d for d in details if d.get("near_ema50")]
            if near_ema20 or near_ema50:
                logger.info(f"\nCandidates near EMA (adaptive mode):")
                logger.info(f"  Near EMA20: {len(near_ema20)}")
                logger.info(f"  Near EMA50: {len(near_ema50)}")
                for d in (near_ema20 + near_ema50)[:5]:
                    logger.info(f"    {d['symbol']}: {d['regime']} ema20_dist={d['ema20_dist_atr']:.2f}atr ema50_dist={d['ema50_dist_atr']:.2f}atr")
        
        logger.info("=" * 80)
        logger.info("DIAGNOSIS: If dominant reason is 'not_near_ema', adjust atr_threshold.")
        logger.info("=" * 80)

    def get_volume_rejection_audit(self) -> Dict:
        """Get volume rejection audit summary for candle-qualified candidates.

        Returns:
            {
                "total_rejections": int,
                "recent_details": [list of recent rejection details],
                "avg_volume_ratio": float,
                "avg_pullback_vol_ratio": float,
                "avg_confirm_vol_ratio": float,
                "dominant_issue": str,
            }
        """
        total = len(self._vol_reject_audit)
        if total == 0:
            return {
                "total_rejections": 0,
                "recent_details": [],
                "avg_volume_ratio": 0,
                "avg_pullback_vol_ratio": 0,
                "avg_confirm_vol_ratio": 0,
                "dominant_issue": "no_data",
            }
        
        details = self._vol_reject_audit
        ratios = [d["volume_ratio"] for d in details]
        pullback_ratios = [d["pullback_vol_ratio"] for d in details]
        confirm_ratios = [d["confirm_vol_ratio"] for d in details]
        
        # Determine dominant issue
        low_ratio_count = sum(1 for d in details if d["volume_ratio"] < 0.4)
        no_expansion_count = sum(1 for d in details if not d["volume_expanding"])
        both_count = sum(1 for d in details if d["volume_ratio"] < 0.4 and not d["volume_expanding"])
        
        if both_count > low_ratio_count and both_count > no_expansion_count:
            dominant = "both_low_ratio_and_no_expansion"
        elif low_ratio_count > no_expansion_count:
            dominant = "low_volume_ratio"
        else:
            dominant = "no_volume_expansion"
        
        return {
            "total_rejections": total,
            "recent_details": details[-20:],  # Last 20
            "avg_volume_ratio": round(sum(ratios) / len(ratios), 2),
            "avg_pullback_vol_ratio": round(sum(pullback_ratios) / len(pullback_ratios), 2),
            "avg_confirm_vol_ratio": round(sum(confirm_ratios) / len(confirm_ratios), 2),
            "dominant_issue": dominant,
            "low_ratio_count": low_ratio_count,
            "no_expansion_count": no_expansion_count,
            "both_count": both_count,
        }

    def get_state_transition_audit(self) -> Dict:
        """Get state-transition audit summary.

        Returns:
            {
                "total_transitions": int,
                "pullback_to_confirmation": int,
                "confirmation_to_volume_reject": int,
                "recent_transitions": [list of recent transitions],
                "stuck_symbols": [list of symbols stuck in WAITING_PULLBACK],
            }
        """
        total = len(self._state_transition_audit)
        if total == 0:
            return {
                "total_transitions": 0,
                "pullback_to_confirmation": 0,
                "confirmation_to_volume_reject": 0,
                "recent_transitions": [],
                "stuck_symbols": [],
            }
        
        transitions = self._state_transition_audit
        
        # Count specific transitions
        pb_to_conf = sum(1 for t in transitions 
                        if t.get("previous_state") == "WAITING_PULLBACK" 
                        and t.get("new_state") == "WAITING_CONFIRMATION")
        
        # Check for stuck symbols (WAITING_PULLBACK that never transition)
        stuck = []
        for t in transitions:
            if t.get("new_state") == "WAITING_PULLBACK":
                sym = t.get("symbol")
                # Check if this symbol ever transitions to WAITING_CONFIRMATION
                has_confirmed = any(
                    tx.get("symbol") == sym and tx.get("new_state") == "WAITING_CONFIRMATION"
                    for tx in transitions
                )
                if not has_confirmed and sym not in stuck:
                    stuck.append(sym)
        
        return {
            "total_transitions": total,
            "pullback_to_confirmation": pb_to_conf,
            "recent_transitions": transitions[-20:],  # Last 20
            "stuck_symbols": stuck[:10],  # Top 10 stuck
        }

    def log_volume_audit_report(self) -> None:
        """Log a detailed volume rejection audit report."""
        audit = self.get_volume_rejection_audit()
        total = audit["total_rejections"]
        
        if total == 0:
            logger.info("📊 VOLUME_AUDIT: No candle-qualified candidates rejected by volume yet")
            return
        
        logger.info("=" * 80)
        logger.info("📊 VOLUME REJECTION AUDIT REPORT")
        logger.info("=" * 80)
        logger.info(f"Total candle-qualified candidates rejected by volume: {total}")
        logger.info(f"Dominant issue: {audit['dominant_issue']}")
        logger.info(f"Average volume ratio: {audit['avg_volume_ratio']:.2f}x (threshold: 0.4x)")
        logger.info(f"Average pullback candle volume: {audit['avg_pullback_vol_ratio']:.2f}x SMA20")
        logger.info(f"Average confirmation candle volume: {audit['avg_confirm_vol_ratio']:.2f}x SMA20")
        
        logger.info(f"\nRejection breakdown:")
        logger.info(f"  Low volume ratio (< 0.4x): {audit['low_ratio_count']}")
        logger.info(f"  No volume expansion: {audit['no_expansion_count']}")
        logger.info(f"  Both issues: {audit['both_count']}")
        
        details = audit["recent_details"]
        if details:
            logger.info(f"\nRecent rejections ({len(details)} samples):")
            for d in details[:10]:
                logger.info(
                    f"  {d['symbol']}: {d['side']} ratio={d['volume_ratio']:.2f} "
                    f"pullback_vol={d['pullback_candle_vol']:.0f} confirm_vol={d['confirm_candle_vol']:.0f} "
                    f"expand={d['volume_expanding']}"
                )
        
        logger.info("=" * 80)
        logger.info("DIAGNOSIS: Check if volume ratio is genuinely too low or if threshold needs adjustment.")
        logger.info("=" * 80)

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

        # ── PIPELINE INTEGRITY: Evaluate accounting identities ──
        try:
            integrity_report = self.evaluate_integrity()
            if integrity_report and not integrity_report.get("all_passed", True):
                logger.error(
                    "❌ PIPELINE_INTEGRITY FAILED: {}/{} ({}%) — {}",
                    integrity_report.get("score", 0),
                    integrity_report.get("total", 0),
                    integrity_report.get("integrity_pct", 0),
                    [i["name"] for i in integrity_report.get("identities", []) if not i["passed"]],
                )
            else:
                logger.debug(
                    "✅ PIPELINE_INTEGRITY: {}/{} (100%)",
                    integrity_report.get("score", 0) if integrity_report else "?",
                    integrity_report.get("total", 0) if integrity_report else "?",
                )
        except Exception as _int_err:
            logger.debug("Pipeline integrity evaluation error: {}", _int_err)

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
        # ── EMA crossover event counters (market events, not strategy entries) ──
        stats["ema_cross_events"] = dict(self._ema_cross_counts)
        # ── Conversion ratios (cross → chain → BUY_MODE) ──
        _ct = self._ema_cross_counts.get("cross_total", 0)
        _cca = self._ema_cross_counts.get("cross_chain_aligned", 0)
        _cu = self._ema_cross_counts.get("cross_up", 0)
        _cd = self._ema_cross_counts.get("cross_down", 0)
        _bmc = self._ema_cross_counts.get("buy_mode_created", 0)
        _smc = self._ema_cross_counts.get("sell_mode_created", 0)
        stats["ema_cross_events"]["ratios"] = {
            "cross_to_chain_pct": round(_cca / max(_ct, 1) * 100, 1),
            "cross_up_to_buy_pct": round(_bmc / max(_cu, 1) * 100, 1),
            "cross_down_to_sell_pct": round(_smc / max(_cd, 1) * 100, 1),
            "chain_to_regime_pct": round((_bmc + _smc) / max(_cca, 1) * 100, 1),
        }
        # ── Pipeline accounting counters ──
        stats["pipeline_accounting"] = self.get_pipeline_accounting()
        # ── Pipeline integrity score ──
        stats["pipeline_integrity"] = self.pipeline_integrity.get_health_summary()
        # ── WAITING_PULLBACK reevaluation counters ──
        stats["pullback_reeval"] = dict(self._pullback_reeval)
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
            "pipeline_integrity": self.pipeline_integrity.get_health_summary(),
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
            "pipeline_accounting": self.get_pipeline_accounting(),
        }

    # Maximum age (seconds) for a signal to appear in bridge data.
    # Signals older than this are stale and should not be displayed.
    _SIGNAL_BRIDGE_TTL_SEC = 4 * 3600  # 4 hours — stale signals removed from bridge

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

    @staticmethod
    def _parse_htf_data(metadata_str):
        """Parse signal metadata into htf_data dict for dashboard display."""
        import json as _json
        try:
            meta = _json.loads(metadata_str) if metadata_str else {}
            if isinstance(meta, dict):
                return meta.get("htf_data", {})
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

        # ── DEDUP: keep only the MOST RECENT signal per symbol ──
        # _signal_history can accumulate multiple signals for the same symbol
        # over hours (within the 4h TTL). Dedup prevents duplicate rows in
        # the dashboard Live Candidate Table.
        _deduped: Dict = {}
        for _s in active_signals:
            _k = _s.get("symbol", "")
            _ts = _s.get("timestamp", 0) or 0
            if _k not in _deduped or _ts > (_deduped[_k].get("timestamp", 0) or 0):
                _deduped[_k] = _s
        active_signals = list(_deduped.values())

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
                        "htf_data": self._parse_htf_data(r[9]),
                    })
                _conn.close()
                if active_signals:
                    logger.info(
                        "📊 EMA_V5 BRIDGE: loaded {} signals from DB fallback",
                        len(active_signals),
                    )
            except Exception as e:
                logger.debug("EMA V5 bridge DB fallback error: {}", e)

        # ── ENRICH SIGNALS: inject live chain + HTF data into all active signals ──
        # Chain tracking runs every cycle (before ACTIVE GUARD) so _chain_tracker
        # always has current data.  The tracker stores BOTH BUY and SELL chains
        # per symbol so we can always find the chain matching the signal's side.
        for _i, _sig in enumerate(active_signals):
            _sym = _sig.get("symbol", "")
            _side = _sig.get("side", "")
            _ema = _sig.get("ema_data", {})
            _is_buy = _side in ("LONG", "BUY")
            _dir = "BUY" if _is_buy else "SELL"
            _sym_chains = self._chain_tracker.get(_sym, {})
            _ct = _sym_chains.get(_dir, {})
            _live_ema = self.cache.get_emas(_sym) or {}

            # 5m chain enrichment — use direction-matched chain data
            if _ct:
                _ema["ema_chain_pattern"] = "20>50>144>200" if _is_buy else "20<50<144<200"
                _ema["ema_cross_price"] = _ct.get("chain_price", 0)
                _ema["ema_cross_time"] = _ct.get("chain_time", 0)
                _ema["bars_since_chain"] = _ct.get("bars_since", 0)
                _ema["ema20_x_50"] = _ct.get("ema20_x_50", 0)
                _ema["ema50_x_144"] = _ct.get("ema50_x_144", 0)
                _ema["ema144_x_200"] = _ct.get("ema144_x_200", 0)
            else:
                # Fallback: no directional chain data — derive pattern from side
                if not _ema.get("ema_chain_pattern"):
                    _ema["ema_chain_pattern"] = "20>50>144>200" if _is_buy else "20<50<144<200"

            # Live EMA values (always refresh from cache)
            if _live_ema:
                _ema["ema20"] = _live_ema.get("ema20", _ema.get("ema20", 0))
                _ema["ema50"] = _live_ema.get("ema50", _ema.get("ema50", 0))
                _ema["ema144"] = _live_ema.get("ema144", _ema.get("ema144", 0))
                _ema["ema200"] = _live_ema.get("ema200", _ema.get("ema200", 0))
                _ema["ema_distance_atr"] = _live_ema.get("ema_distance_atr", _ema.get("ema_distance_atr", 0))
                _atr_now = _live_ema.get("atr_14", 0)
                _atr_prev = _live_ema.get("atr_prev", 0)
                _sig["atr_expanding"] = _atr_now > _atr_prev if _atr_prev > 0 else True
                _bv = _live_ema.get("buy_volume", 0)
                _sv = _live_ema.get("sell_volume", 0)
                _vsma = _live_ema.get("vol_sma20", 1) or 1
                _sig["volume_normalized"] = round(_bv / _vsma, 2) if _is_buy else -round(_sv / _vsma, 2)

            _sig["ema_data"] = _ema

            # HTF enrichment — direction-matched
            _htf_data = _sig.get("htf_data", {})
            _htf_dir = "buy" if _is_buy else "sell"
            _htf_sym = self._htf_tracker.get(_sym, {})
            _htf = _htf_sym.get(_htf_dir, {})
            if _htf and _htf.get("regime") in ("BUY_MODE", "SELL_MODE"):
                _htf_data["htf_chain_pattern"] = _htf.get("chain_pattern", "")
                _htf_data["htf_cross_price"] = _htf.get("chain_price", 0)
                _htf_data["htf_cross_time"] = _htf.get("chain_time", 0)
                _htf_data["htf_bars_since"] = _htf.get("bars_since", 0)
                _htf_data["htf_regime"] = _htf.get("regime", "")
                _sig["htf_data"] = _htf_data

        # Debug: log enrichment summary
        _enriched_count = sum(1 for s in active_signals if s.get("ema_data", {}).get("ema_cross_price"))
        _tracker_syms = len(self._chain_tracker)
        if active_signals:
            logger.debug(
                "📊 EMA_V5 ENRICHMENT: {}/{} signals enriched, tracker has {} symbols, chains={}",
                _enriched_count, len(active_signals), _tracker_syms,
                {s: {d: bool(c.get("chain_price")) for d, c in v.items()} for s, v in list(self._chain_tracker.items())[:3]},
            )

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
            "vol_reject_reasons": dict(self._vol_reject_reasons),
            "pullback_rejection_audit": self.get_pullback_rejection_audit(),
            "volume_rejection_audit": self.get_volume_rejection_audit(),
            "state_transition_audit": self.get_state_transition_audit(),
            "pipeline_monitor": self.pipeline_monitor.to_bridge(),
            "analytics": self._prod_analytics.get_all(),
            "signal_rejection_tracker": _tracker_data,
            "pipeline_integrity": self.pipeline_integrity.get_health_summary(),
            "pipeline_accounting": self.get_pipeline_accounting(),
            "symbol_events": self.get_all_symbol_events(),
            "last_scan": self.get_last_scan_snapshot(),
            "tracked_symbols": list(self._tracked_symbols),
            "filter_attribution": self.observer.get_filter_attribution(),
            "filter_attribution_text": self.observer.format_filter_attribution(),
        }
