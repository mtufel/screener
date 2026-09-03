"""
FastAPI application for Fair Value Gap (FVG) crypto day-trading screener.
Features 2-Stage Strategy:
1. 4H Active FVG Cache (with invalidation) + "ANY_VALID" vs "MOST_RECENT" selection modes.
2. New LTF FVG Formation (1m, 5m, 15m) -> Alert 1: Setup Formed (Pending Retrace).
3. Price Retrace into LTF FVG -> Alert 2: Trade Activated with 1R, 1.5R, 2R, 3R TP targets.
4. Interactive real-time Web Dashboard in IST & Historical Backtesting.
"""

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone, timedelta
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from hyperliquid_client import hyperliquid_client
import strategy
from strategy import SetupResult, run_screener, get_last_n_candles, USE_CLOSE_BASED_INVALIDATION, MAX_HTF_RETRACE_CANDLES, SESSION_FILTER_ENABLED
from telegram_client import broadcast_setups_stateful, broadcast_trade_updates, send_telegram_alert
from trade_tracker import trade_tracker

load_dotenv()

# IST Timezone (UTC + 5:30)
IST = timezone(timedelta(hours=5, minutes=30))

# ==============================================================================
# LOGGING CONFIGURATION
# ==============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fvg-screener")

# ==============================================================================
# CONSTANTS & CONFIG
# ==============================================================================
DEFAULT_LTF_TIMEFRAME = os.getenv("LTF_TIMEFRAME", "5m")
DEFAULT_HTF_MODE = os.getenv("HTF_SELECTION_MODE", "ANY_VALID")
SCAN_INTERVAL_MINUTES = int(os.getenv("SCAN_INTERVAL_MINUTES", "15"))
TOP_N_ALERTS = int(os.getenv("TOP_N_ALERTS", "5"))
COINS_WHITELIST = os.getenv("COINS_WHITELIST", "BTC,ETH,WTIOIL,SILVER,GOLD,PAXG").strip()

# Global state
EXTREME_SCAN_INTERVAL_SECONDS = int(os.getenv("EXTREME_SCAN_INTERVAL_SECONDS", "30"))
EXTREME_LTF_TIMEFRAME = os.getenv("EXTREME_LTF_TIMEFRAME", "15m")
EXTREME_COMPLETION_TARGET = os.getenv("EXTREME_COMPLETION_TARGET", "2R")
EXTREME_MIN_GAP_PCT = float(os.getenv("EXTREME_MIN_GAP_PCT", "0.05"))
EXTREME_USE_CLOSE_INVALIDATION = os.getenv("EXTREME_USE_CLOSE_INVALIDATION", "false").lower() == "true"

state: Dict[str, Any] = {
    "is_running": False,
    "scan_interval_minutes": SCAN_INTERVAL_MINUTES,
    "top_n_alerts": TOP_N_ALERTS,
    "coins_whitelist": COINS_WHITELIST,
    "ltf_timeframe": DEFAULT_LTF_TIMEFRAME,
    "htf_mode": DEFAULT_HTF_MODE,
    "use_close_invalidation": strategy.USE_CLOSE_BASED_INVALIDATION,
    "max_htf_retrace_candles": strategy.MAX_HTF_RETRACE_CANDLES,
    "session_filter_enabled": strategy.SESSION_FILTER_ENABLED,
    "single_position": trade_tracker.single_active_position,
    "universe_count": 0,
    "last_scan_time": None,
    "last_scan_time_ist": None,
    "last_scan_results_count": 0,
    "activated_count": 0,
    "pending_count": 0,
    "last_scan_setups": [],
    "total_scans_completed": 0,
    "background_task": None,
    "monitor_task": None,
    # Extreme Strategy Daemon State
    "extreme_is_running": True,
    "extreme_interval_seconds": EXTREME_SCAN_INTERVAL_SECONDS,
    "extreme_ltf": EXTREME_LTF_TIMEFRAME,
    "extreme_target": EXTREME_COMPLETION_TARGET,
    "extreme_min_gap": EXTREME_MIN_GAP_PCT,
    "extreme_use_close": EXTREME_USE_CLOSE_INVALIDATION,
    "extreme_last_scan_time_ist": None,
    "extreme_setups": [],
    "extreme_active_count": 0,
    "extreme_pending_count": 0,
    "extreme_total_cycles": 0,
    "extreme_background_task": None,
    "extreme_notified_states": {},
}


# ==============================================================================
# BACKGROUND SCAN & TRADE MONITOR LOOPS
# ==============================================================================
async def execute_screener_cycle(
    ltf: Optional[str] = None,
    htf_mode: Optional[str] = None,
    use_close_invalidation: Optional[bool] = None,
    max_htf_retrace_candles: Optional[int] = None,
    session_filter_enabled: Optional[bool] = None,
) -> List[SetupResult]:
    """Execute a single screener cycle, dispatch alerts with charts, and monitor open trades."""
    start_time_utc = datetime.now(timezone.utc)
    start_time_ist = datetime.now(IST)
    ltf_to_use = ltf or state.get("ltf_timeframe", DEFAULT_LTF_TIMEFRAME)
    htf_mode_to_use = htf_mode or state.get("htf_mode", DEFAULT_HTF_MODE)
    close_inval = state.get("use_close_invalidation", False) if use_close_invalidation is None else use_close_invalidation
    retrace_win = state.get("max_htf_retrace_candles", 18) if max_htf_retrace_candles is None else max_htf_retrace_candles
    sess_enabled = state.get("session_filter_enabled", False) if session_filter_enabled is None else session_filter_enabled

    logger.info(
        "--- Starting 2-Stage FVG Screener Cycle [%s IST | LTF=%s | 4H Mode=%s | CloseInval=%s | RetraceWin=%s | SessionFilter=%s] ---",
        start_time_ist.strftime("%Y-%m-%d %I:%M:%S %p"),
        ltf_to_use,
        htf_mode_to_use,
        close_inval,
        retrace_win,
        sess_enabled,
    )

    try:
        universe = await hyperliquid_client.get_universe()
        whitelist_raw = os.getenv("COINS_WHITELIST", state.get("coins_whitelist", COINS_WHITELIST)).strip()
        if whitelist_raw and whitelist_raw.upper() != "ALL":
            allowed = {c.strip().upper() for c in whitelist_raw.split(",") if c.strip()}
            from hyperliquid_client import SYMBOL_ALIASES
            for raw_sym in list(allowed):
                if raw_sym in SYMBOL_ALIASES:
                    allowed.add(SYMBOL_ALIASES[raw_sym])
            active_count = len([c for c in universe if c.upper() in allowed])
        else:
            active_count = len(universe)

        state["universe_count"] = active_count
        state["coins_whitelist"] = whitelist_raw
        state["ltf_timeframe"] = ltf_to_use
        state["htf_mode"] = htf_mode_to_use
        state["use_close_invalidation"] = close_inval
        state["max_htf_retrace_candles"] = retrace_win
        state["session_filter_enabled"] = sess_enabled

        all_mids = await hyperliquid_client.get_all_mids()

        # 1. Check open active trades for TP/SL hits
        tp_sl_updates = trade_tracker.check_open_trades(all_mids)
        if tp_sl_updates:
            logger.info("Broadcasting %d trade TP/SL status updates to Telegram...", len(tp_sl_updates))
            await broadcast_trade_updates(tp_sl_updates)

        # 2. Run Screener for new setups
        top_setups = await run_screener(
            top_n=TOP_N_ALERTS,
            ltf_timeframe=ltf_to_use,
            htf_mode=htf_mode_to_use,
            use_close_invalidation=close_inval,
            max_htf_retrace_candles=retrace_win,
            session_filter_enabled=sess_enabled,
        )

        activated_setups = [s for s in top_setups if s.stage == "ACTIVATED"]
        pending_setups = [s for s in top_setups if s.stage == "PENDING_RETRACE"]

        state["last_scan_time"] = start_time_utc.isoformat()
        state["last_scan_time_ist"] = start_time_ist.strftime("%d-%b-%Y %I:%M:%S %p IST")
        state["last_scan_results_count"] = len(top_setups)
        state["activated_count"] = len(activated_setups)
        state["pending_count"] = len(pending_setups)
        state["total_scans_completed"] += 1
        state["last_scan_setups"] = [s.to_dict() for s in top_setups]

        # 3. Fetch candle data and dispatch stateful alerts (with TradingView charts)
        if top_setups:
            candles_map = {}
            for s in top_setups:
                try:
                    c_list = await get_last_n_candles(symbol=s.symbol, timeframe=ltf_to_use, n=50)
                    candles_map[s.symbol] = c_list
                except Exception as exc:
                    logger.warning("Failed to fetch LTF candles for chart %s: %s", s.symbol, exc)

            sent_count = await broadcast_setups_stateful(top_setups, candles_map=candles_map)
            logger.info("Telegram broadcast finished (%d new setup messages sent).", sent_count)
        else:
            logger.info("Scan completed: No qualified 4H+LTF FVG setups found.")

        return top_setups
    except Exception as exc:
        logger.error("Unexpected error during screener cycle: %s", exc, exc_info=True)
        return []


