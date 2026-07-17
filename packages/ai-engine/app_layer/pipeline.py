"""
App Layer Pipeline — Orchestrates the complete trade decision flow.

This is the main entry point for the App Layer. It chains all engines
in the correct order and produces a final trade decision.

READ-ONLY with respect to upstream data.

Pipeline Flow (Per Master Directive + Executive Assessment):
    Signal → Trade Quality → Institution Agreement → Expectancy
    → Regime Filter → Reward Filter → Correlation → Portfolio
    → Adaptive Risk → Expected Profit Score → Position Sizing
    → Adaptive TP → Execution Quality → Lifecycle Registration
    → Position Queue → Priority → Decision Audit
    → Decision (EXECUTE / REJECT / MONITOR)

Executive Assessment Additions:
    - Expected Profit Score calculation (Problem 8)
    - Adaptive take-profit (Problem 6)
    - Trade lifecycle registration (Problem 11)
    - Capital allocation by expected profit (Problem 10)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from .trade_quality_engine import TradeQualityEngine, TradeQualityScore
from .institution_agreement_engine import InstitutionAgreementEngine, AgreementResult
from .expectancy_engine import ExpectancyEngine, ExpectancyResult
from .regime_filter import RegimeFilter, RegimeFilterResult
from .reward_filter import RewardFilter, RewardFilterResult
from .correlation_engine import CorrelationEngine, CorrelationCheckResult
from .position_sizing_engine import (
    AppPositionSizingEngine, SizingResult, calculate_expected_profit_score,
)
from .execution_quality import ExecutionQualityFilter, ExecutionQualityResult
from .adaptive_risk import AdaptiveRiskGovernor, RiskDecision
from .exit_engine import AppExitEngine
from .trade_lifecycle_manager import TradeLifecycleManager
from .portfolio_manager import PortfolioManager, PortfolioCheckResult
from .position_queue import PositionQueue, QueueDecision
from .signal_priority import SignalPriorityClassifier, PriorityResult
from .decision_audit import DecisionAuditLogger
from .bridge_enricher import BridgeEnricher
from .execution_eligibility import ExecutionEligibilityEngine, EligibilityResult
from .continuous_learning import ContinuousLearningLayer
from .portfolio_ranking import PortfolioRankingEngine, RankingResult
from .trade_quality_validator import TradeQualityValidator, ValidationResult
from .correlation_portfolio_selector import CorrelationAwarePortfolioSelector, PortfolioSelectionResult
from .mfe_mae_dashboard import MFE_MAEDashboard, DashboardMetrics
from .profit_capture_analytics import ProfitCaptureAnalytics, ProfitCaptureDashboard
from .symbol_auto_manager import SymbolAutoManager, SymbolManagementResult
from .regime_adaptive_risk import RegimeAdaptiveRiskSizing, RegimeRiskResult
from .rejected_trade_learner import RejectedTradeLearner, RejectionLearningResult
from .portfolio_health import RollingPortfolioHealth, PortfolioHealthDashboard
from .state_exit_engine import StateBasedExitEngine, StateExitDecision, TrendIndicators
from .predictive_symbol_scorer import PredictiveSymbolScorer, SymbolScore
from .opportunity_cost_tracker import OpportunityCostTracker, OpportunityCostDashboard
from .confidence_exit_engine import ConfidenceExitEngine, ConfidenceExitDecision
from .capital_competition import CapitalCompetitionAllocator, CapitalCompetitionResult
from .validation_framework import ValidationFramework, ValidationReport
from .module_contribution import ModuleContributionAnalyzer, ContributionReport
from .production_gates import ProductionGateChecker, ProductionReadinessReport
from .ablation_matrix import AblationMatrixAnalyzer, AblationMatrixReport
from .market_memory import MarketMemoryEngine, MarketMemoryResult
from .parameter_calibration import ParameterCalibrationEngine, CalibrationReport
from .rolling_stability import RollingStabilityMonitor, StabilityMetrics
from .expected_path import ExpectedPathPredictor, TradePathPrediction
from .adaptive_risk_scaler import AdaptiveRiskScaler, AdaptiveRiskResult
from .probabilistic_path import ProbabilisticPathPredictor, ProbabilisticPrediction
from .smooth_risk_scaler import SmoothRiskScaler, SmoothRiskResult
from .prediction_error import PredictionErrorTracker, PredictionErrorReport
from .multivariate_path import MultivariatePathPredictor, MultivariatePrediction
from .portfolio_ev import PortfolioEVCalculator, PortfolioEVReport
from .feature_stability import FeatureStabilityAnalyzer, FeatureStabilityReport
from .calibration_error import CalibrationErrorTracker, CalibrationReport as CalReport
from .regime_calibrator import RegimeSpecificCalibrator, RegimeCalibrationReport
from .model_health import ModelHealthMonitor, ModelHealthDashboard
from .feature_interactions import FeatureInteractionDetector, InteractionReport
from .model_reliability import ModelReliabilityScorer, ReliabilityScore
from .admission_quality import AdmissionQualityTracker, AdmissionQualityReport
from .portfolio_intelligence import PortfolioIntelligenceEngine, PortfolioIntelligenceReport
from .three_way_validation import ThreeWayValidationEngine
from .system_health import SystemHealthMonitor, SystemHealthScore
from .exit_analysis import ExitAnalysisDashboard, ExitAnalysisReport
from .profit_capture import ProfitCaptureDashboardEngine, ProfitCaptureDashboard as PCDashboard
from .edge_confidence import EdgeConfidenceScorer, EdgeConfidenceScore
from .transparent_edge import TransparentEdgeScorer, TransparentEdgeScore
from .opportunity_cost_v2 import OpportunityCostTrackerV2, OpportunityCostReport
from .admission_metrics import AdmissionMetricsCalculator, AdmissionMetricsResult
from .rolling_metrics import RollingMetricsDashboardEngine, RollingMetricsDashboard
from .admission_dashboard import AdmissionDashboardEngine, AdmissionDashboard
from .contribution_display import ContributionDisplayEngine, ContributionDisplay
from .execution_funnel import ExecutionFunnelEngine, ExecutionFunnel
from .execution_analytics import ExecutionAnalyticsEngine, ExecutionAnalyticsReport
from .trade_governance import TradeGovernanceEngine, GovernanceDecision, StaleTradeExit
from .portfolio_admission import PortfolioAdmissionEngine, AdmissionDecision
from .continuous_trade_monitor import ContinuousTradeMonitor, MonitorDecision
from .evidence_engine import EvidenceBasedDecisionEngine, TQIResult


@dataclass
class PipelineResult:
    """Complete result from the App Layer pipeline."""
    symbol: str = ""
    side: str = ""
    decision: str = "REJECT"  # EXECUTE / REJECT / MONITOR
    priority: str = "REJECT"

    # Engine outputs
    trade_quality: Optional[TradeQualityScore] = None
    institution_agreement: Optional[AgreementResult] = None
    expectancy: Optional[ExpectancyResult] = None
    regime: Optional[RegimeFilterResult] = None
    reward: Optional[RewardFilterResult] = None
    correlation: Optional[CorrelationCheckResult] = None
    portfolio: Optional[PortfolioCheckResult] = None
    sizing: Optional[SizingResult] = None
    execution_quality: Optional[ExecutionQualityResult] = None
    risk_decision: Optional[RiskDecision] = None
    priority_result: Optional[PriorityResult] = None

    # Executive Assessment additions
    expected_profit_score: float = 0.0   # Problem 8: Expected Profit Score
    adaptive_tp: Optional[Dict] = None   # Problem 6: Adaptive take-profit levels

    # v2 additions
    eligibility: Optional[EligibilityResult] = None   # Execution Eligibility
    ranking: Optional[RankingResult] = None            # Portfolio Ranking
    learning_summary: Optional[Dict] = None            # Continuous Learning
    capital_allocation: float = 1.0                     # Rank-based allocation
    adaptive_risk_score: float = 1.0                    # Learning-based risk

    # v3 additions
    quality_validation: Optional[ValidationResult] = None  # Trade Quality Validation
    portfolio_selection: Optional[PortfolioSelectionResult] = None  # Correlation-aware selection
    dashboard_metrics: Optional[DashboardMetrics] = None   # MFE/MAE Analytics
    diversification_bonus: float = 1.0                      # Portfolio diversification bonus
    validation_score: float = 0.0                           # Historical expectancy validation

    # Final trade parameters (if EXECUTE)
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    take_profit_1: float = 0.0   # Multi-TP support
    take_profit_2: float = 0.0
    take_profit_3: float = 0.0
    quantity: float = 0.0
    position_value: float = 0.0
    risk_amount: float = 0.0
    expected_r: float = 0.0

    # Metadata
    timestamp: float = 0.0
    processing_time_ms: float = 0.0
    rejection_reasons: List[str] = field(default_factory=list)

    # v24 additions — Measurement
    rolling_metrics: Optional[RollingMetricsDashboard] = None
    admission_dashboard: Optional[AdmissionDashboard] = None
    contribution_display: Optional[ContributionDisplay] = None
    execution_funnel: Optional[ExecutionFunnel] = None
    execution_analytics: Optional[ExecutionAnalyticsReport] = None

    def to_dict(self) -> Dict:
        result = {
            "symbol": self.symbol,
            "side": self.side,
            "decision": self.decision,
            "priority": self.priority,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "take_profit_3": self.take_profit_3,
            "quantity": round(self.quantity, 6),
            "position_value": round(self.position_value, 2),
            "risk_amount": round(self.risk_amount, 2),
            "expected_r": round(self.expected_r, 2),
            "expected_profit_score": round(self.expected_profit_score, 1),
            "capital_allocation": round(self.capital_allocation, 2),
            "adaptive_risk_score": round(self.adaptive_risk_score, 3),
            "rejection_reasons": self.rejection_reasons,
            "processing_time_ms": round(self.processing_time_ms, 2),
            "timestamp": self.timestamp,
        }
        # Add engine details
        if self.trade_quality:
            result["trade_quality_score"] = self.trade_quality.composite_score
            result["trade_quality_priority"] = self.trade_quality.priority
        if self.institution_agreement:
            result["institution_agreement"] = self.institution_agreement.agreement_ratio
        if self.expectancy:
            result["expected_value_r"] = self.expectancy.expected_value_r
            result["is_positive_ev"] = self.expectancy.is_positive_ev
        if self.execution_quality:
            result["execution_quality_score"] = self.execution_quality.quality_score
        if self.correlation:
            result["correlation_reduction"] = self.correlation.correlation_reduction
        if self.risk_decision:
            result["risk_state"] = self.risk_decision.state
            result["risk_multiplier"] = self.risk_decision.risk_multiplier
        if self.adaptive_tp:
            result["adaptive_tp"] = self.adaptive_tp
        if self.eligibility:
            result["execution_score"] = self.eligibility.execution_score
            result["is_a_plus"] = self.eligibility.is_a_plus
        if self.quality_validation:
            result["validation_score"] = self.quality_validation.validation_score
            result["is_blocked"] = self.quality_validation.is_blocked
        if self.portfolio_selection:
            result["diversification_bonus"] = self.diversification_bonus
        return result


class AppLayerPipeline:
    """
    Main orchestrator for the App Layer trade decision pipeline.

    v3 Pipeline Flow:
        Raw Signals → Trade Quality Validation → Eligibility
        → Portfolio Ranking → Correlation-Aware Selection → Execution

    READ-ONLY: never modifies upstream data.
    """

    def __init__(self) -> None:
        self.quality = TradeQualityEngine()
        self.institution = InstitutionAgreementEngine()
        self.expectancy = ExpectancyEngine()
        self.regime = RegimeFilter()
        self.reward = RewardFilter()
        self.correlation = CorrelationEngine()
        self.sizing = AppPositionSizingEngine()
        self.execution_quality = ExecutionQualityFilter()
        self.adaptive_risk = AdaptiveRiskGovernor()
        self.exit = AppExitEngine()
        self.lifecycle = TradeLifecycleManager()  # Problem 11: Full lifecycle management
        self.portfolio = PortfolioManager()
        self.queue = PositionQueue()
        self.priority = SignalPriorityClassifier()
        self.audit = DecisionAuditLogger()
        self.enricher = BridgeEnricher()

        # v2 additions
        self.eligibility = ExecutionEligibilityEngine()
        self.learning = ContinuousLearningLayer()
        self.ranking = PortfolioRankingEngine()

        # v3 additions
        self.validator = TradeQualityValidator()
        self.portfolio_selector = CorrelationAwarePortfolioSelector()
        self.dashboard = MFE_MAEDashboard()

        # v4 additions
        self.profit_capture = ProfitCaptureAnalytics()
        self.symbol_manager = SymbolAutoManager()
        self.regime_risk = RegimeAdaptiveRiskSizing()
        self.rejection_learner = RejectedTradeLearner()
        self.portfolio_health = RollingPortfolioHealth()

        # v5 additions
        self.state_exit = StateBasedExitEngine()
        self.predictive_scorer = PredictiveSymbolScorer()
        self.opportunity_tracker = OpportunityCostTracker()

        # v6 additions
        self.confidence_exit = ConfidenceExitEngine()
        self.capital_competition = CapitalCompetitionAllocator()
        self.validation = ValidationFramework()

        # v7 additions
        self.module_analyzer = ModuleContributionAnalyzer()
        self.production_gates = ProductionGateChecker()

        # v8 additions
        self.ablation_matrix = AblationMatrixAnalyzer()
        self.market_memory = MarketMemoryEngine()

        # v9 additions
        self.calibration = ParameterCalibrationEngine()
        self.stability_monitor = RollingStabilityMonitor()

        # v10 additions
        self.path_predictor = ExpectedPathPredictor()
        self.adaptive_risk_scaler = AdaptiveRiskScaler()

        # v11 additions
        self.probabilistic_path = ProbabilisticPathPredictor()
        self.smooth_risk = SmoothRiskScaler()
        self.prediction_error = PredictionErrorTracker()

        # v12 additions
        self.multivariate_path = MultivariatePathPredictor()
        self.portfolio_ev = PortfolioEVCalculator()

        # v13 additions
        self.feature_stability = FeatureStabilityAnalyzer()
        self.calibration_error = CalibrationErrorTracker()
        self.regime_calibrator = RegimeSpecificCalibrator()

        # v14 additions
        self.model_health = ModelHealthMonitor()
        self.feature_interactions = FeatureInteractionDetector()

        # v15 additions
        self.model_reliability = ModelReliabilityScorer()
        self.admission_quality = AdmissionQualityTracker()

        # v16 additions
        self.portfolio_intelligence = PortfolioIntelligenceEngine()
        self.three_way_validation = ThreeWayValidationEngine()
        self.system_health = SystemHealthMonitor()

        # v17 additions
        self.exit_analysis = ExitAnalysisDashboard()
        self.profit_capture_dashboard = ProfitCaptureDashboardEngine()
        self.edge_confidence = EdgeConfidenceScorer()

        # v18 additions
        self.transparent_edge = TransparentEdgeScorer()
        self.opportunity_cost_v2 = OpportunityCostTrackerV2()
        self.admission_metrics = AdmissionMetricsCalculator()

        # v24 additions — Measurement & Transparency
        self.rolling_metrics = RollingMetricsDashboardEngine()
        self.admission_dashboard = AdmissionDashboardEngine()
        self.contribution_display = ContributionDisplayEngine()
        self.execution_funnel = ExecutionFunnelEngine()
        self.execution_analytics = ExecutionAnalyticsEngine()

        # Trade Governance — institutional risk controls
        self.governance = TradeGovernanceEngine()
        self.admission = PortfolioAdmissionEngine()
        self.trade_monitor = ContinuousTradeMonitor()
        self.evidence = EvidenceBasedDecisionEngine()

        self._history: List[PipelineResult] = []

    def rank_and_filter_signals(
        self,
        signals: List[Dict[str, Any]],
        open_positions: Optional[List[Dict]] = None,
        market_data: Optional[Dict] = None,
        balance: float = 10_000.0,
    ) -> RankingResult:
        """
        Rank and filter signals using the Portfolio Ranking Engine.

        v3 Pipeline Flow:
            Raw Signals → Trade Quality Validation → Eligibility
            → Portfolio Ranking → Correlation-Aware Selection → Execution

        This is the NEW entry point for batch signal processing.

        v4 Pipeline Flow:
            Raw Signals → Symbol Auto-Check → Trade Quality Validation
            → Eligibility → Regime Adaptive Risk → Portfolio Ranking
            → Correlation-Aware Selection → Execution

        Args:
            signals: List of signal dicts from scanner
            open_positions: Current open positions
            market_data: Optional market data
            balance: Account balance

        Returns:
            RankingResult with executed and rejected signals
        """
        # Inject learning data into adaptive risk
        strategy_pf = self.learning.get_strategy_pf()
        self.adaptive_risk._strategy_pf = strategy_pf

        # ═══════════════════════════════════════════════════════
        # STAGE -1: Trade Governance (Kill Switch, Symbol/Session Blacklist,
        #           Confidence Calibration, Daily Loss Stop, Exposure)
        # ═══════════════════════════════════════════════════════
        if signals:
            gov_result = self.governance.evaluate_signal(
                signals[0],
                open_positions=open_positions,
                balance=balance,
            )
            if not gov_result.approved:
                logger.warning(
                    "🚫 GOVERNANCE BLOCKED ALL SIGNALS: {}",
                    gov_result.rejection_reasons,
                )
                return RankingResult(
                    total_signals=len(signals),
                    executed_count=0,
                    eligible_signals=0,
                    rejected_signals=[],
                    executed_signals=[],
                    ranked_signals=[],
                )

        # ═══════════════════════════════════════════════════════
        # STAGE -0.5: Evidence-Based TQI Pre-Filter
        # Reject signals that resemble historically losing setups
        # ═══════════════════════════════════════════════════════
        tqi_survivors = []
        for sig in signals:
            tqi = self.evidence.query_tqi(sig, market_data)
            if tqi.admit:
                tqi_survivors.append(sig)
            else:
                logger.debug(
                    "TQI PRE-FILTER REJECT: {} {} — {}",
                    sig.get("symbol", ""), sig.get("side", ""), tqi.reason,
                )
        if len(tqi_survivors) != len(signals):
            logger.info(
                "📊 TQI PRE-FILTER: {} → {} signals ({} rejected by historical evidence)",
                len(signals), len(tqi_survivors), len(signals) - len(tqi_survivors),
            )
        signals = tqi_survivors

        # ═══════════════════════════════════════════════════════
        # STAGE 0: Symbol Auto-Management (v4 — NEW)
        # ═══════════════════════════════════════════════════════
        # Filter out disabled symbols before validation
        symbol_result = self.symbol_manager.evaluate()
        disabled_symbols = set(symbol_result.newly_disabled) if hasattr(symbol_result, 'newly_disabled') else set()
        # Also get currently disabled from summary
        disabled_symbols.update(self.symbol_manager.get_disabled_symbols())

        pre_filter_count = len(signals)
        signals = [s for s in signals if s.get("symbol", "") not in disabled_symbols]
        if pre_filter_count != len(signals):
            logger.info(
                "📊 SYMBOL FILTER: {} → {} ({} disabled)",
                pre_filter_count, len(signals), pre_filter_count - len(signals),
            )

        # ═══════════════════════════════════════════════════════
        # STAGE 1: Trade Quality Validation (v3)
        # ═══════════════════════════════════════════════════════
        # Validate signals against historical performance BEFORE eligibility
        validated_signals = []
        for sig in signals:
            validation = self.validator.validate(sig)
            if validation.valid:
                sig["_validation_score"] = validation.validation_score
                sig["_validation"] = validation
                validated_signals.append(sig)
            else:
                # Log rejection for learning
                self.rejection_learner.log_rejection(
                    symbol=sig.get("symbol", ""),
                    side=sig.get("side", ""),
                    rejection_stage="validation",
                    rejection_reason=validation.rejection_reason,
                    scores={"validation_score": validation.validation_score},
                )
                logger.debug(
                    "VALIDATOR REJECT: {} {} — {}",
                    sig.get("symbol", ""), sig.get("side", ""),
                    validation.rejection_reason,
                )

        logger.info(
            "📊 VALIDATOR: {} signals → {} validated",
            len(signals), len(validated_signals),
        )

        # ═══════════════════════════════════════════════════════
        # STAGE 2: Regime Adaptive Risk (v4 — NEW)
        # ═══════════════════════════════════════════════════════
        # Inject regime risk multiplier into signals
        if validated_signals:
            # Use first signal's regime as current regime
            current_regime = validated_signals[0].get("regime", "unknown")
            regime_result = self.regime_risk.evaluate(current_regime)
            regime_mult = regime_result.risk_multiplier

            for sig in validated_signals:
                sig["_regime_risk_mult"] = regime_mult
                sig["_regime_stats"] = regime_result.regime_stats

        # ═══════════════════════════════════════════════════════
        # STAGE 3-5: Existing Pipeline (Eligibility + Ranking)
        # ═══════════════════════════════════════════════════════
        result = self.ranking.rank_and_select(
            validated_signals, open_positions, market_data, balance,
        )

        # Log rejections for learning
        for rs in result.rejected_signals:
            if not rs.blocked:
                self.rejection_learner.log_rejection(
                    symbol=rs.signal.get("symbol", ""),
                    side=rs.signal.get("side", ""),
                    rejection_stage="eligibility",
                    rejection_reason=rs.block_reason or "not eligible",
                    scores={"execution_score": rs.eligibility.execution_score},
                )

        # ═══════════════════════════════════════════════════════
        # STAGE 6: Correlation-Aware Portfolio Selection (v3 — NEW)
        # ═══════════════════════════════════════════════════════
        # Get the ranked signals from the result
        ranked_dicts = []
        for rs in result.executed_signals:
            ranked_dicts.append(rs.signal)

        # Also include signals that were eligible but not yet selected
        for rs in result.ranked_signals:
            if rs.eligibility.eligible and not rs.blocked:
                if rs.signal not in ranked_dicts:
                    ranked_dicts.append(rs.signal)

        portfolio_result = self.portfolio_selector.select_for_execution(
            ranked_dicts, open_positions, result.eligible_signals,
        )

        # Update result with portfolio selection
        result.executed_count = min(result.executed_count, portfolio_result.selected_count)
        result.executed_signals = result.executed_signals[:portfolio_result.selected_count]

        logger.info(
            "📊 PORTFOLIO: {} eligible → {} selected (diversification={:.1f})",
            result.eligible_signals, portfolio_result.selected_count,
            portfolio_result.diversification.diversification_score if portfolio_result.diversification else 0,
        )

        return result

    def process_signal(
        self,
        signal: Dict[str, Any],
        market_data: Optional[Dict] = None,
        open_positions: Optional[List[Dict]] = None,
        balance: float = 10_000.0,
        daily_pnl: float = 0.0,
    ) -> PipelineResult:
        """
        Process a single signal through the complete pipeline.

        Args:
            signal: Live Sheet signal dict
            market_data: Optional market data for the symbol
            open_positions: Current open positions
            balance: Account balance
            daily_pnl: Today's realized PnL

        Returns:
            PipelineResult with complete decision
        """
        start = time.time()
        result = PipelineResult(
            symbol=signal.get("symbol", ""),
            side=signal.get("side", ""),
            timestamp=time.time(),
        )

        positions = open_positions or []

        # ═══════════════════════════════════════════════════════
        # STAGE -1: Trade Governance Pre-Check
        # (Kill Switch, Symbol/Session Blacklist,
        #  Confidence Calibration, Daily Loss, Exposure)
        # ═══════════════════════════════════════════════════════
        gov_decision = self.governance.evaluate_signal(
            signal, open_positions=positions, balance=balance,
        )
        if not gov_decision.approved:
            result.decision = "REJECT"
            result.priority = "REJECT"
            result.rejection_reasons.extend(gov_decision.rejection_reasons)
            result.processing_time_ms = (time.time() - start) * 1000
            # Build module status for attribution
            modules = {"governance": "REJECT"}
            if gov_decision.kill_switch:
                modules["kill_switch"] = "REJECT" if gov_decision.kill_switch.active else "PASS"
            if gov_decision.symbol_blacklist:
                modules["symbol_blacklist"] = gov_decision.symbol_blacklist.status
            if gov_decision.session_blacklist:
                modules["session_blacklist"] = gov_decision.session_blacklist.status
            if gov_decision.confidence_bucket:
                modules["confidence_calibration"] = gov_decision.confidence_bucket.status
            if gov_decision.daily_loss:
                modules["daily_loss_stop"] = "REJECT" if gov_decision.daily_loss.blocked else "PASS"
            if gov_decision.exposure:
                modules["max_exposure"] = "PASS" if gov_decision.exposure.approved else "REJECT"
            self.evidence.log_decision(
                signal, "REJECT",
                reason="; ".join(gov_decision.rejection_reasons),
                modules_checked=modules,
            )
            self._history.append(result)
            logger.info(
                "GOVERNANCE REJECT: {} {} — {}",
                result.symbol, result.side, gov_decision.rejection_reasons,
            )
            return result

        # ═══════════════════════════════════════════════════════
        # STAGE -0.5: Evidence-Based TQI Check
        # Query historical similar trades for expected outcome
        # ═══════════════════════════════════════════════════════
        tqi_result = self.evidence.query_tqi(signal, market_data)
        if not tqi_result.admit:
            result.decision = "REJECT"
            result.priority = "REJECT"
            result.rejection_reasons.append(
                f"tqi_rejected: {tqi_result.reason}"
            )
            result.processing_time_ms = (time.time() - start) * 1000
            self.evidence.log_decision(
                signal, "REJECT",
                reason=tqi_result.reason,
                tqi_data=tqi_result.to_dict(),
                modules_checked={"governance": "PASS", "tqi": "REJECT"},
            )
            self._history.append(result)
            logger.info(
                "TQI REJECT: {} {} — {}",
                result.symbol, result.side, tqi_result.reason,
            )
            return result

        # ═══════════════════════════════════════════════════════
        # STAGE -0.4: Expected Value Computation
        # Estimate dollar EV from similar historical trades
        # ═══════════════════════════════════════════════════════
        ev_result = self.evidence.compute_ev(signal, market_data, balance)
        if not ev_result.get("positive_ev", False):
            result.decision = "REJECT"
            result.priority = "REJECT"
            result.rejection_reasons.append(
                f"negative_ev: ${ev_result.get('ev_usd', 0):.2f} "
                f"(win_prob={ev_result.get('win_probability', 0):.1%}, "
                f"similar_pf={ev_result.get('similar_pf', 0):.2f})"
            )
            result.processing_time_ms = (time.time() - start) * 1000
            self.evidence.log_decision(
                signal, "REJECT",
                reason=f"negative_ev ${ev_result.get('ev_usd', 0):.2f}",
                ev_data=ev_result,
                tqi_data=tqi_result.to_dict(),
                modules_checked={"governance": "PASS", "tqi": "PASS", "ev_engine": "REJECT"},
            )
            self._history.append(result)
            logger.info(
                "EV REJECT: {} {} — EV=${:.2f} win_prob={:.1%}",
                result.symbol, result.side,
                ev_result.get("ev_usd", 0),
                ev_result.get("win_probability", 0),
            )
            return result

        # ═══════════════════════════════════════════════════════
        # STAGE 0: Trade Quality Validation (v3 — NEW)
        # ═══════════════════════════════════════════════════════
        # Validate signal against historical performance BEFORE technical analysis
        result.quality_validation = self.validator.validate(signal)
        result.validation_score = result.quality_validation.validation_score

        if not result.quality_validation.valid:
            result.decision = "REJECT"
            result.priority = "REJECT"
            result.rejection_reasons.append(
                f"quality_validation_rejected: score={result.validation_score:.1f} "
                f"({result.quality_validation.rejection_reason})"
            )
            result.processing_time_ms = (time.time() - start) * 1000
            self._history.append(result)
            return result

        # ═══════════════════════════════════════════════════════
        # STAGE 1: Trade Quality Score
        # ═══════════════════════════════════════════════════════
        result.trade_quality = self.quality.score_signal(signal, market_data)
        tq_score = result.trade_quality.composite_score

        if tq_score < 45:
            result.decision = "REJECT"
            result.priority = "REJECT"
            result.rejection_reasons.append(f"TQ={tq_score:.1f} below absolute minimum")
            result.processing_time_ms = (time.time() - start) * 1000
            self._history.append(result)
            return result

        # ═══════════════════════════════════════════════════════
        # STAGE 2: Institution Agreement
        # ═══════════════════════════════════════════════════════
        result.institution_agreement = self.institution.evaluate(signal)
        inst_agreement = result.institution_agreement.agreement_ratio

        if not result.institution_agreement.approved:
            result.decision = "REJECT"
            result.priority = "REJECT"
            result.rejection_reasons.append(
                f"institution_rejected: {result.institution_agreement.rejection_reason}"
            )
            result.processing_time_ms = (time.time() - start) * 1000
            self._audit_decision(signal, result)
            self._history.append(result)
            return result

        # ═══════════════════════════════════════════════════════
        # STAGE 3: Expectancy Engine (NEW)
        # ═══════════════════════════════════════════════════════
        result.expectancy = self.expectancy.evaluate(signal)
        ev_r = result.expectancy.expected_value_r

        if not result.expectancy.is_positive_ev:
            result.decision = "REJECT"
            result.priority = "REJECT"
            result.rejection_reasons.append(
                f"negative_ev: EV={ev_r:.3f}R < {0.5} minimum"
            )
            result.processing_time_ms = (time.time() - start) * 1000
            self._audit_decision(signal, result)
            self._history.append(result)
            return result

        # ═══════════════════════════════════════════════════════
        # STAGE 4: Regime Filter
        # ═══════════════════════════════════════════════════════
        result.regime = self.regime.evaluate(signal, tq_score)
        regime_approved = result.regime.approved

        if not regime_approved:
            result.rejection_reasons.append(
                f"regime_blocked: {result.regime.reason}"
            )
            # Don't reject yet — check if other factors compensate

        # ═══════════════════════════════════════════════════════
        # STAGE 4: Reward Filter
        # ═══════════════════════════════════════════════════════
        result.reward = self.reward.evaluate(signal)
        reward_approved = result.reward.approved

        if not reward_approved:
            result.decision = "REJECT"
            result.priority = "REJECT"
            result.rejection_reasons.append(
                f"reward_rejected: {result.reward.reason}"
            )
            result.processing_time_ms = (time.time() - start) * 1000
            self._history.append(result)
            return result

        # ═══════════════════════════════════════════════════════
        # STAGE 5: Portfolio Manager
        # ═══════════════════════════════════════════════════════
        result.portfolio = self.portfolio.check_trade(
            signal, positions, balance, daily_pnl,
        )
        portfolio_approved = result.portfolio.approved

        if not portfolio_approved:
            result.decision = "REJECT"
            result.priority = "REJECT"
            result.rejection_reasons.append(
                f"portfolio_rejected: {result.portfolio.reason}"
            )
            result.processing_time_ms = (time.time() - start) * 1000
            self._audit_decision(signal, result)
            self._history.append(result)
            return result

        # ═══════════════════════════════════════════════════════
        # STAGE 6: Correlation Engine (NEW)
        # ═══════════════════════════════════════════════════════
        self.correlation.set_open_positions(positions)
        result.correlation = self.correlation.check_trade(signal, balance)

        if not result.correlation.approved:
            result.decision = "REJECT"
            result.priority = "REJECT"
            result.rejection_reasons.append(
                f"correlation_rejected: {result.correlation.reason}"
            )
            result.processing_time_ms = (time.time() - start) * 1000
            self._audit_decision(signal, result)
            self._history.append(result)
            return result

        # ═══════════════════════════════════════════════════════
        # STAGE 7: Adaptive Risk Governor (with learning data)
        # ═══════════════════════════════════════════════════════
        # Inject learning data for adaptive risk decisions
        symbol_pf = self.learning.get_symbol_pf(signal.get("symbol", ""))
        session_name = signal.get("session", signal.get("at_open_session", "unknown"))
        session_pf = self.learning.get_session_pf(session_name)
        strategy_pf = self.learning.get_strategy_pf()
        symbol_adj = self.learning.get_symbol_adjustment(signal.get("symbol", ""))
        session_adj = self.learning.get_session_adjustment(session_name)

        self.adaptive_risk.set_learning_data(
            symbol_pf=symbol_pf,
            session_pf=session_pf,
            strategy_pf=strategy_pf,
            symbol_adj=symbol_adj,
            session_adj=session_adj,
        )

        result.risk_decision = self.adaptive_risk.evaluate(balance)
        result.adaptive_risk_score = result.risk_decision.risk_multiplier

        if not result.risk_decision.approved:
            result.decision = "REJECT"
            result.priority = "REJECT"
            result.rejection_reasons.append(
                f"risk_governor_rejected: {result.risk_decision.reason}"
            )
            result.processing_time_ms = (time.time() - start) * 1000
            self._audit_decision(signal, result)
            self._history.append(result)
            return result

        # ═══════════════════════════════════════════════════════
        # STAGE 8: Position Sizing (with correlation + risk adjustments)
        # ═══════════════════════════════════════════════════════

        # ── NEW: Calculate Expected Profit Score (Problem 8) ──
        result.expected_profit_score = calculate_expected_profit_score(signal)

        result.sizing = self.sizing.calculate_size(
            signal, tq_score,
            expected_profit_score=result.expected_profit_score,
        )

        # Apply correlation and risk adjustments
        if result.sizing.approved:
            adjusted_multiplier = (
                result.sizing.final_multiplier
                * result.correlation.correlation_reduction
                * result.risk_decision.risk_multiplier
            )
            result.sizing.final_multiplier = adjusted_multiplier
            result.sizing.quantity *= adjusted_multiplier
            result.sizing.position_value *= adjusted_multiplier
            result.sizing.risk_amount *= adjusted_multiplier

        sizing_approved = result.sizing.approved

        if not sizing_approved:
            result.decision = "REJECT"
            result.priority = "REJECT"
            result.rejection_reasons.append(
                f"sizing_rejected: {result.sizing.rejection_reason}"
            )
            result.processing_time_ms = (time.time() - start) * 1000
            self._audit_decision(signal, result)
            self._history.append(result)
            return result

        # ═══════════════════════════════════════════════════════
        # STAGE 8.5: Adaptive Take-Profit (Problem 6 — NEW)
        # ═══════════════════════════════════════════════════════
        regime_name = signal.get("regime", signal.get("market_regime", "unknown"))
        current_atr = signal.get("atr", 0)
        confidence = signal.get("confidence", 85)
        entry_price_calc = signal.get("entry_price", signal.get("entry", 0))
        stop_loss_calc = signal.get("stop_loss", 0)

        if entry_price_calc > 0 and stop_loss_calc > 0:
            result.adaptive_tp = self.lifecycle.calculate_adaptive_tp(
                entry_price=entry_price_calc,
                stop_loss=stop_loss_calc,
                side=result.side,
                regime=regime_name,
                current_atr=current_atr,
                confidence=confidence,
            )
            # Use adaptive TP levels
            result.take_profit_1 = result.adaptive_tp.get("take_profit_1", 0)
            result.take_profit_2 = result.adaptive_tp.get("take_profit_2", 0)
            result.take_profit_3 = result.adaptive_tp.get("take_profit_3", 0)
        else:
            # Fallback to signal TP
            result.take_profit_1 = signal.get("take_profit_1", signal.get("take_profit", 0))
            result.take_profit_2 = signal.get("take_profit_2", 0)
            result.take_profit_3 = signal.get("take_profit_3", 0)

        # ═══════════════════════════════════════════════════════
        # STAGE 9: Execution Quality Filter (NEW)
        # ═══════════════════════════════════════════════════════
        result.execution_quality = self.execution_quality.evaluate(signal, market_data)

        if not result.execution_quality.approved:
            result.decision = "REJECT"
            result.priority = "REJECT"
            result.rejection_reasons.append(
                f"execution_quality_rejected: {result.execution_quality.rejection_reason}"
            )
            result.processing_time_ms = (time.time() - start) * 1000
            self._audit_decision(signal, result)
            self._history.append(result)
            return result

        # ═══════════════════════════════════════════════════════
        # STAGE 9.5: Execution Eligibility (v2 — Elite Filter)
        # ═══════════════════════════════════════════════════════
        result.eligibility = self.eligibility.evaluate(
            signal, market_data,
            symbol_pf=symbol_pf,
            session_pf=session_pf,
            strategy_pf=strategy_pf,
        )

        if not result.eligibility.eligible:
            result.decision = "REJECT"
            result.priority = "REJECT"
            result.rejection_reasons.append(
                f"eligibility_rejected: score={result.eligibility.execution_score:.1f} "
                f"< 90 ({result.eligibility.rejection_reason})"
            )
            result.processing_time_ms = (time.time() - start) * 1000
            self._audit_decision(signal, result)
            self._history.append(result)
            return result

        # ═══════════════════════════════════════════════════════
        # STAGE 9.6: Portfolio Admission (top-percentile filter)
        # Only admit signals in the top 20% of historical quality
        # ═══════════════════════════════════════════════════════
        admission = self.admission.evaluate(
            signal,
            trade_quality_score=tq_score,
            execution_score=result.eligibility.execution_score,
            expected_profit_score=result.expected_profit_score,
            institution_agreement=inst_agreement,
            risk_reward=result.reward.rr if result.reward else 0,
        )

        if not admission.admitted:
            result.decision = "REJECT"
            result.priority = "REJECT"
            result.rejection_reasons.append(
                f"admission_rejected: {admission.rejection_reason}"
            )
            result.processing_time_ms = (time.time() - start) * 1000
            self._audit_decision(signal, result)
            self._history.append(result)
            return result

        # ═══════════════════════════════════════════════════════
        # STAGE 10: Priority Classification
        # ═══════════════════════════════════════════════════════
        result.priority_result = self.priority.classify(
            signal,
            tq_score=tq_score,
            inst_agreement=inst_agreement,
            regime_approved=regime_approved,
            reward_approved=reward_approved,
            portfolio_approved=portfolio_approved,
            sizing_approved=sizing_approved,
        )

        result.priority = result.priority_result.priority

        # ═══════════════════════════════════════════════════════
        # FINAL DECISION
        # ═══════════════════════════════════════════════════════
        if result.priority_result.executable:
            result.decision = "EXECUTE"
            result.entry_price = entry_price_calc
            result.stop_loss = stop_loss_calc
            result.take_profit = result.take_profit_1 or signal.get("take_profit", 0)
            result.quantity = result.sizing.quantity
            result.position_value = result.sizing.position_value
            result.risk_amount = result.sizing.risk_amount
            result.expected_r = result.reward.rr

            # ── Register with lifecycle manager (Problem 11) ──
            self.lifecycle.register_trade(
                symbol=result.symbol,
                side=result.side,
                entry_price=result.entry_price,
                stop_loss=result.stop_loss,
                take_profit=result.take_profit,
                quantity=result.quantity,
                regime=regime_name,
            )

            # ── Register with continuous trade monitor ──
            self.trade_monitor.register_position(
                symbol=result.symbol,
                side=result.side,
                entry_price=result.entry_price,
                stop_loss=result.stop_loss,
                quantity=result.quantity,
                initial_confidence=signal.get("confidence", 50),
            )

            # ── Log acceptance for attribution ──
            self.evidence.log_decision(
                signal, "ACCEPT",
                reason=f"score={tqi_result.tqi:.3f} ev=${ev_result.get('ev_usd', 0):.2f}",
                ev_data=ev_result,
                tqi_data=tqi_result.to_dict(),
                modules_checked={
                    "governance": "PASS",
                    "tqi": "PASS",
                    "ev_engine": "PASS",
                    "trade_quality": "PASS",
                    "institution_agreement": "PASS",
                    "expectancy": "PASS",
                    "regime_filter": "PASS" if regime_approved else "WARN",
                    "reward_filter": "PASS",
                    "portfolio": "PASS",
                    "correlation": "PASS",
                    "adaptive_risk": "PASS",
                    "execution_quality": "PASS",
                    "eligibility": "PASS",
                    "admission": "PASS",
                },
            )
        else:
            result.decision = "MONITOR" if result.priority in ("MEDIUM", "LOW") else "REJECT"
            if result.decision == "REJECT" and not result.rejection_reasons:
                result.rejection_reasons.append(f"priority={result.priority} not executable")
            # Log rejection for attribution
            self.evidence.log_decision(
                signal, "REJECT",
                reason=f"priority={result.priority}",
                ev_data=ev_result,
                tqi_data=tqi_result.to_dict(),
                modules_checked={
                    "governance": "PASS",
                    "tqi": "PASS",
                    "ev_engine": "PASS",
                    "trade_quality": "PASS" if tq_score >= 45 else "REJECT",
                    "institution_agreement": "PASS" if inst_agreement else "REJECT",
                    "expectancy": "PASS" if result.expectancy and result.expectancy.is_positive_ev else "REJECT",
                    "eligibility": "PASS" if result.eligibility and result.eligibility.eligible else "REJECT",
                    "admission": "PASS" if admission and admission.admitted else "REJECT",
                    "priority": "REJECT",
                },
            )

        result.processing_time_ms = (time.time() - start) * 1000

        # ═══════════════════════════════════════════════════════
        # STAGE 11: Decision Audit (NEW)
        # ═══════════════════════════════════════════════════════
        self._audit_decision(signal, result)

        logger.info(
            "PIPELINE: {} {} → {} [{}] "
            "(TQ={:.1f} EP={:.1f} elig={:.1f} EV={:.3f}R inst={:.0%} regime={} reward={} "
            "corr={:.0%} risk={}×{} exec_q={:.1f}) "
            "({:.1f}ms)",
            result.symbol, result.side, result.decision, result.priority,
            tq_score, result.expected_profit_score,
            result.eligibility.execution_score if result.eligibility else 0,
            ev_r, inst_agreement,
            "✓" if regime_approved else "✗",
            "✓" if reward_approved else "✗",
            result.correlation.correlation_reduction,
            result.risk_decision.state, result.risk_decision.risk_multiplier,
            result.execution_quality.quality_score,
            result.processing_time_ms,
        )

        self._history.append(result)
        return result

    def process_bridge_signals(
        self,
        bridge_signals: List[Dict],
        market_data: Optional[Dict] = None,
        open_positions: Optional[List[Dict]] = None,
        balance: float = 10_000.0,
        daily_pnl: float = 0.0,
    ) -> List[PipelineResult]:
        """
        Process a list of bridge signals through the pipeline with enrichment.

        This method first enriches bridge signals with full DB data, then
        processes each through the pipeline.

        Args:
            bridge_signals: List of signals from bridge (signals.json)
            market_data: Optional market data dict
            open_positions: Current open positions
            balance: Account balance
            daily_pnl: Today's realized PnL

        Returns:
            List of PipelineResult for each signal
        """
        # Enrich signals with full institutional data
        enriched = self.enricher.enrich_signals(bridge_signals)

        results = []
        for sig in enriched:
            result = self.process_signal(sig, market_data, open_positions, balance, daily_pnl)
            results.append(result)

        return results

    def get_history(self) -> List[PipelineResult]:
        """Get processing history."""
        return list(self._history)

    def get_stats(self) -> Dict:
        """Get pipeline statistics."""
        total = len(self._history)
        if total == 0:
            return {"total": 0}

        decisions = {}
        priorities = {}
        for r in self._history:
            decisions[r.decision] = decisions.get(r.decision, 0) + 1
            priorities[r.priority] = priorities.get(r.priority, 0) + 1

        avg_tq = sum(
            r.trade_quality.composite_score for r in self._history
            if r.trade_quality
        ) / max(1, sum(1 for r in self._history if r.trade_quality))

        return {
            "total": total,
            "decisions": decisions,
            "priorities": priorities,
            "avg_tq_score": round(avg_tq, 2),
            "execute_rate": round(
                decisions.get("EXECUTE", 0) / total * 100, 1
            ),
        }

    def _audit_decision(self, signal: Dict, result: PipelineResult) -> None:
        """Log decision to audit trail."""
        try:
            self.audit.log_decision(
                signal=signal,
                decision=result.decision,
                priority=result.priority,
                trade_quality_score=result.trade_quality.composite_score if result.trade_quality else 0,
                expected_value_r=result.expectancy.expected_value_r if result.expectancy else 0,
                institution_agreement=result.institution_agreement.agreement_ratio if result.institution_agreement else 0,
                regime_approved=result.regime.approved if result.regime else False,
                reward_approved=result.reward.approved if result.reward else False,
                portfolio_approved=result.portfolio.approved if result.portfolio else False,
                sizing_approved=result.sizing.approved if result.sizing else False,
                execution_quality=result.execution_quality.quality_score if result.execution_quality else 0,
                correlation_reduction=result.correlation.correlation_reduction if result.correlation else 1.0,
                risk_state=result.risk_decision.state if result.risk_decision else "NORMAL",
                risk_multiplier=result.risk_decision.risk_multiplier if result.risk_decision else 1.0,
                rejection_reasons=result.rejection_reasons,
            )
        except Exception as e:
            logger.warning("Audit log error: {}", e)

    def reset(self) -> None:
        """Reset pipeline history."""
        self._history.clear()

    # ─────────────────────────────────────────────────────────
    # Trade Governance — Stale Trade Exits & Status
    # ─────────────────────────────────────────────────────────

    def check_stale_trades(
        self, open_positions: List[Dict],
    ) -> List[StaleTradeExit]:
        """
        Check open positions for time-based exit (governance feature 6).

        Args:
            open_positions: Current open positions

        Returns:
            List of StaleTradeExit decisions
        """
        return self.governance.check_stale_trades(open_positions)

    def get_governance_status(self) -> Dict:
        """Get complete governance status for dashboard display."""
        return self.governance.get_full_status()

    def get_kill_switch_status(self) -> Dict:
        """Get kill switch status."""
        return self.governance.get_kill_switch_status().to_dict()

    def get_symbol_blacklist_status(self) -> Dict:
        """Get symbol blacklist status."""
        return {
            k: v.to_dict()
            for k, v in self.governance.get_symbol_blacklist().items()
        }

    def get_session_blacklist_status(self) -> Dict:
        """Get session blacklist status."""
        return {
            k: v.to_dict()
            for k, v in self.governance.get_session_blacklist().items()
        }

    def get_confidence_calibration_status(self) -> Dict:
        """Get confidence calibration status."""
        return {
            k: v.to_dict()
            for k, v in self.governance.get_confidence_buckets().items()
        }

    def get_admission_status(self) -> Dict:
        """Get portfolio admission engine status."""
        return self.admission.get_status()

    def get_full_governance_report(self) -> Dict:
        """Get complete governance + admission + monitor + evidence report."""
        return {
            "governance": self.get_governance_status(),
            "admission": self.get_admission_status(),
            "trade_monitor": self.get_trade_monitor_status(),
            "evidence": self.get_evidence_status(),
        }

    # ─────────────────────────────────────────────────────────
    # Evidence Engine — TQI & Self-Learning
    # ─────────────────────────────────────────────────────────

    def query_tqi(
        self, signal: Dict[str, Any], market_data: Optional[Dict] = None,
    ) -> TQIResult:
        """Query Trade Quality Index for a signal."""
        return self.evidence.query_tqi(signal, market_data)

    def record_trade_outcome(self, **kwargs) -> Dict:
        """
        Record a completed trade for self-learning.

        Call this immediately after every trade closes.
        All kwargs are passed to EvidenceBasedDecisionEngine.record_trade_outcome().
        """
        update = self.evidence.record_trade_outcome(**kwargs)

        # Link outcome to decision log for attribution
        symbol = kwargs.get("symbol", "")
        side = kwargs.get("side", "")
        opened_at = kwargs.get("opened_at", 0)
        pnl = kwargs.get("pnl", 0)
        realized_r = kwargs.get("realized_r", 0)
        exit_reason = kwargs.get("exit_reason", "")
        if symbol and side:
            self.evidence.link_outcome(
                symbol, side, opened_at, pnl, realized_r, exit_reason,
            )

        # Also unregister from trade monitor
        if symbol:
            self.trade_monitor.unregister_position(symbol)

        # Also record in admission engine
        self.admission.record_outcome(pnl)

        return update.to_dict()

    def get_evidence_status(self) -> Dict:
        """Get evidence engine status."""
        return self.evidence.get_status()

    def get_adaptive_threshold(self, metric_name: str) -> Dict:
        """Get adaptive threshold for a metric."""
        return self.evidence.get_adaptive_threshold(metric_name).to_dict()

    def compute_ev(
        self, signal: Dict[str, Any], market_data: Optional[Dict] = None,
        balance: float = 10_000.0,
    ) -> Dict:
        """Compute Expected Value in dollars for a signal."""
        return self.evidence.compute_ev(signal, market_data, balance)

    def get_validation_report(self) -> Dict:
        """
        Get acceptance/rejection validation report.

        Answers: "Does the Evidence Engine actually reject losers?"
        """
        return self.evidence.get_validation_report()

    def get_attribution_report(self) -> Dict:
        """
        Get per-module attribution report.

        Shows which modules improve Profit Factor.
        """
        return self.evidence.get_attribution_report()

    def get_acceptance_efficiency(self) -> Dict:
        """
        Get Acceptance Efficiency metric.

        AE = PF of accepted trades / PF of all eligible trades
        If > 1.0, the App is adding value.
        """
        return self.evidence.get_acceptance_efficiency()

    def record_shadow_trade(
        self, symbol: str, side: str, entry_price: float,
        opened_at: float, pnl: float, realized_r: float,
        rejection_reason: str = "",
    ) -> None:
        """Record what happened to a rejected trade (shadow/paper)."""
        self.evidence.record_shadow_trade(
            symbol, side, entry_price, opened_at, pnl, realized_r, rejection_reason,
        )

    def get_false_rejection_report(self) -> Dict:
        """
        Get False Rejection Rate report.

        Good rejections = rejected trades that would have lost
        Bad rejections = rejected trades that would have won
        """
        return self.evidence.get_false_rejection_report()

    def log_version_snapshot(self, version: str, description: str = "") -> None:
        """Record a version snapshot for longitudinal comparison."""
        self.evidence.log_version_snapshot(version, description)

    def get_version_history(self) -> List[Dict]:
        """Get all version snapshots."""
        return self.evidence.get_version_history()

    def get_contribution_dashboard(self) -> Dict:
        """
        Get per-module contribution dashboard.

        Shows PF improvement, drawdown reduction, and net effect
        for each App module.
        """
        return self.evidence.get_contribution_dashboard()

    def get_decision_confusion_matrix(self) -> Dict:
        """
        Get decision confusion matrix with FAR/FRR.

        Classifies every decision as:
            Correct Acceptance / False Acceptance / Correct Rejection / False Rejection
        """
        return self.evidence.get_decision_confusion_matrix()

    def get_dollar_weighted_confusion_matrix(self) -> Dict:
        """
        Get confusion matrix weighted by dollar impact.

        Shows which errors cost the most money.
        """
        return self.evidence.get_dollar_weighted_confusion_matrix()

    def get_parameter_sensitivity(self) -> Dict:
        """
        Get parameter sensitivity analysis.

        Shows how threshold changes would have affected historical PF.
        """
        return self.evidence.get_parameter_sensitivity()

    def get_parameter_stability(self) -> Dict:
        """
        Get parameter stability scores.

        Grades each parameter A/B/C/D based on sensitivity to perturbation.
        Only deploy parameters rated A or B.
        """
        return self.evidence.get_parameter_stability()

    def optimize_parameter_bundles(
        self, current_bundle: Optional[Dict[str, float]] = None,
    ) -> Dict:
        """
        Optimize parameter bundles (combinations, not individual).

        Tests EV × TQI × Confidence combinations and ranks by
        PF × regime stability × recovery factor.
        """
        return self.evidence.optimize_parameter_bundles(current_bundle)

    def log_bundle_deployment(
        self, bundle: Dict[str, float], reason: str = "",
    ) -> None:
        """Log a parameter bundle deployment for drift tracking."""
        self.evidence.log_bundle_deployment(bundle, reason)

    def get_parameter_drift(self) -> Dict:
        """Get parameter drift analysis."""
        return self.evidence.get_parameter_drift()

    def check_rollback_conditions(
        self, current_bundle: Dict[str, float],
        baseline_pf: float = 1.0, max_drawdown_pct: float = 10.0,
    ) -> Dict:
        """
        Check if current bundle should be rolled back.

        Triggers rollback if 2+ of: PF below baseline, negative expectancy,
        drawdown exceeded, or degradation across regimes.
        """
        return self.evidence.check_rollback_conditions(
            current_bundle, baseline_pf, max_drawdown_pct,
        )

    def compute_bundle_score(self, bundle_metrics: Dict) -> float:
        """
        Compute multi-objective optimization score.

        Score = 0.35×PF + 0.25×Expectancy + 0.15×Recovery + 0.15×DD Stability + 0.10×Trade Stability
        """
        return self.evidence.compute_bundle_score(bundle_metrics)

    def setup_champion_challenger(
        self, champion: Dict, challenger: Dict, evaluation_window: int = 100,
    ) -> Dict:
        """Set up champion-challenger comparison."""
        return self.evidence.setup_champion_challenger(champion, challenger, evaluation_window)

    def record_champion_challenger_outcome(self, bundle_type: str, pnl: float) -> None:
        """Record outcome for champion or challenger."""
        self.evidence.record_champion_challenger_outcome(bundle_type, pnl)

    def evaluate_champion_challenger(self) -> Dict:
        """Evaluate champion vs challenger."""
        return self.evidence.evaluate_champion_challenger()

    def log_parameter_change(
        self, bundle: Dict, action: str = "DEPLOYED", reason: str = "",
        pf: float = 0.0, expectancy: float = 0.0,
    ) -> None:
        """Log a parameter change for permanent history."""
        self.evidence.log_parameter_change(bundle, action, reason, pf, expectancy)

    def get_parameter_history(self) -> List[Dict]:
        """Get complete parameter change history."""
        return self.evidence.get_parameter_history()

    # ─────────────────────────────────────────────────────────
    # Continuous Trade Monitoring — Post-Entry Re-evaluation
    # ─────────────────────────────────────────────────────────

    def monitor_open_position(
        self,
        symbol: str,
        current_price: float,
        signal: Dict[str, Any],
    ) -> MonitorDecision:
        """
        Re-evaluate an open position using live market data.

        Call every refresh cycle (5-10s) for each open position.
        Returns HOLD / REDUCE / EXIT decision.

        Args:
            symbol: Position symbol
            current_price: Current market price
            signal: Live market data (flow, OI, CVD, volume, etc.)

        Returns:
            MonitorDecision with action to take
        """
        return self.trade_monitor.evaluate(symbol, current_price, signal)

    def monitor_all_positions(
        self,
        open_positions: List[Dict],
        market_data: Dict[str, Any],
    ) -> List[MonitorDecision]:
        """
        Re-evaluate all open positions.

        Args:
            open_positions: List of open position dicts
            market_data: Dict of symbol → live market data

        Returns:
            List of MonitorDecision for each position
        """
        decisions = []
        for pos in open_positions:
            symbol = pos.get("symbol", "")
            current_price = pos.get("current_price", pos.get("price", 0))
            sig_data = market_data.get(symbol, {})
            if not sig_data:
                continue
            decision = self.trade_monitor.evaluate(symbol, current_price, sig_data)
            decisions.append(decision)
        return decisions

    def unregister_position(self, symbol: str) -> None:
        """Remove a position from continuous monitoring."""
        self.trade_monitor.unregister_position(symbol)

    def get_trade_monitor_status(self) -> Dict:
        """Get continuous trade monitor status."""
        return self.trade_monitor.get_status()
