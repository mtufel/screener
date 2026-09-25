"""
Background screener cycle: payload builders, alert dispatch, dashboard broadcast,
the Extreme scan-cycle orchestrator, its daemon loop, and the FastAPI lifespan.

PATCH-SURFACE CONTRACT (why the indirection exists):
qa_harness.core.install_patches, qa_live_sim, and several pytest suites patch the
five services below as attributes of the `main` module (e.g. `main.redis_client = sink`,
`patch("main.send_extreme_telegram_alert", ...)`). Call sites in THIS module therefore
read them as bare names bound into `_patchable_services` (which mirrors them onto
`main`), so a patch on `main` is visible here at call time. Do not "clean up" these
bare-name reads into direct imports.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from app_config import (
    COINS_WHITELIST,
    ENABLE_STRATEGY_2,
    EXTREME_ACTIVE_STRATEGY,
    EXTREME_COMPLETION_TARGET,
    EXTREME_ENTRY_SESSIONS,
    EXTREME_ENTRY_SESSION_FILTER_ENABLED,
    EXTREME_ENTRY_WEEKDAY_FILTER_ENABLED,
    EXTREME_LTF_TIMEFRAME,
    EXTREME_MIN_GAP_PCT,
    EXTREME_SCAN_INTERVAL_SECONDS,
    EXTREME_SESSIONS,
    EXTREME_SESSION_FILTER_ENABLED,
    EXTREME_USE_CLOSE_INVALIDATION,
    EXTREME_WEEKDAY_FILTER_ENABLED,
    IST,
    logger,
    state,
)
from hyperliquid_client import hyperliquid_client, lookup_mid
from market_data_provider import close_all_providers, get_market_data_provider, market_data_provider
from redis_client import redis_client
from session_filter import SessionFilterConfig
from strategy_extreme_fvg import get_last_n_candles
from telegram_client import send_telegram_alert

# ------------------------------------------------------------------------------
# Patchable service bindings (see module docstring). Every call site below reads
# the five services through the `main` module namespace at CALL time, so both
# patch styles keep working: `main.redis_client = sink` (qa_harness) and
# `patch("main.send_extreme_telegram_alert", ...)` (pytest suites).
# ------------------------------------------------------------------------------
dashboard_ws_manager: Any = None  # set by dashboard_ws._register_singleton_in_main()


def _svc():
    """Returns the `main` facade module for late-bound, patchable service reads."""
    import main

    return main


async def send_extreme_telegram_alert(
    message: str,
    image_bytes: Optional[bytes] = None,
    reply_to_message_id: Optional[int] = None,
    return_message_id: bool = False,
    chat_id: Optional[str] = None,
    message_thread_id: Optional[int] = None,
) -> Union[bool, Tuple[bool, Optional[int]]]:
    """Dispatches HTML alert (with optional high-res chart photo) to configured Telegram chat with retries and optional threading."""
    from telegram_client import send_telegram_alert, send_telegram_photo
    if image_bytes:
        return await send_telegram_photo(
            photo_bytes=image_bytes,
            caption=message,
            chat_id=chat_id,
            reply_to_message_id=reply_to_message_id,
            return_message_id=return_message_id,
            message_thread_id=message_thread_id,
        )
    return await send_telegram_alert(
        text=message,
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
        return_message_id=return_message_id,
        message_thread_id=message_thread_id,
    )


# ------------------------------------------------------------------------------
# Screener cycle helpers (single-responsibility units extracted from
# execute_extreme_screener_cycle so the cycle reads as an orchestrator).
# NOTE: helpers intentionally read patched module globals (send_extreme_telegram_alert,
# redis_client, dashboard_ws_manager) by bare name so QA monkeypatching keeps working.
# ------------------------------------------------------------------------------

def _runtime_extreme_config() -> Dict[str, Any]:
    """Snapshots the runtime daemon config from `state` into one dict.

    Keys: active_strategy, coin_list, ltf, target, min_gap, use_close, sess_filter, wkday_filter,
    entry_sess_filter, entry_wkday_filter, sessions_str, entry_sessions_str,
    session_config.
    """
    sessions_str = state.get("extreme_sessions", EXTREME_SESSIONS)
    entry_sessions_str = state.get("extreme_entry_sessions", EXTREME_ENTRY_SESSIONS)
    sess_filter = state.get("extreme_session_filter", EXTREME_SESSION_FILTER_ENABLED)
    wkday_filter = state.get("extreme_weekday_filter", EXTREME_WEEKDAY_FILTER_ENABLED)
    entry_sess_filter = state.get("extreme_entry_session_filter", EXTREME_ENTRY_SESSION_FILTER_ENABLED)
    entry_wkday_filter = state.get("extreme_entry_weekday_filter", EXTREME_ENTRY_WEEKDAY_FILTER_ENABLED)
    return {
        "active_strategy": state.get("extreme_active_strategy", EXTREME_ACTIVE_STRATEGY),
        "coin_list": [c.strip().upper() for c in state.get("coins_whitelist", COINS_WHITELIST).strip().split(",") if c.strip()],
        "ltf": state.get("extreme_ltf", EXTREME_LTF_TIMEFRAME),
        "target": state.get("extreme_target", EXTREME_COMPLETION_TARGET),
        "min_gap": state.get("extreme_min_gap", EXTREME_MIN_GAP_PCT),
        "use_close": state.get("extreme_use_close", EXTREME_USE_CLOSE_INVALIDATION),
        "sess_filter": sess_filter,
        "wkday_filter": wkday_filter,
        "entry_sess_filter": entry_sess_filter,
        "entry_wkday_filter": entry_wkday_filter,
        "sessions_str": sessions_str,
        "entry_sessions_str": entry_sessions_str,
        "session_config": SessionFilterConfig.from_legacy(
            session_filter=sess_filter,
            weekday_filter=wkday_filter,
            entry_session_filter=entry_sess_filter,
            entry_weekday_filter=entry_wkday_filter,
            sessions=sessions_str,
            entry_sessions=entry_sessions_str,
        ),
    }


def _active_trade_setup_payload(sym: str, trade: Any, curr_px: float) -> Dict[str, Any]:
    """Dashboard/scan payload for an open ledger trade (entry/SL/targets LOCKED IMMUTABLE)."""
    risk_r = trade.risk_r if trade.risk_r > 0 else (trade.entry_price * 0.001)
    if curr_px == 0.0:
        curr_px = trade.entry_price
    if trade.direction == "Bullish":
        floating_r = (curr_px - trade.entry_price) / risk_r
    else:
        floating_r = (trade.entry_price - curr_px) / risk_r
    dist_pct = ((curr_px - trade.entry_price) / trade.entry_price) * 100
    return {
        "symbol": sym,
        "direction": trade.direction,
        "state": "TRADE_ACTIVE",
        "entry_price": trade.entry_price,
        "current_price": curr_px,
        "dist_pct": round(dist_pct, 2),
        "stop_loss": trade.stop_loss,
        "risk_r": round(trade.risk_r, 4),
        "risk_pct": round(trade.risk_pct, 2),
        "tp_1r": round(trade.tp_1r, 4),
        "tp_2r": round(trade.tp_2r, 4),
        "tp_3r": round(trade.tp_3r, 4),
        "floating_r": round(floating_r, 2),
        "entry_time_ist": trade.entry_filled_at_ist,
        "entry_timestamp": trade.entry_timestamp,
        "completion_target": trade.completion_target,
        "ltf_timeframe": trade.ltf_timeframe,
        "strategy": getattr(trade, "strategy", "extreme_fvg"),
        "strategy_params": dict(getattr(trade, "strategy_params", {}) or {}),
        "anchor": trade.htf_anchor,
        "target_fvg": trade.ltf_fvg,
        "unmitigated_count": 1,
    }


def _extreme_anchor_payload(anchor: Any) -> Dict[str, Any]:
    """HTF anchor block shared by both payload builders.

    Accepts either an ``ExtremeAnchor`` dataclass (Strategy 2) or a plain
    ``Dict`` (Strategy 3 ``VideoFVGSetup.anchor``) and returns a uniform,
    dashboard/ledger-friendly block in both cases.
    """
    if isinstance(anchor, dict):
        if "direction" in anchor:
            # Strategy 3 engine dict: normalize into the shared shape.
            return {
                "direction": anchor.get("direction"),
                "bottom": anchor.get("bottom"),
                "top": anchor.get("top"),
                "formed_time_ist": _ms_to_ist_str(anchor.get("formed_at")),
                "confirm_time_ist": _ms_to_ist_str(anchor.get("confirm_ts")),
            }
        return dict(anchor)
    return {
        "direction": anchor.fvg.direction,
        "bottom": anchor.fvg.bottom,
        "top": anchor.fvg.top,
        "formed_time_ist": anchor.fvg.formed_time_ist,
        "first_touch_time_ist": anchor.first_touch_time_ist,
        "most_recent_touch_time_ist": anchor.most_recent_touch_time_ist,
    }


def _extreme_target_fvg_payload(ltf_fvg: Any) -> Dict[str, Any]:
    """Target-FVG block shared by both payload builders.

    Accepts either an Extreme LTF FVG dataclass (Strategy 2) or a plain
    ``Dict`` (Strategy 3 ``VideoFVGSetup.ltf_fvg``) and returns a uniform,
    dashboard/ledger-friendly block in both cases.
    """
    if isinstance(ltf_fvg, dict):
        if "direction" in ltf_fvg:
            width = (ltf_fvg.get("top") or 0) - (ltf_fvg.get("bottom") or 0)
            mid = ((ltf_fvg.get("top") or 0) + (ltf_fvg.get("bottom") or 0)) / 2.0
            gap_pct = ((width / mid) * 100.0) if mid > 0 else 0.0
            return {
                "direction": ltf_fvg.get("direction"),
                "bottom": ltf_fvg.get("bottom"),
                "top": ltf_fvg.get("top"),
                "width": width,
                "gap_pct": round(gap_pct, 3),
                "formed_time_ist": _ms_to_ist_str(ltf_fvg.get("formed_at")),
                "formed_at": ltf_fvg.get("formed_at"),
            }
        return dict(ltf_fvg)
    return {
        "direction": ltf_fvg.direction,
        "bottom": ltf_fvg.bottom,
        "top": ltf_fvg.top,
        "width": ltf_fvg.width,
        "gap_pct": round(ltf_fvg.gap_pct, 3),
        "formed_time_ist": ltf_fvg.formed_time_ist,
        "formed_at": ltf_fvg.formed_at,
    }


def _ms_to_ist_str(ts_ms: Optional[int]) -> Optional[str]:
    """Format a millisecond epoch timestamp as an IST string (or None)."""
    if not ts_ms:
        return None
    try:
        from datetime import timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        return datetime.fromtimestamp(ts_ms / 1000.0, tz=ist).strftime("%d-%b %I:%M %p IST")
    except (TypeError, ValueError, OSError):
        return None


def _extreme_setup_payload(
    sym: str,
    setup: Any,
    curr_px: float,
    strategy_name: Optional[str] = None,
    strategy_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Dashboard/scan payload for a freshly scanned setup (pending retrace or already-active)."""
    dist_pct = ((curr_px - setup.entry_price) / setup.entry_price) * 100
    return {
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
        "entry_timestamp": setup.entry_timestamp,
        "completion_target": setup.completion_target,
        "ltf_timeframe": setup.ltf_timeframe,
        "strategy": strategy_name or "extreme_fvg",
        "strategy_params": dict(strategy_params or {}),
        "anchor": _extreme_anchor_payload(setup.anchor) if getattr(setup, "anchor", None) else {},
        "target_fvg": _extreme_target_fvg_payload(setup.ltf_fvg) if getattr(setup, "ltf_fvg", None) else {},
        "unmitigated_count": len(getattr(setup, "all_unmitigated_fvgs", []) or []),
    }


