"""
Dashboard FastAPI Application — Institutional Trading Operations Center.

REST API + WebSocket server for the institutional dashboard.
All endpoints are async and integrate with the service layer.

Endpoints:
- /api/v1/executive — Executive overview
- /api/v1/exchanges — Multi-exchange panel
- /api/v1/portfolio — Portfolio allocation
- /api/v1/positions — Position management
- /api/v1/signals — Signal intelligence
- /api/v1/allocation — Capital allocation
- /api/v1/risk — Risk management
- /api/v1/arbitrage — Arbitrage panel
- /api/v1/execution — Execution monitor
- /api/v1/health — System health
- /api/v1/alerts — Alert center
- /api/v1/analytics — Performance analytics
- /api/v1/reports — Report generation
- /api/v1/auth — Authentication
- /ws/dashboard — WebSocket live stream
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Set

from loguru import logger

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Query
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse, FileResponse
    from pydantic import BaseModel
    _HAS_FASTAPI = True
except ImportError:
    _HAS_FASTAPI = False

import sys
from pathlib import Path

_ai_root = Path(__file__).resolve().parent.parent.parent
if str(_ai_root) not in sys.path:
    sys.path.insert(0, str(_ai_root))

from dashboard.backend.services import (
    PortfolioService, ExchangeService, RiskService,
    ArbitrageService, ExecutionService, SignalService,
    HealthService, AlertService, AlertLevel, AlertCategory,
    AllocationService, AnalyticsService, ReportingEngine,
    market_feed, signal_engine,
)
from dashboard.backend.auth.rbac import RBACManager, Permission


# ── Pydantic Models ─────────────────────────────────────────────

if _HAS_FASTAPI:
    class LoginRequest(BaseModel):
        username: str
        password: str

    class TradeAction(BaseModel):
        position_id: str
        action: str  # close, reduce, move_stop, adjust_target
        params: Optional[Dict[str, Any]] = None

    class AlertAckRequest(BaseModel):
        alert_id: str

    class ReportRequest(BaseModel):
        report_type: str  # daily, weekly, monthly, risk, execution, portfolio, arbitrage
        format: str = "json"  # json, csv, html


# ── App Factory ──────────────────────────────────────────────────

def create_app() -> "FastAPI":
    """Create and configure the FastAPI application."""
    if not _HAS_FASTAPI:
        raise ImportError(
            "FastAPI is required. Install with: pip install fastapi uvicorn"
        )

    app = FastAPI(
        title="DeltaTerminal — Institutional Dashboard",
        description="Production-grade institutional trading operations center",
        version="1.0.0",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Services ─────────────────────────────────────────────────
    portfolio_svc = PortfolioService()
    exchange_svc = ExchangeService()
    risk_svc = RiskService()
    arbitrage_svc = ArbitrageService()
    execution_svc = ExecutionService()
    signal_svc = SignalService()
    health_svc = HealthService()
    alert_svc = AlertService()
    allocation_svc = AllocationService()
    analytics_svc = AnalyticsService()
    reporting_engine = ReportingEngine()
    rbac = RBACManager()

    # ── WebSocket Manager ────────────────────────────────────────
    class ConnectionManager:
        """Manages WebSocket connections for live dashboard updates."""

        def __init__(self) -> None:
            self._connections: Dict[str, WebSocket] = {}
            self._subscriptions: Dict[str, Set[str]] = {}  # conn_id → channels

        async def connect(self, ws: WebSocket, conn_id: str) -> None:
            await ws.accept()
            self._connections[conn_id] = ws
            self._subscriptions[conn_id] = {
                "positions", "orders", "signals", "risk",
                "allocation", "arbitrage", "health", "alerts",
                "live_signal", "signal_alert", "market_ticker", "signal_stats",
            }
            logger.info("[WS] Connected: {}", conn_id)

        def disconnect(self, conn_id: str) -> None:
            self._connections.pop(conn_id, None)
            self._subscriptions.pop(conn_id, None)
            logger.info("[WS] Disconnected: {}", conn_id)

        async def broadcast(self, channel: str, data: Dict[str, Any]) -> None:
            """Broadcast data to all subscribers of a channel."""
            message = json.dumps({
                "channel": channel,
                "data": data,
                "timestamp": time.time(),
            }, default=str)
            disconnected = []
            for conn_id, ws in self._connections.items():
                channels = self._subscriptions.get(conn_id, set())
                if channel in channels:
                    try:
                        await ws.send_text(message)
                    except Exception:
                        disconnected.append(conn_id)
            for cid in disconnected:
                self.disconnect(cid)

        @property
        def connection_count(self) -> int:
            return len(self._connections)

    ws_manager = ConnectionManager()

    # ── Background Tasks ─────────────────────────────────────────

    # Real-time signal callback — push to WS immediately
    async def _on_new_signal(sig: dict) -> None:
        await ws_manager.broadcast("live_signal", {
            "type": "new_signal",
            "signal": sig,
        })

    async def _on_new_alert(alert: dict) -> None:
        await ws_manager.broadcast("signal_alert", {
            "type": "new_alert",
            "alert": alert,
        })

    signal_engine.on_signal(_on_new_signal)
    signal_engine.on_alert(_on_new_alert)

    async def dashboard_broadcast_loop() -> None:
        """Broadcast dashboard data to WebSocket clients."""
        tick_counter = 0
        while True:
            try:
                if ws_manager.connection_count > 0:
                    await ws_manager.broadcast("health", health_svc.get_health_panel())
                    await ws_manager.broadcast("portfolio", portfolio_svc.get_executive_overview())
                    await ws_manager.broadcast("exchanges", exchange_svc.get_exchange_panel())
                    await ws_manager.broadcast("risk", risk_svc.get_risk_panel())
                    await ws_manager.broadcast("signals", signal_engine.get_signal_panel())
                    await ws_manager.broadcast("execution", execution_svc.get_execution_panel())
                    await ws_manager.broadcast("arbitrage", arbitrage_svc.get_arbitrage_panel())
                    await ws_manager.broadcast("allocation", allocation_svc.get_allocation_panel())
                    await ws_manager.broadcast("alerts", alert_svc.get_alert_stats())

                    # High-frequency market data (every 500ms)
                    tick_counter += 1
                    if tick_counter % 1 == 0:  # Every iteration
                        market_ticks = market_feed.get_all_ticks()
                        # Send top 8 symbols as market ticker
                        top_ticks = sorted(market_ticks, key=lambda t: t.get("volume_24h", 0), reverse=True)[:8]
                        await ws_manager.broadcast("market_ticker", {
                            "ticks": top_ticks,
                            "overview": market_feed.get_market_overview(),
                        })

                    # Signal stats every 5 cycles
                    if tick_counter % 5 == 0:
                        await ws_manager.broadcast("signal_stats", signal_engine.get_signal_stats())

            except Exception as e:
                logger.error("[WS] Broadcast error: {}", e)
            await asyncio.sleep(1)

    @app.on_event("startup")
    async def startup() -> None:
        """Initialize services on startup."""
        # Initialize and start market data feed
        await market_feed.initialize()
        await market_feed.start()

        # Initialize signal engine and wire to market data
        await signal_engine.initialize()
        market_feed.on_tick(signal_engine.process_ticks)

        asyncio.create_task(dashboard_broadcast_loop())
        logger.info("Dashboard API started — MarketDataFeed + SignalEngine active")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        """Cleanup on shutdown."""
        await market_feed.stop()
        rbac.save_audit()
        alert_svc.save_alerts()
        logger.info("Dashboard API stopped")

    # ── WebSocket Endpoint ───────────────────────────────────────

    @app.websocket("/ws/dashboard")
    async def websocket_dashboard(ws: WebSocket) -> None:
        """WebSocket endpoint for live dashboard updates."""
        conn_id = f"ws-{uuid.uuid4().hex[:12]}"
        await ws_manager.connect(ws, conn_id)
        try:
            while True:
                data = await ws.receive_text()
                try:
                    msg = json.loads(data)
                    action = msg.get("action", "")

                    if action == "subscribe":
                        channels = msg.get("channels", [])
                        ws_manager._subscriptions[conn_id] = set(channels)
                        await ws.send_text(json.dumps({
                            "status": "subscribed",
                            "channels": channels,
                        }))

                    elif action == "ping":
                        await ws.send_text(json.dumps({
                            "action": "pong",
                            "timestamp": time.time(),
                        }))

                    elif action == "get_snapshot":
                        snapshot = {
                            "portfolio": portfolio_svc.get_executive_overview(),
                            "exchanges": exchange_svc.get_exchange_panel(),
                            "risk": risk_svc.get_risk_panel(),
                            "health": health_svc.get_health_panel(),
                            "timestamp": time.time(),
                        }
                        await ws.send_text(json.dumps(snapshot, default=str))

                except json.JSONDecodeError:
                    pass
        except WebSocketDisconnect:
            ws_manager.disconnect(conn_id)

    # ── Auth Endpoints ───────────────────────────────────────────

    @app.post("/api/v1/auth/login")
    async def login(req: LoginRequest) -> Any:
        """Authenticate user and return session."""
        session = rbac.authenticate(req.username, req.password)
        if not session:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return {
            "session_id": session.session_id,
            "user": session.username,
            "role": session.role.value,
            "permissions": rbac.get_user_permissions(session.session_id),
            "expires_at": session.expires_at,
        }

    @app.post("/api/v1/auth/logout")
    async def logout(session_id: str = Query(...)) -> Any:
        """Revoke session."""
        rbac.revoke_session(session_id)
        return {"status": "logged_out"}

    @app.get("/api/v1/auth/users")
    async def get_users() -> Any:
        """Get all users."""
        return {"users": rbac.get_users()}

    @app.get("/api/v1/auth/audit")
    async def get_audit_log(limit: int = 100) -> Any:
        """Get audit log."""
        return {"entries": rbac.get_audit_log(limit=limit)}

    # ── Executive Overview ───────────────────────────────────────

    @app.get("/api/v1/executive")
    async def get_executive_overview() -> Any:
        """Get executive overview data."""
        data = portfolio_svc.get_executive_overview()
        data["health_score"] = portfolio_svc.get_health_score()
        return data

    @app.get("/api/v1/executive/equity-history")
    async def get_equity_history(limit: int = 500) -> Any:
        """Get equity curve data."""
        return {"history": portfolio_svc.get_equity_history(limit)}

    # ── Multi-Exchange Panel ─────────────────────────────────────

    @app.get("/api/v1/exchanges")
    async def get_exchanges() -> Any:
        """Get multi-exchange panel data."""
        return exchange_svc.get_exchange_panel()

    @app.get("/api/v1/exchanges/{name}")
    async def get_exchange(name: str) -> Any:
        """Get data for a specific exchange."""
        data = exchange_svc.get_exchange(name)
        if not data:
            raise HTTPException(status_code=404, detail=f"Exchange '{name}' not found")
        return data

    @app.get("/api/v1/exchanges/latencies")
    async def get_latencies() -> Any:
        """Get latencies for all exchanges."""
        return {"latencies": exchange_svc.get_all_latencies()}

    # ── Portfolio Allocation ─────────────────────────────────────

    @app.get("/api/v1/portfolio")
    async def get_portfolio() -> Any:
        """Get portfolio allocation data."""
        return allocation_svc.get_portfolio_allocation()

    @app.get("/api/v1/portfolio/allocations")
    async def get_allocations(limit: int = 50) -> Any:
        """Get allocation history."""
        return {"allocations": allocation_svc.get_allocation_audit(limit)}

    # ── Position Management ──────────────────────────────────────

    @app.get("/api/v1/positions")
    async def get_positions() -> Any:
        """Get open positions."""
        return {
            "positions": [],
            "pending_orders": [],
            "closed_positions": [],
            "timestamp": time.time(),
        }

    @app.post("/api/v1/positions/action")
    async def position_action(action: TradeAction) -> Any:
        """Execute a position action."""
        alert_svc.create_alert(
            level=AlertLevel.INFO,
            category=AlertCategory.EXECUTION,
            title=f"Position Action: {action.action}",
            message=f"Action {action.action} on position {action.position_id}",
            data={"position_id": action.position_id, "action": action.action},
        )
        return {"status": "action_queued", "action": action.action}

    # ── Signal Intelligence ──────────────────────────────────────

    @app.get("/api/v1/signals")
    async def get_signals() -> Any:
        """Get signal intelligence panel data."""
        return signal_engine.get_signal_panel()

    @app.get("/api/v1/signals/live")
    async def get_live_signals() -> Any:
        """Get real-time signal panel from the signal engine."""
        return signal_engine.get_signal_panel()

    @app.get("/api/v1/signals/stats")
    async def get_signal_stats() -> Any:
        """Get signal generation statistics."""
        return signal_engine.get_signal_stats()

    @app.get("/api/v1/signals/alerts")
    async def get_signal_alerts(limit: int = 20) -> Any:
        """Get recent signal alerts."""
        return {"alerts": signal_engine.get_active_alerts(limit)}

    @app.get("/api/v1/signals/sources")
    async def get_signal_sources() -> Any:
        """Get signal sources distribution."""
        panel = signal_engine.get_signal_panel()
        return {"sources": panel.get("source_distribution", {})}

    # ── Market Data Feed ─────────────────────────────────────────

    @app.get("/api/v1/market/ticks")
    async def get_market_ticks() -> Any:
        """Get all market ticks (live prices)."""
        return {"ticks": market_feed.get_all_ticks()}

    @app.get("/api/v1/market/tick/{symbol}")
    async def get_market_tick(symbol: str) -> Any:
        """Get tick for a specific symbol."""
        tick = market_feed.get_tick(symbol.upper())
        if not tick:
            raise HTTPException(status_code=404, detail=f"Symbol '{symbol}' not found")
        return tick

    @app.get("/api/v1/market/overview")
    async def get_market_overview() -> Any:
        """Get aggregated market overview."""
        return market_feed.get_market_overview()

    @app.get("/api/v1/market/history/{symbol}")
    async def get_price_history(symbol: str, limit: int = 200) -> Any:
        """Get price history for charting."""
        history = market_feed.get_price_history(symbol.upper(), limit)
        if not history:
            raise HTTPException(status_code=404, detail=f"No history for '{symbol}'")
        return {"symbol": symbol.upper(), "history": history}

    # ── Capital Allocation ───────────────────────────────────────

    @app.get("/api/v1/allocation")
    async def get_allocation() -> Any:
        """Get capital allocation panel data."""
        return allocation_svc.get_allocation_panel()

    @app.get("/api/v1/allocation/audit")
    async def get_allocation_audit(limit: int = 100) -> Any:
        """Get allocation audit log."""
        return {"audit": allocation_svc.get_allocation_audit(limit)}

    # ── Risk Management ──────────────────────────────────────────

    @app.get("/api/v1/risk")
    async def get_risk() -> Any:
        """Get risk management panel data."""
        return risk_svc.get_risk_panel()

    @app.get("/api/v1/risk/heatmap")
    async def get_risk_heatmap() -> Any:
        """Get risk heatmap data."""
        return risk_svc.get_risk_heatmap_data()

    @app.post("/api/v1/risk/stress-test")
    async def run_stress_test() -> Any:
        """Run portfolio stress tests."""
        results = risk_svc.run_stress_test()
        return {"stress_tests": results}

    # ── Arbitrage Panel ──────────────────────────────────────────

    @app.get("/api/v1/arbitrage")
    async def get_arbitrage() -> Any:
        """Get arbitrage panel data."""
        return arbitrage_svc.get_arbitrage_panel()

    @app.get("/api/v1/arbitrage/performance")
    async def get_arbitrage_performance() -> Any:
        """Get arbitrage performance summary."""
        return arbitrage_svc.get_performance_summary()

    @app.get("/api/v1/arbitrage/type/{arb_type}")
    async def get_arbitrage_by_type(arb_type: str) -> Any:
        """Get arbitrages by type."""
        return {"arbitrages": arbitrage_svc.get_by_type(arb_type)}

    # ── Execution Monitor ────────────────────────────────────────

    @app.get("/api/v1/execution")
    async def get_execution() -> Any:
        """Get execution monitor panel data."""
        return execution_svc.get_execution_panel()

    @app.get("/api/v1/execution/routing")
    async def get_routing_stats() -> Any:
        """Get routing statistics."""
        return execution_svc.get_routing_stats()

    # ── System Health ────────────────────────────────────────────

    @app.get("/api/v1/health")
    async def get_health() -> Any:
        """Get system health panel data."""
        return health_svc.get_health_panel()

    @app.get("/api/v1/health/history")
    async def get_health_history(limit: int = 200) -> Any:
        """Get health snapshot history."""
        return {"history": health_svc.get_health_history(limit)}

    # ── Alert Center ─────────────────────────────────────────────

    @app.get("/api/v1/alerts")
    async def get_alerts(
        limit: int = 50,
        level: str = "all",
        category: str = "all",
        unread: bool = False,
    ) -> Any:
        """Get alerts with optional filtering."""
        return {
            "alerts": alert_svc.get_alerts(limit, level, category, unread),
            "stats": alert_svc.get_alert_stats(),
        }

    @app.post("/api/v1/alerts/acknowledge")
    async def acknowledge_alert(req: AlertAckRequest) -> Any:
        """Acknowledge an alert."""
        success = alert_svc.acknowledge_alert(req.alert_id)
        return {"success": success}

    @app.post("/api/v1/alerts/mark-all-read")
    async def mark_all_alerts_read() -> Any:
        """Mark all alerts as read."""
        count = alert_svc.mark_all_read()
        return {"marked": count}

    @app.get("/api/v1/alerts/stats")
    async def get_alert_stats() -> Any:
        """Get alert statistics."""
        return alert_svc.get_alert_stats()

    # ── Performance Analytics ────────────────────────────────────

    @app.get("/api/v1/analytics")
    async def get_analytics() -> Any:
        """Get performance analytics."""
        return analytics_svc.get_performance_analytics()

    # ── Reporting ────────────────────────────────────────────────

    @app.post("/api/v1/reports/generate")
    async def generate_report(req: ReportRequest) -> Any:
        """Generate a report."""
        data = analytics_svc.get_performance_analytics()
        path = reporting_engine.generate_report(req.report_type, data, req.format)
        return {"status": "generated", "path": str(path), "format": req.format}

    @app.get("/api/v1/reports/list")
    async def list_reports() -> Any:
        """List generated reports."""
        return {"reports": reporting_engine.get_generated_reports()}

    @app.get("/api/v1/reports/download/{filename}")
    async def download_report(filename: str) -> Any:
        """Download a report file."""
        path = Path("data/reports") / filename
        if not path.exists():
            raise HTTPException(status_code=404, detail="Report not found")
        return FileResponse(path)

    # ── Dashboard Summary ────────────────────────────────────────

    @app.get("/api/v1/dashboard/summary")
    async def get_dashboard_summary() -> Any:
        """Get complete dashboard summary (all panels)."""
        return {
            "executive": portfolio_svc.get_executive_overview(),
            "exchanges": exchange_svc.get_exchange_panel(),
            "risk": risk_svc.get_risk_panel(),
            "signals": signal_svc.get_signal_panel(),
            "execution": execution_svc.get_execution_panel(),
            "arbitrage": arbitrage_svc.get_arbitrage_panel(),
            "allocation": allocation_svc.get_allocation_panel(),
            "health": health_svc.get_health_panel(),
            "alerts": alert_svc.get_alert_stats(),
            "analytics": analytics_svc.get_performance_analytics(),
            "ws_connections": ws_manager.connection_count,
            "timestamp": time.time(),
        }

    return app


# ── CLI Entry Point ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8502, log_level="info")
