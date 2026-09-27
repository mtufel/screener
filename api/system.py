"""
System router: root/dashboard HTML, health, status, runtime config toggle, and
the Telegram test alert endpoint.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app_config import (
    COINS_WHITELIST,
    ENABLE_STRATEGY_2,
    IST,
    TEMPLATES_DIR,
    logger,
    state,
)
from screener_cycle import extreme_screener_background_worker
from telegram_client import send_telegram_alert

router = APIRouter()


# ==============================================================================
# UI & ROOT ENDPOINTS
# ==============================================================================
@router.get("/", summary="Web Dashboard / Status")
async def root(request: Request, accept: Optional[str] = Header(default=None)):
    accept_header = accept or request.headers.get("accept", "")
    if "application/json" in accept_header and "text/html" not in accept_header:
        return {"status": "screener running"}

    index_html = TEMPLATES_DIR / "index.html"
    if index_html.exists():
        return HTMLResponse(content=index_html.read_text(encoding="utf-8"))

    return {"status": "screener running"}


@router.get("/dashboard", response_class=HTMLResponse, summary="Direct Web Dashboard Route")
async def dashboard():
    index_html = TEMPLATES_DIR / "index.html"
    if index_html.exists():
        return HTMLResponse(content=index_html.read_text(encoding="utf-8"))
    return HTMLResponse("<h3>Dashboard template not found.</h3>", status_code=404)


# ==============================================================================
# JSON API ENDPOINTS
# ==============================================================================
@router.get("/health", summary="Health Check")
@router.get("/api/health", summary="API Health Check")
async def health():
    return {
        "status": "healthy" if state.get("extreme_is_running") else "stopped",
        "strategy_2_enabled": state.get("strategy_2_enabled", ENABLE_STRATEGY_2),
        "timezone": "IST (UTC+5:30)",
        "coins_whitelist": state.get("coins_whitelist", COINS_WHITELIST),
        "universe_count": state["universe_count"],
        "total_scans_completed": state["total_scans_completed"],
        "last_scan_time_ist": state["last_scan_time_ist"],
        "last_scan_results_count": state["last_scan_results_count"],
        "activated_count": state["activated_count"],
        "pending_count": state["pending_count"],
        "last_scan_setups": state["last_scan_setups"],
    }


@router.get("/api/status", summary="Screener Status and Live Setups")
async def get_status():
    return {
        "strategy_2_enabled": state.get("strategy_2_enabled", ENABLE_STRATEGY_2),
        "is_running": state["extreme_is_running"],
        "timezone": "IST",
        "coins_whitelist": state.get("coins_whitelist", COINS_WHITELIST),
        "universe_count": state["universe_count"],
        "total_scans_completed": state["total_scans_completed"],
        "last_scan_time_ist": state["last_scan_time_ist"],
        "last_scan_results_count": state["last_scan_results_count"],
        "activated_count": state["activated_count"],
        "pending_count": state["pending_count"],
        "last_scan_setups": state["last_scan_setups"],
    }


@router.get("/api/config", summary="Get Current Strategy Runtime Config")
@router.post("/api/config", summary="Update Strategy Runtime Config")
async def config_endpoint(
    enable_strategy_2: Optional[bool] = Query(default=None),
    coins_whitelist: Optional[str] = Query(default=None),
):
    """Dynamically get or update runtime screener strategy parameters."""
    if enable_strategy_2 is not None:
        state["strategy_2_enabled"] = bool(enable_strategy_2)
        state["extreme_is_running"] = bool(enable_strategy_2)
        state["is_running"] = bool(enable_strategy_2)
        if state["extreme_is_running"] and (state.get("extreme_background_task") is None or state["extreme_background_task"].done()):
            state["extreme_background_task"] = asyncio.create_task(extreme_screener_background_worker())
        elif not state["extreme_is_running"]:
            if state.get("extreme_background_task"):
                state["extreme_background_task"].cancel()
    if coins_whitelist is not None:
        state["coins_whitelist"] = coins_whitelist.strip()

    return JSONResponse(
        content={
            "status": "success",
            "config": {
                "strategy_2_enabled": state.get("strategy_2_enabled", ENABLE_STRATEGY_2),
                "coins_whitelist": state.get("coins_whitelist", COINS_WHITELIST),
            },
        }
    )


@router.post("/api/test-telegram", summary="Test Telegram Alert Dispatch")
async def test_telegram():
    from telegram_client import send_telegram_photo
    from chart_generator import generate_extreme_setup_chart
    from strategy_extreme_fvg import get_last_n_candles

    now_ist = datetime.now(IST).strftime("%d-%b-%Y %I:%M:%S %p IST")
    candles = await get_last_n_candles(symbol="BTC", timeframe="5m", n=50)
    if not candles:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to fetch live BTC candles"})

    curr = candles[-1].close
    entry = curr
    sl = min([c.low for c in candles[-10:]])
    if sl >= entry:
        sl = entry * 0.995
    risk = entry - sl
    tp1, tp2, tp3 = entry + risk, entry + 2 * risk, entry + 3 * risk

    chart_bytes = generate_extreme_setup_chart(
        symbol="BTC",
        direction="Bullish",
        candles_ltf=candles,
        htf_fvg_bottom=curr * 0.996,
        htf_fvg_top=curr * 1.004,
        htf_first_touch_ist=None,
        ltf_fvg_bottom=curr * 0.998,
        ltf_fvg_top=curr * 1.001,
        ltf_fvg_formed_ts=candles[-1].timestamp,
        entry_price=entry,
        stop_loss=sl,
        tp_1r=tp1,
        tp_2r=tp2,
        tp_3r=tp3,
        state="PENDING_RETRACE",
        ltf_timeframe="5m",
    )

    test_caption = (
        "🚀 🟢 <b>BTC-PERP — TEST ALERT</b>\n"
        "<b>Direction:</b> Bullish (Extreme LTF)\n"
        f"<b>Entry Price:</b> ${entry:,.2f}\n"
        f"<b>Stop Loss:</b> ≤ ${sl:,.2f}\n"
        f"<i>Live real-market test alert at {now_ist}</i>"
    )

    success = False
    if chart_bytes and len(chart_bytes) > 0:
        success = await send_telegram_photo(chart_bytes, test_caption)
    else:
        success = await send_telegram_alert(test_caption)

    if success:
        return {"status": "success", "message": "Test chart alert with real candles sent successfully to Telegram."}
    return JSONResponse(
        status_code=400,
        content={
            "status": "error",
            "message": "Failed to send alert. Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env",
        },
    )
