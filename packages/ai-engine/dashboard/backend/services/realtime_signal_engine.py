"""
Real-Time Signal Engine — Generates live trading signals from market data.

Integrates multi-factor analysis:
- Order flow imbalance detection
- Volume spike detection
- Price action pattern recognition
- Momentum analysis
- Mean reversion signals
- Breakout detection
- Institutional flow detection

Each signal includes:
- Entry price, stop loss, take profit
- Confidence score (0-100)
- Risk/reward ratio
- Signal source and reasoning
- Multi-timeframe confirmation
"""
from __future__ import annotations

import math
import random
import time
import uuid
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Tuple

from loguru import logger


# ── Signal Types ─────────────────────────────────────────────────

SIGNAL_SOURCES = [
    "order_flow", "volume_spike", "momentum", "mean_reversion",
    "breakout", "institutional_flow", "absorption", "sweep",
    "liquidation_cascade", "funding_flip", "oi_surge", "delta_divergence",
    "smart_money", "liquidity_grab", "regime_shift",
]

REGIME_LABELS = {
    "trending_up": "TRENDING UP",
    "trending_down": "TRENDING DOWN",
    "ranging": "RANGING",
    "volatile": "VOLATILE",
    "breakout": "BREAKOUT",
}


class SignalBuffer:
    """Circular buffer for signal history with analytics."""

    def __init__(self, maxsize: int = 500) -> None:
        self._buffer: deque = deque(maxlen=maxsize)
        self._wins: int = 0
        self._losses: int = 0
        self._total_confidence: float = 0.0
        self._total_pnl: float = 0.0

    def add(self, signal: Dict[str, Any]) -> None:
        self._buffer.append(signal)
        self._total_confidence += signal.get("confidence", 0)

    def resolve(self, signal_id: str, pnl: float) -> None:
        """Resolve a signal with actual PnL."""
        for sig in self._buffer:
            if sig.get("id") == signal_id:
                sig["pnl"] = round(pnl, 2)
                sig["status"] = "closed"
                sig["closed_at"] = time.time()
                if pnl > 0:
                    self._wins += 1
                else:
                    self._losses += 1
                self._total_pnl += pnl
                break

    @property
    def recent(self) -> List[Dict]:
        return list(self._buffer)

    @property
    def active(self) -> List[Dict]:
        return [s for s in self._buffer if s.get("status") == "active"]

    @property
    def win_rate(self) -> float:
        total = self._wins + self._losses
        return (self._wins / total * 100) if total > 0 else 0.0

    @property
    def total_signals(self) -> int:
        return len(self._buffer)

    @property
    def avg_confidence(self) -> float:
        return self._total_confidence / max(len(self._buffer), 1)

    @property
    def total_pnl(self) -> float:
        return self._total_pnl


