import asyncio
import sqlite3
import pandas as pd
from pathlib import Path

async def audit_confidence_performance():
    db_path = Path("data/institutional_v1.db")
    if not db_path.exists():
        print("Database not found.")
        return

    conn = sqlite3.connect(db_path)
    
    # Query closed positions joined with their parent signals
    query = """
    SELECT 
        s.confidence * 100 as conf,
        p.pnl,
        p.symbol,
        s.institutional_score as score
    FROM positions p
    JOIN signals s ON p.signal_id = s.id
    WHERE p.status = 'closed'
    """
    
    df = pd.read_sql_query(query, conn)
    conn.close()

    if df.empty:
        print("No historical trade data found to audit.")
        return

    buckets = [(40, 50), (50, 60), (60, 70), (70, 80), (80, 100)]
    
    print("\n" + "="*50)
    print("📊 CONFIDENCE SCORE AUDIT REPORT")
    print("="*50)
    print(f"{'Bucket':<12} | {'Trades':<8} | {'Win Rate':<10} | {'Avg PnL':<10}")
    print("-" * 50)

    for low, high in buckets:
        mask = (df['conf'] >= low) & (df['conf'] < high)
        subset = df[mask]
        
        total = len(subset)
        if total == 0:
            print(f"{low}-{high:<7}% | {'0':<8} | {'N/A':<10} | {'N/A':<10}")
            continue
            
        wins = len(subset[subset['pnl'] > 0])
        wr = (wins / total) * 100
        avg_pnl = subset['pnl'].mean()
        
        print(f"{low}-{high:<7}% | {total:<8} | {wr:>8.1f}% | ${avg_pnl:>8.2f}")

    print("="*50)
    print(f"Total Trades Audited: {len(df)}")

if __name__ == "__main__":
    asyncio.run(audit_confidence_performance())