async def trade_monitor_worker():
    """Lightweight 30-second loop monitoring active trades in real time for TP/SL hits."""
    logger.info("Real-time trade TP/SL monitor started (30s interval).")
    while state["is_running"]:
        try:
            await asyncio.sleep(30)
            if not state["is_running"]:
                break
            all_mids = await hyperliquid_client.get_all_mids()
            if all_mids:
                updates = trade_tracker.check_open_trades(all_mids)
                if updates:
                    logger.info("Real-time monitor detected %d trade updates. Broadcasting...", len(updates))
                    await broadcast_trade_updates(updates)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("Error in trade monitor worker: %s", exc)


async def screener_background_worker():
    """Continuous background loop running the screener every SCAN_INTERVAL_MINUTES."""
    logger.info("Background screener worker started (Interval: %d minutes).", SCAN_INTERVAL_MINUTES)
    state["is_running"] = True

    await execute_screener_cycle()

    while state["is_running"]:
        try:
            sleep_duration_seconds = SCAN_INTERVAL_MINUTES * 60
            await asyncio.sleep(sleep_duration_seconds)

            if state["is_running"]:
                await execute_screener_cycle()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Error in background worker loop: %s. Retrying in 60s...", exc)
            await asyncio.sleep(60)


# ==============================================================================
# EXTREME LTF BACKGROUND SCREENER DAEMON (STEP 6)
# ==============================================================================
async def send_extreme_telegram_alert(message: str, image_bytes: Optional[bytes] = None) -> bool:
    """Dispatches HTML alert (with optional high-res chart photo) to configured Telegram chat."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    import httpx
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if image_bytes:
                url = f"https://api.telegram.org/bot{token}/sendPhoto"
                files = {"photo": ("chart.png", image_bytes, "image/png")}
                data = {"chat_id": chat_id, "caption": message, "parse_mode": "HTML"}
                resp = await client.post(url, data=data, files=files)
                if resp.status_code == 200:
                    return True
            # Fallback to sendMessage
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            resp = await client.post(url, json=payload)
            return resp.status_code == 200
    except Exception as exc:
        logger.warning("Failed to dispatch Extreme Telegram alert: %s", exc)
        return False


async def execute_extreme_screener_cycle() -> List[Dict[str, Any]]:
    """Runs a single background scan across whitelisted coins for Extreme LTF setups."""
    from strategy_extreme_fvg import get_extreme_setup_for_symbol
    from hyperliquid_client import SYMBOL_ALIASES
    from chart_generator import generate_extreme_setup_chart

    start_time_ist = datetime.now(IST)
    whitelist_raw = state.get("coins_whitelist", COINS_WHITELIST).strip()
    coin_list = [c.strip().upper() for c in whitelist_raw.split(",") if c.strip()]
    ltf = state.get("extreme_ltf", EXTREME_LTF_TIMEFRAME)
    target = state.get("extreme_target", EXTREME_COMPLETION_TARGET)
    min_gap = state.get("extreme_min_gap", EXTREME_MIN_GAP_PCT)
    use_close = state.get("extreme_use_close", EXTREME_USE_CLOSE_INVALIDATION)

    setups_out = []
    mids = await hyperliquid_client.get_all_mids()

    for sym in coin_list:
        try:
            raw_sym = SYMBOL_ALIASES.get(sym, sym)
            setup = await get_extreme_setup_for_symbol(
                symbol=raw_sym,
                ltf_timeframe=ltf,
                use_close_invalidation=use_close,
                min_gap_pct=min_gap,
                completion_target=target,
            )
            if setup:
                curr_px = float(mids.get(raw_sym, setup.entry_price))
                dist_pct = ((curr_px - setup.entry_price) / setup.entry_price) * 100
                setup_dict = {
                    "symbol": sym,
                    "direction": setup.direction,
                    "state": setup.state,
                    "entry_price": setup.entry_price,
                    "current_price": curr_px,
                    "dist_pct": round(dist_pct, 2),
                    "stop_loss": setup.stop_loss,
                    "risk_r": round(setup.risk_r, 4),
                    "risk_pct": round(setup.risk_pct, 2),
                    "tp_1r": round(setup.tp_1r, 4),
                    "tp_2r": round(setup.tp_2r, 4),
                    "tp_3r": round(setup.tp_3r, 4),
                    "floating_r": round(setup.floating_r, 2),
                    "entry_time_ist": setup.entry_time_ist,
                    "completion_target": setup.completion_target,
                    "ltf_timeframe": setup.ltf_timeframe,
                    "anchor": {
                        "direction": setup.anchor.fvg.direction,
                        "bottom": setup.anchor.fvg.bottom,
                        "top": setup.anchor.fvg.top,
                        "formed_time_ist": setup.anchor.fvg.formed_time_ist,
                        "first_touch_time_ist": setup.anchor.first_touch_time_ist,
                        "most_recent_touch_time_ist": setup.anchor.most_recent_touch_time_ist,
                    },
                    "target_fvg": {
                        "direction": setup.ltf_fvg.direction,
                        "bottom": setup.ltf_fvg.bottom,
                        "top": setup.ltf_fvg.top,
                        "width": setup.ltf_fvg.width,
                        "gap_pct": round(setup.ltf_fvg.gap_pct, 3),
                        "formed_time_ist": setup.ltf_fvg.formed_time_ist,
                        "formed_at": setup.ltf_fvg.formed_at,
                    },
                    "unmitigated_count": len(setup.all_unmitigated_fvgs),
                }
                setups_out.append(setup_dict)
        except Exception as exc:
            logger.warning("Error in background extreme scan for %s: %s", sym, exc)

    # Process all setups through ExtremeTradeTracker
    from extreme_trade_tracker import extreme_trade_tracker
    events = extreme_trade_tracker.process_live_setups(setups_out, mids)

    for evt_type, tr in events:
        raw_sym = SYMBOL_ALIASES.get(tr.symbol, tr.symbol)
        side = "LONG" if tr.direction == "Bullish" else "SHORT"
        chart_img = None
        try:
            candles_ltf = await get_last_n_candles(symbol=raw_sym, timeframe=tr.ltf_timeframe, n=60)
            chart_img = generate_extreme_setup_chart(
                symbol=tr.symbol,
                direction=tr.direction,
                candles_ltf=candles_ltf,
                htf_fvg_bottom=tr.htf_anchor.get("bottom", 0.0),
                htf_fvg_top=tr.htf_anchor.get("top", 0.0),
                htf_first_touch_ist=tr.htf_anchor.get("first_touch_time_ist"),
                ltf_fvg_bottom=tr.ltf_fvg.get("bottom", 0.0),
                ltf_fvg_top=tr.ltf_fvg.get("top", 0.0),
                ltf_fvg_formed_ts=tr.ltf_fvg.get("formed_at", 0),
                entry_price=tr.entry_price,
                stop_loss=tr.stop_loss,
                tp_1r=tr.tp_1r,
                tp_2r=tr.tp_2r,
                tp_3r=tr.tp_3r,
                state=tr.state,
                floating_r=tr.floating_r,
                ltf_timeframe=tr.ltf_timeframe,
            )
        except Exception as c_exc:
            logger.debug("Chart generation failed for event %s: %s", evt_type, c_exc)

        if evt_type == "NEW_SETUP" and tr.state == "PENDING_RETRACE":
            dist = ((float(mids.get(tr.symbol, tr.entry_price)) - tr.entry_price) / tr.entry_price) * 100
            msg = (
                f"🔔 <b>[NEW SETUP] {tr.symbol} {side} ({tr.ltf_timeframe})</b>\n\n"
                f"• <b>4H Anchor:</b> {tr.htf_anchor.get('direction', '')} [${tr.htf_anchor.get('bottom', 0):,.2f} - ${tr.htf_anchor.get('top', 0):,.2f}]\n"
                f"  └ <i>Formed:</i> {tr.htf_anchor.get('formed_time_ist', '--')} | <i>1st Touch:</i> {tr.htf_anchor.get('first_touch_time_ist', '--')}\n"
                f"• <b>Extreme {tr.ltf_timeframe} FVG:</b> [${tr.ltf_fvg.get('bottom', 0):,.2f} - ${tr.ltf_fvg.get('top', 0):,.2f}] ({tr.ltf_fvg.get('gap_pct', 0):.2f}%)\n"
                f"  └ <i>Formed:</i> {tr.ltf_fvg.get('formed_time_ist', '--')}\n"
                f"• <b>Limit Order Entry:</b> <code>${tr.entry_price:,.2f}</code> ({dist:+.2f}% away)\n"
                f"• <b>Stop Loss:</b> <code>${tr.stop_loss:,.2f}</code>\n"
                f"• <b>Risk ($R$):</b> ${tr.risk_r:,.2f} ({tr.risk_pct:.2f}%)\n"
                f"• <b>TP 1R:</b> ${tr.tp_1r:,.2f} | <b>TP 2R:</b> ${tr.tp_2r:,.2f} | <b>TP 3R:</b> ${tr.tp_3r:,.2f}\n"
                f"• <b>Status:</b> ⏳ WAITING FOR RETRACE"
            )
            logger.info("Fired Telegram Setup Alert for %s %s (with chart)", tr.symbol, side)
            await send_extreme_telegram_alert(msg, image_bytes=chart_img)

        elif evt_type == "ENTRY_FILLED":
            primary_tp = tr.tp_2r if tr.completion_target == "2R" else tr.tp_1r
            msg = (
                f"🚀 <b>[ENTRY FILLED] {tr.symbol} {side} IS NOW LIVE!</b>\n\n"
                f"• <b>Filled At:</b> <code>${tr.entry_price:,.2f}</code>\n"
                f"• <b>Time:</b> {tr.entry_filled_at_ist or 'Live'}\n"
                f"• <b>Stop Loss:</b> <code>${tr.stop_loss:,.2f}</code>\n"
                f"• <b>Primary Target ({tr.completion_target}):</b> <code>${primary_tp:,.2f}</code>\n"
                f"• <b>Status:</b> 🚀 IN POSITION (Monitoring TP/SL)"
            )
            logger.info("Fired Telegram Entry Alert for %s %s (with chart)", tr.symbol, side)
            await send_extreme_telegram_alert(msg, image_bytes=chart_img)

        elif evt_type == "TP_HIT":
            msg = (
                f"🎉 <b>[TARGET ACHIEVED] {tr.symbol} {side} HIT {tr.completion_target}!</b>\n\n"
                f"• <b>Realized Gain:</b> <code>+{tr.realized_r:.1f}R</code>\n"
                f"• <b>Entry Price:</b> <code>${tr.entry_price:,.2f}</code>\n"
                f"• <b>Exit Time:</b> {tr.closed_at_ist}\n"
                f"• <b>Duration:</b> {tr.duration_min} minutes\n"
                f"• <b>Max MFE:</b> +{tr.mfe_r:.2f}R\n"
                f"• <b>Status:</b> 🏆 TRADE WON"
            )
            logger.info("Fired Telegram TP Hit Alert for %s %s", tr.symbol, side)
            await send_extreme_telegram_alert(msg, image_bytes=chart_img)

        elif evt_type == "SL_HIT":
            msg = (
                f"🛑 <b>[STOP LOSS HIT] {tr.symbol} {side} CLOSED</b>\n\n"
                f"• <b>Realized Loss:</b> <code>-1.0R</code>\n"
                f"• <b>Entry Price:</b> <code>${tr.entry_price:,.2f}</code> | <b>SL:</b> <code>${tr.stop_loss:,.2f}</code>\n"
                f"• <b>Exit Time:</b> {tr.closed_at_ist}\n"
                f"• <b>Duration:</b> {tr.duration_min} minutes\n"
                f"• <b>Max MFE:</b> +{tr.mfe_r:.2f}R\n"
                f"• <b>Status:</b> ❌ STOPPED OUT"
            )
            logger.info("Fired Telegram SL Hit Alert for %s %s", tr.symbol, side)
            await send_extreme_telegram_alert(msg, image_bytes=chart_img)

    act_count = len([s for s in setups_out if s["state"] == "TRADE_ACTIVE"])
    pend_count = len([s for s in setups_out if s["state"] == "PENDING_RETRACE"])

    state["extreme_setups"] = setups_out
    state["extreme_active_count"] = act_count
    state["extreme_pending_count"] = pend_count
    state["extreme_last_scan_time_ist"] = start_time_ist.strftime("%d-%b-%Y %I:%M:%S %p IST")
    state["extreme_total_cycles"] += 1

    return setups_out


async def extreme_screener_background_worker():
    """Continuous background loop running the Extreme Screener every EXTREME_SCAN_INTERVAL_SECONDS."""
    logger.info("Extreme Background Screener Daemon started (Interval: %ds).", state.get("extreme_interval_seconds", 30))
    while state.get("extreme_is_running", True):
        try:
            await execute_extreme_screener_cycle()
            await asyncio.sleep(state.get("extreme_interval_seconds", 30))
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Error in extreme background worker: %s. Retrying in 10s...", exc)
            await asyncio.sleep(10)


# ==============================================================================
# APPLICATION LIFESPAN
# ==============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles startup and shutdown events for FastAPI."""
    logger.info("Starting Crypto FVG Screener application (IST & Extreme Strategy Daemon)...")
    state["is_running"] = True
    state["extreme_is_running"] = True
    worker_task = asyncio.create_task(screener_background_worker())
    monitor_task = asyncio.create_task(trade_monitor_worker())
    extreme_task = asyncio.create_task(extreme_screener_background_worker())
    state["background_task"] = worker_task
    state["monitor_task"] = monitor_task
    state["extreme_background_task"] = extreme_task

    yield

    state["is_running"] = False
    state["extreme_is_running"] = False
    if state["background_task"]:
        state["background_task"].cancel()
    if state["monitor_task"]:
        state["monitor_task"].cancel()
    if state["extreme_background_task"]:
        state["extreme_background_task"].cancel()

    await hyperliquid_client.close()
    logger.info("Application shutdown complete.")


