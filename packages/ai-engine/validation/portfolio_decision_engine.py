#!/usr/bin/env python3
"""
Portfolio Decision Engine — Capital Allocation Layer for EMA_V5.

CODE FREEZE: NO strategy changes. PURE CAPITAL ALLOCATION.

Sits AFTER the Dynamic Execution Gate:
  Scanner → Signal Engine → State Machine → Execution Gate → PORTFOLIO DECISION ENGINE → Trade Execution

Determines which signals deserve capital when multiple qualified
opportunities exist. Never creates signals. Never changes strategy.
Only allocates capital to maximize portfolio profitability.

Components:
  1. OpportunityRanker — Rank signals by composite score
  2. CapitalAllocator — Allocate position sizes based on rank
  3. CorrelationManager — Prevent correlated positions
  4. SectorExposure — Manage sector concentration
  5. PortfolioHealth — Monitor overall portfolio metrics
  6. CapitalRotation — Suggest position rotation
  7. DecisionLearning — Update scores after every trade
"""
from __future__ import annotations

import json
import time
import sqlite3
import statistics
import math
from dataclasses import dataclass, field
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from loguru import logger


# ══════════════════════════════════════════════════════════════════════
# CONSTANTS
# ══════════════════════════════════════════════════════════════════════

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "portfolio_decision.db"
BRIDGE_PATH = Path(__file__).resolve().parent.parent / "data" / "bridge"

DECAY_FACTOR = 0.95
LOOKBACK_TRADES = 200
MIN_TRADES_FOR_SCORE = 5

# Portfolio constraints
MAX_CONCURRENT_POSITIONS = 6
MAX_SECTOR_EXPOSURE = 2        # Max positions per sector
MAX_CORRELATION_CLUSTER = 2    # Max positions per correlation cluster
MAX_PORTFOLIO_HEAT = 0.15      # Max 15% total risk
MAX_SINGLE_POSITION = 0.12     # Max 12% per position

# Position size tiers
SIZE_TIERS = {
    "ELITE": 1.00,    # 100%
    "STRONG": 0.75,   # 75%
    "GOOD": 0.50,     # 50%
    "AVERAGE": 0.25,  # 25%
    "WATCH": 0.00,    # 0%
}

# Sector classification
SECTORS = {
    # Layer 1
    "BTCUSDT": "L1_MAJOR", "ETHUSDT": "L1_MAJOR", "SOLUSDT": "L1_MAJOR",
    "BNBUSDT": "L1_MAJOR", "XRPUSDT": "L1_MAJOR",
    # Layer 1 Alt
    "ADAUSDT": "L1_ALT", "AVAXUSDT": "L1_ALT", "DOTUSDT": "L1_ALT",
    "NEARUSDT": "L1_ALT", "ATOMUSDT": "L1_ALT", "APTUSDT": "L1_ALT",
    "SUIUSDT": "L1_ALT", "SEIUSDT": "L1_ALT", "INJUSDT": "L1_ALT",
    "ETCUSDT": "L1_ALT", "BCHUSDT": "L1_ALT", "LTCUSDT": "L1_ALT",
    "TRXUSDT": "L1_ALT", "HBARUSDT": "L1_ALT", "ALGOUSDT": "L1_ALT",
    # Meme
    "DOGEUSDT": "MEME", "1000BONKUSDT": "MEME", "1000PEPEUSDT": "MEME",
    "SHIBUSDT": "MEME", "WIFUSDT": "MEME", "FLOKIUSDT": "MEME",
    "TURBOUSDT": "MEME", "NEIROUSDT": "MEME", "BRETTUSDT": "MEME",
    # AI
    "FETUSDT": "AI", "RENDERUSDT": "AI", "ARUSDT": "AI",
    "TAOUSDT": "AI", "WLDUSDT": "AI", "AKTUSDT": "AI",
    # DeFi
    "UNIUSDT": "DEFI", "AAVEUSDT": "DEFI", "CRVUSDT": "DEFI",
    "COMPUSDT": "DEFI", "LINKUSDT": "DEFI", "MKRUSDT": "DEFI",
    # Infrastructure
    "GRTUSDT": "INFRA", "FILUSDT": "INFRA", "THETAUSDT": "INFRA",
    # Other
    "1000SATSUSDT": "OTHER", "ORDIUSDT": "OTHER", "STXUSDT": "OTHER",
    "RUNEUSDT": "OTHER", "SUSHIUSDT": "OTHER",
}

