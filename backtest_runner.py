"""
Backtest Runner — CLI entry point for the DeltaTerminal backtest engine.

Usage:
  python backtest_runner.py --symbol BTCUSDT --days 14
  python backtest_runner.py --symbol ETHUSDT --days 7 --interval 15m
  python backtest_runner.py --multi BTCUSDT,ETHUSDT,SOLUSDT --days 30
  python backtest_runner.py --top 20 --days 14
  python backtest_runner.py --top 50 --days 7 --interval 15m

Metrics generated:
  Win Rate, Profit Factor, Sharpe, Sortino, Max Drawdown, Expectancy,
  Kelly Criterion, Calmar Ratio, and full exit attribution.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ai_root = Path(__file__).resolve().parent / "packages" / "ai-engine"
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestReporter,
    HistoricalDataFetcher,
)
from backtest.mock_exchange import MockHistoricalExchange  # noqa: F401 — re-export


async def _run_backtest(args) -> None:
    """Dispatch the backtest run based on CLI arguments."""
    config = BacktestConfig(
        initial_balance=args.balance,
        risk_per_trade_pct=args.risk,
        min_score=args.min_score,
        trailing_pct=args.trailing_pct,
    )
    engine = BacktestEngine(config)

    if args.top:
        # ── Top-N futures by volume ──
        symbols = await MockHistoricalExchange.fetch_top_futures(n=args.top)
        print(f"\n⚡ Running top-{len(symbols)} futures backtest")
        print(f"   Days: {args.days} | Interval: {args.interval} | Balance: ${args.balance:,.0f}")
        result = await engine.run_multi_symbol(symbols, days=args.days, interval=args.interval)
        BacktestReporter.print_summary(result, f"TOP-{len(symbols)}")
        if args.report:
            BacktestReporter.generate_report(result, f"TOP-{len(symbols)}")

    elif args.multi:
        # ── Multi-symbol backtest ──
        symbols = [s.strip() for s in args.multi.split(",") if s.strip()]
        print(f"\n⚡ Running multi-symbol backtest: {symbols}")
        print(f"   Days: {args.days} | Interval: {args.interval} | Balance: ${args.balance:,.0f}")
        result = await engine.run_multi_symbol(symbols, days=args.days, interval=args.interval)
        BacktestReporter.print_summary(result, "MULTI")
        if args.report:
            BacktestReporter.generate_report(result, "MULTI")

    else:
        # ── Single-symbol backtest ──
        symbol = args.symbol
        print(f"\n⚡ Running backtest: {symbol}")
        print(f"   Days: {args.days} | Interval: {args.interval} | Balance: ${args.balance:,.0f}")
        result = await engine.run(symbol, days=args.days, interval=args.interval)
        BacktestReporter.print_summary(result, symbol)
        if args.report:
            BacktestReporter.generate_report(result, symbol)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="DeltaTerminal Backtest Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --symbol BTCUSDT --days 14\n"
            "  %(prog)s --multi BTCUSDT,ETHUSDT,SOLUSDT --days 30\n"
            "  %(prog)s --top 50 --days 7 --interval 15m\n"
        ),
    )
    parser.add_argument("--symbol", type=str, default="BTCUSDT", help="Single trading pair")
    parser.add_argument("--multi", type=str, default="", help="Comma-separated trading pairs")
    parser.add_argument("--top", type=int, default=0, help="Top N futures by 24h volume")
    parser.add_argument("--days", type=int, default=7, help="Days of history")
    parser.add_argument("--interval", type=str, default="5m", help="Kline interval")
    parser.add_argument("--balance", type=float, default=10_000, help="Initial balance (USDT)")
    parser.add_argument("--risk", type=float, default=1.0, help="Risk per trade %%")
    parser.add_argument("--min-score", type=float, default=40.0, help="Min signal score to enter")
    parser.add_argument("--trailing-pct", type=float, default=0.50, help="Trailing stop %%")
    parser.add_argument("--report", action="store_true", default=True, help="Generate HTML report")
    parser.add_argument("--no-report", dest="report", action="store_false", help="Skip HTML report")

    args = parser.parse_args()

    if not args.multi and not args.top and not args.symbol:
        parser.error("Provide --symbol, --multi, or --top")

    asyncio.run(_run_backtest(args))


if __name__ == "__main__":
    main()