from fastapi.staticfiles import StaticFiles

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
os.makedirs(STATIC_DIR / "charts", exist_ok=True)

# ==============================================================================
# FASTAPI APP DEFINITION
# ==============================================================================
app = FastAPI(
    title="Crypto Fair Value Gap (FVG) Screener",
    description="2-Stage 4H + 1m/5m/15m FVG Screener & Backtester for Hyperliquid perps with Telegram alerts & Web Dashboard in IST.",
    version="2.3.0",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ==============================================================================
# UI & ROOT ENDPOINTS
# ==============================================================================
@app.get("/", summary="Web Dashboard / Status")
async def root(request: Request, accept: Optional[str] = Header(default=None)):
    accept_header = accept or request.headers.get("accept", "")
    
    if "application/json" in accept_header and "text/html" not in accept_header:
        return {"status": "screener running"}

    index_html = TEMPLATES_DIR / "index.html"
    if index_html.exists():
        return HTMLResponse(content=index_html.read_text(encoding="utf-8"))

    return {"status": "screener running"}


@app.get("/dashboard", response_class=HTMLResponse, summary="Direct Web Dashboard Route")
async def dashboard():
    index_html = TEMPLATES_DIR / "index.html"
    if index_html.exists():
        return HTMLResponse(content=index_html.read_text(encoding="utf-8"))
    return HTMLResponse("<h3>Dashboard template not found.</h3>", status_code=404)


# ==============================================================================
# JSON API ENDPOINTS
# ==============================================================================
@app.get("/health", summary="Health Check")
@app.get("/api/health", summary="API Health Check")
async def health():
    return {
        "status": "healthy" if state["is_running"] else "stopped",
        "timezone": "IST (UTC+5:30)",
        "scan_interval_minutes": SCAN_INTERVAL_MINUTES,
        "top_n_alerts": TOP_N_ALERTS,
        "ltf_timeframe": state.get("ltf_timeframe", DEFAULT_LTF_TIMEFRAME),
        "htf_mode": state.get("htf_mode", DEFAULT_HTF_MODE),
        "use_close_invalidation": state.get("use_close_invalidation", False),
        "max_htf_retrace_candles": state.get("max_htf_retrace_candles", 18),
        "session_filter_enabled": state.get("session_filter_enabled", False),
        "single_position": trade_tracker.single_active_position,
        "coins_whitelist": state.get("coins_whitelist", COINS_WHITELIST),
        "universe_count": state["universe_count"],
        "total_scans_completed": state["total_scans_completed"],
        "last_scan_time_ist": state["last_scan_time_ist"],
        "last_scan_results_count": state["last_scan_results_count"],
        "activated_count": state["activated_count"],
        "pending_count": state["pending_count"],
        "last_scan_setups": state["last_scan_setups"],
    }


@app.get("/api/status", summary="Screener Status and Live Setups")
async def get_status():
    return {
        "is_running": state["is_running"],
        "timezone": "IST",
        "coins_whitelist": state.get("coins_whitelist", COINS_WHITELIST),
        "ltf_timeframe": state.get("ltf_timeframe", DEFAULT_LTF_TIMEFRAME),
        "htf_mode": state.get("htf_mode", DEFAULT_HTF_MODE),
        "use_close_invalidation": state.get("use_close_invalidation", False),
        "max_htf_retrace_candles": state.get("max_htf_retrace_candles", 18),
        "session_filter_enabled": state.get("session_filter_enabled", False),
        "single_position": trade_tracker.single_active_position,
        "universe_count": state["universe_count"],
        "scan_interval_minutes": SCAN_INTERVAL_MINUTES,
        "total_scans_completed": state["total_scans_completed"],
        "last_scan_time_ist": state["last_scan_time_ist"],
        "last_scan_results_count": state["last_scan_results_count"],
        "activated_count": state["activated_count"],
        "pending_count": state["pending_count"],
        "last_scan_setups": state["last_scan_setups"],
    }


@app.get("/api/config", summary="Get Current Strategy Runtime Config")
@app.post("/api/config", summary="Update Strategy Runtime Config")
async def config_endpoint(
    ltf_timeframe: Optional[str] = Query(default=None),
    htf_mode: Optional[str] = Query(default=None),
    use_close_invalidation: Optional[bool] = Query(default=None),
    max_htf_retrace_candles: Optional[int] = Query(default=None),
    session_filter_enabled: Optional[bool] = Query(default=None),
    single_position: Optional[bool] = Query(default=None),
    coins_whitelist: Optional[str] = Query(default=None),
):
    """Dynamically get or update runtime screener strategy parameters."""
    if ltf_timeframe is not None and ltf_timeframe in ["1m", "5m", "15m", "1h"]:
        state["ltf_timeframe"] = ltf_timeframe
    if htf_mode is not None and htf_mode in ["ANY_VALID", "MOST_RECENT"]:
        state["htf_mode"] = htf_mode
    if use_close_invalidation is not None:
        state["use_close_invalidation"] = bool(use_close_invalidation)
    if max_htf_retrace_candles is not None:
        state["max_htf_retrace_candles"] = max(1, min(100, int(max_htf_retrace_candles)))
    if session_filter_enabled is not None:
        state["session_filter_enabled"] = bool(session_filter_enabled)
    if single_position is not None:
        trade_tracker.single_active_position = bool(single_position)
        state["single_position"] = trade_tracker.single_active_position
    if coins_whitelist is not None:
        state["coins_whitelist"] = coins_whitelist.strip()

    return JSONResponse(
        content={
            "status": "success",
            "config": {
                "ltf_timeframe": state.get("ltf_timeframe", DEFAULT_LTF_TIMEFRAME),
                "htf_mode": state.get("htf_mode", DEFAULT_HTF_MODE),
                "use_close_invalidation": state.get("use_close_invalidation", False),
                "max_htf_retrace_candles": state.get("max_htf_retrace_candles", 18),
                "session_filter_enabled": state.get("session_filter_enabled", False),
                "single_position": trade_tracker.single_active_position,
                "coins_whitelist": state.get("coins_whitelist", COINS_WHITELIST),
            },
        }
    )


@app.post("/scan", summary="Trigger Manual On-Demand Scan")
@app.get("/scan", summary="Trigger Manual On-Demand Scan (GET)")
@app.post("/api/scan", summary="Trigger Manual Scan via API")
async def trigger_scan(
    top_n: Optional[int] = Query(default=TOP_N_ALERTS, ge=1, le=50),
    ltf: str = Query(default="5m", pattern="^(1m|5m|15m|1h)$", description="Lower timeframe to scan (1m, 5m, 15m)"),
    htf_mode: str = Query(default="ANY_VALID", pattern="^(ANY_VALID|MOST_RECENT)$", description="4H FVG Selection Mode"),
    use_close_invalidation: Optional[bool] = Query(default=None, description="Close-based vs Wick-based Invalidation"),
    max_htf_retrace_candles: Optional[int] = Query(default=None, description="4H Retrace Lookback Window"),
    session_filter_enabled: Optional[bool] = Query(default=None, description="Session Filter Enabled"),
    send_alert: bool = Query(default=True, description="Whether to broadcast Telegram alert"),
):
    """Manually triggers an immediate 2-stage screener scan."""
    close_inval = state.get("use_close_invalidation", False) if use_close_invalidation is None else use_close_invalidation
    retrace_win = state.get("max_htf_retrace_candles", 18) if max_htf_retrace_candles is None else max_htf_retrace_candles
    sess_enabled = state.get("session_filter_enabled", False) if session_filter_enabled is None else session_filter_enabled

    logger.info(
        "Manual scan triggered (top_n=%d, LTF=%s, 4H Mode=%s, CloseInval=%s, RetraceWin=%s, SessionFilter=%s, send_alert=%s)",
        top_n,
        ltf,
        htf_mode,
        close_inval,
        retrace_win,
        sess_enabled,
        send_alert,
    )
    top_setups = await run_screener(
        top_n=top_n,
        ltf_timeframe=ltf,
        htf_mode=htf_mode,
        use_close_invalidation=close_inval,
        max_htf_retrace_candles=retrace_win,
        session_filter_enabled=sess_enabled,
    )

    start_time_ist = datetime.now(IST)
    state["ltf_timeframe"] = ltf
    state["htf_mode"] = htf_mode
    state["use_close_invalidation"] = close_inval
    state["max_htf_retrace_candles"] = retrace_win
    state["session_filter_enabled"] = sess_enabled
    state["last_scan_time"] = datetime.now(timezone.utc).isoformat()
    state["last_scan_time_ist"] = start_time_ist.strftime("%d-%b-%Y %I:%M:%S %p IST")
    state["last_scan_results_count"] = len(top_setups)
    state["activated_count"] = len([s for s in top_setups if s.stage == "ACTIVATED"])
    state["pending_count"] = len([s for s in top_setups if s.stage == "PENDING_RETRACE"])
    state["last_scan_setups"] = [s.to_dict() for s in top_setups]

    if send_alert and top_setups:
        candles_map = {}
        for s in top_setups:
            try:
                c_list = await get_last_n_candles(symbol=s.symbol, timeframe=ltf, n=50)
                candles_map[s.symbol] = c_list
            except Exception as exc:
                logger.warning("Failed to fetch LTF candles for chart %s: %s", s.symbol, exc)
        await broadcast_setups_stateful(top_setups, candles_map=candles_map)

    return JSONResponse(
        content={
            "status": "success",
            "ltf_timeframe": ltf,
            "htf_mode": htf_mode,
            "use_close_invalidation": close_inval,
            "max_htf_retrace_candles": retrace_win,
            "session_filter_enabled": sess_enabled,
            "timestamp_ist": state["last_scan_time_ist"],
            "count": len(top_setups),
            "activated_count": state["activated_count"],
            "pending_count": state["pending_count"],
            "setups": state["last_scan_setups"],
        }
    )


from fastapi.responses import HTMLResponse, JSONResponse, Response

@app.get("/api/chart", summary="Dynamic TradingView Setup Chart")
async def get_dynamic_chart(
    symbol: str = Query(default="BTC"),
    direction: str = Query(default="Bullish"),
    ltf: str = Query(default="5m"),
    stage: str = Query(default="ACTIVATED"),
    entry: Optional[float] = Query(default=None),
    sl: Optional[float] = Query(default=None),
    htf_bottom: Optional[float] = Query(default=None),
    htf_top: Optional[float] = Query(default=None),
    ltf_bottom: Optional[float] = Query(default=None),
    ltf_top: Optional[float] = Query(default=None),
    fvg_formed_ts: Optional[int] = Query(default=None),
    entry_ts: Optional[int] = Query(default=None),
    timestamp: Optional[int] = Query(default=None, description="Anchor timestamp for historical backtest setup"),
):
    """Generates and serves a real-time or historical TradingView-style candlestick chart for an asset setup."""
    from chart_generator import generate_setup_chart
    from strategy import Candle, FVG, calculate_tp_levels, compute_fvg, get_last_n_candles
    from hyperliquid_client import hyperliquid_client

    clean_symbol = symbol.strip().upper()
    try:
        interval_ms_map = {
            "1m": 60 * 1000,
            "5m": 5 * 60 * 1000,
            "15m": 15 * 60 * 1000,
            "1h": 60 * 60 * 1000,
            "4h": 4 * 60 * 60 * 1000,
            "1d": 24 * 60 * 60 * 1000,
        }
        interval_ms = interval_ms_map.get(ltf, 5 * 60 * 1000)

        effective_fvg_ts = fvg_formed_ts or timestamp
        effective_entry_ts = entry_ts or timestamp
        anchor_ts = effective_fvg_ts or effective_entry_ts

        candles: List[Candle] = []
        if anchor_ts and anchor_ts > 0:
            # Historical backtest trade: fetch snapshot centered around the trade formation timestamp
            start_ms = anchor_ts - (25 * interval_ms)
            end_ms = anchor_ts + (35 * interval_ms)
            raw_candles = await hyperliquid_client.get_candle_snapshot(clean_symbol, ltf, start_ms, end_ms)
            if not raw_candles or len(raw_candles) < 5:
                raw_candles = await hyperliquid_client.fetch_fallback_historical_klines(clean_symbol, ltf, start_ms, end_ms)

            if raw_candles:
                candles = [
                    Candle(
                        timestamp=int(c.get("t", 0)),
                        open=float(c.get("o", 0)),
                        high=float(c.get("h", 0)),
                        low=float(c.get("l", 0)),
                        close=float(c.get("c", 0)),
                        volume=float(c.get("v", 0)),
                    )
                    for c in raw_candles
                ]

        # If live scan or fallback
        if not candles or len(candles) < 3:
            candles = await get_last_n_candles(symbol=clean_symbol, timeframe=ltf, n=50)

        if not candles or len(candles) < 3:
            # Resilient fallback: synthetic candles around entry/sl for offline testing or network outage
            base_p = entry if (entry and entry > 0) else (sl if (sl and sl > 0) else 100.0)
            now_ms = int(time.time() * 1000)
            candles = [
                Candle(now_ms - (i * interval_ms), base_p * 0.99, base_p * 1.01, base_p * 0.98, base_p, 10.0)
                for i in range(10, 0, -1)
            ]

        curr_price = candles[-1].close
        entry_price = entry if (entry and entry > 0) else curr_price

        # Detect or reconstruct LTF FVG
        ltf_fvg = None
        if ltf_bottom is not None and ltf_top is not None:
            ltf_fvg = FVG(
                direction=direction,
                top=max(ltf_bottom, ltf_top),
                bottom=min(ltf_bottom, ltf_top),
                c1=candles[0],
                c2=candles[1],
                c3=candles[2],
                formed_at=effective_fvg_ts or candles[-1].timestamp,
            )
        else:
            ltf_fvg = compute_fvg(candles, direction=direction)

        # Detect or reconstruct 4H FVG
        htf_fvg = None
        if htf_bottom is not None and htf_top is not None:
            htf_fvg = FVG(
                direction=direction,
                top=max(htf_bottom, htf_top),
                bottom=min(htf_bottom, htf_top),
                c1=candles[0],
                c2=candles[1],
                c3=candles[2],
                formed_at=effective_fvg_ts or candles[-1].timestamp,
            )
        else:
            candles_4h = await get_last_n_candles(symbol=clean_symbol, timeframe="4h", n=20)
            htf_fvg = compute_fvg(candles_4h, direction=direction)

        # Stop loss & TP levels
        if sl is not None and sl > 0:
            sl_price = sl
        else:
            if direction == "Bullish":
                sl_price = min([c.low for c in candles[-5:]]) * 0.998
            else:
                sl_price = max([c.high for c in candles[-5:]]) * 1.002

        tp_levels = calculate_tp_levels(direction=direction, entry_price=entry_price, sl_price=sl_price)

        img_bytes = generate_setup_chart(
            symbol=clean_symbol,
            direction=direction,
            candles_ltf=candles,
            htf_fvg=htf_fvg,
            ltf_fvg=ltf_fvg,
            entry_price=entry_price,
            sl_price=sl_price,
            tp_levels=tp_levels,
            stage=stage,
            ltf_timeframe=ltf,
            entry_time_ms=effective_entry_ts,
            fvg_formed_time_ms=effective_fvg_ts,
        )

        return Response(content=img_bytes, media_type="image/png")
    except Exception as exc:
        logger.error("Error generating dynamic chart for %s: %s", clean_symbol, exc)
        return Response(status_code=500, content=b"Error rendering chart.")


@app.post("/api/test-telegram", summary="Test Telegram Alert Dispatch")
async def test_telegram():
    from telegram_client import send_telegram_photo
    from chart_generator import generate_setup_chart
    from strategy import Candle, FVG, calculate_tp_levels, get_last_n_candles

    now_ist = datetime.now(IST).strftime("%d-%b-%Y %I:%M:%S %p IST")
    
    # Fetch REAL live market candles for BTC
    candles = await get_last_n_candles(symbol="BTC", timeframe="5m", n=50)
    if not candles:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to fetch live BTC candles"})

    curr = candles[-1].close
    direction = "Bullish"
    entry = curr
    sl = min([c.low for c in candles[-10:]])
    if sl >= entry:
        sl = entry * 0.995
    tp = calculate_tp_levels(direction, entry, sl)

    # Reconstruct zones
    htf_fvg = FVG("Bullish", top=curr * 1.004, bottom=curr * 0.996, c1=candles[-3], c2=candles[-2], c3=candles[-1], formed_at=candles[-1].timestamp)
    ltf_fvg = FVG("Bullish", top=curr * 1.001, bottom=curr * 0.998, c1=candles[-3], c2=candles[-2], c3=candles[-1], formed_at=candles[-1].timestamp)

    chart_bytes = generate_setup_chart(
        symbol="BTC",
        direction="Bullish",
        candles_ltf=candles,
        htf_fvg=htf_fvg,
        ltf_fvg=ltf_fvg,
        entry_price=entry,
        sl_price=sl,
        tp_levels=tp,
        stage="ACTIVATED",
        ltf_timeframe="5m",
    )

    test_caption = (
        "🚀 🟢 <b>BTC-PERP — TRADE ACTIVATED!</b>\n"
        "<b>Direction:</b> Bullish (5m Retrace Entry)\n"
        f"<b>Entry Price:</b> ${entry:,.2f}\n"
        f"<b>Stop Loss:</b> ≤ ${sl:,.2f} (Risk: {tp.sl_points:,.2f} pts / {tp.risk_pct:.2f}%)\n"
        f"<b>4H FVG:</b> ${htf_fvg.bottom:,.2f} – ${htf_fvg.top:,.2f}\n"
        f"<b>5m FVG:</b> ${ltf_fvg.bottom:,.2f} – ${ltf_fvg.top:,.2f}\n\n"
        "<b>Take Profit Targets:</b>\n"
        f"  🎯 <b>1.0R:</b> ${tp.r1:,.2f} (+{tp.r1_points:,.2f} pts)\n"
        f"  🎯 <b>1.5R:</b> ${tp.r1_5:,.2f} (+{tp.r1_5_points:,.2f} pts)\n"
        f"  🎯 <b>2.0R:</b> ${tp.r2:,.2f} (+{tp.r2_points:,.2f} pts)\n"
        f"  🎯 <b>3.0R:</b> ${tp.r3:,.2f} (+{tp.r3_points:,.2f} pts)\n\n"
        f"<i>Live real-market test alert at {now_ist}</i>"
    )

    success = False
    if chart_bytes and len(chart_bytes) > 0:
        success = await send_telegram_photo(chart_bytes, test_caption)
    else:
        success = await send_telegram_alert(test_caption)

    if success:
        return {"status": "success", "message": "Test chart alert with real candles sent successfully to Telegram."}
    else:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "Failed to send alert. Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env",
            },
        )


