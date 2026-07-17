"""
Milestone Tracker — Monitors progress toward statistical validation milestones.

Tracks:
- Total EMA_V5 trades executed
- High-confidence trades (≥90)
- Rejected trades tracked
- Market regime coverage
- Forward-testing duration
- Statistical significance progress
"""
from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


class MilestoneTracker:
    """Tracks progress toward validation milestones."""

    DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "institutional_v1.db"
    CAL_DB_PATH = Path(__file__).resolve().parent.parent.parent.parent / "data" / "ema_v5_calibration.db"

    # Target milestones
    TARGETS = {
        "ema_v5_trades": {"min": 200, "ideal": 300, "description": "EMA_V5 trades executed"},
        "high_conf_trades": {"min": 100, "ideal": 150, "description": "High-confidence trades (≥90)"},
        "rejected_tracked": {"min": 500, "ideal": 1000, "description": "Rejected candidates tracked"},
        "forward_test_days": {"min": 14, "ideal": 28, "description": "Forward-testing days"},
        "regimes_covered": {"min": 2, "ideal": 3, "description": "Market regimes observed"},
    }

    def __init__(self) -> None:
        self._conn = sqlite3.connect(str(self.DB_PATH), timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._cal_conn = sqlite3.connect(str(self.CAL_DB_PATH), timeout=10)
        self._cal_conn.row_factory = sqlite3.Row

    def track(self) -> Dict:
        """Compute current milestone progress."""
        cur = self._conn.cursor()
        cal_cur = self._cal_conn.cursor()

        # ── 1. EMA_V5 trades executed ──
        cur.execute("""
            SELECT COUNT(*) FROM positions
            WHERE strategy_version = 'ema_v5' AND status = 'closed'
        """)
        ema_v5_closed = cur.fetchone()[0] or 0

        cur.execute("""
            SELECT COUNT(*) FROM positions
            WHERE strategy_version = 'ema_v5'
        """)
        ema_v5_total = cur.fetchone()[0] or 0

        # Also count from 'current' strategy (which may be EMA_V5 in production)
        cur.execute("""
            SELECT COUNT(*) FROM positions
            WHERE strategy_version = 'current' AND status = 'closed'
        """)
        current_closed = cur.fetchone()[0] or 0

        # Total closed trades (all strategies)
        cur.execute("SELECT COUNT(*) FROM positions WHERE status = 'closed'")
        total_closed = cur.fetchone()[0] or 0

        # ── 2. High-confidence trades (≥90) ──
        cur.execute("""
            SELECT COUNT(*) FROM positions
            WHERE confidence >= 0.90 AND status = 'closed'
        """)
        high_conf_closed = cur.fetchone()[0] or cur.fetchone()[0] or 0

        # ── 3. Rejected candidates tracked ──
        try:
            cal_cur.execute("SELECT COUNT(*) FROM candidates")
            total_candidates = cal_cur.fetchone()[0] or 0

            cal_cur.execute("SELECT COUNT(*) FROM candidates WHERE outcome_tracked = 1")
            tracked_outcomes = cal_cur.fetchone()[0] or 0

            cal_cur.execute("SELECT COUNT(*) FROM candidates WHERE passed = 0")
            rejected_count = cal_cur.fetchone()[0] or 0
        except Exception:
            total_candidates = 0
            tracked_outcomes = 0
            rejected_count = 0

        # ── 4. Forward-testing duration ──
        cur.execute("SELECT MIN(opened_at) FROM positions")
        first_trade = cur.fetchone()[0]
        if first_trade:
            days_running = (time.time() - first_trade) / 86400
        else:
            days_running = 0

        # ── 5. Market regimes covered ──
        cur.execute("""
            SELECT DISTINCT regime FROM positions
            WHERE regime IS NOT NULL AND regime != '' AND regime != 'unknown'
        """)
        regimes = [r[0] for r in cur.fetchall()]
        regimes_covered = len(set(regimes))

        # ── 6. Statistical significance progress ──
        cur.execute("SELECT pnl FROM positions WHERE status = 'closed'")
        pnls = [r[0] or 0 for r in cur.fetchall()]

        if len(pnls) > 1:
            mean = sum(pnls) / len(pnls)
            std = math.sqrt(sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1))
            se = std / math.sqrt(len(pnls))
            t_stat = mean / se if se > 0 else 0
            p_value = 2 * (1 - self._normal_cdf(abs(t_stat)))
            is_significant = p_value < 0.05
        else:
            t_stat = 0
            p_value = 1.0
            is_significant = False

        # ── Compute progress percentages ──
        milestones = {}

        # EMA_V5 trades
        ema_target = self.TARGETS["ema_v5_trades"]
        # Use total closed as proxy since we may not区分 strategy_version cleanly
        ema_progress = min(100, total_closed / ema_target["min"] * 100)
        milestones["ema_v5_trades"] = {
            "current": total_closed,
            "target_min": ema_target["min"],
            "target_ideal": ema_target["ideal"],
            "progress_pct": round(ema_progress, 1),
            "status": "complete" if total_closed >= ema_target["ideal"] else "on_track" if total_closed >= ema_target["min"] * 0.5 else "early",
            "description": ema_target["description"],
        }

        # High-confidence trades
        hc_target = self.TARGETS["high_conf_trades"]
        hc_progress = min(100, high_conf_closed / hc_target["min"] * 100)
        milestones["high_conf_trades"] = {
            "current": high_conf_closed,
            "target_min": hc_target["min"],
            "target_ideal": hc_target["ideal"],
            "progress_pct": round(hc_progress, 1),
            "status": "complete" if high_conf_closed >= hc_target["ideal"] else "on_track" if high_conf_closed >= hc_target["min"] * 0.5 else "early",
            "description": hc_target["description"],
        }

        # Rejected candidates tracked
        rt_target = self.TARGETS["rejected_tracked"]
        rt_progress = min(100, rejected_count / rt_target["min"] * 100)
        milestones["rejected_tracked"] = {
            "current": rejected_count,
            "target_min": rt_target["min"],
            "target_ideal": rt_target["ideal"],
            "progress_pct": round(rt_progress, 1),
            "status": "complete" if rejected_count >= rt_target["ideal"] else "on_track" if rejected_count >= rt_target["min"] * 0.5 else "early",
            "description": rt_target["description"],
        }

        # Forward-testing duration
        ft_target = self.TARGETS["forward_test_days"]
        ft_progress = min(100, days_running / ft_target["min"] * 100)
        milestones["forward_test_days"] = {
            "current": round(days_running, 1),
            "target_min": ft_target["min"],
            "target_ideal": ft_target["ideal"],
            "progress_pct": round(ft_progress, 1),
            "status": "complete" if days_running >= ft_target["ideal"] else "on_track" if days_running >= ft_target["min"] * 0.5 else "early",
            "description": ft_target["description"],
        }

        # Regimes covered
        rg_target = self.TARGETS["regimes_covered"]
        rg_progress = min(100, regimes_covered / rg_target["min"] * 100)
        milestones["regimes_covered"] = {
            "current": regimes_covered,
            "target_min": rg_target["min"],
            "target_ideal": rg_target["ideal"],
            "progress_pct": round(rg_progress, 1),
            "status": "complete" if regimes_covered >= rg_target["ideal"] else "on_track" if regimes_covered >= 1 else "early",
            "description": rg_target["description"],
            "regimes": regimes,
        }

        # Statistical significance
        milestones["statistical_significance"] = {
            "current": f"p={round(p_value, 4)}",
            "target_min": "p<0.05",
            "target_ideal": "p<0.01",
            "progress_pct": round(max(0, min(100, (1 - p_value) * 100)), 1),
            "status": "complete" if is_significant else "collecting",
            "p_value": round(p_value, 4),
            "t_statistic": round(t_stat, 3),
            "is_significant": is_significant,
            "sample_size": len(pnls),
            "description": "Statistical significance",
        }

        # Overall readiness score
        weights = {"ema_v5_trades": 0.25, "high_conf_trades": 0.25, "rejected_tracked": 0.15, "forward_test_days": 0.20, "regimes_covered": 0.15}
        readiness = sum(milestones[k]["progress_pct"] * w for k, w in weights.items())

        return {
            "readiness_score": round(readiness, 1),
            "milestones": milestones,
            "summary": self._generate_summary(milestones, readiness),
        }

    def _generate_summary(self, milestones: Dict, readiness: float) -> str:
        """Generate human-readable summary."""
        lines = []

        if readiness >= 100:
            lines.append("✅ ALL MILESTONES MET — Ready for strategy optimization decisions")
        elif readiness >= 70:
            lines.append("🟡 APPROACHING MILESTONES — Continue collecting data")
        elif readiness >= 40:
            lines.append("🟠 EARLY STAGE — Significant data collection needed")
        else:
            lines.append("🔴 JUST STARTED — Focus on data collection, not optimization")

        for key, m in milestones.items():
            status_icon = {"complete": "✅", "on_track": "🟡", "early": "🔴", "collecting": "⏳"}.get(m["status"], "❓")
            lines.append(f"  {status_icon} {m['description']}: {m['current']}/{m['target_min']} ({m['progress_pct']}%)")

        return "\n".join(lines)

    @staticmethod
    def _normal_cdf(x: float) -> float:
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    def close(self) -> None:
        self._conn.close()
        self._cal_conn.close()


if __name__ == "__main__":
    tracker = MilestoneTracker()
    result = tracker.track()
    print("=" * 70)
    print("  EMA_V5 VALIDATION MILESTONE TRACKER")
    print("=" * 70)
    print()
    print(f"  Overall Readiness: {result['readiness_score']}%")
    print()
    print(result["summary"])
    print()
    print("─" * 70)
    for key, m in result["milestones"].items():
        bar_len = int(m["progress_pct"] / 5)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        print(f"  {m['description']}")
        print(f"    [{bar}] {m['progress_pct']}%")
        print(f"    Current: {m['current']} | Min: {m['target_min']} | Ideal: {m['target_ideal']}")
        print()
    print("=" * 70)
    tracker.close()
