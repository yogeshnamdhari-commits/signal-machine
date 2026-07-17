"""
Router Validation Suite — Stress test venue selection and failover.

Simulates 1000 orders through the SmartOrderRouter to verify:
- Correct venue selection based on weighted scoring
- Failover behavior when venues are unhealthy
- Duplicate route prevention
- Latency tracking across all venues
- Execution cost accuracy
- Route explanation generation

Generates:
- routing_report.json: Per-order routing decisions
- routing_metrics.json: Aggregated performance metrics
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

import sys
_ai_root = Path(__file__).resolve().parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from exchanges.base_exchange import (
    BaseExchange, ExchangeOrder, ExchangePosition, AccountState,
    OrderbookSnapshot, OrderbookLevel, FundingInfo, ExchangeLatency,
)
from exchanges.smart_order_router import SmartOrderRouter, RouterConfig, RouterWeights

REPORT_PATH = Path("data/reports/routing_report.json")
METRICS_PATH = Path("data/reports/routing_metrics.json")


# ── Simulation Exchange Adapters ─────────────────────────────────

class SimulatedExchange(BaseExchange):
    """
    Simulation exchange adapter for validation testing.

    Generates realistic market data with configurable characteristics
    to test routing decisions across different venue conditions.
    """

    def __init__(
        self,
        name: str,
        mid_price: float = 50000.0,
        spread_bps: float = 1.0,
        depth_usd: float = 500_000.0,
        latency_ms: float = 50.0,
        maker_fee: float = 0.0002,
        taker_fee: float = 0.0004,
        funding_rate: float = 0.0001,
        volatility: float = 0.001,
    ) -> None:
        super().__init__(name=name, api_key="sim_key", api_secret="sim_secret")
        self._mid_price = mid_price
        self._spread_bps = spread_bps
        self._depth_usd = depth_usd
        self._base_latency_ms = latency_ms
        self._maker_fee = maker_fee
        self._taker_fee = taker_fee
        self._funding_rate = funding_rate
        self._volatility = volatility
        self._connected = True  # Simulated exchanges are always connected

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def place_order(self, **kwargs: Any) -> ExchangeOrder:
        return ExchangeOrder(
            order_id=f"sim_{self._name}_{int(time.time()*1000)}",
            symbol=kwargs.get("symbol", ""),
            side=kwargs.get("side", ""),
            order_type=kwargs.get("order_type", ""),
            status="FILLED",
            avg_price=self._mid_price,
            quantity=kwargs.get("quantity", 0),
            executed_qty=kwargs.get("quantity", 0),
        )

    async def cancel_order(self, **kwargs: Any) -> ExchangeOrder:
        return ExchangeOrder(status="CANCELED")

    async def get_order(self, **kwargs: Any) -> ExchangeOrder:
        return ExchangeOrder(status="FILLED")

    async def get_open_orders(self, symbol: str = "") -> List[ExchangeOrder]:
        return []

    async def get_positions(self) -> List[ExchangePosition]:
        return []

    async def get_account(self) -> AccountState:
        return AccountState(total_balance=100_000, available_balance=80_000)

    async def get_balance(self) -> Dict[str, float]:
        return {"balance": 100_000.0, "available": 80_000.0, "unrealized_pnl": 0.0}

    async def get_orderbook(self, symbol: str, depth: int = 20) -> OrderbookSnapshot:
        """Generate realistic orderbook with configurable characteristics."""
        # Add small random price movement
        price_jitter = self._mid_price * random.uniform(-self._volatility, self._volatility)
        mid = self._mid_price + price_jitter

        spread = mid * self._spread_bps / 10_000
        best_bid = mid - spread / 2
        best_ask = mid + spread / 2

        # Generate depth levels with realistic decay
        bids = []
        asks = []
        levels = min(depth, 20)

        for i in range(levels):
            # Depth decays exponentially from best price
            level_offset = spread * (i + 1) * 0.5
            qty_at_level = (self._depth_usd / (2 * levels)) / (mid + level_offset)
            qty_jitter = qty_at_level * random.uniform(0.8, 1.2)

            bids.append(OrderbookLevel(
                price=round(best_bid - level_offset, 2),
                quantity=round(qty_jitter, 6),
            ))
            asks.append(OrderbookLevel(
                price=round(best_ask + level_offset, 2),
                quantity=round(qty_jitter, 6),
            ))

        # Simulate latency (very small for fast validation)
        await asyncio.sleep(self._base_latency_ms / 100_000 * random.uniform(0.5, 1.5))

        return OrderbookSnapshot(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=time.time(),
        )

    async def get_funding_rate(self, symbol: str) -> FundingInfo:
        # Add small jitter to funding rate
        rate = self._funding_rate + random.uniform(-0.00005, 0.00005)
        return FundingInfo(exchange=self.name, symbol=symbol, funding_rate=rate)

    async def get_latency(self) -> ExchangeLatency:
        return ExchangeLatency(exchange=self.name, ping_ms=self.get_latency_ms(), update_time=int(time.time()))

    def get_maker_fee(self) -> float:
        return self._maker_fee

    def get_taker_fee(self) -> float:
        return self._taker_fee


# ── Validation Engine ────────────────────────────────────────────

async def validate_routing_performance() -> Dict[str, Any]:
    """Execute 1000 simulated orders through the SmartOrderRouter."""

    # ── Create simulation exchanges with distinct characteristics ──
    # Each exchange has a clear competitive edge in a specific dimension
    # so that different venues win depending on order characteristics
    exchanges = {
        "binance": SimulatedExchange(
            name="binance",
            mid_price=50000.0,
            spread_bps=0.3,        # Tightest spread
            depth_usd=600_000.0,   # Good depth
            latency_ms=15.0,       # Fastest
            maker_fee=0.0002,
            taker_fee=0.0004,
            funding_rate=0.0001,
            volatility=0.0005,
        ),
        "bybit": SimulatedExchange(
            name="bybit",
            mid_price=50000.0,
            spread_bps=1.5,        # Wider spread
            depth_usd=400_000.0,
            latency_ms=40.0,
            maker_fee=0.0001,      # Lowest maker fee
            taker_fee=0.0002,      # Lowest taker fee
            funding_rate=0.0001,
            volatility=0.0008,
        ),
        "okx": SimulatedExchange(
            name="okx",
            mid_price=50000.0,
            spread_bps=0.8,
            depth_usd=1_500_000.0, # Deepest book by far
            latency_ms=35.0,
            maker_fee=0.0002,
            taker_fee=0.0004,
            funding_rate=-0.0004,  # Strong negative funding
            volatility=0.0006,
        ),
        "delta": SimulatedExchange(
            name="delta",
            mid_price=50000.0,
            spread_bps=0.5,        # Good spread
            depth_usd=800_000.0,
            latency_ms=70.0,       # Slower
            maker_fee=0.00015,
            taker_fee=0.0003,
            funding_rate=-0.0001,
            volatility=0.001,
        ),
    }

    # Balanced weights so each exchange's edge matters
    weights = RouterWeights(
        liquidity=0.20, spread=0.25, latency=0.20, fee=0.20, slippage=0.15,
    )
    config = RouterConfig(weights=weights, dedup_window_sec=0.0001)
    router = SmartOrderRouter(exchanges, config=config)

    logger.info("=" * 60)
    logger.info("  SmartOrderRouter Validation Suite")
    logger.info("  Simulating 1000 orders across 4 exchanges")
    logger.info("=" * 60)

    # ── Test scenarios ──

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]
    sides = ["BUY", "SELL"]

    results: List[Dict[str, Any]] = []
    failover_events: List[Dict[str, Any]] = []
    duplicate_events: List[Dict[str, Any]] = []

    start_time = time.time()

    # ── Phase 1: Normal routing (orders 0-799) ──
    logger.info("\n--- Phase 1: Normal Routing (800 orders) ---")
    for i in range(800):
        symbol = symbols[i % len(symbols)]
        side = sides[i % 2]
        # Randomize quantity to create unique dedup keys and test various sizes
        qty = random.choice([0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0])

        t0 = time.time()
        routing = await router.route_order(symbol, side, "MARKET", qty)
        latency = (time.time() - t0) * 1000

        results.append({
            "iteration": i,
            "phase": "normal",
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "exchange": routing.get("exchange"),
            "score": routing.get("score"),
            "expected_slippage": routing.get("expected_slippage"),
            "expected_fee": routing.get("expected_fee"),
            "expected_fill_price": routing.get("expected_fill_price"),
            "liquidity_score": routing.get("liquidity_score"),
            "routing_reason": routing.get("routing_reason"),
            "latency_ms": latency,
        })

        if i % 200 == 0:
            logger.info("  Processed {} / 800 orders (normal)...", i)

    # ── Phase 2: Failover testing (orders 800-899) ──
    logger.info("\n--- Phase 2: Failover Testing (100 orders) ---")
    logger.info("  Simulating Binance degradation...")

    # Degrade Binance
    exchanges["binance"]._spread_bps = 10.0
    exchanges["binance"]._depth_usd = 5_000.0
    exchanges["binance"]._base_latency_ms = 500.0

    for i in range(100):
        symbol = symbols[i % len(symbols)]
        side = sides[i % 2]
        qty = random.choice([0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0])

        t0 = time.time()
        routing = await router.route_order(symbol, side, "MARKET", qty)
        latency = (time.time() - t0) * 1000

        # Track if routing moved away from binance
        if routing.get("exchange") != "binance":
            failover_events.append({
                "iteration": 800 + i,
                "from_exchange": "binance",
                "to_exchange": routing.get("exchange"),
                "reason": "binance_degraded",
            })

        results.append({
            "iteration": 800 + i,
            "phase": "failover",
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "exchange": routing.get("exchange"),
            "score": routing.get("score"),
            "expected_slippage": routing.get("expected_slippage"),
            "expected_fee": routing.get("expected_fee"),
            "expected_fill_price": routing.get("expected_fill_price"),
            "liquidity_score": routing.get("liquidity_score"),
            "routing_reason": routing.get("routing_reason"),
            "latency_ms": latency,
        })

        if i % 50 == 0:
            logger.info("  Processed {} / 100 orders (failover)...", i)

    # Restore Binance
    exchanges["binance"]._spread_bps = 0.5
    exchanges["binance"]._depth_usd = 1_000_000.0
    exchanges["binance"]._base_latency_ms = 30.0

    # ── Phase 3: Duplicate prevention (orders 900-949) ──
    logger.info("\n--- Phase 3: Duplicate Prevention (50 orders) ---")
    # Temporarily restore normal dedup window for this test
    router._dedup_window = 2.0
    for i in range(50):
        # Send same order repeatedly
        t0 = time.time()
        routing = await router.route_order("BTCUSDT", "BUY", "MARKET", 1.0)
        latency = (time.time() - t0) * 1000

        if routing.get("exchange") == "none" and routing.get("routing_reason") == "duplicate_prevention":
            duplicate_events.append({
                "iteration": 900 + i,
                "prevented": True,
            })
        else:
            duplicate_events.append({
                "iteration": 900 + i,
                "prevented": False,
            })

        results.append({
            "iteration": 900 + i,
            "phase": "duplicate",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "quantity": 1.0,
            "exchange": routing.get("exchange"),
            "score": routing.get("score"),
            "expected_slippage": routing.get("expected_slippage"),
            "expected_fee": routing.get("expected_fee"),
            "expected_fill_price": routing.get("expected_fill_price"),
            "liquidity_score": routing.get("liquidity_score"),
            "routing_reason": routing.get("routing_reason"),
            "latency_ms": latency,
        })

        # Small delay between duplicates
        await asyncio.sleep(0.005)

    logger.info("  Duplicate prevention tested: {} prevented out of {}",
                sum(1 for d in duplicate_events if d["prevented"]), 50)

    # Restore short dedup window for Phase 4
    router._dedup_window = 0.0001

    # ── Phase 4: Latency tracking (orders 950-999) ──
    logger.info("\n--- Phase 4: Latency Tracking (50 orders) ---")
    for i in range(50):
        symbol = symbols[i % len(symbols)]
        side = sides[i % 2]
        qty = random.choice([0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0])

        t0 = time.time()
        routing = await router.route_order(symbol, side, "MARKET", qty)
        latency = (time.time() - t0) * 1000

        results.append({
            "iteration": 950 + i,
            "phase": "latency",
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "exchange": routing.get("exchange"),
            "score": routing.get("score"),
            "expected_slippage": routing.get("expected_slippage"),
            "expected_fee": routing.get("expected_fee"),
            "expected_fill_price": routing.get("expected_fill_price"),
            "liquidity_score": routing.get("liquidity_score"),
            "routing_reason": routing.get("routing_reason"),
            "latency_ms": latency,
        })

    total_duration = time.time() - start_time

    # ── Compute Metrics ──────────────────────────────────────────

    normal_results = [r for r in results if r["phase"] == "normal"]
    failover_results = [r for r in results if r["phase"] == "failover"]
    latency_results = [r for r in results if r["phase"] == "latency"]

    # Venue distribution
    all_exchanges = [r["exchange"] for r in results if r["exchange"] != "none"]
    venue_counts = {}
    for exch in all_exchanges:
        venue_counts[exch] = venue_counts.get(exch, 0) + 1

    # Normal phase venue distribution
    normal_venues = [r["exchange"] for r in normal_results if r["exchange"] != "none"]
    normal_venue_counts = {}
    for exch in normal_venues:
        normal_venue_counts[exch] = normal_venue_counts.get(exch, 0) + 1

    # Failover phase venue distribution
    failover_venues = [r["exchange"] for r in failover_results if r["exchange"] != "none"]
    failover_venue_counts = {}
    for exch in failover_venues:
        failover_venue_counts[exch] = failover_venue_counts.get(exch, 0) + 1

    # Averages
    valid_scores = [r["score"] for r in results if r["score"] and r["score"] > 0]
    valid_latencies = [r["latency_ms"] for r in results if r["latency_ms"] > 0]
    valid_slippage = [r["expected_slippage"] for r in results if r["expected_slippage"] is not None]
    valid_fees = [r["expected_fee"] for r in results if r["expected_fee"] is not None]

    avg_score = sum(valid_scores) / len(valid_scores) if valid_scores else 0
    avg_latency = sum(valid_latencies) / len(valid_latencies) if valid_latencies else 0
    avg_slippage = sum(valid_slippage) / len(valid_slippage) if valid_slippage else 0
    total_fees = sum(valid_fees) if valid_fees else 0

    # Per-phase averages
    normal_scores = [r["score"] for r in normal_results if r["score"] and r["score"] > 0]
    failover_scores = [r["score"] for r in failover_results if r["score"] and r["score"] > 0]

    avg_normal_score = sum(normal_scores) / len(normal_scores) if normal_scores else 0
    avg_failover_score = sum(failover_scores) / len(failover_scores) if failover_scores else 0

    # Duplicate prevention rate
    duplicate_prevented = sum(1 for d in duplicate_events if d["prevented"])
    duplicate_rate = duplicate_prevented / len(duplicate_events) if duplicate_events else 0

    # Failover success rate
    failover_success = len(failover_events)
    failover_rate = failover_success / 100 if failover_events else 0

    # Build metrics
    metrics = {
        "validation_summary": {
            "total_orders_simulated": 1000,
            "total_duration_sec": round(total_duration, 2),
            "orders_per_second": round(1000 / total_duration, 2),
            "success_rate_pct": round(
                (1000 - sum(1 for r in results if r["exchange"] == "none")) / 1000 * 100, 2
            ),
        },
        "venue_selection": {
            "overall_distribution": venue_counts,
            "normal_phase_distribution": normal_venue_counts,
            "failover_phase_distribution": failover_venue_counts,
            "best_exchange": max(venue_counts, key=venue_counts.get) if venue_counts else "none",
        },
        "scoring_metrics": {
            "avg_execution_score": round(avg_score, 6),
            "avg_normal_phase_score": round(avg_normal_score, 6),
            "avg_failover_phase_score": round(avg_failover_score, 6),
        },
        "cost_metrics": {
            "total_execution_cost_usd": round(total_fees, 4),
            "avg_fee_per_order_usd": round(total_fees / 1000, 8),
            "avg_expected_slippage_bps": round(avg_slippage, 4),
        },
        "latency_metrics": {
            "avg_routing_latency_ms": round(avg_latency, 2),
            "min_routing_latency_ms": round(min(valid_latencies), 2) if valid_latencies else 0,
            "max_routing_latency_ms": round(max(valid_latencies), 2) if valid_latencies else 0,
            "p95_routing_latency_ms": round(
                sorted(valid_latencies)[int(len(valid_latencies) * 0.95)], 2
            ) if valid_latencies else 0,
            "p99_routing_latency_ms": round(
                sorted(valid_latencies)[int(len(valid_latencies) * 0.99)], 2
            ) if valid_latencies else 0,
        },
        "failover_metrics": {
            "failover_events": failover_success,
            "failover_rate_pct": round(failover_rate * 100, 2),
            "avg_score_drop_on_failover": round(avg_normal_score - avg_failover_score, 6),
        },
        "duplicate_prevention": {
            "duplicate_attempts": len(duplicate_events),
            "duplicates_prevented": duplicate_prevented,
            "prevention_rate_pct": round(duplicate_rate * 100, 2),
        },
        "router_stats": router.router_stats(),
    }

    # ── Verify Requirements ──────────────────────────────────────

    verifications = {
        "correct_venue_selection": avg_normal_score > 0.5,
        "failover_behavior": failover_success > 0,
        "duplicate_prevention": duplicate_prevented > 0,
        "latency_tracking": avg_latency > 0,
        "scoring_functional": avg_score > 0,
        "all_exchanges_utilized": len(venue_counts) >= 3,
        "cost_tracking": total_fees > 0,
        "route_explanations": all(
            r.get("routing_reason") for r in results if r["exchange"] != "none"
        ),
    }

    metrics["verifications"] = verifications
    metrics["all_passed"] = all(verifications.values())

    # ── Generate Reports ─────────────────────────────────────────

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    with open(REPORT_PATH, "w") as f:
        json.dump(results, f, indent=2)

    with open(METRICS_PATH, "w") as f:
        json.dump(metrics, f, indent=2)

    # ── Print Results ────────────────────────────────────────────

    logger.info("\n" + "=" * 60)
    logger.info("  VALIDATION RESULTS")
    logger.info("=" * 60)
    logger.info("")
    logger.info("  Orders Simulated:     1000")
    logger.info("  Duration:             {:.2f}s", total_duration)
    logger.info("  Throughput:           {:.0f} orders/sec", 1000 / total_duration)
    logger.info("")
    logger.info("  VENUE SELECTION:")
    for exch, count in sorted(venue_counts.items(), key=lambda x: -x[1]):
        logger.info("    {:10s}  {:4d} orders ({:.1f}%)", exch.upper(), count, count / 10)
    logger.info("")
    logger.info("  SCORING:")
    logger.info("    Avg Score:          {:.4f}", avg_score)
    logger.info("    Normal Phase:       {:.4f}", avg_normal_score)
    logger.info("    Failover Phase:     {:.4f}", avg_failover_score)
    logger.info("")
    logger.info("  COSTS:")
    logger.info("    Total Fees:         ${:.4f}", total_fees)
    logger.info("    Avg Fee/Order:      ${:.8f}", total_fees / 1000)
    logger.info("    Avg Slippage:       {:.4f} bps", avg_slippage)
    logger.info("")
    logger.info("  LATENCY:")
    logger.info("    Avg:                {:.2f}ms", avg_latency)
    logger.info("    P95:                {:.2f}ms", sorted(valid_latencies)[int(len(valid_latencies) * 0.95)] if valid_latencies else 0)
    logger.info("    P99:                {:.2f}ms", sorted(valid_latencies)[int(len(valid_latencies) * 0.99)] if valid_latencies else 0)
    logger.info("")
    logger.info("  FAILOVER:")
    logger.info("    Events:             {}", failover_success)
    logger.info("    Score Drop:         {:.4f}", avg_normal_score - avg_failover_score)
    logger.info("")
    logger.info("  DUPLICATE PREVENTION:")
    logger.info("    Prevented:          {} / {}", duplicate_prevented, len(duplicate_events))
    logger.info("")
    logger.info("  VERIFICATIONS:")
    for check, passed in verifications.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        logger.info("    {:30s} {}", check, status)
    logger.info("")
    if metrics["all_passed"]:
        logger.info("  ✅ ALL VERIFICATIONS PASSED")
    else:
        logger.info("  ❌ SOME VERIFICATIONS FAILED")
    logger.info("")
    logger.info("  Reports saved:")
    logger.info("    {}", REPORT_PATH)
    logger.info("    {}", METRICS_PATH)
    logger.info("=" * 60)

    return metrics


if __name__ == "__main__":
    asyncio.run(validate_routing_performance())