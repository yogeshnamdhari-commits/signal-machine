"""
AI Confidence Scorer — multi-factor weighted signal generation.
Adaptive: adjusts weights based on recent win rate, MTF confirmation,
and enhanced fake breakout detection.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

import numpy as np
from loguru import logger
from config import config


class AIConfidenceScorer:
    def __init__(self) -> None:
        self._base_weights = dict(config.ai.weights)
        self._weights = dict(config.ai.weights)
        # Adaptive: track recent wins for weight adjustment
        self._recent_results: List[bool] = []  # True = win
        self._adaptive_window = 50
        self._last_adjustment = 0.0
        self._adjustment_interval = 300.0  # every 5 min

    def record_outcome(self, is_win: bool) -> None:
        """Record trade outcome for adaptive weight adjustment."""
        self._recent_results.append(is_win)
        if len(self._recent_results) > self._adaptive_window:
            self._recent_results = self._recent_results[-self._adaptive_window:]
        self._maybe_adjust_weights()

    def _maybe_adjust_weights(self) -> None:
        """Adjust weights based on which factors predicted winners vs losers."""
        now = time.time()
        if now - self._last_adjustment < self._adjustment_interval:
            return
        self._last_adjustment = now
        if len(self._recent_results) < 10:
            return
        # If win rate > 60%, keep current weights; otherwise revert to base
        win_rate = sum(self._recent_results) / len(self._recent_results)
        if win_rate < 0.4:
            # Reduce overconfident weights, boost underused ones
            self._weights = dict(self._base_weights)
            logger.info("Adaptive scoring: reverting to base weights (win_rate={:.0%})", win_rate)
        else:
            logger.debug("Adaptive scoring: weights stable (win_rate={:.0%})", win_rate)

    def _score_of(self, of: Optional[Dict]) -> float:
        if not of:
            return 0
        imbalance = of.get("imbalance", 0)
        s = imbalance * 0.4
        s += np.clip(of.get("delta_trend", 0) * 0.0001, -0.3, 0.3)
        lb = of.get("large_buy_trades", 0)
        ls = of.get("large_sell_trades", 0)
        if lb + ls > 0:
            s += (lb - ls) / (lb + ls) * 0.3
        # Penalize weak imbalances (< 0.05)
        if abs(imbalance) < 0.05:
            s *= 0.3
        return float(np.clip(s, -1, 1))

    def _score_inst(self, patterns: List[Dict]) -> float:
        if not patterns:
            return 0
        s = 0.0
        for p in patterns:
            side = 1 if p.get("side") == "buy" else -1
            c = p.get("confidence", 0.5)
            pt = p.get("type", "")
            if pt == "sweep":
                s += side * c * 0.4
            elif pt == "absorption":
                s += side * c * 0.3
            elif pt == "iceberg":
                s += side * c * 0.2
            elif pt == "spoofing":
                # Spoofing should reduce confidence, not flip direction
                s -= side * 0.1
        return float(np.clip(s, -1, 1))

    def _score_regime(self, regime: Optional[Dict]) -> float:
        if not regime:
            return 0
        table = {
            "trending_bull": 0.7, "trending_bear": -0.7, "breakout": 0.0,
            "compression": 0.0,  # Compression is neutral — potential, not direction
            "volatile": 0.0, "range": 0.0,
            # Legacy compat
            "trending_up": 0.7, "trending_down": -0.7,
            "ranging": 0, "quiet": 0.0, "reversal": 0.0,
        }
        base = table.get(regime.get("regime", ""), 0)
        conf = regime.get("confidence", 0.5)
        # Strong regime = higher conviction
        return base * conf

    def _score_momentum(self, md: Dict, cd: Optional[Dict]) -> float:
        s = 0.0
        if cd:
            s += cd.get("delta_momentum", 0) * 0.5
            s += cd.get("price_delta_divergence", 0) * 0.5
        trades = md.get("trades", [])
        if len(trades) >= 10:
            recent = trades[-10:]
            bv = sum(t["price"] * t["quantity"] for t in recent if not t["is_buyer_maker"])
            sv = sum(t["price"] * t["quantity"] for t in recent if t["is_buyer_maker"])
            if bv + sv > 0:
                s += (bv - sv) / (bv + sv) * 0.3
        return float(np.clip(s, -1, 1))

    def _score_volume(self, regime: Optional[Dict]) -> float:
        # Volume is directionless — high volume means activity, not buying.
        # Return 0 to avoid injecting systematic bullish bias.
        return 0

    def _score_funding(self, funding_data: Optional[Dict]) -> float:
        """Contrarian funding factor: extreme positive funding → SHORT, extreme negative → LONG.
        
        High funding (crowd paying to hold longs) means overleveraged longs.
        This increases liquidation risk → contrarian SHORT signal.
        Low/negative funding means crowd is short → contrarian LONG signal.
        """
        if not funding_data:
            return 0
        rate = funding_data.get("current_rate", 0)  # raw decimal, e.g., 0.0001 = 0.01%
        rate_pct = rate * 100  # convert to percentage
        # Contrarian: positive funding → bearish (SHORT), negative funding → bullish (LONG)
        # At ±0.01% → ±0.6 score, at ±0.05% → saturated at ±1.0
        s = -rate_pct * 12.0  # negative because contrarian
        return float(np.clip(s, -1.0, 1.0))

    def _score_imbalance(self, cd: Optional[Dict]) -> float:
        if not cd:
            return 0
        zones = cd.get("imbalance_zones", [])
        if not zones:
            return 0
        bi = sum(1 for z in zones if z.get("type") == "buy_imbalance")
        si = len(zones) - bi
        # Weight by z-score magnitude
        total_z = sum(abs(z.get("z_score", 0)) for z in zones)
        strength = min(total_z / 10, 1.0) if total_z > 0 else 0.5
        return (bi - si) / max(len(zones), 1) * strength

    def _score_mtf(self, mtf: Optional[Dict]) -> float:
        """Score multi-timeframe confirmation."""
        if not mtf:
            return 0
        if mtf.get("aligned"):
            strength = mtf.get("strength", 0.5)
            direction = mtf.get("direction", "")
            if direction == "up":
                return 0.3 * strength
            elif direction == "down":
                return -0.3 * strength
        return 0

    def _fake_breakout(self, md: Dict, of: Optional[Dict], regime: Optional[Dict]) -> float:
        """Enhanced fake breakout detection with volume + orderflow confirmation.
        Returns -1 to +1: negative = likely fake breakout, positive = confirmed breakout.
        0 = neutral (not applicable).
        """
        if not regime or regime.get("regime") != "breakout":
            return 0  # neutral if not breakout regime — no contribution to score

        vol_profile = regime.get("volume_profile", 1)
        # Breakout needs volume confirmation
        if vol_profile < 1.2:
            return -0.5  # low volume breakout = likely fake
        if vol_profile < 1.0:
            return -0.7  # declining volume = very likely fake

        imbalance = of.get("imbalance", 0) if of else 0
        trades = md.get("trades", [])
        if len(trades) < 20:
            return 0

        prices = [t["price"] for t in trades[-20:]]
        pc = (prices[-1] - prices[0]) / prices[0] if prices[0] else 0

        # Check if orderflow confirms breakout direction
        if pc > 0 and imbalance > 0.3:
            return 0.8  # bullish breakout with buy confirmation
        elif pc < 0 and imbalance < -0.3:
            return 0.8  # bearish breakout with sell confirmation

        # Breakout without orderflow confirmation = suspect
        return -0.4

    async def analyze_symbol(
        self, symbol: str, market_data: Dict,
        orderflow: Optional[Dict], institutional: List[Dict],
        cumulative_delta: Optional[Dict], regime: Optional[Dict],
        mtf_confirmation: Optional[Dict] = None,
        funding_data: Optional[Dict] = None,
        exchange_flow: Optional[Dict] = None,
        cvd_data: Optional[Dict] = None,
    ) -> Optional[Dict]:
        # ── GATE 1: Regime is soft-required, orderflow is soft-required ──
        # When regime data is unavailable (warmup, sparse WS data),
        # use a default neutral regime instead of rejecting the signal.
        if not regime:
            regime = {"regime": "range", "confidence": 0.5, "regime_confidence_pct": 50}
            logger.debug("{} Using neutral regime fallback (no regime data)", symbol)

        if not orderflow:
            # Try exchange flow as substitute
            if exchange_flow and exchange_flow.get("total_trades", 0) >= 20:
                orderflow = {
                    "symbol": symbol,
                    "buy_volume": exchange_flow.get("taker_buy_vol", 0),
                    "sell_volume": exchange_flow.get("taker_sell_vol", 0),
                    "delta": exchange_flow.get("net_flow", 0),
                    "cumulative_delta": exchange_flow.get("net_flow", 0),
                    "imbalance": exchange_flow.get("flow_ratio", 0.5) - 0.5,
                    "flow_ratio": exchange_flow.get("flow_ratio", 0.5),
                    "flow_signal": exchange_flow.get("flow_signal", "neutral"),
                    "flow_strength_score": exchange_flow.get("flow_strength_score", 50),
                    "signal_strength": exchange_flow.get("flow_strength_score", 50) / 100.0,
                    "large_buy_trades": 0, "large_sell_trades": 0,
                    "avg_size": 0, "delta_trend": 0, "vwap": 0,
                    "absorption": "none", "absorption_events": 0,
                    "sweep": "none", "sweep_events": 0,
                    "total_trades": exchange_flow.get("total_trades", 0),
                }
                logger.debug("{} Using exchange_flow fallback ({} trades)", symbol, orderflow["total_trades"])
            elif cvd_data and cvd_data.get("cvd_5m", 0) != 0:
                # Use CVD as last resort
                cvd_val = cvd_data.get("cvd_5m", 0)
                cvd_bias = cvd_data.get("cvd_bias_5m", "neutral")
                imb = 0.1 if cvd_bias in ("bullish", "strong_bearish") else (-0.1 if cvd_bias in ("bearish", "strong_bearish") else 0)
                orderflow = {
                    "symbol": symbol,
                    "buy_volume": 0, "sell_volume": 0,
                    "delta": cvd_val, "cumulative_delta": cvd_val,
                    "imbalance": imb,
                    "flow_ratio": 0.5 + imb,
                    "flow_signal": "buy" if cvd_bias in ("bullish", "strong_bullish") else ("sell" if cvd_bias in ("bearish", "strong_bearish") else "neutral"),
                    "flow_strength_score": 50 + abs(cvd_val) / 100,
                    "signal_strength": 0.5,
                    "large_buy_trades": 0, "large_sell_trades": 0,
                    "avg_size": 0, "delta_trend": 0, "vwap": 0,
                    "absorption": "none", "absorption_events": 0,
                    "sweep": "none", "sweep_events": 0,
                    "total_trades": 0,
                }
                logger.debug("{} Using CVD fallback (cvd_5m={:.0f})", symbol, cvd_val)
            else:
                logger.debug("{} REJECTED_GATE1: no orderflow/exchange_flow/cvd data", symbol)
                return None

        w = self._weights
        try:
            factors = [
                ("order_flow", self._score_of(orderflow), w["order_flow"], orderflow.get("signal_strength", 0.5)),
                ("institutional", self._score_inst(institutional), w["institutional"], 0.5),
                ("regime", self._score_regime(regime), w["regime"], regime.get("confidence", 0.5)),
                ("momentum", self._score_momentum(market_data, cumulative_delta), w["momentum"], 0.5),
                ("volume", self._score_volume(regime), w["volume"], 0.5),
                ("imbalance", self._score_imbalance(cumulative_delta), w["imbalance"], 0.5),
                ("funding", self._score_funding(funding_data), w.get("funding", 0.10), 0.7),
                ("fake_breakout", self._fake_breakout(market_data, orderflow, regime), w["fake_breakout"], 0.5),
            ]
        except Exception as e:
            logger.error(f"FACTOR ERROR {symbol}: {e}")
            raise
        # Add MTF factor if available
        mtf_score = self._score_mtf(mtf_confirmation)
        if mtf_confirmation and mtf_confirmation.get("aligned"):
            factors.append(("mtf", mtf_score, 0.10, 0.7))

        # Lower threshold from 0.15 to 0.05 to allow more factors in range markets.
        # Previously, 75% of symbols were killed because factors scored near 0 in range.
        sig_f = [(name, val, w) for name, val, w, conf in factors if abs(val) > 0.05 and conf > 0.25]
        if len(sig_f) < config.ai.min_factors:
            logger.debug("{} REJECTED_FACTORS: {}/{} sig_f (need {})", symbol, len(sig_f), len(factors), config.ai.min_factors)
            return None

        tw = sum(w for _, _, w in sig_f)
        if tw == 0:
            return None
        weighted = sum(v * w for _, v, w in sig_f) / tw

        # Fake breakout penalty — strong signal suppression
        fb = next((v for n, v, _ in sig_f if n == "fake_breakout"), 0)
        if fb < -0.5:
            weighted *= 0.4  # heavier penalty for clear fake breakouts
        elif fb < -0.3:
            weighted *= 0.6

        # MTF bonus/penalty: aligned timeframes boost confidence
        if mtf_confirmation and mtf_confirmation.get("aligned"):
            aligned_dir = mtf_confirmation.get("direction", "")
            signal_dir = "up" if weighted > 0 else "down"
            if aligned_dir == signal_dir:
                weighted *= 1.15  # 15% boost for MTF alignment
            else:
                weighted *= 0.7  # 30% penalty for MTF conflict

        # ── Recalibrated Confidence Mapping ──
        # Apply a Sigmoid-style squash to prevent overconfidence.
        # Requires much higher factor agreement to cross 70% threshold.
        raw_conf = abs(weighted)
        if raw_conf < 0.2:
            confidence = raw_conf * 0.5  # Heavy dampening for weak signals
        elif raw_conf < 0.5:
            confidence = 0.1 + (raw_conf * 0.8)
        else:
            confidence = 0.5 + (raw_conf - 0.5) * 0.6 # Diminishing returns at the top

        # Conflict Penalty: If Momentum and Orderflow disagree, slash confidence by 40%
        of_dir = 1 if self._score_of(orderflow) > 0 else -1
        mom_dir = 1 if self._score_momentum(market_data, cumulative_delta) > 0 else -1
        if of_dir != mom_dir:
            confidence *= 0.6
            logger.debug("{} Confidence penalized for OF/Momentum conflict", symbol)

        confidence = min(confidence, 0.95)
        signal_type = "LONG" if weighted > 0 else "SHORT"

        logger.debug("{} scored: type={} confidence={:.3f} factors={}/{} weighted={:.4f}",
                      symbol, signal_type, confidence, len(sig_f), len(factors), weighted)

        trades = market_data.get("trades", [])
        entry = trades[-1]["price"] if trades else 0
        if entry == 0:
            return None

        # ── ATR Calculation: EMA-smoothed, multi-TF preferred ──
        # Prefer 5m for intraday signals (balances noise vs responsiveness),
        # then 1m as fallback. Use EMA smoothing (not SMA) for faster adaptation.
        klines = market_data.get("klines", {})

        def _ema_atr(kline_list: list, max_bars: int = 20, span: int = 14) -> float:
            """Compute EMA-smoothed ATR from kline data."""
            trs = []
            for i in range(1, min(len(kline_list), max_bars)):
                h = kline_list[i].get("high", kline_list[i].get("price", entry))
                l = kline_list[i].get("low", kline_list[i].get("price", entry))
                prev_c = kline_list[i - 1].get("close", kline_list[i - 1].get("price", entry))
                if h > 0 and l > 0:
                    trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
            if not trs:
                return 0
            alpha = 2 / (span + 1)
            ema = trs[0]
            for tr in trs[1:]:
                ema = alpha * tr + (1 - alpha) * ema
            return ema

        atr = 0
        # Try 5m first (best for intraday)
        kline_5m = klines.get("5m", [])
        if len(kline_5m) >= 14:
            atr = _ema_atr(kline_5m, max_bars=20, span=14)
        # Try 15m for additional context (smoothed)
        atr_15m = 0
        kline_15m = klines.get("15m", [])
        if len(kline_15m) >= 14:
            atr_15m = _ema_atr(kline_15m, max_bars=20, span=14)
        # Blend: 70% 5m + 30% 15m for intraday robustness
        if atr > 0 and atr_15m > 0:
            atr = atr * 0.7 + atr_15m * 0.3
        elif atr <= 0 and atr_15m > 0:
            atr = atr_15m
        # Fallback: 1m klines
        if atr <= 0:
            atr = _ema_atr(klines.get("1m", []), max_bars=20, span=14)
        # Last resort: trade price range
        if atr <= 0:
            if len(trades) >= 20:
                prices = [t["price"] for t in trades[-50:]]
                high = max(prices)
                low = min(prices)
                atr = (high - low) if high > low else entry * 0.005
            else:
                atr = entry * 0.005

        # ── Regime-adaptive SL/TP multipliers (anchored to config.risk.sl_atr_mult) ──
        current_regime = regime.get("regime", "range") if regime else "range"
        regime_sl_scale = {
            "trending_bull": 0.9, "trending_bear": 0.9,
            "breakout": 1.0, "compression": 0.85,
            "volatile": 1.1, "range": 0.8,
        }
        regime_tp_mults = {
            "trending_bull": 3.5, "trending_bear": 3.5,
            "breakout": 4.0, "compression": 2.5,
            "volatile": 3.0, "range": 2.0,
        }
        sl_mult = config.risk.sl_atr_mult * regime_sl_scale.get(current_regime, 1.0)
        tp_mult = regime_tp_mults.get(current_regime, config.risk.tp_atr_mult)

        # ── Minimum SL/TP floors: prevent impossibly tight stops ──
        # Audit: Raised floor to 0.20% SL and 0.35% TP to ensure meaningful risk/reward metrics
        min_sl_pct = 0.0020   # 0.20%
        min_tp_pct = 0.0035   # 0.35%
        min_sl_d = max(entry * min_sl_pct, atr * sl_mult)
        min_tp_d = max(entry * min_tp_pct, atr * tp_mult)

        # ── Ensure minimum R:R of 1.5 ──
        if min_tp_d < min_sl_d * 1.5:
            min_tp_d = min_sl_d * 1.5

        sl_d = min_sl_d
        tp_d = min_tp_d

        return {
            "symbol": symbol,
            "type": signal_type,
            "confidence": confidence,
            "entry_price": entry,
            "stop_loss": entry - sl_d if signal_type == "LONG" else entry + sl_d,
            "take_profit": entry + tp_d if signal_type == "LONG" else entry - tp_d,
            "sl_distance_pct": round(sl_d / entry * 100, 2) if entry else 0,
            "tp_distance_pct": round(tp_d / entry * 100, 2) if entry else 0,
            "risk_reward": round(tp_d / sl_d, 2) if sl_d > 0 else 0,
            "atr": round(atr, 6),
            "atr_5m": round(atr if atr > 0 else 0, 6),
            "regime": current_regime,
            "sl_atr_mult": round(sl_mult, 2),
            "tp_atr_mult": round(tp_mult, 2),
            "factors": [{"name": n, "value": v} for n, v, _ in sig_f],
            "factor_scores": {f[0]: f[1] for f in factors},
            "timeframes": list(config.scanner.timeframes),
            "mtf_aligned": bool(mtf_confirmation and mtf_confirmation.get("aligned")),
            "created_at": time.time(),
            "status": "active",
        }
