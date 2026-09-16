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
        self._test_duplicate_survives_trade_close()
        self._test_partial_exit_no_refire()
        self._test_stale_price_gate()
        self._test_outage_recovery_entry_gate()
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

    def _test_duplicate_survives_trade_close(self) -> None:
        """Regression: duplicate protection must survive a trade close.

        Production audit (2026-08-11): an identical SIRENUSDT signal was
        re-emitted 63s after the position closed because clear_cooldown()
        erased _last_signal[symbol]. Same-symbol dedup must stay active for
        the full same_symbol_sec window (3600s).
        """
        import types

        from ..config import ema_v5_config
        from ..signal_engine import SignalEngine

        _now = [time.time()]
        fake_time = types.SimpleNamespace(time=lambda: _now[0])
        import scanner.ema_v5.signal_engine as se_mod

        orig_time = se_mod.time
        se_mod.time = fake_time

        def _ema_data(entry: float = 0.03086):
            atr = (0.03089557 - entry) / ema_v5_config.signal.sl_atr_mult
            return {
                "last_close": entry,
                "atr_14": atr,
                "ema20": 0.03095,
                "ema50": 0.03099,
                "ema144": 0.0305,
                "ema200": 0.0304,
                "ema20_slope": 0.02,
                "ema50_slope": 0.02,
                "ema144_slope": -0.01,
                "ema200_slope": -0.01,
            }

        try:
            se = SignalEngine()

            def _emit(ts: float, symbol: str = "SIRENUSDT"):
                _now[0] = ts
                return se.generate(
                    symbol=symbol,
                    regime="SELL_MODE",
                    regime_eval={},
                    trend_eval={},
                    pullback_eval={"touch_level": "ema20"},
                    candle_eval={},
                    volume_eval={},
                    confidence_eval={"confidence": 0.471},
                    ema_data=_ema_data(),
                )

            t0 = _now[0]

            # 1. Emit signal #1 at T
            sig1 = _emit(t0)
            assert sig1 is not None, "first signal should be emitted"
            assert sig1["side"] == "SHORT"
            assert sig1["confidence"] == 0.471

            # 2. Position closes -> on_trade_closed -> clear_cooldown
            _now[0] = t0 + 5
            se.clear_cooldown("SIRENUSDT")

            # clear_cooldown must NOT erase duplicate identity
            assert "SIRENUSDT" in se._last_signal, "duplicate identity must survive close"
            assert "SIRENUSDT" not in se._cooldowns, "trade cooldown should be cleared"

            # 3. Identical signal 63s after T -> rejected as duplicate
            sig2 = _emit(t0 + 63)
            assert sig2 is None, "identical signal after close must be rejected"
            assert se.last_rejection_reason == "duplicate", (
                f"expected duplicate rejection, got {se.last_rejection_reason}"
            )

            # 4. New signal after full dedup window -> emitted
            sig3 = _emit(t0 + 3600 + 1)
            assert sig3 is not None, "signal after dedup window must be emitted"
            assert sig3["side"] == "SHORT"

            self._record(
                "duplicate_survives_trade_close",
                True,
                "Identical signal 63s after close rejected; new signal after 3600s emitted",
            )
        except Exception as e:
            self._record("duplicate_survives_trade_close", False, str(e))
        finally:
            se_mod.time = orig_time

    def _test_partial_exit_no_refire(self) -> None:
        """Regression: take_profit_1/2 must fire EXACTLY ONCE per level.

        Audit (2026-08-11): HOME/KOMA/C98/ADAUSDT. check_exit_conditions()
        receives a fresh DB dict every scan cycle. Before the fix, the
        _tp1_hit/_tp2_hit flags and current_tp_index set on that throwaway
        dict were lost, so TP1 re-fired 20-45x and whittled quantity to dust.
        The fix (a) persists current_tp_index via update_position_tp_index(),
        (b) mirrors TP state onto self._positions, and (c) derives tp1_hit/
        tp2_hit from the persisted index — so a fresh dict that already has
        current_tp_index>=2 can never re-fire take_profit_1.
        """
        import asyncio

        try:
            from execution.risk_engine import RiskEngine

            risk = RiskEngine()
            sym = "KOMAUSDT"
            pos = {
                "symbol": sym, "side": "LONG", "entry_price": 100.0,
                "stop_loss": 98.0, "take_profit": 0.0,
                "take_profit_1": 102.0, "take_profit_2": 0.0, "take_profit_3": 0.0,
                "quantity": 1.0, "leverage": 10,
                "opened_at": time.time() - 60, "current_tp_index": 1,
            }
            risk._positions[sym] = pos

            # Scan 1: price above TP1 → TP1 partial fires, advances to TP2
            close, reason = risk.check_exit_conditions(pos, 103.0)
            assert close is True and reason == "take_profit_1", f"scan1 got {reason}"
            assert pos["current_tp_index"] == 2, "current_tp_index must advance to 2"
            assert pos["_tp1_hit"] is True
            assert risk._positions[sym]["current_tp_index"] == 2, "in-memory state must mirror"
            assert sym in risk._partials_taken

            # Scan 2 (SAME object, pre-fix bug path): must NOT re-fire TP1
            close2, reason2 = risk.check_exit_conditions(pos, 103.0)
            assert reason2 != "take_profit_1", "same dict must not re-fire TP1"

            # Scan 3: fresh DB dict (as get_open_positions would return AFTER the
            # fix persists current_tp_index=2). No _tp1_hit flag on it — the
            # persisted index alone must suppress the re-fire.
            fresh = dict(pos)
            fresh["current_tp_index"] = 2
            fresh.pop("_tp1_hit", None)
            close3, reason3 = risk.check_exit_conditions(fresh, 103.0)
            assert reason3 != "take_profit_1", f"fresh persisted dict re-fired TP1: {reason3}"

            # Verify the DB method exists and writes without error
            from database.signal_repository import SignalRepository
            assert hasattr(SignalRepository, "update_position_tp_index"), \
                "update_position_tp_index() must exist on SignalRepository"
            assert asyncio.iscoroutinefunction(SignalRepository.update_position_tp_index)

            self._record(
                "partial_exit_no_refire", True,
                "TP1 fires once; current_tp_index=2 persists and suppresses re-fire on fresh dicts",
            )
        except Exception as e:
            self._record("partial_exit_no_refire", False, str(e))

    def _test_stale_price_gate(self) -> None:
        """Regression: risk/SL/trailing evaluation must reject stale prices.

        BLUAIUSDT incident: WS outage + REST fallback failure left a stale
        _ticker_data price; the risk loop kept evaluating it so the SL was
        never triggered. The engine must (a) measure ticker age and (b) skip
        protective-exit evaluation entirely when the cached price is stale AND
        no fresh REST price is obtainable.
        """
        import asyncio

        try:
            from core.engine import DeltaTerminalEngine
            from config import config

            # Lightweight engine stub — avoids the heavy production __init__
            eng = object.__new__(DeltaTerminalEngine)
            eng._ticker_data = {}
            eng.symbol_data = {}

            # Outage: ticker cache is 1 hour old (BLUAI scenario)
            eng._ticker_data["BLUAIUSDT"] = {"price": 0.0120, "last_update": time.time() - 3600}
            age = eng._price_age("BLUAIUSDT")
            assert age is not None and age > config.risk.max_price_age_sec, \
                f"stale ticker must exceed max_price_age_sec (age={age})"

            # Outage continues: REST refresh ALSO fails → fresh price unobtainable
            async def _no_rest(_sym):
                return None
            eng._fetch_price_rest = _no_rest
            fresh = asyncio.run(eng._price_fresh("BLUAIUSDT"))
            assert fresh is None, "stale + REST-fail must yield no fresh price (gate blocks)"

            # Recovery: REST is back → fresh price obtained and cached
            async def _rest_ok(_sym):
                return 0.0115
            eng._fetch_price_rest = _rest_ok
            fresh2 = asyncio.run(eng._price_fresh("BLUAIUSDT"))
            assert fresh2 == 0.0115, "recovered REST must refresh the price"
            assert eng._ticker_data["BLUAIUSDT"]["last_update"] > time.time() - 2, \
                "last_update must be re-stamped after refresh"
            assert eng._price_age("BLUAIUSDT") < config.risk.max_price_age_sec, \
                "post-refresh ticker must be fresh"

            self._record(
                "stale_price_gate", True,
                "Stale ticker (age>60s) blocks eval when REST fails; refresh restores freshness",
            )
        except Exception as e:
            self._record("stale_price_gate", False, str(e))

    def _test_outage_recovery_entry_gate(self) -> None:
        """Regression: no new entries during outage; entries resume post-recovery.

        PLUMEUSDT/ADAUSDT were opened during the outage tail. The feed-health
        gate must block an entry while the price is stale/unavailable, and
        allow it again once a fresh price is obtainable (outage recovery).
        """
        import asyncio

        try:
            from core.engine import DeltaTerminalEngine
            from config import config

            eng = object.__new__(DeltaTerminalEngine)
            eng._ticker_data = {}
            eng.symbol_data = {}

            # Healthy feed → entry allowed WITHOUT touching REST
            eng._ticker_data["BTCUSDT"] = {"price": 60000.0, "last_update": time.time()}

            async def _boom(_sym):
                raise AssertionError("REST must NOT be called for fresh cached data")
            eng._fetch_price_rest = _boom
            fresh = asyncio.run(eng._price_fresh("BTCUSDT"))
            assert fresh == 60000.0, "fresh cached price must pass the gate without REST"

            # Outage: stale cache + REST down → gate blocks the entry
            eng._ticker_data["BTCUSDT"]["last_update"] = time.time() - 9000
            async def _down(_sym):
                return None
            eng._fetch_price_rest = _down
            blocked = asyncio.run(eng._price_fresh("BTCUSDT"))
            assert blocked is None, "stale price during outage must block entry"

            # Recovery: REST restored → gate passes, entry allowed
            async def _back(_sym):
                return 60100.0
            eng._fetch_price_rest = _back
            recovered = asyncio.run(eng._price_fresh("BTCUSDT"))
            assert recovered == 60100.0, "post-recovery fresh price must allow entry"

            self._record(
                "outage_recovery_entry_gate", True,
                "Entry blocked while stale (REST down), allowed after recovery (REST up)",
            )
        except Exception as e:
            self._record("outage_recovery_entry_gate", False, str(e))

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