async def _fetch_recent_candles_map(provider: Any, ltf: str, symbols: List[str], tracker: Any) -> Dict[str, List[Any]]:
    """Fetches LTF candles for whitelisted + ledger symbols; sizes history to cover open trades."""
    import main

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    dur_ms = 5 * 60 * 1000 if ltf == "5m" else (15 * 60 * 1000 if ltf == "15m" else (60 * 60 * 1000 if ltf == "1h" else 60 * 1000))
    symbols_to_fetch = set(symbols) | {t.symbol.strip().upper() for t in tracker.active_trades.values()}

    candles_map: Dict[str, List[Any]] = {}
    for sym in symbols_to_fetch:
        try:
            trade = tracker.get_active_trade_for_symbol(sym) or tracker.get_pending_trade_for_symbol(sym)
            n_candles = 50
            if trade:
                earliest_ts = trade.entry_timestamp or trade.ltf_fvg.get("formed_at") or 0
                if earliest_ts > 0 and dur_ms > 0:
                    n_candles = max(50, min(500, int((now_ms - earliest_ts) / dur_ms) + 10))
            candles_map[sym] = await main.get_last_n_candles(symbol=provider.resolve_symbol(sym), timeframe=ltf, n=n_candles, client=provider)
            await asyncio.sleep(0.1)
        except Exception as exc:
            logger.debug("Failed to fetch recent candles for %s: %s", sym, exc)
    return candles_map


