"""
formation.py -- FVG formation/filter scenarios.

Covers:
  - no 4H anchor touch -> NO setup formed (no alert fired)
  - open-candle exclusion (filter_closed_candles must exclude the currently-open candle)
  - min_gap_pct filter (FVGs below threshold are dropped)
  - session filter (formation outside NY session -> rejected)
  - weekday filter (formation on weekend -> rejected)
  - best-of-FVGs (multiple FVGs -> lowest for bullish / highest for bearish wins)

Each scenario factory has signature `(direction, completion_target) -> ScenarioRunner`.
"""
from __future__ import annotations

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
)


# ---------------------------------------------------------------------------
# No 4H anchor touch
# ---------------------------------------------------------------------------

def _no_touch_4h(direction: str) -> List[Dict[str, Any]]:
    """4H where the FVG is formed but never touched by subsequent candles."""
    if direction == "Bullish":
        return [
            c(T0, 2400.0, 2402.0, 2390.0, 2395.0),
            c(T0 + FOUR_H_MS, 2395.0, 2450.0, 2394.0, 2445.0),
            c(T0 + 2 * FOUR_H_MS, 2445.0, 2460.0, 2415.0, 2455.0),
            # c4 stays well above the zone, no touch
            c(T0 + 3 * FOUR_H_MS, 2455.0, 2470.0, 2440.0, 2465.0),
        ]
    else:  # Bearish
        return [
            c(T0, 2410.0, 2420.0, 2400.0, 2405.0),
            c(T0 + FOUR_H_MS, 2405.0, 2410.0, 2395.0, 2400.0),
            c(T0 + 2 * FOUR_H_MS, 2400.0, 2395.0, 2380.0, 2385.0),
            # c4 stays well below the zone, no touch
            c(T0 + 3 * FOUR_H_MS, 2385.0, 2388.0, 2375.0, 2380.0),
        ]


def no_4h_anchor_touch(direction: str, completion_target: str) -> ScenarioRunner:
    """No setup should form because the 4H FVG was never touched."""
    symbol = "NOTOUCH"
    feeds = [{}, {symbol: _build_ltf_fvg(direction, T0 + 3 * FOUR_H_MS + 3 * FIVE_MIN_MS)}]
    name = f"{direction} {completion_target} no 4H anchor touch"
    runner = ScenarioRunner(name, [symbol], feeds, direction)
    # Override the 4h feed pre-seed to use the no-touch version
    original_run = runner.run

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
        provider._feed[symbol]["4h"] = _no_touch_4h(direction)
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
# Open-candle exclusion
# ---------------------------------------------------------------------------

def open_candle_exclusion(direction: str, completion_target: str) -> ScenarioRunner:
    """Verify that the currently-open 5m candle (last) is NOT counted for FVG detection.

    Construction: 4H anchor + 3 closed 5m candles forming a valid FVG, then
    ONE extra (open) 5m candle with extreme prices. The open candle's price
    movement should not invalidate or affect the FVG since it is not yet closed.
    """
    symbol = "OPENEXCL"
    formed_at = T0 + 3 * FOUR_H_MS + 3 * FIVE_MIN_MS
    ltf_fvg = _build_ltf_fvg(direction, formed_at)
    # Open candle: extreme prices, NOT closed
    if direction == "Bullish":
        open_candle = c(formed_at + 3 * FIVE_MIN_MS, 2435.0, 2500.0, 2350.0, 2440.0)
    else:
        open_candle = c(formed_at + 3 * FIVE_MIN_MS, 2350.0, 2400.0, 2300.0, 2350.0)
    feeds = [{}, {symbol: ltf_fvg + [open_candle]}]
    name = f"{direction} {completion_target} open-candle exclusion"
    runner = ScenarioRunner(name, [symbol], feeds, direction)
    original_run = runner.run

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
        provider._feed[symbol]["5m"] = list(ltf_fvg)  # closed FVG; open candle comes via feed
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
# min_gap_pct filter
# ---------------------------------------------------------------------------

def min_gap_pct_filter(direction: str, completion_target: str) -> ScenarioRunner:
    """LTF FVG below min_gap_pct -> setup rejected.
    We construct a tiny gap (< 0.05%) and verify no setup forms.
    """
    symbol = "MINGAP"
    formed_at = T0 + 3 * FOUR_H_MS + 3 * FIVE_MIN_MS
    if direction == "Bullish":
        # Tiny gap: c1.high=2415.001, c3.low=2415.002 -> gap = 0.001
        tiny_fvg = [
            c(formed_at, 2414.0, 2415.001, 2413.0, 2414.0),
            c(formed_at + FIVE_MIN_MS, 2414.0, 2420.0, 2414.0, 2419.0),
            c(formed_at + 2 * FIVE_MIN_MS, 2419.0, 2425.0, 2415.002, 2420.0),
        ]
    else:
        tiny_fvg = [
            c(formed_at, 2406.0, 2407.0, 2406.0, 2406.5),
            c(formed_at + FIVE_MIN_MS, 2400.0, 2406.5, 2400.0, 2401.0),
            c(formed_at + 2 * FIVE_MIN_MS, 2401.0, 2405.999, 2395.0, 2400.0),
        ]
    feeds = [{}, {symbol: tiny_fvg}]
    name = f"{direction} {completion_target} min_gap_pct filter"
    runner = ScenarioRunner(name, [symbol], feeds, direction)
    original_run = runner.run

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
        provider._feed[symbol]["5m"] = list(tiny_fvg)
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
# Session/weekday filter
# ---------------------------------------------------------------------------