# Correlation clusters (symbols that move together)
CORRELATION_CLUSTERS = {
    "BTC_ECOSYSTEM": ["BTCUSDT", "ETCUSDT", "BCHUSDT", "LTCUSDT"],
    "ETH_ECOSYSTEM": ["ETHUSDT", "UNIUSDT", "AAVEUSDT", "LINKUSDT", "CRVUSDT", "MKRUSDT"],
    "SOL_ECOSYSTEM": ["SOLUSDT", "INJUSDT", "RAYUSDT", "JUPUSDT"],
    "LAYER1_ALT": ["ADAUSDT", "AVAXUSDT", "DOTUSDT", "NEARUSDT", "ATOMUSDT", "APTUSDT", "SUIUSDT"],
    "MEME_BASKET": ["DOGEUSDT", "1000BONKUSDT", "1000PEPEUSDT", "SHIBUSDT", "WIFUSDT", "FLOKIUSDT"],
    "AI_BASKET": ["FETUSDT", "RENDERUSDT", "ARUSDT", "TAOUSDT", "WLDUSDT", "AKTUSDT"],
}


def get_sector(symbol: str) -> str:
    return SECTORS.get(symbol, "OTHER")


def get_cluster(symbol: str) -> str:
    for cluster, syms in CORRELATION_CLUSTERS.items():
        if symbol in syms:
            return cluster
    return "INDEPENDENT"


# ══════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════════

@dataclass
class PortfolioSignal:
    """A signal that has passed through the Execution Gate."""
    symbol: str = ""
    direction: str = ""        # LONG / SHORT
    entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    confidence: float = 0.0
    regime: str = ""
    session: str = ""
    trend: str = ""
    htf_trend: str = ""
    alpha_score: float = 0.0   # From Execution Gate
    execution_decision: str = ""  # EXECUTE / GOOD / AVERAGE
    atr: float = 0.0
    rvol: float = 0.0
    funding_rate: float = 0.0
    open_interest: float = 0.0
    cvd: float = 0.0
    liquidity_sweep: bool = False
    order_block: bool = False
    fvg: bool = False
    score: float = 0.0         # Raw signal score


@dataclass
class AllocationDecision:
    """Output for every signal after portfolio optimization."""
    rank: int = 0
    symbol: str = ""
    direction: str = ""
    expected_profit: float = 0.0
    expected_drawdown: float = 0.0
    alpha_score: float = 0.0
    portfolio_score: float = 0.0
    position_size_pct: float = 0.0  # 0/25/50/75/100
    size_tier: str = "WATCH"
    decision: str = "IGNORE"        # EXECUTE / WATCH / IGNORE
    sector: str = ""
    cluster: str = ""
    reason: str = ""
    component_scores: Dict = field(default_factory=dict)


@dataclass
class PortfolioHealth:
    """Current portfolio health metrics."""
    net_exposure: float = 0.0
    sector_exposure: Dict = field(default_factory=dict)
    cluster_exposure: Dict = field(default_factory=dict)
    open_positions: int = 0
    portfolio_heat: float = 0.0
    expected_pf: float = 0.0
    expected_drawdown: float = 0.0
    expected_expectancy: float = 0.0
    recovery_factor: float = 0.0
    sharpe: float = 0.0
    calmar: float = 0.0
    net_pnl: float = 0.0
    health_status: str = "UNKNOWN"


# ══════════════════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════════════════