@app.post("/api/backtest", summary="Run Historical Backtest")
@app.get("/api/backtest", summary="Run Historical Backtest (GET)")
async def backtest_endpoint(
    symbol: str = Query(default="BTC", description="Coin symbol to backtest"),
    days: int = Query(default=14, ge=1, le=180, description="Lookback days (1 to 180)"),
    start_date: Optional[str] = Query(default=None, description="Start date in YYYY-MM-DD format (IST)"),
    end_date: Optional[str] = Query(default=None, description="End date in YYYY-MM-DD format (IST)"),
    target_rr: float = Query(default=2.0, ge=0.5, le=10.0, description="Risk-to-Reward ratio target"),
    ltf: str = Query(default="5m", pattern="^(1m|5m|15m|1h)$", description="Lower timeframe for entry (1m, 5m, 15m)"),
    htf_mode: str = Query(default="ANY_VALID", pattern="^(ANY_VALID|MOST_RECENT)$", description="4H FVG Selection Mode"),
    single_position: bool = Query(default=True, description="Single active position mode (one trade at a time until exit)"),
    use_close_invalidation: bool = Query(default=False, description="Whether 4H FVG invalidation requires candle close"),
    max_htf_retrace_candles: int = Query(default=18, ge=1, le=100, description="4H Retrace Lookback Window in 4H candles"),
    min_candle_gap: Optional[int] = Query(default=None, description="Custom candle gap cooldown between trades"),
):
    from backtest import run_historical_backtest

    clean_symbol = symbol.strip().upper()
    logger.info(
        "Historical backtest triggered for %s (days=%d, start_date=%s, end_date=%s, target_rr=%.1f, LTF=%s, 4H Mode=%s, single_pos=%s, close_inval=%s, retrace_win=%d, gap=%s)",
        clean_symbol,
        days,
        start_date,
        end_date,
        target_rr,
        ltf,
        htf_mode,
        single_position,
        use_close_invalidation,
        max_htf_retrace_candles,
        min_candle_gap,
    )

    summary = await run_historical_backtest(
        symbol=clean_symbol,
        days=days,
        start_date=start_date,
        end_date=end_date,
        target_rr=target_rr,
        ltf_timeframe=ltf,
        htf_mode=htf_mode,
        single_position=single_position,
        use_close_invalidation=use_close_invalidation,
        max_htf_retrace_candles=max_htf_retrace_candles,
        min_candle_gap=min_candle_gap,
    )

    return JSONResponse(content={"status": "success", "data": summary.to_dict()})


