"""
Validation Report Generator
==============================
Generates comprehensive validation reports.

Includes:
- Executive Summary, Performance Summary, Risk Summary
- Validation Summary, Deployment Readiness
- Promotion Recommendation, Failure Reasons
- Suggested Next Actions

READ-ONLY — Never modifies trading logic.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger


class ValidationReportGenerator:
    """
    Comprehensive validation report generator.
    
    READ-ONLY: Never modifies trading logic.
    """
    
    def __init__(self):
        pass
    
    def generate_full_report(
        self,
        statistical_metrics: Any,
        portfolio_metrics: Any,
        governance_summary: Dict,
        deployment_readiness: Dict,
    ) -> str:
        """Generate a comprehensive validation report."""
        lines = []
        
        # Header
        lines.append("=" * 100)
        lines.append("📊 INSTITUTIONAL VALIDATION REPORT")
        lines.append(f"   Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        lines.append("=" * 100)
        
        # Executive Summary
        lines.append("\n" + "=" * 100)
        lines.append("📋 EXECUTIVE SUMMARY")
        lines.append("=" * 100)
        
        if statistical_metrics:
            lines.append(f"\n   Strategy Performance:")
            lines.append(f"   • Sample Size: {statistical_metrics.sample_size} trades")
            lines.append(f"   • Win Rate: {statistical_metrics.win_rate:.1f}%")
            lines.append(f"   • Profit Factor: {statistical_metrics.profit_factor:.2f}")
            lines.append(f"   • Expectancy: {statistical_metrics.expectancy:.4f}")
            lines.append(f"   • Sharpe Ratio: {statistical_metrics.sharpe_ratio:.2f}")
            
            # Overall assessment
            if statistical_metrics.profit_factor >= 1.5 and statistical_metrics.win_rate >= 40:
                lines.append(f"\n   ✅ OVERALL: Strategy shows positive edge")
            elif statistical_metrics.profit_factor >= 1.0:
                lines.append(f"\n   ⚠️ OVERALL: Strategy is marginal — needs improvement")
            else:
                lines.append(f"\n   ❌ OVERALL: Strategy is losing money")
        
        # Performance Summary
        lines.append("\n" + "=" * 100)
        lines.append("📈 PERFORMANCE SUMMARY")
        lines.append("=" * 100)
        
        if statistical_metrics:
            lines.append(f"\n   Core Metrics:")
            lines.append(f"   • Win Count: {statistical_metrics.win_count}")
            lines.append(f"   • Loss Count: {statistical_metrics.loss_count}")
            lines.append(f"   • Average R: {statistical_metrics.average_r:.2f}")
            lines.append(f"   • Median R: {statistical_metrics.median_r:.2f}")
            
            lines.append(f"\n   Risk Metrics:")
            lines.append(f"   • Max Drawdown: ${statistical_metrics.max_drawdown:.2f}")
            lines.append(f"   • Recovery Factor: {statistical_metrics.recovery_factor:.2f}")
            lines.append(f"   • Ulcer Index: {statistical_metrics.ulcer_index:.2f}")
            lines.append(f"   • Kelly Fraction: {statistical_metrics.kelly_fraction:.4f}")
            
            lines.append(f"\n   Risk-Adjusted Returns:")
            lines.append(f"   • Sharpe Ratio: {statistical_metrics.sharpe_ratio:.2f}")
            lines.append(f"   • Sortino Ratio: {statistical_metrics.sortino_ratio:.2f}")
        
        # Risk Summary
        lines.append("\n" + "=" * 100)
        lines.append("📉 RISK SUMMARY")
        lines.append("=" * 100)
        
        if statistical_metrics:
            lines.append(f"\n   Confidence Interval:")
            lines.append(f"   • 95% CI: [{statistical_metrics.confidence_interval_lower:.4f}, {statistical_metrics.confidence_interval_upper:.4f}]")
            lines.append(f"   • Bootstrap Confidence: {statistical_metrics.bootstrap_confidence:.4f}")
            
            lines.append(f"\n   Drift Detection:")
            lines.append(f"   • Configuration Drift: {statistical_metrics.configuration_drift:.4f}")
            lines.append(f"   • Drift Detected: {'⚠️ YES' if statistical_metrics.drift_detected else '✅ NO'}")
        
        # Validation Summary
        lines.append("\n" + "=" * 100)
        lines.append("🔬 VALIDATION SUMMARY")
        lines.append("=" * 100)
        
        if statistical_metrics:
            lines.append(f"\n   Statistical Validation:")
            lines.append(f"   • Monte Carlo Stability: {statistical_metrics.monte_carlo_stability:.4f}")
            lines.append(f"   • Parameter Stability: {statistical_metrics.parameter_stability:.4f}")
            lines.append(f"   • Cross-Validation: {statistical_metrics.cross_validation_score:.4f}")
            lines.append(f"   • Walk-Forward: {statistical_metrics.walk_forward_score:.4f}")
            lines.append(f"   • Out-of-Sample: {statistical_metrics.out_of_sample_score:.4f}")
            lines.append(f"   • Overfitting Score: {statistical_metrics.overfitting_score:.4f}")
            
            if statistical_metrics.regime_stability:
                lines.append(f"\n   Regime Stability:")
                for regime, score in sorted(statistical_metrics.regime_stability.items()):
                    emoji = "🟢" if score >= 0.5 else "🟡" if score >= 0.3 else "🔴"
                    lines.append(f"   {emoji} {regime}: {score:.2f}")
        
        # Portfolio Summary
        if portfolio_metrics:
            lines.append("\n" + "=" * 100)
            lines.append("💼 PORTFOLIO SUMMARY")
            lines.append("=" * 100)
            
            lines.append(f"\n   Exposure:")
            lines.append(f"   • Total Trades: {portfolio_metrics.total_trades}")
            lines.append(f"   • Max Drawdown: ${portfolio_metrics.max_drawdown:.2f}")
            
            if portfolio_metrics.top_symbols:
                lines.append(f"\n   Top Symbols:")
                for sym in portfolio_metrics.top_symbols[:5]:
                    emoji = "🟢" if sym["pnl"] > 0 else "🔴"
                    lines.append(f"   {emoji} {sym['symbol']}: ${sym['pnl']:.2f} ({sym['trades']} trades)")
            
            if portfolio_metrics.regime_performance:
                lines.append(f"\n   Regime Performance:")
                for regime, pnl in sorted(portfolio_metrics.regime_performance.items(), key=lambda x: -x[1]):
                    emoji = "🟢" if pnl > 0 else "🔴"
                    lines.append(f"   {emoji} {regime}: ${pnl:.2f}")
        
        # Deployment Readiness
        lines.append("\n" + "=" * 100)
        lines.append("🚀 DEPLOYMENT READINESS")
        lines.append("=" * 100)
        
        if deployment_readiness:
            score = deployment_readiness.get("score", 0)
            level = deployment_readiness.get("level", "L0")
            ready = deployment_readiness.get("ready", False)
            
            emoji = "🟢" if score >= 80 else "🟡" if score >= 60 else "🔴"
            lines.append(f"\n   {emoji} Readiness Score: {score}/100")
            lines.append(f"   📊 Promotion Level: {level}")
            lines.append(f"   ✅ Ready for Production: {'YES' if ready else 'NO'}")
            
            if deployment_readiness.get("blockers"):
                lines.append(f"\n   ⚠️ Blockers:")
                for blocker in deployment_readiness["blockers"]:
                    lines.append(f"   • {blocker}")
            
            if deployment_readiness.get("recommendations"):
                lines.append(f"\n   💡 Recommendations:")
                for rec in deployment_readiness["recommendations"]:
                    lines.append(f"   • {rec}")
        
        # Promotion Recommendation
        lines.append("\n" + "=" * 100)
        lines.append("🎯 PROMOTION RECOMMENDATION")
        lines.append("=" * 100)
        
        if statistical_metrics and deployment_readiness:
            pf = statistical_metrics.profit_factor
            wr = statistical_metrics.win_rate
            n = statistical_metrics.sample_size
            score = deployment_readiness.get("score", 0)
            
            if pf >= 1.5 and wr >= 40 and n >= 100 and score >= 80:
                lines.append(f"\n   ✅ RECOMMENDATION: PROMOTE TO PRODUCTION")
                lines.append(f"   All criteria met for production deployment.")
            elif pf >= 1.0 and n >= 50:
                lines.append(f"\n   ⚠️ RECOMMENDATION: CONTINUE PAPER TRADING")
                lines.append(f"   Strategy shows promise but needs more validation.")
            else:
                lines.append(f"\n   ❌ RECOMMENDATION: DO NOT PROMOTE")
                lines.append(f"   Strategy does not meet minimum criteria.")
        
        # Failure Reasons
        if statistical_metrics:
            failures = []
            if statistical_metrics.profit_factor < 1.0:
                failures.append("Profit Factor below 1.0 — strategy is losing money")
            if statistical_metrics.win_rate < 30:
                failures.append("Win Rate below 30% — too many losing trades")
            if statistical_metrics.sample_size < 30:
                failures.append("Sample size below 30 — insufficient data")
            if statistical_metrics.overfitting_score > 0.7:
                failures.append("High overfitting score — may not generalize")
            if statistical_metrics.drift_detected:
                failures.append("Configuration drift detected — parameters may need adjustment")
            
            if failures:
                lines.append("\n" + "=" * 100)
                lines.append("❌ FAILURE REASONS")
                lines.append("=" * 100)
                for f in failures:
                    lines.append(f"\n   • {f}")
        
        # Suggested Next Actions
        lines.append("\n" + "=" * 100)
        lines.append("💡 SUGGESTED NEXT ACTIONS")
        lines.append("=" * 100)
        
        actions = []
        if statistical_metrics:
            if statistical_metrics.sample_size < 50:
                actions.append("Continue trading to build sample size (target: 100+ trades)")
            if statistical_metrics.profit_factor < 1.5:
                actions.append("Optimize entry/exit parameters to improve profit factor")
            if statistical_metrics.overfitting_score > 0.5:
                actions.append("Run out-of-sample validation to check for overfitting")
            if statistical_metrics.drift_detected:
                actions.append("Review recent trades for parameter drift")
            if not actions:
                actions.append("Strategy is performing well — continue monitoring")
        
        for i, action in enumerate(actions, 1):
            lines.append(f"\n   {i}. {action}")
        
        # Footer
        lines.append("\n" + "=" * 100)
        lines.append("📊 END OF REPORT")
        lines.append("=" * 100)
        
        return "\n".join(lines)
    
    def generate_summary(self, statistical_metrics: Any) -> str:
        """Generate a brief summary."""
        if not statistical_metrics:
            return "No data available."
        
        return (
            f"Trades: {statistical_metrics.sample_size} | "
            f"WR: {statistical_metrics.win_rate:.1f}% | "
            f"PF: {statistical_metrics.profit_factor:.2f} | "
            f"Exp: {statistical_metrics.expectancy:.4f} | "
            f"Sharpe: {statistical_metrics.sharpe_ratio:.2f} | "
            f"MaxDD: ${statistical_metrics.max_drawdown:.2f}"
        )


# Global singleton
_generator: Optional[ValidationReportGenerator] = None

def get_report_generator() -> ValidationReportGenerator:
    """Get or create the global report generator."""
    global _generator
    if _generator is None:
        _generator = ValidationReportGenerator()
    return _generator
