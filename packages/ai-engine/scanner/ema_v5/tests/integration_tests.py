"""
EMA_V5 Integration Tests — Tests for module interactions.
Validates that modules work together correctly.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from loguru import logger


class EMAv5IntegrationTests:
    """Integration tests for EMA_V5 module interactions."""

    def __init__(self) -> None:
        self._results: List[Dict] = []
        self._passed = 0
        self._failed = 0

    def run_all(self) -> Dict[str, Any]:
        """Run all integration tests."""
        logger.info("📊 EMA_V5 integration tests starting")
        self._results = []
        self._passed = 0
        self._failed = 0

        self._test_scanner_initialization()
        self._test_signal_flow()
        self._test_storage_flow()
        self._test_verification_flow()
        self._test_analytics_flow()
        self._test_performance_flow()
        self._test_report_flow()

        return self._compile_report()

    def _test_scanner_initialization(self) -> None:
        """Test that scanner initializes all sub-engines."""
        try:
            from ..scanner import EMAv5Scanner
            scanner = EMAv5Scanner()
            assert hasattr(scanner, 'cache')
            assert hasattr(scanner, 'regime_engine')
            assert hasattr(scanner, 'trend_engine')
            assert hasattr(scanner, 'pullback_engine')
            assert hasattr(scanner, 'candle_engine')
            assert hasattr(scanner, 'volume_engine')
            assert hasattr(scanner, 'confidence_engine')
            assert hasattr(scanner, 'signal_engine')
            assert hasattr(scanner, 'trade_manager')
            assert hasattr(scanner, 'state_manager')
            self._record("scanner_init", True, "All sub-engines initialized")
        except Exception as e:
            self._record("scanner_init", False, str(e))

    def _test_signal_flow(self) -> None:
        """Test signal generation flow."""
        try:
            from ..signal_engine import SignalEngine
            from ..config import ema_v5_config

            se = SignalEngine()

            # Test cooldown check
            assert se._check_cooldown("BTCUSDT") == True

            # Test duplicate check
            assert se._check_duplicate("BTCUSDT", "BUY_MODE") == True

            self._record("signal_flow", True, "Signal flow checks passed")
        except Exception as e:
            self._record("signal_flow", False, str(e))

    def _test_storage_flow(self) -> None:
        """Test storage flow (serialize → store → retrieve)."""
        try:
            import tempfile
            import os

            from ..storage.serializer import EMAv5Serializer
            from ..storage.database import EMAv5Database

            db_path = os.path.join(tempfile.gettempdir(), "test_integration.db")
            if os.path.exists(db_path):
                os.remove(db_path)

            # Serialize
            ser = EMAv5Serializer()
            test_signal = {
                "symbol": "BTCUSDT", "side": "LONG", "entry": 100000,
                "sl": 99000, "take_profit_1": 101500, "timestamp": time.time(),
                "confidence": 0.92, "regime": "BUY_MODE", "strategy_version": "ema_v5",
                "ema_data": {"ema20": 100100, "ema50": 99800, "ema144": 99500, "ema200": 99000},
                "components": {"trend": "bullish", "regime": "strong", "candle": "engulfing", "volume": "confirmed"},
            }
            serialized = ser.serialize_signal(test_signal)

            # Store
            db = EMAv5Database(db_path)
            result = db.store_signal(serialized)
            assert result == True

            # Retrieve
            retrieved = db.get_signal(serialized["uuid"])
            assert retrieved is not None
            assert retrieved["symbol"] == "BTCUSDT"

            os.remove(db_path)
            self._record("storage_flow", True, "Serialize → Store → Retrieve OK")
        except Exception as e:
            self._record("storage_flow", False, str(e))

    def _test_verification_flow(self) -> None:
        """Test verification flow."""
        try:
            from ..verification.verifier import EMAv5Verifier

            v = EMAv5Verifier()
            signal = {
                "symbol": "BTCUSDT", "side": "LONG", "entry": 100000, "sl": 99000,
                "take_profit_1": 101500, "take_profit_2": 103000, "take_profit_3": 105000,
                "confidence": 0.92, "regime": "BUY_MODE", "uuid": "test-int-001",
                "timestamp": time.time(), "strategy_version": "ema_v5",
            }
            ema = {"ema20": 100100, "ema50": 99800, "ema144": 99500, "ema200": 99000, "slope_ema20": 0.3, "slope_ema50": 0.2}
            regime = {"regime": "BUY_MODE", "reason": "strong"}
            trend = {"direction": "bullish", "trend_score": 85}
            pb = {"pullback_detected": True, "touch_level": "ema20", "bounce_confirmed": True}
            candle = {"pattern_found": True, "pattern_name": "engulfing", "candle_score": 90}
            vol = {"volume_ok": True, "volume_ratio": 1.5, "volume_score": 80}
            conf = {"confidence": 0.92, "passed": True, "breakdown": {"trend": 90}}

            verdict, diag = v.verify(signal, ema, regime, trend, pb, candle, vol, conf, state="BUY_MODE")
            assert verdict in ("PASS", "WARNING", "FAIL")
            assert len(diag.checks) == 12

            self._record("verification_flow", True, f"Verdict: {verdict}, checks: {len(diag.checks)}")
        except Exception as e:
            self._record("verification_flow", False, str(e))

    def _test_analytics_flow(self) -> None:
        """Test analytics flow."""
        try:
            from ..analytics.performance_calculator import PerformanceCalculator
            from ..analytics.risk_metrics import RiskMetrics

            pc = PerformanceCalculator()
            rm = RiskMetrics()

            # Test empty metrics
            empty = pc._empty_metrics()
            assert empty["total_trades"] == 0

            empty_risk = rm._empty_risk()
            assert empty_risk["sharpe_ratio"] == 0

            self._record("analytics_flow", True, "Analytics modules initialized correctly")
        except Exception as e:
            self._record("analytics_flow", False, str(e))

    def _test_performance_flow(self) -> None:
        """Test performance tracking flow."""
        try:
            from ..performance.real_time_tracker import EMAv5RealTimeTracker, TradeRecord

            tracker = EMAv5RealTimeTracker()
            trade = TradeRecord(symbol="BTCUSDT", side="LONG", pnl=100, result="win", timestamp=time.time())
            tracker.record_trade(trade)

            metrics = tracker.get_current_metrics()
            assert metrics["total_trades"] == 1
            assert metrics["wins"] == 1

            self._record("performance_flow", True, "Real-time tracking works")
        except Exception as e:
            self._record("performance_flow", False, str(e))

    def _test_report_flow(self) -> None:
        """Test report generation flow."""
        try:
            from ..reports.daily_report import DailyReport
            from ..reports.report_formatter import ReportFormatter

            formatter = ReportFormatter()
            test_report = {"report_type": "test", "summary": {"total_trades": 10, "wins": 6, "losses": 4, "win_rate": 60, "total_pnl": 500, "profit_factor": 2.5, "avg_pnl": 50, "expectancy": 50}}

            text = formatter.to_text(test_report)
            assert "EMA_V5" in text

            md = formatter.to_markdown(test_report)
            assert "# EMA_V5" in md

            self._record("report_flow", True, "Report formatting works")
        except Exception as e:
            self._record("report_flow", False, str(e))

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
            "test_type": "integration",
            "total": self._passed + self._failed,
            "passed": self._passed,
            "failed": self._failed,
            "pass_rate": round(self._passed / max(self._passed + self._failed, 1) * 100, 1),
            "results": self._results,
        }
