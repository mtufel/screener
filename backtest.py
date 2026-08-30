"""
Historical Backtesting and Setup Verification Engine for 2-Stage Multi-Timeframe FVG Strategy.
Simulates:
1. 4H Active FVG Cache (with invalidation).
2. "ANY_VALID" vs "MOST_RECENT" 4H selection modes.
3. Lower Timeframe (1m, 5m, 15m) FVG Formation inside 4H zone.
4. Retrace Entry confirmation into the LTF FVG.
5. Forward simulation against SL and 1.0R, 1.5R, 2.0R, 3.0R Take Profit targets in IST.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import logging
import os
import time
from typing import Any, Dict, List, Literal, Optional

from dotenv import load_dotenv
from hyperliquid_client import HyperliquidClient, hyperliquid_client
from strategy import (
    Candle,
    FVG,
    HTF_TIMEFRAME,
    LTF_TIMEFRAME,
    TPLevels,
    calculate_tp_levels,
    compute_all_active_4h_fvgs,
    compute_fvg,
    price_in_fvg,
    score_coin,
    is_4h_fvg_retraced_after_creation,
)

load_dotenv()

logger = logging.getLogger(__name__)

# IST Timezone (UTC + 5:30)
IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class HistoricalTrade:
    """Represents a historical trade setup and its forward outcome."""
    symbol: str
    direction: Literal["Bullish", "Bearish"]
    entry_timestamp: int
    entry_datetime: str
    entry_price: float
    sl_price: float
    tp_price: float
    risk_amount: float
    target_rr: float
    htf_fvg_bottom: float
    htf_fvg_top: float
    ltf_fvg_bottom: float
    ltf_fvg_top: float
    score: float
    outcome: Literal["WIN", "LOSS", "OPEN"]
    ltf_timeframe: str = "5m"
    htf_mode: str = "ANY_VALID"
    fvg_formation_timestamp: int = 0
    fvg_formation_datetime: str = ""
    exit_candle_idx: int = -1
    exit_timestamp: Optional[int] = None
    exit_datetime: Optional[str] = None
    exit_price: Optional[float] = None
    pnl_pct: float = 0.0
    r_multiple: float = 0.0
    duration_minutes: int = 0
    mfe_pct: float = 0.0  # Max Favorable Excursion %
    mae_pct: float = 0.0  # Max Adverse Excursion %
    sl_points: float = 0.0
    tp_points: float = 0.0

    def __post_init__(self):
        if self.sl_points == 0.0:
            self.sl_points = abs(self.entry_price - self.sl_price)
        if self.tp_points == 0.0:
            self.tp_points = abs(self.entry_price - self.tp_price)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "ltf_timeframe": self.ltf_timeframe,
            "htf_mode": self.htf_mode,
            "fvg_formation_timestamp": self.fvg_formation_timestamp,
            "fvg_formation_datetime": self.fvg_formation_datetime,
            "entry_timestamp": self.entry_timestamp,
            "entry_datetime": self.entry_datetime,
            "entry_price": round(self.entry_price, 4),
            "sl_price": round(self.sl_price, 4),
            "tp_price": round(self.tp_price, 4),
            "sl_points": round(self.sl_points, 4),
            "tp_points": round(self.tp_points, 4),
            "risk_amount": round(self.risk_amount, 4),
            "target_rr": self.target_rr,
            "htf_fvg": {"bottom": round(self.htf_fvg_bottom, 4), "top": round(self.htf_fvg_top, 4)},
            "ltf_fvg": {"bottom": round(self.ltf_fvg_bottom, 4), "top": round(self.ltf_fvg_top, 4)},
            "score": round(self.score, 4),
            "outcome": self.outcome,
            "exit_candle_idx": self.exit_candle_idx,
            "exit_timestamp": self.exit_timestamp,
            "exit_datetime": self.exit_datetime,
            "exit_price": round(self.exit_price, 4) if self.exit_price is not None else None,
            "pnl_pct": round(self.pnl_pct, 2),
            "r_multiple": round(self.r_multiple, 2),
            "duration_minutes": self.duration_minutes,
            "mfe_pct": round(self.mfe_pct, 2),
            "mae_pct": round(self.mae_pct, 2),
        }


@dataclass
class BacktestSummary:
    """Summary metrics of a historical backtest run."""
    symbol: str
    days: int
    target_rr: float
    ltf_timeframe: str
    htf_mode: str
    start_date_ist: str
    end_date_ist: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    open_trades: int
    win_rate_pct: float
    profit_factor: float
    total_r_return: float
    avg_trade_duration_minutes: float
    min_duration_minutes: int
    max_duration_minutes: int
    avg_sl_points: float
    min_sl_points: float
    max_sl_points: float
    avg_tp_points: float
    min_tp_points: float
    max_tp_points: float
    avg_mfe_pct: float
    min_mfe_pct: float
    max_mfe_pct: float
    avg_mae_pct: float
    trades: List[HistoricalTrade]
    single_position: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "days": self.days,
            "target_rr": self.target_rr,
            "ltf_timeframe": self.ltf_timeframe,
            "htf_mode": self.htf_mode,
            "single_position": self.single_position,
            "start_date_ist": self.start_date_ist,
            "end_date_ist": self.end_date_ist,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "open_trades": self.open_trades,
            "win_rate_pct": round(self.win_rate_pct, 2),
            "profit_factor": round(self.profit_factor, 2),
            "total_r_return": round(self.total_r_return, 2),
            "avg_trade_duration_minutes": round(self.avg_trade_duration_minutes, 1),
            "min_duration_minutes": self.min_duration_minutes,
            "max_duration_minutes": self.max_duration_minutes,
            "avg_sl_points": round(self.avg_sl_points, 4),
            "min_sl_points": round(self.min_sl_points, 4),
            "max_sl_points": round(self.max_sl_points, 4),
            "avg_tp_points": round(self.avg_tp_points, 4),
            "min_tp_points": round(self.min_tp_points, 4),
            "max_tp_points": round(self.max_tp_points, 4),
            "avg_mfe_pct": round(self.avg_mfe_pct, 2),
            "min_mfe_pct": round(self.min_mfe_pct, 2),
            "max_mfe_pct": round(self.max_mfe_pct, 2),
            "avg_mae_pct": round(self.avg_mae_pct, 2),
            "trades": [t.to_dict() for t in self.trades],
        }


def _simulate_trade_forward(
    entry_idx: int,
    candles_ltf: List[Candle],
    symbol: str,
    direction: Literal["Bullish", "Bearish"],
    entry_price: float,
    sl_price: float,
    target_rr: float,
    htf_fvg: FVG,
    ltf_fvg: FVG,
    score: float,
    ltf_timeframe: str = "5m",
    htf_mode: str = "ANY_VALID",
) -> HistoricalTrade:
    """
    Forward-simulates the outcome of an activated retrace trade using subsequent LTF candles.
    """
    entry_candle = candles_ltf[entry_idx]
    entry_ts = entry_candle.timestamp
    entry_dt = datetime.fromtimestamp(entry_ts / 1000.0, tz=IST).strftime("%Y-%m-%d %I:%M %p IST")

    risk_amount = abs(entry_price - sl_price)
    if risk_amount <= 0:
        risk_amount = entry_price * 0.005

    if direction == "Bullish":
        tp_price = entry_price + (target_rr * risk_amount)
    else:
        tp_price = entry_price - (target_rr * risk_amount)

    outcome: Literal["WIN", "LOSS", "OPEN"] = "OPEN"
    exit_candle_idx = len(candles_ltf) - 1
    exit_ts: Optional[int] = None
    exit_dt: Optional[str] = None
    exit_price: Optional[float] = None
    duration_min = 0

    max_fav_excursion = 0.0
    max_adv_excursion = 0.0

    # Simulate future candles starting from entry_idx + 1
    for k in range(entry_idx + 1, len(candles_ltf)):
        c = candles_ltf[k]

        if direction == "Bullish":
            fav = ((c.high - entry_price) / entry_price) * 100.0
            adv = ((entry_price - c.low) / entry_price) * 100.0
        else:
            fav = ((entry_price - c.low) / entry_price) * 100.0
            adv = ((c.high - entry_price) / entry_price) * 100.0

        max_fav_excursion = max(max_fav_excursion, fav)
        max_adv_excursion = max(max_adv_excursion, adv)

        # Check exit conditions
        if direction == "Bullish":
            if c.low <= sl_price:
                outcome = "LOSS"
                exit_price = sl_price
                exit_ts = c.timestamp
                exit_candle_idx = k
                break
            if c.high >= tp_price:
                outcome = "WIN"
                exit_price = tp_price
                exit_ts = c.timestamp
                exit_candle_idx = k
                break
        else:
            if c.high >= sl_price:
                outcome = "LOSS"
                exit_price = sl_price
                exit_ts = c.timestamp
                exit_candle_idx = k
                break
            if c.low <= tp_price:
                outcome = "WIN"
                exit_price = tp_price
                exit_ts = c.timestamp
                exit_candle_idx = k
                break

    if exit_ts:
        exit_dt = datetime.fromtimestamp(exit_ts / 1000.0, tz=IST).strftime("%Y-%m-%d %I:%M %p IST")
        duration_min = int((exit_ts - entry_ts) / (1000 * 60))
    else:
        last_c = candles_ltf[-1]
        exit_price = last_c.close
        exit_ts = last_c.timestamp
        exit_dt = datetime.fromtimestamp(exit_ts / 1000.0, tz=IST).strftime("%Y-%m-%d %I:%M %p IST")
        duration_min = int((exit_ts - entry_ts) / (1000 * 60))

    if outcome == "WIN":
        pnl_pct = (risk_amount / entry_price) * target_rr * 100.0
        r_multiple = target_rr
    elif outcome == "LOSS":
        pnl_pct = -(risk_amount / entry_price) * 100.0
        r_multiple = -1.0
    else:
        if direction == "Bullish":
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0
        else:
            pnl_pct = ((entry_price - exit_price) / entry_price) * 100.0
        r_multiple = pnl_pct / ((risk_amount / entry_price) * 100.0) if risk_amount > 0 else 0.0

    fvg_formation_ts = ltf_fvg.formed_at
    fvg_formation_dt = datetime.fromtimestamp(fvg_formation_ts / 1000.0, tz=IST).strftime("%Y-%m-%d %I:%M %p IST")

    return HistoricalTrade(
        symbol=symbol,
        direction=direction,
        entry_timestamp=entry_ts,
        entry_datetime=entry_dt,
        fvg_formation_timestamp=fvg_formation_ts,
        fvg_formation_datetime=fvg_formation_dt,
        entry_price=entry_price,
        sl_price=sl_price,
        tp_price=tp_price,
        risk_amount=risk_amount,
        target_rr=target_rr,
        htf_fvg_bottom=htf_fvg.bottom,
        htf_fvg_top=htf_fvg.top,
        ltf_fvg_bottom=ltf_fvg.bottom,
        ltf_fvg_top=ltf_fvg.top,
        score=score,
        outcome=outcome,
        ltf_timeframe=ltf_timeframe,
        htf_mode=htf_mode,
        exit_candle_idx=exit_candle_idx,
        exit_timestamp=exit_ts,
        exit_datetime=exit_dt,
        exit_price=exit_price,
        pnl_pct=pnl_pct,
        r_multiple=r_multiple,
        duration_minutes=duration_min,
        mfe_pct=max_fav_excursion,
        mae_pct=max_adv_excursion,
    )


async def run_historical_backtest(
    symbol: str = "BTC",
    days: int = 14,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    target_rr: float = 2.0,
    ltf_timeframe: str = "5m",
    htf_mode: str = "ANY_VALID",
    single_position: bool = True,
    client: Optional[HyperliquidClient] = None,
) -> BacktestSummary:
    """
    Simulates the full 2-stage FVG Strategy historically across a selected time range.
    Supports either a relative lookback (in days) or exact custom date range (start_date to end_date).
    Supports single_position mode (one trade at a time until exit) vs concurrent positions mode.
    """
    cli = client or hyperliquid_client
    ltf = ltf_timeframe if ltf_timeframe in ["1m", "5m", "15m", "1h"] else "5m"
    h_mode = htf_mode if htf_mode in ["ANY_VALID", "MOST_RECENT"] else "ANY_VALID"

    if start_date and end_date:
        try:
            start_dt = datetime.strptime(start_date.strip(), "%Y-%m-%d").replace(tzinfo=IST)
            end_dt = (datetime.strptime(end_date.strip(), "%Y-%m-%d") + timedelta(days=1, seconds=-1)).replace(tzinfo=IST)
            start_time_ms = int(start_dt.timestamp() * 1000)
            end_time_ms = int(end_dt.timestamp() * 1000)
            days = max(1, int((end_time_ms - start_time_ms) / (24 * 3600 * 1000)))
        except Exception as exc:
            logger.warning("Error parsing custom date range (%s to %s): %s", start_date, end_date, exc)
            days = max(1, min(180, days))
            end_time_ms = int(time.time() * 1000)
            start_time_ms = end_time_ms - (days * 24 * 60 * 60 * 1000)
    else:
        days = max(1, min(180, days))
        end_time_ms = int(time.time() * 1000)
        start_time_ms = end_time_ms - (days * 24 * 60 * 60 * 1000)

    start_date_str = datetime.fromtimestamp(start_time_ms / 1000.0, tz=IST).strftime("%d-%b-%Y")
    end_date_str = datetime.fromtimestamp(end_time_ms / 1000.0, tz=IST).strftime("%d-%b-%Y")

    # 1. Fetch historical 4H and LTF candles
    raw_4h = await cli.get_candle_snapshot(
        coin=symbol,
        interval=HTF_TIMEFRAME,
        start_time_ms=start_time_ms - (14 * 24 * 3600 * 1000),  # 14-day buffer for 4H history
        end_time_ms=end_time_ms,
    )
    raw_ltf = await cli.get_candle_snapshot(
        coin=symbol,
        interval=ltf,
        start_time_ms=start_time_ms,
        end_time_ms=end_time_ms,
    )

    if len(raw_4h) < 5 or len(raw_ltf) < 10:
        logger.warning("Insufficient historical candle data for %s (%s) backtest.", symbol, ltf)
        return BacktestSummary(
            symbol=symbol,
            days=days,
            target_rr=target_rr,
            ltf_timeframe=ltf,
            htf_mode=h_mode,
            single_position=single_position,
            start_date_ist=start_date_str,
            end_date_ist=end_date_str,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            open_trades=0,
            win_rate_pct=0.0,
            profit_factor=0.0,
            total_r_return=0.0,
            avg_trade_duration_minutes=0.0,
            min_duration_minutes=0,
            max_duration_minutes=0,
            avg_sl_points=0.0,
            min_sl_points=0.0,
            max_sl_points=0.0,
            avg_tp_points=0.0,
            min_tp_points=0.0,
            max_tp_points=0.0,
            avg_mfe_pct=0.0,
            min_mfe_pct=0.0,
            max_mfe_pct=0.0,
            avg_mae_pct=0.0,
            trades=[],
        )

    candles_4h = [Candle.from_dict(c) for c in sorted(raw_4h, key=lambda x: x.get("t", 0))]
    candles_ltf = [Candle.from_dict(c) for c in sorted(raw_ltf, key=lambda x: x.get("t", 0))]

    trades: List[HistoricalTrade] = []
    min_candle_gap = 40 if ltf == "1m" else (20 if ltf == "5m" else 8)
    last_trade_candle_idx = -min_candle_gap
    active_trade_exit_idx = -1

    # Iterate through LTF candles chronologically
    for i in range(15, len(candles_ltf) - 1):
        if single_position and i <= active_trade_exit_idx:
            continue
        if not single_position and (i - last_trade_candle_idx < min_candle_gap):
            continue

        curr_candle = candles_ltf[i]
        curr_ts = curr_candle.timestamp
        curr_price = curr_candle.close

        # Filter 4H candles finished strictly at or before curr_ts
        htf_slice = [c for c in candles_4h if c.timestamp <= curr_ts]
        if len(htf_slice) < 3:
            continue

        active_4h_fvgs = compute_all_active_4h_fvgs(htf_slice)
        if not active_4h_fvgs:
            continue

        fvgs_to_check = [active_4h_fvgs[0]] if h_mode == "MOST_RECENT" else active_4h_fvgs
        matched_htf_fvg: Optional[FVG] = None
        for htf_fvg in fvgs_to_check:
            if is_4h_fvg_retraced_after_creation(htf_slice, htf_fvg, current_price=curr_price, max_candles_since_test=6):
                matched_htf_fvg = htf_fvg
                break

        if matched_htf_fvg is None:
            continue

        # Check for newly formed LTF FVG
        ltf_slice = candles_ltf[: i + 1]
        ltf_fvg = compute_fvg(ltf_slice[-20:], direction=matched_htf_fvg.direction)
        if ltf_fvg is None:
            continue

        # Stop loss calculation
        if matched_htf_fvg.direction == "Bullish":
            sl_price = min(ltf_fvg.c1.low, ltf_fvg.c2.low, ltf_fvg.c3.low)
            if sl_price >= curr_price:
                continue
        else:
            sl_price = max(ltf_fvg.c1.high, ltf_fvg.c2.high, ltf_fvg.c3.high)
            if sl_price <= curr_price:
                continue

        # Look for retrace into the LTF FVG strictly on subsequent candles (i + 1 onwards)
        retrace_idx = None
        retrace_entry_price = None
        max_lookahead = min(len(candles_ltf), i + 20)  # Look ahead up to 20 LTF candles for retrace
        for check_idx in range(i + 1, max_lookahead):
            chk_c = candles_ltf[check_idx]
            if matched_htf_fvg.direction == "Bullish":
                # Invalidation if price breaches SL before retrace
                if chk_c.low < sl_price:
                    break
                if chk_c.low <= ltf_fvg.top and chk_c.high >= ltf_fvg.bottom:
                    retrace_idx = check_idx
                    retrace_entry_price = min(chk_c.open, ltf_fvg.top)
                    break
            else:
                # Invalidation if price breaches SL before retrace
                if chk_c.high > sl_price:
                    break
                if chk_c.high >= ltf_fvg.bottom and chk_c.low <= ltf_fvg.top:
                    retrace_idx = check_idx
                    retrace_entry_price = max(chk_c.open, ltf_fvg.bottom)
                    break

        if retrace_idx is None or retrace_entry_price is None:
            continue

        score = score_coin(matched_htf_fvg, ltf_fvg, retrace_entry_price)

        # Simulate forward from retrace entry index
        trade = _simulate_trade_forward(
            entry_idx=retrace_idx,
            candles_ltf=candles_ltf,
            symbol=symbol,
            direction=matched_htf_fvg.direction,
            entry_price=retrace_entry_price,
            sl_price=sl_price,
            target_rr=target_rr,
            htf_fvg=matched_htf_fvg,
            ltf_fvg=ltf_fvg,
            score=score,
            ltf_timeframe=ltf,
            htf_mode=h_mode,
        )

        trades.append(trade)
        last_trade_candle_idx = retrace_idx
        if single_position and trade.exit_candle_idx > 0:
            active_trade_exit_idx = trade.exit_candle_idx

    total_trades = len(trades)
    winning_trades = sum(1 for t in trades if t.outcome == "WIN")
    losing_trades = sum(1 for t in trades if t.outcome == "LOSS")
    open_trades = sum(1 for t in trades if t.outcome == "OPEN")

    resolved_trades = winning_trades + losing_trades
    win_rate_pct = (winning_trades / resolved_trades * 100.0) if resolved_trades > 0 else 0.0

    gross_profit_r = winning_trades * target_rr
    gross_loss_r = losing_trades * 1.0
    profit_factor = (gross_profit_r / gross_loss_r) if gross_loss_r > 0 else (gross_profit_r if gross_profit_r > 0 else 1.0)
    total_r_return = sum(t.r_multiple for t in trades)

    sl_points_list = [t.sl_points for t in trades]
    tp_points_list = [t.tp_points for t in trades]
    mfe_list = [t.mfe_pct for t in trades]
    duration_list = [t.duration_minutes for t in trades]

    avg_sl_points = sum(sl_points_list) / total_trades if total_trades > 0 else 0.0
    min_sl_points = min(sl_points_list) if total_trades > 0 else 0.0
    max_sl_points = max(sl_points_list) if total_trades > 0 else 0.0

    avg_tp_points = sum(tp_points_list) / total_trades if total_trades > 0 else 0.0
    min_tp_points = min(tp_points_list) if total_trades > 0 else 0.0
    max_tp_points = max(tp_points_list) if total_trades > 0 else 0.0

    avg_duration = sum(duration_list) / total_trades if total_trades > 0 else 0.0
    min_duration = min(duration_list) if total_trades > 0 else 0
    max_duration = max(duration_list) if total_trades > 0 else 0

    avg_mfe = sum(mfe_list) / total_trades if total_trades > 0 else 0.0
    min_mfe = min(mfe_list) if total_trades > 0 else 0.0
    max_mfe = max(mfe_list) if total_trades > 0 else 0.0

    avg_mae = (sum(t.mae_pct for t in trades) / total_trades) if total_trades > 0 else 0.0

    return BacktestSummary(
        symbol=symbol,
        days=days,
        target_rr=target_rr,
        ltf_timeframe=ltf,
        htf_mode=h_mode,
        single_position=single_position,
        start_date_ist=start_date_str,
        end_date_ist=end_date_str,
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        open_trades=open_trades,
        win_rate_pct=win_rate_pct,
        profit_factor=profit_factor,
        total_r_return=total_r_return,
        avg_trade_duration_minutes=avg_duration,
        min_duration_minutes=min_duration,
        max_duration_minutes=max_duration,
        avg_sl_points=avg_sl_points,
        min_sl_points=min_sl_points,
        max_sl_points=max_sl_points,
        avg_tp_points=avg_tp_points,
        min_tp_points=min_tp_points,
        max_tp_points=max_tp_points,
        avg_mfe_pct=avg_mfe,
        min_mfe_pct=min_mfe,
        max_mfe_pct=max_mfe,
        avg_mae_pct=avg_mae,
        trades=trades,
    )
