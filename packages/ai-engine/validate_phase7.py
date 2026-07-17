"""
Phase 7 — Final Validation: Comprehensive Integration & Optimization Suite
Tasks 41-48: Integration testing, import validation, runtime validation,
performance/memory/async optimization, error auto-fix, production cleanup.
"""
from __future__ import annotations

import asyncio
import gc
import importlib
import inspect
import json
import os
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Ensure we're in the right directory
BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))


# ═══════════════════════════════════════════════════════════════════
# Task 41 + 42: Full Integration Testing + Import Validation
# ═══════════════════════════════════════════════════════════════════

# All modules that should be importable
CORE_MODULES = [
    "config",
    "config.settings",
    "database",
    "database.db",
    "exchanges",
    "exchanges.binance_ws",
    "scanner",
    "scanner.orderflow",
    "scanner.cumulative_delta",
    "scanner.dom_analytics",
    "scanner.regime",
    "scanner.ai_scorer",
    "scanner.ranking",
    "scanner.symbol_scanner",
    "scanner.funding_rate",
    "scanner.open_interest",
    "scanner.exchange_flow",
    "scanner.liquidation",
    "scanner.smart_money",
    "scanner.sweep_detector",
    "scanner.absorption_detector",
    "scanner.spoofing_iceberg",
    "scanner.liquidity_map",
    "scanner.institutional",
    "scanner.fake_breakout_filter",
    "scanner.entry_confirmation",
    "scanner.position_sizing",
    "execution",
    "execution.risk_engine",
    "alerts",
    "alerts.telegram",
    "infrastructure",
    "infrastructure.logging",
    "core",
    "core.engine",
    "backtesting",
    "backtesting.historical_data",
    "backtesting.backtester",
    "backtesting.walk_forward",
    "backtesting.monte_carlo",
    "backtesting.optimizer",
    "backtesting.analytics",
    "dashboard",
    "dashboard.heatmaps",
    "dashboard.telegram_engine",
    "dashboard.alert_system",
    "dashboard.live_metrics",
    "dashboard.trade_analytics_panel",
]

# Modules that need Streamlit runtime (skip in headless)
SKIP_MODULES = {
    "dashboard.app",
}


def test_imports() -> Tuple[int, int, List[str]]:
    """Test all module imports. Returns (passed, failed, errors)."""
    passed = 0
    failed = 0
    errors = []

    for module_name in CORE_MODULES:
        if module_name in SKIP_MODULES:
            passed += 1
            continue
        try:
            importlib.import_module(module_name)
            passed += 1
        except Exception as e:
            failed += 1
            errors.append(f"  ❌ {module_name}: {e}")

    return passed, failed, errors


# ═══════════════════════════════════════════════════════════════════
# Task 43: Runtime Validation
# ═══════════════════════════════════════════════════════════════════

async def test_runtime_backtesting() -> bool:
    """Validate backtesting engine runs end-to-end."""
    from backtesting.backtester import BacktestEngine, BacktestConfig
    import numpy as np
    import pandas as pd
    from datetime import datetime, timedelta

    config = BacktestConfig(initial_capital=10000, leverage=10, risk_per_trade_pct=0.02)
    engine = BacktestEngine(config)
    await engine.initialize()

    # Generate test data
    np.random.seed(42)
    n = 500
    dates = [datetime.now() - timedelta(minutes=5 * (n - i)) for i in range(n)]
    prices = 50000 * np.exp(np.cumsum(np.random.normal(0.0001, 0.02, n)))
    df = pd.DataFrame({
        "open_time": dates,
        "open": prices * (1 + np.random.uniform(-0.005, 0.005, n)),
        "high": prices * (1 + np.random.uniform(0, 0.02, n)),
        "low": prices * (1 - np.random.uniform(0, 0.02, n)),
        "close": prices,
        "volume": np.random.uniform(100, 10000, n),
        "trades": np.random.randint(100, 1000, n),
    })
    df["high"] = df[["open", "high", "close"]].max(axis=1) * 1.001
    df["low"] = df[["open", "low", "close"]].min(axis=1) * 0.999

    def strategy(data, i):
        if i < 50:
            return None
        sma20 = data["close"].iloc[i-20:i].mean()
        sma50 = data["close"].iloc[i-50:i].mean()
        price = data["close"].iloc[i]
        atr = data["high"].iloc[i] - data["low"].iloc[i]
        if sma20 > sma50 and price > sma20:
            return {"side": "LONG", "confidence": 0.7, "stop_loss": price - 2*atr, "take_profit": price + 3*atr}
        return None

    result = await engine.run("BTCUSDT", df, strategy)
    assert result.total_trades >= 0
    assert result.initial_capital == 10000
    return True


