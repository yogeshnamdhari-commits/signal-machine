"""
Pattern Analytics — Candle Pattern Performance Analysis
========================================================
Phase 7: Compare performance by candle pattern type.

READ-ONLY — Never modifies trading logic.

Analyzes:
- Hammer, Bullish Pin Bar, Bearish Pin Bar
- Engulfing, Shooting Star, Doji, Inside Bar
- Win Rate, Profit Factor, Average RR, Average R
- Best Pattern, Worst Pattern
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


class PatternAnalytics:
    """
    Analyzes performance by candle pattern type.
    
    READ-ONLY: Never modifies trading logic.
    """
    
    DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "institutional_v1.db"
    
    def __init__(self, db_path: Optional[Path] = None):
        self._db_path = db_path or self.DB_PATH
    
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn
    
    def _get_trades_with_patterns(self) -> List[Dict]:
        """Get closed trades that have pattern information."""
        conn = self._connect()
        try:
            # Try to get pattern from metadata or entry_reason
            rows = conn.execute("""
                SELECT symbol, side, entry_price, stop_loss, take_profit,
                       pnl, fees, confidence, regime, session,
                       risk_reward, hold_minutes, mfe_pct, mae_pct,
                       realized_r, exit_reason, entry_reason,
                       outcome, closed_at
                FROM positions 
                WHERE status = 'closed'
                ORDER BY closed_at DESC
            """).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    
    def _extract_pattern(self, trade: Dict) -> str:
        """Extract pattern name from trade data."""
        # Try entry_reason first
        entry_reason = trade.get("entry_reason", "")
        if entry_reason:
            # Common pattern names
            patterns = [
                "hammer", "bullish_pin_bar", "bearish_pin_bar",
                "engulfing", "shooting_star", "doji", "inside_bar",
                "morning_star", "evening_star", "three_white_soldiers",
                "three_black_crows", "harami", "piercing_line",
                "dark_cloud_cover", "tweezer_top", "tweezer_bottom",
            ]
            entry_lower = entry_reason.lower()
            for pattern in patterns:
                if pattern in entry_lower:
                    return pattern.replace("_", " ").title()
        
        # Try to infer from side and regime
        side = trade.get("side", "")
        regime = trade.get("regime", "")
        
        if "bull" in regime.lower():
            return "Bullish Setup"
        elif "bear" in regime.lower():
            return "Bearish Setup"
        else:
            return "Unknown"
    
    def analyze_patterns(self, min_trades: int = 5) -> List[Dict]:
        """Analyze performance by pattern type."""
        trades = self._get_trades_with_patterns()
        if not trades:
            return []
        
        # Group by pattern
        pattern_trades = {}
        for t in trades:
            pattern = self._extract_pattern(t)
            if pattern not in pattern_trades:
                pattern_trades[pattern] = []
            pattern_trades[pattern].append(t)
        
        results = []
        for pattern, pt in pattern_trades.items():
            if len(pt) < min_trades:
                continue
            
            pnls = [t["pnl"] or 0 for t in pt]
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
            
            # RR
            rrs = [t["risk_reward"] for t in pt if t.get("risk_reward")]
            avg_rr = sum(rrs) / len(rrs) if rrs else 0
            
            # R-multiples
            rs = [t["realized_r"] for t in pt if t.get("realized_r") is not None and t["realized_r"] != 0]
            avg_r = sum(rs) / len(rs) if rs else 0
            
            # Holding time
            holds = [t["hold_minutes"] for t in pt if t.get("hold_minutes")]
            avg_hold = sum(holds) / len(holds) if holds else 0
            
            # MFE/MAE
            mfes = [t["mfe_pct"] for t in pt if t.get("mfe_pct") is not None]
            maes = [t["mae_pct"] for t in pt if t.get("mae_pct") is not None]
            avg_mfe = sum(mfes) / len(mfes) if mfes else 0
            avg_mae = sum(maes) / len(maes) if maes else 0
            
            results.append({
                "pattern": pattern,
                "trades": n,
                "win_rate": round(wr, 1),
                "profit_factor": round(pf, 2),
                "expectancy": round(exp, 4),
                "avg_rr": round(avg_rr, 2),
                "avg_r": round(avg_r, 2),
                "avg_hold_minutes": round(avg_hold, 0),
                "avg_mfe_pct": round(avg_mfe, 2),
                "avg_mae_pct": round(avg_mae, 2),
                "total_pnl": round(sum(pnls), 2),
                "avg_pnl": round(sum(pnls) / n, 4) if n else 0,
            })
        
        # Sort by profit factor
        results.sort(key=lambda x: -x["profit_factor"])
        return results
    
    def get_best_pattern(self) -> Optional[Dict]:
        """Get the best performing pattern."""
        patterns = self.analyze_patterns()
        return patterns[0] if patterns else None
    
    def get_worst_pattern(self) -> Optional[Dict]:
        """Get the worst performing pattern."""
        patterns = self.analyze_patterns()
        return patterns[-1] if patterns else None
    
    def get_pattern_comparison(self) -> Dict:
        """Get pattern comparison summary."""
        patterns = self.analyze_patterns()
        if not patterns:
            return {"status": "no_data"}
        
        best = patterns[0]
        worst = patterns[-1]
        
        return {
            "total_patterns": len(patterns),
            "best_pattern": {
                "name": best["pattern"],
                "win_rate": best["win_rate"],
                "profit_factor": best["profit_factor"],
                "trades": best["trades"],
            },
            "worst_pattern": {
                "name": worst["pattern"],
                "win_rate": worst["win_rate"],
                "profit_factor": worst["profit_factor"],
                "trades": worst["trades"],
            },
            "patterns": patterns,
        }


# Global singleton
_analytics: Optional[PatternAnalytics] = None

def get_pattern_analytics() -> PatternAnalytics:
    """Get or create the global pattern analytics."""
    global _analytics
    if _analytics is None:
        _analytics = PatternAnalytics()
    return _analytics