class PortfolioDB:
    """SQLite persistence for portfolio decision state."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.execute("PRAGMA journal_mode=WAL")

        # Component scores (decay-weighted)
        db.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dimension TEXT NOT NULL,
                dimension_value TEXT NOT NULL,
                score REAL DEFAULT 50.0,
                trade_count INTEGER DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                avg_r REAL DEFAULT 0,
                profit_factor REAL DEFAULT 1.0,
                expectancy REAL DEFAULT 0,
                sharpe REAL DEFAULT 0,
                recovery_factor REAL DEFAULT 0,
                max_dd REAL DEFAULT 0,
                last_updated REAL DEFAULT 0,
                UNIQUE(dimension, dimension_value)
            )
        """)

        # Trade log for decay
        db.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                sector TEXT DEFAULT '',
                cluster TEXT DEFAULT '',
                session TEXT DEFAULT '',
                regime TEXT DEFAULT '',
                confidence REAL DEFAULT 0,
                alpha_score REAL DEFAULT 0,
                portfolio_score REAL DEFAULT 0,
                position_size_pct REAL DEFAULT 0,
                net_profit REAL DEFAULT 0,
                r_multiple REAL DEFAULT 0,
                outcome TEXT DEFAULT ''
            )
        """)

        # Allocation decisions log
        db.execute("""
            CREATE TABLE IF NOT EXISTS allocation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                signal_count INTEGER DEFAULT 0,
                executed_count INTEGER DEFAULT 0,
                watched_count INTEGER DEFAULT 0,
                ignored_count INTEGER DEFAULT 0,
                top_symbol TEXT DEFAULT '',
                top_score REAL DEFAULT 0,
                portfolio_heat REAL DEFAULT 0,
                decisions_json TEXT DEFAULT '[]'
            )
        """)

        db.execute("CREATE INDEX IF NOT EXISTS idx_ps_dim ON portfolio_scores(dimension)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_pt_sym ON portfolio_trades(symbol)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_pt_time ON portfolio_trades(timestamp)")

        db.commit()
        db.close()

    def get_score(self, dimension: str, value: str) -> Dict:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM portfolio_scores WHERE dimension=? AND dimension_value=?",
            (dimension, value)
        ).fetchone()
        db.close()
        if row:
            return dict(row)
        return {"dimension": dimension, "dimension_value": value, "score": 50.0,
                "trade_count": 0, "total_pnl": 0, "avg_r": 0, "profit_factor": 1.0,
                "expectancy": 0, "sharpe": 0, "recovery_factor": 0, "max_dd": 0}

    def update_score(self, dimension: str, value: str, stats: Dict) -> None:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.execute("""
            INSERT OR REPLACE INTO portfolio_scores 
            (dimension, dimension_value, score, trade_count, total_pnl, avg_r,
             profit_factor, expectancy, sharpe, recovery_factor, max_dd, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            dimension, value, stats.get("score", 50.0), stats.get("trade_count", 0),
            stats.get("total_pnl", 0), stats.get("avg_r", 0),
            stats.get("profit_factor", 1.0), stats.get("expectancy", 0),
            stats.get("sharpe", 0), stats.get("recovery_factor", 0),
            stats.get("max_dd", 0), time.time(),
        ))
        db.commit()
        db.close()

    def record_trade(self, trade: Dict) -> None:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.execute("""
            INSERT INTO portfolio_trades (timestamp, symbol, direction, sector, cluster,
            session, regime, confidence, alpha_score, portfolio_score, position_size_pct,
            net_profit, r_multiple, outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            trade.get("timestamp", time.time()), trade.get("symbol", ""),
            trade.get("direction", ""), trade.get("sector", ""),
            trade.get("cluster", ""), trade.get("session", ""),
            trade.get("regime", ""), trade.get("confidence", 0),
            trade.get("alpha_score", 0), trade.get("portfolio_score", 0),
            trade.get("position_size_pct", 0), trade.get("net_profit", 0),
            trade.get("r_multiple", 0),
            "win" if trade.get("net_profit", 0) > 0 else "loss",
        ))
        db.commit()
        db.close()

    def log_allocation(self, log: Dict) -> None:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.execute("""
            INSERT INTO allocation_log (timestamp, signal_count, executed_count,
            watched_count, ignored_count, top_symbol, top_score, portfolio_heat,
            decisions_json) VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            time.time(), log.get("signal_count", 0), log.get("executed_count", 0),
            log.get("watched_count", 0), log.get("ignored_count", 0),
            log.get("top_symbol", ""), log.get("top_score", 0),
            log.get("portfolio_heat", 0), json.dumps(log.get("decisions", [])),
        ))
        db.commit()
        db.close()

    def get_recent_trades(self, n: int = LOOKBACK_TRADES) -> List[Dict]:
        db = sqlite3.connect(str(self.db_path), timeout=10)
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT * FROM portfolio_trades ORDER BY timestamp DESC LIMIT ?", (n,)).fetchall()
        db.close()
        return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════
# 1. OPPORTUNITY RANKER
# ══════════════════════════════════════════════════════════════════════

