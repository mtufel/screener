"""
Replay router: start/pause/resume/abort watch-mode replay backtests, query
snapshots and final reports, and stream progress over WebSocket.

Replay runs the LIVE screener execution path against historical data (see
replay_manager.py) — only the data feed and clock differ from live operation.
"""

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect

logger = logging.getLogger("replay-api")

router = APIRouter()


def _manager():
    from replay_manager import replay_manager

    return replay_manager


def _error_payload(status_code: int, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=message)


@router.post("/api/replay/start", summary="Start a Watch-Mode Replay Backtest")
async def api_replay_start(
    strategy: str = Query(..., description="Registered strategy name"),
    symbols: str = Query(default="BTC", description="Comma-separated symbols"),
    days: int = Query(default=14, ge=1, le=90, description="Lookback days"),
    ltf_timeframe: str = Query(default="5m", pattern="^(1m|5m|15m|1h)$", description="LTF timeframe"),
    speed: str = Query(default="MAX", description="Virtual-minutes per real-second, or 'MAX'"),
    params: Optional[Dict[str, Any]] = None,
):
    """Launches a replay run of the live execution path over historical candles.

    ``params`` (JSON body, optional) carries strategy-declared overrides; the
    effective set is resolved with framework precedence (request > strategy
    defaults) and reported in snapshots and the final report.
    """
    from strategies import get_strategy, list_strategy_names

    try:
        get_strategy(strategy)
    except KeyError:
        raise _error_payload(
            404,
            f"Unknown strategy {strategy!r}. Available: {', '.join(list_strategy_names())}",
        )

    try:
        replay_id = await _manager().start(
            strategy=strategy,
            symbols=[s for s in symbols.split(",") if s.strip()],
            days=days,
            ltf_timeframe=ltf_timeframe,
            params=params,
            speed=speed,
        )
    except RuntimeError as exc:
        raise _error_payload(422, str(exc))
    return {"status": "success", "replay_id": replay_id}


@router.post("/api/replay/{replay_id}/pause", summary="Pause a Running Replay")
async def api_replay_pause(replay_id: str):
    try:
        return await _manager().pause(replay_id)
    except KeyError:
        raise _error_payload(404, f"Unknown replay_id {replay_id!r}")


@router.post("/api/replay/{replay_id}/resume", summary="Resume a Paused Replay")
async def api_replay_resume(replay_id: str):
    try:
        return await _manager().resume(replay_id)
    except KeyError:
        raise _error_payload(404, f"Unknown replay_id {replay_id!r}")


@router.post("/api/replay/{replay_id}/abort", summary="Abort a Replay (partial report kept)")
async def api_replay_abort(replay_id: str):
    try:
        return await _manager().abort(replay_id)
    except KeyError:
        raise _error_payload(404, f"Unknown replay_id {replay_id!r}")


@router.get("/api/replay/{replay_id}", summary="Get Replay Snapshot (status, virtual time, ledger)")
async def api_replay_snapshot(replay_id: str):
    try:
        return _manager().snapshot(replay_id)
    except KeyError:
        raise _error_payload(404, f"Unknown replay_id {replay_id!r}")


@router.get("/api/replay/{replay_id}/report", summary="Get Final Replay Report")
async def api_replay_report(replay_id: str):
    try:
        return _manager().report(replay_id)
    except KeyError:
        raise _error_payload(404, f"Unknown replay_id {replay_id!r}")


@router.websocket("/ws/replay")
async def websocket_replay(websocket: WebSocket):
    """Streams replay frames (replay_progress / replay_complete / replay_error).

    Mirrors the /ws/extreme-live conventions: an initial_state frame on
    connect, then broadcast frames as runs advance.
    """
    import dashboard_ws

    await websocket.accept()
    dashboard_ws.dashboard_ws_manager.connect_replay(websocket)
    try:
        await websocket.send_json({"type": "initial_state", "channel": "replay"})
        while True:
            # Client -> server messages are only pings/ignores; keep the socket open.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("Replay WS error: %s", exc)
    finally:
        dashboard_ws.dashboard_ws_manager.disconnect_replay(websocket)
