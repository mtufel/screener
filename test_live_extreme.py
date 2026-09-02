"""
Live Interactive CLI Scanner for Step 1 & Step 2 (test_live_extreme.py).
Usage:
    .venv/bin/python test_live_extreme.py BTC
    .venv/bin/python test_live_extreme.py PAXG --ltf 5m
    .venv/bin/python test_live_extreme.py --all
"""

import argparse
import asyncio
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import List

from hyperliquid_client import hyperliquid_client
from strategy_extreme_fvg import (
    Candle,
    FVG,
    HTFFVGCache,
    TouchedAnchor,
    ExtremeTradeSetup,
    get_most_recent_touched_4h_fvg,
    find_unmitigated_ltf_fvgs,
    select_extreme_ltf_fvg,
    build_extreme_trade_setup,
    HTF_CANDLE_DURATION_MS,
    TIMEFRAME_MS,
)

IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_UNIVERSE = [
    "BTC", "ETH", "SOL", "PAXG", "DOGE", "AVAX", "BNB", "SUI", "APT", "LINK",
    "NEAR", "ARB", "OP", "XRP", "ADA"
]


async def inspect_symbol_live(
    symbol: str,
    ltf_timeframe: str = "15m",
    use_close_invalidation: bool = False,
    min_gap_pct: float = 0.05,
    completion_target: str = "2R",
):
    print("\n" + "=" * 80)
    print(f"  🔍 LIVE SCAN: {symbol} (LTF Refinement: {ltf_timeframe})")
    print("=" * 80)

    # 1. Fetch 4H and LTF live candles
    try:
        raw_4h = await hyperliquid_client.get_last_n_candles(symbol, "4h", n=200)
        raw_ltf = await hyperliquid_client.get_last_n_candles(symbol, ltf_timeframe, n=300)
    except Exception as exc:
        print(f"❌ Error fetching live data for {symbol}: {exc}")
        return

    if not raw_4h or len(raw_4h) < 3:
        print(f"⚠️ Insufficient 4H candle data for {symbol}.")
        return

    candles_4h = [Candle.from_dict(c) for c in raw_4h]
    candles_ltf = [Candle.from_dict(c) for c in raw_ltf] if raw_ltf else []
    current_price = candles_4h[-1].close
    now_ms = int(time.time() * 1000)

    # Check latest candle closed status
    latest_4h = candles_4h[-1]
    is_closed = (latest_4h.timestamp + HTF_CANDLE_DURATION_MS) <= now_ms
    c_open_str = datetime.fromtimestamp(latest_4h.timestamp / 1000.0, tz=IST).strftime("%d-%b %I:%M %p")
    c_close_str = datetime.fromtimestamp((latest_4h.timestamp + HTF_CANDLE_DURATION_MS) / 1000.0, tz=IST).strftime("%d-%b %I:%M %p")

    print(f"Live Price:        ${current_price:,.2f}")
    print(f"Latest 4H Bar:     {c_open_str} -> {c_close_str} (Closed: {is_closed})")

    # =========================================================================
    # STEP 1: Non-Invalidated 4H FVG Detection & Caching
    # =========================================================================
    inv_label = "CANDLE CLOSE" if use_close_invalidation else "WICK"
    print(f"\n--- [STEP 1] Non-Invalidated 4H FVGs (Invalidation: {inv_label}) ---")
    cache = HTFFVGCache()
    t0 = time.perf_counter()
    active_fvgs = cache.bootstrap(
        symbol,
        candles_4h,
        current_time_ms=now_ms,
        use_close_invalidation=use_close_invalidation,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000

    if not active_fvgs:
        print(f"  ⚪ No active 4H FVGs found for {symbol}.")
    else:
        print(f"  ⚡ Found {len(active_fvgs)} active 4H FVG(s) (computed in {elapsed_ms:.2f}ms):")
        for idx, f in enumerate(active_fvgs, 1):
            dist = ((current_price - f.midpoint) / f.midpoint) * 100
            inside_tag = " [PRICE CURRENTLY INSIDE!]" if (f.bottom <= current_price <= f.top) else ""
            print(f"    {idx:2d}. {f.direction:7s} [${f.bottom:,.2f} - ${f.top:,.2f}] | Formed: {f.formed_time_ist} | Dist: {dist:+.2f}%{inside_tag}")

    # =========================================================================
    # STEP 2: Most Recent Touched 4H FVG Anchor Isolation
    # =========================================================================
    print(f"\n--- [STEP 2] Most Recent Touched 4H FVG Anchor ---")
    anchor = get_most_recent_touched_4h_fvg(
        candles_4h=candles_4h,
        active_fvgs=active_fvgs,
        current_price=current_price,
        candles_ltf=candles_ltf,
        ltf_timeframe=ltf_timeframe,
    )

    if anchor:
        f = anchor.fvg
        time_since_first_touch_hrs = (now_ms - anchor.first_touch_timestamp) / (3600 * 1000)
        print(f"  🎯 ACTIVE 4H ANCHOR ISOLATED:")
        print(f"     • Direction:               {f.direction.upper()}")
        print(f"     • Zone:                    [${f.bottom:,.2f} - ${f.top:,.2f}]")
        print(f"     • Formed At (4H Close):    {f.formed_time_ist}")
        print(f"     • Most Recent Touch:       {anchor.most_recent_touch_time_ist}")
        print(f"     • First Touch Time:        {anchor.first_touch_time_ist} ({time_since_first_touch_hrs:.1f} hrs ago)")

        # =====================================================================
        # STEP 3: Unmitigated LTF FVG Discovery, Extreme Ranking & Trade Setup
        # =====================================================================
        print(f"\n--- [STEP 3] Unmitigated LTF FVGs ({ltf_timeframe}) & Extreme Trade Setup (Min Gap: {min_gap_pct:.2f}%, Target: {completion_target}) ---")
        unmitigated_fvgs = find_unmitigated_ltf_fvgs(
            candles_ltf=candles_ltf,
            after_timestamp=anchor.first_touch_timestamp,
            direction=f.direction,
            current_price=current_price,
            ltf_timeframe=ltf_timeframe,
            min_gap_pct=min_gap_pct,
            completion_target=completion_target,
        )

        if not unmitigated_fvgs:
            print(f"  ⏳ No active or pending {ltf_timeframe} {f.direction} FVGs (>= {min_gap_pct:.2f}%) found since 4H touch.")
            print(f"     (Waiting for new {f.direction} {ltf_timeframe} FVG to form)")
        else:
            rank_rule = "Lowest Price" if f.direction == "Bullish" else "Highest Price"
            print(f"  ⚡ Found {len(unmitigated_fvgs)} active/pending {ltf_timeframe} {f.direction} FVG(s) (Ranking by {rank_rule}):")
            for idx, uf in enumerate(unmitigated_fvgs[:5], 1):
                st_label = "⏳ PENDING" if uf.lifecycle_state == "PENDING_RETRACE" else f"🚀 ACTIVE ({uf.floating_r:+.2f}R)"
                print(f"    {idx:2d}. [{st_label}] {uf.direction:7s} [${uf.bottom:,.2f} - ${uf.top:,.2f}] | Gap: ${uf.width:,.2f} ({uf.gap_pct:.3f}%) | Formed: {uf.formed_time_ist}")
            if len(unmitigated_fvgs) > 5:
                print(f"    ... and {len(unmitigated_fvgs) - 5} more")

            # Select extreme FVG
            best_ltf = select_extreme_ltf_fvg(unmitigated_fvgs, f.direction)
            if best_ltf:
                setup = build_extreme_trade_setup(
                    symbol=symbol,
                    anchor=anchor,
                    ltf_fvg=best_ltf,
                    ltf_timeframe=ltf_timeframe,
                    completion_target=completion_target,
                    all_unmitigated_fvgs=unmitigated_fvgs,
                )
                side = "LONG" if setup.direction == "Bullish" else "SHORT"
                is_active = (setup.state == "TRADE_ACTIVE")

                header_tag = "🚀 #1 EXTREME TRADE (IN POSITION / ACTIVE NOW)" if is_active else "🔔 #1 EXTREME TRADE SETUP (PENDING RETRACE / LIMIT ORDER)"
                print(f"\n  {header_tag} ({side}):")
                print(f"     • Target LTF FVG:     [${best_ltf.bottom:,.2f} - ${best_ltf.top:,.2f}] (Gap: ${best_ltf.width:,.2f} / {best_ltf.gap_pct:.3f}%)")
                print(f"     • Formed At:          {best_ltf.formed_time_ist}")
                print(f"     • State:              {setup.state}")
                if is_active:
                    print(f"     • Entry Touched At:   {setup.entry_time_ist}")
                    print(f"     • Entry Price Point:  ${setup.entry_price:,.2f}")
                    print(f"     • Current Live Price: ${current_price:,.2f}")
                    print(f"     • Floating PnL:       {setup.floating_r:+.2f}R")
                else:
                    dist_to_entry = ((current_price - setup.entry_price) / setup.entry_price) * 100
                    print(f"     • Limit Order Entry:  ${setup.entry_price:,.2f} ({dist_to_entry:+.2f}% away)")
                    print(f"     • Current Live Price: ${current_price:,.2f}")
                print(f"     • Stop Loss (SL):     ${setup.stop_loss:,.2f} (Exact 3-candle wick extreme)")
                print(f"     • Risk ($R$):          ${setup.risk_r:,.2f} ({setup.risk_pct:.2f}%)")
                print(f"     • Targets:")
                print(f"       - TP 1R (1:1):      ${setup.tp_1r:,.2f}")
                print(f"       - TP 2R (1:2):      ${setup.tp_2r:,.2f}")
                print(f"       - TP 3R (1:3):      ${setup.tp_3r:,.2f}")
    else:
        print(f"  ⏳ Status: WAITING FOR RETRACE")
        print(f"     None of the {len(active_fvgs)} active 4H FVGs have been touched post-close yet.")


async def main():
    parser = argparse.ArgumentParser(description="Live Step 1 & 2 Scanner for Extreme LTF FVG Strategy")
    parser.add_argument("symbol", nargs="?", default="PAXG", help="Crypto symbol to scan (e.g. BTC, ETH, SOL, PAXG)")
    parser.add_argument("--ltf", default="15m", choices=["1m", "5m", "15m", "1h"], help="LTF timeframe for touch refinement")
    parser.add_argument("--invalidation", default="wick", choices=["wick", "close"], help="Invalidation mode: wick or close (default: wick)")
    parser.add_argument("--min-gap-pct", type=float, default=0.05, help="Minimum LTF FVG gap size in %% (default: 0.05%%)")
    parser.add_argument("--target", default="2R", choices=["1R", "2R", "3R"], help="Completion target (default: 2R)")
    parser.add_argument("--all", action="store_true", help="Scan entire top crypto universe")
    args = parser.parse_args()

    use_close = (args.invalidation == "close")
    symbols = DEFAULT_UNIVERSE if args.all else [args.symbol.upper()]

    for sym in symbols:
        await inspect_symbol_live(
            sym,
            ltf_timeframe=args.ltf,
            use_close_invalidation=use_close,
            min_gap_pct=args.min_gap_pct,
            completion_target=args.target,
        )

    print("\n" + "=" * 80)
    print("  ✅ Live scan complete!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