class OpportunityRanker:
    """
    Rank every approved signal by composite portfolio score.
    
    Score components:
      - Alpha Score (from Execution Gate): 30%
      - Historical Symbol Performance: 20%
      - Historical Sector Performance: 10%
      - Historical Session Performance: 10%
      - Historical Regime Performance: 10%
      - Confidence Quality: 10%
      - Risk/Reward Profile: 10%
    """

    WEIGHTS = {
        "alpha_score": 0.30,
        "symbol_history": 0.20,
        "sector_history": 0.10,
        "session_history": 0.10,
        "regime_history": 0.10,
        "confidence_quality": 0.10,
        "risk_reward": 0.10,
    }

    def __init__(self, db: PortfolioDB):
        self.db = db

    def rank(self, signals: List[PortfolioSignal]) -> List[AllocationDecision]:
        """Rank signals and return allocation decisions."""
        decisions = []
        
        for sig in signals:
            components = {}
            
            # 1. Alpha Score (from Execution Gate) — already 0-100
            components["alpha_score"] = min(100, max(0, sig.alpha_score))
            
            # 2. Historical Symbol Performance
            sym_score = self.db.get_score("symbol", sig.symbol)
            components["symbol_history"] = sym_score["score"]
            
            # 3. Historical Sector Performance
            sector = get_sector(sig.symbol)
            sector_score = self.db.get_score("sector", sector)
            components["sector_history"] = sector_score["score"]
            
            # 4. Historical Session Performance
            sess_score = self.db.get_score("session", sig.session) if sig.session else {"score": 50}
            components["session_history"] = sess_score["score"]
            
            # 5. Historical Regime Performance
            regime_score = self.db.get_score("regime", sig.regime) if sig.regime else {"score": 50}
            components["regime_history"] = regime_score["score"]
            
            # 6. Confidence Quality
            conf = sig.confidence
            if conf >= 90: conf_q = 95
            elif conf >= 85: conf_q = 85
            elif conf >= 80: conf_q = 75
            elif conf >= 75: conf_q = 60
            elif conf >= 70: conf_q = 45
            else: conf_q = 30
            components["confidence_quality"] = conf_q
            
            # 7. Risk/Reward Profile
            if sig.stop_loss > 0 and sig.entry > 0:
                sl_dist = abs(sig.entry - sig.stop_loss) / sig.entry * 100
                tp_dist = abs(sig.take_profit - sig.entry) / sig.entry * 100 if sig.take_profit > 0 else 0
                rr_ratio = tp_dist / sl_dist if sl_dist > 0 else 0
                rr_score = min(100, max(0, rr_ratio / 3.0 * 100))
            else:
                rr_score = 50
            components["risk_reward"] = rr_score
            
            # Compute weighted portfolio score
            total_weight = sum(self.WEIGHTS.values())
            weighted_sum = sum(components[k] * self.WEIGHTS[k] for k in self.WEIGHTS)
            portfolio_score = weighted_sum / total_weight if total_weight > 0 else 50
            
            # Expected profit from historical data
            expected_profit = sym_score.get("expectancy", 0)
            expected_dd = sym_score.get("max_dd", 0)
            
            decision = AllocationDecision(
                symbol=sig.symbol,
                direction=sig.direction,
                expected_profit=round(expected_profit, 2),
                expected_drawdown=round(expected_dd, 2),
                alpha_score=round(sig.alpha_score, 1),
                portfolio_score=round(portfolio_score, 1),
                sector=sector,
                cluster=get_cluster(sig.symbol),
                component_scores={k: round(v, 1) for k, v in components.items()},
            )
            decisions.append(decision)
        
        # Sort by portfolio score (highest first)
        decisions.sort(key=lambda d: d.portfolio_score, reverse=True)
        
        # Assign ranks
        for i, d in enumerate(decisions):
            d.rank = i + 1
        
        return decisions


# ══════════════════════════════════════════════════════════════════════
# 2. CAPITAL ALLOCATOR
# ══════════════════════════════════════════════════════════════════════

