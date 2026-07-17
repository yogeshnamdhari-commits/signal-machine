"""
Institutional Reports — Automated Report Generation
=====================================================
Phase 12: Generate daily, weekly, monthly, quarterly, yearly reports.

READ-ONLY — Never modifies trading logic.

Each report includes:
- Performance
- Risk
- Expectancy
- Win Rate
- Profit Factor
- Drawdown
- Confidence distribution
- Symbol rankings
- Session rankings
- Market regime
- RR analysis
- Top winners
- Top losers
- Recommendations
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from .calibration_assistant import get_calibration_assistant
from .drift_detector import get_drift_detector


class InstitutionalReports:
    """
    Generates automated institutional-grade reports.
    
    READ-ONLY: Never modifies trading logic.
    """
    
    DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "institutional_v1.db"
    REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "reports"
    
    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or self.DB_PATH
        self._reports_dir = self.REPORTS_DIR
        self._reports_dir.mkdir(parents=True, exist_ok=True)
    
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _get_trades_in_period(self, start_ts: float, end_ts: float) -> List[Dict]:
        """Get closed trades within a time period."""
        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT * FROM positions 
                WHERE status = 'closed' 
                AND closed_at >= ? AND closed_at <= ?
                ORDER BY closed_at ASC
            """, (start_ts, end_ts)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    
    def _calc_metrics(self, trades: List[Dict]) -> Dict:
        """Calculate metrics for a set of trades."""
        if not trades:
            return {
                "trades": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "total_pnl": 0, "avg_pnl": 0, "profit_factor": 0,
                "expectancy": 0, "max_drawdown": 0, "avg_rr": 0,
                "avg_r": 0, "avg_confidence": 0, "avg_hold": 0,
            }
        
        pnls = [t.get("pnl") or 0 for t in trades]
        n = len(pnls)
        wins = [p for p in pnls if p > 0]
        losses = [abs(p) for p in pnls if p < 0]
        
        wr = len(wins) / n * 100 if n else 0
        gp = sum(wins) if wins else 0
        gl = sum(losses) if losses else 0
        pf = gp / gl if gl > 0 else float('inf') if gp > 0 else 0
        
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        exp = (wr / 100 * avg_win) - ((100 - wr) / 100 * avg_loss)
        
        # Max drawdown
        cum = 0
        peak = 0
        max_dd = 0
        for p in pnls:
            cum += p
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
        
        # RR and R
        rrs = [t.get("risk_reward", 0) or 0 for t in trades if t.get("risk_reward")]
        avg_rr = sum(rrs) / len(rrs) if rrs else 0
        
        rs = [t.get("realized_r", 0) or 0 for t in trades if t.get("realized_r")]
        avg_r = sum(rs) / len(rs) if rs else 0
        
        # Confidence
        confs = [(t.get("confidence") or 0) * 100 for t in trades]
        avg_conf = sum(confs) / len(confs) if confs else 0
        
        # Hold time
        holds = [t.get("hold_minutes", 0) or 0 for t in trades if t.get("hold_minutes")]
        avg_hold = sum(holds) / len(holds) if holds else 0
        
        return {
            "trades": n,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(wr, 1),
            "total_pnl": round(sum(pnls), 2),
            "avg_pnl": round(sum(pnls) / n, 4) if n else 0,
            "profit_factor": round(pf, 2),
            "expectancy": round(exp, 4),
            "max_drawdown": round(max_dd, 2),
            "avg_rr": round(avg_rr, 2),
            "avg_r": round(avg_r, 2),
            "avg_confidence": round(avg_conf, 1),
            "avg_hold_minutes": round(avg_hold, 0),
        }
    
    def _get_symbol_rankings(self, trades: List[Dict]) -> List[Dict]:
        """Get symbol performance rankings."""
        symbol_trades = {}
        for t in trades:
            sym = t.get("symbol", "")
            if sym not in symbol_trades:
                symbol_trades[sym] = []
            symbol_trades[sym].append(t)
        
        rankings = []
        for sym, sym_trades in symbol_trades.items():
            metrics = self._calc_metrics(sym_trades)
            rankings.append({
                "symbol": sym,
                **metrics,
            })
        
        rankings.sort(key=lambda x: -x["total_pnl"])
        return rankings
    
    def _get_session_rankings(self, trades: List[Dict]) -> List[Dict]:
        """Get session performance rankings."""
        session_trades = {}
        for t in trades:
            sess = t.get("session", "unknown")
            if sess not in session_trades:
                session_trades[sess] = []
            session_trades[sess].append(t)
        
        rankings = []
        for sess, sess_trades in session_trades.items():
            metrics = self._calc_metrics(sess_trades)
            rankings.append({
                "session": sess,
                **metrics,
            })
        
        rankings.sort(key=lambda x: -x["total_pnl"])
        return rankings
    
    def _get_regime_rankings(self, trades: List[Dict]) -> List[Dict]:
        """Get regime performance rankings."""
        regime_trades = {}
        for t in trades:
            regime = t.get("regime", "unknown")
            if regime not in regime_trades:
                regime_trades[regime] = []
            regime_trades[regime].append(t)
        
        rankings = []
        for regime, regime_trades_list in regime_trades.items():
            metrics = self._calc_metrics(regime_trades_list)
            rankings.append({
                "regime": regime,
                **metrics,
            })
        
        rankings.sort(key=lambda x: -x["total_pnl"])
        return rankings
    
    def _get_top_trades(self, trades: List[Dict], top_n: int = 5) -> Dict:
        """Get top winners and losers."""
        sorted_trades = sorted(trades, key=lambda x: x.get("pnl") or 0, reverse=True)
        
        winners = []
        for t in sorted_trades[:top_n]:
            winners.append({
                "symbol": t.get("symbol"),
                "side": t.get("side"),
                "pnl": round(t.get("pnl") or 0, 4),
                "realized_r": round(t.get("realized_r") or 0, 2),
                "confidence": round((t.get("confidence") or 0) * 100, 1),
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
                "exit_reason": t.get("exit_reason"),
            })
        
        return {"winners": winners, "losers": losers}
    
    def generate_daily_report(self, date: Optional[datetime] = None) -> Dict:
        """Generate daily report."""
        if date is None:
            date = datetime.now(timezone.utc)
        
        start = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        
        trades = self._get_trades_in_period(start.timestamp(), end.timestamp())
        metrics = self._calc_metrics(trades)
        symbols = self._get_symbol_rankings(trades)
        sessions = self._get_session_rankings(trades)
        top_trades = self._get_top_trades(trades)
        
        report = {
            "type": "daily",
            "date": date.strftime("%Y-%m-%d"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
            "symbol_rankings": symbols[:10],
            "session_rankings": sessions,
            "top_trades": top_trades,
        }
        
        # Save report
        filename = f"daily_{date.strftime('%Y-%m-%d')}.json"
        self._save_report(report, filename)
        
        return report
    
    def generate_weekly_report(self, end_date: Optional[datetime] = None) -> Dict:
        """Generate weekly report."""
        if end_date is None:
            end_date = datetime.now(timezone.utc)
        
        start = end_date - timedelta(days=7)
        trades = self._get_trades_in_period(start.timestamp(), end_date.timestamp())
        
        metrics = self._calc_metrics(trades)
        symbols = self._get_symbol_rankings(trades)
        sessions = self._get_session_rankings(trades)
        regimes = self._get_regime_rankings(trades)
        top_trades = self._get_top_trades(trades)
        
        # Get calibration recommendations
        calibration = get_calibration_assistant().generate_recommendations()
        
        report = {
            "type": "weekly",
            "period": f"{start.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
            "symbol_rankings": symbols[:15],
            "session_rankings": sessions,
            "regime_rankings": regimes,
            "top_trades": top_trades,
            "calibration_recommendations": calibration,
        }
        
        filename = f"weekly_{end_date.strftime('%Y-%m-%d')}.json"
        self._save_report(report, filename)
        
        return report
    
    def generate_monthly_report(self, year: int, month: int) -> Dict:
        """Generate monthly report."""
        start = datetime(year, month, 1, tzinfo=timezone.utc)
        if month == 12:
            end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            end = datetime(year, month + 1, 1, tzinfo=timezone.utc)
        
        trades = self._get_trades_in_period(start.timestamp(), end.timestamp())
        
        metrics = self._calc_metrics(trades)
        symbols = self._get_symbol_rankings(trades)
        sessions = self._get_session_rankings(trades)
        regimes = self._get_regime_rankings(trades)
        top_trades = self._get_top_trades(trades)
        
        # Get drift detection
        drift = get_drift_detector()
        drift_alerts = drift.detect_drift()
        
        report = {
            "type": "monthly",
            "period": f"{year}-{month:02d}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "metrics": metrics,
            "symbol_rankings": symbols,
            "session_rankings": sessions,
            "regime_rankings": regimes,
            "top_trades": top_trades,
            "drift_alerts": [a for a in drift.get_alerts()],
        }
        
        filename = f"monthly_{year}-{month:02d}.json"
        self._save_report(report, filename)
        
        return report
    
    def _save_report(self, report: Dict, filename: str) -> None:
        """Save report to file."""
        filepath = self._reports_dir / filename
        with open(filepath, "w") as f:
            json.dump(report, f, indent=2, default=str)
        logger.info("📊 Report saved: {}", filepath)
    
    def get_report_as_text(self, report: Dict) -> str:
        """Convert report to human-readable text."""
        lines = []
        lines.append("=" * 60)
        lines.append(f"📊 {report['type'].upper()} REPORT")
        lines.append(f"   Period: {report.get('period', report.get('date', 'N/A'))}")
        lines.append(f"   Generated: {report['generated_at']}")
        lines.append("=" * 60)
        
        m = report.get("metrics", {})
        lines.append(f"\n📈 PERFORMANCE SUMMARY")
        lines.append(f"   Total Trades: {m.get('trades', 0)}")
        lines.append(f"   Wins: {m.get('wins', 0)} | Losses: {m.get('losses', 0)}")
        lines.append(f"   Win Rate: {m.get('win_rate', 0):.1f}%")
        lines.append(f"   Total PnL: ${m.get('total_pnl', 0):.2f}")
        lines.append(f"   Profit Factor: {m.get('profit_factor', 0):.2f}")
        lines.append(f"   Expectancy: {m.get('expectancy', 0):.4f}")
        lines.append(f"   Avg R: {m.get('avg_r', 0):.2f}")
        lines.append(f"   Avg RR: {m.get('avg_rr', 0):.2f}")
        lines.append(f"   Max Drawdown: ${m.get('max_drawdown', 0):.2f}")
        lines.append(f"   Avg Confidence: {m.get('avg_confidence', 0):.1f}%")
        
        # Symbol rankings
        symbols = report.get("symbol_rankings", [])
        if symbols:
            lines.append(f"\n🏆 TOP SYMBOLS")
            for i, s in enumerate(symbols[:5], 1):
                lines.append(f"   {i}. {s['symbol']}: ${s['total_pnl']:.2f} ({s['win_rate']:.1f}% WR)")
        
        # Session rankings
        sessions = report.get("session_rankings", [])
        if sessions:
            lines.append(f"\n🕐 SESSION PERFORMANCE")
            for s in sessions:
                lines.append(f"   {s['session']}: {s['win_rate']:.1f}% WR, ${s['total_pnl']:.2f}")
        
        # Top trades
        top = report.get("top_trades", {})
        winners = top.get("winners", [])
        losers = top.get("losers", [])
        
        if winners:
            lines.append(f"\n✅ TOP WINNERS")
            for w in winners[:3]:
                lines.append(f"   {w['symbol']} {w['side']}: ${w['pnl']:.2f} ({w['realized_r']:.1f}R)")
        
        if losers:
            lines.append(f"\n❌ TOP LOSERS")
            for l in losers[:3]:
                lines.append(f"   {l['symbol']} {l['side']}: ${l['pnl']:.2f} ({l['realized_r']:.1f}R)")
        
        # Recommendations
        recs = report.get("calibration_recommendations", [])
        if recs:
            lines.append(f"\n🔧 RECOMMENDATIONS")
            for r in recs[:5]:
                lines.append(f"   • {r.get('message', '')}")
        
        lines.append("\n" + "=" * 60)
        return "\n".join(lines)


# Global singleton
_reports: Optional[InstitutionalReports] = None

def get_institutional_reports() -> InstitutionalReports:
    """Get or create the global institutional reports."""
    global _reports
    if _reports is None:
        _reports = InstitutionalReports()
    return _reports
