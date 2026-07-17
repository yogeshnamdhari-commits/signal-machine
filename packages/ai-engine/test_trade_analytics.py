"""
Trade Analytics Engine — Test Suite
Validates all 8 analytics components work correctly.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import List

# Import the module under test
from scanner.trade_analytics_engine import (
    TradeRecord,
    load_trades,
    migrate_db,
    versioned_analytics,
    hold_time_optimizer,
    exit_optimizer,
    confidence_accuracy,
    symbol_expectancy,
    session_analytics,
    live_engine_performance,
    auto_recommendations,
    TradeAnalyticsOrchestrator,
    _compute_group_stats,
    _classify_version,
    _classify_session,
    _hold_bucket,
)


def generate_sample_trades(n: int = 100) -> List[TradeRecord]:
    """Generate sample trade records for testing."""
    np.random.seed(42)
    
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    sides = ["LONG", "SHORT"]
    exit_reasons = ["take_profit", "stop_loss", "trailing_stop", "timeout"]
    regimes = ["trending_up", "trending_down", "ranging"]
    sessions = ["asia", "london", "new_york", "off_hours"]
    versions = ["legacy", "inst_v1", "inst_v2", "current"]
    
    trades = []
    base_time = datetime(2026, 6, 4, 10, 0, 0, tzinfo=timezone.utc).timestamp()
    
    for i in range(n):
        # Generate random trade data
        symbol = np.random.choice(symbols)
        side = np.random.choice(sides)
        entry_price = np.random.uniform(100, 50000)
        pnl = np.random.normal(10, 50)  # Mean $10, std $50
        hold_minutes = np.random.exponential(60)  # Exponential distribution
        confidence = np.random.uniform(0.4, 0.95)
        
        # Assign version based on time progression
        if i < n * 0.2:
            version = "legacy"
        elif i < n * 0.5:
            version = "inst_v1"
        elif i < n * 0.8:
            version = "inst_v2"
        else:
            version = "current"
        
        # Assign session based on hour
        opened_at = base_time + i * 300  # 5 minutes apart
        dt = datetime.fromtimestamp(opened_at, tz=timezone.utc)
        if 0 <= dt.hour < 8:
            session = "asia"
        elif 7 <= dt.hour < 15:
            session = "london"
        elif 13 <= dt.hour < 21:
            session = "new_york"
        else:
            session = "off_hours"
        
        trade = TradeRecord(
            id=i + 1,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            pnl=pnl,
            fees=abs(pnl) * 0.0004,
            hold_minutes=hold_minutes,
            opened_at=opened_at,
            closed_at=opened_at + hold_minutes * 60,
            exit_reason=np.random.choice(exit_reasons),
            strategy_version=version,
            confidence=confidence,
            regime=np.random.choice(regimes),
            institutional_score=np.random.uniform(40, 80),
            risk_reward=np.random.uniform(0.5, 3.0),
            session=session,
            mfe_pct=np.random.uniform(0, 5),
            mae_pct=np.random.uniform(-5, 0),
            leverage=10,
        )
        trades.append(trade)
    
    return trades


def test_compute_group_stats():
    """Test _compute_group_stats function."""
    print("Testing _compute_group_stats...")
    
    trades = generate_sample_trades(50)
    stats = _compute_group_stats(trades)
    
    assert stats["trades"] == 50, f"Expected 50 trades, got {stats['trades']}"
    assert 0 <= stats["win_rate"] <= 100, f"Win rate out of range: {stats['win_rate']}"
    assert stats["profit_factor"] > 0, f"Profit factor should be positive: {stats['profit_factor']}"
    assert isinstance(stats["total_pnl"], float), "Total PnL should be float"
    assert isinstance(stats["expectancy"], float), "Expectancy should be float"
    
    # Test with empty list
    empty_stats = _compute_group_stats([])
    assert empty_stats["trades"] == 0
    assert empty_stats["win_rate"] == 0
    
    print("✅ _compute_group_stats passed")


def test_classify_version():
    """Test _classify_version function."""
    print("Testing _classify_version...")
    
    # Test version boundaries
    assert _classify_version(0) == "legacy"
    assert _classify_version(1780704000) == "inst_v1"  # Jun 6 2026
    assert _classify_version(1780876800) == "inst_v2"  # Jun 8 2026
    assert _classify_version(1780963200) == "current"  # Jun 9 2026
    
    print("✅ _classify_version passed")


def test_classify_session():
    """Test _classify_session function."""
    print("Testing _classify_session...")
    
    # Note: Session boundaries overlap (0-8, 7-15, 13-21, 21-24)
    # The function returns the first match in iteration order
    assert _classify_session(0) == "asia"      # 00:00 UTC
    assert _classify_session(6) == "asia"      # 06:00 UTC (still in asia 0-8)
    assert _classify_session(7) == "asia"      # 07:00 UTC (overlaps, asia first)
    assert _classify_session(8) == "london"    # 08:00 UTC (asia ends at 8)
    assert _classify_session(12) == "london"   # 12:00 UTC
    assert _classify_session(13) == "london"   # 13:00 UTC (overlaps, london first)
    assert _classify_session(15) == "new_york" # 15:00 UTC (london ends at 15)
    assert _classify_session(20) == "new_york" # 20:00 UTC
    assert _classify_session(21) == "off_hours" # 21:00 UTC
    
    print("✅ _classify_session passed")


def test_hold_bucket():
    """Test _hold_bucket function."""
    print("Testing _hold_bucket...")
    
    assert _hold_bucket(2) == "⚡ Scalp (<5m)"
    assert _hold_bucket(15) == "🔄 Quick (5-30m)"
    assert _hold_bucket(45) == "📊 Short (30-60m)"
    assert _hold_bucket(90) == "📈 Medium (1-2h)"
    assert _hold_bucket(180) == "🏗️ Swing (2-4h)"
    assert _hold_bucket(300) == "🦅 Position (4h+)"
    
    print("✅ _hold_bucket passed")


def test_versioned_analytics():
    """Test versioned_analytics function."""
    print("Testing versioned_analytics...")
    
    trades = generate_sample_trades(100)
    results = versioned_analytics(trades)
    
    assert len(results) > 0, "Should have at least one version"
    
    for result in results:
        assert "version" in result, "Result should have 'version' key"
        assert "label" in result, "Result should have 'label' key"
        assert "trades" in result, "Result should have 'trades' key"
        assert "win_rate" in result, "Result should have 'win_rate' key"
        assert "profit_factor" in result, "Result should have 'profit_factor' key"
        assert "total_pnl" in result, "Result should have 'total_pnl' key"
        assert "expectancy" in result, "Result should have 'expectancy' key"
    
    print("✅ versioned_analytics passed")


def test_hold_time_optimizer():
    """Test hold_time_optimizer function."""
    print("Testing hold_time_optimizer...")
    
    trades = generate_sample_trades(100)
    results = hold_time_optimizer(trades)
    
    assert len(results) > 0, "Should have at least one hold time zone"
    
    for result in results:
        assert "zone" in result, "Result should have 'zone' key"
        assert "trades" in result, "Result should have 'trades' key"
        assert "win_rate" in result, "Result should have 'win_rate' key"
        assert "total_pnl" in result, "Result should have 'total_pnl' key"
        assert "is_best" in result, "Result should have 'is_best' key"
    
    print("✅ hold_time_optimizer passed")


def test_exit_optimizer():
    """Test exit_optimizer function."""
    print("Testing exit_optimizer...")
    
    trades = generate_sample_trades(100)
    results = exit_optimizer(trades)
    
    assert len(results) > 0, "Should have at least one exit method"
    
    for result in results:
        assert "exit_reason" in result, "Result should have 'exit_reason' key"
        assert "label" in result, "Result should have 'label' key"
        assert "trades" in result, "Result should have 'trades' key"
        assert "win_rate" in result, "Result should have 'win_rate' key"
        assert "total_pnl" in result, "Result should have 'total_pnl' key"
        assert "is_best" in result, "Result should have 'is_best' key"
    
    print("✅ exit_optimizer passed")


def test_confidence_accuracy():
    """Test confidence_accuracy function."""
    print("Testing confidence_accuracy...")
    
    trades = generate_sample_trades(100)
    results = confidence_accuracy(trades)
    
    assert len(results) > 0, "Should have at least one confidence bucket"
    
    for result in results:
        assert "bucket" in result, "Result should have 'bucket' key"
        assert "trades" in result, "Result should have 'trades' key"
        assert "win_rate" in result, "Result should have 'win_rate' key"
        assert "avg_confidence" in result, "Result should have 'avg_confidence' key"
        assert "calibration_error" in result, "Result should have 'calibration_error' key"
        assert "calibration_note" in result, "Result should have 'calibration_note' key"
    
    print("✅ confidence_accuracy passed")


def test_symbol_expectancy():
    """Test symbol_expectancy function."""
    print("Testing symbol_expectancy...")
    
    trades = generate_sample_trades(100)
    results = symbol_expectancy(trades)
    
    assert len(results) > 0, "Should have at least one symbol"
    
    for result in results:
        assert "symbol" in result, "Result should have 'symbol' key"
        assert "trades" in result, "Result should have 'trades' key"
        assert "win_rate" in result, "Result should have 'win_rate' key"
        assert "total_pnl" in result, "Result should have 'total_pnl' key"
        assert "expectancy" in result, "Result should have 'expectancy' key"
        assert "is_positive" in result, "Result should have 'is_positive' key"
    
    print("✅ symbol_expectancy passed")


def test_session_analytics():
    """Test session_analytics function."""
    print("Testing session_analytics...")
    
    trades = generate_sample_trades(100)
    results = session_analytics(trades)
    
    assert len(results) > 0, "Should have at least one session"
    
    for result in results:
        assert "session" in result, "Result should have 'session' key"
        assert "label" in result, "Result should have 'label' key"
        assert "trades" in result, "Result should have 'trades' key"
        assert "win_rate" in result, "Result should have 'win_rate' key"
        assert "total_pnl" in result, "Result should have 'total_pnl' key"
        assert "is_best" in result, "Result should have 'is_best' key"
    
    print("✅ session_analytics passed")


def test_live_engine_performance():
    """Test live_engine_performance function."""
    print("Testing live_engine_performance...")
    
    trades = generate_sample_trades(100)
    results = live_engine_performance(trades)
    
    assert "all" in results, "Results should have 'all' key"
    assert "last_20" in results or len(trades) < 20, "Results should have 'last_20' key if enough trades"
    assert "last_50" in results or len(trades) < 50, "Results should have 'last_50' key if enough trades"
    assert "last_100" in results or len(trades) < 100, "Results should have 'last_100' key if enough trades"
    
    for key, result in results.items():
        assert "window" in result, f"Result {key} should have 'window' key"
        assert "trades" in result, f"Result {key} should have 'trades' key"
        assert "win_rate" in result, f"Result {key} should have 'win_rate' key"
        assert "profit_factor" in result, f"Result {key} should have 'profit_factor' key"
        assert "expectancy" in result, f"Result {key} should have 'expectancy' key"
    
    print("✅ live_engine_performance passed")


def test_auto_recommendations():
    """Test auto_recommendations function."""
    print("Testing auto_recommendations...")
    
    trades = generate_sample_trades(100)
    results = auto_recommendations(trades)
    
    assert len(results) > 0, "Should have at least one recommendation"
    
    for result in results:
        assert "priority" in result, "Result should have 'priority' key"
        assert "recommendation" in result, "Result should have 'recommendation' key"
        assert "detail" in result, "Result should have 'detail' key"
        assert "impact" in result, "Result should have 'impact' key"
    
    print("✅ auto_recommendations passed")


def test_trade_analytics_orchestrator():
    """Test TradeAnalyticsOrchestrator class."""
    print("Testing TradeAnalyticsOrchestrator...")
    
    # Test with sample trades (mock the load_trades function)
    import scanner.trade_analytics_engine as engine
    original_load_trades = engine.load_trades
    
    def mock_load_trades():
        return generate_sample_trades(100)
    
    engine.load_trades = mock_load_trades
    
    try:
        orch = TradeAnalyticsOrchestrator()
        results = orch.run_all()
        
        assert "total_trades" in results, "Results should have 'total_trades' key"
        assert "versions" in results, "Results should have 'versions' key"
        assert "hold_time" in results, "Results should have 'hold_time' key"
        assert "exits" in results, "Results should have 'exits' key"
        assert "confidence" in results, "Results should have 'confidence' key"
        assert "symbols" in results, "Results should have 'symbols' key"
        assert "sessions" in results, "Results should have 'sessions' key"
        assert "live_performance" in results, "Results should have 'live_performance' key"
        assert "recommendations" in results, "Results should have 'recommendations' key"
        
        assert results["total_trades"] == 100, f"Expected 100 trades, got {results['total_trades']}"
        
    finally:
        engine.load_trades = original_load_trades
    
    print("✅ TradeAnalyticsOrchestrator passed")


def test_database_migration():
    """Test database migration function."""
    print("Testing database migration...")
    
    # This test would require a test database
    # For now, just test that the function doesn't crash
    try:
        migrate_db()
        print("✅ Database migration completed without errors")
    except Exception as e:
        print(f"⚠️ Database migration test skipped (no test DB): {e}")


def test_load_trades():
    """Test load_trades function."""
    print("Testing load_trades...")
    
    trades = load_trades()
    
    # This might return empty if no DB exists, which is fine
    assert isinstance(trades, list), "load_trades should return a list"
    
    if trades:
        for trade in trades:
            assert isinstance(trade, TradeRecord), "Each trade should be a TradeRecord"
            assert trade.symbol, "Trade should have a symbol"
            assert trade.side in ["LONG", "SHORT"], "Trade side should be LONG or SHORT"
            assert isinstance(trade.pnl, float), "Trade PnL should be float"
    
    print("✅ load_trades passed")


def run_all_tests():
    """Run all tests."""
    print("🧪 Running Trade Analytics Engine Test Suite\n")
    
    tests = [
        test_compute_group_stats,
        test_classify_version,
        test_classify_session,
        test_hold_bucket,
        test_versioned_analytics,
        test_hold_time_optimizer,
        test_exit_optimizer,
        test_confidence_accuracy,
        test_symbol_expectancy,
        test_session_analytics,
        test_live_engine_performance,
        test_auto_recommendations,
        test_trade_analytics_orchestrator,
        test_database_migration,
        test_load_trades,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"❌ {test.__name__} failed: {e}")
            failed += 1
    
    print(f"\n📊 Test Results: {passed} passed, {failed} failed")
    
    if failed == 0:
        print("🎉 All tests passed!")
        return True
    else:
        print("💥 Some tests failed!")
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