# ==============================================================================
# EXTREME LTF STRATEGY ENDPOINTS (STEP 5)
# ==============================================================================
@app.get("/api/extreme/scan", summary="Scan for Extreme LTF FVG Setups")
async def api_extreme_scan(
    symbols: Optional[str] = Query(default=None, description="Comma-separated symbols or leave empty for whitelist"),
    ltf: str = Query(default="15m", pattern="^(1m|5m|15m|1h)$", description="LTF timeframe"),
    invalidation: str = Query(default="wick", pattern="^(wick|close)$", description="Invalidation mode"),
    min_gap_pct: float = Query(default=0.05, ge=0.0, description="Minimum LTF FVG gap size %"),
    target: str = Query(default="2R", pattern="^(1R|2R|3R)$", description="Completion target"),
):
    from strategy_extreme_fvg import get_extreme_setup_for_symbol
    from hyperliquid_client import SYMBOL_ALIASES

    whitelist_raw = symbols or os.getenv("COINS_WHITELIST", "BTC,ETH,SOL,PAXG")
    coin_list = [c.strip().upper() for c in whitelist_raw.split(",") if c.strip()]
    use_close = (invalidation == "close")

    setups_out = []
    mids = await hyperliquid_client.get_all_mids()

    for sym in coin_list:
        try:
            raw_sym = SYMBOL_ALIASES.get(sym, sym)
            setup = await get_extreme_setup_for_symbol(
                symbol=raw_sym,
                ltf_timeframe=ltf,
                use_close_invalidation=use_close,
                min_gap_pct=min_gap_pct,
                completion_target=target,
            )
            if setup:
                curr_px = float(mids.get(raw_sym, setup.entry_price))
                dist_pct = ((curr_px - setup.entry_price) / setup.entry_price) * 100
                setups_out.append({
                    "symbol": sym,
                    "direction": setup.direction,
                    "state": setup.state,
                    "entry_price": setup.entry_price,
                    "current_price": curr_px,
                    "dist_pct": round(dist_pct, 2),
                    "stop_loss": setup.stop_loss,
                    "risk_r": round(setup.risk_r, 4),
                    "risk_pct": round(setup.risk_pct, 2),
                    "tp_1r": round(setup.tp_1r, 4),
                    "tp_2r": round(setup.tp_2r, 4),
                    "tp_3r": round(setup.tp_3r, 4),
                    "floating_r": round(setup.floating_r, 2),
                    "entry_time_ist": setup.entry_time_ist,
                    "completion_target": setup.completion_target,
                    "ltf_timeframe": setup.ltf_timeframe,
                    "anchor": {
                        "direction": setup.anchor.fvg.direction,
                        "bottom": setup.anchor.fvg.bottom,
                        "top": setup.anchor.fvg.top,
                        "formed_time_ist": setup.anchor.fvg.formed_time_ist,
                        "first_touch_time_ist": setup.anchor.first_touch_time_ist,
                        "most_recent_touch_time_ist": setup.anchor.most_recent_touch_time_ist,
                    },
                    "target_fvg": {
                        "direction": setup.ltf_fvg.direction,
                        "bottom": setup.ltf_fvg.bottom,
                        "top": setup.ltf_fvg.top,
                        "width": setup.ltf_fvg.width,
                        "gap_pct": round(setup.ltf_fvg.gap_pct, 3),
                        "formed_time_ist": setup.ltf_fvg.formed_time_ist,
                    },
                    "unmitigated_count": len(setup.all_unmitigated_fvgs),
                })
        except Exception as exc:
            logger.warning("Failed to get extreme setup for %s: %s", sym, exc)

    return JSONResponse(content={"status": "success", "count": len(setups_out), "setups": setups_out})


