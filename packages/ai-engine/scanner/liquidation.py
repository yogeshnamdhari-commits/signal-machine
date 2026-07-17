"""
Liquidation Analytics Engine — Enhanced module for liquidation cascade detection,
cluster analysis, heat zone mapping, sweep signals, and risk assessment.

Tracks:
- Long Liquidations / Short Liquidations (granular volume + count)
- Liquidation Clusters (price levels with concentrated liquidations)
- Liquidation Heat Zones (price ranges with risk scoring)
- Liquidation Sweep Signals (detect when price approaches cluster zones)
- Liquidation Risk: LOW / MEDIUM / HIGH (0-100 score)
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from loguru import logger


# ── Data Classes ──────────────────────────────────────────────

@dataclass
class LiqEvent:
    """Single liquidation event."""
    symbol: str
    side: str           # "long" or "short"
    price: float
    quantity: float
    value_usd: float
    timestamp: float
    is_cascade: bool = False


@dataclass
class LiqCluster:
    """A cluster of liquidations at a price level."""
    price_level: float
    total_volume: float = 0
    long_volume: float = 0
    short_volume: float = 0
    event_count: int = 0
    first_seen: float = 0
    last_seen: float = 0
    density: float = 0  # events per second


@dataclass
class LiqHeatZone:
    """A heat zone covering a price range."""
    zone_low: float = 0
    zone_high: float = 0
    total_risk: float = 0  # 0-100
    long_risk: float = 0
    short_risk: float = 0
    cluster_count: int = 0
    dominant_side: str = "neutral"


@dataclass
class LiqState:
    """Full state for a symbol's liquidation analytics."""
    symbol: str
    events: List[LiqEvent] = field(default_factory=list)
    clusters: Dict[float, LiqCluster] = field(default_factory=dict)
    heat_zones: List[LiqHeatZone] = field(default_factory=list)

    # Aggregate volumes
    long_liq_vol: float = 0
    short_liq_vol: float = 0
    net_liq: float = 0
    long_liq_count: int = 0
    short_liq_count: int = 0

    # Recent window volumes
    recent_long_vol: float = 0
    recent_short_vol: float = 0
    recent_long_count: int = 0
    recent_short_count: int = 0

    # Cascade
    cascade_active: bool = False
    cascade_side: str = ""
    cascade_intensity: float = 0

    # Sweep
    sweep_detected: bool = False
    sweep_direction: str = ""  # "up" = sweeping shorts, "down" = sweeping longs
    sweep_intensity: float = 0

    # Risk
    liq_risk: float = 0       # 0-100
    liq_risk_level: str = "low"  # low / medium / high


# ── Engine ────────────────────────────────────────────────────

