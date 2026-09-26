"""
Extreme LTF strategy router: scan, backtest, status, daemon toggle, runtime
config, chart rendering, and live trade history endpoints.
"""

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from app_config import (
    COINS_WHITELIST,
    EXTREME_COMPLETION_TARGET,
    EXTREME_ENTRY_SESSIONS,
    EXTREME_ENTRY_SESSION_FILTER_ENABLED,
    EXTREME_ENTRY_WEEKDAY_FILTER_ENABLED,
    EXTREME_LTF_TIMEFRAME,
    EXTREME_MAX_DIST_FROM_4H_PCT,
    EXTREME_MAX_GAP_PCT,
    EXTREME_MAX_LTF_FVG_AGE_CANDLES,
    EXTREME_MIN_GAP_PCT,
    EXTREME_REQUIRE_MOMENTUM,
    EXTREME_SCAN_INTERVAL_SECONDS,
    EXTREME_SESSIONS,
    EXTREME_SESSION_FILTER_ENABLED,
    EXTREME_USE_CLOSE_INVALIDATION,
    EXTREME_WEEKDAY_FILTER_ENABLED,
    ENABLE_STRATEGY_2,
    IST,
    logger,
    state,
)
from hyperliquid_client import lookup_mid
# Imported for the facade's name-compatibility re-exports (not called here)
from market_data_provider import close_all_providers, get_market_data_provider  # noqa: F401
from redis_client import redis_client
from screener_cycle import (
    _active_trade_setup_payload,
    _extreme_setup_payload,
    extreme_screener_background_worker,
)
from session_filter import SessionFilterConfig
from strategy_extreme_fvg import (
    Candle,
    get_4h_fvg_first_touch_ts,
    get_4h_fvg_most_recent_touch_ts,
)

router = APIRouter()


def _svc():
    """Returns the `main` facade so endpoints read patchable services (provider, redis)
    through the same namespace the tests/harness patch (behavior identical to when
    these endpoints lived in main.py)."""
    import main

    return main


@router.get("/api/extreme/scan", summary="Scan for Extreme LTF FVG Setups")
async def api_extreme_scan(
    symbols: Optional[str] = Query(default=None, description="Comma-separated symbols or leave empty for whitelist"),
    ltf: Optional[str] = Query(default=None, pattern="^(1m|5m|15m|1h)$", description="LTF timeframe"),
    invalidation: Optional[str] = Query(default=None, pattern="^(wick|close)$", description="Invalidation mode"),
    min_gap_pct: Optional[float] = Query(default=None, ge=0.0, description="Minimum LTF FVG gap size %"),
    target: Optional[str] = Query(default=None, pattern="^(1R|2R|3R)$", description="Completion target"),
    session_filter: Optional[bool] = Query(default=None, description="FVG formation NY session filter (13:00-22:00 UTC)"),
    weekday_filter: Optional[bool] = Query(default=None, description="FVG formation Weekday filter (Mon-Fri UTC)"),
    sessions: Optional[str] = Query(default=None, description="FVG formation session filter ('ALL', 'NY', 'LONDON', 'LONDON,NY', or 'HH:MM-HH:MM')"),
):
    from strategy_extreme_fvg import get_extreme_setup_for_symbol
    from extreme_trade_tracker import extreme_trade_tracker

    ltf_to_use = ltf or state.get("extreme_ltf", EXTREME_LTF_TIMEFRAME)
    target_to_use = target or state.get("extreme_target", "2R")
    min_gap_to_use = min_gap_pct if min_gap_pct is not None else state.get("extreme_min_gap", 0.05)
    inval_to_use = invalidation or ("close" if state.get("extreme_use_close") else "wick")
    use_close = (inval_to_use == "close")
    sessions_str = sessions if sessions is not None else state.get("extreme_sessions", EXTREME_SESSIONS)
    sess_filter = (
        session_filter
        if session_filter is not None
        else (
            (sessions_str.strip().upper() != "ALL")
            if (sessions_str and sessions_str.strip())
            else state.get("extreme_session_filter", EXTREME_SESSION_FILTER_ENABLED)
        )
    )
    wkday_filter = weekday_filter
    session_config = SessionFilterConfig.from_legacy(
        session_filter=sess_filter,
        weekday_filter=wkday_filter,
        sessions=sessions_str,
    )

    whitelist_raw = symbols or state.get("coins_whitelist") or os.getenv("COINS_WHITELIST", "BTC,ETH,SOL")
    coin_list = [c.strip().upper() for c in whitelist_raw.split(",") if c.strip()]

    setups_out = []
    provider = _svc().get_market_data_provider(state.get("data_provider"))
    mids = await provider.get_all_mids()

    for sym in coin_list:
        raw_sym = provider.resolve_symbol(sym)
        curr_px = lookup_mid(mids, sym, float(mids.get(raw_sym, 0.0)))

        # 1. Check ledger for active trade (IMMUTABLE entry price, SL, targets)
        active_trade = extreme_trade_tracker.get_active_trade_for_symbol(sym)
        if active_trade:
            setups_out.append(_active_trade_setup_payload(sym, active_trade, curr_px))
            continue

        # 2. If no active trade, scan for new setups
        try:
            setup = await get_extreme_setup_for_symbol(
                symbol=sym,
                ltf_timeframe=ltf_to_use,
                client=provider,
                use_close_invalidation=use_close,
                min_gap_pct=min_gap_to_use,
                completion_target=target_to_use,
                session_config=session_config,
                session_filter=sess_filter,
                weekday_filter=wkday_filter,
            )
            if setup:
                if curr_px == 0.0:
                    curr_px = float(mids.get(raw_sym, mids.get(sym, setup.entry_price)))
                setups_out.append(_extreme_setup_payload(sym, setup, curr_px))
        except Exception as exc:
            logger.warning("Failed to get extreme setup for %s: %s", sym, exc)

    return JSONResponse(content={"status": "success", "count": len(setups_out), "setups": setups_out})