@app.get("/api/extreme/backtest", summary="Run Historical Backtest on Extreme Strategy")
async def api_extreme_backtest(
    symbol: str = Query(default="BTC", description="Coin symbol"),
    days: int = Query(default=14, ge=1, le=90, description="Lookback days"),
    ltf: str = Query(default="15m", pattern="^(1m|5m|15m|1h)$", description="LTF timeframe"),
    invalidation: str = Query(default="wick", pattern="^(wick|close)$", description="Invalidation mode"),
    min_gap_pct: float = Query(default=0.05, ge=0.0, description="Min gap size %"),
):
    from backtest_extreme_fvg import run_extreme_backtest

    use_close = (invalidation == "close")
    report = await run_extreme_backtest(
        symbol=symbol.strip().upper(),
        days=days,
        ltf_timeframe=ltf,
        use_close_invalidation=use_close,
        min_gap_pct=min_gap_pct,
    )
    return JSONResponse(content={
        "status": "success",
        "symbol": report.symbol,
        "days": report.days,
        "ltf_timeframe": report.ltf_timeframe,
        "invalidation_mode": report.invalidation_mode,
        "min_gap_pct": report.min_gap_pct,
        "total_trades": report.total_trades,
        "wins_1r": report.wins_1r,
        "wins_2r": report.wins_2r,
        "wins_3r": report.wins_3r,
        "losses": report.losses,
        "win_rate_1r": round(report.win_rate_1r, 1),
        "win_rate_2r": round(report.win_rate_2r, 1),
        "win_rate_3r": round(report.win_rate_3r, 1),
        "net_pnl_1r": round(report.net_pnl_1r, 1),
        "net_pnl_2r": round(report.net_pnl_2r, 1),
        "net_pnl_3r": round(report.net_pnl_3r, 1),
        "profit_factor_1r": round(report.profit_factor_1r, 2) if report.profit_factor_1r != float("inf") else 999.0,
        "profit_factor_2r": round(report.profit_factor_2r, 2) if report.profit_factor_2r != float("inf") else 999.0,
        "profit_factor_3r": round(report.profit_factor_3r, 2) if report.profit_factor_3r != float("inf") else 999.0,
        "max_drawdown_r": round(report.max_drawdown_r, 1),
        "avg_trade_duration_min": round(report.avg_trade_duration_min, 1),
        "avg_mfe_r": round(report.avg_mfe_r, 2),
        "trades": [t.to_dict() for t in report.trades],
    })


