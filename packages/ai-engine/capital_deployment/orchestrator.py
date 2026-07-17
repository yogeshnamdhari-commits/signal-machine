"""
Capital Deployment Orchestrator — Main Coordinator
====================================================
Integrates all capital deployment components into a unified system:
- Capital Tier Management
- Position Sizing
- Portfolio Risk Management
- Kill Switch / Emergency Controls
- Slippage Analysis
- Performance Validation
- Automated Reporting
- Operational Monitoring
- Alert Dispatch

Usage:
    orchestrator = CapitalDeploymentOrchestrator(initial_capital=1000)
    await orchestrator.initialize()
    
    # On each signal
    result = await orchestrator.process_signal(signal)
    
    # Continuous monitoring
    await orchestrator.monitor()
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

import sys
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from capital_deployment.capital_tiers import CapitalTierManager, DeploymentTier
from capital_deployment.position_sizing_engine import (
    PositionSizingEngine, SizingRequest, PortfolioState, SizingMethod
)
from capital_deployment.portfolio_risk_engine import (
    PortfolioRiskEngine, PositionRisk, RiskLimits
)
from capital_deployment.kill_switch import EmergencyKillSwitch, KillTrigger
from capital_deployment.slippage_analyzer import SlippageAnalyzer
from capital_deployment.deployment_validator import DeploymentValidator
from capital_deployment.automated_reporter import AutomatedReporter
from capital_deployment.operational_monitor import OperationalMonitor
from capital_deployment.performance_validator import PerformanceValidator
from capital_deployment.alert_dispatcher import AlertDispatcher

DATA_DIR = _root / "data" / "capital"
REPORTS_DIR = _root / "data" / "reports"


@dataclass
class DeploymentState:
    """Current deployment state snapshot."""
    timestamp: str
    status: str  # SIMULATION, LIVE_TIER_1, LIVE_TIER_2, LIVE_TIER_3, LIVE_TIER_4
    current_tier: int
    capital_allocated: float
    total_equity: float
    portfolio_value: float
    total_pnl: float
    daily_pnl: float
    win_rate: float
    profit_factor: float
    max_drawdown: float
    portfolio_risk: float
    margin_usage: float
    active_positions: int
    uptime_pct: float
    health_score: float  # 0-100
    alerts: list = None
    operational_status: str = "HEALTHY"


class CapitalDeploymentOrchestrator:
    """
    Main orchestrator for capital deployment lifecycle.
    
    Coordinates:
    1. Capital Tier Management — graduated capital release
    2. Position Sizing — 7 methods, tier-aware
    3. Risk Management — portfolio limits, kill switch
    4. Slippage Analysis — execution quality tracking
    5. Performance Validation — drift detection
    6. Automated Reporting — daily/weekly/monthly
    7. Operational Monitoring — infrastructure health
    8. Alert Dispatch — multi-channel notifications
    """

    # Update intervals (seconds)
    PORTFOLIO_UPDATE_INTERVAL = 5.0
    RISK_CHECK_INTERVAL = 1.0
    REPORTER_INTERVAL = 60.0
    MONITOR_INTERVAL = 10.0
    SNAPSHOT_INTERVAL = 300.0  # 5 minutes

    def __init__(
        self,
        initial_capital: float = 1000.0,
        initial_tier: int = 0,
        config: Optional[dict] = None,
    ):
        """Initialize orchestrator with capital and tier."""
        self.config = config or {}
        self.initial_capital = initial_capital
        
        # Components
        self.tier_manager = CapitalTierManager(initial_tier, initial_capital)
        self.sizing_engine = PositionSizingEngine(self.config.get("sizing_config"))
        self.risk_engine = PortfolioRiskEngine(
            initial_capital, 
            RiskLimits(**self.config.get("risk_limits", {}))
        )
        self.kill_switch = EmergencyKillSwitch()
        self.slippage_analyzer = SlippageAnalyzer()
        self.validator = DeploymentValidator()
        self.reporter = AutomatedReporter()
        self.monitor = OperationalMonitor()
        self.perf_validator = PerformanceValidator()
        self.alerts = AlertDispatcher(self.config.get("alert_config", {}))
        
        # State
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._start_time = 0.0
        self._signals_processed = 0
        self._trades_executed = 0
        self._total_fees = 0.0
        
        logger.info(
            "CapitalDeploymentOrchestrator initialized: capital=$%.2f, tier=%d",
            initial_capital, initial_tier
        )

    async def initialize(self) -> None:
        """Initialize all components."""
        try:
            await self.monitor.initialize()
            await self.alerts.initialize()
            self._start_time = asyncio.get_event_loop().time()
            self._load_state()
            logger.info("CapitalDeploymentOrchestrator ready")
        except Exception as e:
            logger.error("Failed to initialize: %s", e)
            raise

    async def start(self) -> None:
        """Start background monitoring tasks."""
        if self._running:
            return

        self._running = True
        
        # Launch monitoring tasks
        self._tasks = [
            asyncio.create_task(self._portfolio_updater()),
            asyncio.create_task(self._risk_monitor()),
            asyncio.create_task(self._reporter_loop()),
            asyncio.create_task(self._operational_monitor()),
        ]
        
        logger.info("CapitalDeploymentOrchestrator monitoring started")

    async def stop(self) -> None:
        """Stop all background tasks."""
        self._running = False
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("CapitalDeploymentOrchestrator stopped")

    # ── Signal Processing ────────────────────────────────────────────────────
    async def process_signal(
        self,
        signal: dict,
    ) -> dict:
        """
        Process a trading signal end-to-end.
        
        Input signal format:
        {
            "signal_id": "SIG_...",
            "symbol": "BTCUSDT",
            "side": "LONG",  # or SHORT
            "entry_price": 42000,
            "stop_loss": 41000,
            "confidence": 0.75,
            "atr": 200,
            "volatility": 0.18,
            "win_rate": 0.55,  # Historical
            "payoff_ratio": 1.5,
        }
        """
        try:
            signal_id = signal.get("signal_id")
            
            # 1. Check kill switch
            if self.kill_switch.is_triggered():
                return {
                    "status": "REJECTED",
                    "reason": "kill_switch_active",
                    "signal_id": signal_id,
                }
            
            # 2. Get current portfolio state
            portfolio = self._get_portfolio_state()
            
            # 3. Build sizing request
            request = SizingRequest(
                symbol=signal.get("symbol"),
                signal_id=signal_id,
                side=signal.get("side", "LONG"),
                entry_price=signal.get("entry_price", 0),
                stop_loss_price=signal.get("stop_loss", 0),
                confidence=signal.get("confidence", 0.5),
                atr=signal.get("atr", 0),
                volatility=signal.get("volatility", 0),
                win_rate=signal.get("win_rate", 0.5),
                payoff_ratio=signal.get("payoff_ratio", 1.5),
                current_positions=portfolio.open_positions,
                total_exposure=portfolio.total_exposure,
                leverage=self.tier_manager.get_current_config().max_leverage,
                sector=signal.get("sector", "CRYPTO"),
            )
            
            # 4. Risk pre-check
            risk_check = self.risk_engine.check_limits()
            if risk_check:  # Violations exist
                logger.warning("Risk limit violation pre-check: %s", risk_check)
                return {
                    "status": "REJECTED",
                    "reason": "risk_limit_violation",
                    "violations": risk_check,
                    "signal_id": signal_id,
                }
            
            # 5. Size position
            tier = self.tier_manager.get_current_tier()
            sizing_method = {
                0: SizingMethod.FIXED_FRACTIONAL,  # Simulation
                1: SizingMethod.HALF_KELLY,         # Micro
                2: SizingMethod.VOLATILITY_ADJUSTED,# Small
                3: SizingMethod.RISK_PARITY,        # Moderate
                4: SizingMethod.HALF_KELLY,         # Production
            }.get(tier, SizingMethod.HALF_KELLY)
            
            sizing_result = self.sizing_engine.calculate(request, portfolio, sizing_method)
            
            # 6. Final risk check
            if sizing_result.risk_percent > self.risk_engine._limits.max_risk_per_trade * 100:
                return {
                    "status": "REJECTED",
                    "reason": "exceeds_per_trade_risk",
                    "requested_risk": sizing_result.risk_percent,
                    "max_risk": self.risk_engine._limits.max_risk_per_trade * 100,
                    "signal_id": signal_id,
                }
            
            self._signals_processed += 1
            
            return {
                "status": "ACCEPTED",
                "signal_id": signal_id,
                "sizing": asdict(sizing_result),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            
        except Exception as e:
            logger.error("Error processing signal: %s", e)
            return {
                "status": "ERROR",
                "reason": str(e),
                "signal_id": signal.get("signal_id"),
            }

    async def record_fill(self, fill: dict) -> None:
        """Record a trade execution fill."""
        try:
            symbol = fill.get("symbol")
            quantity = fill.get("quantity", 0)
            price = fill.get("price", 0)
            side = fill.get("side", "LONG")
            fees = fill.get("fees", 0)
            
            self._total_fees += fees
            self._trades_executed += 1
            
            # Record slippage
            expected = fill.get("expected_price", price)
            slippage_bps = (abs(price - expected) / expected * 10000) if expected > 0 else 0
            
            self.slippage_analyzer.record_slippage(
                trade_id=fill.get("trade_id"),
                symbol=symbol,
                side=side,
                expected_price=expected,
                actual_price=price,
                quantity=quantity,
                is_entry=True,
            )
            
            # Update portfolio
            pnl = fill.get("pnl", 0)
            self.risk_engine.record_trade(pnl)
            
            # Update tier
            is_win = pnl > 0
            self.tier_manager.record_trade(pnl, is_win)
            
            logger.info("Recorded fill: %s %s %.4f @ %.2f, pnl=%.2f",
                       side, symbol, quantity, price, pnl)
            
        except Exception as e:
            logger.error("Error recording fill: %s", e)

    async def record_close(self, close: dict) -> None:
        """Record a position close with exit price."""
        try:
            expected = close.get("expected_exit_price", close.get("actual_exit_price"))
            actual = close.get("actual_exit_price")
            
            self.slippage_analyzer.record_slippage(
                trade_id=close.get("trade_id"),
                symbol=close.get("symbol"),
                side=close.get("side"),
                expected_price=expected,
                actual_price=actual,
                quantity=close.get("quantity"),
                is_entry=False,
            )
            
        except Exception as e:
            logger.error("Error recording close: %s", e)

    # ── Monitoring ───────────────────────────────────────────────────────────
    async def _portfolio_updater(self) -> None:
        """Periodically update portfolio snapshot."""
        while self._running:
            try:
                await asyncio.sleep(self.PORTFOLIO_UPDATE_INTERVAL)
                snapshot = self.risk_engine.get_snapshot()
                self.risk_engine.update_equity(snapshot.total_equity)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Portfolio update error: %s", e)

    async def _risk_monitor(self) -> None:
        """Continuously monitor risk and enforce limits."""
        while self._running:
            try:
                await asyncio.sleep(self.RISK_CHECK_INTERVAL)
                
                # Check risk limits
                violations = self.risk_engine.check_limits()
                if violations:
                    logger.warning("Risk violations: %s", violations)
                    await self.alerts.send_alert(
                        "RISK_VIOLATION",
                        f"{len(violations)} risk limit violations detected",
                        violations
                    )
                
                # Check drawdown
                snapshot = self.risk_engine.get_snapshot()
                dd = (snapshot.total_equity - self.initial_capital) / self.initial_capital
                
                if dd < -0.10:
                    logger.critical("Drawdown breach: %.1%% — triggering kill switch", dd)
                    self.kill_switch.trigger(KillTrigger.DRAWDOWN_BREACH, f"DD {dd:.1%}")
                    await self.alerts.send_alert(
                        "KILL_SWITCH_ACTIVATED",
                        f"Drawdown {dd:.1%} exceeded -10%",
                        {"drawdown": dd}
                    )
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Risk monitor error: %s", e)

    async def _reporter_loop(self) -> None:
        """Generate periodic reports."""
        while self._running:
            try:
                await asyncio.sleep(self.REPORTER_INTERVAL)
                
                # Daily report
                report = self.reporter.generate_daily_report(
                    self.risk_engine.get_snapshot(),
                    self.slippage_analyzer.get_report(),
                )
                
                # Save to disk
                REPORTS_DIR.mkdir(parents=True, exist_ok=True)
                path = REPORTS_DIR / f"daily_report_{datetime.now():%Y%m%d_%H%M%S}.json"
                path.write_text(json.dumps(asdict(report), indent=2, default=str))
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Reporter error: %s", e)

    async def _operational_monitor(self) -> None:
        """Monitor system health."""
        while self._running:
            try:
                await asyncio.sleep(self.MONITOR_INTERVAL)
                
                health = self.monitor.check_all()
                if health.health_score < 50:
                    logger.warning("System health degraded: %.0f", health.health_score)
                    await self.alerts.send_alert(
                        "SYSTEM_HEALTH",
                        f"Health score {health.health_score:.0f}",
                        {"health_score": health.health_score}
                    )
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Operational monitor error: %s", e)

    # ── Tier Promotion/Demotion ──────────────────────────────────────────────
    async def evaluate_tier_change(self) -> dict:
        """Evaluate whether to promote or demote capital tier."""
        # Check promotion
        can_promote, blockers = self.tier_manager.evaluate_promotion()
        
        if can_promote and self.tier_manager.get_current_tier() < 4:
            tier_config = self.tier_manager.get_current_config()
            new_capital = min(tier_config.max_capital, self.risk_engine._equity * 1.5)
            
            promoted = self.tier_manager.promote(new_capital)
            if promoted:
                await self.alerts.send_alert(
                    "TIER_PROMOTION",
                    f"Promoted to Tier {self.tier_manager.get_current_tier()}",
                    {"new_capital": new_capital}
                )
                return {"action": "PROMOTED", "new_tier": self.tier_manager.get_current_tier()}
        
        # Check demotion
        should_demote, reasons = self.tier_manager.check_demotion()
        if should_demote:
            demoted = self.tier_manager.demote()
            if demoted:
                await self.alerts.send_alert(
                    "TIER_DEMOTION",
                    f"Demoted to Tier {self.tier_manager.get_current_tier()}: {reasons}",
                    {"reasons": reasons}
                )
                return {"action": "DEMOTED", "new_tier": self.tier_manager.get_current_tier(), "reasons": reasons}
        
        return {"action": "NO_CHANGE"}

    # ── State & Reporting ────────────────────────────────────────────────────
    def get_state(self) -> DeploymentState:
        """Get current deployment state."""
        snapshot = self.risk_engine.get_snapshot()
        tier_state = self.tier_manager.get_state()
        
        uptime = 100.0
        if self._start_time > 0:
            elapsed = (asyncio.get_event_loop().time() - self._start_time) / 3600
            uptime = min(100.0, elapsed / (elapsed + 0.001))  # Approximate
        
        return DeploymentState(
            timestamp=datetime.now(timezone.utc).isoformat(),
            status=["SIMULATION", "LIVE_TIER_1", "LIVE_TIER_2", "LIVE_TIER_3", "LIVE_TIER_4"][
                self.tier_manager.get_current_tier()
            ],
            current_tier=self.tier_manager.get_current_tier(),
            capital_allocated=self.tier_manager._capital,
            total_equity=snapshot.total_equity,
            portfolio_value=snapshot.total_exposure,
            total_pnl=snapshot.total_unrealized_pnl,
            daily_pnl=self.risk_engine._daily_pnl,
            win_rate=tier_state.tier_win_rate,
            profit_factor=tier_state.tier_profit_factor,
            max_drawdown=tier_state.tier_max_drawdown,
            portfolio_risk=snapshot.portfolio_risk_pct,
            margin_usage=snapshot.margin_usage_pct,
            active_positions=snapshot.total_positions,
            uptime_pct=uptime,
            health_score=self.monitor.health_score,
            operational_status="HEALTHY" if not self.kill_switch.is_triggered() else "HALTED",
        )

    def _get_portfolio_state(self) -> PortfolioState:
        """Get current portfolio for sizing."""
        snapshot = self.risk_engine.get_snapshot()
        return PortfolioState(
            total_equity=snapshot.total_equity,
            available_margin=snapshot.available_margin,
            open_positions=snapshot.total_positions,
            total_exposure=snapshot.total_exposure,
            current_drawdown=self.risk_engine._get_drawdown(),
            daily_pnl=self.risk_engine._daily_pnl,
            weekly_pnl=self.risk_engine._weekly_pnl,
            monthly_pnl=self.risk_engine._monthly_pnl,
            tier=self.tier_manager.get_current_tier(),
            symbol_exposures=snapshot.sector_exposures,
            sector_exposures=snapshot.sector_exposures,
        )

    # ── State Persistence ────────────────────────────────────────────────────
    def _save_state(self) -> None:
        """Save orchestrator state."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "deployment_state": asdict(self.get_state()),
            "signals_processed": self._signals_processed,
            "trades_executed": self._trades_executed,
            "total_fees": self._total_fees,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        (DATA_DIR / "orchestrator_state.json").write_text(json.dumps(state, indent=2, default=str))

    def _load_state(self) -> None:
        """Load persisted orchestrator state."""
        path = DATA_DIR / "orchestrator_state.json"
        if path.exists():
            try:
                state = json.loads(path.read_text())
                self._signals_processed = state.get("signals_processed", 0)
                self._trades_executed = state.get("trades_executed", 0)
                self._total_fees = state.get("total_fees", 0.0)
            except Exception as e:
                logger.error("Failed to load state: %s", e)

    def get_stats(self) -> dict:
        """Get orchestrator statistics."""
        self._save_state()
        return {
            "deployment_state": asdict(self.get_state()),
            "signals_processed": self._signals_processed,
            "trades_executed": self._trades_executed,
            "total_fees": round(self._total_fees, 2),
            "tier_manager": self.tier_manager.get_stats(),
            "sizing_engine": self.sizing_engine.get_stats(),
            "slippage": self.slippage_analyzer.get_report(),
        }
