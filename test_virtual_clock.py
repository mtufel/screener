"""
Tests for the virtual clock module (clock.py).

Pins the replay-safety contract: real-time behavior is unchanged when no
virtual clock is installed, and an installed clock drives every clock.now_ms()
read with explicit per-call parameters still taking precedence.
"""

import asyncio
import time

import pytest

import clock
from clock import VirtualClock, install_virtual_clock, now_ms as clock_now_ms


def test_no_clock_installed_returns_real_time():
    before = int(time.time() * 1000)
    val = clock_now_ms()
    after = int(time.time() * 1000)
    assert before <= val <= after


def test_virtual_clock_advances_monotonically():
    vc = VirtualClock(start_ms=1_000_000)
    assert vc.now_ms() == 1_000_000
    assert vc.advance_to(2_000_000) == 2_000_000
    assert vc.now_ms() == 2_000_000
    # Backwards advance is ignored
    assert vc.advance_to(1_500_000) == 2_000_000
    assert vc.now_ms() == 2_000_000


def test_install_virtual_clock_drives_now_ms():
    vc = VirtualClock(start_ms=1_700_000_000_000)
    with install_virtual_clock(vc):
        assert clock_now_ms() == 1_700_000_000_000
        vc.advance_to(1_700_000_100_000)
        assert clock_now_ms() == 1_700_000_100_000
    # After the context, real time again
    assert clock_now_ms() >= int(time.time() * 1000) - 1000


def test_nested_install_restores_previous_clock():
    outer = VirtualClock(start_ms=1_000)
    inner = VirtualClock(start_ms=2_000)
    with install_virtual_clock(outer):
        with install_virtual_clock(inner):
            assert clock_now_ms() == 2_000
        assert clock_now_ms() == 1_000
    assert clock.installed_clock() is None


def test_explicit_current_time_ms_takes_precedence():
    """Engine functions with explicit time params ignore the installed clock."""
    from strategy_extreme_fvg import Candle, filter_closed_candles

    vc = VirtualClock(start_ms=10 * 60 * 1000)  # 10 minutes
    with install_virtual_clock(vc):
        candles = [
            Candle(timestamp=0, open=1, high=2, low=0.5, close=1.5, volume=1),
            Candle(timestamp=15 * 60 * 1000, open=1, high=2, low=0.5, close=1.5, volume=1),
        ]
        # Explicit param: only candle 0 closed by 20 min
        out = filter_closed_candles(candles, 15 * 60 * 1000, current_time_ms=20 * 60 * 1000)
        assert len(out) == 1
        # No param: uses the virtual clock (10 min) -> nothing closed
        out2 = filter_closed_candles(candles, 15 * 60 * 1000)
        assert len(out2) == 0


@pytest.mark.asyncio
async def test_clock_isolation_across_async_tasks():
    """A clock installed inside a task must not leak into sibling tasks."""
    vc = VirtualClock(start_ms=5_000)
    results = {}

    async def with_clock():
        with install_virtual_clock(vc):
            await asyncio.sleep(0.01)
            results["inside"] = clock_now_ms()

    async def without_clock():
        await asyncio.sleep(0.005)
        results["outside"] = clock_now_ms()

    await asyncio.gather(with_clock(), without_clock())
    assert results["inside"] == 5_000
    assert results["outside"] > int(time.time() * 1000) - 1000


def test_tracker_now_uses_installed_clock():
    """process_live_setups stamps its bookkeeping 'now' from the installed clock."""
    from extreme_trade_tracker import ExtremeTradeTracker

    vc = VirtualClock(start_ms=1_700_000_000_000)
    tracker = ExtremeTradeTracker(storage_path="/tmp/definitely_missing_replay_ledger.json")
    tracker.active_trades = {}
    tracker.history = []
    with install_virtual_clock(vc):
        events = tracker.process_live_setups([], {})
    # No trades: still exercises the now-stamp path without error under a virtual clock
    assert events == []
