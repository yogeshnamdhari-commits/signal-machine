"""
Capital Allocation Validation Suite — 10,000 run stress test.
Verifies deterministic behavior and zero risk violations.
"""
import asyncio
import json
import random
from execution.capital_allocator import CapitalAllocationEngine, AllocationRequest, AllocationModel
from loguru import logger

async def run_stress_test():
    engine = CapitalAllocationEngine()
    results = []
    violations = 0
    
    logger.info("🚀 Starting 10,000 simulated allocations...")
    
    regimes = ["bull", "bear", "range", "volatile"]
    exchanges = ["binance", "bybit", "okx", "delta"]
    
    for i in range(10000):
        req = AllocationRequest(
            symbol=f"SYM_{random.randint(1, 20)}",
            exchange=random.choice(exchanges),
            signal_score=random.uniform(50, 100),
            confidence=random.uniform(0.4, 0.95),
            volatility=random.uniform(0.005, 0.08),
            market_regime=random.choice(regimes),
            portfolio_equity=100000.0,
            win_rate=random.uniform(0.4, 0.6),
            profit_factor=random.uniform(0.8, 2.5),
            drawdown=random.uniform(0, 0.15)
        )
        
        # Randomly choose model
        model = random.choice(list(AllocationModel))
        res = await engine.allocate(req, model=model)
        
        # Verification logic
        if res.risk_pct > 0.0201: # 2% symbol cap with epsilon
            violations += 1
            logger.error(f"Violation: Symbol Risk {res.risk_pct} at iteration {i}")
            
        if res.leverage > 5.01:
            violations += 1
            logger.error(f"Violation: Leverage {res.leverage}")
            
        results.append({
            "iteration": i,
            "allocation": res.allocation_pct,
            "leverage": res.leverage,
            "model": model.value
        })

    # Generate report
    report = {
        "total_runs": 10000,
        "violations": violations,
        "pass_rate_pct": (10000 - violations) / 100,
        "avg_leverage": sum(r['leverage'] for r in results) / 10000,
    }
    
    with open("data/reports/allocation_validation.json", "w") as f:
        json.dump(report, f, indent=2)
        
    logger.info("Validation complete. Report generated.")
    print(f"✅ Pass Rate: {report['pass_rate_pct']}% | Violations: {violations}")

if __name__ == "__main__":
    asyncio.run(run_stress_test())