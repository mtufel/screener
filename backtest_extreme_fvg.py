"""
Historical Backtesting Engine for Extreme LTF FVG Strategy.

Simulates the multi-timeframe strategy across historical market data:
1. Reconstructs 4H active FVGs chronologically (closed candles, selectable wick/close invalidation).
2. Isolates the Active 4H Anchor on first touch.
3. Identifies post-touch unmitigated LTF FVGs (>= min_gap_pct).
4. Selects the #1 Extreme FVG (lowest price for Bullish, highest price for Bearish).
5. Simulates limit order execution at outer boundary.
6. Evaluates forward outcomes across 1R, 2R, and 3R targets, SL hits, MFE, MAE, and duration.
"""

import argparse
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import logging
import os
import time
from typing import Any, Dict, List, Literal, Optional, Tuple

from dotenv import load_dotenv

from hyperliquid_client import HyperliquidClient, hyperliquid_client, SYMBOL_ALIASES
from strategy_extreme_fvg import (
    Candle,
    FVG,
    HTFFVGCache,
    TouchedAnchor,
    ExtremeTradeSetup,
    compute_all_active_4h_fvgs,
    filter_closed_candles,
    get_4h_fvg_first_touch_ts,
    get_most_recent_touched_4h_fvg,
    find_unmitigated_ltf_fvgs,
    select_extreme_ltf_fvg,
    build_extreme_trade_setup,
    HTF_CANDLE_DURATION_MS,
    TIMEFRAME_MS,
)

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))
logger = logging.getLogger("extreme-backtester")


