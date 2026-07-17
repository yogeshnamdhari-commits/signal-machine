"""
Automated Reporter — Daily/Weekly/Monthly Report Generation
============================================================
Generates: PnL, Profit Factor, Win Rate, Drawdown, Risk,
           Exposure, Capital Efficiency reports
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data" / "capital"
REPORTS_DIR = Path(__file__).parent.parent / "data" / "reports"


# ─── Data Classes ────────────────────────────────────────────────────────────
@dataclass
class TradeSummary:
    """Summary of trades in a period."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    gross_profit: float
    gross_loss: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    avg_holding_time_hours: float
    total_volume: float
    total_fees: float


@dataclass
class RiskSummary:
    """Risk metrics for a period."""
    max_drawdown: float
    avg_drawdown: float
    max_consecutive_losses: int
    risk_per_trade_avg: float
    max_leverage_used: float
    avg_leverage_used: float
    max_position_size_pct: float
    risk_breaches: int
    kill_switch_activations: int


@dataclass
class CapitalSummary:
    """Capital efficiency metrics."""
    starting_equity: float
    ending_equity: float
    equity_change_pct: float
    peak_equity: float
    trough_equity: float
    avg_margin_usage: float
    capital_efficiency: float  # PnL / Avg Capital Used
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float


@dataclass
class ExposureSummary:
    """Exposure metrics."""
    avg_long_exposure: float
    avg_short_exposure: float
    avg_net_exposure: float
    avg_gross_exposure: float
    max_single_position_pct: float
    max_sector_exposure_pct: float
    avg_positions: float
    most_traded_symbols: list


@dataclass
class PeriodReport:
    """Complete period report."""
    report_type: str
    period_start: str
    period_end: str
    generated_at: str
    trades: dict
    risk: dict
    capital: dict
    exposure: dict
    slippage: dict
    performance_drift: dict
    alerts: list
    recommendations: list