async def test_runtime_monte_carlo() -> bool:
    """Validate Monte Carlo engine."""
    from backtesting.monte_carlo import MonteCarloEngine, MonteCarloConfig
    import numpy as np

    config = MonteCarloConfig(n_simulations=500, random_seed=42)
    engine = MonteCarloEngine(config)
    await engine.initialize()

    pnls = np.random.normal(50, 200, 50).tolist()
    result = await engine.bootstrap_trades(pnls)
    assert result.n_simulations == 500
    assert 0 <= result.probability_of_profit <= 1
    return True


async def test_runtime_analytics() -> bool:
    """Validate Performance Analytics."""
    from backtesting.analytics import PerformanceAnalyticsEngine
    from datetime import datetime, timedelta

    engine = PerformanceAnalyticsEngine(initial_capital=10000)
    await engine.initialize()

    trades = []
    base = datetime.now() - timedelta(days=30)
    for i in range(30):
        trades.append({
            "side": "LONG" if i % 3 != 0 else "SHORT",
            "entry_price": 50000 + i * 10,
            "exit_price": 50000 + i * 10 + (200 if i % 3 != 0 else -100),
            "size": 0.1,
            "pnl": 200 if i % 3 != 0 else -100,
            "fees": 5,
            "slippage": 2,
            "entry_time": base + timedelta(hours=i * 12),
            "exit_time": base + timedelta(hours=i * 12 + 4),
            "exit_reason": "take_profit" if i % 3 != 0 else "stop_loss",
            "hold_time_minutes": 240,
        })

    report = await engine.analyze("BTCUSDT", trades)
    assert report.trade_analytics.total_trades == 30
    assert 0 <= report.overall_score <= 100
    return True


async def test_runtime_telegram() -> bool:
    """Validate Telegram Alert Engine."""
    from dashboard.telegram_engine import TelegramAlertEngine, AlertConfig

    config = AlertConfig(enabled=True, bot_token="", chat_id="")
    engine = TelegramAlertEngine(config)
    await engine.initialize()

    result = await engine.send_signal_alert({
        "id": 1, "type": "LONG", "symbol": "BTCUSDT",
        "entry_price": 68500, "stop_loss": 67800, "take_profit": 70200,
        "confidence": 0.85, "regime": "trending_up",
        "risk_adjusted": {"quantity": 0.05, "position_value": 3425, "margin_required": 342.5},
    })
    assert result
    stats = engine.get_stats()
    assert stats["total"] >= 1
    return True


# ═══════════════════════════════════════════════════════════════════
# Task 44 + 45: Performance + Memory Optimization Checks
# ═══════════════════════════════════════════════════════════════════

def check_memory_usage() -> Dict[str, Any]:
    """Check memory usage of key components."""
    tracemalloc.start()

    # Import all modules
    from backtesting.backtester import BacktestEngine
    from backtesting.monte_carlo import MonteCarloEngine
    from backtesting.analytics import PerformanceAnalyticsEngine
    from backtesting.optimizer import AIAdaptiveOptimizer
    from backtesting.walk_forward import WalkForwardEngine
    from backtesting.historical_data import HistoricalDataEngine

    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    return {
        "current_mb": current / 1024 / 1024,
        "peak_mb": peak / 1024 / 1024,
        "acceptable": peak / 1024 / 1024 < 100,  # < 100MB for imports
    }


def check_async_patterns() -> List[str]:
    """Check for common async anti-patterns."""
    issues = []
    for py_file in BASE.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        try:
            content = py_file.read_text()
        except Exception:
            continue

        # Check for blocking calls in async functions
        lines = content.split("\n")
        in_async = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("async def"):
                in_async = True
            elif stripped.startswith("def ") and not stripped.startswith("def _"):
                in_async = False

            if in_async:
                if "time.sleep(" in stripped:
                    issues.append(f"{py_file.name}:{i+1}: time.sleep() in async function")
                if "requests." in stripped:
                    issues.append(f"{py_file.name}:{i+1}: requests library in async function (use aiohttp)")

    return issues


def check_large_data_structures() -> List[str]:
    """Check for potential memory issues in data structures."""
    warnings = []
    for py_file in BASE.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        try:
            content = py_file.read_text()
        except Exception:
            continue

        # Check for unbounded lists/dicts
        if "List[Dict]" in content and "max" not in content.lower():
            warnings.append(f"{py_file.name}: Unbounded List[Dict] without size limit")

        # Check for large buffer sizes
        for i, line in enumerate(content.split("\n")):
            if "> 10_000" in line or "> 10000" in line:
                warnings.append(f"{py_file.name}:{i+1}: Large buffer limit detected")

    return warnings


# ═══════════════════════════════════════════════════════════════════
# Task 47: Error Auto-Fix Pass
# ═══════════════════════════════════════════════════════════════════

def check_syntax_errors() -> List[str]:
    """Check all Python files for syntax errors."""
    errors = []
    for py_file in BASE.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        try:
            compile(py_file.read_text(), str(py_file), "exec")
        except SyntaxError as e:
            errors.append(f"{py_file.name}:{e.lineno}: {e.msg}")
    return errors


