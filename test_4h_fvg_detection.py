"""
TDD Test Suite: 4H FVG Detection Layer
Maps to spec: strategy-2-extreme §"Incremental 4H FVG Cache" and §"4H Anchor Selection & First Touch Pinpointing"
"""
import pytest
from datetime import datetime, timezone, timedelta

from strategy_extreme_fvg import (
    Candle, FVG, HTFFVGCache,
    filter_closed_candles,
    compute_all_active_4h_fvgs,
    get_4h_fvg_first_touch_ts,
    get_4h_fvg_most_recent_touch_ts,
    get_most_recent_touched_4h_fvg,
    HTF_CANDLE_DURATION_MS, TIMEFRAME_MS,
)

IST = timezone(timedelta(hours=5, minutes=30))
H4 = HTF_CANDLE_DURATION_MS
M5 = TIMEFRAME_MS["5m"]


def make_candle(ts: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=100.0)


# ==============================================================================
# §1.1 — Incremental 4H FVG Cache: O(1) delta updates
# ==============================================================================

def test_cache_bootstrap_initializes_empty_for_no_candles():
    """GIVEN no historical 4H candles WHEN bootstrap() runs THEN active_fvgs is empty and last_ts=0."""
    cache = HTFFVGCache()
    active = cache.bootstrap("BTC", [], current_time_ms=10 * H4)
    assert active == []
    assert cache.is_bootstrapped("BTC", use_close_invalidation=False) is True
    assert cache.last_processed_candle_ts["BTC:wick"] == 0


def test_cache_bootstrap_detects_active_bullish_fvg():
    """GIVEN closed 4H candles with bullish imbalance WHEN bootstrap runs THEN FVG appears with bottom=c1.high top=c3.low."""
    c1 = make_candle(0, 100, 110, 95, 105)
    c2 = make_candle(H4, 105, 130, 104, 128)
    c3 = make_candle(2 * H4, 128, 135, 115, 132)

    cache = HTFFVGCache()
    active = cache.bootstrap("BTC", [c1, c2, c3], current_time_ms=3 * H4)

    assert len(active) == 1
    assert active[0].direction == "Bullish"
    assert active[0].bottom == 110.0
    assert active[0].top == 115.0
    assert active[0].formed_at == 2 * H4


def test_cache_bootstrap_detects_active_bearish_fvg():
    """GIVEN bearish imbalance (c3.high < c1.low) WHEN bootstrap runs THEN FVG bottom=c3.high top=c1.low."""
    c1 = make_candle(0, 130, 140, 120, 125)
    c2 = make_candle(H4, 125, 128, 110, 112)
    c3 = make_candle(2 * H4, 112, 115, 100, 105)

    cache = HTFFVGCache()
    active = cache.bootstrap("BTC", [c1, c2, c3], current_time_ms=3 * H4)

    assert len(active) == 1
    assert active[0].direction == "Bearish"
    assert active[0].bottom == 115.0
    assert active[0].top == 120.0


def test_cache_bootstrap_excludes_invalidated_fvg():
    """GIVEN a bullish FVG subsequently breached (price < bottom) WHEN bootstrap runs THEN it is excluded."""
    c1 = make_candle(0, 100, 110, 95, 105)
    c2 = make_candle(H4, 105, 130, 104, 128)
    c3 = make_candle(2 * H4, 128, 135, 115, 132)
    c4 = make_candle(3 * H4, 125, 125, 108, 110)  # breaches 110

    cache = HTFFVGCache()
    active = cache.bootstrap("BTC", [c1, c2, c3, c4], current_time_ms=4 * H4)
    assert active == []


def test_cache_bootstrap_returns_newest_first():
    """GIVEN multiple active 4H FVGs WHEN bootstrap runs THEN they are sorted newest-first."""
    c1 = make_candle(0, 100, 110, 95, 105)
    c2 = make_candle(H4, 105, 130, 104, 128)
    c3 = make_candle(2 * H4, 128, 135, 115, 132)  # FVG 1: formed at 2*H4 [110, 115]

    c4 = make_candle(3 * H4, 132, 134, 126, 130)  # no FVG (c4.low 126 <= c2.high 130)
    c5 = make_candle(4 * H4, 130, 133, 127, 131)  # no FVG (c5.low 127 <= c3.high 135)

    c6 = make_candle(5 * H4, 131, 140, 129, 138)  # no FVG (c6.low 129 <= c4.high 134)
    c7 = make_candle(6 * H4, 138, 160, 130, 158)  # no FVG with c5 (c7.low 130 <= c5.high 133)
    c8 = make_candle(7 * H4, 158, 165, 145, 162)  # FVG 2: formed at 7*H4 [140, 145] (c8.low 145 > c6.high 140)

    cache = HTFFVGCache()
    active = cache.bootstrap("BTC", [c1, c2, c3, c4, c5, c6, c7, c8], current_time_ms=8 * H4)
    assert len(active) == 2
    assert active[0].formed_at == 7 * H4
    assert active[1].formed_at == 2 * H4
    assert active[0].formed_at > active[1].formed_at