@app.get("/api/extreme/status", summary="Get Extreme Background Daemon Status and Live Setups")
async def api_extreme_status():
    return JSONResponse(content={
        "status": "success",
        "is_running": state.get("extreme_is_running", False),
        "interval_seconds": state.get("extreme_interval_seconds", 30),
        "ltf_timeframe": state.get("extreme_ltf", "15m"),
        "completion_target": state.get("extreme_target", "2R"),
        "min_gap_pct": state.get("extreme_min_gap", 0.05),
        "last_scan_time_ist": state.get("extreme_last_scan_time_ist"),
        "active_count": state.get("extreme_active_count", 0),
        "pending_count": state.get("extreme_pending_count", 0),
        "total_cycles": state.get("extreme_total_cycles", 0),
        "setups": state.get("extreme_setups", []),
    })


@app.post("/api/extreme/toggle-daemon", summary="Start or Pause Extreme Background Daemon")
async def api_extreme_toggle_daemon(
    enable: Optional[bool] = Query(default=None),
    interval_seconds: Optional[int] = Query(default=None, ge=5, le=3600, description="Optional new interval in seconds"),
):
    if interval_seconds is not None:
        state["extreme_interval_seconds"] = interval_seconds
        logger.info("Updated Extreme Daemon interval to %d seconds", interval_seconds)

    if enable is None:
        state["extreme_is_running"] = not state.get("extreme_is_running", True)
    else:
        state["extreme_is_running"] = enable

    if state["extreme_is_running"] and (state.get("extreme_background_task") is None or state["extreme_background_task"].done()):
        state["extreme_background_task"] = asyncio.create_task(extreme_screener_background_worker())

    status_str = "RUNNING" if state["extreme_is_running"] else "STOPPED"
    return JSONResponse(content={
        "status": "success",
        "is_running": state["extreme_is_running"],
        "interval_seconds": state["extreme_interval_seconds"],
        "message": f"Extreme Daemon is now {status_str} (Interval: {state['extreme_interval_seconds']}s)",
    })


