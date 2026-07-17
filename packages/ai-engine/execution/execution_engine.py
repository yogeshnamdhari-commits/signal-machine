"""
Execution Engine — Main orchestrator for institutional-grade execution.

Coordinates:
- Signal → Order conversion
- Risk validation
- Order submission via exchange adapter
- Fill tracking
- Position management
- Stop loss / take profit management
- Position reconciliation
- System recovery
- Health monitoring
- Audit logging

Integrates with:
- DeltaTerminalEngine (signal generation)
- EventBus (decoupled communication)
- DataBridge (dashboard)
- Database (persistence)
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from loguru import logger

import sys
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from config import config
from execution.exchange_adapter import (
    ExchangeAdapter, OrderType, OrderSide, PositionSide,
    TimeInForce, ExchangeError,
)
from execution.order_manager import OrderManager, OrderRecord, OrderState, OrderPurpose
from execution.position_manager import PositionManager, Position, PositionStatus
from execution.fill_manager import FillManager
from execution.execution_audit import ExecutionAudit, AuditEventType
from execution.risk_guardian import RiskGuardian, RiskAction
from execution.position_reconciler import PositionReconciler
from execution.execution_recovery import ExecutionRecovery
from execution.execution_monitor import ExecutionMonitor
from execution.multi_exchange_portfolio_risk import MultiExchangePortfolioRiskEngine, RiskLimits
from exchanges.base_exchange import BaseExchange
from exchanges.binance_adapter import BinanceAdapter
from exchanges.bybit_adapter import BybitAdapter
from exchanges.okx_adapter import OKXAdapter
from exchanges.delta_adapter import DeltaAdapter
from exchanges.smart_order_router import SmartOrderRouter
from core.event_bus import bus
from execution.arbitrage_engine import ArbitrageEngine

from execution.capital_allocator import CapitalAllocationEngine, AllocationRequest, AllocationModel


class ExecutionEngine:
    """
    Institutional-grade execution engine.

    Signal → Order → Fill → Position lifecycle management
    with full idempotency, recovery, reconciliation, and monitoring.

    Flow:
    1. Signal received (from EventBus or direct call)
    2. Risk validation (RiskGuardian)
    3. Position sizing (existing PositionSizingEngine)
    4. Entry confirmation (existing EntryConfirmationEngine)
    5. Order creation (OrderManager with idempotency)
    6. Order submission (ExchangeAdapter)
    7. Fill tracking (FillManager)
    8. Position creation (PositionManager)
    9. Stop loss / take profit placement
    10. Continuous monitoring (SL/TP checks, reconciliation, health)
    """

    STATE_FILE = _ai_root / "data" / "execution" / "engine_state.json"
    SL_CHECK_INTERVAL = 1.0  # Check SL/TP every 1 second
    ORDER_SYNC_INTERVAL = 10.0  # Sync order status every 10 seconds
    STATE_SAVE_INTERVAL = 60.0  # Save state every 60 seconds

    def __init__(self) -> None:
        # Core components
        self.exchanges: Dict[str, BaseExchange] = {
            "binance": BinanceAdapter(config.binance.api_key, config.binance.api_secret),
            "bybit": BybitAdapter(config.bybit.api_key, config.bybit.api_secret),
            "okx": OKXAdapter(config.okx.api_key, config.okx.api_secret, config.okx.passphrase),
            "delta": DeltaAdapter(config.delta.api_key, config.delta.api_secret),
        }
        self.order_manager = OrderManager(self.exchanges["binance"]) # Primary state tracking
        self.position_manager = PositionManager(self.exchanges["binance"])
        self.fill_manager = FillManager()
        self.audit = ExecutionAudit()
        self.router = SmartOrderRouter(self.exchanges, self.audit)
        self.allocator = CapitalAllocationEngine()
        self.portfolio_risk = MultiExchangePortfolioRiskEngine(RiskLimits())
        self.reconciler = PositionReconciler(self.exchanges["binance"], self.position_manager, self.order_manager, self.audit)
        self.recovery = ExecutionRecovery(
            self.exchanges, self.position_manager, self.order_manager,
            self.fill_manager, self.audit,
        )
        self.monitor = ExecutionMonitor()
        self.arbitrage_engine = ArbitrageEngine(self.exchanges, self, self.audit)

        # State
        self._running = False
        self._tasks: List[asyncio.Task] = []
        self._start_time = 0.0
        self._signal_count = 0
        self._trade_count = 0
        self._equity = config.risk.max_position_pct * 500  # Default starting equity

        # Connect callbacks
        self.order_manager.set_callbacks(
            on_fill=self._on_order_filled,
            on_cancel=self._on_order_cancelled,
            on_reject=self._on_order_rejected,
        )
        self.position_manager.set_callbacks(
            on_close=self._on_position_closed,
        )

        # State file
        self.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    # ── Lifecycle ────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the execution engine."""
        self._start_time = time.time()
        self._running = True

        logger.info("═══════════════════════════════════════════")
        logger.info("  EXECUTION ENGINE — STARTING")
        logger.info("═══════════════════════════════════════════")

        # Initialize components
        await self.audit.initialize()
        for exchange in self.exchanges.values():
            await exchange.connect()

        # Record start
        await self.audit.system_event(
            AuditEventType.SYSTEM_START,
            "Execution engine started",
        )

        # Recovery
        recovery_result = await self.recovery.recover()
        if recovery_result.account_balance > 0:
            self._equity = recovery_result.account_balance

        # Start subsystems
        await self.reconciler.start()
        await self.monitor.start()
        await self.arbitrage_engine.start()

        # Start main loops
        self._tasks = [
            asyncio.create_task(self._sl_tp_loop(), name="sl_tp_check"),
            asyncio.create_task(self._order_sync_loop(), name="order_sync"),
            asyncio.create_task(self._state_save_loop(), name="state_save"),
            asyncio.create_task(self._timeout_loop(), name="timeout_check"),
        ]

        # Register event bus handler
        bus.subscribe("execution_signal", self.on_signal)

        logger.info("═══════════════════════════════════════════")
        logger.info("  EXECUTION ENGINE — READY")
        logger.info("  Equity: ${:,.2f}", self._equity)
        logger.info("  Positions: {}", len(self.position_manager.get_open_positions()))
        logger.info("═══════════════════════════════════════════")

    async def stop(self) -> None:
        """Stop the execution engine gracefully."""
        logger.info("Stopping execution engine...")
        self._running = False

        # Cancel tasks
        for t in self._tasks:
            if not t.done():
                t.cancel()
        done, pending = await asyncio.wait(self._tasks, timeout=10.0)
        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

        # Stop subsystems
        await self.reconciler.stop()
        await self.monitor.stop()
        await self.arbitrage_engine.stop()

        # Save state
        await self._save_state()

        # Close components
        await self.audit.close()
        for exchange in self.exchanges.values():
            await exchange.disconnect()

        await self.audit.system_event(
            AuditEventType.SYSTEM_STOP,
            "Execution engine stopped",
        )

        logger.info("Execution engine stopped")

    # ── Signal Processing ────────────────────────────────────────

    async def on_signal(self, signal: Dict) -> Optional[Position]:
        """
        Process a trading signal.

        Full pipeline:
        1. Validate signal
        2. Check idempotency
        3. Risk validation
        4. Position sizing
        5. Order creation and submission
        6. Fill tracking
        7. Position creation
        8. SL/TP order placement
        """
        signal_id = signal.get("id", "")
        symbol = signal.get("symbol", "")
        side = signal.get("type", "")  # LONG / SHORT
        entry_price = signal.get("entry_price", 0)
        stop_loss = signal.get("stop_loss", 0)
        take_profit = signal.get("take_profit", 0)
        confidence = signal.get("confidence", 0)
        inst_score = signal.get("institutional_score", 0)
        regime = signal.get("regime", "")
        rr = signal.get("risk_reward", 0)

        if not all([signal_id, symbol, side, entry_price]):
            return None

        # ═══════════════════════════════════════════════════════════════
        # P0 GATE: Block zero-confidence / unknown-regime trades
        # June 16 proof: 3 trades with conf=0, regime=unknown, inst=0
        # lost $9.04 — ALL were losses (0% win rate)
        # ═══════════════════════════════════════════════════════════════
        if confidence < 0.85 or regime in ("unknown", "") or inst_score == 0:
            logger.warning(
                "🚫 GATE_BLOCKED: {} {} — conf={:.1%} regime={} inst_score={}",
                symbol, side, confidence, regime, inst_score,
            )
            await self.audit.signal_rejected(signal_id, f"confidence_gate: conf={confidence:.1%} regime={regime}")
            return None

        # ═══════════════════════════════════════════════════════════════
        # SAFETY NET: Reject trades with missing SL/TP
        # June 16 proof: BCHUSDT, SYNUSDT, METUSDT opened SL=0, TP=0
        # had zero exit protection — lost $3.96 total
        # ═══════════════════════════════════════════════════════════════
        if stop_loss == 0 or take_profit == 0:
            logger.warning(
                "🚫 SL_TP_BLOCKED: {} {} — SL={} TP={}",
                symbol, side, stop_loss, take_profit,
            )
            await self.audit.signal_rejected(signal_id, f"sl_tp_gate: SL={stop_loss} TP={take_profit}")
            return None

        self._signal_count += 1

        await self.audit.signal_received(signal_id, symbol, {
            "side": side, "entry": entry_price, "sl": stop_loss,
            "tp": take_profit, "confidence": confidence,
        })

        # Idempotency: check if already processed
        if self.order_manager.has_entry_order(signal_id):
            logger.info("Signal already has entry order: {}", signal_id[:8])
            await self.audit.signal_rejected(signal_id, "duplicate_entry_order")
            return None

        if self.position_manager.has_open_position(signal_id):
            logger.info("Signal already has open position: {}", signal_id[:8])
            await self.audit.signal_rejected(signal_id, "duplicate_position")
            return None

        # 1. Capital Allocation Decision
        alloc_req = AllocationRequest(
            symbol=symbol,
            exchange="binance", # Initial context, router will refine
            signal_score=confidence * 100, # Assuming confidence is 0-1
            confidence=confidence,
            volatility=0.02, # To be supplied by scanner/signal
            market_regime=regime or "range",
            portfolio_equity=self._equity, # Current total equity
            win_rate=0.55, # From historical database
            profit_factor=1.5,
            is_arbitrage=False # This is a regular signal, not arbitrage
        )
        
        allocation = await self.allocator.allocate(alloc_req)
        
        if allocation.capital_usd <= 0:
            logger.warning("Signal rejected by Capital Allocator: {}", allocation.reason)
            await self.audit.signal_rejected(signal_id, f"allocation_zero: {allocation.reason}")
            return None
            
        # Determine quantity and leverage from allocation result
        quantity = allocation.position_size # allocation.position_size is already notional, needs to be converted to base asset quantity
        leverage = allocation.leverage

        # 2. Dynamic Routing - Score all venues and select the most efficient
        target_side = "BUY" if side == "LONG" else "SELL"
        routing = await self.router.route_order(symbol, target_side, "MARKET", quantity)
        
        if not routing or routing["exchange"] == "none":
            logger.error("SmartOrderRouter failed to find a valid venue for {}", symbol)
            return None
            
        exchange = self.exchanges[routing["exchange"]]

        # 3. Global Portfolio Risk Validation
        snapshot = await self.portfolio_risk.get_snapshot(self._equity)
        can_trade, violations = self.portfolio_risk.can_add_position(symbol, side, quantity, entry_price, snapshot)
        
        if not can_trade:
            logger.warning("Trade blocked by Portfolio Risk: {}", violations)
            return None

        # Determine order side
        order_side = OrderSide.BUY if side == "LONG" else OrderSide.SELL
        
        # Create and submit entry order
        entry_order = await self.order_manager.create_order(
            signal_id=signal_id,
            symbol=symbol,
            side=order_side.value,
            order_type=OrderType.MARKET,
            purpose=OrderPurpose.ENTRY, # This is an entry order for a signal
            quantity=quantity,
            price=entry_price,
            leverage=leverage,
            timeout_sec=30,
        )

        if not entry_order:
            logger.error("Failed to create entry order for {}", signal_id[:8])
            return None

        # Wait for fill (with timeout)
        fill_timeout = 10.0  # seconds
        start = time.time()
        while time.time() - start < fill_timeout:
            if entry_order.state == OrderState.FILLED.value:
                break
            if entry_order.state in (
                OrderState.CANCELLED.value,
                OrderState.REJECTED.value,
                OrderState.FAILED.value,
            ):
                logger.warning("Entry order failed: {} state={}",
                              entry_order.order_id[:8], entry_order.state)
                return None
            await asyncio.sleep(0.1)

        if entry_order.state != OrderState.FILLED.value:
            logger.warning("Entry order not filled in time: {} state={}",
                          entry_order.order_id[:8], entry_order.state)
            return None

        # Record fill
        fill = self.fill_manager.record_fill(
            order_id=entry_order.order_id,
            signal_id=signal_id,
            symbol=symbol,
            side=order_side.value,
            price=entry_order.avg_price or entry_price,
            quantity=entry_order.executed_qty or quantity,
            fee=entry_order.avg_price * (entry_order.executed_qty or quantity) * 0.0004,
            expected_price=entry_price,
            order_quantity=quantity,
        )

        # Create position
        position = await self.position_manager.open_position(
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            entry_price=entry_order.avg_price or entry_price,
            quantity=entry_order.executed_qty or quantity,
            leverage=leverage,
            stop_loss=stop_loss,
            take_profit=take_profit,
            confidence=confidence,
            institutional_score=inst_score,
            market_regime=regime,
            risk_reward=rr,
            entry_order_id=entry_order.order_id,
            fees=fill.fee,
        )

        if not position:
            logger.error("Failed to create position for {}", signal_id[:8])
            return None

        self._trade_count += 1

        # Place stop loss order
        if stop_loss > 0:
            sl_side = OrderSide.SELL if side == "LONG" else OrderSide.BUY
            sl_order = await self.order_manager.create_order(
                signal_id=signal_id,
                symbol=symbol,
                side=sl_side.value,
                order_type=OrderType.STOP_MARKET,
                purpose=OrderPurpose.STOP_LOSS,
                quantity=position.quantity,
                stop_price=stop_loss,
                reduce_only=True,
                leverage=leverage,
                timeout_sec=0,  # No timeout for SL
            )
            if sl_order:
                position.stop_order_id = sl_order.order_id

        # Place take profit order
        if take_profit > 0:
            tp_side = OrderSide.SELL if side == "LONG" else OrderSide.BUY
            tp_order = await self.order_manager.create_order(
                signal_id=signal_id,
                symbol=symbol,
                side=tp_side.value,
                order_type=OrderType.TAKE_PROFIT_MARKET,
                purpose=OrderPurpose.TAKE_PROFIT,
                quantity=position.quantity,
                stop_price=take_profit,
                reduce_only=True,
                leverage=leverage,
                timeout_sec=0,  # No timeout for TP
            )
            if tp_order:
                position.tp_order_id = tp_order.order_id

        await self.audit.position_event(
            AuditEventType.POSITION_OPENED,
            position.position_id,
            symbol,
            f"Position opened: {side} {symbol} qty={position.quantity:.6f} "
            f"entry={position.entry_price:.4f} SL={stop_loss:.4f} TP={take_profit:.4f}",
        )

        logger.info("✅ Position opened: {} {} {} qty={:.6f} entry={:.4f}",
                    position.position_id[:8], side, symbol,
                    position.quantity, position.entry_price)

        return position

    async def place_arbitrage_order(
        self,
        arb_id: str,
        exchange_name: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float, # Expected price from arbitrage opportunity
        is_long_leg: bool,
    ) -> Optional[OrderRecord]:
        """
        Places an order for an arbitrage leg, bypassing SmartOrderRouter and CapitalAllocationEngine.
        Still goes through PortfolioRiskEngine and OrderManager.
        """
        # 1. Global Portfolio Risk Validation (arbitrage-specific)
        snapshot = await self.portfolio_risk.get_snapshot(self._equity)
        can_trade, violations = self.portfolio_risk.can_add_position(
            symbol, side, quantity, price, snapshot, is_arbitrage=True
        )
        
        if not can_trade:
            logger.warning(f"Arbitrage leg blocked by Portfolio Risk on {exchange_name}: {violations}")
            await self.audit.system_event(
                AuditEventType.RISK_CHECK_FAILED,
                f"Arbitrage leg blocked: {violations}",
                {"arb_id": arb_id, "symbol": symbol, "exchange": exchange_name}
            )
            return None

        exchange = self.exchanges[exchange_name]
        order_side = OrderSide.BUY if side == "BUY" else OrderSide.SELL
        
        # Create and submit order for arbitrage leg
        order = await self.order_manager.create_order(
            signal_id=arb_id, # Use arb_id as signal_id for tracking
            symbol=symbol,
            side=order_side.value,
            order_type=OrderType.MARKET, # Arbitrage usually uses market orders for speed
            purpose=OrderPurpose.ENTRY, # Both legs are "entry" for the arbitrage
            quantity=quantity,
            price=price, # Use the expected price for tracking
            leverage=1, # Arbitrage is typically 1x leverage
            timeout_sec=config.arbitrage.execution_timeout_sec, # Short timeout for arb legs
            exchange_adapter=exchange # Pass the specific exchange adapter
        )

        if not order:
            logger.error(f"Failed to create arbitrage order for {arb_id} on {exchange_name}")
            await self.audit.system_event(
                AuditEventType.ORDER_FAILED,
                f"Failed to create arbitrage order for {arb_id} on {exchange_name}",
                {"arb_id": arb_id, "symbol": symbol, "exchange": exchange_name}
            )
            return None

        return order

    # ── Order Callbacks ──────────────────────────────────────────

    async def _on_order_filled(self, order: OrderRecord) -> None:
        """Handle order fill."""
        await self.audit.order_event(
            AuditEventType.ORDER_FILLED,
            order.order_id,
            order.symbol,
            f"Order filled: {order.purpose} {order.side} {order.symbol} "
            f"qty={order.executed_qty} @ {order.avg_price}",
        )

        if order.purpose == OrderPurpose.ENTRY.value:
            # Entry filled — position will be created by on_signal
            pass
        elif order.purpose == OrderPurpose.STOP_LOSS.value:
            # SL filled — close position
            pos = self._find_position_by_sl(order)
            if pos:
                await self.position_manager.close_position(
                    pos.position_id, order.avg_price, "stop_loss",
                )
        elif order.purpose == OrderPurpose.TAKE_PROFIT.value:
            # TP filled — close position
            pos = self._find_position_by_tp(order)
            if pos:
                await self.position_manager.close_position(
                    pos.position_id, order.avg_price, "take_profit",
                )

    async def _on_order_cancelled(self, order: OrderRecord) -> None:
        """Handle order cancellation."""
        await self.audit.order_event(
            AuditEventType.ORDER_CANCELLED,
            order.order_id,
            order.symbol,
            f"Order cancelled: {order.purpose} {order.symbol}",
        )

    async def _on_order_rejected(self, order: OrderRecord) -> None:
        """Handle order rejection."""
        await self.audit.order_event(
            AuditEventType.ORDER_REJECTED,
            order.order_id,
            order.symbol,
            f"Order rejected: {order.purpose} {order.symbol} — {order.rejection_reason}",
        )

    async def _on_position_closed(self, position: Position) -> None:
        """Handle position close."""
        # Update portfolio risk
        self.portfolio_risk.record_pnl(position.net_pnl)

        # Update equity
        self._equity += position.net_pnl

        # Cancel any remaining orders for this signal
        await self.order_manager.cancel_signal_orders(
            position.signal_id, "Position closed",
        )

        event_type = {
            "stop_loss": AuditEventType.POSITION_STOPPED,
            "take_profit": AuditEventType.POSITION_TP_HIT,
            "liquidation": AuditEventType.POSITION_LIQUIDATED,
        }.get(position.close_reason, AuditEventType.POSITION_CLOSED)

        await self.audit.position_event(
            event_type,
            position.position_id,
            position.symbol,
            f"Position closed: {position.side} {position.symbol} "
            f"PnL={position.net_pnl:.2f} reason={position.close_reason}",
        )

    def _find_position_by_sl(self, order: OrderRecord) -> Optional[Position]:
        """Find position associated with a stop loss order."""
        for pos in self.position_manager.get_open_positions():
            if pos.stop_order_id == order.order_id:
                return pos
            if pos.signal_id == order.signal_id:
                return pos
        return None

    def _find_position_by_tp(self, order: OrderRecord) -> Optional[Position]:
        """Find position associated with a take profit order."""
        for pos in self.position_manager.get_open_positions():
            if pos.tp_order_id == order.order_id:
                return pos
            if pos.signal_id == order.signal_id:
                return pos
        return None

    # ── Background Loops ─────────────────────────────────────────

    async def _sl_tp_loop(self) -> None:
        """Check stop loss and take profit conditions every second."""
        while self._running:
            try:
                open_positions = self.position_manager.get_open_positions()
                for pos in open_positions:
                    if pos.current_price <= 0:
                        continue

                    exits = await self.position_manager.update_price(
                        pos.symbol, pos.current_price,
                    )

                    for pos_id, reason in exits:
                        # Close position directly (exchange SL/TP orders handle actual execution)
                        # This is a backup check
                        logger.info("SL/TP hit detected: {} {}", pos_id[:8], reason)
                        await self.position_manager.close_position(
                            pos_id, pos.current_price, reason,
                        )

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("SL/TP check error: {}", exc)

            await asyncio.sleep(self.SL_CHECK_INTERVAL)

    async def _order_sync_loop(self) -> None:
        """Sync order status with exchange."""
        while self._running:
            try:
                await self.order_manager.sync_all_active()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Order sync error: {}", exc)
            await asyncio.sleep(self.ORDER_SYNC_INTERVAL)

    async def _state_save_loop(self) -> None:
        """Persist state to disk."""
        while self._running:
            try:
                await self._save_state()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("State save error: {}", exc)
            await asyncio.sleep(self.STATE_SAVE_INTERVAL)

    async def _timeout_loop(self) -> None:
        """Check for timed-out orders."""
        while self._running:
            try:
                expired = await self.order_manager.check_timeouts()
                if expired > 0:
                    logger.info("Cancelled {} timed-out orders", expired)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Timeout check error: {}", exc)
            await asyncio.sleep(10)

    async def _save_state(self) -> None:
        """Save all component states."""
        await asyncio.gather(
            self.order_manager.save_state(),
            self.position_manager.save_state(),
            self.fill_manager.save_state(),
            self._save_engine_state(),
            return_exceptions=True,
        )

    async def _save_engine_state(self) -> None:
        """Save engine-level state."""
        try:
            state = {
                "equity": self._equity,
                "signal_count": self._signal_count,
                "trade_count": self._trade_count,
                "start_time": self._start_time,
                "saved_at": time.time(),
            }
            tmp = str(self.STATE_FILE) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f)
            Path(tmp).rename(self.STATE_FILE)
        except Exception as exc:
            logger.error("Engine state save failed: {}", exc)

    async def _load_engine_state(self) -> None:
        """Load engine-level state."""
        if not self.STATE_FILE.exists():
            return
        try:
            with open(self.STATE_FILE) as f:
                state = json.load(f)
            self._equity = state.get("equity", self._equity)
            self._signal_count = state.get("signal_count", 0)
            self._trade_count = state.get("trade_count", 0)
            logger.info("Engine state loaded: equity=${:.2f} signals={} trades={}",
                       self._equity, self._signal_count, self._trade_count)
        except Exception as exc:
            logger.error("Engine state load failed: {}", exc)

    # ── Price Updates ────────────────────────────────────────────

    async def update_price(self, symbol: str, price: float) -> None:
        """Update price for position P&L tracking."""
        await self.position_manager.update_price(symbol, price)

    # ── Queries ──────────────────────────────────────────────────

    def get_equity(self) -> float:
        return self._equity

    def get_open_positions(self) -> List[Position]:
        return self.position_manager.get_open_positions()

    def get_portfolio_metrics(self) -> Dict:
        return self.position_manager.get_portfolio_metrics()

    def get_risk_state(self) -> Dict:
        return {}

    def get_system_health(self) -> Dict:
        snapshot = self.monitor.get_latest_snapshot()
        return snapshot.to_dict() if snapshot else {}

    # ── Comprehensive Stats ──────────────────────────────────────

    def get_stats(self) -> Dict:
        """Get comprehensive execution engine statistics."""
        uptime = time.time() - self._start_time if self._start_time else 0
        return {
            "engine": {
                "running": self._running,
                "uptime_sec": round(uptime, 0),
                "equity": round(self._equity, 2),
                "signal_count": self._signal_count,
                "trade_count": self._trade_count,
            },
            "orders": self.order_manager.get_stats(),
            "positions": self.position_manager.get_stats(),
            "fills": self.fill_manager.get_stats(),
            "risk": {},
            "reconciler": self.reconciler.get_stats(),
            "recovery": self.recovery.get_stats(),
            "monitor": self.monitor.get_stats(),
            "audit": self.audit.get_stats(),
            "router": self.router.router_stats(),
            "arbitrage": self.arbitrage_engine.get_stats(),
            "allocator": self.allocator.get_stats(),
            "exchange": self.exchanges["binance"].get_stats(),
        }
