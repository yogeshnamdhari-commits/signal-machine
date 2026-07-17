"""
Phase 3 — Live Paper Trading Validation Engine

Institutional-grade paper trading system that runs the full signal generation
pipeline against live Binance Futures market data with simulated execution.

NO REAL ORDERS. NO REAL CAPITAL. NO EXCHANGE EXECUTION.

Tracks:
- Every signal generated
- Every simulated trade (entry/exit/PnL/slippage/fees)
- Execution quality (win rate, PF, streaks, R:R)
- Market impact (expected vs actual fill prices)
- System health (API errors, reconnects, latency, memory)
- Daily/Weekly/Final performance reports

State is persisted to disk for restart recovery.
"""
from __future__ import annotations

import asyncio
import csv
import json
import os
import signal
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import numpy as np

# Ensure ai-engine is on path
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from loguru import logger

# Optional matplotlib (deferred import for chart generation)
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
from config import config
from exchanges.binance_ws import BinanceWebSocket
from scanner.orderflow import OrderFlowAnalyzer
from scanner.cumulative_delta import CumulativeDeltaEngine
from core.cvd_engine import CVDEngine
from scanner.dom_analytics import DOMAnalytics
from scanner.funding_rate import FundingRateEngine
from scanner.open_interest import OpenInterestEngine
from scanner.exchange_flow import ExchangeFlowEngine
from scanner.liquidation import LiquidationEngine
from scanner.symbol_scanner import AutoSymbolScanner
from core.institutional_engine import InstitutionalEngine
from core.institutional_scoring_engine import InstitutionalScoringEngine
from scanner.smart_money import SmartMoneyEngine
from scanner.sweep_detector import SweepDetector
from scanner.absorption_detector import AbsorptionDetector
from scanner.spoofing_iceberg import SpoofingIcebergDetector
from scanner.liquidity_map import LiquidityMappingEngine
from scanner.regime import MarketRegimeDetector
from scanner.ai_scorer import AIConfidenceScorer
from execution.risk_engine import RiskEngine
from scanner.position_sizing import PositionSizingEngine
from scanner.entry_confirmation import EntryConfirmationEngine

# ── Constants ────────────────────────────────────────────────────
STARTING_EQUITY = 10_000.0
RISK_PER_TRADE_PCT = 1.0
MAX_OPEN_POSITIONS = 5
MAX_PORTFOLIO_RISK_PCT = 5.0
DEFAULT_LEVERAGE = 10
MAKER_FEE = 0.0002   # 0.02% Binance futures maker
TAKER_FEE = 0.0004   # 0.04% Binance futures taker
SLIPPAGE_BPS = 2.0    # 2 basis points simulated slippage
MIN_DURATION_DAYS = 14
PREFERRED_DURATION_DAYS = 30

# Success criteria thresholds
CRITERIA_PF = 1.30
CRITERIA_WR = 0.48
CRITERIA_DD = 10.0   # percent
CRITERIA_UPTIME = 0.99

# Report intervals
RISK_CHECK_SEC = 1
HEALTH_CHECK_SEC = 30
STATE_SAVE_SEC = 60
DAILY_REPORT_HOUR = 0  # Midnight UTC

# Data paths
DATA_DIR = _ai_root / "data" / "reports"
STATE_FILE = DATA_DIR / "paper_trading_state.json"
TRADES_CSV = DATA_DIR / "paper_trading_trades.csv"
SIGNALS_CSV = DATA_DIR / "paper_trading_signals.csv"
DAILY_CSV = DATA_DIR / "paper_trading_daily.csv"
WEEKLY_CSV = DATA_DIR / "paper_trading_weekly.csv"
SUMMARY_JSON = DATA_DIR / "paper_trading_summary.json"
FIGURES_DIR = DATA_DIR / "figures"


# ══════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════

@dataclass
class PaperSignal:
    """Record of every signal generated during paper trading."""
    id: str
    timestamp: float
    symbol: str
    side: str                   # LONG / SHORT
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float
    institutional_score: float
    market_regime: str
    position_size: float = 0.0
    status: str = "generated"   # generated / filled / expired / rejected
    rejection_reason: str = ""
    filled_at: float = 0.0
    mtf_alignment: int = 0
    risk_reward: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class PaperTrade:
    """Record of every simulated trade."""
    id: str
    signal_id: str
    symbol: str
    side: str
    entry_time: float
    exit_time: float = 0.0
    duration_min: float = 0.0
    entry_price: float = 0.0
    expected_entry: float = 0.0
    exit_price: float = 0.0
    expected_exit: float = 0.0
    quantity: float = 0.0
    leverage: int = DEFAULT_LEVERAGE
    gross_pnl: float = 0.0
    net_pnl: float = 0.0
    return_pct: float = 0.0
    entry_slippage: float = 0.0
    exit_slippage: float = 0.0
    total_slippage: float = 0.0
    fees: float = 0.0
    drawdown: float = 0.0
    exit_reason: str = ""
    stop_loss: float = 0.0
    take_profit: float = 0.0
    confidence: float = 0.0
    institutional_score: float = 0.0
    market_regime: str = ""
    status: str = "open"        # open / closed

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ExecutionQuality:
    """Aggregated execution quality metrics."""
    signal_count: int = 0
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    risk_reward: float = 0.0
    avg_hold_time_min: float = 0.0
    longest_win_streak: int = 0
    longest_loss_streak: int = 0
    total_gross_pnl: float = 0.0
    total_net_pnl: float = 0.0
    total_fees: float = 0.0
    total_slippage: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown_pct: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class DailyReport:
    """Daily performance snapshot."""
    date: str
    signals_generated: int = 0
    trades_opened: int = 0
    trades_closed: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    net_pnl: float = 0.0
    drawdown_pct: float = 0.0
    open_positions: int = 0
    api_errors: int = 0
    reconnects: int = 0
    latency_ms: float = 0.0
    equity: float = 0.0
    exposure_pct: float = 0.0
    open_risk_pct: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class WeeklyReport:
    """Weekly performance snapshot."""
    week_start: str
    week_end: str
    win_rate: float = 0.0
    profit_factor: float = 0.0
    net_pnl: float = 0.0
    drawdown_pct: float = 0.0
    best_symbol: str = ""
    worst_symbol: str = ""
    best_regime: str = ""
    worst_regime: str = ""
    trades: int = 0
    signals: int = 0
    execution_quality: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class SystemHealth:
    """System health metrics snapshot."""
    api_errors: int = 0
    reconnect_events: int = 0
    ws_disconnects: int = 0
    memory_mb: float = 0.0
    cpu_pct: float = 0.0
    latency_ms: float = 0.0
    msg_processing_delay_ms: float = 0.0
    queue_backlog: int = 0
    dropped_messages: int = 0
    uptime_sec: float = 0.0
    uptime_pct: float = 100.0
    total_messages: int = 0
    last_error: str = ""
    last_error_time: float = 0.0

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class PaperTradingSummary:
    """Final paper trading validation summary."""
    start_time: float = 0.0
    end_time: float = 0.0
    duration_days: float = 0.0
    total_signals: int = 0
    total_trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    net_profit: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_slippage_bps: float = 0.0
    api_errors: int = 0
    reconnects: int = 0
    uptime_pct: float = 0.0
    performance_drift: Dict = field(default_factory=dict)
    criteria: Dict = field(default_factory=dict)
    overall_result: str = "PENDING"
    recommendation: str = "NOT READY"

    def to_dict(self) -> Dict:
        return asdict(self)


# ══════════════════════════════════════════════════════════════════
# SIMULATED POSITION MANAGER
# ══════════════════════════════════════════════════════════════════

