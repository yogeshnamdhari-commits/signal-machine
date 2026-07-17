"""
Quiet Market Filter — FIX #4: Block trades during low-volatility / quiet conditions.

Based on forensic audit of 1,436 completed trades:
  - Quiet market loss: -$8,982
  - RANGE regime PF: 0.53, Expectancy: -$6.10
  - Low volatility environments produce choppy, mean-reverting price action
    that destroys trend-following and breakout signals.

This filter operates INDEPENDENTLY of the regime detector:
  - Regime detector classifies the regime type (range, compression, etc.)
  - Quiet Market Filter adds a VOLATILITY-BASED gate that blocks trades
    when the market environment is objectively quiet, regardless of regime label.

Detection criteria (ANY two trigger a block):
  1. ATR percentile < 25th (volatility in bottom quartile)
  2. BB bandwidth percentile < 30th (squeeze / compression)
  3. Volume ratio < 0.7 (below-average participation)
  4. EMA bias magnitude < 0.003 (no directional momentum)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from loguru import logger


@dataclass
class QuietMarketVerdict:
    """Result of quiet market evaluation."""
    is_quiet: bool
    score: float              # 0-100 (higher = quieter)
    reason: str
    atr_percentile: float
    bb_bandwidth_percentile: float
    volume_ratio: float
    ema_bias: float
    components_triggered: int  # How many of 4 criteria fired


class QuietMarketFilter:
    """
    FIX #4: Quiet Market Filter.
    
    Blocks trades when market is in low-volatility, range-bound state.
    Based on forensic audit: quiet markets produce PF=0.40, PnL=-$8,982.
    """
    
    # ── Configuration (from forensic audit) ──
    # Thresholds: any 2 of 4 firing = quiet market
    ATR_PERCENTILE_THRESHOLD = 25.0      # Bottom 25% = quiet
    BB_BANDWIDTH_PERCENTILE_THRESHOLD = 30.0  # Bottom 30% = squeeze
    VOLUME_RATIO_THRESHOLD = 0.7         # Below 0.7 = low participation
    EMA_BIAS_THRESHOLD = 0.003           # Near zero = no momentum
    
    # Minimum components to trigger block
    MIN_COMPONENTS_TO_BLOCK = 3
    
    # Quiet market score: how quiet is it? (0=active, 100=dead)
    def __init__(self) -> None:
        self._blocked_count = 0
        self._allowed_count = 0
    
    def evaluate(
        self,
        regime_data: Optional[Dict] = None,
        timeframes: Optional[Dict] = None,
    ) -> QuietMarketVerdict:
        """
        Evaluate whether the market environment is too quiet to trade.
        
        Uses 5m timeframe data as primary (most representative of trade duration).
        Falls back to composite regime data if TF data unavailable.
        
        Parameters
        ----------
        regime_data : dict
            From regime.get_regime() — has volatility, trend_strength, volume_profile, ema_bias
        timeframes : dict
            From regime_data["timeframes"] — per-TF breakdown with atr_pct, bb_bandwidth, vol_ratio, ema_bias
        
        Returns
        -------
        QuietMarketVerdict
        """
        components_triggered = 0
        reasons = []
        
        # Default scores (assume active market if no data)
        atr_pctile = 50.0
        bb_bw_pctile = 50.0
        vol_ratio = 1.0
        ema_bias = 0.01  # Assume some momentum
        
        # ── Extract from 5m timeframe (primary for trade duration) ──
        tf_5m = None
        if timeframes and "5m" in timeframes:
            tf_5m = timeframes["5m"]
        elif regime_data and regime_data.get("timeframes"):
            tf_5m = regime_data["timeframes"].get("5m")
        
        if tf_5m:
            # ATR % — lower = quieter
            atr_val = tf_5m.get("atr_pct", 0)
            # Map ATR% to rough percentile: BTC ATR~1.5% avg, <0.8% = quiet
            if atr_val < 0.5:
                atr_pctile = 10.0
            elif atr_val < 0.8:
                atr_pctile = 20.0
            elif atr_val < 1.2:
                atr_pctile = 35.0
            elif atr_val < 2.0:
                atr_pctile = 55.0
            else:
                atr_pctile = 80.0
            
            # BB bandwidth — lower = tighter squeeze
            bb_bw = tf_5m.get("bb_bandwidth", 0)
            # Map to percentile: <0.01 = very tight, >0.04 = wide
            if bb_bw < 0.005:
                bb_bw_pctile = 10.0
            elif bb_bw < 0.01:
                bb_bw_pctile = 25.0
            elif bb_bw < 0.02:
                bb_bw_pctile = 50.0
            elif bb_bw < 0.04:
                bb_bw_pctile = 70.0
            else:
                bb_bw_pctile = 85.0
            
            # Volume ratio
            vol_ratio = tf_5m.get("vol_ratio", 1.0)
            
            # EMA bias (absolute value)
            ema_bias = abs(tf_5m.get("ema_bias", 0))
        
        elif regime_data:
            # Fallback to composite data
            atr_val = regime_data.get("volatility", 1.0)
            if atr_val < 0.5:
                atr_pctile = 10.0
            elif atr_val < 0.8:
                atr_pctile = 20.0
            elif atr_val < 1.2:
                atr_pctile = 35.0
            else:
                atr_pctile = 60.0
            
            vol_ratio = regime_data.get("volume_profile", 1.0)
            ema_bias = abs(regime_data.get("ema_bias", 0.01))
            # BB bandwidth not available in composite
            bb_bw_pctile = 50.0  # Neutral assumption
        
        # ── Evaluate each component ──
        
        # 1. ATR percentile < 25 → quiet
        if atr_pctile < self.ATR_PERCENTILE_THRESHOLD:
            components_triggered += 1
            reasons.append(f"ATR={atr_pctile:.0f}pctl<{self.ATR_PERCENTILE_THRESHOLD:.0f}")
        
        # 2. BB bandwidth percentile < 30 → squeeze
        if bb_bw_pctile < self.BB_BANDWIDTH_PERCENTILE_THRESHOLD:
            components_triggered += 1
            reasons.append(f"BB_BW={bb_bw_pctile:.0f}pctl<{self.BB_BANDWIDTH_PERCENTILE_THRESHOLD:.0f}")
        
        # 3. Volume ratio < 0.7 → low participation
        if vol_ratio < self.VOLUME_RATIO_THRESHOLD:
            components_triggered += 1
            reasons.append(f"VolRatio={vol_ratio:.2f}<{self.VOLUME_RATIO_THRESHOLD}")
        
        # 4. EMA bias < 0.003 → no momentum
        if ema_bias < self.EMA_BIAS_THRESHOLD:
            components_triggered += 1
            reasons.append(f"EMABias={ema_bias:.4f}<{self.EMA_BIAS_THRESHOLD}")
        
        # ── Compute quiet score (0-100, higher = quieter) ──
        # Weighted: ATR(30%) + BB(25%) + Vol(25%) + EMA(20%)
        atr_score = max(0, 100 - atr_pctile * 2)  # Low ATR → high quiet score
        bb_score = max(0, 100 - bb_bw_pctile * 2)  # Low BB → high quiet score
        vol_score = max(0, (1 - vol_ratio) * 150)  # Low vol → high quiet score
        ema_score = max(0, (0.005 - ema_bias) * 20000)  # Low bias → high quiet score
        
        quiet_score = (atr_score * 0.30 + bb_score * 0.25 + 
                       vol_score * 0.25 + ema_score * 0.20)
        quiet_score = max(0, min(100, quiet_score))
        
        # ── Verdict ──
        is_quiet = components_triggered >= self.MIN_COMPONENTS_TO_BLOCK
        
        if is_quiet:
            self._blocked_count += 1
            verdict = QuietMarketVerdict(
                is_quiet=True,
                score=round(quiet_score, 1),
                reason=f"QUIET: {components_triggered}/4 components — {'; '.join(reasons)}",
                atr_percentile=round(atr_pctile, 1),
                bb_bandwidth_percentile=round(bb_bw_pctile, 1),
                volume_ratio=round(vol_ratio, 2),
                ema_bias=round(ema_bias, 4),
                components_triggered=components_triggered,
            )
            logger.info("🔇 QUIET MARKET BLOCK: {} (score={:.0f})", verdict.reason, quiet_score)
        else:
            self._allowed_count += 1
            verdict = QuietMarketVerdict(
                is_quiet=False,
                score=round(quiet_score, 1),
                reason=f"ACTIVE: {components_triggered}/4 components — market has sufficient volatility",
                atr_percentile=round(atr_pctile, 1),
                bb_bandwidth_percentile=round(bb_bw_pctile, 1),
                volume_ratio=round(vol_ratio, 2),
                ema_bias=round(ema_bias, 4),
                components_triggered=components_triggered,
            )
        
        return verdict
    
    def get_stats(self) -> Dict:
        """Get filter statistics."""
        total = self._allowed_count + self._blocked_count
        return {
            "allowed": self._allowed_count,
            "blocked": self._blocked_count,
            "total": total,
            "block_rate": self._blocked_count / total * 100 if total > 0 else 0,
        }
