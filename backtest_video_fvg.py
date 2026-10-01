"""
Strategy 3 — Video FVG Backtest Engine (backtest_video_fvg.py).

Historical forward-simulation of the Video FVG strategy: a 4H FVG anchor is
confirmed by HTF respect (strong-body rejection candle), then the FIRST LTF FVG
after confirmation is entered with SL at the formation wick and a 3:1 target.

The report (``VideoBacktestReport``) and per-trade record (``VideoHistoricalTrade``)
mirror Strategy 2's shapes so the ledger, dashboard, and strategy-parameterized
API routes accept them unchanged. ``htf_confirm_body_pct`` and ``completion_target``
are additive Video-specific fields.
"""

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import json
import logging
import os
import time
from typing import Any, Dict, List, Literal, Optional, Tuple

from hyperliquid_client import SYMBOL_ALIASES
from market_data_provider import market_data_provider
from session_filter import SessionFilterConfig

from strategy_video_fvg import (
    TIMEFRAME_MS,
    HTF_CANDLE_DURATION_MS,
    Candle,
    _htf_respect_details,
    calc_entry_params,
    find_first_ltf_fvg,
)

load_dotenv = None  # set below (guarded import keeps module import-time clean)
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:  # pragma: no cover
    pass

IST = timezone(timedelta(hours=5, minutes=30))
logger = logging.getLogger("video-fvg-backtester")

HTF_TIMEFRAME = "4h"


# ==============================================================================
# Per-trade record — mirrors ExtremeHistoricalTrade
# ==============================================================================

