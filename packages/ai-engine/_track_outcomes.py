#!/usr/bin/env python3
"""
EMA V5 Outcome Tracker — Fetches historical forward prices for candidates.

Fetches the actual price at each forward timestamp (15m, 30m, 1h, 2h, 4h, 8h, 24h)
using Binance historical klines, then computes returns and MFE/MAE.

Usage:
    python _track_outcomes.py
    python _track_outcomes.py --hours 72
    python _track_outcomes.py --batch 500   # process N candidates per run
"""
import sqlite3
import time
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import requests


# Use the same path as CandidateLifecycleTracker (4 .parent from scanner/ema_v5/)
# From packages/ai-engine/_track_outcomes.py: parent=ai-engine, parent=packages, parent=root, parent=above-root?
# Actually: CandidateLifecycleTracker is at scanner/ema_v5/candidate_lifecycle_tracker.py
#   .parent.parent.parent.parent = packages/
# _track_outcomes.py is at packages/ai-engine/_track_outcomes.py
#   .parent.parent = packages/  (only need 2 .parent calls)
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "ema_v5_lifecycle.db"
BINANCE_API = "https://fapi.binance.com/fapi/v1/klines"

# Forward intervals to track (label, offset_hours)
INTERVALS = [
    ("15m", 15 / 60),
    ("30m", 30 / 60),
    ("1h", 1),
    ("2h", 2),
    ("4h", 4),
    ("8h", 8),
    ("24h", 24),
]

# For MFE/MAE: fetch 5m klines covering the full 24h window
MFE_MAE_WINDOW_HOURS = 24


def fetch_kline_at(symbol: str, target_ts_ms: int) -> Optional[float]:
    """Fetch the close price of the 5m kline closest to target_ts_ms."""
    try:
        # Fetch 1 candle around the target time
        url = f"{BINANCE_API}?symbol={symbol}&interval=5m&startTime={target_ts_ms - 300_000}&endTime={target_ts_ms + 300_000}&limit=3"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        if not data or not isinstance(data, list):
            return None
        # Find the kline whose close time is closest to target
        best = None
        best_dist = float("inf")
        for k in data:
            close_time = k[6]  # close time ms
            dist = abs(close_time - target_ts_ms)
            if dist < best_dist:
                best_dist = dist
                best = float(k[4])  # close price
        return best
    except Exception:
        return None


def fetch_5m_klines_range(symbol: str, start_ms: int, end_ms: int) -> List[float]:
    """Fetch all 5m kline close prices in [start_ms, end_ms]."""
    prices = []
    try:
        # Binance limit is 1500 candles; 5m = 30 per hour, so max ~50h per request
        url = f"{BINANCE_API}?symbol={symbol}&interval=5m&startTime={start_ms}&endTime={end_ms}&limit=1500"
        resp = requests.get(url, timeout=15)
        data = resp.json()
        if data and isinstance(data, list):
            prices = [float(k[4]) for k in data]  # close prices
    except Exception:
        pass
    return prices


def compute_forward_prices(
    symbol: str, direction: str, entry_price: float, entry_ts: float
) -> Tuple[Dict[str, float], float, float]:
    """Fetch historical prices at each forward interval. Returns (prices, mfe, mae)."""
    entry_ms = int(entry_ts * 1000)
    now_ms = int(time.time() * 1000)
    elapsed_hours = (time.time() - entry_ts) / 3600

    prices = {}
    for label, hours_needed in INTERVALS:
        if elapsed_hours >= hours_needed:
            target_ms = entry_ms + int(hours_needed * 3600 * 1000)
            # Don't fetch future prices
            if target_ms <= now_ms:
                price = fetch_kline_at(symbol, target_ms)
                if price and price > 0:
                    prices[label] = price
            time.sleep(0.05)  # rate limit

    # MFE/MAE: fetch all 5m klines from entry to min(entry+24h, now)
    end_window_ms = min(entry_ms + MFE_MAE_WINDOW_HOURS * 3600 * 1000, now_ms)
    kline_prices = fetch_5m_klines_range(symbol, entry_ms, end_window_ms)
    time.sleep(0.05)

    mfe = 0.0
    mae = 0.0
    if kline_prices and entry_price > 0:
        if direction == "LONG":
            mfe = max((p - entry_price) / entry_price * 100 for p in kline_prices)
            mae = min((p - entry_price) / entry_price * 100 for p in kline_prices)
        else:
            mfe = max((entry_price - p) / entry_price * 100 for p in kline_prices)
            mae = min((entry_price - p) / entry_price * 100 for p in kline_prices)

    return prices, mfe, mae


