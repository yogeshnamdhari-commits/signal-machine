"""
DeltaTerminal API Server — Production-grade FastAPI backend.
Bridges the AI Engine EventBus and JSON state to the Frontend.
Endpoints: 3001/api/* | WebSocket: 3001/ws/dashboard
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
import sys
from typing import List

AI_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AI_ROOT))

import pandas as pd
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from core.event_bus import bus
from dashboard.data_bridge import reader

app = FastAPI(title="DeltaTerminal Backend API")

# Enable CORS for Dashboard connection
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"Dashboard WebSocket connected. Active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                continue

manager = ConnectionManager()

# ── Event Bus Bridge ───────────────────────────────────────────

async def on_engine_event(topic: str, data: any):
    """Forward events from the internal bus to WebSockets."""
    payload = {
        "type": "EVENT",
        "topic": topic,
        "timestamp": datetime.now().isoformat(),
        "data": data
    }
    await manager.broadcast(payload)

@app.on_event("startup")
async def setup_event_bridge():
    # Subscribe to key topics mentioned in checklist
    bus.subscribe("signal_generated", lambda d: asyncio.create_task(on_engine_event("signals", d)))
    bus.subscribe("trade_event", lambda d: asyncio.create_task(on_engine_event("trades", d)))
    logger.info("API Event Bridge initialized.")

# ── REST Endpoints ──────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "engine": reader.read_status().running}

@app.get("/api/signals")
async def get_signals():
    return reader.read_signals()

@app.get("/api/positions")
async def get_positions():
    return reader.read_positions()

@app.get("/api/dashboard")
async def get_dashboard_summary():
    return {
        "metrics": reader.read_metrics(),
        "status": reader.read_status(),
        "intelligence": reader.read_market_intelligence()
    }

@app.get("/api/orders")
async def get_orders():
    return reader.read_trade_history()

@app.get("/api/allocation")
async def get_allocation_logs():
    log_path = Path("data/reports/allocation_log.csv")
    if not log_path.exists():
        return []
    df = pd.read_csv(log_path)
    return df.tail(100).to_dict(orient="records")

@app.get("/api/risk")
async def get_risk_state():
    # This integrates with the Bridge's market intelligence/metrics
    return reader.read_metrics()

# ── WebSocket Endpoint ──────────────────────────────────────────

@app.websocket("/ws/dashboard")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        # Send initial state
        await websocket.send_json({
            "type": "INIT",
            "data": {"status": "CONNECTED", "server_time": datetime.now().isoformat()}
        })
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=3001)