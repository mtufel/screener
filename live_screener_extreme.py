"""
Continuous Live Scanner Daemon for Extreme LTF FVG Strategy.

Runs an infinite monitoring loop across configured symbols:
1. Evaluates 4H FVGs (closed candles, invalidation, cache).
2. Isolates the Active 4H Anchor (Most Recent Touch & First Touch Time).
3. Evaluates LTF FVGs (15m/5m) with the Trade State Machine:
   - PENDING_RETRACE (Waiting for limit order entry)
   - TRADE_ACTIVE (Price entered, floating towards TP/SL)
   - STOPPED_OUT / COMPLETED (Mitigated/closed)
4. Selects #1 Extreme FVG (lowest for Bullish, highest for Bearish).
5. Dispatches deduplicated alerts on state transitions (Console & Telegram).
"""

import argparse
import asyncio
from datetime import datetime, timezone, timedelta
import logging
import os
import time
from typing import Dict, List, Optional, Set

from dotenv import load_dotenv
import httpx

from hyperliquid_client import hyperliquid_client, SYMBOL_ALIASES
from strategy_extreme_fvg import (
    Candle,
    FVG,
    HTFFVGCache,
    TouchedAnchor,
    ExtremeTradeSetup,
    get_most_recent_touched_anchor_for_symbol,
    find_unmitigated_ltf_fvgs,
    select_extreme_ltf_fvg,
    build_extreme_trade_setup,
    get_extreme_setup_for_symbol,
    TIMEFRAME_MS,
)

load_dotenv()

IST = timezone(timedelta(hours=5, minutes=30))
logger = logging.getLogger("extreme-live-scanner")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DEFAULT_WHITELIST = [c.strip().upper() for c in os.getenv("COINS_WHITELIST", "BTC,ETH,SOL,PAXG").split(",") if c.strip()]