def track_outcomes(hours: int = 24, batch: int = 0):
    """Track outcomes for untracked candidates using historical klines."""
    if not DB_PATH.exists():
        print(f"❌ Database not found: {DB_PATH}")
        print("   Run the scanner with lifecycle tracking enabled first.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    cutoff = datetime.now().timestamp() - (hours * 3600)

    query = """
        SELECT id, symbol, direction, entry_price, timestamp
        FROM candidate_lifecycle
        WHERE outcome_tracked = 0 AND timestamp > ? AND entry_price > 0
        ORDER BY timestamp ASC
    """
    if batch > 0:
        query += f" LIMIT {batch}"

    cur = conn.execute(query, (cutoff,))
    candidates = cur.fetchall()

    if not candidates:
        print(f"✅ No untracked candidates in the last {hours} hours")
        conn.close()
        return

    print(f"📊 Tracking outcomes for {len(candidates)} candidates...")
    print(f"   DB: {DB_PATH}")

    updated = 0
    errors = 0
    for candidate_id, symbol, direction, entry_price, timestamp in candidates:
        try:
            prices, mfe, mae = compute_forward_prices(
                symbol, direction, entry_price, timestamp
            )

            if not prices:
                errors += 1
                continue

            # Calculate returns
            multiplier = 1 if direction == "LONG" else -1
            returns = {
                label: multiplier * (price - entry_price) / entry_price * 100
                for label, price in prices.items()
            }

            # Classify outcome based on 1h return (or best available)
            return_1h = returns.get("1h", returns.get("30m", returns.get("15m", 0)))
            if return_1h > 0.5:
                outcome = "profitable"
            elif return_1h < -0.5:
                outcome = "losing"
            else:
                outcome = "breakeven"

            # Opportunity type based on best return across all intervals
            best_return = max(returns.values()) if returns else 0
            if best_return >= 2.0:
                opportunity_type = "strong_winner"
            elif best_return >= 0.5:
                opportunity_type = "small_winner"
            elif best_return >= -0.5:
                opportunity_type = "breakeven"
            elif best_return >= -2.0:
                opportunity_type = "small_loser"
            else:
                opportunity_type = "large_loser"

            conn.execute("""
                UPDATE candidate_lifecycle
                SET price_15m = ?, price_30m = ?, price_1h = ?, price_2h = ?,
                    price_4h = ?, price_8h = ?, price_24h = ?,
                    return_15m_pct = ?, return_30m_pct = ?, return_1h_pct = ?,
                    return_2h_pct = ?, return_4h_pct = ?, return_8h_pct = ?,
                    return_24h_pct = ?,
                    would_be_mfe = ?, would_be_mae = ?,
                    outcome = ?, opportunity_type = ?,
                    outcome_tracked = 1
                WHERE id = ?
            """, (
                prices.get("15m", 0), prices.get("30m", 0), prices.get("1h", 0),
                prices.get("2h", 0), prices.get("4h", 0), prices.get("8h", 0),
                prices.get("24h", 0),
                returns.get("15m", 0), returns.get("30m", 0), returns.get("1h", 0),
                returns.get("2h", 0), returns.get("4h", 0), returns.get("8h", 0),
                returns.get("24h", 0),
                mfe, mae,
                outcome, opportunity_type,
                candidate_id,
            ))

            updated += 1
            if updated % 25 == 0:
                conn.commit()
                print(f"  ✅ {updated}/{len(candidates)} updated ({errors} errors)...")

        except Exception as e:
            errors += 1
            if errors <= 3:
                print(f"  ⚠️ Error on {symbol}: {e}")

    conn.commit()
    conn.close()

    print(f"\n{'='*60}")
    print(f"✅ Updated: {updated} candidates")
    print(f"⚠️ Errors:  {errors} candidates")
    print(f"📊 Run analysis:")
    print(f"   python _lifecycle_analysis.py --hours {hours}")
    print(f"   python _baseline_report.py --hours {hours}")


def main():
    """Main entry point."""
    hours = 24
    batch = 0  # 0 = unlimited

    # Parse arguments
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--hours" and i + 1 < len(args):
            hours = int(args[i + 1])
        elif arg == "--batch" and i + 1 < len(args):
            batch = int(args[i + 1])

    track_outcomes(hours, batch)


if __name__ == "__main__":
    main()
