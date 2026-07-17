#!/usr/bin/env python3
"""
Regime-Aware Governance & Configuration Versioning
====================================================
- Regime-aware validation (score per market environment)
- Configuration versioning (audit trail)
- Automatic regime-based profile switching

READ-ONLY — Never modifies trading logic.
"""
import sqlite3
import json
import time
import math
from pathlib import Path
from collections import defaultdict
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional
from datetime import datetime, timezone

DB_PATH = Path("/Users/targetmobile/Documents/signal machine/packages/ai-engine/data/institutional_v1.db")
VERSIONS_PATH = Path("/Users/targetmobile/Documents/signal machine/packages/ai-engine/data/governance/versions.json")


@dataclass
class RegimePerformance:
    """Performance metrics for a specific regime."""
    regime: str
    trades: int
    win_rate: float
    profit_factor: float
    expectancy: float
    net_pnl: float
    status: str  # "✅", "⚠️", "❌"


@dataclass
class RegimeValidation:
    """Regime-aware validation results."""
    config_name: str
    regime_performances: List[RegimePerformance]
    recommended_regimes: List[str]
    blocked_regimes: List[str]
    overall_score: float


@dataclass
class ConfigVersion:
    """Configuration version record."""
    version: str
    date: str
    config_name: str
    params: Dict
    reason: str
    status: str  # "Active", "Testing", "Archived", "Retired"
    performance: Dict
    deployment_level: int
    notes: str = ""


def connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def get_all_trades():
    """Get all trades from both tables."""
    conn = connect()
    rows = conn.execute("""
        SELECT * FROM positions WHERE status = 'closed' ORDER BY closed_at ASC
    """).fetchall()
    trades = [dict(r) for r in rows]
    
    rows2 = conn.execute("""
        SELECT * FROM positions_archive WHERE status = 'closed' ORDER BY closed_at ASC
    """).fetchall()
    trades.extend([dict(r) for r in rows2])
    conn.close()
    
    # Deduplicate
    seen = set()
    unique = []
    for t in trades:
        key = (t.get("symbol"), t.get("closed_at"))
        if key not in seen:
            seen.add(key)
            unique.append(t)
    
    return unique


def calculate_metrics(trades):
    """Calculate portfolio-level metrics."""
    if not trades:
        return {"trades": 0, "win_rate": 0, "profit_factor": 0, "expectancy": 0, "net_pnl": 0}
    
    pnls = [t.get("pnl", 0) or 0 for t in trades]
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
    
    return {
        "trades": n,
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "expectancy": round(exp, 4),
        "net_pnl": round(sum(pnls), 2),
    }


def apply_profile(trades, profile):
    """Apply a parameter profile to filter trades."""
    filtered = []
    for t in trades:
        rr = t.get("risk_reward", 0) or t.get("planned_rr", 0) or 0
        if rr < profile["rr_threshold"]:
            continue
        session = t.get("session", "unknown") or "unknown"
        if session not in profile["session_filter"]:
            continue
        conf = (t.get("confidence", 0) or 0) * 100
        if conf < profile["min_confidence"]:
            continue
        filtered.append(t)
    return filtered


def run_regime_validation(trades, profile):
    """Run regime-aware validation for a configuration."""
    filtered = apply_profile(trades, profile)
    
    # Group by regime
    regimes = defaultdict(list)
    for t in filtered:
        regime = t.get("regime", "unknown") or t.get("at_open_regime", "unknown") or "unknown"
        regimes[regime].append(t)
    
    regime_performances = []
    recommended = []
    blocked = []
    
    for regime, regime_trades in regimes.items():
        metrics = calculate_metrics(regime_trades)
        
        # Determine status
        if metrics["profit_factor"] > 1.5 and metrics["trades"] >= 5:
            status = "✅"
            recommended.append(regime)
        elif metrics["profit_factor"] > 1.0 and metrics["trades"] >= 3:
            status = "⚠️"
        else:
            status = "❌"
            if metrics["trades"] >= 3:
                blocked.append(regime)
        
        regime_performances.append(RegimePerformance(
            regime=regime,
            trades=metrics["trades"],
            win_rate=metrics["win_rate"],
            profit_factor=metrics["profit_factor"],
            expectancy=metrics["expectancy"],
            net_pnl=metrics["net_pnl"],
            status=status,
        ))
    
    # Calculate overall score
    if regime_performances:
        scores = []
        for rp in regime_performances:
            if rp.profit_factor > 1.5:
                scores.append(100)
            elif rp.profit_factor > 1.0:
                scores.append(70)
            elif rp.profit_factor > 0.5:
                scores.append(40)
            else:
                scores.append(10)
        overall_score = sum(scores) / len(scores)
    else:
        overall_score = 0
    
    return RegimeValidation(
        config_name=profile["name"],
        regime_performances=regime_performances,
        recommended_regimes=recommended,
        blocked_regimes=blocked,
        overall_score=round(overall_score, 1),
    )


