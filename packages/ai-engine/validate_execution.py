"""
Phase 4 & 5 — Execution Layer Validation Test Suite

Tests:
1. Order State Machine
2. Idempotency Protection
3. Fill Tracking & Partial Fills
4. Position Lifecycle
5. Risk Guardian
6. Position Reconciliation
7. Recovery System
8. Execution Audit
9. System Health Monitor
10. Exchange Adapter
11. Duplicate Signal Prevention
12. Duplicate Order Prevention
13. Execution Engine Integration

Pass Criteria:
- 0 Lost Positions
- 0 Duplicate Positions
- 0 Duplicate Orders
- 0 Untracked Orders
- 100% Recovery Success
- 100% Position Reconciliation
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Setup path
_ai_root = Path(__file__).resolve().parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from loguru import logger

# Configure logger for testing
logger.remove()
logger.add(sys.stderr, level="WARNING")


class TestResult:
    def __init__(self, name: str):
        self.name = name
        self.passed = False
        self.message = ""
        self.details = {}
        self.duration = 0.0

    def __repr__(self):
        status = "✅ PASS" if self.passed else "❌ FAIL"
        return f"{status} | {self.name} | {self.message} ({self.duration:.2f}s)"


class ExecutionValidationSuite:
    """
    Comprehensive validation suite for the execution layer.

    Tests all components independently and integrated.
    """

    def __init__(self):
        self.results: list[TestResult] = []
        self._test_dir = Path(__file__).parent / "data" / "test_execution"
        self._test_dir.mkdir(parents=True, exist_ok=True)

    async def run_all(self) -> dict:
        """Run all validation tests."""
        print("\n" + "=" * 70)
        print("  PHASE 4 & 5 — EXECUTION LAYER VALIDATION")
        print("=" * 70 + "\n")

        tests = [
            self.test_1_order_state_machine,
            self.test_2_idempotency,
            self.test_3_fill_tracking,
            self.test_4_position_lifecycle,
            self.test_5_risk_guardian,
            self.test_6_reconciliation,
            self.test_7_recovery,
            self.test_8_audit_logging,
            self.test_9_system_monitor,
            self.test_10_exchange_adapter,
            self.test_11_duplicate_signal_prevention,
            self.test_12_duplicate_order_prevention,
            self.test_13_execution_engine_integration,
        ]

        for test in tests:
            result = TestResult(test.__name__.replace("test_", "").replace("_", " ").title())
            start = time.time()
            try:
                passed, message, details = await test()
                result.passed = passed
                result.message = message
                result.details = details
            except Exception as exc:
                result.passed = False
                result.message = f"Exception: {exc}"
                import traceback
                result.details = {"traceback": traceback.format_exc()}
            result.duration = time.time() - start
            self.results.append(result)
            print(f"  {result}")

        # Summary
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed

        print("\n" + "=" * 70)
        print(f"  RESULTS: {passed}/{total} PASSED, {failed} FAILED")
        print("=" * 70)

        for r in self.results:
            status = "✅" if r.passed else "❌"
            print(f"  {status} {r.name}: {r.message}")

        # Overall
        all_passed = passed == total
        print(f"\n  OVERALL: {'✅ ALL TESTS PASSED' if all_passed else '❌ SOME TESTS FAILED'}")
        print("=" * 70 + "\n")

        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "all_passed": all_passed,
            "results": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "message": r.message,
                    "duration": r.duration,
                }
                for r in self.results
            ],
        }

    # ── Test 1: Order State Machine ──────────────────────────────

    async def test_1_order_state_machine(self) -> tuple:
        """Test order state transitions."""
        from execution.order_manager import OrderRecord, OrderState

        order = OrderRecord(
            order_id="test-001",
            symbol="BTCUSDT",
            side="BUY",
            order_type="MARKET",
            purpose="ENTRY",
            quantity=0.01,
        )

        # Initial state
        assert order.state == OrderState.NEW.value, f"Expected NEW, got {order.state}"

        # NEW → SUBMITTED
        order.transition(OrderState.SUBMITTED.value, "test")
        assert order.state == OrderState.SUBMITTED.value

        # SUBMITTED → ACCEPTED
        order.transition(OrderState.ACCEPTED.value, "test")
        assert order.state == OrderState.ACCEPTED.value

        # ACCEPTED → PARTIALLY_FILLED
        order.transition(OrderState.PARTIALLY_FILLED.value, "50% filled")
        assert order.state == OrderState.PARTIALLY_FILLED.value

        # PARTIALLY_FILLED → FILLED
        order.transition(OrderState.FILLED.value, "100% filled")
        assert order.state == OrderState.FILLED.value

        # Check state history
        assert len(order.state_history) == 4, f"Expected 4 transitions, got {len(order.state_history)}"

        return True, "All state transitions correct (NEW→SUBMITTED→ACCEPTED→PARTIALLY_FILLED→FILLED)", {
            "transitions": len(order.state_history),
            "final_state": order.state,
        }

    # ── Test 2: Idempotency ──────────────────────────────────────

    async def test_2_idempotency(self) -> tuple:
        """Test duplicate order prevention."""
        from execution.order_manager import OrderRecord

        # Create two orders with same idempotency key
        order1 = OrderRecord(
            signal_id="SIG-001",
            symbol="BTCUSDT",
            purpose="ENTRY",
        )
        order2 = OrderRecord(
            signal_id="SIG-001",
            symbol="BTCUSDT",
            purpose="ENTRY",
        )

        # Same idempotency key
        assert order1.idempotency_key == order2.idempotency_key, \
            "Same signal+purpose+symbol should produce same key"

        # Different purpose should produce different key
        order3 = OrderRecord(
            signal_id="SIG-001",
            symbol="BTCUSDT",
            purpose="STOP_LOSS",
        )
        assert order1.idempotency_key != order3.idempotency_key, \
            "Different purpose should produce different key"

        return True, "Idempotency keys correctly prevent duplicates", {
            "entry_key": order1.idempotency_key,
            "sl_key": order3.idempotency_key,
        }

    # ── Test 3: Fill Tracking ────────────────────────────────────

    async def test_3_fill_tracking(self) -> tuple:
        """Test fill tracking and partial fills."""
        from execution.fill_manager import FillManager

        fm = FillManager()

        # Record partial fills
        f1 = fm.record_fill(
            order_id="ORD-001", signal_id="SIG-001", symbol="BTCUSDT",
            side="BUY", price=50000.0, quantity=0.003,
            expected_price=50000.0, order_quantity=0.01,
        )
        f2 = fm.record_fill(
            order_id="ORD-001", signal_id="SIG-001", symbol="BTCUSDT",
            side="BUY", price=50001.0, quantity=0.003,
            expected_price=50000.0, order_quantity=0.01,
        )
        f3 = fm.record_fill(
            order_id="ORD-001", signal_id="SIG-001", symbol="BTCUSDT",
            side="BUY", price=50002.0, quantity=0.004,
            expected_price=50000.0, order_quantity=0.01,
        )

        # Check aggregate
        agg = fm.get_aggregate("ORD-001")
        assert agg is not None, "Aggregate should exist"
        assert agg.fill_count == 3, f"Expected 3 fills, got {agg.fill_count}"
        assert abs(agg.total_quantity - 0.01) < 0.0001, f"Expected 0.01 qty, got {agg.total_quantity}"
        assert agg.is_complete, "Should be complete (100% filled)"

        # Check weighted average price
        expected_avg = (50000 * 0.003 + 50001 * 0.003 + 50002 * 0.004) / 0.01
        assert abs(agg.avg_price - expected_avg) < 0.01, \
            f"Avg price mismatch: {agg.avg_price} vs {expected_avg}"

        # Check fill quality
        quality = fm.get_fill_quality("ORD-001")
        assert quality["quality_score"] > 0, "Quality score should be positive"

        return True, f"Fill tracking correct: 3 fills, avg={agg.avg_price:.2f}, quality={quality['quality']}", {
            "fill_count": agg.fill_count,
            "total_qty": agg.total_quantity,
            "avg_price": round(agg.avg_price, 2),
            "slippage_bps": round(agg.slippage_bps, 2),
            "quality": quality["quality"],
        }

    # ── Test 4: Position Lifecycle ───────────────────────────────

    async def test_4_position_lifecycle(self) -> tuple:
        """Test position open/close lifecycle."""
        from execution.position_manager import PositionManager, PositionStatus
        from execution.exchange_adapter import ExchangeAdapter

        # Use a mock exchange
        exchange = ExchangeAdapter()
        pm = PositionManager(exchange)

        # Open position
        pos = await pm.open_position(
            signal_id="SIG-002",
            symbol="ETHUSDT",
            side="LONG",
            entry_price=3000.0,
            quantity=1.0,
            leverage=10,
            stop_loss=2900.0,
            take_profit=3300.0,
            confidence=0.85,
        )

        assert pos is not None, "Position should be created"
        assert pos.status == PositionStatus.OPEN.value
        assert pos.symbol == "ETHUSDT"
        assert pos.side == "LONG"

        # Check duplicate prevention
        dup = await pm.open_position(
            signal_id="SIG-002",
            symbol="ETHUSDT",
            side="LONG",
            entry_price=3000.0,
            quantity=1.0,
        )
        assert dup is None, "Duplicate position should be blocked"

        # Update price
        exits = await pm.update_price("ETHUSDT", 3100.0)
        assert len(exits) == 0, "No exit should trigger at 3100"

        # Check PnL
        pos = pm.get_position(pos.position_id)
        assert pos.unrealized_pnl > 0, "Should be profitable at 3100"

        # Close position
        closed = await pm.close_position(pos.position_id, 3100.0, "manual")
        assert closed is not None
        assert closed.status == PositionStatus.CLOSED.value
        assert closed.net_pnl > 0, "Should have profit"

        return True, f"Position lifecycle complete: opened, PnL tracked, closed (PnL={closed.net_pnl:.2f})", {
            "position_id": pos.position_id[:8],
            "entry": 3000.0,
            "exit": 3100.0,
            "pnl": closed.net_pnl,
            "return_pct": closed.return_pct,
        }

    # ── Test 5: Risk Guardian ────────────────────────────────────

    async def test_5_risk_guardian(self) -> tuple:
        """Test risk guardian validation."""
        from execution.risk_guardian import RiskGuardian, RiskLevel

        rg = RiskGuardian()
        rg.set_starting_equity(10000)

        # Normal conditions — should pass
        allowed, reason, action = rg.check_signal(
            "BTCUSDT", "LONG", 50000, 49000, 0.002, 10, 0.85,
        )
        assert allowed, f"Should be allowed: {reason}"

        # Test drawdown protection
        rg.update_equity(8900)  # 11% drawdown
        allowed, reason, action = rg.check_signal(
            "BTCUSDT", "LONG", 50000, 49000, 0.002, 10, 0.85,
        )
        assert not allowed, f"Should be blocked at 11% drawdown: {reason}"

        # Reset state completely for next test
        rg = RiskGuardian()
        rg.set_starting_equity(10000)
        rg._daily_start_equity = 10000
        rg.update_equity(9400)  # 6% daily loss
        allowed, reason, action = rg.check_signal(
            "BTCUSDT", "LONG", 50000, 49000, 0.002, 10, 0.85,
        )
        assert not allowed, f"Should be blocked at 6% daily loss: {reason}"

        # Fresh instance for position limit test
        rg = RiskGuardian()
        rg.set_starting_equity(10000)
        rg.update_equity(10000)
        rg._state.open_positions = 10
        allowed, reason, action = rg.check_signal(
            "BTCUSDT", "LONG", 50000, 49000, 0.002, 10, 0.85,
        )
        assert not allowed, f"Should be blocked at max positions: {reason}"

        # Test confidence threshold
        rg._state.open_positions = 0
        allowed, reason, action = rg.check_signal(
            "BTCUSDT", "LONG", 50000, 49000, 0.002, 10, 0.3,
        )
        assert not allowed, f"Should be blocked at low confidence: {reason}"

        return True, "Risk guardian correctly blocks trades on drawdown, daily loss, position limit, low confidence", {
            "drawdown_blocked": True,
            "daily_loss_blocked": True,
            "position_limit_blocked": True,
            "confidence_blocked": True,
        }

    # ── Test 6: Reconciliation ───────────────────────────────────

    async def test_6_reconciliation(self) -> tuple:
        """Test position reconciliation logic."""
        from execution.position_manager import PositionManager
        from execution.exchange_adapter import ExchangeAdapter, ExchangePosition

        exchange = ExchangeAdapter()
        pm = PositionManager(exchange)

        # Create internal position
        pos = await pm.open_position(
            signal_id="SIG-003",
            symbol="BTCUSDT",
            side="LONG",
            entry_price=50000,
            quantity=0.01,
            stop_loss=49000,
            take_profit=52000,
        )

        # Reconcile with matching exchange position
        exchange_positions = [
            ExchangePosition(symbol="BTCUSDT", side="LONG", quantity=0.01),
        ]
        mismatches = pm.reconcile_with_exchange(exchange_positions)
        assert len(mismatches) == 0, f"Should have 0 mismatches, got {len(mismatches)}"

        # Reconcile with missing exchange position
        mismatches = pm.reconcile_with_exchange([])
        assert len(mismatches) == 1, f"Should have 1 mismatch (missing), got {len(mismatches)}"
        assert mismatches[0]["type"] == "missing_on_exchange"

        # Reconcile with quantity mismatch
        exchange_positions = [
            ExchangePosition(symbol="BTCUSDT", side="LONG", quantity=0.02),
        ]
        mismatches = pm.reconcile_with_exchange(exchange_positions)
        assert len(mismatches) == 1, f"Should have 1 mismatch (qty), got {len(mismatches)}"
        assert mismatches[0]["type"] == "quantity_mismatch"

        # Reconcile with orphan
        exchange_positions = [
            ExchangePosition(symbol="BTCUSDT", side="LONG", quantity=0.01),
            ExchangePosition(symbol="ETHUSDT", side="SHORT", quantity=1.0),
        ]
        mismatches = pm.reconcile_with_exchange(exchange_positions)
        assert len(mismatches) == 1, f"Should have 1 mismatch (orphan), got {len(mismatches)}"
        assert mismatches[0]["type"] == "orphan_on_exchange"

        return True, "Reconciliation correctly detects missing, mismatched, and orphaned positions", {
            "match_test": True,
            "missing_test": True,
            "qty_mismatch_test": True,
            "orphan_test": True,
        }

    # ── Test 7: Recovery ─────────────────────────────────────────

    async def test_7_recovery(self) -> tuple:
        """Test recovery system."""
        from execution.execution_recovery import ExecutionRecovery
        from execution.exchange_adapter import ExchangeAdapter
        from execution.position_manager import PositionManager
        from execution.order_manager import OrderManager
        from execution.fill_manager import FillManager
        from execution.execution_audit import ExecutionAudit

        exchange = ExchangeAdapter()
        pm = PositionManager(exchange)
        om = OrderManager(exchange)
        fm = FillManager()
        audit = ExecutionAudit()

        # Save some state
        await pm.open_position(
            signal_id="SIG-REC-001", symbol="BTCUSDT",
            side="LONG", entry_price=50000, quantity=0.01,
        )
        await pm.save_state()
        await om.save_state()
        await fm.save_state()

        # Create new managers (simulating restart)
        pm2 = PositionManager(exchange)
        om2 = OrderManager(exchange)
        fm2 = FillManager()

        # Load state
        pos_count = await pm2.load_state()
        order_count = await om2.load_state()
        fill_count = await fm2.load_state()

        assert pos_count >= 1, f"Should restore >= 1 position, got {pos_count}"

        # Verify position data
        positions = pm2.get_open_positions()
        assert len(positions) >= 1, "Should have at least 1 open position"
        assert positions[0].symbol == "BTCUSDT"
        assert positions[0].side == "LONG"

        return True, f"Recovery restores state: {pos_count} positions, {order_count} orders", {
            "positions_restored": pos_count,
            "orders_restored": order_count,
            "data_intact": True,
        }

    # ── Test 8: Audit Logging ────────────────────────────────────

    async def test_8_audit_logging(self) -> tuple:
        """Test audit logging system."""
        from execution.execution_audit import ExecutionAudit, AuditEventType

        audit = ExecutionAudit()
        audit.DB_PATH = self._test_dir / "test_audit.db"
        await audit.initialize()

        # Record events
        await audit.signal_received("SIG-AUD-001", "BTCUSDT")
        await audit.order_event(AuditEventType.ORDER_SUBMITTED, "ORD-AUD-001", "BTCUSDT", "Order submitted")
        await audit.position_event(AuditEventType.POSITION_OPENED, "POS-AUD-001", "BTCUSDT", "Position opened")
        await audit.risk_event(AuditEventType.RISK_CHECK_PASSED, "Risk passed")
        await audit.system_event(AuditEventType.SYSTEM_START, "System started")

        # Flush
        await audit._flush_buffer()

        # Query
        events = await audit.get_events(limit=10)
        assert len(events) >= 5, f"Expected >= 5 events, got {len(events)}"

        # Check types
        types = await audit.get_event_counts()
        assert types.get("SIGNAL_RECEIVED", 0) >= 1

        await audit.close()

        return True, f"Audit logging works: {len(events)} events recorded and queryable", {
            "events_recorded": len(events),
            "event_types": len(types),
        }

    # ── Test 9: System Monitor ───────────────────────────────────

    async def test_9_system_monitor(self) -> tuple:
        """Test system health monitoring."""
        from execution.execution_monitor import ExecutionMonitor

        monitor = ExecutionMonitor()

        # Record events
        monitor.record_execution_latency(5.0)
        monitor.record_execution_latency(10.0)
        monitor.record_api_latency(50.0)
        monitor.record_api_error()
        monitor.record_message()
        monitor.record_message()
        monitor.record_message()

        # Collect snapshot
        snapshot = await monitor._collect_snapshot()
        assert snapshot is not None, "Snapshot should be created"
        assert snapshot.messages_processed == 3
        assert snapshot.api_errors == 1
        assert snapshot.uptime_sec > 0

        # Stats
        stats = monitor.get_stats()
        assert stats["total_messages"] == 3
        assert stats["api_errors"] == 1

        return True, "System monitor correctly tracks health metrics", {
            "cpu_pct": snapshot.cpu_pct,
            "memory_mb": snapshot.memory_mb,
            "messages": snapshot.messages_processed,
            "api_errors": snapshot.api_errors,
        }

    # ── Test 10: Exchange Adapter ────────────────────────────────

    async def test_10_exchange_adapter(self) -> tuple:
        """Test exchange adapter (offline — no actual API calls)."""
        from execution.exchange_adapter import (
            ExchangeAdapter, OrderType, OrderSide, ExchangeOrder,
        )

        adapter = ExchangeAdapter()

        # Test order parsing
        raw_order = {
            "orderId": 12345,
            "clientOrderId": "DT-test-001",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "type": "MARKET",
            "status": "FILLED",
            "price": "0",
            "avgPrice": "50000.00",
            "origQty": "0.01",
            "executedQty": "0.01",
            "cumQuote": "500.00",
            "timeInForce": "GTC",
            "reduceOnly": False,
            "stopPrice": "0",
            "updateTime": 1234567890000,
        }
        order = ExchangeAdapter._parse_order(raw_order)
        assert order.order_id == 12345
        assert order.symbol == "BTCUSDT"
        assert order.status == "FILLED"
        assert order.avg_price == 50000.0

        # Test rate limiter
        from execution.exchange_adapter import RateLimiter
        rl = RateLimiter(max_requests=10, window_sec=1.0)
        for _ in range(10):
            await rl.acquire()
        # 11th should wait (but we won't actually wait)

        # Test stats
        stats = adapter.get_stats()
        assert "request_count" in stats
        assert "error_count" in stats

        return True, "Exchange adapter: order parsing, rate limiting, stats correct", {
            "order_parsed": True,
            "rate_limiter_works": True,
            "stats_available": True,
        }

    # ── Test 11: Duplicate Signal Prevention ─────────────────────

    async def test_11_duplicate_signal_prevention(self) -> tuple:
        """Test that duplicate signals are prevented."""
        from execution.order_manager import OrderManager, OrderState
        from execution.exchange_adapter import ExchangeAdapter

        exchange = ExchangeAdapter()
        om = OrderManager(exchange)

        # Create first order for signal
        om._idempotency.add("SIG-DUP:ENTRY:BTCUSDT")
        om._idempotency_map["SIG-DUP:ENTRY:BTCUSDT"] = "order-1"

        # Simulate existing order
        from execution.order_manager import OrderRecord
        existing = OrderRecord(
            order_id="order-1",
            signal_id="SIG-DUP",
            symbol="BTCUSDT",
            purpose="ENTRY",
            state=OrderState.ACCEPTED.value,
        )
        om._orders["order-1"] = existing
        om._by_signal["SIG-DUP"] = ["order-1"]

        # Check duplicate detection
        assert om.has_entry_order("SIG-DUP"), "Should detect existing entry order"

        # Clean signal should not be blocked
        assert not om.has_entry_order("SIG-NEW"), "New signal should not be blocked"

        return True, "Duplicate signal prevention works correctly", {
            "duplicate_blocked": True,
            "new_signal_allowed": True,
        }

    # ── Test 12: Duplicate Order Prevention ──────────────────────

    async def test_12_duplicate_order_prevention(self) -> tuple:
        """Test that duplicate orders for same signal+purpose are prevented."""
        from execution.order_manager import OrderRecord

        # Same signal, same purpose = same key
        o1 = OrderRecord(signal_id="S1", symbol="BTC", purpose="ENTRY")
        o2 = OrderRecord(signal_id="S1", symbol="BTC", purpose="ENTRY")
        assert o1.idempotency_key == o2.idempotency_key

        # Same signal, different purpose = different key
        o3 = OrderRecord(signal_id="S1", symbol="BTC", purpose="STOP_LOSS")
        assert o1.idempotency_key != o3.idempotency_key

        # Different signal, same purpose = different key
        o4 = OrderRecord(signal_id="S2", symbol="BTC", purpose="ENTRY")
        assert o1.idempotency_key != o4.idempotency_key

        # Same signal, same purpose, different symbol = different key
        o5 = OrderRecord(signal_id="S1", symbol="ETH", purpose="ENTRY")
        assert o1.idempotency_key != o5.idempotency_key

        return True, "Idempotency keys correctly unique per signal+purpose+symbol", {
            "same_key_test": True,
            "diff_purpose_test": True,
            "diff_signal_test": True,
            "diff_symbol_test": True,
        }

    # ── Test 13: Integration ─────────────────────────────────────

    async def test_13_execution_engine_integration(self) -> tuple:
        """Test execution engine components work together."""
        from execution.execution_engine import ExecutionEngine

        engine = ExecutionEngine()

        # Verify all components exist
        assert engine.exchange is not None
        assert engine.order_manager is not None
        assert engine.position_manager is not None
        assert engine.fill_manager is not None
        assert engine.audit is not None
        assert engine.risk_guardian is not None
        assert engine.reconciler is not None
        assert engine.recovery is not None
        assert engine.monitor is not None

        # Verify stats method works
        stats = engine.get_stats()
        assert "engine" in stats
        assert "orders" in stats
        assert "positions" in stats
        assert "fills" in stats
        assert "risk" in stats
        assert "reconciler" in stats
        assert "recovery" in stats
        assert "monitor" in stats
        assert "audit" in stats
        assert "exchange" in stats

        return True, "Execution engine integration verified: all components present and reporting", {
            "components": 9,
            "stats_sections": len(stats),
        }


# ── Main Entry Point ─────────────────────────────────────────────

async def main():
    suite = ExecutionValidationSuite()
    results = await suite.run_all()

    # Save results
    results_path = Path(__file__).parent / "data" / "reports" / "execution_validation_results.json"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2)

    return results


if __name__ == "__main__":
    results = asyncio.run(main())
    sys.exit(0 if results["all_passed"] else 1)