class LiquidationEngine:
    """
    Enhanced liquidation analytics engine.

    Detection:
    - Unusual trade size spikes = forced liquidations
    - Long vs short liquidation tracking with counts
    - Cascade detection (multiple liqs in short window)
    - Cluster analysis (price levels with concentrated liqs)
    - Heat zone mapping (price ranges with risk scoring)
    - Sweep signals (price approaching cluster zones)
    - Risk assessment (Low / Medium / High)
    """

    def __init__(self) -> None:
        self._states: Dict[str, LiqState] = {}

        # Detection thresholds
        self._spike_threshold = 5.0      # 5x normal = liquidation (raised from 3)
        self._min_liq_value = 100_000    # Min USD value for standalone liq detection
        self._cascade_window = 30        # seconds for cascade detection
        self._cascade_min_events = 8     # min events for cascade
        self._recent_window = 120        # 2 min recent window
        self._cluster_price_tolerance = 0.002  # 0.2% price tolerance for clusters
        self._cluster_expiry = 600       # 10 min cluster expiry
        self._heat_zone_count = 5        # number of heat zones
        self._sweep_approach_pct = 0.005 # 0.5% approach = sweep signal
        self._max_events = 2000          # max events to retain

    async def initialize(self) -> None:
        logger.info("Liquidation analytics engine ready (clusters + heat zones + sweep + risk)")

    async def process_trade(self, symbol: str, trade: Dict, normal_volume: float = 0) -> None:
        """Process a trade and detect liquidation events."""
        st = self._states.setdefault(symbol, LiqState(symbol=symbol))

        price = trade.get("price", 0)
        qty = trade.get("quantity", 0)
        val = price * qty
        now = time.time()

        if val <= 0 or price <= 0:
            return

        # ── Detect liquidation from unusual trade size ──
        is_maker = trade.get("is_buyer_maker", False)
        is_liq = False

        if normal_volume > 0 and val / max(normal_volume, 1) > self._spike_threshold:
            is_liq = True
        elif normal_volume == 0 and val > self._min_liq_value * 500:
            # Without baseline, require extremely large trade ($50M+) to classify as liquidation
            # Lower thresholds cause regular large trades to be misclassified as liquidations
            is_liq = True

        if is_liq:
            # Maker selling = long liquidation (forced selling), Maker buying = short liquidation
            side = "long" if is_maker else "short"
            event = LiqEvent(
                symbol=symbol,
                side=side,
                price=price,
                quantity=qty,
                value_usd=val,
                timestamp=now,
            )

            # Check if this event is part of a cascade
            recent_events = [e for e in st.events if now - e.timestamp < self._cascade_window]
            if len(recent_events) >= self._cascade_min_events - 1:
                event.is_cascade = True

            st.events.append(event)

            # Trim old events
            if len(st.events) > self._max_events:
                st.events = st.events[-self._max_events // 2:]

            # Update aggregate volumes
            if side == "long":
                st.long_liq_vol += val
                st.long_liq_count += 1
                st.recent_long_vol += val
                st.recent_long_count += 1
            else:
                st.short_liq_vol += val
                st.short_liq_count += 1
                st.recent_short_vol += val
                st.recent_short_count += 1

            # Update clusters
            self._update_clusters(st, event)

        # ── Cascade detection ──
        self._detect_cascade(st, now)

        # ── Sweep detection ──
        self._detect_sweep(st, price, now)

        # ── Update heat zones ──
        self._compute_heat_zones(st)

        # ── Compute risk level ──
        self._compute_risk(st, now)

        # ── Reset periodic counters ──
        self._reset_recent_if_stale(st, now)

    def _update_clusters(self, st: LiqState, event: LiqEvent) -> None:
        """Update liquidation clusters with new event."""
        now = event.timestamp
        price = event.price

        # Find existing cluster within tolerance
        matched_level = None
        for level in list(st.clusters.keys()):
            if abs(level - price) / max(price, 1) < self._cluster_price_tolerance:
                matched_level = level
                break

        if matched_level is not None:
            cluster = st.clusters[matched_level]
            cluster.total_volume += event.value_usd
            cluster.event_count += 1
            cluster.last_seen = now
            if event.side == "long":
                cluster.long_volume += event.value_usd
            else:
                cluster.short_volume += event.value_usd
            # Recalculate density
            duration = max(cluster.last_seen - cluster.first_seen, 1)
            cluster.density = cluster.event_count / duration
        else:
            cluster = LiqCluster(
                price_level=price,
                total_volume=event.value_usd,
                long_volume=event.value_usd if event.side == "long" else 0,
                short_volume=event.value_usd if event.side == "short" else 0,
                event_count=1,
                first_seen=now,
                last_seen=now,
                density=1.0,
            )
            st.clusters[price] = cluster

        # Prune expired clusters
        expired = [lvl for lvl, c in st.clusters.items()
                   if now - c.last_seen > self._cluster_expiry]
        for lvl in expired:
            del st.clusters[lvl]

    def _detect_cascade(self, st: LiqState, now: float) -> None:
        """Detect liquidation cascades (multiple events in short window)."""
        recent = [e for e in st.events if now - e.timestamp < self._cascade_window]

        if len(recent) >= self._cascade_min_events:
            total_val = sum(e.value_usd for e in recent)
            long_count = sum(1 for e in recent if e.side == "long")
            short_count = len(recent) - long_count

            st.cascade_active = True

            # Intensity scales with volume relative to symbol's normal size
            # Use total event count to normalize — more events = more significant
            vol_per_event = total_val / len(recent) if recent else 0
            event_factor = min(len(recent) / 20, 1.0)  # 20 events = max
            vol_factor = min(vol_per_event / 500_000, 1.0)  # $500k/event = max

            if long_count > short_count * 2:
                st.cascade_side = "long_liquidation"
                st.cascade_intensity = min(event_factor * 0.5 + vol_factor * 0.5, 1.0)
            elif short_count > long_count * 2:
                st.cascade_side = "short_liquidation"
                st.cascade_intensity = min(event_factor * 0.5 + vol_factor * 0.5, 1.0)
            else:
                st.cascade_side = "mixed"
                st.cascade_intensity = min((event_factor * 0.5 + vol_factor * 0.5) * 0.7, 1.0)
        else:
            st.cascade_active = False
            st.cascade_side = ""
            st.cascade_intensity = 0

    def _detect_sweep(self, st: LiqState, current_price: float, now: float) -> None:
        """
        Detect liquidation sweep — when price moves toward a cluster zone,
        indicating potential for triggering liquidations.
        """
        st.sweep_detected = False
        st.sweep_direction = ""
        st.sweep_intensity = 0

        if not st.clusters or current_price <= 0:
            return

        # Find nearest cluster
        nearest_level = min(st.clusters.keys(), key=lambda x: abs(x - current_price))
        distance_pct = abs(nearest_level - current_price) / current_price

        if distance_pct < self._sweep_approach_pct:
            cluster = st.clusters[nearest_level]
            # Determine sweep direction
            if current_price > nearest_level:
                # Price above cluster = could sweep long liquidations below
                st.sweep_detected = True
                st.sweep_direction = "down"
                st.sweep_intensity = min(
                    cluster.total_volume / 50_000 * (1 - distance_pct / self._sweep_approach_pct),
                    1.0
                )
            else:
                # Price below cluster = could sweep short liquidations above
                st.sweep_detected = True
                st.sweep_direction = "up"
                st.sweep_intensity = min(
                    cluster.total_volume / 50_000 * (1 - distance_pct / self._sweep_approach_pct),
                    1.0
                )

    def _compute_heat_zones(self, st: LiqState) -> None:
        """Compute heat zones — price ranges with concentrated liquidation risk."""
        if not st.events:
            st.heat_zones = []
            return

        prices = [e.price for e in st.events[-200:]]  # last 200 events
        if len(prices) < 5:
            st.heat_zones = []
            return

        # Create price bins
        p_min = min(prices)
        p_max = max(prices)
        if p_max <= p_min:
            st.heat_zones = []
            return

        # Use 5 heat zones
        n_zones = min(self._heat_zone_count, len(set(prices)))
        if n_zones < 2:
            n_zones = 2
        bin_edges = np.linspace(p_min, p_max, n_zones + 1)

        zones = []
        now = time.time()

        for i in range(n_zones):
            zone_low = bin_edges[i]
            zone_high = bin_edges[i + 1]

            # Count events in this zone
            zone_events = [e for e in st.events[-200:]
                          if zone_low <= e.price < zone_high or
                          (i == n_zones - 1 and e.price == zone_high)]

            if not zone_events:
                zones.append(LiqHeatZone(
                    zone_low=round(float(zone_low), 6),
                    zone_high=round(float(zone_high), 6),
                    total_risk=0,
                    long_risk=0,
                    short_risk=0,
                    cluster_count=0,
                    dominant_side="neutral",
                ))
                continue

            # Risk scoring: volume + recency + event density
            total_vol = sum(e.value_usd for e in zone_events)
            long_vol = sum(e.value_usd for e in zone_events if e.side == "long")
            short_vol = total_vol - long_vol

            # Recency weight: more recent events = higher risk
            recency_weights = [1.0 / (1.0 + (now - e.timestamp) / 60) for e in zone_events]
            weighted_vol = sum(e.value_usd * w for e, w in zip(zone_events, recency_weights))

            # Normalize to 0-100
            max_vol = max(sum(e.value_usd for e in st.events[-200:]) / n_zones, 1)
            risk = min(weighted_vol / max_vol * 100, 100)

            long_risk = min(long_vol / max(total_vol, 1) * risk, 100)
            short_risk = min(short_vol / max(total_vol, 1) * risk, 100)

            # Count nearby clusters
            cluster_count = sum(1 for c in st.clusters.values()
                               if zone_low <= c.price_level < zone_high)

            dominant = "long" if long_vol > short_vol * 1.5 else (
                "short" if short_vol > long_vol * 1.5 else "neutral"
            )

            zones.append(LiqHeatZone(
                zone_low=round(float(zone_low), 6),
                zone_high=round(float(zone_high), 6),
                total_risk=round(risk, 1),
                long_risk=round(long_risk, 1),
                short_risk=round(short_risk, 1),
                cluster_count=cluster_count,
                dominant_side=dominant,
            ))

        st.heat_zones = zones

    def _compute_risk(self, st: LiqState, now: float) -> None:
        """
        Compute overall liquidation risk: 0-100.
        Components: cascade (30%) + cluster density (25%) + volume surge (25%) + sweep (20%)
        
        Includes data-quality dampening: when there are too few events,
        the risk score is dampened to prevent false positives on testnet
        or low-activity symbols.
        """
        score = 0.0

        # 1. Cascade risk (0-30)
        if st.cascade_active:
            score += st.cascade_intensity * 30

        # 2. Cluster density risk (0-25)
        # Require at least 3 events per cluster and higher volume for meaningful risk
        active_clusters = [c for c in st.clusters.values()
                          if now - c.last_seen < 300 and c.event_count >= 3]
        if active_clusters:
            max_density = max(c.density for c in active_clusters)
            avg_volume = float(np.mean([c.total_volume for c in active_clusters]))
            # Scale: density > 0.1 and avg_vol > 10k needed for meaningful risk
            cluster_risk = min((max_density * 5 + avg_volume / 50_000) / 2, 1.0)
            score += cluster_risk * 25

        # 3. Volume surge risk (0-25) — only with sufficient historical baseline
        recent_total = st.recent_long_vol + st.recent_short_vol
        total_events = st.long_liq_count + st.short_liq_count
        if recent_total > 0 and total_events >= 10:
            # Need at least 10 historical events for a meaningful baseline
            hist_total = st.long_liq_vol + st.short_liq_vol
            recent_count = st.recent_long_count + st.recent_short_count
            if recent_count > 0 and hist_total > 0:
                avg_per_event = hist_total / total_events
                surge_ratio = recent_total / max(recent_count * avg_per_event, 1)
                # 5x avg needed for max risk (was 3x)
                volume_risk = min(surge_ratio / 5, 1.0)
                score += volume_risk * 25

        # 4. Sweep risk (0-20)
        if st.sweep_detected:
            score += st.sweep_intensity * 20

        # ── Data-quality dampening ──
        # With <10 total events, dampen risk significantly (noisy data)
        # With <20 events, moderate dampening
        raw_score = score
        if total_events < 5:
            score *= 0.25  # Very few events → 75% dampening
        elif total_events < 10:
            score *= 0.50  # Few events → 50% dampening
        elif total_events < 20:
            score *= 0.75  # Some events → 25% dampening

        st.liq_risk = min(round(score, 1), 100)

        # Classify risk level
        if st.liq_risk >= 60:
            st.liq_risk_level = "high"
        elif st.liq_risk >= 30:
            st.liq_risk_level = "medium"
        else:
            st.liq_risk_level = "low"

    def _reset_recent_if_stale(self, st: LiqState, now: float) -> None:
        """Reset recent counters if window has expired."""
        if st.events:
            oldest_recent = min(e.timestamp for e in st.events[-50:]) if st.events else now
            if now - oldest_recent > self._recent_window * 2:
                # Recalculate from scratch
                window_events = [e for e in st.events if now - e.timestamp < self._recent_window]
                st.recent_long_vol = sum(e.value_usd for e in window_events if e.side == "long")
                st.recent_short_vol = sum(e.value_usd for e in window_events if e.side == "short")
                st.recent_long_count = sum(1 for e in window_events if e.side == "long")
                st.recent_short_count = sum(1 for e in window_events if e.side == "short")

    def get_analysis(self, symbol: str) -> Optional[Dict]:
        """Get full liquidation analysis for a symbol."""
        st = self._states.get(symbol)
        if not st:
            return None

        st.net_liq = st.long_liq_vol - st.short_liq_vol

        # Get top clusters (sorted by volume, top 5)
        top_clusters = sorted(st.clusters.values(), key=lambda c: c.total_volume, reverse=True)[:5]
        cluster_data = [
            {
                "price": round(c.price_level, 6),
                "volume": round(c.total_volume, 2),
                "long_vol": round(c.long_volume, 2),
                "short_vol": round(c.short_volume, 2),
                "events": c.event_count,
                "density": round(c.density, 3),
            }
            for c in top_clusters
        ]

        # Heat zone data
        heat_data = [
            {
                "low": z.zone_low,
                "high": z.zone_high,
                "risk": z.total_risk,
                "long_risk": z.long_risk,
                "short_risk": z.short_risk,
                "clusters": z.cluster_count,
                "dominant": z.dominant_side,
            }
            for z in st.heat_zones
        ]

        return {
            "symbol": symbol,

            # ── Aggregate volumes ──
            "long_liq_vol": round(st.long_liq_vol, 2),
            "short_liq_vol": round(st.short_liq_vol, 2),
            "net_liq": round(st.net_liq, 2),
            "long_liq_count": st.long_liq_count,
            "short_liq_count": st.short_liq_count,

            # ── Recent window ──
            "recent_long_vol": round(st.recent_long_vol, 2),
            "recent_short_vol": round(st.recent_short_vol, 2),
            "recent_long_count": st.recent_long_count,
            "recent_short_count": st.recent_short_count,

            # ── Cascade ──
            "cascade_active": st.cascade_active,
            "cascade_side": st.cascade_side,
            "cascade_intensity": round(st.cascade_intensity, 3),

            # ── Clusters ──
            "cluster_count": len(st.clusters),
            "clusters": cluster_data,

            # ── Heat zones ──
            "heat_zones": heat_data,
            "max_heat_risk": round(max((z.total_risk for z in st.heat_zones), default=0), 1),

            # ── Sweep ──
            "sweep_detected": st.sweep_detected,
            "sweep_direction": st.sweep_direction,
            "sweep_intensity": round(st.sweep_intensity, 3),

            # ── Risk ──
            "liq_risk": st.liq_risk,
            "liq_risk_level": st.liq_risk_level,

            # ── Backward compatibility (used by scoring engine) ──
            "signal": (
                "long_squeeze" if st.cascade_side == "long_liquidation" and st.cascade_intensity > 0.5 else
                "short_squeeze" if st.cascade_side == "short_liquidation" and st.cascade_intensity > 0.5 else
                "neutral"
            ),
        }
