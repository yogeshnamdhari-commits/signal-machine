"""
Calibration Assistant — Evidence-Based Recommendations
=======================================================
Phase 13: Generates recommendations WITHOUT modifying strategy.

READ-ONLY — Never modifies trading logic.

Provides recommendations like:
- "SL multiplier appears too high"
- "TP1 appears conservative"
- "Confidence bucket 60-70 underperforms"
- "BTC performs well"
- "DOGE underperforms"

Never auto-modifies logic. Only suggests.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


class CalibrationAssistant:
    """
    Generates evidence-based recommendations for strategy tuning.
    
    NEVER modifies trading logic. Only suggests changes.
    
    READ-ONLY: Never modifies trading logic.
    """
    
    DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "institutional_v1.db"
    
    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or self.DB_PATH
    
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _get_trades(self, limit: int = 500) -> List[Dict]:
        """Get recent closed trades."""
        conn = self._connect()
        try:
            rows = conn.execute("""
                SELECT * FROM positions 
                WHERE status = 'closed' 
                ORDER BY closed_at DESC 
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    
    def generate_recommendations(self) -> List[Dict]:
        """Generate calibration recommendations based on evidence."""
        trades = self._get_trades()
        if len(trades) < 20:
            return [{"type": "info", "message": "Not enough trades for recommendations (need 20+)"}]
        
        recommendations = []
        
        # Analyze SL distance
        recommendations.extend(self._analyze_sl_distance(trades))
        
        # Analyze TP targets
        recommendations.extend(self._analyze_tp_targets(trades))
        
        # Analyze confidence buckets
        recommendations.extend(self._analyze_confidence(trades))
        
        # Analyze symbols
        recommendations.extend(self._analyze_symbols(trades))
        
        # Analyze sessions
        recommendations.extend(self._analyze_sessions(trades))
        
        # Analyze RR
        recommendations.extend(self._analyze_rr(trades))
        
        return recommendations
    
    def _analyze_sl_distance(self, trades: List[Dict]) -> List[Dict]:
        """Analyze stop-loss distance patterns."""
        recs = []
        
        # Calculate average SL distance for winners vs losers
        winners = [t for t in trades if (t.get("pnl") or 0) > 0]
        losers = [t for t in trades if (t.get("pnl") or 0) < 0]
        
        if not winners or not losers:
            return recs
        
        def avg_sl_dist(ts):
            dists = []
            for t in ts:
                entry = t.get("entry_price", 0)
                sl = t.get("stop_loss", 0)
                if entry and sl:
                    dists.append(abs(entry - sl) / entry * 100)
            return sum(dists) / len(dists) if dists else 0
        
        winner_sl = avg_sl_dist(winners)
        loser_sl = avg_sl_dist(losers)
        
        if loser_sl > winner_sl * 1.5:
            recs.append({
                "type": "warning",
                "category": "stop_loss",
                "message": f"Losers have {loser_sl:.1f}% SL distance vs {winner_sl:.1f}% for winners. "
                          f"SL multiplier may be too high for losing trades.",
                "metric": "sl_distance",
                "winner_value": round(winner_sl, 2),
                "loser_value": round(loser_sl, 2),
                "recommendation": "Consider tightening SL or reducing ATR multiplier",
            })
        
        return recs
    
    def _analyze_tp_targets(self, trades: List[Dict]) -> List[Dict]:
        """Analyze take-profit target effectiveness."""
        recs = []
        
        # Check MFE vs TP targets
        mfes = [t.get("mfe_pct", 0) or 0 for t in trades if t.get("mfe_pct")]
        if not mfes:
            return recs
        
        avg_mfe = sum(mfes) / len(mfes)
        
        # If MFE is consistently much higher than typical TP, targets may be conservative
        if avg_mfe > 3.0:  # Average MFE > 3%
            recs.append({
                "type": "info",
                "category": "take_profit",
                "message": f"Average MFE is {avg_mfe:.1f}%, suggesting trades have room to run. "
                          f"TP1 may be too conservative.",
                "metric": "mfe",
                "value": round(avg_mfe, 2),
                "recommendation": "Consider allowing trades to run further before taking profit",
            })
        
        return recs
    
    def _analyze_confidence(self, trades: List[Dict]) -> List[Dict]:
        """Analyze confidence bucket performance."""
        recs = []
        
        buckets = {
            "90-100": [], "80-89": [], "70-79": [], "60-69": [], "<60": []
        }
        
        for t in trades:
            conf = (t.get("confidence") or 0) * 100
            if conf >= 90:
                buckets["90-100"].append(t)
            elif conf >= 80:
                buckets["80-89"].append(t)
            elif conf >= 70:
                buckets["70-79"].append(t)
            elif conf >= 60:
                buckets["60-69"].append(t)
            else:
                buckets["<60"].append(t)
        
        for bucket_name, bucket_trades in buckets.items():
            if len(bucket_trades) < 5:
                continue
            
            pnls = [t.get("pnl") or 0 for t in bucket_trades]
            wins = sum(1 for p in pnls if p > 0)
            wr = wins / len(pnls) * 100
            
            if wr < 40:
                recs.append({
                    "type": "warning",
                    "category": "confidence",
                    "message": f"Confidence bucket {bucket_name} has {wr:.1f}% win rate ({len(bucket_trades)} trades). "
                              f"Signals in this range may need additional filtering.",
                    "metric": "confidence_win_rate",
                    "bucket": bucket_name,
                    "win_rate": round(wr, 1),
                    "trades": len(bucket_trades),
                    "recommendation": f"Consider raising minimum confidence threshold above {bucket_name.split('-')[0]}%",
                })
        
        return recs
    
    def _analyze_symbols(self, trades: List[Dict]) -> List[Dict]:
        """Analyze symbol performance."""
        recs = []
        
        symbol_trades = {}
        for t in trades:
            sym = t.get("symbol", "")
            if sym not in symbol_trades:
                symbol_trades[sym] = []
            symbol_trades[sym].append(t)
        
        # Find underperforming symbols
        for sym, sym_trades in symbol_trades.items():
            if len(sym_trades) < 5:
                continue
            
            pnls = [t.get("pnl") or 0 for t in sym_trades]
            total_pnl = sum(pnls)
            wins = sum(1 for p in pnls if p > 0)
            wr = wins / len(pnls) * 100
            
            if total_pnl < 0 and wr < 40:
                recs.append({
                    "type": "warning",
                    "category": "symbol",
                    "message": f"{sym} is underperforming: {wr:.1f}% WR, ${total_pnl:.2f} PnL "
                              f"({len(sym_trades)} trades). Consider excluding or reducing exposure.",
                    "metric": "symbol_pnl",
                    "symbol": sym,
                    "win_rate": round(wr, 1),
                    "pnl": round(total_pnl, 2),
                    "trades": len(sym_trades),
                    "recommendation": f"Consider adding {sym} to exclusion list or reducing position size",
                })
        
        return recs
    
    def _analyze_sessions(self, trades: List[Dict]) -> List[Dict]:
        """Analyze session performance."""
        recs = []
        
        session_trades = {}
        for t in trades:
            sess = t.get("session", "unknown")
            if sess not in session_trades:
                session_trades[sess] = []
            session_trades[sess].append(t)
        
        for sess, sess_trades in session_trades.items():
            if len(sess_trades) < 10:
                continue
            
            pnls = [t.get("pnl") or 0 for t in sess_trades]
            total_pnl = sum(pnls)
            wins = sum(1 for p in pnls if p > 0)
            wr = wins / len(pnls) * 100
            
            if wr < 40:
                recs.append({
                    "type": "warning",
                    "category": "session",
                    "message": f"{sess} session has {wr:.1f}% win rate ({len(sess_trades)} trades). "
                              f"Consider avoiding trades during this session.",
                    "metric": "session_win_rate",
                    "session": sess,
                    "win_rate": round(wr, 1),
                    "trades": len(sess_trades),
                    "recommendation": f"Consider adding session filter for {sess}",
                })
        
        return recs
    
    def _analyze_rr(self, trades: List[Dict]) -> List[Dict]:
        """Analyze risk-reward effectiveness."""
        recs = []
        
        # Compare planned RR vs realized R
        planned = [t.get("planned_rr", 0) or 0 for t in trades if t.get("planned_rr")]
        realized = [t.get("realized_r", 0) or 0 for t in trades if t.get("realized_r")]
        
        if planned and realized:
            avg_planned = sum(planned) / len(planned)
            avg_realized = sum(realized) / len(realized)
            
            if avg_realized < avg_planned * 0.5:
                recs.append({
                    "type": "info",
                    "category": "risk_reward",
                    "message": f"Realized R ({avg_realized:.2f}) is much lower than planned RR ({avg_planned:.2f}). "
                              f"Trades may be exiting too early.",
                    "metric": "rr_realization",
                    "planned_rr": round(avg_planned, 2),
                    "realized_r": round(avg_realized, 2),
                    "recommendation": "Consider letting trades run further toward targets",
                })
        
        return recs
    
    def get_report(self) -> str:
        """Generate calibration report."""
        recs = self.generate_recommendations()
        
        lines = []
        lines.append("=" * 60)
        lines.append("🔧 CALIBRATION ASSISTANT REPORT")
        lines.append("=" * 60)
        
        if not recs:
            lines.append("\n✅ No calibration issues detected.")
            lines.append("   Strategy parameters appear well-tuned.")
        else:
            lines.append(f"\n📋 {len(recs)} recommendations generated:\n")
            
            for i, rec in enumerate(recs, 1):
                icon = {"warning": "⚠️", "info": "ℹ️", "critical": "🔴"}.get(rec.get("type", "info"), "ℹ️")
                lines.append(f"{i}. {icon} {rec.get('category', 'general').upper()}")
                lines.append(f"   {rec['message']}")
                if "recommendation" in rec:
                    lines.append(f"   → {rec['recommendation']}")
                lines.append("")
        
        lines.append("=" * 60)
        lines.append("NOTE: These are recommendations only.")
        lines.append("No changes have been made to the strategy.")
        lines.append("=" * 60)
        
        return "\n".join(lines)


# Global singleton
_assistant: Optional[CalibrationAssistant] = None

def get_calibration_assistant() -> CalibrationAssistant:
    """Get or create the global calibration assistant."""
    global _assistant
    if _assistant is None:
        _assistant = CalibrationAssistant()
    return _assistant