# ─── Automated Reporter ──────────────────────────────────────────────────────
class AutomatedReporter:
    """
    Generates automated trading reports.

    Usage:
        reporter = AutomatedReporter()
        reporter.ingest_trades(trades_list)
        daily = reporter.generate_daily()
        weekly = reporter.generate_weekly()
        monthly = reporter.generate_monthly()
    """

    def __init__(self):
        self._trades: list[dict] = []
        self._daily_reports: list[dict] = []
        self._weekly_reports: list[dict] = []
        self._monthly_reports: list[dict] = []
        self._equity_snapshots: list[dict] = []
        self._risk_events: list[dict] = []
        self._load_state()
        logger.info("AutomatedReporter initialized: %d trades ingested", len(self._trades))

    # ── Data Ingestion ────────────────────────────────────────────────────────
    def ingest_trade(self, trade: dict):
        """Ingest a single trade record."""
        trade.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        self._trades.append(trade)

    def ingest_trades(self, trades: list[dict]):
        """Ingest multiple trades."""
        for t in trades:
            self.ingest_trade(t)

    def ingest_equity_snapshot(self, equity: float, timestamp: Optional[str] = None):
        """Record equity snapshot."""
        self._equity_snapshots.append({
            "equity": equity,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        })

    def ingest_risk_event(self, event: dict):
        """Record a risk event."""
        event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        self._risk_events.append(event)

    # ── Daily Report ──────────────────────────────────────────────────────────
    def generate_daily(self, date: Optional[str] = None) -> PeriodReport:
        """Generate daily report."""
        now = datetime.now(timezone.utc)
        if date:
            target = datetime.fromisoformat(date)
        else:
            target = now

        start = target.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)

        report = self._generate_report("daily", start, end)
        self._daily_reports.append(asdict(report))
        self._save_state()
        self._save_report(report, "daily")
        return report

    # ── Weekly Report ─────────────────────────────────────────────────────────
    def generate_weekly(self) -> PeriodReport:
        """Generate weekly report (last 7 days)."""
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=7)

        report = self._generate_report("weekly", start, now)
        self._weekly_reports.append(asdict(report))
        self._save_state()
        self._save_report(report, "weekly")
        return report

    # ── Monthly Report ────────────────────────────────────────────────────────
    def generate_monthly(self) -> PeriodReport:
        """Generate monthly report (last 30 days)."""
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=30)

        report = self._generate_report("monthly", start, now)
        self._monthly_reports.append(asdict(report))
        self._save_state()
        self._save_report(report, "monthly")
        return report

    # ── Report Generation ─────────────────────────────────────────────────────
    def _generate_report(
        self, report_type: str, start: datetime, end: datetime
    ) -> PeriodReport:
        """Generate a report for the given period."""
        # Filter trades in period
        period_trades = self._filter_trades(start, end)

        # Trade summary
        trade_summary = self._calc_trade_summary(period_trades)

        # Risk summary
        risk_summary = self._calc_risk_summary(period_trades)

        # Capital summary
        capital_summary = self._calc_capital_summary(period_trades)

        # Exposure summary
        exposure_summary = self._calc_exposure_summary(period_trades)

        # Slippage summary
        slippage_summary = self._calc_slippage_summary(period_trades)

        # Alerts
        alerts = self._generate_alerts(trade_summary, risk_summary)

        # Recommendations
        recs = self._generate_recommendations(trade_summary, risk_summary, capital_summary)

        return PeriodReport(
            report_type=report_type,
            period_start=start.isoformat(),
            period_end=end.isoformat(),
            generated_at=datetime.now(timezone.utc).isoformat(),
            trades=asdict(trade_summary),
            risk=asdict(risk_summary),
            capital=asdict(capital_summary),
            exposure=asdict(exposure_summary),
            slippage=slippage_summary,
            performance_drift={},
            alerts=alerts,
            recommendations=recs,
        )

    # ── Trade Calculations ────────────────────────────────────────────────────
    def _calc_trade_summary(self, trades: list[dict]) -> TradeSummary:
        """Calculate trade summary."""
        if not trades:
            return TradeSummary(
                total_trades=0, winning_trades=0, losing_trades=0, win_rate=0,
                total_pnl=0, gross_profit=0, gross_loss=0, profit_factor=0,
                avg_win=0, avg_loss=0, largest_win=0, largest_loss=0,
                avg_holding_time_hours=0, total_volume=0, total_fees=0,
            )

        pnls = [t.get("pnl", 0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        volumes = [t.get("volume", t.get("position_value", 0)) for t in trades]
        fees = [t.get("fees", 0) for t in trades]
        holding_times = [t.get("holding_time_hours", 0) for t in trades]

        return TradeSummary(
            total_trades=len(trades),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=len(wins) / len(trades) if trades else 0,
            total_pnl=round(sum(pnls), 2),
            gross_profit=round(gross_profit, 2),
            gross_loss=round(gross_loss, 2),
            profit_factor=round(pf, 2),
            avg_win=round(gross_profit / len(wins), 2) if wins else 0,
            avg_loss=round(gross_loss / len(losses), 2) if losses else 0,
            largest_win=round(max(wins), 2) if wins else 0,
            largest_loss=round(min(losses), 2) if losses else 0,
            avg_holding_time_hours=round(
                sum(holding_times) / len(holding_times), 1
            ) if holding_times else 0,
            total_volume=round(sum(volumes), 2),
            total_fees=round(sum(fees), 2),
        )

    def _calc_risk_summary(self, trades: list[dict]) -> RiskSummary:
        """Calculate risk summary."""
        drawdowns = [t.get("drawdown", 0) for t in trades]
        consecutive_losses = self._max_consecutive_losses(trades)
        leverages = [t.get("leverage", 1) for t in trades]
        risk_pcts = [t.get("risk_pct", 0) for t in trades]
        pos_sizes = [t.get("position_size_pct", 0) for t in trades]

        return RiskSummary(
            max_drawdown=max(drawdowns) if drawdowns else 0,
            avg_drawdown=sum(drawdowns) / len(drawdowns) if drawdowns else 0,
            max_consecutive_losses=consecutive_losses,
            risk_per_trade_avg=sum(risk_pcts) / len(risk_pcts) if risk_pcts else 0,
            max_leverage_used=max(leverages) if leverages else 0,
            avg_leverage_used=sum(leverages) / len(leverages) if leverages else 0,
            max_position_size_pct=max(pos_sizes) if pos_sizes else 0,
            risk_breaches=sum(1 for t in trades if t.get("risk_breach", False)),
            kill_switch_activations=sum(1 for t in trades if t.get("kill_switch", False)),
        )

    def _calc_capital_summary(self, trades: list[dict]) -> CapitalSummary:
        """Calculate capital efficiency."""
        pnls = [t.get("pnl", 0) for t in trades]
        equities = [s["equity"] for s in self._equity_snapshots]

        starting = equities[0] if equities else 10000
        ending = equities[-1] if equities else starting
        peak = max(equities) if equities else starting
        trough = min(equities) if equities else starting

        # Sharpe calculation
        if pnls and len(pnls) > 1:
            avg_pnl = sum(pnls) / len(pnls)
            var = sum((p - avg_pnl) ** 2 for p in pnls) / (len(pnls) - 1)
            std = var ** 0.5
            sharpe = (avg_pnl / std) * (365 ** 0.5) if std > 0 else 0
        else:
            sharpe = 0

        return CapitalSummary(
            starting_equity=starting,
            ending_equity=ending,
            equity_change_pct=round((ending - starting) / starting * 100, 2) if starting > 0 else 0,
            peak_equity=peak,
            trough_equity=trough,
            avg_margin_usage=0,
            capital_efficiency=round(sum(pnls) / starting * 100, 2) if starting > 0 else 0,
            sharpe_ratio=round(sharpe, 2),
            sortino_ratio=0,
            calmar_ratio=0,
        )

    def _calc_exposure_summary(self, trades: list[dict]) -> ExposureSummary:
        """Calculate exposure summary."""
        longs = [t for t in trades if t.get("side") == "LONG"]
        shorts = [t for t in trades if t.get("side") == "SHORT"]

        # Symbol frequency
        symbol_counts = {}
        for t in trades:
            sym = t.get("symbol", "UNKNOWN")
            symbol_counts[sym] = symbol_counts.get(sym, 0) + 1
        top_symbols = sorted(symbol_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        return ExposureSummary(
            avg_long_exposure=sum(t.get("position_value", 0) for t in longs) / len(longs) if longs else 0,
            avg_short_exposure=sum(t.get("position_value", 0) for t in shorts) / len(shorts) if shorts else 0,
            avg_net_exposure=0,
            avg_gross_exposure=0,
            max_single_position_pct=max((t.get("position_size_pct", 0) for t in trades), default=0),
            max_sector_exposure_pct=0,
            avg_positions=len(trades) / max(1, len(set(t.get("date", "") for t in trades))),
            most_traded_symbols=[{"symbol": s, "count": c} for s, c in top_symbols],
        )

    def _calc_slippage_summary(self, trades: list[dict]) -> dict:
        """Calculate slippage summary."""
        slippages = [t.get("slippage_bps", 0) for t in trades if "slippage_bps" in t]
        if not slippages:
            return {"avg_bps": 0, "worst_bps": 0, "total_usd": 0, "quality": "N/A"}

        return {
            "avg_bps": round(sum(slippages) / len(slippages), 2),
            "worst_bps": round(max(slippages, key=abs), 2),
            "total_usd": sum(t.get("slippage_usd", 0) for t in trades),
            "quality": "GOOD" if abs(sum(slippages) / len(slippages)) < 15 else "FAIR",
        }

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _filter_trades(self, start: datetime, end: datetime) -> list[dict]:
        """Filter trades within time range."""
        filtered = []
        for t in self._trades:
            try:
                ts = datetime.fromisoformat(t.get("timestamp", ""))
                if start <= ts < end:
                    filtered.append(t)
            except (ValueError, TypeError):
                continue
        return filtered

    @staticmethod
    def _max_consecutive_losses(trades: list[dict]) -> int:
        """Calculate max consecutive losing trades."""
        max_streak = 0
        current = 0
        for t in trades:
            if t.get("pnl", 0) < 0:
                current += 1
                max_streak = max(max_streak, current)
            else:
                current = 0
        return max_streak

    def _generate_alerts(self, trades: TradeSummary, risk: RiskSummary) -> list[dict]:
        """Generate alerts from report data."""
        alerts = []
        if trades.profit_factor < 1.0:
            alerts.append({"severity": "CRITICAL", "message": f"Profit factor below 1.0: {trades.profit_factor}"})
        if trades.win_rate < 0.40:
            alerts.append({"severity": "HIGH", "message": f"Win rate below 40%: {trades.win_rate:.1%}"})
        if risk.max_drawdown > 0.08:
            alerts.append({"severity": "HIGH", "message": f"Max drawdown {risk.max_drawdown:.1%}"})
        if risk.risk_breaches > 0:
            alerts.append({"severity": "CRITICAL", "message": f"{risk.risk_breaches} risk breaches"})
        return alerts

    def _generate_recommendations(
        self, trades: TradeSummary, risk: RiskSummary, capital: CapitalSummary
    ) -> list[str]:
        """Generate recommendations."""
        recs = []
        if trades.profit_factor < 1.3:
            recs.append("PF below 1.3 — review entry criteria")
        if risk.max_consecutive_losses > 5:
            recs.append(f"Max {risk.max_consecutive_losses} consecutive losses — consider position size reduction")
        if not recs:
            recs.append("✅ All metrics within acceptable ranges")
        return recs

    # ── Save Report ───────────────────────────────────────────────────────────
    def _save_report(self, report: PeriodReport, report_type: str):
        """Save report to file."""
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        filename = f"{report_type}_report_{date_str}.json"
        (REPORTS_DIR / filename).write_text(json.dumps(asdict(report), indent=2, default=str))
        logger.info("Saved %s report: %s", report_type, filename)

    # ── State Persistence ─────────────────────────────────────────────────────
    def _save_state(self):
        """Save reporter state."""
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "total_trades": len(self._trades),
            "trades": self._trades[-1000:],
            "equity_snapshots": self._equity_snapshots[-1000:],
            "daily_reports": len(self._daily_reports),
            "weekly_reports": len(self._weekly_reports),
            "monthly_reports": len(self._monthly_reports),
            "updated": datetime.now(timezone.utc).isoformat(),
        }
        (DATA_DIR / "reporter_state.json").write_text(json.dumps(state, indent=2, default=str))

    def _load_state(self):
        """Load persisted state."""
        path = DATA_DIR / "reporter_state.json"
        if not path.exists():
            return
        try:
            state = json.loads(path.read_text())
            self._trades = state.get("trades", [])
            self._equity_snapshots = state.get("equity_snapshots", [])
        except Exception as e:
            logger.error("Failed to load reporter state: %s", e)

    def get_stats(self) -> dict:
        """Get reporter statistics."""
        return {
            "total_trades": len(self._trades),
            "equity_snapshots": len(self._equity_snapshots),
            "daily_reports": len(self._daily_reports),
            "weekly_reports": len(self._weekly_reports),
            "monthly_reports": len(self._monthly_reports),
            "risk_events": len(self._risk_events),
        }