def display_regime_validation(validation):
    """Display regime validation results."""
    print(f"\n{'='*80}")
    print(f"📊 REGIME-AWARE VALIDATION: {validation.config_name}")
    print(f"{'='*80}")
    
    print(f"\nOverall Regime Score: {validation.overall_score}/100")
    
    print(f"\n{'REGIME':<20} {'TRADES':<8} {'WR%':<8} {'PF':<8} {'EXP':<10} {'PNL':<12} {'STATUS':<8}")
    print("-" * 75)
    
    for rp in sorted(validation.regime_performances, key=lambda x: -x.profit_factor):
        print(f"{rp.status} {rp.regime:<18} {rp.trades:<8} {rp.win_rate:<7.1f}% "
              f"{rp.profit_factor:<8.2f} {rp.expectancy:<10.4f} ${rp.net_pnl:<11.2f}")
    
    print(f"\n✅ RECOMMENDED REGIMES: {', '.join(validation.recommended_regimes) if validation.recommended_regimes else 'None'}")
    print(f"❌ BLOCKED REGIMES: {', '.join(validation.blocked_regimes) if validation.blocked_regimes else 'None'}")
    
    # Recommendation
    if validation.overall_score >= 70:
        print(f"\n🟢 RECOMMENDATION: Deploy in recommended regimes only")
    elif validation.overall_score >= 50:
        print(f"\n🟡 RECOMMENDATION: Paper trade in recommended regimes")
    else:
        print(f"\n🔴 RECOMMENDATION: Not ready for deployment")


def load_config_versions():
    """Load configuration versions from file."""
    if not VERSIONS_PATH.exists():
        return []
    
    try:
        with open(VERSIONS_PATH) as f:
            data = json.load(f)
        return [ConfigVersion(**v) for v in data]
    except Exception:
        return []


def save_config_versions(versions):
    """Save configuration versions to file."""
    VERSIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(VERSIONS_PATH, "w") as f:
        json.dump([asdict(v) for v in versions], f, indent=2)


def add_config_version(versions, config_name, params, reason, status, performance, deployment_level):
    """Add a new configuration version."""
    # Determine version number
    existing_versions = [v.version for v in versions if v.config_name == config_name]
    if existing_versions:
        # Increment version
        latest = max(existing_versions)
        parts = latest.split(".")
        new_minor = int(parts[1]) + 1 if len(parts) > 1 else 1
        version = f"{parts[0]}.{new_minor}"
    else:
        version = "1.0"
    
    new_version = ConfigVersion(
        version=version,
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        config_name=config_name,
        params=params,
        reason=reason,
        status=status,
        performance=performance,
        deployment_level=deployment_level,
    )
    
    versions.append(new_version)
    save_config_versions(versions)
    
    return new_version


def display_config_versions(versions):
    """Display configuration versions."""
    print(f"\n{'='*100}")
    print(f"📋 CONFIGURATION VERSIONS")
    print(f"{'='*100}\n")
    
    if not versions:
        print("   No configuration versions recorded yet.")
        return
    
    print(f"{'VERSION':<10} {'DATE':<20} {'CONFIG':<20} {'STATUS':<12} {'LEVEL':<8} {'REASON':<30}")
    print("-" * 100)
    
    for v in sorted(versions, key=lambda x: x.date, reverse=True):
        status_emoji = {"Active": "🟢", "Testing": "🟡", "Archived": "⚫", "Retired": "🔴"}.get(v.status, "⚪")
        print(f"{v.version:<10} {v.date:<20} {v.config_name:<20} {status_emoji} {v.status:<10} L{v.deployment_level:<7} {v.reason:<30}")