async def send_telegram_notification(message: str) -> bool:
    """Sends HTML notification to configured Telegram channel."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            return resp.status_code == 200
    except Exception as exc:
        logger.warning("Failed to send Telegram alert: %s", exc)
        return False


class ExtremeLiveScanner:
    """Continuous live scanner daemon managing state transitions and alert deduplication."""

    def __init__(
        self,
        symbols: List[str],
        ltf_timeframe: str = "15m",
        use_close_invalidation: bool = False,
        min_gap_pct: float = 0.05,
        completion_target: str = "2R",
        poll_interval_seconds: int = 30,
        enable_telegram: bool = True,
    ):
        self.symbols = symbols
        self.ltf_timeframe = ltf_timeframe
        self.use_close_invalidation = use_close_invalidation
        self.min_gap_pct = min_gap_pct
        self.completion_target = completion_target
        self.poll_interval = poll_interval_seconds
        self.enable_telegram = enable_telegram

        # State tracking: symbol -> last known state ("PENDING_RETRACE", "TRADE_ACTIVE", etc.)
        self.active_setups: Dict[str, ExtremeTradeSetup] = {}
        self.notified_states: Dict[str, str] = {}  # symbol:fvg_id -> last_notified_state

    def _fvg_key(self, setup: ExtremeTradeSetup) -> str:
        return f"{setup.symbol}:{setup.ltf_fvg.formed_at}:{setup.entry_price:.2f}"

    async def scan_symbol(self, symbol: str) -> Optional[ExtremeTradeSetup]:
        """Performs a single evaluation pass for a symbol."""
        cli = hyperliquid_client
        raw_sym = SYMBOL_ALIASES.get(symbol, symbol)

        # 1. Fetch 4H anchor
        anchor = await get_most_recent_touched_anchor_for_symbol(
            symbol=raw_sym,
            ltf_timeframe=self.ltf_timeframe,
            client=cli,
            use_close_invalidation=self.use_close_invalidation,
        )
        if not anchor:
            return None

        # 2. Fetch LTF candles
        raw_ltf = await cli.get_last_n_candles(symbol=raw_sym, timeframe=self.ltf_timeframe, n=300)
        if not raw_ltf:
            return None

        candles_ltf = [Candle.from_dict(c) for c in raw_ltf]
        current_price = candles_ltf[-1].close if candles_ltf else 0.0

        # 3. Find unmitigated LTF FVGs with state machine
        unmitigated = find_unmitigated_ltf_fvgs(
            candles_ltf=candles_ltf,
            after_timestamp=anchor.first_touch_timestamp,
            direction=anchor.fvg.direction,
            current_price=current_price,
            ltf_timeframe=self.ltf_timeframe,
            min_gap_pct=self.min_gap_pct,
            completion_target=self.completion_target,
        )
        if not unmitigated:
            return None

        # 4. Select #1 Extreme
        best_ltf = select_extreme_ltf_fvg(unmitigated, anchor.fvg.direction)
        if not best_ltf:
            return None

        return build_extreme_trade_setup(
            symbol=symbol,
            anchor=anchor,
            ltf_fvg=best_ltf,
            ltf_timeframe=self.ltf_timeframe,
            completion_target=self.completion_target,
            all_unmitigated_fvgs=unmitigated,
        )

    async def handle_setup_state(self, setup: ExtremeTradeSetup, current_price: float):
        """Deduplicates and dispatches alerts on state changes."""
        key = self._fvg_key(setup)
        last_state = self.notified_states.get(key)
        curr_state = setup.state
        side = "LONG" if setup.direction == "Bullish" else "SHORT"

        if last_state == curr_state:
            return  # Already notified

        # Alert 1: New Setup Formed (Pending Retrace)
        if curr_state == "PENDING_RETRACE" and last_state is None:
            dist_pct = ((current_price - setup.entry_price) / setup.entry_price) * 100
            msg = (
                f"🔔 <b>[NEW SETUP] {setup.symbol} {side} ({self.ltf_timeframe})</b>\n\n"
                f"• <b>4H Anchor:</b> {setup.anchor.fvg.direction} [${setup.anchor.fvg.bottom:,.2f} - ${setup.anchor.fvg.top:,.2f}]\n"
                f"• <b>Target FVG:</b> [${setup.ltf_fvg.bottom:,.2f} - ${setup.ltf_fvg.top:,.2f}]\n"
                f"• <b>Limit Order Entry:</b> <code>${setup.entry_price:,.2f}</code> ({dist_pct:+.2f}% away)\n"
                f"• <b>Stop Loss:</b> <code>${setup.stop_loss:,.2f}</code>\n"
                f"• <b>Risk ($R$):</b> ${setup.risk_r:,.2f} ({setup.risk_pct:.2f}%)\n"
                f"• <b>TP 1R:</b> ${setup.tp_1r:,.2f} | <b>TP 2R:</b> ${setup.tp_2r:,.2f} | <b>TP 3R:</b> ${setup.tp_3r:,.2f}\n"
                f"• <b>Status:</b> ⏳ WAITING FOR RETRACE"
            )
            print(f"\n🔔 [ALERT DISPATCHED] {setup.symbol} NEW SETUP: Limit Order at ${setup.entry_price:,.2f}")
            if self.enable_telegram:
                await send_telegram_notification(msg)
            self.notified_states[key] = curr_state

        # Alert 2: Entry Triggered (Trade Active)
        elif curr_state == "TRADE_ACTIVE" and last_state != "TRADE_ACTIVE":
            msg = (
                f"🚀 <b>[ENTRY FILLED] {setup.symbol} {side} IS NOW LIVE!</b>\n\n"
                f"• <b>Filled At:</b> <code>${setup.entry_price:,.2f}</code>\n"
                f"• <b>Time:</b> {setup.entry_time_ist}\n"
                f"• <b>Stop Loss:</b> <code>${setup.stop_loss:,.2f}</code>\n"
                f"• <b>Primary Target ({self.completion_target}):</b> "
                f"${setup.tp_2r:,.2f if self.completion_target == '2R' else setup.tp_1r:,.2f}\n"
                f"• <b>Status:</b> 🚀 IN POSITION (Monitoring TP/SL)"
            )
            print(f"\n🚀 [ALERT DISPATCHED] {setup.symbol} {side} ENTRY TRIGGERED! Trade is now ACTIVE.")
            if self.enable_telegram:
                await send_telegram_notification(msg)
            self.notified_states[key] = curr_state

    async def run_cycle(self):
        """Runs one full evaluation pass across all monitored symbols."""
        now_str = datetime.now(IST).strftime("%I:%M:%S %p IST")
        print(f"\n{'='*80}")
        print(f"  ⚡ EXTREME LIVE SCANNER CYCLE — {now_str} (LTF: {self.ltf_timeframe} | Target: {self.completion_target})")
        print(f"{'='*80}")

        found_any = False
        for sym in self.symbols:
            try:
                setup = await self.scan_symbol(sym)
                if setup:
                    found_any = True
                    self.active_setups[sym] = setup
                    # Get current price
                    raw_sym = SYMBOL_ALIASES.get(sym, sym)
                    mids = await hyperliquid_client.get_all_mids()
                    curr_px = float(mids.get(raw_sym, 0.0))
                    side = "LONG" if setup.direction == "Bullish" else "SHORT"

                    st_color = "🟢" if setup.state == "TRADE_ACTIVE" else "🟡"
                    print(f"\n  {st_color} {sym} ({side}):")
                    print(f"     • State:         {setup.state}")
                    print(f"     • 4H Anchor:     {setup.anchor.fvg.direction} [${setup.anchor.fvg.bottom:,.2f} - ${setup.anchor.fvg.top:,.2f}]")
                    print(f"     • LTF Target:    [${setup.ltf_fvg.bottom:,.2f} - ${setup.ltf_fvg.top:,.2f}] (Gap: ${setup.ltf_fvg.width:,.2f} / {setup.ltf_fvg.gap_pct:.3f}%)")
                    print(f"     • Entry Point:   ${setup.entry_price:,.2f} | Current: ${curr_px:,.2f}")
                    print(f"     • Stop Loss:     ${setup.stop_loss:,.2f} | Risk: ${setup.risk_r:,.2f} ({setup.risk_pct:.2f}%)")
                    print(f"     • Targets:       1R: ${setup.tp_1r:,.2f} | 2R: ${setup.tp_2r:,.2f} | 3R: ${setup.tp_3r:,.2f}")
                    if setup.state == "TRADE_ACTIVE":
                        print(f"     • Floating PnL:  {setup.floating_r:+.2f}R (Entered: {setup.entry_time_ist})")

                    await self.handle_setup_state(setup, curr_px)
            except Exception as exc:
                logger.error("Error scanning %s: %s", sym, exc)

        if not found_any:
            print(f"  ⏳ No active or pending setups found across {len(self.symbols)} symbols. Waiting for retrace...")

    async def start(self):
        """Starts the infinite continuous scanning loop."""
        print("\n" + "#" * 80)
        print("  🚀 STARTING CONTINUOUS LIVE EXTREME FVG SCANNER")
        print(f"  • Monitored Coins:  {', '.join(self.symbols)}")
        print(f"  • LTF Timeframe:    {self.ltf_timeframe}")
        print(f"  • Target:           {self.completion_target}")
        print(f"  • Min Gap Size:     {self.min_gap_pct:.2f}%")
        print(f"  • Invalidation:     {'CLOSE' if self.use_close_invalidation else 'WICK'}")
        print(f"  • Poll Interval:    Every {self.poll_interval} seconds")
        print(f"  • Telegram Alerts:  {'ENABLED' if self.enable_telegram and TELEGRAM_BOT_TOKEN else 'DISABLED'}")
        print("#" * 80 + "\n")

        cycle_count = 0
        while True:
            cycle_count += 1
            try:
                await self.run_cycle()
            except Exception as exc:
                logger.error("Error in scanner cycle: %s", exc)

            print(f"\n  ⏱ Next scan in {self.poll_interval}s... (Press Ctrl+C to stop)")
            await asyncio.sleep(self.poll_interval)


def main():
    parser = argparse.ArgumentParser(description="Continuous Live Screener for Extreme LTF FVG Strategy")
    parser.add_argument("--symbols", default=",".join(DEFAULT_WHITELIST), help="Comma-separated symbols to monitor")
    parser.add_argument("--ltf", default="5m", choices=["1m", "5m", "15m", "1h"], help="LTF timeframe (default: 5m)")
    parser.add_argument("--invalidation", default="wick", choices=["wick", "close"], help="Invalidation mode (default: wick)")
    parser.add_argument("--min-gap-pct", type=float, default=0.05, help="Minimum gap size in %% (default: 0.05%%)")
    parser.add_argument("--target", default="2R", choices=["1R", "2R", "3R"], help="Completion target (default: 2R)")
    parser.add_argument("--interval", type=int, default=30, help="Poll interval in seconds (default: 30s)")
    parser.add_argument("--no-telegram", action="store_true", help="Disable Telegram notifications")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    use_close = (args.invalidation == "close")

    scanner = ExtremeLiveScanner(
        symbols=symbols,
        ltf_timeframe=args.ltf,
        use_close_invalidation=use_close,
        min_gap_pct=args.min_gap_pct,
        completion_target=args.target,
        poll_interval_seconds=args.interval,
        enable_telegram=not args.no_telegram,
    )

    try:
        asyncio.run(scanner.start())
    except KeyboardInterrupt:
        print("\n\n🛑 Scanner stopped by user. Goodbye!")


if __name__ == "__main__":
    main()
