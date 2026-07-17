"""
SmartOrderRouter — Production-grade multi-exchange order routing engine.

Automatically routes every trade to the best exchange based on:
- Orderbook liquidity and depth
- Bid-ask spread
- Exchange latency
- Maker/taker fees
- Estimated slippage
- Funding rates
- Available margin

Architecture:
- Async-first with concurrent venue data collection
- Weighted scoring with configurable weights
- Automatic failover to next-best venue
- Duplicate order prevention
- Execution audit logging
- Route explanation generation

Supported exchanges: Binance, Bybit, OKX, Delta Exchange
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

import sys
_ai_root = Path(__file__).resolve().parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from exchanges.base_exchange import (
    BaseExchange, OrderbookSnapshot, VenueHealth,
)


# ── Configuration ────────────────────────────────────────────────

@dataclass(frozen=True)
class RouterWeights:
    """Configurable scoring weights for venue selection."""
    liquidity: float = 0.30       # Depth of orderbook
    spread: float = 0.20          # Tighter spread = better
    latency: float = 0.20         # Lower latency = better
    fee: float = 0.15             # Lower fee = better
    slippage: float = 0.15        # Lower expected slippage = better

    def validate(self) -> bool:
        total = self.liquidity + self.spread + self.latency + self.fee + self.slippage
        return abs(total - 1.0) < 0.001


@dataclass(frozen=True)
class RouterConfig:
    """Smart order router configuration."""
    weights: RouterWeights = field(default_factory=RouterWeights)
    max_slippage_bps: float = 50.0        # Max acceptable slippage in bps
    min_liquidity_usd: float = 10_000.0   # Min liquidity required
    latency_window_sec: float = 300.0     # Window for latency averaging
    health_check_interval: float = 10.0   # Health check frequency
    max_consecutive_failures: int = 5     # Failover threshold
    enable_funding_penalty: bool = True   # Penalize high funding rates
    funding_penalty_weight: float = 0.02  # Funding rate penalty multiplier
    dedup_window_sec: float = 2.0         # Duplicate route prevention window


# ── Routing Result ───────────────────────────────────────────────

@dataclass
class VenueScore:
    """Detailed score breakdown for a single venue."""
    exchange: str = ""
    total_score: float = 0.0
    liquidity_score: float = 0.0
    spread_score: float = 0.0
    latency_score: float = 0.0
    fee_score: float = 0.0
    slippage_score: float = 0.0
    expected_slippage_bps: float = 0.0
    expected_fee: float = 0.0
    expected_fill_price: float = 0.0
    funding_rate: float = 0.0
    spread_bps: float = 0.0
    avg_latency_ms: float = 0.0
    orderbook_depth_usd: float = 0.0
    is_healthy: bool = True
    failure_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exchange": self.exchange,
            "score": round(self.total_score, 6),
            "liquidity_score": round(self.liquidity_score, 6),
            "spread_score": round(self.spread_score, 6),
            "latency_score": round(self.latency_score, 6),
            "fee_score": round(self.fee_score, 6),
            "slippage_score": round(self.slippage_score, 6),
            "expected_slippage": round(self.expected_slippage_bps, 4),
            "expected_fee": round(self.expected_fee, 8),
            "expected_fill_price": round(self.expected_fill_price, 4),
            "funding_rate": round(self.funding_rate, 8),
            "spread_bps": round(self.spread_bps, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "orderbook_depth_usd": round(self.orderbook_depth_usd, 2),
            "is_healthy": self.is_healthy,
        }


@dataclass
class RoutingResult:
    """Complete routing decision with explanation."""
    exchange: str = "none"
    score: float = 0.0
    expected_slippage: float = 0.0
    expected_fee: float = 0.0
    expected_fill_price: float = 0.0
    liquidity_score: float = 0.0
    routing_reason: str = ""
    all_scores: List[VenueScore] = field(default_factory=list)
    selected_venue: Optional[VenueScore] = None
    timestamp: float = 0.0
    latency_ms: float = 0.0
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exchange": self.exchange,
            "score": round(self.score, 6),
            "expected_slippage": round(self.expected_slippage, 4),
            "expected_fee": round(self.expected_fee, 8),
            "expected_fill_price": round(self.expected_fill_price, 4),
            "liquidity_score": round(self.liquidity_score, 6),
            "routing_reason": self.routing_reason,
            "all_venues": [s.to_dict() for s in self.all_scores],
            "latency_ms": round(self.latency_ms, 2),
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "timestamp": self.timestamp,
        }


# ── Router Statistics ────────────────────────────────────────────

@dataclass
class RouterStats:
    """Aggregated routing statistics."""
    total_routes: int = 0
    successful_routes: int = 0
    failed_routes: int = 0
    failover_count: int = 0
    duplicate_preventions: int = 0
    venue_distribution: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    avg_score: float = 0.0
    avg_latency_ms: float = 0.0
    total_execution_cost: float = 0.0
    best_exchange: str = ""
    routing_reasons: Dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _score_sum: float = 0.0
    _latency_sum: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_routes": self.total_routes,
            "successful_routes": self.successful_routes,
            "failed_routes": self.failed_routes,
            "failover_count": self.failover_count,
            "duplicate_preventions": self.duplicate_preventions,
            "venue_distribution": dict(self.venue_distribution),
            "avg_score": round(self.avg_score, 6),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "total_execution_cost": round(self.total_execution_cost, 4),
            "best_exchange": self.best_exchange,
            "routing_reasons": dict(self.routing_reasons),
        }


# ── Smart Order Router ───────────────────────────────────────────

class SmartOrderRouter:
    """
    Production-grade multi-exchange order router.

    For every signal:
    1. Collect market data from all venues concurrently
    2. Score each venue using weighted multi-factor algorithm
    3. Select the best venue with failover support
    4. Log the decision with full explanation
    5. Track statistics and performance

    Scoring formula:
        execution_score = (
            liquidity_weight * normalized_liquidity +
            spread_weight * normalized_spread +
            latency_weight * normalized_latency +
            fee_weight * normalized_fee +
            slippage_weight * normalized_slippage
        )
    """

    def __init__(
        self,
        exchanges: Dict[str, BaseExchange],
        audit: Any = None,
        config: Optional[RouterConfig] = None,
    ) -> None:
        self._exchanges = exchanges
        self._audit = audit
        self._config = config or RouterConfig()

        # Validate weights
        if not self._config.weights.validate():
            raise ValueError("Router weights must sum to 1.0")

        # Statistics
        self._stats = RouterStats()
        self._routing_history: List[RoutingResult] = []
        self._max_history = 1000

        # Duplicate prevention
        self._recent_routes: Dict[str, float] = {}  # symbol:side:qty -> timestamp
        self._dedup_window = self._config.dedup_window_sec

        logger.info("[router] SmartOrderRouter initialized with {} venues: {}",
                     len(exchanges), list(exchanges.keys()))

    # ── Main Routing Entry Point ─────────────────────────────────

    async def route_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Route an order to the best exchange.

        Args:
            symbol: Trading pair (e.g. "BTCUSDT")
            side: "BUY" or "SELL"
            order_type: "MARKET" or "LIMIT"
            quantity: Order size
            price: Limit price (0 for market orders)

        Returns:
            Dict with exchange, score, expected_slippage, expected_fee,
            expected_fill_price, liquidity_score, routing_reason
        """
        t0 = time.monotonic()
        self._stats.total_routes += 1

        # Duplicate prevention
        dedup_key = f"{symbol}:{side}:{quantity:.6f}"
        now = time.time()
        if dedup_key in self._recent_routes:
            if now - self._recent_routes[dedup_key] < self._dedup_window:
                self._stats.duplicate_preventions += 1
                logger.warning("[router] Duplicate route prevented: {}", dedup_key)
                return {
                    "exchange": "none",
                    "score": 0.0,
                    "expected_slippage": 0.0,
                    "expected_fee": 0.0,
                    "expected_fill_price": 0.0,
                    "liquidity_score": 0.0,
                    "routing_reason": "duplicate_prevention",
                }
        self._recent_routes[dedup_key] = now

        # Cleanup old dedup entries
        cutoff = now - self._dedup_window * 2
        self._recent_routes = {
            k: v for k, v in self._recent_routes.items() if v > cutoff
        }

        # Collect venue data concurrently
        venue_scores = await self._score_all_venues(symbol, side, quantity, price)

        if not venue_scores:
            self._stats.failed_routes += 1
            logger.error("[router] No venues available for {} {} {}", symbol, side, quantity)
            return {
                "exchange": "none",
                "score": 0.0,
                "expected_slippage": 0.0,
                "expected_fee": 0.0,
                "expected_fill_price": 0.0,
                "liquidity_score": 0.0,
                "routing_reason": "no_venues_available",
            }

        # Sort by score descending
        venue_scores.sort(key=lambda v: v.total_score, reverse=True)

        # Select best venue with failover
        selected = await self._select_with_failover(venue_scores)

        if not selected:
            self._stats.failed_routes += 1
            logger.error("[router] All venues failed for {} {} {}", symbol, side, quantity)
            return {
                "exchange": "none",
                "score": 0.0,
                "expected_slippage": 0.0,
                "expected_fee": 0.0,
                "expected_fill_price": 0.0,
                "liquidity_score": 0.0,
                "routing_reason": "all_venues_failed",
            }

        # Build routing result
        latency_ms = (time.monotonic() - t0) * 1000
        routing_reason = self._build_routing_reason(selected, venue_scores)

        result = RoutingResult(
            exchange=selected.exchange,
            score=selected.total_score,
            expected_slippage=selected.expected_slippage_bps,
            expected_fee=selected.expected_fee,
            expected_fill_price=selected.expected_fill_price,
            liquidity_score=selected.liquidity_score,
            routing_reason=routing_reason,
            all_scores=venue_scores,
            selected_venue=selected,
            timestamp=time.time(),
            latency_ms=latency_ms,
            symbol=symbol,
            side=side,
            quantity=quantity,
        )

        # Update statistics
        self._stats.successful_routes += 1
        self._stats._score_sum += selected.total_score
        self._stats._latency_sum += latency_ms
        self._stats.avg_score = self._stats._score_sum / self._stats.successful_routes
        self._stats.avg_latency_ms = self._stats._latency_sum / self._stats.successful_routes
        self._stats.total_execution_cost += selected.expected_fee
        self._stats.venue_distribution[selected.exchange] = (
            self._stats.venue_distribution.get(selected.exchange, 0) + 1
        )
        self._stats.routing_reasons[routing_reason] = (
            self._stats.routing_reasons.get(routing_reason, 0) + 1
        )

        # Update best exchange
        if self._stats.venue_distribution:
            self._stats.best_exchange = max(
                self._stats.venue_distribution,
                key=self._stats.venue_distribution.get,  # type: ignore
            )

        # Store in history
        self._routing_history.append(result)
        if len(self._routing_history) > self._max_history:
            self._routing_history.pop(0)

        # Audit logging
        if self._audit:
            await self._log_routing_decision(result)

        logger.info(
            "[router] Routed {} {} {} → {} (score={:.4f}, slippage={:.2f}bps, fee=${:.6f}, reason={})",
            side, symbol, quantity,
            selected.exchange, selected.total_score,
            selected.expected_slippage_bps, selected.expected_fee,
            routing_reason,
        )

        return result.to_dict()

    # ── Venue Scoring ────────────────────────────────────────────

    async def _score_all_venues(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float = 0.0,
    ) -> List[VenueScore]:
        """Collect data from all venues and compute scores concurrently."""
        tasks = []
        for name, exchange in self._exchanges.items():
            if exchange.is_connected:
                tasks.append(
                    self._score_venue(name, exchange, symbol, side, quantity, price)
                )

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)

        scores = []
        for result in results:
            if isinstance(result, VenueScore) and result.is_healthy:
                scores.append(result)
            elif isinstance(result, Exception):
                logger.warning("[router] Venue scoring exception: {}", result)

        return scores

    async def _score_venue(
        self,
        name: str,
        exchange: BaseExchange,
        symbol: str,
        side: str,
        quantity: float,
        price: float = 0.0,
    ) -> VenueScore:
        """Score a single venue by collecting all metrics."""
        try:
            # Collect data concurrently
            orderbook_task = exchange.get_orderbook(symbol, depth=20)
            funding_task = exchange.get_funding_rate(symbol)

            orderbook, funding_rate = await asyncio.gather(
                orderbook_task, funding_task, return_exceptions=True,
            )

            # Handle exceptions
            if isinstance(orderbook, Exception):
                logger.warning("[router] {} orderbook failed: {}", name, orderbook)
                return VenueScore(exchange=name, is_healthy=False, failure_reason=str(orderbook))
            if isinstance(funding_rate, Exception):
                funding_rate = 0.0

            orderbook: OrderbookSnapshot = orderbook  # type: ignore
            # Extract float from FundingInfo if needed
            if hasattr(funding_rate, 'funding_rate'):
                funding_rate: float = funding_rate.funding_rate  # type: ignore
            else:
                funding_rate: float = float(funding_rate) if funding_rate else 0.0  # type: ignore

            # Get exchange properties
            maker_fee = exchange.get_maker_fee()
            taker_fee = exchange.get_taker_fee()
            avg_latency = exchange.get_latency_ms()

            # ── Compute individual scores ──

            # 1. Liquidity score (depth available)
            depth_usd = orderbook.total_depth
            liquidity_score = min(depth_usd / max(quantity * orderbook.mid_price, 1), 2.0) / 2.0

            # 2. Spread score (tighter = better, normalize to bps)
            spread_bps = orderbook.spread_bps
            spread_score = max(0, 1.0 - spread_bps / 50.0)  # 50bps = worst

            # 3. Latency score (lower = better)
            latency_score = max(0, 1.0 - avg_latency / 1000.0)  # 1000ms = worst

            # 4. Fee score (lower = better)
            avg_fee_rate = (maker_fee + taker_fee) / 2
            fee_score = max(0, 1.0 - avg_fee_rate / 0.001)  # 0.1% = worst

            # 5. Slippage estimate
            slippage_bps = self._estimate_slippage(orderbook, side, quantity)
            slippage_score = max(0, 1.0 - slippage_bps / self._config.max_slippage_bps)

            # Apply funding penalty if enabled
            funding_penalty = 0.0
            if self._config.enable_funding_penalty:
                funding_penalty = abs(funding_rate) * self._config.funding_penalty_weight

            # ── Weighted total score ──
            w = self._config.weights
            total_score = (
                w.liquidity * liquidity_score +
                w.spread * spread_score +
                w.latency * latency_score +
                w.fee * fee_score +
                w.slippage * slippage_score -
                funding_penalty
            )

            # Clamp to [0, 1]
            total_score = max(0.0, min(1.0, total_score))

            # Expected fill price
            mid = orderbook.mid_price
            if side == "BUY":
                expected_fill = mid * (1 + slippage_bps / 10_000)
            else:
                expected_fill = mid * (1 - slippage_bps / 10_000)

            # Expected fee in USD
            expected_fee_usd = quantity * expected_fill * taker_fee

            return VenueScore(
                exchange=name,
                total_score=total_score,
                liquidity_score=liquidity_score,
                spread_score=spread_score,
                latency_score=latency_score,
                fee_score=fee_score,
                slippage_score=slippage_score,
                expected_slippage_bps=slippage_bps,
                expected_fee=expected_fee_usd,
                expected_fill_price=expected_fill,
                funding_rate=funding_rate,
                spread_bps=spread_bps,
                avg_latency_ms=avg_latency,
                orderbook_depth_usd=depth_usd,
                is_healthy=True,
            )

        except Exception as exc:
            logger.error("[router] {} scoring failed: {}", name, exc)
            return VenueScore(
                exchange=name,
                is_healthy=False,
                failure_reason=str(exc),
            )

    def _estimate_slippage(
        self,
        orderbook: OrderbookSnapshot,
        side: str,
        quantity: float,
    ) -> float:
        """
        Estimate slippage by walking the orderbook.

        Simulates filling the order against the orderbook
        and measures price impact in basis points.
        """
        if not orderbook.bids or not orderbook.asks:
            return self._config.max_slippage_bps

        levels = orderbook.asks if side == "BUY" else orderbook.bids
        if not levels:
            return self._config.max_slippage_bps

        mid_price = orderbook.mid_price
        if mid_price <= 0:
            return self._config.max_slippage_bps

        remaining = quantity
        total_cost = 0.0

        for level in levels:
            if remaining <= 0:
                break

            fill_qty = min(remaining, level.quantity)
            total_cost += fill_qty * level.price
            remaining -= fill_qty

        if remaining > 0:
            # Not enough liquidity — extrapolate slippage
            total_cost += remaining * levels[-1].price * 1.001  # 0.1% penalty

        avg_fill = total_cost / quantity
        slippage_bps = abs(avg_fill - mid_price) / mid_price * 10_000

        return slippage_bps

    # ── Failover Logic ───────────────────────────────────────────

    async def _select_with_failover(
        self,
        ranked_venues: List[VenueScore],
    ) -> Optional[VenueScore]:
        """
        Select the best venue with automatic failover.

        Tries venues in order of score. If a venue is unhealthy
        or fails the health check, tries the next one.
        """
        failover_count = 0

        for venue in ranked_venues:
            exchange = self._exchanges.get(venue.exchange)
            if not exchange:
                continue

            if not exchange.is_healthy():
                failover_count += 1
                logger.warning(
                    "[router] Venue {} unhealthy (failures={}), trying next",
                    venue.exchange,
                    exchange.get_health().consecutive_failures,
                )
                continue

            if failover_count > 0:
                self._stats.failover_count += failover_count

            return venue

        return None

    # ── Route Explanation ─────────────────────────────────────────

    def _build_routing_reason(
        self,
        selected: VenueScore,
        all_scores: List[VenueScore],
    ) -> str:
        """Generate human-readable explanation for the routing decision."""
        reasons = []

        # Why this venue won
        if selected.liquidity_score > 0.7:
            reasons.append(f"high_liquidity({selected.orderbook_depth_usd:.0f}USD)")
        if selected.spread_score > 0.8:
            reasons.append(f"tight_spread({selected.spread_bps:.2f}bps)")
        if selected.latency_score > 0.8:
            reasons.append(f"low_latency({selected.avg_latency_ms:.0f}ms)")
        if selected.fee_score > 0.8:
            reasons.append(f"low_fee({selected.expected_fee:.6f}USD)")
        if selected.slippage_score > 0.8:
            reasons.append(f"low_slippage({selected.expected_slippage_bps:.2f}bps)")

        if not reasons:
            reasons.append("best_overall_score")

        # Compare to second best
        if len(all_scores) > 1:
            second = all_scores[1]
            margin = selected.total_score - second.total_score
            reasons.append(f"beat_{second.exchange}_by_{margin:.4f}")

        return "|".join(reasons)

    # ── Audit Logging ────────────────────────────────────────────

    async def _log_routing_decision(self, result: RoutingResult) -> None:
        """Log routing decision to audit system."""
        if not self._audit:
            return

        try:
            from execution.execution_audit import AuditEventType
            await self._audit.record(
                AuditEventType.ORDER_SUBMITTED,
                "smart_order_router",
                "routing", f"{result.symbol}:{result.side}",
                "INFO",
                f"Route: {result.side} {result.symbol} → {result.exchange} "
                f"(score={result.score:.4f}, reason={result.routing_reason})",
                result.to_dict(),
            )
        except Exception as exc:
            logger.debug("[router] Audit log failed: {}", exc)

    # ── Router Stats ─────────────────────────────────────────────

    def router_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive router statistics.

        Returns:
            best_exchange, execution_cost, routing_reason,
            venue distribution, and all metrics
        """
        # Determine routing reason from most common
        top_reason = ""
        if self._stats.routing_reasons:
            top_reason = max(
                self._stats.routing_reasons,
                key=self._stats.routing_reasons.get,  # type: ignore
            )

        stats = self._stats.to_dict()
        stats["routing_reason"] = top_reason

        # Add per-venue health
        venue_health = {}
        for name, exchange in self._exchanges.items():
            health = exchange.get_health()
            venue_health[name] = {
                "is_healthy": health.is_healthy,
                "avg_latency_ms": round(health.avg_latency_ms, 2),
                "error_rate": round(health.error_rate, 4),
                "consecutive_failures": health.consecutive_failures,
            }
        stats["venue_health"] = venue_health

        # Recent routing history summary
        if self._routing_history:
            recent = self._routing_history[-10:]
            stats["recent_routes"] = [
                {
                    "exchange": r.exchange,
                    "score": round(r.score, 4),
                    "symbol": r.symbol,
                    "side": r.side,
                    "reason": r.routing_reason,
                }
                for r in recent
            ]

        return stats

    # ── Venue Management ─────────────────────────────────────────

    def get_venue_rankings(self) -> List[Dict[str, Any]]:
        """Get current venue rankings based on historical performance."""
        rankings = []
        for name, exchange in self._exchanges.items():
            health = exchange.get_health()
            count = self._stats.venue_distribution.get(name, 0)
            total = self._stats.successful_routes or 1
            rankings.append({
                "exchange": name,
                "selection_rate": round(count / total, 4),
                "total_selections": count,
                "is_healthy": health.is_healthy,
                "avg_latency_ms": round(health.avg_latency_ms, 2),
                "error_rate": round(health.error_rate, 4),
            })
        rankings.sort(key=lambda r: r["selection_rate"], reverse=True)
        return rankings

    def get_routing_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get recent routing decisions."""
        recent = self._routing_history[-limit:]
        return [r.to_dict() for r in reversed(recent)]

    def reset_stats(self) -> None:
        """Reset all routing statistics."""
        self._stats = RouterStats()
        self._routing_history.clear()
        self._recent_routes.clear()
        logger.info("[router] Statistics reset")
