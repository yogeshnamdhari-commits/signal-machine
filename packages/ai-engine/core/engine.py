"""
YOG'Z Core Engine — self-healing async orchestrator.
Production-grade: circuit breakers, parallel scan, bounded memory, graceful shutdown.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

import numpy as np
from loguru import logger

from config import config
from database.signal_repository import repo as db
from database import db as db_conn
from core.event_bus import bus
from scanner.signal_router import SignalRouter
from exchanges.binance_ws import BinanceWebSocket
from scanner.orderflow import OrderFlowAnalyzer
from core.institutional_engine import InstitutionalEngine
from scanner.institutional import InstitutionalDetector as InstitutionalDetectorScanner
from scanner.cumulative_delta import CumulativeDeltaEngine
from scanner.regime import MarketRegimeDetector
from scanner.ai_scorer import AIConfidenceScorer
from scanner.confidence_calibrator import ConfidenceCalibrator
from scanner.smart_money_upgrade import SmartMoneyUpgradeOrchestrator
from scanner.performance_tracker import PerformanceOrchestrator
from scanner.alpha_ranking import AlphaRankingEngine
from scanner.range_reversal_engine import RangeReversalEngine
from scanner.checklist_gate import ChecklistGate
from scanner.ranking import RankingEngine
from scanner.symbol_scanner import AutoSymbolScanner
from scanner.dom_analytics import DOMAnalytics
from scanner.funding_rate import FundingRateEngine
from scanner.open_interest import OpenInterestEngine
from scanner.exchange_flow import ExchangeFlowEngine
from scanner.liquidation import LiquidationEngine
from scanner.smart_money import SmartMoneyEngine
from scanner.sweep_detector import SweepDetector
from scanner.absorption_detector import AbsorptionDetector
from scanner.spoofing_iceberg import SpoofingIcebergDetector
from scanner.liquidity_map import LiquidityMappingEngine
from scanner.fvg_detector import FVGDetect
from scanner.regime_filter import MarketRegimeFilter
from scanner.liquidity_sweep_engine import LiquiditySweepEngine
from scanner.trade_blocker import TradeBlocker
from core.cvd_engine import CVDEngine
from core.institutional_scoring_engine import InstitutionalScoringEngine
from scanner.signal_grade import SignalGradeEngine
from execution.risk_engine import RiskEngine
from alerts.telegram import TelegramAlerts
from dashboard.data_bridge import writer as bridge_writer, EngineStatus
from core.data_integrity import integrity_guard
from core.state_persistence import StatePersistence
from scanner.intraday_enhancer import IntradaySignalEnhancer
from scanner.trade_lifecycle_engine import TradeLifecycleEngine
from scanner.session_quality_filter import SessionQualityFilter
from scanner.symbol_expectancy_tracker import SymbolExpectancyTracker
from scanner.forensic_analytics import ForensicAnalytics
from scanner.production_validator import ProductionValidator
from scanner.session_filter import session_filter as session_filter_v2, daily_budget
from scanner.ema_v5.score_calibration.outcome_tracker import OutcomeTracker
from core.regime_state import regime_state
from scanner.confidence_validation import ConfidenceValidator
from scanner.institutional_validation import InstitutionalValidator
from scanner.exit_forensics import ExitForensics
from scanner.production_targets import ProductionTargetEngine
from scanner.quiet_market_filter import QuietMarketFilter
from scanner.signal_filter import SignalFilter
from scanner.data_quality import DataQualityValidator
from scanner.directional_neutralizer import DirectionalNeutralizer
from scanner.directional_exposure_limiter import DirectionalExposureLimiter
from scanner.forward_test_db import forward_test_db
from scanner.pipeline_health_monitor import pipeline_monitor
from scanner.forward_analytics import forward_analytics
from scanner.deployment_gate import deployment_gate
from detectors.smart_money_engine import SmartMoneyProbabilityDetector
from detectors.institutional_detector import InstitutionalProbabilityDetector
from detectors.whale_detector import WhaleProbabilityDetector
from engines.trade_engine import TradeEngine
from engines.entry_exit_engine import EntryExitEngine
from engines.tp_sl_engine import TPSLEngine
from engines.data_freshness_engine import DataFreshnessEngine
from engines.backtest_stats_engine import BacktestStatsEngine
from engines.risk_metrics_engine import RiskMetricsEngine
from scanner.institutional_signal_engine import InstitutionalSignalEngine
from scanner.ema_v5.lifecycle_logger import lifecycle_log
from scanner.ema_v5.rr_audit import get_rr_audit
from scanner.ema_v5.signal_rejection_tracker import get_tracker as get_signal_tracker

# Max data per symbol to prevent memory leak
_MAX_TRADES_PER_SYMBOL = 5_000   # Reduced from 10K — each trade ~200 bytes → 1MB/symbol saved
_MAX_KLINES_PER_INTERVAL = 150   # Reduced from 300
_MAX_SIGNALS = 200
_MAX_CLOSED_TRADES = 500
_MAX_EQUITY_HISTORY = 1_000
# Parallel scan batch size to avoid overwhelming the event loop
_SCAN_BATCH_SIZE = 50
# Memory thresholds (MB)
_MEM_WARN_MB = 800   # Start aggressive trimming
_MEM_CRITICAL_MB = 1200  # Drop all non-essential data


def _price_round(value: float, reference_price: float = 0) -> float:
    """Round a price to an appropriate number of decimal places based on magnitude.
    
    Binance returns different precision per symbol:
      - BTC (~$60K) → 2 decimals ($60,123.45)
      - ETH (~$1.5K) → 2 decimals ($1,523.45)
      - SOL (~$60) → 2 decimals ($61.40)
      - DOGE (~$0.08) → 4 decimals ($0.0808)
      - 1000PEPE (~$0.0026) → 6 decimals ($0.002612)
      - PORTAL (~$0.019) → 5 decimals ($0.01892)
    
    This function preserves the full precision returned by Binance REST API
    instead of blindly rounding to 2 decimals which destroys data for low-price tokens.
    """
    if value <= 0:
        return value
    ref = reference_price if reference_price > 0 else value
    if ref >= 100:
        return round(value, 2)
    elif ref >= 1:
        return round(value, 4)
    elif ref >= 0.01:
        return round(value, 6)
    else:
        return round(value, 8)


# ═══════════════════════════════════════════════════════════════
# PHASE 2: VALIDATED DB WRAPPER — Blocks trades with missing TP/SL
# Replaces ALL direct db.open_position() calls to prevent
# take_profit=0 reaching the database (CRITICAL BUG B)
# ═══════════════════════════════════════════════════════════════
async def safe_db_open_position(db, **kwargs):
    """
    Single validated wrapper for all open_position DB calls.
    Logs warning if TP/SL values are missing — surfaces the bug.
    Uses explicit checks instead of assert (which is stripped with -O flag).
    """
    tp1 = kwargs.get("take_profit", 0)
    tp2 = kwargs.get("take_profit_2", 0)
    tp3 = kwargs.get("take_profit_3", 0)
    sl = kwargs.get("stop_loss", 0)
    symbol = kwargs.get("symbol", "?")
    side = kwargs.get("side", "?")

    # Explicit checks (NOT assert — stripped by python -O)
    if sl <= 0:
        logger.error("[DB_GUARD] sl={} for {} {} — no stop loss", sl, symbol, side)
    if tp1 <= 0:
        logger.error("[DB_GUARD] tp1={} for {} {} — signal incomplete", tp1, symbol, side)
    if tp2 <= 0:
        logger.warning("[DB_GUARD] tp2={} for {} {} — TP2 not calculated (defaulting)", tp2, symbol, side)
    if tp3 <= 0:
        logger.warning("[DB_GUARD] tp3={} for {} {} — TP3 not calculated (defaulting)", tp3, symbol, side)

    # HARD BLOCK: SL and TP1 must be positive
    if sl <= 0 or tp1 <= 0:
        logger.error("[DB_GUARD] BLOCKED {} {} — sl={} tp1={}", symbol, side, sl, tp1)
        return None

    return await db.open_position(**kwargs)


class DeltaTerminalEngine:
    def __init__(self) -> None:
        self.ws = BinanceWebSocket()
        # Phase 1: Core Data Engines
        self.orderflow = OrderFlowAnalyzer()
        self.cumulative_delta = CumulativeDeltaEngine()
        self.cvd_inst = CVDEngine()
        self.dom = DOMAnalytics()
        self.funding = FundingRateEngine()
        self.oi = OpenInterestEngine()
        self.exchange_flow = ExchangeFlowEngine()
        self.liquidation = LiquidationEngine()
        self.symbol_scanner = AutoSymbolScanner()
        # Phase 2: Detection & Analysis Engines
        self.institutional = InstitutionalEngine()  # scoring engine
        self.institutional_detector = InstitutionalDetectorScanner()  # pattern detection
        self.scoring_engine = InstitutionalScoringEngine()
        self.signal_grade = SignalGradeEngine()
        self.router = SignalRouter()
        self.smart_money = SmartMoneyEngine()
        self.sweep = SweepDetector()
        self.absorption = AbsorptionDetector()
        self.spoof_iceberg = SpoofingIcebergDetector()
        self.liquidity_map = LiquidityMappingEngine()
        self.fvg = FVGDetect()
        self.regime_filter = MarketRegimeFilter()
        self.liquidity_sweep = LiquiditySweepEngine()
        self.trade_blocker = TradeBlocker()
        # Existing
        self.regime = MarketRegimeDetector()
        self.scorer = AIConfidenceScorer()
        self.calibrator = ConfidenceCalibrator()
        self.sm_upgrade = SmartMoneyUpgradeOrchestrator()
        self.perf_tracker = PerformanceOrchestrator()
        self.alpha_ranking = AlphaRankingEngine()
        self.range_reversal = RangeReversalEngine()
        self.ranking = RankingEngine()
        self.risk = RiskEngine()
        self.telegram = TelegramAlerts()
        self.intraday_enhancer = IntradaySignalEnhancer()
        self.production_targets = ProductionTargetEngine()
        self.signal_filter = SignalFilter()
        self.checklist_gate = ChecklistGate()
        # FIX #1: Trade Lifecycle Engine — minimum hold + MAE/MFE tracking
        self.lifecycle = TradeLifecycleEngine()
        # FIX #3: Session Quality Filter — block Asia/off-hours
        self.session_filter = SessionQualityFilter()
        # FIX #5: Symbol Expectancy Tracker — auto blacklist/promote
        self.symbol_tracker = SymbolExpectancyTracker()
        # EMA_V5 Institutional Strategy (isolated plugin)
        from scanner.ema_v5 import EMAv5Scanner
        self.ema_v5 = EMAv5Scanner()
        # FIX #7: Forensic Analytics — post-trade analysis
        self.forensics = ForensicAnalytics()
        # EMA_V5 Calibration Outcome Tracker — tracks forward prices for rejected candidates
        self._calibration_outcome_tracker = OutcomeTracker()
        # 🆕 Cycle position limiter — max new positions per scan cycle
        self._cycle_positions_opened: int = 0
        self._cycle_id: int = 0
        # FIX #10: Production Validator
        self.validator = ProductionValidator()
        # FIX #4: Quiet Market Filter — block low-volatility environments
        self.quiet_filter = QuietMarketFilter()
        # Phase 5-7: Validation modules
        self.conf_validator = ConfidenceValidator()
        self.inst_validator = InstitutionalValidator()
        self.exit_forensics = ExitForensics()
        self.data_quality = DataQualityValidator()
        # Probability-based detectors (P(institutional), P(accumulation), P(whale))
        self.prob_inst = InstitutionalProbabilityDetector()
        self.prob_accum = SmartMoneyProbabilityDetector()
        self.prob_whale = WhaleProbabilityDetector()
        # Trade lifecycle tracking engines
        self.trade_engine = TradeEngine()
        self.entry_exit_engine = EntryExitEngine()
        self.tp_sl_engine = TPSLEngine()
        self.data_freshness = DataFreshnessEngine()
        self.backtest_stats = BacktestStatsEngine()
        self.risk_metrics = RiskMetricsEngine()
        # State persistence — survives restarts
        self.state = StatePersistence()
        # Directional Neutralizer — prevents excessive LONG/SHORT bias per cycle
        db_cfg = config.directional_bias
        self.directional_neutralizer = DirectionalNeutralizer(
            max_direction_ratio=db_cfg.max_direction_ratio,
            penalty_floor=db_cfg.penalty_floor,
            divergence_threshold=db_cfg.divergence_threshold,
            divergence_bonus_max=db_cfg.divergence_bonus_max,
            extreme_imbalance_ratio=db_cfg.extreme_imbalance_ratio,
            extreme_penalty=db_cfg.extreme_penalty,
            uniform_direction_bonus=db_cfg.uniform_direction_bonus,
        )
        # Directional Exposure Limiter — prevents stacking same-direction positions
        # Fix for June 16 bug: 4 SHORTs opened in 2h, all hit SL
        de_cfg = config.directional_exposure
        self.directional_exposure = DirectionalExposureLimiter(
            max_same_direction=de_cfg.max_same_direction,
            window_minutes=de_cfg.window_minutes,
            max_same_direction_pct=de_cfg.max_same_direction_pct,
            max_positions_per_window=de_cfg.max_positions_per_window,
        )
        # Phase 2-14: Institutional Signal Engine — replaces over-filtered pipeline
        self.inst_signal_engine = InstitutionalSignalEngine()

        self.active_symbols: Set[str] = set()
        self.symbol_data: Dict[str, Dict] = {}
        self.signals: List[Dict] = []
        self.is_running = False
        self._tasks: List[asyncio.Task] = []
        self._t0: float = 0
        self._equity_history: List[Dict] = []
        self._closed_trades: List[Dict] = []
        self._symbol_cooldowns: Dict[str, float] = {}
        self._last_bridge_sync: float = 0
        # Circuit breaker: track consecutive errors per loop
        self._circuit_breakers: Dict[str, int] = {}
        self._circuit_breaker_threshold = 10
        self._mark_prices: Dict[str, float] = {}  # mark price from premiumIndex for OI valuation
        self._premium_data: Dict[str, Dict] = {}  # full premium index data per symbol
        self._ticker_data: Dict[str, Dict] = {}   # full 24h ticker data per symbol
        # PHASE 2 FIX: Persistent data cache — survives engine restarts
        self._data_cache_path = Path(__file__).resolve().parent.parent / "data" / "market_cache.json"
        self._load_market_cache()
        self._directional_stats: Dict = {}  # per-cycle directional balance stats
        # ── Signal Funnel Analytics ──
        self._funnel: Dict = {
            "symbols_processed": 0,
            "scorer_rejected": 0,
            "phase1_rejected": 0,
            "regime_blocked": 0,
            "sweep_blocked": 0,
            "oi_blocked": 0,
            "cvd_blocked": 0,
            "signals_emitted": 0,
            "rejection_reasons": [],  # last 100 rejection reasons
            "top_scores": [],  # top 10 scores this cycle
            "cycle_start": 0.0,
            "cycle_end": 0.0,
        }

        # Register Event Bus Handlers — async lambda wrapper for coroutine functions
        async def _on_trade_event(d):
            await self.orderflow.process_trade(*d)
            await self.exchange_flow.process_trade(*d)
        bus.subscribe("trade_event", _on_trade_event)
        bus.subscribe("signal_generated", self.router.route_signal)

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        self._t0 = time.time()
        self.is_running = True
        self._circuit_breakers.clear()
        logger.info("🚀 Starting YOG'Z Engine …")

        await db.initialize()
        await db_conn.connect()
        # NOTE: _load_symbols() moved AFTER WS start (needs WS ticker data when REST is banned)

        # ── Load persisted state from last session ──
        self.state.bind(self, self.risk, self.signal_filter)
        loaded = self.state.load()

        # ── FIX: Seed _closed_trades from DB for rolling PF circuit breaker ──
        # Without this, the rolling PF check reads an empty list after restart
        # and never triggers halt even when PF is critically low.
        try:
            import sqlite3 as _sqlite3_seed
            import os as _os_seed
            _db_path_seed = _os_seed.path.join(_os_seed.path.dirname(_os_seed.path.dirname(__file__)),
                                                "data", "institutional_v1.db")
            _conn_seed = _sqlite3_seed.connect(_db_path_seed, timeout=10)
            _cur_seed = _conn_seed.cursor()
            _cur_seed.execute(
                "SELECT symbol, pnl, entry_price, quantity, side, regime "
                "FROM positions WHERE status='closed' ORDER BY closed_at DESC LIMIT 50"
            )
            for _row in _cur_seed.fetchall():
                self._closed_trades.append({
                    "symbol": _row[0], "pnl": _row[1],
                    "entry_price": _row[2], "quantity": _row[3],
                    "side": _row[4], "regime": _row[5],
                })
            _conn_seed.close()
            if self._closed_trades:
                logger.info("📋 Seeded {} closed trades from DB for rolling PF check",
                            len(self._closed_trades))
        except Exception as _seed_err:
            logger.debug("Could not seed _closed_trades from DB: {}", _seed_err)

        # If no state file existed (first run), recover balance from DB
        if not loaded:
            try:
                import sqlite3 as _sqlite3
                import os as _os
                _db_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)),
                                          "data", "institutional_v1.db")
                _conn = _sqlite3.connect(_db_path, timeout=10)
                _cur = _conn.cursor()
                _cur.execute("SELECT COALESCE(SUM(pnl), 0) FROM positions WHERE status='closed' UNION ALL SELECT COALESCE(SUM(pnl), 0) FROM positions_archive WHERE status='closed'")
                total_pnl = _cur.fetchone()[0]
                self.risk.balance = 10_000.0 + total_pnl
                self.risk.peak = max(self.risk.balance, 10_000.0)
                self.risk._equity_peak = self.risk.peak
                _conn.close()
                logger.info("💰 Balance recovered from DB: ${:,.2f} (10K + ${:,.2f} PnL)",
                            self.risk.balance, total_pnl)
            except Exception as e:
                logger.warning("Could not recover balance from DB: {}", e)
        self.state.start_autosave()
        # Wire signal filter → state persistence callback
        self.signal_filter._on_state_change = self.state.mark_dirty
        # Save initial state as baseline (even if no changes yet)
        self.state.mark_dirty()

        # ── Restore open positions into risk engine from DB ──
        await self.risk.load_positions_from_db()

        for m in (self.orderflow, self.institutional, self.institutional_detector,
                  self.cumulative_delta, self.regime,
                  self.dom, self.funding, self.oi, self.exchange_flow, self.liquidation,
                  self.symbol_scanner, self.smart_money, self.sweep, self.absorption,
                  self.spoof_iceberg, self.liquidity_map, self.cvd_inst,
                  self.prob_inst, self.prob_accum, self.prob_whale, self.fvg, self.liquidity_sweep):
            await m.initialize()
        # Phase 2-14: Institutional Signal Engine
        await self.inst_signal_engine.initialize()
        # Initialize trade lifecycle engines (async)
        await self.entry_exit_engine.initialize()

        # Initialize Alpha Ranking Engine
        await self.alpha_ranking.initialize()
        # Wire alpha ranking to symbol scanner and position sizing
        self.symbol_scanner.set_alpha_ranking(self.alpha_ranking)
        self.risk.position_sizing.set_alpha_ranking(self.alpha_ranking)
        logger.info("🏆 Alpha Ranking integrated — {} symbols ranked", len(self.alpha_ranking.get_all_profiles()))

        # Start WS FIRST — ticker_arr stream provides data for symbol loading
        await self.ws.start(self._on_data)
        # Wait briefly for WS ticker data to populate (needed when REST is banned)
        await asyncio.sleep(3)

        # Load symbols — uses REST (if available) or WS ticker cache (if REST banned)
        try:
            await self._load_symbols()
        except Exception as exc:
            logger.error("Symbol loading failed: {} — retrying after WS data populates", exc)
            # Wait for WS ticker cache to populate, then retry
            for _wait in range(15):
                await asyncio.sleep(2)
                if len(self.ws._ws_ticker_cache) > 0:
                    break
            try:
                await self._load_symbols()
            except Exception as exc2:
                logger.error("Symbol loading retry also failed: {} — starting with cached symbols", exc2)
                # Last resort: use any previously cached symbols
                if self.ws._ws_symbols_cache:
                    for s in self.ws._ws_symbols_cache:
                        self.active_symbols.add(s)
                elif self.ws._ws_ticker_cache:
                    for s in self.ws._ws_ticker_cache:
                        if s.endswith("USDT"):
                            self.active_symbols.add(s)
                if not self.active_symbols:
                    raise RuntimeError(f"Cannot load any symbols — WS and REST both failed: {exc2}")
                logger.warning("⚠️  Started with {} fallback symbols", len(self.active_symbols))
        await self._sync_symbols_to_db()

        # ── POST-LOAD VALIDATION: Ensure we have symbols before proceeding ──
        if not self.active_symbols:
            logger.error("🚨 CRITICAL: 0 active symbols after _load_symbols()! Engine cannot scan.")
            # Last resort: try loading from persisted state file
            try:
                _state_file = Path(__file__).resolve().parent.parent / "data" / "ema_v5_state.json"
                if _state_file.exists():
                    import json as _json_fallback
                    _states = _json_fallback.loads(_state_file.read_text())
                    for _sym in _states:
                        if _sym.endswith("USDT"):
                            self.active_symbols.add(_sym)
                    if self.active_symbols:
                        logger.warning("⚠️  Recovered {} symbols from persisted state file", len(self.active_symbols))
            except Exception as _fb_err:
                logger.debug("State file fallback failed: {}", _fb_err)

        logger.info("✅ Pre-scan: {} active symbols confirmed", len(self.active_symbols))

        # Pre-fetch initial klines so regime/orderflow engines have data immediately
        asyncio.create_task(self._prefetch_klines(), name="prefetch_klines")
        # Pre-fetch 24h tickers and premium index for full market data immediately
        asyncio.create_task(self._prefetch_market_data(), name="prefetch_market_data")

        # Wire calibration outcome tracker with price function
        self._calibration_outcome_tracker.set_price_function(self._price)

        self._tasks = [
            asyncio.create_task(self._loop("scan", self._scan_loop), name="scan"),
            asyncio.create_task(self._loop("rank", self._rank_loop), name="rank"),
            asyncio.create_task(self._loop("risk", self._risk_loop), name="risk"),
            asyncio.create_task(self._loop("cleanup", self._cleanup_loop), name="cleanup"),
            asyncio.create_task(self._oi_poll_loop(), name="oi_poll"),
            asyncio.create_task(self._kline_poll_loop(), name="kline_poll"),
            asyncio.create_task(self._rest_trade_poll_loop(), name="rest_trade_poll"),
            asyncio.create_task(self._loop("calibration", self._calibration_outcome_loop), name="calibration"),
        ]

        logger.info("✅ Engine running — {} symbols", len(self.active_symbols))
        await self.telegram.send_message(
            f"🚀 *YOG'Z started*\n{len(self.active_symbols)} symbols | "
            f"{'Testnet' if config.binance.testnet else 'Production'}"
        )

    async def stop(self) -> None:
        logger.info("🛑 Stopping engine …")
        self.is_running = False
        # ── Persist state before shutdown ──
        self.state.stop_autosave()
        # Guard: if engine crashed before tasks were created, skip task cancellation
        if self._tasks:
            # Cancel all tasks and wait with timeout
            for t in self._tasks:
                if not t.done():
                    t.cancel()
            # Wait for all tasks to finish (with timeout to avoid hanging)
            try:
                _active_tasks = [t for t in self._tasks if not t.done()]
                if _active_tasks:
                    done, pending = await asyncio.wait(_active_tasks, timeout=5.0)
                    for t in pending:
                        logger.warning("Task {} did not finish in time, forcing cancel", t.get_name())
                        t.cancel()
                        try:
                            await t
                        except (asyncio.CancelledError, Exception):
                            pass
                else:
                    logger.info("All tasks already completed")
            except Exception as exc:
                logger.warning("Error waiting for tasks: {}", exc)
        else:
            logger.info("⚠️  No tasks to cancel (engine crashed early)")
        try:
            await self.ws.stop()
        except Exception as exc:
            logger.warning("Error stopping WS: {}", exc)
        try:
            await db.disconnect()
        except Exception as exc:
            logger.warning("Error disconnecting DB: {}", exc)
        logger.info("✅ Engine stopped")

    # ── Self-healing loop wrapper with circuit breaker ───────────

    async def _loop(self, name: str, coro) -> None:
        backoff = 1
        while self.is_running:
            try:
                await coro()
                backoff = 1
                self._circuit_breakers[name] = 0  # reset on success
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._circuit_breakers[name] = self._circuit_breakers.get(name, 0) + 1
                errors = self._circuit_breakers[name]
                if errors >= self._circuit_breaker_threshold:
                    logger.error("Loop '{}' tripped circuit breaker ({} consecutive errors): {}",
                                 name, errors, exc)
                    await asyncio.sleep(30)  # long cooldown
                    self._circuit_breakers[name] = 0  # reset to try again
                else:
                    logger.error("Loop '{}' error ({}): {} — retry {}s", name, errors, exc, backoff)
                    await asyncio.sleep(min(backoff, 30))
                    backoff = min(backoff * 2, 30)

    # ── Symbol loading ───────────────────────────────────────────

    async def _load_symbols(self) -> None:
        logger.info("Loading symbols …")
        all_syms = await self.ws.get_futures_symbols()
        logger.info("BINANCE RETURNED {}", len(all_syms))

        # Get 24h tickers — tries REST first, falls back to WS cache
        tickers = await self.ws.get_24h_tickers()
        self._vol_map = {t["symbol"]: t.get("quoteVolume", 0) for t in tickers}
        vol_map = self._vol_map

        # If REST failed (0 tickers) and WS cache is populating, wait and retry
        if not tickers and len(self.ws._ws_ticker_cache) == 0:
            logger.info("⏳ Waiting for WS ticker data (REST banned)...")
            for _wait in range(10):  # Wait up to 10 seconds
                await asyncio.sleep(1)
                if len(self.ws._ws_ticker_cache) > 0:
                    break
            # Retry with WS cache
            tickers = await self.ws.get_24h_tickers()
            self._vol_map = {t["symbol"]: t.get("quoteVolume", 0) for t in tickers}
            vol_map = self._vol_map

        filtered = sorted(
            [{"symbol": s, "base": s.replace("USDT", ""), "vol": vol_map.get(s, 0)}
             for s in all_syms if s.endswith("USDT") and vol_map.get(s, 0) >= config.scanner.min_volume_24h],
            key=lambda x: x["vol"], reverse=True,
        )[: config.scanner.max_symbols] # Check this value in your config.py
        
        # ── SAFETY: If volume filter eliminated ALL symbols, fall back to top by name ──
        # This happens when REST is banned and WS ticker cache hasn't populated yet.
        # Without this, the engine starts with 0 active_symbols and the scan loop does nothing.
        if not filtered and all_syms:
            logger.warning(
                "⚠️  SYMBOL_LOAD: Volume filter returned 0 symbols (tickers={}). "
                "Falling back to top {} USDT symbols by name.",
                len(vol_map), config.scanner.max_symbols,
            )
            filtered = sorted(
                [{"symbol": s, "base": s.replace("USDT", ""), "vol": 0}
                 for s in all_syms if s.endswith("USDT")],
                key=lambda x: x["symbol"], reverse=True,
            )[: config.scanner.max_symbols]
        
        if config.scanner.max_symbols == 1:
            logger.warning("⚠️  Scanner is restricted to 1 symbol by config.scanner.max_symbols")
            
        logger.info(f"TOTAL SYMBOLS LOADED: {len(filtered)}")
        logger.info(f"FIRST 20 SYMBOLS: {[s['symbol'] for s in filtered[:20]]}")

        for item in filtered:
            await db.upsert_symbol(item["symbol"], item["base"], "USDT")
            self.active_symbols.add(item["symbol"])
        logger.info("ACTIVE SYMBOLS {}", len(self.active_symbols))
        logger.info("Loaded {} symbols", len(self.active_symbols))

    async def _sync_symbols_to_db(self) -> None:
        """Sync symbols to Database instance used by binance_ws._subscribe_all."""
        for sym in self.active_symbols:
            base = sym.replace("USDT", "")
            await db_conn.upsert_symbol(sym, base, "USDT")
        logger.info("Synced {} symbols to Database", len(self.active_symbols))

    # ── Market data handler (bounded memory) ────────────────────

    async def _on_data(self, event: str, data: Dict) -> None:
        sym = data.get("symbol")
        if not sym or sym not in self.active_symbols:
            return
        sd = self.symbol_data.setdefault(
            sym, {"trades": [], "orderbook": {"bids": [], "asks": []}, "klines": {}, "ts": 0}
        )
        sd["ts"] = time.time()
        try:
            # ── Record data freshness tick ──
            if event in ("trade", "depth", "kline"):
                self.data_freshness.record_tick("binance")
                # Wire data flow to Trade Blocker so it knows exchange data is alive
                self.trade_blocker.record_data_tick("binance")
            if event == "trade":
                self.data_freshness.record_data_update("exchange_flow", "Binance Futures Taker Trade Stream")
            if event == "funding":
                self.data_freshness.record_tick("binance")
                self.trade_blocker.record_data_tick("binance")
                self.data_freshness.record_data_update("funding", "Binance + Bybit + OKX (volume-weighted avg)")
            if event == "liquidation":
                self.data_freshness.record_data_update("liquidation", "Binance Futures Liquidation Stream")
                self.trade_blocker.record_data_tick("binance")

            if event == "trade":
                sd["trades"].append(data)
                if len(sd["trades"]) > _MAX_TRADES_PER_SYMBOL:
                    sd["trades"] = sd["trades"][-_MAX_TRADES_PER_SYMBOL // 2:]
                
                # Publish to Event Bus for parallel engine processing
                await bus.publish("trade_event", (sym, data))
                
                # Data Quality: validate trade
                self.data_quality.validate_trade(sym, data)
                
                # Feed Smart Money Engine — stealth accumulation/distribution detection
                await self.smart_money.process_trade(sym, data)
                # Feed probability-based detectors
                await self.prob_accum.process_trade(sym, data)
                await self.prob_whale.process_trade(sym, data)
                await self.prob_inst.process_trade(sym, data)
                
                # Update institutional CVD tracker (skip synthetic trades)
                if data.get("_source") != "ticker_arr":
                    self.cvd_inst.update(
                        sym, data.get("price", 0), data.get("quantity", 0), 
                        data.get("is_buyer_maker", False)
                    )
                
                # Feed liquidation engine — only very large trades (> $100k)
                trade_val = data.get("price", 0) * data.get("quantity", 0)
                if trade_val > 100_000:
                    await self.liquidation.process_trade(sym, data)
                
            elif event == "depth":
                sd["orderbook"] = {"bids": data.get("bids", []), "asks": data.get("asks", [])}
                # Data Quality: validate orderbook
                self.data_quality.validate_orderbook(sym, sd["orderbook"])
                # Phase 2 engines — both scoring + pattern detection
                await self.institutional.process_orderbook(sym, sd["orderbook"])
                await self.institutional_detector.process_orderbook(sym, sd["orderbook"])
                await self.dom.process_orderbook(sym, data.get("bids", []), data.get("asks", []))
                await self.spoof_iceberg.process_orderbook(sym, data.get("bids", []), data.get("asks", []))
                await self.liquidity_map.process_orderbook(sym, data.get("bids", []), data.get("asks", []))
                # Feed institutional probability detector
                await self.prob_inst.process_orderbook(sym, sd["orderbook"])
                # Absorption (needs recent trades + orderbook)
                if sd["trades"]:
                    await self.absorption.process_trades(sym, sd["trades"][-20:], sd["orderbook"])
            elif event == "kline":
                iv = data.get("interval", "5m")
                kl = sd["klines"].setdefault(iv, [])
                # ── DIAG-1: Duplicate kline detection ──
                _new_ot = data.get("open_time", 0)
                if kl and kl[-1].get("open_time") == _new_ot:
                    _dup = getattr(self, '_diag_dups', 0) + 1
                    self._diag_dups = _dup
                    if _dup <= 5 or _dup % 200 == 0:
                        logger.warning("🔍 DIAG[KLINE_DUP] sym={} ot={} total={}", sym, _new_ot, _dup)
                kl.append(data)
                # ── DIAG-2: Truncation detection + kline count ──
                _pre = len(kl)
                if _pre > _MAX_KLINES_PER_INTERVAL:
                    sd["klines"][iv] = kl[-_MAX_KLINES_PER_INTERVAL // 2:]
                    _trunc = getattr(self, '_diag_truncs', 0) + 1
                    self._diag_truncs = _trunc
                    logger.warning("🔍 DIAG[KLINE_TRUNC] sym={} pre={} post={} max={} total_truncs={}",
                                   sym, _pre, len(sd["klines"][iv]), _MAX_KLINES_PER_INTERVAL, _trunc)
                # ── DIAG-3: Periodic kline count log ──
                _cnt = len(sd["klines"].get(iv, []))
                _ck = f'_diag_ctr_{sym}'
                _cv = getattr(self, _ck, 0) + 1
                setattr(self, _ck, _cv)
                if _cv % 500 == 0:
                    logger.info("🔍 DIAG[KLINE_COUNT] sym={} interval={} count={}", sym, iv, _cnt)
                # Data Quality: validate kline
                self.data_quality.validate_kline(sym, data)
                # Phase 2 engines
                await self.regime.process_kline(sym, iv, data)
                await self.sweep.process_kline(sym, data)
                await self.liquidity_map.process_kline(sym, data)
                await self.fvg.process_kline(sym, data)

            elif event == "funding":
                # Data Quality: validate funding rate
                self.data_quality.validate_funding(sym, data.get("funding_rate", 0), data.get("timestamp", 0) / 1000)
                # Skip WS funding feed if we already have production premium data.
                # WS may be connected to testnet (different rates), while _premium_data
                # comes from production REST /fapi/v1/premiumIndex — always authoritative.
                if sym not in self._premium_data:
                    await self.funding.process_funding(
                        sym, data.get("funding_rate", 0), data.get("timestamp", 0) / 1000
                    )

            elif event == "open_interest":
                # ── OI via WebSocket (3-second push, bypasses banned REST) ──
                try:
                    oi_contracts = data.get("open_interest", 0)
                    if oi_contracts > 0:
                        # Use mark price (from premiumIndex) for OI valuation
                        price = self._mark_prices.get(sym, 0)
                        if price <= 0:
                            sd_trades = sd.get("trades", [])
                            if sd_trades:
                                price = sd_trades[-1].get("price", 0)
                        if price > 0:
                            self.data_quality.validate_oi(sym, oi_contracts, price)
                            await self.oi.process_oi(sym, oi_contracts, price, time.time())
                            # Track WS OI as active source
                            if not getattr(self, '_oi_ws_active', False):
                                self._oi_ws_active = True
                                logger.info("✅ OI WebSocket stream active — bypassing banned REST endpoint")
                except Exception as e:
                    logger.debug("OI WS handler error {}: {}", sym, e)

            elif event == "liquidation":
                # Feed liquidation engine with trade-like data
                liq_trade = {
                    "price": data.get("price", 0),
                    "quantity": data.get("quantity", 0),
                    "is_buyer_maker": data.get("side") == "SELL",
                }
                await self.liquidation.process_trade(sym, liq_trade, normal_volume=5000)

        except Exception as exc:
            logger.error("Handler error {}: {}", sym, exc)

    # ── Scanning loop (parallel batch scan) ─────────────────────

    async def _oi_poll_loop(self) -> None:
        """Poll open interest and funding rates concurrently every 60s."""
        await asyncio.sleep(10)  # Wait for engine to settle
        _POLL_CONCURRENCY = 10  # max concurrent REST requests
        _vol_refresh_counter = 0
        _VOL_REFRESH_INTERVAL = 3  # refresh 24h volume + market data every 3 cycles (~3 min)

        while self.is_running:
            try:
                # Refresh 24h volume periodically
                # FIX: Merge instead of replace — prevents momentary blank data
                # during refresh. Old data persists until new data arrives.
                _vol_refresh_counter += 1
                if _vol_refresh_counter >= _VOL_REFRESH_INTERVAL:
                    _vol_refresh_counter = 0
                    try:
                        tickers = await self.ws.get_24h_tickers()
                        if tickers:
                            _new_map = {t["symbol"]: t.get("quoteVolume", 0) for t in tickers}
                            self._vol_map.update(_new_map)
                            _new_ticker = {t["symbol"]: t for t in tickers}
                            self._ticker_data.update(_new_ticker)
                            logger.debug("Volume refresh: {} tickers updated", len(tickers))
                    except Exception as e:
                        logger.debug("Volume refresh error: {}", e)

                # Fetch real-time funding rates for ALL symbols in ONE call (zero-lag)
                premium_map = {}
                try:
                    premium_map = await self.ws.get_premium_index_all()
                except Exception as e:
                    logger.debug("Premium index fetch error: {}", e)

                # Feed real-time funding rates into engine + cache mark prices for OI valuation
                for sym in self.active_symbols:
                    if sym in premium_map:
                        pi = premium_map[sym]
                        try:
                            await self.funding.process_funding(
                                sym, pi["current_rate"], pi["timestamp"] / 1000
                            )
                        except Exception as e:
                            logger.debug("Funding process error {}: {}", sym, e)
                        # Cache mark price for accurate OI USD conversion
                        mp = pi.get("mark_price", 0)
                        if mp > 0:
                            self._mark_prices[sym] = mp
                        # Cache full premium data (mark, index, funding countdown)
                        self._premium_data[sym] = pi

                symbols = list(self.active_symbols)
                sem = asyncio.Semaphore(_POLL_CONCURRENCY)

                # ── OI DATA: Try REST first, then derive from trade flow proxy ──
                # Binance IP ban blocks: REST /fapi/v1/openInterest AND WS @openInterest stream
                # When both are unavailable, derive OI change from orderflow + CVD data
                _oi_rest_ok = False
                _oi_proxy_count = 0

                async def _poll_one(sym: str) -> bool:
                    async with sem:
                        try:
                            oi_data = await self.ws.get_open_interest(sym)
                            if oi_data and oi_data.get("open_interest", 0) > 0:
                                price = self._mark_prices.get(sym, 0)
                                if price <= 0:
                                    sd = self.symbol_data.get(sym, {})
                                    trades = sd.get("trades", [])
                                    if trades:
                                        price = trades[-1].get("price", 0)
                                if price > 0:
                                    self.data_quality.validate_oi(sym, oi_data["open_interest"], price)
                                    await self.oi.process_oi(
                                        sym, oi_data["open_interest"], price, time.time()
                                    )
                                    return True
                        except Exception as e:
                            pass
                        return False

                # Try REST for a small batch first to detect if ban is active
                _test_batch = symbols[:5]
                _test_results = await asyncio.gather(*[_poll_one(s) for s in _test_batch])
                _oi_rest_ok = any(_test_results)

                if _oi_rest_ok:
                    # REST is working — poll all symbols
                    _remaining = [s for s in symbols if s not in set(_test_batch)]
                    await asyncio.gather(*[_poll_one(s) for s in _remaining])
                    self.data_freshness.record_data_update("open_interest", "Binance REST /fapi/v1/openInterest (60s poll)")
                else:
                    # REST banned — derive OI from trade flow proxy
                    for sym in symbols:
                        try:
                            price = self._mark_prices.get(sym, 0)
                            if price <= 0:
                                sd = self.symbol_data.get(sym, {})
                                trades = sd.get("trades", [])
                                if trades:
                                    price = trades[-1].get("price", 0)
                            if price <= 0:
                                continue

                            # ── OI PROXY: Derive from orderflow + CVD ──
                            of = self.orderflow.get_analysis(sym)
                            cvd_data = self.cvd_inst.get_analysis(sym)
                            if not of:
                                continue

                            # Net delta = buy_volume - sell_volume (in contracts)
                            buy_vol = of.get("buy_volume", 0)
                            sell_vol = of.get("sell_volume", 0)
                            total_vol = buy_vol + sell_vol
                            net_delta = buy_vol - sell_vol  # positive = net buying

                            # Convert net delta to approximate OI change (contracts)
                            # Scale: $1M net delta ≈ 1000 contracts OI change
                            _scale = 1.0 / price if price > 0 else 0
                            oi_change_contracts = net_delta * _scale

                            # Current OI estimate: use cumulative delta as proxy
                            # Start from a reasonable base (volume * 0.1 as rough OI estimate)
                            if not hasattr(self, '_oi_proxy_state'):
                                self._oi_proxy_state = {}
                            proxy_st = self._oi_proxy_state.setdefault(sym, {
                                "oi": total_vol * 0.1 if total_vol > 0 else 1000,
                                "readings": 0,
                            })

                            # Update OI estimate
                            prev_oi = proxy_st["oi"]
                            proxy_st["oi"] = max(1, prev_oi + oi_change_contracts)
                            proxy_st["readings"] += 1

                            # Feed to OpenInterestEngine for regime/positioning analysis
                            await self.oi.process_oi(
                                sym, proxy_st["oi"], price, time.time()
                            )
                            _oi_proxy_count += 1
                        except Exception as e:
                            logger.debug("OI proxy error {}: {}", sym, e)

                    self.data_freshness.record_data_update(
                        "open_interest",
                        f"Trade flow proxy (REST banned, {_oi_proxy_count}/{len(symbols)} symbols)"
                    )

                # ── Record data freshness for other polling sources ──
                self.data_freshness.record_data_update("klines", "Binance OHLCV (1m/5m/15m/1h/4h)")
                self.data_freshness.record_data_update("trades", "Binance Futures WebSocket Trade Stream")

                # ── Exchange Flow REST fallback: fetch trades for symbols without WS flow ──
                # Testnet WS only delivers aggTrade for ~6 active symbols.
                # Use production REST /fapi/v1/trades for the rest.
                try:
                    _flow_needs = [s for s in symbols if self.exchange_flow.needs_flow_data(s, min_trades=20)]
                    if _flow_needs:
                        _batch_size = 10
                        for i in range(0, len(_flow_needs), _batch_size):
                            batch = _flow_needs[i:i+_batch_size]
                            _tasks = [self._fetch_flow_rest(sym) for sym in batch]
                            await asyncio.gather(*_tasks, return_exceptions=True)
                            await asyncio.sleep(0.5)  # Rate limit
                except Exception as e:
                    logger.debug("Exchange flow REST fallback error: {}", e)
            except Exception as e:
                logger.error("OI poll loop error: {}", e)
            await asyncio.sleep(60)

    async def _fetch_flow_rest(self, sym: str) -> None:
        """Fetch recent trades via REST API for exchange flow (testnet WS fallback)."""
        try:
            data = await self.ws._get("/fapi/v1/trades", {"symbol": sym, "limit": 500})
            if data:
                count = await self.exchange_flow.fetch_rest_trades(sym, data)
                if count > 0:
                    logger.debug("📊 Flow REST: {} processed {} trades", sym, count)
        except Exception as e:
            logger.debug("Flow REST error {}: {}", sym, e)

    async def _kline_poll_loop(self) -> None:
        """Poll 5m klines via REST every 60s to keep regime detection fresh."""
        await asyncio.sleep(10)  # Wait for prefetch to complete first
        _POLL_CONCURRENCY = 10
        sem = asyncio.Semaphore(_POLL_CONCURRENCY)

        while self.is_running:
            try:
                # Poll ALL active symbols (not just those with trades)
                sem = asyncio.Semaphore(_POLL_CONCURRENCY)

                async def _poll_kline(sym: str) -> None:
                    async with sem:
                        try:
                            klines = await self.ws.get_klines(sym, interval="5m", limit=50)
                            if not klines:
                                return
                            # ── FIX: Recreate symbol_data entry if cleanup deleted it ──
                            # Without this, once cleanup loops removes entries (stale > 300s),
                            # kline poll can never restore them → scan loop sees 0 symbols → stall.
                            sd = self.symbol_data.get(sym)
                            if not sd:
                                logger.info("🔧 KLINE_POLL: Recreating symbol_data for {} (was deleted by cleanup)", sym)
                                sd = self.symbol_data.setdefault(
                                    sym, {"trades": [], "orderbook": {"bids": [], "asks": []}, "klines": {}, "ts": 0}
                                )
                            # ── FIX: Refresh timestamp so cleanup loop doesn't delete active symbols ──
                            sd["ts"] = time.time()
                            # ── TRACE: Verify timestamp is refreshed ──
                            _ts_age = time.time() - sd.get("ts", 0)
                            logger.debug("TRACE[POLL] sym={} candles={} ts_age={:.0f}s (FIXED)", sym, len(klines), _ts_age)
                            kl_list = sd.setdefault("klines", {}).get("5m", [])
                            # Build open_time set for O(1) dedup
                            _existing_ot = {k.get("open_time", 0) for k in kl_list}
                            # Update last candle + append new ones (skip duplicates)
                            for kl in klines:
                                kline_event = {
                                    "symbol": sym, "interval": "5m",
                                    "open_time": kl.get("open_time", 0),
                                    "close_time": kl.get("close_time", 0),
                                    "open": kl.get("open", 0), "high": kl.get("high", 0),
                                    "low": kl.get("low", 0), "close": kl.get("close", 0),
                                    "volume": kl.get("volume", 0), "trades": kl.get("trades", 0),
                                    "is_closed": True,
                                }
                                _ot = kline_event["open_time"]
                                _is_new = False
                                # Replace last candle if same open_time (updated close)
                                if kl_list and kl_list[-1].get("open_time") == _ot:
                                    kl_list[-1] = kline_event
                                    _is_new = True
                                elif _ot not in _existing_ot:
                                    # New candle — append and track
                                    kl_list.append(kline_event)
                                    _existing_ot.add(_ot)
                                    _is_new = True
                                # else: duplicate — skip entirely
                                if _is_new:
                                    # Feed regime + sweep engines only for new/updated candles
                                    await self.regime.process_kline(sym, "5m", kline_event)
                                    await self.sweep.process_kline(sym, kline_event)
                                    await self.liquidity_map.process_kline(sym, kline_event)
                                    await self.fvg.process_kline(sym, kline_event)
                            await asyncio.sleep(0.1)  # Rate limit
                        except Exception as e:
                            logger.debug("Kline poll error {}: {}", sym, e)

                # Poll ALL active symbols for klines (regime needs data even without trades)
                top_syms = sorted(self.active_symbols, key=lambda s: self._vol_map.get(s, 0), reverse=True)[:250]
                await asyncio.gather(*[_poll_kline(s) for s in top_syms])
                # ── DIAG: Log kline counts for top 5 symbols after poll ──
                for _dsym in top_syms[:5]:
                    _dsd = self.symbol_data.get(_dsym, {})
                    _dkl = _dsd.get("klines", {}).get("5m", [])
                    if _dkl:
                        logger.info("🔍 DIAG[POLL] sym={} 5m_count={}", _dsym, len(_dkl))
                self.data_freshness.record_data_update("klines", "Binance OHLCV 5m REST poll (60s)")
            except Exception as e:
                logger.debug("Kline poll loop error: {}", e)
            await asyncio.sleep(60)

    async def _prefetch_klines(self) -> None:
        """Pre-fetch klines (1m + 5m + 15m + 1h + 4h) for all active symbols via REST.

        1m  → regime (short-term)
        5m  → regime, orderflow, sweep, liquidity_map
        15m → regime, intraday trend analysis
        1h  → regime, structural S/R levels
        4h  → regime (macro trend)
        """
        await asyncio.sleep(5)  # Wait for WS to connect
        symbols = list(self.active_symbols)
        logger.info("📥 Pre-fetching klines for {} symbols (1m+5m+15m+1h+4h)…", len(symbols))
        fetched = 0

        # Timeframe configs: (interval, limit, feed_engines)
        tf_configs = [
            ("1m", 60, True),    # Feed regime (short-term)
            ("5m", 250, True),   # Feed EMA_V5 (needs 220+ for EMA200 warmup), regime, sweep
            ("15m", 40, True),   # Feed regime + intraday trend analysis
            ("1h", 35, True),    # Feed regime + structural S/R levels
            ("4h", 30, True),    # Feed regime (macro trend)
        ]

        for sym in symbols:
            try:
                sd = self.symbol_data.setdefault(
                    sym, {"trades": [], "orderbook": {"bids": [], "asks": []}, "klines": {}, "ts": 0}
                )
                sd["ts"] = time.time()

                for interval, limit, feed_engines in tf_configs:
                    klines = await self.ws.get_klines(sym, interval=interval, limit=limit)
                    if not klines:
                        continue

                    kl_list = sd["klines"].setdefault(interval, [])
                    # Build open_time set for O(1) dedup (poll may have added candles first)
                    _existing_ot = {k.get("open_time", 0) for k in kl_list}
                    for kl in klines:
                        kline_event = {
                            "symbol": sym,
                            "interval": interval,
                            "open_time": kl.get("open_time", 0),
                            "close_time": kl.get("close_time", 0),
                            "open": kl.get("open", 0),
                            "high": kl.get("high", 0),
                            "low": kl.get("low", 0),
                            "close": kl.get("close", 0),
                            "volume": kl.get("volume", 0),
                            "trades": kl.get("trades", 0),
                            "is_closed": True,
                        }
                        _ot = kline_event["open_time"]
                        if _ot not in _existing_ot:
                            kl_list.append(kline_event)
                            _existing_ot.add(_ot)
                            if feed_engines:
                                await self.regime.process_kline(sym, interval, kline_event)
                                if interval in ("5m",):
                                    await self.sweep.process_kline(sym, kline_event)
                                    await self.liquidity_map.process_kline(sym, kline_event)
                                    await self.fvg.process_kline(sym, kline_event)

                # ── DIAG: Log initial kline counts after prefetch ──
                for _iv, _kl in sd.get("klines", {}).items():
                    logger.info("🔍 DIAG[PREFETCH] sym={} interval={} count={}", sym, _iv, len(_kl))
                # Generate a synthetic trade from last 5m close price
                klines_5m = sd["klines"].get("5m", [])
                if klines_5m:
                    last_close = klines_5m[-1].get("close", 0)
                    if last_close > 0:
                        sd["trades"].append({
                            "symbol": sym,
                            "price": last_close,
                            "quantity": 0.001,
                            "is_buyer_maker": False,
                            "trade_time": int(time.time() * 1000),
                        })
                fetched += 1
                await asyncio.sleep(0.1)  # Rate limit: ~10 req/s
            except Exception as e:
                logger.debug("Prefetch error {}: {}", sym, e)
        logger.info("✅ Pre-fetched klines for {}/{} symbols (1m+5m+15m+1h+4h)", fetched, len(symbols))

    async def _prefetch_market_data(self) -> None:
        """Pre-fetch 24h tickers and premium index for full market data (mark, index, high, low, vol_btc)."""
        await asyncio.sleep(3)  # Wait for WS to connect and populate caches
        try:
            tickers = await self.ws.get_24h_tickers()
            if tickers:
                self._vol_map.update({t["symbol"]: t.get("quoteVolume", 0) for t in tickers})
                self._ticker_data.update({t["symbol"]: t for t in tickers})
                logger.info("📥 Pre-fetched 24h tickers: {} symbols ({} total)", len(tickers), len(self._ticker_data))
        except Exception as e:
            logger.debug("Ticker prefetch error: {}", e)
        try:
            premium_map = await self.ws.get_premium_index_all()
            if premium_map:
                for sym, pi in premium_map.items():
                    mp = pi.get("mark_price", 0)
                    if mp > 0:
                        self._mark_prices[sym] = mp
                    self._premium_data[sym] = pi
                logger.info("📥 Pre-fetched premium index: {} symbols", len(premium_map))
            # Also use WS mark price cache directly (faster, no REST needed)
            ws_mp = getattr(self.ws, '_ws_mark_prices', {})
            if ws_mp:
                for sym, mp in ws_mp.items():
                    if mp > 0 and sym not in self._mark_prices:
                        self._mark_prices[sym] = mp
                logger.info("📥 WS mark price cache: {} symbols", len(ws_mp))
        except Exception as e:
            logger.debug("Premium prefetch error: {}", e)

    # ═══════════════════════════════════════════════════════════════
    # REST TRADE POLLER — Backup when WS trade stream is dead
    # Fetches recent trades via REST and feeds into the engine
    # ═══════════════════════════════════════════════════════════════
    async def _rest_trade_poll_loop(self) -> None:
        """Poll recent trades via REST API when WS trade stream is inactive.

        Detects when symbols have 0 or very few trades (WS dead) and
        backfills via REST /fapi/v1/trades so orderflow, CVD, smart money,
        and scoring all have data to work with.
        """
        await asyncio.sleep(15)  # Wait for initial WS connection attempt
        _POLL_CONCURRENCY = 10
        _POLL_INTERVAL = 15  # seconds between full cycles

        while self.is_running:
            try:
                # Only poll symbols where WS isn't delivering trade data
                _needs_trades = []
                _now = time.time()
                for sym in self.active_symbols:
                    sd = self.symbol_data.get(sym)
                    if not sd:
                        _needs_trades.append(sym)
                        continue
                    trades = sd.get("trades", [])
                    # No trades, or last trade older than 60s = WS is dead
                    if not trades:
                        _needs_trades.append(sym)
                    else:
                        last_ts = trades[-1].get("trade_time", 0)
                        if last_ts > 1e10:
                            last_ts = last_ts / 1000
                        if _now - last_ts > 60:
                            _needs_trades.append(sym)

                if not _needs_trades:
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue

                # Sort by volume to prioritize high-liquidity symbols
                _needs_trades.sort(key=lambda s: self._vol_map.get(s, 0), reverse=True)
                _batch = _needs_trades[:80]  # Max 80 per cycle to avoid rate limits

                logger.info("🔄 REST trade poll: {}/{} symbols need trades", len(_batch), len(self.active_symbols))

                sem = asyncio.Semaphore(_POLL_CONCURRENCY)
                _fed_count = 0

                async def _fetch_sym_trades(sym: str) -> int:
                    nonlocal _fed_count
                    async with sem:
                        try:
                            data = await self.ws._get("/fapi/v1/trades", {"symbol": sym, "limit": 500})
                            if not data or not isinstance(data, list):
                                return 0
                            sd = self.symbol_data.setdefault(
                                sym, {"trades": [], "orderbook": {"bids": [], "asks": []}, "klines": {}, "ts": 0}
                            )
                            sd["ts"] = _now
                            existing_count = len(sd.get("trades", []))
                            fed = 0
                            for t in data:
                                price = float(t.get("price", 0))
                                qty = float(t.get("qty", 0))
                                if price <= 0 or qty <= 0:
                                    continue
                                trade_event = {
                                    "symbol": sym,
                                    "price": price,
                                    "quantity": qty,
                                    "is_buyer_maker": t.get("isBuyerMaker", False),
                                    "trade_time": t.get("time", int(_now * 1000)),
                                    "_source": "rest_trades",
                                }
                                sd["trades"].append(trade_event)
                                # Feed event bus for orderflow, exchange flow, CVD, smart money
                                try:
                                    await bus.publish("trade_event", (sym, trade_event))
                                except Exception as _e:
                                    logger.debug("Event bus publish failed for {}: {}", sym, _e)
                                # Feed Smart Money Engine
                                try:
                                    await self.smart_money.process_trade(sym, trade_event)
                                    await self.prob_accum.process_trade(sym, trade_event)
                                    await self.prob_whale.process_trade(sym, trade_event)
                                    await self.prob_inst.process_trade(sym, trade_event)
                                except Exception as _e:
                                    logger.debug("ML engine trade feed failed for {}: {}", sym, _e)
                                # Feed CVD tracker
                                try:
                                    self.cvd_inst.update(sym, price, qty, trade_event["is_buyer_maker"])
                                except Exception as _e:
                                    logger.debug("CVD tracker update failed for {}: {}", sym, _e)
                                fed += 1

                            # Trim excess trades
                            max_t = _MAX_TRADES_PER_SYMBOL
                            if len(sd["trades"]) > max_t:
                                sd["trades"] = sd["trades"][-max_t // 2:]

                            if fed > 0 and existing_count == 0:
                                _fed_count += 1
                            return fed
                        except Exception as e:
                            logger.debug("REST trade fetch error {}: {}", sym, e)
                            return 0

                results = await asyncio.gather(*[_fetch_sym_trades(s) for s in _batch], return_exceptions=True)
                total_fed = sum(r for r in results if isinstance(r, int))

                if _fed_count > 0:
                    logger.info("✅ REST trade poll: fed trades to {} new symbols ({} total trades)", _fed_count, total_fed)
                    self.data_freshness.record_data_update("trades", f"REST /fapi/v1/trades poll ({_fed_count} symbols)")
                    # Sync bridge immediately so dashboard shows fresh data
                    try:
                        self._sync_bridge()
                    except Exception as _e:
                        logger.debug("Bridge sync after REST poll failed: {}", _e)

            except Exception as e:
                logger.error("REST trade poll loop error: {}", e)
            await asyncio.sleep(_POLL_INTERVAL)

    async def _scan_loop(self) -> None:
        symbols_with_data = [s for s in self.active_symbols if s in self.symbol_data]
        # ── Data availability diagnostics ──
        logger.debug("TRACE[SCAN_START] active={} with_data={} symbol_data_total={}",
                    len(self.active_symbols), len(symbols_with_data), len(self.symbol_data))
        # ── CRITICAL WARNING: If active_symbols is empty, log loudly ──
        if not self.active_symbols:
            logger.warning("🚨 SCAN_LOOP: 0 active_symbols — nothing to scan! Check _load_symbols().")
        elif not symbols_with_data:
            logger.warning("⚠️  SCAN_LOOP: {} active symbols but 0 have data in symbol_data", len(self.active_symbols))
        logger.debug("Scan loop: {} symbols with data", len(symbols_with_data))
        # ── Reset funnel counters for this cycle ──
        self._cycle_id += 1
        self._cycle_positions_opened = 0
        self._funnel = {
            "symbols_processed": 0,
            "scorer_rejected": 0,
            "phase1_rejected": 0,
            "regime_blocked": 0,
            "sweep_blocked": 0,
            "oi_blocked": 0,
            "cvd_blocked": 0,
            "filter": 0,
            "signals_emitted": 0,
            "generated": 0,
            "rejection_reasons": [],
            "top_scores": [],
            "cycle_start": time.time(),
            "cycle_end": 0.0,
            "missing_data_rejected": 0,  # TRACE: symbols rejected because market_data deleted
            # Pipeline monitor fields
            "session_blocked": 0,
            "checklist_blocked": 0,
            "checklist_passed": 0,
            "pipeline_traces": [],
            "session_diagnostics": {},
        }
        # Begin directional tracking for this cycle
        if config.directional_bias.enabled:
            self.directional_neutralizer.begin_cycle()

        # ═══════════════════════════════════════════════════════════════
        # PHASE 2 CRITICAL: REGIME HALT CHECK — Before any signal processing
        # If the system is halted (time + regime condition), skip entire cycle.
        # This prevents resuming into the same bad market after cooldown.
        # ═══════════════════════════════════════════════════════════════
        try:
            # Get composite regime from BTC (market leader) for halt evaluation
            _btc_regime = "unknown"
            _btc_rg = self.regime.get_regime("BTCUSDT") if hasattr(self.regime, 'get_regime') else None
            if _btc_rg:
                _btc_regime = _btc_rg.get("regime", "unknown")
            regime_state.update_regime(_btc_regime)

            is_halted, halt_reason = regime_state.is_halted(_btc_regime)
            if is_halted:
                logger.info("🛑 SYSTEM_HALTED: {} — skipping entire scan cycle", halt_reason)
                self._funnel["cycle_end"] = time.time()
                # CRITICAL: Must yield control to asyncio event loop.
                # Without this sleep, the tight loop blocks _risk_loop from
                # running bridge sync, causing the Live Sheet to go OFFLINE.
                await asyncio.sleep(config.scanner.scan_interval_sec)
                return
        except Exception as _halt_err:
            logger.debug("Regime halt check failed: {}", _halt_err)

        # ── ADAPTIVE THRESHOLDS: Compute market breadth once per cycle ──
        try:
            _regime_dist: Dict[str, int] = {}
            for sym in symbols_with_data:
                _rg = self.regime.get_regime(sym) if hasattr(self.regime, 'get_regime') else None
                _r = _rg.get("regime", "range") if _rg else "range"
                _regime_dist[_r] = _regime_dist.get(_r, 0) + 1
            self.perf_tracker.adaptive_threshold.update_market_breadth(
                _regime_dist, len(symbols_with_data)
            )
        except Exception as e:
            logger.debug("Adaptive threshold update error: {}", e)

        # ── BTC Control Index Update (once per cycle) ──
        try:
            btc_sm = self.smart_money.get_analysis("BTCUSDT")
            if btc_sm:
                self.sm_upgrade.update_btc(btc_sm)
                logger.debug("📊 BTC Control Index: score={:.1f} dir={} regime={}",
                             self.sm_upgrade._btc_index.btc_score if self.sm_upgrade._btc_index else 50,
                             self.sm_upgrade._btc_index.btc_direction if self.sm_upgrade._btc_index else "N/A",
                             self.sm_upgrade._btc_index.btc_regime if self.sm_upgrade._btc_index else "N/A")
        except Exception:
            pass

        # ── Alpha Ranking Update (every 5 minutes) ──
        try:
            self.alpha_ranking.update()
        except Exception as e:
            logger.debug("Alpha ranking update error: {}", e)

        # Process in batches to avoid overwhelming the event loop
        for i in range(0, len(symbols_with_data), _SCAN_BATCH_SIZE):
            batch = symbols_with_data[i:i + _SCAN_BATCH_SIZE]
            tasks = [self._scan_symbol(sym) for sym in batch]
            await asyncio.gather(*tasks, return_exceptions=True)
        # End directional tracking — log cycle stats
        if config.directional_bias.enabled:
            self._directional_stats = self.directional_neutralizer.end_cycle()
        # ── Finalize funnel data for this cycle ──
        self._funnel["cycle_end"] = time.time()
        self._funnel["cycle_duration_sec"] = round(self._funnel["cycle_end"] - self._funnel["cycle_start"], 2)
        # ── EMA_V5 SCAN CYCLE SUMMARY ──
        try:
            self.ema_v5._emit_scan_cycle_summary()
        except Exception as _summary_err:
            logger.debug("Scan cycle summary error: {}", _summary_err)
        # ── Log missing data rejections ──
        if self._funnel["missing_data_rejected"] > 0:
            logger.debug("TRACE[CYCLE] MISSING_DATA_REJECTED={} / {} processed",
                        self._funnel["missing_data_rejected"], self._funnel["symbols_processed"])
        # Sort rejection reasons by time (most recent first)
        self._funnel["rejection_reasons"] = self._funnel["rejection_reasons"][-100:]
        # Limit pipeline traces to top 50 by readiness
        self._funnel["pipeline_traces"] = self._funnel["pipeline_traces"][-50:]
        # ── SESSION DIAGNOSTICS: compute current session state for dashboard ──
        try:
            from datetime import datetime, timezone
            _now = datetime.now(timezone.utc)
            _hour = _now.hour
            if 13 <= _hour < 16:
                _current_session = "london_ny_overlap"
            elif 7 <= _hour < 16:
                _current_session = "london"
            elif 13 <= _hour < 22:
                _current_session = "new_york"
            elif 0 <= _hour < 8:
                _current_session = "asia"
            else:
                _current_session = "off_hours"
            _session_pfs = {"london": 1.40, "london_ny_overlap": 1.40, "new_york": 0.83, "asia": 0.54, "off_hours": 0.14}
            _session_wrs = {"london": 52, "london_ny_overlap": 55, "new_york": 41, "asia": 31, "off_hours": 18}
            _session_trades = {"london": 412, "london_ny_overlap": 187, "new_york": 389, "asia": 356, "off_hours": 280}
            _allowed = {"london", "london_ny_overlap", "new_york"}
            _session_status = "ALLOWED" if _current_session in _allowed else "BLOCKING"
            _expectancy = "Positive" if _session_pfs.get(_current_session, 0) >= 1.0 else "Negative"
            # Next allowed session
            _next_session = "london"
            _next_hour = 7
            if _hour < 7:
                _remaining_h = 7 - _hour
            elif _hour < 13:
                _remaining_h = 13 - _hour  # overlap starts at 13
                _next_session = "london_ny_overlap"
            else:
                _remaining_h = (24 - _hour) + 7  # next day london
            _remaining_min = _remaining_h * 60 - _now.minute
            _remaining_str = f"{_remaining_min // 60:02d}:{_remaining_min % 60:02d}:00"
            self._funnel["session_diagnostics"] = {
                "current_session": _current_session,
                "session_pf": _session_pfs.get(_current_session, 0),
                "session_wr": _session_wrs.get(_current_session, 0),
                "session_trades": _session_trades.get(_current_session, 0),
                "session_expectancy": _expectancy,
                "session_status": _session_status,
                "next_session": _next_session,
                "next_session_start": f"{_next_hour:02d}:00 UTC",
                "time_remaining": _remaining_str,
                "allowed_sessions": list(_allowed),
                "blocked_sessions": ["asia", "off_hours"],
            }
        except Exception as e:
            logger.debug("Session diagnostics error: {}", e)
        # Write funnel to bridge
        try:
            from dashboard.data_bridge import writer as _bw
            _bw.write_funnel(self._funnel)
        except Exception as e:
            logger.debug("Funnel bridge write error: {}", e)

        # ── RR AUDIT: Write rejection stats to bridge ──
        try:
            from scanner.ema_v5.rr_audit import get_rr_audit
            rr_audit = get_rr_audit()
            rr_stats = rr_audit.get_rejection_stats()
            rr_stats["timestamp"] = time.time()
            bridge_path = Path(__file__).resolve().parent.parent / "data" / "bridge" / "rr_audit.json"
            bridge_path.parent.mkdir(parents=True, exist_ok=True)
            import json as _json
            with open(bridge_path, "w") as f:
                _json.dump(rr_stats, f, indent=2)
        except Exception as e:
            logger.debug("RR audit bridge write error: {}", e)

        # ── ADAPTIVE THRESHOLDS: Record cycle stats + write death report ──
        try:
            self.perf_tracker.adaptive_threshold.record_cycle(
                scanned=self._funnel["symbols_processed"],
                emitted=self._funnel["signals_emitted"],
            )
            death_report = self.perf_tracker.adaptive_threshold.get_death_report()
            adaptive_state = self.perf_tracker.adaptive_threshold.get_state()
            _bw.write_death_report(death_report, adaptive_state)
        except Exception as e:
            logger.debug("Death report write error: {}", e)

        await self._expire_signals()
        # Sync to dashboard bridge
        self._sync_bridge()
        await asyncio.sleep(config.scanner.scan_interval_sec)

    async def _emit_institutional_signal(self, inst_sig) -> None:
        """
        Phase 2-14: Emit a signal from the new Institutional Signal Engine.
        Stores signal to database and logs the emission.
        """
        sym = inst_sig.symbol
        side = inst_sig.direction

        logger.info(
            "🏛️🟢 SIGNAL_EMITTED: {} {} | score={:.0f} entry={:.4f} SL={:.4f} TP1={:.4f} TP2={:.4f} rr={:.1f}",
            side, sym, inst_sig.signal_score, inst_sig.entry_price,
            inst_sig.stop_loss, inst_sig.target_1, inst_sig.target_2, inst_sig.risk_reward,
        )

        # Store to database
        try:
            sig_id = await db.save_signal({
                "symbol": sym,
                "type": side,
                "confidence": inst_sig.confidence / 100.0,
                "entry_price": inst_sig.entry_price,
                "stop_loss": inst_sig.stop_loss,
                "take_profit": inst_sig.target_1,
                "regime": inst_sig.regime,
                "factors": f"signal_score={inst_sig.signal_score:.0f},sweep={inst_sig.sweep_type},mss={inst_sig.mss_type},fvg={inst_sig.fvg_quality},cvd={inst_sig.cvd_status},delta={inst_sig.delta_status},oi={inst_sig.oi_status},funding={inst_sig.funding_status}",
                "status": "active",
                "risk_reward": inst_sig.risk_reward,
                "institutional_score": inst_sig.signal_score,
            })
            logger.info("💾 {} signal stored: id={}", sym, sig_id)
        except Exception as e:
            logger.error("🚨 FAILED to store signal for {}: {} — {}", sym, type(e).__name__, e)
            logger.exception("Signal persistence failure")

        # Record metrics
        self.inst_signal_engine.metrics.record_signal_generated()

        # Dashboard bridge update
        self._funnel["inst_signal_emitted"] = self._funnel.get("inst_signal_emitted", 0) + 1
        self._funnel["last_signal"] = {
            "symbol": sym, "side": side,
            "score": inst_sig.signal_score,
            "entry": inst_sig.entry_price,
            "sl": inst_sig.stop_loss,
            "tp": inst_sig.target_1,
            "rr": inst_sig.risk_reward,
            "time": time.time(),
        }

    async def _scan_symbol(self, sym: str) -> None:
        """Scan a single symbol — runs in parallel with others."""
        try:
            # ── PHASE 12: Trade Blocker Check ──
            if self.trade_blocker.is_blocked():
                return  # Skip all scanning when blocked

            self._funnel["symbols_processed"] += 1
            md = self.symbol_data.get(sym)
            if not md:
                self._funnel["missing_data_rejected"] += 1
                logger.debug("TRACE[SCAN] REJECTED sym={} — market_data MISSING", sym)
                return

            trades_count = len(md.get("trades", []))
            klines_5m = len(md.get("klines", {}).get("5m", []))
            klines_1m = len(md.get("klines", {}).get("1m", []))

            # 1. Gather component intelligence
            orderflow = self.orderflow.get_analysis(sym)
            inst_patterns = self.institutional.get_patterns(sym)
            # Extract pattern list from dict (institutional stores {imbalance, walls})
            if isinstance(inst_patterns, dict):
                inst_patterns = inst_patterns.get("walls", [])
            cumulative_delta = self.cumulative_delta.get_analysis(sym)
            cvd_data = self.cvd_inst.get_analysis(sym)  # Multi-TF CVD
            regime = self.regime.get_regime(sym)
            funding_data = self.funding.get_analysis(sym)

            # ── ORDERFLOW FALLBACK: build from trade buffer if analyzer has no snapshots ──
            # CRITICAL: Filter out synthetic !ticker@arr trades (inflated quantities)
            if not orderflow and trades_count >= 20:
                trades = md.get("trades", [])
                now_ts = time.time()
                recent = [t for t in trades[-1000:]
                           if t.get("_source") != "ticker_arr"
                           and now_ts - (t.get("trade_time", 0) / 1000 if t.get("trade_time", 0) > 1e10 else t.get("trade_time", 0)) < 300]
                if recent:
                    buy_v = sum(t["price"] * t["quantity"] for t in recent if not t["is_buyer_maker"])
                    sell_v = sum(t["price"] * t["quantity"] for t in recent if t["is_buyer_maker"])
                    total = buy_v + sell_v
                    delta = buy_v - sell_v
                    imb = delta / total if total else 0
                    lb = sum(1 for t in recent if not t["is_buyer_maker"] and t["price"] * t["quantity"] >= 10000)
                    ls = sum(1 for t in recent if t["is_buyer_maker"] and t["price"] * t["quantity"] >= 10000)
                    fr = buy_v / total if total > 0 else 0.5
                    flow_sig = "buy" if fr > 0.55 else ("sell" if fr < 0.45 else "neutral")
                    ratio_dev = abs(fr - 0.5) * 2
                    direction = 1 if fr > 0.5 else -1
                    flow_strength = max(0, min(100, 50 + direction * ratio_dev * 50))
                    orderflow = {
                        "symbol": sym, "buy_volume": buy_v, "sell_volume": sell_v,
                        "delta": delta, "cumulative_delta": delta,
                        "imbalance": imb, "flow_ratio": fr, "flow_signal": flow_sig,
                        "flow_strength_score": flow_strength, "signal_strength": flow_strength / 100.0,
                        "large_buy_trades": lb, "large_sell_trades": ls,
                        "avg_size": total / len(recent) if recent else 0,
                        "delta_trend": 0, "vwap": 0,
                        "absorption": "none", "absorption_events": 0,
                        "sweep": "none", "sweep_events": 0,
                        "total_trades": len(recent),
                    }
                    logger.debug("🔧 {} orderflow fallback from trade buffer ({} recent trades)", sym, len(recent))

            of_ok = bool(orderflow)
            rg_ok = bool(regime)
            logger.info("📊 {} trades={} 5m_klines={} of={} rg={}", sym, trades_count, klines_5m, of_ok, rg_ok)

            # Update signal filter with current volume data
            self.signal_filter.update_volume_map(self._vol_map)

            # ═══════════════════════════════════════════════════════════════
            # PHASE 2-14: INSTITUTIONAL SIGNAL ENGINE — New 5-component model
            # Bypasses the old over-filtered pipeline (AI scorer → confidence
            # floor → regime → session → checklist = 0 signals)
            # Uses: Sweep(35%) + MSS(25%) + FVG(15%) + CVD(15%) + OI/Funding(10%)
            # ═══════════════════════════════════════════════════════════════
            oi_data = self.oi.get_analysis(sym)
            sweep_analysis = self.sweep.get_analysis(sym) if hasattr(self.sweep, 'get_analysis') else None
            sweep_setup_data = None
            fvg_analysis = self.fvg.get_analysis(sym) if hasattr(self.fvg, 'get_analysis') else None

            # Get sweep setup from liquidity sweep engine
            try:
                sweep_setup_obj = self.liquidity_sweep.evaluate_setup(
                    symbol=sym, side="LONG",  # Will evaluate both sides
                    sweep_analysis=sweep_analysis,
                    regime_data=regime,
                    fvg_analysis=fvg_analysis,
                    orderflow=orderflow,
                    cvd_data=cvd_data,
                    market_data=md,
                )
                if sweep_setup_obj:
                    sweep_setup_data = {
                        "valid_setup": getattr(sweep_setup_obj, "valid_setup", False),
                        "sweep_detected": getattr(sweep_setup_obj, "sweep_detected", False),
                        "sweep_score": getattr(sweep_setup_obj, "sweep_score", 0),
                        "composite_score": getattr(sweep_setup_obj, "composite_score", 0),
                        "sweep_type": getattr(sweep_setup_obj, "sweep_type", ""),
                        "conditions_met": getattr(sweep_setup_obj, "conditions_met", 0),
                    }
            except Exception:
                sweep_setup_data = None

            # ═══════════════════════════════════════════════════════════════
            # EMA_V5 INSTITUTIONAL STRATEGY — Parallel signal source
            # Evaluates BEFORE institutional path. If EMA_V5 generates a
            # signal, it skips the rest of the pipeline (same as Path A).
            # ═══════════════════════════════════════════════════════════════
            try:
                ema_sig = await self.ema_v5.evaluate(
                    symbol=sym, market_data=md, regime_data=regime,
                    orderflow=orderflow, cvd_data=cvd_data,
                )
                if ema_sig:
                    # ── TRANSITION LOG: Every candidate that reaches this point ──
                    _ema_conf = ema_sig.get("confidence", 0)
                    _ema_side = ema_sig.get("side", "?")
                    _ema_regime = ema_sig.get("regime", "")
                    logger.info("TRANSITION sym={} entered_signal conf={:.1f} side={} regime={}",
                                sym, _ema_conf, _ema_side, _ema_regime)

                    # ── SIGNAL REJECTION TRACKER: Begin trace ──
                    _sig_tracker = get_signal_tracker()
                    _sig_tracker.record_generated()  # Level 1: signal generated
                    _sig_trace = _sig_tracker.start_trace(
                        symbol=sym, side=_ema_side, confidence=_ema_conf,
                        regime=_ema_regime, signal_id=f"ema_v5_{sym}_{int(time.time())}",
                        entry_price=ema_sig.get("entry", 0),
                        stop_loss=ema_sig.get("sl", 0),
                        take_profit=ema_sig.get("take_profit_1", 0),
                        risk_reward=ema_sig.get("rr_1", 0),
                    )
                    _sig_trace.add_gate("scanner", passed=True, reason="EMA V5 signal generated")

                    # ── SHADOW TRACKER: Record every candidate for threshold calibration ──
                    try:
                        from scanner.ema_v5.shadow_confidence_tracker import get_shadow_tracker
                        _shadow = get_shadow_tracker()
                        _shadow.record_candidate(
                            symbol=sym,
                            side=ema_sig.get("side", "LONG"),
                            confidence=_ema_conf,
                            regime=_ema_regime,
                            entry_price=ema_sig.get("entry", 0),
                            stop_loss=ema_sig.get("sl", 0),
                            take_profit=ema_sig.get("take_profit_1", 0),
                            session="",
                            components=ema_sig.get("components", {}),
                        )
                    except Exception as _shadow_err:
                        logger.debug("Shadow tracker error: {}", _shadow_err)

                    # Route through same session filter as institutional path
                    from scanner.session_quality_filter import SessionQualityFilter
                    _ema_sess = SessionQualityFilter()
                    _ema_regime = ema_sig.get("regime", "")
                    _ema_ok, _ema_reason, _ema_data = _ema_sess.evaluate(
                        confidence_100=ema_sig.get("confidence", 0),
                        side=ema_sig.get("side", "LONG"),
                        regime=_ema_regime,
                    )
                    # ── TRANSITION LOG: Session filter result ──
                    _ema_session_result = "PASS" if _ema_ok else f"REJECT:{_ema_reason}"
                    logger.info("TRANSITION sym={} session={} confidence={:.1f}",
                                sym, _ema_session_result, _ema_conf)

                    # ── SIGNAL REJECTION TRACKER: Session filter gate ──
                    _sig_trace.add_gate("session_filter", passed=_ema_ok, reason=_ema_reason,
                                        session=_ema_data.get("session", "unknown"))
                    # ── LIFECYCLE: Log session filter result ──
                    _state_before = self.ema_v5.state_manager.get_state(sym)
                    if _ema_ok:
                        _sig_tracker.record_published()  # Level 1: passed session filter
                        logger.info("TRANSITION sym={} session=PASS conf={:.1f} → routing to execution", sym, _ema_conf)
                        # ── SIGNAL REJECTION TRACKER: Session passed, continue tracing ──
                        logger.info("📊 EMA_V5: {} {} passed session filter — routing to execution bridge", ema_sig.get("side", "?"), sym)
                        # ── FIX: Set ACTIVE state here, AFTER engine validation ──
                        _ema_new_state = "ACTIVE_BUY" if ema_sig.get("side") == "LONG" else "ACTIVE_SELL"
                        self.ema_v5.state_manager.set_state(sym, _ema_new_state)
                        # ── LIFECYCLE: Log ACTIVE state set ──
                        lifecycle_log.transition(sym, _state_before, _ema_new_state, "session_pass_executing",
                                                confidence=_ema_conf, side=_ema_side)
                        # ── AUDIT: WC exit — engine accepted → ACTIVE ──
                        self.ema_v5.waiting_audit.on_exit(sym, _ema_new_state)
                        # ── TRANSITION LOG: Final result ──
                        logger.info("TRANSITION sym={} FINAL=SIGNAL_CREATED state={} conf={:.1f} side={} regime={}",
                                    sym, _ema_new_state, _ema_conf, _ema_side, _ema_regime)
                        logger.info("📊 EMA_V5: {} {} state → {} (engine accepted)", ema_sig.get("side", "?"), sym, _ema_new_state)
                        # Emit through institutional signal path
                        self._funnel["inst_signal_emitted"] = self._funnel.get("inst_signal_emitted", 0) + 1
                        _ema_trace = {
                            "symbol": sym, "side": ema_sig.get("side", "?"),
                            "confidence": ema_sig.get("confidence", 0),
                            "institutional_score": 0,
                            "regime": _ema_regime,
                            "tf_override": False,
                            "session": {"passed": True, "session": _ema_data.get("session", "unknown")},
                            "checklist": {"passed": True, "score": "ema_v5", "note": "EMA_V5 signal"},
                            "generated": True, "emitted": True,
                            "failed_gate": None,
                            "timestamp": time.time(),
                        }
                        self._funnel["pipeline_traces"].append(_ema_trace)
                        # Store signal for execution
                        sig = ema_sig
                        sig["strategy_version"] = "ema_v5"
                        sig["status"] = "active"
                        sig["id"] = await db.save_signal({
                            "symbol": sym,
                            "type": ema_sig.get("side", "LONG"),
                            "confidence": ema_sig.get("confidence", 0) / 100.0,
                            "entry_price": ema_sig.get("entry", 0),
                            "stop_loss": ema_sig.get("sl", 0),
                            "take_profit": ema_sig.get("take_profit_1", 0),
                            "regime": _ema_regime,
                            "factors": json.dumps({
                                "strategy_version": "ema_v5",
                                "components": ema_sig.get("components", {}),
                                "ema_data": ema_sig.get("ema_data", {}),
                            }),
                            "status": "active",
                            "risk_reward": ema_sig.get("rr_1", 0),
                            "institutional_score": ema_sig.get("institutional_score", 0),
                            "mss_score": ema_sig.get("mss_score", 0),
                            "fvg_score": ema_sig.get("fvg_score", 0),
                            "volatility_score": ema_sig.get("volatility_score", 0),
                            "entry_reason": ema_sig.get("entry_reason", "ema_v5"),
                        })
                        logger.info("📊 EMA_V5: {} {} emitted via institutional path", ema_sig.get("side", "?"), sym)
                        # Add to self.signals so bridge/dashboard can display it
                        _ema_bridge_sig = dict(sig)
                        _ema_bridge_sig["strategy_version"] = "ema_v5"
                        _ema_bridge_sig["id"] = f"ema_v5_{sym}_{int(time.time())}"
                        _ema_bridge_sig["created_at"] = time.time()
                        _ema_bridge_sig["status"] = "active"
                        _ema_bridge_sig["symbol"] = sym
                        _ema_bridge_sig["side"] = ema_sig.get("side", "LONG")
                        _ema_bridge_sig["confidence"] = ema_sig.get("confidence", 0)
                        _ema_bridge_sig["entry_price"] = ema_sig.get("entry", 0)
                        _ema_bridge_sig["stop_loss"] = ema_sig.get("sl", 0)
                        _ema_bridge_sig["take_profit_1"] = ema_sig.get("take_profit_1", 0)
                        self.signals.append(_ema_bridge_sig)
                        # ═══════════════════════════════════════════════════════════════
                        # EMA_V5 EXECUTION BRIDGE — Connect signal to execution layer
                        # Previously, EMA_V5 signals were stored in DB/bridge but never
                        # routed to safe_db_open_position(). This bridge connects the
                        # signal to the same execution gates (risk, sizing, cooldown)
                        # used by production_v2, with strategy_version="ema_v5".
                        # ═══════════════════════════════════════════════════════════════
                        # Normalize fields for execution path
                        sig["entry_price"] = ema_sig.get("entry", 0)
                        sig["stop_loss"] = ema_sig.get("sl", 0)
                        sig["take_profit"] = ema_sig.get("take_profit_1", 0)
                        sig["institutional_score"] = 0
                        sig["risk_reward"] = ema_sig.get("rr_1", 0)
                        _ema_confidence_100 = ema_sig.get("confidence", 0)

                        # Cycle limit: max new positions per scan cycle
                        _cycle_max = getattr(config.risk, 'max_positions_per_cycle', 5)
                        if self._cycle_positions_opened >= _cycle_max:
                            _sig_trace.add_gate("cycle_limit", passed=False,
                                                reason=f"max {_cycle_max} positions this cycle",
                                                opened=self._cycle_positions_opened)
                            _sig_tracker.finish_trace(_sig_trace)
                            logger.info("⏱️ EMA_V5 CYCLE_LIMIT: {} {} — max {} positions this cycle",
                                        sym, ema_sig.get("side", "?"), _cycle_max)
                            return
                        _sig_trace.add_gate("cycle_limit", passed=True,
                                            opened=self._cycle_positions_opened, max=_cycle_max)

                        # Cooldown: skip if symbol was recently closed
                        _now = time.time()
                        _cooldown_until = self._symbol_cooldowns.get(sym, 0)
                        if _now < _cooldown_until:
                            _remaining = int(_cooldown_until - _now)
                            _sig_trace.add_gate("cooldown", passed=False,
                                                reason=f"{_remaining}s remaining")
                            _sig_tracker.finish_trace(_sig_trace)
                            logger.debug("⏱️ EMA_V5 COOLDOWN: {} skipped ({}s remaining)", sym, _remaining)
                            return
                        _sig_trace.add_gate("cooldown", passed=True)

                        # Skip if symbol already has an open position (prevent duplicates)
                        if sym in self.risk._positions:
                            _sig_trace.add_gate("duplicate_position", passed=False,
                                                reason="symbol already has open position")
                            _sig_tracker.finish_trace(_sig_trace)
                            logger.debug("🔒 EMA_V5 {} already has open position, skipping", sym)
                            return
                        _sig_trace.add_gate("duplicate_position", passed=True)

                        # Risk check — same gate as production_v2
                        _ema_risk_check = await self.risk.check_signal(sig)
                        if not _ema_risk_check.get("allowed"):
                            _risk_reason = _ema_risk_check.get("reason", "unknown")
                            _sig_trace.add_gate("risk_check", passed=False, reason=_risk_reason)
                            _sig_tracker.finish_trace(_sig_trace)
                            logger.info("🚫 EMA_V5 RISK_BLOCKED: {} {} — {}",
                                        ema_sig.get("side", "?"), sym, _risk_reason)
                            return
                        _sig_trace.add_gate("risk_check", passed=True,
                                            quantity=_ema_risk_check.get("quantity", 0))

                        _ema_qty = _ema_risk_check["quantity"]
                        if _ema_qty <= 0:
                            _sig_trace.add_gate("ghost_filter", passed=False, reason="qty=0")
                            _sig_tracker.finish_trace(_sig_trace)
                            logger.info("🚫 EMA_V5 GHOST_BLOCKED: {} {} — qty=0",
                                        ema_sig.get("side", "?"), sym)
                            return
                        _sig_trace.add_gate("ghost_filter", passed=True, quantity=_ema_qty)

                        _ema_entry = sig["entry_price"]
                        _ema_sl = sig.get("stop_loss", 0)
                        _ema_tp = sig.get("take_profit", 0)
                        _ema_side = sig.get("side", "LONG")
                        _ema_leverage = max(1, min(int(config.risk.max_leverage), 10))
                        _ema_fees = _ema_entry * _ema_qty * 0.0004

                        _ema_pos_id = await safe_db_open_position(
                            db,
                            signal_id=sig["id"],
                            symbol=sym,
                            side=_ema_side,
                            entry_price=_ema_entry,
                            quantity=_ema_qty,
                            leverage=_ema_leverage,
                            stop_loss=_ema_sl,
                            take_profit=_ema_tp,
                            fees=_ema_fees,
                            confidence=_ema_confidence_100 / 100.0,
                            regime=_ema_regime,
                            institutional_score=0,
                            risk_reward=sig.get("risk_reward", 0),
                            session=_ema_data.get("session", "unknown"),
                            strategy_version="ema_v5",
                            planned_rr=sig.get("risk_reward", 0),
                            volatility_score=50,
                            quiet_market_blocked=0,
                            mss_score=0,
                            fvg_score=0,
                        )

                        # ── SIGNAL REJECTION TRACKER: Position opened ──
                        _sig_trace.mark_opened(position_id=str(_ema_pos_id))
                        _sig_tracker.finish_trace(_sig_trace)

                        # Post-execution: cooldown + cycle counter
                        self._symbol_cooldowns[sym] = time.time() + 3600  # 1hr cooldown
                        self._cycle_positions_opened += 1

                        # Register in risk engine for exit monitoring
                        self.risk._positions[sym] = {
                            "id": _ema_pos_id,
                            "signal_id": sig["id"],
                            "symbol": sym,
                            "side": _ema_side,
                            "entry_price": _ema_entry,
                            "quantity": _ema_qty,
                            "leverage": _ema_leverage,
                            "stop_loss": _ema_sl,
                            "take_profit": _ema_tp,
                            "take_profit_1": _ema_tp,
                            "take_profit_2": 0,
                            "take_profit_3": 0,
                            "current_tp_index": 1,
                            "_tp1_hit": False,
                            "_tp2_hit": False,
                            "tp1_exit_pct": 0.40,
                            "tp2_exit_pct": 0.40,
                            "tp3_exit_pct": 0.30,
                            "opened_at": time.time(),
                            "confidence": _ema_confidence_100 / 100.0,
                            "institutional_score": 0,
                            "regime": _ema_regime,
                            "risk_reward": sig.get("risk_reward", 0),
                            "strategy_version": "ema_v5",
                        }

                        # Register in trade lifecycle engines
                        self.trade_engine.open_position(
                            symbol=sym, side=_ema_side, entry_price=_ema_entry,
                            signal_price=_ema_entry, quantity=_ema_qty,
                            leverage=_ema_leverage, stop_loss=_ema_sl, take_profit=_ema_tp,
                            signal_time=time.time(),
                        )
                        self.tp_sl_engine.register_position(
                            symbol=sym, side=_ema_side, entry_price=_ema_entry,
                            stop_loss=_ema_sl, take_profit=_ema_tp, quantity=_ema_qty,
                        )
                        self.entry_exit_engine.record_signal(sym, _ema_entry, _ema_side)
                        self.entry_exit_engine.record_fill(sym, _ema_entry, is_entry=True)
                        self.lifecycle.register_position(
                            symbol=sym, side=_ema_side, entry_price=_ema_entry,
                            stop_loss=_ema_sl, take_profit=_ema_tp,
                            risk_reward=sig.get("risk_reward", 3.0),
                        )

                        # Record in directional exposure limiter
                        if config.directional_exposure.enabled:
                            self.directional_exposure.record_open(sym, _ema_side)

                        # Telegram entry alert
                        await self.telegram.send_position_opened({
                            "symbol": sym,
                            "side": _ema_side,
                            "entry_price": _ema_entry,
                            "quantity": _ema_qty,
                            "leverage": _ema_leverage,
                            "stop_loss": _ema_sl,
                            "take_profit": _ema_tp,
                            "score": 0,
                            "opened_at": time.time(),
                        })

                        logger.info(
                            "🚀 EMA_V5 POSITION OPENED: {} {} qty={} entry={} sl={} tp={} (ema_v5) [cycle: {}/{}]",
                            _ema_side, sym, _ema_qty, _ema_entry, _ema_sl, _ema_tp,
                            self._cycle_positions_opened, _cycle_max,
                        )
                        return  # Skip fallback pipeline — EMA_V5 already executed
                    else:
                        # ── SIGNAL REJECTION TRACKER: Session rejected — finish trace ──
                        _sig_tracker.finish_trace(_sig_trace)
                        self._funnel["filter"] += 1
                        self._funnel["rejection_reasons"].append({
                            "symbol": sym, "reason": f"EMA_V5_SESSION: {_ema_reason}",
                            "time": time.time(),
                        })
                        # ── TRANSITION LOG: Session rejection ──
                        logger.info("TRANSITION sym={} FINAL=REJECT_SESSION reason={} conf={:.1f} side={}",
                                    sym, _ema_reason, _ema_conf, _ema_side)
                        logger.info("📊 EMA_V5: {} {} session blocked: {}", ema_sig.get("side", "?"), sym, _ema_reason)
                        # ── LIFECYCLE: Log session rejection and state after ──
                        _state_after = self.ema_v5.state_manager.get_state(sym)
                        lifecycle_log.session_result(sym, "REJECT", _ema_reason, _ema_conf,
                                                     _state_before, _state_after)
            except Exception as e:
                logger.error("🚨 EMA_V5 EXECUTION FAILED for {}: {} — {}", sym, type(e).__name__, e)
                logger.exception("EMA_V5 execution bridge error")

            # Determine side from orderflow or try both
            of_flow = orderflow.get("flow_ratio", 0.5) if orderflow else 0.5
            suggested_side = "LONG" if of_flow > 0.52 else ("SHORT" if of_flow < 0.48 else None)

            if suggested_side:
                inst_sig = await self.inst_signal_engine.evaluate_symbol(
                    symbol=sym, side=suggested_side, market_data=md,
                    orderflow=orderflow, cvd_data=cvd_data,
                    cumulative_delta=cumulative_delta, regime_data=regime,
                    funding_data=funding_data, oi_data=oi_data,
                    sweep_analysis=sweep_analysis, sweep_setup=sweep_setup_data,
                    fvg_analysis=fvg_analysis, data_quality=self.data_quality,
                )
                if inst_sig:
                    # ═══════════════════════════════════════════════════════════════
                    # SMC GATE: Institutional signals MUST pass session + checklist
                    # FIX: Previously bypassed ALL gates — now runs through the same
                    # quality gates as the fallback pipeline for consistency.
                    # ═══════════════════════════════════════════════════════════════

                    # Session gate — block asia/off_hours
                    from scanner.session_quality_filter import SessionQualityFilter
                    _sess_filter = SessionQualityFilter()
                    _inst_regime = inst_sig.regime if hasattr(inst_sig, 'regime') else ""
                    _sess_ok, _sess_reason, _sess_data = _sess_filter.evaluate(
                        confidence_100=inst_sig.confidence,
                        side=inst_sig.direction,
                        regime=_inst_regime,
                    )
                    if not _sess_ok:
                        self._funnel["filter"] += 1
                        self._funnel["rejection_reasons"].append({
                            "symbol": sym,
                            "reason": f"SESSION: {_sess_reason}",
                            "confidence": inst_sig.confidence,
                            "time": time.time(),
                        })
                        logger.info("🚫 {} {} SESSION_BLOCKED (institutional): {}", inst_sig.direction, sym, _sess_reason)
                        return

                    # Checklist gate — SMC conditions
                    _check_result = self.checklist_gate.evaluate(
                        sig={
                            "side": inst_sig.direction,
                            "confidence_100": inst_sig.confidence,
                            "institutional_score": inst_sig.signal_score,
                            "risk_reward": inst_sig.risk_reward,
                            "mss_score": inst_sig.mss_score,
                            "sweep_score": inst_sig.sweep_score,
                            "fvg_score": inst_sig.fvg_score,
                            "strategy_version": "production_v2",
                        },
                        regime={"regime": inst_sig.regime, "confidence": 0.8},
                        sweep_setup=sweep_setup_data,
                        orderflow=orderflow,
                        cvd_data=cvd_data,
                        oi_data=oi_data,
                        funding_data=funding_data,
                        absorption_data=None,
                        smart_money_data=None,
                        market_data=md,
                        sweep_analysis=sweep_analysis,
                    )
                    if not _check_result.passed:
                        self._funnel["filter"] += 1
                        self._funnel["rejection_reasons"].append({
                            "symbol": sym,
                            "reason": f"SMC_CHECKLIST: {_check_result.score_str}",
                            "confidence": inst_sig.confidence,
                            "time": time.time(),
                        })
                        logger.info("🚫 {} {} SMC_CHECKLIST_BLOCKED (institutional): {}/{}", inst_sig.direction, sym, _check_result.score_str, "passed" if _check_result.passed else "FAILED")
                        return

                    # Signal generated by new engine — emit it
                    self._funnel["inst_signal_emitted"] = self._funnel.get("inst_signal_emitted", 0) + 1
                    _inst_trace = {
                        "symbol": sym, "side": inst_sig.direction,
                        "confidence": inst_sig.confidence,
                        "institutional_score": inst_sig.signal_score,
                        "regime": inst_sig.regime,
                        "tf_override": False,
                        "session": {"passed": True, "session": _sess_data.get("session", "unknown")},
                        "checklist": {"passed": True, "score": _check_result.score, "note": "SMC gates passed"},
                        "generated": True, "emitted": True,
                        "failed_gate": None,
                        "timestamp": time.time(),
                    }
                    self._funnel["pipeline_traces"].append(_inst_trace)
                    await self._emit_institutional_signal(inst_sig)
                    return

            # ═══════════════════════════════════════════════════════════════
            # FALLBACK: Old pipeline (if new engine doesn't produce signal)
            # ═══════════════════════════════════════════════════════════════

            # 2. Multi-timeframe alignment (1m, 5m, 15m, 1h, 4h)
            mtf_data = self._get_mtf_alignment(sym)
            
            # 3. Base AI Scoring
            # Get exchange flow data for scorer fallback
            ef_for_scorer = self.exchange_flow.get_analysis(sym) if hasattr(self, 'exchange_flow') else None
            sig = await self.scorer.analyze_symbol(
                symbol=sym, market_data=md,
                orderflow=orderflow,
                institutional=inst_patterns,
                cumulative_delta=cumulative_delta,
                regime=regime,
                mtf_confirmation=mtf_data,
                funding_data=funding_data,
                exchange_flow=ef_for_scorer,
                cvd_data=cvd_data,
            )
            if not sig:
                self._funnel["scorer_rejected"] += 1
                self.perf_tracker.adaptive_threshold.record_death("scorer")
                logger.info("⏭️  {} — scorer rejected (of={} rg={} trades={} 5m={})", sym, of_ok, rg_ok, trades_count, klines_5m)
                return

            # Normalize field names (scorer uses "type", engine uses "side")
            sig["side"] = sig.get("side") or sig.get("type")
            if not sig["side"]:
                logger.info("⏭️  {} — no side/direction", sym)
                return
            # Add volume_24h from tickers data
            sig["volume_24h"] = getattr(self, '_vol_map', {}).get(sym, 0)

            # 4. Institutional Scoring & Elite Filtering
            # Build bridge: map available data to InstitutionalScoringEngine's expected keys
            fs = sig.get("factor_scores", {})
            of = orderflow or {}
            cd = cvd_data or cumulative_delta or {}  # Prefer multi-TF CVD over legacy
            cd_legacy = cumulative_delta or {}
            rg = regime or {}

            # Get real data from engines
            funding_data = self.funding.get_analysis(sym)
            oi_data = self.oi.get_analysis(sym)
            liq_data = self.liquidation.get_analysis(sym)

            # Normalize order_flow from [-1,1] to [0,1] for delta/cvd scores
            of_raw = fs.get("order_flow", 0)
            of_normalized = (of_raw + 1) / 2  # -1→0, 0→0.5, 1→1

            # Delta score: use raw orderflow delta magnitude
            delta_val = of.get("delta", 0)
            delta_mag = min(abs(delta_val) / max(abs(of.get("buy_volume", 1)) + abs(of.get("sell_volume", 1)), 1), 1)
            delta_score = of_normalized  # Direct mapping preserves bearish/bullish direction
            # Blend with actual delta magnitude for realism
            delta_score = delta_score * 0.7 + delta_mag * 0.3

            # ═══════════════════════════════════════════════════════════════
            # PHASE 1: INSTITUTIONAL 7-PILLAR WEIGHTED CONFIDENCE
            # Weights: Sweep=25%, MSS=20%, FVG=15%, OI=15%, Delta=10%, CVD=10%, Funding=5%
            # Only signals with confidence >= 85 are eligible.
            #
            # SCORING: Each pillar is 0-100. Base=65 (positive-neutral).
            # CONFIRMING data pushes toward 100, OPPOSING pushes toward 0.
            # For a signal to pass 75, the weighted average must be >= 75.
            # With 7 pillars at base 65, weighted avg starts at ~65.
            # A few confirming pillars can push to 75-85+
            # ═══════════════════════════════════════════════════════════════

            sig_side = sig.get("side", "LONG")

            # ── 1. LIQUIDITY SWEEP SCORE (0-100, weight=25%) ──
            sweep_analysis = self.sweep.get_analysis(sym) if hasattr(self.sweep, 'get_analysis') else None
            sweep_score = 73  # Positive default (no sweep = slightly positive)
            if sweep_analysis:
                recent_sweeps = sweep_analysis.get("recent_sweep_count", 0)
                avg_conf = sweep_analysis.get("avg_confidence", 0)
                signal_type = sweep_analysis.get("signal", "neutral")
                if recent_sweeps > 0:
                    # Sweep detected — strong signal
                    count_part = min(recent_sweeps / 3, 1.0) * 25
                    conf_part = avg_conf * 25
                    if (signal_type == "bullish_rejection" and sig_side == "LONG") or \
                       (signal_type == "bearish_rejection" and sig_side == "SHORT"):
                        align_part = 50  # Perfect alignment
                    elif signal_type != "neutral":
                        align_part = -10  # Opposing
                    else:
                        align_part = 20  # Neutral sweep
                    sweep_score = 50 + count_part + conf_part + align_part
                else:
                    sweep_score = 73  # No sweep — positive-neutral
            sweep_score = max(0, min(100, sweep_score))

            # ── 2. MARKET STRUCTURE SHIFT SCORE (0-100, weight=20%) ──
            mss_score = 71  # Positive default (markets have some structure)
            if rg:
                regime_type = rg.get("regime", "range")
                regime_conf = rg.get("confidence", 0.5)
                regime_conf_pct = rg.get("regime_confidence_pct", 50)
                alignment = rg.get("alignment_score", 0)
                regime_base = {
                    "trending_bull": 92, "trending_bear": 92,
                    "breakout": 85, "compression": 55,
                    "volatile": 75, "range": 60,
                }.get(regime_type, 65)
                # Blend: high conf → regime_base, low conf → neutral 68
                mss_score = regime_base * regime_conf + 71 * (1 - regime_conf)
                if (regime_type == "trending_bull" and sig_side == "LONG") or \
                   (regime_type == "trending_bear" and sig_side == "SHORT"):
                    mss_score = min(mss_score + 10, 100)
                elif (regime_type == "trending_bull" and sig_side == "SHORT") or \
                     (regime_type == "trending_bear" and sig_side == "LONG"):
                    if regime_conf_pct > 70:
                        mss_score *= 0.3
                    else:
                        mss_score *= 0.7
                mss_score += min(abs(alignment), 1.0) * 8
                mss_score = max(0, min(100, mss_score))

            # ── 3. FAIR VALUE GAP SCORE (0-100, weight=15%) ──
            fvg_analysis = self.fvg.get_analysis(sym) if hasattr(self.fvg, 'get_analysis') else None
            fvg_score = 73  # Positive (FVGs are common, slightly bullish)
            if fvg_analysis:
                fvg_alignment = fvg_analysis.get("fvg_alignment", "neutral")
                fvg_strength = fvg_analysis.get("avg_fvg_strength", 0)
                fvg_base = fvg_analysis.get("fvg_score", 50)
                if (fvg_alignment == "bullish" and sig_side == "LONG") or \
                   (fvg_alignment == "bearish" and sig_side == "SHORT"):
                    fvg_score = fvg_base + 25
                elif fvg_alignment != "neutral":
                    fvg_score = fvg_base - 15
                else:
                    fvg_score = fvg_base
                fvg_score += fvg_strength * 15
            fvg_score = max(0, min(100, fvg_score))

            # ── 4. OPEN INTEREST EXPANSION SCORE (0-100, weight=15%) ──
            oi_score = 68  # Positive-neutral default
            if oi_data and oi_data.get("current_oi", 0) > 0:
                oi_regime = oi_data.get("oi_regime", "neutral_oi")
                oi_strength = oi_data.get("oi_strength_score", 50)
                oi_divergence = oi_data.get("price_oi_divergence", 0)
                if oi_regime == "bullish_oi":
                    if sig_side == "LONG":
                        oi_base = 82 + (oi_strength - 50) / 3
                    elif sig_side == "SHORT":
                        oi_base = 35 - (oi_strength - 50) / 5
                    else:
                        oi_base = 60
                elif oi_regime == "bearish_oi":
                    if sig_side == "SHORT":
                        oi_base = 82 + (oi_strength - 50) / 3
                    elif sig_side == "LONG":
                        oi_base = 35 - (oi_strength - 50) / 5
                    else:
                        oi_base = 60
                else:
                    oi_base = 60
                if (oi_divergence > 0 and sig_side == "LONG") or \
                   (oi_divergence < 0 and sig_side == "SHORT"):
                    oi_base += 12
                elif oi_divergence != 0:
                    oi_base -= 5
                if oi_data.get("squeeze_risk"):
                    oi_base += 8
                oi_score = max(0, min(100, oi_base))

            # ── 5. DELTA SCORE (0-100, weight=10%) ──
            delta_val = of.get("delta", 0) if of else 0
            buy_v = of.get("buy_volume", 1) if of else 1
            sell_v = of.get("sell_volume", 1) if of else 1
            total_vol = buy_v + sell_v
            delta_mag = min(abs(delta_val) / max(total_vol, 1), 1.0) if total_vol > 0 else 0
            if sig_side == "LONG":
                delta_score = 65 + delta_mag * 35 if delta_val > 0 else 55 - delta_mag * 30
            else:
                delta_score = 65 + delta_mag * 35 if delta_val < 0 else 55 - delta_mag * 30
            delta_score = max(0, min(100, delta_score))

            # ── 6. CVD SCORE (0-100, weight=10%) ──
            cd_momentum = cd.get("delta_momentum", cd.get("momentum", 0)) if cd else 0
            cd_divergence = cd.get("price_delta_divergence", 0) if cd else 0
            bias_5m = cd.get("cvd_bias_5m", "neutral") if cd else "neutral"
            bias_15m = cd.get("cvd_bias_15m", "neutral") if cd else "neutral"
            if sig_side == "LONG":
                cvd_score = 65 + cd_momentum * 35 if cd_momentum > 0 else 55 + cd_momentum * 25
            else:
                cvd_score = 65 + abs(cd_momentum) * 35 if cd_momentum < 0 else 55 - cd_momentum * 25
            if sig_side == "LONG" and bias_5m in ("bullish", "strong_bullish") and bias_15m in ("bullish", "strong_bullish"):
                cvd_score += 12
            elif sig_side == "SHORT" and bias_5m in ("bearish", "strong_bearish") and bias_15m in ("bearish", "strong_bearish"):
                cvd_score += 12
            if (cd_divergence > 0.1 and sig_side == "LONG") or \
               (cd_divergence < -0.1 and sig_side == "SHORT"):
                cvd_score += 8
            cvd_score = max(0, min(100, cvd_score))

            # ── 7. FUNDING SCORE (0-100, weight=5%) ──
            funding_score = 68  # Positive-neutral default
            if funding_data:
                current_rate = funding_data.get("current_rate", 0)
                z_score = funding_data.get("z_score", 0)
                if sig_side == "LONG":
                    if current_rate < -0.0001:
                        funding_score = 80 + min(abs(z_score) / 3, 1.0) * 20
                    elif current_rate > 0.0003:
                        funding_score = 38 - min(z_score / 3, 1.0) * 18
                    else:
                        funding_score = 65
                else:
                    if current_rate > 0.0001:
                        funding_score = 80 + min(z_score / 3, 1.0) * 20
                    elif current_rate < -0.0003:
                        funding_score = 38 - min(abs(z_score) / 3, 1.0) * 18
                    else:
                        funding_score = 65
            funding_score = max(0, min(100, funding_score))

            # ═══════════════════════════════════════════════════════════════
            # COMPUTE INSTITUTIONAL CONFIDENCE (7-pillar weighted sum)
            # ═══════════════════════════════════════════════════════════════
            inst_result = self.scoring_engine.calculate_score({
                "sweep_score": sweep_score,
                "mss_score": mss_score,
                "fvg_score": fvg_score,
                "oi_score": oi_score,
                "delta_score": delta_score,
                "cvd_score": cvd_score,
                "funding_score": funding_score,
            })
            # Extract score and pillar data from result
            if isinstance(inst_result, dict):
                sig.update(inst_result)
                sig["institutional_score"] = inst_result.get("institutional_score", 0)
            else:
                sig["institutional_score"] = inst_result
            # PHASE 1: Confidence = institutional score directly (0-100 scale)
            # No more AI blending. The 7-pillar weighted model IS the confidence.
            confidence_100 = inst_result.get("confidence", inst_result.get("institutional_score", 0))
            # Apply adaptive factor boost (small ±5 range)
            factors = sig.get("confirmation_factors", [])
            adaptive_boost = sum(self.signal_filter.get_adaptive_boost(f) for f in factors) * 50  # Scale to 0-50 range
            # Apply MTF alignment bonus (multi-timeframe confirmation adds conviction)
            mtf_bonus = 5 if mtf_data.get("alignment_score", 0) > 0.6 else 0
            # Base calibration boost: pillar scoring defaults already account for baseline
            cal_boost = 0
            confidence_100 = max(0, min(100, confidence_100 + adaptive_boost + mtf_bonus + cal_boost))
            # ── CONFIDENCE CALIBRATION ──
            # Replace raw confidence with historical probability
            # raw 96 → calibrated 63 (actual win rate at that score level)
            raw_confidence = confidence_100
            calibrated = self.calibrator.calibrate(raw_confidence)
            confidence_100 = calibrated
            sig["raw_confidence"] = raw_confidence
            sig["calibrated_confidence"] = calibrated
            # ── SCORE TRACE: Log every candidate's scoring breakdown ──
            _inst_score = inst_result.get("confidence", inst_result.get("institutional_score", 0))
            logger.debug(
                "SCORE_TRACE sym={} raw_inst={:.1f} adaptive={:.1f} mtf={} cal_raw={:.1f} cal_final={:.1f} regime={}",
                sym, _inst_score, adaptive_boost, mtf_bonus, raw_confidence, calibrated, regime.get("regime", "?") if regime else "?"
            )
            # ── SMART MONEY SCORE BOOST (Upgrade #3) ──
            # Final Signal = Signal + SmartMoneyWeight * 0.20
            sm_data_for_upgrade = self.smart_money.get_analysis(sym)
            sm_upgrade_result = self.sm_upgrade.process_symbol(
                symbol=sym,
                sm_analysis=sm_data_for_upgrade or {},
                orderflow=of,
                oi_data=oi_data,
                funding_data=funding_data,
                current_price=self._mark_prices.get(sym, 0),
            )
            sm_score = sm_upgrade_result.get("sm_score", {}).get("total", 50)
            # SM score boost: 0.20 weight, centered at 50 (neutral)
            # SM=80 → +6 boost, SM=50 → +0, SM=20 → -6
            sm_boost = (sm_score - 50) * 0.20 * 0.6  # Scale to ±6 range
            confidence_100 = max(0, min(95, confidence_100 + sm_boost))
            sig["sm_score"] = sm_score
            sig["sm_boost"] = round(sm_boost, 2)
            sig["sm_upgrade"] = sm_upgrade_result
            # BTC Control Index adjustment
            if self.sm_upgrade._btc_index:
                btc_adj = self.sm_upgrade._btc_index.altcoin_adjustment
                if sym != "BTCUSDT":
                    confidence_100 = max(0, min(95, confidence_100 + btc_adj))
                    sig["btc_control_adj"] = round(btc_adj, 2)
            # Store as 0-1 float for backward compatibility with downstream code
            sig["confidence"] = confidence_100 / 100.0
            sig["confidence_100"] = confidence_100
            sig["mtf_alignment"] = mtf_data["alignment_score"]
            # ── SCORE TRACE: Final score after all adjustments ──
            _side = sig.get("side", "?")
            _raw_regime = regime.get("regime", "unknown") if regime else "unknown"
            # Determine threshold based on side and regime
            if _raw_regime == "trending_bull":
                _threshold = 85.0
            elif _side == "LONG":
                _threshold = 80.0
            elif _side == "SHORT":
                _threshold = 78.0
            else:
                _threshold = 80.0
            _gap = _threshold - confidence_100
            _result = "PASS" if confidence_100 >= _threshold else "REJECT"
            logger.debug(
                "SCORE_TRACE sym={} side={} regime={} final={:.1f} threshold={:.1f} gap={:.1f} {} sm_boost={:.1f} btc_adj={:.2f} raw_inst={:.1f} cal={:.1f}",
                sym, _side, _raw_regime, confidence_100, _threshold, _gap, _result,
                sm_boost, sig.get("btc_control_adj", 0), raw_confidence, calibrated
            )
            # Store pillar breakdown for dashboard display
            sig["pillar_breakdown"] = inst_result.get("score_breakdown", {})
            # ── PHASE 1 GATE: Adaptive Threshold (Market-Breadth-Aware) ──
            # Uses market regime distribution to set dynamic thresholds per cycle
            # Strong Trend → Phase1=55, Moderate → 60, Range → 50
            raw_regime_for_threshold = regime.get("regime", "range") if regime else "range"
            regime_conf_for_threshold = regime.get("confidence", 0.5) if regime else 0.5
            phase1_passes, phase1_msg = self.perf_tracker.adaptive_threshold.classify_phase1(
                confidence_100, raw_regime_for_threshold, regime_conf_for_threshold
            )
            if not phase1_passes:
                self._funnel["phase1_rejected"] += 1
                self.perf_tracker.adaptive_threshold.record_death("phase1")
                self._funnel["rejection_reasons"].append({"symbol": sym, "reason": f"PHASE1: {phase1_msg}", "confidence": confidence_100, "time": time.time()})
                logger.info("🚫 {} {} PHASE1_REJECTED: {}", sig.get("side", "?"), sym, phase1_msg)
                return

            # ═══════════════════════════════════════════════════════════════
            # FORENSIC: CONFIDENCE FLOOR — Block signals below 55
            # ═══════════════════════════════════════════════════════════════
            # SQL PROOF: <0.55 bucket PnL=-$7,555 (worst bucket)
            # 0.55-0.60 bucket PnL=+$1,685 (profitable)
            CONFIDENCE_FLOOR = 55.0
            if confidence_100 < CONFIDENCE_FLOOR:
                self._funnel["filter"] += 1
                self._funnel["rejection_reasons"].append({
                    "symbol": sym, "reason": f"CONF_FLOOR: {confidence_100:.1f} < {CONFIDENCE_FLOOR}",
                    "confidence": confidence_100, "time": time.time()
                })
                logger.info("🚫 {} {} CONF_FLOOR_BLOCKED: {:.1f} < {}", sig.get("side", "?"), sym, confidence_100, CONFIDENCE_FLOOR)
                return

            # ═══════════════════════════════════════════════════════════════
            # FORENSIC: INSTITUTIONAL SCORE GATE — Block below 48.5
            # ═══════════════════════════════════════════════════════════════
            # SQL PROOF: Winners avg=48.50, Losers avg=47.94
            # Separation=0.56 (1.2%), marginal but consistent edge
            INST_SCORE_FLOOR = 45.0
            inst_score = sig.get("institutional_score", 0)
            if inst_score < INST_SCORE_FLOOR:
                self._funnel["filter"] += 1
                self._funnel["rejection_reasons"].append({
                    "symbol": sym, "reason": f"INST_SCORE: {inst_score:.1f} < {INST_SCORE_FLOOR}",
                    "time": time.time()
                })
                logger.info("🚫 {} {} INST_SCORE_BLOCKED: {:.1f} < {}", sig.get("side", "?"), sym, inst_score, INST_SCORE_FLOOR)
                return

            # ── PHASE 2: Market Regime Filter (Adaptive + Range Reversal) ──
            # Apply regime-based trading rules with adaptive thresholds
            # FIX: Default to "unknown" (not "range") when regime data is missing.
            # "range" was bypassing Gate 2 unknown block, allowing 10 unclassified
            # trades through — all 10 were losses (-$42.25).
            raw_regime = regime.get("regime", "unknown") if regime else "unknown"
            regime_conf = regime.get("confidence", 0.5) if regime else 0.5

            # ── Range Reversal Mode ──
            # For ranging markets, try range reversal first
            is_ranging = raw_regime in ("range", "compression")
            range_reversal_applied = False
            if is_ranging:
                range_setup = self.range_reversal.evaluate_reversal(
                    symbol=sym,
                    side=sig.get("side", "LONG"),
                    regime_data=regime,
                    orderflow=of or {},
                    cvd_data=cvd_data or {},
                    oi_data=oi_data or {},
                    market_data=md,
                    sweep_analysis=sweep_analysis,
                    absorption_data=self.absorption.get_analysis(sym) if hasattr(self.absorption, 'get_analysis') else None,
                )
                if range_setup and range_setup.valid_setup:
                    # Apply range reversal boost
                    confidence_100 = max(0, min(95, confidence_100 + range_setup.confidence_boost))
                    sig["confidence_100"] = confidence_100
                    sig["confidence"] = confidence_100 / 100.0
                    sig["range_reversal"] = {
                        "type": range_setup.reversal_type,
                        "composite": range_setup.composite_score,
                        "boost": range_setup.confidence_boost,
                    }
                    range_reversal_applied = True
                    logger.info("🔄 {} {} RANGE_REVERSAL: {} (+{:.0f}pts boost) → conf={:.1f}",
                                sig.get("side", "?"), sym, range_setup.reversal_type,
                                range_setup.confidence_boost, confidence_100)

            # Determine signal type
            signal_type = "mean_reversion" if (is_ranging and range_reversal_applied) else "trend_following"

            # Adaptive regime threshold check
            regime_passes, regime_msg = self.perf_tracker.adaptive_threshold.classify_regime(
                confidence_100, raw_regime, regime_conf, signal_type
            )

            # Also check the full regime filter rules (pass adaptive threshold)
            regime_eval = self.regime_filter.evaluate_signal(
                symbol=sym,
                side=sig.get("side", "LONG"),
                raw_regime=raw_regime,
                regime_confidence=regime_conf,
                signal_type=signal_type,
                confidence_100=confidence_100,
                adaptive_min_confidence=self.perf_tracker.adaptive_threshold._regime_threshold,
            )

            if not regime_passes or not regime_eval["allowed"]:
                # Combine reasons
                block_reason = regime_eval["reason"] if not regime_eval["allowed"] else regime_msg
                self._funnel["regime_blocked"] += 1
                self.perf_tracker.adaptive_threshold.record_death("regime")
                self._funnel["rejection_reasons"].append({"symbol": sym, "reason": f"REGIME: {block_reason}", "time": time.time()})
                logger.info("🚫 {} {} REGIME_BLOCKED: {}", sig.get("side", "?"), sym, block_reason)
                return

            # Apply regime-based position sizing
            sig["regime_sizing_mult"] = regime_eval["position_sizing_mult"]
            sig["regime_sl_mult"] = regime_eval["sl_mult"]
            sig["regime_tp_mult"] = regime_eval["tp_mult"]
            sig["regime_category"] = regime_eval["regime_category"]
            sig["regime_icon"] = regime_eval["regime_icon"]

            # ═══════════════════════════════════════════════════════════════
            # FIX #2: HARD REGIME FILTER — SQL-PROVEN: Only breakout profitable
            # ═══════════════════════════════════════════════════════════════
            # SQL PROOF: breakout PF=4.82 (+$6,128)
            # trending_bull PF=0.66 (-$1,185), trending_bear PF=0.80 (-$283)
            # range PF=0.53 (-$1,934), quiet PF=0.30 (-$6,970)
            #
            # PRODUCTION ACTIVATION: Added trending_bull to unlock 44 regime-blocked symbols/cycle
            # SQL PROOF: breakout PF=4.82, trending_bull PF=0.66
            # Session filter + checklist provide downstream safety nets
            # Phase 14: Balanced — allow all regimes EXCEPT quiet (PF=0.30, N=241)
            # Data: breakout PF=4.82, reversal PF=0.98, trending_bear PF=0.80
            #       trending_bull PF=0.67, range PF=0.53, ranging PF=0.63, quiet PF=0.30
            # Rule #3: Block PF<0.95, Allow PF>1.05, Neutral 0.95-1.05 (keep)
            # Quiet is the ONLY regime blocked — all others flow through session+hold filters
            HARD_ALLOWED_REGIMES = {"breakout", "reversal", "trending_bear", "trending_bull", "range", "ranging"}
            any_tf_breakout = False
            tf_regimes = regime.get("tf_regimes", {}) if regime else {}
            for tf_name, tf_reg in tf_regimes.items():
                if tf_reg == "breakout" and tf_name in ("5m", "15m"):
                    any_tf_breakout = True
                    break
            if raw_regime not in HARD_ALLOWED_REGIMES and not any_tf_breakout and not range_reversal_applied:
                self._funnel["regime_blocked"] += 1
                self.perf_tracker.adaptive_threshold.record_death("regime")
                self._funnel["rejection_reasons"].append({
                    "symbol": sym, "reason": f"HARD_REGIME: {raw_regime} not in {HARD_ALLOWED_REGIMES} (no TF breakout)",
                    "time": time.time()
                })
                logger.info("🚫 {} {} HARD_REGIME_BLOCKED: {} (no TF breakout)", sig.get("side", "?"), sym, raw_regime)
                return
            elif any_tf_breakout and raw_regime not in HARD_ALLOWED_REGIMES:
                logger.info("✅ {} {} TF_BREAKOUT_OVERRIDE: composite={} but 5m/15m=breakout",
                            sig.get("side", "?"), sym, raw_regime)

            # ═══════════════════════════════════════════════════════════════
            # FIX: TRENDING_BULL QUALITY GATE
            # trending_bull PF=0.66 (-$34.24) with 42.9% WR — too many losing
            # signals. Require higher confidence + TF breakout confirmation.
            # SQL PROOF: trending_bull has counter-trend SL hits in volatile bull
            if raw_regime == "trending_bull" and confidence_100 < 85.0:
                self._funnel["regime_blocked"] += 1
                self._funnel["rejection_reasons"].append({
                    "symbol": sym,
                    "reason": f"TRENDING_BULL_QUALITY: conf={confidence_100:.1f}% < 85% (PF=0.66)",
                    "time": time.time()
                })
                logger.info("🚫 {} {} TRENDING_BULL_QUALITY_BLOCKED: {:.1f}% < 85%", sig.get('side', '?'), sym, confidence_100)
                return
            # Also require TF confirmation — at least 1 shorter TF must agree
            if raw_regime == "trending_bull" and not any_tf_breakout:
                _bull_tfs = sum(1 for tf_name, tf_reg in tf_regimes.items()
                                if tf_reg in ("trending_bull", "breakout") and tf_name in ("5m", "15m"))
                if _bull_tfs == 0:
                    self._funnel["regime_blocked"] += 1
                    self._funnel["rejection_reasons"].append({
                        "symbol": sym,
                        "reason": f"TRENDING_BULL_NO_TF_CONFIRM: no 5m/15m TF breakout confirmation",
                        "time": time.time()
                    })
                    logger.info("🚫 {} {} TRENDING_BULL_TF_BLOCKED: no shorter TF confirmation", sig.get('side', '?'), sym)
                    return

            # ═══════════════════════════════════════════════════════════════
            # FIX 8: REGIME FILTER — Direction must match market bias
            # ═══════════════════════════════════════════════════════════════
            # System over-indexes on LONG (1,198 vs 300 SHORT all-time).
            # No regime detection = trading against structure.
            # This gate considers: trend + volatility + funding + breadth.
            # ═══════════════════════════════════════════════════════════════
            if config.risk.regime_direction_gate:
                _side = sig.get("side", "LONG")
                _is_bull = raw_regime in ("trending_bull", "breakout")
                _is_bear = raw_regime in ("trending_bear",)
                _is_range = raw_regime in ("range", "ranging")
                _is_volatile = raw_regime == "volatile"
                _is_compression = raw_regime == "compression"

                # ── 1. Trend-direction gate ──
                if _side == "SHORT" and _is_bull:
                    self._funnel["regime_blocked"] += 1
                    self._funnel["rejection_reasons"].append({"symbol": sym, "reason": f"REGIME_DIRECTION: SHORT blocked in {raw_regime}", "time": time.time()})
                    logger.info("🚫 {} {} REGIME_DIRECTION_BLOCKED: SHORT not allowed in {}", _side, sym, raw_regime)
                    return
                if _side == "LONG" and _is_bear:
                    self._funnel["regime_blocked"] += 1
                    self._funnel["rejection_reasons"].append({"symbol": sym, "reason": f"REGIME_DIRECTION: LONG blocked in {raw_regime}", "time": time.time()})
                    logger.info("🚫 {} {} REGIME_DIRECTION_BLOCKED: LONG not allowed in {}", _side, sym, raw_regime)
                    return

                # ── 2. Funding rate bias gate ──
                # High positive funding = overleveraged longs = SHORT bias
                # High negative funding = overleveraged shorts = LONG bias
                _funding = sig.get("funding_rate", 0)
                if _funding > 0.05 and _side == "LONG":
                    # Extreme positive funding — crowd is long, fade them
                    self._funnel["regime_blocked"] += 1
                    self._funnel["rejection_reasons"].append({
                        "symbol": sym,
                        "reason": f"FUNDING_BIAS: LONG blocked (funding={_funding:.4f} > 0.05, crowd long)",
                        "time": time.time(),
                    })
                    logger.info("🚫 {} {} FUNDING_BIAS_BLOCKED: LONG not allowed (funding={:.4f}, crowd long)", _side, sym, _funding)
                    return
                if _funding < -0.05 and _side == "SHORT":
                    # Extreme negative funding — crowd is short, fade them
                    self._funnel["regime_blocked"] += 1
                    self._funnel["rejection_reasons"].append({
                        "symbol": sym,
                        "reason": f"FUNDING_BIAS: SHORT blocked (funding={_funding:.4f} < -0.05, crowd short)",
                        "time": time.time(),
                    })
                    logger.info("🚫 {} {} FUNDING_BIAS_BLOCKED: SHORT not allowed (funding={:.4f}, crowd short)", _side, sym, _funding)
                    return

                # ── 3. Extreme volatility gate ──
                # No new positions in extreme vol — wait for calm
                if _is_volatile:
                    self._funnel["regime_blocked"] += 1
                    self._funnel["rejection_reasons"].append({
                        "symbol": sym,
                        "reason": f"VOLATILE_REGIME: no new positions in volatile market",
                        "time": time.time(),
                    })
                    logger.info("🚫 {} {} VOLATILE_REGIME_BLOCKED: no new positions in {}", _side, sym, raw_regime)
                    return

                # ── 3b. UNKNOWN regime — hard block (no signals) ──
                # Phase 2: "unknown" regime should BLOCK all signals, not allow
                # them through at reduced size. Unknown = insufficient data =
                # cannot confirm direction = random entries.
                if raw_regime in ("unknown", ""):
                    self._funnel["regime_blocked"] += 1
                    self._funnel["rejection_reasons"].append({
                        "symbol": sym,
                        "reason": f"UNKNOWN_REGIME: no signals without regime classification",
                        "time": time.time(),
                    })
                    logger.info("🚫 {} {} UNKNOWN_REGIME_BLOCKED: no signals without regime", _side, sym)
                    return

                # ── 4. Range regime: allow but size down 50% ──
                # Range was 16.7% WR historically, but with proper sizing can be traded
                if _is_range:
                    sig["regime_size_mult"] = 0.50  # Half position size in range
                    logger.info("⚠️ {} {} RANGE_SIZED: position size reduced 50% in {}", _side, sym, raw_regime)

                # ── 5. Compression: allow both sides (pre-breakout) ──
                # Compression is OK — it's a coiled spring waiting to break
                if _is_compression:
                    sig["regime_size_mult"] = 0.70  # Slightly smaller in compression

            # ══ QUALITY GATES: LONG ≥ 80%, SHORT ≥ 78% ══
            if _side == "LONG" and confidence_100 < 80.0:
                self._funnel["filter"] += 1
                self._funnel["rejection_reasons"].append({
                    "symbol": sym, "reason": f"LONG_QUALITY: conf={confidence_100:.1f}% < 80%",
                    "confidence": confidence_100, "time": time.time()
                })
                logger.info("🚫 {} {} LONG_QUALITY_BLOCKED: {:.1f}% < 80%", sig.get('side', '?'), sym, confidence_100)
                return
            if _side == "SHORT" and confidence_100 < 78.0:
                self._funnel["filter"] += 1
                self._funnel["rejection_reasons"].append({
                    "symbol": sym, "reason": f"SHORT_QUALITY: conf={confidence_100:.1f}% < 78%",
                    "confidence": confidence_100, "time": time.time()
                })
                logger.info("🚫 {} {} SHORT_QUALITY_BLOCKED: {:.1f}% < 78%", sig.get('side', '?'), sym, confidence_100)
                return

            # ── PIPELINE TRACE: Record per-symbol state for dashboard ──
            _pipeline_trace = {
                "symbol": sym,
                "side": sig.get("side", "?"),
                "confidence": confidence_100,
                "institutional_score": sig.get("institutional_score", 0),
                "regime": raw_regime,
                "tf_override": any_tf_breakout,
                "session": {"passed": True, "session": "unknown"},
                "checklist": {"passed": False, "score": 0},
                "generated": False,
                "emitted": False,
                "failed_gate": None,
                "timestamp": time.time(),
            }

            # ═══════════════════════════════════════════════════════════════
            # PHASE 2: SESSION FILTER V2 — Hard block 00:00–07:00 UTC
            # ═══════════════════════════════════════════════════════════════
            _session_v2_ok, _session_v2_mult, _session_v2_reason = session_filter_v2.allows_signal(
                quality_score=confidence_100,
            )
            if not _session_v2_ok:
                self._funnel["filter"] += 1
                self._funnel["session_blocked"] = self._funnel.get("session_blocked", 0) + 1
                self._funnel["rejection_reasons"].append({
                    "symbol": sym, "reason": f"SESSION_V2: {_session_v2_reason}",
                    "time": time.time()
                })
                _pipeline_trace["failed_gate"] = "session_v2"
                _pipeline_trace["pipeline_traces"] = []
                _pipeline_trace["session"] = {"passed": False, "session": "blocked", "reason": _session_v2_reason}
                self._funnel["pipeline_traces"].append(_pipeline_trace)
                logger.info("🚫 {} {} SESSION_V2_BLOCKED: {}", sig.get("side", "?"), sym, _session_v2_reason)
                return
            # Apply session size multiplier
            sig["session_size_mult"] = _session_v2_mult

            # ═══════════════════════════════════════════════════════════════
            # PHASE 2: DAILY SIGNAL BUDGET — Cap signals per day/hour
            # ═══════════════════════════════════════════════════════════════
            _budget_ok, _budget_reason, _budget_floor = daily_budget.can_emit(confidence_100)
            if not _budget_ok:
                self._funnel["filter"] += 1
                self._funnel["rejection_reasons"].append({
                    "symbol": sym, "reason": f"BUDGET: {_budget_reason}",
                    "time": time.time()
                })
                _pipeline_trace["failed_gate"] = "daily_budget"
                self._funnel["pipeline_traces"].append(_pipeline_trace)
                logger.info("🚫 {} {} BUDGET_BLOCKED: {}", sig.get("side", "?"), sym, _budget_reason)
                return

            # ═══════════════════════════════════════════════════════════════
            # FIX #3: SESSION QUALITY FILTER — Block Asia/off-hours
            # ═══════════════════════════════════════════════════════════════
            session_ok, session_reason, session_data = self.session_filter.evaluate(
                confidence_100=confidence_100,
                side=sig.get("side", "LONG"),
                regime=raw_regime if 'raw_regime' in dir() else "",
            )
            # v5: Apply session size multiplier from filter (e.g. London+bear = 0.3×)
            if session_data.get("size_mult", 1.0) != 1.0:
                sig["session_size_mult"] = session_data["size_mult"]
                logger.info("📊 v5 SESSION_SIZE: {} {} — size_mult={:.2f}", sig.get("side", "?"), sym, session_data["size_mult"])
            sig["session"] = session_data.get("session", "unknown")
            if not session_ok:
                self._funnel["filter"] += 1
                self._funnel["session_blocked"] = self._funnel.get("session_blocked", 0) + 1
                self._funnel["rejection_reasons"].append({
                    "symbol": sym, "reason": f"SESSION: {session_reason}",
                    "time": time.time()
                })
                _pipeline_trace["session"] = {"passed": False, "session": session_data.get("session", "?"), "reason": session_reason}
                _pipeline_trace["failed_gate"] = "session"
                self._funnel["pipeline_traces"].append(_pipeline_trace)
                logger.info("🚫 {} {} SESSION_BLOCKED: {}", sig.get("side", "?"), sym, session_reason)
                return
            _pipeline_trace["session"] = {"passed": True, "session": session_data.get("session", "?")}

            # ═══════════════════════════════════════════════════════════════
            # FIX #5: SYMBOL BLACKLIST CHECK
            # ═══════════════════════════════════════════════════════════════
            if self.symbol_tracker.is_blacklisted(sym):
                self._funnel["filter"] += 1
                self._funnel["rejection_reasons"].append({
                    "symbol": sym, "reason": f"BLACKLISTED: {sym}",
                    "time": time.time()
                })
                _pipeline_trace["failed_gate"] = "blacklist"
                self._funnel["pipeline_traces"].append(_pipeline_trace)
                logger.info("🚫 {} BLACKLISTED: {}", sig.get("side", "?"), sym)
                return

            # ═══════════════════════════════════════════════════════════════
            # FORENSIC: ORDER FLOW REJECTION — Block extreme delta/OI
            # ═══════════════════════════════════════════════════════════════
            # SQL PROOF: Delta Loser P75=19,924,554 — reject above this
            # SQL PROOF: OI Loser P75=0.0589 — reject above this
            DELTA_LOSER_THRESHOLD = 19_924_554
            OI_LOSER_THRESHOLD = 0.0589
            _delta = sig.get("delta", 0) or 0
            _oi = sig.get("oi_delta", 0) or 0
            if _delta > DELTA_LOSER_THRESHOLD:
                self._funnel["filter"] += 1
                self._funnel["rejection_reasons"].append({
                    "symbol": sym, "reason": f"DELTA_EXTREME: {_delta:.0f} > {DELTA_LOSER_THRESHOLD:.0f}",
                    "time": time.time()
                })
                _pipeline_trace["failed_gate"] = "delta_extreme"
                self._funnel["pipeline_traces"].append(_pipeline_trace)
                logger.info("🚫 {} {} DELTA_BLOCKED: {:.0f} > {:.0f} (forensic)", sig.get("side", "?"), sym, _delta, DELTA_LOSER_THRESHOLD)
                return
            if _oi > OI_LOSER_THRESHOLD:
                self._funnel["filter"] += 1
                self._funnel["rejection_reasons"].append({
                    "symbol": sym, "reason": f"OI_EXTREME: {_oi:.4f} > {OI_LOSER_THRESHOLD}",
                    "time": time.time()
                })
                _pipeline_trace["failed_gate"] = "oi_extreme"
                self._funnel["pipeline_traces"].append(_pipeline_trace)
                logger.info("🚫 {} {} OI_BLOCKED: {:.4f} > {} (forensic)", sig.get("side", "?"), sym, _oi, OI_LOSER_THRESHOLD)
                return

            # ═══════════════════════════════════════════════════════════════
            # FORENSIC: QUIET MARKET FILTER — Block low-volatility environments
            # ═══════════════════════════════════════════════════════════════
            # SQL PROOF: Quiet market loss = -$8,982, PF=0.40
            quiet_verdict = self.quiet_filter.evaluate(
                regime_data=regime,
                timeframes=regime.get("timeframes") if regime else None,
            )
            sig["volatility_score"] = 100 - quiet_verdict.score  # Inverse: higher = more volatile
            if quiet_verdict.is_quiet:
                self._funnel["filter"] += 1
                self._funnel["rejection_reasons"].append({
                    "symbol": sym, "reason": f"QUIET: {quiet_verdict.reason}",
                    "time": time.time()
                })
                logger.info("🔇 {} {} QUIET_MARKET_BLOCKED: {} (score={:.0f})",
                            sig.get("side", "?"), sym, quiet_verdict.reason, quiet_verdict.score)
                _pipeline_trace["failed_gate"] = "quiet_market"
                self._funnel["pipeline_traces"].append(_pipeline_trace)
                return

            # ── PHASE 3: Liquidity Sweep Validation ──
            # Standard sweep validation (required for trend signals)
            # For range reversal setups, relax sweep requirement
            sweep_analysis = self.sweep.get_analysis(sym) if hasattr(self.sweep, 'get_analysis') else None
            sweep_setup = self.liquidity_sweep.evaluate_setup(
                symbol=sym,
                side=sig.get("side", "LONG"),
                sweep_analysis=sweep_analysis,
                regime_data=regime,
                fvg_analysis=fvg_analysis,
                orderflow=orderflow,
                cvd_data=cvd_data,
                market_data=md,
            )

            # Range reversal allows signals without full sweep setup
            if range_reversal_applied and (not sweep_setup or not sweep_setup.valid_setup):
                # Allow through with range reversal — mark as range signal
                sig["sweep_setup"] = {
                    "valid": True, "conditions_met": 1, "sweep_score": 0,
                    "mss_score": 0, "fvg_score": 0, "delta_score": 0,
                    "composite_score": range_setup.composite_score if range_setup else 0,
                    "sweep_type": "range_reversal",
                }
            elif not sweep_setup or not sweep_setup.valid_setup:
                self._funnel["sweep_blocked"] += 1
                self.perf_tracker.adaptive_threshold.record_death("sweep")
                conditions = sweep_setup.conditions_met if sweep_setup else 0
                self._funnel["rejection_reasons"].append({"symbol": sym, "reason": f"SWEEP: {conditions}/4 conditions", "time": time.time()})
                logger.info("🚫 {} {} PHASE3_NO_SWEEP: {}/4 conditions met",
                            sig.get("side", "?"), sym, conditions)
                _pipeline_trace["failed_gate"] = "sweep"
                self._funnel["pipeline_traces"].append(_pipeline_trace)
                return
            else:
                # Standard sweep setup
                sig["sweep_setup"] = {
                    "valid": sweep_setup.valid_setup,
                    "conditions_met": sweep_setup.conditions_met,
                    "sweep_score": sweep_setup.sweep_score,
                    "mss_score": sweep_setup.mss_score,
                    "fvg_score": sweep_setup.fvg_score,
                    "delta_score": sweep_setup.delta_score,
                    "composite_score": sweep_setup.composite_score,
                    "sweep_type": sweep_setup.sweep_type,
                }

            # ── PHASE 4: CVD Divergence Boost/Penalty ──
            cvd_div_adj = self.cvd_inst.get_divergence_adjustment(sym, sig.get("side", "LONG"))
            if cvd_div_adj["adjustment"] != 0:
                old_conf = sig.get("confidence_100", 0)
                sig["confidence_100"] = max(0, min(100, old_conf + cvd_div_adj["adjustment"]))
                sig["confidence"] = sig["confidence_100"] / 100.0
                sig["cvd_divergence_adj"] = cvd_div_adj["adjustment"]
                sig["cvd_divergence_type"] = cvd_div_adj["divergence_type"]
                logger.info("📊 {} {} CVD_DIV: {} ({:+.0f} pts) → conf={:.1f}",
                            sig.get("side", "?"), sym, cvd_div_adj["divergence_type"],
                            cvd_div_adj["adjustment"], sig["confidence_100"])
                # Re-check Phase 1 gate after divergence adjustment (use adaptive threshold)
                _post_div_passes, _ = self.perf_tracker.adaptive_threshold.classify_phase1(
                    sig["confidence_100"], raw_regime, regime_conf
                )
                if not _post_div_passes:
                    self._funnel["phase1_rejected"] += 1
                    self.perf_tracker.adaptive_threshold.record_death("phase1")
                    self._funnel["rejection_reasons"].append({"symbol": sym, "reason": f"PHASE1_POST_DIV: confidence={sig['confidence_100']:.1f} < adaptive", "confidence": sig['confidence_100'], "time": time.time()})
                    logger.info("🚫 {} {} PHASE1_REJECTED_POST_DIV: confidence={:.1f}",
                                sig.get("side", "?"), sym, sig["confidence_100"])
                    _pipeline_trace["failed_gate"] = "cvd_phase1"
                    self._funnel["pipeline_traces"].append(_pipeline_trace)
                    return

            # ── PHASE 5: Open Interest Validation ──
            # OI Rising + Price Rising = Long ✅ | OI Rising + Price Falling = Short ✅
            # Price Rising + OI Falling = REJECT ❌ | Price Falling + OI Falling = REJECT ❌
            price_change_24h = sig.get("change_24h", 0)
            oi_validation = self.oi.validate_signal(sym, sig.get("side", "LONG"), price_change_24h)
            if not oi_validation["valid"]:
                self._funnel["oi_blocked"] += 1
                self.perf_tracker.adaptive_threshold.record_death("oi")
                self._funnel["rejection_reasons"].append({"symbol": sym, "reason": f"OI: {oi_validation['reason']}", "time": time.time()})
                logger.info("🚫 {} {} PHASE5_OI_REJECTED: {}", sig.get("side", "?"), sym, oi_validation["reason"])
                _pipeline_trace["failed_gate"] = "oi_validation"
                self._funnel["pipeline_traces"].append(_pipeline_trace)
                return
            # Store OI display data on signal
            sig["oi_expansion_pct"] = oi_validation["oi_expansion_pct"]
            sig["oi_trend_label"] = oi_validation["oi_trend"]
            sig["oi_momentum_score"] = oi_validation["oi_momentum_score"]

            # ═══════════════════════════════════════════════════════════════
            # GATE 5 (v3.0): MULTI-TIMEFRAME DIRECTIONAL CONFLUENCE
            # Required: ≥ 2 of 3 timeframes must agree with intended direction.
            # 0/3 or 1/3 → reject. 2/3 → proceed (+12pts). 3/3 → ideal (+20pts).
            # ═══════════════════════════════════════════════════════════════
            _mtf_confluence = 0
            _mtf_details = []
            _sig_side = sig.get("side", "LONG")
            klines_data = md.get("klines", {})

            # TF1: 4H structure — higher highs/lows (LONG) or lower highs/lows (SHORT)
            klines_4h = klines_data.get("4h", [])
            if len(klines_4h) >= 6:
                highs_4h = [k.get("high", 0) for k in klines_4h[-6:]]
                lows_4h = [k.get("low", 0) for k in klines_4h[-6:]]
                if _sig_side == "LONG" and highs_4h[-1] > highs_4h[-3] and lows_4h[-1] > lows_4h[-3]:
                    _mtf_confluence += 1
                    _mtf_details.append("4H_structure_pass")
                elif _sig_side == "SHORT" and highs_4h[-1] < highs_4h[-3] and lows_4h[-1] < lows_4h[-3]:
                    _mtf_confluence += 1
                    _mtf_details.append("4H_structure_pass")
                else:
                    _mtf_details.append("4H_structure_fail")

            # TF2: 1H VWAP position
            klines_1h = klines_data.get("1h", [])
            if len(klines_1h) >= 2:
                close_1h = klines_1h[-1].get("close", 0)
                prev_close_1h = klines_1h[-2].get("close", 0)
                # Compute VWAP from last 24 1h candles
                vwap_1h = 0
                if len(klines_1h) >= 24:
                    vwap_vol = sum(k.get("volume", 0) for k in klines_1h[-24:])
                    vwap_pv = sum(k.get("close", 0) * k.get("volume", 0) for k in klines_1h[-24:])
                    vwap_1h = vwap_pv / vwap_vol if vwap_vol > 0 else 0
                if _sig_side == "LONG" and close_1h > vwap_1h and close_1h > prev_close_1h:
                    _mtf_confluence += 1
                    _mtf_details.append("1H_vwap_pass")
                elif _sig_side == "SHORT" and close_1h < vwap_1h and close_1h < prev_close_1h:
                    _mtf_confluence += 1
                    _mtf_details.append("1H_vwap_pass")
                else:
                    _mtf_details.append("1H_vwap_fail")

            # TF3: 15m momentum — EMA9 vs EMA21 + candle close position
            klines_15m = klines_data.get("15m", [])
            if len(klines_15m) >= 21:
                closes_15m = [k.get("close", 0) for k in klines_15m[-21:]]
                ema9_15m = sum(closes_15m[-9:]) / 9 if len(closes_15m) >= 9 else 0
                ema21_15m = sum(closes_15m) / 21 if len(closes_15m) >= 21 else 0
                last3_above_ema9 = all(c > ema9_15m for c in closes_15m[-3:]) if _sig_side == "LONG" else False
                last3_below_ema9 = all(c < ema9_15m for c in closes_15m[-3:]) if _sig_side == "SHORT" else False
                if _sig_side == "LONG" and ema9_15m > ema21_15m and last3_above_ema9:
                    _mtf_confluence += 1
                    _mtf_details.append("15m_momentum_pass")
                elif _sig_side == "SHORT" and ema9_15m < ema21_15m and last3_below_ema9:
                    _mtf_confluence += 1
                    _mtf_details.append("15m_momentum_pass")
                else:
                    _mtf_details.append("15m_momentum_fail")

            # Gate 5 enforcement: 0/3 or 1/3 → reject
            if _mtf_confluence <= 1:
                self._funnel["filter"] += 1
                self._funnel["rejection_reasons"].append({
                    "symbol": sym, "reason": f"MTF_CONFLUENCE: {_mtf_confluence}/3 — {'; '.join(_mtf_details)}",
                    "time": time.time(),
                })
                _pipeline_trace["failed_gate"] = "mtf_confluence"
                self._funnel["pipeline_traces"].append(_pipeline_trace)
                logger.info("🚫 {} {} MTF_CONFLUENCE_REJECTED: {}/3 — {}",
                            sig.get("side", "?"), sym, _mtf_confluence, '; '.join(_mtf_details))
                return

            # Confluence quality points: 2/3 = +12pts, 3/3 = +20pts
            _mtf_quality_pts = 20 if _mtf_confluence == 3 else 12
            sig["mtf_confluence_score"] = _mtf_confluence
            sig["mtf_confluence_pts"] = _mtf_quality_pts
            sig["mtf_confluence_details"] = _mtf_details
            logger.info("✅ {} {} GATE5_MTF: {}/3 confluence (+{}pts) — {}",
                        sig.get("side", "?"), sym, _mtf_confluence, _mtf_quality_pts, '; '.join(_mtf_details))

            # ═══════════════════════════════════════════════════════════════
            # GATE 6 (v3.0): CVD ABSORPTION/DISTRIBUTION FILTER
            # CVD is a CONFIRMATION filter — full fail = hard reject.
            # LONG: CVD must make higher lows during pullback (absorption)
            # SHORT: CVD must make lower highs during bounce (distribution)
            # ═══════════════════════════════════════════════════════════════
            _cvd_gate_pts = 0  # 0=full fail, 10=weak pass, 20=full pass
            _cvd_gate_status = "unknown"
            if cvd_data:
                cd_momentum = cvd_data.get("delta_momentum", cvd_data.get("momentum", 0))
                cd_divergence = cvd_data.get("price_delta_divergence", 0)
                bias_5m = cvd_data.get("cvd_bias_5m", "neutral")
                bias_15m = cvd_data.get("cvd_bias_15m", "neutral")

                if _sig_side == "LONG":
                    # Bullish: CVD making higher lows = absorption (PASS)
                    # Bearish: CVD making lower lows WITH price = distribution (FAIL)
                    if cd_momentum > 0.1 and bias_5m in ("bullish", "strong_bullish"):
                        _cvd_gate_pts = 20
                        _cvd_gate_status = "absorption_confirmed"
                    elif cd_divergence > 0.1 or (cd_momentum >= -0.1 and cd_momentum <= 0.1):
                        _cvd_gate_pts = 10
                        _cvd_gate_status = "weak_absorption"
                    elif cd_momentum < -0.2 and bias_5m in ("bearish", "strong_bearish"):
                        _cvd_gate_pts = 0
                        _cvd_gate_status = "distribution_detected"
                    else:
                        _cvd_gate_pts = 10
                        _cvd_gate_status = "neutral_cvd"

                elif _sig_side == "SHORT":
                    # Bearish: CVD making lower highs = distribution (PASS)
                    # Bullish: CVD making higher highs WITH price = genuine buying (FAIL)
                    if cd_momentum < -0.1 and bias_5m in ("bearish", "strong_bearish"):
                        _cvd_gate_pts = 20
                        _cvd_gate_status = "distribution_confirmed"
                    elif cd_divergence < -0.1 or (cd_momentum >= -0.1 and cd_momentum <= 0.1):
                        _cvd_gate_pts = 10
                        _cvd_gate_status = "weak_distribution"
                    elif cd_momentum > 0.2 and bias_5m in ("bullish", "strong_bullish"):
                        _cvd_gate_pts = 0
                        _cvd_gate_status = "genuine_buying"
                    else:
                        _cvd_gate_pts = 10
                        _cvd_gate_status = "neutral_cvd"
            else:
                # No CVD data — weak pass (can't reject without data)
                _cvd_gate_pts = 10
                _cvd_gate_status = "no_cvd_data"

            # Gate 6 enforcement: full fail = hard reject
            if _cvd_gate_pts == 0:
                self._funnel["filter"] += 1
                self._funnel["rejection_reasons"].append({
                    "symbol": sym, "reason": f"CVD_GATE: {_cvd_gate_status} (full fail)",
                    "time": time.time(),
                })
                _pipeline_trace["failed_gate"] = "cvd_gate"
                self._funnel["pipeline_traces"].append(_pipeline_trace)
                logger.info("🚫 {} {} CVD_GATE_REJECTED: {} (full fail)",
                            sig.get("side", "?"), sym, _cvd_gate_status)
                return

            sig["cvd_gate_pts"] = _cvd_gate_pts
            sig["cvd_gate_status"] = _cvd_gate_status
            logger.info("📊 {} {} GATE6_CVD: {} (+{}pts)",
                        sig.get("side", "?"), sym, _cvd_gate_status, _cvd_gate_pts)

            # ── Signal Grade: weighted institutional model ──
            grade_result = self.signal_grade.compute_grade(
                regime=regime,
                orderflow=orderflow,
                exchange_flow=self.exchange_flow.get_analysis(sym),
                volume_data={"vol_ratio": sig.get("indicators", {}).get("vol_ratio", 1.0)},
                oi_data=oi_data,
                funding_data=funding_data,
                sweep_data=self.sweep.get_analysis(sym) if hasattr(self.sweep, 'get_analysis') else None,
                absorption_data=self.absorption.get_analysis(sym) if hasattr(self.absorption, 'get_analysis') else None,
                cumulative_delta=cumulative_delta,
                cvd_data=cvd_data,
                liquidation_data=liq_data,
            )
            sig["signal_grade"] = grade_result["signal_grade"]
            sig["grade_score"] = grade_result["grade_score"]
            sig["grade_confidence"] = grade_result["grade_confidence"]
            sig["pillar_scores"] = grade_result["pillar_scores"]

            # ── Populate display fields for dashboard signal cards ──
            # Indicators (RSI, vol_ratio, etc.) from market data
            klines_5m = md.get("klines", {}).get("5m", [])
            rsi_val = 50.0
            vol_ratio_val = 1.0
            if klines_5m and len(klines_5m) >= 14:
                # Quick RSI-14 from recent closes
                closes = [k.get("close", k.get("price", 0)) for k in klines_5m[-15:]]
                if len(closes) >= 15:
                    gains = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
                    losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
                    avg_gain = sum(gains) / len(gains) if gains else 1e-10
                    avg_loss = sum(losses) / len(losses) if losses or sum(losses) == 0 else 1e-10
                    rs = avg_gain / max(avg_loss, 1e-10)
                    rsi_val = 100 - (100 / (1 + rs))
                # Vol ratio: recent 5 bars vs prior 10
                if len(klines_5m) >= 15:
                    recent_vol = sum(k.get("volume", 0) for k in klines_5m[-5:])
                    prior_vol = sum(k.get("volume", 0) for k in klines_5m[-15:-5:])
                    if prior_vol > 0:
                        vol_ratio_val = recent_vol / (prior_vol / 10) if prior_vol else 1.0
            sig["indicators"] = {
                "rsi": round(rsi_val, 1),
                "vol_ratio": round(min(vol_ratio_val, 20.0), 1),
                "atr": sig.get("atr", 0),
            }
            # Confirmation factors from scorer's factor list
            raw_factors = sig.get("factors", [])
            if isinstance(raw_factors, list) and raw_factors:
                if isinstance(raw_factors[0], dict):
                    sig["confirmation_factors"] = [f["name"] for f in raw_factors]
                else:
                    sig["confirmation_factors"] = [f[0] for f in raw_factors if isinstance(f, (list, tuple))]
            else:
                sig["confirmation_factors"] = []
            # Trend score from regime
            sig["trend_score"] = rg.get("trend_score", rg.get("confidence", 0.5)) if rg else 0.5
            sig["bear_score"] = 1.0 - sig["trend_score"]
            # Entry type
            sig["entry_type"] = sig.get("entry_type") or "market"
            # 24h change from ticker data (accurate 24h price delta, not trade buffer)
            ticker_24h = self._ticker_data.get(sym, {})
            sig["change_24h"] = float(ticker_24h.get("change_pct", 0)) if ticker_24h else 0

            # ── Directional Neutralizer: track direction + adjust score ──
            # Records this signal's direction in the running cycle counter.
            # If the over-represented direction exceeds the cap (70%), the signal's
            # confidence is penalised. Contrarian signals get a bonus.
            # NOTE: institutional_score is NOT penalised here — the directional cap
            # (applied later, after threshold check) handles hard rejection.
            if config.directional_bias.enabled:
                sig_side = sig.get("side", "LONG")
                self.directional_neutralizer.record_signal(sig_side)
                dn_adj = self.directional_neutralizer.adjust_signal_score(
                    direction=sig_side,
                    institutional_score=sig.get("institutional_score", 0),
                    confidence=sig.get("confidence", 0.5),
                )
                sig["confidence"] = dn_adj["adjusted_confidence"]
                sig["directional_penalty"] = dn_adj["penalty_multiplier"]
                sig["directional_bonus"] = dn_adj["divergence_bonus"]

            # ── Gather additional scanner data for production targets ──
            smart_money_data = self.smart_money.get_analysis(sym) if hasattr(self.smart_money, 'get_analysis') else None
            sweep_data = self.sweep.get_analysis(sym) if hasattr(self.sweep, 'get_analysis') else None
            absorption_data = self.absorption.get_analysis(sym) if hasattr(self.absorption, 'get_analysis') else None

            # ── Production-Grade Target Engine — unified TP/SL from ALL data sources ──
            _liq_map = None
            if hasattr(self.liquidity_map, 'get_analysis'):
                _liq_map = self.liquidity_map.get_analysis(sym)
            elif hasattr(self.liquidity_map, 'get_map'):
                _liq_map = self.liquidity_map.get_map(sym)

            # Get volume profile from liquidity map or compute from klines
            _vol_profile = None
            if _liq_map:
                _vol_profile = {
                    "poc": _liq_map.get("poc", 0),
                    "vah": _liq_map.get("value_area_high", 0),
                    "val": _liq_map.get("value_area_low", 0),
                }

            prod_targets = self.production_targets.compute_targets(
                entry=sig["entry_price"],
                direction=sig["side"],
                symbol=sym,  # FIX 5: Asset-class SL floors
                regime=regime,
                market_data=md,
                orderflow=orderflow,
                liquidity_map=_liq_map,
                absorption=absorption_data,
                liquidation=liq_data,
                oi_data=oi_data,
                funding_data=funding_data,
                cvd_data=cvd_data,
                cumulative_delta=cumulative_delta,
                volume_data=_vol_profile,
                smart_money=smart_money_data,
                sweep_data=sweep_data,
                session=sig.get("intraday", {}).get("session", ""),
                vol_regime=sig.get("intraday", {}).get("volatility_regime", "normal"),
            )

            # Override signal targets with production-grade targets
            if prod_targets.stop_loss > 0:
                sig["stop_loss"] = prod_targets.stop_loss
                sig["take_profit"] = prod_targets.take_profit_1  # TP1 as primary
                sig["sl_distance_pct"] = prod_targets.sl_distance_pct
                sig["tp_distance_pct"] = prod_targets.tp1_distance_pct
                sig["risk_reward"] = prod_targets.rr_1
                # Multi-target system
                sig["take_profit_1"] = prod_targets.take_profit_1
                sig["take_profit_2"] = prod_targets.take_profit_2
                sig["take_profit_3"] = prod_targets.take_profit_3
                sig["rr_1"] = prod_targets.rr_1
                sig["rr_2"] = prod_targets.rr_2
                sig["rr_3"] = prod_targets.rr_3
                sig["tp1_source"] = prod_targets.tp1_source
                sig["tp2_source"] = prod_targets.tp2_source
                sig["tp3_source"] = prod_targets.tp3_source
                sig["sl_source"] = prod_targets.sl_source
                sig["trailing_activation"] = prod_targets.trailing_activation
                sig["trailing_step_atr"] = prod_targets.trailing_step
                sig["breakeven_activation"] = prod_targets.breakeven_activation
                sig["tp1_exit_pct"] = prod_targets.tp1_exit_pct
                sig["tp2_exit_pct"] = prod_targets.tp2_exit_pct
                sig["tp3_exit_pct"] = prod_targets.tp3_exit_pct
                sig["target_quality"] = {
                    "sl_quality": prod_targets.sl_quality,
                    "tp_quality": prod_targets.tp_quality,
                    "data_coverage": prod_targets.data_coverage,
                }
                # Store S/R map for dashboard display
                sig["support_levels_map"] = prod_targets.support_levels
                sig["resistance_levels_map"] = prod_targets.resistance_levels
                sig["vp_poc"] = prod_targets.poc
                sig["vp_vah"] = prod_targets.vah
                sig["vp_val"] = prod_targets.val
                logger.info(
                    "🎯 PRODUCTION TARGETS: {} {} | SL={} ({}) TP1={} ({}) R:R={}/{} | Coverage={:.0f}%",
                    sig["side"], sym,
                    round(prod_targets.stop_loss, 4), prod_targets.sl_source,
                    round(prod_targets.take_profit_1, 4), prod_targets.tp1_source,
                    prod_targets.rr_1, prod_targets.rr_2,
                    prod_targets.data_coverage * 100,
                )

            # 5. Intraday Enhancement — adaptive SL/TP + quality scoring
            if config.intraday.enabled:
                sig = self.intraday_enhancer.enhance_signal(
                    signal=sig,
                    market_data=md,
                    orderflow=orderflow,
                    regime=regime,
                    liquidity_map=_liq_map,
                    cumulative_delta=cumulative_delta,
                    absorption=absorption_data,
                    liquidation=liq_data,
                    oi_data=oi_data,
                    funding_data=funding_data,
                )
                # Log quality tier for monitoring
                intraday_data = sig.get("intraday", {})
                if intraday_data.get("quality_tier") == "A":
                    logger.info("⭐ A-TIER INTRADAY: {} {} Quality={:.0f} Session={} Vol={}",
                                sig["side"], sym, intraday_data.get("quality_score", 0),
                                intraday_data.get("session", "?"),
                                intraday_data.get("volatility_regime", "?"))
                elif intraday_data.get("quality_score", 0) < 35:  # Phase 10: lowered from config.intraday.min_quality_score (45) to 35
                    logger.info("⚠️  {} {} — LOW QUALITY: Quality={:.0f} (min=35)",
                                 sig["side"], sym, intraday_data.get("quality_score", 0))
                    return  # Skip low quality intraday signals

            # 6. Elite Filter & Persistence
            # Apply 8-priority signal quality filter
            filter_pass, filter_reason = self.signal_filter.apply_all_filters(
                signal=sig, market_data=md, orderflow=orderflow,
                oi_data=oi_data, funding_data=funding_data,
                regime=regime, vol_map=self._vol_map,
            )
            if not filter_pass:
                logger.info("🚫 {} {} FILTERED: {}", sig.get("side","?"), sym, filter_reason)
                _pipeline_trace["failed_gate"] = "signal_filter"
                _pipeline_trace["filter_reason"] = filter_reason
                self._funnel["pipeline_traces"].append(_pipeline_trace)
                return

            # ═══════════════════════════════════════════════════════════════
            # FORENSIC: MINIMUM RR FILTER — Block RR < 1.5 (Phase 8: aligned with signal_filter)
            # ═══════════════════════════════════════════════════════════════
            # SQL PROOF: Winners avg RR=3.52, Losers avg RR=2.69
            # Production targets deliver rr_1=1.8x — threshold must be below that
            _rr = sig.get("risk_reward", 0) or 0
            if _rr < 1.5:
                self._funnel["filter"] += 1
                self._funnel["rejection_reasons"].append({
                    "symbol": sym, "reason": f"RR: {_rr:.2f} < 1.5 forensic threshold",
                    "time": time.time()
                })
                logger.info("🚫 {} {} RR_BLOCKED: {:.2f} < 1.5 (forensic)", sig.get("side", "?"), sym, _rr)
                _pipeline_trace["failed_gate"] = "rr_filter"
                self._funnel["pipeline_traces"].append(_pipeline_trace)
                # ── RR AUDIT: Record detailed rejection data ──
                try:
                    rr_audit = get_rr_audit()
                    rr_audit.record_rejection(
                        symbol=sym,
                        side=sig.get("side", "?"),
                        entry=sig.get("entry_price", sig.get("entry", 0)),
                        stop_loss=sig.get("stop_loss", sig.get("sl", 0)),
                        tp1=sig.get("take_profit", sig.get("take_profit_1", 0)),
                        tp2=sig.get("take_profit_2", 0),
                        tp3=sig.get("take_profit_3", 0),
                        atr_value=sig.get("atr", 0),
                        sl_atr_mult=sig.get("sl_atr_mult", 1.5),
                        tp1_rr_mult=sig.get("tp1_rr", 1.5),
                        session=sig.get("session", ""),
                        regime=sig.get("regime", ""),
                        confidence=confidence_100,
                        rr_required=1.5,
                        rejection_source="engine_rr_filter",
                        rejection_reason=f"RR {_rr:.2f} < 1.5 forensic threshold",
                    )
                except Exception as e:
                    logger.debug("RR_AUDIT: Failed to record engine rejection: {}", e)
                return

            # ── Populate sweep/mss/fvg scores in sig dict BEFORE checklist ──
            # These local variables were set at L1184/L1206/L1233 in the 7-pillar scoring.
            # They must be in sig dict so the checklist gate and DB write can see them.
            sig["sweep_score"] = sweep_score
            sig["mss_score"] = mss_score
            sig["fvg_score"] = fvg_score

            # ── INSTITUTIONAL CHECKLIST GATE (ALL-PASS required) ──
            # Replaces score-based elite gate with strict checklist:
            # ALL 10 criteria must pass. ANY fail = REJECT.
            checklist = self.checklist_gate.evaluate(
                sig=sig, regime=regime,
                sweep_setup=sig.get("sweep_setup"),
                orderflow=orderflow, cvd_data=cvd_data,
                oi_data=oi_data, funding_data=funding_data,
                absorption_data=absorption_data,
                smart_money_data=smart_money_data,
                market_data=md,
                sweep_analysis=sweep_analysis,
            )
            sig["checklist"] = checklist.to_dict()
            self._funnel["checklist_reached"] = self._funnel.get("checklist_reached", 0) + 1
            if not checklist.passed:
                self._funnel["filter"] += 1
                self._funnel["checklist_blocked"] = self._funnel.get("checklist_blocked", 0) + 1
                self.perf_tracker.adaptive_threshold.record_death("filter")
                self._funnel["rejection_reasons"].append({
                    "symbol": sym,
                    "reason": f"CHECKLIST: {checklist.score_str} passed — {'; '.join(checklist.failures[:3])}",
                    "confidence": confidence_100,
                    "time": time.time(),
                })
                _pipeline_trace["checklist"] = checklist.to_dict()
                _pipeline_trace["failed_gate"] = "checklist"
                self._funnel["pipeline_traces"].append(_pipeline_trace)
                logger.info("🚫 {} {} CHECKLIST_REJECTED: {} | skipped={} | {}",
                            sig.get("side", "?"), sym, checklist.score_str,
                            checklist.skipped,
                            '; '.join(checklist.failures[:3]))
                return
            self._funnel["checklist_passed"] = self._funnel.get("checklist_passed", 0) + 1
            self._funnel["generated"] = self._funnel.get("generated", 0) + 1
            _pipeline_trace["checklist"] = checklist.to_dict()
            _pipeline_trace["generated"] = True

            # ═══════════════════════════════════════════════════════════════
            # GATE 10 (v3.0): QUALITY SCORE — 5-category breakdown (max 100)
            # 1. Regime alignment:      0–25 pts
            # 2. Confluence (Gate 5):   0–20 pts
            # 3. Entry quality (Gate 7): 0–20 pts
            # 4. CVD confirmation (Gate 6): 0–20 pts
            # 5. SL quality (Gate 8):   0–15 pts
            # ═══════════════════════════════════════════════════════════════
            _v3_regime_pts = 0
            _v3_confluence_pts = sig.get("mtf_confluence_pts", 0)
            _v3_entry_pts = 0
            _v3_cvd_pts = sig.get("cvd_gate_pts", 0)
            _v3_sl_pts = 0

            # 1. Regime alignment (25pts max)
            _regime_type = regime.get("regime", "range") if regime else "range"
            _regime_side = sig.get("side", "LONG")
            _is_counter = (_regime_type == "trending_bull" and _regime_side == "SHORT") or \
                          (_regime_type == "trending_bear" and _regime_side == "LONG")
            if _regime_type in ("trending_bull", "trending_bear") and not _is_counter:
                _v3_regime_pts = 25  # Aligned with trending regime
            elif _regime_type in ("range", "ranging"):
                _v3_regime_pts = 15  # Ranging — allowed but lower
            elif _is_counter:
                _v3_regime_pts = 5   # Counter-trend — heavy penalty
            else:
                _v3_regime_pts = 10  # Unknown/volatile (shouldn't reach here)

            # 3. Entry quality (20pts max) — from production targets
            _sl_source = sig.get("sl_source", "")
            if "structural" in _sl_source or "liquidity_map" in _sl_source:
                _v3_entry_pts = 20  # At exact structural level
            elif "absorption" in _sl_source or "kline" in _sl_source:
                _v3_entry_pts = 14  # Within 0.3% of key level
            elif "atr" in _sl_source:
                _v3_entry_pts = 8   # EMA/ATR only (no structure)
            else:
                _v3_entry_pts = 10  # Default mid-range

            # 5. SL quality (15pts max) — from production targets
            _sl_quality = sig.get("target_quality", {}).get("sl_quality", 0)
            if _sl_quality >= 0.8:
                _v3_sl_pts = 15  # ATR-based, beyond structure, above floor
            elif _sl_quality >= 0.5:
                _v3_sl_pts = 8   # Borderline (at exact floor)
            else:
                _v3_sl_pts = 5   # Minimal quality

            # Total v3.0 quality score
            _v3_total = _v3_regime_pts + _v3_confluence_pts + _v3_entry_pts + _v3_cvd_pts + _v3_sl_pts

            # Dynamic threshold (Gate 10 v3.0)
            _session_floor = sig.get("session_quality_floor", 72)
            _budget_floor = _budget_floor if '_budget_floor' in dir() else 72
            _base_floor = max(_session_floor, _budget_floor)
            if _is_counter:
                _v3_floor = max(_base_floor, 87)  # Counter-trend premium
            elif _regime_type in ("range", "ranging"):
                _v3_floor = _base_floor + 6  # Ranging penalty
            else:
                _v3_floor = _base_floor

            # Store v3.0 scoring breakdown
            sig["v3_quality_score"] = _v3_total
            sig["v3_quality_floor"] = _v3_floor
            sig["v3_breakdown"] = {
                "regime": _v3_regime_pts,
                "confluence": _v3_confluence_pts,
                "entry": _v3_entry_pts,
                "cvd": _v3_cvd_pts,
                "sl": _v3_sl_pts,
            }
            sig["gates_passed"] = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]

            logger.info(
                "📊 GATE10_SCORE: {} {} — total={}/100 floor={} | regime={} conf={} entry={} cvd={} sl={}",
                sig.get("side", "?"), sym, _v3_total, _v3_floor,
                _v3_regime_pts, _v3_confluence_pts, _v3_entry_pts, _v3_cvd_pts, _v3_sl_pts,
            )

            # ── CRITICAL FIX: Tag signal as production_v2 after passing checklist ──
            # Without this, the LEGACY_BLOCK at L2380 blocks ALL signals because
            # sig.get("strategy_version") defaults to "legacy". 318 signals passed
            # all 15 SMC checks but 316 were killed by LEGACY_BLOCKED.
            sig["strategy_version"] = "production_v2"
            logger.info("✅ {} {} CHECKLIST_PASSED: {} | RR={:.1f} conf={:.1f}",
                        sig.get("side", "?"), sym, checklist.score_str,
                        sig.get("risk_reward", 0), confidence_100)

            # ═══════════════════════════════════════════════════════════════
            # v5 GATE 10: CONFIDENCE REALITY CHECK
            # Root Cause 3: AI overconfident on losing trades
            # CLOUDUSDT 94.2% confidence → -$56.04 (biggest loss)
            # MMTUSDT 93.8% confidence → -$20.39
            # High AI scores correlate with LOSSES, not wins in bear regime
            # ═══════════════════════════════════════════════════════════════
            _sig_side = sig.get("side", "LONG")
            _sig_regime = raw_regime if 'raw_regime' in dir() else "unknown"
            _size_override = None
            _size_reason = ""

            # Reality check 1: Very high confidence in bear regime → reduce size
            if _sig_regime == "trending_bear" and confidence_100 >= 90:
                _size_override = 0.50
                _size_reason = f"Bear overconfidence guard: conf={confidence_100:.0f}%"
                logger.info("⚠️  v5 REALITY_CHECK: {} {} — bear regime conf={:.0f}% ≥ 90 → force 0.5× size",
                            _sig_side, sym, confidence_100)

            # Reality check 2: Score ≥ 90 on SHORT → review flag
            if _sig_side == "SHORT" and sig.get("institutional_score", 0) >= 90:
                sig["review_flag"] = True
                if _size_override is None:
                    _size_override = 0.60
                    _size_reason = f"SHORT high score guard: score={sig['institutional_score']:.0f}"
                logger.info("⚠️  v5 REALITY_CHECK: {} {} — SHORT score={:.0f} ≥ 90 → review flag + 0.6×",
                            _sig_side, sym, sig["institutional_score"])

            # Reality check 3: If symbol has prior losses AND score ≥ 85 → reduce size
            _prior_losses = 0
            if hasattr(self, 'symbol_tracker'):
                _perf = self.symbol_tracker.get_performance(sym) if hasattr(self.symbol_tracker, 'get_performance') else None
                if _perf:
                    _prior_losses = _perf.get("recent_losses_7d", 0) if isinstance(_perf, dict) else 0
            if _prior_losses >= 1 and sig.get("institutional_score", 0) >= 85:
                if _size_override is None:
                    _size_override = 0.50
                elif _size_override > 0.50:
                    _size_override = 0.50
                _size_reason = f"{_size_reason} | Prior loss: {_prior_losses} in 7d" if _size_reason else f"Prior loss guard: {_prior_losses} in 7d"
                logger.info("⚠️  v5 REALITY_CHECK: {} {} — {} prior losses + score≥85 → 0.5×",
                            _sig_side, sym, _prior_losses)

            # Apply size override to signal
            if _size_override is not None:
                sig["size_override"] = _size_override
                sig["size_reason"] = _size_reason

            # ═══════════════════════════════════════════════════════════════
            # v5: BEAR REGIME SCORE CAP — max 86
            # Root cause: worst losses had scores 87-90 with 90%+ confidence
            # High scores in bear regime = suspicious, not confirmed edge
            # ═══════════════════════════════════════════════════════════════
            if _sig_regime == "trending_bear" and sig.get("v3_quality_score", 0) > 86:
                sig["v3_quality_score"] = 86
                sig["v3_quality_capped"] = True
                logger.info("🔒 v5 BEAR_SCORE_CAP: {} {} — quality {} → capped at 86", _sig_side, sym, sig.get("v3_quality_score", 0))

            # Apply session size multiplier (from v5 session filter)
            _session_size_mult = sig.get("session_size_mult", 1.0)
            if _size_override is not None:
                sig["size_override"] = _size_override  # Ignores session mult
                sig["size_reason"] = _size_reason

            if inst_result.get('institutional_score', 0) >= 70:  # Phase 12: lowered from 90 — signals passing 13/13 checklist have proven quality
                # ── Directional Cap: hard reject excess signals in over-represented direction ──
                if config.directional_bias.enabled:
                    if not self.directional_neutralizer.check_directional_cap(sig.get("side", "LONG")):
                        _ratios = self.directional_neutralizer.get_ratios()
                        _dominant = max(_ratios.get("long_ratio", 0), _ratios.get("short_ratio", 0))
                        logger.info("🚫 {} {} DIRECTIONAL CAP: rejected (dominant direction={:.0%})",
                                     sig.get("side", "?"), sym, _dominant)
                        _pipeline_trace["failed_gate"] = "directional_cap"
                        self._funnel["pipeline_traces"].append(_pipeline_trace)
                        return
                # ── Probability-based detector outputs ──
                _prob_inst = self.prob_inst.get_probability(sym)
                _prob_accum = self.prob_accum.get_probability(sym)
                _prob_whale = self.prob_whale.get_probability(sym)
                sig["institutional_probability"] = _prob_inst.get("institutional_probability", 0)
                sig["inst_prob_confidence"] = _prob_inst.get("confidence", 0)
                sig["accumulation_probability"] = _prob_accum.get("accumulation_probability", 0)
                sig["accum_prob_confidence"] = _prob_accum.get("confidence", 0)
                sig["whale_probability"] = _prob_whale.get("whale_probability", 0)
                sig["whale_prob_confidence"] = _prob_whale.get("confidence", 0)

                # ── Populate enriched fields for DB persistence ──
                _mark_price = self._mark_prices.get(sym, 0)
                sig["open_interest"] = (oi_data.get("current_oi", 0) * _mark_price) if oi_data and _mark_price else 0
                sig["oi_delta"] = oi_data.get("change_pct", 0) if oi_data else 0
                sig["funding_rate"] = funding_data.get("current_rate", 0) if funding_data else 0
                ef_analysis = self.exchange_flow.get_analysis(sym) if hasattr(self, 'exchange_flow') else None
                sig["exchange_flow"] = (ef_analysis or {}).get("flow_ratio", 0.5)
                sig["delta"] = (of or {}).get("delta", 0)
                sig["cvd"] = (cvd_data or {}).get("delta_momentum", 0)
                # Absorption: safe access to variables that may not be in scope
                _abs_total = (of or {}).get("large_buy_trades", 0) + (of or {}).get("large_sell_trades", 0)
                sig["absorption_score"] = 0.5  # default neutral
                # FIX F1-F3: Store actual calculated scores (not re-fetched values)
                sig["sweep_score"] = sweep_score  # Local variable from L1058-1077 (actual sweep analysis)
                sig["mss_score"] = mss_score      # Local variable from L1080-1101 (actual MSS analysis)
                sig["fvg_score"] = fvg_score      # Local variable from L1107-1118 (actual FVG analysis)
                # FIX F4: Store entry reason
                sig["entry_reason"] = "institutional_checklist_pass"
                sig["spoofing_score"] = (self.spoof_iceberg.get_analysis(sym) or {}).get("spoofing_score", 0) if hasattr(self.spoof_iceberg, 'get_analysis') else 0
                sig["id"] = await db.save_signal(sig)
                sig["status"] = "active"
                sig["created_at"] = time.time()
                # ── FORWARD TEST: Record all signals (live data only) ──
                try:
                    forward_test_db.record_signal({
                        "timestamp": time.time(),
                        "symbol": sym,
                        "side": sig.get("side", ""),
                        "confidence_100": confidence_100,
                        "institutional_score": sig.get("institutional_score", 0),
                        "regime": regime.get("regime", "unknown") if regime else "unknown",
                        "session": session_data.get("session", "unknown"),
                        "entry_price": sig.get("entry_price", 0),
                        "stop_loss": sig.get("stop_loss", 0),
                        "take_profit": sig.get("take_profit", 0),
                        "risk_reward": sig.get("risk_reward", 0),
                        "delta": sig.get("delta", 0),
                        "cvd": sig.get("cvd", 0),
                        "oi_delta": sig.get("oi_delta", 0),
                        "funding_rate": sig.get("funding_rate", 0),
                        "sweep_score": sig.get("sweep_score", 0),
                        "mss_score": sig.get("mss_score", 0),
                        "fvg_score": sig.get("fvg_score", 0),
                        "entry_reason": sig.get("entry_reason", ""),
                        "signal_status": "emitted",
                        "mtf_alignment": sig.get("mtf_alignment", 0),
                        "checklist_score": sig.get("checklist", {}).get("score", 0),
                        "regime_confidence": regime.get("confidence", 0) if regime else 0,
                        "volatility_score": sig.get("volatility_score", 50),
                        "quiet_market_blocked": 0,
                    })
                except Exception as e:
                    logger.debug("Forward test signal record failed: {}", e)
                # Record signal for confidence calibration
                try:
                    self.calibrator.record_signal(
                        symbol=sym,
                        side=sig.get("side", "LONG"),
                        regime=sig.get("regime", "unknown"),
                        raw_confidence=sig.get("confidence_100", 0),
                        institutional_score=sig.get("institutional_score", 0),
                    )
                except Exception:
                    pass
                # ── TRADE QUALITY SCORE ──
                try:
                    _regime_str = regime.get("confidence", 0.5) * 100 if regime else 50
                    _flow_str = (of or {}).get("imbalance", 0) * 100 if of else 50
                    _sm_str = sig.get("sm_score", 50)
                    _rr = sig.get("risk_reward", sig.get("risk_reward_ratio", 2.0))
                    quality_result = self.perf_tracker.quality_engine.compute_quality(
                        confidence=confidence_100,
                        risk_reward=_rr,
                        regime_strength=_regime_str,
                        flow_strength=_flow_str,
                        sm_score=_sm_str,
                    )
                    sig["trade_quality"] = quality_result["quality_score"]
                    sig["quality_grade"] = quality_result["grade"]
                    sig["quality_breakdown"] = quality_result
                    # Record for forward performance tracking
                    self.perf_tracker.forward_tracker.record_signal(
                        symbol=sym, side=sig.get("side", "LONG"),
                        regime=sig.get("regime", "unknown"),
                        confidence=confidence_100,
                        institutional_score=sig.get("institutional_score", 0),
                        sm_score=_sm_str,
                        trade_quality=quality_result["quality_score"],
                        quality_grade=quality_result["grade"],
                        risk_reward=_rr,
                        entry_price=sig.get("entry_price", 0),
                        stop_loss=sig.get("stop_loss", 0),
                        take_profit=sig.get("take_profit", 0),
                    )
                    self.perf_tracker.health_monitor.record_elite()
                    logger.info("📊 {} {} QUALITY: Grade={} Score={:.1f} (Conf={:.1f} RR={:.1f} SM={:.1f})",
                                sig.get("side", "?"), sym, quality_result["grade"],
                                quality_result["quality_score"], confidence_100, _rr, _sm_str)
                except Exception:
                    pass
                # Dedup: if signal for same symbol exists, keep only the higher-scored one
                _new_score = sig.get('institutional_score', 0) + sig.get('confidence', 0) * 10
                _existing_idx = next((i for i, s in enumerate(self.signals) if s.get('symbol') == sym), None)
                if _existing_idx is not None:
                    _old = self.signals[_existing_idx]
                    _old_score = _old.get('institutional_score', 0) + _old.get('confidence', 0) * 10
                    if _new_score > _old_score:
                        self.signals[_existing_idx] = sig
                        logger.debug("🔄 {} REPLACED existing signal (new score {:.0f} > old {:.0f})", sym, _new_score, _old_score)
                    else:
                        logger.debug("⏭️  {} skipped (existing score {:.0f} >= new {:.0f})", sym, _old_score, _new_score)
                else:
                    self.signals.append(sig)
                self._funnel["signals_emitted"] += 1
                # Pipeline trace: mark as emitted
                _pipeline_trace["emitted"] = True
                _pipeline_trace["generated"] = True
                self._funnel["pipeline_traces"].append(_pipeline_trace)
                # Track top scores for funnel display
                self._funnel["top_scores"].append({
                    "symbol": sym, "side": sig.get("side", "?"),
                    "confidence": sig.get("confidence_100", 0),
                    "institutional_score": sig.get("institutional_score", 0),
                })
                self._funnel["top_scores"] = sorted(
                    self._funnel["top_scores"], key=lambda x: x["confidence"], reverse=True
                )[:10]
                
                if isinstance(inst_result, dict):
                    classification = self.scoring_engine.get_tier(inst_result.get('institutional_score', 0))
                else:
                    classification = self.scoring_engine.get_tier(inst_result)
                
                if classification == "ELITE":
                    try:
                        await self.telegram.send_signal_alert(sig)
                    except Exception:
                        pass
                    logger.info("💎 ELITE SIGNAL: {} {} (Score: {} Grade: {})", sig["side"], sym, sig["institutional_score"], sig.get("signal_grade", "?"))
                else:
                    logger.info("📊 {} {} | Score: {} | Grade: {} | {}", 
                                sig["side"], sym, sig["institutional_score"], sig.get("signal_grade", "?"), classification)

                # 6. Entry Execution — run through risk engine and open position
                logger.info("🚀 EXECUTION_REACHED: {} {} — checking cooldown, risk, and opening position", sig.get("side","?"), sym)
                # Cycle limit: max new positions per scan cycle (prevents rapid-fire over-concentration)
                _cycle_max = getattr(config.risk, 'max_positions_per_cycle', 5)
                if self._cycle_positions_opened >= _cycle_max:
                    logger.info("⏱️  CYCLE_LIMIT: {} {} blocked — {} positions already opened this cycle (max {})", sig.get("side","?"), sym, self._cycle_positions_opened, _cycle_max)
                    return
                # Quality guard: after 3 positions, require 90%+ confidence
                if self._cycle_positions_opened >= 3 and confidence_100 < 90.0:
                    logger.info("🛡️  CYCLE_QUALITY_GUARD: {} {} — {} positions open, conf {:.1f}% < 90%", sig.get("side","?"), sym, self._cycle_positions_opened, confidence_100)
                    return
                # Cooldown: skip if symbol was recently closed (prevents re-entry whipsaws)
                # FIX: Loss trades get 1hr cooldown (was 5min for all) — prevents revenge re-entry
                # Evidence: BTWUSDT lost TWICE (-$10.47 + -$3.30) due to immediate re-entry
                _now = time.time()
                _cooldown_until = self._symbol_cooldowns.get(sym, 0)
                if _now < _cooldown_until:
                    _remaining = int(_cooldown_until - _now)
                    logger.debug("⏱️  COOLDOWN: {} skipped ({:.0f}s remaining)", sym, _remaining)
                    return
                # Skip if symbol already has an open position (prevent duplicates)
                if sym in self.risk._positions:
                    logger.debug("🔒  {} already has open position, skipping duplicate", sym)
                    return
                risk_check = await self.risk.check_signal(sig)
                _rc_side = sig.get("side", "?")
                _rc_allowed = risk_check.get("allowed", False)
                _rc_reason = risk_check.get("reason", "ok")
                if _rc_allowed:
                    logger.info("🎯 RISK_CHECK: {} {} → ALLOWED", _rc_side, sym)
                else:
                    logger.info("🚫 RISK_CHECK: {} {} → BLOCKED: {}", _rc_side, sym, _rc_reason)
                if risk_check.get("allowed"):
                    # ═══════════════════════════════════════════════════════════════
                    # FIX 4: PULLBACK CONFIRMATION — Entry is LIMIT at support/resistance
                    # The old MOMENTUM_BLOCKED just checked price within 0.5% of entry.
                    # This allowed entries at local TOPS (LONG) or BOTTOMS (SHORT).
                    # Now we require: EMA alignment + CVD absorption + quiet volume
                    # so entries happen on pullbacks, not at momentum peaks.
                    # ═══════════════════════════════════════════════════════════════
                    _entry_price = sig.get("entry_price", 0)
                    _current_price = self._price(sym)
                    _side = sig.get("side", "LONG")
                    _rejection = None

                    if _current_price and _entry_price:
                        _sd = self.symbol_data.get(sym, {})
                        _klines_5m = _sd.get("klines", {}).get("5m", [])
                        _klines_15m = _sd.get("klines", {}).get("15m", [])

                        # ── 1. EMA 20 filter: price must be on the right side ──
                        # LONG: price above EMA 20 (uptrend context)
                        # SHORT: price below EMA 20 (downtrend context)
                        _ema_ok = True
                        if len(_klines_5m) >= 25:
                            _closes = [c["close"] for c in _klines_5m[-25:]]
                            _ema20 = sum(_closes) / len(_closes)  # SMA-25 as EMA proxy
                            if _side == "LONG" and _current_price < _ema20:
                                _ema_ok = False
                                _rejection = f"EMA_BEARISH: price {_current_price:.4f} < EMA25 {_ema20:.4f}"
                            elif _side == "SHORT" and _current_price > _ema20:
                                _ema_ok = False
                                _rejection = f"EMA_BULLISH: price {_current_price:.4f} > EMA25 {_ema20:.4f}"

                        # ── 2. Pullback quality: not chasing a climax move ──
                        # Check that the last 5 candles aren't all in the signal direction
                        # (which would mean we're buying the top / selling the bottom)
                        _chase_block = False
                        if len(_klines_5m) >= 5:
                            _last5 = _klines_5m[-5:]
                            if _side == "LONG":
                                _all_up = all(c["close"] > c["open"] for c in _last5)
                                _last_move_pct = (_last5[-1]["close"] - _last5[0]["open"]) / _last5[0]["open"] * 100 if _last5[0]["open"] else 0
                                if _all_up and _last_move_pct > 1.0:
                                    _chase_block = True
                                    _rejection = f"CHASE_LONG: 5 consecutive green candles, +{_last_move_pct:.2f}% — buy the top?"
                            else:
                                _all_down = all(c["close"] < c["open"] for c in _last5)
                                _last_move_pct = (_last5[0]["open"] - _last5[-1]["close"]) / _last5[0]["open"] * 100 if _last5[0]["open"] else 0
                                if _all_down and _last_move_pct > 1.0:
                                    _chase_block = True
                                    _rejection = f"CHASE_SHORT: 5 consecutive red candles, -{_last_move_pct:.2f}% — sell the bottom?"

                        # ── 3. Volume check: pullback should be QUIET ──
                        # Volume below 1.5x average = healthy pullback
                        # Volume above 2x average = climax (don't enter)
                        _vol_ok = True
                        if len(_klines_5m) >= 20:
                            _vols = [c.get("volume", 0) for c in _klines_5m[-20:]]
                            _avg_vol = sum(_vols) / len(_vols) if _vols else 0
                            _cur_vol = _klines_5m[-1].get("volume", 0)
                            if _avg_vol > 0 and _cur_vol > _avg_vol * 2.0:
                                _vol_ok = False
                                _rejection = f"CLIMAX_VOLUME: vol={_cur_vol:.0f} > 2x avg={_avg_vol:.0f}"

                        # ── 4. CVD absorption filter (not a trigger — a filter) ──
                        # CVD should show buying absorption on LONG pullback (higher lows on CVD)
                        # CVD should show selling absorption on SHORT bounce (lower highs on CVD)
                        _cvd_ok = True
                        _of_data = sig.get("orderflow", {}) or {}
                        _cvd_val = _of_data.get("cvd", sig.get("cvd", 0))
                        if _side == "LONG" and _cvd_val < -1.0:
                            # Strong selling CVD during a LONG signal — bearish divergence
                            _cvd_ok = False
                            _rejection = f"CVD_ABSORPTION_FAIL: cvd={_cvd_val:.1f} (strong selling)"
                        elif _side == "SHORT" and _cvd_val > 1.0:
                            # Strong buying CVD during a SHORT signal — bullish divergence
                            _cvd_ok = False
                            _rejection = f"CVD_ABSORPTION_FAIL: cvd={_cvd_val:.1f} (strong buying)"

                        # ── Combine all checks ──
                        _all_pass = _ema_ok and not _chase_block and _vol_ok and _cvd_ok
                        if not _all_pass:
                            logger.info(
                                "🚫 PULLBACK_BLOCKED: {} {} — {}",
                                _side, sym, _rejection,
                            )
                            self._funnel["filter"] += 1
                            self._funnel["rejection_reasons"].append({
                                "symbol": sym,
                                "reason": f"PULLBACK: {_rejection}",
                                "confidence": _conf * 100,
                                "time": time.time(),
                            })
                            return

                    # ═══════════════════════════════════════════════════════════════
                    # P0 GATE: Block zero-confidence / unknown-regime trades
                    # Defense-in-depth: even if upstream missed the check, block here
                    # June 16 proof: HOMEUSDT, BABYUSDT, NAORISUSDT all had conf=0
                    # ═══════                    # ═══════════════════════════════════════════════════════════════
                    _conf = confidence_100 / 100.0 if 'confidence_100' in dir() else sig.get('confidence', 0)
                    _regime = regime.get('regime', 'unknown') if regime else 'unknown'
                    _inst = sig.get('institutional_score', 0)
                    if _conf < 0.85 or _regime in ('unknown', '') or _inst == 0:
                        logger.info(
                            "🚫 GATE_BLOCKED: {} {} — conf={:.1%} regime={} inst_score={}",
                            sym, sig.get('side', '?'), _conf, _regime, _inst,
                        )
                        self._funnel["filter"] += 1
                        self._funnel["rejection_reasons"].append({
                            "symbol": sym, "reason": f"GATE: conf={_conf:.1%} regime={_regime} inst={_inst}",
                            "confidence": _conf * 100, "time": time.time(),
                        })
                        return

                    # ═══════════════════════════════════════════════════════════════
                    # FIX D: Directional Exposure Limiter
                    # Prevent stacking same-direction positions in a rolling window
                    # June 16 proof: 4 SHORTs (USELESS, HAEDAL, POWER, ZEREBRO) opened
                    # in 2h — all hit SL. This blocks the 4th+ same-direction entry.
                    # ═══════════════════════════════════════════════════════════════
                    if config.directional_exposure.enabled:
                        _de_side = sig.get("side", "LONG")
                        _de_allowed, _de_reason = self.directional_exposure.check(
                            symbol=sym,
                            side=_de_side,
                            risk_positions=self.risk._positions,
                        )
                        if not _de_allowed:
                            logger.info(
                                "🚫 DIRECTIONAL_EXPOSURE: {} {} — {}",
                                _de_side, sym, _de_reason,
                            )
                            self._funnel["filter"] += 1
                            self._funnel["rejection_reasons"].append({
                                "symbol": sym,
                                "reason": f"DIR_EXPOSURE: {_de_reason}",
                                "confidence": _conf * 100,
                                "time": time.time(),
                            })
                            _pipeline_trace["failed_gate"] = "directional_exposure"
                            self._funnel["pipeline_traces"].append(_pipeline_trace)
                            return

                    # ═══════════════════════════════════════════════════════════════
                    # SAFETY NET: Reject trades with missing SL/TP
                    # June 16 proof: BCHUSDT (SL=0, TP=0) opened and lost $1.21
                    # ═══════════════════════════════════════════════════════════════
                    _sl = sig.get('stop_loss', 0)
                    _tp = sig.get('take_profit', 0)
                    _tp2 = sig.get('take_profit_2', 0)
                    _tp3 = sig.get('take_profit_3', 0)
                    if _sl == 0 or _tp == 0:
                        logger.info(
                            "🚫 SL_TP_BLOCKED: {} {} — SL={} TP={}",
                            sym, sig.get('side', '?'), _sl, _tp,
                        )
                        self._funnel["filter"] += 1
                        self._funnel["rejection_reasons"].append({
                            "symbol": sym, "reason": f"SL_TP: SL={_sl} TP={_tp}",
                            "confidence": _conf * 100, "time": time.time(),
                        })
                        return

                    # ═══════════════════════════════════════════════════════════════
                    # GATE 9 (v3.0): TRIPLE TP MANDATORY — ALL three TPs must be > 0
                    # Zero-value TP2 or TP3 = rejected signal (prevents partial execution)
                    # ═══════════════════════════════════════════════════════════════
                    if _tp2 <= 0 or _tp3 <= 0:
                        logger.info(
                            "🚫 GATE9_TP_BLOCKED: {} {} — TP1={} TP2={} TP3={}",
                            sym, sig.get('side', '?'), _tp, _tp2, _tp3,
                        )
                        self._funnel["filter"] += 1
                        self._funnel["rejection_reasons"].append({
                            "symbol": sym, "reason": f"GATE9_TRIPLE_TP: TP2={_tp2} TP3={_tp3} (must be >0)",
                            "confidence": _conf * 100, "time": time.time(),
                        })
                        return

                    # Also reject duplicate TP values (TP1 == TP2 == TP3 is invalid)
                    if _tp > 0 and _tp2 > 0 and abs(_tp - _tp2) < _tp * 0.001:
                        logger.info(
                            "🚫 GATE9_TP_DUPLICATE: {} {} — TP1={} TP2={}",
                            sym, sig.get('side', '?'), _tp, _tp2,
                        )
                        self._funnel["filter"] += 1
                        self._funnel["rejection_reasons"].append({
                            "symbol": sym, "reason": f"GATE9_TP_DUPLICATE: TP1={_tp} TP2={_tp2}",
                            "confidence": _conf * 100, "time": time.time(),
                        })
                        return

                    qty = risk_check["quantity"]

                    # ═══════════════════════════════════════════════════════════════
                    # v5 GATE 10: Apply size_override from confidence reality check
                    # If overconfidence guard triggered, use override instead of multipliers
                    # ═══════════════════════════════════════════════════════════════
                    _size_override = sig.get("size_override")
                    if _size_override is not None and _size_override > 0:
                        qty = risk_check.get("base_quantity", qty) * _size_override
                        logger.info("📊 v5 SIZE_OVERRIDE: {} {} — qty={:.6f} (override={:.2f}, reason={})",
                                    sig.get('side', '?'), sym, qty, _size_override, sig.get("size_reason", ""))
                    else:
                        # ═══════════════════════════════════════════════════════════════
                        # PHASE 2: Apply session size multiplier to final quantity
                        # ═══════════════════════════════════════════════════════════════
                        _session_mult = sig.get("session_size_mult", 1.0)
                        if _session_mult != 1.0:
                            qty = qty * _session_mult
                            logger.info("📊 SESSION_SIZING: {} {} — qty scaled by {:.2f}x (session mult)", sig.get('side', '?'), sym, _session_mult)

                    # ═══════════════════════════════════════════════════════════════
                    # P0 GHOST TRADE GUARD: Block qty=0 positions
                    # _confidence_sizing_mult() returns 0 for inst_score < 85,
                    # but quality gate allows score >= 70. The 15-point gap created
                    # ghost trades that consumed position slots, triggered cooldowns,
                    # and wasted cycle budget. 50% of today's entries were ghost trades.
                    # ═══════════════════════════════════════════════════════════════
                    if qty <= 0:
                        logger.info(
                            "🚫 GHOST_BLOCKED: {} {} — qty={} (inst_score={}) sizing returned zero",
                            sym, sig.get('side', '?'), qty, sig.get('institutional_score', 0),
                        )
                        self._funnel["filter"] += 1
                        self._funnel["rejection_reasons"].append({
                            "symbol": sym,
                            "reason": f"GHOST_TRADE: qty={qty} inst_score={sig.get('institutional_score', 0)}",
                            "confidence": _conf * 100,
                            "time": time.time(),
                        })
                        return

                    entry = sig["entry_price"]
                    sl = sig.get("stop_loss", 0)
                    tp = sig.get("take_profit", 0)
                    side = sig.get("side", "LONG")
                    leverage = max(1, min(int(config.risk.max_leverage), 10))
                    risk_dist = abs(entry - sl) if sl else entry * 0.02
                    fees = entry * qty * 0.0004  # taker fee estimate

                    # ═══════════════════════════════════════════════════════════════
                    # CRITICAL: Block ALL legacy engine trades
                    # 99.7% of historical losses came from legacy/inst_v1/inst_v2/current engines
                    # Only allow production_v2 strategy to open positions
                    # ═══════════════════════════════════════════════════════════════
                    _strat = sig.get("strategy_version", "legacy")
                    if _strat not in ("production_v2", "ema_v5"):
                        logger.info(
                            "🚫 LEGACY_BLOCKED: {} {} — strategy={}, ONLY production_v2 allowed",
                            sym, sig.get('side', '?'), _strat,
                        )
                        self._funnel["filter"] += 1
                        self._funnel["rejection_reasons"].append({
                            "symbol": sym, "reason": f"LEGACY_BLOCKED: strategy={_strat}",
                            "confidence": _conf * 100, "time": time.time(),
                        })
                        return

                    pos_id = await safe_db_open_position(
                        db,
                        signal_id=sig["id"],
                        symbol=sym,
                        side=side,
                        entry_price=entry,
                        quantity=qty,
                        leverage=leverage,
                        stop_loss=sl,
                        take_profit=tp,
                        fees=fees,
                        # FIX 2: Pass TP2/TP3 to DB for persistence
                        take_profit_2=sig.get("take_profit_2", 0),
                        take_profit_3=sig.get("take_profit_3", 0),
                        # FIX #6: Pass institutional data
                        confidence=confidence_100 / 100.0,
                        regime=regime.get("regime", "unknown") if regime else "unknown",
                        institutional_score=sig.get("institutional_score", 0),
                        risk_reward=sig.get("risk_reward", 0),
                        session=session_data.get("session", "unknown"),
                        strategy_version=sig.get("strategy_version", "production_v2"),
                        # FIX #4/7: Quiet market + persistence fields
                        planned_rr=sig.get("risk_reward", 0),
                        volatility_score=sig.get("volatility_score", 50),
                        quiet_market_blocked=0,
                        # FIX: Pass MSS and FVG scores
                        mss_score=sig.get("mss_score", 0),
                        fvg_score=sig.get("fvg_score", 0),
                    )
                    # ── FORWARD TEST: Record trade entry ──
                    try:
                        forward_test_db.record_trade({
                            "signal_id": sig.get("ft_signal_id"),
                            "timestamp": time.time(),
                            "symbol": sym,
                            "side": side,
                            "entry_price": entry,
                            "entry_time": time.time(),
                            "exit_price": 0,
                            "exit_time": 0,
                            "exit_reason": "",
                            "pnl": 0,
                            "fees": fees,
                            "net_pnl": -fees,
                            "stop_loss": sl,
                            "take_profit": tp,
                            "planned_rr": sig.get("risk_reward", 0),
                            "realized_r": 0,
                            "hold_minutes": 0,
                            "mae_pct": 0,
                            "mfe_pct": 0,
                            "regime": regime.get("regime", "unknown") if regime else "unknown",
                            "session": session_data.get("session", "unknown"),
                            "confidence_100": confidence_100,
                            "institutional_score": sig.get("institutional_score", 0),
                            "sweep_score": sig.get("sweep_score", 0),
                            "mss_score": sig.get("mss_score", 0),
                            "fvg_score": sig.get("fvg_score", 0),
                            "delta": sig.get("delta", 0),
                            "cvd": sig.get("cvd", 0),
                            "oi_delta": sig.get("oi_delta", 0),
                            "funding_rate": sig.get("funding_rate", 0),
                        })
                    except Exception as e:
                        logger.debug("Forward test trade record failed: {}", e)
                    # Track in risk engine for exit monitoring
                    # ═══════════════════════════════════════════════════════════════
                    # FIX 2: Store TP2/TP3 + multi-target tracking fields
                    # The 3-tier profit system (TP1=1.5R, TP2=3R, TP3=5R) was dead code
                    # because take_profit_2/3 were never stored in the position dict.
                    # Risk engine checked for them but always found 0 → never triggered.
                    # ═══════════════════════════════════════════════════════════════
                    self.risk._positions[sym] = {
                        "id": pos_id,
                        "signal_id": sig["id"],
                        "symbol": sym,
                        "side": side,
                        "entry_price": entry,
                        "quantity": qty,
                        "leverage": leverage,
                        "stop_loss": sl,
                        "take_profit": tp,
                        # ── Multi-target TP system (Fix 2) ──
                        "take_profit_1": tp,
                        "take_profit_2": sig.get("take_profit_2", 0),
                        "take_profit_3": sig.get("take_profit_3", 0),
                        "tp2_source": sig.get("tp2_source", ""),
                        "tp3_source": sig.get("tp3_source", ""),
                        "current_tp_index": 1,   # tracks which TP level is active (1→2→3)
                        "_tp1_hit": False,
                        "_tp2_hit": False,
                        # ── Exit percentages from production targets ──
                        "tp1_exit_pct": sig.get("tp1_exit_pct", 0.40),
                        "tp2_exit_pct": sig.get("tp2_exit_pct", 0.40),
                        "tp3_exit_pct": sig.get("tp3_exit_pct", 0.30),
                        "opened_at": time.time(),
                        # Metadata for adaptive learning
                        "confidence": sig.get("confidence", 0),
                        "institutional_score": sig.get("institutional_score", 0),
                        "regime": sig.get("regime", "unknown"),
                        "trend_score": sig.get("trend_score", 0),
                        "risk_reward": sig.get("risk_reward", 0),
                        "confirmation_factors": sig.get("confirmation_factors", []),
                        "score_breakdown": sig.get("score_breakdown", {}),
                    }
                    # Register in trade lifecycle engines
                    signal_price = sig.get("entry_price", entry)
                    self.trade_engine.open_position(
                        symbol=sym, side=side, entry_price=entry,
                        signal_price=signal_price, quantity=qty,
                        leverage=leverage, stop_loss=sl, take_profit=tp,
                        signal_time=sig.get("created_at", time.time()),
                    )
                    self.tp_sl_engine.register_position(
                        symbol=sym, side=side, entry_price=entry,
                        stop_loss=sl, take_profit=tp, quantity=qty,
                    )
                    self.entry_exit_engine.record_signal(sym, signal_price, side)
                    self.entry_exit_engine.record_fill(sym, entry, is_entry=True)

                    # ═══════════════════════════════════════════════════════
                    # FIX #1: REGISTER IN TRADE LIFECYCLE ENGINE
                    # ═══════════════════════════════════════════════════════
                    self.lifecycle.register_position(
                        symbol=sym, side=side, entry_price=entry,
                        stop_loss=sl, take_profit=tp,
                        risk_reward=sig.get("risk_reward", 3.0),
                    )

                    # open_count is now a property derived from _positions
                    self._cycle_positions_opened += 1
                    # PHASE 2: Record signal in daily budget counter
                    daily_budget.record_signal()
                    logger.info(
                        "📈 POSITION OPENED: {} {} qty={} entry={} sl={} tp={} (Score: {}) [cycle: {}/{}]",
                        side, sym, qty, entry, sl, tp, sig["institutional_score"],
                        self._cycle_positions_opened, getattr(config.risk, 'max_positions_per_cycle', 2),
                    )
                    # Record in directional exposure limiter (for rolling window tracking)
                    if config.directional_exposure.enabled:
                        self.directional_exposure.record_open(sym, side)
                    # ── Telegram entry alert ──
                    await self.telegram.send_position_opened({
                        "symbol": sym,
                        "side": side,
                        "entry_price": entry,
                        "quantity": qty,
                        "leverage": leverage,
                        "stop_loss": sl,
                        "take_profit": tp,
                        "score": sig.get("institutional_score", 0),
                        "opened_at": time.time(),
                    })
                else:
                    logger.debug(
                        "⚠️  SIGNAL REJECTED BY RISK: {} {} reason={}",
                        sig.get("side", ""), sym, risk_check.get("reason", "unknown"),
                    )
            else:
                logger.info("🚫 {} {} — REJECTED score={:.1f} (min=49.5)",
                            sig.get("side", "?"), sym, sig.get("institutional_score", 0))
        except Exception as e:
            import traceback
            logger.error(f"SCAN ERROR {sym}: {type(e).__name__}: {e}\n{traceback.format_exc()}")

    def _get_mtf_alignment(self, symbol: str) -> Dict:
        """Institutional Multi-timeframe alignment using regime engine's 5-TF analysis."""
        regime_data = self.regime.get_regime(symbol)

        if regime_data and regime_data.get("tf_regimes"):
            tf_regimes = regime_data["tf_regimes"]
            tf_confs = regime_data.get("tf_confidences", {})
            alignment = regime_data.get("alignment_score", 0)
            composite = regime_data.get("regime", "range")
            conf_pct = regime_data.get("regime_confidence_pct", 50)

            # Count directional regimes
            bull_count = sum(1 for r in tf_regimes.values() if r == "trending_bull")
            bear_count = sum(1 for r in tf_regimes.values() if r == "trending_bear")
            total_tfs = len(tf_regimes)

            # Strong alignment: 4+ TFs agree on direction
            if bull_count >= 4:
                return {"alignment_score": bull_count, "direction": "up",
                        "regime_alignment": True, "regime_confidence": conf_pct,
                        "composite_regime": composite}
            elif bear_count >= 4:
                return {"alignment_score": bear_count, "direction": "down",
                        "regime_alignment": True, "regime_confidence": conf_pct,
                        "composite_regime": composite}
            else:
                max_count = max(bull_count, bear_count)
                direction = "up" if bull_count > bear_count else ("down" if bear_count > bull_count else "neutral")
                return {"alignment_score": max_count, "direction": direction,
                        "regime_alignment": False, "regime_confidence": conf_pct,
                        "composite_regime": composite}

        # Fallback: basic kline-based alignment
        md = self.symbol_data.get(symbol, {})
        klines = md.get("klines", {})
        result = {"alignment_score": 0, "direction": "NEUTRAL"}
        regimes = []

        timeframes = ["1m", "5m", "15m", "1h", "4h"]
        for tf in timeframes:
            tf_klines = klines.get(tf, [])
            if len(tf_klines) >= 5:
                dir = "LONG" if tf_klines[-1]["close"] > tf_klines[-5]["close"] else "SHORT"
                regimes.append(dir)

        if regimes:
            long_count = regimes.count("LONG")
            short_count = regimes.count("SHORT")

            if long_count >= 4:
                result = {"alignment_score": long_count, "direction": "up"}
            elif short_count >= 4:
                result = {"alignment_score": short_count, "direction": "down"}
            else:
                result = {"alignment_score": max(long_count, short_count), "direction": "neutral"}

        return result

    # ── Ranking loop ─────────────────────────────────────────────

    async def _rank_loop(self) -> None:
        top = self.ranking.rank_signals(self.signals)
        if top:
            await self.telegram.send_ranking_update(top[:10])
        await asyncio.sleep(60)

    # ── Risk loop ────────────────────────────────────────────────

    async def _risk_loop(self) -> None:
        # ── Sync bridge every 1 second (near-zero lag) ──
        now = time.time()
        if now - self._last_bridge_sync >= 1:
            self._sync_bridge()
            self._last_bridge_sync = now

        # ── Shadow tracker: update candidate outcomes every 60 seconds ──
        if now - getattr(self, '_last_shadow_update', 0) >= 60:
            self._last_shadow_update = now
            try:
                from scanner.ema_v5.shadow_confidence_tracker import get_shadow_tracker
                _shadow = get_shadow_tracker()
                # Build price map from ticker data
                _price_map = {sym: data.get("price", 0) for sym, data in self._ticker_data.items() if data.get("price", 0) > 0}
                _shadow.update_outcomes(_price_map)
            except Exception:
                pass

        positions = await db.get_open_positions()
        if not positions:
            await asyncio.sleep(1)
            return
        for pos in positions:
          try:
            if not pos or not isinstance(pos, dict):
                continue
            sym = pos.get("symbol", "")
            price = self._price(sym)
            # FIX: If symbol fell out of scan list, use bridge data as fallback
            # so MFE tracking and trailing stops still work for open positions.
            if price is None or price <= 0:
                try:
                    from pathlib import Path as _Path
                    _bridge_file = _Path(__file__).resolve().parent.parent / "data" / "bridge" / "market_data.json"
                    if _bridge_file.exists():
                        import json as _json
                        _md = _json.loads(_bridge_file.read_text())
                        for _row in _md.get("rows", []):
                            if _row.get("symbol") == sym:
                                price = _row.get("price", 0)
                                break
                except Exception:
                    pass
            # LAST RESORT: Direct Binance REST API for open position symbols
            # This ensures position monitoring NEVER skips a symbol due to missing price
            if price is None or price <= 0:
                price = await self._fetch_price_rest(sym)
                if price and price > 0:
                    logger.debug("🔄 REST fallback price for {}: {}", sym, price)
            if price is None or price <= 0:
                logger.warning("⚠️ RISK_LOOP: No price for {} — skipping position monitoring!", sym)
                continue
            # Update trade lifecycle engines with current price (for MAE/MFE)
            sym = pos["symbol"]
            self.trade_engine.update_price(sym, price)
            self.tp_sl_engine.update(sym, price)
            self.entry_exit_engine.record_price_snapshot(sym, price)
            # ═══════════════════════════════════════════════════════════
            # PHASE 2: LIFECYCLE ENFORCEMENT — minimum hold before exits
            # ═══════════════════════════════════════════════════════════
            lifecycle_status = self.lifecycle.update_price(sym, price)
            lifecycle_action = lifecycle_status.get("action", "none")

            # ALWAYS compute MFE/peak — even during lifecycle hold — so
            # trailing stop state is tracked from the first scan cycle.
            close, reason = self.risk.check_exit_conditions(pos, price)

            # If lifecycle says HOLD, block discretionary exits (only hard SL allowed)
            if lifecycle_action == "hold" and close and reason != "stop_loss":
                close = False  # Block exit — trade hasn't reached min hold time
            # ── P0 FIX + FIX 5: Persist trailing stop peak + MFE% to DB every cycle ──
            # This ensures _highest_pnl and _mfe_pct survive engine restarts.
            # Without this, peak resets to 0R on restart, causing time_exit_6h
            # to fire on trades that had significant unrealized profit (e.g. SPACEUSDT).
            _peak = self.risk._highest_pnl.get(sym, 0)
            _mfe = self.risk._mfe_pct.get(sym, 0)
            _pos_id = pos.get("id", 0)
            # FIX: Always persist — even when peak=0 and mfe=0 — so positions
            # get tracked from the first scan cycle after restart.
            if _pos_id:
                try:
                    await db.update_position_peak(_pos_id, _peak, _mfe)
                except Exception:
                    pass  # Non-critical — don't block trading loop
                # Also mark JSON state dirty for autosave (belt + suspenders)
                self.state.mark_dirty()
            if close:
                sym = pos["symbol"]
                # ═══════════════════════════════════════════════════════════════
                # FIX 2b: Match actual exit reasons from risk engine
                # Risk engine returns "take_profit_1", "take_profit_2", "take_profit_3"
                # TP1 and TP2 are partial exits (40%/40%), TP3 is full close (20%)
                # Previously checked "partial_profit" prefix — never matched, so
                # ALL TP hits did full close, defeating the 3-tier profit system.
                # ═══════════════════════════════════════════════════════════════
                is_partial = reason in ("take_profit_1", "take_profit_2")

                if is_partial:
                    # ── Partial exit: close tier-appropriate % of position ──
                    orig_qty = pos.get("quantity", 0)
                    if reason == "take_profit_1":
                        exit_pct = pos.get("tp1_exit_pct", 0.40)  # default 40%
                    else:  # take_profit_2
                        exit_pct = pos.get("tp2_exit_pct", 0.40)  # default 40%
                    partial_qty = round(orig_qty * exit_pct, 6)
                    remaining_qty = round(orig_qty - partial_qty, 6)
                    if partial_qty <= 0 or remaining_qty <= 0:
                        # Edge case: position too small, close fully
                        is_partial = False
                    else:
                        # PnL on the partial slice
                        pos_copy = dict(pos)
                        pos_copy["_exit_qty"] = partial_qty
                        partial_pnl = self.risk.calculate_pnl(pos_copy, price)
                        # Update DB: reduce position quantity
                        await db.close_position(pos["id"], partial_pnl, partial=True, remaining_qty=remaining_qty)
                        # Update in-memory position quantity
                        if sym in self.risk._positions:
                            self.risk._positions[sym]["quantity"] = remaining_qty

                        # ═══════════════════════════════════════════════════════
                        # FIX 3: SL TRAILING AFTER PARTIAL EXITS
                        # TP1 hit → move SL to breakeven (entry) — risk-free trade
                        # TP2 hit → trail SL to TP1 — locked in 1.5R on remainder
                        # This ensures the remaining position can never lose money
                        # after TP1, and locks in 1.5R after TP2.
                        # Note: risk engine already moves SL to entry±fee at 1.0R,
                        # so TP1 trail is mostly a DB persistence + safety net.
                        # TP2 trail to TP1 is the genuinely new behavior.
                        # ═══════════════════════════════════════════════════════
                        old_sl = self.risk._positions[sym].get("stop_loss", 0)
                        entry = self.risk._positions[sym].get("entry_price", 0)
                        tp1_level = self.risk._positions[sym].get("take_profit_1", 0)
                        side = self.risk._positions[sym].get("side", "LONG")
                        if reason == "take_profit_1":
                            new_sl = entry  # Breakeven
                            sl_source = "breakeven_at_tp1"
                        elif reason == "take_profit_2" and tp1_level:
                            new_sl = tp1_level  # Trail to TP1
                            sl_source = "trail_to_tp1_at_tp2"
                        else:
                            new_sl = old_sl
                            sl_source = None

                        # Only tighten SL — never widen it
                        if sl_source and new_sl and new_sl != old_sl:
                            _tighter = (side == "LONG" and new_sl > old_sl) or \
                                       (side == "SHORT" and new_sl < old_sl)
                            if _tighter:
                                self.risk._positions[sym]["stop_loss"] = new_sl
                                try:
                                    await db.update_position_sl(pos["id"], new_sl)
                                except Exception:
                                    pass
                                logger.info(
                                    "🔒 {} SL_TRAIL: {} {} → {} ({})",
                                    pos.get("side", ""), sym, old_sl, new_sl, sl_source,
                                )

                        # Update balance with partial PnL
                        self.risk.balance += partial_pnl
                        self.risk.daily_pnl += partial_pnl
                        if self.risk.balance > self.risk.peak:
                            self.risk.peak = self.risk.balance
                        logger.info(
                            "💰 {} PARTIAL EXIT: {} qty={}/{} PnL=${:.2f} ({} → let {} ride)",
                            pos.get("side", ""), sym, partial_qty, orig_qty,
                            partial_pnl, reason, remaining_qty,
                        )
                        # Record for analytics (with metadata for adaptive learning)
                        self._closed_trades.append({
                            "symbol": sym,
                            "side": pos.get("side", "LONG"),
                            "entry_price": pos.get("entry_price", 0),
                            "exit_price": price,
                            "pnl": round(partial_pnl, 2),
                            "exit_reason": reason,
                            "quantity": partial_qty,
                            "partial": True,
                            "timestamp": time.time(),
                            # Metadata for adaptive learning
                            "confidence": pos.get("confidence", 0),
                            "institutional_score": pos.get("institutional_score", 0),
                            "regime": pos.get("regime", "unknown"),
                            "trend_score": pos.get("trend_score", 0),
                            "risk_reward": pos.get("risk_reward", 0),
                            "confirmation_factors": pos.get("confirmation_factors", []),
                        })
                        if len(self._closed_trades) > _MAX_CLOSED_TRADES:
                            self._closed_trades = self._closed_trades[-_MAX_CLOSED_TRADES:]
                        continue  # Don't close full position

                # ── Full exit ──
                pnl = self.risk.calculate_pnl(pos, price)

                # ═══════════════════════════════════════════════════════
                # FIX #1: Get lifecycle data before closing
                # ═══════════════════════════════════════════════════════
                _lc_state = self.lifecycle._positions.get(sym)
                _hold_min = (_lc_state.hold_minutes if _lc_state else 0) or 0
                _mae = (_lc_state.mae_pct if _lc_state else 0) or 0
                _mfe = (_lc_state.mfe_pct if _lc_state else 0) or 0

                # Compute realized R-multiple
                _entry = pos.get("entry_price", 0)
                _sl = pos.get("stop_loss", 0)
                _qty = pos.get("quantity", 1)
                _risk_dist = abs(_entry - _sl) if _sl and _entry else 0
                _realized_r = pnl / (_risk_dist * _qty) if _risk_dist and _qty else 0
                _realized_r = round(_realized_r, 2) if _realized_r != 0 else 0

                await db.close_position(
                    pos["id"], pnl,
                    hold_minutes=round(_hold_min, 1),
                    mae_pct=round(_mae, 4),
                    mfe_pct=round(_mfe, 4),
                    exit_reason=reason,
                    realized_r=_realized_r,
                )
                # ── FORWARD TEST: Update trade exit ──
                try:
                    # Find the most recent forward_test trade for this symbol
                    _ft_trades = forward_test_db.query(
                        "SELECT id FROM forward_trades WHERE symbol=? AND exit_reason='' ORDER BY id DESC LIMIT 1",
                        (sym,)
                    )
                    if _ft_trades:
                        _ft_id = _ft_trades[0]["id"]
                        risk_dist_close = abs(pos.get("entry_price", 0) - pos.get("stop_loss", 0)) if pos.get("stop_loss") else 0
                        realized_r = pnl / (risk_dist_close * pos.get("quantity", 1)) if risk_dist_close and pos.get("quantity") else 0
                        forward_test_db.query(
                            """UPDATE forward_trades SET 
                                exit_price=?, exit_time=?, exit_reason=?,
                                net_pnl=?, hold_minutes=?, mae_pct=?, mfe_pct=?,
                                realized_r=?, outcome=?
                               WHERE id=?""",
                            (price, time.time(), reason,
                             round(pnl, 2), round(_hold_min, 1), round(_mae, 4), round(_mfe, 4),
                             round(realized_r, 2), "win" if pnl > 0 else "loss",
                             _ft_id)
                        )
                except Exception as e:
                    logger.debug("Forward test trade update failed: {}", e)
                # Update risk engine state — balance, daily PnL, position count
                self.risk.balance += pnl
                self.risk.daily_pnl += pnl
                if self.risk.balance > self.risk.peak:
                    self.risk.peak = self.risk.balance
                # open_count is now a property derived from _positions
                # Remove from in-memory position tracking
                if sym in self.risk._positions:
                    del self.risk._positions[sym]
                # Clean up trailing stop state
                self.risk.cleanup_position_state(sym)
                # ═══════════════════════════════════════════════════════
                # FIX 13: Tiered cooldown — escalation for large losses
                # ═══════════════════════════════════════════════════════
                # Tiny loss (<0.5%): no cooldown
                # Small loss (<1.5%): 45min (existing behavior)
                # Medium loss (<3%): 2 hours
                # Large loss (<5%): 4 hours
                # Catastrophic loss (≥5%): 24 hours
                _balance = self.risk.balance if self.risk.balance > 0 else 1000
                if pnl <= 0:
                    _pnl_pct = abs(pnl) / _balance * 100
                    if _pnl_pct < 0.5:
                        _cooldown_sec = 0        # Tiny loss — no cooldown
                    elif _pnl_pct < 1.5:
                        _cooldown_sec = 2700     # 45 min
                    elif _pnl_pct < 3.0:
                        _cooldown_sec = 7200     # 2 hours
                    elif _pnl_pct < 5.0:
                        _cooldown_sec = 14400    # 4 hours
                    else:
                        _cooldown_sec = 86400    # 24 hours — catastrophic
                else:
                    _cooldown_sec = 300          # Win: 5min cooldown
                self._symbol_cooldowns[sym] = time.time() + _cooldown_sec
                if pnl <= 0:
                    # Cross-symbol cooldown scales with loss severity
                    _cross_sec = min(_cooldown_sec // 3, 3600)  # 1/3 of same-symbol, max 1hr
                    for _csym in list(self.risk._positions.keys()):
                        if _csym != sym:
                            self._symbol_cooldowns[_csym] = max(
                                self._symbol_cooldowns.get(_csym, 0),
                                time.time() + _cross_sec
                            )
                    _tier_name = {0: "none", 2700: "45min", 7200: "2hr", 14400: "4hr", 86400: "24hr"}.get(_cooldown_sec, f"{_cooldown_sec}s")
                    logger.info("⏱️  LOSS_COOLDOWN: {} — same-symbol {} ({:.1f}% loss=${:.2f}), cross-symbol {}min", sym, _tier_name, _pnl_pct, pnl, _cross_sec // 60)
                # Record outcome for adaptive scoring
                if hasattr(self.scorer, 'record_outcome'):
                    self.scorer.record_outcome(pnl > 0)
                # Record outcome for confidence calibration
                try:
                    risk_dist = abs(entry - sl) if sl and entry else 0
                    r_mult = pnl / (risk_dist * qty) if risk_dist and qty else 0
                    r_mult = float(r_mult) if np.isfinite(r_mult) else 0
                    self.calibrator.record_outcome(
                        symbol=sym,
                        side=pos.get("side", "LONG"),
                        pnl=pnl,
                        r_multiple=r_mult,
                    )
                    # Record outcome for forward performance tracking
                    self.perf_tracker.forward_tracker.record_outcome(
                        symbol=sym,
                        side=pos.get("side", "LONG"),
                        pnl=pnl,
                        r_multiple=r_mult,
                    )
                except Exception:
                    pass
                # Get enriched trade data from lifecycle engines
                te_record = self.trade_engine.close_position(sym, price, reason)
                exe_report = self.entry_exit_engine.cleanup(sym)
                final_excursion = self.tp_sl_engine.deregister_position(sym)
                await self.telegram.send_position_closed(pos, price, pnl, reason)
                # Track closed trade for dashboard analytics (bounded)
                trade_record = {
                    "symbol": pos["symbol"],
                    "side": pos.get("side", "LONG"),
                    "entry_price": pos.get("entry_price", 0),
                    "exit_price": price,
                    "pnl": round(pnl, 2),
                    "exit_reason": reason,
                    "quantity": pos.get("quantity", 0),
                    "partial": False,
                    "timestamp": time.time(),
                    # ── Lifecycle tracking (from TradeEngine) ──
                    "entry_time": te_record.get("entry_time", pos.get("opened_at", 0)),
                    "exit_time": te_record.get("exit_time", time.time()),
                    "holding_period": te_record.get("holding_period", 0),
                    "holding_period_str": te_record.get("holding_period_str", "0s"),
                    # MAE / MFE
                    "mae": te_record.get("mae", final_excursion.get("mae", 0)) if te_record else final_excursion.get("mae", 0),
                    "mae_pct": te_record.get("mae_pct", final_excursion.get("mae_pct", 0)) if te_record else final_excursion.get("mae_pct", 0),
                    "mfe": te_record.get("mfe", final_excursion.get("mfe", 0)) if te_record else final_excursion.get("mfe", 0),
                    "mfe_pct": te_record.get("mfe_pct", final_excursion.get("mfe_pct", 0)) if te_record else final_excursion.get("mfe_pct", 0),
                    # Slippage
                    "entry_slippage_bps": te_record.get("entry_slippage_bps", exe_report.get("entry_slippage_bps", 0)),
                    "total_slippage_bps": te_record.get("total_slippage_bps", exe_report.get("total_slippage_bps", 0)),
                    # Execution quality
                    "entry_quality": exe_report.get("entry_quality", 0),
                    "exit_quality": exe_report.get("exit_quality", 0),
                    "execution_score": exe_report.get("execution_score", 0),
                    # Metadata for adaptive learning
                    "confidence": pos.get("confidence", 0),
                    "institutional_score": pos.get("institutional_score", 0),
                    "regime": pos.get("regime", "unknown"),
                    "trend_score": pos.get("trend_score", 0),
                    "risk_reward": pos.get("risk_reward", 0),
                    "confirmation_factors": pos.get("confirmation_factors", []),
                }
                self._closed_trades.append(trade_record)
                # P7+P8: Record for adaptive learning
                self.signal_filter.record_trade_outcome(trade_record)
                # Phase 12: Record trade outcome for blocker tracking
                self.trade_blocker.record_trade_outcome(pnl, sym)

                # ═══════════════════════════════════════════════════════
                # PHASE 2: REGIME HALT EVALUATION — After every trade close
                # ═══════════════════════════════════════════════════════
                try:
                    _btc_regime = "unknown"
                    _btc_rg = self.regime.get_regime("BTCUSDT") if hasattr(self.regime, 'get_regime') else None
                    if _btc_rg:
                        _btc_regime = _btc_rg.get("regime", "unknown")

                    if pnl <= 0:
                        _cl = regime_state.increment_consecutive_losses()
                    else:
                        regime_state.reset_consecutive_losses()
                        _cl = 0

                    regime_state.evaluate_halt_conditions(
                        daily_stats={"pnl": self.risk.daily_pnl, "trades": len(self._closed_trades)},
                        consecutive_losses=_cl,
                        account_balance=self.risk.balance,
                        current_regime=_btc_regime,
                    )

                    # ═══════════════════════════════════════════════════════
                    # ROLLING PF CHECK — Gate 0 v3.0
                    # If PF < 0.8 over last 20 closed trades → trigger halt
                    # ═══════════════════════════════════════════════════════
                    try:
                        _recent_gw = self.risk.daily_pnl if self.risk.daily_pnl > 0 else 0
                        _recent_gl = abs(self.risk.daily_pnl) if self.risk.daily_pnl < 0 else 0
                        _rolling_pf = _recent_gw / _recent_gl if _recent_gl > 0 else 999
                        # Also check from closed trades in last 20
                        _last20 = [t.get("pnl", 0) for t in list(self._closed_trades)[-20:]]
                        if _last20:
                            _g20 = sum(p for p in _last20 if p > 0)
                            _l20 = sum(abs(p) for p in _last20 if p < 0)
                            _rolling_pf = _g20 / _l20 if _l20 > 0 else 999
                        if _rolling_pf < 0.8 and len(_last20) >= 10:
                            regime_state.trigger_halt(
                                reason=f"Rolling PF {_rolling_pf:.2f} < 0.8 over {len(_last20)} trades",
                                duration_hours=4,
                                resume_condition="regime_must_change",
                                current_regime=_btc_regime,
                            )
                            logger.warning("🛑 PF_HALT: Rolling PF={:.2f} < 0.8 over {} trades", _rolling_pf, len(_last20))
                    except Exception as _pf_err:
                        logger.debug("Rolling PF check failed: {}", _pf_err)
                except Exception as _halt_eval_err:
                    logger.debug("Regime halt evaluation failed: {}", _halt_eval_err)

                # ═══════════════════════════════════════════════════════
                # FIX #5: SYMBOL EXPECTANCY TRACKING
                # ═══════════════════════════════════════════════════════
                try:
                    self.symbol_tracker.record_trade(
                        symbol=sym,
                        side=pos.get("side", "LONG"),
                        pnl=pnl,
                        confidence=pos.get("confidence", 0),
                        regime=pos.get("regime", "unknown"),
                    )
                    # ═══════════════════════════════════════════════
                    # FIX 12: Fast blacklist — 3 losses in 48h
                    # ═══════════════════════════════════════════════
                    _entry_p = pos.get("entry_price", 0)
                    if _entry_p > 0:
                        _pnl_pct = (pnl / (_entry_p * pos.get("quantity", 1))) * 100
                        self.symbol_tracker.record_loss(sym, _pnl_pct)
                except Exception:
                    pass

                # ═══════════════════════════════════════════════════════
                # FIX #1: CLOSE IN TRADE LIFECYCLE ENGINE
                # ═══════════════════════════════════════════════════════
                try:
                    lifecycle_close = self.lifecycle.close_position(sym, price, reason)
                    if lifecycle_close:
                        trade_record["hold_minutes"] = lifecycle_close.get("hold_minutes", 0)
                        trade_record["mae_pct"] = lifecycle_close.get("mae_pct", 0)
                        trade_record["mfe_pct"] = lifecycle_close.get("mfe_pct", 0)
                        trade_record["realized_r"] = lifecycle_close.get("realized_r", 0)
                except Exception:
                    pass

                # ── EMA V5 scanner cleanup on trade close ──
                if pos.get("strategy_version") == "ema_v5":
                    try:
                        self.ema_v5.on_trade_closed(sym)
                    except Exception:
                        pass

                # ── Persist beneficial change (trade closed) ──
                self.state.mark_dirty()
                if len(self._closed_trades) > _MAX_CLOSED_TRADES:
                    self._closed_trades = self._closed_trades[-_MAX_CLOSED_TRADES:]
                logger.info(
                    "📉 {} closed PnL=${:.2f} ({}) | Balance=${:.2f}",
                    pos["symbol"], pnl, reason, self.risk.balance,
                )
          except Exception as _pos_err:
              # CRITICAL: One position's error must NOT kill the loop for other positions
              logger.error("Risk loop error for {}: {}", pos.get("symbol", "?") if pos else "?", _pos_err)
              continue
        await asyncio.sleep(1)

    # ═══════════════════════════════════════════════════════════════════
    # PHASE 3: REGIME QUALITY FILTER
    # ═══════════════════════════════════════════════════════════════════
    BLOCKED_REGIMES = {
        "quiet": True,          # PF=0.30, Exp=-$28.92, N=241 — BLOCKED (catastrophic)
        # Phase 14: range (PF=0.53) and trending_bull (PF=0.67) UNBLOCKED
        # Reason: blocking them violates Rule #9 anti-overfitting (>70% trade reduction)
        # Session filter + MIN_HOLD=20 provide downstream quality gates
    }
    # Phase 14: Only quiet blocked. All other regimes flow through.

    # ═══════════════════════════════════════════════════════════════════
    # PHASE 4: SESSION QUALITY FILTER
    # ═══════════════════════════════════════════════════════════════════
    BLOCKED_SESSIONS = {
        "off_hours": True,  # PF=0.14, Exp=-$38.60, N=80 — BLOCKED
        "asia": True,       # PF=0.54, Exp=-$7.86, N=228 — BLOCKED
    }
    # Safety gate: only block if N>=50 AND PF<0.80
    # Active: off_hours, asia blocked (PF<0.80, N>=50)

    def _is_regime_blocked(self, regime: str) -> bool:
        """Check if regime is in the blocked list."""
        return regime in self.BLOCKED_REGIMES

    def _is_session_blocked(self, session: str) -> bool:
        """Check if session is in the blocked list."""
        return session in self.BLOCKED_SESSIONS

    # ═══════════════════════════════════════════════════════════════
    # PERSISTENT MARKET DATA CACHE — Survives engine restarts
    # Prevents blank dashboard during crash-restart cycles
    # ═══════════════════════════════════════════════════════════════
    def _load_market_cache(self) -> None:
        """Load cached market data from disk. Called at __init__ before WS connects."""
        try:
            if self._data_cache_path.exists():
                with open(self._data_cache_path) as f:
                    cache = json.load(f)
                _age = time.time() - cache.get("timestamp", 0)
                # Only use cache if less than 10 minutes old
                if _age < 600:
                    self._ticker_data = cache.get("ticker_data", {})
                    self._premium_data = cache.get("premium_data", {})
                    self._mark_prices = cache.get("mark_prices", {})
                    self._vol_map = cache.get("vol_map", {})
                    logger.info(
                        "📦 Market cache loaded: {} tickers, {} premium, {} marks (age {:.0f}s)",
                        len(self._ticker_data), len(self._premium_data),
                        len(self._mark_prices), _age,
                    )
                else:
                    logger.info("📦 Market cache expired (age {:.0f}s > 600s) — will refresh", _age)
        except Exception as e:
            logger.debug("Market cache load failed: {}", e)

    def _save_market_cache(self) -> None:
        """Persist market data to disk. Called periodically from cleanup loop."""
        try:
            cache = {
                "ticker_data": self._ticker_data,
                "premium_data": self._premium_data,
                "mark_prices": self._mark_prices,
                "vol_map": self._vol_map,
                "timestamp": time.time(),
            }
            self._data_cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._data_cache_path.with_suffix(".tmp")
            with open(tmp, "w") as f:
                json.dump(cache, f, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp), str(self._data_cache_path))
        except Exception as e:
            logger.debug("Market cache save failed: {}", e)

    # ── Cleanup loop ─────────────────────────────────────────────

    async def _calibration_outcome_loop(self) -> None:
        """Periodically update calibration outcome tracker for forward price tracking."""
        while self.is_running:
            try:
                updated = self._calibration_outcome_tracker.update_outcomes()
                if updated > 0:
                    logger.debug("📊 Calibration: {} outcomes updated", updated)
            except Exception as e:
                logger.debug("Calibration outcome update error: {}", e)
            await asyncio.sleep(300)  # Check every 5 minutes

    async def _cleanup_loop(self) -> None:
        await db.cleanup_old_data(30)
        now = time.time()
        # ── FIX: Increased stale timeout from 300s → 600s ──
        # The kline poll runs every 60s. If REST is slow/failing for one cycle,
        # 300s was too tight — symbols would go stale before poll could refresh them.
        # 600s gives 10 full poll cycles of buffer.
        _STALE_TIMEOUT = 600
        stale = [s for s, d in self.symbol_data.items() if now - d["ts"] > _STALE_TIMEOUT]
        # ── SAFETY: If ALL symbols are stale, something is wrong with data fetching,
        # not with the symbols themselves. Don't mass-delete — log warning instead. ──
        if stale and len(stale) == len(self.symbol_data) and len(self.symbol_data) > 10:
            logger.warning("⚠️  CLEANUP SAFETY: ALL {} symbols stale (>{}s) — "
                          "data fetch likely broken. Skipping mass-delete to prevent scan stall.",
                          len(stale), _STALE_TIMEOUT)
            stale = []
        # ── Cleanup diagnostics ──
        if stale:
            logger.info("🧹 CLEANUP: Deleting {} stale symbols (total={}) — ages: {}",
                        len(stale), len(self.symbol_data),
                        ", ".join(f"{s}={now - self.symbol_data[s].get('ts',0):.0f}s" for s in stale[:10]))
        for s in stale:
            _ts_age = now - self.symbol_data[s].get("ts", 0)
            logger.debug("TRACE[CLEANUP] DELETING sym={} ts_age={:.0f}s", s, _ts_age)
            del self.symbol_data[s]
        # Trim signals list
        self.signals = [s for s in self.signals if s.get("status") == "active"][-_MAX_SIGNALS:]
        
        # Memory guardrail
        try:
            import psutil
            proc = psutil.Process()
            mem_mb = proc.memory_info().rss / (1024 * 1024)
            
            if mem_mb > _MEM_CRITICAL_MB:
                logger.warning("⚠️  MEMORY CRITICAL: {:.0f}MB — trimming", mem_mb)
                _min_5m = ema_v5_config.ema.min_candles  # 220 — required by Fast Filter
                for sd in self.symbol_data.values():
                    # Preserve 5m klines (critical for Fast Filter + regime detection)
                    for iv in list(sd.get("klines", {})):
                        if iv != "5m":
                            sd["klines"][iv] = []
                    kl5 = sd.get("klines", {}).get("5m", [])
                    if len(kl5) > _min_5m:
                        sd["klines"]["5m"] = kl5[-_min_5m:]
                    if len(sd["trades"]) > 200:
                        sd["trades"] = sd["trades"][-200:]
            elif mem_mb > _MEM_WARN_MB:
                logger.warning("⚠️  MEMORY HIGH: {:.0f}MB — trimming", mem_mb)
                _min_5m = ema_v5_config.ema.min_candles  # 220 — required by Fast Filter
                for sd in self.symbol_data.values():
                    for iv in list(sd.get("klines", {})):
                        kl = sd["klines"][iv]
                        if iv == "5m" and len(kl) > _min_5m:
                            sd["klines"][iv] = kl[-_min_5m:]
                        elif iv != "5m" and len(kl) > 50:
                            sd["klines"][iv] = kl[-50:]
                    if len(sd["trades"]) > 500:
                        sd["trades"] = sd["trades"][-500:]
            else:
                logger.debug("Memory: {:.0f}MB | Symbols: {} | Signals: {}", 
                             mem_mb, len(self.symbol_data), len(self.signals))
        except ImportError:
            pass  # psutil not installed — skip memory check
        
        # ── EQUITY HISTORY TRIM: Prevent unbounded growth ──
        try:
            _max_eq = getattr(self, '_MAX_EQUITY_HISTORY', 1000)
            if len(self._equity_history) > _max_eq:
                self._equity_history = self._equity_history[-_max_eq:]
        except Exception:
            pass

        # PHASE 2 FIX: Persist market data cache every 5 minutes
        # Ensures dashboard has data immediately after engine restart
        try:
            self._save_market_cache()
        except Exception as e:
            logger.debug("Market cache save error: {}", e)

        # ── WAITING_CONFIRMATION timeout check ──
        try:
            _wc_timeouts = self.ema_v5.waiting_audit.check_timeouts()
            if _wc_timeouts:
                logger.warning("⏰ WC_TIMEOUT: {} symbols stuck in WAITING_CONFIRMATION", len(_wc_timeouts))
                for _sym in _wc_timeouts:
                    # ── LIFECYCLE: Log timeout reset ──
                    _old = self.ema_v5.state_manager.get_state(_sym)
                    self.ema_v5.state_manager.set_state(_sym, NO_TREND)
                    lifecycle_log.transition(_sym, _old, NO_TREND, "wc_timeout_300s")
        except Exception as _wc_err:
            logger.debug("WC timeout check error: {}", _wc_err)

        # ── STALE STATE RESET: Fix for disk-loaded stuck states ──
        # On restart, symbols loaded from disk state file are not tracked by
        # waiting_audit._active. This block checks ALL waiting states in the
        # state_manager and resets any that exceed the timeout.
        try:
            _now = time.time()
            _reset_count = 0
            for _sym, _sdata in self.ema_v5.state_manager.get_all_states().items():
                _st = _sdata.get("state", "")
                _upd = _sdata.get("last_update", 0)
                _dur = _now - _upd if _upd > 0 else 0
                if _st in ("WAITING_PULLBACK", "WAITING_CONFIRMATION") and _dur > 300:
                    # Stuck too long — reset to NO_TREND
                    self.ema_v5.state_manager.set_state(_sym, "NO_TREND")
                    lifecycle_log.transition(_sym, _st, "NO_TREND", f"stale_state_reset_{_dur:.0f}s")
                    _reset_count += 1
            if _reset_count > 0:
                logger.warning("🧹 STALE_STATE_RESET: {} symbols reset from stuck waiting states", _reset_count)
        except Exception as _stale_err:
            logger.debug("Stale state reset error: {}", _stale_err)

        # ── FAILURE DETECTOR: Check for stale ACTIVE signals ──
        try:
            _stale_active = []
            for _sig in list(self.signals):
                if _sig.get("status") == "active":
                    _sig_age = time.time() - _sig.get("created_at", time.time())
                    if _sig_age > 48 * 3600:  # 48 hours
                        _stale_active.append(_sig.get("symbol", "?"))
            if _stale_active:
                logger.warning("🚨 STALE_ACTIVE: {} signals active > 48h: {}", len(_stale_active), _stale_active)
        except Exception as _stale_err:
            logger.debug("Stale active check error: {}", _stale_err)
        
        await asyncio.sleep(300)  # Run every 5 minutes

    # ── Helpers ──────────────────────────────────────────────────

    def _price(self, sym: str) -> Optional[float]:
        """Return current price for a symbol.
        Prefer production REST price (from /fapi/v1/ticker/24hr) over WS price,
        because WS may be connected to testnet when --testnet flag is set.
        """
        # Production price from REST ticker (always accurate)
        prod = self._ticker_data.get(sym, {}).get("price", 0)
        if prod > 0:
            return prod
        # Fallback: WS trade price (may be testnet)
        trades = self.symbol_data.get(sym, {}).get("trades", [])
        return trades[-1]["price"] if trades else None

    async def _fetch_price_rest(self, sym: str) -> Optional[float]:
        """Fetch current price directly from Binance REST API.
        Used as last-resort fallback for open position monitoring when
        _ticker_data and symbol_data don't have the symbol.
        """
        try:
            data = await self.ws._get(f"/fapi/v1/ticker/price?symbol={sym}")
            if data and "price" in data:
                return float(data["price"])
        except Exception as e:
            logger.debug("REST price fetch failed for {}: {}", sym, e)
        return None

    async def _expire_signals(self) -> None:
        now = time.time()
        cutoff = now - config.scanner.signal_cooldown_sec
        # Get active EMA V5 states — don't expire signals for active trades
        _ema_active = set()
        try:
            for sym, sdata in self.ema_v5.state_manager.get_all_states().items():
                st = sdata.get("state", "")
                if st in ("ACTIVE_BUY", "ACTIVE_SELL", "WAITING_CONFIRMATION"):
                    _ema_active.add(sym)
        except Exception:
            pass
        # Get symbols with open positions — NEVER expire signals for active trades
        _open_position_syms = set()
        try:
            _open_position_syms = set(getattr(self.risk, '_positions', {}).keys())
        except Exception:
            pass
        for s in self.signals:
            if s.get("created_at", now) < cutoff and s.get("status") == "active":
                _sym = s.get("symbol", "")
                # Don't expire signals that have an open position
                if _sym in _open_position_syms:
                    continue
                # Don't expire EMA V5 signals while trade state is still active
                if s.get("strategy_version") == "ema_v5" and _sym in _ema_active:
                    continue
                s["status"] = "expired"
        self.signals = [s for s in self.signals if s.get("status") == "active"]

        # Persist zombie cleanup to DB: expire active signals >24h with no position.
        # This unblocks dedup for symbols that have stale DB records.
        try:
            await db.expire_zombie_signals(max_age_hours=24)
        except Exception as e:
            logger.debug("Zombie signal cleanup error: {}", e)

        # ── EMA_V5 state cleanup: expire orphaned ACTIVE states ──
        # If a symbol is ACTIVE_BUY/ACTIVE_SELL but has no DB signal and
        # no open position, the state is a zombie from a prior session.
        # Age check removed: rely on DB position existence for accuracy.
        #
        # ═══ RE-ENABLED: Orphan cleanup now checks DB signals too ═══
        # Added DB signal check to prevent race condition with self.signals
        _ORPHAN_CLEANUP_ENABLED = True  # Re-enabled after proof
        #
        try:
            _state_mgr = self.ema_v5.state_manager
            _orphan_count = 0
            # Build set of symbols with active engine signals
            _engine_active = {s.get("symbol") for s in self.signals if s.get("status") == "active"}
            # Also check DB for recent active signals (fixes race condition with self.signals)
            _db_active = set()
            try:
                import sqlite3
                _db_conn = sqlite3.connect(str(Path("packages/ai-engine/data/institutional_v1.db")))
                _db_cur = _db_conn.cursor()
                # Check for ANY active signal (strategy_version may not be set)
                _db_cur.execute("SELECT symbol FROM signals WHERE status='active'")
                _db_active = {row[0] for row in _db_cur.fetchall()}
                _db_conn.close()
            except Exception:
                pass
            _all_active = _engine_active | _db_active
            for sym, sdata in list(_state_mgr.get_all_states().items()):
                st = sdata.get("state", "")
                if st not in ("ACTIVE_BUY", "ACTIVE_SELL"):
                    continue
                # Check if engine has an active signal for this symbol
                _has_signal = sym in _all_active
                # Check if there's an open position
                _has_pos = sym in _open_position_syms
                # ── DETAILED TRACE for every ACTIVE state ──
                _created = sdata.get("created_at", 0)
                _age = time.time() - _created if _created else -1
                logger.info("ORPHAN_TRACE sym={} state={} age={:.0f}s has_signal={} has_pos={} db_signal={}",
                            sym, st, _age, _has_signal, _has_pos, sym in _db_active)
                # ── Grace period: don't expire states set within last 60s ──
                # This prevents the orphan cleanup from racing with the session filter
                if _age >= 0 and _age < 60:
                    logger.debug("ORPHAN_TRACE sym={} SKIP (grace period {:.0f}s < 60s)", sym, _age)
                    continue
                if not _has_signal and not _has_pos:
                    if _ORPHAN_CLEANUP_ENABLED:
                        _state_mgr.reset(sym)
                        _orphan_count += 1
                        logger.info("🧹 EMA_V5 orphan state expired: {} (was {}) — has_signal={} has_pos={}",
                                    sym, st, _has_signal, _has_pos)
            if _orphan_count:
                logger.info("🧹 EMA_V5 state cleanup: {} orphaned ACTIVE states expired", _orphan_count)
        except Exception as e:
            logger.debug("EMA_V5 state cleanup error: {}", e)

    def get_status(self) -> Dict:
        return {
            "running": self.is_running,
            "symbols": len(self.active_symbols),
            "with_data": len(self.symbol_data),
            "signals": len(self.signals),
            "uptime": round(time.time() - self._t0, 1) if self._t0 else 0,
        }

    def _sync_bridge(self) -> None:
        """Sync engine state to dashboard bridge — SINGLE SOURCE OF TRUTH for all data.

        Fixes:
        - Position count: always from len(risk._positions), not manual counter
        - Signal count: dedup once, same count for status + signals
        - Portfolio metrics: includes unrealized PnL, computes real sharpe/sortino/drawdown
        - Equity curve: proper peak tracking and drawdown calculation
        - PHASE 2: Auto-refresh ticker data from WS cache if stale
        """
        import math

        # PHASE 2 FIX: Auto-refresh _ticker_data from WS cache every sync cycle
        # Prevents None values for 24h/high/low/open fields
        try:
            _ws_cache = self.ws._ws_ticker_cache if hasattr(self.ws, '_ws_ticker_cache') else {}
            if _ws_cache and len(_ws_cache) > len(self._ticker_data) * 0.5:
                self._ticker_data.update(_ws_cache)
        except (AttributeError, TypeError):
            pass

        # PHASE 2 FIX: Ensure production_targets TP values reach the signal dict
        # If production_targets was called but TP2/TP3 are still 0, recompute
        for sig in list(self.signals):
            sym = sig.get("symbol", "")
            tp2 = sig.get("take_profit_2", 0)
            tp3 = sig.get("take_profit_3", 0)
            if tp2 <= 0 or tp3 <= 0:
                # Recompute from production_targets
                try:
                    _entry = sig.get("entry_price", 0)
                    _side = sig.get("side", "LONG")
                    _sl = sig.get("stop_loss", 0)
                    _sl_dist = abs(_entry - _sl) if _sl else _entry * 0.02
                    if _entry > 0 and _sl_dist > 0:
                        # Fallback TP calculation if production_targets didn't set them
                        if tp2 <= 0:
                            sig["take_profit_2"] = _entry + _sl_dist * 3.0 if _side == "LONG" else _entry - _sl_dist * 3.0
                        if tp3 <= 0:
                            sig["take_profit_3"] = _entry + _sl_dist * 5.0 if _side == "LONG" else _entry - _sl_dist * 5.0
                        logger.debug("TP_FALLBACK: {} {} tp2={:.4f} tp3={:.4f}", sym, _side, sig["take_profit_2"], sig["take_profit_3"])
                except Exception:
                    pass

        # ── Pre-compute shared data used by multiple sections ──

        # A. Deduplicate signals — keep only the HIGHEST-SCORED signal per symbol
        #    (prevents same symbol appearing as both LONG and SHORT)
        seen: Dict[str, Dict] = {}
        for sig in self.signals:
            sym_key = sig.get('symbol', '')
            sig_score = sig.get('institutional_score', 0) + sig.get('confidence', 0) * 10
            if sym_key not in seen or sig_score > (seen[sym_key].get('institutional_score', 0) + seen[sym_key].get('confidence', 0) * 10):
                seen[sym_key] = sig
        deduped_signals = list(seen.values())
        n_signals = len(deduped_signals)

        # Debug: log EMA_V5 signal count in deduped list
        _ema_in_deduped = [s for s in deduped_signals if s.get("strategy_version") == "ema_v5"]
        if _ema_in_deduped:
            logger.info(
                "📊 BRIDGE DEBUG: {} EMA_V5 signals in deduped_signals (total_signals={})",
                len(_ema_in_deduped), n_signals,
            )

        # B. Build positions with full metadata (single source for positions + metrics)
        risk = self.risk
        closed = getattr(self, '_closed_trades', [])
        positions: List[Dict] = []
        total_unrealized = 0.0
        try:
            # Build a lookup of signals by symbol for enrichment
            _sig_by_sym: Dict[str, Dict] = {}
            for sig in deduped_signals:
                _sig_by_sym[sig.get("symbol", "")] = sig

            for sym, pos in getattr(risk, '_positions', {}).items():
                cur_price = self._price(sym) or 0
                entry_price = pos.get('entry_price', 0) or 0
                side = pos.get('side', 'LONG')
                qty = pos.get('quantity', 0) or 0
                lev = pos.get('leverage', 1) or 1
                sl = pos.get('stop_loss', 0) or 0
                tp = pos.get('take_profit', 0) or 0
                opened_at = pos.get('opened_at', time.time())
                if cur_price > 0 and entry_price > 0:
                    if side == 'LONG':
                        unrealized_pnl = (cur_price - entry_price) * qty
                    else:
                        unrealized_pnl = (entry_price - cur_price) * qty
                else:
                    unrealized_pnl = 0
                unrealized_pnl = round(unrealized_pnl, 2)
                total_unrealized += unrealized_pnl

                # ── Enrich with signal metadata ──
                # First try current cycle signal, then fall back to position-stored values
                sig = _sig_by_sym.get(sym, {})
                # Audit: Enforce minimum risk floor for consistent R-multiple reporting
                risk_dist = max(abs(entry_price - sl) if sl > 0 else entry_price * 0.02, entry_price * 0.002)
                # R-Multiple: how many R of profit/loss (null-safe for missing prices)
                if cur_price and entry_price:
                    if side == 'LONG':
                        r_multiple = (cur_price - entry_price) / risk_dist if risk_dist else 0
                    else:
                        r_multiple = (entry_price - cur_price) / risk_dist if risk_dist else 0
                else:
                    r_multiple = 0
                # Risk %: position risk as % of entry
                risk_pct = (risk_dist / entry_price * 100) if entry_price else 0
                # Expected value: win_rate * avg_win - loss_rate * avg_loss (from signal)
                # FIX: Fall back to position-stored values when signal not in current cycle
                confidence = sig.get('confidence', 0) or pos.get('confidence', 0)
                inst_score = sig.get('institutional_score', 0) or pos.get('institutional_score', 0)
                regime = sig.get('regime') or pos.get('regime', 'unknown')
                risk_reward = sig.get('risk_reward', 0)
                score_breakdown = sig.get('score_breakdown', {})
                if risk_reward <= 0 and risk_dist > 0 and tp:
                    risk_reward = abs(tp - entry_price) / risk_dist

                positions.append({
                    "symbol": sym,
                    "side": side,
                    "status": "open",
                    "entry_price": entry_price,
                    "current_price": cur_price or 0,
                    "quantity": qty,
                    "pnl": unrealized_pnl,
                    "unrealized_pnl": unrealized_pnl,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "take_profit_1": pos.get('take_profit_1', tp),
                    "take_profit_2": pos.get('take_profit_2', 0),
                    "take_profit_3": pos.get('take_profit_3', 0),
                    "sl_source": pos.get('sl_source', ''),
                    "tp1_source": pos.get('tp1_source', ''),
                    "tp2_source": pos.get('tp2_source', ''),
                    "tp3_source": pos.get('tp3_source', ''),
                    "trailing_activation": pos.get('trailing_activation', 2.5),
                    "breakeven_activation": pos.get('breakeven_activation', 1.2),
                    "opened_at": opened_at,
                    "leverage": lev,
                    # ── New professional fields ──
                    "r_multiple": round(r_multiple, 2),
                    "risk_pct": round(risk_pct, 2),
                    "risk_reward": round(risk_reward, 2),
                    "confidence": round(confidence, 3),
                    "institutional_score": round(inst_score, 1),
                    "regime": regime,
                    "score_breakdown": score_breakdown,
                    "score": round(inst_score, 1),  # alias for dashboard compatibility
                })
        except Exception as e:
            logger.debug("Bridge sync error (positions compute): {}", e)

        n_open = len(positions)  # In-memory count

        # C. Compute portfolio-level analytics from closed trades
        realized_pnl = sum(t.get('pnl', 0) for t in closed)
        wins = [t for t in closed if t.get('pnl', 0) > 0]
        losses = [t for t in closed if t.get('pnl', 0) <= 0]
        n_wins = len(wins)
        n_losses = len(losses)
        n_total = len(closed)
        win_rate = (n_wins / n_total * 100) if n_total > 0 else 0

        # ── Enrich metrics from database (survives restarts) ──
        _db_path = None
        db_daily_pnl = 0.0
        gross_wins = 0.0
        gross_losses = 0.0
        _db_total = 0
        _db_wins = 0
        _db_realized = 0.0
        _db_gross_profit = 0.0
        _db_gross_loss = 0.0
        _trades_today = 0
        _db_open_positions = 0
        try:
            import sqlite3 as _sqlite3
            import os as _os
            _db_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)),
                                      "data", "institutional_v1.db")
            _conn = _sqlite3.connect(_db_path, timeout=10)
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA busy_timeout=5000")  # Wait up to 5s for locks
            _cur = _conn.cursor()
            # Query both positions AND positions_archive for lifetime metrics
            _union_sql = "SELECT symbol, side, pnl, status, opened_at, closed_at FROM positions WHERE status='closed' UNION ALL SELECT symbol, side, pnl, status, opened_at, closed_at FROM positions_archive WHERE status='closed'"
            _cur.execute(f"SELECT COUNT(*) FROM ({_union_sql})")
            _db_total = _cur.fetchone()[0]
            _cur.execute(f"SELECT COUNT(*) FROM ({_union_sql}) WHERE pnl > 0")
            _db_wins = _cur.fetchone()[0]
            _cur.execute(f"SELECT COALESCE(SUM(pnl),0) FROM ({_union_sql})")
            _db_realized = _cur.fetchone()[0]
            _cur.execute(f"SELECT COALESCE(AVG(pnl),0) FROM ({_union_sql}) WHERE pnl > 0")
            _db_avg_win = _cur.fetchone()[0]
            _cur.execute(f"SELECT COALESCE(AVG(pnl),0) FROM ({_union_sql}) WHERE pnl <= 0")
            _db_avg_loss = _cur.fetchone()[0]
            _cur.execute(f"SELECT COALESCE(SUM(pnl),0) FROM ({_union_sql}) WHERE pnl > 0")
            _db_gross_profit = _cur.fetchone()[0]
            _cur.execute(f"SELECT COALESCE(SUM(ABS(pnl)),0) FROM ({_union_sql}) WHERE pnl <= 0")
            _db_gross_loss = _cur.fetchone()[0]
            # Today's PnL from DB
            _cur.execute(f"SELECT COALESCE(SUM(pnl),0) FROM ({_union_sql}) WHERE closed_at > ?", (time.time() - 86400,))
            db_daily_pnl = _cur.fetchone()[0]
            # Trades today (closed in last 24h) — ALWAYS from DB
            _cur.execute("""SELECT COUNT(*) FROM (
                SELECT closed_at FROM positions WHERE status='closed' AND closed_at > ?
                UNION ALL
                SELECT closed_at FROM positions_archive WHERE status='closed' AND closed_at > ?
            )""", (time.time() - 86400, time.time() - 86400,))
            _trades_today = _cur.fetchone()[0]
            # Open positions from DB — cross-check with in-memory count
            _cur.execute("SELECT COUNT(*) FROM positions WHERE status='open'")
            _db_open_positions = _cur.fetchone()[0]
            _conn.close()
            # Use DB data for lifetime metrics (survives restarts)
            if _db_total > 0:
                n_total = _db_total
                n_wins = _db_wins
                win_rate = (_db_wins / _db_total * 100) if _db_total > 0 else 0
                realized_pnl = _db_realized
                gross_wins = _db_gross_profit
                gross_losses = _db_gross_loss
        except Exception as _db_err:
            logger.warning("Bridge DB metrics query failed: {}", _db_err)

        # ── P0 FIX: Reconcile risk._positions with DB ──
        # If DB has more open positions than in-memory dict, restore missing ones.
        # This prevents split-brain between risk engine and database.
        if _db_path and _db_open_positions > n_open:
            try:
                _rec = _sqlite3.connect(_db_path, timeout=10)
                _rec.execute("PRAGMA journal_mode=WAL")
                _rec.execute("PRAGMA busy_timeout=5000")  # Wait up to 5s for locks
                _rcr = _rec.cursor()
                _existing_syms = set(risk._positions.keys())
                _rcr.execute("""SELECT id, signal_id, symbol, side, entry_price,
                    quantity, leverage, stop_loss, take_profit, opened_at,
                    confidence, institutional_score, regime, risk_reward,
                    highest_pnl, mfe_pct
                    FROM positions WHERE status='open'""")
                _restored = 0
                for row in _rcr.fetchall():
                    (_rid, _rsig, _rsym, _rsd, _rep, _rqty, _rlv, _rsl, _rtp,
                     _roa, _rcf, _ris, _rrg, _rrr, _rpeak, _rmfe) = row
                    if _rsym and _rsym not in _existing_syms:
                        risk._positions[_rsym] = {
                            'id': _rid, 'signal_id': _rsig, 'symbol': _rsym,
                            'side': _rsd, 'entry_price': _rep, 'quantity': _rqty,
                            'leverage': _rlv or 1, 'stop_loss': _rsl,
                            'take_profit': _rtp, 'opened_at': _roa,
                            'confidence': _rcf or 0,
                            'institutional_score': _ris or 0,
                            'regime': _rrg or 'unknown',
                        }
                        if _rpeak and _rpeak > 0:
                            risk._highest_pnl[_rsym] = _rpeak
                        if _rmfe and _rmfe > 0:
                            risk._mfe_pct[_rsym] = _rmfe
                        # ═══════════════════════════════════════════════════════
                        # FIX: Register restored positions in lifecycle engine
                        # Without this, MFE/MAE tracking stops after engine restart
                        # ═══════════════════════════════════════════════════════
                        if _rsym not in self.lifecycle._positions:
                            self.lifecycle.register_position(
                                symbol=_rsym, side=_rsd, entry_price=_rep,
                                stop_loss=_rsl, take_profit=_rtp,
                                risk_reward=_rrr or 3.0,
                            )
                        # Also restore trade engine tracking
                        if _rsym not in self.trade_engine._positions:
                            self.trade_engine.open_position(
                                symbol=_rsym, side=_rsd, entry_price=_rep,
                                signal_price=_rep, quantity=_rqty,
                                leverage=_rlv or 1, stop_loss=_rsl, take_profit=_rtp,
                                signal_time=_roa or time.time(),
                            )
                        _cp = self._price(_rsym) or 0
                        if _rsd == 'LONG':
                            _pnl = (_cp - _rep) * _rqty if _cp and _rep else 0
                        else:
                            _pnl = (_rep - _cp) * _rqty if _cp and _rep else 0
                        _pnl = round(_pnl, 2)
                        total_unrealized += _pnl
                        positions.append({
                            'symbol': _rsym, 'side': _rsd, 'status': 'open',
                            'entry_price': _rep, 'current_price': _cp or 0,
                            'quantity': _rqty, 'pnl': _pnl, 'unrealized_pnl': _pnl,
                            'stop_loss': _rsl or 0, 'take_profit': _rtp or 0,
                            'take_profit_1': _rtp or 0, 'take_profit_2': 0, 'take_profit_3': 0,
                            'sl_source': '', 'tp1_source': '', 'tp2_source': '', 'tp3_source': '',
                            'trailing_activation': 2.5, 'breakeven_activation': 1.2,
                            'opened_at': _roa or 0, 'leverage': _rlv or 1,
                            'r_multiple': 0, 'risk_pct': 0,
                            'risk_reward': round(_rrr or 0, 2),
                            'confidence': round(_rcf or 0, 3),
                            'institutional_score': round(_ris or 0, 1),
                            'regime': _rrg or 'unknown',
                            'score_breakdown': {}, 'score': round(_ris or 0, 1),
                        })
                        _restored += 1
                _rec.close()
                if _restored > 0:
                    logger.info("🔄 Reconciled {} missing positions from DB into risk engine", _restored)
                    n_open = len(positions)
            except Exception as _rec_err:
                logger.debug("Bridge position reconciliation failed: {}", _rec_err)
                n_open = len(positions)

        # Profit factor — use DB values (already set above) or in-memory fallback
        if gross_wins == 0 and gross_losses == 0 and n_total > 0:
            # Fallback: compute from in-memory
            gross_wins = sum(t.get('pnl', 0) for t in wins)
            gross_losses = abs(sum(t.get('pnl', 0) for t in losses))
        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else float('inf')

        # Expectancy (avg PnL per trade)
        expectancy = (realized_pnl / n_total) if n_total > 0 else 0

        # Current balance + unrealized = true portfolio value
        current_balance = getattr(risk, 'balance', 10000.0)
        portfolio_value = current_balance + total_unrealized

        # Daily PnL: use risk engine if nonzero, otherwise use DB value
        _daily_pnl = getattr(risk, 'daily_pnl', 0.0)
        if _daily_pnl == 0:
            try:
                _daily_pnl = db_daily_pnl
            except NameError:
                pass

        # Track equity peak for drawdown
        if not hasattr(self, '_equity_peak'):
            self._equity_peak = portfolio_value
        if portfolio_value > self._equity_peak:
            self._equity_peak = portfolio_value
        max_drawdown_pct = ((self._equity_peak - portfolio_value) / self._equity_peak * 100) if self._equity_peak > 0 else 0

        # If max drawdown is 0 (fresh start), compute from DB equity curve
        if max_drawdown_pct == 0 and _db_path:
            try:
                import sqlite3 as _sqlite3_dd
                _conn_dd = _sqlite3_dd.connect(_db_path)
                _cur_dd = _conn_dd.cursor()
                _cur_dd.execute("SELECT pnl FROM positions WHERE status='closed' AND pnl IS NOT NULL ORDER BY opened_at")
                _dd_rows = _cur_dd.fetchall()
                _conn_dd.close()
                if _dd_rows:
                    _eq = 10000.0
                    _pk = 10000.0
                    _mdd = 0.0
                    for (_pnl,) in _dd_rows:
                        _eq += _pnl
                        if _eq > _pk:
                            _pk = _eq
                        _dd = (_pk - _eq) / _pk * 100 if _pk > 0 else 0
                        if _dd > _mdd:
                            _mdd = _dd
                    max_drawdown_pct = _mdd
            except Exception:
                pass

        # Sharpe ratio (annualized from per-trade returns)
        sharpe_ratio = 0.0
        sortino_ratio = 0.0
        # Use in-memory trades if available, otherwise query DB
        _pnl_source = [t.get('pnl', 0) for t in closed] if closed else []
        if not _pnl_source and n_total >= 2 and _db_path:
            try:
                _conn2 = _sqlite3.connect(_db_path, timeout=10)
                _cur2 = _conn2.cursor()
                _cur2.execute("SELECT pnl FROM positions WHERE status='closed' AND pnl IS NOT NULL ORDER BY opened_at DESC LIMIT 200")
                _pnl_source = [r[0] for r in _cur2.fetchall()]
                _conn2.close()
            except Exception:
                pass
        if len(_pnl_source) >= 2:
            mean_pnl = np.mean(_pnl_source)
            std_pnl = np.std(_pnl_source, ddof=1)
            if std_pnl > 0:
                sharpe_ratio = mean_pnl / std_pnl * np.sqrt(252)
                # Sortino: only downside deviation
                downside = [p for p in _pnl_source if p < 0]
                downside_std = np.std(downside, ddof=1) if len(downside) > 1 else std_pnl
                if downside_std > 0:
                    sortino_ratio = mean_pnl / downside_std * np.sqrt(252)

        # ── 1. Signals (deduped) — enriched with backtest stats + risk metrics ──
        try:
            self.backtest_stats.load_reports()
            # Get open positions for correlation/exposure analysis
            open_pos = list(self.risk._positions.values()) if hasattr(self.risk, '_positions') else []
            for sig in deduped_signals:
                self.backtest_stats.enrich_signal(sig)
                # Compute risk metrics
                bt_stats = self.backtest_stats.get_symbol_stats(sig.get("symbol", ""))
                risk_mets = self.risk_metrics.compute(
                    signal=sig,
                    balance=self.risk.balance,
                    open_positions=open_pos,
                    backtest_stats=bt_stats,
                    risk_per_trade_pct=config.risk.risk_per_trade_pct,
                    max_daily_loss_pct=config.risk.max_daily_loss_pct,
                    max_position_pct=config.risk.max_position_pct,
                    max_open_positions=config.risk.max_open_positions,
                    daily_pnl=getattr(self.risk, 'daily_pnl', 0.0),
                    peak_balance=getattr(self.risk, 'peak', self.risk.balance),
                )
                sig["risk_metrics"] = risk_mets

            # Merge with DB-active signals: self.signals expires after 600s but
            # the DB is the source of truth. Pull today's active signals from DB
            # so the dashboard always shows all signals that have open positions.
            _bridge_signals = list(deduped_signals)
            _bridge_syms = {s.get("symbol", "") for s in _bridge_signals}
            try:
                import sqlite3 as _sig_sql
                _db_conn = _sig_sql.connect(
                    str(Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"),
                    timeout=10,
                )
                _cur = _db_conn.cursor()
                _today_start = time.time() - 86400  # last 24 hours
                _cur.execute(
                    "SELECT DISTINCT s.symbol, s.side, s.confidence, s.entry, "
                    "s.stop_loss, s.take_profit, s.risk_reward, s.status, "
                    "s.timestamp, s.id, s.market_regime, s.institutional_score "
                    "FROM signals s "
                    "LEFT JOIN positions p ON s.id = p.signal_id "
                    "WHERE (s.status='active' OR (p.status='open' AND p.symbol=s.symbol)) "
                    "AND s.timestamp >= ? "
                    "ORDER BY s.timestamp DESC",
                    (_today_start,),
                )
                for row in _cur.fetchall():
                    _sym = row[0]
                    if _sym not in _bridge_syms:
                        _bridge_signals.append({
                            "symbol": _sym,
                            "side": row[1],
                            "confidence": row[2] * 100 if row[2] <= 1 else row[2],
                            "entry_price": row[3],
                            "stop_loss": row[4],
                            "take_profit": row[5],
                            "risk_reward": row[6],
                            "status": row[7],
                            "timestamp": row[8],
                            "id": row[9],
                            "regime": row[10] or "",
                            "institutional_score": row[11] or 0,
                            "strategy_version": "production_v2",
                        })
                        _bridge_syms.add(_sym)
                _db_conn.close()
            except Exception as _db_sig_err:
                logger.debug("DB signal merge for bridge: {}", _db_sig_err)
            bridge_writer.write_signals(_bridge_signals)
        except Exception as e:
            logger.debug("Bridge sync error (signals): {}", e)

        # ── 2. Status (uses SAME merged count) ──
        try:
            # Check halt state from regime_state
            _halted = False
            _halt_reason = ""
            try:
                _btc_rg = self.regime.get_regime("BTCUSDT") if hasattr(self.regime, 'get_regime') else None
                _btc_regime = _btc_rg.get("regime", "unknown") if _btc_rg else "unknown"
                _halted, _halt_reason = regime_state.is_halted(_btc_regime)
            except Exception:
                pass
            bridge_writer.write_status(EngineStatus(
                running=self.is_running,
                symbols=len(self.active_symbols),
                signals=len(_bridge_signals),
                alerts=len(_bridge_signals),
                uptime=round(time.time() - self._t0, 1) if self._t0 else 0,
                last_update=time.time(),
                ws_connected=self.ws._connected if hasattr(self.ws, '_connected') else False,
                halted=_halted,
                halt_reason=_halt_reason,
                freshness_snapshot=self.data_freshness.get_snapshot(),
            ))
        except Exception as e:
            logger.debug("Bridge sync error (status): {}", e)

        # ── 3. Metrics (single source of truth — all views read from here) ──
        # trades_today and _db_open_positions computed in DB block above
        # Fallback: in-memory if DB query failed
        if _trades_today == 0:
            _trades_today = sum(1 for t in closed if time.time() - t.get('timestamp', 0) < 86400)
        try:
            bridge_writer.write_metrics({
                "portfolio_value": round(portfolio_value, 2),
                "total_pnl": round(realized_pnl + total_unrealized, 2),
                "realized_pnl": round(realized_pnl, 2),
                "unrealized_pnl": round(total_unrealized, 2),
                "daily_pnl": round(_daily_pnl, 2),
                "win_rate": round(win_rate, 1),
                "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else 999.99,
                "expectancy": round(expectancy, 2),
                "sharpe_ratio": round(sharpe_ratio, 2),
                "sortino_ratio": round(sortino_ratio, 2),
                "max_drawdown": round(max_drawdown_pct, 1),
                "trades_total": n_total,
                "trades_today": _trades_today,
                "symbols_scanned": len(self.active_symbols),
                "open_positions": n_open,
                "scan_time_sec": 0.0,
                "errors": 0,
            })
        except Exception as e:
            logger.debug("Bridge sync error (metrics): {}", e)

        # ── 3b. Engine Health Data ──
        try:
            health = self.perf_tracker.health_monitor.get_health()
            forward = self.perf_tracker.forward_tracker.get_forward_stats()
            bridge_writer.write_engine_health({
                "signals_generated_today": health["signals_generated"],
                "signals_rejected_today": health["signals_rejected"],
                "elite_signals_today": health["elite_signals"],
                "avg_confidence": health["avg_confidence"],
                "avg_rr": health["avg_rr"],
                "pass_rate": health["pass_rate"],
                "total_attempts": health["total_attempts"],
                "forward_win_rate": forward.get("win_rate", 0),
                "forward_profit_factor": forward.get("profit_factor", 0),
                "forward_expectancy": forward.get("expectancy", 0),
                "forward_total": forward.get("total", 0),
                "dynamic_threshold": self.perf_tracker.dynamic_threshold.get_threshold(
                    self._last_regime if hasattr(self, '_last_regime') else "range", 0.5
                ),
            })
        except Exception as e:
            logger.debug("Bridge sync error (health): {}", e)

        # ── 4. Market data ──
        try:
            market_rows = []
            # Touch data quality freshness for ALL active symbols
            # REST data is being processed every scan cycle (~15s) for all symbols
            for sym in self.active_symbols:
                self.data_quality.touch_rest_data(sym)
            
            for sym, sd in self.symbol_data.items():
                if not sd.get("trades"): continue
                last = sd["trades"][-1]
                # PHASE 2 FIX: Multi-source price + ticker data with WS cache fallback
                # _ticker_data may not have all symbols yet; WS cache has them immediately
                _tk = self._ticker_data.get(sym, {})
                # Direct access to WS ticker cache (always populated by !ticker@arr)
                try:
                    _ws_tk = self.ws._ws_ticker_cache.get(sym, {})
                except (AttributeError, TypeError):
                    _ws_tk = {}
                # Merge: _ticker_data takes priority, WS cache fills gaps
                _eff_ticker = {**_ws_tk, **_tk} if (_ws_tk or _tk) else {}
                prod_price = float(_eff_ticker.get("price") or 0)
                price = prod_price if prod_price > 0 else last.get("price", 0)

                funding_data = self.funding.get_analysis(sym)
                oi_data = self.oi.get_analysis(sym)
                liq_data = self.liquidation.get_analysis(sym)
                sweep_det_data = self.sweep.get_analysis(sym) if hasattr(self.sweep, 'get_analysis') else None
                fvg_det_data = self.fvg.get_analysis(sym) if hasattr(self.fvg, 'get_analysis') else None
                # Pass 24h volume to exchange flow for validation
                vol_24h_quote = float(_eff_ticker.get('quoteVolume', 0))
                if vol_24h_quote > 0:
                    self.exchange_flow.set_vol_24h(sym, vol_24h_quote)
                ef_data = self.exchange_flow.get_analysis(sym)
                cvd_data = self.cvd_inst.get_analysis(sym)
                regime_data = self.regime.get_regime(sym) if hasattr(self.regime, 'get_regime') else None

                funding_rate = funding_data.get("current_rate", 0) if funding_data else 0
                funding_bias = funding_data.get("signal", "neutral") if funding_data else "neutral"
                funding_z = funding_data.get("z_score", 0) if funding_data else 0
                # Use production premium index funding rate (WS may have testnet data)
                if sym in self._premium_data:
                    funding_rate = self._premium_data[sym].get("current_rate", funding_rate)
                    # Recompute funding_bias from actual rate (WS signal may be stale)
                    if funding_rate < -0.0001:
                        funding_bias = "buy"
                    elif funding_rate > 0.0001:
                        funding_bias = "sell"
                    else:
                        funding_bias = "neutral"
                funding_rate = max(-0.05, min(0.05, funding_rate))

                current_oi = oi_data.get("current_oi", 0) if oi_data else 0
                oi_change_pct = oi_data.get("change_pct", 0) if oi_data else 0
                oi_signal = oi_data.get("signal", "neutral") if oi_data else "neutral"
                oi_regime_val = oi_data.get("oi_regime", "neutral_oi") if oi_data else "neutral_oi"
                oi_positioning_val = oi_data.get("oi_positioning", "neutral") if oi_data else "neutral"
                oi_strength_val = oi_data.get("oi_strength", 50) if oi_data else 50
                # Compute oi_bias directly from OI change + price direction (more reliable than regime alone)
                if oi_regime_val == "bullish_oi":
                    oi_bias = "buy"
                elif oi_regime_val == "bearish_oi":
                    oi_bias = "sell"
                elif abs(oi_change_pct) >= 0.005 and oi_data:
                    # Fallback: use oi_change_pct + price direction from ticker
                    price_chg_24h = float(_eff_ticker.get("price_change", 0))
                    if oi_change_pct > 0:
                        oi_bias = "buy" if price_chg_24h >= 0 else "sell"
                    else:
                        oi_bias = "sell" if price_chg_24h >= 0 else "buy"
                else:
                    oi_bias = "neutral"

                vol = getattr(self, '_vol_map', {}).get(sym, 0)
                if vol == 0:
                    # Filter out synthetic !ticker@arr trades for volume calc
                    _real_trades = [t for t in sd["trades"][-50:] if t.get("_source") != "ticker_arr"]
                    vol = sum(t.get("quantity", 0) * t.get("price", 0) for t in _real_trades)
                _trades_count = len(sd.get("trades", []))
                # Filter out synthetic ticker trades for fallback computation
                # Include ALL trades (even ticker_arr) for CVD/OF — they have real price data
                _real_trades = [t for t in sd.get("trades", []) if t.get("_source") != "ticker_arr"]
                _real_count = len(_real_trades)
                of = self.orderflow.get_analysis(sym)

                imbalance = of.get("imbalance", 0) if of else 0
                # ── VOL BIAS: use orderflow imbalance, fallback to exchange flow ──
                if of and imbalance != 0:
                    vol_bias = "buy" if imbalance > 0.1 else ("sell" if imbalance < -0.1 else "neutral")
                else:
                    # Fallback: use exchange flow ratio (taker buy vs sell pressure)
                    _ef = self.exchange_flow.get_analysis(sym) if hasattr(self, 'exchange_flow') else None
                    if _ef and _ef.get("total_trades", 0) >= 20:
                        _fr = _ef.get("flow_ratio", 0.5)
                        vol_bias = "buy" if _fr > 0.55 else ("sell" if _fr < 0.45 else "neutral")
                    else:
                        vol_bias = "neutral"

                regime = regime_data.get("regime", "range") if regime_data else "range"

                sig = next((s for s in deduped_signals if s["symbol"] == sym), None)
                signal_side = sig.get("side", "").lower() if sig else ""

                ts = last.get("time", time.time())

                # ── EXCHANGE FLOW FALLBACK: build from trade buffer if engine has no data ──
                # CRITICAL: Filter out synthetic !ticker@arr trades (inflated quantities)
                if not ef_data and _real_count >= 1:
                    _trades = sd.get("trades", [])
                    _now_ts = time.time()
                    _recent = [t for t in _trades[-1000:]
                               if t.get("_source") != "ticker_arr"
                               and _now_ts - (t.get("trade_time", 0) / 1000 if t.get("trade_time", 0) > 1e10 else t.get("trade_time", 0)) < 300]
                    if _recent:
                        _buy_v = sum(t["price"] * t["quantity"] for t in _recent if not t["is_buyer_maker"])
                        _sell_v = sum(t["price"] * t["quantity"] for t in _recent if t["is_buyer_maker"])
                        _total = _buy_v + _sell_v
                        _net = _buy_v - _sell_v
                        _ratio = _buy_v / _total if _total > 0 else 0.5
                        _ratio_dev = abs(_ratio - 0.5) * 2
                        _flow_str = 50 + _ratio_dev * 50
                        _signal = "buy" if _ratio > 0.6 else ("sell" if _ratio < 0.4 else "neutral")
                        _bias = "taker_buy" if _ratio > 0.6 else ("taker_sell" if _ratio < 0.4 else "balanced")
                        # Validate: total flow must be < 24h volume
                        _vol_24h = vol_24h_quote if vol_24h_quote > 0 else vol
                        _vol_valid = True
                        _vol_msg = ""
                        if _vol_24h > 0 and _total > _vol_24h:
                            _vol_valid = False
                            _vol_msg = f"Flow ${_total/1e9:.1f}B > Vol ${_vol_24h/1e9:.1f}B — scaled down"
                            # Scale down proportionally to stay within 24h volume
                            _scale = _vol_24h / _total * 0.95
                            _buy_v *= _scale
                            _sell_v *= _scale
                            _total = _buy_v + _sell_v
                            _net = _buy_v - _sell_v
                            _ratio = _buy_v / _total if _total > 0 else 0.5
                        ef_data = {
                            "net_flow": round(_net, 2), "aggressive_side": _bias,
                            "taker_buy_vol": round(_buy_v, 2), "taker_sell_vol": round(_sell_v, 2),
                            "recent_net_delta": round(_net, 2), "flow_ratio": round(_ratio, 4),
                            "taker_dominance": 0.5, "flow_strength_score": round(_flow_str, 1),
                            "flow_signal": _signal, "total_trades": len(_recent),
                            "source_label": "Binance Futures (fallback)", "vol_24h": round(_vol_24h, 2),
                            "vol_24h_valid": _vol_valid, "vol_validation_msg": _vol_msg,
                        }
                        # ── Piggyback OF/CVD from the same _recent list ──
                        # Orderflow fallback: build from same trade data as EF
                        _of_has_real = of and (of.get("buy_volume", 0) > 10 or of.get("sell_volume", 0) > 10)
                        if (not of) or (not _of_has_real):
                            _imb = _net / _total if _total else 0
                            _of_str = max(0, min(100, 50 + (1 if _ratio > 0.5 else -1) * _ratio_dev * 50))
                            of = {
                                "symbol": sym, "buy_volume": round(_buy_v, 2), "sell_volume": round(_sell_v, 2),
                                "delta": round(_net, 2), "cumulative_delta": round(_net, 2),
                                "imbalance": round(_imb, 4), "flow_ratio": round(_ratio, 4),
                                "flow_signal": _signal, "flow_strength_score": round(_of_str, 1),
                                "signal_strength": round(_of_str / 100.0, 3),
                                "large_buy_trades": sum(1 for t in _recent if not t["is_buyer_maker"] and t["price"] * t["quantity"] >= 10000),
                                "large_sell_trades": sum(1 for t in _recent if t["is_buyer_maker"] and t["price"] * t["quantity"] >= 10000),
                                "avg_size": round(_total / len(_recent), 2) if _recent else 0,
                                "delta_trend": 0, "vwap": 0,
                                "absorption": "none", "absorption_events": 0,
                                "sweep": "none", "sweep_events": 0,
                                "total_trades": len(_recent),
                            }
                        # CVD fallback: build from same trade data as EF
                        if not cvd_data:
                            _cvd_5m = sum((t["price"] * t["quantity"]) * (1 if not t["is_buyer_maker"] else -1)
                                          for t in _recent if _now_ts - (t.get("trade_time", 0) / 1000 if t.get("trade_time", 0) > 1e10 else t.get("trade_time", 0)) < 300)
                            _cvd_1h = _cvd_5m  # Use same window since _recent is already 5min-filtered
                            _buy_ratio = _buy_v / _total if _total > 0 else 0.5
                            cvd_data = {
                                "cvd_5m": round(_cvd_5m, 2), "cvd_1h": round(_cvd_1h, 2), "cvd_4h": round(_cvd_1h, 2),
                                "cvd_bias": "buy" if _cvd_5m > 0 else ("sell" if _cvd_5m < 0 else "neutral"),
                                "cvd_bias_5m": "buy" if _cvd_5m > 0 else ("sell" if _cvd_5m < 0 else "neutral"),
                                "cvd_bias_1m": "buy" if _cvd_5m > 0 else ("sell" if _cvd_5m < 0 else "neutral"),
                                "cvd_bias_15m": "buy" if _cvd_5m > 0 else ("sell" if _cvd_5m < 0 else "neutral"),
                                "cvd_bias_1h": "buy" if _cvd_5m > 0 else ("sell" if _cvd_5m < 0 else "neutral"),
                                "cvd_bias_4h": "buy" if _cvd_5m > 0 else ("sell" if _cvd_5m < 0 else "neutral"),
                                "cvd_divergence_5m": 0, "cvd_divergence_15m": 0,
                                "cvd_buy_ratio_5m": round(_buy_ratio, 4),
                            }

                # ── OF/CVD COMPLEMENT: compute from EF data if analyzer has no real data ──
                # EF analyzer always has data (REST fallback). Use its buy/sell volumes for OF/CVD.
                if ef_data:
                    _ef_buy = ef_data.get("taker_buy_vol", 0)
                    _ef_sell = ef_data.get("taker_sell_vol", 0)
                    _ef_total = _ef_buy + _ef_sell
                    _ef_net = _ef_buy - _ef_sell
                    # Orderflow: override if analyzer has no meaningful data
                    _of_has_real = of and (of.get("buy_volume", 0) > 10 or of.get("sell_volume", 0) > 10)
                    if (not of) or (not _of_has_real):
                        _ef_ratio = _ef_buy / _ef_total if _ef_total > 0 else 0.5
                        _ef_ratio_dev = abs(_ef_ratio - 0.5) * 2
                        _ef_of_str = max(0, min(100, 50 + (1 if _ef_ratio > 0.5 else -1) * _ef_ratio_dev * 50))
                        _ef_sig = "buy" if _ef_ratio > 0.6 else ("sell" if _ef_ratio < 0.4 else "neutral")
                        _ef_imb = _ef_net / _ef_total if _ef_total else 0
                        of = {
                            "symbol": sym, "buy_volume": round(_ef_buy, 2), "sell_volume": round(_ef_sell, 2),
                            "delta": round(_ef_net, 2), "cumulative_delta": round(_ef_net, 2),
                            "imbalance": round(_ef_imb, 4), "flow_ratio": round(_ef_ratio, 4),
                            "flow_signal": _ef_sig, "flow_strength_score": round(_ef_of_str, 1),
                            "signal_strength": round(_ef_of_str / 100.0, 3),
                            "large_buy_trades": 0, "large_sell_trades": 0,
                            "avg_size": round(_ef_total / max(ef_data.get("total_trades", 1), 1), 2),
                            "delta_trend": 0, "vwap": 0,
                            "absorption": "none", "absorption_events": 0,
                            "sweep": "none", "sweep_events": 0,
                            "total_trades": ef_data.get("total_trades", 0),
                        }
                    # CVD: override if analyzer has no data
                    if not cvd_data:
                        _ef_cvd = _ef_net  # Net delta as CVD proxy
                        _cvd_bias = "bullish" if _ef_cvd > 0 else ("bearish" if _ef_cvd < 0 else "neutral")
                        cvd_data = {
                            "cvd_5m": round(_ef_cvd, 2), "cvd_1h": round(_ef_cvd, 2), "cvd_4h": round(_ef_cvd, 2),
                            "cvd_bias": _cvd_bias,
                            "cvd_bias_5m": _cvd_bias,
                            "cvd_bias_1m": _cvd_bias,
                            "cvd_bias_15m": _cvd_bias,
                            "cvd_bias_1h": _cvd_bias,
                            "cvd_bias_4h": _cvd_bias,
                            "cvd_divergence_5m": 0, "cvd_divergence_15m": 0,
                            "cvd_buy_ratio_5m": round(_ef_buy / _ef_total, 4) if _ef_total > 0 else 0.5,
                        }

                # Convert OI from contracts to USD: contracts × mark_price = USD value
                # Use cached mark price (more accurate than last trade price for valuation)
                mark = self._mark_prices.get(sym, 0)
                oi_price = mark if mark > 0 else price
                oi_usd = current_oi * oi_price if oi_price > 0 else 0

                # Sanity validation — flag unrealistic OI values
                # Production max OI: BTC ~$40B, ETH ~$15B, others <$5B
                oi_threshold = 50_000_000_000 if sym == "BTCUSDT" else (
                    20_000_000_000 if sym == "ETHUSDT" else 5_000_000_000)
                if oi_usd > oi_threshold:
                    logger.warning("Suspicious OI USD: {} ${:.1f} (raw contracts={}, mark_price={}, last_price={})",
                                   sym, oi_usd, current_oi, mark, price)

                market_rows.append({
                    "symbol": sym,
                    "price": price,
                    "volume": vol,
                    # ── Binance full market data (API returns strings, cast to float) ──
                    # Use dynamic precision to preserve accuracy for low-price tokens
                    "mark_price": _price_round(float(self._premium_data.get(sym, {}).get("mark_price", 0)), price) if sym in self._premium_data else _price_round(price, price),
                    "index_price": _price_round(float(self._premium_data.get(sym, {}).get("index_price", 0)), price) if sym in self._premium_data else _price_round(price, price),
                    "funding_countdown": max(0, int((self._premium_data.get(sym, {}).get("next_funding_time", 0) - int(time.time() * 1000)) / 1000)) if sym in self._premium_data and self._premium_data.get(sym, {}).get("next_funding_time", 0) > 0 else 0,
                    "high_24h": _price_round(float(_eff_ticker.get("high") or 0), price),
                    "low_24h": _price_round(float(_eff_ticker.get("low") or 0), price),
                    "volume_btc": round(float(_eff_ticker.get("volume") or 0), 2),
                    "change_24h": round(float(_eff_ticker.get("change_pct") or 0), 2),
                    "change_24h_raw": round(float(_eff_ticker.get("price_change") or 0), 4),
                    "open_24h": _price_round(float(_eff_ticker.get("open") or 0), price),
                    "trades_24h": int(_eff_ticker.get("count") or 0),
                    "signal": signal_side,
                    "regime": regime,
                    # Regime — multi-timeframe fields
                    "regime_confidence_pct": round(regime_data.get("regime_confidence_pct", 50), 1) if regime_data else 50,
                    "regime_alignment": round(regime_data.get("alignment_score", 0), 3) if regime_data else 0,
                    "regime_1m": regime_data.get("tf_regimes", {}).get("1m", "") if regime_data else "",
                    "regime_5m": regime_data.get("tf_regimes", {}).get("5m", "") if regime_data else "",
                    "regime_15m": regime_data.get("tf_regimes", {}).get("15m", "") if regime_data else "",
                    "regime_1h": regime_data.get("tf_regimes", {}).get("1h", "") if regime_data else "",
                    "regime_4h": regime_data.get("tf_regimes", {}).get("4h", "") if regime_data else "",
                    "regime_conf_1m": round(regime_data.get("tf_confidences", {}).get("1m", 0), 3) if regime_data else 0,
                    "regime_conf_5m": round(regime_data.get("tf_confidences", {}).get("5m", 0), 3) if regime_data else 0,
                    "regime_conf_15m": round(regime_data.get("tf_confidences", {}).get("15m", 0), 3) if regime_data else 0,
                    "regime_conf_1h": round(regime_data.get("tf_confidences", {}).get("1h", 0), 3) if regime_data else 0,
                    "regime_conf_4h": round(regime_data.get("tf_confidences", {}).get("4h", 0), 3) if regime_data else 0,
                    # End regime enhanced
                    "funding": round(funding_rate * 100, 6),
                    "funding_bias": funding_bias if funding_bias != "neutral" else ("buy" if funding_rate < 0 else "sell"),
                    "funding_z": round(funding_z, 2),
                    "open_interest": round(oi_usd, 2),
                    "oi_bias": oi_bias,
                    "oi_change_pct": round(oi_change_pct, 2),
                    # OI — enhanced fields
                    "oi_regime": oi_data.get("oi_regime", "neutral_oi") if oi_data else "neutral_oi",
                    "oi_positioning": oi_data.get("positioning", "neutral") if oi_data else "neutral",
                    "oi_strength": round(oi_data.get("oi_strength_score", 50), 1) if oi_data else 50,
                    "oi_spike": oi_data.get("spike_detected", False) if oi_data else False,
                    "oi_flush": oi_data.get("flush_detected", False) if oi_data else False,
                    "oi_peak": round(oi_data.get("peak_oi", 0), 2) if oi_data else 0,
                    # End OI enhanced
                    # Exchange flow — enhanced fields (real aggTrade only, no synthetic)
                    "exchange_flow": round(ef_data.get("net_flow", 0), 2) if ef_data else 0,
                    "exchange_bias": ef_data.get("aggressive_side", "neutral") if ef_data else "neutral",
                    "aggressive_buy_vol": round(ef_data.get("taker_buy_vol", 0), 2) if ef_data else 0,
                    "aggressive_sell_vol": round(ef_data.get("taker_sell_vol", 0), 2) if ef_data else 0,
                    "net_delta": round(ef_data.get("recent_net_delta", 0), 2) if ef_data else 0,
                    "buy_sell_ratio": round(ef_data.get("flow_ratio", 0.5), 4) if ef_data else 0.5,
                    "taker_dominance": round(ef_data.get("taker_dominance", 0.5), 3) if ef_data else 0.5,
                    "flow_strength": round(ef_data.get("flow_strength_score", 50), 1) if ef_data else 50,
                    "flow_signal": ef_data.get("flow_signal", "neutral") if ef_data else "neutral",
                    # Exchange flow — debug panel
                    "flow_total_trades": ef_data.get("total_trades", 0) if ef_data else 0,
                    "flow_source": ef_data.get("source_label", "Binance Futures") if ef_data else "Binance Futures",
                    "flow_vol_24h": round(ef_data.get("vol_24h", 0), 2) if ef_data else 0,
                    "flow_vol_valid": ef_data.get("vol_24h_valid", True) if ef_data else True,
                    "flow_vol_msg": ef_data.get("vol_validation_msg", "") if ef_data else "",
                    # End exchange flow
                    # CVD — multi-timeframe fields
                    "cvd_bias": cvd_data.get("cvd_bias", "neutral") if cvd_data else "neutral",
                    "cvd_bias_1m": cvd_data.get("cvd_bias_1m", "neutral") if cvd_data else "neutral",
                    "cvd_bias_5m": cvd_data.get("cvd_bias_5m", "neutral") if cvd_data else "neutral",
                    "cvd_bias_15m": cvd_data.get("cvd_bias_15m", "neutral") if cvd_data else "neutral",
                    "cvd_bias_1h": cvd_data.get("cvd_bias_1h", "neutral") if cvd_data else "neutral",
                    "cvd_bias_4h": cvd_data.get("cvd_bias_4h", "neutral") if cvd_data else "neutral",
                    "cvd_5m": round(cvd_data.get("cvd_5m", 0), 2) if cvd_data else 0,
                    "cvd_1h": round(cvd_data.get("cvd_1h", 0), 2) if cvd_data else 0,
                    "cvd_4h": round(cvd_data.get("cvd_4h", 0), 2) if cvd_data else 0,
                    "cvd_divergence_5m": round(cvd_data.get("cvd_divergence_5m", 0), 4) if cvd_data else 0,
                    "cvd_divergence_15m": round(cvd_data.get("cvd_divergence_15m", 0), 4) if cvd_data else 0,
                    "cvd_buy_ratio_5m": round(cvd_data.get("cvd_buy_ratio_5m", 0.5), 4) if cvd_data else 0.5,
                    # End CVD
                    "imbalance": round(imbalance, 4),
                    "vol_bias": vol_bias,
                    # Orderflow — debug panel (real aggTrade only)
                    "of_buy_volume": round(of.get("buy_volume", 0), 2) if of else 0,
                    "of_sell_volume": round(of.get("sell_volume", 0), 2) if of else 0,
                    "of_flow_ratio": round(of.get("flow_ratio", 0.5), 4) if of else 0.5,
                    "of_flow_signal": of.get("flow_signal", "neutral") if of else "neutral",
                    "of_flow_strength": round(of.get("flow_strength_score", 50), 1) if of else 50,
                    "of_total_trades": of.get("total_trades", 0) if of else 0,
                    "of_absorption": of.get("absorption", "none") if of else "none",
                    "of_sweep": of.get("sweep", "none") if of else "none",
                    # End orderflow debug
                    "cascade_active": liq_data.get("cascade_active", False) if liq_data else False,
                    "cascade_side": liq_data.get("cascade_side", "") if liq_data else "",
                    # Liquidation — enhanced fields
                    "long_liq_vol": round(liq_data.get("long_liq_vol", 0), 2) if liq_data else 0,
                    "short_liq_vol": round(liq_data.get("short_liq_vol", 0), 2) if liq_data else 0,
                    "long_liq_count": liq_data.get("long_liq_count", 0) if liq_data else 0,
                    "short_liq_count": liq_data.get("short_liq_count", 0) if liq_data else 0,
                    "cascade_intensity": round(liq_data.get("cascade_intensity", 0), 3) if liq_data else 0,
                    "cluster_count": liq_data.get("cluster_count", 0) if liq_data else 0,
                    "sweep_detected": liq_data.get("sweep_detected", False) if liq_data else False,
                    "sweep_direction": liq_data.get("sweep_direction", "") if liq_data else "",
                    "sweep_intensity": round(liq_data.get("sweep_intensity", 0), 3) if liq_data else 0,
                    "liq_risk": round(liq_data.get("liq_risk", 0), 1) if liq_data else 0,
                    "liq_risk_level": liq_data.get("liq_risk_level", "low") if liq_data else "low",
                    # End liquidation enhanced
                    # ── FVG Detector — real Fair Value Gap data ──
                    "fvg_alignment": fvg_det_data.get("fvg_alignment", "neutral") if fvg_det_data else "neutral",
                    "fvg_type": fvg_det_data.get("latest_fvg_type", "none") if fvg_det_data else "none",
                    "fvg_score": round(fvg_det_data.get("fvg_score", 50), 1) if fvg_det_data else 50,
                    "fvg_bull_count": fvg_det_data.get("unfilled_bullish_count", 0) if fvg_det_data else 0,
                    "fvg_bear_count": fvg_det_data.get("unfilled_bearish_count", 0) if fvg_det_data else 0,
                    # FVG price levels — actual gap boundaries from detector
                    "fvg_gap_high": round(fvg_det_data.get("fvg_gap_high", 0), 4) if fvg_det_data else 0,
                    "fvg_gap_low": round(fvg_det_data.get("fvg_gap_low", 0), 4) if fvg_det_data else 0,
                    "fvg_gap_size": round(fvg_det_data.get("fvg_gap_size", 0), 6) if fvg_det_data else 0,
                    "fvg_latest_strength": round(fvg_det_data.get("fvg_latest_strength", 0), 2) if fvg_det_data else 0,
                    # ── Sweep Detector — real sweep data from price action ──
                    "sw_signal": sweep_det_data.get("signal", "neutral") if sweep_det_data else "neutral",
                    "sw_recent_count": sweep_det_data.get("recent_sweep_count", 0) if sweep_det_data else 0,
                    "sw_high_sweeps": sweep_det_data.get("high_sweeps", 0) if sweep_det_data else 0,
                    "sw_low_sweeps": sweep_det_data.get("low_sweeps", 0) if sweep_det_data else 0,
                    "sw_avg_confidence": round(sweep_det_data.get("avg_confidence", 0), 2) if sweep_det_data else 0,
                    "sw_last_side": sweep_det_data.get("last_sweep_side", "") if sweep_det_data else "",
                    # Sweep price levels — actual sweep event prices from detector
                    "sweep_price": round(sweep_det_data.get("sweep_price", 0), 4) if sweep_det_data else 0,
                    "sweep_reject_price": round(sweep_det_data.get("sweep_reject_price", 0), 4) if sweep_det_data else 0,
                    "date": time.strftime("%Y-%m-%d", time.localtime(ts)),
                    "time": time.strftime("%H:%M:%S", time.localtime(ts)),
                    "timestamp": ts,
                    # ── Additional dashboard fields ──
                    "volume_24h": round(vol, 2),
                    "change_1h": 0.0,  # computed from klines below
                    "change_4h": 0.0,  # computed from klines below
                    "spread": 0.0,  # computed from best bid/ask if available
                    "confidence": round(sig.get("confidence", 0) if sig else 0, 3),
                    "institutional_score": round(sig.get("institutional_score", 0) if sig else 0, 1),
                    "absorption_score": round(of.get("absorption_score", 0) if of else 0, 2),
                    "sweep_score": round(of.get("sweep_score", 0) if of else 0, 2),
                    "smart_money_score": 0.0,  # computed from smart_money engine
                })

            # ── Normalize liq_risk to percentile rank across all symbols ──
            # This produces differentiated risk values even when absolute scores are similar
            if market_rows:
                raw_risks = [(i, r.get("liq_risk", 0)) for i, r in enumerate(market_rows)]
                sorted_risks = sorted(raw_risks, key=lambda x: x[1])
                n = len(sorted_risks)
                for rank, (idx, _) in enumerate(sorted_risks):
                    # Percentile rank: 0 (lowest) to 100 (highest)
                    if n > 1:
                        pct = rank / (n - 1) * 100
                    else:
                        pct = 50
                    market_rows[idx]["liq_risk"] = round(pct, 1)
                    # Update risk level based on normalized percentile
                    if pct >= 70:
                        market_rows[idx]["liq_risk_level"] = "high"
                    elif pct >= 30:
                        market_rows[idx]["liq_risk_level"] = "medium"
                    else:
                        market_rows[idx]["liq_risk_level"] = "low"

            # ═══════════════════════════════════════════════════════════════
            # DATA INTEGRITY GUARD — Validate ALL fields before bridge write
            # Prevents corrupt/fake data from reaching the dashboard
            # ═══════════════════════════════════════════════════════════════
            market_rows = integrity_guard.validate(market_rows)

            bridge_writer.write_market_data(market_rows)
        except Exception as e:
            logger.warning("⚠️ Bridge sync error (market_data): {}", e)

        # ── 5. Equity history (with proper drawdown) ──
        try:
            equity_history = getattr(self, '_equity_history', []) or []
            equity_history.append({
                "timestamp": time.time(),
                "equity": round(portfolio_value, 2),
                "pnl": round(realized_pnl + total_unrealized, 2),
                "realized_pnl": round(realized_pnl, 2),
                "unrealized_pnl": round(total_unrealized, 2),
                "drawdown": round(-max_drawdown_pct, 1),  # negative = drawdown
                "peak_equity": round(self._equity_peak, 2),
            })
            if len(equity_history) > 2000:
                equity_history = equity_history[-2000:]
            self._equity_history = equity_history
            bridge_writer.write_equity_history(equity_history)
        except Exception as e:
            logger.debug("Bridge sync error (equity): {}", e)

        # ── 6. Open positions (already computed above) ──
        try:
            bridge_writer.write_positions(positions)
        except Exception as e:
            logger.debug("Bridge sync error (positions): {}", e)

        # ── 7. Trade history ──
        try:
            bridge_writer.write_trade_history(closed)
        except Exception as e:
            logger.debug("Bridge sync error (trade_history): {}", e)

        # ── 8. Market intelligence ──
        try:
            intel = self._build_market_intelligence()
            bridge_writer.write_market_intelligence(intel)
        except Exception as e:
            logger.debug("Bridge sync error (intel): {}", e)

        # ── 9. Smart Money Price Map ──
        try:
            # Build per-symbol lookup from already-computed market data
            _md_by_sym = {}
            for _mr in (market_rows if 'market_rows' in dir() else []):
                _md_by_sym[_mr["symbol"]] = _mr

            sm_rows = []
            for sym in self.active_symbols:
                # Use market data for price (REST-based, always available)
                _md = _md_by_sym.get(sym, {})
                price = _md.get("price", 0)
                if price <= 0:
                    # Fallback: try trade data
                    sd = self.symbol_data.get(sym, {})
                    if sd.get("trades"):
                        price = sd["trades"][-1].get("price", 0)
                    if price <= 0:
                        continue

                sm = self.smart_money.get_analysis(sym)
                # Merge patterns from both scoring engine + real detector
                inst_scoring = self.institutional.get_patterns(sym)
                inst_detect = self.institutional_detector.get_patterns(sym)
                walls = []
                if isinstance(inst_scoring, dict):
                    walls.extend(inst_scoring.get("walls", []))
                if isinstance(inst_detect, list):
                    walls.extend(inst_detect)
                elif isinstance(inst_detect, dict):
                    walls.extend(inst_detect.get("walls", []))
                sweep = self.sweep.get_analysis(sym)
                absorb = self.absorption.get_analysis(sym)
                spoof = self.spoof_iceberg.get_analysis(sym) if hasattr(self.spoof_iceberg, 'get_analysis') else None
                liq = self.liquidity_map.get_analysis(sym) if hasattr(self.liquidity_map, 'get_analysis') else None

                # Feed external detector data into smart money engine
                sweep_conf = sweep.get("avg_confidence", 0) if sweep else 0
                abs_conf = absorb.get("avg_confidence", 0) if absorb else 0
                iceberg_conf = 0
                if spoof:
                    iceberg_events = [e for e in (spoof.get("events", []) if isinstance(spoof, dict) else []) 
                                     if (isinstance(e, dict) and e.get("event_type") == "iceberg")]
                    iceberg_conf = len(iceberg_events) / 10 if iceberg_events else 0
                liq_pool = 0
                if liq:
                    levels = liq.get("levels", []) if isinstance(liq, dict) else []
                    liq_pool = min(len(levels) / 10, 1.0) if levels else 0
                self.smart_money.update_external_signals(
                    sym,
                    sweep_confidence=sweep_conf,
                    absorption_confidence=abs_conf,
                    iceberg_confidence=min(iceberg_conf, 1.0),
                    liquidity_pool_score=liq_pool,
                )
                # Re-read after updating external signals
                sm = self.smart_money.get_analysis(sym)

                # Build price level entries
                price_levels = []

                # Hidden orders from smart money
                if sm:
                    for ho in sm.get("hidden_orders", []):
                        price_levels.append({
                            "price": ho["price"],
                            "type": ho["type"],
                            "source": "smart_money",
                            "strength": ho.get("strength", 0),
                            "side": "buy" if ho["type"] == "accumulation" else "sell",
                        })

                # Institutional patterns (iceberg, absorption, sweep, spoofing)
                for p in walls:
                    price_levels.append({
                        "price": p.get("price", 0),
                        "type": p.get("type", "unknown"),
                        "source": "institutional",
                        "strength": p.get("confidence", 0),
                        "side": p.get("side", "neutral"),
                    })

                # Absorption levels
                if absorb:
                    for tl in absorb.get("top_levels", []):
                        price_levels.append({
                            "price": tl.get("price", 0),
                            "type": "absorption",
                            "source": "absorption",
                            "strength": tl.get("vol", 0) / 1_000_000,
                            "side": "buy" if "bid" in tl.get("side", "") else "sell",
                        })

                # Liquidity map levels
                if liq:
                    for lvl in liq.get("support_levels", [])[:3]:
                        price_levels.append({
                            "price": lvl,
                            "type": "support",
                            "source": "liquidity_map",
                            "strength": 0.6,
                            "side": "buy",
                        })
                    for lvl in liq.get("resistance_levels", [])[:3]:
                        price_levels.append({
                            "price": lvl,
                            "type": "resistance",
                            "source": "liquidity_map",
                            "strength": 0.6,
                            "side": "sell",
                        })
                    # POC (Point of Control)
                    if liq.get("poc", 0) > 0:
                        price_levels.append({
                            "price": liq["poc"],
                            "type": "poc",
                            "source": "liquidity_map",
                            "strength": 0.9,
                            "side": "neutral",
                        })

                # ── Derive Smart Money scores from real market data ──
                # When WebSocket trade pipeline isn't feeding SmartMoneyEngine,
                # compute scores from orderflow, exchange flow, CVD, OI data.
                _of_buy = _md.get("aggressive_buy_vol", 0)
                _of_sell = _md.get("aggressive_sell_vol", 0)
                _of_total = _of_buy + _of_sell
                _of_ratio = _md.get("buy_sell_ratio", 0.5)
                _ef_net = _md.get("exchange_flow", 0) or _md.get("net_delta", 0)
                _cvd_bias = _md.get("cvd_bias", "neutral")
                _oi_pos = _md.get("oi_positioning", "neutral")
                _oi_chg = _md.get("oi_change_pct", 0) or 0
                _flow_str = _md.get("flow_strength", 50) or 50
                _flow_sig = _md.get("flow_signal", "neutral")

                # Accumulation score: buy dominance from orderflow + CVD + OI
                _sm_accum = 0.0
                if _of_ratio > 0.55:
                    _sm_accum += min((_of_ratio - 0.5) * 2, 1.0) * 0.4
                if _cvd_bias in ("buy", "bullish"):
                    _sm_accum += 0.25
                if "long" in str(_oi_pos).lower():
                    _sm_accum += 0.2
                if _ef_net > 0:
                    _sm_accum += min(abs(_ef_net) / max(_of_total, 1), 0.15)
                _sm_accum = min(_sm_accum, 1.0)

                # Distribution score: sell dominance
                _sm_distrib = 0.0
                if _of_ratio < 0.45:
                    _sm_distrib += min((0.5 - _of_ratio) * 2, 1.0) * 0.4
                if _cvd_bias in ("sell", "bearish"):
                    _sm_distrib += 0.25
                if "short" in str(_oi_pos).lower():
                    _sm_distrib += 0.2
                if _ef_net < 0:
                    _sm_distrib += min(abs(_ef_net) / max(_of_total, 1), 0.15)
                _sm_distrib = min(_sm_distrib, 1.0)

                # Stealth buys/sells: estimate from actual aggressive volumes
                _large_buy = _md.get("aggressive_buy_vol", 0) or 0
                _large_sell = _md.get("aggressive_sell_vol", 0) or 0
                _vol_total = _large_buy + _large_sell
                # Stealth buys: count of aggressive buy volume units ($10K each)
                _sm_stealth_buys = int(_large_buy / 10000) if _large_buy > 0 else 0
                # Stealth sells: count of aggressive sell volume units ($10K each)
                _sm_stealth_sells = int(_large_sell / 10000) if _large_sell > 0 else 0

                # Institutional flow: exchange net delta
                _sm_inst_flow = _ef_net

                # Whale confidence: from OI change magnitude + flow strength + volume
                _whale_raw = self._safe_prob_val(self.prob_whale.get_probability(sym), "confidence")
                if _whale_raw > 0:
                    _sm_whale_conf = _whale_raw
                else:
                    # Combine OI change, flow strength, and funding for whale signal
                    _oi_factor = min(abs(_oi_chg) / 0.5, 1.0) if _oi_chg else 0  # 0.5% OI change = max
                    _flow_factor = min(_flow_str / 100, 1.0) if _flow_str else 0
                    _funding = _md.get("funding", 0) or 0
                    _funding_factor = min(abs(_funding) / 0.05, 1.0) if _funding else 0  # 5% funding = max
                    _sm_whale_conf = min((_oi_factor * 0.4 + _flow_factor * 0.3 + _funding_factor * 0.3), 1.0)

                # Sweep and absorption confidence (from per-symbol detectors)
                _sweep_conf_raw = sweep_conf
                _abs_conf_raw = abs_conf

                # Absorption score: from detector + orderflow imbalance + OI positioning
                _sm_absorb_score = _abs_conf_raw if _abs_conf_raw > 0 else 0.0
                # Boost from absorption detector signal
                if _sm_absorb_score == 0 and absorb and absorb.get("signal", "none") not in ("none", "neutral"):
                    _sm_absorb_score = min(absorb.get("total_absorptions", 0) / 5, 1.0)
                # Derive from orderflow: volume imbalance + sweep rejection = absorption
                if _sm_absorb_score == 0 and _vol_total > 0:
                    _of_abs = _md.get("of_absorption", "none") or "none"
                    if _of_abs != "none":
                        _sm_absorb_score = 0.5
                    # Sweep rejection = price absorbed at level (sweep_confidence high)
                    elif _sweep_conf_raw > 0.5:
                        _sm_absorb_score = min(_sweep_conf_raw * 0.4, 0.5)
                    # OI building while price flat = absorption
                    elif _oi_pos in ("long_buildup", "short_buildup") and abs(_oi_chg) > 0.01:
                        _sm_absorb_score = min(abs(_oi_chg) / 0.3, 0.6)
                # Volume imbalance as absorption proxy (lowered thresholds)
                if _sm_absorb_score == 0 and _vol_total > 5000:
                    _imbalance = abs(_large_buy - _large_sell) / _vol_total
                    if _imbalance > 0.4:
                        _sm_absorb_score = min(_imbalance * 0.6, 0.5)

                # Smart money side: use dominant direction
                if _sm_accum > 0.2 and _sm_accum > _sm_distrib * 1.2:
                    _sm_side = "accumulating"
                elif _sm_distrib > 0.2 and _sm_distrib > _sm_accum * 1.2:
                    _sm_side = "distributing"
                elif _sm_accum > 0.3:
                    _sm_side = "accumulating"
                elif _sm_distrib > 0.3:
                    _sm_side = "distributing"
                elif _sm_inst_flow > 1e7:
                    _sm_side = "accumulating"
                elif _sm_inst_flow < -1e7:
                    _sm_side = "distributing"
                else:
                    _sm_side = "neutral"

                # Active signals list
                _active_sigs = []
                if _sm_accum > 0.2: _active_sigs.append("accumulation")
                if _sm_distrib > 0.2: _active_sigs.append("distribution")
                if _sweep_conf_raw > 0.3: _active_sigs.append("sweep_active")
                if _sm_absorb_score > 0.3: _active_sigs.append("absorption_active")
                if _sm_whale_conf > 0.3: _active_sigs.append("whale_active")
                if abs(_sm_inst_flow) > 1e6:
                    _active_sigs.append("flow_buying" if _sm_inst_flow > 0 else "flow_selling")

                # Override WebSocket scores with derived values when WS scores are zero
                _final_accum = round(sm.get("accumulation_score", 0), 4) if sm else 0
                _final_distrib = round(sm.get("distribution_score", 0), 4) if sm else 0
                _final_stealth_b = sm.get("stealth_buys", 0) if sm else 0
                _final_stealth_s = sm.get("stealth_sells", 0) if sm else 0
                _final_inst_flow = round(sm.get("institutional_flow", 0), 2) if sm else 0
                _final_side = sm.get("smart_money_side", "neutral") if sm else "neutral"
                if _final_accum == 0 and _final_distrib == 0:
                    _final_accum = round(_sm_accum, 4)
                    _final_distrib = round(_sm_distrib, 4)
                if _final_stealth_b == 0 and _final_stealth_s == 0:
                    _final_stealth_b = _sm_stealth_buys
                    _final_stealth_s = _sm_stealth_sells
                if _final_inst_flow == 0:
                    _final_inst_flow = round(_sm_inst_flow, 2)
                if _final_side == "neutral" and _sm_side != "neutral":
                    _final_side = _sm_side

                # Recompute strength from all sources
                _sm_strength = 0.0
                _sm_strength += max(_final_accum, _final_distrib) * 25
                _sm_strength += min((_final_stealth_b + _final_stealth_s) / 20, 1.0) * 15
                _sm_strength += min((sm.get("hidden_order_depth", 0) if sm else 0) / 10, 1.0) * 10
                _sm_strength += max(sm.get("reaccumulation_score", 0) if sm else 0, sm.get("redistribution_score", 0) if sm else 0) * 15
                _sm_strength += _sweep_conf_raw * 10
                _sm_strength += _sm_absorb_score * 10
                _sm_strength += min(iceberg_conf, 1.0) * 10
                _sm_strength += liq_pool * 5
                if _final_inst_flow != 0:
                    _sm_strength += min(abs(_final_inst_flow) / 1e9, 1.0) * 10
                _sm_strength = min(round(_sm_strength, 1), 100)
                _sm_level = "strong" if _sm_strength >= 60 else ("moderate" if _sm_strength >= 30 else "weak")

                # Merge active signals
                _ws_sigs = sm.get("active_signals", []) if sm else []
                _all_sigs = list(set(_active_sigs + _ws_sigs))

                # ── Derive probabilities from real market data ──
                # WS probability detectors need trade data that isn't flowing,
                # so derive from the same real market data used for scores
                _ws_inst_prob = self._safe_prob_val(self.prob_inst.get_probability(sym), "institutional_probability")
                _ws_accum_prob = self._safe_prob_val(self.prob_accum.get_probability(sym), "accumulation_probability")
                _ws_whale_prob = self._safe_prob_val(self.prob_whale.get_probability(sym), "whale_probability")
                
                # Institutional probability: flow strength + volume magnitude + OI change
                _inst_vol = _of_buy + _of_sell
                _inst_prob_derived = min((_flow_str / 100 * 0.4 + min(_inst_vol / 50000, 1.0) * 0.3 + (abs(_oi_chg) / 0.5 if _oi_chg else 0) * 0.3), 1.0) if _flow_str else 0
                
                # Accumulation probability: derived from accum/distrib scores
                _accum_prob_derived = _final_accum if _final_accum > _final_distrib else 0

                sm_rows.append({
                    "symbol": sym,
                    "price": price,
                    # Existing fields (backward compatible)
                    "accumulation_score": _final_accum,
                    "distribution_score": _final_distrib,
                    "smart_money_side": _final_side,
                    "stealth_buys": _final_stealth_b,
                    "stealth_sells": _final_stealth_s,
                    "institutional_flow": _final_inst_flow,
                    "sweep_signal": sweep.get("signal", "neutral") if sweep else "neutral",
                    "sweep_count": sweep.get("recent_sweep_count", 0) if sweep else 0,
                    "absorption_signal": absorb.get("signal", "neutral") if absorb else "neutral",
                    "absorption_count": absorb.get("total_absorptions", 0) if absorb else 0,
                    "pattern_count": len(walls),
                    "price_levels": price_levels,
                    # Extended fields
                    "reaccumulation_score": round(sm.get("reaccumulation_score", 0), 4) if sm else 0,
                    "redistribution_score": round(sm.get("redistribution_score", 0), 4) if sm else 0,
                    "hidden_order_depth": sm.get("hidden_order_depth", 0) if sm else 0,
                    "liquidity_pool_score": round(sm.get("liquidity_pool_score", 0), 4) if sm else 0,
                    "sweep_confidence": round(_sweep_conf_raw, 4),
                    "absorption_confidence": round(_sm_absorb_score, 4),
                    "absorption_score": round(_sm_absorb_score, 4),
                    "iceberg_confidence": round(min(iceberg_conf, 1.0), 4),
                    # Strength score
                    "smart_money_strength": _sm_strength,
                    "strength_level": _sm_level,
                    "active_signals": _all_sigs,
                    "signal_count": len(_all_sigs),
                    # Probability-based detector outputs — use WS if available, else derived
                    "inst_probability": max(_ws_inst_prob, round(_inst_prob_derived, 4)),
                    "inst_confidence": self._safe_prob_val(self.prob_inst.get_probability(sym), "confidence"),
                    "accum_probability": max(_ws_accum_prob, round(_accum_prob_derived, 4)),
                    "accum_confidence": self._safe_prob_val(self.prob_accum.get_probability(sym), "confidence"),
                    "whale_probability": max(_ws_whale_prob, round(_sm_whale_conf, 4)),
                    "whale_confidence": round(_sm_whale_conf, 4),
                    # Real market data for SM matrix
                    "buy_sell_ratio": round(_of_ratio, 4),
                    "cvd_bias": _cvd_bias,
                    "oi_positioning": _oi_pos,
                    "oi_change_pct": round(_oi_chg, 2),
                    "exchange_flow": round(_ef_net, 2),
                    "flow_signal": ef_data.get("flow_signal", "neutral") if ef_data else "neutral",
                    "flow_strength": round(ef_data.get("flow_strength_score", 50), 1) if ef_data else 50,
                    "aggressive_buy_vol": round(_of_buy, 2),
                    "aggressive_sell_vol": round(_of_sell, 2),
                })
            bridge_writer.write_smart_money_map(sm_rows)
        except Exception as e:
            logger.debug("Bridge sync error (smart_money_map): {}", e)

        # ── 10. Data Quality Validation Status ──
        try:
            dq_data = self.data_quality.get_dashboard_data()
            bridge_writer.write_data_quality(dq_data)
        except Exception as e:
            logger.debug("Bridge sync error (data_quality): {}", e)

        # ── 11. Alerts (engine health + data quality warnings) ──
        try:
            _alerts = []
            # Data quality issues as alerts
            dq_issues = dq_data.get("recent_issues", []) if isinstance(dq_data, dict) else []
            for issue in dq_issues[-10:]:
                sym = issue.get("symbol", "")
                _alerts.append({
                    "level": issue.get("severity", "info"),
                    "title": f"⚠️ {sym}: {issue.get('category', 'Data Issue')}",
                    "message": f"{sym}: {issue.get('message', '')}",
                    "symbol": sym,
                    "category": "data_quality",
                    "timestamp": issue.get("time", time.time()),
                })
            # Engine health alert
            _alerts.append({
                "level": "success",
                "title": f"⚙️ Engine Running",
                "message": f"Engine running — {len(self.active_symbols)} symbols, {n_signals} signals, {n_open} positions",
                "symbol": "",
                "category": "system",
                "timestamp": time.time(),
            })
            bridge_writer.write_alerts(_alerts)
        except Exception as e:
            logger.debug("Bridge sync error (alerts): {}", e)

        # ── 12. EMA_V5 Scanner State ──
        try:
            ema_v5_data = self.ema_v5.get_bridge_data()
            # NOTE: get_bridge_data() now returns scanner's own signal history
            # which persists even when engine's self.signals expires them.
            # Merge any fresh signals from engine that aren't already in scanner history.
            _scanner_sigs = {s.get("symbol", ""): s for s in ema_v5_data.get("signals", [])}
            for s in deduped_signals:
                if s.get("strategy_version") == "ema_v5":
                    _sym = s.get("symbol", "")
                    if _sym not in _scanner_sigs:
                        ema_v5_data.setdefault("signals", []).append(s)
            ema_v5_data["signals"] = sorted(
                ema_v5_data.get("signals", []),
                key=lambda x: x.get("timestamp", 0) or 0,
                reverse=True,
            )
            # Add scan timing from last scan cycle
            ema_v5_data["scanner"]["last_scan_time"] = time.time()
            # Debug: trace EMA_V5 signals reaching bridge
            _ema_count = len(ema_v5_data.get("signals", []))
            if _ema_count:
                logger.debug(
                    "📊 EMA_V5 BRIDGE: {} signals in bridge (scanner_history + engine)",
                    _ema_count,
                )
            # ── PRODUCTION DIAGNOSTICS: Add comprehensive diagnostics to bridge ──
            try:
                ema_v5_data["diagnostics"] = self.ema_v5.get_diagnostics()
            except Exception as _diag_err:
                logger.debug("Diagnostics bridge error: {}", _diag_err)
            # Health metrics
            _ema_status = raw_status if 'raw_status' in dir() else {}
            ema_v5_data["health"] = {
                "engine_running": self.is_running,
                "api_connected": True,
                "ws_connected": getattr(self, '_ws_connected', True),
                "db_connected": True,
                "error_count": getattr(self, '_ema_v5_errors', 0),
            }
            bridge_writer.write_ema_v5(ema_v5_data)
        except Exception as e:
            logger.debug("Bridge sync error (ema_v5): {}", e)

    @staticmethod
    def _safe_prob_val(prob_result: Dict, field: str) -> float:
        """Safely extract a probability value, returning 0.0 on NaN/Inf/error."""
        try:
            import math
            val = prob_result.get(field, 0)
            return round(float(val), 4) if math.isfinite(float(val)) else 0.0
        except Exception:
            return 0.0

    def _build_market_intelligence(self) -> Dict:
        """Build aggregated market intelligence for dashboard heatmaps/analytics."""
        try:
            # Regime distribution from ALL active symbols (not just signals)
            regime_counts: Dict[str, int] = {}
            for sym in self.active_symbols:
                regime_data = self.regime.get_regime(sym) if hasattr(self.regime, 'get_regime') else None
                r = regime_data.get("regime", "unknown") if regime_data else "unknown"
                regime_counts[r] = regime_counts.get(r, 0) + 1
            # Also count from signals
            for sig in self.signals:
                r = sig.get("regime", "unknown")
                regime_counts[r] = regime_counts.get(r, 0) + 1

            # Price changes from symbol data
            symbol_perf: Dict[str, Dict] = {}
            for sym, sd in self.symbol_data.items():
                trades_list = sd.get("trades", [])
                if trades_list:
                    first_price = trades_list[0].get("price", 0)
                    last_price = trades_list[-1].get("price", 0)
                    if first_price > 0:
                        pct_change = (last_price - first_price) / first_price * 100
                    else:
                        pct_change = 0
                    volume = sum(t.get("qty", 0) * t.get("price", 0) for t in trades_list[-100:])
                    raw_regime = self.regime.get_regime(sym) if hasattr(self.regime, 'get_regime') else None
                    if isinstance(raw_regime, dict):
                        regime_name = raw_regime.get("regime", "unknown")
                        regime_conf = raw_regime.get("confidence", 0)
                        regime_align = raw_regime.get("alignment_score", 0)
                        regime_vol = raw_regime.get("volatility", 0)
                        regime_trend = raw_regime.get("trend_strength", 0)
                    else:
                        regime_name = str(raw_regime) if raw_regime else "unknown"
                        regime_conf = 0
                        regime_align = 0
                        regime_vol = 0
                        regime_trend = 0
                    symbol_perf[sym] = {
                        "price_change_pct": round(pct_change, 4),
                        "change_24h": round(pct_change, 2),
                        "volume": round(volume, 2),
                        "trade_count": len(trades_list),
                        "regime": regime_name,
                        "confidence": regime_conf,
                        "alignment_score": regime_align,
                        "volatility": regime_vol,
                        "trend_strength": regime_trend,
                    }

            # Orderflow aggregations
            flow_summary: Dict[str, Dict] = {}
            for sym in list(self.symbol_data.keys())[:50]:
                of_data = self.orderflow.get_analysis(sym) if hasattr(self.orderflow, 'get_analysis') else {}
                if of_data:
                    flow_summary[sym] = {
                        "buy_volume": of_data.get("buy_volume", 0),
                        "sell_volume": of_data.get("sell_volume", 0),
                        "delta": of_data.get("cumulative_delta", 0),
                        "imbalance": of_data.get("imbalance", 0),
                    }

            return {
                "regime_distribution": regime_counts,
                "symbol_performance": symbol_perf,
                "flow_summary": flow_summary,
                "total_signals": len(self.signals),
                "active_symbols": len(self.active_symbols),
            }
        except Exception as e:
            logger.debug("Market intelligence build error: {}", e)
            return {}
