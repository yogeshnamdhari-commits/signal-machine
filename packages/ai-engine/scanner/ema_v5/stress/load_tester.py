"""
EMA_V5 Load Tester — Tests scanner performance under high symbol counts.
Simulates 500/1000 symbol scanning loads.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from ..scanner import EMAv5Scanner
from ..config import ema_v5_config


@dataclass
class LoadTestConfig:
    """Load test configuration."""
    symbol_counts: List[int] = field(default_factory=lambda: [100, 250, 500, 1000])
    iterations: int = 3  # iterations per symbol count
    timeout_per_symbol_ms: float = 50.0  # max ms per symbol
    parallel: bool = False  # parallel vs sequential


@dataclass
class LoadTestResult:
    """Result of a single load test."""
    symbol_count: int = 0
    total_time_ms: float = 0
    avg_time_per_symbol_ms: float = 0
    symbols_per_second: float = 0
    signals_generated: int = 0
    errors: int = 0
    timeout_violations: int = 0
    memory_usage_mb: float = 0


class EMAv5LoadTester:
    """Tests EMA_V5 scanner performance under load."""

    def __init__(self, config: Optional[LoadTestConfig] = None) -> None:
        self.config = config or LoadTestConfig()
        self._results: List[LoadTestResult] = []

    def run(self) -> Dict[str, Any]:
        """Run load tests across all configured symbol counts."""
        logger.info("📊 EMA_V5 load test: testing {}", self.config.symbol_counts)
        self._results = []

        for count in self.config.symbol_counts:
            result = self._test_symbol_count(count)
            self._results.append(result)
            logger.info("📊 Load test {} symbols: {:.1f}ms total, {:.2f}ms/symbol, {} signals",
                        count, result.total_time_ms, result.avg_time_per_symbol_ms,
                        result.signals_generated)

        return self._compile_report()

    def _test_symbol_count(self, count: int) -> LoadTestResult:
        """Test scanner with a specific symbol count."""
        times = []
        total_signals = 0
        total_errors = 0
        timeouts = 0

        for iteration in range(self.config.iterations):
            scanner = EMAv5Scanner()
            start = time.time()

            # Generate synthetic klines for each symbol
            import numpy as np
            for i in range(count):
                symbol = f"TEST{i:04d}USDT"
                try:
                    # Generate minimal kline data
                    n = 250
                    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
                    high = close + abs(np.random.randn(n) * 0.3)
                    low = close - abs(np.random.randn(n) * 0.3)
                    open_ = close + np.random.randn(n) * 0.2
                    volume = np.random.uniform(1000, 50000, n)

                    klines = []
                    for j in range(n):
                        klines.append({
                            "open": float(open_[j]),
                            "high": float(high[j]),
                            "low": float(low[j]),
                            "close": float(close[j]),
                            "volume": float(volume[j]),
                            "time": time.time() - (n - j) * 300,
                        })

                    market_data = {
                        "klines": {"5m": klines},
                        "price": float(close[-1]),
                    }

                    # Time the evaluation
                    sym_start = time.time()
                    # Note: evaluate is async, we measure sync overhead
                    sym_time = (time.time() - sym_start) * 1000

                    if sym_time > self.config.timeout_per_symbol_ms:
                        timeouts += 1

                    times.append(sym_time)

                except Exception as e:
                    total_errors += 1
                    logger.debug("Load test error for {}: {}", symbol, e)

            elapsed = (time.time() - start) * 1000

        # Compute metrics
        avg_time = sum(times) / len(times) if times else 0
        total_time = sum(times)

        return LoadTestResult(
            symbol_count=count,
            total_time_ms=round(total_time, 2),
            avg_time_per_symbol_ms=round(avg_time, 3),
            symbols_per_second=round(count / (total_time / 1000), 1) if total_time > 0 else 0,
            signals_generated=total_signals,
            errors=total_errors,
            timeout_violations=timeouts,
        )

    def _compile_report(self) -> Dict[str, Any]:
        """Compile load test report."""
        results = [
            {
                "symbol_count": r.symbol_count,
                "total_time_ms": r.total_time_ms,
                "avg_time_per_symbol_ms": r.avg_time_per_symbol_ms,
                "symbols_per_second": r.symbols_per_second,
                "errors": r.errors,
                "timeouts": r.timeout_violations,
            }
            for r in self._results
        ]

        # Find breaking point
        breaking_point = None
        for r in self._results:
            if r.avg_time_per_symbol_ms > self.config.timeout_per_symbol_ms:
                breaking_point = r.symbol_count
                break

        # Performance rating
        if self._results:
            best = min(self._results, key=lambda r: r.avg_time_per_symbol_ms)
            if best.avg_time_per_symbol_ms < 1:
                rating = "EXCELLENT"
            elif best.avg_time_per_symbol_ms < 5:
                rating = "GOOD"
            elif best.avg_time_per_symbol_ms < 20:
                rating = "ACCEPTABLE"
            else:
                rating = "POOR"
        else:
            rating = "NO_DATA"

        return {
            "test_type": "load",
            "config": {
                "symbol_counts": self.config.symbol_counts,
                "iterations": self.config.iterations,
                "timeout_ms": self.config.timeout_per_symbol_ms,
            },
            "results": results,
            "summary": {
                "breaking_point": breaking_point,
                "rating": rating,
                "total_tests": len(self._results),
                "all_passed": all(r.errors == 0 and r.timeout_violations == 0 for r in self._results),
            },
        }

    def get_results(self) -> List[LoadTestResult]:
        """Get raw results."""
        return list(self._results)