class SimulatedPositionManager:
    """
    Manages simulated positions — fills, exits, trailing stops, partial exits.
    NO real exchange orders.
    """

    def __init__(self) -> None:
        self.positions: Dict[str, PaperTrade] = {}  # trade_id -> PaperTrade
        self._trade_counter = 0

    def open_position(self, sig: PaperSignal, current_price: float,
                      quantity: float, leverage: int) -> PaperTrade:
        """Simulate opening a position with slippage."""
        self._trade_counter += 1
        trade_id = f"PT-{int(time.time())}-{self._trade_counter:04d}"

        # Apply slippage to entry
        slippage_pct = SLIPPAGE_BPS / 10000.0
        if sig.side == "LONG":
            fill_price = current_price * (1 + slippage_pct)  # Buy higher
        else:
            fill_price = current_price * (1 - slippage_pct)  # Sell lower

        entry_slippage = abs(fill_price - current_price)
        fees = fill_price * quantity * TAKER_FEE

        trade = PaperTrade(
            id=trade_id,
            signal_id=sig.id,
            symbol=sig.symbol,
            side=sig.side,
            entry_time=time.time(),
            entry_price=fill_price,
            expected_entry=current_price,
            quantity=quantity,
            leverage=leverage,
            entry_slippage=entry_slippage,
            fees=fees,
            stop_loss=sig.stop_loss,
            take_profit=sig.take_profit,
            confidence=sig.confidence,
            institutional_score=sig.institutional_score,
            market_regime=sig.market_regime,
            status="open",
        )
        self.positions[trade_id] = trade
        return trade

    def check_exit(self, trade: PaperTrade, current_price: float,
                   high: float = 0, low: float = 0) -> Tuple[bool, str]:
        """Check if a position should be closed based on SL/TP."""
        if trade.side == "LONG":
            if current_price <= trade.stop_loss:
                return True, "stop_loss"
            if current_price >= trade.take_profit:
                return True, "take_profit"
        else:  # SHORT
            if current_price >= trade.stop_loss:
                return True, "stop_loss"
            if current_price <= trade.take_profit:
                return True, "take_profit"
        return False, ""

    def close_position(self, trade_id: str, current_price: float,
                       reason: str) -> Optional[PaperTrade]:
        """Simulate closing a position with slippage and fees."""
        trade = self.positions.get(trade_id)
        if not trade or trade.status != "open":
            return None

        # Apply slippage to exit
        slippage_pct = SLIPPAGE_BPS / 10000.0
        if trade.side == "LONG":
            fill_price = current_price * (1 - slippage_pct)  # Sell lower
        else:
            fill_price = current_price * (1 + slippage_pct)  # Buy higher

        exit_slippage = abs(fill_price - current_price)
        exit_fees = fill_price * trade.quantity * TAKER_FEE

        # Calculate PnL
        if trade.side == "LONG":
            gross_pnl = (fill_price - trade.entry_price) * trade.quantity * trade.leverage
        else:
            gross_pnl = (trade.entry_price - fill_price) * trade.quantity * trade.leverage

        total_fees = trade.fees + exit_fees
        net_pnl = gross_pnl - total_fees
        return_pct = (net_pnl / (trade.entry_price * trade.quantity)) * 100 if trade.entry_price * trade.quantity > 0 else 0

        # Update trade
        trade.exit_time = time.time()
        trade.duration_min = (trade.exit_time - trade.entry_time) / 60.0
        trade.exit_price = fill_price
        trade.expected_exit = current_price
        trade.gross_pnl = round(gross_pnl, 2)
        trade.net_pnl = round(net_pnl, 2)
        trade.return_pct = round(return_pct, 4)
        trade.exit_slippage = exit_slippage
        trade.total_slippage = trade.entry_slippage + exit_slippage
        trade.fees = round(total_fees, 4)
        trade.exit_reason = reason
        trade.status = "closed"

        del self.positions[trade_id]
        return trade

    def get_open_positions(self) -> List[PaperTrade]:
        return list(self.positions.values())

    def position_count(self) -> int:
        return len(self.positions)

    def symbol_has_position(self, symbol: str) -> bool:
        return any(t.symbol == symbol for t in self.positions.values())


# ══════════════════════════════════════════════════════════════════
# SYSTEM HEALTH MONITOR
# ══════════════════════════════════════════════════════════════════

class SystemHealthMonitor:
    """Tracks system health metrics for paper trading validation."""

    def __init__(self) -> None:
        self.start_time = time.time()
        self.api_errors = 0
        self.reconnect_events = 0
        self.ws_disconnects = 0
        self.dropped_messages = 0
        self.total_messages = 0
        self.latencies: List[float] = []
        self.processing_times: List[float] = []
        self.queue_backlog = 0
        self.last_error = ""
        self.last_error_time = 0.0
        self._down_time = 0.0
        self._last_ws_disconnect = 0.0
        self._memory_samples: List[float] = []

    def record_api_error(self, error: str) -> None:
        self.api_errors += 1
        self.last_error = error
        self.last_error_time = time.time()

    def record_reconnect(self) -> None:
        self.reconnect_events += 1

    def record_ws_disconnect(self) -> None:
        self.ws_disconnects += 1
        self._last_ws_disconnect = time.time()

    def record_ws_reconnect(self) -> None:
        if self._last_ws_disconnect > 0:
            self._down_time += time.time() - self._last_ws_disconnect
            self._last_ws_disconnect = 0

    def record_message(self, processing_time_ms: float) -> None:
        self.total_messages += 1
        self.processing_times.append(processing_time_ms)
        if len(self.processing_times) > 1000:
            self.processing_times = self.processing_times[-500:]

    def record_latency(self, latency_ms: float) -> None:
        self.latencies.append(latency_ms)
        if len(self.latencies) > 1000:
            self.latencies = self.latencies[-500:]

    def record_dropped_message(self) -> None:
        self.dropped_messages += 1

    def record_memory(self, mb: float) -> None:
        self._memory_samples.append(mb)
        if len(self._memory_samples) > 100:
            self._memory_samples = self._memory_samples[-50:]

    def get_snapshot(self) -> SystemHealth:
        elapsed = time.time() - self.start_time
        current_down = 0.0
        if self._last_ws_disconnect > 0:
            current_down = time.time() - self._last_ws_disconnect
        total_down = self._down_time + current_down
        uptime_pct = ((elapsed - total_down) / elapsed * 100) if elapsed > 0 else 100.0

        return SystemHealth(
            api_errors=self.api_errors,
            reconnect_events=self.reconnect_events,
            ws_disconnects=self.ws_disconnects,
            memory_mb=self._memory_samples[-1] if self._memory_samples else 0.0,
            cpu_pct=0.0,  # Requires psutil
            latency_ms=np.median(self.latencies) if self.latencies else 0.0,
            msg_processing_delay_ms=np.median(self.processing_times) if self.processing_times else 0.0,
            queue_backlog=self.queue_backlog,
            dropped_messages=self.dropped_messages,
            uptime_sec=elapsed,
            uptime_pct=round(uptime_pct, 2),
            total_messages=self.total_messages,
            last_error=self.last_error,
            last_error_time=self.last_error_time,
        )


# ══════════════════════════════════════════════════════════════════
# EXECUTION QUALITY ANALYZER
# ══════════════════════════════════════════════════════════════════

class ExecutionQualityAnalyzer:
    """Calculates execution quality metrics from closed trades."""

    def __init__(self) -> None:
        self._returns: List[float] = []

    def calculate(self, trades: List[PaperTrade], signal_count: int) -> ExecutionQuality:
        if not trades:
            return ExecutionQuality(signal_count=signal_count)

        wins = [t for t in trades if t.net_pnl > 0]
        losses = [t for t in trades if t.net_pnl <= 0]
        win_count = len(wins)
        loss_count = len(losses)
        total = len(trades)
        win_rate = win_count / total if total > 0 else 0.0

        gross_wins = sum(t.net_pnl for t in wins)
        gross_losses = abs(sum(t.net_pnl for t in losses))
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else float('inf')

        avg_win = gross_wins / win_count if win_count > 0 else 0.0
        avg_loss = gross_losses / loss_count if loss_count > 0 else 0.0

        # Risk/Reward
        rr = avg_win / avg_loss if avg_loss > 0 else 0.0

        # Hold time
        hold_times = [t.duration_min for t in trades if t.duration_min > 0]
        avg_hold = np.mean(hold_times) if hold_times else 0.0

        # Streaks
        longest_win = self._longest_streak(trades, True)
        longest_loss = self._longest_streak(trades, False)

        # Total fees & slippage
        total_fees = sum(t.fees for t in trades)
        total_slippage = sum(t.total_slippage * t.quantity for t in trades)

        # Sharpe ratio (annualized from per-trade returns)
        returns = [t.return_pct for t in trades if t.return_pct != 0]
        if len(returns) >= 2:
            sharpe = (np.mean(returns) / np.std(returns)) * np.sqrt(252) if np.std(returns) > 0 else 0.0
        else:
            sharpe = 0.0

        # Max drawdown
        equity_curve = self._build_equity_curve(trades)
        max_dd = self._max_drawdown(equity_curve)

        return ExecutionQuality(
            signal_count=signal_count,
            trade_count=total,
            win_count=win_count,
            loss_count=loss_count,
            win_rate=round(win_rate, 4),
            profit_factor=round(profit_factor, 4),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            risk_reward=round(rr, 2),
            avg_hold_time_min=round(avg_hold, 1),
            longest_win_streak=longest_win,
            longest_loss_streak=longest_loss,
            total_gross_pnl=round(sum(t.gross_pnl for t in trades), 2),
            total_net_pnl=round(sum(t.net_pnl for t in trades), 2),
            total_fees=round(total_fees, 2),
            total_slippage=round(total_slippage, 6),
            sharpe_ratio=round(sharpe, 2),
            max_drawdown_pct=round(max_dd, 2),
        )

    @staticmethod
    def _longest_streak(trades: List[PaperTrade], is_win: bool) -> int:
        longest = 0
        current = 0
        for t in sorted(trades, key=lambda x: x.entry_time):
            if (t.net_pnl > 0) == is_win:
                current += 1
                longest = max(longest, current)
            else:
                current = 0
        return longest

    @staticmethod
    def _build_equity_curve(trades: List[PaperTrade]) -> List[float]:
        equity = [STARTING_EQUITY]
        for t in sorted(trades, key=lambda x: x.entry_time):
            equity.append(equity[-1] + t.net_pnl)
        return equity

    @staticmethod
    def _max_drawdown(equity: List[float]) -> float:
        if len(equity) < 2:
            return 0.0
        peak = equity[0]
        max_dd = 0.0
        for e in equity:
            peak = max(peak, e)
            dd = (peak - e) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        return max_dd