def _position_size_snippet(trade: Any) -> str:
    """Renders the position-sizing alert line ('' when sizing is disabled or inputs are invalid)."""
    try:
        from position_sizing import PositionSizingEngine
        res = PositionSizingEngine.calculate(entry_price=trade.entry_price, stop_loss=trade.stop_loss, symbol=trade.symbol)
        if res:
            return f"\n• <b>Position Size:</b> <code>{res.quantity_formatted}</code> (${res.notional_usd:,.2f} Notional @ ${res.risk_usd:.2f} Risk)"
    except Exception:
        pass
    return ""


async def _generate_event_chart(evt_type: str, trade: Any, candles_map: Dict[str, List[Any]], provider: Any) -> Optional[bytes]:
    """Renders the alert chart for a trade event; returns None when candle data or generation fails."""
    import main

    from chart_generator import generate_extreme_setup_chart
    raw_sym = provider.resolve_symbol(trade.symbol)
    try:
        candles_ltf = candles_map.get(trade.symbol) or candles_map.get(raw_sym)
        if not candles_ltf:
            candles_ltf = await main.get_last_n_candles(symbol=raw_sym, timeframe=trade.ltf_timeframe, n=60, client=provider)
        return generate_extreme_setup_chart(
            symbol=trade.symbol,
            direction=trade.direction,
            candles_ltf=candles_ltf,
            htf_fvg_bottom=trade.htf_anchor.get("bottom", 0.0),
            htf_fvg_top=trade.htf_anchor.get("top", 0.0),
            htf_first_touch_ist=trade.htf_anchor.get("first_touch_time_ist"),
            ltf_fvg_bottom=trade.ltf_fvg.get("bottom", 0.0),
            ltf_fvg_top=trade.ltf_fvg.get("top", 0.0),
            ltf_fvg_formed_ts=trade.ltf_fvg.get("formed_at", 0),
            entry_price=trade.entry_price,
            stop_loss=trade.stop_loss,
            tp_1r=trade.tp_1r,
            tp_2r=trade.tp_2r,
            tp_3r=trade.tp_3r,
            state=trade.state,
            floating_r=trade.floating_r,
            ltf_timeframe=trade.ltf_timeframe,
        )
    except Exception as exc:
        logger.debug("Chart generation failed for event %s: %s", evt_type, exc)
        return None


