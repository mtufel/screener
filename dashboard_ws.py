"""
Dashboard WebSocket: connection manager and the /ws/extreme-live endpoint.

The singleton `dashboard_ws_manager` is created here and mirrored onto `main`
so `main.dashboard_ws_manager` stays the single point of truth for both direct
substitution (`main.dashboard_ws_manager.broadcast = sink.broadcast`) and
instance-scope patching (`patch("main.dashboard_ws_manager.broadcast", ...)`).

`broadcast()` targets the live dashboard sockets; `broadcast_replay()` targets
the dedicated replay-channel sockets (falling back to the live set for legacy
clients that haven't connected on the replay channel).
"""

import asyncio
import logging
from typing import Any, Dict, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app_config import state

logger = logging.getLogger("fvg-screener.ws")

router = APIRouter()


class DashboardWSManager:
    """Manages active browser WebSocket connections for real-time dashboard push updates."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.replay_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)

    def connect_replay(self, websocket: WebSocket):
        """Registers an already-accepted replay-channel socket."""
        self.replay_connections.add(websocket)

    def disconnect_replay(self, websocket: WebSocket):
        self.replay_connections.discard(websocket)

    async def broadcast(self, message: Dict[str, Any]):
        if not self.active_connections:
            return
        dead = set()

        async def _send(ws: WebSocket):
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=2.0)
            except Exception:
                dead.add(ws)

        await asyncio.gather(*[_send(ws) for ws in list(self.active_connections)], return_exceptions=True)
        if dead:
            self.active_connections.difference_update(dead)

    async def broadcast_replay(self, message: Dict[str, Any]):
        """Pushes a replay frame to replay-channel sockets only (falls back to the main dashboard set for legacy clients)."""
        targets = self.replay_connections or self.active_connections
        if not targets:
            return
        dead = set()

        async def _send(ws: WebSocket):
            try:
                await asyncio.wait_for(ws.send_json(message), timeout=2.0)
            except Exception:
                dead.add(ws)

        await asyncio.gather(*[_send(ws) for ws in list(targets)], return_exceptions=True)
        if dead:
            self.replay_connections.difference_update(dead)
            self.active_connections.difference_update(dead)


dashboard_ws_manager = DashboardWSManager()


def _register_singleton_in_main() -> None:
    """Mirrors the manager singleton onto the `main` module (import-time late binding)."""
    import main

    main.dashboard_ws_manager = dashboard_ws_manager


_register_singleton_in_main()


@router.websocket("/ws/extreme-live")
async def websocket_extreme_live(websocket: WebSocket):
    """Real-time WebSocket feed for dashboard live setups, trade ledger events, and KPIs."""
    await dashboard_ws_manager.connect(websocket)
    try:
        from extreme_trade_tracker import extreme_trade_tracker
        initial_data = {
            "type": "initial_state",
            "is_running": state.get("extreme_is_running", False),
            "interval_seconds": state.get("extreme_interval_seconds", 30),
            "last_scan_time_ist": state.get("extreme_last_scan_time_ist", "--"),
            "total_cycles": state.get("extreme_total_cycles", 0),
            "setups": state.get("extreme_setups", []),
            "history_data": extreme_trade_tracker.get_filtered_trades(),
        }
        await websocket.send_json(initial_data)

        while True:
            text = await websocket.receive_text()
            if text == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        dashboard_ws_manager.disconnect(websocket)
    except Exception as exc:
        logger.debug("Dashboard WS disconnected: %s", exc)
        dashboard_ws_manager.disconnect(websocket)