def check_type_hints() -> Dict[str, int]:
    """Check type hint coverage."""
    total_funcs = 0
    hinted_funcs = 0

    for py_file in BASE.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        try:
            tree = ast_parse(py_file.read_text())
        except Exception:
            continue

        for node in tree:
            if hasattr(node, "name") and hasattr(node, "args"):
                total_funcs += 1
                if hasattr(node, "returns") and node.returns is not None:
                    hinted_funcs += 1

    return {
        "total_functions": total_funcs,
        "hinted_functions": hinted_funcs,
        "coverage_pct": (hinted_funcs / total_funcs * 100) if total_funcs > 0 else 0,
    }


def ast_parse(source: str):
    """Simple AST parse for function detection."""
    import ast
    try:
        tree = ast.parse(source)
        return [node for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    except SyntaxError:
        return []


# ═══════════════════════════════════════════════════════════════════
# Main Runner
# ═══════════════════════════════════════════════════════════════════

async def run_validation():
    """Run the complete Phase 7 validation suite."""
    print("=" * 70)
    print("Phase 7 — Final Validation Suite")
    print("=" * 70)
    results = {}

    # ── Task 41 + 42: Import Validation ──────────────────────────
    print("\n📦 Task 41+42: Import Validation")
    print("-" * 40)
    passed, failed, errors = test_imports()
    results["imports"] = {"passed": passed, "failed": failed}
    print(f"  ✅ {passed}/{passed + failed} modules imported successfully")
    if errors:
        for e in errors:
            print(e)

    # ── Task 43: Runtime Validation ──────────────────────────────
    print("\n🚀 Task 43: Runtime Validation")
    print("-" * 40)
    runtime_tests = [
        ("Backtesting Engine", test_runtime_backtesting),
        ("Monte Carlo Engine", test_runtime_monte_carlo),
        ("Performance Analytics", test_runtime_analytics),
        ("Telegram Alert Engine", test_runtime_telegram),
    ]

    runtime_passed = 0
    for name, test_func in runtime_tests:
        try:
            await test_func()
            print(f"  ✅ {name}")
            runtime_passed += 1
        except Exception as e:
            print(f"  ❌ {name}: {e}")
    results["runtime"] = {"passed": runtime_passed, "total": len(runtime_tests)}

    # ── Task 44: Performance Check ───────────────────────────────
    print("\n⚡ Task 44: Performance Check")
    print("-" * 40)
    start = time.time()
    memory = check_memory_usage()
    elapsed = time.time() - start
    results["memory"] = memory
    print(f"  Memory: {memory['current_mb']:.1f}MB current, {memory['peak_mb']:.1f}MB peak")
    print(f"  {'✅' if memory['acceptable'] else '⚠️'} Memory {'OK' if memory['acceptable'] else 'HIGH'}")
    print(f"  Import time: {elapsed:.2f}s")

    # ── Task 45: Memory Optimization Check ───────────────────────
    print("\n🧠 Task 45: Memory Optimization Check")
    print("-" * 40)
    warnings = check_large_data_structures()
    if warnings:
        for w in warnings[:5]:
            print(f"  ⚠️ {w}")
    else:
        print("  ✅ No memory warnings")

    # ── Task 46: Async Optimization Check ────────────────────────
    print("\n🔄 Task 46: Async Optimization Check")
    print("-" * 40)
    async_issues = check_async_patterns()
    if async_issues:
        for issue in async_issues[:5]:
            print(f"  ⚠️ {issue}")
    else:
        print("  ✅ No async anti-patterns detected")
    results["async_issues"] = len(async_issues)

    # ── Task 47: Error Auto-Fix Pass ─────────────────────────────
    print("\n🔧 Task 47: Syntax Error Check")
    print("-" * 40)
    syntax_errors = check_syntax_errors()
    if syntax_errors:
        for err in syntax_errors:
            print(f"  ❌ {err}")
    else:
        print("  ✅ No syntax errors found")
    results["syntax_errors"] = len(syntax_errors)

    # ── Summary ──────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("📊 VALIDATION SUMMARY")
    print("=" * 70)

    total_pass = (
        results["imports"]["failed"] == 0 and
        results["runtime"]["passed"] == results["runtime"]["total"] and
        results["memory"]["acceptable"] and
        len(syntax_errors) == 0
    )

    print(f"  Imports:    {results['imports']['passed']}/{results['imports']['passed'] + results['imports']['failed']}")
    print(f"  Runtime:    {results['runtime']['passed']}/{results['runtime']['total']}")
    print(f"  Memory:     {results['peak_mb']:.1f}MB" if 'peak_mb' in results else f"  Memory:     {results['memory']['peak_mb']:.1f}MB")
    print(f"  Async:      {results['async_issues']} issues")
    print(f"  Syntax:     {results['syntax_errors']} errors")
    print(f"  ─────────────────────────────────")
    print(f"  {'✅ ALL VALIDATIONS PASSED' if total_pass else '⚠️ ISSUES FOUND — see above'}")

    return results


if __name__ == "__main__":
    asyncio.run(run_validation())
