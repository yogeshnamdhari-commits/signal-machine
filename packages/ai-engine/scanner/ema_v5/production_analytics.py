"""
Production Analytics — Aggregated trade performance metrics.

Reads from institutional_v1.db and forward_test.db to compute:
- Overall: Win rate, Profit factor, Expectancy, Avg R, Max drawdown
- TP1/TP2/TP3 hit rates
- Performance by session, confidence bucket, regime, symbol
- Exit reason distribution
- Equity curve data
- Rolling metrics (20-trade window)
- Trade lifecycle tracking

All results are cached for 60 seconds to avoid DB hammering.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Any

from loguru import logger


class ProductionAnalytics:
    """Aggregated trade performance analytics engine."""

    CACHE_TTL_SEC = 60  # refresh every 60 seconds

    def __init__(self, data_dir: Optional[Path] = None):
        if data_dir is None:
            data_dir = Path(__file__).resolve().parent.parent.parent / "data"
        self._data_dir = data_dir
        self._inst_db = str(data_dir / "institutional_v1.db")
        self._fwd_db = str(data_dir / "forward_test.db")
        self._cache: Dict[str, Any] = {}
        self._cache_time: float = 0
        self._trade_count: int = 0
        logger.info("📊 ProductionAnalytics initialized")

    def get_all(self, force: bool = False) -> Dict[str, Any]:
        """Get all analytics, using cache if fresh."""
        now = time.time()
        if not force and self._cache and (now - self._cache_time) < self.CACHE_TTL_SEC:
            return self._cache
        try:
            result = self._compute_all()
            self._cache = result
            self._cache_time = now
            self._trade_count = result.get("overall", {}).get("total_trades", 0)
            return result
        except Exception as e:
            logger.error("📊 ProductionAnalytics error: {}", e)
            return self._cache or {"error": str(e)}

    def get_trade_count(self) -> int:
        """Return cached trade count without recomputing."""
        return self._trade_count

    # ──────────────────────────────────────────────
    # Core computation
    # ──────────────────────────────────────────────

    def _compute_all(self) -> Dict[str, Any]:
        trades = self._load_trades()
        if not trades:
            return {"error": "No trade data available", "overall": {"total_trades": 0}}

        return {
            "overall": self._compute_overall(trades),
            "by_session": self._compute_by_dimension(trades, "session"),
            "by_regime": self._compute_by_dimension(trades, "regime"),
            "by_confidence_bucket": self._compute_by_confidence(trades),
            "by_symbol": self._compute_by_symbol(trades),
            "exit_reasons": self._compute_exit_reasons(trades),
            "tp_hit_rates": self._compute_tp_hit_rates(trades),
            "equity_curve": self._compute_equity_curve(trades),
            "rolling_metrics": self._compute_rolling_metrics(trades),
            "lifecycle": self._compute_lifecycle(trades),
            "computed_at": time.time(),
            "total_trades": len(trades),
        }

    # ──────────────────────────────────────────────
    # Trade loading
    # ──────────────────────────────────────────────

    def _load_trades(self) -> List[Dict]:
        """Load completed trades from institutional_v1.db positions_archive."""
        trades = []
        try:
            conn = sqlite3.connect(self._inst_db)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT
                    symbol, side, entry_price, stop_loss, take_profit,
                    take_profit_2, take_profit_3, current_tp_index,
                    pnl, fees, status, opened_at, closed_at,
                    exit_reason, strategy_version, confidence, regime,
                    institutional_score, risk_reward, hold_minutes,
                    session, mfe_pct, mae_pct, alpha_score, alpha_tier,
                    mss_score, fvg_score, entry_reason, outcome,
                    realized_r, planned_rr, volatility_score,
                    quiet_market_blocked, highest_pnl
                FROM positions_archive
                WHERE status IN ('closed', 'win', 'loss', 'breakeven')
                   OR exit_reason IS NOT NULL
                   OR pnl != 0
                ORDER BY closed_at DESC
            """).fetchall()
            for r in rows:
                trades.append(dict(r))
            conn.close()
        except Exception as e:
            logger.warning("📊 Failed to load institutional trades: {}", e)

        # Also load from forward_test.db if available
        try:
            conn = sqlite3.connect(self._fwd_db)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT
                    symbol, side, entry_price, stop_loss, take_profit,
                    pnl, fees, net_pnl, exit_price, exit_time,
                    exit_reason, hold_minutes, mae_pct, mfe_pct,
                    regime, session, confidence_100, institutional_score,
                    sweep_score, mss_score, fvg_score, delta, cvd,
                    oi_delta, funding_rate, outcome, strategy_version,
                    realized_r, planned_rr, entry_time
                FROM forward_trades
                WHERE exit_price IS NOT NULL OR pnl != 0
                ORDER BY exit_time DESC
            """).fetchall()
            for r in rows:
                d = dict(r)
                # Normalize field names to match institutional format
                d["confidence"] = d.pop("confidence_100", 0) or 0
                if d["confidence"] > 1:
                    d["confidence"] = d["confidence"] / 100.0  # normalize to 0-1
                d["opened_at"] = d.pop("entry_time", None)
                d["closed_at"] = d.pop("exit_time", None)
                d["status"] = "closed"
                trades.append(d)
            conn.close()
        except Exception as e:
            logger.debug("📊 Forward test DB not available: {}", e)

        return trades

    # ──────────────────────────────────────────────
    # Overall metrics
    # ──────────────────────────────────────────────

    def _compute_overall(self, trades: List[Dict]) -> Dict:
        total = len(trades)
        if total == 0:
            return {"total_trades": 0}

        pnls = [self._get_pnl(t) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        breakevens = [p for p in pnls if p == 0]

        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        net_pnl = sum(pnls)

        win_rate = len(wins) / total * 100 if total else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (
            float("inf") if gross_profit > 0 else 0
        )

        # Expectancy = average R per trade
        realized_rs = [self._get_realized_r(t) for t in trades]
        avg_r = sum(realized_rs) / len(realized_rs) if realized_rs else 0

        # Expectancy in R: (win_rate * avg_win_R) - (loss_rate * avg_loss_R)
        win_rs = [r for r in realized_rs if r > 0]
        loss_rs = [r for r in realized_rs if r < 0]
        avg_win_r = sum(win_rs) / len(win_rs) if win_rs else 0
        avg_loss_r = sum(loss_rs) / len(loss_rs) if loss_rs else 0
        expectancy = (len(wins) / total * avg_win_r) + (len(losses) / total * avg_loss_r) if total else 0

        # Max drawdown from equity curve
        max_dd = self._compute_max_drawdown(pnls)

        # Average hold time
        hold_mins = [t.get("hold_minutes") or 0 for t in trades if t.get("hold_minutes")]
        avg_hold = sum(hold_mins) / len(hold_mins) if hold_mins else 0

        # Average confidence
        confs = [t.get("confidence") or 0 for t in trades]
        avg_conf = sum(confs) / len(confs) * 100 if confs else 0  # display as percentage

        # Streak analysis
        streaks = self._compute_streaks(pnls)

        return {
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "breakevens": len(breakevens),
            "win_rate_pct": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "net_pnl": round(net_pnl, 4),
            "gross_profit": round(gross_profit, 4),
            "gross_loss": round(gross_loss, 4),
            "avg_pnl": round(net_pnl / total, 4) if total else 0,
            "avg_win": round(sum(wins) / len(wins), 4) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 4) if losses else 0,
            "avg_r": round(avg_r, 3),
            "avg_win_r": round(avg_win_r, 3),
            "avg_loss_r": round(avg_loss_r, 3),
            "expectancy_r": round(expectancy, 3),
            "max_drawdown_pct": round(max_dd, 2),
            "avg_hold_minutes": round(avg_hold, 0),
            "avg_confidence_pct": round(avg_conf, 1),
            "max_win": round(max(pnls), 4) if pnls else 0,
            "max_loss": round(min(pnls), 4) if pnls else 0,
            "best_streak": streaks["best"],
            "worst_streak": streaks["worst"],
            "current_streak": streaks["current"],
            "current_streak_type": streaks["current_type"],
        }

    # ──────────────────────────────────────────────
    # Dimension breakdowns
    # ──────────────────────────────────────────────

    def _compute_by_dimension(self, trades: List[Dict], dim: str) -> Dict[str, Dict]:
        """Compute metrics grouped by a dimension (session, regime, etc.)."""
        groups: Dict[str, List] = {}
        for t in trades:
            key = t.get(dim) or "unknown"
            if key not in groups:
                groups[key] = []
            groups[key].append(t)

        result = {}
        for key, group in sorted(groups.items()):
            result[key] = self._compute_group_metrics(group)
        return result

    def _compute_by_confidence(self, trades: List[Dict]) -> Dict[str, Dict]:
        """Compute metrics by confidence bucket."""
        buckets = {
            "<80": [], "80-85": [], "85-90": [], "90-95": [], "95+": []
        }
        for t in trades:
            conf = (t.get("confidence") or 0)
            if conf > 1:
                conf = conf / 100.0  # normalize
            conf_pct = conf * 100
            if conf_pct < 80:
                buckets["<80"].append(t)
            elif conf_pct < 85:
                buckets["80-85"].append(t)
            elif conf_pct < 90:
                buckets["85-90"].append(t)
            elif conf_pct < 95:
                buckets["90-95"].append(t)
            else:
                buckets["95+"].append(t)

        result = {}
        for key, group in buckets.items():
            if group:
                result[key] = self._compute_group_metrics(group)
        return result

    def _compute_by_symbol(self, trades: List[Dict]) -> Dict[str, Dict]:
        """Compute metrics for top symbols by trade count."""
        groups: Dict[str, List] = {}
        for t in trades:
            sym = t.get("symbol", "unknown")
            if sym not in groups:
                groups[sym] = []
            groups[sym].append(t)

        # Sort by trade count descending, take top 20
        sorted_syms = sorted(groups.items(), key=lambda x: len(x[1]), reverse=True)[:20]
        result = {}
        for sym, group in sorted_syms:
            result[sym] = self._compute_group_metrics(group)
        return result

    def _compute_group_metrics(self, group: List[Dict]) -> Dict:
        """Compute metrics for a group of trades."""
        total = len(group)
        if total == 0:
            return {"trades": 0}

        pnls = [self._get_pnl(t) for t in group]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        pf = gross_profit / gross_loss if gross_loss > 0 else (
            float("inf") if gross_profit > 0 else 0
        )

        realized_rs = [self._get_realized_r(t) for t in group]
        avg_r = sum(realized_rs) / len(realized_rs) if realized_rs else 0

        return {
            "trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": round(len(wins) / total * 100, 1) if total else 0,
            "profit_factor": round(pf, 2),
            "net_pnl": round(sum(pnls), 4),
            "avg_pnl": round(sum(pnls) / total, 4) if total else 0,
            "avg_r": round(avg_r, 3),
            "avg_hold_min": round(
                sum(t.get("hold_minutes") or 0 for t in group) / total, 0
            ) if total else 0,
        }

    # ──────────────────────────────────────────────
    # Exit reasons
    # ──────────────────────────────────────────────

    def _compute_exit_reasons(self, trades: List[Dict]) -> Dict[str, Dict]:
        """Compute metrics by exit reason (normalized)."""
        groups: Dict[str, List] = {}
        for t in trades:
            raw_reason = t.get("exit_reason") or "unknown"
            # Normalize trailing stop variants
            reason = raw_reason
            if reason.startswith("trailing_stop"):
                reason = "trailing_stop"
            elif reason.startswith("mfe_trailing_stop"):
                reason = "mfe_trailing_stop"
            if reason not in groups:
                groups[reason] = []
            groups[reason].append(t)

        result = {}
        for reason, group in sorted(groups.items(), key=lambda x: len(x[1]), reverse=True):
            result[reason] = self._compute_group_metrics(group)
        return result

    # ──────────────────────────────────────────────
    # TP hit rates
    # ──────────────────────────────────────────────

    def _compute_tp_hit_rates(self, trades: List[Dict]) -> Dict[str, Any]:
        """Compute TP reach rates and exit-at-TP rates.

        current_tp_index tracks the HIGHEST TP level the price reached (MFE).
        exit_reason tells us where the trade actually exited.
        """
        tp1_reached = 0
        tp2_reached = 0
        tp3_reached = 0
        tp1_exits = 0
        tp2_exits = 0
        tp3_exits = 0
        sl_hits = 0
        trailing_hits = 0
        time_exits = 0
        total = len(trades)

        for t in trades:
            tp_idx = t.get("current_tp_index") or 0
            exit_reason = (t.get("exit_reason") or "").lower()

            # TP level reached (MFE tracking)
            if tp_idx >= 1:
                tp1_reached += 1
            if tp_idx >= 2:
                tp2_reached += 1
            if tp_idx >= 3:
                tp3_reached += 1

            # Actual exit reason
            if "take_profit_1" in exit_reason:
                tp1_exits += 1
            elif "take_profit_2" in exit_reason:
                tp2_exits += 1
            elif "take_profit_3" in exit_reason:
                tp3_exits += 1
            elif "stop_loss" in exit_reason:
                sl_hits += 1
            elif "trailing" in exit_reason:
                trailing_hits += 1
            elif "time" in exit_reason or "no_progress" in exit_reason:
                time_exits += 1

        return {
            "tp1_reached_pct": round(tp1_reached / total * 100, 1) if total else 0,
            "tp2_reached_pct": round(tp2_reached / total * 100, 1) if total else 0,
            "tp3_reached_pct": round(tp3_reached / total * 100, 1) if total else 0,
            "tp1_exit_pct": round(tp1_exits / total * 100, 1) if total else 0,
            "tp2_exit_pct": round(tp2_exits / total * 100, 1) if total else 0,
            "tp3_exit_pct": round(tp3_exits / total * 100, 1) if total else 0,
            "sl_exit_pct": round(sl_hits / total * 100, 1) if total else 0,
            "trailing_exit_pct": round(trailing_hits / total * 100, 1) if total else 0,
            "time_exit_pct": round(time_exits / total * 100, 1) if total else 0,
            "tp1_reached": tp1_reached,
            "tp2_reached": tp2_reached,
            "tp3_reached": tp3_reached,
            "tp1_exits": tp1_exits,
            "tp2_exits": tp2_exits,
            "tp3_exits": tp3_exits,
            "sl_count": sl_hits,
            "trailing_count": trailing_hits,
            "time_exit_count": time_exits,
        }

    # ──────────────────────────────────────────────
    # Equity curve
    # ──────────────────────────────────────────────

    def _compute_equity_curve(self, trades: List[Dict]) -> List[Dict]:
        """Compute cumulative equity curve (last 100 trades)."""
        # Sort by close time
        sorted_trades = sorted(trades, key=lambda t: t.get("closed_at") or t.get("opened_at") or "")
        # Take last 100
        recent = sorted_trades[-100:]

        curve = []
        cumulative = 0
        for t in recent:
            pnl = self._get_pnl(t)
            cumulative += pnl
            curve.append({
                "symbol": t.get("symbol", ""),
                "pnl": round(pnl, 4),
                "cumulative": round(cumulative, 4),
                "time": t.get("closed_at") or t.get("opened_at") or "",
            })
        return curve

    # ──────────────────────────────────────────────
    # Rolling metrics (20-trade window)
    # ──────────────────────────────────────────────

    def _compute_rolling_metrics(self, trades: List[Dict]) -> Dict[str, Any]:
        """Compute rolling 20-trade profit factor and win rate."""
        sorted_trades = sorted(trades, key=lambda t: t.get("closed_at") or t.get("opened_at") or "")
        pnls = [self._get_pnl(t) for t in sorted_trades]

        window = 20
        if len(pnls) < window:
            return {
                "window": len(pnls),
                "current_pf": round(self._rolling_pf(pnls), 2),
                "current_wr": round(
                    len([p for p in pnls if p > 0]) / len(pnls) * 100, 1
                ) if pnls else 0,
                "history": [],
            }

        # Compute rolling metrics over the last window
        history = []
        for i in range(window, len(pnls) + 1):
            w = pnls[i - window:i]
            wins_in_w = [p for p in w if p > 0]
            losses_in_w = [p for p in w if p < 0]
            gp = sum(wins_in_w)
            gl = abs(sum(losses_in_w))
            pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0)
            wr = len(wins_in_w) / len(w) * 100 if w else 0
            history.append({
                "trade_num": i,
                "pf": round(pf, 2),
                "wr": round(wr, 1),
                "net": round(sum(w), 4),
            })

        # Current (last window)
        current_w = pnls[-window:]
        current_wins = [p for p in current_w if p > 0]
        current_losses = [p for p in current_w if p < 0]
        c_gp = sum(current_wins)
        c_gl = abs(sum(current_losses))
        current_pf = c_gp / c_gl if c_gl > 0 else (float("inf") if c_gp > 0 else 0)

        return {
            "window": window,
            "current_pf": round(current_pf, 2),
            "current_wr": round(
                len(current_wins) / len(current_w) * 100, 1
            ) if current_w else 0,
            "history": history[-30:],  # last 30 data points
        }

    # ──────────────────────────────────────────────
    # Trade lifecycle
    # ──────────────────────────────────────────────

    def _compute_lifecycle(self, trades: List[Dict]) -> Dict[str, Any]:
        """Compute trade lifecycle metrics."""
        sorted_trades = sorted(trades, key=lambda t: t.get("closed_at") or t.get("opened_at") or "")

        # Recent 10 trades
        recent = []
        for t in sorted_trades[-10:]:
            recent.append({
                "symbol": t.get("symbol", ""),
                "side": t.get("side", ""),
                "confidence": round((t.get("confidence") or 0) * 100, 0) if (t.get("confidence") or 0) <= 1 else round(t.get("confidence") or 0, 0),
                "pnl": round(self._get_pnl(t), 4),
                "realized_r": round(self._get_realized_r(t), 2),
                "exit_reason": t.get("exit_reason") or "unknown",
                "session": t.get("session") or "unknown",
                "regime": t.get("regime") or "unknown",
                "hold_minutes": t.get("hold_minutes") or 0,
                "closed_at": t.get("closed_at") or "",
            })

        # Time since last trade
        if sorted_trades:
            last_close = sorted_trades[-1].get("closed_at") or sorted_trades[-1].get("opened_at")
            if last_close:
                try:
                    # Handle both Unix timestamps (float/int) and ISO strings
                    if isinstance(last_close, (int, float)) and last_close > 1_000_000_000:
                        hours_since = (time.time() - float(last_close)) / 3600
                    else:
                        from datetime import datetime, timezone
                        last_str = str(last_close)
                        if "T" in last_str:
                            last_dt = datetime.fromisoformat(last_str.replace("Z", "+00:00"))
                        else:
                            last_dt = datetime.strptime(last_str.split(".")[0], "%Y-%m-%d %H:%M:%S")
                        if last_dt.tzinfo is None:
                            last_dt = last_dt.replace(tzinfo=timezone.utc)
                        now_dt = datetime.now(timezone.utc)
                        hours_since = (now_dt - last_dt).total_seconds() / 3600
                except Exception:
                    hours_since = -1
            else:
                hours_since = -1
        else:
            hours_since = -1

        return {
            "recent_trades": recent,
            "hours_since_last_trade": round(hours_since, 1),
        }

    # ──────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────

    @staticmethod
    def _get_pnl(trade: Dict) -> float:
        """Get PnL from trade, preferring net_pnl if available."""
        pnl = trade.get("net_pnl")
        if pnl is None:
            pnl = trade.get("pnl", 0)
        return float(pnl or 0)

    @staticmethod
    def _get_realized_r(trade: Dict) -> float:
        """Get realized R from trade."""
        r = trade.get("realized_r")
        if r is None:
            r = 0
        return float(r or 0)

    @staticmethod
    def _compute_max_drawdown(pnls: List[float]) -> float:
        """Compute maximum drawdown percentage from PnL sequence."""
        if not pnls:
            return 0.0
        equity = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in pnls:
            equity += p
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
        # Convert to percentage of peak
        if peak > 0:
            return (max_dd / peak) * 100
        return 0.0

    @staticmethod
    def _compute_streaks(pnls: List[float]) -> Dict:
        """Compute best, worst, and current streak."""
        if not pnls:
            return {"best": 0, "worst": 0, "current": 0, "current_type": "none"}

        best = 0
        worst = 0
        current = 0
        current_type = "none"

        streak = 0
        streak_type = None
        for p in pnls:
            if p > 0:
                if streak_type == "win":
                    streak += 1
                else:
                    streak = 1
                    streak_type = "win"
            elif p < 0:
                if streak_type == "loss":
                    streak += 1
                else:
                    streak = 1
                    streak_type = "loss"
            else:
                streak = 0
                streak_type = "breakeven"

            if streak_type == "win" and streak > best:
                best = streak
            if streak_type == "loss" and abs(streak) > abs(worst):
                worst = streak

        current = streak
        current_type = streak_type or "none"

        return {
            "best": best,
            "worst": worst,
            "current": current,
            "current_type": current_type,
        }

    @staticmethod
    def _rolling_pf(window: List[float]) -> float:
        """Compute profit factor for a window of PnLs."""
        gp = sum(p for p in window if p > 0)
        gl = abs(sum(p for p in window if p < 0))
        return gp / gl if gl > 0 else (float("inf") if gp > 0 else 0)