class CapitalAllocator:
    """
    Allocate position sizes based on rank and portfolio state.
    
    Instead of equal-weight:
      Rank 1 → 100% size
      Rank 2 → 75% size
      Rank 3 → 50% size
      Rank 4+ → 25% or WATCH
    
    Adjusted by:
      - Portfolio heat (reduce if approaching max)
      - Correlation (reduce if correlated with existing)
      - Sector exposure (reduce if sector concentrated)
    """

    def __init__(self, db: PortfolioDB):
        self.db = db

    def allocate(self, decisions: List[AllocationDecision],
                 open_positions: List[Dict],
                 portfolio_heat: float = 0.0,
                 current_equity: float = 10000.0) -> List[AllocationDecision]:
        """Allocate position sizes to ranked decisions."""
        if not decisions:
            return []
        
        # Count existing positions by sector/cluster
        existing_sectors = defaultdict(int)
        existing_clusters = defaultdict(int)
        existing_count = len(open_positions)
        
        for pos in open_positions:
            sym = pos.get("symbol", "")
            existing_sectors[get_sector(sym)] += 1
            existing_clusters[get_cluster(sym)] += 1
        
        allocated = []
        allocated_count = 0
        
        for dec in decisions:
            # Check if we can take more positions
            if existing_count + allocated_count >= MAX_CONCURRENT_POSITIONS:
                dec.decision = "WATCH"
                dec.size_tier = "WATCH"
                dec.position_size_pct = 0
                dec.reason = f"Max concurrent ({MAX_CONCURRENT_POSITIONS}) reached"
                allocated.append(dec)
                continue
            
            # Check sector exposure
            if existing_sectors[dec.sector] >= MAX_SECTOR_EXPOSURE:
                dec.decision = "WATCH"
                dec.size_tier = "WATCH"
                dec.position_size_pct = 0
                dec.reason = f"Sector {dec.sector} max exposure ({MAX_SECTOR_EXPOSURE})"
                allocated.append(dec)
                continue
            
            # Check cluster correlation
            if existing_clusters[dec.cluster] >= MAX_CORRELATION_CLUSTER:
                dec.decision = "WATCH"
                dec.size_tier = "WATCH"
                dec.position_size_pct = 0
                dec.reason = f"Cluster {dec.cluster} max correlation ({MAX_CORRELATION_CLUSTER})"
                allocated.append(dec)
                continue
            
            # Determine base size tier from rank
            rank = dec.rank
            if rank == 1:
                base_tier = "ELITE"
                base_pct = 100
            elif rank == 2:
                base_tier = "STRONG"
                base_pct = 75
            elif rank == 3:
                base_tier = "GOOD"
                base_pct = 50
            elif rank <= 5:
                base_tier = "AVERAGE"
                base_pct = 25
            else:
                dec.decision = "WATCH"
                dec.size_tier = "WATCH"
                dec.position_size_pct = 0
                dec.reason = f"Rank {rank} — below top 5"
                allocated.append(dec)
                continue
            
            # Adjust for portfolio heat
            heat_remaining = MAX_PORTFOLIO_HEAT - portfolio_heat
            if heat_remaining <= 0:
                dec.decision = "WATCH"
                dec.size_tier = "WATCH"
                dec.position_size_pct = 0
                dec.reason = "Portfolio heat maxed"
                allocated.append(dec)
                continue
            
            # Adjust size based on heat
            heat_factor = min(1.0, heat_remaining / 0.05)  # Scale down as heat approaches max
            adjusted_pct = base_pct * heat_factor
            adjusted_pct = max(25, min(100, adjusted_pct))
            
            # Final decision
            if adjusted_pct >= 75:
                dec.decision = "EXECUTE"
            elif adjusted_pct >= 50:
                dec.decision = "EXECUTE"
            elif adjusted_pct >= 25:
                dec.decision = "EXECUTE"
            else:
                dec.decision = "WATCH"
            
            dec.position_size_pct = round(adjusted_pct, 0)
            dec.size_tier = base_tier
            dec.reason = f"Rank {rank} | {base_tier} | heat_adj={heat_factor:.1f}"
            
            allocated.append(dec)
            allocated_count += 1
            existing_sectors[dec.sector] += 1
            existing_clusters[dec.cluster] += 1
        
        return allocated


# ══════════════════════════════════════════════════════════════════════
# 3. PORTFOLIO HEALTH MONITOR
# ══════════════════════════════════════════════════════════════════════

