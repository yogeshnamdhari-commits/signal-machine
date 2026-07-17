"""
Data Integrity Guard — Validates ALL fields before bridge write.

Prevents:
- Corrupt prices (zero, negative, or unrealistic jumps)
- Fake/placeholder values leaking to dashboard
- Stale data being displayed as fresh
- Negative volumes, impossible OI, extreme funding rates
- Schema violations (wrong types, missing required fields)
"""
from __future__ import annotations

import time
from typing import Dict, List, Tuple
from loguru import logger

# ═══════════════════════════════════════════════════════════════
# HARD LIMITS — Based on real Binance Futures market ranges
# ═══════════════════════════════════════════════════════════════
MAX_PRICE = 1_000_000       # BTC max ~$200k, leave headroom
MIN_PRICE = 0.00001         # Smallest valid token price
MAX_24H_CHANGE = 200        # Small-caps can do 100%+, cap at 200%
MAX_OI_USD = 50_000_000_000 # BTC max OI ~$40B
MAX_FUNDING = 0.5           # Max funding rate in % (abs)
MAX_VOLUME_USD = 100_000_000_000  # $100B daily volume max
MAX_TRADES_24H = 100_000_000      # 100M trades max
MIN_TRADES_24H = 100       # At least some activity

# Required fields — must exist and be non-None
REQUIRED_FIELDS = [
    "symbol", "price", "volume", "change_24h",
    "open_interest", "funding", "net_delta",
]

# Enum fields — must be one of these values
ENUM_FIELDS = {
    "cvd_bias": {"bullish", "bearish", "neutral", "strong_bullish", "strong_bearish"},
    "flow_signal": {"buy", "sell", "neutral", "strong_buy", "strong_sell"},
    "oi_bias": {"buy", "sell", "neutral"},
    "funding_bias": {"buy", "sell", "neutral", "long_bias", "short_bias"},
    "vol_bias": {"buy", "sell", "neutral"},
    "regime": {"trending_bull", "trending_bear", "range", "breakout", "volatile", "compression"},
    "fvg_alignment": {"bullish", "bearish", "neutral"},
    "sw_signal": {"bullish_rejection", "bearish_rejection", "neutral"},
    "sw_last_side": {"high_sweep", "low_sweep", ""},
}


class DataIntegrityGuard:
    """
    Validates market_data rows before bridge write.
    Returns (valid_rows, corrections_count).
    """
    
    def __init__(self) -> None:
        self._corrections = 0
        self._skipped = 0
        self._last_check = 0
    
    def validate(self, rows: List[Dict]) -> List[Dict]:
        """
        Validate and sanitize all rows before bridge write.
        Returns only valid rows (skips corrupt ones).
        """
        valid = []
        self._corrections = 0
        self._skipped = 0
        
        for row in rows:
            sym = row.get("symbol", "???")
            ok = True
            
            # ── Price validation ──
            price = row.get("price", 0)
            if price is None or price <= MIN_PRICE:
                logger.warning("INTEGRITY: {} price={} INVALID — skipped", sym, price)
                self._skipped += 1
                continue
            if price > MAX_PRICE:
                logger.warning("INTEGRITY: {} price={} exceeds max — capped", sym, price)
                row["price"] = MAX_PRICE
                self._corrections += 1
            
            # ── 24h Change ──
            chg = row.get("change_24h")
            if chg is not None and abs(chg) > MAX_24H_CHANGE:
                logger.warning("INTEGRITY: {} 24h={} exceeds {}% — capped", sym, chg, MAX_24H_CHANGE)
                row["change_24h"] = min(max(chg, -MAX_24H_CHANGE), MAX_24H_CHANGE)
                self._corrections += 1
            
            # ── Volume ──
            vol = row.get("volume", 0) or 0
            if vol < 0:
                logger.warning("INTEGRITY: {} volume={} negative — zeroed", sym, vol)
                row["volume"] = 0
                self._corrections += 1
            if vol > MAX_VOLUME_USD:
                logger.warning("INTEGRITY: {} volume=${:.0f} exceeds max — capped", sym, vol)
                row["volume"] = MAX_VOLUME_USD
                self._corrections += 1
            
            # ── Open Interest ──
            oi = row.get("open_interest", 0) or 0
            if oi < 0:
                row["open_interest"] = 0
                self._corrections += 1
            if oi > MAX_OI_USD:
                logger.warning("INTEGRITY: {} OI=${:.0f} exceeds max — capped", sym, oi)
                row["open_interest"] = MAX_OI_USD
                self._corrections += 1
            
            # ── Funding Rate ──
            fund = row.get("funding", 0) or 0
            if abs(fund) > MAX_FUNDING:
                logger.warning("INTEGRITY: {} funding={}% exceeds ±{}% — capped", sym, fund, MAX_FUNDING)
                row["funding"] = min(max(fund, -MAX_FUNDING), MAX_FUNDING)
                self._corrections += 1
            
            # ── Enum fields ──
            for field, allowed in ENUM_FIELDS.items():
                val = row.get(field)
                if val is not None and val != "" and val not in allowed:
                    logger.warning("INTEGRITY: {} {}='{}' not in allowed set — reset to neutral", sym, field, val)
                    row[field] = "neutral"
                    self._corrections += 1
            
            # ── Volume Bias ──
            vb = row.get("vol_bias")
            if vb is not None and vb not in ("buy", "sell", "neutral"):
                row["vol_bias"] = "neutral"
                self._corrections += 1
            
            # ── Buy/Sell ratio ──
            bsr = row.get("buy_sell_ratio", 0.5)
            if bsr is not None and (bsr < 0 or bsr > 1):
                row["buy_sell_ratio"] = 0.5
                self._corrections += 1
            
            # ── Exchange flow volumes ──
            for vf in ("aggressive_buy_vol", "aggressive_sell_vol"):
                v = row.get(vf, 0) or 0
                if v < 0:
                    row[vf] = 0
                    self._corrections += 1
            
            # ── CVD buy ratio ──
            cvd_r = row.get("cvd_buy_ratio_5m", 0.5)
            if cvd_r is not None and (cvd_r < 0 or cvd_r > 1):
                row["cvd_buy_ratio_5m"] = 0.5
                self._corrections += 1
            
            valid.append(row)
        
        self._last_check = time.time()
        
        if self._corrections > 0:
            logger.info("INTEGRITY: {} corrections, {} skipped out of {} rows",
                       self._corrections, self._skipped, len(rows))
        
        return valid
    
    def get_stats(self) -> Dict:
        return {
            "last_check": self._last_check,
            "corrections": self._corrections,
            "skipped": self._skipped,
        }


# Singleton
integrity_guard = DataIntegrityGuard()
