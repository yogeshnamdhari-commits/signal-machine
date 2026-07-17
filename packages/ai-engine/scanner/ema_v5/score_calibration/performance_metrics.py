"""
Performance Metrics Calculator — Win rate, profit factor, expectancy,
maximum drawdown, average R multiple, Sharpe ratio, Sortino ratio.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


class PerformanceMetrics:
    """Calculates comprehensive performance metrics from trade history."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "institutional_v1.db"

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = db_path or self.DB_PATH
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.row_factory = sqlite3.Row

    def calculate(self, strategy_filter: Optional[str] = None) -> Dict:
        """Calculate all performance metrics, optionally filtered by strategy."""
        cur = self._conn.cursor()

        query = """
            SELECT symbol, side, entry_price, pnl, fees, exit_reason,
                   confidence, strategy_version, opened_at, closed_at,
                   hold_minutes, mfe_pct, mae_pct, realized_r, planned_rr,
                   highest_pnl, leverage, quantity
            FROM positions WHERE status = 'closed'
        """
        params = []
        if strategy_filter:
            query += " AND strategy_version = ?"
            params.append(strategy_filter)
        query += " ORDER BY closed_at ASC"

        cur.execute(query, params)
        rows = cur.fetchall()

        if not rows:
            return {"status": "no_data", "message": "No closed trades"}

        # Extract PnL series
        pnls = [r["pnl"] or 0 for r in rows]
        fees = [r["fees"] or 0 for r in rows]
        confidences = [(r["confidence"] or 0) * 100 for r in rows]  # Convert to 0-100
        hold_times = [r["hold_minutes"] or 0 for r in rows]
        r_multiples = [r["realized_r"] or 0 for r in rows if r["realized_r"] is not None]

        # Basic metrics
        total_trades = len(pnls)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        win_count = len(wins)
        loss_count = len(losses)
        win_rate = win_count / total_trades * 100 if total_trades else 0

        total_pnl = sum(pnls)
        total_fees = sum(fees)
        net_pnl = total_pnl - total_fees

        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(abs(l) for l in losses) / len(losses) if losses else 0
        avg_pnl = sum(pnls) / total_trades if total_trades else 0

        # Profit factor
        gross_profit = sum(wins) if wins else 0
        gross_loss = sum(abs(l) for l in losses) if losses else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf') if gross_profit > 0 else 0

        # Expectancy
        expectancy = (win_rate / 100 * avg_win) - ((100 - win_rate) / 100 * avg_loss)

        # Payoff ratio
        payoff = avg_win / avg_loss if avg_loss > 0 else float('inf')

        # Kelly criterion
        kelly = (win_rate / 100 * payoff - (1 - win_rate / 100)) / payoff if payoff > 0 else 0

        # Maximum drawdown
        cumulative = 0
        peak = 0
        max_dd = 0
        max_dd_pct = 0
        equity_curve = [0]
        for p in pnls:
            cumulative += p
            equity_curve.append(cumulative)
            peak = max(peak, cumulative)
            dd = peak - cumulative
            max_dd = max(max_dd, dd)
            max_dd_pct = max(max_dd_pct, (dd / peak * 100) if peak > 0 else 0)

        # Sharpe ratio (annualized, assuming ~4 trades/day)
        if len(pnls) > 1:
            mean_ret = sum(pnls) / len(pnls)
            std_ret = math.sqrt(sum((p - mean_ret) ** 2 for p in pnls) / (len(pnls) - 1))
            sharpe = (mean_ret / std_ret) * math.sqrt(252) if std_ret > 0 else 0
        else:
            sharpe = 0

        # Sortino ratio (downside deviation)
        downside = [p for p in pnls if p < 0]
        if len(downside) > 1:
            ds_mean = sum(downside) / len(downside)
            ds_std = math.sqrt(sum((p - ds_mean) ** 2 for p in downside) / (len(downside) - 1))
            sortino = (mean_ret / ds_std) * math.sqrt(252) if ds_std > 0 else 0
        else:
            sortino = sharpe

        # Calmar ratio
        calmar = (total_pnl / max_dd) if max_dd > 0 else 0

        # Average R multiple
        avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0
        max_r = max(r_multiples) if r_multiples else 0
        min_r = min(r_multiples) if r_multiples else 0

        # Consecutive wins/losses
        max_consec_wins = 0
        max_consec_losses = 0
        current_wins = 0
        current_losses = 0
        for p in pnls:
            if p > 0:
                current_wins += 1
                current_losses = 0
                max_consec_wins = max(max_consec_wins, current_wins)
            else:
                current_losses += 1
                current_wins = 0
                max_consec_losses = max(max_consec_losses, current_losses)

        # Recovery factor
        recovery_factor = total_pnl / max_dd if max_dd > 0 else 0

        # Average hold time
        avg_hold = sum(hold_times) / len(hold_times) if hold_times else 0

        # Confidence correlation
        if len(confidences) > 1:
            pairs = list(zip(confidences, pnls))
            corr = self._pearson(pairs)
        else:
            corr = 0

        return {
            "status": "complete",
            "total_trades": total_trades,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": round(win_rate, 1),
            "total_pnl": round(total_pnl, 3),
            "total_fees": round(total_fees, 4),
            "net_pnl": round(net_pnl, 3),
            "avg_pnl": round(avg_pnl, 3),
            "avg_win": round(avg_win, 3),
            "avg_loss": round(avg_loss, 3),
            "profit_factor": round(profit_factor, 2),
            "expectancy": round(expectancy, 3),
            "payoff_ratio": round(payoff, 2),
            "kelly_criterion": round(kelly, 4),
            "max_drawdown": round(max_dd, 3),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "sharpe_ratio": round(sharpe, 3),
            "sortino_ratio": round(sortino, 3),
            "calmar_ratio": round(calmar, 3),
            "recovery_factor": round(recovery_factor, 3),
            "avg_r_multiple": round(avg_r, 3),
            "max_r": round(max_r, 3),
            "min_r": round(min_r, 3),
            "max_consec_wins": max_consec_wins,
            "max_consec_losses": max_consec_losses,
            "avg_hold_minutes": round(avg_hold, 1),
            "confidence_correlation": round(corr, 4),
            "equity_curve": equity_curve,
        }

    def by_confidence_bucket(self) -> List[Dict]:
        """Performance metrics broken down by confidence bucket."""
        cur = self._conn.cursor()
        cur.execute("""
            SELECT confidence * 100 as conf_pct, pnl, exit_reason, hold_minutes,
                   realized_r, mfe_pct, mae_pct, strategy_version
            FROM positions WHERE status = 'closed'
            ORDER BY conf_pct DESC
        """)
        rows = cur.fetchall()

        buckets = {
            "95-100": [], "90-94": [], "85-89": [], "80-84": [], "75-79": [], "<75": []
        }

        for r in rows:
            conf = r["conf_pct"] or 0
            if conf >= 95: buckets["95-100"].append(r)
            elif conf >= 90: buckets["90-94"].append(r)
            elif conf >= 85: buckets["85-89"].append(r)
            elif conf >= 80: buckets["80-84"].append(r)
            elif conf >= 75: buckets["75-79"].append(r)
            else: buckets["<75"].append(r)

        results = []
        for bucket_name, trades in sorted(buckets.items(), reverse=True):
            if not trades:
                continue
            pnls = [t["pnl"] or 0 for t in trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            r_multiples = [t["realized_r"] or 0 for t in trades if t["realized_r"] is not None]
            mfes = [t["mfe_pct"] or 0 for t in trades if t["mfe_pct"] is not None]
            maes = [t["mae_pct"] or 0 for t in trades if t["mae_pct"] is not None]

            total = len(pnls)
            wr = len(wins) / total * 100 if total else 0
            gp = sum(wins)
            gl = sum(abs(l) for l in losses)
            pf = gp / gl if gl > 0 else 0
            avg_r = sum(r_multiples) / len(r_multiples) if r_multiples else 0
            avg_mfe = sum(mfes) / len(mfes) if mfes else 0
            avg_mae = sum(maes) / len(maes) if maes else 0

            results.append({
                "bucket": bucket_name,
                "trades": total,
                "win_rate": round(wr, 1),
                "total_pnl": round(sum(pnls), 3),
                "avg_pnl": round(sum(pnls) / total, 3) if total else 0,
                "profit_factor": round(pf, 2),
                "avg_r": round(avg_r, 3),
                "avg_mfe": round(avg_mfe, 3),
                "avg_mae": round(avg_mae, 3),
            })

        return results

    def by_exit_reason(self) -> List[Dict]:
        """Performance metrics broken down by exit reason."""
        cur = self._conn.cursor()
        cur.execute("""
            SELECT exit_reason, pnl, confidence, hold_minutes, realized_r
            FROM positions WHERE status = 'closed'
        """)
        rows = cur.fetchall()

        reasons = {}
        for r in rows:
            reason = r["exit_reason"] or "unknown"
            if reason not in reasons:
                reasons[reason] = []
            reasons[reason].append(r)

        results = []
        for reason, trades in sorted(reasons.items(), key=lambda x: len(x[1]), reverse=True):
            pnls = [t["pnl"] or 0 for t in trades]
            wins = [p for p in pnls if p > 0]
            total = len(pnls)
            wr = len(wins) / total * 100 if total else 0

            results.append({
                "reason": reason,
                "trades": total,
                "win_rate": round(wr, 1),
                "total_pnl": round(sum(pnls), 3),
                "avg_pnl": round(sum(pnls) / total, 3) if total else 0,
            })

        return results

    @staticmethod
    def _pearson(pairs):
        n = len(pairs)
        if n < 3:
            return 0.0
        x = [p[0] for p in pairs]
        y = [p[1] for p in pairs]
        mx = sum(x) / n
        my = sum(y) / n
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y)) / n
        sx = math.sqrt(sum((xi - mx) ** 2 for xi, yi in zip(x, y)) / n)
        sy = math.sqrt(sum((yi - my) ** 2 for xi, yi in zip(x, y)) / n)
        if sx * sy == 0:
            return 0.0
        return cov / (sx * sy)

    def close(self) -> None:
        self._conn.close()