@dataclass
class ExtremeHistoricalTrade:
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
    realized_r_1r: float  # PnL if targeting 1R (+1.0 or -1.0)
    realized_r_2r: float  # PnL if targeting 2R (+2.0 or -1.0)
    realized_r_3r: float  # PnL if targeting 3R (+3.0 or -1.0)
    mfe_r: float          # Maximum Favorable Excursion in R
    mae_r: float          # Maximum Adverse Excursion in R
    duration_minutes: int
    ltf_fvg_bottom: float
    ltf_fvg_top: float
    htf_fvg_bottom: float
    htf_fvg_top: float
    fvg_formation_timestamp: int
    htf_formed_timestamp: int = 0
    htf_first_touch_timestamp: int = 0
    htf_most_recent_touch_timestamp: int = 0
    ltf_gap_pct: float = 0.0

    @property
    def entry_time_ist(self) -> str:
        return datetime.fromtimestamp(self.entry_timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")

    @property
    def exit_time_ist(self) -> str:
        return datetime.fromtimestamp(self.exit_timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")

    @property
    def htf_formed_time_ist(self) -> str:
        if not self.htf_formed_timestamp:
            return "--"
        return datetime.fromtimestamp(self.htf_formed_timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")

    @property
    def htf_first_touch_time_ist(self) -> str:
        if not self.htf_first_touch_timestamp:
            return "--"
        return datetime.fromtimestamp(self.htf_first_touch_timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")

    @property
    def htf_most_recent_touch_time_ist(self) -> str:
        if not self.htf_most_recent_touch_timestamp:
            return "--"
        return datetime.fromtimestamp(self.htf_most_recent_touch_timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")

    @property
    def ltf_formed_time_ist(self) -> str:
        if not self.fvg_formation_timestamp:
            return "--"
        return datetime.fromtimestamp(self.fvg_formation_timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p IST")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
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
                "first_touch_time": self.htf_first_touch_time_ist,
                "most_recent_touch_time": self.htf_most_recent_touch_time_ist,
            },
            "ltf_fvg": {
                "bottom": self.ltf_fvg_bottom,
                "top": self.ltf_fvg_top,
                "gap_pct": round(self.ltf_gap_pct, 3),
                "formed_time": self.ltf_formed_time_ist,
            },
            "fvg_formation_timestamp": self.fvg_formation_timestamp,
            "entry_timestamp": self.entry_timestamp,
        }


@dataclass
class ExtremeBacktestReport:
    """Summary statistics report for backtest simulation."""
    symbol: str
    days: int
    ltf_timeframe: str
    invalidation_mode: str
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
    trades: List[ExtremeHistoricalTrade] = field(default_factory=list)


def simulate_trade_execution(
    symbol: str,
    direction: Literal["Bullish", "Bearish"],
    entry_price: float,
    stop_loss: float,
    entry_timestamp: int,
    subsequent_candles: List[Candle],
    anchor: TouchedAnchor,
    ltf_fvg: FVG,
) -> ExtremeHistoricalTrade:
    """
    Simulates a trade forward candle-by-candle from the entry point until SL or TP3 is hit.
    Evaluates independent 1R, 2R, and 3R resolution.
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

    for c in subsequent_candles:
        exit_ts = c.timestamp + TIMEFRAME_MS.get(ltf_fvg.timeframe, 15 * 60 * 1000)

        # Track extremes
        if direction == "Bullish":
            max_fav_price = max(max_fav_price, c.high)
            max_adv_price = min(max_adv_price, c.low)

            # Check TP milestones before SL
            if not hit_1r and c.high >= tp_1r:
                hit_1r = True
            if not hit_2r and c.high >= tp_2r:
                hit_2r = True
            if c.high >= tp_3r:
                hit_3r = True
                exit_reason = "TP_3R"
                break

            # Check SL
            if c.low <= stop_loss:
                exit_reason = "STOPPED_OUT"
                break
        else:
            max_fav_price = min(max_fav_price, c.low)
            max_adv_price = max(max_adv_price, c.high)

            if not hit_1r and c.low <= tp_1r:
                hit_1r = True
            if not hit_2r and c.low <= tp_2r:
                hit_2r = True
            if c.low <= tp_3r:
                hit_3r = True
                exit_reason = "TP_3R"
                break

            if c.high >= stop_loss:
                exit_reason = "STOPPED_OUT"
                break

    # Calculate MFE & MAE
    if direction == "Bullish":
        mfe_r = max(0.0, (max_fav_price - entry_price) / risk_r)
        mae_r = max(0.0, (entry_price - max_adv_price) / risk_r)
    else:
        mfe_r = max(0.0, (entry_price - max_fav_price) / risk_r)
        mae_r = max(0.0, (max_adv_price - entry_price) / risk_r)

    duration_min = max(1, int((exit_ts - entry_timestamp) / (60 * 1000)))

    # Realized R for each fixed-target policy:
    realized_1r = 1.0 if hit_1r else -1.0
    realized_2r = 2.0 if hit_2r else -1.0
    realized_3r = 3.0 if hit_3r else -1.0

    return ExtremeHistoricalTrade(
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
        ltf_fvg_bottom=ltf_fvg.bottom,
        ltf_fvg_top=ltf_fvg.top,
        htf_fvg_bottom=anchor.fvg.bottom,
        htf_fvg_top=anchor.fvg.top,
        fvg_formation_timestamp=ltf_fvg.formed_at,
        htf_formed_timestamp=anchor.fvg.formed_at,
        htf_first_touch_timestamp=anchor.first_touch_timestamp,
        htf_most_recent_touch_timestamp=anchor.most_recent_touch_timestamp,
        ltf_gap_pct=ltf_fvg.gap_pct,
    )


async def run_extreme_backtest(
    symbol: str,
    days: int = 30,
    ltf_timeframe: str = "15m",
    use_close_invalidation: bool = False,
    min_gap_pct: float = 0.05,
    client: Optional[HyperliquidClient] = None,
) -> ExtremeBacktestReport:
    """
    Executes a complete historical backtest over the specified number of days.
    """
    cli = client or hyperliquid_client
    raw_sym = SYMBOL_ALIASES.get(symbol.upper(), symbol.upper())
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (days * 24 * 3600 * 1000)

    # 1. Fetch historical 4H and LTF candles
    raw_4h = await cli.get_candle_snapshot(raw_sym, "4h", start_ms - (14 * 24 * 3600 * 1000), now_ms)
    raw_ltf = await cli.get_candle_snapshot(raw_sym, ltf_timeframe, start_ms, now_ms)

    if not raw_4h or not raw_ltf:
        logger.error("Failed to retrieve sufficient historical candles for %s", symbol)
        return ExtremeBacktestReport(
            symbol=symbol,
            days=days,
            ltf_timeframe=ltf_timeframe,
            invalidation_mode="close" if use_close_invalidation else "wick",
            min_gap_pct=min_gap_pct,
            total_trades=0,
            wins_1r=0,
            wins_2r=0,
            wins_3r=0,
            losses=0,
            win_rate_1r=0.0,
            win_rate_2r=0.0,
            win_rate_3r=0.0,
            net_pnl_1r=0.0,
            net_pnl_2r=0.0,
            net_pnl_3r=0.0,
            profit_factor_1r=0.0,
            profit_factor_2r=0.0,
            profit_factor_3r=0.0,
            max_drawdown_r=0.0,
            avg_trade_duration_min=0.0,
            avg_mfe_r=0.0,
            trades=[],
        )

    candles_4h = [Candle.from_dict(c) for c in sorted(raw_4h, key=lambda x: x.get("t", 0))]
    candles_ltf = [Candle.from_dict(c) for c in sorted(raw_ltf, key=lambda x: x.get("t", 0))]

    ltf_duration_ms = TIMEFRAME_MS.get(ltf_timeframe, 15 * 60 * 1000)
    executed_trades: List[ExtremeHistoricalTrade] = []
    entered_fvg_timestamps: set = set()

    # Step forward through LTF candles (skipping initial 10 for warm-up)
    i = 10
    while i < len(candles_ltf) - 5:
        curr_ltf = candles_ltf[i]
        curr_time = curr_ltf.timestamp + ltf_duration_ms

        # 4H candles closed strictly before curr_time
        closed_4h = [c for c in candles_4h if (c.timestamp + HTF_CANDLE_DURATION_MS) <= curr_time]
        if len(closed_4h) < 3:
            i += 1
            continue

        # Active 4H FVGs
        active_4h = compute_all_active_4h_fvgs(
            candles_4h=closed_4h,
            current_time_ms=curr_time,
            use_close_invalidation=use_close_invalidation,
            enforce_closed_filter=True,
        )
        if not active_4h:
            i += 1
            continue

        # Available closed LTF candles up to curr_time
        closed_ltf = candles_ltf[:i + 1]

        # Isolate touched 4H anchor
        anchor = get_most_recent_touched_4h_fvg(
            candles_4h=closed_4h,
            active_fvgs=active_4h,
            current_price=curr_ltf.close,
            candles_ltf=closed_ltf,
            ltf_timeframe=ltf_timeframe,
        )
        if not anchor:
            i += 1
            continue

        # Search for post-touch unmitigated LTF FVGs
        unmitigated = find_unmitigated_ltf_fvgs(
            candles_ltf=closed_ltf,
            after_timestamp=anchor.first_touch_timestamp,
            direction=anchor.fvg.direction,
            current_price=curr_ltf.close,
            current_time_ms=curr_time,
            ltf_timeframe=ltf_timeframe,
            min_gap_pct=min_gap_pct,
            completion_target="2R",
        )
        if not unmitigated:
            i += 1
            continue

        best_ltf = select_extreme_ltf_fvg(unmitigated, anchor.fvg.direction)
        if not best_ltf or best_ltf.formed_at in entered_fvg_timestamps:
            i += 1
            continue

        # Check if current candle triggers entry (touches outer boundary)
        is_bullish = best_ltf.direction == "Bullish"
        entry_price = best_ltf.top if is_bullish else best_ltf.bottom
        stop_loss = min(best_ltf.c1.low, best_ltf.c2.low, best_ltf.c3.low) if is_bullish else max(best_ltf.c1.high, best_ltf.c2.high, best_ltf.c3.high)

        entry_triggered = False
        if is_bullish:
            if curr_ltf.low <= entry_price and curr_ltf.low > stop_loss:
                entry_triggered = True
        else:
            if curr_ltf.high >= entry_price and curr_ltf.high < stop_loss:
                entry_triggered = True

        if entry_triggered:
            # Simulate forward
            subsequent = candles_ltf[i + 1:]
            trade = simulate_trade_execution(
                symbol=symbol,
                direction=best_ltf.direction,
                entry_price=entry_price,
                stop_loss=stop_loss,
                entry_timestamp=curr_ltf.timestamp,
                subsequent_candles=subsequent,
                anchor=anchor,
                ltf_fvg=best_ltf,
            )
            executed_trades.append(trade)
            entered_fvg_timestamps.add(best_ltf.formed_at)

            # Advance index forward past trade hold duration
            bars_held = max(1, trade.duration_minutes // (ltf_duration_ms // 60000))
            i += bars_held
        else:
            i += 1

    # Tally results
    total_trades = len(executed_trades)
    wins_1r = sum(1 for t in executed_trades if t.hit_1r)
    wins_2r = sum(1 for t in executed_trades if t.hit_2r)
    wins_3r = sum(1 for t in executed_trades if t.hit_3r)
    losses = sum(1 for t in executed_trades if t.exit_reason == "STOPPED_OUT")

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

    # Max drawdown in R (using 2R target curve)
    peak = 0.0
    curr_equity = 0.0
    max_dd = 0.0
    for t in executed_trades:
        curr_equity += t.realized_r_2r
        peak = max(peak, curr_equity)
        dd = peak - curr_equity
        max_dd = max(max_dd, dd)

    return ExtremeBacktestReport(
        symbol=symbol,
        days=days,
        ltf_timeframe=ltf_timeframe,
        invalidation_mode="close" if use_close_invalidation else "wick",
        min_gap_pct=min_gap_pct,
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


def print_backtest_report(report: ExtremeBacktestReport):
    """Prints a formatted ASCII report of the backtest results."""
    print("\n" + "=" * 80)
    print(f"  📊 HISTORICAL BACKTEST REPORT: {report.symbol} ({report.days} Days)")
    print("=" * 80)
    print(f"  • Lower Timeframe:   {report.ltf_timeframe}")
    print(f"  • Invalidation Mode: {report.invalidation_mode.upper()}")
    print(f"  • Min Gap Size:      {report.min_gap_pct:.2f}%")
    print(f"  • Total Trades:      {report.total_trades}")
    print(f"  • Avg Hold Duration: {report.avg_trade_duration_min:.1f} minutes")
    print(f"  • Avg Max MFE:       {report.avg_mfe_r:+.2f}R")
    print(f"  • Max Drawdown:      -{report.max_drawdown_r:.1f}R")

    print("\n" + "-" * 80)
    print("  🎯 MULTI-TARGET OUTCOMES (1R vs 2R vs 3R):")
    print("-" * 80)
    print(f"  {'Metric':<25} | {'1R Target':<15} | {'2R Target':<15} | {'3R Target':<15}")
    print(f"  {'-'*25}-|-{'-'*15}-|-{'-'*15}-|-{'-'*15}")
    print(f"  {'Wins / Losses':<25} | {report.wins_1r:>4d} / {report.total_trades - report.wins_1r:<4d}      | {report.wins_2r:>4d} / {report.total_trades - report.wins_2r:<4d}      | {report.wins_3r:>4d} / {report.total_trades - report.wins_3r:<4d}")
    print(f"  {'Win Rate':<25} | {report.win_rate_1r:>6.1f}%          | {report.win_rate_2r:>6.1f}%          | {report.win_rate_3r:>6.1f}%")
    print(f"  {'Net Realized PnL':<25} | {report.net_pnl_1r:>+7.1f}R         | {report.net_pnl_2r:>+7.1f}R         | {report.net_pnl_3r:>+7.1f}R")
    print(f"  {'Profit Factor':<25} | {report.profit_factor_1r:>7.2f}          | {report.profit_factor_2r:>7.2f}          | {report.profit_factor_3r:>7.2f}")
    print("=" * 80)

    if report.trades:
        print("\n  📜 LAST 5 EXECUTED TRADES:")
        for idx, t in enumerate(report.trades[-5:], 1):
            w1 = "✅" if t.hit_1r else "❌"
            w2 = "✅" if t.hit_2r else "❌"
            w3 = "✅" if t.hit_3r else "❌"
            print(f"    {idx}. {t.direction:7s} @ ${t.entry_price:,.2f} | Entry: {t.entry_time_ist} | 1R:{w1} 2R:{w2} 3R:{w3} | MFE: {t.mfe_r:+.2f}R | Exit: {t.exit_reason}")
    print()


async def main():
    parser = argparse.ArgumentParser(description="Historical Backtester for Extreme LTF FVG Strategy")
    parser.add_argument("--symbol", default="BTC", help="Symbol to backtest (e.g. BTC, ETH, SOL, PAXG)")
    parser.add_argument("--days", type=int, default=30, help="Number of historical days to backtest (default: 30)")
    parser.add_argument("--ltf", default="15m", choices=["1m", "5m", "15m", "1h"], help="LTF timeframe (default: 15m)")
    parser.add_argument("--invalidation", default="wick", choices=["wick", "close"], help="Invalidation mode (default: wick)")
    parser.add_argument("--min-gap-pct", type=float, default=0.05, help="Minimum gap size in %% (default: 0.05%%)")
    args = parser.parse_args()

    use_close = (args.invalidation == "close")
    report = await run_extreme_backtest(
        symbol=args.symbol.upper(),
        days=args.days,
        ltf_timeframe=args.ltf,
        use_close_invalidation=use_close,
        min_gap_pct=args.min_gap_pct,
    )
    print_backtest_report(report)


if __name__ == "__main__":
    asyncio.run(main())