@router.get("/api/extreme/backtest", summary="Run Historical Backtest on Extreme Strategy")
async def api_extreme_backtest(
    symbol: str = Query(default="BTC", description="Coin symbol"),
    days: int = Query(default=14, ge=1, le=90, description="Lookback days"),
    ltf: str = Query(default="5m", pattern="^(1m|5m|15m|1h)$", description="LTF timeframe"),
    invalidation: str = Query(default="wick", pattern="^(wick|close)$", description="Invalidation mode"),
    min_gap_pct: float = Query(default=0.05, ge=0.0, description="Min gap size %"),
    session_filter: Optional[bool] = Query(default=None, description="FVG formation NY session filter (13:00-22:00 UTC)"),
    weekday_filter: Optional[bool] = Query(default=None, description="FVG formation Weekday filter (Mon-Fri UTC)"),
    entry_session_filter: Optional[bool] = Query(default=None, description="Entry fill NY session filter (13:00-22:00 UTC)"),
    entry_weekday_filter: Optional[bool] = Query(default=None, description="Entry fill Weekday filter (Mon-Fri UTC)"),
    sessions: Optional[str] = Query(default=None, description="FVG formation session filter ('ALL', 'NY', 'LONDON', 'LONDON,NY', or 'HH:MM-HH:MM')"),
    entry_sessions: Optional[str] = Query(default=None, description="Entry fill session filter ('ALL', 'NY', 'LONDON', 'LONDON,NY', or 'HH:MM-HH:MM')"),
    # Bias filter params
    max_dist_from_4h_pct: Optional[float] = Query(default=None, description="Reject LTF FVGs further than this %% from the 4H anchor zone (0 disables)"),
    require_momentum: Optional[bool] = Query(default=None, description="Require strong directional impulse candle (body >= 50%% of range)"),
    max_gap_pct: Optional[float] = Query(default=None, description="Reject LTF FVGs with gap_pct above this ceiling (0 disables)"),
    max_ltf_fvg_age_candles: Optional[int] = Query(default=None, description="Reject LTF FVGs that form more than this many candles after the 4H touch (9999 disables)"),
):
    import math
    from backtest_extreme_fvg import run_extreme_backtest

    def _safe_float(val: Any, default: float = 0.0, digits: int = 2) -> float:
        try:
            f = float(val)
            if math.isnan(f) or math.isinf(f):
                return default
            return round(f, digits)
        except (TypeError, ValueError):
            return default

    try:
        use_close = (invalidation == "close")
        sess_filter = (
            session_filter
            if session_filter is not None
            else (
                (sessions.strip().upper() != "ALL")
                if (sessions and sessions.strip())
                else os.getenv("EXTREME_SESSION_FILTER_ENABLED", "false").strip().lower() in ("true", "1", "yes")
            )
        )
        wkday_filter = (
            weekday_filter
            if weekday_filter is not None
            else os.getenv("EXTREME_WEEKDAY_FILTER_ENABLED", "false").strip().lower() in ("true", "1", "yes")
        )
        entry_sess_filter = (
            entry_session_filter
            if entry_session_filter is not None
            else (
                (entry_sessions.strip().upper() != "ALL")
                if (entry_sessions and entry_sessions.strip())
                else os.getenv("EXTREME_ENTRY_SESSION_FILTER_ENABLED", "false").strip().lower() in ("true", "1", "yes")
            )
        )
        entry_wkday_filter = (
            entry_weekday_filter
            if entry_weekday_filter is not None
            else os.getenv("EXTREME_ENTRY_WEEKDAY_FILTER_ENABLED", "false").strip().lower() in ("true", "1", "yes")
        )

        session_config = SessionFilterConfig.from_legacy(
            session_filter=sess_filter,
            weekday_filter=wkday_filter,
            entry_session_filter=entry_sess_filter,
            entry_weekday_filter=entry_wkday_filter,
            sessions=sessions,
            entry_sessions=entry_sessions,
        )

        # Bias filter params: CLI args override env vars; env vars override lenient defaults.
        _max_dist = (
            max_dist_from_4h_pct
            if max_dist_from_4h_pct is not None
            else float(os.getenv("EXTREME_MAX_DIST_FROM_4H_PCT", "0.0"))
        )
        _require_momentum = (
            require_momentum
            if require_momentum is not None
            else os.getenv("EXTREME_REQUIRE_MOMENTUM", "false").strip().lower() in ("true", "1", "yes")
        )
        _max_gap = (
            max_gap_pct
            if max_gap_pct is not None
            else float(os.getenv("EXTREME_MAX_GAP_PCT", "0.0"))
        )
        _max_age = (
            max_ltf_fvg_age_candles
            if max_ltf_fvg_age_candles is not None
            else int(os.getenv("EXTREME_MAX_LTF_FVG_AGE_CANDLES", "9999"))
        )

        provider = _svc().get_market_data_provider(state.get("data_provider"))
        report = await run_extreme_backtest(
            symbol=symbol.strip().upper(),
            days=days,
            ltf_timeframe=ltf,
            use_close_invalidation=use_close,
            min_gap_pct=min_gap_pct,
            session_config=session_config,
            session_filter=sess_filter,
            weekday_filter=wkday_filter,
            entry_session_filter=entry_sess_filter,
            entry_weekday_filter=entry_wkday_filter,
            sessions=sessions,
            entry_sessions=entry_sessions,
            max_dist_from_4h_pct=_max_dist,
            require_momentum=_require_momentum,
            max_gap_pct=_max_gap,
            max_ltf_fvg_age_candles=_max_age,
            client=provider,
        )
        return JSONResponse(content={
            "status": "success",
            "symbol": report.symbol,
            "days": report.days,
            "ltf_timeframe": report.ltf_timeframe,
            "invalidation_mode": report.invalidation_mode,
            "min_gap_pct": report.min_gap_pct,
            "session_filter_enabled": report.session_filter_enabled,
            "weekday_filter_enabled": report.weekday_filter_enabled,
            "entry_session_filter_enabled": report.entry_session_filter_enabled,
            "entry_weekday_filter_enabled": report.entry_weekday_filter_enabled,
            "sessions": report.fvg_sessions,
            "entry_sessions": report.entry_sessions,
            "trades_filtered_out": report.trades_filtered_out,
            "max_dist_from_4h_pct": report.max_dist_from_4h_pct,
            "require_momentum": report.require_momentum,
            "max_gap_pct": report.max_gap_pct,
            "max_ltf_fvg_age_candles": report.max_ltf_fvg_age_candles,
            "total_trades": report.total_trades,
            "wins_1r": report.wins_1r,
            "wins_2r": report.wins_2r,
            "wins_3r": report.wins_3r,
            "losses": report.losses,
            "win_rate_1r": _safe_float(report.win_rate_1r, 0.0, 1),
            "win_rate_2r": _safe_float(report.win_rate_2r, 0.0, 1),
            "win_rate_3r": _safe_float(report.win_rate_3r, 0.0, 1),
            "net_pnl_1r": _safe_float(report.net_pnl_1r, 0.0, 1),
            "net_pnl_2r": _safe_float(report.net_pnl_2r, 0.0, 1),
            "net_pnl_3r": _safe_float(report.net_pnl_3r, 0.0, 1),
            "profit_factor_1r": _safe_float(report.profit_factor_1r, 999.0, 2),
            "profit_factor_2r": _safe_float(report.profit_factor_2r, 999.0, 2),
            "profit_factor_3r": _safe_float(report.profit_factor_3r, 999.0, 2),
            "max_drawdown_r": _safe_float(report.max_drawdown_r, 0.0, 1),
            "avg_trade_duration_min": _safe_float(report.avg_trade_duration_min, 0.0, 1),
            "avg_mfe_r": _safe_float(report.avg_mfe_r, 0.0, 2),
            "trades": [t.to_dict() for t in report.trades],
        })
    except Exception as exc:
        logger.error("Error executing extreme backtest for %s (%s): %s", symbol, ltf, exc)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"Backtest failed: {str(exc)}", "symbol": symbol, "days": days, "total_trades": 0, "trades": []}
        )