def _trade_alert_message(evt_type: str, trade: Any, side: str, mids: Dict[str, float]) -> str:
    """Builds the Telegram HTML body for a trade event ('' for events with no alert, e.g. SETUP_INVALIDATED)."""
    pos_str = _position_size_snippet(trade)

    if evt_type == "NEW_SETUP" and trade.state == "PENDING_RETRACE":
        dist = ((lookup_mid(mids, trade.symbol, trade.entry_price) - trade.entry_price) / trade.entry_price) * 100
        return (
            f"🔔 <b>[NEW SETUP] {trade.symbol} {side} ({trade.ltf_timeframe})</b>\n\n"
            f"• <b>4H Anchor:</b> {trade.htf_anchor.get('direction', '')} [${trade.htf_anchor.get('bottom', 0):,.2f} - ${trade.htf_anchor.get('top', 0):,.2f}]\n"
            f"  └ <i>Formed:</i> {trade.htf_anchor.get('formed_time_ist', '--')} | <i>1st Touch:</i> {trade.htf_anchor.get('first_touch_time_ist', '--')}\n"
            f"• <b>Extreme {trade.ltf_timeframe} FVG:</b> [${trade.ltf_fvg.get('bottom', 0):,.2f} - ${trade.ltf_fvg.get('top', 0):,.2f}] ({trade.ltf_fvg.get('gap_pct', 0):.2f}%)\n"
            f"  └ <i>Formed:</i> {trade.ltf_fvg.get('formed_time_ist', '--')}\n"
            f"• <b>Limit Order Entry:</b> <code>${trade.entry_price:,.2f}</code> ({dist:+.2f}% away)\n"
            f"• <b>Stop Loss:</b> <code>${trade.stop_loss:,.2f}</code>\n"
            f"• <b>Risk ($R$):</b> ${trade.risk_r:,.2f} ({trade.risk_pct:.2f}%){pos_str}\n"
            f"• <b>TP 1R:</b> ${trade.tp_1r:,.2f} | <b>TP 2R:</b> ${trade.tp_2r:,.2f} | <b>TP 3R:</b> ${trade.tp_3r:,.2f}\n"
            f"• <b>Status:</b> ⏳ WAITING FOR RETRACE"
        )

    if evt_type == "ENTRY_FILLED":
        primary_tp = trade.tp_2r if trade.completion_target == "2R" else trade.tp_1r
        return (
            f"🚀 <b>[ENTRY FILLED] {trade.symbol} {side} IS NOW LIVE!</b>\n\n"
            f"• <b>Extreme {trade.ltf_timeframe} FVG:</b> [${trade.ltf_fvg.get('bottom', 0):,.2f} - ${trade.ltf_fvg.get('top', 0):,.2f}]\n"
            f"  └ <i>Formed:</i> {trade.ltf_fvg.get('formed_time_ist', '--')}\n"
            f"• <b>4H Anchor:</b> {trade.htf_anchor.get('direction', '')} [${trade.htf_anchor.get('bottom', 0):,.2f} - ${trade.htf_anchor.get('top', 0):,.2f}]\n"
            f"  └ <i>Formed:</i> {trade.htf_anchor.get('formed_time_ist', '--')} | <i>1st Touch:</i> {trade.htf_anchor.get('first_touch_time_ist', '--')}\n"
            f"• <b>Filled At:</b> <code>${trade.entry_price:,.2f}</code>\n"
            f"• <b>Fill Time:</b> {trade.entry_filled_at_ist or 'Live'}\n"
            f"• <b>Stop Loss:</b> <code>${trade.stop_loss:,.2f}</code>{pos_str}\n"
            f"• <b>Primary Target ({trade.completion_target}):</b> <code>${primary_tp:,.2f}</code>\n"
            f"• <b>Status:</b> 🚀 IN POSITION (Monitoring TP/SL)"
        )

    if evt_type == "TP_HIT":
        return (
            f"🎉 <b>[TARGET ACHIEVED] {trade.symbol} {side} HIT {trade.completion_target}!</b>\n\n"
            f"• <b>Extreme {trade.ltf_timeframe} FVG Formed:</b> {trade.ltf_fvg.get('formed_time_ist', '--')}\n"
            f"• <b>Realized Gain:</b> <code>+{trade.realized_r:.1f}R</code>\n"
            f"• <b>Entry Price:</b> <code>${trade.entry_price:,.2f}</code>\n"
            f"• <b>Exit Time:</b> {trade.closed_at_ist}\n"
            f"• <b>Duration:</b> {trade.duration_min} minutes\n"
            f"• <b>Max MFE:</b> +{trade.mfe_r:.2f}R\n"
            f"• <b>Status:</b> 🏆 TRADE WON"
        )

    if evt_type == "SL_HIT":
        return (
            f"🛑 <b>[STOP LOSS HIT] {trade.symbol} {side} CLOSED</b>\n\n"
            f"• <b>Extreme {trade.ltf_timeframe} FVG Formed:</b> {trade.ltf_fvg.get('formed_time_ist', '--')}\n"
            f"• <b>Realized Loss:</b> <code>-1.0R</code>\n"
            f"• <b>Entry Price:</b> <code>${trade.entry_price:,.2f}</code> | <b>SL:</b> <code>${trade.stop_loss:,.2f}</code>\n"
            f"• <b>Exit Time:</b> {trade.closed_at_ist}\n"
            f"• <b>Duration:</b> {trade.duration_min} minutes\n"
            f"• <b>Max MFE:</b> +{trade.mfe_r:.2f}R\n"
            f"• <b>Status:</b> ❌ STOPPED OUT"
        )

    return ""