@app.post("/api/extreme/config", summary="Update Extreme Daemon Runtime Configuration")
async def api_extreme_config(
    interval_seconds: Optional[int] = Query(default=None, ge=5, le=3600, description="Daemon interval in seconds"),
    ltf: Optional[str] = Query(default=None, pattern="^(1m|5m|15m|1h)$", description="LTF timeframe"),
    target: Optional[str] = Query(default=None, pattern="^(1R|2R|3R)$", description="Completion target"),
    min_gap_pct: Optional[float] = Query(default=None, ge=0.0, description="Min gap size %"),
    invalidation: Optional[str] = Query(default=None, pattern="^(wick|close)$", description="Invalidation mode"),
    symbols: Optional[str] = Query(default=None, description="Comma-separated symbols"),
):
    if interval_seconds is not None:
        state["extreme_interval_seconds"] = interval_seconds
        logger.info("Updated Extreme Daemon interval to %d seconds", interval_seconds)
    if ltf is not None:
        state["extreme_ltf"] = ltf
    if target is not None:
        state["extreme_target"] = target
    if min_gap_pct is not None:
        state["extreme_min_gap"] = min_gap_pct
    if invalidation is not None:
        state["extreme_use_close"] = (invalidation == "close")
    if symbols is not None and symbols.strip():
        state["coins_whitelist"] = symbols.strip().upper()

    return JSONResponse(content={
        "status": "success",
        "message": "Extreme Daemon configuration updated successfully",
        "config": {
            "interval_seconds": state["extreme_interval_seconds"],
            "ltf_timeframe": state["extreme_ltf"],
            "completion_target": state["extreme_target"],
            "min_gap_pct": state["extreme_min_gap"],
            "use_close_invalidation": state["extreme_use_close"],
            "coins_whitelist": state["coins_whitelist"],
        },
    })


@app.get("/api/extreme/chart", summary="Generate TradingView-Style Chart for Extreme Setup")
async def api_extreme_chart(
    symbol: str = Query(..., description="Symbol e.g. BTC"),
    direction: str = Query(default="Bullish", description="Direction"),
    ltf: str = Query(default="15m", description="LTF Timeframe"),
    entry_price: float = Query(...),
    stop_loss: float = Query(...),
    tp_1r: float = Query(...),
    tp_2r: float = Query(...),
    tp_3r: float = Query(...),
    htf_bottom: float = Query(...),
    htf_top: float = Query(...),
    ltf_bottom: float = Query(...),
    ltf_top: float = Query(...),
    ltf_formed_ts: Optional[int] = Query(default=0),
    htf_first_touch_ist: Optional[str] = Query(default=None),
    state: str = Query(default="PENDING_RETRACE"),
    floating_r: float = Query(default=0.0),
    entry_ts: Optional[int] = Query(default=None),
    exit_ts: Optional[int] = Query(default=None),
):
    from chart_generator import generate_extreme_setup_chart
    from hyperliquid_client import SYMBOL_ALIASES, hl_client
    from strategy import Candle
    import time

    raw_sym = SYMBOL_ALIASES.get(symbol.strip().upper(), symbol.strip().upper())
    c_dur = 15 * 60 * 1000 if ltf == "15m" else (5 * 60 * 1000 if ltf == "5m" else (60 * 60 * 1000 if ltf == "1h" else 60 * 1000))
    is_historical = str(state).startswith("HISTORICAL_") or (exit_ts is not None and exit_ts > 0)

    candles = []
    if is_historical and entry_ts and entry_ts > 0:
        # For historical trades: only fetch from entry to exit time + delta on both sides (and LTF FVG formation if within 25 bars)
        t_exit = exit_ts if (exit_ts and exit_ts > entry_ts) else (entry_ts + 6 * c_dur)
        t_end = t_exit + 8 * c_dur

        if ltf_formed_ts and 0 < (entry_ts - ltf_formed_ts) <= 25 * c_dur:
            t_start = ltf_formed_ts - 5 * c_dur
        else:
            t_start = entry_ts - 8 * c_dur

        try:
            raw_candles = await hl_client.get_candle_snapshot(
                coin=raw_sym,
                interval=ltf,
                start_time_ms=t_start,
                end_time_ms=t_end,
            )
            if raw_candles:
                candles = [Candle.from_dict(c) for c in raw_candles]
        except Exception as exc:
            logger.debug("Historical snapshot fetch error for %s: %s", raw_sym, exc)

    if not candles:
        candles = await get_last_n_candles(symbol=raw_sym, timeframe=ltf, n=60)

    if not candles:
        now_ts = int(time.time() * 1000)
        mid_val = (entry_price + stop_loss) / 2
        candles = [
            Candle(
                timestamp=now_ts - (50 - i) * c_dur,
                open=mid_val,
                high=max(entry_price, tp_3r, htf_top),
                low=min(entry_price, stop_loss, htf_bottom),
                close=entry_price,
                volume=100.0,
            )
            for i in range(50)
        ]
    img_bytes = generate_extreme_setup_chart(
        symbol=symbol.strip().upper(),
        direction=direction,
        candles_ltf=candles,
        htf_fvg_bottom=htf_bottom,
        htf_fvg_top=htf_top,
        htf_first_touch_ist=htf_first_touch_ist,
        ltf_fvg_bottom=ltf_bottom,
        ltf_fvg_top=ltf_top,
        ltf_fvg_formed_ts=ltf_formed_ts or 0,
        entry_price=entry_price,
        stop_loss=stop_loss,
        tp_1r=tp_1r,
        tp_2r=tp_2r,
        tp_3r=tp_3r,
        state=state,
        floating_r=floating_r,
        ltf_timeframe=ltf,
        entry_time_ts=entry_ts,
        exit_time_ts=exit_ts,
    )
    if not img_bytes:
        raise HTTPException(status_code=500, detail="Failed to generate chart image")
    return Response(content=img_bytes, media_type="image/png")


@app.get("/api/extreme/live-history", summary="Get Tracked Live Trade History for Extreme Strategy")
async def api_extreme_live_history():
    from extreme_trade_tracker import extreme_trade_tracker
    return JSONResponse(content={
        "status": "success",
        "summary": extreme_trade_tracker.get_summary(),
        "active_trades": [t.to_dict() for t in extreme_trade_tracker.active_trades.values()],
        "history": [t.to_dict() for t in extreme_trade_tracker.history],
    })


@app.post("/api/extreme/clear-live-history", summary="Clear Closed Live Trade History")
async def api_extreme_clear_live_history():
    from extreme_trade_tracker import extreme_trade_tracker
    extreme_trade_tracker.clear_history()
    return JSONResponse(content={"status": "success", "message": "Live trade history cleared successfully"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