@router.get("/api/extreme/status", summary="Get Extreme Background Daemon Status and Live Setups")
async def api_extreme_status():
    return JSONResponse(content={
        "status": "success",
        "strategy_2_enabled": state.get("strategy_2_enabled", ENABLE_STRATEGY_2),
        "is_running": state.get("extreme_is_running", False),
        "interval_seconds": state.get("extreme_interval_seconds", EXTREME_SCAN_INTERVAL_SECONDS),
        "ltf_timeframe": state.get("extreme_ltf", EXTREME_LTF_TIMEFRAME),
        "completion_target": state.get("extreme_target", EXTREME_COMPLETION_TARGET),
        "min_gap_pct": state.get("extreme_min_gap", EXTREME_MIN_GAP_PCT),
        "use_close_invalidation": state.get("extreme_use_close", EXTREME_USE_CLOSE_INVALIDATION),
        "session_filter_enabled": state.get("extreme_session_filter", EXTREME_SESSION_FILTER_ENABLED),
        "weekday_filter_enabled": state.get("extreme_weekday_filter", EXTREME_WEEKDAY_FILTER_ENABLED),
        "entry_session_filter_enabled": state.get("extreme_entry_session_filter", EXTREME_ENTRY_SESSION_FILTER_ENABLED),
        "entry_weekday_filter_enabled": state.get("extreme_entry_weekday_filter", EXTREME_ENTRY_WEEKDAY_FILTER_ENABLED),
        "max_dist_from_4h_pct": state.get("extreme_max_dist_from_4h_pct", EXTREME_MAX_DIST_FROM_4H_PCT),
        "require_momentum": state.get("extreme_require_momentum", EXTREME_REQUIRE_MOMENTUM),
        "max_gap_pct": state.get("extreme_max_gap_pct", EXTREME_MAX_GAP_PCT),
        "max_ltf_fvg_age_candles": state.get("extreme_max_ltf_fvg_age_candles", EXTREME_MAX_LTF_FVG_AGE_CANDLES),
        "sessions": state.get("extreme_sessions", EXTREME_SESSIONS),
        "entry_sessions": state.get("extreme_entry_sessions", EXTREME_ENTRY_SESSIONS),
        "coins_whitelist": state.get("coins_whitelist", COINS_WHITELIST),
        "data_provider": state.get("data_provider", "binance"),
        "fallback_data_provider": state.get("fallback_data_provider", "hyperliquid"),
        "last_scan_time_ist": state.get("extreme_last_scan_time_ist"),
        "active_count": state.get("extreme_active_count", 0),
        "pending_count": state.get("extreme_pending_count", 0),
        "total_cycles": state.get("extreme_total_cycles", 0),
        "setups": state.get("extreme_setups", []),
    })


