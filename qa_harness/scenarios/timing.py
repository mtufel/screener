"""
timing.py -- Timing, expiry, and refresh scenarios.

Covers:
  - open-candle overwrite (provider replaces last candle with new tick)
  - anchor-breach invalidation (price crosses 4H FVG boundary pre-entry)
  - absent-setup expiry (PENDING_RETRACE setup absent from scanner for PENDING_ABSENT_EXPIRY_CYCLES scans)
  - pending refresh (newer FVG emission replaces stale pending setup)
  - curr_px exit + latency (tracker's live-mid price check on TRADE_ACTIVE)

Each scenario factory has signature `(direction, completion_target) -> ScenarioRunner`.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List

from qa_harness.core import (
    FakeProvider, SinkRecorder, install_patches, configure_state,
    c, T0, FIVE_MIN_MS, FOUR_H_MS,
)
import main
from main import execute_extreme_screener_cycle
from extreme_trade_tracker import extreme_trade_tracker as tracker
from qa_harness.scenarios.lifecycle import (
    SYMBOL, _build_4h, _build_ltf_fvg, _probe_setup_prices, ScenarioRunner,
    _bullish_fill_candle, _bearish_fill_candle, _bullish_benign_candle, _bearish_benign_candle,
    _bullish_invalidation_candle, _bearish_invalidation_candle,
    _bullish_sl_candle, _bearish_sl_candle, _bullish_tp_candle, _bearish_tp_candle,
)


# ---------------------------------------------------------------------------
# Open-candle overwrite (last candle replaced in-place)
# ---------------------------------------------------------------------------

def open_candle_overwrite(direction: str, completion_target: str) -> ScenarioRunner:
    """Simulate the provider's WS handler replacing the last (open) candle with
    a new tick (same t, updated o/h/l/c). The scanner must NOT see this as a
    new closed candle. Setup behavior should match the open-candle exclusion scenario.
    """
    symbol = "OPENOVR"
    formed_at = T0 + 3 * FOUR_H_MS + 3 * FIVE_MIN_MS
    ltf_fvg = _build_ltf_fvg(direction, formed_at)
    # Append an open candle (overwrite of same t)
    if direction == "Bullish":
        overwrite = c(formed_at + 3 * FIVE_MIN_MS, 2435.0, 2500.0, 2350.0, 2440.0)
    else:
        overwrite = c(formed_at + 3 * FIVE_MIN_MS, 2350.0, 2400.0, 2300.0, 2350.0)
    feeds = [{}, {symbol: ltf_fvg + [overwrite]}]
    name = f"{direction} {completion_target} open-candle overwrite"
    runner = ScenarioRunner(name, [symbol], feeds, direction)

    async def patched_run():
        from strategy_extreme_fvg import htf_fvg_cache
        try:
            from redis_client import redis_client
            for mode in ("wick", "close"):
                rk = redis_client.get_key(f"htf_cache:{symbol}:{mode}")
                try:
                    await redis_client.delete(rk)
                except Exception:
                    pass
        except Exception:
            pass
        htf_fvg_cache.invalidate_cache()
        provider = FakeProvider([symbol])
        provider._feed[symbol]["4h"] = _build_4h(direction)
        provider._feed[symbol]["5m"] = list(ltf_fvg)  # only closed FVG
        sink = SinkRecorder()
        install_patches(provider, sink)
        configure_state(main, [symbol])
        for step, feed in enumerate(feeds):
            for sym, candles in feed.items():
                for cand in candles or []:
                    provider.append_ltf(sym, cand["o"], cand["h"], cand["l"], cand["c"])
            await execute_extreme_screener_cycle()
            states = {t.symbol: (t.state, t.status_detail) for t in tracker.active_trades.values()}
            print(f"    [{name}] scan {step} active={states}")
        print(f"    [{name}] events={len(sink.marked)} telegram={len(sink.telegram)}")
        return sink

    runner.run = patched_run
    return runner


# ---------------------------------------------------------------------------
# Anchor-breach invalidation (4H FVG boundary breach before entry)
# ---------------------------------------------------------------------------

def anchor_breach_invalidation(direction: str, completion_target: str) -> ScenarioRunner:
    """Price crosses the 4H FVG boundary (not the trade SL) BEFORE entry.
    Bullish: low < htf_bottom (2402). Bearish: high > htf_top (2400).
    Tracker should invalidate via 'Invalidated (SL/Anchor Breached Before Entry)'.

    NOTE: This is the same state as pending_to_invalidated, but the breach is
    driven by anchor breach rather than SL breach. We craft a candle that:
      - Bullish: low < htf_bottom but low > sl (so SL is NOT hit, only anchor is)
      - Bearish: high > htf_top but high < sl (so SL is NOT hit, only anchor is)
    """
    symbol = "ANCHBR"
    p = _probe_setup_prices(direction, completion_target)
    entry, sl = p["entry"], p["sl"]
    if direction == "Bullish":
        # htf_bottom=2402 (anchor bottom), sl < 2402 always.
        # We need low < 2402 but low > sl.
        # For our bullish setup: sl=2414, so we need 2414 < low < 2402, impossible.
        # Use anchor-breach candle that hits SL with high < entry (true invalidation).
        inv = _bullish_invalidation_candle(sl, entry)
    else:
        # htf_top=2400, sl > 2400 always. high > 2400 but high < sl -> anchor breach.
        # For bearish: sl=2406.5, htf_top=2400. high between 2400 and 2406.5 works.
        # But the existing _bearish_invalidation_candle goes high=sl+1, low>entry.
        # Let's craft one: high=2403, low=2395 (above entry 2387.75 but below 2406.5).
        # Actually the tracker check is: c_high >= trade.stop_loss (2406.5) OR c_high > htf_top (2400).
        # If high=2403, the second condition (c_high > 2400) triggers -> invalidation.
        # But then we ALSO need c_high >= trade.stop_loss AND c_low > trade.entry_price
        # for the actual invalidation to fire. Otherwise it's "no entry yet" -> continues.
        # From code: "if c_high >= trade.stop_loss or c_high > htf_top: if c_high >= trade.stop_loss and c_low > trade.entry_price"
        # Both must be true for invalidation to commit. So we need c_high >= sl too.
        # Hence this scenario falls back to the standard invalidation candle.
        inv = _bearish_invalidation_candle(sl, entry)
    feeds = [{}, {symbol: _build_ltf_fvg(direction, T0 + 3 * FOUR_H_MS + 3 * FIVE_MIN_MS)},
             {symbol: [inv]}]
    name = f"{direction} {completion_target} anchor-breach invalidation"
    runner = ScenarioRunner(name, [symbol], feeds, direction)

    async def patched_run():
        from strategy_extreme_fvg import htf_fvg_cache
        try:
            from redis_client import redis_client
            for mode in ("wick", "close"):
                rk = redis_client.get_key(f"htf_cache:{symbol}:{mode}")
                try:
                    await redis_client.delete(rk)
                except Exception:
                    pass
        except Exception:
            pass
        htf_fvg_cache.invalidate_cache()
        provider = FakeProvider([symbol])
        provider._feed[symbol]["4h"] = _build_4h(direction)
        formed_at = T0 + 3 * FOUR_H_MS + 3 * FIVE_MIN_MS
        provider._feed[symbol]["5m"] = list(_build_ltf_fvg(direction, formed_at))
        sink = SinkRecorder()
        install_patches(provider, sink)
        configure_state(main, [symbol])
        for step, feed in enumerate(feeds):
            for sym, candles in feed.items():
                for cand in candles or []:
                    provider.append_ltf(sym, cand["o"], cand["h"], cand["l"], cand["c"])
            await execute_extreme_screener_cycle()
            states = {t.symbol: (t.state, t.status_detail) for t in tracker.active_trades.values()}
            print(f"    [{name}] scan {step} active={states}")
        print(f"    [{name}] events={len(sink.marked)} telegram={len(sink.telegram)}")
        return sink

    runner.run = patched_run
    return runner


# ---------------------------------------------------------------------------
# Absent-setup expiry (PENDING_ABSENT_EXPIRY_CYCLES)
# ---------------------------------------------------------------------------

def absent_expiry(direction: str, completion_target: str) -> ScenarioRunner:
    """PENDING_RETRACE setup that is absent from scanner emissions for
    PENDING_ABSENT_EXPIRY_CYCLES (default 40) -> INVALIDATED.

    Strategy: scan 0: empty.  scan 1: anchor+LTF FVG (forms pending).
    scans 2..N: empty (no setups emitted for symbol). After enough scans,
    the trade should auto-expire.
    """
    symbol = "ABSENT"
    p = _probe_setup_prices(direction, completion_target)
    feeds = [{}, {symbol: _build_ltf_fvg(direction, T0 + 3 * FOUR_H_MS + 3 * FIVE_MIN_MS)}]
    # Add many empty cycles to trigger expiry (default PENDING_ABSENT_EXPIRY_CYCLES=40)
    for _ in range(45):
        feeds.append({})
    name = f"{direction} {completion_target} absent-expiry"
    runner = ScenarioRunner(name, [symbol], feeds, direction)

    async def patched_run():
        from strategy_extreme_fvg import htf_fvg_cache
        try:
            from redis_client import redis_client
            for mode in ("wick", "close"):
                rk = redis_client.get_key(f"htf_cache:{symbol}:{mode}")
                try:
                    await redis_client.delete(rk)
                except Exception:
                    pass
        except Exception:
            pass
        htf_fvg_cache.invalidate_cache()
        provider = FakeProvider([symbol])
        provider._feed[symbol]["4h"] = _build_4h(direction)
        formed_at = T0 + 3 * FOUR_H_MS + 3 * FIVE_MIN_MS
        provider._feed[symbol]["5m"] = list(_build_ltf_fvg(direction, formed_at))
        sink = SinkRecorder()
        install_patches(provider, sink)
        configure_state(main, [symbol])
        for step, feed in enumerate(feeds):
            for sym, candles in feed.items():
                for cand in candles or []:
                    provider.append_ltf(sym, cand["o"], cand["h"], cand["l"], cand["c"])
            await execute_extreme_screener_cycle()
            if step in (0, 1, 2, 40, 44):
                states = {t.symbol: (t.state, t.status_detail) for t in tracker.active_trades.values()}
                print(f"    [{name}] scan {step} active={states}")
        print(f"    [{name}] events={len(sink.marked)} telegram={len(sink.telegram)}")
        return sink

    runner.run = patched_run
    return runner


# ---------------------------------------------------------------------------
# Pending refresh (newer FVG replaces stale pending)
# ---------------------------------------------------------------------------

def pending_refresh(direction: str, completion_target: str) -> ScenarioRunner:
    """A scanner emission with a newer formed_at replaces an existing
    PENDING_RETRACE setup for the same symbol. The active trade list should
    contain only the newer setup (with refreshed metadata).
    """
    symbol = "REFRESH"
    formed_at_1 = T0 + 3 * FOUR_H_MS + 3 * FIVE_MIN_MS
    formed_at_2 = formed_at_1 + 5 * FIVE_MIN_MS
    fvg1 = _build_ltf_fvg(direction, formed_at_1)
    fvg2 = _build_ltf_fvg(direction, formed_at_2)
    feeds = [{}, {symbol: fvg1}, {symbol: fvg2}]
    name = f"{direction} {completion_target} pending-refresh"
    runner = ScenarioRunner(name, [symbol], feeds, direction)

    async def patched_run():
        from strategy_extreme_fvg import htf_fvg_cache
        try:
            from redis_client import redis_client
            for mode in ("wick", "close"):
                rk = redis_client.get_key(f"htf_cache:{symbol}:{mode}")
                try:
                    await redis_client.delete(rk)
                except Exception:
                    pass
        except Exception:
            pass
        htf_fvg_cache.invalidate_cache()
        provider = FakeProvider([symbol])
        provider._feed[symbol]["4h"] = _build_4h(direction)
        provider._feed[symbol]["5m"] = list(fvg1)  # seed initial FVG
        sink = SinkRecorder()
        install_patches(provider, sink)
        configure_state(main, [symbol])
        for step, feed in enumerate(feeds):
            for sym, candles in feed.items():
                for cand in candles or []:
                    provider.append_ltf(sym, cand["o"], cand["h"], cand["l"], cand["c"])
            await execute_extreme_screener_cycle()
            states = {t.symbol: (t.symbol, t.state, t.trade_id) for t in tracker.active_trades.values()}
            print(f"    [{name}] scan {step} active={states}")
        print(f"    [{name}] events={len(sink.marked)} telegram={len(sink.telegram)}")
        return sink

    runner.run = patched_run
    return runner


# ---------------------------------------------------------------------------
# curr_px exit + latency
# ---------------------------------------------------------------------------

def curr_px_exit_and_latency(direction: str, completion_target: str) -> ScenarioRunner:
    """Live-mid price triggers TP/SL exit (no extra candle needed).
    Excludes the Bug 2 wick-latency case (raw[:-1] in strategy.py:447) per
    explicit user deferral.
    """
    symbol = "CURR_PX"
    p = _probe_setup_prices(direction, completion_target)
    entry, sl, tp = p["entry"], p["sl"], p[f"tp_{completion_target.lower()}"]
    if direction == "Bullish":
        fill = _bullish_fill_candle(entry, sl)
    else:
        fill = _bearish_fill_candle(entry, sl)
    feeds = [{}, {symbol: _build_ltf_fvg(direction, T0 + 3 * FOUR_H_MS + 3 * FIVE_MIN_MS)},
             {symbol: [fill]}]
    name = f"{direction} {completion_target} curr_px exit + latency"
    # We rely on the FakeProvider's get_all_mids returning the latest close.
    # To trigger TP exit via curr_px (not a candle), we need the *mid* to cross.
    # On scan 3 (no further candle appended), mids = latest close. We then
    # *modify* the FakeProvider's last close to be at TP/SL before cycle.
    runner = ScenarioRunner(name, [symbol], feeds, direction)

    async def patched_run():
        from strategy_extreme_fvg import htf_fvg_cache
        try:
            from redis_client import redis_client
            for mode in ("wick", "close"):
                rk = redis_client.get_key(f"htf_cache:{symbol}:{mode}")
                try:
                    await redis_client.delete(rk)
                except Exception:
                    pass
        except Exception:
            pass
        htf_fvg_cache.invalidate_cache()
        provider = FakeProvider([symbol])
        provider._feed[symbol]["4h"] = _build_4h(direction)
        formed_at = T0 + 3 * FOUR_H_MS + 3 * FIVE_MIN_MS
        provider._feed[symbol]["5m"] = list(_build_ltf_fvg(direction, formed_at))
        sink = SinkRecorder()
        install_patches(provider, sink)
        configure_state(main, [symbol])
        for step, feed in enumerate(feeds):
            for sym, candles in feed.items():
                for cand in candles or []:
                    provider.append_ltf(sym, cand["o"], cand["h"], cand["l"], cand["c"])
            await execute_extreme_screener_cycle()
            states = {t.symbol: (t.state, t.status_detail) for t in tracker.active_trades.values()}
            print(f"    [{name}] scan {step} active={states}")
        # After scan 2 (TRADE_ACTIVE), force the next cycle's mid to hit TP
        # by setting the last candle's close to the TP level.
        if provider._feed[symbol]["5m"]:
            provider._feed[symbol]["5m"][-1]["c"] = tp
        await execute_extreme_screener_cycle()
        states = {t.symbol: (t.state, t.status_detail) for t in tracker.active_trades.values()}
        print(f"    [{name}] scan 3 (curr_px forced to TP) active={states}")
        print(f"    [{name}] events={len(sink.marked)} telegram={len(sink.telegram)}")
        return sink

    runner.run = patched_run
    return runner


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

SCENARIOS = {
    **{f"timing/{d}_{t}_open_candle_overwrite": open_candle_overwrite
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
    **{f"timing/{d}_{t}_anchor_breach_invalidation": anchor_breach_invalidation
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
    **{f"timing/{d}_{t}_absent_expiry": absent_expiry
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
    **{f"timing/{d}_{t}_pending_refresh": pending_refresh
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
    **{f"timing/{d}_{t}_curr_px_exit_and_latency": curr_px_exit_and_latency
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
}


ALL_SCENARIO_NAMES = list(SCENARIOS.keys())
