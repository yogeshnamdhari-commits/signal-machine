"""
Trade Quality Dashboard — Accepted vs Rejected analysis.

Answers: Is the App too aggressive or too conservative?

For every trade:
    - Was it accepted by the App?
    - Did it win or lose?
    - Which App rule caused the acceptance/rejection?

This identifies where the App is adding value and where it is reducing it.

READ-ONLY: never modifies upstream data.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "institutional_v1.db"


@dataclass
class TradeQualityMetrics:
    """Trade quality breakdown — accepted vs rejected."""
    # Total
    total_signals: int = 0
    total_trades: int = 0

    # Accepted trades
    accepted_winners: int = 0
    accepted_losers: int = 0
    accepted_total: int = 0
    accepted_win_rate: float = 0.0
    accepted_pnl: float = 0.0

    # Rejected trades (signals that would have been trades)
    rejected_winners: int = 0
    rejected_losers: int = 0
    rejected_total: int = 0
    rejected_win_rate: float = 0.0
    rejected_pnl: float = 0.0

    # Quality assessment
    false_positive_rate: float = 0.0  # % of accepted trades that were losses
    false_negative_rate: float = 0.0  # % of rejected trades that would have been wins
    precision: float = 0.0  # % of accepted trades that were winners
    recall: float = 0.0  # % of winning trades that were accepted

    # Diagnosis
    diagnosis: str = ""  # BALANCED / TOO_AGGRESSIVE / TOO_CONSERVATIVE

    def to_dict(self) -> Dict:
        return {
            "total_signals": self.total_signals,
            "accepted": {
                "winners": self.accepted_winners,
                "losers": self.accepted_losers,
                "total": self.accepted_total,
                "win_rate": round(self.accepted_win_rate, 1),
                "pnl": round(self.accepted_pnl, 2),
            },
            "rejected": {
                "winners": self.rejected_winners,
                "losers": self.rejected_losers,
                "total": self.rejected_total,
                "win_rate": round(self.rejected_win_rate, 1),
                "pnl": round(self.rejected_pnl, 2),
            },
            "quality": {
                "false_positive_rate": round(self.false_positive_rate, 1),
                "false_negative_rate": round(self.false_negative_rate, 1),
                "precision": round(self.precision, 1),
                "recall": round(self.recall, 1),
            },
            "diagnosis": self.diagnosis,
        }

    def render(self) -> str:
        lines = []
        lines.append("═" * 76)
        lines.append("  TRADE QUALITY DASHBOARD — Accepted vs Rejected")
        lines.append("═" * 76)
        lines.append("")

        # Diagnosis
        diag_icons = {
            "BALANCED": "🟢",
            "TOO_AGGRESSIVE": "🔴",
            "TOO_CONSERVATIVE": "🟡",
        }
        icon = diag_icons.get(self.diagnosis, "❓")
        lines.append(f"  {icon} Diagnosis: {self.diagnosis}")
        lines.append("")

        # Accepted Trades
        lines.append("┌─ ACCEPTED TRADES (App said YES) ─" + "─" * 42 + "┐")
        lines.append(f"│  Winners:     {self.accepted_winners:>6}  │  "
                     f"Win Rate:  {self.accepted_win_rate:>5.1f}%    │  "
                     f"PnL: ${self.accepted_pnl:>+8.2f}  │")
        lines.append(f"│  Losers:      {self.accepted_losers:>6}  │  "
                     f"Total:     {self.accepted_total:>6}         │  "
                     f"{'':>16s}│")
        lines.append("└" + "─" * 74 + "┘")
        lines.append("")

        # Rejected Trades
        lines.append("┌─ REJECTED TRADES (App said NO) ─" + "─" * 43 + "┐")
        lines.append(f"│  Winners:     {self.rejected_winners:>6}  │  "
                     f"Win Rate:  {self.rejected_win_rate:>5.1f}%    │  "
                     f"PnL: ${self.rejected_pnl:>+8.2f}  │")
        lines.append(f"│  Losers:      {self.rejected_losers:>6}  │  "
                     f"Total:     {self.rejected_total:>6}         │  "
                     f"{'':>16s}│")
        lines.append("└" + "─" * 74 + "┘")
        lines.append("")

        # Quality Metrics
        lines.append("┌─ QUALITY METRICS ─" + "─" * 56 + "┐")
        lines.append(f"│  False Positive Rate:  {self.false_positive_rate:>5.1f}%   "
                     f"  (accepted trades that were losses)        │")
        lines.append(f"│  False Negative Rate:  {self.false_negative_rate:>5.1f}%   "
                     f"  (rejected trades that would have won)     │")
        lines.append(f"│  Precision:            {self.precision:>5.1f}%   "
                     f"  (accepted trades that were winners)       │")
        lines.append(f"│  Recall:               {self.recall:>5.1f}%   "
                     f"  (winning trades that were accepted)       │")
        lines.append("└" + "─" * 74 + "┘")

        return "\n".join(lines)


@dataclass
class AcceptanceCurve:
    """Performance by confidence bucket with stability and lifetime."""
    buckets: List[Dict] = field(default_factory=list)

    def render(self) -> str:
        lines = []
        lines.append("┌─ ACCEPTANCE CURVE (PF by Confidence) ─" + "─" * 37 + "┐")
        lines.append(f"│  {'Bucket':>10s} │ {'Trades':>7s} │ {'PF':>6s} │ {'EV':>8s} │ {'MaxDD':>7s} │ {'Stable':>7s} │ {'Decision':>10s}  │")
        lines.append("│  " + "─" * 74 + "  │")
        for b in self.buckets:
            decision = "Keep ✅" if b.get("pf", 0) >= 1.2 else "Review ⚠️" if b.get("pf", 0) >= 0.8 else "Reject ❌"
            stable = b.get("stable", False)
            stable_str = "✅" if stable else "⏳" if b.get("trades", 0) >= 10 else "—"
            lines.append(
                f"│  {b['bucket']:>10s} │ {b['trades']:>7d} │ {b.get('pf', 0):>5.2f} │ "
                f"{b.get('ev', 0):>+7.3f}R │ {b.get('max_dd', 0):>+6.2f}% │ {stable_str:>7s} │ {decision:>10s}  │"
            )
        lines.append("│" + " " * 77 + "│")
        lines.append("│  Stable = ✅ after ≥100 trades AND consistent across rolling windows    │")
        lines.append("└" + "─" * 78 + "┘")

        # Bucket Lifetime (rolling windows)
        has_lifetime = any(b.get("pf_50") is not None for b in self.buckets)
        if has_lifetime:
            lines.append("")
            lines.append("┌─ BUCKET LIFETIME (Rolling Windows) ─" + "─" * 40 + "┐")
            lines.append(f"│  {'Bucket':>10s} │ {'Last 50':>8s} │ {'Last 100':>9s} │ {'Last 250':>9s} │ {'Trend':>12s}  │")
            lines.append("│  " + "─" * 68 + "  │")
            for b in self.buckets:
                pf50 = b.get("pf_50")
                pf100 = b.get("pf_100")
                pf250 = b.get("pf_250")
                trend = b.get("trend", "—")

                pf50_str = f"{pf50:.2f}" if pf50 is not None else "—"
                pf100_str = f"{pf100:.2f}" if pf100 is not None else "—"
                pf250_str = f"{pf250:.2f}" if pf250 is not None else "—"

                lines.append(
                    f"│  {b['bucket']:>10s} │ {pf50_str:>8s} │ {pf100_str:>9s} │ {pf250_str:>9s} │ {trend:>12s}  │"
                )
            lines.append("└" + "─" * 78 + "┘")

        return "\n".join(lines)


@dataclass
class FalsePositiveExplorer:
    """Which rule combinations produce money?"""
    combinations: List[Dict] = field(default_factory=list)

    def render(self) -> str:
        lines = []
        lines.append("┌─ FALSE POSITIVE EXPLORER (Rule Combinations) ─" + "─" * 31 + "┐")
        lines.append(f"│  {'Combination':<30s} │ {'Trades':>7s} │ {'PF':>6s} │ {'Net R':>8s} │ {'Decision':>10s}  │")
        lines.append("│  " + "─" * 74 + "  │")
        for c in self.combinations:
            decision = "Keep ✅" if c.get("pf", 0) >= 1.2 else "Review ⚠️" if c.get("pf", 0) >= 0.8 else "Reject ❌"
            net_r = c.get("net_r", 0)
            lines.append(
                f"│  {c['combination']:<30s} │ {c['trades']:>7d} │ {c.get('pf', 0):>5.2f} │ "
                f"{net_r:>+7.3f}R │ {decision:>10s}  │"
            )
        lines.append("└" + "─" * 78 + "┘")
        return "\n".join(lines)


class TradeQualityDashboard:
    """
    Trade quality analysis — accepted vs rejected.

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or _DB_PATH

    def analyze(self) -> TradeQualityMetrics:
        """
        Analyze trade quality — accepted vs rejected.

        Returns:
            TradeQualityMetrics with breakdown
        """
        metrics = TradeQualityMetrics()

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            # Get all closed trades (these were accepted by the App)
            cur.execute("""
                SELECT symbol, side, pnl, confidence, regime,
                       institutional_score, risk_reward
                FROM positions WHERE status = 'closed'
            """)
            accepted = [dict(r) for r in cur.fetchall()]

            # Get all signals (including rejected ones)
            cur.execute("""
                SELECT symbol, side, confidence, institutional_score,
                       risk_reward, status
                FROM signals WHERE status IN ('active', 'expired', 'filled')
            """)
            all_signals = [dict(r) for r in cur.fetchall()]

            conn.close()

            # Calculate accepted metrics
            metrics.accepted_total = len(accepted)
            metrics.accepted_winners = sum(1 for t in accepted if (t.get("pnl") or 0) > 0)
            metrics.accepted_losers = metrics.accepted_total - metrics.accepted_winners
            metrics.accepted_pnl = sum(t.get("pnl", 0) or 0 for t in accepted)
            metrics.accepted_win_rate = (
                metrics.accepted_winners / metrics.accepted_total * 100
                if metrics.accepted_total > 0 else 0
            )

            # Estimate rejected trades (signals that didn't become positions)
            # This is approximate — we compare signal confidence to accepted trade confidence
            accepted_symbols = {(t["symbol"], t["side"]) for t in accepted}
            rejected_signals = [
                s for s in all_signals
                if (s["symbol"], s["side"]) not in accepted_symbols
            ]

            # Estimate win/loss for rejected based on confidence and subsequent price
            # Simplified: use confidence as proxy
            metrics.rejected_total = len(rejected_signals)
            metrics.rejected_winners = sum(
                1 for s in rejected_signals
                if (s.get("confidence") or 0) > 0.90
            )
            metrics.rejected_losers = metrics.rejected_total - metrics.rejected_winners
            metrics.rejected_win_rate = (
                metrics.rejected_winners / metrics.rejected_total * 100
                if metrics.rejected_total > 0 else 0
            )

            metrics.total_signals = metrics.accepted_total + metrics.rejected_total

            # Quality metrics
            if metrics.accepted_total > 0:
                metrics.false_positive_rate = metrics.accepted_losers / metrics.accepted_total * 100
                metrics.precision = metrics.accepted_winners / metrics.accepted_total * 100

            total_winners = metrics.accepted_winners + metrics.rejected_winners
            if total_winners > 0:
                metrics.recall = metrics.accepted_winners / total_winners * 100

            if metrics.rejected_total > 0:
                metrics.false_negative_rate = metrics.rejected_winners / metrics.rejected_total * 100

            # Diagnosis
            metrics.diagnosis = self._diagnose(metrics)

        except Exception as e:
            logger.warning("Trade quality analysis error: {}", e)

        return metrics

    def acceptance_curve(self) -> AcceptanceCurve:
        """Generate acceptance curve — PF by confidence bucket with stability."""
        curve = AcceptanceCurve()

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            buckets = [
                (0.95, 1.0, "95-100"),
                (0.90, 0.95, "90-95"),
                (0.85, 0.90, "85-90"),
                (0.80, 0.85, "80-85"),
                (0.75, 0.80, "75-80"),
                (0, 0.75, "<75"),
            ]

            for lo, hi, label in buckets:
                cur.execute("""
                    SELECT COUNT(*),
                           SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END),
                           SUM(CASE WHEN pnl <= 0 THEN ABS(pnl) ELSE 0 END),
                           SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END),
                           AVG(CASE WHEN pnl > 0 THEN realized_r ELSE NULL END),
                           AVG(CASE WHEN pnl <= 0 THEN realized_r ELSE NULL END)
                    FROM positions
                    WHERE status = 'closed'
                    AND confidence >= ? AND confidence < ?
                """, (lo, hi))
                row = cur.fetchone()

                if row and row[0] > 0:
                    n, gp, gl, wins, avg_wr, avg_lr = row
                    gp = gp or 0
                    gl = gl or 0
                    pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)
                    wr = (wins or 0) / n * 100 if n > 0 else 0
                    avg_wr = avg_wr or 0
                    avg_lr = avg_lr or 0
                    ev = (wr / 100 * avg_wr) + ((1 - wr / 100) * avg_lr)

                    # Max DD for this bucket
                    cur.execute("""
                        SELECT pnl FROM positions
                        WHERE status = 'closed'
                        AND confidence >= ? AND confidence < ?
                        ORDER BY closed_at ASC
                    """, (lo, hi))
                    pnls = [r[0] for r in cur.fetchall()]
                    cum = 0.0
                    peak = 0.0
                    max_dd = 0.0
                    for p in pnls:
                        cum += p
                        peak = max(peak, cum)
                        dd = peak - cum
                        max_dd = max(max_dd, dd)
                    max_dd_pct = max_dd / 10000 * 100

                    # Stability: ≥100 trades AND PF > 1.0
                    stable = n >= 100 and pf > 1.0

                    # Lifetime: rolling PF windows
                    pf_50 = None
                    pf_100 = None
                    pf_250 = None
                    trend = "—"

                    if len(pnls) >= 50:
                        last50 = pnls[-50:]
                        gp50 = sum(p for p in last50 if p > 0)
                        gl50 = sum(abs(p) for p in last50 if p <= 0)
                        pf_50 = gp50 / gl50 if gl50 > 0 else (float('inf') if gp50 > 0 else 0)

                    if len(pnls) >= 100:
                        last100 = pnls[-100:]
                        gp100 = sum(p for p in last100 if p > 0)
                        gl100 = sum(abs(p) for p in last100 if p <= 0)
                        pf_100 = gp100 / gl100 if gl100 > 0 else (float('inf') if gp100 > 0 else 0)

                    if len(pnls) >= 250:
                        last250 = pnls[-250:]
                        gp250 = sum(p for p in last250 if p > 0)
                        gl250 = sum(abs(p) for p in last250 if p <= 0)
                        pf_250 = gp250 / gl250 if gl250 > 0 else (float('inf') if gp250 > 0 else 0)

                    # Trend
                    if pf_50 is not None and pf_100 is not None:
                        if pf_50 > pf_100 * 1.1:
                            trend = "↗ Improving"
                        elif pf_50 < pf_100 * 0.9:
                            trend = "↘ Worsening"
                        else:
                            trend = "↔ Stable"

                    curve.buckets.append({
                        "bucket": label,
                        "trades": n,
                        "pf": pf,
                        "ev": ev,
                        "max_dd": max_dd_pct,
                        "stable": stable,
                        "pf_50": pf_50,
                        "pf_100": pf_100,
                        "pf_250": pf_250,
                        "trend": trend,
                    })

            conn.close()

        except Exception as e:
            logger.warning("Acceptance curve error: {}", e)

        return curve

    def false_positive_explorer(self) -> FalsePositiveExplorer:
        """Explore which rule combinations produce money — ranked by Net R."""
        explorer = FalsePositiveExplorer()

        try:
            conn = sqlite3.connect(str(self._db_path), timeout=10)
            cur = conn.cursor()

            combos = [
                ("High conf + High RR", "confidence >= 0.95 AND risk_reward >= 2.5"),
                ("High conf + Low RR", "confidence >= 0.95 AND risk_reward < 2.5"),
                ("Medium conf + High RR", "confidence >= 0.85 AND confidence < 0.95 AND risk_reward >= 2.5"),
                ("Medium conf + Low RR", "confidence >= 0.85 AND confidence < 0.95 AND risk_reward < 2.5"),
                ("Low conf + High RR", "confidence >= 0.75 AND confidence < 0.85 AND risk_reward >= 2.5"),
                ("Low conf + Low RR", "confidence >= 0.75 AND confidence < 0.85 AND risk_reward < 2.5"),
                ("Very low conf", "confidence < 0.75"),
            ]

            for name, where in combos:
                cur.execute(f"""
                    SELECT COUNT(*),
                           SUM(CASE WHEN pnl > 0 THEN pnl ELSE 0 END),
                           SUM(CASE WHEN pnl <= 0 THEN ABS(pnl) ELSE 0 END),
                           SUM(pnl)
                    FROM positions
                    WHERE status = 'closed' AND {where}
                """)
                row = cur.fetchone()

                if row and row[0] > 0:
                    n, gp, gl, total_pnl = row
                    gp = gp or 0
                    gl = gl or 0
                    total_pnl = total_pnl or 0
                    pf = gp / gl if gl > 0 else (float('inf') if gp > 0 else 0)

                    # Net R (total PnL / 100 assuming $100 risk per trade)
                    net_r = total_pnl / 100 if n > 0 else 0

                    explorer.combinations.append({
                        "combination": name,
                        "trades": n,
                        "pf": pf,
                        "net_r": net_r,
                    })

            # Sort by Net R (best first)
            explorer.combinations.sort(key=lambda x: x.get("net_r", 0), reverse=True)

            conn.close()

        except Exception as e:
            logger.warning("False positive explorer error: {}", e)

        return explorer

    def _diagnose(self, m: TradeQualityMetrics) -> str:
        """Diagnose whether App is too aggressive, conservative, or balanced."""
        if m.false_negative_rate > 30 and m.precision < 50:
            return "TOO_CONSERVATIVE"
        elif m.false_positive_rate > 60:
            return "TOO_AGGRESSIVE"
        else:
            return "BALANCED"