# ══════════════════════════════════════════════════════════════════
# PAPER TRADING ENGINE — MAIN ORCHESTRATOR
# ══════════════════════════════════════════════════════════════════

class PaperTradingEngine:
    """
    Live paper trading validation engine.
    
    Connects to Binance Futures live data, runs the full institutional
    signal pipeline, simulates trade execution, and tracks everything.
    
    NO REAL ORDERS. NO REAL CAPITAL.
    """

    def __init__(self) -> None:
        # ── Core config ──────────────────────────────────────────
        self.starting_equity = STARTING_EQUITY
        self.current_equity = STARTING_EQUITY
        self.peak_equity = STARTING_EQUITY
        self.risk_per_trade_pct = RISK_PER_TRADE_PCT
        self.max_open_positions = MAX_OPEN_POSITIONS
        self.max_portfolio_risk_pct = MAX_PORTFOLIO_RISK_PCT
        self.leverage = DEFAULT_LEVERAGE

        # ── Live data source ─────────────────────────────────────
        self.ws = BinanceWebSocket()

        # ── Full signal generation pipeline ──────────────────────
        self.orderflow = OrderFlowAnalyzer()
        self.cumulative_delta = CumulativeDeltaEngine()
        self.cvd_inst = CVDEngine()
        self.dom = DOMAnalytics()
        self.funding = FundingRateEngine()
        self.oi = OpenInterestEngine()
        self.exchange_flow = ExchangeFlowEngine()
        self.liquidation = LiquidationEngine()
        self.symbol_scanner = AutoSymbolScanner()
        self.institutional = InstitutionalEngine()
        self.scoring_engine = InstitutionalScoringEngine()
        self.smart_money = SmartMoneyEngine()
        self.sweep = SweepDetector()
        self.absorption = AbsorptionDetector()
        self.spoof_iceberg = SpoofingIcebergDetector()
        self.liquidity_map = LiquidityMappingEngine()
        self.regime_detector = MarketRegimeDetector()
        self.scorer = AIConfidenceScorer()
        self.risk_engine = RiskEngine()
        self.position_sizer = PositionSizingEngine()
        self.entry_confirmer = EntryConfirmationEngine()

        # ── Subsystems ───────────────────────────────────────────
        self.position_mgr = SimulatedPositionManager()
        self.health_monitor = SystemHealthMonitor()
        self.execution_analyzer = ExecutionQualityAnalyzer()

        # ── State ────────────────────────────────────────────────
        self.active_symbols: Set[str] = set()
        self.symbol_data: Dict[str, Dict] = {}
        self.signals: List[PaperSignal] = []
        self.closed_trades: List[PaperTrade] = []
        self.daily_reports: List[DailyReport] = []
        self.equity_history: List[Dict] = []
        self.is_running = False
        self._tasks: List[asyncio.Task] = []
        self._start_time: float = 0.0
        self._last_state_save: float = 0.0
        self._last_health_check: float = 0.0
        self._last_daily_report: float = 0.0
        self._current_date: str = ""
        self._daily_signal_count: int = 0
        self._daily_trade_opened: int = 0
        self._daily_trade_closed: int = 0

        # ── Signal cooldowns ─────────────────────────────────────
        self._signal_cooldowns: Dict[str, float] = {}

        # Ensure directories exist
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        FIGURES_DIR.mkdir(parents=True, exist_ok=True)

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Start paper trading engine with live market data."""
        self._start_time = time.time()
        self.is_running = True
        self._current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        logger.info("=" * 60)
        logger.info("PAPER TRADING VALIDATION ENGINE")
        logger.info("=" * 60)
        logger.info("Mode: SIMULATION (NO REAL ORDERS)")
        logger.info("Starting Equity: ${:,.2f}", self.starting_equity)
        logger.info("Risk Per Trade: {}%", self.risk_per_trade_pct)
        logger.info("Max Open Positions: {}", self.max_open_positions)
        logger.info("Leverage: {}x", self.leverage)
        logger.info("=" * 60)

        # Load previous state if exists (restart recovery)
        await self._load_state()

        # Initialize database
        try:
            from database.signal_repository import repo as db
            await db.initialize()
        except Exception as e:
            logger.warning("Signal DB init skipped: {}", e)

        from database import db as db_conn
        await db_conn.connect()

        # Load symbols
        await self._load_symbols()

        # Initialize all engines
        engines = [
            self.orderflow, self.institutional, self.cumulative_delta,
            self.regime_detector, self.dom, self.funding, self.oi,
            self.exchange_flow, self.liquidation, self.symbol_scanner,
            self.smart_money, self.sweep, self.absorption,
            self.spoof_iceberg, self.liquidity_map, self.cvd_inst,
            self.position_sizer, self.entry_confirmer,
        ]
        for eng in engines:
            try:
                await eng.initialize()
            except Exception as e:
                logger.warning("Engine init error: {}", e)
                self.health_monitor.record_api_error(str(e))

        # Start WebSocket for live data
        await self.ws.start(self._on_market_data)
        self.health_monitor.record_ws_reconnect()

        # Start background loops
        self._tasks = [
            asyncio.create_task(self._safe_loop("scan", self._scan_loop), name="scan"),
            asyncio.create_task(self._safe_loop("risk", self._risk_loop), name="risk"),
            asyncio.create_task(self._safe_loop("health", self._health_loop), name="health"),
            asyncio.create_task(self._safe_loop("state", self._state_save_loop), name="state"),
            asyncio.create_task(self._safe_loop("daily", self._daily_report_loop), name="daily"),
        ]

        logger.info("✅ Paper Trading Engine running — {} symbols", len(self.active_symbols))
        logger.info("📊 Tracking: signals, trades, execution quality, system health")
        logger.info("⏱️  Minimum duration: {} days", MIN_DURATION_DAYS)

        # Wait for shutdown
        try:
            while self.is_running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Graceful shutdown — save state, generate final report."""
        logger.info("🛑 Stopping Paper Trading Engine...")
        self.is_running = False

        # Cancel all tasks
        for t in self._tasks:
            if not t.done():
                t.cancel()
        done, pending = await asyncio.wait(self._tasks, timeout=10)
        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        # Stop WebSocket
        await self.ws.stop()

        # Close all open positions at market
        for trade_id in list(self.position_mgr.positions.keys()):
            pos = self.position_mgr.positions[trade_id]
            price = self._get_price(pos.symbol)
            if price:
                self.position_mgr.close_position(trade_id, price, "engine_shutdown")
                closed = self.position_mgr.positions.get(trade_id)
                if closed:
                    self.closed_trades.append(closed)

        # Save final state
        await self._save_state()

        # Generate final reports and exports
        await self._generate_all_outputs()

        logger.info("✅ Paper Trading Engine stopped")

    # ── Self-healing loop wrapper ────────────────────────────────

    async def _safe_loop(self, name: str, coro: Callable) -> None:
        backoff = 1
        while self.is_running:
            try:
                await coro()
                backoff = 1
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.health_monitor.record_api_error(f"{name}: {exc}")
                logger.error("Loop '{}' error: {} — retry {}s", name, exc, backoff)
                await asyncio.sleep(min(backoff, 60))
                backoff = min(backoff * 2, 60)

    # ── Symbol loading ───────────────────────────────────────────

    async def _load_symbols(self) -> None:
        logger.info("Loading symbols from Binance Futures...")
        try:
            all_syms = await self.ws.get_futures_symbols()
            tickers = await self.ws.get_24h_tickers()
            vol_map = {t["symbol"]: t.get("quoteVolume", 0) for t in tickers}

            filtered = sorted(
                [{"symbol": s, "base": s.replace("USDT", ""), "vol": vol_map.get(s, 0)}
                 for s in all_syms
                 if s.endswith("USDT") and vol_map.get(s, 0) >= config.scanner.min_volume_24h],
                key=lambda x: x["vol"], reverse=True,
            )[:config.scanner.max_symbols]

            for item in filtered:
                self.active_symbols.add(item["symbol"])

            logger.info("Loaded {} active symbols", len(self.active_symbols))
        except Exception as e:
            logger.error("Symbol loading failed: {}", e)
            self.health_monitor.record_api_error(f"symbol_load: {e}")
            # Fallback to top symbols
            for sym in ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]:
                self.active_symbols.add(sym)
            logger.info("Using {} fallback symbols", len(self.active_symbols))

    # ── Market data handler ──────────────────────────────────────

    async def _on_market_data(self, event: str, data: Dict) -> None:
        """Process incoming market data from Binance WebSocket."""
        t0 = time.time()
        sym = data.get("symbol")
        if not sym or sym not in self.active_symbols:
            return

        sd = self.symbol_data.setdefault(
            sym, {"trades": [], "orderbook": {"bids": [], "asks": []}, "klines": {}, "ts": 0}
        )
        sd["ts"] = time.time()

        try:
            if event == "trade":
                sd["trades"].append(data)
                if len(sd["trades"]) > 5000:
                    sd["trades"] = sd["trades"][-2500:]

                # Feed orderflow engine
                await self.orderflow.process_trade(sym, data)

            elif event == "depth":
                sd["orderbook"] = {"bids": data.get("bids", []), "asks": data.get("asks", [])}
                await self.institutional.process_orderbook(sym, sd["orderbook"])
                await self.dom.process_orderbook(sym, data.get("bids", []), data.get("asks", []))
                await self.spoof_iceberg.process_orderbook(sym, data.get("bids", []), data.get("asks", []))
                await self.liquidity_map.process_orderbook(sym, data.get("bids", []), data.get("asks", []))
                if sd["trades"]:
                    await self.absorption.process_trades(sym, sd["trades"][-20:], sd["orderbook"])

            elif event == "kline":
                iv = data.get("interval", "5m")
                kl = sd["klines"].setdefault(iv, [])
                kl.append(data)
                if len(kl) > 150:
                    sd["klines"][iv] = kl[-75:]
                await self.regime_detector.process_kline(sym, iv, data)
                await self.sweep.process_kline(sym, data)
                await self.liquidity_map.process_kline(sym, data)

        except Exception as exc:
            self.health_monitor.record_api_error(f"data_handler:{sym}: {exc}")

        # Record processing time
        elapsed_ms = (time.time() - t0) * 1000
        self.health_monitor.record_message(elapsed_ms)

    # ── Scan loop — signal generation ────────────────────────────

    async def _scan_loop(self) -> None:
        """Main signal generation loop — scans all active symbols."""
        symbols_with_data = [s for s in self.active_symbols if s in self.symbol_data]

        for sym in symbols_with_data:
            if not self.is_running:
                break
            await self._scan_symbol(sym)
            await asyncio.sleep(0.05)  # Yield to event loop

        await asyncio.sleep(config.scanner.scan_interval_sec)

    async def _scan_symbol(self, sym: str) -> None:
        """Scan a single symbol for trading signals."""
        # Cooldown check
        cooldown_until = self._signal_cooldowns.get(sym, 0)
        if time.time() < cooldown_until:
            return

        md = self.symbol_data.get(sym)
        if not md or not md.get("trades"):
            return

        try:
            # 1. Gather intelligence
            of_analysis = self.orderflow.get_analysis(sym)
            inst_patterns = self.institutional.get_patterns(sym)
            cd_analysis = self.cumulative_delta.get_analysis(sym)
            regime = self.regime_detector.get_regime(sym)
            mtf_data = self._get_mtf_alignment(sym)

            # 2. AI Scoring
            sig_data = await self.scorer.analyze_symbol(
                symbol=sym, market_data=md,
                orderflow=of_analysis,
                institutional=inst_patterns,
                cumulative_delta=cd_analysis,
                regime=regime,
                mtf_confirmation=mtf_data,
            )

            if not sig_data:
                return

            # 3. Institutional scoring
            inst_intel = self.scoring_engine.calculate_score({
                **sig_data,
                **(of_analysis or {}),
                "mtf_alignment": mtf_data.get("alignment_score", 0),
                "regime": regime.get("regime", "ranging") if regime else "ranging",
            })
            sig_data.update(inst_intel)

            # 4. Create paper signal
            sig = PaperSignal(
                id=f"SIG-{sym}-{int(time.time())}",
                timestamp=time.time(),
                symbol=sym,
                side=sig_data.get("type", "LONG"),
                entry_price=sig_data.get("entry_price", 0),
                stop_loss=sig_data.get("stop_loss", 0),
                take_profit=sig_data.get("take_profit", 0),
                confidence=sig_data.get("confidence", 0),
                institutional_score=inst_intel.get("score", 0),
                market_regime=regime.get("regime", "unknown") if regime else "unknown",
                mtf_alignment=mtf_data.get("alignment_score", 0),
                risk_reward=sig_data.get("risk_reward", 0),
            )

            self.signals.append(sig)
            self._daily_signal_count += 1
            self.health_monitor.total_messages += 1

            # 5. Gate: minimum institutional score
            if sig.institutional_score < 50:
                sig.status = "rejected"
                sig.rejection_reason = f"Low institutional score: {sig.institutional_score:.0f} < 50"
                self._signal_cooldowns[sym] = time.time() + 60
                return

            # 6. Risk check
            risk_result = await self.risk_engine.check_signal(sig_data)
            if not risk_result.get("allowed"):
                sig.status = "rejected"
                sig.rejection_reason = risk_result.get("reason", "risk_check_failed")
                self._signal_cooldowns[sym] = time.time() + 60
                return

            # 7. Entry confirmation
            confirm = await self.entry_confirmer.confirm_entry(
                symbol=sym, direction=sig.side,
                entry_price=sig.entry_price,
                stop_loss=sig.stop_loss,
                take_profit=sig.take_profit,
                market_data=md,
                orderflow=of_analysis,
                regime=regime,
            )
            if not confirm.confirmed:
                sig.status = "rejected"
                sig.rejection_reason = confirm.rejection_reason
                self._signal_cooldowns[sym] = time.time() + 60
                return

            # 8. Position limit check
            if self.position_mgr.position_count() >= self.max_open_positions:
                sig.status = "rejected"
                sig.rejection_reason = "max_positions_reached"
                self._signal_cooldowns[sym] = time.time() + 120
                return

            # 9. Duplicate position check
            if self.position_mgr.symbol_has_position(sym):
                sig.status = "rejected"
                sig.rejection_reason = "already_in_position"
                self._signal_cooldowns[sym] = time.time() + 300
                return

            # 10. Calculate position size
            size_result = await self.position_sizer.calculate_size(
                symbol=sym, direction=sig.side,
                entry_price=sig.entry_price,
                stop_loss=sig.stop_loss,
            )
            if size_result.quantity <= 0:
                sig.status = "rejected"
                sig.rejection_reason = "zero_position_size"
                return

            # 11. Portfolio risk check
            open_risk = self._calculate_open_risk()
            trade_risk = sig.entry_price * size_result.quantity * self.risk_per_trade_pct / 100
            if (open_risk + trade_risk) / self.current_equity * 100 > self.max_portfolio_risk_pct:
                sig.status = "rejected"
                sig.rejection_reason = "portfolio_risk_exceeded"
                return

            # 12. OPEN POSITION (SIMULATED)
            sig.status = "filled"
            sig.filled_at = time.time()
            sig.position_size = size_result.quantity

            current_price = self._get_price(sym) or sig.entry_price
            trade = self.position_mgr.open_position(
                sig, current_price, size_result.quantity, self.leverage
            )

            self._daily_trade_opened += 1
            self._signal_cooldowns[sym] = time.time() + config.scanner.signal_cooldown_sec

            logger.info(
                "📈 PAPER TRADE: {} {} @ ${:.2f} | Qty: {:.4f} | SL: ${:.2f} | TP: ${:.2f} | Score: {:.0f}",
                sig.side, sym, trade.entry_price, trade.quantity,
                trade.stop_loss, trade.take_profit, sig.institutional_score
            )

        except Exception as exc:
            self.health_monitor.record_api_error(f"scan:{sym}: {exc}")
            logger.debug("Scan error {}: {}", sym, exc)

    def _get_mtf_alignment(self, symbol: str) -> Dict:
        """Multi-timeframe alignment check."""
        md = self.symbol_data.get(symbol, {})
        klines = md.get("klines", {})
        result = {"alignment_score": 0, "direction": "NEUTRAL"}
        regimes = []
        for tf in ["1m", "5m", "15m", "1h", "4h"]:
            tf_klines = klines.get(tf, [])
            if len(tf_klines) >= 5:
                d = "LONG" if tf_klines[-1].get("close", 0) > tf_klines[-5].get("close", 0) else "SHORT"
                regimes.append(d)
        if regimes:
            long_c = regimes.count("LONG")
            short_c = regimes.count("SHORT")
            if long_c >= 4:
                result = {"alignment_score": long_c, "direction": "LONG"}
            elif short_c >= 4:
                result = {"alignment_score": short_c, "direction": "SHORT"}
            else:
                result = {"alignment_score": max(long_c, short_c), "direction": "NEUTRAL"}
        return result

    # ── Risk loop — exit checking ────────────────────────────────

    async def _risk_loop(self) -> None:
        """Check exit conditions for all open positions."""
        for trade in self.position_mgr.get_open_positions():
            price = self._get_price(trade.symbol)
            if price is None:
                continue

            should_close, reason = self.position_mgr.check_exit(trade, price)
            if should_close:
                closed = self.position_mgr.close_position(trade.id, price, reason)
                if closed:
                    # Calculate drawdown at close
                    equity_after = self.current_equity + closed.net_pnl
                    self.peak_equity = max(self.peak_equity, equity_after)
                    dd = (self.peak_equity - equity_after) / self.peak_equity * 100 if self.peak_equity > 0 else 0
                    closed.drawdown = round(dd, 2)

                    self.current_equity = equity_after
                    self.closed_trades.append(closed)
                    self._daily_trade_closed += 1

                    # Update risk engine state
                    self.risk_engine.balance = self.current_equity
                    self.risk_engine.peak = self.peak_equity

                    # Record equity point
                    self.equity_history.append({
                        "timestamp": time.time(),
                        "equity": self.current_equity,
                        "pnl": self.current_equity - self.starting_equity,
                        "drawdown": dd,
                        "trade_id": closed.id,
                        "symbol": closed.symbol,
                    })

                    pnl_emoji = "💰" if closed.net_pnl > 0 else "💸"
                    logger.info(
                        "{} PAPER CLOSE: {} {} | PnL: ${:.2f} | Reason: {} | Equity: ${:,.2f}",
                        pnl_emoji, closed.side, closed.symbol,
                        closed.net_pnl, reason, self.current_equity
                    )

        await asyncio.sleep(RISK_CHECK_SEC)

    # ── Health monitoring loop ───────────────────────────────────

    async def _health_loop(self) -> None:
        """Periodic system health check."""
        try:
            import psutil
            proc = psutil.Process()
            mem_mb = proc.memory_info().rss / (1024 * 1024)
            self.health_monitor.record_memory(mem_mb)
        except ImportError:
            pass

        # Check WS connection
        if hasattr(self.ws, '_connected') and not self.ws._connected:
            self.health_monitor.record_ws_disconnect()

        await asyncio.sleep(HEALTH_CHECK_SEC)

    # ── State persistence loop ───────────────────────────────────

    async def _state_save_loop(self) -> None:
        """Periodically save state for restart recovery."""
        await asyncio.sleep(STATE_SAVE_SEC)
        await self._save_state()

    async def _save_state(self) -> None:
        """Persist engine state to disk."""
        try:
            state = {
                "version": 2,
                "timestamp": time.time(),
                "current_equity": self.current_equity,
                "peak_equity": self.peak_equity,
                "starting_equity": self.starting_equity,
                "start_time": self._start_time,
                "closed_trades_count": len(self.closed_trades),
                "signals_count": len(self.signals),
                "open_positions": [
                    {
                        "id": t.id, "signal_id": t.signal_id, "symbol": t.symbol,
                        "side": t.side, "entry_time": t.entry_time,
                        "entry_price": t.entry_price, "expected_entry": t.expected_entry,
                        "quantity": t.quantity, "leverage": t.leverage,
                        "stop_loss": t.stop_loss, "take_profit": t.take_profit,
                        "fees": t.fees, "confidence": t.confidence,
                        "institutional_score": t.institutional_score,
                        "market_regime": t.market_regime,
                    }
                    for t in self.position_mgr.get_open_positions()
                ],
                "equity_history": self.equity_history[-500:],
                "health": self.health_monitor.get_snapshot().to_dict(),
            }
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
            self._last_state_save = time.time()
        except Exception as exc:
            logger.error("State save failed: {}", exc)

    async def _load_state(self) -> None:
        """Load previous state for restart recovery."""
        if not STATE_FILE.exists():
            logger.info("No previous state found — fresh start")
            return

        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)

            self.current_equity = state.get("current_equity", self.starting_equity)
            self.peak_equity = state.get("peak_equity", self.current_equity)
            self._start_time = state.get("start_time", time.time())
            self.equity_history = state.get("equity_history", [])

            # Restore open positions
            for pos_data in state.get("open_positions", []):
                trade = PaperTrade(
                    id=pos_data["id"],
                    signal_id=pos_data.get("signal_id", ""),
                    symbol=pos_data["symbol"],
                    side=pos_data["side"],
                    entry_time=pos_data.get("entry_time", time.time()),
                    entry_price=pos_data["entry_price"],
                    expected_entry=pos_data.get("expected_entry", pos_data["entry_price"]),
                    quantity=pos_data["quantity"],
                    leverage=pos_data.get("leverage", DEFAULT_LEVERAGE),
                    stop_loss=pos_data.get("stop_loss", 0),
                    take_profit=pos_data.get("take_profit", 0),
                    fees=pos_data.get("fees", 0),
                    confidence=pos_data.get("confidence", 0),
                    institutional_score=pos_data.get("institutional_score", 0),
                    market_regime=pos_data.get("market_regime", ""),
                    status="open",
                )
                self.position_mgr.positions[trade.id] = trade

            logger.info(
                "♻️  State restored: Equity=${:,.2f} | {} open positions | {} equity points",
                self.current_equity, len(self.position_mgr.positions), len(self.equity_history)
            )
        except Exception as exc:
            logger.error("State load failed: {}", exc)

    # ── Daily report loop ────────────────────────────────────────

    async def _daily_report_loop(self) -> None:
        """Generate daily report at midnight UTC."""
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")

        # Check if date changed
        if today != self._current_date and self._current_date:
            await self._generate_daily_report(self._current_date)
            self._current_date = today
            self._daily_signal_count = 0
            self._daily_trade_opened = 0
            self._daily_trade_closed = 0

        await asyncio.sleep(60)  # Check every minute

    async def _generate_daily_report(self, date: str) -> None:
        """Generate and store daily performance report."""
        # Get today's trades
        day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        day_end = day_start + 86400
        day_trades = [t for t in self.closed_trades if day_start <= t.entry_time < day_end]

        wins = [t for t in day_trades if t.net_pnl > 0]
        losses = [t for t in day_trades if t.net_pnl <= 0]
        win_rate = len(wins) / len(day_trades) if day_trades else 0
        gross_wins = sum(t.net_pnl for t in wins)
        gross_losses = abs(sum(t.net_pnl for t in losses))
        pf = gross_wins / gross_losses if gross_losses > 0 else float('inf')
        net_pnl = sum(t.net_pnl for t in day_trades)

        # Drawdown
        dd = (self.peak_equity - self.current_equity) / self.peak_equity * 100 if self.peak_equity > 0 else 0

        # Exposure
        open_val = sum(t.entry_price * t.quantity for t in self.position_mgr.get_open_positions())
        exposure = open_val / self.current_equity * 100 if self.current_equity > 0 else 0

        report = DailyReport(
            date=date,
            signals_generated=self._daily_signal_count,
            trades_opened=self._daily_trade_opened,
            trades_closed=self._daily_trade_closed,
            win_rate=round(win_rate, 4),
            profit_factor=round(pf, 4) if pf != float('inf') else 999.0,
            net_pnl=round(net_pnl, 2),
            drawdown_pct=round(dd, 2),
            open_positions=self.position_mgr.position_count(),
            api_errors=self.health_monitor.api_errors,
            reconnects=self.health_monitor.reconnect_events,
            latency_ms=round(self.health_monitor.latencies[-1], 1) if self.health_monitor.latencies else 0,
            equity=self.current_equity,
            exposure_pct=round(exposure, 2),
            open_risk_pct=round(self._calculate_open_risk() / self.current_equity * 100, 2) if self.current_equity > 0 else 0,
        )
        self.daily_reports.append(report)

        logger.info("=" * 60)
        logger.info("DAILY PAPER TRADING REPORT — {}", date)
        logger.info("=" * 60)
        logger.info("Signals: {} | Trades Opened: {} | Closed: {}",
                     report.signals_generated, report.trades_opened, report.trades_closed)
        logger.info("Win Rate: {:.1%} | PF: {:.2f} | Net PnL: ${:,.2f}",
                     report.win_rate, report.profit_factor, report.net_pnl)
        logger.info("Drawdown: {:.2f}% | Open Positions: {} | Equity: ${:,.2f}",
                     report.drawdown_pct, report.open_positions, report.equity)
        logger.info("API Errors: {} | Reconnects: {} | Latency: {:.0f}ms",
                     report.api_errors, report.reconnects, report.latency_ms)
        logger.info("=" * 60)

    # ── Helpers ──────────────────────────────────────────────────

    def _get_price(self, symbol: str) -> Optional[float]:
        """Get current price from live trade data."""
        trades = self.symbol_data.get(symbol, {}).get("trades", [])
        return trades[-1]["price"] if trades else None

    def _calculate_open_risk(self) -> float:
        """Calculate total open risk in USD."""
        total_risk = 0.0
        for pos in self.position_mgr.get_open_positions():
            risk_per_unit = abs(pos.entry_price - pos.stop_loss)
            total_risk += risk_per_unit * pos.quantity * pos.leverage
        return total_risk

    # ── Final output generation ──────────────────────────────────

    async def _generate_all_outputs(self) -> None:
        """Generate all reports, exports, and charts."""
        logger.info("Generating final outputs...")

        # Close any remaining positions
        for tid in list(self.position_mgr.positions.keys()):
            pos = self.position_mgr.positions[tid]
            price = self._get_price(pos.symbol) or pos.entry_price
            closed = self.position_mgr.close_position(tid, price, "final_close")
            if closed:
                self.closed_trades.append(closed)

        # Generate weekly reports
        self._generate_weekly_reports()

        # Calculate final summary
        summary = self._calculate_final_summary()

        # Export everything
        self._export_trades_csv()
        self._export_signals_csv()
        self._export_daily_csv()
        self._export_weekly_csv()
        self._export_summary_json(summary)

        # Generate charts
        self._generate_charts()

        # Print final report
        self._print_final_report(summary)

    def _calculate_final_summary(self) -> PaperTradingSummary:
        """Calculate final validation summary."""
        eq = ExecutionQualityAnalyzer()
        quality = eq.calculate(self.closed_trades, len(self.signals))

        duration = time.time() - self._start_time if self._start_time else 1
        duration_days = duration / 86400

        # Average slippage in bps
        slippages = []
        for t in self.closed_trades:
            if t.entry_price > 0:
                entry_bps = t.entry_slippage / t.entry_price * 10000
                exit_bps = t.exit_slippage / t.exit_price * 10000 if t.exit_price > 0 else 0
                slippages.append(entry_bps + exit_bps)
        avg_slippage = np.mean(slippages) if slippages else 0

        # Performance drift (compare to backtest baselines from Phase 1/2)
        backtest_pf = 1.60  # From trade_log.csv
        backtest_wr = 0.515
        backtest_dd = 8.72  # From Monte Carlo worst DD

        drift_pf = ((backtest_pf - quality.profit_factor) / backtest_pf * 100) if backtest_pf > 0 else 0
        drift_wr = ((backtest_wr - quality.win_rate) / backtest_wr * 100) if backtest_wr > 0 else 0
        drift_dd = ((backtest_dd - quality.max_drawdown_pct) / backtest_dd * 100) if backtest_dd > 0 else 0

        # Success criteria
        health = self.health_monitor.get_snapshot()
        criteria = {
            "pf_gt_1_30": quality.profit_factor > CRITERIA_PF,
            "wr_gt_48pct": quality.win_rate > CRITERIA_WR,
            "dd_lt_10pct": quality.max_drawdown_pct < CRITERIA_DD,
            "positive_profit": quality.total_net_pnl > 0,
            "uptime_gt_99pct": health.uptime_pct > CRITERIA_UPTIME * 100,
            "no_critical_failures": health.api_errors < 100,
        }
        all_pass = all(criteria.values())

        # Recommendation
        if all_pass and duration_days >= MIN_DURATION_DAYS:
            if quality.profit_factor > 1.5 and quality.max_drawdown_pct < 5:
                recommendation = "READY FOR MODERATE CAPITAL"
            else:
                recommendation = "READY FOR SMALL CAPITAL"
        elif all_pass:
            recommendation = "READY FOR SMALL CAPITAL"
        else:
            recommendation = "NOT READY"

        return PaperTradingSummary(
            start_time=self._start_time,
            end_time=time.time(),
            duration_days=round(duration_days, 1),
            total_signals=len(self.signals),
            total_trades=len(self.closed_trades),
            win_rate=quality.win_rate,
            profit_factor=quality.profit_factor,
            net_profit=quality.total_net_pnl,
            max_drawdown_pct=quality.max_drawdown_pct,
            avg_slippage_bps=round(avg_slippage, 2),
            api_errors=health.api_errors,
            reconnects=health.reconnect_events,
            uptime_pct=health.uptime_pct,
            performance_drift={
                "pf_drift_pct": round(drift_pf, 1),
                "wr_drift_pct": round(drift_wr, 1),
                "dd_drift_pct": round(drift_dd, 1),
                "interpretation": self._interpret_drift(max(abs(drift_pf), abs(drift_wr), abs(drift_dd))),
            },
            criteria=criteria,
            overall_result="PASS" if all_pass else "FAIL",
            recommendation=recommendation,
        )

    @staticmethod
    def _interpret_drift(drift_pct: float) -> str:
        if drift_pct <= 10:
            return "Excellent"
        elif drift_pct <= 20:
            return "Acceptable"
        elif drift_pct <= 30:
            return "Warning"
        else:
            return "Potential Overfit"

    # ── Weekly reports ───────────────────────────────────────────

    def _generate_weekly_reports(self) -> None:
        """Generate weekly aggregated reports."""
        if not self.closed_trades:
            return

        # Group trades by week
        weeks: Dict[str, List[PaperTrade]] = {}
        for t in self.closed_trades:
            dt = datetime.fromtimestamp(t.entry_time, tz=timezone.utc)
            # ISO week
            year, week_num, _ = dt.isocalendar()
            week_key = f"{year}-W{week_num:02d}"
            weeks.setdefault(week_key, []).append(t)

        for week_key, trades in sorted(weeks.items()):
            wins = [t for t in trades if t.net_pnl > 0]
            losses = [t for t in trades if t.net_pnl <= 0]
            wr = len(wins) / len(trades) if trades else 0
            gw = sum(t.net_pnl for t in wins)
            gl = abs(sum(t.net_pnl for t in losses))
            pf = gw / gl if gl > 0 else float('inf')

            # Best/worst symbol
            sym_pnl: Dict[str, float] = {}
            for t in trades:
                sym_pnl[t.symbol] = sym_pnl.get(t.symbol, 0) + t.net_pnl
            best_sym = max(sym_pnl, key=sym_pnl.get) if sym_pnl else ""  # type: ignore
            worst_sym = min(sym_pnl, key=sym_pnl.get) if sym_pnl else ""  # type: ignore

            # Best/worst regime
            regime_pnl: Dict[str, float] = {}
            for t in trades:
                r = t.market_regime or "unknown"
                regime_pnl[r] = regime_pnl.get(r, 0) + t.net_pnl
            best_regime = max(regime_pnl, key=regime_pnl.get) if regime_pnl else ""  # type: ignore
            worst_regime = min(regime_pnl, key=regime_pnl.get) if regime_pnl else ""  # type: ignore

            # Equity curve for DD
            eq_curve = [STARTING_EQUITY]
            for t in sorted(trades, key=lambda x: x.entry_time):
                eq_curve.append(eq_curve[-1] + t.net_pnl)
            peak = eq_curve[0]
            max_dd = 0
            for e in eq_curve:
                peak = max(peak, e)
                dd = (peak - e) / peak * 100 if peak > 0 else 0
                max_dd = max(max_dd, dd)

            # Week start/end
            first_trade = min(trades, key=lambda x: x.entry_time)
            last_trade = max(trades, key=lambda x: x.entry_time)
            ws = datetime.fromtimestamp(first_trade.entry_time, tz=timezone.utc).strftime("%Y-%m-%d")
            we = datetime.fromtimestamp(last_trade.entry_time, tz=timezone.utc).strftime("%Y-%m-%d")

            eq = ExecutionQualityAnalyzer()
            quality = eq.calculate(trades, 0)

            report = WeeklyReport(
                week_start=ws, week_end=we,
                win_rate=round(wr, 4),
                profit_factor=round(pf, 4) if pf != float('inf') else 999.0,
                net_pnl=round(sum(t.net_pnl for t in trades), 2),
                drawdown_pct=round(max_dd, 2),
                best_symbol=best_sym, worst_symbol=worst_sym,
                best_regime=best_regime, worst_regime=worst_regime,
                trades=len(trades), signals=0,
                execution_quality=quality.to_dict(),
            )
            self._weekly_reports = getattr(self, '_weekly_reports', [])
            self._weekly_reports.append(report)

    # ── Export functions ─────────────────────────────────────────

    def _export_trades_csv(self) -> None:
        """Export all trades to CSV."""
        fields = [
            "id", "signal_id", "symbol", "side", "entry_time", "exit_time",
            "duration_min", "entry_price", "expected_entry", "exit_price",
            "expected_exit", "quantity", "leverage", "gross_pnl", "net_pnl",
            "return_pct", "entry_slippage", "exit_slippage", "total_slippage",
            "fees", "drawdown", "exit_reason", "stop_loss", "take_profit",
            "confidence", "institutional_score", "market_regime", "status",
        ]
        with open(TRADES_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for t in sorted(self.closed_trades, key=lambda x: x.entry_time):
                row = t.to_dict()
                writer.writerow({k: row.get(k, "") for k in fields})
        logger.info("Exported {} trades → {}", len(self.closed_trades), TRADES_CSV)

    def _export_signals_csv(self) -> None:
        """Export all signals to CSV."""
        fields = [
            "id", "timestamp", "symbol", "side", "entry_price", "stop_loss",
            "take_profit", "confidence", "institutional_score", "market_regime",
            "position_size", "status", "rejection_reason", "filled_at",
            "mtf_alignment", "risk_reward",
        ]
        with open(SIGNALS_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for s in sorted(self.signals, key=lambda x: x.timestamp):
                row = s.to_dict()
                writer.writerow({k: row.get(k, "") for k in fields})
        logger.info("Exported {} signals → {}", len(self.signals), SIGNALS_CSV)

    def _export_daily_csv(self) -> None:
        """Export daily reports to CSV."""
        if not self.daily_reports:
            return
        fields = list(self.daily_reports[0].to_dict().keys())
        with open(DAILY_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for r in self.daily_reports:
                writer.writerow(r.to_dict())
        logger.info("Exported {} daily reports → {}", len(self.daily_reports), DAILY_CSV)

    def _export_weekly_csv(self) -> None:
        """Export weekly reports to CSV."""
        weekly = getattr(self, '_weekly_reports', [])
        if not weekly:
            return
        # Flatten execution_quality for CSV
        flat_fields = [
            "week_start", "week_end", "win_rate", "profit_factor", "net_pnl",
            "drawdown_pct", "best_symbol", "worst_symbol", "best_regime",
            "worst_regime", "trades", "signals",
        ]
        with open(WEEKLY_CSV, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=flat_fields)
            writer.writeheader()
            for r in weekly:
                row = r.to_dict()
                writer.writerow({k: row.get(k, "") for k in flat_fields})
        logger.info("Exported {} weekly reports → {}", len(weekly), WEEKLY_CSV)

    def _export_summary_json(self, summary: PaperTradingSummary) -> None:
        """Export final summary to JSON."""
        with open(SUMMARY_JSON, "w") as f:
            json.dump(summary.to_dict(), f, indent=2, default=str)
        logger.info("Exported summary → {}", SUMMARY_JSON)

    # ── Chart generation ─────────────────────────────────────────

    def _generate_charts(self) -> None:
        """Generate all 7 visualization charts."""
        if not HAS_MATPLOTLIB:
            logger.warning("matplotlib not available — skipping charts")
            return

        if not self.closed_trades:
            logger.warning("No trades to chart")
            return

        # Dark theme
        plt.style.use("dark_background")
        BG = "#0e1117"
        GRID = "#1e2530"

        trades = sorted(self.closed_trades, key=lambda x: x.entry_time)
        equities = [e["equity"] for e in self.equity_history] if self.equity_history else [self.starting_equity]
        timestamps = [e["timestamp"] for e in self.equity_history] if self.equity_history else [time.time()]

        # 1. Equity Curve
        self._chart_equity_curve(BG, GRID, equities, timestamps)

        # 2. Drawdown Curve
        self._chart_drawdown_curve(BG, GRID, equities, timestamps)

        # 3. PnL Distribution
        self._chart_pnl_distribution(BG, GRID, trades)

        # 4. Trade Distribution (by symbol)
        self._chart_trade_distribution(BG, GRID, trades)

        # 5. Slippage Distribution
        self._chart_slippage_distribution(BG, GRID, trades)

        # 6. Symbol Performance
        self._chart_symbol_performance(BG, GRID, trades)

        # 7. Regime Performance
        self._chart_regime_performance(BG, GRID, trades)

        plt.close("all")
        logger.info("Generated 7 charts in {}", FIGURES_DIR)

    def _chart_equity_curve(self, bg, grid, equities, timestamps) -> None:
        fig, ax = plt.subplots(figsize=(14, 7), facecolor=bg)
        ax.set_facecolor(bg)
        dates = [datetime.fromtimestamp(t, tz=timezone.utc) for t in timestamps]
        ax.plot(dates, equities, color="#00d4aa", linewidth=1.5, label="Equity")
        ax.axhline(y=self.starting_equity, color="#ff6b6b", linestyle="--", alpha=0.5, label="Starting Equity")
        ax.fill_between(dates, self.starting_equity, equities,
                        where=[e >= self.starting_equity for e in equities],
                        color="#00d4aa", alpha=0.1)
        ax.fill_between(dates, self.starting_equity, equities,
                        where=[e < self.starting_equity for e in equities],
                        color="#ff6b6b", alpha=0.1)
        ax.set_title("Paper Trading — Equity Curve", fontsize=14, color="white")
        ax.set_xlabel("Date", color="white")
        ax.set_ylabel("Equity ($)", color="white")
        ax.legend(facecolor=grid, edgecolor=grid)
        ax.grid(True, alpha=0.2, color=grid)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        fig.tight_layout()
        fig.savefig(FIGURES_DIR / "paper_equity_curve.png", dpi=150, facecolor=bg)
        plt.close(fig)

    def _chart_drawdown_curve(self, bg, grid, equities, timestamps) -> None:
        fig, ax = plt.subplots(figsize=(14, 5), facecolor=bg)
        ax.set_facecolor(bg)
        dates = [datetime.fromtimestamp(t, tz=timezone.utc) for t in timestamps]
        peak = equities[0]
        dd_curve = []
        for e in equities:
            peak = max(peak, e)
            dd = (peak - e) / peak * 100 if peak > 0 else 0
            dd_curve.append(dd)
        ax.fill_between(dates, 0, dd_curve, color="#ff6b6b", alpha=0.4)
        ax.plot(dates, dd_curve, color="#ff6b6b", linewidth=1)
        ax.axhline(y=10, color="#ffaa00", linestyle="--", alpha=0.5, label="10% Limit")
        ax.set_title("Paper Trading — Drawdown", fontsize=14, color="white")
        ax.set_ylabel("Drawdown (%)", color="white")
        ax.legend(facecolor=grid, edgecolor=grid)
        ax.grid(True, alpha=0.2, color=grid)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        fig.tight_layout()
        fig.savefig(FIGURES_DIR / "paper_drawdown_curve.png", dpi=150, facecolor=bg)
        plt.close(fig)

    def _chart_pnl_distribution(self, bg, grid, trades) -> None:
        fig, ax = plt.subplots(figsize=(12, 6), facecolor=bg)
        ax.set_facecolor(bg)
        pnls = [t.net_pnl for t in trades]
        colors = ["#00d4aa" if p > 0 else "#ff6b6b" for p in pnls]
        n_bins = min(50, max(10, len(pnls) // 3))
        ax.hist(pnls, bins=n_bins, color="#4a9eff", alpha=0.7, edgecolor="#2a4a6f")
        ax.axvline(x=0, color="#ff6b6b", linestyle="--", alpha=0.7)
        ax.axvline(x=np.mean(pnls), color="#00d4aa", linestyle="--", alpha=0.7, label=f"Mean: ${np.mean(pnls):.2f}")
        ax.set_title("Paper Trading — PnL Distribution", fontsize=14, color="white")
        ax.set_xlabel("PnL ($)", color="white")
        ax.set_ylabel("Frequency", color="white")
        ax.legend(facecolor=grid, edgecolor=grid)
        ax.grid(True, alpha=0.2, color=grid)
        fig.tight_layout()
        fig.savefig(FIGURES_DIR / "paper_pnl_distribution.png", dpi=150, facecolor=bg)
        plt.close(fig)

    def _chart_trade_distribution(self, bg, grid, trades) -> None:
        fig, ax = plt.subplots(figsize=(12, 6), facecolor=bg)
        ax.set_facecolor(bg)
        sym_counts: Dict[str, int] = {}
        for t in trades:
            sym_counts[t.symbol] = sym_counts.get(t.symbol, 0) + 1
        sorted_syms = sorted(sym_counts.items(), key=lambda x: x[1], reverse=True)[:15]
        syms = [s[0] for s in sorted_syms]
        counts = [s[1] for s in sorted_syms]
        colors = plt.cm.viridis(np.linspace(0.3, 0.9, len(syms)))
        ax.barh(syms, counts, color=colors)
        ax.set_title("Paper Trading — Trade Distribution by Symbol", fontsize=14, color="white")
        ax.set_xlabel("Number of Trades", color="white")
        ax.grid(True, alpha=0.2, color=grid)
        fig.tight_layout()
        fig.savefig(FIGURES_DIR / "paper_trade_distribution.png", dpi=150, facecolor=bg)
        plt.close(fig)

    def _chart_slippage_distribution(self, bg, grid, trades) -> None:
        fig, ax = plt.subplots(figsize=(12, 6), facecolor=bg)
        ax.set_facecolor(bg)
        slippages = []
        for t in trades:
            if t.entry_price > 0:
                total_bps = t.total_slippage / t.entry_price * 10000
                slippages.append(total_bps)
        if slippages:
            n_bins = min(40, max(10, len(slippages) // 3))
            ax.hist(slippages, bins=n_bins, color="#ffaa00", alpha=0.7, edgecolor="#6f5a20")
            ax.axvline(x=np.mean(slippages), color="#ff6b6b", linestyle="--",
                       label=f"Mean: {np.mean(slippages):.1f} bps")
        ax.set_title("Paper Trading — Slippage Distribution", fontsize=14, color="white")
        ax.set_xlabel("Slippage (bps)", color="white")
        ax.set_ylabel("Frequency", color="white")
        ax.legend(facecolor=grid, edgecolor=grid)
        ax.grid(True, alpha=0.2, color=grid)
        fig.tight_layout()
        fig.savefig(FIGURES_DIR / "paper_slippage_distribution.png", dpi=150, facecolor=bg)
        plt.close(fig)

    def _chart_symbol_performance(self, bg, grid, trades) -> None:
        fig, ax = plt.subplots(figsize=(14, 7), facecolor=bg)
        ax.set_facecolor(bg)
        sym_pnl: Dict[str, float] = {}
        sym_wr: Dict[str, List[bool]] = {}
        for t in trades:
            sym_pnl[t.symbol] = sym_pnl.get(t.symbol, 0) + t.net_pnl
            sym_wr.setdefault(t.symbol, []).append(t.net_pnl > 0)

        sorted_syms = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)
        syms = [s[0] for s in sorted_syms]
        pnls = [s[1] for s in sorted_syms]
        colors = ["#00d4aa" if p > 0 else "#ff6b6b" for p in pnls]
        wrs = [np.mean(sym_wr.get(s, [0])) * 100 for s in syms]

        bars = ax.bar(syms, pnls, color=colors, alpha=0.8)
        ax2 = ax.twinx()
        ax2.plot(syms, wrs, color="#ffaa00", marker="o", linewidth=2, label="Win Rate %")
        ax2.set_ylabel("Win Rate (%)", color="#ffaa00")

        ax.set_title("Paper Trading — Symbol Performance", fontsize=14, color="white")
        ax.set_xlabel("Symbol", color="white")
        ax.set_ylabel("Net PnL ($)", color="white")
        ax.axhline(y=0, color="white", linestyle="-", alpha=0.2)
        ax2.legend(facecolor=grid, edgecolor=grid)
        ax.grid(True, alpha=0.2, color=grid)
        plt.xticks(rotation=45, ha="right")
        fig.tight_layout()
        fig.savefig(FIGURES_DIR / "paper_symbol_performance.png", dpi=150, facecolor=bg)
        plt.close(fig)

    def _chart_regime_performance(self, bg, grid, trades) -> None:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 7), facecolor=bg)
        ax1.set_facecolor(bg)
        ax2.set_facecolor(bg)

        regime_pnl: Dict[str, float] = {}
        regime_counts: Dict[str, int] = {}
        for t in trades:
            r = t.market_regime or "unknown"
            regime_pnl[r] = regime_pnl.get(r, 0) + t.net_pnl
            regime_counts[r] = regime_counts.get(r, 0) + 1

        # PnL by regime
        regimes = sorted(regime_pnl.keys())
        pnls = [regime_pnl[r] for r in regimes]
        colors = ["#00d4aa" if p > 0 else "#ff6b6b" for p in pnls]
        ax1.barh(regimes, pnls, color=colors, alpha=0.8)
        ax1.set_title("PnL by Regime", fontsize=12, color="white")
        ax1.set_xlabel("Net PnL ($)", color="white")
        ax1.axvline(x=0, color="white", linestyle="-", alpha=0.2)
        ax1.grid(True, alpha=0.2, color=grid)

        # Trade count by regime
        counts = [regime_counts[r] for r in regimes]
        ax2.barh(regimes, counts, color="#4a9eff", alpha=0.8)
        ax2.set_title("Trade Count by Regime", fontsize=12, color="white")
        ax2.set_xlabel("Number of Trades", color="white")
        ax2.grid(True, alpha=0.2, color=grid)

        fig.suptitle("Paper Trading — Regime Performance", fontsize=14, color="white")
        fig.tight_layout()
        fig.savefig(FIGURES_DIR / "paper_regime_performance.png", dpi=150, facecolor=bg)
        plt.close(fig)

    # ── Final report printing ────────────────────────────────────

    def _print_final_report(self, summary: PaperTradingSummary) -> None:
        """Print the final validation report."""
        logger.info("")
        logger.info("=" * 70)
        logger.info("         PAPER TRADING VALIDATION REPORT")
        logger.info("=" * 70)
        logger.info("")
        logger.info("Duration:              {:.1f} days", summary.duration_days)
        logger.info("Signals Generated:     {}", summary.total_signals)
        logger.info("Trades Executed:       {}", summary.total_trades)
        logger.info("Win Rate:              {:.1%}", summary.win_rate)
        logger.info("Profit Factor:         {:.2f}", summary.profit_factor)
        logger.info("Net Profit:            ${:,.2f}", summary.net_profit)
        logger.info("Max Drawdown:          {:.2f}%", summary.max_drawdown_pct)
        logger.info("Avg Slippage:          {:.1f} bps", summary.avg_slippage_bps)
        logger.info("API Errors:            {}", summary.api_errors)
        logger.info("Reconnects:            {}", summary.reconnects)
        logger.info("Uptime:                {:.1f}%", summary.uptime_pct)
        logger.info("")
        logger.info("─" * 70)
        logger.info("  PERFORMANCE DRIFT vs BACKTEST")
        logger.info("─" * 70)
        drift = summary.performance_drift
        logger.info("PF Drift:              {:.1f}%  ({})", drift.get("pf_drift_pct", 0), drift.get("interpretation", ""))
        logger.info("WR Drift:              {:.1f}%", drift.get("wr_drift_pct", 0))
        logger.info("DD Drift:              {:.1f}%", drift.get("dd_drift_pct", 0))
        logger.info("")
        logger.info("─" * 70)
        logger.info("  SUCCESS CRITERIA")
        logger.info("─" * 70)
        crit = summary.criteria
        logger.info("PF > 1.30:             {}", "✅ PASS" if crit.get("pf_gt_1_30") else "❌ FAIL")
        logger.info("WR > 48%:              {}", "✅ PASS" if crit.get("wr_gt_48pct") else "❌ FAIL")
        logger.info("DD < 10%:              {}", "✅ PASS" if crit.get("dd_lt_10pct") else "❌ FAIL")
        logger.info("Positive Profit:       {}", "✅ PASS" if crit.get("positive_profit") else "❌ FAIL")
        logger.info("99% Uptime:            {}", "✅ PASS" if crit.get("uptime_gt_99pct") else "❌ FAIL")
        logger.info("No Critical Failures:  {}", "✅ PASS" if crit.get("no_critical_failures") else "❌ FAIL")
        logger.info("")
        logger.info("=" * 70)
        result_color = "🟢" if summary.overall_result == "PASS" else "🔴"
        logger.info("  OVERALL RESULT:      {} {}", result_color, summary.overall_result)
        logger.info("  RECOMMENDATION:      {}", summary.recommendation)
        logger.info("=" * 70)
        logger.info("")
        logger.info("EXPORTS:")
        logger.info("  → {}", TRADES_CSV)
        logger.info("  → {}", SIGNALS_CSV)
        logger.info("  → {}", DAILY_CSV)
        logger.info("  → {}", WEEKLY_CSV)
        logger.info("  → {}", SUMMARY_JSON)
        logger.info("  → {} (7 charts)".format(FIGURES_DIR))
        logger.info("")


# ══════════════════════════════════════════════════════════════════
# STANDALONE RUNNER
# ══════════════════════════════════════════════════════════════════

async def run_paper_trading_validation() -> None:
    """Run the paper trading validation engine."""
    engine = PaperTradingEngine()

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _on_signal(sig):
        logger.info("Signal {} received — initiating graceful shutdown", sig.name)
        stop_event.set()

    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _on_signal, s)
        except NotImplementedError:
            pass

    async def _run():
        await engine.start()

    task = asyncio.create_task(_run())

    # Wait for shutdown signal
    await stop_event.wait()
    engine.is_running = False
    await asyncio.sleep(2)  # Allow graceful shutdown
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Phase 3 — Live Paper Trading Validation")
    parser.add_argument("--testnet", action="store_true", default=True,
                        help="Use Binance testnet (default: True)")
    parser.add_argument("--production", action="store_true",
                        help="Use Binance production data")
    args = parser.parse_args()

    if args.production:
        os.environ["BINANCE_TESTNET"] = "false"
    else:
        os.environ["BINANCE_TESTNET"] = "true"

    # Setup logging
    logger.remove()
    logger.add(
        sys.stderr,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan> — <level>{message}</level>"
        ),
        level="INFO",
    )
    logger.add(
        str(DATA_DIR.parent / "logs" / "paper_trading_{time:YYYY-MM-DD}.log"),
        rotation="1 day",
        retention="30 days",
        level="DEBUG",
    )

    logger.info("=" * 60)
    logger.info("PHASE 3 — LIVE PAPER TRADING VALIDATION")
    logger.info("=" * 60)
    logger.info("Mode: {}", "PRODUCTION" if args.production else "TESTNET")
    logger.info("PAPER_TRADING = TRUE")
    logger.info("EXECUTION_MODE = SIMULATION")
    logger.info("ORDER_MODE = NO_REAL_ORDERS")
    logger.info("=" * 60)

    asyncio.run(run_paper_trading_validation())


if __name__ == "__main__":
    main()
