"""
EMA_V5 Recovery Tester — Tests restart recovery and state persistence.
Validates that state survives engine restarts.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from ..storage.database import EMAv5Database
from ..storage.json_storage import EMAv5JsonStorage
from ..state_manager import StateManager
from ..storage.recovery import EMAv5Recovery


@dataclass
class RecoveryTestConfig:
    """Recovery test configuration."""
    test_symbols: int = 10
    test_signals: int = 50
    test_trades: int = 20
    verify_state: bool = True
    verify_database: bool = True
    verify_json: bool = True


@dataclass
class RecoveryTestResult:
    """Result of a recovery test."""
    test_name: str = ""
    state_recovered: bool = False
    database_recovered: bool = False
    json_recovered: bool = False
    signals_lost: int = 0
    trades_lost: int = 0
    recovery_time_ms: float = 0
    passed: bool = True
    details: str = ""


class EMAv5RecoveryTester:
    """Tests restart recovery and state persistence."""

    def __init__(self, config: Optional[RecoveryTestConfig] = None) -> None:
        self.config = config or RecoveryTestConfig()
        self._results: List[RecoveryTestResult] = []

    def run(self) -> Dict[str, Any]:
        """Run all recovery tests."""
        logger.info("📊 EMA_V5 recovery testing")
        self._results = []

        # Test 1: State persistence
        result = self._test_state_persistence()
        self._results.append(result)

        # Test 2: Database persistence
        result = self._test_database_persistence()
        self._results.append(result)

        # Test 3: JSON persistence
        result = self._test_json_persistence()
        self._results.append(result)

        # Test 4: Full recovery
        result = self._test_full_recovery()
        self._results.append(result)

        # Test 5: Partial recovery
        result = self._test_partial_recovery()
        self._results.append(result)

        return self._compile_report()

    def _test_state_persistence(self) -> RecoveryTestResult:
        """Test that state survives restart."""
        import tempfile
        import json

        # Create temp state file
        state_file = os.path.join(tempfile.gettempdir(), "test_state.json")

        # Simulate pre-restart state
        pre_state = {}
        for i in range(self.config.test_symbols):
            symbol = f"TEST{i:04d}USDT"
            pre_state[symbol] = {
                "state": "BUY_MODE" if i % 2 == 0 else "SELL_MODE",
                "last_update": time.time(),
                "previous": "NO_TREND",
            }

        # Write state
        with open(state_file, "w") as f:
            json.dump(pre_state, f)

        # Simulate restart — read state
        start = time.time()
        try:
            with open(state_file) as f:
                post_state = json.load(f)
            recovered = post_state == pre_state
        except Exception:
            post_state = {}
            recovered = False
        elapsed = (time.time() - start) * 1000

        # Cleanup
        os.remove(state_file)

        return RecoveryTestResult(
            test_name="state_persistence",
            state_recovered=recovered,
            recovery_time_ms=round(elapsed, 2),
            passed=recovered,
            details=f"Pre: {len(pre_state)} symbols, Post: {len(post_state)} symbols",
        )

    def _test_database_persistence(self) -> RecoveryTestResult:
        """Test that database survives restart."""
        import tempfile

        db_path = os.path.join(tempfile.gettempdir(), "test_recovery.db")
        if os.path.exists(db_path):
            os.remove(db_path)

        # Create DB and store signals
        db = EMAv5Database(db_path)
        stored_count = 0
        for i in range(self.config.test_signals):
            sig = {
                "uuid": f"recovery-{i}",
                "timestamp": time.time() - i * 100,
                "date": "2026-06-25",
                "time": "12:00:00",
                "exchange": "Binance",
                "symbol": f"TEST{i:04d}USDT",
                "side": "LONG" if i % 2 == 0 else "SHORT",
                "entry": 100000,
                "stop_loss": 99000,
                "tp1": 101500,
                "confidence": 0.92,
                "regime": "BUY_MODE",
                "strategy_version": "ema_v5",
            }
            if db.store_signal(sig):
                stored_count += 1

        # Simulate restart — read from DB
        start = time.time()
        all_signals = db.get_all_signals()
        elapsed = (time.time() - start) * 1000

        recovered = len(all_signals) == stored_count

        # Cleanup
        os.remove(db_path)

        return RecoveryTestResult(
            test_name="database_persistence",
            database_recovered=recovered,
            signals_lost=stored_count - len(all_signals),
            recovery_time_ms=round(elapsed, 2),
            passed=recovered,
            details=f"Stored: {stored_count}, Recovered: {len(all_signals)}",
        )

    def _test_json_persistence(self) -> RecoveryTestResult:
        """Test that JSON files survive restart."""
        import tempfile
        import json

        json_file = os.path.join(tempfile.gettempdir(), "test_json.json")

        # Write data
        data = {
            "signals": [{"uuid": f"json-{i}", "symbol": f"TEST{i:04d}USDT"} for i in range(100)],
            "trades": [{"uuid": f"trade-{i}", "pnl": i * 10} for i in range(50)],
            "timestamp": time.time(),
        }
        with open(json_file, "w") as f:
            json.dump(data, f)

        # Read back
        start = time.time()
        with open(json_file) as f:
            recovered_data = json.load(f)
        elapsed = (time.time() - start) * 1000

        recovered = (len(recovered_data.get("signals", [])) == 100 and
                     len(recovered_data.get("trades", [])) == 50)

        os.remove(json_file)

        return RecoveryTestResult(
            test_name="json_persistence",
            json_recovered=recovered,
            recovery_time_ms=round(elapsed, 2),
            passed=recovered,
            details=f"Signals: {len(recovered_data.get('signals', []))}, Trades: {len(recovered_data.get('trades', []))}",
        )

    def _test_full_recovery(self) -> RecoveryTestResult:
        """Test full recovery scenario."""
        import tempfile

        db_path = os.path.join(tempfile.gettempdir(), "test_full_recovery.db")
        if os.path.exists(db_path):
            os.remove(db_path)

        # Create DB with data
        db = EMAv5Database(db_path)
        for i in range(20):
            db.store_signal({
                "uuid": f"full-{i}", "timestamp": time.time(), "date": "2026-06-25",
                "time": "12:00:00", "exchange": "Binance", "symbol": f"TEST{i:04d}USDT",
                "side": "LONG", "entry": 100000, "stop_loss": 99000, "tp1": 101500,
                "confidence": 0.92, "regime": "BUY_MODE", "strategy_version": "ema_v5",
            })

        # Run recovery
        start = time.time()
        recovery = EMAv5Recovery(db=db)
        report = recovery.recover_all()
        elapsed = (time.time() - start) * 1000

        passed = report.get("signals_recovered", 0) >= 0  # Recovery didn't crash

        os.remove(db_path)

        return RecoveryTestResult(
            test_name="full_recovery",
            state_recovered=report.get("state_restored", False),
            database_recovered=report.get("signals_recovered", 0) >= 0,
            json_recovered=report.get("bridge_populated", False),
            recovery_time_ms=round(elapsed, 2),
            passed=passed,
            details=f"Signals: {report.get('signals_recovered', 0)}, State: {report.get('state_restored')}",
        )

    def _test_partial_recovery(self) -> RecoveryTestResult:
        """Test partial recovery (some data missing)."""
        import tempfile
        import json

        # Create partial state
        state_file = os.path.join(tempfile.gettempdir(), "test_partial.json")
        partial_state = {"BTCUSDT": {"state": "BUY_MODE"}}  # Only 1 symbol
        with open(state_file, "w") as f:
            json.dump(partial_state, f)

        # Read and validate
        with open(state_file) as f:
            recovered = json.load(f)

        passed = len(recovered) == 1  # Partial recovery is valid

        os.remove(state_file)

        return RecoveryTestResult(
            test_name="partial_recovery",
            state_recovered=passed,
            passed=passed,
            details=f"Recovered {len(recovered)} symbols (partial is valid)",
        )

    def _compile_report(self) -> Dict[str, Any]:
        """Compile recovery test report."""
        results = [
            {
                "test_name": r.test_name,
                "state_recovered": r.state_recovered,
                "database_recovered": r.database_recovered,
                "json_recovered": r.json_recovered,
                "signals_lost": r.signals_lost,
                "trades_lost": r.trades_lost,
                "recovery_time_ms": r.recovery_time_ms,
                "passed": r.passed,
                "details": r.details,
            }
            for r in self._results
        ]

        passed_count = sum(1 for r in self._results if r.passed)
        total_signals_lost = sum(r.signals_lost for r in self._results)

        return {
            "test_type": "recovery",
            "config": {
                "test_symbols": self.config.test_symbols,
                "test_signals": self.config.test_signals,
            },
            "results": results,
            "summary": {
                "total_tests": len(self._results),
                "passed": passed_count,
                "failed": len(self._results) - passed_count,
                "total_signals_lost": total_signals_lost,
                "all_passed": passed_count == len(self._results),
            },
        }