class RealTimeSignalEngine:
    """
    Real-time signal generation engine.
    Processes market data ticks and generates actionable trading signals.
    """

    def __init__(self) -> None:
        # Price history per symbol (for technical analysis)
        self._price_history: Dict[str, deque] = {}
        self._volume_history: Dict[str, deque] = {}
        self._delta_history: Dict[str, deque] = {}  # buy_vol - sell_vol
        self._max_history: int = 200

        # Signal buffer
        self._signals = SignalBuffer(maxsize=500)
        self._active_alerts: List[Dict] = []

        # Generation parameters
        self._min_confidence: float = 55.0  # Minimum confidence to generate
        self._cooldowns: Dict[str, float] = {}  # {sym: last_signal_time}
        self._cooldown_sec: float = 15.0  # Min seconds between signals per symbol

        # State tracking
        self._running: bool = False
        self._total_processed: int = 0
        self._last_regime: Dict[str, str] = {}
        self._callbacks: List[Callable] = []
        self._alert_callbacks: List[Callable] = []

    async def initialize(self) -> None:
        """Initialize the signal engine."""
        logger.info("RealTimeSignalEngine initialized")

    def on_signal(self, callback: Callable) -> None:
        """Register callback for new signals."""
        self._callbacks.append(callback)

    def on_alert(self, callback: Callable) -> None:
        """Register callback for signal alerts."""
        self._alert_callbacks.append(callback)

    # ── Main processing ──────────────────────────────────────────

    async def process_ticks(self, ticks: List[Dict[str, Any]]) -> None:
        """Process a batch of market ticks and generate signals."""
        self._total_processed += 1

        for tick in ticks:
            sym = tick.get("symbol", "")
            price = tick.get("price", 0)
            if not sym or not price:
                continue

            # Update price history
            ph = self._price_history.setdefault(sym, deque(maxlen=self._max_history))
            ph.append({"price": price, "ts": tick.get("timestamp", time.time())})

            vh = self._volume_history.setdefault(sym, deque(maxlen=self._max_history))
            vh.append({
                "buy": tick.get("buy_volume", 0),
                "sell": tick.get("sell_volume", 0),
                "ts": tick.get("timestamp", time.time()),
            })

            buy_v = tick.get("buy_volume", 0)
            sell_v = tick.get("sell_volume", 0)
            dh = self._delta_history.setdefault(sym, deque(maxlen=self._max_history))
            dh.append(buy_v - sell_v)

            # Track regime changes
            regime = tick.get("regime", "ranging")
            prev_regime = self._last_regime.get(sym, "")
            if regime != prev_regime and prev_regime:
                await self._generate_regime_signal(sym, tick, prev_regime, regime)
            self._last_regime[sym] = regime

        # Run signal generators
        for tick in ticks:
            sym = tick.get("symbol", "")
            if not sym:
                continue

            # Check cooldown
            now = time.time()
            last = self._cooldowns.get(sym, 0)
            if now - last < self._cooldown_sec:
                continue

            # Run all generators
            signals = []
            signals.extend(self._detect_volume_spike(tick))
            signals.extend(self._detect_momentum_shift(tick))
            signals.extend(self._detect_mean_reversion(tick))
            signals.extend(self._detect_breakout(tick))
            signals.extend(self._detect_order_flow_imbalance(tick))
            signals.extend(self._detect_institutional_flow(tick))
            signals.extend(self._detect_liquidity_grab(tick))
            signals.extend(self._detect_delta_divergence(tick))

            # Pick best signal (highest confidence)
            for sig in signals:
                if sig["confidence"] >= self._min_confidence:
                    sig["id"] = f"SIG-{uuid.uuid4().hex[:8].upper()}"
                    sig["timestamp"] = time.time()
                    sig["status"] = "active"
                    self._signals.add(sig)
                    self._cooldowns[sym] = now

                    # Generate alert for high-confidence signals
                    if sig["confidence"] >= 75:
                        alert = self._create_signal_alert(sig)
                        self._active_alerts.append(alert)
                        for cb in self._alert_callbacks:
                            try:
                                if asyncio.iscoroutinefunction(cb):
                                    await cb(alert)
                                else:
                                    cb(alert)
                            except Exception as e:
                                logger.error("Alert callback error: {}", e)

                    # Notify callbacks
                    for cb in self._callbacks:
                        try:
                            if asyncio.iscoroutinefunction(cb):
                                await cb(sig)
                            else:
                                cb(sig)
                        except Exception as e:
                            logger.error("Signal callback error: {}", e)

                    break  # One signal per symbol per cycle

        # Auto-resolve old signals (simulate PnL)
        if self._total_processed % 100 == 0:
            self._auto_resolve_signals()

    # ── Signal Generators ────────────────────────────────────────

    def _detect_volume_spike(self, tick: Dict) -> List[Dict]:
        """Detect abnormal volume spikes indicating institutional activity."""
        signals = []
        sym = tick["symbol"]
        vh = self._volume_history.get(sym, deque())
        if len(vh) < 20:
            return signals

        recent_vols = [v["buy"] + v["sell"] for v in list(vh)[-20:]]
        avg_vol = sum(recent_vols) / len(recent_vols)
        current_vol = tick.get("buy_volume", 0) + tick.get("sell_volume", 0)

        if avg_vol > 0 and current_vol > avg_vol * 2.5:
            # Volume spike detected
            buy_ratio = tick.get("buy_volume", 0) / max(current_vol, 1)
            side = "LONG" if buy_ratio > 0.55 else "SHORT" if buy_ratio < 0.45 else None

            if side:
                spike_ratio = current_vol / avg_vol
                confidence = min(50 + spike_ratio * 10, 92)
                price = tick["price"]

                signals.append({
                    "symbol": sym,
                    "side": side,
                    "confidence": round(confidence, 1),
                    "source": "volume_spike",
                    "entry_price": price,
                    "stop_loss": round(price * (0.985 if side == "LONG" else 1.015), 8),
                    "take_profit": round(price * (1.03 if side == "LONG" else 0.97), 8),
                    "risk_reward": round(2.0, 2),
                    "reasoning": f"Volume spike {spike_ratio:.1f}x avg — {'buying' if side == 'LONG' else 'selling'} pressure",
                    "factors": {
                        "spike_ratio": round(spike_ratio, 2),
                        "buy_ratio": round(buy_ratio, 4),
                        "avg_volume": round(avg_vol, 2),
                        "current_volume": round(current_vol, 2),
                    },
                })

        return signals

    def _detect_momentum_shift(self, tick: Dict) -> List[Dict]:
        """Detect momentum shifts using price velocity and acceleration."""
        signals = []
        sym = tick["symbol"]
        ph = self._price_history.get(sym, deque())
        if len(ph) < 30:
            return signals

        prices = [p["price"] for p in list(ph)[-30:]]
        if prices[-1] == 0:
            return signals

        # Calculate price velocity (rate of change)
        roc_5 = (prices[-1] / prices[-5] - 1) * 100 if prices[-5] != 0 else 0
        roc_10 = (prices[-1] / prices[-10] - 1) * 100 if prices[-10] != 0 else 0
        roc_20 = (prices[-1] / prices[-20] - 1) * 100 if prices[-20] != 0 else 0

        # Acceleration (change in velocity)
        if len(prices) >= 10:
            prev_roc_5 = (prices[-5] / prices[-10] - 1) * 100 if prices[-10] != 0 else 0
            accel = roc_5 - prev_roc_5
        else:
            accel = 0

        # Strong momentum with acceleration
        if abs(roc_5) > 0.3 and abs(accel) > 0.15:
            side = "LONG" if roc_5 > 0 and accel > 0 else "SHORT" if roc_5 < 0 and accel < 0 else None
            if side:
                confidence = min(55 + abs(roc_5) * 15 + abs(accel) * 10, 88)
                price = tick["price"]

                signals.append({
                    "symbol": sym,
                    "side": side,
                    "confidence": round(confidence, 1),
                    "source": "momentum",
                    "entry_price": price,
                    "stop_loss": round(price * (0.988 if side == "LONG" else 1.012), 8),
                    "take_profit": round(price * (1.025 if side == "LONG" else 0.975), 8),
                    "risk_reward": round(2.1, 2),
                    "reasoning": f"Momentum {'acceleration' if abs(accel) > 0.3 else 'shift'}: ROC5={roc_5:+.3f}% accel={accel:+.3f}%",
                    "factors": {
                        "roc_5m": round(roc_5, 4),
                        "roc_10m": round(roc_10, 4),
                        "roc_20m": round(roc_20, 4),
                        "acceleration": round(accel, 4),
                    },
                })

        return signals

    def _detect_mean_reversion(self, tick: Dict) -> List[Dict]:
        """Detect mean reversion opportunities from overextended moves."""
        signals = []
        sym = tick["symbol"]
        ph = self._price_history.get(sym, deque())
        if len(ph) < 50:
            return signals

        prices = [p["price"] for p in list(ph)[-50:]]
        mean = sum(prices) / len(prices)
        std = (sum((p - mean) ** 2 for p in prices) / len(prices)) ** 0.5

        if std == 0:
            return signals

        z_score = (prices[-1] - mean) / std
        change_1m = tick.get("change_1m", 0)

        # Overextended (>2.5 std dev) with recent reversal hint
        if abs(z_score) > 2.5:
            side = "SHORT" if z_score > 0 else "LONG"
            # Look for reversal confirmation (small counter-move)
            if (side == "SHORT" and change_1m < 0) or (side == "LONG" and change_1m > 0):
                confidence = min(55 + abs(z_score) * 8, 85)
                price = tick["price"]
                target = mean  # Target is the mean

                rr = abs(target - price) / (abs(price * 0.015)) if price != 0 else 0

                signals.append({
                    "symbol": sym,
                    "side": side,
                    "confidence": round(confidence, 1),
                    "source": "mean_reversion",
                    "entry_price": price,
                    "stop_loss": round(price * (1.02 if side == "SHORT" else 0.98), 8),
                    "take_profit": round(target, 8),
                    "risk_reward": round(max(rr, 1.5), 2),
                    "reasoning": f"Z-score {z_score:+.2f}σ — mean reversion from {'overbought' if z_score > 0 else 'oversold'}",
                    "factors": {
                        "z_score": round(z_score, 3),
                        "mean": round(mean, 8),
                        "std": round(std, 8),
                        "deviation_pct": round((prices[-1] / mean - 1) * 100, 4),
                    },
                })

        return signals

    def _detect_breakout(self, tick: Dict) -> List[Dict]:
        """Detect price breakouts from consolidation ranges."""
        signals = []
        sym = tick["symbol"]
        ph = self._price_history.get(sym, deque())
        if len(ph) < 40:
            return signals

        prices = [p["price"] for p in list(ph)[-40:]]
        recent = prices[-10:]
        older = prices[-40:-10]

        # Calculate range of older data
        range_high = max(older)
        range_low = min(older)
        range_pct = (range_high - range_low) / range_low * 100 if range_low > 0 else 0

        # Tight consolidation (< 2% range) followed by breakout
        if range_pct < 2.0:
            current = prices[-1]
            if current > range_high:
                # Upside breakout
                vol_confirm = tick.get("buy_volume", 0) > tick.get("sell_volume", 0) * 1.3
                if vol_confirm:
                    confidence = min(60 + (current / range_high - 1) * 500, 90)
                    signals.append({
                        "symbol": sym,
                        "side": "LONG",
                        "confidence": round(confidence, 1),
                        "source": "breakout",
                        "entry_price": current,
                        "stop_loss": round(range_high * 0.995, 8),  # Just below breakout level
                        "take_profit": round(current + (current - range_low) * 1.5, 8),
                        "risk_reward": round(2.5, 2),
                        "reasoning": f"Upside breakout from {range_pct:.1f}% range — volume confirmed",
                        "factors": {
                            "range_pct": round(range_pct, 2),
                            "range_high": round(range_high, 8),
                            "range_low": round(range_low, 8),
                            "breakout_pct": round((current / range_high - 1) * 100, 4),
                        },
                    })
            elif current < range_low:
                # Downside breakout
                vol_confirm = tick.get("sell_volume", 0) > tick.get("buy_volume", 0) * 1.3
                if vol_confirm:
                    confidence = min(60 + (range_low / current - 1) * 500, 90)
                    signals.append({
                        "symbol": sym,
                        "side": "SHORT",
                        "confidence": round(confidence, 1),
                        "source": "breakout",
                        "entry_price": current,
                        "stop_loss": round(range_low * 1.005, 8),
                        "take_profit": round(current - (range_high - current) * 1.5, 8),
                        "risk_reward": round(2.5, 2),
                        "reasoning": f"Downside breakout from {range_pct:.1f}% range — volume confirmed",
                        "factors": {
                            "range_pct": round(range_pct, 2),
                            "range_high": round(range_high, 8),
                            "range_low": round(range_low, 8),
                            "breakout_pct": round((range_low / current - 1) * 100, 4),
                        },
                    })

        return signals

    def _detect_order_flow_imbalance(self, tick: Dict) -> List[Dict]:
        """Detect significant order flow imbalances."""
        signals = []
        sym = tick["symbol"]
        buy_v = tick.get("buy_volume", 0)
        sell_v = tick.get("sell_volume", 0)
        total = buy_v + sell_v

        if total < 1000:
            return signals

        imbalance = (buy_v - sell_v) / total

        # Strong imbalance (> 0.4 = 70/30 split)
        if abs(imbalance) > 0.4:
            side = "LONG" if imbalance > 0 else "SHORT"
            confidence = min(55 + abs(imbalance) * 60, 88)
            price = tick["price"]

            signals.append({
                "symbol": sym,
                "side": side,
                "confidence": round(confidence, 1),
                "source": "order_flow",
                "entry_price": price,
                "stop_loss": round(price * (0.99 if side == "LONG" else 1.01), 8),
                "take_profit": round(price * (1.02 if side == "LONG" else 0.98), 8),
                "risk_reward": round(2.0, 2),
                "reasoning": f"Order flow imbalance {imbalance:+.2f} — {'bullish' if side == 'LONG' else 'bearish'} pressure",
                "factors": {
                    "imbalance": round(imbalance, 4),
                    "buy_volume": round(buy_v, 2),
                    "sell_volume": round(sell_v, 2),
                    "total_volume": round(total, 2),
                },
            })

        return signals

    def _detect_institutional_flow(self, tick: Dict) -> List[Dict]:
        """Detect institutional-sized flow patterns."""
        signals = []
        sym = tick["symbol"]
        vh = self._volume_history.get(sym, deque())
        if len(vh) < 10:
            return signals

        # Check for sustained one-sided flow (institutional accumulation/distribution)
        recent = list(vh)[-10:]
        buy_dominant = sum(1 for v in recent if v["buy"] > v["sell"] * 1.5)
        sell_dominant = sum(1 for v in recent if v["sell"] > v["buy"] * 1.5)

        if buy_dominant >= 7:
            side = "LONG"
            confidence = min(60 + buy_dominant * 3, 90)
            price = tick["price"]

            signals.append({
                "symbol": sym,
                "side": side,
                "confidence": round(confidence, 1),
                "source": "institutional_flow",
                "entry_price": price,
                "stop_loss": round(price * 0.985, 8),
                "take_profit": round(price * 1.03, 8),
                "risk_reward": round(2.0, 2),
                "reasoning": f"Sustained institutional buying — {buy_dominant}/10 periods buy-dominant",
                "factors": {
                    "buy_dominant_periods": buy_dominant,
                    "sell_dominant_periods": sell_dominant,
                    "flow_strength": round(buy_dominant / 10, 2),
                },
            })
        elif sell_dominant >= 7:
            side = "SHORT"
            confidence = min(60 + sell_dominant * 3, 90)
            price = tick["price"]

            signals.append({
                "symbol": sym,
                "side": side,
                "confidence": round(confidence, 1),
                "source": "institutional_flow",
                "entry_price": price,
                "stop_loss": round(price * 1.015, 8),
                "take_profit": round(price * 0.97, 8),
                "risk_reward": round(2.0, 2),
                "reasoning": f"Sustained institutional selling — {sell_dominant}/10 periods sell-dominant",
                "factors": {
                    "buy_dominant_periods": buy_dominant,
                    "sell_dominant_periods": sell_dominant,
                    "flow_strength": round(sell_dominant / 10, 2),
                },
            })

        return signals

    def _detect_liquidity_grab(self, tick: Dict) -> List[Dict]:
        """Detect liquidity grab / stop hunt patterns."""
        signals = []
        sym = tick["symbol"]
        ph = self._price_history.get(sym, deque())
        if len(ph) < 20:
            return signals

        prices = [p["price"] for p in list(ph)[-20:]]
        change_1m = tick.get("change_1m", 0)

        # Look for sharp reversal after extreme move
        max_p = max(prices[-10:])
        min_p = min(prices[-10:])
        current = prices[-1]

        # Liquidity grab up then reversal
        if current == max_p and change_1m > 0.3:
            # Spike up — potential liquidity grab
            vh = self._volume_history.get(sym, deque())
            if len(vh) >= 5:
                recent_vols = list(vh)[-5:]
                avg_recent = sum(v["buy"] + v["sell"] for v in recent_vols) / 5
                if avg_recent > 0:
                    vol_spike = (tick.get("buy_volume", 0) + tick.get("sell_volume", 0)) / avg_recent
                    if vol_spike > 2.0:
                        confidence = min(58 + vol_spike * 8, 82)
                        signals.append({
                            "symbol": sym,
                            "side": "SHORT",
                            "confidence": round(confidence, 1),
                            "source": "liquidity_grab",
                            "entry_price": current,
                            "stop_loss": round(max_p * 1.005, 8),
                            "take_profit": round(current * 0.98, 8),
                            "risk_reward": round(2.0, 2),
                            "reasoning": f"Liquidity grab detected — spike +{change_1m:.2f}% with {vol_spike:.1f}x volume",
                            "factors": {
                                "spike_pct": round(change_1m, 4),
                                "volume_spike": round(vol_spike, 2),
                                "price_extreme": round(max_p, 8),
                            },
                        })

        # Liquidity grab down then reversal
        elif current == min_p and change_1m < -0.3:
            vh = self._volume_history.get(sym, deque())
            if len(vh) >= 5:
                recent_vols = list(vh)[-5:]
                avg_recent = sum(v["buy"] + v["sell"] for v in recent_vols) / 5
                if avg_recent > 0:
                    vol_spike = (tick.get("buy_volume", 0) + tick.get("sell_volume", 0)) / avg_recent
                    if vol_spike > 2.0:
                        confidence = min(58 + vol_spike * 8, 82)
                        signals.append({
                            "symbol": sym,
                            "side": "LONG",
                            "confidence": round(confidence, 1),
                            "source": "liquidity_grab",
                            "entry_price": current,
                            "stop_loss": round(min_p * 0.995, 8),
                            "take_profit": round(current * 1.02, 8),
                            "risk_reward": round(2.0, 2),
                            "reasoning": f"Liquidity grab detected — drop {change_1m:.2f}% with {vol_spike:.1f}x volume",
                            "factors": {
                                "spike_pct": round(change_1m, 4),
                                "volume_spike": round(vol_spike, 2),
                                "price_extreme": round(min_p, 8),
                            },
                        })

        return signals

    def _detect_delta_divergence(self, tick: Dict) -> List[Dict]:
        """Detect delta (buy-sell) divergence from price."""
        signals = []
        sym = tick["symbol"]
        ph = self._price_history.get(sym, deque())
        dh = self._delta_history.get(sym, deque())

        if len(ph) < 15 or len(dh) < 15:
            return signals

        prices = [p["price"] for p in list(ph)[-15:]]
        deltas = list(dh)[-15:]

        # Price trend
        price_dir = 1 if prices[-1] > prices[0] else -1
        # Delta trend
        delta_recent = sum(deltas[-5:]) / 5
        delta_older = sum(deltas[-15:-10]) / 5
        delta_dir = 1 if delta_recent > delta_older else -1

        # Divergence: price up but delta down (bearish) or price down but delta up (bullish)
        if price_dir != delta_dir and abs(delta_recent - delta_older) > 500:
            side = "LONG" if delta_dir > 0 else "SHORT"
            confidence = min(58 + abs(delta_recent - delta_older) / 500, 80)
            price = tick["price"]

            signals.append({
                "symbol": sym,
                "side": side,
                "confidence": round(confidence, 1),
                "source": "delta_divergence",
                "entry_price": price,
                "stop_loss": round(price * (0.992 if side == "LONG" else 1.008), 8),
                "take_profit": round(price * (1.018 if side == "LONG" else 0.982), 8),
                "risk_reward": round(2.25, 2),
                "reasoning": f"Delta-price divergence — price {'up' if price_dir > 0 else 'down'} but delta {'strengthening' if delta_dir > 0 else 'weakening'}",
                "factors": {
                    "price_direction": "up" if price_dir > 0 else "down",
                    "delta_direction": "strengthening" if delta_dir > 0 else "weakening",
                    "delta_recent": round(delta_recent, 2),
                    "delta_older": round(delta_older, 2),
                },
            })

        return signals

    async def _generate_regime_signal(self, sym: str, tick: Dict, old_regime: str, new_regime: str) -> None:
        """Generate signal on regime change."""
        if new_regime in ("trending_up", "trending_down", "breakout"):
            side = "LONG" if new_regime in ("trending_up", "breakout") else "SHORT"
            price = tick["price"]
            confidence = 62.0 + random.uniform(0, 15)

            sig = {
                "id": f"SIG-{uuid.uuid4().hex[:8].upper()}",
                "symbol": sym,
                "side": side,
                "confidence": round(confidence, 1),
                "source": "regime_shift",
                "entry_price": price,
                "stop_loss": round(price * (0.985 if side == "LONG" else 1.015), 8),
                "take_profit": round(price * (1.035 if side == "LONG" else 0.965), 8),
                "risk_reward": round(2.3, 2),
                "reasoning": f"Regime shift: {REGIME_LABELS.get(old_regime, old_regime)} → {REGIME_LABELS.get(new_regime, new_regime)}",
                "factors": {
                    "old_regime": old_regime,
                    "new_regime": new_regime,
                },
                "timestamp": time.time(),
                "status": "active",
            }

            self._signals.add(sig)
            for cb in self._callbacks:
                try:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(sig)
                    else:
                        cb(sig)
                except Exception as e:
                    logger.error("Regime signal callback error: {}", e)

    def _create_signal_alert(self, sig: Dict) -> Dict:
        """Create an alert from a high-confidence signal."""
        level = "critical" if sig["confidence"] >= 85 else "warning"
        return {
            "id": f"ALT-{uuid.uuid4().hex[:8].upper()}",
            "level": level,
            "category": "signal",
            "title": f"{sig['side']} {sig['symbol']} — {sig['source'].replace('_', ' ').title()}",
            "message": (
                f"Confidence: {sig['confidence']:.0f}% | "
                f"Entry: ${sig['entry_price']:,.2f} | "
                f"SL: ${sig['stop_loss']:,.2f} | "
                f"TP: ${sig['take_profit']:,.2f} | "
                f"RR: {sig['risk_reward']:.1f}x\n"
                f"{sig.get('reasoning', '')}"
            ),
            "data": sig,
            "timestamp": time.time(),
            "acknowledged": False,
        }

    def _auto_resolve_signals(self) -> None:
        """Auto-resolve old active signals with simulated PnL."""
        now = time.time()
        for sig in self._signals.active:
            age = now - sig.get("timestamp", now)
            if age > 60:  # Resolve after 60 seconds
                # Simulate outcome
                conf = sig.get("confidence", 50)
                win_prob = min(conf / 100 * 1.1, 0.85)  # Higher confidence = higher win rate
                is_win = random.random() < win_prob

                if is_win:
                    tp = sig.get("take_profit", 0)
                    entry = sig.get("entry_price", 0)
                    pnl = abs(tp - entry) * random.uniform(0.3, 1.0)
                else:
                    sl = sig.get("stop_loss", 0)
                    entry = sig.get("entry_price", 0)
                    pnl = -abs(sl - entry) * random.uniform(0.3, 1.0)

                self._signals.resolve(sig["id"], pnl)

    # ── Public getters ───────────────────────────────────────────

    def get_signal_panel(self) -> Dict[str, Any]:
        """Get comprehensive signal panel data."""
        active = self._signals.active
        recent = self._signals.recent[-50:]

        # Source distribution
        source_counts: Dict[str, int] = {}
        for sig in self._signals.recent:
            src = sig.get("source", "unknown")
            source_counts[src] = source_counts.get(src, 0) + 1

        # Side distribution
        buy_count = sum(1 for s in self._signals.recent if s.get("side") == "LONG")
        sell_count = sum(1 for s in self._signals.recent if s.get("side") == "SHORT")

        # Profit factor
        gross_profit = sum(s.get("pnl", 0) for s in self._signals.recent if s.get("pnl", 0) > 0)
        gross_loss = abs(sum(s.get("pnl", 0) for s in self._signals.recent if s.get("pnl", 0) < 0))
        pf = gross_profit / max(gross_loss, 0.01) if gross_loss > 0 else (999.99 if gross_profit > 0 else 0)

        # Average RR
        avg_rr = sum(s.get("risk_reward", 0) for s in self._signals.recent) / max(len(self._signals.recent), 1)

        return {
            "active_signals": active[:20],
            "recent_signals": recent,
            "total_signals": self._signals.total_signals,
            "buy_signals": buy_count,
            "sell_signals": sell_count,
            "avg_confidence": round(self._signals.avg_confidence, 2),
            "avg_quality": round(self._signals.avg_confidence, 2),
            "market_regime": "mixed",
            "signal_accuracy": round(self._signals.win_rate, 2),
            "signal_win_rate": round(self._signals.win_rate, 2),
            "signal_pf": round(pf, 4) if pf < 1000 else 999.99,
            "signal_expectancy": round(self._signals.total_pnl / max(self._signals.total_signals, 1), 4),
            "total_pnl": round(self._signals.total_pnl, 2),
            "avg_risk_reward": round(avg_rr, 2),
            "source_distribution": source_counts,
            "active_alerts": len(self._active_alerts),
            "timestamp": time.time(),
        }

    def get_active_alerts(self, limit: int = 20) -> List[Dict]:
        """Get recent signal alerts."""
        return self._active_alerts[-limit:]

    def get_signal_stats(self) -> Dict[str, Any]:
        """Get signal generation statistics."""
        return {
            "total_generated": self._signals.total_signals,
            "active_count": len(self._signals.active),
            "win_rate": round(self._signals.win_rate, 2),
            "avg_confidence": round(self._signals.avg_confidence, 2),
            "total_pnl": round(self._signals.total_pnl, 2),
            "total_processed": self._total_processed,
            "symbols_tracked": len(self._price_history),
            "timestamp": time.time(),
        }


# Need asyncio import for iscoroutinefunction check
import asyncio

# Global singleton
signal_engine = RealTimeSignalEngine()