class PortfolioHealthMonitor:
    """
    Continuously calculate portfolio health metrics.
    
    Net Exposure, Sector Exposure, Correlation,
    Expected PF, Expected DD, Expected Expectancy,
    Recovery Factor, Sharpe, Calmar.
    """

    def __init__(self, db: PortfolioDB):
        self.db = db

    def compute_health(self, open_positions: List[Dict] = None,
                       recent_trades: List[Dict] = None) -> PortfolioHealth:
        """Compute current portfolio health."""
        health = PortfolioHealth()
        
        if not open_positions:
            open_positions = []
        if not recent_trades:
            recent_trades = self.db.get_recent_trades(LOOKBACK_TRADES)
        
        # Open position metrics
        health.open_positions = len(open_positions)
        
        # Sector exposure
        sector_counts = defaultdict(int)
        for pos in open_positions:
            sector = get_sector(pos.get("symbol", ""))
            sector_counts[sector] += 1
        health.sector_exposure = dict(sector_counts)
        
        # Cluster exposure
        cluster_counts = defaultdict(int)
        for pos in open_positions:
            cluster = get_cluster(pos.get("symbol", ""))
            cluster_counts[cluster] += 1
        health.cluster_exposure = dict(cluster_counts)
        
        # Portfolio heat (total risk as % of equity)
        total_risk = 0
        for pos in open_positions:
            risk = pos.get("risk_amount", 0)
            equity = pos.get("equity", 10000)
            total_risk += risk / equity if equity > 0 else 0
        health.portfolio_heat = round(total_risk * 100, 2)
        
        # Net exposure (long - short as % of equity)
        long_val = sum(p.get("position_value", 0) for p in open_positions if p.get("direction") == "LONG")
        short_val = sum(p.get("position_value", 0) for p in open_positions if p.get("direction") == "SHORT")
        total_equity = sum(p.get("equity", 10000) for p in open_positions) or 10000
        health.net_exposure = round((long_val - short_val) / total_equity * 100, 2)
        
        # Recent trade metrics
        if recent_trades:
            pnls = [t.get("net_profit", 0) for t in recent_trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            gp = sum(wins) if wins else 0
            gl = abs(sum(losses)) if losses else 0.001
            
            health.net_pnl = round(sum(pnls), 2)
            health.expected_pf = round(gp / gl, 2)
            health.expected_expectancy = round(statistics.mean(pnls), 2) if pnls else 0
            
            # Sharpe
            if len(pnls) > 1:
                std = statistics.stdev(pnls)
                health.sharpe = round(statistics.mean(pnls) / std * math.sqrt(252), 2) if std > 0 else 0
            
            # Drawdown
            ec = [10000]
            for p in pnls: ec.append(ec[-1] + p)
            peak = max(ec)
            max_dd = 0
            for eq in ec:
                peak = max(peak, eq)
                dd = (peak - eq) / peak * 100 if peak > 0 else 0
                max_dd = max(max_dd, dd)
            health.expected_drawdown = round(max_dd, 2)
            
            # Recovery Factor
            health.recovery_factor = round(sum(pnls) / max_dd if max_dd > 0 else 0, 2)
            
            # Calmar
            if len(recent_trades) >= 2:
                first = min(t.get("timestamp", time.time()) for t in recent_trades)
                last = max(t.get("timestamp", time.time()) for t in recent_trades)
                days = max((last - first) / 86400, 1)
                years = days / 365.25
                final_eq = ec[-1]
                cagr = (final_eq / 10000) ** (1/years) - 1 if final_eq > 0 and years > 0 else 0
                health.calmar = round(cagr / (max_dd / 100) if max_dd > 0 else 0, 2)
        
        # Health status
        if health.expected_pf >= 1.5 and health.expected_drawdown < 10:
            health.health_status = "EXCELLENT"
        elif health.expected_pf >= 1.2 and health.expected_drawdown < 15:
            health.health_status = "GOOD"
        elif health.expected_pf >= 1.0:
            health.health_status = "NEUTRAL"
        else:
            health.health_status = "WARNING"
        
        return health


# ══════════════════════════════════════════════════════════════════════
# 4. CAPITAL ROTATION ENGINE
# ══════════════════════════════════════════════════════════════════════

class CapitalRotation:
    """
    If a higher-ranked signal appears, compare against open positions.
    If replacing increases expected portfolio return, recommend rotation.
    """

    def __init__(self, db: PortfolioDB):
        self.db = db

    def check_rotation(self, new_signals: List[AllocationDecision],
                       open_positions: List[Dict]) -> List[Dict]:
        """Check if any new signal should replace an existing position."""
        rotations = []
        
        if not open_positions or not new_signals:
            return rotations
        
        # Get scores for open positions
        for pos in open_positions:
            sym = pos.get("symbol", "")
            pos_score = self.db.get_score("symbol", sym)
            
            # Find best new signal that could replace this position
            for sig in new_signals:
                if sig.decision not in ("EXECUTE",):
                    continue
                
                # Must be same direction or better
                if sig.direction != pos.get("direction", ""):
                    continue
                
                # New signal must score significantly higher
                improvement = sig.portfolio_score - pos_score["score"]
                if improvement > 20:  # 20+ point improvement triggers rotation
                    rotations.append({
                        "action": "ROTATE",
                        "from_symbol": sym,
                        "from_score": pos_score["score"],
                        "to_symbol": sig.symbol,
                        "to_score": sig.portfolio_score,
                        "improvement": round(improvement, 1),
                        "reason": f"Score improvement {improvement:.0f} ({pos_score['score']:.0f} → {sig.portfolio_score:.0f})",
                    })
        
        return rotations


# ══════════════════════════════════════════════════════════════════════
# 5. DECISION LEARNING ENGINE
# ══════════════════════════════════════════════════════════════════════

class DecisionLearning:
    """
    After every closed trade, update all portfolio-level scores.
    
    Dimensions: symbol, sector, session, regime, confidence_bucket,
    trend, cluster, volatility_bucket
    """

    DIMENSIONS = [
        "symbol", "sector", "session", "regime", "direction",
        "confidence_bucket", "trend", "cluster", "volatility_bucket",
    ]

    def __init__(self, db: PortfolioDB):
        self.db = db

    def on_trade_closed(self, trade: Dict) -> None:
        """Update all scores after a closed trade."""
        self.db.record_trade(trade)
        recent = self.db.get_recent_trades(LOOKBACK_TRADES)
        
        dimensions = self._extract_dimensions(trade)
        for dim, value in dimensions.items():
            if dim in self.DIMENSIONS:
                self._update_dimension(dim, value, recent)

    def _extract_dimensions(self, trade: Dict) -> Dict[str, str]:
        return {
            "symbol": trade.get("symbol", "unknown"),
            "sector": get_sector(trade.get("symbol", "")),
            "session": trade.get("session", "unknown"),
            "regime": trade.get("regime", "unknown"),
            "direction": trade.get("direction", "unknown"),
            "confidence_bucket": self._conf_bucket(trade.get("confidence", 0)),
            "trend": trade.get("trend", trade.get("htf_trend", "unknown")),
            "cluster": get_cluster(trade.get("symbol", "")),
            "volatility_bucket": self._vol_bucket(trade.get("atr", 0)),
        }

    def _update_dimension(self, dimension: str, value: str, recent: List[Dict]) -> None:
        matching = []
        for i, t in enumerate(recent):
            t_dims = self._extract_dimensions(t)
            if t_dims.get(dimension) == value:
                weight = DECAY_FACTOR ** i
                matching.append((weight, t))
        
        if not matching:
            return
        
        total_w = sum(w for w, _ in matching)
        weighted_pnl = sum(w * t.get("net_profit", 0) for w, t in matching) / total_w
        weighted_r = sum(w * (t.get("r_multiple", 0) or 0) for w, t in matching) / total_w
        
        wins_w = sum(w for w, t in matching if t.get("net_profit", 0) > 0)
        wr = wins_w / total_w * 100 if total_w > 0 else 0
        
        wins_pnl = sum(w * t.get("net_profit", 0) for w, t in matching if t.get("net_profit", 0) > 0)
        losses_pnl = abs(sum(w * t.get("net_profit", 0) for w, t in matching if t.get("net_profit", 0) <= 0))
        pf = wins_pnl / max(losses_pnl, 0.01)
        
        pnls = [t.get("net_profit", 0) for _, t in matching]
        if len(pnls) > 1:
            std = statistics.stdev(pnls)
            sharpe = statistics.mean(pnls) / std * math.sqrt(252) if std > 0 else 0
        else:
            sharpe = 0
        
        ec = [10000]
        for _, t in sorted(matching, key=lambda x: x[1].get("timestamp", 0)):
            ec.append(ec[-1] + t.get("net_profit", 0))
        peak = max(ec); max_dd = 0
        for eq in ec:
            peak = max(peak, eq)
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            max_dd = max(max_dd, dd)
        rf = sum(pnls) / max_dd if max_dd > 0 else 0
        
        n = len(matching)
        score = self._compute_score(wr, pf, weighted_r, sharpe, rf, n)
        
        self.db.update_score(dimension, value, {
            "score": score, "trade_count": n,
            "total_pnl": round(sum(t.get("net_profit", 0) for _, t in matching), 2),
            "avg_r": round(weighted_r, 2), "profit_factor": round(pf, 2),
            "expectancy": round(weighted_pnl, 2), "sharpe": round(sharpe, 2),
            "recovery_factor": round(rf, 2), "max_dd": round(max_dd, 2),
        })

    def _compute_score(self, wr, pf, avg_r, sharpe, rf, n):
        if n < MIN_TRADES_FOR_SCORE:
            return 50.0
        pf_s = min(30, max(0, (pf - 0.5) / 2.5 * 30))
        r_s = min(25, max(0, (avg_r + 1) / 4 * 25))
        sh_s = min(20, max(0, sharpe / 3 * 20))
        wr_s = min(15, max(0, (wr - 30) / 40 * 15))
        rf_s = min(10, max(0, rf / 500 * 10))
        n_s = min(5, n / 50 * 5)
        return round(pf_s + r_s + sh_s + wr_s + rf_s + n_s, 1)

    def _conf_bucket(self, c):
        if c >= 90: return "90+"
        elif c >= 80: return "80-90"
        elif c >= 70: return "70-80"
        elif c >= 60: return "60-70"
        else: return "<60"

    def _vol_bucket(self, a):
        if a <= 0: return "unknown"
        elif a < 1.0: return "low"
        elif a < 2.0: return "medium"
        else: return "high"


# ══════════════════════════════════════════════════════════════════════
# PORTFOLIO DECISION ENGINE (Main Orchestrator)
# ══════════════════════════════════════════════════════════════════════

class PortfolioDecisionEngine:
    """
    Portfolio Decision Engine — Capital Allocation Layer.
    
    Sits AFTER the Dynamic Execution Gate:
      Scanner → Signal Engine → State Machine → Execution Gate → THIS → Trade Execution
    
    Determines which signals deserve capital when multiple qualified
    opportunities exist. Never creates signals. Only allocates capital.
    
    Usage:
        pde = PortfolioDecisionEngine()
        
        # Convert execution gate outputs to portfolio signals
        signals = [PortfolioSignal(**sig_dict) for sig_dict in approved_signals]
        
        # Get allocation decisions
        decisions = pde.process(signals, open_positions)
        
        # For each decision
        for dec in decisions:
            if dec.decision == "EXECUTE":
                execute_trade(dec.symbol, dec.direction, dec.position_size_pct)
        
        # After trade closes
        pde.on_trade_closed(trade_result)
        
        # Get portfolio health
        health = pde.get_health(open_positions)
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db = PortfolioDB(db_path)
        self.ranker = OpportunityRanker(self.db)
        self.allocator = CapitalAllocator(self.db)
        self.health_monitor = PortfolioHealthMonitor(self.db)
        self.rotation = CapitalRotation(self.db)
        self.learning = DecisionLearning(self.db)
        logger.info("✅ PortfolioDecisionEngine initialized")

    def process(self, signals: List[PortfolioSignal],
                open_positions: List[Dict] = None,
                current_equity: float = 10000.0) -> List[AllocationDecision]:
        """
        Process approved signals through the portfolio decision engine.
        
        Returns ranked allocation decisions with position sizes.
        """
        if not signals:
            return []
        
        if open_positions is None:
            open_positions = []
        
        # Step 1: Rank opportunities
        ranked = self.ranker.rank(signals)
        
        # Step 2: Check for rotation opportunities
        rotations = self.rotation.check_rotation(ranked, open_positions)
        if rotations:
            logger.info("🔄 {} rotation opportunities detected", len(rotations))
        
        # Step 3: Allocate capital
        health = self.health_monitor.compute_health(open_positions)
        allocated = self.allocator.allocate(
            ranked, open_positions, health.portfolio_heat, current_equity
        )
        
        # Step 4: Log the allocation
        executed = sum(1 for d in allocated if d.decision == "EXECUTE")
        watched = sum(1 for d in allocated if d.decision == "WATCH")
        ignored = sum(1 for d in allocated if d.decision == "IGNORE")
        
        self.db.log_allocation({
            "signal_count": len(signals),
            "executed_count": executed,
            "watched_count": watched,
            "ignored_count": ignored,
            "top_symbol": allocated[0].symbol if allocated else "",
            "top_score": allocated[0].portfolio_score if allocated else 0,
            "portfolio_heat": health.portfolio_heat,
            "decisions": [
                {"symbol": d.symbol, "direction": d.direction, "score": d.portfolio_score,
                 "size": d.position_size_pct, "decision": d.decision}
                for d in allocated[:10]
            ],
        })
        
        return allocated

    def on_trade_closed(self, trade: Dict) -> None:
        """Update learning engine after every closed trade."""
        self.learning.on_trade_closed(trade)

    def get_health(self, open_positions: List[Dict] = None) -> PortfolioHealth:
        """Get current portfolio health."""
        return self.health_monitor.compute_health(open_positions or [])

    def get_status(self) -> Dict:
        """Get engine status for monitoring."""
        recent = self.db.get_recent_trades(50)
        if not recent:
            return {"status": "NO_DATA", "trades": 0}
        
        pnls = [t.get("net_profit", 0) for t in recent]
        return {
            "status": "OK",
            "recent_trades": len(recent),
            "net_pnl": round(sum(pnls), 2),
            "avg_pnl": round(statistics.mean(pnls), 2) if pnls else 0,
        }


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pde = PortfolioDecisionEngine()
    print("PortfolioDecisionEngine initialized")
    print(f"Status: {pde.get_status()}")