def session_filter(direction: str, completion_target: str) -> ScenarioRunner:
    """Formation outside NY session (13:00-22:00 UTC) -> setup rejected."""
    symbol = "SESSFILT"
    # Force formation timestamp to 02:00 UTC (outside NY session)
    # NY session = 13:00-22:00 UTC. We pick 02:00 UTC.
    formed_at = (T0 // FOUR_H_MS) * FOUR_H_MS + FOUR_H_MS  # arbitrary base
    # Adjust to 02:00 UTC = 7:30 AM IST
    from datetime import datetime, timezone
    import time
    # Find a timestamp that falls at 02:00 UTC on some date
    target_utc = datetime(2024, 1, 15, 2, 0, 0, tzinfo=timezone.utc)  # Monday 02:00 UTC
    formed_at = int(target_utc.timestamp() * 1000) - (int(target_utc.timestamp() * 1000) % FIVE_MIN_MS)
    ltf_fvg = _build_ltf_fvg(direction, formed_at)
    feeds = [{}, {symbol: ltf_fvg}]
    name = f"{direction} {completion_target} session filter (rejected)"
    runner = ScenarioRunner(name, [symbol], feeds, direction)
    original_run = runner.run

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
        provider._feed[symbol]["5m"] = list(ltf_fvg)
        sink = SinkRecorder()
        install_patches(provider, sink)
        configure_state(main, [symbol])
        # Enable session filter
        main.state["extreme_session_filter"] = True
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


def weekday_filter(direction: str, completion_target: str) -> ScenarioRunner:
    """Formation on weekend (Saturday) -> setup rejected."""
    symbol = "WKDFILT"
    from datetime import datetime, timezone
    target_utc = datetime(2024, 1, 13, 15, 0, 0, tzinfo=timezone.utc)  # Saturday 15:00 UTC (inside NY session, but weekend)
    formed_at = int(target_utc.timestamp() * 1000) - (int(target_utc.timestamp() * 1000) % FIVE_MIN_MS)
    ltf_fvg = _build_ltf_fvg(direction, formed_at)
    feeds = [{}, {symbol: ltf_fvg}]
    name = f"{direction} {completion_target} weekday filter (rejected)"
    runner = ScenarioRunner(name, [symbol], feeds, direction)
    original_run = runner.run

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
        provider._feed[symbol]["5m"] = list(ltf_fvg)
        sink = SinkRecorder()
        install_patches(provider, sink)
        configure_state(main, [symbol])
        main.state["extreme_weekday_filter"] = True
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
# Best-of-FVGs
# ---------------------------------------------------------------------------

def best_of_fvgs(direction: str, completion_target: str) -> ScenarioRunner:
    """When multiple unmitigated FVGs exist post-touch, the scanner picks the
    lowest for bullish / highest for bearish.

    Construct: 2 valid FVGs at different price levels. Verify the chosen setup
    matches the expected extreme.
    """
    symbol = "BESTOF"
    formed_at = T0 + 3 * FOUR_H_MS + 3 * FIVE_MIN_MS
    if direction == "Bullish":
        # Two bullish FVGs: one shallow (2418..2429), one deeper (2420..2430)
        # Bullish -> lowest bottom wins
        fvg1 = [
            c(formed_at, 2412.0, 2418.0, 2414.0, 2416.0),
            c(formed_at + FIVE_MIN_MS, 2416.0, 2427.0, 2416.0, 2426.0),
            c(formed_at + 2 * FIVE_MIN_MS, 2426.0, 2431.0, 2429.0, 2430.5),
        ]
        fvg2 = [
            c(formed_at + 3 * FIVE_MIN_MS, 2416.0, 2420.0, 2418.0, 2419.0),
            c(formed_at + 4 * FIVE_MIN_MS, 2419.0, 2428.0, 2419.0, 2427.0),
            c(formed_at + 5 * FIVE_MIN_MS, 2427.0, 2432.0, 2430.0, 2431.5),
        ]
        # fvg1 bottom=2418, fvg2 bottom=2420 -> fvg1 wins (lower)
    else:
        # Two bearish FVGs: one shallower, one deeper
        fvg1 = [
            c(formed_at, 2407.0, 2408.0, 2406.5, 2407.0),
            c(formed_at + FIVE_MIN_MS, 2407.0, 2407.0, 2387.5, 2387.9),
            c(formed_at + 2 * FIVE_MIN_MS, 2387.9, 2387.75, 2387.75, 2387.6),
        ]
        fvg2 = [
            c(formed_at + 3 * FIVE_MIN_MS, 2400.0, 2401.0, 2399.5, 2400.0),
            c(formed_at + 4 * FIVE_MIN_MS, 2400.0, 2400.0, 2382.0, 2382.5),
            c(formed_at + 5 * FIVE_MIN_MS, 2382.5, 2382.25, 2382.25, 2382.0),
        ]
    feeds = [{}, {symbol: fvg1 + fvg2}]
    name = f"{direction} {completion_target} best-of-FVGs"
    runner = ScenarioRunner(name, [symbol], feeds, direction)
    original_run = runner.run

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
        provider._feed[symbol]["5m"] = list(fvg1 + fvg2)
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
# Exports
# ---------------------------------------------------------------------------

SCENARIOS = {
    **{f"formation/{d}_{t}_no_4h_anchor_touch": no_4h_anchor_touch
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
    **{f"formation/{d}_{t}_open_candle_exclusion": open_candle_exclusion
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
    **{f"formation/{d}_{t}_min_gap_pct_filter": min_gap_pct_filter
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
    **{f"formation/{d}_{t}_session_filter_rejected": session_filter
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
    **{f"formation/{d}_{t}_weekday_filter_rejected": weekday_filter
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
    **{f"formation/{d}_{t}_best_of_fvgs": best_of_fvgs
        for d in ["Bullish", "Bearish"] for t in ["1R", "2R", "3R"]},
}


ALL_SCENARIO_NAMES = list(SCENARIOS.keys())