def test_cache_bootstrap_needs_minimum_three_candles():
    """GIVEN fewer than 3 closed 4H candles WHEN bootstrap runs THEN active list is empty."""
    c1 = make_candle(0, 100, 110, 95, 105)
    c2 = make_candle(H4, 105, 115, 104, 110)

    cache = HTFFVGCache()
    assert cache.bootstrap("BTC", [c1, c2], current_time_ms=2 * H4) == []
    assert cache.bootstrap("BTC", [c1], current_time_ms=H4) == []


# ==============================================================================
# §1.2 — Delta update: O(1) on new closed 4H bar
# ==============================================================================

def test_cache_delta_adds_new_fvg_on_new_closed_bar():
    """GIVEN cache bootstrapped with c1,c2,c3 (no FVG) WHEN c4 closes forming bullish FVG THEN update_delta adds it."""
    c1 = make_candle(0, 100, 105, 95, 100)
    c2 = make_candle(H4, 100, 110, 99, 105)
    c3 = make_candle(2 * H4, 105, 115, 104, 110)
    c4 = make_candle(3 * H4, 110, 120, 112, 118)  # c4.low=112 > c2.high=110

    cache = HTFFVGCache()
    cache.bootstrap("BTC", [c1, c2, c3], current_time_ms=3 * H4)
    assert cache.active_fvgs.get("BTC:wick", []) == []

    active = cache.update_delta("BTC", [c4], current_price=118.0, current_time_ms=4 * H4)
    assert len(active) == 1
    assert active[0].direction == "Bullish"
    assert active[0].formed_at == 3 * H4
    assert len(cache.active_fvgs["BTC:wick"]) == 1


def test_cache_delta_invalidates_existing_fvg():
    """GIVEN an active bullish FVG with bottom=110 WHEN new candle low<110 THEN FVG is removed."""
    c1 = make_candle(0, 100, 110, 95, 105)
    c2 = make_candle(H4, 105, 130, 104, 128)
    c3 = make_candle(2 * H4, 128, 135, 115, 132)
    c4 = make_candle(3 * H4, 130, 130, 108, 112)  # low 108 < 110

    cache = HTFFVGCache()
    cache.bootstrap("BTC", [c1, c2, c3], current_time_ms=3 * H4)
    assert len(cache.active_fvgs["BTC:wick"]) == 1

    active = cache.update_delta("BTC", [c4], current_price=112.0, current_time_ms=4 * H4)
    assert len(cache.active_fvgs["BTC:wick"]) == 0
    assert len(active) == 0


def test_cache_delta_ignores_unfinished_candle():
    """GIVEN an unfinished candle (now_ms < c.timestamp + 4h) WHEN update_delta receives it THEN cache unchanged."""
    c1 = make_candle(0, 100, 110, 95, 105)
    c2 = make_candle(H4, 105, 130, 104, 128)
    c3 = make_candle(2 * H4, 128, 135, 115, 132)
    c4_unfinished = make_candle(3 * H4, 130, 140, 128, 138)

    cache = HTFFVGCache()
    cache.bootstrap("BTC", [c1, c2, c3], current_time_ms=3 * H4)
    initial_count = len(cache.active_fvgs.get("BTC:wick", []))

    active = cache.update_delta("BTC", [c4_unfinished], current_price=138.0, current_time_ms=int(3.5 * H4))
    assert len(active) == initial_count
    assert len(cache.active_fvgs.get("BTC:wick", [])) == initial_count


def test_cache_delta_no_op_when_no_new_candle():
    """GIVEN cache up to date WHEN update_delta receives older candle THEN no change."""
    c1 = make_candle(0, 100, 110, 95, 105)
    c2 = make_candle(H4, 105, 130, 104, 128)
    c3 = make_candle(2 * H4, 128, 135, 115, 132)

    cache = HTFFVGCache()
    cache.bootstrap("BTC", [c1, c2, c3], current_time_ms=3 * H4)

    active = cache.update_delta("BTC", [c2], current_price=132.0, current_time_ms=3 * H4)
    assert len(active) == 1


def test_cache_uses_separate_keys_per_invalidation_mode():
    """GIVEN same symbol+candles WHEN bootstrap with wick and close modes THEN each has independent state."""
    c1 = make_candle(0, 100, 110, 95, 105)
    c2 = make_candle(H4, 105, 130, 104, 128)
    c3 = make_candle(2 * H4, 128, 135, 115, 132)

    cache = HTFFVGCache()
    cache.bootstrap("BTC", [c1, c2, c3], current_time_ms=3 * H4, use_close_invalidation=False)
    cache.bootstrap("BTC", [c1, c2, c3], current_time_ms=3 * H4, use_close_invalidation=True)

    assert "BTC:wick" in cache.active_fvgs
    assert "BTC:close" in cache.active_fvgs
    assert cache.is_bootstrapped("BTC", use_close_invalidation=False)
    assert cache.is_bootstrapped("BTC", use_close_invalidation=True)