_DISPATCH_LOG_LABELS = {
    "ENTRY_FILLED": "Entry Alert",
    "TP_HIT": "TP Hit Alert",
    "SL_HIT": "SL Hit Alert",
}


async def _dispatch_trade_alert(evt_type: str, trade: Any, msg: str, chart_img: Optional[bytes], side: str) -> None:
    """Sends a trade alert over Telegram, threading lifecycle alerts into the discussion group when configured."""
    import main

    _send_alert = main.send_extreme_telegram_alert
    if evt_type == "NEW_SETUP":
        logger.info("Fired Telegram Setup Alert for %s %s (with chart)", trade.symbol, side)
        success, sent_msg_id = await _send_alert(msg, image_bytes=chart_img, return_message_id=True)
        if success and sent_msg_id:
            trade.telegram_message_id = sent_msg_id
            from telegram_client import is_telegram_thread_mode
            if is_telegram_thread_mode():
                from telegram_client import get_linked_discussion_chat_id, resolve_discussion_thread_id, _resolve_chat_id
                chan_chat_id = _resolve_chat_id()
                disc_chat_id = await get_linked_discussion_chat_id(chan_chat_id)
                if disc_chat_id:
                    disc_thread_id = await resolve_discussion_thread_id(
                        channel_chat_id=chan_chat_id,
                        channel_message_id=sent_msg_id,
                        discussion_chat_id=disc_chat_id,
                    )
                    if disc_thread_id:
                        trade.telegram_discussion_thread_id = disc_thread_id
            from extreme_trade_tracker import extreme_trade_tracker
            extreme_trade_tracker._save()
        return

    label = _DISPATCH_LOG_LABELS.get(evt_type, evt_type)
    from telegram_client import is_telegram_thread_mode, get_linked_discussion_chat_id, _resolve_chat_id
    use_thread = is_telegram_thread_mode()
    disc_chat_id = await get_linked_discussion_chat_id(_resolve_chat_id()) if use_thread else None
    if use_thread and disc_chat_id and trade.telegram_discussion_thread_id:
        logger.info("Fired Telegram %s for %s %s into discussion comment thread %s", label, trade.symbol, side, trade.telegram_discussion_thread_id)
        await _send_alert(
            msg,
            image_bytes=chart_img,
            chat_id=str(disc_chat_id),
            reply_to_message_id=trade.telegram_discussion_thread_id,
            message_thread_id=trade.telegram_discussion_thread_id,
        )
    else:
        if evt_type == "ENTRY_FILLED":
            logger.info("Fired Telegram %s for %s %s (with chart, reply_to=%s)", label, trade.symbol, side, trade.telegram_message_id)
        else:
            logger.info("Fired Telegram %s for %s %s (reply_to=%s)", label, trade.symbol, side, trade.telegram_message_id)
        await _send_alert(msg, image_bytes=chart_img, reply_to_message_id=trade.telegram_message_id)