@router.get("/api/extreme/4h-fvgs", summary="Current View of All Unmitigated 4H FVGs (with Active Anchor)")
async def api_extreme_4h_fvgs(
    symbols: Optional[str] = Query(default=None, description="Comma-separated symbols or leave empty for whitelist"),
    invalidation: Optional[str] = Query(default=None, pattern="^(wick|close)$", description="Invalidation mode"),
):
    """Returns ALL active (unmitigated) 4H FVGs per tracked symbol, flagging the
    strategy's current anchor (most-recent-touched zone, price-inside-first) and
    which zones currently contain price. Zero-FVG symbols are still listed."""
    from strategy_extreme_fvg import (
        get_active_4h_fvgs_for_symbol,
        get_most_recent_touched_4h_fvg,
    )

    inval_to_use = invalidation or ("close" if state.get("extreme_use_close") else "wick")
    use_close = (inval_to_use == "close")

    whitelist_raw = symbols or state.get("coins_whitelist") or os.getenv("COINS_WHITELIST", "BTC,ETH,SOL")
    coin_list = [c.strip().upper() for c in whitelist_raw.split(",") if c.strip()]

    provider = _svc().get_market_data_provider(state.get("data_provider"))
    mids = await provider.get_all_mids()

    symbols_out = []
    for sym in coin_list:
        raw_sym = provider.resolve_symbol(sym)
        curr_px = lookup_mid(mids, sym, float(mids.get(raw_sym, 0.0)))

        try:
            raw_4h = await provider.get_last_n_candles(symbol=sym, timeframe="4h", n=200)
            candles_4h = [Candle.from_dict(c) for c in raw_4h] if raw_4h else []
        except Exception as exc:
            logger.warning("[4h-fvgs] Failed to fetch 4H candles for %s: %s", sym, exc)
            candles_4h = []

        if not candles_4h and curr_px > 0:
            curr_px = curr_px  # keep live mid even without candles
        if candles_4h and curr_px <= 0:
            curr_px = candles_4h[-1].close

        fvgs_out: List[Dict[str, Any]] = []
        anchor_formed_at: Optional[int] = None
        try:
            passed_candles = candles_4h if len(candles_4h) >= 3 else None
            active_fvgs = await get_active_4h_fvgs_for_symbol(
                symbol=sym,
                client=provider,
                use_close_invalidation=use_close,
                candles_4h=passed_candles,
            )
            anchor = (
                get_most_recent_touched_4h_fvg(
                    candles_4h=candles_4h,
                    active_fvgs=active_fvgs,
                    current_price=curr_px,
                )
                if candles_4h
                else None
            )
            if anchor is not None:
                anchor_formed_at = anchor.fvg.formed_at

            fvgs_sorted = sorted(active_fvgs, key=lambda f: f.formed_at, reverse=True)
            for fvg in fvgs_sorted:
                if curr_px > 0:
                    if fvg.bottom <= curr_px <= fvg.top:
                        distance_pct = 0.0  # price is inside the zone
                    else:
                        near_edge = fvg.bottom if curr_px >= fvg.bottom else fvg.top
                        distance_pct = ((curr_px - near_edge) / curr_px) * 100.0
                else:
                    distance_pct = None

                first_touch = get_4h_fvg_first_touch_ts(candles_4h, fvg, current_price=curr_px) if candles_4h else None
                rec_touch = get_4h_fvg_most_recent_touch_ts(candles_4h, fvg, current_price=curr_px) if candles_4h else None
                first_touch_ts = first_touch[0] if first_touch else None
                rec_ts, is_inside = (rec_touch[0], rec_touch[1]) if rec_touch else (first_touch_ts, False)

                def _ist(ts: Optional[int]) -> Optional[str]:
                    return (
                        datetime.fromtimestamp(ts / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")
                        if ts else None
                    )

                fvgs_out.append({
                    "direction": fvg.direction,
                    "top": fvg.top,
                    "bottom": fvg.bottom,
                    "width": fvg.width,
                    "gap_pct": round(fvg.gap_pct, 3),
                    "formed_at": fvg.formed_at,
                    "formed_time_ist": fvg.formed_time_ist,
                    "first_touch_timestamp": first_touch_ts,
                    "first_touch_time_ist": _ist(first_touch_ts),
                    "most_recent_touch_timestamp": rec_ts,
                    "most_recent_touch_time_ist": _ist(rec_ts) if rec_ts and not is_inside else ("Currently Inside (Active Now)" if is_inside else None),
                    "is_currently_inside": is_inside,
                    "distance_to_zone_pct": round(distance_pct, 3) if distance_pct is not None else None,
                    "is_active_anchor": (anchor_formed_at is not None and fvg.formed_at == anchor_formed_at),
                })
        except Exception as exc:
            logger.warning("[4h-fvgs] FVG computation failed for %s: %s", sym, exc)

        symbols_out.append({
            "symbol": sym,
            "current_price": curr_px,
            "invalidation_mode": inval_to_use,
            "fvg_count": len(fvgs_out),
            "active_anchor_formed_at": anchor_formed_at,
            "fvgs": fvgs_out,
        })

    return JSONResponse(content={
        "status": "success",
        "generated_at_ist": datetime.now(IST).strftime("%d-%b-%Y %I:%M:%S %p IST"),
        "coins_whitelist": coin_list,
        "symbols": symbols_out,
    })


@router.post("/api/extreme/toggle-daemon", summary="Start or Pause Extreme Background Daemon")
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


@router.get("/api/extreme/config", summary="Get Extreme Daemon Runtime Configuration")
@router.post("/api/extreme/config", summary="Update Extreme Daemon Runtime Configuration")
async def api_extreme_config(
    interval_seconds: Optional[int] = Query(default=None, ge=5, le=3600, description="Daemon interval in seconds"),
    ltf: Optional[str] = Query(default=None, pattern="^(1m|5m|15m|1h)$", description="LTF timeframe"),
    target: Optional[str] = Query(default=None, pattern="^(1R|2R|3R)$", description="Completion target"),
    min_gap_pct: Optional[float] = Query(default=None, ge=0.0, description="Min gap size %"),
    invalidation: Optional[str] = Query(default=None, pattern="^(wick|close)$", description="Invalidation mode"),
    session_filter: Optional[bool] = Query(default=None, description="FVG formation NY session filter"),
    weekday_filter: Optional[bool] = Query(default=None, description="FVG formation Weekday filter"),
    entry_session_filter: Optional[bool] = Query(default=None, description="Entry fill NY session filter"),
    entry_weekday_filter: Optional[bool] = Query(default=None, description="Entry fill Weekday filter"),
    sessions: Optional[str] = Query(default=None, description="FVG formation session filter ('ALL', 'NY', 'LONDON', 'LONDON,NY', or 'HH:MM-HH:MM')"),
    entry_sessions: Optional[str] = Query(default=None, description="Entry fill session filter ('ALL', 'NY', 'LONDON', 'LONDON,NY', or 'HH:MM-HH:MM')"),
    symbols: Optional[str] = Query(default=None, description="Comma-separated symbols"),
    provider: Optional[str] = Query(default=None, pattern="^(binance|binance_futures|binance_spot|oanda|hyperliquid)$", description="Market data provider"),
    fallback_provider: Optional[str] = Query(default=None, pattern="^(hyperliquid|binance|binance_futures|binance_spot|oanda|none)$", description="Fallback market data provider"),
    max_dist_from_4h_pct: Optional[float] = Query(default=None, ge=0.0, description="Max distance of LTF FVG from 4H anchor zone % (0 disables)"),
    require_momentum: Optional[bool] = Query(default=None, description="Require strong directional impulse candle (body >= 50% of range)"),
    max_gap_pct: Optional[float] = Query(default=None, ge=0.0, description="Reject LTF FVGs with gap_pct above this ceiling (0 disables)"),
    max_ltf_fvg_age_candles: Optional[int] = Query(default=None, ge=0, description="Reject LTF FVGs older than this many candles after the 4H touch"),
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

    if sessions is not None and sessions.strip():
        state["extreme_sessions"] = sessions.strip()
        state["extreme_session_filter"] = (sessions.strip().upper() != "ALL")
    elif session_filter is not None:
        state["extreme_session_filter"] = session_filter
        if not session_filter:
            state["extreme_sessions"] = "ALL"
        elif state.get("extreme_sessions", "ALL").upper() == "ALL":
            state["extreme_sessions"] = "NY"

    if entry_sessions is not None and entry_sessions.strip():
        state["extreme_entry_sessions"] = entry_sessions.strip()
        state["extreme_entry_session_filter"] = (entry_sessions.strip().upper() != "ALL")
    elif entry_session_filter is not None:
        state["extreme_entry_session_filter"] = entry_session_filter
        if not entry_session_filter:
            state["extreme_entry_sessions"] = "ALL"
        elif state.get("extreme_entry_sessions", "ALL").upper() == "ALL":
            state["extreme_entry_sessions"] = "NY"

    if weekday_filter is not None:
        state["extreme_weekday_filter"] = weekday_filter
    if entry_weekday_filter is not None:
        state["extreme_entry_weekday_filter"] = entry_weekday_filter
    if symbols is not None and symbols.strip():
        state["coins_whitelist"] = symbols.strip().upper()
    if provider is not None and provider.strip():
        state["data_provider"] = provider.strip().lower()
        logger.info("Switched active data provider to '%s'", state["data_provider"])
    if fallback_provider is not None and fallback_provider.strip():
        state["fallback_data_provider"] = fallback_provider.strip().lower()
        logger.info("Switched active fallback data provider to '%s'", state["fallback_data_provider"])
    if max_dist_from_4h_pct is not None:
        state["extreme_max_dist_from_4h_pct"] = float(max_dist_from_4h_pct)
    if require_momentum is not None:
        state["extreme_require_momentum"] = bool(require_momentum)
    if max_gap_pct is not None:
        state["extreme_max_gap_pct"] = float(max_gap_pct)
    if max_ltf_fvg_age_candles is not None:
        state["extreme_max_ltf_fvg_age_candles"] = int(max_ltf_fvg_age_candles)

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

    cfg_payload = {
        "interval_seconds": state["extreme_interval_seconds"],
        "ltf_timeframe": state["extreme_ltf"],
        "completion_target": state["extreme_target"],
        "min_gap_pct": state["extreme_min_gap"],
        "use_close_invalidation": state["extreme_use_close"],
        "session_filter_enabled": state["extreme_session_filter"],
        "weekday_filter_enabled": state["extreme_weekday_filter"],
        "entry_session_filter_enabled": state["extreme_entry_session_filter"],
        "entry_weekday_filter_enabled": state["extreme_entry_weekday_filter"],
        "sessions": state["extreme_sessions"],
        "entry_sessions": state["extreme_entry_sessions"],
        "coins_whitelist": state["coins_whitelist"],
        "data_provider": state.get("data_provider", "binance"),
        "fallback_data_provider": state.get("fallback_data_provider", "hyperliquid"),
        # Bias filter state
        "max_dist_from_4h_pct": state.get("extreme_max_dist_from_4h_pct", EXTREME_MAX_DIST_FROM_4H_PCT),
        "require_momentum": state.get("extreme_require_momentum", EXTREME_REQUIRE_MOMENTUM),
        "max_gap_pct": state.get("extreme_max_gap_pct", EXTREME_MAX_GAP_PCT),
        "max_ltf_fvg_age_candles": state.get("extreme_max_ltf_fvg_age_candles", EXTREME_MAX_LTF_FVG_AGE_CANDLES),
    }

    # Persist updated configuration to Redis
    try:
        m = _svc()
        if m.redis_client.is_configured():
            cfg_key = m.redis_client.get_key("config")
            await m.redis_client.set_json(cfg_key, cfg_payload)
    except Exception as exc:
        logger.debug("Failed to persist config to Redis: %s", exc)

    return JSONResponse(content={
        "status": "success",
        "message": "Extreme Daemon configuration updated successfully",
        "config": cfg_payload,
    })


@router.get("/api/extreme/chart", summary="Generate TradingView-Style Chart for Extreme Setup")
async def api_extreme_chart(
    symbol: str = Query(..., description="Symbol e.g. BTC"),
    direction: str = Query(default="Bullish", description="Direction"),
    ltf: str = Query(default="5m", description="LTF Timeframe"),
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
    setup_state: str = Query(default="PENDING_RETRACE", alias="state"),
    floating_r: float = Query(default=0.0),
    entry_ts: Optional[int] = Query(default=None),
    exit_ts: Optional[int] = Query(default=None),
):
    from chart_generator import generate_extreme_setup_chart
    from strategy_extreme_fvg import Candle

    provider = _svc().get_market_data_provider(state.get("data_provider"))
    clean_sym = symbol.strip().upper()
    c_dur = 15 * 60 * 1000 if ltf == "15m" else (5 * 60 * 1000 if ltf == "5m" else (60 * 60 * 1000 if ltf == "1h" else 60 * 1000))
    is_historical = str(setup_state).startswith("HISTORICAL_") or (exit_ts is not None and exit_ts > 0)
    now_ts = int(time.time() * 1000)

    candles = []
    if is_historical and entry_ts and entry_ts > 0:
        # For historical closed trades: only fetch from entry to exit time + delta on both sides
        t_exit = exit_ts if (exit_ts and exit_ts > entry_ts) else (entry_ts + 6 * c_dur)
        t_end = t_exit + 8 * c_dur

        if ltf_formed_ts and 0 < (entry_ts - ltf_formed_ts) <= 25 * c_dur:
            t_start = ltf_formed_ts - 5 * c_dur
        else:
            t_start = entry_ts - 8 * c_dur

        try:
            raw_candles = await provider.get_historical_candles_range(
                coin=clean_sym,
                interval=ltf,
                start_time_ms=t_start,
                end_time_ms=t_end,
            )
            if raw_candles:
                candles = [Candle.from_dict(c) for c in raw_candles]
        except Exception as exc:
            logger.debug("Historical snapshot fetch error for %s: %s", clean_sym, exc)

    elif entry_ts and entry_ts > 0:
        # For active open trades: fetch from entry/formation time (up to 200 candles) to now
        earliest_anchor = min(entry_ts, ltf_formed_ts or entry_ts) - (8 * c_dur)
        t_start = max(earliest_anchor, now_ts - (200 * c_dur))
        t_end = now_ts + (2 * c_dur)

        try:
            raw_candles = await provider.get_historical_candles_range(
                coin=clean_sym,
                interval=ltf,
                start_time_ms=t_start,
                end_time_ms=t_end,
            )
            if raw_candles:
                candles = [Candle.from_dict(c) for c in raw_candles]
        except Exception as exc:
            logger.debug("Active snapshot fetch error for %s: %s", clean_sym, exc)

    if not candles:
        raw_last = await provider.get_last_n_candles(symbol=clean_sym, timeframe=ltf, n=60)
        if raw_last:
            candles = [Candle.from_dict(c) for c in raw_last]

    if not candles:
        # Synthetic fallback so the UI always gets a drawable chart
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
        state=setup_state,
        floating_r=floating_r,
        ltf_timeframe=ltf,
        entry_time_ts=entry_ts,
        exit_time_ts=exit_ts,
    )
    if not img_bytes:
        raise HTTPException(status_code=500, detail="Failed to generate chart image")
    return Response(content=img_bytes, media_type="image/png")


@router.get("/api/extreme/live-history", summary="Get Tracked Live Trade History for Extreme Strategy")
async def api_extreme_live_history(
    state: Optional[str] = Query(default=None, description="Filter by state: PENDING_RETRACE, TRADE_ACTIVE, COMPLETED_TP, STOPPED_OUT"),
    symbol: Optional[str] = Query(default=None),
    direction: Optional[str] = Query(default=None, description="Bullish or Bearish"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
):
    from extreme_trade_tracker import extreme_trade_tracker
    return JSONResponse(
        content=extreme_trade_tracker.get_filtered_trades(
            state=state,
            symbol=symbol,
            direction=direction,
            page=page,
            per_page=per_page,
        )
    )


@router.post("/api/extreme/clear-live-history", summary="Clear Closed Live Trade History")
async def api_extreme_clear_live_history():
    from extreme_trade_tracker import extreme_trade_tracker
    extreme_trade_tracker.clear_history()
    return JSONResponse(content={"status": "success", "message": "Live trade history cleared successfully"})
