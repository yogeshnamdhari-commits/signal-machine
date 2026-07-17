"""
EMA_V5 Unit Tests — Tests for individual modules.
Each module is tested in isolation.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple

from loguru import logger


class EMAv5UnitTests:
    """Unit tests for EMA_V5 modules."""

    def __init__(self) -> None:
        self._results: List[Dict] = []
        self._passed = 0
        self._failed = 0

    def run_all(self) -> Dict[str, Any]:
        """Run all unit tests."""
        logger.info("📊 EMA_V5 unit tests starting")
        self._results = []
        self._passed = 0
        self._failed = 0

        # Test each module
        self._test_config()
        self._test_state_manager()
        self._test_cache()
        self._test_serializer()
        self._test_signal_engine()
        self._test_trade_manager()
        self._test_regime_engine()
        self._test_trend_engine()
        self._test_pullback_engine()
        self._test_candle_engine()
        self._test_volume_engine()
        self._test_confidence_engine()

        return self._compile_report()

    def _test_config(self) -> None:
        """Test configuration module."""
        try:
            from ..config import ema_v5_config
            assert ema_v5_config.ema.fast == 20
            assert ema_v5_config.ema.medium == 50
            assert ema_v5_config.signal.min_rr == 1.5
            assert ema_v5_config.confidence.min_confidence == 90.0
            self._record("config", True, "All config values correct")
        except Exception as e:
            self._record("config", False, str(e))

    def _test_state_manager(self) -> None:
        """Test state manager."""
        try:
            from ..state_manager import StateManager, NO_TREND, BUY_MODE, SELL_MODE
            sm = StateManager()
            sm.reset("TESTSYM")
            assert sm.get_state("TESTSYM") == NO_TREND
            sm.set_state("TESTSYM", BUY_MODE)
            assert sm.get_state("TESTSYM") == BUY_MODE
            sm.reset("TESTSYM")
            assert sm.get_state("TESTSYM") == NO_TREND
            self._record("state_manager", True, "State transitions correct")
        except Exception as e:
            self._record("state_manager", False, str(e))

    def _test_cache(self) -> None:
        """Test EMA cache."""
        try:
            from ..cache import EMACache
            cache = EMACache()
            assert cache.size == 0
            self._record("cache", True, "Cache initialized correctly")
        except Exception as e:
            self._record("cache", False, str(e))

    def _test_serializer(self) -> None:
        """Test serializer."""
        try:
            from ..storage.serializer import EMAv5Serializer
            ser = EMAv5Serializer()
            test_signal = {
                "symbol": "BTCUSDT", "side": "LONG", "entry": 100000,
                "sl": 99000, "timestamp": time.time(),
            }
            uuid = ser.generate_uuid(test_signal)
            assert uuid.startswith("emav5-")
            serialized = ser.serialize_signal(test_signal)
            assert serialized["symbol"] == "BTCUSDT"
            assert serialized["side"] == "LONG"
            self._record("serializer", True, f"UUID: {uuid[:20]}...")
        except Exception as e:
            self._record("serializer", False, str(e))

    def _test_signal_engine(self) -> None:
        """Test signal engine."""
        try:
            from ..signal_engine import SignalEngine
            se = SignalEngine()
            # Test duplicate check
            assert se._check_duplicate("BTCUSDT", "BUY_MODE") == True
            self._record("signal_engine", True, "Signal engine initialized")
        except Exception as e:
            self._record("signal_engine", False, str(e))

    def _test_trade_manager(self) -> None:
        """Test trade manager."""
        try:
            from ..trade_manager import TradeManager
            tm = TradeManager()
            assert tm.open_count == 0
            assert tm.has_position("BTCUSDT") == False
            self._record("trade_manager", True, "Trade manager initialized")
        except Exception as e:
            self._record("trade_manager", False, str(e))

    def _test_regime_engine(self) -> None:
        """Test regime engine."""
        try:
            from ..regime_engine import RegimeEngine
            re = RegimeEngine()
            self._record("regime_engine", True, "Regime engine initialized")
        except Exception as e:
            self._record("regime_engine", False, str(e))

    def _test_trend_engine(self) -> None:
        """Test trend engine."""
        try:
            from ..trend_engine import TrendEngine
            te = TrendEngine()
            self._record("trend_engine", True, "Trend engine initialized")
        except Exception as e:
            self._record("trend_engine", False, str(e))

    def _test_pullback_engine(self) -> None:
        """Test pullback engine."""
        try:
            from ..pullback_engine import PullbackEngine
            pe = PullbackEngine()
            self._record("pullback_engine", True, "Pullback engine initialized")
        except Exception as e:
            self._record("pullback_engine", False, str(e))

    def _test_candle_engine(self) -> None:
        """Test candle engine."""
        try:
            from ..candle_engine import CandleEngine
            ce = CandleEngine()
            self._record("candle_engine", True, "Candle engine initialized")
        except Exception as e:
            self._record("candle_engine", False, str(e))

    def _test_volume_engine(self) -> None:
        """Test volume engine."""
        try:
            from ..volume_engine import VolumeEngine
            ve = VolumeEngine()
            self._record("volume_engine", True, "Volume engine initialized")
        except Exception as e:
            self._record("volume_engine", False, str(e))

    def _test_confidence_engine(self) -> None:
        """Test confidence engine."""
        try:
            from ..confidence_engine import ConfidenceEngine
            ce = ConfidenceEngine()
            self._record("confidence_engine", True, "Confidence engine initialized")
        except Exception as e:
            self._record("confidence_engine", False, str(e))

    def _record(self, test_name: str, passed: bool, details: str) -> None:
        """Record a test result."""
        if passed:
            self._passed += 1
        else:
            self._failed += 1
        self._results.append({
            "test": test_name,
            "passed": passed,
            "details": details,
        })

    def _compile_report(self) -> Dict[str, Any]:
        """Compile test report."""
        return {
            "test_type": "unit",
            "total": self._passed + self._failed,
            "passed": self._passed,
            "failed": self._failed,
            "pass_rate": round(self._passed / max(self._passed + self._failed, 1) * 100, 1),
            "results": self._results,
        }
