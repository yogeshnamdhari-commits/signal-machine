#!/usr/bin/env python3
"""
Phase 3 — Paper Trading Launcher

Usage:
    python launch_paper_trading.py                   # Testnet (default)
    python launch_paper_trading.py --production      # Production data
    python launch_paper_trading.py --test --quick     # Quick validation test
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 3 — Live Paper Trading Validation Launcher"
    )
    parser.add_argument("--testnet", action="store_true", default=True,
                        help="Use Binance testnet data (default)")
    parser.add_argument("--production", action="store_true",
                        help="Use Binance production data (live market)")
    parser.add_argument("--test", action="store_true",
                        help="Run validation tests only")
    parser.add_argument("--quick", action="store_true",
                        help="Quick mode (with --test)")
    args = parser.parse_args()

    ai_root = Path(__file__).parent

    if args.test:
        print("Running Phase 3 validation tests...")
        result = subprocess.run(
            [sys.executable, str(ai_root / "validate_paper_trading.py")],
            cwd=str(ai_root),
        )
        sys.exit(result.returncode)

    # Set environment
    if args.production:
        os.environ["BINANCE_TESTNET"] = "false"
        mode_label = "PRODUCTION"
    else:
        os.environ["BINANCE_TESTNET"] = "true"
        mode_label = "TESTNET"

    print("=" * 60)
    print("  PHASE 3 — LIVE PAPER TRADING VALIDATION")
    print("=" * 60)
    print(f"  Data Source:   {mode_label}")
    print(f"  PAPER_TRADING: TRUE")
    print(f"  EXECUTION:     SIMULATION")
    print(f"  ORDERS:        NO_REAL_ORDERS")
    print("=" * 60)
    print()

    # Run the paper trading engine
    result = subprocess.run(
        [sys.executable, str(ai_root / "backtesting" / "paper_trading_validator.py")],
        cwd=str(ai_root),
        env={**os.environ, "BINANCE_TESTNET": "false" if args.production else "true"},
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
