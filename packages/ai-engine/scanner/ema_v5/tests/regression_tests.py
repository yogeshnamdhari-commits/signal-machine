"""
EMA_V5 Regression Tests — Tests that verify no regressions in existing functionality.
Ensures changes don't break existing behavior.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from loguru import logger


class EMAv5RegressionTests:
    """Regression tests for EMA_V5."""

    def __init__(self) -> None:
        self._results: List[Dict] = []
        self._passed = 0
        self._failed = 0

    def run_all(self) -> Dict[str, Any]:
        """Run all regression tests."""
        logger.info("📊 EMA_V5 regression tests starting")
        self._results = []
        self._passed = 0
        self._failed = 0

        self._test_scanner_unchanged()
        self._test_config_unchanged()
        self._test_state_transitions()
        self._test_signal_dedup()
        self._test_cooldown_logic()
        self._test_storage_integrity()
        self._test_bridge_integrity()

        return self._compile_report()

    def _test_scanner_unchanged(self) -> None:
        """Verify scanner module is unchanged."""
        try:
            from ..scanner import EMAv5Scanner
            scanner = EMAv5Scanner()

            # Verify expected attributes exist
            assert hasattr(scanner, '_scan_count')
            assert hasattr(scanner, '_signal_count')
            assert hasattr(scanner, '_start_time')
            assert hasattr(scanner, 'evaluate')

            # Verify method signatures
            import inspect
            sig = inspect.signature(scanner.evaluate)
            params = list(sig.parameters.keys())
            assert 'symbol' in params
            assert 'market_data' in params

            self._record("scanner_unchanged", True, "Scanner interface unchanged")
        except Exception as e:
            self._record("scanner_unchanged", False, str(e))

    def _test_config_unchanged(self) -> None:
        """Verify config values are unchanged."""
        try:
            from ..config import ema_v5_config

            # Verify critical config values
            assert ema_v5_config.ema.fast == 20
            assert ema_v5_config.ema.medium == 50
            assert ema_v5_config.ema.institutional == 144
            assert ema_v5_config.ema.long_term == 200
            assert ema_v5_config.signal.min_rr == 1.5
            assert ema_v5_config.signal.sl_atr_mult == 1.5
            assert ema_v5_config.confidence.min_confidence == 90.0
            assert ema_v5_config.trade.max_positions == 3

            self._record("config_unchanged", True, "Config values unchanged")
        except Exception as e:
            self._record("config_unchanged", False, str(e))

    def _test_state_transitions(self) -> None:
        """Verify state machine transitions are correct."""
        try:
            from ..state_manager import (
                StateManager, NO_TREND, BUY_MODE, SELL_MODE,
                WAITING_PULLBACK, WAITING_CONFIRMATION,
                ACTIVE_BUY, ACTIVE_SELL, TRADE_CLOSED,
            )

            sm = StateManager()

            # Test valid transitions
            sm.reset("TEST")
            assert sm.get_state("TEST") == NO_TREND

            sm.set_state("TEST", BUY_MODE)
            assert sm.get_state("TEST") == BUY_MODE

            sm.set_state("TEST", WAITING_PULLBACK)
            assert sm.get_state("TEST") == WAITING_PULLBACK

            sm.set_state("TEST", WAITING_CONFIRMATION)
            assert sm.get_state("TEST") == WAITING_CONFIRMATION

            sm.set_state("TEST", ACTIVE_BUY)
            assert sm.get_state("TEST") == ACTIVE_BUY

            sm.set_state("TEST", TRADE_CLOSED)
            assert sm.get_state("TEST") == TRADE_CLOSED

            # Test reset
            sm.reset("TEST")
            assert sm.get_state("TEST") == NO_TREND

            self._record("state_transitions", True, "All state transitions correct")
        except Exception as e:
            self._record("state_transitions", False, str(e))

    def _test_signal_dedup(self) -> None:
        """Verify signal deduplication works."""
        try:
            from ..signal_engine import SignalEngine

            se = SignalEngine()

            # First signal should pass
            assert se._check_duplicate("BTCUSDT", "BUY_MODE") == True

            # Record signal
            se._last_signal["BTCUSDT"] = {"regime": "BUY_MODE", "timestamp": time.time()}

            # Same signal should be duplicate
            assert se._check_duplicate("BTCUSDT", "BUY_MODE") == False

            # Different symbol should pass
            assert se._check_duplicate("ETHUSDT", "BUY_MODE") == True

            self._record("signal_dedup", True, "Deduplication works correctly")
        except Exception as e:
            self._record("signal_dedup", False, str(e))

    def _test_cooldown_logic(self) -> None:
        """Verify cooldown logic works."""
        try:
            from ..signal_engine import SignalEngine

            se = SignalEngine()

            # No cooldown initially
            assert se._check_cooldown("BTCUSDT") == True

            # Set cooldown
            se._cooldowns["BTCUSDT"] = time.time() + 3600  # 1 hour from now

            # Should be blocked
            assert se._check_cooldown("BTCUSDT") == False

            # Different symbol should pass
            assert se._check_cooldown("ETHUSDT") == True

            self._record("cooldown_logic", True, "Cooldown logic works correctly")
        except Exception as e:
            self._record("cooldown_logic", False, str(e))

    def _test_storage_integrity(self) -> None:
        """Verify storage integrity."""
        try:
            import tempfile
            import os

            from ..storage.database import EMAv5Database

            db_path = os.path.join(tempfile.gettempdir(), "test_regression.db")
            if os.path.exists(db_path):
                os.remove(db_path)

            db = EMAv5Database(db_path)

            # Store signal
            signal = {
                "uuid": "regression-001", "timestamp": time.time(), "date": "2026-06-25",
                "time": "12:00:00", "exchange": "Binance", "symbol": "BTCUSDT",
                "side": "LONG", "entry": 100000, "stop_loss": 99000, "tp1": 101500,
                "confidence": 0.92, "regime": "BUY_MODE", "strategy_version": "ema_v5",
            }
            result = db.store_signal(signal)
            assert result == True

            # Retrieve
            retrieved = db.get_signal("regression-001")
            assert retrieved is not None
            assert retrieved["symbol"] == "BTCUSDT"

            # Duplicate protection
            result2 = db.store_signal(signal)
            assert result2 == True
            assert db.count_signals() == 1  # Still 1, not 2

            os.remove(db_path)
            self._record("storage_integrity", True, "Storage integrity maintained")
        except Exception as e:
            self._record("storage_integrity", False, str(e))

    def _test_bridge_integrity(self) -> None:
        """Verify bridge file integrity."""
        try:
            import json
            from pathlib import Path

            bridge_path = Path(__file__).resolve().parent.parent.parent.parent / "data" / "bridge" / "ema_v5.json"

            if bridge_path.exists():
                with open(bridge_path) as f:
                    data = json.load(f)

                assert "ema_v5" in data
                assert "scanner" in data["ema_v5"]
                assert "states" in data["ema_v5"]
                assert "signals" in data["ema_v5"]
                assert "health" in data["ema_v5"]

                self._record("bridge_integrity", True, "Bridge file structure intact")
            else:
                self._record("bridge_integrity", True, "Bridge file not found (OK for fresh install)")
        except Exception as e:
            self._record("bridge_integrity", False, str(e))

    def _record(self, test_name: str, passed: bool, details: str) -> None:
        """Record a test result."""
        if passed:
            self._passed += 1
        else:
            self._failed += 1
        self._results.append({"test": test_name, "passed": passed, "details": details})

    def _compile_report(self) -> Dict[str, Any]:
        """Compile test report."""
        return {
            "test_type": "regression",
            "total": self._passed + self._failed,
            "passed": self._passed,
            "failed": self._failed,
            "pass_rate": round(self._passed / max(self._passed + self._failed, 1) * 100, 1),
            "results": self._results,
        }