@dataclass
class VideoHistoricalTrade:
    """Represents an executed trade during historical backtesting."""
    symbol: str
    direction: Literal["Bullish", "Bearish"]
    entry_timestamp: int
    entry_price: float
    stop_loss: float
    risk_r: float
    tp_1r: float
    tp_2r: float
    tp_3r: float
    hit_1r: bool
    hit_2r: bool
    hit_3r: bool
    exit_timestamp: int
    exit_reason: Literal["STOPPED_OUT", "TP_3R", "TIME_EXPIRED"]
    realized_r_1r: float
    realized_r_2r: float
    realized_r_3r: float
    mfe_r: float
    mae_r: float
    duration_minutes: int
    ltf_fvg_bottom: float
    ltf_fvg_top: float
    htf_fvg_bottom: float
    htf_fvg_top: float
    fvg_formation_timestamp: int
    ltf_timeframe: str = "5m"
    fvg_formed_at: int = 0
    htf_formed_timestamp: int = 0
    ltf_gap_pct: float = 0.0
    htf_confirm_timestamp: int = 0

    @property
    def entry_time_ist(self) -> str:
        dur = TIMEFRAME_MS.get(self.ltf_timeframe, 5 * 60 * 1000)
        return datetime.fromtimestamp((self.entry_timestamp + dur) / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")

    @property
    def exit_time_ist(self) -> str:
        dur = TIMEFRAME_MS.get(self.ltf_timeframe, 5 * 60 * 1000)
        return datetime.fromtimestamp((self.exit_timestamp + dur) / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")

    @property
    def htf_formed_time_ist(self) -> str:
        if not self.htf_formed_timestamp:
            return "--"
        return datetime.fromtimestamp(self.htf_formed_timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")

    @property
    def htf_confirm_time_ist(self) -> str:
        if not self.htf_confirm_timestamp:
            return "--"
        return datetime.fromtimestamp(self.htf_confirm_timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")

    @property
    def ltf_formed_time_ist(self) -> str:
        if not self.fvg_formation_timestamp:
            return "--"
        return datetime.fromtimestamp(self.fvg_formation_timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "ltf_timeframe": self.ltf_timeframe,
            "entry_time": self.entry_time_ist,
            "exit_time": self.exit_time_ist,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "risk_r": round(self.risk_r, 2),
            "tp_1r": round(self.tp_1r, 2),
            "tp_2r": round(self.tp_2r, 2),
            "tp_3r": round(self.tp_3r, 2),
            "hit_1r": self.hit_1r,
            "hit_2r": self.hit_2r,
            "hit_3r": self.hit_3r,
            "exit_reason": self.exit_reason,
            "mfe_r": round(self.mfe_r, 2),
            "mae_r": round(self.mae_r, 2),
            "duration_min": self.duration_minutes,
            "htf_anchor": {
                "bottom": self.htf_fvg_bottom,
                "top": self.htf_fvg_top,
                "formed_time": self.htf_formed_time_ist,
                "confirm_time": self.htf_confirm_time_ist,
            },
            "ltf_fvg": {
                "bottom": self.ltf_fvg_bottom,
                "top": self.ltf_fvg_top,
                "gap_pct": round(self.ltf_gap_pct, 3),
                "formed_time": self.ltf_formed_time_ist,
                "formed_at": self.fvg_formed_at or self.fvg_formation_timestamp,
            },
            "fvg_formation_timestamp": self.fvg_formation_timestamp,
            "fvg_formed_at": self.fvg_formed_at or self.fvg_formation_timestamp,
            "entry_timestamp": self.entry_timestamp,
            "exit_timestamp": self.exit_timestamp,
        }


# ==============================================================================
# Report — mirrors ExtremeBacktestReport (+ Video-specific fields)
# ==============================================================================

@dataclass
class VideoBacktestReport:
    """Summary statistics report for Video FVG backtest simulation."""
    symbol: str
    days: int
    ltf_timeframe: str
    htf_confirm_body_pct: float
    completion_target: Literal["1R", "2R", "3R"]
    min_gap_pct: float
    total_trades: int
    wins_1r: int
    wins_2r: int
    wins_3r: int
    losses: int
    win_rate_1r: float
    win_rate_2r: float
    win_rate_3r: float
    net_pnl_1r: float
    net_pnl_2r: float
    net_pnl_3r: float
    profit_factor_1r: float
    profit_factor_2r: float
    profit_factor_3r: float
    max_drawdown_r: float
    avg_trade_duration_min: float
    avg_mfe_r: float
    session_filter_enabled: bool = False
    weekday_filter_enabled: bool = False
    fvg_sessions: str = "ALL"
    trades_filtered_out: int = 0
    trades: List[VideoHistoricalTrade] = field(default_factory=list)


# ==============================================================================
# Simulation — SL takes precedence over TP on same-bar collision (parity with live)
# ==============================================================================

def simulate_trade_execution(
    symbol: str,
    direction: Literal["Bullish", "Bearish"],
    entry_price: float,
    stop_loss: float,
    entry_timestamp: int,
    subsequent_candles: List[Candle],
    ltf_timeframe: str,
    ltf_fvg_bottom: float,
    ltf_fvg_top: float,
    fvg_formation_timestamp: int,
    fvg_formed_at: int,
    htf_fvg_bottom: float,
    htf_fvg_top: float,
    htf_formed_timestamp: int,
    htf_confirm_timestamp: int,
    ltf_gap_pct: float,
) -> VideoHistoricalTrade:
    """Simulates a trade forward candle-by-candle until SL or TP3 is hit.

    Evaluates independent 1R, 2R, and 3R resolution. Fill candle is included in
    exit evaluation; SL takes precedence over TP on the same candle.
    """
    risk_r = abs(entry_price - stop_loss)
    if risk_r <= 0:
        risk_r = entry_price * 0.001

    if direction == "Bullish":
        tp_1r = entry_price + 1.0 * risk_r
        tp_2r = entry_price + 2.0 * risk_r
        tp_3r = entry_price + 3.0 * risk_r
    else:
        tp_1r = entry_price - 1.0 * risk_r
        tp_2r = entry_price - 2.0 * risk_r
        tp_3r = entry_price - 3.0 * risk_r

    hit_1r = False
    hit_2r = False
    hit_3r = False
    exit_ts = entry_timestamp
    exit_reason = "TIME_EXPIRED"
    max_fav_price = entry_price
    max_adv_price = entry_price

    ltf_dur = TIMEFRAME_MS.get(ltf_timeframe, 15 * 60 * 1000)

    for c in subsequent_candles:
        exit_ts = c.timestamp + ltf_dur

        if direction == "Bullish":
            max_fav_price = max(max_fav_price, c.high)
            max_adv_price = min(max_adv_price, c.low)
            # 1. Stop Loss check FIRST (conservative execution & parity with live)
            if c.low <= stop_loss:
                exit_reason = "STOPPED_OUT"
                break
            # 2. Take Profit check SECOND
            if not hit_1r and c.high >= tp_1r:
                hit_1r = True
            if not hit_2r and c.high >= tp_2r:
                hit_2r = True
            if c.high >= tp_3r:
                hit_3r = True
                exit_reason = "TP_3R"
                break
        else:
            max_fav_price = min(max_fav_price, c.low)
            max_adv_price = max(max_adv_price, c.high)
            if c.high >= stop_loss:
                exit_reason = "STOPPED_OUT"
                break
            if not hit_1r and c.low <= tp_1r:
                hit_1r = True
            if not hit_2r and c.low <= tp_2r:
                hit_2r = True
            if c.low <= tp_3r:
                hit_3r = True
                exit_reason = "TP_3R"
                break

    if direction == "Bullish":
        mfe_r = max(0.0, (max_fav_price - entry_price) / risk_r)
        mae_r = max(0.0, (entry_price - max_adv_price) / risk_r)
    else:
        mfe_r = max(0.0, (entry_price - max_fav_price) / risk_r)
        mae_r = max(0.0, (max_adv_price - entry_price) / risk_r)

    duration_min = max(1, int((exit_ts - entry_timestamp) / (60 * 1000)))

    realized_1r = 1.0 if hit_1r else -1.0
    realized_2r = 2.0 if hit_2r else -1.0
    realized_3r = 3.0 if hit_3r else -1.0

    return VideoHistoricalTrade(
        symbol=symbol,
        direction=direction,
        entry_timestamp=entry_timestamp,
        entry_price=entry_price,
        stop_loss=stop_loss,
        risk_r=risk_r,
        tp_1r=tp_1r,
        tp_2r=tp_2r,
        tp_3r=tp_3r,
        hit_1r=hit_1r,
        hit_2r=hit_2r,
        hit_3r=hit_3r,
        exit_timestamp=exit_ts,
        exit_reason=exit_reason,
        realized_r_1r=realized_1r,
        realized_r_2r=realized_2r,
        realized_r_3r=realized_3r,
        mfe_r=mfe_r,
        mae_r=mae_r,
        duration_minutes=duration_min,
        ltf_fvg_bottom=ltf_fvg_bottom,
        ltf_fvg_top=ltf_fvg_top,
        htf_fvg_bottom=htf_fvg_bottom,
        htf_fvg_top=htf_fvg_top,
        fvg_formation_timestamp=fvg_formation_timestamp,
        fvg_formed_at=fvg_formed_at,
        htf_formed_timestamp=htf_formed_timestamp,
        ltf_gap_pct=ltf_gap_pct,
        ltf_timeframe=ltf_timeframe,
        htf_confirm_timestamp=htf_confirm_timestamp,
    )


# ==============================================================================
# Main backtest
# ==============================================================================

async def run_video_fvg_backtest(
    symbol: str,
    days: int = 30,
    ltf_timeframe: str = "5m",
    min_gap_pct: float = 0.03,
    htf_confirm_body_pct: float = 0.5,
    completion_target: Literal["1R", "2R", "3R"] = "3R",
    session_filter: bool = False,
    weekday_filter: bool = False,
    sessions: Optional[str] = None,
    session_config: Optional[SessionFilterConfig] = None,
    client: Optional[Any] = None,
) -> VideoBacktestReport:
    """
    Executes a complete historical backtest over the specified number of days.
    """
    if session_config is None:
        session_config = SessionFilterConfig.from_legacy(
            session_filter=session_filter,
            weekday_filter=weekday_filter,
            sessions=sessions,
        )

    prov = client or market_data_provider
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (days * 24 * 3600 * 1000)

    # 1. Fetch historical 4H and LTF candles
    try:
        if hasattr(prov, "get_historical_candles_range"):
            raw_4h = await prov.get_historical_candles_range(symbol, "4h", start_ms - (14 * 24 * 3600 * 1000), now_ms)
            raw_ltf = await prov.get_historical_candles_range(symbol, ltf_timeframe, start_ms, now_ms)
        else:
            raw_sym = SYMBOL_ALIASES.get(symbol.upper(), symbol.upper())
            raw_4h = await prov.get_candle_snapshot(raw_sym, "4h", start_ms - (14 * 24 * 3600 * 1000), now_ms)
            raw_ltf = await prov.get_candle_snapshot(raw_sym, ltf_timeframe, start_ms, now_ms)
    except Exception as exc:
        logger.error("Failed to retrieve historical candles for %s (%s): %s", symbol, ltf_timeframe, exc)
        raw_4h = []
        raw_ltf = []

    if not raw_4h or not raw_ltf:
        logger.warning("Insufficient or unavailable historical candles for %s (%s)", symbol, ltf_timeframe)
        return _empty_report(
            symbol=symbol, days=days, ltf_timeframe=ltf_timeframe,
            min_gap_pct=min_gap_pct, htf_confirm_body_pct=htf_confirm_body_pct,
            completion_target=completion_target, session_config=session_config,
        )

    candles_4h = [Candle.from_dict(c) for c in sorted(raw_4h, key=lambda x: x.get("t", 0))]
    candles_ltf = [Candle.from_dict(c) for c in sorted(raw_ltf, key=lambda x: x.get("t", 0))]

    ltf_duration_ms = TIMEFRAME_MS.get(ltf_timeframe, 15 * 60 * 1000)
    ltf_close_timestamps = [c.timestamp + ltf_duration_ms for c in candles_ltf]

    # 2. Pre-detect all 4H FVGs, chronological
    from strategy_video_fvg import find_4h_fvgs, select_anchor_4h
    fvg4h_all = sorted(find_4h_fvgs(candles_4h), key=lambda f: f["formed_at"])
    if not fvg4h_all:
        logger.warning("[VideoFVG] No 4H FVG found in %s history", symbol)
        return _empty_report(
            symbol=symbol, days=days, ltf_timeframe=ltf_timeframe,
            min_gap_pct=min_gap_pct, htf_confirm_body_pct=htf_confirm_body_pct,
            completion_target=completion_target, session_config=session_config,
        )

    executed_trades: List[VideoHistoricalTrade] = []
    trades_filtered_out = 0
    min_formed_after = start_ms  # never enter before the backtest window start

    # 3. Iterate over each 4H FVG formation (each is the most-recent anchor at its time)
    for anchor in fvg4h_all:
        direction = anchor["direction"]

        # HTF respect confirmation
        confirmed, confirm_ts = _htf_respect_details(anchor, candles_4h, htf_confirm_body_pct)
        if not confirmed:
            continue

        # Skip if confirmation already passed (a prior trade consumed it)
        ltf_scan_from = max(confirm_ts, min_formed_after) + ltf_duration_ms

        first_fvg = find_first_ltf_fvg(
            candles_ltf=candles_ltf,
            direction=direction,
            min_gap_pct=min_gap_pct,
            formed_after=ltf_scan_from,
            ltf_tf=ltf_timeframe,
        )
        if first_fvg is None:
            continue

        # Optional session filter on LTF FVG formation time
        if not session_config.is_fvg_valid(first_fvg["close_timestamp"]):
            trades_filtered_out += 1
            continue

        params = calc_entry_params(first_fvg, direction)
        entry_price = params["entry_price"]
        stop_loss = params["stop_loss"]

        # Entry gating: find the candle where price first reaches entry (before SL)
        fvg_formed_at = first_fvg["formed_at"]
        start_idx = candles_ltf.index(first_fvg["c3"])
        entered, entry_idx, entry_ts = _find_entry(
            candles_ltf, start_idx + 1, direction, entry_price, stop_loss
        )
        if not entered:
            continue

        subsequent = candles_ltf[entry_idx:]
        trade = simulate_trade_execution(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            entry_timestamp=entry_ts,
            subsequent_candles=subsequent,
            ltf_timeframe=ltf_timeframe,
            ltf_fvg_bottom=first_fvg["bottom"],
            ltf_fvg_top=first_fvg["top"],
            fvg_formation_timestamp=first_fvg["close_timestamp"],
            fvg_formed_at=fvg_formed_at,
            htf_fvg_bottom=anchor["bottom"],
            htf_fvg_top=anchor["top"],
            htf_formed_timestamp=anchor["close_timestamp"],
            htf_confirm_timestamp=confirm_ts,
            ltf_gap_pct=_gap_pct(first_fvg),
        )
        executed_trades.append(trade)

        # Consume time up to trade exit so we don't re-enter overlapping setups
        min_formed_after = trade.exit_timestamp

    # 4. Tally results (identical aggregation to Strategy 2)
    total_trades = len(executed_trades)
    wins_1r = sum(1 for t in executed_trades if t.hit_1r)
    wins_2r = sum(1 for t in executed_trades if t.hit_2r)
    wins_3r = sum(1 for t in executed_trades if t.hit_3r)
    losses = sum(1 for t in executed_trades if t.exit_reason == "STOPPED_OUT" and not t.hit_1r)

    win_rate_1r = (wins_1r / total_trades * 100) if total_trades > 0 else 0.0
    win_rate_2r = (wins_2r / total_trades * 100) if total_trades > 0 else 0.0
    win_rate_3r = (wins_3r / total_trades * 100) if total_trades > 0 else 0.0

    net_pnl_1r = sum(t.realized_r_1r for t in executed_trades)
    net_pnl_2r = sum(t.realized_r_2r for t in executed_trades)
    net_pnl_3r = sum(t.realized_r_3r for t in executed_trades)

    gross_profit_1r = sum(1.0 for t in executed_trades if t.hit_1r)
    gross_profit_2r = sum(2.0 for t in executed_trades if t.hit_2r)
    gross_profit_3r = sum(3.0 for t in executed_trades if t.hit_3r)
    gross_loss = sum(1.0 for t in executed_trades if not t.hit_1r)

    pf_1r = (gross_profit_1r / gross_loss) if gross_loss > 0 else float("inf")
    pf_2r = (gross_profit_2r / gross_loss) if gross_loss > 0 else float("inf")
    pf_3r = (gross_profit_3r / gross_loss) if gross_loss > 0 else float("inf")

    avg_duration = sum(t.duration_minutes for t in executed_trades) / total_trades if total_trades > 0 else 0.0
    avg_mfe = sum(t.mfe_r for t in executed_trades) / total_trades if total_trades > 0 else 0.0

    peak = 0.0
    curr_equity = 0.0
    max_dd = 0.0
    for t in executed_trades:
        curr_equity += t.realized_r_2r
        peak = max(peak, curr_equity)
        max_dd = max(max_dd, peak - curr_equity)

    return VideoBacktestReport(
        symbol=symbol,
        days=days,
        ltf_timeframe=ltf_timeframe,
        htf_confirm_body_pct=htf_confirm_body_pct,
        completion_target=completion_target,
        min_gap_pct=min_gap_pct,
        session_filter_enabled=(session_config.fvg_sessions.strip().upper() != "ALL"),
        weekday_filter_enabled=session_config.fvg_weekdays_only,
        fvg_sessions=session_config.fvg_sessions,
        trades_filtered_out=trades_filtered_out,
        total_trades=total_trades,
        wins_1r=wins_1r,
        wins_2r=wins_2r,
        wins_3r=wins_3r,
        losses=losses,
        win_rate_1r=win_rate_1r,
        win_rate_2r=win_rate_2r,
        win_rate_3r=win_rate_3r,
        net_pnl_1r=net_pnl_1r,
        net_pnl_2r=net_pnl_2r,
        net_pnl_3r=net_pnl_3r,
        profit_factor_1r=pf_1r,
        profit_factor_2r=pf_2r,
        profit_factor_3r=pf_3r,
        max_drawdown_r=max_dd,
        avg_trade_duration_min=avg_duration,
        avg_mfe_r=avg_mfe,
        trades=executed_trades,
    )


def _find_entry(
    candles_ltf: List[Candle],
    start_idx: int,
    direction: Literal["Bullish", "Bearish"],
    entry_price: float,
    stop_loss: float,
) -> Tuple[bool, int, int]:
    """Return (entered, candle_idx, entry_timestamp) for the first entry trigger."""
    for k in range(start_idx, len(candles_ltf)):
        c = candles_ltf[k]
        if direction == "Bullish":
            if c.low <= stop_loss and c.high < entry_price:
                return False, k, 0
            if c.low <= entry_price:
                return True, k, c.timestamp
        else:
            if c.high >= stop_loss and c.low > entry_price:
                return False, k, 0
            if c.high >= entry_price:
                return True, k, c.timestamp
    return False, start_idx, 0


def _gap_pct(fvg: Dict) -> float:
    mid = (fvg["top"] + fvg["bottom"]) / 2.0
    return ((fvg["top"] - fvg["bottom"]) / mid) * 100.0 if mid > 0 else 0.0


def _empty_report(
    symbol: str,
    days: int,
    ltf_timeframe: str,
    min_gap_pct: float,
    htf_confirm_body_pct: float,
    completion_target: Literal["1R", "2R", "3R"],
    session_config: SessionFilterConfig,
) -> VideoBacktestReport:
    return VideoBacktestReport(
        symbol=symbol,
        days=days,
        ltf_timeframe=ltf_timeframe,
        htf_confirm_body_pct=htf_confirm_body_pct,
        completion_target=completion_target,
        min_gap_pct=min_gap_pct,
        session_filter_enabled=(session_config.fvg_sessions.strip().upper() != "ALL"),
        weekday_filter_enabled=session_config.fvg_weekdays_only,
        fvg_sessions=session_config.fvg_sessions,
        total_trades=0,
        wins_1r=0, wins_2r=0, wins_3r=0, losses=0,
        win_rate_1r=0.0, win_rate_2r=0.0, win_rate_3r=0.0,
        net_pnl_1r=0.0, net_pnl_2r=0.0, net_pnl_3r=0.0,
        profit_factor_1r=0.0, profit_factor_2r=0.0, profit_factor_3r=0.0,
        max_drawdown_r=0.0, avg_trade_duration_min=0.0, avg_mfe_r=0.0,
        trades=[],
    )


# ==============================================================================
# Output
# ==============================================================================

def report_to_dict(report: VideoBacktestReport) -> Dict[str, Any]:
    """Serialize a report to a JSON-compatible dictionary."""
    return {
        "symbol": report.symbol,
        "days": report.days,
        "ltf_timeframe": report.ltf_timeframe,
        "htf_confirm_body_pct": report.htf_confirm_body_pct,
        "completion_target": report.completion_target,
        "min_gap_pct": report.min_gap_pct,
        "session_filter_enabled": report.session_filter_enabled,
        "weekday_filter_enabled": report.weekday_filter_enabled,
        "fvg_sessions": report.fvg_sessions,
        "trades_filtered_out": report.trades_filtered_out,
        "total_trades": report.total_trades,
        "wins_1r": report.wins_1r,
        "wins_2r": report.wins_2r,
        "wins_3r": report.wins_3r,
        "losses": report.losses,
        "win_rate_1r": round(report.win_rate_1r, 2),
        "win_rate_2r": round(report.win_rate_2r, 2),
        "win_rate_3r": round(report.win_rate_3r, 2),
        "net_pnl_1r": round(report.net_pnl_1r, 2),
        "net_pnl_2r": round(report.net_pnl_2r, 2),
        "net_pnl_3r": round(report.net_pnl_3r, 2),
        "profit_factor_1r": round(report.profit_factor_1r, 2),
        "profit_factor_2r": round(report.profit_factor_2r, 2),
        "profit_factor_3r": round(report.profit_factor_3r, 2),
        "max_drawdown_r": round(report.max_drawdown_r, 2),
        "avg_trade_duration_min": round(report.avg_trade_duration_min, 2),
        "avg_mfe_r": round(report.avg_mfe_r, 2),
        "trades": [t.to_dict() for t in report.trades],
    }


def print_backtest_report(report: VideoBacktestReport) -> None:
    """Prints a formatted ASCII report."""
    print("\n" + "=" * 80)
    print(f"  📊 VIDEO FVG BACKTEST REPORT: {report.symbol} ({report.days} Days)")
    print("=" * 80)
    print(f"  • Lower Timeframe:      {report.ltf_timeframe}")
    print(f"  • HTF Confirm Body:     {report.htf_confirm_body_pct:.0%} of range")
    print(f"  • Completion Target:    {report.completion_target}")
    print(f"  • Min Gap Size:         {report.min_gap_pct:.2f}%")
    fvg_sess_state = f"{report.fvg_sessions} [ENABLED]" if report.session_filter_enabled else "[DISABLED]"
    print(f"  • FVG Session Filter:   {fvg_sess_state}")
    if report.trades_filtered_out:
        print(f"  • Trades Filtered:      {report.trades_filtered_out}")
    print(f"  • Total Trades:         {report.total_trades}")
    print(f"  • Avg Hold Duration:    {report.avg_trade_duration_min:.1f} minutes")
    print(f"  • Avg Max MFE:          {report.avg_mfe_r:+.2f}R")
    print(f"  • Max Drawdown:         -{report.max_drawdown_r:.1f}R")

    print("\n" + "-" * 80)
    print("  🎯 MULTI-TARGET OUTCOMES (1R vs 2R vs 3R):")
    print("-" * 80)
    print(f"  {'Metric':<25} | {'1R Target':<15} | {'2R Target':<15} | {'3R Target':<15}")
    print(f"  {'-'*25}-|-{'-'*15}-|-{'-'*15}-|-{'-'*15}")
    print(f"  {'Wins':<25} | {report.wins_1r:>4d}              | {report.wins_2r:>4d}              | {report.wins_3r:>4d}")
    print(f"  {'Win Rate':<25} | {report.win_rate_1r:>6.1f}%          | {report.win_rate_2r:>6.1f}%          | {report.win_rate_3r:>6.1f}%")
    print(f"  {'Net PnL (R)':<25} | {report.net_pnl_1r:>+9.1f}R      | {report.net_pnl_2r:>+9.1f}R      | {report.net_pnl_3r:>+9.1f}R")
    print(f"  {'Profit Factor':<25} | {report.profit_factor_1r:>9.2f}      | {report.profit_factor_2r:>9.2f}      | {report.profit_factor_3r:>9.2f}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Video FVG Backtest")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--ltf", default="5m", help="Lower timeframe (1m/5m/15m)")
    parser.add_argument("--min-gap-pct", type=float, default=0.03)
    parser.add_argument("--htf-confirm-body-pct", type=float, default=0.5)
    parser.add_argument("--completion-target", default="3R", choices=["1R", "2R", "3R"])
    parser.add_argument("--session-filter", action="store_true")
    parser.add_argument("--weekday-filter", action="store_true")
    parser.add_argument("--sessions", default=None)
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = parser.parse_args()

    report = await run_video_fvg_backtest(
        symbol=args.symbol,
        days=args.days,
        ltf_timeframe=args.ltf,
        min_gap_pct=args.min_gap_pct,
        htf_confirm_body_pct=args.htf_confirm_body_pct,
        completion_target=args.completion_target,
        session_filter=args.session_filter,
        weekday_filter=args.weekday_filter,
        sessions=args.sessions,
    )

    if args.json:
        print(json.dumps(report_to_dict(report), indent=2))
    else:
        print_backtest_report(report)


if __name__ == "__main__":
    asyncio.run(main())
