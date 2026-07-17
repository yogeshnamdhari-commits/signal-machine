"""
Production Analytics Engine — Core metrics calculation from live trade data.

Works directly on institutional_v1.db positions table.
READ-ONLY — never modifies strategy or trading logic.

Calculates:
- Win Rate, Profit Factor, Expectancy, Sharpe, Sortino
- Average R, Max Drawdown, Recovery Factor
- Confidence bucket analysis
- Session analysis
- Regime analysis
- Exit reason analysis
- Symbol analysis
- Long vs Short performance
- Equity curve data
- Rolling metrics
"""
from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class ProductionAnalytics:
    """Comprehensive production analytics from live trade data."""

    DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"

    # Confidence bucket boundaries
    CONF_BUCKETS = [
        (95, 100, "95-100"),
        (90, 94, "90-94"),
        (85, 89, "85-89"),
        (80, 84, "80-84"),
        (75, 79, "75-79"),
        (70, 74, "70-74"),
        (0, 69, "<70"),
    ]

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or self.DB_PATH

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _get_closed_trades(self, strategy_filter: Optional[str] = None) -> List[Dict]:
        """Fetch all closed trades from positions table."""
        conn = self._connect()
        cur = conn.cursor()

        query = """
            SELECT id, signal_id, symbol, side, entry_price, quantity, leverage,
                   stop_loss, take_profit, pnl, fees, status, opened_at, closed_at,
                   take_profit_2, take_profit_3, exit_reason, strategy_version,
                   confidence, regime, institutional_score, risk_reward,
                   hold_minutes, session, mfe_pct, mae_pct, realized_r,
                   planned_rr, volatility_score, highest_pnl
            FROM positions WHERE status = 'closed'
        """
        params = []
        if strategy_filter:
            query += " AND strategy_version = ?"
            params.append(strategy_filter)
        query += " ORDER BY closed_at ASC"

        cur.execute(query, params)
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    # ── Core Metrics ──────────────────────────────────────────────

    def core_metrics(self, strategy_filter: Optional[str] = None) -> Dict:
        """Calculate all core performance metrics."""
        trades = self._get_closed_trades(strategy_filter)
        if not trades:
            return {"status": "no_data", "total_trades": 0}

        pnls = [t["pnl"] or 0 for t in trades]
        fees = [t["fees"] or 0 for t in trades]
        n = len(pnls)

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        win_count = len(wins)
        loss_count = len(losses)
        win_rate = win_count / n * 100 if n else 0

        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(abs(l) for l in losses) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (
            float('inf') if gross_profit > 0 else 0
        )

        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(abs(l) for l in losses) / len(losses) if losses else 0
        avg_pnl = sum(pnls) / n if n else 0

        expectancy = (win_rate / 100 * avg_win) - ((100 - win_rate) / 100 * avg_loss)
        payoff = avg_win / avg_loss if avg_loss > 0 else float('inf')

        # R-multiples
        r_multiples = [t["realized_r"] for t in trades
                       if t.get("realized_r") is not None and t["realized_r"] != 0]
        avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0

        # Sharpe Ratio (annualized)
        if n > 1:
            mean_pnl = sum(pnls) / n
            std_pnl = math.sqrt(sum((p - mean_pnl) ** 2 for p in pnls) / (n - 1))
            sharpe = (mean_pnl / std_pnl) * math.sqrt(252) if std_pnl > 0 else 0
        else:
            sharpe = 0

        # Sortino Ratio
        downside = [p for p in pnls if p < 0]
        if len(downside) > 1:
            mean_pnl = sum(pnls) / n
            downside_std = math.sqrt(sum(p ** 2 for p in downside) / len(downside))
            sortino = (mean_pnl / downside_std) * math.sqrt(252) if downside_std > 0 else 0
        else:
            sortino = 0

        # Max Drawdown
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        dd_curve = []
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            dd = peak - cum
            max_dd = max(max_dd, dd)
            dd_curve.append(dd)

        # Recovery Factor
        recovery_factor = sum(pnls) / max_dd if max_dd > 0 else float('inf') if sum(pnls) > 0 else 0

        # Holding time
        hold_times = [t["hold_minutes"] for t in trades if t.get("hold_minutes")]
        avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0

        # MAE / MFE
        maes = [t["mae_pct"] for t in trades if t.get("mae_pct") is not None]
        mfes = [t["mfe_pct"] for t in trades if t.get("mfe_pct") is not None]
        avg_mae = sum(maes) / len(maes) if maes else 0
        avg_mfe = sum(mfes) / len(mfes) if mfes else 0

        # Long vs Short
        longs = [t for t in trades if t["side"] == "LONG"]
        shorts = [t for t in trades if t["side"] == "SHORT"]
        long_pnls = [t["pnl"] or 0 for t in longs]
        short_pnls = [t["pnl"] or 0 for t in shorts]

        long_wr = len([p for p in long_pnls if p > 0]) / len(long_pnls) * 100 if long_pnls else 0
        short_wr = len([p for p in short_pnls if p > 0]) / len(short_pnls) * 100 if short_pnls else 0

        return {
            "status": "ok",
            "total_trades": n,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "avg_pnl": round(avg_pnl, 4),
            "payoff_ratio": round(payoff, 2),
            "avg_r": round(avg_r, 2),
            "sharpe": round(sharpe, 2),
            "sortino": round(sortino, 2),
            "recovery_factor": round(recovery_factor, 2),
            "max_drawdown": round(max_dd, 2),
            "total_pnl": round(sum(pnls), 2),
            "total_fees": round(sum(fees), 2),
            "net_pnl": round(sum(pnls) - sum(fees), 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "avg_hold_minutes": round(avg_hold, 0),
            "avg_mae_pct": round(avg_mae, 2),
            "avg_mfe_pct": round(avg_mfe, 2),
            "long_trades": len(longs),
            "short_trades": len(shorts),
            "long_win_rate": round(long_wr, 1),
            "short_win_rate": round(short_wr, 1),
            "long_pnl": round(sum(long_pnls), 2),
            "short_pnl": round(sum(short_pnls), 2),
        }

    # ── Confidence Bucket Analysis ────────────────────────────────

    def confidence_buckets(self, strategy_filter: Optional[str] = None) -> List[Dict]:
        """Analyze performance by confidence bucket."""
        trades = self._get_closed_trades(strategy_filter)
        if not trades:
            return []

        buckets = {}
        for lo, hi, label in self.CONF_BUCKETS:
            buckets[label] = []

        for t in trades:
            conf = (t["confidence"] or 0) * 100
            for lo, hi, label in self.CONF_BUCKETS:
                if lo <= conf <= hi:
                    buckets[label].append(t)
                    break

        results = []
        for lo, hi, label in self.CONF_BUCKETS:
            bt = buckets[label]
            if not bt:
                results.append({
                    "bucket": label, "trades": 0, "win_rate": 0, "pf": 0,
                    "expectancy": 0, "avg_r": 0, "pnl": 0, "avg_hold": 0,
                })
                continue

            pnls = [t["pnl"] or 0 for t in bt]
            n = len(pnls)
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]

            wr = len(wins) / n * 100 if n else 0
            gp = sum(wins) if wins else 0
            gl = sum(abs(l) for l in losses) if losses else 0
            pf = gp / gl if gl > 0 else float('inf') if gp > 0 else 0
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(abs(l) for l in losses) / len(losses) if losses else 0
            exp = (wr / 100 * avg_win) - ((100 - wr) / 100 * avg_loss)

            rs = [t["realized_r"] for t in bt if t.get("realized_r") is not None and t["realized_r"] != 0]
            avg_r = sum(rs) / len(rs) if rs else 0

            holds = [t["hold_minutes"] for t in bt if t.get("hold_minutes")]
            avg_hold = sum(holds) / len(holds) if holds else 0

            results.append({
                "bucket": label,
                "trades": n,
                "win_rate": round(wr, 1),
                "pf": round(pf, 2),
                "expectancy": round(exp, 4),
                "avg_r": round(avg_r, 2),
                "pnl": round(sum(pnls), 2),
                "avg_hold": round(avg_hold, 0),
            })

        return results

    # ── Session Analysis ──────────────────────────────────────────

    def session_analysis(self, strategy_filter: Optional[str] = None) -> List[Dict]:
        """Analyze performance by trading session."""
        trades = self._get_closed_trades(strategy_filter)
        if not trades:
            return []

        sessions = {}
        for t in trades:
            s = t.get("session") or "unknown"
            if s not in sessions:
                sessions[s] = []
            sessions[s].append(t)

        results = []
        for session_name, st in sorted(sessions.items()):
            pnls = [t["pnl"] or 0 for t in st]
            n = len(pnls)
            wins = [p for p in pnls if p > 0]
            wr = len(wins) / n * 100 if n else 0
            gp = sum(p for p in pnls if p > 0)
            gl = sum(abs(p) for p in pnls if p < 0)
            pf = gp / gl if gl > 0 else float('inf') if gp > 0 else 0
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(abs(p) for p in pnls if p < 0) / max(1, len([p for p in pnls if p < 0]))
            exp = (wr / 100 * avg_win) - ((100 - wr) / 100 * avg_loss)

            rs = [t["realized_r"] for t in st if t.get("realized_r") is not None and t["realized_r"] != 0]
            avg_r = sum(rs) / len(rs) if rs else 0

            results.append({
                "session": session_name,
                "trades": n,
                "win_rate": round(wr, 1),
                "pf": round(pf, 2),
                "expectancy": round(exp, 4),
                "avg_r": round(avg_r, 2),
                "pnl": round(sum(pnls), 2),
            })

        return results

    # ── Regime Analysis ───────────────────────────────────────────

    def regime_analysis(self, strategy_filter: Optional[str] = None) -> List[Dict]:
        """Analyze performance by market regime."""
        trades = self._get_closed_trades(strategy_filter)
        if not trades:
            return []

        regimes = {}
        for t in trades:
            r = t.get("regime") or t.get("at_open_regime") or "unknown"
            if r not in regimes:
                regimes[r] = []
            regimes[r].append(t)

        results = []
        for regime_name, rt in sorted(regimes.items()):
            pnls = [t["pnl"] or 0 for t in rt]
            n = len(pnls)
            wins = [p for p in pnls if p > 0]
            wr = len(wins) / n * 100 if n else 0
            gp = sum(p for p in pnls if p > 0)
            gl = sum(abs(p) for p in pnls if p < 0)
            pf = gp / gl if gl > 0 else float('inf') if gp > 0 else 0
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(abs(p) for p in pnls if p < 0) / max(1, len([p for p in pnls if p < 0]))
            exp = (wr / 100 * avg_win) - ((100 - wr) / 100 * avg_loss)

            rs = [t["realized_r"] for t in rt if t.get("realized_r") is not None and t["realized_r"] != 0]
            avg_r = sum(rs) / len(rs) if rs else 0

            results.append({
                "regime": regime_name,
                "trades": n,
                "win_rate": round(wr, 1),
                "pf": round(pf, 2),
                "expectancy": round(exp, 4),
                "avg_r": round(avg_r, 2),
                "pnl": round(sum(pnls), 2),
            })

        return results

    # ── Exit Reason Analysis ──────────────────────────────────────

    def exit_analysis(self, strategy_filter: Optional[str] = None) -> List[Dict]:
        """Analyze performance by exit reason with detailed metrics."""
        trades = self._get_closed_trades(strategy_filter)
        if not trades:
            return []

        reasons = {}
        for t in trades:
            r = t.get("exit_reason") or "unknown"
            if r not in reasons:
                reasons[r] = []
            reasons[r].append(t)

        results = []
        for reason, rt in sorted(reasons.items(), key=lambda x: -len(x[1])):
            pnls = [t["pnl"] or 0 for t in rt]
            n = len(pnls)
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            wr = len(wins) / n * 100 if n else 0
            total_pnl = sum(pnls)
            
            # Profit Factor
            gp = sum(wins) if wins else 0
            gl = sum(abs(l) for l in losses) if losses else 0
            pf = gp / gl if gl > 0 else float('inf') if gp > 0 else 0
            
            # Average R
            rs = [t["realized_r"] for t in rt if t.get("realized_r") is not None and t["realized_r"] != 0]
            avg_r = sum(rs) / len(rs) if rs else 0
            
            # Expectancy
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(abs(l) for l in losses) / len(losses) if losses else 0
            exp = (wr / 100 * avg_win) - ((100 - wr) / 100 * avg_loss)

            results.append({
                "reason": reason,
                "trades": n,
                "win_rate": round(wr, 1),
                "pf": round(pf, 2),
                "expectancy": round(exp, 4),
                "avg_r": round(avg_r, 2),
                "pnl": round(total_pnl, 2),
                "avg_pnl": round(total_pnl / n, 4) if n else 0,
            })

        return results

    # ── Symbol Analysis ───────────────────────────────────────────

    def symbol_analysis(self, strategy_filter: Optional[str] = None, top_n: int = 20) -> List[Dict]:
        """Analyze performance by symbol with PF and Avg R."""
        trades = self._get_closed_trades(strategy_filter)
        if not trades:
            return []

        symbols = {}
        for t in trades:
            s = t.get("symbol") or "unknown"
            if s not in symbols:
                symbols[s] = []
            symbols[s].append(t)

        results = []
        for symbol, st in symbols.items():
            pnls = [t["pnl"] or 0 for t in st]
            n = len(pnls)
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            wr = len(wins) / n * 100 if n else 0
            total_pnl = sum(pnls)
            
            # Profit Factor
            gp = sum(wins) if wins else 0
            gl = sum(abs(l) for l in losses) if losses else 0
            pf = gp / gl if gl > 0 else float('inf') if gp > 0 else 0
            
            # Average R
            rs = [t["realized_r"] for t in st if t.get("realized_r") is not None and t["realized_r"] != 0]
            avg_r = sum(rs) / len(rs) if rs else 0
            
            # Expectancy
            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(abs(l) for l in losses) / len(losses) if losses else 0
            exp = (wr / 100 * avg_win) - ((100 - wr) / 100 * avg_loss)

            results.append({
                "symbol": symbol,
                "trades": n,
                "win_rate": round(wr, 1),
                "pf": round(pf, 2),
                "expectancy": round(exp, 4),
                "avg_r": round(avg_r, 2),
                "pnl": round(total_pnl, 2),
            })

        results.sort(key=lambda x: -x["pnl"])
        return results[:top_n]
    
    # ── Largest Winners/Losers ────────────────────────────────────

    def largest_trades(self, strategy_filter: Optional[str] = None, top_n: int = 5) -> Dict:
        """Get largest winners and losers."""
        trades = self._get_closed_trades(strategy_filter)
        if not trades:
            return {"winners": [], "losers": []}
        
        # Sort by PnL
        sorted_trades = sorted(trades, key=lambda x: x.get("pnl") or 0, reverse=True)
        
        winners = []
        for t in sorted_trades[:top_n]:
            winners.append({
                "symbol": t.get("symbol"),
                "side": t.get("side"),
                "pnl": round(t.get("pnl") or 0, 4),
                "realized_r": round(t.get("realized_r") or 0, 2),
                "confidence": round((t.get("confidence") or 0) * 100, 1),
                "hold_minutes": t.get("hold_minutes"),
                "exit_reason": t.get("exit_reason"),
            })
        
        losers = []
        for t in sorted_trades[-top_n:]:
            losers.append({
                "symbol": t.get("symbol"),
                "side": t.get("side"),
                "pnl": round(t.get("pnl") or 0, 4),
                "realized_r": round(t.get("realized_r") or 0, 2),
                "confidence": round((t.get("confidence") or 0) * 100, 1),
                "hold_minutes": t.get("hold_minutes"),
                "exit_reason": t.get("exit_reason"),
            })
        
        return {"winners": winners, "losers": losers}

    # ── Equity Curve Data ─────────────────────────────────────────

    def equity_curve(self, strategy_filter: Optional[str] = None) -> List[Dict]:
        """Generate equity curve data points."""
        trades = self._get_closed_trades(strategy_filter)
        if not trades:
            return []

        cum_pnl = 0.0
        peak = 0.0
        curve = []

        for t in trades:
            pnl = t["pnl"] or 0
            cum_pnl += pnl
            peak = max(peak, cum_pnl)
            dd = peak - cum_pnl

            curve.append({
                "trade_num": len(curve) + 1,
                "symbol": t.get("symbol"),
                "pnl": round(pnl, 4),
                "cum_pnl": round(cum_pnl, 4),
                "drawdown": round(dd, 4),
                "timestamp": t.get("closed_at"),
            })

        return curve

    # ── Rolling Metrics ───────────────────────────────────────────

    def rolling_metrics(self, window: int = 20, strategy_filter: Optional[str] = None) -> List[Dict]:
        """Calculate rolling metrics over a window of trades."""
        trades = self._get_closed_trades(strategy_filter)
        if len(trades) < window:
            return []

        pnls = [t["pnl"] or 0 for t in trades]
        results = []

        for i in range(window, len(pnls) + 1):
            window_pnls = pnls[i - window:i]
            n = window
            wins = [p for p in window_pnls if p > 0]
            losses = [p for p in window_pnls if p <= 0]

            wr = len(wins) / n * 100 if n else 0
            gp = sum(wins) if wins else 0
            gl = sum(abs(l) for l in losses) if losses else 0
            pf = gp / gl if gl > 0 else 0

            mean = sum(window_pnls) / n
            std = math.sqrt(sum((p - mean) ** 2 for p in window_pnls) / (n - 1)) if n > 1 else 0
            sharpe = (mean / std) * math.sqrt(252) if std > 0 else 0

            avg_win = sum(wins) / len(wins) if wins else 0
            avg_loss = sum(abs(l) for l in losses) / len(losses) if losses else 0
            exp = (wr / 100 * avg_win) - ((100 - wr) / 100 * avg_loss)

            results.append({
                "end_trade": i,
                "window": window,
                "win_rate": round(wr, 1),
                "pf": round(pf, 2),
                "expectancy": round(exp, 4),
                "sharpe": round(sharpe, 2),
                "pnl": round(sum(window_pnls), 2),
            })

        return results

    # ── Complete Report ───────────────────────────────────────────

    def full_report(self, strategy_filter: Optional[str] = None) -> Dict:
        """Generate the complete analytics report."""
        return {
            "core": self.core_metrics(strategy_filter),
            "confidence_buckets": self.confidence_buckets(strategy_filter),
            "sessions": self.session_analysis(strategy_filter),
            "regimes": self.regime_analysis(strategy_filter),
            "exits": self.exit_analysis(strategy_filter),
            "symbols": self.symbol_analysis(strategy_filter),
            "largest_trades": self.largest_trades(strategy_filter),
            "equity_curve": self.equity_curve(strategy_filter),
            "rolling": self.rolling_metrics(strategy_filter=strategy_filter),
        }
