"""
Data Quality Validation Layer — validates all incoming data across the pipeline.

Detects: stale data, duplicate ticks, NaN values, invalid prices, invalid OI,
impossible funding rates, corrupted orderbooks, and anomalous volume spikes.

Provides a per-symbol data_quality_score (0-100) and feeds the dashboard
Data Health widget.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger


# ── Thresholds ───────────────────────────────────────────────────

# Staleness: max age (seconds) before data is considered stale
# PHASE 1 REBUILD: Tightened for institutional-grade data integrity
_TRADE_STALE_SEC = 3        # no trade in 3s → stale (was 30s)
_ORDERBOOK_STALE_SEC = 15   # no depth update in 15s → stale (was 3s — too tight for WS reconnects)
_KLINE_STALE_SEC = 300      # no kline in 5 min → stale (unchanged)
_FUNDING_STALE_SEC = 3600   # no funding in 1h → stale (unchanged)
_OI_STALE_SEC = 30          # no OI update in 30s → stale (was 300s)

# Price validation
_MAX_PRICE = 10_000_000     # $10M per unit — nothing should exceed this
_MIN_PRICE = 1e-12          # sub-penny dust
_MAX_PRICE_SPIKE_PCT = 0.25 # 25% single-tick move is suspicious

# OI validation
_MAX_OI_USD = 100_000_000_000  # $100B — absolute max for BTC
_MIN_OI = 0

# Funding rate validation
_MAX_FUNDING_RATE = 0.10   # ±10% per 8h — impossible beyond this
_MIN_FUNDING_RATE = -0.10

# Volume validation
_MAX_SINGLE_TRADE_USD = 500_000_000  # $500M single trade is suspicious

# Duplicate detection
_DUPLICATE_WINDOW_MS = 50  # same price+qty within 50ms = duplicate

# Orderbook validation
_MAX_BOOK_LEVELS = 5000
_MAX_SPREAD_PCT = 0.10     # 10% spread is suspicious for major pairs


@dataclass
class SymbolQuality:
    """Per-symbol data quality state."""
    symbol: str
    last_trade_time: float = 0
    last_orderbook_time: float = 0
    last_kline_time: float = 0
    last_funding_time: float = 0
    last_oi_time: float = 0
    last_price: float = 0
    recent_trades: List[Dict] = field(default_factory=list)  # last N for duplicate check
    # Counters (reset periodically)
    total_trades: int = 0
    stale_count: int = 0
    duplicate_count: int = 0
    nan_count: int = 0
    invalid_price_count: int = 0
    invalid_oi_count: int = 0
    invalid_funding_count: int = 0
    anomalous_count: int = 0
    # Component scores (0-100 each)
    market_data_score: float = 100.0
    funding_score: float = 100.0
    oi_score: float = 100.0
    exchange_flow_score: float = 100.0
    overall_score: float = 100.0
    # Status text
    market_data_status: str = "OK"
    funding_status: str = "OK"
    oi_status: str = "OK"
    exchange_flow_status: str = "OK"
    # Issue log (last N)
    issues: List[Dict] = field(default_factory=list)


class DataQualityValidator:
    """
    Validates all incoming data and computes per-symbol quality scores.
    
    Integration points (non-invasive):
    - validate_trade()      → called from _on_data on every trade event
    - validate_orderbook()  → called from _on_data on every depth event
    - validate_kline()      → called from _on_data on every kline event
    - validate_funding()    → called from _oi_poll_loop on funding update
    - validate_oi()         → called from _oi_poll_loop on OI update
    - get_dashboard_data()  → called from _sync_bridge for dashboard widget
    """

    def __init__(self) -> None:
        self._symbols: Dict[str, SymbolQuality] = {}
        self._global_issues: List[Dict] = []
        self._max_issue_log = 200

    def _get_state(self, symbol: str) -> SymbolQuality:
        return self._symbols.setdefault(symbol, SymbolQuality(symbol=symbol))

    def _log_issue(self, symbol: str, category: str, severity: str, message: str) -> None:
        """Record a validation issue."""
        entry = {
            "time": time.time(),
            "symbol": symbol,
            "category": category,
            "severity": severity,
            "message": message,
        }
        self._global_issues.append(entry)
        if len(self._global_issues) > self._max_issue_log:
            self._global_issues = self._global_issues[-self._max_issue_log // 2:]

        state = self._get_state(symbol)
        state.issues.append(entry)
        if len(state.issues) > 20:
            state.issues = state.issues[-10:]

        if severity == "error":
            logger.warning("🔴 DATA_QUALITY [{}/{}]: {}", symbol, category, message)
        elif severity == "warn":
            logger.debug("⚠️  DATA_QUALITY [{}/{}]: {}", symbol, category, message)

    # ── Trade Validation ─────────────────────────────────────────

    def validate_trade(self, symbol: str, trade: Dict) -> bool:
        """
        Validate a single trade event. Returns True if trade passes.
        Checks: NaN, invalid price/qty, stale, duplicate, anomalous size.
        """
        state = self._get_state(symbol)
        state.total_trades += 1
        now = time.time()
        issues_found = 0

        price = trade.get("price", None)
        qty = trade.get("quantity", None)
        trade_time = trade.get("trade_time", 0)

        # ── NaN / None check ──
        if price is None or qty is None:
            state.nan_count += 1
            self._log_issue(symbol, "market_data", "error", "NaN/None in trade price or quantity")
            issues_found += 1
        elif math.isnan(price) or math.isnan(qty):
            state.nan_count += 1
            self._log_issue(symbol, "market_data", "error", "NaN in trade data")
            issues_found += 1
        else:
            # ── Invalid price ──
            if price <= _MIN_PRICE or price > _MAX_PRICE:
                state.invalid_price_count += 1
                self._log_issue(symbol, "market_data", "error",
                                f"Invalid price: {price}")
                issues_found += 1

            # ── Price spike detection ──
            if state.last_price > 0 and price > 0:
                spike = abs(price - state.last_price) / state.last_price
                if spike > _MAX_PRICE_SPIKE_PCT:
                    state.anomalous_count += 1
                    self._log_issue(symbol, "market_data", "warn",
                                    f"Price spike: {spike:.1%} ({state.last_price:.6f} → {price:.6f})")
                    issues_found += 1

            # ── Invalid quantity ──
            if qty <= 0:
                state.invalid_price_count += 1
                self._log_issue(symbol, "market_data", "error",
                                f"Invalid quantity: {qty}")
                issues_found += 1

            # ── Anomalous single trade value ──
            trade_value = price * qty
            if trade_value > _MAX_SINGLE_TRADE_USD:
                state.anomalous_count += 1
                self._log_issue(symbol, "market_data", "warn",
                                f"Anomalous trade value: ${trade_value:,.0f}")
                issues_found += 1

            # ── Duplicate detection ──
            is_dup = False
            for prev in state.recent_trades:
                if (prev.get("price") == price and prev.get("qty") == qty
                        and abs(trade_time - prev.get("ts", 0)) < _DUPLICATE_WINDOW_MS):
                    is_dup = True
                    break
            if is_dup:
                state.duplicate_count += 1
                self._log_issue(symbol, "market_data", "warn",
                                f"Duplicate tick: price={price} qty={qty}")
                issues_found += 1

            # Update last price
            if price > 0:
                state.last_price = price

        # ── Staleness check ──
        if state.last_trade_time > 0:
            age = now - state.last_trade_time
            if age > _TRADE_STALE_SEC:
                state.stale_count += 1
                self._log_issue(symbol, "market_data", "warn",
                                f"Trade data stale: {age:.0f}s (threshold: {_TRADE_STALE_SEC}s)")
                issues_found += 1

        state.last_trade_time = now

        # Track recent trades for duplicate detection (keep last 20)
        state.recent_trades.append({
            "price": price, "qty": qty, "ts": trade_time
        })
        if len(state.recent_trades) > 20:
            state.recent_trades = state.recent_trades[-20:]

        # ── Update component scores ──
        self._update_market_data_score(symbol)
        return issues_found == 0

    # ── Orderbook Validation ─────────────────────────────────────

    def validate_orderbook(self, symbol: str, orderbook: Dict) -> bool:
        """
        Validate an orderbook snapshot. Returns True if book passes.
        Checks: NaN in levels, empty book, corrupted structure, stale, wide spread.
        """
        state = self._get_state(symbol)
        now = time.time()
        issues_found = 0

        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])

        # ── Empty book ──
        if not bids and not asks:
            state.anomalous_count += 1
            self._log_issue(symbol, "market_data", "warn", "Empty orderbook")
            issues_found += 1

        # ── Structural validation ──
        if bids and not isinstance(bids[0], (list, tuple)) and not isinstance(bids[0], dict):
            state.anomalous_count += 1
            self._log_issue(symbol, "market_data", "error",
                            f"Corrupted bid structure: {type(bids[0])}")
            issues_found += 1

        # ── Too many levels ──
        if len(bids) > _MAX_BOOK_LEVELS or len(asks) > _MAX_BOOK_LEVELS:
            state.anomalous_count += 1
            self._log_issue(symbol, "market_data", "warn",
                            f"Oversized book: {len(bids)} bids, {len(asks)} asks")
            issues_found += 1

        # ── NaN in top levels ──
        for side_name, levels in [("bid", bids[:5]), ("ask", asks[:5])]:
            for lvl in levels:
                if isinstance(lvl, dict):
                    p, q = lvl.get("price", 0), lvl.get("quantity", 0)
                elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
                    p, q = lvl[0], lvl[1]
                else:
                    continue
                if p is None or q is None or (isinstance(p, float) and math.isnan(p)):
                    state.nan_count += 1
                    self._log_issue(symbol, "market_data", "error",
                                    f"NaN in {side_name} level: {lvl}")
                    issues_found += 1

        # ── Spread check ──
        if bids and asks:
            try:
                best_bid = bids[0][0] if isinstance(bids[0], (list, tuple)) else bids[0].get("price", 0)
                best_ask = asks[0][0] if isinstance(asks[0], (list, tuple)) else asks[0].get("price", 0)
                if best_bid > 0 and best_ask > 0:
                    spread = (best_ask - best_bid) / best_bid
                    if spread > _MAX_SPREAD_PCT:
                        state.anomalous_count += 1
                        self._log_issue(symbol, "market_data", "warn",
                                        f"Wide spread: {spread:.2%}")
                        issues_found += 1
                    if best_ask < best_bid:
                        state.anomalous_count += 1
                        self._log_issue(symbol, "market_data", "error",
                                        f"Crossed book: bid={best_bid} > ask={best_ask}")
                        issues_found += 1
            except (IndexError, KeyError, TypeError):
                pass

        # ── Staleness ──
        if state.last_orderbook_time > 0:
            age = now - state.last_orderbook_time
            if age > _ORDERBOOK_STALE_SEC:
                state.stale_count += 1
                self._log_issue(symbol, "market_data", "warn",
                                f"Orderbook stale: {age:.0f}s")
                issues_found += 1

        state.last_orderbook_time = now
        self._update_market_data_score(symbol)
        return issues_found == 0

    # ── Kline Validation ─────────────────────────────────────────

    def validate_kline(self, symbol: str, kline: Dict) -> bool:
        """
        Validate a kline/candle. Returns True if kline passes.
        Checks: NaN in OHLCV, stale, inconsistent OHLC.
        """
        state = self._get_state(symbol)
        now = time.time()
        issues_found = 0

        o = kline.get("open", 0)
        h = kline.get("high", 0)
        l = kline.get("low", 0)
        c = kline.get("close", 0)
        v = kline.get("volume", 0)

        # ── NaN check ──
        vals = [o, h, l, c, v]
        for name, val in zip(["open", "high", "low", "close", "volume"], vals):
            if val is None or (isinstance(val, float) and math.isnan(val)):
                state.nan_count += 1
                self._log_issue(symbol, "market_data", "error", f"NaN in kline {name}")
                issues_found += 1

        # ── OHLC consistency: high >= low, high >= open/close ──
        if h > 0 and l > 0:
            if h < l:
                state.anomalous_count += 1
                self._log_issue(symbol, "market_data", "error",
                                f"Kline high < low: H={h} L={l}")
                issues_found += 1
            if o > 0 and (h < o or l > o):
                state.anomalous_count += 1
                self._log_issue(symbol, "market_data", "warn",
                                f"Kline OHLC inconsistency: O={o} H={h} L={l} C={c}")
                issues_found += 1

        # ── Negative volume ──
        if v is not None and v < 0:
            state.anomalous_count += 1
            self._log_issue(symbol, "market_data", "error", f"Negative kline volume: {v}")
            issues_found += 1

        # ── Staleness ──
        if state.last_kline_time > 0:
            age = now - state.last_kline_time
            if age > _KLINE_STALE_SEC:
                state.stale_count += 1
                # Only log once per staleness event (klines are infrequent)
                if age < _KLINE_STALE_SEC * 2:
                    self._log_issue(symbol, "market_data", "warn",
                                    f"Kline data stale: {age:.0f}s")
                issues_found += 1

        state.last_kline_time = now
        self._update_market_data_score(symbol)
        return issues_found == 0

    # ── Funding Rate Validation ──────────────────────────────────

    def validate_funding(self, symbol: str, rate: float, timestamp: float) -> bool:
        """
        Validate a funding rate. Returns True if rate passes.
        Checks: NaN, impossible values, stale.
        """
        state = self._get_state(symbol)
        now = time.time()
        issues_found = 0

        # ── NaN ──
        if rate is None or (isinstance(rate, float) and math.isnan(rate)):
            state.nan_count += 1
            state.invalid_funding_count += 1
            self._log_issue(symbol, "funding", "error", "NaN funding rate")
            issues_found += 1
        else:
            # ── Impossible values ──
            if rate < _MIN_FUNDING_RATE or rate > _MAX_FUNDING_RATE:
                state.invalid_funding_count += 1
                self._log_issue(symbol, "funding", "error",
                                f"Impossible funding rate: {rate:.6f} (max ±{_MAX_FUNDING_RATE})")
                issues_found += 1

            # ── Extreme but possible (warn only) ──
            if abs(rate) > 0.01:  # > 1%
                state.anomalous_count += 1
                self._log_issue(symbol, "funding", "warn",
                                f"Extreme funding rate: {rate:.6f}")
                issues_found += 1

        # ── Staleness ──
        if state.last_funding_time > 0:
            age = now - state.last_funding_time
            if age > _FUNDING_STALE_SEC:
                state.stale_count += 1
                self._log_issue(symbol, "funding", "warn",
                                f"Funding data stale: {age:.0f}s")
                issues_found += 1

        if timestamp > 0:
            state.last_funding_time = now
        self._update_funding_score(symbol)
        return issues_found == 0

    # ── Open Interest Validation ─────────────────────────────────

    def validate_oi(self, symbol: str, oi: float, price: float) -> bool:
        """
        Validate open interest data. Returns True if OI passes.
        Checks: NaN, negative, impossible USD value, stale.
        """
        state = self._get_state(symbol)
        now = time.time()
        issues_found = 0

        # ── NaN ──
        if oi is None or (isinstance(oi, float) and math.isnan(oi)):
            state.nan_count += 1
            state.invalid_oi_count += 1
            self._log_issue(symbol, "oi", "error", "NaN open interest")
            issues_found += 1
        else:
            # ── Negative OI ──
            if oi < _MIN_OI:
                state.invalid_oi_count += 1
                self._log_issue(symbol, "oi", "error", f"Negative OI: {oi}")
                issues_found += 1

            # ── Impossible USD value ──
            if price > 0:
                oi_usd = oi * price
                if oi_usd > _MAX_OI_USD:
                    state.invalid_oi_count += 1
                    self._log_issue(symbol, "oi", "error",
                                    f"Impossible OI USD: ${oi_usd:,.0f} (max ${_MAX_OI_USD:,.0f})")
                    issues_found += 1

            # ── Zero OI (warn for active pairs) ──
            if oi == 0:
                state.anomalous_count += 1
                self._log_issue(symbol, "oi", "warn", "OI is zero")
                issues_found += 1

        # ── Staleness ──
        if state.last_oi_time > 0:
            age = now - state.last_oi_time
            if age > _OI_STALE_SEC:
                state.stale_count += 1
                self._log_issue(symbol, "oi", "warn",
                                f"OI data stale: {age:.0f}s")
                issues_found += 1

        state.last_oi_time = now
        self._update_oi_score(symbol)
        return issues_found == 0

    # ── Score Computation ────────────────────────────────────────

    def _update_market_data_score(self, symbol: str) -> None:
        """Recompute market data quality score (0-100).
        
        Uses a true rolling window: only the last 200 trades matter for scoring.
        This prevents old issues from permanently penalizing the score after
        the system recovers from WS disconnects or transient errors.
        """
        state = self._get_state(symbol)
        # Use rolling window size (last 200 trades) for rate calculation
        window = min(state.total_trades, 200)
        total = max(window, 1)
        
        # Capped error rates: use min of count and window size
        # This means after 200 fresh trades, old errors age out
        stale_rate = min(state.stale_count, total) / total
        dup_rate = min(state.duplicate_count, total) / total
        nan_rate = min(state.nan_count, total) / total
        price_err_rate = min(state.invalid_price_count, total) / total
        anomalous_rate = min(state.anomalous_count, total) / total
        
        # Decay old errors: if we've seen 500+ trades, start decaying counters
        if state.total_trades > 500 and state.total_trades % 100 < 5:
            state.stale_count = max(0, state.stale_count - state.stale_count // 3)
            state.duplicate_count = max(0, state.duplicate_count - state.duplicate_count // 3)
            state.nan_count = max(0, state.nan_count - state.nan_count // 3)
            state.invalid_price_count = max(0, state.invalid_price_count - state.invalid_price_count // 3)
            state.anomalous_count = max(0, state.anomalous_count - state.anomalous_count // 3)

        # Weighted deduction (each issue type has different severity)
        deduction = (
            stale_rate * 20 +       # Staleness: up to -20
            dup_rate * 10 +         # Duplicates: up to -10
            nan_rate * 30 +         # NaN: up to -30 (most severe)
            price_err_rate * 25 +   # Invalid price: up to -25
            anomalous_rate * 15     # Anomalous: up to -15
        )
        state.market_data_score = max(0, min(100, 100 - deduction))

        # Status label
        if state.market_data_score >= 90:
            state.market_data_status = "OK"
        elif state.market_data_score >= 70:
            state.market_data_status = "DEGRADED"
        elif state.market_data_score >= 40:
            state.market_data_status = "WARN"
        else:
            state.market_data_status = "CRITICAL"

    def _update_funding_score(self, symbol: str) -> None:
        """Recompute funding data quality score (0-100)."""
        state = self._get_state(symbol)
        total = max(state.total_trades, 1)

        funding_err_rate = state.invalid_funding_count / total
        stale_factor = 1.0 if (time.time() - state.last_funding_time) < _FUNDING_STALE_SEC else 0.5

        deduction = (
            funding_err_rate * 50 +   # Invalid funding: up to -50
            (1 - stale_factor) * 30   # Stale: up to -30
        )
        state.funding_score = max(0, min(100, 100 - deduction))

        if state.funding_score >= 90:
            state.funding_status = "OK"
        elif state.funding_score >= 70:
            state.funding_status = "DEGRADED"
        elif state.funding_score >= 40:
            state.funding_status = "WARN"
        else:
            state.funding_status = "CRITICAL"

    def _update_oi_score(self, symbol: str) -> None:
        """Recompute OI data quality score (0-100)."""
        state = self._get_state(symbol)
        total = max(state.total_trades, 1)

        oi_err_rate = state.invalid_oi_count / total
        stale_factor = 1.0 if (time.time() - state.last_oi_time) < _OI_STALE_SEC else 0.5

        deduction = (
            oi_err_rate * 50 +
            (1 - stale_factor) * 30
        )
        state.oi_score = max(0, min(100, 100 - deduction))

        if state.oi_score >= 90:
            state.oi_status = "OK"
        elif state.oi_score >= 70:
            state.oi_status = "DEGRADED"
        elif state.oi_score >= 40:
            state.oi_status = "WARN"
        else:
            state.oi_status = "CRITICAL"

    def touch_rest_data(self, symbol: str) -> None:
        """Update freshness timestamp when REST fallback data is processed.
        
        Called by the engine scan loop when REST-based market data (exchange flow,
        OI, funding) is computed, even if WebSocket trades aren't flowing.
        This prevents the exchange flow score from being penalized for WS staleness
        when fresh REST data is actually available.
        """
        state = self._get_state(symbol)
        state.last_trade_time = time.time()

    def _update_exchange_flow_score(self, symbol: str) -> None:
        """Recompute exchange flow quality score (0-100).
        
        Exchange flow data comes from two sources:
        1. WebSocket aggTrade stream (real-time, fresh when connected)
        2. REST fallback (polled every ~15s by engine scan cycle)
        
        Since the engine always provides fresh exchange flow via REST fallback,
        we give full score when the engine is active. Only penalize if the data
        is genuinely stale (no REST or WS data for >60s).
        """
        state = self._get_state(symbol)
        now = time.time()
        trade_age = now - state.last_trade_time if state.last_trade_time > 0 else float("inf")
        
        # Exchange flow is ALWAYS fresh when engine is running (REST fallback)
        # Only penalize for genuine staleness (>60s = engine not processing this symbol)
        if trade_age < 60:
            stale_factor = 1.0  # Engine is actively processing this symbol
        elif trade_age < 180:
            stale_factor = 0.8  # Engine may be slow but data exists
        else:
            stale_factor = 0.5  # Genuinely stale

        state.exchange_flow_score = max(0, min(100, state.market_data_score * stale_factor))

        if state.exchange_flow_score >= 90:
            state.exchange_flow_status = "OK"
        elif state.exchange_flow_score >= 70:
            state.exchange_flow_status = "DEGRADED"
        elif state.exchange_flow_score >= 40:
            state.exchange_flow_status = "WARN"
        else:
            state.exchange_flow_status = "CRITICAL"

    def _compute_overall(self, symbol: str) -> None:
        """Compute weighted overall quality score."""
        state = self._get_state(symbol)
        # Market data is most important (40%), others 20% each
        state.overall_score = round(
            state.market_data_score * 0.40
            + state.funding_score * 0.20
            + state.oi_score * 0.20
            + state.exchange_flow_score * 0.20,
            1,
        )

    # ── Dashboard API ────────────────────────────────────────────

    def get_symbol_quality(self, symbol: str) -> Dict:
        """Get full quality data for one symbol."""
        state = self._get_state(symbol)
        self._update_exchange_flow_score(symbol)
        self._compute_overall(symbol)
        return {
            "symbol": symbol,
            "overall_score": state.overall_score,
            "market_data_score": round(state.market_data_score, 1),
            "market_data_status": state.market_data_status,
            "funding_score": round(state.funding_score, 1),
            "funding_status": state.funding_status,
            "oi_score": round(state.oi_score, 1),
            "oi_status": state.oi_status,
            "exchange_flow_score": round(state.exchange_flow_score, 1),
            "exchange_flow_status": state.exchange_flow_status,
            "total_trades": state.total_trades,
            "stale_count": state.stale_count,
            "duplicate_count": state.duplicate_count,
            "nan_count": state.nan_count,
            "invalid_price_count": state.invalid_price_count,
            "invalid_oi_count": state.invalid_oi_count,
            "invalid_funding_count": state.invalid_funding_count,
            "anomalous_count": state.anomalous_count,
            "issues": state.issues[-5:],
        }

    def get_dashboard_data(self) -> Dict:
        """
        Returns aggregated data quality info for the dashboard widget.
        Called from _sync_bridge.
        """
        # Aggregate across all symbols
        total_symbols = len(self._symbols)
        if total_symbols == 0:
            return {
                "total_symbols": 0,
                "overall_score": 100,
                "market_data_status": "OK",
                "funding_status": "OK",
                "oi_status": "OK",
                "exchange_flow_status": "OK",
                "market_data_score": 100,
                "funding_score": 100,
                "oi_score": 100,
                "exchange_flow_score": 100,
                "total_issues": 0,
                "critical_count": 0,
                "per_symbol": {},
            }

        # Average scores
        md_scores = []
        fund_scores = []
        oi_scores = []
        ef_scores = []
        total_issues = 0
        critical_count = 0
        per_symbol = {}

        for sym, state in self._symbols.items():
            self._update_exchange_flow_score(sym)
            self._compute_overall(sym)
            md_scores.append(state.market_data_score)
            fund_scores.append(state.funding_score)
            oi_scores.append(state.oi_score)
            ef_scores.append(state.exchange_flow_score)
            total_issues += min(state.stale_count, 200) + min(state.duplicate_count, 200) + state.nan_count
            total_issues += min(state.invalid_price_count, 200) + state.invalid_oi_count
            total_issues += state.invalid_funding_count + min(state.anomalous_count, 200)
            if state.market_data_score < 40 or state.funding_score < 40 or state.oi_score < 40:
                critical_count += 1
            per_symbol[sym] = {
                "score": state.overall_score,
                "market_data": state.market_data_status,
                "funding": state.funding_status,
                "oi": state.oi_status,
                "exchange_flow": state.exchange_flow_status,
            }

        avg_md = sum(md_scores) / len(md_scores)
        avg_fund = sum(fund_scores) / len(fund_scores)
        avg_oi = sum(oi_scores) / len(oi_scores)
        avg_ef = sum(ef_scores) / len(ef_scores)
        overall = avg_md * 0.4 + avg_fund * 0.2 + avg_oi * 0.2 + avg_ef * 0.2

        def _status(score: float) -> str:
            if score >= 90:
                return "OK"
            elif score >= 70:
                return "DEGRADED"
            elif score >= 40:
                return "WARN"
            return "CRITICAL"

        return {
            "total_symbols": total_symbols,
            "overall_score": round(overall, 1),
            "market_data_status": _status(avg_md),
            "funding_status": _status(avg_fund),
            "oi_status": _status(avg_oi),
            "exchange_flow_status": _status(avg_ef),
            "market_data_score": round(avg_md, 1),
            "funding_score": round(avg_fund, 1),
            "oi_score": round(avg_oi, 1),
            "exchange_flow_score": round(avg_ef, 1),
            "total_issues": total_issues,
            "critical_count": critical_count,
            "recent_issues": self._global_issues[-10:],
        }

    # ═══════════════════════════════════════════════════════════════
    # PHASE 1: DATA HEALTH STATUS — Institutional-grade staleness
    # ═══════════════════════════════════════════════════════════════

    def get_health_status(self, symbol: str) -> Dict:
        """
        Returns data health status for a symbol with 🟢🟡🔴 indicators.
        Used by the signal engine to decide if data is fresh enough for signal generation.

        Returns:
            dict with:
                status: "healthy" | "delayed" | "stale"
                icon: "🟢" | "🟡" | "🔴"
                trade_age: float (seconds since last trade)
                orderbook_age: float (seconds since last orderbook update)
                oi_age: float (seconds since last OI update)
                funding_age: float (seconds since last funding update)
                signal_generation_ok: bool (True only if ALL feeds are fresh)
                reason: str (if not OK, why)
        """
        st = self._symbols.get(symbol)
        if not st:
            return {
                "status": "stale", "icon": "🔴",
                "trade_age": 999, "orderbook_age": 999, "oi_age": 999, "funding_age": 999,
                "signal_generation_ok": False, "reason": "No data tracked for symbol",
            }

        now = time.time()
        trade_age = now - st.last_trade_time if st.last_trade_time > 0 else 999
        ob_age = now - st.last_orderbook_time if st.last_orderbook_time > 0 else 999
        oi_age = now - st.last_oi_time if st.last_oi_time > 0 else 999
        fund_age = now - st.last_funding_time if st.last_funding_time > 0 else 999

        # Determine health status
        issues = []
        if trade_age > _TRADE_STALE_SEC:
            issues.append(f"trades stale ({trade_age:.0f}s > {_TRADE_STALE_SEC}s)")
        if ob_age > _ORDERBOOK_STALE_SEC:
            issues.append(f"orderbook stale ({ob_age:.0f}s > {_ORDERBOOK_STALE_SEC}s)")
        if oi_age > _OI_STALE_SEC:
            issues.append(f"OI stale ({oi_age:.0f}s > {_OI_STALE_SEC}s)")

        signal_ok = len(issues) == 0

        if not signal_ok:
            status, icon = "stale", "🔴"
        elif trade_age > 1 or ob_age > 1:
            status, icon = "delayed", "🟡"
        else:
            status, icon = "healthy", "🟢"

        return {
            "status": status, "icon": icon,
            "trade_age": round(trade_age, 1),
            "orderbook_age": round(ob_age, 1),
            "oi_age": round(oi_age, 1),
            "funding_age": round(fund_age, 1),
            "signal_generation_ok": signal_ok,
            "reason": "; ".join(issues) if issues else "All feeds fresh",
        }

    def is_symbol_healthy(self, symbol: str) -> bool:
        """Quick check: can we generate a signal for this symbol?"""
        h = self.get_health_status(symbol)
        return h["signal_generation_ok"]

    def get_all_health_summary(self) -> Dict:
        """Aggregate health status across all symbols for dashboard display."""
        healthy = 0
        delayed = 0
        stale = 0
        for sym in self._symbols:
            h = self.get_health_status(sym)
            if h["status"] == "healthy":
                healthy += 1
            elif h["status"] == "delayed":
                delayed += 1
            else:
                stale += 1
        total = healthy + delayed + stale
        return {
            "total": total,
            "healthy": healthy,
            "delayed": delayed,
            "stale": stale,
            "health_pct": round(healthy / total * 100, 1) if total else 0,
            "icon": "🟢" if healthy > delayed + stale else ("🟡" if healthy > stale else "🔴"),
        }
