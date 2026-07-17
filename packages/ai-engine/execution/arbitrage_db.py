"""
Arbitrage Persistence — Opportunity and execution audit database.
"""
import aiosqlite
import json
from pathlib import Path

class ArbitrageDB:
    def __init__(self):
        self.path = Path("data/database/arbitrage.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def initialize(self):
        async with aiosqlite.connect(self.path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS opportunities (
                    id TEXT PRIMARY KEY, type TEXT, symbol TEXT, 
                    long_ex TEXT, short_ex TEXT, edge REAL, timestamp INTEGER
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS executions (
                    arb_id TEXT PRIMARY KEY, status TEXT, profit REAL,
                    details TEXT, timestamp INTEGER
                )
            """)
            await db.commit()

    async def save_opportunity(self, opp: any):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO opportunities VALUES (?, ?, ?, ?, ?, ?, ?)",
                (opp.arb_id, opp.arb_type, opp.symbol, opp.long_exchange, 
                 opp.short_exchange, opp.net_edge_bps, opp.timestamp)
            )
            await db.commit()

    async def record_execution(self, opp: any, l1: any, l2: any, status: str):
        profit = (l2.avg_price - l1.avg_price) * l1.executed_qty if l1 and l2 else 0
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT INTO executions VALUES (?, ?, ?, ?, ?)",
                (opp.arb_id, status, profit, json.dumps(opp.to_dict()), opp.timestamp)
            )
            await db.commit()

    async def get_opportunity_counts(self):
        async with aiosqlite.connect(self.path) as db:
            async with db.execute("SELECT type, COUNT(*) FROM opportunities GROUP BY type") as cursor:
                rows = await cursor.fetchall()
                return {row[0]: row[1] for row in rows}
    async def close(self): pass