def run_governance_analysis(trades):
    """Run complete governance analysis."""
    print("\n" + "=" * 100)
    print("🔬 REGIME-AWARE GOVERNANCE & CONFIGURATION VERSIONING")
    print("=" * 100)
    print(f"   Total trades: {len(trades)}")
    
    # Define configurations
    configs = [
        {"name": "Baseline", "rr_threshold": 1.5, "session_filter": ["new_york", "london", "unknown", "asia"], "min_confidence": 40},
        {"name": "RR_2.5_NY", "rr_threshold": 2.5, "session_filter": ["new_york"], "min_confidence": 40},
        {"name": "RR_3.0_NY", "rr_threshold": 3.0, "session_filter": ["new_york"], "min_confidence": 40},
    ]
    
    # Run regime validation for each
    validations = []
    for config in configs:
        validation = run_regime_validation(trades, config)
        validations.append(validation)
        display_regime_validation(validation)
    
    # Load existing versions
    versions = load_config_versions()
    
    # Add versions for current configurations
    for config in configs:
        filtered = apply_profile(trades, config)
        metrics = calculate_metrics(filtered)
        
        # Check if this config already exists
        existing = [v for v in versions if v.config_name == config["name"] and v.status == "Active"]
        if not existing:
            # Determine deployment level
            if metrics["trades"] >= 200 and metrics["profit_factor"] > 1.5:
                level = 3
            elif metrics["trades"] >= 50 and metrics["profit_factor"] > 1.0:
                level = 2
            else:
                level = 1
            
            add_config_version(
                versions=versions,
                config_name=config["name"],
                params=config,
                reason="Initial configuration",
                status="Testing" if level < 3 else "Active",
                performance=metrics,
                deployment_level=level,
            )
    
    # Display versions
    display_config_versions(versions)
    
    # Display regime-based recommendations
    print(f"\n{'='*100}")
    print(f"📋 REGIME-BASED DEPLOYMENT RECOMMENDATIONS")
    print(f"{'='*100}\n")
    
    # Find best config for each regime
    regime_configs = defaultdict(list)
    for validation in validations:
        for rp in validation.regime_performances:
            if rp.profit_factor > 1.0:
                regime_configs[rp.regime].append({
                    "config": validation.config_name,
                    "pf": rp.profit_factor,
                    "trades": rp.trades,
                })
    
    print(f"{'REGIME':<20} {'BEST CONFIG':<20} {'PF':<8} {'TRADES':<8} {'ACTION':<15}")
    print("-" * 75)
    
    for regime, configs_list in sorted(regime_configs.items()):
        if configs_list:
            best = max(configs_list, key=lambda x: x["pf"])
            action = "✅ Enable" if best["pf"] > 1.5 else "⚠️  Monitor"
            print(f"{regime:<20} {best['config']:<20} {best['pf']:<8.2f} {best['trades']:<8} {action}")
        else:
            print(f"{regime:<20} {'None':<20} {'N/A':<8} {'0':<8} {'❌ Disable'}")
    
    # Final summary
    print(f"\n{'='*100}")
    print(f"📋 FINAL GOVERNANCE SUMMARY")
    print(f"{'='*100}")
    
    # Find best overall config
    best_validation = max(validations, key=lambda v: v.overall_score)
    
    print(f"""
🏆 BEST CONFIGURATION: {best_validation.config_name}
   Regime Score: {best_validation.overall_score}/100
   Recommended Regimes: {', '.join(best_validation.recommended_regimes) if best_validation.recommended_regimes else 'None'}
   Blocked Regimes: {', '.join(best_validation.blocked_regimes) if best_validation.blocked_regimes else 'None'}

📋 DEPLOYMENT STRATEGY:
   1. Deploy {best_validation.config_name} in recommended regimes only
   2. Monitor performance by regime
   3. Block deployment in regimes with PF < 1.0
   4. Re-run regime validation weekly

📋 CONFIGURATION VERSIONING:
   • All configurations are now versioned
   • Changes require version increment
   • Rollback is straightforward (revert to previous version)
""")
    
    print("=" * 100)
    print("NOTE: These are governance recommendations only.")
    print("No changes have been made to the strategy.")
    print("=" * 100)


def main():
    print("=" * 100)
    print("🔬 REGIME-AWARE GOVERNANCE & CONFIGURATION VERSIONING")
    print("=" * 100)
    
    trades = get_all_trades()
    print(f"\n📊 Loaded {len(trades)} total trades")
    
    if len(trades) < 20:
        print("❌ Not enough trades for governance analysis")
        return
    
    run_governance_analysis(trades)


if __name__ == "__main__":
    main()
