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
    get_most_recent_touched_4h_fvg,
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
        time_since_touch_hrs = (now_ms - anchor.touch_timestamp) / (3600 * 1000)
        print(f"  🎯 ACTIVE 4H ANCHOR ISOLATED:")
        print(f"     • Direction:           {f.direction.upper()}")
        print(f"     • Zone:                [${f.bottom:,.2f} - ${f.top:,.2f}]")
        print(f"     • Formed At (4H Close): {f.formed_time_ist}")
        print(f"     • Exact Touch At:      {anchor.touch_time_ist} (on {anchor.touch_timeframe} bar)")
        print(f"     • Time Since Touch:    {time_since_touch_hrs:.1f} hours ago")
        print(f"     • Ready for Step 3:    ✅ YES (Search LTF FVGs starting from {anchor.touch_time_ist})")
    else:
        print(f"  ⏳ Status: WAITING FOR RETRACE")
        print(f"     None of the {len(active_fvgs)} active 4H FVGs have been touched post-close yet.")


async def main():
    parser = argparse.ArgumentParser(description="Live Step 1 & 2 Scanner for Extreme LTF FVG Strategy")
    parser.add_argument("symbol", nargs="?", default="PAXG", help="Crypto symbol to scan (e.g. BTC, ETH, SOL, PAXG)")
    parser.add_argument("--ltf", default="15m", choices=["1m", "5m", "15m", "1h"], help="LTF timeframe for touch refinement")
    parser.add_argument("--invalidation", default="wick", choices=["wick", "close"], help="Invalidation mode: wick or close (default: wick)")
    parser.add_argument("--all", action="store_true", help="Scan entire top crypto universe")
    args = parser.parse_args()

    use_close = (args.invalidation == "close")
    symbols = DEFAULT_UNIVERSE if args.all else [args.symbol.upper()]

    for sym in symbols:
        await inspect_symbol_live(sym, ltf_timeframe=args.ltf, use_close_invalidation=use_close)

    print("\n" + "=" * 80)
    print("  ✅ Live scan complete!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