async def _broadcast_trade_event(evt_type: str, trade: Any) -> None:
    """Pushes a trade ledger event to connected dashboard WebSockets (best-effort)."""
    import main

    from extreme_trade_tracker import extreme_trade_tracker
    try:
        await main.dashboard_ws_manager.broadcast({
            "type": "trade_event",
            "event": evt_type,
            "trade": trade.to_dict(),
            "history_data": extreme_trade_tracker.get_filtered_trades(),
        })
    except Exception as exc:
        logger.debug("Error broadcasting trade event to dashboard WS: %s", exc)


async def execute_extreme_screener_cycle() -> List[Dict[str, Any]]:
    """Runs a single background scan across whitelisted coins for Extreme LTF setups."""
    import main

    from strategies import get_strategy
    from extreme_trade_tracker import extreme_trade_tracker

    start_time_ist = datetime.now(IST)
    cfg = _runtime_extreme_config()
    provider = main.get_market_data_provider(state.get("data_provider", "binance"))
    logger.info(
        "[ScreenerCycle] Starting Extreme scan cycle for %d symbol(s): %s (LTF: %s, Target: %s, Provider: %s)",
        len(cfg["coin_list"]), cfg["coin_list"], cfg["ltf"], cfg["target"], provider.name
    )
    mids = await provider.get_all_mids()

    # Resolve the active strategy by name (freqtrade StrategyResolver analog) and
    # build its effective params with config > strategy-default precedence.
    strategy = get_strategy(cfg["active_strategy"])
    strategy_params = strategy.resolve_params({
        "ltf_timeframe": cfg["ltf"],
        "completion_target": cfg["target"],
        "min_gap_pct": cfg["min_gap"],
        "use_close_invalidation": cfg["use_close"],
        "session_filter": cfg["sess_filter"],
        "weekday_filter": cfg["wkday_filter"],
        "entry_session_filter": cfg["entry_sess_filter"],
        "entry_weekday_filter": cfg["entry_wkday_filter"],
        "sessions": cfg["sessions_str"],
        "entry_sessions": cfg["entry_sessions_str"],
    })

    setups_out: List[Dict[str, Any]] = []

    for sym in cfg["coin_list"]:
        raw_sym = provider.resolve_symbol(sym)
        curr_px = lookup_mid(mids, sym, float(mids.get(raw_sym, 0.0)))

        # 1. LEDGER CHECK: open TRADE_ACTIVE positions are immutable; report as-is
        active_trade = extreme_trade_tracker.get_active_trade_for_symbol(sym)
        if active_trade:
            setups_out.append(_active_trade_setup_payload(sym, active_trade, curr_px))
            continue

        # 2. No active trade -> scan for new setups / pending retrace
        try:
            setups = await strategy.find_setups(sym, provider, strategy_params)
            for setup in setups:
                if curr_px == 0.0:
                    curr_px = float(mids.get(raw_sym, mids.get(sym, setup.entry_price)))
                setups_out.append(_extreme_setup_payload(
                    sym, setup, curr_px,
                    strategy_name=cfg["active_strategy"],
                    strategy_params=strategy_params,
                ))
            await asyncio.sleep(0.1)
        except Exception as exc:
            logger.warning("Error in background extreme scan for %s: %s", sym, exc)

    recent_candles_map = await _fetch_recent_candles_map(provider, cfg["ltf"], cfg["coin_list"], extreme_trade_tracker)

    # Process all setups through ExtremeTradeTracker
    events = extreme_trade_tracker.process_live_setups(
        setups_out,
        mids,
        recent_candles_map=recent_candles_map,
        session_config=cfg["session_config"],
        session_filter=cfg["sess_filter"],
        weekday_filter=cfg["wkday_filter"],
        entry_session_filter=cfg["entry_sess_filter"],
        entry_weekday_filter=cfg["entry_wkday_filter"],
    )

    for evt_type, tr in events:
        # Redis dedup prevents duplicate alerts across restarts
        if await main.redis_client.is_alert_sent(tr.symbol, evt_type, tr.trade_id):
            logger.info("Skipping already sent alert: %s %s (%s)", tr.symbol, evt_type, tr.trade_id)
            continue

        side = "LONG" if tr.direction == "Bullish" else "SHORT"
        chart_img = await _generate_event_chart(evt_type, tr, recent_candles_map, provider)
        msg = _trade_alert_message(evt_type, tr, side, mids)
        if msg:
            await _dispatch_trade_alert(evt_type, tr, msg, chart_img, side)
            await main.redis_client.mark_alert_sent(tr.symbol, evt_type, tr.trade_id)
        await _broadcast_trade_event(evt_type, tr)

    act_count = len([s for s in setups_out if s["state"] == "TRADE_ACTIVE"])
    pend_count = len([s for s in setups_out if s["state"] == "PENDING_RETRACE"])

    state["extreme_setups"] = setups_out
    state["extreme_active_count"] = act_count
    state["extreme_pending_count"] = pend_count
    state["extreme_last_scan_time_ist"] = start_time_ist.strftime("%d-%b-%Y %I:%M:%S %p IST")
    state["extreme_total_cycles"] += 1

    elapsed_sec = (datetime.now(IST) - start_time_ist).total_seconds()
    logger.info(
        "[ScreenerCycle] Finished cycle in %.2fs -> Total Setups: %d (Active: %d, Pending Retrace: %d)",
        elapsed_sec, len(setups_out), act_count, pend_count
    )

    try:
        from extreme_trade_tracker import extreme_trade_tracker
        await main.dashboard_ws_manager.broadcast({
            "type": "scan_complete",
            "is_running": state.get("extreme_is_running", False),
            "interval_seconds": state.get("extreme_interval_seconds", 30),
            "last_scan_time_ist": state.get("extreme_last_scan_time_ist", "--"),
            "total_cycles": state.get("extreme_total_cycles", 0),
            "setups": setups_out,
            "history_data": extreme_trade_tracker.get_filtered_trades(),
        })
    except Exception as b_exc:
        logger.debug("Error broadcasting scan_complete to dashboard WS: %s", b_exc)

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
async def lifespan(app):
    """Handles startup and shutdown events for FastAPI."""
    import main

    logger.info("Starting Crypto FVG Screener application (IST & Extreme Strategy Daemon)...")

    # Restore trade ledger state from Redis/disk
    try:
        from extreme_trade_tracker import extreme_trade_tracker
        await extreme_trade_tracker.load_async()

        # Restore runtime configuration from Redis if present
        if main.redis_client.is_configured():
            cfg_key = main.redis_client.get_key("config")
            saved_cfg = await main.redis_client.get_json(cfg_key)
            if saved_cfg and isinstance(saved_cfg, dict):
                if "interval_seconds" in saved_cfg:
                    state["extreme_interval_seconds"] = int(saved_cfg["interval_seconds"])
                if "ltf_timeframe" in saved_cfg:
                    state["extreme_ltf"] = str(saved_cfg["ltf_timeframe"])
                if "completion_target" in saved_cfg:
                    state["extreme_target"] = str(saved_cfg["completion_target"])
                if "min_gap_pct" in saved_cfg:
                    state["extreme_min_gap"] = float(saved_cfg["min_gap_pct"])
                if "use_close_invalidation" in saved_cfg:
                    state["extreme_use_close"] = bool(saved_cfg["use_close_invalidation"])
                if "session_filter_enabled" in saved_cfg:
                    state["extreme_session_filter"] = bool(saved_cfg["session_filter_enabled"])
                if "weekday_filter_enabled" in saved_cfg:
                    state["extreme_weekday_filter"] = bool(saved_cfg["weekday_filter_enabled"])
                if "entry_session_filter_enabled" in saved_cfg:
                    state["extreme_entry_session_filter"] = bool(saved_cfg["entry_session_filter_enabled"])
                if "entry_weekday_filter_enabled" in saved_cfg:
                    state["extreme_entry_weekday_filter"] = bool(saved_cfg["entry_weekday_filter_enabled"])
                if "sessions" in saved_cfg:
                    state["extreme_sessions"] = str(saved_cfg["sessions"]).strip()
                if "entry_sessions" in saved_cfg:
                    state["extreme_entry_sessions"] = str(saved_cfg["entry_sessions"]).strip()
                if "coins_whitelist" in saved_cfg:
                    state["coins_whitelist"] = str(saved_cfg["coins_whitelist"])
                if "data_provider" in saved_cfg:
                    state["data_provider"] = str(saved_cfg["data_provider"]).strip().lower()
                from extreme_trade_tracker import extreme_trade_tracker
                extreme_trade_tracker.update_session_config(
                    SessionFilterConfig.from_legacy(
                        session_filter=state["extreme_session_filter"],
                        weekday_filter=state["extreme_weekday_filter"],
                        entry_session_filter=state["extreme_entry_session_filter"],
                        entry_weekday_filter=state["extreme_entry_weekday_filter"],
                        sessions=state["extreme_sessions"],
                        entry_sessions=state["extreme_entry_sessions"],
                    )
                )
                logger.info("Restored runtime configuration from Redis ('%s').", cfg_key)
    except Exception as exc:
        logger.warning("Failed to restore initial state from Redis: %s", exc)

    # Start Market Data WebSocket streaming for active provider if supported
    try:
        provider = main.get_market_data_provider(state.get("data_provider"))
        if provider.supports_websocket:
            coins = [c.strip().upper() for c in state.get("coins_whitelist", "BTC,ETH,SOL").split(",") if c.strip()]
            ltf = state.get("extreme_ltf", "5m")
            htf = state.get("htf_timeframe", "4h")
            tf_set = list(dict.fromkeys([ltf, "15m", "1h", htf, "4h"]))
            await provider.start_websocket(symbols=coins, timeframes=tf_set)
            logger.info("Started real-time WebSocket market data streaming for %s (%s, %s).", provider.name, coins, tf_set)
    except Exception as ws_err:
        logger.warning("Could not start market data WebSocket stream: %s. Using REST fallback.", ws_err)

    # Extreme LTF daemon
    if ENABLE_STRATEGY_2:
        state["extreme_is_running"] = True
        state["strategy_2_enabled"] = True
        extreme_task = asyncio.create_task(extreme_screener_background_worker())
        state["extreme_background_task"] = extreme_task
        logger.info("Strategy 2 (Extreme LTF) background daemon started.")
    else:
        state["extreme_is_running"] = False
        state["strategy_2_enabled"] = False
        state["extreme_background_task"] = None
        logger.info("Strategy 2 (Extreme LTF) is DISABLED via config (ENABLE_STRATEGY_2=false).")

    yield

    state["is_running"] = False
    state["extreme_is_running"] = False
    if state.get("extreme_background_task"):
        state["extreme_background_task"].cancel()

    await close_all_providers()
    await hyperliquid_client.close()
    await main.redis_client.close()
    logger.info("Application shutdown complete.")


def _register_services_in_main() -> None:
    """Publishes the QA-patchable service names onto the `main` module namespace.

    Only establishes the initial bindings (so `from main import X` and pre-patch
    attribute reads work); call sites read through `_svc()` so later patches of
    `main.X` take effect. Recursion-safe: when the facade itself is importing
    this module, `import main` here re-executes main.py as a fresh module whose
    execution converges to the same state.
    """
    m = _svc()

    m.send_extreme_telegram_alert = send_extreme_telegram_alert
    m.redis_client = redis_client
    m.get_market_data_provider = get_market_data_provider
    m.market_data_provider = market_data_provider
    m.get_last_n_candles = get_last_n_candles
    if dashboard_ws_manager is not None:
        m.dashboard_ws_manager = dashboard_ws_manager
