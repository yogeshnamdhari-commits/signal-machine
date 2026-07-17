"""
Phase 3 — Paper Trading Validation Test

Validates the paper trading system:
1. All imports resolve
2. All data classes instantiate correctly
3. SimulatedPositionManager works
4. SystemHealthMonitor works
5. ExecutionQualityAnalyzer works
6. PaperTradingEngine initializes
7. State persistence works
8. Report generation works
9. CSV/JSON export works
10. Chart generation works

NO live data — unit-level validation of all components.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Ensure ai-engine on path
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))


def test_imports():
    """Test all imports resolve correctly."""
    print("1. Testing imports...")
    try:
        from backtesting.paper_trading_validator import (
            PaperTradingEngine, PaperSignal, PaperTrade,
            ExecutionQuality, DailyReport, WeeklyReport,
            SystemHealth, PaperTradingSummary,
            SimulatedPositionManager, SystemHealthMonitor,
            ExecutionQualityAnalyzer,
            STARTING_EQUITY, RISK_PER_TRADE_PCT, MAX_OPEN_POSITIONS,
        )
        print("   ✅ All imports successful")
        return True
    except Exception as e:
        print(f"   ❌ Import failed: {e}")
        return False


def test_data_classes():
    """Test all data classes instantiate and serialize."""
    print("2. Testing data classes...")
    try:
        from backtesting.paper_trading_validator import (
            PaperSignal, PaperTrade, ExecutionQuality,
            DailyReport, WeeklyReport, SystemHealth, PaperTradingSummary,
        )

        # PaperSignal
        sig = PaperSignal(
            id="SIG-TEST-001", timestamp=time.time(), symbol="BTCUSDT",
            side="LONG", entry_price=65000, stop_loss=64000, take_profit=67000,
            confidence=0.75, institutional_score=82, market_regime="trending_up",
        )
        d = sig.to_dict()
        assert d["symbol"] == "BTCUSDT"
        assert d["side"] == "LONG"

        # PaperTrade
        trade = PaperTrade(
            id="PT-001", signal_id="SIG-TEST-001", symbol="BTCUSDT",
            side="LONG", entry_time=time.time(), entry_price=65000,
            stop_loss=64000, take_profit=67000,
        )
        d = trade.to_dict()
        assert d["symbol"] == "BTCUSDT"

        # ExecutionQuality
        eq = ExecutionQuality(signal_count=10, trade_count=5, win_rate=0.6)
        d = eq.to_dict()
        assert d["win_rate"] == 0.6

        # DailyReport
        dr = DailyReport(date="2026-06-01", signals_generated=50)
        d = dr.to_dict()
        assert d["date"] == "2026-06-01"

        # WeeklyReport
        wr = WeeklyReport(week_start="2026-05-26", week_end="2026-06-01")
        d = wr.to_dict()

        # SystemHealth
        sh = SystemHealth(api_errors=0, uptime_pct=99.9)
        d = sh.to_dict()
        assert d["uptime_pct"] == 99.9

        # PaperTradingSummary
        pts = PaperTradingSummary(overall_result="PASS")
        d = pts.to_dict()
        assert d["overall_result"] == "PASS"

        print("   ✅ All data classes OK")
        return True
    except Exception as e:
        print(f"   ❌ Data class test failed: {e}")
        return False


def test_position_manager():
    """Test SimulatedPositionManager."""
    print("3. Testing SimulatedPositionManager...")
    try:
        from backtesting.paper_trading_validator import (
            SimulatedPositionManager, PaperSignal, STARTING_EQUITY,
        )

        mgr = SimulatedPositionManager()

        # Create a signal
        sig = PaperSignal(
            id="SIG-PM-001", timestamp=time.time(), symbol="ETHUSDT",
            side="LONG", entry_price=3500, stop_loss=3400, take_profit=3700,
            confidence=0.8, institutional_score=75, market_regime="trending_up",
        )

        # Open position
        trade = mgr.open_position(sig, 3500, 0.1, 10)
        assert trade.status == "open"
        assert trade.symbol == "ETHUSDT"
        assert trade.quantity == 0.1
        assert trade.leverage == 10
        assert trade.entry_price > 3500  # Slippage applied
        assert mgr.position_count() == 1
        assert mgr.symbol_has_position("ETHUSDT")

        # Check exit — no exit (price between SL and TP)
        should_close, reason = mgr.check_exit(trade, 3550)
        assert not should_close

        # Check exit — stop loss hit
        should_close, reason = mgr.check_exit(trade, 3390)
        assert should_close
        assert reason == "stop_loss"

        # Close position
        closed = mgr.close_position(trade.id, 3700, "take_profit")
        assert closed is not None
        assert closed.status == "closed"
        assert closed.exit_price < 3700  # Slippage applied
        assert closed.gross_pnl != 0
        assert closed.fees > 0
        assert mgr.position_count() == 0

        # Test SHORT position
        sig2 = PaperSignal(
            id="SIG-PM-002", timestamp=time.time(), symbol="SOLUSDT",
            side="SHORT", entry_price=150, stop_loss=160, take_profit=130,
            confidence=0.7, institutional_score=68, market_regime="trending_down",
        )
        trade2 = mgr.open_position(sig2, 150, 1.0, 10)
        assert trade2.side == "SHORT"

        # SHORT stop loss
        should_close, reason = mgr.check_exit(trade2, 161)
        assert should_close
        assert reason == "stop_loss"

        # SHORT take profit
        trade3 = mgr.open_position(
            PaperSignal(
                id="SIG-PM-003", timestamp=time.time(), symbol="BNBUSDT",
                side="SHORT", entry_price=600, stop_loss=620, take_profit=560,
                confidence=0.75, institutional_score=70, market_regime="ranging",
            ),
            600, 0.05, 10
        )
        should_close, reason = mgr.check_exit(trade3, 555)
        assert should_close
        assert reason == "take_profit"

        print("   ✅ SimulatedPositionManager OK")
        return True
    except Exception as e:
        print(f"   ❌ Position manager test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_health_monitor():
    """Test SystemHealthMonitor."""
    print("4. Testing SystemHealthMonitor...")
    try:
        from backtesting.paper_trading_validator import SystemHealthMonitor

        monitor = SystemHealthMonitor()

        # Record some events
        monitor.record_api_error("test error 1")
        monitor.record_api_error("test error 2")
        monitor.record_reconnect()
        monitor.record_ws_disconnect()
        time.sleep(0.01)
        monitor.record_ws_reconnect()
        monitor.record_message(1.5)
        monitor.record_message(2.0)
        monitor.record_latency(50.0)
        monitor.record_latency(60.0)
        monitor.record_memory(256.0)
        monitor.record_dropped_message()

        snap = monitor.get_snapshot()
        assert snap.api_errors == 2
        assert snap.reconnect_events == 1
        assert snap.ws_disconnects == 1
        assert snap.total_messages == 2
        assert snap.dropped_messages == 1
        assert snap.memory_mb == 256.0
        assert snap.uptime_pct > 0
        assert snap.uptime_pct <= 100

        print("   ✅ SystemHealthMonitor OK")
        return True
    except Exception as e:
        print(f"   ❌ Health monitor test failed: {e}")
        return False


def test_execution_analyzer():
    """Test ExecutionQualityAnalyzer."""
    print("5. Testing ExecutionQualityAnalyzer...")
    try:
        from backtesting.paper_trading_validator import (
            ExecutionQualityAnalyzer, PaperTrade, STARTING_EQUITY,
        )

        analyzer = ExecutionQualityAnalyzer()

        # Create test trades
        trades = []
        for i in range(20):
            pnl = 50.0 if i % 3 != 0 else -30.0  # 67% win rate
            trades.append(PaperTrade(
                id=f"PT-{i}", signal_id=f"SIG-{i}", symbol="BTCUSDT",
                side="LONG", entry_time=time.time() - (20 - i) * 3600,
                exit_time=time.time() - (20 - i) * 3600 + 1800,
                duration_min=30, entry_price=65000, exit_price=65050,
                quantity=0.01, net_pnl=pnl, gross_pnl=pnl + 5,
                return_pct=pnl / 650, fees=5, total_slippage=0.001,
                entry_slippage=0.0005, exit_slippage=0.0005,
                exit_reason="take_profit" if pnl > 0 else "stop_loss",
                stop_loss=64000, take_profit=67000,
                confidence=0.7, institutional_score=70, market_regime="trending",
            ))

        quality = analyzer.calculate(trades, signal_count=50)
        assert quality.trade_count == 20
        assert quality.signal_count == 50
        assert quality.win_count == 13  # i%3!=0 for 13 out of 20
        assert quality.win_rate > 0.6
        assert quality.profit_factor > 1.0
        assert quality.longest_win_streak > 0
        assert quality.avg_hold_time_min > 0
        assert quality.total_net_pnl > 0  # Should be profitable

        print(f"   Trades: {quality.trade_count} | WR: {quality.win_rate:.1%} | PF: {quality.profit_factor:.2f}")
        print(f"   Net PnL: ${quality.total_net_pnl:.2f} | Max DD: {quality.max_drawdown_pct:.2f}%")
        print("   ✅ ExecutionQualityAnalyzer OK")
        return True
    except Exception as e:
        print(f"   ❌ Execution analyzer test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_state_persistence():
    """Test state save/load cycle."""
    print("6. Testing state persistence...")
    try:
        from backtesting.paper_trading_validator import (
            SimulatedPositionManager, PaperSignal, PaperTrade,
            SystemHealthMonitor, DATA_DIR, STATE_FILE,
        )
        import json

        DATA_DIR.mkdir(parents=True, exist_ok=True)

        # Create state
        mgr = SimulatedPositionManager()
        sig = PaperSignal(
            id="SIG-STATE-001", timestamp=time.time(), symbol="BTCUSDT",
            side="LONG", entry_price=65000, stop_loss=64000, take_profit=67000,
            confidence=0.8, institutional_score=80, market_regime="trending_up",
        )
        trade = mgr.open_position(sig, 65000, 0.01, 10)

        # Save state
        state = {
            "version": 2,
            "timestamp": time.time(),
            "current_equity": 10150.0,
            "peak_equity": 10200.0,
            "starting_equity": 10000.0,
            "open_positions": [
                {
                    "id": trade.id, "signal_id": trade.signal_id,
                    "symbol": trade.symbol, "side": trade.side,
                    "entry_time": trade.entry_time,
                    "entry_price": trade.entry_price,
                    "expected_entry": trade.expected_entry,
                    "quantity": trade.quantity, "leverage": trade.leverage,
                    "stop_loss": trade.stop_loss, "take_profit": trade.take_profit,
                    "fees": trade.fees, "confidence": trade.confidence,
                    "institutional_score": trade.institutional_score,
                    "market_regime": trade.market_regime,
                }
            ],
            "equity_history": [
                {"timestamp": time.time(), "equity": 10150, "pnl": 150, "drawdown": 0.5}
            ],
        }

        with open(STATE_FILE, "w") as f:
            json.dump(state, f)

        # Load state
        with open(STATE_FILE, "r") as f:
            loaded = json.load(f)

        assert loaded["current_equity"] == 10150.0
        assert len(loaded["open_positions"]) == 1
        assert loaded["open_positions"][0]["symbol"] == "BTCUSDT"

        # Cleanup
        STATE_FILE.unlink(missing_ok=True)

        print("   ✅ State persistence OK")
        return True
    except Exception as e:
        print(f"   ❌ State persistence test failed: {e}")
        return False


def test_csv_exports():
    """Test CSV export functions."""
    print("7. Testing CSV exports...")
    try:
        from backtesting.paper_trading_validator import (
            PaperTradingEngine, PaperTrade, PaperSignal, DailyReport,
            TRADES_CSV, SIGNALS_CSV, DAILY_CSV, DATA_DIR,
        )
        import csv

        DATA_DIR.mkdir(parents=True, exist_ok=True)

        engine = PaperTradingEngine()

        # Add test trades
        engine.closed_trades = [
            PaperTrade(
                id=f"PT-CSV-{i}", signal_id=f"SIG-{i}", symbol="BTCUSDT",
                side="LONG", entry_time=time.time() - 3600 + i * 60,
                exit_time=time.time() - 1800 + i * 60,
                duration_min=30, entry_price=65000, exit_price=65100,
                quantity=0.01, net_pnl=10.0 * (1 if i % 2 == 0 else -0.5),
                gross_pnl=12.0, return_pct=0.15, fees=2.0,
                entry_slippage=1.3, exit_slippage=1.3, total_slippage=2.6,
                drawdown=0.5, exit_reason="take_profit",
                stop_loss=64000, take_profit=67000,
                confidence=0.75, institutional_score=72,
                market_regime="trending_up", status="closed",
            )
            for i in range(5)
        ]

        # Add test signals
        engine.signals = [
            PaperSignal(
                id=f"SIG-CSV-{i}", timestamp=time.time() - 3600 + i * 60,
                symbol="BTCUSDT", side="LONG", entry_price=65000,
                stop_loss=64000, take_profit=67000, confidence=0.75,
                institutional_score=72, market_regime="trending_up",
                status="filled" if i < 3 else "rejected",
            )
            for i in range(5)
        ]

        # Add daily report
        engine.daily_reports = [
            DailyReport(
                date="2026-06-01", signals_generated=50, trades_opened=5,
                trades_closed=3, win_rate=0.67, profit_factor=1.8,
                net_pnl=25.0, drawdown_pct=0.5, open_positions=2,
                equity=10025.0,
            )
        ]

        # Export
        engine._export_trades_csv()
        engine._export_signals_csv()
        engine._export_daily_csv()

        # Verify
        assert TRADES_CSV.exists(), f"Missing: {TRADES_CSV}"
        assert SIGNALS_CSV.exists(), f"Missing: {SIGNALS_CSV}"
        assert DAILY_CSV.exists(), f"Missing: {DAILY_CSV}"

        # Check content
        with open(TRADES_CSV) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) == 5
            assert rows[0]["symbol"] == "BTCUSDT"

        with open(SIGNALS_CSV) as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            assert len(rows) == 5

        print(f"   Exported: {len(engine.closed_trades)} trades, {len(engine.signals)} signals")
        print("   ✅ CSV exports OK")
        return True
    except Exception as e:
        print(f"   ❌ CSV export test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_json_export():
    """Test JSON summary export."""
    print("8. Testing JSON summary export...")
    try:
        from backtesting.paper_trading_validator import (
            PaperTradingEngine, PaperTradingSummary, SUMMARY_JSON, DATA_DIR,
        )

        DATA_DIR.mkdir(parents=True, exist_ok=True)

        summary = PaperTradingSummary(
            start_time=time.time() - 86400 * 14,
            end_time=time.time(),
            duration_days=14.0,
            total_signals=500,
            total_trades=45,
            win_rate=0.56,
            profit_factor=1.45,
            net_profit=350.0,
            max_drawdown_pct=5.2,
            avg_slippage_bps=2.1,
            api_errors=3,
            reconnects=1,
            uptime_pct=99.8,
            performance_drift={
                "pf_drift_pct": 9.4,
                "wr_drift_pct": 8.2,
                "dd_drift_pct": 40.6,
                "interpretation": "Excellent",
            },
            criteria={
                "pf_gt_1_30": True,
                "wr_gt_48pct": True,
                "dd_lt_10pct": True,
                "positive_profit": True,
                "uptime_gt_99pct": True,
                "no_critical_failures": True,
            },
            overall_result="PASS",
            recommendation="READY FOR SMALL CAPITAL",
        )

        engine = PaperTradingEngine()
        engine._export_summary_json(summary)

        assert SUMMARY_JSON.exists()
        with open(SUMMARY_JSON) as f:
            loaded = json.load(f)
        assert loaded["overall_result"] == "PASS"
        assert loaded["profit_factor"] == 1.45

        print("   ✅ JSON export OK")
        return True
    except Exception as e:
        print(f"   ❌ JSON export test failed: {e}")
        return False


def test_chart_generation():
    """Test chart generation with synthetic data."""
    print("9. Testing chart generation...")
    try:
        from backtesting.paper_trading_validator import (
            PaperTradingEngine, PaperTrade, FIGURES_DIR,
        )
        import random

        FIGURES_DIR.mkdir(parents=True, exist_ok=True)

        engine = PaperTradingEngine()
        engine._start_time = time.time() - 86400 * 14

        # Generate synthetic trades
        random.seed(42)
        base_price = 65000
        for i in range(50):
            pnl = random.gauss(20, 40)
            engine.closed_trades.append(PaperTrade(
                id=f"PT-CHART-{i}", signal_id=f"SIG-{i}",
                symbol=random.choice(["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]),
                side=random.choice(["LONG", "SHORT"]),
                entry_time=time.time() - 86400 * 14 + i * 86400 * 14 / 50,
                exit_time=time.time() - 86400 * 14 + i * 86400 * 14 / 50 + 1800,
                duration_min=30, entry_price=base_price, exit_price=base_price + pnl,
                quantity=0.01, net_pnl=pnl, gross_pnl=pnl + 5, return_pct=pnl / base_price,
                fees=5, entry_slippage=1.3, exit_slippage=1.3, total_slippage=2.6,
                drawdown=max(0, -pnl / 100),
                exit_reason="take_profit" if pnl > 0 else "stop_loss",
                stop_loss=base_price - 1000, take_profit=base_price + 2000,
                confidence=0.7, institutional_score=70,
                market_regime=random.choice(["trending_up", "trending_down", "ranging", "breakout"]),
            ))

        # Build equity history
        equity = 10000
        for t in sorted(engine.closed_trades, key=lambda x: x.entry_time):
            equity += t.net_pnl
            engine.equity_history.append({
                "timestamp": t.exit_time,
                "equity": equity,
                "pnl": equity - 10000,
                "drawdown": max(0, (10200 - equity) / 10200 * 100),
            })

        engine._generate_charts()

        # Check all 7 charts exist
        expected = [
            "paper_equity_curve.png",
            "paper_drawdown_curve.png",
            "paper_pnl_distribution.png",
            "paper_trade_distribution.png",
            "paper_slippage_distribution.png",
            "paper_symbol_performance.png",
            "paper_regime_performance.png",
        ]
        for name in expected:
            path = FIGURES_DIR / name
            assert path.exists(), f"Missing chart: {name}"
            size = path.stat().st_size
            assert size > 1000, f"Chart too small: {name} ({size} bytes)"

        print(f"   Generated {len(expected)} charts in {FIGURES_DIR}")
        print("   ✅ Chart generation OK")
        return True
    except Exception as e:
        print(f"   ❌ Chart generation test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_engine_init():
    """Test PaperTradingEngine initializes correctly."""
    print("10. Testing PaperTradingEngine initialization...")
    try:
        from backtesting.paper_trading_validator import PaperTradingEngine

        engine = PaperTradingEngine()

        assert engine.starting_equity == 10000.0
        assert engine.current_equity == 10000.0
        assert engine.risk_per_trade_pct == 1.0
        assert engine.max_open_positions == 5
        assert engine.max_portfolio_risk_pct == 5.0
        assert engine.leverage == 10
        assert engine.is_running is False
        assert engine.position_mgr is not None
        assert engine.health_monitor is not None
        assert engine.execution_analyzer is not None
        assert len(engine.signals) == 0
        assert len(engine.closed_trades) == 0

        # Verify all engines are instantiated
        assert engine.ws is not None
        assert engine.orderflow is not None
        assert engine.institutional is not None
        assert engine.scorer is not None
        assert engine.risk_engine is not None
        assert engine.position_sizer is not None
        assert engine.entry_confirmer is not None

        print("   ✅ PaperTradingEngine initialization OK")
        return True
    except Exception as e:
        print(f"   ❌ Engine init test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all validation tests."""
    print("=" * 60)
    print("  PHASE 3 — PAPER TRADING VALIDATION TEST")
    print("=" * 60)
    print()

    results = []
    results.append(("Imports", test_imports()))
    results.append(("Data Classes", test_data_classes()))
    results.append(("Position Manager", test_position_manager()))
    results.append(("Health Monitor", test_health_monitor()))
    results.append(("Execution Analyzer", test_execution_analyzer()))
    results.append(("State Persistence", test_state_persistence()))
    results.append(("CSV Exports", test_csv_exports()))
    results.append(("JSON Export", test_json_export()))
    results.append(("Chart Generation", test_chart_generation()))
    results.append(("Engine Init", test_engine_init()))

    print()
    print("=" * 60)
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"  RESULTS: {passed}/{total} PASSED")
    print("=" * 60)
    for name, ok in results:
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"  {status}  {name}")
    print("=" * 60)

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
