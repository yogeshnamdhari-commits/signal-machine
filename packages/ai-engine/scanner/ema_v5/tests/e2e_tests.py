"""
EMA_V5 E2E Tests — End-to-end tests for complete workflows.
Tests the full signal lifecycle from generation to storage.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from loguru import logger


class EMAv5E2ETests:
    """End-to-end tests for EMA_V5 complete workflows."""

    def __init__(self) -> None:
        self._results: List[Dict] = []
        self._passed = 0
        self._failed = 0

    def run_all(self) -> Dict[str, Any]:
        """Run all E2E tests."""
        logger.info("📊 EMA_V5 E2E tests starting")
        self._results = []
        self._passed = 0
        self._failed = 0

        self._test_signal_to_storage()
        self._test_signal_to_verification()
        self._test_signal_to_report()
        self._test_full_lifecycle()
        self._test_paper_trading()
        self._test_export_flow()

        return self._compile_report()

    def _test_signal_to_storage(self) -> None:
        """Test: Signal generation → Storage."""
        try:
            import tempfile
            import os

            from ..storage.serializer import EMAv5Serializer
            from ..storage.database import EMAv5Database
            from ..storage.history import EMAv5History

            db_path = os.path.join(tempfile.gettempdir(), "test_e2e_storage.db")
            if os.path.exists(db_path):
                os.remove(db_path)

            db = EMAv5Database(db_path)
            hist = EMAv5History(db=db)

            # Generate signal
            signal = {
                "symbol": "BTCUSDT", "side": "LONG", "entry": 100000, "sl": 99000,
                "take_profit_1": 101500, "timestamp": time.time(), "confidence": 0.92,
                "regime": "BUY_MODE", "strategy_version": "ema_v5",
                "ema_data": {"ema20": 100100, "ema50": 99800, "ema144": 99500, "ema200": 99000},
                "components": {"trend": "bullish", "regime": "strong", "candle": "engulfing", "volume": "confirmed"},
            }

            # Store
            uuid = hist.record_signal(signal)
            assert uuid is not None

            # Verify stored
            stored = db.get_signal(uuid)
            assert stored is not None
            assert stored["symbol"] == "BTCUSDT"

            os.remove(db_path)
            self._record("signal_to_storage", True, f"Signal stored with UUID: {uuid[:20]}...")
        except Exception as e:
            self._record("signal_to_storage", False, str(e))

    def _test_signal_to_verification(self) -> None:
        """Test: Signal → Verification → Verdict."""
        try:
            from ..verification.verifier import EMAv5Verifier

            v = EMAv5Verifier()
            signal = {
                "symbol": "ETHUSDT", "side": "SHORT", "entry": 3500, "sl": 3600,
                "take_profit_1": 3350, "take_profit_2": 3200, "take_profit_3": 3000,
                "confidence": 0.95, "regime": "SELL_MODE", "uuid": "e2e-001",
                "timestamp": time.time(), "strategy_version": "ema_v5",
            }
            ema = {"ema20": 3490, "ema50": 3510, "ema144": 3530, "ema200": 3550, "slope_ema20": -0.3, "slope_ema50": -0.2}
            regime = {"regime": "SELL_MODE", "reason": "strong downtrend"}
            trend = {"direction": "bearish", "trend_score": 88}
            pb = {"pullback_detected": True, "touch_level": "ema20", "bounce_confirmed": True}
            candle = {"pattern_found": True, "pattern_name": "bearish_engulfing", "candle_score": 92}
            vol = {"volume_ok": True, "volume_ratio": 1.8, "volume_score": 85}
            conf = {"confidence": 0.95, "passed": True, "breakdown": {"trend": 92}}

            verdict, diag = v.verify(signal, ema, regime, trend, pb, candle, vol, conf, state="SELL_MODE")
            assert verdict in ("PASS", "WARNING", "FAIL")
            assert len(diag.checks) == 12

            self._record("signal_to_verification", True, f"Verdict: {verdict}")
        except Exception as e:
            self._record("signal_to_verification", False, str(e))

    def _test_signal_to_report(self) -> None:
        """Test: Signal → Report generation."""
        try:
            from ..reports.report_formatter import ReportFormatter
            from ..verification.report import EMAv5VerificationReport
            from ..verification.verifier import EMAv5Verifier
            from ..verification.statistics import EMAv5Statistics
            from ..verification.quality import EMAv5Quality

            v = EMAv5Verifier()
            stats = EMAv5Statistics(v.get_diagnostics())
            q = EMAv5Quality()
            report = EMAv5VerificationReport(v, stats, q)

            full = report.full_report()
            assert "verification" in full
            assert "quality" in full
            assert "performance" in full
            assert "risk" in full
            assert "recommendations" in full

            formatter = ReportFormatter()
            text = formatter.to_text(full.get("verification", {}))
            assert "EMA_V5" in text

            self._record("signal_to_report", True, "Full report generated")
        except Exception as e:
            self._record("signal_to_report", False, str(e))

    def _test_full_lifecycle(self) -> None:
        """Test: Complete signal lifecycle."""
        try:
            import tempfile
            import os

            from ..storage.serializer import EMAv5Serializer
            from ..storage.database import EMAv5Database
            from ..storage.history import EMAv5History
            from ..verification.verifier import EMAv5Verifier

            db_path = os.path.join(tempfile.gettempdir(), "test_e2e_lifecycle.db")
            if os.path.exists(db_path):
                os.remove(db_path)

            db = EMAv5Database(db_path)
            hist = EMAv5History(db=db)
            v = EMAv5Verifier()

            # 1. Generate signal
            signal = {
                "symbol": "SOLUSDT", "side": "LONG", "entry": 150, "sl": 145,
                "take_profit_1": 157.5, "take_profit_2": 165, "take_profit_3": 175,
                "confidence": 0.91, "regime": "BUY_MODE", "uuid": "lifecycle-001",
                "timestamp": time.time(), "strategy_version": "ema_v5",
                "ema_data": {"ema20": 151, "ema50": 149, "ema144": 147, "ema200": 145},
                "components": {"trend": "bullish", "regime": "strong", "candle": "hammer", "volume": "surge"},
            }

            # 2. Verify
            ema = {"ema20": 151, "ema50": 149, "ema144": 147, "ema200": 145, "slope_ema20": 0.2, "slope_ema50": 0.15}
            regime = {"regime": "BUY_MODE", "reason": "uptrend"}
            trend = {"direction": "bullish", "trend_score": 82}
            pb = {"pullback_detected": True, "touch_level": "ema20", "bounce_confirmed": True}
            candle = {"pattern_found": True, "pattern_name": "hammer", "candle_score": 88}
            vol = {"volume_ok": True, "volume_ratio": 1.3, "volume_score": 75}
            conf = {"confidence": 0.91, "passed": True, "breakdown": {"trend": 85}}

            verdict, diag = v.verify(signal, ema, regime, trend, pb, candle, vol, conf, state="BUY_MODE")

            # 3. Store (if PASS or WARNING)
            if verdict in ("PASS", "WARNING"):
                uuid = hist.record_signal(signal)
                stored = db.get_signal(uuid)
                assert stored is not None

            os.remove(db_path)
            self._record("full_lifecycle", True, f"Lifecycle complete: verdict={verdict}")
        except Exception as e:
            self._record("full_lifecycle", False, str(e))

    def _test_paper_trading(self) -> None:
        """Test: Paper trading flow."""
        try:
            from ..execution.paper_trader import EMAv5PaperTrader

            pt = EMAv5PaperTrader()
            signal = {
                "symbol": "BTCUSDT", "side": "LONG", "entry": 100000, "sl": 99000,
                "take_profit_1": 101500, "take_profit_2": 103000, "take_profit_3": 105000,
                "confidence": 0.92, "regime": "BUY_MODE", "uuid": "paper-001",
            }

            result = pt.process_signal(signal)
            assert result["status"] == "filled"

            status = pt.get_status()
            assert status["open_positions"] >= 0

            self._record("paper_trading", True, f"Paper trade: {result['status']}")
        except Exception as e:
            self._record("paper_trading", False, str(e))

    def _test_export_flow(self) -> None:
        """Test: Export flow."""
        try:
            import tempfile
            import os

            from ..storage.database import EMAv5Database
            from ..storage.exporter import EMAv5Exporter

            db_path = os.path.join(tempfile.gettempdir(), "test_e2e_export.db")
            if os.path.exists(db_path):
                os.remove(db_path)

            db = EMAv5Database(db_path)
            db.store_signal({
                "uuid": "export-001", "timestamp": time.time(), "date": "2026-06-25",
                "time": "12:00:00", "exchange": "Binance", "symbol": "BTCUSDT",
                "side": "LONG", "entry": 100000, "stop_loss": 99000, "tp1": 101500,
                "confidence": 0.92, "regime": "BUY_MODE", "strategy_version": "ema_v5",
            })

            exporter = EMAv5Exporter(db=db)

            # CSV export
            csv_path = exporter.export_csv()
            assert csv_path and os.path.exists(csv_path)
            os.remove(csv_path)

            # JSON export
            json_path = exporter.export_json()
            assert json_path and os.path.exists(json_path)
            os.remove(json_path)

            os.remove(db_path)
            self._record("export_flow", True, "CSV and JSON export work")
        except Exception as e:
            self._record("export_flow", False, str(e))

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
            "test_type": "e2e",
            "total": self._passed + self._failed,
            "passed": self._passed,
            "failed": self._failed,
            "pass_rate": round(self._passed / max(self._passed + self._failed, 1) * 100, 1),
            "results": self._results,
        }
